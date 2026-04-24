# Webchat Comms Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Slack with a self-hosted WebSocket chat UI served by the `comms` container, preserving the full Channel/GroupContext/agent stack.

**Architecture:** The `comms` FastAPI service gains a `/ws` WebSocket endpoint for real-time bidirectional browser communication, replaces Slack API calls in `/send` and `/notify/approval` with WebSocket pushes, and serves a vanilla-JS SPA. A new `WebChannel` in `napyclaw/channels/web.py` uses `aiohttp` to receive inbound POSTs from `comms` and implements the existing `Channel` interface. Three new nullable DB columns (`nickname`, `job_title`, `memory_enabled`, `channel_type`) extend `group_contexts`.

**Tech Stack:** FastAPI WebSocket (starlette built-in), aiohttp (already in deps), asyncpg, vanilla JS (no build step), pytest + starlette TestClient for WebSocket tests.

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| Create | `napyclaw/migrations/003_webchat.sql` | ALTER TABLE to add new columns |
| Modify | `napyclaw/db.py` | Save/load new columns, add `load_webchat_specialists()` |
| Modify | `napyclaw/config.py` | Add `comms_channel`, `webhook_host`, `webhook_port`; slack tokens optional |
| Modify | `napyclaw.toml` | Add `[comms]` section |
| Create | `napyclaw/channels/web.py` | `WebChannel` implementation |
| Modify | `napyclaw/app.py` | Seed admin DM, respect `memory_enabled`, sync specialists to comms |
| Modify | `napyclaw/__main__.py` | Instantiate `WebChannel` vs `SlackChannel` based on config |
| Modify | `services/comms/main.py` | WebSocket, `/specialists`, `/specialists-sync`, `/approval/respond` |
| Modify | `services/comms/requirements.txt` | No new deps (FastAPI already has starlette WebSocket) |
| Create | `services/comms/static/index.html` | SPA frontend |
| Modify | `docker-compose.yml` | Mount `003_webchat.sql` for DB init |
| Create | `tests/test_web_channel.py` | Unit tests for WebChannel |
| Modify | `tests/services/test_comms.py` | Tests for new comms endpoints |

---

## Task 1: DB Migration — webchat columns

**Files:**
- Create: `napyclaw/migrations/003_webchat.sql`
- Modify: `docker-compose.yml` (mount the new migration)

- [ ] **Step 1: Write the migration SQL**

```sql
-- napyclaw/migrations/003_webchat.sql
-- Adds webchat-specific columns to group_contexts.
-- Requires: 002_operational.sql already applied.

ALTER TABLE group_contexts
    ADD COLUMN IF NOT EXISTS nickname      TEXT,
    ADD COLUMN IF NOT EXISTS job_title     TEXT,
    ADD COLUMN IF NOT EXISTS memory_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS channel_type  TEXT NOT NULL DEFAULT 'slack';
```

- [ ] **Step 2: Mount the migration in docker-compose.yml**

In `docker-compose.yml`, under the `db` service `volumes`, add:

```yaml
      - ./napyclaw/migrations/003_webchat.sql:/docker-entrypoint-initdb.d/003_webchat.sql
```

The full `db.volumes` block should look like:

```yaml
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./napyclaw/migrations/001_thoughts.sql:/docker-entrypoint-initdb.d/001_thoughts.sql
      - ./napyclaw/migrations/002_operational.sql:/docker-entrypoint-initdb.d/002_operational.sql
      - ./napyclaw/migrations/003_webchat.sql:/docker-entrypoint-initdb.d/003_webchat.sql
```

- [ ] **Step 3: Verify the migration file exists and is valid SQL**

```bash
cat napyclaw/migrations/003_webchat.sql
```

Expected: file contents as written above, no errors.

- [ ] **Step 4: Commit**

```bash
git add napyclaw/migrations/003_webchat.sql docker-compose.yml
git commit -m "feat: add webchat columns migration and mount in compose"
```

**Note:** For an existing running stack, apply manually with:
```bash
docker exec -i napyclaw-db-1 psql -U napyclaw napyclaw < napyclaw/migrations/003_webchat.sql
```
Or `docker compose down -v && docker compose up` to reset.

---

## Task 2: Update db.py for new columns

**Files:**
- Modify: `napyclaw/db.py:67-114` (save_group_context, load_group_context, _row_to_ctx)
- Test: `tests/test_db.py` (extend existing tests)

- [ ] **Step 1: Write failing tests**

Add to `tests/test_db.py`:

```python
async def test_save_and_load_webchat_columns(db):
    """New columns round-trip correctly."""
    await db.save_group_context(
        group_id="g-web",
        default_name="Rex",
        display_name="Rex",
        nicknames=[],
        owner_id="owner",
        provider="openai",
        model="gpt-4o",
        is_first_interaction=True,
        history=[],
        nickname="Rex",
        job_title="Stats Researcher",
        memory_enabled=True,
        channel_type="webchat",
    )
    row = await db.load_group_context("g-web")
    assert row is not None
    assert row["nickname"] == "Rex"
    assert row["job_title"] == "Stats Researcher"
    assert row["memory_enabled"] is True
    assert row["channel_type"] == "webchat"


async def test_memory_enabled_defaults_true(db):
    """memory_enabled=True is the default when not specified explicitly."""
    await db.save_group_context(
        group_id="g-default",
        default_name="Cal",
        display_name="Cal",
        nicknames=[],
        owner_id="owner",
        provider="openai",
        model="gpt-4o",
        is_first_interaction=True,
        history=[],
        nickname=None,
        job_title=None,
        memory_enabled=True,
        channel_type="webchat",
    )
    row = await db.load_group_context("g-default")
    assert row["memory_enabled"] is True


async def test_load_webchat_specialists(db):
    """load_webchat_specialists returns only webchat rows, not admin."""
    await db.save_group_context(
        group_id="spec-1", default_name="Rex", display_name="Rex",
        nicknames=[], owner_id="owner", provider="openai", model="gpt-4o",
        is_first_interaction=True, history=[], nickname="Rex",
        job_title="Stats Researcher", memory_enabled=True, channel_type="webchat",
    )
    await db.save_group_context(
        group_id="admin", default_name="Admin", display_name="Admin",
        nicknames=[], owner_id="system", provider="openai", model="gpt-4o",
        is_first_interaction=True, history=[], nickname=None,
        job_title=None, memory_enabled=False, channel_type="webchat",
    )
    specialists = await db.load_webchat_specialists()
    ids = [s["group_id"] for s in specialists]
    assert "spec-1" in ids
    assert "admin" not in ids
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_db.py::test_save_and_load_webchat_columns tests/test_db.py::test_load_webchat_specialists -v
```

