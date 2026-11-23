from backend.models.task import (
    HighlightReel,
    ProductOccurrence,
    Segment,
    SegmentAction,
    WordTimestamp,
)
from backend.services.resegment_inheritance import inherit_resegment_results


def _segment(segment_id: str, text: str, start: float, action=SegmentAction.KEEP) -> Segment:
    words = []
    cursor = start
    for char in text:
        words.append(WordTimestamp(word=char, start=round(cursor, 3), end=round(cursor + 0.08, 3)))
        cursor += 0.1
    return Segment(
        id=segment_id,
        start=words[0].start,
        end=words[-1].end,
        text=text,
        words=words,
        action=action,
    )


def test_exact_mapping_preserves_audit_and_subtitle_override():
    old = _segment("old", "你好", 0.0, SegmentAction.DELETE)
    old.claude_action = SegmentAction.DELETE
    old.claude_reason = "重复内容"
    old.subtitle_text = "您好"
    old.subtitle_text_source = "manual"
    new = _segment("new", "你好", 0.0)

    result = inherit_resegment_results([old], [new])

    inherited = result.segments[0]
    assert inherited.id == "new"
    assert inherited.action == SegmentAction.DELETE
    assert inherited.claude_reason == "重复内容"
    assert inherited.subtitle_text == "您好"
    assert result.report.exact_segment_count == 1
    assert result.report.review_segment_count == 0


def test_split_mapping_projects_decision_and_marks_review():
    old = _segment("old", "你好呀", 0.0, SegmentAction.DELETE)
    new_left = _segment("new-left", "你好", 0.0)
    new_right = _segment("new-right", "呀", 0.2)

    result = inherit_resegment_results([old], [new_left, new_right])

    assert [segment.action for segment in result.segments] == [
        SegmentAction.DELETE,
        SegmentAction.DELETE,
    ]
    assert result.old_to_new_ids["old"] == ["new-left", "new-right"]
    assert result.report.review_segment_count == 2


def test_merge_mapping_uses_maximum_word_coverage_and_marks_review():
    old_left = _segment("old-left", "你好", 0.0, SegmentAction.DELETE)
    old_right = _segment("old-right", "呀", 0.2, SegmentAction.KEEP)
    new = Segment(
        id="new",
        start=0.0,
        end=0.28,
        text="你好呀",
        words=[*old_left.words, *old_right.words],
    )

    result = inherit_resegment_results([old_left, old_right], [new])

    assert result.segments[0].action == SegmentAction.DELETE
    assert result.report.review_segment_ids == ["new"]
    assert result.old_to_new_ids == {"old-left": ["new"], "old-right": ["new"]}


def test_occurrence_reel_and_quote_references_are_rewritten():
    old = _segment("old", "产品很好", 0.0)
    new = _segment("new", "产品很好", 0.0)
    occurrence = ProductOccurrence(
        occurrence_id="occ-1",
        name="产品",
        start=0.0,
        end=0.38,
        segment_ids=["old"],
    )
    reel = HighlightReel(
        reel_id="reel-1",
        clip_group=1,
        title="产品集锦",
        segment_ids=["old"],
        hook_segment_id="old",
    )

    result = inherit_resegment_results(
        [old],
        [new],
        occurrences=[occurrence],
        reels=[reel],
        golden_quote_order=["old"],
        target_content_revision=2,
        invalidated_export_count=1,
    )

    assert result.occurrences[0].segment_ids == ["new"]
    assert result.reels[0].segment_ids == ["new"]
    assert result.reels[0].hook_segment_id == "new"
    assert result.reels[0].source_content_revision == 2
    assert result.golden_quote_order == ["new"]
    assert result.report.inherited_reel_count == 1
    assert result.report.invalidated_export_count == 1


def test_unmapped_segment_stays_keep_and_is_reported():
    old = _segment("old", "旧内容", 0.0, SegmentAction.DELETE)
    new = _segment("new", "新内容", 3.0)

    result = inherit_resegment_results([old], [new])

    assert result.segments[0].action == SegmentAction.KEEP
    assert result.report.unmapped_segment_ids == ["new"]
