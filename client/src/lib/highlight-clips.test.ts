import { describe, expect, it } from "vitest";

import type { HighlightReel, Segment } from "@/lib/api";
import {
  aggregateHighlightClipsByOccurrence,
  groupSegmentsIntoHighlightClips,
  highlightClipsFromTask,
  reelsToHighlightClips,
  selectFinalHighlightWinners,
  updateHighlightSegmentMembership,
} from "./highlight-clips";

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

  it("将字幕修正和 ASR 纠错片段视为保留段", () => {
    const clips = groupSegmentsIntoHighlightClips([
      createSegment({
        id: "seg-fix",
        start: 0,
        end: 5,
        text: "剪影很好用",
        display_text: "剪映很好用",
        action: "text_fix",
      }),
      createSegment({ id: "seg-subtitle", start: 5.2, end: 8, text: "就就是这样", action: "subtitle_fix" }),
      createSegment({ id: "seg-delete", start: 8, end: 10, text: "删掉", action: "delete" }),
    ]);

    expect(clips).toHaveLength(1);
    expect(clips[0].title).toBe("剪映很好用");
    expect(clips[0].segments.map((segment) => segment.id)).toEqual(["seg-fix", "seg-subtitle"]);
  });
});

describe("reelsToHighlightClips", () => {
  it("优先使用后端 canonical highlight_reels 计划", () => {
    const segments = [
      createSegment({ id: "seg-a", start: 0, end: 6, clip_group: 1, clip_title: "计划A" }),
      createSegment({ id: "seg-b", start: 20, end: 30, clip_group: 2, clip_title: "计划B" }),
    ];
    const reels: HighlightReel[] = [
      {
        reel_id: "reel-a",
        clip_group: 1,
        title: "计划A",
        segment_ids: ["seg-a"],
        start: 0,
        end: 6,
        duration: 6,
        status: "passed",
        issues: [],
        hidden: false,
      },
      {
        reel_id: "reel-b",
        clip_group: 2,
        title: "计划B",
        segment_ids: ["seg-b"],
        start: 20,
        end: 30,
        duration: 10,
        status: "warning",
        issues: [{ code: "HR-07", severity: "major", message: "时长偏短" }],
        hidden: false,
      },
    ];

    const clips = highlightClipsFromTask(reels, segments);
    expect(clips).toHaveLength(2);
    expect(clips[0].reelId).toBe("reel-a");
    expect(clips[1].issues?.[0].code).toBe("HR-07");
  });

  it("透传产品场次、三版策略、保留率和质检元数据", () => {
    const reel: HighlightReel = {
      reel_id: "reel-faithful",
      clip_group: 1,
      occurrence_id: "occ-1",
      product_group_id: "product-1",
      source_occurrence_ids: ["occ-1", "occ-2"],
      product_name: "土豆动画",
      strategy_kind: "faithful",
      retention_ratio: 0.82,
      title: "忠实版",
      segment_ids: ["seg-a"],
      start: 0,
      end: 6,
      duration: 6,
      status: "passed",
      issues: [],
      hidden: false,
    };

    const clips = highlightClipsFromTask(
      [reel],
      [createSegment({ id: "seg-a", start: 0, end: 6 })],
      {
        qualityStage: "after_quality",
        qualityReport: {
          passed: true,
          score: 0.95,
          issues: [],
          repair_actions: [{ action: "trim", reel_id: reel.reel_id, segment_ids: [], reason: "修复结尾" }],
          repair_count: 1,
        },
      },
    );

    expect(clips[0]).toMatchObject({
      canonicalReel: reel,
      occurrenceId: "occ-1",
      productGroupId: "product-1",
      sourceOccurrenceIds: ["occ-1", "occ-2"],
      productName: "土豆动画",
      strategyKind: "faithful",
      retentionRatio: 0.82,
      quality: {
        stage: "after_quality",
        passed: true,
        score: 0.95,
        repairCount: 1,
      },
    });
  });

  it("当集锦 segment_ids 过期时保持为空并要求重新生成", () => {
    const segments = [
      createSegment({ id: "current-a", start: 6, end: 10, text: "A", action: "keep" }),
      createSegment({ id: "current-b", start: 10, end: 15, text: "B", action: "text_fix", display_text: "修正B" }),
      createSegment({ id: "deleted", start: 15, end: 18, text: "删", action: "delete" }),
      createSegment({ id: "outside", start: 40, end: 45, text: "外", action: "keep" }),
    ];
    const reels: HighlightReel[] = [
      {
        reel_id: "reel-stale",
        clip_group: 1,
        title: "旧计划",
        segment_ids: ["old-a", "old-b"],
        start: 5,
        end: 20,
        duration: 15,
        status: "warning",
        issues: [],
        hidden: false,
      },
    ];

    const clips = highlightClipsFromTask(reels, segments);
    expect(clips[0].segments).toHaveLength(0);
  });

  it("过期集锦时间窗内没有 keep 段时保持为空", () => {
    const segments = [
      createSegment({ id: "deleted-a", start: 6, end: 10, action: "delete" }),
      createSegment({ id: "deleted-b", start: 10, end: 15, action: "delete" }),
    ];
    const reels: HighlightReel[] = [
      {
        reel_id: "reel-stale-delete",
        clip_group: 1,
        title: "旧计划",
        segment_ids: ["old-a"],
        start: 5,
        end: 20,
        duration: 15,
        status: "warning",
        issues: [],
        hidden: false,
      },
    ];

    const clips = highlightClipsFromTask(reels, segments);
    expect(clips[0].segments).toHaveLength(0);
  });

  it("仅在 canonical 字段缺失时回退旧任务分组，空数组不伪造历史", () => {
    const segments = [createSegment({ id: "legacy-seg", start: 0, end: 5 })];

    expect(highlightClipsFromTask(undefined, segments)).toHaveLength(1);
    expect(highlightClipsFromTask([], segments)).toEqual([]);
  });
});

