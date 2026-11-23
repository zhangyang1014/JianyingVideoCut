/**
 * useAppConfig — 全局应用配置持久化 Hook
 * 配置读写后端 backend/data/app_config.json；启动时从 API 加载，并一次性迁移旧 localStorage。
 */

import { useState, useCallback, useEffect, useRef } from "react";
import { fetchAppConfig, putAppConfig, type AppConfigPayload } from "@/lib/api";

const APP_CONFIG_KEY = "goldenclip_app_config";
export const OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1";
export const ROUTERHUB_BASE_URL = "https://api.routerhub.ai/v1";
export const ROUTERHUB_GLM_52_MODEL = "z-ai/glm-5.2";
export const ROUTERHUB_CLAUDE_OPUS_MODEL = "anthropic/claude-opus-4.8";
export const ROUTERHUB_CLAUDE_SONNET_MODEL = "anthropic/claude-sonnet-4.6";
export const ROUTERHUB_GPT_56_SOL_MODEL = "openai/gpt-5.6-sol";
export const ROUTERHUB_GEMINI_PRO_MODEL = "google/gemini-3.1-pro-preview";
export const DEFAULT_AUDIT_MODEL = ROUTERHUB_GLM_52_MODEL;
export const DEFAULT_TRANSLATE_MODEL = ROUTERHUB_CLAUDE_SONNET_MODEL;
export const DEFAULT_HIGHLIGHT_REEL_MODEL = ROUTERHUB_CLAUDE_OPUS_MODEL;
export const DEFAULT_GOLDEN_QUOTE_MODEL = ROUTERHUB_GLM_52_MODEL;
export const DEFAULT_LEARNING_MODEL = ROUTERHUB_CLAUDE_OPUS_MODEL;
export const DEFAULT_TTS_MODEL = "mistralai/voxtral-mini-tts-2603";
export const DEFAULT_TTS_VOICE = "en_paul_neutral";

export type LLMProvider = "openai" | "ollama";
export type FeatureKey = "audit" | "translate" | "highlight_reel" | "golden_quote" | "learning" | "tts";
export type FeatureMode = "cloud" | "local";
export type LlmRoleKey =
  | "audit.semantic"
  | "audit.coherence"
  | "translate.subtitle"
  | "golden_quote.extract"
  | "highlight.strategy"
  | "highlight.validation"
  | "learning.analyze"
  | "learning.rewrite"
  | "prompt_feedback.optimize";

export interface ModelPricingConfig {
  inputPerMillionTokens?: number;
  outputPerMillionTokens?: number;
  reasoningPerMillionTokens?: number;
  perRequest?: number;
  perMillionCharacters?: number;
}

export interface CloudModelConfig {
  providerLabel: string;
  baseUrl: string;
  apiKey: string;
  model: string;
  pricing: ModelPricingConfig;
}

export interface LocalModelConfig {
  baseUrl: string;
  model: string;
}

export interface FeatureModelConfig {
  mode: FeatureMode;
  cloud: CloudModelConfig;
  local: LocalModelConfig;
}

export interface TtsFeatureConfig {
  mode: "cloud" | "silent";
  cloud: CloudModelConfig;
  voice: string;
}

export interface FeatureLlmConfigs {
  audit: FeatureModelConfig;
  translate: FeatureModelConfig;
  highlight_reel: FeatureModelConfig;
  golden_quote: FeatureModelConfig;
  learning: FeatureModelConfig;
  tts: TtsFeatureConfig;
}

export interface ProviderProfile {
  id: string;
  label: string;
  provider: "openai_compatible" | "ollama";
  baseUrl: string;
  apiKey: string;
  apiKeyEnv: string;
  enabled: boolean;
}

export interface LlmRouteCandidate {
  providerProfileId: string;
  model: string;
  tier: "primary" | "fallback" | "escalation";
  enabled: boolean;
  temperature?: number;
  maxTokens?: number;
  thinking: "auto" | "enabled" | "disabled";
  extraBody: Record<string, unknown>;
  pricing: ModelPricingConfig;
}

export interface LlmRoleRoute {
  label: string;
  purpose: string;
  candidates: LlmRouteCandidate[];
  qualityGateEscalation: boolean;
}

export interface AsrRuntimeConfig {
  funasrChunkMinutes: number;
  funasrParallelChunks: number;
}

export interface AuditRuntimeConfig {
  cloudChunkSegments: number;
  cloudMaxParallelChunks: number;
  cloudInitialParallelChunks: number;
  cloudChunkTokenBudget: number;
  enableBatchResume: boolean;
  enableIncrementalCache: boolean;
  coherenceStrategy: "always" | "auto" | "local_first";
}

export interface HighlightRuntimeConfig {
  cloudMaxParallelCalls: number;
}

export interface AppConfig {
  schemaVersion: number;
  openaiApiKey: string;
  openaiModel: string;
  jianyingDir: string;
  /** 旧版全局 provider，保留兼容；新逻辑以 featureLlm 为准 */
  provider: LLMProvider;
  ollamaModel: string;
  ollamaBaseUrl: string;
  featureLlm: FeatureLlmConfigs;
  providerProfiles: ProviderProfile[];
  llmRoleRoutes: Record<LlmRoleKey, LlmRoleRoute>;
  asr: AsrRuntimeConfig;
  auditRuntime: AuditRuntimeConfig;
  highlightRuntime: HighlightRuntimeConfig;
}

export interface ResolvedFeatureLlm {
  feature: FeatureKey;
  mode: FeatureMode | "silent";
  provider: "openai" | "ollama" | "silent";
  model: string;
  baseUrl?: string;
  apiKey: string;
  voice?: string;
}

const DEFAULT_CLOUD: CloudModelConfig = {
  providerLabel: "RouterHub",
  baseUrl: ROUTERHUB_BASE_URL,
  apiKey: "",
  model: DEFAULT_TRANSLATE_MODEL,
  pricing: {},
};

const DEFAULT_LOCAL: LocalModelConfig = {
  baseUrl: "http://localhost:11434",
  model: "deepseek-r1:14b",
};

