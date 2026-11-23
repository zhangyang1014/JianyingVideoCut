"""Tool abstractions for agent loops.

Adapted from PicoAgents, simplified to avoid framework dependencies.
"""

from __future__ import annotations

import inspect
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from enum import Enum
from typing import Any, get_type_hints

from ..core.types import ToolResult


class ApprovalMode(Enum):
    NEVER = "never_require"
    ALWAYS = "always_require"


class BaseTool(ABC):
    def __init__(
        self,
        name: str,
        description: str,
        approval_mode: ApprovalMode = ApprovalMode.NEVER,
    ):
        self.name = name
        self.description = description
        self.approval_mode = approval_mode

    @property
    @abstractmethod
    def parameters(self) -> dict[str, Any]:
        """JSON schema for tool parameters."""

    @abstractmethod
    async def execute(self, parameters: dict[str, Any]) -> ToolResult:
        """Run the tool."""

    def to_llm_format(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def _type_to_json_schema(annotation: Any) -> str:
    mapping = {
        str: "string",
        int: "integer",
        float: "number",
        bool: "boolean",
        list: "array",
        dict: "object",
    }
    return mapping.get(annotation, "string")


class FunctionTool(BaseTool):
    """Wrap a Python function as an agent tool."""

    def __init__(
        self,
        func: Callable[..., Any] | Callable[..., Awaitable[Any]],
        name: str | None = None,
        description: str | None = None,
        approval_mode: ApprovalMode = ApprovalMode.NEVER,
    ):
        self.func = func
        self.signature = inspect.signature(func)
        self.type_hints = get_type_hints(func)
        super().__init__(
            name=name or func.__name__,
            description=description or inspect.getdoc(func) or f"Execute {func.__name__}",
            approval_mode=approval_mode,
        )

    @property
    def parameters(self) -> dict[str, Any]:
        properties: dict[str, Any] = {}
        required: list[str] = []
        for name, param in self.signature.parameters.items():
            annotation = self.type_hints.get(name, str)
            properties[name] = {"type": _type_to_json_schema(annotation)}
            if param.default is inspect.Parameter.empty:
                required.append(name)
        return {"type": "object", "properties": properties, "required": required}

    async def execute(self, parameters: dict[str, Any]) -> ToolResult:
        try:
            result = self.func(**parameters)
            if inspect.isawaitable(result):
                result = await result
            return ToolResult(success=True, result=result, metadata={"tool": self.name})
        except Exception as exc:
            return ToolResult(
                success=False,
                error=str(exc),
                metadata={"tool": self.name, "parameters": parameters},
            )

