"""Workspace-scoped coding tools.

These tools are intentionally small. Treat them as references, not production
security boundaries. Review shell access before enabling them for an agent.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from ..core.types import ToolResult
from .base import ApprovalMode, BaseTool


class WorkspaceTool(BaseTool):
    def __init__(
        self,
        name: str,
        description: str,
        workspace: str | Path,
        approval_mode: ApprovalMode = ApprovalMode.NEVER,
    ):
        super().__init__(name, description, approval_mode=approval_mode)
        self.workspace = Path(workspace).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)

    def resolve_path(self, path: str) -> Path:
        target = (self.workspace / path).resolve()
        target.relative_to(self.workspace)
        return target


class ReadFileTool(WorkspaceTool):
    def __init__(self, workspace: str | Path):
        super().__init__("read_file", "Read a file inside the workspace.", workspace)

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"file_path": {"type": "string"}},
            "required": ["file_path"],
        }

    async def execute(self, parameters: dict[str, Any]) -> ToolResult:
        try:
            path = self.resolve_path(parameters["file_path"])
            return ToolResult(success=True, result=path.read_text(encoding="utf-8"))
        except Exception as exc:
            return ToolResult(success=False, error=str(exc), metadata=parameters)


class WriteFileTool(WorkspaceTool):
    def __init__(self, workspace: str | Path):
        super().__init__(
            "write_file",
            "Write a file or replace text inside a workspace file.",
            workspace,
            approval_mode=ApprovalMode.ALWAYS,
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "content": {"type": "string"},
                "old_str": {"type": "string"},
                "new_str": {"type": "string"},
            },
            "required": ["file_path"],
        }

    async def execute(self, parameters: dict[str, Any]) -> ToolResult:
        try:
            path = self.resolve_path(parameters["file_path"])
            path.parent.mkdir(parents=True, exist_ok=True)

            if "content" in parameters:
                path.write_text(parameters["content"], encoding="utf-8")
                return ToolResult(success=True, result=f"Wrote {path.name}")

            old = parameters["old_str"]
            new = parameters["new_str"]
            text = path.read_text(encoding="utf-8")
            if old not in text:
                raise ValueError("old_str not found")
            path.write_text(text.replace(old, new, 1), encoding="utf-8")
            return ToolResult(success=True, result=f"Updated {path.name}")
        except Exception as exc:
            return ToolResult(success=False, error=str(exc), metadata=parameters)


class ListDirectoryTool(WorkspaceTool):
    def __init__(self, workspace: str | Path):
        super().__init__("list_directory", "List workspace files.", workspace)

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": [],
        }

    async def execute(self, parameters: dict[str, Any]) -> ToolResult:
        try:
            path = self.resolve_path(parameters.get("path", "."))
            items = sorted(p.name + ("/" if p.is_dir() else "") for p in path.iterdir())
            return ToolResult(success=True, result="\n".join(items))
        except Exception as exc:
            return ToolResult(success=False, error=str(exc), metadata=parameters)


class BashExecuteTool(WorkspaceTool):
    def __init__(self, workspace: str | Path, timeout: int = 60):
        super().__init__(
            "bash_execute",
            "Run a shell command inside the workspace.",
            workspace,
            approval_mode=ApprovalMode.ALWAYS,
        )
        self.timeout = timeout

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        }

    async def execute(self, parameters: dict[str, Any]) -> ToolResult:
        try:
            proc = await asyncio.create_subprocess_shell(
                parameters["command"],
                cwd=str(self.workspace),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), self.timeout)
            output = stdout.decode() + stderr.decode()
            return ToolResult(
                success=proc.returncode == 0,
                result=output,
                error=None if proc.returncode == 0 else output,
                metadata={"returncode": proc.returncode},
            )
        except Exception as exc:
            return ToolResult(success=False, error=str(exc), metadata=parameters)


def create_coding_tools(workspace: str | Path, bash_timeout: int = 60) -> list[BaseTool]:
    return [
        ReadFileTool(workspace),
        WriteFileTool(workspace),
        ListDirectoryTool(workspace),
        BashExecuteTool(workspace, timeout=bash_timeout),
    ]

