"""供应商公开报价同步、持久化与历史估算重算。"""

from __future__ import annotations

import html
import json
import os
import re
import threading
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional

import httpx
from pydantic import BaseModel, Field

from ..models.app_config import ModelPricingConfig
from ..models.model_usage import ModelCallRecord, PriceSnapshot


DATA_DIR = Path(__file__).parent.parent / "data"
PROVIDER_PRICING_PATH = DATA_DIR / "provider_pricing.json"
ROUTERHUB_PROFILE_ID = "routerhub"
ROUTERHUB_MODELS_URL = "https://www.routerhub.ai/models"
ROUTERHUB_PUBLIC_MODELS_API = "https://www.routerhub.ai/api/public-models"
ROUTERHUB_READER_URL = f"https://r.jina.ai/{ROUTERHUB_MODELS_URL}"
_MIN_ROUTERHUB_MODELS = 10
_MODEL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")
_PRICE_RE = re.compile(
    r"(?P<context>\d+(?:\.\d+)?\s*[KMB])\s*"
    r"\$(?P<input>\d+(?:\.\d+)?)\s*input\s*"
    r"\$(?P<output>\d+(?:\.\d+)?)\s*output",
    re.IGNORECASE,
)
_SUMMARY_PRICE_RE = re.compile(
    r"\$(?P<input>\d+(?:\.\d+)?)\s*input\s*/\s*"
    r"\$(?P<output>\d+(?:\.\d+)?)\s*output",
    re.IGNORECASE,
)
_lock = threading.RLock()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProviderPricingCatalog(BaseModel):
    provider_profile_id: str
    source_url: str
    registered_at: str
    synced_at: str
    models: dict[str, ModelPricingConfig] = Field(default_factory=dict)

    def status_payload(self) -> dict:
        return {
            "provider_profile_id": self.provider_profile_id,
            "source_url": self.source_url,
            "registered_at": self.registered_at,
            "synced_at": self.synced_at,
            "model_count": len(self.models),
        }


def _load_all() -> dict[str, ProviderPricingCatalog]:
    with _lock:
        if not PROVIDER_PRICING_PATH.exists():
            return {}
        try:
            raw = json.loads(PROVIDER_PRICING_PATH.read_text(encoding="utf-8"))
            providers = raw.get("providers", {}) if isinstance(raw, dict) else {}
            return {
                str(provider_id): ProviderPricingCatalog.model_validate(payload)
                for provider_id, payload in providers.items()
                if isinstance(payload, dict)
            }
        except Exception:
            return {}


def _save_all(catalogs: dict[str, ProviderPricingCatalog]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "providers": {
            provider_id: catalog.model_dump(mode="json")
            for provider_id, catalog in catalogs.items()
        },
    }
    temp_path = PROVIDER_PRICING_PATH.with_suffix(PROVIDER_PRICING_PATH.suffix + ".tmp")
    with _lock:
        try:
            temp_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temp_path, PROVIDER_PRICING_PATH)
        except Exception:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass
            raise


def get_provider_pricing_catalog(
    provider_profile_id: str,
) -> Optional[ProviderPricingCatalog]:
    return _load_all().get(provider_profile_id)


def list_provider_pricing_statuses() -> list[dict]:
    return [
        catalog.status_payload()
        for _, catalog in sorted(_load_all().items())
    ]


def pricing_for_provider_model(
    provider_profile_id: Optional[str],
    model: str,
) -> Optional[ModelPricingConfig]:
    if not provider_profile_id:
        return None
    catalog = get_provider_pricing_catalog(provider_profile_id)
    return catalog.models.get(model) if catalog else None


def provider_profile_id_for_base_url(base_url: Optional[str]) -> Optional[str]:
    if base_url and "routerhub.ai" in base_url.lower():
        return ROUTERHUB_PROFILE_ID
    return None


def _visible_text(raw: str) -> str:
    """把公开页面 HTML/RSC 文本保守转换成可供报价正则扫描的文本。"""
    unescaped = html.unescape(raw).replace("\\n", "\n").replace('\\"', '"')
    without_scripts = re.sub(r"<script[^>]*>", "\n", unescaped, flags=re.IGNORECASE)
    without_tags = re.sub(r"<[^>]+>", "\n", without_scripts)
    return re.sub(r"[ \t]+", " ", without_tags)


