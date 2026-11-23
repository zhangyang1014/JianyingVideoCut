"""ASR 触发/版本切换/说话人重平滑/动态重分段，及后台 ASR 协程。"""

import os
import asyncio
from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..models.task import (
    AnalysisSnapshot,
    DEFAULT_SEGMENT_SILENCE_THRESHOLD,
    MAX_SEGMENT_SILENCE_THRESHOLD,
    MIN_SEGMENT_SILENCE_THRESHOLD,
    ResegmentInheritanceReport,
    TaskStatus, SegmentAction, ASRSnapshot, resolve_enable_diarization,
)
from ..services.task_store import get_store
from ..services.asr_pipeline import (
    run_asr_recognize, run_asr_diarize, run_asr_analyze, strip_analysis_tags_from_words,
    resmooth_speakers, segment_by_silence, postprocess_segments, MAX_SEGMENT_DURATION,
    _build_segment_note, cancel_asr_processes,
)
from ..services.resegment_inheritance import inherit_resegment_results
from ..services.execution_preview import (
    ExecutionPreviewError,
    validate_execution_preview_token,
)
from ..core.ws import manager, log_to_ws
from ..core.task_runner import (
    _start_task_runner, _task_runner_superseded, _rollback_cancelled_task,
    _resolve_cancel_rollback_status,
)

router = APIRouter()

_BLOCKING_RUNNING_STATUSES = {
    TaskStatus.ASR_RUNNING,
    TaskStatus.DIARIZATION_RUNNING,
    TaskStatus.ANALYZE_RUNNING,
    TaskStatus.AUDIT_RUNNING,
    TaskStatus.EXPORT_RUNNING,
    TaskStatus.TRANSLATE_RUNNING,
    TaskStatus.TTS_RUNNING,
    TaskStatus.DUBBING_RUNNING,
}


def _has_diarization_result(task) -> bool:
    return bool(task.asr_result and (task.asr_result.diar_segments or task.asr_result.speakers))


def _recognized_fallback_status(task) -> TaskStatus:
    return TaskStatus.ASR_DIARIZED if _has_diarization_result(task) else TaskStatus.ASR_RECOGNIZED


def _ensure_status_allowed(task, allowed: set[TaskStatus], action: str):
    if task.status not in allowed:
        raise HTTPException(
            status_code=409,
            detail=f"当前状态 {task.status} 不能执行{action}",
        )


def _clear_downstream_results(task):
    """重做分离/分析后，下游审计、集锦和导出结果不再可信。"""
    task.audit_segments = []
    task.audit_json_path = None
    task.audit_history = []
    task.audit_current_version = None
    task.highlight_reels = []
    task.highlight_reels_before_quality = []
    task.highlight_quality_report = None
    task.highlight_quality_report_before_quality = None
    task.product_occurrences = []
    task.product_groups = []
    task.highlight_strategy_quality_results = []
    task.audit_coherence_report = None
    task.export_path = None
    task.jianying_draft_path = None
    task.latest_export_job_id = None
    task.golden_quote_order = []
    task.segments_kept = 0
    task.segments_deleted = 0
    task.edited_duration = None
    task.content_revision = (task.content_revision or 0) + 1


# ============================================================
# ASR Processing
# ============================================================

class AsrRequest(BaseModel):
    model: str = "base"
    backend: str = "whisper"  # "whisper" | "funasr"
    enable_diarization: Optional[bool] = None
    preview_token: Optional[str] = None
    preview_mode: Literal["rerun", "resume", "reset"] = "rerun"


class AnalyzeRequest(BaseModel):
    silence_threshold: float = Field(
        DEFAULT_SEGMENT_SILENCE_THRESHOLD,
        ge=MIN_SEGMENT_SILENCE_THRESHOLD,
        le=MAX_SEGMENT_SILENCE_THRESHOLD,
    )  # 停顿切分阈值（秒），前端可按需覆盖

