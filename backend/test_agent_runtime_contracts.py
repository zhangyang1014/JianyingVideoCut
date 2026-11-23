import json
import tempfile
import unittest
from pathlib import Path

from backend.agent_runtime import trace
from backend.agent_runtime.approvals import ApprovalRequired, assert_human_approval
from backend.agent_runtime.contracts import (
    ContractIssue,
    ContractReport,
    implemented_code_assertion_ids,
    validate_audit_edit_plan_contract,
    validate_audit_segments_contract,
    validate_product_groups_contract,
    validate_translation_cues_contract,
    validate_translation_payload_contract,
)
from backend.agent_runtime.evals import (
    EvalCase,
    build_mock_golden_outputs,
    evaluate_golden_output,
    load_profile_eval_cases,
    run_profile_golden_evals,
    summarize_highlight_eval_metrics,
)
from backend.agent_runtime.harness import ContractVerificationError, verify_contract_report
from backend.agent_runtime.hooks import emit_agent_hook, emit_tool_hook
from backend.agent_runtime.agents import AgentManifestError, get_agent_manifest_registry
from backend.agent_runtime.registry import get_tool_registry
from backend.models.task import (
    AuditEditOperation,
    AuditEditPlan,
    AuditRule,
    ProductGroup,
    ProductOccurrence,
    Segment,
    SegmentAction,
    TranslationCue,
)


