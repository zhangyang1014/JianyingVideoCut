"""Minimal OpenAI-compatible model adapter plus example agent factory."""

from __future__ import annotations

import json
import os
from typing import Any

from ..core.agent import Agent
from ..core.messages import AssistantMessage, Message, ToolCallRequest
from ..tools.base import FunctionTool
from ..tools.core import TaskStatusTool, ThinkTool


class OpenAICompatibleClient:
    """Small adapter for the OpenAI Python SDK.

    Install dependency in the target project:
        pip install openai
    """

    def __init__(
        self,
        model: str = "gpt-4.1-mini",
        api_key: str | None = None,
        base_url: str | None = None,
    ):
        from openai import AsyncOpenAI

        self.model = model
        self.client = AsyncOpenAI(
            api_key=api_key or os.getenv("OPENAI_API_KEY"),
            base_url=base_url,
        )

    async def create(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
    ) -> AssistantMessage:
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[message.to_openai_message() for message in messages],
            tools=tools,
        )
        raw = response.choices[0].message
        tool_calls: list[ToolCallRequest] = []
        for call in raw.tool_calls or []:
            tool_calls.append(
                ToolCallRequest(
                    tool_name=call.function.name,
                    parameters=json.loads(call.function.arguments or "{}"),
                    call_id=call.id,
                )
            )
        return AssistantMessage(
            content=raw.content or "",
            source="llm",
            tool_calls=tool_calls or None,
        )


def calculate(expression: str) -> str:
    """Evaluate a simple math expression. Replace with a safer parser in production."""

    return str(eval(expression, {"__builtins__": {}}, {}))


def create_minimal_agent(model: str = "gpt-4.1-mini") -> Agent:
    client = OpenAICompatibleClient(model=model)
    return Agent(
        name="minimal_agent",
        instructions=(
            "You are a careful autonomous assistant. Use tools when helpful. "
            "Before finishing, call task_status with complete or incomplete."
        ),
        model_client=client,
        tools=[ThinkTool(), TaskStatusTool(), FunctionTool(calculate)],
        max_iterations=8,
    )


async def print_stream(agent: Agent, task: str) -> None:
    async for item in agent.run_stream(task):
        print(item)

