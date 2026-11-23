"""统一后台任务注册表与取消回滚逻辑。注册表 dict 为全局单例，各 router 共享。"""

import asyncio
import contextvars
import inspect
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Awaitable, Callable, Dict, Optional, TypeVar
import uuid

from ..models.task import (
    ExecutionError,
    ExecutionRun,
    ExportJobStatus,
    RetryState,
    TaskStatus,
)
from ..services.ffmpeg_executor import is_export_process_alive
from ..services.task_store import get_store
from .ws import manager, log_to_ws

T = TypeVar("T")


@dataclass(frozen=True)
class RetryDecision:
    retryable: bool
    reason: str
    status_code: Optional[int] = None
    retry_after_seconds: Optional[float] = None
    on_fail: Optional[str] = None


@dataclass
class RetryContext:
    """顶层调用拥有预算；嵌套 execute_with_retry 自动复用且不再开新预算。"""
    owner: str = field(default_factory=lambda: str(uuid.uuid4()))
    retry_state: RetryState = field(default_factory=RetryState)
    attempts: int = 0

    @property
    def retries_used(self) -> int:
        return self.retry_state.retries_used

    @property
    def max_retries(self) -> int:
        return self.retry_state.max_retries


class RetryableExecutionError(RuntimeError):
    def __init__(self, message: str, *, retry_after: Optional[float] = None):
        super().__init__(message)
        self.retryable = True
        self.retry_after = retry_after


class PermanentExecutionError(RuntimeError):
    retryable = False


_active_retry_context: contextvars.ContextVar[Optional[RetryContext]] = contextvars.ContextVar(
    "goldenclip_retry_context",
    default=None,
)
_active_execution_binding: contextvars.ContextVar[Optional[tuple[str, ExecutionRun]]] = contextvars.ContextVar(
    "goldenclip_execution_binding",
    default=None,
)


def current_retry_context() -> Optional[RetryContext]:
    return _active_retry_context.get()


def current_execution_binding() -> Optional[tuple[str, ExecutionRun]]:
    """Return the task/run currently owning this async execution context."""
    return _active_execution_binding.get()


def _status_code_from_exception(exc: BaseException) -> Optional[int]:
    status_code = getattr(exc, "status_code", None)
    if status_code is None:
        status_code = getattr(getattr(exc, "response", None), "status_code", None)
    try:
        return int(status_code) if status_code is not None else None
    except (TypeError, ValueError):
        return None


def _retry_after_from_exception(exc: BaseException) -> Optional[float]:
    explicit = getattr(exc, "retry_after", None)
    if explicit is None:
        explicit = getattr(exc, "retry_after_seconds", None)
    if explicit is not None:
        try:
            return max(0.0, float(explicit))
        except (TypeError, ValueError):
            pass

    headers = getattr(exc, "headers", None)
    if headers is None:
        headers = getattr(getattr(exc, "response", None), "headers", None)
    if not headers:
        return None
    value = headers.get("Retry-After") or headers.get("retry-after")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        try:
            retry_at = parsedate_to_datetime(str(value))
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None


def classify_execution_error(exc: BaseException) -> RetryDecision:
    """统一判定 timeout/transport、HTTP 和契约拒绝是否可重试。"""
    if isinstance(exc, asyncio.CancelledError):
        return RetryDecision(False, "cancelled")
    if isinstance(exc, PermanentExecutionError):
        return RetryDecision(False, "permanent")

    on_fail = getattr(exc, "on_fail", None)
    explicit_retryable = getattr(exc, "retryable", None)
    status_code = _status_code_from_exception(exc)
    retry_after = _retry_after_from_exception(exc)

    if on_fail == "reject":
        return RetryDecision(False, "contract_reject", status_code, retry_after, on_fail)
    if on_fail == "reject_and_retry":
        return RetryDecision(True, "contract_reject_and_retry", status_code, retry_after, on_fail)
    if explicit_retryable is not None:
        return RetryDecision(bool(explicit_retryable), "explicit", status_code, retry_after, on_fail)
    if status_code is not None:
        retryable = status_code in {408, 429} or 500 <= status_code <= 599
        return RetryDecision(retryable, f"http_{status_code}", status_code, retry_after, on_fail)
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError, ConnectionError)):
        return RetryDecision(True, "timeout_or_transport", retry_after_seconds=retry_after)

    # httpx/aiohttp/requests 的 transport 异常不共享统一基类，按模块和类型名识别。
    module = type(exc).__module__.lower()
    name = type(exc).__name__.lower()
    transport_name = any(token in name for token in ("timeout", "transport", "connection"))
    if transport_name and any(token in module for token in ("httpx", "aiohttp", "requests", "urllib3")):
        return RetryDecision(True, "timeout_or_transport", retry_after_seconds=retry_after)
    return RetryDecision(False, "permanent", status_code, retry_after, on_fail)


