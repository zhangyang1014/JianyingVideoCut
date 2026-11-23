import asyncio
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from backend.models.task import (
    ExportJob,
    ExportJobKind,
    ExportJobStatus,
    HighlightReel,
    HighlightExportStatus,
    HighlightReelVariant,
    RenderQualityReport,
    Segment,
    SegmentAction,
    Task,
    TaskStatus,
    TaskType,
)
from backend.routers import export as export_router
from backend.routers import tasks as tasks_router
from backend.core import task_runner as task_runner_module
from backend.services import ffmpeg_executor, jianying_builder
from backend.services import task_store as task_store_module


def _passed_quality(*args, **kwargs):
    return RenderQualityReport(
        passed=True,
        video_stream_present=True,
        audio_stream_present=True,
    )


class FFmpegRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_chunk_is_not_published_and_retry_reuses_successful_chunk(self):
        segments = [
            Segment(id="a", start=0, end=2, text="a", action=SegmentAction.KEEP),
            Segment(id="gap", start=2, end=3, text="x", action=SegmentAction.DELETE),
            Segment(id="b", start=3, end=5, text="b", action=SegmentAction.KEEP),
        ]
        calls = []
        fail_second_once = {"value": True}

        def fake_execute(cmd, **kwargs):
            calls.append(list(cmd))
            output = Path(cmd[-1])
            if (
                "-ss" in cmd
                and float(cmd[cmd.index("-ss") + 1]) == 3.0
                and fail_second_once["value"]
            ):
                fail_second_once["value"] = False
                return False, "temporary encoder failure"
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"valid-media")
            return True, ""

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.mp4"
            source.write_bytes(b"source")
            output = root / "result.mp4"
            with patch.object(ffmpeg_executor, "EXPORTS_DIR", root / "exports"), patch.object(
                ffmpeg_executor,
                "execute_with_retry",
                side_effect=fake_execute,
            ), patch.object(
                ffmpeg_executor,
                "_cached_chunk_is_valid",
                side_effect=lambda path, duration: path.exists() and path.stat().st_size > 0,
            ), patch.object(
                ffmpeg_executor,
                "validate_rendered_media",
                side_effect=_passed_quality,
            ):
                first = await ffmpeg_executor.export_ffmpeg_lossless(
                    str(source), segments, str(output), task_id="job-recovery"
                )
                self.assertFalse(first)
                self.assertFalse(output.exists())
                self.assertFalse(Path(f"{output}.part").exists())

                second = await ffmpeg_executor.export_ffmpeg_lossless(
                    str(source), segments, str(output), task_id="job-recovery-2"
                )

            self.assertTrue(second)
            self.assertTrue(output.exists())
            chunk_starts = [
                float(cmd[cmd.index("-ss") + 1])
                for cmd in calls
                if "-ss" in cmd
            ]
            self.assertEqual(chunk_starts.count(0.0), 1)
            self.assertEqual(chunk_starts.count(3.0), 2)
            manifests = list((root / "exports" / ".chunk_cache" / "manifests").glob("*.json"))
            self.assertEqual(len(manifests), 1)
            payload = json.loads(manifests[0].read_text(encoding="utf-8"))
            self.assertEqual([item["status"] for item in payload["chunks"]], ["reused", "succeeded"])

    def test_kill_export_waits_then_kills_and_cleans_part(self):
        class FakeProcess:
            pid = 123456

            def __init__(self):
                self.terminated = False
                self.killed = False
                self.wait_calls = 0

            def terminate(self):
                self.terminated = True

            def kill(self):
                self.killed = True

            def wait(self, timeout=None):
                self.wait_calls += 1
                if self.wait_calls == 1:
                    raise subprocess.TimeoutExpired("ffmpeg", timeout)
                return 0

            def poll(self):
                return None

        with tempfile.TemporaryDirectory() as temp:
            part = Path(temp) / "output.mp4.part"
            part.write_bytes(b"partial")
            proc = FakeProcess()
            ffmpeg_executor.register_process("job-cancel", proc, [part])
            with patch.object(ffmpeg_executor.os, "killpg", side_effect=OSError):
                killed = ffmpeg_executor.kill_export("job-cancel", timeout=0.01)

            self.assertTrue(killed)
            self.assertTrue(proc.terminated)
            self.assertTrue(proc.killed)
            self.assertEqual(proc.wait_calls, 2)
            self.assertFalse(part.exists())
            self.assertFalse(ffmpeg_executor.is_export_process_alive("job-cancel"))


