/**
 * ReviewWorkbench 共享工具函数
 * 时间格式化、词标签解析、片段字级切割/裁断、颜色工具
 */

import type { Segment } from "@/lib/api";

/** 今日只显示时间，非今日只显示日期。 */
export function formatAsrTime(isoStr: string): string {
  const date = new Date(isoStr);
  if (Number.isNaN(date.getTime())) return isoStr;

  const now = new Date();
  const isToday =
    date.getFullYear() === now.getFullYear() &&
    date.getMonth() === now.getMonth() &&
    date.getDate() === now.getDate();

  if (isToday) {
    return date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false });
  }
  return date.toLocaleDateString("zh-CN", {
    ...(date.getFullYear() === now.getFullYear() ? {} : { year: "numeric" as const }),
    month: "numeric",
    day: "numeric",
  });
}

/** 根据视频时长和模型速度系数估算处理时间 */
export function estimateTime(durationSec: number, speedFactor: number): string {
  if (durationSec <= 0) return "未知";
  const estimatedSec = durationSec / speedFactor;
  if (estimatedSec < 60) return `约 ${Math.ceil(estimatedSec)} 秒`;
  const mins = Math.ceil(estimatedSec / 60);
  if (mins < 60) return `约 ${mins} 分钟`;
  const hours = Math.floor(mins / 60);
  const remMins = mins % 60;
  return remMins > 0 ? `约 ${hours} 小时 ${remMins} 分钟` : `约 ${hours} 小时`;
}
// ============================================================
// 解析词内部标签（<STU>、<FIL s=X e=Y>、<FIL>）
// ============================================================
export interface ParsedWord {
  text: string;
  isStu: boolean; // 结巴/重复词
  isFil: boolean; // 语气词/填充词
}

export function parseWordTag(rawWord: string): ParsedWord {
  let text = rawWord;
  let isStu = false;
  let isFil = false;

  // 剥离 <STU> 前缀
  if (text.startsWith("<STU>")) {
    isStu = true;
    text = text.slice(5);
  }

  // 剥离 <FIL s=... e=...> 或 <FIL> 前缀
  if (text.startsWith("<FIL")) {
    const closeBracket = text.indexOf(">");
    if (closeBracket !== -1) {
      isFil = true;
      text = text.slice(closeBracket + 1);
    }
  }

  return { text, isStu, isFil };
}

// ============================================================
// 时间格式工具
// ============================================================
export function secsToInput(s: number): string {
  const m = Math.floor(s / 60);
  const sec = (s % 60).toFixed(3);
  return `${m.toString().padStart(2, "0")}:${sec.padStart(6, "0")}`;
}

export function inputToSecs(v: string): number | null {
  const m = v.match(/^(\d+):(\d{2}\.\d{1,3})$/);
  if (!m) return null;
  return parseInt(m[1]) * 60 + parseFloat(m[2]);
}

