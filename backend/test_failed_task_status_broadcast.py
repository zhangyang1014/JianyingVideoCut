import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from backend.core.ws import manager
from backend.models.task import AuditRequest, HighlightReelAutoRunRequest, Segment, SegmentAction, Task, TaskStatus, TaskType
from backend.routers import audit as audit_router
from backend.routers import highlight_reels as highlight_router
from backend.services.audit_pipeline import AuditPipelineError


def _fake_store(task: Task) -> SimpleNamespace:
    return SimpleNamespace(
        get=lambda task_id: task if task_id == task.id else None,
        update=lambda t: None,
    )


def _make_task(task_type: TaskType = TaskType.HIGHLIGHT_REEL) -> Task:
    return Task(
        id="task-failed-status",
        name="failed-status-test",
        task_type=task_type,
        status=TaskStatus.AUDIT_RUNNING,
        video_path="/tmp/source.mp4",
        audit_segments=[
            Segment(id="seg_1", start=0.0, end=2.0, text="测试", action=SegmentAction.KEEP),
        ],
    )


class FailedTaskStatusBroadcastTests(unittest.IsolatedAsyncioTestCase):
    async def test_plain_audit_route_does_not_auto_build_highlight_reels(self):
        task = _make_task(TaskType.HIGHLIGHT_REEL)
        task.status = TaskStatus.REVIEW
        broadcast = AsyncMock()
        run_pipeline = AsyncMock(return_value=SimpleNamespace(reels_count=0, quality_passed=None))

        with patch("backend.routers.audit.get_store", return_value=_fake_store(task)), patch(
            "backend.routers.audit.run_audit_pipeline",
            new=run_pipeline,
        ), patch(
            "backend.routers.audit.log_to_ws",
            new=AsyncMock(),
        ), patch.object(manager, "broadcast", broadcast):
            await audit_router._run_audit_task(task.id, AuditRequest())

        run_pipeline.assert_awaited_once()
        self.assertIs(run_pipeline.await_args.kwargs["include_highlight_plan"], False)
        payloads = [call.args[1] for call in broadcast.await_args_list]
        self.assertFalse(any(payload.get("type") == "highlight_reels_ready" for payload in payloads))

    async def test_highlight_auto_pipeline_failure_broadcasts_status_and_fallback_event(self):
        task = _make_task()
        broadcast = AsyncMock()
        failed = AuditPipelineError(
            "模型输出无法解析",
            error_type="contract_failed",
            runtime_model="qwen/qwen3.7-plus",
            allow_rule_engine_fallback=True,
        )

        with patch("backend.routers.highlight_reels.get_store", return_value=_fake_store(task)), patch(
            "backend.routers.highlight_reels.run_audit_pipeline",
            new=AsyncMock(side_effect=failed),
        ), patch(
            "backend.routers.highlight_reels.log_to_ws",
            new=AsyncMock(),
        ), patch.object(manager, "broadcast", broadcast):
            await highlight_router._run_highlight_auto_pipeline(task.id, HighlightReelAutoRunRequest())

        self.assertEqual(task.status, TaskStatus.ASR_DONE)
        payloads = [call.args[1] for call in broadcast.await_args_list]
        self.assertIn(
            {"type": "status_change", "task_id": task.id, "status": TaskStatus.ASR_DONE},
            payloads,
        )
        self.assertTrue(any(payload.get("type") == "claude_failed" for payload in payloads))

    async def test_audit_pipeline_failure_broadcasts_error_status(self):
        task = _make_task(TaskType.MONOLOGUE_CLEAN)
        broadcast = AsyncMock()
        failed = AuditPipelineError(
            "模型调用失败",
            error_type="model_call_failed",
            runtime_model="qwen/qwen3.7-plus",
            allow_rule_engine_fallback=False,
        )

        with patch("backend.routers.audit.get_store", return_value=_fake_store(task)), patch(
            "backend.routers.audit.run_audit_pipeline",
            new=AsyncMock(side_effect=failed),
        ), patch(
            "backend.routers.audit.log_to_ws",
            new=AsyncMock(),
        ), patch.object(manager, "broadcast", broadcast):
            await audit_router._run_audit_task(task.id, AuditRequest())

        self.assertEqual(task.status, TaskStatus.ERROR)
        payloads = [call.args[1] for call in broadcast.await_args_list]
        self.assertIn(
            {"type": "status_change", "task_id": task.id, "status": TaskStatus.ERROR},
            payloads,
        )


if __name__ == "__main__":
    unittest.main()
