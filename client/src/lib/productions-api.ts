/** Shared project contract for the selling-video workbench. */
import { presenterConfig } from "./presenters";
export interface ProductionCue {
  start: number;
  end: number;
  text: string;
}
export interface ProductionSelection {
  asset_id: string;
  start: number;
  end: number;
}
export interface ProductionMediaOrigin {
  kind: "unknown" | "original" | "original_edit" | "aigc";
  provider: string;
}

export interface ProductionAsset {
  id: string;
  filename: string;
  url: string;
  fingerprint: string;
  kind: "video" | "audio" | "image";
  role: "footage" | "product" | "presenter" | "voiceover" | "music";
  shot_id: string | null;
  origin?: ProductionMediaOrigin;
  version: number;
  duration: number;
  width: number;
  height: number;
  has_audio: boolean;
  frames: string[];
  cues: ProductionCue[];
  usable_start: number;
  usable_end: number;
  analysis_status: "pending" | "running" | "done" | "failed";
  analysis_note: string;
  visual_summary: string;
}
export interface ProductionShot {
  shot_id: string;
  revision: number;
  purpose: string;
  visual: string;
  spoken_text: string;
  subtitle: string;
  target_duration: number;
  source_type: "existing" | "shoot" | "aigc";
  voice_mode: "original" | "generated" | "voiceover" | "silent";
  framing: "contain" | "cover";
  trim_pauses: boolean;
  tool: string;
  reference_ids: string[];
  fact_ids: string[];
  filming_guide: string;
  prompt: string;
  selection: ProductionSelection | null;
  voiceover: ProductionSelection | null;
  verified_signature: string | null;
  review_note: string;
  required: boolean;
}
export interface CreatorStyleSnapshot {
  creator_id: string;
  version: string;
  method_version: string;
  evidence_status: "candidate" | "reported" | "verified";
  card_text: string;
  evidence_json: string;
  content_sha256: string;
}
export interface ProductionWritingReview {
  script_intent?: ProductionScriptIntent | null;
  beats: Array<{
    role:
      | "opening"
      | "first_answer"
      | "demonstration"
      | "explanation"
      | "objection"
      | "action";
    purpose: string;
    audience_question: string;
    answer: string;
    shot_ids: string[];
    fact_ids: string[];
  }>;
  issues: Array<{
    code: string;
    message: string;
    shot_ids: string[];
  }>;
  structure_prompt_sha256: string;
  polish_prompt_sha256: string;
  structure_model?: string | null;
  polish_model?: string | null;
}
export interface ProductionDirection {
  id: string;
  title: string;
  hook: string;
  structure: string;
  selling_points: string[];
  new_shots: string;
  shots: ProductionShot[];
  style_snapshot?: CreatorStyleSnapshot | null;
  presenter_snapshot?: ProductionPresenter | null;
  writing_review?: ProductionWritingReview | null;
}
export interface ProductionNarrationDraft {
  text: string;
  shots: ProductionShot[];
}
export interface ProductionRender {
  id: string;
  content_revision: number;
  status: "queued" | "running" | "completed" | "failed" | "cancelled";
  progress: number;
  step: string;
  attempt: number;
  outputs: Record<string, string>;
  error: string;
  checks: Record<string, unknown>;
  is_current: boolean;
  created_at: string;
  timeline: {
    duration: number;
    clips: Array<{
      shot_id: string;
      asset_id: string;
      source_start: number;
      source_end: number;
      start: number;
      duration: number;
      spoken_text: string;
      prompt: string;
    }>;
  };
}

export interface HeyGenJob {
  id: string;
  shot_id: string;
  shot_revision: number;
  status:
    | "submitting"
    | "processing"
    | "importing"
    | "completed"
    | "paused"
    | "failed";
  video_id: string | null;
  asset_id: string | null;
  error: string;
}

export interface HeyGenSettings {
  has_key: boolean;
  managed_key: boolean;
  avatar_id: string;
  avatar_name: string;
  voice_id: string;
  voice_name: string;
  engine?: string;
}

export interface HeyGenCatalog {
  avatars: Array<{ id: string; name: string; engine?: string }>;
  voices: Array<{ id: string; name: string }>;
}
export type ProductionType = "selling_video" | "digital_presenter";
export type ProductionScriptIntent =
  | "product_sales"
  | "buying_comparison"
  | "experience_sharing";

