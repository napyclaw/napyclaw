"""Tests for WebChannel — the self-hosted webchat channel implementation."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from napyclaw.channels.base import Message
from napyclaw.channels.web import WebChannel


class TestWebChannel:
    def test_channel_type(self):
        ch = WebChannel(comms_url="http://comms:8001", webhook_host="bot", webhook_port=9000)
        assert ch.channel_type == "webchat"

    async def test_send_posts_to_comms(self):
        ch = WebChannel(comms_url="http://comms:8001", webhook_host="bot", webhook_port=9000)
        mock_session = AsyncMock()
        mock_session.post = AsyncMock(return_value=AsyncMock(__aenter__=AsyncMock(return_value=AsyncMock()), __aexit__=AsyncMock(return_value=False)))
        ch._session = mock_session

        await ch.send("group-1", "Hello!")

        mock_session.post.assert_called_once_with(
            "http://comms:8001/send",
            json={"channel": "group-1", "text": "Hello!"},
        )

    async def test_inbound_webhook_dispatches_handler(self):
        """POST to /inbound normalizes payload to Message and calls handler."""
        ch = WebChannel(comms_url="http://comms:8001", webhook_host="bot", webhook_port=9000)

        received: list[Message] = []

        async def capture(msg: Message) -> None:
            received.append(msg)

        ch.register_handler(capture)

        # Build a minimal aiohttp app with the channel's handler registered
        app = web.Application()
        app.router.add_post("/inbound", ch._handle_inbound)

        async with TestClient(TestServer(app)) as client:
            resp = await client.post(
                "/inbound",
                json={
                    "group_id": "grp-1",
                    "sender_id": "owner",
                    "text": "Hi there",
                },
            )
            assert resp.status == 200

        assert len(received) == 1
        msg = received[0]
        assert msg.group_id == "grp-1"
        assert msg.sender_id == "owner"
        assert msg.text == "Hi there"
        assert msg.channel_type == "webchat"
        assert msg.channel_name == "grp-1"
        assert msg.sender_name == "owner"

    async def test_set_typing_sends_typing_frame(self):
        ch = WebChannel(comms_url="http://comms:8001", webhook_host="bot", webhook_port=9000)
        mock_session = AsyncMock()
        mock_session.post = AsyncMock(return_value=AsyncMock(__aenter__=AsyncMock(return_value=AsyncMock()), __aexit__=AsyncMock(return_value=False)))
        ch._session = mock_session

        await ch.set_typing("grp-1", True)

        mock_session.post.assert_called_once_with(
            "http://comms:8001/send",
            json={"channel": "grp-1", "text": "\x00typing:true"},
        )
