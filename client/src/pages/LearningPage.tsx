/**
 * GoldenClip 学习模块页面
 * 上传原视频和人工剪辑视频，ASR 提取字幕，Claude 分析剪辑思路，生成版本化提示词
 */

import { useState, useEffect, useCallback, useRef, memo } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import { toast } from "sonner";
import {
  GraduationCap,
  Plus,
  Film,
  Scissors,
  Loader2,
  CheckCircle2,
  AlertCircle,
  Trash2,
  Clock,
  Brain,
  FileText,
  Save,
  Mic,
  Users,
  Flame,
  Eye,
  History,
  ChevronLeft,
  ArrowRight,
  Download,
  Upload,
  Copy,
  Hand,
} from "lucide-react";
import { Textarea } from "@/components/ui/textarea";
import {
  type LearningTask,
  type LearningTaskStatus,
  type ManualPromptPack,
  fetchLearningTasks,
  fetchLearningTask,
  createLearningTask,
  deleteLearningTask,
  uploadOriginalVideo,
  uploadEditedVideo,
  triggerLearningASR,
  triggerLearningAnalysis,
  saveLearningPrompt,
  createLogWebSocket,
  formatDuration,
  TASK_TYPE_LABELS,
  LEARNING_STATUS_LABELS,
  getPrompt,
  getLearningManualPromptStep1,
  submitLearningManualStep1,
  getLearningManualPromptStep2,
  submitLearningManualStep2,
} from "@/lib/api";
import { useAppConfig } from "@/hooks/useAppConfig";
import { ModelPickerPopover } from "@/components/ModelPickerPopover";
import { cn } from "@/lib/utils";

type ScenarioId = "monologue_clean" | "interview_compress" | "highlight_reel";

const SCENARIOS: {
  id: ScenarioId;
  icon: React.ReactNode;
  label: string;
  desc: string;
  tag: string;
  color: string;
  border: string;
  bg: string;
  gradient: string;
  rules: { code: string; name: string }[];
}[] = [
  {
    id: "monologue_clean",
    icon: <Mic className="w-6 h-6" />,
    label: "口播精修",
    tag: "Monologue Clean",
    desc: "博主口播、知识分享、产品讲解",
    color: "text-amber-400",
    border: "border-amber-500/40",
    bg: "bg-amber-500/5",
    gradient: "from-amber-500/20 to-amber-500/5",
    rules: [
      { code: "P1", name: "重说识别" },
      { code: "P2", name: "结巴切除" },
      { code: "P3", name: "语气词分级" },
      { code: "P7", name: "开头钩子" },
    ],
  },
  {
    id: "interview_compress",
    icon: <Users className="w-6 h-6" />,
    label: "直播访谈压缩",
    tag: "Interview Compress",
    desc: "主播×嘉宾对谈、播客、深度访谈",
    color: "text-blue-400",
    border: "border-blue-500/40",
    bg: "bg-blue-500/5",
    gradient: "from-blue-500/20 to-blue-500/5",
    rules: [
      { code: "I1", name: "问答闭环" },
      { code: "I2", name: "跨段去重" },
      { code: "I3", name: "精华保护" },
      { code: "I6", name: "情绪弧线" },
    ],
  },
  {
    id: "highlight_reel",
    icon: <Flame className="w-6 h-6" />,
    label: "精彩集锦",
    tag: "Highlight Reel",
    desc: "抖音/B站/YouTube Shorts 爆款剪辑",
    color: "text-orange-400",
    border: "border-orange-500/40",
    bg: "bg-orange-500/5",
    gradient: "from-orange-500/20 to-orange-500/5",
    rules: [
      { code: "H1", name: "高能识别" },
      { code: "H2", name: "前3秒钩子" },
      { code: "H3", name: "节奏加速" },
      { code: "H5", name: "废话零容忍" },
    ],
  },
];

// ============================================================
// 进行中动效：跳动省略号
// ============================================================
const AnimatedDots = memo(function AnimatedDots() {
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
});

function StatusBadge({ status }: { status: LearningTaskStatus }) {
  const label = LEARNING_STATUS_LABELS[status];
  const isRunning = status.includes("asr_") && status !== "asr_done" || status === "analyzing";
  const colorMap: Record<string, string> = {
    pending: "text-slate-400 bg-slate-800",
    asr_original: "text-blue-300 bg-blue-900/50",
    asr_edited: "text-cyan-300 bg-cyan-900/50",
    asr_done: "text-emerald-300 bg-emerald-900/50",
    analyzing: "text-amber-300 bg-amber-900/50",
    done: "text-green-300 bg-green-900/50",
    error: "text-red-300 bg-red-900/50",
  };
  return (
    <span className={cn("inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium", colorMap[status] || "text-slate-400 bg-slate-800")}>
      {isRunning && <Loader2 className="w-2.5 h-2.5 animate-spin" />}
      {status === "done" && <CheckCircle2 className="w-2.5 h-2.5" />}
      {status === "error" && <AlertCircle className="w-2.5 h-2.5" />}
      {label}
    </span>
  );
}

