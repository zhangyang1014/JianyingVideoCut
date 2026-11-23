"""Contracts for project persistence, source bounds, versioning and real media delivery."""
import asyncio
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from backend.models.production import (AssetSelection, BrandFact, BrandProfile, Cue, HeyGenJob, MediaOrigin,
                                       ProductionAsset, ProductionProject, ProductionRenderJob, StoryboardShot)
from backend.routers import productions as api
from backend.services.llm_router import LlmRouteAttempt, LlmRoutingError
from backend.services.creator_styles import load_creator_style
from backend.services.production_media import inspect_asset
from backend.services.production_planner import (basic_storyboard_from_narration, normalize_narration,
                                                plan_with_ai, template_directions)
from backend.services.production_renderer import render_production
from backend.services.production_store import ProductionConflict, ProductionStore
from backend.services.production_timeline import compile_timeline, shot_issues, signature


def fixture():
    asset = ProductionAsset(filename="S01.mp4", path="/tmp/source.mp4", fingerprint="test", kind="video", duration=9,
                            has_audio=True, cues=[Cue(start=1, end=3, text="真实音频字幕")])
    shot = StoryboardShot(shot_id="S01", purpose="说明", visual="产品", spoken_text="规划文案",
                         selection=AssetSelection(asset_id=asset.id, start=.5, end=4))
    shot.verified_signature = signature(shot)
    return ProductionProject(name="测试制作", brand=BrandProfile(name="测试品牌", product="测试产品"), assets=[asset], shots=[shot])


