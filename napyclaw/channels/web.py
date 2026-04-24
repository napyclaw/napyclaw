"""WebChannel — self-hosted webchat channel using aiohttp for inbound webhook."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import aiohttp
from aiohttp import web

from napyclaw.channels.base import Channel, Message


class WebChannel(Channel):
    """Self-hosted webchat channel. Receives messages via aiohttp webhook, sends via comms."""

    channel_type = "webchat"

    def __init__(self, comms_url: str, webhook_host: str, webhook_port: int) -> None:
        super().__init__()
        self._comms_url = comms_url.rstrip("/")
        self._webhook_host = webhook_host
        self._webhook_port = webhook_port
        self._session: aiohttp.ClientSession | None = None
        self._runner: web.AppRunner | None = None

    async def connect(self) -> None:
        self._session = aiohttp.ClientSession()

        # Start inbound webhook listener
        app = web.Application()
        app.router.add_post("/inbound", self._handle_inbound)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "0.0.0.0", self._webhook_port)
        await site.start()

        # Register webhook URL with comms
        webhook_url = f"http://{self._webhook_host}:{self._webhook_port}/inbound"
        async with self._session.post(
            f"{self._comms_url}/register",
            json={"webhook_url": webhook_url},
        ):
            pass

    async def disconnect(self) -> None:
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
        if self._session:
            await self._session.close()
            self._session = None

    async def send(self, group_id: str, text: str) -> None:
        if self._session:
            async with self._session.post(
                f"{self._comms_url}/send",
                json={"channel": group_id, "text": text},
            ):
                pass

    async def set_typing(self, group_id: str, on: bool) -> None:
        # Encode typing state as a sentinel text frame; comms interprets it
        sentinel = f"\x00typing:{'true' if on else 'false'}"
        await self.send(group_id, sentinel)

    async def _handle_inbound(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()
        except Exception:
            return web.Response(status=400)

        if self._handler:
            msg = Message(
                group_id=data.get("group_id", ""),
                channel_name=data.get("group_id", ""),
                sender_id=data.get("sender_id", "owner"),
                sender_name=data.get("sender_id", "owner"),
                text=data.get("text", ""),
                timestamp=datetime.now(timezone.utc).isoformat(),
                channel_type="webchat",
            )
            try:
                asyncio.create_task(self._handler(msg))
            except Exception:
                import logging
                logging.getLogger(__name__).exception("handler raised in _handle_inbound")

        return web.json_response({"ok": True})