def execution_error_from_exception(
    exc: BaseException,
    *,
    attempt: Optional[int] = None,
) -> ExecutionError:
    decision = classify_execution_error(exc)
    return ExecutionError(
        error_type=type(exc).__name__,
        message=str(exc),
        retryable=decision.retryable,
        on_fail=decision.on_fail,
        status_code=decision.status_code,
        retry_after_seconds=decision.retry_after_seconds,
        attempt=attempt,
    )


async def execute_with_retry(
    operation: Callable[[], Awaitable[T]],
    *,
    context: Optional[RetryContext] = None,
    stage: Optional[str] = None,
    max_retries: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 30.0,
    jitter_ratio: float = 0.2,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    random_fn: Callable[[], float] = random.random,
    on_retry: Optional[Callable[[RetryContext, BaseException, float], Any]] = None,
) -> T:
    """
    执行异步操作：首次失败后最多重试 max_retries 次（默认总计 4 次）。

    若在另一个 execute_with_retry 内调用，则内层只执行一次并把异常交给顶层
    owner，避免 4×4 的嵌套重试。
    """
    parent_context = _active_retry_context.get()
    if parent_context is not None:
        return await operation()

    retry_context = context or RetryContext(
        retry_state=RetryState(max_retries=max_retries),
    )
    execution_binding = _active_execution_binding.get()
    execution_stage = stage or getattr(operation, "__qualname__", getattr(operation, "__name__", "operation"))
    # 只有调用方明确声明 stage 时才把 retry/checkpoint 写回整份 Task。
    # 批量审计/集锦会并发创建大量通用 retry 子调用；若按函数名自动落盘，
    # 每个子调用都会同步序列化几十 MB 的任务文件，足以阻塞健康检查并触发误重启。
    persist_bound_execution = execution_binding is not None and stage is not None

    def persist_execution_state(
        status: str,
        *,
        exc: Optional[BaseException] = None,
        checkpoint_status: Optional[str] = None,
    ) -> None:
        if not persist_bound_execution:
            return
        task_id, run = execution_binding
        store = get_store()
        error = (
            execution_error_from_exception(exc, attempt=retry_context.attempts)
            if exc is not None
            else None
        )

        def mutate(stored_run: ExecutionRun) -> None:
            stored_run.status = status
            stored_run.current_stage = execution_stage
            stored_run.retry_state = retry_context.retry_state.model_copy(deep=True)
            stored_run.error = error

        store.update_execution_run(task_id, run.run_id, run.generation, mutate)
        if checkpoint_status:
            checkpoint_execution(
                task_id,
                run.run_id,
                run.generation,
                stage=execution_stage,
                status=checkpoint_status,
                error=error,
            )

    persist_execution_state("running", checkpoint_status="running")
    token = _active_retry_context.set(retry_context)
    try:
        while True:
            retry_context.attempts += 1
            try:
                result = await operation()
                retry_context.retry_state.next_retry_at = None
                persist_execution_state("running", checkpoint_status="succeeded")
                return result
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                decision = classify_execution_error(exc)
                if (
                    not decision.retryable
                    or retry_context.retry_state.retries_used >= retry_context.retry_state.max_retries
                ):
                    retry_context.retry_state.next_retry_at = None
                    persist_execution_state(
                        "running",
                        exc=exc,
                        checkpoint_status="failed",
                    )
                    raise

                retry_context.retry_state.retries_used += 1
                retry_index = retry_context.retry_state.retries_used - 1
                if decision.retry_after_seconds is not None:
                    delay = decision.retry_after_seconds
                else:
                    exponential = min(max_delay, base_delay * (2 ** retry_index))
                    delay = exponential * (1.0 + max(0.0, jitter_ratio) * random_fn())
                retry_context.retry_state.last_delay_seconds = delay
                retry_at = datetime.now(timezone.utc).timestamp() + delay
                retry_context.retry_state.next_retry_at = datetime.fromtimestamp(
                    retry_at,
                    tz=timezone.utc,
                ).isoformat()
                persist_execution_state(
                    "retry_wait",
                    exc=exc,
                    checkpoint_status="retry_wait",
                )
                if on_retry is not None:
                    callback_result = on_retry(retry_context, exc, delay)
                    if inspect.isawaitable(callback_result):
                        await callback_result
                await sleep(delay)
                retry_context.retry_state.next_retry_at = None
                persist_execution_state("running")
    finally:
        _active_retry_context.reset(token)

