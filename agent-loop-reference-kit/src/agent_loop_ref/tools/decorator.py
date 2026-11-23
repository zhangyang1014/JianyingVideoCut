"""Convenience decorator for creating FunctionTool instances."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .base import ApprovalMode, FunctionTool


def tool(
    func: Callable[..., Any] | None = None,
    *,
    name: str | None = None,
    description: str | None = None,
    approval_mode: ApprovalMode = ApprovalMode.NEVER,
):
    def wrap(inner: Callable[..., Any]) -> FunctionTool:
        return FunctionTool(
            inner,
            name=name,
            description=description,
            approval_mode=approval_mode,
        )

    if func is None:
        return wrap
    return wrap(func)

