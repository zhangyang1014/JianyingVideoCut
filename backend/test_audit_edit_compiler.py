import unittest

from backend.agent_runtime.contracts import validate_audit_edit_plan_contract
from backend.models.task import (
    AuditEditOperation,
    AuditEditPlan,
    AuditRule,
    ProductEntity,
    ProductSemanticMark,
    Segment,
    SegmentAction,
    WordTimestamp,
)
from backend.services.audit_edit_compiler import compile_audit_edit_plan, parse_audit_edit_plan
from backend.services.semantic_auditor import apply_segment_corrections, parse_claude_response


def _char_words(text: str, start: float = 0.0, step: float = 0.1):
    return [
        WordTimestamp(word=char, start=round(start + idx * step, 3), end=round(start + (idx + 1) * step, 3))
        for idx, char in enumerate(text)
    ]


class AuditEditCompilerTests(unittest.TestCase):
    def test_compile_split_delete_tail_to_coherent_segments(self):
        text = "核心观点已经讲完了看第一个事件"
        original = [
            Segment(id="seg_1", start=0.0, end=3.0, text=text, words=_char_words(text)),
            Segment(id="seg_2", start=3.2, end=4.0, text="无效案例", action=SegmentAction.KEEP),
        ]
        plan = AuditEditPlan(
            operations=[
                AuditEditOperation(
                    op="split_at",
                    source_id="seg_1",
                    source_ids=["seg_1"],
                    split_before="看第一个事件",
                    split_part1_action=SegmentAction.KEEP,
                    split_part2_action=SegmentAction.DELETE,
                    reason="前半句完整，尾部引出无效内容",
                ),
                AuditEditOperation(
                    op="delete_span",
                    source_ids=["seg_2"],
                    reason="无效案例",
                ),
            ]
        )

        compiled = compile_audit_edit_plan(original, plan)
        report = validate_audit_edit_plan_contract(original, plan, compiled.segments)
        corrected = apply_segment_corrections(compiled.segments)

        self.assertTrue(report.passed)
        self.assertEqual(corrected[0].text, "核心观点已经讲完了")
        self.assertEqual(corrected[0].action, SegmentAction.KEEP)
        self.assertEqual(corrected[1].text, "看第一个事件")
        self.assertEqual(corrected[1].action, SegmentAction.DELETE)
        self.assertEqual(corrected[2].action, SegmentAction.DELETE)
        self.assertEqual(corrected[0].source_segment_ids, ["seg_1"])

    def test_compile_merge_span_tracks_multiple_sources(self):
        original = [
            Segment(id="seg_1", start=0.0, end=1.0, text="这个书"),
            Segment(id="seg_2", start=1.1, end=2.0, text="就是孩子的逻辑思维"),
        ]
        plan = AuditEditPlan(
            operations=[
                AuditEditOperation(
                    op="merge_span",
                    source_ids=["seg_1", "seg_2"],
                    semantic_unit_id="unit_1",
                    reason="同一句话",
                )
            ]
        )

        compiled = compile_audit_edit_plan(original, plan)
        corrected = apply_segment_corrections(compiled.segments)

        self.assertEqual(len(corrected), 1)
        self.assertEqual(corrected[0].text, "这个书就是孩子的逻辑思维")
        self.assertEqual(corrected[0].source_segment_ids, ["seg_1", "seg_2"])
        self.assertEqual(corrected[0].semantic_unit_id, "unit_1")

    def test_parse_claude_response_accepts_edit_plan_object(self):
        original = [
            Segment(id="seg_1", start=0.0, end=1.0, text="废话"),
            Segment(id="seg_2", start=1.0, end=2.0, text="核心观点"),
        ]
        response = """
        {
          "operations": [
            {"op":"delete_span","source_ids":["seg_1"],"reason":"无效内容"},
            {"op":"keep_span","source_ids":["seg_2"],"semantic_unit_id":"unit_core","reason":"核心观点"}
          ]
        }
        """

        updated = parse_claude_response(response, original)

        self.assertEqual(updated[0].action, SegmentAction.DELETE)
        self.assertEqual(updated[1].action, SegmentAction.KEEP)
        self.assertEqual(updated[1].semantic_unit_id, "unit_core")
        self.assertTrue(updated[1].compiled_from_operation)

    def test_contract_rejects_invalid_split_before(self):
        original = [Segment(id="seg_1", start=0.0, end=1.0, text="完整句子")]
        plan = AuditEditPlan(
            operations=[
                AuditEditOperation(
                    op="split_at",
                    source_ids=["seg_1"],
                    split_before="不存在",
                    reason="非法切分",
                )
            ]
        )
        compiled = compile_audit_edit_plan(original, plan)

        report = validate_audit_edit_plan_contract(original, plan, compiled.segments)

        self.assertTrue(report.rejected)
        self.assertTrue(any(issue.assertion_id == "AE-03" for issue in report.issues))

    def test_parse_audit_edit_plan_maps_numeric_indices_to_ids(self):
        response = '{"operations":[{"op":"delete_span","source_ids":[1],"reason":"废话"}]}'

        plan = parse_audit_edit_plan(response, idx_to_id={1: "seg_1"})

        self.assertIsNotNone(plan)
        self.assertEqual(plan.operations[0].source_ids, ["seg_1"])

    def test_parse_audit_edit_plan_maps_seg_prefixed_indices_to_ids(self):
        response = '{"operations":[{"op":"keep_span","source_ids":["seg_03"],"reason":"产品卖点"}]}'

        plan = parse_audit_edit_plan(response, idx_to_id={3: "actual_3"})

        self.assertIsNotNone(plan)
        self.assertEqual(plan.operations[0].source_ids, ["actual_3"])

    def test_parse_audit_edit_plan_normalizes_short_rule_number(self):
        response = (
            '{"operations":['
            '{"op":"delete_span","source_ids":[1],"reason":"语气词","rule":"Rule 3"},'
            '{"op":"keep_span","source_ids":[2],"reason":"有效内容"}'
            ']}'
        )

        plan = parse_audit_edit_plan(response, idx_to_id={1: "seg_1", 2: "seg_2"})

        self.assertIsNotNone(plan)
        self.assertEqual(len(plan.operations), 2)
        self.assertEqual(plan.operations[0].rule, AuditRule.FILLER)

    def test_compile_selection_plan_can_default_unselected_segments_to_delete(self):
        original = [
            Segment(id="seg_1", start=0.0, end=1.0, text="寒暄"),
            Segment(id="seg_2", start=1.0, end=2.0, text="产品卖点"),
        ]
        plan = AuditEditPlan(
            operations=[
                AuditEditOperation(
                    op="keep_span",
                    source_ids=["seg_2"],
                    clip_group=1,
                    clip_title="产品卖点",
                    style="highlight",
                    reason="核心卖点",
                )
            ]
        )

        compiled = compile_audit_edit_plan(
            original,
            plan,
            default_action=SegmentAction.DELETE,
        )

        self.assertEqual(compiled.segments[0].action, SegmentAction.DELETE)
        self.assertTrue(compiled.segments[0].reason)
        self.assertTrue(compiled.segments[0].rule)
        self.assertEqual(compiled.segments[1].action, SegmentAction.KEEP)
        self.assertEqual(compiled.segments[1].clip_group, 1)
        self.assertEqual(compiled.segments[1].style, "highlight")

    def test_compile_preserves_product_semantic_mark(self):
        original = [
            Segment(id="seg_1", start=0.0, end=2.0, text="这款面霜适合干皮"),
        ]
        plan = AuditEditPlan(
            operations=[
                AuditEditOperation(
                    op="keep_span",
                    source_ids=["seg_1"],
                    product_semantic=ProductSemanticMark(
                        product_entities=[ProductEntity(name="这款面霜", canonical_name="XX面霜", category="面霜")],
                        roles=["product_mention", "selling_point"],
                        selling_points=["适合干皮"],
                        angle_label="干皮适用",
                    ),
                    reason="产品卖点",
                )
            ]
        )

        compiled = compile_audit_edit_plan(original, plan)

        self.assertIsNotNone(compiled.segments[0].product_semantic)
        self.assertEqual(compiled.segments[0].product_semantic.product_entities[0].canonical_name, "XX面霜")
        self.assertIn("selling_point", compiled.segments[0].product_semantic.roles)


if __name__ == "__main__":
    unittest.main()
