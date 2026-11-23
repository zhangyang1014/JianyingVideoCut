import { useEffect, useMemo, useState, type ReactNode } from "react";
import { AlertTriangle, CheckCircle2, Loader2, RefreshCw, Server, Sparkles } from "lucide-react";

import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  previewModelRun,
  type ModelRunPreview,
  type ModelRunPreviewRequest,
} from "@/lib/api";


interface Props {
  open: boolean;
  taskId: string;
  request: ModelRunPreviewRequest | null;
  onOpenChange: (open: boolean) => void;
  onConfirm: (preview: ModelRunPreview) => Promise<void> | void;
  confirmLabel?: string;
  children?: ReactNode;
}


function formatParameter(value: unknown): string {
  if (value === null || value === undefined || value === "") return "自动";
  if (typeof value === "boolean") return value ? "开启" : "关闭";
  if (typeof value === "object") return JSON.stringify(value, null, 2);
  return String(value);
}


function providerLabel(provider: string): string {
  if (provider === "ollama") return "本地 Ollama";
  if (provider === "local_process") return "本机进程";
  if (provider === "openai_compatible") return "OpenAI-compatible";
  return provider;
}


export function AiRunConfirmationDialog({
  open,
  taskId,
  request,
  onOpenChange,
  onConfirm,
  confirmLabel = "确认执行",
  children,
}: Props) {
  const [preview, setPreview] = useState<ModelRunPreview | null>(null);
  const [loading, setLoading] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState("");
  const requestKey = useMemo(() => JSON.stringify(request), [request]);

  const loadPreview = async () => {
    if (!request) return;
    setLoading(true);
    setError("");
    setPreview(null);
    try {
      setPreview(await previewModelRun(taskId, request));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "加载执行预览失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!open || !request) return;
    void loadPreview();
    // requestKey 只在执行意图实际变化时更新，避免对象引用导致重复请求。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, taskId, requestKey]);

  const handleConfirm = async () => {
    if (!preview) return;
    setConfirming(true);
    try {
      await onConfirm(preview);
      onOpenChange(false);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "启动失败，请重新预览");
    } finally {
      setConfirming(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(value) => !confirming && onOpenChange(value)}>
      <DialogContent className="max-h-[88vh] max-w-3xl overflow-hidden border-border bg-card">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-foreground">
            <Sparkles className="h-4 w-4 text-primary" />
            {preview?.title || "确认重新执行模型"}
          </DialogTitle>
          <DialogDescription className="text-xs text-muted-foreground">
            请确认本次实际使用的服务、模型、参数和提示词。确认前不会启动任务。
          </DialogDescription>
        </DialogHeader>

        <div className="max-h-[68vh] space-y-3 overflow-y-auto pr-1">
          {children}

          {loading && (
            <div className="flex h-40 items-center justify-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              正在解析实际执行配置…
            </div>
          )}

          {error && !loading && (
            <div className="rounded-lg border border-destructive/30 bg-destructive/10 p-3">
              <p className="text-xs text-destructive">{error}</p>
              <Button
                type="button"
                size="sm"
                variant="outline"
                className="mt-2 h-7 gap-1 text-xs"
                onClick={() => void loadPreview()}
              >
                <RefreshCw className="h-3 w-3" />
                重新加载
              </Button>
            </div>
          )}

          {preview && !loading && (
            <>
              <div className="rounded-lg border border-border bg-background/40 px-3 py-2">
                <div className="flex items-center gap-2 text-xs text-foreground">
                  <Server className="h-3.5 w-3.5 text-primary" />
                  后端接口
                </div>
                <p className="mt-1 break-all font-mono text-[11px] text-muted-foreground">
                  {preview.api_path}
                </p>
              </div>

              {preview.warnings.map((warning) => (
                <div
                  key={warning}
                  className="flex gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-300"
                >
                  <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                  {warning}
                </div>
              ))}

              {preview.affected_artifacts.length > 0 && (
                <div className="rounded-lg border border-amber-500/20 bg-amber-500/5 px-3 py-2">
                  <p className="text-xs font-medium text-amber-300">本次会更新或失效</p>
                  <p className="mt-1 text-[11px] text-amber-200/75">
                    {preview.affected_artifacts.join(" · ")}
                  </p>
                </div>
              )}

              <div className="space-y-3">
                {preview.stages.map((stage, stageIndex) => (
                  <section key={stage.id} className="rounded-lg border border-border bg-secondary/10 p-3">
                    <div className="flex items-center gap-2">
                      <span className="flex h-5 w-5 items-center justify-center rounded-full bg-primary/15 font-mono text-[10px] text-primary">
                        {stageIndex + 1}
                      </span>
                      <h3 className="text-sm font-medium text-foreground">{stage.label}</h3>
                      <span className="ml-auto rounded bg-secondary px-1.5 py-0.5 text-[10px] text-muted-foreground">
                        {stage.kind === "llm" ? "LLM" : stage.kind === "tts" ? "TTS" : "本地模型"}
                      </span>
                    </div>

                    <div className="mt-2 space-y-2">
                      {stage.routes.map((route, routeIndex) => (
                        <div
                          key={`${route.model}-${routeIndex}`}
                          className="rounded-md border border-border/60 bg-background/50 px-2.5 py-2"
                        >
                          <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
                            <span className="font-medium text-foreground">{route.model}</span>
                            <span className="text-muted-foreground">· {providerLabel(route.provider)}</span>
                            <span className="rounded bg-secondary px-1 py-0.5 text-[9px] text-muted-foreground">
                              {route.tier}
                            </span>
                            <span
                              className={route.credentials_configured ? "text-green-400" : "text-amber-400"}
                            >
                              {route.credentials_configured ? "凭据已配置" : "凭据未配置"}
                            </span>
                          </div>
                          {route.request_url ? (
                            <p className="mt-1 break-all font-mono text-[10px] text-muted-foreground">
                              {route.request_url}
                              {route.port ? ` · 端口 ${route.port}` : ""}
                            </p>
                          ) : (
                            <p className="mt-1 text-[10px] text-muted-foreground">本机直接运行 · 无服务端口</p>
                          )}
                        </div>
                      ))}
                    </div>

                    {Object.keys(stage.parameters).length > 0 && (
                      <div className="mt-2 grid gap-1.5 sm:grid-cols-2">
                        {Object.entries(stage.parameters).map(([key, value]) => (
                          <div key={key} className="flex gap-2 text-[10px]">
                            <span className="shrink-0 text-muted-foreground/60">{key}</span>
                            <span className="min-w-0 break-all text-muted-foreground">
                              {formatParameter(value)}
                            </span>
                          </div>
                        ))}
                      </div>
                    )}

                    {stage.prompt_note && (
                      <p className="mt-2 text-[10px] text-muted-foreground/70">{stage.prompt_note}</p>
                    )}

                    {stage.prompts.length > 0 && (
                      <Accordion type="multiple" className="mt-2 w-full">
                        {stage.prompts.map((prompt, promptIndex) => (
                          <AccordionItem
                            key={`${prompt.id}-${promptIndex}`}
                            value={`${stage.id}-${prompt.id}-${promptIndex}`}
                            className="border-border/50"
                          >
                            <AccordionTrigger className="py-2 text-xs hover:no-underline">
                              <span className="text-left">
                                <span className="block text-foreground">{prompt.label}</span>
                                <span className="block font-mono text-[9px] text-muted-foreground/60">
                                  {prompt.id} · {prompt.active_version || prompt.source}
                                </span>
                              </span>
                            </AccordionTrigger>
                            <AccordionContent className="space-y-2">
                              {prompt.note && (
                                <p className="text-[10px] text-muted-foreground">{prompt.note}</p>
                              )}
                              <div>
                                <p className="mb-1 text-[10px] font-medium text-muted-foreground">
                                  System Prompt
                                </p>
                                <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded-md border border-border bg-background p-2 text-[10px] leading-relaxed text-foreground/80">
                                  {prompt.system_prompt}
                                </pre>
                              </div>
                              {prompt.user_prompt && (
                                <div>
                                  <p className="mb-1 text-[10px] font-medium text-muted-foreground">
                                    User Prompt / 本次输入
                                  </p>
                                  <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded-md border border-border bg-background p-2 text-[10px] leading-relaxed text-foreground/80">
                                    {prompt.user_prompt}
                                  </pre>
                                </div>
                              )}
                            </AccordionContent>
                          </AccordionItem>
                        ))}
                      </Accordion>
                    )}
                  </section>
                ))}
              </div>

              <div className="flex items-center gap-1.5 text-[10px] text-green-400/80">
                <CheckCircle2 className="h-3 w-3" />
                API Key 不会显示；配置或任务变化后，本次确认会自动失效。
              </div>
            </>
          )}
        </div>

        <div className="flex justify-end gap-2 border-t border-border pt-3">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => onOpenChange(false)}
            disabled={confirming}
          >
            取消
          </Button>
          <Button
            type="button"
            size="sm"
            onClick={() => void handleConfirm()}
            disabled={!preview || loading || confirming}
          >
            {confirming && <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />}
            {confirmLabel}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
