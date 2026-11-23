import unittest
from types import SimpleNamespace

from backend.models.task import (
    ASRResult,
    ASRSnapshot,
    AuditRequest,
    AuditSnapshot,
    Segment,
    SegmentAction,
    Task,
)
from backend.routers.audit import _reset_prior_audit_results
from backend.services.audit_pipeline import AuditPipelineError, run_audit_pipeline


class AuditResetTest(unittest.TestCase):
    def test_reset_prior_audit_preserves_current_asr_version(self):
        v1_segment = Segment(id="v1_seg", start=0, end=1, text="旧 ASR")
        v2_segment = Segment(id="v2_seg", start=0, end=1, text="当前 ASR")
        task = Task(name="测试任务")
        task.asr_history = [
            ASRSnapshot(
                version=1,
                created_at="2026-07-27T00:00:00",
                engine="v1",
                segments_count=1,
                duration=1,
                asr_result=ASRResult(segments=[v1_segment], duration=1),
            ),
            ASRSnapshot(
                version=2,
                created_at="2026-07-27T00:01:00",
                engine="v2",
                segments_count=1,
                duration=1,
                asr_result=ASRResult(segments=[v2_segment], duration=1),
            ),
        ]
        task.asr_current_version = 2
        task.audit_segments = [
            Segment(
                id="edited_seg",
                start=0,
                end=1,
                text="手动编辑",
                action=SegmentAction.DELETE,
                reason="旧审计",
                user_override=True,
                claude_action=SegmentAction.DELETE,
                clip_group=3,
            )
        ]
        task.audit_history = [
            AuditSnapshot(
                version=1,
                created_at="2026-07-27T00:02:00",
                model="test",
                segments_count=1,
                segments_kept=0,
                segments_deleted=1,
            )
        ]
        task.audit_current_version = 1

        _reset_prior_audit_results(task)

        self.assertEqual(task.asr_current_version, 2)
        self.assertEqual([segment.text for segment in task.audit_segments], ["当前 ASR"])
        self.assertEqual(task.audit_segments[0].action, SegmentAction.KEEP)
        self.assertFalse(task.audit_segments[0].user_override)
        self.assertIsNone(task.audit_segments[0].claude_action)
        self.assertIsNone(task.audit_segments[0].clip_group)
        self.assertEqual(task.audit_history, [])
        self.assertIsNone(task.audit_current_version)


class AuditResetCommitTimingTest(unittest.IsolatedAsyncioTestCase):
    async def test_failed_reset_run_preserves_previous_audit_results(self):
        source = Segment(id="source", start=0, end=1, text="当前 ASR")
        previous = Segment(
            id="reviewed",
            start=0,
            end=1,
            text="旧审核结果",
            action=SegmentAction.DELETE,
            user_override=True,
        )
        task = Task(
            name="安全重置",
            asr_result=ASRResult(segments=[source], duration=1),
            audit_segments=[previous],
            audit_history=[
                AuditSnapshot(
                    version=1,
                    created_at="2026-07-27T00:02:00",
                    model="test",
                    segments_count=1,
                    segments_kept=0,
                    segments_deleted=1,
                )
            ],
            audit_current_version=1,
        )
        store = SimpleNamespace(
            get=lambda task_id: task if task_id == task.id else None,
            update=lambda updated: updated,
        )

        async def fail_semantic(**kwargs):
            self.assertEqual([segment.id for segment in kwargs["segments"]], ["source"])
            raise RetryableExecutionError("temporary")

        from backend.core.task_runner import RetryableExecutionError

        with self.assertRaises(AuditPipelineError):
            await run_audit_pipeline(
                task.id,
                AuditRequest(force_rule_engine=True, reset_prior_audit=True),
                store=store,
                semantic_audit_runner=fail_semantic,
            )

        self.assertEqual([segment.id for segment in task.audit_segments], ["reviewed"])
        self.assertEqual(task.audit_segments[0].action, SegmentAction.DELETE)
        self.assertEqual(task.audit_current_version, 1)
        self.assertEqual(len(task.audit_history), 1)


if __name__ == "__main__":
    unittest.main()
