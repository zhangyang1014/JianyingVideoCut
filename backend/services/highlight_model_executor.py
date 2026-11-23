"""产品集锦模型请求的异步客户端、限流、重试与取消边界。"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

import httpx
import openai

from .model_usage import (
    manual_pricing_for_feature,
    openrouter_catalog_pricing,
    provider_cost,
    record_model_call,
)
from .provider_pricing import provider_profile_id_for_base_url


class HighlightModelFatalError(RuntimeError):
    """认证、模型或请求配置错误；同一批后续请求不应继续派发。"""


class HighlightModelBatchAborted(RuntimeError):
    """同批次已发生系统性错误，排队请求不再发往供应商。"""


@dataclass
class HighlightModelCallBudget:
    """一次产品集锦顶层执行共享的逻辑模型调用预算。"""

    max_calls: int
    used_calls: int = 0
    _lock: Optional[asyncio.Lock] = None

    async def consume(self) -> None:
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            if self.used_calls >= max(0, self.max_calls):
                raise HighlightModelBatchAborted(
                    f"产品集锦模型调用预算已用尽（{self.used_calls}/{self.max_calls}）"
                )
            self.used_calls += 1

    @property
    def remaining_calls(self) -> int:
        return max(0, self.max_calls - self.used_calls)


@dataclass
class _ProviderGate:
    semaphore: asyncio.Semaphore
    cooldown_until: float = 0.0
    cooldown_lock: Optional[asyncio.Lock] = None

    async def wait_for_cooldown(self) -> None:
        delay = self.cooldown_until - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)

    async def cool_down(self, seconds: float) -> None:
        if self.cooldown_lock is None:
            self.cooldown_lock = asyncio.Lock()
        async with self.cooldown_lock:
            self.cooldown_until = max(
                self.cooldown_until,
                time.monotonic() + max(0.0, seconds),
            )


_PROVIDER_GATES: Dict[Tuple[int, str, str, str, int], _ProviderGate] = {}


def _provider_gate(
    provider: str,
    base_url: str,
    model: str,
    max_parallel_calls: int,
) -> _ProviderGate:
    """同一事件循环内，相同端点和模型共享进程级请求上限。"""
    loop_id = id(asyncio.get_running_loop())
    effective_limit = 1 if provider == "ollama" else max(1, max_parallel_calls)
    key = (loop_id, provider, base_url.rstrip("/"), model, effective_limit)
    gate = _PROVIDER_GATES.get(key)
    if gate is None:
        gate = _ProviderGate(asyncio.Semaphore(effective_limit))
        _PROVIDER_GATES[key] = gate
    return gate


def _status_code(exc: BaseException) -> Optional[int]:
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status
    response = getattr(exc, "response", None)
    response_status = getattr(response, "status_code", None)
    return response_status if isinstance(response_status, int) else None


def _retry_after_seconds(exc: BaseException) -> float:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if not headers:
        return 1.0
    try:
        return max(0.0, min(float(headers.get("retry-after", "1")), 30.0))
    except (TypeError, ValueError):
        return 1.0


def _is_transient(exc: BaseException) -> bool:
    status = _status_code(exc)
    return status in {408, 409, 429} or bool(status and status >= 500) or isinstance(
        exc,
        (httpx.TimeoutException, httpx.TransportError, openai.APITimeoutError, openai.APIConnectionError),
    )


def _is_fatal(exc: BaseException) -> bool:
    status = _status_code(exc)
    return status in {400, 401, 403, 404, 422}


class HighlightModelExecutor:
    """一批产品集锦请求共享的异步客户端和有界并发执行器。"""

    def __init__(
        self,
        *,
        provider: str,
        model: str,
        api_key: str,
        base_url: str,
        max_parallel_calls: int,
        request_timeout: float = 20.0,
        max_retries: int = 1,
        feature: str = "highlight_strategy",
        role: str = "highlight.strategy",
    ) -> None:
        self.provider = "ollama" if provider == "ollama" else "openai"
        self.model = model
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.feature = feature
        self.role = role
        self.request_timeout = request_timeout
        self.max_retries = max(0, max_retries)
        self.max_parallel_calls = 1 if self.provider == "ollama" else max(1, max_parallel_calls)
        self._batch_semaphore = asyncio.Semaphore(self.max_parallel_calls)
        self._gate = _provider_gate(
            self.provider,
            self.base_url,
            self.model,
            self.max_parallel_calls,
        )
        self._http_client = httpx.AsyncClient(
            transport=httpx.AsyncHTTPTransport(proxy=None),
            timeout=httpx.Timeout(
                connect=min(20.0, request_timeout),
                read=request_timeout,
                write=min(20.0, request_timeout),
                pool=request_timeout,
            ),
            limits=httpx.Limits(
                max_connections=self.max_parallel_calls,
                max_keepalive_connections=self.max_parallel_calls,
            ),
        )
        resolved_base_url = (
            f"{self.base_url}/v1" if self.provider == "ollama" else self.base_url
        )
        self._client = openai.AsyncOpenAI(
            api_key="ollama" if self.provider == "ollama" else api_key,
            base_url=resolved_base_url,
            http_client=self._http_client,
            max_retries=0,
        )

    async def __aenter__(self) -> "HighlightModelExecutor":
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.close()

    async def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        max_tokens: int = 4096,
        abort_event: Optional[asyncio.Event] = None,
    ) -> str:
        """执行一次可取消请求；排队等待不计入单请求超时。"""
        async with self._batch_semaphore:
            async with self._gate.semaphore:
                if abort_event is not None and abort_event.is_set():
                    raise HighlightModelBatchAborted("同批次已停止派发")
                attempt = 0
                while True:
                    await self._gate.wait_for_cooldown()
                    started = time.perf_counter()
                    started_at = datetime.now(timezone.utc).isoformat()
                    try:
                        completion = await asyncio.wait_for(
                            self._client.chat.completions.create(
                                model=self.model,
                                max_tokens=max_tokens,
                                temperature=0.0,
                                messages=[
                                    {"role": "system", "content": system_prompt},
                                    {"role": "user", "content": user_prompt},
                                ],
                            ),
                            timeout=self.request_timeout,
                        )
                        dump = getattr(completion, "model_dump", None)
                        payload = dump() if callable(dump) else {}
                        pricing = manual_pricing_for_feature("highlight_reel")
                        pricing_source = "manual"
                        if provider_cost(payload) is None and (
                            pricing is None
                            or not any(value is not None for value in pricing.model_dump().values())
                        ):
                            pricing = await openrouter_catalog_pricing(
                                self.base_url,
                                self.model,
                                self.api_key,
                            )
                            pricing_source = "catalog"
                        record_model_call(
                            feature=self.feature,
                            role=self.role,
                            provider_profile_id=provider_profile_id_for_base_url(
                                self.base_url
                            ),
                            provider="ollama" if self.provider == "ollama" else "openai_compatible",
                            requested_model=self.model,
                            attempt=attempt + 1,
                            status="success",
                            started_at=started_at,
                            elapsed_ms=int((time.perf_counter() - started) * 1000),
                            response_payload=payload,
                            pricing=pricing,
                            pricing_source=pricing_source,
                        )
                        return completion.choices[0].message.content or ""
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        record_model_call(
                            feature=self.feature,
                            role=self.role,
                            provider_profile_id=provider_profile_id_for_base_url(
                                self.base_url
                            ),
                            provider="ollama" if self.provider == "ollama" else "openai_compatible",
                            requested_model=self.model,
                            attempt=attempt + 1,
                            status="failed",
                            error_type=type(exc).__name__,
                            started_at=started_at,
                            elapsed_ms=int((time.perf_counter() - started) * 1000),
                        )
                        retry_after = _retry_after_seconds(exc)
                        if _status_code(exc) == 429:
                            # 即使本执行器不负责重试，也要让同 provider 的新请求进入冷却。
                            await self._gate.cool_down(retry_after)
                        if _is_fatal(exc):
                            raise HighlightModelFatalError(str(exc)) from exc
                        if not _is_transient(exc) or attempt >= self.max_retries:
                            raise
                        attempt += 1
                        await asyncio.sleep(retry_after + random.uniform(0.0, 0.25))
