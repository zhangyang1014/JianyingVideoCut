/**
 * GoldenClip API 配置页 — 按功能 Tab 分别配置云端/本地 LLM
 */

import { useState, useEffect, useCallback } from "react";
import type { ComponentType, ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import {
  getGlossary,
  listAvailableLlmModels,
  testLlmModel,
  updateGlossary,
  type AvailableLlmModel,
  type LlmModelTestResult,
} from "@/lib/api";
import PromptConfigPanel from "@/components/config/PromptConfigPanel";
import ModelRoutingPanel from "@/components/config/ModelRoutingPanel";
import {
  Save, Key, Bot, Loader2, CheckCircle2, RotateCcw, ShieldCheck,
  Terminal, HardDrive, Cpu, RefreshCw, Wifi, WifiOff, Cloud, Zap, Mic, Info, PlayCircle, BookOpen,
  ListTree, Search,
} from "lucide-react";
import {
  useAppConfig,
  type AppConfig,
  type FeatureKey,
  type FeatureModelConfig,
  type LlmRoleKey,
  type TtsFeatureConfig,
  FEATURE_TABS,
  CLOUD_MODEL_SUGGESTIONS,
  LOCAL_MODEL_SUGGESTIONS,
  MODEL_GROUP_LABELS,
  OPENROUTER_BASE_URL,
  ROUTERHUB_BASE_URL,
  TTS_VOICE_SUGGESTIONS,
  patchFeatureLlm,
  getModelShortLabel,
} from "@/hooks/useAppConfig";

const STATUS_ITEMS = [
  { label: "FastAPI 后端", port: 8000, icon: Terminal },
  { label: "React 前端", port: 5174, icon: Cpu },
  { label: "FFmpeg", port: null, icon: HardDrive },
];

const FEATURE_FALLBACK = [
  "未配置 Key 时，任务页会提示确认是否改用规则引擎，不会静默降级。",
  "本地模式优先使用已检测到的 Ollama 模型；也可手动输入模型名覆盖。",
  "Key 解析优先级：当前请求 > 功能配置 > 全局 Key > 环境变量。",
];

const ASR_CHUNK_PRESETS = [2, 5, 8];
const ASR_PARALLEL_PRESETS = [2, 4, 6, 8];
const AUDIT_CHUNK_PRESETS = [80, 120, 150];
const AUDIT_PARALLEL_PRESETS = [4, 8, 12, 16];
const AUDIT_TOKEN_BUDGET_PRESETS = [16000, 24000, 36000];
const HIGHLIGHT_PARALLEL_PRESETS = [1, 3, 4, 6, 8];

const CLOUD_PROVIDER_PRESETS = [
  {
    id: "routerhub",
    label: "RouterHub",
    baseUrl: ROUTERHUB_BASE_URL,
    apiKeyEnv: "ROUTERHUB_API_KEY",
  },
  {
    id: "openrouter",
    label: "OpenRouter",
    baseUrl: OPENROUTER_BASE_URL,
    apiKeyEnv: "OPENROUTER_API_KEY",
  },
] as const;

const FEATURE_ROUTE_ROLES: Partial<Record<FeatureKey, LlmRoleKey[]>> = {
  audit: ["audit.semantic", "audit.coherence"],
  translate: ["translate.subtitle"],
  highlight_reel: ["highlight.strategy", "highlight.validation"],
  golden_quote: ["golden_quote.extract"],
  learning: ["learning.analyze", "learning.rewrite", "prompt_feedback.optimize"],
};

type ApiConfigTabKey = FeatureKey | "routing" | "asr";

const ROUTING_TAB = {
  key: "routing" as const,
  label: "角色路由",
  desc: "供应商凭据与主模型、降级、旗舰升级策略",
};

const ASR_TAB = {
  key: "asr" as const,
  label: "ASR 性能",
  desc: "本地 FunASR 长音频分块与并行识别参数",
};

const API_CONFIG_TABS: { key: ApiConfigTabKey; label: string; desc: string }[] = [
  ROUTING_TAB,
  ...FEATURE_TABS,
  ASR_TAB,
];

function isFeatureTabKey(key: ApiConfigTabKey): key is FeatureKey {
  return key !== "asr" && key !== "routing";
}

function getApiConfigTab(key: ApiConfigTabKey) {
  return API_CONFIG_TABS.find((tab) => tab.key === key) ?? FEATURE_TABS[0];
}

function getApiConfigTabShortLabel(config: AppConfig, key: ApiConfigTabKey): string {
  if (key === "routing") {
    const routes = config.llmRoleRoutes ?? {};
    const enabled = Object.values(routes).flatMap((route) => route.candidates).filter((candidate) => candidate.enabled).length;
    return `${Object.keys(routes).length} 个角色 · ${enabled} 个候选`;
  }
  if (key === "asr") {
    const chunkMinutes = clampNumber(config.asr.funasrChunkMinutes, 1, 15, 5);
    const parallelChunks = Math.round(clampNumber(config.asr.funasrParallelChunks, 1, 8, 4));
    return `FunASR ${chunkMinutes}分钟 · ${parallelChunks}路`;
  }
  return getModelShortLabel(config, key);
}

function clampNumber(value: number, min: number, max: number, fallback: number): number {
  if (!Number.isFinite(value)) return fallback;
  return Math.min(max, Math.max(min, value));
}

function SectionHeader({ title, subtitle }: { title: string; subtitle?: string }) {
  return (
    <div className="space-y-0.5">
      <h2 className="text-sm font-semibold text-foreground">{title}</h2>
      {subtitle && <p className="text-[11px] text-muted-foreground leading-relaxed">{subtitle}</p>}
    </div>
  );
}

interface ConfigSectionNavItem {
  id: string;
  label: string;
  description?: string;
}

function ConfigSectionNav({ items }: { items: ConfigSectionNavItem[] }) {
  const [activeId, setActiveId] = useState(items[0]?.id ?? "");

  useEffect(() => {
    if (typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((entry) => entry.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
        if (visible[0]) setActiveId(visible[0].target.id);
      },
      { rootMargin: "-10% 0px -75% 0px", threshold: 0 },
    );
    items.forEach((item) => {
      const target = document.getElementById(item.id);
      if (target) observer.observe(target);
    });
    return () => observer.disconnect();
  }, [items]);

  const navigateTo = (item: ConfigSectionNavItem) => {
    const target = document.getElementById(item.id);
    if (!target) return;
    setActiveId(item.id);
    target.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  return (
    <aside
      className="sticky top-6 hidden w-40 shrink-0 self-start lg:block"
      aria-label="当前 Tab 配置导航"
    >
      <div className="rounded-xl border border-border/40 bg-secondary/20 p-2.5 shadow-sm backdrop-blur">
        <div className="mb-2 flex items-center gap-1.5 px-2 py-1 text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
          <ListTree className="h-3.5 w-3.5 text-primary" />
          快速定位
        </div>
        <nav className="space-y-1">
          {items.map((item, index) => (
            <button
              key={item.id}
              type="button"
              onClick={() => navigateTo(item)}
              className={cn(
                "group relative w-full rounded-lg px-2.5 py-2 text-left transition-colors",
                activeId === item.id
                  ? "bg-primary/10 text-primary"
                  : "text-muted-foreground hover:bg-secondary/70 hover:text-foreground",
              )}
            >
              <span
                className={cn(
                  "absolute bottom-2 left-0 top-2 w-0.5 rounded-full",
                  activeId === item.id ? "bg-primary" : "bg-transparent",
                )}
              />
              <span className="block text-[11px] font-medium">
                {String(index + 1).padStart(2, "0")} · {item.label}
              </span>
              {item.description && (
                <span className="mt-0.5 block truncate text-[9px] opacity-55">{item.description}</span>
              )}
            </button>
          ))}
        </nav>
      </div>
    </aside>
  );
}

function InfoPanel({
  title,
  children,
  variant = "default",
}: {
  title: string;
  children: ReactNode;
  variant?: "default" | "success" | "warning";
}) {
  const iconClass =
    variant === "success" ? "text-green-500" : variant === "warning" ? "text-amber-400" : "text-primary";
  return (
    <div className="rounded-xl border border-border/40 bg-secondary/30 px-5 py-4 flex gap-3">
      <ShieldCheck className={cn("w-4 h-4 shrink-0 mt-0.5", iconClass)} />
      <div className="space-y-1 min-w-0">
        <p className="text-xs font-medium text-foreground">{title}</p>
        <div className="text-[11px] text-muted-foreground leading-relaxed">{children}</div>
      </div>
    </div>
  );
}

function ModeSelector({
  mode,
  onChange,
  isTts = false,
}: {
  mode: "cloud" | "local" | "silent";
  onChange: (mode: "cloud" | "local" | "silent") => void;
  isTts?: boolean;
}) {
  const options = isTts
    ? [
        { value: "cloud" as const, icon: Cloud, label: "云端 TTS", desc: "OpenRouter Speech" },
        { value: "silent" as const, icon: Mic, label: "静音占位", desc: "对齐静音，不生成语音" },
      ]
    : [
        { value: "cloud" as const, icon: Cloud, label: "优先云端", desc: "RouterHub 首选路由" },
        { value: "local" as const, icon: Zap, label: "优先本地", desc: "Ollama 离线推理" },
      ];

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
      {options.map((opt) => {
        const Icon = opt.icon;
        const selected = mode === opt.value;
        return (
          <button
            key={opt.value}
            type="button"
            onClick={() => onChange(opt.value)}
            className={cn(
              "text-left rounded-xl border px-4 py-3 transition-all space-y-1",
              selected
                ? "border-primary bg-primary/10 ring-1 ring-primary/30"
                : "border-border bg-secondary/50 text-muted-foreground hover:border-border/80 hover:text-foreground",
            )}
          >
            <div className="flex items-center gap-2">
              <Icon className={cn("w-4 h-4", selected ? "text-primary" : "")} />
              <p className={cn("text-xs font-semibold", selected ? "text-primary" : "text-foreground")}>
                {opt.label}
              </p>
            </div>
            <p className="text-[10px] opacity-70 pl-6">{opt.desc}</p>
          </button>
        );
      })}
    </div>
  );
}

function AuditRuntimePanel({
  draft,
  patchDraft,
}: {
  draft: AppConfig;
  patchDraft: (patch: Partial<AppConfig>) => void;
}) {
  const chunkSegments = Math.round(clampNumber(draft.auditRuntime.cloudChunkSegments, 40, 150, 120));
  const maxParallelChunks = Math.round(clampNumber(draft.auditRuntime.cloudMaxParallelChunks, 1, 16, 8));
  const initialParallelChunks = Math.round(clampNumber(
    draft.auditRuntime.cloudInitialParallelChunks,
    1,
    maxParallelChunks,
    Math.min(8, maxParallelChunks),
  ));
  const tokenBudget = Math.round(clampNumber(draft.auditRuntime.cloudChunkTokenBudget, 8000, 120000, 24000));

  const updateAuditRuntime = (patch: Partial<AppConfig["auditRuntime"]>) => {
    patchDraft({
      auditRuntime: {
        ...draft.auditRuntime,
        ...patch,
      },
    });
  };

  return (
    <section className="space-y-4 rounded-xl border border-border/50 bg-secondary/20 p-4">
      <SectionHeader
        title="云端审计性能"
        subtitle="长视频会按自然句群分块并行审计；遇到 429 或服务拥塞时会自动降速。"
      />

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div className="space-y-2">
          <label className="text-[11px] text-muted-foreground uppercase tracking-wider">
            每批核心片段数
          </label>
          <Input
            type="number"
            min={40}
            max={150}
            step={10}
            value={chunkSegments}
            onChange={(e) =>
              updateAuditRuntime({
                cloudChunkSegments: Math.round(clampNumber(e.currentTarget.valueAsNumber, 40, 150, 120)),
              })
            }
            className="h-9 text-sm bg-secondary border-border"
          />
          <div className="flex flex-wrap gap-2">
            {AUDIT_CHUNK_PRESETS.map((count) => (
              <button
                key={count}
                type="button"
                onClick={() => updateAuditRuntime({ cloudChunkSegments: count })}
                className={cn(
                  "px-2.5 py-1 rounded-md border text-xs transition-colors",
                  chunkSegments === count
                    ? "border-primary bg-primary/10 text-primary"
                    : "border-border text-muted-foreground hover:text-foreground",
                )}
              >
                {count} 段
              </button>
            ))}
          </div>
        </div>

        <div className="space-y-2">
          <label className="text-[11px] text-muted-foreground uppercase tracking-wider">
            最大云端并发
          </label>
          <Input
            type="number"
            min={1}
            max={16}
            step={1}
            value={maxParallelChunks}
            onChange={(e) =>
              updateAuditRuntime({
                cloudMaxParallelChunks: Math.round(clampNumber(e.currentTarget.valueAsNumber, 1, 16, 8)),
              })
            }
            className="h-9 text-sm bg-secondary border-border"
          />
          <div className="flex flex-wrap gap-2">
            {AUDIT_PARALLEL_PRESETS.map((count) => (
              <button
                key={count}
                type="button"
                onClick={() => updateAuditRuntime({ cloudMaxParallelChunks: count })}
                className={cn(
                  "px-2.5 py-1 rounded-md border text-xs transition-colors",
                  maxParallelChunks === count
                    ? "border-primary bg-primary/10 text-primary"
                    : "border-border text-muted-foreground hover:text-foreground",
                )}
              >
                {count} 路
              </button>
            ))}
          </div>
        </div>

        <div className="space-y-2">
          <label className="text-[11px] text-muted-foreground uppercase tracking-wider">
            初始云端并发
          </label>
          <Input
            type="number"
            min={1}
            max={maxParallelChunks}
            step={1}
            value={initialParallelChunks}
            onChange={(e) =>
              updateAuditRuntime({
                cloudInitialParallelChunks: Math.round(clampNumber(e.currentTarget.valueAsNumber, 1, maxParallelChunks, Math.min(8, maxParallelChunks))),
              })
            }
            className="h-9 text-sm bg-secondary border-border"
          />
          <p className="text-[10px] text-muted-foreground/70">
            健康供应商可直接从高并发启动；遇到限流会自动降速。
          </p>
        </div>

        <div className="space-y-2">
          <label className="text-[11px] text-muted-foreground uppercase tracking-wider">
            每批 token 预算
          </label>
          <Input
            type="number"
            min={8000}
            max={120000}
            step={1000}
            value={tokenBudget}
            onChange={(e) =>
              updateAuditRuntime({
                cloudChunkTokenBudget: Math.round(clampNumber(e.currentTarget.valueAsNumber, 8000, 120000, 24000)),
              })
            }
            className="h-9 text-sm bg-secondary border-border"
          />
          <div className="flex flex-wrap gap-2">
            {AUDIT_TOKEN_BUDGET_PRESETS.map((budget) => (
              <button
                key={budget}
                type="button"
                onClick={() => updateAuditRuntime({ cloudChunkTokenBudget: budget })}
                className={cn(
                  "px-2.5 py-1 rounded-md border text-xs transition-colors",
                  tokenBudget === budget
                    ? "border-primary bg-primary/10 text-primary"
                    : "border-border text-muted-foreground hover:text-foreground",
                )}
              >
                {Math.round(budget / 1000)}k
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <label className="flex items-center gap-2 rounded-lg border border-border/50 bg-background/40 px-3 py-2 text-xs text-muted-foreground">
          <input
            type="checkbox"
            checked={draft.auditRuntime.enableBatchResume}
            onChange={(e) => updateAuditRuntime({ enableBatchResume: e.currentTarget.checked })}
          />
          批次断点续跑
        </label>
        <label className="flex items-center gap-2 rounded-lg border border-border/50 bg-background/40 px-3 py-2 text-xs text-muted-foreground">
          <input
            type="checkbox"
            checked={draft.auditRuntime.enableIncrementalCache}
            onChange={(e) => updateAuditRuntime({ enableIncrementalCache: e.currentTarget.checked })}
          />
          增量缓存
        </label>
        <label className="space-y-1 rounded-lg border border-border/50 bg-background/40 px-3 py-2 text-xs text-muted-foreground">
          <span className="block">连贯性复核</span>
          <select
            value={draft.auditRuntime.coherenceStrategy}
            onChange={(e) => updateAuditRuntime({ coherenceStrategy: e.currentTarget.value as AppConfig["auditRuntime"]["coherenceStrategy"] })}
            className="w-full rounded-md border border-border bg-secondary px-2 py-1 text-xs text-foreground"
          >
            <option value="auto">自动</option>
            <option value="always">始终模型复核</option>
            <option value="local_first">本地优先</option>
          </select>
        </label>
      </div>

      <InfoPanel title="自适应并发" variant="success">
        默认从 {initialParallelChunks} 路启动，稳定后最多 {maxParallelChunks} 路；批次失败只重试该批，全部成功后才写入审计结果。
      </InfoPanel>
    </section>
  );
}

function HighlightRuntimePanel({
  draft,
  patchDraft,
}: {
  draft: AppConfig;
  patchDraft: (patch: Partial<AppConfig>) => void;
}) {
  const maxParallelCalls = Math.round(clampNumber(
    draft.highlightRuntime.cloudMaxParallelCalls,
    1,
    8,
    3,
  ));

  const updateMaxParallelCalls = (value: number) => {
    patchDraft({
      highlightRuntime: {
        ...draft.highlightRuntime,
        cloudMaxParallelCalls: Math.round(clampNumber(value, 1, 8, 3)),
      },
    });
  };

  return (
    <section className="space-y-4 rounded-xl border border-border/50 bg-secondary/20 p-4">
      <SectionHeader
        title="产品集锦云端并发"
        subtitle="控制三策略生成和逐条质检的最大云端并发数；产品识别仍使用审计结果，保存后对下一次集锦任务生效。"
      />

      <div className="space-y-2">
        <label className="text-[11px] text-muted-foreground uppercase tracking-wider">
          最大云端并发调用
        </label>
        <Input
          type="number"
          min={1}
          max={8}
          step={1}
          value={maxParallelCalls}
          onChange={(event) => updateMaxParallelCalls(event.currentTarget.valueAsNumber)}
          className="h-9 text-sm bg-secondary border-border"
        />
        <div className="flex flex-wrap gap-2">
          {HIGHLIGHT_PARALLEL_PRESETS.map((count) => (
            <button
              key={count}
              type="button"
              onClick={() => updateMaxParallelCalls(count)}
              className={cn(
                "px-2.5 py-1 rounded-md border text-xs transition-colors",
                maxParallelCalls === count
                  ? "border-primary bg-primary/10 text-primary"
                  : "border-border text-muted-foreground hover:text-foreground",
              )}
            >
              {count} 路
            </button>
          ))}
        </div>
      </div>

      <InfoPanel title="并发范围" variant="success">
        当前最多同时发起 {maxParallelCalls} 路云端调用，可设置 1–8 路；供应商限流时请降低并发。
      </InfoPanel>
    </section>
  );
}

function FeatureTabPanel({
  feature,
  draft,
  patchDraft,
  ollamaModels,
  ollamaLoading,
  ollamaOnline,
  onRefreshOllama,
  onPromptDirtyChange,
}: {
  feature: FeatureKey;
  draft: AppConfig;
  patchDraft: (patch: Partial<AppConfig>) => void;
  ollamaModels: string[];
  ollamaLoading: boolean;
  ollamaOnline: boolean | null;
  onRefreshOllama: (baseUrl: string) => void;
  onPromptDirtyChange: (dirty: boolean) => void;
}) {
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<LlmModelTestResult | null>(null);
  const [availableModels, setAvailableModels] = useState<AvailableLlmModel[]>([]);
  const [catalogLoading, setCatalogLoading] = useState(false);
  const [catalogError, setCatalogError] = useState("");
  const [catalogQuery, setCatalogQuery] = useState("");
  const [catalogExpanded, setCatalogExpanded] = useState(false);
  const tab = FEATURE_TABS.find((t) => t.key === feature)!;
  const isTts = feature === "tts";
  const ttsConfig = draft.featureLlm.tts;
  const featConfig = isTts ? null : draft.featureLlm[feature as Exclude<FeatureKey, "tts">];
  const mode = isTts ? ttsConfig.mode : featConfig!.mode;
  const cloudSuggestions = CLOUD_MODEL_SUGGESTIONS[feature];
  const localSuggestions = LOCAL_MODEL_SUGGESTIONS[feature];

  const updateFeature = (patch: Partial<FeatureModelConfig> | Partial<TtsFeatureConfig>) => {
    patchDraft(patchFeatureLlm(draft, feature, patch));
  };

  const cloud = isTts ? ttsConfig.cloud : featConfig!.cloud;
  const cloudPricing = cloud.pricing ?? {};
  const local = isTts ? null : featConfig!.local;
  const availableProviderPresets = isTts
    ? CLOUD_PROVIDER_PRESETS.filter((provider) => provider.id === "openrouter")
    : CLOUD_PROVIDER_PRESETS;
  const selectedProviderPreset = availableProviderPresets.find(
    (provider) => {
      const profile = draft.providerProfiles.find((item) => item.id === provider.id);
      return (profile?.baseUrl || provider.baseUrl).replace(/\/$/, "")
        === cloud.baseUrl.replace(/\/$/, "");
    },
  ) ?? availableProviderPresets[0];
  const selectedProviderProfile = draft.providerProfiles.find(
    (profile) => profile.id === selectedProviderPreset.id,
  );
  const displayedApiKey = selectedProviderProfile?.apiKey || cloud.apiKey;

  const showCloud = mode === "cloud";
  const showLocal = !isTts && mode === "local";
  const voiceSuggestions = isTts ? TTS_VOICE_SUGGESTIONS[cloud.model] ?? [] : [];
  const curatedIds = new Set(cloudSuggestions.map((model) => model.value));
  const normalizedCatalogQuery = catalogQuery.trim().toLowerCase();
  const matchingCatalogModels = availableModels.filter((model) => {
    if (curatedIds.has(model.id)) return false;
    if (!normalizedCatalogQuery) return true;
    return `${model.name} ${model.id} ${model.description}`.toLowerCase().includes(normalizedCatalogQuery);
  });
  const visibleCatalogModels = catalogExpanded || normalizedCatalogQuery
    ? matchingCatalogModels
    : matchingCatalogModels.slice(0, 24);

  useEffect(() => {
    setCatalogQuery("");
    setCatalogExpanded(false);
  }, [feature]);

  useEffect(() => {
    if (!showCloud || !cloud.baseUrl) {
      setAvailableModels([]);
      setCatalogError("");
      return;
    }
    let cancelled = false;
    const timer = window.setTimeout(() => {
      setCatalogLoading(true);
      setCatalogError("");
      void listAvailableLlmModels(
        feature,
        cloud.baseUrl,
        false,
        selectedProviderProfile?.id,
      )
        .then((models) => {
          if (!cancelled) setAvailableModels(models);
        })
        .catch((error) => {
          if (!cancelled) {
            setAvailableModels([]);
            setCatalogError(error instanceof Error ? error.message : "读取可用模型失败");
          }
        })
        .finally(() => {
          if (!cancelled) setCatalogLoading(false);
        });
    }, 350);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [cloud.baseUrl, feature, selectedProviderProfile?.id, showCloud]);

  const refreshModelCatalog = async () => {
    setCatalogLoading(true);
    setCatalogError("");
    try {
      setAvailableModels(await listAvailableLlmModels(
        feature,
        cloud.baseUrl,
        true,
        selectedProviderProfile?.id,
      ));
    } catch (error) {
      setCatalogError(error instanceof Error ? error.message : "读取可用模型失败");
    } finally {
      setCatalogLoading(false);
    }
  };

  const selectCloudModel = (model: string) => {
    const catalogModel = availableModels.find((item) => item.id === model);
    const nextCloud = {
      ...cloud,
      model,
      pricing: catalogModel ? {
        ...cloudPricing,
        inputPerMillionTokens: catalogModel.pricing.prompt == null
          ? cloudPricing.inputPerMillionTokens
          : Number(catalogModel.pricing.prompt) * 1_000_000,
        outputPerMillionTokens: catalogModel.pricing.completion == null
          ? cloudPricing.outputPerMillionTokens
          : Number(catalogModel.pricing.completion) * 1_000_000,
      } : cloudPricing,
    };
    if (isTts) {
      const voices = TTS_VOICE_SUGGESTIONS[model] ?? [];
      const nextVoice = voices.length > 0 && !voices.includes(ttsConfig.voice)
        ? voices[0]
        : ttsConfig.voice;
      updateFeature({ cloud: nextCloud, voice: nextVoice });
    } else {
      updateFeature({ cloud: nextCloud });
    }
  };

  const selectCloudProvider = (providerId: string) => {
    const preset = availableProviderPresets.find((provider) => provider.id === providerId);
    if (!preset) return;

    const profile = draft.providerProfiles.find((item) => item.id === preset.id);
    const nextCloud = {
      ...cloud,
      providerLabel: profile?.label || preset.label,
      baseUrl: profile?.baseUrl || preset.baseUrl,
      apiKey: profile?.apiKey || "",
    };
    const nextConfig = patchFeatureLlm(draft, feature, { cloud: nextCloud });
    const nextRoutes = { ...draft.llmRoleRoutes };

    (FEATURE_ROUTE_ROLES[feature] ?? []).forEach((role) => {
      const route = nextRoutes[role];
      if (!route?.candidates.length) return;
      nextRoutes[role] = {
        ...route,
        candidates: route.candidates.map((candidate, index) =>
          index === 0
            ? { ...candidate, providerProfileId: preset.id }
            : candidate),
      };
    });

    patchDraft({
      featureLlm: nextConfig.featureLlm,
      llmRoleRoutes: nextRoutes,
    });
  };

  const updateCloudApiKey = (apiKey: string) => {
    const nextConfig = patchFeatureLlm(draft, feature, {
      cloud: { ...cloud, apiKey },
    });
    patchDraft({
      featureLlm: nextConfig.featureLlm,
      providerProfiles: draft.providerProfiles.map((profile) =>
        profile.id === selectedProviderPreset.id
          ? { ...profile, apiKey }
          : profile),
    });
  };

  const handleTestModel = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const result = await testLlmModel({
        feature,
        base_url: cloud.baseUrl,
        api_key: displayedApiKey || undefined,
        provider_profile_id: selectedProviderProfile?.id,
        model: cloud.model,
        voice: isTts ? ttsConfig.voice : undefined,
        kind: isTts ? "tts" : "chat",
      });
      setTestResult(result);
      if (result.success) {
        toast.success("模型试跑成功");
      } else {
        toast.error("模型状态可查，但最小试跑失败");
      }
    } catch (e: any) {
      toast.error(e.message || "模型试跑失败");
    } finally {
      setTesting(false);
    }
  };

  return (
    <div className="space-y-6">
      <InfoPanel title={tab.label} variant="default">
        {tab.desc}
        {feature === "audit" && (
          <p className="mt-1">失败时可选择规则引擎兜底，不会静默降级。</p>
        )}
        {feature === "translate" && (
          <p className="mt-1">优先翻译自然度与术语一致，支持任意 OpenAI-compatible 端点。</p>
        )}
        {feature === "highlight_reel" && (
          <p className="mt-1">用于直播卖货集锦计划的模型质检；产品识别与片段判定使用语义审计配置。</p>
        )}
        {feature === "tts" && (
          <p className="mt-1">TTS 不是 Ollama LLM 能力；本地路线请选「静音占位」。</p>
        )}
      </InfoPanel>

      <section id="config-connection" className="scroll-mt-4 space-y-3">
        <SectionHeader title="生效策略" subtitle="每个功能独立选择云端或本地（TTS 可选静音占位）。" />
        <ModeSelector
          isTts={isTts}
          mode={mode}
          onChange={(m) => {
            if (isTts) updateFeature({ mode: m as "cloud" | "silent" });
            else updateFeature({ mode: m === "local" ? "local" : "cloud" });
          }}
        />
      </section>

      {showCloud && (
        <section id="config-model" className="scroll-mt-4 space-y-4 rounded-xl border border-border/50 bg-secondary/20 p-4">
          <SectionHeader title="云端配置" subtitle="默认通过 RouterHub 路由，也支持改成其它 OpenAI-compatible 服务。" />

          <div className="space-y-2">
            <label className="text-[11px] text-muted-foreground uppercase tracking-wider">
              API 服务商
            </label>
            <select
              aria-label="API 服务商"
              value={selectedProviderPreset.id}
              onChange={(event) => selectCloudProvider(event.currentTarget.value)}
              className="h-9 w-full rounded-md border border-border bg-secondary px-3 text-sm text-foreground"
            >
              {availableProviderPresets.map((provider) => {
                const profile = draft.providerProfiles.find((item) => item.id === provider.id);
                return (
                  <option key={provider.id} value={provider.id}>
                    {profile?.label || provider.label} · {profile?.baseUrl || provider.baseUrl}
                  </option>
                );
              })}
            </select>
            <p className="text-[10px] text-muted-foreground/60">
              切换后会同步更新当前功能的主路由供应商和服务地址。
            </p>
          </div>

          <div className="space-y-2">
            <label className="text-[11px] text-muted-foreground uppercase tracking-wider flex items-center gap-1.5">
              <Key className="w-3.5 h-3.5" /> API Key
            </label>
            <Input
              type="password"
              value={displayedApiKey}
              onChange={(e) => updateCloudApiKey(e.target.value)}
              placeholder={selectedProviderPreset.apiKeyEnv}
              className="h-9 text-sm bg-secondary border-border"
            />
          </div>

          <div className="space-y-2">
            <label className="text-[11px] text-muted-foreground uppercase tracking-wider">Base URL</label>
            <Input
              value={cloud.baseUrl}
              readOnly
              aria-readonly="true"
              className="h-9 text-sm bg-secondary/60 border-border font-mono text-muted-foreground"
            />
          </div>

          <div className="space-y-2">
            <label className="text-[11px] text-muted-foreground uppercase tracking-wider flex items-center gap-1.5">
              <Bot className="w-3.5 h-3.5" /> 模型
            </label>
            <div className="space-y-1.5">
              <p className="text-[10px] uppercase tracking-wider text-muted-foreground/60">推荐模型</p>
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
                {cloudSuggestions.map((model) => (
                  <button
                    key={model.value}
                    type="button"
                    onClick={() => selectCloudModel(model.value)}
                    className={cn(
                      "min-w-0 rounded-lg border px-2.5 py-2 text-left transition-all",
                      cloud.model === model.value
                        ? "border-primary bg-primary/10"
                        : "border-border bg-secondary/50 hover:border-border/80",
                    )}
                  >
                    <div className="flex min-w-0 items-center justify-between gap-1.5">
                      <p className={cn("truncate text-[11px] font-semibold", cloud.model === model.value && "text-primary")}>
                        {model.label}
                      </p>
                      <span className="shrink-0 rounded bg-background/50 px-1 py-0.5 text-[8px] text-muted-foreground">
                        {MODEL_GROUP_LABELS[model.group ?? "balanced"]}
                      </span>
                    </div>
                    <p className="mt-1 truncate text-[9px] text-muted-foreground">{model.desc}</p>
                  </button>
                ))}
              </div>
            </div>

            <div className="space-y-2 border-t border-border/40 pt-3">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <p className="text-[10px] uppercase tracking-wider text-muted-foreground/60">服务端可用模型</p>
                  <p className="mt-0.5 text-[9px] text-muted-foreground/60">
                    {catalogLoading
                      ? "正在读取模型目录…"
                      : `已发现 ${availableModels.length} 个，支持搜索全部目录`}
                  </p>
                </div>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="h-7 gap-1 px-2 text-[10px]"
                  onClick={refreshModelCatalog}
                  disabled={catalogLoading}
                >
                  <RefreshCw className={cn("h-3 w-3", catalogLoading && "animate-spin")} />
                  刷新
                </Button>
              </div>

              <div className="relative">
                <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground/60" />
                <Input
                  value={catalogQuery}
                  onChange={(event) => setCatalogQuery(event.currentTarget.value)}
                  placeholder="搜索模型名称或 slug…"
                  className="h-8 bg-secondary pl-8 text-xs"
                />
              </div>

              {catalogError ? (
                <p className="rounded-md border border-amber-500/20 bg-amber-500/5 px-2.5 py-2 text-[10px] text-amber-300">
                  {catalogError}；仍可使用推荐模型或手动输入模型名。
                </p>
              ) : visibleCatalogModels.length > 0 ? (
                <>
                  <div className="grid max-h-72 grid-cols-1 gap-1.5 overflow-y-auto pr-1 sm:grid-cols-3">
                    {visibleCatalogModels.map((model) => (
                      <button
                        key={model.id}
                        type="button"
                        title={`${model.name}\n${model.id}`}
                        onClick={() => selectCloudModel(model.id)}
                        className={cn(
                          "min-w-0 rounded-md border px-2.5 py-2 text-left transition-colors",
                          cloud.model === model.id
                            ? "border-primary bg-primary/10 text-primary"
                            : "border-border/70 bg-background/30 hover:border-border hover:bg-secondary/60",
                        )}
                      >
                        <p className="truncate text-[10px] font-medium">{model.name}</p>
                        <div className="mt-0.5 flex items-center justify-between gap-1 text-[8px] text-muted-foreground/60">
                          <span className="truncate font-mono">{model.id}</span>
                          {model.context_length ? (
                            <span className="shrink-0">{Math.round(model.context_length / 1000)}k</span>
                          ) : null}
                        </div>
                      </button>
                    ))}
                  </div>
                  {!normalizedCatalogQuery && matchingCatalogModels.length > 24 && (
                    <button
                      type="button"
                      onClick={() => setCatalogExpanded((value) => !value)}
                      className="w-full py-1 text-[10px] text-primary/80 hover:text-primary"
                    >
                      {catalogExpanded ? "收起模型目录" : `展开全部 ${matchingCatalogModels.length} 个模型`}
                    </button>
                  )}
                </>
              ) : !catalogLoading ? (
                <p className="py-2 text-center text-[10px] text-muted-foreground/60">
                  {catalogQuery ? "没有匹配的模型" : "当前服务未返回可用模型"}
                </p>
              ) : null}
            </div>
            <div className="flex gap-2">
              <Input
                value={cloud.model}
                onChange={(e) => updateFeature({ cloud: { ...cloud, model: e.target.value } })}
                placeholder="手动输入模型名"
                className="h-9 text-sm bg-secondary border-border font-mono flex-1"
              />
              <Button
                variant="outline"
                size="sm"
                className="h-9 px-3 shrink-0 gap-1.5 text-xs"
                onClick={handleTestModel}
                disabled={testing || !cloud.model || !cloud.baseUrl}
              >
                {testing ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <PlayCircle className="w-3.5 h-3.5" />}
                试跑
              </Button>
            </div>

            {testResult && (
              <div
                className={cn(
                  "rounded-lg border px-3 py-2 text-[11px] space-y-1",
                  testResult.success
                    ? "border-green-500/30 bg-green-500/10 text-green-300"
                    : "border-destructive/30 bg-destructive/10 text-destructive"
                )}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium">{testResult.success ? "试跑成功" : "试跑失败"}</span>
                  <span className="font-mono text-[10px] opacity-80">{testResult.model}</span>
                </div>
                <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-muted-foreground">
                  <span>Key: {testResult.checks.auth?.ok ? "可用" : `失败(${testResult.checks.auth?.status ?? "-"})`}</span>
                  <span>模型: {testResult.checks.model?.ok ? "存在" : `失败(${testResult.checks.model?.status ?? "-"})`}</span>
                  <span>路由: {testResult.checks.endpoints?.ok ? `${testResult.checks.endpoints.count ?? 0} 个 endpoint` : `失败(${testResult.checks.endpoints?.status ?? "-"})`}</span>
                  <span>真实调用: {testResult.checks.trial?.ok ? "通过" : `失败(${testResult.checks.trial?.status ?? "-"})`}</span>
                </div>
                {testResult.checks.model?.pricing && (
                  <p className="text-muted-foreground/70">
                    价格: prompt {testResult.checks.model.pricing.prompt ?? "-"} · completion {testResult.checks.model.pricing.completion ?? "-"}
                  </p>
                )}
                {testResult.checks.trial?.usage && (
                  <p className="text-muted-foreground/70">
                    试跑 tokens: {String(testResult.checks.trial.usage.total_tokens ?? "-")}
                  </p>
                )}
                {testResult.checks.trial?.bytes !== undefined && (
                  <p className="text-muted-foreground/70">
                    试跑音频: {testResult.checks.trial.bytes} bytes
                  </p>
                )}
                {testResult.checks.trial?.error && (
                  <p className="font-mono text-[10px] break-all opacity-80">{testResult.checks.trial.error}</p>
                )}
              </div>
            )}
          </div>

          <div className="space-y-2">
            <label className="text-[11px] text-muted-foreground uppercase tracking-wider">
              费用估算单价（USD）
            </label>
            <div className={cn("grid gap-2", isTts ? "sm:grid-cols-2" : "sm:grid-cols-3")}>
              {!isTts && (
                <>
                  <Input
                    type="number"
                    min={0}
                    step="0.01"
                    value={cloudPricing.inputPerMillionTokens ?? ""}
                    onChange={(event) => updateFeature({
                      cloud: {
                        ...cloud,
                        pricing: {
                          ...cloudPricing,
                          inputPerMillionTokens: event.target.value === "" ? undefined : Number(event.target.value),
                        },
                      },
                    })}
                    placeholder="输入 / 百万 token"
                  />
                  <Input
                    type="number"
                    min={0}
                    step="0.01"
                    value={cloudPricing.outputPerMillionTokens ?? ""}
                    onChange={(event) => updateFeature({
                      cloud: {
                        ...cloud,
                        pricing: {
                          ...cloudPricing,
                          outputPerMillionTokens: event.target.value === "" ? undefined : Number(event.target.value),
                        },
                      },
                    })}
                    placeholder="输出 / 百万 token"
                  />
                </>
              )}
              <Input
                type="number"
                min={0}
                step="0.0001"
                value={cloudPricing.perRequest ?? ""}
                onChange={(event) => updateFeature({
                  cloud: {
                    ...cloud,
                    pricing: {
                      ...cloudPricing,
                      perRequest: event.target.value === "" ? undefined : Number(event.target.value),
                    },
                  },
                })}
                placeholder="每次请求"
              />
              {isTts && (
                <Input
                  type="number"
                  min={0}
                  step="0.01"
                  value={cloudPricing.perMillionCharacters ?? ""}
                  onChange={(event) => updateFeature({
                    cloud: {
                      ...cloud,
                      pricing: {
                        ...cloudPricing,
                        perMillionCharacters: event.target.value === "" ? undefined : Number(event.target.value),
                      },
                    },
                  })}
                  placeholder="每百万字符"
                />
              )}
            </div>
            <p className="text-[10px] text-muted-foreground/60">
              供应商未返回实际费用时使用；从模型目录选择会自动带入 token 单价。
            </p>
          </div>

          {isTts && (
            <div className="space-y-2">
              <label className="text-[11px] text-muted-foreground uppercase tracking-wider">Voice</label>
              {voiceSuggestions.length > 0 && (
                <div className="flex flex-wrap gap-2">
                  {voiceSuggestions.map((voice) => (
                    <button
                      key={voice}
                      type="button"
                      onClick={() => updateFeature({ voice })}
                      className={cn(
                        "px-2.5 py-1 rounded-md border text-xs font-mono transition-colors",
                        ttsConfig.voice === voice
                          ? "border-primary bg-primary/10 text-primary"
                          : "border-border text-muted-foreground hover:text-foreground",
                      )}
                    >
                      {voice}
                    </button>
                  ))}
                </div>
              )}
              <Input
                value={ttsConfig.voice}
                onChange={(e) => updateFeature({ voice: e.target.value })}
                placeholder={voiceSuggestions[0] ?? "voice id"}
                className="h-9 text-sm bg-secondary border-border font-mono"
              />
            </div>
          )}
        </section>
      )}

      {showLocal && local && (
        <section id="config-model" className="scroll-mt-4 space-y-4 rounded-xl border border-border/50 bg-secondary/20 p-4">
          <SectionHeader title="本地 Ollama 配置" subtitle="数据不出本机，无 API 费用。" />

          <div className="space-y-2">
            <label className="text-[11px] text-muted-foreground uppercase tracking-wider">Ollama 服务地址</label>
            <div className="flex gap-2">
              <Input
                value={local.baseUrl}
                onChange={(e) => updateFeature({ local: { ...local, baseUrl: e.target.value } })}
                placeholder="http://localhost:11434"
                className="h-9 text-sm bg-secondary border-border flex-1 font-mono"
              />
              <Button
                variant="outline"
                size="sm"
                className="h-9 px-3 shrink-0 gap-1.5 text-xs"
                onClick={() => onRefreshOllama(local.baseUrl)}
                disabled={ollamaLoading}
              >
                {ollamaLoading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
                刷新
              </Button>
            </div>
            <p className="text-[11px] text-muted-foreground/50 flex items-center gap-1">
              {ollamaOnline === true && (
                <>
                  <Wifi className="w-3 h-3 text-green-500" />
                  Ollama 在线，已发现 {ollamaModels.length} 个模型
                </>
              )}
              {ollamaOnline === false && (
                <>
                  <WifiOff className="w-3 h-3 text-destructive" />
                  无法连接 Ollama
                </>
              )}
              {ollamaOnline === null && "点击刷新检测 Ollama 状态"}
            </p>
          </div>

          {localSuggestions.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {localSuggestions.map((m) => (
                <button
                  key={m}
                  type="button"
                  onClick={() => updateFeature({ local: { ...local, model: m } })}
                  className={cn(
                    "px-2.5 py-1 rounded-md border text-xs font-mono transition-colors",
                    local.model === m
                      ? "border-primary bg-primary/10 text-primary"
                      : "border-border text-muted-foreground hover:text-foreground",
                  )}
                >
                  {m}
                </button>
              ))}
            </div>
          )}

          {ollamaModels.length > 0 && (
            <div className="grid grid-cols-2 gap-2 max-h-40 overflow-y-auto">
              {ollamaModels.map((m) => (
                <button
                  key={m}
                  type="button"
                  onClick={() => updateFeature({ local: { ...local, model: m } })}
                  className={cn(
                    "text-left rounded-lg border px-3 py-2 text-xs font-mono transition-all",
                    local.model === m ? "border-primary bg-primary/10 text-primary" : "border-border",
                  )}
                >
                  {m}
                </button>
              ))}
            </div>
          )}

          <Input
            value={local.model}
            onChange={(e) => updateFeature({ local: { ...local, model: e.target.value } })}
            placeholder="手动输入模型名"
            className="h-9 text-sm bg-secondary border-border font-mono"
          />
        </section>
      )}

      {isTts && mode === "silent" && (
        <div id="config-model" className="scroll-mt-4">
          <InfoPanel title="静音占位模式" variant="success">
            导出时使用对齐静音轨道，不调用 TTS API。适合纯字幕版本或后续接入本地 TTS。
          </InfoPanel>
        </div>
      )}

      {feature === "audit" && showCloud && (
        <div id="config-performance" className="scroll-mt-4">
          <AuditRuntimePanel draft={draft} patchDraft={patchDraft} />
        </div>
      )}

      {feature === "audit" && !showCloud && (
        <div id="config-performance" className="scroll-mt-4">
          <InfoPanel title="性能参数">
            本地 Ollama 审计按安全顺序执行，当前没有可单独调整的云端并发与 token 分块参数。
          </InfoPanel>
        </div>
      )}

      {feature === "highlight_reel" && showCloud && (
        <div id="config-performance" className="scroll-mt-4">
          <HighlightRuntimePanel draft={draft} patchDraft={patchDraft} />
        </div>
      )}

      {feature === "highlight_reel" && !showCloud && (
        <div id="config-performance" className="scroll-mt-4">
          <InfoPanel title="产品集锦并发">
            本地 Ollama 模式固定单路执行，避免多个模型请求同时争用本机算力，无需单独配置并发数。
          </InfoPanel>
        </div>
      )}

      {feature !== "audit" && feature !== "highlight_reel" && (
        <div id="config-performance" className="scroll-mt-4">
          <InfoPanel title="性能参数">
            当前功能暂无独立的并发或分块参数，运行时使用服务内经过验证的安全默认值。
          </InfoPanel>
        </div>
      )}

      <PromptConfigPanel
        feature={feature}
        sectionId="config-prompt"
        onDirtyChange={onPromptDirtyChange}
        emptyTitle={isTts ? "Speech API 无聊天 Prompt" : undefined}
        emptyDescription={isTts ? "TTS 直接把翻译后的字幕发送给语音合成接口，只配置模型与 Voice。" : undefined}
      />
    </div>
  );
}

function AsrRuntimePanel({
  draft,
  patchDraft,
}: {
  draft: AppConfig;
  patchDraft: (patch: Partial<AppConfig>) => void;
}) {
  const chunkMinutes = clampNumber(draft.asr.funasrChunkMinutes, 1, 15, 5);
  const parallelChunks = Math.round(clampNumber(draft.asr.funasrParallelChunks, 1, 8, 4));
  const estimatedChunks = Math.ceil(60 / chunkMinutes);

  const updateAsr = (patch: Partial<AppConfig["asr"]>) => {
    patchDraft({
      asr: {
        ...draft.asr,
        ...patch,
      },
    });
  };

  return (
    <section className="space-y-4 rounded-xl border border-border/50 bg-secondary/20 p-4">
      <SectionHeader
        title="本地 ASR 性能"
        subtitle="控制长音频 FunASR 的宏块长度和并行度；保存后对下一次 ASR 生效。"
      />

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div className="space-y-2">
          <label className="text-[11px] text-muted-foreground uppercase tracking-wider">
            分块时长（分钟）
          </label>
          <Input
            type="number"
            min={1}
            max={15}
            step={1}
            value={chunkMinutes}
            onChange={(e) =>
              updateAsr({
                funasrChunkMinutes: clampNumber(e.currentTarget.valueAsNumber, 1, 15, 5),
              })
            }
            className="h-9 text-sm bg-secondary border-border"
          />
          <div className="flex flex-wrap gap-2">
            {ASR_CHUNK_PRESETS.map((minutes) => (
              <button
                key={minutes}
                type="button"
                onClick={() => updateAsr({ funasrChunkMinutes: minutes })}
                className={cn(
                  "px-2.5 py-1 rounded-md border text-xs transition-colors",
                  chunkMinutes === minutes
                    ? "border-primary bg-primary/10 text-primary"
                    : "border-border text-muted-foreground hover:text-foreground",
                )}
              >
                {minutes} 分钟
              </button>
            ))}
          </div>
        </div>

        <div className="space-y-2">
          <label className="text-[11px] text-muted-foreground uppercase tracking-wider">
            并行块数
          </label>
          <Input
            type="number"
            min={1}
            max={8}
            step={1}
            value={parallelChunks}
            onChange={(e) =>
              updateAsr({
                funasrParallelChunks: Math.round(clampNumber(e.currentTarget.valueAsNumber, 1, 8, 4)),
              })
            }
            className="h-9 text-sm bg-secondary border-border"
          />
          <div className="flex flex-wrap gap-2">
            {ASR_PARALLEL_PRESETS.map((count) => (
              <button
                key={count}
                type="button"
                onClick={() => updateAsr({ funasrParallelChunks: count })}
                className={cn(
                  "px-2.5 py-1 rounded-md border text-xs transition-colors",
                  parallelChunks === count
                    ? "border-primary bg-primary/10 text-primary"
                    : "border-border text-muted-foreground hover:text-foreground",
                )}
              >
                {count} 路
              </button>
            ))}
          </div>
        </div>
      </div>

      <InfoPanel title="当前估算" variant="success">
        1 小时音频约切成 {estimatedChunks} 个宏块，最多 {parallelChunks} 个同时识别。分块越短，日志反馈越频繁；
        分块过短会增加切片与合并开销。
      </InfoPanel>
    </section>
  );
}

function GlossaryPanel({ onDirtyChange }: { onDirtyChange: (dirty: boolean) => void }) {
  const [content, setContent] = useState("");
  const [savedContent, setSavedContent] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const dirty = content !== savedContent;

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    void getGlossary()
      .then((value) => {
        if (cancelled) return;
        setContent(value);
        setSavedContent(value);
      })
      .catch(() => {
        if (!cancelled) toast.error("加载 ASR 术语词典失败");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
      onDirtyChange(false);
    };
  }, [onDirtyChange]);

  useEffect(() => {
    onDirtyChange(dirty);
  }, [dirty, onDirtyChange]);

  const saveGlossary = async () => {
    if (!dirty) return;
    setSaving(true);
    try {
      await updateGlossary(content);
      setSavedContent(content);
      toast.success("ASR 术语词典已保存");
    } catch {
      toast.error("保存 ASR 术语词典失败");
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="space-y-4 rounded-xl border border-border/50 bg-secondary/20 p-4">
      <div className="flex items-start justify-between gap-3">
        <SectionHeader title="ASR 术语词典" subtitle="每行一个标准术语，识别后用于专业词纠错；它不是 LLM Prompt。" />
        <Button size="sm" className="h-8 gap-1.5 text-xs" disabled={!dirty || saving} onClick={saveGlossary}>
          {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
          {dirty ? "保存词典" : "无更改"}
        </Button>
      </div>
      {loading ? (
        <div className="flex h-32 items-center justify-center gap-2 text-xs text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          加载术语词典…
        </div>
      ) : (
        <textarea
          value={content}
          onChange={(event) => setContent(event.currentTarget.value)}
          className="min-h-[280px] w-full resize-y rounded-xl border border-border bg-background p-4 font-mono text-xs leading-relaxed text-foreground focus:outline-none focus:ring-1 focus:ring-primary/50"
          spellCheck={false}
          aria-label="ASR 术语词典"
        />
      )}
      <p className="flex items-center gap-1.5 text-[10px] text-muted-foreground/70">
        <BookOpen className="h-3 w-3" />
        保存后从下一次 ASR 任务开始生效。
      </p>
    </section>
  );
}

export default function ApiConfigPage() {
  const { config, updateConfig, loaded, loadError } = useAppConfig();
  const [draft, setDraft] = useState<AppConfig>({ ...config });
  const [activeTab, setActiveTab] = useState<ApiConfigTabKey>("audit");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [ollamaModels, setOllamaModels] = useState<string[]>([]);
  const [ollamaLoading, setOllamaLoading] = useState(false);
  const [ollamaOnline, setOllamaOnline] = useState<boolean | null>(null);
  const [resourceDirty, setResourceDirty] = useState(false);
  const handleResourceDirtyChange = useCallback((dirty: boolean) => setResourceDirty(dirty), []);

  useEffect(() => {
    if (loaded) setDraft({ ...config });
  }, [config, loaded]);

  useEffect(() => {
    if (loadError === "backend_unavailable") {
      toast.warning("无法连接后端，当前显示本地缓存配置（只读）");
    } else if (loadError === "backend_stale") {
      toast.warning("后端版本过旧，缺少配置接口。请重启 FastAPI 后刷新页面");
    }
  }, [loadError]);

  const fetchOllamaModels = useCallback(async (baseUrl: string) => {
    setOllamaLoading(true);
    setOllamaOnline(null);
    try {
      const ollamaUrl = (baseUrl || "http://localhost:11434").replace(/\/$/, "");
      const res = await fetch(
        `/api/ollama/models?base_url=${encodeURIComponent(ollamaUrl)}`,
        { signal: AbortSignal.timeout(8000) },
      );
      if (res.ok) {
        const data = await res.json();
        setOllamaModels(data.models ?? []);
        setOllamaOnline(true);
      } else {
        setOllamaModels([]);
        setOllamaOnline(false);
      }
    } catch {
      setOllamaModels([]);
      setOllamaOnline(false);
    } finally {
      setOllamaLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!isFeatureTabKey(activeTab) || activeTab === "tts") return;
    const feat = draft.featureLlm[activeTab];
    if (feat.mode === "local") {
      fetchOllamaModels(feat.local.baseUrl);
    }
  }, [activeTab, draft.featureLlm, fetchOllamaModels]);

  const isDirty = JSON.stringify(draft) !== JSON.stringify(config);

  const patchDraft = (patch: Partial<AppConfig>) => {
    setSaved(false);
    setDraft((prev) => ({ ...prev, ...patch }));
  };

  const handleSave = async () => {
    if (!isDirty || loadError) return;
    setSaving(true);
    try {
      await updateConfig(draft);
      setSaved(true);
      toast.success("API 配置已保存到项目文件");
      setTimeout(() => setSaved(false), 2000);
    } catch {
      toast.error("保存失败，请重试");
    } finally {
      setSaving(false);
    }
  };

  const handleReset = () => {
    if (!isDirty) return;
    setDraft({ ...config });
    setSaved(false);
    toast.info("已还原为上次保存的配置");
  };

  const handleTabChange = (nextTab: ApiConfigTabKey) => {
    if (nextTab === activeTab) return;
    if (resourceDirty && !window.confirm("当前 Prompt 或词典有未保存的更改，切换后将丢失。继续？")) return;
    setResourceDirty(false);
    setActiveTab(nextTab);
  };

  const activeTabMeta = getApiConfigTab(activeTab);
  const activeLabel = getApiConfigTabShortLabel(draft, activeTab);
  const sectionNavItems: ConfigSectionNavItem[] = activeTab === "routing"
    ? [{ id: "config-routing", label: "供应商与角色", description: "模型路由矩阵" }]
    : activeTab === "asr"
    ? [
        { id: "config-connection", label: "运行方式", description: "本地模型说明" },
        { id: "config-performance", label: "性能参数", description: "分块与并行" },
        { id: "config-glossary", label: "术语词典", description: "识别纠错" },
        { id: "config-prompt", label: "Prompt 说明", description: "不适用说明" },
        { id: "config-general", label: "通用说明" },
        { id: "config-status", label: "系统状态" },
      ]
    : [
        { id: "config-connection", label: "连接与模式", description: "云端或本地" },
        { id: "config-model", label: "模型配置", description: "Key、端点与模型" },
        { id: "config-performance", label: "性能参数", description: "当前功能设置" },
        { id: "config-prompt", label: "当前 Prompt", description: "运行时生效内容" },
        { id: "config-general", label: "通用说明" },
        { id: "config-status", label: "系统状态" },
      ];

  if (!loaded) {
    return (
      <div className="h-full flex items-center justify-center text-muted-foreground gap-2">
        <Loader2 className="w-4 h-4 animate-spin" />
        加载配置中…
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col overflow-hidden">
      <div className="px-6 py-4 border-b border-border shrink-0">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-lg font-semibold text-foreground flex items-center gap-2">
              <Key className="w-5 h-5 text-primary" />
              AI 配置
            </h1>
            <p className="text-xs text-muted-foreground mt-0.5">
              按功能统一配置 Key、模型、性能与当前生效 Prompt · 当前 Tab：
              <span className="text-foreground/80 ml-1">
                {activeTabMeta.label}
              </span>
              <span className="text-muted-foreground/50 mx-1">·</span>
              <span className="font-mono text-foreground/70">{activeLabel}</span>
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" className="text-xs gap-1.5" onClick={handleReset} disabled={!isDirty}>
              <RotateCcw className="w-3.5 h-3.5" />
              还原
            </Button>
            <Button
              size="sm"
              className={cn("gap-1.5 text-xs", isDirty ? "bg-primary text-primary-foreground" : "bg-primary/60")}
              onClick={handleSave}
              disabled={saving || !isDirty || Boolean(loadError)}
            >
              {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : saved ? <CheckCircle2 className="w-3.5 h-3.5" /> : <Save className="w-3.5 h-3.5" />}
              {saved ? "已保存" : isDirty ? "保存配置" : "无更改"}
            </Button>
          </div>
        </div>

        <div className="flex gap-1 mt-4 overflow-x-auto pb-1">
          {API_CONFIG_TABS.map((tab) => (
            <button
              key={tab.key}
              type="button"
              onClick={() => handleTabChange(tab.key)}
              className={cn(
                "px-3 py-1.5 rounded-lg text-xs whitespace-nowrap transition-colors",
                activeTab === tab.key
                  ? "bg-primary/15 text-primary font-medium"
                  : "text-muted-foreground hover:text-foreground hover:bg-secondary/60",
              )}
            >
              {tab.label}
            </button>
          ))}
        </div>
      </div>

      <div className="flex-1 overflow-auto px-6 py-6">
        <div className="mx-auto flex max-w-6xl items-start gap-6">
          <ConfigSectionNav key={activeTab} items={sectionNavItems} />
          <div className="min-w-0 flex-1 space-y-8">
            {activeTab === "routing" ? (
              <section id="config-routing" className="scroll-mt-4">
                <ModelRoutingPanel
                  draft={draft}
                  onChange={(next) => {
                    setSaved(false);
                    setDraft(next);
                  }}
                />
              </section>
            ) : isFeatureTabKey(activeTab) ? (
              <FeatureTabPanel
                feature={activeTab}
                draft={draft}
                patchDraft={patchDraft}
                ollamaModels={ollamaModels}
                ollamaLoading={ollamaLoading}
                ollamaOnline={ollamaOnline}
                onRefreshOllama={fetchOllamaModels}
                onPromptDirtyChange={handleResourceDirtyChange}
              />
            ) : (
              <>
                <div id="config-connection" className="scroll-mt-4">
                  <InfoPanel title="连接与模型">
                    FunASR 在本机运行，不需要 API Key；识别模型在启动 ASR 任务时选择，本页只管理通用性能参数。
                  </InfoPanel>
                </div>
                <div id="config-performance" className="scroll-mt-4">
                  <AsrRuntimePanel draft={draft} patchDraft={patchDraft} />
                </div>
                <div id="config-glossary" className="scroll-mt-4">
                  <GlossaryPanel onDirtyChange={handleResourceDirtyChange} />
                </div>
                <PromptConfigPanel
                  feature="asr"
                  sectionId="config-prompt"
                  emptyTitle="本地 ASR 无 LLM Prompt"
                  emptyDescription="ASR 使用本地语音识别模型；专业词纠错由上方术语词典控制。"
                />
              </>
            )}

            <section id="config-general" className="scroll-mt-4 space-y-3">
              <SectionHeader title="通用说明" />
              <div className="rounded-xl border border-border/40 bg-secondary/30 px-5 py-4 space-y-2">
                <div className="flex items-center gap-2 mb-1">
                  <Info className="w-3.5 h-3.5 text-primary" />
                  <p className="text-xs font-medium text-foreground">生效规则</p>
                </div>
                <ul className="space-y-1.5">
                  {FEATURE_FALLBACK.map((rule) => (
                    <li key={rule} className="text-[11px] text-muted-foreground flex gap-2">
                      <span className="text-primary/60 shrink-0">·</span>
                      <span>{rule}</span>
                    </li>
                  ))}
                </ul>
              </div>
              <InfoPanel title="密钥存储说明">
                各功能 Key 保存在本机{" "}
                <code className="font-mono bg-secondary px-1 rounded">backend/data/app_config.json</code>
                ，发起任务时随请求传给本机后端，不会写入任务 JSON。
              </InfoPanel>
            </section>

            <section id="config-status" className="scroll-mt-4 space-y-3">
              <SectionHeader title="系统状态" subtitle="本地运行环境概览。" />
              <div className="space-y-2">
                {STATUS_ITEMS.map((item) => {
                  const Icon = item.icon as ComponentType<{ className?: string }>;
                  return (
                    <div
                      key={item.label}
                      className="flex items-center justify-between px-4 py-2.5 rounded-lg bg-secondary/50 border border-border/30"
                    >
                      <div className="flex items-center gap-2">
                        <Icon className="w-3.5 h-3.5 text-muted-foreground" />
                        <span className="text-xs text-foreground">{item.label}</span>
                      </div>
                      <div className="flex items-center gap-2">
                        <div className="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse" />
                        {item.port && <span className="font-mono text-muted-foreground text-[10px]">:{item.port}</span>}
                      </div>
                    </div>
                  );
                })}
              </div>
            </section>
          </div>
        </div>
      </div>
    </div>
  );
}
