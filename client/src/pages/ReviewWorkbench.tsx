/**
 * GoldenClip Review Workbench
 * Design: 暗金剪辑台 · 编导美学
 * Layout: Left (Video Player) | Center (Subtitle Audit Stream) | Right (Speaker Panel)
 * Bottom: Real-time Console Log
 */

import { useState, useEffect, useRef, useCallback, useMemo } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useParams, useLocation } from "wouter";
import { Button } from "@/components/ui/button";
import { toast } from "sonner";
import {
  Play, ArrowLeft, Zap, Scissors, Download,
  Loader2, AlertCircle, RefreshCw, Eye, EyeOff,
  Mic, RotateCcw, Cpu, Trash2, X, Clock, Users,
  Sparkles, Hand, Copy, Upload, BookOpen,
  Undo2, Redo2, Coins,
} from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import {
  fetchTask, fetchTaskRuntimeHealth, triggerASR, triggerDiarization, triggerAnalyze, triggerAudit, toggleSegment,
  exportFFmpeg, exportJianying, revealExport, cancelExport, cancelTask, createLogWebSocket,
  exportHighlightClips,
  autoRunHighlightReel,
  previewSubtitleOptimization,
  applySubtitleOptimization,
  getEffectiveSubtitleText,
  canResumeExecution,
  defaultEnableDiarizationForTaskType,
  formatDuration,
  formatTimestamp,
  classifyReviewExecutionActivity,
  getExecutionCheckpointCount,
  getTaskActiveExecutionRun,
  getTaskExecutionRun,
  isExecutionFailureFinal,
  isExecutionRetrying,
  rebuildHighlightReels,
  updateHighlightReels,
  STATUS_LABELS, STATUS_COLORS,
  switchASRVersion, patchSegment, batchSegmentAction, updateSegments, mergeSegmentNext,
  updateClipRange,
  getManualAuditPrompt, getManualHighlightPrompt, submitManualAuditResult,
  type Task, type TaskStatus, type Segment, type LogMessage, type ASRSnapshot, type AuditSnapshot,
  type WordTimestamp,
  type ManualPromptPack,
  type SubtitleStyle,
  type GoldenQuoteCandidate,
  type TaskRuntimeHealth,
  type HighlightReelBuildOptions,
  type HighlightQualityStage,
  type HighlightReel,
  type ExportJob,
  type RenderProfile,
  type SubtitleOptimizationPreview,
  type ModelRunConfirmation,
  type ModelRunPreview,
  type ModelRunPreviewRequest,
  DEFAULT_AUDIT_MODEL,
} from "@/lib/api";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import { useAppConfig, buildFeatureRequestOptions, resolveFeatureLlm, getModelShortLabel } from "@/hooks/useAppConfig";
import { useUndoHistory } from "@/hooks/useUndoHistory";
import {
  Accordion,
  AccordionItem,
  AccordionTrigger,
  AccordionContent,
} from "@/components/ui/accordion";
import {
  HighlightClipsPanel,
  type HighlightClipActionSnapshot,
} from "@/components/HighlightClipsPanel";
import { AsrAnalyzeDialog, AsrModelDialog, type AsrSelection } from "@/components/review/AsrModelDialog";
import { ConsoleLog, type ConsoleEntry, type RuntimeHealthTone } from "@/components/review/ConsoleLog";
import { SegmentItem } from "@/components/review/SegmentItem";
import { SubtitleOptimizeDialog } from "@/components/review/SubtitleOptimizeDialog";
import { VideoPlayer, type PlaybackMode } from "@/components/review/VideoPlayer";
import { CutReviewPanel } from "@/components/review/CutReviewPanel";
import { buildHighlightPreviewRanges } from "@/lib/highlight-preview";
import { ReferenceScriptPanel } from "@/components/review/ReferenceScriptPanel";
import type { AuditionRequest } from "@/lib/cut-review";
import { createPlaybackClock } from "@/lib/playback-clock";
import { ResegmentPanel, SpeakerPanel } from "@/components/review/SpeakerPanel";
import { StatusStepper, type ReviewStepDef } from "@/components/review/StatusStepper";
import { StepActivityChip, type StepActivityRecord } from "@/components/review/StepActivityChip";
import { ExportPanel } from "@/components/review/ExportPanel";
import {
  BURN_SUBTITLES_LS_KEY,
  DEFAULT_SUBTITLE_STYLE,
  SUBTITLE_STYLE_LS_KEY,
} from "@/components/review/SubtitleConfig";
import { ScenarioInfoPanel } from "@/components/review/ScenarioInfoPanel";
import { FundsUsagePanel } from "@/components/review/FundsUsagePanel";
import {
  ReviewToolDock,
  type ReviewToolId,
  type ReviewToolItem,
} from "@/components/review/ReviewToolDock";
import { ReasonPickerSheet } from "@/components/review/ReasonPickerSheet";
import { PromptFeedbackDialog } from "@/components/review/PromptFeedbackDialog";
import { OneClickTranslateWorkbench } from "@/components/review/OneClickTranslateWorkbench";
import { AiRunConfirmationDialog } from "@/components/review/AiRunConfirmationDialog";
import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from "@/components/ui/resizable";
import { formatAsrTime, splitSegmentByWords, splitSegmentAt } from "@/components/review/utils";
import {
  shouldPromptAsrOnEntry,
  hasShownAsrPrompt,
  markAsrPromptShown,
} from "@/lib/review-asr-prompt";
import {
  formatHighlightBuildOptionsLog,
  formatHighlightBuildOptionsSummary,
  getHighlightReelPrimaryAction,
  normalizeHighlightBuildOptions,
} from "@/lib/highlight-reel-action";
import { buildHighlightAutoRunModelOptions } from "@/lib/highlight-auto-run-options";
import { reelsToHighlightClips, updateHighlightSegmentMembership } from "@/lib/highlight-clips";
import {
  createSubtitleTimeIndex,
  isSubtitleRowVisible,
  resolveSubtitleScrollTarget,
  shouldScrollSubtitleTarget,
  type SubtitleNavigationState,
} from "@/lib/subtitle-scroll";

const PROCESSING_STATUSES: TaskStatus[] = [
  "asr_running",
  "diarization_running",
  "analyze_running",
  "audit_running",
  "translate_running",
  "tts_running",
  "dubbing_running",
];
const VIDEO_VIEWPORT_MIN_HEIGHT = 160;
const VIDEO_VIEWPORT_MAX_HEIGHT = 520;
const VIDEO_VIEWPORT_DEFAULT_HEIGHT = 220;
const VIDEO_VIEWPORT_PORTRAIT_DEFAULT_HEIGHT = 420;

type RecognitionPreviewChunk = {
  id: string;
  start: number;
  end: number;
  text: string;
  wordCount: number;
};

type PendingModelRun = {
  request: ModelRunPreviewRequest;
  confirmLabel?: string;
  execute: (preview: ModelRunPreview) => Promise<void>;
};

function getActiveExportJob(task?: Task | null): ExportJob | null {
  const jobs = task?.export_jobs || [];
  for (let index = jobs.length - 1; index >= 0; index -= 1) {
    const job = jobs[index];
    if (job.status === "queued" || job.status === "running") return job;
  }
  return null;
}

function mergeExportJob(task: Task, job: ExportJob): Task {
  const jobs = task.export_jobs || [];
  const exists = jobs.some((item) => item.id === job.id);
  const isFinalTaskExport = job.kind === "ffmpeg" && job.render_profile === "final";
  return {
    ...task,
    latest_export_job_id: isFinalTaskExport ? job.id : task.latest_export_job_id,
    export_jobs: exists
      ? jobs.map((item) => (item.id === job.id ? job : item))
      : [...jobs, job],
    export_path: isFinalTaskExport ? (job.output_path || task.export_path) : task.export_path,
    jianying_draft_path: job.draft_path || task.jianying_draft_path,
  };
}

function buildRecognitionPreviewChunks(words: WordTimestamp[]): RecognitionPreviewChunk[] {
  const chunks: RecognitionPreviewChunk[] = [];
  let current: WordTimestamp[] = [];

  const flush = () => {
    if (current.length === 0) return;
    const text = current.map((word) => word.word).join("").trim();
    if (text) {
      const first = current[0];
      const last = current[current.length - 1];
      chunks.push({
        id: `${first.start.toFixed(3)}-${last.end.toFixed(3)}-${chunks.length}`,
        start: first.start,
        end: last.end,
        text,
        wordCount: current.length,
      });
    }
    current = [];
  };

  for (const word of words) {
    if (!word.word.trim()) continue;
    const previous = current[current.length - 1];
    const gap = previous ? word.start - previous.end : 0;
    if (current.length > 0 && gap >= 0.8) {
      flush();
    }

    current.push(word);
    const currentText = current.map((item) => item.word).join("");
    if (/[。！？!?；;]$/.test(word.word) || currentText.length >= 80) {
      flush();
    }
  }

  flush();
  return chunks;
}

function formatAnalysisReportValue(value: unknown, key?: string): string {
  if (typeof value === "boolean") return value ? "开启" : "关闭";
  if (value === null || value === undefined || value === "") return "-";
  if (typeof value === "number" && key && key.includes("threshold")) return `${value.toFixed(2)}s`;
  if (typeof value === "object") {
    return Object.entries(value as Record<string, unknown>)
      .map(([itemKey, itemValue]) => `${formatAnalysisReportLabel(itemKey)} ${itemValue}`)
      .join(" · ") || "-";
  }
  return String(value);
}

function formatAnalysisReportLabel(key: string): string {
  const labels: Record<string, string> = {
    silence_threshold: "停顿切分",
    enable_diarization: "说话人参与分段",
    diar_smooth_threshold: "说话人平滑",
    glossary_terms: "词典术语",
    filler_words: "语气词列表",
    max_segment_duration: "最长字幕卡",
    short_segment_candidate_threshold: "短片段候选",
    recognized_words: "识别字词",
    analyzed_words: "整理字词",
    segments_raw: "初始分段",
    segments_final: "最终分段",
    speakers: "说话人数",
    diar_segments: "说话人段",
    stutter_marks: "重复标记",
    filler_marks: "语气词标记",
    glossary_changes: "词典修正",
    candidate_edits: "候选编辑",
    short_segment_candidates: "短片段候选",
    total: "总段数",
    avg_chars: "平均字数",
    over_limit_count: "超长段数",
    over_limit_pct: "超长占比",
    max_chars: "最长字数",
    recommended_threshold: "推荐分段",
  };
  return labels[key] ?? key;
}

function clampVideoViewportHeight(height: number) {
  return Math.min(VIDEO_VIEWPORT_MAX_HEIGHT, Math.max(VIDEO_VIEWPORT_MIN_HEIGHT, height));
}

function isEditableKeyboardTarget(target: EventTarget | null) {
  return target instanceof HTMLElement && Boolean(target.closest("input, textarea, select, [contenteditable=true]"));
}

function buildRuntimeHealthDisplay(
  health: TaskRuntimeHealth | null,
  healthCheckFailed: boolean,
  lastLogTimestamp: string | null,
  hasStaleProgressLog = false,
): { label: string; tone: RuntimeHealthTone } | null {
  if (healthCheckFailed) {
    return { label: "自检失败", tone: "unknown" };
  }
  if (!health) {
    return { label: "自检中...", tone: "unknown" };
  }
  if (!health.expected_running && !hasStaleProgressLog) {
    return null;
  }

  const silentMs = lastLogTimestamp
    ? Math.max(0, Date.now() - new Date(lastLogTimestamp).getTime())
    : 0;
  const silentMinutes = Math.floor(silentMs / 60000);

  if (health.alive) {
    if (silentMinutes >= 1) {
      return {
        label: `无新日志 ${silentMinutes} 分钟 · 后端仍在运行`,
        tone: "ok",
      };
    }
    return { label: "持续运行中 · 自检通过", tone: "ok" };
  }

  return { label: "疑似中断 · 未检测到活跃任务", tone: "error" };
}

function isProgressLikeConsoleEntry(entry: ConsoleEntry | null): boolean {
  if (!entry || entry.level === "error" || entry.level === "success") return false;
  return (
    entry.message.endsWith("...") ||
    entry.message.includes("识别中") ||
    entry.message.includes("审计中") ||
    entry.message.includes("批模型调用中") ||
    entry.message.includes("云端审计进度") ||
    entry.message.includes("导出中") ||
    entry.message.includes("处理中")
  );
}

function auditErrorTypeDescription(errorType: string | null): string {
  switch (errorType) {
    case "model_call_failed":
      return "模型调用失败，可能是网络、API Key、模型服务或本地 Ollama 异常。";
    case "contract_failed":
      return "模型已返回结果，但输出没有通过审计契约校验。";
    case "highlight_contract_failed":
      return "审计已完成，但产品集锦计划没有通过结构校验。";
    case "highlight_validation_failed":
      return "审计已完成，但产品集锦自动验收失败。";
    default:
      return "云端 AI 审计没有产出可直接采用的结果。";
  }
}

