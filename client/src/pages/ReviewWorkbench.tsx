/**
 * GoldenClip Review Workbench
 * Design: 暗金剪辑台 · 编导美学
 * Layout: Left (Video Player) | Center (Subtitle Audit Stream) | Right (Speaker Panel)
 * Bottom: Real-time Console Log
 */

import { useState, useEffect, useRef, useCallback, memo } from "react";
import { useParams, useLocation } from "wouter";
import { Button } from "@/components/ui/button";
import { toast } from "sonner";
import {
  Play, Pause, ArrowLeft, Zap, Scissors, Download,
  ChevronDown, ChevronUp, Loader2, CheckCircle2,
  AlertCircle, RefreshCw, Eye, EyeOff, FileJson,
  Users, Mic, Volume2, VolumeX, SkipBack, SkipForward,
  RotateCcw, Clock, Cpu, Gauge, Trash2, Check, X,
  Pencil, BookOpen, MessageSquare, Sparkles,
  Hand, Copy, Upload, FileDown,
  Undo2, Redo2, Merge, Lightbulb, SlidersHorizontal,
  SplitSquareVertical, Crown, Crop, Image as ImageIcon, Wand2, ClipboardCopy,
} from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import {
  fetchTask, triggerASR, triggerAudit, triggerSegmentOptimize, skipSegmentOptimize, recoverTask, toggleSegment,
  exportFFmpeg, exportJianying, cancelExport, createLogWebSocket,
  exportHighlightClips, deriveRefineTask,
  getVideoUrl, formatTimestamp, formatDuration,
  STATUS_LABELS, STATUS_COLORS, SPEAKER_COLORS,
  switchASRVersion, switchSegOptimVersion, patchSegment, batchSegmentAction, updateSegments, mergeSegmentNext,
  resmooth, resegment, updateClipRange,
  type ResegmentStats,
  getCorrections, submitPromptFeedback, updatePromptContent, getPrompt, adoptStyleUpdate,
  getManualAuditPrompt, submitManualAuditResult,
  suggestGoldenQuotes, saveGoldenQuoteOrder, generateXhsContent, generateCoverTitles, generateCoverImage, getCoverContentPrompts,
  type Task, type TaskStatus, type Segment, type LogMessage, type ASRSnapshot, type AuditSnapshot, type SegOptimSnapshot,
  type CorrectionsResult, type PromptFeedbackResult,
  type ManualPromptPack,
  type SubtitleStyle, type HookConfig,
  type GoldenQuoteCandidate,
  type PreprocStats,
} from "@/lib/api";
import { Textarea } from "@/components/ui/textarea";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { cn } from "@/lib/utils";
import { Input } from "@/components/ui/input";
import { useAppConfig, type AppConfig, type LLMProvider, CLAUDE_MODELS, LLM_PROVIDERS, getModelShortLabel } from "@/hooks/useAppConfig";
import { useUndoHistory } from "@/hooks/useUndoHistory";
import { ModelPickerPopover } from "@/components/ModelPickerPopover";
import {
  Tooltip,
  TooltipTrigger,
  TooltipContent,
} from "@/components/ui/tooltip";
import {
  Accordion,
  AccordionItem,
  AccordionTrigger,
  AccordionContent,
} from "@/components/ui/accordion";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from "@/components/ui/sheet";
import { HighlightClipsPanel } from "@/components/HighlightClipsPanel";