// ============================================================
// 学习任务卡片
// ============================================================
function LearningTaskCard({
  task,
  onSelect,
  onDelete,
}: {
  task: LearningTask;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
}) {
  return (
    <div
      className="group relative bg-card border border-border rounded-xl p-4 hover:-translate-y-0.5 hover:shadow-lg hover:shadow-black/30 transition-all duration-200 cursor-pointer"
      onClick={() => onSelect(task.id)}
    >
      <div className="flex items-start justify-between">
        <div className="flex-1 min-w-0">
          <h3 className="text-sm font-medium text-foreground truncate">{task.name}</h3>
          <p className="text-xs text-muted-foreground mt-0.5">
            {TASK_TYPE_LABELS[task.task_type]} · {new Date(task.created_at).toLocaleDateString("zh-CN")}
          </p>
        </div>
        <StatusBadge status={task.status} />
      </div>

      <div className="flex items-center gap-4 mt-3 text-xs text-muted-foreground">
        {task.original_video_duration && (
          <span className="flex items-center gap-1">
            <Film className="w-3 h-3" />
            原 {formatDuration(task.original_video_duration)}
          </span>
        )}
        {task.edited_video_duration && (
          <span className="flex items-center gap-1">
            <Scissors className="w-3 h-3" />
            剪 {formatDuration(task.edited_video_duration)}
          </span>
        )}
        {task.prompt_version && (
          <span className="flex items-center gap-1">
            <FileText className="w-3 h-3" />
            v{task.prompt_version}
          </span>
        )}
      </div>

      <button
        onClick={(e) => { e.stopPropagation(); onDelete(task.id); }}
        className="absolute top-3 right-3 opacity-0 group-hover:opacity-100 p-1 rounded hover:bg-destructive/20 text-muted-foreground hover:text-destructive transition-all"
      >
        <Trash2 className="w-3.5 h-3.5" />
      </button>
    </div>
  );
}

// ============================================================
// 视频上传区域
// ============================================================
function VideoUploadZone({
  label,
  icon,
  file,
  filename,
  duration,
  uploading,
  progress,
  onFileSelect,
}: {
  label: string;
  icon: React.ReactNode;
  file: File | null;
  filename?: string;
  duration?: number;
  uploading: boolean;
  progress: number;
  onFileSelect: (file: File) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [isDragging, setIsDragging] = useState(false);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    const dropped = e.dataTransfer.files[0];
    if (dropped && /\.(mp4|mov|avi|mkv|webm)$/i.test(dropped.name)) {
      onFileSelect(dropped);
    } else {
      toast.error("请上传 MP4/MOV/AVI/MKV/WebM 格式视频");
    }
  }, [onFileSelect]);

  const displayName = file?.name || filename;

  return (
    <div
      className={cn(
        "relative flex flex-col items-center justify-center rounded-xl border-2 border-dashed p-6 transition-all duration-200 min-h-[160px]",
        isDragging ? "border-primary bg-primary/5" : "border-border hover:border-muted-foreground/50",
        displayName ? "border-solid border-primary/30 bg-primary/5" : ""
      )}
      onDragOver={(e) => { e.preventDefault(); setIsDragging(true); }}
      onDragLeave={() => setIsDragging(false)}
      onDrop={handleDrop}
      onClick={() => !uploading && inputRef.current?.click()}
    >
      <input
        ref={inputRef}
        type="file"
        accept=".mp4,.mov,.avi,.mkv,.webm"
        className="hidden"
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) onFileSelect(f);
        }}
      />

      {uploading ? (
        <div className="flex flex-col items-center gap-2">
          <Loader2 className="w-6 h-6 text-primary animate-spin" />
          <p className="text-xs text-muted-foreground">{Math.round(progress * 100)}%</p>
          <div className="w-32 h-1 rounded-full bg-secondary overflow-hidden">
            <div className="h-full bg-primary transition-all" style={{ width: `${progress * 100}%` }} />
          </div>
        </div>
      ) : displayName ? (
        <div className="flex flex-col items-center gap-2">
          <CheckCircle2 className="w-6 h-6 text-green-400" />
          <p className="text-xs text-foreground font-medium truncate max-w-[200px]">{displayName}</p>
          {duration != null && (
            <p className="text-xs text-muted-foreground flex items-center gap-1">
              <Clock className="w-3 h-3" />
              {formatDuration(duration)}
            </p>
          )}
          <p className="text-xs text-muted-foreground/50">点击重新选择</p>
        </div>
      ) : (
        <div className="flex flex-col items-center gap-2 cursor-pointer">
          {icon}
          <p className="text-sm text-foreground font-medium">{label}</p>
          <p className="text-xs text-muted-foreground">拖拽或点击上传</p>
        </div>
      )}
    </div>
  );
}

// ============================================================
// 日志面板（带最后一条进行中动效）
// ============================================================
function LogPanel({ logs }: { logs: string[] }) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (bottomRef.current) {
      bottomRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [logs]);

  const lastLine = logs.length > 0 ? logs[logs.length - 1] : null;
  const lastIsInProgress =
    lastLine !== null &&
    lastLine.endsWith("...");

  const sourceColorMap: Record<string, string> = {
    asr: "text-cyan-400",
    claude: "text-purple-400",
    ffmpeg: "text-orange-400",
    system: "text-slate-400",
  };

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <History className="w-3 h-3" />
        实时日志
        {lastIsInProgress && (
          <span className="flex items-center gap-1 text-primary">
            <span className="w-1.5 h-1.5 rounded-full bg-primary animate-pulse" />
            进行中
          </span>
        )}
      </div>
      <div className="rounded-lg border border-border bg-card/60 p-3 max-h-40 overflow-y-auto font-mono text-xs text-muted-foreground space-y-0.5">
        {logs.map((line, i) => {
          const isLast = i === logs.length - 1;
          const showAnim = isLast && lastIsInProgress;
          // 去掉末尾的 ... 由 AnimatedDots 接管
          const displayLine = showAnim ? line.slice(0, -3) : line;

          // 解析 [source] 前缀以上色
          const sourceMatch = displayLine.match(/^\[(\w+)\]/);
          const source = sourceMatch ? sourceMatch[1] : null;
          const sourceColor = source ? (sourceColorMap[source] || "text-slate-400") : "text-slate-400";
          const rest = source ? displayLine.slice(sourceMatch![0].length) : displayLine;

          return (
            <div key={i} className="leading-relaxed flex items-baseline gap-1.5">
              {source && (
                <span className={cn("shrink-0", sourceColor)}>[{source}]</span>
              )}
              <span>
                {rest}
                {showAnim && <AnimatedDots />}
              </span>
            </div>
          );
        })}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}

