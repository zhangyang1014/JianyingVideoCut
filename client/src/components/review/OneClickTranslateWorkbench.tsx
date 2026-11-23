import { useState, useEffect } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  AlertCircle, ArrowLeft, Check, Coins, Download, Loader2, RefreshCw,
} from "lucide-react";
import {
  updateTranslationCues, exportOneClickTranslate, retryTranslationVideo, cancelTask,
  getTranslationPreviewUrl, formatTimestamp,
  STATUS_LABELS, STATUS_COLORS,
  canResumeExecution, getExecutionCheckpointCount, getExecutionRetrySeconds,
  getExecutionTotalAttempts, getTaskExecutionRun, isExecutionFailureFinal,
  type Task, type TaskStatus, type TranslationCue, type ModelRunPreview,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import {
  DEFAULT_TTS_MODEL,
  DEFAULT_TTS_VOICE,
  useAppConfig,
  resolveFeatureLlm,
} from "@/hooks/useAppConfig";
import { ConsoleLog, type ConsoleEntry } from "@/components/review/ConsoleLog";
import { FundsUsagePanel } from "@/components/review/FundsUsagePanel";
import { ReviewToolDock } from "@/components/review/ReviewToolDock";
import { AiRunConfirmationDialog } from "@/components/review/AiRunConfirmationDialog";

// ============================================================
// One-click Translate Workbench
// ============================================================
export function OneClickTranslateWorkbench({
  task,
  taskId,
  onBack,
  onRefresh,
  appConfig,
  consoleEntries,
  consoleExpanded,
  onConsoleToggle,
  onConsoleClear,
  onLog,
}: {
  task: Task;
  taskId: string;
  onBack: () => void;
  onRefresh: () => Promise<void>;
  appConfig: ReturnType<typeof useAppConfig>["config"];
  consoleEntries: ConsoleEntry[];
  consoleExpanded: boolean;
  onConsoleToggle: () => void;
  onConsoleClear: () => void;
  onLog?: (
    level: ConsoleEntry["level"],
    source: ConsoleEntry["source"],
    message: string,
    progress?: number,
  ) => void;
}) {
  const videos = task.translation_videos || [];
  const [selectedVideoId, setSelectedVideoId] = useState(videos[0]?.id ?? "");
  const selectedVideo = videos.find((video) => video.id === selectedVideoId) || videos[0];
  const [draftCues, setDraftCues] = useState<TranslationCue[]>([]);
  const [saving, setSaving] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [retryingVideoId, setRetryingVideoId] = useState<string | null>(null);
  const [nowMs, setNowMs] = useState(Date.now());
  const [savedInputChanged, setSavedInputChanged] = useState(false);
  const [pendingRetry, setPendingRetry] = useState<{ videoId: string; forceRestart: boolean } | null>(null);
  const runningStatuses: TaskStatus[] = ["asr_running", "translate_running", "tts_running", "dubbing_running", "export_running"];
  const executionRun = getTaskExecutionRun(task);
  const isRunning = runningStatuses.includes(task.status)
    || executionRun?.status === "running"
    || executionRun?.status === "retry_wait";
  const showRecovery = isExecutionFailureFinal(executionRun);
  const draftInputChanged = Boolean(selectedVideo) && JSON.stringify(draftCues) !== JSON.stringify(selectedVideo.cues || []);
  const inputChanged = draftInputChanged || savedInputChanged;
  const canContinue = canResumeExecution(executionRun) && !inputChanged;
  const retrySeconds = getExecutionRetrySeconds(executionRun, nowMs);
  const failedVideo = videos.find((video) => video.status === "error") || selectedVideo;

  useEffect(() => {
    if (!selectedVideoId && videos[0]?.id) {
      setSelectedVideoId(videos[0].id);
    }
  }, [selectedVideoId, videos]);

  useEffect(() => {
    setDraftCues((selectedVideo?.cues || []).map((cue) => ({ ...cue })));
  }, [selectedVideo?.id, selectedVideo?.cues]);

  useEffect(() => {
    if (executionRun?.status !== "retry_wait") return;
    const timer = setInterval(() => setNowMs(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [executionRun?.run_id, executionRun?.status, executionRun?.retry_state.next_retry_at]);

  useEffect(() => {
    setSavedInputChanged(false);
  }, [executionRun?.run_id]);

  const patchCue = (cueId: string, patch: Partial<TranslationCue>) => {
    setDraftCues((prev) => prev.map((cue) => cue.id === cueId ? { ...cue, ...patch } : cue));
  };

  const handleSave = async () => {
    if (!selectedVideo) return;
    setSaving(true);
    try {
      await updateTranslationCues(taskId, selectedVideo.id, draftCues);
      if (showRecovery) setSavedInputChanged(true);
      toast.success("字幕已保存，修改过的配音会在导出时重新生成");
      await onRefresh();
    } catch (e: any) {
      toast.error(e.message || "字幕保存失败");
    } finally {
      setSaving(false);
    }
  };

  const buildExportOptions = () => {
    const saved = task.one_click_translate_config;
    const translate = resolveFeatureLlm(appConfig, "translate");
    const tts = resolveFeatureLlm(appConfig, "tts");
    return {
      source_language: saved?.source_language || "zh",
      target_language: saved?.target_language || "en",
      subtitle_model: saved?.subtitle_model || {
        provider: translate.provider === "ollama" ? "ollama" : "openai",
        model: translate.model,
        base_url: translate.baseUrl,
      },
      tts_model: saved?.tts_model || {
        provider: tts.provider === "silent" ? "silent" : "openai",
        model: tts.model || DEFAULT_TTS_MODEL,
        voice: tts.voice || DEFAULT_TTS_VOICE,
        base_url: tts.baseUrl,
      },
      subtitle_api_key: translate.apiKey || undefined,
      tts_api_key: tts.apiKey || undefined,
      asr_backend: "funasr",
      asr_model: "paraformer-zh",
    };
  };

  const handleExport = async () => {
    setExporting(true);
    try {
      await exportOneClickTranslate(taskId, buildExportOptions());
      onLog?.(
        "info",
        "ffmpeg",
        `一键翻译最终导出已启动 | 视频: ${videos.length} 个 | 等待后端日志...`,
        0.01,
      );
      toast.success("最终导出已启动");
      await onRefresh();
    } catch (e: any) {
      toast.error(e.message || "导出失败");
    } finally {
      setExporting(false);
    }
  };

  const handleRetryVideo = async (
    videoId: string,
    forceRestart = false,
    preview?: ModelRunPreview,
  ) => {
    setRetryingVideoId(videoId);
    try {
      const result = await retryTranslationVideo(taskId, videoId, {
        ...buildExportOptions(),
        force_restart: forceRestart,
        preview_token: preview?.preview_token,
        preview_mode: preview?.mode,
      });
      const video = videos.find((item) => item.id === videoId);
      onLog?.(
        "info",
        "translate",
        `${forceRestart ? "完全重新执行" : "从检查点继续"}已启动 | 视频: ${video?.filename || videoId} | 断点: ${result.resume_stage || "auto"} | 等待后端日志...`,
        0.01,
      );
      toast.success(result.message || "视频重试已启动");
      await onRefresh();
    } catch (e: any) {
      toast.error(e.message || "重试失败");
    } finally {
      setRetryingVideoId(null);
    }
  };

  const requestRetryVideo = (videoId: string, forceRestart = false) => {
    setPendingRetry({ videoId, forceRestart });
  };

  const handleRecovery = async (forceRestart: boolean) => {
    if (!failedVideo) return;
    if (executionRun?.operation.includes("export")) {
      await handleExport();
      return;
    }
    requestRetryVideo(failedVideo.id, forceRestart);
  };

  const previewUrl = selectedVideo?.preview_path ? getTranslationPreviewUrl(taskId, selectedVideo.id) : null;

  return (
    <div className="h-full flex flex-col overflow-hidden">
      <div className="flex-1 min-h-0 flex flex-col overflow-hidden">
      <div className="h-11 shrink-0 border-b border-border flex items-center px-4 gap-3">
        <button
          onClick={onBack}
          className="flex items-center gap-1.5 text-muted-foreground hover:text-foreground transition-colors text-sm shrink-0"
        >
          <ArrowLeft className="w-4 h-4" />
          <span>返回</span>
        </button>
        <div className="w-px h-4 bg-border shrink-0" />
        <div className="min-w-0">
          <h2 className="text-sm font-medium text-foreground truncate">{task.name}</h2>
          <p className="text-[10px] text-muted-foreground">
            一键转英文 · {videos.length} 个视频 · {task.one_click_translate_config?.source_language || "zh"} → {task.one_click_translate_config?.target_language || "en"}
          </p>
        </div>
        <span className={cn(
          "status-badge text-[10px] shrink-0",
          executionRun?.status === "retry_wait" ? "text-amber-300 bg-amber-900/50" : STATUS_COLORS[task.status],
        )}>
          {isRunning && <Loader2 className="w-2.5 h-2.5 animate-spin" />}
          {executionRun?.status === "retry_wait" ? "自动重试中" : STATUS_LABELS[task.status]}
        </span>
        {executionRun && (
          <span className={cn(
            "max-w-[420px] truncate text-[10px] font-mono",
            executionRun.status === "retry_wait" ? "text-amber-300" : "text-muted-foreground",
          )}>
            {executionRun.status === "retry_wait"
              ? `自动重试第 ${executionRun.retry_state.retries_used} 次（总${getExecutionTotalAttempts(executionRun)}次）${retrySeconds !== null ? ` · ${retrySeconds}秒后` : ""}`
              : executionRun.current_stage ? `当前阶段：${executionRun.current_stage}` : ""}
            {` · 复用检查点 ${getExecutionCheckpointCount(executionRun)}`}
          </span>
        )}
        <div className="ml-auto flex items-center gap-2">
          {isRunning && (
            <Button
              size="sm"
              className="h-7 text-xs bg-destructive text-destructive-foreground hover:bg-destructive/90"
              onClick={async () => {
                try {
                  await cancelTask(taskId);
                  toast.success("任务已终止");
                  // 状态回滚由后端 WebSocket status_change 消息同步
                } catch (e: any) {
                  toast.error(e.message || "终止失败");
                }
              }}
            >
              <Loader2 className="w-3.5 h-3.5 animate-spin mr-1" />
              终止
            </Button>
          )}
          <Button
            size="sm"
            variant="outline"
            className="h-7 text-xs border-border text-muted-foreground hover:text-foreground"
            onClick={onRefresh}
            disabled={saving || exporting}
          >
            <RefreshCw className="w-3.5 h-3.5 mr-1" />
            刷新
          </Button>
          <Button
            size="sm"
            className="h-7 text-xs bg-primary text-primary-foreground hover:bg-primary/90"
            onClick={handleExport}
            disabled={isRunning || exporting || videos.length === 0}
          >
            {exporting ? <Loader2 className="w-3.5 h-3.5 animate-spin mr-1" /> : <Download className="w-3.5 h-3.5 mr-1" />}
            一键导出
          </Button>
        </div>
      </div>

      {showRecovery && (
        <div className="shrink-0 border-b border-amber-500/25 bg-amber-500/5 px-4 py-2">
          <div className="flex flex-wrap items-center gap-2">
            <div className="min-w-0 flex-1">
              <p className={cn("text-xs font-medium", executionRun?.recoverable ? "text-amber-300" : "text-red-400")}>
                {executionRun?.recoverable ? "执行失败，可从已有检查点恢复" : "执行失败，当前错误不可恢复"}
              </p>
              <p className="text-[10px] text-muted-foreground">
                阶段：{executionRun?.current_stage || "未知"} · 已复用 {getExecutionCheckpointCount(executionRun)} 个检查点
                {inputChanged ? " · 输入已变化，不能继续旧执行" : ""}
              </p>
            </div>
            <Button
              size="sm"
              variant="outline"
              className="h-7 text-xs"
              onClick={() => void handleRecovery(false)}
              disabled={!canContinue || retryingVideoId !== null || exporting}
              title={inputChanged ? "字幕输入已变化，请保存后完全重新执行" : !executionRun?.recoverable ? "该失败不可恢复" : undefined}
            >
              从检查点继续
            </Button>
            <Button
              size="sm"
              className="h-7 text-xs bg-primary text-primary-foreground"
              onClick={() => void handleRecovery(true)}
              disabled={retryingVideoId !== null || exporting}
            >
              完全重新执行
            </Button>
          </div>
        </div>
      )}

      <div className="flex-1 overflow-hidden flex">
        <div className="w-[38%] shrink-0 border-r border-border p-4 flex flex-col gap-3">
          <div className="flex flex-wrap gap-2">
            {videos.map((video, index) => (
              <button
                key={video.id}
                onClick={() => setSelectedVideoId(video.id)}
                className={cn(
                  "px-2.5 py-1 rounded border text-xs transition-colors max-w-[180px] truncate",
                  selectedVideo?.id === video.id
                    ? "border-primary/50 bg-primary/10 text-primary"
                    : "border-border text-muted-foreground hover:text-foreground hover:bg-secondary/60",
                  video.status === "error" && "border-destructive/50 text-destructive"
                )}
              >
                {video.status === "error" && "⚠ "}{index + 1}. {video.filename}
              </button>
            ))}
          </div>

          <div className="flex-1 min-h-0 bg-black rounded-lg overflow-hidden border border-border flex items-center justify-center">
            {previewUrl ? (
              <video
                key={previewUrl}
                src={previewUrl}
                controls
                className="w-full h-full object-contain"
              />
            ) : (
              <div className="text-center px-6">
                {isRunning ? (
                  <>
                    <Loader2 className="w-8 h-8 animate-spin text-primary mx-auto mb-3" />
                    <p className="text-sm text-foreground">正在生成预览</p>
                    <p className="text-xs text-muted-foreground mt-1">完成后会自动生成目标语言配音视频</p>
                  </>
                ) : (
                  <>
                    <AlertCircle className="w-8 h-8 text-muted-foreground/50 mx-auto mb-3" />
                    <p className="text-sm text-foreground">暂无预览视频</p>
                    <p className="text-xs text-muted-foreground mt-1">请等待流水线完成或刷新任务状态</p>
                  </>
                )}
              </div>
            )}
          </div>

          {selectedVideo && (
            <div className="rounded-lg border border-border bg-card p-3 text-xs space-y-1.5">
              <div className="flex justify-between">
                <span className="text-muted-foreground">视频状态</span>
                <span className="text-foreground font-mono">{selectedVideo.status}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted-foreground">字幕条数</span>
                <span className="text-foreground font-mono">{selectedVideo.cues?.length || 0}</span>
              </div>
              {selectedVideo.export_path && (
                <p className="text-[10px] text-green-400 break-all pt-1">最终文件：{selectedVideo.export_path}</p>
              )}
              {selectedVideo.status === "error" && (
                <div className="pt-1.5 border-t border-border space-y-1.5">
                  <p className="text-[11px] text-destructive break-all">
                    处理失败：{selectedVideo.error_message || "未知错误"}
                  </p>
                  <Button
                    size="sm"
                    variant="outline"
                    className="h-7 w-full text-xs border-destructive/50 text-destructive hover:bg-destructive/10"
                    onClick={() => requestRetryVideo(selectedVideo.id)}
                    disabled={isRunning || retryingVideoId !== null}
                  >
                    {retryingVideoId === selectedVideo.id
                      ? <Loader2 className="w-3.5 h-3.5 animate-spin mr-1" />
                      : <RefreshCw className="w-3.5 h-3.5 mr-1" />}
                    重试该视频
                  </Button>
                </div>
              )}
            </div>
          )}
        </div>

        <div className="flex-1 min-w-0 flex flex-col overflow-hidden">
        <div className="flex-1 min-h-0 flex flex-col">
          <div className="h-10 shrink-0 border-b border-border flex items-center px-4 gap-3">
            <span className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
              字幕校准 / 翻译
            </span>
            <span className="text-[11px] text-muted-foreground">
              保存后，改过翻译文本的 cue 会清空旧音频并在导出时重新生成
            </span>
            <Button
              size="sm"
              className="ml-auto h-7 text-xs bg-primary text-primary-foreground hover:bg-primary/90"
              onClick={handleSave}
              disabled={!selectedVideo || saving || isRunning}
            >
              {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin mr-1" /> : <Check className="w-3.5 h-3.5 mr-1" />}
              保存字幕
            </Button>
          </div>

          <div className="flex-1 overflow-y-auto p-4 space-y-3">
            {draftCues.length === 0 ? (
              <div className="h-full flex items-center justify-center text-sm text-muted-foreground">
                暂无字幕 cue
              </div>
            ) : (
              draftCues.map((cue, index) => (
                <div key={cue.id} className="rounded-lg border border-border bg-card p-3 space-y-2">
                  <div className="flex items-center gap-2 text-[11px]">
                    <span className="font-mono text-primary">#{index + 1}</span>
                    <span className="font-mono text-muted-foreground">
                      {formatTimestamp(cue.start)} → {formatTimestamp(cue.end)}
                    </span>
                    <span className={cn(
                      "ml-auto px-1.5 py-0.5 rounded text-[10px]",
                      cue.audio_path ? "bg-green-900/40 text-green-300" : "bg-amber-900/40 text-amber-300"
                    )}>
                      {cue.audio_path ? "音频已生成" : "待生成音频"}
                    </span>
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                    <div className="space-y-1">
                      <p className="text-[10px] text-muted-foreground uppercase tracking-wider">校准字幕</p>
                      <Textarea
                        value={cue.calibrated_text || cue.source_text}
                        onChange={(e) => patchCue(cue.id, { calibrated_text: e.target.value })}
                        className="min-h-20 text-xs bg-secondary border-border"
                        disabled={isRunning}
                      />
                    </div>
                    <div className="space-y-1">
                      <p className="text-[10px] text-muted-foreground uppercase tracking-wider">目标语言字幕</p>
                      <Textarea
                        value={cue.translated_text || ""}
                        onChange={(e) => patchCue(cue.id, { translated_text: e.target.value })}
                        className="min-h-20 text-xs bg-secondary border-border"
                        disabled={isRunning}
                      />
                    </div>
                  </div>
                  <p className="text-[11px] text-muted-foreground/70 line-clamp-2">
                    原文：{cue.source_text}
                  </p>
                </div>
              ))
            )}
          </div>
        </div>
        <ConsoleLog
          entries={consoleEntries}
          expanded={consoleExpanded}
          isRunning={isRunning}
          onToggle={onConsoleToggle}
          onClear={onConsoleClear}
          executionRun={executionRun}
        />
      </div>
      <ReviewToolDock
        tools={[
          {
            id: "funds",
            label: "资金消耗",
            description: "云端模型 token 与费用",
            icon: <Coins className="h-4 w-4" />,
            content: <FundsUsagePanel taskId={taskId} refreshKey={task.updated_at} />,
          },
        ]}
      />
      <AiRunConfirmationDialog
        open={pendingRetry !== null}
        taskId={taskId}
        request={pendingRetry ? {
          operation: "translate_retry",
          mode: pendingRetry.forceRestart ? "rerun" : "resume",
          video_id: pendingRetry.videoId,
          options: {
            ...buildExportOptions(),
            force_restart: pendingRetry.forceRestart,
          },
        } : null}
        onOpenChange={(open) => {
          if (!open) setPendingRetry(null);
        }}
        onConfirm={async (preview) => {
          if (!pendingRetry) return;
          await handleRetryVideo(pendingRetry.videoId, pendingRetry.forceRestart, preview);
        }}
        confirmLabel={pendingRetry?.forceRestart ? "确认完全重新执行" : "确认从检查点继续"}
      />
      </div>
    </div>
    </div>
  );
}