@router.post("/api/tasks/{task_id}/asr")
async def trigger_asr(task_id: str, body: AsrRequest = None):
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    if not task.video_path or not os.path.exists(task.video_path):
        raise HTTPException(400, "Video file not uploaded")

    # 已在识别中：不替换后台 runner，避免重复触发导致状态抖动
    if task.status == TaskStatus.ASR_RUNNING:
        return {
            "success": True,
            "already_running": True,
            "message": "ASR 已在识别中",
        }
    if task.status in _BLOCKING_RUNNING_STATUSES:
        raise HTTPException(status_code=409, detail=f"当前状态 {task.status} 正在处理中，不能重新识别")
    if body and body.preview_token:
        try:
            validate_execution_preview_token(
                body.preview_token,
                task=task,
                operation="asr",
                mode=body.preview_mode,
                options=body.model_dump(
                    exclude={"preview_token", "preview_mode"},
                    exclude_unset=True,
                ),
            )
        except ExecutionPreviewError as exc:
            raise HTTPException(409, str(exc)) from exc

    whisper_model = (body.model if body else None) or "base"
    backend = (body.backend if body else None) or "whisper"
    task.params.enable_diarization = resolve_enable_diarization(
        task.task_type,
        request_override=body.enable_diarization if body else None,
    )

    # 立即持久化运行态，避免前端刷新/重进页面时仍读到 pending 并自动弹窗
    task.status = TaskStatus.ASR_RUNNING
    store.update(task)
    await manager.broadcast(task_id, {
        "type": "status_change",
        "task_id": task_id,
        "status": TaskStatus.ASR_RUNNING,
    })

    # Run ASR in background
    _start_task_runner(
        task_id,
        _run_asr_task(task_id, whisper_model, backend),
        TaskStatus.ASR_RUNNING,
        operation="asr",
    )
    label = f"FunASR Paraformer-zh" if backend == "funasr" else f"Whisper {whisper_model}"
    return {"success": True, "message": f"ASR 识别任务已启动（{label}）"}


async def _run_asr_task(task_id: str, whisper_model: str = "base", backend: str = "whisper"):
    store = get_store()
    task = store.get(task_id)
    if not task:
        return

    task.status = TaskStatus.ASR_RUNNING
    store.update(task)

    async def log(level, source, msg, progress=None):
        await log_to_ws(task_id, level, source, msg, progress)

    label = "FunASR Paraformer-zh" if backend == "funasr" else f"Whisper {whisper_model}"
    try:
        await log("info", "system", f"开始识别任务: {task.name}（{label}）")
        asr_result = await run_asr_recognize(
            task.video_path, task.params, log,
            whisper_model=whisper_model,
            backend=backend,
            clip_start=task.clip_start,
            clip_end=task.clip_end,
        )

        task.asr_result = asr_result
        task.audit_segments = []
        task.video_duration = asr_result.duration
        task.original_duration = asr_result.duration
        task.status = TaskStatus.ASR_RECOGNIZED

        # 记录本次识别快照到历史列表
        # 使用 model_copy(deep=True) 确保快照是独立副本，防止后续审计原地修改 Segment 对象时污染历史数据
        snapshot = ASRSnapshot(
            version=len(task.asr_history) + 1,
            created_at=datetime.now().isoformat(),
            engine=label,
            segments_count=0,
            duration=asr_result.duration,
            asr_result=asr_result.model_copy(deep=True),
        )
        task.asr_history.append(snapshot)
        task.asr_current_version = snapshot.version

        store.update(task)

        if task.params.enable_diarization:
            await log("success", "system", f"ASR 识别完成，共 {len(asr_result.words)} 个词，等待说话人分离")
            await manager.broadcast(task_id, {
                "type": "status_change",
                "task_id": task_id,
                "status": TaskStatus.ASR_RECOGNIZED,
                "segments_count": 0
            })
        else:
            await log("success", "system", f"ASR 识别完成，共 {len(asr_result.words)} 个词，自动进入文本整理")
            await _complete_text_organize(task_id, task, log)

    except asyncio.CancelledError:
        cancel_asr_processes(task.video_path)
        # 被新一轮 ASR 替换时不回滚状态，避免覆盖新任务刚写入的 asr_running
        if not _task_runner_superseded(task_id):
            current = store.get(task_id)
            fallback = _resolve_cancel_rollback_status(current or task) or TaskStatus.PENDING
            await _rollback_cancelled_task(task_id, fallback, "system", "ASR 识别")
        # 不重新抛出，让 asyncio 正常回收 Task

    except Exception as e:
        task.status = TaskStatus.ERROR
        task.error_message = str(e)
        store.update(task)
        await log("error", "system", f"ASR 失败: {str(e)}")
        await manager.broadcast(task_id, {"type": "status_change", "task_id": task_id, "status": TaskStatus.ERROR})


