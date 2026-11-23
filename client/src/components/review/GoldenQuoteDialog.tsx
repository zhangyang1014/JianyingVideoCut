import { useState, useEffect } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  Check, ChevronDown, ChevronUp, Crown, Loader2, Sparkles, X,
} from "lucide-react";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from "@/components/ui/sheet";
import {
  suggestGoldenQuotes, saveGoldenQuoteOrder, formatTimestamp, SPEAKER_COLORS,
  getEffectiveSubtitleText, DEFAULT_GOLDEN_QUOTE_MODEL,
  type Task, type Segment, type GoldenQuoteCandidate, type ModelRunPreview,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import { hexToRgba } from "./utils";
import { AiRunConfirmationDialog } from "./AiRunConfirmationDialog";

// ============================================================
// 金句开场对话框
// ============================================================
export function GoldenQuoteDialog({
  open,
  onClose,
  task,
  apiKey,
  provider,
  openaiModel,
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
  openaiModel?: string;
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
  const [confirmRerunOpen, setConfirmRerunOpen] = useState(false);

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

  const analyzeOptions = {
    provider: (provider as "openai" | "ollama") || "openai",
    openai_api_key: apiKey,
    openai_model: openaiModel || DEFAULT_GOLDEN_QUOTE_MODEL,
    use_openrouter: false,
    ollama_model: ollamaModel || "deepseek-r1:14b",
    ollama_base_url: ollamaBaseUrl || "http://localhost:11434",
  };

  const handleAnalyze = async (preview?: ModelRunPreview) => {
    setLoading(true);
    try {
      const results = await suggestGoldenQuotes(task.id, {
        ...analyzeOptions,
        preview_token: preview?.preview_token,
        preview_mode: preview?.mode,
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
          {getEffectiveSubtitleText(seg)}
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
    <>
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
                    const text = candidate?.text || (seg ? getEffectiveSubtitleText(seg) : "") || segId;
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
                onClick={() => {
                  if (analyzed) setConfirmRerunOpen(true);
                  else void handleAnalyze();
                }}
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
      <AiRunConfirmationDialog
        open={confirmRerunOpen}
        taskId={task.id}
        request={{
          operation: "golden_quote",
          mode: "rerun",
          options: analyzeOptions,
        }}
        onOpenChange={setConfirmRerunOpen}
        onConfirm={handleAnalyze}
        confirmLabel="确认重新分析"
      />
    </>
  );
}
