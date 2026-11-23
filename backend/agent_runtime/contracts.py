"""Executable code assertions for GoldenClip Agent contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable, List, Literal, Optional, Sequence

from pydantic import BaseModel, Field

from ..models.task import (
    AuditEditPlan,
    AuditRule,
    HighlightReel,
    HighlightReelBuildOptions,
    HighlightReelQualityReport,
    ProductGroup,
    ProductOccurrence,
    Segment,
    SegmentAction,
    TranslationCue,
)
from ..services.highlight_reel_plan import validate_highlight_reels_code
from ..services.word_edits import word_deletion_error, planned_delete_duration


IMPLEMENTED_CODE_ASSERTIONS = {
    "goldenclip.audit_segments@1": {"AS-01", "AS-02", "AS-03", "AS-04", "AS-05", "AS-06"},
    "goldenclip.audit_edit_plan@1": {"AE-01", "AE-02", "AE-03", "AE-04", "AE-05", "AE-06"},
    "goldenclip.audit_coherence@1": {"AC-01", "AC-02", "AC-03", "AC-04", "AC-05", "AC-06"},
    "goldenclip.translation_cues@1": {"TC-01", "TC-02", "TC-03", "TC-04", "TC-05"},
    "goldenclip.product_groups@1": {"PG-01", "PG-02", "PG-03", "PG-04", "PG-05"},
    "goldenclip.highlight_reels@2": {
        "HR-01", "HR-02", "HR-03", "HR-04", "HR-05", "HR-06", "HR-10",
        "HR-21", "HR-22", "HR-23", "HR-24", "HR-25", "HR-26", "HR-27", "HR-28",
    },
    "goldenclip.highlight_reels@3": {
        "HR-01", "HR-02", "HR-03", "HR-04", "HR-05", "HR-06", "HR-10",
        "HR-21", "HR-22", "HR-23", "HR-27", "HR-28", "HR-29",
    },
}


class ContractIssue(BaseModel):
    assertion_id: str
    severity: str = "minor"  # blocker | major | minor
    message: str
    on_fail: Literal["reject", "reject_and_retry", "flag_for_human"] = "flag_for_human"
    target_id: Optional[str] = None


class ContractReport(BaseModel):
    contract_id: str
    passed: bool
    issues: List[ContractIssue] = Field(default_factory=list)
    metrics: Dict[str, Any] = Field(default_factory=dict)
    flagged_for_human: bool = False
    validated_at: str = Field(default_factory=lambda: datetime.now().isoformat())

    @property
    def rejected(self) -> bool:
        return any(issue.on_fail in {"reject", "reject_and_retry"} for issue in self.issues)

    @property
    def rejected_on_fail(self) -> Optional[str]:
        actions = {
            issue.on_fail
            for issue in self.issues
            if issue.on_fail in {"reject", "reject_and_retry"}
        }
        if "reject" in actions:
            return "reject"
        if "reject_and_retry" in actions:
            return "reject_and_retry"
        return None


def _has_duplicates(values: Sequence[str]) -> bool:
    return len(set(values)) != len(values)


def _word_signature(segment: Segment) -> List[Dict[str, Any]]:
    return [
        {
            "word": word.word,
            "start": word.start,
            "end": word.end,
            "speaker": word.speaker,
        }
        for word in (segment.words or [])
    ]


def _issue(
    assertion_id: str,
    severity: str,
    message: str,
    on_fail: str,
    target_id: Optional[str] = None,
) -> ContractIssue:
    return ContractIssue(
        assertion_id=assertion_id,
        severity=severity,
        message=message,
        on_fail=on_fail,
        target_id=target_id,
    )


def validate_audit_segments_contract(
    original_segments: Sequence[Segment],
    audited_segments: Sequence[Segment],
) -> ContractReport:
    """Validate audit decisions before split/merge post-processing mutates ids."""
    issues: List[ContractIssue] = []
    original_by_id = {seg.id: seg for seg in original_segments}
    audited_ids = [seg.id for seg in audited_segments]
    original_ids = [seg.id for seg in original_segments]

    allowed_actions = {action.value for action in SegmentAction}
    for segment in audited_segments:
        action_value = segment.action.value if isinstance(segment.action, SegmentAction) else str(segment.action)
        if action_value not in allowed_actions:
            issues.append(
                _issue("AS-01", "blocker", f"片段 {segment.id} action 非法: {action_value}", "reject_and_retry", segment.id)
            )

    missing = sorted(set(original_ids) - set(audited_ids))
    extra = sorted(set(audited_ids) - set(original_ids))
    if len(audited_segments) != len(original_segments) or missing or extra or _has_duplicates(audited_ids):
        issues.append(
            _issue(
                "AS-02",
                "blocker",
                f"审计决策覆盖不完整: missing={missing[:5]}, extra={extra[:5]}, duplicate={_has_duplicates(audited_ids)}",
                "reject_and_retry",
            )
        )

    for segment in audited_segments:
        original = original_by_id.get(segment.id)
        if not original:
            continue
        immutable_changed = (
            segment.start != original.start
            or segment.end != original.end
            or segment.text != original.text
            or _word_signature(segment) != _word_signature(original)
        )
        if immutable_changed:
            issues.append(
                _issue("AS-03", "blocker", f"片段 {segment.id} 改写了 ASR 不可变字段", "reject", segment.id)
            )

        if segment.word_deletions:
            error = word_deletion_error(segment, segment.word_deletions)
            if error:
                issues.append(_issue("AS-04", "blocker", f"{segment.id}: {error}", "reject_and_retry", segment.id))

        if segment.action == SegmentAction.DELETE:
            if not (segment.reason or "").strip() or not segment.rule:
                issues.append(
                    _issue("AS-04", "blocker", f"delete 片段 {segment.id} 缺少 reason 或 rule", "reject_and_retry", segment.id)
                )
        elif segment.action in {SegmentAction.SUBTITLE_FIX, SegmentAction.TEXT_FIX}:
            if not (segment.display_text or "").strip() or not (segment.fix_type or "").strip():
                issues.append(
                    _issue(
                        "AS-04",
                        "blocker",
                        f"{segment.action.value} 片段 {segment.id} 缺少 display_text 或 fix_type",
                        "reject_and_retry",
                        segment.id,
                    )
                )
        elif segment.action == SegmentAction.SPLIT:
            split_before = (segment.split_before or "").strip()
            if not split_before or split_before not in (original.text or ""):
                issues.append(
                    _issue("AS-04", "blocker", f"split 片段 {segment.id} 的 split_before 不在原文中", "reject_and_retry", segment.id)
                )

        if segment.rule and not isinstance(segment.rule, AuditRule):
            issues.append(
                _issue("AS-05", "major", f"片段 {segment.id} rule 不在 AuditRule 枚举内", "reject_and_retry", segment.id)
            )

    total_duration = sum(max(0.0, seg.end - seg.start) for seg in original_segments)
    delete_duration = sum(planned_delete_duration(seg) for seg in audited_segments)
    delete_ratio = delete_duration / total_duration if total_duration > 0 else 0.0
    if delete_ratio > 0.5:
        issues.append(
            _issue(
                "AS-06",
                "major",
                f"delete 时长占比 {delete_ratio:.1%} 超过 50%，需要人工复核",
                "flag_for_human",
            )
        )

    blocking = [issue for issue in issues if issue.on_fail in {"reject", "reject_and_retry"}]
    return ContractReport(
        contract_id="goldenclip.audit_segments@1",
        passed=not blocking,
        issues=issues,
        metrics={"delete_duration_ratio": round(delete_ratio, 4)},
        flagged_for_human=any(issue.on_fail == "flag_for_human" for issue in issues),
    )


def validate_audit_edit_plan_contract(
    original_segments: Sequence[Segment],
    edit_plan: AuditEditPlan,
    compiled_segments: Sequence[Segment],
) -> ContractReport:
    """Validate free-form semantic edit plans before post-processing applies split/merge."""
    issues: List[ContractIssue] = []
    original_by_id = {seg.id: seg for seg in original_segments}

    if not edit_plan.operations:
        issues.append(_issue("AE-01", "blocker", "语义编辑计划缺少 operations", "reject_and_retry"))

    covered_source_ids: set[str] = set()
    for operation in edit_plan.operations:
        source_ids = list(operation.source_ids or ([operation.source_id] if operation.source_id else []))
        source_ids.extend(item.segment_id for item in operation.source_word_ranges)
        source_ids = [source_id for source_id in source_ids if source_id]
        if not source_ids:
            issues.append(_issue("AE-01", "blocker", f"操作 {operation.id} 缺少来源片段", "reject_and_retry", operation.id))
            continue

        missing = [source_id for source_id in source_ids if source_id not in original_by_id]
        if missing:
            issues.append(
                _issue("AE-02", "blocker", f"操作 {operation.id} 引用了不存在的 source_ids: {missing}", "reject_and_retry", operation.id)
            )
            continue
        covered_source_ids.update(source_ids)

        if operation.op == "delete_words":
            if not operation.source_word_ranges or not operation.reason.strip():
                issues.append(_issue("AE-03", "blocker", "词级删除必须包含字索引范围与原因", "reject_and_retry", operation.id))
            for source_id in set(source_ids):
                ranges = [item for item in operation.source_word_ranges if item.segment_id == source_id]
                error = word_deletion_error(original_by_id[source_id].model_copy(update={"action": SegmentAction.KEEP}), ranges)
                if error or not ranges:
                    issues.append(_issue("AE-03", "blocker", error or "词级删除来源缺少对应字范围", "reject_and_retry", operation.id))
            conflicting = [
                other for other in edit_plan.operations
                if other.op != "delete_words"
                and set(source_ids).intersection(other.source_ids or ([other.source_id] if other.source_id else []))
            ]
            if conflicting:
                issues.append(_issue("AE-03", "blocker", "同一来源存在与词级删除冲突的操作", "reject_and_retry", operation.id))

        if operation.op == "split_at":
            source = original_by_id[source_ids[0]]
            split_before = (operation.split_before or "").strip()
            if not split_before or split_before not in (source.text or ""):
                issues.append(
                    _issue("AE-03", "blocker", f"操作 {operation.id} 的 split_before 不在原文中", "reject_and_retry", operation.id)
                )

        if operation.op == "merge_span":
            indices = [list(original_by_id.keys()).index(source_id) for source_id in source_ids]
            if sorted(indices) != list(range(min(indices), max(indices) + 1)):
                issues.append(_issue("AE-04", "blocker", f"操作 {operation.id} merge_span 不是相邻片段", "reject_and_retry", operation.id))
            speakers = {
                original_by_id[source_id].speaker
                for source_id in source_ids
                if original_by_id[source_id].speaker
            }
            if len(speakers) > 1:
                issues.append(_issue("AE-04", "major", f"操作 {operation.id} 跨说话人合并，需要人工复核", "flag_for_human", operation.id))

        if operation.op == "text_fix" and not (operation.display_text or "").strip():
            issues.append(_issue("AE-05", "blocker", f"操作 {operation.id} text_fix 缺少 display_text", "reject_and_retry", operation.id))

    uncovered = sorted(set(original_by_id) - covered_source_ids)
    if uncovered:
        issues.append(
            _issue(
                "AE-01",
                "blocker",
                f"语义编辑计划未显式覆盖全部输入片段: {uncovered[:5]}",
                "reject_and_retry",
            )
        )

    for segment in compiled_segments:
        if segment.start > segment.end:
            issues.append(_issue("AE-06", "blocker", f"片段 {segment.id} 时间范围非法", "reject", segment.id))
        if segment.action == SegmentAction.TEXT_FIX and not (segment.display_text or "").strip():
            issues.append(_issue("AE-05", "blocker", f"片段 {segment.id} text_fix 缺少 display_text", "reject_and_retry", segment.id))
        if segment.action == SegmentAction.SPLIT:
            source_id = (segment.source_segment_ids or [segment.id])[0]
            original = original_by_id.get(source_id) or original_by_id.get(segment.id)
            if original and (segment.split_before or "") not in (original.text or ""):
                issues.append(_issue("AE-03", "blocker", f"片段 {segment.id} split_before 不在来源原文中", "reject_and_retry", segment.id))

    delete_duration = sum(planned_delete_duration(segment) for segment in compiled_segments)
    total_duration = sum(max(0.0, segment.end - segment.start) for segment in original_segments)
    delete_ratio = delete_duration / total_duration if total_duration > 0 else 0.0
    if delete_ratio > 0.65:
        issues.append(_issue("AE-06", "major", f"语义编辑计划删除时长占比 {delete_ratio:.1%}", "flag_for_human"))

    blocking = [issue for issue in issues if issue.on_fail in {"reject", "reject_and_retry"}]
    return ContractReport(
        contract_id="goldenclip.audit_edit_plan@1",
        passed=not blocking,
        issues=issues,
        metrics={"delete_duration_ratio": round(delete_ratio, 4)},
        flagged_for_human=any(issue.on_fail == "flag_for_human" for issue in issues),
    )


def validate_audit_coherence_contract(
    original_segments: Sequence[Segment],
    repaired_segments: Sequence[Segment],
) -> ContractReport:
    """Validate the coherence repair pass before split/merge post-processing mutates ids."""
    issues: List[ContractIssue] = []
    original_by_id = {seg.id: seg for seg in original_segments}
    original_ids = [seg.id for seg in original_segments]
    repaired_ids = [seg.id for seg in repaired_segments]

    missing = sorted(set(original_ids) - set(repaired_ids))
    extra = sorted(set(repaired_ids) - set(original_ids))
    if len(repaired_segments) != len(original_segments) or missing or extra or _has_duplicates(repaired_ids):
        issues.append(
            _issue(
                "AC-01",
                "blocker",
                f"连贯性修复覆盖不完整: missing={missing[:5]}, extra={extra[:5]}, duplicate={_has_duplicates(repaired_ids)}",
                "reject_and_retry",
            )
        )

    for segment in repaired_segments:
        original = original_by_id.get(segment.id)
        if not original:
            continue
        immutable_changed = (
            segment.start != original.start
            or segment.end != original.end
            or segment.text != original.text
            or _word_signature(segment) != _word_signature(original)
        )
        if immutable_changed:
            issues.append(
                _issue("AC-02", "blocker", f"连贯性修复改写了片段 {segment.id} 的 ASR 不可变字段", "reject", segment.id)
            )

        if segment.action == SegmentAction.TEXT_FIX:
            if not (segment.display_text or "").strip() or segment.fix_type != "asr_error":
                issues.append(
                    _issue("AC-03", "blocker", f"text_fix 片段 {segment.id} 缺少 display_text 或 fix_type", "reject_and_retry", segment.id)
                )
        elif segment.action == SegmentAction.SPLIT:
            split_before = (segment.split_before or "").strip()
            if not split_before or split_before not in (original.text or ""):
                issues.append(
                    _issue("AC-04", "blocker", f"split 片段 {segment.id} 的 split_before 不在原文中", "reject_and_retry", segment.id)
                )
        elif segment.action == SegmentAction.DELETE:
            if not (segment.reason or "").strip() or not segment.rule:
                issues.append(
                    _issue("AC-05", "blocker", f"delete 片段 {segment.id} 缺少 reason 或 rule", "reject_and_retry", segment.id)
                )

    total_duration = sum(max(0.0, seg.end - seg.start) for seg in original_segments)
    added_keep_duration = 0.0
    for repaired in repaired_segments:
        original = original_by_id.get(repaired.id)
        if not original:
            continue
        if original.action == SegmentAction.DELETE and repaired.action != SegmentAction.DELETE:
            added_keep_duration += max(0.0, repaired.end - repaired.start)
    added_keep_ratio = added_keep_duration / total_duration if total_duration > 0 else 0.0
    if added_keep_ratio > 0.25:
        issues.append(
            _issue(
                "AC-06",
                "major",
                f"连贯性复核新增保留时长占比 {added_keep_ratio:.1%}，需要人工复核",
                "flag_for_human",
            )
        )

    blocking = [issue for issue in issues if issue.on_fail in {"reject", "reject_and_retry"}]
    return ContractReport(
        contract_id="goldenclip.audit_coherence@1",
        passed=not blocking,
        issues=issues,
        metrics={"added_keep_duration_ratio": round(added_keep_ratio, 4)},
        flagged_for_human=any(issue.on_fail == "flag_for_human" for issue in issues),
    )


def validate_translation_payload_contract(
    input_cues: Sequence[TranslationCue],
    payload: Sequence[Dict[str, Any]],
) -> ContractReport:
    """Validate raw LLM cue payload before applying it to TranslationCue objects."""
    issues: List[ContractIssue] = []
    by_id = {cue.id: cue for cue in input_cues}
    payload_ids = [str(item.get("id")) for item in payload if isinstance(item, dict) and item.get("id") is not None]

    for item in payload:
        if not isinstance(item, dict):
            issues.append(_issue("TC-01", "blocker", "字幕模型输出数组中存在非对象条目", "reject_and_retry"))
            continue
        missing_fields = [field for field in ("id", "calibrated_text", "translated_text") if not item.get(field)]
        if missing_fields:
            issues.append(
                _issue("TC-01", "blocker", f"字幕模型输出缺少字段: {missing_fields}", "reject_and_retry", str(item.get("id")))
            )

    missing_ids = sorted(set(by_id.keys()) - set(payload_ids))
    extra_ids = sorted(set(payload_ids) - set(by_id.keys()))
    if missing_ids or extra_ids or _has_duplicates(payload_ids):
        issues.append(
            _issue(
                "TC-02",
                "blocker",
                f"cue id 集合不一致: missing={missing_ids[:5]}, extra={extra_ids[:5]}, duplicate={_has_duplicates(payload_ids)}",
                "reject_and_retry",
            )
        )

    for item in payload:
        if not isinstance(item, dict):
            continue
        cue = by_id.get(str(item.get("id")))
        if not cue:
            continue
        changed_fields = []
        if "start" in item and item.get("start") != cue.start:
            changed_fields.append("start")
        if "end" in item and item.get("end") != cue.end:
            changed_fields.append("end")
        if "source_text" in item and item.get("source_text") != cue.source_text:
            changed_fields.append("source_text")
        if changed_fields:
            issues.append(
                _issue("TC-03", "blocker", f"cue {cue.id} 改写了不可变字段: {changed_fields}", "reject", cue.id)
            )

    metrics = _translation_length_metrics(input_cues, payload)
    if metrics["overlong_translation_ratio"] > 0.1:
        issues.append(
            _issue(
                "TC-05",
                "major",
                f"译文过长占比 {metrics['overlong_translation_ratio']:.1%} 超过 10%",
                "reject_and_retry",
            )
        )

    blocking = [issue for issue in issues if issue.on_fail in {"reject", "reject_and_retry"}]
    return ContractReport(
        contract_id="goldenclip.translation_cues@1",
        passed=not blocking,
        issues=issues,
        metrics=metrics,
        flagged_for_human=any(issue.on_fail == "flag_for_human" for issue in issues),
    )


def validate_translation_cues_contract(
    original_cues: Sequence[TranslationCue],
    output_cues: Sequence[TranslationCue],
) -> ContractReport:
    payload = [
        {
            "id": cue.id,
            "calibrated_text": cue.calibrated_text,
            "translated_text": cue.translated_text,
            "start": cue.start,
            "end": cue.end,
            "source_text": cue.source_text,
        }
        for cue in output_cues
    ]
    report = validate_translation_payload_contract(original_cues, payload)
    issues = list(report.issues)
    for cue in output_cues:
        if not (cue.calibrated_text or "").strip() or not (cue.translated_text or "").strip():
            issues.append(
                _issue("TC-04", "blocker", f"cue {cue.id} 校准或翻译文本为空", "reject_and_retry", cue.id)
            )
    blocking = [issue for issue in issues if issue.on_fail in {"reject", "reject_and_retry"}]
    return report.model_copy(update={"passed": not blocking, "issues": issues})


def validate_product_groups_contract(
    occurrences: Sequence[ProductOccurrence],
    groups: Sequence[ProductGroup],
    segments: Sequence[Segment],
) -> ContractReport:
    """Validate that global product grouping preserves occurrence and segment lineage."""
    issues: List[ContractIssue] = []
    occurrence_by_id = {item.occurrence_id: item for item in occurrences}
    segment_by_id = {item.id: item for item in segments}
    group_ids = [item.product_group_id for item in groups]
    if _has_duplicates(group_ids):
        issues.append(
            _issue("PG-01", "blocker", "product_group_id 存在重复", "reject_and_retry")
        )

    assigned_occurrence_ids: List[str] = []
    for group in groups:
        if not group.canonical_name.strip() or not any(
            alias.strip() for alias in group.aliases
        ):
            issues.append(
                _issue(
                    "PG-05",
                    "blocker",
                    f"产品组 {group.product_group_id} 缺少 canonical_name 或 aliases",
                    "reject_and_retry",
                    group.product_group_id,
                )
            )
        if len(set(group.aliases)) != len(group.aliases):
            issues.append(
                _issue(
                    "PG-05",
                    "major",
                    f"产品组 {group.product_group_id} aliases 重复",
                    "flag_for_human",
                    group.product_group_id,
                )
            )

        missing_occurrences = [
            occurrence_id
            for occurrence_id in group.occurrence_ids
            if occurrence_id not in occurrence_by_id
        ]
        if missing_occurrences:
            issues.append(
                _issue(
                    "PG-02",
                    "blocker",
                    f"产品组引用不存在的 occurrence: {missing_occurrences[:5]}",
                    "reject_and_retry",
                    group.product_group_id,
                )
            )
        assigned_occurrence_ids.extend(group.occurrence_ids)

        expected_segment_ids = {
            segment_id
            for occurrence_id in group.occurrence_ids
            if occurrence_id in occurrence_by_id
            for segment_id in occurrence_by_id[occurrence_id].segment_ids
        }
        actual_segment_ids = set(group.segment_ids)
        unknown_segment_ids = sorted(actual_segment_ids - set(segment_by_id))
        if unknown_segment_ids or actual_segment_ids != expected_segment_ids:
            issues.append(
                _issue(
                    "PG-03",
                    "blocker",
                    (
                        f"产品组 segment lineage 不一致: unknown={unknown_segment_ids[:5]}, "
                        f"missing={sorted(expected_segment_ids - actual_segment_ids)[:5]}, "
                        f"extra={sorted(actual_segment_ids - expected_segment_ids)[:5]}"
                    ),
                    "reject_and_retry",
                    group.product_group_id,
                )
            )

        ordered_ids = sorted(
            (segment_id for segment_id in group.segment_ids if segment_id in segment_by_id),
            key=lambda segment_id: (
                segment_by_id[segment_id].start,
                segment_by_id[segment_id].end,
                segment_id,
            ),
        )
        if ordered_ids != list(group.segment_ids):
            issues.append(
                _issue(
                    "PG-04",
                    "blocker",
                    f"产品组 {group.product_group_id} 的 segment_ids 未按源时间排序",
                    "reject_and_retry",
                    group.product_group_id,
                )
            )
        if ordered_ids:
            expected_start = segment_by_id[ordered_ids[0]].start
            expected_end = segment_by_id[ordered_ids[-1]].end
            if group.start != expected_start or group.end != expected_end:
                issues.append(
                    _issue(
                        "PG-04",
                        "blocker",
                        f"产品组 {group.product_group_id} 的时间边界与片段不一致",
                        "reject_and_retry",
                        group.product_group_id,
                    )
                )

    occurrence_ids = set(occurrence_by_id)
    assigned_ids = set(assigned_occurrence_ids)
    if (
        assigned_ids != occurrence_ids
        or len(assigned_occurrence_ids) != len(assigned_ids)
    ):
        issues.append(
            _issue(
                "PG-02",
                "blocker",
                (
                    f"occurrence 必须且只能属于一个产品组: "
                    f"missing={sorted(occurrence_ids - assigned_ids)[:5]}, "
                    f"duplicate={_has_duplicates(assigned_occurrence_ids)}"
                ),
                "reject_and_retry",
            )
        )

    rejected = [
        issue for issue in issues if issue.on_fail in {"reject", "reject_and_retry"}
    ]
    return ContractReport(
        contract_id="goldenclip.product_groups@1",
        passed=not rejected,
        issues=issues,
        metrics={
            "occurrences_count": len(occurrences),
            "product_groups_count": len(groups),
        },
        flagged_for_human=any(
            issue.on_fail == "flag_for_human" for issue in issues
        ),
    )


def _translation_length_metrics(
    input_cues: Sequence[TranslationCue],
    payload: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    by_id = {cue.id: cue for cue in input_cues}
    checked = 0
    overlong = 0
    for item in payload:
        if not isinstance(item, dict):
            continue
        cue = by_id.get(str(item.get("id")))
        translated = str(item.get("translated_text") or "")
        if not cue or not translated:
            continue
        checked += 1
        duration = max(0.2, cue.end - cue.start)
        estimated_read_seconds = len(translated) / 12.0
        if estimated_read_seconds > duration * 2:
            overlong += 1
    ratio = overlong / checked if checked else 0.0
    return {
        "checked_cues": checked,
        "overlong_cues": overlong,
        "overlong_translation_ratio": round(ratio, 4),
    }


def validate_highlight_reels_contract(
    reels: Sequence[HighlightReel],
    segments: Sequence[Segment],
    video_duration: Optional[float] = None,
    options: Optional[HighlightReelBuildOptions] = None,
    product_occurrences: Optional[Sequence[ProductOccurrence]] = None,
    product_groups: Optional[Sequence[ProductGroup]] = None,
) -> ContractReport:
    report: HighlightReelQualityReport = validate_highlight_reels_code(
        list(reels),
        list(segments),
        video_duration,
        options,
        list(product_occurrences) if product_occurrences is not None else None,
        list(product_groups) if product_groups is not None else None,
    )
    issues = [
        ContractIssue(
            assertion_id=issue.code,
            severity=issue.severity,
            message=issue.message,
            target_id=issue.reel_id,
            on_fail="reject_and_retry" if issue.severity == "blocker" else "flag_for_human",
        )
        for issue in report.issues
    ]
    rejected = [
        issue for issue in issues if issue.on_fail in {"reject", "reject_and_retry"}
    ]
    return ContractReport(
        contract_id=(
            "goldenclip.highlight_reels@3"
            if product_groups is not None
            else "goldenclip.highlight_reels@2"
        ),
        passed=not rejected,
        issues=issues,
        metrics={"score": report.score},
        flagged_for_human=any(issue.severity in {"major", "minor"} for issue in issues),
    )


def format_contract_issues(issues: Iterable[ContractIssue], limit: int = 5) -> str:
    shown = list(issues)[:limit]
    return "; ".join(f"{issue.assertion_id}: {issue.message}" for issue in shown)


def implemented_code_assertion_ids(contract_id: str) -> set[str]:
    return set(IMPLEMENTED_CODE_ASSERTIONS.get(contract_id, set()))