@router.post("/api/tasks/{task_id}/asr/analyze")
async def trigger_asr_analyze(task_id: str, body: AnalyzeRequest = None):
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    if not task.video_path or not os.path.exists(task.video_path):
        raise HTTPException(400, "Video file not uploaded")
    if not task.asr_result or not task.asr_result.words:
        raise HTTPException(400, "尚未完成 ASR 识别，无法文本整理")

    if task.status == TaskStatus.ANALYZE_RUNNING:
        return {
            "success": True,
            "already_running": True,
            "message": "文本整理已在运行中",
        }
    _ensure_status_allowed(
        task,
        {TaskStatus.ASR_RECOGNIZED, TaskStatus.ASR_DIARIZED, TaskStatus.ASR_DONE, TaskStatus.REVIEW, TaskStatus.DONE},
        "文本整理",
    )

    silence_threshold = (body.silence_threshold if body else None) or DEFAULT_SEGMENT_SILENCE_THRESHOLD
    task.params.silence_threshold = silence_threshold
    task.status = TaskStatus.ANALYZE_RUNNING
    store.update(task)
    await manager.broadcast(task_id, {
        "type": "status_change",
        "task_id": task_id,
        "status": TaskStatus.ANALYZE_RUNNING,
    })

    _start_task_runner(
        task_id,
        _run_asr_analyze_task(task_id),
        TaskStatus.ANALYZE_RUNNING,
        operation="asr_analyze",
    )
    return {"success": True, "message": f"文本整理已启动（停顿切分 {silence_threshold}s）"}


class DiarizationRequest(BaseModel):
    enabled: bool = True
    preview_token: Optional[str] = None
    preview_mode: Literal["rerun", "resume", "reset"] = "rerun"


@router.post("/api/tasks/{task_id}/asr/diarize")
async def trigger_asr_diarization(task_id: str, body: DiarizationRequest = None):
    store = get_store()
    task = store.get(task_id)
    if not task or not task.asr_result or not task.asr_result.words:
        raise HTTPException(400, "尚未完成 ASR 识别，无法进行说话人分离")
    if body and not body.enabled:
        task.params.enable_diarization = False
        task.status = TaskStatus.ANALYZE_RUNNING
        store.update(task)
        await manager.broadcast(task_id, {"type": "status_change", "task_id": task_id, "status": TaskStatus.ANALYZE_RUNNING})
        _start_task_runner(
            task_id,
            _run_asr_analyze_task(task_id),
            TaskStatus.ANALYZE_RUNNING,
            operation="asr_analyze",
        )
        return {"success": True, "message": "已跳过说话人分离，正在文本整理"}
    if task.status == TaskStatus.DIARIZATION_RUNNING:
        return {"success": True, "already_running": True, "message": "说话人分离已在运行中"}
    if body and body.preview_token:
        try:
            validate_execution_preview_token(
                body.preview_token,
                task=task,
                operation="diarize",
                mode=body.preview_mode,
                options=body.model_dump(
                    exclude={"preview_token", "preview_mode"},
                    exclude_unset=True,
                ),
            )
        except ExecutionPreviewError as exc:
            raise HTTPException(409, str(exc)) from exc
    _ensure_status_allowed(
        task,
        {TaskStatus.ASR_RECOGNIZED, TaskStatus.ASR_DIARIZED, TaskStatus.ASR_DONE, TaskStatus.REVIEW, TaskStatus.DONE},
        "说话人分离",
    )
    task.status = TaskStatus.DIARIZATION_RUNNING
    store.update(task)
    await manager.broadcast(task_id, {"type": "status_change", "task_id": task_id, "status": TaskStatus.DIARIZATION_RUNNING})
    _start_task_runner(
        task_id,
        _run_asr_diarization_task(task_id),
        TaskStatus.DIARIZATION_RUNNING,
        operation="asr_diarization",
    )
    return {"success": True, "message": "说话人分离已启动"}


async def _run_asr_diarization_task(task_id: str):
    store = get_store()
    task = store.get(task_id)
    if not task or not task.asr_result:
        return
    async def log(level, source, msg, progress=None):
        await log_to_ws(task_id, level, source, msg, progress)
    try:
        oracle_num = 2 if task.task_type.value == "interview_compress" else None
        base_words = strip_analysis_tags_from_words(task.asr_result.words)
        result = await run_asr_diarize(
            task.video_path, base_words, log, oracle_num=oracle_num,
            clip_start=task.clip_start, duration=task.asr_result.duration,
        )
        task.asr_result = result
        if result.speakers or result.diar_segments:
            task.params.enable_diarization = True
            task.status = TaskStatus.ASR_DIARIZED
        else:
            task.params.enable_diarization = False
            task.status = TaskStatus.ASR_RECOGNIZED
            _clear_downstream_results(task)
            if task.asr_result.analysis_report is None:
                task.asr_result.analysis_report = {}
            existing_diarization = task.asr_result.analysis_report.get("diarization") or {}
            failure = existing_diarization.get("failure")
            task.asr_result.analysis_report["diarization"] = {
                "status": "degraded" if failure else "completed_no_speakers",
                "completed_at": datetime.now().isoformat(),
                "engine": "CAM++",
                "message": (
                    failure.get("reason")
                    if failure else "未检测到可用说话人段，可重试或直接继续分析"
                ),
                **({"failure": failure} if failure else {}),
            }
            await log(
                "warn",
                "diarization",
                (
                    f"说话人分离降级：{failure.get('reason')}"
                    if failure else "未检测到可用说话人段，已回到待分析状态"
                ),
            )
        if task.status == TaskStatus.ASR_DIARIZED:
            if task.asr_result.analysis_report is None:
                task.asr_result.analysis_report = {}
            task.asr_result.analysis_report["diarization"] = {
                "status": "completed",
                "completed_at": datetime.now().isoformat(),
                "engine": "CAM++",
                "speakers": len(task.asr_result.speakers),
                "segments": len(task.asr_result.diar_segments),
            }
            await _complete_text_organize(task_id, task, log)
            return
        store.update(task)
        await manager.broadcast(task_id, {"type": "status_change", "task_id": task_id, "status": task.status})
    except asyncio.CancelledError:
        cancel_asr_processes(task.video_path)
        if not _task_runner_superseded(task_id):
            current = store.get(task_id)
            await _rollback_cancelled_task(task_id, _recognized_fallback_status(current or task), "system", "说话人分离")
    except Exception as exc:
        task.status = TaskStatus.ERROR
        task.error_message = str(exc)
        store.update(task)
        await log("error", "diarization", f"说话人分离失败: {exc}")
        await manager.broadcast(task_id, {"type": "status_change", "task_id": task_id, "status": TaskStatus.ERROR})


