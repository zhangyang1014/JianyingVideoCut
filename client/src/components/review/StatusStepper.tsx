import { useEffect, useState, type ReactNode } from "react";
import { Check, Loader2, X } from "lucide-react";
import {
  getExecutionCheckpointCount,
  getExecutionRetrySeconds,
  getExecutionTotalAttempts,
  type ExecutionRun,
  type ExecutionRunSummary,
  type TaskStatus,
} from "@/lib/api";
import { cn } from "@/lib/utils";

// ============================================================
// Status Stepper — 顶部全流程步骤条
// ============================================================
export type ReviewStepKey =
  | "upload"
  | "asr"
  | "audit"
  | "semantic_audit"
  | "reel_plan"
  | "reel_validate"
  | "review";

export interface ReviewStepDef {
  key: ReviewStepKey;
  label: string;
  /** 哪些 TaskStatus 值对应"此步骤正在进行" */
  active: TaskStatus[];
  /** 哪些 TaskStatus 值对应"此步骤已完成" */
  done: TaskStatus[];
  /** 人工确认类步骤不显示转圈，避免误导为后台仍在运行 */
  staticActive?: boolean;
  isActive?: (status: TaskStatus, stage?: string | null, context?: ReviewStepContext) => boolean;
  isDone?: (status: TaskStatus, stage?: string | null, context?: ReviewStepContext) => boolean;
}

export interface ReviewStepContext {
  highlightReelsReady?: boolean;
  highlightQualityReady?: boolean;
}

const STEPS: ReviewStepDef[] = [
  {
    key: "upload",
    label: "上传视频",
    active: ["pending"],
    done: ["asr_running", "asr_recognized", "diarization_running", "asr_diarized", "analyze_running", "asr_done", "audit_running", "review", "done"],
  },
  {
    key: "asr",
    label: "语音识别",
    active: ["asr_running", "asr_recognized", "diarization_running", "asr_diarized", "analyze_running"],
    done: ["asr_done", "audit_running", "review", "done"],
  },
  {
    key: "audit",
    label: "AI 审计",
    active: ["audit_running"],
    done: ["review", "done"],
  },
  {
    key: "review",
    label: "剪辑审核",
    active: ["review"],
    done: ["done"],
    staticActive: true,
  },
];

const REEL_PLAN_STAGES = new Set(["reel_plan", "reel_sync"]);
const REEL_VALIDATE_STAGES = new Set(["reel_validate"]);
const REEL_DONE_STAGES = new Set(["reel_ready", "audit_done"]);

const HIGHLIGHT_STEPS: ReviewStepDef[] = [
  STEPS[0],
  STEPS[1],
  {
    key: "semantic_audit",
    label: "语义审计",
    active: ["audit_running"],
    done: ["review", "done"],
    isActive: (status, stage) =>
      status === "audit_running" &&
      !REEL_PLAN_STAGES.has(stage || "") &&
      !REEL_VALIDATE_STAGES.has(stage || "") &&
      !REEL_DONE_STAGES.has(stage || ""),
    isDone: (status, stage) =>
      ["review", "done"].includes(status) ||
      (status === "audit_running" && (
        REEL_PLAN_STAGES.has(stage || "") ||
        REEL_VALIDATE_STAGES.has(stage || "") ||
        REEL_DONE_STAGES.has(stage || "")
      )),
  },
  {
    key: "reel_plan",
    label: "产品集锦",
    active: ["audit_running"],
    done: ["review", "done"],
    isActive: (status, stage) =>
      status === "audit_running" && (REEL_PLAN_STAGES.has(stage || "") || REEL_VALIDATE_STAGES.has(stage || "")),
    isDone: (status, stage, context) =>
      Boolean(context?.highlightReelsReady || context?.highlightQualityReady) ||
      (status === "audit_running" && REEL_DONE_STAGES.has(stage || "")),
  },
  STEPS[3],
];

export function getReviewSteps(taskType?: string): ReviewStepDef[] {
  return taskType === "highlight_reel" ? HIGHLIGHT_STEPS : STEPS;
}