# 学习任务正在运行的 asyncio.Task 注册表，key 为 learning task_id
_learning_active_tasks: Dict[str, asyncio.Task] = {}

# 导出任务正在运行的 asyncio.Task 注册表，key 为 export_job_id
_export_tasks: Dict[str, asyncio.Task] = {}

# 统一的后台任务注册表（ASR / 语义审计 / 一键翻译），key 为 task_id。
# 导出任务走 _export_tasks（含 FFmpeg 子进程终止逻辑，key 为 export_job_id），学习任务仍走 _learning_active_tasks。
_task_runners: Dict[str, asyncio.Task] = {}
_task_runner_statuses: Dict[str, TaskStatus] = {}
_task_runner_runs: Dict[str, ExecutionRun] = {}

# 取消后各运行中状态的安全回滚目标（与 task_store._INTERRUPTED_STATUS_MAP 一致）
_CANCEL_ROLLBACK_MAP = {
    TaskStatus.ASR_RUNNING:       TaskStatus.PENDING,
    TaskStatus.DIARIZATION_RUNNING: TaskStatus.ASR_RECOGNIZED,
    TaskStatus.ANALYZE_RUNNING:   TaskStatus.ASR_RECOGNIZED,
    TaskStatus.AUDIT_RUNNING:     TaskStatus.ASR_DONE,
    TaskStatus.TRANSLATE_RUNNING: TaskStatus.PENDING,
    TaskStatus.TTS_RUNNING:       TaskStatus.PENDING,
    TaskStatus.DUBBING_RUNNING:   TaskStatus.PENDING,
}


def _resolve_cancel_rollback_status(task) -> Optional[TaskStatus]:
    """根据已有成果选择取消后的安全状态，避免重跑 ASR 被取消时隐藏旧版本。"""
    fallback = _CANCEL_ROLLBACK_MAP.get(task.status)
    active_run = next(
        (
            run
            for run in task.execution_runs
            if run.run_id == task.active_execution_run_id
        ),
        None,
    )
    if (
        task.status == TaskStatus.AUDIT_RUNNING
        and active_run is not None
        and active_run.operation.startswith("highlight_")
    ):
        return TaskStatus.REVIEW
    if task.status == TaskStatus.ASR_RUNNING:
        has_previous_asr = bool(
            task.asr_history
            or (task.asr_result and task.asr_result.words)
        )
        if has_previous_asr:
            has_diarization = bool(
                task.asr_result
                and (task.asr_result.diar_segments or task.asr_result.speakers)
            )
            return TaskStatus.ASR_DIARIZED if has_diarization else TaskStatus.ASR_RECOGNIZED
    if task.status in (TaskStatus.DIARIZATION_RUNNING, TaskStatus.ANALYZE_RUNNING):
        has_diarization = bool(
            task.asr_result
            and (task.asr_result.diar_segments or task.asr_result.speakers)
        )
        if has_diarization:
            return TaskStatus.ASR_DIARIZED
    return fallback


# 任务状态为 running 时，期望后台仍有活跃协程/进程
_EXPECTED_RUNNING_STATUSES = frozenset({
    TaskStatus.ASR_RUNNING,
    TaskStatus.DIARIZATION_RUNNING,
    TaskStatus.ANALYZE_RUNNING,
    TaskStatus.AUDIT_RUNNING,
    TaskStatus.TRANSLATE_RUNNING,
    TaskStatus.TTS_RUNNING,
    TaskStatus.DUBBING_RUNNING,
    TaskStatus.EXPORT_RUNNING,
})


