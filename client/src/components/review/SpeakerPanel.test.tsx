import React from "react";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ResegmentInheritanceReport, Task } from "@/lib/api";
import { ResegmentPanel } from "./SpeakerPanel";

const resegmentMock = vi.fn();

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    resegment: (...args: unknown[]) => resegmentMock(...args),
  };
});

function inheritance(overrides: Partial<ResegmentInheritanceReport> = {}): ResegmentInheritanceReport {
  return {
    generated_at: "2026-07-30T14:00:00",
    mode: "aggressive",
    old_segment_count: 10,
    new_segment_count: 8,
    mapped_segment_count: 7,
    exact_segment_count: 5,
    review_segment_count: 2,
    unmapped_segment_count: 1,
    inherited_audit_count: 7,
    inherited_occurrence_count: 1,
    inherited_reel_count: 3,
    inherited_golden_quote_count: 1,
    invalidated_export_count: 1,
    review_segment_ids: ["review-1", "review-2"],
    unmapped_segment_ids: ["new-1"],
    warnings: [],
    ...overrides,
  };
}

function task(): Task {
  return {
    id: "task-1",
    name: "测试任务",
    task_type: "monologue_clean",
    status: "review",
    created_at: "2026-07-30T13:00:00",
    updated_at: "2026-07-30T13:00:00",
    asr_history: [],
    audit_segments: [{ id: "old", start: 0, end: 1, text: "旧", action: "delete" }],
    audit_history: [{
      version: 1,
      created_at: "2026-07-30T13:10:00",
      model: "模型",
      segments_count: 1,
      segments_kept: 0,
      segments_deleted: 1,
    }],
    content_revision: 4,
    highlight_revision: 1,
    params: {
      silence_threshold: 0.3,
      breath_lead_ms: 150,
      breath_tail_ms: 100,
      min_segment_duration: 1.5,
      filler_words: [],
      retake_char_threshold: 5,
      style_mode: "immersive",
      enable_diarization: false,
      rules_enabled: {},
    },
    segments_kept: 0,
    segments_deleted: 1,
    golden_quote_order: [],
    translation_videos: [],
    highlight_reels: [{
      reel_id: "reel-1",
      clip_group: 1,
      title: "集锦",
      segment_ids: ["old"],
      start: 0,
      end: 1,
      duration: 1,
      status: "passed",
      issues: [],
      hidden: false,
    }],
  };
}

beforeEach(() => {
  vi.useFakeTimers();
  resegmentMock.mockImplementation(
    async (_taskId: string, threshold: number, save: boolean) => ({
      success: true,
      message: save ? "已继承 7 段审计、3 个集锦" : "预览完成",
      segments: [],
      stats: {
        total: threshold === 0.3 ? 10 : 8,
        avg_chars: 12,
        over_limit_count: 0,
        over_limit_pct: 0,
        max_chars: 20,
      },
      recommendation: null,
      inheritance: inheritance(),
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("ResegmentPanel", () => {
  it("预览不执行昂贵继承，保存时仅执行一次继承", async () => {
    const onRefresh = vi.fn();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<ResegmentPanel task={task()} onRefresh={onRefresh} />);

    expect((screen.getByRole("checkbox") as HTMLInputElement).checked).toBe(true);
    await act(async () => {
      await Promise.resolve();
    });

    fireEvent.click(screen.getByText("更完整"));
    await act(async () => {
      vi.advanceTimersByTime(350);
      await Promise.resolve();
    });

    expect(resegmentMock).toHaveBeenCalledWith(
      "task-1",
      0.5,
      false,
      {
        inheritMode: "none",
        signal: expect.any(AbortSignal),
      },
    );
    expect(screen.queryByText("继承审计")).toBeNull();

    await act(async () => {
      fireEvent.click(screen.getByText("应用字幕分段"));
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(onRefresh).toHaveBeenCalledTimes(1);

    expect(resegmentMock).toHaveBeenLastCalledWith(
      "task-1",
      0.5,
      true,
      {
        inheritMode: "aggressive",
        expectedContentRevision: 4,
      },
    );
    expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining("保存时将计算并尽量继承"));
  });
});
