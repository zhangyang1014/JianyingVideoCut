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
import hashlib
import tempfile
import shutil
import threading
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Callable, AsyncGenerator
from pathlib import Path

from ..models.task import (
    WordTimestamp, Segment, ASRResult, SegmentAction, TaskParams, CandidateEdit
)
from .subprocess_utils import run_text_subprocess
from ..core.task_runner import execute_with_retry, RetryableExecutionError

# FunASR 模型固定存放目录（项目根目录 models/），避免依赖系统缓存、重复下载
_MODELS_DIR = Path(__file__).parent.parent.parent / "models"

# 进程内单例：模型初始化一次后常驻内存，同一进程内后续任务 0 初始化时间
_funasr_model = None

_ASR_CHECKPOINT_SCHEMA = 1
_active_asr_processes: dict[str, subprocess.Popen] = {}
_active_asr_processes_lock = threading.Lock()


def _stable_digest(payload: dict) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _file_fingerprint(path: str) -> dict:
    stat = os.stat(path)
    return {
        "path": str(Path(path).resolve()),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    part.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    os.replace(part, path)


def _is_valid_audio(path: str) -> bool:
    if not os.path.exists(path) or os.path.getsize(path) <= 44:
        return False
    result = run_text_subprocess(
        [
            "ffprobe", "-v", "error",
            "-select_streams", "a:0",
            "-show_entries", "stream=codec_type",
            "-of", "json",
            path,
        ],
        capture_output=True,
    )
    if result.returncode != 0:
        return False
    try:
        streams = json.loads(result.stdout or "{}").get("streams", [])
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    return any(stream.get("codec_type") == "audio" for stream in streams)


def _run_asr_process(cmd: List[str], process_key: str) -> subprocess.CompletedProcess:
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    with _active_asr_processes_lock:
        previous = _active_asr_processes.get(process_key)
        _active_asr_processes[process_key] = process
    if previous and previous.poll() is None:
        previous.terminate()
    try:
        stdout, stderr = process.communicate()
        return subprocess.CompletedProcess(cmd, process.returncode, stdout, stderr)
    finally:
        with _active_asr_processes_lock:
            if _active_asr_processes.get(process_key) is process:
                _active_asr_processes.pop(process_key, None)


def cancel_asr_processes(video_path: Optional[str] = None) -> int:
    """尽力终止当前任务的 FFmpeg/ffprobe 子进程；推理线程仍由协程取消负责收口。"""
    audio_path = get_asr_audio_path(video_path) if video_path else None
    with _active_asr_processes_lock:
        targets = [
            (key, process)
            for key, process in _active_asr_processes.items()
            if video_path is None or key in {video_path, audio_path}
        ]
    stopped = 0
    for _key, process in targets:
        if process.poll() is None:
            try:
                process.terminate()
                stopped += 1
            except OSError:
                pass
    return stopped


@dataclass(frozen=True)
class FunASRChunkConfig:
    """长音频 FunASR 宏块并行识别配置。"""
    enabled: bool = True
    min_duration_to_chunk: float = 900.0  # 15 分钟以上启用分块路径
    target_chunk_sec: float = 300.0       # 目标 5 分钟一块
    min_chunk_sec: float = 240.0          # 尽量不短于 4 分钟
    max_chunk_sec: float = 360.0          # 最长 6 分钟强制切
    overlap_sec: float = 2.0              # 块间重叠，避免边界丢字
    max_parallel_chunks: int = 4          # M 系列高性能芯片默认并发 4 路，兼顾速度与资源占用
    recalibrate_chunks: bool = True       # 每个宏块内部仍做现有时间戳校准


@dataclass
class FunASRMacroChunk:
    index: int
    logical_start: float
    logical_end: float
    extract_start: float
    extract_end: float
    words: List[WordTimestamp] = field(default_factory=list)


@dataclass(frozen=True)
class GlossaryEntry:
    source: str
    target: str


DEFAULT_FUNASR_CHUNK_CONFIG = FunASRChunkConfig()


def resolve_funasr_chunk_config() -> FunASRChunkConfig:
    """从全局配置解析 FunASR 分块参数；配置损坏时退回代码默认值。"""
    try:
        from .app_config_store import load_app_config

        asr_config = load_app_config().asr
        target_sec = asr_config.funasr_chunk_minutes * 60.0
        return FunASRChunkConfig(
            enabled=DEFAULT_FUNASR_CHUNK_CONFIG.enabled,
            min_duration_to_chunk=DEFAULT_FUNASR_CHUNK_CONFIG.min_duration_to_chunk,
            target_chunk_sec=target_sec,
            min_chunk_sec=max(60.0, target_sec * 0.8),
            max_chunk_sec=max(target_sec + 1.0, target_sec * 1.2),
            overlap_sec=DEFAULT_FUNASR_CHUNK_CONFIG.overlap_sec,
            max_parallel_chunks=asr_config.funasr_parallel_chunks,
            recalibrate_chunks=DEFAULT_FUNASR_CHUNK_CONFIG.recalibrate_chunks,
        )
    except Exception:
        return DEFAULT_FUNASR_CHUNK_CONFIG


# Filler words dictionary (expandable)
DEFAULT_FILLER_WORDS = {
    "嗯", "啊", "哦", "呃", "那个", "然后", "就是说", "就是", "这个",
    "对对对", "好好好", "嗯嗯", "啊啊", "哎", "诶", "嗯哼", "额",
    "哦哦", "对吧", "是吧", "怎么说呢"
}

# 词典文件路径（相对于项目根目录）
_GLOSSARY_PATH = Path(__file__).parent.parent / "data" / "glossary.txt"


def load_glossary(path: Optional[Path] = None) -> List[GlossaryEntry]:
    """
    加载专业术语词典。
    支持两种格式：
    - 标准词：Claude Code
    - 映射：克劳德 => Claude
    返回按 source 长度降序排列的条目（长词优先匹配，避免短词误替换长词中的子串）。
    """
    target = path or _GLOSSARY_PATH
    if not target.exists():
        return []
    entries: List[GlossaryEntry] = []
    for line in target.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=>" in line:
            source, replacement = [part.strip() for part in line.split("=>", 1)]
        elif "->" in line:
            source, replacement = [part.strip() for part in line.split("->", 1)]
        else:
            source, replacement = line, line
        if source and replacement:
            entries.append(GlossaryEntry(source=source, target=replacement))
    # 按长度降序，长词优先匹配
    entries.sort(key=lambda entry: len(entry.source), reverse=True)
    return entries


def _word_char_spans(words: List[WordTimestamp]) -> List[tuple[int, int]]:
    spans = []
    offset = 0
    for word in words:
        end = offset + len(word.word)
        spans.append((offset, end))
        offset = end
    return spans


def apply_glossary_with_report(
    words: List[WordTimestamp],
    glossary: List[GlossaryEntry],
) -> tuple[List[WordTimestamp], List[CandidateEdit]]:
    """
    对 ASR 输出的词序列做词典纠错。
    - 等长度替换：支持跨 token，按原字符偏移安全回写。
    - 非等长度替换：仅在单个 token 内执行，避免破坏时间戳对齐；跨 token 先记录候选。
    """
    if not glossary or not words:
        return words, []

    full_text = "".join(w.word for w in words)
    corrected_text = full_text
    edits: List[CandidateEdit] = []

    # 先处理跨 token 安全的等长度替换。
    for entry in glossary:
        if len(entry.source) != len(entry.target):
            continue
        pattern = re.escape(entry.source)
        corrected_text = re.sub(pattern, entry.target, corrected_text, flags=re.IGNORECASE)

    result = list(words)
    if corrected_text != full_text:
        # 重新对齐：替换后字符总数与原始相同，按字符偏移逐词还原。
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
                edits.append(CandidateEdit(
                    source="glossary",
                    edit_type="normalize_text",
                    start=w.start,
                    end=w.end,
                    text=f"{w.word} → {new_word}",
                    reason="词典规范化：等长度安全替换",
                    confidence=0.9,
                    word_index=i,
                ))
            offset += char_len

    # 再处理单 token 内的非等长度映射。
    for entry in glossary:
        if len(entry.source) == len(entry.target):
            continue
        for idx, w in enumerate(result):
            if w.word.lower() != entry.source.lower():
                continue
            old_word = w.word
            result[idx] = WordTimestamp(
                word=entry.target,
                start=w.start,
                end=w.end,
                speaker=w.speaker
            )
            edits.append(CandidateEdit(
                source="glossary",
                edit_type="normalize_text",
                start=w.start,
                end=w.end,
                text=f"{old_word} → {entry.target}",
                reason="词典规范化：单词内别名替换",
                confidence=0.85,
                word_index=idx,
            ))

    return result, edits


def apply_glossary(words: List[WordTimestamp], glossary: List[GlossaryEntry]) -> List[WordTimestamp]:
    result, _ = apply_glossary_with_report(words, glossary)
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
    if not os.path.exists(video_path):
        return False
    source = _file_fingerprint(video_path)
    cache_payload = {
        "schema": _ASR_CHECKPOINT_SCHEMA,
        "source": source,
        "clip_start": round(float(clip_start or 0.0), 3),
        "clip_end": round(float(clip_end), 3) if clip_end is not None else None,
        "format": {"codec": "pcm_s16le", "sample_rate": 16000, "channels": 1},
    }
    metadata_path = Path(audio_path + ".meta.json")
    if os.path.exists(audio_path) and metadata_path.exists():
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if metadata == cache_payload and _is_valid_audio(audio_path):
                return True
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            pass

    part_path = audio_path + ".part"
    Path(audio_path).parent.mkdir(parents=True, exist_ok=True)
    try:
        os.unlink(part_path)
    except FileNotFoundError:
        pass

    cmd = ["ffmpeg", "-y"]
    if clip_start > 0:
        cmd += ["-ss", str(clip_start)]
    cmd += ["-i", video_path]
    if clip_end is not None:
        # -to 是相对于输入文件的绝对时间（配合 -ss 在 -i 之前时有效）
        # 这里 -ss 放在 -i 前，所以 -to 需要换成 -t（持续时长）
        duration = clip_end - clip_start
        cmd += ["-t", str(duration)]
    cmd += [
        "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
        "-f", "wav", part_path,
    ]
    result = _run_asr_process(cmd, video_path)
    if result.returncode != 0 or not _is_valid_audio(part_path):
        try:
            os.unlink(part_path)
        except FileNotFoundError:
            pass
        return False
    os.replace(part_path, audio_path)
    _atomic_write_json(metadata_path, cache_payload)
    return True


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


def _detect_silences(audio_path: str, noise: str = "-30dB", min_duration: float = 0.3) -> List[tuple]:
    """用 FFmpeg silencedetect 返回静音区间 [(start, end), ...]。"""
    sil_result = run_text_subprocess(
        [
            "ffmpeg", "-i", audio_path, "-af",
            f"silencedetect=noise={noise}:d={min_duration}",
            "-f", "null", "-",
        ],
        capture_output=True,
    )
    silences = []
    sil_start = None
    for line in sil_result.stderr.split("\n"):
        m_s = re.search(r"silence_start:\s*([\d.]+)", line)
        m_e = re.search(r"silence_end:\s*([\d.]+)", line)
        if m_s:
            sil_start = float(m_s.group(1))
        if m_e and sil_start is not None:
            silences.append((sil_start, float(m_e.group(1))))
            sil_start = None
    return silences


def plan_funasr_macro_chunks(
    duration: float,
    silences: Optional[List[tuple]] = None,
    config: FunASRChunkConfig = DEFAULT_FUNASR_CHUNK_CONFIG,
) -> List[FunASRMacroChunk]:
    """
    为长音频规划 4-6 分钟宏块。优先在接近 target_chunk_sec 的静音点切分，
    找不到静音点时按目标时长硬切，并给每块加 overlap 作为识别上下文。
    """
    if duration <= 0:
        return []

    silences = silences or []
    silence_points = [
        round((start + end) / 2, 3)
        for start, end in silences
        if end > start and (end - start) >= 0.3
    ]

    chunks: List[FunASRMacroChunk] = []
    start = 0.0
    index = 0
    while start < duration - 0.001:
        remaining = duration - start
        if remaining <= config.max_chunk_sec:
            end = duration
        else:
            target = start + config.target_chunk_sec
            min_cut = start + config.min_chunk_sec
            max_cut = min(start + config.max_chunk_sec, duration)
            candidates = [p for p in silence_points if min_cut <= p <= max_cut]
            end = min(candidates, key=lambda p: abs(p - target)) if candidates else min(target, max_cut)

        if end <= start + 0.001:
            break

        chunks.append(FunASRMacroChunk(
            index=index,
            logical_start=round(start, 3),
            logical_end=round(end, 3),
            extract_start=round(max(0.0, start - config.overlap_sec), 3),
            extract_end=round(min(duration, end + config.overlap_sec), 3),
        ))
        start = end
        index += 1

    return chunks


def _extract_audio_range(source_audio: str, output_audio: str, start: float, end: float) -> bool:
    """从已提取的 WAV 中再切一段 16kHz mono WAV，用于宏块并行识别。"""
    duration = max(0.0, end - start)
    if duration <= 0:
        return False
    part_path = output_audio + ".part"
    try:
        os.unlink(part_path)
    except FileNotFoundError:
        pass
    result = _run_asr_process(
        [
            "ffmpeg", "-y",
            "-ss", str(start),
            "-i", source_audio,
            "-t", str(duration),
            "-acodec", "pcm_s16le",
            "-ar", "16000",
            "-ac", "1",
            "-f", "wav",
            part_path,
        ],
        source_audio,
    )
    if result.returncode != 0 or not _is_valid_audio(part_path):
        try:
            os.unlink(part_path)
        except FileNotFoundError:
            pass
        return False
    os.replace(part_path, output_audio)
    return True


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


# 常见以高频字结尾的2字复合词。
# 用于避免"现在在上"这类词尾=词首同字情形被误判为结巴：
# FunASR 字符级分词下，"现 在 在 上"的两个"在"分别是"现在"的尾字和"在上"的首字，
# 若前前字+前字（即"现在"）在此集合中，则该重复字是正常的复合词边界，不标 <STU>。
_COMPOUND_WORD_ENDINGS = {
    # 以"在"结尾
    "现在", "正在", "还在", "已在", "仍在", "一直在",
    # 以"以"结尾
    "所以", "可以", "得以", "用以", "借以",
    # 以"来"结尾
    "过来", "回来", "进来", "出来", "上来", "下来", "前来",
    # 以"去"结尾
    "过去", "回去", "进去", "出去", "上去", "下去",
    # 以"到"结尾
    "想到", "说到", "做到", "看到", "听到", "感到", "得到",
    # 以"上"结尾
    "以上", "实际上", "事实上",
    # 以"下"结尾
    "以下", "底下", "手下",
    # 以"前"结尾
    "以前", "目前", "之前", "当前",
    # 以"后"结尾
    "以后", "之后", "然后", "最后",
}

# 合法的 AA 型叠词：这些两字完全相同的词是独立词汇，不是结巴。
# 规则：若 words[i-1] + words[i] 在此集合中，则不标 <STU>。
_REDUPLICATION_AA_WORDS = {
    # 日常致谢/告别
    "谢谢", "拜拜",
    # 程度/状态副词类叠词
    "好好", "慢慢", "快快", "轻轻", "深深", "静静", "默默", "悄悄",
    "明明", "清清", "暖暖", "凉凉", "多多", "少少", "早早", "晚晚",
    "高高", "低低", "圆圆", "长长", "短短", "大大", "小小",
    "重重", "厚厚", "薄薄", "稳稳", "实实", "老老",
    # 情感/语气叠词
    "哈哈", "嘻嘻", "呵呵", "嗯嗯", "哦哦", "哎哎",
    "对对", "好好", "行行",
    # 其他常见叠词
    "天天", "年年", "时时", "处处", "事事", "人人", "家家",
    "步步", "层层", "条条", "种种", "样样", "项项",
}


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
    candidate_edits: Optional[List[CandidateEdit]] = None,
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
    candidate_edits = candidate_edits or []

    def _flush_segment(segment_words: List[WordTimestamp], segment_start: float) -> Optional[Segment]:
        if not segment_words:
            return None

        seg_text = _segment_text_from_words(segment_words)
        seg_tagged = _segment_tagged_from_words(segment_words)
        duration = segment_words[-1].end - segment_start
        segment_end = segment_words[-1].end
        segment_candidates = _candidate_edits_for_range(segment_start, segment_end, candidate_edits)
        if duration < min_duration:
            segment_candidates.append(CandidateEdit(
                source="short_segment",
                edit_type="delete_range",
                start=round(segment_start, 3),
                end=round(segment_end, 3),
                text=seg_text,
                reason=f"短片段候选：时长 {duration:.2f}s，小于 {min_duration:.2f}s，需审计或人工确认",
                confidence=0.55,
                metadata={"duration": round(duration, 3), "min_duration": min_duration},
            ))
        if not seg_text:
            seg_text = _segment_tagged_from_words(segment_words) or "（无可显示文本）"
            segment_candidates.append(CandidateEdit(
                source="empty_text_segment",
                edit_type="info",
                start=round(segment_start, 3),
                end=round(segment_end, 3),
                text=seg_text,
                reason="片段去除标签后无可显示文本，已保留供检查",
                confidence=0.4,
            ))

        speakers = [w.speaker for w in segment_words if w.speaker]
        speaker = max(set(speakers), key=speakers.count) if speakers else None
        segment = Segment(
            start=round(segment_start, 3),
            end=round(segment_end, 3),
            text=seg_text,
            tagged_text=seg_tagged,
            speaker=speaker,
            action=SegmentAction.KEEP,
            words=list(segment_words),
            candidate_edits=segment_candidates,
        )
        for edit in segment.candidate_edits:
            edit.segment_id = segment.id
        return segment

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


# 段首孤立助词集合：这些字单独成为一段时，通常是上一句的语尾助词被 ASR/VAD 误切
_LEADING_PARTICLES = set("的嘛呢啊哦嗯吧呀哈噢喔了么吗")
# 段首孤立助词回并的最大间隔（秒），超过此间隔认为是有意义的停顿不合并
_PARTICLE_MERGE_MAX_GAP = 1.0

# 句末标点后可能因 VAD 产生“我 / 们继续说”这类孤立句首代词。完整短答（如“好”）
# 不在此集合，避免错误吞并独立表达。
_SENTENCE_START_PRONOUNS = {
    "我", "你", "他", "她", "它", "咱",
    "我们", "你们", "他们", "她们", "它们", "咱们",
}
_SENTENCE_START_FRAGMENT_MAX_GAP = 1.0
_SENTENCE_START_FRAGMENT_MAX_DURATION = 0.5

# 绝不能作为独立字幕卡存在的标点。除了中文标点，也覆盖部分 ASR
# 会原样输出的英文标点；句末标点若被 VAD 单独切出，同样应回并前一段。
_STANDALONE_PUNCTUATION = set("，、；：。！？,.!?;:…")

# 短碎段语义回并：ASR/VAD 常会把“所以它更偏 / 理科啊”这类同一句
# 切成两张卡。这里只处理短间隔、无明确句末标点的保守场景。
_SEMANTIC_FRAGMENT_MERGE_MAX_GAP = 0.5
_SEMANTIC_FRAGMENT_MERGE_MAX_CHARS = 42
_TRAILING_FRAGMENT_ENDINGS = (
    "所以", "因为", "但是", "然后", "那么", "就是", "这个是", "那就是",
    "也就是", "比如", "包括", "尤其是", "更偏", "偏", "比较", "属于",
    "变成", "来自", "指的是", "不是为了", "是为了", "能不能", "有没有",
)
_LEADING_CONTINUATION_PREFIXES = (
    "的", "了", "呢", "嘛", "吗", "吧", "啊", "呀", "而且", "以及", "包括",
    "也", "还", "都", "就", "才", "又", "再", "则", "中", "上", "下",
    "里面", "外面", "前面", "后面", "理科", "文科", "逻辑",
)
_COMPLETE_SHORT_UTTERANCES = {
    "好", "好的", "可以", "对", "没错", "是的", "确实", "明白", "明白了",
    "谢谢", "不用", "没事", "行", "行吧",
}
_DISCOURSE_TAIL_MIGRATION_ANCHORS = ("对吧", "是吧", "对不对", "是不是")
_TAIL_MIGRATION_ANCHORS = (
    "所以", "因为", "但是", "然后", "那么", "这个是", "那就是", "也就是",
    *_DISCOURSE_TAIL_MIGRATION_ANCHORS,
)
_TAIL_MIGRATION_MAX_CHARS = 12
_DISCOURSE_TAIL_MIGRATION_MAX_GAP = 0.15
_DISCOURSE_TAIL_MIGRATION_MAX_DURATION = MAX_SEGMENT_DURATION + 2.0
_PROTECTED_PHRASES = ("谁是卧底",)
_PROTECTED_PHRASE_MERGE_MAX_GAP = 0.9
_SENTENCE_SPLIT_MIN_CHARS = 2


def _clean_word_text(raw_word: str) -> str:
    return re.sub(r'<FIL[^>]*>', '', raw_word).replace("<STU>", "").strip()


def strip_analysis_tags_from_words(words: List[WordTimestamp]) -> List[WordTimestamp]:
    """重做分离/分析时移除上轮分析标签，避免 <STU>/<FIL> 被重复处理。"""
    cleaned: List[WordTimestamp] = []
    for word in words:
        text = _clean_word_text(word.word) or word.word
        cleaned.append(WordTimestamp(
            word=text,
            start=word.start,
            end=word.end,
            speaker=word.speaker,
        ))
    return cleaned


def _segment_text_from_words(words: List[WordTimestamp]) -> str:
    return "".join(_clean_word_text(w.word) for w in words).strip()


def _segment_tagged_from_words(words: List[WordTimestamp]) -> str:
    return "".join(w.word for w in words).strip()


def _majority_speaker(words: List[WordTimestamp]) -> Optional[str]:
    speakers = [w.speaker for w in words if w.speaker]
    return max(set(speakers), key=speakers.count) if speakers else None


_FIL_TAG_RE = re.compile(r"<FIL(?:\s+s=(?P<start>[0-9.]+)\s+e=(?P<end>[0-9.]+))?>")


def _extract_filler_candidate_edits(words: List[WordTimestamp]) -> List[CandidateEdit]:
    """把 <FIL s=... e=...> 字符串标签转成可追踪的结构化候选编辑。"""
    edits: List[CandidateEdit] = []
    for idx, word in enumerate(words):
        raw = word.word
        has_stu = raw.startswith("<STU>")
        tagged = raw[5:] if has_stu else raw
        match = _FIL_TAG_RE.match(tagged)
        if not match:
            continue
        text = tagged[match.end():]
        start = float(match.group("start") or word.start)
        end = float(match.group("end") or word.end)
        if end <= start:
            start, end = word.start, word.end
        edits.append(CandidateEdit(
            source="filler",
            edit_type="delete_range",
            start=round(start, 3),
            end=round(end, 3),
            text=text,
            reason="语气词候选：可由 AI 审计或人工确认后切除",
            confidence=0.75,
            word_index=idx,
            metadata={"word_start": word.start, "word_end": word.end, "has_stutter_tag": has_stu},
        ))
    return edits


def _extract_stutter_candidate_edits(words: List[WordTimestamp]) -> List[CandidateEdit]:
    edits: List[CandidateEdit] = []
    for idx, word in enumerate(words):
        if not word.word.startswith("<STU>"):
            continue
        text = word.word[5:]
        edits.append(CandidateEdit(
            source="stutter",
            edit_type="subtitle_fix",
            start=round(word.start, 3),
            end=round(word.end, 3),
            text=text,
            reason="结巴/重复候选：建议由审计决定删除字幕重复或保留强调",
            confidence=0.65,
            word_index=idx,
            metadata={"raw_word": word.word},
        ))
    return edits


def _overlap_seconds(start: float, end: float, edit: CandidateEdit) -> float:
    return max(0.0, min(end, edit.end) - max(start, edit.start))


def _candidate_edits_for_range(start: float, end: float, edits: List[CandidateEdit]) -> List[CandidateEdit]:
    return [
        edit.model_copy(deep=True)
        for edit in edits
        if _overlap_seconds(start, end, edit) > 0 or (start <= edit.start <= end)
    ]


def _attach_candidate_edits_to_segments(segments: List[Segment], edits: List[CandidateEdit]) -> List[Segment]:
    """按时间范围把候选编辑挂到片段，并回填候选所属 segment_id。"""
    if not edits:
        return segments
    for segment in segments:
        segment_edits: List[CandidateEdit] = [edit.model_copy(deep=True) for edit in (segment.candidate_edits or [])]
        existing_ids = {edit.id for edit in segment_edits}
        for edit in edits:
            if _overlap_seconds(segment.start, segment.end, edit) <= 0 and not (segment.start <= edit.start <= segment.end):
                continue
            if edit.id in existing_ids:
                continue
            if edit.segment_id is None:
                edit.segment_id = segment.id
            copied = edit.model_copy(deep=True)
            copied.segment_id = segment.id
            segment_edits.append(copied)
            existing_ids.add(copied.id)
        segment.candidate_edits = segment_edits
    return segments


def merge_leading_particles(segments: List[Segment]) -> List[Segment]:
    """
    后处理：把被 ASR/VAD 误切成独立段的语尾助词回并到上一段。

    触发条件（同时满足）：
    1. 当前段只有一个字，且属于孤立助词集合
    2. 当前段与上一段 gap ≤ 1s
    3. 没有确认的真实说话人切换

    “的”“吗”等字单独作为字幕卡没有语义价值；但不处理“啊，你好”这类
    含后续正文的段，避免跨句误并。
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
        segment_text = _segment_text_from_words(list(seg.words))

        gap = seg.start - prev.end

        is_particle = (
            len(first_char) == 1
            and first_char in _LEADING_PARTICLES
            and segment_text == first_char
            and gap <= _PARTICLE_MERGE_MAX_GAP
            and _can_merge_speakers(prev, seg)
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


def merge_sentence_start_fragments(
    segments: List[Segment],
    max_gap: float = _SENTENCE_START_FRAGMENT_MAX_GAP,
    max_duration: float = MAX_SEGMENT_DURATION,
) -> List[Segment]:
    """
    把句号后被误切出的孤立代词并入下一段。

    例如“上一句。 / 我 / 们继续说”应显示为“上一句。”和“我们继续说”。
    只合并明确的代词，且要求与下一段同一说话人、间隔短、总时长受限，
    因此不会吞并“好。 / 对 / 我们继续说”中的独立短答。
    """
    if len(segments) < 3:
        return segments

    result: List[Segment] = []
    index = 0
    while index < len(segments):
        curr = segments[index]
        prev = result[-1] if result else None
        next_seg = segments[index + 1] if index + 1 < len(segments) else None
        curr_text = (curr.text or "").strip()

        should_merge_forward = (
            prev is not None
            and next_seg is not None
            and _ends_with_sentence_punctuation((prev.text or "").strip())
            and curr_text in _SENTENCE_START_PRONOUNS
            and (curr.end - curr.start) <= _SENTENCE_START_FRAGMENT_MAX_DURATION
            and 0 <= (next_seg.start - curr.end) <= max_gap
            and len(curr_text) + len((next_seg.text or "").strip()) <= _SEMANTIC_FRAGMENT_MERGE_MAX_CHARS
            and (next_seg.end - curr.start) <= max_duration
            and _can_merge_speakers(curr, next_seg)
        )
        if should_merge_forward:
            result.append(_merge_segment_pair(curr, next_seg))
            index += 2
            continue

        result.append(curr)
        index += 1

    return result


def merge_leading_punctuation(segments: List[Segment]) -> List[Segment]:
    """
    后处理：把被 VAD 切成独立卡或切到下一段段首的标点回并。

    ct-punc-c 按语义插标点，VAD 按静音切段，两者边界不对齐时标点会变成
    下一段的首字符，甚至单独成为一张字幕卡。无论标点前后停顿多久，单独显示
    都没有语义价值；优先回并上一段，确保 Review 和导出字幕不出现“。”或“?”单独一行。
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

        if first_char not in _STANDALONE_PUNCTUATION or not result:
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


def _ends_with_sentence_punctuation(text: str) -> bool:
    return bool(text) and text[-1] in SENTENCE_END_PUNCT


def _looks_like_trailing_fragment(text: str) -> bool:
    if not text or _ends_with_sentence_punctuation(text):
        return False
    if text in _COMPLETE_SHORT_UTTERANCES:
        return False
    if any(text.endswith(ending) for ending in _TRAILING_FRAGMENT_ENDINGS):
        return True
    return len(text) <= 4


def _looks_like_leading_continuation(text: str) -> bool:
    if not text:
        return False
    candidates = [text]
    if len(text) >= 2 and text[0] == text[1]:
        candidates.append(text[1:])
    return any(candidate.startswith(prefix) for candidate in candidates for prefix in _LEADING_CONTINUATION_PREFIXES)


def _can_merge_speakers(prev: Segment, curr: Segment) -> bool:
    return not (prev.speaker and curr.speaker and prev.speaker != curr.speaker)


def _merge_segment_pair(prev: Segment, curr: Segment) -> Segment:
    merged = copy.copy(prev)
    merged_words = list(prev.words or []) + list(curr.words or [])
    merged_candidate_edits = [
        edit.model_copy(deep=True)
        for edit in list(prev.candidate_edits or []) + list(curr.candidate_edits or [])
    ]
    merged.end = round(curr.end, 3)
    if merged_words:
        merged.words = merged_words
        merged.text = _segment_text_from_words(merged_words)
        merged.tagged_text = _segment_tagged_from_words(merged_words)
        merged.speaker = _majority_speaker(merged_words) or prev.speaker or curr.speaker
    else:
        merged.text = ((prev.text or "") + (curr.text or "")).strip()
        merged.tagged_text = ((prev.tagged_text or "") + (curr.tagged_text or "")).strip()
        merged.speaker = prev.speaker or curr.speaker
    merged.candidate_edits = merged_candidate_edits
    merged.merged = True
    return merged


def _matching_protected_phrase(prev_text: str, curr_text: str) -> Optional[str]:
    for phrase in _PROTECTED_PHRASES:
        for split_idx in range(1, len(phrase)):
            if prev_text.endswith(phrase[:split_idx]) and curr_text.startswith(phrase[split_idx:]):
                return phrase
    return None


def _copy_segment_with_words(
    base: Segment,
    words: List[WordTimestamp],
    start: float,
    end: float,
    suffix: str,
) -> Optional[Segment]:
    text = _segment_text_from_words(words)
    if not words or not text:
        return None
    seg = copy.copy(base)
    seg.id = f"{base.id}_{suffix}" if getattr(base, "id", None) else None
    seg.start = round(start, 3)
    seg.end = round(end, 3)
    seg.text = text
    seg.tagged_text = _segment_tagged_from_words(words)
    seg.words = list(words)
    seg.speaker = _majority_speaker(words) or base.speaker
    return seg


def _word_index_for_char_offset(words: List[WordTimestamp], char_offset: int) -> Optional[int]:
    cursor = 0
    for idx, word in enumerate(words):
        text = _clean_word_text(word.word)
        next_cursor = cursor + len(text)
        if cursor <= char_offset < next_cursor:
            # 只在词边界切，避免 Whisper 多字词被切坏；FunASR 字级时间戳会命中这里。
            return idx if char_offset == cursor else None
        cursor = next_cursor
    return None


def _find_tail_fragment_word_index(seg: Segment) -> Optional[int]:
    words = list(seg.words or [])
    text = _segment_text_from_words(words)
    if not words or not text or _ends_with_sentence_punctuation(text):
        return None

    best_pos = -1
    for anchor in _TAIL_MIGRATION_ANCHORS:
        pos = text.rfind(anchor)
        if pos > best_pos:
            best_pos = pos

    if best_pos <= 0:
        return None

    suffix = text[best_pos:]
    if len(suffix) > _TAIL_MIGRATION_MAX_CHARS or not _looks_like_trailing_fragment(suffix):
        return None

    return _word_index_for_char_offset(words, best_pos)


def _move_trailing_fragment_to_next(prev: Segment, curr: Segment, split_idx: int) -> Optional[tuple[Segment, Segment]]:
    prev_words = list(prev.words or [])
    curr_words = list(curr.words or [])
    if split_idx <= 0 or split_idx >= len(prev_words) or not curr_words:
        return None

    head_words = prev_words[:split_idx]
    tail_words = prev_words[split_idx:]
    if not head_words or not tail_words:
        return None

    boundary = round((head_words[-1].end + tail_words[0].start) / 2, 3)
    new_prev = copy.copy(prev)
    new_prev.end = boundary
    new_prev.words = head_words
    new_prev.text = _segment_text_from_words(head_words)
    new_prev.tagged_text = _segment_tagged_from_words(head_words)
    new_prev.speaker = _majority_speaker(head_words) or prev.speaker

    new_curr = copy.copy(curr)
    new_curr.start = boundary
    new_curr.words = tail_words + curr_words
    new_curr.text = _segment_text_from_words(new_curr.words)
    new_curr.tagged_text = _segment_tagged_from_words(new_curr.words)
    new_curr.speaker = _majority_speaker(new_curr.words) or curr.speaker
    return new_prev, new_curr


def merge_short_semantic_fragments(
    segments: List[Segment],
    max_gap: float = _SEMANTIC_FRAGMENT_MERGE_MAX_GAP,
    max_chars: int = _SEMANTIC_FRAGMENT_MERGE_MAX_CHARS,
    max_duration: float = MAX_SEGMENT_DURATION,
) -> List[Segment]:
    """
    后处理：保守回并同一句被 ASR 过度切开的短碎段。

    目标场景：上一段是明显半句话或短句头，下一段紧接着补足语义，
    如“所以它更偏” + “理科啊”。明确句末标点、长停顿、真实说话人切换、
    超长字幕都不合并。
    """
    if len(segments) < 2:
        return segments

    result: List[Segment] = []
    for seg in segments:
        if not result:
            result.append(seg)
            continue

        prev = result[-1]
        prev_text = (prev.text or "").strip()
        curr_text = (seg.text or "").strip()
        if not prev_text or not curr_text:
            result.append(seg)
            continue

        gap = seg.start - prev.end
        merged_text_len = len(prev_text) + len(curr_text)
        merged_duration = seg.end - prev.start
        prev_fragment = _looks_like_trailing_fragment(prev_text)
        curr_continuation = _looks_like_leading_continuation(curr_text)
        curr_short_fragment = len(curr_text) <= 6 and curr_text not in _COMPLETE_SHORT_UTTERANCES
        prev_short_open = (
            len(prev_text) <= 8
            and not _ends_with_sentence_punctuation(prev_text)
            and prev_text not in _COMPLETE_SHORT_UTTERANCES
            and curr_continuation
        )
        protected_phrase = _matching_protected_phrase(prev_text, curr_text)
        should_merge_protected_phrase = (
            protected_phrase is not None
            and 0 <= gap <= _PROTECTED_PHRASE_MERGE_MAX_GAP
            and merged_text_len <= max_chars
            and merged_duration <= max_duration
            and _can_merge_speakers(prev, seg)
        )
        tail_split_idx = _find_tail_fragment_word_index(prev)
        tail_words = list(prev.words or [])[tail_split_idx:] if tail_split_idx is not None else []
        tail_text = _segment_text_from_words(tail_words)
        tail_migration_chars = len(tail_text) + len(curr_text)
        tail_migration_duration = seg.end - tail_words[0].start if tail_words else 0
        is_discourse_tail = tail_text in _DISCOURSE_TAIL_MIGRATION_ANCHORS
        should_move_discourse_tail = (
            is_discourse_tail
            and 0 <= gap <= _DISCOURSE_TAIL_MIGRATION_MAX_GAP
            and (prev.end - prev.start) >= (max_duration - 0.75)
            and tail_migration_duration <= _DISCOURSE_TAIL_MIGRATION_MAX_DURATION
            and _can_merge_speakers(prev, seg)
        )
        should_move_tail = (
            should_move_discourse_tail
            or (
                tail_split_idx is not None
                and 0 <= gap <= max_gap
                and tail_migration_chars <= max_chars
                and tail_migration_duration <= max_duration
                and curr_continuation
                and _can_merge_speakers(prev, seg)
            )
        )

        should_merge = (
            0 <= gap <= max_gap
            and merged_text_len <= max_chars
            and merged_duration <= max_duration
            and not _ends_with_sentence_punctuation(prev_text)
            and _can_merge_speakers(prev, seg)
            and (prev_fragment or curr_short_fragment or prev_short_open)
        )

        if should_move_tail:
            moved = _move_trailing_fragment_to_next(prev, seg, tail_split_idx)
            if moved:
                new_prev, new_curr = moved
                result[-1] = new_prev
                result.append(new_curr)
                continue

        if should_merge_protected_phrase:
            result[-1] = _merge_segment_pair(prev, seg)
        elif should_merge:
            result[-1] = _merge_segment_pair(prev, seg)
        else:
            result.append(seg)

    return result


def split_segments_at_sentence_punctuation(
    segments: List[Segment],
    min_side_chars: int = _SENTENCE_SPLIT_MIN_CHARS,
) -> List[Segment]:
    """
    后处理：把同一段内的明确句末标点拆成独立句子卡片。

    这一步服务于 Review 交互：一张卡只承载一句完整话，方便用户按句快速隐藏/展示。
    仅处理段内的“。！？”，且要求切点左右都有实际文字，避免制造空段或单字碎段。
    """
    result: List[Segment] = []
    for seg in segments:
        words = list(seg.words or [])
        if not words:
            result.append(seg)
            continue

        parts: list[tuple[int, int]] = []
        start_idx = 0
        for idx, word in enumerate(words[:-1]):
            raw_char = _clean_word_text(word.word)
            if raw_char not in SENTENCE_END_PUNCT:
                continue

            left_text = _segment_text_from_words(words[start_idx:idx + 1])
            right_text = _segment_text_from_words(words[idx + 1:])
            if len(left_text) < min_side_chars or len(right_text) < min_side_chars:
                continue

            parts.append((start_idx, idx + 1))
            start_idx = idx + 1

        if not parts:
            result.append(seg)
            continue

        parts.append((start_idx, len(words)))
        for part_idx, (part_start, part_end) in enumerate(parts):
            part_words = words[part_start:part_end]
            part_start_time = seg.start if part_idx == 0 else part_words[0].start
            part_end_time = seg.end if part_idx == len(parts) - 1 else part_words[-1].end
            new_seg = _copy_segment_with_words(
                seg,
                part_words,
                part_start_time,
                part_end_time,
                f"sent{part_idx}",
            )
            if new_seg:
                result.append(new_seg)

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


def postprocess_segments(
    segments: List[Segment],
    *,
    enable_diarization: bool = False,
) -> List[Segment]:
    """统一 ASR 分段后处理，确保新识别和动态重分段规则一致。"""
    segments = merge_leading_particles(segments)
    segments = merge_leading_punctuation(segments)
    segments = merge_sentence_start_fragments(segments)
    segments = merge_short_semantic_fragments(segments)
    segments = split_segments_at_sentence_punctuation(segments)
    if enable_diarization:
        segments = split_at_speaker_changes(segments, min_duration=0.3)
    return segments


async def run_whisper_asr(
    audio_path: str,
    language: str = "zh",
    log_callback: Optional[Callable] = None,
    whisper_model: str = "base",
) -> List[WordTimestamp]:
    """
    Run faster-whisper ASR with word-level timestamps.
    瞬时加载/推理错误统一按首次 + 3 次重试；生产路径绝不静默生成 mock 数据。
    """
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError("faster-whisper 未安装，无法执行 Whisper ASR") from exc

    async def _attempt() -> List[WordTimestamp]:
        try:
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
                    vad_parameters={"min_silence_duration_ms": 500},
                )
                words = []
                for segment in segments:
                    if segment.words:
                        for word in segment.words:
                            words.append(WordTimestamp(
                                word=word.word.strip(),
                                start=round(word.start, 3),
                                end=round(word.end, 3),
                            ))
                return words

            return await loop.run_in_executor(None, _transcribe_words)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise RetryableExecutionError(f"Whisper ASR 瞬时失败: {exc}") from exc

    try:
        words = await execute_with_retry(_attempt)
    except Exception as exc:
        if log_callback:
            await log_callback("error", "asr", f"Whisper ASR 失败（已重试 3 次）: {exc}")
        raise
    if log_callback:
        await log_callback("success", "asr", f"ASR 完成，识别 {len(words)} 个词")
    return words


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


