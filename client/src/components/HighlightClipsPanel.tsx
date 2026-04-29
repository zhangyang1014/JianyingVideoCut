import { useEffect, useMemo, useState } from "react";
import {
  ChevronDown, ChevronUp, Crown, Download, Loader2,
  Mic, Pencil, Play, Sparkles, SlidersHorizontal,
  Users, CheckCircle2, X,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription } from "@/components/ui/sheet";
import { ModelPickerPopover } from "@/components/ModelPickerPopover";
import type { AppConfig } from "@/hooks/useAppConfig";
import {
  exportClipPreview, formatDuration, formatTimestamp, saveClipHookOrders,
  suggestGoldenQuotes, type GoldenQuoteCandidate,
  type HookConfig, type SubtitleStyle, type Task,
} from "@/lib/api";
import { groupSegmentsIntoHighlightClips, type HighlightClip } from "@/lib/highlight-clips";
import { cn } from "@/lib/utils";
import { toast } from "sonner";

// ============================================================
// AI 设置 localStorage keys（per-clip hook 专用）
// ============================================================
const LS_HOOK_PROVIDER  = "gc_clip_hook_provider";
const LS_HOOK_CL_MODEL  = "gc_clip_hook_claude_model";
const LS_HOOK_OL_MODEL  = "gc_clip_hook_ollama_model";

function loadHookAiSettings() {
  return {
    provider:    (localStorage.getItem(LS_HOOK_PROVIDER) || "claude") as "claude" | "ollama",
    claudeModel: localStorage.getItem(LS_HOOK_CL_MODEL)  || "claude-sonnet-4-6",
    ollamaModel: localStorage.getItem(LS_HOOK_OL_MODEL)  || "deepseek-r1:14b",
  };
}

