"""
GET /api/tasks/{task_id}/runtime-health 运行态自检测试。
"""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from backend.core import task_runner as core_runner
from backend.models.task import Task, TaskStatus, TaskType
from backend.routers import learning as learning_router
from backend.routers import tasks as tasks_router
from backend.services import ffmpeg_executor


def _make_task(status: TaskStatus) -> Task:
    return Task(
        id="task-health",
        name="health-test",
        task_type=TaskType.MONOLOGUE_CLEAN,
        status=status,
        video_path="/tmp/source.mp4",
    )


def _fake_store(task: Task) -> SimpleNamespace:
    return SimpleNamespace(
        get=lambda task_id: task if task_id == task.id else None,
    )


class TaskRuntimeHealthHelperTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        core_runner._task_runners.clear()
        core_runner._task_runner_statuses.clear()
        core_runner._export_tasks.clear()
        core_runner._learning_active_tasks.clear()
        ffmpeg_executor._active_processes.clear()

    async def test_non_running_status_reports_not_expected(self):
        health = core_runner.get_task_runtime_health("task-health", TaskStatus.REVIEW)
        self.assertFalse(health["expected_running"])
        self.assertFalse(health["alive"])
        self.assertFalse(health["runner_alive"])
        self.assertFalse(health["export_task_alive"])
        self.assertIn("无运行中", health["message"])

    async def test_asr_running_with_runner_alive(self):
        async def _noop():
            await asyncio.Event().wait()

        runner = asyncio.create_task(_noop())
        core_runner._task_runners["task-health"] = runner

        health = core_runner.get_task_runtime_health("task-health", TaskStatus.ASR_RUNNING)
        self.assertTrue(health["expected_running"])
        self.assertTrue(health["alive"])
        self.assertTrue(health["runner_alive"])
        self.assertIn("自检通过", health["message"])

        runner.cancel()

    async def test_pending_status_with_asr_runner_reports_effective_running(self):
        async def _noop():
            await asyncio.Event().wait()

        runner = asyncio.create_task(_noop())
        core_runner._task_runners["task-health"] = runner
        core_runner._task_runner_statuses["task-health"] = TaskStatus.ASR_RUNNING

        health = core_runner.get_task_runtime_health("task-health", TaskStatus.PENDING)
        self.assertTrue(health["expected_running"])
        self.assertTrue(health["alive"])
        self.assertEqual(health["status"], TaskStatus.PENDING)
        self.assertEqual(health["effective_status"], TaskStatus.ASR_RUNNING)

        runner.cancel()

    async def test_asr_running_without_runner_is_stale(self):
        health = core_runner.get_task_runtime_health("task-health", TaskStatus.ASR_RUNNING)
        self.assertTrue(health["expected_running"])
        self.assertFalse(health["alive"])
        self.assertIn("疑似中断", health["message"])

    async def test_export_running_with_export_task_alive(self):
        async def _noop():
            await asyncio.Event().wait()

        runner = asyncio.create_task(_noop())
        core_runner._export_tasks["task-health"] = runner

        health = core_runner.get_task_runtime_health("task-health", TaskStatus.EXPORT_RUNNING)
        self.assertTrue(health["expected_running"])
        self.assertTrue(health["alive"])
        self.assertTrue(health["export_task_alive"])

        runner.cancel()

    async def test_export_running_with_ffmpeg_process_alive(self):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        ffmpeg_executor._active_processes["task-health"] = mock_proc

        health = core_runner.get_task_runtime_health("task-health", TaskStatus.EXPORT_RUNNING)
        self.assertTrue(health["ffmpeg_process_alive"])
        self.assertTrue(health["alive"])

    async def test_shutdown_cancels_and_clears_all_managed_tasks(self):
        async def _noop():
            await asyncio.Event().wait()

        runner = asyncio.create_task(_noop())
        export = asyncio.create_task(_noop())
        learning = asyncio.create_task(_noop())
        core_runner._task_runners["task-health"] = runner
        core_runner._export_tasks["export-health"] = export
        core_runner._learning_active_tasks["learning-health"] = learning

        await core_runner.shutdown_background_tasks(timeout=1.0)

        self.assertTrue(runner.cancelled())
        self.assertTrue(export.cancelled())
        self.assertTrue(learning.cancelled())
        self.assertEqual(core_runner._task_runners, {})
        self.assertEqual(core_runner._export_tasks, {})
        self.assertEqual(core_runner._learning_active_tasks, {})

    async def test_old_learning_callback_does_not_remove_replacement(self):
        async def _finish_immediately():
            return None

        async def _wait_forever():
            await asyncio.Event().wait()

        old = asyncio.create_task(_finish_immediately())
        learning_router._track_learning_task("learning-health", old)
        replacement = asyncio.create_task(_wait_forever())
        learning_router._track_learning_task("learning-health", replacement)

        await old
        await asyncio.sleep(0)

        self.assertIs(
            core_runner._learning_active_tasks["learning-health"],
            replacement,
        )
        replacement.cancel()
        await asyncio.gather(replacement, return_exceptions=True)


class TaskRuntimeHealthEndpointTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        core_runner._task_runners.clear()
        core_runner._task_runner_statuses.clear()
        core_runner._export_tasks.clear()
        core_runner._learning_active_tasks.clear()

    async def test_endpoint_returns_health_payload(self):
        task = _make_task(TaskStatus.ASR_RUNNING)

        async def _noop():
            await asyncio.Event().wait()

        runner = asyncio.create_task(_noop())
        core_runner._task_runners[task.id] = runner

        with patch("backend.routers.tasks.get_store", return_value=_fake_store(task)):
            payload = await tasks_router.get_runtime_health(task.id)

        self.assertEqual(payload["task_id"], task.id)
        self.assertTrue(payload["alive"])
        runner.cancel()

    async def test_endpoint_missing_task_returns_404(self):
        with patch(
            "backend.routers.tasks.get_store",
            return_value=SimpleNamespace(get=lambda task_id: None),
        ):
            with self.assertRaises(HTTPException) as ctx:
                await tasks_router.get_runtime_health("missing")

        self.assertEqual(ctx.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
