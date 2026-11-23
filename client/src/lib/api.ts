/**
 * GoldenClip API Client
 * Design: 暗金剪辑台 · 编导美学
 * Communicates with FastAPI through the Vite dev proxy.
 */

const API_BASE = "/api";
export * from "./productions-api";
export const DEFAULT_AUDIT_MODEL = "qwen/qwen3.7-plus";
export const DEFAULT_GOLDEN_QUOTE_MODEL = "deepseek/deepseek-v3.2";
const DEFAULT_LEARNING_MODEL = "qwen/qwen3.7-plus";
const REVIEW_APPROVAL_TOKEN = "review-workbench";

function getWsBase(): string {
  if (typeof window === "undefined") return "ws://localhost:5177/ws";
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}/ws`;
}

function backendUnavailableError(): Error {
  return new Error("后端服务未启动或无法访问，请确认本地服务已启动后刷新页面。");
}

async function apiFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  try {
    const response = await fetch(input, init);
    return response;
  } catch (error) {
    if (error instanceof TypeError && error.message === "Failed to fetch") {
      throw backendUnavailableError();
    }
    throw error;
  }
}

async function readApiError(res: Response, fallback: string): Promise<Error> {
  try {
    const data = await res.json();
    if (typeof data?.detail === "string") return new Error(data.detail);
    if (typeof data?.error === "string") return new Error(data.error);
  } catch {
    // Ignore JSON parse failures and use fallback.
  }
  return new Error(fallback);
}

export interface Task {
  id: string;
  name: string;
  task_type: "highlight_reel" | "interview_compress" | "monologue_clean" | "one_click_translate";
  status: TaskStatus;
  created_at: string;
  updated_at: string;
  revision?: number;
  video_path?: string;
  video_filename?: string;
  video_duration?: number;
  thumbnail_path?: string;
  asr_result?: ASRResult;
  asr_history: ASRSnapshot[];
  asr_current_version?: number;
  analysis_history?: AnalysisSnapshot[];
  analysis_current_version?: number;
  resegment_inheritance_report?: ResegmentInheritanceReport | null;
  audit_segments: Segment[];
  audit_history: AuditSnapshot[];
  audit_current_version?: number;
  content_revision: number;
  subtitle_revision?: number;
  last_subtitle_optimization?: Record<string, unknown> | null;
  highlight_revision: number;
  export_path?: string;
  jianying_draft_path?: string;
  export_jobs?: ExportJob[];
  latest_export_job_id?: string | null;
  params: TaskParams;
  error_message?: string;
  original_duration?: number;
  edited_duration?: number;
  segments_kept: number;
  segments_deleted: number;
  /** 金句开场：有序 segment_id 列表，导出时放到视频最开头 */
  golden_quote_order: string[];
  /** 用户手动设置的裁剪区间（秒），null 表示不限制 */
  clip_start?: number | null;
  clip_end?: number | null;
  one_click_translate_config?: OneClickTranslateConfig | null;
  translation_videos: TranslationVideo[];
  /** 直播卖货集锦：后端 canonical 多产品集锦计划 */
  highlight_reels?: HighlightReel[];
  /** 直播卖货集锦：按直播中产品出现顺序识别的场次。 */
  product_occurrences?: ProductOccurrence[];
  /** 直播卖货集锦：同一产品跨场次归并后的全局产品组。 */
  product_groups?: ProductGroup[];
  /** 直播卖货集锦：模型质检前的原始生成结果 */
  highlight_reels_before_quality?: HighlightReel[];
  /** 直播卖货集锦：最近一次质检报告 */
  highlight_quality_report?: HighlightReelQualityReport | null;
  /** 直播卖货集锦：质检前版本的首次验收报告 */
  highlight_quality_report_before_quality?: HighlightReelQualityReport | null;
  /** 审计后全文连贯性复核报告 */
  audit_coherence_report?: AuditCoherenceReport | null;
  /** 当前任务执行代次；由后端统一执行协议维护。 */
  execution_generation?: number;
  active_execution_run_id?: string | null;
  /** 任务详情返回完整执行历史（包含阶段 checkpoint）。 */
  execution_runs?: ExecutionRun[];
  /** 任务列表仅返回最新执行摘要（不包含 stages）。 */
  execution_run?: ExecutionRunSummary | null;
}

/** 看板专用轻量任务摘要；重型 ASR/审计/集锦产物只由详情接口返回。 */
export type TaskSummary = Pick<
  Task,
  | "id"
  | "name"
  | "task_type"
  | "status"
  | "created_at"
  | "updated_at"
  | "video_path"
  | "video_filename"
  | "video_duration"
  | "thumbnail_path"
  | "original_duration"
  | "edited_duration"
  | "segments_kept"
  | "segments_deleted"
  | "error_message"
  | "execution_run"
> & {
  revision: number;
};

export type ExecutionStatus =
  | "pending"
  | "running"
  | "retry_wait"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "interrupted";

export interface ExecutionError {
  error_type: string;
  message: string;
  retryable: boolean;
  on_fail?: string | null;
  status_code?: number | null;
  retry_after_seconds?: number | null;
  attempt?: number | null;
  occurred_at?: string;
}

export interface ExecutionRetryState {
  retries_used: number;
  max_retries: number;
  next_retry_at?: string | null;
  last_delay_seconds?: number | null;
}

export interface StageCheckpoint {
  stage: string;
  status: ExecutionStatus;
  attempts: number;
  artifact_ref?: string | null;
  artifact_digest?: string | null;
  error?: ExecutionError | null;
  started_at?: string | null;
  completed_at?: string | null;
  updated_at: string;
}

export interface ExecutionRunSummary {
  run_id: string;
  generation: number;
  operation: string;
  idempotency_key?: string | null;
  input_fingerprint?: string | null;
  status: ExecutionStatus;
  current_stage?: string | null;
  retry_state: ExecutionRetryState;
  error?: ExecutionError | null;
  recoverable: boolean;
  created_at: string;
  started_at?: string | null;
  completed_at?: string | null;
  updated_at: string;
}

export interface ExecutionRun extends ExecutionRunSummary {
  stages: StageCheckpoint[];
}

export function getTaskExecutionRun(task?: Task | null): ExecutionRun | ExecutionRunSummary | null {
  if (!task) return null;
  const runs = task.execution_runs ?? [];
  if (runs.length > 0) {
    if (task.active_execution_run_id) {
      const active = runs.find((run) => run.run_id === task.active_execution_run_id);
      if (active) return active;
    }
    return runs[runs.length - 1];
  }
  return task.execution_run ?? null;
}

/** 只返回后端明确标记为当前活动的执行，绝不回退到最后一条历史记录。 */
export function getTaskActiveExecutionRun(task?: Task | null): ExecutionRun | null {
  if (!task?.active_execution_run_id) return null;
  return (task.execution_runs ?? []).find(
    (run) => run.run_id === task.active_execution_run_id,
  ) ?? null;
}

export type ReviewExecutionActivity = "audit" | "highlight" | null;

/**
 * 将共享的 audit_running 状态进一步拆成独立的审计/集锦活动。
 * operation 是主判据；highlight_auto_run 才根据阶段在两个步骤间切换。
 */
export function classifyReviewExecutionActivity(
  operation?: string | null,
  stage?: string | null,
): ReviewExecutionActivity {
  const normalizedOperation = (operation ?? "").toLowerCase();
  const normalizedStage = (stage ?? "").toLowerCase();

  if (normalizedOperation === "audit") return "audit";
  if (normalizedOperation === "highlight_rebuild") return "highlight";
  if (normalizedOperation === "highlight_auto_run") {
    return (
      normalizedStage.startsWith("reel_")
      || normalizedStage.includes("highlight_reel")
      || normalizedStage.includes("product_strategy")
    )
      ? "highlight"
      : "audit";
  }
  if (normalizedStage.startsWith("reel_")) return "highlight";
  return null;
}

export function getExecutionCheckpointCount(run?: ExecutionRun | ExecutionRunSummary | null): number {
  if (!run || !("stages" in run)) return 0;
  return run.stages.filter((checkpoint) => checkpoint.status === "succeeded").length;
}

export function getExecutionTotalAttempts(run?: ExecutionRun | ExecutionRunSummary | null): number {
  return (run?.retry_state.max_retries ?? 3) + 1;
}

export function getExecutionRetrySeconds(
  run?: ExecutionRun | ExecutionRunSummary | null,
  nowMs = Date.now(),
): number | null {
  const retryAt = run?.retry_state.next_retry_at;
  if (!retryAt) return null;
  const retryAtMs = new Date(retryAt).getTime();
  if (!Number.isFinite(retryAtMs)) return null;
  return Math.max(0, Math.ceil((retryAtMs - nowMs) / 1000));
}

export function isExecutionRetrying(run?: ExecutionRun | ExecutionRunSummary | null): boolean {
  return run?.status === "retry_wait";
}

export function isExecutionFailureFinal(run?: ExecutionRun | ExecutionRunSummary | null): boolean {
  if (!run || (run.status !== "failed" && run.status !== "interrupted")) return false;
  const retriesExhausted = run.retry_state.retries_used >= run.retry_state.max_retries;
  return run.error?.retryable === false || retriesExhausted || run.status === "interrupted";
}

export function canResumeExecution(run?: ExecutionRun | ExecutionRunSummary | null): boolean {
  return Boolean(run && isExecutionFailureFinal(run) && run.recoverable);
}

export type HighlightReelStatus =
  | "pending"
  | "passed"
  | "warning"
  | "failed"
  | "exported"
  | "export_failed";
export type HighlightQualityStatus = "pending" | "passed" | "warning" | "failed";
export type HighlightExportStatus =
  | "not_exported"
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled";

export type ExportJobStatus =
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled";

export type ExportJobKind = "ffmpeg" | "jianying" | "highlight_reel";
export type RenderProfile = "draft" | "preview" | "final";
/** 集锦质检流程阶段；与忠实/均衡/精简创意策略是两个独立维度。 */
export type HighlightQualityStage = "before_quality" | "after_quality";
/** @deprecated 使用 HighlightQualityStage。保留给旧前端调用和旧后端字段。 */
export type HighlightReelVariant = HighlightQualityStage;
export type HighlightStrategyKind = "faithful" | "balanced" | "concise";

export interface ProductOccurrence {
  occurrence_id: string;
  name?: string | null;
  /** 兼容早期 API 草案。 */
  product_name?: string | null;
  aliases?: string[];
  start?: number | null;
  end?: number | null;
  segment_ids?: string[];
  evidence?: string[];
  confidence?: number | null;
  source?: string;
  occurrence_index?: number | null;
}

export interface ProductGroup {
  product_group_id: string;
  canonical_name: string;
  aliases: string[];
  occurrence_ids: string[];
  segment_ids: string[];
  start: number;
  end: number;
  confidence?: number | null;
  evidence?: string[];
}

export interface RenderQualityIssue {
  code: string;
  severity: "error" | "warning" | "info";
  message: string;
}

export interface RenderQualityReport {
  passed: boolean;
  expected_duration?: number | null;
  actual_duration?: number | null;
  video_stream_present: boolean;
  audio_stream_present: boolean;
  issues: RenderQualityIssue[];
  checked_at?: string | null;
}

export interface ExportJob {
  id: string;
  task_id: string;
  kind: ExportJobKind;
  render_profile: RenderProfile;
  status: ExportJobStatus;
  reel_id?: string | null;
  /** @deprecated 旧后端字段；等价于 quality_stage。 */
  reel_variant: HighlightReelVariant;
  source_content_revision: number;
  source_highlight_revision: number;
  source_subtitle_revision?: number;
  request: Record<string, unknown>;
  snapshot: Record<string, unknown>;
  output_path?: string | null;
  output_url?: string | null;
  output_files: string[];
  draft_path?: string | null;
  srt_path?: string | null;
  progress?: number | null;
  error_message?: string | null;
  render_fingerprint?: string | null;
  quality_report?: RenderQualityReport | null;
  created_at: string;
  started_at?: string | null;
  completed_at?: string | null;
  updated_at: string;
}

export interface HighlightReelIssue {
  code: string;
  severity: "blocker" | "major" | "minor";
  message: string;
  reel_id?: string | null;
  suggestion?: string | null;
}

export interface HighlightReelRepairAction {
  action: string;
  reel_id?: string | null;
  segment_ids: string[];
  reason: string;
}

export interface HighlightReel {
  reel_id: string;
  clip_group: number;
  occurrence_id?: string | null;
  /** 新任务按全局产品归组；旧任务缺失时继续使用 occurrence_id。 */
  product_group_id?: string | null;
  /** 最终 winner 所汇总的源产品场次。 */
  source_occurrence_ids?: string[];
  strategy_kind?: HighlightStrategyKind | null;
  retention_ratio?: number | null;
  content_retention_ratio?: number | null;
  source_duration?: number | null;
  source_segment_count?: number | null;
  selected_selling_points?: string[];
  title: string;
  segment_ids: string[];
  start: number;
  end: number;
  duration: number;
  hook_segment_id?: string | null;
  rationale?: string | null;
  product_name?: string | null;
  angle_label?: string | null;
  semantic_roles?: string[];
  score?: number | null;
  status: HighlightReelStatus;
  quality_status?: HighlightQualityStatus | null;
  export_status?: HighlightExportStatus;
  issues: HighlightReelIssue[];
  created_at?: string | null;
  export_path?: string | null;
  source_content_revision?: number | null;
  exported_subtitle_revision?: number | null;
  exported_revision?: number | null;
  exported_job_id?: string | null;
  hidden: boolean;
}

export interface HighlightReelQualityReport {
  passed: boolean;
  score?: number | null;
  issues: HighlightReelIssue[];
  validated_at?: string | null;
  validator_model?: string | null;
  proposed_repair_actions?: HighlightReelRepairAction[];
  repair_actions?: HighlightReelRepairAction[];
  repair_count?: number;
  degraded?: boolean;
  quality_stage?: HighlightQualityStage;
  /** @deprecated 旧后端字段；等价于 quality_stage。 */
  variant?: HighlightReelVariant;
}

export interface AuditCoherenceIssue {
  code: string;
  severity: "blocker" | "major" | "minor";
  message: string;
  segment_id?: string | null;
  suggestion?: string | null;
}

export interface AuditCoherenceTermFix {
  source: string;
  target: string;
  segment_ids: string[];
}

export interface AuditCoherenceReport {
  passed: boolean;
  score?: number | null;
  issues: AuditCoherenceIssue[];
  term_fixes: AuditCoherenceTermFix[];
  repaired_count: number;
  validated_at?: string | null;
  validator_model?: string | null;
}

export interface SourceWordRange {
  segment_id: string;
  start_word_index?: number | null;
  end_word_index?: number | null;
}

export interface ProductEntity {
  name: string;
  canonical_name?: string | null;
  category?: string | null;
}

export interface ProductSemanticMark {
  roles: string[];
  product_entities: ProductEntity[];
  selling_points: string[];
  angle_id?: string | null;
  angle_label?: string | null;
  eligible_for_highlight: boolean;
  exclude_reason?: string | null;
  confidence?: number | null;
  source?: string;
}

export interface AuditEditOperation {
  id: string;
  op: "keep_span" | "delete_span" | "delete_words" | "split_at" | "merge_span" | "text_fix" | "subtitle_line_break";
  source_id?: string | null;
  source_ids: string[];
  source_word_ranges: SourceWordRange[];
  split_before?: string | null;
  split_part1_action?: "keep" | "delete";
  split_part2_action?: "keep" | "delete";
  display_text?: string | null;
  subtitle_line_break?: string | null;
  reason?: string;
  text_role?: string | null;
  semantic_unit_id?: string | null;
  product_semantic?: ProductSemanticMark | null;
  clip_group?: number | null;
  clip_title?: string | null;
  style?: string | null;
}

export interface AuditEditPlan {
  operations: AuditEditOperation[];
  term_fixes: Array<Record<string, unknown>>;
  coherence_notes: string[];
  target_retention?: number | null;
  validator_model?: string | null;
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
  | "asr_recognized"
  | "diarization_running"
  | "asr_diarized"
  | "analyze_running"
  | "asr_done"
  | "audit_running"
  | "translate_running"
  | "tts_running"
  | "dubbing_running"
  | "review"
  | "export_running"
  | "done"
  | "error";

export interface Segment {
  word_deletions?: SourceWordRange[];
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
  /** 最近一次人工操作；仅由用户实际编辑时写入。 */
  manual_action?: "delete" | "restore" | "adjust" | "merge" | "split" | "word_delete";
  style?: string;
  /** 直播卖货集锦：所属独立产品 clip 分组编号 */
  clip_group?: number | null;
  /** 直播卖货集锦：clip 标题，通常只出现在每组首段 */
  clip_title?: string | null;
  words?: WordTimestamp[];
  claude_action?: "keep" | "delete" | "subtitle_fix" | "text_fix" | "split" | "merge_next";
  claude_reason?: string;
  /** subtitle_fix / text_fix 时的字幕展示文本（修正后文本）*/
  display_text?: string;
  /** 用户/字幕优化最终覆盖层，不改变原始 text/words/审计决策 */
  subtitle_text?: string | null;
  subtitle_text_source?: "manual" | "optimizer" | null;
  subtitle_updated_at?: string | null;
  /** 后端已将本段与后续片段合并为一张审核卡 */
  merged?: boolean;
  /** 分析阶段发现的可选编辑，需审计或人工采纳后才影响导出 */
  candidate_edits?: CandidateEdit[];
  /** 自由断句语义稿来源追踪 */
  source_segment_ids?: string[];
  source_word_ranges?: SourceWordRange[];
  compiled_from_operation?: string | null;
  semantic_unit_id?: string | null;
  /** AI 审计产出的句子级产品理解，供集锦规划消费 */
  product_semantic?: ProductSemanticMark | null;
}

export interface CandidateEdit {
  id: string;
  source: string;
  edit_type: "delete_range" | "subtitle_fix" | "normalize_text" | "info" | string;
  start: number;
  end: number;
  text: string;
  reason: string;
  confidence?: number | null;
  segment_id?: string | null;
  word_index?: number | null;
  status: "candidate" | "accepted" | "rejected" | "superseded" | string;
  metadata?: Record<string, unknown>;
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
  analysis_report?: ASRAnalysisReport | null;
  candidate_edits?: CandidateEdit[];
}

export interface ASRAnalysisStep {
  key: string;
  title: string;
  description: string;
  enabled: boolean;
  input_count?: number;
  output_count?: number;
  changed_count?: number;
  note?: string;
}

export interface ASRAnalysisSegmentNote {
  segment_id: string;
  start: number;
  end: number;
  text: string;
  reasons: string[];
}

export interface ASRAnalysisReport {
  settings: Record<string, unknown>;
  stats: Record<string, number | string | boolean | null>;
  steps: ASRAnalysisStep[];
  rule_events?: Array<Record<string, unknown>>;
  segment_notes: ASRAnalysisSegmentNote[];
}

export interface ASRSnapshot {
  version: number;
  created_at: string;
  engine: string;
  segments_count: number;
  duration: number;
  /** 任务详情接口只返回历史元数据；完整结果保留在后端并由版本切换接口读取。 */
  asr_result?: ASRResult;
}

export interface AnalysisSnapshot {
  version: number;
  created_at: string;
  action: "text_organize" | "resegment" | string;
  silence_threshold: number;
  segments_count: number;
  candidate_edits_count: number;
  stats: Record<string, unknown>;
  analysis_report?: ASRAnalysisReport | null;
  segments: Segment[];
}

export interface AuditSnapshot {
  version: number;
  created_at: string;
  model: string;
  segments_count: number;
  segments_kept: number;
  segments_deleted: number;
}

export interface WordTimestamp {
  word: string;
  start: number;
  end: number;
  speaker?: string;
}

export function getEffectiveSubtitleText(segment: Segment): string {
  return (segment.subtitle_text || segment.display_text || segment.text || "").trim();
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
}

export interface TaskParams {
  reference_script?: string;
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

export interface LLMModelConfig {
  provider: string;
  model: string;
  base_url?: string | null;
  use_openrouter?: boolean;
}

export interface TTSModelConfig {
  provider: string;
  model: string;
  voice: string;
  base_url?: string | null;
}

export interface OneClickTranslateConfig {
  source_language: string;
  target_language: string;
  subtitle_model: LLMModelConfig;
  tts_model: TTSModelConfig;
}

export interface TranslationCue {
  id: string;
  source_segment_id: string;
  start: number;
  end: number;
  source_text: string;
  calibrated_text?: string | null;
  translated_text?: string | null;
  audio_path?: string | null;
  audio_duration?: number | null;
  audio_text?: string | null;
  status: string;
  error_message?: string | null;
}

export interface TranslationVideo {
  id: string;
  filename: string;
  video_path: string;
  duration?: number | null;
  thumbnail_path?: string | null;
  // uploaded | asr_running | asr_done | translate_running | translated
  // | tts_running | dubbing_running | ready_preview | done | error
  status: string;
  asr_result?: ASRResult | null;
  cues: TranslationCue[];
  source_srt_path?: string | null;
  calibrated_srt_path?: string | null;
  translated_srt_path?: string | null;
  aligned_audio_path?: string | null;
  preview_path?: string | null;
  export_path?: string | null;
  error_message?: string | null;
}

export interface LogMessage {
  type:
    | "log"
    | "status_change"
    | "export_done"
    | "export_cancelled"
    | "export_job_created"
    | "export_job_started"
    | "export_job_progress"
    | "export_job_succeeded"
    | "export_job_failed"
    | "export_job_cancelled"
    | "task_cancelled"
    | "connected"
    | "heartbeat"
    | "pong"
    | "claude_failed"
    | "highlight_reels_ready";
  timestamp?: string;
  level?: "info" | "warn" | "error" | "success";
  source?: string;
  message?: string;
  progress?: number;
  stage?: string;
  task_id?: string;
  status?: TaskStatus | ExportJobStatus;
  export_job_id?: string;
  job?: ExportJob;
  segments_kept?: number;
  segments_deleted?: number;
  edited_duration?: number;
  reels_count?: number;
  quality_passed?: boolean;
  output_path?: string;
  output_files?: string[];
  clips_count?: number;
  draft_path?: string;
  mode?: string;
  error?: string;
  error_type?: string;
}

// ============================================================
// Task CRUD
// ============================================================

export async function fetchTasks(signal?: AbortSignal): Promise<TaskSummary[]> {
  const res = await apiFetch(`${API_BASE}/tasks`, { signal });
  if (!res.ok) throw new Error("Failed to fetch tasks");
  return res.json();
}

export async function fetchTask(taskId: string): Promise<Task> {
  const res = await apiFetch(`${API_BASE}/tasks/${taskId}`);
  if (!res.ok) throw new Error("Task not found");
  return res.json();
}

export interface FeatureUsageSummary {
  feature: string;
  label: string;
  call_count: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  cost_usd: string;
  unknown_cost_count: number;
  actual_cost_count: number;
  estimated_cost_count: number;
}

export interface TaskUsageSummary {
  task_id: string;
  currency: "USD";
  cost_usd: string;
  call_count: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  unknown_cost_count: number;
  actual_cost_count: number;
  estimated_cost_count: number;
  tracking_started_at?: string | null;
  features: FeatureUsageSummary[];
}

export interface ModelCallUsage {
  input_tokens?: number | null;
  output_tokens?: number | null;
  reasoning_tokens?: number | null;
  cached_input_tokens?: number | null;
  total_tokens?: number | null;
}

export interface ModelCallRecord {
  call_id: string;
  task_id: string;
  execution_run_id?: string | null;
  feature: string;
  role?: string | null;
  stage?: string | null;
  provider: string;
  requested_model: string;
  actual_model: string;
  tier?: string | null;
  attempt: number;
  status: "success" | "failed" | "quality_gate_failed" | "cache_hit";
  error_type?: string | null;
  started_at: string;
  completed_at: string;
  elapsed_ms: number;
  from_cache: boolean;
  usage: ModelCallUsage;
  currency: "USD";
  cost_usd?: string | null;
  cost_source: "provider" | "catalog" | "manual" | "unavailable" | "cache";
}

export interface ModelCallList {
  items: ModelCallRecord[];
  next_cursor?: string | null;
}

export async function fetchTaskUsage(taskId: string): Promise<TaskUsageSummary> {
  const res = await apiFetch(`${API_BASE}/tasks/${taskId}/usage`);
  if (!res.ok) throw await readApiError(res, "资金消耗统计加载失败");
  return res.json();
}

export async function fetchTaskUsageCalls(
  taskId: string,
  options: { feature?: string; cursor?: string; limit?: number } = {},
): Promise<ModelCallList> {
  const params = new URLSearchParams();
  if (options.feature) params.set("feature", options.feature);
  if (options.cursor) params.set("cursor", options.cursor);
  params.set("limit", String(options.limit ?? 50));
  const res = await apiFetch(`${API_BASE}/tasks/${taskId}/usage/calls?${params}`);
  if (!res.ok) throw await readApiError(res, "模型调用明细加载失败");
  return res.json();
}

export interface TaskRuntimeHealth {
  task_id: string;
  status: TaskStatus;
  effective_status: TaskStatus;
  expected_running: boolean;
  alive: boolean;
  runner_alive: boolean;
  export_task_alive: boolean;
  ffmpeg_process_alive: boolean;
  checked_at: string;
  message: string;
}

export async function fetchTaskRuntimeHealth(taskId: string): Promise<TaskRuntimeHealth> {
  const res = await apiFetch(`${API_BASE}/tasks/${taskId}/runtime-health`);
  if (!res.ok) throw await readApiError(res, "运行态自检失败");
  return res.json();
}

export async function createTask(data: {
  name: string;
  task_type: string;
  params?: Partial<TaskParams>;
}): Promise<Task> {
  const res = await apiFetch(`${API_BASE}/tasks`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error("Failed to create task");
  return res.json();
}

export interface ScriptCandidate {
  segment_ids: string[];
  start: number;
  end: number;
  text: string;
  similarity: number;
  coverage: number;
  state: "kept" | "mixed" | "deleted";
  warnings: string[];
}

export interface ReferenceScriptResult {
  reference_script: string;
  report: {
    script_digest: string;
    lines: { index: number; text: string; status: "matched" | "uncertain" | "missing"; retained: boolean; candidates: ScriptCandidate[] }[];
    matched: number;
    uncertain: number;
    missing: number;
    retained: number;
    truncated: boolean;
  };
}

export async function fetchReferenceScript(taskId: string): Promise<ReferenceScriptResult> {
  const res = await apiFetch(`${API_BASE}/tasks/${taskId}/reference-script`);
  if (!res.ok) throw await readApiError(res, "原稿匹配失败");
  return res.json();
}

export async function saveReferenceScript(taskId: string, referenceScript: string, previousScript: string): Promise<ReferenceScriptResult> {
  const res = await apiFetch(`${API_BASE}/tasks/${taskId}/reference-script`, {
    method: "PUT", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reference_script: referenceScript, previous_script: previousScript }),
  });
  if (!res.ok) throw await readApiError(res, "原稿保存失败");
  return res.json();
}

export async function deleteTask(taskId: string): Promise<void> {
  await apiFetch(`${API_BASE}/tasks/${taskId}`, { method: "DELETE" });
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
    xhr.onerror = () => {
      reject(backendUnavailableError());
    };
    xhr.send(formData);
  });
}

export async function uploadTranslationVideo(
  taskId: string,
  file: File,
  onProgress?: (progress: number) => void
): Promise<{ success: boolean; video: TranslationVideo }> {
  return new Promise((resolve, reject) => {
    const formData = new FormData();
    formData.append("file", file);

    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API_BASE}/tasks/${taskId}/one-click-translate/videos`);

    if (onProgress) {
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) onProgress(e.loaded / e.total);
      };
    }

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(JSON.parse(xhr.responseText));
      } else {
        reject(new Error("Failed to upload translation video"));
      }
    };
    xhr.onerror = () => reject(backendUnavailableError());
    xhr.send(formData);
  });
}