class JianyingStagingTests(unittest.IsolatedAsyncioTestCase):
    async def test_fallback_replaces_old_directory_without_mixing_and_cleans_staging(self):
        segment = Segment(id="a", start=0, end=2, text="字幕", action=SegmentAction.KEEP)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old_dir = root / "same-name"
            old_dir.mkdir()
            (old_dir / "old-only.txt").write_text("old", encoding="utf-8")

            with patch.object(jianying_builder, "EXPORTS_DIR", root), patch.dict(
                "sys.modules",
                {"pyJianYingDraft": None},
            ):
                result = await jianying_builder.build_jianying_draft(
                    "source.mp4",
                    [segment],
                    "same-name",
                )

            self.assertEqual(result, str(old_dir / "goldenclip_draft.json"))
            self.assertFalse((old_dir / "old-only.txt").exists())
            marker = json.loads((old_dir / "goldenclip_build.json").read_text(encoding="utf-8"))
            self.assertEqual(marker["builder"], "fallback_json")
            self.assertEqual(list(root.glob(".same-name.staging-*")), [])


class UploadAtomicityTests(unittest.IsolatedAsyncioTestCase):
    async def test_interrupted_upload_does_not_overwrite_existing_media(self):
        class InterruptedUpload:
            filename = "video.mp4"
            size = None

            def __init__(self):
                self.calls = 0

            async def read(self, size):
                self.calls += 1
                if self.calls == 1:
                    return b"new-partial"
                raise RuntimeError("connection lost")

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            uploads = root / "uploads"
            thumbs = root / "thumbs"
            uploads.mkdir()
            old_path = uploads / "upload-task.mp4"
            old_path.write_bytes(b"old-complete")
            task = Task(
                id="upload-task",
                name="upload",
                task_type=TaskType.MONOLOGUE_CLEAN,
                status=TaskStatus.PENDING,
                video_path=str(old_path),
                video_filename="old.mp4",
            )
            store = SimpleNamespace(get=lambda task_id: task, update=lambda value: value)
            with patch.object(tasks_router, "UPLOADS_DIR", uploads), patch.object(
                tasks_router, "THUMBNAILS_DIR", thumbs
            ), patch.object(tasks_router, "get_store", return_value=store):
                with self.assertRaises(RuntimeError):
                    await tasks_router.upload_video(task.id, InterruptedUpload())

            self.assertEqual(old_path.read_bytes(), b"old-complete")
            self.assertFalse(Path(f"{old_path}.part").exists())

    async def test_matching_idempotency_key_reuses_published_upload_after_hash_check(self):
        class RepeatedUpload:
            filename = "video.mp4"
            size = 3

            def __init__(self):
                self.sent = False

            async def read(self, size):
                if not self.sent:
                    self.sent = True
                    return b"old"
                return b""

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            uploads = root / "uploads"
            thumbs = root / "thumbs"
            uploads.mkdir()
            video_path = uploads / "upload-reuse.mp4"
            video_path.write_bytes(b"old")
            (uploads / "upload-reuse.upload.json").write_text(
                json.dumps({
                    "idempotency_key": "same-key",
                    "size": 3,
                    "sha256": "cba06b5736faf67e54b07b561eae94395e774c517a7d910a54369e1263ccfbd4",
                }),
                encoding="utf-8",
            )
            task = Task(
                id="upload-reuse",
                name="upload",
                task_type=TaskType.MONOLOGUE_CLEAN,
                video_path=str(video_path),
                video_filename="video.mp4",
                video_duration=1,
            )
            store = SimpleNamespace(get=lambda task_id: task, update=lambda value: value)
            with patch.object(tasks_router, "UPLOADS_DIR", uploads), patch.object(
                tasks_router, "THUMBNAILS_DIR", thumbs
            ), patch.object(tasks_router, "get_store", return_value=store):
                result = await tasks_router.upload_video(
                    task.id,
                    RepeatedUpload(),
                    idempotency_key="same-key",
                )

            self.assertTrue(result["idempotent_reuse"])
            self.assertEqual(video_path.read_bytes(), b"old")


