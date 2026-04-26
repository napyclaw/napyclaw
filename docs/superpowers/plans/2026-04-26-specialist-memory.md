# Specialist Memory, Onboarding, and Backstage UI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Spec review rule:** Each task lists the spec sections it covers under **Spec Reference**. Before starting a task, read those sections in `docs/superpowers/specs/2026-04-25-specialist-memory-design.md`. If anything in the task conflicts with or is ambiguous against the spec, re-read the relevant spec sections and follow the spec — do not resolve conflicts by guessing or defaulting to what seems reasonable. If the conflict cannot be resolved from the spec alone, surface it before writing any code.

**Goal:** Add structured specialist memory, collaborative onboarding, a layered system prompt builder, background summarization, user identity extraction, and a three-panel Backstage UI to napyclaw.

**Architecture:** A new `specialist_memory` table holds typed per-specialist facts (responsibility/task/tool/resource/preference/fact). A `PromptBuilder` module assembles layered system prompts from identity, responsibilities, semantic working context, and episodic thoughts. A background summarizer fires after each pruning event to capture what's leaving the verbatim window into `thoughts`. The webchat UI gains a third Backstage column showing live context, memory activity, and pending approvals.

**Tech Stack:** Python 3.12, asyncpg, pgvector, aiohttp, FastAPI, vanilla JS (no framework). Tests use pytest-asyncio with in-memory fakes (no Postgres required for unit tests; integration tests require live DB).

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `napyclaw/migrations/004_specialist_memory.sql` | Create | Migration: add job_description/verbatim_turns/summary_turns to group_contexts, create specialist_memory table |
| `napyclaw/db.py` | Modify | CRUD for specialist_memory; update save_group_context/load signatures for new columns; add _row_to_ctx update |
| `napyclaw/prompt_builder.py` | Create | PromptBuilder class: builds layered system prompt from GroupContext + RetrievedMemory, supports markdown/json render |
| `napyclaw/tools/specialist_tools.py` | Create | SetJobDescriptionTool, ManageSpecialistMemoryTool, SaveToMemoryTool (replaces memory_tool.py's version) |
| `napyclaw/summarizer.py` | Create | Background summarizer: detects prune trigger, calls LLM, routes items by trust tier, manages correction window state |
| `napyclaw/app.py` | Modify | Wire PromptBuilder; add owner_name to GroupContext; remove _default_system_prompt; wire summarizer in _run_agent; emit backstage WS events |
| `napyclaw/memory.py` | No change | specialist_memory CRUD lives in db.py, not memory.py |
| `services/comms/main.py` | Modify | Extract Tailscale-User-Name header; new WS event types (context_used, memory_queued, memory_pending_approval, memory_committed, memory_adjusted, memory_excluded, background_task, tool_call); correction window state |
| `services/comms/static/index.html` | Modify | Three-panel layout; Backstage column with sticky area and turn blocks; turn-click linking; Adjust/Exclude UI |
| `tests/test_prompt_builder.py` | Create | Unit tests for PromptBuilder (no DB, no LLM) |
| `tests/test_specialist_tools.py` | Create | Unit tests for SetJobDescriptionTool, ManageSpecialistMemoryTool, SaveToMemoryTool |
| `tests/test_summarizer.py` | Create | Unit tests for summarizer prune detection and item routing |
| `tests/test_db.py` | Modify | Add tests for specialist_memory CRUD and new group_context columns |
| `tests/test_app.py` | Modify | Update _FakeDB, _make_context, existing tests to include new GroupContext fields |

---

## Task 1: Migration 004

**Spec Reference:** Section 1 (Data Model)

**Files:**
- Create: `napyclaw/migrations/004_specialist_memory.sql`

- [ ] **Step 1: Write the migration**

```sql
-- napyclaw/migrations/004_specialist_memory.sql
-- Adds specialist working memory table and per-specialist history window config.
-- Requires: 003_webchat.sql already applied.

ALTER TABLE group_contexts
    ADD COLUMN IF NOT EXISTS job_description  TEXT,
    ADD COLUMN IF NOT EXISTS verbatim_turns   INTEGER NOT NULL DEFAULT 7,
    ADD COLUMN IF NOT EXISTS summary_turns    INTEGER NOT NULL DEFAULT 5;

CREATE TABLE IF NOT EXISTS specialist_memory (
    id          TEXT PRIMARY KEY,
    group_id    TEXT NOT NULL,
    type        TEXT NOT NULL CHECK (type IN (
                    'responsibility','task','tool','resource','preference','fact')),
    content     TEXT NOT NULL,
    embedding   vector(768),
    created_at  TIMESTAMPTZ DEFAULT now(),
    updated_at  TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS specialist_memory_group_idx
    ON specialist_memory (group_id);

CREATE INDEX IF NOT EXISTS specialist_memory_embedding_idx
    ON specialist_memory USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 50);
```

- [ ] **Step 2: Apply migration to running DB**

```bash
docker exec -i napyclaw-db-1 psql -U napyclaw -d napyclaw < napyclaw/migrations/004_specialist_memory.sql
```

Expected output: `ALTER TABLE`, `CREATE TABLE`, `CREATE INDEX`, `CREATE INDEX`

- [ ] **Step 3: Verify columns exist**

```bash
docker exec -i napyclaw-db-1 psql -U napyclaw -d napyclaw -c "\d group_contexts"
```

Confirm `job_description`, `verbatim_turns`, `summary_turns` columns are present.

```bash
docker exec -i napyclaw-db-1 psql -U napyclaw -d napyclaw -c "\d specialist_memory"
```

Confirm `id`, `group_id`, `type`, `content`, `embedding` columns are present.

- [ ] **Step 4: Commit**

```bash
git add napyclaw/migrations/004_specialist_memory.sql
git commit -m "feat: migration 004 — specialist_memory table and history window columns"
```

---

## Task 2: Database Layer

**Spec Reference:** Section 1 (Data Model), Section 6 (History window configuration)

**Files:**
- Modify: `napyclaw/db.py`
- Modify: `tests/test_db.py`

- [ ] **Step 1: Write failing tests for specialist_memory CRUD and new group_context columns**

Add to `tests/test_db.py`:

```python
async def test_save_and_load_job_description(db: Database):
    await db.save_group_context(
        group_id="g-jd",
        default_name="Amy",
        display_name="Amy",
        nicknames=["Amy"],
        owner_id="owner",
        provider="openai",
        model="gpt-4o",
        is_first_interaction=True,
        history=[],
        job_description="I help with financial forecasting.",
        verbatim_turns=10,
        summary_turns=3,
    )
    row = await db.load_group_context("g-jd")
    assert row["job_description"] == "I help with financial forecasting."
    assert row["verbatim_turns"] == 10
    assert row["summary_turns"] == 3


async def test_job_description_defaults_none(db: Database):
    await db.save_group_context(
        group_id="g-nonjd",
        default_name="Sam",
        display_name="Sam",
        nicknames=[],
        owner_id="owner",
        provider="openai",
        model="gpt-4o",
        is_first_interaction=True,
        history=[],
    )
    row = await db.load_group_context("g-nonjd")
    assert row["job_description"] is None
    assert row["verbatim_turns"] == 7
    assert row["summary_turns"] == 5


async def test_save_and_load_specialist_memory(db: Database):
    entry_id = str(uuid.uuid4())
    await db.save_specialist_memory(
        id=entry_id,
        group_id="g-spec",
        type="responsibility",
        content="I own the monthly P&L report.",
        embedding=None,
    )
    entries = await db.load_specialist_memory("g-spec")
    assert len(entries) == 1
    assert entries[0]["content"] == "I own the monthly P&L report."
    assert entries[0]["type"] == "responsibility"


async def test_update_specialist_memory(db: Database):
    entry_id = str(uuid.uuid4())
    await db.save_specialist_memory(
        id=entry_id,
        group_id="g-spec",
        type="task",
        content="Original task content.",
        embedding=None,
    )
    await db.update_specialist_memory(entry_id, content="Updated task content.")
    entries = await db.load_specialist_memory("g-spec")
    assert entries[0]["content"] == "Updated task content."


async def test_delete_specialist_memory(db: Database):
    entry_id = str(uuid.uuid4())
    await db.save_specialist_memory(
        id=entry_id,
        group_id="g-spec",
        type="fact",
        content="Temporary fact.",
        embedding=None,
    )
    await db.delete_specialist_memory(entry_id)
    entries = await db.load_specialist_memory("g-spec")
    assert len(entries) == 0


async def test_load_specialist_memory_by_type(db: Database):
    for t, content in [
        ("responsibility", "I own forecasting."),
        ("task", "Prepare weekly report."),
        ("resource", "https://example.com"),
    ]:
        await db.save_specialist_memory(
            id=str(uuid.uuid4()),
            group_id="g-multi",
            type=t,
            content=content,
            embedding=None,
        )
    responsibilities = await db.load_specialist_memory("g-multi", type_filter="responsibility")
    assert len(responsibilities) == 1
    assert responsibilities[0]["content"] == "I own forecasting."
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /c/Users/NathanKaemingk/source/napyclaw && .venv/Scripts/python -m pytest tests/test_db.py::test_save_and_load_job_description tests/test_db.py::test_save_and_load_specialist_memory -v 2>&1 | tail -20
```

Expected: FAILED — `save_specialist_memory` not defined, `job_description` KeyError

- [ ] **Step 3: Update `save_group_context` signature in db.py**

In `napyclaw/db.py`, update `save_group_context` to accept and persist the new columns:

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
    job_title: str | None = None,
    memory_enabled: bool = True,
    channel_type: str = "slack",
    job_description: str | None = None,
    verbatim_turns: int = 7,
    summary_turns: int = 5,
) -> None:
    await self.pool.execute(
        """
        INSERT INTO group_contexts
            (group_id, default_name, display_name, nicknames, owner_id,
             provider, model, is_first_interaction, history,
             job_title, memory_enabled, channel_type,
             job_description, verbatim_turns, summary_turns)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
        ON CONFLICT (group_id) DO UPDATE SET
            default_name         = EXCLUDED.default_name,
            display_name         = EXCLUDED.display_name,
            nicknames            = EXCLUDED.nicknames,
            owner_id             = EXCLUDED.owner_id,
            provider             = EXCLUDED.provider,
            model                = EXCLUDED.model,
            is_first_interaction = EXCLUDED.is_first_interaction,
            history              = EXCLUDED.history,
            job_title            = EXCLUDED.job_title,
            memory_enabled       = EXCLUDED.memory_enabled,
            channel_type         = EXCLUDED.channel_type,
            job_description      = EXCLUDED.job_description,
            verbatim_turns       = EXCLUDED.verbatim_turns,
            summary_turns        = EXCLUDED.summary_turns
        """,
        group_id, default_name, display_name, json.dumps(nicknames),
        owner_id, provider, model, is_first_interaction, json.dumps(history),
        job_title, memory_enabled, channel_type,
        job_description, verbatim_turns, summary_turns,
    )
