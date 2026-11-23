import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SubtitleOptimizeDialog } from "./SubtitleOptimizeDialog";
import type { SubtitleOptimizationPreview } from "@/lib/api";

afterEach(() => {
  cleanup();
});

const preview: SubtitleOptimizationPreview = {
  subtitle_revision: 2,
  glossary_fingerprint: "abc",
  preview_token: "token",
  total_segments: 2,
  changed_count: 1,
  preserved: {
    audit_segments: 2,
    audit_history: 1,
    highlight_reels: 1,
    golden_quotes: 1,
    export_jobs: 1,
  },
  changes: [
    {
      segment_id: "seg-1",
      start: 0,
      end: 2,
      before: "克劳德",
      after: "Claude",
      reasons: ["词典纠错 1 处"],
    },
  ],
};

describe("SubtitleOptimizeDialog", () => {
  it("shows preserved downstream assets and applies selected changes", () => {
    const onApply = vi.fn();

    render(
      <SubtitleOptimizeDialog
        open
        onClose={vi.fn()}
        preview={preview}
        loading={false}
        applying={false}
        selectedIds={new Set(["seg-1"])}
        onToggle={vi.fn()}
        onSelectAll={vi.fn()}
        onApply={onApply}
        onRefresh={vi.fn()}
        onRunFullAnalyze={vi.fn()}
      />,
    );

    expect(screen.getByText("分析优化字幕")).toBeTruthy();
    expect(screen.getByText(/将保留 1 次 AI 审计记录、1 个精彩集锦/)).toBeTruthy();
    expect(screen.getByText("克劳德")).toBeTruthy();
    expect(screen.getByText("Claude")).toBeTruthy();

    fireEvent.click(screen.getByText("应用 1 处优化"));
    expect(onApply).toHaveBeenCalledTimes(1);
  });
});