def _parse_funasr_words(res) -> tuple[List[WordTimestamp], str]:
    """将 FunASR generate 结果转为字符级 WordTimestamp。"""
    if not res or not res[0].get("timestamp"):
        return [], ""

    text: str = res[0].get("text", "")
    timestamps: list = res[0]["timestamp"]  # [[start_ms, end_ms], ...]
    non_space_chars = [c for c in text if c != " "]

    words = []
    for i, ts in enumerate(timestamps):
        char = non_space_chars[i] if i < len(non_space_chars) else ""
        if not char:
            continue
        words.append(WordTimestamp(
            word=char,
            start=round(ts[0] / 1000, 3),
            end=round(ts[1] / 1000, 3),
        ))
    return words, text


async def _run_funasr_asr_single_pass(
    audio_path: str,
    log_callback: Optional[Callable] = None,
    *,
    recalibrate: bool = True,
) -> List[WordTimestamp]:
    """单段 FunASR 识别。短音频和宏块 worker 共用。"""
    try:
        model = await get_funasr_model(log_callback=log_callback)

        if log_callback:
            await log_callback("info", "asr", "开始字符级语音识别...")

        # 在后台线程中执行推理，避免阻塞事件循环
        loop = asyncio.get_event_loop()
        res = await loop.run_in_executor(
            None,
            lambda: model.generate(input=audio_path, batch_size_s=300)
        )

        words, text = _parse_funasr_words(res)
        if not words:
            if log_callback:
                await log_callback("warn", "asr", "FunASR 未返回时间戳，结果为空")
            return []

        if log_callback:
            await log_callback(
                "success", "asr",
                f"FunASR 完成，识别 {len(words)} 个字符，文本含标点：{text[:60]}..."
            )

        if recalibrate:
            # FunASR 对长音频的 CTC 对齐会产生累积漂移，保留现有校准策略。
            words = await _recalibrate_timestamps(audio_path, words, log_callback)

        return words

    except Exception as e:
        if log_callback:
            await log_callback("error", "asr", f"FunASR 识别错误: {str(e)}")
        raise


