from __future__ import annotations

from typing import TYPE_CHECKING

from napyclaw.tools.base import Tool

if TYPE_CHECKING:
    from napyclaw.channels.base import Channel


class SendMessageTool(Tool):
    name = "send_message"
    description = "Send a message to a channel. Defaults to the current channel if group_id is omitted."
    parameters = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Message text to send"},
            "group_id": {
                "type": "string",
                "description": "Target channel ID (optional, defaults to current)",
            },
        },
        "required": ["text"],
    }

    def __init__(self, channel: Channel, current_group_id: str) -> None:
        self._channel = channel
        self._current_group_id = current_group_id

    async def execute(self, **kwargs) -> str:
        text = kwargs.get("text", "")
        if not text:
            return "Error: text is required."

        group_id = kwargs.get("group_id") or self._current_group_id

        try:
            await self._channel.send(group_id, text)
            return "Sent"
        except Exception as exc:
            return f"Error: could not send message — {exc}"
