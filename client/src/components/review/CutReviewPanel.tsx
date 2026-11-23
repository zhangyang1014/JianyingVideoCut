import { useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import { formatTimestamp, type Segment } from "@/lib/api";
import { buildCutReviews, type PlaybackRange } from "@/lib/cut-review";

export function CutReviewPanel({
  taskId,
  segments,
  disabled,
  onAudition,
  onRestore,
}: {
  taskId: string;
  segments: Segment[];
  disabled?: boolean;
  onAudition: (ranges: PlaybackRange[], label: string) => void;
  onRestore: (ids: string[]) => Promise<void>;
}) {
  const cuts = useMemo(() => buildCutReviews(segments), [segments]);
  const storageKey = `goldenclip:cut-review:v1:${taskId}`;
  const [checked, setChecked] = useState<Set<string>>(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(storageKey) ?? "[]");
      return new Set(
        Array.isArray(saved)
          ? saved.filter((item) => typeof item === "string")
          : [],
      );
    } catch {
      return new Set();
    }
  });
  const [onlyPending, setOnlyPending] = useState(true);
  const [limit, setLimit] = useState(20);
  const [restoring, setRestoring] = useState<string | null>(null);
  const pending = cuts.filter((cut) => !checked.has(cut.id));
  const visible = onlyPending ? pending : cuts;
  const markChecked = (id: string) => {
    const next = new Set(
      cuts.filter((cut) => checked.has(cut.id)).map((cut) => cut.id),
    );
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setChecked(next);
    try {
      localStorage.setItem(storageKey, JSON.stringify(Array.from(next)));
    } catch {
      /* Review still works without storage. */
    }
  };
  return (
    <div className="space-y-4 p-3" aria-label="剪切接缝复核">
      <div>
        <h3 className="text-sm font-medium">接缝复核</h3>
        <p className="mt-1 text-xs text-muted-foreground">
          {cuts.length} 处剪切 · {pending.length} 处待检查
        </p>
        <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
          按原顺序检查删除处，优先显示长段删除和短句衔接。快速试听用于判断内容，精确切口与集锦重排请核对成片预览。
        </p>
      </div>
      <div className="flex flex-wrap gap-2">
        <Button
          size="sm"
          variant="outline"
          disabled={disabled || !pending.some((cut) => cut.after.length)}
          onClick={() =>
            onAudition(
              pending.flatMap((cut) => cut.after),
              "连续试听待检查接缝",
            )
          }
        >
          连续试听待检查
        </Button>
        <Button
          size="sm"
          variant="ghost"
          onClick={() => setOnlyPending((value) => !value)}
        >
          {onlyPending ? "显示全部" : "只看待检查"}
        </Button>
      </div>
      {!visible.length && (
        <p className="py-6 text-center text-sm text-muted-foreground">
          {cuts.length
            ? "当前接缝已检查完，可到成片预览核对最终效果。"
            : "当前没有删除片段，无需接缝复核。"}
        </p>
      )}
      {visible.slice(0, limit).map((cut) => (
        <article
          key={cut.id}
          className="space-y-2 rounded-lg border border-border bg-card p-3"
        >
          <div className="flex flex-wrap items-center justify-between gap-1 text-xs">
            <span className="font-mono text-muted-foreground">
              {formatTimestamp(cut.start)} – {formatTimestamp(cut.end)}
            </span>
            <span
              className={
                cut.priority > 1 ? "text-amber-400" : "text-muted-foreground"
              }
            >
              {checked.has(cut.id)
                ? "已检查"
                : cut.priority > 1
                  ? "优先复核"
                  : "待检查"}
            </span>
          </div>
          <p className="text-xs text-amber-300/90">{cut.hint}</p>
          <p className="line-clamp-3 break-words text-sm">{cut.deletedText}</p>
          <p
            className="line-clamp-2 text-xs text-muted-foreground"
            title={cut.reason}
          >
            {cut.reason}
          </p>
          <div className="flex flex-wrap gap-2">
            <Button
              size="sm"
              variant="outline"
              disabled={disabled}
              onClick={() => onAudition(cut.before, "剪前原片段")}
            >
              听剪前
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={disabled || !cut.after.length}
              onClick={() => onAudition(cut.after, "剪后接缝")}
            >
              听剪后
            </Button>
            <Button
              size="sm"
              variant="ghost"
              disabled={disabled || restoring !== null}
              onClick={async () => {
                setRestoring(cut.id);
                try {
                  await onRestore(cut.deletedIds);
                } finally {
                  setRestoring(null);
                }
              }}
            >
              {restoring === cut.id ? "恢复中…" : "恢复删除内容"}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => markChecked(cut.id)}
            >
              {checked.has(cut.id) ? "重新检查" : "标记已检查"}
            </Button>
          </div>
        </article>
      ))}
      {visible.length > limit && (
        <Button variant="ghost" onClick={() => setLimit((value) => value + 20)}>
          再显示 20 处
        </Button>
      )}
    </div>
  );
}