// ============================================================
// 预处理统计面板
// ============================================================
function PreprocStatsPanel({
  stats,
  currentSegCount,
}: {
  stats: PreprocStats;
  currentSegCount?: number;
}) {
  const origSeg = stats.segment_count;
  const hasOptim = currentSegCount !== undefined && currentSegCount !== origSeg;
  const segDelta = hasOptim ? currentSegCount! - origSeg : 0;

  const items: { label: string; value: string | number; color: string; title: string; extra?: React.ReactNode }[] = [
    {
      label: "识别字数",
      value: stats.total_words,
      color: "text-slate-300",
      title: "ASR 识别的总字符数（含标点）",
    },
    {
      label: "语气词",
      value: stats.filler_count,
      color: stats.filler_count > 0 ? "text-amber-400" : "text-slate-500",
      title: `标记为 <FIL> 的语气词数量（嗯/啊/那个等），可在词级切除时删除`,
    },
    {
      label: "结巴",
      value: stats.stutter_count,
      color: stats.stutter_count > 0 ? "text-orange-400" : "text-slate-500",
      title: "标记为 <STU> 的词语重复处数",
    },
    {
      label: "停顿段",
      value: stats.silence_count,
      color: "text-sky-400",
      title: `检测到 ${stats.silence_count} 处停顿，总计 ${stats.silence_total_s.toFixed(1)}s`,
    },
    {
      label: "停顿时长",
      value: `${stats.silence_total_s.toFixed(1)}s`,
      color: "text-sky-300",
      title: "所有停顿段的总时长",
    },
    {
      label: "分段数",
      value: hasOptim ? currentSegCount! : origSeg,
      color: "text-emerald-400",
      title: hasOptim
        ? `段落优化后 ${currentSegCount} 段（ASR 原始 ${origSeg} 段，${segDelta > 0 ? "+" : ""}${segDelta}）`
        : "ASR 切分的片段数量",
      extra: hasOptim ? (
        <span className={cn(
          "text-[9px] font-mono ml-1",
          segDelta > 0 ? "text-emerald-500/70" : segDelta < 0 ? "text-rose-400/70" : "text-slate-500"
        )}>
          原{origSeg}
        </span>
      ) : undefined,
    },
  ];

  if (stats.speaker_count > 1) {
    items.push({
      label: "说话人",
      value: stats.speaker_count,
      color: "text-purple-400",
      title: `检测到 ${stats.speaker_count} 位说话人`,
    });
  }

  return (
    <div className="mb-3 px-3 py-2.5 rounded-lg bg-slate-900/60 border border-slate-700/40">
      <div className="flex items-center gap-1.5 mb-2">
        <Gauge className="w-3 h-3 text-slate-400" />
        <span className="text-[10px] font-medium text-slate-400 uppercase tracking-wider">预处理统计</span>
        {hasOptim && (
          <span className={cn(
            "text-[9px] font-mono px-1 py-0.5 rounded",
            segDelta > 0 ? "bg-emerald-900/40 text-emerald-400" : "bg-rose-900/40 text-rose-400"
          )}>
            段落优化 {segDelta > 0 ? "+" : ""}{segDelta} 段
          </span>
        )}
      </div>
      <div className="grid grid-cols-3 gap-x-4 gap-y-1.5">
        {items.map((item) => (
          <div key={item.label} title={item.title} className="flex items-center justify-between">
            <span className="text-[10px] text-slate-500">{item.label}</span>
            <span className={`text-[11px] font-mono font-semibold ${item.color} flex items-center`}>
              {item.value}
              {item.extra}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ============================================================
// ASR 模型信息配置
// ============================================================
interface WhisperModel {
  id: string;
  name: string;
  desc: string;
  /** 相对于视频时长的处理倍率（值越小越慢）：realtime_factor */
  speedFactor: number;
  accuracy: "低" | "中" | "高" | "极高";
  accuracyColor: string;
  recommended?: boolean;
}

const WHISPER_MODELS: WhisperModel[] = [
  {
    id: "tiny",
    name: "Tiny",
    desc: "超轻量，速度最快，适合快速预览",
    speedFactor: 32,
    accuracy: "低",
    accuracyColor: "text-slate-400",
  },
  {
    id: "base",
    name: "Base",
    desc: "轻量均衡，日常中文识别推荐",
    speedFactor: 16,
    accuracy: "中",
    accuracyColor: "text-blue-400",
    recommended: true,
  },
  {
    id: "small",
    name: "Small",
    desc: "精度更高，适合口音较重场景",
    speedFactor: 8,
    accuracy: "高",
    accuracyColor: "text-cyan-400",
  },
  {
    id: "medium",
    name: "Medium",
    desc: "高精度，耗时较长",
    speedFactor: 4,
    accuracy: "高",
    accuracyColor: "text-cyan-400",
  },
  {
    id: "large-v3",
    name: "Large-v3",
    desc: "最高精度，适合正式发布内容",
    speedFactor: 2,
    accuracy: "极高",
    accuracyColor: "text-primary",
  },
];

/** 将 ISO 时间字符串格式化为人类可读的相对时间或时钟时间 */
function formatAsrTime(isoStr: string): string {
  const date = new Date(isoStr);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffSec = Math.floor(diffMs / 1000);
  const diffMin = Math.floor(diffSec / 60);
  const diffHour = Math.floor(diffMin / 60);
  const isToday =
    date.getFullYear() === now.getFullYear() &&
    date.getMonth() === now.getMonth() &&
    date.getDate() === now.getDate();

  if (diffSec < 60) return "刚刚";
  if (diffMin < 60) return `${diffMin} 分钟前`;
  if (diffHour < 3) return `${diffHour} 小时前`;
  if (isToday) {
    return date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false });
  }
  return date.toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" }) +
    " " +
    date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false });
}

/** 根据视频时长和模型速度系数估算处理时间 */
function estimateTime(durationSec: number, speedFactor: number): string {
  if (durationSec <= 0) return "未知";
  const estimatedSec = durationSec / speedFactor;
  if (estimatedSec < 60) return `约 ${Math.ceil(estimatedSec)} 秒`;
  const mins = Math.ceil(estimatedSec / 60);
  if (mins < 60) return `约 ${mins} 分钟`;
  const hours = Math.floor(mins / 60);
  const remMins = mins % 60;
  return remMins > 0 ? `约 ${hours} 小时 ${remMins} 分钟` : `约 ${hours} 小时`;
}

/** 当前选中的 ASR 配置（backend + model） */
interface AsrSelection {
  backend: "funasr" | "whisper";
  model: string;
  silenceThreshold: number;
  enableDiarization: boolean;
}

function AsrModelDialog({
  open,
  onClose,
  videoDuration,
  selection,
  onSelect,
  onConfirm,
  isProcessing,
  taskType,
}: {
  open: boolean;
  onClose: () => void;
  videoDuration: number;
  selection: AsrSelection;
  onSelect: (s: AsrSelection) => void;
  onConfirm: () => void;
  isProcessing: boolean;
  taskType?: string;
}) {
  const videoMins = Math.round(videoDuration / 60);
  const isFunASR = selection.backend === "funasr";
  const isHighlightReel = taskType === "highlight_reel";
  const [whisperExpanded, setWhisperExpanded] = useState(false);

  // 阈值候选：0.05~0.50 秒，步长 0.05
  const thresholdOptions = [0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50];
  const thresholdLabels: Record<number, string> = {
    0.05: "极紧", 0.10: "很紧", 0.15: "紧+", 0.20: "紧",
    0.25: "中-",  0.30: "适中", 0.35: "中+", 0.40: "松",
    0.45: "松+",  0.50: "很松",
  };

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="bg-card border-border max-w-lg">
        <DialogHeader>
          <DialogTitle className="text-foreground flex items-center gap-2">
            <Mic className="w-4 h-4 text-primary" />
            选择 ASR 识别引擎
          </DialogTitle>
          <DialogDescription className="text-muted-foreground text-xs">
            视频时长约 <span className="text-foreground font-medium">{videoMins} 分钟</span>，以下为各模型在 CPU 上的估算时间
          </DialogDescription>
        </DialogHeader>

        {/* ── FunASR 区块 ── */}
        <div className="mt-1">
          <p className="text-[10px] text-muted-foreground/60 uppercase tracking-wider mb-1.5 px-0.5">
            阿里达摩院 · FunASR
          </p>
          <button
            onClick={() => onSelect({ ...selection, backend: "funasr", model: "paraformer-zh" })}
            className={cn(
              "w-full text-left rounded-lg border px-4 py-3 transition-all duration-150",
              isFunASR
                ? "border-primary bg-primary/10"
                : "border-border bg-secondary/30 hover:border-border/80 hover:bg-secondary/50"
            )}
          >
            <div className="flex items-center gap-3">
              <div className="flex items-center justify-center w-8 h-8 rounded-md bg-background border border-border shrink-0 text-[10px] font-bold text-primary">
                F
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-semibold text-foreground font-mono">Paraformer-zh</span>
                  <span className="text-[11px] font-medium text-primary">精度极高</span>
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-primary/20 text-primary font-medium">推荐</span>
                </div>
                <p className="text-[11px] text-muted-foreground mt-0.5">
                  字符级时间戳（误差 &lt;50ms）· 自动标点 · 中文专用
                </p>
              </div>
              <div className="text-right shrink-0">
                <div className="flex items-center gap-1 justify-end text-muted-foreground">
                  <Clock className="w-3 h-3" />
                  <span className="text-xs font-mono">{estimateTime(videoDuration, 6)}</span>
                </div>
                <p className="text-[10px] text-muted-foreground/50 mt-1">首次下载约 900MB</p>
              </div>
            </div>
          </button>
        </div>

        {/* ── Whisper 区块 ── */}
        <div className="mt-3">
          <button
            onClick={() => setWhisperExpanded((v) => !v)}
            className="w-full flex items-center justify-between px-0.5 mb-1.5 group"
          >
            <p className="text-[10px] text-muted-foreground/60 uppercase tracking-wider group-hover:text-muted-foreground/80 transition-colors">
              OpenAI · Whisper
            </p>
            <div className="flex items-center gap-1 text-[10px] text-muted-foreground/50 group-hover:text-muted-foreground/70 transition-colors">
              {!isFunASR && (
                <span className="text-primary font-medium">
                  {WHISPER_MODELS.find((m) => m.id === selection.model)?.name ?? selection.model}
                </span>
              )}
              <ChevronDown
                className={cn(
                  "w-3 h-3 transition-transform duration-200",
                  whisperExpanded ? "rotate-180" : ""
                )}
              />
            </div>
          </button>
          {whisperExpanded && (
            <div className="space-y-1.5">
              {WHISPER_MODELS.map((model) => {
                const isSelected = !isFunASR && selection.model === model.id;
                const timeStr = estimateTime(videoDuration, model.speedFactor);
                return (
                  <button
                    key={model.id}
                    onClick={() => onSelect({ ...selection, backend: "whisper", model: model.id })}
                    className={cn(
                      "w-full text-left rounded-lg border px-4 py-2.5 transition-all duration-150",
                      isSelected
                        ? "border-primary bg-primary/10"
                        : "border-border bg-secondary/30 hover:border-border/80 hover:bg-secondary/50"
                    )}
                  >
                    <div className="flex items-center gap-3">
                      <div className="flex items-center justify-center w-8 h-8 rounded-md bg-background border border-border shrink-0">
                        <Cpu className="w-3.5 h-3.5 text-muted-foreground" />
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="text-sm font-semibold text-foreground font-mono">{model.name}</span>
                          <span className={cn("text-[11px] font-medium", model.accuracyColor)}>
                            精度{model.accuracy}
                          </span>
                        </div>
                        <p className="text-[11px] text-muted-foreground mt-0.5">{model.desc}</p>
                      </div>
                      <div className="text-right shrink-0">
                        <div className="flex items-center gap-1 justify-end text-muted-foreground">
                          <Clock className="w-3 h-3" />
                          <span className="text-xs font-mono">{timeStr}</span>
                        </div>
                        <div className="flex items-center gap-0.5 justify-end mt-1">
                          {Array.from({ length: 5 }).map((_, i) => {
                            const filled = i < Math.round(Math.log2(model.speedFactor) / Math.log2(32) * 5);
                            return (
                              <div
                                key={i}
                                className={cn(
                                  "w-2 h-1.5 rounded-sm",
                                  filled ? "bg-muted-foreground/60" : "bg-muted-foreground/15"
                                )}
                              />
                            );
                          })}
                          <span className="text-[10px] text-muted-foreground/50 ml-1">速度</span>
                        </div>
                      </div>
                    </div>
                  </button>
                );
              })}
            </div>
          )}
        </div>

        {/* ── 分段阈值配置 ── */}
        {isHighlightReel ? (
          <div className="mt-3 rounded-lg border border-border bg-secondary/20 px-4 py-3">
            <div className="flex items-center justify-between mb-1.5">
              <div className="flex items-center gap-1.5">
                <Gauge className="w-3.5 h-3.5 text-muted-foreground" />
                <span className="text-xs font-medium text-foreground">分段灵敏度</span>
              </div>
              <div className="flex items-center gap-2">
                {selection.silenceThreshold !== 1.0 && (
                  <button
                    onClick={() => onSelect({ ...selection, silenceThreshold: 1.0 })}
                    className="text-[10px] text-primary hover:text-primary/80 transition-colors"
                  >
                    恢复默认
                  </button>
                )}
                <span className="text-xs font-mono text-primary font-semibold">
                  {selection.silenceThreshold.toFixed(2)}s
                </span>
              </div>
            </div>
            <input
              type="range"
              min={0.5}
              max={2.0}
              step={0.1}
              value={selection.silenceThreshold}
              onChange={(e) => onSelect({ ...selection, silenceThreshold: parseFloat(e.target.value) })}
              className="w-full accent-primary h-1.5 cursor-pointer"
            />
            <p className="text-[10px] text-muted-foreground/60 mt-1.5 leading-snug">
              停顿超过此时长时切为新段落。<span className="text-foreground/70">调小→片段更细</span>（AI 可更精准选取片段），<span className="text-foreground/70">调大→段落更粗</span>（处理更快，token 消耗少）。推荐 1.0s。如后续需调整分段粒度，可在跳转后的精修场景里单独设置。
            </p>
          </div>
        ) : (
          <div className="mt-3 rounded-lg border border-border bg-secondary/20 px-4 py-3">
            <div className="flex items-center justify-between mb-2">
              <div className="flex items-center gap-1.5">
                <Gauge className="w-3.5 h-3.5 text-muted-foreground" />
                <span className="text-xs font-medium text-foreground">分段灵敏度</span>
                <span className="text-[10px] text-muted-foreground/60 ml-1">
                  （停顿 &gt; {selection.silenceThreshold}s 时切新段
                  {isFunASR && "，FunASR 同时依标点切分"}）
                </span>
              </div>
              <span className="text-xs font-mono text-primary font-semibold">
                {thresholdLabels[selection.silenceThreshold] ?? `${selection.silenceThreshold}s`}
              </span>
            </div>
            <div className="space-y-1">
              {[thresholdOptions.slice(0, 5), thresholdOptions.slice(5)].map((row, rowIdx) => (
                <div key={rowIdx} className="flex items-center gap-1">
                  {row.map((val) => {
                    const isActive = selection.silenceThreshold === val;
                    return (
                      <button
                        key={val}
                        onClick={() => onSelect({ ...selection, silenceThreshold: val })}
                        className={cn(
                          "flex-1 rounded py-1 flex flex-col items-center transition-all duration-100",
                          isActive
                            ? "bg-primary text-primary-foreground font-semibold"
                            : "bg-background border border-border text-muted-foreground hover:border-primary/60 hover:text-foreground"
                        )}
                      >
                        <span className="text-[10px] font-mono leading-tight">{val.toFixed(2)}</span>
                        <span className="text-[8px] leading-tight opacity-70">{thresholdLabels[val]}</span>
                      </button>
                    );
                  })}
                </div>
              ))}
            </div>
            <p className="text-[10px] text-muted-foreground/50 mt-1.5">
              值越小分段越细（FunASR 推荐 0.20~0.30）
            </p>
          </div>
        )}

        {/* ── 说话人分离开关 ── */}
        <div className="mt-2 rounded-lg border border-border bg-secondary/20 px-4 py-3">
          <label className="flex items-center justify-between cursor-pointer select-none">
            <div className="flex items-center gap-2">
              <Users className="w-3.5 h-3.5 text-muted-foreground" />
              <div>
                <span className="text-xs font-medium text-foreground">说话人分离（CAM++）</span>
                <p className="text-[10px] text-muted-foreground/60 mt-0.5">
                  {selection.enableDiarization
                    ? "识别主播/嘉宾，在字幕卡左侧显示彩色标识。长视频 CPU 推理可能较慢（超时自动跳过）"
                    : "已关闭，所有片段不显示说话人标识。可加快识别速度"}
                </p>
              </div>
            </div>
            <button
              role="switch"
              aria-checked={selection.enableDiarization}
              onClick={() => onSelect({ ...selection, enableDiarization: !selection.enableDiarization })}
              className={cn(
                "relative inline-flex h-5 w-9 shrink-0 rounded-full border-2 border-transparent transition-colors duration-200",
                selection.enableDiarization ? "bg-primary" : "bg-muted-foreground/30"
              )}
            >
              <span
                className={cn(
                  "pointer-events-none inline-block h-4 w-4 rounded-full bg-white shadow-lg transition-transform duration-200",
                  selection.enableDiarization ? "translate-x-4" : "translate-x-0"
                )}
              />
            </button>
          </label>
        </div>

        <div className="flex items-center gap-2 mt-2 pt-3 border-t border-border">
          <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground flex-1">
            <Gauge className="w-3 h-3" />
            <span>估算基于 CPU 推理，有 GPU 时实际速度更快</span>
          </div>
          <Button
            variant="outline"
            size="sm"
            className="h-8 text-xs"
            onClick={onClose}
          >
            取消
          </Button>
          <Button
            size="sm"
            className="h-8 text-xs bg-primary text-primary-foreground hover:bg-primary/90 gap-1.5"
            onClick={onConfirm}
            disabled={isProcessing}
          >
            {isProcessing ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <Mic className="w-3.5 h-3.5" />
            )}
            开始识别
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

// ============================================================
// Highlight Audit Params Dialog
// ============================================================
interface HighlightAuditParams {
  targetDurMin: number;
  maxClips: number;
  clipMinDur: number;
  clipMaxDur: number;
  totalClips: number;
}

function getHighlightAuditDefaults(isOllama: boolean): HighlightAuditParams {
  return { targetDurMin: isOllama ? 6 : 20, maxClips: isOllama ? 2 : 6, clipMinDur: 45, clipMaxDur: 190, totalClips: 10 };
}

const CLIP_DUR_PRESETS = [
  { label: "精短", desc: "30–90s", min: 30, max: 90 },
  { label: "标准", desc: "45–190s", min: 45, max: 190 },
  { label: "深度", desc: "90–300s", min: 90, max: 300 },
] as const;

function HighlightAuditParamsDialog({
  open, onClose, isOllama, params, onChange, onConfirm, isProcessing,
}: {
  open: boolean;
  onClose: () => void;
  isOllama: boolean;
  params: HighlightAuditParams;
  onChange: (p: HighlightAuditParams) => void;
  onConfirm: () => void;
  isProcessing: boolean;
}) {
  const [customDur, setCustomDur] = useState(false);
  const defaults = getHighlightAuditDefaults(isOllama);
  const isModified =
    params.targetDurMin !== defaults.targetDurMin ||
    params.maxClips !== defaults.maxClips ||
    params.clipMinDur !== defaults.clipMinDur ||
    params.clipMaxDur !== defaults.clipMaxDur ||
    params.totalClips !== defaults.totalClips;

  const providerLabel = isOllama ? "Ollama 本地" : "Claude 云端";

  const windowOptions = isOllama
    ? [1, 2, 5, 6, 10, 15]
    : [5, 10, 15, 20, 30, 60];
  const windowDescs: Record<number, string> = isOllama
    ? { 1: "测试", 2: "3B-7B", 5: "14B", 6: "默认", 10: "32B+", 15: "大模型" }
    : { 5: "极短", 10: "省成本", 15: "短视频", 20: "默认", 30: "长视频", 60: "超长" };

  const maxClipsOptions = isOllama ? [1, 2, 3, 4] : [2, 3, 4, 6, 8];
  const totalClipsOptions = [5, 8, 10, 12, 15, 20];

  const activeDurPreset = CLIP_DUR_PRESETS.find(p => p.min === params.clipMinDur && p.max === params.clipMaxDur);

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-w-md bg-card border-border">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-sm font-semibold">
            <Zap className="w-4 h-4 text-primary" />
            AI 审计参数 · {providerLabel}
          </DialogTitle>
        </DialogHeader>

        <div className="space-y-3 py-1">
          {/* 每次分析时长 */}
          <div className="rounded-lg border border-border bg-secondary/20 px-4 py-3">
            <p className="text-xs font-medium text-foreground mb-2">每次分析时长</p>
            <div className="flex gap-1 flex-wrap">
              {windowOptions.map((v) => (
                <button key={v}
                  onClick={() => onChange({ ...params, targetDurMin: v })}
                  className={cn(
                    "flex-1 min-w-[3rem] rounded py-1.5 flex flex-col items-center transition-all",
                    params.targetDurMin === v
                      ? "bg-primary text-primary-foreground font-semibold"
                      : "bg-background border border-border text-muted-foreground hover:border-primary/60 hover:text-foreground"
                  )}
                >
                  <span className="text-[11px] font-mono leading-tight">{v}min</span>
                  <span className="text-[8px] leading-tight opacity-70">{windowDescs[v]}</span>
                </button>
              ))}
            </div>
            <p className="text-[10px] text-muted-foreground/60 mt-1.5 leading-snug">
              调大→上下文完整，选片连贯；调小→单次请求轻量，适合 OpenRouter 免费层等有单次 token 限制的服务商。
            </p>
          </div>

          {/* 每批最多片段数 */}
          <div className="rounded-lg border border-border bg-secondary/20 px-4 py-3">
            <p className="text-xs font-medium text-foreground mb-2">每批最多片段数</p>
            <div className="flex gap-1">
              {maxClipsOptions.map((v) => (
                <button key={v}
                  onClick={() => onChange({ ...params, maxClips: v })}
                  className={cn(
                    "flex-1 rounded py-1.5 text-[11px] font-mono font-medium transition-all",
                    params.maxClips === v
                      ? "bg-primary text-primary-foreground"
                      : "bg-background border border-border text-muted-foreground hover:border-primary/60 hover:text-foreground"
                  )}
                >
                  {v}
                </button>
              ))}
            </div>
            <p className="text-[10px] text-muted-foreground/60 mt-1.5 leading-snug">
              AI 每次分析窗口最多选几个片段。调大→多选，调小→精选。
            </p>
          </div>

          {/* 最终保留总数 */}
          <div className="rounded-lg border border-border bg-secondary/20 px-4 py-3">
            <p className="text-xs font-medium text-foreground mb-2">最终保留总数</p>
            <div className="flex gap-1">
              {totalClipsOptions.map((v) => (
                <button key={v}
                  onClick={() => onChange({ ...params, totalClips: v })}
                  className={cn(
                    "flex-1 rounded py-1.5 text-[11px] font-mono font-medium transition-all",
                    params.totalClips === v
                      ? "bg-primary text-primary-foreground"
                      : "bg-background border border-border text-muted-foreground hover:border-primary/60 hover:text-foreground"
                  )}
                >
                  {v}
                </button>
              ))}
            </div>
            <p className="text-[10px] text-muted-foreground/60 mt-1.5 leading-snug">
              所有批次汇总后，按评分降序保留前 N 个片段。
            </p>
          </div>

          {/* 片段时长范围 */}
          <div className="rounded-lg border border-border bg-secondary/20 px-4 py-3">
            <p className="text-xs font-medium text-foreground mb-2">片段时长范围</p>
            <div className="flex gap-1 mb-2">
              {CLIP_DUR_PRESETS.map((p) => (
                <button key={p.label}
                  onClick={() => { onChange({ ...params, clipMinDur: p.min, clipMaxDur: p.max }); setCustomDur(false); }}
                  className={cn(
                    "flex-1 rounded py-1.5 flex flex-col items-center transition-all",
                    !customDur && activeDurPreset?.label === p.label
                      ? "bg-primary text-primary-foreground font-semibold"
                      : "bg-background border border-border text-muted-foreground hover:border-primary/60 hover:text-foreground"
                  )}
                >
                  <span className="text-[11px] font-medium leading-tight">{p.label}</span>
                  <span className="text-[8px] leading-tight opacity-70">{p.desc}</span>
                </button>
              ))}
              <button
                onClick={() => setCustomDur(v => !v)}
                className={cn(
                  "flex-1 rounded py-1.5 flex flex-col items-center transition-all text-[11px]",
                  customDur
                    ? "bg-primary text-primary-foreground font-semibold"
                    : "bg-background border border-border text-muted-foreground hover:border-primary/60 hover:text-foreground"
                )}
              >
                <span className="leading-tight">自定义</span>
                {!customDur && <span className="text-[8px] leading-tight opacity-70">{params.clipMinDur}–{params.clipMaxDur}s</span>}
              </button>
            </div>
            {customDur && (
              <div className="flex items-center gap-2 mt-1">
                <div className="flex-1">
                  <p className="text-[10px] text-muted-foreground/50 mb-1">最短（秒）</p>
                  <input
                    type="number" min={10} max={300} step={5}
                    value={params.clipMinDur}
                    onChange={(e) => {
                      const v = Math.max(10, Number(e.target.value));
                      onChange({ ...params, clipMinDur: v, clipMaxDur: Math.max(params.clipMaxDur, v + 30) });
                    }}
                    className="w-full rounded border border-border bg-background px-2 py-1 text-xs font-mono text-center"
                  />
                </div>
                <span className="text-muted-foreground/50 text-xs mt-4">–</span>
                <div className="flex-1">
                  <p className="text-[10px] text-muted-foreground/50 mb-1">最长（秒）</p>
                  <input
                    type="number" min={30} max={1800} step={10}
                    value={params.clipMaxDur}
                    onChange={(e) => {
                      const v = Math.max(params.clipMinDur + 30, Number(e.target.value));
                      onChange({ ...params, clipMaxDur: v });
                    }}
                    className="w-full rounded border border-border bg-background px-2 py-1 text-xs font-mono text-center"
                  />
                </div>
              </div>
            )}
            <p className="text-[10px] text-muted-foreground/60 mt-1.5 leading-snug">
              写入 prompt 引导 AI，同时过滤超出范围的建议片段。
            </p>
          </div>
        </div>

        <div className="flex items-center justify-between pt-1">
          <div>
            {isModified && (
              <button
                onClick={() => { onChange(defaults); setCustomDur(false); }}
                className="text-xs text-muted-foreground hover:text-foreground transition-colors"
              >
                恢复默认
              </button>
            )}
          </div>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" className="h-8 text-xs" onClick={onClose}>取消</Button>
            <Button
              size="sm"
              className="h-8 text-xs bg-primary text-primary-foreground hover:bg-primary/90 gap-1.5"
              onClick={onConfirm}
              disabled={isProcessing}
            >
              <Zap className="w-3 h-3" />
              开始审计
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

// ============================================================
// Console Log Entry
// ============================================================
interface ConsoleEntry {
  id: string;
  timestamp: string;
  level: string;
  source: string;
  message: string;
  progress?: number;
}

// 动态省略号组件：循环播放 . .. ... .. .
function AnimatedDots() {
  const frames = [".", "..", "...", "..", "."];
  const [frame, setFrame] = useState(0);

  useEffect(() => {
    const timer = setInterval(() => {
      setFrame((f) => (f + 1) % frames.length);
    }, 350);
    return () => clearInterval(timer);
  }, []);

  return (
    <span className="inline-block w-5 text-left opacity-80">{frames[frame]}</span>
  );
}

// 动态持续时间计时器：显示"已持续 X分Y秒"或"已持续 Y秒"
function ElapsedTimer({ since }: { since: string }) {
  const [elapsed, setElapsed] = useState(() =>
    Math.max(0, Math.floor((Date.now() - new Date(since).getTime()) / 1000))
  );

  useEffect(() => {
    const startMs = new Date(since).getTime();
    const update = () => setElapsed(Math.max(0, Math.floor((Date.now() - startMs) / 1000)));
    update();
    const timer = setInterval(update, 1000);
    return () => clearInterval(timer);
  }, [since]);

  const mins = Math.floor(elapsed / 60);
  const secs = elapsed % 60;
  const text = mins > 0
    ? `已持续 ${mins}分${secs.toString().padStart(2, "0")}秒`
    : `已持续 ${secs}秒`;

  return (
    <span className="console-log text-[10px] text-amber-400/50 font-mono ml-1 shrink-0">
      {text}
    </span>
  );
}

function ConsoleLog({ entries, expanded, onToggle, onClear, isRunning }: {
  entries: ConsoleEntry[];
  expanded: boolean;
  onToggle: () => void;
  onClear?: () => void;
  isRunning?: boolean;
}) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (expanded && bottomRef.current) {
      bottomRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [entries, expanded]);

  const levelColor: Record<string, string> = {
    info: "text-blue-400",
    warn: "text-yellow-400",
    error: "text-red-400",
    success: "text-green-400",
  };

  const sourceColor: Record<string, string> = {
    asr: "text-cyan-400",
    claude: "text-purple-400",
    ffmpeg: "text-orange-400",
    jianying: "text-pink-400",
    system: "text-slate-400",
    golden_quote: "text-amber-400",
  };

  // 最后一条消息是否为"进行中"
  // 满足以下任一条件：①消息以 ... 结尾；②外部明确传入 isRunning=true（任务仍在跑）
  const lastEntry = entries.length > 0 ? entries[entries.length - 1] : null;
  const lastIsInProgress =
    lastEntry &&
    lastEntry.level !== "error" &&
    lastEntry.level !== "success" &&
    (lastEntry.message.endsWith("...") || !!isRunning);

  return (
    <div
      className="border-t border-border bg-background transition-all duration-200"
      style={{ height: expanded ? "180px" : "32px" }}
    >
      {/* Console Header */}
      <div
        className="h-8 flex items-center px-3 gap-2 cursor-pointer hover:bg-secondary/50 transition-colors"
        onClick={onToggle}
      >
        <div className="flex items-center gap-1.5 shrink-0">
          <div className={cn(
            "w-2 h-2 rounded-full",
            lastIsInProgress ? "bg-amber-400 animate-pulse" : "bg-green-500 animate-pulse"
          )} />
          <span className="console-log text-muted-foreground text-[11px] uppercase tracking-wider">Console</span>
        </div>
        {entries.length > 0 && (
          <span className="text-[10px] text-muted-foreground/50 font-mono shrink-0">{entries.length} 条</span>
        )}
        {/* 折叠状态下展示最新一条日志（含动画省略号） */}
        {!expanded && lastEntry && (
          <>
            <span className={cn(
              "console-log text-[11px] truncate flex-1 min-w-0",
              lastIsInProgress ? "text-slate-300" : (levelColor[lastEntry.level] || "text-slate-300")
            )}>
              {lastIsInProgress
                ? <>{lastEntry.message.endsWith("...") ? lastEntry.message.slice(0, -3) : lastEntry.message}<AnimatedDots /></>
                : lastEntry.message
              }
            </span>
            {lastIsInProgress && (
              <ElapsedTimer since={lastEntry.timestamp} />
            )}
          </>
        )}
        <div className="ml-auto flex items-center gap-1 shrink-0">
          {/* 清空历史日志按钮 */}
          {onClear && entries.length > 0 && (
            <button
              onClick={(e) => { e.stopPropagation(); onClear(); }}
              className="text-[10px] text-muted-foreground/40 hover:text-red-400 transition-colors px-1 py-0.5 rounded font-mono"
              title="清空历史日志"
            >
              清空
            </button>
          )}
          {expanded ? <ChevronDown className="w-3 h-3 text-muted-foreground" /> : <ChevronUp className="w-3 h-3 text-muted-foreground" />}
        </div>
      </div>

      {/* Log entries */}
      {expanded && (
        <div className="overflow-y-auto h-[148px] px-3 pb-2">
          {entries.length === 0 ? (
            <p className="console-log text-muted-foreground/40 mt-2">等待任务启动...</p>
          ) : (
            entries.map((entry, idx) => {
              const isLast = idx === entries.length - 1;
              const showAnimDots = isLast && lastIsInProgress;
              // 如果是最后一条且带有动效，去掉末尾已有的 ...（避免重复）
              const displayMsg = showAnimDots && entry.message.endsWith("...")
                ? entry.message.slice(0, -3)
                : entry.message;

              return (
                <div key={entry.id} className="flex items-start gap-2 py-0.5">
                  <span className="console-log text-muted-foreground/40 shrink-0 text-[10px] mt-px">
                    {new Date(entry.timestamp).toLocaleTimeString("zh-CN", { hour12: false })}
                  </span>
                  <span className={cn("console-log text-[11px] shrink-0 w-24 overflow-hidden truncate", sourceColor[entry.source] || "text-slate-400")}>
                    [{entry.source}]
                  </span>
                  <span className={cn("console-log text-[11px]", levelColor[entry.level] || "text-slate-300")}>
                    {displayMsg}
                    {showAnimDots && <AnimatedDots />}
                  </span>
                  {showAnimDots && (
                    <ElapsedTimer since={entry.timestamp} />
                  )}
                  {entry.progress !== undefined && entry.progress !== null && (
                    <span className="console-log text-[10px] text-primary font-mono ml-auto shrink-0">
                      {Math.round(entry.progress * 100)}%
                    </span>
                  )}
                </div>
              );
            })
          )}
          <div ref={bottomRef} />
        </div>
      )}
    </div>
  );
}

// ============================================================
// 解析词内部标签（<STU>、<FIL s=X e=Y>、<FIL>）
// ============================================================
interface ParsedWord {
  text: string;
  isStu: boolean; // 结巴/重复词
  isFil: boolean; // 语气词/填充词
}

function parseWordTag(rawWord: string): ParsedWord {
  let text = rawWord;
  let isStu = false;
  let isFil = false;

  // 剥离 <STU> 前缀
  if (text.startsWith("<STU>")) {
    isStu = true;
    text = text.slice(5);
  }

  // 剥离 <FIL s=... e=...> 或 <FIL> 前缀
  if (text.startsWith("<FIL")) {
    const closeBracket = text.indexOf(">");
    if (closeBracket !== -1) {
      isFil = true;
      text = text.slice(closeBracket + 1);
    }
  }

  return { text, isStu, isFil };
}

// ============================================================
// 时间格式工具
// ============================================================
function secsToInput(s: number): string {
  const m = Math.floor(s / 60);
  const sec = (s % 60).toFixed(3);
  return `${m.toString().padStart(2, "0")}:${sec.padStart(6, "0")}`;
}

function inputToSecs(v: string): number | null {
  const m = v.match(/^(\d+):(\d{2}\.\d{1,3})$/);
  if (!m) return null;
  return parseInt(m[1]) * 60 + parseFloat(m[2]);
}

// ============================================================
// 字级切割：将一个 Segment 按选中字拆成 KEEP/DELETE 子段
// ============================================================
function splitSegmentByWords(
  seg: Segment,
  selectedIndices: Set<number>,
): Segment[] {
  const words = seg.words || [];
  if (words.length === 0) return [seg];

  type Group = { indices: number[]; isSelected: boolean };
  const groups: Group[] = [];

  for (let i = 0; i < words.length; i++) {
    const sel = selectedIndices.has(i);
    if (groups.length === 0 || groups[groups.length - 1].isSelected !== sel) {
      groups.push({ indices: [i], isSelected: sel });
    } else {
      groups[groups.length - 1].indices.push(i);
    }
  }

  if (groups.length <= 1 && groups[0]?.isSelected) {
    return [{ ...seg, action: "delete" as const, user_override: true, reason: "字级删除（全选）" }];
  }

  // 链式衔接：先计算每个 group 的精确切割边界，保证相邻子段无重叠无间隙。
  // boundary[i] 是第 i 组和第 i+1 组之间的切割点。
  // 取"前组最后一个字的 end"与"后组第一个字的 start"的中点，
  // 消除 ASR 字级时间戳重叠/间隙带来的拼接重复。
  const boundaries: number[] = [];
  for (let i = 0; i < groups.length - 1; i++) {
    const prevEnd = words[groups[i].indices[groups[i].indices.length - 1]].end;
    const nextStart = words[groups[i + 1].indices[0]].start;
    boundaries.push((prevEnd + nextStart) / 2);
  }

  return groups.map((g, gi) => {
    const groupWords = g.indices.map((idx) => words[idx]);
    const text = groupWords.map((w) => {
      const { text: t } = parseWordTag(w.word);
      return t;
    }).join("");

    const start = gi === 0 ? seg.start : boundaries[gi - 1];
    const end = gi === groups.length - 1 ? seg.end : boundaries[gi];

    return {
      ...seg,
      id: `${seg.id}_${gi}`,
      start,
      end,
      text,
      words: groupWords,
      action: g.isSelected ? ("delete" as const) : ("keep" as const),
      user_override: true,
      reason: g.isSelected ? "字级删除" : (seg.action === "delete" ? "手动恢复" : seg.reason),
      tagged_text: undefined,
      claude_action: undefined,
      claude_reason: undefined,
    } as Segment;
  });
}

// ============================================================
// 裁断：将一个 Segment 在第 splitAtIndex 个字之前切成两段
// splitAtIndex 最小为 1（第 0 个字之前无意义）
// ============================================================

/** 从 words 中多数投票推算说话人，无数据时回退到 fallback */
function deriveSpeakerFromWords(ws: { speaker?: string }[], fallback?: string): string | undefined {
  const spks = ws.map(w => w.speaker).filter(Boolean) as string[];
  if (spks.length === 0) return fallback;
  return spks.sort((a, b) =>
    spks.filter(s => s === b).length - spks.filter(s => s === a).length
  )[0];
}

function splitSegmentAt(seg: Segment, splitAtIndex: number): [Segment, Segment] {
  const words = seg.words || [];
  const boundary = (words[splitAtIndex - 1].end + words[splitAtIndex].start) / 2;

  const words0 = words.slice(0, splitAtIndex);
  const words1 = words.slice(splitAtIndex);

  const text0 = words0.map((w) => { const { text: t } = parseWordTag(w.word); return t; }).join("");
  const text1 = words1.map((w) => { const { text: t } = parseWordTag(w.word); return t; }).join("");

  const base = {
    ...seg,
    user_override: true,
    tagged_text: undefined,
    claude_action: undefined,
    claude_reason: undefined,
  };

  const s0: Segment = {
    ...base,
    id: `${seg.id}_s0`,
    start: seg.start,
    end: boundary,
    text: text0,
    words: words0,
    speaker: deriveSpeakerFromWords(words0, seg.speaker),
    reason: "裁断（前半）",
  };

  const s1: Segment = {
    ...base,
    id: `${seg.id}_s1`,
    start: boundary,
    end: seg.end,
    text: text1,
    words: words1,
    speaker: deriveSpeakerFromWords(words1, seg.speaker),
    reason: "裁断（后半）",
  };

  return [s0, s1];
}

// ============================================================
// Segment Item
// ============================================================

// 将 hex 颜色转换为带透明度的 rgba 字符串
function hexToRgba(hex: string, alpha: number): string {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return `rgba(${r},${g},${b},${alpha})`;
}

// 将 action 值映射为竖线颜色：保留=绿，删除=红，微修=黄，无数据=近透明
function actionToColor(action: string | null | undefined): string {
  if (action === 'delete') return 'rgba(220,38,38,0.85)';
  if (action === 'keep')   return 'rgba(22,163,74,0.85)';
  if (action === 'subtitle_fix' || action === 'text_fix' || action === 'merge_next')
    return 'rgba(217,119,6,0.85)';
  return 'rgba(255,255,255,0.06)';
}

const SegmentItem = memo(function SegmentItem({
  segment,
  isActive,
  isSelected,
  isLast = false,
  onClick,
  onToggle,
  onCharClick,
  onSeek,
  activeCharTime,
  onEditSave,
  onMergeNext,
  readOnly = false,
  wordEditMode = false,
  selectedWordIndices,
  onWordEditEnter,
  onWordEditExit,
  onWordToggle,
  onWordCommit,
  splitMode = false,
  onSplitEnter,
  onSplitExit,
  onSplitCommit,
  goldenQuoteIndex = -1,
  onEditReason,
  onSetClipStart,
  onSetClipEnd,
  isOutOfClip = false,
}: {
  segment: Segment;
  isActive: boolean;
  isSelected: boolean;
  isLast?: boolean;
  onClick: (e: React.MouseEvent) => void;
  onToggle: (e: React.MouseEvent) => void;
  onCharClick: (time: number) => void;
  onSeek: (time: number) => void;
  activeCharTime: number;
  onEditSave: (segId: string, newText: string, start: number, end: number) => void;
  onMergeNext?: (segId: string) => void;
  readOnly?: boolean;
  wordEditMode?: boolean;
  selectedWordIndices?: Set<number>;
  onWordEditEnter?: (segId: string) => void;
  onWordEditExit?: () => void;
  onWordToggle?: (segId: string, wordIndex: number, shiftKey: boolean) => void;
  onWordCommit?: (segId: string) => void;
  splitMode?: boolean;
  onSplitEnter?: (segId: string) => void;
  onSplitExit?: () => void;
  onSplitCommit?: (segId: string, splitAtIndex: number) => void;
  /** 金句开场序号（-1 表示不是金句）*/
  goldenQuoteIndex?: number;
  /** 点击 reason 标签修改原因 */
  onEditReason?: (segId: string, action: "keep" | "delete") => void;
  /** 将此段起点设置为视频裁剪起点 */
  onSetClipStart?: (segStart: number) => void;
  /** 将此段终点设置为视频裁剪终点 */
  onSetClipEnd?: (segEnd: number) => void;
  /** 此段在裁剪区间之外（仅影响视觉，不禁用交互） */
  isOutOfClip?: boolean;
}) {
  const speakerColor = SPEAKER_COLORS[segment.speaker || "default"] || SPEAKER_COLORS.default;
  const isDelete = segment.action === "delete";
  const isKeep = segment.action === "keep";
  // 字幕层修正：音频保留不剪切，只在字幕显示层去掉重复词（结巴）
  const isSubtitleFix = segment.action === "subtitle_fix";
  // ASR 识别错误纠正：音频保留，字幕显示修正后文本
  const isTextFix = segment.action === "text_fix";
  // 碎段合并：与下一段合并为一张字幕卡
  const isMergeNext = segment.action === "merge_next";
  // 用户手动编辑后（user_override=true），text 已改但 words 仍是旧 ASR 数据，跳过 words 分支直接显示 text
  const hasWords = !segment.user_override && segment.words && segment.words.length > 0;

  // Bar1：AI 建议颜色（claude_action 有值时用 claude_action，否则 fallback 到 action）
  const aiAction = segment.claude_action ?? segment.action;
  const aiBarColor = actionToColor(aiAction);

  // Bar2：人工修改颜色（有人工介入时固定显示黄色，否则近透明）
  const humanModified = !!(
    segment.user_override ||
    (segment.claude_action && segment.action !== segment.claude_action)
  );
  const humanBarColor = humanModified ? 'rgba(234,179,8,0.85)' : 'rgba(255,255,255,0.06)';

  // 片段底色：删除状态用红色，其他状态跟随说话人角色颜色（高透明度）
  const segmentBg = isDelete
    ? "rgba(220,38,38,0.12)"
    : hexToRgba(speakerColor, 0.15);

  const [isEditing, setIsEditing] = useState(false);
  const [editText, setEditText] = useState(segment.text);
  const [editStart, setEditStart] = useState(0);
  const [editEnd, setEditEnd] = useState(0);
  const [startInput, setStartInput] = useState("");
  const [endInput, setEndInput] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (isEditing && textareaRef.current) {
      textareaRef.current.focus();
      textareaRef.current.select();
    }
  }, [isEditing]);

  // 同步外部 text 更新
  useEffect(() => {
    if (!isEditing) setEditText(segment.text);
  }, [segment.text, isEditing]);

  const handleDoubleClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (readOnly) return;
    setEditText(segment.text);
    setEditStart(segment.start);
    setEditEnd(segment.end);
    setStartInput(secsToInput(segment.start));
    setEndInput(secsToInput(segment.end));
    setIsEditing(true);
  };

  const handleEditSave = () => {
    const trimmed = editText.trim();
    const parsedStart = inputToSecs(startInput) ?? editStart;
    const parsedEnd = inputToSecs(endInput) ?? editEnd;
    onEditSave(segment.id, trimmed, parsedStart, parsedEnd);
    setIsEditing(false);
  };

  const handleEditCancel = () => {
    setEditText(segment.text);
    setIsEditing(false);
  };

  const handleEditKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleEditSave();
    } else if (e.key === "Escape") {
      e.preventDefault();
      handleEditCancel();
    }
  };

  const ruleShort: Record<string, string> = {
    "Rule 1: 重说识别": "重说",
    "Rule 2: 残句清理": "残句",
    "Rule 3: 语气词切除": "语气词",
    "Rule 4: 词内去重": "结巴",
    "Rule 5: 语义去重": "语义重复",
    "Rule 6: 句内重复": "句内重复",
    "Rule 7: 问答闭环": "问答",
    "Rule 8: 气口对齐": "气口",
    "手动调整": "手动",
  };

  return (
    <div
      className={cn(
        "group relative rounded-lg px-3 pt-2 pb-2.5 mb-2 cursor-pointer transition-all duration-150",
        isActive ? "ring-1 ring-primary/50" : "",
        isSelected ? "ring-2 ring-blue-500/70 bg-blue-500/10" : "",
        isDelete
          ? "segment-delete"
          : isSubtitleFix
          ? "segment-subtitle-fix"
          : isTextFix
          ? "segment-text-fix"
          : isMergeNext
          ? "segment-merge-next"
          : "segment-keep",
        "hover:brightness-110"
      )}
      style={{ background: segmentBg }}
      onClick={onClick}
      onDoubleClick={handleDoubleClick}
    >
      {/* 区间外内容遮罩：仅对内容区降低透明度，按钮层保持不变 */}
      <div className={isOutOfClip ? "opacity-60" : undefined}>
      {/* Bar1：AI 建议竖线 */}
      <div
        className="absolute left-0 top-0 bottom-0 w-[5px] rounded-l-lg"
        style={{ background: aiBarColor }}
        title={`AI建议：${aiAction}`}
      />

      {/* Bar2：人工修改竖线 */}
      <div
        className="absolute left-[7px] top-0 bottom-0 w-[4px] rounded-l-sm"
        style={{ background: humanBarColor }}
        title={humanModified ? '有人工修改' : '无人工修改'}
      />

      {/* 多选勾选标记 */}
      {isSelected && (
        <div className="absolute top-1.5 right-1.5 w-4 h-4 rounded-full bg-blue-500 flex items-center justify-center z-10">
          <Check className="w-2.5 h-2.5 text-white" />
        </div>
      )}

      {/* 金句开场标识 */}
      {goldenQuoteIndex >= 0 && (
        <div
          className="absolute top-1.5 right-1.5 flex items-center gap-0.5 bg-amber-500/90 text-black text-[10px] font-bold px-1.5 py-0.5 rounded z-10"
          title={`金句开场第 ${goldenQuoteIndex + 1} 个`}
        >
          <Crown className="w-2.5 h-2.5" />
          {goldenQuoteIndex + 1}
        </div>
      )}

      {/* 头部行：角色 · 时间 · 修改标识 */}
      <div className="flex items-center gap-1.5 pl-[14px] mb-1.5">
        {segment.speaker && (
          <span
            className="inline-flex items-center h-[18px] px-1.5 rounded text-[10px] font-bold shrink-0 select-none"
            style={{ background: speakerColor, color: "rgba(0,0,0,0.60)" }}
          >
            {({ spk0: "主播", spk1: "嘉A", spk2: "嘉B", spk3: "嘉C" } as Record<string, string>)[segment.speaker] ?? segment.speaker}
          </span>
        )}
        <span className="timestamp text-[10px]">{formatTimestamp(segment.start)}</span>
        <span className="text-muted-foreground/35 text-[9px] leading-none">→</span>
        <span className="timestamp text-[10px] opacity-55">{formatTimestamp(segment.end)}</span>
        {humanModified && (
          <span className="text-[9px] px-1 py-px rounded-sm bg-amber-500/15 text-amber-400/90 font-medium shrink-0">
            ✏ 已改
          </span>
        )}
        {isOutOfClip && (
          <span className="text-[9px] px-1.5 py-px rounded-sm bg-white/10 text-white/50 font-medium shrink-0 select-none">
            区间外
          </span>
        )}
      </div>

      {/* Text */}
      <div className="flex-1 min-w-0 pl-[14px]">
          {isEditing ? (
            <div className="flex flex-col gap-1.5" onClick={(e) => e.stopPropagation()}>
              {/* 当前播放位置 */}
              <div className="flex items-center gap-1 text-[10px] text-muted-foreground/60">
                <Clock className="w-3 h-3" />
                <span>当前播放:</span>
                <span className="font-mono text-muted-foreground">{secsToInput(activeCharTime)}</span>
              </div>

              {/* 时间编辑行 */}
              <div className="flex flex-col gap-1">
                {/* 起点 */}
                <div className="flex items-center gap-1">
                  <span className="text-[10px] text-muted-foreground w-6 shrink-0">起点</span>
                  <input
                    type="text"
                    value={startInput}
                    onChange={(e) => setStartInput(e.target.value)}
                    onBlur={() => {
                      const parsed = inputToSecs(startInput);
                      if (parsed !== null) setStartInput(secsToInput(parsed));
                      else setStartInput(secsToInput(editStart));
                    }}
                    className="w-24 bg-background border border-border rounded px-1.5 py-0.5 text-[11px] font-mono text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
                  />
                  <button
                    onClick={() => { const t = inputToSecs(startInput); if (t !== null) onSeek(t); }}
                    className="text-[10px] px-1.5 py-0.5 rounded bg-secondary border border-border text-muted-foreground hover:text-foreground transition-colors"
                    title="跳转到该时间"
                  >
                    ▶跳到
                  </button>
                  <button
                    onClick={() => { setStartInput(secsToInput(activeCharTime)); setEditStart(activeCharTime); }}
                    className="text-[10px] px-1.5 py-0.5 rounded bg-secondary border border-border text-muted-foreground hover:text-foreground transition-colors"
                    title="捕获当前播放时间"
                  >
                    ←捕获
                  </button>
                </div>
                {/* 终点 */}
                <div className="flex items-center gap-1">
                  <span className="text-[10px] text-muted-foreground w-6 shrink-0">终点</span>
                  <input
                    type="text"
                    value={endInput}
                    onChange={(e) => setEndInput(e.target.value)}
                    onBlur={() => {
                      const parsed = inputToSecs(endInput);
                      if (parsed !== null) setEndInput(secsToInput(parsed));
                      else setEndInput(secsToInput(editEnd));
                    }}
                    className="w-24 bg-background border border-border rounded px-1.5 py-0.5 text-[11px] font-mono text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
                  />
                  <button
                    onClick={() => { const t = inputToSecs(endInput); if (t !== null) onSeek(t); }}
                    className="text-[10px] px-1.5 py-0.5 rounded bg-secondary border border-border text-muted-foreground hover:text-foreground transition-colors"
                    title="跳转到该时间"
                  >
                    ▶跳到
                  </button>
                  <button
                    onClick={() => { setEndInput(secsToInput(activeCharTime)); setEditEnd(activeCharTime); }}
                    className="text-[10px] px-1.5 py-0.5 rounded bg-secondary border border-border text-muted-foreground hover:text-foreground transition-colors"
                    title="捕获当前播放时间"
                  >
                    ←捕获
                  </button>
                </div>
              </div>

              {/* 文本编辑框 */}
              <textarea
                ref={textareaRef}
                value={editText}
                onChange={(e) => setEditText(e.target.value)}
                onKeyDown={handleEditKeyDown}
                className="w-full bg-background border border-primary/50 rounded px-2 py-1 text-sm text-foreground resize-none focus:outline-none focus:ring-1 focus:ring-primary"
                rows={Math.max(2, editText.split("\n").length)}
              />
              <div className="flex items-center gap-1.5">
                <button
                  onClick={(e) => { e.stopPropagation(); handleEditSave(); }}
                  className="flex items-center gap-1 px-2 py-0.5 rounded bg-green-600/20 border border-green-600/40 text-green-400 hover:bg-green-600/30 text-[11px] font-medium transition-colors"
                  title="确认保存 (Enter)"
                >
                  <Check className="w-3 h-3" />
                  保存
                </button>
                <button
                  onClick={(e) => { e.stopPropagation(); handleEditCancel(); }}
                  className="flex items-center gap-1 px-2 py-0.5 rounded bg-secondary border border-border text-muted-foreground hover:text-foreground text-[11px] transition-colors"
                  title="取消 (Esc)"
                >
                  <X className="w-3 h-3" />
                  取消
                </button>
                <span className="text-[10px] text-muted-foreground/40 ml-auto">Shift+Enter 换行</span>
              </div>
            </div>
          ) : (
            <>
              {/* 字级编辑模式：悬浮确认工具条 */}
              {wordEditMode && selectedWordIndices && selectedWordIndices.size > 0 && (
                <div className="flex items-center gap-2 mb-1.5 px-2 py-1 rounded bg-red-900/30 border border-red-500/30">
                  <span className="text-[11px] text-red-300">
                    已选 {selectedWordIndices.size} 字
                    （约 {(() => {
                      const ws = segment.words || [];
                      let dur = 0;
                      selectedWordIndices.forEach((idx) => { if (ws[idx]) dur += ws[idx].end - ws[idx].start; });
                      return dur.toFixed(2);
                    })()}秒）
                  </span>
                  <div className="flex items-center gap-1 ml-auto">
                    <button
                      onClick={(e) => { e.stopPropagation(); onWordCommit?.(segment.id); }}
                      className="flex items-center gap-1 px-2 py-0.5 rounded bg-red-600/30 border border-red-500/40 text-red-300 hover:bg-red-600/50 text-[11px] font-medium transition-colors"
                    >
                      <Scissors className="w-3 h-3" />
                      确认删除
                    </button>
                    <button
                      onClick={(e) => { e.stopPropagation(); onWordEditExit?.(); }}
                      className="flex items-center gap-1 px-2 py-0.5 rounded bg-secondary border border-border text-muted-foreground hover:text-foreground text-[11px] transition-colors"
                    >
                      <X className="w-3 h-3" />
                      取消
                    </button>
                  </div>
                </div>
              )}
              {/* 裁断模式：提示条 */}
              {splitMode && (
                <div className="flex items-center gap-2 mb-1.5 px-2 py-1 rounded bg-green-900/30 border border-green-500/30">
                  <SplitSquareVertical className="w-3.5 h-3.5 text-green-400 shrink-0" />
                  <span className="text-[11px] text-green-300">点击文字 — 将在所点位置之前裁断为两段</span>
                  <button
                    onClick={(e) => { e.stopPropagation(); onSplitExit?.(); }}
                    className="flex items-center gap-1 px-2 py-0.5 rounded bg-secondary border border-border text-muted-foreground hover:text-foreground text-[11px] transition-colors ml-auto"
                  >
                    <X className="w-3 h-3" />
                    取消
                  </button>
                </div>
              )}
              <p className={cn(
                "text-sm leading-relaxed break-all",
                isDelete ? "text-foreground/85 line-through decoration-red-400/70" : "text-foreground"
              )}>
                {hasWords
                  ? segment.words!.map((w, i) => {
                      const { text: displayText, isStu, isFil } = parseWordTag(w.word);
                      const isWordSelected = wordEditMode && selectedWordIndices?.has(i);
                      const isActiveChar =
                        !isDelete &&
                        !wordEditMode &&
                        activeCharTime >= w.start &&
                        activeCharTime < w.end;

                      const tagHint = [
                        isStu ? "⚡结巴" : "",
                        isFil ? "💬语气词" : "",
                      ].filter(Boolean).join(" · ");
                      const titleStr = [
                        tagHint,
                        `${formatTimestamp(w.start)} → ${formatTimestamp(w.end)}`,
                      ].filter(Boolean).join(" · ");

                      // 词级说话人颜色：当词的 speaker 与本段 speaker 不同时，加下划线着色标出插话者
                      const wordSpeakerColor =
                        !wordEditMode &&
                        !splitMode &&
                        !isDelete &&
                        w.speaker &&
                        w.speaker !== segment.speaker
                          ? SPEAKER_COLORS[w.speaker] || SPEAKER_COLORS.default
                          : undefined;

                      // 裁断模式：index=0 不可切（切出空段），index>=1 可作为切点
                      const isSplitCandidate = splitMode && i >= 1;

                      return (
                        <span
                          key={`${w.start}-${i}`}
                          onClick={(e) => {
                            e.stopPropagation();
                            if (splitMode) {
                              if (isSplitCandidate) onSplitCommit?.(segment.id, i);
                            } else if (wordEditMode) {
                              onWordToggle?.(segment.id, i, e.shiftKey);
                            } else {
                              onCharClick(w.start);
                            }
                          }}
                          title={splitMode
                            ? (isSplitCandidate ? `在此字之前裁断 (${formatTimestamp(w.start)})` : "首字不可裁断")
                            : wordSpeakerColor ? `[${w.speaker}] ${titleStr}` : titleStr}
                          style={wordSpeakerColor ? {
                            color: wordSpeakerColor,
                            textDecorationColor: wordSpeakerColor,
                            textDecorationLine: "underline",
                            textDecorationStyle: "dotted",
                            textUnderlineOffset: "3px",
                          } : undefined}
                          className={cn(
                            "rounded-sm transition-colors duration-75",
                            splitMode
                              ? cn(
                                  "select-none",
                                  isSplitCandidate
                                    ? "cursor-pointer border-l-2 border-transparent hover:border-green-400 hover:bg-green-500/20"
                                    : "cursor-not-allowed opacity-50"
                                )
                              : wordEditMode
                              ? cn(
                                  "cursor-pointer select-none",
                                  isWordSelected
                                    ? "bg-red-500/30 text-red-300 line-through decoration-red-400/70"
                                    : "hover:bg-primary/20"
                                )
                              : cn(
                                  isDelete
                                    ? "cursor-default"
                                    : "cursor-pointer hover:bg-primary/20",
                                  isStu && !isDelete && "text-orange-400/80 line-through decoration-orange-400/60",
                                  isFil && !isDelete && !isStu && "text-muted-foreground/50",
                                  isActiveChar && "bg-primary/40 text-primary font-medium"
                                )
                          )}
                        >
                          {displayText}
                        </span>
                      );
                    })
                  : segment.text
                }
              </p>

              {/* subtitle_fix: 字幕去重预览（结巴） */}
              {isSubtitleFix && (segment as any).display_text && (
                <div className="mt-1.5 flex items-start gap-1.5">
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-900/40 text-amber-300 font-mono shrink-0 whitespace-nowrap">
                    字幕去重
                  </span>
                  <span className="text-[11px] text-amber-200/90 leading-snug">
                    {(segment as any).display_text}
                  </span>
                </div>
              )}

              {/* text_fix: ASR 纠错预览 */}
              {isTextFix && (segment as any).display_text && (
                <div className="mt-1.5 flex items-start gap-1.5">
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-sky-900/40 text-sky-300 font-mono shrink-0 whitespace-nowrap">
                    ASR纠错
                  </span>
                  <span className="text-[11px] text-sky-200/90 leading-snug">
                    {(segment as any).display_text}
                  </span>
                </div>
              )}

              {/* merge_next: 合并预览提示 */}
              {isMergeNext && (
                <div className="mt-1.5 flex items-start gap-1.5">
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-violet-900/40 text-violet-300 font-mono shrink-0 whitespace-nowrap">
                    ↓ 合并下段
                  </span>
                  <span className="text-[11px] text-violet-200/80 leading-snug">
                    将与下一段合并为一张字幕卡
                  </span>
                </div>
              )}

              {/* subtitle_line_break 分行预览 */}
              {!isDelete && (segment as any).subtitle_line_break && (
                <div className="mt-1.5 flex items-start gap-1.5">
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-teal-900/40 text-teal-300 font-mono shrink-0 whitespace-nowrap">
                    ¶ 分行
                  </span>
                  <span className="text-[11px] text-teal-200/80 leading-snug">
                    "{(segment as any).subtitle_line_break}" 起换第二张字幕卡
                  </span>
                </div>
              )}

              {/* Reason + Rule */}
              {segment.reason && (
                <div className="flex items-start gap-1.5 mt-1 flex-wrap">
                  {segment.rule && (
                    <span className={cn(
                      "text-[10px] px-1.5 py-0.5 rounded font-mono shrink-0",
                      isDelete
                        ? "bg-red-900/40 text-red-300"
                        : isTextFix
                        ? "bg-sky-900/30 text-sky-400"
                        : isMergeNext
                        ? "bg-violet-900/30 text-violet-400"
                        : isKeep
                        ? "bg-green-900/40 text-green-400"
                        : "bg-amber-900/30 text-amber-400"
                    )}>
                      {ruleShort[segment.rule] || segment.rule}
                    </span>
                  )}
                  {/* reason 标签：user_override 段落可点击修改 */}
                  {!readOnly && (isKeep || isDelete) && onEditReason ? (
                    <button
                      className={cn(
                        "group/reason flex items-center gap-1 text-[10px] leading-tight rounded px-1 -ml-1 transition-colors",
                        isKeep
                          ? "text-green-300 hover:bg-green-900/30"
                          : "text-amber-100 hover:bg-amber-900/20"
                      )}
                      onClick={(e) => {
                        e.stopPropagation();
                        onEditReason(segment.id, isKeep ? "keep" : "delete");
                      }}
                      title="点击修改原因"
                    >
                      {segment.reason}
                      <Pencil className="w-2.5 h-2.5 opacity-0 group-hover/reason:opacity-60 transition-opacity shrink-0" />
                    </button>
                  ) : (
                    <span className={cn(
                      "text-[10px] leading-tight",
                      isKeep ? "text-green-300" : "text-amber-100"
                    )}>{segment.reason}</span>
                  )}
                </div>
              )}
            </>
          )}
        </div>

      </div>{/* end 区间外内容遮罩 */}

        {/* 操作按钮区（readOnly 时隐藏，hover 时显示在右上角） */}
        {!readOnly && !isSelected && (
          <div className="absolute top-1.5 right-1.5 flex items-center gap-0.5 z-10 opacity-0 group-hover:opacity-100 transition-opacity duration-75">
            {/* 编辑按钮 */}
            <Tooltip>
              <TooltipTrigger asChild>
                <button
                  className="w-6 h-6 rounded flex items-center justify-center bg-blue-900/40 text-blue-400 hover:bg-blue-900/60 transition-colors"
                  onClick={(e) => { e.stopPropagation(); handleDoubleClick(e); }}
                >
                  <Pencil className="w-3 h-3" />
                </button>
              </TooltipTrigger>
              <TooltipContent side="top" sideOffset={4}>编辑文本</TooltipContent>
            </Tooltip>
            {/* 字级删除按钮：仅 words 非空且非整段删除时显示 */}
            {hasWords && !isDelete && (
              <Tooltip>
                <TooltipTrigger asChild>
                  <button
                    className={cn(
                      "w-6 h-6 rounded flex items-center justify-center transition-colors",
                      wordEditMode
                        ? "bg-red-900/60 text-red-300 ring-1 ring-red-500/50"
                        : "bg-orange-900/40 text-orange-400 hover:bg-orange-900/60"
                    )}
                    onClick={(e) => {
                      e.stopPropagation();
                      if (wordEditMode) { onWordEditExit?.(); } else { onWordEditEnter?.(segment.id); }
                    }}
                  >
                    <Scissors className="w-3 h-3" />
                  </button>
                </TooltipTrigger>
                <TooltipContent side="top" sideOffset={4}>{wordEditMode ? "退出字级删除" : "字级删除"}</TooltipContent>
              </Tooltip>
            )}
            {/* 裁断按钮：仅 words >= 2 且非整段删除时显示 */}
            {hasWords && (segment.words?.length ?? 0) >= 2 && !isDelete && (
              <Tooltip>
                <TooltipTrigger asChild>
                  <button
                    className={cn(
                      "w-6 h-6 rounded flex items-center justify-center transition-colors",
                      splitMode
                        ? "bg-green-900/60 text-green-300 ring-1 ring-green-500/50"
                        : "bg-green-900/40 text-green-400 hover:bg-green-900/60"
                    )}
                    onClick={(e) => {
                      e.stopPropagation();
                      if (splitMode) { onSplitExit?.(); } else { onSplitEnter?.(segment.id); }
                    }}
                  >
                    <SplitSquareVertical className="w-3 h-3" />
                  </button>
                </TooltipTrigger>
                <TooltipContent side="top" sideOffset={4}>{splitMode ? "退出裁断" : "裁断句段"}</TooltipContent>
              </Tooltip>
            )}
            {/* 合并下段按钮 */}
            {!isLast && (
              <Tooltip>
                <TooltipTrigger asChild>
                  <button
                    className="w-6 h-6 rounded flex items-center justify-center bg-violet-900/40 text-violet-400 hover:bg-violet-900/60 transition-colors"
                    onClick={(e) => { e.stopPropagation(); onMergeNext?.(segment.id); }}
                  >
                    <Merge className="w-3 h-3" />
                  </button>
                </TooltipTrigger>
                <TooltipContent side="top" sideOffset={4}>合并下一段</TooltipContent>
              </Tooltip>
            )}
            {/* 设置为视频起点/终点 */}
            {!readOnly && onSetClipStart && (
              <>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <button
                      className="w-6 h-6 rounded flex items-center justify-center bg-amber-900/40 text-amber-400 hover:bg-amber-900/60 transition-colors"
                      onClick={(e) => { e.stopPropagation(); onSetClipStart(segment.start); }}
                    >
                      <SkipBack className="w-3 h-3" />
                    </button>
                  </TooltipTrigger>
                  <TooltipContent side="top" sideOffset={4}>设置为视频起点</TooltipContent>
                </Tooltip>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <button
                      className="w-6 h-6 rounded flex items-center justify-center bg-amber-900/40 text-amber-400 hover:bg-amber-900/60 transition-colors"
                      onClick={(e) => { e.stopPropagation(); onSetClipEnd?.(segment.end); }}
                    >
                      <SkipForward className="w-3 h-3" />
                    </button>
                  </TooltipTrigger>
                  <TooltipContent side="top" sideOffset={4}>设置为视频终点</TooltipContent>
                </Tooltip>
              </>
            )}
            {/* 保留/删除 Toggle */}
            <Tooltip>
              <TooltipTrigger asChild>
                <button
                  className={cn(
                    "w-6 h-6 rounded flex items-center justify-center transition-colors",
                    isDelete
                      ? "bg-green-900/40 text-green-400 hover:bg-green-900/60"
                      : "bg-red-900/40 text-red-400 hover:bg-red-900/60"
                  )}
                  onClick={onToggle}
                >
                  {isDelete ? <Eye className="w-3.5 h-3.5" /> : <EyeOff className="w-3.5 h-3.5" />}
                </button>
              </TooltipTrigger>
              <TooltipContent side="top" sideOffset={4}>{isDelete ? "恢复保留" : "标记删除"}</TooltipContent>
            </Tooltip>
          </div>
        )}

      {/* Claude 决策差异徽章 */}
      {(segment as any).claude_action &&
        segment.action !== (segment as any).claude_action && (
        <div className="absolute top-1 left-1/2 -translate-x-1/2 z-10">
          {(() => {
            const ca = (segment as any).claude_action as string;
            const ua = segment.action as string;
            const actionLabel = (a: string) =>
              ({ keep: "保留", delete: "删除", subtitle_fix: "字幕去重", text_fix: "纠错", merge_next: "合并" }[a] || a);
            const isUserRestore = ca === "delete" && ua !== "delete";
            return (
              <span
                className={cn(
                  "text-[9px] px-1.5 py-0.5 rounded-full font-medium whitespace-nowrap",
                  isUserRestore
                    ? "bg-blue-500/20 text-blue-300 border border-blue-500/30"
                    : "bg-orange-500/20 text-orange-300 border border-orange-500/30"
                )}
                title={`Claude: ${actionLabel(ca)} → 你: ${actionLabel(ua)}`}
              >
                {isUserRestore ? "↑ 你恢复" : "↓ 你删除"}
              </span>
            );
          })()}
        </div>
      )}

      {/* Duration */}
      <div className="absolute bottom-1.5 right-2 pointer-events-none">
        <span className="text-[10px] font-mono text-muted-foreground/40">
          {formatDuration(segment.end - segment.start)}
        </span>
      </div>
    </div>
  );
}, (prev, next) =>
  prev.segment === next.segment &&
  prev.isActive === next.isActive &&
  prev.isSelected === next.isSelected &&
  prev.isLast === next.isLast &&
  prev.activeCharTime === next.activeCharTime &&
  prev.readOnly === next.readOnly &&
  prev.wordEditMode === next.wordEditMode &&
  prev.selectedWordIndices === next.selectedWordIndices &&
  prev.isOutOfClip === next.isOutOfClip
);

