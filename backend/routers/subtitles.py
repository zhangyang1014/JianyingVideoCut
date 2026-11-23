"""Non-destructive subtitle text optimization routes."""

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..models.task import ExportJobStatus, TaskStatus
from ..services.subtitle_optimizer import (
    build_subtitle_optimization_changes,
    glossary_fingerprint,
    preview_token,
)
from ..services.asr_pipeline import load_glossary
from ..services.task_store import get_store
from ..core.ws import manager

router = APIRouter()


_BLOCKING_STATUSES = {
    TaskStatus.ASR_RUNNING,
    TaskStatus.DIARIZATION_RUNNING,
    TaskStatus.ANALYZE_RUNNING,
    TaskStatus.AUDIT_RUNNING,
    TaskStatus.EXPORT_RUNNING,
    TaskStatus.TRANSLATE_RUNNING,
    TaskStatus.TTS_RUNNING,
    TaskStatus.DUBBING_RUNNING,
}


class SubtitleOptimizePreviewRequest(BaseModel):
    segment_ids: Optional[List[str]] = None


class SubtitleOptimizeApplyRequest(BaseModel):
    selected_segment_ids: List[str] = Field(default_factory=list)
    expected_subtitle_revision: int
    glossary_fingerprint: str
    preview_token: Optional[str] = None


def _has_active_export(task) -> bool:
    return any(
        job.status in {ExportJobStatus.QUEUED, ExportJobStatus.RUNNING}
        for job in (task.export_jobs or [])
    )


def _ensure_can_mutate_subtitles(task) -> None:
    if task.status in _BLOCKING_STATUSES or _has_active_export(task):
        raise HTTPException(status_code=409, detail="当前有任务正在运行，暂不能优化字幕")
    if not task.audit_segments:
        raise HTTPException(status_code=400, detail="当前任务还没有字幕段，无法优化")


def _change_payload(change):
    return {
        "segment_id": change.segment_id,
        "start": change.start,
        "end": change.end,
        "before": change.before,
        "after": change.after,
        "reasons": change.reasons,
    }


@router.post("/api/tasks/{task_id}/subtitles/optimize/preview")
async def preview_subtitle_optimization(task_id: str, req: SubtitleOptimizePreviewRequest = SubtitleOptimizePreviewRequest()):
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    _ensure_can_mutate_subtitles(task)

    glossary = load_glossary()
    fingerprint = glossary_fingerprint(glossary)
    changes = build_subtitle_optimization_changes(
        task.audit_segments,
        segment_ids=req.segment_ids,
        glossary=glossary,
    )
    token = preview_token(changes, task.subtitle_revision or 0, fingerprint)
    return {
        "subtitle_revision": task.subtitle_revision or 0,
        "glossary_fingerprint": fingerprint,
        "preview_token": token,
        "total_segments": len(task.audit_segments),
        "changed_count": len(changes),
        "preserved": {
            "audit_segments": len(task.audit_segments),
            "audit_history": len(task.audit_history or []),
            "highlight_reels": len(task.highlight_reels or []),
            "golden_quotes": len(task.golden_quote_order or []),
            "export_jobs": len(task.export_jobs or []),
        },
        "changes": [_change_payload(change) for change in changes],
    }


@router.post("/api/tasks/{task_id}/subtitles/optimize/apply")
async def apply_subtitle_optimization(task_id: str, req: SubtitleOptimizeApplyRequest):
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    _ensure_can_mutate_subtitles(task)

    current_revision = task.subtitle_revision or 0
    if current_revision != req.expected_subtitle_revision:
        raise HTTPException(status_code=409, detail="字幕已被修改，请重新预览后再应用")

    glossary = load_glossary()
    fingerprint = glossary_fingerprint(glossary)
    if fingerprint != req.glossary_fingerprint:
        raise HTTPException(status_code=409, detail="词典已更新，请重新预览后再应用")

    changes = build_subtitle_optimization_changes(
        task.audit_segments,
        segment_ids=req.selected_segment_ids,
        glossary=glossary,
    )
    if req.preview_token:
        expected_token = preview_token(changes, current_revision, fingerprint)
        if expected_token != req.preview_token:
            raise HTTPException(status_code=409, detail="字幕内容已变化，请重新预览后再应用")

    selected = set(req.selected_segment_ids)
    changes = [change for change in changes if change.segment_id in selected]
    if not changes:
        return {
            "success": True,
            "changed_count": 0,
            "subtitle_revision": current_revision,
            "task": task,
        }

    change_map = {change.segment_id: change for change in changes}
    updated_at = datetime.now().isoformat()
    for seg in task.audit_segments:
        change = change_map.get(seg.id)
        if not change:
            continue
        base_text = (seg.display_text or seg.text or "").strip()
        seg.subtitle_text = change.after if change.after != base_text else None
        seg.subtitle_text_source = "optimizer" if seg.subtitle_text is not None else None
        seg.subtitle_updated_at = updated_at if seg.subtitle_text is not None else None

    task.subtitle_revision = current_revision + 1
    task.last_subtitle_optimization = {
        "updated_at": updated_at,
        "changed_count": len(changes),
        "glossary_fingerprint": fingerprint,
        "segments": [
            {
                "segment_id": change.segment_id,
                "before": change.before,
                "after": change.after,
                "reasons": change.reasons,
            }
            for change in changes
        ],
    }
    store.update(task)

    await manager.broadcast(task_id, {
        "type": "subtitle_optimized",
        "task_id": task_id,
        "changed_count": len(changes),
        "subtitle_revision": task.subtitle_revision,
    })
    return {
        "success": True,
        "changed_count": len(changes),
        "subtitle_revision": task.subtitle_revision,
        "changes": [_change_payload(change) for change in changes],
        "task": task,
    }
