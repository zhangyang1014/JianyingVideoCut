import unittest
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from backend.core.task_runner import RetryableExecutionError
from backend.agent_runtime.contracts import validate_audit_coherence_contract
from backend.models.task import (
    AuditCoherenceIssue,
    AuditCoherenceReport,
    AuditRequest,
    AuditRule,
    HighlightReel,
    HighlightReelQualityReport,
    ProductSemanticMark,
    Segment,
    SegmentAction,
    Task,
    TaskStatus,
    TaskType,
    WordTimestamp,
)
from backend.services.audit_coherence import _build_review_payload, apply_coherence_repairs, run_audit_coherence_review
from backend.services.audit_pipeline import run_audit_pipeline
from backend.services.semantic_auditor import apply_segment_corrections


def _fake_store(task: Task) -> SimpleNamespace:
    return SimpleNamespace(
        get=lambda task_id: task if task_id == task.id else None,
        update=lambda t: t,
    )


def _char_words(text: str, start: float = 0.0, step: float = 0.1) -> list[WordTimestamp]:
    return [
        WordTimestamp(word=char, start=round(start + idx * step, 3), end=round(start + (idx + 1) * step, 3))
        for idx, char in enumerate(text)
    ]


class AuditCoherenceTests(unittest.IsolatedAsyncioTestCase):
    def test_review_payload_includes_chunk_boundaries_and_product_semantic(self):
        payload = json.loads(_build_review_payload(
            [
                Segment(
                    id="seg_1",
                    start=0.0,
                    end=2.0,
                    text="这款面霜适合干皮",
                    action=SegmentAction.KEEP,
                    compiled_from_operation="chunk1:op_1",
                    semantic_unit_id="chunk1:unit_1",
                    product_semantic=ProductSemanticMark(
                        product_entities=[{"name": "面霜", "category": "护肤"}],
                        roles=["product_opening", "selling_point"],
                        selling_points=["适合干皮"],
                        angle_label="干皮适用",
                        eligible_for_highlight=True,
                        confidence=0.9,
                    ),
                )
            ],
            "highlight_reel",
        ))

        self.assertEqual(payload["chunk_boundaries"][0]["chunk"], "chunk1")
        self.assertEqual(payload["segments"][0]["chunk"], "chunk1")
        self.assertEqual(payload["segments"][0]["product_semantic"]["angle_label"], "干皮适用")

    async def test_local_review_restores_previous_context_and_inherits_clip_group(self):
        segments = [
            Segment(id="seg_1", start=0.0, end=2.0, text="这套课程的重点是", action=SegmentAction.DELETE),
            Segment(
                id="seg_2",
                start=2.2,
                end=5.0,
                text="所以孩子会形成逻辑思维",
                action=SegmentAction.KEEP,
                clip_group=1,
                style="highlight",
            ),
        ]

        repaired, report = await run_audit_coherence_review(segments, use_model=False)

        self.assertTrue(report.passed)
        self.assertEqual(report.repaired_count, 1)
        self.assertEqual(repaired[0].action, SegmentAction.KEEP)
        self.assertEqual(repaired[0].clip_group, 1)
        self.assertEqual(repaired[0].style, "setup")

    async def test_local_review_splits_tail_that_should_hide_with_next_segment(self):
        text = "最后可以找到谁是卧底看第一个事件"
        words = _char_words(text)
        segments = [
            Segment(
                id="seg_1",
                start=0.0,
                end=words[-1].end,
                text=text,
                action=SegmentAction.KEEP,
                words=words,
            ),
            Segment(id="seg_2", start=words[-1].end + 0.2, end=5.0, text="这个例子不用保留", action=SegmentAction.DELETE),
        ]

        repaired, report = await run_audit_coherence_review(segments, use_model=False)
        corrected = apply_segment_corrections(repaired)

        self.assertTrue(report.passed)
        self.assertEqual(report.repaired_count, 1)
        self.assertTrue(any(issue.code == "AC-L03" for issue in report.issues))
        self.assertEqual(corrected[0].id, "seg_1_p1")
        self.assertEqual(corrected[0].action, SegmentAction.KEEP)
        self.assertEqual(corrected[0].text, "最后可以找到谁是卧底")
        self.assertEqual(corrected[1].id, "seg_1_p2")
        self.assertEqual(corrected[1].action, SegmentAction.DELETE)
        self.assertEqual(corrected[1].text, "看第一个事件")
        self.assertEqual(corrected[2].id, "seg_2")
        self.assertEqual(corrected[2].action, SegmentAction.DELETE)

    async def test_local_review_splits_pronoun_tail_before_hidden_segment(self):
        text = "所以对文字就是对孩子读字的拆掉细节这个能力他自己就"
        words = _char_words(text)
        segments = [
            Segment(
                id="seg_1",
                start=0.0,
                end=words[-1].end,
                text=text,
                action=SegmentAction.KEEP,
                words=words,
            ),
            Segment(id="seg_2", start=words[-1].end + 0.1, end=7.0, text="想要听刚才说的专注力训练", action=SegmentAction.DELETE),
        ]

        repaired, report = await run_audit_coherence_review(segments, use_model=False)
        corrected = apply_segment_corrections(repaired)

        self.assertTrue(any(issue.code == "AC-L03" for issue in report.issues))
        self.assertEqual(corrected[0].text, "所以对文字就是对孩子读字的拆掉细节这个能力")
        self.assertEqual(corrected[0].action, SegmentAction.KEEP)
        self.assertEqual(corrected[1].text, "他自己就")
        self.assertEqual(corrected[1].action, SegmentAction.DELETE)

    async def test_model_response_does_not_erase_local_sentence_repairs(self):
        text = "所以对文字就是对孩子读字的拆掉细节这个能力他自己就"
        words = _char_words(text)
        segments = [
            Segment(id="seg_1", start=0.0, end=words[-1].end, text=text, action=SegmentAction.KEEP, words=words),
            Segment(id="seg_2", start=words[-1].end + 0.1, end=7.0, text="想要听刚才说的专注力训练", action=SegmentAction.DELETE),
        ]

        with patch(
            "backend.services.audit_coherence._call_openai",
            new=AsyncMock(return_value='{"passed":true,"score":1,"issues":[],"repairs":[],"term_fixes":[]}'),
        ):
            repaired, report = await run_audit_coherence_review(
                segments,
                provider="openai",
                api_key="sk-test",
                model="test-model",
                use_model=True,
            )

        corrected = apply_segment_corrections(repaired)

        self.assertEqual(report.repaired_count, 1)
        self.assertTrue(any(issue.code == "AC-L03" for issue in report.issues))
        self.assertEqual(corrected[0].text, "所以对文字就是对孩子读字的拆掉细节这个能力")
        self.assertEqual(corrected[1].text, "他自己就")
        self.assertEqual(corrected[1].action, SegmentAction.DELETE)

    async def test_model_transient_failure_retries_three_times_then_caches(self):
        segments = [
            Segment(id="seg_1", start=0.0, end=2.0, text="完整观点。", action=SegmentAction.KEEP),
        ]
        response = '{"passed":true,"score":1,"issues":[],"repairs":[],"term_fixes":[]}'
        call = AsyncMock(side_effect=[
            RetryableExecutionError("temporary-1"),
            RetryableExecutionError("temporary-2"),
            RetryableExecutionError("temporary-3"),
            response,
        ])

        with tempfile.TemporaryDirectory() as cache_dir, patch(
            "backend.services.audit_coherence.COHERENCE_CACHE_DIR",
            Path(cache_dir),
        ), patch(
            "backend.services.audit_coherence._call_openai",
            new=call,
        ), patch(
            "backend.core.task_runner.asyncio.sleep",
            new=AsyncMock(),
        ):
            await run_audit_coherence_review(
                segments,
                provider="openai",
                api_key="sk-test",
                model="test-model",
                openai_base_url="https://example.test/v1",
                use_model=True,
            )
            await run_audit_coherence_review(
                segments,
                provider="openai",
                api_key="different-secret",
                model="test-model",
                openai_base_url="https://example.test/v1",
                use_model=True,
            )

        self.assertEqual(call.await_count, 4)

    async def test_coherence_cache_invalidates_when_input_changes(self):
        first = [Segment(id="seg_1", start=0, end=1, text="输入 A。")]
        changed = [Segment(id="seg_1", start=0, end=1, text="输入 B。")]
        call = AsyncMock(
            return_value='{"passed":true,"score":1,"issues":[],"repairs":[],"term_fixes":[]}'
        )

        with tempfile.TemporaryDirectory() as cache_dir, patch(
            "backend.services.audit_coherence.COHERENCE_CACHE_DIR",
            Path(cache_dir),
        ), patch(
            "backend.services.audit_coherence._call_openai",
            new=call,
        ):
            await run_audit_coherence_review(
                first, api_key="sk-test", model="test-model", use_model=True
            )
            await run_audit_coherence_review(
                first, api_key="sk-test", model="test-model", use_model=True
            )
            await run_audit_coherence_review(
                changed, api_key="sk-test", model="test-model", use_model=True
            )

        self.assertEqual(call.await_count, 2)

    async def test_pipeline_gate_skips_highlight_plan_when_coherence_fails(self):
        task = Task(
            id="task-coherence-gate",
            name="coherence gate",
            task_type=TaskType.HIGHLIGHT_REEL,
            status=TaskStatus.AUDIT_RUNNING,
            audit_segments=[
                Segment(id="seg_1", start=0.0, end=2.0, text="高能观点", action=SegmentAction.KEEP),
            ],
        )

        async def semantic_runner(**kwargs):
            return [seg.model_copy(deep=True) for seg in kwargs["segments"]]

        failed_report = AuditCoherenceReport(
            passed=False,
            score=0.4,
            issues=[
                AuditCoherenceIssue(
                    code="AC-M01",
                    severity="major",
                    message="保留稿缺少上下文",
                    segment_id="seg_1",
                )
            ],
            repaired_count=0,
        )

        with patch(
            "backend.services.audit_pipeline.run_audit_coherence_review",
            new=AsyncMock(return_value=([seg.model_copy(deep=True) for seg in task.audit_segments], failed_report)),
        ), patch(
            "backend.services.audit_pipeline.refresh_highlight_plan",
            new=AsyncMock(),
        ) as refresh:
            result = await run_audit_pipeline(
                task.id,
                AuditRequest(force_rule_engine=True),
                store=_fake_store(task),
                semantic_audit_runner=semantic_runner,
            )

        refresh.assert_not_awaited()
        self.assertFalse(result.quality_passed)
        self.assertIsNotNone(task.audit_coherence_report)
        self.assertFalse(task.audit_coherence_report.passed)
        self.assertEqual(task.highlight_reels, [])

    async def test_plain_pipeline_clears_existing_highlight_plan_when_skipped(self):
        task = Task(
            id="task-plain-audit",
            name="plain audit",
            task_type=TaskType.HIGHLIGHT_REEL,
            status=TaskStatus.REVIEW,
            audit_segments=[
                Segment(
                    id="seg_1",
                    start=0.0,
                    end=2.0,
                    text="高能观点",
                    action=SegmentAction.KEEP,
                    clip_group=1,
                    clip_title="旧集锦",
                ),
            ],
            highlight_reels=[
                HighlightReel(clip_group=1, title="旧集锦", segment_ids=["seg_1"]),
            ],
            highlight_quality_report=HighlightReelQualityReport(passed=True, score=0.9),
        )

        async def semantic_runner(**kwargs):
            return [seg.model_copy(deep=True) for seg in kwargs["segments"]]

        passed_report = AuditCoherenceReport(passed=True, score=1.0)

        with patch(
            "backend.services.audit_pipeline.run_audit_coherence_review",
            new=AsyncMock(return_value=([seg.model_copy(deep=True) for seg in task.audit_segments], passed_report)),
        ), patch(
            "backend.services.audit_pipeline.refresh_highlight_plan",
            new=AsyncMock(),
        ) as refresh:
            result = await run_audit_pipeline(
                task.id,
                AuditRequest(force_rule_engine=True),
                include_highlight_plan=False,
                store=_fake_store(task),
                semantic_audit_runner=semantic_runner,
            )

        refresh.assert_not_awaited()
        self.assertEqual(task.highlight_reels, [])
        self.assertIsNone(task.highlight_quality_report)
        self.assertIsNone(task.audit_segments[0].clip_group)
        self.assertIsNone(task.audit_segments[0].clip_title)
        self.assertIsNone(result.quality_passed)