function defaultFeature(cloudModel: string, localModel: string): FeatureModelConfig {
  return {
    mode: "cloud",
    cloud: { ...DEFAULT_CLOUD, model: cloudModel },
    local: { ...DEFAULT_LOCAL, model: localModel },
  };
}

const DEFAULT_FEATURE_LLM: FeatureLlmConfigs = {
  audit: defaultFeature(DEFAULT_AUDIT_MODEL, "qwen2.5:32b"),
  translate: defaultFeature(DEFAULT_TRANSLATE_MODEL, "qwen2.5:14b"),
  highlight_reel: defaultFeature(DEFAULT_HIGHLIGHT_REEL_MODEL, "qwen2.5:32b"),
  golden_quote: defaultFeature(DEFAULT_GOLDEN_QUOTE_MODEL, "qwen2.5:14b"),
  learning: defaultFeature(DEFAULT_LEARNING_MODEL, "qwen2.5:32b"),
  tts: {
    mode: "cloud",
    cloud: {
      providerLabel: "OpenRouter TTS",
      baseUrl: OPENROUTER_BASE_URL,
      apiKey: "",
      model: DEFAULT_TTS_MODEL,
      pricing: {},
    },
    voice: DEFAULT_TTS_VOICE,
  },
};

export const DEFAULT_PROVIDER_PROFILES: ProviderProfile[] = [
  { id: "routerhub", label: "RouterHub", provider: "openai_compatible", baseUrl: ROUTERHUB_BASE_URL, apiKey: "", apiKeyEnv: "ROUTERHUB_API_KEY", enabled: true },
  { id: "volcengine-ark", label: "火山方舟", provider: "openai_compatible", baseUrl: "https://ark.cn-beijing.volces.com/api/v3", apiKey: "", apiKeyEnv: "VOLCENGINE_ARK_API_KEY", enabled: true },
  { id: "zhipu", label: "智谱开放平台", provider: "openai_compatible", baseUrl: "https://open.bigmodel.cn/api/paas/v4", apiKey: "", apiKeyEnv: "ZHIPU_API_KEY", enabled: true },
  { id: "openrouter", label: "OpenRouter", provider: "openai_compatible", baseUrl: OPENROUTER_BASE_URL, apiKey: "", apiKeyEnv: "OPENROUTER_API_KEY", enabled: true },
  { id: "ollama-local", label: "本地 Ollama", provider: "ollama", baseUrl: "http://localhost:11434", apiKey: "", apiKeyEnv: "", enabled: true },
];

const routeCandidate = (
  providerProfileId: string,
  model: string,
  tier: LlmRouteCandidate["tier"],
  reasoningEffort?: "medium" | "high",
): LlmRouteCandidate => ({
  providerProfileId,
  model,
  tier,
  enabled: true,
  thinking: "auto",
  extraBody: reasoningEffort ? { reasoning: { effort: reasoningEffort } } : {},
  pricing: {},
});

export const DEFAULT_LLM_ROLE_ROUTES: Record<LlmRoleKey, LlmRoleRoute> = {
  "audit.semantic": { label: "语义审计", purpose: "复杂语义、多约束片段判定", qualityGateEscalation: true, candidates: [routeCandidate("routerhub", ROUTERHUB_GLM_52_MODEL, "primary", "high"), routeCandidate("routerhub", ROUTERHUB_CLAUDE_OPUS_MODEL, "fallback", "high"), routeCandidate("routerhub", ROUTERHUB_GPT_56_SOL_MODEL, "escalation", "high")] },
  "audit.coherence": { label: "连贯性复核", purpose: "独立检查上下文与修复建议", qualityGateEscalation: true, candidates: [routeCandidate("routerhub", ROUTERHUB_GPT_56_SOL_MODEL, "primary", "high"), routeCandidate("routerhub", ROUTERHUB_CLAUDE_OPUS_MODEL, "fallback", "high"), routeCandidate("routerhub", ROUTERHUB_GEMINI_PRO_MODEL, "escalation", "high")] },
  "translate.subtitle": { label: "字幕翻译", purpose: "高频字幕校准与翻译", qualityGateEscalation: false, candidates: [routeCandidate("routerhub", ROUTERHUB_CLAUDE_SONNET_MODEL, "primary"), routeCandidate("routerhub", ROUTERHUB_GEMINI_PRO_MODEL, "fallback")] },
  "golden_quote.extract": { label: "金句提取", purpose: "中文表达判断与钩子候选抽取", qualityGateEscalation: false, candidates: [routeCandidate("routerhub", ROUTERHUB_GLM_52_MODEL, "primary", "medium"), routeCandidate("routerhub", ROUTERHUB_CLAUDE_SONNET_MODEL, "fallback", "medium"), routeCandidate("routerhub", ROUTERHUB_GPT_56_SOL_MODEL, "escalation", "medium")] },
  "highlight.strategy": { label: "集锦策略生成", purpose: "复杂片段选择与叙事编排", qualityGateEscalation: true, candidates: [routeCandidate("routerhub", ROUTERHUB_CLAUDE_OPUS_MODEL, "primary", "high"), routeCandidate("routerhub", ROUTERHUB_GPT_56_SOL_MODEL, "fallback", "high"), routeCandidate("routerhub", ROUTERHUB_GEMINI_PRO_MODEL, "escalation", "high")] },
  "highlight.validation": { label: "集锦独立质检", purpose: "跨模型验收事实与结构完整性", qualityGateEscalation: true, candidates: [routeCandidate("routerhub", ROUTERHUB_GPT_56_SOL_MODEL, "primary", "high"), routeCandidate("routerhub", ROUTERHUB_GEMINI_PRO_MODEL, "fallback", "high"), routeCandidate("routerhub", ROUTERHUB_CLAUDE_OPUS_MODEL, "escalation", "high")] },
  "learning.analyze": { label: "学习分析", purpose: "长上下文证据归因与规则提炼", qualityGateEscalation: true, candidates: [routeCandidate("routerhub", ROUTERHUB_CLAUDE_OPUS_MODEL, "primary", "high"), routeCandidate("routerhub", ROUTERHUB_GPT_56_SOL_MODEL, "fallback", "high"), routeCandidate("routerhub", ROUTERHUB_GEMINI_PRO_MODEL, "escalation", "high")] },
  "learning.rewrite": { label: "Prompt 改写", purpose: "自然中文与规则块重写", qualityGateEscalation: false, candidates: [routeCandidate("routerhub", ROUTERHUB_GLM_52_MODEL, "primary", "medium"), routeCandidate("routerhub", ROUTERHUB_CLAUDE_SONNET_MODEL, "fallback", "medium"), routeCandidate("routerhub", ROUTERHUB_GPT_56_SOL_MODEL, "escalation", "medium")] },
  "prompt_feedback.optimize": { label: "反馈优化", purpose: "根据人工修正优化中文 Prompt", qualityGateEscalation: false, candidates: [routeCandidate("routerhub", ROUTERHUB_GLM_52_MODEL, "primary", "medium"), routeCandidate("routerhub", ROUTERHUB_CLAUDE_SONNET_MODEL, "fallback", "medium"), routeCandidate("routerhub", ROUTERHUB_GPT_56_SOL_MODEL, "escalation", "medium")] },
};