// ============================================================
// ClipHookDialog
// ============================================================
function ClipHookDialog({
  open,
  clip,
  taskId,
  initialOrder,
  apiKey,
  providerProp,
  claudeModelProp,
  ollamaModel: ollamaModelProp,
  ollamaBaseUrl,
  onSaved,
  onClose,
}: {
  open: boolean;
  clip: HighlightClip | null;
  taskId: string;
  initialOrder: string[];
  apiKey?: string;
  providerProp?: string;
  claudeModelProp?: string;
  ollamaModel?: string;
  ollamaBaseUrl?: string;
  onSaved: (clipKey: string, order: string[]) => void;
  onClose: () => void;
}) {
  const saved = loadHookAiSettings();
  const [provider, setProvider]       = useState<"claude" | "ollama">(saved.provider);
  const [claudeModel, setClaudeModel] = useState(saved.claudeModel);
  const [ollamaModel, setOllamaModel] = useState(saved.ollamaModel);

  const hookAiConfig: AppConfig = {
    claudeApiKey: apiKey || "",
    provider,
    claudeModel,
    ollamaModel,
    ollamaBaseUrl: ollamaBaseUrl || "http://localhost:11434",
    jianyingDir: "",
  };
  const updateHookAiConfig = (patch: Partial<AppConfig>) => {
    if (patch.provider    !== undefined) { setProvider(patch.provider as "claude" | "ollama"); localStorage.setItem(LS_HOOK_PROVIDER, patch.provider); }
    if (patch.claudeModel !== undefined) { setClaudeModel(patch.claudeModel); localStorage.setItem(LS_HOOK_CL_MODEL, patch.claudeModel); }
    if (patch.ollamaModel !== undefined) { setOllamaModel(patch.ollamaModel); localStorage.setItem(LS_HOOK_OL_MODEL, patch.ollamaModel); }
  };

  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [ordered, setOrdered]   = useState<string[]>([]);
  const [candidates, setCandidates] = useState<GoldenQuoteCandidate[]>([]);
  const [analyzed, setAnalyzed] = useState(false);
  const [analyzing, setAnalyzing] = useState(false);
  const [saving, setSaving] = useState(false);

  // 打开时同步已有配置
  useEffect(() => {
    if (open) {
      setSelected(new Set(initialOrder));
      setOrdered(initialOrder);
    }
  }, [open, initialOrder]);

  if (!clip) return null;

  const segMap = Object.fromEntries(clip.segments.map(s => [s.id, s]));

  const toggle = (segId: string) => {
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

  const handleAnalyze = async () => {
    setAnalyzing(true);
    try {
      const effectiveApiKey  = apiKey;
      const effectiveProvider = provider;
      const effectiveClaude  = claudeModel || claudeModelProp || "claude-3-5-haiku-20241022";
      const effectiveOllama  = ollamaModel || ollamaModelProp || "deepseek-r1:14b";

      localStorage.setItem(LS_HOOK_PROVIDER, effectiveProvider);
      localStorage.setItem(LS_HOOK_CL_MODEL, effectiveClaude);
      localStorage.setItem(LS_HOOK_OL_MODEL, effectiveOllama);

      const results = await suggestGoldenQuotes(taskId, {
        provider: effectiveProvider,
        claude_api_key: effectiveApiKey,
        claude_model: effectiveClaude,
        ollama_model: effectiveOllama,
        ollama_base_url: ollamaBaseUrl || "http://localhost:11434",
        segment_ids: clip.segments.map(s => s.id),
      });
      setCandidates(results);
      setAnalyzed(true);
    } catch (e: any) {
      toast.error(`AI 分析失败: ${e.message || "未知错误"}`);
    } finally {
      setAnalyzing(false);
    }
  };

  const handleSave = async () => {
    const clipKey = clip.segments[0].id;
    setSaving(true);
    try {
      await saveClipHookOrders(taskId, { [clipKey]: ordered });
      onSaved(clipKey, ordered);
      toast.success(ordered.length > 0 ? `已设置 ${ordered.length} 个开场片段` : "已清除 Hook 开场");
      onClose();
    } catch (e: any) {
      toast.error(e.message || "保存失败");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Sheet open={open} onOpenChange={v => !v && onClose()}>
      <SheetContent side="right" className="w-[820px] sm:max-w-[820px] flex flex-col p-0 gap-0 bg-card border-l border-border">
        <SheetHeader className="px-5 pt-5 pb-4 border-b border-border shrink-0">
          <SheetTitle className="flex items-center gap-2 text-base">
            <Crown className="w-4 h-4 text-amber-400" />
            Hook 开场设置
          </SheetTitle>
          <SheetDescription className="text-xs text-muted-foreground">
            从该 clip 的片段中选择最吸引人的句子放到开头，制造悬念钩子。
            <span className="ml-1 text-muted-foreground/50 font-mono">{clip.title}</span>
          </SheetDescription>
        </SheetHeader>

        {/* AI 操作栏 */}
        <div className="px-5 py-2 border-b border-border/50 shrink-0 flex items-center gap-2">
          <ModelPickerPopover config={hookAiConfig} updateConfig={updateHookAiConfig} disabled={analyzing} />
          <button
            onClick={handleAnalyze}
            disabled={analyzing}
            className="flex items-center gap-1.5 h-7 px-3 rounded-lg bg-amber-500/15 border border-amber-500/30 text-amber-400 text-[11px] hover:bg-amber-500/25 transition-colors disabled:opacity-50 disabled:cursor-wait"
          >
            {analyzing ? <Loader2 className="w-3 h-3 animate-spin" /> : <Sparkles className="w-3 h-3" />}
            {analyzing ? "AI 分析中…" : analyzed ? "重新分析" : "AI 推荐"}
          </button>
        </div>

        {/* 主体：左右两栏 */}
        <div className="flex flex-1 min-h-0">

          {/* 左栏：开场顺序 */}
          <div className="w-[200px] shrink-0 flex flex-col border-r border-border/50">
            <div className="px-4 py-2.5 border-b border-border/50 shrink-0">
              <p className="text-[11px] font-medium text-muted-foreground uppercase tracking-wider">开场顺序</p>
              <p className="text-[10px] text-muted-foreground/50 mt-0.5">勾选后在此调整顺序</p>
            </div>
            <div className="flex-1 overflow-y-auto px-3 py-2 space-y-1">
              {ordered.length === 0 ? (
                <p className="text-[10px] text-muted-foreground/30 text-center mt-8 leading-relaxed">
                  勾选右栏片段<br />加入开场
                </p>
              ) : ordered.map((segId, idx) => {
                const seg = segMap[segId];
                if (!seg) return null;
                return (
                  <div key={segId} className="flex items-start gap-1.5 px-2 py-2 rounded-md border border-border/50 bg-secondary/20">
                    <span className="w-4 h-4 rounded-full bg-amber-500/20 text-amber-400 text-[10px] flex items-center justify-center font-bold shrink-0 mt-0.5">
                      {idx + 1}
                    </span>
                    <div className="flex-1 min-w-0">
                      <p className="text-[10px] text-muted-foreground/40 font-mono">{formatTimestamp(seg.start)}</p>
                      <p className="text-[11px] text-foreground leading-snug line-clamp-3 mt-0.5">
                        {(seg.display_text || seg.text || "").trim()}
                      </p>
                    </div>
                    <div className="flex flex-col gap-0.5 shrink-0">
                      <button onClick={() => moveUp(segId)} disabled={idx === 0}
                        className="p-0.5 rounded hover:bg-secondary text-muted-foreground hover:text-foreground disabled:opacity-30">
                        <ChevronUp className="w-3 h-3" />
                      </button>
                      <button onClick={() => moveDown(segId)} disabled={idx === ordered.length - 1}
                        className="p-0.5 rounded hover:bg-secondary text-muted-foreground hover:text-foreground disabled:opacity-30">
                        <ChevronDown className="w-3 h-3" />
                      </button>
                      <button onClick={() => toggle(segId)}
                        className="p-0.5 rounded hover:bg-destructive/20 text-muted-foreground/50 hover:text-destructive">
                        <X className="w-3 h-3" />
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          {/* 右栏：片段列表 + AI 候选 */}
          <div className="flex-1 flex flex-col min-w-0">
            <div className="px-4 py-2.5 border-b border-border/50 shrink-0 flex items-center gap-2">
              <p className="text-[11px] font-medium text-muted-foreground uppercase tracking-wider flex-1">
                片段列表
              </p>
              {analyzed && candidates.length > 0 && (
                <span className="text-[10px] text-amber-400/70 flex items-center gap-1">
                  <Sparkles className="w-3 h-3" />
                  AI 推荐已标注
                </span>
              )}
            </div>
            <div className="flex-1 overflow-y-auto px-3 py-2 space-y-1.5">
              {clip.segments.map(seg => {
                const isSelected = selected.has(seg.id);
                const aiCandidate = candidates.find(c => c.segment_id === seg.id);
                const dur = seg.end - seg.start;
                return (
                  <div
                    key={seg.id}
                    onClick={() => toggle(seg.id)}
                    className={cn(
                      "flex items-start gap-2.5 px-3 py-2.5 rounded-lg border cursor-pointer transition-all",
                      isSelected
                        ? "border-amber-500/40 bg-amber-500/8 hover:bg-amber-500/12"
                        : "border-border/40 bg-secondary/20 hover:bg-secondary/40"
                    )}
                  >
                    <div className={cn(
                      "w-4 h-4 rounded border-2 flex items-center justify-center shrink-0 mt-0.5 transition-all",
                      isSelected ? "bg-amber-500 border-amber-500" : "border-muted-foreground/40"
                    )}>
                      {isSelected && <span className="text-black text-[9px] leading-none font-bold">
                        {ordered.indexOf(seg.id) + 1}
                      </span>}
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-0.5">
                        <span className="text-[10px] font-mono text-muted-foreground/40">{formatTimestamp(seg.start)}</span>
                        <span className="text-[10px] text-muted-foreground/30">{dur.toFixed(1)}s</span>
                        {aiCandidate && (
                          <span className="ml-auto text-[9px] px-1.5 py-0.5 rounded-full bg-amber-500/15 text-amber-400 border border-amber-500/25 font-medium shrink-0">
                            AI 推荐
                          </span>
                        )}
                      </div>
                      <p className="text-[12px] text-foreground/85 leading-snug">
                        {(seg.display_text || seg.text || "").trim()}
                      </p>
                      {aiCandidate?.reason && (
                        <p className="text-[10px] text-amber-400/60 mt-1 leading-snug">
                          {aiCandidate.reason}
                        </p>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>

        {/* 底部操作栏 */}
        <div className="shrink-0 border-t border-border px-5 py-3 flex items-center gap-3">
          <span className="text-[11px] text-muted-foreground/60 flex-1">
            已选 <span className="text-amber-400 font-mono font-medium">{ordered.length}</span> 个片段作为开场
          </span>
          {ordered.length > 0 && (
            <button
              onClick={() => { setSelected(new Set()); setOrdered([]); }}
              className="text-[11px] text-muted-foreground/50 hover:text-muted-foreground transition-colors"
            >
              清除
            </button>
          )}
          <Button
            onClick={handleSave}
            disabled={saving}
            className="h-8 px-4 bg-amber-500 hover:bg-amber-400 text-black text-[12px] font-medium gap-1.5"
          >
            {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <CheckCircle2 className="w-3.5 h-3.5" />}
            确认
          </Button>
        </div>
      </SheetContent>
    </Sheet>
  );
}

// ============================================================
// HighlightClipsPanel
// ============================================================
export function HighlightClipsPanel({
  task,
  isExporting,
  onPlayClip,
  onExport,
  onRefine,
  subtitleStyle,
  onOpenSubtitleStyle,
  hookEnabled,
  onHookEnabledChange,
  hookStyle,
  onOpenHookStyle,
  apiKey,
  provider: providerProp,
  claudeModel: claudeModelProp,
  ollamaModel: ollamaModelProp,
  ollamaBaseUrl,
}: {
  task: Task;
  isExporting: boolean;
  onPlayClip: (url: string) => void;
  onExport: (clipGroups: string[][], burnSubtitles: boolean, subtitleStyle?: SubtitleStyle, hookConfigs?: (HookConfig | null)[], hookOrders?: (string[] | null)[]) => void;
  onRefine?: (clip: HighlightClip, targetType: "monologue_clean" | "interview_compress") => void;
  subtitleStyle?: SubtitleStyle;
  onOpenSubtitleStyle?: () => void;
  hookEnabled?: boolean;
  onHookEnabledChange?: (v: boolean) => void;
  hookStyle?: Omit<HookConfig, "enabled" | "text">;
  onOpenHookStyle?: () => void;
  apiKey?: string;
  provider?: string;
  claudeModel?: string;
  ollamaModel?: string;
  ollamaBaseUrl?: string;
}) {
  const clips = useMemo(() => groupSegmentsIntoHighlightClips(task.audit_segments || []), [task.audit_segments]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [previewLoading, setPreviewLoading] = useState<string | null>(null);
  const [openRefineId, setOpenRefineId] = useState<string | null>(null);
  const [expandedTranscriptId, setExpandedTranscriptId] = useState<string | null>(null);
  const [burnSubtitles, setBurnSubtitles] = useState(true);

  // per-clip hook state：{clip首段segment_id → ordered hook segment_ids}
  const [clipHookOrders, setClipHookOrders] = useState<Record<string, string[]>>(() => {
    return task.clip_hook_orders || {};
  });
  const [hookDialogClip, setHookDialogClip] = useState<HighlightClip | null>(null);

  useEffect(() => {
    setSelected(new Set(clips.map((clip) => clip.id)));
  }, [clips]);

  // 当 task 外部更新时同步 hook orders
  useEffect(() => {
    setClipHookOrders(task.clip_hook_orders || {});
  }, [task.clip_hook_orders]);

  const allSelected = clips.length > 0 && clips.every((clip) => selected.has(clip.id));
  const selectedCount = clips.filter((clip) => selected.has(clip.id)).length;
  const selectedDuration = clips
    .filter((clip) => selected.has(clip.id))
    .reduce((sum, clip) => sum + clip.duration, 0);

  const toggleAll = () => {
    if (allSelected) { setSelected(new Set()); return; }
    setSelected(new Set(clips.map((clip) => clip.id)));
  };

  const toggleClip = (clipId: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(clipId)) next.delete(clipId);
      else next.add(clipId);
      return next;
    });
  };

  const clipKey = (clip: HighlightClip) => clip.segments[0]?.id ?? clip.id;

  const handleExport = () => {
    const selectedClips = clips.filter((clip) => selected.has(clip.id));
    // clip_groups: 全量 segment IDs（原始顺序），后端用来建窗口
    const clipGroups = selectedClips.map((clip) => clip.segments.map(s => s.id));
    // hook_orders: 每个 clip 的金句顺序（null = 无金句开场）
    const hookOrders: (string[] | null)[] = selectedClips.map((clip) => {
      const segs = clipHookOrders[clipKey(clip)] || [];
      return segs.length > 0 ? segs : null;
    });
    const hookConfigs: (HookConfig | null)[] = hookEnabled && hookStyle
      ? selectedClips.map((clip) => ({ enabled: true, text: clip.title, ...hookStyle }))
      : selectedClips.map(() => null);
    onExport(clipGroups, burnSubtitles, subtitleStyle, hookConfigs, hookOrders);
  };

  const handleHookSaved = (key: string, order: string[]) => {
    setClipHookOrders(prev => {
      const next = { ...prev };
      if (order.length > 0) next[key] = order;
      else delete next[key];
      return next;
    });
  };

  const hookDialogClipKey = hookDialogClip ? clipKey(hookDialogClip) : "";

  return (
    <div className="bg-card border border-border rounded-xl overflow-hidden flex flex-col h-full">
      <div className="h-9 shrink-0 border-b border-border flex items-center px-3 gap-2">
        <Sparkles className="w-3.5 h-3.5 text-orange-400" />
        <span className="text-xs font-medium text-muted-foreground uppercase tracking-wider">精彩片段</span>
        <span className="text-[11px] text-muted-foreground/50 font-mono">{clips.length} 个</span>
        <button
          onClick={toggleAll}
          className="ml-auto text-[11px] text-muted-foreground hover:text-foreground transition-colors"
        >
          {allSelected ? "取消全选" : "全选"}
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-3 space-y-2">
        {clips.length === 0 ? (
          <div className="flex flex-col items-center justify-center gap-3 text-center py-12">
            <Sparkles className="w-8 h-8 text-muted-foreground/20" />
            <p className="text-xs text-muted-foreground">暂无精彩片段，请先运行 AI 审计</p>
          </div>
        ) : (
          clips.map((clip) => {
            const isSelected = selected.has(clip.id);
            const key = clipKey(clip);
            const hookOrder = clipHookOrders[key] || [];
            const hasHook = hookOrder.length > 0;
            return (
              <div
                key={clip.id}
                className={cn(
                  "group flex items-start gap-2.5 p-3 rounded-lg border transition-all cursor-pointer",
                  isSelected
                    ? "bg-orange-500/5 border-orange-500/20 hover:bg-orange-500/10"
                    : "bg-secondary/20 border-border/40 opacity-60 hover:opacity-90"
                )}
                onClick={() => toggleClip(clip.id)}
              >
                <div className={cn(
                  "w-4 h-4 rounded border-2 flex items-center justify-center shrink-0 mt-0.5 transition-all",
                  isSelected ? "bg-orange-500 border-orange-500" : "border-muted-foreground/40"
                )}>
                  {isSelected && <span className="text-white text-[10px] leading-none">✓</span>}
                </div>

                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="text-[11px] font-mono font-bold text-orange-400/70">
                      #{String(clip.index).padStart(2, "0")}
                    </span>
                    {clip.score > 0 && (
                      <span className={cn(
                        "text-[10px] font-mono font-bold px-1 py-0.5 rounded leading-none",
                        clip.score >= 9 ? "bg-orange-500/20 text-orange-400" :
                        clip.score >= 7 ? "bg-yellow-500/15 text-yellow-400" :
                        "bg-muted/40 text-muted-foreground/60"
                      )}>{clip.score.toFixed(1)}</span>
                    )}
                    <span className="timestamp text-[11px] ml-auto text-muted-foreground/60">
                      {formatDuration(clip.duration)}
                    </span>
                  </div>
                  <p className="text-xs text-foreground/85 leading-snug">{clip.title}</p>
                  {clip.reason && (
                    <p className="text-[10px] text-muted-foreground/50 leading-snug mt-1 line-clamp-2">
                      <span className="text-muted-foreground/35">理由：</span>{clip.reason}
                    </p>
                  )}
                  <div className="flex items-center gap-2 mt-1.5 flex-wrap">
                    <span className="text-[10px] text-muted-foreground/40 font-mono">
                      {formatTimestamp(clip.startTime)}
                    </span>
                    <span className="text-[10px] text-muted-foreground/30">·</span>
                    <span className="text-[10px] text-muted-foreground/40">{clip.segments.length} 段</span>
                    {clip.stutterCount > 0 && (
                      <>
                        <span className="text-[10px] text-muted-foreground/30">·</span>
                        <span className="text-[10px] text-amber-500/60">结巴 {clip.stutterCount}</span>
                      </>
                    )}
                    {clip.silenceDuration > 0 && (
                      <>
                        <span className="text-[10px] text-muted-foreground/30">·</span>
                        <span className="text-[10px] text-sky-500/50">停顿 {clip.silenceDuration}s</span>
                      </>
                    )}
                    <button
                      className="ml-auto flex items-center gap-0.5 text-[10px] text-muted-foreground/40 hover:text-muted-foreground transition-colors"
                      onClick={(e) => {
                        e.stopPropagation();
                        setExpandedTranscriptId(expandedTranscriptId === clip.id ? null : clip.id);
                      }}
                    >
                      <ChevronDown className={cn("w-3 h-3 transition-transform", expandedTranscriptId === clip.id && "rotate-180")} />
                      文稿
                    </button>
                  </div>

                  {expandedTranscriptId === clip.id && (
                    <div
                      className="mt-2 pt-2 border-t border-border/30 space-y-1 max-h-48 overflow-y-auto"
                      onClick={(e) => e.stopPropagation()}
                    >
                      {clip.segments.map((seg) => (
                        <div key={seg.id} className="flex gap-2 items-baseline">
                          <span className="text-[9px] font-mono text-muted-foreground/30 shrink-0 w-10 text-right">
                            {formatTimestamp(seg.start)}
                          </span>
                          <p className="text-[11px] text-foreground/70 leading-snug">{seg.text}</p>
                        </div>
                      ))}
                    </div>
                  )}
                </div>

                <div className="shrink-0 flex flex-col items-center gap-1.5" onClick={(e) => e.stopPropagation()}>
                  {/* 预览 */}
                  <button
                    disabled={previewLoading === clip.id || isExporting}
                    className="w-7 h-7 rounded-md bg-orange-500/10 flex items-center justify-center text-orange-400 hover:bg-orange-500/25 transition-colors opacity-0 group-hover:opacity-100 disabled:opacity-60 disabled:cursor-wait"
                    onClick={async (e) => {
                      e.stopPropagation();
                      setPreviewLoading(clip.id);
                      try {
                        const url = await exportClipPreview(task.id, clip.segments.map((s) => s.id));
                        onPlayClip(url);
                      } finally {
                        setPreviewLoading(null);
                      }
                    }}
                    title="预览此片段"
                  >
                    {previewLoading === clip.id ? <Loader2 className="w-3 h-3 animate-spin" /> : <Play className="w-3 h-3 ml-0.5" />}
                  </button>

                  {/* per-clip Hook 开场按钮 */}
                  <button
                    className={cn(
                      "w-7 h-7 rounded-md flex items-center justify-center transition-colors",
                      hasHook
                        ? "bg-amber-500/20 text-amber-400 hover:bg-amber-500/35 opacity-100"
                        : "bg-secondary/60 text-muted-foreground/50 hover:text-amber-400 hover:bg-amber-500/15 opacity-0 group-hover:opacity-100"
                    )}
                    title={hasHook ? `Hook 开场：已设置 ${hookOrder.length} 个` : "设置 Hook 开场"}
                    onClick={(e) => {
                      e.stopPropagation();
                      setHookDialogClip(clip);
                    }}
                  >
                    <Crown className="w-3 h-3" />
                  </button>

                  {/* 精修 */}
                  {onRefine && (
                    <Popover open={openRefineId === clip.id} onOpenChange={(open) => setOpenRefineId(open ? clip.id : null)}>
                      <PopoverTrigger asChild>
                        <button
                          className="w-7 h-7 rounded-md bg-secondary/60 flex items-center justify-center text-muted-foreground/50 hover:text-foreground hover:bg-secondary transition-colors opacity-0 group-hover:opacity-100"
                          title="继续精修这个片段"
                        >
                          <Pencil className="w-3 h-3" />
                        </button>
                      </PopoverTrigger>
                      <PopoverContent align="end" className="w-64 p-1.5 bg-card border-border">
                        <p className="text-[10px] text-muted-foreground/50 px-2 py-1 mb-0.5">选择精修方式</p>
                        <button
                          className="w-full flex items-start gap-2.5 p-2 rounded-md hover:bg-secondary/60 transition-colors text-left"
                          onClick={() => { setOpenRefineId(null); onRefine(clip, "monologue_clean"); }}
                        >
                          <Mic className="w-3.5 h-3.5 text-sky-400 mt-0.5 shrink-0" />
                          <div>
                            <div className="text-xs font-medium text-foreground">口播精修</div>
                            <div className="text-[10px] text-muted-foreground/60 mt-0.5 leading-snug">适合单人对镜讲述。精修结巴、重说、语气词，让表达更流畅专业。</div>
                          </div>
                        </button>
                        <button
                          className="w-full flex items-start gap-2.5 p-2 rounded-md hover:bg-secondary/60 transition-colors text-left"
                          onClick={() => { setOpenRefineId(null); onRefine(clip, "interview_compress"); }}
                        >
                          <Users className="w-3.5 h-3.5 text-violet-400 mt-0.5 shrink-0" />
                          <div>
                            <div className="text-xs font-medium text-foreground">访谈精修</div>
                            <div className="text-[10px] text-muted-foreground/60 mt-0.5 leading-snug">适合主播 + 嘉宾对谈。保持问答结构完整，压缩冗余不破坏逻辑。</div>
                          </div>
                        </button>
                      </PopoverContent>
                    </Popover>
                  )}
                </div>
              </div>
            );
          })
        )}
      </div>

      {clips.length > 0 && (
        <div className="shrink-0 border-t border-border p-3 space-y-2">
          <div className="flex items-center justify-between text-[11px] text-muted-foreground/60">
            <span>已选 <span className="text-orange-400 font-mono font-medium">{selectedCount}</span> / {clips.length} 个</span>
            <span className="timestamp">{formatDuration(selectedDuration)}</span>
          </div>

          {/* 标题设置（原 Hook 字幕） */}
          <div className="flex items-center gap-2">
            <label className={cn(
              "flex flex-1 items-center gap-2 px-2.5 py-1.5 rounded-lg border cursor-pointer transition-colors select-none",
              hookEnabled ? "border-orange-500/30 bg-orange-500/5" : "border-border/40 bg-secondary/20"
            )}>
              <input
                type="checkbox"
                checked={hookEnabled ?? true}
                onChange={(e) => onHookEnabledChange?.(e.target.checked)}
                disabled={isExporting}
                className="w-3 h-3 accent-orange-500"
              />
              <span className="text-[11px] text-muted-foreground font-medium flex-1">标题设置</span>
              <span className="text-[10px] text-muted-foreground/40">
                {hookEnabled ? `标题烧入·${hookStyle?.duration ?? 3}s` : "不添加"}
              </span>
            </label>
            {onOpenHookStyle && (
              <button
                onClick={onOpenHookStyle}
                disabled={isExporting}
                className="shrink-0 h-8 px-2.5 rounded-lg border border-border/40 bg-secondary/20 hover:bg-secondary/60 transition-colors flex items-center gap-1.5 text-[11px] text-muted-foreground hover:text-foreground disabled:opacity-40 disabled:cursor-not-allowed"
                title="标题样式"
              >
                <SlidersHorizontal className="w-3 h-3" />
                样式
              </button>
            )}
          </div>

          {/* 硬字幕烧录行 */}
          <div className="flex items-center gap-2">
            <label className={cn(
              "flex flex-1 items-center gap-2 px-2.5 py-1.5 rounded-lg border cursor-pointer transition-colors select-none",
              burnSubtitles ? "border-orange-500/30 bg-orange-500/5" : "border-border/40 bg-secondary/20"
            )}>
              <input
                type="checkbox"
                checked={burnSubtitles}
                onChange={(e) => setBurnSubtitles(e.target.checked)}
                disabled={isExporting}
                className="w-3 h-3 accent-orange-500"
              />
              <span className="text-[11px] text-muted-foreground font-medium flex-1">硬字幕烧录</span>
              <span className="text-[10px] text-muted-foreground/40">
                {burnSubtitles ? "烧入画面" : "仅导出视频"}
              </span>
            </label>
            {onOpenSubtitleStyle && (
              <button
                onClick={onOpenSubtitleStyle}
                disabled={isExporting}
                className="shrink-0 h-8 px-2.5 rounded-lg border border-border/40 bg-secondary/20 hover:bg-secondary/60 transition-colors flex items-center gap-1.5 text-[11px] text-muted-foreground hover:text-foreground disabled:opacity-40 disabled:cursor-not-allowed"
                title="字幕样式"
              >
                <SlidersHorizontal className="w-3 h-3" />
                样式
              </button>
            )}
          </div>

          <Button
            className="w-full bg-orange-500 hover:bg-orange-500/85 text-white text-xs gap-1.5 h-8"
            size="sm"
            onClick={handleExport}
            disabled={selectedCount === 0 || isExporting}
          >
            <Download className="w-3.5 h-3.5" />
            导出 {selectedCount} 个独立片段
          </Button>
        </div>
      )}

      {/* per-clip Hook 设置弹窗 */}
      <ClipHookDialog
        open={hookDialogClip !== null}
        clip={hookDialogClip}
        taskId={task.id}
        initialOrder={hookDialogClip ? (clipHookOrders[clipKey(hookDialogClip)] || []) : []}
        apiKey={apiKey}
        providerProp={providerProp}
        claudeModelProp={claudeModelProp}
        ollamaModel={ollamaModelProp}
        ollamaBaseUrl={ollamaBaseUrl}
        onSaved={handleHookSaved}
        onClose={() => setHookDialogClip(null)}
      />
    </div>
  );
}