export function getVideoUrl(taskId: string): string {
  return `${API_BASE}/tasks/${taskId}/video`;
}

export function getThumbnailUrl(taskId: string): string {
  return `${API_BASE}/tasks/${taskId}/thumbnail`;
}

export function getTranslationPreviewUrl(taskId: string, videoId: string): string {
  return `${API_BASE}/tasks/${taskId}/one-click-translate/videos/${videoId}/preview`;
}

// ============================================================
// Processing
// ============================================================

export type ModelRunOperation =
  | "asr"
  | "diarize"
  | "audit"
  | "highlight_rebuild"
  | "highlight_auto_run"
  | "golden_quote"
  | "translate_retry";

export type ModelRunMode = "rerun" | "resume" | "reset";

export interface ModelRunRoutePreview {
  provider: string;
  provider_profile_id?: string | null;
  model: string;
  tier: string;
  base_url?: string | null;
  port?: number | null;
  request_url?: string | null;
  credentials_configured: boolean;
  temperature?: number | null;
  max_tokens?: number | null;
  thinking?: string | null;
}

export interface ModelRunPromptPreview {
  id: string;
  label: string;
  source: string;
  active_version?: string | null;
  digest: string;
  system_prompt: string;
  user_prompt?: string | null;
  note?: string | null;
}

