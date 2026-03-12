/**
 * GoldenClip API Client
 * Design: 暗金剪辑台 · 编导美学
 * Communicates with FastAPI backend on port 8000
 */

const API_BASE = "http://localhost:8000/api";
const WS_BASE = "ws://localhost:8000/ws";

export interface Task {
  id: string;
  name: string;
  task_type: "highlight_reel" | "interview_compress" | "monologue_clean";
  status: TaskStatus;
  created_at: string;
  updated_at: string;
  video_path?: string;
  video_filename?: string;
  video_duration?: number;
  thumbnail_path?: string;
  asr_result?: ASRResult;
  audit_segments: Segment[];
  export_path?: string;
  jianying_draft_path?: string;
  params: TaskParams;
  error_message?: string;
  original_duration?: number;
  edited_duration?: number;
  segments_kept: number;
  segments_deleted: number;
}

export type TaskStatus =
  | "pending"
  | "asr_running"
  | "asr_done"
  | "audit_running"
  | "review"
  | "export_running"
  | "done"
  | "error";

export interface Segment {
  id: string;
  start: number;
  end: number;
  text: string;
  tagged_text?: string;
  action: "keep" | "delete";
  reason?: string;
  rule?: string;
  speaker?: string;
  confidence?: number;
  user_override?: boolean;
  style?: string;
}

export interface ASRResult {
  words: WordTimestamp[];
  segments: Segment[];
  tagged_script: string;
  duration: number;
  language: string;
  speakers: string[];
}

export interface WordTimestamp {
  word: string;
  start: number;
  end: number;
  speaker?: string;
}

export interface TaskParams {
  silence_threshold: number;
  breath_lead_ms: number;
  breath_tail_ms: number;
  min_segment_duration: number;
  filler_words: string[];
  retake_char_threshold: number;
  style_mode: string;
  enable_diarization: boolean;
  rules_enabled: Record<string, boolean>;
}

export interface LogMessage {
  type: "log" | "status_change" | "export_done" | "connected" | "heartbeat" | "pong";
  timestamp?: string;
  level?: "info" | "warn" | "error" | "success";
  source?: string;
  message?: string;
  progress?: number;
  task_id?: string;
  status?: TaskStatus;
  segments_kept?: number;
  segments_deleted?: number;
  edited_duration?: number;
  output_path?: string;
  draft_path?: string;
  mode?: string;
}

// ============================================================
// Task CRUD
// ============================================================

export async function fetchTasks(): Promise<Task[]> {
  const res = await fetch(`${API_BASE}/tasks`);
  if (!res.ok) throw new Error("Failed to fetch tasks");
  return res.json();
}

export async function fetchTask(taskId: string): Promise<Task> {
  const res = await fetch(`${API_BASE}/tasks/${taskId}`);
  if (!res.ok) throw new Error("Task not found");
  return res.json();
}

