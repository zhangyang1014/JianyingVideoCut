"""
GoldenClip Task Storage Service
Simple JSON-based local persistence for tasks.
Design: 暗金剪辑台 · 编导美学
"""

import json
import os
import asyncio
from typing import List, Optional, Dict
from datetime import datetime
from pathlib import Path

from ..models.task import Task, TaskStatus

DATA_DIR = Path(__file__).parent.parent / "data"
TASKS_FILE = DATA_DIR / "tasks.json"
UPLOADS_DIR = Path(__file__).parent.parent.parent / "uploads"
EXPORTS_DIR = Path(__file__).parent.parent.parent / "exports"


def ensure_dirs():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)


class TaskStore:
    def __init__(self):
        ensure_dirs()
        self._tasks: Dict[str, Task] = {}
        self._load()

    # 后端重启后这些状态的后台任务已不存在，需要回退到安全状态
    _INTERRUPTED_STATUS_MAP = {
        TaskStatus.ASR_RUNNING:    TaskStatus.PENDING,
        TaskStatus.AUDIT_RUNNING:  TaskStatus.ASR_DONE,
        TaskStatus.EXPORT_RUNNING: TaskStatus.REVIEW,
    }

    def _load(self):
        if TASKS_FILE.exists():
            try:
                with open(TASKS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    needs_save = False
                    for task_data in data:
                        task = Task(**task_data)
                        # 后端重启时，所有「运行中」状态的后台 asyncio 任务已被杀死，
                        # 将这些任务回退到合适的安全状态，避免 UI 永久转圈
                        if task.status in self._INTERRUPTED_STATUS_MAP:
                            fallback = self._INTERRUPTED_STATUS_MAP[task.status]
                            print(f"[TaskStore] 任务 {task.id[:8]}... 因重启从 {task.status} 回退至 {fallback}")
                            task.status = fallback
                            needs_save = True
                        self._tasks[task.id] = task
                    if needs_save:
                        self._save()
            except Exception as e:
                print(f"[TaskStore] Failed to load tasks: {e}")

    def _save(self):
        try:
            with open(TASKS_FILE, "w", encoding="utf-8") as f:
                tasks_list = [task.model_dump() for task in self._tasks.values()]
                json.dump(tasks_list, f, ensure_ascii=False, indent=2, default=str)
        except Exception as e:
            print(f"[TaskStore] Failed to save tasks: {e}")

    def get_all(self) -> List[Task]:
        return sorted(
            self._tasks.values(),
            key=lambda t: t.created_at,
            reverse=True
        )

    def get(self, task_id: str) -> Optional[Task]:
        return self._tasks.get(task_id)

    def create(self, task: Task) -> Task:
        self._tasks[task.id] = task
        self._save()
        return task

    def update(self, task: Task) -> Task:
        task.updated_at = datetime.now().isoformat()
        self._tasks[task.id] = task
        self._save()
        return task

    def delete(self, task_id: str) -> bool:
        if task_id in self._tasks:
            del self._tasks[task_id]
            self._save()
            return True
        return False

    def update_status(self, task_id: str, status: TaskStatus, error: str = None) -> Optional[Task]:
        task = self.get(task_id)
        if task:
            task.status = status
            if error:
                task.error_message = error
            return self.update(task)
        return None


# Global singleton
_store: Optional[TaskStore] = None


def get_store() -> TaskStore:
    global _store
    if _store is None:
        _store = TaskStore()
    return _store
