"""Compile LLM semantic edit plans into safe Segment decisions."""

from __future__ import annotations

import json
import re
from typing import Dict, List, Optional, Tuple

from ..models.task import (
    AuditEditOperation,
    AuditEditPlan,
    AuditRule,
    CompiledAuditResult,
    ProductSemanticMark,
    Segment,
    SegmentAction,
    SourceWordRange,
)


ACTION_TO_OPERATION = {
    "keep": "keep_span",
    "k": "keep_span",
    "delete": "delete_span",
    "d": "delete_span",
    "split": "split_at",
    "sp": "split_at",
    "merge_next": "merge_span",
    "mn": "merge_span",
    "text_fix": "text_fix",
    "tf": "text_fix",
}
RULE_NUMBER_TO_AUDIT_RULE = {
    1: AuditRule.RETAKE,
    2: AuditRule.FRAGMENT,
    3: AuditRule.FILLER,
    4: AuditRule.STUTTER,
    5: AuditRule.DEDUP,
    6: AuditRule.INTRA_REPEAT,
    7: AuditRule.QA_INTEGRITY,
    8: AuditRule.PACING,
}


def parse_audit_edit_plan(response_text: str, idx_to_id: Optional[Dict[int, str]] = None) -> Optional[AuditEditPlan]:
    """Parse a JSON object response as AuditEditPlan; return None for legacy array outputs."""
    parsed = _parse_json(response_text)
    if not isinstance(parsed, dict) or "operations" not in parsed:
        return None

    operations = []
    for raw in parsed.get("operations", []):
        if not isinstance(raw, dict):
            continue
        normalized = _normalize_operation(raw, idx_to_id)
        try:
            operations.append(AuditEditOperation(**normalized))
        except Exception:
            continue

    if not operations:
        return AuditEditPlan(
            operations=[],
            term_fixes=parsed.get("term_fixes") or [],
            coherence_notes=parsed.get("coherence_notes") or [],
            target_retention=parsed.get("target_retention"),
            validator_model=parsed.get("validator_model"),
        )

    return AuditEditPlan(
        operations=operations,
        term_fixes=parsed.get("term_fixes") or [],
        coherence_notes=parsed.get("coherence_notes") or [],
        target_retention=parsed.get("target_retention"),
        validator_model=parsed.get("validator_model"),
    )


def compile_audit_edit_plan(
    original_segments: List[Segment],
    edit_plan: AuditEditPlan,
    default_action: SegmentAction = SegmentAction.KEEP,
    default_delete_reason: str = "未被语义编辑计划选中保留",
    default_delete_rule: AuditRule = AuditRule.DEDUP,
) -> CompiledAuditResult:
    """Compile an edit plan to Segment decisions. Split/merge are executed later by post-processing."""
    compiled = [
        _reset_segment(
            segment,
            default_action=default_action,
            default_delete_reason=default_delete_reason,
            default_delete_rule=default_delete_rule,
        )
        for segment in original_segments
    ]
    index_by_id = {segment.id: idx for idx, segment in enumerate(compiled)}
    warnings: List[str] = []

    for operation in edit_plan.operations:
        source_ids = _operation_source_ids(operation)
        if not source_ids:
            warnings.append(f"{operation.id}: 缺少 source_id/source_ids")
            continue

        missing = [source_id for source_id in source_ids if source_id not in index_by_id]
        if missing:
            warnings.append(f"{operation.id}: source_ids 不存在: {missing}")
            continue

        if operation.op == "keep_span":
            _apply_keep_span(compiled, index_by_id, source_ids, operation)
        elif operation.op == "delete_span":
            _apply_delete_span(compiled, index_by_id, source_ids, operation)
        elif operation.op == "delete_words":
            for word_range in operation.source_word_ranges:
                if word_range.segment_id in index_by_id:
                    segment = compiled[index_by_id[word_range.segment_id]]
                    segment.word_deletions.append(word_range.model_copy(deep=True))
                    segment.rule = operation.rule or AuditRule.FILLER
                    _apply_common_metadata(segment, operation, [word_range.segment_id])
        elif operation.op == "split_at":
            _apply_split_at(compiled, index_by_id, source_ids[0], operation)
        elif operation.op == "merge_span":
            _apply_merge_span(compiled, index_by_id, source_ids, operation, warnings)
        elif operation.op == "text_fix":
            _apply_text_fix(compiled, index_by_id, source_ids[0], operation)
        elif operation.op == "subtitle_line_break":
            _apply_subtitle_line_break(compiled, index_by_id, source_ids[0], operation)

    return CompiledAuditResult(
        segments=compiled,
        edit_plan=edit_plan,
        warnings=warnings,
    )


