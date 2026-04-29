"""
GoldenClip FastAPI Backend
Local video editing workstation API server.

Design: 暗金剪辑台 · 编导美学
Run: uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
"""

import os
import uuid
import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Set

from fastapi import FastAPI, UploadFile, File, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from .models.task import (
    Task, TaskStatus, TaskType, CreateTaskRequest,
    UpdateSegmentsRequest, ExportRequest, AuditRequest,
    Segment, SegmentAction, TaskParams, SubtitleStyle, HookConfig, LogEntry, ASRSnapshot, AuditSnapshot, SegOptimSnapshot, ASRResult,
    LearningTask, LearningTaskStatus, CreateLearningTaskRequest, LearningAnalyzeRequest,
    ClipRangeRequest,
)
from .services.task_store import get_store
from .services.learning_store import get_learning_store
from .services.asr_pipeline import (
    run_asr_pipeline, get_video_duration, generate_thumbnail,
    resmooth_speakers, segment_by_silence, merge_leading_particles, MAX_SEGMENT_DURATION,
    reload_word_config,
)
from .services.semantic_auditor import run_semantic_audit, run_segment_optimizer
from .services.ffmpeg_executor import export_ffmpeg_lossless, generate_srt, check_ffmpeg_available, kill_export, get_video_info
from .services.ffmpeg_executor import export_ffmpeg_multi_clips
from .services.jianying_builder import build_jianying_draft
from .services.prompt_optimizer import collect_corrections, generate_prompt_improvement, merge_user_style
from .services.learning_analyzer import (
    run_learning_analysis, save_versioned_prompt, get_prompt_versions,
    diff_transcripts, build_analysis_prompt, build_rewrite_prompt,
    load_learning_system_prompt, load_rewrite_system_prompt, apply_prompt_changes,
)
from .services.semantic_auditor import (
    load_system_prompt as load_audit_system_prompt,
    build_audit_prompt, parse_claude_response, apply_segment_corrections,
)
from .services.golden_quote_extractor import suggest_golden_quotes

# Directories
BASE_DIR = Path(__file__).parent.parent
UPLOADS_DIR = BASE_DIR / "uploads"
EXPORTS_DIR = BASE_DIR / "exports"
THUMBNAILS_DIR = BASE_DIR / "thumbnails"

LEARNING_UPLOADS_DIR = UPLOADS_DIR / "learning"

for d in [UPLOADS_DIR, EXPORTS_DIR, THUMBNAILS_DIR, LEARNING_UPLOADS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

app = FastAPI(
    title="GoldenClip API",
    description="智能视频工作站 - 编导思维驱动的本地剪辑工具",
    version="3.0.0"
)

# CORS for React frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,  # 与 allow_origins=["*"] 共存时必须为 False（CORS 规范要求）
    allow_methods=["*"],
    allow_headers=["*"],
)

# WebSocket connection manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, List[WebSocket]] = {}

    async def connect(self, task_id: str, websocket: WebSocket):
        await websocket.accept()
        if task_id not in self.active_connections:
            self.active_connections[task_id] = []
        self.active_connections[task_id].append(websocket)

    def disconnect(self, task_id: str, websocket: WebSocket):
        if task_id in self.active_connections:
            self.active_connections[task_id].remove(websocket)

    async def broadcast(self, task_id: str, message: dict):
        if task_id in self.active_connections:
            dead = []
            for ws in self.active_connections[task_id]:
                try:
                    await ws.send_json(message)
                except:
                    dead.append(ws)
            for ws in dead:
                self.active_connections[task_id].remove(ws)

    async def broadcast_all(self, message: dict):
        for task_id in self.active_connections:
            await self.broadcast(task_id, message)


manager = ConnectionManager()

# 学习任务正在运行的 asyncio.Task 注册表，key 为 learning task_id
_learning_active_tasks: Dict[str, asyncio.Task] = {}

# 导出任务正在运行的 asyncio.Task 注册表，key 为 task_id
_export_tasks: Dict[str, asyncio.Task] = {}

TASK_LIST_EXCLUDE_FIELDS = {
    "asr_result",
    "asr_history",
    "audit_segments",
    "audit_history",
    "params",
    "golden_quote_order",
    "seg_optim_history",
}

LEARNING_TASK_LIST_EXCLUDE_FIELDS = {
    "original_asr_result",
    "edited_asr_result",
    "analysis_result",
    "new_prompt_content",
    "new_prompt_path",
}


def serialize_task_list_item(task: Task) -> dict:
    return task.model_dump(exclude=TASK_LIST_EXCLUDE_FIELDS)


def serialize_learning_task_list_item(task: LearningTask) -> dict:
    return task.model_dump(exclude=LEARNING_TASK_LIST_EXCLUDE_FIELDS)


def _cancel_learning_task(task_id: str):
    """取消某个学习任务关联的后台 asyncio.Task（如有）。"""
    task = _learning_active_tasks.pop(task_id, None)
    if task and not task.done():
        task.cancel()


async def log_to_ws(task_id: str, level: str, source: str, message: str, progress: float = None):
    """Send log entry via WebSocket to frontend Console."""
    entry = {
        "type": "log",
        "timestamp": datetime.now().isoformat(),
        "level": level,
        "source": source,
        "message": message,
        "progress": progress
    }
    await manager.broadcast(task_id, entry)
    print(f"[{source.upper()}] [{level.upper()}] {message}")


# ============================================================
# Task Management Endpoints
# ============================================================

@app.post("/api/reload-word-config")
async def reload_word_config_endpoint():
    """热重载词库配置（backend/data/word_config.yaml），无需重启后端。"""
    cfg = reload_word_config()
    from .services.asr_pipeline import (
        DEFAULT_FILLER_WORDS, _COMPOUND_WORD_ENDINGS,
        _REDUPLICATION_AA_WORDS, _LEADING_PARTICLES,
    )
    return {
        "success": True,
        "filler_words_count": len(DEFAULT_FILLER_WORDS),
        "compound_endings_count": len(_COMPOUND_WORD_ENDINGS),
        "reduplication_count": len(_REDUPLICATION_AA_WORDS),
        "leading_particles": len(_LEADING_PARTICLES),
    }


@app.get("/api/health")
async def health_check():
    return {
        "status": "ok",
        "ffmpeg": check_ffmpeg_available(),
        "version": "3.0.0"
    }


@app.get("/api/ollama/models")
async def get_ollama_models(base_url: str = "http://localhost:11434"):
    """
    查询本地 Ollama 已安装的模型列表。
    前端在配置页切换到 Ollama 时调用，用于动态展示可选模型。
    """
    import httpx
    url = base_url.rstrip("/") + "/api/tags"
    try:
        # 使用 AsyncHTTPTransport(proxy=None) 绕过系统代理（VPN/ClashX），确保直连本地 Ollama
        transport = httpx.AsyncHTTPTransport(proxy=None)
        async with httpx.AsyncClient(transport=transport, timeout=5.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
            # Ollama /api/tags 返回 {"models": [{"name": "...", ...}, ...]}
            models = [m["name"] for m in data.get("models", [])]
            return {"models": models, "online": True}
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={"models": [], "online": False, "error": str(e)}
        )


@app.get("/api/tasks")
async def list_tasks():
    store = get_store()
    tasks = store.get_all()
    return [serialize_task_list_item(t) for t in tasks]


@app.post("/api/tasks")
async def create_task(req: CreateTaskRequest):
    store = get_store()
    params = req.params or TaskParams()
    if req.theme_id:
        params.theme_id = req.theme_id
    task = Task(
        name=req.name,
        task_type=req.task_type,
        params=params,
    )
    store.create(task)
    return task.model_dump()


@app.get("/api/tasks/{task_id}")
async def get_task(task_id: str):
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    return task.model_dump()


@app.delete("/api/tasks/{task_id}")
async def delete_task(task_id: str):
    store = get_store()
    success = store.delete(task_id)
    if not success:
        raise HTTPException(404, "Task not found")
    return {"success": True}


# ============================================================
# Video Upload
# ============================================================

@app.post("/api/tasks/{task_id}/upload")
async def upload_video(task_id: str, file: UploadFile = File(...)):
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")

    # Save uploaded file
    ext = Path(file.filename).suffix.lower()
    if ext not in [".mp4", ".mov", ".avi", ".mkv", ".webm"]:
        raise HTTPException(400, f"Unsupported format: {ext}")

    video_path = UPLOADS_DIR / f"{task_id}{ext}"
    with open(video_path, "wb") as f:
        content = await file.read()
        f.write(content)

    # Get video info
    duration = get_video_duration(str(video_path))

    # Generate thumbnail
    thumb_path = THUMBNAILS_DIR / f"{task_id}.jpg"
    generate_thumbnail(str(video_path), str(thumb_path))

    # Update task
    task.video_path = str(video_path)
    task.video_filename = file.filename
    task.video_duration = duration
    task.original_duration = duration
    task.thumbnail_path = str(thumb_path) if thumb_path.exists() else None
    task.status = TaskStatus.PENDING
    store.update(task)

    return {
        "success": True,
        "video_path": str(video_path),
        "duration": duration,
        "filename": file.filename
    }


# ============================================================
# Video Streaming
# ============================================================

@app.get("/api/tasks/{task_id}/video")
async def stream_video(task_id: str):
    store = get_store()
    task = store.get(task_id)
    if not task or not task.video_path:
        raise HTTPException(404, "Video not found")
    if not os.path.exists(task.video_path):
        raise HTTPException(404, "Video file not found on disk")
    return FileResponse(task.video_path, media_type="video/mp4")


@app.get("/api/tasks/{task_id}/frame")
async def extract_frame(task_id: str, t: float = 0.0):
    """使用 FFmpeg 从视频中提取指定时间点的 JPEG 帧，供字幕预览使用。"""
    store = get_store()
    task = store.get(task_id)
    if not task or not task.video_path:
        raise HTTPException(404, "Video not found")
    if not os.path.exists(task.video_path):
        raise HTTPException(404, "Video file not found on disk")

    import subprocess, tempfile
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-ss", str(t),
                "-i", task.video_path,
                "-frames:v", "1",
                "-q:v", "4",
                tmp_path,
            ],
            capture_output=True,
            timeout=10,
        )
        if not os.path.exists(tmp_path) or os.path.getsize(tmp_path) == 0:
            raise HTTPException(500, "Frame extraction failed")
        return FileResponse(tmp_path, media_type="image/jpeg")
    except subprocess.TimeoutExpired:
        raise HTTPException(500, "Frame extraction timed out")


@app.get("/api/tasks/{task_id}/thumbnail")
async def get_thumbnail(task_id: str):
    store = get_store()
    task = store.get(task_id)
    if task and task.thumbnail_path and os.path.exists(task.thumbnail_path):
        return FileResponse(task.thumbnail_path, media_type="image/jpeg")
    raise HTTPException(404, "Thumbnail not found")


# ============================================================
# ASR Processing
# ============================================================

class AsrRequest(BaseModel):
    model: str = "base"
    backend: str = "whisper"  # "whisper" | "funasr"
    silence_threshold: float = 0.3  # 静音切分阈值（秒），前端可按需覆盖
    enable_diarization: Optional[bool] = None  # None = 使用任务默认值；True/False = 本次覆盖

@app.post("/api/tasks/{task_id}/asr")
async def trigger_asr(task_id: str, body: AsrRequest = None):
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    if not task.video_path or not os.path.exists(task.video_path):
        raise HTTPException(400, "Video file not uploaded")

    whisper_model = (body.model if body else None) or "base"
    backend = (body.backend if body else None) or "whisper"
    silence_threshold = (body.silence_threshold if body else None) or 0.3

    # 将前端传入的分段阈值写入任务参数，后续 pipeline 直接读 task.params
    task.params.silence_threshold = silence_threshold
    # enable_diarization 若前端明确传入，则本次覆盖任务默认值
    enable_diarization_override = body.enable_diarization if body else None
    if enable_diarization_override is not None:
        task.params.enable_diarization = enable_diarization_override
    store.update(task)

    # Run ASR in background
    asyncio.create_task(_run_asr_task(task_id, whisper_model, backend))
    label = f"FunASR Paraformer-zh" if backend == "funasr" else f"Whisper {whisper_model}"
    diar_label = "含说话人分离" if task.params.enable_diarization else "无说话人分离"
    return {"success": True, "message": f"ASR 任务已启动（{label}，分段阈值 {silence_threshold}s，{diar_label}）"}


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
    # 访谈压缩场景：指定2位说话人，提升 CAM++ 聚类精度
    oracle_num = 2 if (task.task_type and task.task_type.value == "interview_compress") else None
    try:
        await log("info", "system", f"开始处理任务: {task.name}（{label}）")
        asr_result = await run_asr_pipeline(
            task.video_path, task.params, log,
            whisper_model=whisper_model,
            backend=backend,
            oracle_num=oracle_num,
            clip_start=task.clip_start,
            clip_end=task.clip_end,
        )

        task.asr_result = asr_result
        task.audit_segments = asr_result.segments

        # 若设置了裁剪区间，对区间外段自动标记 delete
        if task.clip_start is not None or task.clip_end is not None:
            cs = task.clip_start or 0.0
            ce = task.clip_end or float("inf")
            for seg in task.audit_segments:
                if seg.end <= cs or seg.start >= ce:
                    seg.action = SegmentAction.DELETE
                    seg.reason = "区间外自动排除"
        task.video_duration = asr_result.duration
        task.original_duration = asr_result.duration
        task.status = TaskStatus.ASR_DONE

        # 记录本次识别快照到历史列表
        # 使用 model_copy(deep=True) 确保快照是独立副本，防止后续审计原地修改 Segment 对象时污染历史数据
        snapshot = ASRSnapshot(
            version=len(task.asr_history) + 1,
            created_at=datetime.now().isoformat(),
            engine=label,
            segments_count=len(asr_result.segments),
            duration=asr_result.duration,
            asr_result=asr_result.model_copy(deep=True),
        )
        task.asr_history.append(snapshot)
        task.asr_current_version = snapshot.version

        store.update(task)

        await log("success", "system", f"ASR 完成，共 {len(asr_result.segments)} 个片段")

        # Notify frontend of status change
        await manager.broadcast(task_id, {
            "type": "status_change",
            "task_id": task_id,
            "status": TaskStatus.ASR_DONE,
            "segments_count": len(asr_result.segments)
        })

    except Exception as e:
        task.status = TaskStatus.ERROR
        task.error_message = str(e)
        store.update(task)
        await log("error", "system", f"ASR 失败: {str(e)}")


