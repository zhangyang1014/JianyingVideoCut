"""Base interfaces for reference agents and model clients."""

from __future__ import annotations

from typing import Protocol

from .messages import AssistantMessage, Message


class ChatModelClient(Protocol):
    """Minimal model client expected by Agent."""

    async def create(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
    ) -> AssistantMessage:
        """Return an assistant message, optionally with tool calls."""


class BaseAgent(Protocol):
    name: str
    description: str