// ============================================================
// Clip Range Bar — 视频裁剪区间（In/Out Point）
// ============================================================
function ClipRangeBar({
  taskId,
  videoDuration,
  clipStart,
  clipEnd,
  onApplied,
  onSeek,
}: {
  taskId: string;
  videoDuration: number;
  clipStart: number | null | undefined;
  clipEnd: number | null | undefined;
  onApplied: () => void;
  onSeek: (time: number) => void;
}) {
  const [startSecs, setStartSecs] = useState<number>(clipStart ?? 0);
  const [endSecs, setEndSecs] = useState<number>(clipEnd ?? videoDuration);
  const [startInput, setStartInput] = useState<string>(secsToInput(clipStart ?? 0));
  const [endInput, setEndInput] = useState<string>(secsToInput(clipEnd ?? videoDuration));
  const trackRef = useRef<HTMLDivElement>(null);
  const draggingRef = useRef<"start" | "end" | null>(null);

  // 持有最新值以便拖拽闭包中读取（避免陈旧闭包）
  const startSecsRef = useRef(startSecs);
  const endSecsRef = useRef(endSecs);
  const onSeekRef = useRef(onSeek);
  const taskIdRef = useRef(taskId);
  const videoDurationRef = useRef(videoDuration);
  const onAppliedRef = useRef(onApplied);
  useEffect(() => { startSecsRef.current = startSecs; }, [startSecs]);
  useEffect(() => { endSecsRef.current = endSecs; }, [endSecs]);
  useEffect(() => { onSeekRef.current = onSeek; }, [onSeek]);
  useEffect(() => { taskIdRef.current = taskId; }, [taskId]);
  useEffect(() => { videoDurationRef.current = videoDuration; }, [videoDuration]);
  useEffect(() => { onAppliedRef.current = onApplied; }, [onApplied]);

  // 外部 clipStart/clipEnd 变化时同步（刷新任务后）
  useEffect(() => {
    const v = clipStart ?? 0;
    setStartSecs(v);
    setStartInput(secsToInput(v));
  }, [clipStart]);
  useEffect(() => {
    const v = clipEnd ?? videoDuration;
    setEndSecs(v);
    setEndInput(secsToInput(v));
  }, [clipEnd, videoDuration]);

  // 自动保存辅助函数：在松手/失焦时调用
  async function autoSave(cs: number, ce: number) {
    const dur = videoDurationRef.current;
    try {
      await updateClipRange(
        taskIdRef.current,
        cs > 0 ? cs : null,
        ce < dur ? ce : null,
      );
      onAppliedRef.current();
    } catch {
      toast.error("保存裁剪区间失败");
    }
  }

  // 全局拖拽监听：mousemove 实时更新视图，mouseup 松手后自动保存
  useEffect(() => {
    function onMove(e: MouseEvent) {
      if (!draggingRef.current || !trackRef.current) return;
      const rect = trackRef.current.getBoundingClientRect();
      const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
      const t = ratio * videoDurationRef.current;
      if (draggingRef.current === "start") {
        const v = Math.max(0, Math.min(t, endSecsRef.current - 0.5));
        setStartSecs(v);
        setStartInput(secsToInput(v));
        onSeekRef.current(v);
      } else {
        const v = Math.min(videoDurationRef.current, Math.max(t, startSecsRef.current + 0.5));
        setEndSecs(v);
        setEndInput(secsToInput(v));
        onSeekRef.current(v);
      }
    }
    function onUp() {
      if (!draggingRef.current) return;
      draggingRef.current = null;
      // 松手后自动保存当前区间
      autoSave(startSecsRef.current, endSecsRef.current);
    }
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
    return () => {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const totalDur = videoDuration || 1;
  const leftPct = Math.max(0, Math.min(100, (startSecs / totalDur) * 100));
  const rightPct = Math.max(0, Math.min(100, (endSecs / totalDur) * 100));

  const isActive = startSecs > 0 || endSecs < videoDuration;

  async function handleClear() {
    try {
      await updateClipRange(taskId, null, null);
      setStartSecs(0);
      setEndSecs(videoDuration);
      setStartInput("00:00.000");
      setEndInput(secsToInput(videoDuration));
      toast.success("裁剪区间已清除");
      onApplied();
    } catch {
      toast.error("清除裁剪区间失败");
    }
  }

  return (
    <div className="space-y-1.5">
      {/* 双柄滑块轨道 */}
      <div ref={trackRef} className="relative h-5 flex items-center select-none">
        <div className="absolute left-0 right-0 h-1 bg-secondary rounded-full" />
        {leftPct > 0 && (
          <div
            className="absolute h-1 bg-black/50 rounded-l-full"
            style={{ left: 0, width: `${leftPct}%` }}
          />
        )}
        <div
          className="absolute h-1 bg-amber-400 rounded-full"
          style={{ left: `${leftPct}%`, width: `${rightPct - leftPct}%` }}
        />
        {rightPct < 100 && (
          <div
            className="absolute h-1 bg-black/50 rounded-r-full"
            style={{ left: `${rightPct}%`, width: `${100 - rightPct}%` }}
          />
        )}
        <div
          className="absolute w-4 h-4 rounded-full bg-amber-400 border-2 border-amber-200 cursor-grab shadow-md z-10 hover:scale-110 transition-transform"
          style={{ left: `${leftPct}%`, transform: "translateX(-50%)" }}
          onMouseDown={(e) => { e.preventDefault(); draggingRef.current = "start"; }}
        />
        <div
          className="absolute w-4 h-4 rounded-full bg-amber-400 border-2 border-amber-200 cursor-grab shadow-md z-10 hover:scale-110 transition-transform"
          style={{ left: `${rightPct}%`, transform: "translateX(-50%)" }}
          onMouseDown={(e) => { e.preventDefault(); draggingRef.current = "end"; }}
        />
      </div>

      {/* 时间输入行 */}
      <div className="flex items-center gap-1.5">
        <div className="flex items-center gap-1">
          <span className="text-[10px] text-muted-foreground w-6">从</span>
          <input
            className="w-24 px-1.5 py-0.5 text-[11px] font-mono bg-secondary border border-border rounded text-foreground focus:outline-none focus:border-amber-400/60"
            value={startInput}
            onChange={(e) => setStartInput(e.target.value)}
            onBlur={() => {
              const v = inputToSecs(startInput);
              if (v != null) {
                const clamped = Math.max(0, Math.min(v, endSecs - 0.5));
                setStartSecs(clamped);
                setStartInput(secsToInput(clamped));
                onSeek(clamped);
                autoSave(clamped, endSecs);
              } else {
                setStartInput(secsToInput(startSecs));
              }
            }}
            placeholder="00:00.000"
          />
        </div>
        <div className="flex items-center gap-1">
          <span className="text-[10px] text-muted-foreground w-6">到</span>
          <input
            className="w-24 px-1.5 py-0.5 text-[11px] font-mono bg-secondary border border-border rounded text-foreground focus:outline-none focus:border-amber-400/60"
            value={endInput}
            onChange={(e) => setEndInput(e.target.value)}
            onBlur={() => {
              const v = inputToSecs(endInput);
              if (v != null) {
                const clamped = Math.min(videoDuration, Math.max(v, startSecs + 0.5));
                setEndSecs(clamped);
                setEndInput(secsToInput(clamped));
                onSeek(clamped);
                autoSave(startSecs, clamped);
              } else {
                setEndInput(secsToInput(endSecs));
              }
            }}
            placeholder="mm:ss.SSS"
          />
        </div>
        {isActive && (
          <button
            onClick={handleClear}
            className="ml-auto text-[10px] px-2 py-0.5 rounded border border-border text-muted-foreground hover:text-foreground hover:border-foreground/40 transition-colors"
          >
            清除
          </button>
        )}
      </div>
    </div>
  );
}

// ============================================================
// Video Player
// ============================================================
function VideoPlayer({
  taskId,
  currentTime,
  onTimeUpdate,
  segments,
  playKeptOnly,
  onPlayKeptOnlyChange,
  clipStart,
  clipEnd,
  videoUrl,
  previewMode,
  onExitPreview,
  onPreviewEnded,
}: {
  taskId: string;
  currentTime: number;
  onTimeUpdate: (t: number) => void;
  segments: Segment[];
  playKeptOnly: boolean;
  onPlayKeptOnlyChange: (v: boolean) => void;
  clipStart?: number | null;
  clipEnd?: number | null;
  videoUrl?: string | null;
  previewMode?: boolean;
  onExitPreview?: () => void;
  onPreviewEnded?: () => void;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [playing, setPlaying] = useState(false);
  const [muted, setMuted] = useState(false);
  const [duration, setDuration] = useState(0);
  const [localTime, setLocalTime] = useState(0);
  const rafRef = useRef<number | null>(null);
  const onTimeUpdateRef = useRef(onTimeUpdate);
  const segmentsRef = useRef(segments);
  const playKeptOnlyRef = useRef(playKeptOnly);
  const previewModeRef = useRef(!!previewMode);
  // 标记是否为程序性 seek（跳过删除片段），避免误触 onPause → stopRaf
  const programmaticSeekRef = useRef(false);

  useEffect(() => { segmentsRef.current = segments; }, [segments]);
  useEffect(() => { playKeptOnlyRef.current = playKeptOnly; }, [playKeptOnly]);
  useEffect(() => { previewModeRef.current = !!previewMode; }, [previewMode]);

  // 始终持有最新的 onTimeUpdate，避免 rAF 闭包过期
  useEffect(() => {
    onTimeUpdateRef.current = onTimeUpdate;
  }, [onTimeUpdate]);

  // rAF 循环：播放时以 ~60fps 精度驱动父组件 currentTime，解决 onTimeUpdate 250ms 延迟问题
  const startRaf = useCallback(() => {
    if (rafRef.current !== null) return;
    const tick = () => {
      if (videoRef.current) {
        const vt = videoRef.current.currentTime;
        if (!previewModeRef.current && playKeptOnlyRef.current && !programmaticSeekRef.current) {
          const LOOKAHEAD = 0.15;
          const CHAIN_GAP = 3.5;
          const deleteSeg = segmentsRef.current.find(
            (s) => s.action === "delete" && vt >= s.start - LOOKAHEAD && vt < s.end
          );
          if (deleteSeg) {
            // 链式跳过：将间隔 < CHAIN_GAP 的连续删除片段合并为一次 seek
            let targetEnd = deleteSeg.end;
            let chainSeg = segmentsRef.current.find(
              (s) => s.action === "delete" && s.start <= targetEnd + CHAIN_GAP && s.end > targetEnd
            );
            while (chainSeg) {
              targetEnd = chainSeg.end;
              chainSeg = segmentsRef.current.find(
                (s) => s.action === "delete" && s.start <= targetEnd + CHAIN_GAP && s.end > targetEnd
              );
            }
            programmaticSeekRef.current = true;
            // 停止 rAF 循环，让浏览器专注关键帧解码；onSeeked 会恢复循环
            if (rafRef.current !== null) { cancelAnimationFrame(rafRef.current); rafRef.current = null; }
            const target = targetEnd + 0.05;
            videoRef.current.currentTime = target;
            return;
          }
        }
        onTimeUpdateRef.current(vt);
      }
      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
  }, []);

  const stopRaf = useCallback(() => {
    if (rafRef.current !== null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
  }, []);

  // 组件卸载时清理 rAF
  useEffect(() => () => stopRaf(), [stopRaf]);

  // Seek when currentTime changes externally
  useEffect(() => {
    if (!videoRef.current) return;
    if (programmaticSeekRef.current) return;
    const diff = Math.abs(videoRef.current.currentTime - currentTime);
    if (diff > 0.5) {
      videoRef.current.currentTime = currentTime;
    }
  }, [currentTime]);

  const togglePlay = useCallback(() => {
    if (!videoRef.current) return;
    if (playing) videoRef.current.pause();
    else videoRef.current.play();
  }, [playing]);

  // 空格键：播放/暂停，并阻止浏览器默认向下翻页
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== " " && e.code !== "Space") return;
      const target = e.target as HTMLElement;
      if (target.closest("input, textarea, select, [contenteditable=true]")) return;
      e.preventDefault();
      togglePlay();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [togglePlay]);

  const skip = (seconds: number) => {
    if (videoRef.current) {
      videoRef.current.currentTime = Math.max(0, videoRef.current.currentTime + seconds);
    }
  };

  const progressPercent = duration > 0 ? (localTime / duration) * 100 : 0;

  return (
    <div className="flex flex-col h-full bg-black rounded-xl overflow-hidden">
      {/* Video */}
      <div className="flex-1 relative bg-black flex items-center justify-center">
        <video
          ref={videoRef}
          src={videoUrl || getVideoUrl(taskId)}
          preload="auto"
          className="max-w-full max-h-full object-contain"
          onTimeUpdate={(e) => {
            // 只更新进度条显示，字幕高亮由 rAF 以 60fps 驱动
            setLocalTime(e.currentTarget.currentTime);
          }}
          onDurationChange={(e) => setDuration(e.currentTarget.duration)}
          onPlay={() => { setPlaying(true); startRaf(); }}
          onPause={() => {
            if (programmaticSeekRef.current) return;
            setPlaying(false); stopRaf();
          }}
          onEnded={() => {
            setPlaying(false);
            stopRaf();
            if (previewMode) onPreviewEnded?.();
          }}
          onSeeked={() => {
            if (programmaticSeekRef.current) {
              programmaticSeekRef.current = false;
              startRaf();
            }
          }}
        />
      </div>

      {/* Controls */}
      <div className="bg-card px-3 py-2 space-y-1.5">
        {/* Progress bar（含裁剪区间蒙层） */}
        <div
          className="relative h-2 bg-secondary rounded-full overflow-hidden cursor-pointer"
          onClick={(e) => {
            if (!videoRef.current || !duration) return;
            const rect = e.currentTarget.getBoundingClientRect();
            const ratio = (e.clientX - rect.left) / rect.width;
            videoRef.current.currentTime = ratio * duration;
          }}
        >
          <div
            className="h-full bg-primary transition-all duration-100"
            style={{ width: `${progressPercent}%` }}
          />
          {/* 裁剪区间外左侧遮罩 */}
          {duration > 0 && clipStart != null && clipStart > 0 && (
            <div
              className="absolute top-0 left-0 h-full bg-black/60 pointer-events-none"
              style={{ width: `${Math.min(100, (clipStart / duration) * 100)}%` }}
            />
          )}
          {/* 裁剪区间外右侧遮罩 */}
          {duration > 0 && clipEnd != null && clipEnd < duration && (
            <div
              className="absolute top-0 h-full bg-black/60 pointer-events-none"
              style={{
                left: `${Math.min(100, (clipEnd / duration) * 100)}%`,
                width: `${Math.min(100, ((duration - clipEnd) / duration) * 100)}%`,
              }}
            />
          )}
        </div>

        {/* Buttons */}
        <div className="flex items-center gap-2">
          <button onClick={() => skip(-5)} className="text-muted-foreground hover:text-foreground transition-colors">
            <SkipBack className="w-4 h-4" />
          </button>
          <button
            onClick={togglePlay}
            className="w-8 h-8 rounded-full bg-primary flex items-center justify-center text-primary-foreground hover:bg-primary/80 transition-colors"
          >
            {playing ? <Pause className="w-4 h-4" /> : <Play className="w-4 h-4 ml-0.5" />}
          </button>
          <button onClick={() => skip(5)} className="text-muted-foreground hover:text-foreground transition-colors">
            <SkipForward className="w-4 h-4" />
          </button>
          <button onClick={() => { setMuted(!muted); if (videoRef.current) videoRef.current.muted = !muted; }}
            className="text-muted-foreground hover:text-foreground transition-colors ml-1">
            {muted ? <VolumeX className="w-4 h-4" /> : <Volume2 className="w-4 h-4" />}
          </button>
          {previewMode && (
            <button
              onClick={onExitPreview}
              className="text-[10px] px-2 py-0.5 rounded border border-orange-500/30 text-orange-400 hover:bg-orange-500/10 transition-colors"
            >
              返回原视频
            </button>
          )}
          {!previewMode && segments.some((s) => s.action === "delete") && (
            <div className="flex items-center rounded overflow-hidden border border-border text-[10px] ml-1">
              <button
                className={cn("px-2 py-0.5 transition-colors",
                  !playKeptOnly ? "bg-secondary text-foreground" : "text-muted-foreground hover:text-foreground")}
              onClick={() => onPlayKeptOnlyChange(false)}
            >
              原字幕
            </button>
            <button
              className={cn("px-2 py-0.5 transition-colors",
                playKeptOnly ? "bg-green-500/20 text-green-400" : "text-muted-foreground hover:text-foreground")}
              onClick={() => onPlayKeptOnlyChange(true)}
              >
                保留字幕
              </button>
            </div>
          )}
          <div className="ml-auto">
            <span className="timestamp text-[11px]">
              {formatTimestamp(localTime)} / {formatTimestamp(duration)}
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}

// ============================================================
// Resegment Panel — 动态调整分段阈值
// ============================================================

// 0.05 步长，范围 0.05 ~ 0.50，共 10 档
const RESEG_OPTIONS = [0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50];
const RESEG_LABELS: Record<number, string> = {
  0.05: "极紧", 0.10: "很紧", 0.15: "紧+", 0.20: "紧",
  0.25: "中-",  0.30: "适中", 0.35: "中+", 0.40: "松",
  0.45: "松+",  0.50: "很松",
};

function ResegmentPanel({ task, onRefresh }: { task: Task; onRefresh: () => void }) {
  const currentThreshold = task.params?.silence_threshold ?? 0.3;
  const [selectedThreshold, setSelectedThreshold] = useState<number>(currentThreshold);
  const [previewStats, setPreviewStats] = useState<ResegmentStats | null>(null);
  const [currentStats, setCurrentStats] = useState<ResegmentStats | null>(null);
  const [recommendation, setRecommendation] = useState<{ threshold: number; reason: string; stats: ResegmentStats } | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const isPreviewing = selectedThreshold !== currentThreshold && previewStats !== null;

  // 首次加载：获取当前阈值的统计 + 推荐值
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const result = await resegment(task.id, currentThreshold, false);
        if (cancelled) return;
        setCurrentStats(result.stats);
        setPreviewStats(null);
        if (result.recommendation) setRecommendation(result.recommendation);
      } catch { /* 静默 */ }
    })();
    return () => { cancelled = true; };
  }, [task.id, currentThreshold]);

  // 阈值变化时 debounce 预览
  const handleThresholdChange = useCallback((val: number) => {
    setSelectedThreshold(val);
    if (debounceRef.current) clearTimeout(debounceRef.current);

    if (val === currentThreshold) {
      setPreviewStats(null);
      return;
    }

    setIsLoading(true);
    debounceRef.current = setTimeout(async () => {
      try {
        const result = await resegment(task.id, val, false);
        setPreviewStats(result.stats);
        if (result.recommendation) setRecommendation(result.recommendation);
      } catch (e: any) {
        toast.error(e.message);
      } finally {
        setIsLoading(false);
      }
    }, 300);
  }, [task.id, currentThreshold]);

  const handleApply = async () => {
    // 若已有审计结果，提示用户
    const hasAudit = task.audit_segments?.some(s => s.action !== "keep");
    if (hasAudit) {
      const ok = window.confirm("重新分段将清除所有审计决策（保留/删除标记），需要重新审计。确认？");
      if (!ok) return;
    }
    setIsSaving(true);
    try {
      await resegment(task.id, selectedThreshold, true);
      toast.success(`已应用阈值 ${selectedThreshold}s 重新分段`);
      setPreviewStats(null);
      onRefresh();
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setIsSaving(false);
    }
  };

  const handleCancel = () => {
    setSelectedThreshold(currentThreshold);
    setPreviewStats(null);
  };

  const displayStats = isPreviewing ? previewStats : currentStats;

  const StatDelta = ({ label, cur, prev, unit = "", reverse = false }: {
    label: string; cur: number; prev: number | null; unit?: string; reverse?: boolean;
  }) => {
    const delta = prev != null ? cur - prev : 0;
    const showDelta = prev != null && delta !== 0;
    const isGood = reverse ? delta < 0 : delta > 0;
    return (
      <div className="text-center">
        <p className="text-[10px] text-muted-foreground mb-0.5">{label}</p>
        <div className="flex items-center justify-center gap-1">
          <span className="text-xs font-mono text-foreground font-semibold">{cur}{unit}</span>
          {showDelta && (
            <span className={cn("text-[10px] font-mono", isGood ? "text-green-400" : "text-red-400")}>
              {delta > 0 ? "+" : ""}{Number.isInteger(cur) && Number.isInteger(prev ?? 0) ? delta : delta.toFixed(1)}{unit}
            </span>
          )}
        </div>
      </div>
    );
  };

  return (
    <div className="bg-secondary rounded-lg p-3 mt-2">
      <div className="flex items-center gap-1.5 mb-2">
        <SlidersHorizontal className="w-3 h-3 text-muted-foreground" />
        <p className="text-[10px] text-muted-foreground uppercase tracking-wider">分段调节</p>
        <span className="ml-auto text-[11px] font-mono text-primary font-semibold">
          {RESEG_LABELS[selectedThreshold] ?? `${selectedThreshold}s`}
        </span>
      </div>

      {/* 阈值按钮组：两行各 5 个，0.05步长 */}
      <div className="space-y-1 mb-2">
        {[RESEG_OPTIONS.slice(0, 5), RESEG_OPTIONS.slice(5)].map((row, rowIdx) => (
          <div key={rowIdx} className="flex items-center gap-1">
            {row.map((val) => {
              const isActive = selectedThreshold === val;
              const isOriginal = currentThreshold === val && isPreviewing;
              return (
                <button
                  key={val}
                  onClick={() => handleThresholdChange(val)}
                  className={cn(
                    "flex-1 rounded py-1 flex flex-col items-center transition-all duration-100",
                    isActive
                      ? "bg-primary text-primary-foreground font-semibold"
                      : isOriginal
                        ? "border-2 border-dashed border-primary/40 text-primary/60 bg-background"
                        : "bg-background border border-border text-muted-foreground hover:border-primary/60 hover:text-foreground"
                  )}
                >
                  <span className="text-[10px] font-mono leading-tight">{val.toFixed(2)}</span>
                  <span className="text-[8px] leading-tight opacity-70">{RESEG_LABELS[val]}</span>
                </button>
              );
            })}
          </div>
        ))}
      </div>

      {/* 智能推荐条 */}
      {recommendation && recommendation.threshold !== currentThreshold && (
        <div className="flex items-center gap-1.5 rounded-md bg-primary/10 border border-primary/20 px-2 py-1.5 mb-2">
          <Lightbulb className="w-3 h-3 text-primary shrink-0" />
          <span className="text-[10px] text-primary/80 flex-1 leading-tight">
            推荐 <span className="font-mono font-semibold">{recommendation.threshold}s</span>
            <span className="text-primary/60 ml-1">— {recommendation.reason}</span>
          </span>
          <button
            onClick={() => handleThresholdChange(recommendation.threshold)}
            className="text-[10px] font-medium text-primary hover:text-primary/80 shrink-0 px-1.5 py-0.5 rounded bg-primary/10 hover:bg-primary/20 transition-colors"
          >
            用它
          </button>
        </div>
      )}

      {/* 实时统计 */}
      {displayStats && (
        <div className={cn("grid grid-cols-4 gap-1 rounded-md p-2 mb-2", isPreviewing ? "bg-primary/5 border border-primary/15" : "bg-background/50")}>
          <StatDelta label="总段数" cur={displayStats.total} prev={isPreviewing ? currentStats?.total ?? null : null} />
          <StatDelta label="均字数" cur={displayStats.avg_chars} prev={isPreviewing ? currentStats?.avg_chars ?? null : null} unit="" reverse />
          <StatDelta label=">30字" cur={displayStats.over_limit_count} prev={isPreviewing ? currentStats?.over_limit_count ?? null : null} reverse />
          <StatDelta label="最长" cur={displayStats.max_chars} prev={isPreviewing ? currentStats?.max_chars ?? null : null} unit="字" reverse />
        </div>
      )}

      {/* 加载指示 */}
      {isLoading && (
        <div className="flex items-center justify-center gap-1.5 py-1 text-[10px] text-muted-foreground">
          <Loader2 className="w-3 h-3 animate-spin" />
          <span>预览中...</span>
        </div>
      )}

      {/* 预览态操作按钮 */}
      {isPreviewing && !isLoading && (
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            className="flex-1 h-7 text-xs gap-1.5"
            onClick={handleApply}
            disabled={isSaving}
          >
            {isSaving ? <Loader2 className="w-3 h-3 animate-spin" /> : <Check className="w-3 h-3" />}
            应用分段
          </Button>
          <Button
            size="sm"
            variant="outline"
            className="h-7 text-xs gap-1"
            onClick={handleCancel}
          >
            <X className="w-3 h-3" />
            取消
          </Button>
        </div>
      )}
    </div>
  );
}

