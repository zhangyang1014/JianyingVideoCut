import { describe, expect, it } from "vitest";

import {
  canResumeExecution,
  classifyReviewExecutionActivity,
  getExecutionCheckpointCount,
  getExecutionRetrySeconds,
  getExecutionTotalAttempts,
  getTaskActiveExecutionRun,
  isExecutionFailureFinal,
  type ExecutionRun,
  type Task,
} from "./api";

function run(overrides: Partial<ExecutionRun> = {}): ExecutionRun {
  return {
    run_id: "run-1",
    generation: 1,
    operation: "audit",
    status: "running",
    current_stage: "semantic_audit",
    stages: [],
    retry_state: {
      retries_used: 0,
      max_retries: 3,
      next_retry_at: null,
      last_delay_seconds: null,
    },
    error: null,
    recoverable: true,
    created_at: "2026-07-30T08:00:00.000Z",
    updated_at: "2026-07-30T08:00:00.000Z",
    ...overrides,
  };
}

describe("execution state helpers", () => {
  it("reads retry_wait countdown and total four attempts", () => {
    const value = run({
      status: "retry_wait",
      retry_state: {
        retries_used: 2,
        max_retries: 3,
        next_retry_at: "2026-07-30T08:00:10.000Z",
      },
    });

    expect(getExecutionTotalAttempts(value)).toBe(4);
    expect(getExecutionRetrySeconds(value, Date.parse("2026-07-30T08:00:04.200Z"))).toBe(6);
    expect(isExecutionFailureFinal(value)).toBe(false);
  });

  it("marks exhausted retryable failures as final and recoverable", () => {
    const value = run({
      status: "failed",
      retry_state: { retries_used: 3, max_retries: 3 },
      error: { error_type: "rate_limit", message: "later", retryable: true },
    });

    expect(isExecutionFailureFinal(value)).toBe(true);
    expect(canResumeExecution(value)).toBe(true);
  });

  it("marks permanent failures as final but not resumable", () => {
    const value = run({
      status: "failed",
      retry_state: { retries_used: 0, max_retries: 3 },
      error: { error_type: "invalid_input", message: "bad input", retryable: false },
      recoverable: false,
    });

    expect(isExecutionFailureFinal(value)).toBe(true);
    expect(canResumeExecution(value)).toBe(false);
  });

  it("counts succeeded checkpoints as reusable", () => {
    const value = run({
      stages: [
        { stage: "asr", status: "succeeded", attempts: 1, updated_at: "2026-07-30T08:00:00Z" },
        { stage: "translate", status: "succeeded", attempts: 1, updated_at: "2026-07-30T08:01:00Z" },
        { stage: "tts", status: "failed", attempts: 4, updated_at: "2026-07-30T08:02:00Z" },
      ],
    });

    expect(getExecutionCheckpointCount(value)).toBe(2);
  });

  it("does not treat the latest historical run as active", () => {
    const interrupted = run({ run_id: "old-run", status: "interrupted" });
    const task = {
      active_execution_run_id: null,
      execution_runs: [interrupted],
    } as unknown as Task;

    expect(getTaskActiveExecutionRun(task)).toBeNull();
  });

  it("returns only the run referenced by active_execution_run_id", () => {
    const interrupted = run({ run_id: "old-run", status: "interrupted" });
    const active = run({ run_id: "active-run", operation: "audit" });
    const task = {
      active_execution_run_id: "active-run",
      execution_runs: [interrupted, active],
    } as unknown as Task;

    expect(getTaskActiveExecutionRun(task)?.run_id).toBe("active-run");
  });

  it("keeps semantic audit and highlight activity independent", () => {
    expect(classifyReviewExecutionActivity("audit", "reel_plan")).toBe("audit");
    expect(classifyReviewExecutionActivity("highlight_rebuild", "semantic_audit")).toBe("highlight");
    expect(classifyReviewExecutionActivity("highlight_auto_run", "semantic_audit")).toBe("audit");
    expect(classifyReviewExecutionActivity("highlight_auto_run", "reel_validate")).toBe("highlight");
  });
});
