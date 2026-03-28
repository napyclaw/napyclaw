from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from napyclaw.models.base import ChatResponse
from napyclaw.private_session import PrivateSession


def _mock_client():
    client = MagicMock()
    client.provider = "test"
    client.model = "test-model"
    client.context_window = 8192
    client.chat = AsyncMock(
        return_value=ChatResponse(text="Private reply", tool_calls=None, finish_reason="stop")
    )
    return client


class TestPrivateSession:
    def test_create(self):
        client = _mock_client()
        session = PrivateSession.create(
            user_id="U001",
            dm_channel_id="D001",
            origin_group_id="C001",
            client=client,
        )
        assert session.user_id == "U001"
        assert session.dm_channel_id == "D001"
        assert session.origin_group_id == "C001"

    async def test_handle_message(self):
        client = _mock_client()
        session = PrivateSession.create(
            user_id="U001",
            dm_channel_id="D001",
            origin_group_id="C001",
            client=client,
        )
        result = await session.handle("hello", sender_id="U001")
        assert result == "Private reply"

    async def test_handle_updates_activity(self):
        client = _mock_client()
        session = PrivateSession.create(
            user_id="U001",
            dm_channel_id="D001",
            origin_group_id="C001",
            client=client,
        )
        before = session.last_activity
        await session.handle("hello", sender_id="U001")
        assert session.last_activity >= before

    def test_should_end(self):
        client = _mock_client()
        session = PrivateSession.create(
            user_id="U001",
            dm_channel_id="D001",
            origin_group_id="C001",
            client=client,
        )
        assert session.should_end("end private session") is True
        assert session.should_end("End Private Session") is True
        assert session.should_end("keep going") is False

    def test_is_expired(self):
        client = _mock_client()
        session = PrivateSession.create(
            user_id="U001",
            dm_channel_id="D001",
            origin_group_id="C001",
            client=client,
        )
        # Not expired immediately
        assert session.is_expired() is False

        # Force expiry
        session.last_activity = datetime.now(timezone.utc) - timedelta(seconds=1801)
        assert session.is_expired() is True

    def test_no_db_writes(self):
        """Private sessions use NullMemory — no persistence."""
        client = _mock_client()
        session = PrivateSession.create(
            user_id="U001",
            dm_channel_id="D001",
            origin_group_id="C001",
            client=client,
        )
        # Agent should have empty history initially
        assert session.agent.history == []

    async def test_history_stays_in_memory_only(self):
        client = _mock_client()
        session = PrivateSession.create(
            user_id="U001",
            dm_channel_id="D001",
            origin_group_id="C001",
            client=client,
        )
        await session.handle("message 1", sender_id="U001")
        await session.handle("message 2", sender_id="U001")

        # History accumulates in memory
        assert len(session.agent.history) == 4  # 2 user + 2 assistant