export interface ModelRunStagePreview {
  id: string;
  label: string;
  kind: "local_model" | "llm" | "tts";
  routes: ModelRunRoutePreview[];
  parameters: Record<string, unknown>;
  prompts: ModelRunPromptPreview[];
  prompt_note?: string;
}

export interface ModelRunPreview {
  operation: ModelRunOperation;
  mode: ModelRunMode;
  title: string;
  api_path: string;
  task_revision: number;
  stages: ModelRunStagePreview[];
  affected_artifacts: string[];
  warnings: string[];
  preview_token: string;
  expires_in_seconds: number;
}

export interface ModelRunPreviewRequest {
  operation: ModelRunOperation;
  mode?: ModelRunMode;
  video_id?: string;
  options?: Record<string, unknown>;
}

export async function previewModelRun(
  taskId: string,
  request: ModelRunPreviewRequest,
): Promise<ModelRunPreview> {
  const res = await apiFetch(`${API_BASE}/tasks/${taskId}/execution-preview`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
  if (!res.ok) throw await readApiError(res, "加载执行预览失败");
  return res.json();
}

export interface ModelRunConfirmation {
  preview_token: string;
  preview_mode: ModelRunMode;
}

export async function triggerASR(
  taskId: string,
  model?: string,
  backend?: string,
  confirmation?: ModelRunConfirmation,
): Promise<void> {
  const body: Record<string, unknown> = {
    model: model || "base",
    backend: backend || "whisper",
    ...confirmation,
  };
  const res = await apiFetch(`${API_BASE}/tasks/${taskId}/asr`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error("Failed to trigger ASR");
}

export async function triggerAnalyze(
  taskId: string,
  silenceThreshold?: number,
): Promise<void> {
  const body: Record<string, unknown> = {
    silence_threshold: silenceThreshold ?? 0.3,
  };
  const res = await apiFetch(`${API_BASE}/tasks/${taskId}/asr/analyze`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error("Failed to trigger ASR analysis");
}

export interface SubtitleOptimizationChange {
  segment_id: string;
  start: number;
  end: number;
  before: string;
  after: string;
  reasons: string[];
}

export interface SubtitleOptimizationPreview {
  subtitle_revision: number;
  glossary_fingerprint: string;
  preview_token: string;
  total_segments: number;
  changed_count: number;
  preserved: {
    audit_segments: number;
    audit_history: number;
    highlight_reels: number;
    golden_quotes: number;
    export_jobs: number;
  };
  changes: SubtitleOptimizationChange[];
}

export interface SubtitleOptimizationApplyResult {
  success: boolean;
  changed_count: number;
  subtitle_revision: number;
  changes?: SubtitleOptimizationChange[];
  task?: Task;
}

export async function previewSubtitleOptimization(
  taskId: string,
  segmentIds?: string[],
): Promise<SubtitleOptimizationPreview> {
  const res = await apiFetch(`${API_BASE}/tasks/${taskId}/subtitles/optimize/preview`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ segment_ids: segmentIds }),
  });
  if (!res.ok) throw await readApiError(res, "字幕优化预览失败");
  return res.json();
}

