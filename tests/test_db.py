"""Integration tests for Database — require Postgres via docker-compose.

Run `docker compose up -d` before running these tests.
Set TEST_DB_URL to override the default connection string.
"""
import os
import uuid
from datetime import datetime, timezone

import pytest

from napyclaw.db import Database, ScheduledTask

TEST_DB_URL = os.environ.get(
    "TEST_DB_URL",
    "postgresql://napyclaw:napyclaw-local@localhost:5432/napyclaw",
)

_TRUNCATE = """
TRUNCATE messages, group_contexts, scheduled_tasks, task_run_log, shield_log, specialist_memory
"""


@pytest.fixture
async def db():
    try:
        database = Database(TEST_DB_URL)
        await database.connect()
    except Exception as exc:
        pytest.skip(f"Postgres not available: {exc}")
    await database.pool.execute(_TRUNCATE)
    yield database
    await database.pool.execute(_TRUNCATE)
    await database.close()


async def test_connect_and_query(db: Database):
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
    assert await db.load_group_context("nonexistent") is None


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
    await db.log_shield_detection(
        id="shield1",
        group_id="C001",
        sender_id="U001",
        detection_types=["api_key"],
        timestamp="2026-03-25T12:00:00Z",
    )


async def test_save_and_load_webchat_columns(db):
    """New columns round-trip correctly."""
    await db.save_group_context(
        group_id="g-web",
        default_name="Rex",
        display_name="Rex",
        nicknames=["Rex"],
        owner_id="owner",
        provider="openai",
        model="gpt-4o",
        is_first_interaction=True,
        history=[],
        job_title="Stats Researcher",
        memory_enabled=True,
        channel_type="webchat",
    )
    row = await db.load_group_context("g-web")
    assert row is not None
    assert row["nicknames"] == ["Rex"]
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
        job_title=None,
        channel_type="webchat",
        # memory_enabled omitted — should default to True
    )
    row = await db.load_group_context("g-default")
    assert row["memory_enabled"] is True


async def test_load_webchat_specialists(db):
    """load_webchat_specialists returns only webchat rows, not admin."""
    await db.save_group_context(
        group_id="spec-1", default_name="Rex", display_name="Rex",
        nicknames=["Rex"], owner_id="owner", provider="openai", model="gpt-4o",
        is_first_interaction=True, history=[],
        job_title="Stats Researcher", memory_enabled=True, channel_type="webchat",
    )
    await db.save_group_context(
        group_id="admin", default_name="Admin", display_name="Admin",
        nicknames=[], owner_id="system", provider="openai", model="gpt-4o",
        is_first_interaction=True, history=[],
        job_title=None, memory_enabled=False, channel_type="webchat",
    )
    specialists = await db.load_webchat_specialists()
    ids = [s["group_id"] for s in specialists]
    assert "spec-1" in ids
    assert "admin" not in ids


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


async def test_search_specialist_memory_returns_similarity(db: Database):
    entry_id = str(uuid.uuid4())
    # Insert with embedding=None — should be excluded from search results
    await db.save_specialist_memory(
        id=entry_id,
        group_id="g-search",
        type="task",
        content="Build the Q2 forecast model.",
        embedding=None,
    )
    # Search with a dummy embedding — rows with embedding=None are filtered out
    fake_embedding = [0.1] * 768
    results = await db.search_specialist_memory(
        group_id="g-search",
        embedding=fake_embedding,
        top_k=5,
    )
    # No results because the only row has embedding=None
    assert isinstance(results, list)
    assert len(results) == 0


async def test_update_specialist_memory_missing_id_raises(db: Database):
    with pytest.raises(ValueError, match="not found"):
        await db.update_specialist_memory("nonexistent-id", content="whatever")
