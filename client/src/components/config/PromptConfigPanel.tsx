import { useCallback, useEffect, useState } from "react";
import { CheckCircle2, FileText, Loader2, Save } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import {
  getConfigPrompt,
  listConfigPrompts,
  updateConfigPrompt,
  type ConfigPromptData,
  type ConfigPromptSummary,
} from "@/lib/api";

interface PromptConfigPanelProps {
  feature: string;
  emptyTitle?: string;
  emptyDescription?: string;
  onDirtyChange?: (dirty: boolean) => void;
  sectionId?: string;
}

export default function PromptConfigPanel({
  feature,
  emptyTitle = "此功能没有聊天 Prompt",
  emptyDescription = "该模块不通过 system/user Prompt 调用聊天模型。",
  onDirtyChange,
  sectionId,
}: PromptConfigPanelProps) {
  const [prompts, setPrompts] = useState<ConfigPromptSummary[]>([]);
  const [activeId, setActiveId] = useState("");
  const [activePrompt, setActivePrompt] = useState<ConfigPromptData | null>(null);
  const [draft, setDraft] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  const dirty = Boolean(activePrompt && draft !== activePrompt.content);

  useEffect(() => {
    onDirtyChange?.(dirty);
    return () => onDirtyChange?.(false);
  }, [dirty, onDirtyChange]);

  const loadPrompt = useCallback(async (promptId: string) => {
    setLoading(true);
    try {
      const prompt = await getConfigPrompt(promptId);
      setActivePrompt(prompt);
      setDraft(prompt.content);
      setActiveId(promptId);
      setSaved(false);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "加载 Prompt 失败");
      setActivePrompt(null);
      setDraft("");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setPrompts([]);
    setActiveId("");
    setActivePrompt(null);
    setDraft("");

    void listConfigPrompts(feature)
      .then(async (items) => {
        if (cancelled) return;
        setPrompts(items);
        if (items[0]) await loadPrompt(items[0].id);
      })
      .catch((error) => {
        if (!cancelled) toast.error(error instanceof Error ? error.message : "加载 Prompt 列表失败");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [feature, loadPrompt]);

  const selectPrompt = (promptId: string) => {
    if (promptId === activeId) return;
    if (dirty && !window.confirm("当前 Prompt 有未保存的更改，切换后将丢失。继续？")) return;
    void loadPrompt(promptId);
  };

  const savePrompt = async () => {
    if (!activePrompt || !dirty) return;
    setSaving(true);
    try {
      const updated = await updateConfigPrompt(activePrompt.id, draft);
      setActivePrompt(updated);
      setDraft(updated.content);
      setSaved(true);
      toast.success("当前生效 Prompt 已保存");
      window.setTimeout(() => setSaved(false), 2000);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "保存 Prompt 失败");
    } finally {
      setSaving(false);
    }
  };

  if (!loading && prompts.length === 0) {
    return (
      <section id={sectionId} className="scroll-mt-4 rounded-xl border border-border/50 bg-secondary/20 p-4">
        <div className="flex gap-3">
          <FileText className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
          <div>
            <h2 className="text-sm font-semibold text-foreground">当前生效 Prompt</h2>
            <p className="mt-2 text-xs font-medium text-foreground/80">{emptyTitle}</p>
            <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">{emptyDescription}</p>
          </div>
        </div>
      </section>
    );
  }

  return (
    <section id={sectionId} className="scroll-mt-4 space-y-4 rounded-xl border border-border/50 bg-secondary/20 p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="flex items-center gap-2 text-sm font-semibold text-foreground">
            <FileText className="h-4 w-4 text-primary" />
            当前生效 Prompt
          </h2>
          <p className="mt-1 text-[11px] text-muted-foreground">
            这里显示的 system prompt 与运行时模型调用使用同一来源。
          </p>
        </div>
        <Button
          size="sm"
          className={cn("h-8 gap-1.5 text-xs", !dirty && "bg-primary/60")}
          onClick={savePrompt}
          disabled={!dirty || saving || !activePrompt?.editable}
        >
          {saving ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : saved ? (
            <CheckCircle2 className="h-3.5 w-3.5" />
          ) : (
            <Save className="h-3.5 w-3.5" />
          )}
          {saved ? "已保存" : dirty ? "保存 Prompt" : "无更改"}
        </Button>
      </div>

      {prompts.length > 1 && (
        <div className="flex flex-wrap gap-2">
          {prompts.map((prompt) => (
            <button
              key={prompt.id}
              type="button"
              onClick={() => selectPrompt(prompt.id)}
              className={cn(
                "rounded-lg border px-3 py-1.5 text-xs transition-colors",
                activeId === prompt.id
                  ? "border-primary bg-primary/10 text-primary"
                  : "border-border text-muted-foreground hover:text-foreground",
              )}
            >
              {prompt.label}
            </button>
          ))}
        </div>
      )}

      {loading ? (
        <div className="flex h-40 items-center justify-center gap-2 text-xs text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          加载当前 Prompt…
        </div>
      ) : activePrompt ? (
        <div className="space-y-3">
          <div className="rounded-lg border border-border/40 bg-background/40 px-3 py-2">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-muted-foreground">
              <span className="font-medium text-foreground/80">{activePrompt.label}</span>
              <code className="font-mono">{activePrompt.source}</code>
              {activePrompt.active_version && (
                <span className="rounded bg-primary/10 px-1.5 py-0.5 text-primary">
                  生效版本：{activePrompt.active_version}
                </span>
              )}
            </div>
            <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
              {activePrompt.description}
            </p>
          </div>
          <textarea
            value={draft}
            onChange={(event) => setDraft(event.currentTarget.value)}
            className="min-h-[360px] w-full resize-y rounded-xl border border-border bg-background p-4 font-mono text-xs leading-relaxed text-foreground focus:outline-none focus:ring-1 focus:ring-primary/50"
            spellCheck={false}
            readOnly={!activePrompt.editable}
            aria-label={`${activePrompt.label} Prompt`}
          />
          <p className="text-[10px] text-muted-foreground/70">
            动态字幕、片段数据和 JSON 输出载荷由运行时代码安全注入，不在此编辑。
          </p>
        </div>
      ) : null}
    </section>
  );
}
