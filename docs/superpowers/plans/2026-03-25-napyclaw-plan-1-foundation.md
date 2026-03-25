# napyclaw Plan 1: Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Set up the project skeleton, core data types, Config (Infisical), and Database layer that all subsequent plans build on.

**Architecture:** Pure Python package with `pyproject.toml`, async SQLite via aiosqlite for persistence, Infisical Cloud for all secrets. No application logic in this plan — just the plumbing every other component depends on.

**Tech Stack:** Python 3.11+, aiosqlite, infisical-python, pytest, pytest-asyncio

---

## File Map

Files created in this plan:

```
pyproject.toml
.gitignore
napyclaw/__init__.py
napyclaw/__main__.py                  — entry point stub
napyclaw/config.py                    — Config dataclass + ConfigError
napyclaw/db.py                        — Database class + ScheduledTask dataclass
napyclaw/channels/__init__.py
napyclaw/channels/base.py             — Message dataclass + Channel ABC
napyclaw/models/__init__.py
napyclaw/models/base.py               — ChatResponse, ToolCall dataclasses + LLMClient ABC
napyclaw/tools/__init__.py
napyclaw/migrations/001_thoughts.sql  — PostgreSQL + pgvector schema
tests/__init__.py
tests/conftest.py
tests/test_types.py
tests/test_config.py
tests/test_db.py
```

---

### Task 1: Project Skeleton

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `napyclaw/__init__.py`
- Create: `napyclaw/channels/__init__.py`
- Create: `napyclaw/models/__init__.py`
- Create: `napyclaw/tools/__init__.py`
- Create: `tests/__init__.py`

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.backends.legacy:build"

[project]
name = "napyclaw"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "aiosqlite>=0.20",
    "infisical-python>=2.1",
    "openai>=1.30",
    "httpx>=0.27",
    "slack-bolt>=1.18",
    "apscheduler>=3.10",
    "asyncpg>=0.29",
    "detect-secrets>=1.4",
    "presidio-analyzer>=2.2",
    "presidio-anonymizer>=2.2",
    "spacy>=3.7",
]

