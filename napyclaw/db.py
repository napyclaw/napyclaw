from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import asyncpg


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


class Database:
    def __init__(self, db_url: str) -> None:
        self._db_url = db_url
        self._pool: asyncpg.Pool | None = None

    @property
    def pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("Database.connect() not called")
        return self._pool

    async def connect(self) -> None:
        import asyncpg
        self._pool = await asyncpg.create_pool(self._db_url, min_size=1, max_size=10)

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
            self._pool = None

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
        await self.pool.execute(
            """
            INSERT INTO messages
                (id, group_id, sender_id, sender_name, text, timestamp, channel_type)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (id) DO NOTHING
            """,
            id, group_id, sender_id, sender_name, text, timestamp, channel_type,
        )

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
    ) -> None:
        await self.pool.execute(
            """
            INSERT INTO group_contexts
                (group_id, default_name, display_name, nicknames, owner_id,
                 provider, model, is_first_interaction, history,
                 job_title, memory_enabled, channel_type)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
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
            job_title,
            memory_enabled,
            channel_type,
        )

    async def load_group_context(self, group_id: str) -> dict | None:
        row = await self.pool.fetchrow(
            "SELECT * FROM group_contexts WHERE group_id = $1", group_id
        )
        return _row_to_ctx(row) if row is not None else None

    async def load_all_group_contexts(self) -> list[dict]:
        rows = await self.pool.fetch("SELECT * FROM group_contexts")
        return [_row_to_ctx(row) for row in rows]

    async def load_webchat_specialists(self) -> list[dict]:
        """Return webchat GroupContexts excluding the admin DM row."""
        rows = await self.pool.fetch(
            "SELECT * FROM group_contexts WHERE channel_type = 'webchat' AND group_id != 'admin'"
        )
        return [_row_to_ctx(row) for row in rows]

    async def save_scheduled_task(self, task: ScheduledTask) -> None:
        await self.pool.execute(
            """
            INSERT INTO scheduled_tasks
                (id, group_id, owner_id, prompt, schedule_type, schedule_value,
                 model, provider, status, next_run, retry_count, created_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
            ON CONFLICT (id) DO UPDATE SET
                group_id       = EXCLUDED.group_id,
                owner_id       = EXCLUDED.owner_id,
                prompt         = EXCLUDED.prompt,
                schedule_type  = EXCLUDED.schedule_type,
                schedule_value = EXCLUDED.schedule_value,
                model          = EXCLUDED.model,
                provider       = EXCLUDED.provider,
                status         = EXCLUDED.status,
                next_run       = EXCLUDED.next_run,
                retry_count    = EXCLUDED.retry_count,
                created_at     = EXCLUDED.created_at
            """,
            task.id, task.group_id, task.owner_id, task.prompt,
            task.schedule_type, task.schedule_value, task.model, task.provider,
            task.status, task.next_run, task.retry_count, task.created_at,
        )

    async def list_scheduled_tasks(self, group_id: str) -> list[ScheduledTask]:
        rows = await self.pool.fetch(
            "SELECT * FROM scheduled_tasks WHERE group_id = $1", group_id
        )
        return [_row_to_task(row) for row in rows]

    async def list_due_tasks(self, now: str) -> list[ScheduledTask]:
        rows = await self.pool.fetch(
            "SELECT * FROM scheduled_tasks WHERE status = 'active' AND next_run <= $1",
            now,
        )
        return [_row_to_task(row) for row in rows]

    async def update_task_status(
        self,
        task_id: str,
        status: str,
        next_run: str | None = None,
        retry_count: int | None = None,
    ) -> None:
        await self.pool.execute(
            """
            UPDATE scheduled_tasks
            SET status      = $1,
                next_run    = COALESCE($2, next_run),
                retry_count = COALESCE($3, retry_count)
            WHERE id = $4
            """,
            status, next_run, retry_count, task_id,
        )

    async def log_task_run(
        self,
        id: str,
        task_id: str,
        ran_at: str,
        status: str,
        result_snippet: str | None,
        duration_ms: int,
    ) -> None:
        await self.pool.execute(
            """
            INSERT INTO task_run_log
                (id, task_id, ran_at, status, result_snippet, duration_ms)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (id) DO NOTHING
            """,
            id, task_id, ran_at, status, result_snippet, duration_ms,
        )

    async def log_shield_detection(
        self,
        id: str,
        group_id: str,
        sender_id: str,
        detection_types: list[str],
        timestamp: str,
    ) -> None:
        await self.pool.execute(
            """
            INSERT INTO shield_log
                (id, group_id, sender_id, detection_types, timestamp)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (id) DO NOTHING
            """,
            id, group_id, sender_id, json.dumps(detection_types), timestamp,
        )


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
    }


def _row_to_task(row) -> ScheduledTask:
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
