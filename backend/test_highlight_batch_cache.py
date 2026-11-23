import json
from types import SimpleNamespace

import pytest

from backend.models.app_config import ResolvedFeatureLlm
from backend.models.task import HighlightReel, HighlightReelStrategy, Segment
from backend.services import highlight_reel_validator as validator
from backend.services.highlight_model_executor import HighlightModelCallBudget


@pytest.fixture
def batch_setup(tmp_path, monkeypatch):
    feature = ResolvedFeatureLlm(feature="highlight_reel", mode="cloud", provider="openai_compatible",
        model="validator-test", base_url="https://example.test/v1", api_key="test-only")
    monkeypatch.setattr(validator, "role_primary_or_feature", lambda *args: feature)
    monkeypatch.setattr(validator, "resolve_role_candidates", lambda *args: [])
    monkeypatch.setattr(validator, "HIGHLIGHT_VALIDATION_CACHE_DIR", tmp_path)
    calls = []
    async def route(role, messages, *, validate, **kwargs):
        calls.append(messages)
        payload = json.loads(messages[1]["content"].split("\n\n", 1)[1])
        content = {"results": [{"reel_id": reel["reel_id"], "passed": True, "score": .95,
            "validator_model": "validator-test", "issues": [], "repair_actions": []} for reel in payload]}
        return SimpleNamespace(value=validate(json.dumps(content)))
    monkeypatch.setattr(validator, "route_chat_completion", route)
    segments = [Segment(id="one", start=0, end=70, text="这款产品的核心卖点")]
    reel = HighlightReel(reel_id="reel-one", product_group_id="product-one", title="产品卖点", product_name="产品",
        start=0, end=70, duration=70, segment_ids=["one"], clip_group=1, strategy_kind=HighlightReelStrategy.BALANCED)
    return segments, [reel], calls, feature


@pytest.mark.asyncio
async def test_unchanged_batch_reuses_cache_without_consuming_budget(batch_setup):
    segments, reels, calls, _ = batch_setup
    budget = HighlightModelCallBudget(max_calls=1)
    first = await validator.run_highlight_reel_validation(reels, segments, use_role_routing=True, call_budget=budget)
    second = await validator.run_highlight_reel_validation(reels, segments, use_role_routing=True, call_budget=budget)
    assert len(calls) == 1
    assert budget.used_calls == 1
    assert not first.degraded and not second.degraded


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["text", "title", "model", "prompt"])
async def test_changed_input_invalidates_batch_cache(batch_setup, monkeypatch, change):
    segments, reels, calls, feature = batch_setup
    await validator.run_highlight_reel_validation(reels, segments, use_role_routing=True)
    if change == "text": segments[0].subtitle_text = "修改后的产品卖点"
    elif change == "title": reels[0].title = "另一个内容角度"
    elif change == "model": feature.model = "validator-new"
    else: monkeypatch.setattr(validator, "_load_validation_overlay", lambda: "新验收规则")
    await validator.run_highlight_reel_validation(reels, segments, use_role_routing=True)
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_cache_cannot_return_a_different_reel(batch_setup, monkeypatch):
    segments, reels, calls, _ = batch_setup
    monkeypatch.setattr(validator, "load_content_cache", lambda *args: {"results": [
        {"reel_id": "wrong-reel", "passed": True, "score": 1, "issues": [], "repair_actions": []}]})
    report = await validator.run_highlight_reel_validation(reels, segments, use_role_routing=True)
    assert len(calls) == 1
    assert not report.degraded
