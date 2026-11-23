import React, { useState, type Dispatch, type SetStateAction } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { HighlightReelBuildOptions, Task } from "@/lib/api";
import { HighlightClipsPanel } from "./HighlightClipsPanel";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function makeTask(): Task {
  return {
    id: "task-highlight-panel",
    name: "产品集锦测试",
    task_type: "highlight_reel",
    status: "review",
    audit_segments: [
      { id: "seg-a", start: 0, end: 12, text: "土豆抖音动画很好用", action: "keep" },
      { id: "seg-b", start: 13, end: 22, text: "模板可以快速升级 App", action: "keep" },
      { id: "seg-c", start: 40, end: 54, text: "另一段产品介绍", action: "keep" },
    ],
    highlight_reels: [
      {
        reel_id: "reel-a",
        clip_group: 1,
        title: "土豆抖音动画四季",
        segment_ids: ["seg-a", "seg-b"],
        start: 0,
        end: 22,
        duration: 22,
        status: "warning",
        issues: [{ code: "HR-01", severity: "major", message: "部分描述需要复核" }],
        hidden: false,
      },
      {
        reel_id: "reel-b",
        clip_group: 2,
        title: "App 平台升级",
        segment_ids: ["seg-c"],
        start: 40,
        end: 54,
        duration: 14,
        status: "passed",
        issues: [],
        hidden: false,
      },
    ],
    highlight_reels_before_quality: [
      {
        reel_id: "reel-before",
        clip_group: 1,
        title: "质检前版本",
        segment_ids: ["seg-a"],
        start: 0,
        end: 12,
        duration: 12,
        status: "failed",
        issues: [],
        hidden: false,
      },
    ],
    highlight_quality_report: {
      passed: false,
      score: 0.72,
      issues: [{ code: "HR-01", severity: "major", message: "需要复核" }],
    },
  } as unknown as Task;
}

function makeOccurrenceTask(): Task {
  const task = makeTask();
  const reel = (
    reelId: string,
    occurrenceId: string,
    strategyKind: "faithful" | "balanced" | "concise",
    title: string,
    segmentId: string,
    productName: string,
    retentionRatio: number,
  ) => ({
    reel_id: reelId,
    clip_group: occurrenceId === "occ-1" ? 1 : 2,
    occurrence_id: occurrenceId,
    product_name: productName,
    strategy_kind: strategyKind,
    retention_ratio: retentionRatio,
    title,
    segment_ids: [segmentId],
    start: task.audit_segments.find((segment) => segment.id === segmentId)?.start ?? 0,
    end: task.audit_segments.find((segment) => segment.id === segmentId)?.end ?? 0,
    duration: 10,
    status: "passed" as const,
    issues: [],
    hidden: false,
  });
  task.product_occurrences = [
    { occurrence_id: "occ-1", product_name: "土豆动画" },
    { occurrence_id: "occ-2", product_name: "升级 App" },
  ];
  task.highlight_reels = [
    reel("occ-1-concise", "occ-1", "concise", "土豆精简", "seg-c", "土豆动画", 0.42),
    reel("occ-1-faithful", "occ-1", "faithful", "土豆忠实", "seg-a", "土豆动画", 0.86),
    reel("occ-1-balanced", "occ-1", "balanced", "土豆均衡", "seg-b", "土豆动画", 0.64),
    reel("occ-2-faithful", "occ-2", "faithful", "App 忠实", "seg-a", "升级 App", 0.88),
    reel("occ-2-balanced", "occ-2", "balanced", "App 均衡", "seg-b", "升级 App", 0.66),
  ];
  return task;
}