export async function applySubtitleOptimization(
  taskId: string,
  options: {
    selected_segment_ids: string[];
    expected_subtitle_revision: number;
    glossary_fingerprint: string;
    preview_token?: string;
  },
): Promise<SubtitleOptimizationApplyResult> {
  const res = await apiFetch(`${API_BASE}/tasks/${taskId}/subtitles/optimize/apply`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(options),
  });
  if (!res.ok) throw await readApiError(res, "应用字幕优化失败");
  return res.json();
}

export async function triggerDiarization(
  taskId: string,
  confirmation?: ModelRunConfirmation,
): Promise<void> {
  const res = await apiFetch(`${API_BASE}/tasks/${taskId}/asr/diarize`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled: true, ...confirmation }),
  });
  if (!res.ok) throw new Error("Failed to trigger speaker diarization");
}

export async function switchASRVersion(taskId: string, version: number): Promise<void> {
  const res = await apiFetch(`${API_BASE}/tasks/${taskId}/asr/switch`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ version }),
  });
  if (!res.ok) {
    let message = "切换 ASR 版本失败";
    try {
      const payload = await res.json();
      message = payload.detail || payload.message || message;
    } catch {
      // 保留通用错误文案
    }
    throw new Error(message);
  }
}

export async function resmooth(
  taskId: string,
  threshold: number,
): Promise<{ message: string; task: Task }> {
  const res = await apiFetch(`${API_BASE}/tasks/${taskId}/asr/resmooth`, {
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

export interface ResegmentInheritanceReport {
  generated_at: string;
  mode: "none" | "aggressive";
  old_segment_count: number;
  new_segment_count: number;
  mapped_segment_count: number;
  exact_segment_count: number;
  review_segment_count: number;
  unmapped_segment_count: number;
  inherited_audit_count: number;
  inherited_occurrence_count: number;
  inherited_reel_count: number;
  inherited_golden_quote_count: number;
  invalidated_export_count: number;
  review_segment_ids: string[];
  unmapped_segment_ids: string[];
  warnings: string[];
}

export interface ResegmentResult {
  success: boolean;
  message: string;
  segments?: Segment[];
  stats: ResegmentStats;
  recommendation: {
    threshold: number;
    reason: string;
    stats: ResegmentStats;
  } | null;
  inheritance: ResegmentInheritanceReport;
  task?: Task;
}

export async function resegment(
  taskId: string,
  silenceThreshold: number,
  save: boolean = false,
  options: {
    inheritMode?: "none" | "aggressive";
    expectedContentRevision?: number;
    includeSegments?: boolean;
    signal?: AbortSignal;
  } = {},
): Promise<ResegmentResult> {
  const res = await apiFetch(`${API_BASE}/tasks/${taskId}/asr/resegment`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      silence_threshold: silenceThreshold,
      save,
      inherit_mode: options.inheritMode ?? "none",
      expected_content_revision: options.expectedContentRevision,
      include_segments: options.includeSegments ?? false,
    }),
    signal: options.signal,
  });
  if (!res.ok) throw await readApiError(res, "重新分段失败");
  return res.json();
}

