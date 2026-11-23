"""渲染后基础媒体校验。

首版只验证可播放性和时间轴，不根据 warning 自动修改剪辑决定。
"""

from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..models.task import RenderQualityIssue, RenderQualityReport
from .subprocess_utils import run_text_subprocess


def validate_rendered_media(
    output_path: str,
    expected_duration: Optional[float] = None,
) -> RenderQualityReport:
    """用 ffprobe 校验导出文件是否可读、含音视频流且时长符合预期。"""
    path = Path(output_path)
    issues: list[RenderQualityIssue] = []
    if not path.exists() or path.stat().st_size == 0:
        return RenderQualityReport(
            passed=False,
            expected_duration=expected_duration,
            issues=[RenderQualityIssue(
                code="RQ-01",
                severity="error",
                message="导出文件不存在或为空",
            )],
            checked_at=datetime.now().isoformat(),
        )

    result = run_text_subprocess(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_streams",
            "-show_format",
            str(path),
        ],
        capture_output=True,
    )
    if result.returncode != 0:
        return RenderQualityReport(
            passed=False,
            expected_duration=expected_duration,
            issues=[RenderQualityIssue(
                code="RQ-02",
                severity="error",
                message="ffprobe 无法读取导出文件",
            )],
            checked_at=datetime.now().isoformat(),
        )

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return RenderQualityReport(
            passed=False,
            expected_duration=expected_duration,
            issues=[RenderQualityIssue(
                code="RQ-03",
                severity="error",
                message="ffprobe 未返回有效媒体信息",
            )],
            checked_at=datetime.now().isoformat(),
        )

    streams = payload.get("streams", [])
    has_video = any(stream.get("codec_type") == "video" for stream in streams)
    has_audio = any(stream.get("codec_type") == "audio" for stream in streams)
    if not has_video:
        issues.append(RenderQualityIssue(
            code="RQ-04",
            severity="error",
            message="导出文件缺少视频流",
        ))
    if not has_audio:
        issues.append(RenderQualityIssue(
            code="RQ-05",
            severity="error",
            message="导出文件缺少音频流",
        ))

    actual_duration: Optional[float] = None
    try:
        actual_duration = float(payload.get("format", {}).get("duration"))
        if not math.isfinite(actual_duration) or actual_duration <= 0:
            raise ValueError("invalid duration")
    except (TypeError, ValueError):
        actual_duration = None
        issues.append(RenderQualityIssue(
            code="RQ-06",
            severity="error",
            message="无法读取导出文件时长",
        ))

    if actual_duration is not None and expected_duration is not None:
        tolerance = max(0.5, expected_duration * 0.01)
        if abs(actual_duration - expected_duration) > tolerance:
            issues.append(RenderQualityIssue(
                code="RQ-07",
                severity="error",
                message=(
                    f"导出时长 {actual_duration:.2f}s 与预期 "
                    f"{expected_duration:.2f}s 偏差超过 {tolerance:.2f}s"
                ),
            ))

    return RenderQualityReport(
        passed=not any(issue.severity == "error" for issue in issues),
        expected_duration=expected_duration,
        actual_duration=actual_duration,
        video_stream_present=has_video,
        audio_stream_present=has_audio,
        issues=issues,
        checked_at=datetime.now().isoformat(),
    )
