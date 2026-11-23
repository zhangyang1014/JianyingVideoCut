import { describe, expect, it } from "vitest";
import type { Segment } from "./api";
import {
  buildCutReviews,
  buildKeptRanges,
  findPlaybackRange,
} from "./cut-review";

const segment = (
  id: string,
  start: number,
  end: number,
  action: Segment["action"] = "keep",
): Segment => ({ id, start, end, action, text: id });

describe("export-aligned playback", () => {
  it("preserves a short kept sentence between nearby deletions", () => {
    const ranges = buildKeptRanges([
      segment("a", 0, 1),
      segment("d1", 1, 2, "delete"),
      segment("important", 2, 2.3),
      segment("d2", 2.3, 3, "delete"),
      segment("b", 3, 4),
    ]);
    expect(ranges).toEqual([
      { start: 0, end: 1 },
      { start: 2, end: 2.3 },
      { start: 3, end: 4 },
    ]);
    expect(findPlaybackRange(ranges, 0.95)?.start).toBe(0);
    expect(findPlaybackRange(ranges, 1)?.start).toBe(2);
    expect(findPlaybackRange(ranges, 2.1)?.start).toBe(2);
    expect(findPlaybackRange(ranges, 4)).toBeUndefined();
  });
  it("keeps natural pauses inside consecutive kept blocks", () => {
    expect(
      buildKeptRanges([segment("a", 0, 1), segment("b", 2, 3, "subtitle_fix")]),
    ).toEqual([{ start: 0, end: 3 }]);
  });
});

describe("cut review queue", () => {
  it("keeps identical context on a long timeline with pauses, overlaps and short phrases", () => {
    const segments = Array.from({ length: 4000 }, (_, i) => segment(`s${i}`, i * 3, i * 3 + (i % 9 === 0 ? 3.5 : 0.4), i % 2 ? "delete" : "keep"));
    const blocks = buildKeptRanges(segments);
    const cuts = buildCutReviews(segments);
    expect(cuts).toHaveLength(2000);
    for (const cut of cuts) {
      const left = blocks.findLast((range) => range.end <= cut.start);
      const right = blocks.find((range) => range.start >= cut.end);
      expect(cut.after).toEqual([
        ...(left ? [{ start: Math.max(left.start, left.end - 2), end: left.end }] : []),
        ...(right ? [{ start: right.start, end: Math.min(right.end, right.start + 2) }] : []),
      ]);
    }
  });

  it("preserves source-order neighbour selection on an unordered imported timeline", () => {
    const cuts = buildCutReviews([
      segment("later", 20, 24), segment("delete-later", 24, 25, "delete"),
      segment("early", 0, 3), segment("delete-early", 3, 4, "delete"), segment("middle", 4, 8),
    ]);
    expect(cuts.find((cut) => cut.start === 24)?.after).toEqual([{ start: 6, end: 8 }]);
    expect(cuts.find((cut) => cut.start === 3)?.after).toEqual([{ start: 1, end: 3 }, { start: 20, end: 22 }]);
  });

  it("groups consecutive deletes and provides before/after context", () => {
    const cuts = buildCutReviews([
      segment("a", 0, 4),
      segment("d1", 4, 6, "delete"),
      segment("d2", 6, 8, "delete"),
      segment("b", 8, 12),
    ]);
    expect(cuts).toHaveLength(1);
    expect(cuts[0].before).toEqual([{ start: 2, end: 10 }]);
    expect(cuts[0].after).toEqual([
      { start: 2, end: 4 },
      { start: 8, end: 10 },
    ]);
    expect(cuts[0].deletedIds).toEqual(["d1", "d2"]);
  });
  it("retains deleted opening and ending in the original audition", () => {
    const cuts = buildCutReviews([
      segment("head", 0, 3, "delete"),
      segment("a", 3, 6),
      segment("tail", 6, 9, "delete"),
    ]);
    expect(cuts[0].before).toEqual([{ start: 0, end: 5 }]);
    expect(cuts[1].before).toEqual([{ start: 4, end: 9 }]);
  });
  it("invalidates review marks when neighbouring text changes", () => {
    const segments = [
      segment("a", 0, 3),
      segment("d", 3, 4, "delete"),
      segment("b", 4, 7),
    ];
    const old = buildCutReviews(segments)[0].id;
    segments[2].text = "new content";
    expect(buildCutReviews(segments)[0].id).not.toBe(old);
  });
  it("shows no after-preview when nothing is kept", () => {
    const cut = buildCutReviews([segment("d", 0, 4, "delete")])[0];
    expect(cut.after).toEqual([]);
    expect(cut.priority).toBe(3);
  });
});