export interface PresenterHeyGen {
  avatar_id: string;
  avatar_name: string;
  voice_id: string;
  voice_name: string;
  engine: string;
}
export interface PresenterConfig {
  name: string;
  primary_audience: string;
  secondary_audience: string;
  address: string[];
  guidance: string[];
  positioning: string;
  text_style: string;
  structure_style: string;
  speaking_style: string;
  visual_style: string;
  boundaries: string;
  heygen: PresenterHeyGen;
}
export interface ProductionPresenter extends PresenterConfig {
  id: string;
  revision: number;
  archived: boolean;
}

export interface ProductionSummary {
  id: string;
  name: string;
  project_type: ProductionType;
  presenter_id: string | null;
  product: string;
  brand_name: string;
  stage: string;
  updated_at: string;
  shot_count: number;
  ready_count: number;
}
export interface ProductionProject {
  id: string;
  name: string;
  project_type: ProductionType;
  presenter_id: string | null;
  presenter_snapshot?: ProductionPresenter | null;
  presenter_style_snapshot?: CreatorStyleSnapshot | null;
  script_intent?: ProductionScriptIntent | null;
  workflow_notes: string;
  platform: string;
  target_duration: number | null;
  selling_points: string;
  offer: string;
  revision: number;
  content_revision: number;
  stage: string;
  selected_direction_id: string | null;
  brand: {
    id: string;
    name: string;
    product: string;
    style: string;
    sources: Array<{
      id: string;
      filename: string;
      page: number;
      text: string;
      preview: string | null;
      images: string[];
      visual_summary: string;
    }>;
    facts: Array<{ id: string; text: string; source_id: string }>;
  };
  directions: ProductionDirection[];
  shots: ProductionShot[];
  narration_draft?: ProductionNarrationDraft | null;
  narration_fact_warnings?: string[];
  assets: ProductionAsset[];
  music_asset_id: string | null;
  renders: ProductionRender[];
  heygen_jobs: HeyGenJob[];
  readiness: Record<string, string[]>;
  planning_status: "idle" | "running" | "done" | "failed";
  planning_note: string;
  warnings: string[];
}
export interface CreateProduction {
  name: string;
  project_type: ProductionType;
  presenter_id: string | null;
  presenter_overrides?: PresenterConfig | null;
  presenter_revision?: number | null;
  script_intent?: ProductionScriptIntent | null;
  brand_name: string;
  product: string;
  platform: string;
  target_duration: number | null;
  selling_points: string;
  offer: string;
  reuse_brand_project_id?: string | null;
}

export class ProductionApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
    this.name = "ProductionApiError";
  }
}

async function request<T>(
  path = "",
  options: RequestInit = {},
  timeoutMs = options.method ? 60000 : 15000,
): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`/api/productions${path}`, {
      ...options,
      signal: controller.signal,
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new ProductionApiError(
        typeof data.detail === "string"
          ? data.detail
          : Array.isArray(data.detail) &&
              typeof data.detail[0]?.msg === "string"
            ? data.detail[0].msg.replace(/^Value error, /, "")
            : `操作未完成（${response.status}），请检查服务连接或稍后重试`,
        response.status,
      );
    }
    return await response.json();
  } catch (error) {
    if (controller.signal.aborted) {
      throw new ProductionApiError(
        options.method
          ? "请求超时，保存结果尚未确认。请重新读取档案或项目核对后再操作。"
          : "读取超时，请检查本地服务连接后重试。",
        408,
      );
    }
    if (error instanceof TypeError) {
      throw new ProductionApiError(
        "无法连接本地服务，请确认后端已启动后重试。",
        0,
      );
    }
    if (error instanceof SyntaxError) {
      throw new ProductionApiError(
        "服务返回的数据无法读取，请确认前后端已更新后重试。",
        502,
      );
    }
    throw error;
  } finally {
    clearTimeout(timer);
  }
}
const json = (body: unknown, method = "POST"): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});
export const listProductions = () => request<ProductionSummary[]>();
export const getHeyGenSettings = (projectId?: string) =>
  request<HeyGenSettings>(
    `/heygen/settings${projectId ? `?project_id=${encodeURIComponent(projectId)}` : ""}`,
  );
export const getHeyGenCatalog = (api_key: string) =>
  request<HeyGenCatalog>("/heygen/catalog", json({ api_key }));
export const saveHeyGenSettings = (
  api_key: string,
  avatar_id: string,
  voice_id: string,
) =>
  request<HeyGenSettings>(
    "/heygen/settings",
    json({ api_key, avatar_id, voice_id }, "PUT"),
  );
