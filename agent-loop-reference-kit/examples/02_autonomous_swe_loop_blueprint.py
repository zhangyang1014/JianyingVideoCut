"""Blueprint for a workspace-scoped SWE agent.

This is a template. Review tool approvals before using it on a real project.
"""

from __future__ import annotations

from pathlib import Path

from agent_loop_ref.control import CompletionCheckHook, PlanningHook
from agent_loop_ref.core.agent import Agent
from agent_loop_ref.minimal.streaming_agent import OpenAICompatibleClient
from agent_loop_ref.tools import MemoryTool, TaskStatusTool, ThinkTool, create_coding_tools


def load_prompt() -> str:
    root = Path(__file__).resolve().parents[1]
    return (root / "prompts" / "swe_system_prompt.md").read_text(encoding="utf-8")


def create_swe_agent(workspace: str | Path, memory_path: str | Path) -> Agent:
    client = OpenAICompatibleClient(model="gpt-4.1-mini")
    return Agent(
        name="swe_agent",
        description="Workspace-scoped software engineering agent blueprint.",
        instructions=load_prompt(),
        model_client=client,
        tools=[
            ThinkTool(),
            TaskStatusTool(),
            MemoryTool(memory_path),
            *create_coding_tools(workspace),
        ],
        start_hooks=[PlanningHook()],
        end_hooks=[CompletionCheckHook(max_restarts=2)],
        max_iterations=30,
    )

