"""Tests for WebChannel — the self-hosted webchat channel implementation."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp import web

from napyclaw.channels.base import Message
from napyclaw.channels.web import WebChannel


class TestWebChannel:
    def test_channel_type(self):
        ch = WebChannel(comms_url="http://comms:8001", webhook_host="bot", webhook_port=9000)
        assert ch.channel_type == "webchat"

    async def test_send_posts_to_comms(self):
        ch = WebChannel(comms_url="http://comms:8001", webhook_host="bot", webhook_port=9000)
        mock_session = MagicMock()
        mock_response = AsyncMock()
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)
        mock_session.post = MagicMock(return_value=mock_response)
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
        request = MagicMock()
        request.json = AsyncMock(return_value={
            "group_id": "grp-1",
            "sender_id": "owner",
            "text": "Hi there",
        })

        resp = await ch._handle_inbound(request)
        assert resp.status == 200
        await asyncio.sleep(0)

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
        mock_session = MagicMock()
        mock_response = AsyncMock()
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)
        mock_session.post = MagicMock(return_value=mock_response)
        ch._session = mock_session

        await ch.set_typing("grp-1", True)

        mock_session.post.assert_called_once_with(
            "http://comms:8001/send",
            json={"channel": "grp-1", "text": "\x00typing:true"},
        )

    async def test_inbound_no_handler(self):
        """POST to /inbound with no handler registered returns 200 without error."""
        ch = WebChannel(comms_url="http://comms:8001", webhook_host="bot", webhook_port=9000)
        request = MagicMock()
        request.json = AsyncMock(return_value={
            "group_id": "grp-1",
            "sender_id": "owner",
            "text": "Hi",
        })

        resp = await ch._handle_inbound(request)
        assert resp.status == 200

    async def test_inbound_malformed_json(self):
        """POST to /inbound with malformed JSON returns 400."""
        ch = WebChannel(comms_url="http://comms:8001", webhook_host="bot", webhook_port=9000)
        request = MagicMock()
        request.json = AsyncMock(side_effect=ValueError("bad json"))

        resp = await ch._handle_inbound(request)
        assert resp.status == 400

    async def test_inbound_sender_name_from_payload(self):
        """POST to /inbound with sender_name sets msg.sender_name correctly."""
        ch = WebChannel(comms_url="http://comms:8001", webhook_host="bot", webhook_port=9000)

        received: list[Message] = []

        async def capture(msg: Message) -> None:
            received.append(msg)

        ch.register_handler(capture)
        request = MagicMock()
        request.json = AsyncMock(return_value={
            "group_id": "grp-1",
            "sender_id": "owner",
            "sender_name": "Nathan",
            "text": "Hi from Nathan",
        })

        resp = await ch._handle_inbound(request)
        assert resp.status == 200
        await asyncio.sleep(0)

        assert len(received) == 1
        assert received[0].sender_name == "Nathan"

    async def test_connect_registers_webhook_and_starts_refresh_task(self):
        ch = WebChannel(comms_url="http://comms:8001", webhook_host="bot", webhook_port=9000)

        mock_session = MagicMock()
        mock_response = AsyncMock()
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)
        mock_session.post = MagicMock(return_value=mock_response)

        mock_runner = AsyncMock()
        mock_site = AsyncMock()
        fake_task = MagicMock()

        def capture_task(coro):
            coro.close()
            return fake_task

        with patch("napyclaw.channels.web.aiohttp.ClientSession", return_value=mock_session):
            with patch("napyclaw.channels.web.web.AppRunner", return_value=mock_runner):
                with patch("napyclaw.channels.web.web.TCPSite", return_value=mock_site):
                    with patch("napyclaw.channels.web.asyncio.create_task", side_effect=capture_task) as mock_create_task:
                        await ch.connect()

        mock_session.post.assert_called_once_with(
            "http://comms:8001/register",
            json={"webhook_url": "http://bot:9000/inbound"},
        )
        mock_create_task.assert_called_once()
        assert ch._register_task is fake_task