def _parse_json(response_text: str):
    json_text = response_text.strip()
    json_text = re.sub(r"^```(?:json)?\s*", "", json_text, flags=re.MULTILINE)
    json_text = re.sub(r"\s*```$", "", json_text, flags=re.MULTILINE).strip()
    try:
        return json.loads(json_text)
    except json.JSONDecodeError:
        match = re.search(r"(\{.*\}|\[.*\])", json_text, re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            return None


def _normalize_operation(raw: dict, idx_to_id: Optional[Dict[int, str]]) -> dict:
    normalized = dict(raw)
    op = str(normalized.get("op") or normalized.get("action") or normalized.get("a") or "").strip()
    normalized["op"] = ACTION_TO_OPERATION.get(op, op)
    rule = normalized.get("rule")
    if rule is not None and not isinstance(rule, AuditRule):
        rule_match = re.search(r"\bRule\s*([1-8])\b", str(rule), flags=re.IGNORECASE)
        if rule_match:
            normalized["rule"] = RULE_NUMBER_TO_AUDIT_RULE[int(rule_match.group(1))]

    source_ids = list(normalized.get("source_ids") or [])
    if normalized.get("source_id") and not source_ids:
        source_ids = [normalized.get("source_id")]
    if normalized.get("source_word_ranges") and not source_ids:
        source_ids = list(dict.fromkeys(item.get("segment_id") for item in normalized["source_word_ranges"] if isinstance(item, dict)))
    if normalized.get("id") and not source_ids:
        source_ids = [normalized.get("id")]
    if normalized.get("i") is not None and not source_ids:
        source_ids = [_idx_to_id(normalized.get("i"), idx_to_id)]
    normalized["source_ids"] = [
        _idx_to_id(source_id, idx_to_id)
        for source_id in source_ids
        if source_id is not None
    ]
    normalized["source_id"] = normalized["source_ids"][0] if normalized["source_ids"] else None

    if "reason" not in normalized and "r" in normalized:
        normalized["reason"] = normalized["r"]
    if "display_text" not in normalized and "corrected_text" in normalized:
        normalized["display_text"] = normalized["corrected_text"]
    if "product_semantic" not in normalized and "semantic" in normalized:
        normalized["product_semantic"] = normalized["semantic"]
    normalized["product_semantic"] = _normalize_product_semantic_payload(normalized)
    return normalized


def _normalize_product_semantic_payload(raw: dict) -> Optional[dict]:
    payload = raw.get("product_semantic")
    if payload is None:
        payload = raw.get("semantic")
    if isinstance(payload, ProductSemanticMark):
        return payload.model_dump()
    if not isinstance(payload, dict):
        payload = {}
    else:
        payload = dict(payload)

    roles = _as_string_list(payload.get("roles") or payload.get("role"))
    text_role = raw.get("text_role")
    if text_role and str(text_role) not in roles:
        roles.append(str(text_role))
    style_role = _role_from_style(raw.get("style"))
    if style_role and style_role not in roles:
        roles.append(style_role)

    entities = payload.get("product_entities") or payload.get("products") or []
    normalized_entities: List[dict] = []
    if isinstance(entities, list):
        for entity in entities:
            if isinstance(entity, dict):
                name = entity.get("name") or entity.get("product_name") or entity.get("canonical_name")
                if name:
                    normalized_entities.append(
                        {
                            "name": str(name),
                            "canonical_name": entity.get("canonical_name") or entity.get("canonical"),
                            "category": entity.get("category"),
                        }
                    )
            elif entity:
                normalized_entities.append({"name": str(entity)})

    product_name = raw.get("product_name") or payload.get("product_name")
    if not product_name and raw.get("clip_title"):
        product_name = _product_name_from_title(str(raw.get("clip_title")))
    if product_name and not any(item.get("name") == str(product_name) for item in normalized_entities):
        normalized_entities.insert(
            0,
            {
                "name": str(product_name),
                "canonical_name": raw.get("canonical_product_name") or payload.get("canonical_product_name"),
                "category": raw.get("product_category") or payload.get("product_category"),
            },
        )

    selling_points = _as_string_list(
        payload.get("selling_points")
        or payload.get("selling_point")
        or raw.get("selling_points")
        or raw.get("selling_point")
    )

    if not (roles or normalized_entities or selling_points or payload.get("angle_label") or raw.get("clip_title")):
        return None

    return {
        "roles": roles,
        "product_entities": normalized_entities,
        "selling_points": selling_points,
        "angle_id": payload.get("angle_id") or raw.get("angle_id"),
        "angle_label": payload.get("angle_label") or raw.get("angle_label") or raw.get("clip_title"),
        "eligible_for_highlight": bool(payload.get("eligible_for_highlight", raw.get("eligible_for_highlight", True))),
        "exclude_reason": payload.get("exclude_reason") or raw.get("exclude_reason"),
        "confidence": payload.get("confidence") or raw.get("confidence"),
        "source": payload.get("source") or "audit",
    }


def _as_string_list(value) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list):
        return [str(item) for item in value if item]
    return [str(value)] if value else []


