"""
GoldenClip FFmpeg Executor
Handles lossless video cutting and concatenation.

Design: 暗金剪辑台 · 编导美学
Strategy: -c copy for lossless cuts, concat demuxer for joining
Formula: Target_Start_n = Σ Duration_i (i=1 to n-1)
"""

import os
import re as _re
import subprocess
import asyncio
import functools
import tempfile
from typing import List, Optional, Callable, Tuple
from pathlib import Path

from ..models.task import Segment, SegmentAction, TaskParams, SubtitleStyle, HookConfig
from .subprocess_utils import run_text_subprocess

EXPORTS_DIR = Path(__file__).parent.parent.parent / "exports"

# 按 task_id 存储当前运行中的 FFmpeg 子进程，用于支持导出中止
_active_processes: dict[str, subprocess.Popen] = {}


def register_process(task_id: str, proc: "subprocess.Popen[str]") -> None:
    """注册正在运行的 FFmpeg 子进程。"""
    _active_processes[task_id] = proc


def unregister_process(task_id: str) -> None:
    """注销已完成的 FFmpeg 子进程。"""
    _active_processes.pop(task_id, None)


def kill_export(task_id: str) -> bool:
    """
    终止指定任务的 FFmpeg 子进程。
    发送 SIGTERM，FFmpeg 会做清理后退出。
    返回 True 表示成功发送终止信号，False 表示无进程可杀。
    """
    proc = _active_processes.pop(task_id, None)
    if proc:
        try:
            proc.terminate()
        except Exception:
            pass
        return True
    return False


def _hex_to_ass_color(hex_str: str) -> str:
    """将 #RRGGBB 转为 ASS 颜色格式 &H00BBGGRR（不透明）"""
    h = hex_str.lstrip("#")
    if len(h) != 6:
        h = "FFFFFF"
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H00{b.upper()}{g.upper()}{r.upper()}"


def _build_force_style(style: SubtitleStyle, video_width: int = 0, video_height: int = 0) -> str:
    """根据 SubtitleStyle 构建 FFmpeg subtitles 滤镜的 force_style 字符串。
    ASS 只有一个 MarginV，顶部对齐（7-9）时取 margin_top，底部对齐（1-3）时取 margin_bottom。
    video_width/height 用于设置 PlayRes，使 FontSize 单位与视频像素一致。
    """
    # alignment 1-3 = 底部，4-6 = 中部，7-9 = 顶部
    if style.alignment >= 7:
        margin_v = style.margin_top
    else:
        margin_v = style.margin_bottom

    parts = []
    # 指定 PlayRes 使 ASS 字体单位 = 视频实际像素，否则 FFmpeg 默认 PlayResY=288 会将字体放大
    if video_width > 0 and video_height > 0:
        parts += [f"PlayResX={video_width}", f"PlayResY={video_height}"]

    parts += [
        f"FontName={style.font_name}",
        f"FontSize={style.font_size}",
        f"PrimaryColour={_hex_to_ass_color(style.primary_color)}",
        f"OutlineColour={_hex_to_ass_color(style.outline_color)}",
        f"Outline={style.outline}",
        f"Shadow={style.shadow}",
        f"Bold={'1' if style.bold else '0'}",
        f"Alignment={style.alignment}",
        f"MarginV={margin_v}",
        f"MarginL={style.margin_l}",
        f"MarginR={style.margin_r}",
        "WrapStyle=0",  # end-of-line wrap within margins
    ]
    return ",".join(parts)


# subtitle_fix / text_fix / merge_next 均视频不剪切，归入保留集合
import sys as _sys


def _get_default_font(bold: bool = False) -> str:
    if _sys.platform == "win32":
        return "C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc"
    if _sys.platform == "darwin":
        return "/System/Library/Fonts/PingFang.ttc"
    return "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


def _hex_to_ffmpeg_color(hex_str: str) -> str:
    """将 #RRGGBB 转为 ffmpeg drawtext 颜色格式 0xRRGGBB。"""
    h = hex_str.lstrip("#")
    if len(h) == 6:
        return f"0x{h.upper()}"
    return "0xFFFFFF"


