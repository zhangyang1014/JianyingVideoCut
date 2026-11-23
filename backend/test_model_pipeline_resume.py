from pathlib import Path

import pytest

from backend.models.task import (
    HighlightReel,
    HighlightReelStrategy,
    ProductEntity,
    ProductOccurrence,
    ProductSemanticMark,
    Segment,
    SegmentAction,
)
from backend.services import highlight_reel_validator, highlight_strategy_generator


def _highlight_segments() -> list[Segment]:
    return [
        Segment(
            id=f"seg_{index}",
            start=index * 10,
            end=(index + 1) * 10,
            text=f"产品卖点 {index}",
            action=SegmentAction.KEEP,
            product_semantic=ProductSemanticMark(
                product_entities=[ProductEntity(name="产品A", canonical_name="产品A")],
                roles=["product_opening" if index == 0 else "selling_point"],
                selling_points=[f"卖点 {index}"],
                eligible_for_highlight=True,
            ),
        )
        for index in range(4)
    ]


@pytest.mark.asyncio
async def test_highlight_strategy_cache_invalidates_with_occurrence_input(tmp_path, monkeypatch):
    segments = _highlight_segments()
    occurrence = ProductOccurrence(
        occurrence_id="occ-a",
        name="产品A",
        start=0,
        end=40,
        segment_ids=[segment.id for segment in segments],
    )

    class FakeExecutor:
        calls = 0

        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def complete(self, system_prompt, user_prompt, **kwargs):
            type(self).calls += 1
            return '{"segment_ids":["seg_0"],"title":"产品A","rationale":"测试"}'

    monkeypatch.setattr(
        highlight_strategy_generator,
        "HIGHLIGHT_STRATEGY_CACHE_DIR",
        Path(tmp_path),
    )
    monkeypatch.setattr(
        highlight_strategy_generator,
        "HighlightModelExecutor",
        FakeExecutor,
    )

    kwargs = {
        "provider": "openai",
        "api_key": "sk-test",
        "model": "test-model",
    }
    await highlight_strategy_generator.generate_product_strategy_reels(
        segments, [occurrence], **kwargs
    )
    await highlight_strategy_generator.generate_product_strategy_reels(
        segments, [occurrence], **{**kwargs, "api_key": "different-secret"}
    )
    assert FakeExecutor.calls == 3

    changed = [segment.model_copy(deep=True) for segment in segments]
    changed[0].text = "变化后的产品卖点"
    await highlight_strategy_generator.generate_product_strategy_reels(
        changed, [occurrence], **kwargs
    )
    assert FakeExecutor.calls == 6


@pytest.mark.asyncio
async def test_validator_rechecks_only_changed_reel(tmp_path, monkeypatch):
    segments = _highlight_segments()
    reels = [
        HighlightReel(
            reel_id="reel-faithful",
            clip_group=1,
            title="忠实版",
            segment_ids=["seg_0", "seg_1"],
            occurrence_id="occ-a",
            strategy_kind=HighlightReelStrategy.FAITHFUL,
        ),
        HighlightReel(
            reel_id="reel-concise",
            clip_group=2,
            title="精简版",
            segment_ids=["seg_2", "seg_3"],
            occurrence_id="occ-a",
            strategy_kind=HighlightReelStrategy.CONCISE,
        ),
    ]

    class FakeExecutor:
        calls = 0

        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def complete(self, *args, **kwargs):
            type(self).calls += 1
            return '{"passed":true,"score":1,"validator_model":"test","issues":[],"repair_actions":[]}'

    monkeypatch.setattr(
        highlight_reel_validator,
        "HIGHLIGHT_VALIDATION_CACHE_DIR",
        Path(tmp_path),
    )
    monkeypatch.setattr(
        highlight_reel_validator,
        "HighlightModelExecutor",
        FakeExecutor,
    )

    kwargs = {
        "provider": "openai",
        "api_key": "sk-test",
        "model": "test-model",
    }
    await highlight_reel_validator.run_highlight_reel_validation(reels, segments, **kwargs)
    await highlight_reel_validator.run_highlight_reel_validation(
        reels, segments, **{**kwargs, "api_key": "different-secret"}
    )
    assert FakeExecutor.calls == 2

    changed_reels = [reel.model_copy(deep=True) for reel in reels]
    changed_reels[1].title = "变化后的精简版"
    await highlight_reel_validator.run_highlight_reel_validation(
        changed_reels, segments, **kwargs
    )
    assert FakeExecutor.calls == 3
