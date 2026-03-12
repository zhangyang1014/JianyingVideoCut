"""
GoldenClip FFmpeg Executor
Handles lossless video cutting and concatenation.

Design: 暗金剪辑台 · 编导美学
Strategy: -c copy for lossless cuts, concat demuxer for joining
Formula: Target_Start_n = Σ Duration_i (i=1 to n-1)
"""

import os
import subprocess
import asyncio
import tempfile
from typing import List, Optional, Callable
from pathlib import Path

from ..models.task import Segment, SegmentAction

EXPORTS_DIR = Path(__file__).parent.parent.parent / "exports"


def _run_ffmpeg(cmd: List[str], log_callback=None) -> tuple[bool, str]:
    """Run FFmpeg command and return (success, output)."""
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    stdout, stderr = process.communicate()
    success = process.returncode == 0
    output = stderr if stderr else stdout
    return success, output


async def export_ffmpeg_lossless(
    video_path: str,
    segments: List[Segment],
    output_path: str,
    log_callback: Optional[Callable] = None
) -> bool:
    """
    Export video using FFmpeg lossless cut + concat.
    
    Steps:
    1. For each kept segment, cut with -c copy (lossless)
    2. Create concat list file
    3. Join all segments with concat demuxer
    
    Time alignment formula:
    Target_Start_n = Σ Duration_i (i=1 to n-1)
    """
    kept_segments = [s for s in segments if s.action == SegmentAction.KEEP]

    if not kept_segments:
        if log_callback:
            await log_callback("error", "ffmpeg", "没有需要保留的片段")
        return False

    if log_callback:
        await log_callback("info", "ffmpeg", f"开始导出 {len(kept_segments)} 个片段...")

    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix="goldenclip_"))
    segment_files = []

    try:
        total = len(kept_segments)
        for i, seg in enumerate(kept_segments):
            seg_path = temp_dir / f"seg_{i:04d}.mp4"
            duration = seg.end - seg.start

            if log_callback:
                progress = (i + 1) / total
                await log_callback(
                    "info", "ffmpeg",
                    f"切割片段 {i+1}/{total}: {seg.start:.2f}s → {seg.end:.2f}s",
                    progress
                )

            cmd = [
                "ffmpeg", "-y",
                "-i", video_path,
                "-ss", str(seg.start),
                "-t", str(duration),
                "-c", "copy",           # Lossless copy
                "-avoid_negative_ts", "make_zero",
                str(seg_path)
            ]

            success, output = _run_ffmpeg(cmd)
            if not success:
                if log_callback:
                    await log_callback("warn", "ffmpeg", f"片段 {i+1} 切割失败，跳过: {output[:200]}")
                continue

            segment_files.append(seg_path)

        if not segment_files:
            if log_callback:
                await log_callback("error", "ffmpeg", "所有片段切割失败")
            return False

        # Create concat list
        concat_file = temp_dir / "concat.txt"
        with open(concat_file, "w", encoding="utf-8") as f:
            for seg_path in segment_files:
                f.write(f"file '{seg_path}'\n")

        if log_callback:
            await log_callback("info", "ffmpeg", f"正在拼接 {len(segment_files)} 个片段...")

        # Concatenate all segments
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_file),
            "-c", "copy",
            output_path
        ]

        success, output = _run_ffmpeg(cmd)

        if success:
            if log_callback:
                await log_callback("success", "ffmpeg", f"导出完成: {output_path}")
            return True
        else:
            if log_callback:
                await log_callback("error", "ffmpeg", f"拼接失败: {output[:300]}")
            return False

    finally:
        # Cleanup temp files
        import shutil
        try:
            shutil.rmtree(temp_dir)
        except:
            pass


def get_video_info(video_path: str) -> dict:
    """Get video metadata using ffprobe."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_streams", "-show_format",
        video_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
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