const DEFAULT_CONFIG: AppConfig = {
  schemaVersion: 5,
  openaiApiKey: "",
  openaiModel: DEFAULT_TRANSLATE_MODEL,
  jianyingDir: "",
  provider: "openai",
  ollamaModel: "deepseek-r1:14b",
  ollamaBaseUrl: "http://localhost:11434",
  featureLlm: DEFAULT_FEATURE_LLM,
  providerProfiles: DEFAULT_PROVIDER_PROFILES,
  llmRoleRoutes: DEFAULT_LLM_ROLE_ROUTES,
  asr: {
    funasrChunkMinutes: 5,
    funasrParallelChunks: 4,
  },
  auditRuntime: {
    cloudChunkSegments: 120,
    cloudMaxParallelChunks: 8,
    cloudInitialParallelChunks: 8,
    cloudChunkTokenBudget: 24000,
    enableBatchResume: true,
    enableIncrementalCache: true,
    coherenceStrategy: "auto",
  },
  highlightRuntime: {
    cloudMaxParallelCalls: 3,
  },
};

function migrateLegacyToFeatureLlm(payload: AppConfigPayload): FeatureLlmConfigs {
  const provider = payload.provider === "ollama" ? "ollama" : "openai";
  const mode: FeatureMode = provider === "ollama" ? "local" : "cloud";
  const globalKey = payload.openai_api_key ?? "";
  const ollamaModel = payload.ollama_model ?? DEFAULT_CONFIG.ollamaModel;
  const ollamaBaseUrl = payload.ollama_base_url ?? DEFAULT_CONFIG.ollamaBaseUrl;

  const cloud = (model: string): CloudModelConfig => ({
    providerLabel: "RouterHub",
    baseUrl: ROUTERHUB_BASE_URL,
    apiKey: globalKey,
    model,
    pricing: {},
  });
  const local = (model: string): LocalModelConfig => ({
    baseUrl: ollamaBaseUrl,
    model,
  });

  return {
    audit: { mode, cloud: cloud(DEFAULT_AUDIT_MODEL), local: local("qwen2.5:32b") },
    translate: { mode, cloud: cloud(DEFAULT_TRANSLATE_MODEL), local: local("qwen2.5:14b") },
    highlight_reel: { mode, cloud: cloud(DEFAULT_HIGHLIGHT_REEL_MODEL), local: local("qwen2.5:32b") },
    golden_quote: { mode, cloud: cloud(DEFAULT_GOLDEN_QUOTE_MODEL), local: local("qwen2.5:14b") },
    learning: { mode, cloud: cloud(DEFAULT_LEARNING_MODEL), local: local("qwen2.5:32b") },
    tts: {
      mode: mode === "cloud" ? "cloud" : "silent",
      cloud: {
        providerLabel: "OpenRouter TTS",
        baseUrl: OPENROUTER_BASE_URL,
        apiKey: globalKey,
        model: DEFAULT_TTS_MODEL,
        pricing: {},
      },
      voice: DEFAULT_TTS_VOICE,
    },
  };
}

function cloudFromPayload(raw: Record<string, unknown> | undefined): CloudModelConfig {
  if (!raw) return { ...DEFAULT_CLOUD };
  const pricing = (raw.pricing ?? {}) as Record<string, unknown>;
  return {
    providerLabel: String(raw.provider_label ?? raw.providerLabel ?? "RouterHub"),
    baseUrl: String(raw.base_url ?? raw.baseUrl ?? DEFAULT_CLOUD.baseUrl),
    apiKey: String(raw.api_key ?? raw.apiKey ?? ""),
    model: String(raw.model ?? DEFAULT_CLOUD.model),
    pricing: {
      inputPerMillionTokens: pricing.input_per_million_tokens == null ? undefined : Number(pricing.input_per_million_tokens),
      outputPerMillionTokens: pricing.output_per_million_tokens == null ? undefined : Number(pricing.output_per_million_tokens),
      reasoningPerMillionTokens: pricing.reasoning_per_million_tokens == null ? undefined : Number(pricing.reasoning_per_million_tokens),
      perRequest: pricing.per_request == null ? undefined : Number(pricing.per_request),
      perMillionCharacters: pricing.per_million_characters == null ? undefined : Number(pricing.per_million_characters),
    },
  };
}

function ttsCloudFromPayload(raw: Record<string, unknown> | undefined): CloudModelConfig {
  const cloud = cloudFromPayload(raw);
  return {
    ...cloud,
    providerLabel: String(raw?.provider_label ?? raw?.providerLabel ?? "OpenRouter TTS"),
    baseUrl: String(raw?.base_url ?? raw?.baseUrl ?? OPENROUTER_BASE_URL),
    model: String(raw?.model ?? DEFAULT_TTS_MODEL),
  };
}

function localFromPayload(l: Record<string, unknown> | undefined): LocalModelConfig {
  if (!l) return { ...DEFAULT_LOCAL };
  return {
    baseUrl: String(l.base_url ?? l.baseUrl ?? DEFAULT_LOCAL.baseUrl),
    model: String(l.model ?? DEFAULT_LOCAL.model),
  };
}

