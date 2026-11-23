import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useLocation, useParams } from "wouter";
import {
  ArrowLeft,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  FileText,
  Images,
  Users,
  Clapperboard,
  Copy,
  Download,
  Film,
  Loader2,
  Plus,
  Sparkles,
  Upload,
} from "lucide-react";
import { toast } from "sonner";
import {
  ShotNavigator,
  type ShotNavigationItem,
} from "@/components/production/ShotNavigator";
import { ProductionShotPreview } from "@/components/production/ProductionShotPreview";
import { DigitalPresenterScript } from "@/components/production/DigitalPresenterScript";
import { DirectionWritingReview } from "@/components/production/DirectionWritingReview";
import { PresenterFields } from "@/components/production/PresenterFields";
import { presenterConfig, validPresenter } from "@/lib/presenters";
import {
  HeyGenOpening,
  isHeyGenActive,
} from "@/components/production/HeyGenOpening";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  cancelProductionRender,
  createProduction,
  getProduction,
  listProductionPresenters,
  listProductions,
  productionAction,
  uploadProductionFile,
  type CreateProduction,
  type ProductionAsset,
  type ProductionMediaOrigin,
  type ProductionPresenter,
  type ProductionProject,
  type ProductionScriptIntent,
  type ProductionSelection,
  type ProductionShot,
  type ProductionSummary,
  type ProductionType,
  type PresenterConfig,
} from "@/lib/productions-api";

const panel = "rounded-xl border border-border bg-card p-5";
const selectClass =
  "h-9 w-full rounded-md border border-input bg-background px-2 text-sm";
const sourceLabels = {
  existing: "已有素材",
  shoot: "用户实拍",
  aigc: "外部 AIGC",
};
const mediaOriginLabels = {
  original: "原素材",
  original_edit: "原素材剪辑",
  aigc: "AIGC 生成",
  unknown: "来源待确认",
} satisfies Record<ProductionMediaOrigin["kind"], string>;
const originalMedia: ProductionMediaOrigin = { kind: "original", provider: "" };
const unknownMedia: ProductionMediaOrigin = { kind: "unknown", provider: "" };

function mediaOriginLabel(origin = unknownMedia) {
  return origin.kind === "aigc" && origin.provider.trim()
    ? `${origin.provider} AIGC 生成`
    : mediaOriginLabels[origin.kind];
}

function MediaOriginBadge({
  origin = unknownMedia,
  detail,
}: {
  origin?: ProductionMediaOrigin;
  detail?: string;
}) {
  return (
    <span
      className={`inline-block max-w-full break-words rounded border px-1.5 py-0.5 text-xs leading-4 ${origin.kind === "aigc" ? "border-primary/30 bg-primary/10 text-primary" : "border-border bg-secondary text-secondary-foreground"}`}
    >
      {mediaOriginLabel(origin)}
      {detail && ` · ${detail}`}
    </span>
  );
}

const voiceLabels = {
  original: "主播原声",
  generated: "生成视频对白",
  voiceover: "独立旁白（画面静音）",
  silent: "无对白",
};
const stages: Record<string, string> = {
  brief: "整理资料",
  storyboard: "制作分镜",
  collecting: "等待素材",
  ready: "可以成片",
  rendering: "正在剪辑",
  completed: "已有成片",
};
const projectTypeLabels: Record<ProductionType, string> = {
  selling_video: "卖货视频",
  digital_presenter: "数字人口播",
};
const scriptIntentLabels: Record<ProductionScriptIntent, string> = {
  product_sales: "卖货转化",
  buying_comparison: "选购对比",
  experience_sharing: "经验分享",
};

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <label className="flex min-w-0 flex-col gap-1.5 text-sm">
      <span className="text-muted-foreground">{label}</span>
      {children}
    </label>
  );
}

function ScriptIntentField({
  value,
  onChange,
  disabled,
}: {
  value: ProductionScriptIntent | null;
  onChange: (value: ProductionScriptIntent | null) => void;
  disabled: boolean;
}) {
  return (
    <div>
      <Field label="口播目标">
        <select
          className={selectClass}
          value={value ?? ""}
          disabled={disabled}
          onChange={(event) =>
            onChange(
              (event.target.value || null) as ProductionScriptIntent | null,
            )
          }
        >
          <option value="">沿用默认写法</option>
          {Object.entries(scriptIntentLabels).map(([intent, label]) => (
            <option key={intent} value={intent}>
              {label}
            </option>
          ))}
        </select>
      </Field>
      <p className="mt-1 text-xs text-muted-foreground">用于下一次 AI 写稿。</p>
    </div>
  );
}

function UploadButton({
  label,
  accept,
  multiple = false,
  disabled,
  onFiles,
}: {
  label: string;
  accept: string;
  multiple?: boolean;
  disabled?: boolean;
  onFiles: (files: File[]) => void;
}) {
  const ref = useRef<HTMLInputElement>(null);
  return (
    <>
      <input
        ref={ref}
        type="file"
        className="hidden"
        accept={accept}
        multiple={multiple}
        aria-label={label}
        onChange={(e) => {
          const files = Array.from(e.target.files || []);
          e.target.value = "";
          if (files.length) onFiles(files);
        }}
      />
      <Button
        size="sm"
        variant="outline"
        disabled={disabled}
        onClick={() => ref.current?.click()}
      >
        <Upload className="mr-2 h-4 w-4" />
        {label}
      </Button>
    </>
  );
}

