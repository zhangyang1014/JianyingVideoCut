"""Run-scoped context for the reference agent loop."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .messages import Message


@dataclass
class ToolApprovalRequest:
    call_id: str
    tool_name: str
    parameters: dict[str, Any]
    reason: str = ""


@dataclass
class ToolApprovalResponse:
    call_id: str
    approved: bool
    reason: str = ""


@dataclass
class AgentContext:
    """Mutable state for a single run or conversation."""

    messages: list[Message] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    approval_requests: dict[str, ToolApprovalRequest] = field(default_factory=dict)
    approval_responses: dict[str, ToolApprovalResponse] = field(default_factory=dict)

    def add_message(self, message: Message) -> None:
        self.messages.append(message)

    def copy(self) -> "AgentContext":
        return AgentContext(
            messages=list(self.messages),
            metadata=dict(self.metadata),
            approval_requests=dict(self.approval_requests),
            approval_responses=dict(self.approval_responses),
        )

