"""Agent manifest registry for the GoldenClip runtime."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_AGENTS_DIR = PROJECT_ROOT / "domains" / "goldenclip" / "agents"


class AgentManifestError(ValueError):
    pass


class AgentManifestRegistry:
    """Load agent manifests and route capabilities to enabled agents."""

    def __init__(self, agents_dir: Path = DEFAULT_AGENTS_DIR):
        self.agents_dir = agents_dir
        self._agents: Dict[str, Dict[str, Any]] = {}
        self._capability_index: Dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        for path in sorted(self.agents_dir.glob("*.json")):
            with open(path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            name = manifest.get("agent_name")
            if not name:
                raise AgentManifestError(f"Agent manifest 缺少 agent_name: {path}")
            self._agents[name] = manifest
            if manifest.get("enabled", True):
                for capability in manifest.get("capabilities", []):
                    self._capability_index[capability] = name

    def names(self) -> Iterable[str]:
        return self._agents.keys()

    def get(self, agent_name: str) -> Dict[str, Any]:
        try:
            return self._agents[agent_name]
        except KeyError as exc:
            raise AgentManifestError(f"未注册的 Agent: {agent_name}") from exc

    def for_capability(self, capability: str) -> Dict[str, Any]:
        try:
            return self.get(self._capability_index[capability])
        except KeyError as exc:
            raise AgentManifestError(f"没有启用的 Agent 可处理能力: {capability}") from exc

    def allowed_tools_for_capability(self, capability: str) -> List[str]:
        return list(self.for_capability(capability).get("allowed_tools") or [])

    def assert_tool_allowed(self, capability: str, tool_name: str) -> Dict[str, Any]:
        manifest = self.for_capability(capability)
        allowed_tools = manifest.get("allowed_tools") or []
        if tool_name not in allowed_tools:
            raise AgentManifestError(f"{manifest['agent_name']} 无权调用工具 {tool_name}")
        return manifest

    def agent_name_for_capability(self, capability: str) -> Optional[str]:
        return self._capability_index.get(capability)


@lru_cache(maxsize=1)
def get_agent_manifest_registry() -> AgentManifestRegistry:
    return AgentManifestRegistry()

