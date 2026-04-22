from __future__ import annotations

import os

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

app = FastAPI(title="comms")

SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
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
    try:
        resp = await _slack.chat_postMessage(channel=req.channel, text=req.text)
    except SlackApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc.response.get("error", exc)))
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
        try:
            await _slack.chat_postMessage(channel=OWNER_CHANNEL, text=text)
        except SlackApiError:
            pass  # approval notification is best-effort
    return {"ok": True}


@app.post("/register")
async def register(req: RegisterRequest) -> dict:
    global _bot_webhook
    _bot_webhook = req.webhook_url
    return {"ok": True}
