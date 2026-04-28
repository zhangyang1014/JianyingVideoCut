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
  asr_history: ASRSnapshot[];
  asr_current_version?: number;
  seg_optim_history: SegOptimSnapshot[];
  seg_optim_current_version?: number;
  audit_segments: Segment[];
  audit_history: AuditSnapshot[];
  audit_current_version?: number;
  export_path?: string;
  jianying_draft_path?: string;
  params: TaskParams;
  error_message?: string;
  original_duration?: number;
  edited_duration?: number;
  segments_kept: number;
  segments_deleted: number;
  /** 金句开场：有序 segment_id 列表，导出时放到视频最开头 */
  golden_quote_order: string[];
  /** 一剪多 per-clip hook：{clip首段segment_id → 有序hook_segment_id列表} */
  clip_hook_orders?: Record<string, string[]>;
  /** 用户手动设置的裁剪区间（秒），null 表示不限制 */
  clip_start?: number | null;
  clip_end?: number | null;
  /** 从哪个一剪多任务派生而来（溯源用） */
  source_task_id?: string;
  /** 从一剪多派生精修任务时的原始 clip 标题（封面与简介弹窗预填用） */
  clip_title?: string;
}

/** 金句候选条目 */
export interface GoldenQuoteCandidate {
  segment_id: string;
  text: string;
  start: number;
  end: number;
  reason: string;
}

export type TaskStatus =
  | "pending"
  | "asr_running"
  | "asr_done"
  | "seg_optim_running"
  | "seg_optim_done"
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
  action: "keep" | "delete" | "subtitle_fix" | "text_fix" | "split" | "merge_next";
  reason?: string;
  rule?: string;
  speaker?: string;
  confidence?: number;
  user_override?: boolean;
  style?: string;
  /** 精彩集锦：所属独立 clip 分组编号 */
  clip_group?: number | null;
  /** 精彩集锦：clip 标题，通常只出现在每组首段 */
  clip_title?: string | null;
  /** 精彩集锦：AI 评分（1-10），只出现在每组首段 */
  clip_score?: number | null;
  /** 精彩集锦：AI 选择理由，只出现在每组首段 */
  clip_reason?: string | null;
  words?: WordTimestamp[];
  claude_action?: "keep" | "delete" | "subtitle_fix" | "text_fix" | "split" | "merge_next";
  claude_reason?: string;
  /** subtitle_fix / text_fix 时的字幕展示文本（修正后文本）*/
  display_text?: string;
}

export interface PreprocStats {
  total_words: number;
  filler_count: number;
  stutter_count: number;
  silence_count: number;
  silence_total_s: number;
  segment_count: number;
  speaker_count: number;
}

export interface ASRResult {
  words: WordTimestamp[];
  segments: Segment[];
  tagged_script: string;
  duration: number;
  language: string;
  speakers: string[];
  diar_segments?: Array<{ start: number; end: number; speaker: string }>;
  diar_smooth_threshold?: number;
  preprocess_stats?: PreprocStats;
}

export interface ASRSnapshot {
  version: number;
  created_at: string;
  engine: string;
  segments_count: number;
  duration: number;
  asr_result: ASRResult;
}

export interface AuditSnapshot {
  version: number;
  created_at: string;
  model: string;
  segments_count: number;
  segments_kept: number;
  segments_deleted: number;
}

export interface SegOptimSnapshot {
  version: number;
  created_at: string;
  model: string;
  segments_before: number;
  segments_after: number;
  optim_deleted_count: number;
  optim_deleted_chars: number;
  segments: Segment[];
}

export interface WordTimestamp {
  word: string;
  start: number;
  end: number;
  speaker?: string;
}