class AuditCoherenceContractTests(unittest.TestCase):
    def test_text_fix_repair_preserves_asr_text_and_sets_display_text(self):
        original = [Segment(id="seg_1", start=0.0, end=2.0, text="剪影很好用")]
        repaired, count = apply_coherence_repairs(
            original,
            [
                {
                    "segment_id": "seg_1",
                    "action": "text_fix",
                    "display_text": "剪映很好用",
                    "reason": "AC-M02: 专有名词纠错",
                }
            ],
        )

        report = validate_audit_coherence_contract(original, repaired)

        self.assertEqual(count, 1)
        self.assertTrue(report.passed)
        self.assertEqual(repaired[0].text, "剪影很好用")
        self.assertEqual(repaired[0].display_text, "剪映很好用")
        self.assertEqual(repaired[0].fix_type, "asr_error")

    def test_split_repair_preserves_part_actions_for_postprocess(self):
        original = [Segment(id="seg_1", start=0.0, end=2.0, text="完整内容看第一个事件")]
        repaired, count = apply_coherence_repairs(
            original,
            [
                {
                    "segment_id": "seg_1",
                    "action": "split",
                    "split_before": "看第一个事件",
                    "split_part1_action": "keep",
                    "split_part2_action": "delete",
                    "reason": "AC-L03: 尾部随下一段隐藏",
                }
            ],
        )

        report = validate_audit_coherence_contract(original, repaired)

        self.assertEqual(count, 1)
        self.assertTrue(report.passed)
        self.assertEqual(repaired[0].split_part1_action, SegmentAction.KEEP)
        self.assertEqual(repaired[0].split_part2_action, SegmentAction.DELETE)
        self.assertEqual(repaired[0].rule, AuditRule.FRAGMENT)

    def test_contract_rejects_invalid_split_before(self):
        original = [Segment(id="seg_1", start=0.0, end=2.0, text="这是完整的一句")]
        repaired = [original[0].model_copy(deep=True)]
        repaired[0].action = SegmentAction.SPLIT
        repaired[0].split_before = "不存在"

        report = validate_audit_coherence_contract(original, repaired)

        self.assertTrue(report.rejected)
        self.assertTrue(any(issue.assertion_id == "AC-04" for issue in report.issues))


if __name__ == "__main__":
    unittest.main()