function featureFromPayload(
  raw: Record<string, unknown> | undefined,
  fallback: FeatureModelConfig,
): FeatureModelConfig {
  if (!raw) return fallback;
  return {
    mode: raw.mode === "local" ? "local" : "cloud",
    cloud: cloudFromPayload(raw.cloud as Record<string, unknown>),
    local: localFromPayload(raw.local as Record<string, unknown>),
  };
}

function featureLlmFromPayload(payload: AppConfigPayload): FeatureLlmConfigs {
  const fl = payload.feature_llm;
  if (!fl) return migrateLegacyToFeatureLlm(payload);

  const globalKey = payload.openai_api_key ?? "";
  const fillKey = (cloud: CloudModelConfig) =>
    cloud.apiKey ? cloud : { ...cloud, apiKey: globalKey };

  return {
    audit: featureFromPayload(fl.audit as Record<string, unknown>, DEFAULT_FEATURE_LLM.audit),
    translate: featureFromPayload(fl.translate as Record<string, unknown>, DEFAULT_FEATURE_LLM.translate),
    highlight_reel: featureFromPayload(fl.highlight_reel as Record<string, unknown>, DEFAULT_FEATURE_LLM.highlight_reel),
    golden_quote: featureFromPayload(fl.golden_quote as Record<string, unknown>, DEFAULT_FEATURE_LLM.golden_quote),
    learning: featureFromPayload(fl.learning as Record<string, unknown>, DEFAULT_FEATURE_LLM.learning),
    tts: (() => {
      const cloud = fillKey(ttsCloudFromPayload(fl.tts?.cloud as Record<string, unknown>));
      return {
      mode: fl.tts?.mode === "silent" ? "silent" : "cloud",
      cloud,
      voice: fl.tts?.voice ?? DEFAULT_TTS_VOICE,
      };
    })(),
  };
}

function asrFromPayload(payload: AppConfigPayload): AsrRuntimeConfig {
  const raw = payload.asr;
  return {
    funasrChunkMinutes: Number(raw?.funasr_chunk_minutes ?? DEFAULT_CONFIG.asr.funasrChunkMinutes),
    funasrParallelChunks: Number(raw?.funasr_parallel_chunks ?? DEFAULT_CONFIG.asr.funasrParallelChunks),
  };
}

function auditRuntimeFromPayload(payload: AppConfigPayload): AuditRuntimeConfig {
  const raw = payload.audit_runtime;
  const coherenceStrategy = raw?.coherence_strategy === "always" || raw?.coherence_strategy === "local_first"
    ? raw.coherence_strategy
    : "auto";
  return {
    cloudChunkSegments: Number(raw?.cloud_chunk_segments ?? DEFAULT_CONFIG.auditRuntime.cloudChunkSegments),
    cloudMaxParallelChunks: Number(raw?.cloud_max_parallel_chunks ?? DEFAULT_CONFIG.auditRuntime.cloudMaxParallelChunks),
    cloudInitialParallelChunks: Number(raw?.cloud_initial_parallel_chunks ?? DEFAULT_CONFIG.auditRuntime.cloudInitialParallelChunks),
    cloudChunkTokenBudget: Number(raw?.cloud_chunk_token_budget ?? DEFAULT_CONFIG.auditRuntime.cloudChunkTokenBudget),
    enableBatchResume: raw?.enable_batch_resume ?? DEFAULT_CONFIG.auditRuntime.enableBatchResume,
    enableIncrementalCache: raw?.enable_incremental_cache ?? DEFAULT_CONFIG.auditRuntime.enableIncrementalCache,
    coherenceStrategy,
  };
}

function highlightRuntimeFromPayload(payload: AppConfigPayload): HighlightRuntimeConfig {
  const rawValue = Number(
    payload.highlight_runtime?.cloud_max_parallel_calls
      ?? DEFAULT_CONFIG.highlightRuntime.cloudMaxParallelCalls,
  );
  return {
    cloudMaxParallelCalls: Number.isFinite(rawValue)
      ? Math.min(8, Math.max(1, Math.round(rawValue)))
      : DEFAULT_CONFIG.highlightRuntime.cloudMaxParallelCalls,
  };
}

function providerProfilesFromPayload(payload: AppConfigPayload): ProviderProfile[] {
  if (!payload.provider_profiles?.length) return DEFAULT_PROVIDER_PROFILES.map((item) => ({ ...item }));
  return payload.provider_profiles.map((profile) => ({
    id: profile.id,
    label: profile.label,
    provider: profile.provider,
    baseUrl: profile.base_url,
    apiKey: profile.api_key ?? "",
    apiKeyEnv: profile.api_key_env ?? "",
    enabled: profile.enabled ?? true,
  }));
}

function roleRoutesFromPayload(payload: AppConfigPayload): Record<LlmRoleKey, LlmRoleRoute> {
  const incoming = payload.llm_role_routes ?? {};
  return Object.fromEntries(
    (Object.keys(DEFAULT_LLM_ROLE_ROUTES) as LlmRoleKey[]).map((role) => {
      const raw = incoming[role];
      if (!raw) return [role, DEFAULT_LLM_ROLE_ROUTES[role]];
      return [role, {
        label: raw.label,
        purpose: raw.purpose,
        qualityGateEscalation: raw.quality_gate_escalation ?? false,
        candidates: raw.candidates.map((candidate) => ({
          providerProfileId: candidate.provider_profile_id,
          model: candidate.model,
          tier: candidate.tier,
          enabled: candidate.enabled ?? true,
          temperature: candidate.temperature ?? undefined,
          maxTokens: candidate.max_tokens ?? undefined,
          thinking: candidate.thinking ?? "auto",
          extraBody: candidate.extra_body ?? {},
          pricing: {
            inputPerMillionTokens: candidate.pricing?.input_per_million_tokens ?? undefined,
            outputPerMillionTokens: candidate.pricing?.output_per_million_tokens ?? undefined,
            reasoningPerMillionTokens: candidate.pricing?.reasoning_per_million_tokens ?? undefined,
            perRequest: candidate.pricing?.per_request ?? undefined,
            perMillionCharacters: candidate.pricing?.per_million_characters ?? undefined,
          },
        })),
      }];
    }),
  ) as Record<LlmRoleKey, LlmRoleRoute>;
}