export interface SubtitleStyle {
  font_name: string;
  font_size: number;
  primary_color: string;
  outline_color: string;
  outline: number;
  shadow: number;
  bold: boolean;
  alignment: number;
  margin_top: number;
  margin_bottom: number;
  margin_l: number;
  margin_r: number;
  /** 每张字幕卡最多显示行数（0 = 不限制），超出时自动拆成两张字幕卡依次显示 */
  max_lines: number;
  x_pct?: number;  // 0-100，文字中心在视频宽度的百分比位置
  y_pct?: number;  // 0-100，文字中心在视频高度的百分比位置
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
  type: "log" | "status_change" | "export_done" | "export_cancelled" | "connected" | "heartbeat" | "pong" | "claude_failed";
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
  output_files?: string[];
  clips_count?: number;
  draft_path?: string;
  mode?: string;
  error?: string;
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
  theme_id?: string | null;
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
// Theme API
// ============================================================

export interface ThemeMeta {
  id: string;
  name: string;
  desc: string;
  content: string;
}

export async function listThemes(): Promise<ThemeMeta[]> {
  const res = await fetch(`${API_BASE}/themes`);
  if (!res.ok) throw new Error("Failed to list themes");
  return res.json();
}

export async function getTheme(themeId: string): Promise<ThemeMeta> {
  const res = await fetch(`${API_BASE}/themes/${themeId}`);
  if (!res.ok) throw new Error("Failed to get theme");
  return res.json();
}

export async function updateTheme(themeId: string, content: string): Promise<void> {
  const res = await fetch(`${API_BASE}/themes/${themeId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
  if (!res.ok) throw new Error("Failed to update theme");
}

// ============================================================
// Cover Content Prompts
// ============================================================

export interface CoverContentPrompts {
  cover_image: { user_prompt: string };
  cover_titles: { system_prompt: string; user_prompt: string };
  xhs_content: { system_prompt: string; user_prompt: string };
  cover_styles: { emotion: string; info: string; vlog: string };
}

export async function getCoverContentPrompts(): Promise<CoverContentPrompts> {
  const res = await fetch(`${API_BASE}/prompts/cover-content`);
  if (!res.ok) throw new Error("Failed to load cover prompts");
  return res.json();
}

export async function updateCoverContentPrompts(data: CoverContentPrompts): Promise<void> {
  const res = await fetch(`${API_BASE}/prompts/cover-content`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error("Failed to save cover prompts");
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

export async function triggerASR(
  taskId: string,
  model?: string,
  backend?: string,
  silenceThreshold?: number,
  enableDiarization?: boolean,
): Promise<void> {
  const body: Record<string, unknown> = {
    model: model || "base",
    backend: backend || "whisper",
    silence_threshold: silenceThreshold ?? 0.3,
  };
  // 仅在明确传入时才覆盖任务默认值
  if (enableDiarization !== undefined) {
    body.enable_diarization = enableDiarization;
  }
  const res = await fetch(`${API_BASE}/tasks/${taskId}/asr`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error("Failed to trigger ASR");
}

export async function switchASRVersion(taskId: string, version: number): Promise<void> {
  const res = await fetch(`${API_BASE}/tasks/${taskId}/asr/switch`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ version }),
  });
  if (!res.ok) throw new Error("Failed to switch ASR version");
}

export async function switchSegOptimVersion(taskId: string, version: number): Promise<void> {
  const res = await fetch(`${API_BASE}/tasks/${taskId}/seg-optim/switch`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ version }),
  });
  if (!res.ok) throw new Error("Failed to switch seg-optim version");
}

export async function resmooth(
  taskId: string,
  threshold: number,
): Promise<{ message: string; task: Task }> {
  const res = await fetch(`${API_BASE}/tasks/${taskId}/asr/resmooth`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ threshold }),
  });
  if (!res.ok) throw new Error("重新标注说话人失败");
  return res.json();
}

// ── 动态重分段 ──

export interface ResegmentStats {
  total: number;
  avg_chars: number;
  over_limit_count: number;
  over_limit_pct: number;
  max_chars: number;
}

export interface ResegmentResult {
  success: boolean;
  message: string;
  segments: Segment[];
  stats: ResegmentStats;
  recommendation: {
    threshold: number;
    reason: string;
    stats: ResegmentStats;
  } | null;
  task?: Task;
}

export async function resegment(
  taskId: string,
  silenceThreshold: number,
  save: boolean = false,
): Promise<ResegmentResult> {
  const res = await fetch(`${API_BASE}/tasks/${taskId}/asr/resegment`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ silence_threshold: silenceThreshold, save }),
  });
  if (!res.ok) throw new Error("重新分段失败");
  return res.json();
}

export async function triggerSegmentOptimize(
  taskId: string,
  options: {
    provider?: string;
    ollama_model?: string;
    ollama_base_url?: string;
    claude_api_key?: string;
    claude_model?: string;
  }
): Promise<void> {
  const res = await fetch(`${API_BASE}/tasks/${taskId}/optimize-segments`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      provider: options.provider || "ollama",
      ollama_model: options.ollama_model || "deepseek-r1:32b",
      ollama_base_url: options.ollama_base_url || "http://localhost:11434",
      claude_api_key: options.claude_api_key,
      claude_model: options.claude_model || "anthropic/claude-3.5-sonnet",
    }),
  });
  if (!res.ok) throw new Error("Failed to trigger segment optimize");
}

export async function skipSegmentOptimize(taskId: string): Promise<void> {
  const res = await fetch(`${API_BASE}/tasks/${taskId}/skip-optimize-segments`, {
    method: "POST",
  });
  if (!res.ok) throw new Error("Failed to skip segment optimize");
}

export async function recoverTask(taskId: string): Promise<{ success: boolean; recovered_status: string }> {
  const res = await fetch(`${API_BASE}/tasks/${taskId}/recover`, { method: "POST" });
  if (!res.ok) throw new Error("恢复任务失败");
  return res.json();
}

export async function triggerAudit(
  taskId: string,
  options: {
    claude_api_key?: string;
    style_mode?: string;
    claude_model?: string;
    force_rule_engine?: boolean;
    provider?: string;
    ollama_model?: string;
    ollama_base_url?: string;
    highlight_target_dur?: number;
    highlight_max_clips?: number;
    highlight_clip_min_dur?: number;
    highlight_clip_max_dur?: number;
    highlight_total_clips?: number;
  }
): Promise<void> {
  const res = await fetch(`${API_BASE}/tasks/${taskId}/audit`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      claude_api_key: options.claude_api_key,
      style_mode: options.style_mode || "immersive",
      claude_model: options.claude_model || "claude-sonnet-4-6",
      force_rule_engine: options.force_rule_engine ?? false,
      provider: options.provider || "claude",
      ollama_model: options.ollama_model || "deepseek-r1:32b",
      ollama_base_url: options.ollama_base_url || "http://localhost:11434",
      highlight_target_dur: options.highlight_target_dur ?? null,
      highlight_max_clips: options.highlight_max_clips ?? null,
      highlight_clip_min_dur: options.highlight_clip_min_dur ?? null,
      highlight_clip_max_dur: options.highlight_clip_max_dur ?? null,
      highlight_total_clips: options.highlight_total_clips ?? null,
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

export async function patchSegment(
  taskId: string,
  segmentId: string,
  patch: { text?: string; start?: number; end?: number; reason?: string }
): Promise<void> {
  const res = await fetch(
    `${API_BASE}/tasks/${taskId}/segments/${segmentId}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    }
  );
  if (!res.ok) throw new Error("Failed to patch segment");
}