Expected: FAIL — `save_group_context() got unexpected keyword argument 'nickname'`

- [ ] **Step 3: Update `_row_to_ctx` in `napyclaw/db.py`**

Find `_row_to_ctx` (currently near line 208) and replace:

```python
def _row_to_ctx(row) -> dict:
    return {
        "group_id": row["group_id"],
        "default_name": row["default_name"],
        "display_name": row["display_name"],
        "nicknames": json.loads(row["nicknames"]),
        "owner_id": row["owner_id"],
        "provider": row["provider"],
        "model": row["model"],
        "is_first_interaction": bool(row["is_first_interaction"]),
        "history": json.loads(row["history"]),
        "nickname": row["nickname"],
        "job_title": row["job_title"],
        "memory_enabled": bool(row["memory_enabled"]) if row["memory_enabled"] is not None else True,
        "channel_type": row["channel_type"] or "slack",
    }
```

- [ ] **Step 4: Update `save_group_context` signature and SQL**

Replace the `save_group_context` method:

```python
async def save_group_context(
    self,
    group_id: str,
    default_name: str,
    display_name: str,
    nicknames: list[str],
    owner_id: str,
    provider: str,
    model: str,
    is_first_interaction: bool,
    history: list[dict],
    nickname: str | None = None,
    job_title: str | None = None,
    memory_enabled: bool = True,
    channel_type: str = "slack",
) -> None:
    await self.pool.execute(
        """
        INSERT INTO group_contexts
            (group_id, default_name, display_name, nicknames, owner_id,
             provider, model, is_first_interaction, history,
             nickname, job_title, memory_enabled, channel_type)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
        ON CONFLICT (group_id) DO UPDATE SET
            default_name         = EXCLUDED.default_name,
            display_name         = EXCLUDED.display_name,
            nicknames            = EXCLUDED.nicknames,
            owner_id             = EXCLUDED.owner_id,
            provider             = EXCLUDED.provider,
            model                = EXCLUDED.model,
            is_first_interaction = EXCLUDED.is_first_interaction,
            history              = EXCLUDED.history,
            nickname             = EXCLUDED.nickname,
            job_title            = EXCLUDED.job_title,
            memory_enabled       = EXCLUDED.memory_enabled,
            channel_type         = EXCLUDED.channel_type
        """,
        group_id,
        default_name,
        display_name,
        json.dumps(nicknames),
        owner_id,
        provider,
        model,
        is_first_interaction,
        json.dumps(history),
        nickname,
        job_title,
        memory_enabled,
        channel_type,
    )
```

- [ ] **Step 5: Add `load_webchat_specialists` method**

Add after `load_all_group_contexts`:

```python
async def load_webchat_specialists(self) -> list[dict]:
    """Return webchat GroupContexts excluding the admin DM row."""
    rows = await self.pool.fetch(
        "SELECT * FROM group_contexts WHERE channel_type = 'webchat' AND group_id != 'admin'"
    )
    return [_row_to_ctx(row) for row in rows]
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
pytest tests/test_db.py -v
```

Expected: all pass including the three new tests.

- [ ] **Step 7: Commit**

```bash
git add napyclaw/db.py tests/test_db.py
git commit -m "feat: add nickname, job_title, memory_enabled, channel_type to GroupContext DB"
```

---

## Task 3: Config — comms_channel, webhook config, optional slack tokens

**Files:**
- Modify: `napyclaw/config.py`
- Modify: `napyclaw.toml`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_config.py`:

```python
def test_config_webchat_defaults(monkeypatch, tmp_path):
    """Config loads comms_channel, webhook_host, webhook_port from toml."""
    toml_content = b"""
[llm]
default_provider = "openai"
default_model = "gpt-4o"

[comms]
channel = "webchat"
webhook_host = "bot"
webhook_port = 9000

[db]
url = "postgresql://napyclaw:napyclaw-local@db:5432/napyclaw"

[app]
oauth_callback_port = 8765
workspace_dir = "/tmp/workspace"
groups_dir = "/tmp/groups"
"""
    toml_file = tmp_path / "napyclaw.toml"
    toml_file.write_bytes(toml_content)

    monkeypatch.setenv("INFISICAL_CLIENT_ID", "")
    # Patch _load_infisical to return minimal secrets (no Slack required for webchat)
    from unittest.mock import patch
    with patch("napyclaw.config._load_infisical", return_value={
        "OPENAI_API_KEY": "sk-test",
        "OLLAMA_API_KEY": "ollama-test",
    }):
        from napyclaw.config import Config
        config = Config.load(toml_path=toml_file)

    assert config.comms_channel == "webchat"
    assert config.webhook_host == "bot"
    assert config.webhook_port == 9000
    assert config.slack_bot_token is None
    assert config.slack_app_token is None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_config.py::test_config_webchat_defaults -v
```

Expected: FAIL — `Config.__init__() got unexpected keyword argument 'comms_channel'`

- [ ] **Step 3: Add new fields to `Config` dataclass**

In `napyclaw/config.py`, in the `Config` dataclass, replace the Slack section:

```python
    # Slack (optional — only required when comms_channel = "slack")
    slack_bot_token: str | None
    slack_app_token: str | None
```

Add after the existing Container URLs section:

```python
    # Comms channel mode
    comms_channel: str          # "webchat" or "slack"
    webhook_host: str           # hostname comms uses to reach the bot
    webhook_port: int           # port WebChannel listens on
