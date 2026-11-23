"""统一执行协议：重试预算、取消、generation 写屏障与幂等测试。"""

import asyncio

import pytest

from backend.agent_runtime.contracts import ContractIssue, ContractReport
from backend.agent_runtime.harness import ContractVerificationError, verify_contract_report
from backend.core import task_runner as task_runner_module
from backend.core.task_runner import (
    PermanentExecutionError,
    RetryableExecutionError,
    execute_task_with_retry,
    execute_with_retry,
)
from backend.models.task import Task
from backend.services import task_store as task_store_module
from backend.services.task_store import TaskStore


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    monkeypatch.setattr(task_store_module, "DATA_DIR", data_dir)
    monkeypatch.setattr(task_store_module, "TASKS_FILE", data_dir / "tasks.json")
    monkeypatch.setattr(task_store_module, "TASKS_DIR", data_dir / "tasks")
    monkeypatch.setattr(task_store_module, "UPLOADS_DIR", tmp_path / "uploads")
    monkeypatch.setattr(task_store_module, "EXPORTS_DIR", tmp_path / "exports")
    return TaskStore()


@pytest.mark.asyncio
async def test_first_two_failures_then_success():
    attempts = 0
    sleeps = []

    async def operation():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise RetryableExecutionError("temporary")
        return "ok"

    async def fake_sleep(delay):
        sleeps.append(delay)

    result = await execute_with_retry(
        operation,
        sleep=fake_sleep,
        random_fn=lambda: 0.0,
    )

    assert result == "ok"
    assert attempts == 3
    assert sleeps == [0.5, 1.0]


@pytest.mark.asyncio
async def test_three_retries_exhausted_means_four_total_attempts():
    attempts = 0

    async def operation():
        nonlocal attempts
        attempts += 1
        raise TimeoutError("still unavailable")

    with pytest.raises(TimeoutError):
        await execute_with_retry(
            operation,
            sleep=lambda _delay: asyncio.sleep(0),
            random_fn=lambda: 0.0,
        )

    assert attempts == 4


@pytest.mark.asyncio
async def test_permanent_error_fails_immediately():
    attempts = 0

    async def operation():
        nonlocal attempts
        attempts += 1
        raise PermanentExecutionError("bad input")

    with pytest.raises(PermanentExecutionError):
        await execute_with_retry(operation)

    assert attempts == 1


@pytest.mark.asyncio
async def test_retry_after_overrides_backoff():
    attempts = 0
    sleeps = []

    class TooManyRequests(Exception):
        status_code = 429
        headers = {"Retry-After": "7"}

    async def operation():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise TooManyRequests("rate limited")
        return "ok"

    async def fake_sleep(delay):
        sleeps.append(delay)

    assert await execute_with_retry(operation, sleep=fake_sleep) == "ok"
    assert sleeps == [7.0]


@pytest.mark.asyncio
async def test_cancelled_operation_is_not_retried():
    started = asyncio.Event()
    attempts = 0

    async def operation():
        nonlocal attempts
        attempts += 1
        started.set()
        await asyncio.Event().wait()

    runner = asyncio.create_task(execute_with_retry(operation))
    await started.wait()
    runner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await runner

    assert attempts == 1


@pytest.mark.asyncio
async def test_nested_retry_does_not_multiply_budget():
    attempts = 0

    async def inner():
        nonlocal attempts
        attempts += 1
        raise RetryableExecutionError("nested failure")

    async def outer():
        return await execute_with_retry(inner)

    with pytest.raises(RetryableExecutionError):
        await execute_with_retry(
            outer,
            sleep=lambda _delay: asyncio.sleep(0),
            random_fn=lambda: 0.0,
        )

    assert attempts == 4


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("on_fail", "retryable"),
    [("reject", False), ("reject_and_retry", True)],
)
async def test_contract_error_exposes_retry_policy(monkeypatch, on_fail, retryable):
    monkeypatch.setattr("backend.agent_runtime.harness.append_trace", lambda *args, **kwargs: None)
    monkeypatch.setattr("backend.agent_runtime.harness.emit_agent_hook", lambda *args, **kwargs: None)
    report = ContractReport(
        contract_id="test.contract",
        passed=False,
        issues=[
            ContractIssue(
                assertion_id="T-01",
                severity="blocker",
                message="invalid",
                on_fail=on_fail,
            )
        ],
    )

    with pytest.raises(ContractVerificationError) as captured:
        await verify_contract_report("task", "phase", report)

    assert captured.value.on_fail == on_fail
    assert captured.value.retryable is retryable


