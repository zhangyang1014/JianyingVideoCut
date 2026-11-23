import { useState, useEffect } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from "@/components/ui/sheet";
import { cn } from "@/lib/utils";

// ============================================================
// 手动修改原因选择浮窗
// ============================================================

/** 保留原因（AI 误删 → 用户恢复）*/
const KEEP_REASONS = [
  "重要知识点，值得保留",
  "有效互动，不是无效沟通",
  "背景铺垫，有助于理解",
  "学生关键提问",
  "流畅过渡，节奏自然",
];

/** 删除原因（AI 漏删 → 用户删除）*/
const DELETE_REASONS = [
  "无效沟通，偏离主题",
  "重复内容",
  "与学习无关的闲聊",
  "过长停顿或填充语",
  "说话含糊，难以理解",
];

export function ReasonPickerSheet({
  open,
  action,
  onSelect,
  onSkip,
}: {
  open: boolean;
  action?: "keep" | "delete";
  onSelect: (reason: string) => void;
  onSkip: () => void;
}) {
  const [customMode, setCustomMode] = useState(false);
  const [customText, setCustomText] = useState("");

  const reasons = action === "keep" ? KEEP_REASONS : DELETE_REASONS;
  const isKeepAction = action === "keep";

  // 每次打开重置
  useEffect(() => {
    if (open) {
      setCustomMode(false);
      setCustomText("");
    }
  }, [open]);

  return (
    <Sheet open={open} onOpenChange={(v) => { if (!v) onSkip(); }}>
      <SheetContent side="bottom" className="bg-card border-border rounded-t-xl max-h-[60vh] overflow-y-auto">
        <SheetHeader className="pb-3">
          <SheetTitle className={cn(
            "text-sm flex items-center gap-2",
            isKeepAction ? "text-green-300" : "text-orange-300"
          )}>
            {isKeepAction ? "📌 为什么保留这段？" : "🗑 为什么删除这段？"}
          </SheetTitle>
          <SheetDescription className="text-[11px] text-muted-foreground">
            选择原因将帮助 AI 学习你的剪辑偏好，也会显示在段落卡片上
          </SheetDescription>
        </SheetHeader>

        {!customMode ? (
          <div className="space-y-3 pt-1">
            {/* 预设原因 chips */}
            <div className="flex flex-wrap gap-2">
              {reasons.map((r) => (
                <button
                  key={r}
                  className={cn(
                    "text-[12px] px-3 py-1.5 rounded-full border transition-colors",
                    isKeepAction
                      ? "border-green-500/40 bg-green-900/20 text-green-300 hover:bg-green-900/40"
                      : "border-orange-500/40 bg-orange-900/20 text-orange-300 hover:bg-orange-900/40"
                  )}
                  onClick={() => onSelect(r)}
                >
                  {r}
                </button>
              ))}
              {/* 自定义 */}
              <button
                className="text-[12px] px-3 py-1.5 rounded-full border border-border bg-secondary/30 text-muted-foreground hover:bg-secondary/60 transition-colors"
                onClick={() => setCustomMode(true)}
              >
                自定义...
              </button>
            </div>

            {/* 跳过按钮 */}
            <div className="pt-1">
              <button
                className="text-[11px] text-muted-foreground/60 hover:text-muted-foreground underline transition-colors"
                onClick={onSkip}
              >
                跳过，不添加原因
              </button>
            </div>
          </div>
        ) : (
          <div className="space-y-3 pt-1">
            <Input
              autoFocus
              placeholder="输入自定义原因..."
              value={customText}
              onChange={(e) => setCustomText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && customText.trim()) onSelect(customText.trim());
                if (e.key === "Escape") setCustomMode(false);
              }}
              className="text-sm bg-secondary/20 border-border"
            />
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                disabled={!customText.trim()}
                onClick={() => onSelect(customText.trim())}
                className={cn(
                  "text-xs h-7",
                  isKeepAction
                    ? "bg-green-700/40 hover:bg-green-700/60 text-green-200"
                    : "bg-orange-700/40 hover:bg-orange-700/60 text-orange-200"
                )}
              >
                确认
              </Button>
              <button
                className="text-[11px] text-muted-foreground/60 hover:text-muted-foreground underline"
                onClick={() => setCustomMode(false)}
              >
                返回选项
              </button>
            </div>
          </div>
        )}
      </SheetContent>
    </Sheet>
  );
}
