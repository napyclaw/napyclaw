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

CREATE TABLE IF NOT EXISTS egress_verdicts (
    hostname     TEXT PRIMARY KEY,
    verdict      TEXT NOT NULL,
    confidence   REAL NOT NULL,
    reason       TEXT NOT NULL,
    cached_until TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS egress_log (
    id        TEXT PRIMARY KEY,
    hostname  TEXT NOT NULL,
    verdict   TEXT NOT NULL,
    reason    TEXT NOT NULL,
    source    TEXT NOT NULL,
    timestamp TEXT NOT NULL
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