async def run_funasr_asr(
    audio_path: str,
    log_callback: Optional[Callable] = None,
    chunk_config: Optional[FunASRChunkConfig] = None,
    source_fingerprint: Optional[dict] = None,
) -> List[WordTimestamp]:
    """
    使用阿里达摩院 FunASR Paraformer-zh 进行字符级语音识别。
    短音频沿用原全量识别；长音频按全局配置切为宏块并行识别后合并时间轴。
    """
    chunk_config = chunk_config or resolve_funasr_chunk_config()
    duration = get_video_duration(audio_path) if os.path.exists(audio_path) else 0.0
    if (
        not chunk_config.enabled
        or duration <= 0
        or duration < chunk_config.min_duration_to_chunk
    ):
        async def _attempt_short() -> List[WordTimestamp]:
            try:
                return await _run_funasr_asr_single_pass(audio_path, log_callback=log_callback)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                raise RetryableExecutionError(f"FunASR 识别失败: {exc}") from exc

        return await execute_with_retry(_attempt_short)

    return await _run_funasr_asr_chunked(
        audio_path,
        duration,
        log_callback,
        chunk_config,
        source_fingerprint=source_fingerprint,
    )


def _funasr_checkpoint_dir(audio_path: str) -> Path:
    configured = os.environ.get("GOLDENCLIP_ASR_CHECKPOINT_DIR")
    if configured:
        return Path(configured) / "funasr"
    return Path(audio_path).parent / ".goldenclip_checkpoints" / "funasr"


