"""一键转英文/目标语言配音：上传、启动流水线、单视频重试、字幕编辑、最终导出、预览。"""

import os
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import FileResponse

from ..models.task import (
    Task, TaskStatus, TaskType, ASRSnapshot,
    OneClickTranslateStartRequest, UpdateTranslationCuesRequest,
    OneClickTranslateConfig,
)
from ..services.task_store import get_store
from ..services.asr_pipeline import (
    run_asr_pipeline, get_video_duration, generate_thumbnail, cancel_asr_processes,
)
from ..services.one_click_translate import (
    build_translation_cues,
    calibrate_and_translate_cues,
    compose_aligned_audio_track,
    determine_resume_stage,
    generate_tts_audio_for_cues,
    mux_video_with_audio,
    resolve_video_duration,
    validate_stage_ready,
    validate_cue_timeline,
    write_cues_srt,
)
from ..core.paths import EXPORTS_DIR, THUMBNAILS_DIR, TRANSLATION_UPLOADS_DIR
from ..core.uploads import _save_upload_file
from ..core.ws import manager, log_to_ws
from ..core.task_runner import (
    _export_tasks, _start_task_runner, _task_runner_superseded, _rollback_cancelled_task,
)
from ..services.app_config_store import (
    resolve_feature_api_key,
    resolve_feature_llm,
    resolve_role_candidates,
)
from ..services.model_usage import model_usage_task_context
from ..services.execution_preview import (
    ExecutionPreviewError,
    validate_execution_preview_token,
)
from ..agent_runtime.contracts import (
    format_contract_issues,
    validate_translation_cues_contract,
)
from ..agent_runtime.approvals import ApprovalRequired, assert_human_approval
from ..agent_runtime.harness import ContractVerificationError, verify_contract_report
from ..agent_runtime.hooks import emit_agent_hook, emit_tool_hook

router = APIRouter()


def _require_export_approval(approval_token: Optional[str]) -> None:
    try:
        assert_human_approval("ffmpeg_export", approval_token)
    except ApprovalRequired as exc:
        raise HTTPException(400, str(exc)) from exc


# ============================================================
# One-click target-language dubbing uploads
# ============================================================

def _require_translate_task(task: Task):
    if task.task_type != TaskType.ONE_CLICK_TRANSLATE:
        raise HTTPException(400, "Task is not a one-click translation task")


def _translation_output_dir(task_id: str, video_id: str) -> Path:
    return EXPORTS_DIR / "one_click_translate" / task_id / video_id


def _find_translation_video(task: Task, video_id: str):
    for video in task.translation_videos:
        if video.id == video_id:
            return video
    return None


def _validate_one_click_translate_start_request(req: OneClickTranslateStartRequest):
    if not (req.subtitle_model.provider or "").strip():
        candidates = resolve_role_candidates("translate.subtitle", include_escalation=False)
        if candidates:
            candidate = candidates[0]
            req.subtitle_model.provider = "ollama" if candidate.provider == "ollama" else "openai"
            req.subtitle_model.model = candidate.model
            req.subtitle_model.base_url = candidate.base_url
            if not req.subtitle_api_key and candidate.provider != "ollama":
                req.subtitle_api_key = candidate.api_key
        else:
            fallback = resolve_feature_llm("translate")
            req.subtitle_model.provider = "ollama" if fallback.provider == "ollama" else "openai"
            req.subtitle_model.model = fallback.model
            req.subtitle_model.base_url = fallback.base_url
            if not req.subtitle_api_key and fallback.provider != "ollama":
                req.subtitle_api_key = fallback.api_key
    if not (req.tts_model.provider or "").strip():
        tts = resolve_feature_llm("tts")
        req.tts_model.provider = "silent" if tts.provider == "silent" else "openai"
        req.tts_model.model = tts.model
        req.tts_model.base_url = tts.base_url
        if not req.tts_api_key and tts.provider != "silent":
            req.tts_api_key = tts.api_key

    subtitle_provider = (req.subtitle_model.provider or "").lower()
    if subtitle_provider in {"openai", "openrouter", "anthropic"}:
        key = resolve_feature_api_key("translate", req.subtitle_api_key)
        if not key:
            raise HTTPException(400, "缺少 OpenAI API Key，无法进行字幕校准和翻译")

    tts_provider = (req.tts_model.provider or "").lower()
    if tts_provider == "openai":
        key = resolve_feature_api_key("tts", req.tts_api_key)
        if not key:
            raise HTTPException(400, "缺少 OPENAI_API_KEY，无法生成目标语言语音。测试流程可选择「对齐静音占位」。")


