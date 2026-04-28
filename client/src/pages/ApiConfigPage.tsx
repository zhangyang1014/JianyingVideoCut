/**
 * GoldenClip API 配置页
 * Design: 暗金剪辑台 · 编导美学
 *
 * 独立管理 Claude API Key、模型选择、剪映草稿目录、本地 Ollama 模型。
 * 使用本地 draft 状态追踪未保存变更，修改后按钮亮起，点击才写入 localStorage。
 */

import { useState, useEffect, useCallback } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import {
  Save, Key, Bot, FolderOpen, Loader2, CheckCircle2,
  RotateCcw, ShieldCheck, Terminal, HardDrive, Cpu,
  RefreshCw, Wifi, WifiOff,
} from "lucide-react";
import {
  useAppConfig, type AppConfig,
  CLAUDE_MODELS, LLM_PROVIDERS,
} from "@/hooks/useAppConfig";

const PROVIDERS = LLM_PROVIDERS.map((p) => ({
  ...p,
  label: p.value === "claude" ? `☁ ${p.label}` : `⚡ ${p.label}`,
}));

// ============================================================
// 系统状态项
// ============================================================
const STATUS_ITEMS = [
  { label: "FastAPI 后端", port: 8000, icon: Terminal },
  { label: "React 前端",   port: 5174, icon: Cpu },
  { label: "FFmpeg",       port: null, icon: HardDrive },
];

