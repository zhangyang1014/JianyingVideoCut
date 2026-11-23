"""Canonical product-highlight lineage resolution without time-window fallback."""

from __future__ import annotations

from typing import Sequence

from ..models.task import HighlightReel, ProductGroup, ProductOccurrence, Segment


class HighlightLineageError(ValueError):
    pass


def resolve_highlight_reel_segment_ids(
    reel: HighlightReel,
    segments: Sequence[Segment],
    *,
    product_groups: Sequence[ProductGroup] = (),
    product_occurrences: Sequence[ProductOccurrence] = (),
) -> list[str]:
    """Resolve exact source IDs and reject stale or cross-scope lineage."""
    segment_by_id = {segment.id: segment for segment in segments}
    missing_ids = [
        segment_id
        for segment_id in reel.segment_ids
        if segment_id not in segment_by_id
    ]
    if missing_ids:
        raise HighlightLineageError(
            f"集锦引用的字幕片段已变化：{missing_ids[:5]}"
        )

    allowed_ids: set[str] | None = None
    if reel.product_group_id:
        group = next(
            (
                item
                for item in product_groups
                if item.product_group_id == reel.product_group_id
            ),
            None,
        )
        if not group:
            raise HighlightLineageError(
                f"集锦引用的产品组不存在：{reel.product_group_id}"
            )
        allowed_ids = set(group.segment_ids)
    elif reel.occurrence_id:
        occurrence = next(
            (
                item
                for item in product_occurrences
                if item.occurrence_id == reel.occurrence_id
            ),
            None,
        )
        if occurrence:
            allowed_ids = set(occurrence.segment_ids)

    out_of_scope = [
        segment_id
        for segment_id in reel.segment_ids
        if allowed_ids is not None and segment_id not in allowed_ids
    ]
    if out_of_scope:
        raise HighlightLineageError(
            f"集锦包含产品范围外片段：{out_of_scope[:5]}"
        )

    ordered_ids = sorted(
        reel.segment_ids,
        key=lambda segment_id: (
            segment_by_id[segment_id].start,
            segment_by_id[segment_id].end,
            segment_id,
        ),
    )
    if ordered_ids != list(reel.segment_ids):
        raise HighlightLineageError("集锦片段未保持源时间顺序")
    return ordered_ids
