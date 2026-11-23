import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  ChevronDown, Crop, Crown, Download, FileJson, Loader2, SlidersHorizontal,
} from "lucide-react";
import {
  canResumeExecution,
  getExecutionCheckpointCount,
  getExecutionRetrySeconds,
  getExecutionTotalAttempts,
  getTaskExecutionRun,
  isExecutionFailureFinal,
  type Task,
  type SubtitleStyle,
  type GoldenQuoteCandidate,
  type RenderProfile,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import { ClipRangeBar } from "./ClipRangeBar";
import {
  SubtitleConfigDialog, SubtitleStyleQuickControls, DEFAULT_SUBTITLE_STYLE, SUBTITLE_STYLE_LS_KEY,
  BURN_SUBTITLES_LS_KEY, getOrientationDefaults,
} from "./SubtitleConfig";
import { GoldenQuoteDialog } from "./GoldenQuoteDialog";

// ============================================================
// Export Panel
// ============================================================
export function ExportPanel({
  task,
  isExporting,
  jianyingDir,
  onJianyingDirChange,
  onExportFFmpeg,
  onExportJianying,
  onCancelExport,
  apiKey,
  provider,
  openaiModel,
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
  isExporting: boolean;
  jianyingDir: string;
  onJianyingDirChange?: (dir: string) => void | Promise<void>;
  onExportFFmpeg: (
    renderProfile: RenderProfile,
    burnSubtitles: boolean,
    subtitleStyle?: SubtitleStyle,
  ) => void;
  onExportJianying: () => void;
  onCancelExport: () => void;
  apiKey?: string;
  provider?: string;
  openaiModel?: string;
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
  const [burnSubtitles, setBurnSubtitles] = useState(() => {
    try {
      return localStorage.getItem(BURN_SUBTITLES_LS_KEY) !== "false";
    } catch {
      return true;
    }
  });
  const [showJianyingDir, setShowJianyingDir] = useState(false);
  const [jianyingDirDraft, setJianyingDirDraft] = useState(jianyingDir);
  const [savingJianyingDir, setSavingJianyingDir] = useState(false);
  const [nowMs, setNowMs] = useState(Date.now());

  useEffect(() => {
    setJianyingDirDraft(jianyingDir);
  }, [jianyingDir]);

  const saveJianyingDir = async () => {
    if (!onJianyingDirChange || jianyingDirDraft === jianyingDir) return;
    setSavingJianyingDir(true);
    try {
      await onJianyingDirChange(jianyingDirDraft);
    } finally {
      setSavingJianyingDir(false);
    }
  };

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
  const setBurnSubtitlesPersisted = (enabled: boolean) => {
    setBurnSubtitles(enabled);
    try {
      localStorage.setItem(BURN_SUBTITLES_LS_KEY, String(enabled));
    } catch {
      // 存储失败（如隐私模式），本次页面状态仍然生效
    }
  };
  const [configOpen, setConfigOpen] = useState(false);
  const [quickStyleOpen, setQuickStyleOpen] = useState(false);
  const [frameDataUrl, setFrameDataUrl] = useState<string | null>(null);
  const [videoAspectRatio, setVideoAspectRatio] = useState(16 / 9);
  const [videoDuration, setVideoDuration] = useState(0);
  const [videoCurrentTime, setVideoCurrentTime] = useState(0);
  const goldenQuoteCount = (task.golden_quote_order || []).length;
  const latestRenderJob = [...(task.export_jobs || [])]
    .reverse()
    .find((job) => job.kind === "ffmpeg" && job.quality_report);
  const latestFailedExportJob = [...(task.export_jobs || [])]
    .reverse()
    .find((job) => job.status === "failed");
  const executionRun = getTaskExecutionRun(task);
  const isExportExecution = Boolean(
    executionRun && (
      executionRun.operation.includes("export")
      || executionRun.current_stage?.includes("export")
    )
  );
  const showExecutionFailure = isExportExecution && isExecutionFailureFinal(executionRun);
  const exportInputChanged = Boolean(latestFailedExportJob && (
    latestFailedExportJob.source_content_revision !== task.content_revision
    || latestFailedExportJob.source_highlight_revision !== task.highlight_revision
    || (
      latestFailedExportJob.source_subtitle_revision != null
      && latestFailedExportJob.source_subtitle_revision !== (task.subtitle_revision ?? 0)
    )
  ));
  const canContinueExport = canResumeExecution(executionRun) && !exportInputChanged && Boolean(latestFailedExportJob);
  const retrySeconds = getExecutionRetrySeconds(executionRun, nowMs);

  useEffect(() => {
    if (!isExportExecution || executionRun?.status !== "retry_wait") return;
    const timer = setInterval(() => setNowMs(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [
    executionRun?.run_id,
    executionRun?.status,
    executionRun?.retry_state.next_retry_at,
    isExportExecution,
  ]);

  const rerunLatestExport = () => {
    if (!latestFailedExportJob) return;
    if (latestFailedExportJob.kind === "jianying") {
      onExportJianying();
      return;
    }
    if (latestFailedExportJob.kind !== "ffmpeg") return;
    const requestBurnSubtitles = latestFailedExportJob.request.burn_subtitles;
    const shouldBurnSubtitles = typeof requestBurnSubtitles === "boolean"
      ? requestBurnSubtitles
      : burnSubtitles;
    onExportFFmpeg(
      latestFailedExportJob.render_profile,
      shouldBurnSubtitles,
      shouldBurnSubtitles ? subtitleStyle : undefined,
    );
  };

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
    const frameUrl = `/api/tasks/${task.id}/frame?t=${t.toFixed(3)}`;
    setFrameDataUrl(frameUrl);

    setConfigOpen(true);
  };

  const clipIsActive = (task.clip_start != null && task.clip_start > 0) ||
    (task.clip_end != null && task.video_duration != null && task.clip_end < task.video_duration);

  return (
    <div className="bg-card border border-border rounded-xl p-4 space-y-2.5">
      <h3 className="text-xs font-medium text-muted-foreground uppercase tracking-wider">导出选项</h3>

      {isExportExecution && executionRun && (
        <div className={cn(
          "rounded-lg border px-3 py-2 text-[10px] font-mono leading-relaxed",
          executionRun.status === "retry_wait"
            ? "border-amber-500/30 bg-amber-500/10 text-amber-300"
            : showExecutionFailure
              ? executionRun.recoverable
                ? "border-amber-500/30 bg-amber-500/5 text-amber-300"
                : "border-red-500/30 bg-red-500/5 text-red-400"
              : "border-border bg-secondary/30 text-muted-foreground",
        )}>
          {executionRun.status === "retry_wait" && (
            <p>
              自动重试第 {executionRun.retry_state.retries_used} 次（总{getExecutionTotalAttempts(executionRun)}次）
              {retrySeconds !== null ? ` · ${retrySeconds}秒后重试` : ""}
            </p>
          )}
          <p>
            当前阶段：{executionRun.current_stage || "导出"} · 复用检查点：{getExecutionCheckpointCount(executionRun)}
            {showExecutionFailure ? ` · ${executionRun.recoverable ? "失败可恢复" : "失败不可恢复"}` : ""}
          </p>
          {showExecutionFailure && (
            <div className="mt-2 flex gap-2">
              <Button
                size="sm"
                variant="outline"
                className="h-7 flex-1 text-[10px]"
                onClick={rerunLatestExport}
                disabled={!canContinueExport || isExporting}
                title={exportInputChanged ? "字幕或剪辑输入已变化，不能继续旧执行" : !executionRun.recoverable ? "该失败不可恢复" : undefined}
              >
                从检查点继续
              </Button>
              <Button
                size="sm"
                className="h-7 flex-1 text-[10px]"
                onClick={rerunLatestExport}
                disabled={!latestFailedExportJob || latestFailedExportJob.kind === "highlight_reel" || isExporting}
              >
                完全重新执行
              </Button>
            </div>
          )}
          {showExecutionFailure && exportInputChanged && (
            <p className="mt-1 text-amber-400">输入版本已变化，已禁用继续；请完全重新执行。</p>
          )}
          {showExecutionFailure && (
            <p className="mt-1 text-muted-foreground">
              当前无通用 resume/reset 接口；以上操作均通过现有导出 trigger 创建作业，继续会由内容指纹复用缓存。
            </p>
          )}
        </div>
      )}

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
        openaiModel={openaiModel}
        ollamaModel={ollamaModel}
        ollamaBaseUrl={ollamaBaseUrl}
        onSaved={onGoldenQuoteSaved}
        onSeek={onSeek}
        candidates={goldenCandidates}
        setCandidates={setGoldenCandidates}
        analyzed={goldenAnalyzed}
        setAnalyzed={setGoldenAnalyzed}
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
              onChange={(e) => setBurnSubtitlesPersisted(e.target.checked)}
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
        {burnSubtitles && (
          <div className="mt-2 border-t border-border/40 pt-2">
            <button
              type="button"
              aria-expanded={quickStyleOpen}
              onClick={() => setQuickStyleOpen((open) => !open)}
              disabled={isExporting}
              className="flex w-full items-center gap-1.5 text-left text-[10px] text-muted-foreground transition-colors hover:text-foreground disabled:opacity-50"
            >
              <ChevronDown className={cn("h-3 w-3 shrink-0 transition-transform", quickStyleOpen && "rotate-180")} />
              <span className="font-medium">快捷样式</span>
              <span className="min-w-0 flex-1 truncate text-muted-foreground/60">
                {subtitleStyle.font_name} · 描边 {subtitleStyle.outline}
              </span>
              <span
                aria-label={`文字颜色 ${subtitleStyle.primary_color}`}
                className="h-3 w-3 shrink-0 rounded-full border border-border"
                style={{ backgroundColor: subtitleStyle.primary_color }}
              />
              <span
                aria-label={`描边颜色 ${subtitleStyle.outline_color}`}
                className="h-3 w-3 shrink-0 rounded-full border border-border"
                style={{ backgroundColor: subtitleStyle.outline_color }}
              />
            </button>
            {quickStyleOpen && (
              <div className="mt-2">
                <SubtitleStyleQuickControls
                  style={subtitleStyle}
                  onChange={setSubtitleStylePersisted}
                  disabled={isExporting}
                />
              </div>
            )}
          </div>
        )}
      </div>

      {/* ── 分隔线 + 导出操作 ── */}
      <div className="border-t border-border/40 pt-2.5 space-y-2">
        {isExporting ? (
          <Button
            size="sm"
            className="w-full bg-destructive text-destructive-foreground hover:bg-destructive/90 text-xs gap-1.5"
            onClick={onCancelExport}
          >
            <Loader2 className="w-3 h-3 animate-spin" />
            终止导出
          </Button>
        ) : (
          <>
            <div className="grid grid-cols-2 gap-2">
              <Button
                size="sm"
                variant="outline"
                className="border-border text-foreground hover:bg-secondary text-xs"
                onClick={() => onExportFFmpeg("draft", false)}
              >
                快速草稿
              </Button>
              <Button
                size="sm"
                variant="outline"
                className="border-primary/40 text-primary hover:bg-primary/10 text-xs"
                onClick={() => onExportFFmpeg(
                  "preview",
                  burnSubtitles,
                  burnSubtitles ? subtitleStyle : undefined,
                )}
              >
                成片预览
              </Button>
              <Button
                size="sm"
                className="col-span-2 bg-primary text-primary-foreground hover:bg-primary/90 text-xs gap-1.5"
                onClick={() => onExportFFmpeg(
                  "final",
                  burnSubtitles,
                  burnSubtitles ? subtitleStyle : undefined,
                )}
              >
                <Download className="w-3 h-3" />
                最终 FFmpeg 导出
              </Button>
            </div>
            <p className="text-[10px] text-muted-foreground/60 leading-relaxed">
              草稿仅检查剪辑结构；成片预览不会覆盖最终导出文件。
            </p>
            <Button
              size="sm"
              variant="outline"
              className="w-full border-border text-foreground hover:bg-secondary text-xs gap-1.5"
              onClick={() => setShowJianyingDir(prev => !prev)}
            >
              <FileJson className="w-3 h-3" />
              剪映草稿
              <ChevronDown className={cn("w-3 h-3 transition-transform", showJianyingDir && "rotate-180")} />
            </Button>
          </>
        )}

        {showJianyingDir && (
          <div className="space-y-1.5 animate-in fade-in slide-in-from-top-1 duration-150">
            <Input
              value={jianyingDirDraft}
              onChange={(e) => setJianyingDirDraft(e.target.value)}
              onBlur={saveJianyingDir}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.currentTarget.blur();
                }
              }}
              placeholder="/Users/xxx/Movies/JianyingPro/User Data/Projects"
              className="h-8 bg-secondary border-border text-[11px]"
              disabled={savingJianyingDir || isExporting}
            />
            <p className="text-[10px] text-muted-foreground/50">
              {jianyingDirDraft
                ? savingJianyingDir ? "正在保存目录..." : "失焦或回车后保存；导出剪映草稿时使用此目录。"
                : "留空则导出到视频同级目录。"}
            </p>
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
        {latestRenderJob?.quality_report && (
          <p className={cn(
            "text-[10px] leading-relaxed",
            latestRenderJob.quality_report.passed ? "text-green-400" : "text-destructive",
          )}>
            {latestRenderJob.quality_report.passed
              ? `✓ ${latestRenderJob.render_profile === "preview" ? "成片预览" : "导出"}媒体校验通过`
              : `媒体校验未通过：${latestRenderJob.quality_report.issues[0]?.message || "请检查导出控制台"}`}
          </p>
        )}
      </div>
    </div>
  );
}
