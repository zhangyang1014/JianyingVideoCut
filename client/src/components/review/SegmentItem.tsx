// ============================================================
// Segment Item — 字幕审计流单个片段卡片
// 注意：memo + 自定义比较器是性能优化关键，勿破坏
// ============================================================

import { memo, useState, useEffect, useRef } from "react";
import { usePlaybackTime } from "@/hooks/usePlaybackTime";
import type { PlaybackClock } from "@/lib/playback-clock";
import {
  Check, ChevronDown, Clock, Crown, Eye, EyeOff, Merge, Pencil, Scissors,
  Minus, Plus, SkipBack, SkipForward, X,
} from "lucide-react";
import {
  Tooltip,
  TooltipTrigger,
  TooltipContent,
} from "@/components/ui/tooltip";
import {
  SPEAKER_COLORS, formatTimestamp, formatDuration,
  getEffectiveSubtitleText,
  type Segment,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import { parseWordTag, secsToInput, inputToSecs, hexToRgba, actionToColor } from "./utils";

const PRODUCT_ROLE_LABELS: Record<string, string> = {
  product_mention: "产品名",
  product_opening: "产品开场",
  selling_point: "卖点",
  differentiation_story: "差异化故事",
  use_case: "使用场景",
  closing: "收束",
  cta: "CTA",
  transition: "串场",
  context: "上下文",
};

const ACTION_LABELS: Record<Segment["action"], string> = {
  keep: "保留",
  delete: "删除",
  subtitle_fix: "字幕去重",
  text_fix: "ASR纠错",
  split: "裁断",
  merge_next: "合并下段",
};

type SegmentWithSubtitleLineBreak = Segment & {
  subtitle_line_break?: string | null;
};

function getCandidateEditLabel(source: string) {
  return source === "filler" ? "语气词" : source === "short_segment" ? "短片段" : source;
}

function getProductSemanticSummary(segment: Segment) {
  const semantic = segment.product_semantic;
  if (!semantic) return null;
  const entity = semantic.product_entities?.[0];
  const productName = entity?.canonical_name || entity?.name || entity?.category || "";
  const roles = (semantic.roles ?? [])
    .map((role) => PRODUCT_ROLE_LABELS[role] || role)
    .slice(0, 3);
  const sellingPoint = semantic.angle_label || semantic.selling_points?.[0] || "";
  return {
    productName,
    roles,
    sellingPoint,
    excludeReason: semantic.exclude_reason || "",
    eligible: semantic.eligible_for_highlight,
  };
}

export const SegmentItem = memo(function SegmentItem({
  segment,
  isActive,
  isSelected,
  isFirst = false,
  isLast = false,
  onClick,
  onToggle,
  onCharClick,
  onSeek,
  activeCharTime: fallbackCharTime,
  playbackClock,
  onEditSave,
  onEditStart,
  onEditEnd,
  onMergePrevious,
  onMergeNext,
  readOnly = false,
  subtitleReadOnly,
  decisionReadOnly,
  wordEditMode = false,
  selectedWordIndices,
  onWordEditEnter,
  onWordEditExit,
  onWordToggle,
  onWordCommit,
  quickSplitMode = false,
  splitMode = false,
  onSplitEnter,
  onSplitExit,
  onSplitCommit,
  goldenQuoteIndex = -1,
  onEditReason,
  onSetClipStart,
  onSetClipEnd,
  isOutOfClip = false,
  isInFocusedHighlight = false,
  highlightMembershipAction,
  highlightMembershipPending = false,
  onHighlightMembershipToggle,
}: {
  segment: Segment;
  isActive: boolean;
  isSelected: boolean;
  isFirst?: boolean;
  isLast?: boolean;
  onClick: (e: React.MouseEvent) => void;
  onToggle: (e: React.MouseEvent) => void;
  onCharClick: (time: number) => void;
  onSeek: (time: number) => void;
  activeCharTime: number;
  playbackClock?: PlaybackClock;
  onEditSave: (segId: string, newText: string, start: number, end: number) => void;
  onEditStart?: (segId: string) => void;
  onEditEnd?: (segId: string, outcome: "save" | "cancel") => void;
  onMergePrevious?: (segId: string) => void;
  onMergeNext?: (segId: string) => void;
  readOnly?: boolean;
  subtitleReadOnly?: boolean;
  decisionReadOnly?: boolean;
  wordEditMode?: boolean;
  selectedWordIndices?: Set<number>;
  onWordEditEnter?: (segId: string) => void;
  onWordEditExit?: () => void;
  onWordToggle?: (segId: string, wordIndex: number, shiftKey: boolean) => void;
  onWordCommit?: (segId: string) => void;
  quickSplitMode?: boolean;
  splitMode?: boolean;
  onSplitEnter?: (segId: string) => void;
  onSplitExit?: () => void;
  onSplitCommit?: (segId: string, splitAtIndex: number) => void;
  /** 金句开场序号（-1 表示不是金句）*/
  goldenQuoteIndex?: number;
  /** 点击 reason 标签修改原因 */
  onEditReason?: (segId: string, action: "keep" | "delete") => void;
  /** 将此段起点设置为视频裁剪起点 */
  onSetClipStart?: (segStart: number) => void;
  /** 将此段终点设置为视频裁剪终点 */
  onSetClipEnd?: (segEnd: number) => void;
  /** 此段在裁剪区间之外（仅影响视觉，不禁用交互） */
  isOutOfClip?: boolean;
  /** 当前左侧选中的产品集锦包含此段 */
  isInFocusedHighlight?: boolean;
  /** 当前句子相对正在编辑的产品集锦可执行的成员操作 */
  highlightMembershipAction?: "add" | "remove" | null;
  /** 当前句子的集锦成员操作正在保存 */
  highlightMembershipPending?: boolean;
  /** 将句子加入或移出正在编辑的产品集锦 */
  onHighlightMembershipToggle?: (segId: string, action: "add" | "remove") => void;
}) {
  const activeCharTime = usePlaybackTime(playbackClock, isActive, fallbackCharTime);
  const speakerColor = SPEAKER_COLORS[segment.speaker || "default"] || SPEAKER_COLORS.default;
  const isDelete = segment.action === "delete";
  const isKeep = segment.action === "keep";
  // 字幕层修正：音频保留不剪切，只在字幕显示层去掉重复词（结巴）
  const isSubtitleFix = segment.action === "subtitle_fix";
  // ASR 识别错误纠正：音频保留，字幕显示修正后文本
  const isTextFix = segment.action === "text_fix";
  // 碎段合并：与下一段合并为一张字幕卡
  const isMergeNext = segment.action === "merge_next";
  // 后处理已完成的自动合并：当前段是保留卡，下一段已从列表移除。
  const isMerged = Boolean(segment.merged);
  const autoMergedByAudit = isMerged && segment.claude_action === "merge_next";
  const candidateEdits = segment.candidate_edits ?? [];
  const sourceSegmentCount = segment.source_segment_ids?.length ?? 0;
  const isSemanticCompiled = Boolean(segment.compiled_from_operation || segment.semantic_unit_id || sourceSegmentCount > 1);
  const productSemantic = getProductSemanticSummary(segment);
  const effectiveSubtitleText = getEffectiveSubtitleText(segment);
  const hasSubtitleOverride = Boolean(segment.subtitle_text);
  const subtitleLineBreak = (segment as SegmentWithSubtitleLineBreak).subtitle_line_break;
  const canEditSubtitle = !(subtitleReadOnly ?? readOnly);
  const canEditDecision = !(decisionReadOnly ?? readOnly);
  const wordsText = segment.words?.map((w) => parseWordTag(w.word).text).join("") ?? "";
  const normalizeText = (value: string) => value.replace(/\s+/g, "");
  const originalDiffersFromEffective = normalizeText(segment.text) !== normalizeText(effectiveSubtitleText);
  const textCanCollapse = effectiveSubtitleText.length > 44 || effectiveSubtitleText.includes("\n");
  // 手动切换保留/删除也会设置 user_override；只在文本确实改写后才跳过旧 ASR words。
  const hasWords = Boolean(
    segment.words?.length &&
    (!segment.user_override || normalizeText(segment.text) === normalizeText(wordsText))
  );
  const canSplit = hasWords && (segment.words?.length ?? 0) >= 2 && !isDelete;
  const isSplitActive = splitMode || (quickSplitMode && canSplit);
  const shouldRenderInteractiveWords = hasWords && (
    (!hasSubtitleOverride && !segment.display_text) ||
    wordEditMode ||
    isSplitActive
  );

  // Bar1：AI 建议颜色（claude_action 有值时用 claude_action，否则 fallback 到 action）
  const aiAction = segment.claude_action ?? segment.action;
  const aiBarColor = actionToColor(aiAction);

  // Bar2：人工修改颜色（有人工介入时固定显示黄色，否则近透明）
  const humanModified = !!(
    (segment.user_override && !autoMergedByAudit) ||
    (segment.claude_action && segment.action !== segment.claude_action)
  ) && !autoMergedByAudit;
  const humanBarColor = humanModified ? 'rgba(234,179,8,0.85)' : 'rgba(255,255,255,0.06)';
  const compactStatusLabels = [
    ACTION_LABELS[segment.action],
    hasSubtitleOverride ? "字幕覆盖" : "",
    humanModified ? "已改" : "",
    isMerged ? "已合并" : "",
    isOutOfClip ? "区间外" : "",
  ].filter(Boolean);
  const detailsCount = [
    originalDiffersFromEffective,
    isSemanticCompiled,
    Boolean(productSemantic),
    candidateEdits.length > 0 && !isDelete,
    Boolean(subtitleLineBreak) && !isDelete,
    isMergeNext || (isMerged && !isMergeNext),
    Boolean(segment.reason),
  ].filter(Boolean).length;
  const hasDetails = detailsCount > 0;
  const canEditHighlightMembership = Boolean(
    highlightMembershipAction && onHighlightMembershipToggle,
  );

  // 片段底色：删除状态用红色，其他状态跟随说话人角色颜色（高透明度）
  const segmentBg = isDelete
    ? "rgba(220,38,38,0.12)"
    : hexToRgba(speakerColor, 0.15);

  const [isEditing, setIsEditing] = useState(false);
  const [editText, setEditText] = useState(segment.text);
  const [editStart, setEditStart] = useState(0);
  const [editEnd, setEditEnd] = useState(0);
  const [startInput, setStartInput] = useState("");
  const [endInput, setEndInput] = useState("");
  const [isTextExpanded, setIsTextExpanded] = useState(false);
  const [isDetailsOpen, setIsDetailsOpen] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (isEditing && textareaRef.current) {
      textareaRef.current.focus();
      textareaRef.current.select();
    }
  }, [isEditing]);

  // 同步外部 text 更新
  useEffect(() => {
    if (!isEditing) setEditText(effectiveSubtitleText);
  }, [effectiveSubtitleText, isEditing]);

  const handleDoubleClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!canEditSubtitle) return;
    setEditText(effectiveSubtitleText);
    setEditStart(segment.start);
    setEditEnd(segment.end);
    setStartInput(secsToInput(segment.start));
    setEndInput(secsToInput(segment.end));
    setIsEditing(true);
    onEditStart?.(segment.id);
  };

  const handleEditSave = () => {
    const trimmed = editText.trim();
    const parsedStart = canEditDecision ? inputToSecs(startInput) ?? editStart : segment.start;
    const parsedEnd = canEditDecision ? inputToSecs(endInput) ?? editEnd : segment.end;
    onEditSave(segment.id, trimmed, parsedStart, parsedEnd);
    setIsEditing(false);
    onEditEnd?.(segment.id, "save");
  };

  const handleEditCancel = () => {
    setEditText(effectiveSubtitleText);
    setIsEditing(false);
    onEditEnd?.(segment.id, "cancel");
  };

  const handleEditKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleEditSave();
    } else if (e.key === "Escape") {
      e.preventDefault();
      handleEditCancel();
    }
  };

  const ruleShort: Record<string, string> = {
    "Rule 1: 重说识别": "重说",
    "Rule 2: 残句清理": "残句",
    "Rule 3: 语气词切除": "语气词",
    "Rule 4: 词内去重": "结巴",
    "Rule 5: 语义去重": "语义重复",
    "Rule 6: 句内重复": "句内重复",
    "Rule 7: 问答闭环": "问答",
    "Rule 8: 气口对齐": "气口",
    "手动调整": "手动",
  };

  return (
    <div
      className={cn(
        "group relative rounded-lg px-3 pt-1.5 pb-2 cursor-pointer transition-all duration-150",
        isActive ? "ring-1 ring-primary/50" : "",
        isInFocusedHighlight ? "ring-2 ring-orange-400/80 shadow-[0_0_0_1px_rgba(251,146,60,0.25)]" : "",
        isSelected ? "ring-2 ring-blue-500/70 bg-blue-500/10" : "",
        isDelete
          ? "segment-delete"
          : isSubtitleFix
          ? "segment-subtitle-fix"
          : isTextFix
          ? "segment-text-fix"
          : isMergeNext || isMerged
          ? "segment-merge-next"
          : "segment-keep",
        quickSplitMode && canSplit ? "cursor-scissors" : "",
        "hover:brightness-110"
      )}
      style={{ background: isInFocusedHighlight ? `linear-gradient(90deg, rgba(251,146,60,0.18), ${segmentBg})` : segmentBg }}
      onClick={onClick}
      onDoubleClick={handleDoubleClick}
    >
      {/* 区间外内容遮罩：仅对内容区降低透明度，按钮层保持不变 */}
      <div className={isOutOfClip ? "opacity-60" : undefined}>
      {/* Bar1：AI 建议竖线 */}
      <div
        className="absolute left-0 top-0 bottom-0 w-[5px] rounded-l-lg"
        style={{ background: aiBarColor }}
        title={`AI建议：${aiAction}`}
      />

      {/* Bar2：人工修改竖线 */}
      <div
        className="absolute left-[7px] top-0 bottom-0 w-[4px] rounded-l-sm"
        style={{ background: humanBarColor }}
        title={humanModified ? '有人工修改' : '无人工修改'}
      />

      {/* 多选勾选标记 */}
      {isInFocusedHighlight && (
        <div className="absolute top-1.5 left-1/2 -translate-x-1/2 z-10">
          <span className="text-[9px] px-1.5 py-0.5 rounded-full font-medium whitespace-nowrap bg-orange-500/20 text-orange-300 border border-orange-500/30">
            集锦句子
          </span>
        </div>
      )}

      {/* 多选勾选标记 */}
      {isSelected && (
        <div className="absolute top-1.5 right-1.5 w-4 h-4 rounded-full bg-blue-500 flex items-center justify-center z-10">
          <Check className="w-2.5 h-2.5 text-white" />
        </div>
      )}

      {/* 金句开场标识 */}
      {goldenQuoteIndex >= 0 && (
        <div
          className="absolute top-1.5 right-1.5 flex items-center gap-0.5 bg-amber-500/90 text-black text-[10px] font-bold px-1.5 py-0.5 rounded z-10"
          title={`金句开场第 ${goldenQuoteIndex + 1} 个`}
        >
          <Crown className="w-2.5 h-2.5" />
          {goldenQuoteIndex + 1}
        </div>
      )}

      {/* 头部行：角色 · 时间 · 决策状态 */}
      <div className="flex items-center gap-1.5 pl-[14px] mb-1 min-w-0 pr-14">
        {segment.speaker && (
          <span
            className="inline-flex items-center h-[18px] px-1.5 rounded text-[10px] font-bold shrink-0 select-none"
            style={{ background: speakerColor, color: "rgba(0,0,0,0.60)" }}
          >
            {({ spk0: "主播", spk1: "嘉A", spk2: "嘉B", spk3: "嘉C" } as Record<string, string>)[segment.speaker] ?? segment.speaker}
          </span>
        )}
        <span className="timestamp text-[10px]">{formatTimestamp(segment.start)}</span>
        <span className="text-muted-foreground/35 text-[9px] leading-none">-</span>
        <span className="timestamp text-[10px] opacity-55">{formatTimestamp(segment.end)}</span>
        <span className="text-[10px] font-mono text-muted-foreground/35 shrink-0">
          {formatDuration(segment.end - segment.start)}
        </span>
        {compactStatusLabels.slice(0, 3).map((label) => (
          <span
            key={label}
            className={cn(
              "text-[9px] px-1 py-px rounded-sm font-medium shrink-0",
              label === "删除"
                ? "bg-red-500/15 text-red-300"
                : label === "保留"
                ? "bg-green-500/15 text-green-300"
                : label === "ASR纠错"
                ? "bg-sky-500/15 text-sky-300"
                : label === "合并下段" || label === "已合并"
                ? "bg-violet-500/15 text-violet-300"
                : label === "区间外"
                ? "bg-white/10 text-white/50"
                : "bg-amber-500/15 text-amber-300"
            )}
            title={compactStatusLabels.join(" · ")}
          >
            {label}
          </span>
        ))}
      </div>

      {/* Text */}
      <div className="flex-1 min-w-0 pl-[14px]">
          {isEditing ? (
            <div className="flex flex-col gap-1.5" onClick={(e) => e.stopPropagation()}>
              {/* 当前播放位置 */}
              <div className="flex items-center gap-1 text-[10px] text-muted-foreground/60">
                <Clock className="w-3 h-3" />
                <span>当前播放:</span>
                <span className="font-mono text-muted-foreground">{secsToInput(activeCharTime)}</span>
              </div>

              {/* 时间编辑行 */}
              {canEditDecision && (
              <div className="flex flex-col gap-1">
                {/* 起点 */}
                <div className="flex items-center gap-1">
                  <span className="text-[10px] text-muted-foreground w-6 shrink-0">起点</span>
                  <input
                    type="text"
                    value={startInput}
                    onChange={(e) => setStartInput(e.target.value)}
                    onBlur={() => {
                      const parsed = inputToSecs(startInput);
                      if (parsed !== null) setStartInput(secsToInput(parsed));
                      else setStartInput(secsToInput(editStart));
                    }}
                    className="w-24 bg-background border border-border rounded px-1.5 py-0.5 text-[11px] font-mono text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
                  />
                  <button
                    onClick={() => { const t = inputToSecs(startInput); if (t !== null) onSeek(t); }}
                    className="text-[10px] px-1.5 py-0.5 rounded bg-secondary border border-border text-muted-foreground hover:text-foreground transition-colors"
                    title="跳转到该时间"
                  >
                    ▶跳到
                  </button>
                  <button
                    onClick={() => { setStartInput(secsToInput(activeCharTime)); setEditStart(activeCharTime); }}
                    className="text-[10px] px-1.5 py-0.5 rounded bg-secondary border border-border text-muted-foreground hover:text-foreground transition-colors"
                    title="捕获当前播放时间"
                  >
                    ←捕获
                  </button>
                </div>
                {/* 终点 */}
                <div className="flex items-center gap-1">
                  <span className="text-[10px] text-muted-foreground w-6 shrink-0">终点</span>
                  <input
                    type="text"
                    value={endInput}
                    onChange={(e) => setEndInput(e.target.value)}
                    onBlur={() => {
                      const parsed = inputToSecs(endInput);
                      if (parsed !== null) setEndInput(secsToInput(parsed));
                      else setEndInput(secsToInput(editEnd));
                    }}
                    className="w-24 bg-background border border-border rounded px-1.5 py-0.5 text-[11px] font-mono text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
                  />
                  <button
                    onClick={() => { const t = inputToSecs(endInput); if (t !== null) onSeek(t); }}
                    className="text-[10px] px-1.5 py-0.5 rounded bg-secondary border border-border text-muted-foreground hover:text-foreground transition-colors"
                    title="跳转到该时间"
                  >
                    ▶跳到
                  </button>
                  <button
                    onClick={() => { setEndInput(secsToInput(activeCharTime)); setEditEnd(activeCharTime); }}
                    className="text-[10px] px-1.5 py-0.5 rounded bg-secondary border border-border text-muted-foreground hover:text-foreground transition-colors"
                    title="捕获当前播放时间"
                  >
                    ←捕获
                  </button>
                </div>
              </div>
              )}

              {/* 文本编辑框 */}
              <textarea
                ref={textareaRef}
                value={editText}
                onChange={(e) => setEditText(e.target.value)}
                onKeyDown={handleEditKeyDown}
                className="w-full bg-background border border-primary/50 rounded px-2 py-1 text-sm text-foreground resize-none focus:outline-none focus:ring-1 focus:ring-primary"
                rows={Math.max(2, editText.split("\n").length)}
              />
              <div className="flex items-center gap-1.5">
                <button
                  onClick={(e) => { e.stopPropagation(); handleEditSave(); }}
                  className="flex items-center gap-1 px-2 py-0.5 rounded bg-green-600/20 border border-green-600/40 text-green-400 hover:bg-green-600/30 text-[11px] font-medium transition-colors"
                  title="确认保存 (Enter)"
                >
                  <Check className="w-3 h-3" />
                  保存
                </button>
                <button
                  onClick={(e) => { e.stopPropagation(); handleEditCancel(); }}
                  className="flex items-center gap-1 px-2 py-0.5 rounded bg-secondary border border-border text-muted-foreground hover:text-foreground text-[11px] transition-colors"
                  title="取消 (Esc)"
                >
                  <X className="w-3 h-3" />
                  取消
                </button>
                <span className="text-[10px] text-muted-foreground/40 ml-auto">Shift+Enter 换行</span>
              </div>
            </div>
          ) : (
            <>
              {/* 字级编辑模式：悬浮确认工具条 */}
              {wordEditMode && selectedWordIndices && selectedWordIndices.size > 0 && (
                <div className="flex items-center gap-2 mb-1.5 px-2 py-1 rounded bg-red-900/30 border border-red-500/30">
                  <span className="text-[11px] text-red-300">
                    已选 {selectedWordIndices.size} 字
                    （约 {(() => {
                      const ws = segment.words || [];
                      let dur = 0;
                      selectedWordIndices.forEach((idx) => { if (ws[idx]) dur += ws[idx].end - ws[idx].start; });
                      return dur.toFixed(2);
                    })()}秒）
                  </span>
                  <div className="flex items-center gap-1 ml-auto">
                    <button
                      onClick={(e) => { e.stopPropagation(); onWordCommit?.(segment.id); }}
                      className="flex items-center gap-1 px-2 py-0.5 rounded bg-red-600/30 border border-red-500/40 text-red-300 hover:bg-red-600/50 text-[11px] font-medium transition-colors"
                    >
                      <X className="w-3 h-3" />
                      确认删除
                    </button>
                    <button
                      onClick={(e) => { e.stopPropagation(); onWordEditExit?.(); }}
                      className="flex items-center gap-1 px-2 py-0.5 rounded bg-secondary border border-border text-muted-foreground hover:text-foreground text-[11px] transition-colors"
                    >
                      <X className="w-3 h-3" />
                      取消
                    </button>
                  </div>
                </div>
              )}
              {/* 裁断模式：提示条 */}
              {splitMode && (
                <div className="flex items-center gap-2 mb-1.5 px-2 py-1 rounded bg-green-900/30 border border-green-500/30">
                  <Scissors className="w-3.5 h-3.5 text-green-400 shrink-0" />
                  <span className="text-[11px] text-green-300">点击文字 — 将在所点位置之前裁断为两段</span>
                  <button
                    onClick={(e) => { e.stopPropagation(); onSplitExit?.(); }}
                    className="flex items-center gap-1 px-2 py-0.5 rounded bg-secondary border border-border text-muted-foreground hover:text-foreground text-[11px] transition-colors ml-auto"
                  >
                    <X className="w-3 h-3" />
                    取消
                  </button>
                </div>
              )}
              <p className={cn(
                "text-sm leading-snug break-all",
                !isTextExpanded && textCanCollapse && !wordEditMode && !isSplitActive
                  ? "overflow-hidden [display:-webkit-box] [-webkit-line-clamp:2] [-webkit-box-orient:vertical]"
                  : "",
                isDelete ? "text-foreground/85 line-through decoration-red-400/70" : "text-foreground"
              )}>
                {shouldRenderInteractiveWords
                  ? segment.words!.map((w, i) => {
                      const { text: displayText, isStu, isFil } = parseWordTag(w.word);
                      const isWordSelected = wordEditMode && selectedWordIndices?.has(i);
                      const isActiveChar =
                        !wordEditMode &&
                        activeCharTime >= w.start &&
                        activeCharTime < w.end;

                      const tagHint = [
                        isStu ? "⚡结巴" : "",
                        isFil ? "💬语气词" : "",
                      ].filter(Boolean).join(" · ");
                      const titleStr = [
                        tagHint,
                        `${formatTimestamp(w.start)} → ${formatTimestamp(w.end)}`,
                      ].filter(Boolean).join(" · ");

                      // 词级说话人颜色：当词的 speaker 与本段 speaker 不同时，加下划线着色标出插话者
                      const wordSpeakerColor =
                        !wordEditMode &&
                        !isSplitActive &&
                        w.speaker &&
                        w.speaker !== segment.speaker
                          ? SPEAKER_COLORS[w.speaker] || SPEAKER_COLORS.default
                          : undefined;

                      // 裁断模式：index=0 不可切（切出空段），index>=1 可作为切点
                      const isSplitCandidate = isSplitActive && i >= 1;

                      return (
                        <span
                          key={`${w.start}-${i}`}
                          onClick={(e) => {
                            e.stopPropagation();
                            if (isSplitActive) {
                              if (isSplitCandidate) onSplitCommit?.(segment.id, i);
                            } else if (wordEditMode) {
                              onWordToggle?.(segment.id, i, e.shiftKey);
                            } else {
                              onCharClick(w.start);
                            }
                          }}
                          title={isSplitActive
                            ? (isSplitCandidate ? `在此字之前裁断 (${formatTimestamp(w.start)})` : "首字不可裁断")
                            : wordSpeakerColor ? `[${w.speaker}] ${titleStr}` : titleStr}
                          style={wordSpeakerColor ? {
                            color: wordSpeakerColor,
                            textDecorationColor: wordSpeakerColor,
                            textDecorationLine: "underline",
                            textDecorationStyle: "dotted",
                            textUnderlineOffset: "3px",
                          } : undefined}
                          className={cn(
                            "rounded-sm transition-colors duration-75",
                            isSplitActive
                              ? cn(
                                  "select-none",
                                  isSplitCandidate
                                    ? "cursor-scissors border-l-2 border-transparent hover:border-green-400 hover:bg-green-500/20"
                                    : "cursor-not-allowed opacity-50"
                                )
                              : wordEditMode
                              ? cn(
                                  "cursor-pointer select-none",
                                  isWordSelected
                                    ? "bg-red-500/30 text-red-300 line-through decoration-red-400/70"
                                    : "hover:bg-primary/20"
                                )
                              : cn(
                                  isDelete
                                    ? "cursor-pointer hover:bg-red-500/20"
                                    : "cursor-pointer hover:bg-primary/20",
                                  isStu && !isDelete && "text-orange-400/80 line-through decoration-orange-400/60",
                                  isFil && !isDelete && !isStu && "text-muted-foreground/50",
                                  isActiveChar && (
                                    isDelete
                                      ? "bg-red-500/35 text-red-100 font-medium"
                                      : "bg-primary/40 text-primary font-medium"
                                  )
                                )
                          )}
                        >
                          {displayText}
                        </span>
                      );
                    })
                  : effectiveSubtitleText
                }
              </p>

              {textCanCollapse && (
                <button
                  type="button"
                  className="mt-0.5 text-[10px] text-muted-foreground/65 hover:text-foreground transition-colors"
                  onClick={(e) => {
                    e.stopPropagation();
                    setIsTextExpanded((value) => !value);
                  }}
                >
                  {isTextExpanded ? "收起全文" : "展开全文"}
                </button>
              )}

              {(segment.reason || hasDetails) && (
                <div className="mt-1 flex items-center gap-1.5 min-w-0">
                  {segment.reason && (
                    <>
                      {segment.rule && (
                        <span className={cn(
                          "text-[10px] px-1.5 py-0.5 rounded font-mono shrink-0",
                          isDelete
                            ? "bg-red-900/40 text-red-300"
                            : isTextFix
                            ? "bg-sky-900/30 text-sky-400"
                            : isMergeNext
                            ? "bg-violet-900/30 text-violet-400"
                            : isKeep
                            ? "bg-green-900/40 text-green-400"
                            : "bg-amber-900/30 text-amber-400"
                        )}>
                          {ruleShort[segment.rule] || segment.rule}
                        </span>
                      )}
                      {canEditDecision && (isKeep || isDelete) && onEditReason ? (
                        <button
                          className={cn(
                            "group/reason min-w-0 flex-1 flex items-center gap-1 text-[10px] leading-tight rounded px-1 -ml-1 transition-colors",
                            isKeep
                              ? "text-green-300 hover:bg-green-900/30"
                              : "text-amber-100 hover:bg-amber-900/20"
                          )}
                          onClick={(e) => {
                            e.stopPropagation();
                            onEditReason(segment.id, isKeep ? "keep" : "delete");
                          }}
                          title={`点击修改原因：${segment.reason}`}
                        >
                          <span className="truncate">{segment.reason}</span>
                          <Pencil className="w-2.5 h-2.5 opacity-0 group-hover/reason:opacity-60 transition-opacity shrink-0" />
                        </button>
                      ) : (
                        <span
                          className={cn(
                            "text-[10px] leading-tight min-w-0 flex-1 truncate",
                            isKeep ? "text-green-300" : "text-amber-100"
                          )}
                          title={segment.reason}
                        >
                          {segment.reason}
                        </span>
                      )}
                    </>
                  )}
                  {hasDetails && (
                    <button
                      type="button"
                      className="ml-auto inline-flex items-center gap-0.5 text-[10px] text-muted-foreground/65 hover:text-foreground shrink-0 transition-colors"
                      aria-expanded={isDetailsOpen}
                      onClick={(e) => {
                        e.stopPropagation();
                        setIsDetailsOpen((value) => !value);
                      }}
                    >
                      <ChevronDown className={cn("w-3 h-3 transition-transform", isDetailsOpen ? "rotate-180" : "")} />
                      详情 {detailsCount}
                    </button>
                  )}
                </div>
              )}

              {isDetailsOpen && hasDetails && (
                <div
                  className="mt-1.5 space-y-1.5 rounded-md border border-white/5 bg-black/10 px-2 py-1.5 text-[10px] text-muted-foreground/80"
                  onClick={(e) => e.stopPropagation()}
                >
                  {originalDiffersFromEffective && (
                    <div className="space-y-1">
                      <div className="flex items-start gap-1.5">
                        <span className="px-1.5 py-0.5 rounded bg-white/10 text-muted-foreground shrink-0">原文</span>
                        <span className="leading-snug text-foreground/70 break-all">{segment.text}</span>
                      </div>
                      <div className="flex items-start gap-1.5">
                        <span className="px-1.5 py-0.5 rounded bg-blue-900/35 text-blue-300 shrink-0">字幕</span>
                        <span className="leading-snug text-blue-100/90 break-all">{effectiveSubtitleText}</span>
                      </div>
                    </div>
                  )}

                  {isMergeNext && (
                    <div className="flex items-start gap-1.5 text-violet-200/80">
                      <span className="px-1.5 py-0.5 rounded bg-violet-900/40 text-violet-300 shrink-0">合并</span>
                      <span>将与下一段合并为一张字幕卡</span>
                    </div>
                  )}
                  {isMerged && !isMergeNext && (
                    <div className="flex items-start gap-1.5 text-violet-200/80">
                      <span className="px-1.5 py-0.5 rounded bg-violet-900/40 text-violet-300 shrink-0">已合并</span>
                      <span>AI 审计已把相邻片段合并为这一张字幕卡</span>
                    </div>
                  )}

                  {isSemanticCompiled && (
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className="px-1.5 py-0.5 rounded bg-cyan-900/35 text-cyan-300 font-mono shrink-0">语义稿</span>
                      {sourceSegmentCount > 1 && <span className="text-cyan-200/75">来源 {sourceSegmentCount} 个原始片段</span>}
                      {segment.semantic_unit_id && <span className="text-muted-foreground/60 font-mono">{segment.semantic_unit_id}</span>}
                    </div>
                  )}

                  {productSemantic && (
                    <div className="flex flex-wrap items-center gap-1.5">
                      {productSemantic.productName && (
                        <span className="px-1.5 py-0.5 rounded bg-orange-900/35 text-orange-300 font-mono shrink-0">
                          产品 {productSemantic.productName}
                        </span>
                      )}
                      {productSemantic.roles.map((role) => (
                        <span key={role} className="px-1.5 py-0.5 rounded bg-cyan-900/30 text-cyan-200 shrink-0">
                          {role}
                        </span>
                      ))}
                      {productSemantic.sellingPoint && (
                        <span className="px-1.5 py-0.5 rounded bg-green-900/30 text-green-200 shrink-0">
                          卖点 {productSemantic.sellingPoint}
                        </span>
                      )}
                      {!productSemantic.eligible && productSemantic.excludeReason && (
                        <span className="px-1.5 py-0.5 rounded bg-red-900/30 text-red-200 shrink-0">
                          排除 {productSemantic.excludeReason}
                        </span>
                      )}
                    </div>
                  )}

                  {candidateEdits.length > 0 && !isDelete && (
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className="px-1.5 py-0.5 rounded bg-primary/10 text-primary font-mono shrink-0">
                        候选 {candidateEdits.length}
                      </span>
                      {candidateEdits.slice(0, 3).map((edit) => (
                        <span
                          key={edit.id}
                          className="px-1.5 py-0.5 rounded bg-secondary/70 text-muted-foreground"
                          title={`${edit.reason} · ${formatTimestamp(edit.start)} → ${formatTimestamp(edit.end)}`}
                        >
                          {getCandidateEditLabel(edit.source)}
                          {edit.text ? `: ${edit.text}` : ""}
                        </span>
                      ))}
                    </div>
                  )}

                  {!isDelete && subtitleLineBreak && (
                    <div className="flex items-start gap-1.5 text-teal-200/80">
                      <span className="px-1.5 py-0.5 rounded bg-teal-900/40 text-teal-300 font-mono shrink-0">¶ 分行</span>
                      <span>"{subtitleLineBreak}" 起换第二张字幕卡</span>
                    </div>
                  )}

                  {segment.reason && (
                    <div className="flex items-start gap-1.5">
                      <span className="px-1.5 py-0.5 rounded bg-white/10 text-muted-foreground shrink-0">完整理由</span>
                      <span className="leading-snug text-foreground/70">{segment.reason}</span>
                    </div>
                  )}
                </div>
              )}
            </>
          )}
        </div>

      </div>{/* end 区间外内容遮罩 */}

        {/* 操作按钮区（readOnly 时隐藏，hover 时显示在右上角） */}
        {(canEditSubtitle || canEditDecision || canEditHighlightMembership) && !isSelected && (
          <div className="absolute top-1.5 right-1.5 flex items-center gap-0.5 z-10 opacity-0 group-hover:opacity-100 transition-opacity duration-75">
            {canEditHighlightMembership && highlightMembershipAction && (
              <Tooltip>
                <TooltipTrigger asChild>
                  <button
                    type="button"
                    aria-label={highlightMembershipAction === "add" ? "加入当前集锦" : "移出当前集锦"}
                    disabled={highlightMembershipPending}
                    className={cn(
                      "w-6 h-6 rounded flex items-center justify-center transition-colors disabled:cursor-wait disabled:opacity-50",
                      highlightMembershipAction === "add"
                        ? "bg-orange-900/45 text-orange-300 hover:bg-orange-900/70"
                        : "bg-orange-500/25 text-orange-200 ring-1 ring-orange-500/40 hover:bg-orange-500/40",
                    )}
                    onClick={(e) => {
                      e.stopPropagation();
                      onHighlightMembershipToggle?.(segment.id, highlightMembershipAction);
                    }}
                  >
                    {highlightMembershipAction === "add"
                      ? <Plus className="w-3.5 h-3.5" />
                      : <Minus className="w-3.5 h-3.5" />}
                  </button>
                </TooltipTrigger>
                <TooltipContent side="top" sideOffset={4}>
                  {highlightMembershipAction === "add" ? "加入当前集锦" : "移出当前集锦"}
                </TooltipContent>
              </Tooltip>
            )}
            {/* 编辑按钮 */}
            {canEditSubtitle && (
            <Tooltip>
              <TooltipTrigger asChild>
                <button
                  className="w-6 h-6 rounded flex items-center justify-center bg-blue-900/40 text-blue-400 hover:bg-blue-900/60 transition-colors"
                  onClick={(e) => { e.stopPropagation(); handleDoubleClick(e); }}
                >
                  <Pencil className="w-3 h-3" />
                </button>
              </TooltipTrigger>
              <TooltipContent side="top" sideOffset={4}>编辑文本</TooltipContent>
            </Tooltip>
            )}
            {/* 字级删除按钮：仅 words 非空且非整段删除时显示 */}
            {canEditDecision && hasWords && !isDelete && (
              <Tooltip>
                <TooltipTrigger asChild>
                  <button
                    className={cn(
                      "w-6 h-6 rounded flex items-center justify-center transition-colors",
                      wordEditMode
                        ? "bg-red-900/60 text-red-300 ring-1 ring-red-500/50"
                        : "bg-orange-900/40 text-orange-400 hover:bg-orange-900/60"
                    )}
                    onClick={(e) => {
                      e.stopPropagation();
                      if (wordEditMode) { onWordEditExit?.(); } else { onWordEditEnter?.(segment.id); }
                    }}
                  >
                    <X className="w-3 h-3" />
                  </button>
                </TooltipTrigger>
                <TooltipContent side="top" sideOffset={4}>{wordEditMode ? "退出字级删除" : "字级删除"}</TooltipContent>
              </Tooltip>
            )}
            {/* 裁断按钮：仅 words >= 2 且非整段删除时显示 */}
            {canEditDecision && hasWords && (segment.words?.length ?? 0) >= 2 && !isDelete && (
              <Tooltip>
                <TooltipTrigger asChild>
                  <button
                    className={cn(
                      "w-6 h-6 rounded flex items-center justify-center transition-colors",
                      splitMode
                        ? "bg-green-900/60 text-green-300 ring-1 ring-green-500/50"
                        : "bg-green-900/40 text-green-400 hover:bg-green-900/60"
                    )}
                    onClick={(e) => {
                      e.stopPropagation();
                      if (splitMode) { onSplitExit?.(); } else { onSplitEnter?.(segment.id); }
                    }}
                  >
                    <Scissors className="w-3 h-3" />
                  </button>
                </TooltipTrigger>
                <TooltipContent side="top" sideOffset={4}>{splitMode ? "退出裁断" : "裁断句段"}</TooltipContent>
              </Tooltip>
            )}
            {/* 合并上一段按钮 */}
            {canEditDecision && !isFirst && (
              <Tooltip>
                <TooltipTrigger asChild>
                  <button
                    className="w-6 h-6 rounded flex items-center justify-center bg-violet-900/40 text-violet-400 hover:bg-violet-900/60 transition-colors"
                    onClick={(e) => { e.stopPropagation(); onMergePrevious?.(segment.id); }}
                  >
                    <Merge className="w-3 h-3 rotate-180" />
                  </button>
                </TooltipTrigger>
                <TooltipContent side="top" sideOffset={4}>合并到上一段</TooltipContent>
              </Tooltip>
            )}
            {/* 合并下一段按钮 */}
            {canEditDecision && !isLast && (
              <Tooltip>
                <TooltipTrigger asChild>
                  <button
                    className="w-6 h-6 rounded flex items-center justify-center bg-violet-900/40 text-violet-400 hover:bg-violet-900/60 transition-colors"
                    onClick={(e) => { e.stopPropagation(); onMergeNext?.(segment.id); }}
                  >
                    <Merge className="w-3 h-3" />
                  </button>
                </TooltipTrigger>
                <TooltipContent side="top" sideOffset={4}>合并到下一段</TooltipContent>
              </Tooltip>
            )}
            {/* 设置为视频起点/终点 */}
            {canEditDecision && onSetClipStart && (
              <>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <button
                      className="w-6 h-6 rounded flex items-center justify-center bg-amber-900/40 text-amber-400 hover:bg-amber-900/60 transition-colors"
                      onClick={(e) => { e.stopPropagation(); onSetClipStart(segment.start); }}
                    >
                      <SkipBack className="w-3 h-3" />
                    </button>
                  </TooltipTrigger>
                  <TooltipContent side="top" sideOffset={4}>设置为视频起点</TooltipContent>
                </Tooltip>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <button
                      className="w-6 h-6 rounded flex items-center justify-center bg-amber-900/40 text-amber-400 hover:bg-amber-900/60 transition-colors"
                      onClick={(e) => { e.stopPropagation(); onSetClipEnd?.(segment.end); }}
                    >
                      <SkipForward className="w-3 h-3" />
                    </button>
                  </TooltipTrigger>
                  <TooltipContent side="top" sideOffset={4}>设置为视频终点</TooltipContent>
                </Tooltip>
              </>
            )}
            {/* 保留/删除 Toggle */}
            {canEditDecision && (
            <Tooltip>
              <TooltipTrigger asChild>
                <button
                  className={cn(
                    "w-6 h-6 rounded flex items-center justify-center transition-colors",
                    isDelete
                      ? "bg-green-900/40 text-green-400 hover:bg-green-900/60"
                      : "bg-red-900/40 text-red-400 hover:bg-red-900/60"
                  )}
                  onClick={onToggle}
                >
                  {isDelete ? <Eye className="w-3.5 h-3.5" /> : <EyeOff className="w-3.5 h-3.5" />}
                </button>
              </TooltipTrigger>
              <TooltipContent side="top" sideOffset={4}>{isDelete ? "恢复保留" : "标记删除"}</TooltipContent>
            </Tooltip>
            )}
          </div>
        )}

      {/* 人工操作徽章：只根据明确记录的操作类型显示，自动规则不冒充用户操作。 */}
      {segment.manual_action && !autoMergedByAudit && (
        <div className="absolute top-1 left-1/2 -translate-x-1/2 z-10">
          {(() => {
            const manualActionLabels = {
              delete: "你删除",
              restore: "你恢复",
              adjust: "你调整",
              merge: "你合并",
              split: "你裁断",
              word_delete: "你字级删除",
            } as const;
            const manualAction = segment.manual_action;
            const isUserRestore = manualAction === "restore";
            return (
              <span
                className={cn(
                  "text-[9px] px-1.5 py-0.5 rounded-full font-medium whitespace-nowrap",
                  isUserRestore
                    ? "bg-blue-500/20 text-blue-300 border border-blue-500/30"
                    : "bg-orange-500/20 text-orange-300 border border-orange-500/30"
                )}
                title={manualActionLabels[manualAction]}
              >
                {isUserRestore ? "↑ " : "✎ "}{manualActionLabels[manualAction]}
              </span>
            );
          })()}
        </div>
      )}

      {/* Duration */}
      <div className="absolute bottom-1.5 right-2 pointer-events-none">
        <span className="text-[10px] font-mono text-muted-foreground/40">
          {formatDuration(segment.end - segment.start)}
        </span>
      </div>
    </div>
  );
}, (prev, next) =>
  prev.segment === next.segment &&
  prev.isActive === next.isActive &&
  prev.isSelected === next.isSelected &&
  prev.isInFocusedHighlight === next.isInFocusedHighlight &&
  prev.highlightMembershipAction === next.highlightMembershipAction &&
  prev.highlightMembershipPending === next.highlightMembershipPending &&
  prev.isFirst === next.isFirst &&
  prev.isLast === next.isLast &&
  prev.activeCharTime === next.activeCharTime &&
  prev.playbackClock === next.playbackClock &&
  prev.readOnly === next.readOnly &&
  prev.subtitleReadOnly === next.subtitleReadOnly &&
  prev.decisionReadOnly === next.decisionReadOnly &&
  prev.wordEditMode === next.wordEditMode &&
  prev.selectedWordIndices === next.selectedWordIndices &&
  prev.quickSplitMode === next.quickSplitMode &&
  prev.splitMode === next.splitMode &&
  prev.isOutOfClip === next.isOutOfClip
);
