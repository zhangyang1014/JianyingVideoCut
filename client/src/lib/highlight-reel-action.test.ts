import { describe, expect, it } from "vitest";

import type { Task } from "@/lib/api";
import {
  formatHighlightBuildOptionsSummary,
  getHighlightReelPrimaryAction,
  normalizeHighlightBuildOptions,
} from "./highlight-reel-action";

function createTask(overrides: Partial<Task> = {}): Task {
  return {
    id: "task-1",
    name: "产品集锦任务",
    task_type: "highlight_reel",
    status: "review",
    created_at: "2026-04-09T12:00:00.000Z",
    updated_at: "2026-04-09T12:00:00.000Z",
    asr_history: [],
    audit_segments: [],
    audit_history: [],
    content_revision: 0,
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
    translation_videos: [],
    ...overrides,
  };
}

describe("highlight reel primary action", () => {
  it("uses the full generation flow before audit segments exist", () => {
    const action = getHighlightReelPrimaryAction(createTask());

    expect(action.kind).toBe("auto_run");
    expect(action.label).toBe("生成产品集锦");
    expect(action.description).toContain("完整流程");
    expect(action.title).toContain("自动优化");
  });

  it("uses lightweight rebuild when audit segments already exist", () => {
    const action = getHighlightReelPrimaryAction(
      createTask({
        audit_segments: [{ id: "seg-1", start: 0, end: 10, text: "产品卖点", action: "keep" }],
      }),
    );

    expect(action.kind).toBe("rebuild");
    expect(action.label).toBe("更新产品集锦");
    expect(action.description).toContain("自动优化");
  });

  it("marks existing reels dirty when content revision changed", () => {
    const action = getHighlightReelPrimaryAction(
      createTask({
        content_revision: 2,
        audit_segments: [{ id: "seg-1", start: 0, end: 10, text: "产品卖点", action: "keep" }],
        highlight_reels: [
          {
            reel_id: "reel-1",
            clip_group: 1,
            title: "旧集锦",
            segment_ids: ["seg-1"],
            start: 0,
            end: 10,
            duration: 10,
            status: "passed",
            issues: [],
            source_content_revision: 1,
            hidden: false,
          },
        ],
      }),
    );

    expect(action.kind).toBe("rebuild");
    expect(action.isContentDirty).toBe(true);
    expect(action.description).toContain("内容已修改");
  });

  it("disables actions while a highlight workflow is pending", () => {
    const action = getHighlightReelPrimaryAction(createTask(), { autoRunPending: true });

    expect(action.disabled).toBe(true);
  });
});

describe("highlight reel build options", () => {
  it("keeps empty target count as automatic", () => {
    const options = normalizeHighlightBuildOptions({
      target_product_count: null,
      target_reel_count: null,
    });

    expect(options.target_product_count).toBeNull();
    expect(options.target_reel_count).toBeNull();
    expect(formatHighlightBuildOptionsSummary(options)).toBe("产品数自动 · 60-300s · 总长自动");
  });

  it("normalizes fixed target count and duration bounds", () => {
    const options = normalizeHighlightBuildOptions({
      target_product_count: 4.6,
      min_reel_duration: 30,
      max_reel_duration: 20,
    });

    expect(options.target_product_count).toBe(5);
    expect(options.target_reel_count).toBe(5);
    expect(options.max_reel_duration).toBe(30);
  });

  it("旧 target_reel_count 会映射到新的产品数并继续回传兼容字段", () => {
    const options = normalizeHighlightBuildOptions({ target_reel_count: 3 });

    expect(options).toMatchObject({
      target_product_count: 3,
      target_reel_count: 3,
    });
  });
});