function fromPayload(payload: AppConfigPayload): AppConfig {
  const rawProvider = payload.provider === "ollama" ? "ollama" : "openai";
  const featureLlm = featureLlmFromPayload(payload);
  return {
    schemaVersion: payload.schema_version ?? 5,
    openaiApiKey: payload.openai_api_key ?? "",
    openaiModel: payload.openai_model ?? DEFAULT_CONFIG.openaiModel,
    jianyingDir: payload.jianying_dir ?? "",
    provider: rawProvider,
    ollamaModel: payload.ollama_model ?? DEFAULT_CONFIG.ollamaModel,
    ollamaBaseUrl: payload.ollama_base_url ?? DEFAULT_CONFIG.ollamaBaseUrl,
    featureLlm,
    providerProfiles: providerProfilesFromPayload(payload),
    llmRoleRoutes: roleRoutesFromPayload(payload),
    asr: asrFromPayload(payload),
    auditRuntime: auditRuntimeFromPayload(payload),
    highlightRuntime: highlightRuntimeFromPayload(payload),
  };
}

function cloudToPayload(c: CloudModelConfig) {
  const pricing = c.pricing ?? {};
  return {
    provider_label: c.providerLabel,
    base_url: c.baseUrl,
    api_key: c.apiKey,
    model: c.model,
    pricing: {
      input_per_million_tokens: pricing.inputPerMillionTokens,
      output_per_million_tokens: pricing.outputPerMillionTokens,
      reasoning_per_million_tokens: pricing.reasoningPerMillionTokens,
      per_request: pricing.perRequest,
      per_million_characters: pricing.perMillionCharacters,
    },
  };
}

function localToPayload(l: LocalModelConfig) {
  return {
    base_url: l.baseUrl,
    model: l.model,
  };
}

function toPayload(config: AppConfig): AppConfigPayload {
  const audit = config.featureLlm.audit;
  const legacyProvider: LLMProvider = audit.mode === "local" ? "ollama" : "openai";
  const legacyOpenaiModel = audit.cloud.model || config.openaiModel;
  const legacyOllamaModel = audit.local.model || config.ollamaModel;
  const legacyKey = config.openaiApiKey || audit.cloud.apiKey;

  return {
    schema_version: config.schemaVersion,
    claude_api_key: "",
    openai_api_key: legacyKey,
    claude_model: "",
    openai_model: legacyOpenaiModel,
    jianying_dir: config.jianyingDir,
    provider: legacyProvider,
    ollama_model: legacyOllamaModel,
    ollama_base_url: audit.local.baseUrl || config.ollamaBaseUrl,
    feature_llm: {
      audit: {
        mode: audit.mode,
        cloud: cloudToPayload(audit.cloud),
        local: localToPayload(audit.local),
      },
      translate: {
        mode: config.featureLlm.translate.mode,
        cloud: cloudToPayload(config.featureLlm.translate.cloud),
        local: localToPayload(config.featureLlm.translate.local),
      },
      highlight_reel: {
        mode: config.featureLlm.highlight_reel.mode,
        cloud: cloudToPayload(config.featureLlm.highlight_reel.cloud),
        local: localToPayload(config.featureLlm.highlight_reel.local),
      },
      golden_quote: {
        mode: config.featureLlm.golden_quote.mode,
        cloud: cloudToPayload(config.featureLlm.golden_quote.cloud),
        local: localToPayload(config.featureLlm.golden_quote.local),
      },
      learning: {
        mode: config.featureLlm.learning.mode,
        cloud: cloudToPayload(config.featureLlm.learning.cloud),
        local: localToPayload(config.featureLlm.learning.local),
      },
      tts: {
        mode: config.featureLlm.tts.mode,
        cloud: cloudToPayload(config.featureLlm.tts.cloud),
        voice: config.featureLlm.tts.voice,
      },
    },
    provider_profiles: config.providerProfiles.map((profile) => ({
      id: profile.id,
      label: profile.label,
      provider: profile.provider,
      base_url: profile.baseUrl,
      api_key: profile.apiKey,
      api_key_env: profile.apiKeyEnv,
      enabled: profile.enabled,
    })),
    llm_role_routes: Object.fromEntries(
      Object.entries(config.llmRoleRoutes).map(([role, route]) => [
        role,
        {
          label: route.label,
          purpose: route.purpose,
          quality_gate_escalation: route.qualityGateEscalation,
          candidates: route.candidates.map((candidate) => ({
            provider_profile_id: candidate.providerProfileId,
            model: candidate.model,
            tier: candidate.tier,
            enabled: candidate.enabled,
            temperature: candidate.temperature,
            max_tokens: candidate.maxTokens,
            thinking: candidate.thinking,
            extra_body: candidate.extraBody,
            pricing: {
              input_per_million_tokens: candidate.pricing?.inputPerMillionTokens,
              output_per_million_tokens: candidate.pricing?.outputPerMillionTokens,
              reasoning_per_million_tokens: candidate.pricing?.reasoningPerMillionTokens,
              per_request: candidate.pricing?.perRequest,
              per_million_characters: candidate.pricing?.perMillionCharacters,
            },
          })),
        },
      ]),
    ),
    asr: {
      funasr_chunk_minutes: config.asr.funasrChunkMinutes,
      funasr_parallel_chunks: config.asr.funasrParallelChunks,
    },
    audit_runtime: {
      cloud_chunk_segments: config.auditRuntime.cloudChunkSegments,
      cloud_max_parallel_chunks: config.auditRuntime.cloudMaxParallelChunks,
      cloud_initial_parallel_chunks: config.auditRuntime.cloudInitialParallelChunks,
      cloud_chunk_token_budget: config.auditRuntime.cloudChunkTokenBudget,
      enable_batch_resume: config.auditRuntime.enableBatchResume,
      enable_incremental_cache: config.auditRuntime.enableIncrementalCache,
      coherence_strategy: config.auditRuntime.coherenceStrategy,
    },
    highlight_runtime: {
      cloud_max_parallel_calls: config.highlightRuntime.cloudMaxParallelCalls,
    },
  };
}

