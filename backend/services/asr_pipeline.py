"""
GoldenClip ASR Pipeline Service
Integrates faster-whisper + VAD to produce word-level timestamps
and tagged script with <SIL>, <FIL>, <STU> markers.

Design: 暗金剪辑台 · 编导美学
Pipeline: Video → Audio Extract → VAD → Whisper ASR → Tag → Segment
"""

import os
import copy
import re
import subprocess
import asyncio
import json
import yaml
from typing import List, Optional, Callable, AsyncGenerator
from pathlib import Path

from ..models.task import (
    WordTimestamp, Segment, ASRResult, SegmentAction, TaskParams, PreprocStats
)
from .subprocess_utils import run_text_subprocess

# FunASR 模型固定存放目录（项目根目录 models/），避免依赖系统缓存、重复下载
_MODELS_DIR = Path(__file__).parent.parent.parent / "models"

# 进程内单例：模型初始化一次后常驻内存，同一进程内后续任务 0 初始化时间
_funasr_model = None

# ─── 词库配置文件 ─────────────────────────────────────────────────────
_WORD_CONFIG_PATH = Path(__file__).parent.parent / "data" / "word_config.yaml"
_GLOSSARY_PATH = Path(__file__).parent.parent / "data" / "glossary.txt"


def _load_word_config() -> dict:
    """加载词库配置文件，文件不存在时返回空 dict（所有列表退化为默认值）。"""
    if not _WORD_CONFIG_PATH.exists():
        return {}
    try:
        with open(_WORD_CONFIG_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _build_word_sets(cfg: dict):
    """从配置 dict 构建运行时所需的集合/字符串常量。"""
    filler = set(cfg.get("filler_words") or [])
    if not filler:
        filler = {
            "嗯", "啊", "哦", "呃", "那个", "然后", "就是说", "就是", "这个",
            "对对对", "好好好", "嗯嗯", "啊啊", "哎", "诶", "嗯哼", "额",
            "哦哦", "对吧", "是吧", "怎么说呢",
        }

    compound = set(cfg.get("compound_word_endings") or [])
    if not compound:
        compound = {
            "现在", "正在", "还在", "已在", "仍在", "一直在",
            "所以", "可以", "得以", "用以", "借以",
            "过来", "回来", "进来", "出来", "上来", "下来", "前来",
            "过去", "回去", "进去", "出去", "上去", "下去",
            "想到", "说到", "做到", "看到", "听到", "感到", "得到",
            "以上", "实际上", "事实上",
            "以下", "底下", "手下",
            "以前", "目前", "之前", "当前",
            "以后", "之后", "然后", "最后",
        }

    reduplication = set(cfg.get("reduplication_aa_words") or [])
    if not reduplication:
        reduplication = {
            "谢谢", "拜拜",
            "妈妈", "爸爸", "哥哥", "姐姐", "弟弟", "妹妹",
            "奶奶", "爷爷", "宝宝", "叔叔", "婆婆", "娃娃",
            "姑姑", "舅舅", "嫂嫂",
            "好好", "慢慢", "快快", "轻轻", "深深", "静静", "默默", "悄悄",
            "明明", "清清", "暖暖", "凉凉", "多多", "少少", "早早", "晚晚",
            "高高", "低低", "圆圆", "长长", "短短", "大大", "小小",
            "重重", "厚厚", "薄薄", "稳稳", "实实", "老老",
            "看看", "想想", "试试", "说说", "听听", "走走", "玩玩",
            "找找", "等等", "问问", "聊聊", "坐坐", "笑笑", "哭哭",
            "哈哈", "嘻嘻", "呵呵", "嗯嗯", "哦哦", "哎哎",
            "对对", "好好", "行行",
            "天天", "年年", "时时", "处处", "事事", "人人", "家家",
            "步步", "层层", "条条", "种种", "样样", "项项",
        }

    particles = set(cfg.get("leading_particles") or "的嘛呢啊哦嗯吧呀哈噢喔了么吗")
    gap_ms = cfg.get("particle_merge_max_gap_ms", 100)
    leading_punct = set(cfg.get("leading_punct_to_merge") or "，、；：")

    return filler, compound, reduplication, particles, gap_ms / 1000.0, leading_punct


# 模块加载时初始化一次；运行时可调用 reload_word_config() 重新加载
_word_cfg = _load_word_config()
(
    DEFAULT_FILLER_WORDS,
    _COMPOUND_WORD_ENDINGS,
    _REDUPLICATION_AA_WORDS,
    _LEADING_PARTICLES,
    _PARTICLE_MERGE_MAX_GAP,
    _LEADING_PUNCT_TO_MERGE,
) = _build_word_sets(_word_cfg)


def reload_word_config() -> dict:
    """热重载词库配置，返回新配置 dict（供 API 路由调用）。"""
    global _word_cfg, DEFAULT_FILLER_WORDS, _COMPOUND_WORD_ENDINGS
    global _REDUPLICATION_AA_WORDS, _LEADING_PARTICLES, _PARTICLE_MERGE_MAX_GAP, _LEADING_PUNCT_TO_MERGE
    _word_cfg = _load_word_config()
    (
        DEFAULT_FILLER_WORDS,
        _COMPOUND_WORD_ENDINGS,
        _REDUPLICATION_AA_WORDS,
        _LEADING_PARTICLES,
        _PARTICLE_MERGE_MAX_GAP,
        _LEADING_PUNCT_TO_MERGE,
    ) = _build_word_sets(_word_cfg)
    return _word_cfg


def load_glossary(path: Optional[Path] = None) -> List[str]:
    """
    加载专业术语词典。
    返回按词长降序排列的词列表（长词优先匹配，避免短词误替换长词中的子串）。
    """
    target = path or _GLOSSARY_PATH
    if not target.exists():
        return []
    terms = []
    for line in target.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            terms.append(line)
    # 按长度降序，长词优先匹配
    terms.sort(key=len, reverse=True)
    return terms


def apply_glossary(words: List[WordTimestamp], glossary: List[str]) -> List[WordTimestamp]:
    """
    对 ASR 输出的词序列做词典纠错。
    策略：将连续词拼接为句子后，逐词典项尝试替换；只接受替换前后字符总数不变的替换项
    （通常是大小写纠正，如 "github" → "GitHub"），跳过字符数变化的替换（无法安全对齐）。
    """
    if not glossary or not words:
        return words

    full_text = "".join(w.word for w in words)
    corrected_text = full_text

    for term in glossary:
        pattern = re.escape(term)
        candidate = re.sub(pattern, term, corrected_text, flags=re.IGNORECASE)
        if len(candidate) != len(corrected_text):
            # 字符总数变化（如 "cloud code"→"Claude Code" 可能长度不同），
            # 无法按字符偏移安全对齐，跳过此词典项
            continue
        corrected_text = candidate

    if corrected_text == full_text:
        return words

    # 重新对齐：替换后字符总数与原始相同，按字符偏移逐词还原
    result = list(words)
    offset = 0
    for i, w in enumerate(result):
        char_len = len(w.word)
        new_word = corrected_text[offset:offset + char_len]
        if new_word != w.word:
            result[i] = WordTimestamp(
                word=new_word,
                start=w.start,
                end=w.end,
                speaker=w.speaker
            )
        offset += char_len

    return result


def extract_audio(
    video_path: str,
    audio_path: str,
    clip_start: float = 0.0,
    clip_end: Optional[float] = None,
) -> bool:
    """
    从视频提取音频（WAV 16kHz 单声道）。
    支持 clip_start/clip_end 裁剪区间，仅提取指定范围内的音频，
    以减少 ASR 处理量（超长直播场景）。
    """
    cmd = ["ffmpeg", "-y"]
    if clip_start > 0:
        cmd += ["-ss", str(clip_start)]
    cmd += ["-i", video_path]
    if clip_end is not None:
        # -to 是相对于输入文件的绝对时间（配合 -ss 在 -i 之前时有效）
        # 这里 -ss 放在 -i 前，所以 -to 需要换成 -t（持续时长）
        duration = clip_end - clip_start
        cmd += ["-t", str(duration)]
    cmd += ["-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", audio_path]
    result = run_text_subprocess(cmd, capture_output=True)
    return result.returncode == 0


def get_video_duration(video_path: str) -> float:
    """Get video duration using ffprobe."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        video_path
    ]
    result = run_text_subprocess(cmd, capture_output=True)
    if result.returncode == 0:
        data = json.loads(result.stdout)
        return float(data.get("format", {}).get("duration", 0))
    return 0.0


def generate_thumbnail(video_path: str, thumbnail_path: str, time: float = 0) -> bool:
    """Generate video thumbnail using FFmpeg."""
    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-ss", str(time), "-vframes", "1",
        "-vf", "scale=320:-1",
        thumbnail_path
    ]
    result = run_text_subprocess(cmd, capture_output=True)
    return result.returncode == 0


def tag_word(word: str, filler_words: set) -> str:
    """Apply FIL tag to filler words."""
    clean = word.strip()
    if clean in filler_words:
        return f"<FIL>{clean}"
    return clean


# _COMPOUND_WORD_ENDINGS, _REDUPLICATION_AA_WORDS 等词库常量
# 已通过 word_config.yaml 外置配置，在文件头部初始化。


def detect_stutter(words: List[WordTimestamp]) -> List[WordTimestamp]:
    """
    检测并标记结巴模式（<STU>）。
    仅当同一字在 2 秒内连续出现，且排除以下三类假阳性后，才标记为结巴：

    1. 复合词边界：现在在上 → "现在"词尾 + "在上"词首共享"在"，不是结巴
    2. AA 型叠词：谢谢、拜拜、好好 → 合法重叠词，不是结巴
    3. AABB 型叠词：老老实实、踏踏实实 → 合法四字成语，不是结巴
       - 正向检测（当前是 AA 的第二A）：后面跟着 BB 则跳过
       - 反向检测（当前是 BB 的第二B）：前面是 AA 则跳过
    """
    marked = []
    for i, w in enumerate(words):
        if i > 0 and words[i-1].word == w.word:
            if w.start - words[i-1].start < 2.0:
                # ── 排除 1：复合词词尾=词首 ──────────────────────────────
                if i >= 2:
                    prev_bigram = words[i-2].word + words[i-1].word
                    if prev_bigram in _COMPOUND_WORD_ENDINGS:
                        marked.append(w)
                        continue

                # ── 排除 2：AA 型叠词 ────────────────────────────────────
                aa_word = words[i-1].word + w.word
                if aa_word in _REDUPLICATION_AA_WORDS:
                    marked.append(w)
                    continue

                # ── 排除 3：AABB 型叠词 ──────────────────────────────────
                # 正向：当前是 AA 的第二A，后面紧跟 BB（A≠B）
                if (i + 2 < len(words)
                        and words[i + 1].word == words[i + 2].word
                        and words[i - 1].word != words[i + 1].word):
                    marked.append(w)
                    continue
                # 反向：当前是 BB 的第二B，前面是 AA（A≠B）
                if (i >= 3
                        and words[i - 3].word == words[i - 2].word
                        and words[i - 2].word != words[i - 1].word):
                    marked.append(w)
                    continue

                # ── 正常结巴：标记前字为 <STU> ───────────────────────────
                prev = marked[-1]
                prev_word = prev.word
                if not prev_word.startswith("<STU>"):
                    marked[-1] = WordTimestamp(
                        word=f"<STU>{prev_word}",
                        start=prev.start,
                        end=prev.end,
                        speaker=prev.speaker
                    )
        marked.append(w)
    return marked


def annotate_filler_boundaries(words: List[WordTimestamp], filler_words: set) -> List[WordTimestamp]:
    """
    为语气词标注精确的删除边界，同时附加 <FIL> 标签（供 transform_to_tagged_script 识别）。

    来自 videocut-skills 的正确逻辑：
      错误：直接用语气词自身的 start~end 作为删除范围
            → 会切掉前字的尾音
      正确：删除范围 = 前字.end → 后字.start
            → 精准切除，不影响相邻字的发音

    注意：此函数在 transform_to_tagged_script 之前调用，词上尚无 <FIL> 标签，
    因此直接检查 filler_words 集合判断是否为语气词，并同步写入 <FIL s=X.XXX e=Y.YYY> 标签。
    后续 transform_to_tagged_script 遇到已有 <FIL> 前缀的词会跳过，不会重复标注。
    """
    result = list(words)
    for i, w in enumerate(result):
        # 判断是否有 <STU> 前缀（用 startswith 而非 lstrip，避免字符集误删）
        has_stu = w.word.startswith("<STU>")
        raw_word = w.word[5:] if has_stu else w.word

        # 此时 FIL 标签尚未加入，直接检查语气词集合
        if raw_word not in filler_words:
            continue

        # 计算精确删除边界：前字结束时间 → 后字开始时间
        delete_start = result[i - 1].end if i > 0 else w.start
        delete_end = result[i + 1].start if i < len(result) - 1 else w.end

        # 写入精确边界的 FIL 标签，后续 tagged_script 生成时直接使用
        stu_prefix = "<STU>" if has_stu else ""
        new_word = f"{stu_prefix}<FIL s={delete_start:.3f} e={delete_end:.3f}>{raw_word}"
        result[i] = WordTimestamp(
            word=new_word,
            start=w.start,
            end=w.end,
            speaker=w.speaker
        )
    return result


def transform_to_tagged_script(
    words: List[WordTimestamp],
    silence_threshold: float = 0.5,
    filler_words: set = None
) -> str:
    """
    Transform word-level ASR output to tagged script for Claude.
    
    Applies:
    - <SIL X.Xs> for pauses > silence_threshold
    - <FIL> for filler words
    - <STU> for stutters (pre-processed)
    
    This is the key preprocessing step that enables Claude's 8 golden rules.
    """
    if filler_words is None:
        filler_words = DEFAULT_FILLER_WORDS

    script_parts = []
    last_end = 0.0

    for item in words:
        word = item.word
        start = item.start
        end = item.end

        # 1. Detect and mark pauses (VAD logic)
        gap = start - last_end
        if gap > silence_threshold:
            pause_dur = round(gap, 2)
            script_parts.append(f"\n[<SIL> {pause_dur}s]\n")

        # 2. Apply filler tag (if not already tagged as STU)
        if not word.startswith("<STU>") and not word.startswith("<FIL>"):
            clean_word = word.strip()
            if clean_word in filler_words:
                word = f"<FIL>{clean_word}"

        script_parts.append(word)
        last_end = end

    return "".join(script_parts)


# FunASR ct-punc-c 模型会插入的句末标点，遇到这些字符后立即切分
SENTENCE_END_PUNCT = {"。", "！", "？"}

# 标点切分保护阈值：标点字符与下一个词间隔 < 此值时，认为标点被 ct-punc-c 误插到
# 连续语音中间（如"他喜。欢"），不在此处切分。
# 自然句间停顿通常 > 200ms，同一词内字符间隔通常 < 50ms，100ms 留有安全余量。
PUNCT_SPLIT_MIN_GAP = 0.1  # 100ms
MAX_SEGMENT_DURATION = 12.0


def segment_by_silence(
    words: List[WordTimestamp],
    silence_threshold: float = 0.5,
    min_duration: float = 0.3,
    max_duration: float = MAX_SEGMENT_DURATION,
) -> List[Segment]:
    """
    Group words into segments based on silence gaps or sentence-ending punctuation.

    切分触发条件（任一满足即切分）：
    1. 下一词与当前词的时间间隔 > silence_threshold（静音切分）
    2. 当前词是句末标点（。！？），由 FunASR ct-punc-c 插入（语义切分）
    3. 当前段即将超过 max_duration（硬切分，防止出现 70s+ 超长段）
    4. 已遍历到最后一词

    两种切分条件互补：Whisper 输出无标点，标点切分不触发，行为不变；
    FunASR 输出含标点，能在短停顿处正确切句。
    """
    if not words:
        return []

    def _flush_segment(segment_words: List[WordTimestamp], segment_start: float) -> Optional[Segment]:
        if not segment_words:
            return None

        seg_text = "".join(
            re.sub(r'<FIL[^>]*>', '', w.word).replace("<STU>", "")
            for w in segment_words
        ).strip()
        seg_tagged = "".join(w.word for w in segment_words).strip()
        duration = segment_words[-1].end - segment_start
        if duration < min_duration or not seg_text:
            return None

        speakers = [w.speaker for w in segment_words if w.speaker]
        speaker = max(set(speakers), key=speakers.count) if speakers else None
        return Segment(
            start=round(segment_start, 3),
            end=round(segment_words[-1].end, 3),
            text=seg_text,
            tagged_text=seg_tagged,
            speaker=speaker,
            action=SegmentAction.KEEP,
            words=list(segment_words),
        )

    segments = []
    current_words = []
    current_start = words[0].start

    for i, word in enumerate(words):
        # 硬上限切分：当前词若并入会触发超长段，则先将前面积累的词落段，
        # 再把当前词作为新段起点，避免产出 12s 以上的大段。
        if current_words and max_duration > 0 and (word.end - current_start) >= max_duration:
            seg = _flush_segment(current_words, current_start)
            if seg:
                segments.append(seg)
            current_words = []
            current_start = word.start

        current_words.append(word)

        is_last = (i == len(words) - 1)
        has_gap = not is_last and (words[i + 1].start - word.end) > silence_threshold
        # 去除 FIL/STU 标签后判断原始字符是否为句末标点
        raw_char = re.sub(r'<FIL[^>]*>', '', word.word).replace("<STU>", "").strip()
        is_sentence_end = raw_char in SENTENCE_END_PUNCT
        # 标点切分保护：标点后紧接的下一个词间隔极短时，说明 ct-punc-c 误插标点到
        # 连续语音中间（如"他喜。欢"间隔仅 20ms），此处不应切分
        if is_sentence_end and not is_last:
            next_gap = words[i + 1].start - word.end
            if next_gap < PUNCT_SPLIT_MIN_GAP:
                is_sentence_end = False
        # 说话人切换点强制切段：下一词 speaker 与当前词不同时切分
        # 仅当两者都有 speaker 标注时才触发，避免无分离数据时误切
        # 极短词（<150ms）且与下一词近乎无间隔（<50ms）时跳过切分，
        # 大概率是 diarization 在说话人边界处的误判（如"好的"被拆到两个说话人）
        has_speaker_turn = (
            not is_last
            and word.speaker is not None
            and words[i + 1].speaker is not None
            and word.speaker != words[i + 1].speaker
            and not (
                (word.end - word.start) < 0.15
                and (words[i + 1].start - word.end) < 0.05
            )
        )

        if has_gap or is_sentence_end or has_speaker_turn or is_last:
            seg = _flush_segment(current_words, current_start)
            if seg:
                segments.append(seg)

            # 开始新段
            if not is_last:
                current_words = []
                current_start = words[i + 1].start

    return segments


# _LEADING_PARTICLES, _PARTICLE_MERGE_MAX_GAP, _LEADING_PUNCT_TO_MERGE
# 已通过 word_config.yaml 外置配置，在文件头部初始化。


def merge_leading_particles(segments: List[Segment]) -> List[Segment]:
    """
    后处理：把因 diarization 边界误判被切到下一段段首的孤立助词回并到上一段。

    触发条件（同时满足）：
    1. 当前段首字属于孤立助词集合，且该段首词为单字（duration<150ms 或就是一个字符）
    2. 当前段与上一段 gap ≤ 100ms（近似衔接，没有明显停顿）
    3. 上一段与当前段说话人不同（即 speaker_turn 导致的切分）

    处理方式：将段首孤立词从当前段移到上一段尾部（而非整段合并），
    保持两段各自的说话人归属不变。
    """
    if len(segments) < 2:
        return segments

    result = []
    skip_next = False

    for i, seg in enumerate(segments):
        if skip_next:
            skip_next = False
            continue

        if i == 0:
            result.append(seg)
            continue

        prev = result[-1]

        # 取当前段第一个词的原始字符
        if not seg.words:
            result.append(seg)
            continue

        first_word = seg.words[0]
        first_char = re.sub(r'<FIL[^>]*>', '', first_word.word).replace("<STU>", "").strip()

        gap = seg.start - prev.end

        is_particle = (
            len(first_char) == 1
            and first_char in _LEADING_PARTICLES
            and gap <= _PARTICLE_MERGE_MAX_GAP
            and prev.speaker is not None
            and seg.speaker is not None
            and prev.speaker != seg.speaker
        )

        if not is_particle:
            result.append(seg)
            continue

        # 将段首孤立词移回上一段
        prev.words = list(prev.words) + [first_word]
        prev.end = round(first_word.end, 3)
        prev.text = (prev.text or "") + first_char
        if prev.tagged_text is not None:
            prev.tagged_text = (prev.tagged_text or "") + first_word.word

        remaining_words = seg.words[1:]
        if remaining_words:
            # 当前段去掉首词后继续保留
            seg_text = "".join(
                re.sub(r'<FIL[^>]*>', '', w.word).replace("<STU>", "")
                for w in remaining_words
            ).strip()
            seg_tagged = "".join(w.word for w in remaining_words).strip()
            seg.words = list(remaining_words)
            seg.start = round(remaining_words[0].start, 3)
            seg.text = seg_text
            seg.tagged_text = seg_tagged
            if seg_text:
                result.append(seg)
            # 若去首词后段文本为空则丢弃该段
        # 若当前段只有一个词（就是那个孤立助词），整段已被吸收，不追加

    return result


def merge_leading_punctuation(segments: List[Segment]) -> List[Segment]:
    """
    后处理：把被 VAD 切到下一段段首的非句首标点（，、；：）回并到上一段。

    ct-punc-c 按语义插标点，VAD 按静音切段，两者边界不对齐时逗号等标点
    会变成下一段的首字符。这类标点永远不可能出现在句首，无条件回并。
    """
    if len(segments) < 2:
        return segments

    result = []
    for i, seg in enumerate(segments):
        if i == 0 or not seg.words:
            result.append(seg)
            continue

        first_word = seg.words[0]
        first_char = re.sub(r'<FIL[^>]*>', '', first_word.word).replace("<STU>", "").strip()

        if first_char not in _LEADING_PUNCT_TO_MERGE or not result:
            result.append(seg)
            continue

        prev = result[-1]

        prev.words = list(prev.words) + [first_word]
        prev.end = round(first_word.end, 3)
        prev.text = (prev.text or "") + first_char
        if prev.tagged_text is not None:
            prev.tagged_text = (prev.tagged_text or "") + first_word.word

        remaining_words = seg.words[1:]
        if remaining_words:
            seg_text = "".join(
                re.sub(r'<FIL[^>]*>', '', w.word).replace("<STU>", "")
                for w in remaining_words
            ).strip()
            seg_tagged = "".join(w.word for w in remaining_words).strip()
            seg.words = list(remaining_words)
            seg.start = round(remaining_words[0].start, 3)
            seg.text = seg_text
            seg.tagged_text = seg_tagged
            if seg_text:
                result.append(seg)

    return result


def split_at_speaker_changes(
    segments: List[Segment],
    min_duration: float = 0.3,
    min_other_run: float = 3.0,
    min_first_seg: float = 3.0,
) -> List[Segment]:
    """
    后处理：将段内出现**持续**说话人切换的片段自动拆成两段。

    仅当"其他说话人"的连续 run 时长 >= min_other_run 时才切分，
    避免 CAM++ 词级噪声（孤立短词被误标为另一说话人）触发误切。
    """
    if not segments:
        return segments

    result: List[Segment] = []

    for seg in segments:
        words = seg.words or []
        if not words or not any(w.speaker for w in words):
            result.append(seg)
            continue

        lead_speaker = seg.speaker
        if not lead_speaker:
            result.append(seg)
            continue

        # 构建 speaker runs: 连续相同 speaker 的词分组
        runs: list = []  # [(speaker, start_idx, end_idx, duration)]
        i = 0
        while i < len(words):
            spk = words[i].speaker
            j = i + 1
            while j < len(words) and words[j].speaker == spk:
                j += 1
            dur = words[j - 1].end - words[i].start
            runs.append((spk, i, j, dur))
            i = j

        # 找第一个"其他说话人" run 且时长 >= min_other_run
        split_run_idx = None
        for ri, (spk, si, ei, dur) in enumerate(runs):
            if spk and spk != lead_speaker and dur >= min_other_run:
                split_run_idx = ri
                break

        if split_run_idx is None:
            result.append(seg)
            continue

        split_idx = runs[split_run_idx][1]  # 该 run 的起始词索引
        if split_idx == 0:
            result.append(seg)
            continue

        boundary = round((words[split_idx - 1].end + words[split_idx].start) / 2, 3)
        words0 = words[:split_idx]
        words1 = words[split_idx:]

        dur0 = boundary - seg.start
        dur1 = seg.end - boundary
        if dur0 < min_duration or dur1 < min_duration:
            result.append(seg)
            continue
        # 前半段太短（被截断的片段），不值得拆分
        if dur0 < min_first_seg:
            result.append(seg)
            continue

        def _text(ws: List) -> str:
            return "".join(
                re.sub(r'<FIL[^>]*>', '', w.word).replace("<STU>", "")
                for w in ws
            ).strip()

        def _tagged(ws: List) -> str:
            return "".join(w.word for w in ws).strip()

        def _majority_speaker(ws: List) -> Optional[str]:
            spks = [w.speaker for w in ws if w.speaker]
            return max(set(spks), key=spks.count) if spks else None

        s0 = copy.copy(seg)
        s0.end = boundary
        s0.text = _text(words0)
        s0.tagged_text = _tagged(words0)
        s0.words = words0
        s0.speaker = _majority_speaker(words0)

        s1 = copy.copy(seg)
        s1.start = boundary
        s1.text = _text(words1)
        s1.tagged_text = _tagged(words1)
        s1.words = words1
        s1.speaker = _majority_speaker(words1)
        s0.id = f"{seg.id}_sp0" if hasattr(seg, 'id') and seg.id else None
        s1.id = f"{seg.id}_sp1" if hasattr(seg, 'id') and seg.id else None

        result.append(s0)
        result.extend(split_at_speaker_changes([s1], min_duration=min_duration, min_other_run=min_other_run, min_first_seg=min_first_seg))

    return result


async def run_whisper_asr(
    audio_path: str,
    language: str = "zh",
    log_callback: Optional[Callable] = None,
    whisper_model: str = "base",
) -> List[WordTimestamp]:
    """
    Run faster-whisper ASR with word-level timestamps.
    Falls back to mock data if faster-whisper is not installed.
    """
    try:
        from faster_whisper import WhisperModel

        if log_callback:
            await log_callback("info", "asr", f"正在加载 Whisper 模型 ({whisper_model})...")

        loop = asyncio.get_running_loop()
        model = await loop.run_in_executor(
            None,
            lambda: WhisperModel(whisper_model, device="cpu", compute_type="int8"),
        )

        if log_callback:
            await log_callback("info", "asr", "开始语音识别，请稍候...")

        def _transcribe_words() -> List[WordTimestamp]:
            segments, _info = model.transcribe(
                audio_path,
                language=language,
                word_timestamps=True,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 500}
            )

            words = []
            for segment in segments:
                if segment.words:
                    for word in segment.words:
                        words.append(WordTimestamp(
                            word=word.word.strip(),
                            start=round(word.start, 3),
                            end=round(word.end, 3)
                        ))
            return words

        words = await loop.run_in_executor(None, _transcribe_words)

        if log_callback:
            await log_callback("success", "asr", f"ASR 完成，识别 {len(words)} 个词")

        return words

    except ImportError:
        if log_callback:
            await log_callback("warn", "asr", "faster-whisper 未安装，使用模拟数据（仅用于演示）")
        return _generate_mock_asr_data()
    except Exception as e:
        if log_callback:
            await log_callback("error", "asr", f"ASR 错误: {str(e)}")
        return _generate_mock_asr_data()


async def get_funasr_model(log_callback: Optional[Callable] = None):
    """
    获取 FunASR 模型单例。

    核心策略：
    - 使用模型名字符串（"paraformer-zh" 等）让 FunASR/ModelScope 管理下载。
    - 在调用前将 MODELSCOPE_CACHE 环境变量设为项目内 models/ 目录，
      ModelScope 的 snapshot_download 会把文件写到 models/hub/... 下，永久持久化。
    - sentinel 文件标记下载状态，无需每次扫描目录。
    - 进程内单例：加载一次后常驻内存，同进程内后续任务 0 初始化时间。
    """
    global _funasr_model
    if _funasr_model is not None:
        return _funasr_model

    try:
        from funasr import AutoModel
    except ImportError:
        raise ImportError("funasr 未安装，请运行：pip install funasr")

    # 模型根目录及 ModelScope 缓存目录
    _MODELS_DIR.mkdir(parents=True, exist_ok=True)
    os.environ["MODELSCOPE_CACHE"] = str(_MODELS_DIR)

    # ModelScope 下载后的实际路径（在 <MODELSCOPE_CACHE>/models/iic/<name>/ 下）
    _iic_dir = _MODELS_DIR / "models" / "iic"
    _paraformer_local = _iic_dir / "speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch"
    _vad_local        = _iic_dir / "speech_fsmn_vad_zh-cn-16k-common-pytorch"
    _punc_local       = _iic_dir / "punc_ct-transformer_zh-cn-common-vocab272727-pytorch"

    # 三个模型目录均存在且非空时直接使用本地路径，跳过任何网络访问
    _local_ready = (
        _paraformer_local.exists() and any(_paraformer_local.iterdir()) and
        _vad_local.exists()        and any(_vad_local.iterdir())        and
        _punc_local.exists()       and any(_punc_local.iterdir())
    )

    if not _local_ready and log_callback:
        await log_callback(
            "info", "asr",
            f"首次运行：正在从 ModelScope 下载 FunASR 模型（约 900MB）到 {_MODELS_DIR} ..."
        )
    elif log_callback:
        await log_callback("info", "asr", "正在从本地磁盘加载 FunASR 模型（无需联网）...")

    loop = asyncio.get_event_loop()

    if _local_ready:
        # 使用本地绝对路径 + disable_update：完全离线，启动速度最快
        _funasr_model = await loop.run_in_executor(
            None,
            lambda: AutoModel(
                model=str(_paraformer_local),
                vad_model=str(_vad_local),
                punc_model=str(_punc_local),
                disable_update=True,
            )
        )
    else:
        # 首次：用模型名让 FunASR 下载到 MODELSCOPE_CACHE 目录
        _funasr_model = await loop.run_in_executor(
            None,
            lambda: AutoModel(
                model="paraformer-zh",
                vad_model="fsmn-vad",
                punc_model="ct-punc-c",
            )
        )

    if log_callback:
        if not _local_ready:
            await log_callback("success", "asr", "模型下载并加载完成，已缓存到本地，后续启动将秒级加载")
        else:
            await log_callback("success", "asr", "FunASR 模型加载完成（本地缓存，无需重新下载）")

    return _funasr_model


async def run_funasr_asr(
    audio_path: str,
    log_callback: Optional[Callable] = None,
) -> List[WordTimestamp]:
    """
    使用阿里达摩院 FunASR Paraformer-zh 进行字符级语音识别。
    输出每个汉字的精确时间戳，误差约 20-30ms。
    同时集成 ct-punc-c 标点恢复模型，解决中文无标点问题。

    模型采用进程内单例 + 固定本地路径双重缓存策略：
    - 首次运行：从 ModelScope 下载到 ./models/，约 900MB
    - 后续运行：直接从 ./models/ 加载，无网络请求
    - 同进程内第 2+ 个任务：模型常驻内存，0 初始化时间
    """
    try:
        # 获取单例模型（首次加载或直接复用）
        model = await get_funasr_model(log_callback=log_callback)

        if log_callback:
            await log_callback("info", "asr", "开始字符级语音识别...")

        # 在后台线程中执行推理，避免阻塞事件循环
        loop = asyncio.get_event_loop()
        res = await loop.run_in_executor(
            None,
            lambda: model.generate(input=audio_path, batch_size_s=300)
        )

        if not res or not res[0].get("timestamp"):
            if log_callback:
                await log_callback("warn", "asr", "FunASR 未返回时间戳，结果为空")
            return []

        text: str = res[0].get("text", "")
        timestamps: list = res[0]["timestamp"]  # [[start_ms, end_ms], ...]

        # 过滤掉文本中的空格，使字符与时间戳索引对齐
        # FunASR 的 timestamp 列表长度对应非空格字符数
        non_space_chars = [c for c in text if c != " "]

        words = []
        for i, ts in enumerate(timestamps):
            char = non_space_chars[i] if i < len(non_space_chars) else ""
            if not char:
                continue
            words.append(WordTimestamp(
                word=char,
                start=round(ts[0] / 1000, 3),  # ms → 秒，保留 3 位小数
                end=round(ts[1] / 1000, 3),
            ))

        if log_callback:
            await log_callback(
                "success", "asr",
                f"FunASR 完成，识别 {len(words)} 个字符，文本含标点：{text[:60]}..."
            )

        # FunASR 对完整长音频的 CTC 对齐会产生累积时间戳漂移（实测 14 分钟音频漂移 1-3.6s）。
        # 对所有语音块独立重新识别（使用无 VAD 的 Paraformer），修正时间戳精度。
        words = await _recalibrate_timestamps(audio_path, words, log_callback)

        return words

    except Exception as e:
        if log_callback:
            await log_callback("error", "asr", f"FunASR 识别错误: {str(e)}")
        raise


# 不含 VAD 的 Paraformer 模型（用于子段精确时间戳重校准）
_funasr_paraformer = None


async def _get_paraformer_model():
    """获取不含 VAD 的 Paraformer 模型单例（子段重校准专用）。"""
    global _funasr_paraformer
    if _funasr_paraformer is not None:
        return _funasr_paraformer

    from funasr import AutoModel

    _iic_dir = _MODELS_DIR / "models" / "iic"
    _paraformer_local = _iic_dir / "speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch"

    loop = asyncio.get_event_loop()
    if _paraformer_local.exists() and any(_paraformer_local.iterdir()):
        _funasr_paraformer = await loop.run_in_executor(
            None,
            lambda: AutoModel(model=str(_paraformer_local), disable_update=True)
        )
    else:
        os.environ.setdefault("MODELSCOPE_CACHE", str(_MODELS_DIR))
        _funasr_paraformer = await loop.run_in_executor(
            None,
            lambda: AutoModel(model="paraformer-zh", disable_update=True)
        )

    return _funasr_paraformer


async def _recalibrate_timestamps(
    audio_path: str,
    words: List[WordTimestamp],
    log_callback: Optional[Callable] = None,
) -> List[WordTimestamp]:
    """
    通过 ffmpeg 静音检测 + 分段独立 ASR 修正 FunASR CTC 时间戳漂移。

    旧方法用漂移的 ASR 时间戳决定音频提取位置，导致提取到错误音频、对齐失败。
    新方法用 ffmpeg silencedetect 从实际音频波形找到真实语音段边界，
    对每段独立运行 Paraformer ASR 获取精确时间戳，
    再通过全局贪心文本对齐将精确时间戳映射回原始词序列。
    """
    import tempfile
    import re as _re

    if not words:
        return words

    # ── 1. ffmpeg 静音检测：找到真实的语音段边界 ──
    sil_result = run_text_subprocess(
        ["ffmpeg", "-i", audio_path, "-af",
         "silencedetect=noise=-30dB:d=0.3", "-f", "null", "-"],
        capture_output=True,
    )
    silences = []
    sil_start = None
    for line in sil_result.stderr.split('\n'):
        m_s = _re.search(r'silence_start:\s*([\d.]+)', line)
        m_e = _re.search(r'silence_end:\s*([\d.]+)', line)
        if m_s:
            sil_start = float(m_s.group(1))
        if m_e and sil_start is not None:
            silences.append((sil_start, float(m_e.group(1))))
            sil_start = None

    # 静音区间 → 语音段
    audio_dur = words[-1].end + 1.0
    speech_segs: List[tuple] = []
    prev_end = 0.0
    for s_s, s_e in silences:
        if s_s > prev_end + 0.1:
            speech_segs.append((prev_end, s_s))
        prev_end = s_e
    if prev_end < audio_dur - 0.1:
        speech_segs.append((prev_end, audio_dur))

    # 超过 15s 的语音段等分切割（Paraformer 对 ≤15s 最精确）
    _MAX_CHUNK = 15.0
    final_segs: List[tuple] = []
    for start, end in speech_segs:
        dur = end - start
        if dur <= _MAX_CHUNK:
            final_segs.append((start, end))
        else:
            n = int(dur / _MAX_CHUNK) + 1
            cd = dur / n
            for i in range(n):
                final_segs.append((round(start + i * cd, 3), round(start + (i + 1) * cd, 3)))

    if log_callback:
        await log_callback(
            "info", "asr",
            f"检测到 {len(final_segs)} 个语音段，正在逐段校准时间戳..."
        )

    # ── 2. 对每个语音段独立 ASR ──
    paraformer = await _get_paraformer_model()
    loop = asyncio.get_event_loop()
    all_re_words: List[WordTimestamp] = []

    total_segs = len(final_segs)
    processed_count = 0  # 实际处理的段数（排除 < 0.3s 的极短段）

    for idx, (seg_start, seg_end) in enumerate(final_segs):
        seg_dur = seg_end - seg_start
        if seg_dur < 0.3:
            continue

        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                tmp_path = tmp.name

            subprocess.run(
                ["ffmpeg", "-y", "-i", audio_path,
                 "-ss", str(seg_start), "-t", str(seg_dur + 0.2),
                 "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
                 tmp_path],
                capture_output=True, check=False,
            )

            re_res = await loop.run_in_executor(
                None,
                lambda p=tmp_path: paraformer.generate(input=p, batch_size_s=300)
            )

            if re_res and re_res[0].get("timestamp"):
                re_ts = re_res[0]["timestamp"]
                re_chars = [c for c in re_res[0].get("text", "") if c != " "]
                for i in range(min(len(re_ts), len(re_chars))):
                    if re_chars[i]:
                        all_re_words.append(WordTimestamp(
                            word=re_chars[i],
                            start=round(re_ts[i][0] / 1000 + seg_start, 3),
                            end=round(re_ts[i][1] / 1000 + seg_start, 3),
                        ))
        except Exception:
            pass
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

        processed_count += 1
        # 每处理 10 段（或最后一段）上报一次进度
        if log_callback and total_segs > 0 and (processed_count % 10 == 0 or idx == total_segs - 1):
            pct = round((idx + 1) / total_segs * 100)
            await log_callback(
                "info", "asr",
                f"时间戳校准中... {pct}%（{idx + 1}/{total_segs} 段）"
            )

    if not all_re_words:
        return words

    # ── 3. 全局文本对齐：将精确时间戳映射回原始词序列 ──
    result = _global_text_align(words, all_re_words)

    if log_callback:
        await log_callback("success", "asr", f"时间戳校准完成（{len(all_re_words)} 个精确时间戳）")

    return result


def _global_text_align(
    orig_words: List[WordTimestamp],
    re_words: List[WordTimestamp],
) -> List[WordTimestamp]:
    """
    容错全局文本对齐：将分段重识别的精确时间戳映射到原始词序列。

    Paraformer 无上下文独立识别短段时，约 2-5% 的字会识别不同（如"居"→"知"）。
    简单贪心匹配遇到不匹配字符时 ri 指针会大幅跳跃，导致后续全部错位。

    本算法采用 n-gram 锚点 + 区间插值策略：
    1. 用连续 3 字符（trigram）作为高可靠锚点，在 re_words 中唯一定位
    2. 锚点之间的区间按比例线性插值时间戳
    这样即使中间有个别字符不匹配，也不会导致全局错位。
    """
    import re
    import unicodedata

    def is_content(c: str) -> bool:
        return bool(c) and unicodedata.category(c)[0] in ('L', 'N')

    # 提取纯文本
    orig_clean = []
    for w in orig_words:
        orig_clean.append(re.sub(r'<[^>]*>', '', w.word))

    re_chars = [w.word for w in re_words]

    # ── 1. 构建内容字符索引 ──
    # orig_content_idx[k] = 原始词列表中第 k 个内容字符的 orig_words 索引
    orig_content_idx = [oi for oi, c in enumerate(orig_clean) if is_content(c)]
    orig_content_chars = [orig_clean[oi] for oi in orig_content_idx]

    # ── 2. 用 trigram 滑动窗口 + 搜索范围限制找锚点 ──
    # 限制搜索窗口防止跨区域错误匹配（如"居"→"知"导致 ri 大幅跳跃）
    NGRAM = 3
    SEARCH_WINDOW = 30
    anchors: List[tuple] = []  # (content_k, re_j)

    ri_search_start = 0
    k = 0
    consecutive_misses = 0
    while k <= len(orig_content_chars) - NGRAM:
        trigram = orig_content_chars[k:k + NGRAM]
        found = False
        search_end = min(ri_search_start + SEARCH_WINDOW, len(re_chars) - NGRAM + 1)
        for j in range(ri_search_start, search_end):
            if re_chars[j:j + NGRAM] == trigram:
                for d in range(NGRAM):
                    anchors.append((k + d, j + d))
                ri_search_start = j + NGRAM
                k += NGRAM
                consecutive_misses = 0
                found = True
                break
        if not found:
            k += 1
            consecutive_misses += 1
            # 连续未匹配时适当推进 ri 避免卡住（re 可能多出了几个字符）
            if consecutive_misses >= 5 and ri_search_start < len(re_chars):
                ri_search_start += 1
                consecutive_misses = 0

    # ── 3. 从锚点构建 orig_words 级别的匹配映射 ──
    matched = [None] * len(orig_words)  # matched[oi] = re_words 索引
    for content_k, re_j in anchors:
        oi = orig_content_idx[content_k]
        matched[oi] = re_j

    # ── 4. 构建结果：已匹配用精确时间戳，未匹配从邻居锚点插值 ──
    result = []
    for oi, w in enumerate(orig_words):
        if matched[oi] is not None:
            rw = re_words[matched[oi]]
            result.append(WordTimestamp(word=w.word, start=rw.start, end=rw.end, speaker=w.speaker))
        else:
            prev_oi = next((p for p in range(oi - 1, -1, -1) if matched[p] is not None), None)
            next_oi = next((n for n in range(oi + 1, len(orig_words)) if matched[n] is not None), None)

            if prev_oi is not None and next_oi is not None:
                prev_re = re_words[matched[prev_oi]]
                next_re = re_words[matched[next_oi]]
                frac = (oi - prev_oi) / (next_oi - prev_oi)
                result.append(WordTimestamp(
                    word=w.word,
                    start=round(prev_re.start + frac * (next_re.start - prev_re.start), 3),
                    end=round(prev_re.end + frac * (next_re.end - prev_re.end), 3),
                    speaker=w.speaker,
                ))
            elif prev_oi is not None:
                prev_re = re_words[matched[prev_oi]]
                dist = oi - prev_oi
                result.append(WordTimestamp(
                    word=w.word,
                    start=round(prev_re.end + (dist - 1) * 0.15, 3),
                    end=round(prev_re.end + dist * 0.15, 3),
                    speaker=w.speaker,
                ))
            elif next_oi is not None:
                next_re = re_words[matched[next_oi]]
                dist = next_oi - oi
                result.append(WordTimestamp(
                    word=w.word,
                    start=round(next_re.start - dist * 0.15, 3),
                    end=round(next_re.start - (dist - 1) * 0.15, 3),
                    speaker=w.speaker,
                ))
            else:
                result.append(w)

    return result


def _align_timestamps(
    orig_words: List[WordTimestamp],
    new_words: List[WordTimestamp],
) -> List[WordTimestamp]:
    """
    将重新识别的时间戳对齐到原始词序列。

    FunASR 重识别子段时，VAD 可能跳过音频开头的部分字符，导致 new_words 相对于
    orig_words 存在一个正偏移量（new 从 orig 的某个中间位置开始）。
    本函数通过滑动窗口搜索最佳对齐偏移，确保每个 orig 字符获取正确的 new 时间戳；
    对于 new 未覆盖的前缀/后缀字符，按平均字符时长进行线性外推。
    """
    if not new_words:
        return orig_words

    orig_chars = [w.word for w in orig_words]
    new_chars = [w.word for w in new_words]

    # 滑动窗口：在 orig_chars 中找 new_chars 的最佳起始偏移量
    search_len = min(20, len(new_chars))
    best_offset = 0
    best_score = -1
    for offset in range(len(orig_chars)):
        score = sum(
            1 for i in range(search_len)
            if offset + i < len(orig_chars) and i < len(new_chars)
            and orig_chars[offset + i] == new_chars[i]
        )
        if score > best_score:
            best_score = score
            best_offset = offset
        if best_score == search_len:
            break  # 完美匹配，提前退出

    # 置信度阈值：匹配率 < 30% 说明重识别文本与原始文本严重不符
    # 此时 best_offset 基本是随机的，应用会导致词时间戳全部错乱
    # 直接保留原始时间戳（CTC 漂移比完全错乱的时间戳更可接受）
    _MIN_MATCH_PCT = 0.30
    if best_score < search_len * _MIN_MATCH_PCT:
        return orig_words

    # 平均字符时长（用于前缀/后缀外推）
    if len(new_words) > 1:
        avg_dur = (new_words[-1].end - new_words[0].start) / len(new_words)
    else:
        avg_dur = 0.2

    corrected = []
    for i, o in enumerate(orig_words):
        ni = i - best_offset  # 在 new_words 中对应的索引
        if 0 <= ni < len(new_words):
            # 在 new_words 覆盖范围内：直接使用精确时间戳
            n = new_words[ni]
            corrected.append(WordTimestamp(word=o.word, start=n.start, end=n.end, speaker=o.speaker))
        elif ni < 0:
            # orig 在 new 之前（前缀）：从 new[0] 反向外推
            dist = -ni
            est_start = new_words[0].start - dist * avg_dur
            est_end = new_words[0].start - (dist - 1) * avg_dur
            corrected.append(WordTimestamp(
                word=o.word,
                start=round(max(orig_words[0].start, est_start), 3),
                end=round(max(orig_words[0].start + 0.001, est_end), 3),
                speaker=o.speaker,
            ))
        else:
            # orig 在 new 之后（后缀）：从 new[-1] 正向外推
            dist = ni - len(new_words) + 1
            est_start = new_words[-1].end + (dist - 1) * avg_dur
            est_end = new_words[-1].end + dist * avg_dur
            corrected.append(WordTimestamp(
                word=o.word,
                start=round(est_start, 3),
                end=round(est_end, 3),
                speaker=o.speaker,
            ))
    return corrected


def _generate_mock_asr_data() -> List[WordTimestamp]:
    """Generate realistic mock ASR data for demo/testing."""
    mock_words = [
        # Segment 1: Normal speech
        {"word": "好", "start": 0.5, "end": 0.8},
        {"word": "我们", "start": 0.9, "end": 1.2},
        {"word": "今天", "start": 1.3, "end": 1.6},
        {"word": "来", "start": 1.7, "end": 1.9},
        {"word": "聊一聊", "start": 2.0, "end": 2.5},
        {"word": "AI", "start": 2.6, "end": 2.9},
        {"word": "在", "start": 3.0, "end": 3.2},
        {"word": "视频", "start": 3.3, "end": 3.7},
        {"word": "剪辑", "start": 3.8, "end": 4.2},
        {"word": "中的", "start": 4.3, "end": 4.6},
        {"word": "应用", "start": 4.7, "end": 5.1},
        # Silence gap ~0.8s
        # Segment 2: Filler words
        {"word": "嗯", "start": 5.9, "end": 6.2},  # FIL
        {"word": "我觉得", "start": 6.3, "end": 6.8},
        {"word": "这个", "start": 6.9, "end": 7.1},  # FIL
        {"word": "技术", "start": 7.2, "end": 7.6},
        {"word": "非常", "start": 7.7, "end": 8.0},
        {"word": "有价值", "start": 8.1, "end": 8.7},
        # Silence gap ~0.6s
        # Segment 3: Retake (重说)
        {"word": "我们", "start": 9.3, "end": 9.6},
        {"word": "需要", "start": 9.7, "end": 10.0},
        {"word": "重新", "start": 10.1, "end": 10.4},
        # Silence ~0.5s (pause before retake)
        {"word": "我们", "start": 10.9, "end": 11.2},
        {"word": "需要", "start": 11.3, "end": 11.6},
        {"word": "重新", "start": 11.7, "end": 12.0},
        {"word": "思考", "start": 12.1, "end": 12.5},
        {"word": "这个", "start": 12.6, "end": 12.8},  # FIL
        {"word": "问题", "start": 12.9, "end": 13.3},
        # Silence gap ~1.2s
        # Segment 4: Stutter
        {"word": "所以", "start": 14.5, "end": 14.8},
        {"word": "我们", "start": 14.9, "end": 15.2},
        {"word": "我们", "start": 15.3, "end": 15.6},  # STU
        {"word": "接下来", "start": 15.7, "end": 16.2},
        {"word": "要做的", "start": 16.3, "end": 16.8},
        {"word": "就是", "start": 16.9, "end": 17.1},  # FIL
        {"word": "把", "start": 17.2, "end": 17.4},
        {"word": "这套", "start": 17.5, "end": 17.8},
        {"word": "流程", "start": 17.9, "end": 18.3},
        {"word": "自动化", "start": 18.4, "end": 19.0},
        # Silence gap ~0.7s
        # Segment 5: Core content
        {"word": "从", "start": 19.7, "end": 19.9},
        {"word": "原始", "start": 20.0, "end": 20.4},
        {"word": "素材", "start": 20.5, "end": 20.9},
        {"word": "到", "start": 21.0, "end": 21.2},
        {"word": "可发布", "start": 21.3, "end": 21.8},
        {"word": "成品", "start": 21.9, "end": 22.3},
        {"word": "只需要", "start": 22.4, "end": 22.9},
        {"word": "三分钟", "start": 23.0, "end": 23.6},
        # Silence gap ~0.9s
        # Segment 6: Short fragment (to be deleted by Rule 2)
        {"word": "那", "start": 24.5, "end": 24.7},
        {"word": "就是", "start": 24.8, "end": 25.0},
        # Silence gap ~1.5s
        # Segment 7: Q&A
        {"word": "你", "start": 26.5, "end": 26.7},
        {"word": "觉得", "start": 26.8, "end": 27.1},
        {"word": "这个", "start": 27.2, "end": 27.4},
        {"word": "产品", "start": 27.5, "end": 27.8},
        {"word": "最大的", "start": 27.9, "end": 28.3},
        {"word": "价值", "start": 28.4, "end": 28.8},
        {"word": "是什么", "start": 28.9, "end": 29.4},
        # Silence gap ~0.6s
        {"word": "最大的", "start": 30.0, "end": 30.4},
        {"word": "价值", "start": 30.5, "end": 30.9},
        {"word": "就是", "start": 31.0, "end": 31.2},  # FIL
        {"word": "帮助", "start": 31.3, "end": 31.6},
        {"word": "创作者", "start": 31.7, "end": 32.2},
        {"word": "节省", "start": 32.3, "end": 32.7},
        {"word": "时间", "start": 32.8, "end": 33.2},
        {"word": "专注", "start": 33.3, "end": 33.7},
        {"word": "创意", "start": 33.8, "end": 34.2},
        {"word": "本身", "start": 34.3, "end": 34.8},
    ]
    return [WordTimestamp(**w) for w in mock_words]


def _parse_diarization_output(raw_segments) -> List[dict]:
    """
    解析 CAM++ 说话人分离模型的原始输出。
    输入格式：[[start_sec, end_sec, speaker_id], ...]
    输出格式：[{'start': float, 'end': float, 'speaker': str}, ...]
    """
    result = []
    if not isinstance(raw_segments, list):
        return result
    for seg in raw_segments:
        if isinstance(seg, list) and len(seg) == 3:
            try:
                result.append({
                    "start": float(seg[0]),
                    "end": float(seg[1]),
                    "speaker": f"spk{int(seg[2])}"
                })
            except (ValueError, TypeError):
                pass
    return result


def smooth_word_speakers(
    words: List[WordTimestamp],
    min_switch_duration: float = 1.0,
) -> List[WordTimestamp]:
    """
    启发式平滑 CAM++ 词级说话人标注噪声。

    CAM++ 是段级说话人分离模型，对齐到字级后，短词（尤其 < 0.5s）
    或音色临时变化（情绪加重、笑着说话）容易被误标为另一说话人。

    策略：将连续相同 speaker 的词视为一个 "run"，
    若 run 时长 < min_switch_duration 且被同一 speaker 的 run 包夹，
    则判定为噪声并修正为包夹方的 speaker。多轮迭代直到收敛。
    """
    if not words or not any(w.speaker for w in words):
        return words

    result = [
        WordTimestamp(word=w.word, start=w.start, end=w.end, speaker=w.speaker)
        for w in words
    ]

    def _build_runs(ws: List[WordTimestamp]) -> List[tuple]:
        """将词序列按连续相同 speaker 分组为 (speaker, start_idx, end_idx, duration)。"""
        runs = []
        i = 0
        while i < len(ws):
            spk = ws[i].speaker
            j = i + 1
            while j < len(ws) and ws[j].speaker == spk:
                j += 1
            dur = ws[j - 1].end - ws[i].start
            runs.append((spk, i, j, dur))
            i = j
        return runs

    smoothed_count = 0
    max_iter = 10
    for _ in range(max_iter):
        runs = _build_runs(result)
        merged = False
        for ri in range(1, len(runs) - 1):
            spk, si, ei, dur = runs[ri]
            prev_spk = runs[ri - 1][0]
            next_spk = runs[ri + 1][0]
            # 被同一 speaker 包夹 + 时长不足 → 噪声
            if spk != prev_spk and prev_spk == next_spk and dur < min_switch_duration:
                for idx in range(si, ei):
                    result[idx] = WordTimestamp(
                        word=result[idx].word,
                        start=result[idx].start,
                        end=result[idx].end,
                        speaker=prev_spk,
                    )
                smoothed_count += (ei - si)
                merged = True
        if not merged:
            break

    return result


def resmooth_speakers(
    words: List[WordTimestamp],
    diar_segments: List[dict],
    min_switch_duration: float,
) -> List[WordTimestamp]:
    """
    用给定的 min_switch_duration 重新对词级 speaker 做平滑。

    从原始 CAM++ 段级结果 diar_segments 重新对齐到 words，
    然后按新阈值执行 smooth_word_speakers。
    适用于前端滑动阈值后点"重新标注"的场景，无需重跑 ASR 或 CAM++。

    注意：words 中可能含有 <STU>/<FIL> 前缀，本函数只更改 speaker 字段，不修改 word 内容。
    """
    if not diar_segments:
        return words
    # 重新从原始段级结果对齐
    aligned = align_speakers_to_words(words, diar_segments)
    # 用新阈值平滑
    return smooth_word_speakers(aligned, min_switch_duration=min_switch_duration)


def align_speakers_to_words(
    words: List[WordTimestamp],
    diar_segments: List[dict]
) -> List[WordTimestamp]:
    """
    将说话人分离结果对齐到词级时间戳。
    对每个词，寻找与其时间区间重叠最长的说话人片段，
    采用"最大重叠优先"策略赋值 speaker 字段。
    """
    if not diar_segments:
        return words

    aligned = []
    for w in words:
        best_speaker = None
        best_overlap = 0.0
        for seg in diar_segments:
            overlap_start = max(w.start, seg["start"])
            overlap_end = min(w.end, seg["end"])
            overlap = max(0.0, overlap_end - overlap_start)
            if overlap > best_overlap:
                best_overlap = overlap
                best_speaker = seg["speaker"]
        aligned.append(WordTimestamp(
            word=w.word,
            start=w.start,
            end=w.end,
            speaker=best_speaker if best_overlap > 0 else w.speaker
        ))
    return aligned


async def run_diarization(
    audio_path: str,
    oracle_num: Optional[int] = None,
    log_callback: Optional[Callable] = None,
    timeout: float = 600.0,
) -> List[dict]:
    """
    使用 ModelScope CAM++ 模型执行说话人分离。
    模型：iic/speech_campplus_speaker-diarization_common
    权重自动从 ModelScope 下载，缓存至 ~/.cache/modelscope。

    返回：[{'start': float, 'end': float, 'speaker': str}, ...]
    若 modelscope 未安装、推理失败或超过 timeout 秒，返回空列表（降级为无说话人模式）。

    Args:
        timeout: 单次推理的最大等待秒数，默认 600s（10 分钟）。
                 CPU 上 30 分钟音频约需 40 分钟，可按实际情况调整。
    """
    try:
        from modelscope.pipelines import pipeline as ms_pipeline
        from modelscope.utils.constant import Tasks
    except ImportError:
        if log_callback:
            await log_callback(
                "warn", "diarization",
                "modelscope 未安装，跳过说话人分离。可运行 pip install modelscope funasr 启用此功能"
            )
        return []

    try:
        if log_callback:
            await log_callback("info", "diarization", "正在加载 CAM++ 说话人分离模型（首次运行将从 ModelScope 下载权重）...")

        diar_pipeline = ms_pipeline(
            task=Tasks.speaker_diarization,
            model="iic/speech_campplus_speaker-diarization_common",
        )

        if log_callback:
            timeout_min = int(timeout // 60)
            await log_callback(
                "info", "diarization",
                f"正在执行说话人分离，请稍候（超时限制 {timeout_min} 分钟）..."
            )

        # oracle_num 指定已知说话人数量，提升精度；None 则模型自动判断
        kwargs = {}
        if oracle_num is not None:
            kwargs["oracle_num"] = oracle_num

        loop = asyncio.get_event_loop()
        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(None, lambda: diar_pipeline(audio_path, **kwargs)),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            if log_callback:
                await log_callback(
                    "warn", "diarization",
                    f"说话人分离超时（>{timeout_min} 分钟），已跳过。"
                    f"可在 ASR 配置中关闭说话人分离后重新识别。"
                )
            return []

        raw_segments = result.get("text", [])
        diar_segments = _parse_diarization_output(raw_segments)

        speaker_ids = list({seg["speaker"] for seg in diar_segments})
        if log_callback:
            await log_callback(
                "success", "diarization",
                f"说话人分离完成，检测到 {len(speaker_ids)} 位说话人：{', '.join(sorted(speaker_ids))}"
            )

        return diar_segments

    except Exception as e:
        if log_callback:
            await log_callback("warn", "diarization", f"说话人分离失败（{e}），继续无说话人模式")
        return []


async def run_asr_pipeline(
    video_path: str,
    params: TaskParams,
    log_callback: Optional[Callable] = None,
    whisper_model: str = "base",
    backend: str = "whisper",
    oracle_num: Optional[int] = None,
    clip_start: Optional[float] = None,
    clip_end: Optional[float] = None,
) -> ASRResult:
    """
    Full ASR pipeline:
    1. Extract audio from video（支持 clip_start/clip_end 裁剪区间）
    2. Run ASR（支持 backend="whisper" 或 backend="funasr"）
    3. Detect stutters
    4. Apply filler word tags
    5. Generate tagged script
    6. Segment by silence

    若设置了 clip_start，ASR 返回的时间戳会自动加上 clip_start 偏移，
    使其与原始视频时间轴对齐。
    """
    audio_path = video_path.rsplit(".", 1)[0] + "_audio.wav"
    _clip_start = clip_start or 0.0
    _clip_end = clip_end  # None 表示到结尾

    # Step 1: Extract audio
    if log_callback:
        if _clip_start > 0 or _clip_end is not None:
            cs_str = f"{_clip_start:.1f}s"
            ce_str = f"{_clip_end:.1f}s" if _clip_end else "结尾"
            await log_callback("info", "asr", f"正在提取音频（裁剪区间 {cs_str} ~ {ce_str}）...")
        else:
            await log_callback("info", "asr", "正在提取音频...")

    if os.path.exists(video_path):
        loop = asyncio.get_running_loop()
        success = await loop.run_in_executor(
            None,
            lambda: extract_audio(
                video_path,
                audio_path,
                clip_start=_clip_start,
                clip_end=_clip_end,
            ),
        )
        if not success:
            if log_callback:
                await log_callback("warn", "asr", "音频提取失败，使用原始文件")
            audio_path = video_path

    # Step 2: Run ASR（按 backend 路由）
    if backend == "funasr":
        words = await run_funasr_asr(audio_path, log_callback=log_callback)
    else:
        words = await run_whisper_asr(audio_path, log_callback=log_callback, whisper_model=whisper_model)

    # Step 2.1: 若使用了裁剪区间，将音频相对时间戳还原为原始视频时间轴坐标
    if _clip_start > 0 and words:
        for w in words:
            w.start += _clip_start
            w.end += _clip_start

    # Step 2.5: 说话人分离（可选，由 enable_diarization 控制）
    # 保存原始 CAM++ 段级结果，供后续前端调整平滑阈值时重新标注使用
    saved_diar_segments: list = []
    diar_smooth_threshold: float = 1.0
    if params.enable_diarization:
        diar_segments = await run_diarization(audio_path, oracle_num=oracle_num, log_callback=log_callback)
        if diar_segments:
            saved_diar_segments = diar_segments  # 保存原始段级结果
            words = align_speakers_to_words(words, diar_segments)
            # Step 2.6: 平滑 CAM++ 词级说话人标注噪声
            # CAM++ 段级对齐到字级后，短词/孤立异常容易误标为另一说话人，
            # 表现为 UI 中随机字被加下划线。此处用 run-length 平滑消除。
            before_speakers = [(w.word, w.speaker) for w in words]
            words = smooth_word_speakers(words, min_switch_duration=diar_smooth_threshold)
            smoothed = sum(
                1 for (_, s1), w in zip(before_speakers, words)
                if s1 != w.speaker
            )
            if log_callback and smoothed > 0:
                await log_callback(
                    "info", "diarization",
                    f"说话人标注平滑完成，修正了 {smoothed} 个词的说话人归属"
                )

    # Step 2.8: 词典纠错（在结巴检测之前，保证术语正确后再做后续分析）
    glossary = load_glossary()
    if glossary:
        words = apply_glossary(words, glossary)
        if log_callback:
            await log_callback("info", "asr", f"词典纠错完成，词典共 {len(glossary)} 条术语")

    # Step 3: Detect stutters
    if log_callback:
        await log_callback("info", "asr", "正在检测结巴和重复词...")
    words = detect_stutter(words)

    # Step 4: Generate tagged script
    if log_callback:
        await log_callback("info", "asr", "正在生成带标签剧本...")
    filler_set = set(params.filler_words) if params.filler_words else DEFAULT_FILLER_WORDS

    # Step 4.1: 为语气词标注精确删除边界（前字.end → 后字.start）
    words = annotate_filler_boundaries(words, filler_set)

    tagged_script = transform_to_tagged_script(
        words,
        silence_threshold=params.silence_threshold,
        filler_words=filler_set
    )

    # Step 5: Segment by silence
    if log_callback:
        await log_callback("info", "asr", "正在按停顿切分片段...")
    segments = segment_by_silence(
        words,
        silence_threshold=params.silence_threshold,
        min_duration=0.3,
        max_duration=MAX_SEGMENT_DURATION,
    )
    segments = merge_leading_particles(segments)
    segments = merge_leading_punctuation(segments)
    # Step 5.1: 补充切割——段内说话人切换时自动拆段
    # segment_by_silence 的保护条件（极短词+极短间隔）可能跳过了某些切点，
    # 此步扫描剩余段内的 speaker 切换并补切，保证每段只含一位主说话人。
    if params.enable_diarization:
        segments = split_at_speaker_changes(segments, min_duration=0.3)

    # Get video duration
    duration = get_video_duration(video_path) if os.path.exists(video_path) else 0.0
    if duration == 0 and words:
        duration = words[-1].end

    # Extract unique speakers
    speakers = list(set(w.speaker for w in words if w.speaker))

    # ── 预处理统计 ──────────────────────────────────────────────────────
    filler_count = sum(1 for w in words if "<FIL" in w.word)
    stutter_count = sum(1 for w in words if "<STU>" in w.word)
    # 统计 tagged_script 中的 <SIL> 标记（停顿段数和总时长）
    sil_matches = re.findall(r'<SIL>\s*([\d.]+)s', tagged_script)
    silence_count = len(sil_matches)
    silence_total_s = round(sum(float(s) for s in sil_matches), 2)
    preprocess_stats = PreprocStats(
        total_words=len(words),
        filler_count=filler_count,
        stutter_count=stutter_count,
        silence_count=silence_count,
        silence_total_s=silence_total_s,
        segment_count=len(segments),
        speaker_count=len(speakers),
    )

    if log_callback:
        await log_callback(
            "success", "asr",
            f"ASR 完成：{len(segments)} 个片段，{len(words)} 个词，"
            f"语气词 {filler_count} 个，结巴 {stutter_count} 处，停顿 {silence_count} 处"
        )

    return ASRResult(
        words=words,
        segments=segments,
        tagged_script=tagged_script,
        duration=duration,
        language="zh",
        speakers=speakers,
        diar_segments=saved_diar_segments,
        diar_smooth_threshold=diar_smooth_threshold,
        preprocess_stats=preprocess_stats,
    )
