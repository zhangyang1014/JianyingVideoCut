import { useState, useEffect, useRef, useCallback } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  Check, Lightbulb, Loader2, Mic, RefreshCw, SlidersHorizontal, Users, X,
} from "lucide-react";
import {
  resegment, resmooth, formatDuration, SPEAKER_COLORS,
  type Task, type ResegmentInheritanceReport, type ResegmentStats,
} from "@/lib/api";
import { cn } from "@/lib/utils";

// ============================================================
// Resegment Panel — 动态调整字幕分段
// ============================================================

const DEFAULT_SEGMENT_THRESHOLD = 0.30;
const RESEG_OPTIONS = [0.10, 0.20, 0.30, 0.40, 0.50];
const RESEG_LABELS: Record<number, string> = {
  0.10: "更细",
  0.20: "偏细",
  0.30: "标准",
  0.40: "完整",
  0.50: "更完整",
};

export function ResegmentPanel({
  task,
  onRefresh,
  onApplied,
}: {
  task: Task;
  onRefresh: () => void;
  onApplied?: () => void;
}) {
  const currentThreshold = task.params?.silence_threshold ?? DEFAULT_SEGMENT_THRESHOLD;
  const [selectedThreshold, setSelectedThreshold] = useState<number>(currentThreshold);
  const [previewStats, setPreviewStats] = useState<ResegmentStats | null>(null);
  const [previewInheritance, setPreviewInheritance] = useState<ResegmentInheritanceReport | null>(null);
  const [currentStats, setCurrentStats] = useState<ResegmentStats | null>(null);
  const [recommendation, setRecommendation] = useState<{ threshold: number; reason: string; stats: ResegmentStats } | null>(null);
  const [inheritResults, setInheritResults] = useState(true);
  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const previewAbortRef = useRef<AbortController | null>(null);
  const thresholdOptions = Array.from(new Set([
    ...RESEG_OPTIONS,
    currentThreshold,
    ...(recommendation ? [recommendation.threshold] : []),
  ])).sort((a, b) => a - b);

  const isPreviewing = selectedThreshold !== currentThreshold && previewStats !== null;

  // 首次加载：获取当前阈值的统计 + 推荐值
  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();
    (async () => {
      try {
        const result = await resegment(task.id, currentThreshold, false, {
          inheritMode: "none",
          signal: controller.signal,
        });
        if (cancelled) return;
        setCurrentStats(result.stats);
        setPreviewStats(null);
        setPreviewInheritance(null);
        if (result.recommendation) setRecommendation(result.recommendation);
      } catch { /* 静默 */ }
    })();
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [task.id, currentThreshold]);

  useEffect(() => () => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    previewAbortRef.current?.abort();
  }, []);

  // 阈值变化时 debounce 预览
  const handleThresholdChange = useCallback((val: number) => {
    setSelectedThreshold(val);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    previewAbortRef.current?.abort();

    if (val === currentThreshold) {
      setPreviewStats(null);
      setPreviewInheritance(null);
      return;
    }

    setIsLoading(true);
    debounceRef.current = setTimeout(async () => {
      const controller = new AbortController();
      previewAbortRef.current = controller;
      try {
        const result = await resegment(task.id, val, false, {
          // 拖动预览只算分段统计；O(n²) 的历史继承仅在最终保存时执行一次。
          inheritMode: "none",
          signal: controller.signal,
        });
        setPreviewStats(result.stats);
        setPreviewInheritance(null);
        if (result.recommendation) setRecommendation(result.recommendation);
      } catch (e: any) {
        if (e?.name === "AbortError") return;
        toast.error(e.message);
      } finally {
        if (previewAbortRef.current === controller) {
          previewAbortRef.current = null;
          setIsLoading(false);
        }
      }
    }, 300);
  }, [task.id, currentThreshold]);

  const handleApply = async () => {
    previewAbortRef.current?.abort();
    const hasDownstreamResults = Boolean(
      task.audit_history?.length
      || task.highlight_reels?.length
      || task.export_path
      || task.jianying_draft_path
      || task.audit_segments?.some((segment) =>
        segment.action !== "keep"
        || segment.claude_action != null
        || segment.user_override
      )
    );
    if (hasDownstreamResults) {
      const message = inheritResults
        ? "保存时将计算并尽量继承历史审计与集锦；有歧义的片段会标为待复核。旧导出仍保留在历史中，但不再视为当前导出。确认应用？"
        : "将应用新分段并清除当前 AI 审计、产品集锦和导出引用，需要重新生成。确认继续？";
      const ok = window.confirm(message);
      if (!ok) return;
    }
    setIsSaving(true);
    try {
      const result = await resegment(task.id, selectedThreshold, true, {
        inheritMode: inheritResults ? "aggressive" : "none",
        expectedContentRevision: task.content_revision,
      });
      toast.success(result.message);
      setPreviewStats(null);
      setPreviewInheritance(null);
      onRefresh();
      onApplied?.();
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setIsSaving(false);
    }
  };

  const handleCancel = () => {
    setSelectedThreshold(currentThreshold);
    setPreviewStats(null);
    setPreviewInheritance(null);
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
        <p className="text-[10px] text-muted-foreground uppercase tracking-wider">调整字幕分段</p>
        <span className="ml-auto text-[11px] font-mono text-primary font-semibold">
          {RESEG_LABELS[selectedThreshold] ?? `${selectedThreshold}s`}
        </span>
      </div>
      <p className="mb-2 text-[10px] leading-relaxed text-muted-foreground/60">
        选择字幕卡长短偏好。停顿超过该时长时会尝试切段，句末标点、说话人切换和 12 秒上限也会参与分段。
      </p>

      <div className="flex items-center gap-1 mb-2">
        {thresholdOptions.map((val) => {
          const isActive = selectedThreshold === val;
          const isOriginal = currentThreshold === val && isPreviewing;
          return (
            <button
              key={val}
              onClick={() => handleThresholdChange(val)}
              className={cn(
                "flex-1 rounded py-1.5 flex flex-col items-center transition-all duration-100",
                isActive
                  ? "bg-primary text-primary-foreground font-semibold"
                  : isOriginal
                    ? "border-2 border-dashed border-primary/40 text-primary/60 bg-background"
                    : "bg-background border border-border text-muted-foreground hover:border-primary/60 hover:text-foreground"
              )}
            >
              <span className="text-[10px] leading-tight">{RESEG_LABELS[val]}</span>
              <span className="text-[8px] font-mono leading-tight opacity-70">{val.toFixed(2)}s</span>
            </button>
          );
        })}
      </div>

      {/* 智能推荐条 */}
      {recommendation && recommendation.threshold !== currentThreshold && (
        <div className="flex items-center gap-1.5 rounded-md bg-primary/10 border border-primary/20 px-2 py-1.5 mb-2">
          <Lightbulb className="w-3 h-3 text-primary shrink-0" />
          <span className="text-[10px] text-primary/80 flex-1 leading-tight">
            推荐 <span className="font-semibold">{RESEG_LABELS[recommendation.threshold] ?? `${recommendation.threshold}s`}</span>
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

      <label className="mb-2 flex cursor-pointer items-start gap-2 rounded-md border border-primary/20 bg-primary/5 px-2 py-1.5">
        <input
          type="checkbox"
          checked={inheritResults}
          onChange={(event) => {
            const enabled = event.target.checked;
            setInheritResults(enabled);
          }}
          className="mt-0.5 accent-primary"
        />
        <span className="text-[10px] leading-relaxed text-muted-foreground">
          <span className="font-semibold text-foreground">尽量继承历史审计与集锦</span>
          <br />
          按词级时间戳迁移旧结果，不自动调用模型；有歧义的片段会提示复核。
        </span>
      </label>

      {/* 实时统计 */}
      {displayStats && (
        <div className={cn("grid grid-cols-4 gap-1 rounded-md p-2 mb-2", isPreviewing ? "bg-primary/5 border border-primary/15" : "bg-background/50")}>
          <StatDelta label="总段数" cur={displayStats.total} prev={isPreviewing ? currentStats?.total ?? null : null} />
          <StatDelta label="均字数" cur={displayStats.avg_chars} prev={isPreviewing ? currentStats?.avg_chars ?? null : null} unit="" reverse />
          <StatDelta label=">30字" cur={displayStats.over_limit_count} prev={isPreviewing ? currentStats?.over_limit_count ?? null : null} reverse />
          <StatDelta label="最长" cur={displayStats.max_chars} prev={isPreviewing ? currentStats?.max_chars ?? null : null} unit="字" reverse />
        </div>
      )}

      {isPreviewing && previewInheritance && (
        <div className="mb-2 rounded-md border border-amber-500/20 bg-amber-500/5 p-2">
          <div className="grid grid-cols-3 gap-1 text-center">
            <div>
              <p className="text-[9px] text-muted-foreground">继承审计</p>
              <p className="font-mono text-xs font-semibold text-green-400">
                {previewInheritance.inherited_audit_count}
              </p>
            </div>
            <div>
              <p className="text-[9px] text-muted-foreground">继承集锦</p>
              <p className="font-mono text-xs font-semibold text-green-400">
                {previewInheritance.inherited_reel_count}
              </p>
            </div>
            <div>
              <p className="text-[9px] text-muted-foreground">待复核</p>
              <p className="font-mono text-xs font-semibold text-amber-400">
                {previewInheritance.review_segment_count + previewInheritance.unmapped_segment_count}
              </p>
            </div>
          </div>
          {previewInheritance.invalidated_export_count > 0 && (
            <p className="mt-1 text-[9px] leading-relaxed text-muted-foreground">
              {previewInheritance.invalidated_export_count} 个当前导出引用会失效；历史导出作业和文件仍保留。
            </p>
          )}
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
            应用字幕分段
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
export function SpeakerPanel({ task, onRefresh }: { task: Task; onRefresh: () => void }) {
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
