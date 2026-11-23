from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from backend.models.app_config import ModelPricingConfig
from backend.models.model_usage import ModelCallRecord, ModelUsage
from backend.services import model_usage
from backend.services.model_usage import (
    estimate_cost,
    extract_usage,
    model_usage_task_context,
    provider_cost,
    record_model_call,
)
from backend.services.model_usage_store import ModelUsageStore


def _record(
    task_id: str,
    feature: str,
    *,
    cost: str | None,
    source: str = "provider",
    tokens: int = 10,
) -> ModelCallRecord:
    now = datetime.now(timezone.utc).isoformat()
    return ModelCallRecord(
        task_id=task_id,
        feature=feature,
        provider="openai_compatible",
        requested_model="demo",
        actual_model="demo",
        status="success",
        started_at=now,
        completed_at=now,
        usage=ModelUsage(input_tokens=4, output_tokens=6, total_tokens=tokens),
        cost_usd=cost,
        cost_source=source,
    )


def test_extracts_openai_and_openrouter_usage_and_provider_cost():
    payload = {
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 25,
            "total_tokens": 125,
            "cost": "0.00125",
            "prompt_tokens_details": {"cached_tokens": 20},
            "completion_tokens_details": {"reasoning_tokens": 5},
        }
    }
    usage = extract_usage(payload)
    assert usage.input_tokens == 100
    assert usage.output_tokens == 25
    assert usage.cached_input_tokens == 20
    assert usage.reasoning_tokens == 5
    assert provider_cost(payload) == Decimal("0.00125")


def test_estimate_cost_uses_decimal_rates():
    usage = ModelUsage(input_tokens=1_000_000, output_tokens=500_000, total_tokens=1_500_000)
    cost, snapshot = estimate_cost(
        usage,
        ModelPricingConfig(
            input_per_million_tokens=1.25,
            output_per_million_tokens=4.5,
        ),
    )
    assert cost == Decimal("3.500")
    assert snapshot is not None
    assert snapshot.input_per_token == "0.00000125"


def test_store_summarizes_features_and_pages_calls(tmp_path):
    store = ModelUsageStore(tmp_path / "usage.sqlite3")
    store.append(_record("task-1", "audit", cost="0.001"))
    store.append(_record("task-1", "audit", cost=None, source="unavailable"))
    store.append(_record("task-1", "tts", cost="0", source="cache", tokens=0))
    store.append(_record("task-2", "audit", cost="99"))

    summary = store.summarize("task-1")
    assert summary.cost_usd == "0.001"
    assert summary.call_count == 3
    assert summary.total_tokens == 20
    assert summary.unknown_cost_count == 1
    assert [item.feature for item in summary.features] == ["audit", "tts"]

    first = store.list_calls("task-1", feature="audit", limit=1)
    assert len(first.items) == 1
    assert first.next_cursor is not None
    second = store.list_calls("task-1", feature="audit", cursor=first.next_cursor, limit=1)
    assert len(second.items) == 1
    assert second.next_cursor is None

    store.delete_task("task-1")
    assert store.summarize("task-1").call_count == 0
    assert store.summarize("task-2").cost_usd == "99"


def test_direct_task_context_binds_calls_without_execution_run(monkeypatch):
    captured = []

    class FakeStore:
        def append(self, record):
            captured.append(record)
            return record

    monkeypatch.setattr(model_usage, "get_model_usage_store", lambda: FakeStore())
    with model_usage_task_context("direct-task"):
        record_model_call(
            feature="golden_quote",
            role="golden_quote.extract",
            provider="openai_compatible",
            requested_model="demo",
            status="success",
            started_at=datetime.now(timezone.utc).isoformat(),
            elapsed_ms=5,
        )

    assert len(captured) == 1
    assert captured[0].task_id == "direct-task"
    assert captured[0].execution_run_id is None
