"""Cloud model usage parsing, pricing and task-ledger recording."""

from __future__ import annotations

import asyncio
import contextvars
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

import httpx

from ..models.app_config import ModelPricingConfig
from ..models.model_usage import (
    ModelCallRecord,
    ModelUsage,
    PriceSnapshot,
)
from .model_usage_store import get_model_usage_store
from .provider_pricing import (
    ROUTERHUB_PROFILE_ID,
    pricing_for_provider_model,
)


ROLE_FEATURES = {
    "production.plan": "production",
    "production.visual": "production",
    "audit.semantic": "audit",
    "audit.coherence": "audit_coherence",
    "translate.subtitle": "translate",
    "golden_quote.extract": "golden_quote",
    "highlight.strategy": "highlight_strategy",
    "highlight.validation": "highlight_validation",
    "learning.analyze": "learning",
    "learning.rewrite": "learning",
    "prompt_feedback.optimize": "prompt_feedback",
}
ROLE_CONFIG_FEATURES = {
    "production.plan": "highlight_reel",
    "production.visual": "highlight_reel",
    "audit.semantic": "audit",
    "audit.coherence": "audit",
    "translate.subtitle": "translate",
    "golden_quote.extract": "golden_quote",
    "highlight.strategy": "highlight_reel",
    "highlight.validation": "highlight_reel",
    "learning.analyze": "learning",
    "learning.rewrite": "learning",
    "prompt_feedback.optimize": "learning",
}
_CATALOG_TTL_SECONDS = 3600
_catalog_cache: dict[str, tuple[float, dict[str, ModelPricingConfig]]] = {}
_catalog_lock = asyncio.Lock()
_active_usage_task_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "goldenclip_model_usage_task_id",
    default=None,
)


def feature_for_role(role: str) -> str:
    return ROLE_FEATURES.get(role, role.split(".", 1)[0])


@contextmanager
def model_usage_task_context(task_id: str):
    """Bind direct model calls that do not run through the task runner."""
    token = _active_usage_task_id.set(task_id)
    try:
        yield
    finally:
        _active_usage_task_id.reset(token)


def manual_pricing_for_role(
    role: str,
    provider_profile_id: str,
    model: str,
) -> Optional[ModelPricingConfig]:
    try:
        from .app_config_store import load_app_config

        route = load_app_config().llm_role_routes.get(role)
    except Exception:
        return None
    if route is None:
        return None
    for candidate in route.candidates:
        if (
            candidate.provider_profile_id == provider_profile_id
            and candidate.model == model
        ):
            if any(value is not None for value in candidate.pricing.model_dump().values()):
                return candidate.pricing
            break
    config_feature = ROLE_CONFIG_FEATURES.get(role)
    return manual_pricing_for_feature(config_feature) if config_feature else None


def manual_pricing_for_feature(feature: str) -> Optional[ModelPricingConfig]:
    try:
        from .app_config_store import load_app_config

        config = getattr(load_app_config().feature_llm, feature)
        return config.cloud.pricing
    except Exception:
        return None


def _as_dict(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        return payload
    model_dump = getattr(payload, "model_dump", None)
    if callable(model_dump):
        result = model_dump()
        return result if isinstance(result, dict) else {}
    return {}


def _non_negative_int(value: Any) -> Optional[int]:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return max(0, parsed)


def extract_usage(payload: Any) -> ModelUsage:
    body = _as_dict(payload)
    raw = body.get("usage")
    usage = _as_dict(raw)
    prompt_details = _as_dict(usage.get("prompt_tokens_details"))
    completion_details = _as_dict(usage.get("completion_tokens_details"))
    input_tokens = _non_negative_int(
        usage.get("prompt_tokens", usage.get("input_tokens"))
    )
    output_tokens = _non_negative_int(
        usage.get("completion_tokens", usage.get("output_tokens"))
    )
    reasoning_tokens = _non_negative_int(
        completion_details.get("reasoning_tokens", usage.get("reasoning_tokens"))
    )
    cached_input_tokens = _non_negative_int(
        prompt_details.get("cached_tokens", usage.get("cached_input_tokens"))
    )
    total_tokens = _non_negative_int(usage.get("total_tokens"))
    if total_tokens is None and (input_tokens is not None or output_tokens is not None):
        total_tokens = (input_tokens or 0) + (output_tokens or 0)
    return ModelUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        reasoning_tokens=reasoning_tokens,
        cached_input_tokens=cached_input_tokens,
        total_tokens=total_tokens,
    )


