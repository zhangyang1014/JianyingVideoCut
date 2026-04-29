import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from backend.main import EXPORTS_DIR
from backend.models.task import Segment, SegmentAction, Task, TaskStatus, TaskType
from backend.services import ffmpeg_executor
import backend.main as main


class HighlightClipExportTests(unittest.IsolatedAsyncioTestCase):
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
        fake_store = SimpleNamespace(get=lambda task_id: task if task_id == task.id else None)

        async def fake_export(video_path, segments, output_path, log_callback=None):
            captured["video_path"] = video_path
            captured["segment_ids"] = [s.id for s in segments]
            captured["actions"] = [s.action for s in segments]
            captured["output_path"] = output_path
            return True

        def fake_exists(path):
            return path == task.video_path

        with patch("backend.main.get_store", return_value=fake_store), patch(
            "backend.main.os.path.exists",
            side_effect=fake_exists,
        ), patch(
            "backend.main.export_ffmpeg_lossless",
            new=AsyncMock(side_effect=fake_export),
        ):
            result = await main.export_clip_preview(task.id, {"segment_ids": ["seg_late", "seg_early"]})

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
        fake_store = SimpleNamespace(get=lambda task_id: task if task_id == task.id else None)
        output_paths = []

        async def fake_export(video_path, segments, output_path, log_callback=None):
            output_paths.append(output_path)
            return True

        def fake_exists(path):
            return path == task.video_path

        with patch("backend.main.get_store", return_value=fake_store), patch(
            "backend.main.os.path.exists",
            side_effect=fake_exists,
        ), patch(
            "backend.main.export_ffmpeg_lossless",
            new=AsyncMock(side_effect=fake_export),
        ):
            await main.export_clip_preview(task.id, {"segment_ids": ["seg_1"]})
            task.audit_segments[0].end = 4.5
            await main.export_clip_preview(task.id, {"segment_ids": ["seg_1"]})

        self.assertEqual(len(output_paths), 2)
        self.assertNotEqual(Path(output_paths[0]).name, Path(output_paths[1]).name)

    async def test_export_highlight_clips_resolves_groups_and_starts_background_task(self):
        segments = [
            Segment(id="seg_1", start=0.0, end=3.0, text="1", action=SegmentAction.KEEP),
            Segment(id="seg_gap", start=3.0, end=5.0, text="gap", action=SegmentAction.DELETE),
            Segment(id="seg_2", start=5.0, end=8.0, text="2", action=SegmentAction.KEEP),
            Segment(id="seg_3", start=9.0, end=12.0, text="3"),
        ]
        task = Task(
            id="task-export",
            name="highlights",
            task_type=TaskType.HIGHLIGHT_REEL,
            status=TaskStatus.REVIEW,
            video_path="/tmp/source.mp4",
            audit_segments=segments,
        )
        fake_store = SimpleNamespace(get=lambda task_id: task if task_id == task.id else None)
        scheduled = []

        def fake_exists(path):
            return path == task.video_path

        def fake_create_task(coro):
            scheduled.append(coro)
            return SimpleNamespace(cancel=lambda: None)

        with patch("backend.main.get_store", return_value=fake_store), patch(
            "backend.main.os.path.exists",
            side_effect=fake_exists,
        ), patch(
            "backend.main.asyncio.create_task",
            side_effect=fake_create_task,
        ), patch(
            "backend.main._run_highlight_clips_export",
            new=AsyncMock(return_value=None),
        ) as mock_runner:
            result = await main.export_highlight_clips(
                task.id,
                {"clip_groups": [["seg_2", "seg_1"], ["seg_3"]], "output_name": "demo"},
            )

        for coro in scheduled:
            coro.close()

        self.assertTrue(result["success"])
        self.assertEqual(len(scheduled), 1)
        self.assertEqual(mock_runner.call_args.args[0], task.id)
        self.assertEqual(
            [[(seg.id, seg.action) for seg in group] for group in mock_runner.call_args.args[1]],
            [
                [
                    ("seg_1", SegmentAction.KEEP),
                    ("seg_gap", SegmentAction.DELETE),
                    ("seg_2", SegmentAction.KEEP),
                ],
                [("seg_3", SegmentAction.KEEP)],
            ],
        )
        self.assertEqual(mock_runner.call_args.args[2], "demo")


if __name__ == "__main__":
    unittest.main()
