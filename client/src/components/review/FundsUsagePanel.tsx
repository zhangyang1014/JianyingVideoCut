import { useCallback, useEffect, useRef, useState } from "react";
import { Coins, Info, Loader2, RefreshCw } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card";
import {
  fetchTaskUsage,
  fetchTaskUsageCalls,
  type FeatureUsageSummary,
  type ModelCallRecord,
  type TaskUsageSummary,
} from "@/lib/api";
import { cn } from "@/lib/utils";

type Props = {
  taskId: string;
  refreshKey?: string | number;
};

function formatUsd(value?: string | null): string {
  if (value == null) return "未知";
  const amount = Number(value);
  if (!Number.isFinite(amount)) return "未知";
  if (amount === 0) return "$0.00";
  if (amount < 0.01) return `$${amount.toFixed(6)}`;
  return `$${amount.toFixed(4)}`;
}

function formatTokens(value?: number | null): string {
  if (value == null) return "-";
  return new Intl.NumberFormat("zh-CN").format(value);
}

function costSourceLabel(source: ModelCallRecord["cost_source"]): string {
  switch (source) {
    case "provider": return "实际";
    case "catalog":
    case "manual": return "估算";
    case "cache": return "缓存";
    default: return "未知";
  }
}

function statusLabel(status: ModelCallRecord["status"]): string {
  switch (status) {
    case "success": return "成功";
    case "failed": return "失败";
    case "quality_gate_failed": return "质检未通过";
    case "cache_hit": return "缓存命中";
  }
}

function CallRow({ call }: { call: ModelCallRecord }) {
  return (
    <div className="space-y-1 border-b border-border/60 py-2 last:border-0">
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <p className="truncate text-[11px] font-medium text-foreground" title={call.actual_model}>
            {call.actual_model}
          </p>
          <p className="text-[10px] text-muted-foreground">
            {new Date(call.completed_at).toLocaleString("zh-CN", { hour12: false })}
            {call.stage ? ` · ${call.stage}` : ""}
          </p>
        </div>
        <span className="shrink-0 font-mono text-[11px] text-primary">
          {formatUsd(call.cost_usd)}
        </span>
      </div>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[10px] text-muted-foreground">
        <span>输入 {formatTokens(call.usage.input_tokens)}</span>
        <span>输出 {formatTokens(call.usage.output_tokens)}</span>
        <span>总计 {formatTokens(call.usage.total_tokens)}</span>
        <Badge
          variant="outline"
          className={cn(
            "h-4 px-1 text-[9px]",
            call.status === "failed" && "border-destructive/50 text-destructive",
          )}
        >
          {statusLabel(call.status)}
        </Badge>
        <Badge variant="secondary" className="h-4 px-1 text-[9px]">
          {costSourceLabel(call.cost_source)}
        </Badge>
      </div>
    </div>
  );
}

function FeatureDetails({
  taskId,
  feature,
}: {
  taskId: string;
  feature: FeatureUsageSummary;
}) {
  const [open, setOpen] = useState(false);
  const [calls, setCalls] = useState<ModelCallRecord[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (nextCursor?: string) => {
    setLoading(true);
    setError(null);
    try {
      const page = await fetchTaskUsageCalls(taskId, {
        feature: feature.feature,
        cursor: nextCursor,
        limit: 50,
      });
      setCalls((current) => nextCursor ? [...current, ...page.items] : page.items);
      setCursor(page.next_cursor ?? null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "明细加载失败");
    } finally {
      setLoading(false);
    }
  }, [feature.feature, taskId]);

  useEffect(() => {
    if (open && calls.length === 0 && !loading && !error) {
      void load();
    }
  }, [calls.length, error, load, loading, open]);

  return (
    <div className="rounded-lg border border-border bg-card/70 px-3 py-2.5">
      <div className="flex items-center gap-2">
        <div className="min-w-0 flex-1">
          <p className="truncate text-xs font-medium text-foreground">{feature.label}</p>
          <p className="mt-0.5 text-[10px] text-muted-foreground">
            {feature.call_count} 次调用 · {formatTokens(feature.total_tokens)} token
          </p>
        </div>
        <div className="text-right">
          <p className="font-mono text-xs text-primary">{formatUsd(feature.cost_usd)}</p>
          {feature.unknown_cost_count > 0 && (
            <p className="text-[9px] text-amber-400">{feature.unknown_cost_count} 笔金额未知</p>
          )}
        </div>
        <HoverCard open={open} onOpenChange={setOpen} openDelay={120} closeDelay={180}>
          <HoverCardTrigger asChild>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="h-7 px-2 text-[11px]"
              aria-label={`查看${feature.label}调用详情`}
              onClick={() => setOpen((current) => !current)}
            >
              详情
            </Button>
          </HoverCardTrigger>
          <HoverCardContent
            align="end"
            side="left"
            className="w-[25rem] max-w-[calc(100vw-2rem)] p-3"
          >
            <div className="mb-2 flex items-center justify-between">
              <div>
                <p className="text-xs font-medium">{feature.label}调用明细</p>
                <p className="text-[10px] text-muted-foreground">逐次模型、token 与费用</p>
              </div>
              <Badge variant="outline">{feature.call_count} 次</Badge>
            </div>
            <div className="max-h-72 overflow-y-auto pr-1">
              {calls.map((call) => <CallRow key={call.call_id} call={call} />)}
              {loading && (
                <div className="flex items-center justify-center gap-2 py-4 text-xs text-muted-foreground">
                  <Loader2 className="h-3.5 w-3.5 animate-spin" /> 加载中
                </div>
              )}
              {error && <p className="py-3 text-xs text-destructive">{error}</p>}
              {!loading && !error && calls.length === 0 && (
                <p className="py-3 text-xs text-muted-foreground">暂无调用明细</p>
              )}
            </div>
            {cursor && (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="mt-2 h-7 w-full text-[11px]"
                disabled={loading}
                onClick={() => void load(cursor)}
              >
                加载更多
              </Button>
            )}
          </HoverCardContent>
        </HoverCard>
      </div>
    </div>
  );
}

