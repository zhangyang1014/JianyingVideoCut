import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Task } from "@/lib/api";
import { OneClickTranslateWorkbench } from "./OneClickTranslateWorkbench";

const { retryTranslationVideo, previewModelRun } = vi.hoisted(() => ({
  retryTranslationVideo: vi.fn(),
  previewModelRun: vi.fn(),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("@/hooks/useAppConfig", () => ({
  DEFAULT_TTS_MODEL: "tts",
  DEFAULT_TTS_VOICE: "voice",
  useAppConfig: () => ({ config: {} }),
  resolveFeatureLlm: (_config: unknown, feature: string) => ({
    provider: feature === "tts" ? "silent" : "openai",
    model: feature === "tts" ? "tts" : "translate",
    voice: "voice",
    baseUrl: undefined,
    apiKey: undefined,
  }),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    retryTranslationVideo,
    previewModelRun,
    updateTranslationCues: vi.fn(),
    exportOneClickTranslate: vi.fn(),
    cancelTask: vi.fn(),
  };
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function failedTask(): Task {
  return {
    id: "translate-1",
    name: "翻译失败",
    task_type: "one_click_translate",
    status: "error",
    created_at: "2026-07-30T08:00:00Z",
    updated_at: "2026-07-30T08:01:00Z",
    asr_history: [],
    audit_segments: [],
    audit_history: [],
    content_revision: 1,
    highlight_revision: 0,
    export_jobs: [],
    params: {
      silence_threshold: 0.3,
      breath_lead_ms: 80,
      breath_tail_ms: 80,
      min_segment_duration: 0.3,
      filler_words: [],
      retake_char_threshold: 5,
      style_mode: "immersive",
      enable_diarization: false,
      rules_enabled: {},
    },
    segments_kept: 0,
    segments_deleted: 0,
    golden_quote_order: [],
    translation_videos: [{
      id: "video-1",
      filename: "one.mp4",
      video_path: "/tmp/one.mp4",
      status: "error",
      error_message: "tts failed",
      cues: [{
        id: "cue-1",
        source_segment_id: "segment-1",
        start: 0,
        end: 1,
        source_text: "你好",
        calibrated_text: "你好",
        translated_text: "hello",
        status: "translated",
      }],
    }],
    execution_runs: [{
      run_id: "run-translate",
      generation: 1,
      operation: "translate_running",
      status: "failed",
      current_stage: "tts",
      stages: [
        { stage: "asr", status: "succeeded", attempts: 1, updated_at: "2026-07-30T08:00:00Z" },
        { stage: "translate", status: "succeeded", attempts: 1, updated_at: "2026-07-30T08:00:20Z" },
        { stage: "tts", status: "failed", attempts: 4, updated_at: "2026-07-30T08:01:00Z" },
      ],
      retry_state: { retries_used: 3, max_retries: 3 },
      error: { error_type: "tts", message: "tts failed", retryable: true },
      recoverable: true,
      created_at: "2026-07-30T08:00:00Z",
      updated_at: "2026-07-30T08:01:00Z",
    }],
  };
}

function renderWorkbench(task = failedTask()) {
  render(
    <OneClickTranslateWorkbench
      task={task}
      taskId={task.id}
      onBack={vi.fn()}
      onRefresh={vi.fn().mockResolvedValue(undefined)}
      appConfig={{} as never}
      consoleEntries={[]}
      consoleExpanded={false}
      onConsoleToggle={vi.fn()}
      onConsoleClear={vi.fn()}
    />,
  );
}

describe("OneClickTranslateWorkbench recovery", () => {
  it("uses the existing retry API for checkpoint resume and forced restart", async () => {
    previewModelRun.mockImplementation(async (_taskId, request) => ({
      operation: "translate_retry",
      mode: request.mode,
      title: "确认重试翻译视频",
      api_path: "/api/tasks/translate-1/one-click-translate/videos/video-1/retry",
      task_revision: 0,
      stages: [],
      affected_artifacts: [],
      warnings: [],
      preview_token: `token-${request.mode}`,
      expires_in_seconds: 600,
    }));
    retryTranslationVideo.mockResolvedValue({
      success: true,
      message: "started",
      resume_stage: "tts",
    });
    renderWorkbench();

    const continueButton = await screen.findByRole("button", { name: "从检查点继续" });
    await waitFor(() => expect(continueButton.hasAttribute("disabled")).toBe(false));
    fireEvent.click(continueButton);
    expect(retryTranslationVideo).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByRole("button", { name: "确认从检查点继续" }));
    await waitFor(() => expect(retryTranslationVideo).toHaveBeenCalledTimes(1));
    expect(retryTranslationVideo.mock.calls[0][2].force_restart).toBe(false);
    expect(retryTranslationVideo.mock.calls[0][2].preview_token).toBe("token-resume");

    const restartButton = screen.getByRole("button", { name: "完全重新执行" });
    await waitFor(() => expect(restartButton.hasAttribute("disabled")).toBe(false));
    fireEvent.click(restartButton);
    fireEvent.click(await screen.findByRole("button", { name: "确认完全重新执行" }));
    await waitFor(() => expect(retryTranslationVideo).toHaveBeenCalledTimes(2));
    expect(retryTranslationVideo.mock.calls[1][2].force_restart).toBe(true);
    expect(retryTranslationVideo.mock.calls[1][2].preview_token).toBe("token-rerun");
  });

  it("disables checkpoint resume after subtitle input changes", async () => {
    renderWorkbench();

    const translated = await screen.findByDisplayValue("hello");
    fireEvent.change(translated, { target: { value: "updated" } });

    expect(screen.getByText(/输入已变化，不能继续旧执行/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "从检查点继续" }).hasAttribute("disabled")).toBe(true);
    expect(screen.getByRole("button", { name: "完全重新执行" }).hasAttribute("disabled")).toBe(false);
  });
});
