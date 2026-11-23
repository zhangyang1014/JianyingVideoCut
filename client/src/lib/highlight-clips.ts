import {
  getEffectiveSubtitleText,
  type HighlightQualityStage,
  type HighlightReel,
  type HighlightReelIssue,
  type HighlightReelQualityReport,
  type HighlightReelRepairAction,
  type HighlightStrategyKind,
  type ProductOccurrence,
  type ProductGroup,
  type Segment,
} from "@/lib/api";

export interface HighlightClipQualityMetadata {
  stage: HighlightQualityStage;
  passed?: boolean;
  score?: number | null;
  issues: HighlightReelIssue[];
  repairActions: HighlightReelRepairAction[];
  repairCount: number;
  degraded: boolean;
}

export interface HighlightClip {
  id: string;
  index: number;
  segments: Segment[];
  title: string;
  duration: number;
  startTime: number;
  endTime: number;
  reelId?: string;
  canonicalReel?: HighlightReel;
  clipGroup?: number;
  occurrenceId?: string | null;
  productGroupId?: string | null;
  sourceOccurrenceIds: string[];
  productName?: string | null;
  strategyKind?: HighlightStrategyKind | null;
  retentionRatio?: number | null;
  quality?: HighlightClipQualityMetadata;
  status?: string;
  exportStatus?: string;
  score?: number | null;
  issues?: Array<{ code: string; severity: string; message: string }>;
  createdAt?: string | null;
  hidden?: boolean;
}

export interface HighlightOccurrenceGroup {
  groupId: string;
  groupingKind: "product" | "occurrence";
  occurrenceId?: string;
  productGroupId?: string;
  productName: string;
  sourceOccurrenceIds: string[];
  sourceOccurrenceCount: number;
  clips: HighlightClip[];
  strategyCount: number;
  completenessLabel: string;
  missingStrategies: HighlightStrategyKind[];
}

export interface HighlightClipAggregation {
  occurrenceGroups: HighlightOccurrenceGroup[];
  /** 无 occurrence_id 的旧任务保持原有平铺顺序，不强行构造产品场次。 */
  legacyClips: HighlightClip[];
}

export type HighlightSegmentMembershipAction = "add" | "remove";

export interface UpdateHighlightSegmentMembershipInput {
  reels: HighlightReel[];
  reelId: string;
  segmentId: string;
  action: HighlightSegmentMembershipAction;
  segments: Segment[];
  productOccurrences: ProductOccurrence[];
  productGroups?: ProductGroup[];
}

const FALLBACK_GAP_SECONDS = 5;
const KEPT_ACTIONS = new Set(["keep", "subtitle_fix", "text_fix", "merge_next"]);
export const HIGHLIGHT_STRATEGY_ORDER: HighlightStrategyKind[] = ["faithful", "balanced", "concise"];

function isKept(segment: Segment): boolean {
  return KEPT_ACTIONS.has(segment.action);
}

function effectiveText(segment: Segment): string {
  return getEffectiveSubtitleText(segment);
}

function buildClip(index: number, segments: Segment[]): HighlightClip {
  const sortedSegments = [...segments].sort((a, b) => a.start - b.start);
  const titleSegment = sortedSegments.find((segment) => segment.clip_title);
  const fallbackTitle = sortedSegments[0] ? effectiveText(sortedSegments[0]) || `片段 ${index}` : `片段 ${index}`;
  const title = titleSegment?.clip_title || fallbackTitle.slice(0, 25) + (fallbackTitle.length > 25 ? "..." : "");
  const duration = sortedSegments.reduce((sum, segment) => sum + (segment.end - segment.start), 0);

  return {
    id: `clip_${String(index).padStart(3, "0")}`,
    index,
    segments: sortedSegments,
    title,
    duration,
    startTime: sortedSegments[0]?.start ?? 0,
    endTime: sortedSegments[sortedSegments.length - 1]?.end ?? 0,
    sourceOccurrenceIds: [],
  };
}

export function groupSegmentsIntoHighlightClips(segments: Segment[]): HighlightClip[] {
  const keptSegments = segments.filter(isKept);
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
      return clips;
    }

    const fallbackClips = groupSegmentsIntoHighlightClips(
      ungroupedSegments.map((segment) => ({
        ...segment,
        clip_group: null,
        clip_title: null,
      }))
    );

    return [...clips, ...fallbackClips]
      .sort((a, b) => a.startTime - b.startTime)
      .map((clip, index) => ({
        ...clip,
        id: `clip_${String(index + 1).padStart(3, "0")}`,
        index: index + 1,
      }));
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

  return groups.map((groupSegments, index) => buildClip(index + 1, groupSegments));
}