export async function triggerAudit(
  taskId: string,
  options: {
    openai_api_key?: string;
    style_mode?: string;
    openai_model?: string;
    force_rule_engine?: boolean;
    provider?: string;
    ollama_model?: string;
    ollama_base_url?: string;
    reset_prior_audit?: boolean;
    preview_token?: string;
    preview_mode?: ModelRunMode;
  }
): Promise<void> {
  const body = {
    openai_api_key: options.openai_api_key,
    style_mode: options.style_mode || "immersive",
    openai_model: options.openai_model || DEFAULT_AUDIT_MODEL,
    force_rule_engine: options.force_rule_engine ?? false,
    provider: options.provider || "openai",
    ollama_model: options.ollama_model || "deepseek-r1:32b",
    ollama_base_url: options.ollama_base_url || "http://localhost:11434",
    reset_prior_audit: options.reset_prior_audit ?? false,
    preview_token: options.preview_token,
    preview_mode: options.preview_mode,
  };
  const res = await apiFetch(`${API_BASE}/tasks/${taskId}/audit`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error("Failed to trigger audit");
}

export interface OneClickTranslateStartOptions {
  source_language: string;
  target_language: string;
  subtitle_model: LLMModelConfig;
  tts_model: TTSModelConfig;
  subtitle_api_key?: string;
  tts_api_key?: string;
  asr_backend?: string;
  asr_model?: string;
  // 单视频重试专用：true 表示无视已有产物从 ASR 从头重跑
  force_restart?: boolean;
  preview_token?: string;
  preview_mode?: ModelRunMode;
}

export async function startOneClickTranslate(
  taskId: string,
  options: OneClickTranslateStartOptions
): Promise<void> {
  const res = await apiFetch(`${API_BASE}/tasks/${taskId}/one-click-translate/start`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      ...options,
      asr_backend: options.asr_backend || "funasr",
      asr_model: options.asr_model || "paraformer-zh",
    }),
  });
  if (!res.ok) throw await readApiError(res, "Failed to start one-click translation");
}

export async function updateTranslationCues(
  taskId: string,
  videoId: string,
  cues: TranslationCue[]
): Promise<{ success: boolean; video: TranslationVideo }> {
  const res = await apiFetch(`${API_BASE}/tasks/${taskId}/one-click-translate/videos/${videoId}/cues`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ cues }),
  });
  if (!res.ok) throw await readApiError(res, "Failed to update translation subtitles");
  return res.json();
}

export async function retryTranslationVideo(
  taskId: string,
  videoId: string,
  options: OneClickTranslateStartOptions
): Promise<{ success: boolean; message: string; resume_stage: string }> {
  const res = await apiFetch(`${API_BASE}/tasks/${taskId}/one-click-translate/videos/${videoId}/retry`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      ...options,
      asr_backend: options.asr_backend || "funasr",
      asr_model: options.asr_model || "paraformer-zh",
    }),
  });
  if (!res.ok) throw await readApiError(res, "Failed to retry translation video");
  return res.json();
}

export async function exportOneClickTranslate(
  taskId: string,
  options: OneClickTranslateStartOptions
): Promise<void> {
  const res = await apiFetch(`${API_BASE}/tasks/${taskId}/one-click-translate/export`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...options, approval_token: REVIEW_APPROVAL_TOKEN }),
  });
  if (!res.ok) throw await readApiError(res, "Failed to export one-click translation");
}

// ============================================================
// Segment Management
// ============================================================

export async function updateSegments(
  taskId: string,
  segments: Segment[]
): Promise<{ kept: number; deleted: number; edited_duration: number }> {
  const res = await apiFetch(`${API_BASE}/tasks/${taskId}/segments`, {
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
  const res = await apiFetch(`${API_BASE}/tasks/${taskId}/clip`, {
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
  renderProfile: RenderProfile = "final",
): Promise<ExportJob> {
  const body: Record<string, unknown> = {
    mode: "ffmpeg",
    output_name: outputName,
    burn_subtitles: burnSubtitles ?? false,
    render_profile: renderProfile,
  };
  if (renderProfile === "final") body.approval_token = REVIEW_APPROVAL_TOKEN;
  if (burnSubtitles && subtitleStyle) {
    body.subtitle_style = subtitleStyle;
  }
  const res = await apiFetch(`${API_BASE}/tasks/${taskId}/export/ffmpeg`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw await readApiError(res, "Failed to start FFmpeg export");
  const data = await res.json();
  return data.job as ExportJob;
}

export async function revealExport(taskId: string): Promise<void> {
  const res = await apiFetch(`${API_BASE}/tasks/${taskId}/export/reveal`, {
    method: "POST",
  });
  if (!res.ok) throw await readApiError(res, "无法在文件管理器中打开导出结果");
}

export interface HighlightClipPreviewRequest {
  signal?: AbortSignal;
  reelId?: string;
  qualityStage?: HighlightQualityStage;
  /** 兼容尚未支持 canonical reel 快照预览的旧后端。 */
  segmentIds?: string[];
}

export async function exportClipPreview(
  taskId: string,
  request: HighlightClipPreviewRequest | string[]
): Promise<string> {
  const options = Array.isArray(request) ? { segmentIds: request } : request;
  const qualityStage = options.qualityStage ?? "after_quality";
  const res = await apiFetch(`${API_BASE}/tasks/${taskId}/export/clip-preview`, {
    method: "POST",
    signal: options.signal,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      reel_id: options.reelId,
      quality_stage: qualityStage,
      // 旧后端仍读取 reel_variant / segment_ids；新后端优先使用上面的 canonical 定位。
      reel_variant: qualityStage,
      segment_ids: options.segmentIds,
    }),
  });
  if (!res.ok) throw await readApiError(res, "片段预览导出失败");
  const data = await res.json();
  return data.url;
}

export async function exportHighlightClips(
  taskId: string,
  options: {
    reelId: string;
    qualityStage?: HighlightQualityStage;
    /** @deprecated 使用 qualityStage。 */
    reelVariant?: HighlightReelVariant;
    outputName?: string;
    burnSubtitles?: boolean;
    subtitleStyle?: SubtitleStyle;
  }
): Promise<ExportJob> {
  const qualityStage = options.qualityStage ?? options.reelVariant ?? "after_quality";
  const res = await apiFetch(`${API_BASE}/tasks/${taskId}/export/highlight-clips`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      reel_id: options.reelId,
      quality_stage: qualityStage,
      reel_variant: qualityStage,
      output_name: options.outputName,
      burn_subtitles: options.burnSubtitles ?? false,
      subtitle_style: options.burnSubtitles ? options.subtitleStyle : undefined,
      approval_token: REVIEW_APPROVAL_TOKEN,
    }),
  });
  if (!res.ok) throw await readApiError(res, "产品集锦导出失败");
  const data = await res.json();
  return data.job as ExportJob;
}

export interface HighlightReelVariantPayload {
  reels: HighlightReel[];
  quality_report?: HighlightReelQualityReport | null;
}

export interface HighlightReelsResponse extends HighlightReelVariantPayload {
  product_occurrences?: ProductOccurrence[];
  product_groups?: ProductGroup[];
  occurrences_count?: number;
  product_groups_count?: number;
  canonical_reels_count?: number;
  variants?: Partial<Record<HighlightReelVariant, HighlightReelVariantPayload>>;
  quality_stages?: Partial<Record<HighlightQualityStage, HighlightReelVariantPayload>>;
}

export async function getHighlightReels(taskId: string): Promise<HighlightReel[]> {
  const res = await apiFetch(`${API_BASE}/tasks/${taskId}/highlight-reels`);
  if (!res.ok) throw await readApiError(res, "获取产品集锦计划失败");
  const data = await res.json();
  return data.reels ?? [];
}

export async function getHighlightReelVariants(taskId: string): Promise<HighlightReelsResponse> {
  const res = await apiFetch(`${API_BASE}/tasks/${taskId}/highlight-reels`);
  if (!res.ok) throw await readApiError(res, "获取产品集锦计划失败");
  return res.json();
}

export async function updateHighlightReels(
  taskId: string,
  reels: HighlightReel[],
  expectedRevision: number,
  qualityStage: HighlightQualityStage = "after_quality",
): Promise<{ reels: HighlightReel[]; revision: number }> {
  const res = await apiFetch(`${API_BASE}/tasks/${taskId}/highlight-reels`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      reels,
      quality_stage: qualityStage,
      variant: qualityStage,
      expected_revision: expectedRevision,
    }),
  });
  if (!res.ok) throw await readApiError(res, "更新产品集锦计划失败");
  const data = await res.json();
  return {
    reels: data.reels ?? [],
    revision: data.revision,
  };
}

export interface HighlightReelBuildOptions {
  /** 每个产品最终只生成一个 winner，因此这是前端“条数”的真实目标。 */
  target_product_count?: number | null;
  /** 兼容旧后端；前端发送时与 target_product_count 保持一致。 */
  target_reel_count?: number | null;
  min_reel_duration?: number;
  max_reel_duration?: number;
  target_total_duration?: number | null;
  preview_token?: string;
  preview_mode?: ModelRunMode;
}

export async function rebuildHighlightReels(
  taskId: string,
  options: HighlightReelBuildOptions = {}
): Promise<{ success: boolean; message: string }> {
  const res = await apiFetch(`${API_BASE}/tasks/${taskId}/highlight-reels/rebuild`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(options),
  });
  if (!res.ok) throw await readApiError(res, "重建产品集锦计划失败");
  return res.json();
}

export async function validateHighlightReels(taskId: string): Promise<HighlightReelQualityReport> {
  const res = await apiFetch(`${API_BASE}/tasks/${taskId}/highlight-reels/validate`, {
    method: "POST",
  });
  if (!res.ok) throw await readApiError(res, "产品集锦验收失败");
  const data = await res.json();
  return data.quality_report;
}

