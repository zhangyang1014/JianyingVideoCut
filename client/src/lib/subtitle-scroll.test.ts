import { describe, expect, it } from "vitest";

import {
  findActiveSubtitleSegmentId,
  createSubtitleTimeIndex,
  isSubtitleRowVisible,
  resolveSubtitleScrollTarget,
  shouldScrollSubtitleTarget,
  type SubtitleNavigationState,
} from "./subtitle-scroll";

const pendingNavigation: SubtitleNavigationState = {
  segmentId: "segment-navigation",
  requestId: 1,
  source: "highlight",
  playbackAnchorId: "segment-playing",
  consumed: false,
};

describe("subtitle scroll priority", () => {
  it("keeps the editing segment above navigation and playback", () => {
    expect(resolveSubtitleScrollTarget({
      editingSegmentId: "segment-editing",
      navigation: pendingNavigation,
      activeSegmentId: "segment-playing",
    })).toEqual({
      segmentId: "segment-editing",
      source: "editing",
    });
  });

  it("uses pending navigation before playback", () => {
    expect(resolveSubtitleScrollTarget({
      editingSegmentId: null,
      navigation: pendingNavigation,
      activeSegmentId: "segment-playing",
    })).toEqual({
      segmentId: "segment-navigation",
      source: "navigation",
    });
  });

  it("does not replay consumed navigation or let playback immediately pull it back", () => {
    expect(resolveSubtitleScrollTarget({
      editingSegmentId: null,
      navigation: { ...pendingNavigation, consumed: true },
      activeSegmentId: "segment-playing",
    })).toBeNull();
  });

  it("follows playback when no higher-priority target exists", () => {
    expect(resolveSubtitleScrollTarget({
      editingSegmentId: null,
      navigation: null,
      activeSegmentId: "segment-playing",
    })).toEqual({
      segmentId: "segment-playing",
      source: "playback",
    });
  });
});

describe("subtitle row visibility", () => {
  const viewport = { top: 100, bottom: 500 };

  it("does not move a playback row that is already in the safe viewport", () => {
    const visible = isSubtitleRowVisible(viewport, { top: 140, bottom: 220 });
    expect(visible).toBe(true);
    expect(shouldScrollSubtitleTarget(
      { segmentId: "segment-playing", source: "playback" },
      visible,
    )).toBe(false);
  });

  it("moves a playback row after it leaves the safe viewport", () => {
    const visible = isSubtitleRowVisible(viewport, { top: 470, bottom: 530 });
    expect(visible).toBe(false);
    expect(shouldScrollSubtitleTarget(
      { segmentId: "segment-playing", source: "playback" },
      visible,
    )).toBe(true);
  });

  it("centers explicit navigation even when the row is visible", () => {
    expect(shouldScrollSubtitleTarget(
      { segmentId: "segment-navigation", source: "navigation" },
      true,
    )).toBe(true);
  });
});

describe("active subtitle resolution", () => {
  const segments = [
    { id: "first", start: 0, end: 1 },
    { id: "second", start: 1, end: 2 },
  ];

  it("uses half-open boundaries between adjacent segments", () => {
    expect(findActiveSubtitleSegmentId(segments, 1, "first")).toBe("second");
  });

  it("keeps the previous active segment while overlapping ranges still contain time", () => {
    const overlapping = [
      { id: "first", start: 0, end: 2 },
      { id: "second", start: 1, end: 3 },
    ];

    expect(findActiveSubtitleSegmentId(overlapping, 1.5, "first")).toBe("first");
  });

  it("clears the active segment while playback is in a gap", () => {
    expect(findActiveSubtitleSegmentId(segments, 3, "second")).toBeNull();
  });
});

describe("indexed subtitle lookup", () => {
  it("preserves boundaries, gaps, input order and overlapping playback when seeking in either direction", () => {
    const segments = [
      { id: "long", start: 2, end: 12 },
      { id: "early", start: 0, end: 3 },
      { id: "nested", start: 4, end: 5 },
      { id: "last", start: 14, end: 15 },
    ];
    const index = createSubtitleTimeIndex(segments);
    for (const time of [-1, 0, 2, 3, 4, 4.9, 5, 12, 13, 14, 15, 16]) {
      for (const previous of [null, "long", "early", "nested", "last", "missing"]) {
        expect(index.find(time, previous)).toBe(findActiveSubtitleSegmentId(segments, time, previous));
      }
    }
    expect(index.find(NaN, "long")).toBeNull();
    expect(createSubtitleTimeIndex([]).find(0, null)).toBeNull();
  });

  it("matches a linear reference across a long timeline and overlapping retakes", () => {
    const segments = Array.from({ length: 3000 }, (_, i) => ({
      id: `s${i}`, start: i * 2, end: i * 2 + (i % 7 === 0 ? 6 : 1.8),
    }));
    const index = createSubtitleTimeIndex(segments);
    let previous: string | null = null;
    for (let i = 0; i < 800; i++) {
      const time = (i * 7919 % 60000) / 10;
      expect(index.find(time, previous)).toBe(findActiveSubtitleSegmentId(segments, time, previous));
      previous = index.find(time, previous);
    }
  });

  it("does not rescan a long timeline on each frame", () => {
    let reads = 0;
    const segments = Array.from({ length: 10000 }, (_, i) => ({
      get id() { reads++; return `s${i}`; }, start: i * 3, end: i * 3 + 2,
    }));
    const index = createSubtitleTimeIndex(segments);
    reads = 0;
    for (let frame = 0; frame < 120; frame++) {
      expect(index.find(29997 + frame / 120, "s9999")).toBe("s9999");
    }
    expect(index.find(18000.5, null)).toBe("s6000");
    expect(reads).toBe(1);
  });
});
