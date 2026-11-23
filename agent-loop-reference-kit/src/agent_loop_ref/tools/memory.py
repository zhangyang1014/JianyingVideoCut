"""File-based memory tool for autonomous agents."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.types import ToolResult
from .base import BaseTool


class MemoryTool(BaseTool):
    """A small memory tool restricted to a single base directory."""

    def __init__(self, base_path: str | Path = "./memories") -> None:
        super().__init__(
            name="memory",
            description=(
                "Read and update persistent memory files. Supports view, create, "
                "append, str_replace, and search within the memory directory."
            ),
        )
        self.base_path = Path(base_path).resolve()
        self.base_path.mkdir(parents=True, exist_ok=True)

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "enum": ["view", "create", "append", "str_replace", "search"],
                },
                "path": {"type": "string"},
                "file_text": {"type": "string"},
                "append_text": {"type": "string"},
                "old_str": {"type": "string"},
                "new_str": {"type": "string"},
                "query": {"type": "string"},
            },
            "required": ["command"],
        }

    def _resolve(self, path: str) -> Path:
        if path.startswith("/memories"):
            path = path[len("/memories") :]
        target = (self.base_path / path.lstrip("/")).resolve()
        target.relative_to(self.base_path)
        return target

    async def execute(self, parameters: dict[str, Any]) -> ToolResult:
        try:
            command = parameters["command"]
            path = parameters.get("path", "")

            if command == "view":
                target = self._resolve(path)
                if target.is_dir():
                    items = sorted(p.name + ("/" if p.is_dir() else "") for p in target.iterdir())
                    return ToolResult(success=True, result="\n".join(items) or "(empty)")
                return ToolResult(success=True, result=target.read_text(encoding="utf-8"))

            if command == "create":
                target = self._resolve(path)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(parameters.get("file_text", ""), encoding="utf-8")
                return ToolResult(success=True, result=f"Created {path}")

            if command == "append":
                target = self._resolve(path)
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("a", encoding="utf-8") as handle:
                    handle.write(parameters.get("append_text", ""))
                return ToolResult(success=True, result=f"Appended to {path}")

            if command == "str_replace":
                target = self._resolve(path)
                content = target.read_text(encoding="utf-8")
                old = parameters["old_str"]
                if old not in content:
                    raise ValueError("old_str not found")
                target.write_text(content.replace(old, parameters["new_str"], 1), encoding="utf-8")
                return ToolResult(success=True, result=f"Updated {path}")

            if command == "search":
                query = parameters["query"].lower()
                matches: list[str] = []
                for item in self.base_path.rglob("*"):
                    if item.is_file():
                        text = item.read_text(encoding="utf-8", errors="ignore")
                        if query in text.lower():
                            matches.append(str(item.relative_to(self.base_path)))
                return ToolResult(success=True, result="\n".join(matches) or "No matches")

            raise ValueError(f"Unknown command: {command}")
        except Exception as exc:
            return ToolResult(success=False, error=str(exc), metadata=parameters)

