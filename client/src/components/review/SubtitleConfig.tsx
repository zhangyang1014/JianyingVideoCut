import { useState, useEffect, useRef, useCallback } from "react";
import { Button } from "@/components/ui/button";
import { SlidersHorizontal } from "lucide-react";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from "@/components/ui/sheet";
import type { SubtitleStyle } from "@/lib/api";
import { cn } from "@/lib/utils";

// ============================================================
// Subtitle Style Editor（字幕样式配置 + 实时预览）
// ============================================================
// 常用中文字体列表（值 = FFmpeg/系统字体名，label = 显示名）
const CHINESE_FONTS: { label: string; value: string; css: string }[] = [
  // macOS 系统字体
  { label: "苹方（PingFang SC）",      value: "PingFang SC",         css: "PingFang SC, sans-serif" },
  { label: "黑体（Heiti SC）",          value: "Heiti SC",             css: "Heiti SC, sans-serif" },
  { label: "宋体（Songti SC）",         value: "Songti SC",            css: "Songti SC, serif" },
  { label: "楷体（STKaiti）",           value: "STKaiti",              css: "STKaiti, cursive" },
  { label: "仿宋（STFangsong）",        value: "STFangsong",           css: "STFangsong, serif" },
  { label: "华文黑体（STHeiti）",        value: "STHeiti",              css: "STHeiti, sans-serif" },
  { label: "华文细黑（STXihei）",        value: "STXihei",              css: "STXihei, sans-serif" },
  // 跨平台 Noto 字体（Linux/服务器常见）
  { label: "Noto Sans CJK SC（无衬线）", value: "Noto Sans CJK SC",   css: "'Noto Sans CJK SC', sans-serif" },
  { label: "Noto Serif CJK SC（衬线）",  value: "Noto Serif CJK SC",  css: "'Noto Serif CJK SC', serif" },
  // 文泉驿（Linux）
  { label: "文泉驿微米黑",               value: "WenQuanYi Micro Hei", css: "'WenQuanYi Micro Hei', sans-serif" },
];

export const DEFAULT_SUBTITLE_STYLE: SubtitleStyle = {
  font_name: "PingFang SC",
  font_size: 28,
  primary_color: "#FFFFFF",
  outline_color: "#000000",
  outline: 2,
  shadow: 0.5,
  bold: false,
  alignment: 2,
  margin_top: 40,
  // 下方边距的初始值会在 openSubtitleConfig 中根据视频方向动态调整（首次使用时）
  margin_bottom: 80,
  margin_l: 60,
  margin_r: 60,
  max_lines: 2,
};

/**
 * 根据视频宽高比返回合理的首次默认样式。
 * 竖屏（9:16）：字号 72、无描边、无阴影、加粗、下边距 100px、左右 80px
 * 横屏（16:9）：下边距 ≈ 7.4% of 1080，左右 ≈ 5.2% of 1920
 */
export function getOrientationDefaults(ar: number): Partial<SubtitleStyle> {
  if (ar < 1) {
    // 竖屏
    return { font_size: 78, outline: 0, shadow: 0, bold: true, margin_bottom: 100, margin_l: 80, margin_r: 80 };
  } else {
    // 横屏
    return { margin_bottom: 80, margin_l: 100, margin_r: 100 };
  }
}

export const SUBTITLE_STYLE_LS_KEY = "gc_subtitle_style";
export const BURN_SUBTITLES_LS_KEY = "gc_burn_subtitles";

// alignment 对应 ASS numpad 布局的位置映射
const ALIGNMENT_LABELS: Record<number, string> = {
  7: "↖", 8: "↑", 9: "↗",
  4: "←", 5: "·", 6: "→",
  1: "↙", 2: "↓", 3: "↘",
};

