import { useEffect, useMemo, useRef, useState, type Dispatch, type SetStateAction } from "react";
import {
  AlertTriangle,
  ChevronDown,
  CheckCircle2,
  ClipboardPaste,
  Copy,
  Download,
  Loader2,
  Play,
  RefreshCw,
  Save,
  Settings2,
  Sparkles,
  Wand2,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  applyManualHighlightResult,
  autoRunHighlightReel,
  exportClipPreview,
  formatDateTime,
  formatDuration,
  formatTimestamp,
  getManualHighlightPrompt,
  type ManualHighlightResultPreview,
  previewManualHighlightResult,
  type HighlightReelBuildOptions,
  type HighlightQualityStage,
  type HighlightStrategyKind,
  rebuildHighlightReels,
  updateHighlightReels,
  type HighlightReel,
  type HighlightReelAutoRunRequest,
  type Task,
} from "@/lib/api";
import {
  aggregateHighlightClipsByOccurrence,
  highlightClipsFromTask,
  selectFinalHighlightWinners,
  type HighlightClip,
} from "@/lib/highlight-clips";
import {
  formatHighlightBuildOptionsLog,
  formatHighlightBuildOptionsSummary,
  getHighlightReelPrimaryAction,
  normalizeHighlightBuildOptions,
} from "@/lib/highlight-reel-action";
import { cn } from "@/lib/utils";
import type { ConsoleEntry } from "@/components/review/ConsoleLog";

function statusBadge(status?: string) {
  switch (status) {
    case "passed":
      return { label: "通过", className: "text-emerald-400 bg-emerald-500/10" };
    case "warning":
      return { label: "待复核", className: "text-amber-400 bg-amber-500/10" };
    case "failed":
      return { label: "不合格", className: "text-red-400 bg-red-500/10" };
    case "exported":
      return { label: "已导出", className: "text-sky-400 bg-sky-500/10" };
    default:
      return { label: "待质检", className: "text-muted-foreground bg-secondary/40" };
  }
}

const STRATEGY_LABELS: Record<HighlightStrategyKind, string> = {
  faithful: "忠实版",
  balanced: "均衡版",
  concise: "精简版",
};

type QualityFilter = "all" | "passed" | "warning";

export interface HighlightClipActionSnapshot {
  clip: HighlightClip;
  /** 后端 canonical reel 快照；旧任务仅有 segment 分组时为空。 */
  reel?: HighlightReel;
  qualityStage: HighlightQualityStage;
}

