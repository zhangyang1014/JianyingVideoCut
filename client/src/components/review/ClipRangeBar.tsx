import { useState, useEffect, useRef } from "react";
import { toast } from "sonner";
import { updateClipRange } from "@/lib/api";
import { secsToInput, inputToSecs } from "./utils";

// ============================================================
// Clip Range Bar — 视频裁剪区间（In/Out Point）
// ============================================================
export function ClipRangeBar({
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
