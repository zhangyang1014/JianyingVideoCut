/**
 * GoldenClip Dashboard — Task Board
 * Design: 暗金剪辑台 · 编导美学
 * Features: Task cards, drag-drop upload, new task creation, status overview
 */

import { useState, useEffect, useCallback, useRef } from "react";
import { useLocation } from "wouter";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { toast } from "sonner";
import {
  Plus,
  Upload,
  Film,
  Clock,
  Trash2,
  ArrowRight,
  Loader2,
  Video,
  AlertCircle,
  CheckCircle2,
  Zap,
  FileVideo,
  Mic,
  Users,
  Flame,
  ChevronRight,
} from "lucide-react";
import {
  fetchTasks,
  createTask,
  uploadVideo,
  deleteTask,
  getThumbnailUrl,
  type Task,
  type TaskStatus,
  STATUS_LABELS,
  STATUS_COLORS,
  TASK_TYPE_LABELS,
  formatDuration,
} from "@/lib/api";
import { cn } from "@/lib/utils";

// ============================================================
// Status Badge
// ============================================================
function StatusBadge({ status }: { status: TaskStatus }) {
  const label = STATUS_LABELS[status];
  const colors = STATUS_COLORS[status];
  const isRunning = status.includes("running");

  return (
    <span className={cn("status-badge", colors)}>
      {isRunning && <Loader2 className="w-2.5 h-2.5 animate-spin" />}
      {status === "done" && <CheckCircle2 className="w-2.5 h-2.5" />}
      {status === "error" && <AlertCircle className="w-2.5 h-2.5" />}
      {label}
    </span>
  );
}

// ============================================================
// Task Card
// ============================================================
function TaskCard({ task, onDelete }: { task: Task; onDelete: (id: string) => void }) {
  const [, navigate] = useLocation();
  const [thumbError, setThumbError] = useState(false);

  const compressionRatio = task.original_duration && task.edited_duration
    ? Math.round((1 - task.edited_duration / task.original_duration) * 100)
    : null;

  return (
    <div
      className="group relative bg-card border border-border rounded-xl overflow-hidden hover:-translate-y-0.5 hover:shadow-lg hover:shadow-black/30 transition-all duration-200 cursor-pointer animate-fade-in-up"
      onClick={() => navigate(`/tasks/${task.id}`)}
    >
      {/* Thumbnail */}
      <div className="relative h-36 bg-secondary overflow-hidden">
        {!thumbError && task.video_path ? (
          <img
            src={getThumbnailUrl(task.id)}
            alt={task.name}
            className="w-full h-full object-cover"
            onError={() => setThumbError(true)}
          />
        ) : (
          <div className="w-full h-full flex items-center justify-center">
            <Film className="w-10 h-10 text-muted-foreground/30" />
          </div>
        )}

        {/* Duration overlay */}
        {task.video_duration && (
          <div className="absolute bottom-2 right-2 bg-black/70 rounded px-1.5 py-0.5">
            <span className="timestamp text-[10px]">{formatDuration(task.video_duration)}</span>
          </div>
        )}

        {/* Status overlay for running */}
        {(task.status === "asr_running" || task.status === "audit_running" || task.status === "export_running") && (
          <div className="absolute inset-0 bg-black/60 flex items-center justify-center">
            <Loader2 className="w-8 h-8 text-primary animate-spin" />
          </div>
        )}
      </div>

      {/* Content */}
      <div className="p-3">
        <div className="flex items-start justify-between gap-2 mb-2">
          <h3 className="text-sm font-medium text-foreground truncate flex-1">{task.name}</h3>
          <StatusBadge status={task.status} />
        </div>

        <div className="flex items-center gap-3 text-xs text-muted-foreground mb-2">
          <span className="flex items-center gap-1">
            <Video className="w-3 h-3" />
            {TASK_TYPE_LABELS[task.task_type] || task.task_type}
          </span>
          {task.video_filename && (
            <span className="truncate max-w-[100px]">{task.video_filename}</span>
          )}
        </div>

        {/* Stats row */}
        {(task.segments_kept > 0 || task.segments_deleted > 0) && (
          <div className="flex items-center gap-3 text-xs mt-1">
            <span className="text-green-400">✓ {task.segments_kept} 保留</span>
            <span className="text-red-400">✗ {task.segments_deleted} 删除</span>
            {compressionRatio !== null && compressionRatio > 0 && (
              <span className="text-primary font-mono">↓{compressionRatio}%</span>
            )}
          </div>
        )}

        {/* Created time */}
        <div className="flex items-center justify-between mt-2">
          <span className="text-xs text-muted-foreground/60">
            {new Date(task.created_at).toLocaleDateString("zh-CN", {
              month: "short", day: "numeric", hour: "2-digit", minute: "2-digit"
            })}
          </span>
          <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
            <Button
              variant="ghost"
              size="icon"
              className="w-6 h-6 text-muted-foreground hover:text-destructive"
              onClick={(e) => {
                e.stopPropagation();
                onDelete(task.id);
              }}
            >
              <Trash2 className="w-3 h-3" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              className="w-6 h-6 text-muted-foreground hover:text-primary"
            >
              <ArrowRight className="w-3 h-3" />
            </Button>
          </div>
        </div>
      </div>

      {/* Gold left border for active tasks */}
      {(task.status === "review" || task.status === "asr_done") && (
        <div className="absolute left-0 top-0 bottom-0 w-0.5 bg-primary" />
      )}
    </div>
  );
}

