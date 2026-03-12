/**
 * GoldenClip Config Page
 * Design: 暗金剪辑台 · 编导美学
 * Features: Editing Aesthetic editor, system info, quick reference
 */

import { useState, useEffect } from "react";
import { Button } from "@/components/ui/button";
import { toast } from "sonner";
import {
  Save, RefreshCw, Settings, Info, Loader2,
  CheckCircle2, Terminal, Cpu, HardDrive, Zap,
} from "lucide-react";
import { getEditingAesthetic, updateEditingAesthetic } from "@/lib/api";

export default function ConfigPage() {
  const [content, setContent] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    loadConfig();
  }, []);

  const loadConfig = async () => {
    setLoading(true);
    try {
      const text = await getEditingAesthetic();
      setContent(text);
    } catch {
      toast.error("加载配置失败");
    } finally {
      setLoading(false);
    }
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      await updateEditingAesthetic(content);
      setSaved(true);
      toast.success("配置已保存");
      setTimeout(() => setSaved(false), 2000);
    } catch {
      toast.error("保存失败");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="h-full flex flex-col overflow-hidden">
      {/* Header */}
      <div className="px-6 py-4 border-b border-border shrink-0">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-lg font-semibold text-foreground flex items-center gap-2">
              <Settings className="w-5 h-5 text-primary" />
              配置管理
            </h1>
            <p className="text-xs text-muted-foreground mt-0.5">
              编辑剪辑美学配置 · 调整 8 条黄金规则参数
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              className="border-border text-muted-foreground hover:text-foreground gap-1.5 text-xs"
              onClick={loadConfig}
              disabled={loading}
            >
              <RefreshCw className="w-3.5 h-3.5" />
              重新加载
            </Button>
            <Button
              size="sm"
              className="bg-primary text-primary-foreground hover:bg-primary/90 gap-1.5 text-xs"
              onClick={handleSave}
              disabled={saving || loading}
            >
              {saving ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
              ) : saved ? (
                <CheckCircle2 className="w-3.5 h-3.5" />
              ) : (
                <Save className="w-3.5 h-3.5" />
              )}
              {saved ? "已保存" : "保存配置"}
            </Button>
          </div>
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-hidden flex gap-0">
        {/* Editor */}
        <div className="flex-1 flex flex-col p-6 overflow-hidden">
          <div className="flex items-center gap-2 mb-3">
            <div className="w-2 h-2 rounded-full bg-primary" />
            <span className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
              Editing_Aesthetic.md
            </span>
          </div>
          {loading ? (
            <div className="flex-1 flex items-center justify-center">
              <Loader2 className="w-6 h-6 animate-spin text-primary" />
            </div>
          ) : (
            <textarea
              value={content}
              onChange={(e) => setContent(e.target.value)}
              className="flex-1 w-full bg-[#080A0D] border border-border rounded-xl p-4 text-sm font-mono text-foreground resize-none focus:outline-none focus:ring-1 focus:ring-primary/50 leading-relaxed"
              spellCheck={false}
              style={{ fontFamily: "'JetBrains Mono', 'Fira Code', monospace" }}
            />
          )}
        </div>

        {/* Right panel: System info */}
        <div className="w-72 shrink-0 border-l border-border p-5 overflow-y-auto">
          {/* System Status */}
          <div className="mb-5">
            <h3 className="text-[11px] font-medium text-muted-foreground uppercase tracking-wider mb-3 flex items-center gap-1.5">
              <Terminal className="w-3.5 h-3.5" />
              系统状态
            </h3>
            <div className="space-y-2">
              {[
                { label: "FastAPI 后端", port: "8000", ok: true },
                { label: "React 前端", port: "3000", ok: true },
                { label: "FFmpeg", port: null, ok: true },
              ].map((item) => (
                <div key={item.label} className="flex items-center justify-between text-xs">
                  <div className="flex items-center gap-1.5">
                    <div className={`w-1.5 h-1.5 rounded-full ${item.ok ? "bg-green-500" : "bg-red-500"}`} />
                    <span className="text-foreground">{item.label}</span>
                  </div>
                  {item.port && (
                    <span className="font-mono text-muted-foreground text-[10px]">:{item.port}</span>
                  )}
                </div>
              ))}
            </div>
          </div>

          {/* Quick Reference */}
          <div className="mb-5">
            <h3 className="text-[11px] font-medium text-muted-foreground uppercase tracking-wider mb-3 flex items-center gap-1.5">
              <Zap className="w-3.5 h-3.5" />
              8 条黄金规则
            </h3>
            <div className="space-y-1.5">
              {[
                { rule: "Rule 1", name: "重说识别", desc: "相邻句开头 5 字相同则删前句" },
                { rule: "Rule 2", name: "残句清理", desc: "删除 <1.5s 无意义片段" },
                { rule: "Rule 3", name: "语气词切除", desc: "移除嗯、啊、那个等 FIL" },
                { rule: "Rule 4", name: "词内去重", desc: "移除 STU 结巴标记" },
                { rule: "Rule 5", name: "语义去重", desc: "跨时段相同观点保留最优" },
                { rule: "Rule 6", name: "句内重复", desc: "删除修正前的半句" },
                { rule: "Rule 7", name: "问答闭环", desc: "访谈场景保证 Q&A 成对" },
                { rule: "Rule 8", name: "气口对齐", desc: "剪辑点预留 150ms/100ms" },
              ].map((item) => (
                <div key={item.rule} className="bg-secondary rounded-lg px-2.5 py-2">
                  <div className="flex items-center gap-1.5 mb-0.5">
                    <span className="text-[10px] font-mono text-primary">{item.rule}</span>
                    <span className="text-[11px] font-medium text-foreground">{item.name}</span>
                  </div>
                  <p className="text-[10px] text-muted-foreground">{item.desc}</p>
                </div>
              ))}
            </div>
          </div>

          {/* Workflow */}
          <div>
            <h3 className="text-[11px] font-medium text-muted-foreground uppercase tracking-wider mb-3 flex items-center gap-1.5">
              <Cpu className="w-3.5 h-3.5" />
              核心工作流
            </h3>
            <div className="space-y-2">
              {[
                { step: "1", label: "感知", desc: "Whisper ASR + VAD → 带标签剧本", color: "text-blue-400" },
                { step: "2", label: "审计", desc: "Claude 应用 8 条规则 → JSON 指令", color: "text-purple-400" },
                { step: "3", label: "重构", desc: "FFmpeg 无损切割 / 剪映草稿生成", color: "text-primary" },
              ].map((item) => (
                <div key={item.step} className="flex gap-2.5">
                  <div className={`w-5 h-5 rounded-full bg-secondary flex items-center justify-center shrink-0 text-[10px] font-mono ${item.color}`}>
                    {item.step}
                  </div>
                  <div>
                    <p className={`text-xs font-medium ${item.color}`}>{item.label}</p>
                    <p className="text-[10px] text-muted-foreground">{item.desc}</p>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Install instructions */}
          <div className="mt-5 bg-secondary rounded-xl p-3">
            <h3 className="text-[11px] font-medium text-muted-foreground uppercase tracking-wider mb-2 flex items-center gap-1.5">
              <HardDrive className="w-3.5 h-3.5" />
              安装依赖
            </h3>
            <div className="space-y-1.5 text-[10px] font-mono text-muted-foreground">
              <p className="text-foreground"># 后端依赖</p>
              <p>pip install -r requirements.txt</p>
              <p className="text-foreground mt-2"># 启动后端</p>
              <p>bash start_backend.sh</p>
              <p className="text-foreground mt-2"># 可选: 更快的 ASR</p>
              <p>pip install faster-whisper</p>
              <p className="text-foreground mt-2"># 可选: 剪映草稿</p>
              <p>pip install pyJianYingDraft</p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
