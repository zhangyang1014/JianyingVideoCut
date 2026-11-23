import asyncio
import json
from types import SimpleNamespace

import pytest

from backend.models.app_config import ResolvedFeatureLlm
from backend.models.task import (
    ProductEntity,
    ProductSemanticMark,
    Segment,
    SegmentAction,
    Task,
)
from backend.services.highlight_model_executor import (
    HighlightModelCallBudget,
    HighlightModelExecutor,
)
from backend.services.highlight_reel_plan import build_highlight_reel_plan
from backend.services.product_occurrence_detector import (
    group_product_occurrences,
    identify_product_occurrences,
)


def _segments(product: str = "产品A", prefix: str = "a", start: float = 0.0):
    return [
        Segment(
            id=f"{prefix}_{index}",
            start=start + index * 10,
            end=start + (index + 1) * 10,
            text=f"{product} 卖点 {index}",
            action=SegmentAction.KEEP,
            product_semantic=ProductSemanticMark(
                roles=["product_opening" if index == 0 else "selling_point"],
                product_entities=[ProductEntity(name=product, canonical_name=product)],
                selling_points=[f"卖点{index}"],
                confidence=0.9,
            ),
        )
        for index in range(4)
    ]


def _cloud_feature():
    return ResolvedFeatureLlm(
        feature="highlight_reel",
        mode="cloud",
        provider="openai_compatible",
        model="test-model",
        base_url="https://example.test/v1",
        api_key="test-key",
    )


@pytest.mark.asyncio
async def test_strategy_generation_is_bounded_and_ordered(monkeypatch):
    from backend.services import highlight_strategy_generator as generator

    segments = _segments() + _segments("产品B", "b", 50) + _segments("产品A", "a2", 100)
    occurrences = identify_product_occurrences(segments)
    product_groups = group_product_occurrences(occurrences, segments)
    active = 0
    maximum = 0

    class FakeExecutor:
        def __init__(self, **kwargs):
            self.limit = kwargs["max_parallel_calls"]
            self.semaphore = asyncio.Semaphore(self.limit)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def complete(self, system_prompt, user_prompt, **kwargs):
            nonlocal active, maximum
            async with self.semaphore:
                active += 1
                maximum = max(maximum, active)
                try:
                    payload = json.loads(user_prompt.split("\n\n", 1)[1])
                    await asyncio.sleep(0.015 if payload["occurrence_id"] == product_groups[0].product_group_id else 0.001)
                    segment_ids = [item["segment_id"] for item in payload["segments"]]
                    return json.dumps(
                        {
                            "segment_ids": segment_ids,
                            "title": f"{payload['product_name']}：核心卖点",
                            "rationale": "保持源时间顺序",
                        }
                    )
                finally:
                    active -= 1

    monkeypatch.setattr(generator, "HighlightModelExecutor", FakeExecutor)
    monkeypatch.setattr(generator, "merge_feature_llm_request", lambda *args, **kwargs: _cloud_feature())
    monkeypatch.setattr(generator, "load_content_cache", lambda *args, **kwargs: None)
    monkeypatch.setattr(generator, "save_content_cache", lambda *args, **kwargs: None)
    reels = await generator.generate_product_strategy_reels(
        segments,
        occurrences,
        max_parallel_calls=2,
        product_groups=product_groups,
    )

    expected = build_highlight_reel_plan(segments, product_groups=product_groups)
    assert maximum == 2
    assert len(reels) == len(product_groups) * 3
    assert [reel.reel_id for reel in reels] == [reel.reel_id for reel in expected]
    groups_by_id = {group.product_group_id: group for group in product_groups}
    for reel in reels:
        group_segment_ids = groups_by_id[reel.product_group_id].segment_ids
        assert reel.segment_ids == group_segment_ids


