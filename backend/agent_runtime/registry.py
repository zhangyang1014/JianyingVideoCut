"""Tool registry backed by domains/goldenclip/tools.json."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TOOLS_PATH = PROJECT_ROOT / "domains" / "goldenclip" / "tools.json"


class ToolRegistryError(ValueError):
    pass


class ToolRegistry:
    def __init__(self, tools_path: Path = DEFAULT_TOOLS_PATH):
        self.tools_path = tools_path
        with open(tools_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self._tools: Dict[str, Dict[str, Any]] = {
            item["tool_name"]: item
            for item in data.get("tools", [])
        }

    def names(self) -> Iterable[str]:
        return self._tools.keys()

    def get(self, tool_name: str) -> Dict[str, Any]:
        try:
            return self._tools[tool_name]
        except KeyError as exc:
            raise ToolRegistryError(f"未注册的 Agent 工具: {tool_name}") from exc

    def assert_allowed(self, tool_name: str, agent_type: Optional[str] = None) -> Dict[str, Any]:
        tool = self.get(tool_name)
        allowed = tool.get("allowed_agent_types") or []
        if agent_type and allowed and agent_type not in allowed:
            raise ToolRegistryError(f"{agent_type} 无权调用工具 {tool_name}")
        return tool

    def requires_human_approval(self, tool_name: str) -> bool:
        tool = self.get(tool_name)
        return bool(tool.get("requires_human_approval")) or tool.get("risk_level") == "irreversible"


@lru_cache(maxsize=1)
def get_tool_registry() -> ToolRegistry:
    return ToolRegistry()