```

- [ ] **Step 4: Update `_row_to_ctx` in db.py**

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
        "job_title": row["job_title"],
        "memory_enabled": bool(row["memory_enabled"]) if row["memory_enabled"] is not None else True,
        "channel_type": row["channel_type"] or "slack",
        "job_description": row["job_description"],
        "verbatim_turns": row["verbatim_turns"] if row["verbatim_turns"] is not None else 7,
        "summary_turns": row["summary_turns"] if row["summary_turns"] is not None else 5,
    }
```

- [ ] **Step 5: Add specialist_memory CRUD methods to Database class**

Add after `load_webchat_specialists` in `napyclaw/db.py`:

```python
async def save_specialist_memory(
    self,
    id: str,
    group_id: str,
    type: str,
    content: str,
    embedding: list[float] | None,
) -> None:
    embedding_str = (
        "[" + ",".join(str(x) for x in embedding) + "]"
        if embedding else None
    )
    await self.pool.execute(
        """
        INSERT INTO specialist_memory (id, group_id, type, content, embedding)
        VALUES ($1, $2, $3, $4, $5::vector)
        ON CONFLICT (id) DO UPDATE SET
            content    = EXCLUDED.content,
            embedding  = EXCLUDED.embedding,
            updated_at = now()
        """,
        id, group_id, type, content, embedding_str,
    )

async def update_specialist_memory(self, id: str, content: str) -> None:
    await self.pool.execute(
        "UPDATE specialist_memory SET content = $1, updated_at = now() WHERE id = $2",
        content, id,
    )

async def delete_specialist_memory(self, id: str) -> None:
    await self.pool.execute("DELETE FROM specialist_memory WHERE id = $1", id)

async def load_specialist_memory(
    self,
    group_id: str,
    type_filter: str | None = None,
) -> list[dict]:
    if type_filter:
        rows = await self.pool.fetch(
            "SELECT id, group_id, type, content, created_at, updated_at "
            "FROM specialist_memory WHERE group_id = $1 AND type = $2 "
            "ORDER BY created_at",
            group_id, type_filter,
        )
    else:
        rows = await self.pool.fetch(
            "SELECT id, group_id, type, content, created_at, updated_at "
            "FROM specialist_memory WHERE group_id = $1 "
            "ORDER BY created_at",
            group_id,
        )
    return [dict(row) for row in rows]

async def search_specialist_memory(
    self,
    group_id: str,
    embedding: list[float],
    type_filter: str | None = None,
    top_k: int = 5,
) -> list[dict]:
    """Semantic search over specialist_memory using cosine similarity."""
    embedding_str = "[" + ",".join(str(x) for x in embedding) + "]"
    if type_filter:
        rows = await self.pool.fetch(
            """
            SELECT id, group_id, type, content,
                   1 - (embedding <=> $1::vector) AS similarity
            FROM specialist_memory
            WHERE group_id = $2 AND type = $3 AND embedding IS NOT NULL
            ORDER BY embedding <=> $1::vector
            LIMIT $4
            """,
            embedding_str, group_id, type_filter, top_k,
        )
    else:
        rows = await self.pool.fetch(
            """
            SELECT id, group_id, type, content,
                   1 - (embedding <=> $1::vector) AS similarity
            FROM specialist_memory
            WHERE group_id = $2 AND embedding IS NOT NULL
            ORDER BY embedding <=> $1::vector
            LIMIT $3
            """,
            embedding_str, group_id, top_k,
        )
    return [dict(row) for row in rows]
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
cd /c/Users/NathanKaemingk/source/napyclaw && .venv/Scripts/python -m pytest tests/test_db.py -v 2>&1 | tail -30
```

Expected: All DB tests pass (Postgres integration tests skip if DB not available)

- [ ] **Step 7: Commit**

```bash
git add napyclaw/db.py tests/test_db.py
git commit -m "feat: db — specialist_memory CRUD, job_description/verbatim_turns/summary_turns columns"
```

---

## Task 3: PromptBuilder Module

**Spec Reference:** Section 2 (System Prompt Layering), Section 5 (Onboarding Flow), Section 9 (New Module: prompt_builder.py)

**Files:**
- Create: `napyclaw/prompt_builder.py`
- Create: `tests/test_prompt_builder.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_prompt_builder.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest

from napyclaw.prompt_builder import PromptBuilder, RetrievedMemory, SpecialistMemoryRow


def _make_ctx(
    display_name: str = "Amy",
    job_title: str | None = "Financial Analyst",
    job_description: str | None = "I help with forecasting and P&L analysis.",
    is_first_interaction: bool = False,
) -> object:
    ctx = MagicMock()
    ctx.display_name = display_name
    ctx.job_title = job_title
    ctx.job_description = job_description
    ctx.is_first_interaction = is_first_interaction
    ctx.active_client.provider = "openai"
    ctx.active_client.model = "gpt-4o"
    return ctx


def _make_memory(
    responsibilities: list[str] | None = None,
    working_context: list[str] | None = None,
    episodic: list[str] | None = None,
) -> RetrievedMemory:
    return RetrievedMemory(
        responsibilities=[
            SpecialistMemoryRow(id="r1", type="responsibility", content=c)
            for c in (responsibilities or [])
        ],
        working_context=[
            SpecialistMemoryRow(id="w1", type="task", content=c)
            for c in (working_context or [])
        ],
        episodic=episodic or [],
    )


class TestPromptBuilderMarkdown:
    def test_identity_block_contains_name(self):
        builder = PromptBuilder()
        memory = _make_memory()
        result = builder.build(_make_ctx(), memory, owner_name="Nate")
        assert "Amy" in result

    def test_identity_block_contains_owner(self):
        builder = PromptBuilder()
        memory = _make_memory()
        result = builder.build(_make_ctx(), memory, owner_name="Nate")
        assert "Nate" in result

    def test_identity_block_contains_job_description(self):
        builder = PromptBuilder()
        memory = _make_memory()
        result = builder.build(_make_ctx(), memory, owner_name="Nate")
        assert "forecasting and P&L" in result

    def test_onboarding_prompt_when_no_job_description(self):
        builder = PromptBuilder()
        ctx = _make_ctx(job_description=None, is_first_interaction=True)
        memory = _make_memory()
        result = builder.build(ctx, memory, owner_name="Nate")
        assert "collaboratively" in result.lower() or "define" in result.lower()

    def test_responsibilities_block_present(self):
        builder = PromptBuilder()
        memory = _make_memory(responsibilities=["I own the monthly P&L report."])
        result = builder.build(_make_ctx(), memory, owner_name="Nate")
        assert "I own the monthly P&L report." in result

    def test_working_context_block_present(self):
        builder = PromptBuilder()
        memory = _make_memory(working_context=["Prepare the Q2 forecast by Friday."])
        result = builder.build(_make_ctx(), memory, owner_name="Nate")
        assert "Q2 forecast" in result

    def test_episodic_block_present(self):
        builder = PromptBuilder()
        memory = _make_memory(episodic=["User prefers bullet-point summaries."])
        result = builder.build(_make_ctx(), memory, owner_name="Nate")
        assert "bullet-point" in result

    def test_first_person_instruction_in_identity(self):
        builder = PromptBuilder()
        memory = _make_memory()
        result = builder.build(_make_ctx(), memory, owner_name="Nate")
        assert "first person" in result.lower() or 'speak as "I"' in result.lower() or "I," in result

    def test_identity_before_responsibilities(self):
        builder = PromptBuilder()
        memory = _make_memory(responsibilities=["Own P&L."])
        result = builder.build(_make_ctx(), memory, owner_name="Nate")
        assert result.index("Amy") < result.index("Own P&L.")

    def test_responsibilities_before_working_context(self):
        builder = PromptBuilder()
        memory = _make_memory(
            responsibilities=["RESP_MARKER"],
            working_context=["CTX_MARKER"],
        )
        result = builder.build(_make_ctx(), memory, owner_name="Nate")
        assert result.index("RESP_MARKER") < result.index("CTX_MARKER")


class TestPromptBuilderJson:
    def test_json_format_contains_identity_key(self):
        builder = PromptBuilder()
        memory = _make_memory()
        result = builder.build(_make_ctx(), memory, owner_name="Nate", fmt="json")
        import json
        parsed = json.loads(result)
        assert "identity" in parsed

    def test_json_format_contains_responsibilities_key(self):
        builder = PromptBuilder()
        memory = _make_memory(responsibilities=["Own P&L."])
        result = builder.build(_make_ctx(), memory, owner_name="Nate", fmt="json")
        import json
        parsed = json.loads(result)
        assert "responsibilities" in parsed
        assert "Own P&L." in parsed["responsibilities"]
```