export function reelsToHighlightClips(
  reels: HighlightReel[],
  segments: Segment[],
  options: {
    qualityStage?: HighlightQualityStage;
    qualityReport?: HighlightReelQualityReport | null;
  } = {},
): HighlightClip[] {
  const segmentMap = new Map(segments.map((segment) => [segment.id, segment]));
  const quality = options.qualityStage
    ? {
        stage: options.qualityStage,
        passed: options.qualityReport?.passed,
        score: options.qualityReport?.score,
        issues: options.qualityReport?.issues ?? [],
        repairActions: options.qualityReport?.repair_actions ?? [],
        repairCount: options.qualityReport?.repair_count ?? 0,
        degraded: options.qualityReport?.degraded ?? false,
      }
    : undefined;
  return reels
    .filter((reel) => !reel.hidden)
    .sort((a, b) => a.start - b.start)
    .map((reel, index) => {
      const matchedSegments = reel.segment_ids
        .map((segmentId) => segmentMap.get(segmentId))
        .filter((segment): segment is Segment => Boolean(segment));
      const reelSegments = matchedSegments.length === reel.segment_ids.length
        ? matchedSegments
        : [];
      return {
        id: reel.reel_id,
        reelId: reel.reel_id,
        canonicalReel: reel,
        clipGroup: reel.clip_group,
        occurrenceId: reel.occurrence_id,
        productGroupId: reel.product_group_id,
        sourceOccurrenceIds: reel.source_occurrence_ids ?? (
          reel.occurrence_id ? [reel.occurrence_id] : []
        ),
        productName: reel.product_name,
        strategyKind: reel.strategy_kind,
        retentionRatio: reel.retention_ratio,
        quality,
        index: index + 1,
        segments: reelSegments,
        title: reel.title,
        duration: reel.duration,
        startTime: reel.start,
        endTime: reel.end,
        status: reel.quality_status ?? reel.status,
        exportStatus: reel.export_status,
        score: reel.score,
        issues: reel.issues,
        createdAt: reel.created_at,
        hidden: reel.hidden,
      };
    });
}

export function highlightClipsFromTask(
  reels: HighlightReel[] | undefined,
  segments: Segment[],
  options: {
    qualityStage?: HighlightQualityStage;
    qualityReport?: HighlightReelQualityReport | null;
  } = {},
): HighlightClip[] {
  if (reels !== undefined) {
    return reelsToHighlightClips(reels, segments, options);
  }
  return groupSegmentsIntoHighlightClips(segments);
}

/**
 * 新任务优先按全局产品归组，旧任务缺 product_group_id 时继续按 occurrence_id
 * 使用原有三策略展示；连 occurrence_id 都没有的更早任务保持平铺。
 */
export function aggregateHighlightClipsByOccurrence(
  clips: HighlightClip[],
  productOccurrences: ProductOccurrence[] = [],
  productGroups: ProductGroup[] = [],
): HighlightClipAggregation {
  const occurrenceOrder = new Map(
    productOccurrences.map((occurrence, index) => [occurrence.occurrence_id, index]),
  );
  const occurrenceNames = new Map(
    productOccurrences.map((occurrence) => [
      occurrence.occurrence_id,
      occurrence.name || occurrence.product_name,
    ]),
  );
  const productGroupOrder = new Map(
    productGroups.map((group, index) => [group.product_group_id, index]),
  );
  const productGroupNames = new Map(
    productGroups.map((group) => [group.product_group_id, group.canonical_name]),
  );
  const productGroupOccurrenceIds = new Map(
    productGroups.map((group) => [group.product_group_id, group.occurrence_ids ?? []]),
  );
  const grouped = new Map<string, HighlightClip[]>();
  const legacyClips: HighlightClip[] = [];

  clips.forEach((clip) => {
    const groupKey = clip.productGroupId
      ? `product:${clip.productGroupId}`
      : clip.occurrenceId
        ? `occurrence:${clip.occurrenceId}`
        : null;
    if (!groupKey) {
      legacyClips.push(clip);
      return;
    }
    const group = grouped.get(groupKey) ?? [];
    group.push(clip);
    grouped.set(groupKey, group);
  });

  const occurrenceGroups = Array.from(grouped.entries())
    .map(([groupKey, groupClips]) => {
      const groupingKind = groupKey.startsWith("product:") ? "product" as const : "occurrence" as const;
      const groupId = groupKey.slice(groupKey.indexOf(":") + 1);
      const sortedClips = groupClips
        .map((clip, sourceIndex) => ({ clip, sourceIndex }))
        .sort((left, right) => {
          const leftStrategy = left.clip.strategyKind
            ? HIGHLIGHT_STRATEGY_ORDER.indexOf(left.clip.strategyKind)
            : HIGHLIGHT_STRATEGY_ORDER.length;
          const rightStrategy = right.clip.strategyKind
            ? HIGHLIGHT_STRATEGY_ORDER.indexOf(right.clip.strategyKind)
            : HIGHLIGHT_STRATEGY_ORDER.length;
          return leftStrategy - rightStrategy || left.sourceIndex - right.sourceIndex;
        })
        .map(({ clip }) => clip);
      const presentStrategies = new Set(
        sortedClips
          .map((clip) => clip.strategyKind)
          .filter((strategy): strategy is HighlightStrategyKind => Boolean(strategy)),
      );
      const missingStrategies = HIGHLIGHT_STRATEGY_ORDER.filter(
        (strategy) => !presentStrategies.has(strategy),
      );
      const reelSourceOccurrenceIds = Array.from(new Set(
        sortedClips.flatMap((clip) =>
          clip.sourceOccurrenceIds.length > 0
            ? clip.sourceOccurrenceIds
            : clip.occurrenceId
              ? [clip.occurrenceId]
              : []
        ),
      ));
      const sourceOccurrenceIds = reelSourceOccurrenceIds.length > 0
        ? reelSourceOccurrenceIds
        : groupingKind === "product"
          ? productGroupOccurrenceIds.get(groupId) ?? []
          : [];
      const productName =
        (groupingKind === "product" ? productGroupNames.get(groupId) : occurrenceNames.get(groupId))
        || sortedClips.find((clip) => clip.productName)?.productName
        || "未命名产品";

      return {
        groupId,
        groupingKind,
        occurrenceId: groupingKind === "occurrence" ? groupId : undefined,
        productGroupId: groupingKind === "product" ? groupId : undefined,
        productName,
        sourceOccurrenceIds,
        sourceOccurrenceCount: sourceOccurrenceIds.length,
        clips: sortedClips,
        strategyCount: presentStrategies.size,
        completenessLabel: `${presentStrategies.size}/3`,
        missingStrategies,
      };
    })
    .sort((left, right) => {
      const leftOrder = left.groupingKind === "product"
        ? productGroupOrder.get(left.groupId)
        : occurrenceOrder.get(left.groupId);
      const rightOrder = right.groupingKind === "product"
        ? productGroupOrder.get(right.groupId)
        : occurrenceOrder.get(right.groupId);
      if (leftOrder != null || rightOrder != null) {
        return (leftOrder ?? Number.MAX_SAFE_INTEGER) - (rightOrder ?? Number.MAX_SAFE_INTEGER);
      }
      const leftStart = Math.min(...left.clips.map((clip) => clip.startTime));
      const rightStart = Math.min(...right.clips.map((clip) => clip.startTime));
      return leftStart - rightStart || left.groupId.localeCompare(right.groupId);
    });

  return { occurrenceGroups, legacyClips };
}

