import { useEffect, useRef, useState } from "react";
import { Link } from "wouter";
import { ArrowLeft, Plus, Save, Users, Video } from "lucide-react";
import { Button } from "@/components/ui/button";
import { PresenterFields } from "@/components/production/PresenterFields";
import {
  createProductionPresenter,
  listProductionPresenters,
  updateProductionPresenter,
  type PresenterConfig,
  type ProductionPresenter,
} from "@/lib/productions-api";
import {
  mergePresenterDraft,
  presenterConfig,
  presenterFieldLabels,
  presenterValidationMessage,
  validPresenter,
} from "@/lib/presenters";

type Draft = { profile?: ProductionPresenter; value: PresenterConfig };
type Resolution = ReturnType<typeof mergePresenterDraft> & {
  latest: ProductionPresenter;
};
const showValue = (value: PresenterConfig[keyof PresenterConfig]) =>
  Array.isArray(value)
    ? value.join("\n")
    : typeof value === "string"
      ? value
      : `${value.avatar_name || value.avatar_id} / ${value.voice_name || value.voice_id}`;

function PresenterEditor({
  profile,
  draft,
  onChange,
  onRebased,
  onBusy,
  onSaved,
}: {
  profile?: ProductionPresenter;
  draft: PresenterConfig;
  onChange: (value: PresenterConfig) => void;
  onRebased: (profile: ProductionPresenter, value: PresenterConfig) => void;
  onBusy: (busy: boolean) => void;
  onSaved: (profile: ProductionPresenter) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [needsReload, setNeedsReload] = useState(false);
  const [resolution, setResolution] = useState<Resolution>();
  const [choices, setChoices] = useState<
    Partial<Record<keyof PresenterConfig, "local" | "latest">>
  >({});
  const lock = useRef(false);
  const feedbackRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (error || resolution)
      feedbackRef.current?.scrollIntoView?.({
        block: "center",
        behavior: "smooth",
      });
  }, [error, resolution]);
  const validation = presenterValidationMessage(draft);
  const dirty =
    JSON.stringify(draft) !== JSON.stringify(presenterConfig(profile));
  const setWorking = (value: boolean) => {
    setBusy(value);
    onBusy(value);
  };
  const save = async (archived = profile?.archived ?? false) => {
    if (lock.current || validation || needsReload || resolution) return;
    lock.current = true;
    setWorking(true);
    setError("");
    try {
      const result = profile
        ? await updateProductionPresenter(profile, draft, archived)
        : await createProductionPresenter(draft);
      onSaved(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : "数字人档案保存失败");
      if (
        profile &&
        e &&
        typeof e === "object" &&
        "status" in e &&
        (e.status === 409 ||
          e.status === 408 ||
          e.status === 0 ||
          e.status === 502)
      )
        setNeedsReload(true);
    } finally {
      lock.current = false;
      setWorking(false);
    }
  };
  const reload = async () => {
    if (!profile || lock.current) return;
    lock.current = true;
    setWorking(true);
    try {
      const latest = (await listProductionPresenters(true)).find(
        (p) => p.id === profile.id,
      );
      if (!latest)
        throw new Error("该数字人已不存在，本次编辑仍保留在页面中。");
      const result = mergePresenterDraft(
        presenterConfig(profile),
        draft,
        presenterConfig(latest),
      );
      setError("");
      setChoices({});
      if (result.conflicts.length) setResolution({ ...result, latest });
      else {
        onRebased(latest, result.merged);
        setNeedsReload(false);
      }
    } catch (e) {
      setError(
        e instanceof Error ? e.message : "最新档案暂时无法读取，请重试。",
      );
    } finally {
      lock.current = false;
      setWorking(false);
    }
  };
  return (
    <form
      className="min-w-0 space-y-5 rounded-xl border border-border bg-card p-4 sm:p-6"
      onSubmit={(e) => {
        e.preventDefault();
        void save();
      }}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">
            {profile ? `${profile.name} 的档案` : "新建数字人"}
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">
            在这里维护长期设定，创建视频时可按本次内容临时调整。
          </p>
        </div>
        {profile && !profile.archived && !dirty && !busy && (
          <Link
            className="flex items-center gap-2 text-sm text-primary"
            href={`/productions?presenter=${encodeURIComponent(profile.id)}`}
          >
            <Video className="h-4 w-4" />
            以此数字人创建视频
          </Link>
        )}
      </div>
      <div ref={feedbackRef} className="space-y-4">
        {error && (
          <p
            role="alert"
            className="rounded-lg border border-red-400/30 bg-red-400/10 p-3 text-sm text-red-200"
          >
            {error}
          </p>
        )}
        {needsReload && !resolution && (
          <div className="space-y-2 rounded-lg border border-amber-400/30 p-3 text-sm">
            <p>
              本次编辑已保留。读取最新档案后会合并未冲突的修改，有冲突的字段由你选择。
            </p>
            <Button
              type="button"
              variant="outline"
              disabled={busy}
              onClick={() => void reload()}
            >
              读取最新档案并保留编辑
            </Button>
          </div>
        )}
        {resolution && (
          <div
            role="region"
            aria-label="处理档案冲突"
            className="space-y-4 rounded-lg border border-amber-400/30 p-4 text-sm"
          >
            <p>以下字段在两处都有修改，请选择要保留的内容，再保存档案。</p>
            {resolution.conflicts.map((key) => (
              <fieldset key={key} className="min-w-0 space-y-2">
                <legend className="font-medium">
                  {presenterFieldLabels[key]}
                </legend>
                {(["local", "latest"] as const).map((source) => (
                  <label
                    key={source}
                    className="flex items-start gap-2 rounded-md border border-border p-3"
                  >
                    <input
                      type="radio"
                      name={`conflict-${key}`}
                      checked={choices[key] === source}
                      onChange={() =>
                        setChoices((old) => ({ ...old, [key]: source }))
                      }
                    />
                    <span className="min-w-0 whitespace-pre-wrap break-words">
                      {source === "local" ? "本次编辑" : "最新档案"}：
                      {showValue(
                        source === "local"
                          ? draft[key]
                          : resolution.latest[key],
                      ) || "（留空）"}
                    </span>
                  </label>
                ))}
              </fieldset>
            ))}
            <Button
              type="button"
              disabled={resolution.conflicts.some((key) => !choices[key])}
              onClick={() => {
                const value = { ...resolution.merged };
                for (const key of resolution.conflicts)
                  if (choices[key] === "latest")
                    Object.assign(value, { [key]: resolution.latest[key] });
                onRebased(resolution.latest, value);
                setResolution(undefined);
                setNeedsReload(false);
              }}
            >
              确认选择并继续编辑
            </Button>
          </div>
        )}
      </div>
      <PresenterFields
        value={draft}
        presenterId={profile?.id}
        disabled={busy || !!resolution}
        onChange={onChange}
      />
      <div className="sticky bottom-0 z-10 flex flex-wrap items-center justify-between gap-3 border-t border-border bg-card py-4">
        <Button
          type="submit"
          disabled={
            busy || !validPresenter(draft) || needsReload || !!resolution
          }
        >
          <Save className="mr-2 h-4 w-4" />
          {busy ? (needsReload ? "正在读取…" : "正在保存…") : "保存数字人档案"}
        </Button>
        {profile && (
          <Button
            type="button"
            variant="ghost"
            disabled={
              busy || !validPresenter(draft) || needsReload || !!resolution
            }
            onClick={() => void save(!profile.archived)}
          >
            {profile.archived ? "重新启用" : "停用数字人"}
          </Button>
        )}
      </div>
      {validation && <p className="text-sm text-amber-300">{validation}</p>}
      {dirty && (
        <p role="status" className="text-xs text-amber-300">
          有未保存的修改；切换档案后可继续编辑。保存后可创建视频。
        </p>
      )}
      <p className="text-xs leading-relaxed text-muted-foreground">
        保存后用于新视频。已创建的视频保留各自的设定；停用后不再出现在新建视频的选择中。
      </p>
    </form>
  );
}