export interface HighlightReelAutoRunRequest extends HighlightReelBuildOptions {
  auto_export?: boolean;
  provider?: string;
  openai_model?: string;
  ollama_model?: string;
  ollama_base_url?: string;
  openai_api_key?: string;
  style_mode?: string;
  force_rule_engine?: boolean;
}

export async function autoRunHighlightReel(
  taskId: string,
  body: HighlightReelAutoRunRequest = {}
): Promise<{ success: boolean; message: string }> {
  const res = await apiFetch(`${API_BASE}/tasks/${taskId}/highlight-reels/auto-run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw await readApiError(res, "直播卖货集锦自动流程启动失败");
  return res.json();
}

export async function cancelTask(taskId: string): Promise<void> {
  const res = await apiFetch(`${API_BASE}/tasks/${taskId}/cancel`, {
    method: "POST",
  });
  if (!res.ok) throw await readApiError(res, "终止任务失败");
}

export async function cancelExport(exportJobId: string): Promise<void> {
  const res = await apiFetch(`${API_BASE}/exports/${exportJobId}/cancel`, {
    method: "POST",
  });
  if (!res.ok) throw new Error("Failed to cancel export");
}

export async function exportJianying(
  taskId: string,
  outputName?: string,
  draftFolder?: string
): Promise<ExportJob> {
  const res = await apiFetch(`${API_BASE}/tasks/${taskId}/export/jianying`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      mode: "jianying",
      output_name: outputName,
      jianying_draft_folder: draftFolder,
      approval_token: REVIEW_APPROVAL_TOKEN,
    }),
  });
  if (!res.ok) throw await readApiError(res, "Failed to start JianYing export");
  const data = await res.json();
  return data.job as ExportJob;
}

// ============================================================
// Config
// ============================================================

export async function getEditingAesthetic(): Promise<string> {
  const res = await apiFetch(`/config/editing-aesthetic`);
  if (!res.ok) return "";
  const data = await res.json();
  return data.content;
}

export async function updateEditingAesthetic(content: string): Promise<void> {
  await apiFetch(`/config/editing-aesthetic`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
}

/** 后端 app_config.json 字段（snake_case） */
export interface ModelPricingConfigPayload {
  input_per_million_tokens?: number | null;
  output_per_million_tokens?: number | null;
  reasoning_per_million_tokens?: number | null;
  per_request?: number | null;
  per_million_characters?: number | null;
}

export interface CloudModelConfigPayload {
  provider_label?: string;
  base_url?: string;
  api_key?: string;
  model?: string;
  pricing?: ModelPricingConfigPayload;
}

export interface LocalModelConfigPayload {
  base_url?: string;
  model?: string;
}

export interface FeatureModelConfigPayload {
  mode?: "cloud" | "local";
  cloud?: CloudModelConfigPayload;
  local?: LocalModelConfigPayload;
}

export interface TtsFeatureConfigPayload {
  mode?: "cloud" | "silent";
  cloud?: CloudModelConfigPayload;
  voice?: string;
}

export interface FeatureLlmConfigsPayload {
  audit?: FeatureModelConfigPayload;
  translate?: FeatureModelConfigPayload;
  highlight_reel?: FeatureModelConfigPayload;
  golden_quote?: FeatureModelConfigPayload;
  learning?: FeatureModelConfigPayload;
  tts?: TtsFeatureConfigPayload;
}

export interface AsrRuntimeConfigPayload {
  funasr_chunk_minutes?: number;
  funasr_parallel_chunks?: number;
}

export interface AuditRuntimeConfigPayload {
  cloud_chunk_segments?: number;
  cloud_max_parallel_chunks?: number;
  cloud_initial_parallel_chunks?: number;
  cloud_chunk_token_budget?: number;
  enable_batch_resume?: boolean;
  enable_incremental_cache?: boolean;
  coherence_strategy?: "always" | "auto" | "local_first";
}

export interface HighlightRuntimeConfigPayload {
  cloud_max_parallel_calls?: number;
}

export interface ProviderProfilePayload {
  id: string;
  label: string;
  provider: "openai_compatible" | "ollama";
  base_url: string;
  api_key?: string;
  api_key_env?: string;
  enabled?: boolean;
}

export interface LlmRouteCandidatePayload {
  provider_profile_id: string;
  model: string;
  tier: "primary" | "fallback" | "escalation";
  enabled?: boolean;
  temperature?: number | null;
  max_tokens?: number | null;
  thinking?: "auto" | "enabled" | "disabled";
  extra_body?: Record<string, unknown>;
  pricing?: ModelPricingConfigPayload;
}

export interface LlmRoleRoutePayload {
  label: string;
  purpose: string;
  candidates: LlmRouteCandidatePayload[];
  quality_gate_escalation?: boolean;
}

export interface AppConfigPayload {
  schema_version?: number;
  claude_api_key?: string;
  openai_api_key: string;
  claude_model?: string;
  openai_model?: string;
  jianying_dir: string;
  provider: "openai" | "ollama" | "claude";
  ollama_model: string;
  ollama_base_url: string;
  feature_llm?: FeatureLlmConfigsPayload;
  asr?: AsrRuntimeConfigPayload;
  audit_runtime?: AuditRuntimeConfigPayload;
  highlight_runtime?: HighlightRuntimeConfigPayload;
  provider_profiles?: ProviderProfilePayload[];
  llm_role_routes?: Record<string, LlmRoleRoutePayload>;
}

export async function fetchAppConfig(): Promise<AppConfigPayload> {
  const url = `${API_BASE}/config/app`;
  const res = await apiFetch(url);
  if (!res.ok) {
    throw await readApiError(res, "Failed to load app config");
  }
  return res.json();
}

export async function putAppConfig(payload: AppConfigPayload): Promise<void> {
  const res = await apiFetch(`${API_BASE}/config/app`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    throw await readApiError(res, "Failed to save app config");
  }
}

export interface LlmModelTestRequest {
  feature: string;
  provider_profile_id?: string;
  base_url: string;
  api_key?: string;
  model: string;
  voice?: string;
  kind: "chat" | "tts";
}

export interface LlmModelTestResult {
  success: boolean;
  model: string;
  kind: "chat" | "tts";
  checks: {
    auth?: { ok: boolean; status: number | string; label?: string; usage?: number; limit?: number };
    model?: {
      ok: boolean;
      status: number | string;
      name?: string;
      context_length?: number;
      pricing?: { prompt?: string; completion?: string };
      supported_parameters?: string[];
    };
    endpoints?: {
      ok: boolean;
      status: number;
      count?: number;
      sample?: Record<string, unknown>;
    };
    trial?: {
      ok: boolean;
      status: number;
      usage?: Record<string, unknown>;
      generation_id?: string;
      bytes?: number;
      error?: string;
    };
  };
}

export interface AvailableLlmModel {
  id: string;
  name: string;
  description: string;
  context_length?: number | null;
  input_modalities: string[];
  output_modalities: string[];
  pricing: {
    prompt?: string | null;
    completion?: string | null;
  };
}

export interface ProviderPricingStatus {
  provider_profile_id: string;
  source_url: string;
  registered_at: string;
  synced_at: string;
  model_count: number;
}

export async function fetchProviderPricingStatuses(): Promise<ProviderPricingStatus[]> {
  const res = await apiFetch(`${API_BASE}/config/provider-pricing`);
  if (!res.ok) throw await readApiError(res, "读取供应商报价状态失败");
  const data = await res.json();
  return data.providers ?? [];
}

export async function syncProviderPricing(
  providerProfileId: string,
): Promise<ProviderPricingStatus> {
  const res = await apiFetch(
    `${API_BASE}/config/provider-pricing/${encodeURIComponent(providerProfileId)}/sync`,
    { method: "POST" },
  );
  if (!res.ok) throw await readApiError(res, "同步供应商报价失败");
  const data = await res.json();
  return data.provider;
}

export async function listAvailableLlmModels(
  feature: string,
  baseUrl: string,
  refresh = false,
  providerProfileId?: string,
): Promise<AvailableLlmModel[]> {
  const params = new URLSearchParams({
    feature,
    base_url: baseUrl,
    refresh: refresh ? "true" : "false",
  });
  if (providerProfileId) params.set("provider_profile_id", providerProfileId);
  const res = await apiFetch(`${API_BASE}/config/llm/models?${params.toString()}`);
  if (!res.ok) throw await readApiError(res, "读取可用模型失败");
  const data = await res.json();
  return data.models ?? [];
}

export async function testLlmModel(payload: LlmModelTestRequest): Promise<LlmModelTestResult> {
  const res = await apiFetch(`${API_BASE}/config/llm/test`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    throw await readApiError(res, "模型试跑失败");
  }
  return res.json();
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

export interface ConfigPromptSummary {
  id: string;
  feature: string;
  label: string;
  description: string;
  editable: boolean;
  source: string;
  active_version?: string | null;
  kind: "system";
}

export interface ConfigPromptData extends ConfigPromptSummary {
  content: string;
}

export async function listConfigPrompts(feature: string): Promise<ConfigPromptSummary[]> {
  const res = await apiFetch(`${API_BASE}/config/prompts?feature=${encodeURIComponent(feature)}`);
  if (!res.ok) throw await readApiError(res, "加载 Prompt 列表失败");
  const data = await res.json();
  return data.prompts ?? [];
}

export async function getConfigPrompt(promptId: string): Promise<ConfigPromptData> {
  const res = await apiFetch(`${API_BASE}/config/prompts/${encodeURIComponent(promptId)}`);
  if (!res.ok) throw await readApiError(res, "加载 Prompt 失败");
  return res.json();
}

export async function updateConfigPrompt(promptId: string, content: string): Promise<ConfigPromptData> {
  const res = await apiFetch(`${API_BASE}/config/prompts/${encodeURIComponent(promptId)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
  if (!res.ok) throw await readApiError(res, "保存 Prompt 失败");
  const data = await res.json();
  return data.prompt;
}

export async function getPrompt(scenario: ScenarioKey): Promise<PromptData> {
  const requestPath = `${API_BASE}/prompts/${scenario}`;
  const res = await apiFetch(requestPath);
  if (!res.ok) return { content: "", rules: [] };
  return res.json();
}

export async function updatePromptContent(scenario: ScenarioKey, content: string): Promise<PromptData> {
  const res = await apiFetch(`${API_BASE}/prompts/${scenario}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
  if (!res.ok) throw new Error("Failed to save prompt");
  return res.json();
}

export async function updatePromptRules(scenario: ScenarioKey, rules: PromptRule[]): Promise<void> {
  const res = await apiFetch(`${API_BASE}/prompts/${scenario}/rules`, {
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

export interface PromptUpdatePoint {
  summary: string;
  target_rule?: string;
  change_type?: string;
  title?: string;
}

export interface PromptRuleChange {
  type: "modify" | "add" | string;
  rule_id: string;
  full_rule_text: string;
  insert_after?: string;
  summary?: string;
}

export interface PromptChangesPayload {
  changes: PromptRuleChange[];
}

export interface PromptFeedbackResult {
  corrections: CorrectionsResult;
  suggestion: string;
  scenario: string;
  update_points?: PromptUpdatePoint[];
  prompt_changes?: PromptChangesPayload;
  style_update?: Record<string, string>;
}

export async function getCorrections(taskId: string): Promise<CorrectionsResult> {
  const res = await apiFetch(`${API_BASE}/tasks/${taskId}/corrections`);
  if (!res.ok) throw new Error("Failed to get corrections");
  return res.json();
}

export async function submitPromptFeedback(
  taskId: string,
  options: {
    openai_api_key?: string;
    openai_model?: string;
    user_notes?: string;
  }
): Promise<PromptFeedbackResult> {
  const res = await apiFetch(`${API_BASE}/tasks/${taskId}/prompt-feedback`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      openai_api_key: options.openai_api_key,
      openai_model: options.openai_model || DEFAULT_LEARNING_MODEL,
      user_notes: options.user_notes || "",
    }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "请求失败" }));
    throw new Error(err.detail || "Failed to submit prompt feedback");
  }
  return res.json();
}

export async function adoptPromptFeedback(
  taskId: string,
  promptChanges: PromptChangesPayload
): Promise<{ success: boolean; version: number; path: string; changes_applied: number; content: string }> {
  const res = await apiFetch(`${API_BASE}/tasks/${taskId}/adopt-prompt-feedback`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt_changes: promptChanges }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "请求失败" }));
    throw new Error(err.detail || "Failed to adopt prompt feedback");
  }
  return res.json();
}

// ============================================================
// 个人剪辑风格档案
// ============================================================

export async function getUserStyle(): Promise<string> {
  const res = await apiFetch(`/config/user-style`);
  if (!res.ok) return "";
  const data = await res.json();
  return data.content;
}

export async function updateUserStyle(content: string): Promise<void> {
  await apiFetch(`/config/user-style`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
}

export async function adoptStyleUpdate(
  taskId: string,
  styleUpdate: Record<string, string>
): Promise<{ success: boolean; content: string }> {
  const res = await apiFetch(`${API_BASE}/tasks/${taskId}/adopt-style`, {
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
  const url = `${API_BASE}/config/glossary`;
  const res = await apiFetch(url);
  if (!res.ok) return "";
  const data = await res.json();
  return data.content;
}

export async function updateGlossary(content: string): Promise<void> {
  const url = `${API_BASE}/config/glossary`;
  const res = await apiFetch(url, {
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
  | "aligning"
  | "diff_ready"
  | "analyzing"
  | "proposals_ready"
  | "done"
  | "error";

export interface LearningEditedVariant {
  id: string;
  filename: string;
  video_path: string;
  duration?: number;
  source_kind: string;
  source_reel_id?: string;
  asr_result?: ASRResult;
}

export interface LearningDiffItem {
  id: string;
  source_segment_id?: string;
  source_start: number;
  source_end: number;
  source_text: string;
  ai_action?: string;
  human_action?: string;
  refined_action: string;
  category: string;
  confidence: number;
  variant_ids: string[];
  selected: boolean;
  note: string;
}

export interface LearningProposal {
  id: string;
  target: string;
  title: string;
  rationale: string;
  evidence_ids: string[];
  change: Record<string, unknown>;
  risk: string;
  status: "pending" | "applied" | "rejected" | "unsupported";
}

export interface LearningSource {
  id: string;
  name: string;
  task_type: LearningTask["task_type"];
  status: string;
  asr_version?: number;
  audit_version?: number;
  segments_count: number;
  human_overrides: number;
  highlight_reels: { reel_id: string; title: string; export_path: string }[];
}

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
  source_task_id?: string;
  source_task_name?: string;
  source_asr_version?: number;
  source_audit_version?: number;
  edited_variants: LearningEditedVariant[];
  diff_items: LearningDiffItem[];
  proposals: LearningProposal[];
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
  const res = await apiFetch(`/learning/tasks`, {
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

export async function fetchLearningSources(): Promise<LearningSource[]> {
  const res = await apiFetch(`/learning/sources`);
  if (!res.ok) throw new Error("获取可学习原任务失败");
  return res.json();
}

export async function createLearningTaskFromSource(data: {
  name: string;
  source_task_id: string;
}): Promise<LearningTask> {
  const res = await apiFetch(`/learning/tasks/from-source`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: "创建学习任务失败" }));
    throw new Error(error.detail || "创建学习任务失败");
  }
  return res.json();
}

export async function fetchLearningTasks(): Promise<LearningTask[]> {
  const res = await apiFetch(`/learning/tasks`);
  if (!res.ok) throw new Error("获取学习任务列表失败");
  return res.json();
}

export async function fetchLearningTask(taskId: string): Promise<LearningTask> {
  const res = await apiFetch(`/learning/tasks/${taskId}`);
  if (!res.ok) throw new Error("学习任务不存在");
  return res.json();
}

export async function deleteLearningTask(taskId: string): Promise<void> {
  await apiFetch(`/learning/tasks/${taskId}`, { method: "DELETE" });
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

export async function uploadLearningVariants(taskId: string, files: File[]): Promise<void> {
  const formData = new FormData();
  files.forEach((file) => formData.append("files", file));
  const res = await fetch(`${API_BASE}/learning/tasks/${taskId}/edited-variants`, {
    method: "POST",
    body: formData,
  });
  if (!res.ok) throw new Error("上传精修集锦失败");
}

export async function triggerLearningAlignment(taskId: string, backend = "funasr"): Promise<void> {
  const res = await apiFetch(`/learning/tasks/${taskId}/align`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ backend, model: "base" }),
  });
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: "对齐启动失败" }));
    throw new Error(error.detail || "对齐启动失败");
  }
}

export async function updateLearningDiff(
  taskId: string, diffId: string, selected: boolean, note = ""
): Promise<LearningDiffItem> {
  const res = await apiFetch(`/learning/tasks/${taskId}/diffs/${diffId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ selected, note }),
  });
  if (!res.ok) throw new Error("更新差异选择失败");
  return res.json();
}

