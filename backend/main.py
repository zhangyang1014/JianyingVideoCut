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

from .models.task import (
    Task, TaskStatus, TaskType, CreateTaskRequest,
    UpdateSegmentsRequest, ExportRequest, AuditRequest,
    Segment, SegmentAction, TaskParams, LogEntry
)
from .services.task_store import get_store
from .services.asr_pipeline import run_asr_pipeline, get_video_duration, generate_thumbnail
from .services.semantic_auditor import run_semantic_audit
from .services.ffmpeg_executor import export_ffmpeg_lossless, check_ffmpeg_available
from .services.jianying_builder import build_jianying_draft

# Directories
BASE_DIR = Path(__file__).parent.parent
UPLOADS_DIR = BASE_DIR / "uploads"
EXPORTS_DIR = BASE_DIR / "exports"
THUMBNAILS_DIR = BASE_DIR / "thumbnails"

for d in [UPLOADS_DIR, EXPORTS_DIR, THUMBNAILS_DIR]:
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
    allow_credentials=True,
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

@app.get("/api/health")
async def health_check():
    return {
        "status": "ok",
        "ffmpeg": check_ffmpeg_available(),
        "version": "3.0.0"
    }


@app.get("/api/tasks")
async def list_tasks():
    store = get_store()
    tasks = store.get_all()
    return [t.model_dump() for t in tasks]


@app.post("/api/tasks")
async def create_task(req: CreateTaskRequest):
    store = get_store()
    task = Task(
        name=req.name,
        task_type=req.task_type,
        params=req.params or TaskParams()
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

@app.post("/api/tasks/{task_id}/asr")
async def trigger_asr(task_id: str):
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    if not task.video_path or not os.path.exists(task.video_path):
        raise HTTPException(400, "Video file not uploaded")

    # Run ASR in background
    asyncio.create_task(_run_asr_task(task_id))
    return {"success": True, "message": "ASR 任务已启动"}


async def _run_asr_task(task_id: str):
    store = get_store()
    task = store.get(task_id)
    if not task:
        return

    task.status = TaskStatus.ASR_RUNNING
    store.update(task)

    async def log(level, source, msg, progress=None):
        await log_to_ws(task_id, level, source, msg, progress)

    try:
        await log("info", "system", f"开始处理任务: {task.name}")
        asr_result = await run_asr_pipeline(task.video_path, task.params, log)

        task.asr_result = asr_result
        task.audit_segments = asr_result.segments
        task.video_duration = asr_result.duration
        task.original_duration = asr_result.duration
        task.status = TaskStatus.ASR_DONE
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
        updated_segments = await run_semantic_audit(
            segments=task.audit_segments,
            params=task.params,
            task_type=task.task_type.value if task.task_type else None,
            api_key=req.claude_api_key,
            model=req.claude_model,
            style_mode=req.style_mode,
            log_callback=log
        )

        task.audit_segments = updated_segments
        task.update_stats()
        task.status = TaskStatus.REVIEW
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
        task.status = TaskStatus.ERROR
        task.error_message = str(e)
        store.update(task)
        await log("error", "system", f"审计失败: {str(e)}")


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

    asyncio.create_task(_run_ffmpeg_export(task_id, req))
    return {"success": True, "message": "FFmpeg 导出已启动"}


async def _run_ffmpeg_export(task_id: str, req: ExportRequest):
    store = get_store()
    task = store.get(task_id)
    if not task:
        return

    task.status = TaskStatus.EXPORT_RUNNING
    store.update(task)

    async def log(level, source, msg, progress=None):
        await log_to_ws(task_id, level, source, msg, progress)

    try:
        output_name = req.output_name or f"{task.name}_output"
        output_path = str(EXPORTS_DIR / f"{output_name}.mp4")

        success = await export_ffmpeg_lossless(
            video_path=task.video_path,
            segments=task.audit_segments,
            output_path=output_path,
            log_callback=log
        )

        if success:
            task.export_path = output_path
            task.status = TaskStatus.DONE
            store.update(task)
            await manager.broadcast(task_id, {
                "type": "export_done",
                "task_id": task_id,
                "output_path": output_path,
                "mode": "ffmpeg"
            })
        else:
            task.status = TaskStatus.ERROR
            store.update(task)

    except Exception as e:
        task.status = TaskStatus.ERROR
        task.error_message = str(e)
        store.update(task)
        await log("error", "ffmpeg", f"导出失败: {str(e)}")


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
            log_callback=log
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