class AsrSwitchRequest(BaseModel):
    version: int  # 要切换到的历史版本号（从 1 开始）


@app.post("/api/tasks/{task_id}/asr/switch")
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
        seg.display_text = None
        seg.fix_type = None
        seg.split_before = None
        seg.subtitle_line_break = None
        seg.merged = False
    task.audit_segments = restored_segments
    task.asr_current_version = target.version
    task.updated_at = datetime.now().isoformat()
    store.update(task)

    return {"success": True, "message": f"已切换到 v{body.version}（{target.engine}）", "task": task}


class ResmoothdRequest(BaseModel):
    threshold: float  # 平滑阈值（秒），推荐范围 0.5 ~ 5.0


@app.post("/api/tasks/{task_id}/asr/resmooth")
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
    if not task.asr_result.diar_segments:
        raise HTTPException(400, "当前 ASR 结果不含说话人分离数据，请重新识别并开启说话人分离")

    threshold = max(0.1, min(10.0, body.threshold))  # 限制在合理范围内

    # 用新阈值重算词级 speaker
    new_words = resmooth_speakers(
        task.asr_result.words,
        task.asr_result.diar_segments,
        min_switch_duration=threshold,
    )

    # 重新切段（speaker turn 切分逻辑在 segment_by_silence 中）
    new_segments_raw = segment_by_silence(
        new_words,
        silence_threshold=task.params.silence_threshold,
        min_duration=0.3,
        max_duration=MAX_SEGMENT_DURATION,
    )
    new_segments_raw = merge_leading_particles(new_segments_raw)

    # 将新的 speaker 信息合并回 audit_segments：
    # 只更新 speaker 字段，保留用户已有的 action/reason 等审计结果
    seg_speaker_map = {seg.id: seg.speaker for seg in new_segments_raw}
    # 同时更新 words 中的 speaker（按 start 时间精确匹配）
    word_speaker_map: dict = {round(w.start, 3): w.speaker for w in new_words}

    # #region agent log
    import json as _json, time as _time
    _new_ids = list(seg_speaker_map.keys())[:3]
    _audit_ids = [s.id for s in task.audit_segments[:3]]
    _id_match_count = sum(1 for s in task.audit_segments if s.id in seg_speaker_map)
    _log_entry = {"sessionId":"9962b8","runId":"resmooth-v1","hypothesisId":"H-A","location":"main.py:512","message":"resmooth id match check","data":{"new_seg_ids_sample":_new_ids,"audit_seg_ids_sample":_audit_ids,"id_match_count":_id_match_count,"total_audit_segs":len(task.audit_segments),"word_map_size":len(word_speaker_map)},"timestamp":int(_time.time()*1000)}
    with open("/Users/zhangyang/Documents/GitHub/JianyingVideoCut/.cursor/debug-9962b8.log","a") as _f: _f.write(_json.dumps(_log_entry)+"\n")
    # #endregion

    for seg in task.audit_segments:
        # 更新 segment.words 中每个词的 speaker
        if seg.words:
            _old_spk = seg.speaker
            seg.words = [
                seg.words[i].model_copy(update={"speaker": word_speaker_map.get(round(seg.words[i].start, 3), seg.words[i].speaker)})
                for i in range(len(seg.words))
            ]
            # 从更新后的 words 重新推算段级 speaker（多数投票）
            speakers_in_seg = [w.speaker for w in seg.words if w.speaker]
            if speakers_in_seg:
                seg.speaker = max(set(speakers_in_seg), key=speakers_in_seg.count)
            # #region agent log
            _updated_spks = [w.speaker for w in seg.words if w.speaker]
            _new_seg_spk = max(set(_updated_spks), key=_updated_spks.count) if _updated_spks else seg.speaker
            _log_entry2 = {"sessionId":"9962b8","runId":"resmooth-v2-fix","hypothesisId":"H-B","location":"main.py:525","message":"audit_seg word update","data":{"seg_id":seg.id,"old_seg_speaker":_old_spk,"new_seg_speaker":seg.speaker,"words_majority_speaker":_new_seg_spk,"changed":_old_spk != seg.speaker},"timestamp":int(_time.time()*1000)}
            with open("/Users/zhangyang/Documents/GitHub/JianyingVideoCut/.cursor/debug-9962b8.log","a") as _f: _f.write(_json.dumps(_log_entry2)+"\n")
            # #endregion

    # 更新 asr_result 中的 words 和平滑阈值记录
    task.asr_result.words = new_words
    task.asr_result.diar_smooth_threshold = threshold
    # 同步更新 asr_result.segments 的 speaker（供历史版本查看）
    for seg in task.asr_result.segments:
        if seg.words:
            seg.words = [
                seg.words[i].model_copy(update={"speaker": word_speaker_map.get(round(seg.words[i].start, 3), seg.words[i].speaker)})
                for i in range(len(seg.words))
            ]
        # 重算段 speaker（取多数词）
        speakers_in_seg = [w.speaker for w in (seg.words or []) if w.speaker]
        if speakers_in_seg:
            seg.speaker = max(set(speakers_in_seg), key=speakers_in_seg.count)

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
    silence_threshold: float  # 0.1 ~ 1.0
    save: bool = False        # False=预览，True=保存


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


def _find_best_threshold(words: list, candidates: list[float], max_chars: int = 30) -> dict:
    """遍历候选阈值，找到超长段最少的最优值。"""
    best = None
    for thr in candidates:
        segs = segment_by_silence(
            words,
            silence_threshold=thr,
            min_duration=0.3,
            max_duration=MAX_SEGMENT_DURATION,
        )
        stats = _calc_segment_stats(segs, max_chars)
        # 用 (超长占比, 碎段惩罚) 联合排序：超长段越少越好，段数不宜过多
        score = stats["over_limit_pct"] + max(0, stats["total"] - 200) * 0.1
        if best is None or score < best["score"]:
            best = {"threshold": thr, "stats": stats, "score": score}
    if best:
        over = best["stats"]["over_limit_count"]
        pct = best["stats"]["over_limit_pct"]
        best["reason"] = f"超{max_chars}字段最少（{over}个/{pct}%）"
    return best