- [ ] **Step 2: Run to confirm tests fail**

```bash
cd /c/Users/NathanKaemingk/source/napyclaw && .venv/Scripts/python -m pytest tests/test_prompt_builder.py -v 2>&1 | tail -20
```

Expected: ModuleNotFoundError for `napyclaw.prompt_builder`

- [ ] **Step 3: Implement `napyclaw/prompt_builder.py`**

```python
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    pass


@dataclass
class SpecialistMemoryRow:
    id: str
    type: str
    content: str


@dataclass
class RetrievedMemory:
    responsibilities: list[SpecialistMemoryRow] = field(default_factory=list)
    working_context: list[SpecialistMemoryRow] = field(default_factory=list)
    episodic: list[str] = field(default_factory=list)


_TRUST_TIER_RULES = (
    "Memory update rules:\n"
    "- For responsibility or job_description changes: propose first, wait for user confirmation before saving.\n"
    "- For task, tool, resource, preference, fact: save immediately and notify the user in the Backstage panel.\n"
    "- If a request is outside your current responsibilities, ask: "
    "'This seems outside my current scope — should I add this as a new responsibility?'"
)

_FIRST_PERSON_RULE = (
    "Always speak in first person. You are a named specialist talking directly to the user. "
    'Use "I", "my role", "I can help with..." — never refer to yourself in the third person.'
)

_ONBOARDING_INSTRUCTION = (
    "Your job description has not been defined yet. "
    "Before doing other work, collaboratively define your role with the user. "
    "Ask open questions about what they need from you. "
    "After a few turns, propose a summary: 'Here is what I understand my role to be — does this look right?' "
    "Once confirmed, use set_job_description to save it, then seed initial specialist_memory entries. "
    "Announce when you are ready to work. "
    "Then ask: 'Are there any specific resources or knowledge I will need to do this role at the highest level?'"
)


class PromptBuilder:
    def build(
        self,
        ctx: object,
        memory: RetrievedMemory,
        owner_name: str,
        fmt: Literal["markdown", "json"] = "markdown",
    ) -> str:
        blocks = self._build_blocks(ctx, memory, owner_name)
        if fmt == "json":
            return self._render_json(blocks)
        return self._render_markdown(blocks)

    def _build_blocks(self, ctx: object, memory: RetrievedMemory, owner_name: str) -> dict[str, str]:
        job_description = getattr(ctx, "job_description", None)
        display_name = getattr(ctx, "display_name", "Specialist")
        job_title = getattr(ctx, "job_title", None)

        identity_parts = [
            f"Your name is {display_name}.",
        ]
        if job_title:
            identity_parts.append(f"Your role is: {job_title}.")
        if job_description:
            identity_parts.append(f"Your job description: {job_description}")
        else:
            identity_parts.append(_ONBOARDING_INSTRUCTION)
        identity_parts.append(f"You are working for {owner_name}.")
        identity_parts.append(_FIRST_PERSON_RULE)
        identity_parts.append(_TRUST_TIER_RULES)

        blocks: dict[str, str] = {
            "identity": "\n".join(identity_parts),
        }

        if memory.responsibilities:
            blocks["responsibilities"] = "\n".join(
                f"- {r.content}" for r in memory.responsibilities
            )

        if memory.working_context:
            blocks["working_context"] = "\n".join(
                f"- [{r.type}] {r.content}" for r in memory.working_context
            )

        if memory.episodic:
            blocks["episodic_memory"] = "\n".join(
                f"- {e}" for e in memory.episodic
            )

        return blocks

    def _render_markdown(self, blocks: dict[str, str]) -> str:
        section_titles = {
            "identity": "## Identity",
            "responsibilities": "## My Responsibilities",
            "working_context": "## Working Context",
            "episodic_memory": "## Relevant Memory",
        }
        parts = []
        for key, content in blocks.items():
            title = section_titles.get(key, f"## {key.replace('_', ' ').title()}")
            parts.append(f"{title}\n{content}")
        return "\n\n".join(parts)

    def _render_json(self, blocks: dict[str, str]) -> str:
        return json.dumps(blocks, ensure_ascii=False, indent=2)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /c/Users/NathanKaemingk/source/napyclaw && .venv/Scripts/python -m pytest tests/test_prompt_builder.py -v 2>&1 | tail -30
```

Expected: All tests pass

- [ ] **Step 5: Commit**

```bash
git add napyclaw/prompt_builder.py tests/test_prompt_builder.py
git commit -m "feat: PromptBuilder module — layered system prompt with markdown/json render"
```

---

## Task 4: Specialist Tools

**Spec Reference:** Section 3 (Trust Tiers), Section 4 (Agent Tools)

**Files:**
- Create: `napyclaw/tools/specialist_tools.py`
- Create: `tests/test_specialist_tools.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_specialist_tools.py`:

```python
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from napyclaw.tools.specialist_tools import (
    ManageSpecialistMemoryTool,
    SaveToMemoryTool,
    SetJobDescriptionTool,
)


class TestSetJobDescriptionTool:
    def _make_tool(self):
        db = MagicMock()
        db.save_group_context = AsyncMock()
        ctx = MagicMock()
        ctx.group_id = "g-spec"
        ctx.default_name = "Amy"
        ctx.display_name = "Amy"
        ctx.nicknames = ["Amy"]
        ctx.owner_id = "owner"
        ctx.active_client.provider = "openai"
        ctx.active_client.model = "gpt-4o"
        ctx.is_first_interaction = False
        ctx.history = []
        ctx.job_title = "Analyst"
        ctx.memory_enabled = True
        ctx.channel_type = "webchat"
        ctx.job_description = None
        ctx.verbatim_turns = 7
        ctx.summary_turns = 5
        return SetJobDescriptionTool(db=db, ctx=ctx), db, ctx

    async def test_saves_job_description(self):
        tool, db, ctx = self._make_tool()
        result = await tool.execute(description="I own the monthly P&L report.")
        db.save_group_context.assert_called_once()
        call_kwargs = db.save_group_context.call_args[1]
        assert call_kwargs["job_description"] == "I own the monthly P&L report."
        assert "saved" in result.lower() or "updated" in result.lower()

    async def test_empty_description_returns_error(self):
        tool, db, ctx = self._make_tool()
        result = await tool.execute(description="   ")
        assert "error" in result.lower()
        db.save_group_context.assert_not_called()


class TestManageSpecialistMemoryTool:
    def _make_tool(self):
        db = MagicMock()
        db.save_specialist_memory = AsyncMock()
        db.update_specialist_memory = AsyncMock()
        db.delete_specialist_memory = AsyncMock()
        notify = AsyncMock()
        return ManageSpecialistMemoryTool(db=db, group_id="g-spec", notify=notify), db, notify

    async def test_add_task_saves_to_db(self):
        tool, db, notify = self._make_tool()
        result = await tool.execute(action="add", type="task", content="Prepare Q2 forecast.")
        db.save_specialist_memory.assert_called_once()
        args = db.save_specialist_memory.call_args[1]
        assert args["type"] == "task"
        assert args["content"] == "Prepare Q2 forecast."
        assert "saved" in result.lower() or "added" in result.lower()

    async def test_add_task_notifies(self):
        tool, db, notify = self._make_tool()
        await tool.execute(action="add", type="task", content="Prepare Q2 forecast.")
        notify.assert_called_once()

    async def test_add_responsibility_does_not_save_directly(self):
        tool, db, notify = self._make_tool()
        result = await tool.execute(action="add", type="responsibility", content="Own P&L.")
        db.save_specialist_memory.assert_not_called()
        assert "confirm" in result.lower() or "approval" in result.lower() or "pending" in result.lower()

    async def test_delete_calls_db(self):
        tool, db, notify = self._make_tool()
        result = await tool.execute(action="delete", type="task", entry_id="entry-123")
        db.delete_specialist_memory.assert_called_once_with("entry-123")

    async def test_unknown_action_returns_error(self):
        tool, db, notify = self._make_tool()
        result = await tool.execute(action="invalid", type="task", content="x")
        assert "error" in result.lower()


class TestSaveToMemoryTool:
    def _make_tool(self):
        memory = MagicMock()
        memory.capture = AsyncMock()
        notify = AsyncMock()
        return SaveToMemoryTool(memory=memory, group_id="g-spec", notify=notify), memory, notify

    async def test_saves_content(self):
        tool, memory, notify = self._make_tool()
        result = await tool.execute(content="User prefers bullet summaries.")
        memory.capture.assert_called_once_with(
            "User prefers bullet summaries.", group_id="g-spec"
        )
        assert "saved" in result.lower()

    async def test_notifies_after_save(self):
        tool, memory, notify = self._make_tool()
        await tool.execute(content="User prefers bullet summaries.")
        notify.assert_called_once()

    async def test_empty_content_returns_error(self):
        tool, memory, notify = self._make_tool()
        result = await tool.execute(content="  ")
        assert "error" in result.lower()
        memory.capture.assert_not_called()
```

