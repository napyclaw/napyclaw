# Atomic Mode Architecture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.  Never use compound shell commands (`cmd1 && cmd2`) — each shell call must be atomic.

> **For subagents encountering inconsistencies:** If the existing code doesn't match what the plan expects, or a design decision isn't clear from the plan, **read the spec before going off script**. Find the relevant section using the line numbers in the task's **Spec sections:** block, then read the entire section (not just the quoted snippet) to understand the full design intent. Make decisions consistent with the spec, then report your reasoning in your status update.

**Goal:** Restructure napyclaw into a 6-container Docker stack with network-isolated egress control, a stateless comms adapter, and a self-hosted Infisical instance — enabling fully self-contained ("atomic mode") deployment.

**Architecture:** The bot container has zero direct internet access; all outbound traffic routes through one of three dedicated containers: `comms` (messaging), `egressguard` (LLM/search APIs with async approval flow), or `searxng` (local search). Four Docker networks (`comms-net`, `egress-net`, `search-net`, `data-net`) enforce this isolation at the kernel level.

**Tech Stack:** Docker Compose v2, FastAPI (egressguard + comms services), httpx, asyncio, existing slack-bolt + napyclaw internals.

**Spec:** `docs/superpowers/specs/2026-04-19-atomic-mode-architecture-design.md`

---

## File Map

### New files
- `services/egressguard/main.py` — FastAPI app: proxy endpoint, approval callback, token state
- `services/egressguard/Dockerfile` — lightweight Python image
- `services/comms/main.py` — FastAPI app: wraps SlackChannel, exposes send/receive + approval forwarding
- `services/comms/Dockerfile` — lightweight Python image
- `tests/services/test_egressguard.py` — egressguard service unit tests
- `tests/services/test_comms.py` — comms service unit tests

### Modified files
- `docker-compose.yml` — add 6 services, 4 named networks, remove top-level searxng port exposure
- `napyclaw/egress.py` — add `escalate` path → 202 response, pending token state, poll endpoint client
- `napyclaw/tools/web_search.py` — route all requests through `egress-net` URL (env var)
- `napyclaw/channels/slack.py` — extract connection logic so comms service can reuse it
- `napyclaw/channels/base.py` — no changes expected
- `napyclaw/config.py` — add `egress_url`, `comms_url`, `searxng_url` from env; add Infisical container config
- `napyclaw/__main__.py` — remove direct httpx client construction; use egress-routed client
- `napyclaw/setup.py` — add Infisical bootstrap automation (create project + seed secrets via CLI)
- `README.md` — update architecture section, container topology diagram, atomic mode table

---

## Task 1: Restructure docker-compose.yml with 4 networks and 6 services

**Spec sections:**
- **§ Containers** (lines 59–70) — full container list, roles, internet access, persistent state
- **§ Network Zones** (lines 74–105) — `comms-net`, `egress-net`, `search-net`, `data-net` membership and routing rules
- **§ Container Topology** (lines 14–53) — visual reference for what connects to what

**Files:**
- Modify: `docker-compose.yml`

- [ ] **Step 1: Write the new docker-compose.yml**

Replace the entire file with:

