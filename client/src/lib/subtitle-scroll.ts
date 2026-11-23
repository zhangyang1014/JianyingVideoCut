export type SubtitleScrollSource = "editing" | "navigation" | "playback";

export interface SubtitleScrollTarget {
  segmentId: string;
  source: SubtitleScrollSource;
}

export interface SubtitleNavigationState {
  segmentId: string;
  requestId: number;
  source: "highlight" | "analysis";
  playbackAnchorId: string | null;
  consumed: boolean;
}

type SegmentTimeRange = {
  id: string;
  start: number;
  end: number;
};

type VerticalBounds = {
  top: number;
  bottom: number;
};

export function resolveSubtitleScrollTarget({
  editingSegmentId,
  navigation,
  activeSegmentId,
}: {
  editingSegmentId: string | null;
  navigation: SubtitleNavigationState | null;
  activeSegmentId: string | null;
}): SubtitleScrollTarget | null {
  if (editingSegmentId) {
    return { segmentId: editingSegmentId, source: "editing" };
  }
  if (navigation) {
    return navigation.consumed
      ? null
      : { segmentId: navigation.segmentId, source: "navigation" };
  }
  return activeSegmentId
    ? { segmentId: activeSegmentId, source: "playback" }
    : null;
}

export function isSubtitleRowVisible(
  viewport: VerticalBounds,
  row: VerticalBounds,
  inset = 12,
): boolean {
  const safeTop = viewport.top + inset;
  const safeBottom = viewport.bottom - inset;
  return row.top >= safeTop && row.bottom <= safeBottom;
}

export function shouldScrollSubtitleTarget(
  target: SubtitleScrollTarget,
  isVisible: boolean,
): boolean {
  return target.source === "navigation" || !isVisible;
}

/** Build once per timeline. Preserve input-order priority and the previous overlapping sentence. */
export function createSubtitleTimeIndex(segments: readonly SegmentTimeRange[]) {
  const byId = new Map<string, number>();
  let size = 1;
  while (size < segments.length) size *= 2;
  const starts = new Float64Array(size * 2).fill(Infinity);
  const ends = new Float64Array(size * 2).fill(-Infinity);
  segments.forEach((segment, index) => {
    if (!byId.has(segment.id)) byId.set(segment.id, index);
    if (!Number.isFinite(segment.start) || !Number.isFinite(segment.end) || segment.end < segment.start) return;
    starts[size + index] = segment.start;
    ends[size + index] = segment.end;
  });
  for (let node = size - 1; node > 0; node--) {
    starts[node] = Math.min(starts[node * 2], starts[node * 2 + 1]);
    ends[node] = Math.max(ends[node * 2], ends[node * 2 + 1]);
  }
  const contains = (index: number, time: number) =>
    time >= starts[size + index] && (
      time < ends[size + index] || (index === segments.length - 1 && time <= ends[size + index])
    );
  return {
    find(time: number, previousId: string | null): string | null {
      if (!Number.isFinite(time)) return null;
      const previous = previousId === null ? undefined : byId.get(previousId);
      if (previous !== undefined && contains(previous, time)) return previousId;
      // Earlier children win, even when intervals overlap or input is not sorted.
      const search = (node: number): number => {
        if (time < starts[node] || time > ends[node]) return -1;
        if (node >= size) return contains(node - size, time) ? node - size : -1;
        const left = search(node * 2);
        return left >= 0 ? left : search(node * 2 + 1);
      };
      const index = search(1);
      return index >= 0 ? segments[index].id : null;
    },
  };
}

export function findActiveSubtitleSegmentId(
  segments: readonly SegmentTimeRange[],
  currentTime: number,
  previousActiveId: string | null,
): string | null {
  const containsTime = (segment: SegmentTimeRange, index: number) => {
    const isLast = index === segments.length - 1;
    return currentTime >= segment.start
      && (currentTime < segment.end || (isLast && currentTime <= segment.end));
  };

  const previousIndex = previousActiveId
    ? segments.findIndex((segment) => segment.id === previousActiveId)
    : -1;
  if (previousIndex >= 0 && containsTime(segments[previousIndex], previousIndex)) {
    return previousActiveId;
  }

  const activeIndex = segments.findIndex(containsTime);
  return activeIndex >= 0 ? segments[activeIndex].id : null;
}
