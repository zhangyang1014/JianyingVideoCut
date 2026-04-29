import type { Segment } from "@/lib/api";

export interface HighlightClip {
  id: string;
  index: number;
  segments: Segment[];
  title: string;
  score: number;
  reason: string;
  duration: number;
  startTime: number;
  endTime: number;
  /** clip 内所有段的 <STU> 结巴总数 */
  stutterCount: number;
  /** clip 内相邻 segment 之间的时间缝隙总和（秒） */
  silenceDuration: number;
}

const FALLBACK_GAP_SECONDS = 5;

function countTags(segments: Segment[], tag: string): number {
  const re = new RegExp(tag, "g");
  return segments.reduce((sum, seg) => {
    const text = seg.tagged_text || seg.text || "";
    return sum + (text.match(re)?.length ?? 0);
  }, 0);
}

function calcSilenceDuration(sortedSegments: Segment[]): number {
  let total = 0;
  for (let i = 1; i < sortedSegments.length; i++) {
    const gap = sortedSegments[i].start - sortedSegments[i - 1].end;
    if (gap > 0) total += gap;
  }
  return Math.round(total * 10) / 10;
}

function buildClip(index: number, segments: Segment[]): HighlightClip {
  const sortedSegments = [...segments].sort((a, b) => a.start - b.start);
  const titleSegment = sortedSegments.find((segment) => segment.clip_title);
  const fallbackTitle = sortedSegments[0]?.text?.trim() || `片段 ${index}`;
  const title = titleSegment?.clip_title || fallbackTitle.slice(0, 25) + (fallbackTitle.length > 25 ? "..." : "");
  const duration = sortedSegments.reduce((sum, segment) => sum + (segment.end - segment.start), 0);
  const scoreSegment = sortedSegments.find((s) => s.clip_score != null);
  const score = scoreSegment?.clip_score ?? 0;
  const reason = scoreSegment?.clip_reason || titleSegment?.clip_reason || "";
  const stutterCount = countTags(sortedSegments, "<STU>");
  const silenceDuration = calcSilenceDuration(sortedSegments);

  return {
    id: `clip_${String(index).padStart(3, "0")}`,
    index,
    segments: sortedSegments,
    title,
    score,
    reason,
    duration,
    startTime: sortedSegments[0]?.start ?? 0,
    endTime: sortedSegments[sortedSegments.length - 1]?.end ?? 0,
    stutterCount,
    silenceDuration,
  };
}

export function groupSegmentsIntoHighlightClips(segments: Segment[]): HighlightClip[] {
  const keptSegments = segments.filter((segment) => segment.action === "keep");
  if (keptSegments.length === 0) return [];

  const hasClipGroups = keptSegments.some((segment) => segment.clip_group != null);
  if (hasClipGroups) {
    const groupedSegments = keptSegments.filter((segment) => segment.clip_group != null);
    const ungroupedSegments = keptSegments.filter((segment) => segment.clip_group == null);
    const grouped = new Map<number, Segment[]>();
    for (const segment of groupedSegments) {
      const group = segment.clip_group;
      if (group == null) continue;
      if (!grouped.has(group)) {
        grouped.set(group, []);
      }
      grouped.get(group)!.push(segment);
    }

    const clips = Array.from(grouped.entries())
      .sort((a, b) => (a[1][0]?.start ?? 0) - (b[1][0]?.start ?? 0))
      .map(([, groupSegments], index) => buildClip(index + 1, groupSegments));

    if (ungroupedSegments.length === 0) {
      return sortByScore(clips);
    }

    const fallbackClips = groupSegmentsIntoHighlightClips(
      ungroupedSegments.map((segment) => ({
        ...segment,
        clip_group: null,
        clip_title: null,
      }))
    );

    const merged = [...clips, ...fallbackClips].sort((a, b) => a.startTime - b.startTime);
    const reindexed = merged.map((clip, index) => ({
      ...clip,
      id: `clip_${String(index + 1).padStart(3, "0")}`,
      index: index + 1,
    }));
    return sortByScore(reindexed);
  }

  const sortedSegments = [...keptSegments].sort((a, b) => a.start - b.start);
  const groups: Segment[][] = [];
  let currentGroup: Segment[] = [sortedSegments[0]];

  for (let i = 1; i < sortedSegments.length; i += 1) {
    const segment = sortedSegments[i];
    const previous = currentGroup[currentGroup.length - 1];
    if (segment.start - previous.end > FALLBACK_GAP_SECONDS) {
      groups.push(currentGroup);
      currentGroup = [segment];
    } else {
      currentGroup.push(segment);
    }
  }
  groups.push(currentGroup);

  const clips = groups.map((groupSegments, index) => buildClip(index + 1, groupSegments));
  return sortByScore(clips);
}

/** 按 score 降序排列，score 相同按时间升序 */
function sortByScore(clips: HighlightClip[]): HighlightClip[] {
  return [...clips].sort((a, b) => b.score - a.score || a.startTime - b.startTime);
}