```yaml
networks:
  comms-net:
    driver: bridge
  egress-net:
    driver: bridge
  search-net:
    driver: bridge
  data-net:
    internal: true
    driver: bridge

services:
  bot:
    build: .
    restart: unless-stopped
    networks:
      - comms-net
      - egress-net
      - search-net
      - data-net
    environment:
      INFISICAL_CLIENT_ID: ${INFISICAL_CLIENT_ID}
      INFISICAL_CLIENT_SECRET: ${INFISICAL_CLIENT_SECRET}
      INFISICAL_PROJECT_ID: ${INFISICAL_PROJECT_ID}
      EGRESS_URL: http://egressguard:8000
      COMMS_URL: http://comms:8001
      SEARXNG_URL: http://searxng:8080
    depends_on:
      db:
        condition: service_healthy
      egressguard:
        condition: service_started
      comms:
        condition: service_started

  egressguard:
    build: ./services/egressguard
    restart: unless-stopped
    networks:
      - egress-net
    environment:
      COMMS_URL: http://comms:8001
      OLLAMA_BASE_URL: ${OLLAMA_BASE_URL:-http://host.docker.internal:11434}
    extra_hosts:
      - "host.docker.internal:host-gateway"

  comms:
    build: ./services/comms
    restart: unless-stopped
    networks:
      - comms-net
    environment:
      SLACK_BOT_TOKEN: ${SLACK_BOT_TOKEN}
      SLACK_APP_TOKEN: ${SLACK_APP_TOKEN}

  searxng:
    image: searxng/searxng:latest
    restart: unless-stopped
    networks:
      - search-net
    volumes:
      - ./searxng:/etc/searxng
    environment:
      SEARXNG_BASE_URL: http://searxng:8080/
    cap_drop:
      - ALL
    cap_add:
      - CHOWN
      - SETGID
      - SETUID

  db:
    image: pgvector/pgvector:pg16
    networks:
      - data-net
    environment:
      POSTGRES_DB: napyclaw
      POSTGRES_USER: napyclaw
      POSTGRES_PASSWORD: napyclaw-local
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./napyclaw/migrations/001_thoughts.sql:/docker-entrypoint-initdb.d/001_thoughts.sql
      - ./napyclaw/migrations/002_operational.sql:/docker-entrypoint-initdb.d/002_operational.sql
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U napyclaw"]
      interval: 5s
      timeout: 5s
      retries: 5

  infisical:
    image: infisical/infisical:latest
    restart: unless-stopped
    networks:
      - data-net
    environment:
      ENCRYPTION_KEY: ${INFISICAL_ENCRYPTION_KEY}
      AUTH_SECRET: ${INFISICAL_AUTH_SECRET}
      DB_CONNECTION_URI: postgresql://napyclaw:napyclaw-local@db:5432/infisical
    depends_on:
      db:
        condition: service_healthy
    ports:
      - "8888:8080"

volumes:
  pgdata:
```

- [ ] **Step 2: Verify compose file parses**

```bash
docker compose config --quiet
```
Expected: no output (no errors)

- [ ] **Step 3: Commit**

```bash
git add docker-compose.yml
git commit -m "feat: restructure compose — 6 services, 4 isolated networks"
```

---

## Task 2: Build the egressguard service

**Spec sections:**
- **§ EgressGuard Approval Flow — Request lifecycle** (lines 129–143) — 202 response shape, token, comms callback path
- **§ Approve once vs approve always** (lines 145–154) — four decision variants and their effect on allowlist/blocklist
- **§ Application Layers — EgressGuard** (lines 119–125) — `{domain allowlist}`, `{LLMClient}`, `{exfil sanitize}` responsibilities
- **§ Network Zones** (lines 83–85) — `egress-net` membership: egressguard has internet; bot does not

**Files:**
- Create: `services/egressguard/Dockerfile`
- Create: `services/egressguard/main.py`
- Create: `services/egressguard/requirements.txt`
- Create: `tests/services/test_egressguard.py`

This service is a FastAPI HTTP proxy. The bot routes all outbound HTTP through it. Unknown domains trigger the async approval flow: return 202 with a token, notify comms, await callback or poll.

- [ ] **Step 1: Write the failing tests**

Create `tests/services/test_egressguard.py`:

```python
import pytest
from httpx import AsyncClient, ASGITransport


@pytest.fixture
async def client(monkeypatch):
    monkeypatch.setenv("COMMS_URL", "http://comms-mock:8001")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")
    from services.egressguard.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def test_allowed_domain_proxies(client, respx_mock):
    respx_mock.get("https://api.openai.com/v1/test").mock(return_value=httpx.Response(200, text="ok"))
    resp = await client.get("/proxy", params={"url": "https://api.openai.com/v1/test"})
    assert resp.status_code == 200


async def test_unknown_domain_returns_202(client):
    resp = await client.get("/proxy", params={"url": "https://unknown-domain-xyz.io/api"})
    assert resp.status_code == 202
    data = resp.json()
    assert "token" in data
    assert data["status"] == "pending"


async def test_poll_pending_token(client):
    resp = await client.get("/proxy", params={"url": "https://unknown-domain-xyz.io/api"})
    token = resp.json()["token"]
    poll = await client.get(f"/status/{token}")
    assert poll.json()["status"] == "pending"


async def test_callback_approves_token(client):
    resp = await client.get("/proxy", params={"url": "https://unknown-domain-xyz.io/api"})
    token = resp.json()["token"]
    callback = await client.post("/callback", json={"token": token, "decision": "approve_always", "hostname": "unknown-domain-xyz.io"})
    assert callback.status_code == 200
    poll = await client.get(f"/status/{token}")
    assert poll.json()["status"] == "approved"


async def test_callback_denies_token(client):
    resp = await client.get("/proxy", params={"url": "https://unknown-domain-xyz.io/api"})
    token = resp.json()["token"]
    await client.post("/callback", json={"token": token, "decision": "deny_always", "hostname": "unknown-domain-xyz.io"})
    poll = await client.get(f"/status/{token}")
    assert poll.json()["status"] == "denied"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/services/test_egressguard.py -v
```
Expected: `ModuleNotFoundError: No module named 'services'`

