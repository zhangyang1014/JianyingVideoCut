import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import workflow from "../../../../domains/goldenclip/presenter_workflow.json";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import { WritingRuleViewer } from "./WritingRuleViewer";
import {
  getHeyGenCatalog,
  type HeyGenCatalog,
  type PresenterConfig,
} from "@/lib/productions-api";

const selectClass =
  "h-9 w-full rounded-md border border-input bg-background px-2 text-sm";

function PresenterModule({
  number,
  title,
  skill,
  timing,
  label,
  collapsible = false,
  children,
}: {
  number: string;
  title: string;
  skill?: { name: string; title: string };
  timing: string;
  label?: string;
  collapsible?: boolean;
  children: ReactNode;
}) {
  const headingId = useId();
  const heading = (
    <div className="flex min-w-0 items-start gap-3">
      <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-primary/10 font-mono text-sm font-semibold text-primary">
        {number}
      </span>
      <div className="min-w-0 space-y-2">
        <h3
          id={headingId}
          className="text-lg font-semibold leading-snug sm:text-xl"
        >
          {skill ? `${title} · ${skill.title}` : title}
        </h3>
        {skill ? (
          <p className="break-words text-xs text-primary/85">
            Skill：<code className="break-all">{skill.name}</code>
          </p>
        ) : (
          <p className="text-xs font-medium text-primary/85">{label}</p>
        )}
      </div>
    </div>
  );
  const body = (
    <>
      <p className="mb-5 mt-3 text-sm leading-relaxed text-muted-foreground">
        {timing}
      </p>
      {children}
    </>
  );
  const className =
    "rounded-xl border border-border bg-background/25 p-4 sm:p-5";
  return collapsible ? (
    <details className={className}>
      <summary className="cursor-pointer list-none [&::-webkit-details-marker]:hidden">
        {heading}
        <span className="mt-2 block text-xs text-muted-foreground">
          点击展开或收起设定
        </span>
      </summary>
      {body}
    </details>
  ) : (
    <section className={className} aria-labelledby={headingId}>
      {heading}
      {body}
    </section>
  );
}

function ListField({
  label,
  value,
  onChange,
  disabled,
  required = false,
}: {
  label: string;
  value: string[];
  onChange: (value: string[]) => void;
  disabled: boolean;
  required?: boolean;
}) {
  const [text, setText] = useState(() => value.join("\n"));
  useEffect(() => {
    setText((old) =>
      JSON.stringify(
        old
          .split("\n")
          .map((x) => x.trim())
          .filter(Boolean),
      ) === JSON.stringify(value)
        ? old
        : value.join("\n"),
    );
  }, [value]);
  return (
    <label className="space-y-1.5 text-sm">
      <span className="text-muted-foreground">{label}</span>
      <Textarea
        aria-label={label}
        value={text}
        rows={3}
        disabled={disabled}
        required={required}
        maxLength={label === "观众称呼" ? 6000 : 12000}
        placeholder="每行一条"
        onChange={(e) => {
          setText(e.target.value);
          onChange(
            e.target.value
              .split("\n")
              .map((x) => x.trim())
              .filter(Boolean),
          );
        }}
      />
    </label>
  );
}

