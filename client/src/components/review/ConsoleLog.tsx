import { useState, useEffect, useRef } from "react";
import { ChevronDown, ChevronUp, FolderOpen } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  getExecutionCheckpointCount,
  getExecutionRetrySeconds,
  getExecutionTotalAttempts,
  type ExecutionRun,
  type ExecutionRunSummary,
} from "@/lib/api";

// ============================================================
// Console Log Entry
// ============================================================
export interface ConsoleEntry {
  id: string;
  timestamp: string;
  level: string;
  source: string;
  message: string;
  progress?: number;
  stage?: string;
}

export type RuntimeHealthTone = "ok" | "warn" | "error" | "unknown";

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

function getFunasrChunkStartKey(message: string): string | null {
  const match = message.match(/^分块识别中\s+(\d+)\/(\d+)\b/);
  return match ? `${match[1]}/${match[2]}` : null;
}

function getFunasrChunkDoneKey(message: string): string | null {
  const match = message.match(/^分块\s+(\d+)\/(\d+)\s+完成\b/);
  return match ? `${match[1]}/${match[2]}` : null;
}

function getActiveChunkEntryIds(entries: ConsoleEntry[]): Set<string> {
  const activeByChunk = new Map<string, string>();

  for (const entry of entries) {
    const startKey = getFunasrChunkStartKey(entry.message);
    if (startKey) {
      activeByChunk.set(startKey, entry.id);
    }

    const doneKey = getFunasrChunkDoneKey(entry.message);
    if (doneKey) {
      activeByChunk.delete(doneKey);
    }
  }

  return new Set(activeByChunk.values());
}