function NewProject({
  projects,
  presenters,
  presenterIssue,
  onCreate,
  busy,
}: {
  projects: ProductionSummary[];
  presenters: ProductionPresenter[];
  presenterIssue: string;
  onCreate: (form: CreateProduction) => void;
  busy: boolean;
}) {
  const [form, setForm] = useState<CreateProduction>({
    name: "",
    project_type: new URLSearchParams(window.location.search).has("presenter")
      ? "digital_presenter"
      : "selling_video",
    presenter_id: new URLSearchParams(window.location.search).get("presenter"),
    brand_name: "",
    product: "",
    platform: "抖音",
    target_duration: new URLSearchParams(window.location.search).has(
      "presenter",
    )
      ? null
      : 45,
    selling_points: "",
    offer: "",
    reuse_brand_project_id: "",
  });
  const change = (key: keyof CreateProduction, value: string | number) =>
    setForm((old) => ({ ...old, [key]: value }));
  const isDigital = form.project_type === "digital_presenter";
  const selectedPresenter = isDigital
    ? presenters.find((p) => p.id === form.presenter_id)
    : undefined;
  const presenterId = selectedPresenter?.id ?? null;
  const config = selectedPresenter
    ? (form.presenter_overrides ?? presenterConfig(selectedPresenter))
    : null;
  const [resetKey, setResetKey] = useState(0);
  return (
    <form
      className={`${panel} mx-auto max-w-3xl space-y-5`}
      onSubmit={(e) => {
        e.preventDefault();
        if (isDigital && (!presenterId || !config || !validPresenter(config)))
          return;
        onCreate({
          ...form,
          ...(isDigital && form.script_intent
            ? { script_intent: form.script_intent }
            : {}),
          presenter_id: presenterId,
          presenter_overrides: isDigital ? config : null,
          presenter_revision: selectedPresenter?.revision ?? null,
          reuse_brand_project_id: form.reuse_brand_project_id || null,
        });
      }}
    >
      <div>
        <h2 className="text-lg font-semibold">新建视频项目</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          品牌资料、创意、拍摄任务与成片，保存在同一个项目里。
        </p>
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="项目类型">
          <select
            className={selectClass}
            value={form.project_type}
            onChange={(e) => {
              const type =
                e.target.value === "digital_presenter"
                  ? "digital_presenter"
                  : "selling_video";
              setForm((old) => ({
                ...old,
                project_type: type,
                presenter_id: null,
                presenter_overrides: null,
                script_intent: undefined,
                target_duration: type === "digital_presenter" ? null : 45,
              }));
            }}
          >
            <option value="selling_video">卖货视频</option>
            <option value="digital_presenter">数字人口播</option>
          </select>
        </Field>
        {isDigital && (
          <Field label="数字人">
            <select
              className={selectClass}
              value={presenterId ?? ""}
              disabled={!presenters.length || busy}
              onChange={(e) =>
                setForm((old) => ({
                  ...old,
                  presenter_id: e.target.value || null,
                  presenter_overrides: null,
                }))
              }
            >
              <option value="">
                {presenterIssue ||
                  (presenters.length ? "选择数字人" : "正在读取数字人档案…")}
              </option>
              {presenters.map((presenter) => (
                <option key={presenter.id} value={presenter.id}>
                  {presenter.name}
                </option>
              ))}
            </select>
          </Field>
        )}
        {isDigital && (
          <ScriptIntentField
            value={form.script_intent ?? null}
            onChange={(scriptIntent) =>
              setForm((old) => ({ ...old, script_intent: scriptIntent }))
            }
            disabled={busy}
          />
        )}
      </div>
      {isDigital && (
        <section
          className="space-y-4 rounded-lg border border-primary/25 bg-primary/5 p-4"
          aria-label="本次数字人设定"
        >
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <h3 className="font-medium">本次数字人设定</h3>
              <p className="mt-1 text-xs text-muted-foreground">
                选定后带入档案，可临时修改；仅用于本次视频。
              </p>
            </div>
            <Link href="/presenters" className="text-sm text-primary underline">
              管理数字人
            </Link>
          </div>
          {selectedPresenter && config ? (
            <>
              <PresenterFields
                key={`${selectedPresenter.id}:${resetKey}`}
                presenterId={selectedPresenter.id}
                value={config}
                compact
                disabled={busy}
                onChange={(next) =>
                  setForm((old) => ({ ...old, presenter_overrides: next }))
                }
              />
              <Button
                type="button"
                variant="ghost"
                size="sm"
                disabled={busy}
                onClick={() => {
                  setForm((old) => ({ ...old, presenter_overrides: null }));
                  setResetKey((k) => k + 1);
                }}
              >
                恢复数字人默认设定
              </Button>
            </>
          ) : (
            <p className="text-sm text-muted-foreground">
              先选择本次视频关联的数字人。还没有合适的数字人时，可到管理页新建。
            </p>
          )}
        </section>
      )}
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="项目名称">
          <Input
            required
            aria-label="项目名称"
            value={form.name}
            onChange={(e) => change("name", e.target.value)}
            placeholder="例如：秋季新品 · 第一条视频"
          />
        </Field>
        <Field label="主推产品">
          <Input
            required
            aria-label="主推产品"
            value={form.product}
            onChange={(e) => change("product", e.target.value)}
            placeholder="一个项目聚焦一个产品"
          />
        </Field>
        <Field label="品牌名称">
          <Input
            aria-label="品牌名称"
            value={form.brand_name}
            onChange={(e) => change("brand_name", e.target.value)}
          />
        </Field>
        <Field label="复用品牌资料">
          <select
            className={selectClass}
            value={form.reuse_brand_project_id || ""}
            onChange={(e) => change("reuse_brand_project_id", e.target.value)}
          >
            <option value="">从新资料开始</option>
            {projects.map((p) => (
              <option key={p.id} value={p.id}>
                {p.brand_name || p.product} · {p.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="发布平台">
          <select
            className={selectClass}
            value={form.platform}
            onChange={(e) => change("platform", e.target.value)}
          >
            {["抖音", "小红书", "视频号"].map((x) => (
              <option key={x}>{x}</option>
            ))}
          </select>
        </Field>
        {form.project_type === "digital_presenter" ? (
          <Field label="参考片长（秒，选填）">
            <Input
              type="number"
              min={1}
              step={1}
              placeholder="留空，按内容和实际配音剪辑"
              value={form.target_duration ?? ""}
              onChange={(e) =>
                setForm((old) => ({
                  ...old,
                  target_duration: e.target.value
                    ? Number(e.target.value)
                    : null,
                }))
              }
            />
          </Field>
        ) : (
          <Field label="目标片长">
            <select
              className={selectClass}
              value={form.target_duration ?? 45}
              onChange={(e) =>
                change("target_duration", Number(e.target.value))
              }
            >
              {[30, 45, 60].map((x) => (
                <option key={x} value={x}>
                  {x} 秒 · 9:16 竖屏
                </option>
              ))}
            </select>
          </Field>
        )}
      </div>
      <Field label="商品事实与主推卖点">
        <Textarea
          aria-label="商品事实与主推卖点"
          value={form.selling_points}
          onChange={(e) => change("selling_points", e.target.value)}
          placeholder="每行一条已确认信息，稍后可以上传品牌 PPT 补充"
          rows={3}
        />
      </Field>
      <Field label="本次价格 / 活动（选填）">
        <Input
          value={form.offer}
          onChange={(e) => change("offer", e.target.value)}
          placeholder="仅保存在本次项目，不复用到其他项目"
        />
      </Field>
      <Button
        type="submit"
        disabled={
          busy ||
          !form.name.trim() ||
          !form.product.trim() ||
          (isDigital && (!presenterId || !config || !validPresenter(config)))
        }
      >
        <Plus className="mr-2 h-4 w-4" />
        建立制作项目
      </Button>
    </form>
  );
}

type WorkProps = {
  project: ProductionProject;
  busy: boolean;
  act: (
    path: string,
    body?: Record<string, unknown>,
    method?: string,
  ) => Promise<boolean>;
  upload: (
    files: File[],
    kind: "brief" | "asset",
    role?: string,
    shotId?: string,
  ) => Promise<void>;
};

type DirtyReporter = (dirty: boolean) => void;

function useDirtyReport(dirty: boolean, onDirtyChange?: DirtyReporter) {
  useEffect(() => {
    onDirtyChange?.(dirty);
  }, [dirty, onDirtyChange]);
  useEffect(
    () => () => {
      onDirtyChange?.(false);
    },
    [onDirtyChange],
  );
}

function EditMediaOrigin({
  asset,
  busy,
  act,
}: Pick<WorkProps, "busy" | "act"> & { asset: ProductionAsset }) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<ProductionMediaOrigin>(unknownMedia);
  const provider = draft.kind === "aigc" ? draft.provider.trim() : "";
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (next) setDraft(asset.origin ?? unknownMedia);
        setOpen(next);
      }}
    >
      <DialogTrigger asChild>
        <button
          type="button"
          disabled={busy}
          className="text-xs text-muted-foreground underline underline-offset-2 hover:text-foreground disabled:opacity-50"
          aria-label={`标记来源 · ${asset.filename}`}
        >
          标记来源
        </button>
      </DialogTrigger>
      <DialogContent>
        <DialogTitle>标记素材来源</DialogTitle>
        <DialogDescription className="break-all">
          {asset.filename}
        </DialogDescription>
        <form
          className="space-y-4"
          onSubmit={async (event) => {
            event.preventDefault();
            if (busy || (draft.kind === "aigc" && !provider)) return;
            if (
              await act(
                `assets/${asset.id}/origin`,
                {
                  origin: { kind: draft.kind, provider },
                },
                "PATCH",
              )
            )
              setOpen(false);
          }}
        >
          <Field label="素材来源">
            <select
              className={selectClass}
              value={draft.kind}
              onChange={(event) =>
                setDraft({
                  ...draft,
                  kind: event.target.value as ProductionMediaOrigin["kind"],
                })
              }
            >
              {Object.entries(mediaOriginLabels).map(([kind, label]) => (
                <option key={kind} value={kind}>
                  {label}
                </option>
              ))}
            </select>
          </Field>
          {draft.kind === "aigc" && (
            <Field label="实际生成平台">
              <Input
                value={draft.provider}
                onChange={(event) =>
                  setDraft({ ...draft, provider: event.target.value })
                }
                placeholder="例如 HeyGen、TapNow、可灵"
                maxLength={100}
                required
              />
            </Field>
          )}
          <DialogFooter>
            <Button
              type="submit"
              disabled={busy || (draft.kind === "aigc" && !provider)}
            >
              保存来源
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function BriefPanel({ project: p, busy, act, upload }: WorkProps) {
  const [points, setPoints] = useState(p.selling_points);
  const [offer, setOffer] = useState(p.offer);
  const [style, setStyle] = useState(p.brand.style);
  const [notes, setNotes] = useState(p.workflow_notes ?? "");
  const [referenceDuration, setReferenceDuration] = useState(
    p.target_duration?.toString() ?? "",
  );
  const [music, setMusic] = useState(p.music_asset_id || "");
  const [scriptIntent, setScriptIntent] = useState(p.script_intent ?? null);
  const isDigitalPresenter = p.project_type === "digital_presenter";
  const intentChanged =
    isDigitalPresenter && scriptIntent !== (p.script_intent ?? null);
  const planning = p.planning_status === "running";
  const validDuration =
    !referenceDuration ||
    (/^\d+$/.test(referenceDuration) &&
      Number.isSafeInteger(Number(referenceDuration)) &&
      Number(referenceDuration) > 0);
  useEffect(() => setMusic(p.music_asset_id || ""), [p.music_asset_id]);
  useEffect(() => setScriptIntent(p.script_intent ?? null), [p.script_intent]);
  return (
    <div className="grid items-start gap-5 xl:grid-cols-[1.15fr_1fr]">
      <div className="space-y-5">
        <section className={panel}>
          <h2 className="mb-4 font-semibold">品牌与本次目标</h2>
          <div className="space-y-4">
            {isDigitalPresenter && (
              <ScriptIntentField
                value={scriptIntent}
                onChange={setScriptIntent}
                disabled={busy || planning}
              />
            )}
            <Field label="商品事实与主推卖点">
              <Textarea
                value={points}
                onChange={(e) => setPoints(e.target.value)}
                rows={4}
              />
            </Field>
            <Field label="本次价格 / 活动">
              <Input value={offer} onChange={(e) => setOffer(e.target.value)} />
            </Field>
            <Field
              label={isDigitalPresenter ? "口播风格与约束" : "视觉与表达风格"}
            >
              <Textarea
                value={style}
                onChange={(e) => setStyle(e.target.value)}
                rows={2}
              />
            </Field>
            <Field label="背景音乐">
              <select
                className={selectClass}
                value={music}
                onChange={(e) => setMusic(e.target.value)}
              >
                <option value="">不使用背景音乐</option>
                {p.assets
                  .filter((a) => a.role === "music")
                  .map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.filename}
                    </option>
                  ))}
              </select>
            </Field>
            {isDigitalPresenter && (
              <div className="space-y-2 rounded-lg border border-border p-4">
                <Field label="参考片长（秒，选填）">
                  <Input
                    type="number"
                    min={1}
                    step={1}
                    placeholder="留空，按内容和实际配音剪辑"
                    value={referenceDuration}
                    onChange={(e) => setReferenceDuration(e.target.value)}
                  />
                </Field>
                <Field label="工作流与复盘">
                  <Textarea
                    value={notes}
                    onChange={(e) => setNotes(e.target.value)}
                    placeholder="记录风格来源、读音和停顿、素材来源、有效设置与待改事项"
                    rows={7}
                    maxLength={8000}
                  />
                </Field>
                <p className="text-xs text-muted-foreground">
                  定稿后用同一个克隆声音生成出镜口播和画外音；用实拍或 TapNow
                  补画面，回到这里对齐字幕与成片。
                </p>
              </div>
            )}
            <Button
              disabled={
                busy ||
                (isDigitalPresenter && !validDuration) ||
                (intentChanged && planning)
              }
              onClick={() =>
                act(
                  "brief",
                  {
                    selling_points: points,
                    offer,
                    style,
                    music_asset_id: music || null,
                    ...(isDigitalPresenter
                      ? {
                          workflow_notes: notes,
                          target_duration: referenceDuration
                            ? Number(referenceDuration)
                            : null,
                        }
                      : {}),
                    ...(intentChanged ? { script_intent: scriptIntent } : {}),
                  },
                  "PATCH",
                )
              }
            >
              保存资料修改
            </Button>
          </div>
        </section>
        <section className={panel}>
          <h2 className="font-semibold">资料与参考图</h2>
          <p className="mb-4 mt-1 text-sm text-muted-foreground">
            PPT
            会保留逐页文字、原图和来源。产品与主播参考图可用于每个镜头的提示词。
          </p>
          <div className="flex flex-wrap gap-2">
            <UploadButton
              label="上传品牌 PPT / 文本"
              accept=".pptx,.txt,.md"
              disabled={busy}
              onFiles={(f) => upload(f, "brief")}
            />
            <UploadButton
              label="产品参考图"
              accept="image/png,image/jpeg,image/webp"
              multiple
              disabled={busy}
              onFiles={(f) => upload(f, "asset", "product")}
            />
            <UploadButton
              label="主播参考图"
              accept="image/png,image/jpeg,image/webp"
              multiple
              disabled={busy}
              onFiles={(f) => upload(f, "asset", "presenter")}
            />
            <UploadButton
              label="已有视频"
              accept="video/*"
              multiple
              disabled={busy}
              onFiles={(f) => upload(f, "asset")}
            />
            <UploadButton
              label="背景音乐"
              accept="audio/*"
              disabled={busy}
              onFiles={(f) => upload(f, "asset", "music")}
            />
          </div>
          <div className="mt-4 flex flex-wrap gap-3">
            {p.assets
              .filter((a) => a.kind === "image")
              .map((a) => (
                <figure key={a.id} className="w-24">
                  <img
                    src={a.url}
                    alt={a.filename}
                    className="h-24 w-24 rounded-lg object-contain bg-background"
                  />
                  <figcaption className="mt-1 space-y-1 text-xs text-muted-foreground">
                    <div className="truncate">
                      {a.role === "presenter" ? "主播" : "产品"} · {a.filename}
                    </div>
                    <MediaOriginBadge origin={a.origin} />
                    <div>
                      <EditMediaOrigin asset={a} busy={busy} act={act} />
                    </div>
                  </figcaption>
                </figure>
              ))}
          </div>
          {p.warnings.map((w, i) => (
            <p key={i} className="mt-3 text-sm text-amber-300">
              {w}
            </p>
          ))}
        </section>
      </div>
      <section className={panel}>
        <h2 className="mb-4 font-semibold">
          可追溯的商品资料{" "}
          <span className="text-muted-foreground">
            {p.brand.sources.length} 页
          </span>
        </h2>
        {!p.brand.sources.length && (
          <p className="py-12 text-center text-sm text-muted-foreground">
            上传资料后，可在这里查看逐页内容与事实出处。
          </p>
        )}
        <div className="space-y-3">
          {p.brand.sources.map((source) => (
            <details
              key={source.id}
              className="rounded-lg border border-border p-3"
            >
              <summary className="cursor-pointer text-sm">
                {source.filename} · 第 {source.page} 页
              </summary>
              {source.preview ? (
                <figure className="mt-3 space-y-1">
                  <figcaption>
                    <MediaOriginBadge
                      origin={originalMedia}
                      detail="PPT 页面"
                    />
                  </figcaption>
                  <img
                    src={source.preview}
                    alt={`${source.filename} 第 ${source.page} 页`}
                    className="w-full rounded"
                  />
                </figure>
              ) : (
                source.images.length > 0 && (
                  <figure className="mt-3 space-y-1">
                    <figcaption>
                      <MediaOriginBadge
                        origin={originalMedia}
                        detail="PPT 原图"
                      />
                    </figcaption>
                    <div className="flex gap-2 overflow-auto">
                      {source.images.map((img) => (
                        <img
                          src={img}
                          key={img}
                          alt="页面原始图片"
                          className="h-32 object-contain"
                        />
                      ))}
                    </div>
                  </figure>
                )
              )}
              <p className="mt-3 whitespace-pre-wrap text-sm leading-relaxed">
                {source.text || "本页无可提取文字"}
              </p>
              {source.visual_summary && (
                <p className="mt-3 text-sm text-primary">
                  画面解读：{source.visual_summary}
                </p>
              )}
            </details>
          ))}
        </div>
      </section>
    </div>
  );
}

