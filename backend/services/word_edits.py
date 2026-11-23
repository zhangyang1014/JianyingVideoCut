"""将明确的字级删除编译为可预览、可导出的子段，保留原始 ASR 快照。"""

from __future__ import annotations

import math
import re
from typing import Sequence

from ..models.task import Segment, SegmentAction, SourceWordRange


def clean_word_text(text: str) -> str:
    return re.sub(r"<[^>]*>", "", text)


def same_word_edit(left: Segment | None, right: Segment | None) -> bool:
    """精确切除后的一句话仍是同一语义单元，不应被局部残句规则自动补回。"""
    return bool(left and right and left.semantic_unit_id
                and left.semantic_unit_id.startswith("word-edit:")
                and left.semantic_unit_id == right.semantic_unit_id)


def planned_delete_duration(segment: Segment) -> float:
    if segment.action == SegmentAction.DELETE:
        return max(0.0, segment.end - segment.start)
    if not segment.word_deletions or word_deletion_error(segment, segment.word_deletions):
        return 0.0
    deleted = {index for item in segment.word_deletions for index in range(item.start_word_index, item.end_word_index)}
    return sum(
        (segment.end if index + 1 == len(segment.words) else segment.words[index + 1].start)
        - (segment.start if index == 0 else segment.words[index].start)
        for index in deleted
    )


def word_deletion_error(segment: Segment, ranges: Sequence[SourceWordRange]) -> str | None:
    words = segment.words
    if not words:
        return "没有字级时间戳，不能执行词级切除"
    previous_end = segment.start
    for word in words:
        if (not math.isfinite(word.start) or not math.isfinite(word.end)
                or word.start < previous_end - 0.001 or word.end <= word.start
                or word.end > segment.end + 0.001):
            return "字级时间戳重叠、越界或无效，不能执行词级切除"
        previous_end = word.end
    normalize = lambda text: re.sub(r"\s+", "", clean_word_text(text))
    if normalize("".join(word.word for word in words)) != normalize(segment.text):
        return "原文与字级文本不一致，不能执行词级切除"
    if segment.subtitle_text is not None and normalize(segment.subtitle_text) != normalize(segment.text):
        return "已修改的字幕需要先复核，不能自动进行词级切除"
    for item in ranges:
        start, end = item.start_word_index, item.end_word_index
        if item.segment_id != segment.id or start is None or end is None or not (0 <= start < end <= len(words)):
            return "词级删除范围必须引用当前片段的有效半开字索引"
    if segment.action != SegmentAction.KEEP:
        return "同一片段的词级删除不能与整段删除、拆分或字幕改写同时执行"
    return None


def apply_word_deletions(segments: Sequence[Segment]) -> list[Segment]:
    result: list[Segment] = []
    for segment in segments:
        if not segment.word_deletions:
            result.append(segment)
            continue
        error = word_deletion_error(segment, segment.word_deletions)
        if error:
            raise ValueError(f"{segment.id}: {error}")
        deleted = {
            index
            for item in segment.word_deletions
            for index in range(item.start_word_index, item.end_word_index)
        }
        boundaries = [0] + [
            index for index in range(1, len(segment.words))
            if (index in deleted) != (index - 1 in deleted)
        ] + [len(segment.words)]
        for start, end in zip(boundaries, boundaries[1:]):
            part = segment.model_copy(deep=True)
            part.id = f"{segment.id}:words:{start}-{end}"
            part.words = part.words[start:end]
            # 把字间气口留给前一个子段；所有子段恰好覆盖原时间轴。
            part.start = segment.start if start == 0 else segment.words[start].start
            part.end = segment.end if end == len(segment.words) else segment.words[end].start
            part.text = clean_word_text("".join(word.word for word in part.words))
            part.tagged_text = "".join(word.word for word in part.words)
            part.action = SegmentAction.DELETE if start in deleted else SegmentAction.KEEP
            part.word_deletions = []
            part.semantic_unit_id = f"word-edit:{segment.id}:{segment.compiled_from_operation or ''}"
            part.source_segment_ids = [segment.id]
            part.source_word_ranges = [SourceWordRange(segment_id=segment.id, start_word_index=start, end_word_index=end)]
            part.display_text = None
            part.subtitle_text = None
            part.subtitle_text_source = None
            part.subtitle_updated_at = None
            part.subtitle_line_break = None
            part.candidate_edits = [edit for edit in part.candidate_edits if edit.start < part.end and edit.end > part.start]
            part.claude_action = part.action
            if part.action == SegmentAction.KEEP:
                part.reason = "词级精修：保留原句有效内容"
                part.rule = None
            part.claude_reason = part.reason
            result.append(part)
    return result
