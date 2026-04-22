from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="egressguard")

COMMS_URL = os.environ.get("COMMS_URL", "http://comms:8001")

_STATIC_ALLOW = {
    "api.openai.com",
    "openai.azure.com",
    "bedrock-runtime.us-east-1.amazonaws.com",
    "api.exa.ai",
    "api.tavily.com",
    "infisical.com",
    "app.infisical.com",
}


class TokenStatus(str, Enum):
    pending = "pending"
    approved = "approved"
    denied = "denied"


class Decision(str, Enum):
    approve_once = "approve_once"
    approve_always = "approve_always"
    deny_once = "deny_once"
    deny_always = "deny_always"


@dataclass
class PendingToken:
    token: str
    hostname: str
    original_url: str
    status: TokenStatus = TokenStatus.pending
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


_pending: dict[str, PendingToken] = {}
_allowlist: set[str] = set(_STATIC_ALLOW)
_blocklist: set[str] = set()


def _is_blocked(hostname: str) -> bool:
    parts = hostname.split(".")
    for i in range(len(parts) - 1):
        candidate = ".".join(parts[i:])
        if candidate in _blocklist:
            return True
    if hostname in _blocklist:
        return True
    return False


def _is_allowed(hostname: str) -> bool:
    parts = hostname.split(".")
    # Check exact and parent-domain allowlist entries
    if hostname in _allowlist:
        return True
    for i in range(1, len(parts)):
        if ".".join(parts[i:]) in _allowlist:
            return True
    return False


class CallbackRequest(BaseModel):
    token: str
    decision: Decision
    hostname: str


@app.get("/proxy")
async def proxy(url: str) -> Any:
    """Proxy a GET request through egressguard. Only GET requests are supported."""
    parsed = httpx.URL(url)
    hostname = parsed.host

    if not hostname:
        raise HTTPException(status_code=400, detail="url must be an absolute URL with a hostname")

    if _is_blocked(hostname):
        raise HTTPException(status_code=403, detail=f"Domain blocked: {hostname}")

    if _is_allowed(hostname):
        from fastapi.responses import Response as FastAPIResponse
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=30.0)
        return FastAPIResponse(
            content=resp.content,
            status_code=resp.status_code,
            media_type=resp.headers.get("content-type", "application/octet-stream"),
        )

    token = secrets.token_urlsafe(16)
    _pending[token] = PendingToken(token=token, hostname=hostname, original_url=url)

    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{COMMS_URL}/notify/approval",
                json={"token": token, "hostname": hostname, "url": url},
                timeout=5.0,
            )
    except (httpx.TimeoutException, httpx.ConnectError, httpx.RequestError):
        pass  # comms notification is best-effort; token is still valid

    from fastapi.responses import JSONResponse
    return JSONResponse(
        status_code=202,
        content={"status": "pending", "token": token, "retry_after": 30},
    )


@app.get("/status/{token}")
async def status(token: str) -> dict:
    entry = _pending.get(token)
    if not entry:
        raise HTTPException(status_code=404, detail="Token not found")
    return {"status": entry.status, "token": token, "hostname": entry.hostname}


@app.post("/callback")
async def callback(req: CallbackRequest) -> dict:
    entry = _pending.get(req.token)
    if not entry:
        raise HTTPException(status_code=404, detail="Token not found")

    if req.decision in (Decision.approve_once, Decision.approve_always):
        entry.status = TokenStatus.approved
        if req.decision == Decision.approve_always:
            _allowlist.add(entry.hostname)
    else:
        entry.status = TokenStatus.denied
        if req.decision == Decision.deny_always:
            _blocklist.add(entry.hostname)

    return {"ok": True}
