from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from napyclaw.channels.base import Message
from napyclaw.channels.slack import SlackChannel


class TestSlackChannel:
    def test_channel_type(self):
        channel = SlackChannel(bot_token="xoxb-test", app_token="xapp-test")
        assert channel.channel_type == "slack"

    async def test_send_calls_client(self):
        channel = SlackChannel(bot_token="xoxb-test", app_token="xapp-test")
        channel._client = AsyncMock()

        await channel.send("C001", "Hello!")
        channel._client.chat_postMessage.assert_called_once_with(
            channel="C001", text="Hello!"
        )

    async def test_on_message_normalizes_event(self):
        channel = SlackChannel(bot_token="xoxb-test", app_token="xapp-test")
        channel._bot_user_id = "U0BOT"
        channel._client = AsyncMock()
        channel._client.conversations_info = AsyncMock(
            return_value={"channel": {"name": "general"}}
        )
        channel._client.users_info = AsyncMock(
            return_value={
                "user": {
                    "name": "alice",
                    "profile": {"display_name": "Alice", "real_name": "Alice Smith"},
                }
            }
        )

        received: list[Message] = []

        async def capture(msg: Message) -> None:
            received.append(msg)

        channel.register_handler(capture)

        event = {
            "channel": "C001",
            "user": "U001",
            "text": "<@U0BOT> hello",
        }

        await channel._on_message(event)

        assert len(received) == 1
        msg = received[0]
        assert msg.group_id == "C001"
        assert msg.channel_name == "general"
        assert msg.sender_id == "U001"
        assert msg.sender_name == "Alice"
        assert msg.text == "<@U0BOT> hello"
        assert msg.channel_type == "slack"

    async def test_ignores_bot_own_messages(self):
        channel = SlackChannel(bot_token="xoxb-test", app_token="xapp-test")
        channel._bot_user_id = "U0BOT"

        received: list[Message] = []
        channel.register_handler(lambda msg: received.append(msg))

        # Message from the bot itself
        await channel._on_message({"channel": "C001", "user": "U0BOT", "text": "hi"})
        assert len(received) == 0

        # Message with bot_id
        await channel._on_message(
            {"channel": "C001", "user": "U001", "text": "hi", "bot_id": "B123"}
        )
        assert len(received) == 0

    async def test_ignores_message_subtypes(self):
        channel = SlackChannel(bot_token="xoxb-test", app_token="xapp-test")
        channel._bot_user_id = "U0BOT"

        received: list[Message] = []
        channel.register_handler(lambda msg: received.append(msg))

        await channel._on_message(
            {"channel": "C001", "user": "U001", "text": "hi", "subtype": "message_changed"}
        )
        assert len(received) == 0

    async def test_channel_name_caching(self):
        channel = SlackChannel(bot_token="xoxb-test", app_token="xapp-test")
        channel._client = AsyncMock()
        channel._client.conversations_info = AsyncMock(
            return_value={"channel": {"name": "dev"}}
        )

        name1 = await channel._resolve_channel_name("C001")
        name2 = await channel._resolve_channel_name("C001")

        assert name1 == "dev"
        assert name2 == "dev"
        # Only called once due to caching
        assert channel._client.conversations_info.call_count == 1

    async def test_channel_name_fallback_on_error(self):
        channel = SlackChannel(bot_token="xoxb-test", app_token="xapp-test")
        channel._client = AsyncMock()
        channel._client.conversations_info = AsyncMock(side_effect=Exception("API error"))

        name = await channel._resolve_channel_name("C001")
        assert name == "C001"  # Falls back to channel ID


class TestOAuth:
    async def test_oauth_not_implemented(self):
        from napyclaw.oauth import OAuthCallbackServer

        server = OAuthCallbackServer()
        with pytest.raises(NotImplementedError):
            await server.get_authorization_url("google", "U001")

        with pytest.raises(NotImplementedError):
            await server.handle_callback("code", "state")


class TestRecipeToolBase:
    def test_missing_credential_message(self):
        from napyclaw.tools.recipes.base import RecipeTool

        class DummyRecipe(RecipeTool):
            name = "dummy"
            description = "test"
            parameters = {}

            async def execute(self, **kwargs) -> str:
                return "ok"

        config = MagicMock()
        tool = DummyRecipe(config=config)
        msg = tool._missing_credential_message("google")
        assert "google" in msg
        assert "connect" in msg

    async def test_get_credential_returns_none(self):
        from napyclaw.tools.recipes.base import RecipeTool

        class DummyRecipe(RecipeTool):
            name = "dummy"
            description = "test"
            parameters = {}

            async def execute(self, **kwargs) -> str:
                return "ok"

        config = MagicMock()
        tool = DummyRecipe(config=config)
        assert await tool.get_credential("google", "U001") is None