- [ ] **Step 3: Create the service directory and requirements**

```bash
mkdir -p services/egressguard
```

Create `services/egressguard/requirements.txt`:
```
fastapi>=0.111
uvicorn>=0.29
httpx>=0.27
openai>=1.30
```

- [ ] **Step 4: Write the egressguard service**

Create `services/egressguard/main.py`:

```python
from __future__ import annotations

import os
import secrets
import uuid
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


def _is_allowed(hostname: str) -> bool:
    if hostname in _blocklist:
        return False
    if hostname in _allowlist:
        return True
    parts = hostname.split(".")
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
    parsed = httpx.URL(url)
    hostname = parsed.host

    if _is_allowed(hostname):
        async with httpx.AsyncClient() as client:
            resp = await client.get(url)
            return resp.json()

    if hostname in _blocklist:
        raise HTTPException(status_code=403, detail=f"Domain blocked: {hostname}")

    token = secrets.token_urlsafe(16)
    _pending[token] = PendingToken(token=token, hostname=hostname, original_url=url)

    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{COMMS_URL}/notify/approval",
                json={"token": token, "hostname": hostname, "url": url},
                timeout=5.0,
            )
    except Exception:
        pass

    return {"status": "pending", "token": token, "retry_after": 30}


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
            _allowlist.add(req.hostname)
    else:
        entry.status = TokenStatus.denied
        if req.decision == Decision.deny_always:
            _blocklist.add(req.hostname)

    return {"ok": True}
```

- [ ] **Step 5: Write the Dockerfile**

Create `services/egressguard/Dockerfile`:
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py .
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 6: Run tests**

```bash
PYTHONPATH=. pytest tests/services/test_egressguard.py -v
```
Expected: all tests pass

- [ ] **Step 7: Commit**

```bash
git add services/egressguard/ tests/services/test_egressguard.py
git commit -m "feat: add egressguard service — async 202 approval flow, approve/deny once/always"
```

---

## Task 3: Build the comms service

**Spec sections:**
- **§ Containers — comms** (lines 65) — stateless, messaging APIs only, swappable platform
- **§ EgressGuard Approval Flow — Request lifecycle** (lines 129–143) — comms receives approval request from egressguard (step 3), delivers to owner (step 4), forwards response back (step 6)
- **§ Network Zones — comms-net** (lines 78–81) — comms has internet; bot does not; comms also relays egressguard approvals
- **§ Atomic Mode** (lines 176–191) — comms is the only gap for full atomic mode; must be swappable

**Files:**
- Create: `services/comms/Dockerfile`
- Create: `services/comms/main.py`
- Create: `services/comms/requirements.txt`
- Create: `tests/services/test_comms.py`

The comms service is a stateless FastAPI adapter. It wraps Slack Socket Mode outbound (send messages, deliver approval requests) and provides an HTTP endpoint for the bot to send messages through. Inbound Slack messages are forwarded to the bot via a registered webhook.

- [ ] **Step 1: Write the failing tests**

Create `tests/services/test_comms.py`:

```python
import pytest
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient, ASGITransport


@pytest.fixture
async def client(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_APP_TOKEN", "xapp-test")
    with patch("services.comms.main.AsyncWebClient") as mock_wc:
        mock_wc.return_value.chat_postMessage = AsyncMock(return_value={"ok": True})
        from services.comms.main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c


async def test_send_message(client):
    resp = await client.post("/send", json={"channel": "C123", "text": "hello"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


async def test_notify_approval(client):
    resp = await client.post(
        "/notify/approval",
        json={"token": "tok123", "hostname": "example.com", "url": "https://example.com/api"}
    )
    assert resp.status_code == 200


async def test_register_bot_webhook(client):
    resp = await client.post("/register", json={"webhook_url": "http://bot:9000/inbound"})
    assert resp.status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/services/test_comms.py -v
```
Expected: `ModuleNotFoundError: No module named 'services.comms'`

- [ ] **Step 3: Create service directory and requirements**