- [ ] **Step 2: Run to confirm tests fail**

```bash
cd /c/Users/NathanKaemingk/source/napyclaw && .venv/Scripts/python -m pytest tests/test_specialist_tools.py -v 2>&1 | tail -20
```

Expected: ModuleNotFoundError for `napyclaw.tools.specialist_tools`

- [ ] **Step 3: Implement `napyclaw/tools/specialist_tools.py`**

```python
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Callable, Awaitable

from napyclaw.tools.base import Tool

if TYPE_CHECKING:
    from napyclaw.db import Database
    from napyclaw.memory import MemoryBackend


class SetJobDescriptionTool(Tool):
    name = "set_job_description"
    description = (
        "Save or update your job description. Call this during onboarding after the user "
        "has confirmed the role summary. Ask first before calling — do not save without confirmation."
    )
    parameters = {
        "type": "object",
        "properties": {
            "description": {
                "type": "string",
                "description": "The full job description to save.",
            }
        },
        "required": ["description"],
    }

    def __init__(self, db: Database, ctx: object) -> None:
        self._db = db
        self._ctx = ctx

    async def execute(self, **kwargs) -> str:
        description = kwargs.get("description", "").strip()
        if not description:
            return "Error: description is required."
        ctx = self._ctx
        await self._db.save_group_context(
            group_id=ctx.group_id,
            default_name=ctx.default_name,
            display_name=ctx.display_name,
            nicknames=ctx.nicknames,
            owner_id=ctx.owner_id,
            provider=ctx.active_client.provider,
            model=ctx.active_client.model,
            is_first_interaction=ctx.is_first_interaction,
            history=ctx.history,
            job_title=ctx.job_title,
            memory_enabled=ctx.memory_enabled,
            channel_type=ctx.channel_type,
            job_description=description,
            verbatim_turns=ctx.verbatim_turns,
            summary_turns=ctx.summary_turns,
        )
        ctx.job_description = description
        return f"Job description saved."


class ManageSpecialistMemoryTool(Tool):
    name = "manage_specialist_memory"
    description = (
        "Add, update, or delete an entry in your specialist working memory. "
        "Types: responsibility, task, tool, resource, preference, fact. "
        "For responsibility type: propose to the user and wait for confirmation before calling. "
        "For all other types: call directly and the user will be notified."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add", "update", "delete"],
                "description": "The operation to perform.",
            },
            "type": {
                "type": "string",
                "enum": ["responsibility", "task", "tool", "resource", "preference", "fact"],
                "description": "The memory entry type.",
            },
            "content": {
                "type": "string",
                "description": "The content to save. Required for add and update.",
            },
            "entry_id": {
                "type": "string",
                "description": "The entry ID to update or delete. Required for update and delete.",
            },
        },
        "required": ["action", "type"],
    }

    _ASK_FIRST_TYPES = {"responsibility"}

    def __init__(
        self,
        db: Database,
        group_id: str,
        notify: Callable[[dict], Awaitable[None]],
    ) -> None:
        self._db = db
        self._group_id = group_id
        self._notify = notify

    async def execute(self, **kwargs) -> str:
        action = kwargs.get("action", "")
        entry_type = kwargs.get("type", "")
        content = kwargs.get("content", "").strip()
        entry_id = kwargs.get("entry_id", "")

        if action not in ("add", "update", "delete"):
            return f"Error: unknown action '{action}'. Use add, update, or delete."

        if action == "delete":
            if not entry_id:
                return "Error: entry_id is required for delete."
            await self._db.delete_specialist_memory(entry_id)
            return f"Memory entry {entry_id} deleted."

        if not content:
            return "Error: content is required for add and update."

        if entry_type in self._ASK_FIRST_TYPES:
            await self._notify({
                "type": "memory_pending_approval",
                "entry_type": entry_type,
                "content": content,
                "token": str(uuid.uuid4()),
            })
            return (
                f"I've proposed adding this as a responsibility. "
                f"You'll see it in the Backstage panel — please approve or reject it there."
            )

        new_id = entry_id or str(uuid.uuid4())
        await self._db.save_specialist_memory(
            id=new_id,
            group_id=self._group_id,
            type=entry_type,
            content=content,
            embedding=None,
        )
        await self._notify({
            "type": "memory_queued",
            "token": new_id,
            "entry_type": entry_type,
            "content": content,
            "window_turns_remaining": 3,
        })
        verb = "updated" if entry_id else "added"
        return f"Memory entry {verb}: [{entry_type}] {content}"


class SaveToMemoryTool(Tool):
    name = "save_to_memory"
    description = (
        "Save a synthesized insight to episodic memory. Use this when something important "
        "was learned, decided, or established in the conversation. Do not use for raw user "
        "messages — only for synthesized, corrected summaries."
    )
    parameters = {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "The synthesized insight to save.",
            }
        },
        "required": ["content"],
    }

    def __init__(
        self,
        memory: MemoryBackend,
        group_id: str,
        notify: Callable[[dict], Awaitable[None]],
    ) -> None:
        self._memory = memory
        self._group_id = group_id
        self._notify = notify

    async def execute(self, **kwargs) -> str:
        content = kwargs.get("content", "").strip()
        if not content:
            return "Error: content is required."
        await self._memory.capture(content, group_id=self._group_id)
        await self._notify({
            "type": "memory_committed",
            "entry_type": "thought",
            "content": content,
        })
        return "Saved to memory."
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /c/Users/NathanKaemingk/source/napyclaw && .venv/Scripts/python -m pytest tests/test_specialist_tools.py -v 2>&1 | tail -30
```

Expected: All tests pass

- [ ] **Step 5: Commit**

```bash
git add napyclaw/tools/specialist_tools.py tests/test_specialist_tools.py
git commit -m "feat: specialist tools — SetJobDescription, ManageSpecialistMemory, SaveToMemory"
```

---

## Task 5: Background Summarizer

**Spec Reference:** Section 6 (Background Summarizer), Section 3 (Trust Tiers — routing logic)

**Files:**
- Create: `napyclaw/summarizer.py`
- Create: `tests/test_summarizer.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_summarizer.py`:

```python
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from napyclaw.summarizer import Summarizer, SummaryItem, should_summarize


def _make_history(n: int) -> list[dict]:
    history = []
    for i in range(n):
        history.append({"role": "user", "content": f"Message {i}"})
        history.append({"role": "assistant", "content": f"Response {i}"})
    return history


class TestShouldSummarize:
    def test_triggers_when_over_limit(self):
        history = _make_history(13)  # 13 exchanges = 26 messages > default 12
        assert should_summarize(history, verbatim_turns=7, summary_turns=5) is True

    def test_no_trigger_when_under_limit(self):
        history = _make_history(6)
        assert should_summarize(history, verbatim_turns=7, summary_turns=5) is False

    def test_exactly_at_limit_no_trigger(self):
        history = _make_history(6)  # 12 messages = 6 exchanges, exactly at 12
        assert should_summarize(history, verbatim_turns=7, summary_turns=5) is False

    def test_custom_window(self):
        history = _make_history(4)  # 8 messages
        assert should_summarize(history, verbatim_turns=3, summary_turns=2) is True


class TestSummaryItemRouting:
    async def test_responsibility_routes_to_pending_approval(self):
        notify = AsyncMock()
        summarizer = Summarizer(client=MagicMock(), notify=notify)
        item = SummaryItem(type="responsibility", content="I own P&L.", scope="specialist")
        await summarizer._route_item(item, group_id="g-spec", db=MagicMock())
        call_args = notify.call_args[0][0]
        assert call_args["type"] == "memory_pending_approval"

    async def test_task_routes_to_correction_window(self):
        notify = AsyncMock()
        db = MagicMock()
        db.save_specialist_memory = AsyncMock()
        summarizer = Summarizer(client=MagicMock(), notify=notify)
        item = SummaryItem(type="task", content="Prepare Q2 forecast.", scope="specialist")
        await summarizer._route_item(item, group_id="g-spec", db=db)
        call_args = notify.call_args[0][0]
        assert call_args["type"] == "memory_queued"
        assert call_args["window_turns_remaining"] == 3

    async def test_fact_routes_to_correction_window(self):
        notify = AsyncMock()
        db = MagicMock()
        db.save_specialist_memory = AsyncMock()
        summarizer = Summarizer(client=MagicMock(), notify=notify)
        item = SummaryItem(type="fact", content="ETL runs at 3am.", scope="specialist")
        await summarizer._route_item(item, group_id="g-spec", db=db)
        call_args = notify.call_args[0][0]
        assert call_args["type"] == "memory_queued"
```

