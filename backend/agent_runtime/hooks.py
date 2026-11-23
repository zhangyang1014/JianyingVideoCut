"""Capability-aware Agent lifecycle hooks."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from .agents import get_agent_manifest_registry
from .registry import get_tool_registry
from .trace import append_trace


class AgentHookEvent(BaseModel):
    task_id: str
    lifecycle: str
    capability: str
    agent_name: str
    agent_type: str
    risk_class: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())


def emit_agent_hook(
    task_id: str,
    lifecycle: str,
    capability: str,
    *,
    payload: Optional[Dict[str, Any]] = None,
) -> AgentHookEvent:
    """Resolve the manifest for a capability and append a trace hook event."""
    manifest = get_agent_manifest_registry().for_capability(capability)
    event = AgentHookEvent(
        task_id=task_id,
        lifecycle=lifecycle,
        capability=capability,
        agent_name=manifest["agent_name"],
        agent_type=manifest.get("agent_type", "unknown"),
        risk_class=manifest.get("risk_class"),
        payload=payload or {},
    )
    append_trace(
        task_id,
        f"agent_hook.{lifecycle}",
        source=event.agent_name,
        payload=event.model_dump(),
    )
    return event


def emit_tool_hook(
    task_id: str,
    lifecycle: str,
    capability: str,
    tool_name: str,
    *,
    payload: Optional[Dict[str, Any]] = None,
) -> AgentHookEvent:
    """Validate tool authorization and trace before/after tool lifecycle hooks."""
    agent_registry = get_agent_manifest_registry()
    tool_registry = get_tool_registry()
    manifest = agent_registry.assert_tool_allowed(capability, tool_name)
    tool_registry.assert_allowed(tool_name, manifest.get("agent_type"))
    return emit_agent_hook(
        task_id,
        lifecycle,
        capability,
        payload={"tool_name": tool_name, **(payload or {})},
    )

