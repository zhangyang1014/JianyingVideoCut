/**
 * useAppConfig — 全局应用配置持久化 Hook
 * 将 Claude API Key、模型选择、剪映默认目录等配置存储到 localStorage
 */

import { useState, useCallback } from "react";

const APP_CONFIG_KEY = "goldenclip_app_config";

export type LLMProvider = "claude" | "ollama";

export interface AppConfig {
  claudeApiKey: string;
  claudeModel: string;
  jianyingDir: string;
  /** 模型提供商：claude = OpenRouter/Anthropic API，ollama = 本地 Ollama */
  provider: LLMProvider;
  /** 本地 Ollama 选用的模型名，如 deepseek-r1:32b */
  ollamaModel: string;
  /** Ollama 服务地址，默认 http://localhost:11434 */
  ollamaBaseUrl: string;
}

const DEFAULT_CONFIG: AppConfig = {
  claudeApiKey: "",
  claudeModel: "claude-sonnet-4-6",
  jianyingDir: "",
  provider: "claude",
  ollamaModel: "deepseek-r1:14b",
  ollamaBaseUrl: "http://localhost:11434",
};

/** 从 localStorage 读取配置，合并默认值 */
function loadConfig(): AppConfig {
  try {
    const raw = localStorage.getItem(APP_CONFIG_KEY);
    if (!raw) return DEFAULT_CONFIG;
    return { ...DEFAULT_CONFIG, ...JSON.parse(raw) };
  } catch {
    return DEFAULT_CONFIG;
  }
}

/** 将配置写入 localStorage */
function saveConfig(config: AppConfig): void {
  try {
    localStorage.setItem(APP_CONFIG_KEY, JSON.stringify(config));
  } catch {
    // 静默失败，不影响主流程
  }
}

export function useAppConfig() {
  const [config, setConfig] = useState<AppConfig>(loadConfig);

  const updateConfig = useCallback((patch: Partial<AppConfig>) => {
    setConfig((prev) => {
      const next = { ...prev, ...patch };
      saveConfig(next);
      return next;
    });
  }, []);

  return { config, updateConfig };
}

// ============================================================
// 共享常量：模型 & 提供商列表（供 ApiConfigPage / ReviewWorkbench / LearningPage 复用）
// ============================================================

export interface ClaudeModelOption {
  value: string;
  label: string;
  shortLabel: string;
  desc: string;
}

export const CLAUDE_MODELS: ClaudeModelOption[] = [
  { value: "claude-opus-4-6",            label: "Claude Opus 4.6",   shortLabel: "Opus 4.6",   desc: "最强推理 · 200K 输出 · 成本最高" },
  { value: "claude-sonnet-4-6",          label: "Claude Sonnet 4.6", shortLabel: "Sonnet 4.6", desc: "速度与智能均衡 · 推荐 · 64K 输出" },
  { value: "claude-3-7-sonnet-20250219", label: "Claude 3.7 Sonnet", shortLabel: "3.7 Sonnet", desc: "扩展思考 · 语义最强 · 64K 输出" },
];

export const LLM_PROVIDERS: { value: LLMProvider; label: string; desc: string }[] = [
  { value: "claude", label: "Claude API",   desc: "OpenRouter / Anthropic · 云端推理" },
  { value: "ollama", label: "本地 Ollama",   desc: "完全离线 · 数据不出本机" },
];

/** 根据 config 获取当前模型的简短显示名称 */
export function getModelShortLabel(config: AppConfig): string {
  if (config.provider === "ollama") {
    const name = config.ollamaModel || "未选择";
    return name.length > 14 ? name.slice(0, 12) + "…" : name;
  }
  const found = CLAUDE_MODELS.find((m) => m.value === config.claudeModel);
  return found?.shortLabel ?? config.claudeModel;
}