@pytest.mark.asyncio
async def test_validation_keeps_partial_failures_visible_and_ordered(monkeypatch):
    from backend.services import highlight_reel_validator as validator

    segments = _segments()
    occurrences = identify_product_occurrences(segments)
    reels = build_highlight_reel_plan(segments, product_occurrences=occurrences)
    active = 0
    maximum = 0
    calls = 0

    class FakeExecutor:
        def __init__(self, **kwargs):
            self.semaphore = asyncio.Semaphore(kwargs["max_parallel_calls"])

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def complete(self, system_prompt, user_prompt, **kwargs):
            nonlocal active, maximum, calls
            async with self.semaphore:
                calls += 1
                reel_id = json.loads(user_prompt.split("\n\n", 1)[1])[0]["reel_id"]
                active += 1
                maximum = max(maximum, active)
                try:
                    await asyncio.sleep(0.005)
                    if reel_id == reels[1].reel_id:
                        return "not-json"
                    return json.dumps(
                        {
                            "passed": True,
                            "score": 0.95,
                            "validator_model": "test-model",
                            "issues": [],
                            "repair_actions": [],
                        }
                    )
                finally:
                    active -= 1

    monkeypatch.setattr(validator, "HighlightModelExecutor", FakeExecutor)
    monkeypatch.setattr(validator, "merge_feature_llm_request", lambda *args, **kwargs: _cloud_feature())
    monkeypatch.setattr(validator, "load_content_cache", lambda *args, **kwargs: None)
    monkeypatch.setattr(validator, "save_content_cache", lambda *args, **kwargs: None)

    report = await validator.run_highlight_reel_validation(
        reels,
        segments,
        product_occurrences=occurrences,
        max_parallel_calls=2,
    )

    unverified = [issue for issue in report.issues if issue.code == "HR-MODEL-UNVERIFIED"]
    assert maximum == 2
    assert report.degraded is True
    assert [issue.reel_id for issue in unverified] == [reels[1].reel_id]
    assert report.score == pytest.approx(0.95)


@pytest.mark.asyncio
async def test_strategy_regeneration_only_calls_target_failed_reel(monkeypatch):
    from backend.services import highlight_strategy_generator as generator

    segments = _segments()
    occurrences = identify_product_occurrences(segments)
    reels = build_highlight_reel_plan(segments, product_occurrences=occurrences)
    calls = 0

    class FakeExecutor:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def complete(self, _system_prompt, user_prompt, **kwargs):
            nonlocal calls
            calls += 1
            payload = json.loads(user_prompt.split("\n\n", 1)[1])
            return json.dumps(
                {
                    "segment_ids": [
                        item["segment_id"] for item in payload["segments"]
                    ],
                    "title": "修复后的标题",
                    "rationale": "仅重跑失败策略",
                }
            )

    monkeypatch.setattr(generator, "HighlightModelExecutor", FakeExecutor)
    monkeypatch.setattr(
        generator,
        "merge_feature_llm_request",
        lambda *args, **kwargs: _cloud_feature(),
    )
    monkeypatch.setattr(generator, "load_content_cache", lambda *args, **kwargs: None)
    monkeypatch.setattr(generator, "save_content_cache", lambda *args, **kwargs: None)

    updated = await generator.generate_product_strategy_reels(
        segments,
        occurrences,
        existing_reels=reels,
        target_reel_ids={reels[1].reel_id},
    )

    assert calls == 1
    assert updated[1].title == "修复后的标题"
    assert updated[0].title == reels[0].title