- [ ] **Step 2: Run to confirm tests fail**

```bash
cd /c/Users/NathanKaemingk/source/napyclaw && .venv/Scripts/python -m pytest tests/test_summarizer.py -v 2>&1 | tail -20
```

Expected: ModuleNotFoundError for `napyclaw.summarizer`

- [ ] **Step 3: Implement `napyclaw/summarizer.py`**

```python
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Awaitable

if TYPE_CHECKING:
    from napyclaw.db import Database
    from napyclaw.models.base import LLMClient


_ASK_FIRST_TYPES = {"responsibility", "job_description"}

_SUMMARIZE_PROMPT = """You are summarizing a conversation batch that is about to be removed from active memory.

Context:
{identity_block}

Exchanges to summarize:
{exchanges}

Instructions:
- Identify what was learned, decided, established, or agreed upon.
- Correct any typos or abandoned trains of thought — capture intent, not exact words.
- Ignore small talk, greetings, and error corrections.
- Return ONLY a JSON array of items. Each item has: type, content, scope.
- type must be one of: responsibility, task, tool, resource, preference, fact
- scope must be: specialist
- Return [] if nothing meaningful happened.

Example output:
[
  {{"type": "task", "content": "Prepare Q2 forecast by end of April.", "scope": "specialist"}},
  {{"type": "resource", "content": "https://example.com/forecast-template", "scope": "specialist"}}
]"""


@dataclass
class SummaryItem:
    type: str
    content: str
    scope: str


def should_summarize(
    history: list[dict],
    verbatim_turns: int = 7,
    summary_turns: int = 5,
) -> bool:
    """Return True when history has more exchanges than verbatim_turns + summary_turns."""
    total_turns = len(history) // 2
    return total_turns > verbatim_turns + summary_turns


def _exchanges_to_summarize(
    history: list[dict],
    verbatim_turns: int,
    summary_turns: int,
) -> list[dict]:
    """Return the oldest summary_turns exchanges (as flat message list)."""
    keep_messages = verbatim_turns * 2
    summary_messages = summary_turns * 2
    start = max(0, len(history) - keep_messages - summary_messages)
    end = max(0, len(history) - keep_messages)
    return history[start:end]


def _format_exchanges(exchanges: list[dict]) -> str:
    lines = []
    for msg in exchanges:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        lines.append(f"{role.capitalize()}: {content}")
    return "\n".join(lines)


class Summarizer:
    def __init__(
        self,
        client: LLMClient,
        notify: Callable[[dict], Awaitable[None]],
    ) -> None:
        self._client = client
        self._notify = notify

    async def run(
        self,
        history: list[dict],
        identity_block: str,
        group_id: str,
        db: Database,
        verbatim_turns: int = 7,
        summary_turns: int = 5,
    ) -> None:
        """Fire-and-forget: summarize oldest batch, route items by trust tier."""
        exchanges = _exchanges_to_summarize(history, verbatim_turns, summary_turns)
        if not exchanges:
            return

        prompt = _SUMMARIZE_PROMPT.format(
            identity_block=identity_block,
            exchanges=_format_exchanges(exchanges),
        )

        try:
            response = await self._client.chat(
                messages=[{"role": "user", "content": prompt}],
                system="You are a memory summarizer. Return only valid JSON.",
                tools=[],
            )
            raw = response.text.strip()
            # Strip markdown code fences if present
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()
            items_data = json.loads(raw)
        except Exception:
            return

        for item_data in items_data:
            try:
                item = SummaryItem(
                    type=item_data.get("type", "fact"),
                    content=item_data.get("content", "").strip(),
                    scope=item_data.get("scope", "specialist"),
                )
                if not item.content:
                    continue
                await self._route_item(item, group_id=group_id, db=db)
            except Exception:
                continue

        await self._notify({
            "type": "background_task",
            "group_id": group_id,
            "event": "summarizer_ran",
        })

    async def _route_item(
        self,
        item: SummaryItem,
        group_id: str,
        db: Database,
    ) -> None:
        token = str(uuid.uuid4())
        if item.type in _ASK_FIRST_TYPES:
            await self._notify({
                "type": "memory_pending_approval",
                "group_id": group_id,
                "token": token,
                "entry_type": item.type,
                "content": item.content,
            })
        else:
            await db.save_specialist_memory(
                id=token,
                group_id=group_id,
                type=item.type,
                content=item.content,
                embedding=None,
            )
            await self._notify({
                "type": "memory_queued",
                "group_id": group_id,
                "token": token,
                "entry_type": item.type,
                "content": item.content,
                "window_turns_remaining": 3,
            })
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /c/Users/NathanKaemingk/source/napyclaw && .venv/Scripts/python -m pytest tests/test_summarizer.py -v 2>&1 | tail -30
```

Expected: All tests pass

- [ ] **Step 5: Commit**

```bash
git add napyclaw/summarizer.py tests/test_summarizer.py
git commit -m "feat: background summarizer — prune detection, LLM summarize, trust-tier routing"
```

---

## Task 6: Wire app.py

**Spec Reference:** Section 2 (System Prompt Layering), Section 5 (Onboarding Flow), Section 6 (Background Summarizer), Section 10 (Implementation Order)

**Files:**
- Modify: `napyclaw/app.py`
- Modify: `tests/test_app.py`

- [ ] **Step 1: Update GroupContext dataclass**

In `napyclaw/app.py`, add new fields to `GroupContext`:

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
    job_title: str | None = None
    job_description: str | None = None
    memory_enabled: bool = True
    channel_type: str = "slack"
    verbatim_turns: int = 7
    summary_turns: int = 5
    owner_name: str = "the user"
```

- [ ] **Step 2: Update all `GroupContext(...)` instantiation sites in app.py**

There are two sites: `start()` (restoring from DB) and `handle_message()` (creating new context). Both need `job_description`, `verbatim_turns`, `summary_turns`, `owner_name` added.

In `start()`, the restored context block:

```python
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
    job_title=row.get("job_title"),
    job_description=row.get("job_description"),
    memory_enabled=row.get("memory_enabled", True),
    channel_type=row.get("channel_type", "slack"),
    verbatim_turns=row.get("verbatim_turns", 7),
    summary_turns=row.get("summary_turns", 5),
    owner_name=row.get("owner_id", "the user"),
)
```

In `handle_message()`, the new-context block:

```python
ctx = GroupContext(
    group_id=msg.group_id,
    default_name=default_name,
    display_name=display_name,
    nicknames=[display_name] if display_name != "Specialist" else [],
    owner_id=msg.sender_id,
    active_client=client,
    is_first_interaction=True,
    agent=Agent(
        client=client,
        tools=[],
        system_prompt="",
        config=self.config,
        injection_guard=self._injection_guard,
    ),
    channel_type=msg.channel_type,
    owner_name=msg.sender_name or msg.sender_id,
)
```

- [ ] **Step 3: Replace _default_system_prompt with PromptBuilder**

Remove the `_default_system_prompt` method entirely. Add import and wiring:

At the top of `app.py` add:
```python
from napyclaw.prompt_builder import PromptBuilder, RetrievedMemory, SpecialistMemoryRow
```

Add `_prompt_builder` attribute in `__init__`:
```python
self._prompt_builder = PromptBuilder()
```

Replace all calls to `self._build_system_prompt(ctx)` in `start()`, `handle_message()` with:
```python
ctx.agent.system_prompt = self._prompt_builder.build(
    ctx,
    RetrievedMemory(),
    owner_name=ctx.owner_name,
)
```

- [ ] **Step 4: Update _run_agent to use PromptBuilder with retrieved memory**

Replace the memory injection block in `_run_agent` (lines 281-288):

```python
async def _run_agent(self, context: GroupContext, msg: Message, text: str) -> None:
    """Execute agent and send response. Runs inside GroupQueue lock."""
    # Build layered system prompt with retrieved memory
    responsibilities: list[SpecialistMemoryRow] = []
    working_context: list[SpecialistMemoryRow] = []
    episodic: list[str] = []

    if context.memory_enabled and self._memory:
        # Responsibilities always injected — no semantic filter
        resp_rows = await self.db.load_specialist_memory(
            context.group_id, type_filter="responsibility"
        )
        responsibilities = [
            SpecialistMemoryRow(id=r["id"], type=r["type"], content=r["content"])
            for r in resp_rows
        ]
        # Working context — semantic search for non-responsibility types
        try:
            wc_rows = await self.db.search_specialist_memory(
                group_id=context.group_id,
                embedding=await self._memory._embed(text),
                top_k=5,
            )
            working_context = [
                SpecialistMemoryRow(id=r["id"], type=r["type"], content=r["content"])
                for r in wc_rows
                if r["type"] != "responsibility"
            ]
        except Exception:
            pass
        # Episodic thoughts
        episodic = await self._memory.search(text, context.group_id, top_k=5)

    retrieved = RetrievedMemory(
        responsibilities=responsibilities,
        working_context=working_context,
        episodic=episodic,
    )
    context.agent.system_prompt = self._prompt_builder.build(
        context, retrieved, owner_name=context.owner_name
    )
