import type { Segment } from "./api";
import { buildKeptRanges, type PlaybackRange } from "./cut-review";

/** Same source window as _build_clip_export_segments, including excluded boundaries. */
export function buildHighlightPreviewRanges(
  segments: Segment[],
  selectedIds: string[],
): PlaybackRange[] {
  const selected = new Set(selectedIds);
  if (!selectedIds.length || selected.size !== selectedIds.length) {
    throw new Error("集锦没有有效片段或存在重复引用，请重建集锦");
  }
  const ordered = [...segments].sort((a, b) => a.start - b.start);
  const indices = ordered.flatMap((segment, index) =>
    selected.has(segment.id) ? [index] : [],
  );
  if (indices.length !== selected.size)
    throw new Error("当前字幕段已变化，请重建集锦");
  const window = ordered.slice(indices[0], indices[indices.length - 1] + 1);
  if (
    window.some(
      (s) =>
        !Number.isFinite(s.start) ||
        !Number.isFinite(s.end) ||
        s.start < 0 ||
        s.end <= s.start,
    )
  ) {
    throw new Error("片段时间范围无效，请检查字幕时间轴");
  }
  // Canonical membership wins over the main audit action, just like the renderer.
  return buildKeptRanges(
    window.map((segment) => ({
      ...segment,
      action: selected.has(segment.id) ? "keep" : "delete",
    })),
  );
}