@pytest.mark.asyncio
async def test_start_task_runner_deduplicates_active_idempotency_key(isolated_store, monkeypatch):
    task = isolated_store.create(Task(name="runner-idempotency"))
    release = asyncio.Event()
    calls = 0

    async def operation():
        nonlocal calls
        calls += 1
        await release.wait()

    monkeypatch.setattr(task_runner_module, "get_store", lambda: isolated_store)
    task_runner_module._task_runners.clear()
    task_runner_module._task_runner_statuses.clear()
    task_runner_module._task_runner_runs.clear()

    first = task_runner_module._start_task_runner(
        task.id,
        operation(),
        operation="audit",
        idempotency_key="request-1",
        input_fingerprint="sha256:abc",
    )
    await asyncio.sleep(0)
    duplicate = task_runner_module._start_task_runner(
        task.id,
        operation(),
        operation="audit",
        idempotency_key="request-1",
        input_fingerprint="sha256:abc",
    )

    assert duplicate is first
    assert calls == 1
    release.set()
    await first
    assert isolated_store.get(task.id).execution_runs[-1].status == "succeeded"


@pytest.mark.asyncio
async def test_runner_observes_nested_retry_and_persists_checkpoint(isolated_store, monkeypatch):
    task = isolated_store.create(Task(name="runner-retry-observer"))
    attempts = 0
    release_retry = asyncio.Event()

    async def operation():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RetryableExecutionError("temporary")
        return "ok"

    async def no_wait(_delay):
        release_retry.set()
        await asyncio.sleep(0)

    async def pipeline():
        return await execute_with_retry(
            operation,
            stage="audit:chunk-1",
            sleep=no_wait,
            random_fn=lambda: 0.0,
        )

    monkeypatch.setattr(task_runner_module, "get_store", lambda: isolated_store)
    task_runner_module._task_runners.clear()
    task_runner_module._task_runner_statuses.clear()
    task_runner_module._task_runner_runs.clear()

    runner = task_runner_module._start_task_runner(
        task.id,
        pipeline(),
        operation="audit",
    )
    await release_retry.wait()
    assert isolated_store.get(task.id).execution_runs[-1].retry_state.retries_used == 1

    assert await runner == "ok"
    stored_run = isolated_store.get(task.id).execution_runs[-1]
    assert stored_run.status == "succeeded"
    assert stored_run.current_stage == "audit:chunk-1"
    assert stored_run.stages[0].status == "succeeded"


@pytest.mark.asyncio
async def test_runner_does_not_persist_implicit_child_retry_stage(isolated_store, monkeypatch):
    task = isolated_store.create(Task(name="runner-bulk-child"))
    update_calls = 0
    original_update_execution_run = isolated_store.update_execution_run

    def tracked_update_execution_run(*args, **kwargs):
        nonlocal update_calls
        update_calls += 1
        return original_update_execution_run(*args, **kwargs)

    async def pipeline():
        return await execute_with_retry(lambda: asyncio.sleep(0, result="ok"))

    monkeypatch.setattr(task_runner_module, "get_store", lambda: isolated_store)
    monkeypatch.setattr(isolated_store, "update_execution_run", tracked_update_execution_run)
    task_runner_module._task_runners.clear()
    task_runner_module._task_runner_statuses.clear()
    task_runner_module._task_runner_runs.clear()

    runner = task_runner_module._start_task_runner(
        task.id,
        pipeline(),
        operation="audit",
    )

    assert await runner == "ok"
    stored_run = isolated_store.get(task.id).execution_runs[-1]
    assert update_calls == 0
    assert stored_run.status == "succeeded"
    assert stored_run.current_stage is None