export function PresenterFields({
  value,
  onChange,
  disabled = false,
  compact = false,
  presenterId,
  projectId,
}: {
  value: PresenterConfig;
  onChange: (config: PresenterConfig) => void;
  disabled?: boolean;
  compact?: boolean;
  presenterId?: string;
  projectId?: string;
}) {
  const [catalog, setCatalog] = useState<HeyGenCatalog>();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const catalogLock = useRef(false);
  const set = (key: keyof PresenterConfig, next: string | string[]) =>
    onChange({ ...value, [key]: next });
  const field = (
    key: keyof PresenterConfig,
    label: string,
    placeholder: string,
    rows = 3,
    required = false,
  ) => (
    <label className="flex min-w-0 flex-col gap-1.5 text-sm" key={key}>
      <span className="text-muted-foreground">{label}</span>
      <Textarea
        aria-label={label}
        value={value[key] as string}
        disabled={disabled}
        required={required}
        maxLength={
          key === "text_style" || key === "structure_style" ? 12000 : 4000
        }
        rows={rows}
        placeholder={placeholder}
        onChange={(e) => set(key, e.target.value)}
      />
    </label>
  );
  const structureSkill =
    presenterId === "lina"
      ? workflow.creators.lina.structure
      : workflow.structure;

  return (
    <div className="space-y-6">
      <p className="rounded-lg border border-primary/20 bg-primary/5 px-4 py-3 text-sm leading-relaxed text-muted-foreground">
        AI 生成创意时：读取档案 → 结构写稿 → 文字润色 →
        镜头提示词。文本框是可编辑设定；完整 Skill
        和补充规则可在对应模块展开查看。
      </p>
      <PresenterModule
        number="01"
        title="基础档案与受众"
        label="共享档案 · 起稿与润色共用"
        timing="先确定对谁说、以什么身份说、主要讲什么；后面的写稿阶段引用同一份档案。"
      >
        <div className="grid gap-4 sm:grid-cols-2">
          <label className="flex flex-col gap-1.5 text-sm">
            <span className="text-muted-foreground">数字人名称</span>
            <Input
              aria-label="数字人名称"
              value={value.name}
              disabled={disabled}
              required
              maxLength={120}
              placeholder="例如：Lina"
              onChange={(e) => set("name", e.target.value)}
            />
          </label>
          <ListField
            label="观众称呼"
            value={value.address}
            onChange={(v) => set("address", v)}
            disabled={disabled}
            required
          />
          {field(
            "primary_audience",
            "主要受众",
            "观众是谁、所处阶段，以及最关心的需求",
            3,
            true,
          )}
          {field("secondary_audience", "兼顾受众", "可以兼顾的观众及适用场景")}
          <div className="sm:col-span-2">
            {field(
              "positioning",
              "内容定位",
              "擅长的话题、身份定位，以及希望给观众的帮助",
            )}
          </div>
        </div>
      </PresenterModule>
      <PresenterModule
        number="02"
        title="结构风格"
        skill={structureSkill}
        timing={`调用时机：${structureSkill.timing}`}
      >
        {field(
          "structure_style",
          "结构风格",
          "开场怎么起、首次回答放在哪里、正文如何分层、结尾怎样收束",
          5,
        )}
        <WritingRuleViewer
          key={`structure:${presenterId}:${projectId}`}
          stage="structure"
          presenterId={presenterId}
          projectId={projectId}
        />
      </PresenterModule>
      <PresenterModule
        number="03"
        title="文字与口播"
        skill={workflow.polish}
        timing={`调用时机：${workflow.polish.timing}`}
      >
        {presenterId === "lina" && (
          <p className="mb-4 text-sm text-muted-foreground">
            本数字人同时引用「{workflow.creators.lina.polish_overlay.title}
            」专属语气规则。
          </p>
        )}
        <div className="grid gap-4 sm:grid-cols-2">
          {field(
            "text_style",
            "文字风格",
            "用词、句式、语气，以及举例和解释的习惯",
            5,
          )}
          {field(
            "speaking_style",
            "口播习惯",
            "语速、停顿、重音与口头语的使用习惯",
            5,
          )}
        </div>
        <WritingRuleViewer
          key={`polish:${presenterId}:${projectId}`}
          stage="polish"
          presenterId={presenterId}
          projectId={projectId}
        />
      </PresenterModule>
      <PresenterModule
        number="04"
        title="画面、形象与声音"
        label="分镜规划与工具绑定 · HeyGen"
        timing="分镜规划与镜头提示词读取画面风格；生成出镜口播时使用本次绑定的形象和声音。"
        collapsible={compact}
      >
        <div className="mb-4">
          {field(
            "visual_style",
            "画面风格",
            "出镜方式、服装、背景与 B-roll 场景偏好",
          )}
        </div>
        <details className="rounded-lg border border-border p-3">
          <summary className="cursor-pointer text-sm font-medium">
            数字人形象与声音
            {value.heygen.avatar_id
              ? ` · ${value.heygen.avatar_name || "已绑定形象"} / ${value.heygen.voice_name || "已绑定声音"}`
              : " · 待绑定"}
          </summary>
          <p className="mt-3 text-xs leading-relaxed text-muted-foreground">
            选择已授权的 HeyGen 私人形象和声音。API 连接可在项目的 HeyGen
            开场中配置；此处只保存素材绑定。
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={disabled || loading}
              onClick={async () => {
                if (catalogLock.current) return;
                catalogLock.current = true;
                setLoading(true);
                setError("");
                try {
                  setCatalog(await getHeyGenCatalog(""));
                } catch (e) {
                  setError(
                    e instanceof Error ? e.message : "暂时无法读取形象和声音",
                  );
                } finally {
                  catalogLock.current = false;
                  setLoading(false);
                }
              }}
            >
              {loading ? "正在读取…" : "读取可用形象与声音"}
            </Button>
            {value.heygen.avatar_id && (
              <Button
                type="button"
                size="sm"
                variant="ghost"
                disabled={disabled}
                onClick={() =>
                  onChange({
                    ...value,
                    heygen: {
                      avatar_id: "",
                      avatar_name: "",
                      voice_id: "",
                      voice_name: "",
                      engine: "",
                    },
                  })
                }
              >
                清除绑定
              </Button>
            )}
          </div>
          <div className="mt-3 grid gap-4 sm:grid-cols-2">
            <label className="space-y-1.5 text-sm">
              <span>HeyGen 形象</span>
              <select
                className={selectClass}
                aria-label="HeyGen 形象"
                disabled={disabled || !catalog}
                value={value.heygen.avatar_id}
                onChange={(e) => {
                  const item = catalog?.avatars.find(
                    (x) => x.id === e.target.value,
                  );
                  onChange({
                    ...value,
                    heygen: {
                      ...value.heygen,
                      avatar_id: item?.id ?? "",
                      avatar_name: item?.name ?? "",
                      engine: item?.engine ?? "",
                    },
                  });
                }}
              >
                <option value="">选择形象（可稍后绑定）</option>
                {value.heygen.avatar_id &&
                  !catalog?.avatars.some(
                    (x) => x.id === value.heygen.avatar_id,
                  ) && (
                    <option value={value.heygen.avatar_id}>
                      {value.heygen.avatar_name || "当前绑定形象"}
                    </option>
                  )}
                {catalog?.avatars.map((x) => (
                  <option key={x.id} value={x.id}>
                    {x.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="space-y-1.5 text-sm">
              <span>HeyGen 声音</span>
              <select
                className={selectClass}
                aria-label="HeyGen 声音"
                disabled={disabled || !catalog}
                value={value.heygen.voice_id}
                onChange={(e) => {
                  const item = catalog?.voices.find(
                    (x) => x.id === e.target.value,
                  );
                  onChange({
                    ...value,
                    heygen: {
                      ...value.heygen,
                      voice_id: item?.id ?? "",
                      voice_name: item?.name ?? "",
                    },
                  });
                }}
              >
                <option value="">选择声音（可稍后绑定）</option>
                {value.heygen.voice_id &&
                  !catalog?.voices.some(
                    (x) => x.id === value.heygen.voice_id,
                  ) && (
                    <option value={value.heygen.voice_id}>
                      {value.heygen.voice_name || "当前绑定声音"}
                    </option>
                  )}
                {catalog?.voices.map((x) => (
                  <option key={x.id} value={x.id}>
                    {x.name}
                  </option>
                ))}
              </select>
            </label>
          </div>
          {Boolean(value.heygen.avatar_id) !==
            Boolean(value.heygen.voice_id) && (
            <p role="alert" className="mt-2 text-xs text-amber-300">
              请同时选择形象和声音，或同时留空。
            </p>
          )}
          {error && (
            <p role="alert" className="mt-2 text-sm text-amber-300">
              {error}
            </p>
          )}
        </details>
      </PresenterModule>
      <PresenterModule
        number="05"
        title="表达边界与补充要求"
        label="共用约束 · 起稿与润色都引用"
        timing="指定哪些表达和经历不能使用，以及本数字人需要长期保留的要求。"
        collapsible={compact}
      >
        <div className="grid gap-4 sm:grid-cols-2">
          {field(
            "boundaries",
            "表达禁忌",
            "不使用的表达、不能虚构的经历及需要说明的适用条件",
            5,
          )}
          <ListField
            label="补充要求"
            value={value.guidance}
            onChange={(v) => set("guidance", v)}
            disabled={disabled}
          />
        </div>
      </PresenterModule>
    </div>
  );
}