// ============================================================
// SubtitlePreviewBox — 预览 + 可拖拽安全区边框
// ============================================================
function SubtitlePreviewBox({
  style,
  frameDataUrl,
  videoAspectRatio,
  onStyleChange,
}: {
  style: SubtitleStyle;
  frameDataUrl?: string | null;
  videoAspectRatio?: number;
  onStyleChange?: (patch: Partial<SubtitleStyle>) => void;
}) {
  const previewRef = useRef<HTMLDivElement>(null);
  const [previewWidth, setPreviewWidth] = useState(400);

  useEffect(() => {
    if (!previewRef.current) return;
    const obs = new ResizeObserver((entries) => {
      for (const e of entries) setPreviewWidth(e.contentRect.width);
    });
    obs.observe(previewRef.current);
    return () => obs.disconnect();
  }, []);

  const ar = videoAspectRatio && videoAspectRatio > 0 ? videoAspectRatio : 16 / 9;
  const refW = ar >= 1 ? 1920 : 1080;
  const refH = ar >= 1 ? 1080 : 1920;
  const scale = previewWidth / refW;
  // 预览容器的实际像素高度（宽度 / 宽高比）
  const previewHeight = previewWidth / ar;
  const isBottomAligned = style.alignment <= 3;
  const isTopAligned = style.alignment >= 7;
  // 字幕条带高度：约 3.5 行文字，保证看起来合理
  const stripH = Math.max(42 * scale, style.font_size * scale * 3.5);
  // 条带左上角距容器顶部的距离（px），用于左右拖拽手柄定位
  const stripTopPx = isBottomAligned
    ? previewHeight - style.margin_bottom * scale - stripH
    : isTopAligned
      ? style.margin_top * scale
      : previewHeight / 2 - stripH / 2;

  // ---- 拖拽逻辑 ----
  const dragRef = useRef<{
    edge: "top" | "bottom" | "left" | "right";
    startX: number; startY: number;
    startVal: number;
  } | null>(null);

  const onDragStart = useCallback((
    e: React.MouseEvent,
    edge: "top" | "bottom" | "left" | "right",
  ) => {
    if (!onStyleChange) return;
    e.preventDefault();
    const valMap = { top: style.margin_top, bottom: style.margin_bottom, left: style.margin_l, right: style.margin_r };
    dragRef.current = { edge, startX: e.clientX, startY: e.clientY, startVal: valMap[edge] };
    document.body.style.userSelect = "none";

    const maxH = Math.floor(refH * 0.8);
    const maxW = Math.floor(refW * 0.8);

    const onMove = (ev: MouseEvent) => {
      if (!dragRef.current) return;
      const { edge: ed, startX, startY, startVal } = dragRef.current;
      const dx = ev.clientX - startX;
      const dy = ev.clientY - startY;
      let newVal: number;
      if (ed === "top") {
        newVal = Math.round(Math.max(0, Math.min(maxH, startVal + dy / scale)));
        onStyleChange({ margin_top: newVal });
      } else if (ed === "bottom") {
        newVal = Math.round(Math.max(0, Math.min(maxH, startVal - dy / scale)));
        onStyleChange({ margin_bottom: newVal });
      } else if (ed === "left") {
        newVal = Math.round(Math.max(0, Math.min(maxW, startVal + dx / scale)));
        onStyleChange({ margin_l: newVal });
      } else {
        newVal = Math.round(Math.max(0, Math.min(maxW, startVal - dx / scale)));
        onStyleChange({ margin_r: newVal });
      }
    };

    const onUp = () => {
      dragRef.current = null;
      document.body.style.userSelect = "";
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };

    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  }, [onStyleChange, style, scale, refH, refW]);

  const getPreviewPosition = (): React.CSSProperties => {
    const a = style.alignment;
    const mtop = style.margin_top * scale;
    const mbot = style.margin_bottom * scale;
    const ml = style.margin_l * scale;
    const mr = style.margin_r * scale;
    const css: React.CSSProperties = { position: "absolute" };
    if (a >= 7) { css.top = mtop; }
    else if (a >= 4) { css.top = "50%"; css.transform = "translateY(-50%)"; }
    else { css.bottom = mbot; }
    const col = a % 3;
    if (col === 1) { css.left = ml; }
    else if (col === 2) { css.left = ml; css.right = mr; css.textAlign = "center"; }
    else { css.right = mr; }
    if (a >= 4 && a <= 6) {
      if (col === 1) css.left = ml;
      else if (col === 0) css.right = mr;
    }
    return css;
  };

  const handleThickness = 8;
  const handleColor = "rgba(250,180,50,0.35)";
  const handleHoverColor = "rgba(250,180,50,0.7)";
  const handleStyle = (cursor: string): React.CSSProperties => ({
    position: "absolute", cursor: onStyleChange ? cursor : "default",
  });

  return (
    <div
      ref={previewRef}
      className="relative w-full rounded-lg overflow-hidden border border-border/50"
      style={{
        aspectRatio: String(ar),
        background: frameDataUrl
          ? `url(${frameDataUrl}) center/cover no-repeat`
          : "#111",
      }}
    >
      {!frameDataUrl && (
        <div className="absolute inset-0 opacity-10 pointer-events-none"
          style={{
            backgroundImage: "linear-gradient(rgba(255,255,255,.06) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,.06) 1px, transparent 1px)",
            backgroundSize: `${previewWidth / 8}px ${previewWidth / 8}px`,
          }}
        />
      )}
      {frameDataUrl && (
        <div className="absolute inset-0 bg-black/20 pointer-events-none" />
      )}
      {/* 字幕位置条带：按当前对齐方向，只在文字真实出现的区域显示小矩形，
           避免旧设计"4边均等内缩"导致竖屏下大框几乎满屏的问题 */}
      <div
        className="absolute pointer-events-none"
        style={{
          left: style.margin_l * scale,
          right: style.margin_r * scale,
          top: stripTopPx,
          height: stripH,
          border: "2px solid rgba(250,180,50,0.85)",
          borderRadius: 2,
          boxShadow: "0 0 0 1px rgba(250,180,50,0.25), inset 0 0 0 1px rgba(250,180,50,0.15)",
        }}
      >
        <span style={{
          position: "absolute", top: 2, left: 4,
          fontSize: Math.max(6, 9 * scale),
          color: "rgba(250,180,50,0.7)",
          lineHeight: 1, userSelect: "none", whiteSpace: "nowrap",
        }}>
          字幕区域
        </span>
      </div>

      {/* 拖拽手柄：顶边（仅顶部对齐时显示） */}
      {isTopAligned && (
        <div
          style={{
            ...handleStyle("ns-resize"),
            top: style.margin_top * scale - handleThickness / 2,
            left: style.margin_l * scale,
            right: style.margin_r * scale,
            height: handleThickness,
          }}
          onMouseDown={(e) => onDragStart(e, "top")}
        >
          <div style={{
            position: "absolute", left: "50%", top: "50%", transform: "translate(-50%,-50%)",
            width: 24, height: 3, borderRadius: 2, background: handleColor,
            transition: "background .15s",
          }}
            onMouseEnter={(e) => { e.currentTarget.style.background = handleHoverColor; }}
            onMouseLeave={(e) => { e.currentTarget.style.background = handleColor; }}
          />
        </div>
      )}
      {/* 拖拽手柄：底边（仅底部对齐时显示） */}
      {isBottomAligned && (
        <div
          style={{
            ...handleStyle("ns-resize"),
            bottom: style.margin_bottom * scale - handleThickness / 2,
            left: style.margin_l * scale,
            right: style.margin_r * scale,
            height: handleThickness,
          }}
          onMouseDown={(e) => onDragStart(e, "bottom")}
        >
          <div style={{
            position: "absolute", left: "50%", top: "50%", transform: "translate(-50%,-50%)",
            width: 24, height: 3, borderRadius: 2, background: handleColor,
            transition: "background .15s",
          }}
            onMouseEnter={(e) => { e.currentTarget.style.background = handleHoverColor; }}
            onMouseLeave={(e) => { e.currentTarget.style.background = handleColor; }}
          />
        </div>
      )}
      {/* 拖拽手柄：左边（沿条带高度布局） */}
      <div
        style={{
          ...handleStyle("ew-resize"),
          left: style.margin_l * scale - handleThickness / 2,
          top: stripTopPx,
          height: stripH,
          width: handleThickness,
        }}
        onMouseDown={(e) => onDragStart(e, "left")}
      >
        <div style={{
          position: "absolute", left: "50%", top: "50%", transform: "translate(-50%,-50%)",
          width: 3, height: 24, borderRadius: 2, background: handleColor,
          transition: "background .15s",
        }}
          onMouseEnter={(e) => { e.currentTarget.style.background = handleHoverColor; }}
          onMouseLeave={(e) => { e.currentTarget.style.background = handleColor; }}
        />
      </div>
      {/* 拖拽手柄：右边（沿条带高度布局） */}
      <div
        style={{
          ...handleStyle("ew-resize"),
          right: style.margin_r * scale - handleThickness / 2,
          top: stripTopPx,
          height: stripH,
          width: handleThickness,
        }}
        onMouseDown={(e) => onDragStart(e, "right")}
      >
        <div style={{
          position: "absolute", left: "50%", top: "50%", transform: "translate(-50%,-50%)",
          width: 3, height: 24, borderRadius: 2, background: handleColor,
          transition: "background .15s",
        }}
          onMouseEnter={(e) => { e.currentTarget.style.background = handleHoverColor; }}
          onMouseLeave={(e) => { e.currentTarget.style.background = handleColor; }}
        />
      </div>

      {/* 字幕预览文字 */}
      <div style={{
        ...getPreviewPosition(),
        fontSize: Math.max(8, style.font_size * scale),
        color: style.primary_color,
        fontWeight: style.bold ? "bold" : "normal",
        WebkitTextStroke: style.outline > 0 ? `${Math.max(0.5, style.outline * scale)}px ${style.outline_color}` : "0px transparent",
        textShadow: style.shadow > 0
          ? `${style.shadow * scale}px ${style.shadow * scale}px ${style.shadow * scale * 2}px rgba(0,0,0,0.8)`
          : "none",
        fontFamily: CHINESE_FONTS.find(f => f.value === style.font_name)?.css ?? style.font_name,
        lineHeight: 1.4,
        padding: "0 2px",
        whiteSpace: "pre-wrap",
        wordBreak: "break-all",
        pointerEvents: "none",
      }}>
        这是一段字幕预览文字
      </div>
    </div>
  );
}