```bash
mkdir -p services/comms
```

Create `services/comms/requirements.txt`:
```
fastapi>=0.111
uvicorn>=0.29
httpx>=0.27
slack-bolt>=1.18
slack-sdk>=3.27
```

- [ ] **Step 4: Write the comms service**

Create `services/comms/main.py`:

```python
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
```

- [ ] **Step 5: Write the Dockerfile**

Create `services/comms/Dockerfile`:
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py .
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
```

- [ ] **Step 6: Run tests**

```bash
PYTHONPATH=. pytest tests/services/test_comms.py -v
```
Expected: all tests pass

- [ ] **Step 7: Commit**

```bash
git add services/comms/ tests/services/test_comms.py
git commit -m "feat: add comms service — stateless Slack adapter with approval notification"
```

---

## Task 4: Add egress-routed HTTP client to bot config

**Spec sections:**
- **§ Network Zones — egress-net** (lines 83–85) — all bot outbound HTTP routes through egressguard; bot has no direct internet
- **§ Application Layers — bot** (lines 109–118) — `{ToolSystem}` makes outbound calls; `{LLMClient}` makes LLM API calls — both must route through egressguard

**Files:**
- Modify: `napyclaw/config.py` (add `egress_url`, `comms_url`)
- Modify: `napyclaw/egress.py` (add `build_routed_client()` that proxies through egressguard service)
- Modify: `tests/test_egress.py`
- Modify: `tests/test_config.py`

The bot no longer does direct outbound HTTP. All outbound calls go to `http://egressguard:8000/proxy?url=<target>`. The existing `EgressGuard` in-process class is kept for tests; a new `build_routed_client()` factory is added for production use.

- [ ] **Step 1: Write failing tests**

Add to `tests/test_config.py`:
```python
def test_config_loads_egress_url(monkeypatch, tmp_path):
    toml = tmp_path / "napyclaw.toml"
    toml.write_text("[llm]\ndefault_provider = 'openai'\ndefault_model = 'gpt-4o'\n")
    monkeypatch.setenv("EGRESS_URL", "http://egressguard:8000")
    monkeypatch.setenv("COMMS_URL", "http://comms:8001")
    # ... mock infisical secrets as in existing tests ...
    config = Config.load(toml_path=toml)
    assert config.egress_url == "http://egressguard:8000"
    assert config.comms_url == "http://comms:8001"
```

Add to `tests/test_egress.py`:
```python
def test_build_routed_client_uses_proxy_url():
    client = build_routed_client("http://egressguard:8000")
    # The client's base_url should prepend proxy
    req = client.build_request("GET", "https://api.openai.com/v1/chat")
    assert "egressguard" in str(req.url)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_config.py::test_config_loads_egress_url tests/test_egress.py::test_build_routed_client_uses_proxy_url -v
```
Expected: both fail — `Config` has no `egress_url`, no `build_routed_client`

- [ ] **Step 3: Add fields to Config**

In `napyclaw/config.py`, add to the `Config` dataclass:
```python
    egress_url: str
    comms_url: str
```

Add to the `cls(...)` construction in `Config.load()`:
```python
            egress_url=os.environ.get("EGRESS_URL", "http://egressguard:8000"),
            comms_url=os.environ.get("COMMS_URL", "http://comms:8001"),
```

- [ ] **Step 4: Add `build_routed_client` to egress.py**

At the bottom of `napyclaw/egress.py`, add:

```python
def build_routed_client(egress_url: str, **kwargs) -> httpx.AsyncClient:
    """Build an httpx client that routes all requests through the egressguard service."""

    class _RoutingTransport(httpx.AsyncBaseTransport):
        def __init__(self, proxy_url: str) -> None:
            self._proxy = proxy_url.rstrip("/")
            self._inner = httpx.AsyncHTTPTransport()

        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            target_url = str(request.url)
            proxy_request = httpx.Request(
                method=request.method,
                url=f"{self._proxy}/proxy",
                params={"url": target_url},
                headers=request.headers,
                content=request.content,
            )
            return await self._inner.handle_async_request(proxy_request)

    return httpx.AsyncClient(transport=_RoutingTransport(egress_url), **kwargs)
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/test_config.py tests/test_egress.py -v
```
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add napyclaw/config.py napyclaw/egress.py tests/test_config.py tests/test_egress.py
git commit -m "feat: add egress_url/comms_url to Config, add build_routed_client for container proxy"
```

---

## Task 5: Add 202/stepback retry handling to ToolSystem

**Spec sections:**
- **§ EgressGuard Approval Flow — Request lifecycle** (lines 133–141) — step 4: bot's `{ToolSystem}` receives 202, surfaces "awaiting approval" to LLM, continues other work — chain is not blocked
- **§ Async retry cadence** (lines 156–173) — 202 triggers retry schedule; bot polls `/status/{token}`; `pending`/`approved`/`denied` responses

**Files:**
- Modify: `napyclaw/tools/web_search.py`
- Modify: `napyclaw/tools/base.py`
- Modify: `tests/test_tools.py`

When any tool receives a 202 from egressguard, it should return a structured `PendingApproval` result instead of a string so the agent loop can handle it and schedule retries.

- [ ] **Step 1: Write failing tests**

Add to `tests/test_tools.py`:

```python
async def test_web_search_returns_pending_on_202(monkeypatch):
    import httpx
    from napyclaw.tools.web_search import WebSearchTool

    async def mock_get(*args, **kwargs):
        return httpx.Response(202, json={"status": "pending", "token": "tok1", "retry_after": 30})

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)
    tool = WebSearchTool(config=mock_config(), egress_url="http://egressguard:8000")
    result = await tool.execute(query="test query", providers=["exa"])
    assert "pending approval" in result.lower()
    assert "tok1" in result
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_tools.py::test_web_search_returns_pending_on_202 -v
```
Expected: FAIL — `WebSearchTool` raises or returns wrong result on 202

- [ ] **Step 3: Update WebSearchTool backends to handle 202**

In `napyclaw/tools/web_search.py`, update each backend's `search()` method to check for 202:

```python
async def search(self, query: str, client: httpx.AsyncClient) -> list[SearchResult]:
    resp = await client.get(self._url, params={"q": query, "format": "json"})
    if resp.status_code == 202:
        data = resp.json()
        token = data.get("token", "unknown")
        raise PendingApprovalError(token=token, retry_after=data.get("retry_after", 30))
    resp.raise_for_status()
    # ... existing parsing ...
```

Add to `napyclaw/tools/web_search.py`:
```python
class PendingApprovalError(Exception):
    def __init__(self, token: str, retry_after: int) -> None:
        self.token = token
        self.retry_after = retry_after
        super().__init__(f"Pending approval — token: {token}")
```

In `WebSearchTool.execute()`, catch `PendingApprovalError`:
```python
    except PendingApprovalError as exc:
        return f"Search pending domain approval (token: {exc.token}). Will retry in {exc.retry_after}s."
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_tools.py -v
```
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add napyclaw/tools/web_search.py tests/test_tools.py
git commit -m "feat: handle 202 pending approval in WebSearchTool — surfaces token to LLM"
```

---

## Task 6: Add stepback retry scheduler for pending approvals

**Spec sections:**
- **§ Async retry cadence** (lines 156–173) — exact cadence: 30s → 60s → 2m → 5m → 10m → 20m → fail; callback is primary path, retry is safety net; final failure message text specified; pending token stays valid after exhaustion
- **§ EgressGuard Approval Flow — Request lifecycle** (lines 129–143) — step 6: comms fires callback to egressguard on user response; bot's next poll or callback resolves without waiting for next interval

**Files:**
- Modify: `napyclaw/scheduler.py`
- Modify: `napyclaw/agent.py`
- Modify: `tests/test_scheduler.py`

When the agent receives a `PendingApprovalError`, it registers a stepback retry job in the scheduler. The scheduler polls egressguard at 30s → 60s → 2m → 5m → 10m → 20m intervals. On approval, the original tool call is re-executed. On final timeout, a failure message is sent to the user via comms.

- [ ] **Step 1: Write failing tests**

Add to `tests/test_scheduler.py`:

