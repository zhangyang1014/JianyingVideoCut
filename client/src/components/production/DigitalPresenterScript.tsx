import { useEffect, useId, useState } from "react";
import { Copy, Loader2, Sparkles } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogTitle,
} from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import type { ProductionProject, ProductionShot } from "@/lib/productions-api";

const scriptOf = (shots: ProductionShot[]) =>
  shots
    .map((shot) => shot.spoken_text.trim())
    .filter(Boolean)
    .join("\n");
const comparable = (text: string) => text.replace(/\s/g, "");

export function DigitalPresenterScript({
  project,
  busy,
  save,
  generate,
  apply,
  onDirtyChange,
}: {
  project: ProductionProject;
  busy: boolean;
  save: (text: string) => Promise<boolean>;
  generate: () => Promise<boolean>;
  apply: () => Promise<boolean>;
  onDirtyChange?: (dirty: boolean) => void;
}) {
  const editorId = useId();
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState("");
  const [editSource, setEditSource] = useState("");
  const [preview, setPreview] = useState(false);
  const [generationRequest, setGenerationRequest] = useState<
    "idle" | "submitting" | "failed"
  >("idle");
  const draft = project.narration_draft;
  const script = draft?.text ?? scriptOf(project.shots);
  const onCamera = draft
    ? ""
    : scriptOf(project.shots.filter((s) => s.voice_mode === "generated"));
  const voiceover = draft
    ? ""
    : scriptOf(project.shots.filter((s) => s.voice_mode === "voiceover"));
  const candidate = draft?.shots ?? [];
  const factWarnings = project.narration_fact_warnings ?? [];
  const submitting = generationRequest === "submitting";
  const generating =
    !!draft && (submitting || project.planning_status === "running");
  const generationFailed =
    !generating &&
    candidate.length === 0 &&
    (project.planning_status === "failed" || generationRequest === "failed");
  const disabled = busy || submitting || project.planning_status === "running";
  const editConflict = editing && comparable(editSource) !== comparable(script);
  const dirty = editing && comparable(text) !== comparable(editSource);
  useEffect(() => {
    onDirtyChange?.(dirty);
  }, [dirty, onDirtyChange]);
  useEffect(
    () => () => {
      onDirtyChange?.(false);
    },
    [onDirtyChange],
  );
  let generationMessage = "新口播稿已保存，待生成新分镜。";
  if (generating) {
    generationMessage = "正在按新口播稿生成分镜…";
  } else if (generationFailed) {
    generationMessage =
      generationRequest === "failed"
        ? "未能提交分镜生成请求，请重试。"
        : project.planning_note || "分镜生成失败，请重试。";
  } else if (candidate.length > 0) {
    generationMessage =
      project.planning_note || "已生成新分镜，可先查看并确认应用。";
  }

  const copy = async (value: string) => {
    try {
      await navigator.clipboard.writeText(value);
      toast.success("已复制口播稿");
    } catch {
      toast.error("复制失败，请选中文案手动复制");
    }
  };

  if (!script && draft === undefined) return null;
  return (
    <section className="rounded-xl border border-border bg-card p-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="font-semibold">数字人口播稿</h2>
        {draft !== undefined && !editing && (
          <Button
            variant="outline"
            size="sm"
            disabled={disabled}
            onClick={() => {
              setText(script);
              setEditSource(script);
              setEditing(true);
            }}
          >
            {script ? "编辑口播稿" : "填写口播稿"}
          </Button>
        )}
      </div>
      {editing ? (
        <div className="mt-3 space-y-3">
          <label htmlFor={editorId} className="sr-only">
            口播稿
          </label>
          <Textarea
            id={editorId}
            rows={10}
            maxLength={12000}
            value={text}
            disabled={disabled}
            onChange={(e) => setText(e.target.value)}
          />
          {editConflict && (
            <p className="text-sm text-amber-300">
              口播稿已在别处更新。请复制需要保留的修改，再取消并重新编辑。
            </p>
          )}
          <div className="flex flex-wrap gap-2">
            <Button
              size="sm"
              disabled={
                disabled ||
                editConflict ||
                !text.trim() ||
                comparable(text) === comparable(script)
              }
              onClick={async () => {
                if (await save(text)) {
                  setEditing(false);
                  setGenerationRequest("idle");
                }
              }}
            >
              保存口播稿
            </Button>
            <Button
              variant="ghost"
              size="sm"
              disabled={busy}
              onClick={() => setEditing(false)}
            >
              取消
            </Button>
          </div>
        </div>
      ) : (
        script && (
          <>
            <p className="mt-2 whitespace-pre-wrap text-sm leading-relaxed">
              {script}
            </p>
            {draft && factWarnings.length > 0 && (
              <div
                role="note"
                className="mt-3 rounded-md border border-amber-400/30 bg-amber-400/5 p-3 text-sm text-amber-200"
              >
                <p className="font-medium">商品表述待核对</p>
                <ul className="mt-1 list-disc space-y-1 pl-5">
                  {factWarnings.map((warning, index) => (
                    <li key={index}>{warning}</li>
                  ))}
                </ul>
              </div>
            )}
            <div className="mt-4 flex flex-wrap gap-2">
              <Button variant="outline" size="sm" onClick={() => copy(script)}>
                <Copy className="mr-2 h-4 w-4" />
                复制全文
              </Button>
              {onCamera && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => copy(onCamera)}
                >
                  复制出镜口播
                </Button>
              )}
              {voiceover && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => copy(voiceover)}
                >
                  复制画外音
                </Button>
              )}
            </div>
          </>
        )
      )}
      {draft && !editing && (
        <div className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-primary/35 bg-primary/5 p-3">
          <p
            role={generationFailed ? "alert" : "status"}
            className={generationFailed ? "text-sm text-amber-300" : "text-sm"}
          >
            {generationMessage}
          </p>
          {candidate.length > 0 && !generating ? (
            <Button
              size="sm"
              disabled={disabled}
              onClick={() => setPreview(true)}
            >
              预览并应用新分镜
            </Button>
          ) : (
            <Button
              size="sm"
              disabled={disabled}
              onClick={async () => {
                setPreview(false);
                setGenerationRequest("submitting");
                try {
                  setGenerationRequest((await generate()) ? "idle" : "failed");
                } catch {
                  setGenerationRequest("failed");
                }
              }}
            >
              {generating ? (
                <Loader2
                  aria-hidden="true"
                  className="mr-2 h-4 w-4 animate-spin"
                />
              ) : (
                <Sparkles aria-hidden="true" className="mr-2 h-4 w-4" />
              )}
              {generating
                ? "正在生成分镜"
                : generationFailed
                  ? "重新尝试生成分镜"
                  : "按新稿重新生成分镜"}
            </Button>
          )}
        </div>
      )}
      <Dialog open={preview && candidate.length > 0} onOpenChange={setPreview}>
        <DialogContent className="max-w-xl">
          <DialogTitle>新分镜预览</DialogTitle>
          <DialogDescription>
            应用后会更新当前分镜。已有素材可在素材库重新关联。
          </DialogDescription>
          <ol className="max-h-[55vh] list-decimal space-y-3 overflow-y-auto pl-6 pr-2 text-sm">
            {candidate.map((shot) => (
              <li
                key={shot.shot_id}
                className="space-y-1 border-b border-border pb-3 last:border-0"
              >
                <h3 className="font-medium">
                  {shot.purpose} · {shot.target_duration} 秒
                </h3>
                <p>{shot.visual}</p>
                <p className="whitespace-pre-wrap text-muted-foreground">
                  {shot.spoken_text}
                </p>
              </li>
            ))}
          </ol>
          <DialogFooter>
            <Button
              variant="outline"
              disabled={busy}
              onClick={() => setPreview(false)}
            >
              关闭
            </Button>
            <Button
              disabled={disabled}
              onClick={async () => {
                if (await apply()) {
                  setPreview(false);
                  toast.success("已应用新分镜");
                }
              }}
            >
              应用新分镜
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
}
