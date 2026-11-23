/**
 * GoldenClip Dashboard — Task Board
 * Design: 暗金剪辑台 · 编导美学
 * Features: Task cards, drag-drop upload, new task creation, status overview
 */

import { useState, useEffect, useCallback, useRef } from "react";
import { useLocation } from "wouter";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
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
  Languages,
  Volume2,
} from "lucide-react";
import {
  fetchTasks,
  createTask,
  uploadVideo,
  uploadTranslationVideo,
  startOneClickTranslate,
  deleteTask,
  getThumbnailUrl,
  type Task,
  type TaskSummary,
  type TaskStatus,
  STATUS_LABELS,
  STATUS_COLORS,
  TASK_TYPE_LABELS,
  formatDuration,
  getExecutionRetrySeconds,
  getExecutionTotalAttempts,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import {
  DEFAULT_TRANSLATE_MODEL,
  DEFAULT_TTS_MODEL,
  DEFAULT_TTS_VOICE,
  useAppConfig,
  resolveFeatureLlm,
} from "@/hooks/useAppConfig";

const PROCESSING_STATUSES: TaskStatus[] = [
  "asr_running",
  "diarization_running",
  "analyze_running",
  "audit_running",
  "translate_running",
  "tts_running",
  "dubbing_running",
  "export_running",
];

// ============================================================
// Status Badge
// ============================================================
function StatusBadge({ status, retrying = false }: { status: TaskStatus; retrying?: boolean }) {
  const label = retrying ? "自动重试中" : STATUS_LABELS[status];
  const colors = retrying ? "text-amber-300 bg-amber-900/50" : STATUS_COLORS[status];
  const isRunning = retrying || status.includes("running");

  return (
    <span className={cn("status-badge", colors)}>
      {isRunning && <Loader2 className="w-2.5 h-2.5 animate-spin" />}
      {status === "done" && <CheckCircle2 className="w-2.5 h-2.5" />}
      {status === "error" && !retrying && <AlertCircle className="w-2.5 h-2.5" />}
      {label}
    </span>
  );
}

// ============================================================
// Task Card
// ============================================================
function TaskCard({ task, onDelete }: { task: TaskSummary; onDelete: (id: string) => void }) {
  const [, navigate] = useLocation();
  const [thumbError, setThumbError] = useState(false);
  const [nowMs, setNowMs] = useState(Date.now());
  const executionRun = task.execution_run;

  useEffect(() => {
    if (executionRun?.status !== "retry_wait") return;
    const timer = setInterval(() => setNowMs(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [executionRun?.run_id, executionRun?.status, executionRun?.retry_state.next_retry_at]);

  const compressionRatio = task.original_duration && task.edited_duration
    ? Math.round((1 - task.edited_duration / task.original_duration) * 100)
    : null;
  const retrySeconds = getExecutionRetrySeconds(executionRun, nowMs);

  return (
    <div
      className="group relative w-[320px] shrink-0 flex flex-col bg-card border border-border rounded-xl overflow-hidden hover:-translate-y-0.5 hover:shadow-lg hover:shadow-black/30 transition-all duration-200 cursor-pointer animate-fade-in-up"
      onClick={() => navigate(`/tasks/${task.id}`)}
    >
      {/* Thumbnail */}
      <div className="relative aspect-[4/3] bg-black overflow-hidden">
        {!thumbError && task.video_path ? (
          <img
            src={getThumbnailUrl(task.id)}
            alt={task.name}
            className="w-full h-full object-contain"
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

        {/* Lightweight running indicator to keep the thumbnail visible */}
        {(["asr_running", "diarization_running", "analyze_running", "audit_running", "translate_running", "tts_running", "dubbing_running", "export_running"].includes(task.status)) && (
          <div className="absolute bottom-0 left-0 right-0 h-1 bg-black/40">
            <div className="h-full w-full bg-primary animate-pulse" />
          </div>
        )}
      </div>

      {/* Content */}
      <div className="p-3 flex flex-col flex-1">
        {/* 标题 + 状态 */}
        <div className="flex items-start justify-between gap-2 mb-1.5">
          <h3 className="text-sm font-medium text-foreground line-clamp-2 flex-1 leading-snug">{task.name}</h3>
          <StatusBadge status={task.status} retrying={executionRun?.status === "retry_wait"} />
        </div>

        {/* 类型 tag + 文件名 */}
        <div className="flex items-center gap-1.5 flex-wrap mb-1">
          <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[11px] bg-muted text-muted-foreground border border-border/50">
            <Video className="w-3 h-3" />
            {TASK_TYPE_LABELS[task.task_type] || task.task_type}
          </span>
        </div>
        {task.video_filename && (
          <p className="text-xs text-muted-foreground line-clamp-2 leading-snug">{task.video_filename}</p>
        )}
        {executionRun && (
          <div className={cn(
            "mt-1.5 rounded border px-2 py-1 text-[10px] font-mono leading-relaxed",
            executionRun.status === "retry_wait"
              ? "border-amber-500/30 bg-amber-500/10 text-amber-300"
              : executionRun.status === "failed" || executionRun.status === "interrupted"
                ? executionRun.recoverable
                  ? "border-amber-500/30 bg-amber-500/5 text-amber-400"
                  : "border-red-500/30 bg-red-500/5 text-red-400"
                : "border-border/60 bg-secondary/30 text-muted-foreground",
          )}>
            {executionRun.status === "retry_wait" && (
              <p>
                自动重试第 {executionRun.retry_state.retries_used} 次（总{getExecutionTotalAttempts(executionRun)}次）
                {retrySeconds !== null ? ` · ${retrySeconds}秒后` : ""}
              </p>
            )}
            <p>
              {executionRun.current_stage ? `当前阶段：${executionRun.current_stage}` : `执行：${executionRun.operation}`}
              {(executionRun.status === "failed" || executionRun.status === "interrupted")
                ? ` · ${executionRun.recoverable ? "可从检查点恢复" : "不可恢复"}`
                : ""}
            </p>
          </div>
        )}

        {/* Stats row */}
        {(task.segments_kept > 0 || task.segments_deleted > 0) && (
          <div className="flex items-center gap-3 text-xs mt-1.5">
            <span className="text-green-400">✓ {task.segments_kept} 保留</span>
            <span className="text-red-400">✗ {task.segments_deleted} 删除</span>
            {compressionRatio !== null && compressionRatio > 0 && (
              <span className="text-primary font-mono">↓{compressionRatio}%</span>
            )}
          </div>
        )}

        {/* Created time — 贴底 */}
        <div className="flex items-center justify-between mt-auto pt-2">
          <span className="text-xs text-muted-foreground">
            {new Date(task.created_at).toLocaleDateString("zh-CN", {
              month: "short", day: "numeric", hour: "2-digit", minute: "2-digit"
            })}
          </span>
          <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
            <Button
              variant="ghost"
              size="icon"
              className="w-6 h-6 text-foreground/80 hover:text-destructive"
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
              className="w-6 h-6 text-foreground/80 hover:text-primary"
            >
              <ArrowRight className="w-3 h-3" />
            </Button>
          </div>
        </div>
      </div>

      {/* Gold left border for active tasks */}
      {(task.status === "review" || task.status === "asr_done" || task.status === "asr_recognized" || task.status === "asr_diarized") && (
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
  const { config } = useAppConfig();
  const [name, setName] = useState("");
  const [taskType, setTaskType] = useState("highlight_reel");
  const [referenceScript, setReferenceScript] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [isDragging, setIsDragging] = useState(false);
  const [sourceLanguage, setSourceLanguage] = useState("zh");
  const [targetLanguage, setTargetLanguage] = useState("en");
  const [subtitleModel, setSubtitleModel] = useState("current");
  const [ttsModel, setTtsModel] = useState("current");
  const fileInputRef = useRef<HTMLInputElement>(null);
  const isTranslateTask = taskType === "one_click_translate";
  const firstFile = files[0] ?? null;

  // Scenario definitions with rules preview
  const SCENARIOS = [
    {
      id: "highlight_reel",
      icon: <Flame className="w-4 h-4" />,
      label: "直播卖货集锦",
      tag: "Live Commerce Reel",
      desc: "按产品拆分直播讲解，提炼卖点短视频",
      target: "一个产品一条，保留产品名/卖点/差异化故事",
      color: "text-orange-400",
      border: "border-orange-500/40",
      bg: "bg-orange-500/5",
      rules: [
        { code: "L1", name: "产品边界", desc: "一个集锦只讲一个产品" },
        { code: "L2", name: "产品开场", desc: "首段说清产品名/品类/痛点" },
        { code: "L3", name: "卖点故事", desc: "保留卖点/场景/差异化背书" },
        { code: "L4", name: "链接删除", desc: "删除小黄车/下单/库存催单" },
        { code: "L5", name: "跨品切分", desc: "新产品介绍必须另起集锦" },
      ],
      styleNote: "气口80ms/50ms | 单条60-300秒 | 标题包含产品名",
    },
    {
      id: "monologue_clean",
      icon: <Mic className="w-4 h-4" />,
      label: "口播精修",
      tag: "Monologue Clean",
      desc: "博主口播、知识分享、产品讲解",
      target: "保留完整表达，去除无效重说",
      color: "text-amber-400",
      border: "border-amber-500/40",
      bg: "bg-amber-500/5",
      rules: [
        { code: "P1", name: "重说识别", desc: "比较多次录制，保留完整流畅版" },
        { code: "P2", name: "结巴切除", desc: "按字级时间定位，接缝可试听" },
        { code: "P3", name: "语气词分级", desc: "必删/酌情/保留三档" },
        { code: "P7", name: "开头钩子", desc: "保留有效开场，去除试录准备" },
        { code: "P8", name: "结尾收束", desc: "干净利落，保留CTA" },
      ],
      styleNote: "quick_cut: 气口80ms，全删语气词 | immersive: 气口150ms，保留情绪词",
    },
    {
      id: "interview_compress",
      icon: <Users className="w-4 h-4" />,
      label: "直播访谈压缩",
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
      id: "one_click_translate",
      icon: <Languages className="w-4 h-4" />,
      label: "一键转英文",
      tag: "One-click Translate",
      desc: "批量视频 ASR、字幕校准、翻译、目标语言配音",
      target: "保持原时间轴，替换为目标语言音轨",
      color: "text-emerald-400",
      border: "border-emerald-500/40",
      bg: "bg-emerald-500/5",
      rules: [
        { code: "T1", name: "多视频上传", desc: "同任务内批量处理" },
        { code: "T2", name: "字幕校准", desc: "先纠错断句，再翻译" },
        { code: "T3", name: "时间轴锁定", desc: "每条配音按原 start 对齐" },
        { code: "T4", name: "静音替换", desc: "移除原音轨，合入新音轨" },
        { code: "T5", name: "可编辑导出", desc: "预览后改字幕再导出" },
      ],
      styleNote: "ASR → 校准 → 翻译 → TTS → 对齐合成，每步校验上一阶段产物",
    },
  ];

  const activeScenario = SCENARIOS.find(s => s.id === taskType);

  const setSelectedFiles = useCallback((selected: File[]) => {
    const valid = selected.filter((f) => /\.(mp4|mov|avi|mkv|webm)$/i.test(f.name));
    if (valid.length !== selected.length || valid.length === 0) {
      toast.error("请上传 MP4/MOV/AVI/MKV/WebM 格式视频");
      return;
    }
    const nextFiles = isTranslateTask ? valid : [valid[0]];
    setFiles(nextFiles);
    if (!name && nextFiles[0]) setName(nextFiles[0].name.replace(/\.[^/.]+$/, ""));
  }, [isTranslateTask, name]);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    setSelectedFiles(Array.from(e.dataTransfer.files));
  }, [setSelectedFiles]);

  const buildSubtitleModelConfig = () => {
    const translate = resolveFeatureLlm(config, "translate");
    if (subtitleModel === "current") {
      return { provider: "", model: "" };
    }
    if (subtitleModel === "openai") {
      return {
        provider: "openai",
        model: translate.model || DEFAULT_TRANSLATE_MODEL,
        base_url: translate.baseUrl,
      };
    }
    if (subtitleModel === "mock") {
      return { provider: "mock", model: "local-mock" };
    }
    return {
      provider: "ollama",
      model: translate.model || "qwen2.5:14b",
      base_url: translate.baseUrl || "http://localhost:11434",
    };
  };

  const buildTtsModelConfig = () => {
    if (ttsModel === "current") {
      return { provider: "", model: "", voice: DEFAULT_TTS_VOICE };
    }
    const [provider, model, voice] = ttsModel.split(":");
    return { provider, model, voice: voice || "alloy" };
  };

  const handleCreate = async () => {
    if (!name.trim()) { toast.error("请输入任务名称"); return; }
    if (files.length === 0) { toast.error("请选择视频文件"); return; }

    setUploading(true);
    try {
      const task = await createTask({ name: name.trim(), task_type: taskType,
        ...(taskType === "monologue_clean" && referenceScript.trim() ? { params: { reference_script: referenceScript.trim() } } : {}),
      });
      if (isTranslateTask) {
        for (let i = 0; i < files.length; i += 1) {
          await uploadTranslationVideo(task.id, files[i], (p) => {
            setUploadProgress((i + p) / files.length);
          });
        }
        await startOneClickTranslate(task.id, {
          source_language: sourceLanguage,
          target_language: targetLanguage,
          subtitle_model: buildSubtitleModelConfig(),
          tts_model: buildTtsModelConfig(),
          subtitle_api_key: subtitleModel === "current" ? undefined : resolveFeatureLlm(config, "translate").apiKey || undefined,
          tts_api_key: ttsModel === "current" ? undefined : resolveFeatureLlm(config, "tts").apiKey || undefined,
          asr_backend: "funasr",
          asr_model: "paraformer-zh",
        });
      } else {
        await uploadVideo(task.id, firstFile!, (p) => setUploadProgress(p));
      }
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
      <DialogContent className="bg-card border-border max-w-md max-h-[90vh] overflow-y-auto">
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
            <div className="grid grid-cols-2 gap-2">
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

          {taskType === "monologue_clean" && (
            <div className="space-y-1.5">
              <Label htmlFor="create-reference-script">口播原稿（选填）</Label>
              <Textarea id="create-reference-script" value={referenceScript} onChange={e => setReferenceScript(e.target.value)} maxLength={10000} rows={4} placeholder="粘贴准备好的口播稿；识别后可核对漏讲和重复录制。" />
              <p className="text-xs text-muted-foreground">原稿将参与语义审计；也可稍后在「原稿匹配」中补充。</p>
            </div>
          )}

          {isTranslateTask && (
            <div className="rounded-lg border border-emerald-500/30 bg-emerald-500/5 p-3 space-y-3">
              <div className="grid grid-cols-2 gap-2">
                <div className="space-y-1.5">
                  <Label className="text-xs text-muted-foreground uppercase tracking-wider">原语言</Label>
                  <Select value={sourceLanguage} onValueChange={setSourceLanguage}>
                    <SelectTrigger className="bg-secondary border-border text-foreground h-8">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="auto">自动检测</SelectItem>
                      <SelectItem value="zh">中文</SelectItem>
                      <SelectItem value="en">英文</SelectItem>
                      <SelectItem value="ja">日文</SelectItem>
                      <SelectItem value="ko">韩文</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-1.5">
                  <Label className="text-xs text-muted-foreground uppercase tracking-wider">目标语言</Label>
                  <Select value={targetLanguage} onValueChange={setTargetLanguage}>
                    <SelectTrigger className="bg-secondary border-border text-foreground h-8">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="en">英文</SelectItem>
                      <SelectItem value="zh">中文</SelectItem>
                      <SelectItem value="ja">日文</SelectItem>
                      <SelectItem value="ko">韩文</SelectItem>
                      <SelectItem value="es">西班牙文</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              </div>

              <div className="space-y-1.5">
                <Label className="text-xs text-muted-foreground uppercase tracking-wider">字幕校准和翻译模型</Label>
                <Select value={subtitleModel} onValueChange={setSubtitleModel}>
                  <SelectTrigger className="bg-secondary border-border text-foreground h-8">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="current">使用字幕翻译配置</SelectItem>
                    <SelectItem value="ollama">本地 Ollama</SelectItem>
                    <SelectItem value="openai">OpenRouter 云端</SelectItem>
                    <SelectItem value="mock">本地占位校准</SelectItem>
                  </SelectContent>
                </Select>
              </div>

              <div className="space-y-1.5">
                <Label className="text-xs text-muted-foreground uppercase tracking-wider">语音生成模型</Label>
                <Select value={ttsModel} onValueChange={setTtsModel}>
                  <SelectTrigger className="bg-secondary border-border text-foreground h-8">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="current">使用 TTS 配置</SelectItem>
                    <SelectItem value={`openai:${DEFAULT_TTS_MODEL}:${DEFAULT_TTS_VOICE}`}>OpenRouter Voxtral Mini TTS</SelectItem>
                    <SelectItem value="openai:openai/gpt-4o-mini-tts-2025-12-15:alloy">OpenRouter GPT-4o Mini TTS · alloy</SelectItem>
                    <SelectItem value="silent:aligned-silent:alloy">对齐静音占位</SelectItem>
                  </SelectContent>
                </Select>
                <p className="text-[10px] text-muted-foreground/60 flex items-center gap-1">
                  <Volume2 className="w-3 h-3" />
                  TTS Key 从 API 配置页读取，任务内不保存密钥。
                </p>
              </div>
            </div>
          )}

          {/* File Drop Zone */}
          <div className="space-y-1.5">
            <Label className="text-xs text-muted-foreground uppercase tracking-wider">视频文件</Label>
            <div
              className={cn(
                "border-2 border-dashed rounded-xl p-6 text-center transition-all duration-200 cursor-pointer",
                isDragging
                  ? "border-primary bg-accent"
                : files.length > 0
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
                multiple={isTranslateTask}
                className="hidden"
                onChange={(e) => {
                  setSelectedFiles(Array.from(e.target.files ?? []));
                }}
              />
              {files.length > 0 ? (
                <div className="flex flex-col items-center gap-2">
                  <FileVideo className="w-8 h-8 text-green-400" />
                  <p className="text-sm text-foreground font-medium">
                    {isTranslateTask ? `${files.length} 个视频已选择` : firstFile?.name}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {(files.reduce((sum, f) => sum + f.size, 0) / 1024 / 1024).toFixed(1)} MB
                  </p>
                  {isTranslateTask && (
                    <p className="text-[10px] text-muted-foreground/60 line-clamp-2">
                      {files.map((f) => f.name).join(" · ")}
                    </p>
                  )}
                </div>
              ) : (
                <div className="flex flex-col items-center gap-2">
                  <Upload className="w-8 h-8 text-muted-foreground/50" />
                  <p className="text-sm text-muted-foreground">
                    {isTranslateTask ? "拖拽 1 个或多个视频到此处，或点击选择" : "拖拽视频到此处，或点击选择"}
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
              disabled={uploading || files.length === 0 || !name}
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
  const [tasks, setTasks] = useState<TaskSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [showNewTask, setShowNewTask] = useState(false);
  const [, navigate] = useLocation();
  const loadInFlightRef = useRef(false);

  const loadTasks = useCallback(async (signal?: AbortSignal): Promise<boolean> => {
    if (loadInFlightRef.current) return false;
    loadInFlightRef.current = true;
    try {
      const data = await fetchTasks(signal);
      setTasks(data);
      return data.some((task) => PROCESSING_STATUSES.includes(task.status));
    } catch (e) {
      // Backend not running — show empty state
      return false;
    } finally {
      loadInFlightRef.current = false;
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let controller: AbortController | null = null;

    const schedule = (delay: number) => {
      if (cancelled) return;
      timer = setTimeout(poll, delay);
    };

    const poll = async () => {
      if (cancelled) return;
      if (document.hidden) {
        schedule(15_000);
        return;
      }
      controller = new AbortController();
      const hasRunningTask = await loadTasks(controller.signal);
      schedule(hasRunningTask ? 3_000 : 15_000);
    };

    const handleVisibilityChange = () => {
      if (document.hidden || cancelled) return;
      if (timer) clearTimeout(timer);
      void poll();
    };

    document.addEventListener("visibilitychange", handleVisibilityChange);
    void poll();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
      controller?.abort();
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
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
    setTasks((prev) => [{ ...task, revision: task.revision ?? 0 }, ...prev]);
    navigate(`/tasks/${task.id}`);
  };

  // Stats
  const stats = {
    total: tasks.length,
    done: tasks.filter((t) => t.status === "done").length,
    inProgress: tasks.filter((t) =>
      PROCESSING_STATUSES.includes(t.status)
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
              编导思维驱动 · ASR + OpenAI 语义审计
            </p>
          </div>
          <div className="flex items-center gap-2"><Button size="sm" variant="outline" onClick={() => window.location.assign("/productions")}>
            卖货视频创作
          </Button><Button
            onClick={() => setShowNewTask(true)}
            className="bg-primary text-primary-foreground hover:bg-primary/90 gap-2"
            size="sm"
          >
            <Plus className="w-4 h-4" />
            新建任务
          </Button></div>
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
          <div className="flex flex-wrap gap-4">
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
