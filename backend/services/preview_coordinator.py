"""同一预览合并请求，不同预览限制并发；请求断开不会取消其他人的等待。"""

import asyncio
from dataclasses import dataclass, field
from typing import Awaitable, Callable


@dataclass
class _State:
    slots: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(2))
    jobs: dict[str, asyncio.Task] = field(default_factory=dict)


_states: dict[asyncio.AbstractEventLoop, _State] = {}


async def join_preview(key: str, render: Callable[[], Awaitable[dict]]) -> dict:
    loop = asyncio.get_running_loop()
    state = _states.setdefault(loop, _State())
    job = state.jobs.get(key)
    if job is None:
        async def run():
            async with state.slots:
                return await render()

        job = asyncio.create_task(run(), name=f"clip-preview:{key}")
        state.jobs[key] = job

        def done(completed):
            state.jobs.pop(key, None)
            if not state.jobs:
                _states.pop(loop, None)
            if not completed.cancelled():
                completed.exception()  # 所有请求都断开时也消费异常，下一次请求可重试。

        job.add_done_callback(done)
    return await asyncio.shield(job)
