from datetime import datetime, timezone
import json

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.models.app_config import ModelPricingConfig
from backend.models.model_usage import ModelCallRecord, ModelUsage
from backend.routers import config as config_router
from backend.services import provider_pricing
from backend.services.provider_pricing import (
    ProviderPricingCatalog,
    parse_routerhub_pricing,
    reprice_model_call,
)


def _markdown(count: int = 2) -> str:
    return "\n".join(
        f"""
vendor/model-{index}

Model {index} description.1.0M${index + 1} input${index + 2}.5 output
""".strip()
        for index in range(count)
    )


def test_parse_routerhub_public_model_prices():
    prices = parse_routerhub_pricing(_markdown())
    assert prices["vendor/model-0"].input_per_million_tokens == 1
    assert prices["vendor/model-0"].output_per_million_tokens == 2.5
    assert prices["vendor/model-1"].input_per_million_tokens == 2


def test_parse_routerhub_public_models_api():
    payload = json.dumps({
        "data": {
            "models": [{
                "model_slug": "openai/gpt-demo",
                "pricing_summary": "$0.25 input / $1.5 output",
            }]
        }
    })
    prices = parse_routerhub_pricing(payload)
    assert prices["openai/gpt-demo"].input_per_million_tokens == 0.25
    assert prices["openai/gpt-demo"].output_per_million_tokens == 1.5


def test_reprices_routerhub_estimates_but_preserves_provider_cost(tmp_path, monkeypatch):
    monkeypatch.setattr(
        provider_pricing,
        "PROVIDER_PRICING_PATH",
        tmp_path / "provider_pricing.json",
    )
    monkeypatch.setattr(provider_pricing, "DATA_DIR", tmp_path)
    catalog = ProviderPricingCatalog(
        provider_profile_id="routerhub",
        source_url=provider_pricing.ROUTERHUB_MODELS_URL,
        registered_at="2026-08-01T00:00:00+00:00",
        synced_at="2026-08-01T01:00:00+00:00",
        models={
            "vendor/model": ModelPricingConfig(
                input_per_million_tokens=2,
                output_per_million_tokens=6,
            )
        },
    )
    provider_pricing._save_all({"routerhub": catalog})
    now = datetime.now(timezone.utc).isoformat()
    record = ModelCallRecord(
        task_id="task-1",
        provider_profile_id="routerhub",
        feature="audit",
        provider="openai_compatible",
        requested_model="vendor/model",
        actual_model="vendor/model",
        status="success",
        started_at=now,
        usage=ModelUsage(
            input_tokens=1_000_000,
            output_tokens=500_000,
            total_tokens=1_500_000,
        ),
        cost_usd="99",
        cost_source="manual",
    )
    repriced = reprice_model_call(record)
    assert repriced.cost_usd == "5.0"
    assert repriced.cost_source == "catalog"
    assert repriced.price_snapshot is not None

    actual = reprice_model_call(
        record.model_copy(update={"cost_usd": "7", "cost_source": "provider"})
    )
    assert actual.cost_usd == "7"
    assert actual.cost_source == "provider"


@pytest.mark.asyncio
async def test_sync_records_first_registration_and_latest_sync(tmp_path, monkeypatch):
    monkeypatch.setattr(
        provider_pricing,
        "PROVIDER_PRICING_PATH",
        tmp_path / "provider_pricing.json",
    )
    monkeypatch.setattr(provider_pricing, "DATA_DIR", tmp_path)

    async def fake_fetch():
        return _markdown(10)

    monkeypatch.setattr(provider_pricing, "_fetch_routerhub_pricing_text", fake_fetch)
    first = await provider_pricing.sync_routerhub_pricing()
    second = await provider_pricing.sync_routerhub_pricing()
    assert first.registered_at == second.registered_at
    assert second.synced_at >= first.synced_at
    assert len(second.models) == 10


def test_provider_pricing_status_and_sync_api(monkeypatch):
    catalog = ProviderPricingCatalog(
        provider_profile_id="routerhub",
        source_url=provider_pricing.ROUTERHUB_MODELS_URL,
        registered_at="2026-08-01T00:00:00+00:00",
        synced_at="2026-08-01T01:00:00+00:00",
        models={
            "openai/gpt-demo": ModelPricingConfig(
                input_per_million_tokens=1,
                output_per_million_tokens=2,
            )
        },
    )

    async def fake_sync():
        return catalog

    monkeypatch.setattr(
        config_router,
        "list_provider_pricing_statuses",
        lambda: [catalog.status_payload()],
    )
    monkeypatch.setattr(config_router, "sync_routerhub_pricing", fake_sync)
    client = TestClient(app)

    status = client.get("/api/config/provider-pricing")
    assert status.status_code == 200
    assert status.json()["providers"][0]["model_count"] == 1

    synced = client.post("/api/config/provider-pricing/routerhub/sync")
    assert synced.status_code == 200
    assert synced.json()["provider"]["synced_at"] == catalog.synced_at

    unsupported = client.post("/api/config/provider-pricing/openrouter/sync")
    assert unsupported.status_code == 400