def _decimal(value: Any) -> Optional[Decimal]:
    if value in (None, ""):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def provider_cost(payload: Any) -> Optional[Decimal]:
    body = _as_dict(payload)
    usage = _as_dict(body.get("usage"))
    cost_details = _as_dict(usage.get("cost_details"))
    for value in (
        usage.get("cost"),
        usage.get("total_cost"),
        cost_details.get("upstream_inference_cost"),
        body.get("cost"),
    ):
        parsed = _decimal(value)
        if parsed is not None:
            return parsed
    return None


def estimate_cost(
    usage: ModelUsage,
    pricing: Optional[ModelPricingConfig],
    *,
    character_count: int = 0,
) -> tuple[Optional[Decimal], Optional[PriceSnapshot]]:
    if pricing is None:
        return None, None
    million = Decimal("1000000")
    input_rate = _decimal(pricing.input_per_million_tokens)
    output_rate = _decimal(pricing.output_per_million_tokens)
    reasoning_rate = _decimal(pricing.reasoning_per_million_tokens)
    request_rate = _decimal(pricing.per_request)
    character_rate = _decimal(pricing.per_million_characters)
    known = any(
        rate is not None
        for rate in (input_rate, output_rate, reasoning_rate, request_rate, character_rate)
    )
    if not known:
        return None, None
    cost = Decimal("0")
    if input_rate is not None:
        cost += Decimal(usage.input_tokens or 0) * input_rate / million
    if output_rate is not None:
        cost += Decimal(usage.output_tokens or 0) * output_rate / million
    if reasoning_rate is not None:
        cost += Decimal(usage.reasoning_tokens or 0) * reasoning_rate / million
    if request_rate is not None:
        cost += request_rate
    if character_rate is not None:
        cost += Decimal(max(0, character_count)) * character_rate / million
    snapshot = PriceSnapshot(
        input_per_token=format(input_rate / million, "f") if input_rate is not None else None,
        output_per_token=format(output_rate / million, "f") if output_rate is not None else None,
        reasoning_per_token=format(reasoning_rate / million, "f") if reasoning_rate is not None else None,
        request_price=format(request_rate, "f") if request_rate is not None else None,
        character_price=format(character_rate / million, "f") if character_rate is not None else None,
    )
    return cost, snapshot


async def openrouter_catalog_pricing(
    base_url: str,
    model: str,
    api_key: str = "",
    provider_profile_id: Optional[str] = None,
) -> Optional[ModelPricingConfig]:
    catalog_profile_id = provider_profile_id
    if not catalog_profile_id and "routerhub.ai" in base_url:
        catalog_profile_id = ROUTERHUB_PROFILE_ID
    persisted = pricing_for_provider_model(catalog_profile_id, model)
    if persisted is not None:
        return persisted
    if "openrouter.ai" not in base_url:
        return None
    cache_key = base_url.rstrip("/")
    now = time.monotonic()
    cached = _catalog_cache.get(cache_key)
    if cached and now - cached[0] < _CATALOG_TTL_SECONDS:
        return cached[1].get(model)
    async with _catalog_lock:
        cached = _catalog_cache.get(cache_key)
        if cached and now - cached[0] < _CATALOG_TTL_SECONDS:
            return cached[1].get(model)
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(f"{cache_key}/models", headers=headers)
                response.raise_for_status()
                data = response.json().get("data", [])
        except Exception:
            return None
        prices: dict[str, ModelPricingConfig] = {}
        for item in data if isinstance(data, list) else []:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            pricing = item.get("pricing") if isinstance(item.get("pricing"), dict) else {}
            prompt = _decimal(pricing.get("prompt"))
            completion = _decimal(pricing.get("completion"))
            request = _decimal(pricing.get("request"))
            prices[str(item["id"])] = ModelPricingConfig(
                input_per_million_tokens=float(prompt * Decimal("1000000")) if prompt is not None else None,
                output_per_million_tokens=float(completion * Decimal("1000000")) if completion is not None else None,
                per_request=float(request) if request is not None else None,
            )
        _catalog_cache[cache_key] = (time.monotonic(), prices)
        return prices.get(model)


