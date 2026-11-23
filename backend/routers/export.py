"""FFmpeg/剪映导出、clip 预览、单集锦导出、导出取消与统一任务取消。"""

import os
import asyncio
import subprocess
import sys
import re
import hashlib
import json
import math
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Any, Dict

from fastapi import APIRouter, HTTPException

from ..models.task import (
    Task,
    TaskStatus,
    ExportRequest,
    Segment,
    SegmentAction,
    HighlightExportStatus,
    HighlightQualityStatus,
    HighlightReelStatus,
    HighlightReelVariant,
    ExportJob,
    ExportJobKind,
    ExportJobStatus,
    RenderProfile,
    RenderQualityReport,
    SubtitleStyle,
)
from ..services.task_store import get_store
from ..services.ffmpeg_executor import (
    export_ffmpeg_lossless,
    generate_srt,
    get_expected_export_duration,
    kill_export,
    get_video_info,
)
from ..services.jianying_builder import build_jianying_draft
from ..services.render_profiles import get_render_profile_settings
from ..services.render_quality_validator import validate_rendered_media
from ..services.preview_coordinator import join_preview
from ..services.highlight_lineage import (
    HighlightLineageError,
    resolve_highlight_reel_segment_ids,
)
from ..core.paths import EXPORTS_DIR
from ..core.ws import manager, log_to_ws
from ..core.task_runner import (
    _task_runners, _export_tasks, _resolve_cancel_rollback_status,
    _rollback_cancelled_task, _finish_execution, checkpoint_execution,
    RetryableExecutionError,
)
from ..agent_runtime.approvals import ApprovalRequired, assert_human_approval

router = APIRouter()


def _require_human_approval(tool_name: str, approval_token: Optional[str]) -> None:
    try:
        assert_human_approval(tool_name, approval_token)
    except ApprovalRequired as exc:
        raise HTTPException(400, str(exc)) from exc


def _safe_name(value: str) -> str:
    safe = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", value.strip())
    return safe.strip("._") or "goldenclip_export"


def _now() -> str:
    return datetime.now().isoformat()


def _export_url(output_path: str) -> str:
    """仅为 exports 根目录内的受控产物生成公开播放 URL。"""
    return f"/api/exports/{os.path.basename(output_path)}"