export async function removeSegment(
  taskId: string,
  segmentId: string
): Promise<void> {
  const res = await fetch(
    `${API_BASE}/tasks/${taskId}/segments/${segmentId}`,
    { method: "DELETE" }
  );
  if (!res.ok) throw new Error("Failed to remove segment");
}

export async function mergeSegmentNext(
  taskId: string,
  segmentId: string
): Promise<{ success: boolean; segment: Segment }> {
  const res = await fetch(
    `${API_BASE}/tasks/${taskId}/segments/${segmentId}/merge-next`,
    { method: "POST" }
  );
  if (!res.ok) throw new Error("Failed to merge segment");
  return res.json();
}

export async function batchSegmentAction(
  taskId: string,
  segmentIds: string[],
  action: "keep" | "delete" | "remove"
): Promise<void> {
  const res = await fetch(
    `${API_BASE}/tasks/${taskId}/segments/batch`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ segment_ids: segmentIds, action }),
    }
  );
  if (!res.ok) throw new Error("Failed to batch action segments");
}

// ============================================================
// Clip Range（裁剪区间）
// ============================================================

export async function updateClipRange(
  taskId: string,
  clipStart: number | null,
  clipEnd: number | null,
): Promise<{ clip_start: number | null; clip_end: number | null; stats: Record<string, number> }> {
  const res = await fetch(`${API_BASE}/tasks/${taskId}/clip`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ clip_start: clipStart, clip_end: clipEnd }),
  });
  if (!res.ok) throw new Error("Failed to update clip range");
  return res.json();
}

