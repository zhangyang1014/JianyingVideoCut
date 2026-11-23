import type { HighlightReelBuildOptions, Task, TaskStatus } from "@/lib/api";

export type HighlightReelRunKind = "auto_run" | "rebuild";

const HIGHLIGHT_BLOCKING_STATUSES: TaskStatus[] = [
  "asr_running",
  "diarization_running",
  "analyze_running",
  "audit_running",
  "translate_running",
  "tts_running",
  "dubbing_running",
];

export interface HighlightReelPrimaryAction {
  kind: HighlightReelRunKind;
  label: string;
  pendingLabel: string;
  description: string;
  title: string;
  disabled: boolean;
  isContentDirty: boolean;
  hasAuditSegments: boolean;
  hasHighlightReels: boolean;
}

export function hasHighlightAuditSegments(task: Task): boolean {
  return (task.audit_segments?.length ?? 0) > 0;
}

export function hasContentChangedSinceHighlightBuild(task: Task): boolean {
  const sourceReels = (task.highlight_reels ?? []).filter((reel) => !reel.hidden);
  if (sourceReels.length === 0) return false;
  return sourceReels.some(
    (reel) =>
      typeof reel.source_content_revision === "number" &&
      reel.source_content_revision !== (task.content_revision ?? 0),
  );
}

export function isHighlightActionBlocked(task: Task, explicitProcessing = false): boolean {
  return explicitProcessing || HIGHLIGHT_BLOCKING_STATUSES.includes(task.status);
}

export function getHighlightReelPrimaryAction(
  task: Task,
  pending: { isProcessing?: boolean; rebuildPending?: boolean; autoRunPending?: boolean } = {},
): HighlightReelPrimaryAction {
  const hasAuditSegments = hasHighlightAuditSegments(task);
  const hasHighlightReels = (task.highlight_reels?.length ?? 0) > 0;
  const isContentDirty = hasContentChangedSinceHighlightBuild(task);
  const disabled = isHighlightActionBlocked(task, pending.isProcessing) || Boolean(pending.rebuildPending || pending.autoRunPending);

  if (!hasAuditSegments) {
    return {
      kind: "auto_run",
      label: "生成产品集锦",
      pendingLabel: "生成中",
      description: "完整流程 · ASR/语义审计/集锦自动优化",
      title: "从当前任务启动完整流程：ASR → 产品识别/语义审计 → 产品集锦自动优化",
      disabled,
      isContentDirty,
      hasAuditSegments,
      hasHighlightReels,
    };
  }

  return {
    kind: "rebuild",
    label: "更新产品集锦",
    pendingLabel: "更新中",
    description: isContentDirty
      ? "内容已修改，将用现有审计结果更新"
      : "使用现有审计结果 · 自动优化",
    title: "按现有审计结果更新并自动优化产品集锦，不重新调用 ASR 或语义审计",
    disabled,
    isContentDirty,
    hasAuditSegments,
    hasHighlightReels,
  };
}

export function normalizeHighlightBuildOptions(options: HighlightReelBuildOptions): HighlightReelBuildOptions {
  const minDuration = Math.max(1, Number(options.min_reel_duration) || 60);
  const requestedCount = options.target_product_count ?? options.target_reel_count;
  const targetCount =
    requestedCount == null || Number(requestedCount) <= 0
      ? null
      : Math.max(1, Math.round(Number(requestedCount)));

  return {
    target_product_count: targetCount,
    // 旧后端仍读取 target_reel_count；过渡期保持两个字段完全一致。
    target_reel_count: targetCount,
    min_reel_duration: minDuration,
    max_reel_duration: Math.max(minDuration, Number(options.max_reel_duration) || 300),
    target_total_duration:
      options.target_total_duration == null || Number(options.target_total_duration) <= 0
        ? null
        : Number(options.target_total_duration),
  };
}

export function formatHighlightBuildOptionsSummary(options: HighlightReelBuildOptions): string {
  const normalized = normalizeHighlightBuildOptions(options);
  const targetLabel = normalized.target_product_count == null ? "产品数自动" : `${normalized.target_product_count} 个产品`;
  const totalLabel = normalized.target_total_duration == null ? "总长自动" : `总长 ${normalized.target_total_duration}s`;
  return `${targetLabel} · ${normalized.min_reel_duration}-${normalized.max_reel_duration}s · ${totalLabel}`;
}

export function formatHighlightBuildOptionsLog(options: HighlightReelBuildOptions): string {
  const normalized = normalizeHighlightBuildOptions(options);
  const targetLabel = normalized.target_product_count == null ? "自动产品数" : `${normalized.target_product_count} 个产品`;
  return `目标: ${targetLabel} · 单条 ${normalized.min_reel_duration}-${normalized.max_reel_duration}s`;
}
