/**
 * GoldenClip Config Page — Prompt Manager
 * Design: 暗金剪辑台 · 编导美学
 *
 * Layout:
 *   Left (60%): 3-tab scenario selector → Rule cards (expandable/editable) + Add Rule
 *   Right (40%): Raw prompt text editor + system info
 */

import { useState, useEffect, useCallback } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import {
  Save, RefreshCw, Settings, Loader2, CheckCircle2,
  Terminal, Cpu, HardDrive, Zap, ChevronDown, ChevronRight,
  Plus, Trash2, Edit3, FileText, Star, X, GripVertical,
  BookOpen,
} from "lucide-react";
import {
  getPrompt, updatePromptContent, updatePromptRules,
  getGlossary, updateGlossary,
  listThemes, updateTheme,
  getCoverContentPrompts, updateCoverContentPrompts,
  type PromptRule, type ScenarioKey, type ThemeMeta, type CoverContentPrompts,
} from "@/lib/api";

// ============================================================
// Scenario Config
// ============================================================
const SCENARIOS: {
  key: ScenarioKey;
  label: string;
  sublabel: string;
  color: string;
  activeBg: string;
  borderColor: string;
  tagColor: string;
  prefix: string;
  description: string;
}[] = [
  {
    key: "monologue_clean",
    label: "口播精修",
    sublabel: "Monologue Clean",
    color: "text-amber-400",
    activeBg: "bg-amber-500/10",
    borderColor: "border-amber-500/40",
    tagColor: "bg-amber-500/20 text-amber-300",
    prefix: "P",
    description: "专为口播博主设计 · 雕刻更专业的演讲者版本 · 保留 60-80%",
  },
  {
    key: "interview_compress",
    label: "访谈压缩",
    sublabel: "Interview Compress",
    color: "text-blue-400",
    activeBg: "bg-blue-500/10",
    borderColor: "border-blue-500/40",
    tagColor: "bg-blue-500/20 text-blue-300",
    prefix: "I",
    description: "《十三邀》级编导视角 · Q&A 闭环保护 · 嘉宾保留 60-70%",
  },
  {
    key: "highlight_reel",
    label: "精彩集锦",
    sublabel: "Highlight Reel",
    color: "text-orange-400",
    activeBg: "bg-orange-500/10",
    borderColor: "border-orange-500/40",
    tagColor: "bg-orange-500/20 text-orange-300",
    prefix: "H",
    description: "短视频爆款操盘手 · 3秒钩子 · 保留 10-25%",
  },
];

const PRIORITY_STARS = ["☆☆☆", "★☆☆", "★★☆", "★★★"];
const PRIORITY_LABELS = ["关闭", "低", "中", "高"];
const PRIORITY_COLORS = [
  "text-muted-foreground",
  "text-slate-400",
  "text-amber-400/70",
  "text-amber-400",
];