@router.post("/api/tasks/{task_id}/one-click-translate/videos")
async def upload_translation_video(task_id: str, file: UploadFile = File(...)):
    """
    Add one source video to a one-click translation task.
    The frontend can call this repeatedly to upload a batch.
    """
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    _require_translate_task(task)

    ext = Path(file.filename).suffix.lower()
    if ext not in [".mp4", ".mov", ".avi", ".mkv", ".webm"]:
        raise HTTPException(400, f"Unsupported format: {ext}")

    from ..models.task import TranslationVideo

    video = TranslationVideo(filename=file.filename, video_path="")
    upload_dir = TRANSLATION_UPLOADS_DIR / task_id
    upload_dir.mkdir(parents=True, exist_ok=True)
    video_path = upload_dir / f"{video.id}{ext}"
    await _save_upload_file(file, video_path)

    loop = asyncio.get_event_loop()
    duration = await loop.run_in_executor(None, get_video_duration, str(video_path))
    thumb_path = THUMBNAILS_DIR / f"{task_id}_{video.id}.jpg"
    await loop.run_in_executor(None, generate_thumbnail, str(video_path), str(thumb_path))

    video.video_path = str(video_path)
    video.duration = duration
    video.thumbnail_path = str(thumb_path) if thumb_path.exists() else None
    task.translation_videos.append(video)

    # Keep the first video visible in existing dashboard/review surfaces.
    if not task.video_path:
        task.video_path = str(video_path)
        task.video_filename = file.filename
        task.video_duration = duration
        task.original_duration = duration
        task.thumbnail_path = video.thumbnail_path
    task.status = TaskStatus.PENDING
    store.update(task)

    return {"success": True, "video": video.model_dump()}


@router.post("/api/tasks/{task_id}/one-click-translate/start")
async def start_one_click_translate(task_id: str, req: OneClickTranslateStartRequest):
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    _require_translate_task(task)
    if not task.translation_videos:
        raise HTTPException(400, "请先上传至少 1 个视频")
    _validate_one_click_translate_start_request(req)

    task.one_click_translate_config = OneClickTranslateConfig(
        source_language=req.source_language,
        target_language=req.target_language,
        subtitle_model=req.subtitle_model,
        tts_model=req.tts_model,
    )
    task.error_message = None
    store.update(task)

    _start_task_runner(
        task_id,
        _run_one_click_translate_task(task_id, req),
        TaskStatus.TRANSLATE_RUNNING,
        operation="one_click_translate",
    )
    return {"success": True, "message": "一键转英文流水线已启动"}


# 阶段名 → 执行顺序索引，供断点续跑跳过已完成阶段
_TRANSLATION_STAGE_INDEX = {"asr": 0, "translate": 1, "tts": 2, "dub": 3}


def _translation_task_has_success(task: Task) -> bool:
    """任务下是否至少有一个视频成功出预览（或已完成导出）。"""
    return any(v.status in ("ready_preview", "done") for v in task.translation_videos)


def _rollback_translation_video_status(video) -> None:
    if video.status not in {
        "asr_running", "translate_running", "tts_running", "dubbing_running",
    }:
        return
    stage = determine_resume_stage(video)
    video.status = {
        "asr": "uploaded",
        "translate": "asr_done",
        "tts": "translated",
        "dub": "audio_done",
    }[stage]
    video.error_message = None


