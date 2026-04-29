import { describe, expect, it } from "vitest";

import type { Segment } from "@/lib/api";
import { groupSegmentsIntoHighlightClips } from "./highlight-clips";

function createSegment(overrides: Partial<Segment> = {}): Segment {
  return {
    id: "seg-1",
    start: 0,
    end: 5,
    text: "默认片段",
    action: "keep",
    ...overrides,
  };
}

describe("groupSegmentsIntoHighlightClips", () => {
  it("优先按 clip_group 分组并读取组标题", () => {
    const clips = groupSegmentsIntoHighlightClips([
      createSegment({
        id: "seg-a",
        start: 30,
        end: 36,
        text: "第二组开头",
        clip_group: 2,
        clip_title: "第二个短视频",
      }),
      createSegment({
        id: "seg-b",
        start: 0,
        end: 6,
        text: "第一组开头",
        clip_group: 1,
        clip_title: "第一个短视频",
      }),
      createSegment({
        id: "seg-c",
        start: 7,
        end: 11,
        text: "第一组延续",
        clip_group: 1,
      }),
    ]);

    expect(clips).toHaveLength(2);
    expect(clips[0].title).toBe("第一个短视频");
    expect(clips[0].segments.map((segment) => segment.id)).toEqual(["seg-b", "seg-c"]);
    expect(clips[1].title).toBe("第二个短视频");
  });

  it("在缺失 clip_group 时按时间间隔 fallback 分组", () => {
    const clips = groupSegmentsIntoHighlightClips([
      createSegment({ id: "seg-a", start: 0, end: 4, text: "A" }),
      createSegment({ id: "seg-b", start: 4.2, end: 7, text: "B" }),
      createSegment({ id: "seg-c", start: 15, end: 20, text: "C" }),
    ]);

    expect(clips).toHaveLength(2);
    expect(clips[0].segments.map((segment) => segment.id)).toEqual(["seg-a", "seg-b"]);
    expect(clips[1].segments.map((segment) => segment.id)).toEqual(["seg-c"]);
  });
});
