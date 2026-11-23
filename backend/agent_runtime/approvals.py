"""Human approval checks for high-risk Agent tools."""

from __future__ import annotations

from typing import Optional

from .registry import get_tool_registry


class ApprovalRequired(PermissionError):
    pass


def assert_human_approval(tool_name: str, approval_token: Optional[str] = None) -> None:
    """Block high-risk tools unless the caller provides an approval token."""
    registry = get_tool_registry()
    if registry.requires_human_approval(tool_name) and not approval_token:
        raise ApprovalRequired(f"工具 {tool_name} 需要人工确认后才能执行")