def _is_asyncio_task_alive(task: Optional[asyncio.Task]) -> bool:
    return task is not None and not task.done()


def get_task_runtime_health(task_id: str, status: TaskStatus) -> dict:
    """
    诊断任务后台协程/导出进程是否仍活跃。
    仅做只读检查，不修改任务状态。
    """
    runner_alive = _is_asyncio_task_alive(_task_runners.get(task_id))
    task = get_store().get(task_id)
    active_export_job_ids = [
        job.id
        for job in (task.export_jobs if task else [])
        if job.status in (ExportJobStatus.QUEUED, ExportJobStatus.RUNNING)
    ]
    export_task_alive = any(_is_asyncio_task_alive(_export_tasks.get(job_id)) for job_id in active_export_job_ids)
    if status == TaskStatus.EXPORT_RUNNING:
        export_task_alive = export_task_alive or _is_asyncio_task_alive(_export_tasks.get(task_id))
    ffmpeg_process_alive = any(is_export_process_alive(job_id) for job_id in active_export_job_ids)
    if status == TaskStatus.EXPORT_RUNNING:
        ffmpeg_process_alive = ffmpeg_process_alive or is_export_process_alive(task_id)

    alive = runner_alive or export_task_alive or ffmpeg_process_alive
    runner_status = _task_runner_statuses.get(task_id)
    if runner_alive and runner_status:
        effective_status = runner_status
    elif runner_alive and status not in _EXPECTED_RUNNING_STATUSES:
        # Older runners may not have status metadata; ASR is the common entry state from pending.
        effective_status = TaskStatus.ASR_RUNNING
    else:
        effective_status = status
    expected_running = effective_status in _EXPECTED_RUNNING_STATUSES

    if (export_task_alive or ffmpeg_process_alive) and not runner_alive:
        message = "导出作业运行中，自检通过"
    elif not expected_running:
        message = "当前无运行中后台任务"
    elif alive:
        message = "后台任务运行中，自检通过"
    else:
        message = "疑似中断：未检测到活跃后台任务"

    result = {
        "task_id": task_id,
        "status": status,
        "effective_status": effective_status,
        "expected_running": expected_running,
        "alive": alive,
        "runner_alive": runner_alive,
        "export_task_alive": export_task_alive,
        "ffmpeg_process_alive": ffmpeg_process_alive,
        "checked_at": datetime.now().isoformat(),
        "message": message,
    }
    return result


