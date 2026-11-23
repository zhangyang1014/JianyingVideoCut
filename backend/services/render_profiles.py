"""主任务 FFmpeg 导出的档位配置。

档位只调整编码成本和预览用途；不改变审核片段、输出时间轴或源帧率。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..models.task import RenderProfile


@dataclass(frozen=True)
class RenderProfileSettings:
    max_long_edge: Optional[int]
    preset: str
    crf: int
    force_disable_subtitles: bool = False


_SETTINGS = {
    RenderProfile.DRAFT: RenderProfileSettings(
        max_long_edge=854,
        preset="ultrafast",
        crf=30,
        force_disable_subtitles=True,
    ),
    RenderProfile.PREVIEW: RenderProfileSettings(
        max_long_edge=1280,
        preset="veryfast",
        crf=24,
    ),
    RenderProfile.FINAL: RenderProfileSettings(
        max_long_edge=None,
        preset="fast",
        crf=18,
    ),
}


def get_render_profile_settings(profile: RenderProfile) -> RenderProfileSettings:
    """返回指定档位的不可变渲染配置。"""
    return _SETTINGS[profile]


def build_scale_filter(max_long_edge: Optional[int]) -> Optional[str]:
    """按最长边等比缩放，保持原始方向和显示比例。"""
    if not max_long_edge:
        return None
    return (
        f"scale='if(gte(iw,ih),min(iw,{max_long_edge}),-2)':"
        f"'if(gte(iw,ih),-2,min(ih,{max_long_edge}))'"
    )
