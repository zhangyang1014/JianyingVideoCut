"""
GoldenClip JianYing Draft Builder
Generates JianyingPro draft files from audit segments.

Design: 暗金剪辑台 · 编导美学
Library: pyJianYingDraft (pip install pyJianYingDraft)
Formula: Target_Start_n = Σ Duration_i (i=1 to n-1)

Note: pyJianYingDraft supports JianYing 5+ for draft generation.
      Auto-export requires JianYing 6 or below (Windows only).
"""

import os
import json
from typing import List, Optional, Callable
from pathlib import Path
from datetime import datetime

from ..models.task import Segment, SegmentAction

EXPORTS_DIR = Path(__file__).parent.parent.parent / "exports"


async def build_jianying_draft(
    video_path: str,
    segments: List[Segment],
    draft_name: str,
    draft_folder: Optional[str] = None,
    log_callback: Optional[Callable] = None
) -> Optional[str]:
    """
    Build a JianyingPro draft file from kept segments.
    
    Time alignment:
    Target_Start_n = Σ Duration_i (i=1 to n-1)
    
    Returns the path to the created draft folder.
    """
    kept_segments = [s for s in segments if s.action == SegmentAction.KEEP]

    if not kept_segments:
        if log_callback:
            await log_callback("error", "jianying", "没有需要保留的片段")
        return None

    if log_callback:
        await log_callback("info", "jianying", f"开始生成剪映草稿: {draft_name}")

    try:
        import pyJianYingDraft as draft
        from pyJianYingDraft import trange, SEC

        # Determine draft folder
        if draft_folder and os.path.exists(draft_folder):
            draft_dir = draft.DraftFolder(draft_folder)
            script = draft_dir.create_draft(draft_name, 1920, 1080)
        else:
            # Create in exports directory
            EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
            output_dir = EXPORTS_DIR / draft_name
            output_dir.mkdir(parents=True, exist_ok=True)
            script = draft.ScriptFile.create(1920, 1080)

        # Add main video track
        script.add_track(draft.TrackType.video, "main")

        # Calculate cumulative timeline position
        # Target_Start_n = Σ Duration_i (i=1 to n-1)
        timeline_cursor = 0  # microseconds

        if log_callback:
            await log_callback("info", "jianying", f"正在添加 {len(kept_segments)} 个片段到时间轴...")

        for i, seg in enumerate(kept_segments):
            duration_sec = seg.end - seg.start
            duration_us = int(duration_sec * 1_000_000)  # Convert to microseconds

            if log_callback:
                progress = (i + 1) / len(kept_segments)
                await log_callback(
                    "info", "jianying",
                    f"添加片段 {i+1}/{len(kept_segments)}: {seg.start:.2f}s → {seg.end:.2f}s",
                    progress
                )

            # Create video segment
            # target_timerange: where it sits on the timeline
            # source_timerange: which part of the source video to use
            video_seg = draft.VideoSegment(
                video_path,
                target_timerange=draft.Timerange(timeline_cursor, duration_us),
                source_timerange=draft.Timerange(
                    int(seg.start * 1_000_000),
                    duration_us
                )
            )

            script.add_segment(video_seg, "main")

            # Add subtitle if text available
            if seg.text and len(seg.text.strip()) > 0:
                try:
                    script.add_track(draft.TrackType.text, f"subtitle_{i}")
                    text_seg = draft.TextSegment(
                        seg.text.strip(),
                        target_timerange=draft.Timerange(timeline_cursor, duration_us),
                    )
                    script.add_segment(text_seg, f"subtitle_{i}")
                except:
                    pass  # Subtitle is optional

            # Advance timeline cursor
            timeline_cursor += duration_us

        # Save draft
        if draft_folder and os.path.exists(draft_folder):
            script.save()
            draft_path = os.path.join(draft_folder, draft_name)
        else:
            draft_path = str(EXPORTS_DIR / draft_name / "draft_content.json")
            script.dump(draft_path)

        if log_callback:
            await log_callback("success", "jianying", f"剪映草稿生成完成: {draft_path}")

        return draft_path

    except ImportError:
        if log_callback:
            await log_callback("warn", "jianying", "pyJianYingDraft 未安装，生成 JSON 草稿文件")
        return await _build_json_draft(video_path, kept_segments, draft_name, log_callback)
    except Exception as e:
        if log_callback:
            await log_callback("error", "jianying", f"剪映草稿生成失败: {str(e)}")
        return await _build_json_draft(video_path, kept_segments, draft_name, log_callback)


