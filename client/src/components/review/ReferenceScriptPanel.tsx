import { useEffect, useRef, useState } from "react";
import { Play, Save, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  fetchReferenceScript,
  saveReferenceScript,
  formatDuration,
  type ReferenceScriptResult,
} from "@/lib/api";
import type { PlaybackRange } from "@/lib/cut-review";

type Props = {
  taskId: string;
  initialScript: string;
  refreshKey: string;
  disabled?: boolean;
  onSaved: () => void;
  onAudition: (ranges: PlaybackRange[], label: string) => void;
};

export function ReferenceScriptPanel({
  taskId,
  initialScript,
  refreshKey,
  disabled,
  onSaved,
  onAudition,
}: Props) {
  const [draft, setDraft] = useState(initialScript);
  const [saved, setSaved] = useState(initialScript);
  const [result, setResult] = useState<ReferenceScriptResult | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [onlyIssues, setOnlyIssues] = useState(false);
  const [limit, setLimit] = useState(20);
  const [reload, setReload] = useState(0);
  const requestId = useRef(0);

  useEffect(() => {
    if (disabled) return;
    const id = ++requestId.current;
    setBusy(true);
    setError("");
    fetchReferenceScript(taskId)
      .then((data) => {
        if (id !== requestId.current) return;
        setResult(data);
        // 刷新比对结果时保留未保存的本地输入。
        setDraft((current) =>
          current === saved ? data.reference_script : current,
        );
        setSaved(data.reference_script);
      })
      .catch((e) => {
        if (id === requestId.current) setError(e.message || "原稿匹配失败");
      })
      .finally(() => {
        if (id === requestId.current) setBusy(false);
      });
    return () => {
      requestId.current += 1;
    };
  }, [taskId, refreshKey, reload, disabled]);

  const save = async () => {
    const id = ++requestId.current;
    setBusy(true);
    setError("");
    setNotice("");
    const submitted = draft;
    try {
      const data = await saveReferenceScript(taskId, submitted, saved);
      if (id !== requestId.current) return;
      setDraft((current) =>
        current === submitted ? data.reference_script : current,
      );
      setSaved(data.reference_script);
      setResult(data);
      setNotice("原稿已保存。下次模型审计将参考新原稿，现有剪辑未改动。");
      onSaved();
    } catch (e) {
      if (id === requestId.current)
        setError(e instanceof Error ? e.message : "原稿保存失败");
    } finally {
      if (id === requestId.current) setBusy(false);
    }
  };
  const report = result?.report;
  const lines =
    report?.lines.filter(
      (line) => !onlyIssues || !line.retained || line.candidates.length > 1,
    ) ?? [];
  const dirty = draft !== saved;

  return (
    <div
      className="h-full overflow-y-auto p-3 space-y-4"
      aria-label="口播原稿匹配"
    >
      <div className="space-y-2">
        <label
          htmlFor={`reference-script-${taskId}`}
          className="text-sm font-medium"
        >
          口播原稿
        </label>
        <Textarea
          id={`reference-script-${taskId}`}
          rows={6}
          value={draft}
          maxLength={10000}
          disabled={disabled}
          placeholder="粘贴口播稿，每句话或每个要点单独一行。"
          onChange={(e) => setDraft(e.target.value)}
        />
        <p className="text-xs text-muted-foreground">
          {draft.length}/10000 字 ·
          本地文字比对；语义审计时会将匹配原稿交给已配置的模型参考。
        </p>
        <div className="flex gap-2">
          <Button
            size="sm"
            onClick={save}
            disabled={busy || disabled || !dirty}
          >
            <Save className="size-3.5" />
            保存并匹配
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={() => setReload((n) => n + 1)}
            disabled={busy || disabled}
          >
            <RefreshCw className="size-3.5" />
            刷新比对
          </Button>
        </div>
        {dirty && (
          <p className="text-xs text-amber-300">
            原稿有未保存修改；下方仍展示已保存版本。
          </p>
        )}
        {notice && (
          <p role="status" className="text-xs text-emerald-400">
            {notice}
          </p>
        )}
        {error && (
          <p role="alert" className="text-xs text-red-400">
            {error}
          </p>
        )}
      </div>
      {disabled && (
        <p className="text-xs text-muted-foreground">
          正在处理任务，完成后将刷新比对结果。
        </p>
      )}
      {busy && (
        <p role="status" className="text-xs text-muted-foreground">
          正在比对原稿…
        </p>
      )}
      {report && !busy && (
        <>
          <div className="rounded-lg border border-border p-3 space-y-2">
            <p className="text-sm">
              共 {report.lines.length} 句 · 已匹配 {report.matched} · 待核对{" "}
              {report.uncertain} · 未匹配 {report.missing}
            </p>
            <p className="text-xs text-muted-foreground">
              {report.retained}{" "}
              句有完整保留候选。相似度仅反映文字接近程度；未匹配也可能是改述或识别差异。最多展示每句
              3 个候选。
            </p>
            {report.truncated && (
              <p className="text-xs text-amber-300">
                本次比对前 200 句，请分批核对剩余原稿。
              </p>
            )}
            <label className="flex items-center gap-2 text-xs">
              <input
                type="checkbox"
                checked={onlyIssues}
                onChange={(e) => {
                  setOnlyIssues(e.target.checked);
                  setLimit(20);
                }}
              />
              只看未完整保留和多次录制
            </label>
          </div>
          {!report.lines.length && (
            <p className="text-xs text-muted-foreground">
              填写原稿并保存，识别分段后即可定位录制内容。
            </p>
          )}
          {lines.slice(0, limit).map((line) => (
            <div
              key={line.index}
              className="rounded-lg border border-border p-3 space-y-2"
            >
              <div className="flex items-start gap-2">
                <span className="text-xs text-muted-foreground shrink-0">
                  {line.index}
                </span>
                <p className="text-sm break-words">{line.text}</p>
              </div>
              <p
                className={
                  line.retained
                    ? "text-xs text-emerald-400"
                    : "text-xs text-amber-300"
                }
              >
                {line.status === "missing"
                  ? "未找到相近台词，请核对是否漏讲"
                  : line.status === "uncertain"
                    ? "表达相近，需核对原声"
                    : line.retained
                      ? "已匹配，有完整保留候选"
                      : "已匹配，但候选含删除内容，请核对"}
              </p>
              {line.candidates.map((candidate, index) => (
                <div
                  key={candidate.segment_ids.join(":")}
                  className="rounded bg-muted/40 p-2 space-y-1.5"
                >
                  <div className="flex flex-wrap items-center justify-between gap-1 text-xs">
                    <span>
                      候选 {index + 1} · 字面相似{" "}
                      {Math.round(candidate.similarity * 100)}% ·{" "}
                      {candidate.state === "kept"
                        ? "已保留"
                        : candidate.state === "mixed"
                          ? "部分删除"
                          : "已删除"}
                    </span>
                    <Button
                      size="sm"
                      variant="ghost"
                      className="h-6 px-1 text-xs"
                      disabled={disabled}
                      aria-label={`试听原稿第 ${line.index} 句候选 ${index + 1}`}
                      onClick={() =>
                        onAudition(
                          [{ start: candidate.start, end: candidate.end }],
                          `原稿第 ${line.index} 句 · 候选 ${index + 1}`,
                        )
                      }
                    >
                      <Play className="size-3" />
                      {formatDuration(candidate.start)}–
                      {formatDuration(candidate.end)}
                    </Button>
                  </div>
                  <p className="text-xs text-muted-foreground break-words">
                    {candidate.text}
                  </p>
                  {candidate.warnings.map((warning) => (
                    <p key={warning} className="text-xs text-amber-300">
                      {warning}
                    </p>
                  ))}
                </div>
              ))}
            </div>
          ))}
          {lines.length > limit && (
            <Button
              size="sm"
              variant="outline"
              onClick={() => setLimit((n) => n + 20)}
            >
              显示更多（剩余 {lines.length - limit} 句）
            </Button>
          )}
        </>
      )}
    </div>
  );
}
