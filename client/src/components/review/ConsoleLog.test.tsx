import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { ConsoleLog } from "./ConsoleLog";
import type { ExecutionRun } from "@/lib/api";

afterEach(cleanup);

describe("ConsoleLog execution summary", () => {
  it("shows retry, stage, checkpoint and recoverability from backend state", () => {
    const executionRun: ExecutionRun = {
      run_id: "run-console",
      generation: 1,
      operation: "audit",
      status: "retry_wait",
      current_stage: "semantic_audit",
      stages: [
        { stage: "prepare", status: "succeeded", attempts: 1, updated_at: "2026-07-30T08:00:00Z" },
      ],
      retry_state: {
        retries_used: 1,
        max_retries: 3,
        next_retry_at: new Date(Date.now() + 10_000).toISOString(),
      },
      error: { error_type: "rate_limit", message: "later", retryable: true },
      recoverable: true,
      created_at: "2026-07-30T08:00:00Z",
      updated_at: "2026-07-30T08:00:00Z",
    };

    render(
      <ConsoleLog
        entries={[]}
        expanded={false}
        onToggle={() => undefined}
        executionRun={executionRun}
      />,
    );

    expect(screen.getByText(/自动重试第 1 次（总4次）/)).toBeTruthy();
    expect(screen.getByText(/阶段 semantic_audit/)).toBeTruthy();
    expect(screen.getByText(/复用检查点 1/)).toBeTruthy();
  });
});
