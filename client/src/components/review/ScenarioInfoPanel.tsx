import { useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { BookOpen, ChevronDown, Loader2 } from "lucide-react";
import { getPrompt, type Task } from "@/lib/api";
import { cn } from "@/lib/utils";

// ============================================================
// Scenario Info Panel (Right Column Top)
// ============================================================
const SCENARIO_META: Record<string, {
  label: string;
  color: string;
  border: string;
  bg: string;
  rules: { code: string; name: string; desc: string }[];
  targets: { host?: string; guest?: string; overall: string };
}> = {
  monologue_clean: {
    label: "口播精修",
    color: "text-amber-400",
    border: "border-amber-500/30",
    bg: "bg-amber-500/5",
    rules: [
      { code: "P1", name: "重说识别", desc: "开头5字相同→删前句" },
      { code: "P2", name: "结巴切除", desc: "词级精准切除，零容忍" },
      { code: "P3", name: "语气词分级", desc: "必删/酌情/保留三档" },
      { code: "P5", name: "冗余压缩", desc: "同观点保留信息密度最高版" },
      { code: "P7", name: "开头钩子", desc: "前5秒强制保护" },
    ],
    targets: { overall: "保留 60-80%" },
  },
  interview_compress: {
    label: "直播访谈压缩",
    color: "text-blue-400",
    border: "border-blue-500/30",
    bg: "bg-blue-500/5",
    rules: [
      { code: "I1", name: "问答闭环", desc: "Q&A成对，不可破坏" },
      { code: "I2", name: "跨段去重", desc: "全文扫描语义重复" },
      { code: "I3", name: "精华保护", desc: "金句/洞见无条件保留" },
      { code: "I4", name: "主播精简", desc: "废话铺垫删除" },
      { code: "I6", name: "情绪弧线", desc: "四节点保护" },
    ],
    targets: { host: "主播 20-30%", guest: "嘉宾 60-70%", overall: "整体 35-45%" },
  },
  highlight_reel: {
    label: "直播卖货集锦",
    color: "text-orange-400",
    border: "border-orange-500/30",
    bg: "bg-orange-500/5",
    rules: [
      { code: "L1", name: "产品边界", desc: "一个集锦只讲一个产品" },
      { code: "L2", name: "产品开场", desc: "首段说清产品名/品类" },
      { code: "L3", name: "卖点故事", desc: "保留卖点/场景/差异化" },
      { code: "L4", name: "链接删除", desc: "删除下单与小黄车话术" },
      { code: "L5", name: "跨品切分", desc: "新产品必须另起集锦" },
    ],
    targets: { overall: "一产品一集锦 · 60-300s" },
  },
};

export function ScenarioInfoPanel({
  task,
  styleMode,
  onStyleModeChange,
  openaiApiKey,
  provider,
  ollamaModel,
}: {
  task: Task;
  styleMode: "immersive" | "quick_cut";
  onStyleModeChange: (m: "immersive" | "quick_cut") => void;
  openaiApiKey: string;
  provider?: string;
  ollamaModel?: string;
}) {
  const meta = SCENARIO_META[task.task_type] || SCENARIO_META.monologue_clean;
  const [expanded, setExpanded] = useState(false);
  const [promptDialogOpen, setPromptDialogOpen] = useState(false);
  const [promptContent, setPromptContent] = useState("");
  const [promptLoading, setPromptLoading] = useState(false);

  // 加载并展示提示词
  const handleViewPrompt = async (e: React.MouseEvent) => {
    e.stopPropagation();
    setPromptDialogOpen(true);
    if (promptContent) return; // 已加载过则直接展示
    setPromptLoading(true);
    try {
      const data = await getPrompt(task.task_type as "monologue_clean" | "interview_compress" | "highlight_reel");
      setPromptContent(data.content);
    } catch {
      setPromptContent("加载提示词失败，请检查后端服务是否正常。");
    } finally {
      setPromptLoading(false);
    }
  };

  return (
    <>
      {/* 提示词查看弹窗 */}
      <Dialog open={promptDialogOpen} onOpenChange={setPromptDialogOpen}>
        <DialogContent className="max-w-2xl max-h-[80vh] flex flex-col">
          <DialogHeader>
            <DialogTitle className={cn("font-mono text-sm", meta.color)}>
              {meta.label} · 提示词
            </DialogTitle>
            <DialogDescription className="text-xs text-muted-foreground">
              当前场景 AI 审计使用的完整提示词内容（只读）
            </DialogDescription>
          </DialogHeader>
          <div className="flex-1 overflow-auto mt-2">
            {promptLoading ? (
              <div className="flex items-center justify-center py-12 text-muted-foreground text-sm gap-2">
                <Loader2 className="w-4 h-4 animate-spin" />
                加载中…
              </div>
            ) : (
              <pre className="text-[11px] leading-relaxed whitespace-pre-wrap font-mono text-foreground/80 bg-secondary/30 rounded-md p-4 border border-border">
                {promptContent || "（提示词为空）"}
              </pre>
            )}
          </div>
        </DialogContent>
      </Dialog>

    <div className={cn("border-b border-border shrink-0", meta.bg)}>
      {/* Header */}
      <button
        className="w-full flex items-center gap-2 px-3 py-2 hover:bg-white/5 transition-colors"
        onClick={() => setExpanded(!expanded)}
      >
        <span className={cn("text-[10px] font-mono font-bold uppercase tracking-wider", meta.color)}>
          {meta.label}
        </span>
        <span className="text-[10px] text-muted-foreground/50 ml-auto">
          {meta.targets.overall}
        </span>
        <ChevronDown className={cn("w-3 h-3 text-muted-foreground/50 transition-transform", expanded && "rotate-180")} />
      </button>

      {/* Expanded: Rules + Config */}
      {expanded && (
        <div className="px-3 pb-3 space-y-3">
          {/* Rules */}
          <div className="space-y-1">
            {meta.rules.map((r) => (
              <div key={r.code} className="flex items-start gap-1.5">
                <span className={cn("text-[10px] font-mono font-bold w-5 shrink-0 mt-0.5", meta.color)}>{r.code}</span>
                <div className="min-w-0">
                  <span className="text-[10px] text-foreground/80 font-medium">{r.name}</span>
                  <span className="text-[10px] text-muted-foreground/60 ml-1">{r.desc}</span>
                </div>
              </div>
            ))}
          </div>

          {/* 查看提示词按钮 */}
          <button
            onClick={handleViewPrompt}
            className="w-full flex items-center justify-center gap-1.5 py-1 rounded border border-border/50 text-[10px] text-muted-foreground hover:text-foreground hover:border-border transition-all"
          >
            <BookOpen className="w-3 h-3" />
            查看提示词
          </button>

          {/* Style Mode */}
          <div className="space-y-1">
            <p className="text-[10px] text-muted-foreground/60 uppercase tracking-wider">剪辑风格</p>
            <div className="grid grid-cols-2 gap-1">
              {(["immersive", "quick_cut"] as const).map((m) => (
                <button
                  key={m}
                  onClick={() => onStyleModeChange(m)}
                  className={cn(
                    "text-[10px] py-1 px-2 rounded border transition-all",
                    styleMode === m
                      ? cn("border-current font-medium", meta.color)
                      : "border-border text-muted-foreground hover:text-foreground"
                  )}
                >
                  {m === "immersive" ? "沉浸" : "快剪"}
                </button>
              ))}
            </div>
            <p className="text-[10px] text-muted-foreground/40 leading-relaxed">
              {styleMode === "immersive"
                ? "气口150ms · 保留思考停顿"
                : "气口80ms · 激进删除废话"}
            </p>
          </div>

          {/* 模型状态（只读，配置入口在设置页） */}
          <div className="space-y-1">
            {provider === "ollama" ? (
              <>
                <p className="text-[10px] text-muted-foreground/60 uppercase tracking-wider">本地模型</p>
                <div className="text-[10px] px-2 py-1 rounded border border-green-500/30 bg-green-500/10 text-green-400 font-mono truncate">
                  ⚡ {ollamaModel || "Ollama"}
                </div>
                <p className="text-[10px] text-muted-foreground/30">
                  数据不出本机 · 完全离线推理
                </p>
              </>
            ) : (
              <>
                <p className="text-[10px] text-muted-foreground/60 uppercase tracking-wider">OpenAI API Key</p>
                <div className={cn(
                  "text-[10px] px-2 py-1 rounded border",
                  openaiApiKey
                    ? "border-green-500/30 bg-green-500/10 text-green-400"
                    : "border-border/30 bg-secondary/30 text-muted-foreground/50"
                )}>
                  {openaiApiKey ? "✓ 已配置 API Key" : "未配置 · 将使用规则引擎"}
                </div>
                <p className="text-[10px] text-muted-foreground/30">
                  在左侧设置页（⚙）中配置 API Key
                </p>
              </>
            )}
          </div>

          {/* Target retention */}
          {(meta.targets.host || meta.targets.guest) && (
            <div className="space-y-1 border-t border-border/30 pt-2">
              <p className="text-[10px] text-muted-foreground/60 uppercase tracking-wider">保留目标</p>
              {meta.targets.host && (
                <div className="flex justify-between text-[10px]">
                  <span className="text-muted-foreground">主播 (spk0)</span>
                  <span className={meta.color}>{meta.targets.host.split(" ")[1]}</span>
                </div>
              )}
              {meta.targets.guest && (
                <div className="flex justify-between text-[10px]">
                  <span className="text-muted-foreground">嘉宾 (spk1)</span>
                  <span className={meta.color}>{meta.targets.guest.split(" ")[1]}</span>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
    </>
  );
}
