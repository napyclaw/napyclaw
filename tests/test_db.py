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
