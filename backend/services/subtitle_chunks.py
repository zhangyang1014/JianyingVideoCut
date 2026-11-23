"""把成片 SRT 投影到每个连续块，字幕随块只编码一次。"""

import re


def _milliseconds(value: str) -> int:
    hours, minutes, seconds, millis = map(int, re.split(r"[:,.]", value))
    if minutes >= 60 or seconds >= 60:
        raise ValueError("字幕时间格式无效")
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + millis


def _timestamp(value: int) -> str:
    seconds, millis = divmod(value, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02},{millis:03}"


def parse_srt(content: str) -> list[tuple[int, int, str]]:
    cues = []
    for block in re.split(r"\n\s*\n", content.lstrip("\ufeff").replace("\r\n", "\n").strip()):
        if not block.strip():
            continue
        lines = block.splitlines()
        if lines and lines[0].strip().isdigit():
            lines = lines[1:]
        match = re.fullmatch(r"\s*(\d{2,}:\d{2}:\d{2}[,.]\d{3})\s+-->\s+(\d{2,}:\d{2}:\d{2}[,.]\d{3})\s*", lines[0]) if lines else None
        if not match or len(lines) < 2:
            raise ValueError("字幕 SRT 格式无效，未生成导出文件")
        start, end = (_milliseconds(value) for value in match.groups())
        if end <= start:
            raise ValueError("字幕结束时间必须晚于开始时间")
        cues.append((start, end, "\n".join(lines[1:])))
    return sorted(cues, key=lambda cue: (cue[0], cue[1]))


def chunk_srt(cues: list[tuple[int, int, str]], start: float, end: float) -> str:
    start_ms, end_ms = round(start * 1000), round(end * 1000)
    result = []
    for cue_start, cue_end, text in cues:
        if cue_start >= end_ms:
            break
        left, right = max(start_ms, cue_start), min(end_ms, cue_end)
        if right > left:
            result.append(f"{len(result) + 1}\n{_timestamp(left - start_ms)} --> {_timestamp(right - start_ms)}\n{text}\n")
    return "\n".join(result)