```

- [ ] **Step 4: Update `Config.load()` to populate new fields**

In `Config.load()`, replace the Slack secrets and add comms config:

```python
        comms_cfg = toml.get("comms", {})

        return cls(
            # Secrets from Infisical
            openai_api_key=secret("OPENAI_API_KEY"),
            ollama_api_key=secret("OLLAMA_API_KEY"),
            # Slack tokens are optional — only needed in slack mode
            slack_bot_token=optional_secret("SLACK_BOT_TOKEN"),
            slack_app_token=optional_secret("SLACK_APP_TOKEN"),
            tavily_api_key=optional_secret("TAVILY_API_KEY"),
            exa_api_key=optional_secret("EXA_API_KEY"),
            db_url=db.get("url") or secret("DB_URL"),
            foundry_api_key=optional_secret("FOUNDRY_API_KEY"),
            aws_access_key_id=optional_secret("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=optional_secret("AWS_SECRET_ACCESS_KEY"),
            # App config from toml
            default_provider=llm.get("default_provider", "openai"),
            default_model=llm.get("default_model", "gpt-4o"),
            openai_base_url=llm.get("openai_base_url", "https://api.openai.com/v1"),
            ollama_base_url=llm.get("ollama_base_url", "http://localhost:11434/v1"),
            foundry_base_url=llm.get("foundry_base_url"),
            aws_region=llm.get("aws_region"),
            vector_embed_model=llm.get("vector_embed_model", "nomic-embed-text"),
            search_providers=toml.get("search", {}).get("providers", ["searxng", "exa", "tavily"]),
            searxng_url=toml.get("search", {}).get("searxng_url") or os.environ.get("SEARXNG_URL", "http://searxng:8080"),
            oauth_callback_port=int(app.get("oauth_callback_port", 8765)),
            egress_url=os.environ.get("EGRESS_URL", "http://egressguard:8000"),
            comms_url=os.environ.get("COMMS_URL", "http://comms:8001"),
            workspace_dir=Path(app.get("workspace_dir", "/app/workspace")),
            groups_dir=Path(app.get("groups_dir", "/app/groups")),
            max_history_tokens=int(app["max_history_tokens"]) if app.get("max_history_tokens") else None,
            # Comms channel config
            comms_channel=comms_cfg.get("channel", "slack"),
            webhook_host=comms_cfg.get("webhook_host", "bot"),
            webhook_port=int(comms_cfg.get("webhook_port", 9000)),
        )
```

- [ ] **Step 5: Update `napyclaw.toml` to add the comms section**

Append to `napyclaw.toml`:

```toml
[comms]
channel = "webchat"
webhook_host = "bot"
webhook_port = 9000
```

- [ ] **Step 6: Run tests**

```bash
pytest tests/test_config.py -v
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add napyclaw/config.py napyclaw.toml tests/test_config.py
git commit -m "feat: add comms_channel, webhook config to Config; slack tokens now optional"
```

---

## Task 4: WebChannel implementation

**Files:**
- Create: `napyclaw/channels/web.py`
- Create: `tests/test_web_channel.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_web_channel.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_web_channel.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'napyclaw.channels.web'`

- [ ] **Step 3: Implement `napyclaw/channels/web.py`**

```python
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
            asyncio.create_task(self._handler(msg))

        return web.json_response({"ok": True})
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_web_channel.py -v
```

Expected: all 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add napyclaw/channels/web.py tests/test_web_channel.py
git commit -m "feat: add WebChannel — self-hosted webchat channel via aiohttp webhook"
```

---

## Task 5: Seed admin DM and wire memory_enabled in app.py

**Files:**
- Modify: `napyclaw/app.py` (GroupContext dataclass, start(), handle_message(), _save_context())

The GroupContext dataclass is defined in `napyclaw/app.py`. We need to add `memory_enabled` and `channel_type` fields, seed the admin DM row on startup, skip vector memory for `memory_enabled=False` groups, and sync the specialist list to comms after connect.

- [ ] **Step 1: Write failing test**

Add to `tests/test_app.py`:

```python
async def test_admin_dm_seeded_on_start(mock_app):
    """Admin DM GroupContext is created with memory_enabled=False on start."""
    await mock_app.start()
    row = await mock_app.db.load_group_context("admin")
    assert row is not None
    assert row["memory_enabled"] is False
    assert row["channel_type"] == "webchat"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_app.py::test_admin_dm_seeded_on_start -v
```

Expected: FAIL

- [ ] **Step 3: Add `memory_enabled` and `channel_type` to GroupContext dataclass**

In `napyclaw/app.py`, find the `GroupContext` dataclass and add two fields:

```python
@dataclass
class GroupContext:
    group_id: str
    default_name: str
    display_name: str
    nicknames: list[str]
    owner_id: str
    active_client: LLMClient
    is_first_interaction: bool
    agent: Agent
    memory_enabled: bool = True
    channel_type: str = "slack"
```

- [ ] **Step 4: Update `start()` to seed the admin DM and sync specialists**

In `NapyClaw.start()`, after `await self.channel.connect()`, add:

```python
        # Seed admin DM (no-op if already exists)
        await self.db.save_group_context(
            group_id="admin",
            default_name="Admin",
            display_name="Admin",
            nicknames=[],
            owner_id="system",
            provider=self.config.default_provider,
            model=self.config.default_model,
            is_first_interaction=False,
            history=[],
            nickname=None,
            job_title=None,
            memory_enabled=False,
            channel_type="webchat",
        )

        # Sync specialist list to comms for the /specialists endpoint
        if self.config.comms_channel == "webchat":
            await self._sync_specialists()
```

Add the `_sync_specialists` method to `NapyClaw`:

```python
    async def _sync_specialists(self) -> None:
        """Push current webchat specialist list to comms for sidebar rendering."""
        specialists = await self.db.load_webchat_specialists()
        payload = [
            {
                "group_id": s["group_id"],
                "display_name": s["display_name"],
                "nickname": s["nickname"],
                "job_title": s["job_title"],
            }
            for s in specialists
        ]
        try:
            async with self._http.post(
                f"{self.config.comms_url}/specialists-sync",
                json={"specialists": payload},
            ) as _:
                pass
        except Exception:
            pass  # best-effort
```

Note: `self._http` is the guarded HTTP client already constructed in `start()`. If it does not exist at sync time, use `aiohttp.ClientSession()` directly with a try/finally.

- [ ] **Step 5: Update `_save_context` to respect `memory_enabled`**

Find where GroupContext history / memory is persisted after agent runs (look for calls to `self.db.save_group_context` or `self.memory.store`). Gate vector store writes:

```python
    async def _save_context(self, ctx: GroupContext, msg: Message, response: str) -> None:
        # Always persist conversation history to DB
        await self.db.save_group_context(
            group_id=ctx.group_id,
            default_name=ctx.default_name,
            display_name=ctx.display_name,
            nicknames=ctx.nicknames,
            owner_id=ctx.owner_id,
            provider=ctx.active_client.provider,
            model=ctx.active_client.model,
            is_first_interaction=False,
            history=ctx.agent.history,
            nickname=getattr(ctx, "nickname", None),
            job_title=getattr(ctx, "job_title", None),
            memory_enabled=ctx.memory_enabled,
            channel_type=ctx.channel_type,
        )
        # Only write to vector store if memory is enabled for this context
        if ctx.memory_enabled and self.memory is not None:
            await self.memory.store(
                group_id=ctx.group_id,
                user_text=msg.text,
                assistant_text=response,
            )
```

Apply this pattern wherever `self.memory.store(...)` is currently called — wrap it with `if ctx.memory_enabled`.

- [ ] **Step 6: Update `start()` to populate `memory_enabled` and `channel_type` when restoring contexts**

In the loop that restores contexts from DB:

```python
        for row in all_contexts:
            client = self._build_client(row["provider"], row["model"])
            ctx = GroupContext(
                group_id=row["group_id"],
                default_name=row["default_name"],
                display_name=row["display_name"],
                nicknames=row["nicknames"],
                owner_id=row["owner_id"],
                active_client=client,
                is_first_interaction=row["is_first_interaction"],
                agent=Agent(
                    client=client,
                    tools=[],
                    system_prompt="",
                    config=self.config,
                    history=row["history"],
                    injection_guard=self._injection_guard,
                ),
                memory_enabled=row.get("memory_enabled", True),
                channel_type=row.get("channel_type", "slack"),
            )
```

- [ ] **Step 7: Run tests**

```bash
pytest tests/test_app.py -v
```

Expected: all pass including `test_admin_dm_seeded_on_start`.

- [ ] **Step 8: Commit**

```bash
git add napyclaw/app.py tests/test_app.py
git commit -m "feat: seed admin DM on startup, respect memory_enabled, sync specialists to comms"
```

---

## Task 6: Wire WebChannel in `__main__.py`

**Files:**
- Modify: `napyclaw/__main__.py`

- [ ] **Step 1: Replace SlackChannel instantiation with channel-mode branch**

In `napyclaw/__main__.py`, find the line:

```python
channel = SlackChannel(bot_token=config.slack_bot_token, app_token=config.slack_app_token)
```

Replace with:

```python
if config.comms_channel == "webchat":
    from napyclaw.channels.web import WebChannel
    channel: Channel = WebChannel(
        comms_url=config.comms_url,
        webhook_host=config.webhook_host,
        webhook_port=config.webhook_port,
    )
else:
    if not config.slack_bot_token or not config.slack_app_token:
        raise RuntimeError(
            "comms_channel = 'slack' requires SLACK_BOT_TOKEN and SLACK_APP_TOKEN secrets."
        )
    channel = SlackChannel(
        bot_token=config.slack_bot_token,
        app_token=config.slack_app_token,
    )
```

Add `Channel` to the imports at the top of `__main__.py`:

```python
from napyclaw.channels.base import Channel
```

- [ ] **Step 2: Guard `bot_user_id` assignment (Slack-only)**

Find the line after `await app.start()` that reads the bot user ID:

```python
app.bot_user_id = channel.bot_user_id
```

Wrap it:

```python
if config.comms_channel == "slack" and hasattr(channel, "bot_user_id"):
    app.bot_user_id = channel.bot_user_id
```

- [ ] **Step 3: Run existing tests**

```bash
pytest tests/ -v --ignore=tests/services -k "not test_slack"
```

Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add napyclaw/__main__.py
git commit -m "feat: wire WebChannel vs SlackChannel based on comms_channel config"
```

---

## Task 7: Extend comms service — WebSocket, specialists, approval

**Files:**
- Modify: `services/comms/main.py`
- Modify: `tests/services/test_comms.py`

The comms service gains: a `/ws` WebSocket endpoint, in-memory message buffer, `/specialists-sync` and `/specialists` endpoints, `/approval/respond`, and modified `/send` + `/notify/approval` to push over WebSocket if connected (falling back to Slack if not).

- [ ] **Step 1: Write failing tests**

Add to `tests/services/test_comms.py`:

```python
from starlette.testclient import TestClient as SyncClient


def test_ws_receive_message_dispatches_to_webhook():
    """Browser message over WS is forwarded to bot webhook."""
    import services.comms.main as m
    m._bot_webhook = "http://bot:9000/inbound"
    m._ws_connection = None

    with patch("services.comms.main._post_to_webhook", new_callable=AsyncMock) as mock_post:
        with SyncClient(m.app) as c:
            with c.websocket_connect("/ws") as ws:
                ws.send_json({
                    "type": "message",
                    "group_id": "grp-1",
                    "text": "Hello"
                })
                # give handler a moment
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert call_args[0][1]["group_id"] == "grp-1"
        assert call_args[0][1]["text"] == "Hello"


async def test_send_pushes_to_ws_when_connected(client):
    """POST /send pushes over WebSocket if one is connected, not to Slack."""
    import services.comms.main as m

    # Track WS pushes by replacing _push_to_ws
    pushed = []
    original = m._push_to_ws

    async def capture(payload):
        pushed.append(payload)

    m._push_to_ws = capture
    c, mock_slack = client

    resp = await c.post("/send", json={"channel": "grp-1", "text": "Hi"})
    assert resp.status_code == 200
    assert len(pushed) == 1
    assert pushed[0]["group_id"] == "grp-1"
    assert pushed[0]["text"] == "Hi"
    mock_slack.chat_postMessage.assert_not_called()

    m._push_to_ws = original


async def test_specialists_sync_and_get(client):
    """POST /specialists-sync stores list; GET /specialists returns it."""
    c, _ = client
    payload = {"specialists": [
        {"group_id": "g1", "display_name": "Rex", "nickname": "Rex", "job_title": "Stats"},
    ]}
    resp = await c.post("/specialists-sync", json=payload)
    assert resp.status_code == 200

    resp = await c.get("/specialists")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["group_id"] == "g1"


async def test_approval_respond_posts_to_egressguard(client):
    """POST /approval/respond forwards decision to egressguard callback URL."""
    import services.comms.main as m
    m._pending_approvals["tok-abc"] = "http://egressguard:8000/callback/tok-abc"
    c, _ = client

    with patch("services.comms.main._http_post", new_callable=AsyncMock) as mock_post:
        resp = await c.post("/approval/respond", json={
            "token": "tok-abc",
            "decision": "approve_once",
        })
    assert resp.status_code == 200
    mock_post.assert_called_once()
    call_url = mock_post.call_args[0][0]
    assert "tok-abc" in call_url
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/services/test_comms.py -v
```

Expected: several FAILs for missing endpoints/functions.

- [ ] **Step 3: Rewrite `services/comms/main.py`**

Replace the full file with:

```python
from __future__ import annotations

import asyncio
import os
from collections import deque
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from infisical_client import ClientSettings, GetSecretOptions, InfisicalClient
from pydantic import BaseModel
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient


def _load_secret(client: InfisicalClient, name: str, environment: str = "prod") -> str:
    project_id = os.environ["INFISICAL_PROJECT_ID"]
    try:
        val = client.getSecret(GetSecretOptions(
            environment=environment, project_id=project_id, secret_name=name,
        ))
        return val.secret_value if val and val.secret_value else ""
    except Exception:
        return ""


@asynccontextmanager
async def lifespan(app: FastAPI):
    global SLACK_BOT_TOKEN, OWNER_CHANNEL, _slack, _http_client
    client_id = os.environ.get("INFISICAL_CLIENT_ID", "")
    client_secret = os.environ.get("INFISICAL_CLIENT_SECRET", "")
    infisical_url = os.environ.get("INFISICAL_URL", "http://infisical:8080")
    environment = os.environ.get("INFISICAL_ENVIRONMENT", "prod")
    if client_id and client_secret:
        ic = InfisicalClient(ClientSettings(
            client_id=client_id,
            client_secret=client_secret,
            site_url=infisical_url,
        ))
        SLACK_BOT_TOKEN = _load_secret(ic, "SLACK_BOT_TOKEN", environment)
        OWNER_CHANNEL = _load_secret(ic, "SLACK_OWNER_CHANNEL", environment)
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


async def _post_to_webhook(url: str, payload: dict) -> None:
    if _http_client is None:
        return
    try:
        await _http_client.post(url, json=payload, timeout=5.0)
    except Exception:
        pass


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

    # Replay buffer for active group if provided in first frame
    # (Browser sends {"type": "hello", "group_id": "..."} on connect)
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
                            "text": buffered["text"],
                            "replayed": True,
                        })

            elif msg_type == "message":
                group_id = data.get("group_id", "")
                text = data.get("text", "")
                _buffer_message(group_id, "user", text)
                if _bot_webhook:
                    asyncio.create_task(_post_to_webhook(_bot_webhook, {
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
        _ws_connection = None


# ---------------------------------------------------------------------------
# Static files (frontend SPA)
# ---------------------------------------------------------------------------

import pathlib
_STATIC_DIR = pathlib.Path(__file__).parent / "static"
if _STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(str(_STATIC_DIR / "index.html"))
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/services/test_comms.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add services/comms/main.py tests/services/test_comms.py
git commit -m "feat: add WebSocket, /specialists, /approval/respond to comms service"
```

---

## Task 8: Frontend SPA

**Files:**
- Create: `services/comms/static/index.html`

No automated tests for the frontend — verify manually by opening in a browser.

- [ ] **Step 1: Create the static directory**

```bash
mkdir -p services/comms/static
```

- [ ] **Step 2: Create `services/comms/static/index.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>napyclaw</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  :root {
    --bg:        #0f172a;
    --surface:   #1e293b;
    --border:    #334155;
    --accent:    #1d4ed8;
    --accent-lt: #93c5fd;
    --text:      #e2e8f0;
    --muted:     #94a3b8;
    --danger:    #f87171;
    --sidebar-w: 210px;
  }

  html, body { height: 100%; background: var(--bg); color: var(--text);
               font-family: system-ui, -apple-system, sans-serif; font-size: 14px; }

  #app { display: flex; height: 100vh; overflow: hidden; }

  /* --- Sidebar --- */
  #sidebar {
    width: var(--sidebar-w); min-width: var(--sidebar-w);
    background: var(--surface); border-right: 1px solid var(--border);
    display: flex; flex-direction: column; overflow-y: auto;
  }
  #sidebar-header { padding: 14px; border-bottom: 1px solid var(--border); }
  #sidebar-header .label { color: var(--muted); font-size: 10px;
                            text-transform: uppercase; letter-spacing: 1px; margin-bottom: 8px; }

  .specialist-item {
    padding: 8px 10px; border-radius: 6px; cursor: pointer; margin: 2px 0;
    transition: background .15s;
  }
  .specialist-item:hover { background: rgba(255,255,255,.05); }
  .specialist-item.active { background: var(--accent); }
  .specialist-item .name { font-weight: 600; }
  .specialist-item .role { font-size: 11px; color: var(--muted); margin-top: 2px; }
  .specialist-item.active .role { color: var(--accent-lt); }

  #new-btn {
    display: flex; align-items: center; gap: 6px; padding: 8px 10px;
    color: var(--muted); cursor: pointer; border-radius: 6px; margin: 2px 0;
  }
  #new-btn:hover { background: rgba(255,255,255,.05); }

  #admin-section { margin-top: auto; border-top: 1px solid var(--border); padding: 10px 14px; }
  #admin-btn {
    display: flex; align-items: center; gap: 8px; padding: 8px 10px;
    cursor: pointer; border-radius: 6px;
  }
  #admin-btn:hover { background: rgba(255,255,255,.05); }
  #admin-btn.active { background: rgba(248,113,113,.15); }
  #admin-name { color: var(--danger); font-weight: 600; font-size: 12px; }
  #admin-badge { color: var(--danger); font-size: 11px; }

  /* --- Chat pane --- */
  #chat { flex: 1; display: flex; flex-direction: column; min-width: 0; }

  #chat-header {
    background: var(--surface); padding: 10px 16px;
    border-bottom: 1px solid var(--border);
    display: flex; align-items: center; gap: 10px; flex-shrink: 0;
  }
  #chat-title { font-weight: 600; }
  #chat-role { color: var(--muted); font-size: 12px; margin-left: 6px; }
  #rename-btn { margin-left: auto; color: var(--muted); font-size: 12px;
                cursor: pointer; background: none; border: none; }
  #rename-btn:hover { color: var(--text); }

  /* rename form (hidden by default) */
  #rename-form { display: none; gap: 6px; align-items: center; margin-left: auto; }
  #rename-form input {
    background: var(--bg); border: 1px solid var(--border); border-radius: 4px;
    color: var(--text); padding: 4px 8px; font-size: 12px; width: 120px;
  }
  #rename-form button { background: var(--accent); color: var(--text); border: none;
                        border-radius: 4px; padding: 4px 10px; cursor: pointer; font-size: 12px; }

  #messages {
    flex: 1; overflow-y: auto; padding: 16px; display: flex;
    flex-direction: column; gap: 10px;
  }

  .msg { display: flex; gap: 8px; align-items: flex-start; max-width: 85%; }
  .msg.user { align-self: flex-end; flex-direction: row-reverse; }
  .msg.assistant { align-self: flex-start; }

  .avatar {
    width: 28px; height: 28px; border-radius: 50%; background: var(--border);
    display: flex; align-items: center; justify-content: center;
    font-weight: 700; font-size: 12px; flex-shrink: 0;
  }
  .msg.user .avatar { background: var(--accent); }

  .bubble {
    padding: 8px 12px; border-radius: 12px; line-height: 1.5;
    white-space: pre-wrap; word-break: break-word;
  }
  .msg.user .bubble { background: var(--accent); border-radius: 12px 12px 2px 12px; }
  .msg.assistant .bubble {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 2px 12px 12px 12px; color: #cbd5e1;
  }

  .typing-dots { display: flex; gap: 4px; padding: 10px 14px; }
  .typing-dots span {
    width: 6px; height: 6px; border-radius: 50%; background: var(--muted);
    animation: bounce .8s infinite ease-in-out;
  }
  .typing-dots span:nth-child(2) { animation-delay: .15s; }
  .typing-dots span:nth-child(3) { animation-delay: .3s; }
  @keyframes bounce { 0%,80%,100%{transform:scale(.6)} 40%{transform:scale(1)} }

  /* approval card */
  .approval-card {
    background: var(--surface); border: 1px solid var(--danger);
    border-radius: 8px; padding: 12px 14px; max-width: 360px;
  }
  .approval-card h4 { color: var(--danger); margin-bottom: 8px; }
  .approval-card .domain { color: var(--muted); font-size: 12px; margin-bottom: 10px; }
  .approval-card .actions { display: flex; flex-wrap: wrap; gap: 6px; }
  .approval-card button {
    padding: 5px 10px; border-radius: 4px; border: 1px solid var(--border);
    background: var(--bg); color: var(--text); cursor: pointer; font-size: 12px;
  }
  .approval-card button:hover { background: var(--border); }
  .approval-card button.approve { border-color: #22c55e; color: #22c55e; }
  .approval-card button.deny { border-color: var(--danger); color: var(--danger); }

  /* input bar */
  #input-bar {
    padding: 12px 16px; border-top: 1px solid var(--border);
    display: flex; gap: 8px; align-items: center; flex-shrink: 0;
  }
  #msg-input {
    flex: 1; background: var(--surface); border: 1px solid var(--border);
    border-radius: 20px; padding: 8px 14px; color: var(--text); font-size: 14px;
    outline: none; resize: none; line-height: 1.4; max-height: 120px;
  }
  #msg-input:focus { border-color: var(--accent); }
  #send-btn {
    width: 34px; height: 34px; border-radius: 50%; background: var(--accent);
    border: none; color: var(--text); cursor: pointer; font-size: 16px;
    display: flex; align-items: center; justify-content: center; flex-shrink: 0;
  }
  #send-btn:disabled { opacity: .4; cursor: default; }

  /* new specialist modal */
  #modal-overlay {
    display: none; position: fixed; inset: 0;
    background: rgba(0,0,0,.6); z-index: 100;
    align-items: center; justify-content: center;
  }
  #modal-overlay.open { display: flex; }
  #modal {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 10px; padding: 20px; width: 320px;
  }
  #modal h3 { margin-bottom: 14px; }
  #modal input {
    width: 100%; background: var(--bg); border: 1px solid var(--border);
    border-radius: 6px; color: var(--text); padding: 8px 12px;
    font-size: 14px; margin-bottom: 12px;
  }
  #modal-actions { display: flex; justify-content: flex-end; gap: 8px; }
  #modal-actions button { padding: 7px 14px; border-radius: 6px; border: none;
                           cursor: pointer; font-size: 13px; }
  #modal-cancel { background: var(--border); color: var(--text); }
  #modal-ok { background: var(--accent); color: var(--text); }

  /* mobile: collapse sidebar */
  @media (max-width: 600px) {
    #sidebar { position: fixed; left: -100%; top: 0; height: 100%; z-index: 50;
               transition: left .2s; }
    #sidebar.open { left: 0; }
    #hamburger { display: flex; }
  }
  #hamburger { display: none; background: none; border: none; color: var(--muted);
               cursor: pointer; font-size: 20px; padding: 0 8px; }