class HighlightQualityTests(unittest.IsolatedAsyncioTestCase):
    async def test_highlight_quality_failure_does_not_publish_mp4_or_srt(self):
        segment = Segment(id="s", start=0, end=2, text="字幕", action=SegmentAction.KEEP)
        reel = HighlightReel(
            reel_id="reel",
            clip_group=1,
            title="集锦",
            segment_ids=["s"],
            duration=2,
        )
        job = ExportJob(
            id="quality-job",
            task_id="quality-task",
            kind=ExportJobKind.HIGHLIGHT_REEL,
            reel_id="reel",
            status=ExportJobStatus.QUEUED,
        )
        task = Task(
            id="quality-task",
            name="quality",
            task_type=TaskType.HIGHLIGHT_REEL,
            status=TaskStatus.REVIEW,
            video_path="/tmp/source.mp4",
            audit_segments=[segment],
            highlight_reels=[reel],
            export_jobs=[job],
        )
        store = SimpleNamespace(get=lambda task_id: task, update=lambda value: value)

        async def fake_export(video_path, segments, output_path, **kwargs):
            Path(output_path).write_bytes(b"bad-render")
            return True

        def fake_srt(*, srt_path, **kwargs):
            Path(srt_path).write_text("subtitle", encoding="utf-8")
            return srt_path

        with tempfile.TemporaryDirectory() as temp, patch.object(
            export_router, "EXPORTS_DIR", Path(temp)
        ), patch.object(export_router, "get_store", return_value=store), patch.object(
            export_router, "generate_srt", side_effect=fake_srt
        ), patch.object(
            export_router, "get_video_info", return_value={"width": 1920}
        ), patch.object(
            export_router, "export_ffmpeg_lossless", new=AsyncMock(side_effect=fake_export)
        ), patch.object(
            export_router,
            "validate_rendered_media",
            return_value=RenderQualityReport(passed=False),
        ), patch.object(
            export_router.manager, "broadcast", new=AsyncMock()
        ), patch.object(
            export_router, "log_to_ws", new=AsyncMock()
        ):
            await export_router._run_highlight_clips_export(
                task.id,
                job.id,
                [segment],
                "demo",
                reel.reel_id,
                HighlightReelVariant.AFTER_QUALITY,
                reel.title,
                task.video_path,
                params=task.params,
                burn_subtitles=False,
                subtitle_style=None,
            )
            exported_files = list(Path(temp).iterdir())

        self.assertEqual(job.status, ExportJobStatus.FAILED)
        self.assertEqual(reel.export_status, HighlightExportStatus.FAILED)
        self.assertFalse(any(path.suffix in {".mp4", ".srt"} for path in exported_files))
        self.assertIsNotNone(job.quality_report)


class ExportExecutionTrackingTests(unittest.IsolatedAsyncioTestCase):
    async def test_export_job_lifecycle_is_persisted_as_execution_run(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data_dir = root / "data"
            with patch.object(task_store_module, "DATA_DIR", data_dir), patch.object(
                task_store_module, "TASKS_FILE", data_dir / "tasks.json"
            ), patch.object(
                task_store_module, "TASKS_DIR", data_dir / "tasks"
            ), patch.object(
                task_store_module, "UPLOADS_DIR", root / "uploads"
            ), patch.object(
                task_store_module, "EXPORTS_DIR", root / "exports"
            ):
                store = task_store_module.TaskStore()
                job = ExportJob(
                    id="tracked-export",
                    task_id="tracked-task",
                    kind=ExportJobKind.FFMPEG,
                    status=ExportJobStatus.QUEUED,
                    render_fingerprint="render-sha256",
                )
                task = store.create(Task(
                    id="tracked-task",
                    name="tracked",
                    status=TaskStatus.REVIEW,
                    export_jobs=[job],
                ))

                async def complete_export():
                    current = store.get(task.id)
                    current.export_jobs[0].status = ExportJobStatus.SUCCEEDED
                    current.export_jobs[0].output_path = str(root / "result.mp4")
                    store.update(current)

                with patch.object(export_router, "get_store", return_value=store), patch.object(
                    task_runner_module, "get_store", return_value=store
                ):
                    runner = export_router._start_export_task(
                        task.id,
                        job,
                        complete_export(),
                    )
                    await runner

                saved_run = store.get(task.id).execution_runs[-1]
                self.assertEqual(saved_run.operation, "export_ffmpeg")
                self.assertEqual(saved_run.status, "succeeded")
                self.assertEqual(saved_run.stages[0].status, "succeeded")
                self.assertEqual(saved_run.stages[0].artifact_ref, str(root / "result.mp4"))


if __name__ == "__main__":
    unittest.main()
