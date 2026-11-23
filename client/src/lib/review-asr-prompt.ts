import type { Task } from "@/lib/api";

const RUNNING_STATUSES = new Set([
  "asr_running",
  "diarization_running",
  "analyze_running",
  "audit_running",
  "translate_running",
  "tts_running",
  "dubbing_running",
  "export_running",
]);

/** 首次进入详情页时是否应弹出 ASR 引导弹窗 */
export function shouldPromptAsrOnEntry(task: Pick<Task, "status" | "video_path" | "asr_result" | "task_type">): boolean {
  if (task.task_type === "one_click_translate") return false;
  if (task.status !== "pending") return false;
  if (!task.video_path) return false;
  if (task.asr_result) return false;
  if (RUNNING_STATUSES.has(task.status)) return false;
  return true;
}

export function getAsrPromptSessionKey(taskId: string): string {
  return `gc_asr_prompt_${taskId}`;
}

export function hasShownAsrPrompt(taskId: string): boolean {
  if (typeof sessionStorage === "undefined") return false;
  try {
    return sessionStorage.getItem(getAsrPromptSessionKey(taskId)) === "1";
  } catch {
    return false;
  }
}

export function markAsrPromptShown(taskId: string): void {
  if (typeof sessionStorage === "undefined") return;
  try {
    sessionStorage.setItem(getAsrPromptSessionKey(taskId), "1");
  } catch {
    // 隐私模式等场景下静默忽略
  }
}
