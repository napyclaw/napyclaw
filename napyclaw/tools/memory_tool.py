from __future__ import annotations

from typing import TYPE_CHECKING

from napyclaw.tools.base import Tool

if TYPE_CHECKING:
    from napyclaw.memory import MemoryBackend


class SaveToMemoryTool(Tool):
    name = "save_to_memory"
    description = (
        "Explicitly save a specific piece of text to long-term memory. "
        "Use this when the user asks to remember a quote, excerpt, or specific detail "
        "from a search result or conversation. Do not use this automatically — only when "
        "the user explicitly asks to save or remember something specific."
    )
    parameters = {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "The exact text to save to memory",
            },
            "note": {
                "type": "string",
                "description": "Optional context note to store alongside the text (e.g. source, why it's relevant)",
            },
        },
        "required": ["text"],
    }

    def __init__(self, memory: MemoryBackend, group_id: str) -> None:
        self._memory = memory
        self._group_id = group_id

    async def execute(self, **kwargs) -> str:
        text = kwargs.get("text", "").strip()
        note = kwargs.get("note", "").strip()

        if not text:
            return "Error: text is required."

        entry = f"{note}\n{text}" if note else text

        try:
            await self._memory.capture(entry, group_id=self._group_id)
            return "Saved to memory."
        except Exception as exc:
            return f"Error saving to memory: {exc}"
