import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { GoldenQuoteCandidate, Task } from "@/lib/api";
import { GoldenQuoteDialog } from "./GoldenQuoteDialog";


const { previewModelRun, suggestGoldenQuotes } = vi.hoisted(() => ({
  previewModelRun: vi.fn(),
  suggestGoldenQuotes: vi.fn(),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    previewModelRun,
    suggestGoldenQuotes,
    saveGoldenQuoteOrder: vi.fn(),
  };
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const candidate: GoldenQuoteCandidate = {
  segment_id: "seg-1",
  text: "这是开场金句",
  start: 0,
  end: 2,
  reason: "有吸引力",
};

const task = {
  id: "task-golden",
  name: "金句测试",
  task_type: "monologue_clean",
  status: "review",
  audit_segments: [{
    id: "seg-1",
    start: 0,
    end: 2,
    text: "这是开场金句",
    action: "keep",
  }],
  golden_quote_order: [],
} as unknown as Task;

describe("GoldenQuoteDialog rerun", () => {
  it("previews configuration before replacing existing candidates", async () => {
    previewModelRun.mockResolvedValue({
      operation: "golden_quote",
      mode: "rerun",
      title: "确认重新分析金句",
      api_path: "/api/tasks/task-golden/golden-quotes/suggest",
      task_revision: 0,
      stages: [],
      affected_artifacts: ["当前金句候选"],
      warnings: [],
      preview_token: "golden-token",
      expires_in_seconds: 600,
    });
    suggestGoldenQuotes.mockResolvedValue([candidate]);

    render(
      <GoldenQuoteDialog
        open
        onClose={vi.fn()}
        task={task}
        provider="openai"
        openaiModel="z-ai/glm-5.2"
        onSaved={vi.fn()}
        onSeek={vi.fn()}
        candidates={[candidate]}
        setCandidates={vi.fn()}
        analyzed
        setAnalyzed={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "重新分析" }));
    expect(suggestGoldenQuotes).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByRole("button", { name: "确认重新分析" }));

    await waitFor(() => expect(suggestGoldenQuotes).toHaveBeenCalledTimes(1));
    expect(suggestGoldenQuotes.mock.calls[0][1].preview_token).toBe("golden-token");
  });
});