export const appConfigFromPayload = fromPayload;
export const appConfigToPayload = toPayload;

/** 从旧版 localStorage 读取配置（仅用于迁移） */
function loadLegacyLocalStorage(): AppConfig {
  try {
    const raw = localStorage.getItem(APP_CONFIG_KEY);
    if (!raw) return DEFAULT_CONFIG;
    const parsed = JSON.parse(raw);
    return {
      ...DEFAULT_CONFIG,
      ...parsed,
      provider: parsed?.provider === "ollama" ? "ollama" : "openai",
      openaiModel: parsed?.openaiModel ?? DEFAULT_CONFIG.openaiModel,
      featureLlm: { ...DEFAULT_FEATURE_LLM, ...(parsed?.featureLlm ?? {}) },
      providerProfiles: parsed?.providerProfiles ?? DEFAULT_PROVIDER_PROFILES,
      llmRoleRoutes: parsed?.llmRoleRoutes ?? DEFAULT_LLM_ROLE_ROUTES,
      asr: parsed?.asr ?? DEFAULT_CONFIG.asr,
      auditRuntime: parsed?.auditRuntime ?? DEFAULT_CONFIG.auditRuntime,
      highlightRuntime: {
        ...DEFAULT_CONFIG.highlightRuntime,
        ...(parsed?.highlightRuntime ?? {}),
      },
    };
  } catch {
    return DEFAULT_CONFIG;
  }
}

function hasApiKeys(config: AppConfig): boolean {
  return Boolean(
    config.openaiApiKey ||
    config.featureLlm.audit.cloud.apiKey ||
    config.featureLlm.translate.cloud.apiKey ||
    config.featureLlm.highlight_reel.cloud.apiKey ||
    config.providerProfiles.some((profile) => Boolean(profile.apiKey)),
  );
}

export type AppConfigLoadError = "backend_unavailable" | "backend_stale";

