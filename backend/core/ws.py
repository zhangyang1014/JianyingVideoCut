"""WebSocket 连接管理器与日志推送。manager 为全局单例，各 router 共享。"""

import asyncio
import json
import re
import threading
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import WebSocket


TASK_LOG_DIR = Path(__file__).resolve().parents[1] / "data" / "task_logs"
_SECRET_KEYS = {
    "api_key",
    "openai_api_key",
    "claude_api_key",
    "tts_api_key",
    "subtitle_api_key",
    "authorization",
}


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "***REDACTED***" if key.lower() in _SECRET_KEYS else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


# WebSocket connection manager
class ConnectionManager:
    MESSAGE_BUFFER_SIZE = 500
    SEND_TIMEOUT_SECONDS = 5.0

    def __init__(self, log_dir: Optional[Path] = None):
        self.active_connections: Dict[str, List[WebSocket]] = {}
        # 每个 task_id 的有界消息缓冲，供重连后回放历史日志
        self.message_buffers: Dict[str, deque] = {}
        # 磁盘日志永久追加保存；内存缓冲仅用于写盘失败时的进程内降级。
        self.log_dir = log_dir or TASK_LOG_DIR
        self._log_lock = threading.Lock()

    def _log_path(self, task_id: str) -> Path:
        safe_task_id = re.sub(r"[^A-Za-z0-9_.-]", "_", task_id)
        return self.log_dir / f"{safe_task_id}.jsonl"

    def _append_persisted_message(self, task_id: str, message: dict) -> dict:
        persisted = _redact(message)
        path = self._log_path(task_id)
        try:
            with self._log_lock:
                path.parent.mkdir(parents=True, exist_ok=True)
                with open(path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(persisted, ensure_ascii=False, default=str) + "\n")
        except OSError as exc:
            print(f"[WS] 持久化任务日志失败 {task_id}: {exc}")
        return persisted

    def _load_persisted_messages(
        self,
        task_id: str,
        limit: Optional[int] = None,
    ) -> List[dict]:
        path = self._log_path(task_id)
        if not path.exists():
            return []
        messages = deque(maxlen=limit) if limit is not None else []
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line_number, line in enumerate(f, start=1):
                    try:
                        message = json.loads(line)
                    except json.JSONDecodeError as exc:
                        print(f"[WS] 跳过损坏日志 {path.name}:{line_number}: {exc}")
                        continue
                    if isinstance(message, dict):
                        messages.append(message)
        except OSError as exc:
            print(f"[WS] 读取任务日志失败 {task_id}: {exc}")
        return list(messages)

    async def connect(self, task_id: str, websocket: WebSocket):
        await websocket.accept()
        if task_id not in self.active_connections:
            self.active_connections[task_id] = []
        self.active_connections[task_id].append(websocket)

    def disconnect(self, task_id: str, websocket: WebSocket):
        connections = self.active_connections.get(task_id)
        if not connections:
            return
        try:
            connections.remove(websocket)
        except ValueError:
            return
        if not connections:
            self.active_connections.pop(task_id, None)

    async def close_all(self) -> None:
        """应用关闭时主动结束连接，避免 Uvicorn 等待存活 WebSocket。"""
        connections = [
            websocket
            for task_connections in self.active_connections.values()
            for websocket in task_connections
        ]
        self.active_connections.clear()
        if connections:
            await asyncio.gather(
                *(
                    asyncio.wait_for(
                        websocket.close(code=1012, reason="service restart"),
                        timeout=self.SEND_TIMEOUT_SECONDS,
                    )
                    for websocket in connections
                ),
                return_exceptions=True,
            )

    def clear_buffer(self, task_id: str):
        """任务删除时仅释放内存；磁盘操作日志按保留策略永久保存。"""
        self.message_buffers.pop(task_id, None)

    async def replay(self, task_id: str, websocket: WebSocket):
        """回放最近的热日志；完整历史永久留在磁盘，避免重连灌入全部记录。"""
        messages = await asyncio.to_thread(
            self._load_persisted_messages,
            task_id,
            self.MESSAGE_BUFFER_SIZE,
        )
        if not messages:
            messages = list(self.message_buffers.get(task_id, ()))
        for message in messages:
            await self.send_json(websocket, {**message, "replay": True})

    async def send_json(self, websocket: WebSocket, message: dict) -> None:
        """有界发送，避免半关闭或不读取的客户端拖住事件循环和服务关停。"""
        await asyncio.wait_for(
            websocket.send_json(message),
            timeout=self.SEND_TIMEOUT_SECONDS,
        )

    async def broadcast(self, task_id: str, message: dict):
        # 无论有没有活跃连接，都先永久追加落盘，重启后也不会丢失。
        message = self._append_persisted_message(task_id, message)
        if task_id not in self.message_buffers:
            self.message_buffers[task_id] = deque(maxlen=self.MESSAGE_BUFFER_SIZE)
        self.message_buffers[task_id].append(message)

        connections = list(self.active_connections.get(task_id, ()))
        if connections:
            async def _send(ws: WebSocket):
                try:
                    await self.send_json(ws, message)
                    return None
                except Exception:
                    return ws

            dead = [
                ws
                for ws in await asyncio.gather(*(_send(ws) for ws in connections))
                if ws is not None
            ]
            for ws in dead:
                self.disconnect(task_id, ws)

    async def broadcast_all(self, message: dict):
        for task_id in list(self.active_connections):
            await self.broadcast(task_id, message)


manager = ConnectionManager()


async def log_to_ws(
    task_id: str,
    level: str,
    source: str,
    message: str,
    progress: float = None,
    *,
    stage: Optional[str] = None,
    error_type: Optional[str] = None,
    **extra: Any,
):
    """Send log entry via WebSocket to frontend Console."""
    entry = {
        "type": "log",
        "timestamp": datetime.now().isoformat(),
        "level": level,
        "source": source,
        "message": message,
        "progress": progress
    }
    if stage:
        entry["stage"] = stage
    if error_type:
        entry["error_type"] = error_type
    for key, value in extra.items():
        if value is not None:
            entry[key] = value
    await manager.broadcast(task_id, entry)
    print(f"[{source.upper()}] [{level.upper()}] {message}")
