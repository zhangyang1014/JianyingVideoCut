"""按任务角色执行 OpenAI-compatible 调用，并提供可审计的降级链路。"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Generic, Optional, TypeVar

import httpx

from ..models.app_config import ResolvedFeatureLlm, ResolvedRoleCandidate
from .app_config_store import load_app_config, resolve_role_candidates
from .model_usage import (
    feature_for_role,
    manual_pricing_for_role,
    openrouter_catalog_pricing,
    provider_cost,
    record_model_call,
)

logger = logging.getLogger(__name__)
T = TypeVar("T")
_ROLE_GATES: dict[tuple[int, str, str, str, int], asyncio.Semaphore] = {}


def _role_parallel_limit(role: str, candidate: ResolvedRoleCandidate) -> int:
    if candidate.provider == "ollama":
        return 1
    config = load_app_config()
    if role.startswith("audit."):
        return max(1, config.audit_runtime.cloud_max_parallel_chunks)
    if role.startswith("highlight."):
        return max(1, config.highlight_runtime.cloud_max_parallel_calls)
    return 3


def _role_gate(role: str, candidate: ResolvedRoleCandidate) -> asyncio.Semaphore:
    """相同事件循环、角色和模型共享并发上限，避免多个任务叠加压垮供应商。"""
    limit = _role_parallel_limit(role, candidate)
    key = (
        id(asyncio.get_running_loop()),
        role,
        candidate.provider_profile_id,
        candidate.model,
        limit,
    )
    gate = _ROLE_GATES.get(key)
    if gate is None:
        gate = asyncio.Semaphore(limit)
        _ROLE_GATES[key] = gate
    return gate


@dataclass
class LlmRouteAttempt:
    role: str
    provider_profile_id: str
    model: str
    tier: str
    elapsed_ms: int
    outcome: str
    error_type: str = ""
    error: str = ""


@dataclass
class RoutedChatResult(Generic[T]):
    value: T
    content: str
    candidate: ResolvedRoleCandidate
    attempts: list[LlmRouteAttempt] = field(default_factory=list)


class LlmRoutingError(RuntimeError):
    def __init__(self, role: str, attempts: list[LlmRouteAttempt]):
        self.role = role
        self.attempts = attempts
        summary = "; ".join(
            f"{attempt.model}:{attempt.error_type or attempt.outcome}"
            for attempt in attempts
        ) or "no_available_candidate"
        super().__init__(f"LLM role {role} exhausted: {summary}")


def _error_type(exc: Exception) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.TransportError):
        return "network"
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in {401, 403}:
            return "authentication"
        if status == 429:
            return "rate_limit"
        if status >= 500:
            return "provider_error"
        return "request_error"
    if isinstance(exc, (ValueError, TypeError, KeyError)):
        return "contract_error"
    return "unknown"


def _retryable(error_type: str) -> bool:
    return error_type in {"timeout", "network", "rate_limit", "provider_error"}


def _message_content(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("模型响应缺少 choices")
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    if not isinstance(message, dict):
        raise ValueError("模型响应缺少 message")
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content
    reasoning = message.get("reasoning_content")
    if isinstance(reasoning, str) and reasoning.strip():
        return reasoning
    raise ValueError("模型响应 content 为空")


def _candidate_from_override(
    role: str,
    override: ResolvedFeatureLlm,
) -> ResolvedRoleCandidate:
    return ResolvedRoleCandidate(
        role=role,
        provider_profile_id="request-override",
        provider="ollama" if override.provider == "ollama" else "openai_compatible",
        model=override.model,
        base_url=(override.base_url or "").rstrip("/"),
        api_key=override.api_key or ("ollama" if override.provider == "ollama" else ""),
        tier="primary",
    )


def get_route_candidates(
    role: str,
    *,
    override: Optional[ResolvedFeatureLlm] = None,
    include_escalation: bool = True,
) -> list[ResolvedRoleCandidate]:
    configured = resolve_role_candidates(role, include_escalation=include_escalation)
    if not override:
        return configured
    explicit = _candidate_from_override(role, override)
    return [
        explicit,
        *[
            candidate
            for candidate in configured
            if (candidate.base_url, candidate.model) != (explicit.base_url, explicit.model)
        ],
    ]


async def _call_candidate(
    client: httpx.AsyncClient,
    candidate: ResolvedRoleCandidate,
    messages: list[dict[str, str]],
    *,
    max_tokens: int,
    temperature: float,
) -> dict[str, Any]:
    base_url = candidate.base_url.rstrip("/")
    if candidate.provider == "ollama" and not base_url.endswith("/v1"):
        base_url += "/v1"
    body: dict[str, Any] = {
        "model": candidate.model,
        "messages": messages,
        "max_tokens": candidate.max_tokens or max_tokens,
        "temperature": (
            candidate.temperature
            if candidate.temperature is not None
            else temperature
        ),
    }
    if candidate.thinking != "auto":
        body["thinking"] = {"type": candidate.thinking}
    body.update(candidate.extra_body)
    response = await client.post(
        f"{base_url}/chat/completions",
        headers={
            "Authorization": f"Bearer {candidate.api_key}",
            "Content-Type": "application/json",
        },
        json=body,
    )
    response.raise_for_status()
    return response.json()


async def route_chat_completion(
    role: str,
    messages: list[dict[str, str]],
    *,
    max_tokens: int,
    temperature: float = 0.0,
    validate: Optional[Callable[[str], T]] = None,
    quality_gate: Optional[Callable[[T], bool | Awaitable[bool]]] = None,
    override: Optional[ResolvedFeatureLlm] = None,
    include_escalation: bool = True,
    retries_per_candidate: int = 2,
    timeout_seconds: float = 180.0,
) -> RoutedChatResult[T | str]:
    """
    顺序执行角色候选。

    validate 把文本解析为业务对象；解析失败视为 contract_error。
    quality_gate 返回 False 时继续候选链，因此旗舰只会在质量门控失败或前序失败时触发。
    """
    candidates = get_route_candidates(
        role,
        override=override,
        include_escalation=include_escalation,
    )
    attempts: list[LlmRouteAttempt] = []
    last_quality_result: Optional[RoutedChatResult[Any]] = None
    if not candidates:
        raise LlmRoutingError(role, attempts)

    transport = httpx.AsyncHTTPTransport(proxy=None)
    timeout = httpx.Timeout(
        connect=min(30.0, timeout_seconds),
        read=timeout_seconds,
        write=min(30.0, timeout_seconds),
        pool=min(30.0, timeout_seconds),
    )
    async with httpx.AsyncClient(transport=transport, timeout=timeout) as client:
        for candidate in candidates:
            for retry_index in range(max(1, retries_per_candidate)):
                started = time.perf_counter()
                started_at = datetime.now(timezone.utc).isoformat()
                response_payload: dict[str, Any] = {}
                try:
                    async with _role_gate(role, candidate):
                        raw_response = await _call_candidate(
                            client,
                            candidate,
                            messages,
                            max_tokens=max_tokens,
                            temperature=temperature,
                        )
                    if isinstance(raw_response, dict):
                        response_payload = raw_response
                        content = _message_content(response_payload)
                    else:
                        # Backward compatibility for custom transports and tests.
                        content = str(raw_response)
                    value: Any = validate(content) if validate else content
                    passed = True
                    if quality_gate:
                        gate_result = quality_gate(value)
                        passed = (
                            await gate_result
                            if asyncio.iscoroutine(gate_result)
                            else bool(gate_result)
                        )
                    elapsed_ms = int((time.perf_counter() - started) * 1000)
                    outcome = "success" if passed else "quality_gate_failed"
                    attempt = LlmRouteAttempt(
                        role=role,
                        provider_profile_id=candidate.provider_profile_id,
                        model=candidate.model,
                        tier=candidate.tier,
                        elapsed_ms=elapsed_ms,
                        outcome=outcome,
                    )
                    attempts.append(attempt)
                    pricing = manual_pricing_for_role(
                        role,
                        candidate.provider_profile_id,
                        candidate.model,
                    )
                    pricing_source = "manual"
                    if provider_cost(response_payload) is None and (
                        pricing is None
                        or not any(value is not None for value in pricing.model_dump().values())
                    ):
                        pricing = await openrouter_catalog_pricing(
                            candidate.base_url,
                            candidate.model,
                            candidate.api_key,
                            candidate.provider_profile_id,
                        )
                        pricing_source = "catalog"
                    record_model_call(
                        feature=feature_for_role(role),
                        role=role,
                        provider=candidate.provider,
                        provider_profile_id=candidate.provider_profile_id,
                        requested_model=candidate.model,
                        tier=candidate.tier,
                        attempt=retry_index + 1,
                        status=outcome,
                        started_at=started_at,
                        elapsed_ms=elapsed_ms,
                        response_payload=response_payload,
                        pricing=pricing,
                        pricing_source=pricing_source,
                    )
                    logger.info(
                        "llm_route role=%s profile=%s model=%s tier=%s outcome=%s elapsed_ms=%s",
                        role,
                        candidate.provider_profile_id,
                        candidate.model,
                        candidate.tier,
                        outcome,
                        elapsed_ms,
                    )
                    if passed:
                        return RoutedChatResult(
                            value=value,
                            content=content,
                            candidate=candidate,
                            attempts=attempts,
                        )
                    last_quality_result = RoutedChatResult(
                        value=value,
                        content=content,
                        candidate=candidate,
                        attempts=attempts,
                    )
                    break
                except Exception as exc:
                    elapsed_ms = int((time.perf_counter() - started) * 1000)
                    error_type = _error_type(exc)
                    pricing = manual_pricing_for_role(
                        role,
                        candidate.provider_profile_id,
                        candidate.model,
                    )
                    pricing_source = "manual"
                    if response_payload and provider_cost(response_payload) is None and (
                        pricing is None
                        or not any(value is not None for value in pricing.model_dump().values())
                    ):
                        pricing = await openrouter_catalog_pricing(
                            candidate.base_url,
                            candidate.model,
                            candidate.api_key,
                            candidate.provider_profile_id,
                        )
                        pricing_source = "catalog"
                    record_model_call(
                        feature=feature_for_role(role),
                        role=role,
                        provider=candidate.provider,
                        provider_profile_id=candidate.provider_profile_id,
                        requested_model=candidate.model,
                        tier=candidate.tier,
                        attempt=retry_index + 1,
                        status="failed",
                        error_type=error_type,
                        started_at=started_at,
                        elapsed_ms=elapsed_ms,
                        response_payload=response_payload,
                        pricing=pricing,
                        pricing_source=pricing_source,
                    )
                    attempts.append(LlmRouteAttempt(
                        role=role,
                        provider_profile_id=candidate.provider_profile_id,
                        model=candidate.model,
                        tier=candidate.tier,
                        elapsed_ms=elapsed_ms,
                        outcome="failed",
                        error_type=error_type,
                        error=str(exc)[:500],
                    ))
                    logger.warning(
                        "llm_route role=%s profile=%s model=%s tier=%s outcome=failed error_type=%s elapsed_ms=%s",
                        role,
                        candidate.provider_profile_id,
                        candidate.model,
                        candidate.tier,
                        error_type,
                        elapsed_ms,
                    )
                    if not _retryable(error_type) or retry_index + 1 >= retries_per_candidate:
                        break
                    await asyncio.sleep(min(2 ** retry_index, 4))
    if last_quality_result is not None:
        last_quality_result.attempts = attempts
        return last_quality_result
    raise LlmRoutingError(role, attempts)


def route_chat_completion_sync(*args: Any, **kwargs: Any) -> RoutedChatResult[Any]:
    """同步业务服务使用的包装器；不得在已有事件循环线程内调用。"""
    return asyncio.run(route_chat_completion(*args, **kwargs))