```

- [ ] **Step 5: Wire summarizer trigger in _run_agent after response**

After the memory capture block (after line 310), add:

```python
    # Trigger background summarizer if history is past window
    from napyclaw.summarizer import Summarizer, should_summarize
    if should_summarize(
        context.agent.history,
        verbatim_turns=context.verbatim_turns,
        summary_turns=context.summary_turns,
    ):
        async def _notify_ws(payload: dict) -> None:
            if self._ws_notify:
                await self._ws_notify(payload)

        summarizer = Summarizer(client=context.active_client, notify=_notify_ws)
        identity_block = self._prompt_builder.build(
            context, RetrievedMemory(), owner_name=context.owner_name
        ).split("## My Responsibilities")[0]
        import asyncio as _asyncio
        _asyncio.create_task(summarizer.run(
            history=context.agent.history,
            identity_block=identity_block,
            group_id=context.group_id,
            db=self.db,
            verbatim_turns=context.verbatim_turns,
            summary_turns=context.summary_turns,
        ))
```

Add a `_notify_backstage` helper to `NapyClaw` that POSTs to comms `/backstage/event`:
```python
async def _notify_backstage(self, group_id: str, event: dict) -> None:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.config.comms_url}/backstage/event",
                json={"group_id": group_id, "event": event},
            ):
                pass
    except Exception:
        pass
```

Update the `_notify_ws` inner function inside `_run_agent` to call it:
```python
async def _notify_ws(payload: dict) -> None:
    await self._notify_backstage(context.group_id, payload)
```

Also wire `notify=_notify_ws` into tool construction. In `_run_agent`, before calling `context.agent.tools = self._build_tools(ctx)`, the tools factory must receive a notify callable. Update `_build_tools` usage so specialist tools receive `_notify_ws`. Since `_build_tools` is a factory passed in at construction time, pass `notify` as a kwarg:
```python
ctx.agent.tools = self._build_tools(ctx, notify=_notify_ws)
```

Update the factory signature in any callers of `NapyClaw` to accept `notify` kwarg on `build_tools`.

- [ ] **Step 6: Update save_group_context calls in app.py**

The `_run_agent` method calls `self.db.save_group_context(...)` at the end. Add the new fields:

```python
await self.db.save_group_context(
    group_id=context.group_id,
    default_name=context.default_name,
    display_name=context.display_name,
    nicknames=context.nicknames,
    owner_id=context.owner_id,
    provider=context.active_client.provider,
    model=context.active_client.model,
    is_first_interaction=context.is_first_interaction,
    history=context.agent.history,
    job_title=context.job_title,
    memory_enabled=context.memory_enabled,
    channel_type=context.channel_type,
    job_description=context.job_description,
    verbatim_turns=context.verbatim_turns,
    summary_turns=context.summary_turns,
)
```

- [ ] **Step 7: Update _FakeDB and _make_context in tests/test_app.py**

Update `_make_context` to include new fields:
```python
def _make_context(...) -> GroupContext:
    ...
    return GroupContext(
        ...
        job_description=None,
        verbatim_turns=7,
        summary_turns=5,
        owner_name="Nate",
    )
```

Update `_FakeDB.save_group_context` to accept `**kwargs` (it already does).

- [ ] **Step 8: Run all tests**

```bash
cd /c/Users/NathanKaemingk/source/napyclaw && .venv/Scripts/python -m pytest tests/ -v --ignore=tests/test_db.py 2>&1 | tail -40
```

Expected: All non-DB tests pass

- [ ] **Step 9: Commit**

```bash
git add napyclaw/app.py napyclaw/prompt_builder.py tests/test_app.py
git commit -m "feat: wire PromptBuilder and summarizer into app.py, update GroupContext"
```

---

## Task 7: User Identity in comms

**Spec Reference:** Section 7 (User Identity)

**Files:**
- Modify: `services/comms/main.py`
- Modify: `services/comms/static/index.html`

- [ ] **Step 1: Extract Tailscale-User-Name header in the /inbound webhook handler**

In `services/comms/main.py`, update the `_handle_inbound` route on `WebChannel`. The owner name is extracted in `comms` not in the bot — comms receives the Tailscale header and passes `owner_name` in the webhook payload.

In `services/comms/main.py`, the WebSocket `message` handler already forwards to `_bot_webhook`. Update it to also pass `owner_name`. First, store the identity on WS connect via a `hello` message — the frontend sends `owner_name` as part of `hello`:

In the `websocket_endpoint` hello handler:
```python
if msg_type == "hello":
    group_id = data.get("group_id")
    owner_name = data.get("owner_name", "")
    # Store per-connection owner_name
    _ws_owner_name = owner_name
    ...
```

Add module-level `_ws_owner_name: str = ""` alongside `_ws_connection`.

Update the message forwarder:
```python
elif msg_type == "message":
    group_id = data.get("group_id", "")
    text = data.get("text", "")
    display_name = data.get("display_name")
    _buffer_message(group_id, "user", text)
    if _bot_webhook:
        payload: dict = {
            "group_id": group_id,
            "sender_id": "owner",
            "sender_name": _ws_owner_name or "owner",
            "text": text,
        }
        if display_name:
            payload["display_name"] = display_name
        asyncio.create_task(_http_post(_bot_webhook, payload))
```

- [ ] **Step 2: Add identity endpoint to comms for the frontend**

Add to `services/comms/main.py`:

```python
@app.get("/identity")
async def get_identity(request: Request) -> dict:
    raw = request.headers.get("Tailscale-User-Name", "")
    if raw and "@" in raw:
        name = raw.split("@")[0].strip().title()
    elif raw:
        name = raw.strip().title()
    else:
        name = ""
    return {"owner_name": name, "raw": raw}
```

Add `from fastapi import Request` to imports.

- [ ] **Step 3: Fetch identity in frontend and send with hello**

In `services/comms/static/index.html`, add identity fetch at init and display top-left.

In the `<style>` section, add:
```css
#identity-bar {
    padding: 8px 14px;
    border-bottom: 1px solid var(--border);
    font-size: 11px;
    color: var(--muted);
}
#identity-name { color: var(--accent-lt); font-weight: 600; }
```

In the sidebar HTML, add above `#sidebar-header`:
```html
<div id="identity-bar">Logged in as <span id="identity-name">...</span></div>
```

In the `<script>` section, add:
```javascript
let ownerName = '';

async function loadIdentity() {
  try {
    const resp = await fetch('/identity');
    const data = await resp.json();
    ownerName = data.owner_name || '';
    document.getElementById('identity-name').textContent = ownerName || 'unknown';
  } catch (e) {
    document.getElementById('identity-name').textContent = 'unknown';
  }
}
```

Update `ws.onopen` to send `owner_name` in hello:
```javascript
ws.onopen = () => {
    reconnectDelay = 1000;
    if (currentGroup) {
        ws.send(JSON.stringify({ type: 'hello', group_id: currentGroup, owner_name: ownerName }));
    }
};
```

Update `selectGroup` hello send:
```javascript
if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: 'hello', group_id: groupId, owner_name: ownerName }));
}
```

Add `loadIdentity()` to the init section alongside `connect()` and `loadSpecialists()`.

- [ ] **Step 4: Rebuild and restart comms**

```bash
docker compose up -d --no-deps comms && docker compose up -d --no-deps comms-tailscale
```

- [ ] **Step 5: Verify identity appears in UI**

