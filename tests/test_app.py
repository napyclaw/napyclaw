import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from napyclaw.agent import Agent
from napyclaw.app import GroupContext, GroupQueue, NapyClaw
from napyclaw.channels.base import Message
from napyclaw.db import Database
from napyclaw.models.base import ChatResponse


# ---------------------------------------------------------------------------
# GroupQueue
# ---------------------------------------------------------------------------


class TestGroupQueue:
    async def test_serializes_same_group(self):
        queue = GroupQueue()
        order = []

        async def task(name: str, delay: float):
            order.append(f"{name}_start")
            await asyncio.sleep(delay)
            order.append(f"{name}_end")

        # Run two tasks for the same group — should serialize
        await asyncio.gather(
            queue.run("G1", task("A", 0.05)),
            queue.run("G1", task("B", 0.01)),
        )

        # A should complete before B starts
        assert order.index("A_end") < order.index("B_start")

    async def test_parallel_different_groups(self):
        queue = GroupQueue()
        order = []

        async def task(name: str, delay: float):
            order.append(f"{name}_start")
            await asyncio.sleep(delay)
            order.append(f"{name}_end")

        await asyncio.gather(
            queue.run("G1", task("A", 0.05)),
            queue.run("G2", task("B", 0.01)),
        )

        # B should start before A ends (parallel)
        assert order.index("B_start") < order.index("A_end")


# ---------------------------------------------------------------------------
# Trigger matching
# ---------------------------------------------------------------------------


def _make_context(
    group_id: str = "C001",
    default_name: str = "General_napy",
    display_name: str = "General_napy",
    nicknames: list[str] | None = None,
) -> GroupContext:
    client = MagicMock()
    client.provider = "ollama"
    client.model = "llama3.3:latest"
    client.context_window = 8192
    return GroupContext(
        group_id=group_id,
        default_name=default_name,
        display_name=display_name,
        nicknames=nicknames or [],
        owner_id="U001",
        active_client=client,
        is_first_interaction=False,
        agent=MagicMock(),
    )


class TestTriggerMatching:
    def _make_app(self) -> NapyClaw:
        config = MagicMock()
        config.workspace_dir = Path("/tmp/ws")
        config.groups_dir = Path("/tmp/groups")
        app = NapyClaw(
            config=config,
            db=MagicMock(),
            channel=MagicMock(),
        )
        return app

    def test_default_name_trigger(self):
        app = self._make_app()
        ctx = _make_context()
        assert app._matches_trigger("@General_napy hello", ctx) is True

    def test_display_name_trigger(self):
        app = self._make_app()
        ctx = _make_context(display_name="Kevin")
        assert app._matches_trigger("@Kevin do something", ctx) is True

    def test_nickname_trigger(self):
        app = self._make_app()
        ctx = _make_context(nicknames=["Kev", "K-bot"])
        assert app._matches_trigger("hey @Kev what's up", ctx) is True
        assert app._matches_trigger("@K-bot help", ctx) is True

    def test_case_insensitive(self):
        app = self._make_app()
        ctx = _make_context()
        assert app._matches_trigger("@general_napy hi", ctx) is True
        assert app._matches_trigger("@GENERAL_NAPY hi", ctx) is True

    def test_slack_native_mention(self):
        app = self._make_app()
        app.bot_user_id = "U0BOT"
        ctx = _make_context()
        assert app._matches_trigger("<@U0BOT> hello", ctx) is True

    def test_no_trigger(self):
        app = self._make_app()
        ctx = _make_context()
        assert app._matches_trigger("just a normal message", ctx) is False


# ---------------------------------------------------------------------------
# NapyClaw handle_message
# ---------------------------------------------------------------------------