// ============================================================
// Export
// ============================================================

export async function exportFFmpeg(
  taskId: string,
  outputName?: string,
  burnSubtitles?: boolean,
  subtitleStyle?: SubtitleStyle,
  coverImageBase64?: string,
  coverDuration?: number,
): Promise<void> {
  const body: Record<string, unknown> = {
    mode: "ffmpeg",
    output_name: outputName,
    burn_subtitles: burnSubtitles ?? false,
  };
  if (burnSubtitles && subtitleStyle) {
    body.subtitle_style = subtitleStyle;
  }
  if (coverImageBase64) {
    body.cover_image_base64 = coverImageBase64;
    body.cover_duration = coverDuration ?? 0.04;
  }
  const res = await fetch(`${API_BASE}/tasks/${taskId}/export/ffmpeg`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error("Failed to start FFmpeg export");
}

export async function exportClipPreview(
  taskId: string,
  segmentIds: string[]
): Promise<string> {
  const res = await fetch(`${API_BASE}/tasks/${taskId}/export/clip-preview`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ segment_ids: segmentIds }),
  });
  if (!res.ok) throw new Error("Failed to export clip preview");
  const data = await res.json();
  return `http://localhost:8000${data.url}`;
}

export interface HookConfig {
  enabled: boolean;
  text: string;
  duration: number;
  font_size: number;
  color: string;
  outline_color: string;
  outline: number;
  bold: boolean;
  h_align: "left" | "center" | "right";
  v_align: "top" | "middle" | "bottom";
  x_pct?: number;  // 0-100，文字中心在视频宽度的百分比位置
  y_pct?: number;  // 0-100，文字中心在视频高度的百分比位置
}

export async function exportHighlightClips(
  taskId: string,
  clipGroups: string[][],
  outputName?: string,
  burnSubtitles?: boolean,
  subtitleStyle?: SubtitleStyle,
  hookConfigs?: (HookConfig | null)[],
  hookOrders?: (string[] | null)[],
): Promise<void> {
  const res = await fetch(`${API_BASE}/tasks/${taskId}/export/highlight-clips`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      clip_groups: clipGroups,
      output_name: outputName,
      burn_subtitles: burnSubtitles ?? false,
      subtitle_style: subtitleStyle,
      hook_configs: hookConfigs ?? [],
      hook_orders: hookOrders ?? [],
    }),
  });
  if (!res.ok) throw new Error("Failed to start highlight clips export");
}

export async function cancelExport(taskId: string): Promise<void> {
  const res = await fetch(`${API_BASE}/tasks/${taskId}/export/cancel`, {
    method: "POST",
  });
  if (!res.ok) throw new Error("Failed to cancel export");
}

export async function generateCoverImage(
  taskId: string,
  data: {
    frame_time: number;
    cover_title: string;
    style_prompt?: string;
    img_provider?: string;
    api_key?: string;
  }
): Promise<{ image_base64: string }> {
  const res = await fetch(`${API_BASE}/tasks/${taskId}/generate-cover-image`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error((err as any).detail || "生成失败");
  }
  return res.json();
}

export async function generateCoverTitles(
  taskId: string,
  data: {
    api_key?: string;
    provider?: string;
    claude_model?: string;
    ollama_model?: string;
    ollama_base_url?: string;
  }
): Promise<{ cover_titles: string[] }> {
  const res = await fetch(`${API_BASE}/tasks/${taskId}/generate-cover-titles`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error((err as any).detail || "生成失败");
  }
  return res.json();
}