function PresenterProfile({
  presenter,
  project,
  busy,
  act,
  onDirtyChange,
}: {
  presenter: ProductionPresenter;
  onDirtyChange?: DirtyReporter;
} & WorkProps) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<PresenterConfig>(() =>
    presenterConfig(presenter),
  );
  const [resetKey, setResetKey] = useState(0);
  useDirtyReport(
    editing &&
      JSON.stringify(draft) !== JSON.stringify(presenterConfig(presenter)),
    onDirtyChange,
  );
  return (
    <details className={panel}>
      <summary className="cursor-pointer font-semibold">
        本次数字人设定 · {presenter.name}
      </summary>
      <p className="mt-3 text-xs text-muted-foreground">
        当前视频使用的设定副本。修改只影响本项目，下次生成时生效。
      </p>
      {editing ? (
        <div className="mt-4 space-y-4">
          <PresenterFields
            key={resetKey}
            presenterId={presenter.id}
            projectId={project.id}
            value={draft}
            onChange={setDraft}
            disabled={busy}
            compact
          />
          <div className="flex flex-wrap gap-2">
            <Button
              size="sm"
              disabled={
                busy ||
                project.planning_status === "running" ||
                !validPresenter(draft)
              }
              onClick={async () => {
                if (
                  await act(
                    "presenter",
                    { presenter_id: presenter.id, presenter_overrides: draft },
                    "PUT",
                  )
                )
                  setEditing(false);
              }}
            >
              保存本次设定
            </Button>
            <Button
              size="sm"
              variant="ghost"
              disabled={busy}
              onClick={() => setEditing(false)}
            >
              取消
            </Button>
          </div>
        </div>
      ) : (
        <>
          <dl className="mt-3 grid gap-x-6 gap-y-3 text-sm sm:grid-cols-2">
            <div>
              <dt className="text-muted-foreground">主要受众</dt>
              <dd>{presenter.primary_audience}</dd>
            </div>
            {(
              [
                ["文字风格", presenter.text_style],
                ["结构风格", presenter.structure_style],
                ["内容定位", presenter.positioning],
                ["口播习惯", presenter.speaking_style],
                ["画面风格", presenter.visual_style],
                ["表达禁忌", presenter.boundaries],
                [
                  "形象与声音",
                  presenter.heygen?.avatar_id
                    ? `${presenter.heygen.avatar_name} · ${presenter.heygen.voice_name}`
                    : "待绑定",
                ],
              ] as const
            )
              .filter(([, value]) => value)
              .map(([label, value]) => (
                <div key={label} className="min-w-0">
                  <dt className="text-muted-foreground">{label}</dt>
                  <dd className="whitespace-pre-wrap break-words">{value}</dd>
                </div>
              ))}
            <div>
              <dt className="text-muted-foreground">兼顾受众</dt>
              <dd>{presenter.secondary_audience}</dd>
            </div>
            <div className="sm:col-span-2">
              <dt className="text-muted-foreground">常用称呼</dt>
              <dd>{presenter.address.join("、")}</dd>
            </div>
          </dl>
          <ul className="mt-3 list-disc space-y-1.5 pl-5 text-sm leading-relaxed">
            {presenter.guidance.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
          <div className="mt-4 flex flex-wrap items-center gap-3">
            <Button
              size="sm"
              variant="outline"
              disabled={busy || project.planning_status === "running"}
              onClick={() => {
                setDraft(presenterConfig(presenter));
                setResetKey((k) => k + 1);
                setEditing(true);
              }}
            >
              临时编辑本次设定
            </Button>
            <Link href="/presenters" className="text-sm text-primary underline">
              管理数字人档案
            </Link>
          </div>
        </>
      )}
    </details>
  );
}

function SelectionEditor({
  selection,
  assets,
  channel,
  disabled,
  onSave,
  onDirtyChange,
}: {
  selection: ProductionSelection | null;
  assets: ProductionAsset[];
  channel: "video" | "voiceover";
  disabled: boolean;
  onSave: (selection: ProductionSelection) => Promise<boolean>;
  onDirtyChange?: DirtyReporter;
}) {
  const [assetId, setAssetId] = useState(selection?.asset_id || "");
  const [start, setStart] = useState(selection?.start || 0);
  const [end, setEnd] = useState(selection?.end || 0);
  const [saved, setSaved] = useState(selection);
  const selected = assets.find((a) => a.id === assetId);
  useDirtyReport(
    assetId !== (saved?.asset_id ?? "") ||
      start !== (saved?.start ?? 0) ||
      end !== (saved?.end ?? 0),
    onDirtyChange,
  );
  return (
    <div className="rounded-lg bg-background p-3">
      <Field label={channel === "video" ? "画面素材 / 版本" : "独立旁白素材"}>
        <select
          aria-label={channel === "video" ? "画面素材版本" : "旁白素材版本"}
          className={selectClass}
          value={assetId}
          onChange={(e) => {
            setAssetId(e.target.value);
            const a = assets.find((a) => a.id === e.target.value);
            setStart(a?.usable_start || 0);
            setEnd(a?.usable_end || a?.duration || 0);
          }}
        >
          <option value="">选择素材</option>
          {assets.map((a) => (
            <option value={a.id} key={a.id}>
              {a.shot_id ? `${a.shot_id} · ` : ""}v{a.version} · {a.filename} ·{" "}
              {a.duration.toFixed(1)}s
              {a.kind === "video" && ` · ${mediaOriginLabel(a.origin)}`}
            </option>
          ))}
        </select>
      </Field>
      {selected ? (
        <>
          <div className="mt-3 flex flex-wrap items-end gap-2">
            <div className="w-24">
              <Field label="入点（秒）">
                <Input
                  aria-label={`${channel}入点`}
                  type="number"
                  min={0}
                  step="0.01"
                  value={start}
                  onChange={(e) => setStart(Number(e.target.value))}
                />
              </Field>
            </div>
            <div className="w-24">
              <Field label="出点（秒）">
                <Input
                  aria-label={`${channel}出点`}
                  type="number"
                  min={0}
                  step="0.01"
                  value={end}
                  onChange={(e) => setEnd(Number(e.target.value))}
                />
              </Field>
            </div>
            <Button
              size="sm"
              variant="outline"
              disabled={
                disabled || end <= start || end > selected.duration + 0.001
              }
              onClick={async () => {
                const next = { asset_id: assetId, start, end };
                if (await onSave(next)) setSaved(next);
              }}
            >
              使用此选段
            </Button>
          </div>
          <p className="mt-2 text-xs text-muted-foreground">
            实际时长 {selected.duration.toFixed(2)}s ·{" "}
            {selected.has_audio ? "有音轨" : "无音轨"} ·{" "}
            {selected.analysis_note}
          </p>
        </>
      ) : (
        <p className="mt-2 text-xs text-muted-foreground">
          回传或选择{channel === "video" ? "视频" : "旁白音频"}
          后，可按素材设置入点和出点。
        </p>
      )}
    </div>
  );
}