// ============================================================
// 字级切割：将一个 Segment 按选中字拆成 KEEP/DELETE 子段
// ============================================================
export function splitSegmentByWords(
  seg: Segment,
  selectedIndices: Set<number>,
): Segment[] {
  const words = seg.words || [];
  if (words.length === 0) return [seg];

  type Group = { indices: number[]; isSelected: boolean };
  const groups: Group[] = [];

  for (let i = 0; i < words.length; i++) {
    const sel = selectedIndices.has(i);
    if (groups.length === 0 || groups[groups.length - 1].isSelected !== sel) {
      groups.push({ indices: [i], isSelected: sel });
    } else {
      groups[groups.length - 1].indices.push(i);
    }
  }

  if (groups.length <= 1 && groups[0]?.isSelected) {
    return [{
      ...seg,
      action: "delete" as const,
      user_override: true,
      manual_action: "word_delete",
      reason: "字级删除（全选）",
    }];
  }

  // 链式衔接：先计算每个 group 的精确切割边界，保证相邻子段无重叠无间隙。
  // boundary[i] 是第 i 组和第 i+1 组之间的切割点。
  // 取"前组最后一个字的 end"与"后组第一个字的 start"的中点，
  // 消除 ASR 字级时间戳重叠/间隙带来的拼接重复。
  const boundaries: number[] = [];
  for (let i = 0; i < groups.length - 1; i++) {
    const prevEnd = words[groups[i].indices[groups[i].indices.length - 1]].end;
    const nextStart = words[groups[i + 1].indices[0]].start;
    boundaries.push((prevEnd + nextStart) / 2);
  }

  return groups.map((g, gi) => {
    const groupWords = g.indices.map((idx) => words[idx]);
    const text = groupWords.map((w) => {
      const { text: t } = parseWordTag(w.word);
      return t;
    }).join("");

    const start = gi === 0 ? seg.start : boundaries[gi - 1];
    const end = gi === groups.length - 1 ? seg.end : boundaries[gi];

    return {
      ...seg,
      id: `${seg.id}_${gi}`,
      start,
      end,
      text,
      words: groupWords,
      action: g.isSelected ? ("delete" as const) : ("keep" as const),
      user_override: true,
      manual_action: "word_delete",
      reason: g.isSelected ? "字级删除" : (seg.action === "delete" ? "手动恢复" : seg.reason),
      tagged_text: undefined,
      claude_action: undefined,
      claude_reason: undefined,
    } as Segment;
  });
}

// ============================================================
// 裁断：将一个 Segment 在第 splitAtIndex 个字之前切成两段
// splitAtIndex 最小为 1（第 0 个字之前无意义）
// ============================================================

/** 从 words 中多数投票推算说话人，无数据时回退到 fallback */
function deriveSpeakerFromWords(ws: { speaker?: string }[], fallback?: string): string | undefined {
  const spks = ws.map(w => w.speaker).filter(Boolean) as string[];
  if (spks.length === 0) return fallback;
  return spks.sort((a, b) =>
    spks.filter(s => s === b).length - spks.filter(s => s === a).length
  )[0];
}

export function splitSegmentAt(seg: Segment, splitAtIndex: number): [Segment, Segment] {
  const words = seg.words || [];
  const boundary = (words[splitAtIndex - 1].end + words[splitAtIndex].start) / 2;

  const words0 = words.slice(0, splitAtIndex);
  const words1 = words.slice(splitAtIndex);

  const text0 = words0.map((w) => { const { text: t } = parseWordTag(w.word); return t; }).join("");
  const text1 = words1.map((w) => { const { text: t } = parseWordTag(w.word); return t; }).join("");

  const base = {
    ...seg,
    user_override: true,
    manual_action: "split" as const,
    tagged_text: undefined,
    claude_action: undefined,
    claude_reason: undefined,
  };

  const s0: Segment = {
    ...base,
    id: `${seg.id}_s0`,
    start: seg.start,
    end: boundary,
    text: text0,
    words: words0,
    speaker: deriveSpeakerFromWords(words0, seg.speaker),
    reason: "裁断（前半）",
  };

  const s1: Segment = {
    ...base,
    id: `${seg.id}_s1`,
    start: boundary,
    end: seg.end,
    text: text1,
    words: words1,
    speaker: deriveSpeakerFromWords(words1, seg.speaker),
    reason: "裁断（后半）",
  };

  return [s0, s1];
}
// 将 hex 颜色转换为带透明度的 rgba 字符串
export function hexToRgba(hex: string, alpha: number): string {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return `rgba(${r},${g},${b},${alpha})`;
}

// 将 action 值映射为竖线颜色：保留=绿，删除=红，微修=黄，无数据=近透明
export function actionToColor(action: string | null | undefined): string {
  if (action === 'delete') return 'rgba(220,38,38,0.85)';
  if (action === 'keep')   return 'rgba(22,163,74,0.85)';
  if (action === 'subtitle_fix' || action === 'text_fix' || action === 'merge_next')
    return 'rgba(217,119,6,0.85)';
  return 'rgba(255,255,255,0.06)';
}