describe("aggregateHighlightClipsByOccurrence", () => {
  it("新任务按全局产品归组并汇总来源场次", () => {
    const clips = reelsToHighlightClips(
      [
        {
          reel_id: "candidate-faithful",
          clip_group: 1,
          occurrence_id: "occ-1",
          product_group_id: "product-1",
          product_name: "产品 A",
          strategy_kind: "faithful",
          title: "忠实候选",
          segment_ids: ["seg-a"],
          start: 0,
          end: 5,
          duration: 5,
          status: "passed",
          issues: [],
          hidden: false,
        },
        {
          reel_id: "candidate-balanced",
          clip_group: 2,
          occurrence_id: "occ-2",
          product_group_id: "product-1",
          product_name: "产品 A",
          strategy_kind: "balanced",
          title: "均衡候选",
          segment_ids: ["seg-b"],
          start: 20,
          end: 25,
          duration: 5,
          status: "passed",
          issues: [],
          hidden: false,
        },
      ],
      [
        createSegment({ id: "seg-a", start: 0, end: 5 }),
        createSegment({ id: "seg-b", start: 20, end: 25 }),
      ],
    );

    const result = aggregateHighlightClipsByOccurrence(
      clips,
      [],
      [{
        product_group_id: "product-1",
        canonical_name: "产品 A",
        occurrence_ids: ["occ-1", "occ-2"],
      }],
    );

    expect(result.occurrenceGroups).toHaveLength(1);
    expect(result.occurrenceGroups[0]).toMatchObject({
      groupId: "product-1",
      groupingKind: "product",
      productName: "产品 A",
      sourceOccurrenceIds: ["occ-1", "occ-2"],
      sourceOccurrenceCount: 2,
    });
    expect(result.occurrenceGroups[0].clips).toHaveLength(2);
  });

  it("按产品场次稳定分组并固定忠实/均衡/精简排序", () => {
    const segments = [
      createSegment({ id: "seg-a", start: 0, end: 5 }),
      createSegment({ id: "seg-b", start: 5, end: 10 }),
      createSegment({ id: "seg-c", start: 10, end: 15 }),
    ];
    const reels: HighlightReel[] = [
      {
        reel_id: "concise",
        clip_group: 1,
        occurrence_id: "occ-1",
        product_name: "产品 A",
        strategy_kind: "concise",
        title: "精简版",
        segment_ids: ["seg-c"],
        start: 10,
        end: 15,
        duration: 5,
        status: "passed",
        issues: [],
        hidden: false,
      },
      {
        reel_id: "faithful",
        clip_group: 1,
        occurrence_id: "occ-1",
        product_name: "产品 A",
        strategy_kind: "faithful",
        title: "忠实版",
        segment_ids: ["seg-a"],
        start: 0,
        end: 5,
        duration: 5,
        status: "passed",
        issues: [],
        hidden: false,
      },
      {
        reel_id: "balanced",
        clip_group: 1,
        occurrence_id: "occ-1",
        product_name: "产品 A",
        strategy_kind: "balanced",
        title: "均衡版",
        segment_ids: ["seg-b"],
        start: 5,
        end: 10,
        duration: 5,
        status: "passed",
        issues: [],
        hidden: false,
      },
    ];

    const result = aggregateHighlightClipsByOccurrence(
      reelsToHighlightClips(reels, segments),
      [{ occurrence_id: "occ-1", product_name: "产品 A" }],
    );

    expect(result.occurrenceGroups).toHaveLength(1);
    expect(result.occurrenceGroups[0].clips.map((clip) => clip.strategyKind)).toEqual([
      "faithful",
      "balanced",
      "concise",
    ]);
    expect(result.occurrenceGroups[0].completenessLabel).toBe("3/3");
    expect(result.occurrenceGroups[0].missingStrategies).toEqual([]);
  });

  it("报告缺版并让无 occurrence_id 的旧任务保持平铺", () => {
    const clips = reelsToHighlightClips(
      [
        {
          reel_id: "balanced",
          clip_group: 1,
          occurrence_id: "occ-1",
          strategy_kind: "balanced",
          title: "均衡版",
          segment_ids: ["seg-a"],
          start: 0,
          end: 5,
          duration: 5,
          status: "passed",
          issues: [],
          hidden: false,
        },
        {
          reel_id: "legacy",
          clip_group: 2,
          title: "旧版集锦",
          segment_ids: ["seg-b"],
          start: 6,
          end: 10,
          duration: 4,
          status: "passed",
          issues: [],
          hidden: false,
        },
      ],
      [
        createSegment({ id: "seg-a", start: 0, end: 5 }),
        createSegment({ id: "seg-b", start: 6, end: 10 }),
      ],
    );

    const result = aggregateHighlightClipsByOccurrence(clips);
    expect(result.occurrenceGroups[0].completenessLabel).toBe("1/3");
    expect(result.occurrenceGroups[0].missingStrategies).toEqual(["faithful", "concise"]);
    expect(result.legacyClips.map((clip) => clip.id)).toEqual(["legacy"]);
  });
});

