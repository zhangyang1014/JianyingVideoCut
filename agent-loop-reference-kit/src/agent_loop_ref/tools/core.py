"""Core deterministic tools for autonomous loops."""

from __future__ import annotations

from typing import Any

from ..core.types import ToolResult
from .base import BaseTool


class ThinkTool(BaseTool):
    def __init__(self) -> None:
        super().__init__(
            name="think",
            description=(
                "Record reasoning before acting. Use this to analyze tool results, "
                "plan next steps, or handle risks."
            ),
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"thought": {"type": "string"}},
            "required": ["thought"],
        }

    async def execute(self, parameters: dict[str, Any]) -> ToolResult:
        thought = parameters["thought"]
        return ToolResult(
            success=True,
            result=f"Reasoning recorded: {thought[:200]}",
            metadata={"thought_length": len(thought)},
        )


class TaskStatusTool(BaseTool):
    def __init__(self) -> None:
        super().__init__(
            name="task_status",
            description=(
                "Declare whether the task is complete or incomplete, with evidence. "
                "Use this before ending an autonomous task."
            ),
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["complete", "incomplete"]},
                "rationale": {"type": "string"},
                "requirements_met": {"type": "array"},
                "requirements_pending": {"type": "array"},
            },
            "required": ["status", "rationale"],
        }

    async def execute(self, parameters: dict[str, Any]) -> ToolResult:
        status = parameters["status"]
        return ToolResult(
            success=True,
            result=parameters,
            metadata={"task_status": status, "is_complete": status == "complete"},
        )


def create_core_tools() -> list[BaseTool]:
    return [ThinkTool(), TaskStatusTool()]