def _role_from_style(style) -> Optional[str]:
    return {
        "cold_open": "product_opening",
        "setup": "context",
        "highlight": "selling_point",
        "punchline": "selling_point",
        "resolution": "closing",
    }.get(str(style or ""))


def _product_name_from_title(title: str) -> str:
    cleaned = re.split(r"为什么|怎么|如何|：|:", title, maxsplit=1)[0]
    return cleaned.strip(" ，,。")[:32] or title.strip()[:32]


def _idx_to_id(value, idx_to_id: Optional[Dict[int, str]]) -> str:
    if isinstance(value, int) and idx_to_id:
        return idx_to_id.get(value, str(value))
    if isinstance(value, str) and value.isdigit() and idx_to_id:
        return idx_to_id.get(int(value), value)
    if isinstance(value, str) and idx_to_id:
        match = re.fullmatch(r"seg[_-]?0*(\d+)", value.strip(), flags=re.IGNORECASE)
        if match:
            numeric_id = int(match.group(1))
            return idx_to_id.get(numeric_id, value)
    return str(value)


def _reset_segment(
    segment: Segment,
    *,
    default_action: SegmentAction = SegmentAction.KEEP,
    default_delete_reason: str = "未被语义编辑计划选中保留",
    default_delete_rule: AuditRule = AuditRule.DEDUP,
) -> Segment:
    copied = segment.model_copy(deep=True)
    copied.action = default_action
    copied.reason = None
    copied.rule = None
    copied.style = None
    copied.clip_group = None
    copied.clip_title = None
    copied.display_text = None
    copied.fix_type = None
    copied.split_before = None
    copied.split_part1_action = SegmentAction.KEEP
    copied.split_part2_action = SegmentAction.KEEP
    copied.subtitle_line_break = None
    copied.merged = False
    copied.user_override = False
    copied.manual_action = None
    copied.claude_action = None
    copied.claude_reason = None
    copied.source_segment_ids = [segment.id]
    copied.source_word_ranges = [
        SourceWordRange(
            segment_id=segment.id,
            start_word_index=0 if segment.words else None,
            end_word_index=len(segment.words) if segment.words else None,
        )
    ]
    copied.compiled_from_operation = None
    copied.word_deletions = []
    copied.semantic_unit_id = None
    copied.product_semantic = None
    if default_action == SegmentAction.DELETE:
        copied.reason = default_delete_reason
        copied.rule = default_delete_rule
        copied.claude_action = SegmentAction.DELETE
        copied.claude_reason = default_delete_reason
    return copied


def _operation_source_ids(operation: AuditEditOperation) -> List[str]:
    if operation.source_ids:
        return list(operation.source_ids)
    if operation.source_id:
        return [operation.source_id]
    return [item.segment_id for item in operation.source_word_ranges]


