from __future__ import annotations

import asyncio
import os
import pathlib
from collections import deque
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient


def _load_secret(name: str, environment: str = "prod") -> str:
    project_id = os.environ.get("INFISICAL_PROJECT_ID", "")
    try:
        from infisical_client import ClientSettings, GetSecretOptions, InfisicalClient
        client_id = os.environ.get("INFISICAL_CLIENT_ID", "")
        client_secret = os.environ.get("INFISICAL_CLIENT_SECRET", "")
        infisical_url = os.environ.get("INFISICAL_URL", "http://infisical:8080")
        if not client_id or not client_secret:
            return ""
        ic = InfisicalClient(ClientSettings(
            client_id=client_id,
            client_secret=client_secret,
            site_url=infisical_url,
        ))
        val = ic.getSecret(GetSecretOptions(
            environment=environment, project_id=project_id, secret_name=name,
        ))
        return val.secret_value if val and val.secret_value else ""
    except Exception:
        return ""


@asynccontextmanager
async def lifespan(app: FastAPI):
    global SLACK_BOT_TOKEN, OWNER_CHANNEL, _slack, _http_client
    environment = os.environ.get("INFISICAL_ENVIRONMENT", "prod")
    SLACK_BOT_TOKEN = _load_secret("SLACK_BOT_TOKEN", environment) or os.environ.get("SLACK_BOT_TOKEN", "")
    OWNER_CHANNEL = _load_secret("SLACK_OWNER_CHANNEL", environment) or os.environ.get("OWNER_CHANNEL", "")
    _slack = AsyncWebClient(token=SLACK_BOT_TOKEN)
    _http_client = httpx.AsyncClient()
    yield
    await _http_client.aclose()


app = FastAPI(title="comms", lifespan=lifespan)

SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
OWNER_CHANNEL = os.environ.get("OWNER_CHANNEL", "")

_slack = AsyncWebClient(token=SLACK_BOT_TOKEN)
_bot_webhook: str | None = None
_ws_connection: WebSocket | None = None
_http_client: httpx.AsyncClient | None = None

# In-memory message buffer: group_id -> deque of {"role", "text"} dicts
_message_buffer: dict[str, deque] = {}
_BUFFER_SIZE = 50

# In-memory specialist list for sidebar
_specialists: list[dict] = []

# Pending approval callbacks: token -> egressguard callback URL
_pending_approvals: dict[str, str] = {}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _push_to_ws(payload: dict) -> None:
    """Push a JSON payload to the active WebSocket connection, if any."""
    if _ws_connection is not None:
        try:
            await _ws_connection.send_json(payload)
        except Exception:
            pass


def _buffer_message(group_id: str, role: str, text: str) -> None:
    if group_id not in _message_buffer:
        _message_buffer[group_id] = deque(maxlen=_BUFFER_SIZE)
    _message_buffer[group_id].append({"role": role, "text": text})


async def _http_post(url: str, payload: dict) -> None:
    if _http_client is None:
        return
    try:
        await _http_client.post(url, json=payload, timeout=5.0)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class SendRequest(BaseModel):
    channel: str
    text: str


class ApprovalRequest(BaseModel):
    token: str
    hostname: str
    url: str


class RegisterRequest(BaseModel):
    webhook_url: str


class SpecialistsSyncRequest(BaseModel):
    specialists: list[dict]


class ApprovalRespondRequest(BaseModel):
    token: str
    decision: str  # "approve_once" | "approve_always" | "deny_once" | "deny_always"


# ---------------------------------------------------------------------------
# Existing endpoints (preserved)
# ---------------------------------------------------------------------------

@app.post("/register")
async def register(req: RegisterRequest) -> dict:
    global _bot_webhook
    _bot_webhook = req.webhook_url
    return {"ok": True}


# ---------------------------------------------------------------------------
# Modified endpoints — WebSocket push first, Slack fallback
# ---------------------------------------------------------------------------

@app.post("/send")
async def send(req: SendRequest) -> dict:
    # Typing indicator sentinel — push over WS only, never to Slack
    if req.text.startswith("\x00typing:"):
        typing_on = req.text == "\x00typing:true"
        await _push_to_ws({"type": "typing", "group_id": req.channel, "on": typing_on})
        return {"ok": True}

    _buffer_message(req.channel, "assistant", req.text)

    if _ws_connection is not None:
        await _push_to_ws({"type": "message", "group_id": req.channel, "text": req.text})
        return {"ok": True}

    # Fallback to Slack if no WebSocket connected
    try:
        resp = await _slack.chat_postMessage(channel=req.channel, text=req.text)
    except SlackApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc.response.get("error", exc)))
    return {"ok": resp.get("ok", False)}


@app.post("/notify/approval")
async def notify_approval(req: ApprovalRequest) -> dict:
    _pending_approvals[req.token] = req.url

    if _ws_connection is not None:
        await _push_to_ws({
            "type": "approval",
            "token": req.token,
            "hostname": req.hostname,
            "url": req.url,
        })
        return {"ok": True}

    # Fallback to Slack
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
            pass
    return {"ok": True}


# ---------------------------------------------------------------------------
# New endpoints
# ---------------------------------------------------------------------------

@app.get("/specialists")
async def get_specialists() -> list[dict]:
    return _specialists


@app.post("/specialists-sync")
async def specialists_sync(req: SpecialistsSyncRequest) -> dict:
    global _specialists
    _specialists = req.specialists
    return {"ok": True}


@app.post("/approval/respond")
async def approval_respond(req: ApprovalRespondRequest) -> dict:
    callback_url = _pending_approvals.pop(req.token, None)
    if callback_url:
        await _http_post(callback_url, {"token": req.token, "decision": req.decision})
    return {"ok": True}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    global _ws_connection
    await ws.accept()
    _ws_connection = ws

    try:
        async for data in ws.iter_json():
            msg_type = data.get("type")

            if msg_type == "hello":
                group_id = data.get("group_id")
                if group_id and group_id in _message_buffer:
                    for buffered in list(_message_buffer[group_id]):
                        await ws.send_json({
                            "type": "message",
                            "group_id": group_id,
                            "role": buffered["role"],
                            "text": buffered["text"],
                            "replayed": True,
                        })

            elif msg_type == "message":
                group_id = data.get("group_id", "")
                text = data.get("text", "")
                _buffer_message(group_id, "user", text)
                if _bot_webhook:
                    asyncio.create_task(_http_post(_bot_webhook, {
                        "group_id": group_id,
                        "sender_id": "owner",
                        "text": text,
                    }))

            elif msg_type == "approval":
                token = data.get("token", "")
                decision = data.get("decision", "")
                callback_url = _pending_approvals.pop(token, None)
                if callback_url:
                    asyncio.create_task(_http_post(callback_url, {
                        "token": token,
                        "decision": decision,
                    }))

    except WebSocketDisconnect:
        pass
    finally:
        if _ws_connection is ws:
            _ws_connection = None


# ---------------------------------------------------------------------------
# Static files (frontend SPA)
# ---------------------------------------------------------------------------

_STATIC_DIR = pathlib.Path(__file__).parent / "static"
if _STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(str(_STATIC_DIR / "index.html"))