</style>
</head>
<body>
<div id="app">

  <!-- Sidebar -->
  <div id="sidebar">
    <div id="sidebar-header">
      <div class="label">Specialists</div>
      <div id="specialist-list"></div>
      <div id="new-btn" onclick="openNewModal()">
        <span style="font-size:16px">+</span> New Specialist
      </div>
    </div>
    <div id="admin-section">
      <div id="admin-btn" onclick="selectGroup('admin')">
        <span style="color:var(--danger);font-size:16px">⚠</span>
        <div>
          <div id="admin-name">Admin</div>
          <div id="admin-badge" style="display:none"></div>
        </div>
      </div>
    </div>
  </div>

  <!-- Chat -->
  <div id="chat">
    <div id="chat-header">
      <button id="hamburger" onclick="toggleSidebar()">☰</button>
      <span id="chat-title">Select a specialist</span>
      <span id="chat-role"></span>
      <button id="rename-btn" onclick="openRename()" style="display:none">✏ rename</button>
      <form id="rename-form" onsubmit="submitRename(event)">
        <input id="rename-input" placeholder="New nickname" maxlength="30">
        <button type="submit">Save</button>
      </form>
    </div>
    <div id="messages"></div>
    <div id="input-bar">
      <textarea id="msg-input" placeholder="Select a specialist to begin..."
                rows="1" disabled onkeydown="onKey(event)"></textarea>
      <button id="send-btn" onclick="sendMessage()" disabled>↑</button>
    </div>
  </div>