export default function PresentersPage() {
  const [profiles, setProfiles] = useState<ProductionPresenter[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [busy, setBusy] = useState(false);
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    listProductionPresenters(true)
      .then((items) => {
        if (!active) return;
        setProfiles(items);
        setSelected((old) => old ?? items[0]?.id ?? "new");
      })
      .catch((e) => {
        if (active) setError(e.message);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [attempt]);
  useEffect(() => {
    if (!Object.keys(drafts).length && !busy) return;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [drafts, busy]);
  const current = profiles.find((p) => p.id === selected);
  const session = selected
    ? (drafts[selected] ?? {
        profile: current,
        value: presenterConfig(current),
      })
    : undefined;
  return (
    <div className="h-full overflow-y-auto">
      <header className="border-b border-border px-4 py-5 sm:px-6">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-3">
          <Link href="/productions" aria-label="返回视频创作">
            <ArrowLeft className="h-4 w-4 text-muted-foreground" />
          </Link>
          <Users className="h-5 w-5 text-primary" />
          <div className="min-w-[180px] flex-1">
            <h1 className="text-lg font-semibold">数字人管理</h1>
            <p className="mt-1 text-sm text-muted-foreground">
              统一维护每位数字人的受众、表达风格、形象与声音。
            </p>
          </div>
          <Button
            disabled={loading || busy || !!error}
            onClick={() => {
              setSelected("new");
              setStatus("");
            }}
          >
            <Plus className="mr-2 h-4 w-4" />
            新建数字人
          </Button>
        </div>
      </header>
      <div className="mx-auto max-w-6xl p-4 sm:p-6">
        {error && (
          <div
            role="alert"
            className="mb-4 space-y-3 rounded-lg border border-red-400/30 p-4 text-sm text-red-300"
          >
            <p>{error}</p>
            <Button
              variant="outline"
              disabled={loading}
              onClick={() => setAttempt((n) => n + 1)}
            >
              重新读取数字人档案
            </Button>
          </div>
        )}
        {status && (
          <p role="status" className="mb-4 text-sm text-primary">
            {status}
          </p>
        )}
        {loading ? (
          <p className="py-12 text-center text-muted-foreground">
            正在读取数字人档案…
          </p>
        ) : (
          !error && (
            <div className="grid items-start gap-5 lg:grid-cols-[240px_minmax(0,1fr)]">
              <nav
                aria-label="数字人档案"
                className="flex gap-3 overflow-x-auto pb-1 lg:flex-col"
              >
                {profiles.map((profile) => (
                  <button
                    key={profile.id}
                    type="button"
                    disabled={busy}
                    aria-pressed={selected === profile.id}
                    onClick={() => {
                      setSelected(profile.id);
                      setStatus("");
                    }}
                    className={`w-60 max-w-full shrink-0 rounded-xl border p-4 text-left transition-colors lg:w-auto ${selected === profile.id ? "border-primary/60 bg-primary/10" : "border-border bg-card hover:border-primary/40"}`}
                  >
                    <span className="flex items-center justify-between gap-2 font-medium">
                      {profile.name}
                      {profile.archived && (
                        <span className="text-xs text-muted-foreground">
                          已停用
                        </span>
                      )}
                    </span>
                    <span className="mt-2 line-clamp-2 block text-xs leading-relaxed text-muted-foreground">
                      {profile.primary_audience}
                    </span>
                  </button>
                ))}
                {!profiles.length && (
                  <p className="text-sm text-muted-foreground">
                    还没有数字人，先建立第一份档案。
                  </p>
                )}
              </nav>
              {selected && session && (
                <PresenterEditor
                  key={selected}
                  profile={session.profile}
                  draft={session.value}
                  onBusy={setBusy}
                  onChange={(value) =>
                    setDrafts((old) => ({
                      ...old,
                      [selected]: { ...session, value },
                    }))
                  }
                  onRebased={(profile, value) => {
                    setProfiles((old) =>
                      old.map((p) => (p.id === profile.id ? profile : p)),
                    );
                    setDrafts((old) => ({
                      ...old,
                      [selected]: { profile, value },
                    }));
                    setStatus("已读取最新档案并保留编辑，请检查后保存。");
                  }}
                  onSaved={(profile) => {
                    setDrafts((old) => {
                      const next = { ...old };
                      delete next[selected];
                      return next;
                    });
                    setProfiles((old) =>
                      old.some((p) => p.id === profile.id)
                        ? old.map((p) => (p.id === profile.id ? profile : p))
                        : [...old, profile],
                    );
                    setSelected(profile.id);
                    setStatus(`${profile.name} 的档案已保存`);
                  }}
                />
              )}
            </div>
          )
        )}
      </div>
    </div>
  );
}