async def _process_translation_video(
    task: Task,
    video,
    req: OneClickTranslateStartRequest,
    config: OneClickTranslateConfig,
    log,
    start_stage: str = "asr",
    set_task_asr: bool = False,
):
    """
    处理单个 TranslationVideo 的 4 阶段：ASR → 校准+翻译 → TTS → 对齐混流出预览。

    主流水线和单视频重跑接口共用。start_stage 之前的阶段会被跳过（断点续跑），
    每个阶段的状态写盘和 WebSocket 广播行为与原主流水线一致。
    """
    store = get_store()
    task_id = task.id
    start_idx = _TRANSLATION_STAGE_INDEX[start_stage]

    out_dir = _translation_output_dir(task_id, video.id)
    out_dir.mkdir(parents=True, exist_ok=True)
    emit_agent_hook(
        task_id,
        "before_agent_run",
        "orchestrate.clip_pipeline",
        payload={"stage": "one_click_translate", "video_id": video.id, "start_stage": start_stage},
    )

    # 1. ASR: produces source subtitles and cue IDs.
    if start_idx <= _TRANSLATION_STAGE_INDEX["asr"]:
        task.status = TaskStatus.ASR_RUNNING
        video.status = "asr_running"
        store.update(task)
        await manager.broadcast(task_id, {"type": "status_change", "task_id": task_id, "status": task.status})

        # 一键翻译只需字幕时间轴，不需要说话人分离
        asr_params = task.params.model_copy(update={"enable_diarization": False})
        emit_tool_hook(
            task_id,
            "before_tool_call",
            "orchestrate.clip_pipeline",
            "run_asr",
            payload={"video_id": video.id, "backend": req.asr_backend, "model": req.asr_model},
        )
        asr_result = await run_asr_pipeline(
            video.video_path,
            asr_params,
            log,
            whisper_model=req.asr_model,
            backend=req.asr_backend,
            oracle_num=None,
        )
        emit_tool_hook(
            task_id,
            "after_tool_call",
            "orchestrate.clip_pipeline",
            "run_asr",
            payload={"video_id": video.id, "segments_count": len(asr_result.segments)},
        )
        video.asr_result = asr_result
        video.duration = asr_result.duration or video.duration
        video.cues = build_translation_cues(asr_result)
        video.status = "asr_done"

        video.source_srt_path = write_cues_srt(video.cues, str(out_dir / "source.srt"), "source_text")
        validate_stage_ready(video, "asr")

        snapshot = ASRSnapshot(
            version=len(task.asr_history) + 1,
            created_at=datetime.now().isoformat(),
            engine=f"{req.asr_backend}:{req.asr_model}",
            segments_count=len(asr_result.segments),
            duration=asr_result.duration,
            asr_result=asr_result.model_copy(deep=True),
        )
        task.asr_history.append(snapshot)
        task.asr_current_version = snapshot.version
        if set_task_asr:
            task.asr_result = asr_result
            task.audit_segments = asr_result.segments

    # 2. Subtitle calibration + translation.
    if start_idx <= _TRANSLATION_STAGE_INDEX["translate"]:
        task.status = TaskStatus.TRANSLATE_RUNNING
        video.status = "translate_running"
        store.update(task)
        await manager.broadcast(task_id, {"type": "status_change", "task_id": task_id, "status": task.status})

        original_cues = [cue.model_copy(deep=True) for cue in video.cues]
        emit_agent_hook(
            task_id,
            "before_model_call",
            "translate.cues",
            payload={
                "video_id": video.id,
                "cues_count": len(video.cues),
                "provider": config.subtitle_model.provider,
                "model": config.subtitle_model.model,
            },
        )
        video.cues = await calibrate_and_translate_cues(
            video.cues,
            config,
            api_key=resolve_feature_api_key("translate", req.subtitle_api_key),
            log_callback=log,
            checkpoint_dir=str(out_dir / "checkpoints" / "translate"),
        )
        emit_agent_hook(
            task_id,
            "after_model_output",
            "translate.cues",
            payload={"video_id": video.id, "cues_count": len(video.cues)},
        )
        report = validate_translation_cues_contract(original_cues, video.cues)
        await verify_contract_report(task_id, "translation_cues", report, log_callback=log)
        emit_tool_hook(
            task_id,
            "before_tool_call",
            "orchestrate.clip_pipeline",
            "write_translation_cues",
            payload={"video_id": video.id, "cues_count": len(video.cues)},
        )
        video.calibrated_srt_path = write_cues_srt(video.cues, str(out_dir / "calibrated.srt"), "calibrated_text")
        video.translated_srt_path = write_cues_srt(video.cues, str(out_dir / "translated.srt"), "translated_text")
        video.status = "translated"
        validate_stage_ready(video, "translated")
        emit_tool_hook(
            task_id,
            "after_tool_call",
            "orchestrate.clip_pipeline",
            "write_translation_cues",
            payload={"video_id": video.id, "status": video.status},
        )

    # 3. TTS.
    if start_idx <= _TRANSLATION_STAGE_INDEX["tts"]:
        task.status = TaskStatus.TTS_RUNNING
        video.status = "tts_running"
        store.update(task)
        await manager.broadcast(task_id, {"type": "status_change", "task_id": task_id, "status": task.status})

        audio_dir = out_dir / "audio"
        emit_tool_hook(
            task_id,
            "before_tool_call",
            "orchestrate.clip_pipeline",
            "generate_tts_audio",
            payload={"video_id": video.id, "cues_count": len(video.cues)},
        )
        video.cues = await generate_tts_audio_for_cues(
            video.cues,
            str(audio_dir),
            config.tts_model,
            api_key=resolve_feature_api_key("tts", req.tts_api_key),
            log_callback=log,
        )
        validate_stage_ready(video, "audio")
        emit_tool_hook(
            task_id,
            "after_tool_call",
            "orchestrate.clip_pipeline",
            "generate_tts_audio",
            payload={"video_id": video.id},
        )

    # 4. Align audio on original timeline and mux with muted original video.
    task.status = TaskStatus.DUBBING_RUNNING
    video.status = "dubbing_running"
    store.update(task)
    await manager.broadcast(task_id, {"type": "status_change", "task_id": task_id, "status": task.status})

    duration = resolve_video_duration(video)
    aligned_audio = str(out_dir / "aligned_audio.m4a")
    preview_path = str(out_dir / "preview.mp4")
    emit_tool_hook(
        task_id,
        "before_tool_call",
        "orchestrate.clip_pipeline",
        "compose_dubbed_video",
        payload={"video_id": video.id, "duration": duration},
    )
    await compose_aligned_audio_track(video.cues, duration, aligned_audio)
    await mux_video_with_audio(video.video_path, aligned_audio, preview_path)
    video.aligned_audio_path = aligned_audio
    video.preview_path = preview_path
    video.status = "ready_preview"
    video.error_message = None
    store.update(task)
    emit_tool_hook(
        task_id,
        "after_tool_call",
        "orchestrate.clip_pipeline",
        "compose_dubbed_video",
        payload={"video_id": video.id, "preview_path": preview_path},
    )
    await log("success", "system", f"视频 {video.filename} 预览已生成")