Open `http://localhost:8001` (or Tailscale IP). Confirm top-left shows "Logged in as Nate" (or empty if Tailscale header not present in local dev — that's expected).

- [ ] **Step 6: Commit**

```bash
git add services/comms/main.py services/comms/static/index.html
git commit -m "feat: user identity — extract Tailscale-User-Name, display in sidebar, pass to bot"
```

---

## Task 8: Backstage WebSocket Events in comms

**Spec Reference:** Section 8 (Backstage Column — WS event types table)

**Files:**
- Modify: `services/comms/main.py`

- [ ] **Step 1: Add correction window state to comms**

Backstage events from the bot flow through comms to the frontend. Comms needs to:
1. Forward backstage events from bot → frontend over WS
2. Handle `memory_adjusted` and `memory_excluded` from frontend → forward to bot

Add module-level state to `services/comms/main.py`:

```python
# Correction window items: token -> {content, entry_type, group_id, turns_remaining}
_correction_window: dict[str, dict] = {}
```

- [ ] **Step 2: Add new backstage endpoints for bot to push events**

Add to `services/comms/main.py`:

```python
class BackstageEventRequest(BaseModel):
    group_id: str
    event: dict


@app.post("/backstage/event")
async def backstage_event(req: BackstageEventRequest) -> dict:
    """Bot pushes a backstage event; comms forwards to WS frontend."""
    event_type = req.event.get("type")

    if event_type == "memory_queued":
        token = req.event.get("token", "")
        _correction_window[token] = {
            "content": req.event.get("content", ""),
            "entry_type": req.event.get("entry_type", ""),
            "group_id": req.group_id,
            "turns_remaining": req.event.get("window_turns_remaining", 3),
        }

    await _push_to_ws({**req.event, "group_id": req.group_id})
    return {"ok": True}
```

- [ ] **Step 3: Handle memory_adjusted and memory_excluded from frontend WS**

In `websocket_endpoint`, add handlers inside `async for data in ws.iter_json()`:

```python
elif msg_type == "memory_adjusted":
    token = data.get("token", "")
    revised = data.get("revised_content", "")
    if token in _correction_window:
        _correction_window[token]["content"] = revised
        if _bot_webhook:
            asyncio.create_task(_http_post(_bot_webhook, {
                "type": "memory_adjusted",
                "token": token,
                "revised_content": revised,
            }))

elif msg_type == "memory_excluded":
    token = data.get("token", "")
    _correction_window.pop(token, None)
    if _bot_webhook:
        asyncio.create_task(_http_post(_bot_webhook, {
            "type": "memory_excluded",
            "token": token,
        }))
```

- [ ] **Step 4: Commit**

```bash
git add services/comms/main.py
git commit -m "feat: comms backstage events — forward bot events to WS, handle adjust/exclude"
```

---

## Task 9: Backstage Column UI

**Spec Reference:** Section 8 (Backstage Column — layout, interaction model, sticky area, colors), Section 3 (Trust Tiers — Adjust/Exclude behavior), Resolved Decisions (background colors)

**Files:**
- Modify: `services/comms/static/index.html`

- [ ] **Step 1: Add three-panel layout CSS**

Replace the `#app` style and add backstage styles in the `<style>` block:

```css
#app { display: flex; height: 100vh; overflow: hidden; }

/* --- Backstage column --- */
#backstage {
    width: 260px; min-width: 260px;
    background: var(--bg); border-left: 1px solid var(--border);
    display: flex; flex-direction: column; overflow: hidden;
    font-size: 12px;
}
#backstage-sticky {
    flex-shrink: 0; border-bottom: 1px solid var(--border);
    padding: 8px; display: flex; flex-direction: column; gap: 6px;
    max-height: 40%; overflow-y: auto;
}
#backstage-sticky:empty::after {
    content: 'No pending items'; color: var(--muted); font-size: 11px; padding: 4px;
}
#backstage-turns {
    flex: 1; overflow-y: auto; padding: 8px;
    display: flex; flex-direction: column; gap: 6px;
}

.backstage-turn {
    border-radius: 6px; border: 1px solid var(--border);
    overflow: hidden;
}
.backstage-turn.current { background: var(--bg); }
.backstage-turn.focused { background: #1e3a5f; border-color: #2563eb; }
.backstage-turn-header {
    padding: 5px 8px; cursor: pointer;
    display: flex; justify-content: space-between; align-items: center;
    color: var(--muted); font-size: 11px;
}
.backstage-turn.current .backstage-turn-header { color: var(--text); }
.backstage-turn.focused .backstage-turn-header { color: var(--accent-lt); }
.backstage-turn-body { display: none; padding: 6px 8px; }
.backstage-turn.open .backstage-turn-body { display: block; }

.backstage-event { margin-bottom: 5px; line-height: 1.4; }
.backstage-event .ev-label { color: var(--muted); font-size: 10px; text-transform: uppercase; letter-spacing: .5px; }
.backstage-event .ev-content { color: var(--text); margin-top: 2px; }

/* pending approval card */
.bs-approval {
    background: var(--surface); border: 1px solid var(--danger);
    border-radius: 6px; padding: 8px;
}
.bs-approval .bs-label { color: var(--danger); font-size: 10px; text-transform: uppercase; margin-bottom: 4px; }
.bs-approval .bs-content { color: var(--text); margin-bottom: 6px; font-size: 12px; }
.bs-approval .bs-actions { display: flex; gap: 4px; }
.bs-approval button { padding: 3px 8px; border-radius: 4px; border: 1px solid var(--border);
                       background: var(--bg); color: var(--text); cursor: pointer; font-size: 11px; }
.bs-approval button.approve { border-color: #22c55e; color: #22c55e; }
.bs-approval button.deny { border-color: var(--danger); color: var(--danger); }

/* correction window card */
.bs-queued {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 6px; padding: 8px;
}
.bs-queued .bs-label { color: var(--muted); font-size: 10px; text-transform: uppercase; margin-bottom: 4px; }
.bs-queued .bs-content { color: var(--text); margin-bottom: 4px; font-size: 12px; }
.bs-queued .bs-turns { color: var(--muted); font-size: 10px; margin-bottom: 6px; }
.bs-queued .bs-actions { display: flex; gap: 4px; align-items: center; }
.bs-queued .bs-adjust-input { display: none; width: 100%; background: var(--bg);
    border: 1px solid var(--border); border-radius: 4px; color: var(--text);
    padding: 4px 6px; font-size: 12px; margin-top: 4px; }
.bs-queued button { padding: 3px 8px; border-radius: 4px; border: 1px solid var(--border);
                    background: var(--bg); color: var(--text); cursor: pointer; font-size: 11px; }
```

- [ ] **Step 2: Add backstage HTML panel**

Inside `<div id="app">`, after the `#chat` div, add:

```html
<!-- Backstage -->
<div id="backstage">
  <div id="backstage-sticky"></div>
  <div id="backstage-turns"></div>
</div>
```

- [ ] **Step 3: Add backstage JavaScript**

Add to the `<script>` section:

```javascript
// backstage: { [group_id]: [{turnIndex, events: []}] }
const backstageTurns = {};
let currentTurnIndex = 0;
let focusedTurnIndex = null;

function bsAddEvent(groupId, event) {
    if (!backstageTurns[groupId]) backstageTurns[groupId] = [];
    const turns = backstageTurns[groupId];
    if (!turns.length || turns[turns.length - 1].sealed) {
        turns.push({ index: currentTurnIndex, events: [], sealed: false });
    }
    turns[turns.length - 1].events.push(event);
    if (groupId === currentGroup) renderBackstage(groupId);
}

function bsSealTurn(groupId) {
    if (!backstageTurns[groupId]) return;
    const turns = backstageTurns[groupId];
    if (turns.length) {
        turns[turns.length - 1].sealed = true;
        currentTurnIndex++;
    }
}

function renderBackstage(groupId) {
    const turnsEl = document.getElementById('backstage-turns');
    turnsEl.innerHTML = '';
    const turns = backstageTurns[groupId] || [];
    [...turns].reverse().forEach(turn => {
        const isCurrent = !turn.sealed;
        const isFocused = turn.index === focusedTurnIndex;
        const div = document.createElement('div');
        div.className = 'backstage-turn' +
            (isCurrent ? ' current' : '') +
            (isFocused ? ' focused' : '') +
            (isCurrent || isFocused ? ' open' : '');
        div.dataset.turnIndex = turn.index;
        const header = document.createElement('div');
        header.className = 'backstage-turn-header';
        header.textContent = isCurrent ? 'Current turn' : `Turn ${turn.index + 1}`;
        header.onclick = () => toggleBackstageTurn(div, turn.index, groupId);
        const body = document.createElement('div');
        body.className = 'backstage-turn-body';
        turn.events.forEach(ev => {
            body.appendChild(buildBackstageEvent(ev));
        });
        div.appendChild(header);
        div.appendChild(body);
        turnsEl.appendChild(div);
    });
}

function toggleBackstageTurn(div, turnIndex, groupId) {
    const wasOpen = div.classList.contains('open');
    // collapse all non-current turns
    document.querySelectorAll('.backstage-turn:not(.current)').forEach(el => {
        el.classList.remove('open', 'focused');
    });
    if (!wasOpen) {
        div.classList.add('open', 'focused');
        focusedTurnIndex = turnIndex;
    } else {
        focusedTurnIndex = null;
    }
}

function buildBackstageEvent(ev) {
    const wrap = document.createElement('div');
    wrap.className = 'backstage-event';
    const label = document.createElement('div');
    label.className = 'ev-label';
    const content = document.createElement('div');
    content.className = 'ev-content';
    label.textContent = (ev.type || '').replace(/_/g, ' ');
    if (ev.tool_name) {
        content.textContent = `${ev.tool_name}: ${esc(JSON.stringify(ev.args || {}))}`;
    } else if (ev.content) {
        content.textContent = esc(ev.content);
    } else if (ev.blocks) {
        content.textContent = ev.blocks.map(b => `${b.type}: ${b.count}`).join(', ');
    } else if (ev.event) {
        content.textContent = ev.event.replace(/_/g, ' ');
    }
    wrap.appendChild(label);
    wrap.appendChild(content);
    return wrap;
}

function renderBackstageSticky() {
    const sticky = document.getElementById('backstage-sticky');
    sticky.innerHTML = '';
    // pending approvals
    (messages['admin'] || []).filter(m => m.role === 'approval').forEach(m => {
        if (!m.data || m.data._resolved) return;
        sticky.appendChild(buildApprovalCard(m.data));
    });
    // memory pending approval items
    (_pendingApprovals || []).forEach(item => {
        sticky.appendChild(buildBsApprovalCard(item));
    });
    // correction window items
    (_correctionWindow || []).forEach(item => {
        sticky.appendChild(buildBsQueuedCard(item));
    });
}

const _pendingApprovals = [];
const _correctionWindow = [];

function buildBsApprovalCard(item) {
    const div = document.createElement('div');
    div.className = 'bs-approval';
    div.innerHTML = `
        <div class="bs-label">Pending: ${esc(item.entry_type)}</div>
        <div class="bs-content">${esc(item.content)}</div>
        <div class="bs-actions">
            <button class="approve" onclick="bsApprove('${esc(item.token)}', this.closest('.bs-approval'))">Approve</button>
            <button class="deny" onclick="bsReject('${esc(item.token)}', this.closest('.bs-approval'))">Reject</button>
        </div>`;
    return div;
}

function buildBsQueuedCard(item) {
    const div = document.createElement('div');
    div.className = 'bs-queued';
    div.dataset.token = item.token;
    div.innerHTML = `
        <div class="bs-label">${esc(item.entry_type)}</div>
        <div class="bs-content">${esc(item.content)}</div>
        <div class="bs-turns">${item.turns_remaining} turn${item.turns_remaining !== 1 ? 's' : ''} to review</div>
        <div class="bs-actions">
            <button onclick="bsAdjustOpen('${esc(item.token)}', this.closest('.bs-queued'))">Adjust</button>
            <button onclick="bsExclude('${esc(item.token)}', this.closest('.bs-queued'))">Exclude</button>
        </div>
        <input class="bs-adjust-input" placeholder="Revised content..." onkeydown="bsAdjustKey(event, '${esc(item.token)}')">`;
    return div;
}

function bsApprove(token, card) {
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'approval', token, decision: 'approve_once' }));
    }
    card.remove();
    const idx = _pendingApprovals.findIndex(i => i.token === token);
    if (idx >= 0) _pendingApprovals.splice(idx, 1);
}

function bsReject(token, card) {
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'memory_excluded', token }));
    }
    card.remove();
    const idx = _pendingApprovals.findIndex(i => i.token === token);
    if (idx >= 0) _pendingApprovals.splice(idx, 1);
}

function bsAdjustOpen(token, card) {
    const input = card.querySelector('.bs-adjust-input');
    input.style.display = 'block';
    input.focus();
}

function bsAdjustKey(e, token) {
    if (e.key === 'Enter') {
        const revised = e.target.value.trim();
        if (!revised) return;
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: 'memory_adjusted', token, revised_content: revised }));
        }
        e.target.closest('.bs-queued').remove();
        const idx = _correctionWindow.findIndex(i => i.token === token);
        if (idx >= 0) _correctionWindow.splice(idx, 1);
    }
}

function bsExclude(token, card) {
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'memory_excluded', token }));
    }
    card.remove();
    const idx = _correctionWindow.findIndex(i => i.token === token);
    if (idx >= 0) _correctionWindow.splice(idx, 1);
}
```

- [ ] **Step 4: Wire WS message handler to backstage events**

In the existing `ws.onmessage` handler, add cases for backstage event types:

```javascript
} else if (data.type === 'context_used') {
    bsAddEvent(data.group_id, data);
} else if (data.type === 'tool_call') {
    bsAddEvent(data.group_id, data);
} else if (data.type === 'background_task') {
    bsAddEvent(data.group_id, data);
} else if (data.type === 'memory_committed') {
    bsAddEvent(data.group_id, data);
} else if (data.type === 'memory_queued') {
    _correctionWindow.push({
        token: data.token,
        entry_type: data.entry_type || data.type,
        content: data.content,
        turns_remaining: data.window_turns_remaining || 3,
    });
    renderBackstageSticky();
} else if (data.type === 'memory_pending_approval') {
    _pendingApprovals.push(data);
    renderBackstageSticky();
}
```

Also seal the current turn when a new assistant message arrives — add to the `data.type === 'message'` handler:
```javascript
if (data.role === 'assistant' || (!data.role && data.group_id)) {
    bsSealTurn(data.group_id);
}
```

- [ ] **Step 5: Wire chat bubble click to backstage turn focus**

In `buildBubble`, add a `data-turn-index` attribute and click handler:

```javascript
function buildBubble(role, text, turnIndex) {
    const wrap = document.createElement('div');
    const safeRole = (role === 'user' || role === 'assistant') ? role : 'assistant';
    wrap.className = `msg ${safeRole}`;
    wrap.dataset.turnIndex = turnIndex;
    wrap.onclick = () => focusTurnInBackstage(turnIndex);
    ...
}

function focusTurnInBackstage(turnIndex) {
    const turns = document.querySelectorAll('.backstage-turn');
    turns.forEach(el => el.classList.remove('open', 'focused'));
    const target = [...turns].find(el => parseInt(el.dataset.turnIndex) === turnIndex);
    if (target) {
        target.classList.add('open', 'focused');
        focusedTurnIndex = turnIndex;
        target.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
}
```

Update `renderMessages` to pass turn index to `buildBubble`:
```javascript
(messages[groupId] || []).forEach((m, i) => {
    const turnIndex = Math.floor(i / 2);
    if (m.role === 'approval') {
        container.appendChild(buildApprovalCard(m.data));
    } else {
        container.appendChild(buildBubble(m.role, m.text, turnIndex));
    }
});
```

- [ ] **Step 6: Rebuild comms and verify three-panel layout**

```bash
docker compose up -d --no-deps comms && docker compose up -d --no-deps comms-tailscale
```

Open the UI. Confirm three panels visible. Send a message. Confirm Backstage column shows current turn block. Click the assistant message bubble and confirm the corresponding Backstage turn expands and focuses.

- [ ] **Step 7: Commit**

```bash
git add services/comms/static/index.html
git commit -m "feat: Backstage column — three-panel layout, sticky approvals, turn linking"
```

---

## Task 10: Onboarding System Prompt Language

**Spec Reference:** Section 5 (Onboarding Flow — all 5 steps)

This is already handled by `PromptBuilder._build_blocks` in Task 3 — when `job_description` is `None`, `_ONBOARDING_INSTRUCTION` is injected in Block 1. No additional code needed.

- [ ] **Step 1: Verify onboarding prompt fires on new specialist**

Create a new specialist in the UI. Confirm the first bot message asks about the user's needs rather than just introducing itself.

- [ ] **Step 2: Commit if any prompt copy tweaks needed**

```bash
git add napyclaw/prompt_builder.py
git commit -m "fix: onboarding prompt copy tweaks"
```

---

## Task 11: Final Integration Test

**Spec Reference:** All sections — validates end-to-end coverage

- [ ] **Step 1: Run full test suite**

```bash
cd /c/Users/NathanKaemingk/source/napyclaw && .venv/Scripts/python -m pytest tests/ --ignore=tests/test_db.py -v 2>&1 | tail -50
```

Expected: All non-DB tests pass

- [ ] **Step 2: Run DB integration tests if Postgres is running**

```bash
cd /c/Users/NathanKaemingk/source/napyclaw && .venv/Scripts/python -m pytest tests/test_db.py -v 2>&1 | tail -30
```

Expected: All DB tests pass (or skip if Postgres unavailable)

- [ ] **Step 3: Rebuild and restart bot**

```bash
docker compose build bot && docker compose up -d --no-deps bot
```

- [ ] **Step 4: End-to-end smoke test**

1. Open webchat UI — confirm three panels, identity shows in sidebar
2. Create new specialist — confirm onboarding prompt asks about role
3. Complete onboarding — confirm `set_job_description` fires, Backstage shows memory events
4. Send 15+ messages — confirm Backstage shows "summarizer_ran" event, correction window items appear in sticky area
5. Click Exclude on a queued item — confirm it disappears without chat interruption
6. Click Adjust on a queued item — confirm inline edit field appears, submit revised content

- [ ] **Step 5: Final commit**

```bash
git add -A
git commit -m "feat: specialist memory, onboarding, backstage UI — issues #9 and #10"
```
