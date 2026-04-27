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
            history=ctx.agent.history,
            job_title=ctx.job_title,
            memory_enabled=ctx.memory_enabled,
            channel_type=ctx.channel_type,
            job_description=description,
            verbatim_turns=ctx.verbatim_turns,
            summary_turns=ctx.summary_turns,
        )
        ctx.job_description = description
        return "Job description saved."


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
            "scope": {
                "type": "string",
                "enum": ["specialist"],
                "description": "Memory scope. Always 'specialist' for now.",
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
        embed_fn: Callable[[str], Awaitable[list[float]]],
    ) -> None:
        self._db = db
        self._group_id = group_id
        self._notify = notify
        self._embed_fn = embed_fn

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
                f"Responsibility pending approval. I've proposed adding this to your memory. "
                f"You'll see it in the Backstage panel — please approve or reject it there."
            )

        try:
            embedding = await self._embed_fn(content)
        except Exception:
            embedding = None

        new_id = entry_id or str(uuid.uuid4())
        await self._db.save_specialist_memory(
            id=new_id,
            group_id=self._group_id,
            type=entry_type,
            content=content,
            embedding=embedding,
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
