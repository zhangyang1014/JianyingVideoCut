"""
直播卖货产品集锦计划派生、代码级校验与段元数据同步。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple
import uuid

from ..models.task import (
    HighlightReel,
    HighlightReelBuildOptions,
    HighlightReelIssue,
    HighlightReelQualityReport,
    HighlightReelRepairAction,
    HighlightReelStrategy,
    HighlightReelStatus,
    HighlightQualityStatus,
    ProductGroup,
    ProductOccurrence,
    Segment,
    SegmentAction,
)
from .product_occurrence_detector import identify_product_occurrences

_KEPT_ACTIONS = {
    SegmentAction.KEEP,
    SegmentAction.SUBTITLE_FIX,
    SegmentAction.TEXT_FIX,
    SegmentAction.MERGE_NEXT,
}

FALLBACK_GAP_SECONDS = 5.0
MIN_REEL_DURATION = 60.0
MAX_REEL_DURATION = 300.0
TARGET_REEL_DURATION_RATIO = 0.70
MAX_SEMANTIC_UNITS_PER_REEL = 4
TARGET_REEL_COUNT_MIN = 5
TARGET_REEL_COUNT_MAX = 10
TARGET_RETENTION_MIN = 0.10
TARGET_RETENTION_MAX = 0.25
_HOOK_STYLES = {"cold_open", "highlight"}
_HOOK_ROLES = {"product_opening", "product_mention", "selling_point"}
_SELLING_POINT_ROLES = {"selling_point", "differentiation_story", "use_case"}
_CTA_ROLES = {"cta", "transition"}
_HARD_SEMANTIC_ISSUE_CODES = {
    "HR-M02",  # 结尾截断，无法自然收束
    "HR-M05",  # 缺少产品开场/卖点锚点，无法独立成片
    "HR-M09",  # 混产品
    "HR-M10",  # 链接/下单话术
    "HR-M11",  # 缺少核心卖点
    "HR-19",   # 结构化标记显示混产品
    "HR-16",   # 缺少钩子/卖点锚点
    "HR-17",   # 首段铺垫/收束，无法开场
}
_REPAIRABLE_MIXED_PRODUCT_CODES = {"HR-19", "HR-M09"}
_REPAIRABLE_CTA_CODES = {"HR-10", "HR-M10"}
_REPAIRABLE_TITLE_CODES = {"HR-M03"}
_REPAIRABLE_OPENING_CODES = {"HR-M01", "HR-M05", "HR-16", "HR-17"}
BALANCED_RETENTION_MIN = 0.40
BALANCED_RETENTION_MAX = 0.50
BALANCED_RETENTION_TARGET = 0.45
CONCISE_MAX_DURATION = 120.0
_STRATEGY_ORDER = (
    HighlightReelStrategy.FAITHFUL,
    HighlightReelStrategy.BALANCED,
    HighlightReelStrategy.CONCISE,
)
_STRATEGY_LABELS = {
    HighlightReelStrategy.FAITHFUL: "忠实版",
    HighlightReelStrategy.BALANCED: "平衡版",
    HighlightReelStrategy.CONCISE: "精简版",
}


def _options_or_default(options: Optional[HighlightReelBuildOptions] = None) -> HighlightReelBuildOptions:
    return options or HighlightReelBuildOptions()


def _is_kept(segment: Segment) -> bool:
    return segment.action in _KEPT_ACTIONS


def _semantic_roles(segment: Segment) -> List[str]:
    if segment.product_semantic:
        return list(segment.product_semantic.roles or [])
    role = {
        "cold_open": "product_opening",
        "setup": "context",
        "highlight": "selling_point",
        "punchline": "selling_point",
        "resolution": "closing",
    }.get(segment.style or "")
    return [role] if role else []


def _segment_product_name(segment: Segment) -> Optional[str]:
    mark = segment.product_semantic
    if mark and mark.product_entities:
        entity = mark.product_entities[0]
        return (entity.canonical_name or entity.name or entity.category or "").strip() or None
    if segment.clip_title:
        return re.split(r"为什么|怎么|如何|：|:", segment.clip_title, maxsplit=1)[0].strip(" ，,") or None
    return None


def _segment_angle_label(segment: Segment) -> Optional[str]:
    mark = segment.product_semantic
    if mark:
        return (mark.angle_label or (mark.selling_points[0] if mark.selling_points else "")).strip() or None
    if segment.clip_title:
        parts = re.split(r"：|:", segment.clip_title, maxsplit=1)
        if len(parts) > 1 and parts[1].strip():
            return parts[1].strip()
    return None


def _segment_semantic_key(segment: Segment) -> Optional[Tuple[str, str]]:
    mark = segment.product_semantic
    if not mark:
        return None
    if not mark.eligible_for_highlight:
        return None
    product_name = None
    if mark.product_entities:
        entity = mark.product_entities[0]
        product_name = (entity.canonical_name or entity.name or entity.category or "").strip() or None
    if not product_name:
        return None
    angle = (mark.angle_label or (mark.selling_points[0] if mark.selling_points else "")).strip() or "综合卖点"
    return (product_name, angle)


def _has_structured_semantics(segments: List[Segment]) -> bool:
    return any(segment.product_semantic for segment in segments)


def _is_highlight_eligible(segment: Segment) -> bool:
    mark = segment.product_semantic
    if mark and not mark.eligible_for_highlight:
        return False
    roles = set(_semantic_roles(segment))
    return not roles.intersection(_CTA_ROLES)


def _has_hook_anchor(segment: Segment) -> bool:
    return bool(set(_semantic_roles(segment)).intersection(_HOOK_ROLES) or segment.style in _HOOK_STYLES)


def _has_selling_point_anchor(segment: Segment) -> bool:
    return bool(
        set(_semantic_roles(segment)).intersection(_SELLING_POINT_ROLES)
        or segment.style in {"highlight", "punchline"}
        or (segment.product_semantic and segment.product_semantic.selling_points)
    )


def _segment_duration(segment: Segment) -> float:
    return max(0.0, segment.end - segment.start)


def _reel_min_duration(options: Optional[HighlightReelBuildOptions] = None) -> float:
    build_options = _options_or_default(options)
    return build_options.min_reel_duration or MIN_REEL_DURATION


def _filter_reels_by_min_duration(
    reels: List[HighlightReel],
    options: Optional[HighlightReelBuildOptions] = None,
) -> List[HighlightReel]:
    min_duration = _reel_min_duration(options)
    max_duration = (_options_or_default(options).max_reel_duration or MAX_REEL_DURATION)
    target_duration = max(min_duration, max_duration * TARGET_REEL_DURATION_RATIO)
    merged_reels = _merge_short_reels(reels, target_duration, max_duration)
    filtered = [reel for reel in merged_reels if reel.hidden or reel.duration >= min_duration]
    visible_index = 0
    normalized: List[HighlightReel] = []
    for reel in sorted(filtered, key=lambda item: item.start):
        if reel.hidden:
            normalized.append(reel)
            continue
        visible_index += 1
        normalized.append(
            reel.model_copy(
                deep=True,
                update={"clip_group": visible_index},
            )
        )
    return normalized


def _merge_two_reels(left: HighlightReel, right: HighlightReel) -> HighlightReel:
    merged_segment_ids = list(dict.fromkeys(list(left.segment_ids) + list(right.segment_ids)))
    title = left.title if left.title and not left.title.startswith("片段 ") else right.title
    return left.model_copy(
        deep=True,
        update={
            "title": title,
            "segment_ids": merged_segment_ids,
            "start": min(left.start, right.start),
            "end": max(left.end, right.end),
            "duration": left.duration + right.duration,
            "hook_segment_id": left.hook_segment_id or right.hook_segment_id,
            "rationale": left.rationale or right.rationale,
            "product_name": left.product_name or right.product_name,
            "angle_label": left.angle_label or right.angle_label,
            "semantic_roles": sorted(set(left.semantic_roles or []) | set(right.semantic_roles or [])),
            "issues": [],
            "hidden": False,
        },
    )


def _can_merge_reels(left: HighlightReel, right: HighlightReel) -> bool:
    """短集锦只能并入同一产品，避免规划层重新制造混产品问题。"""
    if left.product_name and right.product_name:
        return left.product_name == right.product_name
    return True


def _merge_short_reels(
    reels: List[HighlightReel],
    target_duration: float,
    max_duration: float,
) -> List[HighlightReel]:
    """短集锦优先并入相邻卖点块，尽量接近 max_duration 但不超过上限。"""
    visible = [reel for reel in sorted(reels, key=lambda item: item.start) if not reel.hidden]
    hidden = [reel for reel in reels if reel.hidden]
    index = 0
    while index < len(visible):
        reel = visible[index]
        if reel.duration >= target_duration:
            index += 1
            continue
        if not _reel_has_semantic_anchor(reel):
            index += 1
            continue

        next_reel = visible[index + 1] if index + 1 < len(visible) else None
        prev_reel = visible[index - 1] if index > 0 else None
        if next_reel and _can_merge_reels(reel, next_reel) and reel.duration + next_reel.duration <= max_duration:
            visible[index] = _merge_two_reels(reel, next_reel)
            del visible[index + 1]
            continue
        if prev_reel and _can_merge_reels(prev_reel, reel) and prev_reel.duration + reel.duration <= max_duration:
            visible[index - 1] = _merge_two_reels(prev_reel, reel)
            del visible[index]
            index = max(0, index - 1)
            continue
        index += 1
    return hidden + visible


def _reel_has_semantic_anchor(reel: HighlightReel) -> bool:
    return bool(reel.hook_segment_id or reel.rationale or reel.product_name or reel.semantic_roles)


def _build_reel_from_segments(
    clip_group: int,
    segments: List[Segment],
    reel_id: Optional[str] = None,
    parent_title: Optional[str] = None,
) -> HighlightReel:
    sorted_segments = sorted(segments, key=lambda seg: seg.start)
    product_name = next((_segment_product_name(seg) for seg in sorted_segments if _segment_product_name(seg)), None)
    angle_label = next((_segment_angle_label(seg) for seg in sorted_segments if _segment_angle_label(seg)), None)
    title_segment = next((seg for seg in sorted_segments if seg.clip_title), None)
    fallback_title = (sorted_segments[0].text or "").strip() or f"片段 {clip_group}"
    if product_name and angle_label:
        title = f"{product_name}：{angle_label}"
    elif product_name:
        title = product_name
    elif title_segment:
        title = title_segment.clip_title
    elif parent_title:
        title = _derive_child_reel_title(parent_title, sorted_segments)
    else:
        title = fallback_title[:25]
        if len(fallback_title) > 25:
            title += "..."

    duration = sum(_segment_duration(seg) for seg in sorted_segments)
    hook_segment = next((seg for seg in sorted_segments if _has_hook_anchor(seg)), None)
    semantic_roles = sorted({role for seg in sorted_segments for role in _semantic_roles(seg)})

    return HighlightReel(
        reel_id=reel_id or f"reel_{str(uuid.uuid4())[:8]}",
        clip_group=clip_group,
        title=title,
        segment_ids=[seg.id for seg in sorted_segments],
        start=sorted_segments[0].start if sorted_segments else 0.0,
        end=sorted_segments[-1].end if sorted_segments else 0.0,
        duration=duration,
        hook_segment_id=hook_segment.id if hook_segment else None,
        rationale=title_segment.reason if title_segment and title_segment.reason else None,
        product_name=product_name,
        angle_label=angle_label,
        semantic_roles=semantic_roles,
        status=HighlightReelStatus.PENDING,
        issues=[],
        created_at=datetime.now().isoformat(),
        hidden=False,
    )


def _segments_duration(segments: List[Segment]) -> float:
    return sum(_segment_duration(segment) for segment in segments)


def _opening_overlap_segments(
    group_segments: List[Segment],
    max_overlap_duration: float = 15.0,
) -> List[Segment]:
    opening: List[Segment] = []
    for segment in sorted(group_segments, key=lambda seg: seg.start):
        roles = set(_semantic_roles(segment))
        if not opening and not (segment.clip_title or "product_opening" in roles or segment.style == "cold_open"):
            continue
        if opening and not (
            roles.intersection({"product_opening", "context", "selling_point", "product_mention"})
            or segment.style in {"cold_open", "setup", "highlight"}
        ):
            break
        if opening and _segments_duration(opening) + _segment_duration(segment) > max_overlap_duration:
            break
        opening.append(segment)
        if _has_selling_point_anchor(segment) and _segments_duration(opening) >= 8.0:
            break
    return opening


def _add_opening_overlap_to_chunks(
    chunks: List[List[Segment]],
    group_segments: List[Segment],
    max_duration: float,
) -> List[List[Segment]]:
    if len(chunks) < 2:
        return chunks
    opening = _opening_overlap_segments(group_segments)
    if not opening:
        return chunks
    opening_ids = {segment.id for segment in opening}
    updated: List[List[Segment]] = []
    for index, chunk in enumerate(chunks):
        if index == 0 or any(segment.id in opening_ids for segment in chunk):
            updated.append(chunk)
            continue
        if _segments_duration(opening) + _segments_duration(chunk) <= max_duration:
            updated.append(opening + chunk)
        else:
            updated.append(chunk)
    return updated


def _derive_child_reel_title(parent_title: str, segments: List[Segment]) -> str:
    product_title = (parent_title or "").strip()
    product_title = re.split(r"为什么|：|:", product_title, maxsplit=1)[0].strip(" ，,")
    if not product_title:
        product_title = (parent_title or "").strip()[:18] or "产品集锦"

    for segment in segments:
        reason = segment.reason or ""
        quoted = re.findall(r"[‘'“\"]([^’'”\"]{2,24})[’'”\"]", reason)
        if quoted:
            return f"{product_title}：{quoted[0]}"
        cleaned = re.sub(r"^[A-Z]+-?\d*[:：]\s*", "", reason)
        cleaned = re.split(r"[，。,；;]", cleaned.strip(), maxsplit=1)[0]
        if cleaned:
            return f"{product_title}：{cleaned[:18]}"
    return parent_title


def _split_segments_by_max_duration(
    segments: List[Segment],
    max_duration: float,
) -> List[List[Segment]]:
    """按语义操作边界和累计时长切分，避免从卖点中间硬切。"""
    sorted_segments = sorted(segments, key=lambda seg: seg.start)
    if not sorted_segments or max_duration <= 0:
        return [sorted_segments] if sorted_segments else []

    unit_blocks: List[List[Segment]] = []
    current_block: List[Segment] = []
    current_unit: Optional[str] = None
    for segment in sorted_segments:
        semantic_unit = segment.semantic_unit_id or segment.compiled_from_operation or segment.id
        if current_block and semantic_unit != current_unit:
            unit_blocks.append(current_block)
            current_block = [segment]
            current_unit = semantic_unit
        else:
            current_block.append(segment)
            current_unit = semantic_unit
    if current_block:
        unit_blocks.append(current_block)

    groups: List[List[Segment]] = []
    current_group: List[Segment] = []
    current_duration = 0.0

    for block in unit_blocks:
        block_duration = sum(_segment_duration(segment) for segment in block)
        current_unit_count = len({
            segment.semantic_unit_id or segment.compiled_from_operation or segment.id
            for segment in current_group
        })
        would_exceed = current_group and current_duration + block_duration > max_duration
        too_many_units = current_group and current_unit_count >= MAX_SEMANTIC_UNITS_PER_REEL
        if would_exceed or too_many_units:
            groups.append(current_group)
            current_group = []
            current_duration = 0.0

        if block_duration > max_duration:
            groups.extend(_split_segments_by_duration_only(block, max_duration))
            current_group = []
            current_duration = 0.0
            continue

        if not current_group:
            current_group = list(block)
            current_duration = block_duration
        else:
            current_group.extend(block)
            current_duration += block_duration

    if current_group:
        groups.append(current_group)
    return groups


def _split_segments_by_duration_only(
    segments: List[Segment],
    max_duration: float,
) -> List[List[Segment]]:
    """仅在单个语义块自身超长时使用的兜底切分。"""
    groups: List[List[Segment]] = []
    current_group: List[Segment] = []
    current_duration = 0.0
    for segment in sorted(segments, key=lambda seg: seg.start):
        segment_duration = _segment_duration(segment)
        if current_group and current_duration + segment_duration > max_duration:
            groups.append(current_group)
            current_group = [segment]
            current_duration = segment_duration
        else:
            current_group.append(segment)
            current_duration += segment_duration
    if current_group:
        groups.append(current_group)
    return groups


def _strategy_segment_priority(segment: Segment) -> tuple[int, float, str]:
    roles = set(_semantic_roles(segment))
    if roles.intersection({"product_opening", "product_mention"}):
        rank = 0
    elif _has_selling_point_anchor(segment):
        rank = 1
    elif roles.intersection({"proof", "trust", "use_case", "context"}):
        rank = 2
    else:
        rank = 3
    return rank, segment.start, segment.id


def _product_anchor(segments: List[Segment], occurrence: ProductOccurrence) -> Optional[Segment]:
    normalized = occurrence.name.strip().casefold()
    return next(
        (
            segment
            for segment in segments
            if (
                normalized
                and (
                    normalized in (segment.display_text or segment.text or "").strip().casefold()
                    or normalized == (_segment_product_name(segment) or "").strip().casefold()
                )
            )
        ),
        None,
    ) or next((segment for segment in segments if _has_hook_anchor(segment)), None)


def _is_explicit_opening_anchor(segment: Segment, occurrence: ProductOccurrence) -> bool:
    """产品名/别名文本或明确产品开场角色可作为前两句锚点。"""
    roles = set(_semantic_roles(segment))
    if roles.intersection({"product_opening", "product_mention"}):
        return True
    text = (segment.display_text or segment.text or "").strip().casefold()
    product_names = [occurrence.name, *occurrence.aliases]
    return bool(
        text
        and any(
            normalized_name in text
            for name in product_names
            if (normalized_name := name.strip().casefold())
        )
    )


def _opening_anchor_ok(segments: List[Segment], occurrence: ProductOccurrence) -> bool:
    return any(
        _is_explicit_opening_anchor(segment, occurrence)
        for segment in segments[:2]
    )


def _source_order_or_opening_override_ok(
    segments: List[Segment],
    occurrence: ProductOccurrence,
) -> bool:
    """导出按源时间线渲染，因此元数据也必须严格保持源时间顺序。"""
    ordered = sorted(segments, key=lambda item: (item.start, item.end, item.id))
    return segments == ordered


def _selling_anchor(segments: List[Segment]) -> Optional[Segment]:
    return next((segment for segment in segments if _has_selling_point_anchor(segment)), None)


def _mandatory_anchors(segments: List[Segment], occurrence: ProductOccurrence) -> List[Segment]:
    anchors = [_product_anchor(segments, occurrence), _selling_anchor(segments)]
    return list({segment.id: segment for segment in anchors if segment}.values())


def _select_balanced_segments(segments: List[Segment], occurrence: ProductOccurrence) -> List[Segment]:
    if not segments:
        return []
    source_duration = _segments_duration(segments)
    lower = source_duration * BALANCED_RETENTION_MIN
    upper = source_duration * BALANCED_RETENTION_MAX
    target = source_duration * BALANCED_RETENTION_TARGET
    lower_count = max(1, int(len(segments) * BALANCED_RETENTION_MIN + 0.999999))
    upper_count = max(1, int(len(segments) * BALANCED_RETENTION_MAX))
    selected = _mandatory_anchors(segments, occurrence)
    selected_ids = {segment.id for segment in selected}
    candidates = [
        segment
        for segment in sorted(segments, key=_strategy_segment_priority)
        if segment.id not in selected_ids
    ]

    while (
        _segments_duration(selected) < lower or len(selected) < lower_count
    ) and candidates:
        current = _segments_duration(selected)
        fitting = [
            segment
            for segment in candidates
            if current + _segment_duration(segment) <= upper + 1e-6
            and len(selected) + 1 <= upper_count
        ]
        pool = fitting or candidates
        choice = min(
            pool,
            key=lambda segment: (
                abs((current + _segment_duration(segment)) - target),
                _strategy_segment_priority(segment),
            ),
        )
        selected.append(choice)
        selected_ids.add(choice.id)
        candidates.remove(choice)

    # 若 mandatory 原子片段已超过上限，不删除产品名/卖点锚点；交由质检报告明确暴露。
    return sorted(selected, key=lambda segment: (segment.start, segment.end, segment.id))


def _select_concise_segments(segments: List[Segment], occurrence: ProductOccurrence) -> List[Segment]:
    if not segments:
        return []
    selected = _mandatory_anchors(segments, occurrence)
    selected_ids = {segment.id for segment in selected}
    # 先确保“产品是什么 + 核心卖点”成立。
    if len(selected) < 2:
        candidates = [
            segment
            for segment in sorted(segments, key=_strategy_segment_priority)
            if segment.id not in selected_ids
        ]
        if candidates:
            selected.append(candidates[0])
    elif len(selected) == 2:
        proof = next(
            (
                segment
                for segment in sorted(segments, key=_strategy_segment_priority)
                if segment.id not in selected_ids
                and set(_semantic_roles(segment)).intersection({"proof", "trust", "use_case", "differentiation_story"})
            ),
            None,
        )
        if proof:
            selected.append(proof)

    # 长场次的精简版目标接近 2 分钟，而不是只留下十几秒锚点。按卖点/证据优先级
    # 补到约 100 秒，并严格不超过 120 秒；短源不为了凑时长制造或重复内容。
    if _segments_duration(segments) > CONCISE_MAX_DURATION:
        selected_ids = {segment.id for segment in selected}
        candidates = [
            segment
            for segment in sorted(segments, key=_strategy_segment_priority)
            if segment.id not in selected_ids
        ]
        while _segments_duration(selected) < 100.0 and candidates:
            current = _segments_duration(selected)
            fitting = [
                segment
                for segment in candidates
                if current + _segment_duration(segment) <= CONCISE_MAX_DURATION
            ]
            if not fitting:
                break
            selected.append(fitting[0])
            selected_ids.add(fitting[0].id)
            candidates.remove(fitting[0])

    ordered: List[Segment] = []
    duration = 0.0
    for segment in sorted({item.id: item for item in selected}.values(), key=lambda item: item.start):
        segment_duration = _segment_duration(segment)
        if ordered and duration + segment_duration > CONCISE_MAX_DURATION:
            continue
        ordered.append(segment)
        duration += segment_duration
    return ordered or [segments[0]]


def _stable_reel_id(occurrence_id: str, strategy: HighlightReelStrategy) -> str:
    digest = uuid.uuid5(uuid.NAMESPACE_URL, f"goldenclip:{occurrence_id}:{strategy.value}").hex[:12]
    return f"reel_{digest}"


def product_group_as_occurrence(group: ProductGroup) -> ProductOccurrence:
    """复用三策略选择器；产品组 ID 作为候选的 canonical scope ID。"""
    return ProductOccurrence(
        occurrence_id=group.product_group_id,
        name=group.canonical_name,
        aliases=list(group.aliases),
        start=group.start,
        end=group.end,
        segment_ids=list(group.segment_ids),
        evidence=list(group.evidence),
        confidence=group.confidence,
        source="product_group",
    )


def _selected_selling_points(segments: List[Segment]) -> List[str]:
    points: List[str] = []
    for segment in segments:
        if segment.product_semantic:
            points.extend(point.strip() for point in segment.product_semantic.selling_points if point.strip())
    return list(dict.fromkeys(points))


def _build_strategy_reel(
    occurrence: ProductOccurrence,
    strategy: HighlightReelStrategy,
    source_segments: List[Segment],
    clip_group: int,
    existing: Optional[HighlightReel] = None,
) -> HighlightReel:
    if strategy == HighlightReelStrategy.FAITHFUL:
        selected = list(source_segments)
    elif strategy == HighlightReelStrategy.BALANCED:
        selected = _select_balanced_segments(source_segments, occurrence)
    else:
        selected = _select_concise_segments(source_segments, occurrence)

    base = _build_reel_from_segments(
        clip_group=clip_group,
        segments=selected,
        reel_id=existing.reel_id if existing else _stable_reel_id(occurrence.occurrence_id, strategy),
        parent_title=occurrence.name,
    )
    source_duration = _segments_duration(source_segments)
    retention_ratio = base.duration / source_duration if source_duration > 0 else 0.0
    content_retention_ratio = len(selected) / len(source_segments) if source_segments else 0.0
    title_base = base.title or occurrence.name
    title = title_base if title_base.endswith(_STRATEGY_LABELS[strategy]) else f"{title_base} · {_STRATEGY_LABELS[strategy]}"
    updates = {
        "title": title,
        "occurrence_id": occurrence.occurrence_id,
        "strategy_kind": strategy,
        "product_name": occurrence.name,
        "retention_ratio": round(retention_ratio, 4),
        "content_retention_ratio": round(content_retention_ratio, 4),
        "source_duration": round(source_duration, 3),
        "source_segment_count": len(source_segments),
        "selected_selling_points": _selected_selling_points(selected),
        "hidden": False,
    }
    if existing:
        updates.update(
            {
                "export_path": existing.export_path,
                "exported_subtitle_revision": existing.exported_subtitle_revision,
                "exported_revision": existing.exported_revision,
                "exported_job_id": existing.exported_job_id,
            }
        )
    return base.model_copy(deep=True, update=updates)


def _build_legacy_highlight_reel_plan(
    segments: List[Segment],
    existing_reels: Optional[List[HighlightReel]],
    options: Optional[HighlightReelBuildOptions],
) -> List[HighlightReel]:
    """兼容旧的直接 planner 调用；新流水线必须显式传 product_occurrences。"""
    build_options = _options_or_default(options)
    kept_segments = [segment for segment in segments if _is_kept(segment)]
    if not kept_segments:
        return []
    existing_by_group = {
        reel.clip_group: reel
        for reel in (existing_reels or [])
        if not reel.hidden
    }
    has_clip_groups = any(segment.clip_group is not None for segment in kept_segments)
    semantic_candidates = [
        segment
        for segment in kept_segments
        if _segment_semantic_key(segment) and _is_highlight_eligible(segment)
    ]
    clip_grouped_count = sum(1 for segment in kept_segments if segment.clip_group is not None)
    has_semantic_groups = bool(semantic_candidates) and (
        not has_clip_groups or len(semantic_candidates) >= clip_grouped_count
    )
    reels: List[HighlightReel] = []

    if has_semantic_groups:
        grouped: Dict[Tuple[str, str], List[Segment]] = {}
        grouped_ids = set()
        for segment in semantic_candidates:
            key = _segment_semantic_key(segment)
            if key:
                grouped.setdefault(key, []).append(segment)
                grouped_ids.add(segment.id)
        max_reel_duration = build_options.max_reel_duration or MAX_REEL_DURATION
        for product_key in sorted(grouped, key=lambda key: grouped[key][0].start):
            group_segments = grouped[product_key]
            chunks = _split_segments_by_max_duration(group_segments, max_reel_duration)
            chunks = _add_opening_overlap_to_chunks(chunks, group_segments, max_reel_duration)
            for chunk_segments in chunks:
                reels.append(_build_reel_from_segments(len(reels) + 1, chunk_segments))
        ungrouped = [
            segment
            for segment in kept_segments
            if segment.id not in grouped_ids and _is_highlight_eligible(segment)
        ]
        if ungrouped and not reels:
            reels.extend(_build_fallback_reels(ungrouped, len(reels) + 1, build_options))
    elif has_clip_groups:
        grouped: Dict[int, List[Segment]] = {}
        ungrouped: List[Segment] = []
        for segment in kept_segments:
            if segment.clip_group is None:
                ungrouped.append(segment)
            else:
                grouped.setdefault(segment.clip_group, []).append(segment)
        max_reel_duration = build_options.max_reel_duration or MAX_REEL_DURATION
        for clip_group in sorted(grouped):
            group_segments = grouped[clip_group]
            existing = existing_by_group.get(clip_group)
            title_segment = next((segment for segment in group_segments if segment.clip_title), None)
            parent_title = title_segment.clip_title if title_segment else None
            chunks = _split_segments_by_max_duration(group_segments, max_reel_duration)
            chunks = _add_opening_overlap_to_chunks(chunks, group_segments, max_reel_duration)
            for chunk_index, chunk_segments in enumerate(chunks):
                reels.append(
                    _build_reel_from_segments(
                        len(reels) + 1,
                        chunk_segments,
                        reel_id=existing.reel_id if existing and chunk_index == 0 else None,
                        parent_title=parent_title,
                    )
                )
        if ungrouped:
            reels.extend(_build_fallback_reels(ungrouped, len(reels) + 1, build_options))
    else:
        reels = _build_fallback_reels(kept_segments, 1, build_options)
    return _filter_reels_by_min_duration(reels, build_options)


def build_highlight_reel_plan(
    segments: List[Segment],
    existing_reels: Optional[List[HighlightReel]] = None,
    options: Optional[HighlightReelBuildOptions] = None,
    product_occurrences: Optional[List[ProductOccurrence]] = None,
    product_groups: Optional[List[ProductGroup]] = None,
) -> List[HighlightReel]:
    """生成三策略候选；新流程以 product group 为唯一出片单位。"""
    if product_groups is None and product_occurrences is None:
        return _build_legacy_highlight_reel_plan(segments, existing_reels, options)
    groups_by_id = {
        group.product_group_id: group for group in (product_groups or [])
    }
    occurrences = (
        [product_group_as_occurrence(group) for group in (product_groups or [])]
        if product_groups is not None
        else list(product_occurrences or [])
    )
    segment_map = {segment.id: segment for segment in segments}
    existing_by_key = {
        (reel.product_group_id or reel.occurrence_id, reel.strategy_kind): reel
        for reel in (existing_reels or [])
        if (reel.product_group_id or reel.occurrence_id) and reel.strategy_kind
    }
    reels: List[HighlightReel] = []
    for occurrence in sorted(occurrences, key=lambda item: (item.start, item.end, item.occurrence_id)):
        occurrence_segments = [
            segment_map[segment_id]
            for segment_id in occurrence.segment_ids
            if segment_id in segment_map and _is_kept(segment_map[segment_id])
        ]
        eligible = [segment for segment in occurrence_segments if _is_highlight_eligible(segment)]
        source_segments = eligible or occurrence_segments
        if not source_segments:
            continue
        for strategy in _STRATEGY_ORDER:
            candidate = _build_strategy_reel(
                occurrence,
                strategy,
                source_segments,
                clip_group=len(reels) + 1,
                existing=existing_by_key.get((occurrence.occurrence_id, strategy)),
            )
            group = groups_by_id.get(occurrence.occurrence_id)
            if group:
                candidate.product_group_id = group.product_group_id
                candidate.source_occurrence_ids = list(group.occurrence_ids)
            reels.append(candidate)
    return [rebuild_reel_metrics(reel, segments) for reel in reels]


def _build_fallback_reels(
    kept_segments: List[Segment],
    start_group: int = 1,
    options: Optional[HighlightReelBuildOptions] = None,
) -> List[HighlightReel]:
    build_options = _options_or_default(options)
    sorted_segments = sorted(kept_segments, key=lambda seg: seg.start)
    groups: List[List[Segment]] = []
    current_group: List[Segment] = [sorted_segments[0]]
    max_group_duration = build_options.max_reel_duration or MAX_REEL_DURATION
    if build_options.target_total_duration and build_options.target_reel_count:
        max_group_duration = min(
            max_group_duration,
            max(build_options.min_reel_duration or MIN_REEL_DURATION, build_options.target_total_duration / build_options.target_reel_count),
        )

    for segment in sorted_segments[1:]:
        previous = current_group[-1]
        next_duration = sum(_segment_duration(seg) for seg in current_group) + _segment_duration(segment)
        should_split_by_gap = segment.start - previous.end > FALLBACK_GAP_SECONDS
        should_split_by_duration = (
            next_duration > max_group_duration
            and sum(_segment_duration(seg) for seg in current_group) >= (build_options.min_reel_duration or MIN_REEL_DURATION)
        )
        if should_split_by_gap or should_split_by_duration:
            groups.append(current_group)
            current_group = [segment]
        else:
            current_group.append(segment)
    groups.append(current_group)

    target_count = build_options.target_reel_count
    while target_count and len(groups) > target_count:
        merge_candidates = [
            idx
            for idx in range(len(groups) - 1)
            if sum(_segment_duration(seg) for seg in groups[idx] + groups[idx + 1]) <= max_group_duration
        ]
        if not merge_candidates:
            break
        merge_index = min(
            merge_candidates,
            key=lambda idx: groups[idx + 1][0].start - groups[idx][-1].end,
        )
        groups[merge_index] = groups[merge_index] + groups[merge_index + 1]
        del groups[merge_index + 1]

    reels: List[HighlightReel] = []
    for group_segments in groups:
        reels.append(
            _build_reel_from_segments(
                clip_group=start_group + len(reels),
                segments=group_segments,
            )
        )
    return reels


def _validate_product_group_winners(
    reels: List[HighlightReel],
    segments: List[Segment],
    product_groups: List[ProductGroup],
) -> HighlightReelQualityReport:
    """最终 canonical 结果：每个产品组必须且只能有一个 winner。"""
    issues: List[HighlightReelIssue] = []
    segment_map = {segment.id: segment for segment in segments}
    group_map = {group.product_group_id: group for group in product_groups}
    by_group: Dict[str, List[HighlightReel]] = {}
    for reel in reels:
        group_id = reel.product_group_id or reel.occurrence_id or ""
        by_group.setdefault(group_id, []).append(reel)
        if not reel.title.strip():
            issues.append(HighlightReelIssue(code="HR-01", severity="blocker", message="集锦标题为空", reel_id=reel.reel_id))
        if not reel.segment_ids:
            issues.append(HighlightReelIssue(code="HR-02", severity="blocker", message="集锦没有片段引用", reel_id=reel.reel_id))
            continue
        if len(set(reel.segment_ids)) != len(reel.segment_ids):
            issues.append(HighlightReelIssue(code="HR-03", severity="blocker", message="集锦存在重复片段引用", reel_id=reel.reel_id))
        group = group_map.get(group_id)
        if not group:
            issues.append(HighlightReelIssue(code="HR-22", severity="blocker", message=f"集锦引用未知 product group: {group_id}", reel_id=reel.reel_id))
            continue
        outside = [segment_id for segment_id in reel.segment_ids if segment_id not in set(group.segment_ids)]
        if outside:
            issues.append(HighlightReelIssue(code="HR-23", severity="blocker", message=f"{group_id} 集锦跨产品引用片段: {outside[:5]}", reel_id=reel.reel_id))
        missing = [segment_id for segment_id in reel.segment_ids if segment_id not in segment_map]
        if missing:
            issues.append(HighlightReelIssue(code="HR-04", severity="blocker", message=f"集锦引用不存在片段: {missing[:5]}", reel_id=reel.reel_id))
            continue
        reel_segments = [segment_map[segment_id] for segment_id in reel.segment_ids]
        scope = product_group_as_occurrence(group)
        if not _source_order_or_opening_override_ok(reel_segments, scope):
            issues.append(HighlightReelIssue(code="HR-06", severity="major", message="片段未保持源时间顺序", reel_id=reel.reel_id))
        if any(not _is_kept(segment) for segment in reel_segments):
            issues.append(HighlightReelIssue(code="HR-05", severity="blocker", message="集锦引用了非保留片段", reel_id=reel.reel_id))
        if any(not _is_highlight_eligible(segment) for segment in reel_segments):
            issues.append(HighlightReelIssue(code="HR-10", severity="blocker", message="集锦包含 CTA、链接或跨产品串场等不可用片段", reel_id=reel.reel_id))
        if not _product_anchor(reel_segments, scope) or not _selling_anchor(reel_segments):
            issues.append(
                HighlightReelIssue(
                    code="HR-27",
                    severity="major",
                    message="最终集锦缺少产品名/品类或核心卖点锚点",
                    reel_id=reel.reel_id,
                )
            )
        if not _opening_anchor_ok(reel_segments, scope):
            issues.append(
                HighlightReelIssue(
                    code="HR-29",
                    severity="minor",
                    message="集锦前 2 句未明确说出产品名、别名或清晰品类",
                    reel_id=reel.reel_id,
                    suggestion="重新生成时将产品开场锚点放到第 1 或第 2 句",
                )
            )

    for group in product_groups:
        group_reels = by_group.get(group.product_group_id, [])
        if len(group_reels) != 1:
            issues.append(
                HighlightReelIssue(
                    code="HR-21",
                    severity="blocker",
                    message=f"{group.product_group_id} 必须且只能有一条最终集锦，当前为 {len(group_reels)} 条",
                )
            )
    unknown = sorted(set(by_group) - set(group_map))
    if unknown:
        issues.append(HighlightReelIssue(code="HR-22", severity="blocker", message=f"计划包含未知 product group: {unknown[:5]}"))
    duplicate_groups = [group_id for group_id, items in by_group.items() if group_id and len(items) > 1]
    if duplicate_groups:
        issues.append(HighlightReelIssue(code="HR-28", severity="blocker", message=f"最终计划包含重复产品集锦: {duplicate_groups[:5]}"))

    hard_count = sum(issue.severity in {"blocker", "major"} for issue in issues)
    return HighlightReelQualityReport(
        passed=hard_count == 0,
        score=round(max(0.0, 1.0 - hard_count * 0.1), 3),
        issues=issues,
        validated_at=datetime.now().isoformat(),
        validator_model="code_product_group_winner",
    )


def _validate_occurrence_strategy_reels(
    reels: List[HighlightReel],
    segments: List[Segment],
    occurrences: List[ProductOccurrence],
) -> HighlightReelQualityReport:
    issues: List[HighlightReelIssue] = []
    segment_map = {segment.id: segment for segment in segments}
    occurrence_map = {occurrence.occurrence_id: occurrence for occurrence in occurrences}
    by_occurrence: Dict[str, List[HighlightReel]] = {}

    for reel in reels:
        if reel.occurrence_id:
            by_occurrence.setdefault(reel.occurrence_id, []).append(reel)
        if reel.hidden:
            issues.append(
                HighlightReelIssue(
                    code="HR-28",
                    severity="blocker",
                    message=f"{reel.occurrence_id or reel.reel_id} 的 canonical 策略被隐藏",
                    reel_id=reel.reel_id,
                    suggestion="自动修复后保留三策略可见，不以隐藏代替修复",
                )
            )
        if not reel.title.strip():
            issues.append(HighlightReelIssue(code="HR-01", severity="blocker", message="集锦缺少标题", reel_id=reel.reel_id))
        if not reel.segment_ids:
            issues.append(HighlightReelIssue(code="HR-02", severity="blocker", message="集锦没有关联片段", reel_id=reel.reel_id))
            continue
        if len(set(reel.segment_ids)) != len(reel.segment_ids):
            issues.append(HighlightReelIssue(code="HR-03", severity="blocker", message="集锦存在重复片段引用", reel_id=reel.reel_id))

        occurrence = occurrence_map.get(reel.occurrence_id or "")
        if not occurrence:
            issues.append(
                HighlightReelIssue(
                    code="HR-22",
                    severity="blocker",
                    message=f"集锦引用未知 occurrence: {reel.occurrence_id}",
                    reel_id=reel.reel_id,
                )
            )
            continue
        allowed_ids = set(occurrence.segment_ids)
        outside_ids = [segment_id for segment_id in reel.segment_ids if segment_id not in allowed_ids]
        if outside_ids:
            issues.append(
                HighlightReelIssue(
                    code="HR-23",
                    severity="blocker",
                    message=f"{occurrence.occurrence_id} 集锦越界引用片段: {outside_ids[:5]}",
                    reel_id=reel.reel_id,
                    suggestion="只保留本 occurrence.segment_ids 内的片段",
                )
            )

        reel_segments = [
            segment_map[segment_id]
            for segment_id in reel.segment_ids
            if segment_id in segment_map and _is_kept(segment_map[segment_id])
        ]
        missing_ids = [segment_id for segment_id in reel.segment_ids if segment_id not in segment_map]
        if missing_ids:
            issues.append(
                HighlightReelIssue(
                    code="HR-04",
                    severity="blocker",
                    message=f"集锦引用不存在片段: {missing_ids[:5]}",
                    reel_id=reel.reel_id,
                )
            )
        referenced_segments = [
            segment_map[segment_id]
            for segment_id in reel.segment_ids
            if segment_id in segment_map
        ]
        if any(not _is_kept(segment) for segment in referenced_segments):
            issues.append(
                HighlightReelIssue(
                    code="HR-05",
                    severity="blocker",
                    message="集锦引用了非保留片段",
                    reel_id=reel.reel_id,
                )
            )
        if not _source_order_or_opening_override_ok(referenced_segments, occurrence):
            issues.append(
                HighlightReelIssue(
                    code="HR-06",
                    severity="major",
                    message="集锦片段未保持源时间顺序",
                    reel_id=reel.reel_id,
                )
            )
        if any(not _is_highlight_eligible(segment) for segment in referenced_segments):
            issues.append(
                HighlightReelIssue(
                    code="HR-10",
                    severity="blocker",
                    message="集锦包含 CTA/链接/跨产品串场等不可用片段",
                    reel_id=reel.reel_id,
                    suggestion="仅保留本 occurrence 内 eligible_for_highlight 的片段",
                )
            )
        if referenced_segments and not _opening_anchor_ok(referenced_segments, occurrence):
            issues.append(
                HighlightReelIssue(
                    code="HR-29",
                    severity="minor",
                    message="集锦前 2 句未明确说出产品名、别名或清晰品类",
                    reel_id=reel.reel_id,
                    suggestion="重新生成时将产品开场锚点放到第 1 或第 2 句",
                )
            )
        source_segments = [
            segment_map[segment_id]
            for segment_id in occurrence.segment_ids
            if segment_id in segment_map
            and _is_kept(segment_map[segment_id])
            and _is_highlight_eligible(segment_map[segment_id])
        ]
        source_segments = source_segments or [
            segment_map[segment_id]
            for segment_id in occurrence.segment_ids
            if segment_id in segment_map and _is_kept(segment_map[segment_id])
        ]
        source_duration = _segments_duration(source_segments)
        selected_duration = _segments_duration(reel_segments)
        rendered_duration = _rendered_segments_duration(reel.segment_ids, segments)
        ratio = selected_duration / source_duration if source_duration > 0 else 0.0
        content_ratio = len(reel_segments) / len(source_segments) if source_segments else 0.0
        reel.duration = rendered_duration
        reel.source_duration = source_duration
        reel.retention_ratio = round(ratio, 4)
        reel.source_segment_count = len(source_segments)
        reel.content_retention_ratio = round(content_ratio, 4)

        if reel.strategy_kind == HighlightReelStrategy.FAITHFUL and ratio < 0.60:
            issues.append(
                HighlightReelIssue(
                    code="HR-24",
                    severity="major",
                    message=f"faithful 保留率 {ratio:.1%} 低于多数有效内容要求",
                    reel_id=reel.reel_id,
                )
            )
        elif reel.strategy_kind == HighlightReelStrategy.BALANCED and not (
            BALANCED_RETENTION_MIN - 1e-6 <= ratio <= BALANCED_RETENTION_MAX + 1e-6
        ):
            closest_segments = _select_balanced_segments(source_segments, occurrence)
            closest_ratio = (
                _segments_duration(closest_segments) / source_duration
                if source_duration > 0
                else 0.0
            )
            target_reachable = (
                BALANCED_RETENTION_MIN - 1e-6
                <= closest_ratio
                <= BALANCED_RETENTION_MAX + 1e-6
            )
            issues.append(
                HighlightReelIssue(
                    code="HR-25",
                    severity="major" if target_reachable else "minor",
                    message=(
                        f"balanced 时长保留率 {ratio:.1%} 不在 40%-50%"
                        if target_reachable
                        else (
                            f"balanced 受原子片段粒度限制，当前时长/内容保留率 "
                            f"{ratio:.1%}/{content_ratio:.1%}，最接近可行时长比例 "
                            f"{closest_ratio:.1%}"
                        )
                    ),
                    reel_id=reel.reel_id,
                    suggestion=(
                        "按有效时长重新选择 40%-50% 的完整片段"
                        if target_reachable
                        else "保留必要产品、卖点和完整收束锚点，接受最接近目标的组合"
                    ),
                )
            )
        elif reel.strategy_kind == HighlightReelStrategy.CONCISE:
            if rendered_duration > CONCISE_MAX_DURATION + 1e-6:
                issues.append(
                    HighlightReelIssue(
                        code="HR-26",
                        severity="major",
                        message=f"concise 实际渲染时长 {rendered_duration:.1f}s 超过 120s",
                        reel_id=reel.reel_id,
                    )
                )
            if not _product_anchor(reel_segments, occurrence) or not _selling_anchor(reel_segments):
                issues.append(
                    HighlightReelIssue(
                        code="HR-27",
                        severity="major",
                        message="concise 缺少产品名/品类或核心卖点锚点",
                        reel_id=reel.reel_id,
                        suggestion="从本 occurrence 补入产品开场与 selling_point 片段",
                    )
                )

    expected = set(_STRATEGY_ORDER)
    for occurrence in occurrences:
        occurrence_reels = by_occurrence.get(occurrence.occurrence_id, [])
        kinds = [reel.strategy_kind for reel in occurrence_reels if reel.strategy_kind]
        if len(occurrence_reels) != 3 or set(kinds) != expected or len(kinds) != len(set(kinds)):
            issues.append(
                HighlightReelIssue(
                    code="HR-21",
                    severity="blocker",
                    message=(
                        f"{occurrence.occurrence_id} 必须且只能有 faithful/balanced/concise 三条，"
                        f"当前为 {[kind.value for kind in kinds]}"
                    ),
                    suggestion="确定性重建该 occurrence 的三策略",
                )
            )

    unknown_occurrences = sorted(set(by_occurrence) - set(occurrence_map))
    if unknown_occurrences:
        issues.append(
            HighlightReelIssue(
                code="HR-22",
                severity="blocker",
                message=f"计划包含未知 occurrence: {unknown_occurrences[:5]}",
            )
        )

    blockers = [issue for issue in issues if issue.severity == "blocker"]
    majors = [issue for issue in issues if issue.severity == "major"]
    passed = not blockers and not majors
    score = max(0.0, 1.0 - 0.2 * len(blockers) - 0.1 * len(majors) - 0.03 * len(issues))
    return HighlightReelQualityReport(
        passed=passed,
        score=round(score, 3),
        issues=issues,
        validated_at=datetime.now().isoformat(),
        validator_model="code",
    )


def validate_highlight_reels_code(
    reels: List[HighlightReel],
    segments: List[Segment],
    video_duration: Optional[float] = None,
    options: Optional[HighlightReelBuildOptions] = None,
    product_occurrences: Optional[List[ProductOccurrence]] = None,
    product_groups: Optional[List[ProductGroup]] = None,
) -> HighlightReelQualityReport:
    """代码级校验产品集锦计划，不调用模型。"""
    if product_groups is not None:
        return _validate_product_group_winners(reels, segments, product_groups)
    canonical = any(reel.occurrence_id or reel.strategy_kind for reel in reels)
    if canonical:
        occurrences = product_occurrences if product_occurrences is not None else identify_product_occurrences(segments)
        return _validate_occurrence_strategy_reels(reels, segments, occurrences)

    build_options = _options_or_default(options)
    min_reel_duration = build_options.min_reel_duration or MIN_REEL_DURATION
    max_reel_duration = build_options.max_reel_duration or MAX_REEL_DURATION
    target_reel_count = build_options.target_reel_count
    issues: List[HighlightReelIssue] = []
    segment_map = {seg.id: seg for seg in segments}
    kept_segments = [seg for seg in segments if _is_kept(seg)]
    has_structured_semantics = _has_structured_semantics(kept_segments)
    kept_duration = sum(_segment_duration(seg) for seg in kept_segments)
    total_duration = video_duration or sum(_segment_duration(seg) for seg in segments) or 0.0

    visible_reels = [reel for reel in reels if not reel.hidden]
    all_segment_refs: List[str] = []

    for reel in visible_reels:
        if not reel.title.strip():
            issues.append(
                HighlightReelIssue(
                    code="HR-01",
                    severity="blocker",
                    message=f"集锦 #{reel.clip_group} 缺少标题",
                    reel_id=reel.reel_id,
                )
            )

        if not reel.segment_ids:
            issues.append(
                HighlightReelIssue(
                    code="HR-02",
                    severity="blocker",
                    message=f"集锦 #{reel.clip_group} 没有关联片段",
                    reel_id=reel.reel_id,
                )
            )
            continue

        if len(set(reel.segment_ids)) != len(reel.segment_ids):
            issues.append(
                HighlightReelIssue(
                    code="HR-03",
                    severity="blocker",
                    message=f"集锦 #{reel.clip_group} 存在重复片段引用",
                    reel_id=reel.reel_id,
                )
            )

        reel_kept_duration = 0.0
        prev_start = -1.0
        for segment_id in reel.segment_ids:
            segment = segment_map.get(segment_id)
            if segment is None:
                issues.append(
                    HighlightReelIssue(
                        code="HR-04",
                        severity="blocker",
                        message=f"集锦 #{reel.clip_group} 引用了不存在的片段 {segment_id}",
                        reel_id=reel.reel_id,
                    )
                )
                continue
            if not _is_kept(segment):
                issues.append(
                    HighlightReelIssue(
                        code="HR-05",
                        severity="blocker",
                        message=f"集锦 #{reel.clip_group} 包含非保留片段 {segment_id}",
                        reel_id=reel.reel_id,
                    )
                )
            if segment.start < prev_start:
                issues.append(
                    HighlightReelIssue(
                        code="HR-06",
                        severity="major",
                        message=f"集锦 #{reel.clip_group} 片段时间轴未按递增排序",
                        reel_id=reel.reel_id,
                    )
                )
            prev_start = segment.start
            reel_kept_duration += _segment_duration(segment)
            all_segment_refs.append(segment_id)

        if reel.duration <= 0:
            reel.duration = reel_kept_duration

        if reel.duration < min_reel_duration:
            issues.append(
                HighlightReelIssue(
                    code="HR-07",
                    severity="major",
                    message=f"集锦 #{reel.clip_group} 时长 {reel.duration:.1f}s 低于目标下限 {min_reel_duration:.0f}s",
                    reel_id=reel.reel_id,
                )
            )
        elif reel.duration > max_reel_duration:
            issues.append(
                HighlightReelIssue(
                    code="HR-08",
                    severity="major",
                    message=f"集锦 #{reel.clip_group} 时长 {reel.duration:.1f}s 超过目标上限 {max_reel_duration:.0f}s",
                    reel_id=reel.reel_id,
                )
            )

        reel_segments = [
            segment_map[segment_id]
            for segment_id in reel.segment_ids
            if segment_id in segment_map and _is_kept(segment_map[segment_id])
        ]
        product_names = {
            product_name
            for product_name in (_segment_product_name(seg) for seg in reel_segments)
            if product_name
        }
        if has_structured_semantics and len(product_names) > 1:
            issues.append(
                HighlightReelIssue(
                    code="HR-19",
                    severity="blocker",
                    message=f"集锦 #{reel.clip_group} 结构化标记显示混入多个产品: {', '.join(sorted(product_names))}",
                    reel_id=reel.reel_id,
                    suggestion="拆分产品：按 product_semantic.product_entities 重新分组",
                )
            )
        if has_structured_semantics and reel_segments and not product_names:
            issues.append(
                HighlightReelIssue(
                    code="HR-21",
                    severity="major",
                    message=f"集锦 #{reel.clip_group} 缺少产品实体标记",
                    reel_id=reel.reel_id,
                    suggestion="回到审计阶段补充 product_semantic.product_entities",
                )
            )

        has_hook = any(_has_hook_anchor(seg) for seg in reel_segments)
        has_selling_point = any(_has_selling_point_anchor(seg) for seg in reel_segments)
        has_cta = any(
            set(_semantic_roles(seg)).intersection(_CTA_ROLES)
            or (seg.product_semantic and not seg.product_semantic.eligible_for_highlight)
            for seg in reel_segments
        )

        if has_cta:
            issues.append(
                HighlightReelIssue(
                    code="HR-10",
                    severity="blocker",
                    message=f"集锦 #{reel.clip_group} 包含 CTA/链接/跨产品串场等不适合集锦的语义标记",
                    reel_id=reel.reel_id,
                    suggestion="删除链接话术或将其从该集锦移除",
                )
            )
        if reel_segments and not has_hook:
            issues.append(
                HighlightReelIssue(
                    code="HR-16",
                    severity="major",
                    message=f"集锦 #{reel.clip_group} 缺少产品开场或卖点锚点",
                    reel_id=reel.reel_id,
                    suggestion="换首段：选择 product_opening / selling_point 标记的片段作为开场",
                )
            )
        if has_structured_semantics and reel_segments and not has_selling_point:
            issues.append(
                HighlightReelIssue(
                    code="HR-20",
                    severity="major",
                    message=f"集锦 #{reel.clip_group} 缺少核心卖点、差异化故事或使用场景标记",
                    reel_id=reel.reel_id,
                    suggestion="补卖点：加入 selling_point / differentiation_story / use_case 片段",
                )
            )
        if reel_segments:
            first_segment = reel_segments[0]
            if first_segment.style in {"setup", "resolution"} and not has_hook:
                issues.append(
                    HighlightReelIssue(
                        code="HR-17",
                        severity="major",
                        message=f"集锦 #{reel.clip_group} 首段为铺垫/收束，缺少强钩子开场",
                        reel_id=reel.reel_id,
                        suggestion="换首段：首段应说清产品名/品类，使用 product_opening 或 selling_point",
                    )
                )

    for segment in kept_segments:
        if not has_structured_semantics and segment.clip_group is None:
            issues.append(
                HighlightReelIssue(
                    code="HR-09",
                    severity="major",
                    message=f"保留片段 {segment.id} 缺少 clip_group",
                )
            )
        if segment.clip_group is not None and segment.action == SegmentAction.DELETE:
            issues.append(
                HighlightReelIssue(
                    code="HR-10",
                    severity="blocker",
                    message=f"删除片段 {segment.id} 不应带有 clip_group",
                )
            )

    has_product_groups = has_structured_semantics or any(segment.clip_group is not None for segment in kept_segments)
    if target_reel_count:
        target_min = max(1, target_reel_count - 1)
        target_max = target_reel_count + 1
    elif has_product_groups:
        # 自动条数模式：AI 已识别产品分组时以实际产品/拆分结果为准，
        # 不再套用通用 5-10 条建议范围，避免为了凑数量牺牲逻辑完整性。
        target_min = 1
        target_max = max(1, len(visible_reels))
    else:
        target_min = TARGET_REEL_COUNT_MIN
        target_max = TARGET_REEL_COUNT_MAX

    if len(visible_reels) < target_min:
        issues.append(
            HighlightReelIssue(
                code="HR-12",
                severity="major",
                message=f"集锦数量 {len(visible_reels)} 低于目标范围 {target_min}-{target_max}",
            )
        )
    elif len(visible_reels) > target_max:
        issues.append(
            HighlightReelIssue(
                code="HR-13",
                severity="minor",
                message=f"集锦数量 {len(visible_reels)} 超过目标范围 {target_min}-{target_max}",
            )
        )

    if build_options.target_total_duration:
        visible_duration = sum(reel.duration for reel in visible_reels)
        lower = build_options.target_total_duration * 0.85
        upper = build_options.target_total_duration * 1.15
        if visible_duration < lower or visible_duration > upper:
            issues.append(
                HighlightReelIssue(
                    code="HR-18",
                    severity="minor",
                    message=(
                        f"集锦总时长 {visible_duration:.1f}s 与目标 "
                        f"{build_options.target_total_duration:.0f}s 偏差超过 15%"
                    ),
                )
            )

    if total_duration > 0 and kept_duration / total_duration < TARGET_RETENTION_MIN:
        issues.append(
            HighlightReelIssue(
                code="HR-14",
                severity="major",
                message=(
                    f"总保留率 {kept_duration / total_duration:.1%} 低于建议下限 "
                    f"{TARGET_RETENTION_MIN:.0%}"
                ),
            )
        )
    elif total_duration > 0 and kept_duration / total_duration > TARGET_RETENTION_MAX:
        issues.append(
            HighlightReelIssue(
                code="HR-15",
                severity="minor",
                message=(
                    f"总保留率 {kept_duration / total_duration:.1%} 高于建议上限 "
                    f"{TARGET_RETENTION_MAX:.0%}"
                ),
            )
        )

    blockers = [issue for issue in issues if issue.severity == "blocker"]
    majors = [issue for issue in issues if issue.severity == "major"]
    passed = len(blockers) == 0 and len(majors) == 0
    score = max(0.0, 1.0 - 0.2 * len(blockers) - 0.1 * len(majors) - 0.03 * len(issues))

    return HighlightReelQualityReport(
        passed=passed,
        score=round(score, 3),
        issues=issues,
        validated_at=datetime.now().isoformat(),
        validator_model="code",
    )


def highlight_reel_plan_fingerprint(reels: List[HighlightReel]) -> str:
    """用于判断修订器是否真正改变了候选计划。"""
    parts = []
    for reel in sorted(reels, key=lambda item: (item.clip_group, item.start, item.reel_id)):
        parts.append(
            "|".join([
                reel.reel_id,
                str(reel.clip_group),
                reel.occurrence_id or "",
                reel.strategy_kind.value if reel.strategy_kind else "",
                reel.title,
                ",".join(reel.segment_ids),
                "hidden" if reel.hidden else "visible",
            ])
        )
    return "\n".join(parts)


def _rebuild_reel_with_same_id(reel: HighlightReel, segments: List[Segment]) -> HighlightReel:
    rebuilt = _build_reel_from_segments(
        reel.clip_group,
        segments,
        reel_id=reel.reel_id,
        parent_title=reel.title,
    )
    return rebuilt.model_copy(
        deep=True,
        update={
            "export_path": reel.export_path,
            "source_content_revision": reel.source_content_revision,
            "exported_subtitle_revision": reel.exported_subtitle_revision,
            "exported_revision": reel.exported_revision,
            "exported_job_id": reel.exported_job_id,
        },
    )


def _renumber_visible_reels(reels: List[HighlightReel]) -> List[HighlightReel]:
    visible_index = 0
    updated: List[HighlightReel] = []
    for reel in sorted(reels, key=lambda item: item.start):
        if reel.hidden:
            updated.append(reel)
            continue
        visible_index += 1
        updated.append(reel.model_copy(deep=True, update={"clip_group": visible_index}))
    return updated


def repair_highlight_reels(
    reels: List[HighlightReel],
    segments: List[Segment],
    report: HighlightReelQualityReport,
    options: Optional[HighlightReelBuildOptions] = None,
    product_occurrences: Optional[List[ProductOccurrence]] = None,
) -> Tuple[List[HighlightReel], List[HighlightReelRepairAction]]:
    """
    对质检失败的计划做一次确定性修订。

    只使用结构化 segment 元数据和稳定问题码，不解释自然语言 suggestion。
    """
    if any(reel.occurrence_id or reel.strategy_kind for reel in reels):
        occurrences = product_occurrences if product_occurrences is not None else identify_product_occurrences(segments)
        rebuilt = build_highlight_reel_plan(
            segments,
            existing_reels=reels,
            options=options,
            product_occurrences=occurrences,
        )
        if highlight_reel_plan_fingerprint(rebuilt) == highlight_reel_plan_fingerprint(reels):
            return rebuilt, []
        actions = [
            HighlightReelRepairAction(
                action="rebuild_occurrence_strategies",
                reel_id=reel.reel_id,
                segment_ids=list(reel.segment_ids),
                reason="按 occurrence 边界确定性重建 faithful/balanced/concise 三策略",
            )
            for reel in rebuilt
        ]
        return rebuilt, actions

    segment_map = {seg.id: seg for seg in segments}
    issues_by_reel: Dict[str, List[HighlightReelIssue]] = {}
    for issue in report.issues:
        if issue.reel_id:
            issues_by_reel.setdefault(issue.reel_id, []).append(issue)

    repaired: List[HighlightReel] = []
    actions: List[HighlightReelRepairAction] = []
    for reel in reels:
        reel_issues = issues_by_reel.get(reel.reel_id, [])
        issue_codes = {issue.code for issue in reel_issues}
        source_segments = [
            segment_map[segment_id]
            for segment_id in reel.segment_ids
            if segment_id in segment_map and _is_kept(segment_map[segment_id])
        ]
        if not reel_issues or not source_segments:
            repaired.append(reel.model_copy(deep=True))
            continue

        candidate_segments = list(source_segments)
        if issue_codes.intersection(_REPAIRABLE_CTA_CODES):
            candidate_segments = [seg for seg in candidate_segments if _is_highlight_eligible(seg)]
            removed_ids = [seg.id for seg in source_segments if seg.id not in {item.id for item in candidate_segments}]
            if removed_ids:
                actions.append(
                    HighlightReelRepairAction(
                        action="remove_cta",
                        reel_id=reel.reel_id,
                        segment_ids=removed_ids,
                        reason="移除 CTA/链接/跨产品串场片段",
                    )
                )

        if issue_codes.intersection(_REPAIRABLE_MIXED_PRODUCT_CODES):
            grouped: Dict[str, List[Segment]] = {}
            fallback: List[Segment] = []
            for segment in candidate_segments:
                product_name = _segment_product_name(segment)
                if product_name:
                    grouped.setdefault(product_name, []).append(segment)
                else:
                    fallback.append(segment)
            if len(grouped) > 1:
                for product_name, product_segments in sorted(grouped.items(), key=lambda item: item[1][0].start):
                    if fallback:
                        # 不带产品名的上下文只并入时间邻近的第一个产品，避免复制到多个成片。
                        product_segments = sorted(product_segments + fallback, key=lambda seg: seg.start)
                        fallback = []
                    repaired.append(
                        _build_reel_from_segments(
                            clip_group=len(repaired) + 1,
                            segments=product_segments,
                            parent_title=product_name,
                        )
                    )
                actions.append(
                    HighlightReelRepairAction(
                        action="split_product",
                        reel_id=reel.reel_id,
                        segment_ids=[seg.id for seg in candidate_segments],
                        reason="按 product_semantic.product_entities 拆分混合产品",
                    )
                )
                continue

        should_rebuild = (
            [seg.id for seg in candidate_segments] != reel.segment_ids
            or bool(issue_codes.intersection(_REPAIRABLE_TITLE_CODES | _REPAIRABLE_OPENING_CODES))
        )
        if should_rebuild and candidate_segments:
            repaired.append(_rebuild_reel_with_same_id(reel, candidate_segments))
            actions.append(
                HighlightReelRepairAction(
                    action="retitle",
                    reel_id=reel.reel_id,
                    segment_ids=[seg.id for seg in candidate_segments],
                    reason="根据保留片段重算标题、产品名、卖点锚点和时长",
                )
            )
        else:
            repaired.append(reel.model_copy(deep=True))

    normalized = _filter_reels_by_min_duration(repaired, options)
    return _renumber_visible_reels(normalized), actions


def apply_plan_issues_to_reels(
    reels: List[HighlightReel],
    report: HighlightReelQualityReport,
    *,
    hide_hard_issues: bool = True,
) -> List[HighlightReel]:
    """把质检 issue 写回各集锦，并更新状态。"""
    issues_by_reel: Dict[str, List[HighlightReelIssue]] = {}
    for issue in report.issues:
        if issue.reel_id:
            issues_by_reel.setdefault(issue.reel_id, []).append(issue)

    updated: List[HighlightReel] = []
    for reel in reels:
        reel_issues = issues_by_reel.get(reel.reel_id, [])
        has_blocker = any(issue.severity == "blocker" for issue in reel_issues)
        has_major = any(issue.severity == "major" for issue in reel_issues)
        has_hard_semantic_issue = any(
            issue.code in _HARD_SEMANTIC_ISSUE_CODES
            and issue.severity in {"blocker", "major"}
            for issue in reel_issues
        )
        if has_blocker:
            status = HighlightReelStatus.FAILED
            quality_status = HighlightQualityStatus.FAILED
        elif has_hard_semantic_issue:
            status = HighlightReelStatus.FAILED
            quality_status = HighlightQualityStatus.FAILED
        elif has_major:
            status = HighlightReelStatus.WARNING
            quality_status = HighlightQualityStatus.WARNING
        elif reel_issues:
            status = HighlightReelStatus.WARNING
            quality_status = HighlightQualityStatus.WARNING
        else:
            status = HighlightReelStatus.PASSED
            quality_status = HighlightQualityStatus.PASSED

        reel_score = reel.score
        if reel_score is None:
            blockers = sum(issue.severity == "blocker" for issue in reel_issues)
            majors = sum(issue.severity == "major" for issue in reel_issues)
            reel_score = round(
                max(
                    0.0,
                    1.0
                    - 0.2 * blockers
                    - 0.1 * majors
                    - 0.03 * len(reel_issues),
                ),
                3,
            )

        updated.append(
            reel.model_copy(
                deep=True,
                update={
                    "issues": reel_issues,
                    "status": status,
                    "quality_status": quality_status,
                    "score": reel_score,
                    "hidden": (
                        False
                        if reel.occurrence_id and reel.strategy_kind
                        else has_hard_semantic_issue
                        if hide_hard_issues
                        else reel.hidden
                    ),
                },
            )
        )
    return updated


def sync_plan_to_segments(
    segments: List[Segment],
    reels: List[HighlightReel],
) -> List[Segment]:
    """兼容旧调用：集锦计划不再反写覆盖审计片段元数据。"""
    return [segment.model_copy(deep=True) for segment in segments]


def _rendered_segments_duration(
    selected_segment_ids: List[str],
    segments: List[Segment],
) -> float:
    """按 FFmpeg 导出的连续保留块口径计算成片时长，包含相邻语句间的自然停顿。"""
    selected_ids = set(selected_segment_ids)
    duration = 0.0
    chunk_start: Optional[float] = None
    chunk_end: Optional[float] = None

    for segment in sorted(segments, key=lambda item: item.start):
        if segment.id in selected_ids and _is_kept(segment):
            if chunk_start is None:
                chunk_start = segment.start
            chunk_end = segment.end
            continue
        if chunk_start is not None and chunk_end is not None:
            duration += max(0.0, chunk_end - chunk_start)
            chunk_start = None
            chunk_end = None

    if chunk_start is not None and chunk_end is not None:
        duration += max(0.0, chunk_end - chunk_start)
    return duration


def rebuild_reel_metrics(reel: HighlightReel, segments: List[Segment]) -> HighlightReel:
    """根据 segment_ids 重算集锦时间边界、成片时长与有效内容保留率。"""
    segment_map = {seg.id: seg for seg in segments}
    ordered = [
        segment_map[segment_id]
        for segment_id in reel.segment_ids
        if segment_id in segment_map
    ]
    if not ordered:
        return reel
    effective_duration = sum(_segment_duration(seg) for seg in ordered if _is_kept(seg))
    duration = _rendered_segments_duration(reel.segment_ids, segments)
    retention_ratio = (
        effective_duration / reel.source_duration
        if reel.source_duration and reel.source_duration > 0
        else reel.retention_ratio
    )
    content_retention_ratio = (
        len(ordered) / reel.source_segment_count
        if reel.source_segment_count and reel.source_segment_count > 0
        else reel.content_retention_ratio
    )
    return reel.model_copy(
        deep=True,
        update={
            "start": min(segment.start for segment in ordered),
            "end": max(segment.end for segment in ordered),
            "duration": duration,
            "retention_ratio": round(retention_ratio, 4) if retention_ratio is not None else None,
            "content_retention_ratio": (
                round(content_retention_ratio, 4)
                if content_retention_ratio is not None
                else None
            ),
        },
    )


def reels_to_clip_groups(reels: List[HighlightReel]) -> List[List[str]]:
    """导出接口使用的 segment id 分组。"""
    return [
        list(reel.segment_ids)
        for reel in sorted(reels, key=lambda item: item.start)
        if not reel.hidden and reel.segment_ids
    ]