@pytest.mark.asyncio
async def test_swallowed_business_error_can_explicitly_finish_execution_as_failed(isolated_store, monkeypatch):
    task = isolated_store.create(Task(name="swallowed-error"))
    monkeypatch.setattr(task_runner_module, "get_store", lambda: isolated_store)
    task_runner_module._task_runners.clear()
    task_runner_module._task_runner_statuses.clear()
    task_runner_module._task_runner_runs.clear()

    async def pipeline():
        try:
            raise RetryableExecutionError("provider unavailable")
        except RetryableExecutionError as exc:
            assert task_runner_module.mark_current_execution_failed(task.id, exc)

    runner = task_runner_module._start_task_runner(
        task.id,
        pipeline(),
        operation="audit",
    )
    await runner

    stored_run = isolated_store.get(task.id).execution_runs[-1]
    assert stored_run.status == "failed"
    assert stored_run.error.retryable is True
    assert stored_run.recoverable is True


@pytest.mark.asyncio
async def test_task_retry_persists_retry_state_and_checkpoint(isolated_store, monkeypatch):
    task = isolated_store.create(Task(name="checkpoint"))
    run, _ = isolated_store.start_execution(task.id, operation="audit")
    attempts = 0
    monkeypatch.setattr(task_runner_module, "get_store", lambda: isolated_store)

    async def operation():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RetryableExecutionError("temporary")
        return "ok"

    result = await execute_task_with_retry(
        task.id,
        run,
        operation,
        stage="llm",
        sleep=lambda _delay: asyncio.sleep(0),
        random_fn=lambda: 0.0,
    )

    stored_run = isolated_store.get(task.id).execution_runs[-1]
    assert result == "ok"
    assert stored_run.retry_state.retries_used == 1
    assert stored_run.stages[0].status == "succeeded"
    assert stored_run.stages[0].attempts == 1


def test_generation_write_barrier_rejects_old_run(isolated_store):
    task = isolated_store.create(Task(name="generation"))
    old_run, created = isolated_store.start_execution(task.id, operation="audit")
    assert created is True
    new_run, created = isolated_store.start_execution(task.id, operation="audit")
    assert created is True

    old_run.status = "succeeded"
    assert isolated_store.save_execution_run(task.id, old_run, final=True) is False

    stored = isolated_store.get(task.id)
    assert stored.active_execution_run_id == new_run.run_id
    assert stored.execution_generation == new_run.generation
    assert next(run for run in stored.execution_runs if run.run_id == old_run.run_id).status == "running"


def test_same_idempotency_key_is_deduplicated(isolated_store):
    task = isolated_store.create(Task(name="idempotency"))
    first, created = isolated_store.start_execution(
        task.id,
        operation="translate",
        idempotency_key="request-1",
        input_fingerprint="sha256:abc",
    )
    assert created is True

    duplicate, created = isolated_store.start_execution(
        task.id,
        operation="translate",
        idempotency_key="request-1",
        input_fingerprint="sha256:abc",
    )

    assert created is False
    assert duplicate.run_id == first.run_id
    assert len(isolated_store.get(task.id).execution_runs) == 1


def test_stale_business_update_preserves_newer_execution_checkpoint(isolated_store):
    task = isolated_store.create(Task(name="stale-update"))
    stale_task = task.model_copy(deep=True)
    run, _ = isolated_store.start_execution(task.id, operation="audit")
    isolated_store.checkpoint_execution(
        task.id,
        run.run_id,
        run.generation,
        stage="audit:chunk-1",
        status="succeeded",
        artifact_ref="cache/chunk-1.json",
    )

    stale_task.name = "业务字段更新"
    isolated_store.update(stale_task)

    stored = isolated_store.get(task.id)
    assert stored.name == "业务字段更新"
    assert stored.active_execution_run_id == run.run_id
    assert stored.execution_runs[-1].stages[0].artifact_ref == "cache/chunk-1.json"
