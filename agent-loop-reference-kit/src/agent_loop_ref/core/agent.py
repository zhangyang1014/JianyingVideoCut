"""Reference autonomous-capable agent loop."""

from __future__ import annotations

import time
from collections.abc import AsyncGenerator
from typing import Any, Union

from ..control.hooks import BaseEndHook, BaseStartHook, LoopContext
from ..tools.base import ApprovalMode, BaseTool, FunctionTool
from .base_agent import ChatModelClient
from .context import AgentContext, ToolApprovalRequest
from .messages import AssistantMessage, Message, SystemMessage, ToolMessage, UserMessage
from .types import AgentResponse, ToolCallEvent, ToolResultEvent, Usage


StreamItem = Union[Message, ToolCallEvent, ToolResultEvent, AgentResponse]


class Agent:
    """A small, inspectable implementation of the core agent loop."""

    def __init__(
        self,
        name: str,
        instructions: str,
        model_client: ChatModelClient,
        tools: list[BaseTool | Any] | None = None,
        description: str = "",
        max_iterations: int = 10,
        start_hooks: list[BaseStartHook] | None = None,
        end_hooks: list[BaseEndHook] | None = None,
    ):
        self.name = name
        self.instructions = instructions
        self.model_client = model_client
        self.description = description or f"Agent: {name}"
        self.max_iterations = max_iterations
        self.start_hooks = start_hooks or []
        self.end_hooks = end_hooks or []
        self.tools = self._normalize_tools(tools or [])

    def _normalize_tools(self, tools: list[BaseTool | Any]) -> dict[str, BaseTool]:
        normalized: dict[str, BaseTool] = {}
        for item in tools:
            wrapped = item if isinstance(item, BaseTool) else FunctionTool(item)
            normalized[wrapped.name] = wrapped
        return normalized

    async def run(
        self,
        task: str,
        context: AgentContext | None = None,
    ) -> AgentResponse:
        response: AgentResponse | None = None
        async for item in self.run_stream(task, context=context):
            if isinstance(item, AgentResponse):
                response = item
        return response or AgentResponse(messages=[], source=self.name, finish_reason="no_response")

    async def run_stream(
        self,
        task: str,
        context: AgentContext | None = None,
    ) -> AsyncGenerator[StreamItem, None]:
        start = time.time()
        usage = Usage()
        working_context = context.copy() if context else AgentContext()

        user_message = UserMessage(task)
        working_context.add_message(user_message)
        yield user_message

        loop_context = LoopContext(
            agent_context=working_context,
            llm_messages=self._prepare_messages(working_context),
            agent_name=self.name,
            model_client=self.model_client,
        )

        for hook in self.start_hooks:
            injection = await hook.on_start(loop_context)
            if injection:
                hook_message = UserMessage(injection, source="hook")
                working_context.add_message(hook_message)
                loop_context.llm_messages.append(hook_message)

        finish_reason = "stop"
        iteration = 0
        while iteration < self.max_iterations:
            usage.llm_calls += 1
            assistant_message = await self.model_client.create(
                loop_context.llm_messages,
                tools=[tool.to_llm_format() for tool in self.tools.values()] or None,
            )
            assistant_message.source = self.name
            working_context.add_message(assistant_message)
            loop_context.llm_messages.append(assistant_message)

            if not assistant_message.tool_calls:
                should_continue = False
                loop_context.iteration = iteration
                for hook in self.end_hooks:
                    injection = await hook.on_end(loop_context)
                    if injection:
                        resume_message = UserMessage(injection, source="hook")
                        working_context.add_message(resume_message)
                        loop_context.llm_messages.append(resume_message)
                        loop_context.restart_count += 1
                        should_continue = True
                        break
                if should_continue:
                    iteration += 1
                    continue
                yield assistant_message
                break

            for call in assistant_message.tool_calls:
                tool = self.tools.get(call.tool_name)
                if tool is None:
                    result_text = f"Error: tool not found: {call.tool_name}"
                    tool_message = ToolMessage(result_text, call.call_id, call.tool_name)
                    working_context.add_message(tool_message)
                    loop_context.llm_messages.append(tool_message)
                    yield tool_message
                    continue

                yield ToolCallEvent(call.tool_name, call.parameters, self.name)

                if tool.approval_mode == ApprovalMode.ALWAYS:
                    working_context.approval_requests[call.call_id] = ToolApprovalRequest(
                        call_id=call.call_id,
                        tool_name=call.tool_name,
                        parameters=call.parameters,
                        reason="Tool requires approval before execution.",
                    )
                    finish_reason = "approval_needed"
                    break

                usage.tool_calls += 1
                result = await tool.execute(call.parameters)
                yield ToolResultEvent(call.tool_name, result, self.name)
                tool_message = ToolMessage(result.as_text(), call.call_id, call.tool_name)
                working_context.add_message(tool_message)
                loop_context.llm_messages.append(tool_message)
                yield tool_message

            if finish_reason == "approval_needed":
                break
            iteration += 1
        else:
            finish_reason = "max_iterations"

        usage.duration_ms = int((time.time() - start) * 1000)
        yield AgentResponse(
            messages=working_context.messages,
            source=self.name,
            finish_reason=finish_reason,
            usage=usage,
        )

    def _prepare_messages(self, context: AgentContext) -> list[Message]:
        return [SystemMessage(self.instructions, source="system_instructions"), *context.messages]