// ============================================================
// Main ApiConfigPage
// ============================================================
export default function ApiConfigPage() {
  const { config, updateConfig } = useAppConfig();

  // 本地草稿，仅在用户点击"保存"后才写入 localStorage
  const [draft, setDraft] = useState<AppConfig>({ ...config });
  const [saving, setSaving] = useState(false);
  const [saved,  setSaved]  = useState(false);

  // Ollama 相关状态
  const [ollamaModels, setOllamaModels]     = useState<string[]>([]);
  const [ollamaLoading, setOllamaLoading]   = useState(false);
  const [ollamaOnline, setOllamaOnline]     = useState<boolean | null>(null);

  // config 更新后（保存成功）同步 draft，避免外部修改导致 isDirty 误判
  useEffect(() => {
    setDraft({ ...config });
  }, [config]);

  // 切换到 ollama 时自动刷新模型列表
  useEffect(() => {
    if (draft.provider === "ollama") {
      fetchOllamaModels(draft.ollamaBaseUrl);
    }
  }, [draft.provider, draft.ollamaBaseUrl]);

  const fetchOllamaModels = useCallback(async (baseUrl: string) => {
    setOllamaLoading(true);
    setOllamaOnline(null);
    try {
      const ollamaUrl = (baseUrl || "http://localhost:11434").replace(/\/$/, "");
      // 使用完整后端地址，与 api.ts 中 API_BASE 保持一致
      const res = await fetch(
        `http://localhost:8000/api/ollama/models?base_url=${encodeURIComponent(ollamaUrl)}`,
        { signal: AbortSignal.timeout(8000) }
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

  const isDirty = JSON.stringify(draft) !== JSON.stringify(config);

  const patchDraft = (patch: Partial<AppConfig>) => {
    setSaved(false);
    setDraft((prev) => ({ ...prev, ...patch }));
  };

  const handleSave = async () => {
    if (!isDirty) return;
    setSaving(true);
    try {
      updateConfig(draft);
      setSaved(true);
      toast.success("API 配置已保存");
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

  return (
    <div className="h-full flex flex-col overflow-hidden">
      {/* Header */}
      <div className="px-6 py-4 border-b border-border shrink-0">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-lg font-semibold text-foreground flex items-center gap-2">
              <Key className="w-5 h-5 text-primary" />
              API 配置
            </h1>
            <p className="text-xs text-muted-foreground mt-0.5">
              选择 Claude API 或本地 Ollama 模型，保存后全局生效
            </p>
          </div>

          <div className="flex items-center gap-2">
            {/* 还原按钮 */}
            <Button
              variant="outline"
              size="sm"
              className="border-border text-muted-foreground hover:text-foreground gap-1.5 text-xs"
              onClick={handleReset}
              disabled={!isDirty}
            >
              <RotateCcw className="w-3.5 h-3.5" />
              还原
            </Button>

            {/* 保存按钮 */}
            <Button
              size="sm"
              className={cn(
                "gap-1.5 text-xs",
                isDirty
                  ? "bg-primary text-primary-foreground"
                  : "bg-primary/60 text-primary-foreground/70"
              )}
              onClick={handleSave}
              disabled={saving || !isDirty}
            >
              {saving ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
              ) : saved ? (
                <CheckCircle2 className="w-3.5 h-3.5" />
              ) : (
                <Save className="w-3.5 h-3.5" />
              )}
              {saved ? "已保存" : isDirty ? "保存配置" : "无更改"}
            </Button>
          </div>
        </div>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-auto px-6 py-6">
        <div className="max-w-2xl mx-auto space-y-8">

          {/* 提供商切换 */}
          <div className="space-y-2">
            <label className="text-[11px] text-muted-foreground uppercase tracking-wider flex items-center gap-1.5">
              <Bot className="w-3.5 h-3.5" />
              模型提供商
            </label>
            <div className="grid grid-cols-2 gap-3">
              {PROVIDERS.map((p) => (
                <button
                  key={p.value}
                  onClick={() => patchDraft({ provider: p.value })}
                  className={cn(
                    "text-left rounded-xl border px-4 py-3 transition-all",
                    draft.provider === p.value
                      ? "border-primary bg-primary/10 text-foreground"
                      : "border-border bg-secondary/50 text-muted-foreground hover:border-border/80 hover:text-foreground"
                  )}
                >
                  <p className={cn("text-xs font-semibold", draft.provider === p.value ? "text-primary" : "")}>
                    {p.label}
                  </p>
                  <p className="text-[10px] mt-0.5 opacity-70">{p.desc}</p>
                </button>
              ))}
            </div>
          </div>

          {/* ── Claude 区域 ── */}
          {draft.provider === "claude" && (
            <>
              {/* Claude API Key */}
              <div className="space-y-2">
                <label className="text-[11px] text-muted-foreground uppercase tracking-wider flex items-center gap-1.5">
                  <Key className="w-3.5 h-3.5" />
                  Claude API Key
                </label>
                <Input
                  type="password"
                  value={draft.claudeApiKey}
                  onChange={(e) => patchDraft({ claudeApiKey: e.target.value })}
                  placeholder="sk-ant-..."
                  className="h-9 text-sm bg-secondary border-border"
                />
                <p className="text-[11px] text-muted-foreground/50">
                  {draft.claudeApiKey ? "✓ 已配置，将使用 Claude 审计" : "留空则使用规则引擎"}
                </p>
              </div>

              {/* Claude 模型选择 */}
              <div className="space-y-2">
                <label className="text-[11px] text-muted-foreground uppercase tracking-wider flex items-center gap-1.5">
                  <Bot className="w-3.5 h-3.5" />
                  Claude 模型
                </label>
                <div className="grid grid-cols-3 gap-3">
                  {CLAUDE_MODELS.map((m) => (
                    <button
                      key={m.value}
                      onClick={() => patchDraft({ claudeModel: m.value })}
                      className={cn(
                        "text-left rounded-xl border px-4 py-3 transition-all",
                        draft.claudeModel === m.value
                          ? "border-primary bg-primary/10 text-foreground"
                          : "border-border bg-secondary/50 text-muted-foreground hover:border-border/80 hover:text-foreground"
                      )}
                    >
                      <p className={cn("text-xs font-semibold", draft.claudeModel === m.value ? "text-primary" : "")}>
                        {m.label}
                      </p>
                      <p className="text-[10px] mt-0.5 opacity-70">{m.desc}</p>
                    </button>
                  ))}
                </div>
              </div>

              {/* 安全提示 */}
              <div className="rounded-xl border border-border/40 bg-secondary/30 px-5 py-4 flex gap-3">
                <ShieldCheck className="w-4 h-4 text-primary shrink-0 mt-0.5" />
                <div className="space-y-1">
                  <p className="text-xs font-medium text-foreground">API Key 安全说明</p>
                  <p className="text-[11px] text-muted-foreground leading-relaxed">
                    API Key 仅存储于本机浏览器 <code className="font-mono bg-secondary px-1 rounded">localStorage</code>，不会上传至任何服务器。
                    所有审计请求均由本机后端（:8000）直接调用 Anthropic API。
                  </p>
                </div>
              </div>
            </>
          )}

          {/* ── Ollama 区域 ── */}
          {draft.provider === "ollama" && (
            <>
              {/* Ollama 服务地址 */}
              <div className="space-y-2">
                <label className="text-[11px] text-muted-foreground uppercase tracking-wider flex items-center gap-1.5">
                  <Terminal className="w-3.5 h-3.5" />
                  Ollama 服务地址
                </label>
                <div className="flex gap-2">
                  <Input
                    type="text"
                    value={draft.ollamaBaseUrl}
                    onChange={(e) => patchDraft({ ollamaBaseUrl: e.target.value })}
                    placeholder="http://localhost:11434"
                    className="h-9 text-sm bg-secondary border-border flex-1"
                  />
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-9 px-3 shrink-0 gap-1.5 text-xs"
                    onClick={() => fetchOllamaModels(draft.ollamaBaseUrl)}
                    disabled={ollamaLoading}
                  >
                    {ollamaLoading
                      ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
                      : <RefreshCw className="w-3.5 h-3.5" />
                    }
                    刷新
                  </Button>
                </div>
                <p className="text-[11px] text-muted-foreground/50 flex items-center gap-1">
                  {ollamaOnline === true && <><Wifi className="w-3 h-3 text-green-500" /> Ollama 在线，已发现 {ollamaModels.length} 个模型</>}
                  {ollamaOnline === false && <><WifiOff className="w-3 h-3 text-destructive" /> 无法连接 Ollama，请确认已安装并启动</>}
                  {ollamaOnline === null && "点击刷新检测 Ollama 状态"}
                </p>
              </div>

              {/* 本地模型选择 */}
              <div className="space-y-2">
                <label className="text-[11px] text-muted-foreground uppercase tracking-wider flex items-center gap-1.5">
                  <Bot className="w-3.5 h-3.5" />
                  本地模型
                </label>

                {ollamaLoading && (
                  <div className="flex items-center gap-2 py-4 text-xs text-muted-foreground">
                    <Loader2 className="w-4 h-4 animate-spin" />
                    正在查询本地模型列表...
                  </div>
                )}

                {!ollamaLoading && ollamaModels.length === 0 && (
                  <div className="rounded-xl border border-border/40 bg-secondary/20 px-5 py-4">
                    <p className="text-xs text-muted-foreground">
                      {ollamaOnline === false
                        ? "请先安装并启动 Ollama，然后点击上方「刷新」"
                        : "未检测到已安装模型，请先运行 ollama pull deepseek-r1:32b"}
                    </p>
                    <p className="text-[11px] text-muted-foreground/60 mt-2 font-mono">
                      ollama pull deepseek-r1:32b
                    </p>
                  </div>
                )}

                {!ollamaLoading && ollamaModels.length > 0 && (
                  <div className="grid grid-cols-2 gap-3">
                    {ollamaModels.map((m) => (
                      <button
                        key={m}
                        onClick={() => patchDraft({ ollamaModel: m })}
                        className={cn(
                          "text-left rounded-xl border px-4 py-3 transition-all",
                          draft.ollamaModel === m
                            ? "border-primary bg-primary/10 text-foreground"
                            : "border-border bg-secondary/50 text-muted-foreground hover:border-border/80 hover:text-foreground"
                        )}
                      >
                        <p className={cn("text-xs font-semibold font-mono", draft.ollamaModel === m ? "text-primary" : "")}>
                          {m}
                        </p>
                      </button>
                    ))}
                  </div>
                )}

                {/* 手动输入兜底 */}
                <div className="space-y-1.5 pt-1">
                  <p className="text-[11px] text-muted-foreground/60">或手动输入模型名</p>
                  <Input
                    type="text"
                    value={draft.ollamaModel}
                    onChange={(e) => patchDraft({ ollamaModel: e.target.value })}
                    placeholder="deepseek-r1:32b"
                    className="h-9 text-sm bg-secondary border-border font-mono"
                  />
                </div>
              </div>

              {/* Ollama 说明 */}
              <div className="rounded-xl border border-border/40 bg-secondary/30 px-5 py-4 flex gap-3">
                <ShieldCheck className="w-4 h-4 text-green-500 shrink-0 mt-0.5" />
                <div className="space-y-1">
                  <p className="text-xs font-medium text-foreground">完全本地推理</p>
                  <p className="text-[11px] text-muted-foreground leading-relaxed">
                    所有数据在本机处理，不联网、不计费。推荐使用{" "}
                    <code className="font-mono bg-secondary px-1 rounded">deepseek-r1:32b</code>（M4 Max 36GB 内存可流畅运行，约 20-30 tokens/s）。
                  </p>
                </div>
              </div>
            </>
          )}

          {/* 剪映草稿目录（始终显示） */}
          <div className="space-y-2">
            <label className="text-[11px] text-muted-foreground uppercase tracking-wider flex items-center gap-1.5">
              <FolderOpen className="w-3.5 h-3.5" />
              剪映草稿目录
            </label>
            <Input
              type="text"
              value={draft.jianyingDir}
              onChange={(e) => patchDraft({ jianyingDir: e.target.value })}
              placeholder="/Users/xxx/Movies/JianyingPro/User Data/Projects"
              className="h-9 text-sm bg-secondary border-border"
            />
            <p className="text-[11px] text-muted-foreground/50">
              {draft.jianyingDir ? "✓ 已配置导出目录" : "留空则导出到视频同级目录"}
            </p>
          </div>

          {/* 系统状态 */}
          <div className="space-y-3">
            <h3 className="text-[11px] font-medium text-muted-foreground uppercase tracking-wider">
              系统状态
            </h3>
            <div className="space-y-2">
              {STATUS_ITEMS.map((item) => {
                const Icon = item.icon;
                return (
                  <div key={item.label} className="flex items-center justify-between px-4 py-2.5 rounded-lg bg-secondary/50 border border-border/30">
                    <div className="flex items-center gap-2">
                      <Icon className="w-3.5 h-3.5 text-muted-foreground" />
                      <span className="text-xs text-foreground">{item.label}</span>
                    </div>
                    <div className="flex items-center gap-2">
                      <div className="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse" />
                      {item.port && (
                        <span className="font-mono text-muted-foreground text-[10px]">:{item.port}</span>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

        </div>
      </div>
    </div>
  );
}
