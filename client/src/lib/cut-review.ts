import type { Segment } from "./api";

export interface PlaybackRange {
  start: number;
  end: number;
}
export interface AuditionRequest {
  id: number;
  ranges: PlaybackRange[];
  label: string;
}
export interface CutReview {
  id: string;
  deletedIds: string[];
  deletedText: string;
  reason: string;
  start: number;
  end: number;
  before: PlaybackRange[];
  after: PlaybackRange[];
  hint: string;
  priority: number;
}

const keptActions = new Set(["keep", "subtitle_fix", "text_fix", "merge_next"]);

/** Mirrors the exporter's contiguous kept blocks, including natural pauses inside them. */
export function buildKeptRanges(segments: Segment[]): PlaybackRange[] {
  const ranges: PlaybackRange[] = [];
  let current: PlaybackRange | null = null;
  for (const segment of segments) {
    if (keptActions.has(segment.action)) {
      if (!current) {
        current = { start: segment.start, end: segment.end };
        ranges.push(current);
      } else current.end = segment.end;
    } else current = null;
  }
  return ranges.filter(
    (range) =>
      Number.isFinite(range.start) &&
      Number.isFinite(range.end) &&
      range.end > range.start,
  );
}

/** Returns the containing/next range in O(log n); no lookahead that could swallow a kept word. */
export function findPlaybackRange(
  ranges: PlaybackRange[],
  time: number,
): PlaybackRange | undefined {
  let low = 0,
    high = ranges.length;
  while (low < high) {
    const mid = (low + high) >>> 1;
    if (ranges[mid].end <= time) low = mid + 1;
    else high = mid;
  }
  return ranges[low];
}

function reviewSignature(segment?: Segment) {
  return segment
    ? [
        segment.id,
        segment.start,
        segment.end,
        segment.action,
        segment.subtitle_text ?? segment.display_text ?? segment.text,
      ]
    : null;
}

export function buildCutReviews(segments: Segment[]): CutReview[] {
  const result: CutReview[] = [];
  const blocks = buildKeptRanges(segments);
  const orderedBlocks = blocks.every((range, index) => index === 0 || (
    range.start >= blocks[index - 1].start && range.end >= blocks[index - 1].end
  ));
  // Two binary searches replace repeated full scans on long source timelines.
  const neighbours = (start: number, end: number) => {
    if (!orderedBlocks) return [
      blocks.findLast((range) => range.end <= start),
      blocks.find((range) => range.start >= end),
    ];
    let low = 0, high = blocks.length;
    while (low < high) {
      const mid = (low + high) >>> 1;
      if (blocks[mid].end <= start) low = mid + 1;
      else high = mid;
    }
    const left = blocks[low - 1];
    low = 0; high = blocks.length;
    while (low < high) {
      const mid = (low + high) >>> 1;
      if (blocks[mid].start < end) low = mid + 1;
      else high = mid;
    }
    return [left, blocks[low]];
  };
  for (let index = 0; index < segments.length; index++) {
    if (segments[index].action !== "delete") continue;
    const removed: Segment[] = [segments[index]];
    while (segments[index + 1]?.action === "delete")
      removed.push(segments[++index]);
    const first = removed[0],
      last = removed[removed.length - 1];
    const [left, right] = neighbours(first.start, last.end);
    const after: PlaybackRange[] = [];
    if (left)
      after.push({ start: Math.max(left.start, left.end - 2), end: left.end });
    if (right)
      after.push({
        start: right.start,
        end: Math.min(right.end, right.start + 2),
      });
    const duration = last.end - first.start;
    const shortEdge = after.some((range) => range.end - range.start < 0.6);
    const hint = !after.length
      ? "没有保留内容，请先恢复必要片段"
      : shortEdge
        ? "接缝旁有短句，请检查是否吞字或缺少主语"
        : duration >= 8
          ? "删除跨度较长，请检查前后语义是否接得上"
          : !left
            ? "检查正文开头是否完整"
            : !right
              ? "检查结尾是否完整收束"
              : "试听前后衔接是否自然";
    // Include content and neighbouring timing so edits invalidate previous review marks.
    const id = JSON.stringify([
      removed.map((s) => [
        s.id,
        s.start,
        s.end,
        s.subtitle_text ?? s.display_text ?? s.text,
        s.reason,
      ]),
      left,
      right,
      reviewSignature(segments[index - removed.length]),
      reviewSignature(segments[index + 1]),
    ]);
    result.push({
      id,
      deletedIds: removed.map((s) => s.id),
      deletedText: removed.map((s) => s.text).join(""),
      reason:
        Array.from(new Set(removed.map((s) => s.reason).filter(Boolean))).join(
          "；",
        ) || "未说明删除原因",
      start: first.start,
      end: last.end,
      after,
      before: [
        {
          start: left ? Math.max(left.start, left.end - 2) : first.start,
          end: right ? Math.min(right.end, right.start + 2) : last.end,
        },
      ],
      hint,
      priority: !after.length ? 3 : shortEdge || duration >= 8 ? 2 : 1,
    });
  }
  return result.sort((a, b) => b.priority - a.priority || a.start - b.start);
}
