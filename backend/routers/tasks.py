"""任务 CRUD、视频上传、视频流/帧/缩略图、导出文件下载、WebSocket 日志。"""

import os
import asyncio
import hashlib
import json
from pathlib import Path
from typing import Optional

import aiofiles
from fastapi import APIRouter, UploadFile, File, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from starlette.background import BackgroundTask
from fastapi.responses import FileResponse

from ..models.task import (
    Task, TaskStatus, TaskType, CreateTaskRequest, TaskParams, default_task_params, UpdateReferenceScriptRequest,
)
from ..services.task_store import get_store, RevisionConflictError
from ..services.reference_script import match_reference_script
from ..services.model_usage_store import get_model_usage_store
from ..services.asr_pipeline import get_video_duration, generate_thumbnail
from ..core.paths import UPLOADS_DIR, EXPORTS_DIR, THUMBNAILS_DIR
from ..core.ws import manager
from ..core.serialization import serialize_task_detail, serialize_task_list_item
from ..core.uploads import UPLOAD_CHUNK_SIZE
from ..core.task_runner import get_task_runtime_health

router = APIRouter()
_upload_locks: dict[str, asyncio.Lock] = {}


def _upload_lock(task_id: str) -> asyncio.Lock:
    return _upload_locks.setdefault(task_id, asyncio.Lock())


