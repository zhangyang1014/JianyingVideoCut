import { describe, expect, it } from "vitest";

import { getEffectiveSubtitleText, type Segment } from "./api";

function segment(overrides: Partial<Segment>): Segment {
  return {
    id: "seg-1",
    start: 0,
    end: 1,
    text: "原始字幕",
    action: "keep",
    ...overrides,
  };
}

describe("getEffectiveSubtitleText", () => {
  it("prefers subtitle override over audit display text and raw text", () => {
    expect(
      getEffectiveSubtitleText(
        segment({
          display_text: "AI 修正字幕",
          subtitle_text: "最终优化字幕",
        }),
      ),
    ).toBe("最终优化字幕");
  });

  it("falls back to display text then raw text", () => {
    expect(getEffectiveSubtitleText(segment({ display_text: "AI 修正字幕" }))).toBe("AI 修正字幕");
    expect(getEffectiveSubtitleText(segment({}))).toBe("原始字幕");
  });
});