export async function generateXhsContent(
  taskId: string,
  data: {
    cover_title?: string;
    api_key?: string;
    provider?: string;
    claude_model?: string;
    ollama_model?: string;
    ollama_base_url?: string;
  }
): Promise<{ xhs_titles?: string[]; xhs_title?: string; xhs_description: string }> {
  const res = await fetch(`${API_BASE}/tasks/${taskId}/generate-xhs-content`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error((err as any).detail || "生成失败");
  }
  return res.json();
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
// Prompt Feedback (提示词反馈优化)
// ============================================================

export interface CorrectionEntry {
  id: string;
  text: string;
  start: number;
  end: number;
  claude_action: string;
  claude_reason: string;
  user_action: string;
  rule?: string;
}

export interface CorrectionsResult {
  total_modified: number;
  delete_to_keep: CorrectionEntry[];
  keep_to_delete: CorrectionEntry[];
  other_changes: CorrectionEntry[];
  unchanged: number;
}

export interface PromptFeedbackResult {
  corrections: CorrectionsResult;
  suggestion: string;
  scenario: string;
  style_update?: Record<string, string>;
}

export async function getCorrections(taskId: string): Promise<CorrectionsResult> {
  const res = await fetch(`${API_BASE}/tasks/${taskId}/corrections`);
  if (!res.ok) throw new Error("Failed to get corrections");
  return res.json();
}

export async function submitPromptFeedback(
  taskId: string,
  options: {
    claude_api_key?: string;
    claude_model?: string;
    user_notes?: string;
  }
): Promise<PromptFeedbackResult> {
  const res = await fetch(`${API_BASE}/tasks/${taskId}/prompt-feedback`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      claude_api_key: options.claude_api_key,
      claude_model: options.claude_model || "claude-sonnet-4-6",
      user_notes: options.user_notes || "",
    }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "请求失败" }));
    throw new Error(err.detail || "Failed to submit prompt feedback");
  }
  return res.json();
}

// ============================================================
// 个人剪辑风格档案
// ============================================================

export async function getUserStyle(): Promise<string> {
  const res = await fetch(`${API_BASE}/config/user-style`);
  if (!res.ok) return "";
  const data = await res.json();
  return data.content;
}

export async function updateUserStyle(content: string): Promise<void> {
  await fetch(`${API_BASE}/config/user-style`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
}

export async function adoptStyleUpdate(
  taskId: string,
  styleUpdate: Record<string, string>
): Promise<{ success: boolean; content: string }> {
  const res = await fetch(`${API_BASE}/tasks/${taskId}/adopt-style`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ style_update: styleUpdate }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "请求失败" }));
    throw new Error(err.detail || "Failed to adopt style");
  }
  return res.json();
}

// ============================================================
// 词典纠错
// ============================================================

export async function getGlossary(): Promise<string> {
  const res = await fetch(`${API_BASE}/config/glossary`);
  if (!res.ok) return "";
  const data = await res.json();
  return data.content;
}

