/**
 * GoldenClip Review Workbench
 * Design: 暗金剪辑台 · 编导美学
 * Layout: Left (Video Player) | Center (Subtitle Audit Stream) | Right (Speaker Panel)
 * Bottom: Real-time Console Log
 */

import { useState, useEffect, useRef, useCallback } from "react";
import { useParams, useLocation } from "wouter";
import { Button } from "@/components/ui/button";
import { toast } from "sonner";
import {
  Play, Pause, ArrowLeft, Zap, Scissors, Download,
  ChevronDown, ChevronUp, Loader2, CheckCircle2,
  AlertCircle, RefreshCw, Eye, EyeOff, FileJson,
  Users, Mic, Volume2, VolumeX, SkipBack, SkipForward,
} from "lucide-react";
import {
  fetchTask, triggerASR, triggerAudit, toggleSegment,
  exportFFmpeg, exportJianying, createLogWebSocket,
  getVideoUrl, formatTimestamp, formatDuration,
  STATUS_LABELS, STATUS_COLORS, SPEAKER_COLORS,
  type Task, type Segment, type LogMessage,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import { Input } from "@/components/ui/input";

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

function ConsoleLog({ entries, expanded, onToggle }: {
  entries: ConsoleEntry[];
  expanded: boolean;
  onToggle: () => void;
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
  };

  return (
    <div
      className="border-t border-border bg-[#080A0D] transition-all duration-200"
      style={{ height: expanded ? "180px" : "32px" }}
    >
      {/* Console Header */}
      <div
        className="h-8 flex items-center px-3 gap-2 cursor-pointer hover:bg-secondary/50 transition-colors"
        onClick={onToggle}
      >
        <div className="flex items-center gap-1.5">
          <div className="w-2 h-2 rounded-full bg-green-500 animate-pulse" />
          <span className="console-log text-muted-foreground text-[11px] uppercase tracking-wider">Console</span>
        </div>
        {entries.length > 0 && (
          <span className="text-[10px] text-muted-foreground/50 font-mono">{entries.length} 条</span>
        )}
        <div className="ml-auto">
          {expanded ? <ChevronDown className="w-3 h-3 text-muted-foreground" /> : <ChevronUp className="w-3 h-3 text-muted-foreground" />}
        </div>
      </div>

      {/* Log entries */}
      {expanded && (
        <div className="overflow-y-auto h-[148px] px-3 pb-2">
          {entries.length === 0 ? (
            <p className="console-log text-muted-foreground/40 mt-2">等待任务启动...</p>
          ) : (
            entries.map((entry) => (
              <div key={entry.id} className="flex items-start gap-2 py-0.5">
                <span className="console-log text-muted-foreground/40 shrink-0 text-[10px] mt-px">
                  {new Date(entry.timestamp).toLocaleTimeString("zh-CN", { hour12: false })}
                </span>
                <span className={cn("console-log text-[11px] shrink-0 w-12", sourceColor[entry.source] || "text-slate-400")}>
                  [{entry.source}]
                </span>
                <span className={cn("console-log text-[11px]", levelColor[entry.level] || "text-slate-300")}>
                  {entry.message}
                </span>
                {entry.progress !== undefined && entry.progress !== null && (
                  <span className="console-log text-[10px] text-primary font-mono ml-auto shrink-0">
                    {Math.round(entry.progress * 100)}%
                  </span>
                )}
              </div>
            ))
          )}
          <div ref={bottomRef} />
        </div>
      )}
    </div>
  );
}

// ============================================================
// Segment Item
// ============================================================
function SegmentItem({
  segment,
  isActive,
  onClick,
  onToggle,
}: {
  segment: Segment;
  isActive: boolean;
  onClick: () => void;
  onToggle: (e: React.MouseEvent) => void;
}) {
  const speakerColor = SPEAKER_COLORS[segment.speaker || "default"] || SPEAKER_COLORS.default;
  const isDelete = segment.action === "delete";

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
        "group relative rounded-lg px-3 py-2.5 mb-1.5 cursor-pointer transition-all duration-150",
        isActive ? "ring-1 ring-primary/50" : "",
        isDelete ? "segment-delete" : "segment-keep",
        "hover:brightness-110"
      )}
      onClick={onClick}
    >
      {/* Speaker indicator */}
      {segment.speaker && (
        <div
          className="absolute left-0 top-0 bottom-0 w-0.5 rounded-l-lg"
          style={{ background: speakerColor }}
        />
      )}

      <div className="flex items-start gap-2 pl-1">
        {/* Timestamps */}
        <div className="shrink-0 flex flex-col gap-0.5 pt-0.5">
          <span className="timestamp text-[10px]">{formatTimestamp(segment.start)}</span>
          <span className="timestamp text-[10px] opacity-50">{formatTimestamp(segment.end)}</span>
        </div>

        {/* Text */}
        <div className="flex-1 min-w-0">
          <p className={cn(
            "text-sm leading-relaxed",
            isDelete ? "text-muted-foreground line-through" : "text-foreground"
          )}>
            {segment.text}
          </p>

          {/* Reason + Rule */}
          {isDelete && segment.reason && (
            <div className="flex items-center gap-1.5 mt-1">
              {segment.rule && (
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-red-900/30 text-red-400 font-mono">
                  {ruleShort[segment.rule] || segment.rule}
                </span>
              )}
              <span className="text-[10px] text-muted-foreground/60 truncate">{segment.reason}</span>
            </div>
          )}
        </div>

        {/* Toggle button */}
        <button
          className={cn(
            "shrink-0 w-7 h-7 rounded-md flex items-center justify-center transition-all duration-150",
            "opacity-0 group-hover:opacity-100",
            isDelete
              ? "bg-green-900/30 text-green-400 hover:bg-green-900/50"
              : "bg-red-900/30 text-red-400 hover:bg-red-900/50"
          )}
          onClick={onToggle}
          title={isDelete ? "恢复保留" : "标记删除"}
        >
          {isDelete ? <Eye className="w-3.5 h-3.5" /> : <EyeOff className="w-3.5 h-3.5" />}
        </button>
      </div>

      {/* Duration */}
      <div className="absolute bottom-1.5 right-2">
        <span className="text-[10px] font-mono text-muted-foreground/40">
          {formatDuration(segment.end - segment.start)}
        </span>
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
}: {
  taskId: string;
  currentTime: number;
  onTimeUpdate: (t: number) => void;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [playing, setPlaying] = useState(false);
  const [muted, setMuted] = useState(false);
  const [duration, setDuration] = useState(0);
  const [localTime, setLocalTime] = useState(0);

  // Seek when currentTime changes externally
  useEffect(() => {
    if (videoRef.current && Math.abs(videoRef.current.currentTime - currentTime) > 0.5) {
      videoRef.current.currentTime = currentTime;
    }
  }, [currentTime]);

  const togglePlay = () => {
    if (!videoRef.current) return;
    if (playing) videoRef.current.pause();
    else videoRef.current.play();
  };

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
          src={getVideoUrl(taskId)}
          className="max-w-full max-h-full object-contain"
          onTimeUpdate={(e) => {
            const t = e.currentTarget.currentTime;
            setLocalTime(t);
            onTimeUpdate(t);
          }}
          onDurationChange={(e) => setDuration(e.currentTarget.duration)}
          onPlay={() => setPlaying(true)}
          onPause={() => setPlaying(false)}
        />
      </div>

      {/* Controls */}
      <div className="bg-[#0D0F12] px-3 py-2 space-y-1.5">
        {/* Progress bar */}
        <div
          className="h-1 bg-secondary rounded-full overflow-hidden cursor-pointer"
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
// Speaker Panel (Right)
// ============================================================
function SpeakerPanel({ task }: { task: Task }) {
  const speakers = task.asr_result?.speakers || [];
  const segments = task.audit_segments || [];

  const speakerStats = speakers.map((spk) => {
    const spkSegs = segments.filter((s) => s.speaker === spk);
    const kept = spkSegs.filter((s) => s.action === "keep");
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
                <div className="grid grid-cols-2 gap-1 text-[11px]">
                  <div>
                    <span className="text-muted-foreground">片段</span>
                    <span className="text-foreground font-mono ml-1">{total}</span>
                  </div>
                  <div>
                    <span className="text-muted-foreground">保留</span>
                    <span className="text-green-400 font-mono ml-1">{kept}</span>
                  </div>
                  <div className="col-span-2">
                    <span className="text-muted-foreground">时长</span>
                    <span className="timestamp ml-1">{formatDuration(totalDuration)}</span>
                  </div>
                </div>
              </div>
            );
          })
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
// Export Panel
// ============================================================
function ExportPanel({
  task,
  onExportFFmpeg,
  onExportJianying,
}: {
  task: Task;
  onExportFFmpeg: () => void;
  onExportJianying: () => void;
}) {
  const [jianyingFolder, setJianyingFolder] = useState("");
  const [claudeKey, setClaudeKey] = useState("");
  const isExporting = task.status === "export_running";

  return (
    <div className="bg-card border border-border rounded-xl p-4 space-y-3">
      <h3 className="text-xs font-medium text-muted-foreground uppercase tracking-wider">导出选项</h3>

      {/* Claude API Key */}
      <div className="space-y-1">
        <label className="text-[11px] text-muted-foreground">Claude API Key (可选)</label>
        <Input
          type="password"
          value={claudeKey}
          onChange={(e) => setClaudeKey(e.target.value)}
          placeholder="sk-ant-..."
          className="h-7 text-xs bg-secondary border-border"
        />
      </div>

      {/* JianYing folder */}
      <div className="space-y-1">
        <label className="text-[11px] text-muted-foreground">剪映草稿目录 (可选)</label>
        <Input
          value={jianyingFolder}
          onChange={(e) => setJianyingFolder(e.target.value)}
          placeholder="/Users/xxx/Movies/JianyingPro/User Data/Projects"
          className="h-7 text-xs bg-secondary border-border"
        />
      </div>

      <div className="flex gap-2 pt-1">
        <Button
          size="sm"
          className="flex-1 bg-primary text-primary-foreground hover:bg-primary/90 text-xs gap-1.5"
          onClick={onExportFFmpeg}
          disabled={isExporting}
        >
          {isExporting ? <Loader2 className="w-3 h-3 animate-spin" /> : <Download className="w-3 h-3" />}
          FFmpeg 快速导出
        </Button>
        <Button
          size="sm"
          variant="outline"
          className="flex-1 border-border text-foreground hover:bg-secondary text-xs gap-1.5"
          onClick={onExportJianying}
          disabled={isExporting}
        >
          <FileJson className="w-3 h-3" />
          剪映草稿
        </Button>
      </div>

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
  claudeKey,
  onClaudeKeyChange,
}: {
  task: Task;
  styleMode: "immersive" | "quick_cut";
  onStyleModeChange: (m: "immersive" | "quick_cut") => void;
  claudeKey: string;
  onClaudeKeyChange: (k: string) => void;
}) {
  const meta = SCENARIO_META[task.task_type] || SCENARIO_META.monologue_clean;
  const [expanded, setExpanded] = useState(false);

  return (
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

          {/* Claude Key */}
          <div className="space-y-1">
            <p className="text-[10px] text-muted-foreground/60 uppercase tracking-wider">Claude API Key</p>
            <Input
              type="password"
              value={claudeKey}
              onChange={(e) => onClaudeKeyChange(e.target.value)}
              placeholder="sk-ant-... (可选)"
              className="h-6 text-[10px] bg-secondary/50 border-border"
            />
            <p className="text-[10px] text-muted-foreground/40">
              {claudeKey ? "✓ 将使用 Claude 审计" : "未填写则用规则引擎"}
            </p>
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
  );
}

// ============================================================
// Review Workbench Main
// ============================================================
export default function ReviewWorkbench() {
  const params = useParams<{ id: string }>();
  const taskId = params.id;
  const [, navigate] = useLocation();

  const [task, setTask] = useState<Task | null>(null);
  const [loading, setLoading] = useState(true);
  const [currentTime, setCurrentTime] = useState(0);
  const [activeSegmentId, setActiveSegmentId] = useState<string | null>(null);
  const [consoleEntries, setConsoleEntries] = useState<ConsoleEntry[]>([]);
  const [consoleExpanded, setConsoleExpanded] = useState(true);
  const [showOnlyKept, setShowOnlyKept] = useState(false);
  const [claudeKey, setClaudeKey] = useState("");
  const [styleMode, setStyleMode] = useState<"immersive" | "quick_cut">("immersive");
  const wsRef = useRef<WebSocket | null>(null);
  const segmentListRef = useRef<HTMLDivElement>(null);

  const addLog = useCallback((msg: LogMessage) => {
    if (msg.type === "log") {
      setConsoleEntries((prev) => [
        ...prev.slice(-199),
        {
          id: `${Date.now()}-${Math.random()}`,
          timestamp: msg.timestamp || new Date().toISOString(),
          level: msg.level || "info",
          source: msg.source || "system",
          message: msg.message || "",
          progress: msg.progress,
        },
      ]);
    }
    if (msg.type === "status_change" || msg.type === "export_done") {
      loadTask();
    }
  }, []);

  const loadTask = useCallback(async () => {
    try {
      const data = await fetchTask(taskId);
      setTask(data);
    } catch {
      toast.error("任务不存在");
      navigate("/");
    } finally {
      setLoading(false);
    }
  }, [taskId]);

  useEffect(() => {
    loadTask();
    // Connect WebSocket
    const ws = createLogWebSocket(taskId, addLog);
    wsRef.current = ws;
    return () => ws.close();
  }, [taskId, loadTask, addLog]);

  // Auto-highlight active segment based on video time
  useEffect(() => {
    if (!task?.audit_segments) return;
    const active = task.audit_segments.find(
      (s) => currentTime >= s.start && currentTime <= s.end
    );
    if (active && active.id !== activeSegmentId) {
      setActiveSegmentId(active.id);
    }
  }, [currentTime, task?.audit_segments]);

  const handleToggleSegment = async (segId: string) => {
    if (!task) return;
    try {
      await toggleSegment(taskId, segId);
      await loadTask();
    } catch {
      toast.error("操作失败");
    }
  };

  const handleSegmentClick = (seg: Segment) => {
    setActiveSegmentId(seg.id);
    setCurrentTime(seg.start);
  };

  const handleASR = async () => {
    try {
      await triggerASR(taskId);
      toast.success("ASR 识别已启动");
      setConsoleExpanded(true);
      loadTask();
    } catch (e: any) {
      toast.error(e.message);
    }
  };

  const handleAudit = async () => {
    try {
      await triggerAudit(taskId, { claude_api_key: claudeKey || undefined, style_mode: styleMode });
      toast.success("语义审计已启动");
      setConsoleExpanded(true);
      loadTask();
    } catch (e: any) {
      toast.error(e.message);
    }
  };

  const handleExportFFmpeg = async () => {
    try {
      await exportFFmpeg(taskId, `${task?.name}_output`);
      toast.success("FFmpeg 导出已启动");
      setConsoleExpanded(true);
    } catch (e: any) {
      toast.error(e.message);
    }
  };

  const handleExportJianying = async () => {
    try {
      await exportJianying(taskId, `${task?.name}_draft`);
      toast.success("剪映草稿生成已启动");
      setConsoleExpanded(true);
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
  const displaySegments = showOnlyKept
    ? segments.filter((s) => s.action === "keep")
    : segments;

  const isProcessing = ["asr_running", "audit_running", "export_running"].includes(task.status);

  return (
    <div className="h-full flex flex-col overflow-hidden">
      {/* Top bar */}
      <div className="h-11 shrink-0 border-b border-border flex items-center px-4 gap-3">
        <button
          onClick={() => navigate("/")}
          className="flex items-center gap-1.5 text-muted-foreground hover:text-foreground transition-colors text-sm"
        >
          <ArrowLeft className="w-4 h-4" />
          <span>返回</span>
        </button>
        <div className="w-px h-4 bg-border" />
        <h2 className="text-sm font-medium text-foreground truncate max-w-xs">{task.name}</h2>
        <span className={cn("status-badge text-[10px]", STATUS_COLORS[task.status])}>
          {isProcessing && <Loader2 className="w-2.5 h-2.5 animate-spin" />}
          {STATUS_LABELS[task.status]}
        </span>

        {/* Action buttons */}
        <div className="ml-auto flex items-center gap-2">
          {/* ASR Button */}
          {(task.status === "pending" || task.status === "error") && task.video_path && (
            <Button
              size="sm"
              variant="outline"
              className="border-border text-foreground hover:bg-secondary gap-1.5 text-xs h-7"
              onClick={handleASR}
              disabled={isProcessing}
            >
              <Mic className="w-3.5 h-3.5" />
              运行 ASR
            </Button>
          )}

          {/* Audit Button */}
          {(task.status === "asr_done" || task.status === "review") && (
            <Button
              size="sm"
              className="bg-primary text-primary-foreground hover:bg-primary/90 gap-1.5 text-xs h-7"
              onClick={handleAudit}
              disabled={isProcessing}
            >
              {isProcessing ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
              ) : (
                <Zap className="w-3.5 h-3.5" />
              )}
              Claude 审计
            </Button>
          )}

          {/* Show kept only toggle */}
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

      {/* Main 3-panel layout */}
      <div className="flex-1 overflow-hidden flex">
        {/* Left: Video Player (35%) */}
        <div className="w-[35%] shrink-0 border-r border-border p-3 flex flex-col gap-3">
          <div className="flex-1 min-h-0">
            {task.video_path ? (
              <VideoPlayer
                taskId={taskId}
                currentTime={currentTime}
                onTimeUpdate={setCurrentTime}
              />
            ) : (
              <div className="h-full bg-secondary rounded-xl flex flex-col items-center justify-center gap-3">
                <div className="w-12 h-12 rounded-xl bg-card flex items-center justify-center">
                  <Play className="w-6 h-6 text-muted-foreground/40 ml-0.5" />
                </div>
                <p className="text-xs text-muted-foreground">暂无视频</p>
              </div>
            )}
          </div>

          {/* Export Panel */}
          {(task.status === "review" || task.status === "done" || task.status === "asr_done") && (
            <ExportPanel
              task={task}
              onExportFFmpeg={handleExportFFmpeg}
              onExportJianying={handleExportJianying}
            />
          )}
        </div>

        {/* Center: Subtitle Audit Stream (40%) */}
        <div className="flex-1 min-w-0 flex flex-col border-r border-border">
          {/* Header */}
          <div className="h-9 shrink-0 border-b border-border flex items-center px-3 gap-3">
            <span className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
              字幕审计流
            </span>
            {segments.length > 0 && (
              <div className="flex items-center gap-2 ml-auto text-[11px]">
                <span className="text-green-400 font-mono">{task.segments_kept} ✓</span>
                <span className="text-red-400 font-mono">{task.segments_deleted} ✗</span>
                {task.edited_duration && (
                  <span className="timestamp">{formatDuration(task.edited_duration)}</span>
                )}
              </div>
            )}
          </div>

          {/* Segment list */}
          <div ref={segmentListRef} className="flex-1 overflow-y-auto p-3">
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
                    <Button size="sm" onClick={handleASR}
                      className="bg-primary text-primary-foreground hover:bg-primary/90 text-xs gap-1.5">
                      <Mic className="w-3.5 h-3.5" />运行 ASR
                    </Button>
                  </>
                )}
                {(task.status === "asr_running" || task.status === "audit_running") && (
                  <>
                    <Loader2 className="w-8 h-8 animate-spin text-primary" />
                    <p className="text-xs text-muted-foreground">
                      {task.status === "asr_running" ? "ASR 识别中..." : "Claude 审计中..."}
                    </p>
                  </>
                )}
              </div>
            ) : (
              displaySegments.map((seg) => (
                <SegmentItem
                  key={seg.id}
                  segment={seg}
                  isActive={seg.id === activeSegmentId}
                  onClick={() => handleSegmentClick(seg)}
                  onToggle={(e) => { e.stopPropagation(); handleToggleSegment(seg.id); }}
                />
              ))
            )}
          </div>
        </div>

        {/* Right: Speaker Panel + Scenario Info (25%) */}
        <div className="w-56 shrink-0 overflow-hidden flex flex-col">
          {/* Scenario Info Banner */}
          <ScenarioInfoPanel
            task={task}
            styleMode={styleMode}
            onStyleModeChange={setStyleMode}
            claudeKey={claudeKey}
            onClaudeKeyChange={setClaudeKey}
          />
          <div className="flex-1 overflow-hidden">
            <SpeakerPanel task={task} />
          </div>
        </div>
      </div>

      {/* Bottom: Console */}
      <ConsoleLog
        entries={consoleEntries}
        expanded={consoleExpanded}
        onToggle={() => setConsoleExpanded(!consoleExpanded)}
      />
    </div>
  );
}

// Need this import for Upload icon in empty state
import { Upload } from "lucide-react";