def _render_fingerprint(payload: Dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _source_media_snapshot(video_path: str) -> Dict[str, Any]:
    try:
        stat = os.stat(video_path)
    except OSError:
        return {"path": video_path, "size": None, "mtime_ns": None}
    return {
        "path": video_path,
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def _active_export_job(task: Task) -> Optional[ExportJob]:
    for job in reversed(task.export_jobs):
        if job.status in (ExportJobStatus.QUEUED, ExportJobStatus.RUNNING):
            return job
    return None


def _ensure_no_active_export(task: Task) -> None:
    job = _active_export_job(task)
    if job:
        raise HTTPException(409, f"已有导出正在进行: {job.id}")


def _find_export_job(task: Task, export_job_id: str) -> Optional[ExportJob]:
    return next((job for job in task.export_jobs if job.id == export_job_id), None)


def _find_reusable_render_job(
    task: Task,
    fingerprint: str,
    kind: ExportJobKind = ExportJobKind.FFMPEG,
) -> Optional[ExportJob]:
    for job in reversed(task.export_jobs):
        if (
            job.kind == kind
            and job.status == ExportJobStatus.SUCCEEDED
            and job.render_fingerprint == fingerprint
            and job.output_path
            and os.path.exists(job.output_path)
            and (job.quality_report is None or job.quality_report.passed)
        ):
            return job
    return None


def _find_reusable_jianying_job(task: Task, fingerprint: str) -> Optional[ExportJob]:
    for job in reversed(task.export_jobs):
        if (
            job.kind == ExportJobKind.JIANYING
            and job.status == ExportJobStatus.SUCCEEDED
            and job.render_fingerprint == fingerprint
            and job.draft_path
            and os.path.exists(job.draft_path)
        ):
            return job
    return None


def _set_job_status(
    task_id: str,
    export_job_id: str,
    status: ExportJobStatus,
    *,
    progress: Optional[float] = None,
    output_path: Optional[str] = None,
    output_url: Optional[str] = None,
    output_files: Optional[List[str]] = None,
    draft_path: Optional[str] = None,
    srt_path: Optional[str] = None,
    quality_report: Optional[RenderQualityReport] = None,
    error_message: Optional[str] = None,
) -> Optional[ExportJob]:
    store = get_store()
    task = store.get(task_id)
    if not task:
        return None
    job = _find_export_job(task, export_job_id)
    if not job:
        return None

    now = _now()
    job.status = status
    job.updated_at = now
    if status == ExportJobStatus.RUNNING and not job.started_at:
        job.started_at = now
    if status in (ExportJobStatus.SUCCEEDED, ExportJobStatus.FAILED, ExportJobStatus.CANCELLED):
        job.completed_at = now
    if progress is not None:
        job.progress = progress
    if output_path is not None:
        job.output_path = output_path
    if output_url is not None:
        job.output_url = output_url
    if output_files is not None:
        job.output_files = output_files
    if draft_path is not None:
        job.draft_path = draft_path
    if srt_path is not None:
        job.srt_path = srt_path
    if quality_report is not None:
        job.quality_report = quality_report
    if error_message is not None:
        job.error_message = error_message

    if status == ExportJobStatus.SUCCEEDED:
        is_final_ffmpeg = job.kind == ExportJobKind.FFMPEG and job.render_profile == RenderProfile.FINAL
        if output_path and is_final_ffmpeg:
            task.export_path = output_path
        if draft_path:
            task.jianying_draft_path = draft_path
        if is_final_ffmpeg:
            task.latest_export_job_id = export_job_id
    store.update(task)
    return job


def _create_export_job(
    task: Task,
    *,
    kind: ExportJobKind,
    request: Dict[str, Any],
    snapshot: Dict[str, Any],
    reel_id: Optional[str] = None,
    reel_variant: HighlightReelVariant = HighlightReelVariant.AFTER_QUALITY,
    render_profile: RenderProfile = RenderProfile.FINAL,
    render_fingerprint: Optional[str] = None,
) -> ExportJob:
    job = ExportJob(
        task_id=task.id,
        kind=kind,
        reel_id=reel_id,
        reel_variant=reel_variant,
        render_profile=render_profile,
        source_content_revision=task.content_revision or 0,
        source_highlight_revision=task.highlight_revision or 0,
        source_subtitle_revision=task.subtitle_revision or 0,
        request=request,
        snapshot=snapshot,
        render_fingerprint=render_fingerprint,
    )
    task.export_jobs.append(job)
    if kind == ExportJobKind.FFMPEG and render_profile == RenderProfile.FINAL:
        task.latest_export_job_id = job.id
    get_store().update(task)
    return job


async def _broadcast_export_job(task_id: str, event_type: str, job: ExportJob) -> None:
    await manager.broadcast(task_id, {
        "type": event_type,
        "task_id": task_id,
        "export_job_id": job.id,
        "job": job.model_dump(),
        "status": job.status.value,
        "kind": job.kind.value,
        "progress": job.progress,
    })


def _start_export_task(task_id: str, job: ExportJob, coroutine) -> asyncio.Task:
    """登记导出协程，同时把作业生命周期映射到统一可恢复执行记录。"""
    store = get_store()
    start_execution = getattr(store, "start_execution", None)
    if not callable(start_execution):
        task = asyncio.create_task(coroutine)
        _export_tasks[job.id] = task
        return task

    run, _ = start_execution(
        task_id,
        operation=f"export_{job.kind.value}",
        idempotency_key=job.id,
        input_fingerprint=job.render_fingerprint,
        max_retries=3,
    )
    stage = f"export:{job.kind.value}"

    async def runner():
        checkpoint_execution(
            task_id,
            run.run_id,
            run.generation,
            stage=stage,
            status="running",
        )
        try:
            await coroutine
        except asyncio.CancelledError:
            checkpoint_execution(
                task_id,
                run.run_id,
                run.generation,
                stage=stage,
                status="cancelled",
            )
            _finish_execution(task_id, run, "cancelled")
            raise
        except Exception as exc:
            checkpoint_execution(
                task_id,
                run.run_id,
                run.generation,
                stage=stage,
                status="failed",
            )
            _finish_execution(task_id, run, "failed", exc)
            raise
        else:
            task = store.get(task_id)
            persisted_job = _find_export_job(task, job.id) if task else None
            if persisted_job and persisted_job.status == ExportJobStatus.SUCCEEDED:
                checkpoint_execution(
                    task_id,
                    run.run_id,
                    run.generation,
                    stage=stage,
                    status="succeeded",
                    artifact_ref=persisted_job.output_path or persisted_job.draft_path,
                )
                _finish_execution(task_id, run, "succeeded")
            elif persisted_job and persisted_job.status == ExportJobStatus.CANCELLED:
                checkpoint_execution(
                    task_id,
                    run.run_id,
                    run.generation,
                    stage=stage,
                    status="cancelled",
                )
                _finish_execution(task_id, run, "cancelled")
            else:
                message = (
                    persisted_job.error_message
                    if persisted_job and persisted_job.error_message
                    else "导出作业未成功完成"
                )
                checkpoint_execution(
                    task_id,
                    run.run_id,
                    run.generation,
                    stage=stage,
                    status="failed",
                )
                def mark_retry_exhausted(stored_run):
                    stored_run.retry_state.retries_used = stored_run.retry_state.max_retries

                store.update_execution_run(
                    task_id,
                    run.run_id,
                    run.generation,
                    mark_retry_exhausted,
                )
                _finish_execution(
                    task_id,
                    run,
                    "failed",
                    RetryableExecutionError(message),
                )

    task = asyncio.create_task(runner())
    _export_tasks[job.id] = task
    return task


# ============================================================
# Export
# ============================================================

@router.post("/api/tasks/{task_id}/export/reveal")
async def reveal_export(task_id: str):
    """在运行后端的本机文件管理器中定位任务的最新导出结果。"""
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    latest_export_job_id = getattr(task, "latest_export_job_id", None)
    latest_job = _find_export_job(task, latest_export_job_id) if latest_export_job_id else None
    latest_path = None
    if latest_job:
        latest_path = latest_job.output_path or latest_job.draft_path
    latest_path = latest_path or getattr(task, "export_path", None)
    if not latest_path:
        raise HTTPException(404, "Export file not found")

    export_path = os.path.realpath(latest_path)
    exports_root = os.path.realpath(EXPORTS_DIR)
    if os.path.commonpath([export_path, exports_root]) != exports_root:
        raise HTTPException(403, "Export path is outside the exports directory")
    if not os.path.exists(export_path):
        raise HTTPException(404, "Export file not found")

    try:
        if sys.platform == "darwin":
            command = ["open", export_path] if os.path.isdir(export_path) else ["open", "-R", export_path]
        elif sys.platform == "win32":
            command = ["explorer", export_path] if os.path.isdir(export_path) else ["explorer", f"/select,{export_path}"]
        else:
            target = export_path if os.path.isdir(export_path) else os.path.dirname(export_path)
            command = ["xdg-open", target]
        subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        raise HTTPException(500, "Failed to open the export folder") from exc

    return {"success": True}


@router.post("/api/tasks/{task_id}/export/ffmpeg")
async def export_ffmpeg(task_id: str, req: ExportRequest):
    if req.render_profile == RenderProfile.FINAL:
        _require_human_approval("ffmpeg_export", req.approval_token)
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    if not task.audit_segments:
        raise HTTPException(400, "No segments to export")
    if not task.video_path or not os.path.exists(task.video_path):
        raise HTTPException(400, "Source video not found")

    _ensure_no_active_export(task)
    segments_snapshot = [seg.model_copy(deep=True) for seg in task.audit_segments]
    params_snapshot = task.params.model_copy(deep=True)
    golden_quote_order = list(task.golden_quote_order or [])
    output_name = req.output_name or f"{task.name}_output"
    source_snapshot = _source_media_snapshot(task.video_path)
    profile_settings = get_render_profile_settings(req.render_profile)
    request = req.model_dump()
    snapshot = {
        "video_path": task.video_path,
        "source_media": source_snapshot,
        "segments": [seg.model_dump() for seg in segments_snapshot],
        "params": params_snapshot.model_dump(),
        "golden_quote_order": golden_quote_order,
        "subtitle_revision": task.subtitle_revision or 0,
        "render_profile": req.render_profile.value,
        "render_settings": {
            "max_long_edge": profile_settings.max_long_edge,
            "preset": profile_settings.preset,
            "crf": profile_settings.crf,
            "force_disable_subtitles": profile_settings.force_disable_subtitles,
        },
    }
    fingerprint = _render_fingerprint({
        "source_media": source_snapshot,
        "segments": snapshot["segments"],
        "params": snapshot["params"],
        "golden_quote_order": golden_quote_order,
        "render_profile": req.render_profile.value,
        "burn_subtitles": (
            req.burn_subtitles
            and not profile_settings.force_disable_subtitles
        ),
        "subtitle_style": req.subtitle_style.model_dump() if req.subtitle_style else None,
    })
    reusable_job = _find_reusable_render_job(task, fingerprint, ExportJobKind.FFMPEG)
    if reusable_job:
        return {
            "success": True,
            "message": "已复用相同渲染条件的已有产物",
            "job": reusable_job.model_dump(),
        }
    job = _create_export_job(
        task,
        kind=ExportJobKind.FFMPEG,
        request=request,
        snapshot=snapshot,
        render_profile=req.render_profile,
        render_fingerprint=fingerprint,
    )
    await _broadcast_export_job(task_id, "export_job_created", job)

    _start_export_task(
        task_id,
        job,
        _run_ffmpeg_export(
            task_id,
            job.id,
            req,
            video_path=task.video_path,
            segments=segments_snapshot,
            params=params_snapshot,
            golden_quote_order=golden_quote_order,
            output_name=output_name,
        )
    )
    profile_label = {
        RenderProfile.DRAFT: "快速草稿",
        RenderProfile.PREVIEW: "成片预览",
        RenderProfile.FINAL: "最终导出",
    }[req.render_profile]
    return {"success": True, "message": f"{profile_label}已启动", "job": job.model_dump()}


def _build_clip_export_segments(all_segments: List[Segment], selected_segment_ids: List[str]) -> List[Segment]:
    """
    基于完整 audit_segments 构建一个 clip 的导出窗口。

    返回从首个选中段到最后一个选中段之间的完整序列：
    - 选中的段标记为 KEEP
    - 中间未选中的段标记为 DELETE

    这样导出时能保留原时间轴里的删除边界，避免把中间应删除内容重新带回。
    """
    if not all_segments or not selected_segment_ids:
        return []

    ordered_segments = sorted(all_segments, key=lambda seg: seg.start)
    selected_set = set(selected_segment_ids)
    selected_indices = [idx for idx, seg in enumerate(ordered_segments) if seg.id in selected_set]
    if not selected_indices:
        return []

    window_start = min(selected_indices)
    window_end = max(selected_indices)
    export_segments: List[Segment] = []
    for seg in ordered_segments[window_start:window_end + 1]:
        export_segments.append(
            seg.model_copy(
                deep=True,
                update={
                    "action": SegmentAction.KEEP if seg.id in selected_set else SegmentAction.DELETE,
                },
            )
        )
    return export_segments


def _resolve_reel_segment_ids(task: Task, reel) -> List[str]:
    try:
        return resolve_highlight_reel_segment_ids(
            reel,
            task.audit_segments or [],
            product_groups=task.product_groups,
            product_occurrences=task.product_occurrences,
        )
    except HighlightLineageError as exc:
        raise HTTPException(
            409,
            f"{exc}，请重新生成集锦后再导出",
        ) from exc


def _set_current_reel_export_status(
    task_id: str,
    reel_id: str,
    status: HighlightExportStatus,
) -> None:
    store = get_store()
    task = store.get(task_id)
    if not task:
        return
    reel = next(
        (item for item in task.highlight_reels if item.reel_id == reel_id),
        None,
    )
    if not reel:
        return
    reel.export_status = status
    store.update(task)


async def _render_clip_preview(task: Task, requested_ids: Any):
    """仅渲染已验证的完整选段快照，缓存绑定源文件和预览档位。"""
    if not task.video_path or not os.path.exists(task.video_path):
        raise HTTPException(400, "Source video not found")
    if not isinstance(requested_ids, list) or not requested_ids or not all(isinstance(sid, str) and sid for sid in requested_ids):
        raise HTTPException(400, "segment_ids 必须是非空片段 ID 数组")
    if len(set(requested_ids)) != len(requested_ids):
        raise HTTPException(400, "预览片段 ID 不得重复")
    source_ids = {segment.id for segment in task.audit_segments}
    if not set(requested_ids).issubset(source_ids):
        raise HTTPException(409, "集锦引用的字幕片段已变化，请重新生成后再预览")
    preview_segments = _build_clip_export_segments(task.audit_segments or [], requested_ids)
    if not preview_segments:
        raise HTTPException(400, "No segments found")
    if any(not math.isfinite(s.start) or not math.isfinite(s.end) or s.start < 0 or s.end <= s.start for s in preview_segments):
        raise HTTPException(409, "片段时间范围无效，请检查字幕时间轴")
    source_snapshot = _source_media_snapshot(task.video_path)
    cache_key = _render_fingerprint({
        "version": 2,
        "task_id": task.id,
        "source": source_snapshot,
        "segments": [[s.id, s.start, s.end, s.action.value] for s in preview_segments],
        "render_profile": RenderProfile.PREVIEW.value,
        "render_settings": vars(get_render_profile_settings(RenderProfile.PREVIEW)),
    })
    filename = f"preview_{cache_key[:32]}.mp4"
    output_path = str(EXPORTS_DIR / filename)
    expected_duration = get_expected_export_duration(preview_segments)
    video_path = task.video_path

    async def render():
        # 等待并发槽期间源文件可能被替换，不能以旧指纹发布新素材。
        if _source_media_snapshot(video_path) != source_snapshot:
            raise HTTPException(409, "源视频已变化，请重新预览")
        cached = False
        if os.path.exists(output_path):
            cached = (await asyncio.to_thread(validate_rendered_media, output_path, expected_duration)).passed
        preview_owner = f"preview-{cache_key}"
        if not cached:
            try:
                success = await export_ffmpeg_lossless(
                    video_path, preview_segments, output_path,
                    render_profile=RenderProfile.PREVIEW, task_id=preview_owner,
                )
            except asyncio.CancelledError:
                kill_export(preview_owner)
                raise
            if not success:
                raise HTTPException(500, "FFmpeg export failed")
        if _source_media_snapshot(video_path) != source_snapshot:
            Path(output_path).unlink(missing_ok=True)
            raise HTTPException(409, "源视频已变化，请重新预览")
        return {"url": f"/api/exports/{filename}", "cached": cached, "render_profile": "preview"}

    return await join_preview(str(Path(output_path).resolve()), render)


@router.post("/api/tasks/{task_id}/export/clip-preview")
async def export_clip_preview(task_id: str, body: dict):
    task = get_store().get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    # 新客户端携带 canonical 定位，旧客户端仍可只传 segment_ids。
    if body.get("reel_id"):
        return await export_highlight_preview(task_id, body)
    return await _render_clip_preview(task, body.get("segment_ids"))


@router.post("/api/tasks/{task_id}/export/highlight-preview")
async def export_highlight_preview(task_id: str, body: dict):
    """按 reel_id + quality_stage 读取 canonical 快照，同时保留旧 segment_ids 预览接口。"""
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    reel_id = str(body.get("reel_id") or "").strip()
    if not reel_id:
        raise HTTPException(400, "reel_id is required")
    try:
        quality_stage = HighlightReelVariant(
            body.get("quality_stage")
            or body.get("reel_variant")
            or body.get("variant")
            or HighlightReelVariant.AFTER_QUALITY
        )
    except ValueError:
        raise HTTPException(400, "Invalid quality_stage")
    source_reels = (
        task.highlight_reels_before_quality
        if quality_stage == HighlightReelVariant.BEFORE_QUALITY
        else task.highlight_reels
    )
    reel = next((item for item in source_reels if item.reel_id == reel_id), None)
    if not reel:
        raise HTTPException(404, "Highlight reel snapshot not found")
    reel = reel.model_copy(deep=True)
    if reel.source_content_revision is not None and reel.source_content_revision != (task.content_revision or 0):
        raise HTTPException(409, "集锦基于旧字幕生成，请更新集锦后再预览")
    resolved_ids = _resolve_reel_segment_ids(task, reel)
    result = await _render_clip_preview(task, resolved_ids)
    return {
        **result,
        "reel_id": reel.reel_id,
        "quality_stage": quality_stage.value,
        "occurrence_id": reel.occurrence_id,
        "strategy_kind": reel.strategy_kind.value if reel.strategy_kind else None,
        "segment_ids": list(reel.segment_ids),
    }


@router.post("/api/tasks/{task_id}/export/highlight-clips")
async def export_highlight_clips(task_id: str, body: dict):
    _require_human_approval("ffmpeg_export", body.get("approval_token"))
    """
    导出单条已生成产品集锦。
    Body: { "reel_id": "xxx", "output_name": "..." }
    """
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    if not task.audit_segments:
        raise HTTPException(400, "No segments to export")
    if not task.video_path or not os.path.exists(task.video_path):
        raise HTTPException(400, "Source video not found")

    reel_id: Optional[str] = body.get("reel_id")
    legacy_reel_ids: List[str] = body.get("reel_ids", [])
    if not reel_id and len(legacy_reel_ids) == 1:
        reel_id = legacy_reel_ids[0]
    output_name: str = body.get("output_name") or task.name
    burn_subtitles = bool(body.get("burn_subtitles", False))
    subtitle_style = (
        SubtitleStyle.model_validate(body["subtitle_style"])
        if burn_subtitles and body.get("subtitle_style")
        else None
    )

    if body.get("clip_groups") or len(legacy_reel_ids) > 1:
        raise HTTPException(400, "一次只能导出一条已生成的产品集锦")
    if not reel_id:
        raise HTTPException(400, "reel_id is required")

    try:
        reel_variant = HighlightReelVariant(body.get("reel_variant") or body.get("variant") or HighlightReelVariant.AFTER_QUALITY)
    except ValueError:
        raise HTTPException(400, "Invalid highlight reel variant")
    if reel_variant != HighlightReelVariant.AFTER_QUALITY:
        raise HTTPException(409, "质检前集锦仅供比较，不能导出")
    source_reels = task.highlight_reels
    reel = next((item for item in source_reels if item.reel_id == reel_id and not item.hidden), None)
    if not reel:
        raise HTTPException(404, "Highlight reel not found")
    if (
        reel.source_content_revision is not None
        and reel.source_content_revision != (task.content_revision or 0)
    ):
        raise HTTPException(409, "集锦基于旧字幕生成，请重新生成后再导出")
    hard_issues = [
        issue for issue in reel.issues if issue.severity in {"blocker", "major"}
    ]
    if hard_issues:
        raise HTTPException(409, "集锦仍有 blocker/major 质检问题，不能导出")
    quality_passed = (
        reel.quality_status == HighlightQualityStatus.PASSED
        if reel.quality_status is not None
        else reel.status in {HighlightReelStatus.PASSED, HighlightReelStatus.EXPORTED}
    )
    if not quality_passed:
        raise HTTPException(409, "集锦尚未通过质检，不能导出")
    if task.highlight_quality_report and task.highlight_quality_report.degraded:
        raise HTTPException(409, "集锦模型质检未完整完成，请重新质检后再导出")

    _ensure_no_active_export(task)
    group_ids = _resolve_reel_segment_ids(task, reel)
    group_segments = _build_clip_export_segments(task.audit_segments, group_ids)
    if not group_segments:
        raise HTTPException(400, "No valid segments found in highlight reel")

    request = {
        "reel_id": reel_id,
        "reel_variant": reel_variant.value,
        "output_name": output_name,
        "burn_subtitles": burn_subtitles,
        "subtitle_style": subtitle_style.model_dump() if subtitle_style else None,
        "approval_token": body.get("approval_token"),
    }
    source_media = _source_media_snapshot(task.video_path)
    render_segments = [seg.model_dump() for seg in group_segments]
    render_params = task.params.model_dump()
    fingerprint = _render_fingerprint({
        "source_media": source_media,
        "reel_id": reel_id,
        "reel_variant": reel_variant.value,
        "segments": render_segments,
        "params": render_params,
        "burn_subtitles": burn_subtitles,
        "subtitle_style": subtitle_style.model_dump() if subtitle_style else None,
        "render_profile": RenderProfile.FINAL.value,
    })
    snapshot = {
        "source_media": source_media,
        "reel_ref": {
            "reel_id": reel.reel_id,
            "product_group_id": reel.product_group_id,
            "strategy_kind": reel.strategy_kind.value if reel.strategy_kind else None,
        },
        "segment_ids": list(reel.segment_ids),
        "render_fingerprint": fingerprint,
        "content_revision": task.content_revision or 0,
        "highlight_revision": task.highlight_revision or 0,
        "subtitle_revision": task.subtitle_revision or 0,
    }
    reusable_job = _find_reusable_render_job(
        task,
        fingerprint,
        ExportJobKind.HIGHLIGHT_REEL,
    )
    if reusable_job:
        return {
            "success": True,
            "message": "已复用相同渲染条件的已有集锦产物",
            "job": reusable_job.model_dump(),
        }
    reel.export_status = HighlightExportStatus.QUEUED
    job = _create_export_job(
        task,
        kind=ExportJobKind.HIGHLIGHT_REEL,
        request=request,
        snapshot=snapshot,
        reel_id=reel_id,
        reel_variant=reel_variant,
        render_profile=RenderProfile.FINAL,
        render_fingerprint=fingerprint,
    )
    await _broadcast_export_job(task_id, "export_job_created", job)

    _start_export_task(
        task_id,
        job,
        _run_highlight_clips_export(
            task_id,
            job.id,
            group_segments,
            output_name,
            reel_id,
            reel_variant,
            reel.title,
            task.video_path or "",
            params=task.params.model_copy(deep=True),
            burn_subtitles=burn_subtitles,
            subtitle_style=subtitle_style,
        ),
    )
    return {"success": True, "message": "产品集锦导出已启动", "job": job.model_dump()}


async def _run_ffmpeg_export(
    task_id: str,
    export_job_id: str,
    req: ExportRequest,
    *,
    video_path: str,
    segments: List[Segment],
    params,
    golden_quote_order: List[str],
    output_name: str,
):
    job = _set_job_status(task_id, export_job_id, ExportJobStatus.RUNNING, progress=0.01)
    if job:
        await _broadcast_export_job(task_id, "export_job_started", job)

    async def log(level, source, msg, progress=None):
        if progress is not None:
            updated_job = _set_job_status(task_id, export_job_id, ExportJobStatus.RUNNING, progress=progress)
            if updated_job:
                await _broadcast_export_job(task_id, "export_job_progress", updated_job)
        await log_to_ws(task_id, level, source, msg, progress)

    try:
        safe_output = _safe_name(output_name)
        profile_suffix = req.render_profile.value
        output_path = str(EXPORTS_DIR / f"{safe_output}_{profile_suffix}_{export_job_id[:8]}.mp4")
        output_url = _export_url(output_path)
        profile_settings = get_render_profile_settings(req.render_profile)
        burn_subtitles = req.burn_subtitles and not profile_settings.force_disable_subtitles

        srt_path = None
        srt_part_path = None
        if req.render_profile != RenderProfile.DRAFT:
            srt_path = str(EXPORTS_DIR / f"{safe_output}_{profile_suffix}_{export_job_id[:8]}.srt")
            srt_part_path = f"{srt_path}.part"
            _vinfo = get_video_info(video_path)
            generate_srt(
                segments=segments,
                srt_path=srt_part_path,
                params=params,
                subtitle_style=req.subtitle_style,
                golden_quote_order=golden_quote_order if golden_quote_order else None,
                video_width=_vinfo.get("width", 0),
            )
            await log("info", "ffmpeg", f"SRT 字幕 staging 已生成: {srt_part_path}")
        else:
            await log("info", "ffmpeg", "快速草稿跳过字幕烧录与 SRT 生成")

        success = await export_ffmpeg_lossless(
            video_path=video_path,
            segments=segments,
            output_path=output_path,
            log_callback=log,
            burn_subtitles=burn_subtitles,
            srt_path=srt_part_path,
            task_id=export_job_id,
            subtitle_style=req.subtitle_style,
            golden_quote_order=golden_quote_order if golden_quote_order else None,
            render_profile=req.render_profile,
        )

        if success:
            expected_duration = get_expected_export_duration(
                segments,
                golden_quote_order if golden_quote_order else None,
            )
            quality_report = validate_rendered_media(output_path, expected_duration)
            if not quality_report.passed:
                Path(output_path).unlink(missing_ok=True)
                if srt_part_path:
                    Path(srt_part_path).unlink(missing_ok=True)
                updated_job = _set_job_status(
                    task_id,
                    export_job_id,
                    ExportJobStatus.FAILED,
                    output_path=output_path,
                    output_url=output_url,
                    srt_path=srt_path,
                    quality_report=quality_report,
                    error_message="导出产物未通过基础媒体校验",
                )
                if updated_job:
                    await _broadcast_export_job(task_id, "export_job_failed", updated_job)
                await log("error", "ffmpeg", "导出完成但未通过基础媒体校验，请检查导出控制台")
                return

            if srt_path and srt_part_path:
                os.replace(srt_part_path, srt_path)
            updated_job = _set_job_status(
                task_id,
                export_job_id,
                ExportJobStatus.SUCCEEDED,
                progress=1,
                output_path=output_path,
                output_url=output_url,
                srt_path=srt_path,
                quality_report=quality_report,
            )
            if updated_job:
                await _broadcast_export_job(task_id, "export_job_succeeded", updated_job)
            await manager.broadcast(task_id, {
                "type": "export_done",
                "task_id": task_id,
                "export_job_id": export_job_id,
                "output_path": output_path,
                "output_url": output_url,
                "srt_path": srt_path,
                "mode": "ffmpeg",
                "render_profile": req.render_profile.value,
                "quality_report": quality_report.model_dump(),
            })
        else:
            if srt_part_path:
                Path(srt_part_path).unlink(missing_ok=True)
            updated_job = _set_job_status(task_id, export_job_id, ExportJobStatus.FAILED, error_message="FFmpeg export failed")
            if updated_job:
                await _broadcast_export_job(task_id, "export_job_failed", updated_job)

    except asyncio.CancelledError:
        updated_job = _set_job_status(task_id, export_job_id, ExportJobStatus.CANCELLED)
        await log_to_ws(task_id, "warn", "ffmpeg", "⚠️ 导出已被用户提前终止，字幕编辑不受影响")
        if updated_job:
            await _broadcast_export_job(task_id, "export_job_cancelled", updated_job)
        await manager.broadcast(task_id, {
            "type": "export_cancelled",
            "task_id": task_id,
            "export_job_id": export_job_id,
        })

    except Exception as e:
        updated_job = _set_job_status(task_id, export_job_id, ExportJobStatus.FAILED, error_message=str(e))
        if updated_job:
            await _broadcast_export_job(task_id, "export_job_failed", updated_job)
        await log("error", "ffmpeg", f"导出失败: {str(e)}")

    finally:
        pending_srt = locals().get("srt_part_path")
        if pending_srt:
            Path(pending_srt).unlink(missing_ok=True)
        # 无论何种结束方式，都清理 asyncio Task 注册表
        _export_tasks.pop(export_job_id, None)


async def _run_highlight_clips_export(
    task_id: str,
    export_job_id: str,
    clip_segments: List[Segment],
    output_name: str,
    reel_id: str,
    reel_variant: HighlightReelVariant,
    reel_title: str,
    video_path: str,
    *,
    params,
    burn_subtitles: bool,
    subtitle_style: Optional[SubtitleStyle],
):
    job = _set_job_status(task_id, export_job_id, ExportJobStatus.RUNNING, progress=0.01)
    _set_current_reel_export_status(
        task_id,
        reel_id,
        HighlightExportStatus.RUNNING,
    )
    if job:
        await _broadcast_export_job(task_id, "export_job_started", job)

    async def log(level, source, msg, progress=None):
        if progress is not None:
            updated_job = _set_job_status(task_id, export_job_id, ExportJobStatus.RUNNING, progress=progress)
            if updated_job:
                await _broadcast_export_job(task_id, "export_job_progress", updated_job)
        await log_to_ws(task_id, level, source, msg, progress)

    try:
        safe_output = _safe_name(f"{output_name}_{reel_title or reel_id}")
        output_path = str(EXPORTS_DIR / f"{safe_output}_{export_job_id[:8]}.mp4")
        srt_path = str(EXPORTS_DIR / f"{safe_output}_{export_job_id[:8]}.srt")
        srt_part_path = f"{srt_path}.part"
        video_info = get_video_info(video_path)
        generate_srt(
            segments=clip_segments,
            srt_path=srt_part_path,
            params=params,
            subtitle_style=subtitle_style,
            video_width=video_info.get("width", 0),
        )
        await log("info", "ffmpeg", f"产品集锦 SRT staging 已生成: {srt_part_path}")
        success = await export_ffmpeg_lossless(
            video_path=video_path,
            segments=clip_segments,
            output_path=output_path,
            log_callback=log,
            task_id=export_job_id,
            burn_subtitles=burn_subtitles,
            srt_path=srt_part_path,
            subtitle_style=subtitle_style,
            render_profile=RenderProfile.FINAL,
        )

        if success:
            expected_duration = get_expected_export_duration(clip_segments)
            quality_report = validate_rendered_media(output_path, expected_duration)
            if not quality_report.passed:
                Path(output_path).unlink(missing_ok=True)
                Path(srt_part_path).unlink(missing_ok=True)
                task_fresh = get_store().get(task_id)
                if task_fresh:
                    target_reels = (
                        task_fresh.highlight_reels_before_quality
                        if reel_variant == HighlightReelVariant.BEFORE_QUALITY
                        else task_fresh.highlight_reels
                    )
                    failed_reel = next((item for item in target_reels if item.reel_id == reel_id), None)
                    if failed_reel:
                        failed_reel.export_status = HighlightExportStatus.FAILED
                        get_store().update(task_fresh)
                updated_job = _set_job_status(
                    task_id,
                    export_job_id,
                    ExportJobStatus.FAILED,
                    quality_report=quality_report,
                    error_message="产品集锦未通过基础媒体校验",
                )
                if updated_job:
                    await _broadcast_export_job(task_id, "export_job_failed", updated_job)
                await log("error", "ffmpeg", "产品集锦未通过 ffprobe 质量校验")
                return

            os.replace(srt_part_path, srt_path)
            updated_job = _set_job_status(
                task_id,
                export_job_id,
                ExportJobStatus.SUCCEEDED,
                progress=1,
                output_path=output_path,
                output_files=[output_path],
                srt_path=srt_path,
                quality_report=quality_report,
            )
            task_fresh = get_store().get(task_id)
            revisions_match = bool(
                task_fresh
                and updated_job
                and updated_job.source_content_revision == (task_fresh.content_revision or 0)
                and updated_job.source_highlight_revision == (task_fresh.highlight_revision or 0)
                and updated_job.source_subtitle_revision == (task_fresh.subtitle_revision or 0)
            )
            if task_fresh and revisions_match:
                target_reels = (
                    task_fresh.highlight_reels_before_quality
                    if reel_variant == HighlightReelVariant.BEFORE_QUALITY
                    else task_fresh.highlight_reels
                )
                reel = next((item for item in target_reels if item.reel_id == reel_id), None)
                if reel:
                    reel.export_path = output_path
                    reel.export_status = HighlightExportStatus.SUCCEEDED
                    reel.exported_revision = updated_job.source_content_revision if updated_job else task_fresh.content_revision
                    reel.exported_subtitle_revision = updated_job.source_subtitle_revision if updated_job else task_fresh.subtitle_revision
                    reel.exported_job_id = export_job_id
                    get_store().update(task_fresh)
            elif task_fresh:
                await log(
                    "warn",
                    "ffmpeg",
                    "集锦导出完成，但任务 revision 已变化；仅保留历史导出作业，不更新当前 winner",
                )
            if updated_job:
                await _broadcast_export_job(task_id, "export_job_succeeded", updated_job)
            await manager.broadcast(task_id, {
                "type": "export_done",
                "task_id": task_id,
                "export_job_id": export_job_id,
                "output_path": output_path,
                "output_files": [output_path],
                "srt_path": srt_path,
                "mode": "highlight_clips",
                "clips_count": 1,
                "quality_report": quality_report.model_dump(),
            })
        else:
            Path(srt_part_path).unlink(missing_ok=True)
            task_fresh = get_store().get(task_id)
            if task_fresh:
                target_reels = (
                    task_fresh.highlight_reels_before_quality
                    if reel_variant == HighlightReelVariant.BEFORE_QUALITY
                    else task_fresh.highlight_reels
                )
                reel = next((item for item in target_reels if item.reel_id == reel_id), None)
                if reel:
                    reel.export_status = HighlightExportStatus.FAILED
                    get_store().update(task_fresh)
            updated_job = _set_job_status(task_id, export_job_id, ExportJobStatus.FAILED, error_message="Highlight reel export failed")
            if updated_job:
                await _broadcast_export_job(task_id, "export_job_failed", updated_job)

    except asyncio.CancelledError:
        updated_job = _set_job_status(task_id, export_job_id, ExportJobStatus.CANCELLED)
        _set_current_reel_export_status(
            task_id,
            reel_id,
            HighlightExportStatus.CANCELLED,
        )
        await log_to_ws(task_id, "warn", "ffmpeg", "⚠️ 产品集锦导出已被用户提前终止，字幕编辑不受影响")
        if updated_job:
            await _broadcast_export_job(task_id, "export_job_cancelled", updated_job)
        await manager.broadcast(task_id, {
            "type": "export_cancelled",
            "task_id": task_id,
            "export_job_id": export_job_id,
        })

    except Exception as e:
        updated_job = _set_job_status(task_id, export_job_id, ExportJobStatus.FAILED, error_message=str(e))
        _set_current_reel_export_status(
            task_id,
            reel_id,
            HighlightExportStatus.FAILED,
        )
        if updated_job:
            await _broadcast_export_job(task_id, "export_job_failed", updated_job)
        await log("error", "ffmpeg", f"多片段导出失败: {str(e)}")

    finally:
        pending_srt = locals().get("srt_part_path")
        if pending_srt:
            Path(pending_srt).unlink(missing_ok=True)
        _export_tasks.pop(export_job_id, None)


@router.post("/api/tasks/{task_id}/export/cancel")
async def cancel_export(task_id: str):
    """兼容入口：终止该任务当前唯一运行中的导出作业。"""
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    job = _active_export_job(task)
    if not job:
        raise HTTPException(400, "No export running for this task")
    return await cancel_export_job(job.id)


@router.post("/api/exports/{export_job_id}/cancel")
async def cancel_export_job(export_job_id: str):
    """终止指定导出作业，不改变主任务状态。"""
    store = get_store()
    task = next((item for item in store.get_all() if _find_export_job(item, export_job_id)), None)
    if not task:
        raise HTTPException(404, "Export job not found")
    job = _find_export_job(task, export_job_id)
    if not job or job.status not in (ExportJobStatus.QUEUED, ExportJobStatus.RUNNING):
        raise HTTPException(400, "Export job is not running")

    # 1. 终止 FFmpeg 子进程
    kill_export(export_job_id)

    # 2. 取消 asyncio Task（触发 CancelledError，由导出协程内部处理清理逻辑）
    t = _export_tasks.pop(export_job_id, None)
    if t and not t.done():
        t.cancel()
    else:
        updated_job = _set_job_status(task.id, export_job_id, ExportJobStatus.CANCELLED)
        await log_to_ws(task.id, "warn", "ffmpeg", "⚠️ 导出已被用户提前终止，字幕编辑不受影响")
        if updated_job:
            await _broadcast_export_job(task.id, "export_job_cancelled", updated_job)
        await manager.broadcast(task.id, {
            "type": "export_cancelled",
            "task_id": task.id,
            "export_job_id": export_job_id,
        })

    return {"success": True, "message": "导出已终止"}


@router.post("/api/tasks/{task_id}/cancel")
async def cancel_task(task_id: str):
    """
    终止任务当前正在运行的后台协程（ASR / 语义审计 / 一键翻译）。
    状态为 export_running 时委托给现有的导出取消逻辑。
    """
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")

    if task.status == TaskStatus.EXPORT_RUNNING:
        kill_export(task_id)
        t = _export_tasks.pop(task_id, None)
        if t and not t.done():
            t.cancel()
        else:
            task.status = TaskStatus.REVIEW
            store.update(task)
            await manager.broadcast(task_id, {"type": "status_change", "task_id": task_id, "status": "review"})
        return {"success": True, "message": "导出已终止"}

    fallback = _resolve_cancel_rollback_status(task)
    if fallback is None:
        raise HTTPException(400, "No cancellable background task for this task")

    # ASR 管道等如有以 task_id 注册的 FFmpeg 子进程，顺手终止（通常没有，无害）
    kill_export(task_id)

    t = _task_runners.get(task_id)
    if t and not t.done():
        # 正常路径：保留注册信息直到 done callback 清理，确保取消处理器仍能
        # 读取 execution run 并正确判断当前协程没有被新任务替换。
        t.cancel()
    else:
        # 兜底路径：注册表无协程（竞态/重启场景），直接在此回滚并广播
        await _rollback_cancelled_task(task_id, fallback, "system", "后台任务")

    return {"success": True, "message": "任务已终止"}


# ============================================================
# 剪映草稿导出
# ============================================================

@router.post("/api/tasks/{task_id}/export/jianying")
async def export_jianying(task_id: str, req: ExportRequest):
    _require_human_approval("jianying_export", req.approval_token)
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    if not task.audit_segments:
        raise HTTPException(400, "No segments to export")

    _ensure_no_active_export(task)
    segments_snapshot = [seg.model_copy(deep=True) for seg in task.audit_segments]
    params_snapshot = task.params.model_copy(deep=True)
    draft_name = req.output_name or f"{task.name}_draft"
    request = req.model_dump()
    snapshot = {
        "video_path": task.video_path,
        "source_media": (
            _source_media_snapshot(task.video_path)
            if task.video_path and os.path.exists(task.video_path)
            else {"path": task.video_path}
        ),
        "segments": [seg.model_dump() for seg in segments_snapshot],
        "params": params_snapshot.model_dump(),
    }
    fingerprint = _render_fingerprint({
        "source_media": snapshot["source_media"],
        "segments": snapshot["segments"],
        "params": snapshot["params"],
        "draft_name": draft_name,
        "draft_folder": req.jianying_draft_folder,
    })
    reusable_job = _find_reusable_jianying_job(task, fingerprint)
    if reusable_job:
        return {
            "success": True,
            "message": "已复用相同条件的剪映草稿",
            "job": reusable_job.model_dump(),
        }
    job = _create_export_job(
        task,
        kind=ExportJobKind.JIANYING,
        request=request,
        snapshot=snapshot,
        render_fingerprint=fingerprint,
    )
    await _broadcast_export_job(task_id, "export_job_created", job)

    _start_export_task(
        task_id,
        job,
        _run_jianying_export(
            task_id,
            job.id,
            req,
            video_path=task.video_path or "",
            segments=segments_snapshot,
            params=params_snapshot,
            draft_name=draft_name,
        )
    )
    return {"success": True, "message": "剪映草稿生成已启动", "job": job.model_dump()}


async def _run_jianying_export(
    task_id: str,
    export_job_id: str,
    req: ExportRequest,
    *,
    video_path: str,
    segments: List[Segment],
    params,
    draft_name: str,
):
    job = _set_job_status(task_id, export_job_id, ExportJobStatus.RUNNING, progress=0.01)
    if job:
        await _broadcast_export_job(task_id, "export_job_started", job)

    async def log(level, source, msg, progress=None):
        if progress is not None:
            updated_job = _set_job_status(task_id, export_job_id, ExportJobStatus.RUNNING, progress=progress)
            if updated_job:
                await _broadcast_export_job(task_id, "export_job_progress", updated_job)
        await log_to_ws(task_id, level, source, msg, progress)

    try:
        draft_path = await build_jianying_draft(
            video_path=video_path,
            segments=segments,
            draft_name=draft_name,
            draft_folder=req.jianying_draft_folder,
            log_callback=log,
            params=params,
        )

        if draft_path:
            updated_job = _set_job_status(
                task_id,
                export_job_id,
                ExportJobStatus.SUCCEEDED,
                progress=1,
                draft_path=draft_path,
            )
            if updated_job:
                await _broadcast_export_job(task_id, "export_job_succeeded", updated_job)
            await manager.broadcast(task_id, {
                "type": "export_done",
                "task_id": task_id,
                "export_job_id": export_job_id,
                "draft_path": draft_path,
                "mode": "jianying"
            })
        else:
            updated_job = _set_job_status(task_id, export_job_id, ExportJobStatus.FAILED, error_message="Jianying draft export failed")
            if updated_job:
                await _broadcast_export_job(task_id, "export_job_failed", updated_job)

    except asyncio.CancelledError:
        updated_job = _set_job_status(task_id, export_job_id, ExportJobStatus.CANCELLED)
        await log_to_ws(task_id, "warn", "jianying", "⚠️ 剪映草稿导出已被用户提前终止，字幕编辑不受影响")
        if updated_job:
            await _broadcast_export_job(task_id, "export_job_cancelled", updated_job)
        await manager.broadcast(task_id, {
            "type": "export_cancelled",
            "task_id": task_id,
            "export_job_id": export_job_id,
        })

    except Exception as e:
        updated_job = _set_job_status(task_id, export_job_id, ExportJobStatus.FAILED, error_message=str(e))
        if updated_job:
            await _broadcast_export_job(task_id, "export_job_failed", updated_job)
        await log("error", "jianying", f"草稿生成失败: {str(e)}")

    finally:
        _export_tasks.pop(export_job_id, None)
