import { useEffect, useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { CheckCircle2, Loader2, Plus, RotateCcw, Trash2 } from "lucide-react";
import { toast } from "sonner";
import {
  fetchProviderPricingStatuses,
  listAvailableLlmModels,
  syncProviderPricing,
  testLlmModel,
  type AvailableLlmModel,
  type ProviderPricingStatus,
} from "@/lib/api";
import {
  DEFAULT_LLM_ROLE_ROUTES,
  DEFAULT_PROVIDER_PROFILES,
  type AppConfig,
  type LlmRoleKey,
  type ProviderProfile,
} from "@/hooks/useAppConfig";

interface ModelRoutingPanelProps {
  draft: AppConfig;
  onChange: (next: AppConfig) => void;
}

const ROLE_GROUPS: { label: string; roles: LlmRoleKey[] }[] = [
  { label: "语义审计", roles: ["audit.semantic", "audit.coherence"] },
  { label: "字幕与内容", roles: ["translate.subtitle", "golden_quote.extract"] },
  { label: "精彩集锦", roles: ["highlight.strategy", "highlight.validation"] },
  { label: "学习优化", roles: ["learning.analyze", "learning.rewrite", "prompt_feedback.optimize"] },
];

const TIER_LABEL = {
  primary: "主模型",
  fallback: "降级",
  escalation: "旗舰升级",
};

export default function ModelRoutingPanel({ draft, onChange }: ModelRoutingPanelProps) {
  const [testingId, setTestingId] = useState("");
  const [catalogLoadingId, setCatalogLoadingId] = useState("");
  const [pricingSyncingId, setPricingSyncingId] = useState("");
  const [catalogs, setCatalogs] = useState<Record<string, AvailableLlmModel[]>>({});
  const [pricingStatuses, setPricingStatuses] = useState<Record<string, ProviderPricingStatus>>({});
  const providerProfiles = draft.providerProfiles ?? DEFAULT_PROVIDER_PROFILES;
  const llmRoleRoutes = draft.llmRoleRoutes ?? DEFAULT_LLM_ROLE_ROUTES;
  const profileMap = useMemo(
    () => new Map(providerProfiles.map((profile) => [profile.id, profile])),
    [providerProfiles],
  );

  useEffect(() => {
    let cancelled = false;
    void fetchProviderPricingStatuses()
      .then((statuses) => {
        if (!cancelled) {
          setPricingStatuses(Object.fromEntries(
            statuses.map((status) => [status.provider_profile_id, status]),
          ));
        }
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  const updateProfile = (id: string, patch: Partial<ProviderProfile>) => {
    onChange({
      ...draft,
      providerProfiles: providerProfiles.map((profile) =>
        profile.id === id ? { ...profile, ...patch } : profile),
    });
  };

  const addProfile = () => {
    let index = 1;
    while (profileMap.has(`custom-${index}`)) index += 1;
    onChange({
      ...draft,
      providerProfiles: [
        ...providerProfiles,
        {
          id: `custom-${index}`,
          label: `自定义供应商 ${index}`,
          provider: "openai_compatible",
          baseUrl: "",
          apiKey: "",
          apiKeyEnv: "",
          enabled: true,
        },
      ],
    });
  };

  const removeProfile = (id: string) => {
    const referenced = Object.values(llmRoleRoutes).some((route) =>
      route.candidates.some((candidate) => candidate.providerProfileId === id));
    if (referenced) {
      toast.error("该供应商仍被任务角色引用，请先调整候选");
      return;
    }
    onChange({
      ...draft,
      providerProfiles: providerProfiles.filter((profile) => profile.id !== id),
    });
  };

  const testProfile = async (profile: ProviderProfile) => {
    const model = Object.values(llmRoleRoutes)
      .flatMap((route) => route.candidates)
      .find((candidate) => candidate.providerProfileId === profile.id)?.model;
    if (!profile.baseUrl || !model) {
      toast.error("请先填写 Base URL，并让至少一个角色引用该供应商");
      return;
    }
    setTestingId(profile.id);
    try {
      const result = await testLlmModel({
        feature: "audit",
        provider_profile_id: profile.id,
        base_url: profile.baseUrl,
        api_key: profile.apiKey || undefined,
        model,
        kind: "chat",
      });
      result.success ? toast.success(`${profile.label} 连接成功`) : toast.error(`${profile.label} 试跑失败`);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "模型试跑失败");
    } finally {
      setTestingId("");
    }
  };

  const loadCatalog = async (profile: ProviderProfile) => {
    setCatalogLoadingId(profile.id);
    try {
      const models = await listAvailableLlmModels(
        "audit",
        profile.baseUrl,
        true,
        profile.id,
      );
      setCatalogs((current) => ({ ...current, [profile.id]: models }));
      toast.success(`已读取 ${models.length} 个模型`);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "读取模型目录失败");
    } finally {
      setCatalogLoadingId("");
    }
  };

  const syncPricing = async (profile: ProviderProfile) => {
    setPricingSyncingId(profile.id);
    try {
      const status = await syncProviderPricing(profile.id);
      setPricingStatuses((current) => ({ ...current, [profile.id]: status }));
      toast.success(`已同步 ${status.model_count} 个 RouterHub 模型报价`);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "同步供应商报价失败");
    } finally {
      setPricingSyncingId("");
    }
  };

  const resetRecommended = () => {
    onChange({
      ...draft,
      providerProfiles: DEFAULT_PROVIDER_PROFILES.map((profile) => ({ ...profile })),
      llmRoleRoutes: structuredClone(DEFAULT_LLM_ROLE_ROUTES),
    });
    toast.success("已恢复推荐路由，保存后生效");
  };

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-sm font-semibold">供应商凭据档案</h2>
          <p className="mt-1 text-xs text-muted-foreground">
            凭据按供应商复用；任务角色只引用档案和模型，不再重复保存 Base URL 与 Key。
          </p>
        </div>
        <div className="flex gap-2">
          <Button type="button" variant="outline" size="sm" onClick={addProfile}>
            <Plus className="mr-1 h-3.5 w-3.5" />新增供应商
          </Button>
          <Button type="button" variant="outline" size="sm" onClick={resetRecommended}>
            <RotateCcw className="mr-1 h-3.5 w-3.5" />恢复推荐路由
          </Button>
        </div>
      </div>

      <div className="grid gap-3 lg:grid-cols-2">
        {providerProfiles.map((profile) => (
          <div key={profile.id} className="rounded-lg border bg-card p-4 space-y-3">
            <div className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={profile.enabled}
                onChange={(event) => updateProfile(profile.id, { enabled: event.target.checked })}
                aria-label={`启用 ${profile.label}`}
              />
              <Input
                value={profile.label}
                onChange={(event) => updateProfile(profile.id, { label: event.target.value })}
                className="h-8 font-medium"
              />
              {profile.id === "routerhub" && <Badge>首选 API</Badge>}
              <Badge variant="outline">{profile.provider === "ollama" ? "Ollama" : "兼容 API"}</Badge>
            </div>
            <Input
              value={profile.baseUrl}
              onChange={(event) => updateProfile(profile.id, { baseUrl: event.target.value })}
              placeholder="Base URL"
            />
            {profile.provider !== "ollama" && (
              <div className="grid gap-2 sm:grid-cols-2">
                <Input
                  type="password"
                  value={profile.apiKey}
                  onChange={(event) => updateProfile(profile.id, { apiKey: event.target.value })}
                  placeholder="API Key"
                />
                <Input
                  value={profile.apiKeyEnv}
                  onChange={(event) => updateProfile(profile.id, { apiKeyEnv: event.target.value })}
                  placeholder="环境变量名"
                />
              </div>
            )}
            {profile.id === "routerhub" && (
              <div className="rounded-md border border-primary/20 bg-primary/5 px-3 py-2 text-[10px] text-muted-foreground">
                {pricingStatuses[profile.id] ? (
                  <>
                    <p>
                      报价登记：{new Date(pricingStatuses[profile.id].registered_at).toLocaleString("zh-CN", { hour12: false })}
                    </p>
                    <p>
                      最近同步：{new Date(pricingStatuses[profile.id].synced_at).toLocaleString("zh-CN", { hour12: false })}
                      {" · "}{pricingStatuses[profile.id].model_count} 个模型
                    </p>
                  </>
                ) : (
                  <p>报价尚未登记；同步后会用于新调用和历史估算金额展示。</p>
                )}
              </div>
            )}
            <div className="flex justify-between">
              <Button type="button" variant="ghost" size="sm" onClick={() => removeProfile(profile.id)}>
                <Trash2 className="mr-1 h-3.5 w-3.5" />删除
              </Button>
              {profile.provider !== "ollama" && (
                <div className="flex gap-2">
                  {profile.id === "routerhub" && (
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      disabled={pricingSyncingId === profile.id}
                      onClick={() => void syncPricing(profile)}
                    >
                      {pricingSyncingId === profile.id && <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />}
                      同步最新报价
                    </Button>
                  )}
                  <Button type="button" variant="outline" size="sm" onClick={() => void loadCatalog(profile)}>
                    {catalogLoadingId === profile.id && <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />}
                    模型目录
                  </Button>
                  <Button type="button" variant="outline" size="sm" onClick={() => void testProfile(profile)}>
                    {testingId === profile.id
                      ? <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
                      : <CheckCircle2 className="mr-1 h-3.5 w-3.5" />}
                    试跑
                  </Button>
                </div>
              )}
            </div>
          </div>
        ))}
      </div>

      <div>
        <h2 className="text-sm font-semibold">任务角色路由</h2>
        <p className="mt-1 text-xs text-muted-foreground">
          候选按顺序执行；契约失败、限流或服务错误会降级，旗舰升级只在前序失败或质量门控未通过时使用。
          RouterHub 历史估算金额按最近一次同步报价重新展示，供应商返回的实际费用不会被覆盖。
        </p>
      </div>

      {ROLE_GROUPS.map((group) => (
        <section key={group.label} className="space-y-3">
          <h3 className="text-xs font-semibold text-muted-foreground">{group.label}</h3>
          {group.roles.map((roleKey) => {
            const route = llmRoleRoutes[roleKey];
            return (
              <div key={roleKey} className="rounded-lg border bg-card p-4 space-y-3">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium text-sm">{route.label}</span>
                  <code className="text-[10px] text-muted-foreground">{roleKey}</code>
                  {route.qualityGateEscalation && <Badge variant="secondary">质量门控</Badge>}
                  <span className="text-xs text-muted-foreground">{route.purpose}</span>
                </div>
                <div className="space-y-2">
                  {route.candidates.map((candidate, index) => (
                    <div key={`${candidate.providerProfileId}-${candidate.model}-${index}`} className="rounded-md border border-border/50 p-2">
                      <div className="grid gap-2 sm:grid-cols-[82px_1fr_1.4fr_90px]">
                        <Badge variant={candidate.tier === "primary" ? "default" : "outline"} className="justify-center">
                          {TIER_LABEL[candidate.tier]}
                        </Badge>
                        <select
                          className="h-9 rounded-md border bg-background px-2 text-sm"
                          value={candidate.providerProfileId}
                          onChange={(event) => {
                            const candidates = route.candidates.map((item, itemIndex) =>
                              itemIndex === index ? { ...item, providerProfileId: event.target.value } : item);
                            onChange({ ...draft, llmRoleRoutes: { ...llmRoleRoutes, [roleKey]: { ...route, candidates } } });
                          }}
                        >
                          {providerProfiles.map((profile) => (
                            <option key={profile.id} value={profile.id}>{profile.label}</option>
                          ))}
                        </select>
                        <Input
                          value={candidate.model}
                          list={`models-${roleKey}-${index}`}
                          onChange={(event) => {
                            const selected = (catalogs[candidate.providerProfileId] ?? [])
                              .find((model) => model.id === event.target.value);
                            const candidates = route.candidates.map((item, itemIndex) =>
                              itemIndex === index ? {
                                ...item,
                                model: event.target.value,
                                pricing: selected ? {
                                  ...item.pricing,
                                  inputPerMillionTokens: selected.pricing.prompt == null
                                    ? item.pricing?.inputPerMillionTokens
                                    : Number(selected.pricing.prompt) * 1_000_000,
                                  outputPerMillionTokens: selected.pricing.completion == null
                                    ? item.pricing?.outputPerMillionTokens
                                    : Number(selected.pricing.completion) * 1_000_000,
                                } : item.pricing,
                              } : item);
                            onChange({ ...draft, llmRoleRoutes: { ...llmRoleRoutes, [roleKey]: { ...route, candidates } } });
                          }}
                          placeholder="模型 ID"
                        />
                        <datalist id={`models-${roleKey}-${index}`}>
                          {(catalogs[candidate.providerProfileId] ?? []).map((model) => (
                            <option key={model.id} value={model.id}>{model.name}</option>
                          ))}
                        </datalist>
                        <label className="flex items-center gap-2 text-xs">
                          <input
                            type="checkbox"
                            checked={candidate.enabled}
                            onChange={(event) => {
                              const candidates = route.candidates.map((item, itemIndex) =>
                                itemIndex === index ? { ...item, enabled: event.target.checked } : item);
                              onChange({ ...draft, llmRoleRoutes: { ...llmRoleRoutes, [roleKey]: { ...route, candidates } } });
                            }}
                          />
                          启用
                        </label>
                      </div>
                      {profileMap.get(candidate.providerProfileId)?.provider !== "ollama" && (
                        <div className="mt-2 grid gap-2 pl-[90px] sm:grid-cols-2">
                          <Input
                            type="number"
                            min={0}
                            step="0.01"
                            value={candidate.pricing?.inputPerMillionTokens ?? ""}
                            onChange={(event) => {
                              const value = event.target.value === "" ? undefined : Number(event.target.value);
                              const candidates = route.candidates.map((item, itemIndex) =>
                                itemIndex === index
                                  ? { ...item, pricing: { ...item.pricing, inputPerMillionTokens: value } }
                                  : item);
                              onChange({ ...draft, llmRoleRoutes: { ...llmRoleRoutes, [roleKey]: { ...route, candidates } } });
                            }}
                            placeholder="输入价 USD / 百万 token"
                          />
                          <Input
                            type="number"
                            min={0}
                            step="0.01"
                            value={candidate.pricing?.outputPerMillionTokens ?? ""}
                            onChange={(event) => {
                              const value = event.target.value === "" ? undefined : Number(event.target.value);
                              const candidates = route.candidates.map((item, itemIndex) =>
                                itemIndex === index
                                  ? { ...item, pricing: { ...item.pricing, outputPerMillionTokens: value } }
                                  : item);
                              onChange({ ...draft, llmRoleRoutes: { ...llmRoleRoutes, [roleKey]: { ...route, candidates } } });
                            }}
                            placeholder="输出价 USD / 百万 token"
                          />
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            );
          })}
        </section>
      ))}
    </div>
  );
}
