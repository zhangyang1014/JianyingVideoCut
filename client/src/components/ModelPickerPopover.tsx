/**
 * ModelPickerPopover — 快速模型选择器
 * 在审计/分析按钮旁显示当前模型名，点击弹出 Popover 可切换 Provider 和模型
 */

import { useState, useEffect, useCallback } from "react";
import { ChevronDown, Settings, Check, Loader2 } from "lucide-react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";
import {
  type AppConfig,
  type LLMProvider,
  CLAUDE_MODELS,
  LLM_PROVIDERS,
  getModelShortLabel,
} from "@/hooks/useAppConfig";

interface ModelPickerPopoverProps {
  config: AppConfig;
  updateConfig: (patch: Partial<AppConfig>) => void;
  disabled?: boolean;
}

export function ModelPickerPopover({ config, updateConfig, disabled }: ModelPickerPopoverProps) {
  const [open, setOpen] = useState(false);
  const [ollamaModels, setOllamaModels] = useState<string[]>([]);
  const [ollamaLoading, setOllamaLoading] = useState(false);

  const fetchOllamaModels = useCallback(async () => {
    setOllamaLoading(true);
    try {
      const baseUrl = (config.ollamaBaseUrl || "http://localhost:11434").replace(/\/$/, "");
      const res = await fetch(
        `http://localhost:8000/api/ollama/models?base_url=${encodeURIComponent(baseUrl)}`,
        { signal: AbortSignal.timeout(5000) }
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
  }, [config.ollamaBaseUrl]);

  useEffect(() => {
    if (open && config.provider === "ollama") {
      fetchOllamaModels();
    }
  }, [open, config.provider, fetchOllamaModels]);

  const shortLabel = getModelShortLabel(config);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          disabled={disabled}
          className={cn(
            "flex items-center gap-1 px-2 py-1 h-7 rounded text-xs border transition-colors",
            "border-border text-muted-foreground hover:text-foreground hover:bg-secondary/60",
            disabled && "opacity-50 cursor-not-allowed"
          )}
          title="快速切换模型"
        >
          <span className="truncate max-w-[100px]">{shortLabel}</span>
          <ChevronDown className="w-3 h-3 shrink-0 opacity-50" />
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-64 p-0 bg-card border-border">
        {/* 提供商切换 */}
        <div className="px-3 py-2 border-b border-border">
          <p className="text-[10px] text-muted-foreground/60 uppercase tracking-wider mb-1.5">提供商</p>
          <div className="flex gap-1">
            {LLM_PROVIDERS.map((p) => (
              <button
                key={p.value}
                onClick={() => updateConfig({ provider: p.value })}
                className={cn(
                  "flex-1 px-2 py-1 rounded text-xs transition-colors text-center",
                  config.provider === p.value
                    ? "bg-primary/20 text-primary font-medium"
                    : "text-muted-foreground hover:text-foreground hover:bg-secondary/60"
                )}
              >
                {p.label}
              </button>
            ))}
          </div>
        </div>

        {/* 模型列表 */}
        <div className="max-h-56 overflow-y-auto">
          {config.provider === "claude" ? (
            CLAUDE_MODELS.map((m) => {
              const selected = config.claudeModel === m.value;
              return (
                <button
                  key={m.value}
                  onClick={() => {
                    updateConfig({ claudeModel: m.value });
                    setOpen(false);
                  }}
                  className={cn(
                    "w-full text-left px-3 py-2 flex items-center gap-2 transition-colors",
                    selected ? "bg-primary/10" : "hover:bg-secondary/60"
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
            })
          ) : (
            <>
              {ollamaLoading && (
                <div className="flex items-center gap-2 px-3 py-3 text-xs text-muted-foreground">
                  <Loader2 className="w-3 h-3 animate-spin" />
                  加载模型列表...
                </div>
              )}
              {!ollamaLoading && ollamaModels.length === 0 && (
                <div className="px-3 py-3 text-xs text-muted-foreground/60">
                  未检测到 Ollama 模型
                </div>
              )}
              {!ollamaLoading && ollamaModels.map((m) => {
                const selected = config.ollamaModel === m;
                return (
                  <button
                    key={m}
                    onClick={() => {
                      updateConfig({ ollamaModel: m });
                      setOpen(false);
                    }}
                    className={cn(
                      "w-full text-left px-3 py-2 flex items-center gap-2 transition-colors",
                      selected ? "bg-primary/10" : "hover:bg-secondary/60"
                    )}
                  >
                    <div className="w-4 shrink-0 flex justify-center">
                      {selected && <Check className="w-3 h-3 text-primary" />}
                    </div>
                    <p className={cn(
                      "text-xs font-mono truncate",
                      selected ? "text-primary font-medium" : "text-foreground"
                    )}>
                      {m}
                    </p>
                  </button>
                );
              })}
            </>
          )}
        </div>

        {/* 底部：跳转完整设置 */}
        <div className="border-t border-border px-3 py-1.5">
          <a
            href="/config"
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