export async function resolveLearningProposal(
  taskId: string, proposalId: string, action: "apply" | "reject"
): Promise<LearningProposal> {
  const res = await apiFetch(`/learning/tasks/${taskId}/proposals/${proposalId}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action }),
  });
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: "处理学习草案失败" }));
    throw new Error(error.detail || "处理学习草案失败");
  }
  return res.json();
}

export async function triggerLearningASR(
  taskId: string,
  backend: string = "funasr"
): Promise<void> {
  const res = await apiFetch(`/learning/tasks/${taskId}/asr`, {
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
  options: { openai_api_key?: string; openai_model?: string } = {}
): Promise<void> {
  const res = await apiFetch(`/learning/tasks/${taskId}/analyze`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      openai_api_key: options.openai_api_key,
      openai_model: options.openai_model || DEFAULT_LEARNING_MODEL,
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
  const res = await apiFetch(`/learning/tasks/${taskId}/save-prompt`, {
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
  const res = await apiFetch(`/learning/prompts/${scenario}/versions`);
  if (!res.ok) return { versions: [], scenario };
  return res.json();
}

export const LEARNING_STATUS_LABELS: Record<LearningTaskStatus, string> = {
  pending: "待处理",
  asr_original: "原视频 ASR 中",
  asr_edited: "剪辑视频 ASR 中",
  asr_done: "ASR 完成",
  aligning: "正在对齐",
  diff_ready: "差异待确认",
  analyzing: "OpenAI 分析中",
  proposals_ready: "草案待确认",
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
  let connectionAttempt = 0;
  const socketInstanceId = `${Date.now()}-${Math.random().toString(16).slice(2)}`;

  function clearTimers() {
    if (heartbeatTimer) { clearInterval(heartbeatTimer); heartbeatTimer = null; }
    if (reconnectTimer) { clearTimeout(reconnectTimer);  reconnectTimer = null; }
  }

  function connect() {
    if (destroyed) return;
    connectionAttempt += 1;
    const currentAttempt = connectionAttempt;
    ws = new WebSocket(`${getWsBase()}/tasks/${taskId}/log`);

    ws.onopen = () => {
      retryDelay = 1000; // 连接成功后重置退避间隔
      // #region agent log
      fetch('http://127.0.0.1:7296/ingest/805af596-423f-410e-860c-f2f1305f2af9',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'f0779c'},body:JSON.stringify({sessionId:'f0779c',runId:'pre-fix',hypothesisId:'H2,H3',location:'client/src/lib/api.ts:createLogWebSocket.onopen',message:'Task log websocket opened',data:{taskId,socketInstanceId,currentAttempt,destroyed,readyState:ws?.readyState ?? null},timestamp:Date.now()})}).catch(()=>{});
      // #endregion
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
        if (msg.type === "claude_failed" || msg.type === "connected") {
          // #region agent log
          fetch('http://127.0.0.1:7296/ingest/805af596-423f-410e-860c-f2f1305f2af9',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'f0779c'},body:JSON.stringify({sessionId:'f0779c',runId:'pre-fix',hypothesisId:'H1,H2,H3',location:'client/src/lib/api.ts:createLogWebSocket.onmessage',message:'Diagnostic websocket message received',data:{taskId,socketInstanceId,currentAttempt,type:msg.type,replay:Boolean((msg as LogMessage & {replay?: boolean}).replay),status:msg.status ?? null,errorType:msg.error_type ?? null},timestamp:Date.now()})}).catch(()=>{});
          // #endregion
        }
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

export function formatDateTime(isoString: string | undefined | null): string {
  if (!isoString) return "";
  const date = new Date(isoString);
  if (isNaN(date.getTime())) return String(isoString);
  return date.toLocaleString("zh-CN", {
    year: "numeric",
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

export const STATUS_LABELS: Record<TaskStatus, string> = {
  pending: "待处理",
  asr_running: "识别中",
  asr_recognized: "识别完成",
  diarization_running: "说话人分离中",
  asr_diarized: "说话人分离完成",
  analyze_running: "准备审计输入中",
  asr_done: "ASR 完成",
  audit_running: "AI 审计中",
  translate_running: "校准翻译中",
  tts_running: "配音生成中",
  dubbing_running: "音轨合成中",
  review: "待剪辑审核",
  export_running: "导出中",
  done: "已完成",
  error: "出错",
};

export const STATUS_COLORS: Record<TaskStatus, string> = {
  pending: "text-slate-400 bg-slate-800",
  asr_running: "text-blue-300 bg-blue-900/50",
  asr_recognized: "text-sky-300 bg-sky-900/50",
  diarization_running: "text-violet-300 bg-violet-900/50",
  asr_diarized: "text-violet-300 bg-violet-900/50",
  analyze_running: "text-cyan-300 bg-cyan-900/50",
  asr_done: "text-cyan-300 bg-cyan-900/50",
  audit_running: "text-amber-300 bg-amber-900/50",
  translate_running: "text-indigo-300 bg-indigo-900/50",
  tts_running: "text-fuchsia-300 bg-fuchsia-900/50",
  dubbing_running: "text-purple-300 bg-purple-900/50",
  review: "text-yellow-300 bg-yellow-900/50",
  export_running: "text-purple-300 bg-purple-900/50",
  done: "text-green-300 bg-green-900/50",
  error: "text-red-300 bg-red-900/50",
};

export const TASK_TYPE_LABELS: Record<string, string> = {
  highlight_reel: "直播卖货集锦",
  interview_compress: "直播访谈压缩",
  monologue_clean: "口播精修",
  one_click_translate: "一键转英文",
};

/** 仅直播访谈/对话类任务默认开启说话人分离 */
export function defaultEnableDiarizationForTaskType(taskType: string): boolean {
  return taskType === "interview_compress";
}

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
  jobs_count?: number;
  product_groups_count?: number;
}

export async function getLearningManualPromptStep1(taskId: string): Promise<ManualPromptPack> {
  const res = await apiFetch(`/learning/tasks/${taskId}/manual-prompt/step1`);
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "获取第一步提示词失败");
  }
  return res.json();
}

export async function submitLearningManualStep1(taskId: string, analysisResult: string): Promise<void> {
  const res = await apiFetch(`/learning/tasks/${taskId}/manual-result/step1`, {
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
  const res = await apiFetch(`/learning/tasks/${taskId}/manual-prompt/step2`);
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "获取第二步提示词失败");
  }
  return res.json();
}

export async function submitLearningManualStep2(taskId: string, changesJson: string): Promise<void> {
  const res = await apiFetch(`/learning/tasks/${taskId}/manual-result/step2`, {
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
  const res = await apiFetch(`${API_BASE}/tasks/${taskId}/manual-audit-prompt`);
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "获取审计提示词失败");
  }
  return res.json();
}

export async function getManualHighlightPrompt(taskId: string): Promise<ManualPromptPack> {
  const res = await apiFetch(`${API_BASE}/tasks/${taskId}/manual-highlight-prompt`);
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "获取集锦分析提示词失败");
  }
  return res.json();
}

export interface ManualHighlightResultPreview {
  can_apply: boolean;
  expected_revision: number;
  input_results_count: number;
  product_occurrences_count: number;
  product_groups_count: number;
  candidates_count: number;
  winners_count: number;
  repair_count: number;
  issues_count: number;
  blocker_count: number;
  major_count: number;
  quality_passed: boolean;
}

export async function previewManualHighlightResult(
  taskId: string,
  resultJson: string,
  expectedRevision: number,
): Promise<ManualHighlightResultPreview> {
  const res = await apiFetch(`${API_BASE}/tasks/${taskId}/manual-highlight-result/preview`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ result_json: resultJson, expected_revision: expectedRevision }),
  });
  if (!res.ok) throw await readApiError(res, "预览 Cursor 集锦结果失败");
  return res.json();
}

export async function applyManualHighlightResult(
  taskId: string,
  resultJson: string,
  expectedRevision: number,
): Promise<ManualHighlightResultPreview & {
  success: boolean;
  message: string;
  revision: number;
}> {
  const res = await apiFetch(`${API_BASE}/tasks/${taskId}/manual-highlight-result/apply`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ result_json: resultJson, expected_revision: expectedRevision }),
  });
  if (!res.ok) throw await readApiError(res, "应用 Cursor 集锦结果失败");
  return res.json();
}

export async function submitManualAuditResult(taskId: string, auditJson: string): Promise<{
  success: boolean;
  message: string;
  segments_kept: number;
  segments_deleted: number;
  edited_duration: number;
}> {
  const res = await apiFetch(`${API_BASE}/tasks/${taskId}/manual-audit-result`, {
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
  provider?: "openai" | "ollama";
  openai_api_key?: string;
  openai_model?: string;
  use_openrouter?: boolean;
  ollama_model?: string;
  ollama_base_url?: string;
  preview_token?: string;
  preview_mode?: ModelRunMode;
}

/**
 * 调用 AI 分析当前任务的保留片段，推荐最多 5 个适合做开场钩子的金句候选。
 * 同步接口，约 5-15 秒返回。
 */
export async function suggestGoldenQuotes(
  taskId: string,
  options: GoldenQuoteSuggestOptions = {},
): Promise<GoldenQuoteCandidate[]> {
  const res = await apiFetch(`${API_BASE}/tasks/${taskId}/golden-quotes/suggest`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      provider: options.provider || "openai",
      openai_api_key: options.openai_api_key,
      openai_model: options.openai_model || DEFAULT_GOLDEN_QUOTE_MODEL,
      use_openrouter: options.use_openrouter || false,
      ollama_model: options.ollama_model || "deepseek-r1:14b",
      ollama_base_url: options.ollama_base_url || "http://localhost:11434",
      preview_token: options.preview_token,
      preview_mode: options.preview_mode,
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
 * 保存用户选定并排序后的金句片段 ID 列表。
 * 导出时这些片段将被放在视频最开头。传空数组可清除设置。
 */
export async function saveGoldenQuoteOrder(
  taskId: string,
  segmentIds: string[],
): Promise<{ success: boolean; golden_quote_order: string[]; count: number }> {
  const res = await apiFetch(`${API_BASE}/tasks/${taskId}/golden-quotes/order`, {
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