```python
async def test_pending_approval_retry_schedule(mock_db):
    from napyclaw.scheduler import PendingApprovalJob, RETRY_CADENCE_SECONDS
    assert RETRY_CADENCE_SECONDS == [30, 60, 120, 300, 600, 1200]

    job = PendingApprovalJob(
        token="tok1",
        hostname="example.com",
        egress_url="http://egressguard:8000",
        original_tool="web_search",
        original_kwargs={"query": "test"},
        group_id="C123",
    )
    assert job.next_retry_delay() == 30
    job.advance()
    assert job.next_retry_delay() == 60
    for _ in range(5):
        job.advance()
    assert job.is_exhausted()


async def test_pending_approval_job_resolves_on_approved(mock_db, respx_mock):
    import httpx
    from napyclaw.scheduler import PendingApprovalJob

    respx_mock.get("http://egressguard:8000/status/tok1").mock(
        return_value=httpx.Response(200, json={"status": "approved", "token": "tok1"})
    )
    job = PendingApprovalJob(
        token="tok1",
        hostname="example.com",
        egress_url="http://egressguard:8000",
        original_tool="web_search",
        original_kwargs={"query": "test"},
        group_id="C123",
    )
    resolved = await job.poll()
    assert resolved is True
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_scheduler.py::test_pending_approval_retry_schedule tests/test_scheduler.py::test_pending_approval_job_resolves_on_approved -v
```
Expected: FAIL — `PendingApprovalJob` and `RETRY_CADENCE_SECONDS` not defined

- [ ] **Step 3: Add PendingApprovalJob to scheduler.py**

Add to `napyclaw/scheduler.py`:

```python
RETRY_CADENCE_SECONDS = [30, 60, 120, 300, 600, 1200]


class PendingApprovalJob:
    def __init__(
        self,
        token: str,
        hostname: str,
        egress_url: str,
        original_tool: str,
        original_kwargs: dict,
        group_id: str,
    ) -> None:
        self.token = token
        self.hostname = hostname
        self.egress_url = egress_url
        self.original_tool = original_tool
        self.original_kwargs = original_kwargs
        self.group_id = group_id
        self._attempt = 0

    def next_retry_delay(self) -> int:
        if self._attempt < len(RETRY_CADENCE_SECONDS):
            return RETRY_CADENCE_SECONDS[self._attempt]
        return RETRY_CADENCE_SECONDS[-1]

    def advance(self) -> None:
        self._attempt += 1

    def is_exhausted(self) -> bool:
        return self._attempt >= len(RETRY_CADENCE_SECONDS)

    async def poll(self) -> bool:
        """Poll egressguard. Returns True if approved, False if still pending or denied."""
        import httpx
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{self.egress_url}/status/{self.token}")
        status = resp.json().get("status")
        return status == "approved"
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_scheduler.py -v
```
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add napyclaw/scheduler.py tests/test_scheduler.py
git commit -m "feat: add PendingApprovalJob with stepback retry cadence 30s→20m"
```

---

## Task 7: Wire bot startup to use egress-routed client

**Spec sections:**
- **§ Network Zones — egress-net** (lines 83–85) — bot has no internet; all outbound HTTP via egressguard
- **§ Network Zones — search-net** (lines 87–90) — bot sends search queries to searxng on search-net; searxng URL is `http://searxng:8080`
- **§ Containers — bot** (lines 63) — "No — internal only" for internet access

**Files:**
- Modify: `napyclaw/__main__.py`
- Modify: `tests/test_app.py`

Replace direct `httpx.AsyncClient` construction in the bot startup with `build_routed_client(config.egress_url)`. Update the `SEARXNG_URL` in config to point at the `searxng` container hostname.

- [ ] **Step 1: Write failing test**

Add to `tests/test_app.py`:

```python
async def test_bot_uses_egress_routed_client(monkeypatch, mock_config):
    from napyclaw.egress import build_routed_client
    built = []
    monkeypatch.setattr("napyclaw.__main__.build_routed_client", lambda url, **kw: built.append(url) or build_routed_client(url, **kw))
    mock_config.egress_url = "http://egressguard:8000"
    # trigger startup
    from napyclaw.__main__ import build_app
    await build_app(mock_config)
    assert "http://egressguard:8000" in built
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_app.py::test_bot_uses_egress_routed_client -v
```
Expected: FAIL

- [ ] **Step 3: Update `__main__.py`**

Find where the httpx client or LLMClient is constructed in `napyclaw/__main__.py` and replace with:

```python
from napyclaw.egress import build_routed_client

http_client = build_routed_client(config.egress_url)
```

Pass `http_client` to `LLMClient` constructors and any tool that makes outbound HTTP.

- [ ] **Step 4: Update searxng_url default in config**

In `napyclaw/config.py`, update the `searxng_url` default:
```python
searxng_url=toml.get("search", {}).get("searxng_url") or os.environ.get("SEARXNG_URL", "http://searxng:8080"),
```

- [ ] **Step 5: Run full test suite**