// ============================================================
// 手动模式弹窗（离线 AI 分析，两步流程）
// ============================================================
function ManualAnalysisDialog({
  open,
  onOpenChange,
  taskId,
  onComplete,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  taskId: string;
  onComplete: () => void;
}) {
  const [step, setStep] = useState<1 | 2>(1);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [promptPack, setPromptPack] = useState<ManualPromptPack | null>(null);
  const [aiOutput, setAiOutput] = useState("");
  const [promptTab, setPromptTab] = useState<"combined" | "system" | "user">("combined");

  useEffect(() => {
    if (!open) {
      setStep(1);
      setPromptPack(null);
      setAiOutput("");
      setPromptTab("combined");
      return;
    }
    loadStep1Prompt();
  }, [open, taskId]);

  const loadStep1Prompt = async () => {
    setLoading(true);
    try {
      const pack = await getLearningManualPromptStep1(taskId);
      setPromptPack(pack);
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setLoading(false);
    }
  };

  const loadStep2Prompt = async () => {
    setLoading(true);
    try {
      const pack = await getLearningManualPromptStep2(taskId);
      setPromptPack(pack);
      setAiOutput("");
      setPromptTab("combined");
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setLoading(false);
    }
  };

  const handleCopy = async (text: string) => {
    await navigator.clipboard.writeText(text);
    toast.success("已复制到剪贴板");
  };

  const handleDownload = (text: string, filename: string) => {
    const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
    toast.success(`已下载 ${filename}`);
  };

  const handleSubmitStep1 = async () => {
    if (!aiOutput.trim()) { toast.error("请粘贴 AI 输出的分析报告"); return; }
    setSubmitting(true);
    try {
      await submitLearningManualStep1(taskId, aiOutput.trim());
      toast.success("第一步分析报告已保存，加载第二步...");
      setStep(2);
      await loadStep2Prompt();
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setSubmitting(false);
    }
  };

  const handleSubmitStep2 = async () => {
    if (!aiOutput.trim()) { toast.error("请粘贴 AI 输出的规则变更 JSON"); return; }
    setSubmitting(true);
    try {
      await submitLearningManualStep2(taskId, aiOutput.trim());
      toast.success("新提示词已生成！");
      onOpenChange(false);
      onComplete();
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setSubmitting(false);
    }
  };

  const displayText = promptTab === "combined"
    ? promptPack?.combined
    : promptTab === "system"
      ? promptPack?.system_prompt
      : promptPack?.user_prompt;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="bg-card border-border max-w-4xl max-h-[90vh] flex flex-col">
        <DialogHeader>
          <DialogTitle className="text-foreground flex items-center gap-2">
            <Hand className="w-4 h-4" />
            手动模式 · 第 {step} 步 / 共 2 步
            <span className="text-xs text-muted-foreground font-normal ml-2">
              {step === 1 ? "剪辑思路分析" : "提示词规则改写"}
            </span>
          </DialogTitle>
          <DialogDescription className="text-muted-foreground text-sm">
            {step === 1
              ? "复制以下提示词到 Cursor / Claude 等工具运行，将 AI 输出的分析报告粘贴回来"
              : "复制以下提示词运行 AI，将输出的规则变更 JSON 粘贴回来"}
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
                    onClick={() => setPromptTab(tab)}
                    className={cn(
                      "px-2.5 py-1 rounded text-xs transition-colors",
                      promptTab === tab
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
                  onClick={() => displayText && handleCopy(displayText)}
                  disabled={!displayText}
                >
                  <Copy className="w-3 h-3" />
                  复制
                </Button>
                <Button
                  size="sm" variant="ghost" className="h-7 text-xs gap-1"
                  onClick={() => displayText && handleDownload(
                    displayText,
                    `learning_step${step}_${promptTab}.txt`
                  )}
                  disabled={!displayText}
                >
                  <Download className="w-3 h-3" />
                  下载
                </Button>
              </div>
            </div>
            <div className="rounded-lg border border-border bg-card/60 p-3 h-48 overflow-y-auto">
              {loading ? (
                <div className="flex items-center justify-center h-full">
                  <Loader2 className="w-4 h-4 animate-spin text-muted-foreground" />
                </div>
              ) : (
                <pre className="text-xs text-muted-foreground whitespace-pre-wrap font-mono leading-relaxed">
                  {displayText || "加载中..."}
                </pre>
              )}
            </div>
          </div>

          {/* AI 输出粘贴区 */}
          <div className="space-y-2">
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <Upload className="w-3 h-3" />
              粘贴 AI 输出结果
              <span className="text-muted-foreground/50">
                {step === 1 ? "（分析报告，Markdown 格式）" : "（规则变更 JSON）"}
              </span>
            </div>
            <Textarea
              value={aiOutput}
              onChange={(e) => setAiOutput(e.target.value)}
              placeholder={step === 1
                ? "在此粘贴 AI 输出的剪辑思路分析报告..."
                : "在此粘贴 AI 输出的规则变更 JSON..."
              }
              className="h-40 bg-secondary border-border text-foreground font-mono text-xs resize-none"
            />
          </div>
        </div>

        <DialogFooter className="flex gap-2 mt-2">
          <Button variant="outline" size="sm" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button
            size="sm"
            onClick={step === 1 ? handleSubmitStep1 : handleSubmitStep2}
            disabled={submitting || !aiOutput.trim()}
          >
            {submitting && <Loader2 className="w-3 h-3 mr-1 animate-spin" />}
            {step === 1 ? "提交分析报告 → 第2步" : "提交并生成新提示词"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ============================================================
// 主页面
// ============================================================
export default function LearningPage() {
  const { config, updateConfig } = useAppConfig();

  // 场景筛选状态（null = 场景选择页）
  const [activeScenario, setActiveScenario] = useState<ScenarioId | null>(null);

  const [tasks, setTasks] = useState<LearningTask[]>([]);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [selectedTask, setSelectedTask] = useState<LearningTask | null>(null);
  const [showNewDialog, setShowNewDialog] = useState(false);
  const [logs, setLogs] = useState<string[]>([]);

  // 新建任务表单
  const [newName, setNewName] = useState("");
  // newTaskType 跟随 activeScenario
  const newTaskType = activeScenario || "monologue_clean";

  // 上传状态（与 selectedTaskId 绑定，切换任务时重置）
  const [originalFile, setOriginalFile] = useState<File | null>(null);
  const [editedFile, setEditedFile] = useState<File | null>(null);
  const [uploadingOriginal, setUploadingOriginal] = useState(false);
  const [uploadingEdited, setUploadingEdited] = useState(false);
  const [originalProgress, setOriginalProgress] = useState(0);
  const [editedProgress, setEditedProgress] = useState(0);

  // 分析状态
  const [saving, setSaving] = useState(false);
  const [showAnalysis, setShowAnalysis] = useState(false);
  const [showNewPrompt, setShowNewPrompt] = useState(false);

  // 手动模式弹窗
  const [showManualDialog, setShowManualDialog] = useState(false);

  // 删除确认弹窗
  const [deleteConfirmId, setDeleteConfirmId] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);

  // 老提示词（当前使用中的提示词内容）
  const [currentPromptContent, setCurrentPromptContent] = useState<string | null>(null);
  const [loadingCurrentPrompt, setLoadingCurrentPrompt] = useState(false);

  // WebSocket 引用
  const wsRef = useRef<WebSocket | null>(null);

  // 加载任务列表
  const loadTasks = useCallback(async () => {
    try {
      const list = await fetchLearningTasks();
      setTasks(list);
    } catch {
      // 静默失败
    }
  }, []);

  useEffect(() => { loadTasks(); }, [loadTasks]);

  // 选中任务后加载详情 + 轮询
  useEffect(() => {
    if (!selectedTaskId) {
      setSelectedTask(null);
      return;
    }
    let active = true;
    const poll = async () => {
      try {
        const t = await fetchLearningTask(selectedTaskId);
        if (active) setSelectedTask(t);
      } catch { /* ignore */ }
    };
    poll();
    const timer = setInterval(poll, 3000);
    return () => { active = false; clearInterval(timer); };
  }, [selectedTaskId]);

  // WebSocket 日志
  useEffect(() => {
    if (!selectedTaskId) return;
    // #region agent log
    fetch('http://127.0.0.1:7496/ingest/dd829ab7-cec4-4fd5-9bf8-f4d54528a53c',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'e5a0fe'},body:JSON.stringify({sessionId:'e5a0fe',location:'LearningPage.tsx:wsEffect',message:'ws_connect_attempt',data:{channel:`learn_${selectedTaskId}`},timestamp:Date.now(),hypothesisId:'CD'})}).catch(()=>{});
    // #endregion
    const ws = createLogWebSocket(`learn_${selectedTaskId}`, (msg) => {
      // #region agent log
      if (msg.type === "log") fetch('http://127.0.0.1:7496/ingest/dd829ab7-cec4-4fd5-9bf8-f4d54528a53c',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'e5a0fe'},body:JSON.stringify({sessionId:'e5a0fe',location:'LearningPage.tsx:wsMsg',message:'ws_message_received',data:{type:msg.type,src:msg.source,msg:msg.message?.slice(0,80)},timestamp:Date.now(),hypothesisId:'CD'})}).catch(()=>{});
      // #endregion
      if (msg.type === "log" && msg.message) {
        setLogs((prev) => [...prev.slice(-100), `[${msg.source}] ${msg.message}`]);
      }
    });
    wsRef.current = ws;
    return () => { ws.close(); wsRef.current = null; };
  }, [selectedTaskId]);

  // 过滤当前场景的任务列表
  const filteredTasks = activeScenario
    ? tasks.filter((t) => t.task_type === activeScenario)
    : [];

  // 进入场景后重置已选任务
  const handleSelectScenario = (id: ScenarioId) => {
    setActiveScenario(id);
    setSelectedTaskId(null);
    setSelectedTask(null);
    setLogs([]);
  };

  // 创建新学习任务
  const handleCreate = async () => {
    if (!newName.trim()) { toast.error("请输入任务名称"); return; }
    try {
      const task = await createLearningTask({ name: newName.trim(), task_type: newTaskType });
      toast.success("学习任务创建成功");
      setShowNewDialog(false);
      setNewName("");
      await loadTasks();
      setSelectedTaskId(task.id);
      setOriginalFile(null);
      setEditedFile(null);
      setLogs([]);
    } catch (e: any) {
      toast.error(`创建失败: ${e.message}`);
    }
  };

  // 上传原视频
  const handleUploadOriginal = async (file: File) => {
    if (!selectedTask) return;
    setOriginalFile(file);
    setUploadingOriginal(true);
    try {
      await uploadOriginalVideo(selectedTask.id, file, setOriginalProgress);
      toast.success("原视频上传成功");
      setSelectedTask(await fetchLearningTask(selectedTask.id));
    } catch (e: any) {
      toast.error(`上传失败: ${e.message}`);
    } finally {
      setUploadingOriginal(false);
      setOriginalProgress(0);
    }
  };

  // 上传剪辑视频
  const handleUploadEdited = async (file: File) => {
    if (!selectedTask) return;
    setEditedFile(file);
    setUploadingEdited(true);
    try {
      await uploadEditedVideo(selectedTask.id, file, setEditedProgress);
      toast.success("剪辑视频上传成功");
      setSelectedTask(await fetchLearningTask(selectedTask.id));
    } catch (e: any) {
      toast.error(`上传失败: ${e.message}`);
    } finally {
      setUploadingEdited(false);
      setEditedProgress(0);
    }
  };

  // 启动 ASR
  const handleStartASR = async () => {
    if (!selectedTask) return;
    try {
      await triggerLearningASR(selectedTask.id, "funasr");
      toast.success("双轨 ASR 已启动");
      setLogs([]);
    } catch (e: any) {
      toast.error(e.message);
    }
  };

  // 启动分析
  const handleStartAnalysis = async () => {
    if (!selectedTask) return;
    try {
      await triggerLearningAnalysis(selectedTask.id, {
        claude_api_key: config.claudeApiKey || undefined,
        claude_model: config.claudeModel,
      });
      toast.success("学习分析已启动");
      setLogs([]);
    } catch (e: any) {
      toast.error(e.message);
    }
  };

  // 保存新提示词
  const handleSavePrompt = async () => {
    if (!selectedTask?.new_prompt_content) return;
    setSaving(true);
    try {
      const result = await saveLearningPrompt(selectedTask.id);
      toast.success(`新提示词已保存: ${result.filename} (v${result.version})`);
      setSelectedTask(await fetchLearningTask(selectedTask.id));
      loadTasks();
    } catch (e: any) {
      toast.error(`保存失败: ${e.message}`);
    } finally {
      setSaving(false);
    }
  };

  // 请求删除：若任务正在运行则弹确认框，否则直接删除
  const handleDeleteRequest = (id: string) => {
    const task = tasks.find((t) => t.id === id);
    const runningStatuses: LearningTaskStatus[] = ["asr_original", "asr_edited", "analyzing"];
    if (task && runningStatuses.includes(task.status)) {
      setDeleteConfirmId(id);
    } else {
      handleDeleteConfirmed(id);
    }
  };

  // 执行实际删除
  const handleDeleteConfirmed = async (id: string) => {
    setDeleting(true);
    try {
      await deleteLearningTask(id);
      toast.success("已删除");
      if (selectedTaskId === id) {
        setSelectedTaskId(null);
        setSelectedTask(null);
        setLogs([]);
      }
      loadTasks();
    } catch {
      toast.error("删除失败");
    } finally {
      setDeleting(false);
      setDeleteConfirmId(null);
    }
  };

  // 当选中任务有新提示词时，加载当前（老）提示词内容
  useEffect(() => {
    if (!selectedTask?.new_prompt_content || !activeScenario) {
      setCurrentPromptContent(null);
      return;
    }
    setLoadingCurrentPrompt(true);
    getPrompt(activeScenario)
      .then((data) => setCurrentPromptContent(data.content))
      .catch(() => setCurrentPromptContent(null))
      .finally(() => setLoadingCurrentPrompt(false));
  }, [selectedTask?.new_prompt_content, activeScenario]);

  // 选中任务时重置上传文件状态
  const handleSelectTask = (id: string) => {
    setSelectedTaskId(id);
    setOriginalFile(null);
    setEditedFile(null);
    setLogs([]);
    setShowAnalysis(false);
    setShowNewPrompt(false);
    setCurrentPromptContent(null);
  };

  const t = selectedTask;
  const canASR = !!(t && t.original_video_path && t.edited_video_path && t.status === "pending");
  const canAnalyze = !!(t && t.status === "asr_done");
  const isRunning = !!(t && ["asr_original", "asr_edited", "analyzing"].includes(t.status));
  const isDone = !!(t && t.status === "done");

  const currentScenario = SCENARIOS.find((s) => s.id === activeScenario);

  // ── 场景选择页 ──────────────────────────────────────────────
  if (!activeScenario) {
    return (
      <div className="h-full flex flex-col items-center justify-center p-8">
        <div className="text-center mb-10">
          <div className="inline-flex items-center justify-center w-12 h-12 rounded-2xl bg-primary/10 border border-primary/20 mb-4">
            <GraduationCap className="w-6 h-6 text-primary" />
          </div>
          <h1 className="text-xl font-semibold text-foreground">选择学习场景</h1>
          <p className="text-sm text-muted-foreground mt-1.5">
            上传原视频与人工剪辑对比，让 Claude 提炼编导的剪辑思路并更新提示词
          </p>
        </div>

        <div className="grid grid-cols-3 gap-5 w-full max-w-3xl">
          {SCENARIOS.map((s) => (
            <button
              key={s.id}
              onClick={() => handleSelectScenario(s.id)}
              className={cn(
                "group relative flex flex-col p-6 rounded-2xl border-2 text-left transition-all duration-200 hover:-translate-y-1 hover:shadow-xl hover:shadow-black/40",
                s.border, s.bg
              )}
            >
              {/* 背景光晕 */}
              <div className={cn("absolute inset-0 rounded-2xl bg-gradient-to-br opacity-0 group-hover:opacity-100 transition-opacity duration-300", s.gradient)} />

              <div className="relative">
                <span className={cn("mb-4 block", s.color)}>{s.icon}</span>
                <p className={cn("text-xs font-mono font-semibold mb-1", s.color)}>{s.tag}</p>
                <h3 className="text-base font-semibold text-foreground mb-1">{s.label}</h3>
                <p className="text-xs text-muted-foreground mb-4">{s.desc}</p>

                <div className="space-y-1">
                  {s.rules.map((r) => (
                    <div key={r.code} className="flex items-center gap-2">
                      <span className={cn("text-xs font-mono font-bold w-5", s.color)}>{r.code}</span>
                      <span className="text-xs text-muted-foreground">{r.name}</span>
                    </div>
                  ))}
                </div>

                <div className={cn("mt-5 flex items-center gap-1 text-xs font-medium transition-all duration-200", s.color)}>
                  查看学习记录
                  <ArrowRight className="w-3 h-3 group-hover:translate-x-1 transition-transform" />
                </div>
              </div>
            </button>
          ))}
        </div>
      </div>
    );
  }

  // ── 场景已选：左侧任务列表 + 右侧工作区 ─────────────────────
  return (
    <div className="h-full flex overflow-hidden">
      {/* 左侧：任务列表 */}
      <div className="w-72 shrink-0 border-r border-border flex flex-col">
        {/* 场景头部 */}
        <div className="p-3 border-b border-border">
          <button
            onClick={() => { setActiveScenario(null); setSelectedTaskId(null); setSelectedTask(null); }}
            className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors mb-3"
          >
            <ChevronLeft className="w-3 h-3" />
            全部场景
          </button>
          <div className="flex items-center justify-between">
            <div className={cn("flex items-center gap-2", currentScenario?.color)}>
              {currentScenario?.icon && (
                <span>{currentScenario.icon}</span>
              )}
              <h2 className="text-sm font-semibold text-foreground">{currentScenario?.label}</h2>
            </div>
            <Button
              size="sm"
              variant="outline"
              className="h-7 text-xs"
              onClick={() => { setShowNewDialog(true); setNewName(""); }}
            >
              <Plus className="w-3 h-3 mr-1" />
              新建
            </Button>
          </div>
        </div>

        {/* 任务列表 */}
        <div className="flex-1 overflow-y-auto p-3 space-y-2">
          {filteredTasks.length === 0 ? (
            <div className="text-center py-12 text-muted-foreground">
              <GraduationCap className="w-8 h-8 mx-auto mb-2 opacity-30" />
              <p className="text-xs">暂无 {currentScenario?.label} 学习记录</p>
              <button
                className="text-xs text-primary mt-3 hover:underline"
                onClick={() => { setShowNewDialog(true); setNewName(""); }}
              >
                + 新建第一个学习任务
              </button>
            </div>
          ) : (
            filteredTasks.map((task) => (
              <div
                key={task.id}
                className={cn(
                  "group relative rounded-xl border p-3 cursor-pointer transition-all duration-200 hover:-translate-y-0.5 hover:shadow-md hover:shadow-black/20",
                  selectedTaskId === task.id
                    ? `border-primary/50 bg-primary/5`
                    : "border-border bg-card hover:border-border/80"
                )}
                onClick={() => handleSelectTask(task.id)}
              >
                <div className="flex items-start justify-between gap-2">
                  <h3 className="text-xs font-medium text-foreground truncate leading-relaxed">{task.name}</h3>
                  <StatusBadge status={task.status} />
                </div>
                <div className="flex items-center gap-3 mt-2 text-xs text-muted-foreground">
                  <span>{new Date(task.created_at).toLocaleDateString("zh-CN")}</span>
                  {task.original_video_duration && (
                    <span className="flex items-center gap-0.5">
                      <Film className="w-2.5 h-2.5" />
                      {formatDuration(task.original_video_duration)}
                    </span>
                  )}
                  {task.prompt_version && (
                    <span className={cn("font-mono font-bold", currentScenario?.color)}>
                      v{task.prompt_version}
                    </span>
                  )}
                </div>
                <button
                  onClick={(e) => { e.stopPropagation(); handleDeleteRequest(task.id); }}
                  className="absolute top-2.5 right-2 opacity-0 group-hover:opacity-100 p-1 rounded hover:bg-destructive/20 text-muted-foreground hover:text-destructive transition-all"
                  title="删除任务"
                >
                  <Trash2 className="w-3 h-3" />
                </button>
              </div>
            ))
          )}
        </div>
      </div>

      {/* 右侧：工作区 */}
      <div className="flex-1 overflow-y-auto">
        {!t ? (
          <div className="h-full flex items-center justify-center text-muted-foreground">
            <div className="text-center">
              <div className={cn("inline-flex items-center justify-center w-14 h-14 rounded-2xl border mb-4", currentScenario?.border, currentScenario?.bg)}>
                <span className={currentScenario?.color}>{currentScenario?.icon}</span>
              </div>
              <p className="text-sm font-medium text-foreground/70 mb-1">{currentScenario?.label}</p>
              <p className="text-xs text-muted-foreground">从左侧选择学习记录，或点击「新建」开始</p>
            </div>
          </div>
        ) : (
          <div className="p-6 space-y-6 max-w-4xl mx-auto">
            {/* 任务头部 */}
            <div className="flex items-center justify-between">
              <div>
                <h1 className="text-base font-semibold text-foreground">{t.name}</h1>
                <p className="text-xs text-muted-foreground mt-0.5">
                  创建于 {new Date(t.created_at).toLocaleString("zh-CN")}
                </p>
              </div>
              <div className="flex items-center gap-2">
                <StatusBadge status={t.status} />
                <Button
                  size="sm"
                  variant="ghost"
                  className="h-7 px-2 text-muted-foreground hover:text-destructive hover:bg-destructive/10"
                  onClick={() => handleDeleteRequest(t.id)}
                  title="删除任务"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </Button>
              </div>
            </div>

            {/* 错误信息 */}
            {t.error_message && (
              <div className="rounded-lg border border-red-500/30 bg-red-500/5 p-3 flex items-start gap-2">
                <AlertCircle className="w-4 h-4 text-red-400 shrink-0 mt-0.5" />
                <p className="text-xs text-red-300">{t.error_message}</p>
              </div>
            )}

            {/* Step 1: 上传视频 */}
            <div className="space-y-3">
              <div className="flex items-center gap-2">
                <span className="w-6 h-6 rounded-full bg-primary/20 text-primary text-xs font-bold flex items-center justify-center">1</span>
                <h3 className="text-sm font-medium text-foreground">上传视频</h3>
              </div>
              <div className="grid grid-cols-2 gap-4">
                <VideoUploadZone
                  label="原视频"
                  icon={<Film className="w-8 h-8 text-muted-foreground/30" />}
                  file={originalFile}
                  filename={t.original_video_filename}
                  duration={t.original_video_duration}
                  uploading={uploadingOriginal}
                  progress={originalProgress}
                  onFileSelect={handleUploadOriginal}
                />
                <VideoUploadZone
                  label="人工剪辑视频"
                  icon={<Scissors className="w-8 h-8 text-muted-foreground/30" />}
                  file={editedFile}
                  filename={t.edited_video_filename}
                  duration={t.edited_video_duration}
                  uploading={uploadingEdited}
                  progress={editedProgress}
                  onFileSelect={handleUploadEdited}
                />
              </div>
            </div>

            {/* Step 2: ASR 提取 */}
            <div className="space-y-3">
              <div className="flex items-center gap-2">
                <span className={cn(
                  "w-6 h-6 rounded-full text-xs font-bold flex items-center justify-center",
                  canASR || t.original_asr_result ? "bg-primary/20 text-primary" : "bg-muted text-muted-foreground"
                )}>2</span>
                <h3 className="text-sm font-medium text-foreground">ASR 字幕提取</h3>
              </div>

              {t.original_asr_result && t.edited_asr_result ? (
                <div className="rounded-lg border border-green-500/30 bg-green-500/5 p-3 flex items-center gap-3">
                  <CheckCircle2 className="w-4 h-4 text-green-400" />
                  <div className="text-xs text-green-300">
                    双轨 ASR 完成 · 原视频 {t.original_asr_result.segments.length} 段
                    · 剪辑视频 {t.edited_asr_result.segments.length} 段
                  </div>
                </div>
              ) : (
                <Button
                  onClick={handleStartASR}
                  disabled={!canASR || isRunning}
                  size="sm"
                  className="text-xs"
                >
                  {(t.status === "asr_original" || t.status === "asr_edited") ? (
                    <><Loader2 className="w-3 h-3 mr-1 animate-spin" />ASR 进行中...</>
                  ) : (
                    <>开始双轨 ASR</>
                  )}
                </Button>
              )}
            </div>

            {/* Step 3: Claude 分析 */}
            <div className="space-y-3">
              <div className="flex items-center gap-2">
                <span className={cn(
                  "w-6 h-6 rounded-full text-xs font-bold flex items-center justify-center",
                  canAnalyze || isDone ? "bg-primary/20 text-primary" : "bg-muted text-muted-foreground"
                )}>3</span>
                <h3 className="text-sm font-medium text-foreground">AI 分析剪辑思路</h3>
              </div>

              {!isDone && (
                <div className="flex items-center gap-2">
                  <Button
                    onClick={handleStartAnalysis}
                    disabled={!canAnalyze || isRunning}
                    size="sm"
                    className="text-xs"
                  >
                    {t.status === "analyzing" ? (
                      <><Loader2 className="w-3 h-3 mr-1 animate-spin" />分析中...</>
                    ) : (
                      <><Brain className="w-3 h-3 mr-1" />分析</>
                    )}
                  </Button>
                  <ModelPickerPopover config={config} updateConfig={updateConfig} disabled={!canAnalyze || isRunning} />
                  <Button
                    onClick={() => setShowManualDialog(true)}
                    disabled={!canAnalyze || isRunning}
                    size="sm"
                    variant="outline"
                    className="text-xs"
                  >
                    <Hand className="w-3 h-3 mr-1" />
                    手动模式
                  </Button>
                </div>
              )}

              {t.analysis_result && (
                <div className="space-y-2">
                  <button
                    onClick={() => setShowAnalysis(!showAnalysis)}
                    className="flex items-center gap-1 text-xs text-primary hover:text-primary/80 transition-colors"
                  >
                    <Eye className="w-3 h-3" />
                    {showAnalysis ? "收起" : "查看"}分析报告
                  </button>
                  {showAnalysis && (
                    <div className="rounded-lg border border-border bg-card p-4 max-h-96 overflow-y-auto">
                      <div className="prose prose-invert prose-sm max-w-none text-xs leading-relaxed whitespace-pre-wrap">
                        {t.analysis_result}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>

            {/* Step 4: 新提示词（含老提示词对比） */}
            {t.new_prompt_content && (
              <div className="space-y-3">
                {/* 步骤标题行 */}
                <div className="flex items-center gap-2">
                  <span className="w-6 h-6 rounded-full bg-primary/20 text-primary text-xs font-bold flex items-center justify-center">4</span>
                  <h3 className="text-sm font-medium text-foreground">新提示词</h3>
                  {t.prompt_version && (
                    <span className="text-xs text-muted-foreground bg-muted px-2 py-0.5 rounded">
                      已保存 v{t.prompt_version}
                    </span>
                  )}
                </div>

                {/* 操作按钮行 */}
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => setShowNewPrompt(!showNewPrompt)}
                    className="flex items-center gap-1 text-xs text-primary hover:text-primary/80 transition-colors"
                  >
                    <FileText className="w-3 h-3" />
                    {showNewPrompt ? "收起新提示词" : "展开对比查看"}
                  </button>
                  {!t.prompt_version && (
                    <Button size="sm" className="text-xs h-6" onClick={handleSavePrompt} disabled={saving}>
                      {saving ? <Loader2 className="w-3 h-3 mr-1 animate-spin" /> : <Save className="w-3 h-3 mr-1" />}
                      保存新版本
                    </Button>
                  )}
                </div>

                {/* 新老提示词对比面板 */}
                {showNewPrompt && (
                  <div className="grid grid-cols-2 gap-3">
                    {/* 左侧：老提示词（当前使用中） */}
                    <div className="flex flex-col space-y-1.5">
                      <div className="flex items-center gap-1.5">
                        <span className="w-2 h-2 rounded-full bg-muted-foreground/50" />
                        <span className="text-xs font-medium text-muted-foreground">当前提示词（老版本）</span>
                        {loadingCurrentPrompt && (
                          <Loader2 className="w-3 h-3 animate-spin text-muted-foreground" />
                        )}
                      </div>
                      <div className="rounded-lg border border-border bg-card/50 p-3 h-96 overflow-y-auto">
                        {currentPromptContent ? (
                          <pre className="text-xs text-muted-foreground whitespace-pre-wrap font-mono leading-relaxed">
                            {currentPromptContent}
                          </pre>
                        ) : loadingCurrentPrompt ? (
                          <div className="flex items-center justify-center h-full">
                            <Loader2 className="w-4 h-4 animate-spin text-muted-foreground" />
                          </div>
                        ) : (
                          <p className="text-xs text-muted-foreground/50 text-center mt-8">
                            暂无老版本内容
                          </p>
                        )}
                      </div>
                    </div>

                    {/* 右侧：新提示词（Claude 生成） */}
                    <div className="flex flex-col space-y-1.5">
                      <div className="flex items-center gap-1.5">
                        <span className="w-2 h-2 rounded-full bg-primary" />
                        <span className="text-xs font-medium text-primary">新提示词（Claude 生成）</span>
                      </div>
                      <div className="rounded-lg border border-primary/30 bg-primary/5 p-3 h-96 overflow-y-auto">
                        <pre className="text-xs text-foreground/90 whitespace-pre-wrap font-mono leading-relaxed">
                          {t.new_prompt_content}
                        </pre>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* 日志面板 */}
            {logs.length > 0 && (
              // #region agent log
              (() => { fetch('http://127.0.0.1:7496/ingest/dd829ab7-cec4-4fd5-9bf8-f4d54528a53c',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'e5a0fe'},body:JSON.stringify({sessionId:'e5a0fe',location:'LearningPage.tsx:logPanel',message:'log_panel_render',data:{logsCount:logs.length},timestamp:Date.now(),hypothesisId:'B'})}).catch(()=>{}); return null; })(),
              // #endregion
              <LogPanel logs={logs} />
            )}
          </div>
        )}
      </div>

      {/* 删除确认弹窗（任务运行中时使用） */}
      <Dialog open={deleteConfirmId !== null} onOpenChange={(open) => { if (!open) setDeleteConfirmId(null); }}>
        <DialogContent className="bg-card border-border max-w-sm">
          <DialogHeader>
            <DialogTitle className="text-foreground flex items-center gap-2">
              <Trash2 className="w-4 h-4 text-destructive" />
              确认删除学习任务
            </DialogTitle>
            <DialogDescription className="text-muted-foreground text-sm">
              该任务正在运行中（ASR 或 Claude 分析），删除后将立即终止所有相关后台任务，且无法恢复。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="flex gap-2 mt-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => setDeleteConfirmId(null)}
              disabled={deleting}
            >
              取消
            </Button>
            <Button
              variant="destructive"
              size="sm"
              onClick={() => deleteConfirmId && handleDeleteConfirmed(deleteConfirmId)}
              disabled={deleting}
            >
              {deleting ? <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" /> : <Trash2 className="w-3.5 h-3.5 mr-1" />}
              强制删除
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 新建任务弹窗 */}
      <Dialog open={showNewDialog} onOpenChange={setShowNewDialog}>
        <DialogContent className="bg-card border-border max-w-sm">
          <DialogHeader>
            <DialogTitle className="text-foreground flex items-center gap-2">
              <span className={currentScenario?.color}>{currentScenario?.icon}</span>
              新建 {currentScenario?.label} 学习任务
            </DialogTitle>
            <DialogDescription className="text-muted-foreground text-sm">
              通过对比原视频与人工剪辑，学习编导的剪辑思路
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4 mt-2">
            <div className="space-y-1.5">
              <Label className="text-xs text-muted-foreground uppercase tracking-wider">任务名称</Label>
              <Input
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleCreate()}
                placeholder={`例：学习${currentScenario?.label}_01`}
                className="bg-secondary border-border text-foreground placeholder:text-muted-foreground/50"
                autoFocus
              />
            </div>

            <Button className="w-full" onClick={handleCreate}>
              <Plus className="w-4 h-4 mr-1" />
              创建学习任务
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      {/* 手动模式弹窗 */}
      {selectedTaskId && (
        <ManualAnalysisDialog
          open={showManualDialog}
          onOpenChange={setShowManualDialog}
          taskId={selectedTaskId}
          onComplete={async () => {
            setSelectedTask(await fetchLearningTask(selectedTaskId));
            loadTasks();
          }}
        />
      )}
    </div>
  );
}