// ============================================================
// SubtitleStyleQuickControls — 导出面板常用样式快捷配置
// ============================================================
export function SubtitleStyleQuickControls({
  style,
  onChange,
  disabled,
}: {
  style: SubtitleStyle;
  onChange: (s: SubtitleStyle) => void;
  disabled?: boolean;
}) {
  const update = (patch: Partial<SubtitleStyle>) => onChange({ ...style, ...patch });

  return (
    <div className="space-y-2 rounded-md border border-border/50 bg-background/40 p-2.5">
      <div className="space-y-0.5">
        <label htmlFor="quick-subtitle-font" className="text-[10px] text-muted-foreground">字体</label>
        <select
          id="quick-subtitle-font"
          aria-label="快捷字幕字体"
          value={style.font_name}
          disabled={disabled}
          onChange={(e) => update({ font_name: e.target.value })}
          className="w-full h-7 px-2 rounded-md border border-border bg-secondary text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-primary disabled:opacity-50"
          style={{ fontFamily: CHINESE_FONTS.find(f => f.value === style.font_name)?.css ?? style.font_name }}
        >
          {CHINESE_FONTS.map((font) => (
            <option key={font.value} value={font.value} style={{ fontFamily: font.css }}>
              {font.label}
            </option>
          ))}
        </select>
      </div>

      <div className="grid grid-cols-2 gap-2">
        <label className="space-y-0.5 text-[10px] text-muted-foreground">
          <span>文字颜色</span>
          <span className="flex h-7 items-center gap-1.5 rounded-md border border-border bg-secondary px-1.5">
            <input
              aria-label="快捷字幕文字颜色"
              type="color"
              value={style.primary_color}
              disabled={disabled}
              onChange={(e) => update({ primary_color: e.target.value })}
              className="h-5 w-6 cursor-pointer rounded border-0 bg-transparent p-0 disabled:opacity-50"
            />
            <span className="truncate font-mono">{style.primary_color}</span>
          </span>
        </label>
        <label className="space-y-0.5 text-[10px] text-muted-foreground">
          <span>描边颜色</span>
          <span className="flex h-7 items-center gap-1.5 rounded-md border border-border bg-secondary px-1.5">
            <input
              aria-label="快捷字幕描边颜色"
              type="color"
              value={style.outline_color}
              disabled={disabled}
              onChange={(e) => update({ outline_color: e.target.value })}
              className="h-5 w-6 cursor-pointer rounded border-0 bg-transparent p-0 disabled:opacity-50"
            />
            <span className="truncate font-mono">{style.outline_color}</span>
          </span>
        </label>
      </div>

      <label className="block space-y-0.5 text-[10px] text-muted-foreground">
        <span>描边厚度 ({style.outline})</span>
        <input
          aria-label="快捷字幕描边厚度"
          type="range"
          min={0}
          max={4}
          step={0.5}
          value={style.outline}
          disabled={disabled}
          onChange={(e) => update({ outline: +e.target.value })}
          className="w-full h-1.5 accent-primary disabled:opacity-50"
        />
      </label>
    </div>
  );
}

