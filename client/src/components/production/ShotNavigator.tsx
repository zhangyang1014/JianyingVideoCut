import { useEffect, useRef, useState } from "react";
import {
  CheckCircle2,
  Circle,
  Film,
  Loader2,
  TriangleAlert,
} from "lucide-react";
import { cn } from "@/lib/utils";

export type ShotNavigationStatus =
  | "ready"
  | "pending"
  | "processing"
  | "warning";

export interface ShotNavigationItem {
  id: string;
  title: string;
  thumbnailUrl?: string;
  duration: number;
  status: ShotNavigationStatus;
  detail?: string;
}

export interface ShotNavigatorProps {
  items: ShotNavigationItem[];
  activeId: string | null;
  onSelect: (id: string) => void;
}

const statuses = {
  ready: { label: "可用", icon: CheckCircle2, color: "text-emerald-400" },
  pending: { label: "待素材", icon: Circle, color: "text-muted-foreground" },
  processing: { label: "处理中", icon: Loader2, color: "text-primary" },
  warning: { label: "待检查", icon: TriangleAlert, color: "text-amber-300" },
} satisfies Record<
  ShotNavigationStatus,
  { label: string; icon: typeof Circle; color: string }
>;

function ShotThumbnail({ url }: { url?: string }) {
  const [failedUrl, setFailedUrl] = useState<string>();
  if (url && failedUrl !== url) {
    return (
      <img
        src={url}
        alt=""
        loading="lazy"
        className="h-full w-full object-contain"
        onError={() => setFailedUrl(url)}
      />
    );
  }
  return (
    <span className="flex h-full flex-col items-center justify-center gap-1.5 text-muted-foreground/70">
      <Film className="h-5 w-5" aria-hidden="true" />
      <span className="text-[10px]">暂无画面</span>
    </span>
  );
}

export function ShotNavigator({
  items,
  activeId,
  onSelect,
}: ShotNavigatorProps) {
  const [filter, setFilter] = useState<"all" | "pending">("all");
  const activeRef = useRef<HTMLButtonElement>(null);
  const readyCount = items.filter((item) => item.status === "ready").length;
  const pendingCount = items.length - readyCount;
  const visibleItems = items.filter(
    (item) => filter === "all" || item.status !== "ready",
  );
  const visibleActiveId = visibleItems.find((item) => item.id === activeId)?.id;

  useEffect(() => {
    activeRef.current?.scrollIntoView?.({
      block: "nearest",
      inline: "nearest",
    });
  }, [visibleActiveId, filter]);

  return (
    <aside
      aria-label="分幕目录"
      className="flex min-h-0 w-full flex-col overflow-hidden rounded-xl border border-border bg-card"
    >
      <div className="shrink-0 border-b border-border p-3">
        <div className="flex items-baseline justify-between gap-2">
          <h2 className="text-sm font-semibold">分幕目录</h2>
          <span className="text-[11px] tabular-nums text-muted-foreground">
            {items.length} 幕
          </span>
        </div>
        <div
          className="mt-3 flex rounded-md bg-background p-0.5"
          role="group"
          aria-label="分幕筛选"
        >
          {(
            [
              ["all", "全部", items.length],
              ["pending", "待处理", pendingCount],
            ] as const
          ).map(([value, label, count]) => (
            <button
              key={value}
              type="button"
              aria-label={`${label} ${count}`}
              aria-pressed={filter === value}
              onClick={() => setFilter(value)}
              className={cn(
                "flex min-h-8 flex-1 items-center justify-center gap-1.5 rounded px-2 text-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                filter === value
                  ? "bg-secondary font-medium text-foreground"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {label}
              <span className="text-[10px] tabular-nums opacity-70">
                {count}
              </span>
            </button>
          ))}
        </div>
      </div>

      <nav
        aria-label="选择分幕"
        className="flex min-h-0 gap-2 overflow-x-auto p-3 lg:block lg:flex-1 lg:space-y-2 lg:overflow-x-hidden lg:overflow-y-auto"
      >
        {visibleItems.map((item) => {
          const index = items.indexOf(item);
          const current = item.id === activeId;
          const status = statuses[item.status];
          const Icon = status.icon;
          const duration =
            Number.isFinite(item.duration) && item.duration > 0
              ? `${Math.round(item.duration * 10) / 10}s`
              : "时长待定";
          return (
            <button
              key={item.id}
              ref={current ? activeRef : undefined}
              type="button"
              aria-label={`第 ${index + 1} 幕 ${item.id} ${item.title}，${status.label}`}
              aria-current={current ? "step" : undefined}
              title={item.detail || item.title}
              onClick={() => onSelect(item.id)}
              className={cn(
                "group w-40 shrink-0 overflow-hidden rounded-lg border text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring lg:flex lg:min-h-24 lg:w-full lg:items-center lg:gap-2 lg:p-2",
                current
                  ? "border-primary bg-primary/5 ring-1 ring-primary/30"
                  : "border-border bg-background/30 hover:border-primary/40 hover:bg-secondary/40",
              )}
            >
              <span className="relative block aspect-video overflow-hidden border-b border-border bg-black/70 lg:aspect-auto lg:h-[76px] lg:w-[52px] lg:shrink-0 lg:rounded lg:border-b-0">
                <ShotThumbnail url={item.thumbnailUrl} />
                <span
                  className={cn(
                    "absolute left-1.5 top-1.5 rounded px-1.5 py-0.5 text-[10px] font-medium tabular-nums lg:hidden",
                    current
                      ? "bg-primary text-primary-foreground"
                      : "bg-black/70 text-white",
                  )}
                >
                  {String(index + 1).padStart(2, "0")}
                </span>
                <span className="absolute bottom-1.5 right-1.5 rounded bg-black/75 px-1 py-0.5 text-[10px] tabular-nums text-white lg:hidden">
                  {duration}
                </span>
              </span>
              <span className="block space-y-1.5 px-2.5 py-2 lg:min-w-0 lg:flex-1 lg:space-y-1 lg:p-0">
                <span className="flex min-w-0 items-center gap-1.5">
                  <span className="hidden text-[10px] text-muted-foreground lg:inline">
                    第 {index + 1} 幕
                  </span>
                  <span
                    className={cn(
                      "shrink-0 font-mono text-[10px]",
                      current ? "text-primary" : "text-muted-foreground",
                    )}
                  >
                    {item.id}
                  </span>
                </span>
                <span className="line-clamp-2 text-xs font-medium leading-4">
                  {item.title}
                </span>
                <span
                  className={cn(
                    "flex items-center gap-1 text-[10px]",
                    status.color,
                  )}
                >
                  <Icon
                    aria-hidden="true"
                    className={cn(
                      "h-3 w-3",
                      item.status === "processing" && "animate-spin",
                    )}
                  />
                  {status.label}
                  <span className="ml-auto hidden shrink-0 tabular-nums text-muted-foreground lg:inline">
                    {duration}
                  </span>
                </span>
              </span>
            </button>
          );
        })}
        {visibleItems.length === 0 && (
          <p
            role="status"
            className="w-full py-8 text-center text-xs leading-5 text-muted-foreground"
          >
            {items.length === 0 ? "分镜生成后会显示在这里" : "所有分幕均已可用"}
          </p>
        )}
      </nav>
      <div className="hidden shrink-0 border-t border-border px-3 py-2.5 text-[11px] text-muted-foreground lg:block">
        <span className="text-emerald-400">{readyCount}</span> / {items.length}{" "}
        幕可用
      </div>
    </aside>
  );
}
