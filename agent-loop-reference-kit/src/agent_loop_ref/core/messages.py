"""Message protocol used by the reference agent loop.

Adapted from PicoAgents concepts, simplified for portability.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


Role = Literal["system", "user", "assistant", "tool"]


@dataclass
class ToolCallRequest:
    """A model-requested tool call."""

    tool_name: str
    parameters: dict[str, Any]
    call_id: str


@dataclass
class Message:
    """Base message passed through the agent loop."""

    content: str
    source: str
    role: Role
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_openai_message(self) -> dict[str, Any]:
        return {"role": self.role, "content": self.content}


@dataclass
class SystemMessage(Message):
    def __init__(self, content: str, source: str = "system"):
        super().__init__(content=content, source=source, role="system")


@dataclass
class UserMessage(Message):
    def __init__(self, content: str, source: str = "user"):
        super().__init__(content=content, source=source, role="user")


@dataclass
class AssistantMessage(Message):
    tool_calls: list[ToolCallRequest] | None = None

    def __init__(
        self,
        content: str,
        source: str = "assistant",
        tool_calls: list[ToolCallRequest] | None = None,
    ):
        super().__init__(content=content, source=source, role="assistant")
        self.tool_calls = tool_calls


@dataclass
class ToolMessage(Message):
    tool_call_id: str = ""
    tool_name: str = ""

    def __init__(self, content: str, tool_call_id: str, tool_name: str):
        super().__init__(content=content, source="tool", role="tool")
        self.tool_call_id = tool_call_id
        self.tool_name = tool_name

    def to_openai_message(self) -> dict[str, Any]:
        return {
            "role": "tool",
            "tool_call_id": self.tool_call_id,
            "content": self.content,
        }