function shotEditValues(shot: ProductionShot) {
  const {
    purpose,
    visual,
    spoken_text,
    subtitle,
    target_duration,
    source_type,
    voice_mode,
    framing,
    trim_pauses,
    tool,
    reference_ids,
  } = shot;
  return {
    purpose,
    visual,
    spoken_text,
    subtitle,
    target_duration,
    source_type,
    voice_mode,
    framing,
    trim_pauses,
    tool,
    reference_ids,
  };
}

function ShotCard({
  shot: s,
  project: p,
  busy,
  act,
  upload,
  studio = false,
  onDirtyChange,
}: WorkProps & {
  shot: ProductionShot;
  studio?: boolean;
  onDirtyChange?: DirtyReporter;
}) {
  const [draft, setDraft] = useState(s);
  const [savedDraft, setSavedDraft] = useState(s);
  const [videoDirty, setVideoDirty] = useState(false);
  const [voiceDirty, setVoiceDirty] = useState(false);
  const draftDirty =
    JSON.stringify(shotEditValues(draft)) !==
    JSON.stringify(shotEditValues(savedDraft));
  useDirtyReport(draftDirty || videoDirty || voiceDirty, onDirtyChange);
  // Saving another channel updates the shot revision without replacing unsaved copy.
  useEffect(() => {
    setDraft((current) =>
      JSON.stringify(shotEditValues(current)) ===
      JSON.stringify(shotEditValues(savedDraft))
        ? s
        : { ...s, ...shotEditValues(current) },
    );
    setSavedDraft(s);
  }, [s]);
  const [inspector, setInspector] = useState<"script" | "visual" | "audio">(
    "script",
  );
  const [editing, setEditing] = useState(false);
  const issues = p.readiness[s.shot_id] || [];
  const selected = p.assets.find((a) => a.id === s.selection?.asset_id);
  const canGenerateOpening =
    !p.narration_draft &&
    p.project_type === "digital_presenter" &&
    !!p.presenter_id &&
    !!p.selected_direction_id &&
    p.shots[0]?.shot_id === s.shot_id &&
    s.source_type === "aigc" &&
    s.voice_mode === "generated" &&
    s.tool.trim().toLowerCase() === "heygen";
  const heygenJob = p.heygen_jobs?.findLast((job) => job.shot_id === s.shot_id);
  const heygenAsset = p.assets.find(
    (asset) => asset.id === heygenJob?.asset_id,
  );
  const change = (key: keyof ProductionShot, value: unknown) =>
    setDraft((old) => ({ ...old, [key]: value }));
  const related = p.assets.filter((a) => a.shot_id === s.shot_id);
  const selectedKey = `${s.selection?.asset_id}:${s.selection?.start}:${s.selection?.end}`;
  const referenceImages = [
    ...p.assets
      .filter((a) => a.kind === "image")
      .map((a) => ({
        id: a.id,
        url: a.url,
        filename: a.filename,
        origin: a.origin,
        detail: undefined,
      })),
    ...p.brand.sources
      .filter((source) => source.preview || source.images.length)
      .map((source) => ({
        id: source.id,
        url: source.preview || source.images[0],
        filename: `${source.filename} 第 ${source.page} 页`,
        origin: originalMedia,
        detail: source.preview ? "PPT 页面" : "PPT 原图",
      })),
  ];
  const shotHeading = (
    <>
      <div className="flex flex-wrap items-center gap-2">
        <span className="rounded bg-primary/15 px-2 py-1 font-mono text-primary">
          {s.shot_id}
        </span>
        <h3 className="font-semibold">{s.purpose}</h3>
        <span className="ml-auto text-xs text-muted-foreground">
          {s.source_type === "aigc" && s.tool
            ? `计划用 ${s.tool}`
            : sourceLabels[s.source_type]}{" "}
          · 预计 {s.target_duration.toFixed(1)} 秒
        </span>
        <span
          className={`rounded-full px-2 py-1 text-xs ${issues.length ? "bg-amber-500/10 text-amber-300" : "bg-emerald-500/10 text-emerald-300"}`}
        >
          {issues.length ? (selected ? "待检查 / 补充" : "待回传") : "可用"}
        </span>
      </div>
    </>
  );
  const spokenContent = (
    <>
      <div className="rounded-lg bg-background p-3 text-sm">
        <p className="mb-1 text-xs text-muted-foreground">
          {s.voice_mode === "generated"
            ? "口播文案（生成用）"
            : voiceLabels[s.voice_mode]}
        </p>
        {s.spoken_text || "无对白"}
      </div>
    </>
  );
  const guidanceContent = (
    <>
      <details className="text-sm">
        <summary className="cursor-pointer text-muted-foreground">
          {s.source_type === "aigc" ? "制作提示词与参考" : "拍摄指导与参考"}
        </summary>
        <p className="mt-3 whitespace-pre-wrap leading-relaxed">
          {s.source_type === "aigc" ? s.prompt : s.filming_guide}
        </p>
        <div className="my-3 flex flex-wrap gap-2">
          {referenceImages
            .filter((a) => s.reference_ids.includes(a.id))
            .map((a) => (
              <figure key={a.id} className="w-24 space-y-1">
                <img
                  src={a.url}
                  alt={a.filename}
                  className="h-20 w-20 rounded object-contain"
                />
                <figcaption>
                  <MediaOriginBadge origin={a.origin} detail={a.detail} />
                </figcaption>
              </figure>
            ))}
        </div>
        <Button
          size="sm"
          variant="outline"
          onClick={async () => {
            try {
              await navigator.clipboard.writeText(s.prompt);
              toast.success("已复制当前镜头提示词");
            } catch {
              toast.error("复制失败，请选择提示词手动复制");
            }
          }}
        >
          <Copy className="mr-2 h-3 w-3" />
          复制完整提示词
        </Button>
      </details>
      {s.fact_ids.length > 0 && (
        <details className="text-xs text-muted-foreground">
          <summary className="cursor-pointer">卖点出处</summary>
          {s.fact_ids.map((id) => {
            const fact = p.brand.facts.find((f) => f.id === id);
            const source = p.brand.sources.find(
              (x) => x.id === fact?.source_id,
            );
            return (
              <p className="mt-2" key={id}>
                {fact?.text || "事实已更新，请重新检查"} · {source?.filename}{" "}
                {source ? `第 ${source.page} 页` : ""}
              </p>
            );
          })}
        </details>
      )}
    </>
  );
  const editingContent = (
    <>
      <Button
        variant="ghost"
        size="sm"
        disabled={busy}
        onClick={() => {
          if (!draftDirty) setDraft(s);
          setEditing(!editing);
        }}
      >
        {editing ? "收起编辑" : "修改文案与镜头"}
      </Button>
      {editing && (
        <div className="space-y-3 rounded-lg border border-primary/30 p-3">
          <Field label="镜头用途">
            <Input
              value={draft.purpose}
              onChange={(e) => change("purpose", e.target.value)}
            />
          </Field>
          <Field label="预期画面">
            <Textarea
              value={draft.visual}
              onChange={(e) => change("visual", e.target.value)}
            />
          </Field>
          <Field label="口播 / 旁白">
            <Textarea
              value={draft.spoken_text}
              onChange={(e) => change("spoken_text", e.target.value)}
            />
          </Field>
          <Field label="无对白时的画面字幕">
            <Input
              value={draft.subtitle}
              onChange={(e) => change("subtitle", e.target.value)}
            />
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="素材来源">
              <select
                className={selectClass}
                value={draft.source_type}
                onChange={(e) => change("source_type", e.target.value)}
              >
                {Object.entries(sourceLabels).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="声音安排">
              <select
                className={selectClass}
                value={draft.voice_mode}
                onChange={(e) => change("voice_mode", e.target.value)}
              >
                {Object.entries(voiceLabels).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="目标秒数">
              <Input
                type="number"
                min={1}
                max={60}
                value={draft.target_duration}
                onChange={(e) =>
                  change("target_duration", Number(e.target.value))
                }
              />
            </Field>
            <Field label="画幅处理">
              <select
                className={selectClass}
                value={draft.framing}
                onChange={(e) => change("framing", e.target.value)}
              >
                <option value="contain">完整展示（留边）</option>
                <option value="cover">填满画面（居中裁切）</option>
              </select>
            </Field>
          </div>
          <Field label="外部生成工具">
            <Input
              value={draft.tool}
              onChange={(e) => change("tool", e.target.value)}
              placeholder="通用 / 可灵 / 即梦 / Runway…"
            />
          </Field>
          <label className="flex items-center gap-2 text-xs">
            <input
              type="checkbox"
              checked={draft.trim_pauses}
              onChange={(e) => change("trim_pauses", e.target.checked)}
            />
            自动收紧对白间的长停顿（动作演示可关闭）
          </label>
          <div className="flex flex-wrap gap-3">
            {referenceImages.map((a) => (
              <label key={a.id} className="text-xs">
                <input
                  type="checkbox"
                  checked={draft.reference_ids.includes(a.id)}
                  onChange={(e) =>
                    change(
                      "reference_ids",
                      e.target.checked
                        ? [...draft.reference_ids, a.id]
                        : draft.reference_ids.filter((id) => id !== a.id),
                    )
                  }
                />{" "}
                {a.filename}
              </label>
            ))}
          </div>
          <Button
            size="sm"
            disabled={busy}
            onClick={async () => {
              const next = { ...s, ...shotEditValues(draft) };
              if (await act(`shots/${s.shot_id}`, { shot: next }, "PUT")) {
                setSavedDraft(next);
                setEditing(false);
              }
            }}
          >
            保存并更新提示词
          </Button>
        </div>
      )}
    </>
  );
  const generationContent = (
    <>
      {(canGenerateOpening || heygenJob) && (
        <HeyGenOpening
          projectId={p.id}
          presenterName={p.presenter_snapshot?.name ?? "数字人"}
          onSaveBinding={(binding) => act("heygen/binding", { binding }, "PUT")}
          job={heygenJob}
          asset={heygenAsset}
          stale={!!heygenJob && heygenJob.shot_revision !== s.revision}
          canGenerate={canGenerateOpening && !editing}
          spokenText={s.spoken_text}
          busy={busy}
          onGenerate={() => act(`shots/${s.shot_id}/heygen`)}
          onResume={() => act(`heygen/${heygenJob?.id}/resume`)}
          onSelect={
            heygenAsset &&
            heygenAsset.id !== selected?.id &&
            heygenAsset.shot_id === s.shot_id
              ? () =>
                  act(`shots/${s.shot_id}/select`, {
                    selection: {
                      asset_id: heygenAsset.id,
                      start: 0,
                      end: heygenAsset.duration,
                    },
                  })
              : undefined
          }
        />
      )}
    </>
  );
  const legacyPreview = (
    <>
      {selected && (
        <figure className="space-y-2">
          <figcaption className="flex flex-wrap items-center gap-2">
            <MediaOriginBadge origin={selected.origin} />
            <EditMediaOrigin asset={selected} busy={busy} act={act} />
          </figcaption>
          <video
            key={selected.id}
            aria-label={`${s.shot_id} 画面预览`}
            src={selected.url}
            controls
            preload="metadata"
            className="max-h-64 w-full rounded-lg bg-black"
          />
        </figure>
      )}
    </>
  );
  const videoSelectionContent = (
    <>
      <SelectionEditor
        key={`video:${selectedKey}`}
        selection={s.selection}
        assets={p.assets.filter((a) => a.kind === "video")}
        channel="video"
        disabled={busy}
        onDirtyChange={setVideoDirty}
        onSave={(selection) => act(`shots/${s.shot_id}/select`, { selection })}
      />
    </>
  );
  const voiceSelectionContent = (
    <>
      {s.voice_mode === "voiceover" && (
        <SelectionEditor
          key={`voice:${s.voiceover?.asset_id}:${s.voiceover?.start}:${s.voiceover?.end}`}
          selection={s.voiceover}
          assets={p.assets.filter((a) => a.has_audio)}
          channel="voiceover"
          disabled={busy}
          onDirtyChange={setVoiceDirty}
          onSave={(selection) =>
            act(`shots/${s.shot_id}/select`, {
              selection,
              channel: "voiceover",
            })
          }
        />
      )}
    </>
  );
  const versionsContent = (
    <>
      {related.length > 0 && (
        <details>
          <summary className="cursor-pointer text-xs text-muted-foreground">
            全部回传版本（{related.length}）
          </summary>
          <div className="mt-2 space-y-2">
            {related.map((a) => (
              <div
                key={a.id}
                className="flex flex-wrap items-center gap-2 text-xs"
              >
                <span className="break-all">
                  v{a.version} · {a.filename} · {a.duration.toFixed(1)}s{" "}
                  {s.selection?.asset_id === a.id ? "· 当前画面" : ""}
                </span>
                {a.kind === "video" && <MediaOriginBadge origin={a.origin} />}
              </div>
            ))}
          </div>
        </details>
      )}
    </>
  );
  const verificationContent = (
    <>
      {issues.length > 0 && (
        <ul className="space-y-1 text-xs text-amber-300">
          {issues.map((issue) => (
            <li key={issue}>{issue}</li>
          ))}
        </ul>
      )}
      {s.review_note && (
        <p className="text-xs text-muted-foreground">{s.review_note}</p>
      )}
      <Button
        size="sm"
        variant="outline"
        disabled={busy || !selected}
        onClick={() => act(`shots/${s.shot_id}/verify`)}
      >
        <CheckCircle2 className="mr-2 h-4 w-4" />
        已检查画面与声音，标记可用
      </Button>
    </>
  );
  const videoUpload = (
    <UploadButton
      label={`回传 ${s.shot_id} 视频`}
      accept="video/*"
      multiple
      disabled={busy}
      onFiles={(f) => upload(f, "asset", "footage", s.shot_id)}
    />
  );
  const voiceUpload =
    s.voice_mode === "voiceover" ? (
      <UploadButton
        label="回传独立旁白"
        accept="audio/*,video/*"
        disabled={busy}
        onFiles={(f) => upload(f, "asset", "voiceover", s.shot_id)}
      />
    ) : null;
  const dropFiles: React.DragEventHandler<HTMLElement> = (event) => {
    event.preventDefault();
    if (!busy && event.dataTransfer.files.length)
      upload(
        Array.from(event.dataTransfer.files),
        "asset",
        "footage",
        s.shot_id,
      );
  };
  return studio ? (
    <article
      aria-label={`${s.shot_id} 当前幕编辑`}
      className="grid min-w-0 flex-1 gap-3 xl:min-h-0 xl:grid-cols-[minmax(0,1fr)_320px]"
      onDragOver={(e) => e.preventDefault()}
      onDrop={dropFiles}
    >
      <section
        className="flex min-h-[400px] min-w-0 flex-col overflow-auto rounded-xl border border-border bg-card/40 xl:min-h-0"
        aria-label="当前幕画面"
      >
        <div className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-3">
          <span className="font-mono text-xs text-primary">{s.shot_id}</span>
          <h3 className="min-w-0 flex-1 truncate text-sm font-semibold">
            {s.purpose}
          </h3>
          <span
            className={`rounded-full px-2 py-1 text-[11px] ${issues.length ? "bg-amber-500/10 text-amber-300" : "bg-emerald-500/10 text-emerald-300"}`}
          >
            {issues.length ? "待处理" : "可用"}
          </span>
        </div>
        <div className="flex min-h-[280px] flex-1 flex-col justify-center p-3 sm:p-5">
          <ProductionShotPreview
            key={selectedKey}
            shot={s}
            asset={selected}
            voiceoverAsset={p.assets.find(
              (asset) => asset.id === s.voiceover?.asset_id,
            )}
          />
        </div>
        <div className="flex flex-wrap items-center gap-2 border-t border-border px-4 py-3 text-xs text-muted-foreground">
          {selected ? (
            <>
              <MediaOriginBadge origin={selected.origin} />
              <EditMediaOrigin asset={selected} busy={busy} act={act} />
            </>
          ) : (
            videoUpload
          )}
          <span className="ml-auto">9:16 · 当前选段预览</span>
        </div>
      </section>
      <aside
        className="flex max-h-[640px] min-h-[400px] min-w-0 flex-col overflow-hidden rounded-xl border border-border bg-card xl:max-h-none xl:min-h-0"
        aria-label="当前幕设置"
      >
        <div
          className="flex shrink-0 gap-1 border-b border-border p-2"
          role="tablist"
          aria-label="当前幕编辑内容"
        >
          {(
            [
              ["script", "文案"],
              ["visual", "画面"],
              ["audio", "声音"],
            ] as const
          ).map(([key, label]) => (
            <button
              key={key}
              type="button"
              role="tab"
              id={`shot-tab-${key}`}
              aria-selected={inspector === key}
              aria-controls={`shot-panel-${key}`}
              onClick={() => setInspector(key)}
              className={`flex-1 rounded-md px-3 py-2 text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary ${inspector === key ? "bg-primary/10 text-primary" : "text-muted-foreground hover:bg-secondary"}`}
            >
              {label}
            </button>
          ))}
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          <div
            className="space-y-4"
            role="tabpanel"
            id="shot-panel-script"
            aria-labelledby="shot-tab-script"
            hidden={inspector !== "script"}
          >
            {spokenContent}
            <p className="text-xs leading-relaxed text-muted-foreground">
              {s.visual}
            </p>
            {editingContent}
          </div>
          <div
            className="space-y-4"
            role="tabpanel"
            id="shot-panel-visual"
            aria-labelledby="shot-tab-visual"
            hidden={inspector !== "visual"}
          >
            <p className="text-sm leading-relaxed">{s.visual}</p>
            <p className="text-xs text-muted-foreground">
              {s.source_type === "aigc" && s.tool
                ? `计划用 ${s.tool}`
                : sourceLabels[s.source_type]}{" "}
              · 预计 {s.target_duration.toFixed(1)} 秒
            </p>
            {videoUpload}
            {videoSelectionContent}
            {guidanceContent}
            {generationContent}
            {versionsContent}
          </div>
          <div
            className="space-y-4"
            role="tabpanel"
            id="shot-panel-audio"
            aria-labelledby="shot-tab-audio"
            hidden={inspector !== "audio"}
          >
            <p className="text-sm font-medium">{voiceLabels[s.voice_mode]}</p>
            <p className="text-xs leading-relaxed text-muted-foreground">
              {s.voice_mode === "voiceover"
                ? "当前幕画面静音，预览时同步播放所选旁白。"
                : s.voice_mode === "silent"
                  ? "当前幕不播放对白。"
                  : "使用当前画面素材中的原有声音。"}
            </p>
            {voiceUpload}
            {voiceSelectionContent}
            {s.voice_mode === "voiceover" && !s.voiceover && (
              <p className="text-xs text-amber-300">
                请先选择独立旁白素材及选段。
              </p>
            )}
          </div>
        </div>
        <div className="max-h-44 shrink-0 space-y-2 overflow-y-auto border-t border-border p-3 [&>button]:w-full">
          {verificationContent}
        </div>
      </aside>
    </article>
  ) : (
    <article
      className={`${panel} space-y-4`}
      onDragOver={(e) => e.preventDefault()}
      onDrop={dropFiles}
    >
      {shotHeading}
      <p className="text-sm leading-relaxed">{s.visual}</p>
      {spokenContent}
      {guidanceContent}
      {editingContent}
      {generationContent}
      {legacyPreview}
      <div className="flex flex-wrap gap-2">
        {videoUpload}
        {voiceUpload}
      </div>
      {videoSelectionContent}
      {voiceSelectionContent}
      {versionsContent}
      {verificationContent}
    </article>
  );
}

function AssetLibrary({ project: p, busy, act, upload }: WorkProps) {
  return (
    <section className={panel}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="font-semibold">素材库与实际字幕</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            批量文件名含 S01、S02…可自动绑定。也可以手动修正，所有版本都会保留。
          </p>
        </div>
        <UploadButton
          label="批量回传视频"
          accept="video/*"
          multiple
          disabled={busy}
          onFiles={(f) => upload(f, "asset")}
        />
      </div>
      <div className="mt-4 space-y-3">
        {p.assets
          .filter((a) => a.kind !== "image")
          .map((a) => (
            <div key={a.id} className="rounded-lg border border-border p-3">
              <div className="flex flex-wrap items-center gap-3">
                <span className="min-w-0 flex-1 break-all text-sm">
                  {a.filename}{" "}
                  <span className="text-muted-foreground">
                    · v{a.version} · {a.duration.toFixed(1)}s · {a.cues.length}{" "}
                    条字幕
                  </span>
                </span>
                {a.kind === "video" && (
                  <>
                    <MediaOriginBadge origin={a.origin} />
                    <EditMediaOrigin asset={a} busy={busy} act={act} />
                  </>
                )}
                <select
                  aria-label={`${a.filename}绑定镜头`}
                  className={`${selectClass} !w-32`}
                  value={a.shot_id || ""}
                  disabled={busy}
                  onChange={(e) =>
                    act(
                      `assets/${a.id}/binding`,
                      { shot_id: e.target.value || null },
                      "PATCH",
                    )
                  }
                >
                  <option value="">未绑定</option>
                  {p.shots.map((s) => (
                    <option key={s.shot_id}>{s.shot_id}</option>
                  ))}
                </select>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={
                    busy ||
                    a.analysis_status === "running" ||
                    a.role === "music"
                  }
                  onClick={() => act(`assets/${a.id}/analyze`)}
                >
                  {a.analysis_status === "running" ? (
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  ) : (
                    <Sparkles className="mr-2 h-4 w-4" />
                  )}
                  分析画面与语音
                </Button>
                {a.has_audio && (
                  <UploadButton
                    label="导入对应 SRT"
                    accept=".srt"
                    disabled={busy}
                    onFiles={async (files) => {
                      await act(`assets/${a.id}/subtitles`, {
                        srt: await files[0].text(),
                      });
                    }}
                  />
                )}
              </div>
              <p className="mt-2 text-xs text-muted-foreground">
                {a.visual_summary || a.analysis_note}
              </p>
              {a.kind === "audio" && (
                <audio
                  src={a.url}
                  controls
                  preload="metadata"
                  className="mt-3 max-w-full"
                />
              )}
              {a.frames.length > 0 && (
                <figure className="mt-3 space-y-1">
                  <figcaption>
                    <MediaOriginBadge origin={a.origin} detail="视频抽帧" />
                  </figcaption>
                  <div className="flex gap-2">
                    {a.frames.map((src) => (
                      <img
                        key={src}
                        src={src}
                        alt={`${a.filename} 抽帧`}
                        className="h-20 min-w-0 rounded object-contain"
                      />
                    ))}
                  </div>
                </figure>
              )}
              {a.cues.length > 0 && (
                <details className="mt-2 text-xs">
                  <summary className="cursor-pointer text-muted-foreground">
                    查看实际音频字幕
                  </summary>
                  <div className="mt-2 max-h-40 overflow-auto space-y-1">
                    {a.cues.map((c, i) => (
                      <p key={i}>
                        {c.start.toFixed(2)}–{c.end.toFixed(2)}s · {c.text}
                      </p>
                    ))}
                  </div>
                </details>
              )}
            </div>
          ))}
      </div>
    </section>
  );
}

function DeliveryPanel({
  project: p,
  busy,
  act,
  onCancel,
}: WorkProps & { onCancel: (id: string) => void }) {
  const blockers = p.shots.flatMap((s) =>
    s.required || s.selection ? p.readiness[s.shot_id] || [] : [],
  );
  if (p.narration_draft)
    blockers.unshift("请到创意与镜头应用新口播稿的分镜后再剪辑");
  const assetsById = new Map(p.assets.map((a) => [a.id, a]));
  const running = p.renders.some(
    (j) => j.status === "queued" || j.status === "running",
  );
  return (
    <div className="space-y-5">
      <section className={panel}>
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <h2 className="font-semibold">一键剪辑成片</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              按当前分镜和素材版本完成画面、声音、中文字幕、品牌与音乐。保留每个成片版本。
            </p>
          </div>
          <Button
            disabled={busy || running || !p.shots.length || blockers.length > 0}
            onClick={() => act("renders")}
          >
            <Film className="mr-2 h-4 w-4" />
            {running ? "正在剪辑" : "一键剪辑"}
          </Button>
        </div>
        {blockers.length > 0 && (
          <ul className="mt-4 grid gap-1 text-sm text-amber-300 sm:grid-cols-2">
            {blockers.map((x, i) => (
              <li key={i}>{x}</li>
            ))}
          </ul>
        )}
        {!p.shots.length && (
          <p className="mt-4 text-sm text-muted-foreground">
            先选择创意并回传镜头素材。
          </p>
        )}
      </section>
      {p.renders.length === 0 && (
        <div className="py-16 text-center text-muted-foreground">
          <Film className="mx-auto mb-3 h-10 w-10 opacity-40" />
          <p>第一条成片将在这里出现</p>
          <p className="mt-2 text-sm">
            MP4、字幕、剪映多轨草稿和镜头来源可一起交付。
          </p>
        </div>
      )}
      {[...p.renders].reverse().map((job, i) => (
        <section key={job.id} className={panel}>
          <div className="flex flex-wrap items-center gap-3">
            <h3 className="font-semibold">成片 {p.renders.length - i}</h3>
            <span
              className={`text-xs ${job.is_current ? "text-primary" : "text-muted-foreground"}`}
            >
              {job.is_current ? "当前方案" : "历史方案，修改后请重新成片"} ·
              版本 {job.content_revision}
            </span>
            <span className="ml-auto text-xs text-muted-foreground">
              {job.timeline.duration.toFixed(1)}s · 第 {job.attempt} 次执行
            </span>
          </div>
          {(job.status === "queued" || job.status === "running") && (
            <div className="mt-4">
              <div className="h-2 overflow-hidden rounded bg-secondary">
                <div
                  className="h-full bg-primary transition-all"
                  style={{ width: `${job.progress * 100}%` }}
                />
              </div>
              <div className="mt-2 flex items-center justify-between text-sm">
                <p>
                  {job.step} · {Math.round(job.progress * 100)}%
                </p>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => onCancel(job.id)}
                >
                  取消剪辑
                </Button>
              </div>
            </div>
          )}
          {job.error && (
            <div className="mt-3">
              <p className="whitespace-pre-wrap text-sm text-red-300">
                {job.error}
              </p>
              {job.is_current && (
                <Button
                  className="mt-3"
                  size="sm"
                  variant="outline"
                  disabled={busy || running}
                  onClick={() => act("renders")}
                >
                  重试这次成片
                </Button>
              )}
            </div>
          )}
          {job.status === "completed" && (
            <div className="mt-4 grid gap-5 md:grid-cols-[280px_1fr]">
              <figure className="space-y-2">
                <figcaption className="text-xs text-muted-foreground">
                  剪辑成片
                  {job.timeline.clips.length > 0 && (
                    <>
                      {" "}
                      · 画面来源{" "}
                      {Array.from(
                        new Set(
                          job.timeline.clips.map((clip) =>
                            mediaOriginLabel(
                              assetsById.get(clip.asset_id)?.origin,
                            ),
                          ),
                        ),
                      ).join("、")}
                    </>
                  )}
                </figcaption>
                <video
                  aria-label="成片预览"
                  src={job.outputs.mp4}
                  controls
                  preload="metadata"
                  className="max-h-[500px] w-full rounded-lg bg-black"
                />
              </figure>
              <div>
                <p className="mb-3 flex items-center gap-2 text-sm text-emerald-300">
                  <CheckCircle2 className="h-4 w-4" />
                  时长、画幅、音视频流与完整解码检查通过
                </p>
                <div className="flex flex-wrap gap-2">
                  {Object.entries({
                    mp4: "下载 MP4",
                    srt: "下载字幕",
                    draft: "剪映草稿",
                    timeline: "镜头来源记录",
                  }).map(
                    ([key, label]) =>
                      job.outputs[key] && (
                        <a
                          key={key}
                          href={job.outputs[key]}
                          download
                          className="inline-flex items-center gap-2 rounded-md border border-border px-3 py-2 text-sm hover:bg-secondary"
                        >
                          <Download className="h-3 w-3" />
                          {label}
                        </a>
                      ),
                  )}
                </div>
                <p className="mt-4 text-xs leading-relaxed text-muted-foreground">
                  剪映草稿包含画面、对白、字幕、品牌和可选音乐轨道。画面已按成片画幅预处理；字体与位置仍需在当前剪映版本中打开复核。
                </p>
                <details className="mt-4 text-sm">
                  <summary className="cursor-pointer">逐镜头追溯</summary>
                  <div className="mt-3 space-y-3">
                    {job.timeline.clips.map((c, n) => (
                      <div key={n} className="rounded border border-border p-3">
                        <p>
                          {c.shot_id} · 成片 {c.start.toFixed(2)}s 起 · 源区间{" "}
                          {c.source_start.toFixed(2)}–{c.source_end.toFixed(2)}s
                        </p>
                        <p className="mt-1 text-xs text-muted-foreground">
                          {assetsById.get(c.asset_id)?.filename} ·{" "}
                          {c.spoken_text}
                        </p>
                        <div className="mt-2">
                          <MediaOriginBadge
                            origin={assetsById.get(c.asset_id)?.origin}
                          />
                        </div>
                        <details className="mt-2 text-xs">
                          <summary className="cursor-pointer">
                            本次制作提示词
                          </summary>
                          <p className="mt-2 whitespace-pre-wrap">{c.prompt}</p>
                        </details>
                      </div>
                    ))}
                  </div>
                </details>
              </div>
            </div>
          )}
        </section>
      ))}
    </div>
  );
}

function DirectionsPanel({ project: p, busy, act }: WorkProps) {
  return (
    <section className={panel}>
      <div className="flex flex-wrap items-center gap-3">
        <div className="min-w-[220px] flex-1">
          <h2 className="font-semibold">选择创意方向</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            根据品牌资料与现有镜头，选择这一条视频的表达方式。
          </p>
        </div>
        <Button
          disabled={
            busy || !!p.narration_draft || p.planning_status === "running"
          }
          onClick={() => act("plan", { mode: "ai" })}
        >
          <Sparkles className="mr-2 h-4 w-4" />
          {p.planning_status === "running" ? "正在生成创意" : "AI 生成创意"}
        </Button>
        <Button
          variant="outline"
          disabled={
            busy || !!p.narration_draft || p.planning_status === "running"
          }
          onClick={() => act("plan", { mode: "template" })}
        >
          使用基础框架
        </Button>
      </div>
      {p.planning_note && (
        <p className="mt-3 text-sm text-muted-foreground">{p.planning_note}</p>
      )}
      <div className="mt-5 grid gap-4 lg:grid-cols-3">
        {p.directions.map((d) => (
          <div
            key={d.id}
            className={`rounded-lg border p-4 ${p.selected_direction_id === d.id ? "border-primary bg-primary/5" : "border-border"}`}
          >
            <h3 className="font-semibold">{d.title}</h3>
            <p className="my-3 text-sm text-primary">“{d.hook}”</p>
            <p className="text-xs leading-relaxed text-muted-foreground">
              {d.structure}
            </p>
            <p className="mt-3 text-xs">{d.new_shots}</p>
            {d.writing_review && (
              <DirectionWritingReview
                review={d.writing_review}
                intentLabel={
                  d.writing_review.script_intent
                    ? scriptIntentLabels[d.writing_review.script_intent]
                    : undefined
                }
              />
            )}
            <Button
              className="mt-4"
              size="sm"
              variant="outline"
              disabled={
                busy || !!p.narration_draft || !!p.selected_direction_id
              }
              onClick={() => act("direction", { direction_id: d.id })}
            >
              {p.selected_direction_id === d.id ? "当前方向" : "选择此方向"}
            </Button>
          </div>
        ))}
      </div>
    </section>
  );
}

function PresenterStudio({
  presenter,
  onDelivery,
  onDirtyChange,
  requestNavigation,
  ...props
}: WorkProps & {
  presenter?: ProductionPresenter;
  onDelivery: () => void;
  onDirtyChange: DirtyReporter;
  requestNavigation: (action: () => void) => void;
}) {
  const p = props.project;
  const [selectedId, setSelectedId] = useState(p.shots[0]?.shot_id ?? "");
  const [tool, setTool] = useState<
    "script" | "direction" | "assets" | "presenter" | null
  >(null);
  const shot =
    p.shots.find((item) => item.shot_id === selectedId) ?? p.shots[0];
  const index = p.shots.indexOf(shot);
  const assetsById = new Map(p.assets.map((asset) => [asset.id, asset]));
  const items: ShotNavigationItem[] = p.shots.map((item) => {
    const asset = assetsById.get(item.selection?.asset_id ?? "");
    const job = p.heygen_jobs?.findLast(
      (entry) => entry.shot_id === item.shot_id,
    );
    const issues = p.readiness[item.shot_id] ?? [];
    const processing =
      (job && isHeyGenActive(job.status)) ||
      asset?.analysis_status === "running";
    const duration = item.selection
      ? item.selection.end - item.selection.start
      : item.target_duration;
    const time = item.selection
      ? item.selection.start + Math.min(0.15, duration / 2)
      : 0;
    return {
      id: item.shot_id,
      title: item.purpose,
      duration,
      thumbnailUrl:
        asset && item.selection
          ? `/api/productions/${encodeURIComponent(p.id)}/assets/${encodeURIComponent(asset.id)}/thumbnail?t=${time.toFixed(3)}&v=${encodeURIComponent(asset.fingerprint)}`
          : undefined,
      status: processing
        ? "processing"
        : !issues.length && asset
          ? "ready"
          : !asset
            ? "pending"
            : "warning",
      detail: issues.join("；") || item.spoken_text,
    };
  });
  const total = items.reduce((sum, item) => sum + item.duration, 0);
  const selectScene = (id: string) => {
    if (!tool && shot?.shot_id === id) return;
    requestNavigation(() => {
      setSelectedId(id);
      setTool(null);
    });
  };
  const move = (nextIndex: number) => {
    if (p.shots[nextIndex]) selectScene(p.shots[nextIndex].shot_id);
  };
  return (
    <section
      aria-label="数字人分幕工作台"
      className="flex min-h-0 flex-1 flex-col gap-3"
    >
      <div className="flex shrink-0 flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold">分镜工作台</h2>
          <span className="text-xs tabular-nums text-muted-foreground">
            {p.shots.length} 幕 · 选段合计 {total.toFixed(1)} 秒
          </span>
        </div>
        <div className="flex flex-wrap gap-1">
          {(
            [
              ["script", "完整口播稿", FileText],
              ["direction", "创意方向", Sparkles],
              ["assets", "素材库", Images],
              ...(presenter
                ? [["presenter", "数字人设定", Users] as const]
                : []),
            ] as const
          ).map(([key, label, Icon]) => (
            <Button
              key={key}
              size="sm"
              variant={tool === key ? "secondary" : "ghost"}
              aria-expanded={tool === key}
              onClick={() =>
                requestNavigation(() => setTool(tool === key ? null : key))
              }
              className={`h-8 px-2 text-xs ${tool === key ? "text-primary" : "text-muted-foreground"}`}
            >
              <Icon className="mr-1.5 h-3.5 w-3.5" />
              {label}
            </Button>
          ))}
          <Button
            size="sm"
            variant="outline"
            className="h-8 px-2 text-xs"
            onClick={() => requestNavigation(onDelivery)}
          >
            成片与交付 <ChevronRight className="ml-1 h-3 w-3" />
          </Button>
        </div>
      </div>
      <div className="flex min-h-0 flex-1 flex-col gap-3 lg:flex-row">
        <div className="flex shrink-0 lg:max-h-[calc(100dvh-240px)] lg:w-[212px] xl:max-h-none xl:w-[224px]">
          <ShotNavigator
            items={items}
            activeId={shot?.shot_id ?? null}
            onSelect={selectScene}
          />
        </div>
        <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-3">
          <div className="flex shrink-0 items-center justify-between gap-2">
            <div className="flex items-center gap-2 text-xs">
              <span className="font-medium">
                {tool
                  ? {
                      script: "完整口播稿",
                      direction: "创意方向",
                      assets: "项目素材库",
                      presenter: "本次数字人设定",
                    }[tool]
                  : `第 ${index + 1} / ${p.shots.length} 幕`}
              </span>
              {tool && (
                <Button
                  size="sm"
                  variant="ghost"
                  className="h-7 px-2 text-xs text-primary"
                  onClick={() => requestNavigation(() => setTool(null))}
                >
                  返回当前幕
                </Button>
              )}
            </div>
            <div className="flex gap-1">
              <Button
                size="sm"
                variant="ghost"
                className="h-7 px-2 text-xs"
                aria-label="上一幕"
                disabled={index <= 0}
                onClick={() => {
                  move(index - 1);
                }}
              >
                <ChevronLeft className="mr-1 h-3 w-3" />
                上一幕
              </Button>
              <Button
                size="sm"
                variant="ghost"
                className="h-7 px-2 text-xs"
                aria-label="下一幕"
                disabled={index >= p.shots.length - 1}
                onClick={() => {
                  move(index + 1);
                }}
              >
                下一幕
                <ChevronRight className="ml-1 h-3 w-3" />
              </Button>
            </div>
          </div>
          {tool ? (
            <div
              className="min-h-0 flex-1 overflow-y-auto"
              aria-label="项目工具"
            >
              {tool === "script" && (
                <DigitalPresenterScript
                  project={p}
                  busy={props.busy}
                  onDirtyChange={onDirtyChange}
                  save={(text) => props.act("narration", { text }, "PUT")}
                  generate={() => props.act("narration/storyboard")}
                  apply={async () => {
                    const applied = await props.act(
                      "narration/storyboard/apply",
                    );
                    if (applied) setTool(null);
                    return applied;
                  }}
                />
              )}
              {tool === "direction" && <DirectionsPanel {...props} />}
              {tool === "assets" && <AssetLibrary {...props} />}
              {tool === "presenter" && presenter && (
                <PresenterProfile
                  presenter={presenter}
                  {...props}
                  onDirtyChange={onDirtyChange}
                />
              )}
            </div>
          ) : shot ? (
            <ShotCard
              key={`${p.id}:${shot.shot_id}`}
              shot={shot}
              studio
              onDirtyChange={onDirtyChange}
              {...props}
            />
          ) : (
            <div className={`${panel} text-sm text-muted-foreground`}>
              生成分镜后，可逐幕选择画面与声音。
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

export default function ProductionWorkbench() {
  const params = useParams<{ id?: string }>();
  const [, navigate] = useLocation();
  const [projects, setProjects] = useState<ProductionSummary[]>([]);
  const [project, setProject] = useState<ProductionProject | null>(null);
  const [presenters, setPresenters] = useState<ProductionPresenter[]>([]);
  const [presenterIssue, setPresenterIssue] = useState("");
  const [creating, setCreating] = useState(() =>
    new URLSearchParams(window.location.search).has("presenter"),
  );
  const [tab, setTab] = useState<"brief" | "shots" | "delivery">("brief");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const studioDirty = useRef(false);
  const reportStudioDirty = useCallback((dirty: boolean) => {
    studioDirty.current = dirty;
  }, []);
  const [pendingNavigation, setPendingNavigation] = useState<
    (() => void) | null
  >(null);
  const requestNavigation = useCallback((action: () => void) => {
    if (studioDirty.current) setPendingNavigation(() => action);
    else action();
  }, []);
  const id = params.id;
  const needsPresenters = !id || project?.project_type === "digital_presenter";
  const presenter =
    project?.presenter_snapshot ??
    presenters.find((item) => item.id === project?.presenter_id);
  const projectRef = useRef(project);
  projectRef.current = project;
  useEffect(() => {
    if (!needsPresenters) return;
    let active = true;
    listProductionPresenters()
      .then((items) => {
        if (!active) return;
        setPresenters(items);
        setPresenterIssue(items.length ? "" : "暂无可选主播");
      })
      .catch(() => {
        if (active) setPresenterIssue("暂时无法读取主播档案");
      });
    return () => {
      active = false;
    };
  }, [needsPresenters]);
  useEffect(() => {
    let active = true;
    listProductions()
      .then((data) => {
        if (active) setProjects(data);
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    return () => {
      active = false;
    };
  }, [id]);
  useEffect(() => {
    let active = true;
    setProject(null);
    setError("");
    if (id) {
      setCreating(false);
      getProduction(id)
        .then((p) => {
          if (active) {
            setProject(p);
            setTab(p.shots.length ? "shots" : "brief");
          }
        })
        .catch((e) => {
          if (active) setError(e.message);
        });
    }
    return () => {
      active = false;
    };
  }, [id]);
  const isWorking =
    project?.planning_status === "running" ||
    project?.heygen_jobs?.some((job) => isHeyGenActive(job.status)) ||
    project?.assets.some((a) => a.analysis_status === "running") ||
    project?.renders.some(
      (j) => j.status === "running" || j.status === "queued",
    );
  useEffect(() => {
    if (!id || !isWorking || busy) return;
    let active = true;
    const timer = setInterval(() => {
      getProduction(id)
        .then((p) => {
          if (active)
            setProject((old) => (old && old.revision > p.revision ? old : p));
        })
        .catch((e) => {
          if (active) setError(e.message);
        });
    }, 2000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [id, isWorking, busy]);
  const perform = async (operation: () => Promise<ProductionProject>) => {
    setBusy(true);
    setError("");
    try {
      const p = await operation();
      setProject(p);
      return p;
    } catch (e) {
      const message = e instanceof Error ? e.message : "操作失败";
      setError(message);
      toast.error(message);
      if (id)
        getProduction(id)
          .then(setProject)
          .catch(() => {});
    } finally {
      setBusy(false);
    }
  };
  const act: WorkProps["act"] = async (path, body = {}, method) => {
    const p = projectRef.current;
    if (p) {
      const saved = await perform(() =>
        productionAction(p, path, body, method),
      );
      if (saved && path === "renders") setTab("delivery");
      return !!saved;
    }
    return false;
  };
  const upload: WorkProps["upload"] = async (
    files,
    kind,
    role = "footage",
    shotId,
  ) => {
    const p = projectRef.current;
    if (!p) return;
    await perform(async () => {
      let next = p;
      for (const file of files) {
        next = await uploadProductionFile(next, file, kind, role, shotId);
        setProject(next);
      }
      toast.success(`已保存 ${files.length} 个文件`);
      return next;
    });
  };
  const ready =
    project?.shots.filter((s) => !(project.readiness[s.shot_id] || []).length)
      .length || 0;
  const props = project ? { project, busy, act, upload } : null;
  const durationLabel = project?.target_duration
    ? `${project.project_type === "digital_presenter" ? "参考" : "目标"} ${project.target_duration} 秒`
    : "按实际内容剪辑";
  const studioActive =
    project?.project_type === "digital_presenter" &&
    project.shots.length > 0 &&
    tab === "shots";
  return (
    <div
      className={
        studioActive
          ? "flex h-full min-h-0 flex-col overflow-y-auto xl:overflow-hidden"
          : "h-full overflow-y-auto"
      }
    >
      <header className="sticky top-0 z-10 shrink-0 border-b border-border bg-background/95 px-4 py-4 backdrop-blur sm:px-6">
        <div className="flex flex-wrap items-center gap-3">
          <Link
            href={id ? "/productions" : "/"}
            aria-label="返回"
            onClick={(event) => {
              if (studioDirty.current && !event.metaKey && !event.ctrlKey) {
                event.preventDefault();
                requestNavigation(() => navigate(id ? "/productions" : "/"));
              }
            }}
          >
            <ArrowLeft className="h-4 w-4 text-muted-foreground" />
          </Link>
          <Clapperboard className="h-5 w-5 text-primary" />
          <div className="min-w-0 flex-1">
            <h1 className="truncate text-lg font-semibold">
              {project?.name || "视频创作"}
            </h1>
            <p className="mt-0.5 text-xs text-muted-foreground">
              {project
                ? [
                    projectTypeLabels[project.project_type ?? "selling_video"],
                    presenter?.name,
                    project.brand.product,
                    project.platform,
                    durationLabel,
                    "1080×1920",
                  ]
                    .filter(Boolean)
                    .join(" · ")
                : "从品牌资料，到一条真实可播放的成片"}
            </p>
          </div>
          {busy && (
            <span
              role="status"
              className="flex items-center gap-2 text-xs text-primary"
            >
              <Loader2 className="h-4 w-4 animate-spin" />
              正在保存 / 处理
            </span>
          )}
          {project ? (
            <span className="text-sm text-primary">
              {ready}/{project.shots.length} 个镜头可用
            </span>
          ) : (
            <Button size="sm" onClick={() => setCreating(true)}>
              <Plus className="mr-2 h-4 w-4" />
              新建制作项目
            </Button>
          )}
        </div>
        {project && (
          <nav className="mt-4 flex flex-wrap gap-2" aria-label="创作阶段">
            {(
              [
                ["brief", "01 品牌资料"],
                ["shots", "02 创意与镜头"],
                ["delivery", "03 成片与交付"],
              ] as const
            ).map(([key, label]) => (
              <Button
                key={key}
                size="sm"
                variant={tab === key ? "secondary" : "ghost"}
                className={
                  tab === key ? "text-primary" : "text-muted-foreground"
                }
                onClick={() => {
                  if (key !== tab) requestNavigation(() => setTab(key));
                }}
              >
                {label}
              </Button>
            ))}
          </nav>
        )}
      </header>
      <div
        className={
          studioActive
            ? "flex w-full shrink-0 flex-col gap-3 p-3 sm:p-4 xl:min-h-0 xl:flex-1"
            : "mx-auto max-w-[1600px] space-y-5 p-4 sm:p-6"
        }
      >
        {error && (
          <div
            role="alert"
            className="rounded-lg border border-red-400/30 bg-red-400/10 p-4 text-sm text-red-200"
          >
            {error}
          </div>
        )}
        {!id && (creating || projects.length === 0) && (
          <NewProject
            projects={projects}
            presenters={presenters}
            presenterIssue={presenterIssue}
            busy={busy}
            onCreate={async (body) => {
              const p = await perform(() => createProduction(body));
              if (p) navigate(`/productions/${p.id}`);
            }}
          />
        )}
        {!id && projects.length > 0 && (
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {projects.map((p) => (
              <Link
                key={p.id}
                href={`/productions/${p.id}`}
                className={`${panel} block transition-colors hover:border-primary/50`}
              >
                <div className="flex items-center justify-between">
                  <Clapperboard className="h-6 w-6 text-primary" />
                  <span className="text-xs text-muted-foreground">
                    {stages[p.stage]}
                  </span>
                </div>
                <h2 className="mt-5 font-semibold">{p.name}</h2>
                <p className="mt-1 text-sm text-muted-foreground">
                  {projectTypeLabels[p.project_type ?? "selling_video"]} ·{" "}
                  {p.product}
                </p>
                <p className="mt-4 text-xs text-primary">
                  {p.ready_count}/{p.shot_count} 个镜头可用 · 继续制作 →
                </p>
              </Link>
            ))}
          </div>
        )}
        {id && !project && !error && (
          <p className="py-20 text-center text-muted-foreground">
            正在读取项目…
          </p>
        )}
        {project && props && tab === "brief" && (
          <>
            {presenter && (
              <PresenterProfile
                key={`${project.id}:${presenter.id}`}
                presenter={presenter}
                {...props}
              />
            )}
            <BriefPanel key={project.id} {...props} />
          </>
        )}
        {project &&
          props &&
          tab === "shots" &&
          (studioActive ? (
            <PresenterStudio
              key={project.id}
              {...props}
              presenter={presenter}
              onDelivery={() => setTab("delivery")}
              onDirtyChange={reportStudioDirty}
              requestNavigation={requestNavigation}
            />
          ) : (
            <>
              {presenter && (
                <PresenterProfile
                  key={`${project.id}:${presenter.id}`}
                  presenter={presenter}
                  {...props}
                />
              )}
              {project.project_type === "digital_presenter" && (
                <DigitalPresenterScript
                  key={project.id}
                  project={project}
                  busy={busy}
                  save={(text) => act("narration", { text }, "PUT")}
                  generate={() => act("narration/storyboard")}
                  apply={() => act("narration/storyboard/apply")}
                />
              )}
              <DirectionsPanel {...props} />
              {project.shots.length > 0 && (
                <>
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <h2 className="font-semibold">
                      分镜工作台{" "}
                      <span className="ml-2 text-sm font-normal text-muted-foreground">
                        拖入视频到对应镜头；离开后可继续
                      </span>
                    </h2>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => setTab("delivery")}
                    >
                      查看成片准备情况 →
                    </Button>
                  </div>
                  <div className="grid items-start gap-5 lg:grid-cols-2 2xl:grid-cols-3">
                    {project.shots.map((s) => (
                      <ShotCard
                        key={`${s.shot_id}:${s.revision}`}
                        shot={s}
                        {...props}
                      />
                    ))}
                  </div>
                </>
              )}
              <AssetLibrary {...props} />
            </>
          ))}
        {project && props && tab === "delivery" && (
          <DeliveryPanel
            {...props}
            onCancel={(jobId) =>
              perform(() => cancelProductionRender(project.id, jobId))
            }
          />
        )}
      </div>
      <Dialog
        open={pendingNavigation !== null}
        onOpenChange={(open) => {
          if (!open) setPendingNavigation(null);
        }}
      >
        <DialogContent>
          <DialogTitle>未保存当前修改</DialogTitle>
          <DialogDescription>
            切换后会放弃尚未保存的文案、选段或数字人设定。可以继续编辑并保存，也可以放弃修改后切换。
          </DialogDescription>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setPendingNavigation(null)}
            >
              继续编辑
            </Button>
            <Button
              onClick={() => {
                const next = pendingNavigation;
                setPendingNavigation(null);
                reportStudioDirty(false);
                next?.();
              }}
            >
              放弃修改并切换
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
