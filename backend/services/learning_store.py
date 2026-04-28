"""
GoldenClip 学习任务存储
复用 TaskStore 的 JSON 持久化模式，管理学习任务的增删改查。
"""

import json
from typing import List, Optional, Dict
from datetime import datetime
from pathlib import Path

from ..models.task import LearningTask, LearningTaskStatus

DATA_DIR = Path(__file__).parent.parent / "data"
LEARNING_TASKS_FILE = DATA_DIR / "learning_tasks.json"


class LearningStore:
    def __init__(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._tasks: Dict[str, LearningTask] = {}
        self._load()

    def _load(self):
        if LEARNING_TASKS_FILE.exists():
            try:
                with open(LEARNING_TASKS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for task_data in data:
                        task = LearningTask(**task_data)
                        self._tasks[task.id] = task
            except Exception as e:
                print(f"[LearningStore] 加载学习任务失败: {e}")

    def _save(self):
        try:
            with open(LEARNING_TASKS_FILE, "w", encoding="utf-8") as f:
                tasks_list = [task.model_dump() for task in self._tasks.values()]
                json.dump(tasks_list, f, ensure_ascii=False, indent=2, default=str)
        except Exception as e:
            print(f"[LearningStore] 保存学习任务失败: {e}", flush=True)
            raise

    def get_all(self) -> List[LearningTask]:
        return sorted(
            self._tasks.values(),
            key=lambda t: t.created_at,
            reverse=True,
        )

    def get(self, task_id: str) -> Optional[LearningTask]:
        return self._tasks.get(task_id)

    def create(self, task: LearningTask) -> LearningTask:
        self._tasks[task.id] = task
        self._save()
        return task

    def update(self, task: LearningTask) -> LearningTask:
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

    def update_status(
        self, task_id: str, status: LearningTaskStatus, error: str = None
    ) -> Optional[LearningTask]:
        task = self.get(task_id)
        if task:
            task.status = status
            if error:
                task.error_message = error
            return self.update(task)
        return None


_store: Optional[LearningStore] = None


def get_learning_store() -> LearningStore:
    global _store
    if _store is None:
        _store = LearningStore()
    return _store