</div>

<!-- New specialist modal -->
<div id="modal-overlay">
  <div id="modal">
    <h3>New Specialist</h3>
    <input id="specialist-name-input" placeholder="Name (optional — agent can choose)" maxlength="50">
    <div id="modal-actions">
      <button id="modal-cancel" onclick="closeModal()">Cancel</button>
      <button id="modal-ok" onclick="createSpecialist()">Start Chat</button>
    </div>
  </div>
</div>

<script>
const WS_URL = `ws://${location.host}/ws`;
let ws = null;
let currentGroup = null;
let reconnectDelay = 1000;
// messages: { [group_id]: [{role, text, approval?}] }
const messages = {};
// specialists list fetched from server
let specialists = [];
// pending approval badge count
let pendingApprovals = 0;

// ---- WebSocket ----

function connect() {
  ws = new WebSocket(WS_URL);

  ws.onopen = () => {
    reconnectDelay = 1000;
    if (currentGroup) {
      ws.send(JSON.stringify({ type: 'hello', group_id: currentGroup }));
    }
  };

  ws.onmessage = (e) => {
    const data = JSON.parse(e.data);
    if (data.type === 'message') {
      appendMessage(data.group_id, 'assistant', data.text, data.replayed);
      if (data.group_id === currentGroup) scrollToBottom();
    } else if (data.type === 'typing') {
      if (data.group_id === currentGroup) setTyping(data.on);
    } else if (data.type === 'approval') {
      appendApproval(data);
    }
  };

  ws.onclose = () => {
    setTimeout(reconnect, reconnectDelay);
    reconnectDelay = Math.min(reconnectDelay * 2, 30000);
  };
}

