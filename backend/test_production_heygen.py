"""HeyGen provider contract and the production asset handoff."""
import asyncio
import json
from pathlib import Path

import httpx
from pydantic import SecretStr

from backend.models.production import BrandProfile, ProductionAsset, ProductionProject, StoryboardShot
from backend.services import production_heygen as heygen
from backend.services.production_store import ProductionStore


def make_job(tmp_path, monkeypatch):
    monkeypatch.delenv("HEYGEN_API_KEY", raising=False)
    store = ProductionStore(tmp_path)
    heygen.save_settings(store, heygen.Settings(api_key=SecretStr("test-key"), avatar_id="look", voice_id="voice"))
    project = ProductionProject(
        project_type="digital_presenter", name="Lina 开场", brand=BrandProfile(),
        selected_direction_id="direction",
        shots=[StoryboardShot(shot_id="S01", purpose="开场", visual="Lina 出镜", spoken_text="孩子想喝杯温水。",
                             source_type="aigc", voice_mode="generated", tool="HeyGen")],
    )
    job = heygen.build_job(project, heygen.load_settings(store))
    project.heygen_jobs.append(job)
    store.create(project)
    return store, project, job


def test_submission_polls_and_imports_local_asset_with_provider_srt(tmp_path, monkeypatch):
    store, project, job = make_job(tmp_path, monkeypatch)
    events = []

    def handler(request):
        events.append((request.method, request.url.path))
        if request.method == "POST":
            assert request.headers["Idempotency-Key"] == job.id
            assert request.headers["X-Api-Key"] == "test-key"
            payload = json.loads(request.content)
            assert payload == {
                "type": "avatar", "avatar_id": "look", "voice_id": "voice", "script": "孩子想喝杯温水。",
                "title": "Lina 开场 · S01", "aspect_ratio": "9:16", "resolution": "1080p",
                "caption": {"file_format": "srt"},
            }
            return httpx.Response(202, json={"data": {"video_id": "v_abc", "status": "waiting"}})
        if request.url.host == "api.heygen.com":
            assert store.get(project.id).heygen_jobs[0].video_id == "v_abc"
            return httpx.Response(200, json={"data": {
                "status": "completed", "video_url": "https://files.heygen.ai/result.mp4",
                "subtitle_url": "https://files.heygen.ai/result.srt",
            }})
        assert "X-Api-Key" not in request.headers
        if request.url.path.endswith(".srt"):
            return httpx.Response(200, content="1\n00:00:00,240 --> 00:00:02,100\n孩子想喝杯温水。\n".encode())
        return httpx.Response(200, content=b"video-fixture")

    real_client = httpx.AsyncClient
    monkeypatch.setattr(heygen.httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))

    def inspect(path, filename, role, shot_id):
        assert path.read_bytes() == b"video-fixture"
        return ProductionAsset(path=str(path), filename=filename, fingerprint="media", kind="video",
                               duration=3.1, has_audio=True, role=role, shot_id=shot_id)

    monkeypatch.setattr(heygen, "inspect_asset", inspect)
    asyncio.run(heygen.run_job(store, project.id, job))

    saved = store.get(project.id)
    assert saved.heygen_jobs[0].status == "completed"
    asset, = saved.assets
    assert asset.origin.provider == "HeyGen"
    assert Path(asset.path).is_file()
    assert asset.cues[0].start == 0.24
    assert saved.shots[0].selection.asset_id == asset.id
    assert saved.shots[0].verified_signature is None
    assert events == [("POST", "/v3/videos"), ("GET", "/v3/videos/v_abc"),
                      ("GET", "/result.mp4"), ("GET", "/result.srt")]
    assert "api_key" not in heygen.load_settings(store).public()


def test_resume_downloads_original_without_overwriting_edited_shot(tmp_path, monkeypatch):
    store, project, job = make_job(tmp_path, monkeypatch)

    def interrupt(p):
        p.heygen_jobs[0].video_id = "v_original"
        p.heygen_jobs[0].status = "processing"
        p.shots[0].revision += 1

    job = store.update(project.id, interrupt).heygen_jobs[0]

    async def status(_client, _key, method, path, **_kwargs):
        assert (method, path) == ("GET", "videos/v_original")
        return {"data": {"status": "completed"}}

    async def import_video(_client, directory, *_args):
        return ProductionAsset(path=str(directory / "saved.mp4"), filename="saved.mp4", fingerprint="media",
                               kind="video", duration=4, has_audio=True)

    monkeypatch.setattr(heygen, "api", status)
    monkeypatch.setattr(heygen, "import_asset", import_video)
    asyncio.run(heygen.run_job(store, project.id, job))

    saved = store.get(project.id)
    assert saved.heygen_jobs[0].status == "completed"
    assert saved.shots[0].selection is None
    assert saved.assets[0].shot_id is None
