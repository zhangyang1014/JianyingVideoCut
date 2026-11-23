"""把旧审计与集锦结果确定性投影到同一 ASR 词序列的新分段。"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

from ..models.task import (
    HighlightReel,
    HighlightReelQualityReport,
    HighlightReelStatus,
    ProductGroup,
    ProductOccurrence,
    ResegmentInheritanceReport,
    Segment,
    SegmentAction,
)
from .highlight_reel_plan import rebuild_reel_metrics, validate_highlight_reels_code
from .highlight_reel_plan import product_group_as_occurrence
from .product_occurrence_detector import group_product_occurrences


_TAG_RE = re.compile(r"<(?:STU|FIL)(?:\s+[^>]*)?>")
_STRUCTURAL_ACTIONS = {SegmentAction.SPLIT, SegmentAction.MERGE_NEXT}
_TEXT_FIX_ACTIONS = {SegmentAction.SUBTITLE_FIX, SegmentAction.TEXT_FIX}


@dataclass
class ResegmentInheritanceResult:
    segments: List[Segment]
    occurrences: List[ProductOccurrence]
    product_groups: List[ProductGroup]
    reels: List[HighlightReel]
    reels_before_quality: List[HighlightReel]
    quality_report: Optional[HighlightReelQualityReport]
    quality_report_before: Optional[HighlightReelQualityReport]
    golden_quote_order: List[str]
    old_to_new_ids: Dict[str, List[str]]
    report: ResegmentInheritanceReport


def _clean_word(value: str) -> str:
    return _TAG_RE.sub("", value or "").strip()


def _word_keys(segment: Segment) -> List[Tuple[float, float, str]]:
    return [
        (round(word.start, 3), round(word.end, 3), _clean_word(word.word))
        for word in (segment.words or [])
        if _clean_word(word.word)
    ]


def _temporal_overlap(left: Segment, right: Segment) -> float:
    return max(0.0, min(left.end, right.end) - max(left.start, right.start))


def _match_score(old: Segment, new: Segment) -> Tuple[int, float, bool]:
    old_keys = _word_keys(old)
    new_keys = _word_keys(new)
    if old_keys and new_keys:
        old_set = set(old_keys)
        common = sum(1 for key in new_keys if key in old_set)
        exact = len(old_keys) == len(new_keys) == common and old_keys == new_keys
        return common, common / max(1, len(new_keys)), exact

    overlap = _temporal_overlap(old, new)
    if overlap <= 0:
        return 0, 0.0, False
    coverage = overlap / max(0.001, new.end - new.start)
    exact = (
        abs(old.start - new.start) <= 0.001
        and abs(old.end - new.end) <= 0.001
        and (old.text or "").strip() == (new.text or "").strip()
    )
    return 1, coverage, exact


def _copy_decision(old: Segment, new: Segment, *, exact: bool) -> Segment:
    inherited = new.model_copy(deep=True)
    inherited.action = old.action
    inherited.reason = old.reason
    inherited.rule = old.rule
    inherited.user_override = old.user_override
    inherited.manual_action = old.manual_action
    inherited.claude_action = old.claude_action
    inherited.claude_reason = old.claude_reason
    inherited.style = old.style
    inherited.clip_group = old.clip_group
    inherited.clip_title = old.clip_title
    inherited.semantic_unit_id = old.semantic_unit_id
    inherited.product_semantic = old.product_semantic.model_copy(deep=True) if old.product_semantic else None

    if exact:
        inherited.display_text = old.display_text
        inherited.subtitle_text = old.subtitle_text
        inherited.subtitle_text_source = old.subtitle_text_source
        inherited.subtitle_updated_at = old.subtitle_updated_at
        inherited.fix_type = old.fix_type
        inherited.split_before = old.split_before
        inherited.split_part1_action = old.split_part1_action
        inherited.split_part2_action = old.split_part2_action
        inherited.subtitle_line_break = old.subtitle_line_break
        inherited.merged = old.merged
        inherited.source_segment_ids = list(old.source_segment_ids or [])
        inherited.source_word_ranges = [item.model_copy(deep=True) for item in (old.source_word_ranges or [])]
        inherited.compiled_from_operation = old.compiled_from_operation
    else:
        # 结构动作和逐字字幕修正依赖旧边界；保留语义决策，但不把旧文本强套给新卡片。
        if inherited.action in _STRUCTURAL_ACTIONS | _TEXT_FIX_ACTIONS:
            inherited.action = SegmentAction.KEEP
        if inherited.claude_action in _STRUCTURAL_ACTIONS | _TEXT_FIX_ACTIONS:
            inherited.claude_action = SegmentAction.KEEP
        inherited.manual_action = None

    return inherited


def _unique(values: Iterable[str]) -> List[str]:
    seen: set[str] = set()
    result: List[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _map_segment_ids(old_ids: Iterable[str], mapping: Dict[str, List[str]]) -> List[str]:
    return _unique(new_id for old_id in old_ids for new_id in mapping.get(old_id, []))


def _migrate_occurrences(
    occurrences: List[ProductOccurrence],
    mapping: Dict[str, List[str]],
    new_segments: List[Segment],
) -> List[ProductOccurrence]:
    segment_map = {segment.id: segment for segment in new_segments}
    migrated: List[ProductOccurrence] = []
    for occurrence in occurrences:
        segment_ids = _map_segment_ids(occurrence.segment_ids, mapping)
        selected = [segment_map[segment_id] for segment_id in segment_ids if segment_id in segment_map]
        if not selected:
            continue
        migrated.append(occurrence.model_copy(
            deep=True,
            update={
                "segment_ids": [segment.id for segment in selected],
                "start": min(segment.start for segment in selected),
                "end": max(segment.end for segment in selected),
            },
        ))
    return migrated


def _migrate_reels(
    reels: List[HighlightReel],
    mapping: Dict[str, List[str]],
    new_segments: List[Segment],
    occurrence_ids: set[str],
    target_content_revision: int,
) -> List[HighlightReel]:
    migrated: List[HighlightReel] = []
    for reel in reels:
        segment_ids = _map_segment_ids(reel.segment_ids, mapping)
        if not segment_ids:
            continue
        if reel.occurrence_id and reel.occurrence_id not in occurrence_ids:
            continue
        hook_ids = mapping.get(reel.hook_segment_id or "", [])
        updated = reel.model_copy(
            deep=True,
            update={
                "segment_ids": segment_ids,
                "hook_segment_id": hook_ids[0] if hook_ids else (segment_ids[0] if segment_ids else None),
                "source_content_revision": target_content_revision,
                "export_path": None,
                "exported_subtitle_revision": None,
                "exported_revision": None,
                "exported_job_id": None,
                "status": HighlightReelStatus.PENDING,
                "issues": [],
            },
        )
        migrated.append(rebuild_reel_metrics(updated, new_segments))
    return migrated


def _validate_reels(
    reels: List[HighlightReel],
    segments: List[Segment],
    occurrences: List[ProductOccurrence],
    video_duration: Optional[float],
    product_groups: Optional[List[ProductGroup]] = None,
) -> Tuple[List[HighlightReel], Optional[HighlightReelQualityReport]]:
    if not reels:
        return [], None
    report = validate_highlight_reels_code(
        reels,
        segments,
        video_duration=video_duration,
        product_occurrences=occurrences if product_groups is None else None,
        product_groups=product_groups,
    )
    issues_by_reel: Dict[str, list] = {}
    global_issues = []
    for issue in report.issues:
        if issue.reel_id:
            issues_by_reel.setdefault(issue.reel_id, []).append(issue)
        else:
            global_issues.append(issue)
    validated: List[HighlightReel] = []
    for reel in reels:
        issues = [*issues_by_reel.get(reel.reel_id, []), *global_issues]
        status = HighlightReelStatus.PASSED if not issues else HighlightReelStatus.WARNING
        validated.append(reel.model_copy(deep=True, update={"issues": issues, "status": status}))
    return validated, report


def inherit_resegment_results(
    old_segments: List[Segment],
    new_segments: List[Segment],
    *,
    occurrences: Optional[List[ProductOccurrence]] = None,
    reels: Optional[List[HighlightReel]] = None,
    reels_before_quality: Optional[List[HighlightReel]] = None,
    golden_quote_order: Optional[List[str]] = None,
    video_duration: Optional[float] = None,
    target_content_revision: int = 0,
    invalidated_export_count: int = 0,
) -> ResegmentInheritanceResult:
    """尽可能继承旧结果；歧义映射被保留但列入待复核报告。"""
    old_to_new: Dict[str, List[str]] = {segment.id: [] for segment in old_segments}
    inherited_segments: List[Segment] = []
    review_ids: List[str] = []
    unmapped_ids: List[str] = []
    exact_count = 0

    for new in new_segments:
        candidates = []
        for old in old_segments:
            common, coverage, exact = _match_score(old, new)
            if common > 0 and coverage > 0:
                candidates.append((old, common, coverage, exact))
        candidates.sort(key=lambda item: (item[3], item[1], item[2]), reverse=True)
        if not candidates:
            inherited_segments.append(new.model_copy(deep=True))
            unmapped_ids.append(new.id)
            continue

        dominant, _, coverage, exact = candidates[0]
        inherited = _copy_decision(dominant, new, exact=exact)
        inherited_segments.append(inherited)
        if exact:
            exact_count += 1
        if not exact or coverage < 0.999 or len(candidates) > 1:
            review_ids.append(new.id)

        for old, _, _, _ in candidates:
            old_to_new.setdefault(old.id, []).append(new.id)

    for old_id, ids in list(old_to_new.items()):
        old_to_new[old_id] = _unique(ids)

    migrated_occurrences = _migrate_occurrences(occurrences or [], old_to_new, inherited_segments)
    migrated_groups = group_product_occurrences(migrated_occurrences, inherited_segments)
    occurrence_ids = {
        occurrence.occurrence_id for occurrence in migrated_occurrences
    } | {group.product_group_id for group in migrated_groups}
    migrated_reels = _migrate_reels(
        reels or [],
        old_to_new,
        inherited_segments,
        occurrence_ids,
        target_content_revision,
    )
    migrated_before = _migrate_reels(
        reels_before_quality or [],
        old_to_new,
        inherited_segments,
        occurrence_ids,
        target_content_revision,
    )
    migrated_reels, quality_report = _validate_reels(
        migrated_reels,
        inherited_segments,
        [],
        video_duration,
        product_groups=migrated_groups if migrated_groups else None,
    )
    migrated_before, quality_report_before = _validate_reels(
        migrated_before,
        inherited_segments,
        [product_group_as_occurrence(group) for group in migrated_groups]
        if migrated_groups
        else migrated_occurrences,
        video_duration,
    )
    migrated_quotes = _map_segment_ids(golden_quote_order or [], old_to_new)

    mapped_count = len(inherited_segments) - len(unmapped_ids)
    report = ResegmentInheritanceReport(
        old_segment_count=len(old_segments),
        new_segment_count=len(new_segments),
        mapped_segment_count=mapped_count,
        exact_segment_count=exact_count,
        review_segment_count=len(review_ids),
        unmapped_segment_count=len(unmapped_ids),
        inherited_audit_count=mapped_count,
        inherited_occurrence_count=len(migrated_occurrences),
        inherited_reel_count=len(migrated_reels),
        inherited_golden_quote_count=len(migrated_quotes),
        invalidated_export_count=invalidated_export_count,
        review_segment_ids=review_ids,
        unmapped_segment_ids=unmapped_ids,
        warnings=[
            warning
            for condition, warning in (
                (bool(review_ids), f"{len(review_ids)} 个片段按最大词级覆盖继承，建议人工复核"),
                (bool(unmapped_ids), f"{len(unmapped_ids)} 个片段没有旧结果可继承，保持默认保留"),
                (bool((reels or []) and not migrated_reels), "旧集锦未找到有效片段引用，未继承"),
                (invalidated_export_count > 0, "旧导出保留在作业历史中，但不再视为当前内容导出"),
            )
            if condition
        ],
    )
    return ResegmentInheritanceResult(
        segments=inherited_segments,
        occurrences=migrated_occurrences,
        product_groups=migrated_groups,
        reels=migrated_reels,
        reels_before_quality=migrated_before,
        quality_report=quality_report,
        quality_report_before=quality_report_before,
        golden_quote_order=migrated_quotes,
        old_to_new_ids=copy.deepcopy(old_to_new),
        report=report,
    )
