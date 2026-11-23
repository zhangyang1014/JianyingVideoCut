import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Task } from "@/lib/api";
import { ExportPanel } from "./ExportPanel";

afterEach(() => {
  cleanup();
  localStorage.clear();
});

function renderPanel() {
  const onExportFFmpeg = vi.fn();
  const task = {
    id: "task-render",
    name: "渲染测试",
    golden_quote_order: [],
    export_jobs: [],
    audit_segments: [],
  } as unknown as Task;

  render(
    <ExportPanel
      task={task}
      isExporting={false}
      jianyingDir=""
      onExportFFmpeg={onExportFFmpeg}
      onExportJianying={vi.fn()}
      onCancelExport={vi.fn()}
      onGoldenQuoteSaved={vi.fn()}
      onSeek={vi.fn()}
      goldenCandidates={[]}
      setGoldenCandidates={vi.fn()}
      goldenAnalyzed={false}
      setGoldenAnalyzed={vi.fn()}
      goldenDialogOpen={false}
      setGoldenDialogOpen={vi.fn()}
      onClipApplied={vi.fn()}
    />,
  );
  return onExportFFmpeg;
}

function renderFailedPanel(inputChanged = false) {
  const onExportFFmpeg = vi.fn();
  const task = {
    id: "task-render-failed",
    name: "失败导出",
    content_revision: inputChanged ? 2 : 1,
    highlight_revision: 1,
    subtitle_revision: 1,
    golden_quote_order: [],
    audit_segments: [],
    export_jobs: [{
      id: "job-1",
      task_id: "task-render-failed",
      kind: "ffmpeg",
      render_profile: "final",
      status: "failed",
      source_content_revision: 1,
      source_highlight_revision: 1,
      source_subtitle_revision: 1,
      request: { burn_subtitles: true },
      snapshot: {},
      output_files: [],
      created_at: "2026-07-30T08:00:00Z",
      updated_at: "2026-07-30T08:01:00Z",
    }],
    execution_runs: [{
      run_id: "run-export",
      generation: 1,
      operation: "export_running",
      status: "failed",
      current_stage: "render",
      stages: [
        { stage: "prepare", status: "succeeded", attempts: 1, updated_at: "2026-07-30T08:00:00Z" },
        { stage: "render", status: "failed", attempts: 4, updated_at: "2026-07-30T08:01:00Z" },
      ],
      retry_state: { retries_used: 3, max_retries: 3 },
      error: { error_type: "ffmpeg", message: "failed", retryable: true },
      recoverable: true,
      created_at: "2026-07-30T08:00:00Z",
      updated_at: "2026-07-30T08:01:00Z",
    }],
  } as unknown as Task;

  render(
    <ExportPanel
      task={task}
      isExporting={false}
      jianyingDir=""
      onExportFFmpeg={onExportFFmpeg}
      onExportJianying={vi.fn()}
      onCancelExport={vi.fn()}
      onGoldenQuoteSaved={vi.fn()}
      onSeek={vi.fn()}
      goldenCandidates={[]}
      setGoldenCandidates={vi.fn()}
      goldenAnalyzed={false}
      setGoldenAnalyzed={vi.fn()}
      goldenDialogOpen={false}
      setGoldenDialogOpen={vi.fn()}
      onClipApplied={vi.fn()}
    />,
  );
  return onExportFFmpeg;
}

describe("ExportPanel", () => {
  it("starts a subtitle-free draft render", () => {
    const onExportFFmpeg = renderPanel();

    fireEvent.click(screen.getByText("快速草稿"));

    expect(onExportFFmpeg).toHaveBeenCalledWith("draft", false);
  });

  it("starts preview and final render with configured subtitles", () => {
    const onExportFFmpeg = renderPanel();

    fireEvent.click(screen.getByText("成片预览"));
    fireEvent.click(screen.getByText("最终 FFmpeg 导出"));

    expect(onExportFFmpeg.mock.calls[0][0]).toBe("preview");
    expect(onExportFFmpeg.mock.calls[0][1]).toBe(true);
    expect(onExportFFmpeg.mock.calls[1][0]).toBe("final");
    expect(onExportFFmpeg.mock.calls[1][1]).toBe(true);
  });

  it("persists quick subtitle styles and sends them with the export", () => {
    const onExportFFmpeg = renderPanel();

    fireEvent.click(screen.getByRole("button", { name: /快捷样式/ }));
    fireEvent.change(screen.getByLabelText("快捷字幕字体"), {
      target: { value: "STKaiti" },
    });
    fireEvent.change(screen.getByLabelText("快捷字幕文字颜色"), {
      target: { value: "#ffcc00" },
    });
    fireEvent.change(screen.getByLabelText("快捷字幕描边颜色"), {
      target: { value: "#123456" },
    });
    fireEvent.change(screen.getByLabelText("快捷字幕描边厚度"), {
      target: { value: "3.5" },
    });

    expect(JSON.parse(localStorage.getItem("gc_subtitle_style") || "{}")).toMatchObject({
      font_name: "STKaiti",
      primary_color: "#ffcc00",
      outline_color: "#123456",
      outline: 3.5,
    });

    fireEvent.click(screen.getByText("最终 FFmpeg 导出"));

    expect(onExportFFmpeg).toHaveBeenCalledWith(
      "final",
      true,
      expect.objectContaining({
        font_name: "STKaiti",
        primary_color: "#ffcc00",
        outline_color: "#123456",
        outline: 3.5,
      }),
    );
  });

  it("offers checkpoint resume and full rerun after retry exhaustion", () => {
    const onExportFFmpeg = renderFailedPanel();

    expect(screen.getByText(/复用检查点：1/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "从检查点继续" }));
    fireEvent.click(screen.getByRole("button", { name: "完全重新执行" }));

    expect(onExportFFmpeg).toHaveBeenCalledTimes(2);
    expect(onExportFFmpeg).toHaveBeenCalledWith("final", true, expect.any(Object));
  });

  it("disables checkpoint resume when export inputs changed", () => {
    renderFailedPanel(true);

    expect(screen.getByText(/输入版本已变化/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "从检查点继续" }).hasAttribute("disabled")).toBe(true);
    expect(screen.getByRole("button", { name: "完全重新执行" }).hasAttribute("disabled")).toBe(false);
  });
});