export async function createTask(data: {
  name: string;
  task_type: string;
  params?: Partial<TaskParams>;
}): Promise<Task> {
  const res = await fetch(`${API_BASE}/tasks`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error("Failed to create task");
  return res.json();
}

export async function deleteTask(taskId: string): Promise<void> {
  await fetch(`${API_BASE}/tasks/${taskId}`, { method: "DELETE" });
}

// ============================================================
// Video Upload
// ============================================================

export async function uploadVideo(
  taskId: string,
  file: File,
  onProgress?: (progress: number) => void
): Promise<{ success: boolean; duration: number }> {
  return new Promise((resolve, reject) => {
    const formData = new FormData();
    formData.append("file", file);

    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API_BASE}/tasks/${taskId}/upload`);

    if (onProgress) {
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) {
          onProgress(e.loaded / e.total);
        }
      };
    }

    xhr.onload = () => {
      if (xhr.status === 200) {
        resolve(JSON.parse(xhr.responseText));
      } else {
        reject(new Error(`Upload failed: ${xhr.statusText}`));
      }
    };
    xhr.onerror = () => reject(new Error("Upload failed"));
    xhr.send(formData);
  });
}

export function getVideoUrl(taskId: string): string {
  return `${API_BASE}/tasks/${taskId}/video`;
}

export function getThumbnailUrl(taskId: string): string {
  return `${API_BASE}/tasks/${taskId}/thumbnail`;
}

// ============================================================
// Processing
// ============================================================

export async function triggerASR(taskId: string): Promise<void> {
  const res = await fetch(`${API_BASE}/tasks/${taskId}/asr`, { method: "POST" });
  if (!res.ok) throw new Error("Failed to trigger ASR");
}

export async function triggerAudit(
  taskId: string,
  options: { claude_api_key?: string; style_mode?: string }
): Promise<void> {
  const res = await fetch(`${API_BASE}/tasks/${taskId}/audit`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      claude_api_key: options.claude_api_key,
      style_mode: options.style_mode || "immersive",
      claude_model: "claude-3-5-sonnet-20241022",
    }),
  });
  if (!res.ok) throw new Error("Failed to trigger audit");
}

// ============================================================
// Segment Management
// ============================================================

export async function updateSegments(
  taskId: string,
  segments: Segment[]
): Promise<{ kept: number; deleted: number; edited_duration: number }> {
  const res = await fetch(`${API_BASE}/tasks/${taskId}/segments`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ segments }),
  });
  if (!res.ok) throw new Error("Failed to update segments");
  const data = await res.json();
  return data.stats;
}

export async function toggleSegment(
  taskId: string,
  segmentId: string
): Promise<void> {
  const res = await fetch(
    `${API_BASE}/tasks/${taskId}/segments/${segmentId}/toggle`,
    { method: "PUT" }
  );
  if (!res.ok) throw new Error("Failed to toggle segment");
}

// ============================================================
// Export
// ============================================================

export async function exportFFmpeg(
  taskId: string,
  outputName?: string
): Promise<void> {
  const res = await fetch(`${API_BASE}/tasks/${taskId}/export/ffmpeg`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode: "ffmpeg", output_name: outputName }),
  });
  if (!res.ok) throw new Error("Failed to start FFmpeg export");
}

export async function exportJianying(
  taskId: string,
  outputName?: string,
  draftFolder?: string
): Promise<void> {
  const res = await fetch(`${API_BASE}/tasks/${taskId}/export/jianying`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      mode: "jianying",
      output_name: outputName,
      jianying_draft_folder: draftFolder,
    }),
  });
  if (!res.ok) throw new Error("Failed to start JianYing export");
}

// ============================================================
// Config
// ============================================================

export async function getEditingAesthetic(): Promise<string> {
  const res = await fetch(`${API_BASE}/config/editing-aesthetic`);
  if (!res.ok) return "";
  const data = await res.json();
  return data.content;
}

export async function updateEditingAesthetic(content: string): Promise<void> {
  await fetch(`${API_BASE}/config/editing-aesthetic`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
}

// ============================================================
// Prompt Management
// ============================================================

export interface PromptRule {
  code: string;
  name: string;
  priority: number;
  priority_note: string;
  stars: string;
  desc: string;
  full_text: string;
}

export interface PromptData {
  content: string;
  rules: PromptRule[];
}

export type ScenarioKey = "monologue_clean" | "interview_compress" | "highlight_reel";

export async function getPrompt(scenario: ScenarioKey): Promise<PromptData> {
  const res = await fetch(`${API_BASE}/prompts/${scenario}`);
  if (!res.ok) return { content: "", rules: [] };
  return res.json();
}

export async function updatePromptContent(scenario: ScenarioKey, content: string): Promise<PromptData> {
  const res = await fetch(`${API_BASE}/prompts/${scenario}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
  if (!res.ok) throw new Error("Failed to save prompt");
  return res.json();
}

export async function updatePromptRules(scenario: ScenarioKey, rules: PromptRule[]): Promise<void> {
  const res = await fetch(`${API_BASE}/prompts/${scenario}/rules`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ rules }),
  });
  if (!res.ok) throw new Error("Failed to save rules");
}

// ============================================================
// WebSocket
// ============================================================

export function createLogWebSocket(
  taskId: string,
  onMessage: (msg: LogMessage) => void,
  onError?: (err: Event) => void
): WebSocket {
  const ws = new WebSocket(`${WS_BASE}/tasks/${taskId}/log`);

  ws.onmessage = (event) => {
    try {
      const msg = JSON.parse(event.data) as LogMessage;
      onMessage(msg);
    } catch (e) {
      console.error("WS parse error:", e);
    }
  };

  ws.onerror = (err) => {
    console.error("WebSocket error:", err);
    if (onError) onError(err);
  };

  // Heartbeat
  const heartbeatInterval = setInterval(() => {
    if (ws.readyState === WebSocket.OPEN) {
      ws.send("ping");
    } else {
      clearInterval(heartbeatInterval);
    }
  }, 25000);

  ws.onclose = () => clearInterval(heartbeatInterval);

  return ws;
}

// ============================================================
// Helpers
// ============================================================

export function formatDuration(seconds: number): string {
  if (!seconds || isNaN(seconds)) return "0:00";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (h > 0) return `${h}:${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

export function formatTimestamp(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = (seconds % 60).toFixed(3);
  if (h > 0) return `${h}:${m.toString().padStart(2, "0")}:${s.padStart(6, "0")}`;
  return `${m.toString().padStart(2, "0")}:${s.padStart(6, "0")}`;
}

export const STATUS_LABELS: Record<TaskStatus, string> = {
  pending: "待处理",
  asr_running: "ASR 识别中",
  asr_done: "ASR 完成",
  audit_running: "审计中",
  review: "待 Review",
  export_running: "导出中",
  done: "已完成",
  error: "错误",
};

export const STATUS_COLORS: Record<TaskStatus, string> = {
  pending: "text-slate-400 bg-slate-800",
  asr_running: "text-blue-300 bg-blue-900/50",
  asr_done: "text-cyan-300 bg-cyan-900/50",
  audit_running: "text-amber-300 bg-amber-900/50",
  review: "text-yellow-300 bg-yellow-900/50",
  export_running: "text-purple-300 bg-purple-900/50",
  done: "text-green-300 bg-green-900/50",
  error: "text-red-300 bg-red-900/50",
};

export const TASK_TYPE_LABELS: Record<string, string> = {
  highlight_reel: "精彩集锦",
  interview_compress: "访谈压缩",
  monologue_clean: "口播精修",
};

export const SPEAKER_COLORS: Record<string, string> = {
  spk0: "#F0B429",
  spk1: "#60A5FA",
  spk2: "#34D399",
  spk3: "#F472B6",
  default: "#94A3B8",
};