def _apply_clip_exclusion(task):
    if task.clip_start is None and task.clip_end is None:
        return
    cs = task.clip_start or 0.0
    ce = task.clip_end or float("inf")
    for seg in task.audit_segments:
        if seg.end <= cs or seg.start >= ce:
            seg.action = SegmentAction.DELETE
            seg.reason = "区间外自动排除"


def _update_current_asr_snapshot(task, asr_result):
    if task.asr_current_version:
        for snapshot in task.asr_history:
            if snapshot.version == task.asr_current_version:
                snapshot.segments_count = len(asr_result.segments)
                snapshot.duration = asr_result.duration
                snapshot.asr_result = asr_result.model_copy(deep=True)
                return

    snapshot = ASRSnapshot(
        version=len(task.asr_history) + 1,
        created_at=datetime.now().isoformat(),
        engine="文本整理",
        segments_count=len(asr_result.segments),
        duration=asr_result.duration,
        asr_result=asr_result.model_copy(deep=True),
    )
    task.asr_history.append(snapshot)
    task.asr_current_version = snapshot.version


def _candidate_edits_count(segments: list) -> int:
    return sum(len(getattr(segment, "candidate_edits", []) or []) for segment in segments)


def _analysis_stats_from_report(asr_result) -> dict:
    report = asr_result.analysis_report or {}
    stats = report.get("stats")
    return dict(stats) if isinstance(stats, dict) else {}


def _append_analysis_snapshot(task, *, action: str, segments: list, report: Optional[dict] = None, stats: Optional[dict] = None):
    snapshot = AnalysisSnapshot(
        version=len(task.analysis_history) + 1,
        created_at=datetime.now().isoformat(),
        action=action,
        silence_threshold=task.params.silence_threshold,
        segments_count=len(segments),
        candidate_edits_count=_candidate_edits_count(segments),
        stats=stats or {},
        analysis_report=report,
        segments=[segment.model_copy(deep=True) for segment in segments],
    )
    task.analysis_history.append(snapshot)
    task.analysis_current_version = snapshot.version
    return snapshot


def _build_resegment_analysis_report(task, threshold: float, segments: list, stats: dict, recommendation: Optional[dict]) -> dict:
    existing = task.asr_result.analysis_report or {}
    candidate_edits_count = _candidate_edits_count(segments)
    enable_diarization = bool(task.params.enable_diarization and task.asr_result.diar_segments)
    return {
        "generated_at": datetime.now().isoformat(),
        "source": "resegment",
        "settings": {
            "silence_threshold": threshold,
            "enable_diarization": enable_diarization,
            "diar_smooth_threshold": task.asr_result.diar_smooth_threshold,
            "max_segment_duration": MAX_SEGMENT_DURATION,
            "short_segment_candidate_threshold": 0.3,
        },
        "stats": {
            **stats,
            "segments_final": len(segments),
            "candidate_edits": candidate_edits_count,
            "candidate_edits_by_source": {
                source: sum(
                    1
                    for segment in segments
                    for edit in (segment.candidate_edits or [])
                    if edit.source == source
                )
                for source in sorted({
                    edit.source
                    for segment in segments
                    for edit in (segment.candidate_edits or [])
                })
            },
            "recommended_threshold": recommendation.get("threshold") if recommendation else None,
        },
        "steps": [
            *[
                step for step in existing.get("steps", [])
                if step.get("key") in {"glossary", "stutter", "filler", "tagged_script"}
            ],
            {
                "key": "segmentation",
                "title": "字幕分段调整",
                "description": "复用已识别和已标注的字词，按新的停顿切分阈值重新生成字幕卡。",
                "enabled": True,
                "input_count": len(task.asr_result.words),
                "output_count": len(segments),
                "changed_count": 0,
                "note": f"保存为 {threshold:.2f}s，{recommendation.get('reason') if recommendation else '未生成推荐说明'}",
            },
        ],
        "rule_events": existing.get("rule_events", []),
        "segment_notes": [
            _build_segment_note(segment, silence_threshold=threshold, enable_diarization=enable_diarization)
            for segment in segments
        ],
    }


