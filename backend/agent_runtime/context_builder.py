"""Small context builders for Agent runtime calls."""

from __future__ import annotations

from typing import Any, Dict

from ..models.task import Task


def build_task_context(task: Task, *, max_segments: int = 20) -> Dict[str, Any]:
    """Return a compact, non-secret task context for Agent prompts or traces."""
    segments = task.audit_segments or []
    return {
        "task_id": task.id,
        "task_type": task.task_type.value if task.task_type else None,
        "status": task.status.value if task.status else None,
        "video_duration": task.video_duration,
        "segments_count": len(segments),
        "segments_sample": [
            {
                "id": seg.id,
                "start": seg.start,
                "end": seg.end,
                "text": seg.text[:120],
                "action": seg.action.value if seg.action else None,
            }
            for seg in segments[:max_segments]
        ],
        "highlight_reels_count": len(task.highlight_reels or []),
        "translation_videos_count": len(task.translation_videos or []),
    }