function parsePresenter(value: unknown): ProductionPresenter {
  const invalid = () =>
    new ProductionApiError(
      "数字人档案格式无法读取，请确认后端已更新后重试。",
      502,
    );
  if (!value || typeof value !== "object") throw invalid();
  const row = value as ProductionPresenter;
  if (
    typeof row.id !== "string" ||
    typeof row.name !== "string" ||
    typeof row.primary_audience !== "string" ||
    !Number.isSafeInteger(row.revision) ||
    row.revision < 1 ||
    !Array.isArray(row.address) ||
    row.address.some((item) => typeof item !== "string") ||
    (row.guidance !== undefined &&
      (!Array.isArray(row.guidance) ||
        row.guidance.some((item) => typeof item !== "string"))) ||
    (row.heygen !== undefined &&
      (!row.heygen ||
        typeof row.heygen !== "object" ||
        Array.isArray(row.heygen))) ||
    (row.archived !== undefined && typeof row.archived !== "boolean")
  )
    throw invalid();
  const config = presenterConfig(row);
  for (const [key, entry] of Object.entries(config)) {
    if (key === "heygen") {
      if (Object.values(entry).some((item) => typeof item !== "string"))
        throw invalid();
    } else if (key === "address" || key === "guidance") {
      if (
        !Array.isArray(entry) ||
        entry.some((item) => typeof item !== "string")
      )
        throw invalid();
    } else if (typeof entry !== "string") throw invalid();
  }
  return {
    ...config,
    id: row.id,
    revision: row.revision,
    archived: row.archived ?? false,
  };
}

export const listProductionPresenters = async (includeArchived = false) => {
  const rows = await request<unknown>(
    `/presenters${includeArchived ? "?include_archived=true" : ""}`,
  );
  if (!Array.isArray(rows))
    throw new ProductionApiError(
      "数字人列表格式无法读取，请确认后端已更新后重试。",
      502,
    );
  return rows.map(parsePresenter);
};
export interface WritingRuleDocument {
  title: string;
  path: string;
  kind: "skill" | "creator_rule" | "shared_rule" | "style_card" | "evidence";
  content: string;
  sha256: string;
}
export interface PresenterWritingRules {
  stage: "structure" | "polish";
  skill_name: string;
  title: string;
  timing: string;
  documents: WritingRuleDocument[];
  effective_prompt: string;
  style_source: "project_snapshot" | "current_catalog";
}
export const getPresenterWritingRules = (
  stage: "structure" | "polish",
  presenterId?: string,
  projectId?: string,
) => {
  const params = new URLSearchParams({ stage });
  if (presenterId) params.set("presenter_id", presenterId);
  if (projectId) params.set("project_id", projectId);
  return request<PresenterWritingRules>(`/presenter-workflow?${params}`);
};
export const createProductionPresenter = async (body: PresenterConfig) =>
  parsePresenter(await request<unknown>("/presenters", json(body)));
export const updateProductionPresenter = (
  profile: ProductionPresenter,
  body: PresenterConfig,
  archived = profile.archived,
) =>
  request<unknown>(
    `/presenters/${profile.id}`,
    json({ ...body, archived, expected_revision: profile.revision }, "PUT"),
  ).then(parsePresenter);
export const getProduction = (id: string) =>
  request<ProductionProject>(`/${id}`);
export const createProduction = (body: CreateProduction) =>
  request<ProductionProject>("", json(body));
export const productionAction = (
  project: ProductionProject,
  path: string,
  body: Record<string, unknown> = {},
  method = "POST",
) =>
  request<ProductionProject>(
    `/${project.id}/${path}`,
    json({ expected_revision: project.revision, ...body }, method),
  );
export async function uploadProductionFile(
  project: ProductionProject,
  file: File,
  kind: "brief" | "asset",
  role = "footage",
  shotId?: string,
) {
  const body = new FormData();
  body.append("file", file);
  body.append("expected_revision", String(project.revision));
  if (kind === "asset") body.append("role", role);
  if (shotId) body.append("shot_id", shotId);
  return request<ProductionProject>(
    `/${project.id}/${kind === "brief" ? "brief/files" : "assets"}`,
    { method: "POST", body },
    10 * 60 * 1000,
  );
}
export const cancelProductionRender = (projectId: string, jobId: string) =>
  request<ProductionProject>(`/${projectId}/renders/${jobId}/cancel`, json({}));