@pytest.mark.asyncio
async def test_validation_stops_dispatch_when_call_budget_is_exhausted(monkeypatch):
    from backend.services import highlight_reel_validator as validator

    segments = _segments()
    occurrences = identify_product_occurrences(segments)
    reels = build_highlight_reel_plan(segments, product_occurrences=occurrences)
    calls = 0
    budget = HighlightModelCallBudget(max_calls=1)

    class FakeExecutor:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def complete(self, *_args, **_kwargs):
            nonlocal calls
            calls += 1
            return json.dumps(
                {
                    "passed": True,
                    "score": 0.95,
                    "validator_model": "test-model",
                    "issues": [],
                    "repair_actions": [],
                }
            )

    monkeypatch.setattr(validator, "HighlightModelExecutor", FakeExecutor)
    monkeypatch.setattr(
        validator,
        "merge_feature_llm_request",
        lambda *args, **kwargs: _cloud_feature(),
    )
    monkeypatch.setattr(validator, "load_content_cache", lambda *args, **kwargs: None)
    monkeypatch.setattr(validator, "save_content_cache", lambda *args, **kwargs: None)

    report = await validator.run_highlight_reel_validation(
        reels,
        segments,
        product_occurrences=occurrences,
        call_budget=budget,
    )

    assert calls == 1
    assert budget.used_calls == 1
    assert report.degraded is True


@pytest.mark.asyncio
async def test_role_routed_strategy_batches_three_strategies_per_product(monkeypatch):
    from backend.services import highlight_strategy_generator as generator

    segments = _segments()
    occurrences = identify_product_occurrences(segments)
    product_groups = group_product_occurrences(occurrences, segments)
    calls = 0

    async def fake_route(_role, _messages, *, validate, **_kwargs):
        nonlocal calls
        calls += 1
        content = json.dumps(
            {
                "results": [
                    {
                        "strategy": strategy,
                        "segment_ids": product_groups[0].segment_ids,
                        "title": f"{strategy} 标题",
                        "rationale": "批量生成",
                    }
                    for strategy in ("faithful", "balanced", "concise")
                ]
            }
        )
        return SimpleNamespace(value=validate(content))

    monkeypatch.setattr(
        generator,
        "role_primary_or_feature",
        lambda *args, **kwargs: _cloud_feature(),
    )
    monkeypatch.setattr(generator, "route_chat_completion", fake_route)
    monkeypatch.setattr(generator, "load_content_cache", lambda *args, **kwargs: None)
    monkeypatch.setattr(generator, "save_content_cache", lambda *args, **kwargs: None)

    reels = await generator.generate_product_strategy_reels(
        segments,
        occurrences,
        use_role_routing=True,
        product_groups=product_groups,
    )

    assert calls == 1
    assert [reel.title for reel in reels] == [
        "faithful 标题",
        "balanced 标题",
        "concise 标题",
    ]


@pytest.mark.asyncio
async def test_role_routed_validation_batches_strategies_per_product(monkeypatch, tmp_path):
    from backend.services import highlight_reel_validator as validator

    segments = _segments()
    occurrences = identify_product_occurrences(segments)
    product_groups = group_product_occurrences(occurrences, segments)
    reels = build_highlight_reel_plan(segments, product_groups=product_groups)
    calls = 0

    async def fake_route(_role, messages, *, validate, **_kwargs):
        nonlocal calls
        calls += 1
        payload = json.loads(messages[1]["content"].split("\n\n", 1)[1])
        content = json.dumps(
            {
                "results": [
                    {
                        "reel_id": item["reel_id"],
                        "passed": True,
                        "score": 0.95,
                        "validator_model": "batch-validator",
                        "issues": [],
                        "repair_actions": [],
                    }
                    for item in payload
                ]
            }
        )
        return SimpleNamespace(value=validate(content))

    monkeypatch.setattr(
        validator,
        "role_primary_or_feature",
        lambda *args, **kwargs: _cloud_feature(),
    )
    monkeypatch.setattr(validator, "route_chat_completion", fake_route)

    monkeypatch.setattr(validator, "HIGHLIGHT_VALIDATION_CACHE_DIR", tmp_path)
    report = await validator.run_highlight_reel_validation(
        reels,
        segments,
        product_occurrences=[
            SimpleNamespace(
                occurrence_id=product_groups[0].product_group_id,
                name=product_groups[0].canonical_name,
                aliases=product_groups[0].aliases,
                segment_ids=product_groups[0].segment_ids,
                start=product_groups[0].start,
                end=product_groups[0].end,
            )
        ],
        use_role_routing=True,
    )

    assert calls == 1
    assert report.passed is True
    assert report.degraded is False


