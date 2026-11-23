import type { ReactNode } from "react";
import { ChevronRight, RotateCcw } from "lucide-react";

import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card";
import { cn } from "@/lib/utils";

export type StepActivityTone = "muted" | "running" | "done" | "warning" | "danger" | "primary";

export interface StepActivityRecord {
  id?: string | number;
  version?: string | number;
  label: string;
  meta?: string;
  status?: string;
  time?: string | null;
  current?: boolean;
  disabled?: boolean;
  actionLabel?: string;
  onSelect?: () => void;
}

export interface StepActivityAction {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  pending?: boolean;
  title?: string;
}

export interface StepActivityChipProps {
  icon: ReactNode;
  name: string;
  model?: string | null;
  status: string;
  time?: string | null;
  tone?: StepActivityTone;
  records?: StepActivityRecord[];
  action?: StepActivityAction | null;
  hoverTitle?: string;
  /** 保留兼容旧调用，空记录时会自动展示当前摘要。 */
  emptyLabel?: string;
  className?: string;
  defaultOpen?: boolean;
}

const TONE_CLASS: Record<StepActivityTone, string> = {
  muted: "text-muted-foreground border-border/50 bg-secondary/20",
  running: "text-blue-300 border-blue-500/30 bg-blue-500/10",
  done: "text-primary border-primary/25 bg-primary/10",
  warning: "text-amber-300 border-amber-500/30 bg-amber-500/10",
  danger: "text-destructive border-destructive/40 bg-destructive/10",
  primary: "text-foreground border-border bg-card/70",
};

export function StepActivityChip({
  icon,
  name,
  model,
  status,
  time,
  tone = "muted",
  records = [],
  action,
  hoverTitle,
  className,
  defaultOpen,
}: StepActivityChipProps) {
  const noHistoryStatuses = new Set(["未执行", "未生成", "待质检"]);
  const shouldUseCurrentSummary = records.length === 0 && !noHistoryStatuses.has(status);
  const displayRecords: StepActivityRecord[] = records.length > 0
    ? records
    : shouldUseCurrentSummary
      ? [{
        id: "current-summary",
        version: "当前",
        label: name,
        meta: model ?? undefined,
        status,
        time: time ?? null,
        current: true,
      }]
      : [];

  return (
    <HoverCard defaultOpen={defaultOpen} openDelay={80} closeDelay={180}>
      <HoverCardTrigger asChild>
        <button
          type="button"
          className={cn(
            "flex h-7 min-w-fit max-w-[220px] shrink-0 items-center gap-1.5 overflow-hidden rounded-md border px-2 text-xs transition-colors",
            "hover:border-border hover:bg-secondary/60 hover:text-foreground",
            TONE_CLASS[tone],
            className,
          )}
          title={hoverTitle ?? `${name}：${status}`}
        >
          <span className="shrink-0">{icon}</span>
          <span className="shrink-0 whitespace-nowrap font-medium text-foreground">{name}</span>
          <span className="shrink-0 text-muted-foreground/50">·</span>
          <span className="shrink-0 whitespace-nowrap font-medium">{status}</span>
          {time && (
            <>
              <span className="shrink-0 text-muted-foreground/50">·</span>
              <span className="shrink-0 text-muted-foreground/70">{time}</span>
            </>
          )}
        </button>
      </HoverCardTrigger>
      <HoverCardContent align="start" className="w-80 border-border bg-card p-0">
        <div className="border-b border-border px-3 py-2">
          <p className="text-xs font-medium text-foreground">{hoverTitle ?? name}</p>
          <p className="mt-0.5 text-[10px] text-muted-foreground/60">
            {displayRecords.length > 0 ? `共 ${displayRecords.length} 条记录` : "无历史版本"}
          </p>
        </div>
        <div className="max-h-72 overflow-y-auto">
          {displayRecords.length === 0 ? (
            <div className="px-3 py-4 text-center text-[11px] text-muted-foreground/50">
              无历史版本
            </div>
          ) : displayRecords.map((record, index) => (
              <button
                type="button"
                key={record.id ?? `${record.label}-${record.time ?? index}`}
                disabled={record.disabled || !record.onSelect}
                onClick={record.onSelect}
                className={cn(
                  "flex w-full items-center gap-3 border-b border-border/40 px-3 py-2.5 text-left last:border-0",
                  record.current ? "bg-primary/8" : "bg-transparent",
                  record.onSelect && !record.disabled && "hover:bg-secondary/40",
                  (record.disabled || !record.onSelect) && "cursor-default",
                )}
              >
                <span
                  className={cn(
                    "w-7 shrink-0 rounded py-0.5 text-center font-mono text-[10px] font-medium",
                    record.current ? "bg-primary/20 text-primary" : "bg-secondary text-muted-foreground",
                  )}
                >
                  {record.version != null
                    ? (typeof record.version === "number" ? `v${record.version}` : record.version)
                    : index + 1}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-1.5">
                    <span className="truncate text-[11px] text-foreground">{record.label}</span>
                    {record.current && (
                      <span className="shrink-0 rounded bg-primary/20 px-1 py-0.5 text-[9px] text-primary">
                        当前
                      </span>
                    )}
                  </span>
                  <span className="mt-0.5 flex items-center gap-1.5 text-[10px] text-muted-foreground/60">
                    {record.status && <span>{record.status}</span>}
                    {record.meta && (
                      <>
                        {record.status && <span className="text-muted-foreground/30">·</span>}
                        <span className="truncate">{record.meta}</span>
                      </>
                    )}
                    {record.time && (
                      <>
                        {(record.status || record.meta) && <span className="text-muted-foreground/30">·</span>}
                        <span className="shrink-0">{record.time}</span>
                      </>
                    )}
                  </span>
                </span>
                {record.actionLabel && !record.current && (
                  <span className="flex shrink-0 items-center gap-0.5 text-[10px] text-primary">
                    {record.actionLabel}
                    <ChevronRight className="h-3 w-3" />
                  </span>
                )}
              </button>
          ))}
        </div>
        {action && (
          <div className="border-t border-border px-3 py-2">
            <button
              type="button"
              className="flex w-full items-center justify-center gap-1.5 rounded py-1 text-[10px] text-muted-foreground transition-colors hover:bg-secondary/40 hover:text-foreground disabled:opacity-50"
              onClick={action.onClick}
              disabled={action.disabled || action.pending}
              title={action.title}
            >
              {action.pending ? (
                <span className="h-3 w-3 animate-spin rounded-full border border-current border-t-transparent" />
              ) : (
                <RotateCcw className="h-3 w-3" />
              )}
              {action.label}
            </button>
          </div>
        )}
      </HoverCardContent>
    </HoverCard>
  );
}
