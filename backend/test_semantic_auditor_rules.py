import unittest

from backend.models.task import CandidateEdit, Segment, SegmentAction, TaskParams
from backend.services.semantic_auditor import (
    _rule_based_audit,
    apply_segment_corrections,
    apply_sentence_group_guard,
    build_audit_prompt,
)


class RuleBasedAuditCandidateTests(unittest.IsolatedAsyncioTestCase):
    async def test_filler_candidate_uses_precise_delete_boundary(self):
        segment = Segment(
            start=0.0,
            end=0.5,
            text="嗯",
            candidate_edits=[
                CandidateEdit(
                    source="filler",
                    edit_type="delete_range",
                    start=0.1,
                    end=0.32,
                    text="嗯",
                    reason="语气词候选",
                )
            ],
        )

        audited = await _rule_based_audit(
            [segment],
            TaskParams(min_segment_duration=0.1),
            task_type="monologue_clean",
            log_callback=None,
        )

        self.assertEqual(audited[0].action, SegmentAction.DELETE)
        self.assertEqual(audited[0].start, 0.1)
        self.assertEqual(audited[0].end, 0.32)
        self.assertEqual(audited[0].candidate_edits[0].status, "accepted")

    def test_sentence_group_guard_restores_deleted_prefix_and_merges_sentence(self):
        segments = [
            Segment(
                id="seg_1",
                start=0.0,
                end=0.8,
                text="这个书",
                action=SegmentAction.DELETE,
                reason="残句过短",
            ),
            Segment(
                id="seg_2",
                start=0.9,
                end=3.0,
                text="就是让孩子学会独立思考",
                action=SegmentAction.KEEP,
            ),
        ]

        guarded = apply_sentence_group_guard(segments, task_type="monologue_clean")
        self.assertEqual(guarded[0].action, SegmentAction.MERGE_NEXT)
        self.assertEqual(guarded[0].claude_action, SegmentAction.MERGE_NEXT)
        self.assertIn("SG-L01", guarded[0].reason)
        self.assertIn("SG-L02", guarded[0].reason)

        corrected = apply_segment_corrections(guarded)
        self.assertEqual(len(corrected), 1)
        self.assertEqual(corrected[0].action, SegmentAction.KEEP)
        self.assertTrue(corrected[0].merged)
        self.assertEqual(corrected[0].text, "这个书就是让孩子学会独立思考")

    def test_sentence_group_guard_merges_continuous_same_sentence_chain(self):
        segments = [
            Segment(id="seg_1", start=0.0, end=0.5, text="这个书", action=SegmentAction.KEEP),
            Segment(id="seg_2", start=0.6, end=1.2, text="就是孩子", action=SegmentAction.KEEP),
            Segment(id="seg_3", start=1.3, end=2.0, text="学习专注力的文字", action=SegmentAction.KEEP),
        ]

        guarded = apply_sentence_group_guard(segments, task_type="monologue_clean")
        self.assertEqual(guarded[0].action, SegmentAction.MERGE_NEXT)
        self.assertEqual(guarded[1].action, SegmentAction.MERGE_NEXT)
        self.assertEqual(guarded[0].claude_action, SegmentAction.MERGE_NEXT)
        self.assertEqual(guarded[1].claude_action, SegmentAction.MERGE_NEXT)

        corrected = apply_segment_corrections(guarded)
        self.assertEqual(len(corrected), 1)
        self.assertEqual(corrected[0].text, "这个书就是孩子学习专注力的文字")
        self.assertTrue(corrected[0].merged)

    def test_build_audit_prompt_includes_sentence_group_integrity_contract(self):
        prompt, _ = build_audit_prompt(
            segments=[
                Segment(id="seg_1", start=0.0, end=0.8, text="这个书"),
                Segment(id="seg_2", start=0.9, end=3.0, text="就是让孩子学会独立思考"),
            ],
            task_type="monologue_clean",
        )

        self.assertIn("句群完整性约束", prompt)
        self.assertIn("不要只删掉前半句", prompt)
        self.assertIn('a:"mn"', prompt)

    def test_build_audit_prompt_prefers_semantic_edit_plan(self):
        prompt, _ = build_audit_prompt(
            segments=[Segment(id="seg_1", start=0.0, end=2.0, text="可以自由断句的内容")],
            task_type="highlight_reel",
        )

        self.assertIn("自由断句语义编辑计划", prompt)
        self.assertIn("AuditEditPlan", prompt)
        self.assertIn('"operations"', prompt)
        self.assertIn('"id": "seg_1"', prompt)


if __name__ == "__main__":
    unittest.main()