// ============================================================
// New Task Dialog
// ============================================================
function NewTaskDialog({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: (task: Task) => void;
}) {
  const [name, setName] = useState("");
  const [taskType, setTaskType] = useState("monologue_clean");
  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [isDragging, setIsDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Scenario definitions with rules preview
  const SCENARIOS = [
    {
      id: "monologue_clean",
      icon: <Mic className="w-4 h-4" />,
      label: "口播精修",
      tag: "Monologue Clean",
      desc: "博主口播、知识分享、产品讲解",
      target: "保留 60-80%，输出专业感",
      color: "text-amber-400",
      border: "border-amber-500/40",
      bg: "bg-amber-500/5",
      rules: [
        { code: "P1", name: "重说识别", desc: "开头5字相同→删前句" },
        { code: "P2", name: "结巴切除", desc: "词级精准切除，零容忍" },
        { code: "P3", name: "语气词分级", desc: "必删/酌情/保留三档" },
        { code: "P7", name: "开头钩子", desc: "前5秒强制保护" },
        { code: "P8", name: "结尾收束", desc: "干净利落，保留CTA" },
      ],
      styleNote: "quick_cut: 气口80ms，全删语气词 | immersive: 气口150ms，保留情绪词",
    },
    {
      id: "interview_compress",
      icon: <Users className="w-4 h-4" />,
      label: "访谈压缩",
      tag: "Interview Compress",
      desc: "主播×嘉宾对谈、播客、深度访谈",
      target: "嘉宾保留60-70%，主播保留20-30%",
      color: "text-blue-400",
      border: "border-blue-500/40",
      bg: "bg-blue-500/5",
      rules: [
        { code: "I1", name: "问答闭环", desc: "Q&A成对保护，不可破坏" },
        { code: "I2", name: "跨段去重", desc: "全文扫描，保留信息密度最高版" },
        { code: "I3", name: "精华保护", desc: "金句/洞见/情绪高峰无条件保留" },
        { code: "I4", name: "主播精简", desc: "废话铺垫删除，保留核心提问" },
        { code: "I6", name: "情绪弧线", desc: "保留开场/高峰/深度/升华四节点" },
      ],
      styleNote: "气口预留200ms/150ms | 停顿>1.5s保留（体现思考真实性）",
    },
    {
      id: "highlight_reel",
      icon: <Flame className="w-4 h-4" />,
      label: "精彩集锦",
      tag: "Highlight Reel",
      desc: "抖音/B站/YouTube Shorts 爆款剪辑",
      target: "保留 10-25%，1-3分钟高能输出",
      color: "text-orange-400",
      border: "border-orange-500/40",
      bg: "bg-orange-500/5",
      rules: [
        { code: "H1", name: "高能识别", desc: "数字冲击/反转/金句/情绪爆发" },
        { code: "H2", name: "前3秒钩子", desc: "最强内容强制冷开场" },
        { code: "H3", name: "节奏加速", desc: "删除所有>0.5s停顿" },
        { code: "H4", name: "情绪弧线", desc: "勾引→爆发→余韵→再勾引" },
        { code: "H5", name: "废话零容忍", desc: "语气词/过渡句全删" },
      ],
      styleNote: "气口80ms/50ms | 每片段5-30秒 | 建议重新排序",
    },
  ];

  const activeScenario = SCENARIOS.find(s => s.id === taskType);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    const dropped = e.dataTransfer.files[0];
    if (dropped && /\.(mp4|mov|avi|mkv|webm)$/i.test(dropped.name)) {
      setFile(dropped);
      if (!name) setName(dropped.name.replace(/\.[^/.]+$/, ""));
    } else {
      toast.error("请上传 MP4/MOV/AVI/MKV/WebM 格式视频");
    }
  }, [name]);

  const handleCreate = async () => {
    if (!name.trim()) { toast.error("请输入任务名称"); return; }
    if (!file) { toast.error("请选择视频文件"); return; }

    setUploading(true);
    try {
      const task = await createTask({ name: name.trim(), task_type: taskType });
      await uploadVideo(task.id, file, (p) => setUploadProgress(p));
      toast.success("任务创建成功");
      onCreated(task);
      onClose();
    } catch (e: any) {
      toast.error(`创建失败: ${e.message}`);
    } finally {
      setUploading(false);
      setUploadProgress(0);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onClose}>
      <DialogContent className="bg-card border-border max-w-md">
        <DialogHeader>
          <DialogTitle className="text-foreground flex items-center gap-2">
            <Plus className="w-4 h-4 text-primary" />
            新建剪辑任务
          </DialogTitle>
          <DialogDescription className="text-muted-foreground text-sm">
            上传视频，选择任务类型，开始智能剪辑
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 mt-2">
          {/* Task Name */}
          <div className="space-y-1.5">
            <Label className="text-xs text-muted-foreground uppercase tracking-wider">任务名称</Label>
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="例：产品发布访谈_20240312"
              className="bg-secondary border-border text-foreground placeholder:text-muted-foreground/50"
            />
          </div>

          {/* Task Type — Scenario Cards */}
          <div className="space-y-2">
            <Label className="text-xs text-muted-foreground uppercase tracking-wider">选择场景</Label>
            <div className="grid grid-cols-3 gap-2">
              {SCENARIOS.map((s) => (
                <button
                  key={s.id}
                  onClick={() => setTaskType(s.id)}
                  className={cn(
                    "relative flex flex-col items-center gap-1.5 p-3 rounded-lg border-2 text-center transition-all duration-200",
                    taskType === s.id
                      ? `${s.border} ${s.bg} ${s.color}`
                      : "border-border hover:border-border/80 text-muted-foreground hover:text-foreground"
                  )}
                >
                  <span className={taskType === s.id ? s.color : "text-muted-foreground"}>
                    {s.icon}
                  </span>
                  <span className="text-xs font-medium leading-tight">{s.label}</span>
                  {taskType === s.id && (
                    <div className={cn("absolute -top-1 -right-1 w-2 h-2 rounded-full", s.color.replace("text-", "bg-"))} />
                  )}
                </button>
              ))}
            </div>

            {/* Active Scenario Detail Card */}
            {activeScenario && (
              <div className={cn("rounded-lg border p-3 space-y-2.5 transition-all duration-300", activeScenario.border, activeScenario.bg)}>
                <div className="flex items-start justify-between">
                  <div>
                    <p className={cn("text-xs font-semibold font-mono", activeScenario.color)}>
                      {activeScenario.tag}
                    </p>
                    <p className="text-xs text-muted-foreground mt-0.5">{activeScenario.desc}</p>
                  </div>
                  <span className="text-xs text-muted-foreground/60 text-right leading-tight">
                    {activeScenario.target}
                  </span>
                </div>

                {/* Rules Preview */}
                <div className="space-y-1">
                  <p className="text-xs text-muted-foreground/60 uppercase tracking-wider">核心规则</p>
                  <div className="space-y-1">
                    {activeScenario.rules.map((rule) => (
                      <div key={rule.code} className="flex items-center gap-2">
                        <span className={cn("text-xs font-mono font-bold w-6 shrink-0", activeScenario.color)}>
                          {rule.code}
                        </span>
                        <span className="text-xs text-foreground/80 font-medium w-16 shrink-0">
                          {rule.name}
                        </span>
                        <span className="text-xs text-muted-foreground truncate">
                          {rule.desc}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>

                {/* Style Note */}
                <div className="border-t border-border/30 pt-2">
                  <p className="text-xs text-muted-foreground/50 leading-relaxed">
                    <span className="text-muted-foreground/70">气口策略：</span>
                    {activeScenario.styleNote}
                  </p>
                </div>
              </div>
            )}
          </div>

          {/* File Drop Zone */}
          <div className="space-y-1.5">
            <Label className="text-xs text-muted-foreground uppercase tracking-wider">视频文件</Label>
            <div
              className={cn(
                "border-2 border-dashed rounded-xl p-6 text-center transition-all duration-200 cursor-pointer",
                isDragging
                  ? "border-primary bg-accent"
                  : file
                    ? "border-green-500/50 bg-green-900/10"
                    : "border-border hover:border-primary/50 hover:bg-accent/50"
              )}
              onDragOver={(e) => { e.preventDefault(); setIsDragging(true); }}
              onDragLeave={() => setIsDragging(false)}
              onDrop={handleDrop}
              onClick={() => fileInputRef.current?.click()}
            >
              <input
                ref={fileInputRef}
                type="file"
                accept=".mp4,.mov,.avi,.mkv,.webm"
                className="hidden"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) {
                    setFile(f);
                    if (!name) setName(f.name.replace(/\.[^/.]+$/, ""));
                  }
                }}
              />
              {file ? (
                <div className="flex flex-col items-center gap-2">
                  <FileVideo className="w-8 h-8 text-green-400" />
                  <p className="text-sm text-foreground font-medium">{file.name}</p>
                  <p className="text-xs text-muted-foreground">
                    {(file.size / 1024 / 1024).toFixed(1)} MB
                  </p>
                </div>
              ) : (
                <div className="flex flex-col items-center gap-2">
                  <Upload className="w-8 h-8 text-muted-foreground/50" />
                  <p className="text-sm text-muted-foreground">
                    拖拽视频到此处，或点击选择
                  </p>
                  <p className="text-xs text-muted-foreground/50">
                    支持 MP4 · MOV · AVI · MKV · WebM
                  </p>
                </div>
              )}
            </div>
          </div>

          {/* Upload Progress */}
          {uploading && (
            <div className="space-y-1.5">
              <div className="flex justify-between text-xs text-muted-foreground">
                <span>上传中...</span>
                <span className="font-mono">{Math.round(uploadProgress * 100)}%</span>
              </div>
              <div className="h-1 bg-secondary rounded-full overflow-hidden">
                <div
                  className="h-full bg-primary transition-all duration-300"
                  style={{ width: `${uploadProgress * 100}%` }}
                />
              </div>
            </div>
          )}

          {/* Actions */}
          <div className="flex gap-2 pt-1">
            <Button
              variant="outline"
              className="flex-1 border-border text-muted-foreground hover:text-foreground"
              onClick={onClose}
              disabled={uploading}
            >
              取消
            </Button>
            <Button
              className="flex-1 bg-primary text-primary-foreground hover:bg-primary/90"
              onClick={handleCreate}
              disabled={uploading || !file || !name}
            >
              {uploading ? (
                <><Loader2 className="w-4 h-4 animate-spin mr-2" />上传中</>
              ) : (
                <><Zap className="w-4 h-4 mr-2" />创建任务</>
              )}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

// ============================================================
// Dashboard Main
// ============================================================
export default function Dashboard() {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [loading, setLoading] = useState(true);
  const [showNewTask, setShowNewTask] = useState(false);
  const [, navigate] = useLocation();

  const loadTasks = useCallback(async () => {
    try {
      const data = await fetchTasks();
      setTasks(data);
    } catch (e) {
      // Backend not running — show empty state
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadTasks();
    // Poll for status updates every 3s
    const interval = setInterval(loadTasks, 3000);
    return () => clearInterval(interval);
  }, [loadTasks]);

  const handleDelete = async (id: string) => {
    try {
      await deleteTask(id);
      setTasks((prev) => prev.filter((t) => t.id !== id));
      toast.success("任务已删除");
    } catch {
      toast.error("删除失败");
    }
  };

  const handleTaskCreated = (task: Task) => {
    setTasks((prev) => [task, ...prev]);
    navigate(`/tasks/${task.id}`);
  };

  // Stats
  const stats = {
    total: tasks.length,
    done: tasks.filter((t) => t.status === "done").length,
    inProgress: tasks.filter((t) =>
      ["asr_running", "audit_running", "export_running"].includes(t.status)
    ).length,
    review: tasks.filter((t) => t.status === "review").length,
  };

  return (
    <div className="h-full flex flex-col overflow-hidden">
      {/* Header */}
      <div className="px-6 py-4 border-b border-border shrink-0">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-lg font-semibold text-foreground">任务看板</h1>
            <p className="text-xs text-muted-foreground mt-0.5">
              编导思维驱动 · ASR + Claude 语义审计
            </p>
          </div>
          <Button
            onClick={() => setShowNewTask(true)}
            className="bg-primary text-primary-foreground hover:bg-primary/90 gap-2"
            size="sm"
          >
            <Plus className="w-4 h-4" />
            新建任务
          </Button>
        </div>

        {/* Stats Row */}
        <div className="flex items-center gap-6 mt-3">
          {[
            { label: "全部", value: stats.total, color: "text-foreground" },
            { label: "待 Review", value: stats.review, color: "text-yellow-400" },
            { label: "进行中", value: stats.inProgress, color: "text-blue-400" },
            { label: "已完成", value: stats.done, color: "text-green-400" },
          ].map((s) => (
            <div key={s.label} className="flex items-center gap-1.5">
              <span className={cn("text-lg font-mono font-semibold", s.color)}>{s.value}</span>
              <span className="text-xs text-muted-foreground">{s.label}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Task Grid */}
      <div className="flex-1 overflow-y-auto p-6">
        {loading ? (
          <div className="flex items-center justify-center h-40">
            <Loader2 className="w-6 h-6 animate-spin text-primary" />
          </div>
        ) : tasks.length === 0 ? (
          <EmptyState onNew={() => setShowNewTask(true)} />
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
            {tasks.map((task) => (
              <TaskCard key={task.id} task={task} onDelete={handleDelete} />
            ))}
          </div>
        )}
      </div>

      <NewTaskDialog
        open={showNewTask}
        onClose={() => setShowNewTask(false)}
        onCreated={handleTaskCreated}
      />
    </div>
  );
}

function EmptyState({ onNew }: { onNew: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center h-64 text-center">
      <div className="w-16 h-16 rounded-2xl bg-secondary flex items-center justify-center mb-4">
        <Film className="w-8 h-8 text-muted-foreground/40" />
      </div>
      <h3 className="text-sm font-medium text-foreground mb-1">还没有任务</h3>
      <p className="text-xs text-muted-foreground mb-4">
        上传一段视频，让 GoldenClip 帮你完成初剪
      </p>
      <Button
        onClick={onNew}
        size="sm"
        className="bg-primary text-primary-foreground hover:bg-primary/90 gap-2"
      >
        <Plus className="w-4 h-4" />
        新建第一个任务
      </Button>
    </div>
  );
}