async def _run_one_click_translate_task(task_id: str, req: OneClickTranslateStartRequest):
    store = get_store()
    task = store.get(task_id)
    if not task:
        return

    async def log(level, source, msg, progress=None):
        await log_to_ws(task_id, level, source, msg, progress)

    config = OneClickTranslateConfig(
        source_language=req.source_language,
        target_language=req.target_language,
        subtitle_model=req.subtitle_model,
        tts_model=req.tts_model,
    )

    try:
        total_videos = len(task.translation_videos)
        failed_videos = []
        for index, video in enumerate(task.translation_videos, start=1):
            await log("info", "system", f"处理视频 {index}/{total_videos}: {video.filename}")
            try:
                await _process_translation_video(
                    task, video, req, config, log,
                    start_stage="asr",
                    set_task_asr=(index == 1),
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                # 失败隔离：单个视频失败不中断整个批次，标记后继续处理下一个视频
                video.status = "error"
                video.error_message = str(e)
                failed_videos.append(video.filename)
                store.update(task)
                await log("error", "system", f"视频 {video.filename} 处理失败: {str(e)}，继续处理下一个视频")

        if _translation_task_has_success(task):
            task.status = TaskStatus.REVIEW
            task.export_path = str(EXPORTS_DIR / "one_click_translate" / task_id)
            task.error_message = (
                f"{len(failed_videos)} 个视频处理失败，可单独重试: {', '.join(failed_videos)}"
                if failed_videos else None
            )
        else:
            task.status = TaskStatus.ERROR
            task.error_message = f"全部 {total_videos} 个视频处理失败"
        store.update(task)
        await manager.broadcast(task_id, {"type": "status_change", "task_id": task_id, "status": task.status})

    except asyncio.CancelledError:
        for video in task.translation_videos:
            cancel_asr_processes(video.video_path)
            _rollback_translation_video_status(video)
        store.update(task)
        if not _task_runner_superseded(task_id):
            fallback = TaskStatus.REVIEW if _translation_task_has_success(task) else TaskStatus.PENDING
            await _rollback_cancelled_task(task_id, fallback, "system", "一键转英文流水线")

    except Exception as e:
        task = store.get(task_id)
        if task:
            task.status = TaskStatus.ERROR
            task.error_message = str(e)
            store.update(task)
        await log("error", "system", f"一键转英文失败: {str(e)}")


_TRANSLATION_STAGE_LABELS = {
    "asr": "ASR 识别",
    "translate": "字幕校准/翻译",
    "tts": "TTS 配音",
    "dub": "音频对齐/预览合成",
}


@router.post("/api/tasks/{task_id}/one-click-translate/videos/{video_id}/retry")
async def retry_translation_video(task_id: str, video_id: str, req: OneClickTranslateStartRequest):
    """
    单视频断点续跑：根据已有阶段产物（validate_stage_ready 契约）判断续跑起点，
    force_restart=True 时无视产物从 ASR 从头重跑。API key 只随请求体使用，不落盘。
    """
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    _require_translate_task(task)
    video = _find_translation_video(task, video_id)
    if not video:
        raise HTTPException(404, "Translation video not found")
    if req.preview_token:
        try:
            options = req.model_dump(
                exclude={"preview_token", "preview_mode"},
                exclude_unset=True,
            )
            options["video_id"] = video_id
            validate_execution_preview_token(
                req.preview_token,
                task=task,
                operation="translate_retry",
                mode=req.preview_mode,
                options=options,
            )
        except ExecutionPreviewError as exc:
            raise HTTPException(409, str(exc)) from exc
    _validate_one_click_translate_start_request(req)

    if req.force_restart:
        start_stage = "asr"
    else:
        start_stage = determine_resume_stage(video)
        previous_config = task.one_click_translate_config
        if previous_config is not None:
            subtitle_changed = (
                previous_config.source_language != req.source_language
                or previous_config.target_language != req.target_language
                or previous_config.subtitle_model.model_dump(mode="json")
                != req.subtitle_model.model_dump(mode="json")
            )
            tts_changed = (
                previous_config.tts_model.model_dump(mode="json")
                != req.tts_model.model_dump(mode="json")
            )
            if subtitle_changed and _TRANSLATION_STAGE_INDEX[start_stage] > _TRANSLATION_STAGE_INDEX["translate"]:
                start_stage = "translate"
            elif tts_changed and _TRANSLATION_STAGE_INDEX[start_stage] > _TRANSLATION_STAGE_INDEX["tts"]:
                start_stage = "tts"

    task.one_click_translate_config = OneClickTranslateConfig(
        source_language=req.source_language,
        target_language=req.target_language,
        subtitle_model=req.subtitle_model,
        tts_model=req.tts_model,
    )
    task.error_message = None
    video.error_message = None
    store.update(task)

    _start_task_runner(
        task_id,
        _run_translation_video_retry(task_id, video_id, req, start_stage),
        TaskStatus.TRANSLATE_RUNNING,
        operation="one_click_translate_retry",
    )
    return {
        "success": True,
        "message": f"视频重试已启动，从「{_TRANSLATION_STAGE_LABELS[start_stage]}」阶段续跑",
        "resume_stage": start_stage,
    }


async def _run_translation_video_retry(
    task_id: str,
    video_id: str,
    req: OneClickTranslateStartRequest,
    start_stage: str,
):
    store = get_store()
    task = store.get(task_id)
    if not task:
        return
    video = _find_translation_video(task, video_id)
    if not video:
        return

    async def log(level, source, msg, progress=None):
        await log_to_ws(task_id, level, source, msg, progress)

    config = OneClickTranslateConfig(
        source_language=req.source_language,
        target_language=req.target_language,
        subtitle_model=req.subtitle_model,
        tts_model=req.tts_model,
    )

    try:
        await log("info", "system",
                  f"重试视频 {video.filename}，从「{_TRANSLATION_STAGE_LABELS[start_stage]}」阶段续跑")
        set_task_asr = bool(task.translation_videos) and task.translation_videos[0].id == video.id
        await _process_translation_video(
            task, video, req, config, log,
            start_stage=start_stage,
            set_task_asr=set_task_asr,
        )

        task.status = TaskStatus.REVIEW
        task.export_path = str(EXPORTS_DIR / "one_click_translate" / task_id)
        store.update(task)
        await manager.broadcast(task_id, {"type": "status_change", "task_id": task_id, "status": task.status})

    except asyncio.CancelledError:
        cancel_asr_processes(video.video_path)
        if not _task_runner_superseded(task_id):
            _rollback_translation_video_status(video)
            store.update(task)
            fallback = TaskStatus.REVIEW if _translation_task_has_success(task) else TaskStatus.PENDING
            await _rollback_cancelled_task(task_id, fallback, "system", "单视频重试")

    except Exception as e:
        video.status = "error"
        video.error_message = str(e)
        if _translation_task_has_success(task):
            task.status = TaskStatus.REVIEW
        else:
            task.status = TaskStatus.ERROR
            task.error_message = str(e)
        store.update(task)
        await log("error", "system", f"视频 {video.filename} 重试失败: {str(e)}")
        await manager.broadcast(task_id, {"type": "status_change", "task_id": task_id, "status": task.status})


@router.put("/api/tasks/{task_id}/one-click-translate/videos/{video_id}/cues")
async def update_translation_cues(task_id: str, video_id: str, req: UpdateTranslationCuesRequest):
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    _require_translate_task(task)
    video = _find_translation_video(task, video_id)
    if not video:
        raise HTTPException(404, "Translation video not found")

    old_by_id = {cue.id: cue for cue in video.cues}
    new_ids = {cue.id for cue in req.cues}
    if set(old_by_id.keys()) != new_ids:
        raise HTTPException(400, "字幕 cue 数量或 id 不一致，不能破坏时间轴交接")

    validate_cue_timeline(req.cues)
    contract_report = validate_translation_cues_contract(list(old_by_id.values()), req.cues)
    if contract_report.rejected:
        try:
            await verify_contract_report(task_id, "manual_translation_cues", contract_report)
        except ContractVerificationError:
            pass
        raise HTTPException(400, f"字幕 cue 契约失败: {format_contract_issues(contract_report.issues)}")

    for cue in req.cues:
        old = old_by_id[cue.id]
        if (cue.translated_text or "") != (old.translated_text or ""):
            cue.audio_path = None
            cue.audio_duration = None
            cue.audio_text = None
            cue.status = "translated"
        else:
            cue.audio_path = old.audio_path
            cue.audio_duration = old.audio_duration
            cue.audio_text = old.audio_text

    video.cues = req.cues
    out_dir = _translation_output_dir(task_id, video.id)
    video.calibrated_srt_path = write_cues_srt(video.cues, str(out_dir / "calibrated.srt"), "calibrated_text")
    video.translated_srt_path = write_cues_srt(video.cues, str(out_dir / "translated.srt"), "translated_text")
    video.status = "translated"
    task.status = TaskStatus.REVIEW
    store.update(task)
    return {"success": True, "video": video.model_dump()}


@router.post("/api/tasks/{task_id}/one-click-translate/export")
async def export_one_click_translate(task_id: str, req: OneClickTranslateStartRequest):
    """
    Final export after users edit intermediate subtitles.
    Missing/stale cue audio is regenerated, then every video is muxed again.
    """
    _require_export_approval(req.approval_token)
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    _require_translate_task(task)
    if not task.translation_videos:
        raise HTTPException(400, "No videos to export")
    _validate_one_click_translate_start_request(req)

    t = asyncio.create_task(_run_one_click_translate_export(task_id, req))
    _export_tasks[task_id] = t
    return {"success": True, "message": "一键转英文最终导出已启动"}


async def _run_one_click_translate_export(task_id: str, req: OneClickTranslateStartRequest):
    store = get_store()
    task = store.get(task_id)
    if not task:
        return

    task.status = TaskStatus.EXPORT_RUNNING
    store.update(task)
    await manager.broadcast(task_id, {"type": "status_change", "task_id": task_id, "status": TaskStatus.EXPORT_RUNNING})

    async def log(level, source, msg, progress=None):
        await log_to_ws(task_id, level, source, msg, progress)

    try:
        output_files = []
        tts_config = req.tts_model
        for video in task.translation_videos:
            validate_stage_ready(video, "translated")
            out_dir = _translation_output_dir(task_id, video.id)
            audio_dir = out_dir / "audio"
            with model_usage_task_context(task_id):
                video.cues = await generate_tts_audio_for_cues(
                    video.cues,
                    str(audio_dir),
                    tts_config,
                    api_key=resolve_feature_api_key("tts", req.tts_api_key),
                    log_callback=log,
                )
            validate_stage_ready(video, "audio")
            duration = resolve_video_duration(video)
            aligned_audio = str(out_dir / "aligned_audio_final.m4a")
            final_path = str(out_dir / "final.mp4")
            await compose_aligned_audio_track(video.cues, duration, aligned_audio)
            await mux_video_with_audio(video.video_path, aligned_audio, final_path)
            video.aligned_audio_path = aligned_audio
            video.export_path = final_path
            video.status = "done"
            output_files.append(final_path)

        task.status = TaskStatus.DONE
        task.export_path = str(EXPORTS_DIR / "one_click_translate" / task_id)
        store.update(task)
        await manager.broadcast(task_id, {
            "type": "export_done",
            "task_id": task_id,
            "output_path": task.export_path,
            "output_files": output_files,
            "mode": "one_click_translate",
        })
    except asyncio.CancelledError:
        for video in task.translation_videos:
            _rollback_translation_video_status(video)
        task.status = TaskStatus.REVIEW if _translation_task_has_success(task) else TaskStatus.PENDING
        task.error_message = None
        store.update(task)
        await manager.broadcast(task_id, {
            "type": "task_cancelled",
            "task_id": task_id,
            "status": task.status,
        })
        await manager.broadcast(task_id, {
            "type": "status_change",
            "task_id": task_id,
            "status": task.status,
        })
    except Exception as e:
        task.status = TaskStatus.ERROR
        task.error_message = str(e)
        store.update(task)
        await log("error", "ffmpeg", f"一键转英文导出失败: {str(e)}")
    finally:
        _export_tasks.pop(task_id, None)


@router.get("/api/tasks/{task_id}/one-click-translate/videos/{video_id}/preview")
async def get_translation_preview(task_id: str, video_id: str):
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    _require_translate_task(task)
    video = _find_translation_video(task, video_id)
    if not video or not video.preview_path:
        raise HTTPException(404, "Preview not found")
    if not os.path.exists(video.preview_path):
        raise HTTPException(404, "Preview file not found on disk")
    return FileResponse(video.preview_path, media_type="video/mp4")
