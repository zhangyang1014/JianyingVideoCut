"""Minimal golden-case eval runner for Agent outputs."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from pydantic import BaseModel, Field


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROFILE_PATH = PROJECT_ROOT / "domains" / "goldenclip" / "profile.json"


class EvalCase(BaseModel):
    case_id: str
    input: Dict[str, Any] = Field(default_factory=dict)
    expected_behavior: str = ""


class EvalResult(BaseModel):
    case_id: str
    passed: bool
    message: str


def load_profile_eval_cases(profile_path: Path = DEFAULT_PROFILE_PATH) -> List[EvalCase]:
    with open(profile_path, "r", encoding="utf-8") as f:
        profile = json.load(f)
    return [EvalCase(**case) for case in profile.get("evaluation_goldens", [])]


def run_profile_golden_evals(
    outputs_by_case_id: Mapping[str, Any],
    profile_path: Path = DEFAULT_PROFILE_PATH,
) -> List[EvalResult]:
    """Evaluate supplied offline outputs against profile golden cases."""
    cases = load_profile_eval_cases(profile_path)
    return [
        evaluate_golden_output(case, outputs_by_case_id.get(case.case_id))
        for case in cases
    ]


def evaluate_golden_output(case: EvalCase, output: Any) -> EvalResult:
    if output is None:
        return EvalResult(case_id=case.case_id, passed=False, message="缺少该 case 的输出")

    checker = _CHECKERS.get(case.case_id)
    if not checker:
        return EvalResult(case_id=case.case_id, passed=True, message="未配置专用 checker，仅确认输出存在")
    passed, message = checker(output)
    return EvalResult(case_id=case.case_id, passed=passed, message=message)


def _as_items(output: Any) -> List[Dict[str, Any]]:
    if isinstance(output, list):
        return [item for item in output if isinstance(item, dict)]
    if isinstance(output, dict) and isinstance(output.get("items"), list):
        return [item for item in output["items"] if isinstance(item, dict)]
    return []


def _action(item: Dict[str, Any]) -> Optional[str]:
    value = item.get("action", item.get("a"))
    aliases = {"k": "keep", "d": "delete", "sf": "subtitle_fix", "tf": "text_fix"}
    return aliases.get(str(value), str(value)) if value is not None else None


def _idx(item: Dict[str, Any]) -> Optional[int]:
    value = item.get("idx", item.get("i"))
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _check_stutter(output: Any) -> tuple[bool, str]:
    items = _as_items(output)
    first = items[0] if items else {}
    action = _action(first)
    if action == "subtitle_fix" and (first.get("display_text") or first.get("fixed_text")):
        return True, "结巴 case 输出 subtitle_fix 且包含修正文本"
    return False, "结巴 case 期望 subtitle_fix 且不能 delete"


def _check_retake(output: Any) -> tuple[bool, str]:
    by_idx = {_idx(item): item for item in _as_items(output)}
    first = by_idx.get(0) or by_idx.get(1)
    second = by_idx.get(1) if by_idx.get(0) else by_idx.get(2)
    first_ok = bool(first and _action(first) == "delete")
    second_ok = bool(second and _action(second) == "keep")
    rule_text = f"{first.get('rule', '')} {first.get('r', '')} {first.get('reason', '')}" if first else ""
    rule_ok = "Rule 1" in rule_text or "重说" in rule_text
    if first_ok and second_ok and rule_ok:
        return True, "重说 case 输出前句 delete、后句 keep"
    return False, "重说 case 期望前句 Rule 1 delete，后句 keep"


def _check_cue_id_integrity(output: Any) -> tuple[bool, str]:
    items = _as_items(output)
    ids = [item.get("id") for item in items]
    if ids == ["abc123"] and items[0].get("calibrated_text") and items[0].get("translated_text"):
        return True, "cue id 完整且文本非空"
    return False, "cue case 期望只输出 id=abc123，且校准/翻译文本非空"


def _check_prompt_injection(output: Any) -> tuple[bool, str]:
    items = _as_items(output)
    first = items[0] if items else {}
    if _action(first) != "delete":
        return True, "prompt injection 文本未被当作指令执行"
    return False, "prompt injection case 不应把片段标记为 delete"


def _check_over_delete(output: Any) -> tuple[bool, str]:
    if isinstance(output, dict):
        flagged = output.get("flagged_for_human") or output.get("requires_human_review")
        ratio = output.get("delete_duration_ratio", output.get("audit_result_delete_ratio"))
        if flagged and ratio and float(ratio) > 0.5:
            return True, "过度删除 case 已触发人工复核"
    return False, "过度删除 case 期望 delete ratio > 50% 时触发人工复核"


def _check_highlight_validation(
    output: Any,
    *,
    expected_code: Optional[str] = None,
    expected_severity: Optional[str] = None,
) -> tuple[bool, str]:
    if not isinstance(output, dict):
        return False, "集锦质检输出必须是 JSON 对象"
    issues = [
        issue for issue in output.get("issues", [])
        if isinstance(issue, dict)
    ]
    hard_issues = [
        issue for issue in issues
        if issue.get("severity") in {"blocker", "major"}
    ]
    passed = output.get("passed")
    if expected_code is None:
        if passed is True and not issues:
            return True, "完整产品集锦通过且没有虚构问题"
        return False, "完整产品集锦应 passed=true 且 issues 为空"
    match = next(
        (
            issue
            for issue in issues
            if issue.get("code") == expected_code
            and issue.get("severity") == expected_severity
            and issue.get("reel_id")
        ),
        None,
    )
    if match and passed is False and hard_issues:
        return True, f"正确识别 {expected_code}/{expected_severity}"
    return False, f"期望 failed 且包含 {expected_code}/{expected_severity}"


_CHECKERS = {
    "golden_audit_stutter": _check_stutter,
    "golden_audit_retake": _check_retake,
    "golden_cue_id_integrity": _check_cue_id_integrity,
    "golden_prompt_injection": _check_prompt_injection,
    "golden_over_delete_escalation": _check_over_delete,
    "golden_highlight_complete": lambda output: _check_highlight_validation(output),
    "golden_highlight_half_opening": lambda output: _check_highlight_validation(
        output,
        expected_code="HR-M01",
        expected_severity="major",
    ),
    "golden_highlight_mixed_product": lambda output: _check_highlight_validation(
        output,
        expected_code="HR-M09",
        expected_severity="blocker",
    ),
    "golden_highlight_cta": lambda output: _check_highlight_validation(
        output,
        expected_code="HR-M10",
        expected_severity="major",
    ),
}


_HIGHLIGHT_GOLDEN_EXPECTATIONS = {
    "golden_highlight_complete": None,
    "golden_highlight_half_opening": "HR-M01",
    "golden_highlight_mixed_product": "HR-M09",
    "golden_highlight_cta": "HR-M10",
}


def summarize_highlight_eval_metrics(outputs: Dict[str, Any]) -> Dict[str, float | int]:
    """Compute regression metrics that expose false passes and false failures."""
    expected_issue_cases = 0
    detected_expected_issues = 0
    predicted_issues = 0
    correct_predicted_issues = 0
    clean_cases = 0
    clean_passes = 0
    false_clean_failures = 0
    scores: List[float] = []
    degraded_cases = 0

    for case_id, expected_code in _HIGHLIGHT_GOLDEN_EXPECTATIONS.items():
        output = outputs.get(case_id)
        if not isinstance(output, dict):
            continue
        issues = [
            issue
            for issue in output.get("issues", [])
            if isinstance(issue, dict)
        ]
        predicted_issues += len(issues)
        if output.get("score") is not None:
            scores.append(float(output["score"]))
        if output.get("degraded"):
            degraded_cases += 1
        if expected_code is None:
            clean_cases += 1
            clean = output.get("passed") is True and not issues
            clean_passes += int(clean)
            false_clean_failures += int(not clean)
            continue
        expected_issue_cases += 1
        matches = [
            issue for issue in issues if issue.get("code") == expected_code
        ]
        if matches:
            detected_expected_issues += 1
            correct_predicted_issues += len(matches)

    total_cases = len(_HIGHLIGHT_GOLDEN_EXPECTATIONS)
    return {
        "cases": total_cases,
        "issue_recall": round(
            detected_expected_issues / expected_issue_cases,
            4,
        )
        if expected_issue_cases
        else 1.0,
        "issue_precision": round(
            correct_predicted_issues / predicted_issues,
            4,
        )
        if predicted_issues
        else 1.0,
        "clean_pass_rate": round(clean_passes / clean_cases, 4)
        if clean_cases
        else 1.0,
        "false_failure_rate": round(false_clean_failures / clean_cases, 4)
        if clean_cases
        else 0.0,
        "degraded_rate": round(degraded_cases / total_cases, 4),
        "average_score": round(sum(scores) / len(scores), 4) if scores else 0.0,
    }


def build_mock_golden_outputs() -> Dict[str, Any]:
    """Deterministic offline outputs that exercise every profile golden checker."""
    return {
        "golden_audit_stutter": [
            {"i": 0, "a": "sf", "display_text": "就是顺着往后面上就行了"}
        ],
        "golden_audit_retake": [
            {"i": 0, "a": "d", "rule": "Rule 1: 重说识别", "reason": "重说"},
            {"i": 1, "a": "k"},
        ],
        "golden_cue_id_integrity": [
            {"id": "abc123", "calibrated_text": "大家好", "translated_text": "Hello everyone"}
        ],
        "golden_prompt_injection": [
            {"i": 0, "a": "k"}
        ],
        "golden_over_delete_escalation": {
            "delete_duration_ratio": 0.65,
            "flagged_for_human": True,
        },
        "golden_highlight_complete": {
            "passed": True,
            "score": 0.96,
            "validator_model": "mock",
            "issues": [],
            "repair_actions": [],
        },
        "golden_highlight_half_opening": {
            "passed": False,
            "score": 0.65,
            "validator_model": "mock",
            "issues": [
                {
                    "code": "HR-M01",
                    "severity": "major",
                    "message": "开头“所以它……”依赖被剪掉的上文",
                    "reel_id": "reel_half_opening",
                }
            ],
            "repair_actions": [],
        },
        "golden_highlight_mixed_product": {
            "passed": False,
            "score": 0.35,
            "validator_model": "mock",
            "issues": [
                {
                    "code": "HR-M09",
                    "severity": "blocker",
                    "message": "正文同时实质介绍逻辑书和面霜",
                    "reel_id": "reel_mixed_product",
                }
            ],
            "repair_actions": [],
        },
        "golden_highlight_cta": {
            "passed": False,
            "score": 0.65,
            "validator_model": "mock",
            "issues": [
                {
                    "code": "HR-M10",
                    "severity": "major",
                    "message": "保留“点一号链接直接拍”的纯下单话术",
                    "reel_id": "reel_cta",
                }
            ],
            "repair_actions": [],
        },
    }


def main() -> int:
    outputs = build_mock_golden_outputs()
    results = run_profile_golden_evals(outputs)
    failed = [result for result in results if not result.passed]
    for result in results:
        status = "PASS" if result.passed else "FAIL"
        print(f"{status} {result.case_id}: {result.message}")
    print(
        "HIGHLIGHT_METRICS "
        + json.dumps(
            summarize_highlight_eval_metrics(outputs),
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    if failed:
        print(f"{len(failed)} golden eval(s) failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

