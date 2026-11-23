"""
POST /api/tasks/{task_id}/cancel 统一取消接口测试。

覆盖：
- ASR 运行中取消 → 回滚 pending
- 审计运行中取消 → 回滚 asr_done
- 注册表无协程时的兜底回滚（翻译流水线）
- 非运行状态取消返回 400
- 重复触发时新协程替换旧协程（旧协程被取消且不回滚状态）
- export_running 委托给现有导出取消逻辑
"""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from backend.core import task_runner as core_runner
from backend.core.ws import manager
from backend.routers import asr as asr_router
from backend.routers import audit as audit_router
from backend.routers import export as export_router
from backend.routers import highlight_reels as highlight_reels_router
from backend.models.task import (
    ASRResult,
    ASRSnapshot,
    AuditRequest,
    ExecutionRun,
    HighlightReelBuildOptions,
    Segment,
    SegmentAction,
    Task,
    TaskStatus,
    TaskType,
    WordTimestamp,
)


def _make_task(status: TaskStatus) -> Task:
    return Task(
        id="task-cancel",
        name="cancel-test",
        task_type=TaskType.MONOLOGUE_CLEAN,
        status=status,
        video_path="/tmp/source.mp4",
        audit_segments=[
            Segment(id="seg_1", start=0.0, end=2.0, text="测试", action=SegmentAction.KEEP),
        ],
    )


def _fake_store(task: Task) -> SimpleNamespace:
    return SimpleNamespace(
        get=lambda task_id: task if task_id == task.id else None,
        update=lambda t: None,
    )


class CancelTaskEndpointTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        core_runner._task_runners.clear()
        core_runner._task_runner_statuses.clear()
        core_runner._export_tasks.clear()

    async def _wait_forever(self, *args, **kwargs):
        await asyncio.Event().wait()

    async def test_highlight_execution_resolves_review_rollback(self):
        task = _make_task(TaskStatus.AUDIT_RUNNING)
        run = ExecutionRun(operation="highlight_rebuild")
        task.execution_runs = [run]
        task.active_execution_run_id = run.run_id

        self.assertEqual(
            core_runner._resolve_cancel_rollback_status(task),
            TaskStatus.REVIEW,
        )

    async def test_cancel_asr_rolls_back_to_pending(self):
        task = _make_task(TaskStatus.PENDING)
        broadcast = AsyncMock()

        with patch("backend.routers.asr.get_store", return_value=_fake_store(task)), patch(
            "backend.routers.export.get_store", return_value=_fake_store(task)
        ), patch(
            "backend.core.task_runner.get_store", return_value=_fake_store(task)
        ), patch(
            "backend.routers.asr.run_asr_recognize",
            new=AsyncMock(side_effect=self._wait_forever),
        ), patch.object(manager, "broadcast", broadcast):
            runner = core_runner._start_task_runner(task.id, asr_router._run_asr_task(task.id, "base", "whisper"))
            await asyncio.sleep(0)  # 让协程启动并写入 asr_running
            self.assertEqual(task.status, TaskStatus.ASR_RUNNING)

            result = await export_router.cancel_task(task.id)
            await runner  # 协程内部吞掉 CancelledError 后正常结束

        self.assertTrue(result["success"])
        self.assertEqual(task.status, TaskStatus.PENDING)
        self.assertNotIn(task.id, core_runner._task_runners)
        statuses = [
            call.args[1].get("status")
            for call in broadcast.await_args_list
            if call.args[1].get("type") == "status_change"
        ]
        self.assertIn(TaskStatus.PENDING, statuses)

    async def test_cancel_asr_rerun_restores_previous_version(self):
        task = _make_task(TaskStatus.ASR_RECOGNIZED)
        previous_result = ASRResult(
            words=[WordTimestamp(word="旧", start=0.0, end=0.2)],
            duration=1.0,
        )
        task.asr_result = previous_result
        task.asr_history = [
            ASRSnapshot(
                version=2,
                created_at="2026-07-27T10:00:00",
                engine="FunASR Paraformer-zh",
                segments_count=0,
                duration=1.0,
                asr_result=previous_result.model_copy(deep=True),
            )
        ]
        task.asr_current_version = 2
        broadcast = AsyncMock()

        with patch("backend.routers.asr.get_store", return_value=_fake_store(task)), patch(
            "backend.routers.export.get_store", return_value=_fake_store(task)
        ), patch(
            "backend.core.task_runner.get_store", return_value=_fake_store(task)
        ), patch(
            "backend.routers.asr.run_asr_recognize",
            new=AsyncMock(side_effect=self._wait_forever),
        ), patch.object(manager, "broadcast", broadcast):
            runner = core_runner._start_task_runner(
                task.id,
                asr_router._run_asr_task(task.id, "base", "whisper"),
                TaskStatus.ASR_RUNNING,
            )
            await asyncio.sleep(0)
            self.assertEqual(task.status, TaskStatus.ASR_RUNNING)

            result = await export_router.cancel_task(task.id)
            await runner

        self.assertTrue(result["success"])
        self.assertEqual(task.status, TaskStatus.ASR_RECOGNIZED)
        self.assertEqual(task.asr_current_version, 2)
        self.assertEqual([snapshot.version for snapshot in task.asr_history], [2])

    async def test_cancel_analyze_rolls_back_to_asr_recognized(self):
        task = _make_task(TaskStatus.ASR_RECOGNIZED)
        task.asr_result = ASRResult(
            words=[WordTimestamp(word="测", start=0.0, end=0.2)],
            duration=1.0,
        )
        broadcast = AsyncMock()

        with patch("backend.routers.asr.get_store", return_value=_fake_store(task)), patch(
            "backend.routers.export.get_store", return_value=_fake_store(task)
        ), patch(
            "backend.core.task_runner.get_store", return_value=_fake_store(task)
        ), patch(
            "backend.routers.asr.run_asr_analyze",
            new=AsyncMock(side_effect=self._wait_forever),
        ), patch.object(manager, "broadcast", broadcast):
            runner = core_runner._start_task_runner(
                task.id,
                asr_router._run_asr_analyze_task(task.id),
                TaskStatus.ANALYZE_RUNNING,
            )
            await asyncio.sleep(0)
            self.assertEqual(task.status, TaskStatus.ANALYZE_RUNNING)

            result = await export_router.cancel_task(task.id)
            await runner

        self.assertTrue(result["success"])
        self.assertEqual(task.status, TaskStatus.ASR_RECOGNIZED)
        self.assertNotIn(task.id, core_runner._task_runners)

    async def test_cancel_analyze_with_diarization_rolls_back_to_asr_diarized(self):
        task = _make_task(TaskStatus.ASR_DIARIZED)
        task.asr_result = ASRResult(
            words=[WordTimestamp(word="测", start=0.0, end=0.2, speaker="spk0")],
            duration=1.0,
            speakers=["spk0"],
            diar_segments=[{"start": 0.0, "end": 1.0, "speaker": "spk0"}],
        )
        broadcast = AsyncMock()

        with patch("backend.routers.asr.get_store", return_value=_fake_store(task)), patch(
            "backend.routers.export.get_store", return_value=_fake_store(task)
        ), patch(
            "backend.core.task_runner.get_store", return_value=_fake_store(task)
        ), patch(
            "backend.routers.asr.run_asr_analyze",
            new=AsyncMock(side_effect=self._wait_forever),
        ), patch.object(manager, "broadcast", broadcast):
            runner = core_runner._start_task_runner(
                task.id,
                asr_router._run_asr_analyze_task(task.id),
                TaskStatus.ANALYZE_RUNNING,
            )
            await asyncio.sleep(0)
            self.assertEqual(task.status, TaskStatus.ANALYZE_RUNNING)

            result = await export_router.cancel_task(task.id)
            await runner

        self.assertTrue(result["success"])
        self.assertEqual(task.status, TaskStatus.ASR_DIARIZED)
        self.assertNotIn(task.id, core_runner._task_runners)

    async def test_cancel_audit_rolls_back_to_asr_done(self):
        task = _make_task(TaskStatus.ASR_DONE)
        broadcast = AsyncMock()
        req = AuditRequest(force_rule_engine=True)

        with patch("backend.routers.audit.get_store", return_value=_fake_store(task)), patch(
            "backend.routers.export.get_store", return_value=_fake_store(task)
        ), patch(
            "backend.core.task_runner.get_store", return_value=_fake_store(task)
        ), patch(
            "backend.routers.audit.run_semantic_audit",
            new=AsyncMock(side_effect=self._wait_forever),
        ), patch.object(manager, "broadcast", broadcast):
            runner = core_runner._start_task_runner(task.id, audit_router._run_audit_task(task.id, req))
            await asyncio.sleep(0)
            self.assertEqual(task.status, TaskStatus.AUDIT_RUNNING)

            result = await export_router.cancel_task(task.id)
            await runner

        self.assertTrue(result["success"])
        self.assertEqual(task.status, TaskStatus.ASR_DONE)
        self.assertNotIn(task.id, core_runner._task_runners)

    async def test_cancel_highlight_rebuild_rolls_back_to_review(self):
        task = _make_task(TaskStatus.REVIEW)
        task.task_type = TaskType.HIGHLIGHT_REEL
        store = _fake_store(task)
        broadcast = AsyncMock()

        with patch("backend.routers.highlight_reels.get_store", return_value=store), patch(
            "backend.routers.export.get_store", return_value=store
        ), patch(
            "backend.core.task_runner.get_store", return_value=store
        ), patch(
            "backend.routers.highlight_reels._refresh_highlight_plan",
            new=AsyncMock(side_effect=self._wait_forever),
        ), patch.object(manager, "broadcast", broadcast):
            response = await highlight_reels_router.rebuild_highlight_reels(
                task.id,
                HighlightReelBuildOptions(),
            )
            runner = core_runner._task_runners[task.id]
            await asyncio.sleep(0)

            self.assertTrue(response["success"])
            self.assertEqual(task.status, TaskStatus.AUDIT_RUNNING)

            result = await export_router.cancel_task(task.id)
            await runner

        self.assertTrue(result["success"])
        self.assertEqual(task.status, TaskStatus.REVIEW)
        self.assertNotIn(task.id, core_runner._task_runners)
        types = [call.args[1].get("type") for call in broadcast.await_args_list]
        self.assertIn("task_cancelled", types)
        self.assertIn("status_change", types)

    async def test_cancel_translate_without_runner_falls_back_to_direct_rollback(self):
        # 注册表无协程（竞态/重启场景）：接口直接回滚状态并广播
        task = _make_task(TaskStatus.TRANSLATE_RUNNING)
        broadcast = AsyncMock()

        with patch("backend.routers.export.get_store", return_value=_fake_store(task)), patch(
            "backend.core.task_runner.get_store", return_value=_fake_store(task)
        ), patch.object(
            manager, "broadcast", broadcast
        ):
            result = await export_router.cancel_task(task.id)

        self.assertTrue(result["success"])
        self.assertEqual(task.status, TaskStatus.PENDING)
        types = [call.args[1].get("type") for call in broadcast.await_args_list]
        self.assertIn("task_cancelled", types)
        self.assertIn("status_change", types)

    async def test_cancel_non_running_status_returns_400(self):
        task = _make_task(TaskStatus.REVIEW)

        with patch("backend.routers.export.get_store", return_value=_fake_store(task)):
            with self.assertRaises(HTTPException) as ctx:
                await export_router.cancel_task(task.id)

        self.assertEqual(ctx.exception.status_code, 400)

    async def test_cancel_missing_task_returns_404(self):
        with patch(
            "backend.routers.export.get_store",
            return_value=SimpleNamespace(get=lambda task_id: None),
        ):
            with self.assertRaises(HTTPException) as ctx:
                await export_router.cancel_task("no-such-task")

        self.assertEqual(ctx.exception.status_code, 404)

    async def test_retrigger_replaces_previous_runner_without_rollback(self):
        # 同一任务重复触发 ASR：旧协程被取消，且因已被替换而跳过状态回滚
        task = _make_task(TaskStatus.PENDING)
        broadcast = AsyncMock()

        with patch("backend.routers.asr.get_store", return_value=_fake_store(task)), patch(
            "backend.routers.asr.run_asr_recognize",
            new=AsyncMock(side_effect=self._wait_forever),
        ), patch.object(manager, "broadcast", broadcast):
            old_runner = core_runner._start_task_runner(task.id, asr_router._run_asr_task(task.id, "base", "whisper"))
            await asyncio.sleep(0)

            new_runner = core_runner._start_task_runner(task.id, asr_router._run_asr_task(task.id, "large", "whisper"))
            await old_runner  # 旧协程被取消后正常结束
            await asyncio.sleep(0)

            self.assertIs(core_runner._task_runners.get(task.id), new_runner)
            # 旧协程跳过回滚，状态保持新协程写入的 asr_running
            self.assertEqual(task.status, TaskStatus.ASR_RUNNING)

            new_runner.cancel()
            await new_runner

    async def test_cancel_export_running_delegates_to_export_cancel(self):
        task = _make_task(TaskStatus.EXPORT_RUNNING)

        async def dummy_export():
            await asyncio.Event().wait()

        export_task = asyncio.get_event_loop().create_task(dummy_export())
        core_runner._export_tasks[task.id] = export_task

        with patch("backend.routers.export.get_store", return_value=_fake_store(task)), patch(
            "backend.routers.export.kill_export", return_value=False
        ):
            result = await export_router.cancel_task(task.id)
            await asyncio.sleep(0)

        self.assertTrue(result["success"])
        self.assertNotIn(task.id, core_runner._export_tasks)
        self.assertTrue(export_task.cancelled())


if __name__ == "__main__":
    unittest.main()