def parse_routerhub_pricing(raw: str) -> dict[str, ModelPricingConfig]:
    """解析 RouterHub 模型页或其可读 Markdown 镜像。"""
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        payload = None
    if isinstance(payload, dict):
        data = payload.get("data")
        raw_models = data.get("models", []) if isinstance(data, dict) else []
        api_prices: dict[str, ModelPricingConfig] = {}
        for item in raw_models if isinstance(raw_models, list) else []:
            if not isinstance(item, dict):
                continue
            model_id = str(item.get("model_slug") or "").strip()
            summary = str(item.get("pricing_summary") or "")
            match = _SUMMARY_PRICE_RE.search(summary)
            if not _MODEL_ID_RE.fullmatch(model_id) or not match:
                continue
            api_prices[model_id] = ModelPricingConfig(
                input_per_million_tokens=float(match.group("input")),
                output_per_million_tokens=float(match.group("output")),
            )
        if api_prices:
            return api_prices

    text = _visible_text(raw)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    prices: dict[str, ModelPricingConfig] = {}
    current_model: Optional[str] = None
    current_lines: list[str] = []

    def flush() -> None:
        if not current_model:
            return
        match = _PRICE_RE.search(" ".join(current_lines))
        if not match:
            return
        prices[current_model] = ModelPricingConfig(
            input_per_million_tokens=float(match.group("input")),
            output_per_million_tokens=float(match.group("output")),
        )

    for line in lines:
        if _MODEL_ID_RE.fullmatch(line):
            flush()
            current_model = line
            current_lines = []
        elif current_model:
            current_lines.append(line)
    flush()
    return prices


async def _fetch_routerhub_pricing_text() -> str:
    errors: list[str] = []
    transport = httpx.AsyncHTTPTransport(proxy=None)
    headers = {"User-Agent": "GoldenClip/1.0 RouterHub pricing sync"}
    async with httpx.AsyncClient(
        transport=transport,
        timeout=30.0,
        follow_redirects=True,
        headers=headers,
    ) as client:
        for url in (
            ROUTERHUB_PUBLIC_MODELS_API,
            ROUTERHUB_MODELS_URL,
            ROUTERHUB_READER_URL,
        ):
            try:
                response = await client.get(url)
                response.raise_for_status()
                prices = parse_routerhub_pricing(response.text)
                if len(prices) >= _MIN_ROUTERHUB_MODELS:
                    return response.text
                errors.append(f"{url}: 仅解析到 {len(prices)} 个模型")
            except Exception as exc:
                errors.append(f"{url}: {type(exc).__name__}")
    raise RuntimeError("；".join(errors))


async def sync_routerhub_pricing() -> ProviderPricingCatalog:
    raw = await _fetch_routerhub_pricing_text()
    prices = parse_routerhub_pricing(raw)
    if len(prices) < _MIN_ROUTERHUB_MODELS:
        raise ValueError("RouterHub 报价页结构异常，未覆盖足够模型")
    catalogs = _load_all()
    previous = catalogs.get(ROUTERHUB_PROFILE_ID)
    now = _utc_now()
    catalog = ProviderPricingCatalog(
        provider_profile_id=ROUTERHUB_PROFILE_ID,
        source_url=ROUTERHUB_MODELS_URL,
        registered_at=previous.registered_at if previous else now,
        synced_at=now,
        models=prices,
    )
    catalogs[ROUTERHUB_PROFILE_ID] = catalog
    _save_all(catalogs)
    return catalog


def _decimal(value: object) -> Optional[Decimal]:
    if value in (None, ""):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def reprice_model_call(record: ModelCallRecord) -> ModelCallRecord:
    """用最新登记价重算历史估算；供应商实际账单与缓存费用保持不变。"""
    if record.cost_source in {"provider", "cache"}:
        return record
    pricing = pricing_for_provider_model(
        record.provider_profile_id,
        record.actual_model or record.requested_model,
    )
    if pricing is None:
        return record
    input_rate = _decimal(pricing.input_per_million_tokens)
    output_rate = _decimal(pricing.output_per_million_tokens)
    reasoning_rate = _decimal(pricing.reasoning_per_million_tokens)
    request_rate = _decimal(pricing.per_request)
    if not any(
        rate is not None
        for rate in (input_rate, output_rate, reasoning_rate, request_rate)
    ):
        return record
    million = Decimal("1000000")
    cost = Decimal("0")
    if input_rate is not None:
        cost += Decimal(record.usage.input_tokens or 0) * input_rate / million
    if output_rate is not None:
        cost += Decimal(record.usage.output_tokens or 0) * output_rate / million
    if reasoning_rate is not None:
        cost += Decimal(record.usage.reasoning_tokens or 0) * reasoning_rate / million
    if request_rate is not None:
        cost += request_rate
    snapshot = PriceSnapshot(
        input_per_token=format(input_rate / million, "f") if input_rate is not None else None,
        output_per_token=format(output_rate / million, "f") if output_rate is not None else None,
        reasoning_per_token=(
            format(reasoning_rate / million, "f")
            if reasoning_rate is not None
            else None
        ),
        request_price=format(request_rate, "f") if request_rate is not None else None,
        source="catalog",
    )
    return record.model_copy(update={
        "cost_usd": format(cost, "f"),
        "cost_source": "catalog",
        "price_snapshot": snapshot,
    })