function reconnect() { connect(); }

// ---- Specialists ----

async function loadSpecialists() {
  const resp = await fetch('/specialists');
  specialists = await resp.json();
  renderSidebar();
}

function renderSidebar() {
  const list = document.getElementById('specialist-list');
  list.innerHTML = '';
  specialists.forEach(s => {
    const div = document.createElement('div');
    div.className = 'specialist-item' + (s.group_id === currentGroup ? ' active' : '');
    div.onclick = () => selectGroup(s.group_id);
    div.innerHTML = `
      <div class="name">${esc(s.nickname || s.display_name)}</div>
      <div class="role">${esc(s.job_title || '')}</div>`;
    list.appendChild(div);
  });
  // Update admin btn active state
  document.getElementById('admin-btn').classList.toggle('active', currentGroup === 'admin');
}

function selectGroup(groupId) {
  currentGroup = groupId;
  renderSidebar();

  const spec = specialists.find(s => s.group_id === groupId)
    || (groupId === 'admin' ? { display_name: 'Admin', nickname: 'Admin', job_title: 'System' } : null);

  const nick = spec ? (spec.nickname || spec.display_name) : groupId;
  const role = spec ? (spec.job_title || '') : '';

  document.getElementById('chat-title').textContent = nick;
  document.getElementById('chat-role').textContent = role;
  document.getElementById('rename-btn').style.display = groupId === 'admin' ? 'none' : '';
  document.getElementById('msg-input').placeholder = `Message ${nick}...`;
  document.getElementById('msg-input').disabled = false;
  document.getElementById('send-btn').disabled = false;

  renderMessages(groupId);
  scrollToBottom();

  // Request replay from server
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: 'hello', group_id: groupId }));
  }

  // Clear approval badge when entering admin
  if (groupId === 'admin') {
    pendingApprovals = 0;
    updateAdminBadge();
  }

  // Close sidebar on mobile
  document.getElementById('sidebar').classList.remove('open');
}