@pytest.mark.asyncio
async def test_provider_gate_caps_two_batches_and_ollama_is_single_lane():
    cloud_active = 0
    cloud_maximum = 0

    class FakeCompletions:
        async def create(self, **kwargs):
            nonlocal cloud_active, cloud_maximum
            cloud_active += 1
            cloud_maximum = max(cloud_maximum, cloud_active)
            try:
                await asyncio.sleep(0.01)
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))]
                )
            finally:
                cloud_active -= 1

    class FakeClient:
        def __init__(self):
            self.chat = SimpleNamespace(completions=FakeCompletions())

        async def close(self):
            return None

    first = HighlightModelExecutor(
        provider="openai",
        model="shared-model",
        api_key="key",
        base_url="https://shared.example/v1",
        max_parallel_calls=2,
    )
    second = HighlightModelExecutor(
        provider="openai",
        model="shared-model",
        api_key="key",
        base_url="https://shared.example/v1",
        max_parallel_calls=2,
    )
    first._client = FakeClient()
    second._client = FakeClient()
    try:
        await asyncio.gather(
            *(first.complete("s", "u") for _ in range(4)),
            *(second.complete("s", "u") for _ in range(4)),
        )
    finally:
        await first.aclose()
        await second.aclose()
        await first._http_client.aclose()
        await second._http_client.aclose()
    assert cloud_maximum == 2

    local = HighlightModelExecutor(
        provider="ollama",
        model="local-model",
        api_key="",
        base_url="http://localhost:11434",
        max_parallel_calls=8,
    )
    assert local.max_parallel_calls == 1
    await local.aclose()


@pytest.mark.asyncio
async def test_cancelled_generation_cancels_all_children(monkeypatch):
    from backend.services import highlight_strategy_generator as generator

    segments = _segments()
    occurrences = identify_product_occurrences(segments)
    started = asyncio.Event()
    active = 0

    class BlockingExecutor:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def complete(self, *args, **kwargs):
            nonlocal active
            active += 1
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                active -= 1

    monkeypatch.setattr(generator, "HighlightModelExecutor", BlockingExecutor)
    monkeypatch.setattr(generator, "merge_feature_llm_request", lambda *args, **kwargs: _cloud_feature())
    monkeypatch.setattr(generator, "load_content_cache", lambda *args, **kwargs: None)
    running = asyncio.create_task(
        generator.generate_product_strategy_reels(segments, occurrences, max_parallel_calls=3)
    )
    await started.wait()
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    assert active == 0


@pytest.mark.asyncio
async def test_pipeline_rejects_stale_subtitle_write(monkeypatch):
    from backend.services import highlight_reel_pipeline as pipeline

    task = Task(name="stale", audit_segments=_segments())

    class Store:
        def __init__(self):
            self.task = task

        def get(self, task_id):
            return self.task if task_id == self.task.id else None

        def update(self, updated, **kwargs):
            self.task = updated
            return updated

    entered = asyncio.Event()
    release = asyncio.Event()

    async def delayed_generate(segments, occurrences, *args, **kwargs):
        entered.set()
        await release.wait()
        return build_highlight_reel_plan(segments, product_occurrences=occurrences)

    async def accept_contract(*args, **kwargs):
        return None

    store = Store()
    monkeypatch.setattr(pipeline, "get_store", lambda: store)
    monkeypatch.setattr(pipeline, "generate_product_strategy_reels", delayed_generate)
    monkeypatch.setattr(pipeline, "verify_contract_report", accept_contract)

    running = asyncio.create_task(
        pipeline.refresh_highlight_plan(task.id, run_model_validation=False)
    )
    await entered.wait()
    store.task.subtitle_revision += 1
    release.set()
    with pytest.raises(pipeline.HighlightPipelineError) as exc_info:
        await running
    assert exc_info.value.error_type == "highlight_source_changed"
