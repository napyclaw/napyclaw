from __future__ import annotations

import os

import httpx
from fastapi import FastAPI
from pydantic import BaseModel
from slack_sdk.web.async_client import AsyncWebClient

app = FastAPI(title="comms")

SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_APP_TOKEN = os.environ.get("SLACK_APP_TOKEN", "")
OWNER_CHANNEL = os.environ.get("OWNER_CHANNEL", "")

_slack = AsyncWebClient(token=SLACK_BOT_TOKEN)
_bot_webhook: str | None = None


class SendRequest(BaseModel):
    channel: str
    text: str


class ApprovalRequest(BaseModel):
    token: str
    hostname: str
    url: str


class RegisterRequest(BaseModel):
    webhook_url: str


@app.post("/send")
async def send(req: SendRequest) -> dict:
    resp = await _slack.chat_postMessage(channel=req.channel, text=req.text)
    return {"ok": resp.get("ok", False)}


@app.post("/notify/approval")
async def notify_approval(req: ApprovalRequest) -> dict:
    text = (
        f":lock: *Egress approval needed*\n"
        f"Domain: `{req.hostname}`\n"
        f"Token: `{req.token}`\n\n"
        f"Reply with one of:\n"
        f"• `approve once {req.token}`\n"
        f"• `approve always {req.token}`\n"
        f"• `deny once {req.token}`\n"
        f"• `deny always {req.token}`"
    )
    if OWNER_CHANNEL:
        await _slack.chat_postMessage(channel=OWNER_CHANNEL, text=text)
    return {"ok": True}


@app.post("/register")
async def register(req: RegisterRequest) -> dict:
    global _bot_webhook
    _bot_webhook = req.webhook_url
    return {"ok": True}