async def _complete_text_organize(task_id: str, task, log):
    """Run the mandatory ASR post-processing step and persist its outputs."""
    store = get_store()
    if not task.asr_result or not task.asr_result.words:
        task.status = TaskStatus.ERROR
        task.error_message = "尚未完成 ASR 识别，无法文本整理"
        store.update(task)
        return

    task.status = TaskStatus.ANALYZE_RUNNING
    store.update(task)
    await manager.broadcast(task_id, {
        "type": "status_change",
        "task_id": task_id,
        "status": TaskStatus.ANALYZE_RUNNING,
    })

    await log("info", "system", f"开始文本整理: {task.name}")
    base_words = strip_analysis_tags_from_words(task.asr_result.words)
    asr_result = await run_asr_analyze(
        task.video_path,
        base_words,
        task.params,
        log,
        diar_segments=task.asr_result.diar_segments,
        diar_smooth_threshold=task.asr_result.diar_smooth_threshold,
        duration=task.asr_result.duration,
    )

    task.asr_result = asr_result
    _clear_downstream_results(task)
    task.audit_segments = asr_result.segments
    _apply_clip_exclusion(task)
    task.video_duration = asr_result.duration
    task.original_duration = asr_result.duration
    task.status = TaskStatus.ASR_DONE
    _append_analysis_snapshot(
        task,
        action="text_organize",
        segments=asr_result.segments,
        report=asr_result.analysis_report,
        stats=_analysis_stats_from_report(asr_result),
    )
    store.update(task)

    await log("success", "system", f"文本整理完成，共 {len(asr_result.segments)} 个片段")
    await manager.broadcast(task_id, {
        "type": "status_change",
        "task_id": task_id,
        "status": TaskStatus.ASR_DONE,
        "segments_count": len(asr_result.segments)
    })


async def _run_asr_analyze_task(task_id: str):
    store = get_store()
    task = store.get(task_id)
    if not task:
        return

    async def log(level, source, msg, progress=None):
        await log_to_ws(task_id, level, source, msg, progress)

    try:
        await _complete_text_organize(task_id, task, log)

    except asyncio.CancelledError:
        if not _task_runner_superseded(task_id):
            current = store.get(task_id)
            await _rollback_cancelled_task(task_id, _recognized_fallback_status(current or task), "system", "文本整理")

    except Exception as e:
        task.status = TaskStatus.ERROR
        task.error_message = str(e)
        store.update(task)
        await log("error", "system", f"文本整理失败: {str(e)}")
        await manager.broadcast(task_id, {"type": "status_change", "task_id": task_id, "status": TaskStatus.ERROR})


class AsrSwitchRequest(BaseModel):
    version: int  # 要切换到的历史版本号（从 1 开始）


@router.post("/api/tasks/{task_id}/asr/switch")
async def switch_asr_version(task_id: str, body: AsrSwitchRequest):
    """切换到指定历史 ASR 版本，更新当前 asr_result 和 audit_segments。"""
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    if not task.asr_history:
        raise HTTPException(400, "无 ASR 历史记录")

    # 找到目标版本快照
    target = next((s for s in task.asr_history if s.version == body.version), None)
    if not target:
        raise HTTPException(404, f"未找到版本 v{body.version}")

    # 使用 model_copy(deep=True) 确保独立副本，避免审计时原地修改 Segment 对象污染历史快照
    task.asr_result = target.asr_result.model_copy(deep=True)
    if not task.asr_result.segments:
        task.audit_segments = []
        task.status = TaskStatus.ASR_DIARIZED if task.asr_result.diar_segments else TaskStatus.ASR_RECOGNIZED
        task.asr_current_version = target.version
        task.updated_at = datetime.now().isoformat()
        store.update(task)
        return {"success": True, "message": f"已切换到 v{body.version}（{target.engine}，仅识别）", "task": task}

    # 恢复并重置审计决策为初始状态（原始 ASR 分段全部为 keep，无任何审计痕迹）
    # 兼容已存在的已损坏历史数据：即使快照中 segment.action 已被旧版逻辑污染，也在此处强制归零
    restored_segments = [seg.model_copy(deep=True) for seg in target.asr_result.segments]
    for seg in restored_segments:
        seg.action = SegmentAction.KEEP
        seg.reason = None
        seg.rule = None
        seg.claude_action = None
        seg.claude_reason = None
        seg.user_override = False
        seg.manual_action = None
        seg.display_text = None
        seg.fix_type = None
        seg.split_before = None
        seg.subtitle_line_break = None
        seg.merged = False
    task.audit_segments = restored_segments
    task.asr_current_version = target.version
    task.status = TaskStatus.ASR_DONE
    task.updated_at = datetime.now().isoformat()
    store.update(task)

    return {"success": True, "message": f"已切换到 v{body.version}（{target.engine}）", "task": task}


