"""
GoldenClip Task Storage Service
Per-task JSON file persistence with atomic writes.
Design: 暗金剪辑台 · 编导美学

存储结构：backend/data/tasks/{task_id}.json，每次写入先落临时文件再 os.replace()，
避免崩溃时损坏全部数据。首次启动时自动从旧的 tasks.json 迁移（旧文件保留为 tasks.json.bak）。
"""

import json
import os
import tempfile
import threading
from typing import Callable, Dict, List, Optional, Tuple
from datetime import datetime
from pathlib import Path

from ..models.task import (
    ExecutionError,
    ExecutionRun,
    ExportJobStatus,
    RetryState,
    StageCheckpoint,
    Task,
    TaskStatus,
)

DATA_DIR = Path(__file__).parent.parent / "data"
TASKS_FILE = DATA_DIR / "tasks.json"  # 旧的全量存储文件，仅用于迁移
TASKS_DIR = DATA_DIR / "tasks"
UPLOADS_DIR = Path(__file__).parent.parent.parent / "uploads"
EXPORTS_DIR = Path(__file__).parent.parent.parent / "exports"


def ensure_dirs():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    TASKS_DIR.mkdir(parents=True, exist_ok=True)
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)


def _atomic_write_json(path: Path, payload) -> None:
    """唯一临时文件写入并 fsync，再原子替换目标文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Optional[Path] = None
    try:
        model_dump_json = getattr(payload, "model_dump_json", None)
        if callable(model_dump_json):
            # Pydantic v2 的 Rust serializer 显著快于 model_dump + json.dump，
            # 并使用紧凑 JSON，降低大任务每次 checkpoint 的落盘成本。
            serialized = model_dump_json()
        else:
            serialized = json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                default=str,
            )
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as f:
            tmp_path = Path(f.name)
            f.write(serialized)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
        # 尽力同步目录项；某些测试文件系统/平台不支持目录 fsync。
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
    except Exception:
        # 写失败时清掉残留的临时文件，再向上抛出由调用方决定如何处理
        if tmp_path is not None:
            try:
                tmp_path.unlink()
            except FileNotFoundError:
                pass
        raise


class RevisionConflictError(RuntimeError):
    """TaskStore CAS 更新发现版本不一致。"""


class TaskStore:
    def __init__(self):
        ensure_dirs()
        self._tasks: Dict[str, Task] = {}
        self._locks_guard = threading.Lock()
        self._task_locks: Dict[str, threading.RLock] = {}
        self._load()

    # 后端重启后这些状态的后台任务已不存在，需要回退到安全状态
    _INTERRUPTED_STATUS_MAP = {
        TaskStatus.ASR_RUNNING:    TaskStatus.PENDING,
        TaskStatus.DIARIZATION_RUNNING: TaskStatus.ASR_RECOGNIZED,
        TaskStatus.ANALYZE_RUNNING: TaskStatus.ASR_RECOGNIZED,
        TaskStatus.AUDIT_RUNNING:  TaskStatus.ASR_DONE,
        TaskStatus.TRANSLATE_RUNNING: TaskStatus.PENDING,
        TaskStatus.TTS_RUNNING:       TaskStatus.PENDING,
        TaskStatus.DUBBING_RUNNING:   TaskStatus.PENDING,
        TaskStatus.EXPORT_RUNNING: TaskStatus.REVIEW,
    }

    def _load(self):
        self._migrate_legacy_file()

        task_files = sorted(TASKS_DIR.glob("*.json"))
        for task_file in task_files:
            try:
                with open(task_file, "r", encoding="utf-8") as f:
                    task = Task(**json.load(f))
            except Exception as e:
                print(f"[TaskStore] 跳过损坏的任务文件 {task_file.name}: {e}")
                continue

            # 后端重启时，所有「运行中」状态的后台 asyncio 任务已被杀死，
            # 将这些任务回退到合适的安全状态，避免 UI 永久转圈
            interrupted_task_status = task.status in self._INTERRUPTED_STATUS_MAP
            if interrupted_task_status:
                fallback = self._INTERRUPTED_STATUS_MAP[task.status]
                if task.status in (TaskStatus.DIARIZATION_RUNNING, TaskStatus.ANALYZE_RUNNING):
                    if task.asr_result and (task.asr_result.diar_segments or task.asr_result.speakers):
                        fallback = TaskStatus.ASR_DIARIZED
                print(f"[TaskStore] 任务 {task.id[:8]}... 因重启从 {task.status} 回退至 {fallback}")
                task.status = fallback
            interrupted_exports = False
            for job in task.export_jobs:
                if job.status in (ExportJobStatus.QUEUED, ExportJobStatus.RUNNING):
                    job.status = ExportJobStatus.FAILED
                    job.error_message = "服务重启后导出已中断"
                    job.completed_at = datetime.now().isoformat()
                    job.updated_at = job.completed_at
                    interrupted_exports = True
            interrupted_execution = False
            now = datetime.now().isoformat()
            for run in task.execution_runs:
                if run.status in {"pending", "running", "retry_wait"}:
                    run.status = "interrupted"
                    run.recoverable = True
                    run.completed_at = None
                    run.updated_at = now
                    run.error = ExecutionError(
                        error_type="ServiceRestart",
                        message="服务重启导致执行中断，可从已有 checkpoint 继续",
                        retryable=True,
                    )
                    for stage in run.stages:
                        if stage.status in {"running", "retry_wait"}:
                            stage.status = "interrupted"
                            stage.updated_at = now
                    interrupted_execution = True
            if interrupted_execution:
                task.active_execution_run_id = None
            if interrupted_task_status or interrupted_exports or interrupted_execution:
                self._save_task(task)
            self._tasks[task.id] = task

    def _migrate_legacy_file(self):
        """旧的全量 tasks.json 存在时，把其中任务迁移为分文件结构，并保留 .bak 备份。"""
        if not TASKS_FILE.exists():
            return
        try:
            with open(TASKS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            for task_data in data:
                task = Task(**task_data)
                task_path = TASKS_DIR / f"{task.id}.json"
                if not task_path.exists():
                    _atomic_write_json(task_path, task.model_dump())
        except Exception as e:
            print(f"[TaskStore] 迁移旧 tasks.json 失败，保留原文件: {e}")
            return
        backup_path = TASKS_FILE.parent / (TASKS_FILE.name + ".bak")
        try:
            os.replace(TASKS_FILE, backup_path)
            print(f"[TaskStore] 已迁移 {len(data)} 个任务至分文件存储，旧文件备份为 {backup_path.name}")
        except Exception as e:
            print(f"[TaskStore] 备份旧 tasks.json 失败: {e}")

    def _task_path(self, task_id: str) -> Path:
        return TASKS_DIR / f"{task_id}.json"

    def _lock_for(self, task_id: str) -> threading.RLock:
        with self._locks_guard:
            lock = self._task_locks.get(task_id)
            if lock is None:
                lock = threading.RLock()
                self._task_locks[task_id] = lock
            return lock

    def _save_task(self, task: Task):
        TASKS_DIR.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(self._task_path(task.id), task)

    def _delete_task_file(self, task_id: str):
        try:
            self._task_path(task_id).unlink()
        except FileNotFoundError:
            pass
        except Exception as e:
            print(f"[TaskStore] Failed to delete task file {task_id}: {e}")

    def get_all(self) -> List[Task]:
        return sorted(
            self._tasks.values(),
            key=lambda t: t.created_at,
            reverse=True
        )

    def get(self, task_id: str) -> Optional[Task]:
        return self._tasks.get(task_id)

    def create(self, task: Task) -> Task:
        with self._lock_for(task.id):
            self._save_task(task)
            self._tasks[task.id] = task
            return task

    def update(self, task: Task, *, expected_revision: Optional[int] = None) -> Task:
        """兼容原 update(task)，可选 expected_revision 提供 CAS。"""
        with self._lock_for(task.id):
            current = self._tasks.get(task.id)
            current_revision = current.revision if current is not None else task.revision
            if expected_revision is not None and current_revision != expected_revision:
                raise RevisionConflictError(
                    f"task {task.id} revision conflict: expected {expected_revision}, actual {current_revision}"
                )

            # 业务流水线常持有较早读取的 Task 对象；统一重试器会并发写 execution
            # checkpoint。旧对象回写时必须保留更新后的执行元数据，避免断点被覆盖。
            if current is not None and task.revision < current_revision:
                task.execution_generation = current.execution_generation
                task.active_execution_run_id = current.active_execution_run_id
                task.execution_runs = [
                    run.model_copy(deep=True) for run in current.execution_runs
                ]

            previous_revision = task.revision
            previous_updated_at = task.updated_at
            task.revision = current_revision + 1
            task.updated_at = datetime.now().isoformat()
            try:
                self._save_task(task)
            except Exception:
                task.revision = previous_revision
                task.updated_at = previous_updated_at
                raise
            self._tasks[task.id] = task
            return task

    def delete(self, task_id: str) -> bool:
        with self._lock_for(task_id):
            if task_id in self._tasks:
                del self._tasks[task_id]
                self._delete_task_file(task_id)
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

    def start_execution(
        self,
        task_id: str,
        *,
        operation: str,
        idempotency_key: Optional[str] = None,
        input_fingerprint: Optional[str] = None,
        max_retries: int = 3,
    ) -> Tuple[ExecutionRun, bool]:
        """
        创建并持久化新 run。

        同 operation + idempotency_key + input_fingerprint 的运行中或已成功 run
        会直接复用，返回 (run, False)；新建时返回 (run, True)。
        """
        with self._lock_for(task_id):
            stored_task = self._tasks.get(task_id)
            if stored_task is None:
                raise KeyError(f"task not found: {task_id}")
            task = stored_task.model_copy(deep=True)

            if idempotency_key:
                for existing in reversed(task.execution_runs):
                    if (
                        existing.operation == operation
                        and existing.idempotency_key == idempotency_key
                        and existing.input_fingerprint == input_fingerprint
                        and existing.status in {"pending", "running", "retry_wait", "succeeded"}
                    ):
                        return existing.model_copy(deep=True), False

            task.execution_generation += 1
            now = datetime.now().isoformat()
            run = ExecutionRun(
                generation=task.execution_generation,
                operation=operation,
                idempotency_key=idempotency_key,
                input_fingerprint=input_fingerprint,
                status="running",
                retry_state=RetryState(max_retries=max_retries),
                started_at=now,
                updated_at=now,
            )
            task.execution_runs.append(run.model_copy(deep=True))
            task.active_execution_run_id = run.run_id
            self.update(task)
            return run, True

    def save_execution_run(
        self,
        task_id: str,
        run: ExecutionRun,
        *,
        final: bool = False,
    ) -> bool:
        """generation/CAS 写屏障；旧 run 的更新（尤其最终状态）直接拒绝。"""
        with self._lock_for(task_id):
            stored_task = self._tasks.get(task_id)
            if (
                stored_task is None
                or run.generation != stored_task.execution_generation
                or stored_task.active_execution_run_id != run.run_id
            ):
                return False
            task = stored_task.model_copy(deep=True)
            for index, existing in enumerate(task.execution_runs):
                if existing.run_id == run.run_id:
                    run.updated_at = datetime.now().isoformat()
                    task.execution_runs[index] = run
                    if final or run.status in {"succeeded", "failed", "cancelled"}:
                        task.active_execution_run_id = None
                        if run.completed_at is None:
                            run.completed_at = run.updated_at
                    self.update(task)
                    return True
            return False

    def update_execution_run(
        self,
        task_id: str,
        run_id: str,
        generation: int,
        mutate: Callable[[ExecutionRun], None],
        *,
        final: bool = False,
    ) -> bool:
        """在 task 锁内修改当前 run，供 checkpoint/retry 状态持久化。"""
        with self._lock_for(task_id):
            stored_task = self._tasks.get(task_id)
            if (
                stored_task is None
                or stored_task.execution_generation != generation
                or stored_task.active_execution_run_id != run_id
            ):
                return False
            task = stored_task.model_copy(deep=True)
            run = next((item for item in task.execution_runs if item.run_id == run_id), None)
            if run is None:
                return False
            mutate(run)
            return self.save_execution_run(task_id, run, final=final)

    def checkpoint_execution(
        self,
        task_id: str,
        run_id: str,
        generation: int,
        *,
        stage: str,
        status: str,
        artifact_ref: Optional[str] = None,
        artifact_digest: Optional[str] = None,
        error: Optional[ExecutionError] = None,
    ) -> bool:
        """新增或更新阶段断点；只接受产物 ref/digest。"""
        def mutate(run: ExecutionRun) -> None:
            now = datetime.now().isoformat()
            checkpoint = next((item for item in run.stages if item.stage == stage), None)
            if checkpoint is None:
                checkpoint = StageCheckpoint(stage=stage)
                run.stages.append(checkpoint)
            checkpoint.status = status
            checkpoint.updated_at = now
            checkpoint.error = error
            if artifact_ref is not None:
                checkpoint.artifact_ref = artifact_ref
            if artifact_digest is not None:
                checkpoint.artifact_digest = artifact_digest
            if status == "running":
                checkpoint.attempts += 1
                checkpoint.started_at = checkpoint.started_at or now
            if status in {"succeeded", "failed", "cancelled"}:
                checkpoint.completed_at = now
            run.current_stage = stage

        return self.update_execution_run(task_id, run_id, generation, mutate)


# Global singleton
_store: Optional[TaskStore] = None


def get_store() -> TaskStore:
    global _store
    if _store is None:
        _store = TaskStore()
    return _store
