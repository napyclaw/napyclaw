import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from napyclaw.agent import Agent
from napyclaw.app import GroupContext, GroupQueue
from napyclaw.db import Database, ScheduledTask
from napyclaw.models.base import ChatResponse
from napyclaw.models.openai_client import LLMUnavailableError
from napyclaw.scheduler import Scheduler


def _make_context(group_id: str = "C001") -> GroupContext:
    client = MagicMock()
    client.provider = "ollama"
    client.model = "llama3.3:latest"
    client.context_window = 8192
    client.chat = AsyncMock(
        return_value=ChatResponse(text="Task result", tool_calls=None, finish_reason="stop")
    )
    return GroupContext(
        group_id=group_id,
        default_name="General_napy",
        display_name="General_napy",
        nicknames=[],
        owner_id="U001",
        active_client=client,
        is_first_interaction=False,
        agent=Agent(client=client, tools=[], system_prompt="Test."),
    )


async def _make_task(db: Database, group_id: str = "C001", **overrides) -> ScheduledTask:
    defaults = {
        "id": str(uuid.uuid4()),
        "group_id": group_id,
        "owner_id": "U001",
        "prompt": "Say hello",
        "schedule_type": "once",
        "schedule_value": "2026-01-01T00:00:00Z",
        "model": None,
        "provider": None,
        "status": "active",
        "next_run": "2026-01-01T00:00:00Z",
        "retry_count": 0,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    defaults.update(overrides)
    task = ScheduledTask(**defaults)
    await db.save_scheduled_task(task)
    return task


@pytest.fixture
async def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "test.db")
    await database.init()
    return database


class TestScheduler:
    async def test_fires_due_task(self, db: Database):
        ctx = _make_context()
        contexts = {"C001": ctx}
        channel = AsyncMock()
        queue = GroupQueue()

        task = await _make_task(db)

        scheduler = Scheduler(
            db=db,
            queue=queue,
            channel=channel,
            contexts=contexts,
        )

        await scheduler._poll_once()

        # Channel should receive the task result
        channel.send.assert_called_once_with("C001", "Task result")

    async def test_once_task_completed_after_run(self, db: Database):
        ctx = _make_context()
        contexts = {"C001": ctx}
        channel = AsyncMock()
        queue = GroupQueue()

        task = await _make_task(db, schedule_type="once")

        scheduler = Scheduler(db=db, queue=queue, channel=channel, contexts=contexts)
        await scheduler._poll_once()

        tasks = await db.list_scheduled_tasks("C001")
        assert tasks[0].status == "completed"

    async def test_interval_task_reschedules(self, db: Database):
        ctx = _make_context()
        contexts = {"C001": ctx}
        channel = AsyncMock()
        queue = GroupQueue()

        task = await _make_task(
            db, schedule_type="interval", schedule_value="3600"
        )

        scheduler = Scheduler(db=db, queue=queue, channel=channel, contexts=contexts)
        await scheduler._poll_once()

        tasks = await db.list_scheduled_tasks("C001")
        assert tasks[0].status == "active"
        assert tasks[0].next_run is not None
        assert tasks[0].next_run > task.next_run  # Next run is in the future

    async def test_missing_group_pauses_task(self, db: Database):
        contexts: dict = {}  # No contexts — group doesn't exist
        channel = AsyncMock()
        queue = GroupQueue()

        task = await _make_task(db)

        scheduler = Scheduler(db=db, queue=queue, channel=channel, contexts=contexts)
        await scheduler._poll_once()

        tasks = await db.list_scheduled_tasks("C001")
        assert tasks[0].status == "paused"
        channel.send.assert_not_called()

    async def test_failure_increments_retry(self, db: Database):
        client = MagicMock()
        client.provider = "ollama"
        client.model = "llama3.3:latest"
        client.context_window = 8192
        client.chat = AsyncMock(side_effect=LLMUnavailableError("down"))

        ctx = _make_context()
        ctx.active_client = client

        contexts = {"C001": ctx}
        channel = AsyncMock()
        queue = GroupQueue()

        task = await _make_task(db, retry_count=0)

        scheduler = Scheduler(db=db, queue=queue, channel=channel, contexts=contexts)
        await scheduler._poll_once()

        tasks = await db.list_scheduled_tasks("C001")
        assert tasks[0].retry_count == 1
        assert tasks[0].status == "active"

    async def test_max_retries_marks_failed(self, db: Database):
        client = MagicMock()
        client.provider = "ollama"
        client.model = "llama3.3:latest"
        client.context_window = 8192
        client.chat = AsyncMock(side_effect=LLMUnavailableError("down"))

        ctx = _make_context()
        ctx.active_client = client

        contexts = {"C001": ctx}
        channel = AsyncMock()
        queue = GroupQueue()

        # Already at retry_count=2, max_retries=3
        task = await _make_task(db, retry_count=2)

        scheduler = Scheduler(
            db=db, queue=queue, channel=channel, contexts=contexts, max_retries=3
        )
        await scheduler._poll_once()

        tasks = await db.list_scheduled_tasks("C001")
        assert tasks[0].status == "failed"

    async def test_model_override_uses_custom_client(self, db: Database):
        custom_client = MagicMock()
        custom_client.provider = "openai"
        custom_client.model = "gpt-4o"
        custom_client.context_window = 128000
        custom_client.chat = AsyncMock(
            return_value=ChatResponse(
                text="Custom model response", tool_calls=None, finish_reason="stop"
            )
        )

        ctx = _make_context()
        contexts = {"C001": ctx}
        channel = AsyncMock()
        queue = GroupQueue()

        task = await _make_task(
            db, model="gpt-4o", provider="openai"
        )

        scheduler = Scheduler(
            db=db,
            queue=queue,
            channel=channel,
            contexts=contexts,
            build_client=lambda p, m: custom_client,
        )
        await scheduler._poll_once()

        channel.send.assert_called_once_with("C001", "Custom model response")