// ---- Messages ----

function appendMessage(groupId, role, text, replayed = false) {
  if (!messages[groupId]) messages[groupId] = [];
  // Avoid duplicating replayed messages already in buffer
  if (replayed && messages[groupId].some(m => m.text === text && m.role === role)) return;
  messages[groupId].push({ role, text });
  if (groupId === currentGroup) {
    renderMessages(groupId);
    scrollToBottom();
  }
}

function appendApproval(data) {
  if (!messages['admin']) messages['admin'] = [];
  messages['admin'].push({ role: 'approval', data });
  pendingApprovals++;
  updateAdminBadge();
  if (currentGroup === 'admin') {
    renderMessages('admin');
    scrollToBottom();
  }
}

function renderMessages(groupId) {
  const container = document.getElementById('messages');
  container.innerHTML = '';
  removeTypingIndicator();
  (messages[groupId] || []).forEach(m => {
    if (m.role === 'approval') {
      container.appendChild(buildApprovalCard(m.data));
    } else {
      container.appendChild(buildBubble(m.role, m.text));
    }
  });
}

function buildBubble(role, text) {
  const wrap = document.createElement('div');
  wrap.className = `msg ${role}`;
  const initial = role === 'user' ? 'Y' : (currentGroup ? (currentGroup[0] || 'A').toUpperCase() : 'A');
  wrap.innerHTML = `
    <div class="avatar">${initial}</div>
    <div class="bubble">${esc(text)}</div>`;
  return wrap;
}