[project.optional-dependencies]
dev = [
    "pytest>=8",
    "pytest-asyncio>=0.23",
    "pytest-mock>=3.12",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
```

- [ ] **Step 2: Create empty package init files**

```bash
mkdir -p napyclaw/channels napyclaw/models napyclaw/tools napyclaw/migrations tests
touch napyclaw/__init__.py napyclaw/channels/__init__.py napyclaw/models/__init__.py napyclaw/tools/__init__.py tests/__init__.py
```

- [ ] **Step 3: Create `.gitignore`**

```
__pycache__/
*.py[cod]
*.egg-info/
dist/
.venv/
*.db
*.sqlite
```

- [ ] **Step 4: Install dependencies**

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
python -m spacy download en_core_web_lg
```

Expected: No errors. `pip show napyclaw` shows the package.

- [ ] **Step 5: Verify pytest collects with no errors**

```bash
pytest --collect-only
```

Expected: `no tests ran` (0 items collected, no errors)

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml napyclaw/ tests/ .gitignore
git commit -m "feat: project skeleton"
```

---

### Task 2: Core Data Types

**Files:**
- Create: `napyclaw/channels/base.py`
- Create: `napyclaw/models/base.py`
- Create: `tests/test_types.py`

- [ ] **Step 1: Write failing tests**

`tests/test_types.py`:
```python
from napyclaw.channels.base import Message
from napyclaw.models.base import ChatResponse, ToolCall


def test_message_fields():
    msg = Message(
        group_id="C0123ABC",
        channel_name="general",
        sender_id="U0123ABC",
        sender_name="Alice",
        text="hello",
        timestamp="2026-03-24T12:00:00Z",
        channel_type="slack",
    )
    assert msg.group_id == "C0123ABC"
    assert msg.channel_type == "slack"


def test_tool_call_fields():
    tc = ToolCall(id="call_1", name="web_search", arguments={"query": "test"})
    assert tc.name == "web_search"
    assert tc.arguments == {"query": "test"}


def test_chat_response_text_only():
    resp = ChatResponse(text="Hello!", tool_calls=None, finish_reason="stop")
    assert resp.text == "Hello!"
    assert resp.tool_calls is None


def test_chat_response_tool_calls():
    tc = ToolCall(id="call_1", name="web_search", arguments={"query": "test"})
    resp = ChatResponse(text=None, tool_calls=[tc], finish_reason="tool_calls")
    assert resp.text is None
    assert len(resp.tool_calls) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_types.py -v
```

Expected: `ImportError: No module named 'napyclaw.channels.base'`

- [ ] **Step 3: Create `napyclaw/channels/base.py`**

```python
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass


@dataclass
class Message:
    group_id: str
    channel_name: str
    sender_id: str
    sender_name: str
    text: str
    timestamp: str
    channel_type: str


class Channel(ABC):
    channel_type: str

    def __init__(self) -> None:
        self._handler: Callable[[Message], Awaitable[None]] | None = None

    def register_handler(self, handler: Callable[[Message], Awaitable[None]]) -> None:
        self._handler = handler

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def disconnect(self) -> None: ...

    @abstractmethod
    async def send(self, group_id: str, text: str) -> None: ...

    @abstractmethod
    async def set_typing(self, group_id: str, on: bool) -> None: ...
```

- [ ] **Step 4: Create `napyclaw/models/base.py`**

```python
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class ChatResponse:
    text: str | None
    tool_calls: list[ToolCall] | None
    finish_reason: str


class LLMClient(ABC):
    model: str
    provider: str
    context_window: int

    @abstractmethod
    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> ChatResponse: ...

    @abstractmethod
    async def stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> AsyncIterator[str]: ...
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/test_types.py -v
```

Expected: 4 passed

- [ ] **Step 6: Commit**

```bash
git add napyclaw/channels/base.py napyclaw/models/base.py tests/test_types.py
git commit -m "feat: core data types — Message, ChatResponse, ToolCall, LLMClient ABC"
```

---

### Task 3: Config

**Files:**
- Create: `napyclaw/config.py`
- Create: `tests/test_config.py`

`Config` loads all secrets from Infisical Cloud using machine identity auth. The three bootstrap values needed to reach Infisical (`INFISICAL_CLIENT_ID`, `INFISICAL_CLIENT_SECRET`, `INFISICAL_PROJECT_ID`) live as system environment variables. Everything else is fetched via the SDK.

- [ ] **Step 1: Write failing Config tests**

`tests/test_config.py`:
```python
import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path
from napyclaw.config import Config, ConfigError


def _make_infisical_client(secrets: dict) -> MagicMock:
    """Returns a mock InfisicalClient whose getSecret() returns values from `secrets`."""
    client = MagicMock()

    def get_secret(options):
        name = options.secret_name
        if name not in secrets:
            raise KeyError(name)
        result = MagicMock()
        result.secretValue = secrets[name]
        return result

    client.getSecret.side_effect = get_secret
    return client


FULL_SECRETS = {
    "OPENAI_API_KEY": "sk-test",
    "OPENAI_BASE_URL": "https://api.openai.com/v1",
    "OLLAMA_BASE_URL": "http://100.1.2.3:11434/v1",
    "OLLAMA_API_KEY": "ollama",
    "DEFAULT_MODEL": "llama3.3:latest",
    "DEFAULT_PROVIDER": "ollama",
    "SLACK_BOT_TOKEN": "xoxb-test",
    "SLACK_APP_TOKEN": "xapp-test",
    "BRAVE_API_KEY": "brave-test",
    "VECTOR_DB_URL": "postgresql://localhost:5432/napyclaw",
    "VECTOR_EMBED_MODEL": "nomic-embed-text",
    "OAUTH_CALLBACK_PORT": "8765",
    "WORKSPACE_DIR": "/tmp/napyclaw/workspace",
    "DB_PATH": "/tmp/napyclaw/napyclaw.db",
    "GROUPS_DIR": "/tmp/napyclaw/groups",
}

BOOTSTRAP_ENV = {
    "INFISICAL_CLIENT_ID": "id",
    "INFISICAL_CLIENT_SECRET": "secret",
    "INFISICAL_PROJECT_ID": "proj",
}


def test_config_loads_all_fields():
    mock_client = _make_infisical_client(FULL_SECRETS)
    with patch("napyclaw.config.InfisicalClient", return_value=mock_client), \
         patch.dict("os.environ", BOOTSTRAP_ENV):
        config = Config.from_infisical()

    assert config.openai_api_key == "sk-test"
    assert config.default_provider == "ollama"
    assert config.oauth_callback_port == 8765
    assert isinstance(config.workspace_dir, Path)
    assert config.vector_db_url == "postgresql://localhost:5432/napyclaw"
    assert config.max_history_tokens is None


def test_config_vector_db_url_optional():
    secrets = {**FULL_SECRETS}
    del secrets["VECTOR_DB_URL"]
    mock_client = _make_infisical_client(secrets)
    with patch("napyclaw.config.InfisicalClient", return_value=mock_client), \
         patch.dict("os.environ", BOOTSTRAP_ENV):
        config = Config.from_infisical()

    assert config.vector_db_url is None


def test_config_max_history_tokens_optional():
    secrets = {**FULL_SECRETS, "MAX_HISTORY_TOKENS": "4000"}
    mock_client = _make_infisical_client(secrets)
    with patch("napyclaw.config.InfisicalClient", return_value=mock_client), \
         patch.dict("os.environ", BOOTSTRAP_ENV):
        config = Config.from_infisical()

    assert config.max_history_tokens == 4000


def test_config_missing_required_field_raises():
    secrets = {**FULL_SECRETS}
    del secrets["OPENAI_API_KEY"]
    mock_client = _make_infisical_client(secrets)
    with patch("napyclaw.config.InfisicalClient", return_value=mock_client), \
         patch.dict("os.environ", BOOTSTRAP_ENV):
        with pytest.raises(ConfigError, match="OPENAI_API_KEY"):
            Config.from_infisical()


def test_config_missing_client_id_raises():
    with patch.dict("os.environ", {"INFISICAL_CLIENT_SECRET": "s", "INFISICAL_PROJECT_ID": "p"},
                    clear=True):
        with pytest.raises(ConfigError, match="INFISICAL_CLIENT_ID"):
            Config.from_infisical()


def test_config_missing_client_secret_raises():
    with patch.dict("os.environ", {"INFISICAL_CLIENT_ID": "i", "INFISICAL_PROJECT_ID": "p"},
                    clear=True):
        with pytest.raises(ConfigError, match="INFISICAL_CLIENT_SECRET"):
            Config.from_infisical()


def test_config_missing_project_id_raises():
    with patch.dict("os.environ", {"INFISICAL_CLIENT_ID": "i", "INFISICAL_CLIENT_SECRET": "s"},
                    clear=True):
        with pytest.raises(ConfigError, match="INFISICAL_PROJECT_ID"):
            Config.from_infisical()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_config.py -v
```

Expected: `ImportError: No module named 'napyclaw.config'`

- [ ] **Step 3: Create `napyclaw/config.py`**

```python
import os
from dataclasses import dataclass
from pathlib import Path

from infisical_client import ClientSettings, GetSecretOptions, InfisicalClient


class ConfigError(Exception):
    pass


@dataclass
class Config:
    # LLM
    openai_api_key: str
    openai_base_url: str
    ollama_base_url: str
    ollama_api_key: str
    default_model: str
    default_provider: str

    # Slack
    slack_bot_token: str
    slack_app_token: str

    # Web search
    brave_api_key: str

    # Vector memory (optional)
    vector_db_url: str | None
    vector_embed_model: str

    # OAuth
    oauth_callback_port: int

    # Paths
    workspace_dir: Path
    db_path: Path
    groups_dir: Path

    # Agent tuning (optional)
    max_history_tokens: int | None

    @classmethod
    def from_infisical(cls) -> "Config":
        client_id = os.environ.get("INFISICAL_CLIENT_ID")
        client_secret = os.environ.get("INFISICAL_CLIENT_SECRET")
        project_id = os.environ.get("INFISICAL_PROJECT_ID")

        if not client_id:
            raise ConfigError(
                "Cannot connect to Infisical. Check INFISICAL_CLIENT_ID and "
                "INFISICAL_CLIENT_SECRET environment variables."
            )
        if not client_secret:
            raise ConfigError(
                "Cannot connect to Infisical. Check INFISICAL_CLIENT_ID and "
                "INFISICAL_CLIENT_SECRET environment variables."
            )
        if not project_id:
            raise ConfigError(
                "Cannot connect to Infisical. Check INFISICAL_PROJECT_ID environment variable."
            )

        try:
            client = InfisicalClient(
                ClientSettings(client_id=client_id, client_secret=client_secret)
            )
        except Exception as exc:
            raise ConfigError(
                "Cannot connect to Infisical. Check INFISICAL_CLIENT_ID and "
                "INFISICAL_CLIENT_SECRET environment variables."
            ) from exc

        def require(name: str) -> str:
            try:
                result = client.getSecret(
                    GetSecretOptions(
                        environment="prod",
                        project_id=project_id,
                        secret_name=name,
                    )
                )
                return result.secretValue
            except Exception:
                raise ConfigError(f"Missing required config: {name}")

        def optional(name: str) -> str | None:
            try:
                result = client.getSecret(
                    GetSecretOptions(
                        environment="prod",
                        project_id=project_id,
                        secret_name=name,
                    )
                )
                return result.secretValue
            except Exception:
                return None

        max_history_raw = optional("MAX_HISTORY_TOKENS")

        return cls(
            openai_api_key=require("OPENAI_API_KEY"),
            openai_base_url=require("OPENAI_BASE_URL"),
            ollama_base_url=require("OLLAMA_BASE_URL"),
            ollama_api_key=require("OLLAMA_API_KEY"),
            default_model=require("DEFAULT_MODEL"),
            default_provider=require("DEFAULT_PROVIDER"),
            slack_bot_token=require("SLACK_BOT_TOKEN"),
            slack_app_token=require("SLACK_APP_TOKEN"),
            brave_api_key=require("BRAVE_API_KEY"),
            vector_db_url=optional("VECTOR_DB_URL"),
            vector_embed_model=require("VECTOR_EMBED_MODEL"),
            oauth_callback_port=int(require("OAUTH_CALLBACK_PORT")),
            workspace_dir=Path(require("WORKSPACE_DIR")),
            db_path=Path(require("DB_PATH")),
            groups_dir=Path(require("GROUPS_DIR")),
            max_history_tokens=int(max_history_raw) if max_history_raw else None,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_config.py -v
```

Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add napyclaw/config.py tests/test_config.py
git commit -m "feat: Config class with Infisical integration"
```

---

### Task 4: Database

**Files:**
- Create: `napyclaw/db.py`
- Create: `tests/test_db.py`

- [ ] **Step 1: Write failing DB tests**

`tests/test_db.py`:
```python
import pytest
import uuid
from datetime import datetime, timezone
from pathlib import Path

from napyclaw.db import Database, ScheduledTask


@pytest.fixture
async def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "test.db")
    await database.init()
    return database


async def test_init_creates_tables(db: Database):
    # If init() ran without error and we can query, schema is correct
    tasks = await db.list_scheduled_tasks("group-1")
    assert tasks == []


async def test_save_and_load_group_context(db: Database):
    await db.save_group_context(
        group_id="C001",
        default_name="General_napy",
        display_name="General_napy",
        nicknames=[],
        owner_id="U001",
        provider="ollama",
        model="llama3.3:latest",
        is_first_interaction=True,
        history=[],
    )
    ctx = await db.load_group_context("C001")
    assert ctx is not None
    assert ctx["display_name"] == "General_napy"
    assert ctx["nicknames"] == []
    assert ctx["is_first_interaction"] is True
    assert ctx["history"] == []


async def test_load_group_context_missing_returns_none(db: Database):
    result = await db.load_group_context("nonexistent")
    assert result is None


async def test_update_group_context(db: Database):
    await db.save_group_context(
        group_id="C001",
        default_name="General_napy",
        display_name="General_napy",
        nicknames=[],
        owner_id="U001",
        provider="ollama",
        model="llama3.3:latest",
        is_first_interaction=True,
        history=[],
    )
    await db.save_group_context(
        group_id="C001",
        default_name="General_napy",
        display_name="Kevin",
        nicknames=["Kev"],
        owner_id="U001",
        provider="openai",
        model="gpt-4o",
        is_first_interaction=False,
        history=[{"role": "user", "content": "hi"}],
    )
    ctx = await db.load_group_context("C001")
    assert ctx["display_name"] == "Kevin"
    assert ctx["nicknames"] == ["Kev"]
    assert ctx["is_first_interaction"] is False
    assert ctx["history"] == [{"role": "user", "content": "hi"}]


async def test_load_all_group_contexts(db: Database):
    for i in range(3):
        await db.save_group_context(
            group_id=f"C00{i}",
            default_name=f"Chan{i}_napy",
            display_name=f"Chan{i}_napy",
            nicknames=[],
            owner_id="U001",
            provider="ollama",
            model="llama3.3:latest",
            is_first_interaction=True,
            history=[],
        )
    all_ctx = await db.load_all_group_contexts()
    assert len(all_ctx) == 3


async def test_save_and_list_scheduled_tasks(db: Database):
    task_id = str(uuid.uuid4())
    task = ScheduledTask(
        id=task_id,
        group_id="C001",
        owner_id="U001",
        prompt="Say hello",
        schedule_type="interval",
        schedule_value="3600",
        model=None,
        provider=None,
        status="active",
        next_run="2026-03-25T12:00:00Z",
        retry_count=0,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    await db.save_scheduled_task(task)
    tasks = await db.list_scheduled_tasks("C001")
    assert len(tasks) == 1
    assert tasks[0].id == task_id
    assert tasks[0].prompt == "Say hello"


async def test_list_due_tasks(db: Database):
    past = "2026-01-01T00:00:00Z"
    future = "2099-01-01T00:00:00Z"
    now = "2026-03-25T12:00:00Z"

    for next_run, task_id in [(past, "t1"), (future, "t2")]:
        await db.save_scheduled_task(ScheduledTask(
            id=task_id,
            group_id="C001",
            owner_id="U001",
            prompt="test",
            schedule_type="once",
            schedule_value=next_run,
            model=None,
            provider=None,
            status="active",
            next_run=next_run,
            retry_count=0,
            created_at=now,
        ))

    due = await db.list_due_tasks(now)
    assert len(due) == 1
    assert due[0].id == "t1"


async def test_update_task_status(db: Database):
    task_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    await db.save_scheduled_task(ScheduledTask(
        id=task_id,
        group_id="C001",
        owner_id="U001",
        prompt="test",
        schedule_type="interval",
        schedule_value="3600",
        model=None,
        provider=None,
        status="active",
        next_run=now,
        retry_count=0,
        created_at=now,
    ))
    await db.update_task_status(task_id, "paused")
    tasks = await db.list_scheduled_tasks("C001")
    assert tasks[0].status == "paused"


async def test_save_message(db: Database):
    # No exception = pass; messages table is write-only in v1
    await db.save_message(
        id="msg1",
        group_id="C001",
        sender_id="U001",
        sender_name="Alice",
        text="hello",
        timestamp="2026-03-25T12:00:00Z",
        channel_type="slack",
    )


async def test_log_shield_detection(db: Database):
    # No exception = pass; shield_log is append-only
    await db.log_shield_detection(
        id="shield1",
        group_id="C001",
        sender_id="U001",
        detection_types=["api_key"],
        timestamp="2026-03-25T12:00:00Z",
    )
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_db.py -v
```

Expected: `ImportError: No module named 'napyclaw.db'`

- [ ] **Step 3: Create `napyclaw/db.py`**

```python
import json
from dataclasses import dataclass
from pathlib import Path

import aiosqlite


@dataclass
class ScheduledTask:
    id: str
    group_id: str
    owner_id: str
    prompt: str
    schedule_type: str
    schedule_value: str
    model: str | None
    provider: str | None
    status: str
    next_run: str | None
    retry_count: int
    created_at: str


_SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS messages (
    id           TEXT PRIMARY KEY,
    group_id     TEXT NOT NULL,
    sender_id    TEXT NOT NULL,
    sender_name  TEXT NOT NULL,
    text         TEXT NOT NULL,
    timestamp    TEXT NOT NULL,
    channel_type TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS group_contexts (
    group_id             TEXT PRIMARY KEY,
    default_name         TEXT NOT NULL,
    display_name         TEXT NOT NULL,
    nicknames            TEXT NOT NULL DEFAULT '[]',
    owner_id             TEXT NOT NULL,
    provider             TEXT NOT NULL,
    model                TEXT NOT NULL,
    is_first_interaction INTEGER NOT NULL DEFAULT 1,
    history              TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id             TEXT PRIMARY KEY,
    group_id       TEXT NOT NULL,
    owner_id       TEXT NOT NULL,
    prompt         TEXT NOT NULL,
    schedule_type  TEXT NOT NULL,
    schedule_value TEXT NOT NULL,
    model          TEXT,
    provider       TEXT,
    status         TEXT NOT NULL DEFAULT 'active',
    next_run       TEXT,
    retry_count    INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_run_log (
    id             TEXT PRIMARY KEY,
    task_id        TEXT NOT NULL,
    ran_at         TEXT NOT NULL,
    status         TEXT NOT NULL,
    result_snippet TEXT,
    duration_ms    INTEGER
);

CREATE TABLE IF NOT EXISTS shield_log (
    id              TEXT PRIMARY KEY,
    group_id        TEXT NOT NULL,
    sender_id       TEXT NOT NULL,
    detection_types TEXT NOT NULL DEFAULT '[]',
    timestamp       TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: Path) -> None:
        self._path = path

    async def init(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self._path) as db:
            await db.executescript(_SCHEMA)
            await db.commit()

    async def save_message(
        self,
        id: str,
        group_id: str,
        sender_id: str,
        sender_name: str,
        text: str,
        timestamp: str,
        channel_type: str,
    ) -> None:
        async with aiosqlite.connect(self._path) as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO messages
                    (id, group_id, sender_id, sender_name, text, timestamp, channel_type)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (id, group_id, sender_id, sender_name, text, timestamp, channel_type),
            )
            await db.commit()

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
    ) -> None:
        async with aiosqlite.connect(self._path) as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO group_contexts
                    (group_id, default_name, display_name, nicknames, owner_id,
                     provider, model, is_first_interaction, history)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    group_id,
                    default_name,
                    display_name,
                    json.dumps(nicknames),
                    owner_id,
                    provider,
                    model,
                    1 if is_first_interaction else 0,
                    json.dumps(history),
                ),
            )
            await db.commit()

    async def load_group_context(self, group_id: str) -> dict | None:
        async with aiosqlite.connect(self._path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM group_contexts WHERE group_id = ?", (group_id,)
            ) as cursor:
                row = await cursor.fetchone()

        if row is None:
            return None

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
        }

    async def load_all_group_contexts(self) -> list[dict]:
        async with aiosqlite.connect(self._path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM group_contexts") as cursor:
                rows = await cursor.fetchall()

        return [
            {
                "group_id": row["group_id"],
                "default_name": row["default_name"],
                "display_name": row["display_name"],
                "nicknames": json.loads(row["nicknames"]),
                "owner_id": row["owner_id"],
                "provider": row["provider"],
                "model": row["model"],
                "is_first_interaction": bool(row["is_first_interaction"]),
                "history": json.loads(row["history"]),
            }
            for row in rows
        ]

    async def save_scheduled_task(self, task: ScheduledTask) -> None:
        async with aiosqlite.connect(self._path) as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO scheduled_tasks
                    (id, group_id, owner_id, prompt, schedule_type, schedule_value,
                     model, provider, status, next_run, retry_count, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task.id,
                    task.group_id,
                    task.owner_id,
                    task.prompt,
                    task.schedule_type,
                    task.schedule_value,
                    task.model,
                    task.provider,
                    task.status,
                    task.next_run,
                    task.retry_count,
                    task.created_at,
                ),
            )
            await db.commit()

    async def list_scheduled_tasks(self, group_id: str) -> list[ScheduledTask]:
        async with aiosqlite.connect(self._path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM scheduled_tasks WHERE group_id = ?", (group_id,)
            ) as cursor:
                rows = await cursor.fetchall()

        return [_row_to_task(row) for row in rows]

    async def list_due_tasks(self, now: str) -> list[ScheduledTask]:
        async with aiosqlite.connect(self._path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT * FROM scheduled_tasks
                WHERE status = 'active' AND next_run <= ?
                """,
                (now,),
            ) as cursor:
                rows = await cursor.fetchall()

        return [_row_to_task(row) for row in rows]

    async def update_task_status(
        self,
        task_id: str,
        status: str,
        next_run: str | None = None,
        retry_count: int | None = None,
    ) -> None:
        async with aiosqlite.connect(self._path) as db:
            await db.execute(
                """
                UPDATE scheduled_tasks
                SET status      = ?,
                    next_run    = COALESCE(?, next_run),
                    retry_count = COALESCE(?, retry_count)
                WHERE id = ?
                """,
                (status, next_run, retry_count, task_id),
            )
            await db.commit()

    async def log_task_run(
        self,
        id: str,
        task_id: str,
        ran_at: str,
        status: str,
        result_snippet: str | None,
        duration_ms: int,
    ) -> None:
        async with aiosqlite.connect(self._path) as db:
            await db.execute(
                """
                INSERT INTO task_run_log
                    (id, task_id, ran_at, status, result_snippet, duration_ms)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (id, task_id, ran_at, status, result_snippet, duration_ms),
            )
            await db.commit()

    async def log_shield_detection(
        self,
        id: str,
        group_id: str,
        sender_id: str,
        detection_types: list[str],
        timestamp: str,
    ) -> None:
        async with aiosqlite.connect(self._path) as db:
            await db.execute(
                """
                INSERT INTO shield_log
                    (id, group_id, sender_id, detection_types, timestamp)
                VALUES (?, ?, ?, ?, ?)
                """,
                (id, group_id, sender_id, json.dumps(detection_types), timestamp),
            )
            await db.commit()


def _row_to_task(row: aiosqlite.Row) -> ScheduledTask:
    return ScheduledTask(
        id=row["id"],
        group_id=row["group_id"],
        owner_id=row["owner_id"],
        prompt=row["prompt"],
        schedule_type=row["schedule_type"],
        schedule_value=row["schedule_value"],
        model=row["model"],
        provider=row["provider"],
        status=row["status"],
        next_run=row["next_run"],
        retry_count=row["retry_count"],
        created_at=row["created_at"],
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_db.py -v
```

Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add napyclaw/db.py tests/test_db.py
git commit -m "feat: Database class with full schema and CRUD"
```

---

### Task 5: Entry Point Stub + pgvector Migration

**Files:**
- Create: `napyclaw/__main__.py`
- Create: `napyclaw/migrations/001_thoughts.sql`

- [ ] **Step 1: Create `napyclaw/__main__.py`**

```python
"""Entry point — builds and starts NapyClaw."""
import asyncio
import sys

from napyclaw.config import Config, ConfigError


async def main() -> None:
    try:
        config = Config.from_infisical()
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    # NapyClaw orchestrator is wired in Plan 4 (app.py)
    print(f"napyclaw starting with model {config.default_model} on {config.default_provider}")
    print("NapyClaw app not yet wired — see Plan 4.")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Create `napyclaw/migrations/001_thoughts.sql`**

```sql
-- PostgreSQL + pgvector schema for VectorMemory backend (Plan 5)
-- Apply with: psql $VECTOR_DB_URL -f napyclaw/migrations/001_thoughts.sql

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS thoughts (
    id         UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    content    TEXT        NOT NULL,
    embedding  vector(768),           -- nomic-embed-text produces 768-dim vectors
    group_id   TEXT,                  -- NULL = global memory; set = group-scoped
    user_id    TEXT        NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS thoughts_embedding_idx
    ON thoughts USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

CREATE INDEX IF NOT EXISTS thoughts_group_idx ON thoughts (group_id);

-- Returns union of group-scoped + global thoughts ordered by cosine similarity
CREATE OR REPLACE FUNCTION match_thoughts(
    query_embedding vector(768),
    p_group_id      TEXT,
    match_count     INT DEFAULT 5
)
RETURNS TABLE (
    id         UUID,
    content    TEXT,
    similarity FLOAT
)
LANGUAGE SQL STABLE AS $$
    SELECT id, content, 1 - (embedding <=> query_embedding) AS similarity
    FROM   thoughts
    WHERE  group_id = p_group_id OR group_id IS NULL
    ORDER  BY embedding <=> query_embedding
    LIMIT  match_count;
$$;
```

- [ ] **Step 3: Run full test suite**

```bash
pytest -v
```

Expected: All tests pass (21 tests total across test_types.py, test_config.py, test_db.py)

- [ ] **Step 4: Commit**

```bash
git add napyclaw/__main__.py napyclaw/migrations/001_thoughts.sql
git commit -m "feat: entry point stub and pgvector migration"
```

---

## Self-Review

**Spec coverage check:**
- Config fields: all 15 fields from spec covered, plus `INFISICAL_PROJECT_ID` bootstrap (required to identify the Infisical project — not in spec but architecturally necessary) ✓
- `Message` dataclass: all 7 fields ✓
- `ChatResponse` / `ToolCall` dataclasses ✓
- `LLMClient` ABC with `chat()` / `stream()` signatures ✓
- `ScheduledTask` dataclass: all 12 fields ✓
- DB tables: messages, group_contexts, scheduled_tasks, task_run_log, shield_log — all 5 from spec ✓
- `Database` methods cover all operations needed by Plans 3–7 ✓
- pgvector migration: thoughts table + match_thoughts fn + indexes ✓
- `Channel` ABC ✓

**What is intentionally deferred:**
- `Agent`, `GroupContext`, `NapyClaw` — Plan 4
- `OpenAIClient`, `OllamaClient` — Plan 2
- All tools — Plan 3
- `MemoryBackend` implementations — Plan 5
- `Scheduler` — Plan 6
- `ContentShield`, `PrivateSession` — Plan 7
- `OAuthCallbackServer`, `RecipeTool` — Plan 8
