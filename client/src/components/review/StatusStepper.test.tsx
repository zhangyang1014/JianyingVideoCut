import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { StatusStepper, getReviewSteps } from "./StatusStepper";
import type { ExecutionRun } from "@/lib/api";

afterEach(() => {
  cleanup();
});

describe("StatusStepper", () => {
  const executionRun: ExecutionRun = {
    run_id: "run-1",
    generation: 1,
    operation: "audit",
    status: "retry_wait",
    current_stage: "semantic_audit",
    stages: [
      { stage: "prepare", status: "succeeded", attempts: 1, updated_at: "2026-07-30T08:00:00Z" },
    ],
    retry_state: {
      retries_used: 2,
      max_retries: 3,
      next_retry_at: new Date(Date.now() + 10_000).toISOString(),
    },
    error: { error_type: "rate_limit", message: "later", retryable: true },
    recoverable: true,
    created_at: "2026-07-30T08:00:00Z",
    updated_at: "2026-07-30T08:00:00Z",
  };

  it("renders retry wait, current stage and reusable checkpoints", () => {
    render(<StatusStepper status="audit_running" executionRun={executionRun} />);

    expect(screen.getByText(/自动重试第 2 次（总4次）/)).toBeTruthy();
    expect(screen.getByText("当前阶段：semantic_audit")).toBeTruthy();
    expect(screen.getByText("复用检查点：1")).toBeTruthy();
  });

  it("renders permanent failure as unrecoverable", () => {
    render(
      <StatusStepper
        status="error"
        executionRun={{
          ...executionRun,
          status: "failed",
          recoverable: false,
          retry_state: { retries_used: 0, max_retries: 3 },
          error: { error_type: "invalid_input", message: "bad", retryable: false },
        }}
      />,
    );

    expect(screen.getByText("失败不可恢复")).toBeTruthy();
  });

  it("keeps export outside the main review flow", () => {
    render(<StatusStepper status="review" taskType="highlight_reel" />);

    expect(screen.queryByText("导出成片")).toBeNull();
    expect(screen.queryByText("模型质检")).toBeNull();
    expect(screen.getByText("剪辑审核")).toBeTruthy();
    expect(getReviewSteps("highlight_reel").map((step) => step.key)).not.toContain("export");
  });

  it("folds highlight validation into the public product reel step", () => {
    const highlightSteps = getReviewSteps("highlight_reel");

    expect(highlightSteps.map((step) => step.label)).toEqual([
      "上传视频",
      "语音识别",
      "语义审计",
      "产品集锦",
      "剪辑审核",
    ]);

    render(<StatusStepper status="audit_running" taskType="highlight_reel" stage="reel_validate" />);

    expect(screen.getByText("产品集锦")).toBeTruthy();
    expect(screen.queryByText("模型质检")).toBeNull();
  });

  it("keeps speaker diarization as an optional ASR child instead of a numbered step", () => {
    const regularSteps = getReviewSteps();
    const highlightSteps = getReviewSteps("highlight_reel");

    expect(regularSteps.map((step) => step.key)).not.toContain("diarization");
    expect(highlightSteps.map((step) => step.key)).not.toContain("diarization");
    expect(regularSteps.map((step) => step.label)).toEqual([
      "上传视频",
      "语音识别",
      "AI 审计",
      "剪辑审核",
    ]);
    expect(regularSteps.map((step) => step.key)).not.toContain("analyze");
    expect(regularSteps.find((step) => step.key === "asr")?.active).toContain("analyze_running");
  });

  it.each(["未执行", "分离中", "已完成"])("renders optional diarization state: %s", (stateLabel) => {
    render(
      <StatusStepper
        status="asr_recognized"
        renderAction={(step) =>
          step.key === "asr" ? (
            <div>
              <span>可选 · 说话人分离</span>
              <span>{stateLabel}</span>
            </div>
          ) : null
        }
      />,
    );

    expect(screen.getByText("可选 · 说话人分离")).toBeTruthy();
    expect(screen.getByText(stateLabel)).toBeTruthy();
  });

  it("keeps step actions in a single horizontal lane", () => {
    render(
      <StatusStepper
        status="asr_recognized"
        renderAction={(step) => step.key === "asr" ? <span>ASR · 已完成 · 10:00</span> : null}
      />,
    );

    const actionLane = screen.getByText("ASR · 已完成 · 10:00").parentElement;
    expect(actionLane?.className).toContain("overflow-x-auto");
    expect(actionLane?.className).toContain("whitespace-nowrap");
  });
});