describe("selectFinalHighlightWinners", () => {
  it("现代任务每个产品只保留一个 winner，旧任务继续按 occurrence 展示", () => {
    const clips = reelsToHighlightClips(
      [
        {
          reel_id: "product-1-winner",
          clip_group: 1,
          product_group_id: "product-1",
          strategy_kind: "balanced",
          title: "产品一 winner",
          segment_ids: ["seg-a"],
          start: 0,
          end: 5,
          duration: 5,
          status: "passed",
          issues: [],
          hidden: false,
        },
        {
          reel_id: "product-1-extra",
          clip_group: 2,
          product_group_id: "product-1",
          strategy_kind: "concise",
          title: "不应重复展示",
          segment_ids: ["seg-b"],
          start: 6,
          end: 10,
          duration: 4,
          status: "passed",
          issues: [],
          hidden: false,
        },
        {
          reel_id: "legacy-occurrence",
          clip_group: 3,
          occurrence_id: "occ-legacy",
          strategy_kind: "faithful",
          title: "旧任务场次",
          segment_ids: ["seg-c"],
          start: 12,
          end: 16,
          duration: 4,
          status: "passed",
          issues: [],
          hidden: false,
        },
      ],
      [
        createSegment({ id: "seg-a", start: 0, end: 5 }),
        createSegment({ id: "seg-b", start: 6, end: 10 }),
        createSegment({ id: "seg-c", start: 12, end: 16 }),
      ],
    );

    expect(selectFinalHighlightWinners(clips).map((clip) => clip.id)).toEqual([
      "product-1-winner",
      "legacy-occurrence",
    ]);
  });
});

