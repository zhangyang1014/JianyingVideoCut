import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.models.task import Task, Segment, HighlightReel, ProductGroup, RenderProfile
from backend.routers import export as router
from backend.services.preview_coordinator import join_preview
from backend.services import render_quality_validator


@pytest.fixture
def preview(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"; source.write_bytes(b"source")
    task = Task(name="preview", video_path=str(source), audit_segments=[
        Segment(id="a", start=0, end=2, text="开场"),
        Segment(id="gap", start=2, end=4, text="不入选", action="keep"),
        Segment(id="b", start=4, end=6, text="卖点", action="delete"),
    ])
    calls = []
    async def render(video_path, segments, output, **kwargs):
        calls.append((segments, output, kwargs))
        await asyncio.sleep(0.01)
        Path(output).write_bytes(b"valid")
        return True
    monkeypatch.setattr(router, "get_store", lambda: SimpleNamespace(get=lambda _: task))
    monkeypatch.setattr(router, "EXPORTS_DIR", tmp_path)
    monkeypatch.setattr(router, "export_ffmpeg_lossless", render)
    monkeypatch.setattr(router, "validate_rendered_media", lambda path, _: SimpleNamespace(passed=Path(path).read_bytes() == b"valid"))
    return task, calls, source


@pytest.mark.asyncio
async def test_same_preview_is_coalesced_and_validated_cache_reused(preview):
    task, calls, source = preview
    responses = await asyncio.gather(*(router.export_clip_preview(task.id, {"segment_ids": ["a", "b"]}) for _ in range(8)))
    assert len(calls) == 1
    assert len({r["url"] for r in responses}) == 1
    assert [s.action.value for s in calls[0][0]] == ["keep", "delete", "keep"]
    assert calls[0][2]["render_profile"] == RenderProfile.PREVIEW
    assert (await router.export_clip_preview(task.id, {"segment_ids": ["a", "b"]}))["cached"]
    assert len(calls) == 1
    # 损坏缓存必须重建，不能只因文件存在就返回。
    Path(calls[0][1]).write_bytes(b"broken")
    assert not (await router.export_clip_preview(task.id, {"segment_ids": ["a", "b"]}))["cached"]
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_source_replacement_and_submillisecond_cut_change_invalidate_cache(preview):
    task, calls, source = preview
    first = await router.export_clip_preview(task.id, {"segment_ids": ["a"]})
    source.write_bytes(b"replacement video")
    second = await router.export_clip_preview(task.id, {"segment_ids": ["a"]})
    task.audit_segments[0].end += 0.0001
    third = await router.export_clip_preview(task.id, {"segment_ids": ["a"]})
    assert len({first["url"], second["url"], third["url"]}) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("ids, status", [(["a", "missing"], 409), (["a", "a"], 400), ("a", 400), ([], 400), ([None], 400)])
async def test_invalid_selection_does_not_render_partial_reel(preview, ids, status):
    task, calls, _ = preview
    with pytest.raises(HTTPException) as error:
        await router.export_clip_preview(task.id, {"segment_ids": ids})
    assert error.value.status_code == status and not calls


@pytest.mark.asyncio
async def test_canonical_preview_ignores_client_ids_and_rejects_stale_scope(preview):
    task, calls, _ = preview
    reel = HighlightReel(reel_id="r", clip_group=1, title="集锦", segment_ids=["b"], source_content_revision=0)
    task.highlight_reels = [reel]
    result = await router.export_clip_preview(task.id, {"reel_id": "r", "segment_ids": ["a"]})
    assert [s.id for s in calls[-1][0]] == ["b"] and result["segment_ids"] == ["b"]
    task.content_revision = 1
    with pytest.raises(HTTPException, match="旧字幕"):
        await router.export_clip_preview(task.id, {"reel_id": "r"})
    task.content_revision = 0
    reel.product_group_id = "p"
    task.product_groups = [ProductGroup(product_group_id="p", product_name="产品", canonical_name="产品", segment_ids=["a"])]
    with pytest.raises(HTTPException, match="范围外"):
        await router.export_clip_preview(task.id, {"reel_id": "r"})


@pytest.mark.asyncio
async def test_failed_render_retries_next_request(preview, monkeypatch):
    task, calls, _ = preview
    original = router.export_ffmpeg_lossless
    async def fail(*args, **kwargs):
        return False
    monkeypatch.setattr(router, "export_ffmpeg_lossless", fail)
    with pytest.raises(HTTPException):
        await router.export_clip_preview(task.id, {"segment_ids": ["a"]})
    monkeypatch.setattr(router, "export_ffmpeg_lossless", original)
    assert (await router.export_clip_preview(task.id, {"segment_ids": ["a"]}))["url"]


@pytest.mark.asyncio
async def test_cancelled_waiter_does_not_cancel_shared_preview():
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0
    async def render():
        nonlocal calls
        calls += 1; entered.set(); await release.wait()
        return {"url": "same"}
    first = asyncio.create_task(join_preview("same", render))
    second = asyncio.create_task(join_preview("same", render))
    await entered.wait(); first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    release.set()
    assert await second == {"url": "same"} and calls == 1


@pytest.mark.asyncio
async def test_different_previews_limit_render_concurrency():
    active = peak = 0
    async def render():
        nonlocal active, peak
        active += 1; peak = max(peak, active)
        await asyncio.sleep(0.01); active -= 1
        return {}
    await asyncio.gather(*(join_preview(str(i), render) for i in range(7)))
    assert peak == 2


@pytest.mark.parametrize("duration", ["NaN", "Infinity", "0", "-1"])
def test_nonfinite_or_nonpositive_media_duration_rejected(tmp_path, monkeypatch, duration):
    path = tmp_path / "video.mp4"; path.write_bytes(b"file")
    payload = {"streams": [{"codec_type": "audio"}, {"codec_type": "video"}], "format": {"duration": duration}}
    monkeypatch.setattr(render_quality_validator, "run_text_subprocess", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=json.dumps(payload)))
    assert not render_quality_validator.validate_rendered_media(str(path), 5).passed