def _apply_common_metadata(segment: Segment, operation: AuditEditOperation, source_ids: List[str]) -> None:
    segment.reason = operation.reason or segment.reason
    segment.semantic_unit_id = operation.semantic_unit_id
    segment.compiled_from_operation = operation.id
    segment.source_segment_ids = list(source_ids)
    if operation.style:
        segment.style = operation.style
    if operation.clip_group is not None:
        segment.clip_group = operation.clip_group
    if operation.clip_title is not None:
        segment.clip_title = operation.clip_title
    if operation.product_semantic:
        segment.product_semantic = operation.product_semantic.model_copy(deep=True)
    elif operation.text_role:
        segment.product_semantic = ProductSemanticMark(
            roles=[operation.text_role],
            source="audit_legacy",
        )


def _apply_keep_span(
    compiled: List[Segment],
    index_by_id: Dict[str, int],
    source_ids: List[str],
    operation: AuditEditOperation,
) -> None:
    for source_id in source_ids:
        segment = compiled[index_by_id[source_id]]
        segment.action = SegmentAction.KEEP
        segment.rule = None
        _apply_common_metadata(segment, operation, source_ids)


def _apply_delete_span(
    compiled: List[Segment],
    index_by_id: Dict[str, int],
    source_ids: List[str],
    operation: AuditEditOperation,
) -> None:
    for source_id in source_ids:
        segment = compiled[index_by_id[source_id]]
        segment.action = SegmentAction.DELETE
        segment.rule = operation.rule or segment.rule or AuditRule.DEDUP
        segment.clip_group = None
        segment.clip_title = None
        _apply_common_metadata(segment, operation, source_ids)


def _apply_split_at(
    compiled: List[Segment],
    index_by_id: Dict[str, int],
    source_id: str,
    operation: AuditEditOperation,
) -> None:
    segment = compiled[index_by_id[source_id]]
    segment.action = SegmentAction.SPLIT
    segment.split_before = operation.split_before
    segment.split_part1_action = operation.split_part1_action
    segment.split_part2_action = operation.split_part2_action
    if segment.split_part1_action == SegmentAction.DELETE or segment.split_part2_action == SegmentAction.DELETE:
        segment.rule = operation.rule or segment.rule or AuditRule.FRAGMENT
    _apply_common_metadata(segment, operation, [source_id])


def _apply_merge_span(
    compiled: List[Segment],
    index_by_id: Dict[str, int],
    source_ids: List[str],
    operation: AuditEditOperation,
    warnings: List[str],
) -> None:
    indices = [index_by_id[source_id] for source_id in source_ids]
    if sorted(indices) != list(range(min(indices), max(indices) + 1)):
        warnings.append(f"{operation.id}: merge_span 只能合并相邻片段")
        return
    for source_id in source_ids[:-1]:
        segment = compiled[index_by_id[source_id]]
        segment.action = SegmentAction.MERGE_NEXT
        segment.rule = None
        _apply_common_metadata(segment, operation, source_ids)
    last = compiled[index_by_id[source_ids[-1]]]
    if last.action != SegmentAction.DELETE:
        last.action = SegmentAction.KEEP
        _apply_common_metadata(last, operation, source_ids)


def _apply_text_fix(
    compiled: List[Segment],
    index_by_id: Dict[str, int],
    source_id: str,
    operation: AuditEditOperation,
) -> None:
    segment = compiled[index_by_id[source_id]]
    segment.action = SegmentAction.TEXT_FIX
    segment.display_text = operation.display_text or ""
    segment.fix_type = "asr_error"
    segment.rule = None
    _apply_common_metadata(segment, operation, [source_id])


def _apply_subtitle_line_break(
    compiled: List[Segment],
    index_by_id: Dict[str, int],
    source_id: str,
    operation: AuditEditOperation,
) -> None:
    segment = compiled[index_by_id[source_id]]
    segment.action = SegmentAction.KEEP
    segment.subtitle_line_break = operation.subtitle_line_break
    segment.rule = None
    _apply_common_metadata(segment, operation, [source_id])