def _start_task_runner(
    task_id: str,
    coro,
    running_status: Optional[TaskStatus] = None,
    *,
    operation: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    input_fingerprint: Optional[str] = None,
    max_retries: int = 3,
) -> asyncio.Task:
    """
    启动并登记一个任务级后台协程。

    同一 task_id 已有运行中的协程时，先取消旧协程再启动新协程
    （旧协程的 CancelledError 处理器会发现自己已被替换而跳过状态回滚）。
    协程结束后自动从注册表移除。可选执行参数用于持久化、幂等去重和
    generation 写屏障；旧调用签名保持兼容。
    """
    store = get_store()
    run: Optional[ExecutionRun] = None
    start_execution = getattr(store, "start_execution", None)
    if callable(start_execution):
        try:
            run, created = start_execution(
                task_id,
                operation=operation or (running_status.value if running_status else "task"),
                idempotency_key=idempotency_key,
                input_fingerprint=input_fingerprint,
                max_retries=max_retries,
            )
        except KeyError:
            # 单元测试或遗留调用可能先登记协程、后创建持久化 Task。
            created = True
        if run is not None and not created:
            existing_runner = _task_runners.get(task_id)
            existing_run = _task_runner_runs.get(task_id)
            close = getattr(coro, "close", None)
            if callable(close):
                close()
            if (
                existing_runner is not None
                and not existing_runner.done()
                and existing_run is not None
                and existing_run.run_id == run.run_id
            ):
                return existing_runner

            async def _deduplicated_result():
                return run

            return asyncio.create_task(_deduplicated_result())

    old = _task_runners.pop(task_id, None)
    _task_runner_statuses.pop(task_id, None)
    _task_runner_runs.pop(task_id, None)
    if old and not old.done():
        old.cancel()

    async def _run_with_execution_lifecycle():
        execution_token = (
            _active_execution_binding.set((task_id, run))
            if run is not None
            else None
        )
        try:
            result = await coro
        except asyncio.CancelledError:
            if run is not None:
                _finish_execution(task_id, run, "cancelled")
            raise
        except Exception as exc:
            if run is not None:
                _finish_execution(task_id, run, "failed", exc)
            raise
        else:
            if run is not None:
                current = asyncio.current_task()
                task = store.get(task_id)
                cancelling = getattr(current, "cancelling", None)
                cancel_requested = bool(cancelling()) if callable(cancelling) else False
                if cancel_requested:
                    _finish_execution(task_id, run, "cancelled")
                elif task is not None and task.status == TaskStatus.ERROR:
                    error = PermanentExecutionError(task.error_message or "任务执行失败")
                    _finish_execution(task_id, run, "failed", error)
                else:
                    _finish_execution(task_id, run, "succeeded")
            return result
        finally:
            if execution_token is not None:
                _active_execution_binding.reset(execution_token)

    t = asyncio.create_task(_run_with_execution_lifecycle())
    _task_runners[task_id] = t
    if run is not None:
        _task_runner_runs[task_id] = run
    if running_status:
        _task_runner_statuses[task_id] = running_status

    def _cleanup(done_task: asyncio.Task):
        if _task_runners.get(task_id) is done_task:
            _task_runners.pop(task_id, None)
            _task_runner_statuses.pop(task_id, None)
            _task_runner_runs.pop(task_id, None)

    t.add_done_callback(_cleanup)
    return t


def _finish_execution(
    task_id: str,
    run: ExecutionRun,
    status: str,
    exc: Optional[BaseException] = None,
) -> bool:
    """持久化 run 最终状态；generation 过期时 TaskStore 会拒绝写入。"""
    store = get_store()
    task = store.get(task_id)
    if (
        task is not None
        and task.execution_generation == run.generation
        and task.active_execution_run_id == run.run_id
    ):
        persisted = next(
            (item for item in task.execution_runs if item.run_id == run.run_id),
            None,
        )
        if persisted is not None:
            run = persisted.model_copy(deep=True)
    run.status = status
    run.retry_state.next_retry_at = None
    run.completed_at = datetime.now(timezone.utc).isoformat()
    if exc is not None:
        run.error = execution_error_from_exception(exc)
    elif status == "succeeded":
        run.error = None
    if status == "interrupted":
        run.recoverable = True
    elif status == "failed":
        decision = classify_execution_error(exc) if exc is not None else RetryDecision(False, "unknown")
        run.recoverable = decision.retryable or any(
            checkpoint.status == "succeeded" for checkpoint in run.stages
        )
    else:
        run.recoverable = False
    save_execution_run = getattr(store, "save_execution_run", None)
    if not callable(save_execution_run):
        return False
    return bool(save_execution_run(task_id, run, final=True))


