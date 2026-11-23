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
import re as _re
import shutil
import threading
import uuid
from typing import List, Optional, Callable
from pathlib import Path
from datetime import datetime

from ..models.task import Segment, SegmentAction, TaskParams
from .subtitle_text import get_effective_subtitle_text

EXPORTS_DIR = Path(__file__).parent.parent.parent / "exports"
_draft_claims: set[str] = set()
_draft_claims_lock = threading.Lock()


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(f"{path.name}.part")
    with open(part, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(part, path)


def _validate_staged_draft(staged_dir: Path) -> None:
    if not staged_dir.is_dir():
        raise RuntimeError("剪映草稿 staging 目录不存在")
    json_files = list(staged_dir.rglob("*.json"))
    if not json_files:
        raise RuntimeError("剪映草稿未生成 JSON 文件")
    for json_path in json_files:
        if json_path.stat().st_size <= 0:
            raise RuntimeError(f"剪映草稿文件为空: {json_path.name}")
        with open(json_path, "r", encoding="utf-8") as handle:
            json.load(handle)


def _publish_staged_directory(staged_dir: Path, final_dir: Path) -> None:
    """同文件系统 rename 发布；同名旧目录先整体移走，绝不混写内容。"""
    backup_dir = final_dir.with_name(f".{final_dir.name}.backup-{uuid.uuid4().hex}")
    moved_old = False
    published = False
    try:
        if final_dir.exists():
            os.rename(final_dir, backup_dir)
            moved_old = True
        os.rename(staged_dir, final_dir)
        published = True
    except Exception:
        if moved_old and backup_dir.exists() and not final_dir.exists():
            os.rename(backup_dir, final_dir)
        raise
    finally:
        if published and backup_dir.exists():
            shutil.rmtree(backup_dir, ignore_errors=True)


def _get_subtitle_text(seg: Segment, params: Optional[TaskParams] = None) -> str:
    """
    计算段落最终字幕文本：
    1. subtitle_fix / text_fix：使用 display_text（修正后文本）
    2. show_speaker_label=True：在文本前追加 [说话人] 前缀
    """
    return get_effective_subtitle_text(seg, params)


def _find_line_break_time(seg: Segment) -> Optional[float]:
    """
    根据 subtitle_line_break 字段，在 words 中找到分行起始时间。
    返回 None 表示未找到或未设置。
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


async def build_jianying_draft(
    video_path: str,
    segments: List[Segment],
    draft_name: str,
    draft_folder: Optional[str] = None,
    log_callback: Optional[Callable] = None,
    params: Optional[TaskParams] = None,
) -> Optional[str]:
    """
    Build a JianyingPro draft file from kept segments.
    
    Time alignment:
    Target_Start_n = Σ Duration_i (i=1 to n-1)
    
    Returns the path to the created draft folder.
    """
    # subtitle_fix / text_fix / merge_next 视频不剪切，与 keep 相同处理
    _kept_actions = (SegmentAction.KEEP, SegmentAction.SUBTITLE_FIX, SegmentAction.TEXT_FIX, SegmentAction.MERGE_NEXT)
    kept_segments = [s for s in segments if s.action in _kept_actions]

    if not kept_segments:
        if log_callback:
            await log_callback("error", "jianying", "没有需要保留的片段")
        return None

    if log_callback:
        await log_callback("info", "jianying", f"开始生成剪映草稿: {draft_name}")

    final_root = Path(draft_folder) if draft_folder and Path(draft_folder).is_dir() else EXPORTS_DIR
    final_root.mkdir(parents=True, exist_ok=True)
    final_dir = final_root / draft_name
    draft_claim = str(final_dir.resolve())
    with _draft_claims_lock:
        claim_conflict = draft_claim in _draft_claims
        if not claim_conflict:
            _draft_claims.add(draft_claim)
    if claim_conflict:
        if log_callback:
            await log_callback("error", "jianying", "相同剪映草稿目标正在构建，拒绝并发覆盖")
        return None
    staging_root = final_root / f".{draft_name}.staging-{uuid.uuid4().hex}"
    staged_dir = staging_root / draft_name

    try:
        staging_root.mkdir(parents=True, exist_ok=False)
        try:
            import pyJianYingDraft as draft

            if draft_folder and Path(draft_folder).is_dir():
                draft_dir = draft.DraftFolder(str(staging_root))
                script = draft_dir.create_draft(draft_name, 1920, 1080)
            else:
                staged_dir.mkdir(parents=True, exist_ok=False)
                script = draft.ScriptFile.create(1920, 1080)

            script.add_track(draft.TrackType.video, "main")
            timeline_cursor = 0
            if log_callback:
                await log_callback("info", "jianying", f"正在添加 {len(kept_segments)} 个片段到时间轴...")

            for i, seg in enumerate(kept_segments):
                duration_us = int((seg.end - seg.start) * 1_000_000)
                if log_callback:
                    await log_callback(
                        "info", "jianying",
                        f"添加片段 {i+1}/{len(kept_segments)}: {seg.start:.2f}s → {seg.end:.2f}s",
                        (i + 1) / len(kept_segments),
                    )
                video_seg = draft.VideoSegment(
                    video_path,
                    target_timerange=draft.Timerange(timeline_cursor, duration_us),
                    source_timerange=draft.Timerange(int(seg.start * 1_000_000), duration_us),
                )
                script.add_segment(video_seg, "main")

                subtitle_text = _get_subtitle_text(seg, params)
                if subtitle_text:
                    try:
                        line_break_time = _find_line_break_time(seg)
                        if line_break_time is not None and seg.start < line_break_time < seg.end:
                            lb_offset_us = int((line_break_time - seg.start) * 1_000_000)
                            text_part1, text_part2 = _split_text_at_break(seg, line_break_time)
                            script.add_track(draft.TrackType.text, f"subtitle_{i}_a")
                            script.add_segment(
                                draft.TextSegment(
                                    text_part1 or subtitle_text,
                                    target_timerange=draft.Timerange(timeline_cursor, lb_offset_us),
                                ),
                                f"subtitle_{i}_a",
                            )
                            script.add_track(draft.TrackType.text, f"subtitle_{i}_b")
                            script.add_segment(
                                draft.TextSegment(
                                    text_part2 or "",
                                    target_timerange=draft.Timerange(
                                        timeline_cursor + lb_offset_us,
                                        duration_us - lb_offset_us,
                                    ),
                                ),
                                f"subtitle_{i}_b",
                            )
                        else:
                            script.add_track(draft.TrackType.text, f"subtitle_{i}")
                            script.add_segment(
                                draft.TextSegment(
                                    subtitle_text,
                                    target_timerange=draft.Timerange(timeline_cursor, duration_us),
                                ),
                                f"subtitle_{i}",
                            )
                    except Exception:
                        pass
                timeline_cursor += duration_us

            if draft_folder and Path(draft_folder).is_dir():
                script.save()
            else:
                script.dump(str(staged_dir / "draft_content.json"))
            _write_json_atomic(
                staged_dir / "goldenclip_build.json",
                {"builder": "pyJianYingDraft", "completed_at": datetime.now().isoformat()},
            )
            _validate_staged_draft(staged_dir)
            _publish_staged_directory(staged_dir, final_dir)
            draft_path = str(final_dir if draft_folder else final_dir / "draft_content.json")
            if log_callback:
                await log_callback("success", "jianying", f"pyJianYingDraft 草稿原子发布成功: {draft_path}")
            return draft_path
        except Exception as py_error:
            shutil.rmtree(staging_root, ignore_errors=True)
            staging_root.mkdir(parents=True, exist_ok=False)
            staged_dir.mkdir(parents=True, exist_ok=False)
            if log_callback:
                await log_callback(
                    "warn",
                    "jianying",
                    f"pyJianYingDraft 构建未完成，改用 fallback JSON: {py_error}",
                )
            audit_path = await _build_json_draft(
                video_path,
                kept_segments,
                draft_name,
                log_callback,
                params,
                output_dir=staged_dir,
            )
            _write_json_atomic(
                staged_dir / "goldenclip_build.json",
                {"builder": "fallback_json", "completed_at": datetime.now().isoformat()},
            )
            _validate_staged_draft(staged_dir)
            _publish_staged_directory(staged_dir, final_dir)
            final_audit_path = final_dir / Path(audit_path).name
            if log_callback:
                await log_callback("success", "jianying", f"fallback JSON 草稿原子发布成功: {final_audit_path}")
            return str(final_audit_path)
    except Exception as error:
        if log_callback:
            await log_callback("error", "jianying", f"剪映草稿生成失败: {error}")
        return None
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)
        with _draft_claims_lock:
            _draft_claims.discard(draft_claim)


def _split_text_at_break(seg: Segment, line_break_time: float):
    """
    按分行时间把 seg.text 拆成两部分字符串。
    以 words 中时间 < line_break_time 的字符归第一张卡，其余归第二张卡。
    """
    if not seg.words:
        # 无 words 信息：按时间比例估算字符切点
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


async def _build_json_draft(
    video_path: str,
    kept_segments: List[Segment],
    draft_name: str,
    log_callback: Optional[Callable] = None,
    params: Optional[TaskParams] = None,
    output_dir: Optional[Path] = None,
) -> str:
    """
    Fallback: Build a JSON draft file compatible with pyJianYingDraft format.
    This can be manually imported or used with pyJianYingDraft later.
    """
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    output_dir = output_dir or (EXPORTS_DIR / draft_name)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Build timeline with cumulative offsets
    # Target_Start_n = Σ Duration_i (i=1 to n-1)
    timeline_cursor = 0  # microseconds
    timeline_segments = []

    for i, seg in enumerate(kept_segments):
        duration_sec = seg.end - seg.start
        duration_us = int(duration_sec * 1_000_000)

        final_text = _get_subtitle_text(seg, params)
        line_break_time = _find_line_break_time(seg)
        line_break_texts = None
        if line_break_time is not None and seg.start < line_break_time < seg.end:
            t1, t2 = _split_text_at_break(seg, line_break_time)
            line_break_texts = {"part1": t1, "part2": t2, "break_at_sec": round(line_break_time, 3)}

        timeline_segments.append({
            "index": i,
            "source_start": seg.start,
            "source_end": seg.end,
            "source_duration": duration_sec,
            "target_start_us": timeline_cursor,
            "target_duration_us": duration_us,
            "text": final_text,
            "original_text": seg.text,
            "display_text": seg.display_text,
            "fix_type": seg.fix_type,
            "subtitle_fixed": bool(seg.display_text),
            "subtitle_line_break": line_break_texts,
            "speaker": seg.speaker,
            "reason": seg.reason,
            "rule": seg.rule.value if seg.rule else None,
            "action": seg.action.value if seg.action else None,
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
    _write_json_atomic(audit_path, draft_data)

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
        await log_callback("info", "jianying", f"fallback JSON staging 已生成: {audit_path}")

    return str(audit_path)