// ============================================================
// SubtitleStyleEditor — 纯控件，不含预览框
// ============================================================
function SubtitleStyleEditor({
  style,
  onChange,
  disabled,
  videoAspectRatio,
}: {
  style: SubtitleStyle;
  onChange: (s: SubtitleStyle) => void;
  disabled?: boolean;
  videoAspectRatio?: number;
}) {
  const update = (patch: Partial<SubtitleStyle>) => onChange({ ...style, ...patch });
  const ar = videoAspectRatio && videoAspectRatio > 0 ? videoAspectRatio : 16 / 9;
  const refW = ar >= 1 ? 1920 : 1080;
  const refH = ar >= 1 ? 1080 : 1920;
  const maxH = Math.floor(refH * 0.8);
  const maxW = Math.floor(refW * 0.8);

  return (
    <div className="space-y-3">
      {/* 字体选择 */}
      <div className="space-y-0.5">
        <label className="text-[10px] text-muted-foreground">字体</label>
        <select
          value={style.font_name}
          disabled={disabled}
          onChange={(e) => update({ font_name: e.target.value })}
          className="w-full h-7 px-2 rounded-md border border-border bg-secondary text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-primary disabled:opacity-50"
          style={{ fontFamily: CHINESE_FONTS.find(f => f.value === style.font_name)?.css ?? style.font_name }}
        >
          {CHINESE_FONTS.map((f) => (
            <option key={f.value} value={f.value} style={{ fontFamily: f.css }}>{f.label}</option>
          ))}
        </select>
      </div>

      {/* 控件网格 */}
      <div className="grid grid-cols-2 gap-x-3 gap-y-2">
        <div className="space-y-0.5">
          <label className="text-[10px] text-muted-foreground">字体大小 ({style.font_size}px)</label>
          <input type="range" min={16} max={100} step={1}
            value={style.font_size} disabled={disabled}
            onChange={(e) => update({ font_size: +e.target.value })}
            className="w-full h-1.5 accent-primary"
          />
        </div>
        <div className="space-y-0.5">
          <label className="text-[10px] text-muted-foreground">描边厚度 ({style.outline})</label>
          <input type="range" min={0} max={4} step={0.5}
            value={style.outline} disabled={disabled}
            onChange={(e) => update({ outline: +e.target.value })}
            className="w-full h-1.5 accent-primary"
          />
        </div>
        <div className="space-y-0.5">
          <label className="text-[10px] text-muted-foreground">文字颜色</label>
          <div className="flex items-center gap-1.5">
            <input type="color" value={style.primary_color} disabled={disabled}
              onChange={(e) => update({ primary_color: e.target.value })}
              className="w-6 h-6 rounded border border-border cursor-pointer p-0"
            />
            <span className="text-[10px] font-mono text-muted-foreground">{style.primary_color}</span>
          </div>
        </div>
        <div className="space-y-0.5">
          <label className="text-[10px] text-muted-foreground">描边颜色</label>
          <div className="flex items-center gap-1.5">
            <input type="color" value={style.outline_color} disabled={disabled}
              onChange={(e) => update({ outline_color: e.target.value })}
              className="w-6 h-6 rounded border border-border cursor-pointer p-0"
            />
            <span className="text-[10px] font-mono text-muted-foreground">{style.outline_color}</span>
          </div>
        </div>
        <div className="space-y-0.5">
          <label className="text-[10px] text-muted-foreground">阴影 ({style.shadow})</label>
          <input type="range" min={0} max={2} step={0.5}
            value={style.shadow} disabled={disabled}
            onChange={(e) => update({ shadow: +e.target.value })}
            className="w-full h-1.5 accent-primary"
          />
        </div>
        <div className="space-y-0.5">
          <label className="text-[10px] text-muted-foreground">加粗</label>
          <label className="flex items-center gap-1.5 cursor-pointer">
            <input type="checkbox" checked={style.bold} disabled={disabled}
              onChange={(e) => update({ bold: e.target.checked })}
              className="rounded border-border accent-primary w-3 h-3"
            />
            <span className="text-[10px] text-muted-foreground">{style.bold ? "已开启" : "关闭"}</span>
          </label>
        </div>
      </div>

      {/* 位置选择器（九宫格） */}
      <div className="space-y-1">
        <label className="text-[10px] text-muted-foreground">字幕位置</label>
        <div className="inline-grid grid-cols-3 gap-0.5">
          {[7, 8, 9, 4, 5, 6, 1, 2, 3].map((a) => (
            <button
              key={a}
              disabled={disabled}
              onClick={() => update({ alignment: a })}
              className={cn(
                "w-7 h-7 rounded text-[11px] font-medium transition-colors border",
                style.alignment === a
                  ? "bg-primary text-primary-foreground border-primary"
                  : "bg-secondary text-muted-foreground border-border hover:bg-secondary/80"
              )}
            >
              {ALIGNMENT_LABELS[a]}
            </button>
          ))}
        </div>
      </div>

      {/* 边距 */}
      <div className="grid grid-cols-2 gap-x-3 gap-y-2">
        <div className="space-y-0.5">
          <label className="text-[10px] text-muted-foreground">上边距 ({style.margin_top}px)</label>
          <input type="range" min={0} max={maxH} step={5}
            value={style.margin_top} disabled={disabled}
            onChange={(e) => {
              const newVal = +e.target.value;
              update({ margin_top: newVal });
            }}
            className="w-full h-1.5 accent-primary"
          />
        </div>
        <div className="space-y-0.5">
          <label className="text-[10px] text-muted-foreground">下边距 ({style.margin_bottom}px)</label>
          <input type="range" min={0} max={maxH} step={5}
            value={style.margin_bottom} disabled={disabled}
            onChange={(e) => update({ margin_bottom: +e.target.value })}
            className="w-full h-1.5 accent-primary"
          />
        </div>
        <div className="space-y-0.5">
          <label className="text-[10px] text-muted-foreground">左边距 ({style.margin_l}px)</label>
          <input type="range" min={0} max={maxW} step={5}
            value={style.margin_l} disabled={disabled}
            onChange={(e) => update({ margin_l: +e.target.value })}
            className="w-full h-1.5 accent-primary"
          />
        </div>
        <div className="space-y-0.5">
          <label className="text-[10px] text-muted-foreground">右边距 ({style.margin_r}px)</label>
          <input type="range" min={0} max={maxW} step={5}
            value={style.margin_r} disabled={disabled}
            onChange={(e) => update({ margin_r: +e.target.value })}
            className="w-full h-1.5 accent-primary"
          />
        </div>
      </div>

      {/* 字幕行数上限 */}
      <div className="space-y-0.5">
        <label className="text-[10px] text-muted-foreground">
          每张字幕卡行数上限&nbsp;
          <span className="font-mono">
            {style.max_lines === 0 ? "（不限制）" : `≤ ${style.max_lines} 行`}
          </span>
        </label>
        <input
          type="range"
          min={0}
          max={5}
          step={1}
          value={style.max_lines}
          disabled={disabled}
          onChange={(e) => update({ max_lines: +e.target.value })}
          className="w-full h-1.5 accent-primary"
        />
        <p className="text-[9px] text-muted-foreground leading-tight">
          {(() => {
            if (style.max_lines === 0) return "不限制行数，字幕不会自动拆卡";
            const available = refW - style.margin_l - style.margin_r;
            const perLine = Math.max(1, Math.floor(available / style.font_size * 0.85));
            return `约每行 ${perLine} 字，超过 ${style.max_lines * perLine} 字时自动拆成两张字幕卡`;
          })()}
        </p>
      </div>
    </div>
  );
}

