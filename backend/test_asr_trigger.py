"""
POST /api/tasks/{task_id}/asr 启动行为测试。

覆盖：
- trigger_asr 立即持久化 asr_running 并广播 status_change
- 已在 asr_running 时重复触发不会替换后台 runner
"""

import asyncio
import os
import tempfile
import unittest
from types import SimpleNamespace
from typing import Optional
from unittest.mock import AsyncMock, patch

from backend.core import task_runner as core_runner
from backend.core.ws import manager
from backend.routers import asr as asr_router
from backend.models.task import (
    ASRResult,
    ASRSnapshot,
    AuditSnapshot,
    HighlightReel,
    Segment,
    SegmentAction,
    Task,
    TaskStatus,
    TaskType,
    WordTimestamp,
)


def _make_task(status: TaskStatus, *, video_path: Optional[str] = "/tmp/source.mp4") -> Task:
    return Task(
        id="task-asr-trigger",
        name="asr-trigger-test",
        task_type=TaskType.MONOLOGUE_CLEAN,
        status=status,
        video_path=video_path,
    )


def _fake_store(task: Task) -> SimpleNamespace:
    return SimpleNamespace(
        get=lambda task_id: task if task_id == task.id else None,
        update=lambda t: None,
    )


def _recognized_result() -> ASRResult:
    return ASRResult(
        words=[WordTimestamp(word="测", start=0.0, end=0.2)],
        duration=1.0,
    )


def _organized_result(text: str = "测") -> ASRResult:
    return ASRResult(
        words=[WordTimestamp(word=text, start=0.0, end=0.2)],
        segments=[Segment(start=0.0, end=0.2, text=text)],
        duration=1.0,
        analysis_report={
            "generated_at": "2026-01-01T00:01:00",
            "settings": {"silence_threshold": 0.3},
            "stats": {"segments_final": 1, "candidate_edits": 0},
            "steps": [],
            "segment_notes": [],
        },
    )


class AsrTriggerEndpointTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        core_runner._task_runners.clear()

    async def _wait_forever(self, *args, **kwargs):
        await asyncio.Event().wait()

    def test_analyze_request_rejects_out_of_range_threshold(self):
        with self.assertRaises(Exception):
            asr_router.AnalyzeRequest(silence_threshold=0.01)

        with self.assertRaises(Exception):
            asr_router.ResegmentRequest(silence_threshold=0.8, save=False)

    async def test_trigger_asr_sets_running_immediately_and_broadcasts(self):
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp.write(b"fake")
            video_path = tmp.name

        task = _make_task(TaskStatus.PENDING, video_path=video_path)
        broadcast = AsyncMock()

        try:
            with patch("backend.routers.asr.get_store", return_value=_fake_store(task)), patch(
                "backend.routers.asr.run_asr_recognize",
                new=AsyncMock(side_effect=self._wait_forever),
            ), patch.object(manager, "broadcast", broadcast):
                result = await asr_router.trigger_asr(task.id)

            self.assertTrue(result["success"])
            self.assertEqual(task.status, TaskStatus.ASR_RUNNING)
            self.assertIn(task.id, core_runner._task_runners)

            status_calls = [
                call.args[1].get("status")
                for call in broadcast.await_args_list
                if call.args[0] == task.id and call.args[1].get("type") == "status_change"
            ]
            self.assertIn(TaskStatus.ASR_RUNNING, status_calls)

            runner = core_runner._task_runners[task.id]
            await asyncio.sleep(0)
            runner.cancel()
            try:
                await runner
            except asyncio.CancelledError:
                pass
        finally:
            os.unlink(video_path)

    async def test_run_asr_auto_runs_text_organize_when_diarization_not_required(self):
        task = _make_task(TaskStatus.ASR_RUNNING)
        broadcast = AsyncMock()

        with patch("backend.routers.asr.get_store", return_value=_fake_store(task)), patch(
            "backend.routers.asr.run_asr_recognize",
            new=AsyncMock(return_value=_recognized_result()),
        ) as recognize_mock, patch(
            "backend.routers.asr.run_asr_analyze",
            new=AsyncMock(return_value=_organized_result()),
        ) as analyze_mock, patch.object(manager, "broadcast", broadcast), patch(
            "backend.routers.asr.log_to_ws",
            new=AsyncMock(),
        ):
            await asr_router._run_asr_task(task.id, backend="funasr")

        recognize_mock.assert_awaited_once()
        analyze_mock.assert_awaited_once()
        self.assertEqual(task.status, TaskStatus.ASR_DONE)
        self.assertEqual(len(task.asr_history), 1)
        self.assertEqual(len(task.analysis_history), 1)
        self.assertEqual(task.audit_segments[0].text, "测")

        status_calls = [
            call.args[1].get("status")
            for call in broadcast.await_args_list
            if call.args[0] == task.id and call.args[1].get("type") == "status_change"
        ]
        self.assertIn(TaskStatus.ANALYZE_RUNNING, status_calls)
        self.assertIn(TaskStatus.ASR_DONE, status_calls)

    async def test_run_asr_waits_for_diarization_when_required(self):
        task = _make_task(TaskStatus.ASR_RUNNING)
        task.params.enable_diarization = True

        with patch("backend.routers.asr.get_store", return_value=_fake_store(task)), patch(
            "backend.routers.asr.run_asr_recognize",
            new=AsyncMock(return_value=_recognized_result()),
        ), patch(
            "backend.routers.asr.run_asr_analyze",
            new=AsyncMock(return_value=_organized_result()),
        ) as analyze_mock, patch.object(manager, "broadcast", AsyncMock()), patch(
            "backend.routers.asr.log_to_ws",
            new=AsyncMock(),
        ):
            await asr_router._run_asr_task(task.id, backend="funasr")

        analyze_mock.assert_not_awaited()
        self.assertEqual(task.status, TaskStatus.ASR_RECOGNIZED)
        self.assertEqual(len(task.analysis_history), 0)

    async def test_trigger_analyze_sets_running_immediately_and_broadcasts(self):
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp.write(b"fake")
            video_path = tmp.name

        task = _make_task(TaskStatus.ASR_RECOGNIZED, video_path=video_path)
        task.asr_result = ASRResult(
            words=[WordTimestamp(word="测", start=0.0, end=0.2)],
            duration=1.0,
        )
        broadcast = AsyncMock()

        try:
            with patch("backend.routers.asr.get_store", return_value=_fake_store(task)), patch(
                "backend.routers.asr.run_asr_analyze",
                new=AsyncMock(side_effect=self._wait_forever),
            ), patch.object(manager, "broadcast", broadcast):
                result = await asr_router.trigger_asr_analyze(task.id)

            self.assertTrue(result["success"])
            self.assertEqual(task.status, TaskStatus.ANALYZE_RUNNING)
            self.assertIn(task.id, core_runner._task_runners)

            status_calls = [
                call.args[1].get("status")
                for call in broadcast.await_args_list
                if call.args[0] == task.id and call.args[1].get("type") == "status_change"
            ]
            self.assertIn(TaskStatus.ANALYZE_RUNNING, status_calls)

            runner = core_runner._task_runners[task.id]
            await asyncio.sleep(0)
            runner.cancel()
            try:
                await runner
            except asyncio.CancelledError:
                pass
        finally:
            os.unlink(video_path)

    async def test_trigger_analyze_allows_rerun_from_review(self):
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp.write(b"fake")
            video_path = tmp.name

        task = _make_task(TaskStatus.REVIEW, video_path=video_path)
        task.asr_result = ASRResult(
            words=[WordTimestamp(word="<FIL s=0.000 e=0.200>嗯", start=0.0, end=0.2)],
            duration=1.0,
        )

        try:
            with patch("backend.routers.asr.get_store", return_value=_fake_store(task)), patch(
                "backend.routers.asr.run_asr_analyze",
                new=AsyncMock(side_effect=self._wait_forever),
            ), patch.object(manager, "broadcast", AsyncMock()):
                result = await asr_router.trigger_asr_analyze(task.id)

            self.assertTrue(result["success"])
            self.assertEqual(task.status, TaskStatus.ANALYZE_RUNNING)

            runner = core_runner._task_runners[task.id]
            await asyncio.sleep(0)
            runner.cancel()
            try:
                await runner
            except asyncio.CancelledError:
                pass
        finally:
            os.unlink(video_path)

    async def test_run_analyze_appends_analysis_history_without_overwriting_asr_history(self):
        task = _make_task(TaskStatus.ANALYZE_RUNNING)
        task.asr_result = ASRResult(
            words=[WordTimestamp(word="测", start=0.0, end=0.2)],
            duration=1.0,
        )
        task.asr_history = [
            ASRSnapshot(
                version=1,
                created_at="2026-01-01T00:00:00",
                engine="FunASR Paraformer-zh",
                segments_count=0,
                duration=1.0,
                asr_result=task.asr_result.model_copy(deep=True),
            )
        ]
        task.asr_current_version = 1
        analyzed = ASRResult(
            words=[WordTimestamp(word="测", start=0.0, end=0.2)],
            segments=[Segment(start=0.0, end=0.2, text="测")],
            duration=1.0,
            analysis_report={
                "generated_at": "2026-01-01T00:01:00",
                "settings": {"silence_threshold": 0.3},
                "stats": {"segments_final": 1, "candidate_edits": 0},
                "steps": [],
                "segment_notes": [],
            },
        )

        with patch("backend.routers.asr.get_store", return_value=_fake_store(task)), patch(
            "backend.routers.asr.run_asr_analyze",
            new=AsyncMock(return_value=analyzed),
        ), patch.object(manager, "broadcast", AsyncMock()), patch(
            "backend.routers.asr.log_to_ws",
            new=AsyncMock(),
        ):
            await asr_router._run_asr_analyze_task(task.id)

        self.assertEqual(task.status, TaskStatus.ASR_DONE)
        self.assertEqual(len(task.analysis_history), 1)
        self.assertEqual(task.analysis_history[0].action, "text_organize")
        self.assertEqual(task.analysis_history[0].segments_count, 1)
        self.assertEqual(task.audit_segments[0].text, "测")
        self.assertEqual(task.asr_history[0].segments_count, 0)

    async def test_resegment_save_appends_history_and_invalidates_downstream_results(self):
        task = _make_task(TaskStatus.REVIEW)
        task.asr_result = ASRResult(
            words=[
                WordTimestamp(word="你", start=0.0, end=0.1),
                WordTimestamp(word="好", start=0.12, end=0.22),
                WordTimestamp(word="呀", start=0.8, end=0.9),
            ],
            segments=[Segment(start=0.0, end=0.9, text="你好呀")],
            duration=1.0,
            analysis_report={
                "generated_at": "2026-01-01T00:01:00",
                "settings": {"silence_threshold": 0.3},
                "stats": {"segments_final": 1, "candidate_edits": 0},
                "steps": [],
                "segment_notes": [],
            },
        )
        task.audit_segments = [
            Segment(start=0.0, end=0.9, text="你好呀", action=SegmentAction.DELETE),
        ]
        task.audit_history = [
            AuditSnapshot(
                version=1,
                created_at="2026-01-01T00:02:00",
                model="规则引擎",
                segments_count=1,
                segments_kept=0,
                segments_deleted=1,
            )
        ]
        task.audit_current_version = 1
        task.highlight_reels = [HighlightReel(clip_group=1, title="旧集锦")]
        task.export_path = "/tmp/old.mp4"
        task.jianying_draft_path = "/tmp/draft"

        with patch("backend.routers.asr.get_store", return_value=_fake_store(task)):
            result = await asr_router.resegment_segments(
                task.id,
                asr_router.ResegmentRequest(
                    silence_threshold=0.2,
                    save=True,
                    inherit_mode="none",
                ),
            )

        self.assertTrue(result["success"])
        self.assertEqual(task.status, TaskStatus.ASR_DONE)
        self.assertEqual(task.audit_history, [])
        self.assertEqual(task.highlight_reels, [])
        self.assertIsNone(task.export_path)
        self.assertIsNone(task.jianying_draft_path)
        self.assertEqual(task.params.silence_threshold, 0.2)
        self.assertEqual(len(task.analysis_history), 1)
        self.assertEqual(task.analysis_history[0].action, "resegment")
        self.assertEqual(task.asr_result.analysis_report["source"], "resegment")
        self.assertEqual(result["inheritance"]["mode"], "none")

    async def test_resegment_aggressive_inherits_audit_decisions(self):
        task = _make_task(TaskStatus.REVIEW)
        words = [
            WordTimestamp(word="你", start=0.0, end=0.1),
            WordTimestamp(word="好", start=0.12, end=0.22),
            WordTimestamp(word="走", start=0.8, end=0.9),
        ]
        task.asr_result = ASRResult(
            words=words,
            segments=[Segment(start=0.0, end=0.9, text="你好走", words=words)],
            duration=1.0,
        )
        task.audit_segments = [
            Segment(
                id="old-audit",
                start=0.0,
                end=0.9,
                text="你好走",
                words=words,
                action=SegmentAction.DELETE,
                claude_action=SegmentAction.DELETE,
                claude_reason="旧审计决定",
            ),
        ]
        task.audit_history = [
            AuditSnapshot(
                version=1,
                created_at="2026-01-01T00:02:00",
                model="模型",
                segments_count=1,
                segments_kept=0,
                segments_deleted=1,
            ),
        ]
        task.audit_current_version = 1

        with patch("backend.routers.asr.get_store", return_value=_fake_store(task)):
            result = await asr_router.resegment_segments(
                task.id,
                asr_router.ResegmentRequest(
                    silence_threshold=0.2,
                    save=True,
                    inherit_mode="aggressive",
                    expected_content_revision=0,
                ),
            )

        self.assertEqual(task.status, TaskStatus.REVIEW)
        self.assertEqual([segment.action for segment in task.audit_segments], [
            SegmentAction.DELETE,
            SegmentAction.DELETE,
        ])
        self.assertEqual(len(task.audit_history), 1)
        self.assertEqual(result["inheritance"]["inherited_audit_count"], 2)
        self.assertEqual(result["inheritance"]["review_segment_count"], 2)
        self.assertEqual(task.resegment_inheritance_report.inherited_audit_count, 2)

    async def test_resegment_save_rejects_stale_content_revision(self):
        task = _make_task(TaskStatus.REVIEW)
        task.content_revision = 3
        task.asr_result = ASRResult(
            words=[WordTimestamp(word="测", start=0.0, end=0.2)],
            segments=[Segment(start=0.0, end=0.2, text="测")],
            duration=1.0,
        )

        with patch("backend.routers.asr.get_store", return_value=_fake_store(task)):
            with self.assertRaisesRegex(Exception, "内容已在预览后发生变化"):
                await asr_router.resegment_segments(
                    task.id,
                    asr_router.ResegmentRequest(
                        silence_threshold=0.2,
                        save=True,
                        inherit_mode="aggressive",
                        expected_content_revision=2,
                    ),
                )

    async def test_trigger_diarization_allows_rerun_from_review(self):
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp.write(b"fake")
            video_path = tmp.name

        task = _make_task(TaskStatus.REVIEW, video_path=video_path)
        task.asr_result = ASRResult(
            words=[WordTimestamp(word="<STU>测", start=0.0, end=0.2)],
            duration=1.0,
            speakers=["spk0"],
            diar_segments=[{"start": 0.0, "end": 1.0, "speaker": "spk0"}],
        )

        try:
            with patch("backend.routers.asr.get_store", return_value=_fake_store(task)), patch(
                "backend.routers.asr.run_asr_diarize",
                new=AsyncMock(side_effect=self._wait_forever),
            ), patch.object(manager, "broadcast", AsyncMock()):
                result = await asr_router.trigger_asr_diarization(task.id)

            self.assertTrue(result["success"])
            self.assertEqual(task.status, TaskStatus.DIARIZATION_RUNNING)

            runner = core_runner._task_runners[task.id]
            await asyncio.sleep(0)
            runner.cancel()
            try:
                await runner
            except asyncio.CancelledError:
                pass
        finally:
            os.unlink(video_path)

    async def test_run_diarization_auto_runs_text_organize_on_success(self):
        task = _make_task(TaskStatus.DIARIZATION_RUNNING)
        task.asr_result = _recognized_result()
        diarized = ASRResult(
            words=[WordTimestamp(word="访", start=0.0, end=0.2, speaker="spk0")],
            speakers=["spk0"],
            diar_segments=[{"start": 0.0, "end": 1.0, "speaker": "spk0"}],
            duration=1.0,
        )

        with patch("backend.routers.asr.get_store", return_value=_fake_store(task)), patch(
            "backend.routers.asr.run_asr_diarize",
            new=AsyncMock(return_value=diarized),
        ) as diarize_mock, patch(
            "backend.routers.asr.run_asr_analyze",
            new=AsyncMock(return_value=_organized_result("访")),
        ) as analyze_mock, patch.object(manager, "broadcast", AsyncMock()), patch(
            "backend.routers.asr.log_to_ws",
            new=AsyncMock(),
        ):
            await asr_router._run_asr_diarization_task(task.id)

        diarize_mock.assert_awaited_once()
        analyze_mock.assert_awaited_once()
        self.assertEqual(task.status, TaskStatus.ASR_DONE)
        self.assertTrue(task.params.enable_diarization)
        self.assertEqual(len(task.analysis_history), 1)
        self.assertEqual(task.audit_segments[0].text, "访")

    async def test_trigger_asr_when_already_running_is_idempotent(self):
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp.write(b"fake")
            video_path = tmp.name

        task = _make_task(TaskStatus.ASR_RUNNING, video_path=video_path)
        broadcast = AsyncMock()
        start_runner = AsyncMock(return_value=asyncio.create_task(asyncio.sleep(0)))

        try:
            with patch("backend.routers.asr.get_store", return_value=_fake_store(task)), patch(
                "backend.routers.asr._start_task_runner",
                start_runner,
            ), patch.object(manager, "broadcast", broadcast):
                result = await asr_router.trigger_asr(task.id)

            self.assertTrue(result["success"])
            self.assertTrue(result.get("already_running"))
            start_runner.assert_not_called()
            broadcast.assert_not_called()
        finally:
            os.unlink(video_path)


if __name__ == "__main__":
    unittest.main()
