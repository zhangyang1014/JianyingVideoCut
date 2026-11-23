"""Selected source frames are bounded, cached and isolated by production project."""
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.models.production import BrandProfile, ProductionAsset, ProductionProject
from backend.routers import productions as api
from backend.services.production_store import ProductionStore


JPEG = b"\xff\xd8test-frame\xff\xd9"


@pytest.fixture
def thumbnail_api(tmp_path, monkeypatch):
    store = ProductionStore(tmp_path / "productions")
    project = store.create(ProductionProject(name="多幕预览", brand=BrandProfile(product="饮水机")))
    source = store.directory(project.id) / "source.mp4"
    source.write_bytes(b"video-source")
    asset = ProductionAsset(filename=source.name, path=str(source), fingerprint="first-content",
                            kind="video", duration=2)
    project = store.update(project.id, lambda p: p.assets.append(asset))
    monkeypatch.setattr(api, "store", store)
    app = FastAPI()
    app.include_router(api.router)
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, store=store, project=project, asset=asset, source=source,
                              url=f"/api/productions/{project.id}/assets/{asset.id}/thumbnail")


@pytest.mark.parametrize("time", ["-0.01", "2", "2.1", "nan", "inf", "-inf", "invalid"])
def test_invalid_or_out_of_range_times_never_start_ffmpeg(thumbnail_api, monkeypatch, time):
    def unexpected(*args, **kwargs):
        pytest.fail("Invalid source time must be rejected before FFmpeg starts")
    monkeypatch.setattr(api.subprocess, "run", unexpected)
    response = thumbnail_api.client.get(thumbnail_api.url, params={"t": time})
    assert response.status_code == 422


def test_project_and_media_scope_are_checked_before_extraction(thumbnail_api, tmp_path, monkeypatch):
    state = thumbnail_api
    def unexpected(*args, **kwargs):
        pytest.fail("Unowned or unsupported sources must not reach FFmpeg")
    monkeypatch.setattr(api.subprocess, "run", unexpected)
    other = state.store.create(ProductionProject(name="其他项目", brand=BrandProfile(product="杯子")))
    missing_project = "0" * 32
    assert state.client.get(state.url.replace(state.project.id, missing_project)).status_code == 404
    assert state.client.get(state.url.replace(state.project.id, other.id)).status_code == 404
    state.store.update(state.project.id, lambda p: setattr(p.assets[0], "kind", "audio"))
    assert state.client.get(state.url).status_code == 422
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"outside")
    def replace_path(p):
        p.assets[0].kind = "video"
        p.assets[0].path = str(outside)
    state.store.update(state.project.id, replace_path)
    assert state.client.get(state.url).status_code == 404


def test_precise_time_and_source_content_change_invalidate_cache(thumbnail_api, monkeypatch):
    state = thumbnail_api
    calls = []
    def extract(command, **kwargs):
        calls.append(command)
        Path(command[-1]).write_bytes(JPEG)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(api.subprocess, "run", extract)
    before = state.store.get(state.project.id).model_dump()
    for _ in range(2):
        response = state.client.get(state.url, params={"t": 0.50001})
        assert response.status_code == 200 and response.content == JPEG
        assert response.headers["content-type"] == "image/jpeg"
    assert len(calls) == 1
    assert calls[0][calls[0].index("-ss") + 1] == "0.50001"
    assert state.store.get(state.project.id).model_dump() == before
    assert state.client.get(state.url, params={"t": 0.50002}).status_code == 200
    state.store.update(state.project.id, lambda p: setattr(p.assets[0], "fingerprint", "replaced-content"))
    assert state.client.get(state.url, params={"t": 0.50001}).status_code == 200
    assert len(calls) == 3
    cache = state.store.directory(state.project.id) / ".thumbnails"
    cached = list(cache.glob("*.jpg"))
    assert len(cached) == 3
    for path in cached:
        path.write_bytes(b"broken")
    assert state.client.get(state.url, params={"t": 0.50001}).status_code == 200
    assert len(calls) == 4


@pytest.mark.parametrize("failure, status", [("timeout", 504), ("tool_missing", 503), ("decode", 422)])
def test_extraction_failures_report_reason_and_remove_partial_files(thumbnail_api, monkeypatch, failure, status):
    state = thumbnail_api
    def fail(command, **kwargs):
        Path(command[-1]).write_bytes(b"partial")
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        if failure == "tool_missing":
            raise FileNotFoundError("ffmpeg")
        return SimpleNamespace(returncode=1)
    monkeypatch.setattr(api.subprocess, "run", fail)
    response = state.client.get(state.url)
    assert response.status_code == status and response.json()["detail"]
    assert list((state.store.directory(state.project.id) / ".thumbnails").iterdir()) == []


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg is required for real source-frame verification")
def test_real_selected_frame_comes_from_selected_time_and_has_small_dimensions(thumbnail_api):
    state = thumbnail_api
    subprocess.run([
        "ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i", "color=red:s=360x640:d=1",
        "-f", "lavfi", "-i", "color=blue:s=360x640:d=1",
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        str(state.source),
    ], capture_output=True, check=True, timeout=15)
    response = state.client.get(state.url, params={"t": 1.5})
    assert response.status_code == 200
    decoded = subprocess.run([
        "ffmpeg", "-nostdin", "-v", "error", "-i", "pipe:0", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
    ], input=response.content, capture_output=True, check=True, timeout=10).stdout
    # 9:16 remains 180x320, and t=1.5 is the blue half rather than the red opening.
    assert len(decoded) == 180 * 320 * 3
    red, green, blue = decoded[len(decoded) // 6 * 3:][:3]
    assert blue > 200 and red < 40 and green < 40
