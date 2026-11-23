"""Shared result and event types for the reference agent loop."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .messages import Message


@dataclass
class Usage:
    duration_ms: int = 0
    llm_calls: int = 0
    tool_calls: int = 0
    tokens_input: int = 0
    tokens_output: int = 0


@dataclass
class ToolResult:
    success: bool
    result: Any = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_text(self) -> str:
        if self.success:
            return str(self.result)
        return f"Error: {self.error or 'tool failed'}"


@dataclass
class AgentResponse:
    messages: list[Message]
    source: str
    finish_reason: str = "stop"
    usage: Usage = field(default_factory=Usage)

    @property
    def final_content(self) -> str:
        for message in reversed(self.messages):
            if message.role == "assistant" and message.content:
                return message.content
        return ""


@dataclass
class ToolCallEvent:
    tool_name: str
    parameters: dict[str, Any]
    source: str


@dataclass
class ToolResultEvent:
    tool_name: str
    result: ToolResult
    source: str


@dataclass
class StopMessage:
    content: str
    source: str
    metadata: dict[str, Any] = field(default_factory=dict)

