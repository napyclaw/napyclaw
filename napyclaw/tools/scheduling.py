from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from napyclaw.db import ScheduledTask
from napyclaw.tools.base import Tool

if TYPE_CHECKING:
    from napyclaw.db import Database


class ScheduleTaskTool(Tool):
    name = "schedule_task"
    description = "Create, list, or cancel scheduled tasks for the current channel."
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["create", "list", "cancel"],
                "description": "Action to perform",
            },
            "prompt": {"type": "string", "description": "Prompt to run (create only)"},
            "schedule_type": {
                "type": "string",
                "enum": ["cron", "interval", "once"],
                "description": "Schedule type (create only)",
            },
            "schedule_value": {
                "type": "string",
                "description": "Cron expression, interval in seconds, or ISO-8601 datetime (create only)",
            },
            "model": {"type": "string", "description": "Override model (optional)"},
            "provider": {"type": "string", "description": "Override provider (optional)"},
            "task_id": {"type": "string", "description": "Task ID (cancel only)"},
        },
        "required": ["action"],
    }

    def __init__(self, db: Database, group_id: str, owner_id: str) -> None:
        self._db = db
        self._group_id = group_id
        self._owner_id = owner_id

    async def execute(self, **kwargs) -> str:
        action = kwargs.get("action", "")

        if action == "create":
            return await self._create(kwargs)
        elif action == "list":
            return await self._list()
        elif action == "cancel":
            return await self._cancel(kwargs)
        else:
            return f"Error: unknown action '{action}'. Use create, list, or cancel."

    async def _create(self, kwargs: dict) -> str:
        prompt = kwargs.get("prompt")
        schedule_type = kwargs.get("schedule_type")
        schedule_value = kwargs.get("schedule_value")

        if not prompt:
            return "Error: prompt is required for create."
        if not schedule_type:
            return "Error: schedule_type is required for create."
        if not schedule_value:
            return "Error: schedule_value is required for create."

        task = ScheduledTask(
            id=str(uuid.uuid4()),
            group_id=self._group_id,
            owner_id=self._owner_id,
            prompt=prompt,
            schedule_type=schedule_type,
            schedule_value=schedule_value,
            model=kwargs.get("model"),
            provider=kwargs.get("provider"),
            status="active",
            next_run=schedule_value if schedule_type == "once" else None,
            retry_count=0,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        try:
            await self._db.save_scheduled_task(task)
            return json.dumps({
                "id": task.id,
                "prompt": task.prompt[:80],
                "schedule_type": task.schedule_type,
                "schedule_value": task.schedule_value,
                "status": task.status,
            })
        except Exception as exc:
            return f"Error: could not create task — {exc}"

    async def _list(self) -> str:
        try:
            tasks = await self._db.list_scheduled_tasks(self._group_id)
        except Exception as exc:
            return f"Error: could not list tasks — {exc}"

        if not tasks:
            return "No scheduled tasks."

        return json.dumps([
            {
                "id": t.id,
                "prompt": t.prompt[:80],
                "schedule_type": t.schedule_type,
                "schedule_value": t.schedule_value,
                "status": t.status,
                "next_run": t.next_run,
            }
            for t in tasks
        ])

    async def _cancel(self, kwargs: dict) -> str:
        task_id = kwargs.get("task_id")
        if not task_id:
            return "Error: task_id is required for cancel."

        # Verify the task belongs to this group
        try:
            tasks = await self._db.list_scheduled_tasks(self._group_id)
        except Exception as exc:
            return f"Error: could not verify task — {exc}"

        matching = [t for t in tasks if t.id == task_id]
        if not matching:
            return f"Error: task {task_id} not found in this channel."

        try:
            await self._db.update_task_status(task_id, "paused")
            return json.dumps({"id": task_id, "status": "paused"})
        except Exception as exc:
            return f"Error: could not cancel task — {exc}"