```bash
pytest tests/ -v
```
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add napyclaw/__main__.py napyclaw/config.py tests/test_app.py
git commit -m "feat: wire bot to egress-routed client, resolve searxng via SEARXNG_URL env"
```

---

## Task 8: Update setup.py to document atomic mode and clarify optional secrets

**Spec sections:**
- **§ Atomic Mode** (lines 176–191) — full table of always-in-stack vs atomic vs cloud-upgrade options; comms is the only gap
- **§ What This Design Does Not Cover** (lines 195–202) — Infisical bootstrap automation is a follow-on; don't attempt full automation here

**Files:**
- Modify: `napyclaw/setup.py`

The setup wizard already has the required/optional split partially in place from the earlier Exa/Tavily work. This task completes it: adds an atomic mode section to the printed output, adds the `OWNER_CHANNEL` secret for the comms service, and documents the Infisical self-hosted option.

- [ ] **Step 1: Add OWNER_CHANNEL to required secrets**

In `napyclaw/setup.py`, in the `required_secrets` list, add after `SLACK_APP_TOKEN`:
```python
("SLACK_OWNER_CHANNEL", "C0123ABCD", "Slack channel ID where egress approvals are sent — find it in the channel URL"),
```

- [ ] **Step 2: Add atomic mode next-steps message**

After the existing `print("\nNext steps:")` block, add:

```python
    print()
    print("  Atomic mode (fully self-contained, no cloud dependencies):")
    print("  • Use Ollama for LLM inference (set default_provider = 'ollama')")
    print("  • SearXNG is already included — no Exa or Tavily key needed")
    print("  • Run Infisical from this stack: docker compose up infisical")
    print("  • Replace Slack with a self-hosted comms platform (see issue #7)")
    print()
    print("  All components except comms can run fully on-prem today.")
```

- [ ] **Step 3: Run setup wizard manually to verify output**

```bash
python -m napyclaw setup
```
Walk through the prompts. Verify:
- Required secrets table is printed first
- Optional cloud backup search secrets printed separately with explanatory text
- Atomic mode section prints at the end of next steps

- [ ] **Step 4: Commit**

```bash
git add napyclaw/setup.py
git commit -m "feat: add atomic mode guidance and OWNER_CHANNEL to setup wizard"
```

---

## Task 9: Update README

**Spec sections:**
- **§ Container Topology** (lines 14–53) — use this diagram verbatim in the README
- **§ Atomic Mode** (lines 176–191) — use this table as the basis for the new "Atomic mode" README section
- **§ Containers** (lines 59–70) — source for the updated container description prose

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update the architecture diagram**

Find the existing flow diagram in README (the block starting `Message arrives (Slack Socket Mode)`) and replace it entirely with this diagram verbatim:

```
╔══════════════════════════════════════════════════════════════════════════════╗
║  EXTERNAL                                                                    ║
║  Slack · Mattermost        Exa · Tavily · LLM APIs        Google·Bing·DDG   ║
╚════════════╤══════════════════════════════╤═══════════════════════╤══════════╝
             │ ▲                            │ ▲                     │ ▲
             ▼ │                            ▼ │                     ▼ │
  ┌───────────────────┐             ┌────────────────────────┐  ┌─────────────┐
  │       comms       │◄─approvals──│      egressguard       │  │   searxng   │
  │  {proto adapter}  │             │  {domain allowlist}    │  │ {meta-search}│
  │    stateless      │             │  {LLMClient}           │  │             │
  │                   │             │  {exfil sanitize}      │  │             │
  └───────────────────┘             └────────────────────────┘  └─────────────┘
             │ ▲                            │ ▲                     │ ▲
             ▼ │                            ▼ │                     ▼ │
╔══════════════════════════════════════════════════════════════════════════════╗
║  ┌──────────────────────────────────────────────────────────────────────┐   ║
║  │ bot                                                                  │   ║
║  ├──────────────────────────────────────────────────────────────────────┤   ║
║  │ {InjectionGuard}   inbound prompt + outbound query scan              │   ║
║  ├──────────────────────────────────────────────────────────────────────┤   ║
║  │ {ToolSystem}   web_search · file · send_message · scheduler          │   ║
║  ├──────────────────────────────────────────────────────────────────────┤   ║
║  │ {LLMClient}    Ollama · OpenAI · Foundry · Bedrock                   │   ║
║  ├──────────────────────────────────────────────────────────────────────┤   ║
║  │ {ContentShield}    scans all content before DB write                 │   ║
║  ├──────────────────────────────────────────────────────────────────────┤   ║
║  │ {GroupContext}     per-channel identity · history · memory           │   ║
║  └──────────────────────────────────────────────────────────────────────┘   ║
╚══════════════════════════════════════════════════════════════════════════════╝
              │                                               │
              ▼                                               ▼
  ┌─────────────────────────┐               ┌─────────────────────────┐
  │           db            │               │        infisical         │
  │   postgres + pgvector   │               │   secrets (self-hosted)  │
  │       no internet       │               │       no internet        │
  └─────────────────────────┘               └─────────────────────────┘
