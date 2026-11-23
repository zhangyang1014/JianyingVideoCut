"""Shared helpers for reading the effective subtitle text."""

from typing import Optional

from ..models.task import Segment, TaskParams


def get_effective_subtitle_text(
    seg: Segment,
    params: Optional[TaskParams] = None,
    *,
    include_speaker_label: bool = True,
) -> str:
    """Return the final subtitle text without mutating the source segment."""
    text = (seg.subtitle_text or seg.display_text or seg.text or "").strip()
    if include_speaker_label and params and params.show_speaker_label and seg.speaker:
        speaker_names = params.speaker_names or {}
        label = speaker_names.get(seg.speaker, seg.speaker)
        text = f"[{label}] {text}"
    return text


def set_manual_subtitle_text(seg: Segment, text: str, *, updated_at: Optional[str] = None) -> bool:
    """Set or clear a manual subtitle override. Returns whether the effective text changed."""
    normalized = text.strip()
    previous = get_effective_subtitle_text(seg, include_speaker_label=False)
    base_text = (seg.display_text or seg.text or "").strip()
    seg.subtitle_text = normalized if normalized != base_text else None
    seg.subtitle_text_source = "manual" if seg.subtitle_text is not None else None
    seg.subtitle_updated_at = updated_at if seg.subtitle_text is not None else None
    return normalized != previous