// ============================================================
// Rule Card Component
// ============================================================
function RuleCard({
  rule,
  scenarioColor,
  tagColor,
  onUpdate,
  onDelete,
  index,
}: {
  rule: PromptRule;
  scenarioColor: string;
  tagColor: string;
  onUpdate: (updated: PromptRule) => void;
  onDelete: () => void;
  index: number;
}) {
  const [expanded, setExpanded] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<PromptRule>(rule);

  const handleSave = () => {
    onUpdate(draft);
    setEditing(false);
  };

  const handleCancel = () => {
    setDraft(rule);
    setEditing(false);
  };

  return (
    <div
      className={cn(
        "border rounded-xl overflow-hidden transition-all duration-200",
        expanded ? "border-border/60" : "border-border/30 hover:border-border/50"
      )}
    >
      {/* Card Header */}
      <div
        className="flex items-center gap-2.5 px-3 py-2.5 cursor-pointer hover:bg-white/3 transition-colors select-none"
        onClick={() => !editing && setExpanded(!expanded)}
      >
        <GripVertical className="w-3 h-3 text-muted-foreground/20 shrink-0" />

        {/* Rule code badge */}
        <span className={cn("text-[10px] font-mono font-bold w-7 shrink-0", scenarioColor)}>
          {rule.code}
        </span>

        {/* Rule name */}
        <span className="text-sm font-medium text-foreground flex-1 truncate">
          {rule.name}
        </span>

        {/* Priority stars */}
        <span className={cn("text-[11px] font-mono shrink-0", PRIORITY_COLORS[rule.priority] || "text-muted-foreground")}>
          {PRIORITY_STARS[rule.priority] || "☆☆☆"}
        </span>

        {/* Action buttons */}
        <div className="flex items-center gap-1 shrink-0" onClick={(e) => e.stopPropagation()}>
          <button
            onClick={() => { setEditing(true); setExpanded(true); }}
            className="w-6 h-6 rounded flex items-center justify-center text-muted-foreground/40 hover:text-amber-400 hover:bg-amber-400/10 transition-colors"
            title="编辑规则"
          >
            <Edit3 className="w-3 h-3" />
          </button>
          <button
            onClick={onDelete}
            className="w-6 h-6 rounded flex items-center justify-center text-muted-foreground/40 hover:text-red-400 hover:bg-red-400/10 transition-colors"
            title="删除规则"
          >
            <Trash2 className="w-3 h-3" />
          </button>
        </div>

        {expanded ? (
          <ChevronDown className="w-3.5 h-3.5 text-muted-foreground/40 shrink-0" />
        ) : (
          <ChevronRight className="w-3.5 h-3.5 text-muted-foreground/40 shrink-0" />
        )}
      </div>

      {/* Short desc (always visible) */}
      {!expanded && rule.desc && (
        <div className="px-3 pb-2.5 -mt-1">
          <p className="text-[11px] text-muted-foreground/60 leading-relaxed pl-[2.375rem] truncate">
            {rule.desc}
          </p>
        </div>
      )}

      {/* Expanded body */}
      {expanded && (
        <div className="border-t border-border/30 bg-background/60">
          {editing ? (
            /* ---- Edit Mode ---- */
            <div className="p-4 space-y-3">
              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1">
                  <label className="text-[10px] text-muted-foreground uppercase tracking-wider">规则代码</label>
                  <Input
                    value={draft.code}
                    onChange={(e) => setDraft({ ...draft, code: e.target.value.toUpperCase() })}
                    className="h-7 text-xs font-mono bg-secondary/50"
                    placeholder="P1"
                  />
                </div>
                <div className="space-y-1">
                  <label className="text-[10px] text-muted-foreground uppercase tracking-wider">规则名称</label>
                  <Input
                    value={draft.name}
                    onChange={(e) => setDraft({ ...draft, name: e.target.value })}
                    className="h-7 text-xs bg-secondary/50"
                    placeholder="重说识别"
                  />
                </div>
              </div>

              <div className="space-y-1">
                <label className="text-[10px] text-muted-foreground uppercase tracking-wider">优先级</label>
                <div className="flex gap-2">
                  {[0, 1, 2, 3].map((p) => (
                    <button
                      key={p}
                      onClick={() => setDraft({ ...draft, priority: p })}
                      className={cn(
                        "flex-1 py-1 text-[11px] rounded border transition-all",
                        draft.priority === p
                          ? cn("border-current font-medium", PRIORITY_COLORS[p])
                          : "border-border/30 text-muted-foreground/40 hover:text-muted-foreground"
                      )}
                    >
                      {PRIORITY_LABELS[p]}
                    </button>
                  ))}
                </div>
              </div>

              <div className="space-y-1">
                <label className="text-[10px] text-muted-foreground uppercase tracking-wider">简短描述</label>
                <Input
                  value={draft.desc}
                  onChange={(e) => setDraft({ ...draft, desc: e.target.value })}
                  className="h-7 text-xs bg-secondary/50"
                  placeholder="一句话描述规则的核心逻辑"
                />
              </div>

              <div className="space-y-1">
                <label className="text-[10px] text-muted-foreground uppercase tracking-wider">
                  完整规则内容 (Markdown)
                </label>
                <textarea
                  value={draft.full_text}
                  onChange={(e) => setDraft({ ...draft, full_text: e.target.value })}
                  className="w-full h-48 bg-secondary/50 border border-border rounded-lg p-3 text-xs font-mono text-foreground resize-none focus:outline-none focus:ring-1 focus:ring-primary/50 leading-relaxed"
                  spellCheck={false}
                />
              </div>

              <div className="flex gap-2 justify-end">
                <Button variant="outline" size="sm" className="h-7 text-xs gap-1" onClick={handleCancel}>
                  <X className="w-3 h-3" /> 取消
                </Button>
                <Button size="sm" className="h-7 text-xs gap-1 bg-primary" onClick={handleSave}>
                  <Save className="w-3 h-3" /> 保存规则
                </Button>
              </div>
            </div>
          ) : (
            /* ---- View Mode ---- */
            <div className="p-4 space-y-2">
              <div className="flex items-center gap-2 mb-2">
                <span className={cn("text-[10px] font-mono font-bold", scenarioColor)}>{rule.code}</span>
                <span className="text-sm font-semibold text-foreground">{rule.name}</span>
                <span className={cn("text-xs font-mono ml-auto", PRIORITY_COLORS[rule.priority])}>
                  {PRIORITY_STARS[rule.priority]} {rule.priority_note}
                </span>
              </div>
              <div
                className="text-[11px] text-muted-foreground/80 leading-relaxed whitespace-pre-wrap font-mono bg-muted rounded-lg p-3 max-h-64 overflow-y-auto"
                style={{ fontFamily: "'JetBrains Mono', monospace" }}
              >
                {rule.full_text || rule.desc}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ============================================================
// Add Rule Dialog (inline)
// ============================================================
function AddRuleForm({
  prefix,
  existingCodes,
  onAdd,
  onCancel,
}: {
  prefix: string;
  existingCodes: string[];
  onAdd: (rule: PromptRule) => void;
  onCancel: () => void;
}) {
  const nextNum = existingCodes.length + 1;
  const [code, setCode] = useState(`${prefix}${nextNum}`);
  const [name, setName] = useState("");
  const [priority, setPriority] = useState(2);
  const [desc, setDesc] = useState("");
  const [fullText, setFullText] = useState(
    `### 【${prefix}${nextNum}】新规则名称 ★★☆ 中等优先级\n**触发条件**：\n\n**执行逻辑**：\n\n**示例**：\n`
  );

  useEffect(() => {
    setFullText(
      `### 【${code}】${name || "新规则"} ${PRIORITY_STARS[priority]} ${PRIORITY_LABELS[priority]}优先级\n**触发条件**：${desc}\n\n**执行逻辑**：\n\n**示例**：\n`
    );
  }, [code, name, priority, desc]);

  const handleAdd = () => {
    if (!code.trim() || !name.trim()) {
      toast.error("规则代码和名称不能为空");
      return;
    }
    if (existingCodes.includes(code.toUpperCase())) {
      toast.error(`规则代码 ${code} 已存在`);
      return;
    }
    onAdd({
      code: code.toUpperCase(),
      name,
      priority,
      priority_note: `${PRIORITY_LABELS[priority]}优先级`,
      stars: PRIORITY_STARS[priority],
      desc,
      full_text: fullText,
    });
  };

  return (
    <div className="border border-primary/30 rounded-xl bg-primary/5 p-4 space-y-3">
      <div className="flex items-center gap-2 mb-1">
        <Plus className="w-3.5 h-3.5 text-primary" />
        <span className="text-xs font-medium text-primary">添加新规则</span>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1">
          <label className="text-[10px] text-muted-foreground uppercase tracking-wider">规则代码</label>
          <Input
            value={code}
            onChange={(e) => setCode(e.target.value.toUpperCase())}
            className="h-7 text-xs font-mono bg-secondary/50"
            placeholder={`${prefix}9`}
          />
        </div>
        <div className="space-y-1">
          <label className="text-[10px] text-muted-foreground uppercase tracking-wider">规则名称</label>
          <Input
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="h-7 text-xs bg-secondary/50"
            placeholder="如：情绪弧线保护"
          />
        </div>
      </div>

      <div className="space-y-1">
        <label className="text-[10px] text-muted-foreground uppercase tracking-wider">优先级</label>
        <div className="flex gap-2">
          {[0, 1, 2, 3].map((p) => (
            <button
              key={p}
              onClick={() => setPriority(p)}
              className={cn(
                "flex-1 py-1 text-[11px] rounded border transition-all",
                priority === p
                  ? cn("border-current font-medium", PRIORITY_COLORS[p])
                  : "border-border/30 text-muted-foreground/40 hover:text-muted-foreground"
              )}
            >
              {PRIORITY_LABELS[p]}
            </button>
          ))}
        </div>
      </div>

      <div className="space-y-1">
        <label className="text-[10px] text-muted-foreground uppercase tracking-wider">简短描述</label>
        <Input
          value={desc}
          onChange={(e) => setDesc(e.target.value)}
          className="h-7 text-xs bg-secondary/50"
          placeholder="一句话描述触发条件"
        />
      </div>

      <div className="space-y-1">
        <label className="text-[10px] text-muted-foreground uppercase tracking-wider">完整规则 (Markdown)</label>
        <textarea
          value={fullText}
          onChange={(e) => setFullText(e.target.value)}
          className="w-full h-32 bg-secondary/50 border border-border rounded-lg p-3 text-xs font-mono text-foreground resize-none focus:outline-none focus:ring-1 focus:ring-primary/50 leading-relaxed"
          spellCheck={false}
        />
      </div>

      <div className="flex gap-2 justify-end">
        <Button variant="outline" size="sm" className="h-7 text-xs gap-1" onClick={onCancel}>
          <X className="w-3 h-3" /> 取消
        </Button>
        <Button size="sm" className="h-7 text-xs gap-1 bg-primary" onClick={handleAdd}>
          <Plus className="w-3 h-3" /> 添加规则
        </Button>
      </div>
    </div>
  );
}

// ============================================================
// Main ConfigPage（仅 Prompt 管理）
// ============================================================
export default function ConfigPage() {
  // "scenario" | "glossary" | "themes" | "cover_content"
  const [activeTab, setActiveTab] = useState<"scenario" | "glossary" | "themes" | "cover_content">("scenario");
  const [activeScenario, setActiveScenario] = useState<ScenarioKey>("monologue_clean");
  const [rules, setRules] = useState<PromptRule[]>([]);
  const [rawContent, setRawContent] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [showRaw, setShowRaw] = useState(false);
  const [showAddRule, setShowAddRule] = useState(false);
  const [dirty, setDirty] = useState(false);

  // 词典纠错状态
  const [glossaryContent, setGlossaryContent] = useState("");
  const [glossaryLoading, setGlossaryLoading] = useState(false);
  const [glossarySaving, setGlossarySaving] = useState(false);
  const [glossarySaved, setGlossarySaved] = useState(false);
  const [glossaryDirty, setGlossaryDirty] = useState(false);

  // 封面与文案 prompt 状态
  const [coverPrompts, setCoverPrompts] = useState<CoverContentPrompts | null>(null);
  const [coverPromptsLoading, setCoverPromptsLoading] = useState(false);
  const [coverPromptsSaving, setCoverPromptsSaving] = useState(false);
  const [coverPromptsDirty, setCoverPromptsDirty] = useState(false);
  const [coverPromptsSaved, setCoverPromptsSaved] = useState(false);

  const scenarioMeta = SCENARIOS.find((s) => s.key === activeScenario)!;

  const loadScenario = useCallback(async (scenario: ScenarioKey) => {
    setLoading(true);
    setDirty(false);
    setShowAddRule(false);
    try {
      const data = await getPrompt(scenario);
      setRules(data.rules || []);
      setRawContent(data.content || "");
    } catch {
      toast.error("加载 Prompt 失败，请确认后端已启动");
      setRules([]);
      setRawContent("");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadScenario(activeScenario);
  }, [activeScenario, loadScenario]);

  // 主题状态
  const [themes, setThemes] = useState<ThemeMeta[]>([]);
  const [activeThemeId, setActiveThemeId] = useState<string | null>(null);
  const [themeContent, setThemeContent] = useState("");
  const [themeLoading, setThemeLoading] = useState(false);
  const [themeSaving, setThemeSaving] = useState(false);
  const [themeSaved, setThemeSaved] = useState(false);
  const [themeDirty, setThemeDirty] = useState(false);

  const loadThemes = useCallback(async () => {
    setThemeLoading(true);
    try {
      const data = await listThemes();
      setThemes(data);
      if (data.length > 0 && !activeThemeId) {
        setActiveThemeId(data[0].id);
        setThemeContent(data[0].content);
      }
    } catch {
      toast.error("加载主题失败，请确认后端已启动");
    } finally {
      setThemeLoading(false);
    }
  }, [activeThemeId]);

  useEffect(() => {
    if (activeTab === "themes") loadThemes();
  }, [activeTab, loadThemes]);

  const handleSaveTheme = async () => {
    if (!activeThemeId) return;
    setThemeSaving(true);
    try {
      await updateTheme(activeThemeId, themeContent);
      setThemeDirty(false);
      setThemeSaved(true);
      toast.success("主题 Prompt 已保存");
      setTimeout(() => setThemeSaved(false), 2000);
    } catch {
      toast.error("保存主题失败，请确认后端已启动");
    } finally {
      setThemeSaving(false);
    }
  };

  // 加载词典
  const loadGlossary = useCallback(async () => {
    setGlossaryLoading(true);
    setGlossaryDirty(false);
    try {
      const content = await getGlossary();
      setGlossaryContent(content);
    } catch {
      toast.error("加载词典失败，请确认后端已启动");
    } finally {
      setGlossaryLoading(false);
    }
  }, []);

  useEffect(() => {
    if (activeTab === "glossary") {
      loadGlossary();
    }
  }, [activeTab, loadGlossary]);

  useEffect(() => {
    if (activeTab === "cover_content" && !coverPrompts) {
      setCoverPromptsLoading(true);
      getCoverContentPrompts()
        .then(data => { setCoverPrompts(data); setCoverPromptsDirty(false); })
        .catch(() => toast.error("加载封面文案 Prompt 失败"))
        .finally(() => setCoverPromptsLoading(false));
    }
  }, [activeTab, coverPrompts]);

  const handleSaveCoverPrompts = async () => {
    if (!coverPrompts) return;
    setCoverPromptsSaving(true);
    try {
      await updateCoverContentPrompts(coverPrompts);
      setCoverPromptsDirty(false);
      setCoverPromptsSaved(true);
      toast.success("封面文案 Prompt 已保存");
      setTimeout(() => setCoverPromptsSaved(false), 2000);
    } catch {
      toast.error("保存失败，请确认后端已启动");
    } finally {
      setCoverPromptsSaving(false);
    }
  };

  const updateCoverField = (section: keyof CoverContentPrompts, field: string, value: string) => {
    setCoverPrompts(prev => prev ? {
      ...prev,
      [section]: { ...prev[section], [field]: value },
    } : prev);
    setCoverPromptsDirty(true);
  };

  const handleSaveGlossary = async () => {
    setGlossarySaving(true);
    try {
      await updateGlossary(glossaryContent);
      setGlossaryDirty(false);
      setGlossarySaved(true);
      toast.success("词典已保存，下次 ASR 时生效");
      setTimeout(() => setGlossarySaved(false), 2000);
    } catch {
      toast.error("保存词典失败，请确认后端已启动");
    } finally {
      setGlossarySaving(false);
    }
  };

  const handleSaveRules = async () => {
    setSaving(true);
    try {
      if (showRaw) {
        // Save raw content
        const result = await updatePromptContent(activeScenario, rawContent);
        setRules(result.rules || []);
        toast.success("Prompt 已保存");
      } else {
        // Save rules
        await updatePromptRules(activeScenario, rules);
        toast.success(`已保存 ${rules.length} 条规则`);
      }
      setSaved(true);
      setDirty(false);
      setTimeout(() => setSaved(false), 2000);
    } catch {
      toast.error("保存失败，请确认后端已启动");
    } finally {
      setSaving(false);
    }
  };

  const handleUpdateRule = (index: number, updated: PromptRule) => {
    const next = [...rules];
    next[index] = updated;
    setRules(next);
    setDirty(true);
  };

  const handleDeleteRule = (index: number) => {
    const rule = rules[index];
    setRules(rules.filter((_, i) => i !== index));
    setDirty(true);
    toast.success(`已删除规则 ${rule.code}`);
  };

  const handleAddRule = (newRule: PromptRule) => {
    setRules([...rules, newRule]);
    setShowAddRule(false);
    setDirty(true);
    toast.success(`已添加规则 ${newRule.code}`);
  };

  return (
    <div className="h-full flex flex-col overflow-hidden">
      {/* Header */}
      <div className="px-6 py-4 border-b border-border shrink-0">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-lg font-semibold text-foreground flex items-center gap-2">
              <Settings className="w-5 h-5 text-primary" />
              Prompt 管理
            </h1>
            <p className="text-xs text-muted-foreground mt-0.5">
              为每个剪辑场景独立配置 Claude 审计规则
            </p>
          </div>
          <div className="flex items-center gap-2">
            {activeTab !== "glossary" && (
              <>
                <Button
                  variant="outline"
                  size="sm"
                  className="border-border text-muted-foreground hover:text-foreground gap-1.5 text-xs"
                  onClick={() => loadScenario(activeScenario)}
                  disabled={loading}
                >
                  <RefreshCw className={cn("w-3.5 h-3.5", loading && "animate-spin")} />
                  重新加载
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  className={cn(
                    "border-border gap-1.5 text-xs",
                    showRaw ? "text-primary border-primary/40" : "text-muted-foreground hover:text-foreground"
                  )}
                  onClick={() => setShowRaw(!showRaw)}
                >
                  <FileText className="w-3.5 h-3.5" />
                  {showRaw ? "规则视图" : "原始 Prompt"}
                </Button>
                <Button
                  size="sm"
                  className={cn(
                    "gap-1.5 text-xs",
                    dirty ? "bg-primary text-primary-foreground" : "bg-primary/60 text-primary-foreground/70"
                  )}
                  onClick={handleSaveRules}
                  disabled={saving || loading || !dirty}
                >
                  {saving ? (
                    <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  ) : saved ? (
                    <CheckCircle2 className="w-3.5 h-3.5" />
                  ) : (
                    <Save className="w-3.5 h-3.5" />
                  )}
                  {saved ? "已保存" : dirty ? "保存更改" : "无更改"}
                </Button>
              </>
            )}
          </div>
        </div>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-hidden flex">
        {/* Left: Scenario Tabs + Rules */}
        <div className="flex-1 flex flex-col overflow-hidden">
          {/* Scenario Tabs */}
          <div className="flex border-b border-border shrink-0 px-4 pt-3 gap-1">
            {SCENARIOS.map((s) => (
              <button
                key={s.key}
                onClick={() => {
                  if (dirty) {
                    if (!confirm("有未保存的更改，切换场景将丢失。继续？")) return;
                  }
                  setActiveTab("scenario");
                  setActiveScenario(s.key);
                }}
                className={cn(
                  "flex flex-col items-start px-4 py-2.5 rounded-t-lg border-b-2 transition-all text-left",
                  activeTab === "scenario" && activeScenario === s.key
                    ? cn("border-current", s.color, s.activeBg)
                    : "border-transparent text-muted-foreground hover:text-foreground hover:bg-secondary/50"
                )}
              >
                <span className="text-xs font-semibold">{s.label}</span>
                <span className="text-[10px] opacity-60 font-mono">{s.sublabel}</span>
              </button>
            ))}
            {/* 词典纠错 Tab */}
            <button
              onClick={() => {
                if (dirty) {
                  if (!confirm("有未保存的更改，切换将丢失。继续？")) return;
                }
                setActiveTab("glossary");
              }}
              className={cn(
                "flex flex-col items-start px-4 py-2.5 rounded-t-lg border-b-2 transition-all text-left",
                activeTab === "glossary"
                  ? "border-green-500 text-green-400 bg-green-500/10"
                  : "border-transparent text-muted-foreground hover:text-foreground hover:bg-secondary/50"
              )}
            >
              <span className="text-xs font-semibold">词典纠错</span>
              <span className="text-[10px] opacity-60 font-mono">Glossary</span>
            </button>
            {/* 视频主题 Tab */}
            <button
              onClick={() => {
                if (dirty) {
                  if (!confirm("有未保存的更改，切换将丢失。继续？")) return;
                }
                setActiveTab("themes");
              }}
              className={cn(
                "flex flex-col items-start px-4 py-2.5 rounded-t-lg border-b-2 transition-all text-left",
                activeTab === "themes"
                  ? "border-orange-500 text-orange-400 bg-orange-500/10"
                  : "border-transparent text-muted-foreground hover:text-foreground hover:bg-secondary/50"
              )}
            >
              <span className="text-xs font-semibold">视频主题</span>
              <span className="text-[10px] opacity-60 font-mono">Themes</span>
            </button>
            {/* 封面与文案 Tab */}
            <button
              onClick={() => {
                if (dirty) {
                  if (!confirm("有未保存的更改，切换将丢失。继续？")) return;
                }
                setActiveTab("cover_content");
              }}
              className={cn(
                "flex flex-col items-start px-4 py-2.5 rounded-t-lg border-b-2 transition-all text-left",
                activeTab === "cover_content"
                  ? "border-purple-500 text-purple-400 bg-purple-500/10"
                  : "border-transparent text-muted-foreground hover:text-foreground hover:bg-secondary/50"
              )}
            >
              <span className="text-xs font-semibold">封面与文案</span>
              <span className="text-[10px] opacity-60 font-mono">Cover & XHS</span>
            </button>
          </div>

          {/* Scenario description / Glossary description / Themes description */}
          {activeTab === "glossary" ? (
            <div className="px-5 py-2.5 border-b border-border/30 shrink-0 bg-green-500/10">
              <p className="text-[11px] text-green-400">
                ASR 专业术语词典 · 每行一个词 · 识别后自动替换为标准写法
              </p>
              <p className="text-[10px] text-muted-foreground/50 mt-0.5">
                支持中英文混合 · 词典较长时优先匹配 · 修改后下次 ASR 任务时生效
              </p>
            </div>
          ) : activeTab === "themes" ? (
            <div className="px-5 py-2.5 border-b border-border/30 shrink-0 bg-orange-500/10">
              <p className="text-[11px] text-orange-400">
                视频主题补充规则 · 用户创建一剪多任务时选择主题 · AI 将优先遵守对应主题的规则
              </p>
              <p className="text-[10px] text-muted-foreground/50 mt-0.5">
                主题规则追加在主 Prompt 末尾 · 如与通用规则冲突以主题规则为准
              </p>
            </div>
          ) : activeTab === "cover_content" ? (
            <div className="px-5 py-2.5 border-b border-border/30 shrink-0 bg-purple-500/10">
              <p className="text-[11px] text-purple-400">
                封面图片生成 · 封面标题推荐 · 小红书文案生成 · 三个 AI 接口的指令模板
              </p>
              <p className="text-[10px] text-muted-foreground/50 mt-0.5">
                使用 {"{变量名}"} 作为动态占位符 · 修改后保存立即生效
              </p>
            </div>
          ) : (
            <div className={cn("px-5 py-2.5 border-b border-border/30 shrink-0", scenarioMeta.activeBg)}>
              <p className={cn("text-[11px]", scenarioMeta.color)}>{scenarioMeta.description}</p>
              <p className="text-[10px] text-muted-foreground/50 mt-0.5">
                {rules.length} 条规则 · 点击规则卡片展开详情 · 点击编辑图标修改
              </p>
            </div>
          )}

          {/* Rules List or Raw Editor or Glossary Editor or Themes Editor */}
          <div className="flex-1 overflow-y-auto p-5">
            {activeTab === "themes" ? (
              /* 主题 Prompt 编辑器 */
              themeLoading ? (
                <div className="flex items-center justify-center h-32">
                  <Loader2 className="w-6 h-6 animate-spin text-orange-400" />
                  <span className="ml-2 text-sm text-muted-foreground">加载中...</span>
                </div>
              ) : (
                <div className="h-full flex flex-col space-y-3">
                  {/* 主题选择器 */}
                  <div className="flex flex-col gap-10">
                    <div className="flex gap-1.5 flex-wrap">
                      {themes.map((t) => (
                        <button
                          key={t.id}
                          onClick={() => {
                            if (themeDirty && !confirm("有未保存的更改，切换将丢失。继续？")) return;
                            setActiveThemeId(t.id);
                            setThemeContent(t.content);
                            setThemeDirty(false);
                          }}
                          className={cn(
                            "px-3 py-1.5 rounded-lg border text-xs transition-all",
                            activeThemeId === t.id
                              ? "border-orange-500/50 bg-orange-500/10 text-orange-400"
                              : "border-border/40 bg-secondary/20 text-muted-foreground hover:text-foreground hover:border-border"
                          )}
                        >
                          <span className="font-medium">{t.name}</span>
                        </button>
                      ))}
                    </div>
                    {activeThemeId && (
                      <span className="text-[10px] font-mono text-muted-foreground/40 px-0.5">
                        themes/{activeThemeId}.md
                      </span>
                    )}
                  </div>

                  {activeThemeId && (
                    <>
                      <div className="flex items-center gap-2">
                        <div className="flex items-center gap-2 ml-auto">
                          <Button
                            variant="outline"
                            size="sm"
                            className="h-6 text-[10px] gap-1 border-border text-muted-foreground"
                            onClick={loadThemes}
                            disabled={themeLoading}
                          >
                            <RefreshCw className={cn("w-3 h-3", themeLoading && "animate-spin")} />
                            重新加载
                          </Button>
                          <Button
                            size="sm"
                            className={cn(
                              "h-6 text-[10px] gap-1",
                              themeDirty
                                ? "bg-orange-600 hover:bg-orange-500 text-white"
                                : "bg-orange-600/40 text-white/50"
                            )}
                            onClick={handleSaveTheme}
                            disabled={themeSaving || !themeDirty}
                          >
                            {themeSaving ? (
                              <Loader2 className="w-3 h-3 animate-spin" />
                            ) : themeSaved ? (
                              <CheckCircle2 className="w-3 h-3" />
                            ) : (
                              <Save className="w-3 h-3" />
                            )}
                            {themeSaved ? "已保存" : themeDirty ? "保存主题" : "无更改"}
                          </Button>
                        </div>
                      </div>
                      <p className="text-[10px] text-muted-foreground/50 bg-orange-500/5 border border-orange-500/20 rounded-lg px-3 py-2 mt-10">
                        此内容会在用户选择该主题时追加到 highlight_reel 主 Prompt 末尾，并附注「如有冲突以本节为准」。支持 Markdown。
                      </p>
                      <textarea
                        value={themeContent}
                        onChange={(e) => { setThemeContent(e.target.value); setThemeDirty(true); }}
                        className="flex-1 min-h-[400px] w-full bg-background border border-orange-500/20 rounded-xl p-4 text-xs font-mono text-foreground resize-none focus:outline-none focus:ring-1 focus:ring-orange-500/40 leading-relaxed"
                        spellCheck={false}
                        style={{ fontFamily: "'JetBrains Mono', 'Fira Code', monospace" }}
                      />
                    </>
                  )}
                </div>
              )
            ) : activeTab === "cover_content" ? (
              /* 封面与文案 Prompt 编辑器 */
              coverPromptsLoading ? (
                <div className="flex items-center justify-center h-32">
                  <Loader2 className="w-6 h-6 animate-spin text-purple-400" />
                  <span className="ml-2 text-sm text-muted-foreground">加载中...</span>
                </div>
              ) : !coverPrompts ? null : (
                <div className="flex flex-col space-y-5">
                  {/* 保存按钮 */}
                  <div className="flex items-center gap-2 justify-end">
                    <Button
                      size="sm"
                      className={cn(
                        "h-6 text-[10px] gap-1",
                        coverPromptsDirty
                          ? "bg-purple-600 hover:bg-purple-500 text-white"
                          : "bg-purple-600/40 text-white/50"
                      )}
                      onClick={handleSaveCoverPrompts}
                      disabled={coverPromptsSaving || !coverPromptsDirty}
                    >
                      {coverPromptsSaving ? (
                        <Loader2 className="w-3 h-3 animate-spin" />
                      ) : coverPromptsSaved ? (
                        <CheckCircle2 className="w-3 h-3" />
                      ) : (
                        <Save className="w-3 h-3" />
                      )}
                      {coverPromptsSaved ? "已保存" : coverPromptsDirty ? "保存更改" : "无更改"}
                    </Button>
                  </div>

                  {/* 区块1：封面图片生成 */}
                  <div className="rounded-xl border border-purple-500/20 bg-purple-500/5 overflow-hidden">
                    <div className="px-4 py-2.5 border-b border-purple-500/20 flex items-center justify-between">
                      <div>
                        <span className="text-xs font-semibold text-purple-400">封面图片生成</span>
                        <span className="ml-2 text-[10px] text-muted-foreground/50">发给图像生成模型的指令</span>
                      </div>
                      <span className="text-[10px] text-muted-foreground/40 font-mono">
                        可用变量：<code className="text-purple-400/70">{"{cover_title}"}</code>{" "}
                        <code className="text-purple-400/70">{"{style_prompt}"}</code>
                      </span>
                    </div>
                    <div className="p-4 space-y-2">
                      <label className="text-[10px] text-muted-foreground uppercase tracking-wider">用户指令</label>
                      <textarea
                        value={coverPrompts.cover_image.user_prompt}
                        onChange={e => updateCoverField("cover_image", "user_prompt", e.target.value)}
                        rows={5}
                        className="w-full bg-background border border-purple-500/20 rounded-lg p-3 text-xs font-mono text-foreground resize-y focus:outline-none focus:ring-1 focus:ring-purple-500/40 leading-relaxed"
                        spellCheck={false}
                      />
                    </div>
                  </div>

                  {/* 区块2：封面标题推荐 */}
                  <div className="rounded-xl border border-purple-500/20 bg-purple-500/5 overflow-hidden">
                    <div className="px-4 py-2.5 border-b border-purple-500/20 flex items-center justify-between">
                      <div>
                        <span className="text-xs font-semibold text-purple-400">封面标题推荐</span>
                        <span className="ml-2 text-[10px] text-muted-foreground/50">AI 推荐 3 个封面标题</span>
                      </div>
                      <span className="text-[10px] text-muted-foreground/40 font-mono">
                        可用变量：<code className="text-purple-400/70">{"{segments_text}"}</code>
                      </span>
                    </div>
                    <div className="p-4 space-y-3">
                      <div className="space-y-2">
                        <label className="text-[10px] text-muted-foreground uppercase tracking-wider">系统角色</label>
                        <textarea
                          value={coverPrompts.cover_titles.system_prompt}
                          onChange={e => updateCoverField("cover_titles", "system_prompt", e.target.value)}
                          rows={2}
                          className="w-full bg-background border border-purple-500/20 rounded-lg p-3 text-xs font-mono text-foreground resize-y focus:outline-none focus:ring-1 focus:ring-purple-500/40 leading-relaxed"
                          spellCheck={false}
                        />
                      </div>
                      <div className="space-y-2">
                        <label className="text-[10px] text-muted-foreground uppercase tracking-wider">用户指令</label>
                        <textarea
                          value={coverPrompts.cover_titles.user_prompt}
                          onChange={e => updateCoverField("cover_titles", "user_prompt", e.target.value)}
                          rows={8}
                          className="w-full bg-background border border-purple-500/20 rounded-lg p-3 text-xs font-mono text-foreground resize-y focus:outline-none focus:ring-1 focus:ring-purple-500/40 leading-relaxed"
                          spellCheck={false}
                        />
                      </div>
                    </div>
                  </div>

                  {/* 区块3：小红书文案生成 */}
                  <div className="rounded-xl border border-purple-500/20 bg-purple-500/5 overflow-hidden">
                    <div className="px-4 py-2.5 border-b border-purple-500/20 flex items-center justify-between">
                      <div>
                        <span className="text-xs font-semibold text-purple-400">小红书文案生成</span>
                        <span className="ml-2 text-[10px] text-muted-foreground/50">生成标题 + 简介</span>
                      </div>
                      <span className="text-[10px] text-muted-foreground/40 font-mono">
                        可用变量：<code className="text-purple-400/70">{"{cover_title}"}</code>
                      </span>
                    </div>
                    <div className="p-4 space-y-3">
                      <div className="space-y-2">
                        <label className="text-[10px] text-muted-foreground uppercase tracking-wider">系统角色</label>
                        <textarea
                          value={coverPrompts.xhs_content.system_prompt}
                          onChange={e => updateCoverField("xhs_content", "system_prompt", e.target.value)}
                          rows={2}
                          className="w-full bg-background border border-purple-500/20 rounded-lg p-3 text-xs font-mono text-foreground resize-y focus:outline-none focus:ring-1 focus:ring-purple-500/40 leading-relaxed"
                          spellCheck={false}
                        />
                      </div>
                      <div className="space-y-2">
                        <label className="text-[10px] text-muted-foreground uppercase tracking-wider">用户指令</label>
                        <textarea
                          value={coverPrompts.xhs_content.user_prompt}
                          onChange={e => updateCoverField("xhs_content", "user_prompt", e.target.value)}
                          rows={10}
                          className="w-full bg-background border border-purple-500/20 rounded-lg p-3 text-xs font-mono text-foreground resize-y focus:outline-none focus:ring-1 focus:ring-purple-500/40 leading-relaxed"
                          spellCheck={false}
                        />
                      </div>
                    </div>
                  </div>

                  {/* 区块4：封面风格 */}
                  <div className="rounded-xl border border-purple-500/20 bg-purple-500/5 overflow-hidden">
                    <div className="px-4 py-2.5 border-b border-purple-500/20">
                      <span className="text-xs font-semibold text-purple-400">封面风格描述</span>
                      <span className="ml-2 text-[10px] text-muted-foreground/50">作为 {"{style_prompt}"} 传给封面图片生成接口</span>
                    </div>
                    <div className="p-4 space-y-4">
                      {(["emotion", "info", "vlog"] as const).map((styleId) => {
                        const labelMap = { emotion: "😱 情绪冲击", info: "📋 知识卡片", vlog: "📷 生活vlog" };
                        return (
                          <div key={styleId} className="space-y-1.5">
                            <label className="text-[10px] font-medium text-purple-300/70">{labelMap[styleId]}</label>
                            <textarea
                              value={coverPrompts.cover_styles?.[styleId] ?? ""}
                              onChange={e => updateCoverField("cover_styles", styleId, e.target.value)}
                              rows={5}
                              className="w-full bg-background border border-purple-500/20 rounded-lg p-3 text-xs font-mono text-foreground resize-y focus:outline-none focus:ring-1 focus:ring-purple-500/40 leading-relaxed"
                              spellCheck={false}
                            />
                          </div>
                        );
                      })}
                    </div>
                  </div>
                </div>
              )
            ) : activeTab === "glossary" ? (
              /* 词典编辑器 */
              glossaryLoading ? (
                <div className="flex items-center justify-center h-32">
                  <Loader2 className="w-6 h-6 animate-spin text-green-400" />
                  <span className="ml-2 text-sm text-muted-foreground">加载词典中...</span>
                </div>
              ) : (
                <div className="h-full flex flex-col space-y-3">
                  <div className="flex items-center gap-2">
                    <BookOpen className="w-3.5 h-3.5 text-green-400" />
                    <span className="text-[10px] font-mono text-muted-foreground uppercase tracking-wider">
                      backend/data/glossary.txt
                    </span>
                    <div className="flex items-center gap-2 ml-auto">
                      <Button
                        variant="outline"
                        size="sm"
                        className="h-6 text-[10px] gap-1 border-border text-muted-foreground"
                        onClick={loadGlossary}
                        disabled={glossaryLoading}
                      >
                        <RefreshCw className={cn("w-3 h-3", glossaryLoading && "animate-spin")} />
                        重新加载
                      </Button>
                      <Button
                        size="sm"
                        className={cn(
                          "h-6 text-[10px] gap-1",
                          glossaryDirty
                            ? "bg-green-600 hover:bg-green-500 text-white"
                            : "bg-green-600/40 text-white/50"
                        )}
                        onClick={handleSaveGlossary}
                        disabled={glossarySaving || !glossaryDirty}
                      >
                        {glossarySaving ? (
                          <Loader2 className="w-3 h-3 animate-spin" />
                        ) : glossarySaved ? (
                          <CheckCircle2 className="w-3 h-3" />
                        ) : (
                          <Save className="w-3 h-3" />
                        )}
                        {glossarySaved ? "已保存" : glossaryDirty ? "保存词典" : "无更改"}
                      </Button>
                    </div>
                  </div>
                  <div className="text-[10px] text-muted-foreground/50 bg-secondary/30 rounded-lg p-3 font-mono leading-relaxed">
                    <p className="text-muted-foreground mb-1"># 格式说明</p>
                    <p># 每行一个词，以 # 开头的行为注释</p>
                    <p># 大小写不敏感匹配，按词长优先（长词先匹配）</p>
                    <p># 示例：将 "cloud code" 自动替换为 "Claude Code"</p>
                  </div>
                  <textarea
                    value={glossaryContent}
                    onChange={(e) => {
                      setGlossaryContent(e.target.value);
                      setGlossaryDirty(true);
                    }}
                    className="flex-1 min-h-[400px] w-full bg-background border border-green-500/20 rounded-xl p-4 text-xs font-mono text-foreground resize-none focus:outline-none focus:ring-1 focus:ring-green-500/40 leading-relaxed"
                    spellCheck={false}
                    style={{ fontFamily: "'JetBrains Mono', 'Fira Code', monospace" }}
                    placeholder={"# 在此添加专业术语，每行一个\nClaude Code\nGitHub\nMCP\nAPI"}
                  />
                </div>
              )
            ) : loading ? (
              <div className="flex items-center justify-center h-32">
                <Loader2 className="w-6 h-6 animate-spin text-primary" />
                <span className="ml-2 text-sm text-muted-foreground">加载中...</span>
              </div>
            ) : showRaw ? (
              /* Raw Prompt Editor */
              <div className="h-full flex flex-col space-y-2">
                <div className="flex items-center gap-2">
                  <div className="w-2 h-2 rounded-full bg-primary" />
                  <span className="text-[10px] font-mono text-muted-foreground uppercase tracking-wider">
                    {activeScenario}.md
                  </span>
                  <span className="text-[10px] text-muted-foreground/40 ml-auto">
                    直接编辑 Markdown · 保存后自动解析规则
                  </span>
                </div>
                <textarea
                  value={rawContent}
                  onChange={(e) => { setRawContent(e.target.value); setDirty(true); }}
                  className="flex-1 min-h-[500px] w-full bg-background border border-border rounded-xl p-4 text-xs font-mono text-foreground resize-none focus:outline-none focus:ring-1 focus:ring-primary/50 leading-relaxed"
                  spellCheck={false}
                  style={{ fontFamily: "'JetBrains Mono', 'Fira Code', monospace" }}
                />
              </div>
            ) : (
              /* Rule Cards */
              <div className="space-y-2">
                {rules.length === 0 ? (
                  <div className="text-center py-12">
                    <Zap className="w-8 h-8 text-muted-foreground/20 mx-auto mb-3" />
                    <p className="text-sm text-muted-foreground/50">暂无规则</p>
                    <p className="text-xs text-muted-foreground/30 mt-1">
                      后端未启动时无法加载 · 或切换到「原始 Prompt」手动编辑
                    </p>
                  </div>
                ) : (
                  rules.map((rule, i) => (
                    <RuleCard
                      key={`${rule.code}-${i}`}
                      rule={rule}
                      scenarioColor={scenarioMeta.color}
                      tagColor={scenarioMeta.tagColor}
                      index={i}
                      onUpdate={(updated) => handleUpdateRule(i, updated)}
                      onDelete={() => handleDeleteRule(i)}
                    />
                  ))
                )}

                {/* Add Rule */}
                {showAddRule ? (
                  <AddRuleForm
                    prefix={scenarioMeta.prefix}
                    existingCodes={rules.map((r) => r.code)}
                    onAdd={handleAddRule}
                    onCancel={() => setShowAddRule(false)}
                  />
                ) : (
                  <button
                    onClick={() => setShowAddRule(true)}
                    className="w-full border border-dashed border-border/40 rounded-xl py-3 flex items-center justify-center gap-2 text-xs text-muted-foreground/50 hover:text-muted-foreground hover:border-border/70 transition-all hover:bg-secondary/20"
                  >
                    <Plus className="w-3.5 h-3.5" />
                    添加新规则
                  </button>
                )}
              </div>
            )}
          </div>
        </div>

        {/* Right: System Info */}
        <div className="w-64 shrink-0 border-l border-border p-5 overflow-y-auto">
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

          {/* Scenario Summary */}
          <div className="mb-5">
            <h3 className="text-[11px] font-medium text-muted-foreground uppercase tracking-wider mb-3 flex items-center gap-1.5">
              <Star className="w-3.5 h-3.5" />
              场景对比
            </h3>
            <div className="space-y-2">
              {SCENARIOS.map((s) => (
                <button
                  key={s.key}
                  onClick={() => setActiveScenario(s.key)}
                  className={cn(
                    "w-full text-left rounded-lg px-2.5 py-2 transition-all border",
                    activeScenario === s.key
                      ? cn("border-current/40", s.color, s.activeBg)
                      : "border-border/20 hover:border-border/40 hover:bg-secondary/30"
                  )}
                >
                  <div className="flex items-center justify-between">
                    <span className={cn("text-[11px] font-medium", activeScenario === s.key ? s.color : "text-foreground/70")}>
                      {s.label}
                    </span>
                    <span className="text-[10px] text-muted-foreground/40 font-mono">{s.prefix}*</span>
                  </div>
                  <p className="text-[10px] text-muted-foreground/50 mt-0.5 leading-relaxed">
                    {s.description.split("·")[2]?.trim()}
                  </p>
                </button>
              ))}
            </div>
          </div>

          {/* Workflow */}
          <div className="mb-5">
            <h3 className="text-[11px] font-medium text-muted-foreground uppercase tracking-wider mb-3 flex items-center gap-1.5">
              <Cpu className="w-3.5 h-3.5" />
              核心工作流
            </h3>
            <div className="space-y-2">
              {[
                { step: "1", label: "感知", desc: "Whisper ASR + VAD → 带标签剧本", color: "text-blue-400" },
                { step: "2", label: "审计", desc: "Claude 应用场景规则 → JSON 指令", color: "text-purple-400" },
                { step: "3", label: "重构", desc: "FFmpeg 无损切割 / 剪映草稿", color: "text-primary" },
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

          {/* Install */}
          <div className="bg-secondary rounded-xl p-3">
            <h3 className="text-[11px] font-medium text-muted-foreground uppercase tracking-wider mb-2 flex items-center gap-1.5">
              <HardDrive className="w-3.5 h-3.5" />
              快速启动
            </h3>
            <div className="space-y-1 text-[10px] font-mono text-muted-foreground">
              <p className="text-foreground"># 启动后端</p>
              <p>bash start_backend.sh</p>
              <p className="text-foreground mt-2"># 可选: 更快 ASR</p>
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