export function HighlightClipsPanel({
  task,
  isExporting,
  onPlayClip,
  onQuickPreview,
  onFocusClip,
  focusedClipId,
  onExport,
  onTaskUpdated,
  onLog,
  onPrimaryAction,
  primaryActionPending: externalPrimaryActionPending,
  autoRunOptions,
  autoRunModelLabel,
  buildOptions,
  onBuildOptionsChange,
}: {
  task: Task;
  isExporting: boolean;
  onPlayClip: (url: string, snapshot: HighlightClipActionSnapshot) => void;
  onQuickPreview?: (snapshot: HighlightClipActionSnapshot) => void;
  onFocusClip?: (snapshot: HighlightClipActionSnapshot | null) => void;
  focusedClipId?: string | null;
  onExport: (snapshot: HighlightClipActionSnapshot & { reel: HighlightReel }) => void;
  onTaskUpdated?: () => void;
  onPrimaryAction?: (kind: "auto_run" | "rebuild") => void;
  primaryActionPending?: boolean;
  autoRunOptions?: HighlightReelAutoRunRequest;
  autoRunModelLabel?: string;
  buildOptions: HighlightReelBuildOptions;
  onBuildOptionsChange: Dispatch<SetStateAction<HighlightReelBuildOptions>>;
  onLog?: (
    level: ConsoleEntry["level"],
    source: ConsoleEntry["source"],
    message: string,
    progress?: number,
  ) => void;
}) {
  const previousSignatureRef = useRef<string>("");
  const [selectedQualityStage, setSelectedQualityStage] = useState<HighlightQualityStage>("after_quality");
  const activeTaskReels = useMemo(
    () => selectedQualityStage === "before_quality"
      ? task.highlight_reels_before_quality || []
      : task.highlight_reels || [],
    [selectedQualityStage, task.highlight_reels, task.highlight_reels_before_quality],
  );
  const qualityReport = selectedQualityStage === "before_quality"
    ? task.highlight_quality_report_before_quality
    : task.highlight_quality_report;
  const [localReels, setLocalReels] = useState<HighlightReel[]>(activeTaskReels);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [qualityFilter, setQualityFilter] = useState<QualityFilter>("all");

  useEffect(() => {
    setLocalReels(activeTaskReels);
    setSelected(new Set());
    previousSignatureRef.current = "";
  }, [activeTaskReels, selectedQualityStage]);

  const clips = useMemo(
    () => {
      const stageClips = highlightClipsFromTask(
        selectedQualityStage === "after_quality" && task.highlight_reels === undefined
          ? undefined
          : localReels,
        task.audit_segments || [],
        {
          qualityStage: selectedQualityStage,
          qualityReport,
        },
      );
      const selectedStageClips = selectedQualityStage === "after_quality"
        ? selectFinalHighlightWinners(stageClips)
        : stageClips;
      return selectedStageClips.filter((clip) =>
        qualityFilter === "all" || clip.status === qualityFilter
      );
    },
    [localReels, qualityFilter, qualityReport, selectedQualityStage, task.audit_segments, task.highlight_reels]
  );
  const clipAggregation = useMemo(
    () => aggregateHighlightClipsByOccurrence(clips, task.product_occurrences, task.product_groups),
    [clips, task.product_groups, task.product_occurrences],
  );
  const clipDisplayItems = useMemo(() => {
    const items: Array<
      | {
          kind: "group";
          key: string;
          productName: string;
          groupingKind: "product" | "occurrence";
          completenessLabel: string;
          clipCount: number;
          sourceOccurrenceCount: number;
        }
      | { kind: "clip"; key: string; clip: HighlightClip }
      | { kind: "missing"; key: string; strategyKind: HighlightStrategyKind }
      | { kind: "legacy"; key: string }
    > = [];
    clipAggregation.occurrenceGroups.forEach((group) => {
      items.push({
        kind: "group",
        key: `${group.groupingKind}-${group.groupId}`,
        productName: group.productName,
        groupingKind: group.groupingKind,
        completenessLabel: group.completenessLabel,
        clipCount: group.clips.length,
        sourceOccurrenceCount: group.sourceOccurrenceCount,
      });
      if (group.groupingKind === "occurrence") {
        (["faithful", "balanced", "concise"] as HighlightStrategyKind[]).forEach((strategyKind) => {
          const clip = group.clips.find((item) => item.strategyKind === strategyKind);
          items.push(clip
            ? { kind: "clip", key: clip.id, clip }
            : { kind: "missing", key: `${group.groupId}-${strategyKind}`, strategyKind });
        });
        group.clips
          .filter((clip) => !clip.strategyKind)
          .forEach((clip) => items.push({ kind: "clip", key: clip.id, clip }));
      } else {
        group.clips.forEach((clip) => items.push({ kind: "clip", key: clip.id, clip }));
      }
    });
    if (clipAggregation.legacyClips.length > 0) {
      if (clipAggregation.occurrenceGroups.length > 0) {
        items.push({ kind: "legacy", key: "legacy-reels" });
      }
      clipAggregation.legacyClips.forEach((clip) => items.push({ kind: "clip", key: clip.id, clip }));
    }
    return items;
  }, [clipAggregation]);
  const hiddenRejectedReels = useMemo(
    () => localReels.filter((reel) => reel.hidden && reel.status === "failed"),
    [localReels]
  );
  const clipSignature = useMemo(
    () => clips.map((clip) => `${clip.id}:${clip.title}:${clip.segments.length}`).join("|"),
    [clips]
  );

  const [previewLoading, setPreviewLoading] = useState<string | null>(null);
  const previewRequestRef = useRef(0);
  const previewAbortRef = useRef<AbortController | null>(null);
  const invalidatePreviewRequest = () => {
    previewRequestRef.current += 1;
    previewAbortRef.current?.abort();
    previewAbortRef.current = null;
    setPreviewLoading(null);
  };
  useEffect(() => {
    invalidatePreviewRequest();
    return () => { previewRequestRef.current += 1; previewAbortRef.current?.abort(); };
  }, [task.id, task.content_revision, task.highlight_revision, selectedQualityStage]);
  const [exportPending, setExportPending] = useState(false);
  const [savePending, setSavePending] = useState(false);
  const [rebuildPending, setRebuildPending] = useState(false);
  const [autoRunPending, setAutoRunPending] = useState(false);
  const [cursorPromptPending, setCursorPromptPending] = useState(false);
  const [cursorResultDialogOpen, setCursorResultDialogOpen] = useState(false);
  const [cursorResultJson, setCursorResultJson] = useState("");
  const [cursorResultPreview, setCursorResultPreview] = useState<ManualHighlightResultPreview | null>(null);
  const [cursorResultPending, setCursorResultPending] = useState<"preview" | "apply" | null>(null);
  const [titleDrafts, setTitleDrafts] = useState<Record<string, string>>({});
  const [configDialogOpen, setConfigDialogOpen] = useState(false);
  const [configDraft, setConfigDraft] = useState<HighlightReelBuildOptions>(buildOptions);

  useEffect(() => {
    setTitleDrafts(
      Object.fromEntries(clips.map((clip) => [clip.id, clip.title]))
    );
  }, [clipSignature, clips]);

  useEffect(() => {
    if (!clipSignature) {
      setSelected(new Set());
      previousSignatureRef.current = "";
      return;
    }

    if (!previousSignatureRef.current) {
      setSelected(clips[0] ? new Set([clips[0].id]) : new Set());
      previousSignatureRef.current = clipSignature;
      return;
    }

    if (previousSignatureRef.current !== clipSignature) {
      setSelected((prev) => {
        const next = new Set<string>();
        for (const clip of clips) {
          if (prev.has(clip.id)) next.add(clip.id);
        }
        if (next.size === 0) {
          return clips[0] ? new Set([clips[0].id]) : new Set();
        }
        return next;
      });
      previousSignatureRef.current = clipSignature;
    }
  }, [clipSignature, clips]);

  const isBeforeQualityStage = selectedQualityStage === "before_quality";
  const hasSelection = selected.size > 0;
  const selectedCount = clips.filter((clip) => selected.has(clip.id)).length;
  const exportableSelectedClips = clips.filter(
    (clip) =>
      !isBeforeQualityStage
      && selected.has(clip.id)
      && clip.segments.length > 0
      && clip.status === "passed"
      && qualityReport?.degraded !== true
      && Boolean(clip.canonicalReel),
  );
  const selectedDuration = clips
    .filter((clip) => selected.has(clip.id))
    .reduce((sum, clip) => sum + clip.duration, 0);
  const beforeVariantCount = (task.highlight_reels_before_quality || []).filter((reel) => !reel.hidden).length;
  const afterVariantCount = selectFinalHighlightWinners(
    highlightClipsFromTask(task.highlight_reels ?? [], task.audit_segments || []),
  ).length;
  const coherenceReport = task.audit_coherence_report;
  const autoRunRuntimeKind = autoRunOptions?.provider === "ollama" ? "本地 Ollama" : "云端模型";
  const autoRunModel =
    autoRunModelLabel ||
    (autoRunOptions?.provider === "ollama" ? autoRunOptions?.ollama_model : autoRunOptions?.openai_model) ||
    "未指定";
  const isTaskProcessing =
    task.status === "asr_running" ||
    task.status === "diarization_running" ||
    task.status === "analyze_running" ||
    task.status === "audit_running" ||
    task.status === "translate_running" ||
    task.status === "tts_running" ||
    task.status === "dubbing_running";
  const anyPrimaryPending = Boolean(externalPrimaryActionPending || autoRunPending || rebuildPending);
  const canAutoRunHighlights = !isTaskProcessing && !anyPrimaryPending;
  const canCopyCursorPrompt = Boolean(
    task.audit_segments?.length
    && ((task.product_groups?.length ?? 0) > 0 || (task.product_occurrences?.length ?? 0) > 0),
  );
  const primaryAction = getHighlightReelPrimaryAction(task, {
    isProcessing: isTaskProcessing || Boolean(externalPrimaryActionPending),
    rebuildPending,
    autoRunPending,
  });
  const localPrimaryActionPending = primaryAction.kind === "rebuild" ? rebuildPending : autoRunPending;
  const primaryActionPending = externalPrimaryActionPending ?? localPrimaryActionPending;
  const highlightOptionsSummary = formatHighlightBuildOptionsSummary(buildOptions);

  const openConfigDialog = () => {
    setConfigDraft(buildOptions);
    setConfigDialogOpen(true);
  };

  const updateConfigDraft = <K extends keyof HighlightReelBuildOptions>(
    key: K,
    value: HighlightReelBuildOptions[K],
  ) => {
    setConfigDraft((prev) => ({ ...prev, [key]: value }));
  };

  const applyConfigDraft = () => {
    onBuildOptionsChange((prev) => ({ ...prev, ...configDraft }));
    setConfigDialogOpen(false);
  };

  const toggleAll = () => {
    if (hasSelection) {
      setSelected(new Set());
      return;
    }
    setSelected(clips[0] ? new Set([clips[0].id]) : new Set());
  };

  const toggleClip = (clipId: string) => {
    setSelected((prev) => {
      if (prev.has(clipId)) return new Set();
      return new Set([clipId]);
    });
  };

  const normalizedBuildOptions = (): HighlightReelBuildOptions => {
    return normalizeHighlightBuildOptions(buildOptions);
  };

  const buildUpdatedReels = (onlyClipId?: string): HighlightReel[] => {
    const source = localReels.length > 0 ? localReels : activeTaskReels;
    return source.map((reel) => ({
      ...reel,
      title: !onlyClipId || reel.reel_id === onlyClipId
        ? titleDrafts[reel.reel_id] ?? reel.title
        : reel.title,
    }));
  };

  const handleSave = async (onlyClipId?: string) => {
    setSavePending(true);
    try {
      const reels = buildUpdatedReels(onlyClipId);
      const saved = await updateHighlightReels(
        task.id,
        reels,
        task.revision ?? 0,
        selectedQualityStage,
      );
      setLocalReels(saved.reels);
      toast.success(onlyClipId ? "当前版本标题已保存" : `${isBeforeQualityStage ? "质检前" : "质检后"}版本已保存`);
      onTaskUpdated?.();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "保存失败");
    } finally {
      setSavePending(false);
    }
  };

  const handleRebuild = async () => {
    setRebuildPending(true);
    setLocalReels((prev) => prev.map((reel) => ({ ...reel, issues: [] })));
    try {
      const options = normalizedBuildOptions();
      await rebuildHighlightReels(task.id, options);
      onLog?.(
        "info",
        "golden_quote",
        `产品集锦计划更新已启动 | ${formatHighlightBuildOptionsLog(options)} | 使用现有审计结果 | 等待后端日志...`,
        0.01,
      );
      toast.success("产品集锦计划更新已启动");
      onTaskUpdated?.();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "重建失败");
    } finally {
      setRebuildPending(false);
    }
  };

  const handleAutoRun = async () => {
    setAutoRunPending(true);
    try {
      const requestBody = { ...autoRunOptions, ...normalizedBuildOptions(), auto_export: false };
      await autoRunHighlightReel(task.id, requestBody);
      onLog?.(
        "info",
        "audit",
        `直播卖货集锦完整生成已启动 | ${formatHighlightBuildOptionsLog(requestBody)} | 审计调用: ${autoRunRuntimeKind} | 审计模型: ${autoRunModel} | 阶段: ASR/产品识别与审计/产品集锦自动优化 | 等待后端日志...`,
        0.01,
      );
      toast.success(`直播卖货集锦完整生成已启动（审计：${autoRunModel}）`);
      onTaskUpdated?.();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "自动流程启动失败");
    } finally {
      setAutoRunPending(false);
    }
  };

  const handlePrimaryAction = () => {
    if (onPrimaryAction) {
      onPrimaryAction(primaryAction.kind);
      return;
    }
    if (primaryAction.kind === "rebuild") {
      void handleRebuild();
      return;
    }
    void handleAutoRun();
  };

  const handleCopyCursorPrompt = async () => {
    setCursorPromptPending(true);
    try {
      const prompt = await getManualHighlightPrompt(task.id);
      await navigator.clipboard.writeText(prompt.combined);
      toast.success("Cursor 集锦分析指令已复制");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "复制 Cursor 指令失败");
    } finally {
      setCursorPromptPending(false);
    }
  };

  const handlePreviewCursorResult = async () => {
    setCursorResultPending("preview");
    try {
      const preview = await previewManualHighlightResult(
        task.id,
        cursorResultJson,
        task.revision ?? 0,
      );
      setCursorResultPreview(preview);
      toast.success(`预览完成：${preview.product_groups_count} 个产品组`);
    } catch (error) {
      setCursorResultPreview(null);
      toast.error(error instanceof Error ? error.message : "预览 Cursor 结果失败");
    } finally {
      setCursorResultPending(null);
    }
  };

  const handleApplyCursorResult = async () => {
    if (!cursorResultPreview) return;
    setCursorResultPending("apply");
    try {
      const result = await applyManualHighlightResult(
        task.id,
        cursorResultJson,
        cursorResultPreview.expected_revision,
      );
      toast.success(result.message);
      setCursorResultDialogOpen(false);
      setCursorResultJson("");
      setCursorResultPreview(null);
      onTaskUpdated?.();
    } catch (error) {
      setCursorResultPreview(null);
      toast.error(error instanceof Error ? error.message : "应用 Cursor 结果失败");
    } finally {
      setCursorResultPending(null);
    }
  };

  const handleExport = async () => {
    const selectedClips = exportableSelectedClips;
    if (selectedClips.length !== 1 || !selectedClips[0].canonicalReel) return;

    setExportPending(true);
    try {
      onExport({
        clip: selectedClips[0],
        reel: selectedClips[0].canonicalReel,
        qualityStage: selectedQualityStage,
      });
    } finally {
      setExportPending(false);
    }
  };

  const handleExportClip = (clip: HighlightClip) => {
    if (
      isBeforeQualityStage
      || !clip.canonicalReel
      || clip.status !== "passed"
      || qualityReport?.degraded
      || clip.segments.length === 0
    ) return;
    onExport({
      clip,
      reel: clip.canonicalReel,
      qualityStage: selectedQualityStage,
    });
  };

  return (
    <div
      className="flex h-full min-h-0 flex-col overflow-hidden rounded-xl border border-border bg-card"
      data-highlight-clips-panel="true"
      data-quality-stage={selectedQualityStage}
      data-active-reels={activeTaskReels.length}
      data-local-reels={localReels.length}
      data-clips={clips.length}
      data-occurrence-groups={clipAggregation.occurrenceGroups.length}
      data-legacy-clips={clipAggregation.legacyClips.length}
      data-display-items={clipDisplayItems.length}
      data-audit-segments={task.audit_segments?.length ?? 0}
      data-quality-report={qualityReport ? "present" : "missing"}
    >
      <div className="flex h-9 shrink-0 items-center gap-2 border-b border-border px-3">
        <Sparkles className="w-3.5 h-3.5 text-orange-400" />
        <span className="text-xs font-medium text-muted-foreground uppercase tracking-wider">产品集锦</span>
        <span className="text-[11px] text-muted-foreground/50 font-mono">
          {isBeforeQualityStage ? `${clips.length} 条候选` : `${clips.length} 条 winner`}
          {hiddenRejectedReels.length > 0 ? ` · 拦截 ${hiddenRejectedReels.length}` : ""}
        </span>
        <button
          onClick={toggleAll}
          className="ml-auto text-[11px] text-muted-foreground hover:text-foreground transition-colors"
        >
          {hasSelection ? "取消选择" : "选择首条"}
        </button>
      </div>

      <div className="shrink-0 border-b border-border/60 bg-secondary/10 px-3 py-1.5">
        <div className="inline-flex rounded-md border border-border/60 bg-background/40 p-0.5">
          {([
            { key: "after_quality" as const, label: "质检后", count: afterVariantCount },
            { key: "before_quality" as const, label: "质检前", count: beforeVariantCount },
          ]).map((item) => (
            <button
              key={item.key}
              type="button"
              onClick={() => setSelectedQualityStage(item.key)}
              className={cn(
                "h-6 rounded px-2 text-[11px] transition-colors",
                selectedQualityStage === item.key
                  ? "bg-orange-500 text-white"
                  : "text-muted-foreground hover:text-foreground"
              )}
            >
              {item.label}
              <span className="ml-1 opacity-70">{item.count}</span>
            </button>
          ))}
        </div>
        <span className="ml-2 text-[10px] text-muted-foreground/60">
          {isBeforeQualityStage
            ? "质检前策略候选，仅供比较，不计入最终条数"
            : "最终结果；每个产品仅展示一条最佳集锦"}
        </span>
        <div className="ml-auto inline-flex shrink-0 rounded-md border border-border/60 bg-background/40 p-0.5">
          {([
            { key: "all" as const, label: "All" },
            { key: "passed" as const, label: "质检成功" },
            { key: "warning" as const, label: "待复核" },
          ]).map((item) => (
            <button
              key={item.key}
              type="button"
              aria-pressed={qualityFilter === item.key}
              onClick={() => setQualityFilter(item.key)}
              className={cn(
                "h-6 rounded px-2 text-[11px] transition-colors",
                qualityFilter === item.key
                  ? "bg-orange-500 text-white"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {item.label}
            </button>
          ))}
        </div>
      </div>

      {coherenceReport && (
        <div
          className={cn(
            "flex shrink-0 items-start gap-2 border-b border-border px-3 py-1.5 text-[11px]",
            coherenceReport.passed ? "bg-emerald-500/5" : "bg-amber-500/5"
          )}
        >
          {coherenceReport.passed ? (
            <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400 shrink-0 mt-0.5" />
          ) : (
            <AlertTriangle className="w-3.5 h-3.5 text-amber-400 shrink-0 mt-0.5" />
          )}
          <div className="min-w-0 text-muted-foreground">
            <span>
              连贯性{coherenceReport.passed ? "通过" : "需复核"}
              {coherenceReport.repaired_count > 0 ? ` · 自动修复 ${coherenceReport.repaired_count} 处` : ""}
              {coherenceReport.issues.length > 0 ? ` · ${coherenceReport.issues.length} 项问题` : ""}
            </span>
            {!coherenceReport.passed && coherenceReport.issues[0] && (
              <span className="block mt-0.5 text-amber-400/80 truncate">
                {coherenceReport.issues[0].message}
              </span>
            )}
          </div>
        </div>
      )}

      {qualityReport && (
        <div
          className={cn(
            "flex shrink-0 items-center gap-2 border-b border-border px-3 py-1.5 text-[11px]",
            qualityReport.passed ? "bg-emerald-500/5" : "bg-amber-500/5"
          )}
        >
          {qualityReport.passed ? (
            <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400 shrink-0" />
          ) : (
            <AlertTriangle className="w-3.5 h-3.5 text-amber-400 shrink-0" />
          )}
          <span className="truncate text-muted-foreground">
            {isBeforeQualityStage ? "质检前" : "质检后"} · 质检{qualityReport.passed && !qualityReport.degraded ? "通过" : "需复核"}
            {qualityReport.score != null ? ` · 评分 ${(qualityReport.score * 100).toFixed(0)}` : ""}
            {qualityReport.repair_count ? ` · 自动修订 ${qualityReport.repair_count} 项` : ""}
            {qualityReport.degraded ? " · 仅代码校验" : ""}
            {qualityReport.issues.length > 0 ? ` · ${qualityReport.issues.length} 项问题` : ""}
          </span>
        </div>
      )}

      <div className="shrink-0 border-b border-border/60 bg-secondary/10 px-3 py-2">
        <div className="flex items-center justify-between gap-2">
          <div className="min-w-0">
            <span className="block text-[11px] font-medium text-muted-foreground">生成配置</span>
            <span className="block truncate text-[10px] text-muted-foreground/50">
              {highlightOptionsSummary} · 审计模型 {autoRunModel}
            </span>
          </div>
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="h-7 shrink-0 px-2 text-[11px] gap-1"
            onClick={openConfigDialog}
          >
            <Settings2 className="w-3 h-3" />
            编辑配置
          </Button>
        </div>
        <div className="mt-2 flex items-center">
          <Button
            size="sm"
            className="h-7 flex-1 rounded-r-none px-2 text-[11px] gap-1 bg-orange-500 hover:bg-orange-500/85 text-white"
            onClick={handlePrimaryAction}
            disabled={primaryAction.disabled}
            title={primaryAction.title}
          >
            {primaryActionPending ? (
              <Loader2 className="w-3 h-3 animate-spin" />
            ) : primaryAction.kind === "rebuild" ? (
              <RefreshCw className="w-3 h-3" />
            ) : (
              <Wand2 className="w-3 h-3" />
            )}
            {primaryActionPending ? primaryAction.pendingLabel : primaryAction.label}
          </Button>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button
                type="button"
                disabled={!canAutoRunHighlights}
                className={cn(
                  "h-7 w-8 rounded-l-none rounded-r-md border-l border-orange-300/30 bg-orange-500 text-white",
                  "flex items-center justify-center hover:bg-orange-500/85 transition-colors disabled:opacity-50 disabled:cursor-not-allowed",
                )}
                title="产品集锦更多操作"
              >
                <ChevronDown className="w-3.5 h-3.5" />
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-64 bg-card border-border">
              <DropdownMenuLabel className="text-[11px]">高级操作</DropdownMenuLabel>
              <DropdownMenuSeparator />
              <DropdownMenuItem
                className="items-start gap-2 text-xs"
                disabled={!canAutoRunHighlights}
                onSelect={() => {
                  if (onPrimaryAction) {
                    onPrimaryAction("auto_run");
                  } else {
                    void handleAutoRun();
                  }
                }}
              >
                <Wand2 className="mt-0.5 w-3.5 h-3.5 text-orange-300" />
                <span>
                  <span className="block text-foreground">完整重新分析并生成</span>
                  <span className="block text-[10px] text-muted-foreground/70">
                    会重跑 ASR/语义审计/产品分组，耗时更长并可能覆盖现有分组。
                  </span>
                </span>
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="ml-2 h-7 shrink-0 gap-1 px-2 text-[11px]"
            onClick={() => void handleCopyCursorPrompt()}
            disabled={!canCopyCursorPrompt || cursorPromptPending}
            title={
              canCopyCursorPrompt
                ? "复制完整提示词到 Cursor 对话窗"
                : "请先完成产品识别与语义审计"
            }
          >
            {cursorPromptPending ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : (
              <Copy className="h-3 w-3" />
            )}
            复制 Cursor 指令
          </Button>
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="ml-1 h-7 shrink-0 gap-1 px-2 text-[11px]"
            onClick={() => setCursorResultDialogOpen(true)}
            disabled={!canCopyCursorPrompt}
            title="粘贴 Cursor 返回的 JSON，预览后写回最新版产品组结构"
          >
            <ClipboardPaste className="h-3 w-3" />
            回填结果
          </Button>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-2.5 space-y-2">
        {clips.length === 0 && hiddenRejectedReels.length > 0 ? (
          <div className="space-y-3">
            <div className="rounded-lg border border-amber-500/25 bg-amber-500/5 p-3 text-left">
              <div className="flex items-start gap-2">
                <AlertTriangle className="w-4 h-4 text-amber-400 shrink-0 mt-0.5" />
                <div className="min-w-0">
                  <p className="text-xs font-medium text-amber-300">没有合格产品集锦</p>
                  <p className="text-[11px] text-muted-foreground mt-1 leading-relaxed">
                    已生成 {hiddenRejectedReels.length} 条候选，但全部因半句话开头、产品名不清、结尾截断或缺少卖点锚点被硬门槛拦截。逻辑完整性优先于数量和时长。
                  </p>
                </div>
              </div>
            </div>
            <div className="space-y-2">
              {hiddenRejectedReels.map((reel, index) => (
                <div key={reel.reel_id} className="rounded-lg border border-border/50 bg-secondary/15 p-2">
                  <div className="flex items-center gap-2">
                    <span className="text-[11px] font-mono font-bold text-muted-foreground/70">
                      #{String(index + 1).padStart(2, "0")}
                    </span>
                    <span className="text-[10px] px-1.5 py-0.5 rounded text-red-400 bg-red-500/10">已拦截</span>
                    <span className="timestamp text-[11px] ml-auto text-muted-foreground/60">
                      {formatDuration(reel.duration)}
                    </span>
                  </div>
                  <p className="text-xs text-muted-foreground mt-1 truncate">{reel.title}</p>
                  <p className="text-[10px] text-muted-foreground/40 mt-0.5">
                    {formatDateTime(reel.created_at)}
                  </p>
                  {reel.issues?.[0] && (
                    <p className="text-[10px] text-amber-400/80 mt-1.5 leading-snug">
                      {reel.issues[0].message}
                    </p>
                  )}
                </div>
              ))}
            </div>
            <Button
              size="sm"
              variant="outline"
              className="w-full h-7 text-xs gap-1.5"
              onClick={handlePrimaryAction}
              disabled={primaryAction.disabled}
              title={primaryAction.title}
            >
              {primaryActionPending ? <Loader2 className="w-3 h-3 animate-spin" /> : <RefreshCw className="w-3 h-3" />}
              {primaryActionPending ? primaryAction.pendingLabel : primaryAction.label}
            </Button>
          </div>
        ) : clips.length === 0 ? (
          <div className="flex flex-col items-center justify-center gap-3 text-center py-8">
            <Sparkles className="w-8 h-8 text-muted-foreground/20" />
            <p className="text-xs text-muted-foreground">暂无产品集锦，可直接生成或先完成 AI 审计后更新</p>
            <Button
              size="sm"
              variant="outline"
              className="h-7 text-xs gap-1.5"
              onClick={handlePrimaryAction}
              disabled={primaryAction.disabled}
              title={primaryAction.title}
            >
              {primaryActionPending ? <Loader2 className="w-3 h-3 animate-spin" /> : <Wand2 className="w-3 h-3" />}
              {primaryActionPending ? primaryAction.pendingLabel : primaryAction.label}
            </Button>
            <p className="text-[10px] text-muted-foreground/50">审计模型 {autoRunModel}</p>
          </div>
        ) : (
          clipDisplayItems.map((item) => {
            if (item.kind === "group") {
              return (
                <div key={item.key} className="mt-2 first:mt-0 rounded-md border border-orange-500/15 bg-orange-500/5 px-2.5 py-2">
                  <div className="flex items-center gap-2">
                    <span className="truncate text-xs font-medium text-foreground">{item.productName}</span>
                    <span className="ml-auto rounded bg-orange-500/10 px-1.5 py-0.5 font-mono text-[10px] text-orange-300">
                      {item.groupingKind === "product"
                        ? isBeforeQualityStage
                          ? `${item.clipCount} 条候选`
                          : `${item.sourceOccurrenceCount} 个来源场次`
                        : `${item.completenessLabel} 完整度`}
                    </span>
                  </div>
                </div>
              );
            }
            if (item.kind === "missing") {
              return (
                <div key={item.key} className="rounded-lg border border-dashed border-border/50 bg-secondary/10 p-2.5">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-[11px] font-medium text-muted-foreground">
                      {STRATEGY_LABELS[item.strategyKind]}
                    </span>
                    <span className="rounded bg-amber-500/10 px-1.5 py-0.5 text-[10px] text-amber-400">缺版</span>
                  </div>
                  <p className="mt-1 text-[10px] text-muted-foreground/50">本质检阶段尚未生成该策略版本</p>
                </div>
              );
            }
            if (item.kind === "legacy") {
              return (
                <div key={item.key} className="pt-2 text-[10px] text-muted-foreground/60">
                  旧任务集锦（无产品场次信息）
                </div>
              );
            }
            const clip = item.clip;
            const isSelected = selected.has(clip.id);
            const isFocused = focusedClipId === clip.id;
            const badge = statusBadge(clip.status);
            const strategyLabel = clip.strategyKind ? STRATEGY_LABELS[clip.strategyKind] : null;
            const strategyDisplayLabel = strategyLabel
              ? clip.productGroupId
                ? `${isBeforeQualityStage ? "候选" : "胜出策略"} · ${strategyLabel}`
                : strategyLabel
              : null;
            const reelRepairCount = clip.quality?.repairActions.filter(
              (action) => action.reel_id === clip.reelId,
            ).length ?? 0;
            const semanticSegmentCount = clip.segments.filter((segment) =>
              Boolean(segment.compiled_from_operation || segment.semantic_unit_id || (segment.source_segment_ids?.length ?? 0) > 1)
            ).length;
            return (
              <div
                key={item.key}
                className={cn(
                    "group flex items-start gap-2 p-2.5 rounded-lg border transition-all cursor-pointer",
                  isSelected
                    ? "bg-orange-500/5 border-orange-500/20 hover:bg-orange-500/10"
                    : "bg-secondary/20 border-border/40 opacity-60 hover:opacity-90"
                )}
                onClick={() => toggleClip(clip.id)}
              >
                <div
                  className={cn(
                    "w-4 h-4 rounded border-2 flex items-center justify-center shrink-0 mt-0.5 transition-all",
                    isSelected ? "bg-orange-500 border-orange-500" : "border-muted-foreground/40"
                  )}
                >
                  {isSelected && <span className="text-white text-[10px] leading-none">✓</span>}
                </div>

                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    {strategyDisplayLabel ? (
                      <span className="text-[11px] font-semibold text-orange-300">{strategyDisplayLabel}</span>
                    ) : (
                      <span className="text-[11px] font-mono font-bold text-orange-400/70">
                        #{String(clip.index).padStart(2, "0")}
                      </span>
                    )}
                    <span className={cn("text-[10px] px-1.5 py-0.5 rounded", badge.className)}>
                      {badge.label}
                    </span>
                    <span className="timestamp text-[11px] ml-auto text-muted-foreground/60">
                      {formatDuration(clip.duration)}
                    </span>
                  </div>

                  <Input
                    value={titleDrafts[clip.id] ?? clip.title}
                    onClick={(event) => event.stopPropagation()}
                    onChange={(event) =>
                      setTitleDrafts((prev) => ({ ...prev, [clip.id]: event.target.value }))
                    }
                    className="h-6 text-xs bg-background/60 border-border/60 px-2"
                  />

                  <div className="mt-1.5 flex flex-wrap items-center gap-x-1.5 gap-y-0.5">
                    <span className="shrink-0 whitespace-nowrap font-mono text-[10px] text-muted-foreground/40">
                      {formatTimestamp(clip.startTime)}
                    </span>
                    <span className="shrink-0 whitespace-nowrap text-[10px] text-muted-foreground/30">·</span>
                    <span className="shrink-0 whitespace-nowrap text-[10px] text-muted-foreground/40">{clip.segments.length} 段</span>
                    {clip.retentionRatio != null && (
                      <>
                        <span className="shrink-0 whitespace-nowrap text-[10px] text-muted-foreground/30">·</span>
                        <span className="shrink-0 whitespace-nowrap text-[10px] text-muted-foreground/50">
                          保留率 {(clip.retentionRatio * 100).toFixed(0)}%
                        </span>
                      </>
                    )}
                    {clip.createdAt && (
                      <>
                        <span className="shrink-0 whitespace-nowrap text-[10px] text-muted-foreground/30">·</span>
                        <span className="shrink-0 whitespace-nowrap text-[10px] text-muted-foreground/40">{formatDateTime(clip.createdAt)}</span>
                      </>
                    )}
                    {semanticSegmentCount > 0 && (
                      <>
                        <span className="shrink-0 whitespace-nowrap text-[10px] text-muted-foreground/30">·</span>
                        <span className="shrink-0 whitespace-nowrap text-[10px] text-cyan-300/70">语义稿 {semanticSegmentCount}</span>
                      </>
                    )}
                    <button
                      type="button"
                      aria-pressed={isFocused}
                      aria-label={isFocused ? "完成编辑语句" : "查看语句"}
                      title={isFocused ? "完成当前集锦语句编辑" : "查看语句，并可直接加入或移出当前集锦"}
                      className={cn(
                        "ml-auto shrink-0 whitespace-nowrap rounded px-1.5 py-0.5 text-[10px] transition-colors",
                        isFocused
                          ? "bg-orange-500/15 text-orange-300"
                          : "text-muted-foreground hover:bg-secondary/60 hover:text-foreground",
                      )}
                      onClick={(event) => {
                        event.stopPropagation();
                        onFocusClip?.(isFocused ? null : {
                          clip,
                          reel: clip.canonicalReel,
                          qualityStage: selectedQualityStage,
                        });
                      }}
                    >
                      {isFocused ? "完成编辑" : "查看语句"}
                    </button>
                  </div>

                  {onQuickPreview && (
                    <div className="mt-2 flex flex-wrap items-center gap-2">
                      <Button size="sm" variant="outline" className="h-7 gap-1 px-2 text-xs"
                        disabled={clip.segments.length === 0}
                        aria-label={`快速试看 ${clip.title}`}
                        onClick={(event) => {
                          event.stopPropagation();
                          invalidatePreviewRequest();
                          try {
                            if (clip.canonicalReel?.source_content_revision != null && clip.canonicalReel.source_content_revision !== (task.content_revision ?? 0)) {
                              throw new Error("集锦基于旧字幕生成，请更新集锦后再试看");
                            }
                            onQuickPreview({ clip, reel: clip.canonicalReel, qualityStage: selectedQualityStage });
                          } catch (error) { toast.error(error instanceof Error ? error.message : "试看失败"); }
                        }}><Play className="h-3 w-3" />快速试看</Button>
                      <span className="text-[10px] text-muted-foreground">无需渲染 · 按本条选段播放</span>
                    </div>
                  )}

                  {previewLoading === clip.id && (
                    <div className="mt-1 flex items-center gap-2 text-[10px] text-orange-400/80">
                      <span>正在生成预览...</span>
                      <button className="underline" onClick={(event) => { event.stopPropagation(); invalidatePreviewRequest(); }}>取消等待</button>
                    </div>
                  )}

                  {clip.issues && clip.issues.length > 0 && (
                    <p className="mt-1 truncate text-[10px] text-amber-400/80">
                      {clip.issues.length} 项问题 · {clip.issues[0].message}
                    </p>
                  )}
                  {reelRepairCount > 0 && (
                    <p className="mt-1 text-[10px] text-emerald-400/80">已修复 {reelRepairCount} 项</p>
                  )}
                  {clip.issues?.length === 0 && reelRepairCount === 0 && clip.status === "passed" && (
                    <p className="mt-1 text-[10px] text-emerald-400/70">无待处理问题</p>
                  )}

                </div>

                <div className="flex shrink-0 flex-col gap-1">
                  <button
                    disabled={clip.segments.length === 0 || previewLoading === clip.id || exportPending}
                    className="w-6 h-6 rounded-md bg-orange-500/10 flex items-center justify-center text-orange-400 hover:bg-orange-500/25 transition-colors opacity-70 group-hover:opacity-100 disabled:opacity-60 disabled:cursor-wait"
                    onClick={async (event) => {
                      event.stopPropagation();
                      invalidatePreviewRequest();
                      const requestId = previewRequestRef.current;
                      const controller = new AbortController();
                      previewAbortRef.current = controller;
                      setPreviewLoading(clip.id);
                      try {
                        const snapshot = {
                          clip,
                          reel: clip.canonicalReel,
                          qualityStage: selectedQualityStage,
                        };
                        const url = await exportClipPreview(task.id, {
                          signal: controller.signal,
                          reelId: clip.reelId,
                          qualityStage: selectedQualityStage,
                          segmentIds: clip.segments.map((segment) => segment.id),
                        });
                        if (requestId === previewRequestRef.current) onPlayClip(url, snapshot);
                      } catch (error) {
                        if (requestId === previewRequestRef.current && !controller.signal.aborted) toast.error(error instanceof Error ? error.message : "预览失败");
                      } finally {
                        if (requestId === previewRequestRef.current) setPreviewLoading(null);
                      }
                    }}
                    aria-label={`渲染预览 ${clip.title}`}
                    title={clip.segments.length === 0
                      ? "当前字幕段已变化，请重建集锦"
                      : `预览${strategyLabel ?? "整条集锦"}`}
                  >
                    {previewLoading === clip.id ? (
                      <Loader2 className="w-3 h-3 animate-spin" />
                    ) : (
                      <Play className="w-3 h-3 ml-0.5" />
                    )}
                  </button>
                  <button
                    disabled={!clip.canonicalReel || savePending}
                    className="w-6 h-6 rounded-md bg-secondary/50 flex items-center justify-center text-muted-foreground hover:text-foreground transition-colors disabled:opacity-40"
                    onClick={(event) => {
                      event.stopPropagation();
                      void handleSave(clip.id);
                    }}
                    title={`保存${strategyLabel ?? "当前集锦"}标题`}
                  >
                    <Save className="w-3 h-3" />
                  </button>
                  <button
                    disabled={
                      isBeforeQualityStage
                      || !clip.canonicalReel
                      || clip.status !== "passed"
                      || qualityReport?.degraded
                      || clip.segments.length === 0
                      || isExporting
                      || exportPending
                    }
                    className="w-6 h-6 rounded-md bg-secondary/50 flex items-center justify-center text-muted-foreground hover:text-orange-300 transition-colors disabled:opacity-40"
                    onClick={(event) => {
                      event.stopPropagation();
                      handleExportClip(clip);
                    }}
                    title={
                      isBeforeQualityStage
                        ? "质检前候选不可导出，请切换到质检后 winner"
                        : clip.status !== "passed" || qualityReport?.degraded
                          ? "当前集锦尚未完整通过质检"
                          : clip.segments.length === 0
                            ? "集锦引用的字幕片段已变化，请重新生成"
                            : `导出${strategyLabel ?? "当前集锦"}`
                    }
                  >
                    <Download className="w-3 h-3" />
                  </button>
                </div>
              </div>
            );
          })
        )}
      </div>

      {clips.length > 0 && (
        <div className="shrink-0 border-t border-border p-2.5 space-y-2">
          <div
            className="flex items-center justify-between text-[11px] text-muted-foreground/60"
            aria-label={`已选 ${selectedCount} / ${clips.length} 个，${formatDuration(selectedDuration)}`}
          >
            <span>
              已选 <span className="text-orange-400 font-mono font-medium">{selectedCount}</span> / {clips.length} {isBeforeQualityStage ? "条候选" : "条 winner"}
            </span>
            <span className="timestamp">{formatDuration(selectedDuration)}</span>
          </div>

          <div className="grid grid-cols-2 gap-2">
            <Button
              variant="outline"
              size="sm"
              className="h-8 text-[11px] gap-1"
              onClick={() => void handleSave()}
              disabled={savePending}
              title="保存标题、隐藏状态等人工调整"
            >
              {savePending ? <Loader2 className="w-3 h-3 animate-spin" /> : <Save className="w-3 h-3" />}
              保存
            </Button>
            <Button
              variant="outline"
              size="sm"
              className="h-8 text-[11px] gap-1"
              onClick={handlePrimaryAction}
              disabled={primaryAction.disabled}
              title={primaryAction.title}
            >
              {primaryActionPending ? (
                <Loader2 className="w-3 h-3 animate-spin" />
              ) : primaryAction.kind === "rebuild" ? (
                <RefreshCw className="w-3 h-3" />
              ) : (
                <Wand2 className="w-3 h-3" />
              )}
              {primaryActionPending ? primaryAction.pendingLabel : primaryAction.label}
            </Button>
          </div>
          <div className="text-[10px] text-muted-foreground/50 truncate">
            {primaryAction.description} · 审计模型 {autoRunModel}
          </div>

          <Button
            className="w-full bg-orange-500 hover:bg-orange-500/85 text-white text-xs gap-1.5 h-8"
            size="sm"
            onClick={handleExport}
            disabled={exportableSelectedClips.length !== 1 || isExporting || exportPending}
          >
            {isExporting || exportPending ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <Download className="w-3.5 h-3.5" />
            )}
            {isExporting || exportPending
              ? "导出中"
              : isBeforeQualityStage
                ? "质检前候选不可导出"
                : exportableSelectedClips.length === 1
                  ? "导出当前 winner"
                  : "选择 1 条 winner 导出"}
          </Button>
        </div>
      )}

      <Dialog
        open={cursorResultDialogOpen}
        onOpenChange={(open) => {
          setCursorResultDialogOpen(open);
          if (!open) setCursorResultPreview(null);
        }}
      >
        <DialogContent className="sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle className="text-base">回填 Cursor 集锦结果</DialogTitle>
            <DialogDescription className="text-xs">
              支持旧 occurrence_id 和最新 product_group_id 输出。系统会先按当前任务版本预览、归组和质检，再原子写回。
            </DialogDescription>
          </DialogHeader>

          <Textarea
            aria-label="Cursor 集锦结果 JSON"
            value={cursorResultJson}
            onChange={(event) => {
              setCursorResultJson(event.target.value);
              setCursorResultPreview(null);
            }}
            placeholder='粘贴 {"results":[...]}'
            className="min-h-56 font-mono text-[11px]"
          />

          {cursorResultPreview && (
            <div className="rounded-md border border-emerald-500/25 bg-emerald-500/5 px-3 py-2 text-xs text-muted-foreground">
              <p className="font-medium text-emerald-300">可以安全写回</p>
              <p className="mt-1">
                {cursorResultPreview.input_results_count} 个 JOB → {cursorResultPreview.product_groups_count} 个产品组
                · {cursorResultPreview.candidates_count} 条候选 · {cursorResultPreview.winners_count} 条 winner
              </p>
              <p className="mt-1 text-[11px]">
                自动修订 {cursorResultPreview.repair_count} 项 · 原始质检问题 {cursorResultPreview.issues_count} 项
              </p>
            </div>
          )}

          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="h-8 text-xs"
              onClick={() => setCursorResultDialogOpen(false)}
              disabled={cursorResultPending !== null}
            >
              取消
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="h-8 text-xs"
              onClick={() => void handlePreviewCursorResult()}
              disabled={!cursorResultJson.trim() || cursorResultPending !== null}
            >
              {cursorResultPending === "preview" && <Loader2 className="mr-1 h-3 w-3 animate-spin" />}
              预览升级
            </Button>
            <Button
              type="button"
              size="sm"
              className="h-8 bg-orange-500 text-xs text-white hover:bg-orange-500/85"
              onClick={() => void handleApplyCursorResult()}
              disabled={!cursorResultPreview || cursorResultPending !== null}
            >
              {cursorResultPending === "apply" && <Loader2 className="mr-1 h-3 w-3 animate-spin" />}
              应用到最新版结构
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={configDialogOpen} onOpenChange={setConfigDialogOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle className="text-base">产品集锦配置</DialogTitle>
            <DialogDescription className="text-xs">
              只调整生成目标；标题、选择和导出仍在右侧集锦列表中处理。
            </DialogDescription>
          </DialogHeader>

          <div className="grid grid-cols-2 gap-3">
            <label className="space-y-1.5">
              <span className="text-xs text-muted-foreground">条数</span>
              <Input
                type="number"
                min={1}
                max={20}
                placeholder="自动"
                value={configDraft.target_product_count ?? configDraft.target_reel_count ?? ""}
                onChange={(event) => {
                  const count = event.target.value ? Number(event.target.value) : null;
                  setConfigDraft((prev) => ({
                    ...prev,
                    target_product_count: count,
                    target_reel_count: count,
                  }));
                }}
                className="h-8 text-xs"
              />
            </label>
            <label className="space-y-1.5">
              <span className="text-xs text-muted-foreground">目标总长(s)</span>
              <Input
                type="number"
                min={0}
                placeholder="自动"
                value={configDraft.target_total_duration ?? ""}
                onChange={(event) =>
                  updateConfigDraft("target_total_duration", event.target.value ? Number(event.target.value) : null)
                }
                className="h-8 text-xs"
              />
            </label>
            <label className="space-y-1.5">
              <span className="text-xs text-muted-foreground">最短时长(s)</span>
              <Input
                type="number"
                min={1}
                placeholder="60"
                value={configDraft.min_reel_duration ?? ""}
                onChange={(event) =>
                  updateConfigDraft("min_reel_duration", event.target.value ? Number(event.target.value) : undefined)
                }
                className="h-8 text-xs"
              />
            </label>
            <label className="space-y-1.5">
              <span className="text-xs text-muted-foreground">最长时长(s)</span>
              <Input
                type="number"
                min={1}
                placeholder="300"
                value={configDraft.max_reel_duration ?? ""}
                onChange={(event) =>
                  updateConfigDraft("max_reel_duration", event.target.value ? Number(event.target.value) : undefined)
                }
                className="h-8 text-xs"
              />
            </label>
          </div>

          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="h-8 text-xs"
              onClick={() => setConfigDialogOpen(false)}
            >
              取消
            </Button>
            <Button
              type="button"
              size="sm"
              className="h-8 bg-orange-500 text-xs text-white hover:bg-orange-500/85"
              onClick={applyConfigDraft}
            >
              应用配置
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
