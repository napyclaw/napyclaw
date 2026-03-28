from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from napyclaw.tools.base import Tool

if TYPE_CHECKING:
    from napyclaw.config import Config


class FileReadTool(Tool):
    name = "file_read"
    description = "Read the contents of a file in the workspace directory."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path relative to workspace"},
        },
        "required": ["path"],
    }

    def __init__(self, config: Config, memory_path: Path | None = None) -> None:
        self._workspace = config.workspace_dir
        self._memory_path = memory_path

    async def execute(self, **kwargs) -> str:
        raw_path = kwargs.get("path", "")
        if not raw_path:
            return "Error: path is required."

        if ".." in raw_path:
            return "Error: path traversal is not allowed."

        if raw_path.lower() == "memory.md" and self._memory_path:
            target = self._memory_path
        else:
            target = self._workspace / raw_path

        try:
            return target.read_text(encoding="utf-8")
        except FileNotFoundError:
            return f"Error: file not found — {raw_path}"
        except Exception as exc:
            return f"Error: could not read file — {exc}"


class FileWriteTool(Tool):
    name = "file_write"
    description = "Write content to a file in the workspace directory."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path relative to workspace"},
            "content": {"type": "string", "description": "Content to write"},
        },
        "required": ["path", "content"],
    }

    def __init__(self, config: Config, memory_path: Path | None = None) -> None:
        self._workspace = config.workspace_dir
        self._memory_path = memory_path

    async def execute(self, **kwargs) -> str:
        raw_path = kwargs.get("path", "")
        content = kwargs.get("content", "")
        if not raw_path:
            return "Error: path is required."

        if ".." in raw_path:
            return "Error: path traversal is not allowed."

        if raw_path.lower() == "memory.md" and self._memory_path:
            target = self._memory_path
        else:
            target = self._workspace / raw_path

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return f"Written: {raw_path}"
        except Exception as exc:
            return f"Error: could not write file — {exc}"
