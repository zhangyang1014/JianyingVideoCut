"""
Subprocess helpers shared across backend services.
"""

from __future__ import annotations

import subprocess
from typing import Any, Sequence


def run_text_subprocess(cmd: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    """
    Run a subprocess and always decode stdout/stderr as UTF-8 with replacement.

    This avoids platform-default decoding issues on Windows where `text=True`
    would otherwise use the active code page (often GBK), which can crash when
    FFmpeg/ffprobe emit UTF-8 output.
    """
    return subprocess.run(
        cmd,
        text=True,
        encoding="utf-8",
        errors="replace",
        **kwargs,
    )