class TestNapyClawHandleMessage:
    async def test_triggered_message_invokes_agent(self, tmp_path: Path):
        db = Database(tmp_path / "test.db")
        await db.init()

        channel = AsyncMock()
        config = MagicMock()
        config.workspace_dir = tmp_path / "workspace"
        config.groups_dir = tmp_path / "groups"
        config.default_provider = "ollama"
        config.default_model = "llama3.3:latest"
        config.max_history_tokens = None

        mock_client = MagicMock()
        mock_client.provider = "ollama"
        mock_client.model = "llama3.3:latest"
        mock_client.context_window = 8192
        mock_client.chat = AsyncMock(
            return_value=ChatResponse(text="Hi there!", tool_calls=None, finish_reason="stop")
        )

        app = NapyClaw(
            config=config,
            db=db,
            channel=channel,
            build_client=lambda p, m: mock_client,
        )
        app.bot_user_id = "U0BOT"

        msg = Message(
            group_id="C001",
            channel_name="general",
            sender_id="U001",
            sender_name="Alice",
            text="<@U0BOT> hello",
            timestamp="2026-03-27T12:00:00Z",
            channel_type="slack",
        )

        await app.handle_message(msg)

        # Channel should have received set_typing and send
        channel.set_typing.assert_called()
        channel.send.assert_called_once_with("C001", "Hi there!")

        # Context should be created
        assert "C001" in app.contexts
        ctx = app.contexts["C001"]
        assert ctx.default_name == "General_napy"
        assert ctx.owner_id == "U001"

    async def test_untriggered_message_stored_only(self, tmp_path: Path):
        db = Database(tmp_path / "test.db")
        await db.init()

        channel = AsyncMock()
        config = MagicMock()
        config.workspace_dir = tmp_path / "workspace"
        config.groups_dir = tmp_path / "groups"

        app = NapyClaw(
            config=config,
            db=db,
            channel=channel,
        )

        msg = Message(
            group_id="C001",
            channel_name="general",
            sender_id="U001",
            sender_name="Alice",
            text="just chatting",
            timestamp="2026-03-27T12:00:00Z",
            channel_type="slack",
        )

        await app.handle_message(msg)

        # Channel should NOT have sent anything
        channel.send.assert_not_called()
        # But no context created
        assert "C001" not in app.contexts

    async def test_existing_context_trigger(self, tmp_path: Path):
        db = Database(tmp_path / "test.db")
        await db.init()

        channel = AsyncMock()
        config = MagicMock()
        config.workspace_dir = tmp_path / "workspace"
        config.groups_dir = tmp_path / "groups"

        mock_client = MagicMock()
        mock_client.provider = "ollama"
        mock_client.model = "llama3.3:latest"
        mock_client.context_window = 8192
        mock_client.chat = AsyncMock(
            return_value=ChatResponse(text="Hey!", tool_calls=None, finish_reason="stop")
        )

        app = NapyClaw(
            config=config,
            db=db,
            channel=channel,
        )

        # Pre-create a context
        ctx = _make_context()
        ctx.agent = Agent(
            client=mock_client,
            tools=[],
            system_prompt="Test.",
        )
        app.contexts["C001"] = ctx

        msg = Message(
            group_id="C001",
            channel_name="general",
            sender_id="U002",
            sender_name="Bob",
            text="@General_napy what's up?",
            timestamp="2026-03-27T12:01:00Z",
            channel_type="slack",
        )

        await app.handle_message(msg)
        channel.send.assert_called_once_with("C001", "Hey!")

    async def test_llm_error_sends_error_message(self, tmp_path: Path):
        from napyclaw.models.openai_client import LLMUnavailableError

        db = Database(tmp_path / "test.db")
        await db.init()

        channel = AsyncMock()
        config = MagicMock()
        config.workspace_dir = tmp_path / "workspace"
        config.groups_dir = tmp_path / "groups"
        config.default_provider = "ollama"
        config.default_model = "llama3.3:latest"
        config.max_history_tokens = None

        mock_client = MagicMock()
        mock_client.provider = "ollama"
        mock_client.model = "llama3.3:latest"
        mock_client.context_window = 8192
        mock_client.chat = AsyncMock(
            side_effect=LLMUnavailableError("Ollama server error: connection refused")
        )

        app = NapyClaw(
            config=config,
            db=db,
            channel=channel,
            build_client=lambda p, m: mock_client,
        )
        app.bot_user_id = "U0BOT"

        msg = Message(
            group_id="C001",
            channel_name="general",
            sender_id="U001",
            sender_name="Alice",
            text="<@U0BOT> hello",
            timestamp="2026-03-27T12:00:00Z",
            channel_type="slack",
        )

        await app.handle_message(msg)

        # Should send error message, not crash
        channel.send.assert_called_once()
        sent_text = channel.send.call_args[0][1]
        assert "Ollama" in sent_text


def test_build_routed_client_is_imported_in_main():
    """Verify build_routed_client is importable from __main__ context."""
    import napyclaw.__main__ as main_module
    assert hasattr(main_module, "build_routed_client")