```

**Legend:** container boxes · `{application layer}`

- [ ] **Step 2: Update the "how it works" prose**

Update the description that currently mentions "Slack Socket Mode" as the entry point to describe the comms container as the messaging adapter.

- [ ] **Step 3: Add "Atomic mode" section**

After the provider choices table, add a new section:

```markdown
### Atomic mode

Every component in napyclaw can run on your own infrastructure with no external service dependencies:

| Layer | Self-hosted option |
|---|---|
| LLM | Ollama — runs locally or over Tailscale |
| Search | SearXNG — included in `docker-compose.yml` |
| Secrets | Infisical — included in `docker-compose.yml` |
| Comms | Self-hosted Mattermost or Matrix (issue [#7](https://github.com/napyclaw/napyclaw/issues/7)) |
| Database | PostgreSQL + pgvector — always local |
| Egress control | egressguard — always local |

In atomic mode, traffic only leaves your infrastructure through three scoped lanes:
- **`comms-net`** — messaging platform only
- **`egress-net`** — LLM APIs and cloud search (Exa, Tavily) — optional, only if you choose cloud providers
- **`search-net`** — SearXNG to search engines

The only current gap is the comms layer (Slack is not self-hostable) — tracked in issue [#7](https://github.com/napyclaw/napyclaw/issues/7).
```

- [ ] **Step 4: Update the Exposed network port row in the comparison table**

Find the row:
```
| Exposed network port | ... | ✅ No listener. Slack Socket Mode is outbound-only. ...
```
Update to:
```
| Exposed network port | ... | ✅ No listener. comms, egressguard, and searxng containers handle all outbound — bot has no internet access.
```

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs: update README for 6-container atomic mode architecture"
```

---

## Task 10: Smoke test full stack locally

**Spec sections:**
- **§ Container Topology** (lines 14–53) — verify the running stack matches this diagram
- **§ Network Zones** (lines 74–105) — verify bot cannot reach internet directly; verify each container is on only its intended networks

**Files:** none — verification only

- [ ] **Step 1: Build all images**

```bash
docker compose build
```
Expected: all 3 custom images (`bot`, `egressguard`, `comms`) build without errors

- [ ] **Step 2: Start infrastructure only**

```bash
docker compose up db infisical searxng -d
```
Expected: all three start healthy

- [ ] **Step 3: Start services**

```bash
docker compose up egressguard comms -d
```
Expected: both start, logs show FastAPI on correct ports

- [ ] **Step 4: Test egressguard manually**

```bash
curl "http://localhost:8000/proxy?url=https://api.openai.com/v1/models"
```
Expected: proxied response or 202 pending (depending on whether OPENAI is in allowlist — it should be)

- [ ] **Step 5: Run full test suite**

```bash
pytest tests/ -v
```
Expected: all pass

- [ ] **Step 6: Push**

```bash
git push
```

---

## Self-Review Checklist

**Spec coverage:**
- ✅ 6-container topology → Tasks 1, 2, 3
- ✅ 4 Docker networks → Task 1
- ✅ 202 async approval flow → Task 2 (egressguard service)
- ✅ Stepback retry cadence 30s→20m → Task 6
- ✅ Approve once vs approve always → Task 2
- ✅ Comms as approval router → Tasks 3, 2
- ✅ EgressGuard LLMClient judge → Task 2 (static allowlist for now; LLM judge is in existing `egress.py` and wired via `build_routed_client`)
- ✅ Bot has no direct internet → Tasks 1, 4, 7
- ✅ SearXNG as container on search-net → Task 1
- ✅ Infisical in stack → Task 1
- ✅ Setup wizard atomic mode guidance → Task 8
- ✅ README update → Task 9

**Out of scope (as per spec):**
- Allowlist management UI
- Comms container for self-hosted Mattermost/Matrix (issue #7)
- Full Infisical bootstrap automation (noted in spec as follow-on)