export function ConsoleLog({
  entries,
  expanded,
  onToggle,
  onClear,
  isRunning,
  healthLabel,
  healthTone,
  healthActionLabel,
  onHealthAction,
  onRevealExport,
  executionRun,
}: {
  entries: ConsoleEntry[];
  expanded: boolean;
  onToggle: () => void;
  onClear?: () => void;
  isRunning?: boolean;
  healthLabel?: string | null;
  healthTone?: RuntimeHealthTone;
  healthActionLabel?: string | null;
  onHealthAction?: () => void;
  onRevealExport?: () => void;
  executionRun?: ExecutionRun | ExecutionRunSummary | null;
}) {
  const bottomRef = useRef<HTMLDivElement>(null);
  const [nowMs, setNowMs] = useState(Date.now());

  useEffect(() => {
    if (executionRun?.status !== "retry_wait") return;
    const timer = setInterval(() => setNowMs(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [executionRun?.run_id, executionRun?.status, executionRun?.retry_state.next_retry_at]);

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
    audit: "text-amber-300",
    claude: "text-purple-400",
    openai: "text-purple-400",
    ollama: "text-emerald-400",
    ffmpeg: "text-orange-400",
    jianying: "text-pink-400",
    translate: "text-sky-400",
    tts: "text-fuchsia-400",
    dubbing: "text-indigo-400",
    dub: "text-indigo-400",
    system: "text-slate-400",
    golden_quote: "text-amber-400",
    highlight: "text-yellow-300",
    orchestrator: "text-sky-400",
    diarization: "text-teal-400",
    validator: "text-violet-400",
    rule_engine: "text-lime-400",
  };

  // 最后一条消息是否为"进行中"
  // 满足以下任一条件：①消息以 ... 结尾；②外部明确传入 isRunning=true（任务仍在跑）
  const lastEntry = entries.length > 0 ? entries[entries.length - 1] : null;
  const lastIsInProgress =
    lastEntry &&
    lastEntry.level !== "error" &&
    lastEntry.level !== "success" &&
    (lastEntry.message.endsWith("...") || !!isRunning);
  const activeChunkEntryIds = isRunning ? getActiveChunkEntryIds(entries) : new Set<string>();

  const healthToneClass: Record<RuntimeHealthTone, string> = {
    ok: "text-emerald-400/80",
    warn: "text-amber-400/80",
    error: "text-red-400/80",
    unknown: "text-muted-foreground/60",
  };
  const retrySeconds = getExecutionRetrySeconds(executionRun, nowMs);
  const checkpointCount = getExecutionCheckpointCount(executionRun);
  const executionSummary = executionRun ? [
    executionRun.status === "retry_wait"
      ? `自动重试第 ${executionRun.retry_state.retries_used} 次（总${getExecutionTotalAttempts(executionRun)}次）`
      : null,
    retrySeconds !== null ? `${retrySeconds}秒后重试` : null,
    executionRun.current_stage ? `阶段 ${executionRun.current_stage}` : null,
    checkpointCount > 0 ? `复用检查点 ${checkpointCount}` : null,
    executionRun.status === "failed" || executionRun.status === "interrupted"
      ? (executionRun.recoverable ? "失败可恢复" : "失败不可恢复")
      : null,
  ].filter(Boolean).join(" · ") : "";

  return (
    <div
      className="border-t border-border bg-background transition-all duration-200"
      style={{ height: expanded ? "252px" : "32px" }}
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
        {healthLabel && (
          <span className={cn(
            "text-[10px] font-mono shrink-0 truncate max-w-[280px]",
            healthToneClass[healthTone || "unknown"],
          )}>
            {healthLabel}
          </span>
        )}
        {executionSummary && (
          <span
            className={cn(
              "text-[10px] font-mono shrink-0 truncate max-w-[420px]",
              executionRun?.status === "retry_wait"
                ? "text-amber-300"
                : executionRun?.status === "failed" || executionRun?.status === "interrupted"
                  ? executionRun.recoverable ? "text-amber-400" : "text-red-400"
                  : "text-sky-300/80",
            )}
            title={executionSummary}
          >
            {executionSummary}
          </span>
        )}
        {healthActionLabel && onHealthAction && (
          <button
            onClick={(e) => {
              e.stopPropagation();
              onHealthAction();
            }}
            className="text-[10px] font-mono shrink-0 rounded border border-amber-500/50 bg-amber-500/15 px-2 py-0.5 text-amber-300 hover:bg-amber-500/25 hover:text-amber-200 transition-colors"
            title={healthActionLabel}
          >
            {healthActionLabel}
          </button>
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
        <div className="h-[220px] overflow-x-hidden overflow-y-auto px-3 pb-2">
          {entries.length === 0 ? (
            <p className="console-log text-muted-foreground/40 mt-2">等待任务启动...</p>
          ) : (
            entries.map((entry, idx) => {
              const isLast = idx === entries.length - 1;
              const isActiveChunk = activeChunkEntryIds.has(entry.id);
              const showAnimDots = isActiveChunk || (isLast && lastIsInProgress);
              const canRevealExport =
                isLast &&
                entry.level === "success" &&
                entry.source === "ffmpeg" &&
                entry.message.includes("导出完成") &&
                !!onRevealExport;
              // 如果是最后一条且带有动效，去掉末尾已有的 ...（避免重复）
              const displayMsg = showAnimDots && entry.message.endsWith("...")
                ? entry.message.slice(0, -3)
                : entry.message;

              return (
                <div key={entry.id} className="py-0.5 leading-4">
                  <span className="console-log mr-2 inline-block align-top text-[10px] text-muted-foreground/40">
                    {new Date(entry.timestamp).toLocaleTimeString("zh-CN", { hour12: false })}
                  </span>
                  <span className={cn(
                    "console-log mr-2 inline-block max-w-24 truncate align-top text-[11px]",
                    sourceColor[entry.source] || "text-slate-400",
                  )}>
                    [{entry.source}]
                  </span>
                  {entry.stage && (
                    <span className="console-log mr-2 inline-block max-w-20 truncate align-top text-[10px] text-muted-foreground/60">
                      {entry.stage}
                    </span>
                  )}
                  <span className={cn(
                    "console-log break-all text-[11px]",
                    levelColor[entry.level] || "text-slate-300",
                  )}>
                    {displayMsg}
                    {showAnimDots && <AnimatedDots />}
                  </span>
                  {showAnimDots && (
                    <ElapsedTimer since={entry.timestamp} />
                  )}
                  {canRevealExport && (
                    <button
                      type="button"
                      onClick={onRevealExport}
                      className="ml-2 inline-flex align-middle rounded border border-green-500/40 bg-green-500/10 p-1 text-green-400 transition-colors hover:bg-green-500/20 hover:text-green-300"
                      title="在 Finder 中显示"
                      aria-label="在 Finder 中显示导出文件"
                    >
                      <FolderOpen className="h-3.5 w-3.5" />
                    </button>
                  )}
                  {entry.progress !== undefined && entry.progress !== null && (
                    <span className="console-log ml-2 inline-block text-[10px] font-mono text-primary">
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
