import { useState, useEffect } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import {
  Accordion,
  AccordionItem,
  AccordionTrigger,
  AccordionContent,
} from "@/components/ui/accordion";
import {
  BookOpen, Check, CheckCircle2, Eye, EyeOff, Loader2, MessageSquare, Sparkles,
} from "lucide-react";
import {
  getCorrections, submitPromptFeedback, adoptPromptFeedback, adoptStyleUpdate,
  type CorrectionsResult, type PromptFeedbackResult, type Segment,
} from "@/lib/api";

// ============================================================
// Prompt Feedback Dialog (提示词反馈优化)
// ============================================================
export function PromptFeedbackDialog({
  open,
  onClose,
  taskId,
  taskType,
  openaiApiKey,
  openaiModel,
  segments,
}: {
  open: boolean;
  onClose: () => void;
  taskId: string;
  taskType: string;
  openaiApiKey: string;
  openaiModel?: string;
  segments?: Segment[];
  }) {
  const [corrections, setCorrections] = useState<CorrectionsResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<PromptFeedbackResult | null>(null);
  const [userNotes, setUserNotes] = useState("");
  const [adopting, setAdopting] = useState(false);
  const [adopted, setAdopted] = useState(false);

  useEffect(() => {
    if (!open) return;
    setLoading(true);
    setResult(null);
    setAdopted(false);
    getCorrections(taskId)
      .then(setCorrections)
      .catch(() => toast.error("获取修正统计失败"))
      .finally(() => setLoading(false));
  }, [open, taskId]);

  const handleSubmit = async () => {
    setSubmitting(true);
    try {
      const res = await submitPromptFeedback(taskId, {
        openai_api_key: openaiApiKey || undefined,
        openai_model: openaiModel,
        user_notes: userNotes,
      });
      setResult(res);
      toast.success("OpenAI 已返回改进建议");
    } catch (e: any) {
      toast.error(e.message || "提交失败");
    } finally {
      setSubmitting(false);
    }
  };

  const handleAdopt = async () => {
    if (!result) return;
    const hasPromptChanges = (result.prompt_changes?.changes.length ?? 0) > 0;
    const hasStyleUpdate = !!result.style_update && Object.keys(result.style_update).length > 0;
    if (!hasPromptChanges && !hasStyleUpdate) return;
    setAdopting(true);
    try {
      const promptPromise = hasPromptChanges
        ? adoptPromptFeedback(taskId, result.prompt_changes!)
        : Promise.resolve(null);

      const stylePromise = hasStyleUpdate
        ? adoptStyleUpdate(taskId, result.style_update!).catch(() => {})
        : Promise.resolve();

      await Promise.all([promptPromise, stylePromise]);
      setAdopted(true);
      toast.success(hasPromptChanges && hasStyleUpdate
        ? "规则已写入新提示词版本，个人风格档案已更新"
        : hasPromptChanges
          ? "规则已写入新提示词版本"
          : "个人风格档案已更新");
    } catch (e: any) {
      toast.error(e.message || "写入失败");
    } finally {
      setAdopting(false);
    }
  };

  const actionLabel = (a: string) =>
    ({ keep: "保留", delete: "删除", subtitle_fix: "字幕去重", text_fix: "纠错", merge_next: "合并" }[a] || a);

  const changeTypeLabel = (type?: string) =>
    ({ modify: "修改", add: "新增" }[type || ""] || type || "更新");

  const promptChangeCount = result?.prompt_changes?.changes.length ?? 0;
  const styleUpdateCount = result?.style_update ? Object.keys(result.style_update).length : 0;
  const canAdopt = promptChangeCount > 0 || styleUpdateCount > 0;
  const adoptLabel = adopted
    ? "已写入"
    : promptChangeCount > 0 && styleUpdateCount > 0
      ? "采纳规则 + 更新风格档案"
      : promptChangeCount > 0
        ? "采纳规则更新"
        : "更新风格档案";

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="bg-card border-border max-w-2xl max-h-[85vh] overflow-hidden flex flex-col">
        <DialogHeader>
          <DialogTitle className="text-foreground flex items-center gap-2">
            <Sparkles className="w-4 h-4 text-primary" />
            提示词反馈优化
          </DialogTitle>
          <DialogDescription className="text-muted-foreground text-xs">
            只将你主动调整过的片段提交给 OpenAI 分析，生成提示词规则改进建议
          </DialogDescription>
        </DialogHeader>

        <div className="flex-1 overflow-y-auto space-y-4 pr-1">
          {loading ? (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="w-5 h-5 animate-spin text-primary" />
            </div>
          ) : corrections ? (
            <>
              {/* 汇总面板 */}
              <div className="grid grid-cols-3 gap-2">
                <div className="rounded-lg border border-blue-500/30 bg-blue-500/10 p-3 text-center">
                  <div className="text-lg font-bold font-mono text-blue-300">
                    {corrections.delete_to_keep.length}
                  </div>
                  <div className="text-[10px] text-blue-400 mt-0.5">
                    AI 误删 → 你恢复
                  </div>
                </div>
                <div className="rounded-lg border border-orange-500/30 bg-orange-500/10 p-3 text-center">
                  <div className="text-lg font-bold font-mono text-orange-300">
                    {corrections.keep_to_delete.length}
                  </div>
                  <div className="text-[10px] text-orange-400 mt-0.5">
                    AI 漏删 → 你删除
                  </div>
                </div>
                <div className="rounded-lg border border-purple-500/30 bg-purple-500/10 p-3 text-center">
                  <div className="text-lg font-bold font-mono text-purple-300">
                    {corrections.other_changes.length}
                  </div>
                  <div className="text-[10px] text-purple-400 mt-0.5">
                    其他主动调整
                  </div>
                </div>
              </div>

              {corrections.total_modified === 0 ? (
                <div className="text-center py-6 text-muted-foreground text-sm">
                  没有发现修改，无需优化提示词
                </div>
              ) : (
                <>
                  {/* 修改明细（可折叠） */}
                  <Accordion type="single" collapsible className="w-full">
                    {corrections.delete_to_keep.length > 0 && (
                      <AccordionItem value="d2k" className="border-border/40">
                        <AccordionTrigger className="text-sm py-2 hover:no-underline">
                          <span className="flex items-center gap-1.5 text-blue-300">
                            <Eye className="w-3.5 h-3.5" />
                            你恢复的 {corrections.delete_to_keep.length} 个片段
                          </span>
                        </AccordionTrigger>
                        <AccordionContent>
                          <div className="space-y-2 pl-1">
                            {corrections.delete_to_keep.map((c) => {
                              const userReason = segments?.find((s) => s.id === c.id)?.reason;
                              return (
                                <div key={c.id} className="text-[11px] bg-blue-500/5 border border-blue-500/15 rounded-md px-2.5 py-1.5">
                                  <div className="text-foreground/80 leading-snug">"{c.text}"</div>
                                  <div className="flex items-center gap-2 mt-1 text-[10px] text-muted-foreground flex-wrap">
                                    <span className="text-red-400">AI: {actionLabel(c.claude_action)}</span>
                                    <span>→</span>
                                    <span className="text-blue-400">你: {actionLabel(c.user_action)}</span>
                                    {c.claude_reason && (
                                      <span className="text-muted-foreground/50 ml-auto truncate max-w-[200px]" title={c.claude_reason}>
                                        ({c.claude_reason})
                                      </span>
                                    )}
                                  </div>
                                  {userReason && userReason !== "手动恢复" && (
                                    <div className="mt-1">
                                      <span className="text-[10px] px-1.5 py-0.5 rounded bg-green-900/30 text-green-300">
                                        你的原因：{userReason}
                                      </span>
                                    </div>
                                  )}
                                </div>
                              );
                            })}
                          </div>
                        </AccordionContent>
                      </AccordionItem>
                    )}

                    {corrections.keep_to_delete.length > 0 && (
                      <AccordionItem value="k2d" className="border-border/40">
                        <AccordionTrigger className="text-sm py-2 hover:no-underline">
                          <span className="flex items-center gap-1.5 text-orange-300">
                            <EyeOff className="w-3.5 h-3.5" />
                            你删除的 {corrections.keep_to_delete.length} 个片段
                          </span>
                        </AccordionTrigger>
                        <AccordionContent>
                          <div className="space-y-2 pl-1">
                            {corrections.keep_to_delete.map((c) => {
                              const userReason = segments?.find((s) => s.id === c.id)?.reason;
                              return (
                                <div key={c.id} className="text-[11px] bg-orange-500/5 border border-orange-500/15 rounded-md px-2.5 py-1.5">
                                  <div className="text-foreground/80 leading-snug">"{c.text}"</div>
                                  <div className="flex items-center gap-2 mt-1 text-[10px] text-muted-foreground flex-wrap">
                                    <span className="text-green-400">AI: {actionLabel(c.claude_action)}</span>
                                    <span>→</span>
                                    <span className="text-orange-400">你: {actionLabel(c.user_action)}</span>
                                  </div>
                                  {userReason && (
                                    <div className="mt-1">
                                      <span className="text-[10px] px-1.5 py-0.5 rounded bg-orange-900/30 text-orange-300">
                                        你的原因：{userReason}
                                      </span>
                                    </div>
                                  )}
                                </div>
                              );
                            })}
                          </div>
                        </AccordionContent>
                      </AccordionItem>
                    )}

                    {corrections.other_changes.length > 0 && (
                      <AccordionItem value="other" className="border-border/40">
                        <AccordionTrigger className="text-sm py-2 hover:no-underline">
                          <span className="flex items-center gap-1.5 text-purple-300">
                            <Sparkles className="w-3.5 h-3.5" />
                            其他主动调整的 {corrections.other_changes.length} 个片段
                          </span>
                        </AccordionTrigger>
                        <AccordionContent>
                          <div className="space-y-2 pl-1">
                            {corrections.other_changes.map((c) => {
                              const userReason = segments?.find((s) => s.id === c.id)?.reason;
                              return (
                                <div key={c.id} className="text-[11px] bg-purple-500/5 border border-purple-500/15 rounded-md px-2.5 py-1.5">
                                  <div className="text-foreground/80 leading-snug">"{c.text}"</div>
                                  <div className="flex items-center gap-2 mt-1 text-[10px] text-muted-foreground flex-wrap">
                                    <span className="text-muted-foreground">AI: {actionLabel(c.claude_action)}</span>
                                    <span>→</span>
                                    <span className="text-purple-400">你: {actionLabel(c.user_action)}</span>
                                  </div>
                                  {userReason && (
                                    <div className="mt-1">
                                      <span className="text-[10px] px-1.5 py-0.5 rounded bg-purple-900/30 text-purple-300">
                                        你的原因：{userReason}
                                      </span>
                                    </div>
                                  )}
                                </div>
                              );
                            })}
                          </div>
                        </AccordionContent>
                      </AccordionItem>
                    )}
                  </Accordion>

                  {/* 用户备注 */}
                  <div className="space-y-1.5">
                    <label className="text-xs text-muted-foreground flex items-center gap-1">
                      <MessageSquare className="w-3 h-3" />
                      备注说明（可选，帮助 AI 理解你的修改意图）
                    </label>
                    <textarea
                      value={userNotes}
                      onChange={(e) => setUserNotes(e.target.value)}
                      placeholder="例如：这个场景需要保留更多嘉宾的情绪表达..."
                      className="w-full bg-background border border-border rounded-lg px-3 py-2 text-sm text-foreground resize-none focus:outline-none focus:ring-1 focus:ring-primary placeholder:text-muted-foreground/40"
                      rows={2}
                    />
                  </div>

                  {/* 提交按钮 */}
                  {!result && (
                    <Button
                      className="w-full bg-primary text-primary-foreground hover:bg-primary/90 gap-2"
                      onClick={handleSubmit}
                      disabled={submitting || !openaiApiKey}
                    >
                      {submitting ? (
                        <Loader2 className="w-4 h-4 animate-spin" />
                      ) : (
                        <Sparkles className="w-4 h-4" />
                      )}
                      {submitting ? "OpenAI 分析中..." : `提交给 OpenAI 学习这 ${corrections.total_modified} 处调整`}
                    </Button>
                  )}

                  {!openaiApiKey && !result && (
                    <p className="text-[10px] text-amber-400 text-center">
                      需要在设置中配置 API Key 才能提交
                    </p>
                  )}

                  {/* OpenAI 返回的建议 */}
                  {result && (
                    <div className="space-y-3">
                      <div className="rounded-lg border border-primary/30 bg-primary/5 px-3 py-2.5 space-y-2">
                        <div className="flex items-center justify-between gap-2">
                          <div className="flex items-center gap-1.5 text-xs text-primary font-medium">
                            <BookOpen className="w-3.5 h-3.5" />
                            本次更新点
                          </div>
                          {promptChangeCount > 0 && (
                            <span className="text-[10px] text-primary/80 bg-primary/10 rounded-full px-2 py-0.5">
                              可写入 {promptChangeCount} 条规则
                            </span>
                          )}
                        </div>

                        {result.update_points && result.update_points.length > 0 ? (
                          <div className="space-y-1.5">
                            {result.update_points.map((point, index) => (
                              <div key={`${point.target_rule || "point"}-${index}`} className="flex gap-2 text-xs text-foreground/85 leading-relaxed">
                                <span className="mt-0.5 shrink-0 text-primary font-mono">{index + 1}.</span>
                                <div className="min-w-0">
                                  <div>{point.summary}</div>
                                  {(point.target_rule || point.change_type) && (
                                    <div className="mt-0.5 text-[10px] text-muted-foreground">
                                      {point.target_rule && <span>规则 {point.target_rule}</span>}
                                      {point.target_rule && point.change_type && <span> · </span>}
                                      {point.change_type && <span>{changeTypeLabel(point.change_type)}</span>}
                                    </div>
                                  )}
                                </div>
                              </div>
                            ))}
                          </div>
                        ) : (
                          <p className="text-xs text-muted-foreground">
                            模型未返回结构化更新点，请展开完整建议查看变化。
                          </p>
                        )}
                      </div>

                      <Accordion type="single" collapsible className="w-full">
                        <AccordionItem value="full-suggestion" className="border-border/40">
                          <AccordionTrigger className="text-sm py-2 hover:no-underline">
                            <span className="flex items-center gap-1.5 text-primary">
                              <BookOpen className="w-3.5 h-3.5" />
                              查看完整 OpenAI 改进建议
                            </span>
                          </AccordionTrigger>
                          <AccordionContent>
                            <div className="bg-background border border-border rounded-lg px-4 py-3 text-sm text-foreground/90 leading-relaxed whitespace-pre-wrap max-h-64 overflow-y-auto">
                              {result.suggestion}
                            </div>
                          </AccordionContent>
                        </AccordionItem>

                        {result.style_update && Object.keys(result.style_update).length > 0 && (
                          <AccordionItem value="style-update" className="border-border/40">
                            <AccordionTrigger className="text-sm py-2 hover:no-underline">
                              <span className="flex items-center gap-1.5 text-amber-400">
                                <Sparkles className="w-3.5 h-3.5" />
                                查看学到的个人偏好
                              </span>
                            </AccordionTrigger>
                            <AccordionContent>
                              <div className="bg-amber-500/5 border border-amber-500/20 rounded-lg px-3 py-2 space-y-1">
                                {Object.entries(result.style_update).map(([k, v]) => (
                                  <div key={k} className="text-[11px] text-foreground/70">
                                    <span className="text-amber-300/80">{k}：</span>{v}
                                  </div>
                                ))}
                              </div>
                            </AccordionContent>
                          </AccordionItem>
                        )}
                      </Accordion>

                      {!canAdopt && (
                        <p className="text-[10px] text-amber-400 text-center">
                          本次没有可自动写入的结构化变更，可展开完整建议手动判断。
                        </p>
                      )}

                      <div className="flex items-center gap-2">
                        <Button
                          variant="outline"
                          size="sm"
                          className="flex-1 border-primary/50 text-primary hover:bg-primary/10 gap-1.5"
                          onClick={handleAdopt}
                          disabled={adopting || adopted || !canAdopt}
                        >
                          {adopted ? (
                            <CheckCircle2 className="w-3.5 h-3.5" />
                          ) : adopting ? (
                            <Loader2 className="w-3.5 h-3.5 animate-spin" />
                          ) : (
                            <Check className="w-3.5 h-3.5" />
                          )}
                          {adoptLabel}
                        </Button>
                        <Button
                          variant="outline"
                          size="sm"
                          className="border-border text-muted-foreground"
                          onClick={onClose}
                        >
                          关闭
                        </Button>
                      </div>
                    </div>
                  )}
                </>
              )}
            </>
          ) : null}
        </div>
      </DialogContent>
    </Dialog>
  );
}
