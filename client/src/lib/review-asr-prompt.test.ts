import { describe, expect, it } from "vitest";
import { shouldPromptAsrOnEntry } from "./review-asr-prompt";
import type { Task } from "./api";

const baseTask = {
  task_type: "monologue_clean",
  status: "pending",
  video_path: "/tmp/video.mp4",
  asr_result: undefined,
} as Pick<Task, "status" | "video_path" | "asr_result" | "task_type">;

describe("shouldPromptAsrOnEntry", () => {
  it("prompts for pending task with video and no ASR result", () => {
    expect(shouldPromptAsrOnEntry(baseTask)).toBe(true);
  });

  it("does not prompt when ASR is running", () => {
    expect(shouldPromptAsrOnEntry({ ...baseTask, status: "asr_running" })).toBe(false);
  });

  it("does not prompt when analysis is running", () => {
    expect(shouldPromptAsrOnEntry({ ...baseTask, status: "analyze_running" })).toBe(false);
  });

  it("does not prompt when ASR result already exists", () => {
    expect(
      shouldPromptAsrOnEntry({
        ...baseTask,
        asr_result: {
          words: [],
          segments: [],
          tagged_script: "",
          duration: 10,
          language: "zh",
          speakers: [],
        },
      }),
    ).toBe(false);
  });

  it("does not prompt for one_click_translate tasks", () => {
    expect(
      shouldPromptAsrOnEntry({
        ...baseTask,
        task_type: "one_click_translate",
      }),
    ).toBe(false);
  });
});