class ProductionContractTests(unittest.TestCase):
    def test_timeline_uses_real_source_audio_not_planning_text_or_duration(self):
        p = fixture()
        t = compile_timeline(p)
        self.assertEqual(t.duration, 3.5)
        self.assertEqual(t.subtitles[0].text, "真实音频字幕")
        self.assertEqual((t.subtitles[0].start, t.subtitles[0].end), (.5, 2.5))
        self.assertEqual(t.clips[0].source_start, .5)

    def test_stale_shot_and_out_of_bounds_are_blocked(self):
        p = fixture()
        p.shots[0].spoken_text = "改了台词"
        self.assertTrue(any("变更" in issue for issue in shot_issues(p, p.shots[0])))
        p.shots[0].selection.end = 12
        with self.assertRaisesRegex(ValueError, "超出"):
            compile_timeline(p)

    def test_voiceover_suppresses_source_and_uses_audio_duration(self):
        p = fixture()
        voice = ProductionAsset(filename="voice.wav", path="/tmp/voice.wav", fingerprint="voice", kind="audio", duration=3,
                                has_audio=True, cues=[Cue(start=0, end=2, text="独立旁白")])
        p.assets.append(voice)
        shot = p.shots[0]
        shot.voice_mode = "voiceover"
        shot.voiceover = AssetSelection(asset_id=voice.id, start=0, end=2)
        shot.verified_signature = signature(shot)
        t = compile_timeline(p)
        self.assertEqual(t.duration, 2)
        self.assertEqual(t.clips[0].audio_asset_id, voice.id)
        self.assertEqual(t.subtitles[0].text, "独立旁白")
        shot.voiceover.end = 5
        with self.assertRaisesRegex(ValueError, "旁白"):
            compile_timeline(p)

    def test_missing_audio_cues_not_faked_by_script(self):
        p = fixture()
        p.assets[0].cues = []
        with self.assertRaisesRegex(ValueError, "实际音频字幕"):
            compile_timeline(p)

    def test_full_production_does_not_silently_complete_below_requested_length_range(self):
        p = fixture()
        p.selected_direction_id = "demo"
        with self.assertRaisesRegex(ValueError, "30–60"):
            compile_timeline(p)

    def test_digital_timeline_uses_media_length_without_selling_video_limits(self):
        project = fixture()
        project.project_type = "digital_presenter"
        project.target_duration = None
        project.selected_direction_id = "demo"
        shot = project.shots[0]
        shot.selection.start = 0
        for duration in (12, 120, 205):
            with self.subTest(duration=duration):
                project.assets[0].duration = duration
                shot.selection.end = duration
                shot.verified_signature = signature(shot)
                self.assertEqual(compile_timeline(project).duration, duration)

    def test_invalid_times_rejected(self):
        for start, end in [(2, 1), (-1, 2), (0, float("nan")), (0, float("inf"))]:
            with self.assertRaises(ValidationError):
                AssetSelection(asset_id="a", start=start, end=end)

    def test_fractional_clip_boundaries_are_frame_aligned_and_do_not_exceed_sources(self):
        p = fixture()
        p.shots[0].selection.end = 4.019
        p.shots[0].verified_signature = signature(p.shots[0])
        t = compile_timeline(p)
        self.assertEqual(t.duration, 3.5)
        self.assertLessEqual(t.clips[0].source_end, p.shots[0].selection.end)

    def test_long_pauses_shift_audio_video_and_subtitles_together(self):
        p = fixture()
        p.assets[0].cues = [Cue(start=.6, end=1.5, text="前句"), Cue(start=3, end=3.8, text="后句")]
        timeline = compile_timeline(p)
        self.assertEqual(len(timeline.clips), 2)
        self.assertLess(timeline.duration, 3.5)
        self.assertAlmostEqual(timeline.clips[1].source_start, 2.85)
        self.assertAlmostEqual(timeline.clips[1].audio_source_start, 2.85)
        self.assertLess(timeline.subtitles[1].start, 2.5)
        p.shots[0].trim_pauses = False
        p.shots[0].verified_signature = signature(p.shots[0])
        self.assertEqual(compile_timeline(p).duration, 3.5)

    def test_verification_signature_survives_json_round_trip(self):
        p = fixture()
        shot = p.shots[0]
        shot.target_duration = 5
        shot.selection.start = 0
        shot.verified_signature = signature(shot)
        reopened = ProductionProject.model_validate_json(p.model_dump_json())
        self.assertEqual(reopened.shots[0].verified_signature, signature(reopened.shots[0]))

    def test_new_model_roles_preserve_existing_custom_routes(self):
        from backend.models.app_config import AppConfig
        from backend.services.app_config_store import _migrate_config_payload
        payload = AppConfig().model_dump()
        payload["llm_role_routes"].pop("production.plan")
        payload["llm_role_routes"].pop("production.visual")
        payload["llm_role_routes"]["highlight.strategy"]["candidates"][0]["model"] = "my-custom-model"
        migrated, changed = _migrate_config_payload(payload)
        self.assertTrue(changed)
        self.assertIn("production.plan", migrated["llm_role_routes"])
        self.assertEqual(migrated["llm_role_routes"]["highlight.strategy"]["candidates"][0]["model"], "my-custom-model")

    def test_store_cas_rollback_restart_and_history(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ProductionStore(Path(temp))
            p = store.create(fixture())
            timeline = compile_timeline(p)
            job = ProductionRenderJob(fingerprint="x", content_revision=p.content_revision,
                                      snapshot=p.model_dump(), timeline=timeline, status="running")
            p = store.update(p.id, lambda x: x.renders.append(job), p.revision, content=False)
            with self.assertRaises(ProductionConflict):
                store.update(p.id, lambda x: setattr(x, "name", "覆盖"), 1)
            def broken(x):
                x.name = "损坏更新"
                raise ValueError("failed")
            with self.assertRaises(ValueError):
                store.update(p.id, broken)
            self.assertEqual(store.get(p.id).name, "测试制作")
            reopened = ProductionStore(Path(temp))
            reopened.recover()
            self.assertEqual(reopened.get(p.id).renders[0].status, "failed")
            current = reopened.update(p.id, lambda x: setattr(x, "offer", "本次活动"))
            self.assertEqual(current.content_revision, 2)
            self.assertTrue((Path(temp) / p.id / "history/1.json").exists())

    def test_store_omits_compatibility_defaults_from_project_and_history(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ProductionStore(Path(temp))
            source = fixture()
            source.directions = template_directions(source)
            self.assertTrue(source.directions)
            project = store.create(source)
            project_dir = store.directory(project.id)
            active_path = project_dir / "project.json"
            created = json.loads(active_path.read_text(encoding="utf-8"))
            store.update(project.id, lambda p: setattr(p, "offer", "本次活动"), project.revision)
            updated = json.loads(active_path.read_text(encoding="utf-8"))
            archived = json.loads((project_dir / "history/1.json").read_text(encoding="utf-8"))

            for label, payload in [("create", created), ("update", updated), ("history", archived)]:
                with self.subTest(label=label):
                    self.assertNotIn("heygen_jobs", payload)
                    self.assertNotIn("origin", payload["assets"][0])
                    for direction in payload["directions"]:
                        self.assertNotIn("style_snapshot", direction)
                    reopened = ProductionProject.model_validate(payload)
                    self.assertEqual(reopened.heygen_jobs, [])
                    self.assertEqual(reopened.assets[0].origin.kind, "unknown")
            self.assertEqual(updated["offer"], "本次活动")
            self.assertEqual(archived, created)

    def test_store_preserves_populated_heygen_jobs_and_known_asset_origins(self):
        project = fixture()
        project.assets[0].origin = MediaOrigin(kind="aigc", provider="HeyGen")
        job = HeyGenJob(
            shot_id=project.shots[0].shot_id, shot_revision=project.shots[0].revision,
            request={"script": project.shots[0].spoken_text}, key_fingerprint="test-fingerprint",
        )
        project.heygen_jobs.append(job)
        project.directions = template_directions(project)
        project.directions[0].style_snapshot = load_creator_style("lina")

        with tempfile.TemporaryDirectory() as temp:
            store = ProductionStore(Path(temp))
            project = store.create(project)
            project_dir = store.directory(project.id)
            updated = store.update(project.id, lambda p: setattr(p, "offer", "本次活动"), project.revision)
            self.assertEqual(store.get(project.id), updated)
            for path in [project_dir / "project.json", project_dir / "history/1.json"]:
                with self.subTest(path=path.name):
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    self.assertEqual(payload["heygen_jobs"], [job.model_dump()])
                    self.assertEqual(payload["assets"][0]["origin"], {"kind": "aigc", "provider": "HeyGen"})
                    self.assertEqual(payload["directions"][0]["style_snapshot"],
                                     project.directions[0].style_snapshot.model_dump())


class ProductionApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = ProductionStore(Path(self.temp.name))
        self.patcher = patch.object(api, "store", self.store)
        self.patcher.start()
        app = FastAPI()
        app.include_router(api.router)
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.patcher.stop()
        self.temp.cleanup()

    def test_media_urls_and_streaming_survive_project_root_symlink(self):
        root = Path(self.temp.name)
        storage = root / "external-project"
        storage.mkdir()
        legacy = root / "old-project"
        legacy.symlink_to(storage, target_is_directory=True)
        for store_root, media_root in ((storage, legacy), (legacy, storage)):
            with self.subTest(store_root=store_root.name):
                store = ProductionStore(store_root / "productions")
                project = fixture()
                source = media_root / "productions" / project.id / "source.mp4"
                project.assets[0].path = str(source)
                store.create(project)
                content = b"migrated-video" * 100
                source.write_bytes(content)
                with patch.object(api, "store", store):
                    prefix = f"/api/productions/{project.id}"
                    response = self.client.get(prefix)
                    self.assertEqual(response.status_code, 200)
                    media_url = response.json()["assets"][0]["url"]
                    self.assertEqual(media_url, prefix + "/files/source.mp4")
                    video = self.client.get(media_url, headers={"Range": "bytes=0-15"})
                    self.assertEqual(video.status_code, 206)
                    self.assertEqual(video.content, content[:16])

    def test_create_import_plan_choose_and_reopen(self):
        response = self.client.post("/api/productions", json={"name": "品牌项目", "product": "保温杯", "selling_points": "容量 350ml", "offer": "本次活动"})
        self.assertEqual(response.status_code, 200)
        p = response.json()
        prefix = f"/api/productions/{p['id']}"
        response = self.client.post(prefix + "/brief/files", data={"expected_revision": p["revision"]}, files={"file": ("brand.txt", "重量 200g".encode(), "text/plain")})
        self.assertEqual(response.status_code, 200)
        p = response.json()
        self.assertEqual(p["brand"]["facts"][-1]["text"], "重量 200g")
        p = self.client.post(prefix + "/plan", json={"expected_revision": p["revision"], "mode": "template"}).json()
        self.assertEqual(len(p["directions"]), 3)
        p = self.client.post(prefix + "/direction", json={"expected_revision": p["revision"], "direction_id": "demo"}).json()
        self.assertEqual(len(p["shots"]), 6)
        self.assertTrue(all(p["readiness"].values()))
        self.assertEqual(self.client.get(prefix).json()["shots"], p["shots"])
        self.assertEqual(self.client.post(prefix + "/renders", json={"expected_revision": p["revision"]}).status_code, 422)
        self.assertEqual(self.client.get(prefix + "/files/project.json").status_code, 404)
        self.assertEqual(self.client.get(prefix + "/files/%2e%2e%2fproject.json").status_code, 404)
        reused = self.client.post("/api/productions", json={"name": "复用", "product": "保温杯", "reuse_brand_project_id": p["id"]}).json()
        self.assertEqual(reused["offer"], "")
        self.assertIn("重量 200g", [f["text"] for f in reused["brand"]["facts"]])

    def test_digital_presenter_project_creates_a_routed_spoken_video_plan(self):
        response = self.client.post("/api/productions", json={
            "name": "饮水机口播", "product": "饮水机", "project_type": "digital_presenter", "presenter_id": "lina",
            "target_duration": 35, "selling_points": "可旋转出水口",
            "workflow_notes": "开头生成有声口播，正文用同一声线的旁白。",
        })
        self.assertEqual(response.status_code, 200)
        project = response.json()
        self.assertEqual(project["presenter_id"], "lina")
        prefix = f"/api/productions/{project['id']}"
        self.assertEqual(self.client.get("/api/productions").json()[0]["project_type"], "digital_presenter")
        plan = self.client.post(prefix + "/plan", json={"expected_revision": project["revision"], "mode": "template"})
        self.assertEqual(plan.status_code, 200)
        chosen = self.client.post(prefix + "/direction", json={"expected_revision": plan.json()["revision"], "direction_id": "demo"})
        self.assertEqual(chosen.status_code, 200)
        opening, *other_shots = chosen.json()["shots"]
        self.assertEqual((opening["tool"], opening["voice_mode"], opening["target_duration"]), ("HeyGen", "generated", 5))
        self.assertIn("各位妈妈", opening["spoken_text"])
        self.assertIn("Lina", opening["visual"])
        self.assertIn("同步口型", opening["prompt"])
        self.assertEqual(len(other_shots), 5)
        self.assertTrue(all(s["voice_mode"] == "voiceover" for s in other_shots))
        self.assertTrue(all("与开场相同" in s["prompt"] for s in other_shots))
        self.assertEqual(sum(s["target_duration"] for s in [opening, *other_shots]), 35)
        self.assertEqual(ProductionStore(Path(self.temp.name)).get(project["id"]).workflow_notes, project["workflow_notes"])

    def test_presenter_catalog_and_project_selection_are_consistent(self):
        response = self.client.get("/api/productions/presenters")
        self.assertEqual(response.status_code, 200)
        presenter, = response.json()
        self.assertEqual((presenter["id"], presenter["name"]), ("lina", "Lina"))
        self.assertIn("小学到初中", presenter["primary_audience"])
        self.assertIn("幼儿园", presenter["secondary_audience"])
        self.assertIn("高中", presenter["secondary_audience"])
        self.assertEqual(presenter["address"], ["各位妈妈", "你家孩子", "你家宝宝"])
        base = {"name": "主播选择", "product": "绘本"}
        for fields in ({"presenter_id": "lina"}, {"project_type": "digital_presenter", "presenter_id": "unknown"}):
            self.assertEqual(self.client.post("/api/productions", json={**base, **fields}).status_code, 422)
        created = self.client.post("/api/productions", json={**base, "project_type": "digital_presenter", "presenter_id": "lina"})
        self.assertEqual(created.status_code, 200)
        saved = created.json()
        path = self.store.directory(saved["id"]) / "project.json"
        persisted = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(persisted.pop("presenter_id"), "lina")
        path.write_text(json.dumps(persisted), encoding="utf-8")
        self.assertEqual(self.client.get(f"/api/productions/{saved['id']}").json()["presenter_id"], "lina")
        self.assertEqual(self.client.get("/api/productions").json()[0]["presenter_id"], "lina")

    def test_workflow_notes_preserve_verified_media_and_old_project_defaults(self):
        project = fixture()
        project.assets[0].path = str(self.store.directory(project.id) / "source.mp4")
        project.stage = "ready"
        self.store.create(project)
        path = self.store.directory(project.id) / "project.json"
        legacy = project.model_dump(exclude={"project_type", "workflow_notes"})
        path.write_text(json.dumps(legacy), encoding="utf-8")
        prefix = f"/api/productions/{project.id}"
        previous = self.client.get(prefix).json()
        self.assertEqual((previous["project_type"], previous["workflow_notes"]), ("selling_video", ""))
        response = self.client.patch(prefix + "/brief", json={
            "expected_revision": previous["revision"], "selling_points": previous["selling_points"],
            "offer": previous["offer"], "style": previous["brand"]["style"], "workflow_notes": "记录复盘。",
        })
        self.assertEqual(response.status_code, 200)
        current = response.json()
        self.assertEqual(current["workflow_notes"], "记录复盘。")
        self.assertEqual(current["content_revision"], previous["content_revision"])
        self.assertEqual(current["stage"], "ready")
        self.assertEqual(current["shots"][0]["verified_signature"], previous["shots"][0]["verified_signature"])

    def test_digital_duration_is_optional_and_editable_without_invalidating_media(self):
        base = {"name": "测试时长", "product": "保温杯"}
        response = self.client.post("/api/productions", json=base)
        self.assertEqual(response.json()["target_duration"], 45)
        for duration in (None, 120):
            response = self.client.post("/api/productions", json={**base, "target_duration": duration})
            self.assertEqual(response.status_code, 422)
        digital = {**base, "project_type": "digital_presenter", "presenter_id": "lina"}
        response = self.client.post("/api/productions", json=digital)
        empty = response.json()
        self.assertIsNone(empty["target_duration"])
        plan = self.client.post(f"/api/productions/{empty['id']}/plan", json={"expected_revision": empty["revision"], "mode": "template"})
        self.assertEqual(plan.status_code, 200)
        self.assertEqual(sum(s["target_duration"] for s in plan.json()["directions"][0]["shots"]), 90)
        for duration in (120, 480):
            response = self.client.post("/api/productions", json={**digital, "target_duration": duration})
            self.assertEqual(response.json()["target_duration"], duration)
            plan = self.client.post(f"/api/productions/{response.json()['id']}/plan", json={"expected_revision": 1, "mode": "template"})
            self.assertEqual(plan.status_code, 200)
        response = self.client.post("/api/productions", json={**digital, "target_duration": 0})
        self.assertEqual(response.status_code, 422)

        project = fixture()
        project.project_type, project.target_duration, project.stage = "digital_presenter", 120, "ready"
        project.assets[0].path = str(self.store.directory(project.id) / "source.mp4")
        self.store.create(project)
        prefix = f"/api/productions/{project.id}"
        response = self.client.patch(prefix + "/brief", json={
            "expected_revision": project.revision, "selling_points": project.selling_points,
            "offer": project.offer, "style": project.brand.style, "target_duration": None,
        })
        self.assertEqual(response.status_code, 200)
        current = response.json()
        self.assertIsNone(current["target_duration"])
        self.assertEqual(current["content_revision"], project.content_revision)
        self.assertEqual(current["shots"][0]["verified_signature"], project.shots[0].verified_signature)
        self.assertEqual(current["stage"], "ready")

    def test_asset_origin_defaults_to_unknown_and_does_not_change_the_edit(self):
        p = fixture()
        asset = p.assets[0]
        asset.path = str(self.store.directory(p.id) / "source.mp4")
        p.shots[0].source_type = "aigc"
        p.shots[0].tool = "HeyGen"
        p.shots[0].verified_signature = signature(p.shots[0])
        self.store.create(p)
        legacy = p.model_dump()
        legacy["assets"][0].pop("origin")
        (self.store.directory(p.id) / "project.json").write_text(json.dumps(legacy), encoding="utf-8")
        prefix = f"/api/productions/{p.id}"
        before = self.client.get(prefix).json()
        self.assertEqual(before["assets"][0]["origin"]["kind"], "unknown")
        url = prefix + f"/assets/{asset.id}/origin"
        body = {"expected_revision": p.revision, "origin": {"kind": "aigc", "provider": " "}}
        self.assertEqual(self.client.patch(url, json=body).status_code, 422)

        body["origin"]["provider"] = " TapNow "
        response = self.client.patch(url, json=body)
        self.assertEqual(response.status_code, 200)
        after = response.json()
        self.assertEqual(after["assets"][0]["origin"], {"kind": "aigc", "provider": "TapNow"})
        self.assertEqual(after["content_revision"], before["content_revision"])
        self.assertEqual(after["shots"], before["shots"])
        self.assertEqual(self.client.patch(url, json=body).status_code, 409)
        self.assertEqual(self.client.get(prefix).json()["assets"], after["assets"])

    def test_asset_upload_can_record_the_actual_provider(self):
        p = fixture()
        p.assets[0].path = str(self.store.directory(p.id) / "source.mp4")
        p = self.store.create(p)
        url = f"/api/productions/{p.id}/assets"
        values = {"expected_revision": p.revision, "analyze": "false", "origin_kind": "aigc"}
        file = {"file": ("generated.mp4", b"synthetic media", "video/mp4")}
        self.assertEqual(self.client.post(url, data=values, files=file).status_code, 422)

        def inspect(path, filename, role, shot_id):
            return ProductionAsset(path=str(path), filename=filename, fingerprint="synthetic",
                                   kind="video", role=role, shot_id=shot_id, duration=5)

        with patch.object(api, "inspect_asset", inspect):
            response = self.client.post(url, data={**values, "origin_provider": "Kling"}, files=file)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["assets"][-1]["origin"], {"kind": "aigc", "provider": "Kling"})

    def test_selection_multiple_versions_rebinding_and_subtitles(self):
        p = fixture()
        p.assets[0].path = str(self.store.directory(p.id) / "source.mp4")
        p = self.store.create(p)
        asset_id = p.assets[0].id
        prefix = f"/api/productions/{p.id}"
        response = self.client.post(prefix + f"/assets/{asset_id}/subtitles", json={"expected_revision": p.revision, "srt": "1\n00:00:01,000 --> 00:00:03,000\n新字幕"})
        self.assertEqual(response.status_code, 200)
        p = response.json()
        self.assertIsNone(p["shots"][0]["verified_signature"])
        response = self.client.post(prefix + "/shots/S01/select", json={"expected_revision": p["revision"], "selection": {"asset_id": asset_id, "start": 0, "end": 100}})
        self.assertEqual(response.status_code, 422)
        response = self.client.patch(prefix + f"/assets/{asset_id}/binding", json={"expected_revision": p["revision"], "shot_id": "S99"})
        self.assertEqual(response.status_code, 422)


class ProductionAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_narration_storyboard_uses_basic_preview_for_connection_failures(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ProductionStore(Path(temp))
            project = ProductionProject(name="数字人口播", project_type="digital_presenter",
                                        brand=BrandProfile(product="温水机"))
            project.shots = [StoryboardShot(shot_id="S01", purpose="开场", visual="原开场", spoken_text="旧稿。"),
                             StoryboardShot(shot_id="S02", purpose="水杯演示", visual="已确认的水杯近景",
                                            spoken_text="现在，接一杯温水。", voice_mode="voiceover")]
            project = store.create(project)
            narration = "新的开场？\n现在，接一杯温水。"

            with patch.object(api, "store", store):
                response = api.save_narration(project.id, api.NarrationUpdate(expected_revision=project.revision, text=narration))
                revision = response["revision"]
                for error_types, expected in ((["network"], "基础分镜"), (["timeout"], "模型请求超时"),
                                              (["network", "authentication"], "认证失败"), (["contract_error"], "分镜生成失败"),
                                              ([], "没有可用的模型服务")):
                    with self.subTest(error_types=error_types):
                        attempts = [LlmRouteAttempt("production.plan", "test", "test", "primary", 10,
                                                    "failed", error_type=error_type) for error_type in error_types]
                        with patch.object(api, "storyboard_from_narration", side_effect=LlmRoutingError("production.plan", attempts)):
                            await api.generate_narration_storyboard(project.id, api.Revision(expected_revision=revision))
                            await asyncio.wait_for(api.workers[f"plan:{project.id}"], timeout=5)
                        saved = store.get(project.id)
                        revision = saved.revision
                        self.assertEqual(saved.shots, project.shots)
                        self.assertEqual(saved.content_revision, project.content_revision)
                        self.assertEqual(saved.narration_draft.text, narration)
                        self.assertIn(expected, saved.planning_note)
                        preview = saved.narration_draft.shots
                        if error_types in (["network"], ["timeout"]):
                            self.assertEqual(saved.planning_status, "done")
                            self.assertIn("基础分镜", saved.planning_note)
                            self.assertEqual("\n".join(shot.spoken_text for shot in preview), narration)
                            self.assertEqual((preview[0].tool, preview[0].voice_mode), ("HeyGen", "generated"))
                            self.assertEqual(preview[1].voice_mode, "voiceover")
                            self.assertEqual(preview[1].visual, project.shots[1].visual)
                            self.assertTrue(all(shot.selection is None and shot.voiceover is None for shot in preview))
                        else:
                            self.assertEqual(saved.planning_status, "failed")
                            self.assertFalse(preview)

    def test_basic_narration_storyboard_keeps_text_through_sentence_grouping(self):
        project = ProductionProject(name="数字人口播", project_type="digital_presenter", brand=BrandProfile())
        narration = "他问：‘准备好了吗？’" + "".join(f"这是第 {i} 句。" for i in range(27))
        shots = basic_storyboard_from_narration(project, narration)
        self.assertEqual(len(shots), 20)
        self.assertEqual(shots[0].spoken_text, "他问：‘准备好了吗？’")
        self.assertEqual(normalize_narration("".join(shot.spoken_text for shot in shots)), normalize_narration(narration))

    async def test_save_generate_and_apply_narration_through_the_workbench_api(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ProductionStore(Path(temp))
            project = fixture()
            project.project_type = "digital_presenter"
            asset = project.assets[0]
            asset.shot_id = project.shots[0].shot_id
            source = store.directory(project.id) / "source.mp4"
            source.parent.mkdir()
            source.write_bytes(b"the existing footage")
            asset.path = str(source)
            render = ProductionRenderJob(content_revision=project.content_revision, fingerprint="prior-render",
                                         snapshot=project.model_dump(), timeline=compile_timeline(project))
            project.renders.append(render)
            project = store.create(project)
            prefix = f"/api/productions/{project.id}"
            key = f"plan:{project.id}"
            narration = "现在，接一杯温水。\n把杯子放回桌边。"

            async def planner(snapshot, text):
                self.assertEqual(text, narration)
                self.assertEqual(snapshot.narration_draft.text, narration)
                return [StoryboardShot(shot_id=f"S{i:02}", purpose="演示", visual="镜头", spoken_text=section)
                        for i, section in enumerate(narration.splitlines(), 1)]

            app = FastAPI()
            app.include_router(api.router)
            with patch.object(api, "store", store), patch.object(api, "storyboard_from_narration", planner):
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                    current = (await client.get(prefix)).json()
                    self.assertIsNone(current["narration_draft"])
                    response = await client.put(prefix + "/narration", json={"expected_revision": project.revision,
                                                                            "text": "规划　文案\r\n"})
                    self.assertEqual(response.json(), current)
                    response = await client.put(prefix + "/narration", json={"expected_revision": project.revision,
                                                                            "text": narration})
                    self.assertEqual(response.status_code, 200)
                    saved = response.json()
                    self.assertEqual(saved["narration_draft"], {"text": narration, "shots": []})
                    self.assertEqual(saved["shots"], current["shots"])
                    self.assertEqual(saved["assets"], current["assets"])
                    self.assertEqual(saved["content_revision"], current["content_revision"])
                    response = await client.put(prefix + "/narration", json={"expected_revision": project.revision,
                                                                            "text": "较早的编辑器"})
                    self.assertEqual(response.status_code, 409)
                    for path in ("/plan", "/renders", "/shots/S01/heygen"):
                        with self.subTest(path=path):
                            response = await client.post(prefix + path, json={"expected_revision": saved["revision"]})
                            self.assertEqual(response.status_code, 422)
                    response = await client.post(prefix + "/narration/storyboard",
                                                 json={"expected_revision": saved["revision"]})
                    self.assertEqual(response.json()["planning_status"], "running")
                    if worker := api.workers.get(key):
                        await asyncio.wait_for(worker, timeout=5)
                    preview = (await client.get(prefix)).json()
                    self.assertEqual(preview["planning_status"], "done")
                    self.assertEqual(preview["shots"], current["shots"])
                    self.assertEqual(preview["assets"], current["assets"])
                    self.assertEqual(len(preview["narration_draft"]["shots"]), 2)
                    response = await client.post(prefix + "/narration/storyboard/apply",
                                                 json={"expected_revision": preview["revision"]})
                    self.assertEqual(response.status_code, 200)
                    applied = response.json()
                    self.assertIsNone(applied["narration_draft"])
                    self.assertEqual("\n".join(s["spoken_text"] for s in applied["shots"]), narration)
                    self.assertEqual([s["shot_id"] for s in applied["shots"]], ["S02", "S03"])
                    self.assertTrue(all(not s["selection"] and not s["voiceover"] for s in applied["shots"]))
                    self.assertEqual(applied["assets"][0]["id"], asset.id)
                    self.assertIsNone(applied["assets"][0]["shot_id"])
                    self.assertFalse(applied["renders"][0]["is_current"])
                    self.assertEqual(store.get(project.id).renders[0], render)
                    self.assertEqual((await client.get(applied["assets"][0]["url"])).content, b"the existing footage")
                    project_file = store.directory(project.id) / "project.json"
                    self.assertNotIn("narration_draft", json.loads(project_file.read_text(encoding="utf-8")))

    async def test_storyboard_candidate_does_not_survive_new_narration_or_duration(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ProductionStore(Path(temp))
            project = ProductionProject(name="数字人口播", project_type="digital_presenter", brand=BrandProfile())
            project.shots = [StoryboardShot(shot_id="S01", purpose="开场", visual="出镜", spoken_text="旧稿。")]
            project = store.create(project)
            started = asyncio.Event()
            release = asyncio.Event()

            async def planner(snapshot, text):
                started.set()
                await release.wait()
                return [StoryboardShot(shot_id="S01", purpose="开场", visual="出镜", spoken_text=text)]

            with patch.object(api, "store", store), patch.object(api, "storyboard_from_narration", planner):
                draft = api.save_narration(project.id, api.NarrationUpdate(expected_revision=project.revision, text="第一稿。"))
                await api.generate_narration_storyboard(project.id, api.Revision(expected_revision=draft["revision"]))
                worker = api.workers[f"plan:{project.id}"]
                try:
                    await asyncio.wait_for(started.wait(), timeout=5)
                    active = store.get(project.id)
                    api.save_narration(project.id, api.NarrationUpdate(expected_revision=active.revision, text="第二稿。"))
                finally:
                    release.set()
                    await asyncio.wait_for(worker, timeout=5)
                self.assertEqual(store.get(project.id).narration_draft.text, "第二稿。")
                self.assertEqual(store.get(project.id).narration_draft.shots, [])

                active = store.get(project.id)
                await api.generate_narration_storyboard(project.id, api.Revision(expected_revision=active.revision))
                await asyncio.wait_for(api.workers[f"plan:{project.id}"], timeout=5)
                preview = store.get(project.id)
                self.assertTrue(preview.narration_draft.shots)
                api.edit_brief(project.id, api.BriefUpdate(expected_revision=preview.revision, offer=preview.offer,
                    selling_points=preview.selling_points, style=preview.brand.style, target_duration=30))
                edited = store.get(project.id)
                self.assertEqual(edited.content_revision, preview.content_revision)
                self.assertEqual(edited.narration_draft.shots, [])
                self.assertEqual(edited.shots, project.shots)
                reverted = api.save_narration(project.id, api.NarrationUpdate(expected_revision=edited.revision, text="旧稿。"))
                self.assertIsNone(reverted["narration_draft"])

    async def test_digital_ai_plans_lina_structure_then_polishes_the_draft(self):
        fact = BrandFact(text="共 12 页", source_id="brief")
        project = ProductionProject(name="家庭阅读介绍", project_type="digital_presenter", presenter_id="lina",
                                    brand=BrandProfile(product="绘本", style="经过学习的表达风格", facts=[fact]))
        drafts = template_directions(project)[:2]
        drafts[0].shots[-1].spoken_text = ""
        drafts[0].shots[-1].subtitle = ""
        drafts[0].style_snapshot = load_creator_style("lina").model_copy(
            update={"version": "v999", "evidence_status": "verified"})
        observed = []
        style_inputs = []

        async def route(role, messages, *, validate, **_kwargs):
            self.assertEqual(role, "production.plan")
            system = messages[0]["content"]
            observed.append(system)
            if len(observed) == 1:
                self.assertIn("# Lina 短视频结构", system)
                self.assertNotIn("# Lina 口播语气润色", system)
                payload = json.loads(messages[1]["content"][0]["text"])
                self.assertEqual(payload["presenter"]["id"], "lina")
                style_inputs.append(payload["creator_style"])
                self.assertEqual(payload["creator_style"]["evidence_status"], "reported")
                self.assertEqual(payload["creator_style"]["version"], "v1")
                self.assertIn("LINA-01", payload["creator_style"]["card_text"])
                self.assertEqual(payload["brand"]["style"], "经过学习的表达风格")
                self.assertTrue(any("i9 RAG" in guidance for guidance in payload["presenter"]["guidance"]))
                self.assertEqual(payload["brand"]["product"], "绘本")
                return SimpleNamespace(value=validate(json.dumps({"directions": [d.model_dump() for d in drafts]})))

            self.assertIn("# Lina 口播语气润色", system)
            payload = json.loads(messages[1]["content"])
            self.assertEqual(payload["presenter"]["id"], "lina")
            self.assertEqual(payload["brand"]["style"], "经过学习的表达风格")
            self.assertEqual(payload["brand"]["product"], "绘本")
            draft_direction = payload["directions"][0]
            self.assertEqual(draft_direction["structure"], drafts[0].structure)
            self.assertEqual(draft_direction["shots"][0]["spoken_text"], drafts[0].shots[0].spoken_text)
            self.assertIn(fact.text, json.dumps(draft_direction["shots"][1]["facts"], ensure_ascii=False))
            polished = []
            for direction in payload["directions"]:
                shots = [{key: shot[key] for key in ("shot_id", "spoken_text", "subtitle")} for shot in direction["shots"]]
                shots[0].update(spoken_text="你家孩子会翻这本书吗？", subtitle="你家孩子会翻这本书吗？")
                shots[1].update(spoken_text="这本书一共 12 页。", subtitle="这本书一共 12 页")
                polished.append({"id": direction["id"], "shots": shots})
            return SimpleNamespace(value=validate(json.dumps({"directions": polished})))

        with patch("backend.services.production_planner.route_chat_completion", new=route):
            directions, _ = await plan_with_ai(project)
        self.assertEqual(len(observed), 2)
        self.assertTrue(all(d.style_snapshot.model_dump() == style_inputs[0] for d in directions))
        direction = directions[0]
        opening = direction.shots[0]
        self.assertEqual(direction.structure, drafts[0].structure)
        self.assertEqual(direction.hook, "你家孩子会翻这本书吗？")
        self.assertEqual((opening.spoken_text, opening.subtitle), (direction.hook, direction.hook))
        self.assertEqual((opening.tool, opening.voice_mode, opening.target_duration), ("HeyGen", "generated", 5))
        self.assertIn(opening.spoken_text, opening.prompt)
        self.assertIn(opening.spoken_text, opening.filming_guide)
        self.assertIn("Lina", opening.prompt)
        voiceover = direction.shots[1]
        self.assertEqual(voiceover.voice_mode, "voiceover")
        self.assertEqual((voiceover.spoken_text, voiceover.subtitle), ("这本书一共 12 页。", "这本书一共 12 页"))
        self.assertIn(voiceover.spoken_text, voiceover.prompt)
        self.assertEqual(voiceover.fact_ids, [fact.id])
        self.assertEqual(voiceover.visual, drafts[0].shots[1].visual)
        self.assertEqual((direction.shots[-1].spoken_text, direction.shots[-1].subtitle), ("", ""))

    async def test_invalid_style_bundle_stops_before_any_model_call(self):
        project = ProductionProject(name="无效风格配置", project_type="digital_presenter", brand=BrandProfile(product="书"))
        with patch("backend.services.production_planner.load_creator_style", side_effect=ValueError("版本不一致")), \
                patch("backend.services.production_planner.route_chat_completion") as route:
            with self.assertRaisesRegex(ValueError, "版本不一致"):
                await plan_with_ai(project)
            route.assert_not_called()

    async def test_digital_ai_rejects_polish_that_changes_shot_ids_or_adds_dialogue(self):
        project = ProductionProject(name="家庭阅读介绍", project_type="digital_presenter", brand=BrandProfile(product="绘本"))
        drafts = template_directions(project)[:2]
        drafts[0].shots[-1].spoken_text = ""
        drafts[0].shots[-1].subtitle = ""
        for invalid_field, invalid_value in (("shot_id", "S99"), ("spoken_text", "新添的旁白"), ("subtitle", "新添的字幕")):
            with self.subTest(invalid_field=invalid_field):
                async def route(role, messages, *, validate, **_kwargs):
                    if "# Lina 短视频结构" in messages[0]["content"]:
                        return SimpleNamespace(value=validate(json.dumps({"directions": [d.model_dump() for d in drafts]})))
                    revised = [{
                        "id": direction.id,
                        "shots": [shot.model_dump(include={"shot_id", "spoken_text", "subtitle"}) for shot in direction.shots],
                    } for direction in drafts]
                    revised[0]["shots"][-1][invalid_field] = invalid_value
                    return SimpleNamespace(value=validate(json.dumps({"directions": revised})))

                with patch("backend.services.production_planner.route_chat_completion", new=route):
                    with self.assertRaises(ValueError):
                        await plan_with_ai(project)

    async def test_selling_video_plans_in_one_call_without_lina_styles(self):
        project = ProductionProject(name="卖货视频", brand=BrandProfile(product="绘本"))
        drafts = template_directions(project)[:2]

        async def route(role, messages, *, validate, **_kwargs):
            self.assertEqual(role, "production.plan")
            self.assertNotIn("# Lina 短视频结构", messages[0]["content"])
            self.assertNotIn("# Lina 口播语气润色", messages[0]["content"])
            self.assertNotIn("presenter", json.loads(messages[1]["content"][0]["text"]))
            self.assertNotIn("creator_style", json.loads(messages[1]["content"][0]["text"]))
            return SimpleNamespace(value=validate(json.dumps({"directions": [d.model_dump() for d in drafts]})))

        with patch("backend.services.production_planner.route_chat_completion", side_effect=route) as mocked_route:
            directions, _ = await plan_with_ai(project)
        mocked_route.assert_awaited_once()
        self.assertTrue(all(d.style_snapshot is None for d in directions))
        self.assertEqual(directions[0].shots[0].spoken_text, drafts[0].shots[0].spoken_text)

    async def test_duplicate_render_cancel_retry_and_new_revision_keeps_old_result(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(api.manager, "broadcast", new=self._broadcast):
            store = ProductionStore(Path(temp))
            p = fixture()
            p.assets[0].path = str(store.directory(p.id) / "source.mp4")
            p = store.create(p)
            async def delayed(*args):
                await args[-1](.1, "处理中")
                await asyncio.sleep(60)
            with patch.object(api, "store", store), patch.object(api, "render_production", delayed):
                first = await api.request_render(p.id, api.Revision(expected_revision=p.revision))
                duplicate = await api.request_render(p.id, api.Revision(expected_revision=p.revision))
                self.assertEqual(first["renders"][0]["id"], duplicate["renders"][0]["id"])
                await asyncio.sleep(.01)
                cancelled = await api.cancel_render(p.id, first["renders"][0]["id"])
                self.assertEqual(cancelled["renders"][0]["status"], "cancelled")
                retry = await api.request_render(p.id, api.Revision(expected_revision=cancelled["revision"]))
                self.assertEqual(retry["renders"][0]["attempt"], 2)
                await api.cancel_render(p.id, retry["renders"][0]["id"])
                job = store.get(p.id).renders[0]
                async def complete(timeline, assets, directory, progress):
                    store.update(p.id, lambda x: setattr(x, "offer", "新方案"))
                    return {}, {"passed": True}
                store.update(p.id, lambda x: setattr(x.renders[0], "status", "running"), content=False)
                with patch.object(api, "render_production", complete):
                    await api._render(p.id, job)
                final = api.public(store.get(p.id))
                self.assertEqual(final["renders"][0]["status"], "completed")
                self.assertFalse(final["renders"][0]["is_current"])
                self.assertNotEqual(final["stage"], "completed")

    async def _broadcast(self, *args):
        pass

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
    async def test_real_multisource_render_subtitles_and_editable_draft(self):
        with tempfile.TemporaryDirectory(prefix="production-media-test-") as tmp:
            root, assets, shots = Path(tmp), [], []
            for i, size in enumerate(["320x180", "180x320", "240x240"]):
                path = root / f"s{i}.mp4"
                subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c={'red' if i == 0 else 'blue'}:s={size}:d=0.6:r=30",
                                "-f", "lavfi", "-i", "sine=frequency=440:duration=0.6", "-c:v", "libx264", "-c:a", "aac", "-shortest", str(path)], check=True)
                asset = inspect_asset(path, path.name, "footage", f"S{i+1:02}")
                asset.cues = [Cue(start=0, end=.5, text=f"第{i+1}段中文字幕")]
                shot = StoryboardShot(shot_id=f"S{i+1:02}", purpose="测试", visual="画幅检查", selection=AssetSelection(asset_id=asset.id, start=0, end=.5))
                shot.verified_signature = signature(shot)
                assets.append(asset); shots.append(shot)
            p = ProductionProject(name="真实渲染验证", brand=BrandProfile(name="测试品牌"), assets=assets, shots=shots)
            timeline = compile_timeline(p)
            async def progress(*args):
                pass
            outputs, checks = await render_production(timeline, assets, root / "out", progress)
            self.assertTrue(checks["full_decode"])
            self.assertTrue(checks["draft_structure"])
            self.assertFalse(checks["jianying_open_verified"])
            self.assertFalse(checks["subtitles_burned"])
            self.assertFalse(checks["brand_overlay_burned"])
            draft = json.loads((root / "out/jianying/GoldenClip/draft_content.json").read_text())
            self.assertEqual(len(next(t for t in draft["tracks"] if t["type"] == "video")["segments"]), 3)
            self.assertTrue(any(t["type"] == "text" and len(t["segments"]) == 3 for t in draft["tracks"]))
            self.assertIn("第1段中文字幕", Path(outputs["srt"]).read_text())
            # The first landscape shot has black top/bottom padding. Burned
            # captions or the global brand label would put bright pixels there.
            frame = subprocess.check_output(["ffmpeg", "-v", "error", "-ss", "0.25", "-i", outputs["mp4"],
                                             "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"])
            self.assertEqual(len(frame), timeline.width * timeline.height)
            self.assertLess(max(frame[:timeline.width * 400]), 20)
            self.assertLess(max(frame[-timeline.width * 400:]), 20)
            self.assertTrue(all(Path(path).stat().st_size > 0 for path in outputs.values()))


if __name__ == "__main__":
    unittest.main()
