"""Deterministic hooks around the LLM-controlled tool loop."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ..core.context import AgentContext
from ..core.messages import Message


@dataclass
class LoopContext:
    agent_context: AgentContext
    llm_messages: list[Message]
    agent_name: str
    iteration: int = 0
    restart_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    model_client: Any = None


class BaseStartHook(ABC):
    @abstractmethod
    async def on_start(self, context: LoopContext) -> str | None:
        """Return text to inject before the first model call."""


class BaseEndHook(ABC):
    @abstractmethod
    async def on_end(self, context: LoopContext) -> str | None:
        """Return text to inject if the loop should continue."""


class PlanningHook(BaseStartHook):
    def __init__(self, instruction: str | None = None):
        self.instruction = instruction or (
            "Before acting, create a concise plan. Track progress explicitly. "
            "Use task_status before final completion."
        )

    async def on_start(self, context: LoopContext) -> str | None:
        return self.instruction


class CompletionCheckHook(BaseEndHook):
    """Restart the loop if no task_status completion signal was observed."""

    def __init__(self, max_restarts: int = 2):
        self.max_restarts = max_restarts

    async def on_end(self, context: LoopContext) -> str | None:
        if context.restart_count >= self.max_restarts:
            return None

        has_completion = any(
            message.role == "tool"
            and "task_status" in getattr(message, "tool_name", "")
            and "complete" in message.content
            for message in context.agent_context.messages
        )
        if has_completion:
            return None

        return (
            "You are about to stop, but no task_status completion signal was found. "
            "Review remaining requirements, continue if needed, and call task_status "
            "with complete or incomplete before stopping."
        )