// ============================================================
// Speaker Panel (Right)
// ============================================================
function SpeakerPanel({ task, onRefresh }: { task: Task; onRefresh: () => void }) {
  const speakers = task.asr_result?.speakers || [];
  const segments = task.audit_segments || [];
  const hasDiarData = (task.asr_result?.diar_segments?.length ?? 0) > 0;

  // 平滑阈值 slider：从 asr_result 读取当前值作为初始值
  const [smoothThreshold, setSmoothThreshold] = useState<number>(
    task.asr_result?.diar_smooth_threshold ?? 1.0
  );
  const [isResmoothing, setIsResmoothing] = useState(false);

  // 当 task 更新时同步阈值显示
  useEffect(() => {
    if (task.asr_result?.diar_smooth_threshold != null) {
      setSmoothThreshold(task.asr_result.diar_smooth_threshold);
    }
  }, [task.asr_result?.diar_smooth_threshold]);

  const handleResmooth = async () => {
    setIsResmoothing(true);
    try {
      await resmooth(task.id, smoothThreshold);
      toast.success(`说话人已重新标注（阈值 ${smoothThreshold}s）`);
      onRefresh();
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setIsResmoothing(false);
    }
  };

  const speakerStats = speakers.map((spk) => {
    const spkSegs = segments.filter((s) => s.speaker === spk);
    // subtitle_fix / text_fix / merge_next 视频不剪切，统计上归入"保留"
    const kept = spkSegs.filter((s) => ["keep", "subtitle_fix", "text_fix", "merge_next"].includes(s.action));
    const totalDuration = spkSegs.reduce((acc, s) => acc + (s.end - s.start), 0);
    const keptDuration = kept.reduce((acc, s) => acc + (s.end - s.start), 0);
    return { spk, total: spkSegs.length, kept: kept.length, totalDuration, keptDuration };
  });

  const speakerNames: Record<string, string> = {
    spk0: "主播",
    spk1: "嘉宾 A",
    spk2: "嘉宾 B",
    spk3: "嘉宾 C",
  };

  return (
    <div className="h-full flex flex-col">
      <div className="px-3 py-2 border-b border-border">
        <div className="flex items-center gap-1.5">
          <Users className="w-3.5 h-3.5 text-muted-foreground" />
          <span className="text-xs font-medium text-muted-foreground uppercase tracking-wider">说话人</span>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-3 space-y-3">
        {speakerStats.length === 0 ? (
          <div className="text-center py-6">
            <Mic className="w-6 h-6 text-muted-foreground/30 mx-auto mb-2" />
            <p className="text-xs text-muted-foreground/50">暂无说话人数据</p>
          </div>
        ) : (
          speakerStats.map(({ spk, total, kept, totalDuration }) => {
            const color = SPEAKER_COLORS[spk] || SPEAKER_COLORS.default;
            return (
              <div key={spk} className="bg-secondary rounded-lg p-3">
                <div className="flex items-center gap-2 mb-2">
                  <div className="w-3 h-3 rounded-full" style={{ background: color }} />
                  <span className="text-xs font-medium text-foreground">{speakerNames[spk] || spk}</span>
                  <span className="text-[10px] text-muted-foreground ml-auto font-mono">{spk}</span>
                </div>
                <div className="flex items-center gap-3 text-[11px]">
                  <div>
                    <span className="text-muted-foreground">片段</span>
                    <span className="text-foreground font-mono ml-1">{total}</span>
                  </div>
                  <div>
                    <span className="text-muted-foreground">保留</span>
                    <span className="text-green-400 font-mono ml-1">{kept}</span>
                  </div>
                  <div>
                    <span className="text-muted-foreground">时长</span>
                    <span className="timestamp ml-1">{formatDuration(totalDuration)}</span>
                  </div>
                </div>
              </div>
            );
          })
        )}

        {/* 说话人平滑阈值调节 */}
        {hasDiarData && (
          <div className="bg-secondary rounded-lg p-3 mt-1">
            <div className="flex items-center gap-1.5 mb-2">
              <RefreshCw className="w-3 h-3 text-muted-foreground" />
              <p className="text-[10px] text-muted-foreground uppercase tracking-wider">平滑阈值</p>
              <span className="ml-auto text-[11px] font-mono text-primary font-semibold">{smoothThreshold.toFixed(1)}s</span>
            </div>
            <p className="text-[10px] text-muted-foreground/60 mb-2 leading-relaxed">
              短于此时长的孤立说话人切换视为噪声并修正。值越大，消除的误标越多，但也可能合并真实短插话。
            </p>
            <input
              type="range"
              min={0.5}
              max={5.0}
              step={0.5}
              value={smoothThreshold}
              onChange={(e) => setSmoothThreshold(parseFloat(e.target.value))}
              className="w-full accent-primary cursor-pointer mb-1"
            />
            <div className="flex justify-between text-[9px] text-muted-foreground/50 font-mono mb-2">
              <span>0.5s</span>
              <span>1.0</span>
              <span>2.0</span>
              <span>3.0</span>
              <span>4.0</span>
              <span>5.0s</span>
            </div>
            <Button
              size="sm"
              className="w-full h-7 text-xs gap-1.5"
              onClick={handleResmooth}
              disabled={isResmoothing}
            >
              {isResmoothing ? (
                <Loader2 className="w-3 h-3 animate-spin" />
              ) : (
                <RefreshCw className="w-3 h-3" />
              )}
              重新标注说话人
            </Button>
          </div>
        )}

        {/* 分段调节面板 */}
        {(task.asr_result?.words?.length ?? 0) > 0 && (
          <ResegmentPanel task={task} onRefresh={onRefresh} />
        )}

        {/* Audit Stats */}
        {segments.length > 0 && (
          <div className="bg-secondary rounded-lg p-3 mt-2">
            <p className="text-[10px] text-muted-foreground uppercase tracking-wider mb-2">审计统计</p>
            <div className="space-y-1.5">
              <div className="flex justify-between text-[11px]">
                <span className="text-muted-foreground">总片段</span>
                <span className="font-mono text-foreground">{segments.length}</span>
              </div>
              <div className="flex justify-between text-[11px]">
                <span className="text-green-400">保留</span>
                <span className="font-mono text-green-400">{task.segments_kept}</span>
              </div>
              <div className="flex justify-between text-[11px]">
                <span className="text-red-400">删除</span>
                <span className="font-mono text-red-400">{task.segments_deleted}</span>
              </div>
              {task.edited_duration && task.original_duration && (
                <div className="flex justify-between text-[11px]">
                  <span className="text-primary">压缩比</span>
                  <span className="font-mono text-primary">
                    {Math.round((1 - task.edited_duration / task.original_duration) * 100)}%
                  </span>
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ============================================================
// Status Stepper — 顶部全流程步骤条
// ============================================================
interface StepDef {
  label: string;
  /** 哪些 TaskStatus 值对应"此步骤正在进行" */
  active: TaskStatus[];
  /** 哪些 TaskStatus 值对应"此步骤已完成" */
  done: TaskStatus[];
}

const STEPS: StepDef[] = [
  {
    label: "上传视频",
    active: ["pending"],
    done: ["asr_running", "asr_done", "seg_optim_running", "seg_optim_done", "audit_running", "review", "export_running", "done"],
  },
  {
    label: "语音识别",
    active: ["asr_running"],
    done: ["asr_done", "seg_optim_running", "seg_optim_done", "audit_running", "review", "export_running", "done"],
  },
  {
    label: "段落优化",
    active: ["seg_optim_running"],
    done: ["seg_optim_done", "audit_running", "review", "export_running", "done"],
  },
  {
    label: "AI 审计",
    active: ["audit_running"],
    done: ["review", "export_running", "done"],
  },
  {
    label: "剪辑审核",
    active: ["review"],
    done: ["export_running", "done"],
  },
  {
    label: "导出成片",
    active: ["export_running"],
    done: ["done"],
  },
];

function StatusStepper({ status, taskType, onRecover }: { status: TaskStatus; taskType?: string; onRecover?: () => void }) {
  const isError = status === "error";
  const steps = taskType === "highlight_reel" ? STEPS.filter(s => s.label !== "段落优化") : STEPS;

  return (
    <div className="h-10 shrink-0 border-b border-border bg-card/40 flex items-center px-6">
      <div className="flex items-center w-full max-w-2xl mx-auto">
        {steps.map((step, idx) => {
          // error 状态时不激活任何步骤，只显示历史完成步骤
          const isDone = !isError && step.done.includes(status);
          const isActive = !isError && step.active.includes(status);
          const isLast = idx === steps.length - 1;

          return (
            <div key={step.label} className="flex items-center flex-1 min-w-0">
              {/* 步骤节点 */}
              <div className="flex items-center gap-1.5 shrink-0">
                {/* 圆圈指示器 */}
                <div
                  className={cn(
                    "w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-bold transition-all",
                    isDone && "bg-primary text-black",
                    isActive && "border-2 border-primary text-primary bg-primary/10",
                    !isDone && !isActive && "border border-border text-muted-foreground bg-transparent",
                  )}
                >
                  {isDone ? (
                    <Check className="w-2.5 h-2.5" />
                  ) : isActive ? (
                    <Loader2 className="w-2.5 h-2.5 animate-spin" />
                  ) : (
                    <span>{idx + 1}</span>
                  )}
                </div>
                {/* 步骤名 */}
                <span
                  className={cn(
                    "text-[11px] whitespace-nowrap",
                    isDone && "text-primary font-medium",
                    isActive && "text-foreground font-semibold",
                    !isDone && !isActive && "text-muted-foreground",
                  )}
                >
                  {step.label}
                </span>
              </div>

              {/* 连接线（最后一步不显示） */}
              {!isLast && (
                <div
                  className={cn(
                    "flex-1 h-px mx-2",
                    isDone ? "bg-primary/50" : "bg-border",
                  )}
                />
              )}
            </div>
          );
        })}

        {/* 出错时在末尾追加红色标签 + 恢复按钮 */}
        {isError && (
          <div className="ml-3 flex items-center gap-2 shrink-0">
            <div className="w-4 h-px bg-border" />
            <div className="flex items-center gap-1 px-2 py-0.5 rounded-full bg-destructive/20 border border-destructive/40">
              <X className="w-2.5 h-2.5 text-destructive" />
              <span className="text-[10px] text-destructive font-medium">出错</span>
            </div>
            {onRecover && (
              <button
                onClick={onRecover}
                className="flex items-center gap-1 px-2 py-0.5 rounded-full bg-amber-500/20 border border-amber-500/40 hover:bg-amber-500/30 transition-colors"
              >
                <RotateCcw className="w-2.5 h-2.5 text-amber-400" />
                <span className="text-[10px] text-amber-400 font-medium">恢复到出错前</span>
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// ============================================================
// Subtitle Style Editor（字幕样式配置 + 实时预览）
// ============================================================
// 常用中文字体列表（值 = FFmpeg/系统字体名，label = 显示名）
const CHINESE_FONTS: { label: string; value: string; css: string }[] = [
  // macOS 系统字体
  { label: "苹方（PingFang SC）",      value: "PingFang SC",         css: "PingFang SC, sans-serif" },
  { label: "黑体（Heiti SC）",          value: "Heiti SC",             css: "Heiti SC, sans-serif" },
  { label: "宋体（Songti SC）",         value: "Songti SC",            css: "Songti SC, serif" },
  { label: "楷体（STKaiti）",           value: "STKaiti",              css: "STKaiti, cursive" },
  { label: "仿宋（STFangsong）",        value: "STFangsong",           css: "STFangsong, serif" },
  { label: "华文黑体（STHeiti）",        value: "STHeiti",              css: "STHeiti, sans-serif" },
  { label: "华文细黑（STXihei）",        value: "STXihei",              css: "STXihei, sans-serif" },
  // 跨平台 Noto 字体（Linux/服务器常见）
  { label: "Noto Sans CJK SC（无衬线）", value: "Noto Sans CJK SC",   css: "'Noto Sans CJK SC', sans-serif" },
  { label: "Noto Serif CJK SC（衬线）",  value: "Noto Serif CJK SC",  css: "'Noto Serif CJK SC', serif" },
  // 文泉驿（Linux）
  { label: "文泉驿微米黑",               value: "WenQuanYi Micro Hei", css: "'WenQuanYi Micro Hei', sans-serif" },
];

const DEFAULT_SUBTITLE_STYLE: SubtitleStyle = {
  font_name: "PingFang SC",
  font_size: 28,
  primary_color: "#FFFFFF",
  outline_color: "#000000",
  outline: 2,
  shadow: 0.5,
  bold: false,
  alignment: 2,
  margin_top: 40,
  // 下方边距的初始值会在 openSubtitleConfig 中根据视频方向动态调整（首次使用时）
  margin_bottom: 80,
  margin_l: 60,
  margin_r: 60,
  max_lines: 2,
};

/**
 * 根据视频宽高比返回合理的首次默认样式。
 * 竖屏（9:16）：字号 72、无描边、无阴影、加粗、下边距 100px、左右 80px
 * 横屏（16:9）：下边距 ≈ 7.4% of 1080，左右 ≈ 5.2% of 1920
 */
function getOrientationDefaults(ar: number): Partial<SubtitleStyle> {
  if (ar < 1) {
    // 竖屏
    return { font_size: 78, outline: 0, shadow: 0, bold: true, margin_bottom: 100, margin_l: 80, margin_r: 80 };
  } else {
    // 横屏
    return { margin_bottom: 80, margin_l: 100, margin_r: 100 };
  }
}

const SUBTITLE_STYLE_LS_KEY = "gc_subtitle_style";

// ============================================================
// 封面风格定义
// ============================================================
const COVER_STYLES = [
  {
    id: "emotion",
    label: "情绪冲击",
    emoji: "😱",
    example: "/style-examples/情绪冲击.jpg",
    prompt: `情绪冲击型封面（爆款流量型）。
人物必须占画面主体，优先使用面部特写（放大到肩膀以上）。
表情要明显：震惊/困惑/崩溃/不理解。
文案必须拆分成2-3行，每行不超过6个字；关键词（1-2个）放大1.3倍。
字体：粗体 + 黑色描边（必须）。
颜色：黄色或白色高对比字体。
文案位置贴近人物脸部，制造压迫感。
构图略不对称，增加冲突感。
禁止：居中排版、小字、干净设计风。
目标效果：强情绪、略夸张、第一眼吸引点击。`,
  },
  {
    id: "info",
    label: "知识卡片",
    emoji: "📋",
    example: "/style-examples/知识卡片.jpg",
    prompt: `信息解释型封面（知识卡片）。
文案拆为主标题（结论，大字）+ 副标题（解释/补充，小字），最多2层结构。
字体清晰，不使用粗描边；白色或浅黄色字体 + 轻阴影。
可加简单框或底色块突出关键词。
背景轻微模糊，提高可读性。
人物不需要强情绪，正常表情即可。
禁止：夸张表情、大面积黄色粗字、营销广告感。
目标效果：清晰、有逻辑、像"有干货"。`,
  },
  {
    id: "vlog",
    label: "生活vlog",
    emoji: "📷",
    example: "/style-examples/生活vlog.jpg",
    prompt: `生活感vlog封面（真实日常）。
保留完整场景（房间/桌面/环境），人物为中景（不要脸部特写）。
文案像说话，保持口语感；字体中等大小，不抢画面。
文案放在边角或人物旁边，自然排布。
整体色调偏暖或生活化，可加轻微滤镜（不要重设计）。
禁止：大字压脸、强对比色、广告感。
目标效果：像真实截图，但有轻微"钩子"。`,
  },
];

// ============================================================
// ImgModelPicker — 图像模型选择器（封面生成专用）
// ============================================================
const IMG_MODELS = [
  { value: "google/gemini-2.5-flash-image", label: "Gemini 2.5 Flash Image", price: "" },
  { value: "riverflow-v2-pro", label: "Riverflow V2 Pro", price: "≈$0.15/张" },
  { value: "google/gemini-3-pro-image-preview", label: "Gemini 3 Pro Image", price: "≈$0.20/张" },
  { value: "bytedance-seed/seedream-4.5", label: "Seedream 4.5", price: "" },
  { value: "openai/dall-e-3", label: "DALL-E 3", price: "" },
  { value: "bfl/flux-1.1-pro-ultra", label: "Flux 1.1 Pro Ultra", price: "" },
  { value: "recraft-ai/recraft-v3", label: "Recraft V3", price: "" },
];

function ImgModelPicker({
  value,
  onChange,
  disabled,
}: {
  value: string;
  onChange: (v: string) => void;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [customInput, setCustomInput] = useState("");

  const preset = IMG_MODELS.find((m) => m.value === value);
  const shortLabel = preset
    ? preset.label.length > 16 ? preset.label.slice(0, 14) + "…" : preset.label
    : value.length > 16 ? "…" + value.slice(-14) : value;

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          disabled={disabled}
          className={cn(
            "flex items-center gap-1 px-2 py-1 h-7 rounded text-xs border transition-colors",
            "border-border text-muted-foreground hover:text-foreground hover:bg-secondary/60",
            disabled && "opacity-50 cursor-not-allowed"
          )}
          title="切换图像生成模型"
        >
          <span className="truncate max-w-[120px]">{shortLabel}</span>
          <ChevronDown className="w-3 h-3 shrink-0 opacity-50" />
        </button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-64 p-0 bg-card border-border">
        <div className="px-3 py-2 border-b border-border">
          <p className="text-[10px] text-muted-foreground/60 uppercase tracking-wider">图像生成模型</p>
        </div>
        <div className="max-h-56 overflow-y-auto">
          {IMG_MODELS.map((m) => {
            const selected = value === m.value;
            return (
              <button
                key={m.value}
                onClick={() => { onChange(m.value); setOpen(false); }}
                className={cn(
                  "w-full text-left px-3 py-2 flex items-center gap-2 transition-colors",
                  selected ? "bg-primary/10" : "hover:bg-secondary/60"
                )}
              >
                <div className="w-4 shrink-0 flex justify-center">
                  {selected && <Check className="w-3 h-3 text-primary" />}
                </div>
                <div className="flex-1 min-w-0">
                  <p className={cn("text-xs truncate", selected ? "text-primary font-medium" : "text-foreground")}>
                    {m.label}
                  </p>
                  {m.price && (
                    <p className="text-[10px] text-muted-foreground/60">{m.price}</p>
                  )}
                </div>
              </button>
            );
          })}
        </div>
        <div className="border-t border-border px-3 py-2">
          <p className="text-[10px] text-muted-foreground/60 mb-1.5">自定义模型 ID</p>
          <div className="flex gap-1.5">
            <Input
              value={customInput}
              onChange={(e) => setCustomInput(e.target.value)}
              placeholder="provider/model-id"
              className="h-6 text-xs px-2"
              onKeyDown={(e) => {
                if (e.key === "Enter" && customInput.trim()) {
                  onChange(customInput.trim());
                  setCustomInput("");
                  setOpen(false);
                }
              }}
            />
            <button
              onClick={() => {
                if (customInput.trim()) {
                  onChange(customInput.trim());
                  setCustomInput("");
                  setOpen(false);
                }
              }}
              className="px-2 h-6 text-xs rounded bg-primary/20 text-primary hover:bg-primary/30 transition-colors whitespace-nowrap"
            >
              确认
            </button>
          </div>
        </div>
      </PopoverContent>
    </Popover>
  );
}

// ============================================================
// CoverAndDescriptionDialog — 封面与简介
// ============================================================
function CoverAndDescriptionDialog({
  open,
  onClose,
  taskId,
  defaultCoverTitle,
  frameDataUrl,
  videoAspectRatio,
  videoDuration,
  videoCurrentTime,
  segments,
  apiKey,
  provider,
  claudeModel,
  ollamaModel,
  ollamaBaseUrl,
  onCoverGenerated,
  initialGeneratedCover,
}: {
  open: boolean;
  onClose: () => void;
  taskId: string;
  defaultCoverTitle?: string;
  frameDataUrl?: string | null;
  videoAspectRatio?: number;
  videoDuration?: number;
  videoCurrentTime?: number;
  segments?: Segment[];
  apiKey?: string;
  provider?: string;
  claudeModel?: string;
  ollamaModel?: string;
  ollamaBaseUrl?: string;
  onCoverGenerated?: (base64: string) => void;
  initialGeneratedCover?: string | null;
}) {
  // ---- 封面预览尺寸（ResizeObserver 精确计算，和 SubtitlePreviewBox 同模式）
  const previewWrapRef = useRef<HTMLDivElement>(null);
  const [wrapSize, setWrapSize] = useState({ w: 400, h: 300 });
  useEffect(() => {
    if (!previewWrapRef.current) return;
    const obs = new ResizeObserver((entries) => {
      for (const e of entries) setWrapSize({ w: e.contentRect.width, h: e.contentRect.height });
    });
    obs.observe(previewWrapRef.current);
    return () => obs.disconnect();
  }, []);
  const ar = videoAspectRatio && videoAspectRatio > 0 ? videoAspectRatio : 16 / 9;
  const previewWidth  = wrapSize.h > 0 ? Math.min(wrapSize.w, wrapSize.h * ar) : wrapSize.w;
  const previewHeight = previewWidth / ar;

  // ---- localStorage 持久化（按 taskId 存储生成内容）
  const _contentLsKey = `gc_cover_content_${taskId}`;
  const _loadPersistedContent = () => {
    try { return JSON.parse(localStorage.getItem(_contentLsKey) || "{}"); } catch { return {}; }
  };
  const _saveContentField = (patch: Record<string, unknown>) => {
    try {
      const cur = _loadPersistedContent();
      localStorage.setItem(_contentLsKey, JSON.stringify({ ...cur, ...patch }));
    } catch { /* quota */ }
  };

  // ---- 内容状态（从 localStorage 初始化）
  const _saved = _loadPersistedContent();
  const [localFrame, setLocalFrame] = useState<string | null>(frameDataUrl ?? null);
  const [localTime, setLocalTime] = useState(videoCurrentTime ?? 0);
  const [coverTitle, setCoverTitle] = useState<string>(_saved.coverTitle ?? defaultCoverTitle ?? "");
  const [selectedStyle, setSelectedStyle] = useState<string | null>(null);
  const [xhsTitles, setXhsTitles] = useState<string[]>(_saved.xhsTitles ?? []);
  const [xhsDesc, setXhsDesc] = useState<string>(_saved.xhsDesc ?? "");
  const [xhsLoading, setXhsLoading] = useState(false);

  // ---- 独立模型配置（两个功能区各自维护，不写回全局 config）
  const makeInitialConfig = (): AppConfig => ({
    claudeApiKey: apiKey ?? "",
    claudeModel: claudeModel ?? "claude-sonnet-4-6",
    jianyingDir: "",
    provider: (provider ?? "claude") as LLMProvider,
    ollamaModel: ollamaModel ?? "deepseek-r1:14b",
    ollamaBaseUrl: ollamaBaseUrl ?? "http://localhost:11434",
  });
  const [coverImgConfig, setCoverImgConfig] = useState<AppConfig>(makeInitialConfig);
  const [xhsConfig, setXhsConfig] = useState<AppConfig>(makeInitialConfig);
  const [coverTitleConfig, setCoverTitleConfig] = useState<AppConfig>(makeInitialConfig);
  const updateCoverImgConfig   = useCallback((patch: Partial<AppConfig>) => setCoverImgConfig(p => ({ ...p, ...patch })), []);
  const updateXhsConfig        = useCallback((patch: Partial<AppConfig>) => setXhsConfig(p => ({ ...p, ...patch })), []);
  const updateCoverTitleConfig = useCallback((patch: Partial<AppConfig>) => setCoverTitleConfig(p => ({ ...p, ...patch })), []);

  const [coverTitleSuggestions, setCoverTitleSuggestions] = useState<string[]>(_saved.coverTitleSuggestions ?? []);
  const [coverTitleLoading, setCoverTitleLoading] = useState(false);
  const [imgProvider, setImgProvider] = useState<string>("google/gemini-2.5-flash-image");
  const [coverImgLoading, setCoverImgLoading] = useState(false);
  const [generatedCoverUrl, setGeneratedCoverUrl] = useState<string | null>(initialGeneratedCover ?? null);
  const [showAiCover, setShowAiCover] = useState(false);
  const [stylePrompts, setStylePrompts] = useState<Record<string, string>>({});

  // 弹窗打开时同步外部传入的初始值，并加载风格 prompts
  useEffect(() => {
    if (open) {
      getCoverContentPrompts().then(data => {
        if (data.cover_styles) setStylePrompts(data.cover_styles as Record<string, string>);
      }).catch(() => {});

      setLocalFrame(frameDataUrl ?? null);
      setLocalTime(videoCurrentTime ?? 0);
      setShowAiCover(false);
      // 从 localStorage 恢复已生成内容
      const saved = _loadPersistedContent();
      if (saved.coverTitle) setCoverTitle(saved.coverTitle);
      if (saved.xhsTitles?.length) setXhsTitles(saved.xhsTitles);
      if (saved.xhsDesc) setXhsDesc(saved.xhsDesc);
      if (saved.coverTitleSuggestions?.length) setCoverTitleSuggestions(saved.coverTitleSuggestions);
      if (initialGeneratedCover) setGeneratedCoverUrl(initialGeneratedCover);
      const cfg = makeInitialConfig();
      setCoverImgConfig(cfg);
      setXhsConfig(cfg);
      setCoverTitleConfig(cfg);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, frameDataUrl, videoCurrentTime, defaultCoverTitle, apiKey, provider, claudeModel, ollamaModel, ollamaBaseUrl]);

  const captureFrameAt = useCallback((t: number) => {
    setLocalFrame(`http://localhost:8000/api/tasks/${taskId}/frame?t=${t.toFixed(3)}&_=${Date.now()}`);
  }, [taskId]);

  const seekAndCapture = useCallback((t: number) => {
    setLocalTime(t);
    const video = document.querySelector("video") as HTMLVideoElement | null;
    if (video) video.currentTime = t;
    captureFrameAt(t);
  }, [captureFrameAt]);

  const handleGenerateXhs = async () => {
    setXhsLoading(true);
    try {
      const res = await generateXhsContent(taskId, {
        cover_title: coverTitle,
        api_key: xhsConfig.claudeApiKey || apiKey,
        provider: xhsConfig.provider,
        claude_model: xhsConfig.claudeModel,
        ollama_model: xhsConfig.ollamaModel,
        ollama_base_url: xhsConfig.ollamaBaseUrl,
      });
      const titles = res.xhs_titles ?? (res.xhs_title ? [res.xhs_title] : []);
      const desc = res.xhs_description;
      setXhsTitles(titles);
      setXhsDesc(desc);
      _saveContentField({ xhsTitles: titles, xhsDesc: desc });
    } catch (e: any) {
      toast.error(e.message || "生成失败，请检查 API 配置");
    } finally {
      setXhsLoading(false);
    }
  };

  const handleGenerateCoverTitles = async () => {
    setCoverTitleLoading(true);
    try {
      const res = await generateCoverTitles(taskId, {
        api_key: coverTitleConfig.claudeApiKey || apiKey,
        provider: coverTitleConfig.provider,
        claude_model: coverTitleConfig.claudeModel,
        ollama_model: coverTitleConfig.ollamaModel,
        ollama_base_url: coverTitleConfig.ollamaBaseUrl,
      });
      const suggestions = res.cover_titles ?? [];
      setCoverTitleSuggestions(suggestions);
      _saveContentField({ coverTitleSuggestions: suggestions });
    } catch (e: any) {
      toast.error(e.message || "生成失败，请检查 API 配置");
    } finally {
      setCoverTitleLoading(false);
    }
  };

  const handleGenerateCoverImage = async () => {
    if (!coverTitle.trim()) {
      toast.error("请先填写封面标题");
      return;
    }
    if (!selectedStyle) {
      toast.error("请先选择封面风格");
      return;
    }
    const stylePrompt = (selectedStyle && stylePrompts[selectedStyle])
      ? stylePrompts[selectedStyle]
      : (COVER_STYLES.find(s => s.id === selectedStyle)?.prompt ?? "");
    setCoverImgLoading(true);
    try {
      const res = await generateCoverImage(taskId, {
        frame_time: localTime,
        cover_title: coverTitle,
        style_prompt: stylePrompt,
        img_provider: imgProvider,
        api_key: apiKey,
      });
      setGeneratedCoverUrl(res.image_base64);
      setShowAiCover(true);
      if (res.image_base64) onCoverGenerated?.(res.image_base64);
    } catch (e: any) {
      toast.error(e.message || "生成失败，请检查 API 配置");
    } finally {
      setCoverImgLoading(false);
    }
  };

  const copyText = (text: string, label: string) => {
    navigator.clipboard.writeText(text).then(() => toast.success(`${label}已复制`));
  };

  const fmtTime = (s: number) => {
    const mm = Math.floor(s / 60);
    const ss = Math.floor(s % 60);
    return `${String(mm).padStart(2, "0")}:${String(ss).padStart(2, "0")}`;
  };

  const dur = videoDuration ?? 0;

  return (
    <Sheet open={open} onOpenChange={v => !v && onClose()}>
      <SheetContent
        side="right"
        className="w-[900px] sm:max-w-[900px] flex flex-col p-0 gap-0 bg-card border-l border-border"
      >
        <SheetHeader className="px-5 pt-5 pb-4 border-b border-border shrink-0">
          <SheetTitle className="flex items-center gap-2 text-base">
            <ImageIcon className="w-4 h-4 text-primary" />
            封面与简介
          </SheetTitle>
          <SheetDescription className="text-xs text-muted-foreground">
            选择封面关键帧，填写标题与风格，AI 一键生成小红书爆款文案
          </SheetDescription>
        </SheetHeader>

        <div className="flex flex-1 min-h-0 overflow-hidden">
          {/* 左栏：封面预览 + 时间轴 */}
          <div className="flex flex-col gap-2 p-4 border-r border-border" style={{ width: "52%", minWidth: 0 }}>
            {/* 预览区：填满剩余空间，尺寸用 ResizeObserver 精确计算 */}
            <div ref={previewWrapRef} className="flex-1 min-h-0 flex items-center justify-center">
              <div
                className="relative rounded-lg overflow-hidden border border-border/50 shrink-0"
                style={{
                  width: previewWidth,
                  height: previewHeight,
                  background: (showAiCover && generatedCoverUrl)
                    ? `url(${generatedCoverUrl}) center/cover no-repeat`
                    : localFrame
                    ? `url(${localFrame}) center/cover no-repeat`
                    : "#111",
                }}
              >
                {!localFrame && !generatedCoverUrl && (
                  <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-muted-foreground/40">
                    <ImageIcon className="w-8 h-8" />
                    <span className="text-xs">拖动时间轴选取封面帧</span>
                  </div>
                )}
                {showAiCover && generatedCoverUrl && (
                  <div className="absolute top-2 left-2 bg-primary/80 rounded px-2 py-0.5 text-[10px] text-white font-medium">
                    AI 生成
                  </div>
                )}
                {(localFrame || generatedCoverUrl) && (
                  <div className="absolute bottom-2 right-2 bg-black/60 rounded px-2 py-0.5 text-[10px] text-white/80 font-mono">
                    {fmtTime(localTime)}
                  </div>
                )}
                {showAiCover && generatedCoverUrl ? (
                  <button
                    onClick={() => setShowAiCover(false)}
                    className="absolute top-2 right-2 bg-black/60 hover:bg-black/80 rounded px-2 py-0.5 text-[10px] text-white/80 transition-colors"
                  >
                    还原
                  </button>
                ) : (
                  <button
                    onClick={() => {
                      if (generatedCoverUrl) {
                        setShowAiCover(true);
                      } else {
                        toast.error("请先去 AI 生成封面");
                      }
                    }}
                    className="absolute top-2 right-2 bg-black/60 hover:bg-black/80 rounded px-2 py-0.5 text-[10px] text-white/80 transition-colors"
                  >
                    封面
                  </button>
                )}
              </div>
            </div>
            {dur > 0 && (
              <div className="shrink-0 flex items-center gap-2 pt-1">
                <span className="text-[10px] font-mono text-muted-foreground w-10 text-right shrink-0">
                  {fmtTime(localTime)}
                </span>
                <input
                  type="range"
                  min={0}
                  max={dur}
                  step={0.1}
                  value={localTime}
                  onChange={(e) => seekAndCapture(+e.target.value)}
                  className="flex-1 h-1.5 accent-primary cursor-pointer"
                />
                <span className="text-[10px] font-mono text-muted-foreground w-10 shrink-0">
                  {fmtTime(dur)}
                </span>
              </div>
            )}
          </div>

          {/* 右栏：配置 + XHS 输出 */}
          <div className="flex-1 min-w-0 overflow-y-auto p-5 space-y-5">
            {/* 封面标题 */}
            <div className="space-y-1.5">
              <label className="text-xs font-medium text-foreground">封面标题</label>
              <input
                type="text"
                value={coverTitle}
                onChange={(e) => { setCoverTitle(e.target.value); _saveContentField({ coverTitle: e.target.value }); }}
                placeholder="输入封面标题，或用 AI 推荐"
                className="w-full h-8 px-3 rounded-md border border-border bg-secondary text-xs text-foreground placeholder:text-muted-foreground/50 focus:outline-none focus:ring-1 focus:ring-primary"
              />

              {/* AI 推荐封面标题 */}
              <div className="space-y-1.5 pt-0.5">
                <div className="flex items-center gap-2">
                  <ModelPickerPopover config={coverTitleConfig} updateConfig={updateCoverTitleConfig} />
                  <Button
                    size="sm"
                    variant="outline"
                    className="h-6 text-[10px] px-2 gap-1"
                    onClick={handleGenerateCoverTitles}
                    disabled={coverTitleLoading}
                  >
                    {coverTitleLoading ? (
                      <><Loader2 className="w-3 h-3 animate-spin" />生成中…</>
                    ) : (
                      <><Wand2 className="w-3 h-3" />{coverTitleSuggestions.length > 0 ? "换一批" : "AI 推荐"}</>
                    )}
                  </Button>
                </div>
                {coverTitleSuggestions.length > 0 && (
                  <div className="flex flex-col gap-1">
                    {coverTitleSuggestions.map((t, i) => (
                      <button
                        key={i}
                        onClick={() => { setCoverTitle(t); _saveContentField({ coverTitle: t }); }}
                        className="text-left w-full px-2.5 py-1.5 rounded-md border border-border/60 bg-secondary/40 hover:bg-primary/10 hover:border-primary/40 text-xs text-foreground transition-colors"
                      >
                        {t}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            </div>

            {/* 封面风格 */}
            <div className="space-y-1.5">
              <label className="text-xs font-medium text-foreground">封面风格</label>
              <div className="grid grid-cols-3 gap-2">
                {COVER_STYLES.map((s) => (
                  <button
                    key={s.id}
                    onClick={() => setSelectedStyle(s.id === selectedStyle ? null : s.id)}
                    className={cn(
                      "flex flex-col overflow-hidden rounded-lg border transition-all",
                      selectedStyle === s.id
                        ? "border-primary ring-1 ring-primary"
                        : "border-border hover:border-primary/50"
                    )}
                  >
                    <div className="relative w-full bg-muted overflow-hidden" style={{ aspectRatio: "3/4" }}>
                      <img
                        src={s.example}
                        alt={s.label}
                        className="absolute inset-0 w-full h-full object-cover object-top"
                      />
                      {selectedStyle === s.id && (
                        <div className="absolute inset-0 bg-primary/20 flex items-center justify-center">
                          <div className="w-5 h-5 rounded-full bg-primary flex items-center justify-center">
                            <svg className="w-3 h-3 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
                              <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                            </svg>
                          </div>
                        </div>
                      )}
                    </div>
                    <div className={cn(
                      "py-1 text-center text-[10px] font-medium leading-tight",
                      selectedStyle === s.id ? "bg-primary/10 text-primary" : "bg-secondary/60 text-muted-foreground"
                    )}>
                      {s.label}
                    </div>
                  </button>
                ))}
              </div>
            </div>

            {/* AI 生成封面 */}
            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <label className="text-xs font-medium text-foreground">AI 生成封面</label>
                <ImgModelPicker value={imgProvider} onChange={setImgProvider} disabled={coverImgLoading} />
              </div>
              <div>
                <Button
                  size="sm"
                  variant="outline"
                  className="h-6 text-[10px] px-2 gap-1 w-full"
                  onClick={handleGenerateCoverImage}
                  disabled={coverImgLoading}
                >
                  {coverImgLoading ? (
                    <><Loader2 className="w-3 h-3 animate-spin" />生成中…</>
                  ) : (
                    <><Wand2 className="w-3 h-3" />生成封面</>
                  )}
                </Button>
              </div>
              {generatedCoverUrl && (
                <div className="text-[10px] text-muted-foreground/60 text-center">
                  ↑ 生成结果已显示在左侧预览区
                </div>
              )}
            </div>

            <div className="border-t border-border/40" />

            {/* 小红书文案生成：模型选择 + 生成按钮 */}
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <label className="text-xs font-medium text-foreground">小红书爆款文案</label>
                <ModelPickerPopover config={xhsConfig} updateConfig={updateXhsConfig} disabled={xhsLoading} />
              </div>
              <Button
                size="sm"
                className="w-full text-xs gap-1.5"
                onClick={handleGenerateXhs}
                disabled={xhsLoading}
              >
                {xhsLoading ? (
                  <><Loader2 className="w-3 h-3 animate-spin" />生成中…</>
                ) : (
                  <><Sparkles className="w-3 h-3" />生成小红书标题与简介</>
                )}
              </Button>

              {/* 生成结果 */}
              {(xhsTitles.length > 0 || xhsDesc) && (
                <div className="space-y-2">
                  {xhsTitles.length > 0 && (
                    <div className="space-y-1">
                      <span className="text-[10px] text-muted-foreground font-medium">爆款标题（3选1）</span>
                      <div className="space-y-1.5 mt-1">
                        {xhsTitles.map((t, i) => (
                          <div key={i} className="flex items-start gap-2 rounded-md bg-secondary/50 border border-border/40 px-3 py-2">
                            <span className="text-[10px] text-muted-foreground/50 shrink-0 mt-0.5">#{i + 1}</span>
                            <span className="text-xs text-foreground leading-relaxed flex-1">{t}</span>
                            <button
                              onClick={() => copyText(t, `标题${i + 1}`)}
                              className="shrink-0 flex items-center gap-1 text-[10px] text-muted-foreground/60 hover:text-primary transition-colors"
                            >
                              <ClipboardCopy className="w-3 h-3" />
                            </button>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                  {xhsDesc && (
                    <div className="space-y-1">
                      <div className="flex items-center justify-between">
                        <span className="text-[10px] text-muted-foreground font-medium">视频简介</span>
                        <button
                          onClick={() => copyText(xhsDesc, "简介")}
                          className="flex items-center gap-1 text-[10px] text-muted-foreground/60 hover:text-primary transition-colors"
                        >
                          <ClipboardCopy className="w-3 h-3" />
                          复制
                        </button>
                      </div>
                      <div className="rounded-md bg-secondary/50 border border-border/40 px-3 py-2 text-xs text-foreground leading-relaxed min-h-[60px] whitespace-pre-wrap">
                        {xhsDesc}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>

        <div className="px-5 py-4 border-t border-border flex justify-end shrink-0">
          <Button size="sm" onClick={onClose} className="text-xs">完成</Button>
        </div>
      </SheetContent>
    </Sheet>
  );
}

// alignment 对应 ASS numpad 布局的位置映射
const ALIGNMENT_LABELS: Record<number, string> = {
  7: "↖", 8: "↑", 9: "↗",
  4: "←", 5: "·", 6: "→",
  1: "↙", 2: "↓", 3: "↘",
};

// ============================================================
// SubtitlePreviewBox — 预览 + 可拖拽安全区边框
// ============================================================
function SubtitlePreviewBox({
  style,
  frameDataUrl,
  videoAspectRatio,
  onStyleChange,
}: {
  style: SubtitleStyle;
  frameDataUrl?: string | null;
  videoAspectRatio?: number;
  onStyleChange?: (patch: Partial<SubtitleStyle>) => void;
}) {
  const wrapperRef = useRef<HTMLDivElement>(null);
  const [wrapSize, setWrapSize] = useState({ w: 400, h: 300 });

  useEffect(() => {
    if (!wrapperRef.current) return;
    const obs = new ResizeObserver((entries) => {
      for (const e of entries) setWrapSize({ w: e.contentRect.width, h: e.contentRect.height });
    });
    obs.observe(wrapperRef.current);
    return () => obs.disconnect();
  }, []);

  const ar = videoAspectRatio && videoAspectRatio > 0 ? videoAspectRatio : 16 / 9;
  const refW = ar >= 1 ? 1920 : 1080;
  const refH = ar >= 1 ? 1080 : 1920;
  const previewWidth = wrapSize.h > 0 ? Math.min(wrapSize.w, wrapSize.h * ar) : wrapSize.w;
  const previewHeight = previewWidth / ar;
  const scale = previewWidth / refW;
  const isBottomAligned = style.alignment <= 3;
  const isTopAligned = style.alignment >= 7;

  // ---- 字幕文字框 2D 拖拽 ----
  const handleStripDrag = (e: React.MouseEvent) => {
    if (!onStyleChange) return;
    e.preventDefault();
    e.stopPropagation();
    // 用局部变量快照所有起始值，避免 closure 陷阱
    const align   = style.alignment ?? 2;
    const isBot   = align <= 3;
    const isTop   = align >= 7;
    const startX  = e.clientX;
    const startY  = e.clientY;
    const startML = style.margin_l   ?? 0;
    const startMR = style.margin_r   ?? 0;
    const startMB = style.margin_bottom ?? 80;
    const startMT = style.margin_top    ?? 40;
    const maxH    = Math.floor(refH * 0.85);
    const maxW    = Math.floor(refW * 0.8);
    const snapScale = scale; // 固定 scale，防止 resize 导致漂移
    document.body.style.userSelect = "none";

    const onMove = (ev: MouseEvent) => {
      // 换算为参考坐标系偏移（px）
      const dx = (ev.clientX - startX) / snapScale;
      const dy = (ev.clientY - startY) / snapScale;

      // 水平：等量平移，保持条带宽度不变
      const newL = Math.round(Math.max(0, Math.min(maxW, startML + dx)));
      const newR = Math.round(Math.max(0, Math.min(maxW, startMR - dx)));

      const updates: Partial<SubtitleStyle> = { margin_l: newL, margin_r: newR };
      if (isBot) {
        // 底部对齐：向下拖(dy>0) → margin_bottom 减小 → 文字下移
        updates.margin_bottom = Math.round(Math.max(0, Math.min(maxH, startMB - dy)));
      } else if (isTop) {
        // 顶部对齐：向下拖(dy>0) → margin_top 增大 → 文字下移
        updates.margin_top = Math.round(Math.max(0, Math.min(maxH, startMT + dy)));
      }
      onStyleChange(updates);
    };

    const onUp = () => {
      document.body.style.userSelect = "";
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };

    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  };

  const a = style.alignment;
  const col = a % 3;
  const textAlignCSS: React.CSSProperties["textAlign"] = col === 1 ? "left" : col === 0 ? "right" : "center";
  const textPosCSS: React.CSSProperties = {
    position: "absolute",
    left: style.margin_l * scale,
    right: style.margin_r * scale,
    textAlign: textAlignCSS,
    ...(a >= 7 ? { top: style.margin_top * scale }
      : a >= 4 ? { top: "50%", transform: "translateY(-50%)" }
      : { bottom: style.margin_bottom * scale }),
  };

  return (
    <div ref={wrapperRef} className="w-full h-full flex items-center justify-center">
    <div
      className="relative rounded-lg overflow-hidden border border-border/50 shrink-0"
      style={{
        width: previewWidth,
        height: previewHeight,
        background: frameDataUrl ? `url(${frameDataUrl}) center/cover no-repeat` : "#111",
      }}
    >
      {!frameDataUrl && (
        <div className="absolute inset-0 opacity-10 pointer-events-none"
          style={{
            backgroundImage: "linear-gradient(rgba(255,255,255,.06) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,.06) 1px, transparent 1px)",
            backgroundSize: `${previewWidth / 8}px ${previewWidth / 8}px`,
          }}
        />
      )}
      {frameDataUrl && <div className="absolute inset-0 bg-black/20 pointer-events-none" />}

      {/* 字幕文字 — 直接拖动定位，上下左右均可 */}
      <div
        style={{
          ...textPosCSS,
          fontSize: Math.max(8, style.font_size * scale),
          color: style.primary_color,
          fontWeight: style.bold ? "bold" : "normal",
          WebkitTextStroke: style.outline > 0 ? `${Math.max(0.5, style.outline * scale)}px ${style.outline_color}` : "0px transparent",
          textShadow: style.shadow > 0
            ? `${style.shadow * scale}px ${style.shadow * scale}px ${style.shadow * scale * 2}px rgba(0,0,0,0.8)`
            : "none",
          fontFamily: CHINESE_FONTS.find(f => f.value === style.font_name)?.css ?? style.font_name,
          lineHeight: 1.4,
          padding: "4px 6px",
          whiteSpace: "pre-wrap",
          wordBreak: "break-all",
          cursor: onStyleChange ? "move" : "default",
          userSelect: "none",
          outline: onStyleChange ? "2px solid rgba(250,180,50,0.7)" : "none",
          outlineOffset: 2,
          borderRadius: 2,
        }}
        onMouseDown={handleStripDrag}
      >
        这是一段字幕预览文字
      </div>
    </div>
    </div>
  );
}

// ============================================================
// SubtitleStyleEditor — 纯控件，不含预览框
// ============================================================
function SubtitleStyleEditor({
  style,
  onChange,
  disabled,
  videoAspectRatio,
}: {
  style: SubtitleStyle;
  onChange: (s: SubtitleStyle) => void;
  disabled?: boolean;
  videoAspectRatio?: number;
}) {
  const update = (patch: Partial<SubtitleStyle>) => onChange({ ...style, ...patch });
  const ar = videoAspectRatio && videoAspectRatio > 0 ? videoAspectRatio : 16 / 9;
  const refW = ar >= 1 ? 1920 : 1080;
  const refH = ar >= 1 ? 1080 : 1920;
  const maxH = Math.floor(refH * 0.8);
  const maxW = Math.floor(refW * 0.8);

  return (
    <div className="space-y-3">
      {/* 字体选择 */}
      <div className="space-y-0.5">
        <label className="text-[10px] text-muted-foreground">字体</label>
        <select
          value={style.font_name}
          disabled={disabled}
          onChange={(e) => update({ font_name: e.target.value })}
          className="w-full h-7 px-2 rounded-md border border-border bg-secondary text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-primary disabled:opacity-50"
          style={{ fontFamily: CHINESE_FONTS.find(f => f.value === style.font_name)?.css ?? style.font_name }}
        >
          {CHINESE_FONTS.map((f) => (
            <option key={f.value} value={f.value} style={{ fontFamily: f.css }}>{f.label}</option>
          ))}
        </select>
      </div>

      {/* 控件网格 */}
      <div className="grid grid-cols-2 gap-x-3 gap-y-2">
        <div className="space-y-0.5">
          <label className="text-[10px] text-muted-foreground">字体大小 ({style.font_size}px)</label>
          <input type="range" min={16} max={100} step={1}
            value={style.font_size} disabled={disabled}
            onChange={(e) => update({ font_size: +e.target.value })}
            className="w-full h-1.5 accent-primary"
          />
        </div>
        <div className="space-y-0.5">
          <label className="text-[10px] text-muted-foreground">描边厚度 ({style.outline})</label>
          <input type="range" min={0} max={4} step={0.5}
            value={style.outline} disabled={disabled}
            onChange={(e) => update({ outline: +e.target.value })}
            className="w-full h-1.5 accent-primary"
          />
        </div>
        <div className="space-y-0.5">
          <label className="text-[10px] text-muted-foreground">文字颜色</label>
          <div className="flex items-center gap-1.5">
            <input type="color" value={style.primary_color} disabled={disabled}
              onChange={(e) => update({ primary_color: e.target.value })}
              className="w-6 h-6 rounded border border-border cursor-pointer p-0"
            />
            <span className="text-[10px] font-mono text-muted-foreground">{style.primary_color}</span>
          </div>
        </div>
        <div className="space-y-0.5">
          <label className="text-[10px] text-muted-foreground">描边颜色</label>
          <div className="flex items-center gap-1.5">
            <input type="color" value={style.outline_color} disabled={disabled}
              onChange={(e) => update({ outline_color: e.target.value })}
              className="w-6 h-6 rounded border border-border cursor-pointer p-0"
            />
            <span className="text-[10px] font-mono text-muted-foreground">{style.outline_color}</span>
          </div>
        </div>
        <div className="space-y-0.5">
          <label className="text-[10px] text-muted-foreground">阴影 ({style.shadow})</label>
          <input type="range" min={0} max={2} step={0.5}
            value={style.shadow} disabled={disabled}
            onChange={(e) => update({ shadow: +e.target.value })}
            className="w-full h-1.5 accent-primary"
          />
        </div>
        <div className="space-y-0.5">
          <label className="text-[10px] text-muted-foreground">加粗</label>
          <label className="flex items-center gap-1.5 cursor-pointer">
            <input type="checkbox" checked={style.bold} disabled={disabled}
              onChange={(e) => update({ bold: e.target.checked })}
              className="rounded border-border accent-primary w-3 h-3"
            />
            <span className="text-[10px] text-muted-foreground">{style.bold ? "已开启" : "关闭"}</span>
          </label>
        </div>
      </div>

      {/* 位置选择器（九宫格） */}
      <div className="space-y-1">
        <label className="text-[10px] text-muted-foreground">字幕位置</label>
        <div className="inline-grid grid-cols-3 gap-0.5">
          {[7, 8, 9, 4, 5, 6, 1, 2, 3].map((a) => (
            <button
              key={a}
              disabled={disabled}
              onClick={() => update({ alignment: a })}
              className={cn(
                "w-7 h-7 rounded text-[11px] font-medium transition-colors border",
                style.alignment === a
                  ? "bg-primary text-primary-foreground border-primary"
                  : "bg-secondary text-muted-foreground border-border hover:bg-secondary/80"
              )}
            >
              {ALIGNMENT_LABELS[a]}
            </button>
          ))}
        </div>
      </div>

      {/* 边距 */}
      <div className="grid grid-cols-2 gap-x-3 gap-y-2">
        <div className="space-y-0.5">
          <label className="text-[10px] text-muted-foreground">上边距 ({style.margin_top}px)</label>
          <input type="range" min={0} max={maxH} step={5}
            value={style.margin_top} disabled={disabled}
            onChange={(e) => {
              const newVal = +e.target.value;
              update({ margin_top: newVal });
            }}
            className="w-full h-1.5 accent-primary"
          />
        </div>
        <div className="space-y-0.5">
          <label className="text-[10px] text-muted-foreground">下边距 ({style.margin_bottom}px)</label>
          <input type="range" min={0} max={maxH} step={5}
            value={style.margin_bottom} disabled={disabled}
            onChange={(e) => update({ margin_bottom: +e.target.value })}
            className="w-full h-1.5 accent-primary"
          />
        </div>
        <div className="space-y-0.5">
          <label className="text-[10px] text-muted-foreground">左边距 ({style.margin_l}px)</label>
          <input type="range" min={0} max={maxW} step={5}
            value={style.margin_l} disabled={disabled}
            onChange={(e) => update({ margin_l: +e.target.value })}
            className="w-full h-1.5 accent-primary"
          />
        </div>
        <div className="space-y-0.5">
          <label className="text-[10px] text-muted-foreground">右边距 ({style.margin_r}px)</label>
          <input type="range" min={0} max={maxW} step={5}
            value={style.margin_r} disabled={disabled}
            onChange={(e) => update({ margin_r: +e.target.value })}
            className="w-full h-1.5 accent-primary"
          />
        </div>
      </div>

      {/* 字幕行数上限 */}
      <div className="space-y-0.5">
        <label className="text-[10px] text-muted-foreground">
          每张字幕卡行数上限&nbsp;
          <span className="font-mono">
            {style.max_lines === 0 ? "（不限制）" : `≤ ${style.max_lines} 行`}
          </span>
        </label>
        <input
          type="range"
          min={0}
          max={5}
          step={1}
          value={style.max_lines}
          disabled={disabled}
          onChange={(e) => update({ max_lines: +e.target.value })}
          className="w-full h-1.5 accent-primary"
        />
        <p className="text-[9px] text-muted-foreground leading-tight">
          {(() => {
            if (style.max_lines === 0) return "不限制行数，字幕不会自动拆卡";
            const available = refW - style.margin_l - style.margin_r;
            const perLine = Math.max(1, Math.floor(available / style.font_size * 0.85));
            return `约每行 ${perLine} 字，超过 ${style.max_lines * perLine} 字时自动拆成两张字幕卡`;
          })()}
        </p>
      </div>
    </div>
  );
}

// ============================================================
// SubtitleConfigDialog（弹窗形式的字幕样式配置）
// ============================================================
function SubtitleConfigDialog({
  open,
  onClose,
  style,
  onChange,
  frameDataUrl,
  videoAspectRatio,
  videoDuration,
  videoCurrentTime,
  taskId,
}: {
  open: boolean;
  onClose: () => void;
  style: SubtitleStyle;
  onChange: (s: SubtitleStyle) => void;
  frameDataUrl: string | null;
  videoAspectRatio: number;
  videoDuration: number;
  videoCurrentTime: number;
  taskId: string;
}) {
  const [localFrame, setLocalFrame] = useState<string | null>(frameDataUrl);
  const [localTime, setLocalTime] = useState(videoCurrentTime);

  // 弹窗打开时同步外部传入的初始帧和时间
  useEffect(() => {
    if (open) {
      setLocalFrame(frameDataUrl);
      setLocalTime(videoCurrentTime);
    }
  }, [open, frameDataUrl, videoCurrentTime]);

  // 用后端 FFmpeg 抽帧，避免 canvas CORS 限制
  const captureFrameAt = useCallback((t: number) => {
    // 加时间戳防止浏览器缓存相同 t 的旧帧
    setLocalFrame(`http://localhost:8000/api/tasks/${taskId}/frame?t=${t.toFixed(3)}&_=${Date.now()}`);
  }, [taskId]);

  const seekAndCapture = useCallback((t: number) => {
    setLocalTime(t);
    // 同步主播放器位置
    const video = document.querySelector("video") as HTMLVideoElement | null;
    if (video) video.currentTime = t;
    // 直接请求后端抽帧，不需要等待 seeked 事件
    captureFrameAt(t);
  }, [captureFrameAt]);

  const fmtTime = (s: number) => {
    const mm = Math.floor(s / 60);
    const ss = Math.floor(s % 60);
    return `${String(mm).padStart(2, "0")}:${String(ss).padStart(2, "0")}`;
  };

  return (
    <Sheet open={open} onOpenChange={v => !v && onClose()}>
      <SheetContent
        side="right"
        className="w-[900px] sm:max-w-[900px] flex flex-col p-0 gap-0 bg-card border-l border-border"
      >
        {/* 顶部标题栏 */}
        <SheetHeader className="px-5 pt-5 pb-4 border-b border-border shrink-0">
          <SheetTitle className="flex items-center gap-2 text-base">
            <SlidersHorizontal className="w-4 h-4 text-primary" />
            字幕样式配置
          </SheetTitle>
          <SheetDescription className="text-xs text-muted-foreground">
            拖拽预览框边线或右侧滑块调整安全边距，拖动时间轴切换预览帧
          </SheetDescription>
        </SheetHeader>

        {/* 左右双栏主内容 */}
        <div className="flex flex-1 min-h-0 overflow-hidden">
          {/* 左栏：预览帧 + 时间轴 */}
          <div className="flex flex-col gap-2 p-4 border-r border-border" style={{ width: "52%", minWidth: 0 }}>
            <div className="flex-1 min-h-0 flex items-center justify-center">
              <SubtitlePreviewBox
                style={style}
                frameDataUrl={localFrame}
                videoAspectRatio={videoAspectRatio}
                onStyleChange={(patch) => onChange({ ...style, ...patch })}
              />
            </div>
            {/* 时间轴（置于预览框下方） */}
            {videoDuration > 0 && (
              <div className="shrink-0 flex items-center gap-2 pt-1">
                <span className="text-[10px] font-mono text-muted-foreground w-10 text-right shrink-0">
                  {fmtTime(localTime)}
                </span>
                <input
                  type="range"
                  min={0}
                  max={videoDuration}
                  step={0.1}
                  value={localTime}
                  onChange={(e) => seekAndCapture(+e.target.value)}
                  className="flex-1 h-1.5 accent-primary cursor-pointer"
                />
                <span className="text-[10px] font-mono text-muted-foreground w-10 shrink-0">
                  {fmtTime(videoDuration)}
                </span>
              </div>
            )}
          </div>

          {/* 右栏：样式控件（可滚动） */}
          <div className="flex-1 min-w-0 overflow-y-auto p-5">
            <SubtitleStyleEditor
              style={style}
              onChange={onChange}
              videoAspectRatio={videoAspectRatio}
            />
          </div>
        </div>

        {/* 底部操作栏 */}
        <div className="px-5 py-4 border-t border-border flex justify-end shrink-0">
          <Button size="sm" onClick={onClose} className="text-xs">
            完成
          </Button>
        </div>
      </SheetContent>
    </Sheet>
  );
}

// ============================================================
// HookConfigDialog（Hook 字幕样式配置，Sheet 面板）
// ============================================================
const HOOK_STYLE_LS_KEY = "gc_hook_style";

export const DEFAULT_HOOK_STYLE: Omit<HookConfig, "enabled" | "text"> = {
  duration: 3.0,
  font_size: 48,
  color: "#FFFFFF",
  outline_color: "#000000",
  outline: 2.0,
  bold: false,
  h_align: "center",
  v_align: "top",
  x_pct: 50,
  y_pct: 8,
};

function HookPreviewBox({
  hookStyle,
  frameDataUrl,
  videoAspectRatio,
  onStyleChange,
}: {
  hookStyle: Omit<HookConfig, "enabled" | "text">;
  frameDataUrl?: string | null;
  videoAspectRatio?: number;
  onStyleChange?: (patch: Partial<Omit<HookConfig, "enabled" | "text">>) => void;
}) {
  const wrapperRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [wrapSize, setWrapSize] = useState({ w: 400, h: 300 });

  useEffect(() => {
    if (!wrapperRef.current) return;
    const obs = new ResizeObserver(entries => {
      for (const e of entries) setWrapSize({ w: e.contentRect.width, h: e.contentRect.height });
    });
    obs.observe(wrapperRef.current);
    return () => obs.disconnect();
  }, []);

  const ar = videoAspectRatio && videoAspectRatio > 0 ? videoAspectRatio : 16 / 9;
  const containerWidth = wrapSize.h > 0 ? Math.min(wrapSize.w, wrapSize.h * ar) : wrapSize.w;
  const containerHeight = containerWidth / ar;
  const previewFontSize = Math.max(10, hookStyle.font_size * containerWidth / (ar >= 1 ? 1920 : 1080));

  // 用 x_pct/y_pct 决定位置，没有时从 h_align/v_align 推导
  const xPct = hookStyle.x_pct ?? (hookStyle.h_align === "left" ? 8 : hookStyle.h_align === "right" ? 92 : 50);
  const yPct = hookStyle.y_pct ?? (hookStyle.v_align === "top" ? 8 : hookStyle.v_align === "bottom" ? 92 : 50);

  // ---- 和字幕一样，点文字直接拖动到任意位置 ----
  const handleTextDrag = (e: React.MouseEvent) => {
    if (!onStyleChange || !containerRef.current) return;
    e.preventDefault();
    e.stopPropagation();
    const rect = containerRef.current.getBoundingClientRect();
    document.body.style.userSelect = "none";

    const onMove = (ev: MouseEvent) => {
      const xP = Math.max(3, Math.min(97, (ev.clientX - rect.left) / rect.width * 100));
      const yP = Math.max(3, Math.min(97, (ev.clientY - rect.top) / rect.height * 100));
      onStyleChange({ x_pct: Math.round(xP * 10) / 10, y_pct: Math.round(yP * 10) / 10 });
    };

    const onUp = () => {
      document.body.style.userSelect = "";
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };

    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  };

  return (
    <div ref={wrapperRef} className="w-full h-full flex items-center justify-center">
    <div ref={containerRef} className="relative overflow-hidden rounded-lg bg-black shrink-0"
      style={{ width: containerWidth, height: containerHeight }}>
      {frameDataUrl ? (
        <img src={frameDataUrl} alt="preview" className="absolute inset-0 w-full h-full object-cover" />
      ) : (
        <div className="absolute inset-0 bg-gradient-to-br from-zinc-800 to-zinc-900" />
      )}

      {/* 标题文字 — 直接点击拖动，和字幕完全一样的交互 */}
      <div style={{
        position: "absolute",
        left: `${xPct}%`,
        top: `${yPct}%`,
        transform: "translate(-50%, -50%)",
        maxWidth: "92%",
        pointerEvents: onStyleChange ? "auto" : "none",
        cursor: onStyleChange ? "move" : "default",
        userSelect: "none",
      }}
        onMouseDown={handleTextDrag}
      >
        <span style={{
          display: "inline-block",
          fontSize: previewFontSize,
          color: hookStyle.color,
          fontWeight: hookStyle.bold ? "bold" : "normal",
          textShadow: hookStyle.outline > 0
            ? `0 0 ${hookStyle.outline * 2}px ${hookStyle.outline_color}, 0 0 ${hookStyle.outline * 4}px ${hookStyle.outline_color}`
            : "none",
          whiteSpace: "nowrap",
          padding: "0.2em 0.4em",
          lineHeight: 1.2,
          outline: onStyleChange ? "2px solid rgba(250,180,50,0.7)" : "none",
          outlineOffset: 2,
          borderRadius: 2,
        }}>
          示例标题文字
        </span>
      </div>
    </div>
    </div>
  );
}

function HookConfigDialog({
  open,
  onClose,
  hookStyle,
  onChange,
  frameDataUrl,
  videoAspectRatio,
  videoDuration,
  videoCurrentTime,
  taskId,
}: {
  open: boolean;
  onClose: () => void;
  hookStyle: Omit<HookConfig, "enabled" | "text">;
  onChange: (s: Omit<HookConfig, "enabled" | "text">) => void;
  frameDataUrl: string | null;
  videoAspectRatio: number;
  videoDuration: number;
  videoCurrentTime: number;
  taskId: string;
}) {
  const [localFrame, setLocalFrame] = useState<string | null>(frameDataUrl);
  const [localTime, setLocalTime] = useState(videoCurrentTime);

  useEffect(() => {
    if (open) {
      setLocalFrame(frameDataUrl);
      setLocalTime(videoCurrentTime);
    }
  }, [open, frameDataUrl, videoCurrentTime]);

  const captureFrameAt = useCallback((t: number) => {
    setLocalFrame(`http://localhost:8000/api/tasks/${taskId}/frame?t=${t.toFixed(3)}&_=${Date.now()}`);
  }, [taskId]);

  const seekAndCapture = useCallback((t: number) => {
    setLocalTime(t);
    const video = document.querySelector("video") as HTMLVideoElement | null;
    if (video) video.currentTime = t;
    captureFrameAt(t);
  }, [captureFrameAt]);

  const update = (patch: Partial<Omit<HookConfig, "enabled" | "text">>) => onChange({ ...hookStyle, ...patch });

  const fmtTime = (s: number) => {
    const mm = Math.floor(s / 60);
    const ss = Math.floor(s % 60);
    return `${String(mm).padStart(2, "0")}:${String(ss).padStart(2, "0")}`;
  };

  // 九宫格位置映射
  const POSITION_GRID: Array<[string, HookConfig["h_align"], HookConfig["v_align"]]> = [
    ["↖", "left", "top"],   ["↑", "center", "top"],   ["↗", "right", "top"],
    ["←", "left", "middle"], ["·", "center", "middle"], ["→", "right", "middle"],
    ["↙", "left", "bottom"], ["↓", "center", "bottom"], ["↘", "right", "bottom"],
  ];

  return (
    <Sheet open={open} onOpenChange={v => !v && onClose()}>
      <SheetContent
        side="right"
        className="w-[820px] sm:max-w-[820px] flex flex-col p-0 gap-0 bg-card border-l border-border"
      >
        <SheetHeader className="px-5 pt-5 pb-4 border-b border-border shrink-0">
          <SheetTitle className="flex items-center gap-2 text-base">
            <SlidersHorizontal className="w-4 h-4 text-primary" />
            Hook 字幕样式配置
          </SheetTitle>
          <SheetDescription className="text-xs text-muted-foreground">
            配置开场标题字幕的字体、颜色、位置和显示时长
          </SheetDescription>
        </SheetHeader>

        <div className="flex flex-1 min-h-0 overflow-hidden">
          {/* 左栏：预览 + 时间轴 */}
          <div className="flex flex-col gap-2 p-4 border-r border-border" style={{ width: "52%", minWidth: 0 }}>
            <div className="flex-1 min-h-0 flex items-center justify-center">
              <HookPreviewBox
                hookStyle={hookStyle}
                frameDataUrl={localFrame}
                videoAspectRatio={videoAspectRatio}
                onStyleChange={update}
              />
            </div>
            {videoDuration > 0 && (
              <div className="shrink-0 flex items-center gap-2 pt-1">
                <span className="text-[10px] font-mono text-muted-foreground w-10 text-right shrink-0">
                  {fmtTime(localTime)}
                </span>
                <input
                  type="range"
                  min={0}
                  max={videoDuration}
                  step={0.1}
                  value={localTime}
                  onChange={(e) => seekAndCapture(+e.target.value)}
                  className="flex-1 h-1.5 accent-primary cursor-pointer"
                />
                <span className="text-[10px] font-mono text-muted-foreground w-10 shrink-0">
                  {fmtTime(videoDuration)}
                </span>
              </div>
            )}
          </div>

          {/* 右栏：控件 */}
          <div className="flex-1 min-w-0 overflow-y-auto p-5">
            <div className="space-y-4">
              {/* 显示时长 */}
              <div className="space-y-0.5">
                <label className="text-[10px] text-muted-foreground">显示时长 ({hookStyle.duration}s)</label>
                <input type="range" min={0.5} max={10} step={0.5}
                  value={hookStyle.duration}
                  onChange={(e) => update({ duration: +e.target.value })}
                  className="w-full h-1.5 accent-primary"
                />
              </div>

              {/* 字体大小 */}
              <div className="space-y-0.5">
                <label className="text-[10px] text-muted-foreground">字体大小 ({hookStyle.font_size}px)</label>
                <input type="range" min={24} max={120} step={2}
                  value={hookStyle.font_size}
                  onChange={(e) => update({ font_size: +e.target.value })}
                  className="w-full h-1.5 accent-primary"
                />
              </div>

              {/* 颜色 */}
              <div className="grid grid-cols-2 gap-x-3 gap-y-2">
                <div className="space-y-0.5">
                  <label className="text-[10px] text-muted-foreground">文字颜色</label>
                  <div className="flex items-center gap-1.5">
                    <input type="color" value={hookStyle.color}
                      onChange={(e) => update({ color: e.target.value })}
                      className="w-6 h-6 rounded border border-border cursor-pointer p-0"
                    />
                    <span className="text-[10px] font-mono text-muted-foreground">{hookStyle.color}</span>
                  </div>
                </div>
                <div className="space-y-0.5">
                  <label className="text-[10px] text-muted-foreground">描边颜色</label>
                  <div className="flex items-center gap-1.5">
                    <input type="color" value={hookStyle.outline_color}
                      onChange={(e) => update({ outline_color: e.target.value })}
                      className="w-6 h-6 rounded border border-border cursor-pointer p-0"
                    />
                    <span className="text-[10px] font-mono text-muted-foreground">{hookStyle.outline_color}</span>
                  </div>
                </div>
              </div>

              {/* 描边厚度 + 加粗 */}
              <div className="grid grid-cols-2 gap-x-3 gap-y-2">
                <div className="space-y-0.5">
                  <label className="text-[10px] text-muted-foreground">描边厚度 ({hookStyle.outline})</label>
                  <input type="range" min={0} max={6} step={0.5}
                    value={hookStyle.outline}
                    onChange={(e) => update({ outline: +e.target.value })}
                    className="w-full h-1.5 accent-primary"
                  />
                </div>
                <div className="space-y-0.5">
                  <label className="text-[10px] text-muted-foreground">加粗</label>
                  <label className="flex items-center gap-1.5 cursor-pointer">
                    <input type="checkbox" checked={hookStyle.bold}
                      onChange={(e) => update({ bold: e.target.checked })}
                      className="rounded border-border accent-primary w-3 h-3"
                    />
                    <span className="text-[10px] text-muted-foreground">{hookStyle.bold ? "已开启" : "关闭"}</span>
                  </label>
                </div>
              </div>

              {/* 位置（九宫格） */}
              <div className="space-y-1">
                <label className="text-[10px] text-muted-foreground">文字位置</label>
                <div className="inline-grid grid-cols-3 gap-0.5">
                  {POSITION_GRID.map(([label, ha, va]) => {
                    // 九宫格高亮：基于当前 x_pct/y_pct 所在区域
                    const cx = hookStyle.x_pct ?? (hookStyle.h_align === "left" ? 8 : hookStyle.h_align === "right" ? 92 : 50);
                    const cy = hookStyle.y_pct ?? (hookStyle.v_align === "top" ? 8 : hookStyle.v_align === "bottom" ? 92 : 50);
                    const curH = cx < 35 ? "left" : cx > 65 ? "right" : "center";
                    const curV = cy < 35 ? "top" : cy > 65 ? "bottom" : "middle";
                    const active = curH === ha && curV === va;
                    const xp = ha === "left" ? 8 : ha === "right" ? 92 : 50;
                    const yp = va === "top" ? 8 : va === "bottom" ? 92 : 50;
                    return (
                      <button
                        key={`${ha}-${va}`}
                        onClick={() => update({ h_align: ha, v_align: va, x_pct: xp, y_pct: yp })}
                        className={cn(
                          "w-8 h-8 rounded text-sm font-medium transition-colors border",
                          active
                            ? "bg-primary text-primary-foreground border-primary"
                            : "bg-secondary text-muted-foreground border-border hover:bg-secondary/80"
                        )}
                      >{label}</button>
                    );
                  })}
                </div>
              </div>
            </div>
          </div>
        </div>

        <div className="px-5 py-4 border-t border-border flex justify-end shrink-0">
          <Button size="sm" onClick={onClose} className="text-xs">完成</Button>
        </div>
      </SheetContent>
    </Sheet>
  );
}

// ============================================================
// 金句开场对话框
// ============================================================
function GoldenQuoteDialog({
  open,
  onClose,
  task,
  apiKey,
  provider,
  claudeModel,
  ollamaModel,
  ollamaBaseUrl,
  onSaved,
  onSeek,
  // 候选列表由父组件持有，避免 Sheet 关闭后状态丢失
  candidates,
  setCandidates,
  analyzed,
  setAnalyzed,
}: {
  open: boolean;
  onClose: () => void;
  task: Task;
  apiKey?: string;
  provider?: string;
  claudeModel?: string;
  ollamaModel?: string;
  ollamaBaseUrl?: string;
  onSaved: (order: string[]) => void;
  onSeek: (time: number) => void;
  candidates: GoldenQuoteCandidate[];
  setCandidates: React.Dispatch<React.SetStateAction<GoldenQuoteCandidate[]>>;
  analyzed: boolean;
  setAnalyzed: React.Dispatch<React.SetStateAction<boolean>>;
}) {
  const [loading, setLoading] = useState(false);
  // 已选中的 segment_id 集合
  const [selected, setSelected] = useState<Set<string>>(new Set(task.golden_quote_order || []));
  // 已排好序的选中列表
  const [ordered, setOrdered] = useState<string[]>(task.golden_quote_order || []);
  const [saving, setSaving] = useState(false);
  // 右栏当前查看上下文的候选 ID
  const [activeCtxId, setActiveCtxId] = useState<string | null>(null);

  // 仅在 sheet 从关闭→打开时同步已选状态；candidates/analyzed 由父组件持有无需重置
  useEffect(() => {
    if (open) {
      const existing = task.golden_quote_order || [];
      setSelected(new Set(existing));
      setOrdered(existing);
      setActiveCtxId(null);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const handleAnalyze = async () => {
    setLoading(true);
    try {
      const results = await suggestGoldenQuotes(task.id, {
        provider: (provider as "claude" | "ollama") || "claude",
        claude_api_key: apiKey,
        claude_model: claudeModel,
        ollama_model: ollamaModel,
        ollama_base_url: ollamaBaseUrl,
      });
      setCandidates(results);
      setAnalyzed(true);
    } catch (e: unknown) {
      const err = e as Error;
      toast.error(`金句分析失败: ${err.message || "未知错误"}`);
    } finally {
      setLoading(false);
    }
  };

  const toggleSelect = (segId: string) => {
    setSelected(prev => {
      const next = new Set(prev);
      if (next.has(segId)) {
        next.delete(segId);
        setOrdered(o => o.filter(id => id !== segId));
      } else {
        next.add(segId);
        setOrdered(o => [...o, segId]);
      }
      return next;
    });
  };

  const moveUp = (segId: string) => {
    setOrdered(prev => {
      const idx = prev.indexOf(segId);
      if (idx <= 0) return prev;
      const next = [...prev];
      [next[idx - 1], next[idx]] = [next[idx], next[idx - 1]];
      return next;
    });
  };

  const moveDown = (segId: string) => {
    setOrdered(prev => {
      const idx = prev.indexOf(segId);
      if (idx < 0 || idx >= prev.length - 1) return prev;
      const next = [...prev];
      [next[idx], next[idx + 1]] = [next[idx + 1], next[idx]];
      return next;
    });
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      await saveGoldenQuoteOrder(task.id, ordered);
      onSaved(ordered);
      toast.success(
        ordered.length > 0
          ? `已设置 ${ordered.length} 个金句开场片段`
          : "已清除金句开场设置"
      );
      onClose();
    } catch (e: unknown) {
      const err = e as Error;
      toast.error(`保存失败: ${err.message}`);
    } finally {
      setSaving(false);
    }
  };

  // segment 索引：id → 在 audit_segments 中的位置，用于查找前后句
  const segMap = Object.fromEntries(task.audit_segments.map(s => [s.id, s]));
  const segIndexMap = Object.fromEntries(task.audit_segments.map((s, i) => [s.id, i]));

  // 找到距某片段最近的已保留的前/后 N 个片段（跳过 delete 段，最多取 count 个）
  const _keptActions = new Set(["keep", "subtitle_fix", "text_fix", "merge_next"]);
  const findAdjacentKeptN = (segId: string, direction: "prev" | "next", count: number): Segment[] => {
    const idx = segIndexMap[segId];
    if (idx === undefined) return [];
    const step = direction === "next" ? 1 : -1;
    const results: Segment[] = [];
    for (let i = idx + step; i >= 0 && i < task.audit_segments.length; i += step) {
      const s = task.audit_segments[i];
      if (_keptActions.has(s.action)) {
        results.push(s);
        if (results.length >= count) break;
      }
    }
    // prev 方向收集到的是「离目标由近到远」，展示时需要反转为时间顺序（远→近）
    return direction === "prev" ? results.reverse() : results;
  };

  // 第二栏只展示 AI 候选，不混入从上下文「+ 加入」的片段
  const displayCandidates: GoldenQuoteCandidate[] = [...candidates];

  // 加入单个片段到选中列表（不重复）
  const addSegToSelected = (segId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (selected.has(segId)) return;
    setSelected(prev => { const n = new Set(prev); n.add(segId); return n; });
    setOrdered(prev => [...prev, segId]);
  };

  // 从选中列表移除
  const removeSegFromSelected = (segId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    setSelected(prev => { const n = new Set(prev); n.delete(segId); return n; });
    setOrdered(prev => prev.filter(id => id !== segId));
  };

  // 说话人名称映射（与主字幕列表保持一致）
  const _SPEAKER_LABELS: Record<string, string> = { spk0: "主播", spk1: "嘉A", spk2: "嘉B", spk3: "嘉C" };
  const renderSpeakerBadge = (speaker?: string) => {
    if (!speaker) return null;
    const color = SPEAKER_COLORS[speaker] || SPEAKER_COLORS.default;
    return (
      <span
        className="inline-flex items-center h-[16px] px-1 rounded text-[9px] font-bold shrink-0 select-none"
        style={{ background: color, color: "rgba(0,0,0,0.60)" }}
      >
        {_SPEAKER_LABELS[speaker] ?? speaker}
      </span>
    );
  };

  // 上下文行渲染 helper
  const renderCtxRow = (seg: Segment, label: string) => (
    <div key={seg.id} className="flex items-start gap-2 px-3 py-1.5 rounded-md border border-border/40 bg-card/50">
      <span className="text-[10px] text-muted-foreground/50 shrink-0 w-6 text-right mt-0.5">{label}</span>
      <div className="flex-1 min-w-0 space-y-0.5">
        <div className="flex items-center gap-1.5">
          {renderSpeakerBadge(seg.speaker)}
          <span className="text-[10px] text-muted-foreground/40 font-mono">
            {formatTimestamp(seg.start)} · {(seg.end - seg.start).toFixed(1)}s
          </span>
        </div>
        <p className="text-[12px] text-muted-foreground/80 leading-snug">
          {(seg.display_text || seg.text || "").trim()}
        </p>
      </div>
      <button
        className={cn(
          "shrink-0 text-[10px] px-1.5 py-0.5 rounded border transition-colors mt-0.5",
          selected.has(seg.id)
            ? "border-destructive/40 text-destructive hover:bg-destructive/10"
            : "border-border text-muted-foreground hover:border-amber-500/40 hover:text-amber-400"
        )}
        onClick={(e) => selected.has(seg.id) ? removeSegFromSelected(seg.id, e) : addSegToSelected(seg.id, e)}
      >
        {selected.has(seg.id) ? "取消" : "+ 加入"}
      </button>
    </div>
  );

  // 右栏上下文数据
  const activeCtxCandidate = activeCtxId ? displayCandidates.find(c => c.segment_id === activeCtxId) : null;
  const ctxPrevSegs = activeCtxId ? findAdjacentKeptN(activeCtxId, "prev", 5) : [];
  const ctxNextSegs = activeCtxId ? findAdjacentKeptN(activeCtxId, "next", 5) : [];

  return (
    <Sheet open={open} onOpenChange={v => !v && onClose()}>
      <SheetContent
        side="right"
        className="w-[960px] sm:max-w-[960px] flex flex-col p-0 gap-0 bg-card border-l border-border"
      >
        {/* 顶部标题栏 */}
        <SheetHeader className="px-5 pt-5 pb-4 border-b border-border shrink-0">
          <SheetTitle className="flex items-center gap-2 text-base">
            <Crown className="w-5 h-5 text-amber-400" />
            金句开场设置
          </SheetTitle>
          <SheetDescription className="text-xs text-muted-foreground">
            选择最吸引人的片段放到视频最开头，制造悬念钩子，再正序播放全片。
          </SheetDescription>
        </SheetHeader>

        {/* 主体：三栏布局 */}
        <div className="flex flex-1 min-h-0">

          {/* 第一栏：开场顺序（最终结果） */}
          <div className="w-[200px] shrink-0 flex flex-col border-r border-border/50">
            <div className="px-4 py-3 border-b border-border/50 shrink-0">
              <p className="text-[11px] font-medium text-muted-foreground uppercase tracking-wider">
                开场顺序
              </p>
              <p className="text-[10px] text-muted-foreground/50 mt-0.5">
                勾选后在此调整顺序
              </p>
            </div>

            <div className="flex-1 overflow-y-auto px-3 py-2 space-y-1">
              {ordered.length > 0 ? (
                <>
                  {ordered.map((segId, idx) => {
                    const candidate = displayCandidates.find(c => c.segment_id === segId);
                    const seg = segMap[segId];
                    const text = candidate?.text || seg?.display_text || seg?.text || segId;
                    return (
                      <div
                        key={segId}
                        className="flex items-start gap-1.5 px-2 py-2 rounded-md border border-border/50 bg-secondary/20"
                      >
                        <span className="w-4 h-4 rounded-full bg-amber-500/20 text-amber-400 text-[10px] flex items-center justify-center font-bold flex-shrink-0 mt-0.5">
                          {idx + 1}
                        </span>
                        <div className="flex-1 min-w-0 space-y-0.5">
                          <div className="flex items-center gap-1.5 flex-wrap">
                            {renderSpeakerBadge(seg?.speaker)}
                            <span className="text-[10px] text-muted-foreground/50 font-mono">
                              {seg ? formatTimestamp(seg.start) : ""}
                            </span>
                          </div>
                          <p className="text-[11px] text-foreground leading-snug line-clamp-3">{text}</p>
                        </div>
                        <div className="flex flex-col gap-0.5 shrink-0">
                          <button
                            className="p-0.5 rounded hover:bg-secondary text-muted-foreground hover:text-foreground disabled:opacity-30"
                            onClick={() => moveUp(segId)}
                            disabled={idx === 0}
                          >
                            <ChevronUp className="w-3 h-3" />
                          </button>
                          <button
                            className="p-0.5 rounded hover:bg-secondary text-muted-foreground hover:text-foreground disabled:opacity-30"
                            onClick={() => moveDown(segId)}
                            disabled={idx === ordered.length - 1}
                          >
                            <ChevronDown className="w-3 h-3" />
                          </button>
                          <button
                            className="p-0.5 rounded hover:bg-destructive/20 text-muted-foreground hover:text-destructive"
                            onClick={() => toggleSelect(segId)}
                          >
                            <X className="w-3 h-3" />
                          </button>
                        </div>
                      </div>
                    );
                  })}
                  <p className="text-[10px] text-muted-foreground/50 pt-1 leading-snug">
                    导出时按此顺序播放金句，再正序播放剩余片段。
                  </p>
                </>
              ) : (
                <div className="flex flex-col items-center justify-center h-full gap-2 text-center py-8">
                  <p className="text-[11px] text-muted-foreground/50">
                    勾选中栏候选<br />加入开场
                  </p>
                </div>
              )}
            </div>

            {/* 底部操作按钮 */}
            <div className="px-3 py-3 border-t border-border/50 space-y-2 shrink-0">
              {ordered.length > 0 && (
                <button
                  className="w-full text-xs text-muted-foreground hover:text-destructive transition-colors text-center"
                  onClick={() => { setSelected(new Set()); setOrdered([]); }}
                >
                  清除全部
                </button>
              )}
              <Button
                size="sm"
                className="w-full bg-amber-500 text-black hover:bg-amber-400 gap-1.5"
                onClick={handleSave}
                disabled={saving}
              >
                {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Check className="w-3.5 h-3.5" />}
                确认 {ordered.length > 0 ? `(${ordered.length} 个)` : ""}
              </Button>
            </div>
          </div>

          {/* 第二栏：AI 识别的金句 */}
          <div className="flex-1 flex flex-col min-w-0 border-r border-border/50">
            {/* 分析按钮栏 */}
            <div className="flex items-center gap-3 px-4 py-3 border-b border-border/50 shrink-0">
              <Button
                size="sm"
                onClick={handleAnalyze}
                disabled={loading}
                className="bg-amber-500/20 text-amber-400 border border-amber-500/40 hover:bg-amber-500/30 gap-1.5 shrink-0"
              >
                {loading ? (
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                ) : (
                  <Sparkles className="w-3.5 h-3.5" />
                )}
                {loading ? "AI 分析中..." : analyzed ? "重新分析" : "AI 分析金句"}
              </Button>
              <span className="text-xs text-muted-foreground">
                {analyzed
                  ? `找到 ${candidates.length} 个候选`
                  : "点击让 AI 自动识别"}
              </span>
            </div>

            {/* 候选列表滚动区 */}
            <div className="flex-1 overflow-y-auto px-4 py-3 space-y-2">
              {displayCandidates.length > 0 ? (
                <>
                  <p className="text-[11px] text-muted-foreground/60 pb-1">
                    点击金句查看上下文，勾选加入开场
                  </p>
                  {displayCandidates.map((c) => {
                    const isChecked = selected.has(c.segment_id);
                    const isActive = activeCtxId === c.segment_id;
                    const duration = c.end - c.start;
                    const seg = segMap[c.segment_id];
                    const speakerColor = SPEAKER_COLORS[seg?.speaker || "default"] || SPEAKER_COLORS.default;
                    const speakerBg = hexToRgba(speakerColor, 0.12);

                    return (
                      <div
                        key={c.segment_id}
                        className={cn(
                          "rounded-lg border cursor-pointer transition-colors overflow-hidden",
                          isActive
                            ? "border-primary/60 ring-1 ring-primary/30"
                            : isChecked
                              ? "border-amber-500/50"
                              : "border-border hover:border-border/80"
                        )}
                        onClick={() => setActiveCtxId(prev => prev === c.segment_id ? null : c.segment_id)}
                      >
                        {/* 卡片顶部：说话人 + 时间信息行 */}
                        <div
                          className="flex items-center gap-2 px-3 py-1.5 border-b border-border/30"
                          style={{ background: speakerBg }}
                        >
                          {/* 复选框 */}
                          <div
                            className={cn(
                              "w-4 h-4 rounded border flex-shrink-0 flex items-center justify-center",
                              isChecked
                                ? "bg-amber-500 border-amber-500"
                                : "border-border/60"
                            )}
                            onClick={(e) => { e.stopPropagation(); toggleSelect(c.segment_id); }}
                          >
                            {isChecked && <Check className="w-2.5 h-2.5 text-black" />}
                          </div>
                          {renderSpeakerBadge(seg?.speaker)}
                          <span className="text-[11px] text-muted-foreground font-mono">
                            {formatTimestamp(c.start)} · {duration.toFixed(1)}s
                          </span>
                          <button
                            className="text-[11px] text-primary/70 hover:text-primary underline ml-auto shrink-0"
                            onClick={(e) => { e.stopPropagation(); onSeek(c.start); }}
                          >
                            预览
                          </button>
                        </div>

                        {/* 卡片主体：文本 + 理由 */}
                        <div
                          className={cn(
                            "px-3 py-2.5",
                            isActive ? "bg-primary/5" : isChecked ? "bg-amber-500/10" : "bg-secondary/20"
                          )}
                        >
                          <p className="text-sm text-foreground leading-relaxed">
                            {c.text}
                          </p>
                          {c.reason && (
                            <p className="text-[11px] text-amber-400/80 mt-1.5">
                              {c.reason}
                            </p>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </>
              ) : !loading ? (
                <div className="flex flex-col items-center justify-center h-full gap-3 text-center py-12">
                  <Crown className="w-10 h-10 text-muted-foreground/20" />
                  <p className="text-xs text-muted-foreground">
                    点击「AI 分析金句」让 AI 自动推荐<br />最适合做开场的片段
                  </p>
                </div>
              ) : null}
            </div>
          </div>

          {/* 第三栏：上下文（前后5句） */}
          <div className="flex-1 flex flex-col min-w-0">
            <div className="px-4 py-3 border-b border-border/50 shrink-0">
              <p className="text-[11px] font-medium text-muted-foreground uppercase tracking-wider">
                上下文
              </p>
              <p className="text-[10px] text-muted-foreground/50 mt-0.5">
                点击中栏金句查看前后句
              </p>
            </div>

            <div className="flex-1 overflow-y-auto px-3 py-2">
              {activeCtxCandidate ? (
                <div className="space-y-1">
                  {/* 前5句 */}
                  {ctxPrevSegs.map((seg, i) => {
                    const label = ctxPrevSegs.length === 1 ? "前1" : `前${ctxPrevSegs.length - i}`;
                    return renderCtxRow(seg, label);
                  })}

                  {/* 当前金句高亮 */}
                  <div className="rounded-md border border-amber-500/50 bg-amber-500/10 px-3 py-2">
                    <div className="flex items-center gap-1.5 mb-1">
                      {renderSpeakerBadge(segMap[activeCtxCandidate.segment_id]?.speaker)}
                      <span className="text-[10px] text-amber-400/60 font-mono">
                        {formatTimestamp(activeCtxCandidate.start)} · {(activeCtxCandidate.end - activeCtxCandidate.start).toFixed(1)}s
                      </span>
                      <button
                        className={cn(
                          "ml-auto shrink-0 text-[10px] px-1.5 py-0.5 rounded border transition-colors",
                          selected.has(activeCtxCandidate.segment_id)
                            ? "border-destructive/40 text-destructive hover:bg-destructive/10"
                            : "border-amber-500/40 text-amber-400 hover:bg-amber-500/20"
                        )}
                        onClick={(e) => selected.has(activeCtxCandidate.segment_id)
                          ? removeSegFromSelected(activeCtxCandidate.segment_id, e)
                          : addSegToSelected(activeCtxCandidate.segment_id, e)
                        }
                      >
                        {selected.has(activeCtxCandidate.segment_id) ? "取消" : "+ 加入"}
                      </button>
                    </div>
                    <p className="text-[12px] text-amber-300 leading-snug">
                      {activeCtxCandidate.text}
                    </p>
                  </div>

                  {/* 后5句 */}
                  {ctxNextSegs.map((seg, i) => {
                    const label = `后${i + 1}`;
                    return renderCtxRow(seg, label);
                  })}
                </div>
              ) : (
                <div className="flex flex-col items-center justify-center h-full gap-2 text-center py-8">
                  <ChevronDown className="w-6 h-6 text-muted-foreground/20" />
                  <p className="text-[11px] text-muted-foreground/50">
                    点击中栏金句<br />查看上下文
                  </p>
                </div>
              )}
            </div>
          </div>
        </div>
      </SheetContent>
    </Sheet>
  );
}

// ============================================================
// Export Panel
// ============================================================
function ExportPanel({
  task,
  jianyingDir,
  onExportFFmpeg,
  onExportJianying,
  onCancelExport,
  apiKey,
  provider,
  claudeModel,
  ollamaModel,
  ollamaBaseUrl,
  onGoldenQuoteSaved,
  onSeek,
  goldenCandidates,
  setGoldenCandidates,
  goldenAnalyzed,
  setGoldenAnalyzed,
  goldenDialogOpen,
  setGoldenDialogOpen,
  onClipApplied,
}: {
  task: Task;
  jianyingDir: string;
  onExportFFmpeg: (burnSubtitles: boolean, subtitleStyle?: SubtitleStyle, coverImageBase64?: string) => void;
  onExportJianying: () => void;
  onCancelExport: () => void;
  apiKey?: string;
  provider?: string;
  claudeModel?: string;
  ollamaModel?: string;
  ollamaBaseUrl?: string;
  onGoldenQuoteSaved: (order: string[]) => void;
  onSeek: (time: number) => void;
  goldenCandidates: GoldenQuoteCandidate[];
  setGoldenCandidates: React.Dispatch<React.SetStateAction<GoldenQuoteCandidate[]>>;
  goldenAnalyzed: boolean;
  setGoldenAnalyzed: React.Dispatch<React.SetStateAction<boolean>>;
  goldenDialogOpen: boolean;
  setGoldenDialogOpen: React.Dispatch<React.SetStateAction<boolean>>;
  onClipApplied: () => void;
}) {
  const isExporting = task.status === "export_running";
  const [burnSubtitles, setBurnSubtitles] = useState(true);
  const [showJianyingDir, setShowJianyingDir] = useState(false);

  // 从 localStorage 恢复上一次的字幕样式配置，读取失败则使用默认值
  const [subtitleStyle, setSubtitleStyle] = useState<SubtitleStyle>(() => {
    try {
      const saved = localStorage.getItem(SUBTITLE_STYLE_LS_KEY);
      if (saved) {
        const parsed = JSON.parse(saved) as Partial<SubtitleStyle>;
        return { ...DEFAULT_SUBTITLE_STYLE, ...parsed };
      }
    } catch {
      // 解析失败，忽略，使用默认值
    }
    return { ...DEFAULT_SUBTITLE_STYLE };
  });

  // 包装 setSubtitleStyle，每次修改后同步持久化到 localStorage
  const setSubtitleStylePersisted = (
    updater: SubtitleStyle | ((prev: SubtitleStyle) => SubtitleStyle)
  ) => {
    setSubtitleStyle((prev) => {
      const next = typeof updater === "function" ? updater(prev) : updater;
      try {
        localStorage.setItem(SUBTITLE_STYLE_LS_KEY, JSON.stringify(next));
      } catch {
        // 存储失败（如隐私模式），忽略
      }
      return next;
    });
  };
  const [configOpen, setConfigOpen] = useState(false);
  const [frameDataUrl, setFrameDataUrl] = useState<string | null>(null);
  const [videoAspectRatio, setVideoAspectRatio] = useState(16 / 9);
  const [videoDuration, setVideoDuration] = useState(0);
  const [videoCurrentTime, setVideoCurrentTime] = useState(0);
  const [coverDialogOpen, setCoverDialogOpen] = useState(false);
  const [coverFrameDataUrl, setCoverFrameDataUrl] = useState<string | null>(null);
  const [coverVideoAspectRatio, setCoverVideoAspectRatio] = useState(16 / 9);
  const [coverVideoDuration, setCoverVideoDuration] = useState(0);
  const [coverVideoCurrentTime, setCoverVideoCurrentTime] = useState(0);
  const coverLsKey = `gc_cover_b64_${task.id}`;
  const [coverImageBase64, setCoverImageBase64] = useState<string | null>(() => {
    try { return localStorage.getItem(`gc_cover_b64_${task.id}`); } catch { return null; }
  });
  const persistCover = (b64: string) => {
    setCoverImageBase64(b64);
    try { localStorage.setItem(coverLsKey, b64); } catch { /* quota exceeded */ }
  };
  const [includeCover, setIncludeCover] = useState(false);
  const goldenQuoteCount = (task.golden_quote_order || []).length;

  const openSubtitleConfig = () => {
    const video = document.querySelector("video") as HTMLVideoElement | null;

    // 读取视频元信息（不依赖 crossOrigin/canvas）
    let ar = videoAspectRatio;
    if (video && video.videoWidth) {
      ar = video.videoWidth / video.videoHeight;
      setVideoAspectRatio(ar);
    }
    if (video && video.duration) {
      setVideoDuration(video.duration);
    }
    const t = video?.currentTime ?? 0;
    setVideoCurrentTime(t);

    // 首次使用（localStorage 无记录）时，根据视频实际方向应用合理默认边距，
    // 避免竖屏下 margin_bottom=80px 仅占 1920px 高度的 4% 而显得贴底
    const hasSavedStyle = !!localStorage.getItem(SUBTITLE_STYLE_LS_KEY);
    if (!hasSavedStyle && ar > 0) {
      setSubtitleStylePersisted((prev) => ({
        ...prev,
        ...getOrientationDefaults(ar),
      }));
    }

    // 用后端 FFmpeg 抽帧，完全不依赖 canvas CORS
    const frameUrl = `http://localhost:8000/api/tasks/${task.id}/frame?t=${t.toFixed(3)}`;
    setFrameDataUrl(frameUrl);

    setConfigOpen(true);
  };

  const openCoverDialog = () => {
    const video = document.querySelector("video") as HTMLVideoElement | null;
    let ar = coverVideoAspectRatio;
    if (video && video.videoWidth) {
      ar = video.videoWidth / video.videoHeight;
      setCoverVideoAspectRatio(ar);
    }
    if (video && video.duration) setCoverVideoDuration(video.duration);
    const t = video?.currentTime ?? 0;
    setCoverVideoCurrentTime(t);
    setCoverFrameDataUrl(`http://localhost:8000/api/tasks/${task.id}/frame?t=${t.toFixed(3)}`);
    setCoverDialogOpen(true);
  };

  const clipIsActive = (task.clip_start != null && task.clip_start > 0) ||
    (task.clip_end != null && task.video_duration != null && task.clip_end < task.video_duration);

  return (
    <div className="bg-card border border-border rounded-xl p-4 space-y-2.5">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-medium text-muted-foreground uppercase tracking-wider">导出选项</h3>
        <div className="flex items-center gap-2">
          <label
            className="flex items-center gap-1.5 cursor-pointer select-none"
            title={coverImageBase64 ? "导出时在片头插入封面" : "请先在封面简介中生成 AI 封面"}
          >
            <div
              onClick={() => {
                if (!coverImageBase64) {
                  toast.error("请先在封面简介中生成 AI 封面");
                  return;
                }
                setIncludeCover(v => !v);
              }}
              className={cn(
                "w-3.5 h-3.5 rounded border flex items-center justify-center transition-colors shrink-0",
                includeCover && coverImageBase64
                  ? "bg-purple-500 border-purple-500"
                  : "border-border/60 hover:border-border"
              )}
            >
              {includeCover && coverImageBase64 && <Check className="w-2.5 h-2.5 text-white" />}
            </div>
            <span className="text-[10px] text-muted-foreground/70">导出封面</span>
          </label>
          <button
            onClick={openCoverDialog}
            title="封面与简介"
            className="flex items-center gap-1 px-2 py-1 rounded text-[10px] transition-colors border text-muted-foreground/70 border-border/50 hover:border-border hover:text-foreground"
          >
            <ImageIcon className="w-2.5 h-2.5" />
            <span>封面简介</span>
          </button>
        </div>
      </div>

      {/* 弹窗（不占布局） */}
      <SubtitleConfigDialog
        open={configOpen}
        onClose={() => setConfigOpen(false)}
        style={subtitleStyle}
        onChange={setSubtitleStylePersisted}
        frameDataUrl={frameDataUrl}
        videoAspectRatio={videoAspectRatio}
        videoDuration={videoDuration}
        videoCurrentTime={videoCurrentTime}
        taskId={task.id}
      />
      <GoldenQuoteDialog
        open={goldenDialogOpen}
        onClose={() => setGoldenDialogOpen(false)}
        task={task}
        apiKey={apiKey}
        provider={provider}
        claudeModel={claudeModel}
        ollamaModel={ollamaModel}
        ollamaBaseUrl={ollamaBaseUrl}
        onSaved={onGoldenQuoteSaved}
        onSeek={onSeek}
        candidates={goldenCandidates}
        setCandidates={setGoldenCandidates}
        analyzed={goldenAnalyzed}
        setAnalyzed={setGoldenAnalyzed}
      />
      <CoverAndDescriptionDialog
        open={coverDialogOpen}
        onClose={() => setCoverDialogOpen(false)}
        taskId={task.id}
        defaultCoverTitle={task.clip_title ?? (task.source_task_id ? (task.name?.replace(/ · (口播精修|访谈精修)$/, '') ?? "") : "")}
        frameDataUrl={coverFrameDataUrl}
        videoAspectRatio={coverVideoAspectRatio}
        videoDuration={coverVideoDuration}
        videoCurrentTime={coverVideoCurrentTime}
        segments={task.audit_segments}
        apiKey={apiKey}
        provider={provider}
        claudeModel={claudeModel}
        ollamaModel={ollamaModel}
        ollamaBaseUrl={ollamaBaseUrl}
        onCoverGenerated={persistCover}
        initialGeneratedCover={coverImageBase64}
      />

      {/* ── Card 1: 裁剪区间 ── */}
      {task.video_duration != null && (
        <div className={cn(
          "border-l-[3px] rounded-lg px-3 py-2.5 space-y-1",
          clipIsActive
            ? "border-l-amber-400 bg-amber-500/5"
            : "border-l-border bg-secondary/30"
        )}>
          <div className="flex items-center gap-1.5">
            <Crop className="w-3 h-3 text-muted-foreground/70" />
            <span className="text-[11px] font-medium text-muted-foreground">裁剪区间</span>
            {clipIsActive && (
              <span className="text-[9px] px-1.5 py-0.5 rounded-full bg-amber-500/20 text-amber-400 border border-amber-500/30 font-medium">
                已激活
              </span>
            )}
          </div>
          <ClipRangeBar
            taskId={task.id}
            videoDuration={task.video_duration}
            clipStart={task.clip_start}
            clipEnd={task.clip_end}
            onApplied={onClipApplied}
            onSeek={onSeek}
          />
        </div>
      )}

      {/* ── Card 2: 金句开场 ── */}
      <button
        onClick={() => setGoldenDialogOpen(true)}
        className={cn(
          "w-full border-l-[3px] rounded-lg px-3 py-2.5 flex items-center justify-between text-[12px] transition-colors text-left",
          goldenQuoteCount > 0
            ? "border-l-amber-400 bg-amber-500/5 text-amber-400 hover:bg-amber-500/10"
            : "border-l-border bg-secondary/30 text-muted-foreground hover:bg-secondary/50"
        )}
      >
        <div className="flex items-center gap-1.5">
          <Crown className="w-3.5 h-3.5" />
          <span className="font-medium">金句开场</span>
          {goldenQuoteCount > 0 && (
            <span className="bg-amber-500 text-black text-[10px] font-bold px-1.5 py-0.5 rounded-full leading-none">
              {goldenQuoteCount}
            </span>
          )}
        </div>
        <span className="text-[10px] text-muted-foreground/60">
          {goldenQuoteCount > 0 ? `已设置 ${goldenQuoteCount} 个` : "未设置"}
        </span>
      </button>

      {/* ── Card 3: 字幕 ── */}
      <div className={cn(
        "border-l-[3px] rounded-lg px-3 py-2.5",
        burnSubtitles
          ? "border-l-primary bg-primary/5"
          : "border-l-border bg-secondary/30"
      )}>
        <div className="flex items-center gap-2">
          <label className="flex items-center gap-2 cursor-pointer select-none flex-1 min-w-0">
            <input
              type="checkbox"
              checked={burnSubtitles}
              onChange={(e) => setBurnSubtitles(e.target.checked)}
              disabled={isExporting}
              className="rounded border-border accent-primary w-3.5 h-3.5 shrink-0"
            />
            <span className="text-[12px] text-muted-foreground font-medium">硬字幕烧录</span>
            <span className="text-[10px] text-muted-foreground/50 truncate">
              {burnSubtitles ? "烧入画面" : "生成 .srt 文件"}
            </span>
          </label>
          <button
            onClick={openSubtitleConfig}
            title="字幕样式配置"
            className={cn(
              "shrink-0 flex items-center gap-1 px-2 py-1 rounded text-[10px] transition-colors border",
              burnSubtitles
                ? "text-primary border-primary/40 bg-primary/10 hover:bg-primary/20"
                : "text-muted-foreground/60 border-border/40 hover:border-border hover:text-muted-foreground"
            )}
          >
            <SlidersHorizontal className="w-2.5 h-2.5" />
            <span>样式</span>
          </button>
        </div>
      </div>

      {/* ── 分隔线 + 导出操作 ── */}
      <div className="border-t border-border/40 pt-2.5 space-y-2">
        <div className="flex gap-2">
          {isExporting ? (
            <Button
              size="sm"
              className="flex-1 bg-destructive text-destructive-foreground hover:bg-destructive/90 text-xs gap-1.5"
              onClick={onCancelExport}
            >
              <Loader2 className="w-3 h-3 animate-spin" />
              终止导出
            </Button>
          ) : (
            <Button
              size="sm"
              className="flex-1 bg-primary text-primary-foreground hover:bg-primary/90 text-xs gap-1.5"
              onClick={() => onExportFFmpeg(burnSubtitles, burnSubtitles ? subtitleStyle : undefined, includeCover && coverImageBase64 ? coverImageBase64 : undefined)}
            >
              <Download className="w-3 h-3" />
              FFmpeg 快速导出
            </Button>
          )}
          <Button
            size="sm"
            variant="outline"
            className="flex-1 border-border text-foreground hover:bg-secondary text-xs gap-1.5"
            onClick={() => setShowJianyingDir(prev => !prev)}
            disabled={isExporting}
          >
            <FileJson className="w-3 h-3" />
            剪映草稿
            <ChevronDown className={cn("w-3 h-3 transition-transform", showJianyingDir && "rotate-180")} />
          </Button>
        </div>

        {showJianyingDir && (
          <div className="space-y-1.5 animate-in fade-in slide-in-from-top-1 duration-150">
            <div className="h-7 px-2 flex items-center rounded-md bg-secondary border border-border text-[11px] text-muted-foreground truncate">
              {jianyingDir || (
                <span className="text-muted-foreground/40">未配置 · 导出到视频同级目录（可在设置页配置）</span>
              )}
            </div>
            <Button
              size="sm"
              variant="outline"
              className="w-full border-border text-foreground hover:bg-secondary text-xs gap-1.5"
              onClick={onExportJianying}
              disabled={isExporting}
            >
              <FileJson className="w-3 h-3" />
              导出剪映草稿
            </Button>
          </div>
        )}

        {task.export_path && (
          <p className="text-[10px] text-green-400 font-mono truncate">
            ✓ {task.export_path}
          </p>
        )}
        {task.jianying_draft_path && (
          <p className="text-[10px] text-green-400 font-mono truncate">
            ✓ {task.jianying_draft_path}
          </p>
        )}
      </div>
    </div>
  );
}

// ============================================================
// Scenario Info Panel (Right Column Top)
// ============================================================
const SCENARIO_META: Record<string, {
  label: string;
  color: string;
  border: string;
  bg: string;
  rules: { code: string; name: string; desc: string }[];
  targets: { host?: string; guest?: string; overall: string };
}> = {
  monologue_clean: {
    label: "口播精修",
    color: "text-amber-400",
    border: "border-amber-500/30",
    bg: "bg-amber-500/5",
    rules: [
      { code: "P1", name: "重说识别", desc: "开头5字相同→删前句" },
      { code: "P2", name: "结巴切除", desc: "词级精准切除，零容忍" },
      { code: "P3", name: "语气词分级", desc: "必删/酌情/保留三档" },
      { code: "P5", name: "冗余压缩", desc: "同观点保留信息密度最高版" },
      { code: "P7", name: "开头钩子", desc: "前5秒强制保护" },
    ],
    targets: { overall: "保留 60-80%" },
  },
  interview_compress: {
    label: "访谈压缩",
    color: "text-blue-400",
    border: "border-blue-500/30",
    bg: "bg-blue-500/5",
    rules: [
      { code: "I1", name: "问答闭环", desc: "Q&A成对，不可破坏" },
      { code: "I2", name: "跨段去重", desc: "全文扫描语义重复" },
      { code: "I3", name: "精华保护", desc: "金句/洞见无条件保留" },
      { code: "I4", name: "主播精简", desc: "废话铺垫删除" },
      { code: "I6", name: "情绪弧线", desc: "四节点保护" },
    ],
    targets: { host: "主播 20-30%", guest: "嘉宾 60-70%", overall: "整体 35-45%" },
  },
  highlight_reel: {
    label: "精彩集锦",
    color: "text-orange-400",
    border: "border-orange-500/30",
    bg: "bg-orange-500/5",
    rules: [
      { code: "H1", name: "高能识别", desc: "数字/反转/金句/情绪" },
      { code: "H2", name: "前3秒钩子", desc: "最强内容冷开场" },
      { code: "H3", name: "节奏加速", desc: "删除所有>0.5s停顿" },
      { code: "H4", name: "情绪弧线", desc: "勾引→爆发→余韵" },
      { code: "H5", name: "废话零容忍", desc: "语气词/过渡句全删" },
    ],
    targets: { overall: "保留 10-25%" },
  },
};

function ScenarioInfoPanel({
  task,
  styleMode,
  onStyleModeChange,
  claudeApiKey,
  provider,
  ollamaModel,
}: {
  task: Task;
  styleMode: "immersive" | "quick_cut";
  onStyleModeChange: (m: "immersive" | "quick_cut") => void;
  claudeApiKey: string;
  provider?: string;
  ollamaModel?: string;
}) {
  const meta = SCENARIO_META[task.task_type] || SCENARIO_META.monologue_clean;
  const [expanded, setExpanded] = useState(false);
  const [promptDialogOpen, setPromptDialogOpen] = useState(false);
  const [promptContent, setPromptContent] = useState("");
  const [promptLoading, setPromptLoading] = useState(false);

  // 加载并展示提示词
  const handleViewPrompt = async (e: React.MouseEvent) => {
    e.stopPropagation();
    setPromptDialogOpen(true);
    if (promptContent) return; // 已加载过则直接展示
    setPromptLoading(true);
    try {
      const data = await getPrompt(task.task_type as "monologue_clean" | "interview_compress" | "highlight_reel");
      setPromptContent(data.content);
    } catch {
      setPromptContent("加载提示词失败，请检查后端服务是否正常。");
    } finally {
      setPromptLoading(false);
    }
  };

  return (
    <>
      {/* 提示词查看弹窗 */}
      <Dialog open={promptDialogOpen} onOpenChange={setPromptDialogOpen}>
        <DialogContent className="max-w-2xl max-h-[80vh] flex flex-col">
          <DialogHeader>
            <DialogTitle className={cn("font-mono text-sm", meta.color)}>
              {meta.label} · 提示词
            </DialogTitle>
            <DialogDescription className="text-xs text-muted-foreground">
              当前场景 AI 审计使用的完整提示词内容（只读）
            </DialogDescription>
          </DialogHeader>
          <div className="flex-1 overflow-auto mt-2">
            {promptLoading ? (
              <div className="flex items-center justify-center py-12 text-muted-foreground text-sm gap-2">
                <Loader2 className="w-4 h-4 animate-spin" />
                加载中…
              </div>
            ) : (
              <pre className="text-[11px] leading-relaxed whitespace-pre-wrap font-mono text-foreground/80 bg-secondary/30 rounded-md p-4 border border-border">
                {promptContent || "（提示词为空）"}
              </pre>
            )}
          </div>
        </DialogContent>
      </Dialog>

    <div className={cn("border-b border-border shrink-0", meta.bg)}>
      {/* Header */}
      <button
        className="w-full flex items-center gap-2 px-3 py-2 hover:bg-white/5 transition-colors"
        onClick={() => setExpanded(!expanded)}
      >
        <span className={cn("text-[10px] font-mono font-bold uppercase tracking-wider", meta.color)}>
          {meta.label}
        </span>
        <span className="text-[10px] text-muted-foreground/50 ml-auto">
          {meta.targets.overall}
        </span>
        <ChevronDown className={cn("w-3 h-3 text-muted-foreground/50 transition-transform", expanded && "rotate-180")} />
      </button>

      {/* Expanded: Rules + Config */}
      {expanded && (
        <div className="px-3 pb-3 space-y-3">
          {/* Rules */}
          <div className="space-y-1">
            {meta.rules.map((r) => (
              <div key={r.code} className="flex items-start gap-1.5">
                <span className={cn("text-[10px] font-mono font-bold w-5 shrink-0 mt-0.5", meta.color)}>{r.code}</span>
                <div className="min-w-0">
                  <span className="text-[10px] text-foreground/80 font-medium">{r.name}</span>
                  <span className="text-[10px] text-muted-foreground/60 ml-1">{r.desc}</span>
                </div>
              </div>
            ))}
          </div>

          {/* 查看提示词按钮 */}
          <button
            onClick={handleViewPrompt}
            className="w-full flex items-center justify-center gap-1.5 py-1 rounded border border-border/50 text-[10px] text-muted-foreground hover:text-foreground hover:border-border transition-all"
          >
            <BookOpen className="w-3 h-3" />
            查看提示词
          </button>

          {/* Style Mode */}
          <div className="space-y-1">
            <p className="text-[10px] text-muted-foreground/60 uppercase tracking-wider">剪辑风格</p>
            <div className="grid grid-cols-2 gap-1">
              {(["immersive", "quick_cut"] as const).map((m) => (
                <button
                  key={m}
                  onClick={() => onStyleModeChange(m)}
                  className={cn(
                    "text-[10px] py-1 px-2 rounded border transition-all",
                    styleMode === m
                      ? cn("border-current font-medium", meta.color)
                      : "border-border text-muted-foreground hover:text-foreground"
                  )}
                >
                  {m === "immersive" ? "沉浸" : "快剪"}
                </button>
              ))}
            </div>
            <p className="text-[10px] text-muted-foreground/40 leading-relaxed">
              {styleMode === "immersive"
                ? "气口150ms · 保留思考停顿"
                : "气口80ms · 激进删除废话"}
            </p>
          </div>

          {/* 模型状态（只读，配置入口在设置页） */}
          <div className="space-y-1">
            {provider === "ollama" ? (
              <>
                <p className="text-[10px] text-muted-foreground/60 uppercase tracking-wider">本地模型</p>
                <div className="text-[10px] px-2 py-1 rounded border border-green-500/30 bg-green-500/10 text-green-400 font-mono truncate">
                  ⚡ {ollamaModel || "Ollama"}
                </div>
                <p className="text-[10px] text-muted-foreground/30">
                  数据不出本机 · 完全离线推理
                </p>
              </>
            ) : (
              <>
                <p className="text-[10px] text-muted-foreground/60 uppercase tracking-wider">Claude API Key</p>
                <div className={cn(
                  "text-[10px] px-2 py-1 rounded border",
                  claudeApiKey
                    ? "border-green-500/30 bg-green-500/10 text-green-400"
                    : "border-border/30 bg-secondary/30 text-muted-foreground/50"
                )}>
                  {claudeApiKey ? "✓ 已配置 API Key" : "未配置 · 将使用规则引擎"}
                </div>
                <p className="text-[10px] text-muted-foreground/30">
                  在左侧设置页（⚙）中配置 API Key
                </p>
              </>
            )}
          </div>

          {/* Target retention */}
          {(meta.targets.host || meta.targets.guest) && (
            <div className="space-y-1 border-t border-border/30 pt-2">
              <p className="text-[10px] text-muted-foreground/60 uppercase tracking-wider">保留目标</p>
              {meta.targets.host && (
                <div className="flex justify-between text-[10px]">
                  <span className="text-muted-foreground">主播 (spk0)</span>
                  <span className={meta.color}>{meta.targets.host.split(" ")[1]}</span>
                </div>
              )}
              {meta.targets.guest && (
                <div className="flex justify-between text-[10px]">
                  <span className="text-muted-foreground">嘉宾 (spk1)</span>
                  <span className={meta.color}>{meta.targets.guest.split(" ")[1]}</span>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
    </>
  );
}

// ============================================================
// 手动修改原因选择浮窗
// ============================================================

/** 保留原因（AI 误删 → 用户恢复）*/
const KEEP_REASONS = [
  "重要知识点，值得保留",
  "有效互动，不是无效沟通",
  "背景铺垫，有助于理解",
  "学生关键提问",
  "流畅过渡，节奏自然",
];

/** 删除原因（AI 漏删 → 用户删除）*/
const DELETE_REASONS = [
  "无效沟通，偏离主题",
  "重复内容",
  "与学习无关的闲聊",
  "过长停顿或填充语",
  "说话含糊，难以理解",
];

function ReasonPickerSheet({
  open,
  action,
  onSelect,
  onSkip,
}: {
  open: boolean;
  action?: "keep" | "delete";
  onSelect: (reason: string) => void;
  onSkip: () => void;
}) {
  const [customMode, setCustomMode] = useState(false);
  const [customText, setCustomText] = useState("");

  const reasons = action === "keep" ? KEEP_REASONS : DELETE_REASONS;
  const isKeepAction = action === "keep";

  // 每次打开重置
  useEffect(() => {
    if (open) {
      setCustomMode(false);
      setCustomText("");
    }
  }, [open]);

  return (
    <Sheet open={open} onOpenChange={(v) => { if (!v) onSkip(); }}>
      <SheetContent side="bottom" className="bg-card border-border rounded-t-xl max-h-[60vh] overflow-y-auto">
        <SheetHeader className="pb-3">
          <SheetTitle className={cn(
            "text-sm flex items-center gap-2",
            isKeepAction ? "text-green-300" : "text-orange-300"
          )}>
            {isKeepAction ? "📌 为什么保留这段？" : "🗑 为什么删除这段？"}
          </SheetTitle>
          <SheetDescription className="text-[11px] text-muted-foreground">
            选择原因将帮助 AI 学习你的剪辑偏好，也会显示在段落卡片上
          </SheetDescription>
        </SheetHeader>

        {!customMode ? (
          <div className="space-y-3 pt-1">
            {/* 预设原因 chips */}
            <div className="flex flex-wrap gap-2">
              {reasons.map((r) => (
                <button
                  key={r}
                  className={cn(
                    "text-[12px] px-3 py-1.5 rounded-full border transition-colors",
                    isKeepAction
                      ? "border-green-500/40 bg-green-900/20 text-green-300 hover:bg-green-900/40"
                      : "border-orange-500/40 bg-orange-900/20 text-orange-300 hover:bg-orange-900/40"
                  )}
                  onClick={() => onSelect(r)}
                >
                  {r}
                </button>
              ))}
              {/* 自定义 */}
              <button
                className="text-[12px] px-3 py-1.5 rounded-full border border-border bg-secondary/30 text-muted-foreground hover:bg-secondary/60 transition-colors"
                onClick={() => setCustomMode(true)}
              >
                自定义...
              </button>
            </div>

            {/* 跳过按钮 */}
            <div className="pt-1">
              <button
                className="text-[11px] text-muted-foreground/60 hover:text-muted-foreground underline transition-colors"
                onClick={onSkip}
              >
                跳过，不添加原因
              </button>
            </div>
          </div>
        ) : (
          <div className="space-y-3 pt-1">
            <Input
              autoFocus
              placeholder="输入自定义原因..."
              value={customText}
              onChange={(e) => setCustomText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && customText.trim()) onSelect(customText.trim());
                if (e.key === "Escape") setCustomMode(false);
              }}
              className="text-sm bg-secondary/20 border-border"
            />
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                disabled={!customText.trim()}
                onClick={() => onSelect(customText.trim())}
                className={cn(
                  "text-xs h-7",
                  isKeepAction
                    ? "bg-green-700/40 hover:bg-green-700/60 text-green-200"
                    : "bg-orange-700/40 hover:bg-orange-700/60 text-orange-200"
                )}
              >
                确认
              </Button>
              <button
                className="text-[11px] text-muted-foreground/60 hover:text-muted-foreground underline"
                onClick={() => setCustomMode(false)}
              >
                返回选项
              </button>
            </div>
          </div>
        )}
      </SheetContent>
    </Sheet>
  );
}

// ============================================================
// Prompt Feedback Dialog (提示词反馈优化)
// ============================================================
function PromptFeedbackDialog({
  open,
  onClose,
  taskId,
  claudeApiKey,
  claudeModel,
  segments,
}: {
  open: boolean;
  onClose: () => void;
  taskId: string;
  taskType: string;
  claudeApiKey: string;
  claudeModel?: string;
  segments?: Segment[];
  }) {
  const [corrections, setCorrections] = useState<CorrectionsResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<PromptFeedbackResult | null>(null);
  const [userNotes, setUserNotes] = useState("");
  const [adopting, setAdopting] = useState(false);
  const [adopted, setAdopted] = useState(false);

  useEffect(() => {
    if (!open) return;
    setLoading(true);
    setResult(null);
    setAdopted(false);
    getCorrections(taskId)
      .then(setCorrections)
      .catch(() => toast.error("获取修正统计失败"))
      .finally(() => setLoading(false));
  }, [open, taskId]);

  const handleSubmit = async () => {
    setSubmitting(true);
    try {
      const res = await submitPromptFeedback(taskId, {
        claude_api_key: claudeApiKey || undefined,
        claude_model: claudeModel,
        user_notes: userNotes,
      });
      setResult(res);
      toast.success("Claude 已返回改进建议");
    } catch (e: any) {
      toast.error(e.message || "提交失败");
    } finally {
      setSubmitting(false);
    }
  };

  const handleAdopt = async () => {
    if (!result?.suggestion || !result.scenario) return;
    setAdopting(true);
    try {
      const scenario = result.scenario as "monologue_clean" | "interview_compress" | "highlight_reel";
      // 并行：写入提示词规则 + 写入个人风格档案
      const promptPromise = (async () => {
        const promptData = await getPrompt(scenario);
        const updatedContent = promptData.content + "\n\n<!-- Claude 提示词优化建议（已采纳） -->\n" +
          "<!--\n" + result.suggestion + "\n-->\n";
        await updatePromptContent(scenario, updatedContent);
      })();

      const stylePromise = result.style_update
        ? adoptStyleUpdate(taskId, result.style_update).catch(() => {})
        : Promise.resolve();

      await Promise.all([promptPromise, stylePromise]);
      setAdopted(true);
      toast.success(result.style_update
        ? "建议已写入提示词 + 个人风格档案已更新"
        : "建议已追加写入提示词文件");
    } catch (e: any) {
      toast.error(e.message || "写入失败");
    } finally {
      setAdopting(false);
    }
  };

  const actionLabel = (a: string) =>
    ({ keep: "保留", delete: "删除", subtitle_fix: "字幕去重", text_fix: "纠错", merge_next: "合并" }[a] || a);

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="bg-card border-border max-w-2xl max-h-[85vh] overflow-hidden flex flex-col">
        <DialogHeader>
          <DialogTitle className="text-foreground flex items-center gap-2">
            <Sparkles className="w-4 h-4 text-primary" />
            提示词反馈优化
          </DialogTitle>
          <DialogDescription className="text-muted-foreground text-xs">
            将你的修改提交给 Claude 分析，生成提示词规则改进建议
          </DialogDescription>
        </DialogHeader>

        <div className="flex-1 overflow-y-auto space-y-4 pr-1">
          {loading ? (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="w-5 h-5 animate-spin text-primary" />
            </div>
          ) : corrections ? (
            <>
              {/* 汇总面板 */}
              <div className="grid grid-cols-3 gap-2">
                <div className="rounded-lg border border-blue-500/30 bg-blue-500/10 p-3 text-center">
                  <div className="text-lg font-bold font-mono text-blue-300">
                    {corrections.delete_to_keep.length}
                  </div>
                  <div className="text-[10px] text-blue-400 mt-0.5">
                    Claude 误删 → 你恢复
                  </div>
                </div>
                <div className="rounded-lg border border-orange-500/30 bg-orange-500/10 p-3 text-center">
                  <div className="text-lg font-bold font-mono text-orange-300">
                    {corrections.keep_to_delete.length}
                  </div>
                  <div className="text-[10px] text-orange-400 mt-0.5">
                    Claude 漏删 → 你删除
                  </div>
                </div>
                <div className="rounded-lg border border-border bg-secondary/30 p-3 text-center">
                  <div className="text-lg font-bold font-mono text-muted-foreground">
                    {corrections.unchanged}
                  </div>
                  <div className="text-[10px] text-muted-foreground/60 mt-0.5">
                    未改动
                  </div>
                </div>
              </div>

              {corrections.total_modified === 0 ? (
                <div className="text-center py-6 text-muted-foreground text-sm">
                  没有发现修改，无需优化提示词
                </div>
              ) : (
                <>
                  {/* 修改明细（可折叠） */}
                  <Accordion type="single" collapsible className="w-full">
                    {corrections.delete_to_keep.length > 0 && (
                      <AccordionItem value="d2k" className="border-border/40">
                        <AccordionTrigger className="text-sm py-2 hover:no-underline">
                          <span className="flex items-center gap-1.5 text-blue-300">
                            <Eye className="w-3.5 h-3.5" />
                            你恢复的 {corrections.delete_to_keep.length} 个片段
                          </span>
                        </AccordionTrigger>
                        <AccordionContent>
                          <div className="space-y-2 pl-1">
                            {corrections.delete_to_keep.map((c) => {
                              const userReason = segments?.find((s) => s.id === c.id)?.reason;
                              return (
                                <div key={c.id} className="text-[11px] bg-blue-500/5 border border-blue-500/15 rounded-md px-2.5 py-1.5">
                                  <div className="text-foreground/80 leading-snug">"{c.text}"</div>
                                  <div className="flex items-center gap-2 mt-1 text-[10px] text-muted-foreground flex-wrap">
                                    <span className="text-red-400">Claude: {actionLabel(c.claude_action)}</span>
                                    <span>→</span>
                                    <span className="text-blue-400">你: {actionLabel(c.user_action)}</span>
                                    {c.claude_reason && (
                                      <span className="text-muted-foreground/50 ml-auto truncate max-w-[200px]" title={c.claude_reason}>
                                        ({c.claude_reason})
                                      </span>
                                    )}
                                  </div>
                                  {userReason && userReason !== "手动恢复" && (
                                    <div className="mt-1">
                                      <span className="text-[10px] px-1.5 py-0.5 rounded bg-green-900/30 text-green-300">
                                        你的原因：{userReason}
                                      </span>
                                    </div>
                                  )}
                                </div>
                              );
                            })}
                          </div>
                        </AccordionContent>
                      </AccordionItem>
                    )}

                    {corrections.keep_to_delete.length > 0 && (
                      <AccordionItem value="k2d" className="border-border/40">
                        <AccordionTrigger className="text-sm py-2 hover:no-underline">
                          <span className="flex items-center gap-1.5 text-orange-300">
                            <EyeOff className="w-3.5 h-3.5" />
                            你删除的 {corrections.keep_to_delete.length} 个片段
                          </span>
                        </AccordionTrigger>
                        <AccordionContent>
                          <div className="space-y-2 pl-1">
                            {corrections.keep_to_delete.map((c) => {
                              const userReason = segments?.find((s) => s.id === c.id)?.reason;
                              return (
                                <div key={c.id} className="text-[11px] bg-orange-500/5 border border-orange-500/15 rounded-md px-2.5 py-1.5">
                                  <div className="text-foreground/80 leading-snug">"{c.text}"</div>
                                  <div className="flex items-center gap-2 mt-1 text-[10px] text-muted-foreground flex-wrap">
                                    <span className="text-green-400">Claude: {actionLabel(c.claude_action)}</span>
                                    <span>→</span>
                                    <span className="text-orange-400">你: {actionLabel(c.user_action)}</span>
                                  </div>
                                  {userReason && (
                                    <div className="mt-1">
                                      <span className="text-[10px] px-1.5 py-0.5 rounded bg-orange-900/30 text-orange-300">
                                        你的原因：{userReason}
                                      </span>
                                    </div>
                                  )}
                                </div>
                              );
                            })}
                          </div>
                        </AccordionContent>
                      </AccordionItem>
                    )}
                  </Accordion>

                  {/* 用户备注 */}
                  <div className="space-y-1.5">
                    <label className="text-xs text-muted-foreground flex items-center gap-1">
                      <MessageSquare className="w-3 h-3" />
                      备注说明（可选，帮助 Claude 理解你的修改意图）
                    </label>
                    <textarea
                      value={userNotes}
                      onChange={(e) => setUserNotes(e.target.value)}
                      placeholder="例如：这个场景需要保留更多嘉宾的情绪表达..."
                      className="w-full bg-background border border-border rounded-lg px-3 py-2 text-sm text-foreground resize-none focus:outline-none focus:ring-1 focus:ring-primary placeholder:text-muted-foreground/40"
                      rows={2}
                    />
                  </div>

                  {/* 提交按钮 */}
                  {!result && (
                    <Button
                      className="w-full bg-primary text-primary-foreground hover:bg-primary/90 gap-2"
                      onClick={handleSubmit}
                      disabled={submitting || !claudeApiKey}
                    >
                      {submitting ? (
                        <Loader2 className="w-4 h-4 animate-spin" />
                      ) : (
                        <Sparkles className="w-4 h-4" />
                      )}
                      {submitting ? "Claude 分析中..." : "提交给 Claude 学习"}
                    </Button>
                  )}

                  {!claudeApiKey && !result && (
                    <p className="text-[10px] text-amber-400 text-center">
                      需要在设置中配置 API Key 才能提交
                    </p>
                  )}

                  {/* Claude 返回的建议 */}
                  {result && (
                    <div className="space-y-3">
                      <div className="flex items-center gap-1.5 text-xs text-primary font-medium">
                        <BookOpen className="w-3.5 h-3.5" />
                        Claude 改进建议
                      </div>
                      <div className="bg-background border border-border rounded-lg px-4 py-3 text-sm text-foreground/90 leading-relaxed whitespace-pre-wrap max-h-64 overflow-y-auto">
                        {result.suggestion}
                      </div>

                      {/* 风格档案预览 */}
                      {result.style_update && Object.keys(result.style_update).length > 0 && (
                        <div className="bg-amber-500/5 border border-amber-500/20 rounded-lg px-3 py-2 space-y-1">
                          <div className="text-[11px] text-amber-400 font-medium flex items-center gap-1">
                            <Sparkles className="w-3 h-3" />
                            学到的个人偏好
                          </div>
                          {Object.entries(result.style_update).map(([k, v]) => (
                            <div key={k} className="text-[11px] text-foreground/70">
                              <span className="text-amber-300/80">{k}：</span>{v}
                            </div>
                          ))}
                        </div>
                      )}

                      <div className="flex items-center gap-2">
                        <Button
                          variant="outline"
                          size="sm"
                          className="flex-1 border-primary/50 text-primary hover:bg-primary/10 gap-1.5"
                          onClick={handleAdopt}
                          disabled={adopting || adopted}
                        >
                          {adopted ? (
                            <CheckCircle2 className="w-3.5 h-3.5" />
                          ) : adopting ? (
                            <Loader2 className="w-3.5 h-3.5 animate-spin" />
                          ) : (
                            <Check className="w-3.5 h-3.5" />
                          )}
                          {adopted ? "已写入" : result.style_update ? "采纳规则 + 更新风格档案" : "采纳并写入提示词"}
                        </Button>
                        <Button
                          variant="outline"
                          size="sm"
                          className="border-border text-muted-foreground"
                          onClick={onClose}
                        >
                          关闭
                        </Button>
                      </div>
                    </div>
                  )}
                </>
              )}
            </>
          ) : null}
        </div>
      </DialogContent>
    </Dialog>
  );
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
  const [currentTime, setCurrentTime] = useState(0);
  const [previewVideoUrl, setPreviewVideoUrl] = useState<string | null>(null);
  const [activeSegmentId, setActiveSegmentId] = useState<string | null>(null);
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
  const [showOnlyKept, setShowOnlyKept] = useState(false);
  const initialShowOnlyKeptSet = useRef(false);
  const [playKeptOnly, setPlayKeptOnly] = useState(false);
  const [styleMode, setStyleMode] = useState<"immersive" | "quick_cut">("immersive");
  const [asrDialogOpen, setAsrDialogOpen] = useState(false);
  const [asrSelection, setAsrSelection] = useState<AsrSelection>({ backend: "funasr", model: "paraformer-zh", silenceThreshold: 0.3, enableDiarization: true });
  useEffect(() => {
    if (asrDialogOpen && task?.task_type === "highlight_reel" && asrSelection.silenceThreshold < 0.5) {
      setAsrSelection(prev => ({ ...prev, silenceThreshold: 1.0 }));
    }
  }, [asrDialogOpen, task?.task_type]);
  const [asrHistoryOpen, setAsrHistoryOpen] = useState(false);
  const [auditHistoryOpen, setAuditHistoryOpen] = useState(false);
  // 切换 ASR 历史版本的二次确认弹窗（null 表示关闭，数字表示目标版本号）
  const [switchAsrConfirmVersion, setSwitchAsrConfirmVersion] = useState<number | null>(null);
  const [segOptimHistoryOpen, setSegOptimHistoryOpen] = useState(false);
  // 切换段落优化历史版本的二次确认弹窗（null 表示关闭，数字表示目标版本号）
  const [switchSegOptimConfirmVersion, setSwitchSegOptimConfirmVersion] = useState<number | null>(null);
  // 无 API Key 时弹出确认框，询问是否使用本地规则引擎
  const [ruleEngineDialogOpen, setRuleEngineDialogOpen] = useState(false);
  // 一剪多 AI 审计参数弹窗
  const [highlightAuditDialogOpen, setHighlightAuditDialogOpen] = useState(false);
  const [highlightAuditParams, setHighlightAuditParams] = useState<HighlightAuditParams>(() =>
    getHighlightAuditDefaults(false)
  );
  // Claude API 调用失败时弹出确认框
  const [claudeFailedDialogOpen, setClaudeFailedDialogOpen] = useState(false);
  const [claudeFailedError, setClaudeFailedError] = useState("");
  // 「重新审计」选择弹窗（合并了原「重审」和「AI 审计」逻辑）
  const [reauditDialogOpen, setReauditDialogOpen] = useState(false);
  const [reauditMode, setReauditMode] = useState<"keep" | "reset">("keep");
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
  const previewRestoreTimeRef = useRef<number | null>(null);
  const segmentListRef = useRef<HTMLDivElement>(null);
  // 字级删除状态
  const [wordEditSegId, setWordEditSegId] = useState<string | null>(null);
  const [selectedWordIndices, setSelectedWordIndices] = useState<Set<number>>(new Set());
  const lastWordClickRef = useRef<number>(-1);
  // 裁断模式状态
  const [splitSegId, setSplitSegId] = useState<string | null>(null);
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

  // 一剪多 — 字幕样式（与导出面板共享同一个 localStorage key）
  const [hlSubtitleStyle, setHlSubtitleStyle] = useState<SubtitleStyle>(() => {
    try {
      const saved = localStorage.getItem(SUBTITLE_STYLE_LS_KEY);
      if (saved) {
        const parsed = JSON.parse(saved) as Partial<SubtitleStyle>;
        return { ...DEFAULT_SUBTITLE_STYLE, ...parsed };
      }
    } catch { /* ignore */ }
    return { ...DEFAULT_SUBTITLE_STYLE };
  });

  const [hlHookEnabled, setHlHookEnabled] = useState<boolean>(() => {
    try {
      const saved = localStorage.getItem("gc_hook_enabled");
      if (saved !== null) return JSON.parse(saved) as boolean;
    } catch { /* ignore */ }
    return true;
  });
  const setHlHookEnabledPersisted = (v: boolean) => {
    setHlHookEnabled(v);
    try { localStorage.setItem("gc_hook_enabled", JSON.stringify(v)); } catch { /* ignore */ }
  };

  const [hlHookStyle, setHlHookStyle] = useState<Omit<HookConfig, "enabled" | "text">>(() => {
    try {
      const saved = localStorage.getItem(HOOK_STYLE_LS_KEY);
      if (saved) return { ...DEFAULT_HOOK_STYLE, ...(JSON.parse(saved) as Partial<typeof DEFAULT_HOOK_STYLE>) };
    } catch { /* ignore */ }
    return { ...DEFAULT_HOOK_STYLE };
  });
  const setHlHookStylePersisted = (s: Omit<HookConfig, "enabled" | "text">) => {
    setHlHookStyle(s);
    try { localStorage.setItem(HOOK_STYLE_LS_KEY, JSON.stringify(s)); } catch { /* ignore */ }
  };

  const [hlHookConfigOpen, setHlHookConfigOpen] = useState(false);
  const [hlHookFrameDataUrl, setHlHookFrameDataUrl] = useState<string | null>(null);
  const [hlHookVideoAspectRatio, setHlHookVideoAspectRatio] = useState(16 / 9);
  const [hlHookVideoDuration, setHlHookVideoDuration] = useState(0);
  const [hlHookVideoCurrentTime, setHlHookVideoCurrentTime] = useState(0);

  const openHlHookConfig = () => {
    const video = document.querySelector("video") as HTMLVideoElement | null;
    if (video && video.videoWidth) setHlHookVideoAspectRatio(video.videoWidth / video.videoHeight);
    if (video && video.duration) setHlHookVideoDuration(video.duration);
    const t = video?.currentTime ?? 0;
    setHlHookVideoCurrentTime(t);
    setHlHookFrameDataUrl(`http://localhost:8000/api/tasks/${taskId}/frame?t=${t.toFixed(3)}`);
    setHlHookConfigOpen(true);
  };
  const setHlSubtitleStylePersisted = (
    updater: SubtitleStyle | ((prev: SubtitleStyle) => SubtitleStyle)
  ) => {
    setHlSubtitleStyle((prev) => {
      const next = typeof updater === "function" ? updater(prev) : updater;
      try { localStorage.setItem(SUBTITLE_STYLE_LS_KEY, JSON.stringify(next)); } catch { /* ignore */ }
      return next;
    });
  };
  const [hlConfigOpen, setHlConfigOpen] = useState(false);
  const [hlFrameDataUrl, setHlFrameDataUrl] = useState<string | null>(null);
  const [hlVideoAspectRatio, setHlVideoAspectRatio] = useState(16 / 9);
  const [hlVideoDuration, setHlVideoDuration] = useState(0);
  const [hlVideoCurrentTime, setHlVideoCurrentTime] = useState(0);

  const openHlSubtitleConfig = () => {
    const video = document.querySelector("video") as HTMLVideoElement | null;
    let ar = hlVideoAspectRatio;
    if (video && video.videoWidth) { ar = video.videoWidth / video.videoHeight; setHlVideoAspectRatio(ar); }
    if (video && video.duration) setHlVideoDuration(video.duration);
    const t = video?.currentTime ?? 0;
    setHlVideoCurrentTime(t);
    const hasSavedStyle = !!localStorage.getItem(SUBTITLE_STYLE_LS_KEY);
    if (!hasSavedStyle && ar > 0) {
      setHlSubtitleStylePersisted((prev) => ({ ...prev, ...getOrientationDefaults(ar) }));
    }
    setHlFrameDataUrl(`http://localhost:8000/api/tasks/${taskId}/frame?t=${t.toFixed(3)}`);
    setHlConfigOpen(true);
  };

  // 用 ref 持有最新的 loadTask，避免 addLog（先于 loadTask 定义）产生 TDZ 错误
  // 同时保证 WS 消息处理始终调用当前 taskId 对应的 loadTask，不会出现跨任务 stale 闭包
  const loadTaskRef = useRef<(() => void) | null>(null);

  const addLog = useCallback((msg: LogMessage) => {
    if (msg.type === "log") {
      setConsoleEntries((prev) => {
        const next = [
          ...prev.slice(-199),
          {
            id: `${Date.now()}-${Math.random()}`,
            timestamp: msg.timestamp || new Date().toISOString(),
            level: msg.level || "info",
            source: msg.source || "system",
            message: msg.message || "",
            progress: msg.progress,
          },
        ];
        // 同步持久化到 localStorage，刷新后可恢复
        try {
          localStorage.setItem(consoleStorageKey, JSON.stringify(next));
        } catch {
          // localStorage 写入失败时静默忽略（如隐私模式）
        }
        return next;
      });
    }
    if (msg.type === "claude_failed") {
      setClaudeFailedError(msg.error || "未知错误");
      setClaudeFailedDialogOpen(true);
      loadTaskRef.current?.();
    }
    if (msg.type === "status_change" || msg.type === "export_done" || msg.type === "export_cancelled") {
      // 立即同步更新前端 task.status，不等 HTTP loadTask() 返回
      // 解决：WS 日志到达时 isProcessing 还是旧状态导致动画不显示的竞态问题
      if (msg.status) {
        setTask(prev => prev ? { ...prev, status: msg.status as TaskStatus } : prev);
      }
      loadTaskRef.current?.();
    }
  }, [consoleStorageKey]);

  const loadTask = useCallback(async () => {
    try {
      const data = await fetchTask(taskId);
      setTask(data);
      // 同步金句开场顺序（从后端最新数据中读取）
      setGoldenQuoteOrder(data.golden_quote_order || []);
      // 派生精修任务首次加载时默认开启"仅保留"
      if (!initialShowOnlyKeptSet.current && data.source_task_id) {
        setShowOnlyKept(true);
        initialShowOnlyKeptSet.current = true;
      }
      // 如果任务已完成 ASR 但 console 最后一条还是进行中的消息（断线导致日志丢失），补一条提示
      const asrCompletedStatuses = ["asr_done", "seg_optim_running", "seg_optim_done", "audit_running", "review", "export_running", "done"];
      if (asrCompletedStatuses.includes(data.status)) {
        setConsoleEntries(prev => {
          if (prev.length === 0) return prev;
          const last = prev[prev.length - 1];
          const isStaleInProgress = ["开始字符级", "正在提取", "正在从本地", "开始说话人"].some(p => last.message.includes(p));
          if (!isStaleInProgress) return prev;
          const note: ConsoleEntry = {
            id: `stale-note-${Date.now()}`,
            timestamp: new Date().toISOString(),
            level: "success",
            source: "system",
            message: `（连接中断恢复：ASR 已完成，部分进度日志未能实时接收）`,
          };
          const next = [...prev, note];
          try { localStorage.setItem(`gc_console_${taskId}`, JSON.stringify(next)); } catch { /* ignore */ }
          return next;
        });
      }
    } catch {
      toast.error("任务不存在");
      navigate("/");
    } finally {
      setLoading(false);
    }
  }, [taskId]);

  // 保持 ref 与最新 loadTask 同步，供 addLog 中 WS 回调使用
  loadTaskRef.current = loadTask;

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

  // Auto-highlight active segment based on video time
  useEffect(() => {
    if (previewVideoUrl || !task?.audit_segments) return;
    const active = task.audit_segments.find(
      (s) => currentTime >= s.start && currentTime <= s.end
    );
    if (active && active.id !== activeSegmentId) {
      setActiveSegmentId(active.id);
    }
  }, [currentTime, task?.audit_segments, activeSegmentId, previewVideoUrl]);

  // 字幕列表自动滚动到当前高亮段
  useEffect(() => {
    if (!activeSegmentId || !segmentListRef.current) return;
    const el = segmentListRef.current.querySelector(
      `[data-segment-id="${activeSegmentId}"]`
    );
    if (el) el.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [activeSegmentId]);

  // 审计完成后若存在删除片段，默认切换为"播放保留字幕"
  useEffect(() => {
    if (!task) return;
    const postAuditStatuses = ["review", "done", "export_running"];
    if (
      postAuditStatuses.includes(task.status) &&
      task.audit_segments?.some((s) => s.action === "delete")
    ) {
      setPlayKeptOnly(true);
    }
  }, [task?.status]);

  // 审计完成前，片段列表只读——用户只能在 AI 审计后再加工
  // done 状态也允许编辑，方便导出后继续调整再次导出
  const isReadOnly = !["review", "done", "asr_done", "seg_optim_done"].includes(task?.status ?? "");

  // 返回前检查：有未提交的修改时提示用户（必须在早返回之前声明，避免 Hooks 数量变化）
  const handleBack = useCallback(() => {
    const segs = task?.audit_segments || [];
    const count = segs.filter(
      (s) => (s as any).claude_action && s.action !== (s as any).claude_action
    ).length;
    if (count > 0 && !nudgeDismissed) {
      toast(`你有 ${count} 处修改未提交给 Claude 学习`, {
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

  const handleSegmentClick = (seg: Segment, e: React.MouseEvent) => {
    const segments = task?.audit_segments || [];
    // 审计完成前禁止多选操作
    const isMeta = !isReadOnly && (e.metaKey || e.ctrlKey);
    const isShift = !isReadOnly && e.shiftKey;

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

    setActiveSegmentId(seg.id);
    setCurrentTime(seg.start);
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
        reason: newAction === "keep" ? "手动恢复" : seg.reason,
      };
    });
    setTask({ ...task, audit_segments: newSegments });
    // 弹出原因选择浮窗
    setPendingReasonSeg({ id: segId, action: toggledAction });

    // 存在 delete 段时自动切换为"播放保留字幕"，让用户立即感知删除效果
    if (newSegments.some((s) => s.action === "delete")) {
      setPlayKeptOnly(true);
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

  // ── 编辑保存 ──
  const handleEditSave = async (segId: string, newText: string, start: number, end: number) => {
    try {
      const seg = task!.audit_segments.find((s) => s.id === segId);
      if (!seg) return;
      const patch: Record<string, any> = {};
      if (newText !== seg.text) patch.text = newText;
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
    setPlayKeptOnly(true);
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
      const target = e.target as HTMLElement;
      if (target.closest("input, textarea, select, [contenteditable=true]")) return;

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

  const handleASR = async (selection: AsrSelection = asrSelection) => {
    try {
      const effectiveThreshold = selection.silenceThreshold;
      await triggerASR(taskId, selection.model, selection.backend, effectiveThreshold, selection.enableDiarization);
      const label = selection.backend === "funasr" ? "FunASR Paraformer-zh" : `Whisper ${selection.model}`;
      const diarLabel = selection.enableDiarization ? "含说话人分离" : "无说话人分离";
      toast.success(`ASR 识别已启动（${label}，${diarLabel}）`);
      setConsoleExpanded(true);
      setAsrDialogOpen(false);
      loadTask();
    } catch (e: any) {
      toast.error(e.message);
    }
  };

  const handleSwitchAsrVersion = (version: number) => {
    // 有审计数据时需二次确认，避免用户无意覆盖已审计结果
    const hasAuditData = task?.audit_segments && task.audit_segments.length > 0;
    if (hasAuditData) {
      setAsrHistoryOpen(false);
      setSwitchAsrConfirmVersion(version);
    } else {
      doSwitchAsrVersion(version);
    }
  };

  const doSwitchAsrVersion = async (version: number) => {
    try {
      await switchASRVersion(taskId, version);
      toast.success(`已切换到 v${version} 识别结果`);
      setAsrHistoryOpen(false);
      setSwitchAsrConfirmVersion(null);
      await loadTask();
    } catch (e: any) {
      toast.error(e.message);
    }
  };

  const handleSwitchSegOptimVersion = (version: number) => {
    // 有审计数据时需二次确认，避免用户无意覆盖已审计结果
    const hasAuditData = task?.audit_segments && task.audit_segments.length > 0;
    if (hasAuditData) {
      setSegOptimHistoryOpen(false);
      setSwitchSegOptimConfirmVersion(version);
    } else {
      doSwitchSegOptimVersion(version);
    }
  };

  const doSwitchSegOptimVersion = async (version: number) => {
    try {
      await switchSegOptimVersion(taskId, version);
      toast.success(`已切换到段落优化 v${version}`);
      setSegOptimHistoryOpen(false);
      setSwitchSegOptimConfirmVersion(null);
      await loadTask();
    } catch (e: any) {
      toast.error(e.message);
    }
  };

  const handleRecover = async () => {
    try {
      const res = await recoverTask(taskId);
      setTask(prev => prev ? { ...prev, status: res.recovered_status as TaskStatus, error_message: undefined } : prev);
      toast.success(`已恢复到 ${res.recovered_status === "review" ? "审阅" : res.recovered_status === "asr_done" ? "ASR 完成" : "初始"} 状态`);
      await loadTask();
    } catch (e: any) {
      toast.error(e.message);
    }
  };

  const handleReOptimize = async () => {
    setTask(prev => prev ? { ...prev, status: "seg_optim_running" as TaskStatus } : prev);
    try {
      await triggerSegmentOptimize(taskId, {
        provider: config.provider || "ollama",
        ollama_model: config.ollamaModel || "deepseek-r1:32b",
        ollama_base_url: config.ollamaBaseUrl || "http://localhost:11434",
        claude_api_key: config.claudeApiKey,
        claude_model: config.claudeModel || undefined,
      });
      toast.success("重新段落优化已启动");
      setConsoleExpanded(true);
      loadTask();
    } catch (e: any) {
      loadTask();
      toast.error(e.message);
    }
  };

  const handleDeriveRefine = async (clip: { startTime: number; endTime: number; title: string }, targetType: "monologue_clean" | "interview_compress") => {
    try {
      const { task_id } = await deriveRefineTask(taskId, {
        target_type: targetType,
        clip_start: clip.startTime,
        clip_end: clip.endTime,
        clip_title: clip.title,
      });
      toast.success("精修任务已创建，正在新窗口打开…");
      window.open(`/tasks/${task_id}`, "_blank");
    } catch (e: any) {
      toast.error(e.message || "创建精修任务失败");
    }
  };

  const handleAudit = async (hlParams?: HighlightAuditParams) => {
    const isOllama = config.provider === "ollama";
    const isHL = task?.task_type === "highlight_reel";

    // Claude 模式下没有 API Key 时，优先弹确认框（在 params dialog 之前）
    if (!isOllama && !config.claudeApiKey) {
      setRuleEngineDialogOpen(true);
      return;
    }

    // 一剪多：先弹参数面板（除非已经从面板里触发进来，即 hlParams 已传入）
    if (isHL && !hlParams) {
      setHighlightAuditParams(getHighlightAuditDefaults(isOllama));
      setHighlightAuditDialogOpen(true);
      return;
    }
    // 乐观更新：立即显示"AI 审计中"指示器，无需等待 WS 推送
    setTask(prev => prev ? { ...prev, status: "audit_running" as TaskStatus } : prev);
    try {
      const p = hlParams;
      await triggerAudit(taskId, {
        claude_api_key: config.claudeApiKey,
        style_mode: styleMode,
        claude_model: config.claudeModel || undefined,
        provider: config.provider || "claude",
        ollama_model: config.ollamaModel || "deepseek-r1:32b",
        ollama_base_url: config.ollamaBaseUrl || "http://localhost:11434",
        ...(p && isHL ? {
          highlight_target_dur: p.targetDurMin * 60,
          highlight_max_clips: p.maxClips,
          highlight_clip_min_dur: p.clipMinDur,
          highlight_clip_max_dur: p.clipMaxDur,
          highlight_total_clips: p.totalClips,
        } : {}),
      });
      toast.success(isOllama ? `本地 ${config.ollamaModel} 审计已启动` : "语义审计已启动");
      setConsoleExpanded(true);
      loadTask();
    } catch (e: any) {
      // 回滚乐观更新
      loadTask();
      toast.error(e.message);
    }
  };

  /**
   * 重置为最早 ASR 版本（v1）并立即重新发起 AI 审计。
   * 适用于 review 状态下希望对原始 SRT 做二次审查的场景。
   */
  const handleResetAndReaudit = async () => {
    const firstVersion = task?.asr_history?.[0]?.version ?? 1;

    // 乐观更新：立即在客户端展示原始 ASR 片段，无需等待后端响应
    const originalSnapshot = task?.asr_history?.find(h => h.version === firstVersion);
    if (originalSnapshot) {
      const resetSegments: Segment[] = originalSnapshot.asr_result.segments.map(seg => ({
        ...seg,
        action: "keep" as const,
        reason: undefined,
        rule: undefined,
        claude_action: undefined,
        claude_reason: undefined,
        user_override: false,
      }));
      setTask(prev => prev ? {
        ...prev,
        audit_segments: resetSegments,
        asr_current_version: firstVersion,
        segments_kept: resetSegments.length,
        segments_deleted: 0,
      } : prev);
    }

    // 1. 后端切回最早 ASR 快照
    try {
      await switchASRVersion(taskId, firstVersion);
    } catch (e: any) {
      toast.error(`恢复原始 ASR 失败: ${e.message}`);
      await loadTask();
      return;
    }
    // 2. 触发审计
    const isOllama = config.provider === "ollama";
    if (!isOllama && !config.claudeApiKey) {
      setRuleEngineDialogOpen(true);
      return;
    }
    try {
      setTask(prev => prev ? { ...prev, status: "audit_running" as TaskStatus } : prev);
      await triggerAudit(taskId, {
        claude_api_key: config.claudeApiKey,
        style_mode: styleMode,
        claude_model: config.claudeModel || undefined,
        provider: config.provider || "claude",
        ollama_model: config.ollamaModel || "deepseek-r1:32b",
        ollama_base_url: config.ollamaBaseUrl || "http://localhost:11434",
      });
      toast.success("已重置为原始 ASR，语义审计已重新启动");
      setConsoleExpanded(true);
    } catch (e: any) {
      toast.error(e.message);
      await loadTask();
    }
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
    if (!manualAuditInput.trim()) { toast.error("请粘贴 Claude 返回的 JSON"); return; }
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

  const handleExportFFmpeg = async (burnSubtitles: boolean, subtitleStyle?: SubtitleStyle, coverImageBase64?: string) => {
    try {
      await exportFFmpeg(taskId, `${task?.name}_output`, burnSubtitles, subtitleStyle, coverImageBase64);
      toast.success("FFmpeg 导出已启动");
      setConsoleExpanded(true);
    } catch (e: any) {
      toast.error(e.message);
    }
  };

  const handleExportJianying = async () => {
    try {
      await exportJianying(taskId, `${task?.name}_draft`, config.jianyingDir || undefined);
      toast.success("剪映草稿生成已启动");
      setConsoleExpanded(true);
    } catch (e: any) {
      toast.error(e.message);
    }
  };

  const handlePlayHighlightClip = async (url: string) => {
    if (!previewVideoUrl) {
      previewRestoreTimeRef.current = currentTime;
    }
    setPreviewVideoUrl(url);
    setCurrentTime(0);
    setPlayKeptOnly(false);
  };

  const handleExitPreview = useCallback(() => {
    const restoreTime = previewRestoreTimeRef.current ?? 0;
    setPreviewVideoUrl(null);
    setCurrentTime(restoreTime);
    previewRestoreTimeRef.current = null;
  }, []);

  const handleExportHighlightClips = async (clipGroups: string[][], burnSubtitles: boolean, subtitleStyle?: SubtitleStyle, hookConfigs?: (HookConfig | null)[], hookOrders?: (string[] | null)[]) => {
    try {
      await exportHighlightClips(taskId, clipGroups, task?.name, burnSubtitles, subtitleStyle, hookConfigs, hookOrders);
      toast.success(`${clipGroups.length} 个片段导出已启动`);
      setConsoleExpanded(true);
    } catch (e: any) {
      toast.error(e.message);
    }
  };

  const handleCancelExport = async () => {
    try {
      await cancelExport(taskId);
      toast.success("导出已终止");
      // 乐观更新：立即设为 review，WebSocket 的 status_change/export_cancelled 消息
      // 会在后端处理完后触发 WS handler 里的 loadTask() 做最终同步。
      // 注意：不能在这里直接调 loadTask()，会产生竞态——
      // 后端异步处理尚未完成时 loadTask 返回的仍是 export_running，覆盖乐观状态。
      setTask(prev => prev ? { ...prev, status: "review" as TaskStatus } : prev);
    } catch (e: any) {
      toast.error(e.message);
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

  const segments = task.audit_segments || [];
  // "只看保留" 模式：keep / subtitle_fix / text_fix / merge_next（都会出现在最终视频中）
  const displaySegments = showOnlyKept
    ? segments.filter((s) => ["keep", "subtitle_fix", "text_fix", "merge_next"].includes(s.action))
    : segments;

  const isProcessing = ["asr_running", "seg_optim_running", "audit_running", "export_running"].includes(task.status);

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
        <span className={cn("status-badge text-[10px] shrink-0", STATUS_COLORS[task.status])}>
          {isProcessing && <Loader2 className="w-2.5 h-2.5 animate-spin" />}
          {STATUS_LABELS[task.status]}
        </span>

        {/* 识别区 */}
        <div className="w-px h-4 bg-border/50 shrink-0 mx-1" />
        <span className="text-[10px] text-foreground font-bold tracking-wider shrink-0">ASR语音识别</span>
        <div className="flex items-center gap-1">
          {/* pending/error 初次运行 */}
          {(task.status === "pending" || task.status === "error") && task.video_path && (
            <Button
              size="sm"
              variant="outline"
              className="border-border text-foreground hover:bg-secondary gap-1.5 text-xs h-7"
              onClick={() => setAsrDialogOpen(true)}
              disabled={isProcessing}
            >
              <Mic className="w-3.5 h-3.5" />
              运行 ASR
            </Button>
          )}

          {/* ASR 已完成后：info chip + 重新识别 */}
          {task.video_path && !["pending", "error"].includes(task.status) && (
            <>
              {task.asr_history && task.asr_history.length > 0 && (() => {
                const latestAsr = task.asr_history[task.asr_history.length - 1];
                return (
                  <Popover open={asrHistoryOpen} onOpenChange={setAsrHistoryOpen}>
                    <PopoverTrigger asChild>
                      <button
                        className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors px-2 py-1 rounded hover:bg-secondary/60 border border-transparent hover:border-border/40"
                        title="查看字幕历史版本"
                      >
                        <Mic className="w-3.5 h-3.5 shrink-0 text-blue-400" />
                        <span className="text-foreground/90 font-medium truncate max-w-[110px]">{latestAsr.engine}</span>
                        <span className="text-muted-foreground/30">·</span>
                        <span>{formatAsrTime(latestAsr.created_at)}</span>
                        {task.asr_history.length > 1 && (
                          <span className="text-[10px] bg-secondary px-1 py-0.5 rounded font-mono text-muted-foreground/60">{task.asr_history.length}</span>
                        )}
                      </button>
                    </PopoverTrigger>
                    <PopoverContent align="start" className="w-80 p-0 bg-card border-border">
                      <div className="px-3 py-2 border-b border-border">
                        <p className="text-xs font-medium text-foreground">字幕识别历史</p>
                        <p className="text-[10px] text-muted-foreground/60 mt-0.5">共 {task.asr_history.length} 次识别，点击切换</p>
                      </div>
                      <div className="max-h-72 overflow-y-auto">
                        {[...task.asr_history].reverse().map((snap: ASRSnapshot) => {
                          const isCurrent = snap.version === task.asr_current_version;
                          return (
                            <div
                              key={snap.version}
                              className={cn(
                                "flex items-center gap-3 px-3 py-2.5 border-b border-border/40 last:border-0",
                                isCurrent ? "bg-primary/8" : "hover:bg-secondary/40 cursor-pointer"
                              )}
                              onClick={() => !isCurrent && !isProcessing && handleSwitchAsrVersion(snap.version)}
                            >
                              <span className={cn(
                                "text-[10px] font-mono font-medium shrink-0 w-6 text-center py-0.5 rounded",
                                isCurrent ? "bg-primary/20 text-primary" : "bg-secondary text-muted-foreground"
                              )}>
                                v{snap.version}
                              </span>
                              <div className="flex-1 min-w-0">
                                <div className="flex items-center gap-1.5">
                                  <span className="text-[11px] text-foreground truncate">{snap.engine}</span>
                                  {isCurrent && (
                                    <span className="text-[9px] bg-primary/20 text-primary px-1 py-0.5 rounded shrink-0">当前</span>
                                  )}
                                </div>
                                <div className="flex items-center gap-2 mt-0.5">
                                  <span className="text-[10px] text-muted-foreground/60">{snap.segments_count} 个片段</span>
                                  <span className="text-[10px] text-muted-foreground/40">·</span>
                                  <span className="text-[10px] text-muted-foreground/60">{formatAsrTime(snap.created_at)}</span>
                                </div>
                              </div>
                              {!isCurrent && (
                                <button
                                  className="text-[10px] text-primary hover:text-primary/80 shrink-0 px-1.5 py-0.5 rounded hover:bg-primary/10 transition-colors"
                                  disabled={isProcessing}
                                  onClick={(e) => { e.stopPropagation(); handleSwitchAsrVersion(snap.version); }}
                                >
                                  切换
                                </button>
                              )}
                            </div>
                          );
                        })}
                      </div>
                      <div className="px-3 py-2 border-t border-border/40">
                        <button
                          className="w-full text-[10px] text-muted-foreground hover:text-foreground flex items-center gap-1.5 justify-center py-1 rounded hover:bg-secondary/40 transition-colors"
                          onClick={() => { setAsrHistoryOpen(false); setAsrDialogOpen(true); }}
                        >
                          <RotateCcw className="w-3 h-3" />
                          重新识别
                        </button>
                      </div>
                    </PopoverContent>
                  </Popover>
                );
              })()}
              <Button
                size="sm"
                variant="outline"
                className="border-border text-foreground hover:bg-secondary gap-1.5 text-xs h-7"
                onClick={() => setAsrDialogOpen(true)}
                disabled={isProcessing}
              >
                <RotateCcw className="w-3.5 h-3.5" />
                重新识别
              </Button>
            </>
          )}
        </div>

        {/* 识别/审计分隔 */}
        <div className="w-px h-4 bg-border/50 shrink-0 mx-1" />

        {/* 段落优化区 */}
        {task.task_type !== "highlight_reel" && (task.status === "asr_done" || task.status === "seg_optim_running") && (
          <div className="flex items-center gap-1 mr-1">
            {task.status === "seg_optim_running" ? (
              <div className="flex items-center gap-1.5 text-xs text-sky-400 animate-pulse">
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
                <span className="font-medium">段落优化中</span>
              </div>
            ) : (
              <>
                <Button
                  size="sm"
                  className="bg-sky-600 text-white hover:bg-sky-500 gap-1.5 text-xs h-7"
                  onClick={async () => {
                    setTask(prev => prev ? { ...prev, status: "seg_optim_running" as TaskStatus } : prev);
                    try {
                      await triggerSegmentOptimize(taskId, {
                        provider: config.provider || "ollama",
                        ollama_model: config.ollamaModel || "deepseek-r1:32b",
                        ollama_base_url: config.ollamaBaseUrl || "http://localhost:11434",
                        claude_api_key: config.claudeApiKey,
                        claude_model: config.claudeModel || undefined,
                      });
                      toast.success("段落优化已启动");
                      setConsoleExpanded(true);
                      loadTask();
                    } catch (e: any) {
                      loadTask();
                      toast.error(e.message);
                    }
                  }}
                  disabled={isProcessing}
                  title="修正断句错误、合并碎段，节约主 LLM token"
                >
                  <Sparkles className="w-3.5 h-3.5" />
                  段落优化
                </Button>
                <ModelPickerPopover config={config} updateConfig={updateConfig} disabled={isProcessing} />
                <Button
                  size="sm"
                  variant="ghost"
                  className="text-muted-foreground hover:text-foreground text-xs h-7 px-2"
                  onClick={async () => {
                    try {
                      await skipSegmentOptimize(taskId);
                      loadTask();
                    } catch (e: any) {
                      toast.error(e.message);
                    }
                  }}
                  disabled={isProcessing}
                  title="跳过段落优化，直接进入 AI 审计"
                >
                  跳过
                </Button>
              </>
            )}
          </div>
        )}

        {/* 段落优化完成后：历史 chip + 重新优化 */}
        {task.task_type !== "highlight_reel" && ["seg_optim_done", "review", "done"].includes(task.status) && (
          <div className="flex items-center gap-1 mr-1">
            {/* 历史版本 chip */}
            {task.seg_optim_history && task.seg_optim_history.length > 0 && (() => {
              const latestSnap = task.seg_optim_history[task.seg_optim_history.length - 1];
              return (
                <Popover open={segOptimHistoryOpen} onOpenChange={setSegOptimHistoryOpen}>
                  <PopoverTrigger asChild>
                    <button
                      className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors px-1.5 py-1 rounded hover:bg-secondary/60 border border-transparent hover:border-border/40"
                      title={`段落优化 v${latestSnap.version}（${latestSnap.segments_before}→${latestSnap.segments_after} 段）点击查看历史`}
                    >
                      <Sparkles className="w-3 h-3 text-sky-400 shrink-0" />
                      <span className="font-mono text-[10px] text-sky-400/80">v{latestSnap.version}</span>
                      {task.seg_optim_history.length > 1 && (
                        <span className="text-[9px] bg-secondary px-1 py-0.5 rounded font-mono text-muted-foreground/60">{task.seg_optim_history.length}</span>
                      )}
                    </button>
                  </PopoverTrigger>
                  <PopoverContent align="start" className="w-80 p-0 bg-card border-border">
                    <div className="px-3 py-2 border-b border-border">
                      <p className="text-xs font-medium text-foreground">段落优化历史</p>
                      <p className="text-[10px] text-muted-foreground/60 mt-0.5">共 {task.seg_optim_history.length} 次优化，点击切换</p>
                    </div>
                    <div className="max-h-72 overflow-y-auto">
                      {[...task.seg_optim_history].reverse().map((snap: SegOptimSnapshot) => {
                        const isCurrent = snap.version === task.seg_optim_current_version;
                        return (
                          <div
                            key={snap.version}
                            className={cn(
                              "flex items-center gap-3 px-3 py-2.5 border-b border-border/40 last:border-0",
                              isCurrent ? "bg-primary/8" : "hover:bg-secondary/40 cursor-pointer"
                            )}
                            onClick={() => !isCurrent && !isProcessing && handleSwitchSegOptimVersion(snap.version)}
                          >
                            <span className={cn(
                              "text-[10px] font-mono font-medium shrink-0 w-6 text-center py-0.5 rounded",
                              isCurrent ? "bg-sky-500/20 text-sky-400" : "bg-secondary text-muted-foreground"
                            )}>
                              v{snap.version}
                            </span>
                            <div className="flex-1 min-w-0">
                              <div className="flex items-center gap-1.5">
                                <span className="text-[11px] text-foreground truncate">{snap.model}</span>
                                {isCurrent && (
                                  <span className="text-[9px] bg-sky-500/20 text-sky-400 px-1 py-0.5 rounded shrink-0">当前</span>
                                )}
                              </div>
                              <div className="flex items-center gap-2 mt-0.5">
                                <span className="text-[10px] text-muted-foreground/60">{snap.segments_before}→{snap.segments_after} 段</span>
                                <span className="text-[10px] text-muted-foreground/40">·</span>
                                <span className="text-[10px] text-muted-foreground/60">{formatAsrTime(snap.created_at)}</span>
                              </div>
                            </div>
                            {!isCurrent && (
                              <button
                                className="text-[10px] text-sky-400 hover:text-sky-300 shrink-0 px-1.5 py-0.5 rounded hover:bg-sky-500/10 transition-colors"
                                disabled={isProcessing}
                                onClick={(e) => { e.stopPropagation(); handleSwitchSegOptimVersion(snap.version); }}
                              >
                                切换
                              </button>
                            )}
                          </div>
                        );
                      })}
                    </div>
                    <div className="px-3 py-2 border-t border-border/40">
                      <button
                        className="w-full text-[10px] text-muted-foreground hover:text-foreground flex items-center gap-1.5 justify-center py-1 rounded hover:bg-secondary/40 transition-colors"
                        onClick={() => { setSegOptimHistoryOpen(false); handleReOptimize(); }}
                        disabled={isProcessing}
                      >
                        <RotateCcw className="w-3 h-3" />
                        重新优化
                      </button>
                    </div>
                  </PopoverContent>
                </Popover>
              );
            })()}
            {/* 重新优化按钮 */}
            <Button
              size="sm"
              variant="outline"
              className="border-border text-foreground hover:bg-secondary gap-1.5 text-xs h-7"
              onClick={handleReOptimize}
              disabled={isProcessing}
              title="重新运行段落优化（会保存当前版本到历史）"
            >
              <RotateCcw className="w-3.5 h-3.5" />
              重新优化
            </Button>
            <ModelPickerPopover config={config} updateConfig={updateConfig} disabled={isProcessing} />
          </div>
        )}

        <span className="text-[10px] text-foreground font-bold tracking-wider shrink-0">AI审计</span>

        {/* 审计区 */}
        <div className="flex items-center gap-1">
          {/* 审计中状态指示器 */}
          {task.status === "audit_running" && (
            <div className="flex items-center gap-1.5 text-xs text-amber-400 animate-pulse">
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
              <span className="font-medium">AI 审计中</span>
            </div>
          )}

          {(task.status === "seg_optim_done" || task.status === "review" || task.status === "done" || (task.status === "asr_done" && task.task_type === "highlight_reel")) && (
            <>
              {/* 审计 info chip — review 状态始终显示，无历史时用 updated_at 兜底 */}
              {task.status === "review" && (() => {
                const auditHistory = task.audit_history ?? [];
                const latestAudit = auditHistory.length > 0 ? auditHistory[auditHistory.length - 1] : null;
                const displayModel = latestAudit?.model ?? "已审计";
                const displayTime = latestAudit?.created_at ?? task.updated_at;
                return (
                  <Popover open={auditHistoryOpen} onOpenChange={setAuditHistoryOpen}>
                    <PopoverTrigger asChild>
                      <button
                        className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors px-2 py-1 rounded hover:bg-secondary/60 border border-transparent hover:border-border/40"
                        title="查看审计历史"
                      >
                        <Zap className="w-3.5 h-3.5 shrink-0 text-amber-400" />
                        <span className="text-foreground/90 font-medium truncate max-w-[120px]">{displayModel}</span>
                        <span className="text-muted-foreground/30">·</span>
                        <span>{formatAsrTime(displayTime)}</span>
                        {auditHistory.length > 1 && (
                          <span className="text-[10px] bg-secondary px-1 py-0.5 rounded font-mono text-muted-foreground/60">{auditHistory.length}</span>
                        )}
                      </button>
                    </PopoverTrigger>
                    <PopoverContent align="start" className="w-80 p-0 bg-card border-border">
                      <div className="px-3 py-2 border-b border-border">
                        <p className="text-xs font-medium text-foreground">审计历史</p>
                        <p className="text-[10px] text-muted-foreground/60 mt-0.5">
                          {auditHistory.length > 0 ? `共 ${auditHistory.length} 次审计` : "历史记录从本次更新起开始追踪"}
                        </p>
                      </div>
                      <div className="max-h-72 overflow-y-auto">
                        {auditHistory.length === 0 ? (
                          <div className="px-3 py-4 text-center">
                            <p className="text-[11px] text-muted-foreground/50">暂无版本历史</p>
                            <p className="text-[10px] text-muted-foreground/30 mt-1">下次审计完成后将在此记录</p>
                          </div>
                        ) : (
                          [...auditHistory].reverse().map((snap: AuditSnapshot) => {
                            const isCurrent = snap.version === task.audit_current_version;
                            return (
                              <div
                                key={snap.version}
                                className={cn(
                                  "flex items-center gap-3 px-3 py-2.5 border-b border-border/40 last:border-0",
                                  isCurrent ? "bg-primary/8" : "bg-transparent"
                                )}
                              >
                                <span className={cn(
                                  "text-[10px] font-mono font-medium shrink-0 w-6 text-center py-0.5 rounded",
                                  isCurrent ? "bg-primary/20 text-primary" : "bg-secondary text-muted-foreground"
                                )}>
                                  v{snap.version}
                                </span>
                                <div className="flex-1 min-w-0">
                                  <div className="flex items-center gap-1.5">
                                    <span className="text-[11px] text-foreground truncate">{snap.model}</span>
                                    {isCurrent && (
                                      <span className="text-[9px] bg-primary/20 text-primary px-1 py-0.5 rounded shrink-0">当前</span>
                                    )}
                                  </div>
                                  <div className="flex items-center gap-2 mt-0.5">
                                    <span className="text-[10px] text-green-400/70">{snap.segments_kept} 保留</span>
                                    <span className="text-[10px] text-muted-foreground/40">·</span>
                                    <span className="text-[10px] text-red-400/70">{snap.segments_deleted} 删除</span>
                                    <span className="text-[10px] text-muted-foreground/40">·</span>
                                    <span className="text-[10px] text-muted-foreground/60">{formatAsrTime(snap.created_at)}</span>
                                  </div>
                                </div>
                              </div>
                            );
                          })
                        )}
                      </div>
                    </PopoverContent>
                  </Popover>
                );
              })()}

              {/* AI 审计按钮 + 模型选择器 */}
              <Button
                size="sm"
                className="bg-primary text-primary-foreground hover:bg-primary/90 gap-1.5 text-xs h-7"
                onClick={() => {
                  if (task.status === "review" && task.task_type !== "highlight_reel") {
                    setReauditMode("keep");
                    setReauditDialogOpen(true);
                  } else {
                    handleAudit();
                  }
                }}
                disabled={isProcessing}
              >
                {isProcessing ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Zap className="w-3.5 h-3.5" />}
                AI 审计
              </Button>

              <ModelPickerPopover config={config} updateConfig={updateConfig} disabled={isProcessing} />

              {/* 离线审计按钮 */}
              <Button
                size="sm"
                variant="outline"
                className="border-border text-muted-foreground hover:text-foreground hover:bg-secondary gap-1 text-xs h-7 px-2"
                onClick={handleOpenManualAudit}
                disabled={isProcessing}
                title="下载提示词，在 Cursor 等工具中运行 AI，粘贴结果回来"
              >
                <FileDown className="w-3 h-3" />
                离线
              </Button>


              {/* 优化提示词：有用户修改时显示 */}
              {(task.status === "review" || task.status === "done") &&
                segments.some((s) => (s as any).claude_action && s.action !== (s as any).claude_action) && (
                <Button
                  size="sm"
                  variant="outline"
                  className="border-primary/40 text-primary hover:bg-primary/10 gap-1.5 text-xs h-7"
                  onClick={() => setPromptFeedbackDialogOpen(true)}
                >
                  <Sparkles className="w-3 h-3" />
                  优化提示词
                  <span className="text-[9px] bg-primary/20 px-1 py-0.5 rounded-full font-mono">
                    {segments.filter((s) => (s as any).claude_action && s.action !== (s as any).claude_action).length}
                  </span>
                </Button>
              )}
            </>
          )}
        </div>

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
              onClick={() => {
                const next = !showOnlyKept;
                setShowOnlyKept(next);
                // 切到"显示全部"时自动定位到第一个 keep 段
                if (!next && segmentListRef.current) {
                  const firstKeep = segments.find(s => s.action === "keep");
                  if (firstKeep) {
                    setTimeout(() => {
                      const el = segmentListRef.current?.querySelector(`[data-segment-id="${firstKeep.id}"]`);
                      el?.scrollIntoView({ behavior: "smooth", block: "start" });
                    }, 50);
                  }
                }
              }}
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
      <StatusStepper status={task.status} taskType={task.task_type} onRecover={task.status === "error" ? handleRecover : undefined} />

      {/* Main 3-panel layout */}
      <div className="flex-1 overflow-hidden flex">
        {/* Left: Video Player (35%) */}
        <div className="w-[35%] shrink-0 border-r border-border p-3 flex flex-col gap-3">
          <div className="flex-1 min-h-0">
            {task.video_path ? (
              <div className="h-full flex flex-col">
                <div className="flex-1 min-h-0">
                  <VideoPlayer
                    taskId={taskId}
                    currentTime={currentTime}
                    onTimeUpdate={setCurrentTime}
                    segments={segments}
                    playKeptOnly={playKeptOnly}
                    onPlayKeptOnlyChange={setPlayKeptOnly}
                    clipStart={previewVideoUrl ? null : task.clip_start}
                    clipEnd={previewVideoUrl ? null : task.clip_end}
                    videoUrl={previewVideoUrl}
                    previewMode={!!previewVideoUrl}
                    onExitPreview={handleExitPreview}
                    onPreviewEnded={handleExitPreview}
                  />
                </div>
              </div>
            ) : (
              <div className="h-full bg-secondary rounded-xl flex flex-col items-center justify-center gap-3">
                <div className="w-12 h-12 rounded-xl bg-card flex items-center justify-center">
                  <Play className="w-6 h-6 text-muted-foreground/40 ml-0.5" />
                </div>
                <p className="text-xs text-muted-foreground">暂无视频</p>
              </div>
            )}
          </div>

          {/* Export Panel — highlight_reel 不显示（导出按钮在中栏片段列表里） */}
          {task.task_type !== "highlight_reel" && (task.status === "review" || task.status === "done" || task.status === "asr_done" || task.status === "seg_optim_done" || task.status === "export_running") && (
            <ExportPanel
              task={task}
              jianyingDir={config.jianyingDir}
              onExportFFmpeg={handleExportFFmpeg}
              onExportJianying={handleExportJianying}
              onCancelExport={handleCancelExport}
              apiKey={config.claudeApiKey}
              provider={config.provider}
              claudeModel={config.claudeModel}
              ollamaModel={config.ollamaModel}
              ollamaBaseUrl={config.ollamaBaseUrl}
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
          )}
        </div>

        {/* Center: Subtitle Audit Stream / Highlight Clips (40%) */}
        {/* highlight_reel 审计完成后，整个中栏替换为片段列表 */}
        {task.task_type === "highlight_reel" && ["review", "done", "export_running"].includes(task.status) ? (
          <div className="flex-1 min-w-0 flex flex-col border-r border-border p-3">
            <SubtitleConfigDialog
              open={hlConfigOpen}
              onClose={() => setHlConfigOpen(false)}
              style={hlSubtitleStyle}
              onChange={setHlSubtitleStylePersisted}
              frameDataUrl={hlFrameDataUrl}
              videoAspectRatio={hlVideoAspectRatio}
              videoDuration={hlVideoDuration}
              videoCurrentTime={hlVideoCurrentTime}
              taskId={taskId!}
            />
            <HookConfigDialog
              open={hlHookConfigOpen}
              onClose={() => setHlHookConfigOpen(false)}
              hookStyle={hlHookStyle}
              onChange={setHlHookStylePersisted}
              frameDataUrl={hlHookFrameDataUrl}
              videoAspectRatio={hlHookVideoAspectRatio}
              videoDuration={hlHookVideoDuration}
              videoCurrentTime={hlHookVideoCurrentTime}
              taskId={taskId!}
            />
            <HighlightClipsPanel
              task={task}
              isExporting={task.status === "export_running"}
              onPlayClip={handlePlayHighlightClip}
              onExport={handleExportHighlightClips}
              onRefine={handleDeriveRefine}
              subtitleStyle={hlSubtitleStyle}
              onOpenSubtitleStyle={openHlSubtitleConfig}
              hookEnabled={hlHookEnabled}
              onHookEnabledChange={setHlHookEnabledPersisted}
              hookStyle={hlHookStyle}
              onOpenHookStyle={openHlHookConfig}
              apiKey={config.claudeApiKey}
              provider={config.provider}
              claudeModel={config.claudeModel}
              ollamaModel={config.ollamaModel}
              ollamaBaseUrl={config.ollamaBaseUrl}
            />
          </div>
        ) : (
        <div className="flex-1 min-w-0 flex flex-col border-r border-border">
          {/* Header */}
          <div className="h-9 shrink-0 border-b border-border flex items-center px-3 gap-3">
            <span className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
              {task.task_type === "highlight_reel" ? "转录预览" : "字幕审计流"}
            </span>
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
                <span className="text-green-400 font-mono">
                  {segments.filter(s => ["keep","subtitle_fix","text_fix","merge_next"].includes(s.action)).length} ✓
                </span>
                <span className="text-red-400 font-mono">
                  {segments.filter(s => s.action === "delete").length} ✗
                </span>
                {task.edited_duration && (
                  <span className="timestamp">{formatDuration(task.edited_duration)}</span>
                )}
              </div>
            )}
          </div>

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
            {/* 预处理统计面板：ASR 完成后持续显示 */}
            {task.task_type !== "highlight_reel" && task.asr_result?.preprocess_stats &&
              ["asr_done", "seg_optim_running", "seg_optim_done", "audit_running", "review", "done"].includes(task.status) &&
              (() => {
                const isPostOptim = ["seg_optim_done", "audit_running", "review", "done"].includes(task.status);
                return (
                  <PreprocStatsPanel
                    stats={task.asr_result.preprocess_stats}
                    currentSegCount={isPostOptim ? task.audit_segments.length : undefined}
                  />
                );
              })()
            }
            {/* ASR 完成、段落优化待触发 */}
            {task.status === "asr_done" && task.task_type !== "highlight_reel" && segments.length > 0 && (
              <div className="flex items-center gap-2 mb-3 px-3 py-2 rounded-lg bg-sky-950/40 border border-sky-800/40 text-sky-400 text-xs">
                <AlertCircle className="w-3.5 h-3.5 shrink-0" />
                <span>ASR 识别完成。建议先运行「段落优化」修正断句错误，或点击「跳过」直接 AI 审计。</span>
              </div>
            )}
            {/* 段落优化完成、审计未开始 */}
            {task.status === "seg_optim_done" && segments.length > 0 && (
              <div className="flex items-center gap-2 mb-3 px-3 py-2 rounded-lg bg-primary/10 border border-primary/20 text-primary text-xs">
                <AlertCircle className="w-3.5 h-3.5 shrink-0" />
                <span>段落优化完成，请点击「AI 审计」开始语义审计。</span>
              </div>
            )}
            {/* ASR 重新识别中：有旧版本时顶部显示提示条 */}
            {task.status === "asr_running" && task.asr_history && task.asr_history.length > 0 && (
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
            {task.status === "audit_running" && segments.length > 0 && (
              <div className="flex items-center gap-2 mb-3 px-3 py-2 rounded-lg bg-amber-950/40 border border-amber-800/40 text-amber-400 text-xs">
                <Loader2 className="w-3.5 h-3.5 animate-spin shrink-0" />
                <span>AI 审计中，结果将在完成后自动更新...</span>
              </div>
            )}
            {segments.length === 0 ? (
              <div className="flex flex-col items-center justify-center h-full text-center gap-3">
                {task.status === "pending" && !task.video_path && (
                  <>
                    <div className="w-10 h-10 rounded-xl bg-secondary flex items-center justify-center">
                      <Upload className="w-5 h-5 text-muted-foreground/40" />
                    </div>
                    <p className="text-xs text-muted-foreground">请先上传视频文件</p>
                  </>
                )}
                {task.status === "pending" && task.video_path && (
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
                {(task.status === "asr_running" || task.status === "seg_optim_running" || task.status === "audit_running") && (
                  <>
                    <Loader2 className="w-8 h-8 animate-spin text-primary" />
                    <p className="text-xs text-muted-foreground">
                      {task.status === "asr_running" ? "ASR 识别中..." : task.status === "seg_optim_running" ? "段落优化中，请稍候..." : "AI 审计中，请稍候..."}
                    </p>
                  </>
                )}
              </div>
            ) : (
              displaySegments.map((seg, idx) => {
                // 判断此段是否在裁剪区间之外（用于视觉提示）
                const cs = task.clip_start ?? null;
                const ce = task.clip_end ?? null;
                const isOutOfClip =
                  (cs !== null && seg.end <= cs) ||
                  (ce !== null && seg.start >= ce);
                return (
                  <div
                    key={seg.id}
                    data-segment-id={seg.id}
                    className={isOutOfClip ? "relative" : undefined}
                    title={isOutOfClip ? "此段在裁剪区间之外，不会被导出" : undefined}
                  >
                    <SegmentItem
                      segment={seg}
                      isActive={seg.id === activeSegmentId}
                      isSelected={selectedIds.has(seg.id)}
                      isLast={idx === displaySegments.length - 1}
                      onClick={(e) => handleSegmentClick(seg, e)}
                      onToggle={(e) => { e.stopPropagation(); handleToggleSegment(seg.id); }}
                      onCharClick={(time) => setCurrentTime(time)}
                      onSeek={(time) => setCurrentTime(time)}
                      activeCharTime={seg.id === activeSegmentId ? currentTime : -1}
                      onEditSave={handleEditSave}
                      onMergeNext={handleMergeNext}
                      readOnly={isReadOnly}
                      wordEditMode={wordEditSegId === seg.id}
                      selectedWordIndices={wordEditSegId === seg.id ? selectedWordIndices : undefined}
                      onWordEditEnter={handleWordEditEnter}
                      onWordEditExit={handleWordEditExit}
                      onWordToggle={handleWordToggle}
                      onWordCommit={handleWordCommit}
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

              })
            )}
          </div>

          {/* 学习提示浮动条 */}
          {feedbackNudgeShown && !promptFeedbackDialogOpen && (() => {
            const modifiedCount = segments.filter(
              (s) => (s as any).claude_action && s.action !== (s as any).claude_action
            ).length;
            if (modifiedCount === 0) return null;
            return (
              <div className="shrink-0 border-t border-primary/30 bg-primary/5 px-3 py-2 flex items-center gap-2 animate-in fade-in slide-in-from-bottom-1 duration-200">
                <Sparkles className="w-3.5 h-3.5 text-primary shrink-0" />
                <span className="text-xs text-foreground/80 flex-1">
                  你修改了 {modifiedCount} 处，让 Claude 学习你的剪辑偏好？
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
        )}

        {/* Right: Speaker Panel + Scenario Info (25%) */}
        <div className="w-56 shrink-0 overflow-hidden flex flex-col">
          {/* Scenario Info Banner */}
          <ScenarioInfoPanel
            task={task}
            styleMode={styleMode}
            onStyleModeChange={setStyleMode}
            claudeApiKey={config.claudeApiKey}
            provider={config.provider}
            ollamaModel={config.ollamaModel}
          />
          <div className="flex-1 overflow-hidden">
            {task.task_type === "highlight_reel" ? (
              <div className="h-full flex flex-col items-center justify-center gap-2 text-center px-4">
                <div className="w-8 h-8 rounded-lg bg-secondary flex items-center justify-center">
                  <Sparkles className="w-4 h-4 text-muted-foreground/30" />
                </div>
                <p className="text-[11px] text-muted-foreground/40">功能开发中</p>
              </div>
            ) : (
              <SpeakerPanel task={task} onRefresh={loadTask} />
            )}
          </div>
        </div>
      </div>

      {/* Bottom: Console */}
      <ConsoleLog
        entries={consoleEntries}
        expanded={consoleExpanded}
        isRunning={isProcessing}
        onToggle={() => setConsoleExpanded(!consoleExpanded)}
        onClear={() => {
          setConsoleEntries([]);
          try { localStorage.removeItem(consoleStorageKey); } catch { /* ignore */ }
        }}
      />

      {/* ASR 模型选择弹窗 */}
      <AsrModelDialog
        open={asrDialogOpen}
        onClose={() => setAsrDialogOpen(false)}
        videoDuration={task.video_duration || 0}
        selection={asrSelection}
        onSelect={setAsrSelection}
        onConfirm={() => handleASR(asrSelection)}
        isProcessing={isProcessing}
        taskType={task.task_type}
      />

      {/* 一剪多 AI 审计参数弹窗 */}
      <HighlightAuditParamsDialog
        open={highlightAuditDialogOpen}
        onClose={() => setHighlightAuditDialogOpen(false)}
        isOllama={config.provider === "ollama"}
        params={highlightAuditParams}
        onChange={setHighlightAuditParams}
        onConfirm={() => {
          setHighlightAuditDialogOpen(false);
          handleAudit(highlightAuditParams);
        }}
        isProcessing={isProcessing}
      />

      {/* 无 API Key 时的确认对话框 */}
      <Dialog open={ruleEngineDialogOpen} onOpenChange={setRuleEngineDialogOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <AlertCircle className="w-5 h-5 text-amber-400" />
              未配置 API Key
            </DialogTitle>
            <DialogDescription className="text-sm text-muted-foreground pt-1">
              使用 AI 审计需要在设置中填写 OpenRouter 或 Anthropic API Key。
            </DialogDescription>
          </DialogHeader>
          <div className="text-sm text-muted-foreground space-y-3 py-1">
            <p>
              你可以使用内置的<span className="text-foreground font-medium">本地规则引擎</span>进行初步审计，
              它会根据片段时长、语气词、重说等规则自动标记，
              但准确度低于 Claude。
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

      {/* 重新审计选择弹窗（review 状态下点击「AI 审计」触发） */}
      <Dialog open={reauditDialogOpen} onOpenChange={setReauditDialogOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Zap className="w-5 h-5 text-primary" />
              重新进行 AI 审计
            </DialogTitle>
            <DialogDescription className="text-sm text-muted-foreground pt-1">
              当前已有审计记录，请选择如何继续：
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2 py-1">
            {/* 选项一：保留手动编辑 */}
            <button
              className={`w-full text-left rounded-md border px-3 py-2.5 transition-colors ${
                reauditMode === "keep"
                  ? "border-primary bg-primary/10"
                  : "border-border hover:border-border/80 hover:bg-secondary/50"
              }`}
              onClick={() => setReauditMode("keep")}
            >
              <p className="text-sm font-medium">保留手动编辑，仅重新跑 AI</p>
              <p className="text-xs text-muted-foreground mt-0.5">
                基于当前片段列表重新提交，保留你的手动 keep / delete 改动作为参考
              </p>
            </button>
            {/* 选项二：从头开始 */}
            <button
              className={`w-full text-left rounded-md border px-3 py-2.5 transition-colors ${
                reauditMode === "reset"
                  ? "border-amber-500 bg-amber-500/10"
                  : "border-border hover:border-border/80 hover:bg-secondary/50"
              }`}
              onClick={() => setReauditMode("reset")}
            >
              <p className="text-sm font-medium">从头开始（丢弃历史）</p>
              <p className="text-xs text-muted-foreground mt-0.5">
                重置为原始 ASR v{task?.asr_history?.[0]?.version ?? 1}，丢弃所有现有审计决策和手动编辑
              </p>
            </button>
            {reauditMode === "reset" && (
              <div className="rounded-md bg-amber-500/10 border border-amber-500/20 px-3 py-2 space-y-1">
                <p className="text-xs text-amber-400 font-medium">以下内容将被丢弃：</p>
                <ul className="text-xs text-amber-300/80 list-disc pl-4 space-y-0.5">
                  <li>AI 上次的所有 keep / delete 决策</li>
                  <li>你手动修改过的片段状态</li>
                  <li>任何手动编辑的文本或时间戳</li>
                </ul>
              </div>
            )}
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => setReauditDialogOpen(false)}
            >
              取消
            </Button>
            <Button
              size="sm"
              className={reauditMode === "reset" ? "bg-amber-500 hover:bg-amber-600 text-black gap-1.5" : "gap-1.5"}
              onClick={() => {
                setReauditDialogOpen(false);
                if (reauditMode === "reset") {
                  handleResetAndReaudit();
                } else {
                  handleAudit();
                }
              }}
              disabled={isProcessing}
            >
              {reauditMode === "reset" ? (
                <RotateCcw className="w-3.5 h-3.5" />
              ) : (
                <Zap className="w-3.5 h-3.5" />
              )}
              确认重新审计
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      {/* Claude API 调用失败的确认对话框 */}
      <Dialog open={claudeFailedDialogOpen} onOpenChange={setClaudeFailedDialogOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <AlertCircle className="w-5 h-5 text-red-400" />
              AI 审计失败
            </DialogTitle>
            <DialogDescription className="text-sm text-muted-foreground pt-1">
              Claude API 调用出错，你可以选择使用本地规则引擎作为替代。
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-3 py-1">
            <div className="rounded-md bg-red-500/10 border border-red-500/20 px-3 py-2">
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
                        <p className="text-foreground/80 font-medium">访谈压缩规则：</p>
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
                        <p className="text-foreground/80 font-medium">精彩集锦规则：</p>
                        <ul className="list-disc pl-4 space-y-1">
                          <li><span className="text-amber-400">Fragment</span> — 片段 &lt; 3 秒自动删除</li>
                          <li><span className="text-amber-400">Filler</span> — 含语气词且内容短的片段激进删除</li>
                          <li><span className="text-amber-400">高能标注</span> — 最长保留片段标记为核心高能时刻</li>
                          <li><span className="text-amber-400">气口补偿</span> — 快节奏：前后各加 80ms / 50ms</li>
                        </ul>
                      </>
                    )}
                    <p className="text-muted-foreground/60 pt-1 italic">
                      规则引擎仅做机械过滤，无法识别语义重复、内容冗余等深层问题。
                    </p>
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
        claudeApiKey={config.claudeApiKey}
        claudeModel={config.claudeModel}
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

      {/* 手动审计弹窗 */}
      <Dialog open={manualAuditDialogOpen} onOpenChange={setManualAuditDialogOpen}>
        <DialogContent className="bg-card border-border max-w-4xl max-h-[90vh] flex flex-col">
          <DialogHeader>
            <DialogTitle className="text-foreground flex items-center gap-2">
              <Hand className="w-4 h-4" />
              手动 AI 审计
            </DialogTitle>
            <DialogDescription className="text-muted-foreground text-sm">
              复制提示词到 Cursor / Claude 等工具运行，将 AI 输出的审计 JSON 粘贴回来
            </DialogDescription>
          </DialogHeader>

          <div className="flex-1 overflow-y-auto space-y-4 min-h-0">
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
                placeholder={'粘贴 Claude 输出的审计 JSON...\n例: [{"i":1,"a":"k"},{"i":2,"a":"d","r":"P1 重说"}]'}
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

      {/* 段落优化版本切换二次确认弹窗 */}
      <Dialog
        open={switchSegOptimConfirmVersion !== null}
        onOpenChange={(open) => { if (!open) setSwitchSegOptimConfirmVersion(null); }}
      >
        <DialogContent className="max-w-sm bg-card border-border">
          <DialogHeader>
            <DialogTitle className="text-sm font-medium text-foreground flex items-center gap-2">
              <AlertCircle className="w-4 h-4 text-amber-400 shrink-0" />
              切换段落优化版本
            </DialogTitle>
            <DialogDescription className="text-xs text-muted-foreground mt-1 leading-relaxed">
              切换到{" "}
              <span className="font-mono font-semibold text-foreground">v{switchSegOptimConfirmVersion}</span>{" "}
              后，当前的全部审计决策（保留 / 删除 / 人工修改）将被重置为默认保留状态，操作不可撤销。
            </DialogDescription>
          </DialogHeader>
          <div className="flex justify-end gap-2 mt-2">
            <Button
              variant="outline"
              size="sm"
              className="text-xs"
              onClick={() => setSwitchSegOptimConfirmVersion(null)}
            >
              取消
            </Button>
            <Button
              size="sm"
              className="text-xs bg-amber-600 hover:bg-amber-500 text-white border-0"
              onClick={() => switchSegOptimConfirmVersion !== null && doSwitchSegOptimVersion(switchSegOptimConfirmVersion)}
            >
              确认切换
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
              onClick={() => switchAsrConfirmVersion !== null && doSwitchAsrVersion(switchAsrConfirmVersion)}
            >
              确认切换
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