export function StatusStepper({
  status,
  taskType,
  stage,
  highlightReelsReady,
  highlightQualityReady,
  renderAction,
  executionRun,
}: {
  status: TaskStatus;
  taskType?: string;
  stage?: string | null;
  highlightReelsReady?: boolean;
  highlightQualityReady?: boolean;
  renderAction?: (step: ReviewStepDef) => ReactNode;
  executionRun?: ExecutionRun | ExecutionRunSummary | null;
}) {
  const [nowMs, setNowMs] = useState(Date.now());
  const isError = status === "error" && executionRun?.status !== "retry_wait";
  const steps = getReviewSteps(taskType);
  const hasActions = Boolean(renderAction);
  const effectiveStage = executionRun?.current_stage ?? stage;
  const stepContext: ReviewStepContext = {
    highlightReelsReady,
    highlightQualityReady,
  };
  const checkpointCount = getExecutionCheckpointCount(executionRun);
  const retrySeconds = getExecutionRetrySeconds(executionRun, nowMs);

  useEffect(() => {
    if (executionRun?.status !== "retry_wait") return;
    const timer = setInterval(() => setNowMs(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [executionRun?.run_id, executionRun?.status, executionRun?.retry_state.next_retry_at]);

  return (
    <div className={cn(
      "shrink-0 border-b border-border bg-card/40 px-4",
      hasActions || executionRun ? "py-2" : "h-10 flex items-center",
    )}>
      {executionRun && (
        <div className="mb-1.5 flex min-h-5 flex-wrap items-center gap-x-3 gap-y-1 text-[10px] font-mono">
          {executionRun.status === "retry_wait" && (
            <span className="inline-flex items-center gap-1 text-amber-300">
              <Loader2 className="h-3 w-3 animate-spin" />
              自动重试第 {executionRun.retry_state.retries_used} 次（总{getExecutionTotalAttempts(executionRun)}次）
              {retrySeconds !== null && ` · ${retrySeconds}秒后重试`}
            </span>
          )}
          {effectiveStage && <span className="text-sky-300/80">当前阶段：{effectiveStage}</span>}
          <span className="text-muted-foreground">复用检查点：{checkpointCount}</span>
          {(executionRun.status === "failed" || executionRun.status === "interrupted") && (
            <span className={executionRun.recoverable ? "text-amber-400" : "text-red-400"}>
              {executionRun.recoverable ? "失败可恢复" : "失败不可恢复"}
            </span>
          )}
        </div>
      )}
      <div className="w-full overflow-x-auto">
        <div className="flex min-w-max sm:min-w-full">
        {steps.map((step, idx) => {
          // error 状态时不激活任何步骤，只显示历史完成步骤
          const isDone = !isError && (step.isDone ? step.isDone(status, effectiveStage, stepContext) : step.done.includes(status));
          const isActive = !isError && (step.isActive ? step.isActive(status, effectiveStage, stepContext) : step.active.includes(status));
          const isLast = idx === steps.length - 1;
          const action = renderAction?.(step);

          return (
            <div
              key={step.key}
              className={cn(
                "min-w-[118px] flex-1 basis-0 overflow-hidden",
              )}
            >
              <div className="flex items-center min-w-0">
              {/* 步骤节点 */}
              <div className="flex items-center gap-1.5 shrink-0">
                {/* 圆圈指示器 */}
                <div
                  className={cn(
                    "w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-bold transition-all",
                    isDone && "bg-primary text-black",
                    isActive && "border-2 border-primary text-primary bg-primary/10",
                    !isDone && !isActive && "border border-border text-muted-foreground bg-transparent",
                  )}
                >
                  {isDone ? (
                    <Check className="w-2.5 h-2.5" />
                  ) : isActive && !step.staticActive ? (
                    <Loader2 className="w-2.5 h-2.5 animate-spin" />
                  ) : (
                    <span>{idx + 1}</span>
                  )}
                </div>
                {/* 步骤名 */}
                <span
                  className={cn(
                    "text-[11px] whitespace-nowrap",
                    isDone && "text-primary font-medium",
                    isActive && "text-foreground font-semibold",
                    !isDone && !isActive && "text-muted-foreground",
                  )}
                >
                  {step.label}
                </span>
              </div>

              {/* 连接线（最后一步不显示） */}
              {!isLast && (
                <div
                  className={cn(
                    "flex-1 h-px mx-2",
                    isDone ? "bg-primary/50" : "bg-border",
                  )}
                />
              )}
              </div>

              {hasActions && (
                <div className="mt-1 flex min-h-7 min-w-0 max-w-full items-center gap-1.5 overflow-x-auto overflow-y-hidden pr-2 whitespace-nowrap">
                  {action}
                </div>
              )}
            </div>
          );
        })}

        {/* 出错时在末尾追加红色标签 */}
        {isError && executionRun?.status !== "retry_wait" && (
          <div className="ml-3 flex items-center gap-1 shrink-0">
            <div className="w-4 h-px bg-border" />
            <div className="flex items-center gap-1 px-2 py-0.5 rounded-full bg-destructive/20 border border-destructive/40">
              <X className="w-2.5 h-2.5 text-destructive" />
              <span className="text-[10px] text-destructive font-medium">
                {executionRun?.recoverable ? "出错 · 可恢复" : "出错"}
              </span>
            </div>
          </div>
        )}
        </div>
      </div>
    </div>
  );
}