describe("updateHighlightSegmentMembership", () => {
  const segments = [
    createSegment({ id: "seg-a", start: 10, end: 12 }),
    createSegment({ id: "seg-b", start: 2, end: 5 }),
    createSegment({ id: "seg-c", start: 20, end: 24 }),
  ];
  const reels: HighlightReel[] = [
    {
      reel_id: "reel-a",
      clip_group: 1,
      occurrence_id: "occ-1",
      title: "产品 A",
      segment_ids: ["seg-a"],
      start: 10,
      end: 12,
      duration: 2,
      status: "passed",
      issues: [],
      hidden: false,
    },
    {
      reel_id: "reel-b",
      clip_group: 1,
      occurrence_id: "occ-1",
      title: "产品 A 另一版",
      segment_ids: ["seg-c"],
      start: 20,
      end: 24,
      duration: 4,
      status: "passed",
      issues: [],
      hidden: false,
    },
  ];
  const productOccurrences = [{
    occurrence_id: "occ-1",
    segment_ids: ["seg-a", "seg-b"],
  }];

  it("只修改当前集锦并按字幕时间排序新增句子", () => {
    const result = updateHighlightSegmentMembership({
      reels,
      reelId: "reel-a",
      segmentId: "seg-b",
      action: "add",
      segments,
      productOccurrences,
    });

    expect(result[0].segment_ids).toEqual(["seg-b", "seg-a"]);
    expect(result[1]).toBe(reels[1]);
    expect(reels[0].segment_ids).toEqual(["seg-a"]);
  });

  it("允许移出已有句子但不直接改写原始数据", () => {
    const result = updateHighlightSegmentMembership({
      reels,
      reelId: "reel-a",
      segmentId: "seg-a",
      action: "remove",
      segments,
      productOccurrences,
    });

    expect(result[0].segment_ids).toEqual([]);
    expect(reels[0].segment_ids).toEqual(["seg-a"]);
  });

  it("新任务按 product group 范围允许加入其他返场句子", () => {
    const winner = {
      ...reels[0],
      occurrence_id: "product-1",
      product_group_id: "product-1",
      source_occurrence_ids: ["occ-1", "occ-2"],
    };
    const result = updateHighlightSegmentMembership({
      reels: [winner],
      reelId: "reel-a",
      segmentId: "seg-b",
      action: "add",
      segments,
      productOccurrences,
      productGroups: [{
        product_group_id: "product-1",
        canonical_name: "产品 A",
        occurrence_ids: ["occ-1", "occ-2"],
        segment_ids: ["seg-a", "seg-b"],
      }],
    });

    expect(result[0].segment_ids).toEqual(["seg-b", "seg-a"]);
  });

  it("拒绝加入场次外或非保留状态的句子", () => {
    expect(() => updateHighlightSegmentMembership({
      reels,
      reelId: "reel-a",
      segmentId: "seg-c",
      action: "add",
      segments,
      productOccurrences,
    })).toThrow("只能加入当前产品场次内的句子");

    expect(() => updateHighlightSegmentMembership({
      reels,
      reelId: "reel-a",
      segmentId: "seg-b",
      action: "add",
      segments: segments.map((segment) => (
        segment.id === "seg-b" ? { ...segment, action: "delete" as const } : segment
      )),
      productOccurrences,
    })).toThrow("只能加入处于保留状态的句子");
  });
});