class ResmoothdRequest(BaseModel):
    threshold: float  # 平滑阈值（秒），推荐范围 0.5 ~ 5.0


def _segment_words_with_postprocess(
    words: list,
    silence_threshold: float,
    *,
    enable_diarization: bool = False,
) -> list:
    segments = segment_by_silence(
        words,
        silence_threshold=silence_threshold,
        min_duration=0.3,
        max_duration=MAX_SEGMENT_DURATION,
    )
    return postprocess_segments(segments, enable_diarization=enable_diarization)


@router.post("/api/tasks/{task_id}/asr/resmooth")
async def resmooth_speaker_labels(task_id: str, body: ResmoothdRequest):
    """
    用新的平滑阈值重新标注词级说话人。

    不重跑 ASR 或 CAM++，直接从 task.asr_result.diar_segments
    重新对齐 + 平滑，更新 words / segments / audit_segments 中的 speaker 字段。
    响应时间通常 < 1 秒。
    """
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    if not task.asr_result:
        raise HTTPException(400, "尚未完成 ASR 识别")
    if not task.asr_result.segments:
        raise HTTPException(400, "请先完成文本整理，再重新标注说话人")
    if not task.asr_result.diar_segments:
        raise HTTPException(400, "当前 ASR 结果不含说话人分离数据，请重新识别并开启说话人分离")

    threshold = max(0.1, min(10.0, body.threshold))  # 限制在合理范围内

    # 用新阈值重算词级 speaker
    new_words = resmooth_speakers(
        task.asr_result.words,
        task.asr_result.diar_segments,
        min_switch_duration=threshold,
    )

    # 重新切段并走统一后处理，保持与正式 ASR / 动态重分段一致
    new_segments_raw = _segment_words_with_postprocess(
        new_words,
        task.params.silence_threshold,
        enable_diarization=task.params.enable_diarization,
    )

    # 将新的 speaker 信息合并回 audit_segments：
    # 只更新 speaker 字段，保留用户已有的 action/reason 等审计结果。
    # 同时更新 words 中的 speaker（按 start 时间精确匹配）
    word_speaker_map: dict = {round(w.start, 3): w.speaker for w in new_words}

    for seg in task.audit_segments:
        # 更新 segment.words 中每个词的 speaker
        if seg.words:
            seg.words = [
                seg.words[i].model_copy(update={"speaker": word_speaker_map.get(round(seg.words[i].start, 3), seg.words[i].speaker)})
                for i in range(len(seg.words))
            ]
            # 从更新后的 words 重新推算段级 speaker（多数投票）
            speakers_in_seg = [w.speaker for w in seg.words if w.speaker]
            if speakers_in_seg:
                seg.speaker = max(set(speakers_in_seg), key=speakers_in_seg.count)

    # 更新 asr_result 中的 words 和平滑阈值记录
    task.asr_result.words = new_words
    task.asr_result.segments = new_segments_raw
    task.asr_result.diar_smooth_threshold = threshold

    task.updated_at = datetime.now().isoformat()
    store.update(task)

    smoothed_count = sum(
        1 for orig, new in zip(task.asr_result.words, new_words)
        if orig.speaker != new.speaker
    )

    return {
        "success": True,
        "message": f"已用阈值 {threshold}s 重新标注，{len(new_words)} 个词中调整了 {smoothed_count} 个说话人归属",
        "threshold": threshold,
        "task": task,
    }


# ============================================================
# 动态重分段（不重跑 ASR）
# ============================================================

class ResegmentRequest(BaseModel):
    silence_threshold: float = Field(
        ...,
        ge=MIN_SEGMENT_SILENCE_THRESHOLD,
        le=MAX_SEGMENT_SILENCE_THRESHOLD,
    )                         # 0.05 ~ 0.50
    save: bool = False        # False=预览，True=保存
    inherit_mode: Literal["none", "aggressive"] = "none"
    expected_content_revision: Optional[int] = None
    include_segments: bool = False