export async function deriveRefineTask(
  sourceTaskId: string,
  data: {
    target_type: "monologue_clean" | "interview_compress";
    clip_start: number;
    clip_end: number;
    clip_title?: string;
  }
): Promise<{ task_id: string }> {
  const res = await fetch(`${API_BASE}/tasks/${sourceTaskId}/derive-refine-task`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error("Failed to derive refine task");
  return res.json();
}

export async function updateGlossary(content: string): Promise<void> {
  const res = await fetch(`${API_BASE}/config/glossary`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
  if (!res.ok) throw new Error("Failed to save glossary");
}

// ============================================================
// 学习模块 (Learning Module)
// ============================================================

export type LearningTaskStatus =
  | "pending"
  | "asr_original"
  | "asr_edited"
  | "asr_done"
  | "analyzing"
  | "done"
  | "error";

export interface LearningTask {
  id: string;
  name: string;
  task_type: "highlight_reel" | "interview_compress" | "monologue_clean";
  status: LearningTaskStatus;
  created_at: string;
  updated_at: string;
  original_video_path?: string;
  original_video_filename?: string;
  original_video_duration?: number;
  original_asr_result?: ASRResult;
  edited_video_path?: string;
  edited_video_filename?: string;
  edited_video_duration?: number;
  edited_asr_result?: ASRResult;
  analysis_result?: string;
  new_prompt_content?: string;
  new_prompt_path?: string;
  prompt_version?: number;
  error_message?: string;
}

export interface PromptVersion {
  version: number;
  filename: string;
  path: string;
  size: number;
  modified: number;
  is_base: boolean;
}

export async function createLearningTask(data: {
  name: string;
  task_type: string;
}): Promise<LearningTask> {
  const res = await fetch(`${API_BASE}/learning/tasks`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    let msg = "创建学习任务失败";
    try {
      const body = await res.json();
      const detail = body?.detail;
      if (typeof detail === "string") msg = detail;
      else if (Array.isArray(detail) && detail[0]?.msg) msg = detail[0].msg;
    } catch {
      // 非 JSON 或解析失败时保留默认文案
    }
    throw new Error(msg);
  }
  return res.json();
}

export async function fetchLearningTasks(): Promise<LearningTask[]> {
  const res = await fetch(`${API_BASE}/learning/tasks`);
  if (!res.ok) throw new Error("获取学习任务列表失败");
  return res.json();
}

export async function fetchLearningTask(taskId: string): Promise<LearningTask> {
  const res = await fetch(`${API_BASE}/learning/tasks/${taskId}`);
  if (!res.ok) throw new Error("学习任务不存在");
  return res.json();
}

export async function deleteLearningTask(taskId: string): Promise<void> {
  await fetch(`${API_BASE}/learning/tasks/${taskId}`, { method: "DELETE" });
}

export async function uploadOriginalVideo(
  taskId: string,
  file: File,
  onProgress?: (progress: number) => void
): Promise<{ success: boolean; duration: number; filename: string }> {
  return new Promise((resolve, reject) => {
    const formData = new FormData();
    formData.append("file", file);
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API_BASE}/learning/tasks/${taskId}/upload-original`);
    if (onProgress) {
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) onProgress(e.loaded / e.total);
      };
    }
    xhr.onload = () => {
      if (xhr.status === 200) resolve(JSON.parse(xhr.responseText));
      else reject(new Error(`上传失败: ${xhr.statusText}`));
    };
    xhr.onerror = () => reject(new Error("上传失败"));
    xhr.send(formData);
  });
}

export async function uploadEditedVideo(
  taskId: string,
  file: File,
  onProgress?: (progress: number) => void
): Promise<{ success: boolean; duration: number; filename: string }> {
  return new Promise((resolve, reject) => {
    const formData = new FormData();
    formData.append("file", file);
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API_BASE}/learning/tasks/${taskId}/upload-edited`);
    if (onProgress) {
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) onProgress(e.loaded / e.total);
      };
    }
    xhr.onload = () => {
      if (xhr.status === 200) resolve(JSON.parse(xhr.responseText));
      else reject(new Error(`上传失败: ${xhr.statusText}`));
    };
    xhr.onerror = () => reject(new Error("上传失败"));
    xhr.send(formData);
  });
}

export async function triggerLearningASR(
  taskId: string,
  backend: string = "funasr"
): Promise<void> {
  const res = await fetch(`${API_BASE}/learning/tasks/${taskId}/asr`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ backend, model: "base" }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "ASR 启动失败" }));
    throw new Error(err.detail || "ASR 启动失败");
  }
}

export async function triggerLearningAnalysis(
  taskId: string,
  options: { claude_api_key?: string; claude_model?: string } = {}
): Promise<void> {
  const res = await fetch(`${API_BASE}/learning/tasks/${taskId}/analyze`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      claude_api_key: options.claude_api_key,
      claude_model: options.claude_model || "claude-sonnet-4-6",
    }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "分析启动失败" }));
    throw new Error(err.detail || "分析启动失败");
  }
}