def _build_drawtext_filter(hook: "HookConfig") -> str:
    """将 HookConfig 转为 FFmpeg drawtext 滤镜字符串。"""
    if hook.x_pct is not None and hook.y_pct is not None:
        # 精确百分比定位：文字中心对齐到 (x_pct%, y_pct%) 处
        x = f"(w*{hook.x_pct/100:.4f}-text_w/2)"
        y = f"(h*{hook.y_pct/100:.4f}-text_h/2)"
    else:
        x = {"left": "40", "right": "w-text_w-40"}.get(hook.h_align, "(w-text_w)/2")
        y = {"top": "60", "bottom": "h-text_h-60"}.get(hook.v_align, "(h-text_h)/2")
    text = hook.text.replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:").replace(",", "\\,")
    # Use bold font file instead of bold=1 (removed in ffmpeg 8.x)
    font = _get_default_font(hook.bold).replace(":", "\\:")
    color = _hex_to_ffmpeg_color(hook.color)
    border_color = _hex_to_ffmpeg_color(hook.outline_color)
    return (
        f"drawtext=fontfile='{font}':text='{text}':"
        f"fontsize={hook.font_size}:fontcolor={color}:"
        f"borderw={hook.outline}:bordercolor={border_color}:"
        f"x={x}:y={y}:enable='between(t\\,0\\,{hook.duration})'"
    )


_KEPT_ACTIONS = (
    SegmentAction.KEEP,
    SegmentAction.SUBTITLE_FIX,
    SegmentAction.TEXT_FIX,
    SegmentAction.MERGE_NEXT,
)


