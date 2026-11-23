import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from backend.main import EXPORTS_DIR
from backend.models.task import (
    Segment,
    SegmentAction,
    Task,
    TaskStatus,
    TaskType,
    HighlightReel,
    HighlightExportStatus,
    HighlightReelStatus,
    HighlightReelVariant,
    ExportJob,
    ExportJobKind,
    ExportJobStatus,
    ExportRequest,
    RenderProfile,
)
from backend.services import ffmpeg_executor
from backend.routers import export as export_router


class HighlightClipExportTests(unittest.IsolatedAsyncioTestCase):
    async def test_export_preview_does_not_require_approval_or_replace_latest_final_job(self):
        task = Task(
            id="task-render-preview",
            name="preview",
            task_type=TaskType.MONOLOGUE_CLEAN,
            status=TaskStatus.REVIEW,
            video_path="/tmp/source.mp4",
            audit_segments=[
                Segment(id="seg_1", start=0.0, end=3.0, text="预览", action=SegmentAction.KEEP),
            ],
            content_revision=2,
        )
        fake_store = SimpleNamespace(get=lambda task_id: task if task_id == task.id else None, update=lambda t: t)
        scheduled = []

        def fake_create_task(coro):
            scheduled.append(coro)
            return SimpleNamespace(cancel=lambda: None)

        with patch("backend.routers.export.get_store", return_value=fake_store), patch(
            "backend.routers.export.os.path.exists",
            return_value=True,
        ), patch(
            "backend.routers.export._source_media_snapshot",
            return_value={"path": task.video_path, "size": 100, "mtime_ns": 1},
        ), patch(
            "backend.routers.export.asyncio.create_task",
            side_effect=fake_create_task,
        ), patch(
            "backend.routers.export._run_ffmpeg_export",
            new=AsyncMock(return_value=None),
        ) as mock_runner:
            result = await export_router.export_ffmpeg(
                task.id,
                ExportRequest(
                    output_name="preview",
                    render_profile=RenderProfile.PREVIEW,
                ),
            )

        for coro in scheduled:
            coro.close()

        self.assertTrue(result["success"])
        self.assertEqual(task.export_jobs[0].render_profile, RenderProfile.PREVIEW)
        self.assertIsNone(task.latest_export_job_id)
        self.assertEqual(mock_runner.call_args.args[2].render_profile, RenderProfile.PREVIEW)

    async def test_export_ffmpeg_uses_snapshot_and_leaves_task_in_review(self):
        task = Task(
            id="task-ffmpeg-job",
            name="ffmpeg",
            task_type=TaskType.MONOLOGUE_CLEAN,
            status=TaskStatus.REVIEW,
            video_path="/tmp/source.mp4",
            audit_segments=[
                Segment(id="seg_1", start=0.0, end=3.0, text="原字幕", action=SegmentAction.KEEP),
            ],
            content_revision=4,
        )
        fake_store = SimpleNamespace(get=lambda task_id: task if task_id == task.id else None, update=lambda t: t)
        scheduled = []

        def fake_exists(path):
            return path == task.video_path

        def fake_create_task(coro):
            scheduled.append(coro)
            return SimpleNamespace(cancel=lambda: None)

        with patch("backend.routers.export.get_store", return_value=fake_store), patch(
            "backend.routers.export.os.path.exists",
            side_effect=fake_exists,
        ), patch(
            "backend.routers.export._source_media_snapshot",
            return_value={"path": task.video_path, "size": 100, "mtime_ns": 1},
        ), patch(
            "backend.routers.export.asyncio.create_task",
            side_effect=fake_create_task,
        ), patch(
            "backend.routers.export._run_ffmpeg_export",
            new=AsyncMock(return_value=None),
        ) as mock_runner:
            result = await export_router.export_ffmpeg(
                task.id,
                ExportRequest(output_name="demo", approval_token="review-workbench"),
            )
            task.audit_segments[0].text = "后续编辑"

        for coro in scheduled:
            coro.close()

        self.assertTrue(result["success"])
        self.assertEqual(task.status, TaskStatus.REVIEW)
        self.assertEqual(len(task.export_jobs), 1)
        self.assertEqual(task.export_jobs[0].source_content_revision, 4)
        self.assertEqual(mock_runner.call_args.kwargs["segments"][0].text, "原字幕")
        self.assertEqual(task.audit_segments[0].text, "后续编辑")

    async def test_export_ffmpeg_multi_clips_exports_each_group_without_mutating_original_segments(self):
        seg_a = Segment(id="seg_a", start=0.0, end=4.0, text="A", action=SegmentAction.KEEP)
        seg_gap = Segment(id="seg_gap", start=4.0, end=8.0, text="GAP", action=SegmentAction.DELETE)
        seg_b = Segment(id="seg_b", start=8.0, end=12.0, text="B", action=SegmentAction.KEEP)

        captured_groups = []

        async def fake_export(video_path, segments, output_path, log_callback=None, **kwargs):
            captured_groups.append((video_path, [s.id for s in segments], [s.action for s in segments], output_path))
            return True

        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "backend.services.ffmpeg_executor.export_ffmpeg_lossless",
            new=AsyncMock(side_effect=fake_export),
        ):
            output_paths = await ffmpeg_executor.export_ffmpeg_multi_clips(
                video_path="source.mp4",
                clip_groups=[[seg_a, seg_gap, seg_b]],
                output_dir=temp_dir,
                base_name="demo",
            )

        self.assertEqual(len(output_paths), 1)
        self.assertEqual(captured_groups[0][1], ["seg_a", "seg_gap", "seg_b"])
        self.assertEqual(captured_groups[0][2], [SegmentAction.KEEP, SegmentAction.DELETE, SegmentAction.KEEP])
        self.assertEqual(seg_a.action, SegmentAction.KEEP)
        self.assertEqual(seg_gap.action, SegmentAction.DELETE)
        self.assertEqual(seg_b.action, SegmentAction.KEEP)

    async def test_export_clip_preview_sorts_segments_and_returns_preview_url(self):
        seg_late = Segment(id="seg_late", start=10.0, end=12.0, text="晚", action=SegmentAction.KEEP)
        seg_gap = Segment(id="seg_gap", start=4.0, end=10.0, text="中间删掉", action=SegmentAction.DELETE)
        seg_early = Segment(id="seg_early", start=2.0, end=4.0, text="早", action=SegmentAction.KEEP)
        task = Task(
            id="task-preview",
            name="preview",
            task_type=TaskType.HIGHLIGHT_REEL,
            status=TaskStatus.REVIEW,
            video_path="/tmp/source.mp4",
            audit_segments=[seg_late, seg_gap, seg_early],
        )

        captured = {}
        fake_store = SimpleNamespace(get=lambda task_id: task if task_id == task.id else None, update=lambda t: t)

        async def fake_export(video_path, segments, output_path, log_callback=None, **kwargs):
            captured["video_path"] = video_path
            captured["segment_ids"] = [s.id for s in segments]
            captured["actions"] = [s.action for s in segments]
            captured["output_path"] = output_path
            return True

        def fake_exists(path):
            return path == task.video_path

        with patch("backend.routers.export.get_store", return_value=fake_store), patch(
            "backend.routers.export.os.path.exists",
            side_effect=fake_exists,
        ), patch(
            "backend.routers.export.export_ffmpeg_lossless",
            new=AsyncMock(side_effect=fake_export),
        ):
            result = await export_router.export_clip_preview(task.id, {"segment_ids": ["seg_late", "seg_early"]})

        self.assertTrue(result["url"].startswith("/api/exports/preview_"))
        self.assertEqual(captured["segment_ids"], ["seg_early", "seg_gap", "seg_late"])
        self.assertEqual(captured["actions"], [SegmentAction.KEEP, SegmentAction.DELETE, SegmentAction.KEEP])
        self.assertEqual(seg_late.action, SegmentAction.KEEP)
        self.assertEqual(seg_gap.action, SegmentAction.DELETE)
        self.assertEqual(seg_early.action, SegmentAction.KEEP)

    async def test_export_clip_preview_cache_key_changes_when_segment_boundaries_change(self):
        segment = Segment(id="seg_1", start=1.0, end=3.0, text="片段", action=SegmentAction.KEEP)
        task = Task(
            id="task-preview-cache",
            name="preview-cache",
            task_type=TaskType.HIGHLIGHT_REEL,
            status=TaskStatus.REVIEW,
            video_path="/tmp/source.mp4",
            audit_segments=[segment],
        )
        fake_store = SimpleNamespace(get=lambda task_id: task if task_id == task.id else None, update=lambda t: t)
        output_paths = []

        async def fake_export(video_path, segments, output_path, log_callback=None, **kwargs):
            output_paths.append(output_path)
            return True

        def fake_exists(path):
            return path == task.video_path

        with patch("backend.routers.export.get_store", return_value=fake_store), patch(
            "backend.routers.export.os.path.exists",
            side_effect=fake_exists,
        ), patch(
            "backend.routers.export.export_ffmpeg_lossless",
            new=AsyncMock(side_effect=fake_export),
        ):
            await export_router.export_clip_preview(task.id, {"segment_ids": ["seg_1"]})
            task.audit_segments[0].end = 4.5
            await export_router.export_clip_preview(task.id, {"segment_ids": ["seg_1"]})

        self.assertEqual(len(output_paths), 2)
        self.assertNotEqual(Path(output_paths[0]).name, Path(output_paths[1]).name)

    async def test_export_highlight_clip_creates_single_reel_job_snapshot(self):
        segments = [
            Segment(id="seg_1", start=0.0, end=3.0, text="1", action=SegmentAction.KEEP),
            Segment(id="seg_gap", start=3.0, end=5.0, text="gap", action=SegmentAction.DELETE),
            Segment(id="seg_2", start=5.0, end=8.0, text="2", action=SegmentAction.KEEP),
        ]
        task = Task(
            id="task-export",
            name="highlights",
            task_type=TaskType.HIGHLIGHT_REEL,
            status=TaskStatus.REVIEW,
            video_path="/tmp/source.mp4",
            audit_segments=segments,
            content_revision=7,
            highlight_revision=3,
            highlight_reels=[
                HighlightReel(
                    reel_id="reel_a",
                    clip_group=1,
                    title="A",
                    segment_ids=["seg_1", "seg_2"],
                    start=0.0,
                    end=8.0,
                    duration=6.0,
                    status=HighlightReelStatus.PASSED,
                    source_content_revision=7,
                ),
            ],
        )
        fake_store = SimpleNamespace(get=lambda task_id: task if task_id == task.id else None, update=lambda t: t)
        scheduled = []

        def fake_exists(path):
            return path == task.video_path

        def fake_create_task(coro):
            scheduled.append(coro)
            return SimpleNamespace(cancel=lambda: None)

        with patch("backend.routers.export.get_store", return_value=fake_store), patch(
            "backend.routers.export.os.path.exists",
            side_effect=fake_exists,
        ), patch(
            "backend.routers.export.asyncio.create_task",
            side_effect=fake_create_task,
        ), patch(
            "backend.routers.export._run_highlight_clips_export",
            new=AsyncMock(return_value=None),
        ) as mock_runner:
            result = await export_router.export_highlight_clips(
                task.id,
                {
                    "reel_id": "reel_a",
                    "output_name": "demo",
                    "approval_token": "review-workbench",
                },
            )

        for coro in scheduled:
            coro.close()

        self.assertTrue(result["success"])
        self.assertEqual(len(scheduled), 1)
        self.assertEqual(len(task.export_jobs), 1)
        self.assertEqual(task.status, TaskStatus.REVIEW)
        self.assertEqual(task.export_jobs[0].reel_id, "reel_a")
        self.assertEqual(task.export_jobs[0].reel_variant, HighlightReelVariant.AFTER_QUALITY)
        self.assertEqual(task.export_jobs[0].source_content_revision, 7)
        self.assertEqual(task.export_jobs[0].source_highlight_revision, 3)
        self.assertIsNone(task.latest_export_job_id)
        self.assertEqual(
            task.highlight_reels[0].export_status,
            HighlightExportStatus.QUEUED,
        )
        self.assertNotIn("segments", task.export_jobs[0].snapshot)
        self.assertNotIn("reel", task.export_jobs[0].snapshot)
        self.assertNotIn("params", task.export_jobs[0].snapshot)
        self.assertEqual(
            task.export_jobs[0].snapshot["segment_ids"],
            ["seg_1", "seg_2"],
        )
        self.assertEqual(mock_runner.call_args.args[0], task.id)
        self.assertEqual(
            [(seg.id, seg.action) for seg in mock_runner.call_args.args[2]],
            [
                ("seg_1", SegmentAction.KEEP),
                ("seg_gap", SegmentAction.DELETE),
                ("seg_2", SegmentAction.KEEP),
            ],
        )
        self.assertEqual(mock_runner.call_args.args[3], "demo")
        self.assertEqual(mock_runner.call_args.args[4], "reel_a")
        self.assertEqual(mock_runner.call_args.args[5], HighlightReelVariant.AFTER_QUALITY)

    async def test_export_highlight_clip_rejects_before_quality_variant(self):
        segments = [
            Segment(id="seg_1", start=0.0, end=3.0, text="1", action=SegmentAction.KEEP),
        ]
        task = Task(
            id="task-export-before",
            name="highlights",
            task_type=TaskType.HIGHLIGHT_REEL,
            status=TaskStatus.REVIEW,
            video_path="/tmp/source.mp4",
            audit_segments=segments,
            highlight_reels=[
                HighlightReel(
                    reel_id="after_reel",
                    clip_group=1,
                    title="After",
                    segment_ids=["seg_1"],
                    start=0.0,
                    end=3.0,
                    duration=3.0,
                ),
            ],
            highlight_reels_before_quality=[
                HighlightReel(
                    reel_id="before_reel",
                    clip_group=1,
                    title="Before",
                    segment_ids=["seg_1"],
                    start=0.0,
                    end=3.0,
                    duration=3.0,
                ),
            ],
        )
        fake_store = SimpleNamespace(get=lambda task_id: task if task_id == task.id else None, update=lambda t: t)
        with patch("backend.routers.export.get_store", return_value=fake_store), patch(
            "backend.routers.export.os.path.exists",
            return_value=True,
        ):
            with self.assertRaises(Exception) as ctx:
                await export_router.export_highlight_clips(
                    task.id,
                    {
                        "reel_id": "before_reel",
                        "reel_variant": "before_quality",
                        "output_name": "demo",
                        "approval_token": "review-workbench",
                    },
                )

        self.assertEqual(getattr(ctx.exception, "status_code", None), 409)
        self.assertIn("质检前集锦", str(ctx.exception))

    async def test_export_highlight_clips_rejects_multiple_reels(self):
        segments = [
            Segment(id="seg_1", start=0.0, end=3.0, text="1", action=SegmentAction.KEEP),
            Segment(id="seg_2", start=5.0, end=8.0, text="2", action=SegmentAction.KEEP),
        ]
        task = Task(
            id="task-reels",
            name="highlights",
            task_type=TaskType.HIGHLIGHT_REEL,
            status=TaskStatus.REVIEW,
            video_path="/tmp/source.mp4",
            audit_segments=segments,
            highlight_reels=[
                HighlightReel(
                    reel_id="reel_a",
                    clip_group=1,
                    title="A",
                    segment_ids=["seg_1"],
                    start=0.0,
                    end=3.0,
                    duration=3.0,
                    status=HighlightReelStatus.PASSED,
                ),
                HighlightReel(
                    reel_id="reel_b",
                    clip_group=2,
                    title="B",
                    segment_ids=["seg_2"],
                    start=5.0,
                    end=8.0,
                    duration=3.0,
                ),
            ],
        )
        fake_store = SimpleNamespace(get=lambda task_id: task if task_id == task.id else None, update=lambda t: t)

        def fake_exists(path):
            return path == task.video_path

        with patch("backend.routers.export.get_store", return_value=fake_store), patch(
            "backend.routers.export.os.path.exists",
            side_effect=fake_exists,
        ):
            with self.assertRaises(Exception) as ctx:
                await export_router.export_highlight_clips(
                    task.id,
                    {
                        "reel_ids": ["reel_b", "reel_a"],
                        "output_name": "demo",
                        "approval_token": "review-workbench",
                    },
                )

        self.assertIn("一次只能导出一条", str(ctx.exception))

    async def test_export_highlight_clips_rejects_when_job_is_active(self):
        task = Task(
            id="task-active-export",
            name="highlights",
            task_type=TaskType.HIGHLIGHT_REEL,
            status=TaskStatus.REVIEW,
            video_path="/tmp/source.mp4",
            audit_segments=[
                Segment(id="seg_1", start=0.0, end=3.0, text="1", action=SegmentAction.KEEP),
            ],
            highlight_reels=[
                HighlightReel(
                    reel_id="reel_a",
                    clip_group=1,
                    title="A",
                    segment_ids=["seg_1"],
                    start=0.0,
                    end=3.0,
                    duration=3.0,
                    status=HighlightReelStatus.PASSED,
                ),
            ],
            export_jobs=[
                ExportJob(
                    id="job_running",
                    task_id="task-active-export",
                    kind=ExportJobKind.HIGHLIGHT_REEL,
                    status=ExportJobStatus.RUNNING,
                    reel_id="reel_a",
                )
            ],
        )
        fake_store = SimpleNamespace(get=lambda task_id: task if task_id == task.id else None, update=lambda t: t)

        def fake_exists(path):
            return path == task.video_path

        with patch("backend.routers.export.get_store", return_value=fake_store), patch(
            "backend.routers.export.os.path.exists",
            side_effect=fake_exists,
        ):
            with self.assertRaises(Exception) as ctx:
                await export_router.export_highlight_clips(
                    task.id,
                    {
                        "reel_id": "reel_a",
                        "output_name": "demo",
                        "approval_token": "review-workbench",
                    },
                )

        self.assertIn("已有导出正在进行", str(ctx.exception))

    async def test_export_highlight_clips_rejects_stale_reel_segment_ids(self):
        segments = [
            Segment(id="current_1", start=10.0, end=13.0, text="1", action=SegmentAction.KEEP),
            Segment(id="current_2", start=13.0, end=16.0, text="2", action=SegmentAction.KEEP),
            Segment(id="deleted", start=16.0, end=18.0, text="delete", action=SegmentAction.DELETE),
            Segment(id="outside", start=30.0, end=35.0, text="outside", action=SegmentAction.KEEP),
        ]
        task = Task(
            id="task-stale-reel",
            name="highlights",
            task_type=TaskType.HIGHLIGHT_REEL,
            status=TaskStatus.REVIEW,
            video_path="/tmp/source.mp4",
            audit_segments=segments,
            highlight_reels=[
                HighlightReel(
                    reel_id="reel_stale",
                    clip_group=1,
                    title="旧计划",
                    segment_ids=["old_1", "old_2"],
                    start=9.0,
                    end=20.0,
                    duration=11.0,
                    status=HighlightReelStatus.PASSED,
                ),
            ],
        )
        fake_store = SimpleNamespace(get=lambda task_id: task if task_id == task.id else None, update=lambda t: t)
        def fake_exists(path):
            return path == task.video_path

        with patch("backend.routers.export.get_store", return_value=fake_store), patch(
            "backend.routers.export.os.path.exists",
            side_effect=fake_exists,
        ):
            with self.assertRaises(Exception) as ctx:
                await export_router.export_highlight_clips(
                    task.id,
                    {
                        "reel_ids": ["reel_stale"],
                        "output_name": "demo",
                        "approval_token": "review-workbench",
                    },
                )

        self.assertEqual(getattr(ctx.exception, "status_code", None), 409)
        self.assertIn("字幕片段已变化", str(ctx.exception))

    async def test_export_highlight_clips_requires_approval_token(self):
        task = Task(
            id="task-approval",
            name="approval",
            task_type=TaskType.HIGHLIGHT_REEL,
            status=TaskStatus.REVIEW,
            video_path="/tmp/source.mp4",
            audit_segments=[
                Segment(id="seg_1", start=0.0, end=3.0, text="1", action=SegmentAction.KEEP),
            ],
        )
        fake_store = SimpleNamespace(get=lambda task_id: task if task_id == task.id else None, update=lambda t: t)

        with patch("backend.routers.export.get_store", return_value=fake_store):
            with self.assertRaises(Exception) as ctx:
                await export_router.export_highlight_clips(
                    task.id,
                    {"clip_groups": [["seg_1"]], "output_name": "demo"},
                )

        self.assertIn("需要人工确认", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
