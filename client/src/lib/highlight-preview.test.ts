import { expect, it } from "vitest";
import type { Segment } from "./api";
import { buildHighlightPreviewRanges } from "./highlight-preview";

const segments: Segment[] = [
  { id: "a", start: 0, end: 2, text: "开场", action: "keep" },
  { id: "b", start: 2.2, end: 3, text: "接话", action: "delete" },
  { id: "c", start: 3, end: 5, text: "其他产品", action: "keep" },
  { id: "d", start: 5, end: 5.4, text: "但是", action: "keep" },
  { id: "e", start: 5.5, end: 8, text: "解释", action: "keep" },
];
it("matches export membership, preserves natural pauses and a short kept phrase", () => {
  expect(buildHighlightPreviewRanges(segments, ["a", "b", "d", "e"])).toEqual([
    { start: 0, end: 3 },
    { start: 5, end: 8 },
  ]);
  expect(segments[1].action).toBe("delete");
});
it("plays only one selected clip and normalizes source order", () => {
  expect(buildHighlightPreviewRanges(segments, ["e", "d"])).toEqual([
    { start: 5, end: 8 },
  ]);
});
it("does not silently drop stale, repeated or invalid segments", () => {
  expect(() => buildHighlightPreviewRanges(segments, ["a", "missing"])).toThrow(
    "字幕段已变化",
  );
  expect(() => buildHighlightPreviewRanges(segments, ["a", "a"])).toThrow();
  expect(() =>
    buildHighlightPreviewRanges([{ ...segments[0], end: NaN }], ["a"]),
  ).toThrow("时间范围无效");
});