export async function saveLearningPrompt(
  taskId: string,
  content?: string
): Promise<{ success: boolean; path: string; version: number; filename: string }> {
  const res = await fetch(`${API_BASE}/learning/tasks/${taskId}/save-prompt`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
  if (!res.ok) throw new Error("保存提示词失败");
  return res.json();
}

export async function fetchPromptVersions(
  scenario: string
): Promise<{ versions: PromptVersion[]; scenario: string }> {
  const res = await fetch(`${API_BASE}/learning/prompts/${scenario}/versions`);
  if (!res.ok) return { versions: [], scenario };
  return res.json();
}

export const LEARNING_STATUS_LABELS: Record<LearningTaskStatus, string> = {
  pending: "待处理",
  asr_original: "原视频 ASR 中",
  asr_edited: "剪辑视频 ASR 中",
  asr_done: "ASR 完成",
  analyzing: "Claude 分析中",
  done: "分析完成",
  error: "错误",
};

// ============================================================
// WebSocket
// ============================================================

/**
 * 创建带自动重连的 LogWebSocket 代理对象。
 * 后端重启、网络抖动等导致连接断开时，指数退避后自动重连。
 * 返回一个带 close() 方法的代理，调用方用于组件卸载时彻底关闭。
 */
export function createLogWebSocket(
  taskId: string,
  onMessage: (msg: LogMessage) => void,
  onError?: (err: Event) => void
): { close: () => void } {
  let ws: WebSocket | null = null;
  let heartbeatTimer: ReturnType<typeof setInterval> | null = null;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let destroyed = false;
  let retryDelay = 1000; // 初始重连间隔 1s，最大 16s

  function clearTimers() {
    if (heartbeatTimer) { clearInterval(heartbeatTimer); heartbeatTimer = null; }
    if (reconnectTimer) { clearTimeout(reconnectTimer);  reconnectTimer = null; }
  }

  function connect() {
    if (destroyed) return;
    ws = new WebSocket(`${WS_BASE}/tasks/${taskId}/log`);

    ws.onopen = () => {
      retryDelay = 1000; // 连接成功后重置退避间隔
      // Heartbeat：每 25s 发一次 ping
      heartbeatTimer = setInterval(() => {
        if (ws?.readyState === WebSocket.OPEN) {
          ws.send("ping");
        }
      }, 25000);
    };

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data) as LogMessage;
        onMessage(msg);
      } catch (e) {
        console.error("WS parse error:", e);
      }
    };

    ws.onerror = (err) => {
      if (onError) onError(err);
    };

    ws.onclose = () => {
      clearTimers();
      if (!destroyed) {
        // 指数退避重连（1s → 2s → 4s → 8s → 16s 上限）
        reconnectTimer = setTimeout(() => {
          retryDelay = Math.min(retryDelay * 2, 16000);
          connect();
        }, retryDelay);
      }
    };
  }

  connect();

  return {
    close() {
      destroyed = true;
      clearTimers();
      ws?.close();
    },
  };
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
  asr_running: "识别中",
  asr_done: "识别完成",
  seg_optim_running: "段落优化中",
  seg_optim_done: "优化完成",
  audit_running: "AI 审计中",
  review: "待剪辑审核",
  export_running: "导出中",
  done: "已完成",
  error: "出错",
};

export const STATUS_COLORS: Record<TaskStatus, string> = {
  pending: "text-slate-400 bg-slate-800",
  asr_running: "text-blue-300 bg-blue-900/50",
  asr_done: "text-cyan-300 bg-cyan-900/50",
  seg_optim_running: "text-sky-300 bg-sky-900/50",
  seg_optim_done: "text-sky-300 bg-sky-900/50",
  audit_running: "text-amber-300 bg-amber-900/50",
  review: "text-yellow-300 bg-yellow-900/50",
  export_running: "text-purple-300 bg-purple-900/50",
  done: "text-green-300 bg-green-900/50",
  error: "text-red-300 bg-red-900/50",
};

export const TASK_TYPE_LABELS: Record<string, string> = {
  highlight_reel: "精彩集锦",
  interview_compress: "直播访谈压缩",
  monologue_clean: "口播精修",
};

export const SPEAKER_COLORS: Record<string, string> = {
  spk0: "#E8764A",
  spk1: "#0EA5E9",
  spk2: "#A78BFA",
  spk3: "#F472B6",
  default: "#94A3B8",
};

// ============================================================
// 离线 AI 手动模式 API
// ============================================================

export interface ManualPromptPack {
  system_prompt: string;
  user_prompt: string;
  combined: string;
  idx_to_id?: Record<number, string>;
}