export function useAppConfig() {
  const [config, setConfig] = useState<AppConfig>(DEFAULT_CONFIG);
  const [loaded, setLoaded] = useState(false);
  const [loadError, setLoadError] = useState<AppConfigLoadError | null>(null);
  const configRef = useRef(config);
  configRef.current = config;

  useEffect(() => {
    let cancelled = false;

    (async () => {
      try {
        const remote = fromPayload(await fetchAppConfig());
        if (cancelled) return;

        const legacy = loadLegacyLocalStorage();
        if (!hasApiKeys(remote) && hasApiKeys(legacy)) {
          const merged = { ...remote, ...legacy };
          await putAppConfig(toPayload(merged));
          localStorage.removeItem(APP_CONFIG_KEY);
          setConfig(merged);
        } else {
          setConfig(remote);
        }
        setLoadError(null);
      } catch (err) {
        if (cancelled) return;
        setConfig(loadLegacyLocalStorage());
        const msg = err instanceof Error ? err.message : "";
        setLoadError(msg === "Not Found" ? "backend_stale" : "backend_unavailable");
      } finally {
        if (!cancelled) setLoaded(true);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  const updateConfig = useCallback(async (patch: Partial<AppConfig>) => {
    const next = { ...configRef.current, ...patch };
    await putAppConfig(toPayload(next));
    setConfig(next);
    setLoadError(null);
  }, []);

  return { config, updateConfig, loaded, loadError };
}

// ============================================================
// 共享常量与工具函数
// ============================================================

export interface OpenAIModelOption {
  value: string;
  label: string;
  shortLabel: string;
  desc: string;
  group?: "recommended" | "quality" | "balanced" | "economy" | "experimental";
}

export const OPENAI_MODELS: OpenAIModelOption[] = [
  { value: "openai/gpt-5.4", label: "GPT-5.4", shortLabel: "GPT-5.4", desc: "最高质量 · 关键任务复核", group: "quality" },
  { value: "openai/gpt-5.4-mini", label: "GPT-5.4 Mini", shortLabel: "5.4 Mini", desc: "稳定 JSON · 成本可控", group: "balanced" },
  { value: "openai/gpt-5.4-nano", label: "GPT-5.4 Nano", shortLabel: "5.4 Nano", desc: "低成本 · 响应快", group: "economy" },
];

export const LOCAL_MODEL_SUGGESTIONS: Record<FeatureKey, string[]> = {
  audit: ["qwen2.5:32b", "qwen3:32b", "qwen2.5:14b"],
  translate: ["qwen2.5:14b", "qwen2.5:32b"],
  highlight_reel: ["qwen2.5:32b", "qwen3:32b", "qwen2.5:14b"],
  golden_quote: ["qwen2.5:14b"],
  learning: ["qwen2.5:32b", "qwen3:32b", "qwen2.5:14b"],
  tts: [],
};

export const CLOUD_MODEL_SUGGESTIONS: Record<FeatureKey, OpenAIModelOption[]> = {
  audit: [
    { value: DEFAULT_AUDIT_MODEL, label: "GLM-5.2", shortLabel: "GLM-5.2", desc: "推荐默认 · 中文复杂语义与多约束判断", group: "recommended" },
    { value: ROUTERHUB_CLAUDE_OPUS_MODEL, label: "Claude Opus 4.8", shortLabel: "Opus 4.8", desc: "深层语义 · 长上下文证据分析", group: "quality" },
    { value: ROUTERHUB_GPT_56_SOL_MODEL, label: "GPT-5.6 Sol", shortLabel: "GPT-5.6 Sol", desc: "独立复核 · 稳定结构化输出", group: "quality" },
    { value: ROUTERHUB_GEMINI_PRO_MODEL, label: "Gemini 3.1 Pro", shortLabel: "Gemini 3.1", desc: "长上下文 · 跨模型质检", group: "balanced" },
    { value: "openai/gpt-5.4-mini", label: "GPT-5.4 Mini", shortLabel: "5.4 Mini", desc: "经济备选 · 稳定 JSON", group: "economy" },
  ],
  translate: [
    { value: DEFAULT_TRANSLATE_MODEL, label: "Claude Sonnet 4.6", shortLabel: "Sonnet 4.6", desc: "推荐默认 · 语境自然 · 文风与原意兼顾", group: "recommended" },
    { value: ROUTERHUB_GEMINI_PRO_MODEL, label: "Gemini 3.1 Pro", shortLabel: "Gemini 3.1", desc: "高质量多语言 · 长上下文字幕", group: "quality" },
    { value: "openai/gpt-5.4", label: "GPT-5.4", shortLabel: "GPT-5.4", desc: "英文自然 · 指令与术语稳定", group: "quality" },
    { value: "google/gemini-2.5-flash", label: "Gemini 2.5 Flash", shortLabel: "Gemini Flash", desc: "批量字幕 · 速度与成本平衡", group: "balanced" },
    { value: "qwen/qwen3.7-plus", label: "Qwen3.7 Plus", shortLabel: "Qwen3.7+", desc: "经济备选 · 中文语境与术语一致", group: "economy" },
  ],
  highlight_reel: [
    { value: DEFAULT_HIGHLIGHT_REEL_MODEL, label: "Claude Opus 4.8", shortLabel: "Opus 4.8", desc: "推荐默认 · 复杂叙事与片段编排", group: "recommended" },
    { value: ROUTERHUB_GPT_56_SOL_MODEL, label: "GPT-5.6 Sol", shortLabel: "GPT-5.6 Sol", desc: "独立质检 · 事实与结构验收", group: "quality" },
    { value: ROUTERHUB_GEMINI_PRO_MODEL, label: "Gemini 3.1 Pro", shortLabel: "Gemini 3.1", desc: "长上下文 · 跨模型验收", group: "quality" },
    { value: ROUTERHUB_GLM_52_MODEL, label: "GLM-5.2", shortLabel: "GLM-5.2", desc: "中文叙事 · 表达与语义判断", group: "balanced" },
    { value: "openai/gpt-5.4-mini", label: "GPT-5.4 Mini", shortLabel: "5.4 Mini", desc: "经济备选 · 稳定计划 JSON", group: "economy" },
  ],
  golden_quote: [
    { value: DEFAULT_GOLDEN_QUOTE_MODEL, label: "GLM-5.2", shortLabel: "GLM-5.2", desc: "推荐默认 · 中文表达与钩子判断", group: "recommended" },
    { value: ROUTERHUB_CLAUDE_SONNET_MODEL, label: "Claude Sonnet 4.6", shortLabel: "Sonnet 4.6", desc: "文风自然 · 候选质量复核", group: "quality" },
    { value: ROUTERHUB_GPT_56_SOL_MODEL, label: "GPT-5.6 Sol", shortLabel: "GPT-5.6 Sol", desc: "高质量升级 · 结构化候选", group: "quality" },
    { value: "anthropic/claude-haiku-4.5", label: "Claude Haiku 4.5", shortLabel: "Haiku 4.5", desc: "快速批量 · 表达判断", group: "balanced" },
    { value: "deepseek/deepseek-v4-flash", label: "DeepSeek V4 Flash", shortLabel: "DeepSeek V4", desc: "经济备选 · 多候选抽取", group: "economy" },
  ],
  learning: [
    { value: DEFAULT_LEARNING_MODEL, label: "Claude Opus 4.8", shortLabel: "Opus 4.8", desc: "推荐默认 · 长上下文证据归因", group: "recommended" },
    { value: ROUTERHUB_GPT_56_SOL_MODEL, label: "GPT-5.6 Sol", shortLabel: "GPT-5.6 Sol", desc: "规则提炼 · 独立结构化复核", group: "quality" },
    { value: ROUTERHUB_GLM_52_MODEL, label: "GLM-5.2", shortLabel: "GLM-5.2", desc: "中文 Prompt 改写 · 文风自然", group: "quality" },
    { value: ROUTERHUB_GEMINI_PRO_MODEL, label: "Gemini 3.1 Pro", shortLabel: "Gemini 3.1", desc: "长上下文 · 跨模型归纳", group: "balanced" },
    { value: ROUTERHUB_CLAUDE_SONNET_MODEL, label: "Claude Sonnet 4.6", shortLabel: "Sonnet 4.6", desc: "高质量改写 · 成本较低", group: "balanced" },
  ],
  tts: [
    { value: DEFAULT_TTS_MODEL, label: "Voxtral Mini TTS", shortLabel: "Voxtral TTS", desc: "推荐默认 · 多语言 · OpenRouter Speech", group: "recommended" },
    { value: "openai/gpt-4o-mini-tts-2025-12-15", label: "GPT-4o Mini TTS", shortLabel: "4o TTS", desc: "OpenAI 语音 · alloy/nova 等声音", group: "quality" },
    { value: "microsoft/mai-voice-2", label: "MAI Voice 2", shortLabel: "MAI Voice", desc: "Azure 语音 · 风格化可选", group: "experimental" },
  ],
};

export const MODEL_GROUP_LABELS: Record<NonNullable<OpenAIModelOption["group"]>, string> = {
  recommended: "推荐默认",
  quality: "质量优先",
  balanced: "稳定均衡",
  economy: "经济批量",
  experimental: "实验/预览",
};

export const TTS_VOICE_SUGGESTIONS: Record<string, string[]> = {
  [DEFAULT_TTS_MODEL]: [
    "en_paul_neutral",
    "en_paul_confident",
    "en_paul_cheerful",
    "gb_jane_neutral",
    "gb_oliver_neutral",
  ],
  "openai/gpt-4o-mini-tts-2025-12-15": ["alloy", "nova", "shimmer", "echo", "fable", "onyx"],
  "microsoft/mai-voice-2": ["en-US-Harper:MAI-Voice-2"],
};

export const FEATURE_TABS: { key: FeatureKey; label: string; desc: string }[] = [
  { key: "audit", label: "语义审计", desc: "口播精修 / 访谈压缩 / 直播卖货集锦的 AI 片段判定" },
  { key: "highlight_reel", label: "产品集锦质检", desc: "直播卖货集锦计划的模型语义质检" },
  { key: "translate", label: "字幕翻译", desc: "一键转英文：字幕校准与目标语言翻译" },
  { key: "golden_quote", label: "金句推荐", desc: "识别适合作为开场的金句片段" },
  { key: "learning", label: "Prompt 学习", desc: "基于人工修正归纳规则、改写 Prompt" },
  { key: "tts", label: "TTS 配音", desc: "目标语言语音合成（非 Ollama LLM）" },
];

export const LLM_PROVIDERS: { value: LLMProvider; label: string; desc: string }[] = [
  { value: "openai", label: "OpenAI API", desc: "云端推理 · 语义审计与翻译" },
  { value: "ollama", label: "本地 Ollama", desc: "完全离线 · 数据不出本机" },
];

const FEATURE_PRIMARY_ROLE: Partial<Record<FeatureKey, LlmRoleKey>> = {
  audit: "audit.semantic",
  translate: "translate.subtitle",
  highlight_reel: "highlight.validation",
  golden_quote: "golden_quote.extract",
  learning: "learning.analyze",
};

function resolveRolePrimary(config: AppConfig, feature: FeatureKey): ResolvedFeatureLlm | null {
  const role = FEATURE_PRIMARY_ROLE[feature];
  if (!role) return null;
  const candidate = config.llmRoleRoutes?.[role]?.candidates.find((item) => item.enabled);
  if (!candidate) return null;
  const profile = config.providerProfiles?.find(
    (item) => item.id === candidate.providerProfileId && item.enabled,
  );
  if (!profile) return null;
  return {
    feature,
    mode: profile.provider === "ollama" ? "local" : "cloud",
    provider: profile.provider === "ollama" ? "ollama" : "openai",
    model: candidate.model,
    baseUrl: profile.baseUrl,
    apiKey: profile.provider === "ollama" ? "ollama" : profile.apiKey,
  };
}

/** 解析某功能的运行时 LLM 参数 */
export function resolveFeatureLlm(config: AppConfig, feature: FeatureKey): ResolvedFeatureLlm {
  if (feature === "tts") {
    const tts = config.featureLlm.tts;
    if (tts.mode === "silent") {
      return {
        feature,
        mode: "silent",
        provider: "silent",
        model: "aligned-silent",
        apiKey: "",
        voice: tts.voice,
      };
    }
    return {
      feature,
      mode: "cloud",
      provider: "openai",
      model: tts.cloud.model || DEFAULT_TTS_MODEL,
      baseUrl: tts.cloud.baseUrl,
      apiKey: tts.cloud.apiKey || config.openaiApiKey,
      voice: tts.voice,
    };
  }

  const rolePrimary = resolveRolePrimary(config, feature);
  if (rolePrimary) return rolePrimary;

  const feat = config.featureLlm[feature];
  if (feat.mode === "local") {
    return {
      feature,
      mode: "local",
      provider: "ollama",
      model: feat.local.model || config.ollamaModel,
      baseUrl: feat.local.baseUrl || config.ollamaBaseUrl,
      apiKey: "ollama",
    };
  }
  return {
    feature,
    mode: "cloud",
    provider: "openai",
    model: feat.cloud.model || config.openaiModel,
    baseUrl: feat.cloud.baseUrl,
    apiKey: feat.cloud.apiKey || config.openaiApiKey,
  };
}

/** 根据 config 获取当前模型的简短显示名称（默认 audit 功能） */
export function getModelShortLabel(config: AppConfig, feature: FeatureKey = "audit"): string {
  const resolved = resolveFeatureLlm(config, feature);
  if (resolved.provider === "silent") return "静音占位";
  if (resolved.provider === "ollama") {
    const name = resolved.model || "未选择";
    return name.length > 14 ? name.slice(0, 12) + "…" : name;
  }
  const allCloudSuggestions = Object.values(CLOUD_MODEL_SUGGESTIONS).flat();
  const found = allCloudSuggestions.find((m) => m.value === resolved.model);
  return found?.shortLabel ?? resolved.model;
}

/** 更新某功能的配置片段 */
export function patchFeatureLlm(
  config: AppConfig,
  feature: FeatureKey,
  patch: Partial<FeatureModelConfig> | Partial<TtsFeatureConfig>,
): AppConfig {
  if (feature === "tts") {
    return {
      ...config,
      featureLlm: {
        ...config.featureLlm,
        tts: { ...config.featureLlm.tts, ...(patch as Partial<TtsFeatureConfig>) },
      },
    };
  }
  return {
    ...config,
    featureLlm: {
      ...config.featureLlm,
      [feature]: {
        ...config.featureLlm[feature],
        ...(patch as Partial<FeatureModelConfig>),
      },
    },
  };
}

export function patchRolePrimaryModel(
  config: AppConfig,
  feature: FeatureKey,
  model: string,
): AppConfig {
  const role = FEATURE_PRIMARY_ROLE[feature];
  if (!role) return config;
  const providerProfileId = model.startsWith("glm-")
    ? "zhipu"
    : model.startsWith("doubao-")
      ? "volcengine-ark"
      : "routerhub";
  const route = config.llmRoleRoutes[role];
  if (!route?.candidates.length) return config;
  return {
    ...config,
    llmRoleRoutes: {
      ...config.llmRoleRoutes,
      [role]: {
        ...route,
        candidates: route.candidates.map((candidate, index) =>
          index === 0 ? { ...candidate, model, providerProfileId } : candidate),
      },
    },
  };
}

/** 构建 API 请求用的 provider/model/base_url 参数 */
export function buildFeatureRequestOptions(config: AppConfig, feature: FeatureKey) {
  const resolved = resolveFeatureLlm(config, feature);
  if (resolved.provider === "ollama") {
    return {
      provider: "ollama" as const,
      ollama_model: resolved.model,
      ollama_base_url: resolved.baseUrl,
      openai_api_key: undefined,
      openai_model: undefined,
      base_url: resolved.baseUrl,
    };
  }
  if (resolved.provider === "silent") {
    return {
      provider: "silent" as const,
      openai_api_key: undefined,
      openai_model: undefined,
    };
  }
  return {
    provider: "openai" as const,
    openai_api_key: resolved.apiKey || undefined,
    openai_model: resolved.model,
    base_url: resolved.baseUrl,
  };
}