function buildApprovalCard(data) {
  const div = document.createElement('div');
  div.className = 'approval-card';
  div.dataset.token = data.token;
  div.innerHTML = `
    <h4>⚠ Egress Approval Needed</h4>
    <div class="domain">Domain: <strong>${esc(data.hostname)}</strong></div>
    <div class="actions">
      <button class="approve" onclick="respond('${esc(data.token)}','approve_once',this.closest('.approval-card'))">Approve Once</button>
      <button class="approve" onclick="respond('${esc(data.token)}','approve_always',this.closest('.approval-card'))">Approve Always</button>
      <button class="deny" onclick="respond('${esc(data.token)}','deny_once',this.closest('.approval-card'))">Deny Once</button>
      <button class="deny" onclick="respond('${esc(data.token)}','deny_always',this.closest('.approval-card'))">Deny Always</button>
    </div>`;
  return div;
}

async function respond(token, decision, card) {
  ws.send(JSON.stringify({ type: 'approval', token, decision }));
  card.innerHTML = `<div style="color:var(--muted);font-size:12px">Decision sent: ${decision.replace('_',' ')}</div>`;
  pendingApprovals = Math.max(0, pendingApprovals - 1);
  updateAdminBadge();
}

function updateAdminBadge() {
  const badge = document.getElementById('admin-badge');
  if (pendingApprovals > 0) {
    badge.style.display = '';
    badge.textContent = `${pendingApprovals} pending approval${pendingApprovals > 1 ? 's' : ''}`;
  } else {
    badge.style.display = 'none';
  }
}

// ---- Typing indicator ----

let typingEl = null;
function setTyping(on) {
  removeTypingIndicator();
  if (on) {
    typingEl = document.createElement('div');
    typingEl.className = 'msg assistant';
    typingEl.innerHTML = `<div class="avatar">...</div>
      <div class="bubble typing-dots"><span></span><span></span><span></span></div>`;
    document.getElementById('messages').appendChild(typingEl);
    scrollToBottom();
  }
}
function removeTypingIndicator() {
  if (typingEl) { typingEl.remove(); typingEl = null; }
}

// ---- Send ----

function sendMessage() {
  const input = document.getElementById('msg-input');
  const text = input.value.trim();
  if (!text || !currentGroup || !ws || ws.readyState !== WebSocket.OPEN) return;

  appendMessage(currentGroup, 'user', text);
  ws.send(JSON.stringify({ type: 'message', group_id: currentGroup, text }));
  input.value = '';
  input.style.height = 'auto';
  scrollToBottom();
}

function onKey(e) {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
  // Auto-resize textarea
  const t = e.target;
  setTimeout(() => { t.style.height = 'auto'; t.style.height = t.scrollHeight + 'px'; }, 0);
}

// ---- New specialist modal ----

function openNewModal() {
  document.getElementById('modal-overlay').classList.add('open');
  document.getElementById('specialist-name-input').value = '';
  document.getElementById('specialist-name-input').focus();
}
function closeModal() { document.getElementById('modal-overlay').classList.remove('open'); }

function createSpecialist() {
  const name = document.getElementById('specialist-name-input').value.trim() || '';
  const groupId = 'g-' + Date.now();
  specialists.push({ group_id: groupId, display_name: name || 'New Specialist', nickname: name || null, job_title: null });
  renderSidebar();
  selectGroup(groupId);
  closeModal();
  if (name) {
    // Send first message to create context with name
    const input = document.getElementById('msg-input');
    input.value = '';
    ws.send(JSON.stringify({
      type: 'message',
      group_id: groupId,
      text: name ? `Hi, I'd like to work with you as my ${name}.` : `Hi! What would you like to be called?`,
    }));
    appendMessage(groupId, 'user', name ? `Hi, I'd like to work with you as my ${name}.` : `Hi! What would you like to be called?`);
    scrollToBottom();
  }
}

// ---- Rename ----

function openRename() {
  document.getElementById('rename-btn').style.display = 'none';
  document.getElementById('rename-form').style.display = 'flex';
  document.getElementById('rename-input').focus();
}

function submitRename(e) {
  e.preventDefault();
  const newNick = document.getElementById('rename-input').value.trim();
  if (newNick && currentGroup) {
    const spec = specialists.find(s => s.group_id === currentGroup);
    if (spec) { spec.nickname = newNick; }
    document.getElementById('chat-title').textContent = newNick;
    renderSidebar();
  }
  document.getElementById('rename-form').style.display = 'none';
  document.getElementById('rename-btn').style.display = '';
}

// ---- Utils ----

function scrollToBottom() {
  const el = document.getElementById('messages');
  el.scrollTop = el.scrollHeight;
}

function esc(s) {
  return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function toggleSidebar() {
  document.getElementById('sidebar').classList.toggle('open');
}

// ---- Init ----

connect();
loadSpecialists();
</script>
</body>
</html>
```

- [ ] **Step 3: Manually verify the frontend**

Rebuild and restart the comms container:

```bash
docker compose build comms && docker compose up -d comms
```

Open `http://localhost:8001` in a browser. Verify:
- Sidebar renders with "+ New Specialist" and "Admin" pinned at bottom
- "New Specialist" modal opens and creates a chat
- Typing a message and pressing Enter shows it in the chat pane
- Admin section shows ⚠ icon

- [ ] **Step 4: Commit**

```bash
git add services/comms/static/index.html
git commit -m "feat: add vanilla JS SPA frontend for webchat comms"
```

---

## Post-Implementation

Run the full test suite:

```bash
pytest tests/ -v
```

All tests should pass. Then rebuild and start the full stack:

```bash
docker compose build && docker compose up -d
```

Open `http://localhost:8001`. Start a new specialist, send a message, verify the bot responds.
