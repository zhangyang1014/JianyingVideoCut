import { useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { ChevronDown, Clock, Cpu, Gauge, Loader2, Mic } from "lucide-react";
import { cn } from "@/lib/utils";
import { estimateTime } from "./utils";

// ============================================================
// ASR 模型信息配置
// ============================================================
interface WhisperModel {
  id: string;
  name: string;
  desc: string;
  /** 相对于视频时长的处理倍率（值越小越慢）：realtime_factor */
  speedFactor: number;
  accuracy: "低" | "中" | "高" | "极高";
  accuracyColor: string;
  recommended?: boolean;
}

const WHISPER_MODELS: WhisperModel[] = [
  {
    id: "tiny",
    name: "Tiny",
    desc: "超轻量，速度最快，适合快速预览",
    speedFactor: 32,
    accuracy: "低",
    accuracyColor: "text-slate-400",
  },
  {
    id: "base",
    name: "Base",
    desc: "轻量均衡，日常中文识别推荐",
    speedFactor: 16,
    accuracy: "中",
    accuracyColor: "text-blue-400",
    recommended: true,
  },
  {
    id: "small",
    name: "Small",
    desc: "精度更高，适合口音较重场景",
    speedFactor: 8,
    accuracy: "高",
    accuracyColor: "text-cyan-400",
  },
  {
    id: "medium",
    name: "Medium",
    desc: "高精度，耗时较长",
    speedFactor: 4,
    accuracy: "高",
    accuracyColor: "text-cyan-400",
  },
  {
    id: "large-v3",
    name: "Large-v3",
    desc: "最高精度，适合正式发布内容",
    speedFactor: 2,
    accuracy: "极高",
    accuracyColor: "text-primary",
  },
];
/** 当前选中的 ASR 配置（backend + model） */
export interface AsrSelection {
  backend: "funasr" | "whisper";
  model: string;
  silenceThreshold: number;
  enableDiarization: boolean;
}

// 字幕分段偏好。高级数值仍会从任务参数/推荐值兼容带入。
const THRESHOLD_OPTIONS = [0.10, 0.20, 0.30, 0.40, 0.50];
const THRESHOLD_LABELS: Record<number, string> = {
  0.05: "极细",
  0.10: "更细",
  0.15: "较细",
  0.20: "偏细",
  0.25: "稍细",
  0.30: "标准",
  0.35: "稍完整",
  0.40: "完整",
  0.45: "较完整",
  0.50: "更完整",
};

const ANALYZE_RULES = [
  { title: "词典纠错", desc: "读取 backend/data/glossary.txt，对同长度术语做确定性替换。" },
  { title: "结巴检测", desc: "识别连续重复短词，标记为 <STU>，供后续字幕和审计使用。" },
  { title: "语气词标注", desc: "根据 filler words 标记 <FIL>，并记录可删除的精确时间边界。" },
  { title: "字幕分段", desc: "按停顿偏好、标点、说话人切换和最大时长切成字幕卡。" },
  { title: "后处理整理", desc: "合并句首助词/标点和短碎片，必要时按句末标点或说话人变化拆分。" },
];

export function AsrModelDialog({
  open,
  onClose,
  videoDuration,
  selection,
  onSelect,
  onConfirm,
  isProcessing,
}: {
  open: boolean;
  onClose: () => void;
  videoDuration: number;
  selection: AsrSelection;
  onSelect: (s: AsrSelection) => void;
  onConfirm: () => void;
  isProcessing: boolean;
}) {
  const videoMins = Math.round(videoDuration / 60);
  const isFunASR = selection.backend === "funasr";
  const [whisperExpanded, setWhisperExpanded] = useState(false);

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="bg-card border-border max-w-lg">
        <DialogHeader>
          <DialogTitle className="text-foreground flex items-center gap-2">
            <Mic className="w-4 h-4 text-primary" />
            选择 ASR 识别引擎
          </DialogTitle>
          <DialogDescription className="text-muted-foreground text-xs">
            视频时长约 <span className="text-foreground font-medium">{videoMins} 分钟</span>，以下为各模型在 CPU 上的估算时间
          </DialogDescription>
        </DialogHeader>

        {/* ── FunASR 区块 ── */}
        <div className="mt-1">
          <p className="text-[10px] text-muted-foreground/60 uppercase tracking-wider mb-1.5 px-0.5">
            阿里达摩院 · FunASR
          </p>
          <button
            onClick={() => onSelect({ ...selection, backend: "funasr", model: "paraformer-zh" })}
            className={cn(
              "w-full text-left rounded-lg border px-4 py-3 transition-all duration-150",
              isFunASR
                ? "border-primary bg-primary/10"
                : "border-border bg-secondary/30 hover:border-border/80 hover:bg-secondary/50"
            )}
          >
            <div className="flex items-center gap-3">
              <div className="flex items-center justify-center w-8 h-8 rounded-md bg-background border border-border shrink-0 text-[10px] font-bold text-primary">
                F
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-semibold text-foreground font-mono">Paraformer-zh</span>
                  <span className="text-[11px] font-medium text-primary">精度极高</span>
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-primary/20 text-primary font-medium">推荐</span>
                </div>
                <p className="text-[11px] text-muted-foreground mt-0.5">
                  字符级时间戳（误差 &lt;50ms）· 自动标点 · 中文专用
                </p>
              </div>
              <div className="text-right shrink-0">
                <div className="flex items-center gap-1 justify-end text-muted-foreground">
                  <Clock className="w-3 h-3" />
                  <span className="text-xs font-mono">{estimateTime(videoDuration, 6)}</span>
                </div>
                <p className="text-[10px] text-muted-foreground/50 mt-1">首次下载约 900MB</p>
              </div>
            </div>
          </button>
        </div>

        {/* ── Whisper 区块 ── */}
        <div className="mt-3">
          <button
            onClick={() => setWhisperExpanded((v) => !v)}
            className="w-full flex items-center justify-between px-0.5 mb-1.5 group"
          >
            <p className="text-[10px] text-muted-foreground/60 uppercase tracking-wider group-hover:text-muted-foreground/80 transition-colors">
              OpenAI · Whisper
            </p>
            <div className="flex items-center gap-1 text-[10px] text-muted-foreground/50 group-hover:text-muted-foreground/70 transition-colors">
              {!isFunASR && (
                <span className="text-primary font-medium">
                  {WHISPER_MODELS.find((m) => m.id === selection.model)?.name ?? selection.model}
                </span>
              )}
              <ChevronDown
                className={cn(
                  "w-3 h-3 transition-transform duration-200",
                  whisperExpanded ? "rotate-180" : ""
                )}
              />
            </div>
          </button>
          {whisperExpanded && (
            <div className="space-y-1.5">
              {WHISPER_MODELS.map((model) => {
                const isSelected = !isFunASR && selection.model === model.id;
                const timeStr = estimateTime(videoDuration, model.speedFactor);
                return (
                  <button
                    key={model.id}
                    onClick={() => onSelect({ ...selection, backend: "whisper", model: model.id })}
                    className={cn(
                      "w-full text-left rounded-lg border px-4 py-2.5 transition-all duration-150",
                      isSelected
                        ? "border-primary bg-primary/10"
                        : "border-border bg-secondary/30 hover:border-border/80 hover:bg-secondary/50"
                    )}
                  >
                    <div className="flex items-center gap-3">
                      <div className="flex items-center justify-center w-8 h-8 rounded-md bg-background border border-border shrink-0">
                        <Cpu className="w-3.5 h-3.5 text-muted-foreground" />
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="text-sm font-semibold text-foreground font-mono">{model.name}</span>
                          <span className={cn("text-[11px] font-medium", model.accuracyColor)}>
                            精度{model.accuracy}
                          </span>
                        </div>
                        <p className="text-[11px] text-muted-foreground mt-0.5">{model.desc}</p>
                      </div>
                      <div className="text-right shrink-0">
                        <div className="flex items-center gap-1 justify-end text-muted-foreground">
                          <Clock className="w-3 h-3" />
                          <span className="text-xs font-mono">{timeStr}</span>
                        </div>
                        <div className="flex items-center gap-0.5 justify-end mt-1">
                          {Array.from({ length: 5 }).map((_, i) => {
                            const filled = i < Math.round(Math.log2(model.speedFactor) / Math.log2(32) * 5);
                            return (
                              <div
                                key={i}
                                className={cn(
                                  "w-2 h-1.5 rounded-sm",
                                  filled ? "bg-muted-foreground/60" : "bg-muted-foreground/15"
                                )}
                              />
                            );
                          })}
                          <span className="text-[10px] text-muted-foreground/50 ml-1">速度</span>
                        </div>
                      </div>
                    </div>
                  </button>
                );
              })}
            </div>
          )}
        </div>

        <div className="flex items-center gap-2 mt-2 pt-3 border-t border-border">
          <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground flex-1">
            <Gauge className="w-3 h-3" />
            <span>估算基于 CPU 推理，有 GPU 时实际速度更快</span>
          </div>
          <Button
            variant="outline"
            size="sm"
            className="h-8 text-xs"
            onClick={onClose}
          >
            取消
          </Button>
          <Button
            size="sm"
            className="h-8 text-xs bg-primary text-primary-foreground hover:bg-primary/90 gap-1.5"
            onClick={onConfirm}
            disabled={isProcessing}
          >
            {isProcessing ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <Mic className="w-3.5 h-3.5" />
            )}
            开始识别
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

export function AsrAnalyzeDialog({
  open,
  onClose,
  taskType,
  selection,
  onSelect,
  onConfirm,
  isProcessing,
}: {
  open: boolean;
  onClose: () => void;
  taskType?: string;
  selection: AsrSelection;
  onSelect: (s: AsrSelection) => void;
  onConfirm: () => void;
  isProcessing: boolean;
}) {
  const thresholdOptions = Array.from(new Set([...THRESHOLD_OPTIONS, selection.silenceThreshold])).sort((a, b) => a - b);
  const taskTypeTip = taskType === "interview_compress"
    ? "访谈内容默认更重视语义完整和说话人边界。"
    : taskType === "highlight_reel"
      ? "产品集锦默认兼顾节奏和卖点句完整性。"
      : "口播精修默认偏短句和节奏感。";

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="bg-card border-border max-w-lg">
        <DialogHeader>
          <DialogTitle className="text-foreground flex items-center gap-2">
            <Gauge className="w-4 h-4 text-primary" />
            文本整理设置
          </DialogTitle>
          <DialogDescription className="text-muted-foreground text-xs">
            基于已完成的识别文本做词典纠错、语气词/重复标记，并生成字幕分段。
          </DialogDescription>
        </DialogHeader>

        <div className="mt-3 rounded-lg border border-border bg-secondary/20 px-4 py-3">
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-1.5">
              <Gauge className="w-3.5 h-3.5 text-muted-foreground" />
              <span className="text-xs font-medium text-foreground">字幕分段偏好</span>
              <span className="text-[10px] text-muted-foreground/60 ml-1">
                （高级值 {selection.silenceThreshold.toFixed(2)}s）
              </span>
            </div>
            <span className="text-xs text-primary font-semibold">
              {THRESHOLD_LABELS[selection.silenceThreshold] ?? `${selection.silenceThreshold}s`}
            </span>
          </div>
          <div className="flex items-center gap-1">
            {thresholdOptions.map((val) => {
              const isActive = selection.silenceThreshold === val;
              return (
                <button
                  key={val}
                  onClick={() => onSelect({ ...selection, silenceThreshold: val })}
                  className={cn(
                    "flex-1 rounded py-1.5 flex flex-col items-center transition-all duration-100",
                    isActive
                      ? "bg-primary text-primary-foreground font-semibold"
                      : "bg-background border border-border text-muted-foreground hover:border-primary/60 hover:text-foreground"
                  )}
                >
                  <span className="text-[10px] leading-tight">{THRESHOLD_LABELS[val] ?? "自定义"}</span>
                  <span className="text-[8px] font-mono leading-tight opacity-70">{val.toFixed(2)}s</span>
                </button>
              );
            })}
          </div>
          <p className="text-[10px] text-muted-foreground/50 mt-1.5">
            值越小字幕卡越细；值越大更完整。停顿、标点、说话人切换和 12 秒上限都会参与分段。{taskTypeTip}
          </p>
        </div>

        <details className="mt-2 rounded-lg border border-primary/20 bg-primary/5 px-4 py-3 group" open>
          <summary className="cursor-pointer list-none text-xs font-medium text-primary flex items-center justify-between">
            <span>本次会执行什么整理</span>
            <span className="text-[10px] text-muted-foreground group-open:hidden">展开</span>
            <span className="text-[10px] text-muted-foreground hidden group-open:inline">收起</span>
          </summary>
          <div className="mt-3 space-y-3">
            <div className="grid grid-cols-2 gap-2 text-[10px]">
              <div className="rounded border border-border/60 bg-background/40 px-2 py-1.5">
                <span className="text-muted-foreground">分段偏好</span>
                <span className="ml-2 text-foreground">{THRESHOLD_LABELS[selection.silenceThreshold] ?? "自定义"} · {selection.silenceThreshold.toFixed(2)}s</span>
              </div>
              <div className="rounded border border-border/60 bg-background/40 px-2 py-1.5">
                <span className="text-muted-foreground">词典来源</span>
                <span className="ml-2 text-foreground">glossary.txt</span>
              </div>
              <div className="rounded border border-border/60 bg-background/40 px-2 py-1.5">
                <span className="text-muted-foreground">语气词</span>
                <span className="ml-2 text-foreground">任务参数 / 默认列表</span>
              </div>
            </div>
            <div className="space-y-1.5">
              {ANALYZE_RULES.map((rule, index) => (
                <div key={rule.title} className="flex gap-2 text-[11px]">
                  <span className="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-primary/15 text-[9px] font-mono text-primary">
                    {index + 1}
                  </span>
                  <div>
                    <p className="text-foreground/90 font-medium">{rule.title}</p>
                    <p className="text-muted-foreground/70 leading-relaxed">{rule.desc}</p>
                  </div>
                </div>
              ))}
            </div>
            <p className="text-[10px] leading-relaxed text-muted-foreground/70 border-t border-border/60 pt-2">
              这一步是确定性规则处理，不调用大模型，也不会运行 CAM++；真正使用提示词的是下一步「AI 审计」。
            </p>
          </div>
        </details>

        <div className="flex items-center gap-2 mt-2 pt-3 border-t border-border">
          <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground flex-1">
            <Gauge className="w-3 h-3" />
            <span>文本整理完成后即可进入 AI 审计</span>
          </div>
          <Button
            variant="outline"
            size="sm"
            className="h-8 text-xs"
            onClick={onClose}
          >
            取消
          </Button>
          <Button
            size="sm"
            className="h-8 text-xs bg-primary text-primary-foreground hover:bg-primary/90 gap-1.5"
            onClick={onConfirm}
            disabled={isProcessing}
          >
            {isProcessing ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <Gauge className="w-3.5 h-3.5" />
            )}
            开始文本整理
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