// ============================================================
// Review Workbench Main
// ============================================================
export default function ReviewWorkbench() {
  const params = useParams<{ id: string }>();
  const taskId = params.id;
  const [, navigate] = useLocation();
  const { config, updateConfig } = useAppConfig();

  const [task, setTask] = useState<Task | null>(null);
  const segmentHistory = useUndoHistory<Segment[]>(50);
  const [loading, setLoading] = useState(true);
  const playbackClock = useMemo(() => createPlaybackClock(), [taskId]);
  const [seekRequest, setSeekRequest] = useState({ id: 0, time: 0 });
  const setCurrentTime = useCallback((time: number) => {
    playbackClock.setTime(time);
    setSeekRequest((previous) => ({ id: previous.id + 1, time }));
  }, [playbackClock]);
  useEffect(() => { setCurrentTime(0); }, [setCurrentTime]);
  const [auditionRequest, setAuditionRequest] = useState<AuditionRequest | null>(null);
  const auditionRequestId = useRef(0);
  const [previewVideoUrl, setPreviewVideoUrl] = useState<string | null>(null);
  const previewVideoUrlRef = useRef(previewVideoUrl);
  useEffect(() => { previewVideoUrlRef.current = previewVideoUrl; }, [previewVideoUrl]);
  const [previewTitle, setPreviewTitle] = useState<string | null>(null);
  const [previewPlayRequestId, setPreviewPlayRequestId] = useState(0);
  const [focusedHighlight, setFocusedHighlight] = useState<{
    id: string;
    title: string;
    reel?: HighlightReel;
    qualityStage: HighlightQualityStage;
    segmentIds: Set<string>;
  } | null>(null);
  const [highlightSegmentPendingId, setHighlightSegmentPendingId] = useState<string | null>(null);
  const [activeSegmentId, setActiveSegmentId] = useState<string | null>(null);
  const [editingSegmentId, setEditingSegmentId] = useState<string | null>(null);
  const [subtitleNavigation, setSubtitleNavigation] = useState<SubtitleNavigationState | null>(null);
  // 按 taskId 隔离的 localStorage key
  const consoleStorageKey = `gc_console_${taskId}`;
  // 初始化时从 localStorage 恢复历史日志
  const [consoleEntries, setConsoleEntries] = useState<ConsoleEntry[]>(() => {
    try {
      const saved = localStorage.getItem(`gc_console_${taskId}`);
      if (saved) return JSON.parse(saved) as ConsoleEntry[];
    } catch {
      // 解析失败则从空数组开始
    }
    return [];
  });
  const [consoleExpanded, setConsoleExpanded] = useState(true);
  const [runtimeHealth, setRuntimeHealth] = useState<TaskRuntimeHealth | null>(null);
  const [healthCheckFailed, setHealthCheckFailed] = useState(false);
  const [showOnlyKept, setShowOnlyKept] = useState(false);
  const [playbackMode, setPlaybackMode] = useState<PlaybackMode>("all");
  const hasSavedVideoViewportHeightRef = useRef(false);
  const hasAppliedVideoOrientationDefaultRef = useRef(false);
  const [videoViewportHeight, setVideoViewportHeight] = useState(() => {
    try {
      const saved = localStorage.getItem(`gc_video_viewport_height_${taskId}`);
      if (saved) {
        const parsed = Number(saved);
        if (Number.isFinite(parsed)) {
          hasSavedVideoViewportHeightRef.current = true;
          return clampVideoViewportHeight(parsed);
        }
      }
    } catch {
      // 忽略本地偏好读取失败，使用默认高度
    }
    return VIDEO_VIEWPORT_DEFAULT_HEIGHT;
  });
  const [styleMode, setStyleMode] = useState<"immersive" | "quick_cut">("immersive");
  const [asrDialogOpen, setAsrDialogOpen] = useState(false);
  const [analyzeDialogOpen, setAnalyzeDialogOpen] = useState(false);
  const [subtitleOptimizeOpen, setSubtitleOptimizeOpen] = useState(false);
  const [subtitleOptimizePreview, setSubtitleOptimizePreview] = useState<SubtitleOptimizationPreview | null>(null);
  const [subtitleOptimizeLoading, setSubtitleOptimizeLoading] = useState(false);
  const [subtitleOptimizeApplying, setSubtitleOptimizeApplying] = useState(false);
  const [subtitleOptimizeSelectedIds, setSubtitleOptimizeSelectedIds] = useState<Set<string>>(new Set());
  const [resegmentDialogOpen, setResegmentDialogOpen] = useState(false);
  const [analysisReportOpen, setAnalysisReportOpen] = useState(false);
  const [asrSelection, setAsrSelection] = useState<AsrSelection>({
    backend: "funasr",
    model: "paraformer-zh",
    silenceThreshold: 0.3,
    enableDiarization: false,
  });
  const [highlightAutoRunPending, setHighlightAutoRunPending] = useState(false);
  const [highlightRebuildPending, setHighlightRebuildPending] = useState(false);
  const [highlightCursorPromptPending, setHighlightCursorPromptPending] = useState(false);
  const [taskCancelPending, setTaskCancelPending] = useState(false);
  const [highlightBuildOptions, setHighlightBuildOptions] = useState<HighlightReelBuildOptions>({
    target_product_count: null,
    target_reel_count: null,
    min_reel_duration: 60,
    max_reel_duration: 300,
    target_total_duration: null,
  });
  // 切换 ASR 历史版本的二次确认弹窗（null 表示关闭，数字表示目标版本号）
  const [switchAsrConfirmVersion, setSwitchAsrConfirmVersion] = useState<number | null>(null);
  const [asrSwitchingVersion, setAsrSwitchingVersion] = useState<number | null>(null);
  // 无 API Key 时弹出确认框，询问是否使用本地规则引擎
  const [ruleEngineDialogOpen, setRuleEngineDialogOpen] = useState(false);
  // 云端 API 调用失败时弹出确认框
  const [claudeFailedDialogOpen, setClaudeFailedDialogOpen] = useState(false);
  const [claudeFailedError, setClaudeFailedError] = useState("");
  const [claudeFailedErrorType, setClaudeFailedErrorType] = useState<string | null>(null);
  // 「重新审计」选择弹窗（合并了原「重审」和「AI 审计」逻辑）
  const [reauditMode, setReauditMode] = useState<"keep" | "reset">("keep");
  const [pendingModelRun, setPendingModelRun] = useState<PendingModelRun | null>(null);
  // 提示词反馈优化弹窗
  const [promptFeedbackDialogOpen, setPromptFeedbackDialogOpen] = useState(false);
  // 手动 toggle 后弹出原因选择
  const [pendingReasonSeg, setPendingReasonSeg] = useState<{ id: string; action: "keep" | "delete" } | null>(null);
  // 手动审计弹窗
  const [manualAuditDialogOpen, setManualAuditDialogOpen] = useState(false);
  const [manualAuditPrompt, setManualAuditPrompt] = useState<ManualPromptPack | null>(null);
  const [manualAuditLoading, setManualAuditLoading] = useState(false);
  const [manualAuditSubmitting, setManualAuditSubmitting] = useState(false);
  const [manualAuditInput, setManualAuditInput] = useState("");
  const [manualAuditTab, setManualAuditTab] = useState<"combined" | "system" | "user">("combined");
  // 防抖学习提示条
  const [feedbackNudgeShown, setFeedbackNudgeShown] = useState(false);
  const [nudgeDismissed, setNudgeDismissed] = useState(false);
  const nudgeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const wsRef = useRef<{ close: () => void } | null>(null);
  const staleProgressNoticeRef = useRef<string | null>(null);
  const previewRestoreTimeRef = useRef<number | null>(null);
  const segmentListRef = useRef<HTMLDivElement>(null);
  const subtitleNavigationRequestIdRef = useRef(0);
  const [segmentOverscan, setSegmentOverscan] = useState(18);
  // 字级删除状态
  const [wordEditSegId, setWordEditSegId] = useState<string | null>(null);
  const [selectedWordIndices, setSelectedWordIndices] = useState<Set<number>>(new Set());
  const lastWordClickRef = useRef<number>(-1);
  // 裁断模式状态
  const [splitSegId, setSplitSegId] = useState<string | null>(null);
  const [quickSplitMode, setQuickSplitMode] = useState(false);
  // 金句开场：本地缓存已选的有序 segment_id 列表（与 task.golden_quote_order 同步）
  const [goldenQuoteOrder, setGoldenQuoteOrder] = useState<string[]>([]);
  // 金句候选 & 分析标记：按 taskId 持久化到 localStorage，刷新页面后不丢失
  const goldenCandidatesLsKey = `gc_golden_candidates_${taskId}`;
  const [goldenCandidatesTop, _setGoldenCandidatesTop] = useState<GoldenQuoteCandidate[]>(() => {
    try {
      const saved = localStorage.getItem(`gc_golden_candidates_${taskId}`);
      if (saved) return JSON.parse(saved) as GoldenQuoteCandidate[];
    } catch { /* ignore */ }
    return [];
  });
  const [goldenAnalyzedTop, _setGoldenAnalyzedTop] = useState(() => {
    try {
      return localStorage.getItem(`gc_golden_analyzed_${taskId}`) === "true";
    } catch { /* ignore */ }
    return false;
  });
  const setGoldenCandidatesTop: React.Dispatch<React.SetStateAction<GoldenQuoteCandidate[]>> = useCallback((v) => {
    _setGoldenCandidatesTop(prev => {
      const next = typeof v === "function" ? v(prev) : v;
      try { localStorage.setItem(goldenCandidatesLsKey, JSON.stringify(next)); } catch { /* ignore */ }
      return next;
    });
  }, [goldenCandidatesLsKey]);
  const setGoldenAnalyzedTop: React.Dispatch<React.SetStateAction<boolean>> = useCallback((v) => {
    _setGoldenAnalyzedTop(prev => {
      const next = typeof v === "function" ? v(prev) : v;
      try { localStorage.setItem(`gc_golden_analyzed_${taskId}`, String(next)); } catch { /* ignore */ }
      return next;
    });
  }, [taskId]);
  const [goldenDialogOpenTop, setGoldenDialogOpenTop] = useState(false);

  const handleVideoViewportHeightChange = useCallback((height: number) => {
    const next = clampVideoViewportHeight(height);
    hasSavedVideoViewportHeightRef.current = true;
    setVideoViewportHeight(next);
    try {
      localStorage.setItem(`gc_video_viewport_height_${taskId}`, String(next));
    } catch {
      // 忽略本地偏好保存失败
    }
  }, [taskId]);

  const handleVideoNaturalSize = useCallback((size: {
    width: number;
    height: number;
    suggestedViewportHeight?: number;
  }) => {
    if (
      hasSavedVideoViewportHeightRef.current ||
      hasAppliedVideoOrientationDefaultRef.current ||
      size.width <= 0 ||
      size.height <= 0
    ) {
      return;
    }

    hasAppliedVideoOrientationDefaultRef.current = true;
    const isPortrait = size.width < size.height;
    setVideoViewportHeight(clampVideoViewportHeight(
      size.suggestedViewportHeight
        ?? (isPortrait ? VIDEO_VIEWPORT_PORTRAIT_DEFAULT_HEIGHT : VIDEO_VIEWPORT_DEFAULT_HEIGHT),
    ));
  }, []);

  const segments = task?.audit_segments ?? [];
  const highlightPlaybackSegmentIds = useMemo(() => {
    const reels = task?.highlight_reels?.length
      ? task.highlight_reels
      : task?.highlight_reels_before_quality ?? [];
    return new Set(
      reelsToHighlightClips(reels, segments)
        .flatMap((clip) => clip.segments.map((segment) => segment.id)),
    );
  }, [segments, task?.highlight_reels, task?.highlight_reels_before_quality]);
  useEffect(() => {
    if (playbackMode === "highlight-reel" && highlightPlaybackSegmentIds.size === 0) {
      setPlaybackMode("all");
    }
  }, [highlightPlaybackSegmentIds, playbackMode]);
  const displaySegments = useMemo(
    () => showOnlyKept
      ? segments.filter((segment) => ["keep", "subtitle_fix", "text_fix", "merge_next"].includes(segment.action))
      : segments,
    [segments, showOnlyKept],
  );
  const segmentIndexById = useMemo(
    () => new Map(segments.map((segment, index) => [segment.id, index])),
    [segments],
  );
  const displaySegmentIndexById = useMemo(
    () => new Map(displaySegments.map((segment, index) => [segment.id, index])),
    [displaySegments],
  );
  const focusedOccurrenceSegmentIds = useMemo(() => {
    const productGroupId = focusedHighlight?.reel?.product_group_id;
    if (productGroupId) {
      const productGroup = task?.product_groups?.find(
        (item) => item.product_group_id === productGroupId,
      );
      if (productGroup) return new Set(productGroup.segment_ids ?? []);
    }
    const occurrenceId = focusedHighlight?.reel?.occurrence_id;
    if (!occurrenceId) return new Set<string>();
    const occurrence = task?.product_occurrences?.find(
      (item) => item.occurrence_id === occurrenceId,
    );
    return new Set(occurrence?.segment_ids ?? []);
  }, [
    focusedHighlight?.reel?.occurrence_id,
    focusedHighlight?.reel?.product_group_id,
    task?.product_groups,
    task?.product_occurrences,
  ]);
  const playbackSegments = useMemo(
    () => [...segments].sort((left, right) =>
      left.start - right.start || left.end - right.end
    ),
    [segments],
  );

  useEffect(() => {
    const listElement = segmentListRef.current;
    if (!listElement || typeof ResizeObserver === "undefined") return;

    const updateOverscan = () => {
      // 默认卡片约 96px；额外挂载约两个可视屏幕，滚动时无需等待下一批字幕渲染。
      const visibleRows = Math.max(1, Math.ceil(listElement.clientHeight / 96));
      const nextOverscan = Math.min(36, Math.max(14, visibleRows * 2));
      setSegmentOverscan((current) => current === nextOverscan ? current : nextOverscan);
    };

    const observer = new ResizeObserver(updateOverscan);
    observer.observe(listElement);
    updateOverscan();
    return () => observer.disconnect();
  }, [displaySegments.length, task?.task_type]);

  const segmentVirtualizer = useVirtualizer({
    count: task?.task_type === "one_click_translate" ? 0 : displaySegments.length,
    getScrollElement: () => segmentListRef.current,
    estimateSize: () => 96,
    overscan: segmentOverscan,
    gap: 4,
    getItemKey: (index) => displaySegments[index]?.id ?? index,
  });

  const requestSubtitleNavigation = useCallback((
    segmentId: string,
    source: SubtitleNavigationState["source"],
  ) => {
    subtitleNavigationRequestIdRef.current += 1;
    setSubtitleNavigation({
      segmentId,
      requestId: subtitleNavigationRequestIdRef.current,
      source,
      playbackAnchorId: activeSegmentId,
      consumed: false,
    });
  }, [activeSegmentId]);

  useEffect(() => {
    if (!task || task.task_type === "one_click_translate") return;
    const hiddenActionCounts = segments
      .filter((segment) => !displaySegmentIndexById.has(segment.id))
      .reduce<Record<string, number>>((counts, segment) => {
        counts[segment.action] = (counts[segment.action] ?? 0) + 1;
        return counts;
      }, {});
    const displayTimelineAnomalies = displaySegments.flatMap((segment, index) => {
      const previous = index > 0 ? displaySegments[index - 1] : null;
      const gap = previous ? segment.start - previous.end : 0;
      const duration = segment.end - segment.start;
      if (gap < 2 && duration < 8) return [];
      return [{
        index,
        id: segment.id,
        start: segment.start,
        end: segment.end,
        duration: Number(duration.toFixed(3)),
        previousId: previous?.id ?? null,
        gapFromPrevious: Number(gap.toFixed(3)),
        action: segment.action,
        sourceCount: segment.source_segment_ids?.length ?? 0,
        wordCount: segment.words?.length ?? 0,
      }];
    }).slice(0, 40);
    // #region agent log
    fetch('http://127.0.0.1:7296/ingest/805af596-423f-410e-860c-f2f1305f2af9',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'0d9522'},body:JSON.stringify({sessionId:'0d9522',runId:'pre-fix',hypothesisId:'H2,H4',location:'ReviewWorkbench.tsx:display-segments-effect',message:'Display filtering and timeline diagnostics',data:{taskId:task.id,showOnlyKept,totalSegments:segments.length,displaySegments:displaySegments.length,hiddenActionCounts,displayTimelineAnomalies},timestamp:Date.now()})}).catch(()=>{});
    // #endregion
  }, [displaySegmentIndexById, displaySegments, segments, showOnlyKept, task]);

  useEffect(() => {
    if (!task || task.task_type === "one_click_translate") return;
    const virtualItems = segmentVirtualizer.getVirtualItems();
    const scrollElement = segmentListRef.current;
    // #region agent log
    fetch('http://127.0.0.1:7296/ingest/805af596-423f-410e-860c-f2f1305f2af9',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'0d9522'},body:JSON.stringify({sessionId:'0d9522',runId:'pre-fix',hypothesisId:'H3',location:'ReviewWorkbench.tsx:virtual-items-effect',message:'Virtual subtitle rows after render',data:{taskId:task.id,totalDisplaySegments:displaySegments.length,virtualIndices:virtualItems.map((item)=>item.index),virtualSegmentIds:virtualItems.map((item)=>displaySegments[item.index]?.id ?? null),totalSize:segmentVirtualizer.getTotalSize(),scrollTop:scrollElement?.scrollTop ?? null,viewportHeight:scrollElement?.clientHeight ?? null,renderedDomIds:Array.from(scrollElement?.querySelectorAll<HTMLElement>('[data-segment-id]') ?? []).map((element)=>element.dataset.segmentId ?? null)},timestamp:Date.now()})}).catch(()=>{});
    // #endregion
  }, [displaySegments, segmentVirtualizer, showOnlyKept, task]);

  const appendConsoleEntry = useCallback((
    level: ConsoleEntry["level"],
    source: ConsoleEntry["source"],
    message: string,
    progress?: number,
    stage?: string,
  ) => {
    setConsoleEntries((prev) => {
      const next = [
        ...prev.slice(-199),
        {
          id: `${Date.now()}-${Math.random()}`,
          timestamp: new Date().toISOString(),
          level,
          source,
          message,
          progress,
          stage,
        },
      ];
      try {
        localStorage.setItem(consoleStorageKey, JSON.stringify(next));
      } catch {
        // localStorage 写入失败时静默忽略（如隐私模式）
      }
      return next;
    });
  }, [consoleStorageKey]);

  const addLog = useCallback((msg: LogMessage) => {
    if (msg.type === "log") {
      appendConsoleEntry(msg.level || "info", msg.source || "system", msg.message || "", msg.progress, msg.stage);
    }
    if (msg.type === "claude_failed") {
      void fetchTask(taskId).then((data) => {
        setTask(data);
        const run = getTaskExecutionRun(data);
        // retry_wait / 尚未耗尽的可重试错误是中间态，不弹最终失败框。
        if (
          isExecutionRetrying(run)
          || (
            run?.error?.retryable
            && run.retry_state.retries_used < run.retry_state.max_retries
            && !isExecutionFailureFinal(run)
          )
        ) {
          setClaudeFailedDialogOpen(false);
          return;
        }
        if (run && !isExecutionFailureFinal(run)) {
          setClaudeFailedDialogOpen(false);
          return;
        }
        setClaudeFailedError(run?.error?.message || msg.error || "未知错误");
        setClaudeFailedErrorType(run?.error?.error_type || msg.error_type || null);
        setClaudeFailedDialogOpen(true);
      }).catch(() => {
        // 兼容尚未返回 execution_runs 的旧后端。
        setClaudeFailedError(msg.error || "未知错误");
        setClaudeFailedErrorType(msg.error_type || null);
        setClaudeFailedDialogOpen(true);
      });
    }
    if (msg.type === "highlight_reels_ready") {
      appendConsoleEntry(
        msg.quality_passed ? "success" : "warn",
        "highlight",
        `产品集锦计划已生成：${msg.reels_count ?? 0} 个产品集锦${msg.quality_passed === false ? "，需人工复核" : ""}`,
        1,
        "reel_ready",
      );
      loadTask();
    }
    if (msg.type.startsWith("export_job_") && msg.job) {
      const job = msg.job as ExportJob;
      setTask(prev => prev ? mergeExportJob(prev, job) : prev);
      if (
        msg.type === "export_job_succeeded"
        && (job.render_profile === "draft" || job.render_profile === "preview")
        && job.output_url
      ) {
        if (!previewVideoUrlRef.current) previewRestoreTimeRef.current = playbackClock.getTime();
        setPreviewVideoUrl(job.output_url);
        setPreviewTitle(job.render_profile === "draft" ? "快速草稿" : "成片预览");
        setPreviewPlayRequestId((requestId) => requestId + 1);
        setCurrentTime(0);
        setPlaybackMode("all");
      }
      if (["export_job_succeeded", "export_job_failed", "export_job_cancelled"].includes(msg.type)) {
        loadTask();
      }
    }
    if (msg.type === "status_change" || msg.type === "export_done" || msg.type === "export_cancelled" || msg.type === "task_cancelled") {
      // 立即同步更新前端 task.status，不等 HTTP loadTask() 返回
      // 解决：WS 日志到达时 isProcessing 还是旧状态导致动画不显示的竞态问题
      if (msg.type === "status_change" && msg.status) {
        setTask(prev => prev ? { ...prev, status: msg.status as TaskStatus } : prev);
      }
      loadTask();
    }
  }, [appendConsoleEntry, playbackClock, setCurrentTime, taskId]);

  const loadTask = useCallback(async () => {
    try {
      const data = await fetchTask(taskId);
      const auditSegments = data.audit_segments || [];
      const asrSegments = data.asr_result?.segments || [];
      const auditTimelineAnomalies = auditSegments.flatMap((segment, index) => {
        const previous = index > 0 ? auditSegments[index - 1] : null;
        const duration = segment.end - segment.start;
        const gap = previous ? segment.start - previous.end : 0;
        const firstWord = segment.words?.[0];
        const lastWord = segment.words?.at(-1);
        const wordStartOffset = firstWord ? firstWord.start - segment.start : null;
        const wordEndOffset = lastWord ? segment.end - lastWord.end : null;
        const nonMonotonic = Boolean(previous && segment.start < previous.start);
        if (
          duration < 8
          && gap < 2
          && !nonMonotonic
          && (wordStartOffset === null || Math.abs(wordStartOffset) < 2)
          && (wordEndOffset === null || Math.abs(wordEndOffset) < 2)
        ) return [];
        const overlappingAsrCount = asrSegments.filter(
          (source) => source.end > segment.start && source.start < segment.end
        ).length;
        return [{
          index,
          id: segment.id,
          start: segment.start,
          end: segment.end,
          duration: Number(duration.toFixed(3)),
          previousId: previous?.id ?? null,
          gapFromPrevious: Number(gap.toFixed(3)),
          nonMonotonic,
          action: segment.action,
          merged: Boolean(segment.merged),
          sourceCount: segment.source_segment_ids?.length ?? 0,
          wordCount: segment.words?.length ?? 0,
          wordStartOffset: wordStartOffset === null ? null : Number(wordStartOffset.toFixed(3)),
          wordEndOffset: wordEndOffset === null ? null : Number(wordEndOffset.toFixed(3)),
          overlappingAsrCount,
        }];
      }).slice(0, 40);
      // #region agent log
      fetch('http://127.0.0.1:7296/ingest/805af596-423f-410e-860c-f2f1305f2af9',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'0d9522'},body:JSON.stringify({sessionId:'0d9522',runId:'pre-fix',hypothesisId:'H1,H4',location:'ReviewWorkbench.tsx:loadTask',message:'Fetched audit and ASR timeline diagnostics',data:{taskId:data.id,status:data.status,taskType:data.task_type,auditSegmentCount:auditSegments.length,asrSegmentCount:asrSegments.length,auditTimelineAnomalies},timestamp:Date.now()})}).catch(()=>{});
      // #endregion
      const validSegmentIds = new Set((data.audit_segments || []).map((segment) => segment.id));
      setTask(data);
      setSelectedIds((prev) => new Set(Array.from(prev).filter((id) => validSegmentIds.has(id))));
      setActiveSegmentId((prev) => (prev && validSegmentIds.has(prev) ? prev : null));
      setEditingSegmentId((prev) => (prev && validSegmentIds.has(prev) ? prev : null));
      setSubtitleNavigation((prev) => (prev && validSegmentIds.has(prev.segmentId) ? prev : null));
      setFocusedHighlight((prev) => {
        if (!prev) return prev;
        const nextSegmentIds = new Set(Array.from(prev.segmentIds).filter((id) => validSegmentIds.has(id)));
        if (nextSegmentIds.size === 0) return null;
        if (nextSegmentIds.size === prev.segmentIds.size) return prev;
        return { ...prev, segmentIds: nextSegmentIds };
      });
      setWordEditSegId((prev) => (prev && validSegmentIds.has(prev) ? prev : null));
      setSplitSegId((prev) => (prev && validSegmentIds.has(prev) ? prev : null));
      setAsrSelection((prev) => ({
        ...prev,
        silenceThreshold: data.params?.silence_threshold ?? prev.silenceThreshold,
        enableDiarization:
          data.params?.enable_diarization
          ?? defaultEnableDiarizationForTaskType(data.task_type),
      }));
      // 同步金句开场顺序（从后端最新数据中读取）
      setGoldenQuoteOrder((data.golden_quote_order || []).filter((id) => validSegmentIds.has(id)));
    } catch {
      toast.error("任务不存在");
      navigate("/");
    } finally {
      setLoading(false);
    }
  }, [navigate, taskId]);

  // 防抖学习提示：用户修改后 10s 空闲弹出浮动条
  const triggerFeedbackNudge = useCallback(() => {
    if (nudgeDismissed || promptFeedbackDialogOpen) return;
    if (nudgeTimerRef.current) clearTimeout(nudgeTimerRef.current);
    nudgeTimerRef.current = setTimeout(() => {
      setFeedbackNudgeShown(true);
    }, 10_000);
  }, [nudgeDismissed, promptFeedbackDialogOpen]);

  // 清理 nudge timer
  useEffect(() => () => {
    if (nudgeTimerRef.current) clearTimeout(nudgeTimerRef.current);
  }, []);

  useEffect(() => {
    loadTask();
    // Connect WebSocket
    const ws = createLogWebSocket(taskId, addLog);
    wsRef.current = ws;
    return () => ws.close();
  }, [taskId, loadTask, addLog]);

  // 运行中任务：定时向后端确认协程/进程仍活跃，避免日志静默时被误判为假死。
  // 进入详情页时无论当前持久化状态是什么都先查一次，防止 status 滞后导致运行态 UI 消失。
  useEffect(() => {
    if (!taskId || !task) {
      setHealthCheckFailed(false);
      return;
    }

    let cancelled = false;

    const pollHealth = async () => {
      try {
        const health = await fetchTaskRuntimeHealth(taskId);
        if (!cancelled) {
          setRuntimeHealth(health);
          setHealthCheckFailed(false);
          if (health.alive && PROCESSING_STATUSES.includes(health.effective_status)) {
            setTask(prev => {
              if (!prev || prev.status === health.effective_status) return prev;
              return { ...prev, status: health.effective_status };
            });
          }
        }
      } catch {
        if (!cancelled) {
          setHealthCheckFailed(true);
        }
      }
    };

    void pollHealth();
    const shouldKeepPolling = PROCESSING_STATUSES.includes(task.status) || runtimeHealth?.alive;
    const timer = shouldKeepPolling ? setInterval(pollHealth, 15000) : null;
    return () => {
      cancelled = true;
      if (timer) clearInterval(timer);
    };
  }, [taskId, task?.status, runtimeHealth?.alive]);

  const runtimeEffectiveStatus =
    runtimeHealth?.alive && PROCESSING_STATUSES.includes(runtimeHealth.effective_status)
      ? runtimeHealth.effective_status
      : null;
  const currentTaskStatus = runtimeEffectiveStatus ?? task?.status;
  const executionRun = getTaskExecutionRun(task);
  const activeExecutionRun = getTaskActiveExecutionRun(task);
  const activeExecutionRetrying = isExecutionRetrying(activeExecutionRun);
  const executionFailureFinal = isExecutionFailureFinal(executionRun);
  const activeExportJob = getActiveExportJob(task);
  const isExporting = Boolean(activeExportJob);
  const latestConsoleEntry = consoleEntries.length > 0 ? consoleEntries[consoleEntries.length - 1] : null;
  const runtimeHealthCheckedAtMs = runtimeHealth?.checked_at
    ? new Date(runtimeHealth.checked_at).getTime()
    : 0;
  const latestConsoleEntryAtMs = latestConsoleEntry
    ? new Date(latestConsoleEntry.timestamp).getTime()
    : 0;
  const runtimeHealthIsFreshForLastLog =
    Boolean(runtimeHealth) &&
    runtimeHealthCheckedAtMs >= latestConsoleEntryAtMs;
  const hasStaleProgressLogForTask =
    Boolean(runtimeHealth) &&
    runtimeHealthIsFreshForLastLog &&
    runtimeHealth?.alive === false &&
    isProgressLikeConsoleEntry(latestConsoleEntry);

  // 首次进入详情页：pending + 已上传视频 + 无 ASR 结果时，自动弹出 ASR 确认弹窗
  useEffect(() => {
    if (loading || !task) return;

    // 运行中或已有识别结果时，主动关闭弹窗，避免 stale UI 或竞态误弹
    if (currentTaskStatus === "asr_running" || task.asr_result) {
      setAsrDialogOpen(false);
      return;
    }

    if (!shouldPromptAsrOnEntry(task)) return;
    if (hasShownAsrPrompt(taskId)) return;

    markAsrPromptShown(taskId);
    setAsrSelection((prev) => ({
      ...prev,
      enableDiarization:
        task.params?.enable_diarization
        ?? defaultEnableDiarizationForTaskType(task.task_type),
    }));
    setAsrDialogOpen(true);
  }, [loading, task, taskId, currentTaskStatus]);

  useEffect(() => {
    if (!hasStaleProgressLogForTask) return;
    const staleKey = `${taskId}:${latestConsoleEntry?.id ?? ""}:${runtimeHealth?.checked_at ?? ""}`;
    if (latestConsoleEntry && staleProgressNoticeRef.current !== staleKey) {
      staleProgressNoticeRef.current = staleKey;
      appendConsoleEntry(
        "error",
        "system",
        `后台任务已停止，但最后进度停在「${latestConsoleEntry.message.slice(0, 80)}」。请查看上方状态，必要时重新发起审计。`,
      );
      setConsoleExpanded(true);
    }
  }, [
    appendConsoleEntry,
    hasStaleProgressLogForTask,
    latestConsoleEntry,
    runtimeHealth?.checked_at,
    taskId,
  ]);

  useEffect(() => {
    if (!activeExecutionRetrying) return;
    setClaudeFailedDialogOpen(false);
    const timer = setInterval(() => {
      void loadTask();
    }, 1500);
    return () => clearInterval(timer);
  }, [activeExecutionRetrying, loadTask]);

  const subtitleTimeIndex = useMemo(() => createSubtitleTimeIndex(playbackSegments), [playbackSegments]);
  const handlePlaybackTime = useCallback((time: number) => {
    // Only the active row subscribes to frame updates. Workbench state changes at sentence boundaries.
    if (previewVideoUrl) return;
    playbackClock.setTime(time);
    const nextId = subtitleTimeIndex.find(time, activeSegmentId);
    if (nextId !== activeSegmentId) setActiveSegmentId(nextId);
  }, [activeSegmentId, playbackClock, previewVideoUrl, subtitleTimeIndex]);
  useEffect(() => {
    if (previewVideoUrl) return;
    setActiveSegmentId((previous) => subtitleTimeIndex.find(playbackClock.getTime(), previous));
  }, [playbackClock, previewVideoUrl, subtitleTimeIndex]);

  // 用户主动导航完成后，暂时保留当前位置；播放进入下一句时再恢复自动跟随。
  useEffect(() => {
    setSubtitleNavigation((previous) => {
      if (
        !previous?.consumed
        || previous.playbackAnchorId === activeSegmentId
      ) {
        return previous;
      }
      return null;
    });
  }, [activeSegmentId]);

  // 统一协调字幕滚动：编辑句 > 用户主动导航 > 播放句。
  useEffect(() => {
    if (!task || task.task_type === "one_click_translate") return;
    const target = resolveSubtitleScrollTarget({
      editingSegmentId,
      navigation: subtitleNavigation,
      activeSegmentId,
    });
    if (!target) return;

    const displayIndex = displaySegmentIndexById.get(target.segmentId);
    if (displayIndex === undefined) {
      if (target.source === "navigation") {
        setSubtitleNavigation((previous) =>
          previous?.segmentId === target.segmentId ? null : previous
        );
      }
      return;
    }

    const scrollElement = segmentListRef.current;
    const rowElement = scrollElement
      ? Array.from(scrollElement.querySelectorAll<HTMLElement>("[data-segment-id]"))
        .find((element) => element.dataset.segmentId === target.segmentId)
      : null;
    const isVisible = Boolean(
      scrollElement
      && rowElement
      && isSubtitleRowVisible(
        scrollElement.getBoundingClientRect(),
        rowElement.getBoundingClientRect(),
      )
    );

    if (shouldScrollSubtitleTarget(target, isVisible)) {
      segmentVirtualizer.scrollToIndex(displayIndex, {
        align: target.source === "playback" ? "auto" : "center",
        behavior: target.source === "navigation" ? "smooth" : "auto",
      });
    }

    if (target.source === "navigation" && subtitleNavigation) {
      const completedRequest = subtitleNavigation;
      setSubtitleNavigation((previous) => {
        if (!previous || previous.requestId !== completedRequest.requestId) return previous;
        if (previous.playbackAnchorId !== activeSegmentId) return null;
        return { ...previous, consumed: true };
      });
    }
  }, [
    activeSegmentId,
    displaySegmentIndexById,
    editingSegmentId,
    segmentVirtualizer,
    subtitleNavigation,
    task?.task_type,
  ]);

  // 审计完成后若存在删除片段，默认切换为"播放保留字幕"
  useEffect(() => {
    if (!task) return;
    const postAuditStatuses = ["review", "done"];
    if (
      postAuditStatuses.includes(task.status) &&
      task.audit_segments?.some((s) => s.action === "delete")
    ) {
      setPlaybackMode("skip-hidden");
    }
  }, [task?.status]);

  const hasReviewableAuditSegments = Boolean(
    task?.audit_current_version ||
    (task?.audit_history?.length ?? 0) > 0 ||
    task?.audit_segments?.some((segment) =>
      segment.action !== "keep" || Boolean((segment as any).claude_action)
    )
  );
  // 审计结果已写入但状态被失败兜底回退到 asr_done 时，仍应允许用户复核现有结果。
  const reviewEffectiveStatus =
    currentTaskStatus === "asr_done" && hasReviewableAuditSegments
      ? "review"
      : currentTaskStatus;
  // 审计完成前，片段列表只读——用户只能在 AI 审计后再加工
  // done 状态也允许编辑，方便导出后继续调整再次导出
  const isReadOnly = reviewEffectiveStatus !== "review" && reviewEffectiveStatus !== "done";
  const isTaskProcessing = Boolean(
    (reviewEffectiveStatus && PROCESSING_STATUSES.includes(reviewEffectiveStatus))
    || activeExecutionRetrying
    || activeExecutionRun?.status === "running"
  );
  const isSubtitleReadOnly = !task?.audit_segments?.length || isTaskProcessing;
  const isDecisionReadOnly = isReadOnly;

  // 返回前检查：有未提交的修改时提示用户（必须在早返回之前声明，避免 Hooks 数量变化）
  const handleBack = useCallback(() => {
    const segs = task?.audit_segments || [];
    const count = segs.filter(
      (s) => (s as any).user_override && (s as any).claude_action && s.action !== (s as any).claude_action
    ).length;
    if (count > 0 && !nudgeDismissed) {
      toast(`你有 ${count} 处修改未提交给 AI 学习`, {
        action: { label: "现在提交", onClick: () => setPromptFeedbackDialogOpen(true) },
        cancel: { label: "直接离开", onClick: () => navigate("/") },
        duration: 8000,
      });
    } else {
      navigate("/");
    }
  }, [task?.audit_segments, nudgeDismissed, navigate]);

  // ── 多选状态 ──
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const lastClickedIdRef = useRef<string | null>(null);

  const clearSelection = useCallback(() => {
    setSelectedIds(new Set());
    lastClickedIdRef.current = null;
  }, []);

  const handleSegmentSeek = useCallback((seg: Segment, time: number) => {
    setActiveSegmentId(seg.id);
    setCurrentTime(time);
    if (seg.action === "delete") {
      setPlaybackMode("all");
    }
  }, [setCurrentTime]);

  const handleSegmentClick = (seg: Segment, e: React.MouseEvent) => {
    const segments = task?.audit_segments || [];
    // 审计完成前禁止多选操作
    const isMeta = !isReadOnly && (e.metaKey || e.ctrlKey);
    const isShift = !isReadOnly && e.shiftKey;

    if (quickSplitMode) {
      clearSelection();
      lastClickedIdRef.current = seg.id;
      handleSegmentSeek(seg, seg.start);
      return;
    }

    if (isMeta) {
      // Ctrl/Cmd + 点击：追加/取消选中
      setSelectedIds((prev) => {
        const next = new Set(prev);
        if (next.has(seg.id)) next.delete(seg.id);
        else next.add(seg.id);
        return next;
      });
      lastClickedIdRef.current = seg.id;
    } else if (isShift && lastClickedIdRef.current) {
      // Shift + 点击：范围选中
      const ids = segments.map((s) => s.id);
      const lastIdx = ids.indexOf(lastClickedIdRef.current);
      const curIdx = ids.indexOf(seg.id);
      if (lastIdx !== -1 && curIdx !== -1) {
        const [from, to] = lastIdx < curIdx ? [lastIdx, curIdx] : [curIdx, lastIdx];
        const rangeIds = ids.slice(from, to + 1);
        setSelectedIds((prev) => {
          const next = new Set(prev);
          rangeIds.forEach((id) => next.add(id));
          return next;
        });
      }
    } else {
      // 普通点击：清空选中，跳转视频
      setSelectedIds(new Set());
      lastClickedIdRef.current = seg.id;
    }

    handleSegmentSeek(seg, seg.start);
  };

  const handleHighlightSegmentMembership = async (
    segmentId: string,
    action: "add" | "remove",
  ) => {
    if (!task || !focusedHighlight?.reel || highlightSegmentPendingId) return;

    const qualityStage = focusedHighlight.qualityStage;
    const sourceReels = qualityStage === "before_quality"
      ? task.highlight_reels_before_quality
      : task.highlight_reels;
    if (!sourceReels) {
      toast.error("当前产品集锦已变化，请重新选择");
      return;
    }

    let updatedReels: HighlightReel[];
    try {
      updatedReels = updateHighlightSegmentMembership({
        reels: sourceReels,
        reelId: focusedHighlight.reel.reel_id,
        segmentId,
        action,
        segments: task.audit_segments,
        productOccurrences: task.product_occurrences ?? [],
        productGroups: task.product_groups ?? [],
      });
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "无法修改当前产品集锦");
      return;
    }

    const originalFocusedHighlight = focusedHighlight;
    const optimisticReel = updatedReels.find(
      (reel) => reel.reel_id === focusedHighlight.reel?.reel_id,
    );
    if (!optimisticReel) return;

    setHighlightSegmentPendingId(segmentId);
    setTask((previous) => {
      if (!previous) return previous;
      return qualityStage === "before_quality"
        ? { ...previous, highlight_reels_before_quality: updatedReels }
        : { ...previous, highlight_reels: updatedReels };
    });
    setFocusedHighlight((previous) => {
      if (!previous || previous.id !== optimisticReel.reel_id) return previous;
      return {
        ...previous,
        reel: optimisticReel,
        segmentIds: new Set(optimisticReel.segment_ids),
      };
    });

    try {
      const saved = await updateHighlightReels(
        task.id,
        updatedReels,
        task.revision ?? 0,
        qualityStage,
      );
      const savedReels = saved.reels;
      const savedReel = savedReels.find(
        (reel) => reel.reel_id === focusedHighlight.reel?.reel_id,
      );
      if (!savedReel) {
        throw new Error("保存后未找到当前产品集锦，请重新选择");
      }

      setTask((previous) => {
        if (!previous) return previous;
        return qualityStage === "before_quality"
          ? {
              ...previous,
              revision: saved.revision,
              highlight_reels_before_quality: savedReels,
            }
          : {
              ...previous,
              revision: saved.revision,
              highlight_reels: savedReels,
            };
      });
      setFocusedHighlight((previous) => {
        if (!previous || previous.id !== savedReel.reel_id) return previous;
        return {
          ...previous,
          title: savedReel.title,
          reel: savedReel,
          segmentIds: new Set(savedReel.segment_ids),
        };
      });
      toast.success(action === "add" ? "已加入当前集锦" : "已移出当前集锦");
      await loadTask();
    } catch (error) {
      setTask((previous) => {
        if (!previous) return previous;
        return qualityStage === "before_quality"
          ? { ...previous, highlight_reels_before_quality: sourceReels }
          : { ...previous, highlight_reels: sourceReels };
      });
      setFocusedHighlight((previous) => {
        if (!previous || previous.id !== originalFocusedHighlight.id) return previous;
        return originalFocusedHighlight;
      });
      toast.error(error instanceof Error ? error.message : "集锦句子保存失败");
    } finally {
      setHighlightSegmentPendingId(null);
    }
  };

  const handleToggleSegment = async (segId: string) => {
    if (!task) return;

    segmentHistory.push(task.audit_segments);

    // 乐观更新：立即切换本地状态，无需等待 API
    const prevTask = task;
    let toggledAction: "keep" | "delete" = "keep";
    const newSegments = task.audit_segments.map((seg) => {
      if (seg.id !== segId) return seg;
      const newAction = seg.action === "delete" ? "keep" : "delete";
      toggledAction = newAction;
      return {
        ...seg,
        action: newAction as typeof seg.action,
        user_override: true,
        manual_action: newAction === "keep" ? ("restore" as const) : ("delete" as const),
        reason: newAction === "keep" ? "手动恢复" : seg.reason,
      };
    });
    setTask({ ...task, audit_segments: newSegments });
    setActiveSegmentId(segId);
    // 弹出原因选择浮窗
    setPendingReasonSeg({ id: segId, action: toggledAction });

    // 存在 delete 段时自动切换为"播放保留字幕"，让用户立即感知删除效果
    if (newSegments.some((s) => s.action === "delete")) {
      setPlaybackMode("skip-hidden");
    }

    try {
      await toggleSegment(taskId, segId);
      // 后台同步真实统计数据（不阻塞 UI）
      loadTask();
      triggerFeedbackNudge();
    } catch {
      // API 失败时回滚乐观更新
      setTask(prevTask);
      toast.error("操作失败");
    }
  };

  // ── 批量操作 ──
  const handleBatchAction = async (action: "keep" | "delete" | "remove") => {
    if (!task || selectedIds.size === 0) return;
    segmentHistory.push(task.audit_segments);
    try {
      await batchSegmentAction(taskId, Array.from(selectedIds), action);
      clearSelection();
      await loadTask();
      const labels = { keep: "标记保留", delete: "标记删除", remove: "彻底删除" };
      toast.success(`已${labels[action]} ${selectedIds.size} 个片段`);
      triggerFeedbackNudge();
    } catch {
      toast.error("批量操作失败");
    }
  };

  // ── 合并下段 ──
  const handleMergeNext = async (segId: string) => {
    if (!task) return;
    segmentHistory.push(task.audit_segments);
    try {
      await mergeSegmentNext(taskId, segId);
      await loadTask();
      toast.success("已合并到下一段");
    } catch {
      toast.error("合并失败");
    }
  };

  // ── 合并上段 ──
  const handleMergePrevious = async (segId: string) => {
    if (!task) return;
    const segmentIndex = task.audit_segments.findIndex((s) => s.id === segId);
    if (segmentIndex <= 0) return;

    const previousSegment = task.audit_segments[segmentIndex - 1];
    segmentHistory.push(task.audit_segments);
    try {
      await mergeSegmentNext(taskId, previousSegment.id);
      await loadTask();
      toast.success("已合并到上一段");
    } catch {
      toast.error("合并失败");
    }
  };

  // ── 编辑保存 ──
  const handleEditSave = async (segId: string, newText: string, start: number, end: number) => {
    try {
      const seg = task!.audit_segments.find((s) => s.id === segId);
      if (!seg) return;
      const patch: Record<string, any> = {};
      if (newText !== getEffectiveSubtitleText(seg)) patch.text = newText;
      if (Math.abs(start - seg.start) > 0.001) patch.start = start;
      if (Math.abs(end - seg.end) > 0.001) patch.end = end;
      if (Object.keys(patch).length === 0) return;
      segmentHistory.push(task!.audit_segments);
      await patchSegment(taskId, segId, patch);
      await loadTask();
      toast.success("已保存");
    } catch {
      toast.error("编辑失败");
    }
  };

  const handleEditStart = useCallback((segId: string) => {
    setEditingSegmentId(segId);
  }, []);

  const handleEditEnd = useCallback((segId: string) => {
    setEditingSegmentId((current) => current === segId ? null : current);
  }, []);

  // ── 字级删除回调 ──
  const handleWordEditEnter = useCallback((segId: string) => {
    setSplitSegId(null); // 互斥：退出裁断模式
    setWordEditSegId(segId);
    setSelectedWordIndices(new Set());
    lastWordClickRef.current = -1;
  }, []);

  const handleWordEditExit = useCallback(() => {
    setWordEditSegId(null);
    setSelectedWordIndices(new Set());
    lastWordClickRef.current = -1;
  }, []);

  // ── 裁断模式回调 ──
  const handleSplitEnter = useCallback((segId: string) => {
    setWordEditSegId(null); // 互斥：退出字级删除模式
    setSelectedWordIndices(new Set());
    lastWordClickRef.current = -1;
    setSplitSegId(segId);
  }, []);

  const handleSplitExit = useCallback(() => {
    setSplitSegId(null);
  }, []);

  useEffect(() => {
    if (isReadOnly) {
      setQuickSplitMode(false);
      return;
    }

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== "Meta" || isEditableKeyboardTarget(e.target)) return;
      setWordEditSegId(null);
      setSelectedWordIndices(new Set());
      lastWordClickRef.current = -1;
      setQuickSplitMode(true);
    };
    const onKeyUp = (e: KeyboardEvent) => {
      if (e.key === "Meta" || !e.metaKey) {
        setQuickSplitMode(false);
      }
    };
    const resetQuickSplitMode = () => setQuickSplitMode(false);

    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("keyup", onKeyUp);
    window.addEventListener("blur", resetQuickSplitMode);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("keyup", onKeyUp);
      window.removeEventListener("blur", resetQuickSplitMode);
    };
  }, [isReadOnly]);

  const handleWordToggle = useCallback((segId: string, wordIndex: number, shiftKey: boolean) => {
    if (segId !== wordEditSegId) return;
    setSelectedWordIndices((prev) => {
      const next = new Set(prev);
      if (shiftKey && lastWordClickRef.current >= 0) {
        const from = Math.min(lastWordClickRef.current, wordIndex);
        const to = Math.max(lastWordClickRef.current, wordIndex);
        for (let i = from; i <= to; i++) next.add(i);
      } else {
        if (next.has(wordIndex)) next.delete(wordIndex);
        else next.add(wordIndex);
      }
      return next;
    });
    lastWordClickRef.current = wordIndex;
  }, [wordEditSegId]);

  const handleWordCommit = useCallback(async (segId: string) => {
    if (!task || selectedWordIndices.size === 0) return;
    const segIndex = task.audit_segments.findIndex((s) => s.id === segId);
    if (segIndex < 0) return;
    const seg = task.audit_segments[segIndex];

    segmentHistory.push(task.audit_segments);

    const newSubSegments = splitSegmentByWords(seg, selectedWordIndices);
    const newAllSegments = [
      ...task.audit_segments.slice(0, segIndex),
      ...newSubSegments,
      ...task.audit_segments.slice(segIndex + 1),
    ];

    setTask({ ...task, audit_segments: newAllSegments });
    setPlaybackMode("skip-hidden");
    handleWordEditExit();

    try {
      await updateSegments(taskId, newAllSegments);
      await loadTask();
      toast.success(`已切割：删除 ${selectedWordIndices.size} 字`);
    } catch {
      segmentHistory.undo(newAllSegments);
      await loadTask();
      toast.error("字级删除失败");
    }
  }, [task, selectedWordIndices, taskId, segmentHistory, handleWordEditExit]);

  // ── 裁断提交 ──
  const handleSplitCommit = useCallback(async (segId: string, splitAtIndex: number) => {
    if (!task) return;
    const segIndex = task.audit_segments.findIndex((s) => s.id === segId);
    if (segIndex < 0) return;
    const seg = task.audit_segments[segIndex];
    if (!seg.words || splitAtIndex < 1 || splitAtIndex >= seg.words.length) return;

    segmentHistory.push(task.audit_segments);

    const [s0, s1] = splitSegmentAt(seg, splitAtIndex);
    const newAllSegments = [
      ...task.audit_segments.slice(0, segIndex),
      s0, s1,
      ...task.audit_segments.slice(segIndex + 1),
    ];

    setTask({ ...task, audit_segments: newAllSegments });
    setSplitSegId(null);

    try {
      await updateSegments(taskId, newAllSegments);
      await loadTask();
      toast.success("已裁断为两段");
    } catch {
      segmentHistory.undo(newAllSegments);
      await loadTask();
      toast.error("裁断失败");
    }
  }, [task, taskId, segmentHistory]);

  // ── 裁剪区间快捷设置（从字幕段设置起点/终点）──
  const handleSetClipStart = useCallback(async (segStart: number) => {
    if (!task) return;
    try {
      await updateClipRange(taskId, segStart, task.clip_end ?? null);
      toast.success("已设置视频起点");
      loadTask();
    } catch {
      toast.error("设置视频起点失败");
    }
  }, [taskId, task?.clip_end, loadTask]);

  const handleSetClipEnd = useCallback(async (segEnd: number) => {
    if (!task) return;
    try {
      await updateClipRange(taskId, task.clip_start ?? null, segEnd);
      toast.success("已设置视频终点");
      loadTask();
    } catch {
      toast.error("设置视频终点失败");
    }
  }, [taskId, task?.clip_start, loadTask]);

  // ── 撤销/重做 ──
  const handleUndo = useCallback(async () => {
    if (!task || !segmentHistory.canUndo || isReadOnly) return;
    const prev = segmentHistory.undo(task.audit_segments);
    if (!prev) return;
    setTask({ ...task, audit_segments: prev });
    try {
      await updateSegments(taskId, prev);
      await loadTask();
    } catch {
      segmentHistory.pushBack(prev);
      toast.error("撤销同步失败");
    }
  }, [task, segmentHistory.canUndo, isReadOnly, taskId]);

  const handleRedo = useCallback(async () => {
    if (!task || !segmentHistory.canRedo || isReadOnly) return;
    const next = segmentHistory.redo(task.audit_segments);
    if (!next) return;
    setTask({ ...task, audit_segments: next });
    try {
      await updateSegments(taskId, next);
      await loadTask();
    } catch {
      segmentHistory.popFuture();
      toast.error("重做同步失败");
    }
  }, [task, segmentHistory.canRedo, isReadOnly, taskId]);

  // ── 快捷键 ──
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (isEditableKeyboardTarget(e.target)) return;

      if (e.key === "Escape") {
        clearSelection();
        return;
      }

      // Ctrl+Z / Cmd+Z 撤销；Ctrl+Shift+Z / Cmd+Shift+Z / Ctrl+Y 重做
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z" && !e.shiftKey) {
        e.preventDefault();
        handleUndo();
        return;
      }
      if ((e.ctrlKey || e.metaKey) && ((e.shiftKey && e.key.toLowerCase() === "z") || e.key === "y")) {
        e.preventDefault();
        handleRedo();
        return;
      }

      if (selectedIds.size === 0 || isReadOnly) return;

      if (e.key === "Delete" || e.key === "Backspace") {
        e.preventDefault();
        handleBatchAction("remove");
      } else if (e.key === "k" || e.key === "K") {
        e.preventDefault();
        handleBatchAction("keep");
      } else if (e.key === "d" || e.key === "D") {
        e.preventDefault();
        handleBatchAction("delete");
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [selectedIds, clearSelection, isReadOnly, handleUndo, handleRedo]);

  const buildHighlightAutoRunOptions = useCallback(() => {
    return buildHighlightAutoRunModelOptions(config, styleMode, highlightBuildOptions);
  }, [config, highlightBuildOptions, styleMode]);

  const normalizedHighlightBuildOptions = useCallback((): HighlightReelBuildOptions => {
    return normalizeHighlightBuildOptions(highlightBuildOptions);
  }, [highlightBuildOptions]);

  const handleASR = async (
    selection: AsrSelection = asrSelection,
    confirmation?: ModelRunConfirmation,
  ) => {
    try {
      await triggerASR(taskId, selection.model, selection.backend, confirmation);
      const label = selection.backend === "funasr" ? "FunASR Paraformer-zh" : `Whisper ${selection.model}`;
      appendConsoleEntry(
        "info",
        "asr",
        `ASR 识别已启动 | 引擎: ${label} | 等待后端日志...`,
        0.01,
      );
      toast.success(`ASR 识别已启动（${label}）`);
      setConsoleExpanded(true);
      setAsrDialogOpen(false);
      // 乐观更新：按钮立即进入转圈状态，不等 loadTask / WebSocket 返回
      setTask(prev => prev ? { ...prev, status: "asr_running" as TaskStatus } : prev);
      loadTask();
    } catch (e: any) {
      toast.error(e.message);
    }
  };

  const handleAnalyze = async (selection: AsrSelection = asrSelection) => {
    try {
      const hasDownstreamResults = Boolean(
        task?.audit_history?.length
        || task?.highlight_reels?.length
        || task?.export_path
        || task?.jianying_draft_path
      );
      if (["review", "done"].includes(currentTaskStatus ?? "") && hasDownstreamResults) {
        const ok = window.confirm("重新整理全文会清除旧的 AI 审计、产品集锦和导出结果，需要重新审计。确认继续？");
        if (!ok) return;
      }
      await triggerAnalyze(taskId, selection.silenceThreshold);
      appendConsoleEntry(
        "info",
        "asr",
        `文本整理已启动 | 停顿切分: ${selection.silenceThreshold}s | 等待后端日志...`,
        0.01,
      );
      toast.success("文本整理已启动");
      setConsoleExpanded(true);
      setAnalyzeDialogOpen(false);
      setTask(prev => prev ? { ...prev, status: "analyze_running" as TaskStatus } : prev);
      loadTask();
    } catch (e: any) {
      toast.error(e.message);
    }
  };

  const loadSubtitleOptimizationPreview = async () => {
    if (!task) return;
    setSubtitleOptimizeLoading(true);
    try {
      const preview = await previewSubtitleOptimization(taskId);
      setSubtitleOptimizePreview(preview);
      setSubtitleOptimizeSelectedIds(new Set(preview.changes.map((change) => change.segment_id)));
    } catch (e: any) {
      toast.error(e.message || "字幕优化预览失败");
    } finally {
      setSubtitleOptimizeLoading(false);
    }
  };

  const handleOpenSubtitleOptimize = async () => {
    if (!task?.audit_segments?.length) {
      toast.info("当前还没有可优化的字幕段");
      return;
    }
    setSubtitleOptimizeOpen(true);
    await loadSubtitleOptimizationPreview();
  };

  const handleApplySubtitleOptimize = async () => {
    if (!subtitleOptimizePreview) return;
    const selected_segment_ids = subtitleOptimizePreview.changes
      .filter((change) => subtitleOptimizeSelectedIds.has(change.segment_id))
      .map((change) => change.segment_id);
    if (selected_segment_ids.length === 0) return;
    setSubtitleOptimizeApplying(true);
    try {
      const result = await applySubtitleOptimization(taskId, {
        selected_segment_ids,
        expected_subtitle_revision: subtitleOptimizePreview.subtitle_revision,
        glossary_fingerprint: subtitleOptimizePreview.glossary_fingerprint,
      });
      appendConsoleEntry(
        "success",
        "subtitle",
        `字幕优化已应用 | 修正 ${result.changed_count} 处 | 审计与集锦已保留`,
      );
      toast.success(`已应用 ${result.changed_count} 处字幕优化`);
      setSubtitleOptimizeOpen(false);
      setSubtitleOptimizePreview(null);
      setSubtitleOptimizeSelectedIds(new Set());
      if (result.task) setTask(result.task);
      else await loadTask();
    } catch (e: any) {
      toast.error(e.message || "应用字幕优化失败");
      await loadSubtitleOptimizationPreview();
    } finally {
      setSubtitleOptimizeApplying(false);
    }
  };

  const handleRunFullAnalyzeFromSubtitleDialog = () => {
    setSubtitleOptimizeOpen(false);
    setAnalyzeDialogOpen(true);
  };

  const handleDiarization = async (confirmation?: ModelRunConfirmation) => {
    try {
      await triggerDiarization(taskId, confirmation);
      appendConsoleEntry("info", "diarization", "说话人分离已启动 | 等待后端日志...", 0.01);
      setConsoleExpanded(true);
      setTask(prev => prev ? { ...prev, status: "diarization_running" as TaskStatus } : prev);
      loadTask();
    } catch (e: any) {
      toast.error(e.message);
    }
  };

  const handleSwitchAsrVersion = (version: number) => {
    // 有审计数据时需二次确认，避免用户无意覆盖已审计结果
    const hasAuditData = task?.audit_segments && task.audit_segments.length > 0;
    if (hasAuditData) {
      // Radix 悬浮层关闭与 Dialog 打开不能落在同一个事件周期，
      // 否则 dismiss/focus 恢复可能把新 Dialog 一并关闭。
      window.setTimeout(() => setSwitchAsrConfirmVersion(version), 0);
    } else {
      void doSwitchAsrVersion(version);
    }
  };

  const doSwitchAsrVersion = async (version: number) => {
    if (asrSwitchingVersion !== null) return;
    setAsrSwitchingVersion(version);
    try {
      await switchASRVersion(taskId, version);
      toast.success(`已切换到 v${version} 识别结果`);
      setSwitchAsrConfirmVersion(null);
      await loadTask();
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setAsrSwitchingVersion(null);
    }
  };

  const handleAudit = async (confirmation?: ModelRunConfirmation) => {
    const auditOpts = buildFeatureRequestOptions(config, "audit");
    const isOllama = auditOpts.provider === "ollama";
    const hasRoleCredential = config.providerProfiles.some(
      (profile) => profile.enabled && (
        profile.provider === "ollama" || Boolean(profile.apiKey || profile.apiKeyEnv)
      ),
    );
    if (!isOllama && !auditOpts.openai_api_key && !hasRoleCredential) {
      setRuleEngineDialogOpen(true);
      return;
    }
    setRuntimeHealth(null);
    staleProgressNoticeRef.current = null;
    setTask(prev => prev ? { ...prev, status: "audit_running" as TaskStatus } : prev);
    try {
      await triggerAudit(taskId, {
        openai_api_key: auditOpts.openai_api_key,
        style_mode: styleMode,
        openai_model: auditOpts.openai_model,
        provider: auditOpts.provider === "ollama" ? "ollama" : "openai",
        ollama_model: auditOpts.ollama_model,
        ollama_base_url: auditOpts.ollama_base_url,
        preview_token: confirmation?.preview_token,
        preview_mode: confirmation?.preview_mode,
      });
      appendConsoleEntry(
        "info",
        "audit",
        isOllama
          ? `AI 审计已启动 | 调用: 本地 Ollama | 模型: ${auditOpts.ollama_model || "未指定"} | 等待后端日志...`
          : `AI 审计已启动 | 调用: 云端模型 | 模型: ${auditOpts.openai_model || "未指定"} | 等待后端日志...`,
        0.01,
      );
      toast.success(isOllama ? `本地 ${auditOpts.ollama_model} 审计已启动` : "语义审计已启动");
      setConsoleExpanded(true);
      loadTask();
    } catch (e: any) {
      // 回滚乐观更新
      loadTask();
      toast.error(e.message);
    }
  };

  const handleHighlightRebuild = async (confirmation?: ModelRunConfirmation) => {
    setHighlightRebuildPending(true);
    try {
      const options = normalizedHighlightBuildOptions();
      appendConsoleEntry(
        "info",
        "highlight",
        `产品集锦计划更新已启动 | ${formatHighlightBuildOptionsLog(options)} | 使用现有审计结果 | 等待后端日志...`,
        0.01,
        "reel_start",
      );
      setConsoleExpanded(true);
      await rebuildHighlightReels(taskId, { ...options, ...confirmation });
      toast.success("产品集锦计划更新已启动");
      await loadTask();
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setHighlightRebuildPending(false);
    }
  };

  const handleHighlightAutoRun = async (confirmation?: ModelRunConfirmation) => {
    const auditRuntime = resolveFeatureLlm(config, "audit");
    const highlightRuntime = resolveFeatureLlm(config, "highlight_reel");
    const runtimeKind = auditRuntime.provider === "ollama" ? "本地 Ollama" : "云端模型";
    const runtimeModel = auditRuntime.model || "未指定";
    const qualityModel = highlightRuntime.model || "未指定";
    setHighlightAutoRunPending(true);
    try {
      const autoRunBody = {
        ...buildHighlightAutoRunOptions(),
        ...normalizedHighlightBuildOptions(),
        auto_export: false,
        ...confirmation,
      };
      await autoRunHighlightReel(taskId, autoRunBody);
      appendConsoleEntry(
        "info",
        "audit",
        `直播卖货集锦完整生成已启动 | ${formatHighlightBuildOptionsLog(autoRunBody)} | 审计调用: ${runtimeKind} | 审计模型: ${runtimeModel} | 集锦验收: ${qualityModel} | 阶段: ASR/产品识别与审计/产品集锦自动优化 | 等待后端日志...`,
        0.01,
      );
      toast.success(`直播卖货集锦完整生成已启动（审计：${runtimeModel}）`);
      setConsoleExpanded(true);
      await loadTask();
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setHighlightAutoRunPending(false);
    }
  };

  /**
   * 丢弃现有语义审计产物，并基于当前 ASR/文本整理版本重新发起 AI 审计。
   */
  const handleResetAuditAndReaudit = async (confirmation?: ModelRunConfirmation) => {
    const currentVersion = task?.asr_current_version ?? task?.asr_history?.at(-1)?.version ?? null;
    const currentSnapshot = currentVersion
      ? task?.asr_history?.find(h => h.version === currentVersion)
      : null;
    const sourceSegments = currentSnapshot?.asr_result?.segments ?? task?.asr_result?.segments ?? task?.audit_segments ?? [];
    if (sourceSegments.length > 0) {
      const resetSegments: Segment[] = sourceSegments.map(seg => ({
        ...seg,
        action: "keep" as const,
        reason: undefined,
        rule: undefined,
        claude_action: undefined,
        claude_reason: undefined,
        user_override: false,
        manual_action: undefined,
        style: undefined,
        clip_group: null,
        clip_title: null,
        display_text: undefined,
        fix_type: undefined,
        split_before: undefined,
        split_part1_action: "keep" as const,
        split_part2_action: "keep" as const,
        subtitle_line_break: undefined,
        merged: false,
        semantic_unit_id: null,
        product_semantic: null,
      }));
      setTask(prev => prev ? {
        ...prev,
        audit_segments: resetSegments,
        highlight_reels: [],
        highlight_reels_before_quality: [],
        highlight_quality_report: null,
        highlight_quality_report_before_quality: null,
        segments_kept: resetSegments.length,
        segments_deleted: 0,
      } : prev);
    }

    const auditOpts = buildFeatureRequestOptions(config, "audit");
    const isOllama = auditOpts.provider === "ollama";
    const hasRoleCredential = config.providerProfiles.some(
      (profile) => profile.enabled && (
        profile.provider === "ollama" || Boolean(profile.apiKey || profile.apiKeyEnv)
      ),
    );
    if (!isOllama && !auditOpts.openai_api_key && !hasRoleCredential) {
      setRuleEngineDialogOpen(true);
      return;
    }
    try {
      setRuntimeHealth(null);
      staleProgressNoticeRef.current = null;
      setTask(prev => prev ? { ...prev, status: "audit_running" as TaskStatus } : prev);
      await triggerAudit(taskId, {
        openai_api_key: auditOpts.openai_api_key,
        style_mode: styleMode,
        openai_model: auditOpts.openai_model,
        provider: auditOpts.provider === "ollama" ? "ollama" : "openai",
        ollama_model: auditOpts.ollama_model,
        ollama_base_url: auditOpts.ollama_base_url,
        reset_prior_audit: true,
        preview_token: confirmation?.preview_token,
        preview_mode: confirmation?.preview_mode,
      });
      appendConsoleEntry(
        "info",
        "audit",
        isOllama
          ? `已丢弃旧语义审计结果，AI 审计已启动 | 保留当前 ASR v${currentVersion ?? "?"} | 调用: 本地 Ollama | 模型: ${auditOpts.ollama_model || "未指定"} | 等待后端日志...`
          : `已丢弃旧语义审计结果，AI 审计已启动 | 保留当前 ASR v${currentVersion ?? "?"} | 调用: 云端模型 | 模型: ${auditOpts.openai_model || "未指定"} | 等待后端日志...`,
        0.01,
      );
      toast.success("已丢弃旧语义审计结果，语义审计已重新启动");
      setConsoleExpanded(true);
    } catch (e: any) {
      toast.error(e.message);
      await loadTask();
    }
  };

  const openModelRunConfirmation = (pending: PendingModelRun) => {
    // HoverCard/DropdownMenu 关闭时会恢复焦点；延后一帧打开 Dialog，避免被一并 dismiss。
    window.setTimeout(() => setPendingModelRun(pending), 0);
  };

  const requestAsrRerun = (selection: AsrSelection = asrSelection) => {
    const frozenSelection = { ...selection };
    openModelRunConfirmation({
      request: {
        operation: "asr",
        mode: "rerun",
        options: {
          model: frozenSelection.model || "base",
          backend: frozenSelection.backend || "whisper",
        },
      },
      confirmLabel: "确认重新识别",
      execute: async (preview) => {
        appendConsoleEntry("warn", "system", "用户确认重新触发 ASR");
        await handleASR(frozenSelection, {
          preview_token: preview.preview_token,
          preview_mode: preview.mode,
        });
      },
    });
  };

  const handleManualAsrRetry = () => requestAsrRerun(asrSelection);

  const requestDiarizationRerun = () => {
    openModelRunConfirmation({
      request: {
        operation: "diarize",
        mode: "rerun",
        options: { enabled: true },
      },
      confirmLabel: "确认重新分离",
      execute: async (preview) => {
        await handleDiarization({
          preview_token: preview.preview_token,
          preview_mode: preview.mode,
        });
      },
    });
  };

  const auditExecutionOptions = (resetPriorAudit: boolean) => {
    const auditOpts = buildFeatureRequestOptions(config, "audit");
    return {
      openai_api_key: auditOpts.openai_api_key,
      style_mode: styleMode,
      openai_model: auditOpts.openai_model || DEFAULT_AUDIT_MODEL,
      force_rule_engine: false,
      provider: auditOpts.provider === "ollama" ? "ollama" : "openai",
      ollama_model: auditOpts.ollama_model || "deepseek-r1:32b",
      ollama_base_url: auditOpts.ollama_base_url || "http://localhost:11434",
      reset_prior_audit: resetPriorAudit,
    };
  };

  const requestAuditRerun = (mode: "keep" | "reset" = "keep") => {
    setReauditMode(mode);
    const reset = mode === "reset";
    openModelRunConfirmation({
      request: {
        operation: "audit",
        mode: reset ? "reset" : "rerun",
        options: auditExecutionOptions(reset),
      },
      confirmLabel: "确认重新审计",
      execute: async (preview) => {
        const confirmation = {
          preview_token: preview.preview_token,
          preview_mode: preview.mode,
        };
        if (reset) await handleResetAuditAndReaudit(confirmation);
        else await handleAudit(confirmation);
      },
    });
  };

  const requestHighlightRerun = (kind: "auto_run" | "rebuild") => {
    if (kind === "rebuild") {
      const options = normalizedHighlightBuildOptions();
      openModelRunConfirmation({
        request: {
          operation: "highlight_rebuild",
          mode: "rerun",
          options: { ...options },
        },
        confirmLabel: "确认更新产品集锦",
        execute: async (preview) => {
          await handleHighlightRebuild({
            preview_token: preview.preview_token,
            preview_mode: preview.mode,
          });
        },
      });
      return;
    }
    const options = {
      ...buildHighlightAutoRunOptions(),
      ...normalizedHighlightBuildOptions(),
      auto_export: false,
    };
    openModelRunConfirmation({
      request: {
        operation: "highlight_auto_run",
        mode: "rerun",
        options,
      },
      confirmLabel: "确认完整重新分析",
      execute: async (preview) => {
        await handleHighlightAutoRun({
          preview_token: preview.preview_token,
          preview_mode: preview.mode,
        });
      },
    });
  };

  const handleHighlightPrimaryAction = (actionKind: "auto_run" | "rebuild") => {
    const hasPriorHighlightRun = Boolean(
      task?.highlight_reels?.length
      || task?.highlight_reels_before_quality?.length
      || task?.product_occurrences?.length
      || task?.product_groups?.length
      || isReelExecutionFailure,
    );
    if (actionKind === "auto_run" && !hasPriorHighlightRun) {
      void handleHighlightAutoRun();
      return;
    }
    requestHighlightRerun(actionKind);
  };

  const handleResumeAudit = () => {
    setClaudeFailedDialogOpen(false);
    requestAuditRerun("keep");
  };

  // 用户在对话框中确认使用规则引擎
  const handleRuleEngineConfirm = async () => {
    setRuleEngineDialogOpen(false);
    // 乐观更新：立即显示"AI 审计中"指示器
    setTask(prev => prev ? { ...prev, status: "audit_running" as TaskStatus } : prev);
    try {
      await triggerAudit(taskId, {
        style_mode: styleMode,
        force_rule_engine: true,
      });
      appendConsoleEntry(
        "info",
        "rule_engine",
        "本地规则引擎审计已启动 | 等待后端日志...",
        0.01,
      );
      toast.success("本地规则引擎审计已启动");
      setConsoleExpanded(true);
      loadTask();
    } catch (e: any) {
      loadTask();
      toast.error(e.message);
    }
  };

  const handleOpenManualAudit = async () => {
    setManualAuditDialogOpen(true);
    setManualAuditInput("");
    setManualAuditTab("combined");
    setManualAuditLoading(true);
    try {
      const pack = await getManualAuditPrompt(taskId);
      setManualAuditPrompt(pack);
    } catch (e: any) {
      toast.error(e.message);
      setManualAuditDialogOpen(false);
    } finally {
      setManualAuditLoading(false);
    }
  };

  const handleManualAuditCopy = async (text: string) => {
    await navigator.clipboard.writeText(text);
    toast.success("已复制到剪贴板");
  };

  const handleCopyHighlightCursorPrompt = async () => {
    setHighlightCursorPromptPending(true);
    try {
      const prompt = await getManualHighlightPrompt(taskId);
      await navigator.clipboard.writeText(prompt.combined);
      toast.success("Cursor 集锦分析指令已复制");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "复制 Cursor 指令失败");
    } finally {
      setHighlightCursorPromptPending(false);
    }
  };

  const handleManualAuditDownload = (text: string, filename: string) => {
    const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
    toast.success(`已下载 ${filename}`);
  };

  const handleManualAuditDownloadAll = async () => {
    if (!manualAuditPrompt) return;
    const JSZip = (await import("jszip")).default;
    const zip = new JSZip();
    zip.file("audit_system.txt", manualAuditPrompt.system_prompt);
    zip.file("audit_user.txt", manualAuditPrompt.user_prompt);
    zip.file("audit_combined.txt", manualAuditPrompt.combined);
    const blob = await zip.generateAsync({ type: "blob" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "audit_prompts.zip";
    a.click();
    URL.revokeObjectURL(url);
    toast.success("已下载 audit_prompts.zip（含 3 个文件）");
  };

  const handleManualAuditSubmit = async () => {
    if (!manualAuditInput.trim()) { toast.error("请粘贴 AI 返回的 JSON"); return; }
    setManualAuditSubmitting(true);
    try {
      const result = await submitManualAuditResult(taskId, manualAuditInput.trim());
      toast.success(result.message || "审计完成");
      setManualAuditDialogOpen(false);
      setManualAuditInput("");
      loadTask();
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setManualAuditSubmitting(false);
    }
  };

  const handleExportFFmpeg = async (
    renderProfile: RenderProfile,
    burnSubtitles: boolean,
    subtitleStyle?: SubtitleStyle,
  ) => {
    try {
      const job = await exportFFmpeg(
        taskId,
        `${task?.name}_output`,
        burnSubtitles,
        subtitleStyle,
        renderProfile,
      );
      setTask(prev => prev ? mergeExportJob(prev, job) : prev);
      if (
        job.status === "succeeded"
        && (renderProfile === "draft" || renderProfile === "preview")
        && job.output_url
      ) {
        handlePlayHighlightClip(
          job.output_url,
          renderProfile === "draft" ? "快速草稿" : "成片预览",
        );
      }
      const profileLabel = {
        draft: "快速草稿",
        preview: "成片预览",
        final: "最终导出",
      }[renderProfile];
      appendConsoleEntry(
        "info",
        "ffmpeg",
        `${profileLabel}已启动 | 输出: ${task?.name || taskId}_output | 字幕: ${burnSubtitles ? "烧录" : "不烧录"} | 等待后端日志...`,
        0.01,
      );
      toast.success(job.status === "succeeded" ? `${profileLabel}已复用` : `${profileLabel}已启动`);
      setConsoleExpanded(true);
    } catch (e: any) {
      toast.error(e.message);
    }
  };

  const handleExportJianying = async () => {
    try {
      const job = await exportJianying(taskId, `${task?.name}_draft`, config.jianyingDir || undefined);
      setTask(prev => prev ? mergeExportJob(prev, job) : prev);
      appendConsoleEntry(
        "info",
        "jianying",
        `剪映草稿生成已启动 | 输出: ${task?.name || taskId}_draft | 等待后端日志...`,
        0.01,
      );
      toast.success("剪映草稿生成已启动");
      setConsoleExpanded(true);
    } catch (e: any) {
      toast.error(e.message);
    }
  };

  const handlePlayHighlightClip = async (
    url: string,
    context: HighlightClipActionSnapshot | string,
  ) => {
    if (!previewVideoUrl) {
      previewRestoreTimeRef.current = playbackClock.getTime();
    }
    setPreviewVideoUrl(url);
    if (typeof context === "string") {
      setPreviewTitle(context);
    } else {
      const strategyLabels = { faithful: "忠实版", balanced: "均衡版", concise: "精简版" } as const;
      const strategyLabel = context.reel?.strategy_kind
        ? strategyLabels[context.reel.strategy_kind]
        : "产品集锦";
      setPreviewTitle(`${strategyLabel} · ${context.reel?.title ?? context.clip.title}`);
    }
    setPreviewPlayRequestId((requestId) => requestId + 1);
    setCurrentTime(0);
    setPlaybackMode("all");
  };

  const handleExitPreview = useCallback(() => {
    const restoreTime = previewRestoreTimeRef.current ?? 0;
    setPreviewVideoUrl(null);
    setPreviewTitle(null);
    setCurrentTime(restoreTime);
    previewRestoreTimeRef.current = null;
  }, [setCurrentTime]);

  const handleExportHighlightClips = async (
    snapshot: HighlightClipActionSnapshot & { reel: HighlightReel },
  ) => {
    try {
      let burnSubtitles = true;
      let subtitleStyle: SubtitleStyle = { ...DEFAULT_SUBTITLE_STYLE };
      try {
        burnSubtitles = localStorage.getItem(BURN_SUBTITLES_LS_KEY) !== "false";
        const savedStyle = localStorage.getItem(SUBTITLE_STYLE_LS_KEY);
        if (savedStyle) {
          subtitleStyle = {
            ...DEFAULT_SUBTITLE_STYLE,
            ...(JSON.parse(savedStyle) as Partial<SubtitleStyle>),
          };
        }
      } catch {
        // localStorage 不可用时使用与导出面板一致的默认字幕设置
      }
      const job = await exportHighlightClips(taskId, {
        reelId: snapshot.reel.reel_id,
        qualityStage: snapshot.qualityStage,
        outputName: task?.name,
        burnSubtitles,
        subtitleStyle: burnSubtitles ? subtitleStyle : undefined,
      });
      setTask(prev => prev ? mergeExportJob(prev, job) : prev);
      appendConsoleEntry(
        "info",
        "ffmpeg",
        `产品集锦导出已启动 | 质检阶段: ${snapshot.qualityStage === "before_quality" ? "质检前" : "质检后"} | 策略: ${snapshot.reel.strategy_kind || "旧版"} | 输出前缀: ${task?.name || taskId} | 等待后端日志...`,
        0.01,
      );
      toast.success("产品集锦导出已启动");
      setConsoleExpanded(true);
    } catch (e: any) {
      toast.error(e.message);
    }
  };

  const handleCancelExport = async () => {
    try {
      if (!activeExportJob) {
        toast.error("当前没有正在运行的导出");
        return;
      }
      await cancelExport(activeExportJob.id);
      toast.success("导出已终止");
      setTask(prev => prev ? mergeExportJob(prev, {
        ...activeExportJob,
        status: "cancelled",
        completed_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      }) : prev);
    } catch (e: any) {
      toast.error(e.message);
    }
  };

  const handleRevealExport = async () => {
    try {
      await revealExport(taskId);
      toast.success("已在 Finder 中显示导出文件");
    } catch (e: any) {
      toast.error(e.message);
    }
  };

  const handleCancelTask = async (rollbackStatus?: TaskStatus) => {
    if (taskCancelPending) return;
    setTaskCancelPending(true);
    try {
      await cancelTask(taskId);
      toast.success("任务已终止");
      setHighlightRebuildPending(false);
      setHighlightAutoRunPending(false);
      // 乐观更新：立即回滚到安全状态（与后端 _CANCEL_ROLLBACK_MAP 一致），
      // WebSocket 的 task_cancelled/status_change 消息会做最终同步。
      const rollback: Partial<Record<TaskStatus, TaskStatus>> = {
        diarization_running: "asr_recognized",
        analyze_running: "asr_recognized",
        audit_running: "asr_done",
        translate_running: "pending",
        tts_running: "pending",
        dubbing_running: "pending",
        export_running: "review",
      };
      setTask(prev => {
        if (!prev) return prev;
        if (rollbackStatus) {
          return { ...prev, status: rollbackStatus };
        }
        if (prev.status === "asr_running") {
          const hasPreviousAsr = Boolean(prev.asr_history?.length || prev.asr_result?.words?.length);
          const hasDiarization = Boolean(
            prev.asr_result?.diar_segments?.length || prev.asr_result?.speakers?.length
          );
          return {
            ...prev,
            status: hasPreviousAsr
              ? (hasDiarization ? "asr_diarized" : "asr_recognized")
              : "pending",
          };
        }
        const next = rollback[prev.status];
        return next ? { ...prev, status: next } : prev;
      });
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setTaskCancelPending(false);
    }
  };

  if (loading) {
    return (
      <div className="h-full flex items-center justify-center">
        <Loader2 className="w-6 h-6 animate-spin text-primary" />
      </div>
    );
  }

  if (!task) return null;

  if (task.task_type === "one_click_translate") {
    return (
      <OneClickTranslateWorkbench
        task={task}
        taskId={taskId}
        onBack={handleBack}
        onRefresh={loadTask}
        appConfig={config}
        consoleEntries={consoleEntries}
        consoleExpanded={consoleExpanded}
        onConsoleToggle={() => setConsoleExpanded(!consoleExpanded)}
        onConsoleClear={() => {
          setConsoleEntries([]);
          try { localStorage.removeItem(consoleStorageKey); } catch { /* ignore */ }
        }}
        onLog={(level, source, message, progress) => {
          appendConsoleEntry(level, source, message, progress);
          setConsoleExpanded(true);
        }}
      />
    );
  }

  const displayStatus = reviewEffectiveStatus ?? task.status;
  const isProcessing = PROCESSING_STATUSES.includes(displayStatus)
    || activeExecutionRetrying
    || activeExecutionRun?.status === "running";
  const isAsrRunning = displayStatus === "asr_running";
  const isDiarizationRunning = displayStatus === "diarization_running";
  const isAnalyzeRunning = displayStatus === "analyze_running";
  const highlightAutoRunOptions = buildHighlightAutoRunOptions();
  const auditModelShortLabel = getModelShortLabel(config, "audit");
  const highlightModelShortLabel = getModelShortLabel(config, "highlight_reel");
  const hasAsrHistory = Boolean(task.asr_history && task.asr_history.length > 0);
  const hasRecognizedWords = Boolean(task.asr_result?.words?.length);
  const canRunOptionalDiarization =
    hasRecognizedWords &&
    !isProcessing &&
    ["asr_recognized", "asr_diarized", "asr_done", "review", "done"].includes(displayStatus);
  const recognizedChunks = segments.length === 0
    ? buildRecognitionPreviewChunks(task.asr_result?.words ?? [])
    : [];
  const recognizedText = recognizedChunks.map((chunk) => chunk.text).join("");
  const analysisReport = task.asr_result?.analysis_report ?? null;
  const lastLogTimestamp = consoleEntries.length > 0
    ? consoleEntries[consoleEntries.length - 1].timestamp
    : null;
  const executionOperation = executionRun?.operation || "";
  const executionStage = executionRun?.current_stage || latestConsoleEntry?.stage || "";
  const currentExecutionOperation = activeExecutionRun?.operation
    || (highlightRebuildPending
      ? "highlight_rebuild"
      : highlightAutoRunPending
        ? "highlight_auto_run"
        : displayStatus === "audit_running"
          ? "audit"
          : "");
  const currentExecutionStage = activeExecutionRun?.current_stage
    || (currentExecutionOperation === "highlight_rebuild"
      ? "reel_plan"
      : currentExecutionOperation === "audit"
        ? "semantic_audit"
        : latestConsoleEntry?.stage || "");
  const currentExecutionActivity = classifyReviewExecutionActivity(
    currentExecutionOperation,
    currentExecutionStage,
  );
  const isAuditRunning =
    displayStatus === "audit_running" && currentExecutionActivity === "audit";
  const isAsrExecutionFailure = executionFailureFinal && (
    executionOperation.includes("asr")
    || executionStage.includes("asr")
  );
  const isAnalyzeExecutionFailure = executionFailureFinal && (
    executionOperation.includes("analyze")
    || executionStage.includes("analyze")
  );
  const isDiarizationExecutionFailure = executionFailureFinal && (
    executionOperation.includes("diarization")
    || executionStage.includes("diarization")
  );
  const isAuditExecutionFailure = executionFailureFinal && (
    executionOperation.includes("audit")
    || ["audit", "semantic_audit", "coherence"].some(
      (stage) => executionStage.includes(stage),
    )
  );
  const isReelExecutionFailure = executionFailureFinal && (
    executionOperation.includes("highlight")
    || executionStage.includes("reel")
  );
  const canShowManualAsrRetry = Boolean(isAsrExecutionFailure && task.video_path && !isProcessing);
  const canShowAuditRetry = Boolean(
    hasStaleProgressLogForTask &&
    !isProcessing &&
    ["audit", "openai", "ollama", "rule_engine"].includes(latestConsoleEntry?.source || "")
  );
  const runtimeHealthDisplay = isProcessing || hasStaleProgressLogForTask || executionFailureFinal
    ? buildRuntimeHealthDisplay(runtimeHealth, healthCheckFailed, lastLogTimestamp, hasStaleProgressLogForTask || executionFailureFinal)
    : null;
  const isHighlightTask = task.task_type === "highlight_reel";
  const isHighlightExecution = isHighlightTask
    && displayStatus === "audit_running"
    && currentExecutionActivity === "highlight";
  const qualityReport = task.highlight_quality_report;
  const highlightReelsReady = Boolean(task.highlight_reels?.length);
  const highlightQualityReady = Boolean(qualityReport);
  const normalizedHighlightOptions = normalizedHighlightBuildOptions();
  const highlightPrimaryAction = getHighlightReelPrimaryAction(task, {
    isProcessing,
    rebuildPending: highlightRebuildPending,
    autoRunPending: highlightAutoRunPending,
  });
  const highlightPrimaryPending = highlightPrimaryAction.kind === "rebuild" ? highlightRebuildPending : highlightAutoRunPending;
  const canCopyHighlightCursorPrompt = Boolean(
    task.audit_segments?.length
    && ((task.product_groups?.length ?? 0) > 0 || (task.product_occurrences?.length ?? 0) > 0),
  );
  const highlightOptionsSummary = formatHighlightBuildOptionsSummary(normalizedHighlightOptions);
  const handleContinueExecution = () => {
    if (isReelExecutionFailure) {
      requestHighlightRerun("auto_run");
      return;
    }
    if (isAuditExecutionFailure) {
      handleResumeAudit();
      return;
    }
    if (isAsrExecutionFailure) {
      void handleManualAsrRetry();
      return;
    }
    if (isAnalyzeExecutionFailure) {
      void handleAnalyze(asrSelection);
      return;
    }
    if (isDiarizationExecutionFailure) {
      requestDiarizationRerun();
      return;
    }
  };
  const handleFullRestartExecution = () => {
    if (isReelExecutionFailure) {
      requestHighlightRerun("auto_run");
      return;
    }
    if (isAuditExecutionFailure) {
      requestAuditRerun("reset");
      return;
    }
    if (isAsrExecutionFailure) {
      appendConsoleEntry("warn", "system", "完全重新执行 ASR：后端暂无独立 reset 接口，将通过现有 ASR trigger 发起新执行");
      requestAsrRerun(asrSelection);
      return;
    }
    if (isAnalyzeExecutionFailure) {
      void handleAnalyze(asrSelection);
      return;
    }
    if (isDiarizationExecutionFailure) {
      requestDiarizationRerun();
      return;
    }
  };
  const hasRecoveryTrigger = isAuditExecutionFailure
    || isAsrExecutionFailure
    || isAnalyzeExecutionFailure
    || isDiarizationExecutionFailure
    || isReelExecutionFailure;

  const getCurrentAsrSnapshot = () => {
    if (!task.asr_history?.length) return null;
    return task.asr_history.find((snap) => snap.version === task.asr_current_version) ?? task.asr_history[task.asr_history.length - 1];
  };

  const formatStepTime = (value?: string | null) => value ? formatAsrTime(value) : null;

  const renderAsrActivityChip = () => {
    const currentAsr = getCurrentAsrSnapshot();
    const displayAsrModel = currentAsr?.engine ?? (asrSelection.backend === "funasr" ? "FunASR" : `Whisper ${asrSelection.model}`);
    const records: StepActivityRecord[] = [...(task.asr_history ?? [])].reverse().map((snap: ASRSnapshot) => {
      const isCurrent = snap.version === task.asr_current_version;
      return {
        id: snap.version,
        version: snap.version,
        label: snap.engine,
        meta: `${snap.segments_count} 个片段`,
        time: formatStepTime(snap.created_at),
        current: isCurrent,
        disabled: isAsrRunning || isProcessing || asrSwitchingVersion !== null || isCurrent,
        actionLabel: asrSwitchingVersion === snap.version ? "切换中" : "切换",
        onSelect: isCurrent || isAsrRunning || isProcessing || asrSwitchingVersion !== null
          ? undefined
          : () => handleSwitchAsrVersion(snap.version),
      };
    });

    const status = isAsrRunning
      ? "识别中"
      : isAnalyzeRunning
        ? "整理中"
        : canShowManualAsrRetry
          ? "需重试"
          : hasAsrHistory || hasRecognizedWords
            ? "已完成"
            : "未执行";

    const actionLabel = canShowManualAsrRetry
      ? "手动重试 ASR"
      : hasAsrHistory || hasRecognizedWords
        ? "重新识别"
        : "运行 ASR";

    return (
      <StepActivityChip
        icon={isAsrRunning || isAnalyzeRunning ? <Loader2 className="h-3.5 w-3.5 animate-spin text-blue-300" /> : <Mic className="h-3.5 w-3.5 text-blue-400" />}
        name="ASR"
        model={displayAsrModel}
        status={status}
        time={formatStepTime(currentAsr?.created_at)}
        tone={isAsrRunning || isAnalyzeRunning ? "running" : canShowManualAsrRetry ? "warning" : hasAsrHistory || hasRecognizedWords ? "done" : "muted"}
        records={records}
        hoverTitle="字幕识别历史"
        emptyLabel="尚未生成 ASR 历史"
        action={isAsrRunning || isAnalyzeRunning ? null : {
          label: actionLabel,
          onClick: canShowManualAsrRetry ? handleManualAsrRetry : () => setAsrDialogOpen(true),
          disabled: isProcessing || !task.video_path,
        }}
      />
    );
  };

  const renderAuditActivityChip = () => {
    const auditHistory = task.audit_history ?? [];
    const latestAudit = auditHistory.length > 0 ? auditHistory[auditHistory.length - 1] : null;
    const auditDone = auditHistory.length > 0
      || Boolean(task.audit_current_version)
      || ["review", "done"].includes(displayStatus);
    const records: StepActivityRecord[] = [...auditHistory].reverse().map((snap: AuditSnapshot) => ({
      id: snap.version,
      version: snap.version,
      label: snap.model,
      meta: `${snap.segments_kept} 保留 · ${snap.segments_deleted} 删除`,
      time: formatStepTime(snap.created_at),
      current: snap.version === task.audit_current_version,
    }));

    const runAudit = () => {
      if (auditDone || auditHistory.length > 0 || isAuditExecutionFailure) {
        requestAuditRerun("keep");
      } else {
        void handleAudit();
      }
    };

    return (
      <StepActivityChip
        icon={isAuditRunning ? <Loader2 className="h-3.5 w-3.5 animate-spin text-amber-300" /> : <Zap className="h-3.5 w-3.5 text-amber-400" />}
        name="语义审计"
        model={latestAudit?.model ?? auditModelShortLabel}
        status={isAuditRunning ? "审计中" : auditDone ? "已完成" : "未执行"}
        time={formatStepTime(latestAudit?.created_at ?? (auditDone ? task.updated_at : null))}
        tone={isAuditRunning ? "running" : auditDone ? "done" : "muted"}
        records={records}
        hoverTitle="语义审计历史"
        emptyLabel="暂无审计历史"
        action={isAuditRunning ? null : {
          label: auditDone ? "重新审计" : "AI 审计",
          onClick: runAudit,
          disabled: isProcessing,
        }}
      />
    );
  };

  const getDiarizationCompletedAt = () => {
    const report = task.asr_result?.analysis_report as Record<string, any> | undefined;
    return report?.diarization?.completed_at ?? null;
  };

  const getAnalyzeCompletedAt = () => {
    const report = task.asr_result?.analysis_report as Record<string, any> | undefined;
    return report?.generated_at ?? null;
  };

  const renderOptionalDiarizationStep = () => {
    const completedAt = getDiarizationCompletedAt();
    const hasDiarizationCompleted = Boolean(completedAt);
    const shouldShow = hasRecognizedWords || isDiarizationRunning || hasDiarizationCompleted;
    if (!shouldShow) return null;

    const statusLabel = isDiarizationRunning
      ? "分离中"
      : hasDiarizationCompleted
        ? "已完成"
        : "未执行";

    const records: StepActivityRecord[] = hasDiarizationCompleted ? [{
      id: "diarization-current",
      version: 1,
      current: true,
      label: "CAM++",
      meta: `${task.asr_result?.speakers?.length ?? 0} 位说话人 · ${task.asr_result?.diar_segments?.length ?? 0} 段`,
      time: formatStepTime(completedAt),
    }] : [];

    return (
      <>
        <StepActivityChip
          icon={isDiarizationRunning ? <Loader2 className="h-3.5 w-3.5 animate-spin text-violet-300" /> : <Users className="h-3.5 w-3.5 text-violet-300" />}
          name="说话人分离"
          model="CAM++"
          status={statusLabel}
          time={formatStepTime(completedAt)}
          tone={isDiarizationRunning ? "running" : hasDiarizationCompleted ? "done" : "muted"}
          records={records}
          hoverTitle="说话人分离历史"
          emptyLabel="未执行，可按需开启"
          action={isDiarizationRunning ? null : {
            label: hasDiarizationCompleted ? "重新分离" : "说话人分离",
            onClick: hasDiarizationCompleted || isDiarizationExecutionFailure
              ? requestDiarizationRerun
              : () => void handleDiarization(),
            disabled: isProcessing || (!canRunOptionalDiarization && !hasDiarizationCompleted),
          }}
        />
        {isDiarizationRunning && (
          <Button
            size="sm"
            className="bg-destructive text-destructive-foreground hover:bg-destructive/90 text-xs h-7"
            onClick={() => void handleCancelTask()}
            disabled={taskCancelPending}
          >
            {taskCancelPending ? "终止中" : "终止"}
          </Button>
        )}
      </>
    );
  };

  const renderAnalyzeActivityChip = () => {
    const completedAt = getAnalyzeCompletedAt();
    const hasAnalyzeCompleted = Boolean(completedAt || task.asr_result?.segments?.length);
    const threshold = Number(task.params?.silence_threshold ?? (analysisReport?.settings ?? {}).silence_threshold ?? asrSelection.silenceThreshold).toFixed(2);
    const records: StepActivityRecord[] = hasAnalyzeCompleted ? [{
      id: "analyze-current",
      version: 1,
      current: true,
      label: `分段阈值 ${threshold}s`,
      meta: `${analysisReport?.stats?.segments_final ?? task.asr_result?.segments?.length ?? 0} 个片段 · ${analysisReport?.stats?.candidate_edits ?? 0} 个候选`,
      time: formatStepTime(completedAt),
    }] : [];

    return (
      <>
        <StepActivityChip
          icon={isAnalyzeRunning ? <Loader2 className="h-3.5 w-3.5 animate-spin text-cyan-300" /> : <Cpu className="h-3.5 w-3.5 text-cyan-300" />}
          name="分析优化"
          model="规则整理"
          status={isAnalyzeRunning ? "分析中" : hasAnalyzeCompleted ? `字幕 v${task.subtitle_revision ?? 0}` : "未执行"}
          time={formatStepTime(completedAt)}
          tone={isAnalyzeRunning ? "running" : hasAnalyzeCompleted ? "done" : "muted"}
          records={records}
          hoverTitle="分析优化历史"
          emptyLabel="暂无分析记录"
          action={isAnalyzeRunning || hasAnalyzeCompleted ? null : {
            label: "分析优化",
            onClick: () => setAnalyzeDialogOpen(true),
            disabled: isProcessing || !hasRecognizedWords,
          }}
        />
        {isAnalyzeRunning && task.video_path && (
          <Button
            size="sm"
            className="h-6 bg-destructive px-2 text-[10px] text-destructive-foreground hover:bg-destructive/90"
            onClick={() => void handleCancelTask()}
            disabled={taskCancelPending}
          >
            {taskCancelPending ? "终止中" : "终止"}
          </Button>
        )}
      </>
    );
  };

  const renderHighlightPlanActivityChip = () => {
    const visibleReels = (task.highlight_reels ?? []).filter((reel) => !reel.hidden);
    const isHighlightBuilding = isHighlightExecution || highlightPrimaryPending;
    const canCancelHighlight = isHighlightExecution;
    const scoreLabel = qualityReport?.score != null ? `评分 ${(qualityReport.score * 100).toFixed(0)}` : null;
    const qualityModelLabel = qualityReport?.validator_model ?? highlightModelShortLabel;
    const qualityMeta = [
      qualityModelLabel,
      highlightOptionsSummary,
      scoreLabel,
      qualityReport?.repair_count ? `自动修订 ${qualityReport.repair_count} 项` : null,
      qualityReport?.issues.length ? `${qualityReport.issues.length} 项需复核` : null,
      qualityReport?.degraded ? "仅代码校验" : null,
    ].filter(Boolean).join(" · ");
    const latestReelTime = visibleReels.reduce<string | null>((latest, reel) => {
      if (!reel.created_at) return latest;
      if (!latest || reel.created_at > latest) return reel.created_at;
      return latest;
    }, qualityReport?.validated_at ?? null);
    const records: StepActivityRecord[] = visibleReels.length > 0 ? [{
      id: "highlight-plan-current",
      version: 1,
      current: true,
      label: qualityReport ? (qualityReport.passed ? "已生成并优化" : "已生成，需人工复核") : `${visibleReels.length} 个产品集锦`,
      meta: qualityMeta,
      time: formatStepTime(latestReelTime),
    }] : [];

    return (
      <>
        <StepActivityChip
          icon={isHighlightBuilding ? <Loader2 className="h-3.5 w-3.5 animate-spin text-orange-300" /> : <Sparkles className="h-3.5 w-3.5 text-orange-300" />}
          name="产品集锦"
          model={qualityModelLabel}
          status={isHighlightBuilding ? "生成中" : visibleReels.length > 0 ? (qualityReport?.passed === false ? `${visibleReels.length} 个 · 需复核` : `${visibleReels.length} 个 · 已优化`) : "未生成"}
          time={formatStepTime(latestReelTime)}
          tone={isHighlightBuilding ? "running" : qualityReport?.passed === false ? "warning" : visibleReels.length > 0 ? "done" : "muted"}
          records={records}
          hoverTitle="产品集锦历史"
          emptyLabel="暂无产品集锦记录"
          action={isHighlightBuilding ? null : {
            label: highlightPrimaryPending ? highlightPrimaryAction.pendingLabel : highlightPrimaryAction.label,
            onClick: () => handleHighlightPrimaryAction(highlightPrimaryAction.kind),
            disabled: highlightPrimaryAction.disabled,
            pending: highlightPrimaryPending,
            title: highlightPrimaryAction.title,
          }}
        />
        {canCancelHighlight && (
          <Button
            size="sm"
            className="h-6 bg-destructive px-2 text-[10px] text-destructive-foreground hover:bg-destructive/90"
            onClick={() => void handleCancelTask("review")}
            disabled={taskCancelPending}
            title="终止产品集锦生成"
            aria-label="终止产品集锦生成"
          >
            {taskCancelPending ? "终止中" : "终止"}
          </Button>
        )}
        <Button
          type="button"
          size="sm"
          variant="outline"
          className="h-6 gap-1 px-2 text-[10px]"
          onClick={() => void handleCopyHighlightCursorPrompt()}
          disabled={!canCopyHighlightCursorPrompt || highlightCursorPromptPending}
          title={
            canCopyHighlightCursorPrompt
              ? "复制完整提示词到 Cursor 对话窗"
              : "请先完成产品识别与语义审计"
          }
        >
          {highlightCursorPromptPending ? (
            <Loader2 className="h-3 w-3 animate-spin" />
          ) : (
            <Copy className="h-3 w-3" />
          )}
          复制 Cursor 指令
        </Button>
      </>
    );
  };

  const handleAnalysisNoteJump = (segmentId: string, start: number) => {
    setCurrentTime(start);
    setActiveSegmentId(segmentId);
    setAnalysisReportOpen(false);
    requestSubtitleNavigation(segmentId, "analysis");
  };

  const renderStepAction = (step: ReviewStepDef) => {
    if (step.key === "asr") {
      return (
        <div className="flex w-max items-center gap-1.5">
          {renderAsrActivityChip()}
          {(hasRecognizedWords || isAnalyzeRunning) && renderAnalyzeActivityChip()}
          {isAsrRunning && task.video_path && (
            <Button
              size="sm"
              className="h-6 bg-destructive px-2 text-[10px] text-destructive-foreground hover:bg-destructive/90"
              onClick={() => void handleCancelTask()}
              disabled={taskCancelPending}
              title="终止识别"
              aria-label="终止识别"
            >
              {taskCancelPending ? "终止中" : "终止"}
            </Button>
          )}
          {renderOptionalDiarizationStep()}
        </div>
      );
    }

    if (step.key === "audit" || step.key === "semantic_audit") {
      if (displayStatus !== "audit_running" && !["asr_done", "review", "done"].includes(displayStatus)) return null;
      return (
        <div className="flex w-max items-center gap-1.5">
          {renderAuditActivityChip()}
          {!isAuditRunning && (
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="h-6 gap-1 px-2 text-[10px]"
              onClick={() => void handleOpenManualAudit()}
              disabled={!task.audit_segments?.length}
              title="复制完整提示词到 Cursor 对话窗，并回填审计结果"
            >
              <Copy className="h-3 w-3" />
              复制 Cursor 指令
            </Button>
          )}
          {isAuditRunning && (
            <Button
              size="sm"
              className="h-6 bg-destructive px-2 text-[10px] text-destructive-foreground hover:bg-destructive/90"
              onClick={() => void handleCancelTask()}
              disabled={taskCancelPending}
              title="终止语义审计"
              aria-label="终止语义审计"
            >
              {taskCancelPending ? "终止中" : "终止"}
            </Button>
          )}
        </div>
      );
    }

    if (step.key === "reel_plan" && isHighlightTask) {
      return renderHighlightPlanActivityChip();
    }

    return null;
  };

  const canShowExportTools = displayStatus === "review" || displayStatus === "done" || displayStatus === "asr_done";
  const defaultReviewTool: ReviewToolId | null = task.task_type === "highlight_reel"
    ? "highlights"
    : canShowExportTools
      ? "export"
      : null;
  const reviewToolItems: ReviewToolItem[] = [
    {
      id: "script",
      lazy: true,
      label: "原稿匹配",
      description: "核对口播原稿、漏讲和重复录制",
      icon: <BookOpen className="h-4 w-4" />,
      hidden: task.task_type !== "monologue_clean",
      content: <ReferenceScriptPanel key={taskId} taskId={taskId} initialScript={task.params.reference_script ?? ""}
        refreshKey={task.updated_at} disabled={isProcessing} onSaved={loadTask}
        onAudition={(ranges, label) => {
          setPreviewVideoUrl(null);
          setPreviewTitle(null);
          setCurrentTime(ranges[0].start);
          setAuditionRequest({ id: ++auditionRequestId.current, ranges, label });
        }} />,
    },
    {
      id: "cuts",
      label: "接缝复核",
      description: "集中试听剪前剪后，复核删除内容",
      icon: <Scissors className="h-4 w-4" />,
      hidden: !canShowExportTools,
      content: <CutReviewPanel key={taskId} taskId={taskId} segments={segments} disabled={isProcessing}
        onAudition={(ranges, label) => {
          setPreviewVideoUrl(null);
          setPreviewTitle(null);
          setCurrentTime(ranges[0].start);
          setAuditionRequest({ id: ++auditionRequestId.current, ranges, label });
        }}
        onRestore={async (ids) => {
          segmentHistory.push(task.audit_segments);
          try {
            await batchSegmentAction(taskId, ids, "keep");
            await loadTask();
            toast.success(`已恢复 ${ids.length} 个片段`);
            triggerFeedbackNudge();
          } catch { toast.error("恢复失败，请重试"); }
        }} />,
    },
    {
      id: "export",
      label: "导出",
      description: "渲染成片、剪映草稿和金句",
      icon: <Download className="h-4 w-4" />,
      hidden: !canShowExportTools,
      content: (
        <div className="p-3">
          <ExportPanel
            task={task}
            isExporting={isExporting}
            jianyingDir={config.jianyingDir}
            onJianyingDirChange={(jianyingDir) => updateConfig({ jianyingDir })}
            onExportFFmpeg={handleExportFFmpeg}
            onExportJianying={handleExportJianying}
            onCancelExport={handleCancelExport}
            apiKey={resolveFeatureLlm(config, "golden_quote").apiKey}
            provider={resolveFeatureLlm(config, "golden_quote").provider === "ollama" ? "ollama" : "openai"}
            openaiModel={resolveFeatureLlm(config, "golden_quote").model}
            ollamaModel={resolveFeatureLlm(config, "golden_quote").model}
            ollamaBaseUrl={resolveFeatureLlm(config, "golden_quote").baseUrl}
            onGoldenQuoteSaved={(order) => {
              setGoldenQuoteOrder(order);
              setTask(prev => prev ? { ...prev, golden_quote_order: order } : prev);
            }}
            onSeek={(time) => setCurrentTime(time)}
            goldenCandidates={goldenCandidatesTop}
            setGoldenCandidates={setGoldenCandidatesTop}
            goldenAnalyzed={goldenAnalyzedTop}
            setGoldenAnalyzed={setGoldenAnalyzedTop}
            goldenDialogOpen={goldenDialogOpenTop}
            setGoldenDialogOpen={setGoldenDialogOpenTop}
            onClipApplied={loadTask}
          />
        </div>
      ),
    },
    {
      id: "funds",
      label: "资金消耗",
      description: "云端模型 token 与费用",
      icon: <Coins className="h-4 w-4" />,
      content: (
        <FundsUsagePanel taskId={taskId} refreshKey={task.updated_at} />
      ),
    },
    {
      id: "highlights",
      label: "产品集锦",
      description: "集锦版本、质检和批量导出",
      icon: <Sparkles className="h-4 w-4" />,
      hidden: !canShowExportTools || task.task_type !== "highlight_reel",
      content: (
        <div className="h-full min-h-0 p-3">
          <HighlightClipsPanel
            task={task}
            isExporting={isExporting}
            onPlayClip={handlePlayHighlightClip}
            onQuickPreview={(snapshot) => {
              const ranges = buildHighlightPreviewRanges(segments, snapshot.clip.segments.map(segment => segment.id));
              setPreviewVideoUrl(null);
              setPreviewTitle(null);
              previewRestoreTimeRef.current = null;
              setCurrentTime(ranges[0].start);
              setAuditionRequest({ id: ++auditionRequestId.current, ranges, label: `快速试看 · ${snapshot.clip.title}（接缝以渲染预览为准）` });
            }}
            focusedClipId={focusedHighlight?.id}
            onFocusClip={(snapshot) => {
              if (!snapshot) {
                setFocusedHighlight(null);
                setSubtitleNavigation((previous) =>
                  previous?.source === "highlight" ? null : previous
                );
                return;
              }
              const segmentIds = new Set(
                // 集锦规划保存的是裁断前的 segment ID；裁断后使用已按当前时间窗
                // 解析出的片段，确保两段仍会标记为集锦内容。
                snapshot.clip.segments.map((segment) => segment.id),
              );
              setFocusedHighlight({
                id: snapshot.clip.id,
                title: snapshot.reel?.title ?? snapshot.clip.title,
                reel: snapshot.reel,
                qualityStage: snapshot.qualityStage,
                segmentIds,
              });
              const firstSegmentId = segmentIds.values().next().value;
              if (firstSegmentId) {
                requestSubtitleNavigation(firstSegmentId, "highlight");
              }
            }}
            onExport={handleExportHighlightClips}
            onTaskUpdated={loadTask}
            onPrimaryAction={handleHighlightPrimaryAction}
            primaryActionPending={highlightPrimaryPending}
            autoRunOptions={highlightAutoRunOptions}
            autoRunModelLabel={auditModelShortLabel}
            buildOptions={highlightBuildOptions}
            onBuildOptionsChange={setHighlightBuildOptions}
            onLog={(level, source, message, progress) => {
              appendConsoleEntry(level, source, message, progress);
              setConsoleExpanded(true);
            }}
          />
        </div>
      ),
    },
    {
      id: "scenario",
      label: "场景信息",
      description: "审计场景、目标和提示词",
      icon: <BookOpen className="h-4 w-4" />,
      content: (
        <div className="min-h-full">
          <ScenarioInfoPanel
            task={task}
            styleMode={styleMode}
            onStyleModeChange={setStyleMode}
            openaiApiKey={resolveFeatureLlm(config, "audit").apiKey}
            provider={resolveFeatureLlm(config, "audit").provider === "ollama" ? "ollama" : "openai"}
            ollamaModel={resolveFeatureLlm(config, "audit").model}
          />
        </div>
      ),
    },
    {
      id: "speakers",
      label: "说话人",
      description: "说话人统计和字幕分段",
      icon: <Users className="h-4 w-4" />,
      content: (
        <div className="h-full min-h-[420px]">
          <SpeakerPanel task={task} onRefresh={loadTask} />
        </div>
      ),
    },
  ];

  return (
    <div className="h-full flex flex-col overflow-hidden">
      {/* ── 标题行：标题 & 状态 & 识别审计操作（合并为单行，节省视频空间）── */}
      <div className="h-10 shrink-0 border-b border-border flex items-center px-4 gap-2">
        <button
          onClick={handleBack}
          className="flex items-center gap-1.5 text-muted-foreground hover:text-foreground transition-colors text-sm shrink-0"
        >
          <ArrowLeft className="w-4 h-4" />
          <span>返回</span>
        </button>
        <div className="w-px h-4 bg-border shrink-0" />
        <h2 className="text-sm font-medium text-foreground truncate max-w-[160px] shrink-0">{task.name}</h2>
        <span className={cn(
          "status-badge text-[10px] shrink-0",
          activeExecutionRetrying ? "text-amber-300 bg-amber-900/50" : STATUS_COLORS[displayStatus],
        )}>
          {isProcessing && <Loader2 className="w-2.5 h-2.5 animate-spin" />}
          {activeExecutionRetrying ? "自动重试中" : STATUS_LABELS[displayStatus]}
        </span>
        {runtimeHealthDisplay && (
          <span className={cn(
            "text-[10px] font-mono shrink-0 max-w-[240px] truncate",
            runtimeHealthDisplay.tone === "ok" && "text-emerald-400/80",
            runtimeHealthDisplay.tone === "warn" && "text-amber-400/80",
            runtimeHealthDisplay.tone === "error" && "text-red-400/80",
            runtimeHealthDisplay.tone === "unknown" && "text-muted-foreground/60",
          )}>
            {runtimeHealthDisplay.label}
          </span>
        )}

        <div className="ml-auto flex items-center gap-2 shrink-0">
          {/* 仅保留切换 */}
          {segments.length > 0 && (
            <Button
              size="sm"
              variant="outline"
              className={cn(
                "border-border text-xs h-7 gap-1.5",
                showOnlyKept ? "bg-accent text-primary border-primary/30" : "text-muted-foreground"
              )}
              onClick={() => setShowOnlyKept(!showOnlyKept)}
            >
              <Scissors className="w-3 h-3" />
              {showOnlyKept ? "显示全部" : "仅保留"}
            </Button>
          )}
          <Button
            size="sm"
            variant="ghost"
            className="text-muted-foreground hover:text-foreground h-7 w-7 p-0"
            onClick={loadTask}
          >
            <RefreshCw className="w-3.5 h-3.5" />
          </Button>
        </div>
      </div>

      {/* ── 流程步骤条 ── */}
      <StatusStepper
        status={displayStatus}
        taskType={task.task_type}
        stage={currentExecutionActivity ? currentExecutionStage : executionStage}
        executionRun={activeExecutionRun ?? (currentExecutionActivity ? null : executionRun)}
        highlightReelsReady={highlightReelsReady}
        highlightQualityReady={highlightQualityReady}
        renderAction={renderStepAction}
      />

      {executionFailureFinal && !currentExecutionActivity && !executionOperation.includes("export") && (
        <div className="shrink-0 border-b border-amber-500/25 bg-amber-500/5 px-4 py-2">
          <div className="flex flex-wrap items-center gap-2">
            <div className="min-w-0 flex-1">
              <p className={cn("text-xs font-medium", executionRun?.recoverable ? "text-amber-300" : "text-red-400")}>
                {executionRun?.recoverable ? "执行失败，可从已有检查点恢复" : "执行失败，当前错误不可恢复"}
              </p>
              <p className="truncate text-[10px] text-muted-foreground">
                阶段：{executionStage || "未知"} · 复用检查点：{getExecutionCheckpointCount(executionRun)}
                {executionRun?.error?.message ? ` · ${executionRun.error.message}` : ""}
              </p>
            </div>
            <Button
              size="sm"
              variant="outline"
              className="h-7 text-xs"
              onClick={handleContinueExecution}
              disabled={!canResumeExecution(executionRun) || !hasRecoveryTrigger}
              title={!executionRun?.recoverable ? "该失败不可恢复" : !hasRecoveryTrigger ? "当前功能暂无可用触发 API" : undefined}
            >
              从检查点继续
            </Button>
            <Button
              size="sm"
              className="h-7 text-xs"
              onClick={handleFullRestartExecution}
              disabled={!hasRecoveryTrigger}
            >
              完全重新执行
            </Button>
          </div>
        </div>
      )}

      {/* Main 3-panel layout */}
      <ResizablePanelGroup direction="horizontal" className="flex-1 min-h-0 overflow-hidden">
        {/* Left: Video Player */}
        <ResizablePanel defaultSize={25} minSize={24} maxSize={55}>
          <div className="h-full p-3 flex flex-col gap-3 overflow-y-auto">
          <div className="shrink-0">
            {task.video_path ? (
              <div className="space-y-1.5">
                {previewVideoUrl && previewTitle && (
                  <div className="rounded-lg border border-orange-500/25 bg-orange-500/10 px-3 py-1.5 text-[11px] text-orange-300 truncate">
                    当前预览：{previewTitle}
                  </div>
                )}
                <VideoPlayer
                  taskId={taskId}
                  currentTime={seekRequest.time}
                  seekRequestId={seekRequest.id}
                  onTimeUpdate={handlePlaybackTime}
                  segments={segments}
                  playbackMode={playbackMode}
                  onPlaybackModeChange={setPlaybackMode}
                  highlightSegmentIds={highlightPlaybackSegmentIds}
                  clipStart={previewVideoUrl ? null : task.clip_start}
                  clipEnd={previewVideoUrl ? null : task.clip_end}
                  videoUrl={previewVideoUrl}
                  previewMode={!!previewVideoUrl}
                  previewPlayRequestId={previewPlayRequestId}
                  auditionRequest={auditionRequest}
                  onExitPreview={handleExitPreview}
                  onPreviewEnded={handleExitPreview}
                  videoViewportHeight={videoViewportHeight}
                  onVideoViewportHeightChange={handleVideoViewportHeightChange}
                  onVideoNaturalSize={handleVideoNaturalSize}
                  minVideoViewportHeight={VIDEO_VIEWPORT_MIN_HEIGHT}
                  maxVideoViewportHeight={VIDEO_VIEWPORT_MAX_HEIGHT}
                />
              </div>
            ) : (
              <div className="h-[260px] bg-secondary rounded-xl flex flex-col items-center justify-center gap-3">
                <div className="w-12 h-12 rounded-xl bg-card flex items-center justify-center">
                  <Play className="w-6 h-6 text-muted-foreground/40 ml-0.5" />
                </div>
                <p className="text-xs text-muted-foreground">暂无视频</p>
              </div>
            )}
          </div>

          {/* 左下：运行日志 */}
          <div className="mt-auto shrink-0">
            <ConsoleLog
              entries={consoleEntries}
              expanded={consoleExpanded}
              isRunning={isProcessing}
              healthLabel={runtimeHealthDisplay?.label}
              healthTone={runtimeHealthDisplay?.tone}
              healthActionLabel={canShowManualAsrRetry ? "手动重试 ASR" : canShowAuditRetry ? "重新审计" : null}
              onHealthAction={canShowManualAsrRetry ? handleManualAsrRetry : canShowAuditRetry ? () => requestAuditRerun("keep") : undefined}
              onRevealExport={task.export_path ? handleRevealExport : undefined}
              executionRun={executionRun}
              onToggle={() => setConsoleExpanded(!consoleExpanded)}
              onClear={() => {
                setConsoleEntries([]);
                try { localStorage.removeItem(consoleStorageKey); } catch { /* ignore */ }
              }}
            />
          </div>
          </div>
        </ResizablePanel>

        <ResizableHandle
          withHandle
          className="z-10 bg-border hover:bg-primary/70 data-[resize-handle-active]:bg-primary"
        />

        <ResizablePanel minSize={45}>
        <div className="h-full min-w-0 flex flex-col overflow-hidden">
          <div className="flex-1 min-h-0 overflow-hidden flex">
        {/* Center: Subtitle Audit Stream (40%) */}
        <div className="flex-1 min-w-0 flex flex-col">
          {/* Header */}
          <div className="h-9 shrink-0 border-b border-border flex items-center px-3 gap-3">
            <span className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
              字幕审计流
            </span>
            {segments.length > 0 && (
              <Button
                size="sm"
                variant="outline"
                className="h-6 gap-1 px-2 text-[10px]"
                onClick={handleOpenSubtitleOptimize}
                disabled={isProcessing || subtitleOptimizeLoading || subtitleOptimizeApplying}
                title="优化当前字幕文字"
              >
                {subtitleOptimizeLoading
                  ? <Loader2 className="h-3 w-3 animate-spin" />
                  : <Sparkles className="h-3 w-3" />}
                优化字幕
              </Button>
            )}
            {segments.length > 0 && (
              <div className="flex items-center gap-2 ml-auto text-[11px]">
                {!isReadOnly && (
                  <>
                    <Button
                      size="sm"
                      variant="ghost"
                      className="h-6 w-6 p-0 text-muted-foreground hover:text-foreground disabled:opacity-30"
                      disabled={!segmentHistory.canUndo}
                      onClick={handleUndo}
                      title="撤销 (Ctrl+Z)"
                    >
                      <Undo2 className="w-3.5 h-3.5" />
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      className="h-6 w-6 p-0 text-muted-foreground hover:text-foreground disabled:opacity-30"
                      disabled={!segmentHistory.canRedo}
                      onClick={handleRedo}
                      title="重做 (Ctrl+Shift+Z)"
                    >
                      <Redo2 className="w-3.5 h-3.5" />
                    </Button>
                    <div className="w-px h-4 bg-border" />
                  </>
                )}
                <span className="text-green-400 font-mono">{task.segments_kept} ✓</span>
                <span className="text-red-400 font-mono">{task.segments_deleted} ✗</span>
                {task.edited_duration && (
                  <span className="timestamp">{formatDuration(task.edited_duration)}</span>
                )}
              </div>
            )}
          </div>

          {focusedHighlight && (
            <div className="shrink-0 border-b border-orange-500/25 bg-orange-500/10 px-3 py-1.5 flex items-center gap-2">
              <Sparkles className="w-3.5 h-3.5 text-orange-400 shrink-0" />
              <span className="text-xs text-orange-200 truncate">
                正在编辑集锦：{focusedHighlight.title}
              </span>
              <span className="hidden text-[10px] text-orange-200/50 xl:inline">
                可加入同产品场次内的保留句
              </span>
              <span className="text-[10px] text-orange-200/60 ml-auto shrink-0">
                {focusedHighlight.segmentIds.size} 句
              </span>
              <Button
                size="sm"
                variant="ghost"
                className="h-6 px-2 text-[11px] text-orange-200 hover:bg-orange-500/15 hover:text-orange-100"
                onClick={() => setFocusedHighlight(null)}
                disabled={highlightSegmentPendingId !== null}
              >
                完成编辑
              </Button>
            </div>
          )}

          {quickSplitMode && (
            <div className="shrink-0 border-b border-green-500/30 bg-green-500/10 px-3 py-1.5 flex items-center gap-2 animate-in fade-in slide-in-from-top-1 duration-150">
              <Scissors className="w-3.5 h-3.5 text-green-400 shrink-0" />
              <span className="text-xs text-green-200">Command 裁断：点击文字前切开，松开退出</span>
            </div>
          )}

          {/* 浮动批量操作条（审计完成前隐藏） */}
          {!isReadOnly && selectedIds.size > 0 && (
            <div className="shrink-0 border-b border-blue-500/30 bg-blue-500/10 px-3 py-1.5 flex items-center gap-2 animate-in fade-in slide-in-from-top-1 duration-150">
              <span className="text-xs text-blue-300 font-medium">
                已选 {selectedIds.size} 个
              </span>
              <div className="w-px h-4 bg-blue-500/30" />
              <Button
                size="sm"
                variant="ghost"
                className="h-6 px-2 text-[11px] text-green-400 hover:bg-green-900/30 hover:text-green-300 gap-1"
                onClick={() => handleBatchAction("keep")}
              >
                <Eye className="w-3 h-3" />
                标记保留
              </Button>
              <Button
                size="sm"
                variant="ghost"
                className="h-6 px-2 text-[11px] text-red-400 hover:bg-red-900/30 hover:text-red-300 gap-1"
                onClick={() => handleBatchAction("delete")}
              >
                <EyeOff className="w-3 h-3" />
                标记删除
              </Button>
              <Button
                size="sm"
                variant="ghost"
                className="h-6 px-2 text-[11px] text-red-400 hover:bg-red-900/30 hover:text-red-300 gap-1"
                onClick={() => handleBatchAction("remove")}
              >
                <Trash2 className="w-3 h-3" />
                彻底删除
              </Button>
              <div className="ml-auto flex items-center gap-1.5">
                <span className="text-[10px] text-muted-foreground/50">K 保留 · D 删除 · Del 移除 · Esc 取消</span>
                <Button
                  size="sm"
                  variant="ghost"
                  className="h-6 w-6 p-0 text-muted-foreground hover:text-foreground"
                  onClick={clearSelection}
                >
                  <X className="w-3.5 h-3.5" />
                </Button>
              </div>
            </div>
          )}

          {/* Segment list */}
          <div ref={segmentListRef} className="flex-1 overflow-y-auto p-3">
            {/* ASR 完成、审计未开始：提示用户先运行 AI 审计 */}
            {displayStatus === "asr_done" && segments.length > 0 && (
              <div className="flex items-center gap-2 mb-3 px-3 py-2 rounded-lg bg-primary/10 border border-primary/20 text-primary text-xs">
                <AlertCircle className="w-3.5 h-3.5 shrink-0" />
                <span className="flex-1">审计输入已准备好，请先点击「AI 审计」，审计完成后才能手动调整片段。</span>
                {analysisReport && (
                  <Button
                    size="sm"
                    variant="ghost"
                    className="h-6 px-2 text-[11px] text-primary hover:bg-primary/10"
                    onClick={() => setAnalysisReportOpen(true)}
                  >
                    查看整理说明
                  </Button>
                )}
              </div>
            )}
            {/* ASR 重新识别中：有旧版本时顶部显示提示条 */}
            {displayStatus === "asr_running" && task.asr_history && task.asr_history.length > 0 && (
              <div className="flex items-center gap-2 mb-3 px-3 py-2 rounded-lg bg-blue-950/40 border border-blue-800/40 text-blue-400 text-xs">
                <Loader2 className="w-3.5 h-3.5 animate-spin shrink-0" />
                <span>
                  识别中，当前版本已保留为{" "}
                  <span className="font-mono font-semibold">v{task.asr_current_version}</span>
                  ，完成后将自动切换至新版本...
                </span>
              </div>
            )}
            {/* 审计进行中：有旧段落时顶部显示提示条 */}
            {displayStatus === "audit_running" && segments.length > 0 && (
              <div className="flex items-center gap-2 mb-3 px-3 py-2 rounded-lg bg-amber-950/40 border border-amber-800/40 text-amber-400 text-xs">
                <Loader2 className="w-3.5 h-3.5 animate-spin shrink-0" />
                <span>AI 审计中，结果将在完成后自动更新...</span>
              </div>
            )}
            {segments.length === 0 ? (
              <div className={cn(
                "flex flex-col h-full gap-3",
                displayStatus === "asr_recognized" || displayStatus === "asr_diarized"
                  ? "items-stretch justify-start text-left"
                  : "items-center justify-center text-center",
              )}>
                {displayStatus === "pending" && !task.video_path && (
                  <>
                    <div className="w-10 h-10 rounded-xl bg-secondary flex items-center justify-center">
                      <Upload className="w-5 h-5 text-muted-foreground/40" />
                    </div>
                    <p className="text-xs text-muted-foreground">请先上传视频文件</p>
                  </>
                )}
                {displayStatus === "pending" && task.video_path && (
                  <>
                    <div className="w-10 h-10 rounded-xl bg-secondary flex items-center justify-center">
                      <Mic className="w-5 h-5 text-muted-foreground/40" />
                    </div>
                    <p className="text-xs text-muted-foreground">点击「运行 ASR」开始语音识别</p>
                    <Button size="sm" onClick={() => setAsrDialogOpen(true)}
                      className="bg-primary text-primary-foreground hover:bg-primary/90 text-xs gap-1.5">
                      <Mic className="w-3.5 h-3.5" />运行 ASR
                    </Button>
                  </>
                )}
                {(displayStatus === "asr_recognized" || displayStatus === "asr_diarized") && (
                  <>
                    <div className="rounded-lg border border-sky-800/40 bg-sky-950/20 overflow-hidden">
                      <div className="flex items-center justify-between gap-3 px-3 py-2 border-b border-sky-800/30">
                        <div className="flex min-w-0 items-center gap-2 text-xs">
                          <AlertCircle className="h-3.5 w-3.5 shrink-0 text-sky-300" />
                          <span className="shrink-0 font-medium text-sky-300">{displayStatus === "asr_diarized" ? "说话人分离完成" : "语音识别完成"}</span>
                          <span className="truncate text-[10px] text-muted-foreground/70">
                            {task.asr_result?.words?.length ?? 0} 个字词 · {recognizedChunks.length} 个预览段
                          </span>
                        </div>
                        <Button
                          size="sm"
                          onClick={() => setAnalyzeDialogOpen(true)}
                          className="h-7 bg-primary text-primary-foreground hover:bg-primary/90 text-xs gap-1.5 shrink-0"
                        >
                          <Cpu className="w-3.5 h-3.5" />
                          继续准备审计输入
                        </Button>
                        {displayStatus === "asr_recognized" && (
                          <Button size="sm" variant="outline" onClick={() => void handleDiarization()} className="h-7 border-violet-500/40 text-violet-300 hover:bg-violet-500/10 text-xs gap-1 shrink-0">
                            <Users className="w-3.5 h-3.5" />说话人分离
                          </Button>
                        )}
                      </div>

                      {recognizedChunks.length > 0 ? (
                        <div className="max-h-[calc(100vh-430px)] min-h-[220px] overflow-y-auto p-2 space-y-2">
                          {recognizedChunks.map((chunk, index) => (
                            <button
                              key={chunk.id}
                              type="button"
                              onClick={() => setCurrentTime(chunk.start)}
                              className="w-full text-left rounded-md border border-border/50 bg-background/40 px-3 py-2 hover:border-sky-700/60 hover:bg-sky-950/20 transition-colors"
                              title="点击跳转到此时间点"
                            >
                              <div className="flex items-center gap-2 mb-1 text-[10px] text-muted-foreground">
                                <span className="font-mono text-sky-300">#{index + 1}</span>
                                <Clock className="w-3 h-3" />
                                <span className="font-mono">
                                  {formatTimestamp(chunk.start)} - {formatTimestamp(chunk.end)}
                                </span>
                                <span className="ml-auto">{chunk.wordCount} 字词</span>
                              </div>
                              <p className="text-xs leading-6 text-foreground/85">
                                {chunk.text}
                              </p>
                            </button>
                          ))}
                        </div>
                      ) : (
                        <div className="min-h-[220px] flex items-center justify-center px-3 py-8 text-xs text-muted-foreground">
                          {recognizedText || "识别文本已保存，等待准备审计输入。"}
                        </div>
                      )}
                    </div>
                  </>
                )}
                {(displayStatus === "asr_running" || displayStatus === "diarization_running" || displayStatus === "analyze_running" || displayStatus === "audit_running") && (
                  <>
                    <Loader2 className="w-8 h-8 animate-spin text-primary" />
                    <p className="text-xs text-muted-foreground">
                      {displayStatus === "asr_running"
                        ? "ASR 识别中..."
                        : displayStatus === "diarization_running"
                          ? "说话人分离中，请稍候..."
                        : displayStatus === "analyze_running"
                          ? "准备审计输入中，请稍候..."
                          : "AI 审计中，请稍候..."}
                    </p>
                  </>
                )}
              </div>
            ) : (
              <div
                className="relative w-full"
                style={{ height: `${segmentVirtualizer.getTotalSize()}px` }}
              >
                {segmentVirtualizer.getVirtualItems().map((virtualRow) => {
                  const seg = displaySegments[virtualRow.index];
                  if (!seg) return null;
                  const segmentIndex = segmentIndexById.get(seg.id) ?? -1;
                  const previousSegment = segmentIndex > 0 ? segments[segmentIndex - 1] : null;
                  const nextSegment =
                    segmentIndex >= 0 && segmentIndex < segments.length - 1
                      ? segments[segmentIndex + 1]
                      : null;
                  const canMergePrevious = Boolean(
                    previousSegment && (!showOnlyKept || displaySegments[virtualRow.index - 1]?.id === previousSegment.id)
                  );
                  const canMergeNext = Boolean(
                    nextSegment && (!showOnlyKept || displaySegments[virtualRow.index + 1]?.id === nextSegment.id)
                  );
                  const cs = task.clip_start ?? null;
                  const ce = task.clip_end ?? null;
                  const isOutOfClip =
                    (cs !== null && seg.end <= cs) ||
                    (ce !== null && seg.start >= ce);
                  const isInFocusedHighlight = focusedHighlight?.segmentIds.has(seg.id) ?? false;
                  const highlightMembershipAction = focusedHighlight?.reel
                    ? isInFocusedHighlight
                      ? "remove"
                      : focusedOccurrenceSegmentIds.has(seg.id) && seg.action === "keep"
                        ? "add"
                        : null
                    : null;
                  return (
                    <div
                      key={seg.id}
                      ref={segmentVirtualizer.measureElement}
                      data-index={virtualRow.index}
                      data-segment-id={seg.id}
                      className="absolute left-0 top-0 w-full"
                      style={{ transform: `translateY(${virtualRow.start}px)` }}
                      title={isOutOfClip ? "此段在裁剪区间之外，不会被导出" : undefined}
                    >
                      <SegmentItem
                        segment={seg}
                        isActive={seg.id === activeSegmentId}
                        isSelected={selectedIds.has(seg.id)}
                        isInFocusedHighlight={isInFocusedHighlight}
                        highlightMembershipAction={highlightMembershipAction}
                        highlightMembershipPending={highlightSegmentPendingId !== null}
                        onHighlightMembershipToggle={handleHighlightSegmentMembership}
                        isFirst={!canMergePrevious}
                        isLast={!canMergeNext}
                        onClick={(e) => handleSegmentClick(seg, e)}
                        onToggle={(e) => { e.stopPropagation(); handleToggleSegment(seg.id); }}
                        onCharClick={(time) => handleSegmentSeek(seg, time)}
                        onSeek={(time) => handleSegmentSeek(seg, time)}
                        activeCharTime={-1}
                        playbackClock={previewVideoUrl ? undefined : playbackClock}
                        onEditSave={handleEditSave}
                        onEditStart={handleEditStart}
                        onEditEnd={handleEditEnd}
                        onMergePrevious={handleMergePrevious}
                        onMergeNext={handleMergeNext}
                        readOnly={isReadOnly}
                        subtitleReadOnly={isSubtitleReadOnly}
                        decisionReadOnly={isDecisionReadOnly}
                        wordEditMode={wordEditSegId === seg.id}
                        selectedWordIndices={wordEditSegId === seg.id ? selectedWordIndices : undefined}
                        onWordEditEnter={handleWordEditEnter}
                        onWordEditExit={handleWordEditExit}
                        onWordToggle={handleWordToggle}
                        onWordCommit={handleWordCommit}
                        quickSplitMode={quickSplitMode}
                        splitMode={splitSegId === seg.id}
                        onSplitEnter={handleSplitEnter}
                        onSplitExit={handleSplitExit}
                        onSplitCommit={handleSplitCommit}
                        goldenQuoteIndex={goldenQuoteOrder.indexOf(seg.id)}
                        onEditReason={(segId, action) => setPendingReasonSeg({ id: segId, action })}
                        onSetClipStart={handleSetClipStart}
                        onSetClipEnd={handleSetClipEnd}
                        isOutOfClip={isOutOfClip}
                      />
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          {/* 学习提示浮动条 */}
          {feedbackNudgeShown && !promptFeedbackDialogOpen && (() => {
            const modifiedCount = segments.filter(
              (s) => (s as any).user_override && (s as any).claude_action && s.action !== (s as any).claude_action
            ).length;
            if (modifiedCount === 0) return null;
            return (
              <div className="shrink-0 border-t border-primary/30 bg-primary/5 px-3 py-2 flex items-center gap-2 animate-in fade-in slide-in-from-bottom-1 duration-200">
                <Sparkles className="w-3.5 h-3.5 text-primary shrink-0" />
                <span className="text-xs text-foreground/80 flex-1">
                  你修改了 {modifiedCount} 处，让 AI 学习你的剪辑偏好？
                </span>
                <Button
                  size="sm"
                  className="h-6 text-xs bg-primary/20 text-primary hover:bg-primary/30 border-0"
                  onClick={() => { setPromptFeedbackDialogOpen(true); setFeedbackNudgeShown(false); }}
                >
                  立即学习
                </Button>
                <button
                  className="text-muted-foreground hover:text-foreground text-xs px-1"
                  onClick={() => { setFeedbackNudgeShown(false); setNudgeDismissed(true); }}
                >
                  忽略
                </button>
              </div>
            );
          })()}
        </div>

        <ReviewToolDock tools={reviewToolItems} defaultActiveTool={defaultReviewTool} />
          </div>
        </div>
        </ResizablePanel>
      </ResizablePanelGroup>

      {/* ASR 模型选择弹窗 */}
      <AsrModelDialog
        open={asrDialogOpen}
        onClose={() => setAsrDialogOpen(false)}
        videoDuration={task.video_duration || 0}
        selection={asrSelection}
        onSelect={setAsrSelection}
        onConfirm={() => {
          if (hasAsrHistory || hasRecognizedWords || isAsrExecutionFailure) {
            setAsrDialogOpen(false);
            requestAsrRerun(asrSelection);
          } else {
            void handleASR(asrSelection);
          }
        }}
        isProcessing={isProcessing}
      />

      {/* 文本整理配置弹窗 */}
      <AsrAnalyzeDialog
        open={analyzeDialogOpen}
        onClose={() => setAnalyzeDialogOpen(false)}
        taskType={task.task_type}
        selection={asrSelection}
        onSelect={setAsrSelection}
        onConfirm={() => handleAnalyze(asrSelection)}
        isProcessing={isProcessing}
      />

      <SubtitleOptimizeDialog
        open={subtitleOptimizeOpen}
        onClose={() => setSubtitleOptimizeOpen(false)}
        preview={subtitleOptimizePreview}
        loading={subtitleOptimizeLoading}
        applying={subtitleOptimizeApplying}
        selectedIds={subtitleOptimizeSelectedIds}
        onToggle={(segmentId) => {
          setSubtitleOptimizeSelectedIds((prev) => {
            const next = new Set(prev);
            if (next.has(segmentId)) next.delete(segmentId);
            else next.add(segmentId);
            return next;
          });
        }}
        onSelectAll={(checked) => {
          setSubtitleOptimizeSelectedIds(
            checked
              ? new Set((subtitleOptimizePreview?.changes ?? []).map((change) => change.segment_id))
              : new Set()
          );
        }}
        onApply={handleApplySubtitleOptimize}
        onRefresh={loadSubtitleOptimizationPreview}
        onRunFullAnalyze={handleRunFullAnalyzeFromSubtitleDialog}
      />

      {/* 快速调整字幕分段 */}
      <Dialog open={resegmentDialogOpen} onOpenChange={setResegmentDialogOpen}>
        <DialogContent className="bg-card border-border max-w-lg">
          <DialogHeader>
            <DialogTitle className="text-foreground flex items-center gap-2">
              <Scissors className="w-4 h-4 text-primary" />
              调整字幕分段
            </DialogTitle>
            <DialogDescription className="text-muted-foreground text-xs">
              不重跑 ASR 和文本规则，只基于现有字词时间戳快速预览并保存新的字幕卡边界。
            </DialogDescription>
          </DialogHeader>
          <ResegmentPanel
            task={task}
            onRefresh={loadTask}
            onApplied={() => setResegmentDialogOpen(false)}
          />
        </DialogContent>
      </Dialog>

      {/* 无 API Key 时的确认对话框 */}
      <Dialog open={ruleEngineDialogOpen} onOpenChange={setRuleEngineDialogOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <AlertCircle className="w-5 h-5 text-amber-400" />
              未配置 API Key
            </DialogTitle>
            <DialogDescription className="text-sm text-muted-foreground pt-1">
              使用云端 AI 审计需要在设置中填写 OpenAI API Key。
            </DialogDescription>
          </DialogHeader>
          <div className="text-sm text-muted-foreground space-y-3 py-1">
            <p>
              你可以使用内置的<span className="text-foreground font-medium">本地规则引擎</span>进行初步审计，
              它会根据片段时长、语气词、重说等规则自动标记，
              但准确度低于云端模型。
            </p>
            <p className="text-xs text-muted-foreground/70">
              建议先在「设置」中配置 API Key，再使用 AI 审计获得更准确的结果。
            </p>
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => setRuleEngineDialogOpen(false)}
            >
              取消
            </Button>
            <Button
              variant="outline"
              size="sm"
              className="border-amber-500/50 text-amber-400 hover:bg-amber-500/10"
              onClick={handleRuleEngineConfirm}
              disabled={isProcessing}
            >
              <Cpu className="w-3.5 h-3.5 mr-1" />
              使用规则引擎
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      <AiRunConfirmationDialog
        open={pendingModelRun !== null}
        taskId={taskId}
        request={pendingModelRun?.request ?? null}
        confirmLabel={pendingModelRun?.confirmLabel}
        onOpenChange={(open) => {
          if (!open) setPendingModelRun(null);
        }}
        onConfirm={async (preview) => {
          if (pendingModelRun) await pendingModelRun.execute(preview);
        }}
      >
        {pendingModelRun?.request.operation === "audit" && (
          <div className="grid gap-2 sm:grid-cols-2">
            <button
              type="button"
              className={cn(
                "rounded-md border px-3 py-2 text-left transition-colors",
                reauditMode === "keep"
                  ? "border-primary bg-primary/10"
                  : "border-border hover:bg-secondary/50",
              )}
              onClick={() => requestAuditRerun("keep")}
            >
              <p className="text-xs font-medium text-foreground">保留当前结果重跑</p>
              <p className="mt-0.5 text-[10px] text-muted-foreground">复用已有批次和人工编辑</p>
            </button>
            <button
              type="button"
              className={cn(
                "rounded-md border px-3 py-2 text-left transition-colors",
                reauditMode === "reset"
                  ? "border-amber-500 bg-amber-500/10"
                  : "border-border hover:bg-secondary/50",
              )}
              onClick={() => requestAuditRerun("reset")}
            >
              <p className="text-xs font-medium text-foreground">清空审计结果重跑</p>
              <p className="mt-0.5 text-[10px] text-muted-foreground">
                保留 ASR v{task.asr_current_version ?? task.asr_history?.at(-1)?.version ?? "?"}
              </p>
            </button>
          </div>
        )}
      </AiRunConfirmationDialog>

      {/* 云端审计失败的确认对话框 */}
      <Dialog open={claudeFailedDialogOpen && !activeExecutionRetrying} onOpenChange={setClaudeFailedDialogOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <AlertCircle className="w-5 h-5 text-red-400" />
              AI 审计失败
            </DialogTitle>
            <DialogDescription className="text-sm text-muted-foreground pt-1">
              {auditErrorTypeDescription(claudeFailedErrorType)} 自动重试已结束；可复用已完成批次继续，或丢弃旧审计结果完全重跑。
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-3 py-1">
            <div className="rounded-md bg-red-500/10 border border-red-500/20 px-3 py-2">
              {claudeFailedErrorType && (
                <p className="text-[10px] text-red-300/70 font-mono mb-1">error_type: {claudeFailedErrorType}</p>
              )}
              <p className="text-xs text-red-400 font-mono break-all">{claudeFailedError}</p>
            </div>

            <Accordion type="single" collapsible className="w-full">
              <AccordionItem value="rules" className="border-border/40">
                <AccordionTrigger className="text-sm py-2 hover:no-underline">
                  <span className="flex items-center gap-1.5">
                    <Eye className="w-3.5 h-3.5 text-muted-foreground" />
                    预览本地规则引擎
                  </span>
                </AccordionTrigger>
                <AccordionContent>
                  <div className="text-xs text-muted-foreground space-y-2 pl-1">
                    {(!task?.task_type || task.task_type === "monologue_clean") && (
                      <>
                        <p className="text-foreground/80 font-medium">口播精修规则：</p>
                        <ul className="list-disc pl-4 space-y-1">
                          <li><span className="text-amber-400">Fragment</span> — 过短片段（时长 &lt; 阈值且文字 &lt; 4 字）自动删除</li>
                          <li><span className="text-amber-400">Filler</span> — 纯语气词片段（嗯、啊、那个…）自动删除</li>
                          <li><span className="text-amber-400">Retake</span> — 相邻片段开头相同 N 字，删除前一句（重说检测）</li>
                          <li><span className="text-amber-400">气口补偿</span> — 保留片段前后各加 150ms / 100ms 呼吸余量</li>
                        </ul>
                      </>
                    )}
                    {task?.task_type === "interview_compress" && (
                      <>
                        <p className="text-foreground/80 font-medium">直播访谈压缩规则：</p>
                        <ul className="list-disc pl-4 space-y-1">
                          <li><span className="text-amber-400">Fragment</span> — 过短片段自动删除</li>
                          <li><span className="text-amber-400">Filler</span> — 纯语气词片段自动删除</li>
                          <li><span className="text-amber-400">Retake</span> — 重说检测（删除前句）</li>
                          <li><span className="text-amber-400">嘉宾保护</span> — spk1 片段仅在极短（&lt; 0.8s）时删除</li>
                          <li><span className="text-amber-400">气口补偿</span> — 前后各加 200ms / 150ms</li>
                        </ul>
                      </>
                    )}
                    {task?.task_type === "highlight_reel" && (
                      <>
                        <p className="text-foreground/80 font-medium">本地审计兜底：</p>
                        <ul className="list-disc pl-4 space-y-1">
                          <li><span className="text-amber-400">短碎片过滤</span> — 删除过短且无法独立成义的片段</li>
                          <li><span className="text-amber-400">语气词过滤</span> — 删除短语气词、口癖片段</li>
                          <li><span className="text-amber-400">重说过滤</span> — 相邻片段开头重复时删除前句</li>
                          <li><span className="text-amber-400">句子理解</span> — 云端 AI 可标记产品名、开场、卖点、故事；本地规则不做产品识别</li>
                          <li><span className="text-amber-400">气口补偿</span> — 快节奏：前后各加 80ms / 50ms</li>
                        </ul>
                      </>
                    )}
                    {task?.task_type === "highlight_reel" ? (
                      <p className="text-muted-foreground/60 pt-1 italic">
                        本地规则仅做机械过滤和节奏处理，不生成产品语义标记，也不执行“一个集锦只讲一个产品”等集锦规划规则。
                      </p>
                    ) : (
                      <p className="text-muted-foreground/60 pt-1 italic">
                        规则引擎仅做机械过滤，无法识别语义重复、内容冗余等深层问题。
                      </p>
                    )}
                  </div>
                </AccordionContent>
              </AccordionItem>
            </Accordion>
          </div>

          <div className="flex justify-end gap-2 pt-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => setClaudeFailedDialogOpen(false)}
            >
              取消
            </Button>
            <Button
              size="sm"
              className="gap-1.5"
              onClick={handleResumeAudit}
              disabled={isProcessing}
            >
              <RefreshCw className="h-3.5 w-3.5" />
              继续 AI 审计（复用缓存）
            </Button>
            <Button
              variant="outline"
              size="sm"
              className="border-red-500/50 text-red-300 hover:bg-red-500/10"
              onClick={() => {
                setClaudeFailedDialogOpen(false);
                requestAuditRerun("reset");
              }}
              disabled={isProcessing}
            >
              <RotateCcw className="h-3.5 w-3.5 mr-1" />
              完全重新执行
            </Button>
            <Button
              variant="outline"
              size="sm"
              className="border-amber-500/50 text-amber-400 hover:bg-amber-500/10"
              onClick={() => {
                setClaudeFailedDialogOpen(false);
                handleRuleEngineConfirm();
              }}
              disabled={isProcessing}
            >
              <Cpu className="w-3.5 h-3.5 mr-1" />
              使用规则引擎
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      {/* 提示词反馈优化弹窗 */}
      <PromptFeedbackDialog
        open={promptFeedbackDialogOpen}
        onClose={() => setPromptFeedbackDialogOpen(false)}
        taskId={taskId}
        taskType={task.task_type}
        openaiApiKey={resolveFeatureLlm(config, "learning").apiKey}
        openaiModel={resolveFeatureLlm(config, "learning").model}
        segments={task.audit_segments}
      />

      {/* 手动 toggle 后的原因选择浮窗 */}
      <ReasonPickerSheet
        open={pendingReasonSeg !== null}
        action={pendingReasonSeg?.action}
        onSelect={async (reason) => {
          if (!pendingReasonSeg) return;
          const segId = pendingReasonSeg.id;
          setPendingReasonSeg(null);
          try {
            await patchSegment(taskId, segId, { reason });
            setTask((prev) => {
              if (!prev) return prev;
              return {
                ...prev,
                audit_segments: prev.audit_segments.map((s) =>
                  s.id === segId ? { ...s, reason } : s
                ),
              };
            });
          } catch {
            toast.error("保存原因失败");
          }
        }}
        onSkip={() => setPendingReasonSeg(null)}
      />

      {/* 文本整理说明弹窗 */}
      <Dialog open={analysisReportOpen} onOpenChange={setAnalysisReportOpen}>
        <DialogContent className="bg-card border-border max-w-4xl max-h-[90vh] flex flex-col">
          <DialogHeader>
            <DialogTitle className="text-foreground flex items-center gap-2">
              <Cpu className="w-4 h-4 text-primary" />
              文本整理说明
            </DialogTitle>
            <DialogDescription className="text-muted-foreground text-sm">
              这里展示的是规则链执行记录，不是 AI 审计提示词。AI 审计提示词可在下一步「查看/复制 AI 审计提示词」中查看。
            </DialogDescription>
          </DialogHeader>

          {analysisReport ? (
            <div className="flex-1 overflow-y-auto space-y-4 min-h-0">
              <div className="grid grid-cols-2 gap-3">
                <div className="rounded-lg border border-border bg-secondary/20 p-3">
                  <p className="text-xs font-medium text-foreground mb-2">本次设置</p>
                  <div className="grid grid-cols-2 gap-2">
                    {Object.entries(analysisReport.settings ?? {}).map(([key, value]) => (
                      <div key={key} className="rounded border border-border/60 bg-background/40 px-2 py-1.5">
                        <p className="text-[10px] text-muted-foreground">{formatAnalysisReportLabel(key)}</p>
                        <p className="text-xs text-foreground font-mono truncate">{formatAnalysisReportValue(value, key)}</p>
                      </div>
                    ))}
                  </div>
                </div>

                <div className="rounded-lg border border-border bg-secondary/20 p-3">
                  <p className="text-xs font-medium text-foreground mb-2">结果统计</p>
                  <div className="grid grid-cols-2 gap-2">
                    {Object.entries(analysisReport.stats ?? {}).map(([key, value]) => (
                      <div key={key} className="rounded border border-border/60 bg-background/40 px-2 py-1.5">
                        <p className="text-[10px] text-muted-foreground">{formatAnalysisReportLabel(key)}</p>
                        <p className="text-xs text-foreground font-mono truncate">{formatAnalysisReportValue(value, key)}</p>
                      </div>
                    ))}
                  </div>
                </div>
              </div>

              <div className="rounded-lg border border-border bg-secondary/20 p-3">
                <p className="text-xs font-medium text-foreground mb-2">规则链执行结果</p>
                <div className="space-y-2">
                  {(analysisReport.steps ?? []).map((step, index) => (
                    <div key={step.key} className="rounded-md border border-border/60 bg-background/40 px-3 py-2">
                      <div className="flex items-center gap-2">
                        <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-primary/15 text-[10px] font-mono text-primary">
                          {index + 1}
                        </span>
                        <p className="text-xs font-medium text-foreground">{step.title}</p>
                        <span className={cn(
                          "ml-auto rounded px-1.5 py-0.5 text-[10px]",
                          step.enabled ? "bg-primary/15 text-primary" : "bg-muted text-muted-foreground",
                        )}>
                          {step.enabled ? "已执行" : "未启用"}
                        </span>
                      </div>
                      <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">{step.description}</p>
                      <div className="mt-2 flex flex-wrap gap-2 text-[10px] text-muted-foreground">
                        {step.input_count !== undefined && <span>输入 {step.input_count}</span>}
                        {step.output_count !== undefined && <span>输出 {step.output_count}</span>}
                        {step.changed_count !== undefined && <span>调整 {step.changed_count}</span>}
                        {step.note && <span className="text-foreground/70">{step.note}</span>}
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              {(analysisReport.rule_events?.length ?? 0) > 0 && (
                <div className="rounded-lg border border-border bg-secondary/20 p-3">
                  <div className="flex items-center justify-between gap-2 mb-2">
                    <p className="text-xs font-medium text-foreground">规则事件日志</p>
                    <span className="text-[10px] text-muted-foreground">
                      显示前 {Math.min(analysisReport.rule_events?.length ?? 0, 20)} / {analysisReport.rule_events?.length ?? 0} 条
                    </span>
                  </div>
                  <div className="max-h-56 overflow-y-auto space-y-1.5 pr-1">
                    {(analysisReport.rule_events ?? []).slice(0, 20).map((event, index) => (
                      <div key={`${event.type}-${event.segment_id ?? index}-${index}`} className="rounded-md border border-border/60 bg-background/40 px-3 py-2">
                        <div className="flex items-center gap-2 text-[10px]">
                          <span className="font-mono text-primary">#{index + 1}</span>
                          <span className="rounded bg-primary/10 px-1.5 py-0.5 text-primary">{String(event.type ?? "event")}</span>
                          {event.source !== undefined && (
                            <span className="text-muted-foreground">{String(event.source)}</span>
                          )}
                          {event.segment_id !== undefined && (
                            <span className="ml-auto font-mono text-muted-foreground">{String(event.segment_id)}</span>
                          )}
                        </div>
                        <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
                          {String(event.reason ?? event.text ?? "")}
                        </p>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              <div className="rounded-lg border border-border bg-secondary/20 p-3">
                <p className="text-xs font-medium text-foreground mb-2">片段形成原因</p>
                <div className="max-h-72 overflow-y-auto space-y-2 pr-1">
                  {(analysisReport.segment_notes ?? []).map((note, index) => (
                    <button
                      key={note.segment_id}
                      type="button"
                      onClick={() => handleAnalysisNoteJump(note.segment_id, note.start)}
                      className="w-full rounded-md border border-border/60 bg-background/40 px-3 py-2 text-left hover:border-primary/50 hover:bg-primary/5 transition-colors"
                    >
                      <div className="flex items-center gap-2 text-[10px] text-muted-foreground">
                        <span className="font-mono text-primary">#{index + 1}</span>
                        <Clock className="w-3 h-3" />
                        <span className="font-mono">{formatTimestamp(note.start)} - {formatTimestamp(note.end)}</span>
                      </div>
                      <p className="mt-1 text-xs text-foreground/85 line-clamp-2">{note.text}</p>
                      <ul className="mt-1 space-y-0.5">
                        {note.reasons.map((reason) => (
                          <li key={reason} className="text-[10px] leading-relaxed text-muted-foreground">
                            {reason}
                          </li>
                        ))}
                      </ul>
                    </button>
                  ))}
                </div>
              </div>
            </div>
          ) : (
            <div className="rounded-lg border border-border bg-secondary/20 p-6 text-center text-sm text-muted-foreground">
              当前任务没有文本整理报告。旧任务可重新运行文本整理后生成。
            </div>
          )}
        </DialogContent>
      </Dialog>

      {/* 手动审计弹窗 */}
      <Dialog open={manualAuditDialogOpen} onOpenChange={setManualAuditDialogOpen}>
        <DialogContent className="bg-card border-border max-w-4xl max-h-[90vh] flex flex-col">
          <DialogHeader>
            <DialogTitle className="text-foreground flex items-center gap-2">
              <Hand className="w-4 h-4" />
              AI 审计提示词
            </DialogTitle>
            <DialogDescription className="text-muted-foreground text-sm">
              这里展示真正会交给 AI 的审计 prompt；你也可以复制到 Cursor / ChatGPT 等工具运行，并把审计 JSON 粘贴回来。
            </DialogDescription>
          </DialogHeader>

          <div className="flex-1 overflow-y-auto space-y-4 min-h-0">
            {manualAuditTab === "combined" && (
              <div className="rounded-md border border-primary/20 bg-primary/5 px-3 py-2 text-xs leading-relaxed text-muted-foreground">
                复制完整提示词 → 粘贴到 Cursor 对话窗发送 → 将 Cursor 返回的审计 JSON 粘贴到下方并应用。
              </div>
            )}
            {/* 提示词展示区 */}
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-1">
                  {(["combined", "system", "user"] as const).map((tab) => (
                    <button
                      key={tab}
                      onClick={() => setManualAuditTab(tab)}
                      className={cn(
                        "px-2.5 py-1 rounded text-xs transition-colors",
                        manualAuditTab === tab
                          ? "bg-primary/20 text-primary font-medium"
                          : "text-muted-foreground hover:text-foreground hover:bg-muted"
                      )}
                    >
                      {tab === "combined" ? "完整提示词" : tab === "system" ? "System" : "User"}
                    </button>
                  ))}
                </div>
                <div className="flex items-center gap-1">
                  <Button
                    size="sm" variant="ghost" className="h-7 text-xs gap-1"
                    onClick={() => {
                      const text = manualAuditTab === "combined"
                        ? manualAuditPrompt?.combined
                        : manualAuditTab === "system"
                          ? manualAuditPrompt?.system_prompt
                          : manualAuditPrompt?.user_prompt;
                      text && handleManualAuditCopy(text);
                    }}
                    disabled={!manualAuditPrompt}
                  >
                    <Copy className="w-3 h-3" />
                    复制
                  </Button>
                  <Button
                    size="sm" variant="ghost" className="h-7 text-xs gap-1"
                    onClick={handleManualAuditDownloadAll}
                    disabled={!manualAuditPrompt}
                  >
                    <Download className="w-3 h-3" />
                    一键下载
                  </Button>
                </div>
              </div>
              <div className="rounded-lg border border-border bg-card/60 p-3 h-48 overflow-y-auto">
                {manualAuditLoading ? (
                  <div className="flex items-center justify-center h-full">
                    <Loader2 className="w-4 h-4 animate-spin text-muted-foreground" />
                  </div>
                ) : (
                  <pre className="text-xs text-muted-foreground whitespace-pre-wrap font-mono leading-relaxed">
                    {(manualAuditTab === "combined"
                      ? manualAuditPrompt?.combined
                      : manualAuditTab === "system"
                        ? manualAuditPrompt?.system_prompt
                        : manualAuditPrompt?.user_prompt) || "加载中..."}
                  </pre>
                )}
              </div>
            </div>

            {/* AI 输出粘贴区 */}
            <div className="space-y-2">
              <div className="flex items-center gap-2 text-xs text-muted-foreground">
                <Upload className="w-3 h-3" />
                粘贴 AI 输出结果
                <span className="text-muted-foreground/50">（JSON 数组格式）</span>
              </div>
              <Textarea
                value={manualAuditInput}
                onChange={(e) => setManualAuditInput(e.target.value)}
                placeholder={'粘贴 AI 输出的审计 JSON...\n例: [{"i":1,"a":"k"},{"i":2,"a":"d","r":"P1 重说"}]'}
                className="h-40 bg-secondary border-border text-foreground font-mono text-xs resize-none"
              />
            </div>
          </div>

          <div className="flex justify-end gap-2 mt-2">
            <Button variant="outline" size="sm" onClick={() => setManualAuditDialogOpen(false)}>
              取消
            </Button>
            <Button
              size="sm"
              onClick={handleManualAuditSubmit}
              disabled={manualAuditSubmitting || !manualAuditInput.trim()}
            >
              {manualAuditSubmitting && <Loader2 className="w-3 h-3 mr-1 animate-spin" />}
              应用审计结果
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      {/* ASR 版本切换二次确认弹窗 */}
      <Dialog
        open={switchAsrConfirmVersion !== null}
        onOpenChange={(open) => { if (!open) setSwitchAsrConfirmVersion(null); }}
      >
        <DialogContent className="max-w-sm bg-card border-border">
          <DialogHeader>
            <DialogTitle className="text-sm font-medium text-foreground flex items-center gap-2">
              <AlertCircle className="w-4 h-4 text-amber-400 shrink-0" />
              切换识别版本
            </DialogTitle>
            <DialogDescription className="text-xs text-muted-foreground mt-1 leading-relaxed">
              切换到{" "}
              <span className="font-mono font-semibold text-foreground">v{switchAsrConfirmVersion}</span>{" "}
              后，当前的全部审计决策（保留 / 删除 / 人工修改）将被重置为默认保留状态，操作不可撤销。
            </DialogDescription>
          </DialogHeader>
          <div className="flex justify-end gap-2 mt-2">
            <Button
              variant="outline"
              size="sm"
              className="text-xs"
              onClick={() => setSwitchAsrConfirmVersion(null)}
            >
              取消
            </Button>
            <Button
              size="sm"
              className="text-xs bg-amber-600 hover:bg-amber-500 text-white border-0"
              disabled={asrSwitchingVersion !== null}
              onClick={() => switchAsrConfirmVersion !== null && void doSwitchAsrVersion(switchAsrConfirmVersion)}
            >
              {asrSwitchingVersion !== null && <Loader2 className="w-3 h-3 mr-1 animate-spin" />}
              确认切换
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