@app.post("/api/tasks/{task_id}/asr/resegment")
async def resegment_segments(task_id: str, body: ResegmentRequest):
    """
    用新的静音阈值重新切段，不重跑 ASR。

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

    threshold = max(0.05, min(0.50, body.silence_threshold))
    words = task.asr_result.words

    # 用新阈值重新切段
    new_segments = segment_by_silence(
        words,
        silence_threshold=threshold,
        min_duration=0.3,
        max_duration=MAX_SEGMENT_DURATION,
    )
    new_segments = merge_leading_particles(new_segments)
    stats = _calc_segment_stats(new_segments)

    # 智能推荐
    candidates = [0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50]
    recommendation = _find_best_threshold(words, candidates)
    if recommendation:
        recommendation.pop("score", None)

    result: dict = {
        "success": True,
        "segments": [s.model_dump() for s in new_segments],
        "stats": stats,
        "recommendation": recommendation,
    }

    if body.save:
        # 保存新分段
        task.asr_result.segments = new_segments
        task.audit_segments = new_segments
        task.params.silence_threshold = threshold

        # 重算统计（segments_kept / segments_deleted / edited_duration）
        task.update_stats()

        # 若已做过审计，回退到 asr_done（审计决策与旧段 ID 绑定已失效）
        if task.status in (TaskStatus.REVIEW, TaskStatus.AUDIT_RUNNING, TaskStatus.EXPORT_RUNNING, TaskStatus.DONE):
            task.status = TaskStatus.ASR_DONE

        task.updated_at = datetime.now().isoformat()
        store.update(task)
        result["task"] = task
        result["message"] = f"已用阈值 {threshold}s 重新分段，共 {len(new_segments)} 段"
    else:
        result["message"] = f"预览：阈值 {threshold}s 将产生 {len(new_segments)} 段"

    return result


# ============================================================
# Segment Structure Optimizer (段落优化)
# ============================================================

class SegOptimRequest(BaseModel):
    provider: str = "ollama"
    ollama_model: str = "deepseek-r1:32b"
    ollama_base_url: str = "http://localhost:11434"
    claude_api_key: Optional[str] = None
    claude_model: Optional[str] = "anthropic/claude-3.5-sonnet"


@app.post("/api/tasks/{task_id}/optimize-segments")
async def trigger_segment_optimize(task_id: str, req: SegOptimRequest):
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    if not task.audit_segments:
        raise HTTPException(400, "No segments to optimize. Run ASR first.")

    asyncio.create_task(_run_seg_optim_task(task_id, req))
    return {"success": True, "message": "段落优化已启动"}


@app.post("/api/tasks/{task_id}/skip-optimize-segments")
async def skip_segment_optimize(task_id: str):
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")

    task.status = TaskStatus.SEG_OPTIM_DONE
    store.update(task)
    await manager.broadcast(task_id, {
        "type": "status_change",
        "task_id": task_id,
        "status": TaskStatus.SEG_OPTIM_DONE,
    })
    return {"success": True, "message": "已跳过段落优化"}


@app.post("/api/tasks/{task_id}/recover")
async def recover_task(task_id: str):
    """
    从 ERROR 状态恢复任务到最佳可用状态：
    - 有 audit_segments 且含非全 KEEP 决策 → review
    - 有 asr_result / asr_history → asr_done
    - 否则 → pending
    """
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")

    if task.status != TaskStatus.ERROR:
        raise HTTPException(400, f"任务当前状态为 {task.status}，无需恢复")

    # 判断最佳恢复点
    has_audit = bool(task.audit_segments) and any(
        seg.action != "keep" for seg in task.audit_segments
    )
    has_asr = bool(task.asr_result) or bool(task.asr_history)

    if has_audit:
        recovered_status = TaskStatus.REVIEW
    elif has_asr:
        recovered_status = TaskStatus.ASR_DONE
    else:
        recovered_status = TaskStatus.PENDING

    task.status = recovered_status
    task.error_message = None
    task.updated_at = datetime.now().isoformat()
    store.update(task)

    await manager.broadcast(task_id, {
        "type": "status_change",
        "task_id": task_id,
        "status": recovered_status,
    })
    return {"success": True, "recovered_status": recovered_status}


class SegOptimSwitchRequest(BaseModel):
    version: int  # 要切换到的历史版本号（从 1 开始）


@app.post("/api/tasks/{task_id}/seg-optim/switch")
async def switch_seg_optim_version(task_id: str, body: SegOptimSwitchRequest):
    """切换到指定历史段落优化版本，恢复对应的 audit_segments。"""
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    if not task.seg_optim_history:
        raise HTTPException(400, "无段落优化历史记录")

    target = next((s for s in task.seg_optim_history if s.version == body.version), None)
    if not target:
        raise HTTPException(404, f"未找到版本 v{body.version}")

    # 恢复该版本的段落，重置审计决策
    restored = [seg.model_copy(deep=True) for seg in target.segments]
    for seg in restored:
        seg.action = SegmentAction.KEEP
        seg.reason = None
        seg.rule = None
        seg.claude_action = None
        seg.claude_reason = None
        seg.user_override = False
        seg.display_text = None
        seg.fix_type = None
        seg.split_before = None
        seg.subtitle_line_break = None
        seg.merged = False

    task.audit_segments = restored
    task.seg_optim_current_version = target.version
    task.status = TaskStatus.SEG_OPTIM_DONE
    task.updated_at = datetime.now().isoformat()
    store.update(task)

    return {"success": True, "message": f"已切换到段落优化 v{body.version}", "task": task}


async def _run_seg_optim_task(task_id: str, req: SegOptimRequest):
    store = get_store()
    task = store.get(task_id)
    if not task:
        return

    task.status = TaskStatus.SEG_OPTIM_RUNNING
    store.update(task)

    async def log(level, source, msg, progress=None):
        await log_to_ws(task_id, level, source, msg, progress)

    try:
        # 始终从当前 ASR 结果出发，而非上一次优化结果
        # 保证无论重新优化多少次，输入都是同一份 ASR 数据（与 ASR 重新识别的逻辑一致）
        if not task.asr_result or not task.asr_result.segments:
            await log("error", "seg_optim", "无 ASR 结果，请先运行识别")
            task.status = TaskStatus.ASR_DONE
            store.update(task)
            return
        base_segments = [s.model_copy(deep=True) for s in task.asr_result.segments]
        before_count = len(base_segments)
        updated_segments = await run_segment_optimizer(
            segments=base_segments,
            task_type=task.task_type.value,
            provider=req.provider,
            ollama_model=req.ollama_model,
            ollama_base_url=req.ollama_base_url,
            api_key=req.claude_api_key,
            claude_model=req.claude_model or "anthropic/claude-3.5-sonnet",
            log_callback=log,
        )
        after_count = len(updated_segments)

        task.audit_segments = updated_segments

        # 统计段落优化标记删除的片段（用于 token 节约估算）
        deleted_segs = [s for s in updated_segments if s.action == SegmentAction.DELETE]
        optim_deleted_count = len(deleted_segs)
        optim_deleted_chars = sum(len(s.text or "") for s in deleted_segs)

        # 记录本次优化快照
        model_label = req.claude_model or "claude-3.5-sonnet"
        if req.provider == "ollama":
            model_label = req.ollama_model or "ollama"
        snapshot = SegOptimSnapshot(
            version=len(task.seg_optim_history) + 1,
            created_at=datetime.now().isoformat(),
            model=model_label,
            segments_before=before_count,
            segments_after=after_count,
            optim_deleted_count=optim_deleted_count,
            optim_deleted_chars=optim_deleted_chars,
            segments=[s.model_copy(deep=True) for s in updated_segments],
        )
        task.seg_optim_history.append(snapshot)
        task.seg_optim_current_version = snapshot.version

        task.status = TaskStatus.SEG_OPTIM_DONE
        store.update(task)

        await manager.broadcast(task_id, {
            "type": "status_change",
            "task_id": task_id,
            "status": TaskStatus.SEG_OPTIM_DONE,
            "seg_optim_before": before_count,
            "seg_optim_after": after_count,
        })

    except Exception as e:
        # 失败时退回 asr_done，不阻塞流程
        task.status = TaskStatus.ASR_DONE
        task.error_message = f"段落优化失败: {e}"
        store.update(task)
        await log("error", "seg_optim", f"段落优化失败: {e}")
        await manager.broadcast(task_id, {
            "type": "status_change",
            "task_id": task_id,
            "status": TaskStatus.ASR_DONE,
        })


# ============================================================
# Claude Semantic Audit
# ============================================================

@app.post("/api/tasks/{task_id}/audit")
async def trigger_audit(task_id: str, req: AuditRequest):
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    if not task.audit_segments:
        raise HTTPException(400, "No segments to audit. Run ASR first.")

    asyncio.create_task(_run_audit_task(task_id, req))
    return {"success": True, "message": "语义审计已启动"}


async def _run_audit_task(task_id: str, req: AuditRequest):
    store = get_store()
    task = store.get(task_id)
    if not task:
        return

    task.status = TaskStatus.AUDIT_RUNNING
    store.update(task)

    async def log(level, source, msg, progress=None):
        await log_to_ws(task_id, level, source, msg, progress)

    try:
        is_highlight = task.task_type and task.task_type.value == "highlight_reel"

        if is_highlight:
            # highlight_reel 不走 seg_optim，每次 AI 审计都应从最新 ASR 快照重置，
            # 避免上一次 AI 决策（DELETE）污染本次输入。
            current_snap = next(
                (s for s in task.asr_history if s.version == task.asr_current_version),
                task.asr_history[-1] if task.asr_history else None,
            )
            if current_snap:
                import copy
                task.audit_segments = copy.deepcopy(current_snap.asr_result.segments)
                await log("info", "audit", f"已从 ASR 快照(v{current_snap.version}) 重置 {len(task.audit_segments)} 个原始段落")
            segs_to_audit = task.audit_segments
        else:
            # 过滤掉段落优化已标记为 DELETE 的段（噪声/结巴），不送入主 LLM
            # 这些决策已由 seg_optim 确认，无需 LLM 二次审查，同时节约 token
            pre_deleted_ids: set = {
                s.id for s in task.audit_segments
                if s.action == SegmentAction.DELETE
            }
            segs_to_audit = [s for s in task.audit_segments if s.id not in pre_deleted_ids]
            skipped = len(pre_deleted_ids)
            if skipped:
                await log("info", "audit", f"跳过 seg_optim 已标记删除的 {skipped} 段，仅发送 {len(segs_to_audit)} 段给主 LLM")

        updated_segments = await run_semantic_audit(
            segments=segs_to_audit,
            params=task.params,
            task_type=task.task_type.value if task.task_type else None,
            api_key=req.claude_api_key,
            model=req.claude_model,
            style_mode=req.style_mode,
            log_callback=log,
            force_rule_engine=req.force_rule_engine,
            provider=req.provider,
            ollama_model=req.ollama_model,
            ollama_base_url=req.ollama_base_url,
            highlight_target_dur=req.highlight_target_dur,
            highlight_max_clips=req.highlight_max_clips,
            highlight_clip_min_dur=req.highlight_clip_min_dur,
            highlight_clip_max_dur=req.highlight_clip_max_dur,
            highlight_total_clips=req.highlight_total_clips,
            theme_id=task.params.theme_id if task.params else None,
        )

        # 处理断句修正（split 动作）：重新划定段落边界，消除后再变为普通 keep/delete 段
        updated_segments = apply_segment_corrections(updated_segments)

        if is_highlight:
            # highlight_reel：直接用 AI 结果，无需插回 seg_optim 预删段
            task.audit_segments = updated_segments
        else:
            # 将 seg_optim 预删除的段按原始顺序插回（保留 DELETE 决策）
            audited_map = {s.id: s for s in updated_segments}
            merged: list = []
            for orig in task.audit_segments:
                if orig.id in pre_deleted_ids:
                    merged.append(orig)          # seg_optim DELETE 段原样保留
                elif orig.id in audited_map:
                    merged.append(audited_map[orig.id])  # 主 LLM 审计结果
                else:
                    pass
            # split 产生的新段（id 不在原 audit_segments 中）按顺序追加
            orig_ids = {s.id for s in task.audit_segments}
            for s in updated_segments:
                if s.id not in orig_ids and s.id not in {x.id for x in merged}:
                    merged.append(s)
            task.audit_segments = merged
        task.update_stats()
        task.status = TaskStatus.REVIEW

        # 写入审计历史快照
        audit_snap = AuditSnapshot(
            version=len(task.audit_history) + 1,
            created_at=datetime.now().isoformat(),
            model=req.claude_model if not req.force_rule_engine else "规则引擎",
            segments_count=len(updated_segments),
            segments_kept=task.segments_kept,
            segments_deleted=task.segments_deleted,
        )
        task.audit_history.append(audit_snap)
        task.audit_current_version = audit_snap.version

        store.update(task)

        await manager.broadcast(task_id, {
            "type": "status_change",
            "task_id": task_id,
            "status": TaskStatus.REVIEW,
            "segments_kept": task.segments_kept,
            "segments_deleted": task.segments_deleted,
            "edited_duration": task.edited_duration
        })

    except Exception as e:
        error_msg = str(e)
        is_claude_error = (
            req.claude_api_key
            or os.environ.get("OPENROUTER_API_KEY")
            or os.environ.get("ANTHROPIC_API_KEY")
            or os.environ.get("CLAUDE_API_KEY")
        ) and not req.force_rule_engine

        if is_claude_error:
            # Claude 失败 → 任务退回 asr_done，由前端弹窗让用户决定
            task.status = TaskStatus.ASR_DONE
            store.update(task)
            await manager.broadcast(task_id, {
                "type": "claude_failed",
                "task_id": task_id,
                "error": error_msg,
            })
        else:
            task.status = TaskStatus.ERROR
            task.error_message = error_msg
            store.update(task)
            await log("error", "system", f"审计失败: {error_msg}")


# ============================================================
# Segment Review
# ============================================================

@app.put("/api/tasks/{task_id}/segments")
async def update_segments(task_id: str, req: UpdateSegmentsRequest):
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")

    task.audit_segments = req.segments
    task.update_stats()
    store.update(task)
    return {"success": True, "stats": {
        "kept": task.segments_kept,
        "deleted": task.segments_deleted,
        "edited_duration": task.edited_duration
    }}


@app.put("/api/tasks/{task_id}/segments/{segment_id}/toggle")
async def toggle_segment(task_id: str, segment_id: str):
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")

    for seg in task.audit_segments:
        if seg.id == segment_id:
            seg.action = (
                SegmentAction.KEEP
                if seg.action == SegmentAction.DELETE
                else SegmentAction.DELETE
            )
            seg.user_override = True
            if seg.action == SegmentAction.KEEP:
                seg.reason = "手动恢复"
            break

    task.update_stats()
    store.update(task)
    return {"success": True}


class PatchSegmentRequest(BaseModel):
    text: Optional[str] = None
    start: Optional[float] = None
    end: Optional[float] = None


@app.patch("/api/tasks/{task_id}/segments/{segment_id}")
async def patch_segment(task_id: str, segment_id: str, req: PatchSegmentRequest):
    """修改单个片段的文本或时间戳。"""
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")

    found = False
    for seg in task.audit_segments:
        if seg.id == segment_id:
            if req.text is not None:
                seg.text = req.text
                seg.words = []  # 文字人工改写，字级时间戳作废
            if req.start is not None:
                seg.start = req.start
            if req.end is not None:
                seg.end = req.end
            seg.user_override = True
            found = True
            break

    if not found:
        raise HTTPException(404, "Segment not found")

    task.update_stats()
    store.update(task)
    return {"success": True}


@app.delete("/api/tasks/{task_id}/segments/{segment_id}")
async def remove_segment(task_id: str, segment_id: str):
    """彻底删除单个片段（从列表中移除）。"""
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")

    original_len = len(task.audit_segments)
    task.audit_segments = [s for s in task.audit_segments if s.id != segment_id]
    if len(task.audit_segments) == original_len:
        raise HTTPException(404, "Segment not found")

    task.update_stats()
    store.update(task)
    return {"success": True}


@app.post("/api/tasks/{task_id}/segments/{segment_id}/merge-next")
async def merge_segment_next(task_id: str, segment_id: str):
    """将指定片段与其下一段合并：拼接文本/words、延伸时间边界、移除下一段。"""
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")

    segs = task.audit_segments
    idx = next((i for i, s in enumerate(segs) if s.id == segment_id), None)
    if idx is None:
        raise HTTPException(404, "Segment not found")
    if idx + 1 >= len(segs):
        raise HTTPException(400, "已是最后一段，无法合并")

    seg = segs[idx]
    next_seg = segs[idx + 1]

    seg.end = round(next_seg.end, 3)
    seg.text = (seg.text or "") + (next_seg.text or "")
    seg.tagged_text = (seg.tagged_text or "") + (next_seg.tagged_text or "")
    seg.words = list(seg.words) + list(next_seg.words)
    seg.user_override = True
    seg.reason = "手动合并"
    seg.merged = True

    # 移除下一段
    task.audit_segments = segs[:idx + 1] + segs[idx + 2:]

    task.update_stats()
    store.update(task)
    return {"success": True, "segment": seg.model_dump()}


class BatchSegmentRequest(BaseModel):
    segment_ids: List[str]
    action: str  # "keep" | "delete" | "remove"


@app.post("/api/tasks/{task_id}/segments/batch")
async def batch_segment_action(task_id: str, req: BatchSegmentRequest):
    """批量操作片段：标记保留/删除 或 彻底移除。"""
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")

    target_ids = set(req.segment_ids)

    if req.action == "remove":
        task.audit_segments = [s for s in task.audit_segments if s.id not in target_ids]
    elif req.action in ("keep", "delete"):
        new_action = SegmentAction.KEEP if req.action == "keep" else SegmentAction.DELETE
        for seg in task.audit_segments:
            if seg.id in target_ids:
                seg.action = new_action
                seg.user_override = True
                if new_action == SegmentAction.KEEP:
                    seg.reason = "手动恢复"
                else:
                    seg.reason = "手动删除"
                    seg.rule = "手动调整"
    else:
        raise HTTPException(400, f"Unknown action: {req.action}")

    task.update_stats()
    store.update(task)
    return {"success": True, "stats": {
        "kept": task.segments_kept,
        "deleted": task.segments_deleted,
        "edited_duration": task.edited_duration
    }}


# ============================================================
# Clip Range（裁剪区间）
# ============================================================

@app.patch("/api/tasks/{task_id}/clip")
async def update_clip_range(task_id: str, req: ClipRangeRequest):
    """
    设置视频裁剪区间（In/Out Point）。
    若已有 audit_segments，则对区间外的段自动标记为 delete（user_override=False）；
    放大区间时，原先因区间外被 auto-delete 的段自动恢复为 keep。
    """
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")

    old_clip_start = task.clip_start
    old_clip_end = task.clip_end

    task.clip_start = req.clip_start
    task.clip_end = req.clip_end

    clip_start = req.clip_start or 0.0
    clip_end = req.clip_end or (task.video_duration or float("inf"))

    if task.audit_segments:
        for seg in task.audit_segments:
            seg_out_of_range = seg.end <= clip_start or seg.start >= clip_end
            if seg_out_of_range:
                # 仅自动标记非用户手动操作的段
                if not seg.user_override:
                    seg.action = SegmentAction.DELETE
                    seg.reason = "区间外自动排除"
            else:
                # 区间内：若之前是因区间外被 auto-delete 的，恢复为 keep
                if (not seg.user_override) and seg.reason == "区间外自动排除":
                    seg.action = SegmentAction.KEEP
                    seg.reason = None

        task.update_stats()

    store.update(task)
    return {
        "success": True,
        "clip_start": task.clip_start,
        "clip_end": task.clip_end,
        "stats": {
            "kept": task.segments_kept,
            "deleted": task.segments_deleted,
            "edited_duration": task.edited_duration,
        }
    }


# ============================================================
# Export
# ============================================================

@app.post("/api/tasks/{task_id}/export/ffmpeg")
async def export_ffmpeg(task_id: str, req: ExportRequest):
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    if not task.audit_segments:
        raise HTTPException(400, "No segments to export")
    if not task.video_path or not os.path.exists(task.video_path):
        raise HTTPException(400, "Source video not found")

    t = asyncio.create_task(_run_ffmpeg_export(task_id, req))
    _export_tasks[task_id] = t
    return {"success": True, "message": "FFmpeg 导出已启动"}


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


@app.post("/api/tasks/{task_id}/export/clip-preview")
async def export_clip_preview(task_id: str, body: dict):
    """
    导出单个 clip 预览文件并返回可播放 URL。
    Body: { "segment_ids": ["seg_001", "seg_002"] }
    """
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    if not task.video_path or not os.path.exists(task.video_path):
        raise HTTPException(400, "Source video not found")

    requested_ids: List[str] = body.get("segment_ids", [])
    preview_segments = _build_clip_export_segments(task.audit_segments or [], requested_ids)
    if not preview_segments:
        raise HTTPException(400, "No segments found")

    import hashlib

    cache_parts = [
        f"{seg.id}:{seg.start:.3f}:{seg.end:.3f}:{seg.action.value}"
        for seg in preview_segments
    ]
    cache_key = hashlib.md5("|".join(cache_parts).encode("utf-8")).hexdigest()[:8]
    filename = f"preview_{task_id[:8]}_{cache_key}.mp4"
    output_path = str(EXPORTS_DIR / filename)

    if not os.path.exists(output_path):
        success = await export_ffmpeg_lossless(task.video_path, preview_segments, output_path)
        if not success:
            raise HTTPException(500, "FFmpeg export failed")

    return {"url": f"/api/exports/{filename}"}


@app.post("/api/tasks/{task_id}/derive-refine-task")
async def derive_refine_task(task_id: str, body: dict):
    """
    从一剪多的某个 clip 派生一个精修任务。
    无需重新上传视频，复用原视频文件，直接跳过 ASR 进入精修流程。
    Body: { "target_type": "monologue_clean"|"interview_compress",
            "clip_start": float, "clip_end": float, "clip_title": str }
    """
    store = get_store()
    source = store.get(task_id)
    if not source:
        raise HTTPException(404, "Source task not found")
    if source.task_type != TaskType.HIGHLIGHT_REEL:
        raise HTTPException(400, "Only highlight_reel tasks can derive refine tasks")
    if not source.asr_result:
        raise HTTPException(400, "Source task has no ASR result")

    target_type_str: str = body.get("target_type", "monologue_clean")
    clip_start: float = float(body.get("clip_start", 0.0))
    clip_end: float = float(body.get("clip_end", source.video_duration or 0.0))
    clip_title: str = body.get("clip_title", "")

    try:
        target_type = TaskType(target_type_str)
    except ValueError:
        raise HTTPException(400, f"Invalid target_type: {target_type_str}")

    # 纳入原视频所有 ASR 段：clip 范围内默认 keep，范围外默认 delete
    # 用户可在精修任务中手动调整，增加容错率
    margin = 0.1
    all_segs = []
    for s in source.asr_result.segments:
        seg = s.model_copy(deep=True)
        in_clip = seg.start >= clip_start - margin and seg.end <= clip_end + margin
        seg.action = SegmentAction.KEEP if in_clip else SegmentAction.DELETE
        seg.reason = None
        seg.rule = None
        seg.user_override = False
        seg.claude_action = None
        seg.claude_reason = None
        seg.clip_group = None
        seg.clip_title = None
        all_segs.append(seg)

    new_asr = ASRResult(
        words=list(source.asr_result.words or []),
        segments=all_segs,
        tagged_script="\n".join(s.tagged_text or s.text for s in all_segs),
        duration=source.asr_result.duration,
        language=source.asr_result.language,
        speakers=source.asr_result.speakers,
        preprocess_stats=source.asr_result.preprocess_stats,
    )

    type_label = "口播精修" if target_type == TaskType.MONOLOGUE_CLEAN else "访谈精修"
    new_name = f"{clip_title or source.name} · {type_label}" if clip_title else f"{source.name} · {type_label}"

    new_task = Task(
        name=new_name,
        task_type=target_type,
        status=TaskStatus.ASR_DONE,
        video_path=source.video_path,
        video_filename=source.video_filename,
        video_duration=source.video_duration,
        thumbnail_path=source.thumbnail_path,
        clip_start=clip_start,
        clip_end=clip_end,
        asr_result=new_asr,
        audit_segments=[s.model_copy(deep=True) for s in all_segs],
        source_task_id=task_id,
        clip_title=clip_title or None,
        params=source.params.model_copy(deep=True),
    )
    store.create(new_task)
    return {"task_id": new_task.id}


@app.post("/api/tasks/{task_id}/generate-cover-image")
async def generate_cover_image(task_id: str, body: dict):
    """
    用 AI 生成封面图片。
    OpenRouter key (sk-or-*): 通过 chat/completions + modalities 传入视频帧 + 文字 prompt，单步生成封面。
    直接 OpenAI key + gpt-image-1: 用 images.edit（img2img）。
    Body: {
      "frame_time": float,
      "cover_title": str,       // 必填
      "style_prompt": str,
      "img_provider": str,      // 任意 OpenRouter 模型 ID，如 "openai/gpt-image-1"
      "api_key": str,
    }
    Returns: { "image_base64": "data:image/png;base64,..." }
    """
    import asyncio as _asyncio, base64 as _base64, os as _os, subprocess as _sp, tempfile as _tf, json as _json
    from urllib import request as _ur

    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    if not task.video_path or not _os.path.exists(task.video_path):
        raise HTTPException(400, "Source video not found")

    cover_title: str = body.get("cover_title", "").strip()
    if not cover_title:
        raise HTTPException(400, "请先填写封面标题")

    frame_time: float = float(body.get("frame_time", 0.0))
    style_prompt: str = body.get("style_prompt", "")
    img_provider: str = body.get("img_provider", "openai/gpt-image-1").strip()
    api_key: str = body.get("api_key", "") or _os.environ.get("OPENAI_API_KEY") or _os.environ.get("OPENROUTER_API_KEY") or ""
    if not api_key:
        raise HTTPException(400, "未配置 API Key")

    # 提取视频帧
    with _tf.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        frame_path = tmp.name
    try:
        _sp.run(
            ["ffmpeg", "-y", "-ss", str(frame_time), "-i", task.video_path,
             "-frames:v", "1", "-q:v", "2", frame_path],
            capture_output=True, timeout=15,
        )
        if not _os.path.exists(frame_path) or _os.path.getsize(frame_path) == 0:
            raise HTTPException(500, "帧提取失败")

        with open(frame_path, "rb") as _f:
            frame_b64 = _base64.b64encode(_f.read()).decode()
        frame_data_url = f"data:image/png;base64,{frame_b64}"

        _cp = _load_cover_prompts()
        prompt_text = (
            _cp["cover_image"]["user_prompt"]
            .replace("{cover_title}", cover_title)
            .replace("{style_prompt}", style_prompt or "简洁现代，适合小红书平台")
        )

        use_or = api_key.startswith("sk-or-")

        if not use_or:
            # 直接 OpenAI key：gpt-image-1 支持图片编辑（img2img）
            try:
                import openai as _openai
            except ImportError:
                import subprocess as _sp2, sys as _sys
                _sp2.check_call([_sys.executable, "-m", "pip", "install", "openai", "-q"])
                import openai as _openai
            client = _openai.OpenAI(api_key=api_key)
            model_name = img_provider.split("/")[-1] if "/" in img_provider else img_provider
            def _call_edit():
                with open(frame_path, "rb") as f:
                    return client.images.edit(
                        model=model_name,
                        image=f,
                        prompt=prompt_text,
                        size="1024x1024",
                    )
            resp = await _asyncio.to_thread(_call_edit)
            img_data = resp.data[0]
            if hasattr(img_data, "b64_json") and img_data.b64_json:
                b64 = img_data.b64_json
            elif hasattr(img_data, "url") and img_data.url:
                def _dl():
                    with _ur.urlopen(img_data.url, timeout=60) as r:
                        return _base64.b64encode(r.read()).decode()
                b64 = await _asyncio.to_thread(_dl)
            else:
                raise HTTPException(500, "图片生成接口未返回图片数据")
            return {"image_base64": f"data:image/png;base64,{b64}"}

        # OpenRouter key：chat/completions + modalities，单步传图 + 生成
        payload = {
            "model": img_provider,
            "modalities": ["image", "text"],
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": frame_data_url}
                        },
                        {
                            "type": "text",
                            "text": prompt_text
                        }
                    ]
                }
            ]
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/zhangyang1014/JianyingVideoCut",
            "X-Title": "GoldenClip",
        }

        def _call_or():
            from urllib.error import HTTPError as _HTTPError
            req = _ur.Request(
                "https://openrouter.ai/api/v1/chat/completions",
                data=_json.dumps(payload).encode(),
                headers=headers,
                method="POST",
            )
            try:
                with _ur.urlopen(req, timeout=120) as r:
                    return _json.loads(r.read().decode())
            except _HTTPError as e:
                body = e.read().decode(errors="replace")
                raise RuntimeError(f"OpenRouter {e.code}: {body}")

        result = await _asyncio.to_thread(_call_or)

        # 从响应中提取图片（兼容多种返回格式）
        choice = result.get("choices", [{}])[0]
        msg = choice.get("message", {})
        content = msg.get("content", "")
        b64 = None

        def _extract_img_url(url_str):
            if url_str.startswith("data:"):
                return url_str.split(",", 1)[1]
            return None  # 需要下载的 URL 另行处理

        # 格式1: message.images（Gemini 返回格式）
        for img_part in msg.get("images", []):
            url_str = img_part.get("image_url", {}).get("url", "")
            if url_str:
                if url_str.startswith("data:"):
                    b64 = url_str.split(",", 1)[1]
                else:
                    def _dl_img(u=url_str):
                        with _ur.urlopen(u, timeout=60) as r:
                            return _base64.b64encode(r.read()).decode()
                    b64 = await _asyncio.to_thread(_dl_img)
                break

        # 格式2: message.content 数组（标准 OpenAI image_url part）
        if not b64 and isinstance(content, list):
            for part in content:
                if part.get("type") == "image_url":
                    url_str = part["image_url"]["url"]
                    if url_str.startswith("data:"):
                        b64 = url_str.split(",", 1)[1]
                    else:
                        def _dl2(u=url_str):
                            with _ur.urlopen(u, timeout=60) as r:
                                return _base64.b64encode(r.read()).decode()
                        b64 = await _asyncio.to_thread(_dl2)
                    break

        # 格式3: message.content 是 data URL 字符串
        if not b64 and isinstance(content, str) and content.startswith("data:"):
            b64 = content.split(",", 1)[1]

        if not b64:
            # 格式4: 顶层 data[].url
            img_url = result.get("data", [{}])[0].get("url", "") if "data" in result else ""
            if img_url:
                def _dl3(u=img_url):
                    with _ur.urlopen(u, timeout=60) as r:
                        return _base64.b64encode(r.read()).decode()
                b64 = await _asyncio.to_thread(_dl3)

        if not b64:
            raw_preview = str(result)[:500]
            raise HTTPException(500, f"图片生成接口未返回图片数据。响应预览：{raw_preview}")

        return {"image_base64": f"data:image/png;base64,{b64}"}
    except HTTPException:
        raise
    except Exception as e:
        import traceback as _tb, logging as _log
        _log.getLogger(__name__).error("generate-cover-image 异常:\n%s", _tb.format_exc())
        raise HTTPException(500, f"封面生成失败：{type(e).__name__}: {e}")
    finally:
        try:
            _os.unlink(frame_path)
        except Exception:
            pass


@app.post("/api/tasks/{task_id}/generate-cover-titles")
async def generate_cover_titles(task_id: str, body: dict):
    """
    根据视频内容生成3个封面标题备选（10字以内，制造好奇）。
    Body: { "api_key": str, "provider": "claude"|"ollama",
            "claude_model": str, "ollama_model": str, "ollama_base_url": str }
    """
    import asyncio as _asyncio
    import re as _re
    import os as _os

    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")

    api_key: str = body.get("api_key", "")
    provider: str = body.get("provider", "claude")
    claude_model: str = body.get("claude_model", "anthropic/claude-3.5-sonnet")
    ollama_model: str = body.get("ollama_model", "deepseek-r1:14b")
    ollama_base_url: str = body.get("ollama_base_url", "http://localhost:11434")

    from backend.models.task import SegmentAction
    keep_segs = [s for s in (task.audit_segments or []) if s.action == SegmentAction.KEEP]
    segments_text = " ".join(s.text for s in keep_segs)
    if len(segments_text) > 1500:
        segments_text = segments_text[:1500] + "…"

    _cp = _load_cover_prompts()
    system_prompt = _cp["cover_titles"]["system_prompt"]
    user_prompt = _cp["cover_titles"]["user_prompt"].replace("{segments_text}", segments_text or "（暂无内容）")

    async def _call_llm(sys_p: str, usr_p: str) -> str:
        if provider == "ollama":
            try:
                import openai as _openai
            except ImportError:
                import subprocess as _sp, sys as _sys
                _sp.check_call([_sys.executable, "-m", "pip", "install", "openai", "-q"])
                import openai as _openai
            import httpx as _httpx
            _client = _openai.OpenAI(
                api_key="ollama",
                base_url=f"{ollama_base_url.rstrip('/')}/v1",
                http_client=_httpx.Client(transport=_httpx.HTTPTransport(proxy=None)),
            )
            def _call():
                return _client.chat.completions.create(
                    model=ollama_model, max_tokens=256,
                    messages=[{"role": "system", "content": sys_p}, {"role": "user", "content": usr_p}],
                )
            comp = await _asyncio.to_thread(_call)
            return comp.choices[0].message.content or ""
        else:
            key = api_key or _os.environ.get("OPENROUTER_API_KEY") or _os.environ.get("ANTHROPIC_API_KEY") or ""
            if not key:
                raise HTTPException(400, "未配置 API Key，请在设置中填写")
            use_openrouter = key.startswith("sk-or-") or bool(_os.environ.get("OPENROUTER_API_KEY"))
            if use_openrouter:
                try:
                    import openai as _openai
                except ImportError:
                    import subprocess as _sp, sys as _sys
                    _sp.check_call([_sys.executable, "-m", "pip", "install", "openai", "-q"])
                    import openai as _openai
                _model_map = {
                    "claude-3-7-sonnet-20250219": "anthropic/claude-3.7-sonnet",
                    "claude-3-5-sonnet-20241022": "anthropic/claude-3.5-sonnet",
                    "claude-3-5-haiku-20241022":  "anthropic/claude-3.5-haiku",
                }
                or_model = _model_map.get(claude_model, claude_model if "/" in claude_model else f"anthropic/{claude_model}")
                _client = _openai.OpenAI(
                    api_key=key, base_url="https://openrouter.ai/api/v1",
                    default_headers={"HTTP-Referer": "https://github.com/zhangyang1014/JianyingVideoCut", "X-Title": "GoldenClip Video Workstation"},
                )
                def _call():
                    return _client.chat.completions.create(
                        model=or_model, max_tokens=256,
                        messages=[{"role": "system", "content": sys_p}, {"role": "user", "content": usr_p}],
                    )
                comp = await _asyncio.to_thread(_call)
                return comp.choices[0].message.content or ""
            else:
                try:
                    import anthropic as _anthropic
                except ImportError:
                    import subprocess as _sp, sys as _sys
                    _sp.check_call([_sys.executable, "-m", "pip", "install", "anthropic", "-q"])
                    import anthropic as _anthropic
                _client = _anthropic.Anthropic(api_key=key)
                def _call():
                    return _client.messages.create(
                        model=claude_model, max_tokens=256, system=sys_p,
                        messages=[{"role": "user", "content": usr_p}],
                    )
                msg = await _asyncio.to_thread(_call)
                return msg.content[0].text

    raw = await _call_llm(system_prompt, user_prompt)

    # 解析 "1. xxx\n2. xxx\n3. xxx"
    titles = _re.findall(r"^[1-3][\.、\)]\s*(.+)$", raw, _re.MULTILINE)
    titles = [t.strip().strip("。，、") for t in titles[:3]]
    if not titles:
        # 兜底：按行分割取前3行
        titles = [l.strip() for l in raw.strip().splitlines() if l.strip()][:3]

    return {"cover_titles": titles}


@app.post("/api/tasks/{task_id}/generate-xhs-content")
async def generate_xhs_content(task_id: str, body: dict):
    """
    v2: 返回 xhs_titles 数组（3个标题）+ xhs_description。
    Body: { "cover_title": str, "api_key": str, "provider": "claude"|"ollama",
            "claude_model": str, "ollama_model": str, "ollama_base_url": str }
    """
    import asyncio as _asyncio
    import re as _re
    import os as _os

    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")

    cover_title: str = body.get("cover_title", "").strip()
    api_key: str = body.get("api_key", "")
    provider: str = body.get("provider", "claude")
    claude_model: str = body.get("claude_model", "anthropic/claude-3.5-sonnet")
    ollama_model: str = body.get("ollama_model", "deepseek-r1:14b")
    ollama_base_url: str = body.get("ollama_base_url", "http://localhost:11434")

    # 取 KEEP 状态的 segment 拼接文字（限 1500 字）
    from backend.models.task import SegmentAction
    keep_segs = [s for s in (task.audit_segments or []) if s.action == SegmentAction.KEEP]
    segments_text = " ".join(s.text for s in keep_segs)
    if len(segments_text) > 1500:
        segments_text = segments_text[:1500] + "…"

    _cp = _load_cover_prompts()
    system_prompt = _cp["xhs_content"]["system_prompt"]
    user_prompt = _cp["xhs_content"]["user_prompt"].replace("{cover_title}", cover_title or "（未填写）")

    async def call_llm_simple(sys_p: str, usr_p: str) -> str:
        if provider == "ollama":
            try:
                import openai as _openai
            except ImportError:
                import subprocess as _sp, sys as _sys
                _sp.check_call([_sys.executable, "-m", "pip", "install", "openai", "-q"])
                import openai as _openai
            import httpx as _httpx
            _transport = _httpx.HTTPTransport(proxy=None)
            _http_client = _httpx.Client(transport=_transport)
            _client = _openai.OpenAI(
                api_key="ollama",
                base_url=f"{ollama_base_url.rstrip('/')}/v1",
                http_client=_http_client,
            )
            def _call():
                return _client.chat.completions.create(
                    model=ollama_model,
                    max_tokens=512,
                    messages=[{"role": "system", "content": sys_p}, {"role": "user", "content": usr_p}],
                )
            comp = await _asyncio.to_thread(_call)
            return comp.choices[0].message.content or ""
        else:
            key = api_key or _os.environ.get("OPENROUTER_API_KEY") or _os.environ.get("ANTHROPIC_API_KEY") or ""
            if not key:
                raise HTTPException(400, "未配置 API Key，请在设置中填写")
            use_openrouter = key.startswith("sk-or-") or bool(_os.environ.get("OPENROUTER_API_KEY"))
            if use_openrouter:
                try:
                    import openai as _openai
                except ImportError:
                    import subprocess as _sp, sys as _sys
                    _sp.check_call([_sys.executable, "-m", "pip", "install", "openai", "-q"])
                    import openai as _openai
                _model_map = {
                    "claude-3-7-sonnet-20250219": "anthropic/claude-3.7-sonnet",
                    "claude-3-5-sonnet-20241022": "anthropic/claude-3.5-sonnet",
                    "claude-3-5-haiku-20241022":  "anthropic/claude-3.5-haiku",
                }
                or_model = _model_map.get(claude_model, claude_model if "/" in claude_model else f"anthropic/{claude_model}")
                _client = _openai.OpenAI(
                    api_key=key,
                    base_url="https://openrouter.ai/api/v1",
                    default_headers={"HTTP-Referer": "https://github.com/zhangyang1014/JianyingVideoCut", "X-Title": "GoldenClip Video Workstation"},
                )
                def _call():
                    return _client.chat.completions.create(
                        model=or_model, max_tokens=512,
                        messages=[{"role": "system", "content": sys_p}, {"role": "user", "content": usr_p}],
                    )
                comp = await _asyncio.to_thread(_call)
                return comp.choices[0].message.content or ""
            else:
                try:
                    import anthropic as _anthropic
                except ImportError:
                    import subprocess as _sp, sys as _sys
                    _sp.check_call([_sys.executable, "-m", "pip", "install", "anthropic", "-q"])
                    import anthropic as _anthropic
                _client = _anthropic.Anthropic(api_key=key)
                def _call():
                    return _client.messages.create(
                        model=claude_model, max_tokens=512, system=sys_p,
                        messages=[{"role": "user", "content": usr_p}],
                    )
                msg = await _asyncio.to_thread(_call)
                return msg.content[0].text

    raw = await call_llm_simple(system_prompt, user_prompt)


    # 解析标题：兼容 "标题1/一/①：" 和 "1. xxx" 有序列表两种格式
    titles: list = []

    # 格式一：标题1/标题一/标题①：xxx（LLM 明确标注标题编号）
    labeled = _re.findall(r"标题[1-3一二三①②③][\.、：:。]?\s*[：:]?\s*\*{0,2}(.+?)\*{0,2}\s*$", raw, _re.MULTILINE)
    if labeled:
        titles = [t.strip() for t in labeled[:3]]

    # 格式二：仅 "标题：xxx"（单条，兼容旧 prompt）
    if not titles:
        single = _re.findall(r"^标题[：:]\s*(.+)$", raw, _re.MULTILINE)
        if single:
            titles = [t.strip() for t in single[:3]]

    # 格式三：有序列表 "1. xxx" / "1、xxx"
    if not titles:
        listed = _re.findall(r"^[1-3][\.、]\s*\*{0,2}(.+?)\*{0,2}\s*$", raw, _re.MULTILINE)
        if listed:
            titles = [t.strip() for t in listed[:3]]

    # 解析简介
    m_desc = _re.search(r"简介[：:]\s*([\s\S]+?)(?=\n标题|\Z)", raw)
    xhs_description = m_desc.group(1).strip() if m_desc else ""

    if not titles and not xhs_description:
        titles = [raw.strip()]

    return {"xhs_titles": titles, "xhs_description": xhs_description}


@app.post("/api/tasks/{task_id}/export/highlight-clips")
async def export_highlight_clips(task_id: str, body: dict):
    """
    批量导出精彩集锦 clip。
    Body: { "clip_groups": [["seg_id1", "seg_id2"], ["seg_id3"]], "output_name": "..." }
    """
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    if not task.audit_segments:
        raise HTTPException(400, "No segments to export")
    if not task.video_path or not os.path.exists(task.video_path):
        raise HTTPException(400, "Source video not found")

    clip_group_ids: List[List[str]] = body.get("clip_groups", [])
    output_name: str = body.get("output_name") or task.name
    burn_subtitles: bool = bool(body.get("burn_subtitles", False))
    subtitle_style_data = body.get("subtitle_style")
    subtitle_style: Optional[SubtitleStyle] = SubtitleStyle(**subtitle_style_data) if subtitle_style_data else None
    hook_configs_data = body.get("hook_configs") or []
    hook_configs: List[Optional[HookConfig]] = [
        HookConfig(**h) if h else None for h in hook_configs_data
    ]
    hook_orders_raw = body.get("hook_orders") or []
    hook_orders: List[Optional[List[str]]] = [
        (h if h else None) for h in hook_orders_raw
    ]
    if not clip_group_ids:
        raise HTTPException(400, "clip_groups is required")

    clip_groups_segments: List[List[Segment]] = []
    for group_ids in clip_group_ids:
        group_segments = _build_clip_export_segments(task.audit_segments, group_ids)
        if group_segments:
            clip_groups_segments.append(group_segments)

    if not clip_groups_segments:
        raise HTTPException(400, "No valid segments found in clip_groups")

    # hook_orders 长度可能因 clip 过滤而与 clip_groups_segments 不对齐，需对齐
    aligned_hook_orders: List[Optional[List[str]]] = []
    valid_idx = 0
    for i, group_ids in enumerate(clip_group_ids):
        if _build_clip_export_segments(task.audit_segments, group_ids):
            aligned_hook_orders.append(hook_orders[i] if i < len(hook_orders) else None)

    t = asyncio.create_task(_run_highlight_clips_export(
        task_id, clip_groups_segments, output_name, burn_subtitles, subtitle_style, hook_configs, aligned_hook_orders
    ))
    _export_tasks[task_id] = t
    return {"success": True, "message": f"多片段导出已启动，共 {len(clip_groups_segments)} 个片段"}


async def _run_ffmpeg_export(task_id: str, req: ExportRequest):
    store = get_store()
    task = store.get(task_id)
    if not task:
        return

    task.status = TaskStatus.EXPORT_RUNNING
    store.update(task)
    await manager.broadcast(task_id, {"type": "status_change", "task_id": task_id, "status": "export_running"})

    async def log(level, source, msg, progress=None):
        await log_to_ws(task_id, level, source, msg, progress)

    try:
        output_name = req.output_name or f"{task.name}_output"
        output_path = str(EXPORTS_DIR / f"{output_name}.mp4")

        cover_len = len(req.cover_image_base64) if req.cover_image_base64 else 0
        await log("info", "ffmpeg", f"封面参数: cover_image_base64 长度={cover_len}, cover_duration={req.cover_duration}")

        # 金句开场顺序（若已设置）
        golden_quote_order = task.golden_quote_order or []

        # 始终生成 SRT 字幕文件（软字幕）
        srt_path = str(EXPORTS_DIR / f"{output_name}.srt")
        _vinfo = get_video_info(task.video_path)
        generate_srt(
            segments=task.audit_segments,
            srt_path=srt_path,
            params=task.params,
            subtitle_style=req.subtitle_style,
            golden_quote_order=golden_quote_order if golden_quote_order else None,
            video_width=_vinfo.get("width", 0),
        )
        await log("info", "ffmpeg", f"SRT 字幕已生成: {srt_path}")

        success = await export_ffmpeg_lossless(
            video_path=task.video_path,
            segments=task.audit_segments,
            output_path=output_path,
            log_callback=log,
            burn_subtitles=req.burn_subtitles,
            srt_path=srt_path,
            task_id=task_id,
            subtitle_style=req.subtitle_style,
            golden_quote_order=golden_quote_order if golden_quote_order else None,
            cover_image_base64=req.cover_image_base64,
            cover_duration=req.cover_duration,
        )

        if success:
            task.export_path = output_path
            task.status = TaskStatus.DONE
            store.update(task)
            await manager.broadcast(task_id, {
                "type": "export_done",
                "task_id": task_id,
                "output_path": output_path,
                "srt_path": srt_path,
                "mode": "ffmpeg"
            })
        else:
            task.status = TaskStatus.ERROR
            store.update(task)

    except asyncio.CancelledError:
        # 导出被用户主动终止，回滚状态为 review（保留已有审计结果）
        task_fresh = store.get(task_id)
        if task_fresh:
            task_fresh.status = TaskStatus.REVIEW
            store.update(task_fresh)
        # 向控制台推送一条醒目的终止日志
        await log_to_ws(task_id, "warn", "ffmpeg", "⚠️ 导出已被用户提前终止，状态已回滚至「待剪辑审核」")
        await manager.broadcast(task_id, {
            "type": "export_cancelled",
            "task_id": task_id,
            "status": "review",
        })
        await manager.broadcast(task_id, {"type": "status_change", "task_id": task_id, "status": "review"})
        # 不再重新抛出，让 asyncio 正常回收 Task

    except Exception as e:
        task.status = TaskStatus.ERROR
        task.error_message = str(e)
        store.update(task)
        await log("error", "ffmpeg", f"导出失败: {str(e)}")

    finally:
        # 无论何种结束方式，都清理 asyncio Task 注册表
        _export_tasks.pop(task_id, None)


async def _run_highlight_clips_export(task_id: str, clip_groups_segs: List[List[Segment]], output_name: str, burn_subtitles: bool = False, subtitle_style: Optional[SubtitleStyle] = None, hook_configs: Optional[List[Optional[HookConfig]]] = None, hook_orders: Optional[List[Optional[List[str]]]] = None):
    store = get_store()
    task = store.get(task_id)
    if not task:
        return

    task.status = TaskStatus.EXPORT_RUNNING
    store.update(task)
    await manager.broadcast(task_id, {"type": "status_change", "task_id": task_id, "status": "export_running"})

    async def log(level, source, msg, progress=None):
        await log_to_ws(task_id, level, source, msg, progress)

    try:
        output_dir = str(EXPORTS_DIR / output_name)
        output_paths = await export_ffmpeg_multi_clips(
            video_path=task.video_path or "",
            clip_groups=clip_groups_segs,
            output_dir=output_dir,
            base_name=output_name,
            log_callback=log,
            task_id=task_id,
            burn_subtitles=burn_subtitles,
            params=task.params,
            subtitle_style=subtitle_style,
            hook_configs=hook_configs,
            hook_orders=hook_orders,
        )

        if output_paths:
            task.export_path = output_dir
            task.status = TaskStatus.DONE
            store.update(task)
            await manager.broadcast(task_id, {
                "type": "export_done",
                "task_id": task_id,
                "output_path": output_dir,
                "output_files": output_paths,
                "mode": "highlight_clips",
                "clips_count": len(output_paths),
            })
        else:
            task.status = TaskStatus.ERROR
            store.update(task)

    except asyncio.CancelledError:
        task_fresh = store.get(task_id)
        if task_fresh:
            task_fresh.status = TaskStatus.REVIEW
            store.update(task_fresh)
        await log_to_ws(task_id, "warn", "ffmpeg", "⚠️ 多片段导出已被用户提前终止，状态已回滚至「待剪辑审核」")
        await manager.broadcast(task_id, {
            "type": "export_cancelled",
            "task_id": task_id,
            "status": "review",
        })
        await manager.broadcast(task_id, {"type": "status_change", "task_id": task_id, "status": "review"})

    except Exception as e:
        task.status = TaskStatus.ERROR
        task.error_message = str(e)
        store.update(task)
        await log("error", "ffmpeg", f"多片段导出失败: {str(e)}")

    finally:
        _export_tasks.pop(task_id, None)


@app.post("/api/tasks/{task_id}/export/cancel")
async def cancel_export(task_id: str):
    """终止正在进行的 FFmpeg 导出任务。"""
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    if task.status != TaskStatus.EXPORT_RUNNING:
        raise HTTPException(400, "No export running for this task")

    # 1. 终止 FFmpeg 子进程
    kill_export(task_id)

    # 2. 取消 asyncio Task（触发 CancelledError，由 _run_ffmpeg_export 内部处理清理逻辑）
    t = _export_tasks.pop(task_id, None)
    if t and not t.done():
        # 正常路径：asyncio Task 找到且未完成，取消它；
        # except CancelledError 块负责 WS 广播和状态回滚
        t.cancel()
    else:
        # 兜底路径：Task 不在注册表（竞态/重启场景），
        # 直接在此完成状态回滚和 WS 广播，保证前端能收到通知
        task.status = TaskStatus.REVIEW
        store.update(task)
        await log_to_ws(task_id, "warn", "ffmpeg", "⚠️ 导出已被用户提前终止，状态已回滚至「待剪辑审核」")
        await manager.broadcast(task_id, {
            "type": "export_cancelled",
            "task_id": task_id,
            "status": "review",
        })
        await manager.broadcast(task_id, {"type": "status_change", "task_id": task_id, "status": "review"})

    return {"success": True, "message": "导出已终止"}


# ============================================================
# 金句开场
# ============================================================

class GoldenQuoteSuggestRequest(BaseModel):
    """金句分析请求参数，复用 AuditRequest 的 AI 配置结构。"""
    provider: str = "claude"              # "claude" | "ollama"
    claude_api_key: Optional[str] = None
    claude_model: str = "claude-3-5-haiku-20241022"
    use_openrouter: bool = False
    ollama_model: str = "deepseek-r1:14b"
    ollama_base_url: str = "http://localhost:11434"
    # 可选：只在指定 segment 范围内分析（用于 per-clip hook）
    segment_ids: List[str] = []


class GoldenQuoteOrderRequest(BaseModel):
    """保存用户选定并排序后的金句片段 ID 列表。"""
    segment_ids: List[str]


class ClipHookOrdersRequest(BaseModel):
    """保存一剪多各 clip 的 hook segment 配置。"""
    # {clip首段segment_id → 有序hook_segment_id列表}，空列表表示清除该 clip 的 hook
    clip_hooks: Dict[str, List[str]]


@app.post("/api/tasks/{task_id}/golden-quotes/suggest")
async def suggest_golden_quotes_api(task_id: str, req: GoldenQuoteSuggestRequest):
    """
    调用 AI 分析当前任务的保留片段，推荐最多 5 个适合做开场钩子的金句候选。
    同步接口，约 5-15 秒返回。
    """
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    if not task.audit_segments:
        raise HTTPException(400, "暂无审计片段，请先完成 ASR 识别和语义审计")

    async def log(level, source, msg, progress=None):
        await log_to_ws(task_id, level, source, msg, progress)

    try:
        # 若指定了 segment_ids，只在该范围内分析（per-clip hook 场景）
        segments = task.audit_segments
        if req.segment_ids:
            id_set = set(req.segment_ids)
            segments = [s for s in segments if s.id in id_set]

        candidates = await suggest_golden_quotes(
            segments=segments,
            provider=req.provider,
            api_key=req.claude_api_key,
            model=req.claude_model,
            use_openrouter=req.use_openrouter,
            ollama_model=req.ollama_model,
            ollama_base_url=req.ollama_base_url,
            log_callback=log,
            task_type=task.task_type,
        )
        return {"success": True, "candidates": candidates}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"金句分析失败: {str(e)}")


@app.put("/api/tasks/{task_id}/golden-quotes/order")
async def save_golden_quote_order(task_id: str, req: GoldenQuoteOrderRequest):
    """
    保存用户选定并排序后的金句片段 ID 列表。
    导出时这些片段将被放在视频最开头。
    传空列表表示清除金句开场设置。
    """
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")

    # 校验 segment_ids 是否在当前 audit_segments 中
    valid_ids = {s.id for s in task.audit_segments}
    invalid = [sid for sid in req.segment_ids if sid not in valid_ids]
    if invalid:
        raise HTTPException(400, f"以下 segment_id 不存在: {invalid}")

    task.golden_quote_order = req.segment_ids
    task.updated_at = datetime.now().isoformat()
    store.update(task)

    return {
        "success": True,
        "golden_quote_order": task.golden_quote_order,
        "count": len(task.golden_quote_order),
    }


@app.put("/api/tasks/{task_id}/clip-hooks")
async def save_clip_hook_orders(task_id: str, req: ClipHookOrdersRequest):
    """
    保存一剪多各 clip 的 per-clip hook segment 配置。
    clip_hooks: {clip首段segment_id → 有序hook_segment_id列表}
    传空列表表示清除该 clip 的 hook。
    """
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")

    valid_ids = {s.id for s in task.audit_segments}
    for clip_key, seg_ids in req.clip_hooks.items():
        invalid = [sid for sid in seg_ids if sid not in valid_ids]
        if invalid:
            raise HTTPException(400, f"clip {clip_key} 含无效 segment_id: {invalid}")

    # 合并更新（只覆盖传入的 clip，不影响其他 clip）
    updated = dict(task.clip_hook_orders or {})
    for clip_key, seg_ids in req.clip_hooks.items():
        if seg_ids:
            updated[clip_key] = seg_ids
        else:
            updated.pop(clip_key, None)

    task.clip_hook_orders = updated
    task.updated_at = datetime.now().isoformat()
    store.update(task)

    return {
        "success": True,
        "clip_hook_orders": task.clip_hook_orders,
    }


@app.post("/api/tasks/{task_id}/export/jianying")
async def export_jianying(task_id: str, req: ExportRequest):
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    if not task.audit_segments:
        raise HTTPException(400, "No segments to export")

    asyncio.create_task(_run_jianying_export(task_id, req))
    return {"success": True, "message": "剪映草稿生成已启动"}


async def _run_jianying_export(task_id: str, req: ExportRequest):
    store = get_store()
    task = store.get(task_id)
    if not task:
        return

    task.status = TaskStatus.EXPORT_RUNNING
    store.update(task)

    async def log(level, source, msg, progress=None):
        await log_to_ws(task_id, level, source, msg, progress)

    try:
        draft_name = req.output_name or f"{task.name}_draft"
        draft_path = await build_jianying_draft(
            video_path=task.video_path or "",
            segments=task.audit_segments,
            draft_name=draft_name,
            draft_folder=req.jianying_draft_folder,
            log_callback=log,
            params=task.params,
        )

        if draft_path:
            task.jianying_draft_path = draft_path
            task.status = TaskStatus.DONE
            store.update(task)
            await manager.broadcast(task_id, {
                "type": "export_done",
                "task_id": task_id,
                "draft_path": draft_path,
                "mode": "jianying"
            })
        else:
            task.status = TaskStatus.ERROR
            store.update(task)

    except Exception as e:
        task.status = TaskStatus.ERROR
        task.error_message = str(e)
        store.update(task)
        await log("error", "jianying", f"草稿生成失败: {str(e)}")


# ============================================================
# Prompt Feedback (提示词反馈优化)
# ============================================================

@app.get("/api/tasks/{task_id}/corrections")
async def get_corrections(task_id: str):
    """获取用户修改汇总统计（对比 claude_action 与当前 action）。"""
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    corrections = collect_corrections(task.audit_segments or [])
    return corrections


class PromptFeedbackRequest(BaseModel):
    claude_api_key: Optional[str] = None
    claude_model: str = "claude-3-5-sonnet-20241022"
    user_notes: str = ""


@app.post("/api/tasks/{task_id}/prompt-feedback")
async def submit_prompt_feedback(task_id: str, req: PromptFeedbackRequest):
    """将用户修改提交给 Claude 分析，返回提示词改进建议。"""
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")

    try:
        result = await generate_prompt_improvement(
            task=task,
            api_key=req.claude_api_key,
            model=req.claude_model,
            user_notes=req.user_notes,
        )
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"生成改进建议失败: {str(e)}")


class AdoptStyleRequest(BaseModel):
    style_update: Dict[str, str]


@app.post("/api/tasks/{task_id}/adopt-style")
async def adopt_style(task_id: str, req: AdoptStyleRequest):
    """将 Claude 学习到的个人风格偏好合并写入 user_style.md。"""
    try:
        content = merge_user_style(req.style_update)
        return {"success": True, "content": content}
    except Exception as e:
        raise HTTPException(500, f"写入风格档案失败: {str(e)}")


# ============================================================
# Config Management
# ============================================================

@app.get("/api/config/editing-aesthetic")
async def get_editing_aesthetic():
    config_path = BASE_DIR / "Editing_Aesthetic.md"
    if config_path.exists():
        return {"content": config_path.read_text(encoding="utf-8")}
    return {"content": ""}


@app.put("/api/config/editing-aesthetic")
async def update_editing_aesthetic(body: dict):
    config_path = BASE_DIR / "Editing_Aesthetic.md"
    content = body.get("content", "")
    config_path.write_text(content, encoding="utf-8")
    return {"success": True}


@app.get("/api/config/user-style")
async def get_user_style():
    """获取个人剪辑风格档案。"""
    path = BASE_DIR / "backend" / "data" / "user_style.md"
    if path.exists():
        return {"content": path.read_text(encoding="utf-8")}
    return {"content": ""}


@app.put("/api/config/user-style")
async def update_user_style_api(body: dict):
    """更新个人剪辑风格档案。"""
    path = BASE_DIR / "backend" / "data" / "user_style.md"
    content = body.get("content", "")
    path.write_text(content, encoding="utf-8")
    return {"success": True}


@app.get("/api/config/glossary")
async def get_glossary():
    """获取专业术语词典内容。"""
    glossary_path = BASE_DIR / "backend" / "data" / "glossary.txt"
    if glossary_path.exists():
        return {"content": glossary_path.read_text(encoding="utf-8")}
    return {"content": ""}


@app.put("/api/config/glossary")
async def update_glossary(body: dict):
    """保存专业术语词典内容。"""
    glossary_path = BASE_DIR / "backend" / "data" / "glossary.txt"
    content = body.get("content", "")
    glossary_path.write_text(content, encoding="utf-8")
    return {"success": True}


# ============================================================
# Prompt Management API
# ============================================================

PROMPTS_DIR = BASE_DIR / "backend" / "prompts"
THEMES_DIR = PROMPTS_DIR / "themes"

SCENARIO_FILES = {
    "monologue_clean": "monologue_clean.md",
    "interview_compress": "interview_compress.md",
    "highlight_reel": "highlight_reel.md",
}

# 主题元数据（id → 名称/描述），顺序固定
THEME_META = [
    {"id": "interview",    "name": "访谈 / 对话",  "desc": "主播×嘉宾问答，保证问答对完整"},
    {"id": "product_demo", "name": "产品演示",      "desc": "功能演示/操作步骤，突出效果对比"},
    {"id": "vlog",         "name": "Vlog / 日常",   "desc": "情绪高峰与视觉亮点优先"},
    {"id": "lecture",      "name": "课程 / 讲座",   "desc": "知识密度高，逻辑完整为首要条件"},
]


_COVER_PROMPTS_PATH = PROMPTS_DIR / "cover_content.json"

_DEFAULT_COVER_PROMPTS = {
    "cover_image": {
        "user_prompt": (
            "基于这张视频截图，为小红书制作竖版封面图片（9:16）。\n"
            "封面标题文字：「{cover_title}」\n"
            "视觉风格：{style_prompt}\n"
            "要求：将标题文字清晰地叠加在画面上，文字醒目易读，整体构图吸引人，参考原图的色调与场景进行再创作。"
        )
    },
    "cover_titles": {
        "system_prompt": "你是短视频封面文案专家，擅长用极简文字制造强烈好奇心，让用户忍不住点击观看。",
        "user_prompt": (
            "视频内容摘要：{segments_text}\n\n"
            "请生成3个封面标题备选，严格遵守以下规则：\n"
            "1. 三个标题字数各不相同，短的5-8字、中的9-13字、长的14-20字，顺序随机\n"
            "2. 必须是以下类型之一（优先选问句）：提问 / 冲突(A vs B) / 情绪表达\n"
            "3. 必须包含至少一个钩子词：为什么/怎么/不要/真的/底气/改变/凭什么/居然/竟然/敢\n"
            "4. 不要总结内容，只制造好奇和悬念\n"
            "5. 不加emoji，不加标点符号以外的装饰\n\n"
            "严格按以下格式回复，不要有任何多余内容：\n1. xxx\n2. xxx\n3. xxx"
        ),
    },
    "xhs_content": {
        "system_prompt": "你是小红书爆款内容创作专家，擅长根据封面字幕展开写出吸引人的视频标题和简介。语言自然、有温度，符合小红书平台调性。",
        "user_prompt": (
            "封面字幕：{cover_title}\n\n"
            "请根据封面字幕输出：\n"
            "1. 三个视频标题备选，严格遵守以下规则：\n"
            "   - 15-25字\n"
            "   - 对封面字幕进行解释或展开，不要重复封面原文\n"
            "   - 必须包含场景词（如：孩子/学习/读书/课堂/成长）\n"
            "   - 必须包含动作词（如：怎么做/如何选/怎么教/如何引导）\n"
            "   - 可加入情绪词增强可信度（如：亲测/真实/终于/意外/后悔）\n"
            "   - 可以带1-2个emoji\n"
            "2. 视频简介（50字左右，自然叙述，结尾加2-3个相关话题标签）\n\n"
            "严格按以下格式回复，不要有任何多余内容：\n标题1：xxx\n标题2：xxx\n标题3：xxx\n简介：xxx"
        ),
    },
    "cover_styles": {
        "emotion": (
            "情绪冲击型封面（爆款流量型）。\n"
            "人物必须占画面主体，优先使用面部特写（放大到肩膀以上）。\n"
            "表情要明显：震惊/困惑/崩溃/不理解。\n"
            "文案必须拆分成2-3行，每行不超过6个字；关键词（1-2个）放大1.3倍。\n"
            "字体：粗体 + 黑色描边（必须）。颜色：黄色或白色高对比字体。\n"
            "文案位置贴近人物脸部，制造压迫感。构图略不对称，增加冲突感。\n"
            "禁止：居中排版、小字、干净设计风。\n"
            "目标效果：强情绪、略夸张、第一眼吸引点击。"
        ),
        "info": (
            "信息解释型封面（知识卡片）。\n"
            "文案拆为主标题（结论，大字）+ 副标题（解释/补充，小字），最多2层结构。\n"
            "字体清晰，不使用粗描边；白色或浅黄色字体 + 轻阴影。\n"
            "可加简单框或底色块突出关键词。背景轻微模糊，提高可读性。\n"
            "人物不需要强情绪，正常表情即可。\n"
            "禁止：夸张表情、大面积黄色粗字、营销广告感。\n"
            "目标效果：清晰、有逻辑、像『有干货』。"
        ),
        "vlog": (
            "生活感vlog封面（真实日常）。\n"
            "保留完整场景（房间/桌面/环境），人物为中景（不要脸部特写）。\n"
            "文案像说话，保持口语感；字体中等大小，不抢画面。\n"
            "文案放在边角或人物旁边，自然排布。\n"
            "整体色调偏暖或生活化，可加轻微滤镜（不要重设计）。\n"
            "禁止：大字压脸、强对比色、广告感。\n"
            "目标效果：像真实截图，但有轻微『钩子』。"
        ),
    },
}


def _load_cover_prompts() -> dict:
    """从 cover_content.json 加载封面文案 prompt，文件不存在则返回默认值。"""
    import json as _json
    if _COVER_PROMPTS_PATH.exists():
        try:
            data = _json.loads(_COVER_PROMPTS_PATH.read_text(encoding="utf-8"))
            # 用默认值补齐缺失字段
            result = {}
            for section, defaults in _DEFAULT_COVER_PROMPTS.items():
                result[section] = {**defaults, **data.get(section, {})}
            return result
        except Exception:
            pass
    return _DEFAULT_COVER_PROMPTS.copy()


@app.get("/api/prompts/cover-content")
async def get_cover_content_prompts():
    """获取封面与文案生成的 prompt 模板。"""
    return _load_cover_prompts()


@app.put("/api/prompts/cover-content")
async def update_cover_content_prompts(body: dict):
    """保存封面与文案生成的 prompt 模板到 cover_content.json。"""
    import json as _json
    _COVER_PROMPTS_PATH.write_text(_json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True}


@app.get("/api/themes")
async def list_themes():
    """列出所有主题及其 prompt 内容。"""
    result = []
    for meta in THEME_META:
        path = THEMES_DIR / f"{meta['id']}.md"
        content = path.read_text("utf-8") if path.exists() else ""
        result.append({**meta, "content": content})
    return result


@app.get("/api/themes/{theme_id}")
async def get_theme(theme_id: str):
    meta = next((m for m in THEME_META if m["id"] == theme_id), None)
    if not meta:
        raise HTTPException(404, f"Unknown theme: {theme_id}")
    path = THEMES_DIR / f"{theme_id}.md"
    content = path.read_text("utf-8") if path.exists() else ""
    return {**meta, "content": content}


@app.put("/api/themes/{theme_id}")
async def update_theme(theme_id: str, body: dict):
    meta = next((m for m in THEME_META if m["id"] == theme_id), None)
    if not meta:
        raise HTTPException(404, f"Unknown theme: {theme_id}")
    THEMES_DIR.mkdir(parents=True, exist_ok=True)
    path = THEMES_DIR / f"{theme_id}.md"
    path.write_text(body.get("content", ""), encoding="utf-8")
    return {"success": True}


@app.get("/api/prompts/{scenario}")
async def get_prompt(scenario: str):
    """Get the full prompt text for a scenario."""
    if scenario not in SCENARIO_FILES:
        raise HTTPException(status_code=404, detail=f"Unknown scenario: {scenario}")
    path = PROMPTS_DIR / SCENARIO_FILES[scenario]
    if not path.exists():
        return {"content": "", "rules": []}
    content = path.read_text(encoding="utf-8")
    rules = _parse_rules_from_prompt(content)
    return {"content": content, "rules": rules}


@app.put("/api/prompts/{scenario}")
async def update_prompt(scenario: str, body: dict):
    """Save the full prompt text for a scenario."""
    if scenario not in SCENARIO_FILES:
        raise HTTPException(status_code=404, detail=f"Unknown scenario: {scenario}")
    path = PROMPTS_DIR / SCENARIO_FILES[scenario]
    content = body.get("content", "")
    path.write_text(content, encoding="utf-8")
    rules = _parse_rules_from_prompt(content)
    return {"success": True, "rules": rules}


@app.get("/api/prompts/{scenario}/rules")
async def get_rules(scenario: str):
    """Get parsed rules from a scenario prompt."""
    if scenario not in SCENARIO_FILES:
        raise HTTPException(status_code=404, detail=f"Unknown scenario: {scenario}")
    path = PROMPTS_DIR / SCENARIO_FILES[scenario]
    if not path.exists():
        return {"rules": []}
    content = path.read_text(encoding="utf-8")
    return {"rules": _parse_rules_from_prompt(content)}


@app.put("/api/prompts/{scenario}/rules")
async def update_rules(scenario: str, body: dict):
    """
    Receive updated rules list and patch them back into the prompt file.
    Each rule has: {code, name, priority, desc, logic, example}
    """
    if scenario not in SCENARIO_FILES:
        raise HTTPException(status_code=404, detail=f"Unknown scenario: {scenario}")
    path = PROMPTS_DIR / SCENARIO_FILES[scenario]
    if not path.exists():
        raise HTTPException(status_code=404, detail="Prompt file not found")

    rules = body.get("rules", [])
    content = path.read_text(encoding="utf-8")
    content = _patch_rules_into_prompt(content, rules)
    path.write_text(content, encoding="utf-8")
    return {"success": True, "rules": rules}


def _parse_rules_from_prompt(content: str) -> list:
    """
    Parse structured rules from prompt markdown.
    Looks for patterns like: ### 【P1】重说识别 ★★★ ...
    Returns list of {code, name, priority, desc, logic, full_text}
    """
    import re
    rules = []
    # Match rule headers: ### 【CODE】Name ★...
    pattern = re.compile(
        r'###\s+【([A-Z0-9]+)】([^\n★]+)([★☆]*)([^\n]*)\n(.*?)(?=###\s+【|## |\Z)',
        re.DOTALL
    )
    for m in pattern.finditer(content):
        code = m.group(1).strip()
        name = m.group(2).strip()
        stars = m.group(3).strip()
        priority_note = m.group(4).strip()
        body = m.group(5).strip()

        # Extract first paragraph as short desc
        lines = [l.strip() for l in body.split('\n') if l.strip()]
        desc = lines[0] if lines else ""
        # Remove bold markers
        desc = re.sub(r'\*\*([^*]+)\*\*', r'\1', desc)
        if desc.startswith('**') or desc.startswith('触发') or desc.startswith('执行'):
            desc = lines[1] if len(lines) > 1 else desc

        priority = len([c for c in stars if c == '★'])

        rules.append({
            "code": code,
            "name": name,
            "priority": priority,
            "priority_note": priority_note.strip('() '),
            "stars": stars,
            "desc": desc[:80] if desc else "",
            "full_text": m.group(0).strip(),
        })
    return rules


def _patch_rules_into_prompt(content: str, rules: list) -> str:
    """
    Replace individual rule sections in the prompt with updated versions.
    Only updates rules that have a 'full_text' field provided.
    """
    import re
    for rule in rules:
        code = rule.get("code")
        new_text = rule.get("full_text", "").strip()
        if not code or not new_text:
            continue
        # Replace the old rule block with the new one
        pattern = re.compile(
            r'###\s+【' + re.escape(code) + r'】.*?(?=###\s+【|## |\Z)',
            re.DOTALL
        )
        replacement = new_text + "\n\n"
        new_content = pattern.sub(replacement, content)
        if new_content != content:
            content = new_content
    return content


# ============================================================
# 学习模块 (Learning Module)
# ============================================================

@app.post("/api/learning/tasks")
async def create_learning_task(req: CreateLearningTaskRequest):
    try:
        store = get_learning_store()
        task = LearningTask(name=req.name, task_type=req.task_type)
        store.create(task)
        return task.model_dump()
    except Exception as e:
        err_msg = str(e) if str(e) else "创建学习任务失败"
        print(f"[create_learning_task] 失败: {e}", flush=True)
        raise HTTPException(status_code=500, detail=err_msg)


@app.get("/api/learning/tasks")
async def list_learning_tasks():
    store = get_learning_store()
    return [serialize_learning_task_list_item(t) for t in store.get_all()]


@app.get("/api/learning/tasks/{task_id}")
async def get_learning_task(task_id: str):
    store = get_learning_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "学习任务不存在")
    return task.model_dump()


@app.delete("/api/learning/tasks/{task_id}")
async def delete_learning_task(task_id: str):
    # 先取消正在运行的后台 ASR / 分析任务
    _cancel_learning_task(task_id)

    store = get_learning_store()
    if not store.delete(task_id):
        raise HTTPException(404, "学习任务不存在")
    return {"success": True}


@app.post("/api/learning/tasks/{task_id}/upload-original")
async def upload_original_video(task_id: str, file: UploadFile = File(...)):
    """上传原视频。"""
    store = get_learning_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "学习任务不存在")

    ext = Path(file.filename).suffix.lower()
    if ext not in [".mp4", ".mov", ".avi", ".mkv", ".webm"]:
        raise HTTPException(400, f"不支持的视频格式: {ext}")

    video_path = LEARNING_UPLOADS_DIR / f"{task_id}_original{ext}"
    with open(video_path, "wb") as f:
        content = await file.read()
        f.write(content)

    duration = get_video_duration(str(video_path))
    task.original_video_path = str(video_path)
    task.original_video_filename = file.filename
    task.original_video_duration = duration
    store.update(task)

    return {
        "success": True,
        "video_path": str(video_path),
        "duration": duration,
        "filename": file.filename,
    }


@app.post("/api/learning/tasks/{task_id}/upload-edited")
async def upload_edited_video(task_id: str, file: UploadFile = File(...)):
    """上传人工剪辑视频。"""
    store = get_learning_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "学习任务不存在")

    ext = Path(file.filename).suffix.lower()
    if ext not in [".mp4", ".mov", ".avi", ".mkv", ".webm"]:
        raise HTTPException(400, f"不支持的视频格式: {ext}")

    video_path = LEARNING_UPLOADS_DIR / f"{task_id}_edited{ext}"
    with open(video_path, "wb") as f:
        content = await file.read()
        f.write(content)

    duration = get_video_duration(str(video_path))
    task.edited_video_path = str(video_path)
    task.edited_video_filename = file.filename
    task.edited_video_duration = duration
    store.update(task)

    return {
        "success": True,
        "video_path": str(video_path),
        "duration": duration,
        "filename": file.filename,
    }


@app.post("/api/learning/tasks/{task_id}/asr")
async def trigger_learning_asr(task_id: str, body: AsrRequest = None):
    """对原视频和剪辑视频依次执行 ASR。"""
    store = get_learning_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "学习任务不存在")
    if not task.original_video_path or not os.path.exists(task.original_video_path):
        raise HTTPException(400, "请先上传原视频")
    if not task.edited_video_path or not os.path.exists(task.edited_video_path):
        raise HTTPException(400, "请先上传人工剪辑视频")

    backend = (body.backend if body else None) or "funasr"
    whisper_model = (body.model if body else None) or "base"

    # 若该任务已有正在运行的后台任务，先取消
    _cancel_learning_task(task_id)

    bg_task = asyncio.create_task(_run_learning_asr(task_id, whisper_model, backend))
    _learning_active_tasks[task_id] = bg_task
    bg_task.add_done_callback(lambda _: _learning_active_tasks.pop(task_id, None))
    return {"success": True, "message": "双轨 ASR 任务已启动"}


async def _run_learning_asr(task_id: str, whisper_model: str, backend: str):
    """后台执行双轨 ASR：先原视频，再剪辑视频。"""
    store = get_learning_store()
    task = store.get(task_id)
    if not task:
        return

    async def log(level, source, msg, progress=None):
        await log_to_ws(f"learn_{task_id}", level, source, msg, progress)

    params = TaskParams()

    # 阶段一：原视频 ASR
    try:
        task.status = LearningTaskStatus.ASR_ORIGINAL
        store.update(task)
        await log("info", "asr", "开始原视频 ASR...")

        original_result = await run_asr_pipeline(
            task.original_video_path, params, log,
            whisper_model=whisper_model, backend=backend,
        )
        task.original_asr_result = original_result
        task.original_video_duration = original_result.duration
        store.update(task)
        await log("success", "asr", f"原视频 ASR 完成，共 {len(original_result.segments)} 段")
    except asyncio.CancelledError:
        # 任务被删除，静默退出，不写入错误状态
        return
    except Exception as e:
        task.status = LearningTaskStatus.ERROR
        task.error_message = f"原视频 ASR 失败: {str(e)}"
        store.update(task)
        await log("error", "asr", task.error_message)
        return

    # 阶段二：剪辑视频 ASR
    try:
        task.status = LearningTaskStatus.ASR_EDITED
        store.update(task)
        await log("info", "asr", "开始剪辑视频 ASR...")

        edited_result = await run_asr_pipeline(
            task.edited_video_path, params, log,
            whisper_model=whisper_model, backend=backend,
        )
        task.edited_asr_result = edited_result
        task.edited_video_duration = edited_result.duration
        task.status = LearningTaskStatus.ASR_DONE
        store.update(task)
        await log("success", "asr", f"剪辑视频 ASR 完成，共 {len(edited_result.segments)} 段")
    except asyncio.CancelledError:
        # 任务被删除，静默退出
        return
    except Exception as e:
        task.status = LearningTaskStatus.ERROR
        task.error_message = f"剪辑视频 ASR 失败: {str(e)}"
        store.update(task)
        await log("error", "asr", task.error_message)


@app.post("/api/learning/tasks/{task_id}/analyze")
async def trigger_learning_analysis(task_id: str, req: LearningAnalyzeRequest):
    """触发 Claude 分析人工剪辑思路。"""
    store = get_learning_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "学习任务不存在")
    if not task.original_asr_result or not task.edited_asr_result:
        raise HTTPException(400, "请先完成双轨 ASR")

    # 若该任务已有正在运行的后台任务，先取消
    _cancel_learning_task(task_id)

    bg_task = asyncio.create_task(_run_learning_analysis(task_id, req))
    _learning_active_tasks[task_id] = bg_task
    bg_task.add_done_callback(lambda _: _learning_active_tasks.pop(task_id, None))
    return {"success": True, "message": "学习分析已启动"}


async def _run_learning_analysis(task_id: str, req: LearningAnalyzeRequest):
    """后台执行 Claude 学习分析。"""
    store = get_learning_store()
    task = store.get(task_id)
    if not task:
        return

    task.status = LearningTaskStatus.ANALYZING
    store.update(task)

    async def log(level, source, msg, progress=None):
        await log_to_ws(f"learn_{task_id}", level, source, msg, progress)

    try:
        result = await run_learning_analysis(
            task=task,
            api_key=req.claude_api_key,
            model=req.claude_model,
            log_callback=log,
        )

        task.analysis_result = result["analysis_result"]
        task.new_prompt_content = result["new_prompt_content"]
        task.status = LearningTaskStatus.DONE
        store.update(task)

        await manager.broadcast(f"learn_{task_id}", {
            "type": "status_change",
            "task_id": task_id,
            "status": LearningTaskStatus.DONE,
        })

    except asyncio.CancelledError:
        # 任务被删除，静默退出
        return
    except Exception as e:
        task.status = LearningTaskStatus.ERROR
        task.error_message = str(e)
        store.update(task)
        await log("error", "claude", f"学习分析失败: {str(e)}")


class SavePromptRequest(BaseModel):
    content: Optional[str] = None


@app.post("/api/learning/tasks/{task_id}/save-prompt")
async def save_learning_prompt(task_id: str, body: SavePromptRequest = None):
    """将学习分析生成的新提示词保存为版本化文件。"""
    store = get_learning_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "学习任务不存在")

    content = (body.content if body else None) or task.new_prompt_content
    if not content:
        raise HTTPException(400, "没有可保存的提示词内容")

    task_type = task.task_type.value if task.task_type else "monologue_clean"
    filepath, version = save_versioned_prompt(task_type, content)

    task.new_prompt_path = filepath
    task.prompt_version = version
    store.update(task)

    return {
        "success": True,
        "path": filepath,
        "version": version,
        "filename": Path(filepath).name,
    }


@app.get("/api/learning/prompts/{scenario}/versions")
async def list_prompt_versions(scenario: str):
    """获取某场景的提示词版本列表。"""
    versions = get_prompt_versions(scenario)
    return {"versions": versions, "scenario": scenario}


# ============================================================
# 学习模块 手动模式（离线 AI）
# ============================================================

@app.get("/api/learning/tasks/{task_id}/manual-prompt/step1")
async def get_learning_manual_prompt_step1(task_id: str):
    """下载学习分析第一步的提示词包（system + user prompt）。"""
    store = get_learning_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "学习任务不存在")
    if not task.original_asr_result or not task.edited_asr_result:
        raise HTTPException(400, "请先完成双轨 ASR")

    task_type = task.task_type.value if task.task_type else "monologue_clean"
    diff_result = diff_transcripts(task.original_asr_result, task.edited_asr_result)
    current_prompt = load_audit_system_prompt(task_type)

    system_prompt = load_learning_system_prompt()
    user_prompt = build_analysis_prompt(
        diff_result, current_prompt, task_type,
        task.original_asr_result, task.edited_asr_result,
    )

    combined = f"=== SYSTEM PROMPT ===\n{system_prompt}\n\n=== USER PROMPT ===\n{user_prompt}"
    return {
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
        "combined": combined,
    }


class ManualStep1ResultRequest(BaseModel):
    analysis_result: str


@app.post("/api/learning/tasks/{task_id}/manual-result/step1")
async def submit_learning_manual_result_step1(task_id: str, req: ManualStep1ResultRequest):
    """提交第一步（分析报告）的 AI 输出结果。"""
    store = get_learning_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "学习任务不存在")
    if not req.analysis_result.strip():
        raise HTTPException(400, "分析报告内容不能为空")

    task.analysis_result = req.analysis_result.strip()
    store.update(task)
    return {"success": True, "message": "第一步分析报告已保存"}


@app.get("/api/learning/tasks/{task_id}/manual-prompt/step2")
async def get_learning_manual_prompt_step2(task_id: str):
    """下载学习分析第二步的提示词包（基于已有分析报告生成改写提示词）。"""
    store = get_learning_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "学习任务不存在")
    if not task.analysis_result:
        raise HTTPException(400, "请先完成第一步分析")

    task_type = task.task_type.value if task.task_type else "monologue_clean"
    current_prompt = load_audit_system_prompt(task_type)

    system_prompt = load_rewrite_system_prompt()
    user_prompt = build_rewrite_prompt(task.analysis_result, current_prompt, task_type)

    combined = f"=== SYSTEM PROMPT ===\n{system_prompt}\n\n=== USER PROMPT ===\n{user_prompt}"
    return {
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
        "combined": combined,
    }


class ManualStep2ResultRequest(BaseModel):
    changes_json: str


@app.post("/api/learning/tasks/{task_id}/manual-result/step2")
async def submit_learning_manual_result_step2(task_id: str, req: ManualStep2ResultRequest):
    """提交第二步（规则变更 JSON）的 AI 输出结果，合并生成新提示词。"""
    store = get_learning_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "学习任务不存在")
    if not req.changes_json.strip():
        raise HTTPException(400, "变更内容不能为空")

    task_type = task.task_type.value if task.task_type else "monologue_clean"
    current_prompt = load_audit_system_prompt(task_type)
    new_prompt = apply_prompt_changes(current_prompt, req.changes_json.strip())

    task.new_prompt_content = new_prompt
    task.status = LearningTaskStatus.DONE
    store.update(task)

    return {
        "success": True,
        "message": "新提示词已生成",
        "new_prompt_length": len(new_prompt),
        "original_prompt_length": len(current_prompt),
    }


# ============================================================
# 剪辑模块 手动模式（离线 AI 审计）
# ============================================================

@app.get("/api/tasks/{task_id}/manual-audit-prompt")
async def get_manual_audit_prompt(task_id: str):
    """下载剪辑审计的提示词包（system + user prompt）。"""
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    if not task.audit_segments:
        raise HTTPException(400, "No segments to audit. Run ASR first.")

    task_type = task.task_type.value if task.task_type else None
    system_prompt = load_audit_system_prompt(task_type)
    user_prompt, idx_to_id = build_audit_prompt(
        segments=task.audit_segments,
        style_mode=task.params.style_mode,
        task_type=task_type,
    )

    combined = f"=== SYSTEM PROMPT ===\n{system_prompt}\n\n=== USER PROMPT ===\n{user_prompt}"
    return {
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
        "combined": combined,
        "idx_to_id": idx_to_id,
    }


class ManualAuditResultRequest(BaseModel):
    audit_json: str


@app.post("/api/tasks/{task_id}/manual-audit-result")
async def submit_manual_audit_result(task_id: str, req: ManualAuditResultRequest):
    """提交手动 AI 审计的 JSON 输出结果。"""
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    if not task.audit_segments:
        raise HTTPException(400, "No segments to audit")
    if not req.audit_json.strip():
        raise HTTPException(400, "审计结果 JSON 不能为空")

    task_type = task.task_type.value if task.task_type else None

    # 重建 idx_to_id 映射（与 build_audit_prompt 使用相同逻辑）
    idx_to_id = {}
    for i, seg in enumerate(task.audit_segments):
        idx_to_id[i + 1] = seg.id

    updated_segments = parse_claude_response(
        response_text=req.audit_json.strip(),
        original_segments=task.audit_segments,
        idx_to_id=idx_to_id,
    )

    updated_segments = apply_segment_corrections(updated_segments)

    # 冻结 Claude 原始决策快照
    for seg in updated_segments:
        seg.claude_action = seg.action
        seg.claude_reason = seg.reason

    task.audit_segments = updated_segments
    task.update_stats()
    task.status = TaskStatus.REVIEW

    audit_snap = AuditSnapshot(
        version=len(task.audit_history) + 1,
        created_at=datetime.now().isoformat(),
        model="手动模式（离线 AI）",
        segments_count=len(updated_segments),
        segments_kept=task.segments_kept,
        segments_deleted=task.segments_deleted,
    )
    task.audit_history.append(audit_snap)
    task.audit_current_version = audit_snap.version

    store.update(task)

    return {
        "success": True,
        "message": f"审计完成，保留 {task.segments_kept} 段，删除 {task.segments_deleted} 段",
        "segments_kept": task.segments_kept,
        "segments_deleted": task.segments_deleted,
        "edited_duration": task.edited_duration,
    }


# ============================================================
# WebSocket for Real-time Logs
# ============================================================

@app.websocket("/ws/tasks/{task_id}/log")
async def websocket_log(websocket: WebSocket, task_id: str):
    await manager.connect(task_id, websocket)
    try:
        # Send initial connection confirmation
        await websocket.send_json({
            "type": "connected",
            "task_id": task_id,
            "message": f"已连接到任务 {task_id} 的日志流"
        })
        # Keep connection alive
        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30)
                if data == "ping":
                    await websocket.send_json({"type": "pong"})
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "heartbeat"})
    except WebSocketDisconnect:
        manager.disconnect(task_id, websocket)


# ============================================================
# Static file serving (for exports)
# ============================================================

@app.get("/api/exports/{filename}")
async def download_export(filename: str):
    file_path = EXPORTS_DIR / filename
    if not file_path.exists():
        raise HTTPException(404, "Export file not found")
    return FileResponse(str(file_path))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)
