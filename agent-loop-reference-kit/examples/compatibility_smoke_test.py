"""Offline smoke test for the reference kit.

Usage from the kit root:
    PYTHONPATH=src python examples/compatibility_smoke_test.py
"""

from __future__ import annotations

import asyncio

from agent_loop_ref.core.messages import AssistantMessage
from agent_loop_ref.core.types import AgentResponse
from agent_loop_ref import Agent
from agent_loop_ref.tools import TaskStatusTool, ThinkTool


class FakeModelClient:
    async def create(self, messages, tools=None):
        if len(messages) < 3:
            return AssistantMessage("I will finish now.", source="fake")
        return AssistantMessage("Done.", source="fake")


async def main() -> None:
    agent = Agent(
        name="smoke",
        instructions="Return a short answer.",
        model_client=FakeModelClient(),
        tools=[ThinkTool(), TaskStatusTool()],
    )
    response = await agent.run("Say hello")
    assert isinstance(response, AgentResponse)
    assert response.final_content == "I will finish now."
    print("Smoke test passed")


if __name__ == "__main__":
    asyncio.run(main())

