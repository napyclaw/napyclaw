import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from napyclaw.agent import Agent
from napyclaw.app import GroupContext, GroupQueue
from napyclaw.db import ScheduledTask
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


class _FakeDB:
    """In-memory DB stub for scheduler tests."""

    def __init__(self):
        self._tasks: dict[str, ScheduledTask] = {}

    async def save_scheduled_task(self, task: ScheduledTask) -> None:
        self._tasks[task.id] = task

    async def list_scheduled_tasks(self, group_id: str) -> list[ScheduledTask]:
        return [t for t in self._tasks.values() if t.group_id == group_id]

    async def list_due_tasks(self, now: str) -> list[ScheduledTask]:
        return [
            t for t in self._tasks.values()
            if t.status == "active" and t.next_run is not None and t.next_run <= now
        ]

    async def update_task_status(
        self,
        task_id: str,
        status: str,
        next_run: str | None = None,
        retry_count: int | None = None,
    ) -> None:
        if task_id not in self._tasks:
            return
        t = self._tasks[task_id]
        from dataclasses import replace
        self._tasks[task_id] = replace(
            t,
            status=status,
            next_run=next_run if next_run is not None else t.next_run,
            retry_count=retry_count if retry_count is not None else t.retry_count,
        )

    async def log_task_run(self, **_) -> None:
        pass


@pytest.fixture
def db() -> _FakeDB:
    return _FakeDB()


class TestScheduler:
    async def test_fires_due_task(self, db: _FakeDB):
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

    async def test_once_task_completed_after_run(self, db: _FakeDB):
        ctx = _make_context()
        contexts = {"C001": ctx}
        channel = AsyncMock()
        queue = GroupQueue()

        task = await _make_task(db, schedule_type="once")

        scheduler = Scheduler(db=db, queue=queue, channel=channel, contexts=contexts)
        await scheduler._poll_once()

        tasks = await db.list_scheduled_tasks("C001")
        assert tasks[0].status == "completed"

    async def test_interval_task_reschedules(self, db: _FakeDB):
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

    async def test_missing_group_pauses_task(self, db: _FakeDB):
        contexts: dict = {}  # No contexts — group doesn't exist
        channel = AsyncMock()
        queue = GroupQueue()

        task = await _make_task(db)

        scheduler = Scheduler(db=db, queue=queue, channel=channel, contexts=contexts)
        await scheduler._poll_once()

        tasks = await db.list_scheduled_tasks("C001")
        assert tasks[0].status == "paused"
        channel.send.assert_not_called()

    async def test_failure_increments_retry(self, db: _FakeDB):
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

    async def test_max_retries_marks_failed(self, db: _FakeDB):
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

    async def test_model_override_uses_custom_client(self, db: _FakeDB):
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


async def test_pending_approval_retry_schedule():
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
    job.advance()
    assert job.next_retry_delay() == 120
    job.advance()
    assert job.next_retry_delay() == 300
    job.advance()
    assert job.next_retry_delay() == 600
    job.advance()
    assert job.next_retry_delay() == 1200
    job.advance()
    assert job.is_exhausted()


async def test_pending_approval_job_resolves_on_approved(respx_mock):
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


async def test_pending_approval_job_not_resolved_when_pending(respx_mock):
    import httpx
    from napyclaw.scheduler import PendingApprovalJob

    respx_mock.get("http://egressguard:8000/status/tok2").mock(
        return_value=httpx.Response(200, json={"status": "pending", "token": "tok2"})
    )
    job = PendingApprovalJob(
        token="tok2",
        hostname="example.com",
        egress_url="http://egressguard:8000",
        original_tool="web_search",
        original_kwargs={"query": "test"},
        group_id="C123",
    )
    resolved = await job.poll()
    assert resolved is False


async def test_pending_approval_job_returns_false_on_network_error(respx_mock):
    import httpx
    from napyclaw.scheduler import PendingApprovalJob

    respx_mock.get("http://egressguard:8000/status/tok3").mock(
        side_effect=httpx.ConnectError("connection refused")
    )
    job = PendingApprovalJob(
        token="tok3",
        hostname="example.com",
        egress_url="http://egressguard:8000",
        original_tool="web_search",
        original_kwargs={"query": "test"},
        group_id="C123",
    )
    resolved = await job.poll()
    assert resolved is False