def _funasr_chunk_cache_identity(
    audio_path: str,
    chunk: FunASRMacroChunk,
    config: FunASRChunkConfig,
    source_fingerprint: Optional[dict],
) -> tuple[str, dict]:
    identity = {
        "schema": _ASR_CHECKPOINT_SCHEMA,
        "source": source_fingerprint or _file_fingerprint(audio_path),
        "audio": _file_fingerprint(audio_path),
        "model": {
            "engine": "funasr",
            "asr_model": "paraformer-zh",
            "vad_model": "fsmn-vad",
            "punc_model": "ct-punc-c",
            "batch_size_s": 300,
            "recalibrate": config.recalibrate_chunks,
        },
        "interval": {
            "logical_start": chunk.logical_start,
            "logical_end": chunk.logical_end,
            "extract_start": chunk.extract_start,
            "extract_end": chunk.extract_end,
        },
    }
    return _stable_digest(identity), identity


def _load_funasr_chunk_cache(path: Path, digest: str) -> Optional[List[WordTimestamp]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema") != _ASR_CHECKPOINT_SCHEMA or payload.get("digest") != digest:
            return None
        raw_words = payload.get("words")
        if not isinstance(raw_words, list):
            return None
        words = [WordTimestamp.model_validate(item) for item in raw_words]
        if any(word.end < word.start for word in words):
            return None
        return words
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _save_funasr_chunk_cache(
    path: Path,
    digest: str,
    identity: dict,
    words: List[WordTimestamp],
) -> None:
    _atomic_write_json(path, {
        "schema": _ASR_CHECKPOINT_SCHEMA,
        "digest": digest,
        "identity": identity,
        "words": [word.model_dump() for word in words],
    })


def _shift_words(words: List[WordTimestamp], offset: float) -> List[WordTimestamp]:
    """将 chunk 内相对时间戳映射回完整音频时间轴。"""
    if not words or offset == 0:
        return words
    return [
        WordTimestamp(
            word=w.word,
            start=round(w.start + offset, 3),
            end=round(w.end + offset, 3),
            speaker=w.speaker,
        )
        for w in words
    ]


def _is_content_char(char: str) -> bool:
    return bool(char) and unicodedata.category(char)[0] in ("L", "N")


def _content_chars_with_word_indices(words: List[WordTimestamp]) -> tuple[List[str], List[int]]:
    chars: List[str] = []
    indices: List[int] = []
    for idx, word in enumerate(words):
        clean = re.sub(r"<[^>]*>", "", word.word)
        for char in clean:
            if _is_content_char(char):
                chars.append(char)
                indices.append(idx)
    return chars, indices


def _find_right_overlap_skip(
    left_words: List[WordTimestamp],
    right_words: List[WordTimestamp],
    min_run: int = 3,
) -> int:
    """
    在左右重叠窗口中找 trigram 级别的最长相同 run。
    返回 right_words 需要跳过的词数，避免块边界重复。
    """
    left_chars, _left_indices = _content_chars_with_word_indices(left_words)
    right_chars, right_indices = _content_chars_with_word_indices(right_words)
    if len(left_chars) < min_run or len(right_chars) < min_run:
        return 0

    left_positions: dict[tuple[str, ...], List[int]] = {}
    for idx in range(len(left_chars) - min_run + 1):
        key = tuple(left_chars[idx:idx + min_run])
        left_positions.setdefault(key, []).append(idx)

    best_right = None
    best_run = 0
    for r_idx in range(len(right_chars) - min_run + 1):
        key = tuple(right_chars[r_idx:r_idx + min_run])
        for l_idx in left_positions.get(key, []):
            run = 0
            while (
                l_idx + run < len(left_chars)
                and r_idx + run < len(right_chars)
                and left_chars[l_idx + run] == right_chars[r_idx + run]
            ):
                run += 1
            if run > best_run:
                best_run = run
                best_right = r_idx

    if best_right is None or best_run < min_run:
        return 0

    last_dup_char = best_right + best_run - 1
    last_dup_word_idx = right_indices[last_dup_char]
    return last_dup_word_idx + 1


def merge_funasr_macro_chunks(
    chunks: List[FunASRMacroChunk],
    config: FunASRChunkConfig = DEFAULT_FUNASR_CHUNK_CONFIG,
) -> List[WordTimestamp]:
    """
    合并宏块识别结果。块间重叠区优先用 trigram 去重，失败时按逻辑边界硬切。
    合并后只保证全局时间顺序，最终分段仍交给 segment_by_silence。
    """
    chunks = sorted(chunks, key=lambda c: c.index)
    if not chunks:
        return []

    merged: List[WordTimestamp] = []
    window_pad = config.overlap_sec + 0.5

    for chunk in chunks:
        words = sorted(chunk.words, key=lambda w: (w.start, w.end))
        # 保留 overlap 上下文，交给边界合并去重；避免识别在边界轻微偏移时丢字。
        words = [
            w for w in words
            if chunk.logical_start - window_pad <= w.start <= chunk.logical_end + window_pad
        ]
        if not words:
            continue

        if not merged:
            merged.extend(words)
            continue

        boundary = chunk.logical_start
        left_tail = [w for w in merged if boundary - window_pad <= w.start <= boundary + window_pad]
        right_head = [w for w in words if boundary - window_pad <= w.start <= boundary + window_pad]
        skip_count = _find_right_overlap_skip(left_tail, right_head)

        if skip_count > 0:
            right_head_ids = {id(w): i for i, w in enumerate(right_head)}
            skipped = 0
            append_words = []
            for word in words:
                idx = right_head_ids.get(id(word))
                if idx is not None and skipped < skip_count:
                    skipped += 1
                    continue
                append_words.append(word)
        else:
            # 匹配失败时采用时间边界兜底，避免明显重复。
            append_words = [w for w in words if w.end >= boundary - 0.05]

        merged.extend(append_words)

    return _dedupe_and_sort_words(merged)


def _dedupe_and_sort_words(words: List[WordTimestamp]) -> List[WordTimestamp]:
    """按时间排序并去除 overlap 里同字符、近时间的重复识别。"""
    result: List[WordTimestamp] = []
    for word in sorted(words, key=lambda w: (w.start, w.end)):
        if result:
            prev = result[-1]
            same_char = prev.word == word.word
            close_time = abs(prev.start - word.start) < 0.35 or word.start < prev.end
            if same_char and close_time:
                # 保留更早的实例，通常来自左侧 chunk，时间轴更连续。
                continue
        result.append(word)
    return result


async def _run_funasr_asr_chunked(
    audio_path: str,
    duration: float,
    log_callback: Optional[Callable],
    config: FunASRChunkConfig,
    *,
    source_fingerprint: Optional[dict] = None,
) -> List[WordTimestamp]:
    """长音频宏块并行 FunASR。"""
    silences = _detect_silences(audio_path)
    chunks = plan_funasr_macro_chunks(duration, silences=silences, config=config)
    if len(chunks) <= 1:
        return await _run_funasr_asr_single_pass(audio_path, log_callback=log_callback)

    if log_callback:
        await log_callback(
            "info", "asr",
            f"长音频启用分块识别：{len(chunks)} 块，并行度 {config.max_parallel_chunks}"
        )

    # 预加载模型，避免多个 chunk 同时触发模型初始化。
    await get_funasr_model(log_callback=log_callback)
    if config.recalibrate_chunks:
        await _get_paraformer_model()

    temp_dir = tempfile.mkdtemp(prefix="goldenclip_asr_chunks_")
    checkpoint_dir = _funasr_checkpoint_dir(audio_path)
    sem = asyncio.Semaphore(max(1, config.max_parallel_chunks))
    completed = 0

    async def _process_chunk(chunk: FunASRMacroChunk) -> FunASRMacroChunk:
        nonlocal completed
        async with sem:
            digest, identity = _funasr_chunk_cache_identity(
                audio_path,
                chunk,
                config,
                source_fingerprint,
            )
            cache_path = checkpoint_dir / f"{digest}.json"
            cached_words = _load_funasr_chunk_cache(cache_path, digest)
            if cached_words is not None:
                chunk.words = cached_words
                completed += 1
                if log_callback:
                    await log_callback(
                        "info", "asr",
                        f"分块 {chunk.index + 1}/{len(chunks)} 命中断点缓存",
                        progress=completed / len(chunks),
                    )
                return chunk

            if log_callback:
                await log_callback(
                    "info", "asr",
                    f"分块识别中 {chunk.index + 1}/{len(chunks)} "
                    f"({chunk.logical_start:.1f}s~{chunk.logical_end:.1f}s)"
                )

            async def _attempt_chunk() -> List[WordTimestamp]:
                chunk_audio = str(Path(temp_dir) / f"chunk_{chunk.index:03d}.wav")
                try:
                    loop = asyncio.get_running_loop()
                    ok = await loop.run_in_executor(
                        None,
                        lambda: _extract_audio_range(
                            audio_path,
                            chunk_audio,
                            chunk.extract_start,
                            chunk.extract_end,
                        ),
                    )
                    if not ok:
                        raise RuntimeError(f"chunk {chunk.index} 音频提取失败")
                    return await _run_funasr_asr_single_pass(
                        chunk_audio,
                        log_callback=None,
                        recalibrate=config.recalibrate_chunks,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    raise RetryableExecutionError(
                        f"FunASR 分块 {chunk.index + 1} 失败: {exc}"
                    ) from exc

            words = await execute_with_retry(_attempt_chunk)
            chunk.words = _shift_words(words, chunk.extract_start)
            _save_funasr_chunk_cache(cache_path, digest, identity, chunk.words)
            completed += 1
            if log_callback:
                await log_callback(
                    "info", "asr",
                    f"分块 {chunk.index + 1}/{len(chunks)} 完成，识别 {len(chunk.words)} 个字符",
                    progress=completed / len(chunks),
                )
            return chunk

    try:
        results = await asyncio.gather(
            *[_process_chunk(chunk) for chunk in chunks],
            return_exceptions=True,
        )
        failures = [result for result in results if isinstance(result, BaseException)]
        if failures:
            raise RuntimeError(
                f"{len(failures)} 个 FunASR 分块重试耗尽，首个错误: {failures[0]}"
            ) from failures[0]
        completed_chunks = [result for result in results if isinstance(result, FunASRMacroChunk)]
        if log_callback:
            await log_callback("info", "asr", "正在合并分块时间轴...")
        words = merge_funasr_macro_chunks(completed_chunks, config=config)
        if log_callback:
            await log_callback("success", "asr", f"分块识别完成，合并后 {len(words)} 个字符")
        return words
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


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


def _diarization_cache_identity(audio_path: str, oracle_num: Optional[int]) -> tuple[str, dict]:
    identity = {
        "schema": _ASR_CHECKPOINT_SCHEMA,
        "source": _file_fingerprint(audio_path),
        "model": "iic/speech_campplus_speaker-diarization_common",
        "oracle_num": oracle_num,
    }
    return _stable_digest(identity), identity


def _diarization_cache_path(audio_path: str, digest: str) -> Path:
    configured = os.environ.get("GOLDENCLIP_ASR_CHECKPOINT_DIR")
    root = Path(configured) if configured else Path(audio_path).parent / ".goldenclip_checkpoints"
    return root / "diarization" / f"{digest}.json"


def _validate_diarization_segments(value) -> Optional[List[dict]]:
    if not isinstance(value, list):
        return None
    normalized: List[dict] = []
    try:
        for item in value:
            if not isinstance(item, dict):
                return None
            start = float(item["start"])
            end = float(item["end"])
            speaker = str(item["speaker"])
            if start < 0 or end <= start or not speaker:
                return None
            normalized.append({"start": start, "end": end, "speaker": speaker})
    except (KeyError, TypeError, ValueError):
        return None
    return normalized


def _load_diarization_cache(path: Path, digest: str) -> Optional[List[dict]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema") != _ASR_CHECKPOINT_SCHEMA or payload.get("digest") != digest:
            return None
        segments = _validate_diarization_segments(payload.get("segments"))
        return segments if segments else None
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


async def run_diarization(
    audio_path: str,
    oracle_num: Optional[int] = None,
    log_callback: Optional[Callable] = None,
    timeout: float = 600.0,
    failure_out: Optional[dict] = None,
) -> List[dict]:
    """
    使用 ModelScope CAM++ 模型执行说话人分离。
    模型：iic/speech_campplus_speaker-diarization_common
    权重自动从 ModelScope 下载，缓存至 ~/.cache/modelscope。

    返回：[{'start': float, 'end': float, 'speaker': str}, ...]。
    失败会统一重试 3 次；耗尽后保留无说话人降级，并通过 failure_out 明确失败原因。

    Args:
        timeout: 单次推理的最大等待秒数，默认 600s（10 分钟）。
                 CPU 上 30 分钟音频约需 40 分钟，可按实际情况调整。
    """
    digest = None
    identity = None
    cache_path = None
    try:
        digest, identity = _diarization_cache_identity(audio_path, oracle_num)
        cache_path = _diarization_cache_path(audio_path, digest)
        cached = _load_diarization_cache(cache_path, digest)
        if cached is not None:
            if log_callback:
                await log_callback("info", "diarization", "命中 CAM++ 整段 fingerprint 缓存")
            return cached
    except OSError:
        pass

    try:
        from modelscope.pipelines import pipeline as ms_pipeline
        from modelscope.utils.constant import Tasks
    except ImportError as exc:
        reason = "modelscope 未安装，无法执行 CAM++ 说话人分离"
        if failure_out is not None:
            failure_out.update({"reason": reason, "error_type": type(exc).__name__, "retry_exhausted": False})
        if log_callback:
            await log_callback("warn", "diarization", f"{reason}，已降级为无说话人模式")
        return []

    async def _attempt() -> List[dict]:
        timeout_min = max(1, int(timeout // 60))
        if log_callback:
            await log_callback("info", "diarization", "正在加载 CAM++ 说话人分离模型（首次运行将从 ModelScope 下载权重）...")
            await log_callback(
                "info", "diarization",
                f"正在执行说话人分离，请稍候（超时限制 {timeout_min} 分钟）..."
            )
        kwargs = {}
        if oracle_num is not None:
            kwargs["oracle_num"] = oracle_num
        try:
            loop = asyncio.get_running_loop()
            diar_pipeline = await loop.run_in_executor(
                None,
                lambda: ms_pipeline(
                    task=Tasks.speaker_diarization,
                    model="iic/speech_campplus_speaker-diarization_common",
                ),
            )
            result = await asyncio.wait_for(
                loop.run_in_executor(None, lambda: diar_pipeline(audio_path, **kwargs)),
                timeout=timeout,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise RetryableExecutionError(f"CAM++ 说话人分离失败: {exc}") from exc
        raw_segments = result.get("text", [])
        return _parse_diarization_output(raw_segments)

    try:
        diar_segments = await execute_with_retry(_attempt)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        reason = f"CAM++ 说话人分离失败（首次 + 3 次尝试均未成功）: {exc}"
        if failure_out is not None:
            failure_out.update({"reason": reason, "error_type": type(exc).__name__, "retry_exhausted": True})
        if log_callback:
            await log_callback("warn", "diarization", f"{reason}，已降级为无说话人模式")
        return []

    if diar_segments and cache_path is not None and digest is not None and identity is not None:
        _atomic_write_json(cache_path, {
            "schema": _ASR_CHECKPOINT_SCHEMA,
            "digest": digest,
            "identity": identity,
            "segments": diar_segments,
        })
    elif not diar_segments and failure_out is not None:
        failure_out.update({
            "reason": "CAM++ 返回结果中没有可用说话人片段",
            "error_type": "EmptyDiarizationResult",
            "retry_exhausted": False,
        })

    speaker_ids = list({seg["speaker"] for seg in diar_segments})
    if log_callback:
        await log_callback(
            "success" if diar_segments else "warn",
            "diarization",
            (
                f"说话人分离完成，检测到 {len(speaker_ids)} 位说话人：{', '.join(sorted(speaker_ids))}"
                if diar_segments else "说话人分离完成，但未检测到可用说话人片段"
            ),
        )
    return diar_segments


def get_asr_audio_path(video_path: str) -> str:
    """ASR 提取音频的固定缓存路径，供识别和分析两步复用。"""
    return video_path.rsplit(".", 1)[0] + "_audio.wav"


async def run_asr_recognize(
    video_path: str,
    params: TaskParams,
    log_callback: Optional[Callable] = None,
    whisper_model: str = "base",
    backend: str = "whisper",
    clip_start: Optional[float] = None,
    clip_end: Optional[float] = None,
) -> ASRResult:
    """
    ASR recognition only:
    1. Extract audio from video（支持 clip_start/clip_end 裁剪区间）
    2. Run ASR（支持 backend="whisper" 或 backend="funasr"）

    若设置了 clip_start，ASR 返回的时间戳会自动加上 clip_start 偏移，
    使其与原始视频时间轴对齐。
    """
    audio_path = get_asr_audio_path(video_path)
    _clip_start = clip_start or 0.0
    _clip_end = clip_end  # None 表示到结尾
    try:
        source_fingerprint = _file_fingerprint(video_path) if os.path.exists(video_path) else None
    except OSError:
        source_fingerprint = None

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
            raise RuntimeError("音频提取失败或提取结果未通过 ffprobe 校验")

    # Step 2: Run ASR（按 backend 路由）
    if backend == "funasr":
        words = await run_funasr_asr(
            audio_path,
            log_callback=log_callback,
            source_fingerprint=source_fingerprint,
        )
    else:
        words = await run_whisper_asr(audio_path, log_callback=log_callback, whisper_model=whisper_model)

    # Step 2.1: 若使用了裁剪区间，将音频相对时间戳还原为原始视频时间轴坐标
    if _clip_start > 0 and words:
        for w in words:
            w.start += _clip_start
            w.end += _clip_start

    duration = get_video_duration(video_path) if os.path.exists(video_path) else 0.0
    if duration == 0 and words:
        duration = words[-1].end

    if log_callback:
        await log_callback(
            "success", "asr",
            f"ASR 识别完成：{len(words)} 个词，时长 {duration:.1f}s"
        )

    return ASRResult(
        words=words,
        segments=[],
        tagged_script="",
        duration=duration,
        language="zh",
        speakers=[],
        diar_segments=[],
        diar_smooth_threshold=1.0,
    )


async def run_asr_diarize(
    video_path: str,
    words: list[WordTimestamp],
    log_callback: Optional[Callable] = None,
    oracle_num: Optional[int] = None,
    clip_start: Optional[float] = None,
    duration: float = 0.0,
) -> ASRResult:
    """独立运行 CAM++，只补充说话人标签，不做文本/分段优化。"""
    audio_path = get_asr_audio_path(video_path)
    if not os.path.exists(audio_path):
        audio_path = video_path
    words = [word.model_copy(deep=True) for word in words]
    diarization_failure: dict = {}
    diar_segments = await run_diarization(
        audio_path,
        oracle_num=oracle_num,
        log_callback=log_callback,
        failure_out=diarization_failure,
    )
    if clip_start and diar_segments:
        diar_segments = [
            {**segment, "start": float(segment.get("start", 0.0)) + clip_start, "end": float(segment.get("end", 0.0)) + clip_start}
            for segment in diar_segments
        ]
    smoothed = 0
    if diar_segments:
        words = align_speakers_to_words(words, diar_segments)
        before = [(word.word, word.speaker) for word in words]
        words = smooth_word_speakers(words, min_switch_duration=1.0)
        smoothed = sum(1 for (_, speaker), word in zip(before, words) if speaker != word.speaker)
    speakers = list(set(word.speaker for word in words if word.speaker))
    if log_callback:
        await log_callback("success", "diarization", f"说话人分离完成：{len(speakers)} 位说话人，{len(diar_segments)} 个音频段")
    return ASRResult(
        words=words,
        duration=duration or (words[-1].end if words else 0.0),
        language="zh",
        speakers=speakers,
        diar_segments=diar_segments,
        diar_smooth_threshold=1.0,
        analysis_report={
            "diarization": {
                "segments": len(diar_segments),
                "speakers": len(speakers),
                "smoothed_words": smoothed,
                **({"failure": diarization_failure} if diarization_failure else {}),
            }
        },
    )


def _count_words_with_prefix(words: list[WordTimestamp], prefix: str) -> int:
    return sum(1 for w in words if w.word.startswith(prefix))


def _count_words_with_marker(words: list[WordTimestamp], marker: str) -> int:
    return sum(1 for w in words if marker in w.word)


def _build_segment_note(segment: Segment, *, silence_threshold: float, enable_diarization: bool) -> dict:
    reasons = [f"按停顿阈值 {silence_threshold}s、标点和最大时长形成字幕卡"]
    tagged_text = segment.tagged_text or ""
    if "<STU>" in tagged_text:
        reasons.append("包含结巴/重复词标记，后续审计可据此判断是否处理")
    if "<FIL" in tagged_text:
        reasons.append("包含语气词标记，已记录可删除时间边界")
    if segment.candidate_edits:
        reasons.append(f"包含 {len(segment.candidate_edits)} 个候选编辑，需审计或人工确认后才会影响导出")
    if enable_diarization and segment.speaker:
        reasons.append(f"说话人分离判定为 {segment.speaker}")
    if segment.merged:
        reasons.append("后处理合并了相邻碎片，避免字幕过短")
    if len(segment.text) > 30:
        reasons.append("文本较长，后续审计/人工审核可按语义再调整")
    return {
        "segment_id": segment.id,
        "start": segment.start,
        "end": segment.end,
        "text": segment.text,
        "reasons": reasons,
    }


def _build_rule_events(
    raw_segments: List[Segment],
    segments: List[Segment],
    candidate_edits: List[CandidateEdit],
) -> List[dict]:
    events: List[dict] = []
    for edit in candidate_edits:
        events.append({
            "type": "candidate_edit",
            "source": edit.source,
            "edit_type": edit.edit_type,
            "segment_id": edit.segment_id,
            "start": edit.start,
            "end": edit.end,
            "text": edit.text,
            "reason": edit.reason,
            "status": edit.status,
        })
    if len(raw_segments) != len(segments):
        events.append({
            "type": "postprocess_segment_count",
            "source": "postprocess",
            "input_count": len(raw_segments),
            "output_count": len(segments),
            "reason": "后处理调整了字幕卡数量，可能包含合并短碎段或按标点/说话人拆分",
        })
    for segment in segments:
        if segment.merged:
            events.append({
                "type": "segment_merged",
                "source": "postprocess",
                "segment_id": segment.id,
                "start": segment.start,
                "end": segment.end,
                "text": segment.text,
                "reason": "后处理合并了相邻碎片",
            })
    return events


async def run_asr_analyze(
    video_path: str,
    words: list[WordTimestamp],
    params: TaskParams,
    log_callback: Optional[Callable] = None,
    diar_segments: Optional[list] = None,
    diar_smooth_threshold: float = 1.0,
    duration: Optional[float] = None,
) -> ASRResult:
    """
    ASR analysis/optimization only:
    1. Glossary correction
    2. Stutter/filler tagging
    3. Tagged script generation
    4. Segment and postprocess
    """
    # 分析步骤会给词打标签/说话人，先复制一份，避免污染「仅识别」快照。
    words = [w.model_copy(deep=True) for w in words]
    recognized_word_count = len(words)
    analysis_steps: list[dict] = []

    analysis_steps.append({
        "key": "diarization",
        "title": "说话人分离",
        "description": "说话人分离已作为独立步骤执行；此处仅保留其结果参与分段。",
        "enabled": bool(diar_segments),
        "input_count": recognized_word_count,
        "output_count": len(words),
        "changed_count": 0,
        "note": f"使用 {len(diar_segments or [])} 个已有说话人段" if diar_segments else "已跳过说话人分离",
    })

    # Step 2: 词典纠错（在结巴检测之前，保证术语正确后再做后续分析）
    glossary = load_glossary()
    before_glossary = [w.word for w in words]
    glossary_candidate_edits: List[CandidateEdit] = []
    if glossary:
        words, glossary_candidate_edits = apply_glossary_with_report(words, glossary)
        if log_callback:
            await log_callback("info", "asr", f"词典纠错完成，词典共 {len(glossary)} 条术语")
    glossary_changed = sum(
        1 for before, after in zip(before_glossary, words)
        if before != after.word
    )
    analysis_steps.append({
        "key": "glossary",
        "title": "词典纠错",
        "description": "用项目词典做同长度术语替换，减少领域词误识别。",
        "enabled": bool(glossary),
        "input_count": recognized_word_count,
        "output_count": len(words),
        "changed_count": glossary_changed,
        "note": f"加载 {len(glossary)} 条术语" if glossary else "未配置词典术语",
    })

    # Step 3: Detect stutters
    if log_callback:
        await log_callback("info", "asr", "正在检测结巴和重复词...")
    words = detect_stutter(words)
    stutter_count = _count_words_with_prefix(words, "<STU>")
    stutter_candidate_edits = _extract_stutter_candidate_edits(words)
    analysis_steps.append({
        "key": "stutter",
        "title": "结巴检测",
        "description": "识别连续重复短词并打上 STU 标记。",
        "enabled": True,
        "input_count": recognized_word_count,
        "output_count": len(words),
        "changed_count": stutter_count,
        "note": f"标记 {stutter_count} 个疑似结巴/重复词",
    })

    # Step 4: Generate tagged script
    if log_callback:
        await log_callback("info", "asr", "正在生成带标签剧本...")
    filler_set = set(params.filler_words) if params.filler_words else DEFAULT_FILLER_WORDS

    # Step 4.1: 为语气词标注精确删除边界（前字.end → 后字.start）
    words = annotate_filler_boundaries(words, filler_set)
    candidate_edits = [
        *glossary_candidate_edits,
        *stutter_candidate_edits,
        *_extract_filler_candidate_edits(words),
    ]
    filler_count = _count_words_with_marker(words, "<FIL")
    analysis_steps.append({
        "key": "filler",
        "title": "语气词标注",
        "description": "根据 filler words 标记语气词，并记录前后字之间的删除边界。",
        "enabled": True,
        "input_count": recognized_word_count,
        "output_count": len(words),
        "changed_count": filler_count,
        "note": f"语气词列表 {len(filler_set)} 个，标记 {filler_count} 处",
    })

    tagged_script = transform_to_tagged_script(
        words,
        silence_threshold=params.silence_threshold,
        filler_words=filler_set
    )
    analysis_steps.append({
        "key": "tagged_script",
        "title": "带标签剧本",
        "description": "生成带 SIL、STU、FIL 标记的文本，供后续 AI 审计理解语音结构。",
        "enabled": True,
        "input_count": len(words),
        "output_count": len(tagged_script),
        "changed_count": stutter_count + filler_count,
        "note": f"生成 {len(tagged_script)} 个字符的 tagged script",
    })

    # Step 5: Segment by silence
    if log_callback:
        await log_callback("info", "asr", "正在按停顿切分片段...")
    raw_segments = segment_by_silence(
        words,
        silence_threshold=params.silence_threshold,
        min_duration=0.3,
        max_duration=MAX_SEGMENT_DURATION,
        candidate_edits=candidate_edits,
    )
    segments = postprocess_segments(raw_segments, enable_diarization=bool(diar_segments))
    segments = _attach_candidate_edits_to_segments(segments, candidate_edits)
    all_candidate_edits = [
        edit.model_copy(deep=True)
        for segment in segments
        for edit in segment.candidate_edits
    ]
    analysis_steps.append({
        "key": "segmentation",
        "title": "字幕分段与后处理",
        "description": "先按停顿/标点/最大时长切段，再合并碎片、整理句首标点，并按说话人变化拆分。",
        "enabled": True,
        "input_count": len(raw_segments),
        "output_count": len(segments),
        "changed_count": abs(len(raw_segments) - len(segments)),
        "note": f"初始 {len(raw_segments)} 段，后处理后 {len(segments)} 段",
    })

    if duration is None:
        duration = get_video_duration(video_path) if os.path.exists(video_path) else 0.0
        if duration == 0 and words:
            duration = words[-1].end

    # Extract unique speakers
    speakers = list(set(w.speaker for w in words if w.speaker))
    rule_events = _build_rule_events(raw_segments, segments, all_candidate_edits)
    analysis_report = {
        "generated_at": datetime.now().isoformat(),
        "settings": {
            "silence_threshold": params.silence_threshold,
            "enable_diarization": bool(diar_segments),
            "diar_smooth_threshold": diar_smooth_threshold,
            "glossary_terms": len(glossary),
            "filler_words": len(filler_set),
            "max_segment_duration": MAX_SEGMENT_DURATION,
        },
        "stats": {
            "recognized_words": recognized_word_count,
            "analyzed_words": len(words),
            "segments_raw": len(raw_segments),
            "segments_final": len(segments),
            "speakers": len(speakers),
            "diar_segments": len(diar_segments or []),
            "stutter_marks": stutter_count,
            "filler_marks": filler_count,
            "glossary_changes": glossary_changed,
            "candidate_edits": len(all_candidate_edits),
            "candidate_edits_by_source": {
                source: sum(1 for edit in all_candidate_edits if edit.source == source)
                for source in sorted({edit.source for edit in all_candidate_edits})
            },
            "short_segment_candidates": sum(
                1
                for segment in segments
                for edit in segment.candidate_edits
                if edit.source == "short_segment"
            ),
        },
        "steps": analysis_steps,
        "rule_events": rule_events,
        "segment_notes": [
            _build_segment_note(
                segment,
                silence_threshold=params.silence_threshold,
                enable_diarization=bool(diar_segments),
            )
            for segment in segments
        ],
    }

    if log_callback:
        await log_callback(
            "success", "asr",
            f"文本整理完成：{len(segments)} 个片段，{len(words)} 个词，时长 {duration:.1f}s"
        )

    return ASRResult(
        words=words,
        segments=segments,
        tagged_script=tagged_script,
        duration=duration,
        language="zh",
        speakers=speakers,
        diar_segments=diar_segments or [],
        diar_smooth_threshold=diar_smooth_threshold,
        analysis_report=analysis_report,
        candidate_edits=all_candidate_edits,
    )


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
    """Compatibility wrapper for automated flows that still need one ASR call."""
    recognized = await run_asr_recognize(
        video_path,
        params,
        log_callback,
        whisper_model=whisper_model,
        backend=backend,
        clip_start=clip_start,
        clip_end=clip_end,
    )
    diarized = recognized
    if params.enable_diarization:
        diarized = await run_asr_diarize(
            video_path, recognized.words, log_callback, oracle_num=oracle_num,
            clip_start=clip_start, duration=recognized.duration,
        )
    return await run_asr_analyze(
        video_path,
        diarized.words,
        params,
        log_callback,
        diar_segments=diarized.diar_segments,
        diar_smooth_threshold=diarized.diar_smooth_threshold,
        duration=diarized.duration,
    )
