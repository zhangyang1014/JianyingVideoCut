import { useState, useEffect, useRef, useCallback, useMemo, type PointerEvent } from "react";
import { Play, Pause, RotateCcw, RotateCw, SkipBack, SkipForward, Volume2, VolumeX } from "lucide-react";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { getVideoUrl, formatTimestamp, type Segment } from "@/lib/api";
import { cn } from "@/lib/utils";
import { buildKeptRanges, findPlaybackRange, type AuditionRequest } from "@/lib/cut-review";

// ============================================================
// Video Player
// ============================================================
export type PlaybackMode = "all" | "skip-hidden" | "highlight-reel";

export function VideoPlayer({
  taskId,
  currentTime,
  seekRequestId,
  onTimeUpdate,
  segments,
  playbackMode,
  onPlaybackModeChange,
  highlightSegmentIds,
  clipStart,
  clipEnd,
  videoUrl,
  previewMode,
  previewPlayRequestId = 0,
  auditionRequest,
  onExitPreview,
  onPreviewEnded,
  videoViewportHeight,
  onVideoViewportHeightChange,
  onVideoNaturalSize,
  minVideoViewportHeight = 160,
  maxVideoViewportHeight = 520,
}: {
  taskId: string;
  currentTime: number;
  /** Explicit seeks are exact and repeatable, including another click on the same word. */
  seekRequestId?: number;
  onTimeUpdate: (t: number) => void;
  segments: Segment[];
  playbackMode: PlaybackMode;
  onPlaybackModeChange: (mode: PlaybackMode) => void;
  highlightSegmentIds?: ReadonlySet<string>;
  clipStart?: number | null;
  clipEnd?: number | null;
  videoUrl?: string | null;
  previewMode?: boolean;
  previewPlayRequestId?: number;
  auditionRequest?: AuditionRequest | null;
  onExitPreview?: () => void;
  onPreviewEnded?: () => void;
  videoViewportHeight?: number;
  onVideoViewportHeightChange?: (height: number) => void;
  onVideoNaturalSize?: (size: { width: number; height: number; suggestedViewportHeight?: number }) => void;
  minVideoViewportHeight?: number;
  maxVideoViewportHeight?: number;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const videoViewportRef = useRef<HTMLDivElement>(null);
  const [playing, setPlaying] = useState(false);
  const [muted, setMuted] = useState(false);
  const [duration, setDuration] = useState(0);
  const [localTime, setLocalTime] = useState(0);
  const [resizingViewport, setResizingViewport] = useState(false);
  const rafRef = useRef<number | null>(null);
  const onTimeUpdateRef = useRef(onTimeUpdate);
  const playableRanges = useMemo(() => buildKeptRanges(playbackMode === "highlight-reel"
    ? segments.map(segment => highlightSegmentIds?.has(segment.id) ? segment : { ...segment, action: "delete" as const })
    : segments), [segments, playbackMode, highlightSegmentIds]);
  const rangesRef = useRef(playableRanges);
  const consumedAuditionId = useRef<number | null>(null);
  const auditionRef = useRef<{ ranges: AuditionRequest["ranges"]; index: number } | null>(null);
  const [auditionLabel, setAuditionLabel] = useState<string | null>(null);
  const [playbackError, setPlaybackError] = useState<string | null>(null);
  const timelineKey = useMemo(() => JSON.stringify(segments.map(segment => [segment.id, segment.start, segment.end, segment.action])), [segments]);
  const playbackModeRef = useRef(playbackMode);

  const previewModeRef = useRef(!!previewMode);
  const resizeStartRef = useRef<{ pointerId: number; y: number; height: number } | null>(null);
  // 标记是否为程序性 seek（跳过删除片段），避免误触 onPause → stopRaf
  const programmaticSeekRef = useRef(false);
  const resolvedVideoUrl = videoUrl || getVideoUrl(taskId);

  useEffect(() => { rangesRef.current = playableRanges; }, [playableRanges]);
  useEffect(() => { playbackModeRef.current = playbackMode; }, [playbackMode]);

  useEffect(() => { previewModeRef.current = !!previewMode; }, [previewMode]);

  // 始终持有最新的 onTimeUpdate，避免 rAF 闭包过期
  useEffect(() => {
    onTimeUpdateRef.current = onTimeUpdate;
  }, [onTimeUpdate]);

  // rAF 保持逐字高亮精度；工作台通过独立时钟只更新当前句。
  const startRaf = useCallback(() => {
    if (rafRef.current !== null) return;
    const tick = () => {
      if (videoRef.current) {
        const vt = videoRef.current.currentTime;
        const mode = playbackModeRef.current;
        if (!previewModeRef.current && !programmaticSeekRef.current) {
          const audition = auditionRef.current;
          let target: number | undefined;
          let completed = false;
          if (audition) {
            const range = audition.ranges[audition.index];
            if (vt >= range.end) {
              audition.index += 1;
              target = audition.ranges[audition.index]?.start;
              completed = target === undefined;
            } else if (vt < range.start) target = range.start;
          } else if (mode !== "all") {
            const range = findPlaybackRange(rangesRef.current, vt);
            completed = !range;
            if (range && vt < range.start) target = range.start;
          }
          if (completed) {
            auditionRef.current = null;
            setAuditionLabel(null);
            videoRef.current.pause();
            rafRef.current = null;
            onTimeUpdateRef.current(vt);
            return;
          }
          if (target !== undefined) {
            programmaticSeekRef.current = true;
            rafRef.current = null;
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

  // A seek command is independent of playback ticks, so sub-500ms word jumps are not lost.
  useEffect(() => {
    if (!videoRef.current) return;
    if (seekRequestId !== undefined) {
      auditionRef.current = null;
      setAuditionLabel(null);
      programmaticSeekRef.current = false;
    } else if (programmaticSeekRef.current || auditionRef.current) return;
    const diff = Math.abs(videoRef.current.currentTime - currentTime);
    if (seekRequestId !== undefined || diff > 0.5) {
      videoRef.current.currentTime = currentTime;
    }
  }, [currentTime, seekRequestId]);

  useEffect(() => {
    if (!previewMode || !previewPlayRequestId || !videoRef.current) return;
    const video = videoRef.current;
    video.currentTime = 0;
    void video.play().catch(() => {});
  }, [previewMode, previewPlayRequestId, resolvedVideoUrl]);

  const cancelAudition = useCallback(() => {
    auditionRef.current = null;
    setAuditionLabel(null);
  }, []);

  useEffect(() => {
    if (auditionRef.current) videoRef.current?.pause();
    cancelAudition();
    programmaticSeekRef.current = false;
  }, [timelineKey, resolvedVideoUrl, cancelAudition]);

  useEffect(() => {
    if (!auditionRequest || previewMode || !videoRef.current || consumedAuditionId.current === auditionRequest.id) return;
    consumedAuditionId.current = auditionRequest.id;
    const ranges = auditionRequest.ranges.filter(range => Number.isFinite(range.start) && Number.isFinite(range.end) && range.end > range.start);
    if (!ranges.length) return;
    const audition = { ranges, index: 0 };
    auditionRef.current = audition;
    setAuditionLabel(auditionRequest.label);
    setPlaybackError(null);
    programmaticSeekRef.current = false;
    videoRef.current.currentTime = ranges[0].start;
    onTimeUpdateRef.current(ranges[0].start);
    void videoRef.current.play().catch(() => {
      // 切换源视频或主动结束试听会中断旧 play Promise，不能误报或取消新试听。
      if (auditionRef.current !== audition) return;
      cancelAudition();
      setPlaybackError("无法开始试听，请检查视频是否可播放后重试。");
    });
  }, [auditionRequest, previewMode, cancelAudition]);

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
      if (target.closest("input, textarea, select, button, [role=button], [role=radio], [contenteditable=true]")) return;
      e.preventDefault();
      togglePlay();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [togglePlay]);

  const skip = (seconds: number) => {
    cancelAudition();
    if (videoRef.current) {
      videoRef.current.currentTime = Math.min(
        duration || Number.POSITIVE_INFINITY,
        Math.max(0, videoRef.current.currentTime + seconds),
      );
    }
  };

  const seekSentence = (direction: -1 | 1) => {
    cancelAudition();
    if (!videoRef.current) return;
    const navigableSegments = segments
      .filter((segment) => {
        if (playbackMode === "all") return true;
        if (playbackMode === "skip-hidden") return segment.action !== "delete";
        return segment.action !== "delete" && highlightSegmentIds?.has(segment.id);
      })
      .slice()
      .sort((a, b) => a.start - b.start);
    if (navigableSegments.length === 0) return;

    const time = videoRef.current.currentTime;
    const activeIndex = navigableSegments.findIndex(
      (segment) => time >= segment.start && time < segment.end,
    );
    let targetIndex: number;

    if (direction === -1) {
      targetIndex = activeIndex >= 0
        ? Math.max(0, activeIndex - 1)
        : navigableSegments.findLastIndex((segment) => segment.start < time);
      if (targetIndex < 0) targetIndex = 0;
    } else {
      targetIndex = activeIndex >= 0
        ? Math.min(navigableSegments.length - 1, activeIndex + 1)
        : navigableSegments.findIndex((segment) => segment.start > time);
      if (targetIndex < 0) targetIndex = navigableSegments.length - 1;
    }

    videoRef.current.currentTime = navigableSegments[targetIndex].start;
  };

  const progressPercent = duration > 0
    ? Math.min(100, Math.max(0, (localTime / duration) * 100))
    : 0;
  const canResizeViewport = videoViewportHeight != null && !!onVideoViewportHeightChange;
  const hasHighlightSegments = (highlightSegmentIds?.size ?? 0) > 0;
  const boundedVideoViewportHeight = canResizeViewport
    ? Math.min(maxVideoViewportHeight, Math.max(minVideoViewportHeight, videoViewportHeight))
    : undefined;
  const iconButtonClass =
    "flex h-9 w-full min-w-0 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground";

  const reportVideoNaturalSize = useCallback((video: HTMLVideoElement) => {
    const width = video.videoWidth;
    const height = video.videoHeight;
    if (width <= 0 || height <= 0) return;

    const viewportWidth = videoViewportRef.current?.clientWidth ?? 0;
    onVideoNaturalSize?.({
      width,
      height,
      suggestedViewportHeight: viewportWidth > 0 ? Math.round(viewportWidth * (height / width)) : undefined,
    });
  }, [onVideoNaturalSize]);

  const handleResizePointerDown = useCallback((e: PointerEvent<HTMLDivElement>) => {
    if (!canResizeViewport || boundedVideoViewportHeight == null) return;
    e.preventDefault();
    e.currentTarget.setPointerCapture(e.pointerId);
    resizeStartRef.current = {
      pointerId: e.pointerId,
      y: e.clientY,
      height: boundedVideoViewportHeight,
    };
    setResizingViewport(true);
  }, [boundedVideoViewportHeight, canResizeViewport]);

  const handleResizePointerMove = useCallback((e: PointerEvent<HTMLDivElement>) => {
    const start = resizeStartRef.current;
    if (!start || start.pointerId !== e.pointerId || !onVideoViewportHeightChange) return;
    const nextHeight = Math.min(
      maxVideoViewportHeight,
      Math.max(minVideoViewportHeight, start.height + e.clientY - start.y)
    );
    onVideoViewportHeightChange(nextHeight);
  }, [maxVideoViewportHeight, minVideoViewportHeight, onVideoViewportHeightChange]);

  const handleResizePointerEnd = useCallback((e: PointerEvent<HTMLDivElement>) => {
    const start = resizeStartRef.current;
    if (!start || start.pointerId !== e.pointerId) return;
    if (e.currentTarget.hasPointerCapture(e.pointerId)) {
      e.currentTarget.releasePointerCapture(e.pointerId);
    }
    resizeStartRef.current = null;
    setResizingViewport(false);
  }, []);

  return (
    <div className="flex flex-col bg-black rounded-xl overflow-hidden">
      {/* Video */}
      <div
        ref={videoViewportRef}
        className={cn(
          "relative bg-black flex items-center justify-center",
          canResizeViewport ? "shrink-0" : "flex-1"
        )}
        style={boundedVideoViewportHeight != null ? { height: boundedVideoViewportHeight } : undefined}
      >
        <video
          ref={videoRef}
          src={resolvedVideoUrl}
          preload="auto"
          className="max-w-full max-h-full object-contain"
          onTimeUpdate={(e) => {
            setLocalTime(e.currentTarget.currentTime);
            // 暂停后的逐句跳转和进度拖动也要同步字幕；播放时仍由 rAF 驱动。
            if (e.currentTarget.paused) onTimeUpdateRef.current(e.currentTarget.currentTime);
          }}
          onLoadedMetadata={(e) => {
            reportVideoNaturalSize(e.currentTarget);
          }}
          onDurationChange={(e) => setDuration(e.currentTarget.duration)}
          onPlay={() => {
            setPlaying(true); startRaf();
          }}
          onPause={() => {
            if (programmaticSeekRef.current) return;
            setPlaying(false); stopRaf();
            if (videoRef.current) onTimeUpdateRef.current(videoRef.current.currentTime);
          }}
          onEnded={() => {
            setPlaying(false);
            stopRaf();
            cancelAudition();
            if (previewMode) onPreviewEnded?.();
          }}
          onSeeked={() => {
            if (videoRef.current) {
              setLocalTime(videoRef.current.currentTime);
              onTimeUpdateRef.current(videoRef.current.currentTime);
            }
            if (programmaticSeekRef.current) {
              programmaticSeekRef.current = false;
              if (!videoRef.current?.paused) startRaf();
            }
          }}
        />
      </div>

      {canResizeViewport && (
        <div
          className={cn(
            "group relative h-3 shrink-0 cursor-row-resize bg-card touch-none",
            resizingViewport && "bg-sky-400/5"
          )}
          onPointerDown={handleResizePointerDown}
          onPointerMove={handleResizePointerMove}
          onPointerUp={handleResizePointerEnd}
          onPointerCancel={handleResizePointerEnd}
          role="separator"
          aria-orientation="horizontal"
          aria-label="拖动调整视频窗口高度"
        >
          <div className={cn(
            "absolute left-3 right-3 top-1/2 h-1 -translate-y-1/2 rounded-full bg-slate-500/70 transition-colors",
            "group-hover:bg-sky-400/80",
            resizingViewport && "bg-sky-400 shadow-[0_0_12px_rgba(56,189,248,0.4)]"
          )} />
        </div>
      )}

      {auditionLabel && <div className="flex items-center justify-between gap-2 bg-primary/10 px-3 py-2 text-xs" role="status">
        <span>{auditionLabel}</span>
        <button onClick={() => { videoRef.current?.pause(); cancelAudition(); }} className="shrink-0 text-primary">结束试听</button>
      </div>}
      {playbackError && <p className="px-3 py-2 text-xs text-red-400" role="alert">{playbackError}</p>}
      {/* Controls */}
      <div className="@container bg-card px-3 py-2.5 space-y-2">
        {/* Progress bar（含裁剪区间蒙层） */}
        <div
          className="group relative flex h-5 cursor-pointer touch-none items-center rounded-full outline-none focus-visible:ring-2 focus-visible:ring-primary/60 focus-visible:ring-offset-2 focus-visible:ring-offset-card"
          role="slider"
          tabIndex={0}
          aria-label="视频进度"
          aria-valuemin={0}
          aria-valuemax={duration || 0}
          aria-valuenow={localTime}
          aria-valuetext={`${formatTimestamp(localTime)} / ${formatTimestamp(duration)}`}
          onClick={(e) => {
            if (!videoRef.current || !duration) return;
            cancelAudition();
            const rect = e.currentTarget.getBoundingClientRect();
            const ratio = (e.clientX - rect.left) / rect.width;
            videoRef.current.currentTime = ratio * duration;
          }}
          onKeyDown={(e) => {
            if (!videoRef.current || !duration) return;
            if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
            e.preventDefault();
            cancelAudition();
            const offset = e.key === "ArrowLeft" ? -5 : 5;
            videoRef.current.currentTime = Math.min(
              duration,
              Math.max(0, videoRef.current.currentTime + offset),
            );
          }}
        >
          <div className="relative h-1.5 w-full overflow-hidden rounded-full bg-slate-600/70 transition-[height,background-color] group-hover:h-2 group-hover:bg-slate-500/70">
            <div
              className="h-full bg-primary transition-[width] duration-100"
              style={{ width: `${progressPercent}%` }}
            />
            {/* 裁剪区间外左侧遮罩 */}
            {duration > 0 && clipStart != null && clipStart > 0 && (
              <div
                className="pointer-events-none absolute left-0 top-0 h-full bg-black/60"
                style={{ width: `${Math.min(100, (clipStart / duration) * 100)}%` }}
              />
            )}
            {/* 裁剪区间外右侧遮罩 */}
            {duration > 0 && clipEnd != null && clipEnd < duration && (
              <div
                className="pointer-events-none absolute top-0 h-full bg-black/60"
                style={{
                  left: `${Math.min(100, (clipEnd / duration) * 100)}%`,
                  width: `${Math.min(100, ((duration - clipEnd) / duration) * 100)}%`,
                }}
              />
            )}
          </div>
          <div
            className="pointer-events-none absolute top-1/2 h-3.5 w-3.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-card bg-primary shadow-md transition-transform group-hover:scale-110"
            style={{ left: `${progressPercent}%` }}
          />
        </div>

        {/* Buttons */}
        <div className="grid grid-cols-[repeat(2,minmax(0,1fr))_minmax(64px,1.35fr)_repeat(3,minmax(0,1fr))] items-center gap-1.5">
          <button
            type="button"
            aria-label="向前 10 秒"
            title="向前 10 秒"
            onClick={() => skip(-10)}
            className={iconButtonClass}
          >
            <span className="relative flex items-center justify-center">
              <RotateCcw className="h-5 w-5" />
              <span className="absolute text-[8px] font-semibold">10</span>
            </span>
          </button>
          <button
            type="button"
            aria-label="上一句"
            title="上一句"
            onClick={() => seekSentence(-1)}
            className={iconButtonClass}
          >
            <SkipBack className="h-4 w-4" />
          </button>
          <button
            type="button"
            aria-label={playing ? "暂停" : "播放"}
            onClick={togglePlay}
            className="flex h-10 w-full min-w-0 items-center justify-center rounded-xl bg-primary text-primary-foreground shadow-sm transition-colors hover:bg-primary/80"
          >
            {playing ? <Pause className="w-4 h-4" /> : <Play className="w-4 h-4 ml-0.5" />}
          </button>
          <button
            type="button"
            aria-label="下一句"
            title="下一句"
            onClick={() => seekSentence(1)}
            className={iconButtonClass}
          >
            <SkipForward className="h-4 w-4" />
          </button>
          <button
            type="button"
            aria-label="往后 10 秒"
            title="往后 10 秒"
            onClick={() => skip(10)}
            className={iconButtonClass}
          >
            <span className="relative flex items-center justify-center">
              <RotateCw className="h-5 w-5" />
              <span className="absolute text-[8px] font-semibold">10</span>
            </span>
          </button>
          <button
            type="button"
            aria-label={muted ? "取消静音" : "静音"}
            title={muted ? "取消静音" : "静音"}
            onClick={() => { setMuted(!muted); if (videoRef.current) videoRef.current.muted = !muted; }}
            className={iconButtonClass}
          >
            {muted ? <VolumeX className="w-4 h-4" /> : <Volume2 className="w-4 h-4" />}
          </button>
        </div>

        <div className="flex justify-center whitespace-nowrap rounded-lg bg-background/50 px-2.5 py-1">
          <span className="timestamp text-[11px] tabular-nums">
            {formatTimestamp(localTime)} / {formatTimestamp(duration)}
          </span>
        </div>

        {(previewMode || segments.length > 0) && (
          <div className="flex min-w-0 items-center justify-end">
            {previewMode && (
              <button
                type="button"
                onClick={onExitPreview}
                className="w-full rounded-lg border border-orange-500/30 bg-orange-500/10 px-3 py-1.5 text-center text-[11px] font-medium text-orange-300 transition-colors hover:bg-orange-500/15 @[360px]:w-auto"
              >
                返回原视频
              </button>
            )}
            {!previewMode && segments.length > 0 && (
              <RadioGroup
                value={playbackMode}
                onValueChange={(value) => { cancelAudition(); onPlaybackModeChange(value as PlaybackMode); }}
                className="grid w-full grid-cols-3 gap-1 rounded-lg border border-border bg-background/40 p-1 text-[10px] @[360px]:ml-auto @[360px]:text-[11px]"
                aria-label="播放模式"
              >
                <div className="relative">
                  <RadioGroupItem id="playback-mode-all" value="all" className="sr-only" />
                  <label
                    htmlFor="playback-mode-all"
                    className={cn(
                      "flex cursor-pointer items-center justify-center rounded-md px-1.5 py-1.5 font-medium transition-colors",
                      playbackMode === "all" ? "bg-secondary text-foreground" : "text-muted-foreground hover:text-foreground"
                    )}
                  >
                    全部
                  </label>
                </div>
                <div className="relative">
                  <RadioGroupItem id="playback-mode-skip-hidden" value="skip-hidden" className="sr-only" />
                  <label
                    htmlFor="playback-mode-skip-hidden"
                    className={cn(
                      "flex cursor-pointer items-center justify-center rounded-md px-1.5 py-1.5 font-medium transition-colors",
                      playbackMode === "skip-hidden" ? "bg-green-500/20 text-green-400" : "text-muted-foreground hover:text-foreground"
                    )}
                  >
                    只播放不隐藏
                  </label>
                </div>
                <div className="relative">
                  <RadioGroupItem
                    id="playback-mode-highlight-reel"
                    value="highlight-reel"
                    className="sr-only"
                    disabled={!hasHighlightSegments}
                  />
                  <label
                    htmlFor="playback-mode-highlight-reel"
                    title={hasHighlightSegments ? "只播放直播卖货集锦中的片段" : "尚未生成直播卖货集锦"}
                    aria-disabled={!hasHighlightSegments}
                    className={cn(
                      "flex items-center justify-center rounded-md px-1.5 py-1.5 font-medium transition-colors",
                      !hasHighlightSegments
                        ? "cursor-not-allowed text-muted-foreground/40"
                        : playbackMode === "highlight-reel"
                          ? "cursor-pointer bg-orange-500/20 text-orange-300"
                          : "cursor-pointer text-muted-foreground hover:text-foreground"
                    )}
                  >
                    直播卖货集锦
                  </label>
                </div>
              </RadioGroup>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