function makeProductWinnerTask(): Task {
  const task = makeTask();
  const reel = (
    reelId: string,
    productGroupId: string,
    strategyKind: "faithful" | "balanced" | "concise",
    title: string,
    segmentId: string,
    productName: string,
    sourceOccurrenceIds: string[],
  ) => ({
    reel_id: reelId,
    clip_group: productGroupId === "product-1" ? 1 : 2,
    product_group_id: productGroupId,
    source_occurrence_ids: sourceOccurrenceIds,
    product_name: productName,
    strategy_kind: strategyKind,
    title,
    segment_ids: [segmentId],
    start: task.audit_segments.find((segment) => segment.id === segmentId)?.start ?? 0,
    end: task.audit_segments.find((segment) => segment.id === segmentId)?.end ?? 0,
    duration: 10,
    status: "passed" as const,
    issues: [],
    hidden: false,
  });
  task.product_groups = [
    {
      product_group_id: "product-1",
      canonical_name: "土豆动画",
      occurrence_ids: ["occ-1", "occ-2"],
    },
    {
      product_group_id: "product-2",
      canonical_name: "升级 App",
      occurrence_ids: ["occ-3"],
    },
  ];
  task.highlight_reels = [
    reel("product-1-winner", "product-1", "balanced", "土豆最佳集锦", "seg-a", "土豆动画", ["occ-1", "occ-2"]),
    reel("product-2-winner", "product-2", "concise", "App 最佳集锦", "seg-c", "升级 App", ["occ-3"]),
  ];
  task.highlight_reels_before_quality = [
    reel("product-1-faithful", "product-1", "faithful", "土豆忠实候选", "seg-a", "土豆动画", ["occ-1", "occ-2"]),
    reel("product-1-balanced", "product-1", "balanced", "土豆均衡候选", "seg-b", "土豆动画", ["occ-1", "occ-2"]),
    reel("product-1-concise", "product-1", "concise", "土豆精简候选", "seg-c", "土豆动画", ["occ-1", "occ-2"]),
  ];
  return task;
}

function renderPanel({
  onOptionsCommit = vi.fn(),
  onFocusClip = vi.fn(),
  onPlayClip = vi.fn(),
  onQuickPreview,
  onExport = vi.fn(),
  task = makeTask(),
}: {
  onOptionsCommit?: ReturnType<typeof vi.fn>;
  onFocusClip?: ReturnType<typeof vi.fn>;
  onPlayClip?: ReturnType<typeof vi.fn>;
  onQuickPreview?: ReturnType<typeof vi.fn>;
  onExport?: ReturnType<typeof vi.fn>;
  task?: Task;
} = {}) {
  function Harness() {
    const [buildOptions, setBuildOptions] = useState<HighlightReelBuildOptions>({
      target_product_count: null,
      target_reel_count: null,
      min_reel_duration: 60,
      max_reel_duration: 300,
      target_total_duration: null,
    });
    const [focusedClipId, setFocusedClipId] = useState<string | null>(null);
    const handleBuildOptionsChange: Dispatch<SetStateAction<HighlightReelBuildOptions>> = (updater) => {
      setBuildOptions((prev) => {
        const next = typeof updater === "function" ? updater(prev) : updater;
        onOptionsCommit(next);
        return next;
      });
    };

    return (
      <div className="h-[520px]">
        <HighlightClipsPanel
          task={task}
          isExporting={false}
          onPlayClip={onPlayClip}
          onQuickPreview={onQuickPreview}
          focusedClipId={focusedClipId}
          onFocusClip={(snapshot) => {
            setFocusedClipId(snapshot?.clip.id ?? null);
            onFocusClip(snapshot);
          }}
          onExport={onExport}
          onTaskUpdated={vi.fn()}
          onPrimaryAction={vi.fn()}
          autoRunModelLabel="qwen"
          buildOptions={buildOptions}
          onBuildOptionsChange={handleBuildOptionsChange}
        />
      </div>
    );
  }

  render(<Harness />);
  return { onOptionsCommit, onFocusClip, onPlayClip, onExport };
}

