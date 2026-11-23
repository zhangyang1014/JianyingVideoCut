import unittest
import json
from unittest.mock import patch

from backend.routers import learning as learning_router
from backend.routers import tasks as tasks_router
from backend.models.task import (
    AnalysisSnapshot,
    ASRResult,
    ASRSnapshot,
    AuditSnapshot,
    LearningTask,
    Segment,
    Task,
    TaskParams,
    WordTimestamp,
)


class FakeStore:
    def __init__(self, items):
        self._items = items

    def get_all(self):
        return self._items


class TaskListPayloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_tasks_omits_heavy_detail_fields(self):
        asr_result = ASRResult(
            words=[WordTimestamp(word="你", start=0.0, end=0.2)],
            segments=[Segment(start=0.0, end=1.0, text="你好")],
            tagged_script="你好",
            duration=1.0,
        )
        task = Task(
            name="示例任务",
            asr_result=asr_result,
            asr_history=[
                ASRSnapshot(
                    version=1,
                    created_at="2026-04-09T00:00:00",
                    engine="test",
                    segments_count=1,
                    duration=1.0,
                    asr_result=asr_result,
                )
            ],
            audit_segments=[Segment(start=0.0, end=1.0, text="你好")],
            audit_history=[
                AuditSnapshot(
                    version=1,
                    created_at="2026-04-09T00:00:00",
                    model="test",
                    segments_count=1,
                    segments_kept=1,
                    segments_deleted=0,
                )
            ],
            params=TaskParams(),
            golden_quote_order=["seg-1"],
            analysis_history=[
                AnalysisSnapshot(
                    version=1,
                    created_at="2026-04-09T00:00:00",
                    segments_count=1,
                    segments=[Segment(start=0.0, end=1.0, text="很长的分析历史")],
                    analysis_report={"detail": "x" * 100_000},
                )
            ],
        )

        with patch("backend.routers.tasks.get_store", return_value=FakeStore([task])):
            payload = await tasks_router.list_tasks()

        self.assertEqual(payload[0]["name"], "示例任务")
        for field in (
            "asr_result",
            "asr_history",
            "analysis_history",
            "audit_segments",
            "audit_history",
            "params",
            "golden_quote_order",
        ):
            self.assertNotIn(field, payload[0])
        self.assertLess(
            len(json.dumps(payload, ensure_ascii=False)),
            10_000,
            "任务列表必须保持为元数据响应，不能随任务产物体积增长",
        )

    async def test_list_learning_tasks_omits_heavy_detail_fields(self):
        asr_result = ASRResult(
            words=[WordTimestamp(word="学", start=0.0, end=0.2)],
            segments=[Segment(start=0.0, end=1.0, text="学习")],
            tagged_script="学习",
            duration=1.0,
        )
        task = LearningTask(
            name="课程 1",
            original_asr_result=asr_result,
            edited_asr_result=asr_result,
            analysis_result="很长的分析报告",
            new_prompt_content="很长的新提示词全文",
            new_prompt_path="/tmp/prompt.md",
        )

        with patch("backend.routers.learning.get_learning_store", return_value=FakeStore([task])):
            payload = await learning_router.list_learning_tasks()

        self.assertEqual(payload[0]["name"], "课程 1")
        for field in (
            "original_asr_result",
            "edited_asr_result",
            "analysis_result",
            "new_prompt_content",
            "new_prompt_path",
        ):
            self.assertNotIn(field, payload[0])


if __name__ == "__main__":
    unittest.main()