class AgentRuntimeContractTests(unittest.TestCase):
    def test_contract_json_code_assertions_are_implemented(self):
        contracts_dir = Path(__file__).resolve().parents[1] / "domains" / "goldenclip" / "contracts"
        for contract_path in contracts_dir.glob("*.contract.json"):
            with open(contract_path, "r", encoding="utf-8") as f:
                contract = json.load(f)
            contract_id = contract["contract_id"]
            expected = {
                assertion["assertion_id"]
                for assertion in contract.get("assertions", [])
                if assertion.get("kind") == "code"
            }
            implemented = implemented_code_assertion_ids(contract_id)
            self.assertTrue(
                expected.issubset(implemented),
                f"{contract_id} 缺少实现: {sorted(expected - implemented)}",
            )

    def test_audit_contract_passes_complete_decisions(self):
        original = [
            Segment(id="seg_1", start=0.0, end=2.0, text="第一句"),
            Segment(id="seg_2", start=2.0, end=4.0, text="第二句"),
        ]
        audited = [seg.model_copy(deep=True) for seg in original]
        audited[1].action = SegmentAction.DELETE
        audited[1].reason = "重说，删除前句"
        audited[1].rule = AuditRule.RETAKE

        report = validate_audit_segments_contract(original, audited)

        self.assertTrue(report.passed)
        self.assertFalse(report.rejected)

    def test_audit_contract_rejects_immutable_timeline_change(self):
        original = [Segment(id="seg_1", start=0.0, end=2.0, text="第一句")]
        audited = [original[0].model_copy(deep=True)]
        audited[0].start = 0.5

        report = validate_audit_segments_contract(original, audited)

        self.assertTrue(report.rejected)
        self.assertTrue(any(issue.assertion_id == "AS-03" for issue in report.issues))

    def test_audit_contract_flags_over_delete_for_human_review(self):
        original = [
            Segment(id="seg_1", start=0.0, end=5.0, text="第一句"),
            Segment(id="seg_2", start=5.0, end=10.0, text="第二句"),
        ]
        audited = [seg.model_copy(deep=True) for seg in original]
        for seg in audited:
            seg.action = SegmentAction.DELETE
            seg.reason = "重说"
            seg.rule = AuditRule.RETAKE

        report = validate_audit_segments_contract(original, audited)

        self.assertFalse(report.rejected)
        self.assertTrue(report.flagged_for_human)
        self.assertEqual(report.metrics["delete_duration_ratio"], 1.0)

    def test_audit_edit_plan_rejects_uncovered_input_segments(self):
        original = [
            Segment(id="seg_1", start=0.0, end=2.0, text="第一句"),
            Segment(id="seg_2", start=2.0, end=4.0, text="第二句"),
        ]
        plan = AuditEditPlan(
            operations=[
                AuditEditOperation(
                    op="keep_span",
                    source_ids=["seg_1"],
                    reason="保留第一句",
                )
            ]
        )

        report = validate_audit_edit_plan_contract(
            original,
            plan,
            [segment.model_copy(deep=True) for segment in original],
        )

        self.assertTrue(report.rejected)
        self.assertTrue(
            any(
                issue.assertion_id == "AE-01" and "seg_2" in issue.message
                for issue in report.issues
            )
        )

    def test_translation_payload_rejects_immutable_field_change(self):
        cues = [
            TranslationCue(
                id="cue_1",
                source_segment_id="seg_1",
                start=1.0,
                end=3.0,
                source_text="大家好",
                calibrated_text="大家好",
            )
        ]
        payload = [
            {
                "id": "cue_1",
                "start": 1.2,
                "calibrated_text": "大家好",
                "translated_text": "Hello everyone",
            }
        ]

        report = validate_translation_payload_contract(cues, payload)

        self.assertTrue(report.rejected)
        self.assertTrue(any(issue.assertion_id == "TC-03" for issue in report.issues))

    def test_product_group_contract_rejects_duplicate_occurrence_assignment(self):
        segments = [
            Segment(id="seg_1", start=0.0, end=2.0, text="产品介绍"),
        ]
        occurrence = ProductOccurrence(
            occurrence_id="occ_1",
            name="产品 A",
            aliases=["产品 A"],
            start=0.0,
            end=2.0,
            segment_ids=["seg_1"],
        )
        groups = [
            ProductGroup(
                product_group_id="group_1",
                canonical_name="产品 A",
                aliases=["产品 A"],
                occurrence_ids=["occ_1"],
                segment_ids=["seg_1"],
                start=0.0,
                end=2.0,
            ),
            ProductGroup(
                product_group_id="group_2",
                canonical_name="产品 A 套装",
                aliases=["产品 A 套装"],
                occurrence_ids=["occ_1"],
                segment_ids=["seg_1"],
                start=0.0,
                end=2.0,
            ),
        ]

        report = validate_product_groups_contract(
            [occurrence],
            groups,
            segments,
        )

        self.assertTrue(report.rejected)
        self.assertTrue(
            any(issue.assertion_id == "PG-02" for issue in report.issues)
        )

    def test_translation_cues_reject_empty_translation(self):
        original = [
            TranslationCue(
                id="cue_1",
                source_segment_id="seg_1",
                start=1.0,
                end=3.0,
                source_text="大家好",
                calibrated_text="大家好",
            )
        ]
        output = [original[0].model_copy(deep=True)]
        output[0].translated_text = ""

        report = validate_translation_cues_contract(original, output)

        self.assertTrue(report.rejected)
        self.assertTrue(any(issue.assertion_id == "TC-04" for issue in report.issues))

    def test_tool_registry_blocks_high_risk_without_approval(self):
        registry = get_tool_registry()

        self.assertTrue(registry.requires_human_approval("ffmpeg_export"))
        with self.assertRaises(ApprovalRequired):
            assert_human_approval("ffmpeg_export")
        assert_human_approval("ffmpeg_export", approval_token="review-workbench")

    def test_agent_manifest_registry_routes_all_domain_capabilities(self):
        registry = get_agent_manifest_registry()

        expected = {
            "orchestrate.clip_pipeline": "clip_orchestrator",
            "audit.segments": "semantic_audit_agent",
            "optimize.sentence_groups": "sentence_group_optimizer_agent",
            "extract.golden_quotes": "golden_quote_agent",
            "translate.cues": "subtitle_translate_agent",
            "validate.output": "clip_validator",
            "validate.highlight_reels": "highlight_reel_validator",
        }

        for capability, agent_name in expected.items():
            self.assertEqual(registry.for_capability(capability)["agent_name"], agent_name)

    def test_hook_bus_writes_trace_and_blocks_unauthorized_tool(self):
        old_trace_dir = trace.TRACE_DIR
        try:
            with tempfile.TemporaryDirectory() as tmp:
                trace.TRACE_DIR = Path(tmp)
                event = emit_agent_hook("task_hooks", "before_agent_run", "audit.segments")
                self.assertEqual(event.agent_name, "semantic_audit_agent")
                sentence_event = emit_agent_hook("task_hooks", "before_agent_run", "optimize.sentence_groups")
                self.assertEqual(sentence_event.agent_name, "sentence_group_optimizer_agent")

                emit_tool_hook(
                    "task_hooks",
                    "before_tool_call",
                    "orchestrate.clip_pipeline",
                    "write_audit_segments",
                )
                with self.assertRaises(AgentManifestError):
                    emit_tool_hook(
                        "task_hooks",
                        "before_tool_call",
                        "audit.segments",
                        "ffmpeg_export",
                    )

                trace_path = Path(tmp) / "task_hooks.jsonl"
                content = trace_path.read_text(encoding="utf-8")
                self.assertIn("agent_hook.before_agent_run", content)
                self.assertIn("write_audit_segments", content)
        finally:
            trace.TRACE_DIR = old_trace_dir

    def test_eval_runner_loads_profile_goldens_and_checks_output(self):
        cases = load_profile_eval_cases()
        self.assertTrue(any(case.case_id == "golden_cue_id_integrity" for case in cases))

        result = evaluate_golden_output(
            EvalCase(case_id="golden_cue_id_integrity"),
            [{"id": "abc123", "calibrated_text": "大家好", "translated_text": "Hello"}],
        )

        self.assertTrue(result.passed)

    def test_mock_profile_golden_evals_all_pass(self):
        outputs = build_mock_golden_outputs()
        results = run_profile_golden_evals(outputs)
        self.assertTrue(all(result.passed for result in results), [result.model_dump() for result in results])
        metrics = summarize_highlight_eval_metrics(outputs)
        self.assertEqual(metrics["issue_recall"], 1.0)
        self.assertEqual(metrics["issue_precision"], 1.0)
        self.assertEqual(metrics["false_failure_rate"], 0.0)

    def test_trace_redacts_secret_payload_keys(self):
        old_trace_dir = trace.TRACE_DIR
        try:
            with tempfile.TemporaryDirectory() as tmp:
                trace.TRACE_DIR = Path(tmp)
                trace.append_trace(
                    "task_secret",
                    "secret_test",
                    payload={
                        "api_key": "sk-test",
                        "nested": {"openai_api_key": "sk-openai"},
                    },
                )
                content = (Path(tmp) / "task_secret.jsonl").read_text(encoding="utf-8")
                self.assertIn("***REDACTED***", content)
                self.assertNotIn("sk-test", content)
                self.assertNotIn("sk-openai", content)
        finally:
            trace.TRACE_DIR = old_trace_dir


class AgentRuntimeHarnessTests(unittest.IsolatedAsyncioTestCase):
    async def test_verify_contract_report_writes_trace_and_rejects(self):
        old_trace_dir = trace.TRACE_DIR
        try:
            with tempfile.TemporaryDirectory() as tmp:
                trace.TRACE_DIR = Path(tmp)
                report = ContractReport(
                    contract_id="test.contract",
                    passed=False,
                    issues=[
                        ContractIssue(
                            assertion_id="T-01",
                            severity="blocker",
                            message="失败",
                            on_fail="reject",
                        )
                    ],
                )

                with self.assertRaises(ContractVerificationError):
                    await verify_contract_report("task_1", "test_phase", report)

                trace_path = Path(tmp) / "task_1.jsonl"
                self.assertTrue(trace_path.exists())
                self.assertIn("contract_verified", trace_path.read_text(encoding="utf-8"))
        finally:
            trace.TRACE_DIR = old_trace_dir


if __name__ == "__main__":
    unittest.main()