def _read_upload_metadata(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_upload_metadata(path: Path, payload: dict) -> None:
    part = path.with_name(f"{path.name}.part")
    part.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    os.replace(part, path)


def _upload_response(task: Task, *, reused: bool = False) -> dict:
    return {
        "success": True,
        "video_path": task.video_path,
        "duration": task.video_duration,
        "filename": task.video_filename,
        "idempotent_reuse": reused,
    }


# ============================================================
# Task Management Endpoints
# ============================================================

@router.get("/api/tasks")
async def list_tasks():
    store = get_store()
    tasks = store.get_all()
    return [serialize_task_list_item(t) for t in tasks]


@router.post("/api/tasks")
async def create_task(req: CreateTaskRequest):
    store = get_store()
    params = default_task_params(req.task_type)
    if req.params is not None:
        params = params.model_copy(update=req.params.model_dump(exclude_unset=True))
    if params.reference_script and req.task_type != TaskType.MONOLOGUE_CLEAN:
        raise HTTPException(400, "原稿匹配仅适用于口播精修任务")
    task = Task(
        name=req.name,
        task_type=req.task_type,
        params=params,
    )
    store.create(task)
    return task.model_dump()


@router.get("/api/tasks/{task_id}")
async def get_task(task_id: str):
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    return serialize_task_detail(task)


@router.get("/api/tasks/{task_id}/usage")
async def get_task_usage(task_id: str):
    store = get_store()
    if not store.get(task_id):
        raise HTTPException(404, "Task not found")
    return get_model_usage_store().summarize(task_id).model_dump(mode="json")


@router.get("/api/tasks/{task_id}/reference-script")
async def get_reference_script(task_id: str):
    task = get_store().get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    if task.task_type != TaskType.MONOLOGUE_CLEAN:
        raise HTTPException(400, "原稿匹配仅适用于口播精修任务")
    task = task.model_copy(deep=True)
    report = await asyncio.to_thread(match_reference_script, task.params.reference_script, task.audit_segments)
    return {"reference_script": task.params.reference_script, "report": report.model_dump()}


@router.put("/api/tasks/{task_id}/reference-script")
async def update_reference_script(task_id: str, req: UpdateReferenceScriptRequest):
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    if task.task_type != TaskType.MONOLOGUE_CLEAN:
        raise HTTPException(400, "原稿匹配仅适用于口播精修任务")
    if task.status.value.endswith("running") or any(
        run.run_id == task.active_execution_run_id and run.status in {"pending", "running", "retry_wait"}
        for run in task.execution_runs
    ):
        raise HTTPException(409, "请等待当前处理完成后再修改原稿")
    if task.params.reference_script != req.previous_script:
        raise HTTPException(409, "原稿已在其他窗口更新，请刷新后重试")
    revision = task.revision
    task = task.model_copy(deep=True)
    task.params.reference_script = req.reference_script.strip()
    try:
        store.update(task, expected_revision=revision)
    except RevisionConflictError as exc:
        raise HTTPException(409, "任务已更新，请刷新后重试") from exc
    task = task.model_copy(deep=True)
    report = await asyncio.to_thread(match_reference_script, task.params.reference_script, task.audit_segments)
    return {"reference_script": task.params.reference_script, "report": report.model_dump()}


@router.get("/api/tasks/{task_id}/usage/calls")
async def get_task_usage_calls(
    task_id: str,
    feature: Optional[str] = None,
    cursor: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=100),
):
    store = get_store()
    if not store.get(task_id):
        raise HTTPException(404, "Task not found")
    try:
        page = get_model_usage_store().list_calls(
            task_id,
            feature=feature,
            cursor=cursor,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return page.model_dump(mode="json")


@router.get("/api/tasks/{task_id}/runtime-health")
async def get_runtime_health(task_id: str):
    """诊断后台协程/导出进程是否仍活跃，供前端避免假死误判。"""
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    return get_task_runtime_health(task_id, task.status)


@router.delete("/api/tasks/{task_id}")
async def delete_task(task_id: str):
    store = get_store()
    success = store.delete(task_id)
    if not success:
        raise HTTPException(404, "Task not found")
    get_model_usage_store().delete_task(task_id)
    manager.clear_buffer(task_id)
    return {"success": True}


# ============================================================
# Video Upload
# ============================================================

@router.post("/api/tasks/{task_id}/upload")
async def upload_video(
    task_id: str,
    file: UploadFile = File(...),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    store = get_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")

    # Save uploaded file
    ext = Path(file.filename).suffix.lower()
    if ext not in [".mp4", ".mov", ".avi", ".mkv", ".webm"]:
        raise HTTPException(400, f"Unsupported format: {ext}")

    video_path = UPLOADS_DIR / f"{task_id}{ext}"
    part_path = video_path.with_name(f"{video_path.name}.part")
    metadata_path = UPLOADS_DIR / f"{task_id}.upload.json"
    thumb_path = THUMBNAILS_DIR / f"{task_id}.jpg"
    thumb_part = THUMBNAILS_DIR / f"{task_id}.part.jpg"
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)

    async with _upload_lock(task_id):
        metadata = _read_upload_metadata(metadata_path)
        declared_size = getattr(file, "size", None)

        part_path.unlink(missing_ok=True)
        thumb_part.unlink(missing_ok=True)
        digest = hashlib.sha256()
        written = 0
        try:
            async with aiofiles.open(part_path, "wb") as handle:
                while chunk := await file.read(UPLOAD_CHUNK_SIZE):
                    digest.update(chunk)
                    written += len(chunk)
                    await handle.write(chunk)
                await handle.flush()

            if written <= 0 or not part_path.exists() or part_path.stat().st_size != written:
                raise HTTPException(400, "上传文件为空或写入不完整")
            if declared_size is not None and declared_size != written:
                raise HTTPException(400, "上传文件大小与声明不一致")

            content_hash = digest.hexdigest()
            if idempotency_key and metadata.get("idempotency_key") == idempotency_key:
                if (
                    metadata.get("sha256") == content_hash
                    and task.video_path
                    and Path(task.video_path).exists()
                ):
                    part_path.unlink(missing_ok=True)
                    return _upload_response(task, reused=True)
                raise HTTPException(409, "Idempotency-Key 已用于不同的上传内容")
            if (
                metadata.get("sha256") == content_hash
                and task.video_path
                and Path(task.video_path).exists()
            ):
                part_path.unlink(missing_ok=True)
                if idempotency_key and metadata.get("idempotency_key") != idempotency_key:
                    metadata["idempotency_key"] = idempotency_key
                    _write_upload_metadata(metadata_path, metadata)
                return _upload_response(task, reused=True)

            loop = asyncio.get_running_loop()
            duration = await loop.run_in_executor(None, get_video_duration, str(part_path))
            await loop.run_in_executor(None, generate_thumbnail, str(part_path), str(thumb_part))
            thumb_ready = thumb_part.exists() and thumb_part.stat().st_size > 0

            previous_video = Path(task.video_path) if task.video_path else None
            os.replace(part_path, video_path)
            if thumb_ready:
                os.replace(thumb_part, thumb_path)
            else:
                thumb_part.unlink(missing_ok=True)
                thumb_path.unlink(missing_ok=True)

            task.video_path = str(video_path)
            task.video_filename = file.filename
            task.video_duration = duration
            task.original_duration = duration
            task.thumbnail_path = str(thumb_path) if thumb_ready else None
            task.status = TaskStatus.PENDING
            store.update(task)
            _write_upload_metadata(metadata_path, {
                "task_id": task_id,
                "idempotency_key": idempotency_key,
                "sha256": content_hash,
                "size": written,
                "filename": file.filename,
                "path": str(video_path),
            })
            if previous_video and previous_video != video_path:
                previous_video.unlink(missing_ok=True)
            return _upload_response(task)
        except BaseException:
            part_path.unlink(missing_ok=True)
            thumb_part.unlink(missing_ok=True)
            raise


# ============================================================
# Video Streaming
# ============================================================

@router.get("/api/tasks/{task_id}/video")
async def stream_video(task_id: str):
    store = get_store()
    task = store.get(task_id)
    if not task or not task.video_path:
        raise HTTPException(404, "Video not found")
    if not os.path.exists(task.video_path):
        raise HTTPException(404, "Video file not found on disk")
    return FileResponse(task.video_path, media_type="video/mp4")


@router.get("/api/tasks/{task_id}/frame")
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
        return FileResponse(
            tmp_path,
            media_type="image/jpeg",
            background=BackgroundTask(Path(tmp_path).unlink, missing_ok=True),
        )
    except subprocess.TimeoutExpired:
        Path(tmp_path).unlink(missing_ok=True)
        raise HTTPException(500, "Frame extraction timed out")
    except BaseException:
        Path(tmp_path).unlink(missing_ok=True)
        raise


@router.get("/api/tasks/{task_id}/thumbnail")
async def get_thumbnail(task_id: str):
    store = get_store()
    task = store.get(task_id)
    if task and task.thumbnail_path and os.path.exists(task.thumbnail_path):
        return FileResponse(task.thumbnail_path, media_type="image/jpeg")
    raise HTTPException(404, "Thumbnail not found")


# ============================================================
# WebSocket for Real-time Logs
# ============================================================

@router.websocket("/ws/tasks/{task_id}/log")
async def websocket_log(websocket: WebSocket, task_id: str):
    await manager.connect(task_id, websocket)
    try:
        # Send initial connection confirmation
        await manager.send_json(websocket, {
            "type": "connected",
            "task_id": task_id,
            "message": f"已连接到任务 {task_id} 的日志流"
        })
        # 回放断线/刷新期间缓冲的历史消息
        await manager.replay(task_id, websocket)
        # Keep connection alive
        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30)
                if data == "ping":
                    await manager.send_json(websocket, {"type": "pong"})
            except asyncio.TimeoutError:
                await manager.send_json(websocket, {"type": "heartbeat"})
    except WebSocketDisconnect:
        pass
    except asyncio.TimeoutError:
        # 慢连接/半关闭连接达到发送上限后主动结束 handler。
        pass
    finally:
        manager.disconnect(task_id, websocket)


# ============================================================
# Static file serving (for exports)
# ============================================================

@router.get("/api/exports/{filename}")
async def download_export(filename: str):
    # 净化文件名并校验最终路径仍在 EXPORTS_DIR 内，防止路径穿越
    safe_name = Path(filename).name
    if not safe_name:
        raise HTTPException(404, "Export file not found")
    file_path = (EXPORTS_DIR / safe_name).resolve()
    exports_root = EXPORTS_DIR.resolve()
    if exports_root not in file_path.parents:
        raise HTTPException(404, "Export file not found")
    if not file_path.exists():
        raise HTTPException(404, "Export file not found")
    return FileResponse(str(file_path))