export async function getLearningManualPromptStep1(taskId: string): Promise<ManualPromptPack> {
  const res = await fetch(`${API_BASE}/learning/tasks/${taskId}/manual-prompt/step1`);
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "获取第一步提示词失败");
  }
  return res.json();
}

export async function submitLearningManualStep1(taskId: string, analysisResult: string): Promise<void> {
  const res = await fetch(`${API_BASE}/learning/tasks/${taskId}/manual-result/step1`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ analysis_result: analysisResult }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "提交第一步结果失败");
  }
}

export async function getLearningManualPromptStep2(taskId: string): Promise<ManualPromptPack> {
  const res = await fetch(`${API_BASE}/learning/tasks/${taskId}/manual-prompt/step2`);
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "获取第二步提示词失败");
  }
  return res.json();
}

export async function submitLearningManualStep2(taskId: string, changesJson: string): Promise<void> {
  const res = await fetch(`${API_BASE}/learning/tasks/${taskId}/manual-result/step2`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ changes_json: changesJson }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "提交第二步结果失败");
  }
}

export async function getManualAuditPrompt(taskId: string): Promise<ManualPromptPack> {
  const res = await fetch(`${API_BASE}/tasks/${taskId}/manual-audit-prompt`);
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "获取审计提示词失败");
  }
  return res.json();
}

export async function submitManualAuditResult(taskId: string, auditJson: string): Promise<{
  success: boolean;
  message: string;
  segments_kept: number;
  segments_deleted: number;
  edited_duration: number;
}> {
  const res = await fetch(`${API_BASE}/tasks/${taskId}/manual-audit-result`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ audit_json: auditJson }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "提交审计结果失败");
  }
  return res.json();
}

// ============================================================
// 金句开场
// ============================================================

export interface GoldenQuoteSuggestOptions {
  provider?: "claude" | "ollama";
  claude_api_key?: string;
  claude_model?: string;
  use_openrouter?: boolean;
  ollama_model?: string;
  ollama_base_url?: string;
  /** 只在指定 segment 范围内分析（per-clip hook 场景） */
  segment_ids?: string[];
}

/**
 * 调用 AI 分析当前任务的保留片段，推荐最多 5 个适合做开场钩子的金句候选。
 * 同步接口，约 5-15 秒返回。
 */
export async function suggestGoldenQuotes(
  taskId: string,
  options: GoldenQuoteSuggestOptions = {},
): Promise<GoldenQuoteCandidate[]> {
  const res = await fetch(`${API_BASE}/tasks/${taskId}/golden-quotes/suggest`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      provider: options.provider || "claude",
      claude_api_key: options.claude_api_key,
      claude_model: options.claude_model || "claude-3-5-haiku-20241022",
      use_openrouter: options.use_openrouter || false,
      ollama_model: options.ollama_model || "deepseek-r1:14b",
      ollama_base_url: options.ollama_base_url || "http://localhost:11434",
      segment_ids: options.segment_ids || [],
    }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "金句分析失败");
  }
  const data = await res.json();
  return data.candidates as GoldenQuoteCandidate[];
}

/**
 * 保存一剪多各 clip 的 per-clip hook 配置。
 * clipHooks: {clip首段segment_id → 有序hook_segment_id列表}，空数组表示清除该 clip 的 hook。
 */
export async function saveClipHookOrders(
  taskId: string,
  clipHooks: Record<string, string[]>,
): Promise<void> {
  const res = await fetch(`${API_BASE}/tasks/${taskId}/clip-hooks`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ clip_hooks: clipHooks }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "保存 clip hook 失败");
  }
}

/**
 * 保存用户选定并排序后的金句片段 ID 列表。
 * 导出时这些片段将被放在视频最开头。传空数组可清除设置。
 */
export async function saveGoldenQuoteOrder(
  taskId: string,
  segmentIds: string[],
): Promise<{ success: boolean; golden_quote_order: string[]; count: number }> {
  const res = await fetch(`${API_BASE}/tasks/${taskId}/golden-quotes/order`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ segment_ids: segmentIds }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "保存金句顺序失败");
  }
  return res.json();
}