// ============================================================
// SubtitleConfigDialog（弹窗形式的字幕样式配置）
// ============================================================
export function SubtitleConfigDialog({
  open,
  onClose,
  style,
  onChange,
  frameDataUrl,
  videoAspectRatio,
  videoDuration,
  videoCurrentTime,
  taskId,
}: {
  open: boolean;
  onClose: () => void;
  style: SubtitleStyle;
  onChange: (s: SubtitleStyle) => void;
  frameDataUrl: string | null;
  videoAspectRatio: number;
  videoDuration: number;
  videoCurrentTime: number;
  taskId: string;
}) {
  const [localFrame, setLocalFrame] = useState<string | null>(frameDataUrl);
  const [localTime, setLocalTime] = useState(videoCurrentTime);
  const seekPendingRef = useRef(false);

  // 弹窗打开时同步外部传入的初始帧和时间
  useEffect(() => {
    if (open) {
      setLocalFrame(frameDataUrl);
      setLocalTime(videoCurrentTime);
    }
  }, [open, frameDataUrl, videoCurrentTime]);

  // 用后端 FFmpeg 抽帧，避免 canvas CORS 限制
  const captureFrameAt = useCallback((t: number) => {
    // 加时间戳防止浏览器缓存相同 t 的旧帧
    setLocalFrame(`/api/tasks/${taskId}/frame?t=${t.toFixed(3)}&_=${Date.now()}`);
  }, [taskId]);

  const seekAndCapture = useCallback((t: number) => {
    setLocalTime(t);
    // 同步主播放器位置
    const video = document.querySelector("video") as HTMLVideoElement | null;
    if (video) video.currentTime = t;
    // 直接请求后端抽帧，不需要等待 seeked 事件
    captureFrameAt(t);
  }, [captureFrameAt]);

  const fmtTime = (s: number) => {
    const mm = Math.floor(s / 60);
    const ss = Math.floor(s % 60);
    return `${String(mm).padStart(2, "0")}:${String(ss).padStart(2, "0")}`;
  };

  return (
    <Sheet open={open} onOpenChange={v => !v && onClose()}>
      <SheetContent
        side="right"
        className="w-[900px] sm:max-w-[900px] flex flex-col p-0 gap-0 bg-card border-l border-border"
      >
        {/* 顶部标题栏 */}
        <SheetHeader className="px-5 pt-5 pb-4 border-b border-border shrink-0">
          <SheetTitle className="flex items-center gap-2 text-base">
            <SlidersHorizontal className="w-4 h-4 text-primary" />
            字幕样式配置
          </SheetTitle>
          <SheetDescription className="text-xs text-muted-foreground">
            拖拽预览框边线或右侧滑块调整安全边距，拖动时间轴切换预览帧
          </SheetDescription>
        </SheetHeader>

        {/* 左右双栏主内容 */}
        <div className="flex flex-1 min-h-0 overflow-hidden">
          {/* 左栏：预览帧 + 时间轴 */}
          <div className="flex flex-col gap-2 p-4 border-r border-border" style={{ width: "52%", minWidth: 0 }}>
            <div className="flex-1 min-h-0 flex items-center justify-center">
              <SubtitlePreviewBox
                style={style}
                frameDataUrl={localFrame}
                videoAspectRatio={videoAspectRatio}
                onStyleChange={(patch) => onChange({ ...style, ...patch })}
              />
            </div>
            {/* 时间轴（置于预览框下方） */}
            {videoDuration > 0 && (
              <div className="shrink-0 flex items-center gap-2 pt-1">
                <span className="text-[10px] font-mono text-muted-foreground w-10 text-right shrink-0">
                  {fmtTime(localTime)}
                </span>
                <input
                  type="range"
                  min={0}
                  max={videoDuration}
                  step={0.1}
                  value={localTime}
                  onChange={(e) => seekAndCapture(+e.target.value)}
                  className="flex-1 h-1.5 accent-primary cursor-pointer"
                />
                <span className="text-[10px] font-mono text-muted-foreground w-10 shrink-0">
                  {fmtTime(videoDuration)}
                </span>
              </div>
            )}
          </div>

          {/* 右栏：样式控件（可滚动） */}
          <div className="flex-1 min-w-0 overflow-y-auto p-5">
            <SubtitleStyleEditor
              style={style}
              onChange={onChange}
              videoAspectRatio={videoAspectRatio}
            />
          </div>
        </div>

        {/* 底部操作栏 */}
        <div className="px-5 py-4 border-t border-border flex justify-end shrink-0">
          <Button size="sm" onClick={onClose} className="text-xs">
            完成
          </Button>
        </div>
      </SheetContent>
    </Sheet>
  );
}
