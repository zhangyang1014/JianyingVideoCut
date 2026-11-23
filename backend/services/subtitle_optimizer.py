"""Non-destructive subtitle text optimization."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Iterable, List, Optional

from ..models.task import Segment
from .asr_pipeline import GlossaryEntry, load_glossary
from .subtitle_text import get_effective_subtitle_text


@dataclass(frozen=True)
class SubtitleOptimizationChange:
    segment_id: str
    start: float
    end: float
    before: str
    after: str
    reasons: List[str]


def glossary_fingerprint(glossary: Optional[List[GlossaryEntry]] = None) -> str:
    entries = glossary if glossary is not None else load_glossary()
    payload = [
        {"source": entry.source, "target": entry.target}
        for entry in entries
    ]
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _normalize_spaces(text: str) -> str:
    return re.sub(r"[ \t]+", " ", text).strip()


def _collapsed_ascii_duplicate_variant(value: str) -> Optional[str]:
    if not re.fullmatch(r"[A-Za-z0-9]+", value):
        return None
    collapsed = re.sub(r"([A-Za-z0-9])\1+", r"\1", value, flags=re.IGNORECASE)
    if collapsed.lower() == value.lower() or len(collapsed) < 2:
        return None
    return collapsed


def _ascii_token_pattern(value: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![A-Za-z0-9]){re.escape(value)}(?![A-Za-z0-9])", flags=re.IGNORECASE)


def optimize_subtitle_text(text: str, glossary: Optional[List[GlossaryEntry]] = None) -> tuple[str, List[str]]:
    """Apply text-only cleanup rules to a single subtitle string."""
    entries = glossary if glossary is not None else load_glossary()
    result = text
    reasons: List[str] = []

    normalized = _normalize_spaces(result)
    if normalized != result:
        result = normalized
        reasons.append("空白规范化")

    glossary_changes = 0
    near_glossary_changes = 0
    for entry in entries:
        if not entry.source or not entry.target:
            continue
        pattern = re.compile(re.escape(entry.source), flags=re.IGNORECASE)
        result, count = pattern.subn(entry.target, result)
        glossary_changes += count

        collapsed = _collapsed_ascii_duplicate_variant(entry.source)
        if collapsed:
            result, near_count = _ascii_token_pattern(collapsed).subn(entry.target, result)
            near_glossary_changes += near_count

    if glossary_changes:
        reasons.append(f"词典纠错 {glossary_changes} 处")
    if near_glossary_changes:
        reasons.append(f"词典近似修复 {near_glossary_changes} 处")

    return result, reasons


def build_subtitle_optimization_changes(
    segments: Iterable[Segment],
    *,
    segment_ids: Optional[Iterable[str]] = None,
    glossary: Optional[List[GlossaryEntry]] = None,
) -> List[SubtitleOptimizationChange]:
    selected = set(segment_ids or [])
    entries = glossary if glossary is not None else load_glossary()
    changes: List[SubtitleOptimizationChange] = []

    for seg in segments:
        if selected and seg.id not in selected:
            continue
        before = get_effective_subtitle_text(seg, include_speaker_label=False)
        if not before:
            continue
        after, reasons = optimize_subtitle_text(before, entries)
        if after != before:
            changes.append(
                SubtitleOptimizationChange(
                    segment_id=seg.id,
                    start=seg.start,
                    end=seg.end,
                    before=before,
                    after=after,
                    reasons=reasons,
                )
            )

    return changes


def preview_token(changes: List[SubtitleOptimizationChange], subtitle_revision: int, fingerprint: str) -> str:
    payload = {
        "subtitle_revision": subtitle_revision,
        "glossary_fingerprint": fingerprint,
        "changes": [
            {
                "segment_id": change.segment_id,
                "before": change.before,
                "after": change.after,
            }
            for change in changes
        ],
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
