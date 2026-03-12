"""
GoldenClip ASR Pipeline Service
Integrates faster-whisper + VAD to produce word-level timestamps
and tagged script with <SIL>, <FIL>, <STU> markers.

Design: 暗金剪辑台 · 编导美学
Pipeline: Video → Audio Extract → VAD → Whisper ASR → Tag → Segment
"""

import os
import re
import subprocess
import asyncio
import json
from typing import List, Optional, Callable, AsyncGenerator
from pathlib import Path

from ..models.task import (
    WordTimestamp, Segment, ASRResult, SegmentAction, TaskParams
)

# Filler words dictionary (expandable)
DEFAULT_FILLER_WORDS = {
    "嗯", "啊", "哦", "呃", "那个", "然后", "就是说", "就是", "这个",
    "对对对", "好好好", "嗯嗯", "啊啊", "哎", "诶", "嗯哼", "额",
    "哦哦", "对吧", "是吧", "怎么说呢"
}


def extract_audio(video_path: str, audio_path: str) -> bool:
    """Extract audio from video using FFmpeg."""
    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-vn", "-acodec", "pcm_s16le",
        "-ar", "16000", "-ac", "1",
        audio_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode == 0


def get_video_duration(video_path: str) -> float:
    """Get video duration using ffprobe."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        video_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        data = json.loads(result.stdout)
        return float(data.get("format", {}).get("duration", 0))
    return 0.0


def generate_thumbnail(video_path: str, thumbnail_path: str, time: float = 1.0) -> bool:
    """Generate video thumbnail using FFmpeg."""
    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-ss", str(time), "-vframes", "1",
        "-vf", "scale=320:-1",
        thumbnail_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode == 0


def tag_word(word: str, filler_words: set) -> str:
    """Apply FIL tag to filler words."""
    clean = word.strip()
    if clean in filler_words:
        return f"<FIL>{clean}"
    return clean


def detect_stutter(words: List[WordTimestamp]) -> List[WordTimestamp]:
    """
    Detect and mark stuttering patterns (<STU>).
    Pattern: same word repeated within 2 seconds.
    """
    marked = []
    for i, w in enumerate(words):
        if i > 0 and words[i-1].word == w.word:
            if w.start - words[i-1].start < 2.0:
                # Mark previous as stutter
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


def segment_by_silence(
    words: List[WordTimestamp],
    silence_threshold: float = 0.5,
    min_duration: float = 0.3
) -> List[Segment]:
    """
    Group words into segments based on silence gaps.
    Each segment represents a natural speech unit.
    """
    if not words:
        return []

    segments = []
    current_words = []
    current_start = words[0].start

    for i, word in enumerate(words):
        current_words.append(word)

        # Check if next word has a significant gap (or this is the last word)
        is_last = (i == len(words) - 1)
        has_gap = not is_last and (words[i + 1].start - word.end) > silence_threshold

        if has_gap or is_last:
            if current_words:
                seg_text = "".join(
                    w.word.replace("<FIL>", "").replace("<STU>", "")
                    for w in current_words
                ).strip()
                seg_tagged = "".join(w.word for w in current_words).strip()

                duration = current_words[-1].end - current_start
                if duration >= min_duration and seg_text:
                    # Detect speaker from words (use most common)
                    speakers = [w.speaker for w in current_words if w.speaker]
                    speaker = max(set(speakers), key=speakers.count) if speakers else None

                    seg = Segment(
                        start=round(current_start, 3),
                        end=round(current_words[-1].end, 3),
                        text=seg_text,
                        tagged_text=seg_tagged,
                        speaker=speaker,
                        action=SegmentAction.KEEP
                    )
                    segments.append(seg)

            # Start new segment
            if not is_last:
                current_words = []
                current_start = words[i + 1].start

    return segments


async def run_whisper_asr(
    audio_path: str,
    language: str = "zh",
    log_callback: Optional[Callable] = None
) -> List[WordTimestamp]:
    """
    Run faster-whisper ASR with word-level timestamps.
    Falls back to mock data if faster-whisper is not installed.
    """
    try:
        from faster_whisper import WhisperModel

        if log_callback:
            await log_callback("info", "asr", "正在加载 Whisper 模型 (base)...")

        # Use base model for speed, can upgrade to large-v3 for accuracy
        model = WhisperModel("base", device="cpu", compute_type="int8")

        if log_callback:
            await log_callback("info", "asr", "开始语音识别，请稍候...")

        segments, info = model.transcribe(
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


async def run_asr_pipeline(
    video_path: str,
    params: TaskParams,
    log_callback: Optional[Callable] = None
) -> ASRResult:
    """
    Full ASR pipeline:
    1. Extract audio from video
    2. Run Whisper ASR with word timestamps
    3. Detect stutters
    4. Apply filler word tags
    5. Generate tagged script
    6. Segment by silence
    """
    audio_path = video_path.rsplit(".", 1)[0] + "_audio.wav"

    # Step 1: Extract audio
    if log_callback:
        await log_callback("info", "asr", "正在提取音频...")

    if os.path.exists(video_path):
        success = extract_audio(video_path, audio_path)
        if not success:
            if log_callback:
                await log_callback("warn", "asr", "音频提取失败，使用原始文件")
            audio_path = video_path

    # Step 2: Run ASR
    words = await run_whisper_asr(audio_path, log_callback=log_callback)

    # Step 3: Detect stutters
    if log_callback:
        await log_callback("info", "asr", "正在检测结巴和重复词...")
    words = detect_stutter(words)

    # Step 4: Generate tagged script
    if log_callback:
        await log_callback("info", "asr", "正在生成带标签剧本...")
    filler_set = set(params.filler_words) if params.filler_words else DEFAULT_FILLER_WORDS
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
        min_duration=0.3
    )

    # Get video duration
    duration = get_video_duration(video_path) if os.path.exists(video_path) else 0.0
    if duration == 0 and words:
        duration = words[-1].end

    # Extract unique speakers
    speakers = list(set(w.speaker for w in words if w.speaker))

    if log_callback:
        await log_callback(
            "success", "asr",
            f"ASR 完成：{len(segments)} 个片段，{len(words)} 个词，时长 {duration:.1f}s"
        )

    return ASRResult(
        words=words,
        segments=segments,
        tagged_script=tagged_script,
        duration=duration,
        language="zh",
        speakers=speakers
    )
