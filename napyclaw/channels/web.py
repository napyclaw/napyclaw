"""WebChannel — self-hosted webchat channel using aiohttp for inbound webhook."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Awaitable

import aiohttp
from aiohttp import web

from napyclaw.channels.base import Channel, Message

_CONTROL_TYPES = {"memory_approved", "memory_adjusted", "memory_excluded"}
_REGISTER_INTERVAL_SECONDS = 30
_log = logging.getLogger(__name__)


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
        self._control_handler: Callable[[dict], Awaitable[None]] | None = None
        self._register_task: asyncio.Task | None = None

    def register_control_handler(
        self, handler: Callable[[dict], Awaitable[None]]
    ) -> None:
        """Register a handler for non-chat control events (memory_approved, etc.)."""
        self._control_handler = handler

    async def connect(self) -> None:
        self._session = aiohttp.ClientSession()

        # Start inbound webhook listener
        app = web.Application()
        app.router.add_post("/inbound", self._handle_inbound)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "0.0.0.0", self._webhook_port)
        await site.start()

        webhook_url = f"http://{self._webhook_host}:{self._webhook_port}/inbound"
        await self._register_once(webhook_url)
        self._register_task = asyncio.create_task(self._register_loop(webhook_url))

    async def disconnect(self) -> None:
        if self._register_task:
            self._register_task.cancel()
            try:
                await self._register_task
            except asyncio.CancelledError:
                pass
            self._register_task = None
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

    async def _register_once(self, webhook_url: str) -> None:
        if self._session is None:
            return
        try:
            async with self._session.post(
                f"{self._comms_url}/register",
                json={"webhook_url": webhook_url},
            ):
                pass
        except Exception:
            _log.warning(
                "WebChannel: failed to register webhook with comms at %s",
                self._comms_url,
            )

    async def _register_loop(self, webhook_url: str) -> None:
        while True:
            await asyncio.sleep(_REGISTER_INTERVAL_SECONDS)
            await self._register_once(webhook_url)

    async def _handle_inbound(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()
        except Exception:
            return web.Response(status=400)

        # Route control events (memory approvals, adjustments, exclusions) separately
        if data.get("type") in _CONTROL_TYPES:
            if self._control_handler:
                asyncio.create_task(self._control_handler(data))
            return web.json_response({"ok": True})

        if self._handler:
            group_id = data.get("group_id", "")
            msg = Message(
                group_id=group_id,
                channel_name=data.get("display_name") or group_id,
                sender_id=data.get("sender_id", "owner"),
                sender_name=data.get("sender_name") or data.get("sender_id", "owner"),
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
