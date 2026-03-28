from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from napyclaw.channels.base import Channel, Message


class SlackChannel(Channel):
    """Slack Socket Mode channel via slack-bolt."""

    channel_type = "slack"

    def __init__(self, bot_token: str, app_token: str) -> None:
        super().__init__()
        self._bot_token = bot_token
        self._app_token = app_token
        self._app: Any = None
        self._client: Any = None
        self._bot_user_id: str = ""
        self._channel_name_cache: dict[str, str] = {}

    @property
    def bot_user_id(self) -> str:
        return self._bot_user_id

    async def connect(self) -> None:
        from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
        from slack_bolt.async_app import AsyncApp

        self._app = AsyncApp(token=self._bot_token)
        self._client = self._app.client

        # Get bot user ID
        auth_response = await self._client.auth_test()
        self._bot_user_id = auth_response["user_id"]

        # Register message event handler
        @self._app.event("message")
        async def handle_message(event: dict, say: Any) -> None:
            await self._on_message(event)

        # Start Socket Mode
        handler = AsyncSocketModeHandler(self._app, self._app_token)
        asyncio.create_task(handler.start_async())

    async def disconnect(self) -> None:
        # slack-bolt doesn't expose a clean disconnect for Socket Mode
        pass

    async def send(self, group_id: str, text: str) -> None:
        if self._client:
            await self._client.chat_postMessage(channel=group_id, text=text)

    async def set_typing(self, group_id: str, on: bool) -> None:
        # Slack doesn't have a persistent typing indicator API for bots.
        # The typing indicator is shown automatically when processing.
        pass

    async def _on_message(self, event: dict) -> None:
        """Normalize Slack event to Message and dispatch to handler."""
        if not self._handler:
            return

        # Ignore bot's own messages
        if event.get("bot_id") or event.get("user") == self._bot_user_id:
            return

        # Ignore message subtypes (edits, deletes, etc.) except None (normal messages)
        if event.get("subtype") is not None:
            return

        group_id = event.get("channel", "")
        sender_id = event.get("user", "")
        text = event.get("text", "")

        # Resolve channel name (cached)
        channel_name = await self._resolve_channel_name(group_id)

        # Resolve sender display name
        sender_name = await self._resolve_user_name(sender_id)

        msg = Message(
            group_id=group_id,
            channel_name=channel_name,
            sender_id=sender_id,
            sender_name=sender_name,
            text=text,
            timestamp=datetime.now(timezone.utc).isoformat(),
            channel_type="slack",
        )

        await self._handler(msg)

    async def _resolve_channel_name(self, channel_id: str) -> str:
        """Fetch channel name from Slack API, with caching."""
        if channel_id in self._channel_name_cache:
            return self._channel_name_cache[channel_id]

        if not self._client:
            return channel_id

        try:
            info = await self._client.conversations_info(channel=channel_id)
            name = info["channel"]["name"]
            self._channel_name_cache[channel_id] = name
            return name
        except Exception:
            # Fallback to channel ID on API failure
            self._channel_name_cache[channel_id] = channel_id
            return channel_id

    async def _resolve_user_name(self, user_id: str) -> str:
        """Fetch user display name from Slack API."""
        if not self._client:
            return user_id

        try:
            info = await self._client.users_info(user=user_id)
            profile = info["user"].get("profile", {})
            return (
                profile.get("display_name")
                or profile.get("real_name")
                or info["user"].get("name", user_id)
            )
        except Exception:
            return user_id