def _format_srt_time(seconds: float) -> str:
    """将秒数格式化为 SRT 时间码：HH:MM:SS,mmm"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds - int(seconds)) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _get_subtitle_text(seg: Segment, params: Optional[TaskParams] = None) -> str:
    """
    计算段落最终字幕文本：
    - subtitle_fix / text_fix：优先使用 display_text（修正后文本）
    - show_speaker_label=True：在文本前追加 [说话人] 前缀
    """
    text = (seg.display_text or seg.text or "").strip()
    if params and params.show_speaker_label and seg.speaker:
        speaker_names = params.speaker_names or {}
        label = speaker_names.get(seg.speaker, seg.speaker)
        text = f"[{label}] {text}"
    return text


def _find_line_break_time(seg: Segment) -> Optional[float]:
    """
    在 words 中定位 subtitle_line_break 起始词的时间（秒）。
    返回 None 表示未设置或未找到匹配词。
    """
    if not seg.subtitle_line_break or not seg.words:
        return None
    target_chars = [c for c in seg.subtitle_line_break if c.strip()]
    if not target_chars:
        return None
    for w_idx, w in enumerate(seg.words):
        raw = _re.sub(r'<[^>]*>', '', w.word).strip()
        if raw == target_chars[0]:
            match = True
            for k, sc in enumerate(target_chars[1:], 1):
                if w_idx + k < len(seg.words):
                    nxt = _re.sub(r'<[^>]*>', '', seg.words[w_idx + k].word).strip()
                    if nxt != sc:
                        match = False
                        break
            if match:
                return w.start
    return None


def _split_text_at_break(seg: Segment, line_break_time: float) -> Tuple[str, str]:
    """
    按分行时间把 seg.text 拆成两部分字符串。
    以 words 中时间 < line_break_time 的字符归第一张卡，其余归第二张卡。
    无 words 信息时按时间比例估算字符切点。
    """
    if not seg.words:
        ratio = (line_break_time - seg.start) / max(seg.end - seg.start, 0.001)
        text = seg.text or ""
        cut = max(1, int(len(text) * ratio))
        return text[:cut], text[cut:]
    part1_chars, part2_chars = [], []
    for w in seg.words:
        raw = _re.sub(r'<[^>]*>', '', w.word).strip()
        if not raw:
            continue
        if w.start < line_break_time:
            part1_chars.append(raw)
        else:
            part2_chars.append(raw)
    return "".join(part1_chars), "".join(part2_chars)


def _calc_split_threshold(style: "SubtitleStyle", video_width: int) -> int:
    """
    根据字号、左右边距、行数上限，计算触发拆卡的字符数阈值。

    中文字符宽度 ≈ font_size px（1 em），乘以 0.85 安全系数留出行末余量。
    返回 0 表示不限制（max_lines == 0）。
    """
    if style.max_lines <= 0:
        return 0
    effective_width = max(video_width - style.margin_l - style.margin_r, style.font_size)
    chars_per_line = max(1, int(effective_width / style.font_size * 0.85))
    return style.max_lines * chars_per_line


_PUNCT_SPLIT = set('，。？！；：')


def _find_punct_cut(
    chars: list,
    nominal_idx: int,
    window: int,
) -> int:
    """
    从名义切点附近查找标点符号，返回实际切点索引。

    优先向前搜索 window 范围内最近的标点，切在标点之后（标点留在当前卡）；
    若向前无标点则向后搜索；仍无则退化到 nominal_idx。
    """
    total = len(chars)
    lo = max(0, nominal_idx - window)
    hi = min(total - 1, nominal_idx + window)

    # 向前搜索（从 nominal_idx-1 往 lo），取最近的标点
    for k in range(min(nominal_idx, total) - 1, lo - 1, -1):
        if chars[k][0] in _PUNCT_SPLIT:
            return k + 1  # 切在标点之后

    # 向后搜索
    for k in range(nominal_idx, hi + 1):
        if chars[k][0] in _PUNCT_SPLIT:
            return min(k + 1, total)

    return nominal_idx


def _split_seg_to_cards(
    seg: Segment,
    threshold: int,
) -> List[Tuple[str, float, float]]:
    """
    将一段文字按 threshold 字符数切成若干张字幕卡，每张 ≤ threshold 字。
    返回 [(card_text, raw_start, raw_end), ...]，时间为原始视频时间（需由调用方映射到输出时间轴）。

    - 有 words 时：在每个 threshold 步长处取所在词的 word_start 作为下一张卡的起始时间
    - 无 words 时：按字符数线性估算时间
    - 段时长 < 1.0s 时不拆分（避免字幕闪烁）
    """
    text = (seg.text or "").strip()
    if threshold <= 0 or len(text) <= threshold:
        return [(text, seg.start, seg.end)]
    duration = seg.end - seg.start
    if duration < 1.0:
        return [(text, seg.start, seg.end)]

    if seg.words:
        content_chars: List[Tuple[str, float]] = []
        for w in seg.words:
            raw = _re.sub(r'<[^>]*>', '', w.word).strip()
            for c in raw:
                content_chars.append((c, w.start))
    else:
        n = max(len(text), 1)
        content_chars = [(c, seg.start + duration * i / n) for i, c in enumerate(text)]

    if len(content_chars) <= threshold:
        return [(text, seg.start, seg.end)]

    # 按标点感知切点拆分
    window = max(4, threshold // 3)
    cut_indices: List[int] = [0]  # 每张卡的起始字符索引
    i = threshold
    while i < len(content_chars):
        cut = _find_punct_cut(content_chars, i, window)
        if cut >= len(content_chars):
            break
        cut_indices.append(cut)
        i = cut + threshold
    cut_indices.append(len(content_chars))

    cards: List[Tuple[str, float, float]] = []
    for j in range(len(cut_indices) - 1):
        chunk = content_chars[cut_indices[j]: cut_indices[j + 1]]
        card_text = "".join(c for c, _ in chunk)
        card_start = chunk[0][1] if chunk else seg.start
        card_end = content_chars[cut_indices[j + 1]][1] if cut_indices[j + 1] < len(content_chars) else seg.end
        if card_text:
            cards.append((card_text, card_start, card_end))

    return cards if cards else [(text, seg.start, seg.end)]


def _auto_split_text_by_words(
    seg: Segment,
    max_chars: int,
) -> Optional[Tuple[str, float, str]]:
    """
    当段落文字超过 max_chars 时，在 words 时间戳列表中找到最接近文字中点的词边界，
    返回 (part1_text, split_time, part2_text)；否则返回 None。

    split_time 为第二部分第一个词的 start（秒），由调用方映射为输出时间轴。

    保护条件：
    - 段时长 < 1.0s 时不拆分（避免字幕闪烁）
    - words 为空时按字符中点拆分，split_time 取线性估算
    """
    text = (seg.text or "").strip()
    if len(text) <= max_chars:
        return None
    duration = seg.end - seg.start
    if duration < 1.0:
        return None

    if not seg.words:
        # 无 words：按字符中点拆，时间线性估算
        mid = len(text) // 2
        part1, part2 = text[:mid], text[mid:]
        split_time = seg.start + duration * (mid / max(len(text), 1))
        return (part1, split_time, part2) if part1 and part2 else None

    # 收集所有非空字符及其所属词的时间范围
    content_chars: List[Tuple[str, float, float]] = []  # (char, word_start, word_end)
    for w in seg.words:
        raw = _re.sub(r'<[^>]*>', '', w.word).strip()
        for c in raw:
            content_chars.append((c, w.start, w.end))

    total = len(content_chars)
    if total <= max_chars:
        return None

    # 找最接近中点的词边界：遍历词，记录每个词结束后的字符累计数
    target_idx = total // 2
    # 找到索引 target_idx 附近使得切点恰好在词边界
    # 策略：找 content_chars[target_idx] 所在词的 word_start，
    # 该 word_start 作为第二张字幕卡的起始时间
    _, _ws, _we = content_chars[target_idx]
    split_time = _ws  # 第二部分起始时间

    # 在 split_time 前的所有字符 → part1，其余 → part2
    part1_chars, part2_chars = [], []
    for c, ws, we in content_chars:
        if ws < split_time:
            part1_chars.append(c)
        else:
            part2_chars.append(c)

    part1 = "".join(part1_chars)
    part2 = "".join(part2_chars)
    if not part1 or not part2:
        return None
    return (part1, split_time, part2)


def _reorder_segments_for_golden_quotes(
    segments: List[Segment],
    golden_quote_order: List[str],
) -> List[Segment]:
    """
    根据金句开场顺序，将保留片段重新排列：金句片段在前，其余按原始时间顺序在后。

    注意：为不影响原始 segments 列表（审计视图依赖原顺序），返回新列表。
    delete 段保持相对位置，用于 _merge_contiguous_chunks 识别切割点。

    策略：
    1. 按 golden_quote_order 顺序取出金句片段（包裹在只含该片段的虚拟 delete 边界中）
    2. 剩余 kept 片段保持原时间顺序，原有 delete 边界不变
    """
    if not golden_quote_order:
        return segments

    gq_set = set(golden_quote_order)
    seg_map = {s.id: s for s in segments}

    # 构建金句片段列表（保持用户指定顺序）
    golden_segs = [seg_map[sid] for sid in golden_quote_order if sid in seg_map]

    # 剩余片段（保持原始顺序，delete 段也保留用于分块）
    remaining = [s for s in segments if s.id not in gq_set]

    # 金句片段逐个作为独立 chunk（彼此之间不连续，用虚拟 delete 边界隔离）
    # 实现方式：将每个金句片段单独放一个列表片段，delete 段自然隔断
    # 由于 _merge_contiguous_chunks 遇到 delete 就切断，需要在金句之间插入 delete 占位段
    # 这里采用更简单的方式：返回重排后的纯片段列表，调用方使用 _reordered_chunks 计算
    return golden_segs, remaining


def _build_golden_quote_chunks(
    golden_segs: List[Segment],
    remaining_segs: List[Segment],
) -> List[tuple]:
    """
    构建含金句开场的 chunk 列表：
    - 每个金句片段单独构成一个 chunk
    - remaining_segs 按原始连续块逻辑合并
    返回 [(chunk_start, chunk_end), ...]
    """
    chunks = []

    # 金句片段：每段独立为一个 chunk
    for seg in golden_segs:
        if seg.action in _KEPT_ACTIONS:
            chunks.append((seg.start, seg.end))

    # 剩余片段：过滤掉已在金句里的，然后合并连续块
    chunks.extend(_merge_contiguous_chunks(remaining_segs))

    return chunks


def generate_srt(
    segments: List[Segment],
    srt_path: str,
    params: Optional[TaskParams] = None,
    subtitle_style: Optional[SubtitleStyle] = None,
    golden_quote_order: Optional[List[str]] = None,
    video_width: int = 0,
) -> str:
    """
    根据保留片段生成 SRT 字幕文件。

    时间轴按 chunk 结构计算，与实际导出视频完全对齐：
        输出时间 = chunk_cursor + (seg.start - chunk_start)
    其中 chunk_cursor 是前面所有 chunk 的时长累加，
    这样同一 chunk 内段与段之间的自然停顿也会被正确计入。

    当 golden_quote_order 非空时，金句片段出现在 SRT 最开头，时间轴相应偏移。
    """
    if golden_quote_order:
        golden_segs, remaining_segs = _reorder_segments_for_golden_quotes(segments, golden_quote_order)
        chunks = _build_golden_quote_chunks(golden_segs, remaining_segs)
        # 构建用于字幕遍历的有序 kept 列表（金句优先）
        gq_set = set(golden_quote_order)
        seg_map = {s.id: s for s in segments}
        kept_golden = [seg_map[sid] for sid in golden_quote_order if sid in seg_map and seg_map[sid].action in _KEPT_ACTIONS]
        kept_remaining = [s for s in segments if s.action in _KEPT_ACTIONS and s.id not in gq_set]
        kept = kept_golden + kept_remaining
    else:
        chunks = _merge_contiguous_chunks(segments)
        kept = [s for s in segments if s.action in _KEPT_ACTIONS]

    lines: List[str] = []
    idx = 1
    chunk_cursor = 0.0  # 前面所有 chunk 在输出视频中的累积时长

    for chunk_start, chunk_end in chunks:
        chunk_duration = chunk_end - chunk_start
        # 找出属于本 chunk 的保留段（按 start 位置判断）
        chunk_segs = [s for s in kept if chunk_start <= s.start and s.end <= chunk_end + 0.001]

        for seg in chunk_segs:
            # 在输出视频中的精确时间位置
            out_start = chunk_cursor + (seg.start - chunk_start)
            out_end = chunk_cursor + (seg.end - chunk_start)
            text = _get_subtitle_text(seg, params)
            if not text:
                continue

            # 若设置了 subtitle_line_break，则拆成两条独立 SRT 条目
            line_break_time = _find_line_break_time(seg)
            if line_break_time is not None and seg.start < line_break_time < seg.end:
                out_lb = chunk_cursor + (line_break_time - chunk_start)
                text_part1, text_part2 = _split_text_at_break(seg, line_break_time)
                if text_part1:
                    lines.append(str(idx))
                    lines.append(f"{_format_srt_time(out_start)} --> {_format_srt_time(out_lb)}")
                    lines.append(text_part1)
                    lines.append("")
                    idx += 1
                if text_part2:
                    lines.append(str(idx))
                    lines.append(f"{_format_srt_time(out_lb)} --> {_format_srt_time(out_end)}")
                    lines.append(text_part2)
                    lines.append("")
                    idx += 1
            else:
                # 自动分屏：当 subtitle_style.max_lines > 0 时，将文字切成若干张字幕卡
                # 每张 ≤ threshold 字（手动 subtitle_line_break 优先，此处已确认无手动设置）
                if subtitle_style and subtitle_style.max_lines > 0:
                    threshold = _calc_split_threshold(subtitle_style, video_width or 1080)
                    cards = _split_seg_to_cards(seg, threshold) if threshold > 0 else [(text, seg.start, seg.end)]
                else:
                    cards = [(text, seg.start, seg.end)]

                # 对每张卡片：若单行超出屏幕宽度，插入换行符防止溢出
                if subtitle_style:
                    vw = video_width or 1080
                    available_px = max(1, vw - subtitle_style.margin_l - subtitle_style.margin_r)
                    chars_per_line = max(1, int(available_px / subtitle_style.font_size * 0.88))
                    wrapped_cards = []
                    for card_text, cs, ce in cards:
                        if len(card_text) > chars_per_line:
                            lines_out = []
                            remaining = card_text
                            while len(remaining) > chars_per_line:
                                lines_out.append(remaining[:chars_per_line])
                                remaining = remaining[chars_per_line:]
                            if remaining:
                                lines_out.append(remaining)
                            card_text = "\n".join(lines_out)
                        wrapped_cards.append((card_text, cs, ce))
                    cards = wrapped_cards

                for card_text, card_raw_start, card_raw_end in cards:
                    raw_s = max(seg.start, min(seg.end, card_raw_start))
                    raw_e = max(seg.start, min(seg.end, card_raw_end))
                    c_out_start = chunk_cursor + (raw_s - chunk_start)
                    c_out_end = chunk_cursor + (raw_e - chunk_start)
                    if c_out_start >= c_out_end:
                        c_out_end = c_out_start + max(0.1, (out_end - out_start) / len(cards))
                    lines.append(str(idx))
                    lines.append(f"{_format_srt_time(c_out_start)} --> {_format_srt_time(c_out_end)}")
                    lines.append(card_text)
                    lines.append("")
                    idx += 1

        chunk_cursor += chunk_duration

    Path(srt_path).parent.mkdir(parents=True, exist_ok=True)
    with open(srt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return srt_path


def _run_ffmpeg(cmd: List[str], log_callback=None, task_id: Optional[str] = None) -> tuple[bool, str]:
    """Run FFmpeg command and return (success, output)."""
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    # 注册进程，支持外部通过 kill_export 终止
    if task_id:
        register_process(task_id, process)
    try:
        stdout_b, stderr_b = process.communicate()
    finally:
        # 无论正常完成还是被终止，都清理注册表
        if task_id:
            unregister_process(task_id)
    success = process.returncode == 0
    raw = stderr_b if stderr_b else stdout_b
    output = raw.decode("utf-8", errors="replace") if raw else ""
    return success, output


def _merge_contiguous_chunks(
    all_segments: List[Segment],
) -> List[Tuple[float, float]]:
    """
    遍历全部段列表（含 delete），以 delete 段为断点将连续保留段合并为大块。
    ASR 相邻段之间的自然微间隙（呼吸、停顿）会被保留在块内，
    只在真正被删除的位置做切割。
    """
    chunks: List[Tuple[float, float]] = []
    chunk_start: Optional[float] = None
    chunk_end: Optional[float] = None

    for seg in all_segments:
        if seg.action in _KEPT_ACTIONS:
            if chunk_start is None:
                chunk_start = seg.start
            chunk_end = seg.end
        else:
            if chunk_start is not None and chunk_end is not None:
                chunks.append((chunk_start, chunk_end))
                chunk_start = None
                chunk_end = None

    if chunk_start is not None and chunk_end is not None:
        chunks.append((chunk_start, chunk_end))

    return chunks


async def export_ffmpeg_lossless(
    video_path: str,
    segments: List[Segment],
    output_path: str,
    log_callback: Optional[Callable] = None,
    burn_subtitles: bool = False,
    srt_path: Optional[str] = None,
    task_id: Optional[str] = None,
    subtitle_style: Optional[SubtitleStyle] = None,
    golden_quote_order: Optional[List[str]] = None,
    hook_config: Optional[HookConfig] = None,
    cover_image_base64: Optional[str] = None,
    cover_duration: float = 2.0,
) -> bool:
    """
    Export video using FFmpeg re-encode cut + concat.

    核心策略：先将连续保留段合并为大块（chunk），只在真正有删除段的
    位置做切割。这样连续保留的句子之间保持原始音视频的自然连贯性，
    不会因为逐段切割 + 拼接而引入间隙。

    当 golden_quote_order 非空时，金句片段单独成 chunk 并放到最前面，
    其余保留片段按原始时间顺序合并后拼接在后。

    Steps:
    1. 合并连续保留段为 chunks（含金句前置逻辑）
    2. 对每个 chunk 做一次 FFmpeg 重编码切割
    3. concat demuxer 拼接所有 chunks
    4. (可选) burn_subtitles=True 时，用 -vf subtitles= 烧录字幕
    """
    kept_segments = [s for s in segments if s.action in _KEPT_ACTIONS]

    if not kept_segments:
        if log_callback:
            await log_callback("error", "ffmpeg", "没有需要保留的片段")
        return False

    if golden_quote_order:
        golden_segs, remaining_segs = _reorder_segments_for_golden_quotes(segments, golden_quote_order)
        chunks = _build_golden_quote_chunks(golden_segs, remaining_segs)
        if log_callback:
            gq_kept = [s for s in golden_segs if s.action in _KEPT_ACTIONS]
            await log_callback(
                "info", "ffmpeg",
                f"金句开场模式：{len(gq_kept)} 个金句片段前置，"
                f"共 {len(chunks)} 个块"
            )
    else:
        chunks = _merge_contiguous_chunks(segments)

    if log_callback:
        await log_callback(
            "info", "ffmpeg",
            f"共 {len(kept_segments)} 个保留段，合并为 {len(chunks)} 个连续块"
        )

    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix="goldenclip_"))
    chunk_files = []

    # ---- 封面图片转视频 chunk（若提供）
    cover_chunk_path: Optional[Path] = None
    if cover_image_base64:
        try:
            import base64 as _b64
            raw = cover_image_base64
            if raw.startswith("data:"):
                raw = raw.split(",", 1)[1]
            img_bytes = _b64.b64decode(raw)
            cover_img_path = temp_dir / "cover_input.png"
            cover_img_path.write_bytes(img_bytes)

            vinfo = get_video_info(video_path)
            vw = vinfo.get("width", 1080)
            vh = vinfo.get("height", 1920)

            cover_chunk_path = temp_dir / "cover_chunk.mp4"
            cmd_cover = [
                "ffmpeg", "-y",
                "-loop", "1",
                "-i", str(cover_img_path),
                "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=stereo",
                "-t", str(cover_duration),
                "-vf", f"scale={vw}:{vh}:force_original_aspect_ratio=decrease,pad={vw}:{vh}:(ow-iw)/2:(oh-ih)/2,setsar=1",
                "-c:v", "libx264",
                "-preset", "fast",
                "-crf", "18",
                "-pix_fmt", "yuv420p",
                "-r", "25",
                "-c:a", "aac",
                "-b:a", "192k",
                "-shortest",
                str(cover_chunk_path),
            ]
            loop = asyncio.get_event_loop()
            ok, out = await loop.run_in_executor(None, functools.partial(_run_ffmpeg, cmd_cover, task_id=task_id))
            if ok:
                if log_callback:
                    await log_callback("info", "ffmpeg", f"封面图片转视频完成（{cover_duration}s）")
            else:
                if log_callback:
                    await log_callback("warn", "ffmpeg", f"封面转视频失败，跳过封面: {out[:200]}")
                cover_chunk_path = None
        except Exception as e:
            if log_callback:
                await log_callback("warn", "ffmpeg", f"封面处理异常，跳过: {e}")
            cover_chunk_path = None

    try:
        total = len(chunks)
        for i, (chunk_start, chunk_end) in enumerate(chunks):
            chunk_path = temp_dir / f"chunk_{i:04d}.mp4"
            duration = chunk_end - chunk_start

            if log_callback:
                progress = (i + 1) / total
                await log_callback(
                    "info", "ffmpeg",
                    f"切割块 {i+1}/{total}: {chunk_start:.2f}s → {chunk_end:.2f}s ({duration:.1f}s)",
                    progress
                )

            cmd = [
                "ffmpeg", "-y",
                "-ss", str(chunk_start),
                "-i", video_path,
                "-t", str(duration),
                "-c:v", "libx264",
                "-preset", "fast",
                "-crf", "18",
                "-c:a", "aac",
                "-b:a", "192k",
                "-avoid_negative_ts", "make_zero",
                str(chunk_path)
            ]

            # 使用 run_in_executor 在线程池中执行阻塞的 FFmpeg 调用，
            # 避免阻塞 asyncio 事件循环，使取消请求能被及时处理
            loop = asyncio.get_event_loop()
            success, output = await loop.run_in_executor(
                None, functools.partial(_run_ffmpeg, cmd, task_id=task_id)
            )
            if not success:
                if log_callback:
                    await log_callback("warn", "ffmpeg", f"块 {i+1} 切割失败，跳过: {output[:200]}")
                continue

            chunk_files.append(chunk_path)

        if not chunk_files:
            if log_callback:
                await log_callback("error", "ffmpeg", "所有块切割失败")
            return False

        # 封面 chunk 前置
        if cover_chunk_path and cover_chunk_path.exists():
            chunk_files.insert(0, cover_chunk_path)

        # 只有一个块且无需滤镜时直接重命名，无需 concat
        hook_filter = _build_drawtext_filter(hook_config) if (hook_config and hook_config.enabled and hook_config.text) else None
        if len(chunk_files) == 1 and not burn_subtitles and not hook_filter:
            import shutil
            shutil.move(str(chunk_files[0]), output_path)
            if log_callback:
                await log_callback("success", "ffmpeg", f"导出完成（单块，无需拼接）: {output_path}")
            return True

        concat_file = temp_dir / "concat.txt"
        with open(concat_file, "w", encoding="utf-8") as f:
            for cp in chunk_files:
                f.write(f"file '{cp}'\n")

        if log_callback:
            await log_callback("info", "ffmpeg", f"正在拼接 {len(chunk_files)} 个块...")

        if burn_subtitles and srt_path and os.path.exists(srt_path):
            # ffmpeg subtitles filter on Windows cannot handle non-ASCII or backslash paths.
            # Copy SRT to temp dir (guaranteed ASCII short path) before building the filter.
            import shutil as _shutil
            safe_srt = str(temp_dir / "sub.srt")
            _shutil.copy2(srt_path, safe_srt)
            escaped_srt = safe_srt.replace("\\", "/").replace(":", "\\:")
            vf_filter = f"subtitles='{escaped_srt}'"
            if subtitle_style:
                _vinfo = get_video_info(video_path)
                _w, _h = _vinfo.get("width", 0), _vinfo.get("height", 0)
                force_style = _build_force_style(subtitle_style, video_width=_w, video_height=_h)
                vf_filter = f"subtitles='{escaped_srt}':force_style='{force_style}'"
            if hook_filter:
                vf_filter = f"{vf_filter},{hook_filter}"
            cmd = [
                "ffmpeg", "-y",
                "-f", "concat",
                "-safe", "0",
                "-i", str(concat_file),
                "-vf", vf_filter,
                "-c:v", "libx264",
                "-preset", "medium",
                "-crf", "18",
                "-c:a", "aac",
                "-b:a", "192k",
                output_path,
            ]
            if log_callback:
                style_note = "（自定义样式）" if subtitle_style else ""
                hook_note = " + Hook" if hook_filter else ""
                await log_callback("info", "ffmpeg", f"硬字幕烧录模式{style_note}{hook_note}：concat + subtitles 滤镜（重编码）")
        elif hook_filter:
            cmd = [
                "ffmpeg", "-y",
                "-f", "concat",
                "-safe", "0",
                "-i", str(concat_file),
                "-vf", hook_filter,
                "-c:v", "libx264",
                "-preset", "medium",
                "-crf", "18",
                "-c:a", "aac",
                "-b:a", "192k",
                output_path,
            ]
            if log_callback:
                await log_callback("info", "ffmpeg", "Hook 字幕模式：concat + drawtext 滤镜（重编码）")
        elif cover_chunk_path and cover_chunk_path.exists():
            # 有封面时强制重编码，确保帧率/参数一致
            cmd = [
                "ffmpeg", "-y",
                "-f", "concat",
                "-safe", "0",
                "-i", str(concat_file),
                "-c:v", "libx264",
                "-preset", "fast",
                "-crf", "18",
                "-c:a", "aac",
                "-b:a", "192k",
                output_path,
            ]
            if log_callback:
                await log_callback("info", "ffmpeg", "封面拼接模式（重编码）")
        else:
            cmd = [
                "ffmpeg", "-y",
                "-f", "concat",
                "-safe", "0",
                "-i", str(concat_file),
                "-c", "copy",
                output_path,
            ]

        loop = asyncio.get_event_loop()
        success, output = await loop.run_in_executor(
            None, functools.partial(_run_ffmpeg, cmd, task_id=task_id)
        )

        if success:
            if log_callback:
                await log_callback("success", "ffmpeg", f"导出完成: {output_path}")
            return True
        else:
            if log_callback:
                await log_callback("error", "ffmpeg", f"拼接失败: {output[:2000]}")
            return False

    finally:
        import shutil
        try:
            shutil.rmtree(temp_dir)
        except:
            pass


async def export_ffmpeg_multi_clips(
    video_path: str,
    clip_groups: List[List[Segment]],
    output_dir: str,
    base_name: str,
    log_callback: Optional[Callable] = None,
    task_id: Optional[str] = None,
    burn_subtitles: bool = False,
    params: Optional[TaskParams] = None,
    subtitle_style: Optional[SubtitleStyle] = None,
    hook_configs: Optional[List[Optional[HookConfig]]] = None,
    hook_orders: Optional[List[Optional[List[str]]]] = None,
) -> List[str]:
    """
    导出多个独立 clip 文件。

    为避免污染 task.audit_segments 中的原始状态，这里会为每组片段创建深拷贝，
    并将导出副本统一标记为 KEEP 后再复用 export_ffmpeg_lossless。
    burn_subtitles=True 时，为每个 clip 单独生成 SRT 并烧录字幕。
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    output_paths: List[str] = []

    for i, clip_segments in enumerate(clip_groups, start=1):
        if not clip_segments:
            continue

        hook_config = (hook_configs[i - 1] if hook_configs and i - 1 < len(hook_configs) else None)
        hook_order = (hook_orders[i - 1] if hook_orders and i - 1 < len(hook_orders) else None)
        clip_name = f"{base_name}_clip_{i:02d}"
        output_path = str(Path(output_dir) / f"{clip_name}.mp4")
        export_segments = [
            seg.model_copy(deep=True)
            for seg in sorted(clip_segments, key=lambda seg: seg.start)
        ]

        if log_callback:
            total_duration = sum(
                seg.end - seg.start
                for seg in clip_segments
                if seg.action in _KEPT_ACTIONS
            )
            await log_callback(
                "info",
                "ffmpeg",
                f"导出片段 {i}/{len(clip_groups)}: {len(clip_segments)} 段, {total_duration:.1f}s → {clip_name}.mp4",
            )

        # 字幕烧录：为本 clip 生成单独的 SRT
        srt_path: Optional[str] = None
        if burn_subtitles:
            srt_path = str(Path(output_dir) / f"{clip_name}.srt")
            try:
                generate_srt(
                    segments=export_segments,
                    srt_path=srt_path,
                    params=params,
                    subtitle_style=subtitle_style,
                    golden_quote_order=hook_order,
                )
                if log_callback:
                    await log_callback("info", "ffmpeg", f"SRT 字幕已生成: {clip_name}.srt")
            except Exception as e:
                if log_callback:
                    await log_callback("warn", "ffmpeg", f"SRT 生成失败，跳过字幕烧录: {e}")
                srt_path = None

        success = await export_ffmpeg_lossless(
            video_path=video_path,
            segments=export_segments,
            output_path=output_path,
            log_callback=log_callback,
            task_id=task_id,
            burn_subtitles=burn_subtitles and srt_path is not None,
            srt_path=srt_path,
            subtitle_style=subtitle_style,
            hook_config=hook_config,
            golden_quote_order=hook_order,
        )
        if success:
            output_paths.append(output_path)
        elif log_callback:
            await log_callback("warn", "ffmpeg", f"片段 {i} 导出失败，已跳过")

    if log_callback:
        await log_callback(
            "success",
            "ffmpeg",
            f"多片段导出完成: {len(output_paths)}/{len(clip_groups)} 个成功",
        )

    return output_paths


def get_video_info(video_path: str) -> dict:
    """Get video metadata using ffprobe."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_streams", "-show_format",
        video_path
    ]
    result = run_text_subprocess(cmd, capture_output=True)
    if result.returncode == 0:
        import json
        data = json.loads(result.stdout)
        fmt = data.get("format", {})
        streams = data.get("streams", [])
        video_stream = next((s for s in streams if s.get("codec_type") == "video"), {})
        return {
            "duration": float(fmt.get("duration", 0)),
            "size": int(fmt.get("size", 0)),
            "bit_rate": int(fmt.get("bit_rate", 0)),
            "width": video_stream.get("width", 0),
            "height": video_stream.get("height", 0),
            "fps": video_stream.get("r_frame_rate", "0/1"),
            "codec": video_stream.get("codec_name", "unknown"),
        }
    return {}


def check_ffmpeg_available() -> bool:
    """Check if FFmpeg is installed."""
    result = subprocess.run(
        ["ffmpeg", "-version"],
        capture_output=True
    )
    return result.returncode == 0
