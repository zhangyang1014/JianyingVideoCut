import { AlertCircle, Check, Loader2, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";
import type { SubtitleOptimizationPreview } from "@/lib/api";

export function SubtitleOptimizeDialog({
  open,
  onClose,
  preview,
  loading,
  applying,
  selectedIds,
  onToggle,
  onSelectAll,
  onApply,
  onRefresh,
  onRunFullAnalyze,
}: {
  open: boolean;
  onClose: () => void;
  preview: SubtitleOptimizationPreview | null;
  loading: boolean;
  applying: boolean;
  selectedIds: Set<string>;
  onToggle: (segmentId: string) => void;
  onSelectAll: (checked: boolean) => void;
  onApply: () => void;
  onRefresh: () => void;
  onRunFullAnalyze: () => void;
}) {
  const changes = preview?.changes ?? [];
  const selectedCount = changes.filter((change) => selectedIds.has(change.segment_id)).length;
  const hasChanges = changes.length > 0;

  return (
    <Dialog open={open} onOpenChange={(value) => !value && onClose()}>
      <DialogContent className="bg-card border-border max-w-3xl">
        <DialogHeader>
          <DialogTitle className="text-foreground flex items-center gap-2">
            <Sparkles className="h-4 w-4 text-primary" />
            分析优化字幕
          </DialogTitle>
          <DialogDescription className="text-xs text-muted-foreground">
            只优化字幕文字，不修改时间轴、片段 ID、AI 审计决定或精彩集锦分组。
          </DialogDescription>
        </DialogHeader>

        <div className="mt-2 rounded-lg border border-primary/20 bg-primary/5 px-3 py-2 text-[11px] text-primary">
          将保留 {preview?.preserved.audit_history ?? 0} 次 AI 审计记录、{preview?.preserved.highlight_reels ?? 0} 个精彩集锦、
          {preview?.preserved.golden_quotes ?? 0} 个金句和 {preview?.preserved.export_jobs ?? 0} 条导出记录。
        </div>

        {loading ? (
          <div className="flex h-48 items-center justify-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            正在分析当前字幕和词典…
          </div>
        ) : !hasChanges ? (
          <div className="flex h-48 flex-col items-center justify-center gap-2 text-center">
            <Check className="h-8 w-8 text-green-400" />
            <p className="text-sm text-foreground">当前字幕无需优化</p>
            <p className="text-xs text-muted-foreground">词典没有发现可自动修正的文字。</p>
          </div>
        ) : (
          <>
            <div className="flex items-center justify-between text-xs">
              <label className="flex items-center gap-2 text-muted-foreground">
                <input
                  type="checkbox"
                  checked={selectedCount === changes.length}
                  onChange={(event) => onSelectAll(event.target.checked)}
                />
                选择全部 {changes.length} 处修正
              </label>
              <span className="text-muted-foreground">将应用 {selectedCount} 处</span>
            </div>
            <div className="mt-2 max-h-[420px] space-y-2 overflow-y-auto pr-1">
              {changes.map((change) => {
                const checked = selectedIds.has(change.segment_id);
                return (
                  <label
                    key={change.segment_id}
                    className={cn(
                      "block rounded-lg border p-3 text-xs transition-colors",
                      checked
                        ? "border-primary/40 bg-primary/5"
                        : "border-border bg-secondary/20 opacity-70"
                    )}
                  >
                    <div className="mb-2 flex items-center gap-2">
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => onToggle(change.segment_id)}
                      />
                      <span className="font-mono text-muted-foreground">
                        {change.start.toFixed(2)}s → {change.end.toFixed(2)}s
                      </span>
                      <span className="ml-auto text-[10px] text-primary">
                        {change.reasons.join(" · ")}
                      </span>
                    </div>
                    <div className="grid gap-2 md:grid-cols-2">
                      <div className="rounded bg-background/60 p-2 text-muted-foreground">
                        {change.before}
                      </div>
                      <div className="rounded bg-green-500/10 p-2 text-green-100">
                        {change.after}
                      </div>
                    </div>
                  </label>
                );
              })}
            </div>
          </>
        )}

        <div className="mt-3 flex items-center gap-2 border-t border-border pt-3">
          <div className="flex items-center gap-1 text-[11px] text-muted-foreground">
            <AlertCircle className="h-3.5 w-3.5" />
            需要重新分段时请使用高级整理。
          </div>
          <Button variant="ghost" size="sm" className="ml-auto h-8 text-xs" onClick={onRunFullAnalyze}>
            重新整理全文/分段
          </Button>
          <Button variant="outline" size="sm" className="h-8 text-xs" onClick={onRefresh} disabled={loading || applying}>
            重新预览
          </Button>
          <Button variant="outline" size="sm" className="h-8 text-xs" onClick={onClose} disabled={applying}>
            取消
          </Button>
          <Button
            size="sm"
            className="h-8 text-xs bg-primary text-primary-foreground hover:bg-primary/90"
            onClick={onApply}
            disabled={loading || applying || selectedCount === 0}
          >
            {applying && <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />}
            应用 {selectedCount} 处优化
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