/**
 * 最终列表的现代数据每个产品只展示一个 winner。旧任务没有 product_group_id，
 * 仍完整保留原来的 occurrence/平铺行为。
 */
export function selectFinalHighlightWinners(clips: HighlightClip[]): HighlightClip[] {
  const seenProductGroups = new Set<string>();
  return clips.filter((clip) => {
    if (!clip.productGroupId) return true;
    if (seenProductGroups.has(clip.productGroupId)) return false;
    seenProductGroups.add(clip.productGroupId);
    return true;
  });
}

/**
 * 只修改一个 canonical 集锦的句子成员。
 * 新增句子必须属于同一产品场次且仍为 keep；移出操作允许清理当前已有成员，
 * 最终是否合法（例如不可为空）继续由后端 canonical 契约统一校验。
 */
export function updateHighlightSegmentMembership({
  reels,
  reelId,
  segmentId,
  action,
  segments,
  productOccurrences,
  productGroups = [],
}: UpdateHighlightSegmentMembershipInput): HighlightReel[] {
  const targetReel = reels.find((reel) => reel.reel_id === reelId);
  if (!targetReel) {
    throw new Error("当前产品集锦已变化，请重新选择");
  }

  const segment = segments.find((item) => item.id === segmentId);
  if (!segment) {
    throw new Error("当前字幕句子已变化，请刷新后重试");
  }

  if (action === "add") {
    const productGroup = targetReel.product_group_id
      ? productGroups.find(
          (item) => item.product_group_id === targetReel.product_group_id,
        )
      : undefined;
    const occurrence = !productGroup
      ? productOccurrences.find(
          (item) => item.occurrence_id === targetReel.occurrence_id,
        )
      : undefined;
    const allowedSegmentIds = productGroup?.segment_ids ?? occurrence?.segment_ids;
    if (!allowedSegmentIds?.includes(segmentId)) {
      throw new Error("只能加入当前产品场次内的句子");
    }
    if (segment.action !== "keep") {
      throw new Error("只能加入处于保留状态的句子");
    }
  }

  const nextIds = new Set(targetReel.segment_ids);
  if (action === "add") {
    nextIds.add(segmentId);
  } else {
    nextIds.delete(segmentId);
  }

  const segmentStart = new Map(segments.map((item) => [item.id, item.start]));
  const originalOrder = new Map(targetReel.segment_ids.map((id, index) => [id, index]));
  const orderedIds = Array.from(nextIds).sort((left, right) => {
    const leftStart = segmentStart.get(left);
    const rightStart = segmentStart.get(right);
    if (leftStart != null && rightStart != null) return leftStart - rightStart;
    if (leftStart != null) return -1;
    if (rightStart != null) return 1;
    return (originalOrder.get(left) ?? Number.MAX_SAFE_INTEGER)
      - (originalOrder.get(right) ?? Number.MAX_SAFE_INTEGER);
  });

  return reels.map((reel) => (
    reel.reel_id === reelId
      ? { ...reel, segment_ids: orderedIds }
      : reel
  ));
}
