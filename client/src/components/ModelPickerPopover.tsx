/**
 * ModelPickerPopover — 按功能快速切换模型
 */

import { useState, useEffect, useCallback } from "react";
import { ChevronDown, Settings, Check, Loader2 } from "lucide-react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";
import {
  type AppConfig,
  type FeatureKey,
  CLOUD_MODEL_SUGGESTIONS,
  MODEL_GROUP_LABELS,
  getModelShortLabel,
  patchFeatureLlm,
  patchRolePrimaryModel,
} from "@/hooks/useAppConfig";

interface ModelPickerPopoverProps {
  config: AppConfig;
  updateConfig: (patch: Partial<AppConfig>) => void | Promise<void>;
  disabled?: boolean;
  /** 当前上下文功能，默认语义审计 */
  feature?: FeatureKey;
}

export function ModelPickerPopover({
  config,
  updateConfig,
  disabled,
  feature = "audit",
}: ModelPickerPopoverProps) {
  const [open, setOpen] = useState(false);
  const [ollamaModels, setOllamaModels] = useState<string[]>([]);
  const [ollamaLoading, setOllamaLoading] = useState(false);

  const isTts = feature === "tts";
  const featConfig = !isTts ? config.featureLlm[feature] : null;
  const mode = isTts ? config.featureLlm.tts.mode : featConfig!.mode;
  const cloudModel = isTts ? config.featureLlm.tts.cloud.model : featConfig!.cloud.model;
  const localModel = featConfig?.local.model ?? "";
  const localBaseUrl = featConfig?.local.baseUrl ?? "http://localhost:11434";

  const fetchOllamaModels = useCallback(async () => {
    setOllamaLoading(true);
    try {
      const baseUrl = (localBaseUrl || "http://localhost:11434").replace(/\/$/, "");
      const res = await fetch(
        `/api/ollama/models?base_url=${encodeURIComponent(baseUrl)}`,
        { signal: AbortSignal.timeout(5000) },
      );
      if (res.ok) {
        const data = await res.json();
        setOllamaModels(data.models ?? []);
      }
    } catch {
      // 静默失败
    } finally {
      setOllamaLoading(false);
    }
  }, [localBaseUrl]);

  useEffect(() => {
    if (open && !isTts && mode === "local") {
      fetchOllamaModels();
    }
  }, [open, isTts, mode, fetchOllamaModels]);

  const shortLabel = getModelShortLabel(config, feature);
  const cloudSuggestions = CLOUD_MODEL_SUGGESTIONS[feature];
  const modelGroups = (["recommended", "quality", "balanced", "economy", "experimental"] as const)
    .map((group) => ({
      group,
      models: cloudSuggestions.filter((m) => (m.group ?? "balanced") === group),
    }))
    .filter((item) => item.models.length > 0);

  const applyPatch = (patch: Parameters<typeof patchFeatureLlm>[2]) => {
    updateConfig(patchFeatureLlm(config, feature, patch));
  };

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          disabled={disabled}
          className={cn(
            "flex items-center gap-1 px-2 py-1 h-7 rounded text-xs border transition-colors",
            "border-border text-muted-foreground hover:text-foreground hover:bg-secondary/60",
            disabled && "opacity-50 cursor-not-allowed",
          )}
          title="快速切换模型"
        >
          <span className="truncate max-w-[100px]">{shortLabel}</span>
          <ChevronDown className="w-3 h-3 shrink-0 opacity-50" />
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-64 p-0 bg-card border-border">
        {!isTts && (
          <div className="px-3 py-2 border-b border-border">
            <p className="text-[10px] text-muted-foreground/60 uppercase tracking-wider mb-1.5">路线</p>
            <div className="flex gap-1">
              {(["cloud", "local"] as const).map((m) => (
                <button
                  key={m}
                  onClick={() => applyPatch({ mode: m })}
                  className={cn(
                    "flex-1 px-2 py-1 rounded text-xs transition-colors text-center",
                    mode === m
                      ? "bg-primary/20 text-primary font-medium"
                      : "text-muted-foreground hover:text-foreground hover:bg-secondary/60",
                  )}
                >
                  {m === "cloud" ? "云端" : "本地"}
                </button>
              ))}
            </div>
          </div>
        )}

        <div className="max-h-56 overflow-y-auto">
          {!isTts && mode === "local" ? (
            <>
              {ollamaLoading && (
                <div className="flex items-center gap-2 px-3 py-3 text-xs text-muted-foreground">
                  <Loader2 className="w-3 h-3 animate-spin" />
                  加载模型列表...
                </div>
              )}
              {!ollamaLoading && ollamaModels.length === 0 && (
                <div className="px-3 py-3 text-xs text-muted-foreground/60">未检测到 Ollama 模型</div>
              )}
              {!ollamaLoading && ollamaModels.map((m) => {
                const selected = localModel === m;
                return (
                  <button
                    key={m}
                    onClick={() => {
                      applyPatch({ local: { ...featConfig!.local, model: m } });
                      setOpen(false);
                    }}
                    className={cn(
                      "w-full text-left px-3 py-2 flex items-center gap-2 transition-colors",
                      selected ? "bg-primary/10" : "hover:bg-secondary/60",
                    )}
                  >
                    <div className="w-4 shrink-0 flex justify-center">
                      {selected && <Check className="w-3 h-3 text-primary" />}
                    </div>
                    <p className={cn("text-xs font-mono truncate", selected ? "text-primary font-medium" : "text-foreground")}>
                      {m}
                    </p>
                  </button>
                );
              })}
            </>
          ) : (
            modelGroups.map(({ group, models }) => (
              <div key={group} className="py-1">
                <p className="px-3 py-1 text-[10px] text-muted-foreground/50 uppercase tracking-wider">
                  {MODEL_GROUP_LABELS[group]}
                </p>
                {models.map((m) => {
                  const selected = cloudModel === m.value;
                  return (
                    <button
                      key={m.value}
                      onClick={() => {
                        if (isTts) {
                          applyPatch({ cloud: { ...config.featureLlm.tts.cloud, model: m.value } });
                        } else {
                          const legacyPatched = patchFeatureLlm(config, feature, {
                            cloud: { ...featConfig!.cloud, model: m.value },
                          });
                          updateConfig(patchRolePrimaryModel(legacyPatched, feature, m.value));
                        }
                        setOpen(false);
                      }}
                      className={cn(
                        "w-full text-left px-3 py-2 flex items-center gap-2 transition-colors",
                        selected ? "bg-primary/10" : "hover:bg-secondary/60",
                      )}
                    >
                      <div className="w-4 shrink-0 flex justify-center">
                        {selected && <Check className="w-3 h-3 text-primary" />}
                      </div>
                      <div className="flex-1 min-w-0">
                        <p className={cn("text-xs", selected ? "text-primary font-medium" : "text-foreground")}>
                          {m.shortLabel}
                        </p>
                        <p className="text-[10px] text-muted-foreground/60 truncate">{m.desc}</p>
                      </div>
                    </button>
                  );
                })}
              </div>
            ))
          )}
        </div>

        <div className="border-t border-border px-3 py-1.5">
          <a
            href="/config/api"
            className="flex items-center gap-1.5 text-[10px] text-muted-foreground/60 hover:text-muted-foreground transition-colors"
          >
            <Settings className="w-3 h-3" />
            完整设置
          </a>
        </div>
      </PopoverContent>
    </Popover>
  );
}