export function FundsUsagePanel({ taskId, refreshKey }: Props) {
  const rootRef = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(false);
  const [summary, setSummary] = useState<TaskUsageSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setSummary(await fetchTaskUsage(taskId));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "资金消耗统计加载失败");
    } finally {
      setLoading(false);
    }
  }, [taskId]);

  useEffect(() => {
    const root = rootRef.current;
    if (!root) return;
    const region = root.closest<HTMLElement>("[role=region]");
    if (!region) {
      setVisible(true);
      return;
    }
    const update = () => setVisible(!region.hidden);
    update();
    const observer = new MutationObserver(update);
    observer.observe(region, { attributes: true, attributeFilter: ["hidden"] });
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (visible) void load();
  }, [load, refreshKey, visible]);

  return (
    <div ref={rootRef} className="space-y-3 p-3">
      <div className="rounded-xl border border-primary/20 bg-primary/5 p-4">
        <div className="flex items-start gap-3">
          <div className="rounded-lg bg-primary/10 p-2 text-primary">
            <Coins className="h-5 w-5" />
          </div>
          <div className="min-w-0 flex-1">
            <p className="text-[10px] uppercase tracking-wider text-muted-foreground">任务总消耗</p>
            <p className="mt-1 font-mono text-2xl font-semibold text-foreground">
              {loading && !summary ? "--" : formatUsd(summary?.cost_usd ?? "0")}
              <span className="ml-1 text-xs font-normal text-muted-foreground">USD</span>
            </p>
            {summary && (
              <p className="mt-1 text-[10px] text-muted-foreground">
                {summary.call_count} 次调用 · {formatTokens(summary.total_tokens)} token
                {summary.unknown_cost_count > 0 ? ` · ${summary.unknown_cost_count} 笔金额未知` : ""}
              </p>
            )}
          </div>
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            aria-label="刷新资金消耗"
            disabled={loading}
            onClick={() => void load()}
          >
            <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} />
          </Button>
        </div>
      </div>

      {error && (
        <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-xs text-destructive">
          {error}
        </div>
      )}

      {summary?.features.map((feature) => (
        <FeatureDetails key={feature.feature} taskId={taskId} feature={feature} />
      ))}

      {!loading && !error && summary?.features.length === 0 && (
        <div className="rounded-lg border border-dashed border-border p-6 text-center">
          <Coins className="mx-auto h-6 w-6 text-muted-foreground/50" />
          <p className="mt-2 text-xs text-muted-foreground">暂无云端模型消费记录</p>
          <p className="mt-1 text-[10px] text-muted-foreground/70">统计从本功能启用后的新调用开始</p>
        </div>
      )}

      {summary?.tracking_started_at && (
        <div className="flex items-start gap-2 text-[10px] text-muted-foreground">
          <Info className="mt-0.5 h-3 w-3 shrink-0" />
          <p>
            统计始于 {new Date(summary.tracking_started_at).toLocaleString("zh-CN", { hour12: false })}。
            金额优先采用供应商实际费用；RouterHub 缺失实际费用时按最近同步报价重算历史估算。
          </p>
        </div>
      )}
    </div>
  );
}