def record_model_call(
    *,
    feature: str,
    provider: str,
    requested_model: str,
    status: str,
    started_at: str,
    elapsed_ms: int,
    response_payload: Any = None,
    task_id: Optional[str] = None,
    execution_run_id: Optional[str] = None,
    execution_generation: Optional[int] = None,
    role: Optional[str] = None,
    stage: Optional[str] = None,
    provider_profile_id: Optional[str] = None,
    tier: Optional[str] = None,
    attempt: int = 1,
    error_type: Optional[str] = None,
    pricing: Optional[ModelPricingConfig] = None,
    pricing_source: str = "manual",
    character_count: int = 0,
    from_cache: bool = False,
    cache_key: Optional[str] = None,
    source_call_id: Optional[str] = None,
) -> Optional[ModelCallRecord]:
    try:
        from ..core.task_runner import current_execution_binding

        binding = current_execution_binding()
    except Exception:
        binding = None
    task_id = task_id or _active_usage_task_id.get() or (binding[0] if binding else None)
    if binding is not None and binding[0] == task_id:
        if execution_run_id is None:
            execution_run_id = binding[1].run_id
        if execution_generation is None:
            execution_generation = binding[1].generation
    if not task_id or provider in {"ollama", "silent", "local"}:
        return None

    payload = _as_dict(response_payload)
    usage = extract_usage(payload)
    actual_cost = provider_cost(payload)
    estimated_cost, snapshot = estimate_cost(usage, pricing, character_count=character_count)
    if from_cache:
        cost = Decimal("0")
        cost_source = "cache"
    elif actual_cost is not None:
        cost = actual_cost
        cost_source = "provider"
    elif estimated_cost is not None:
        cost = estimated_cost
        cost_source = pricing_source if pricing_source in {"catalog", "manual"} else "manual"
    else:
        cost = None
        cost_source = "unavailable"
    if snapshot is not None:
        snapshot.source = cost_source

    raw_usage = _as_dict(payload.get("usage"))
    safe_raw = {
        key: value
        for key, value in raw_usage.items()
        if isinstance(value, (int, float, str, type(None), bool))
    }
    record = ModelCallRecord(
        task_id=task_id,
        execution_run_id=execution_run_id,
        execution_generation=execution_generation,
        feature=feature,
        role=role,
        stage=stage,
        provider_profile_id=provider_profile_id,
        provider=provider,
        requested_model=requested_model,
        actual_model=str(payload.get("model") or requested_model),
        tier=tier,
        provider_request_id=str(payload.get("id")) if payload.get("id") else None,
        attempt=attempt,
        status=status,  # type: ignore[arg-type]
        error_type=error_type,
        started_at=started_at,
        completed_at=datetime.now(timezone.utc).isoformat(),
        elapsed_ms=max(0, elapsed_ms),
        from_cache=from_cache,
        cache_key=cache_key,
        source_call_id=source_call_id,
        usage=usage,
        cost_usd=format(cost, "f") if cost is not None else None,
        cost_source=cost_source,  # type: ignore[arg-type]
        price_snapshot=snapshot,
        usage_raw=safe_raw,
    )
    return get_model_usage_store().append(record)