def _invalidated_export_count(task) -> int:
    paths = {
        path
        for path in (
            task.export_path,
            task.jianying_draft_path,
            *[reel.export_path for reel in (task.highlight_reels or [])],
            *[reel.export_path for reel in (task.highlight_reels_before_quality or [])],
        )
        if path
    }
    return len(paths)


def _calc_segment_stats(segments: list, max_chars: int = 30) -> dict:
    """计算分段统计指标。"""
    if not segments:
        return {"total": 0, "avg_chars": 0, "over_limit_count": 0, "over_limit_pct": 0, "max_chars": 0}
    char_counts = [len(s.text) for s in segments]
    over = [c for c in char_counts if c > max_chars]
    return {
        "total": len(segments),
        "avg_chars": round(sum(char_counts) / len(char_counts), 1),
        "over_limit_count": len(over),
        "over_limit_pct": round(len(over) / len(char_counts) * 100, 1),
        "max_chars": max(char_counts),
    }


def _find_best_threshold(
    words: list,
    candidates: list[float],
    max_chars: int = 30,
    *,
    enable_diarization: bool = False,
    task_type: str = "monologue_clean",
) -> dict:
    """遍历候选阈值，按任务类型找到较稳的字幕分段值。"""
    task_type_value = getattr(task_type, "value", task_type)
    if task_type_value == "interview_compress":
        max_chars = 42
        ideal_min, ideal_max = 40, 180
        task_hint = "访谈更重视语义完整和说话人边界"
    elif task_type_value == "highlight_reel":
        max_chars = 32
        ideal_min, ideal_max = 80, 240
        task_hint = "集锦需要节奏清晰且避免超长字幕"
    else:
        max_chars = 30
        ideal_min, ideal_max = 70, 220
        task_hint = "口播精修偏短句和节奏感"

    best = None
    for thr in candidates:
        segs = _segment_words_with_postprocess(
            words,
            thr,
            enable_diarization=enable_diarization,
        )
        stats = _calc_segment_stats(segs, max_chars)
        too_many_penalty = max(0, stats["total"] - ideal_max) * 0.08
        too_few_penalty = max(0, ideal_min - stats["total"]) * 0.04
        # 先压低超长字幕，再避免极端碎片化或过度合并。
        score = stats["over_limit_pct"] + too_many_penalty + too_few_penalty + max(0, stats["avg_chars"] - max_chars) * 0.2
        if best is None or score < best["score"]:
            best = {"threshold": thr, "stats": stats, "score": score}
    if best:
        over = best["stats"]["over_limit_count"]
        pct = best["stats"]["over_limit_pct"]
        best["reason"] = f"{task_hint}，超{max_chars}字段 {over} 个/{pct}%"
    return best


def _compute_resegment_base(
    words: list,
    threshold: float,
    *,
    enable_diarization: bool,
    task_type,
) -> tuple[list, dict, Optional[dict]]:
    """CPU 密集的切分与阈值推荐；由路由放入工作线程，避免阻塞事件循环。"""
    segments = _segment_words_with_postprocess(
        words,
        threshold,
        enable_diarization=enable_diarization,
    )
    stats = _calc_segment_stats(segments)
    recommendation = _find_best_threshold(
        words,
        [0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50],
        enable_diarization=enable_diarization,
        task_type=task_type,
    )
    if recommendation:
        recommendation.pop("score", None)
    return segments, stats, recommendation


