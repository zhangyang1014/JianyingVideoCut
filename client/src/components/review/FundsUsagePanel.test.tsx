import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { FundsUsagePanel } from "./FundsUsagePanel";

const fetchSummary = vi.fn();
const fetchCalls = vi.fn();

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    fetchTaskUsage: (...args: unknown[]) => fetchSummary(...args),
    fetchTaskUsageCalls: (...args: unknown[]) => fetchCalls(...args),
  };
});

beforeEach(() => {
  fetchSummary.mockResolvedValue({
    task_id: "task-1",
    currency: "USD",
    cost_usd: "0.0125",
    call_count: 2,
    input_tokens: 100,
    output_tokens: 25,
    total_tokens: 125,
    unknown_cost_count: 0,
    actual_cost_count: 1,
    estimated_cost_count: 1,
    tracking_started_at: "2026-07-31T01:00:00Z",
    features: [{
      feature: "audit",
      label: "语义审计",
      call_count: 2,
      input_tokens: 100,
      output_tokens: 25,
      total_tokens: 125,
      cost_usd: "0.0125",
      unknown_cost_count: 0,
      actual_cost_count: 1,
      estimated_cost_count: 1,
    }],
  });
  fetchCalls.mockResolvedValue({
    items: [{
      call_id: "call-1",
      task_id: "task-1",
      feature: "audit",
      provider: "openai_compatible",
      requested_model: "demo-model",
      actual_model: "demo-model",
      attempt: 1,
      status: "success",
      started_at: "2026-07-31T01:00:00Z",
      completed_at: "2026-07-31T01:00:01Z",
      elapsed_ms: 1000,
      from_cache: false,
      usage: { input_tokens: 100, output_tokens: 25, total_tokens: 125 },
      currency: "USD",
      cost_usd: "0.0125",
      cost_source: "provider",
    }],
    next_cursor: null,
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("FundsUsagePanel", () => {
  it("展示任务总额、功能汇总并按需加载调用详情", async () => {
    render(<FundsUsagePanel taskId="task-1" />);

    await waitFor(() => expect(fetchSummary).toHaveBeenCalledWith("task-1"));
    expect((await screen.findAllByText("$0.0125")).length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("语义审计")).toBeVisible();
    expect(screen.getAllByText("2 次调用 · 125 token")).toHaveLength(2);

    fireEvent.click(screen.getByRole("button", { name: "查看语义审计调用详情" }));

    await waitFor(() => expect(fetchCalls).toHaveBeenCalled());
    expect(await screen.findByText("demo-model")).toBeVisible();
    expect(screen.getByText("实际")).toBeVisible();
  });

  it("空账本提示统计只覆盖新调用", async () => {
    fetchSummary.mockResolvedValueOnce({
      task_id: "task-1",
      currency: "USD",
      cost_usd: "0",
      call_count: 0,
      input_tokens: 0,
      output_tokens: 0,
      total_tokens: 0,
      unknown_cost_count: 0,
      actual_cost_count: 0,
      estimated_cost_count: 0,
      tracking_started_at: null,
      features: [],
    });

    render(<FundsUsagePanel taskId="task-1" />);
    expect(await screen.findByText("暂无云端模型消费记录")).toBeVisible();
    expect(screen.getByText("统计从本功能启用后的新调用开始")).toBeVisible();
  });
});
