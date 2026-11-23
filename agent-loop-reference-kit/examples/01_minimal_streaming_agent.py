"""Run a minimal streaming agent.

Usage from the kit root:
    PYTHONPATH=src OPENAI_API_KEY=... python examples/01_minimal_streaming_agent.py
"""

from __future__ import annotations

import asyncio

from agent_loop_ref.minimal.streaming_agent import create_minimal_agent, print_stream


async def main() -> None:
    agent = create_minimal_agent()
    await print_stream(agent, "Calculate 12 * 8 and explain the result briefly.")


if __name__ == "__main__":
    asyncio.run(main())