@router.post("/api/tasks/{task_id}/asr/resegment")
async def resegment_segments(task_id: str, body: ResegmentRequest):
    """
    用新的停顿切分阈值重新切段，不重跑 ASR。

    save=false：预览模式，返回新段列表和统计，不修改 task。
    save=true：保存模式，更新 audit_segments 并回退审计状态。
    同时返回智能推荐阈值。
    """
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    if not task.asr_result or not task.asr_result.words:
        raise HTTPException(400, "尚未完成 ASR 识别，无法重新分段")
    if not task.asr_result.segments:
        raise HTTPException(400, "请先完成文本整理，再重新分段")
    if (
        body.save
        and body.expected_content_revision is not None
        and body.expected_content_revision != task.content_revision
    ):
        raise HTTPException(
            status_code=409,
            detail="内容已在预览后发生变化，请刷新分段预览后再应用",
        )

    threshold = body.silence_threshold
    words = task.asr_result.words
    old_audit_segments = list(task.audit_segments or [])
    had_audit_results = bool(
        task.audit_history
        or task.audit_current_version
        or task.highlight_reels
        or any(
            segment.action != SegmentAction.KEEP
            or segment.claude_action is not None
            or segment.user_override
            or segment.product_semantic is not None
            for segment in old_audit_segments
        )
    )
    invalidated_export_count = _invalidated_export_count(task)

    # 切分、推荐阈值和结果继承都是 CPU 密集计算，不能占住 FastAPI 事件循环。
    new_segments, stats, recommendation = await asyncio.to_thread(
        _compute_resegment_base,
        words,
        threshold,
        enable_diarization=task.params.enable_diarization,
        task_type=task.task_type,
    )

    inheritance = None
    inheritance_report = ResegmentInheritanceReport(
        mode="none",
        old_segment_count=len(old_audit_segments),
        new_segment_count=len(new_segments),
        unmapped_segment_count=len(new_segments),
        unmapped_segment_ids=[segment.id for segment in new_segments],
        invalidated_export_count=invalidated_export_count,
        warnings=["未启用结果继承；保存后需重新审计和生成集锦"] if had_audit_results else [],
    )
    if body.inherit_mode == "aggressive":
        inheritance = await asyncio.to_thread(
            inherit_resegment_results,
            old_audit_segments,
            new_segments,
            occurrences=list(task.product_occurrences or []),
            reels=list(task.highlight_reels or []),
            reels_before_quality=list(task.highlight_reels_before_quality or []),
            golden_quote_order=list(task.golden_quote_order or []),
            video_duration=task.video_duration or task.asr_result.duration,
            target_content_revision=(task.content_revision or 0) + 1,
            invalidated_export_count=invalidated_export_count,
        )
        inheritance_report = inheritance.report

    result: dict = {
        "success": True,
        "stats": stats,
        "recommendation": recommendation,
        "inheritance": inheritance_report.model_dump(),
    }
    if body.include_segments:
        result["segments"] = [
            segment.model_dump()
            for segment in (inheritance.segments if inheritance else new_segments)
        ]

    if body.save:
        # ASR 原始分段始终保持无审计状态；继承结果写入 audit_segments。
        task.asr_result.segments = new_segments
        task.params.silence_threshold = threshold
        report = _build_resegment_analysis_report(task, threshold, new_segments, stats, recommendation)
        task.asr_result.analysis_report = report

        if body.inherit_mode == "aggressive" and inheritance is not None:
            task.content_revision = (task.content_revision or 0) + 1
            task.audit_segments = inheritance.segments
            task.audit_json_path = None
            task.product_occurrences = inheritance.occurrences
            task.product_groups = inheritance.product_groups
            task.highlight_reels = inheritance.reels
            task.highlight_reels_before_quality = inheritance.reels_before_quality
            task.highlight_quality_report = inheritance.quality_report
            task.highlight_quality_report_before_quality = inheritance.quality_report_before
            task.highlight_strategy_quality_results = []
            task.audit_coherence_report = None
            task.golden_quote_order = inheritance.golden_quote_order
            task.export_path = None
            task.jianying_draft_path = None
            task.latest_export_job_id = None
            task.last_subtitle_optimization = None
            if task.highlight_reels or task.highlight_reels_before_quality:
                task.highlight_revision = (task.highlight_revision or 0) + 1
            task.resegment_inheritance_report = inheritance.report
        else:
            _clear_downstream_results(task)
            task.audit_segments = new_segments
            task.resegment_inheritance_report = inheritance_report

        _append_analysis_snapshot(
            task,
            action="resegment",
            segments=new_segments,
            report=report,
            stats=report.get("stats", stats),
        )

        # 重算统计（segments_kept / segments_deleted / edited_duration）
        task.update_stats()

        if (
            body.inherit_mode == "aggressive"
            and had_audit_results
            and inheritance_report.mapped_segment_count > 0
        ):
            task.status = TaskStatus.REVIEW
        elif task.status in (
            TaskStatus.REVIEW,
            TaskStatus.AUDIT_RUNNING,
            TaskStatus.EXPORT_RUNNING,
            TaskStatus.DONE,
        ):
            task.status = TaskStatus.ASR_DONE

        task.updated_at = datetime.now().isoformat()
        store.update(task)
        if body.inherit_mode == "aggressive":
            result["message"] = (
                f"已用阈值 {threshold}s 重新分段，共 {len(new_segments)} 段；"
                f"继承 {inheritance_report.inherited_audit_count} 段审计、"
                f"{inheritance_report.inherited_reel_count} 个集锦"
            )
        else:
            result["message"] = f"已用阈值 {threshold}s 重新分段，共 {len(new_segments)} 段"
    else:
        result["message"] = f"预览：阈值 {threshold}s 将产生 {len(new_segments)} 段"

    return result
