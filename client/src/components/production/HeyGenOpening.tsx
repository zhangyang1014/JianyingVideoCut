import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  getHeyGenCatalog,
  getHeyGenSettings,
  saveHeyGenSettings,
  type HeyGenCatalog,
  type HeyGenJob,
  type HeyGenSettings,
  type ProductionAsset,
  type PresenterHeyGen,
} from "@/lib/productions-api";

const statusLabels: Record<HeyGenJob["status"], string> = {
  submitting: "正在提交",
  processing: "HeyGen 正在生成",
  importing: "正在下载并导入成片",
  completed: "成片已导入",
  paused: "等待继续",
  failed: "生成失败",
};

export function isHeyGenActive(status: HeyGenJob["status"]) {
  return status === "submitting" || status === "processing" || status === "importing";
}

export function HeyGenOpening({
  job, asset, stale, canGenerate, spokenText, busy, onGenerate, onResume, onSelect,
  projectId, presenterName = "数字人", onSaveBinding,
}: {
  projectId?: string;
  presenterName?: string;
  onSaveBinding?: (binding: PresenterHeyGen) => Promise<boolean>;
  job?: HeyGenJob;
  asset?: ProductionAsset;
  stale: boolean;
  canGenerate: boolean;
  spokenText: string;
  busy: boolean;
  onGenerate: () => Promise<boolean>;
  onResume: () => Promise<boolean>;
  onSelect?: () => Promise<boolean>;
}) {
  const [dialog, setDialog] = useState<"settings" | "generate" | null>(null);
  const [settings, setSettings] = useState<HeyGenSettings>();
  const [catalog, setCatalog] = useState<HeyGenCatalog>();
  const [key, setKey] = useState("");
  const [avatar, setAvatar] = useState("");
  const [voice, setVoice] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const ready = !!(settings?.has_key && settings.avatar_id && settings.voice_id);
  const active = !!job && isHeyGenActive(job.status);

  useEffect(() => {
    let mounted = true;
    getHeyGenSettings(projectId).then((value) => {
      if (mounted) setSettings(value);
    }).catch(() => {});
    return () => { mounted = false; };
  }, [projectId]);

  const openSettings = () => {
    setKey("");
    setAvatar(settings?.avatar_id ?? "");
    setVoice(settings?.voice_id ?? "");
    setCatalog(undefined);
    setError("");
    setDialog("settings");
  };

  const loadChoices = async () => {
    setLoading(true);
    setError("");
    setCatalog(undefined);
    try {
      const choices = await getHeyGenCatalog(key);
      setCatalog(choices);
      setAvatar(choices.avatars.some((item) => item.id === avatar) ? avatar : "");
      setVoice(choices.voices.some((item) => item.id === voice) ? voice : "");
    } catch (e) {
      setError(e instanceof Error ? e.message : "无法读取 HeyGen 素材");
    } finally {
      setLoading(false);
    }
  };

  const save = async () => {
    setLoading(true);
    setError("");
    try {
      const saved = await saveHeyGenSettings(key, avatar, voice);
      if (onSaveBinding && !await onSaveBinding({ avatar_id: saved.avatar_id, avatar_name: saved.avatar_name,
        voice_id: saved.voice_id, voice_name: saved.voice_name, engine: saved.engine ?? "" })) {
        setError("连接已保存，本次视频绑定未更新，请关闭窗口后刷新项目重试");
        return;
      }
      setSettings(saved);
      setKey("");
      setDialog(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "无法保存 HeyGen 配置");
    } finally {
      setLoading(false);
    }
  };

  return (
    <section aria-label="HeyGen 开场生成" className="space-y-3 rounded-lg border border-border p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <h4 className="font-medium">HeyGen 开场</h4>
        <Button size="sm" variant="ghost" disabled={busy} onClick={openSettings}>
          {ready ? "更换形象或声音" : "连接 HeyGen API"}
        </Button>
      </div>
      {ready && <p className="text-xs text-muted-foreground">{settings.avatar_name} · {settings.voice_name}</p>}
      {job && (
        <div role="status" className="space-y-1 text-xs">
          <p>{statusLabels[job.status]}{stale && " · 来自旧版分镜"}</p>
          {job.error && <p className="text-amber-300">{job.error}</p>}
        </div>
      )}
      <div className="flex flex-wrap items-center gap-2">
        {job?.status === "paused" && (
          <Button size="sm" variant="outline" disabled={busy} onClick={onResume}>继续原任务</Button>
        )}
        {canGenerate && ready && !active && job?.status !== "paused" && (
          <Button size="sm" disabled={busy || !spokenText.trim()} onClick={() => setDialog("generate")}>
            {job?.status === "completed" ? "生成新版本" : "生成 HeyGen 开场"}
          </Button>
        )}
        {asset && (
          <a className="text-xs text-primary underline" href={asset.url} target="_blank" rel="noreferrer">
            打开 HeyGen 成片
          </a>
        )}
        {asset && onSelect && (
          <Button size="sm" variant="outline" disabled={busy} onClick={onSelect}>设为当前画面</Button>
        )}
      </div>

      <Dialog open={dialog === "settings"} onOpenChange={(open) => {
        if (!open && !loading) {
          setKey("");
          setDialog(null);
        }
      }}>
        <DialogContent>
          <DialogTitle>连接 HeyGen API</DialogTitle>
          <DialogDescription>在工作台直接生成视频时，使用 API Key 绑定 Lina 已授权的私人形象和克隆声音。</DialogDescription>
          {settings?.managed_key ? (
            <p className="text-sm text-muted-foreground">API Key 已由后端环境变量配置。</p>
          ) : (
            <label className="space-y-2 text-sm">
              <span>HeyGen API Key</span>
              <Input
                type="password"
                autoComplete="off"
                value={key}
                disabled={loading}
                onChange={(e) => {
                  setKey(e.target.value);
                  setCatalog(undefined);
                  setError("");
                }}
                placeholder={settings?.has_key ? "已保存，留空沿用" : "粘贴 HeyGen API Key"}
              />
            </label>
          )}
          <div className="flex flex-wrap items-center gap-3">
            <Button variant="outline" size="sm" disabled={loading || (!key.trim() && !settings?.has_key)} onClick={loadChoices}>
              {loading && !catalog ? "正在读取…" : "读取 HeyGen 素材"}
            </Button>
            <a className="text-xs text-primary underline" href="https://platform.heygen.com/api-keys" target="_blank" rel="noreferrer">
              获取 API Key
            </a>
          </div>
          {catalog && (
            <>
              <label className="space-y-2 text-sm">
                <span>{presenterName} 的私人形象</span>
                <select value={avatar} onChange={(e) => setAvatar(e.target.value)} className="h-9 w-full rounded-md border border-input bg-background px-2">
                  <option value="">{catalog.avatars.length ? "选择形象" : "工作区暂无可用的私人形象"}</option>
                  {catalog.avatars.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.id.slice(-8)}</option>)}
                </select>
              </label>
              <label className="space-y-2 text-sm">
                <span>{presenterName} 的克隆声音</span>
                <select value={voice} onChange={(e) => setVoice(e.target.value)} className="h-9 w-full rounded-md border border-input bg-background px-2">
                  <option value="">{catalog.voices.length ? "选择声音" : "工作区暂无可用的克隆声音"}</option>
                  {catalog.voices.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
                </select>
              </label>
            </>
          )}
          {error && <p role="alert" className="text-sm text-amber-300">{error}</p>}
          <DialogFooter>
            <Button disabled={loading || !catalog || !avatar || !voice} onClick={save}>保存 HeyGen 配置</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={dialog === "generate"} onOpenChange={(open) => {
        if (!open && !busy) setDialog(null);
      }}>
        <DialogContent>
          <DialogTitle>生成 HeyGen 开场</DialogTitle>
          <DialogDescription>用 {settings?.avatar_name} 和 {settings?.voice_name} 生成竖版视频。提交会消耗 HeyGen API 额度。</DialogDescription>
          <p className="whitespace-pre-wrap rounded-lg bg-muted p-3 text-sm">{spokenText}</p>
          <DialogFooter>
            <Button variant="outline" disabled={busy} onClick={() => setDialog(null)}>取消</Button>
            <Button disabled={busy} onClick={async () => {
              if (await onGenerate()) setDialog(null);
            }}>确认生成</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
}
