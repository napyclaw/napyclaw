from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from napyclaw.agent import Agent
from napyclaw.db import Database, ScheduledTask
from napyclaw.models.openai_client import LLMUnavailableError

if TYPE_CHECKING:
    from napyclaw.app import GroupContext, GroupQueue
    from napyclaw.channels.base import Channel
    from napyclaw.memory import MemoryBackend
    from napyclaw.models.base import LLMClient


class Scheduler:
    """Polls the DB for due tasks and fires them through agents."""

    def __init__(
        self,
        db: Database,
        queue: GroupQueue,
        channel: Channel,
        contexts: dict[str, GroupContext],
        build_client: Any = None,
        poll_interval: int = 60,
        max_retries: int = 3,
    ) -> None:
        self._db = db
        self._queue = queue
        self._channel = channel
        self._contexts = contexts
        self._build_client = build_client
        self._poll_interval = poll_interval
        self._max_retries = max_retries
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _poll_loop(self) -> None:
        while self._running:
            try:
                await self._poll_once()
            except Exception:
                pass  # Log in production; never crash the poll loop
            await asyncio.sleep(self._poll_interval)

    async def _poll_once(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        due_tasks = await self._db.list_due_tasks(now)

        for task in due_tasks:
            ctx = self._contexts.get(task.group_id)
            if ctx is None:
                # Group deleted — pause the task
                await self._db.update_task_status(task.id, "paused")
                continue

            await self._queue.run(
                task.group_id, self._run_task(task, ctx)
            )

    async def _run_task(self, task: ScheduledTask, ctx: GroupContext) -> None:
        start = time.monotonic()

        # Build a one-off agent for this task
        if task.model and task.provider and self._build_client:
            try:
                client = self._build_client(task.provider, task.model)
            except Exception:
                client = ctx.active_client
        else:
            client = ctx.active_client

        agent = Agent(
            client=client,
            tools=ctx.agent.tools,
            system_prompt=ctx.agent.system_prompt,
        )

        try:
            result = await agent.run(task.prompt)
            duration_ms = int((time.monotonic() - start) * 1000)

            # Send result to channel
            await self._channel.send(task.group_id, result)

            # Log success
            await self._db.log_task_run(
                id=str(uuid.uuid4()),
                task_id=task.id,
                ran_at=datetime.now(timezone.utc).isoformat(),
                status="success",
                result_snippet=result[:200] if result else None,
                duration_ms=duration_ms,
            )

            # Compute next_run
            if task.schedule_type == "once":
                await self._db.update_task_status(task.id, "completed")
            elif task.schedule_type == "interval":
                next_run = (
                    datetime.now(timezone.utc)
                    + timedelta(seconds=int(task.schedule_value))
                ).isoformat()
                await self._db.update_task_status(
                    task.id, "active", next_run=next_run, retry_count=0
                )
            else:
                # cron — simplified: just set next_run to interval-like offset
                # Full cron parsing would use croniter; for now treat as interval
                await self._db.update_task_status(
                    task.id, "active", retry_count=0
                )

        except (LLMUnavailableError, Exception) as exc:
            duration_ms = int((time.monotonic() - start) * 1000)
            new_retry = task.retry_count + 1

            await self._db.log_task_run(
                id=str(uuid.uuid4()),
                task_id=task.id,
                ran_at=datetime.now(timezone.utc).isoformat(),
                status="failed",
                result_snippet=str(exc)[:200],
                duration_ms=duration_ms,
            )

            if new_retry >= self._max_retries:
                await self._db.update_task_status(task.id, "failed")
            else:
                # Exponential backoff: 5s * 2^retry_count
                backoff = 5 * (2 ** task.retry_count)
                next_retry = (
                    datetime.now(timezone.utc) + timedelta(seconds=backoff)
                ).isoformat()
                await self._db.update_task_status(
                    task.id, "active", next_run=next_retry, retry_count=new_retry
                )