async def _build_json_draft(
    video_path: str,
    kept_segments: List[Segment],
    draft_name: str,
    log_callback: Optional[Callable] = None
) -> Optional[str]:
    """
    Fallback: Build a JSON draft file compatible with pyJianYingDraft format.
    This can be manually imported or used with pyJianYingDraft later.
    """
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    output_dir = EXPORTS_DIR / draft_name
    output_dir.mkdir(parents=True, exist_ok=True)

    # Build timeline with cumulative offsets
    # Target_Start_n = Σ Duration_i (i=1 to n-1)
    timeline_cursor = 0  # microseconds
    timeline_segments = []

    for i, seg in enumerate(kept_segments):
        duration_sec = seg.end - seg.start
        duration_us = int(duration_sec * 1_000_000)

        timeline_segments.append({
            "index": i,
            "source_start": seg.start,
            "source_end": seg.end,
            "source_duration": duration_sec,
            "target_start_us": timeline_cursor,
            "target_duration_us": duration_us,
            "text": seg.text,
            "speaker": seg.speaker,
            "reason": seg.reason,
            "rule": seg.rule.value if seg.rule else None,
        })

        timeline_cursor += duration_us

    draft_data = {
        "goldenclip_version": "3.0",
        "created_at": datetime.now().isoformat(),
        "draft_name": draft_name,
        "source_video": video_path,
        "total_duration_us": timeline_cursor,
        "total_duration_sec": timeline_cursor / 1_000_000,
        "segments_count": len(kept_segments),
        "timeline": timeline_segments,
        "metadata": {
            "note": "此文件由 GoldenClip 生成，可配合 pyJianYingDraft 导入剪映",
            "pyJianYingDraft_url": "https://github.com/GuanYixuan/pyJianYingDraft",
            "formula": "Target_Start_n = Σ Duration_i (i=1 to n-1)"
        }
    }

    # Save audit JSON
    audit_path = output_dir / "goldenclip_draft.json"
    with open(audit_path, "w", encoding="utf-8") as f:
        json.dump(draft_data, f, ensure_ascii=False, indent=2)

    # Also save FFmpeg concat script
    concat_script = output_dir / "ffmpeg_concat.sh"
    with open(concat_script, "w", encoding="utf-8") as f:
        f.write("#!/bin/bash\n")
        f.write("# GoldenClip FFmpeg 拼接脚本\n")
        f.write(f"# 源视频: {video_path}\n\n")
        f.write("# 创建临时目录\n")
        f.write("mkdir -p /tmp/goldenclip_segs\n\n")
        f.write("# 切割各片段\n")
        for i, seg in enumerate(kept_segments):
            duration = seg["source_duration"] if isinstance(seg, dict) else seg.end - seg.start
            start = seg["source_start"] if isinstance(seg, dict) else seg.start
            f.write(f'ffmpeg -y -i "{video_path}" -ss {start:.3f} -t {duration:.3f} -c copy /tmp/goldenclip_segs/seg_{i:04d}.mp4\n')

        f.write("\n# 生成拼接列表\n")
        f.write("echo '' > /tmp/goldenclip_concat.txt\n")
        for i in range(len(kept_segments)):
            f.write(f"echo \"file '/tmp/goldenclip_segs/seg_{i:04d}.mp4'\" >> /tmp/goldenclip_concat.txt\n")

        f.write("\n# 拼接\n")
        f.write(f'ffmpeg -y -f concat -safe 0 -i /tmp/goldenclip_concat.txt -c copy "{output_dir}/{draft_name}_output.mp4"\n')

    if log_callback:
        await log_callback("success", "jianying", f"JSON 草稿已生成: {audit_path}")

    return str(audit_path)