def checkpoint_execution(
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
    """task_runner 层的 checkpoint 便捷入口。"""
    store_checkpoint = getattr(get_store(), "checkpoint_execution", None)
    if not callable(store_checkpoint):
        return False
    return bool(
        store_checkpoint(
            task_id,
            run_id,
            generation,
            stage=stage,
            status=status,
            artifact_ref=artifact_ref,
            artifact_digest=artifact_digest,
            error=error,
        )
    )


def mark_current_execution_failed(task_id: str, exc: BaseException) -> bool:
    """供会吞掉业务异常的路由显式结束当前 execution，避免被误标为成功。"""
    binding = _active_execution_binding.get()
    if binding is None:
        return False
    bound_task_id, run = binding
    if bound_task_id != task_id:
        return False
    return _finish_execution(task_id, run, "failed", exc)


async def execute_task_with_retry(
    task_id: str,
    run: ExecutionRun,
    operation: Callable[[], Awaitable[T]],
    *,
    stage: Optional[str] = None,
    base_delay: float = 0.5,
    max_delay: float = 30.0,
    jitter_ratio: float = 0.2,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    random_fn: Callable[[], float] = random.random,
) -> T:
    """执行并同步持久化 retry/checkpoint 状态的组合 helper。"""
    context = RetryContext(owner=run.run_id, retry_state=run.retry_state)

    def persist_run_state(status: str, error: Optional[ExecutionError]) -> None:
        update_execution_run = getattr(get_store(), "update_execution_run", None)
        if not callable(update_execution_run):
            return

        def mutate(stored_run: ExecutionRun) -> None:
            stored_run.status = status
            stored_run.retry_state = run.retry_state.model_copy(deep=True)
            stored_run.error = error

        update_execution_run(task_id, run.run_id, run.generation, mutate)

    if stage:
        checkpoint_execution(
            task_id,
            run.run_id,
            run.generation,
            stage=stage,
            status="running",
        )

    def persist_retry(
        retry_context: RetryContext,
        exc: BaseException,
        _delay: float,
    ) -> None:
        run.status = "retry_wait"
        run.error = execution_error_from_exception(exc, attempt=retry_context.attempts)
        persist_run_state(run.status, run.error)
        if stage:
            checkpoint_execution(
                task_id,
                run.run_id,
                run.generation,
                stage=stage,
                status="retry_wait",
                error=run.error,
            )

    try:
        result = await execute_with_retry(
            operation,
            context=context,
            base_delay=base_delay,
            max_delay=max_delay,
            jitter_ratio=jitter_ratio,
            sleep=sleep,
            random_fn=random_fn,
            on_retry=persist_retry,
        )
    except asyncio.CancelledError:
        if stage:
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
        run.status = "failed"
        run.error = execution_error_from_exception(exc, attempt=context.attempts)
        if stage:
            checkpoint_execution(
                task_id,
                run.run_id,
                run.generation,
                stage=stage,
                status="failed",
                error=run.error,
            )
        _finish_execution(task_id, run, "failed", exc)
        raise
    else:
        run.status = "running"
        run.error = None
        persist_run_state(run.status, None)
        if stage:
            checkpoint_execution(
                task_id,
                run.run_id,
                run.generation,
                stage=stage,
                status="succeeded",
            )
        return result


def _task_runner_superseded(task_id: str) -> bool:
    """当前协程是否已被同一 task_id 的新协程替换（重复触发场景）。"""
    current = _task_runners.get(task_id)
    return current is not None and current is not asyncio.current_task()


async def _rollback_cancelled_task(task_id: str, fallback: TaskStatus, source: str, label: str):
    """取消后的统一善后：回滚状态、落盘、广播取消消息和 status_change。"""
    run = _task_runner_runs.get(task_id)
    if run is not None:
        _finish_execution(task_id, run, "cancelled")
    task = get_store().get(task_id)
    if task:
        task.status = fallback
        get_store().update(task)
    await log_to_ws(task_id, "warn", source, f"⚠️ {label}已被用户提前终止，状态已回滚")
    await manager.broadcast(task_id, {
        "type": "task_cancelled",
        "task_id": task_id,
        "status": fallback,
    })
    await manager.broadcast(task_id, {"type": "status_change", "task_id": task_id, "status": fallback})


async def shutdown_background_tasks(timeout: float = 10.0) -> None:
    """应用退出时取消所有受管后台协程，避免 reload/重启等待悬挂任务。"""
    current = asyncio.current_task()
    tasks = {
        task
        for task in (
            *_task_runners.values(),
            *_export_tasks.values(),
            *_learning_active_tasks.values(),
        )
        if task is not current and not task.done()
    }
    for task in tasks:
        task.cancel()
    if tasks:
        done, pending = await asyncio.wait(tasks, timeout=timeout)
        for task in done:
            try:
                task.result()
            except (asyncio.CancelledError, Exception):
                pass
        for task in pending:
            task.cancel()

    _task_runners.clear()
    _task_runner_statuses.clear()
    _task_runner_runs.clear()
    _export_tasks.clear()
    _learning_active_tasks.clear()


def _cancel_learning_task(task_id: str):
    """取消某个学习任务关联的后台 asyncio.Task（如有）。"""
    task = _learning_active_tasks.pop(task_id, None)
    if task and not task.done():
        task.cancel()