describe("HighlightClipsPanel", () => {
  it("shows a compact config summary and applies dialog edits", async () => {
    const onOptionsCommit = vi.fn();
    renderPanel({ onOptionsCommit });

    expect(screen.getByText(/产品数自动 · 60-300s · 总长自动/)).toBeInTheDocument();
    expect(screen.queryByLabelText("条数")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "编辑配置" }));

    fireEvent.change(screen.getByLabelText("条数"), { target: { value: "3" } });
    fireEvent.change(screen.getByLabelText("目标总长(s)"), { target: { value: "180" } });
    fireEvent.change(screen.getByLabelText("最短时长(s)"), { target: { value: "15" } });
    fireEvent.change(screen.getByLabelText("最长时长(s)"), { target: { value: "60" } });
    fireEvent.click(screen.getByRole("button", { name: "应用配置" }));

    await waitFor(() => {
      expect(screen.getByText(/3 个产品 · 15-60s · 总长 180s/)).toBeInTheDocument();
    });
    expect(onOptionsCommit).toHaveBeenLastCalledWith({
      target_product_count: 3,
      target_reel_count: 3,
      min_reel_duration: 15,
      max_reel_duration: 60,
      target_total_duration: 180,
    });
  });

  it("keeps current config when dialog edits are cancelled", () => {
    const onOptionsCommit = vi.fn();
    renderPanel({ onOptionsCommit });

    fireEvent.click(screen.getByRole("button", { name: "编辑配置" }));
    fireEvent.change(screen.getByLabelText("条数"), { target: { value: "4" } });
    fireEvent.click(screen.getByRole("button", { name: "取消" }));

    expect(screen.getByText(/产品数自动 · 60-300s · 总长自动/)).toBeInTheDocument();
    expect(onOptionsCommit).not.toHaveBeenCalled();
  });

  it("selects one highlight at a time and toggles the focused transcript clip", () => {
    const { onFocusClip } = renderPanel();

    const firstCard = screen.getByDisplayValue("土豆抖音动画四季").closest(".group");
    const secondCard = screen.getByDisplayValue("App 平台升级").closest(".group");
    expect(firstCard).not.toBeNull();
    expect(secondCard).not.toBeNull();

    expect(screen.getByLabelText(/已选 1 \/ 2 个/)).toBeInTheDocument();
    fireEvent.click(secondCard!);
    expect(screen.getByLabelText(/已选 1 \/ 2 个/)).toBeInTheDocument();

    fireEvent.click(secondCard!);
    expect(screen.getByLabelText(/已选 0 \/ 2 个/)).toBeInTheDocument();

    const focusButton = within(firstCard as HTMLElement).getByRole("button", { name: "查看语句" });
    expect(focusButton).toHaveAttribute("aria-pressed", "false");
    expect(focusButton).toHaveAttribute("title", "查看语句，并可直接加入或移出当前集锦");
    fireEvent.click(focusButton);
    expect(onFocusClip).toHaveBeenCalledWith(expect.objectContaining({
      reel: expect.objectContaining({ title: "土豆抖音动画四季" }),
      qualityStage: "after_quality",
    }));
    expect(focusButton).toHaveAttribute("aria-pressed", "true");
    expect(focusButton).toHaveAccessibleName("完成编辑语句");
    expect(focusButton).toHaveTextContent("完成编辑");

    fireEvent.click(focusButton);
    expect(onFocusClip).toHaveBeenLastCalledWith(null);
    expect(focusButton).toHaveAttribute("aria-pressed", "false");
  });

  it("resets list content when switching highlight variants", async () => {
    renderPanel();

    expect(screen.getByDisplayValue("土豆抖音动画四季")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /质检前/ }));

    await waitFor(() => {
      expect(screen.getByDisplayValue("质检前版本")).toBeInTheDocument();
    });
    expect(screen.queryByDisplayValue("土豆抖音动画四季")).toBeNull();
    expect(screen.getByLabelText(/已选 1 \/ 1 个/)).toBeInTheDocument();
  });

  it("可在 All、质检成功和待复核之间筛选集锦", () => {
    renderPanel();

    const allFilter = screen.getByRole("button", { name: "All" });
    const qualityPassedFilter = screen.getByRole("button", { name: "质检成功" });
    const warningFilter = screen.getByRole("button", { name: "待复核" });
    expect(allFilter).toHaveAttribute("aria-pressed", "true");
    expect(qualityPassedFilter).toHaveAttribute("aria-pressed", "false");
    expect(warningFilter).toHaveAttribute("aria-pressed", "false");

    fireEvent.click(qualityPassedFilter);

    expect(qualityPassedFilter).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByDisplayValue("App 平台升级")).toBeInTheDocument();
    expect(screen.queryByDisplayValue("土豆抖音动画四季")).toBeNull();

    fireEvent.click(warningFilter);

    expect(warningFilter).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByDisplayValue("土豆抖音动画四季")).toBeInTheDocument();
    expect(screen.queryByDisplayValue("App 平台升级")).toBeNull();
  });

  it("质检前快照缺失时不使用字幕伪造历史版本", async () => {
    const task = makeOccurrenceTask();
    task.highlight_reels_before_quality = undefined;
    renderPanel({ task });

    fireEvent.click(screen.getByRole("button", { name: /质检前/ }));
    await waitFor(() => {
      expect(screen.getByText("暂无产品集锦，可直接生成或先完成 AI 审计后更新")).toBeInTheDocument();
    });
    expect(screen.queryByDisplayValue("土豆忠实")).toBeNull();
  });

  it("按产品场次显示三种策略、完整度和缺版", () => {
    renderPanel({ task: makeOccurrenceTask() });

    expect(screen.getByText("土豆动画")).toBeInTheDocument();
    expect(screen.getByText("升级 App")).toBeInTheDocument();
    expect(screen.getByText("3/3 完整度")).toBeInTheDocument();
    expect(screen.getByText("2/3 完整度")).toBeInTheDocument();
    expect(screen.getAllByText("忠实版")).toHaveLength(2);
    expect(screen.getAllByText("均衡版")).toHaveLength(2);
    expect(screen.getAllByText("精简版")).toHaveLength(2);
    expect(screen.getByText("缺版")).toBeInTheDocument();
    expect(screen.getByText("保留率 86%")).toBeInTheDocument();
  });

  it("最终视图每个产品只显示一个 winner，并显示胜出策略和来源场次", () => {
    renderPanel({ task: makeProductWinnerTask() });

    expect(screen.getByDisplayValue("土豆最佳集锦")).toBeInTheDocument();
    expect(screen.getByDisplayValue("App 最佳集锦")).toBeInTheDocument();
    expect(screen.getByText("胜出策略 · 均衡版")).toBeInTheDocument();
    expect(screen.getByText("胜出策略 · 精简版")).toBeInTheDocument();
    expect(screen.getByText("2 个来源场次")).toBeInTheDocument();
    expect(screen.getByText("1 个来源场次")).toBeInTheDocument();
    expect(screen.queryByText("缺版")).toBeNull();
    expect(screen.getByText("2 条 winner")).toBeInTheDocument();
    expect(screen.getByLabelText(/已选 1 \/ 2 个/)).toBeInTheDocument();
  });

  it("质检前展示明确候选，但候选不计入最终条数且不可导出", async () => {
    const onExport = vi.fn();
    renderPanel({ task: makeProductWinnerTask(), onExport });

    expect(screen.getByRole("button", { name: "质检后2" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "质检前3" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "质检前3" }));

    await waitFor(() => {
      expect(screen.getByDisplayValue("土豆忠实候选")).toBeInTheDocument();
    });
    expect(screen.getAllByText(/候选 ·/)).toHaveLength(3);
    expect(screen.getAllByText("3 条候选")).toHaveLength(2);
    expect(screen.getByRole("button", { name: "质检前候选不可导出" })).toBeDisabled();
    fireEvent.click(screen.getAllByTitle("质检前候选不可导出，请切换到质检后 winner")[0]);
    expect(onExport).not.toHaveBeenCalled();
  });

  it("快速试看直接传递当前集锦快照，不请求渲染", () => {
    const onQuickPreview = vi.fn(), fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    renderPanel({ onQuickPreview });
    fireEvent.click(screen.getByRole("button", { name: "快速试看 土豆抖音动画四季" }));
    expect(onQuickPreview).toHaveBeenCalledWith(expect.objectContaining({
      qualityStage: "after_quality", reel: expect.objectContaining({ reel_id: "reel-a" }),
    }));
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("旧渲染响应不能抢占新集锦的预览", async () => {
    const onPlayClip = vi.fn();
    const pending: ((value: unknown) => void)[] = [];
    vi.stubGlobal("fetch", vi.fn(() => new Promise(resolve => pending.push(resolve))));
    renderPanel({ onPlayClip });
    fireEvent.click(screen.getByRole("button", { name: "渲染预览 土豆抖音动画四季" }));
    fireEvent.click(screen.getByRole("button", { name: "渲染预览 App 平台升级" }));
    await act(async () => { pending[1]({ ok: true, json: async () => ({ url: "/new.mp4" }) }); });
    await act(async () => { pending[0]({ ok: true, json: async () => ({ url: "/old.mp4" }) }); });
    expect(onPlayClip).toHaveBeenCalledTimes(1);
    expect(onPlayClip).toHaveBeenCalledWith("/new.mp4", expect.objectContaining({ reel: expect.objectContaining({ reel_id: "reel-b" }) }));
  });

  it("取消等待和快速试看都不会被未完成渲染打断", async () => {
    const onPlayClip = vi.fn(), onQuickPreview = vi.fn();
    const pending: ((value: unknown) => void)[] = [];
    vi.stubGlobal("fetch", vi.fn(() => new Promise(resolve => pending.push(resolve))));
    renderPanel({ onPlayClip, onQuickPreview });
    fireEvent.click(screen.getByRole("button", { name: "渲染预览 土豆抖音动画四季" }));
    fireEvent.click(screen.getByRole("button", { name: "取消等待" }));
    await act(async () => { pending[0]({ ok: true, json: async () => ({ url: "/old.mp4" }) }); });
    fireEvent.click(screen.getByRole("button", { name: "渲染预览 土豆抖音动画四季" }));
    fireEvent.click(screen.getByRole("button", { name: "快速试看 App 平台升级" }));
    await act(async () => { pending[1]({ ok: true, json: async () => ({ url: "/old.mp4" }) }); });
    expect(onQuickPreview).toHaveBeenCalledOnce();
    expect(onPlayClip).not.toHaveBeenCalled();
  });

  it("每种策略可独立预览和导出 canonical reel", async () => {
    const onPlayClip = vi.fn();
    const onExport = vi.fn();
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ url: "/api/exports/preview.mp4" }),
    });
    vi.stubGlobal("fetch", fetchMock);
    renderPanel({ task: makeOccurrenceTask(), onPlayClip, onExport });

    fireEvent.click(screen.getAllByTitle("预览忠实版")[0]);
    await waitFor(() => expect(onPlayClip).toHaveBeenCalled());
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/tasks/task-highlight-panel/export/clip-preview",
      expect.objectContaining({
        body: expect.stringContaining('"reel_id":"occ-1-faithful"'),
      }),
    );
    expect(onPlayClip).toHaveBeenCalledWith(
      "/api/exports/preview.mp4",
      expect.objectContaining({
        reel: expect.objectContaining({ reel_id: "occ-1-faithful" }),
        qualityStage: "after_quality",
      }),
    );

    fireEvent.click(screen.getByTitle("导出精简版"));
    expect(onExport).toHaveBeenCalledWith(expect.objectContaining({
      reel: expect.objectContaining({ reel_id: "occ-1-concise" }),
      qualityStage: "after_quality",
    }));
  });

  it("获取并复制 Cursor 集锦分析指令", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        system_prompt: "system",
        user_prompt: "user",
        combined: "完整 Cursor 集锦分析指令",
      }),
    });
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    vi.stubGlobal("fetch", fetchMock);
    renderPanel({ task: makeOccurrenceTask() });

    fireEvent.click(screen.getByRole("button", { name: "复制 Cursor 指令" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith("/api/tasks/task-highlight-panel/manual-highlight-prompt", undefined);
      expect(writeText).toHaveBeenCalledWith("完整 Cursor 集锦分析指令");
    });
  });

  it("预览并应用 Cursor 集锦结果到最新版结构", async () => {
    const preview = {
      can_apply: true,
      expected_revision: 0,
      input_results_count: 6,
      product_occurrences_count: 2,
      product_groups_count: 1,
      candidates_count: 3,
      winners_count: 1,
      repair_count: 1,
      issues_count: 2,
      blocker_count: 0,
      major_count: 0,
      quality_passed: true,
    };
    const fetchMock = vi.fn().mockImplementation((url: string) => Promise.resolve({
      ok: true,
      json: async () => url.endsWith("/apply")
        ? { ...preview, success: true, message: "升级完成", revision: 1 }
        : preview,
    }));
    vi.stubGlobal("fetch", fetchMock);
    renderPanel({ task: makeOccurrenceTask() });

    fireEvent.click(screen.getByRole("button", { name: "回填结果" }));
    fireEvent.change(screen.getByLabelText("Cursor 集锦结果 JSON"), {
      target: { value: '{"results":[]}' },
    });
    fireEvent.click(screen.getByRole("button", { name: "预览升级" }));

    await waitFor(() => {
      expect(screen.getByText(/6 个 JOB → 1 个产品组/)).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: "应用到最新版结构" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/tasks/task-highlight-panel/manual-highlight-result/apply",
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({
            result_json: '{"results":[]}',
            expected_revision: 0,
          }),
        }),
      );
    });
  });
});
