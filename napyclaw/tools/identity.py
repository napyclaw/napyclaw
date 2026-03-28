from __future__ import annotations

from typing import TYPE_CHECKING

from napyclaw.tools.base import Tool

if TYPE_CHECKING:
    from napyclaw.db import Database


class RenameBot(Tool):
    name = "rename_bot"
    description = "Change the bot's display name in this channel. Owner only."
    parameters = {
        "type": "object",
        "properties": {
            "new_name": {"type": "string", "description": "The new display name"},
        },
        "required": ["new_name"],
    }

    def __init__(self, db: Database, group_id: str, owner_id: str) -> None:
        self._db = db
        self._group_id = group_id
        self._owner_id = owner_id

    async def execute(self, *, sender_id: str = "", **kwargs) -> str:
        if sender_id != self._owner_id:
            return "Only the channel owner can rename me."

        new_name = kwargs.get("new_name", "").strip()
        if not new_name:
            return "Error: new_name is required."

        # Capitalize first letter
        new_name = new_name[0].upper() + new_name[1:]

        try:
            ctx = await self._db.load_group_context(self._group_id)
            if ctx is None:
                return "Error: group context not found."

            await self._db.save_group_context(
                group_id=self._group_id,
                default_name=ctx["default_name"],
                display_name=new_name,
                nicknames=ctx["nicknames"],
                owner_id=ctx["owner_id"],
                provider=ctx["provider"],
                model=ctx["model"],
                is_first_interaction=ctx["is_first_interaction"],
                history=ctx["history"],
            )
            return f"Renamed to {new_name}"
        except Exception as exc:
            return f"Error: could not rename — {exc}"


class AddNickname(Tool):
    name = "add_nickname"
    description = "Add a nickname for the bot in this channel. Any user can add one."
    parameters = {
        "type": "object",
        "properties": {
            "nickname": {"type": "string", "description": "The nickname to add"},
        },
        "required": ["nickname"],
    }

    def __init__(self, db: Database, group_id: str) -> None:
        self._db = db
        self._group_id = group_id

    async def execute(self, **kwargs) -> str:
        nickname = kwargs.get("nickname", "").strip()
        if not nickname:
            return "Error: nickname is required."

        try:
            ctx = await self._db.load_group_context(self._group_id)
            if ctx is None:
                return "Error: group context not found."

            nicknames = ctx["nicknames"]
            if nickname not in nicknames:
                nicknames.append(nickname)

            await self._db.save_group_context(
                group_id=self._group_id,
                default_name=ctx["default_name"],
                display_name=ctx["display_name"],
                nicknames=nicknames,
                owner_id=ctx["owner_id"],
                provider=ctx["provider"],
                model=ctx["model"],
                is_first_interaction=ctx["is_first_interaction"],
                history=ctx["history"],
            )
            return f"Nickname '{nickname}' added"
        except Exception as exc:
            return f"Error: could not add nickname — {exc}"


class ClearNicknames(Tool):
    name = "clear_nicknames"
    description = "Remove all nicknames for the bot in this channel. Owner only."
    parameters = {
        "type": "object",
        "properties": {},
    }

    def __init__(self, db: Database, group_id: str, owner_id: str) -> None:
        self._db = db
        self._group_id = group_id
        self._owner_id = owner_id

    async def execute(self, *, sender_id: str = "", **kwargs) -> str:
        if sender_id != self._owner_id:
            return "Only the channel owner can clear nicknames."

        try:
            ctx = await self._db.load_group_context(self._group_id)
            if ctx is None:
                return "Error: group context not found."

            await self._db.save_group_context(
                group_id=self._group_id,
                default_name=ctx["default_name"],
                display_name=ctx["display_name"],
                nicknames=[],
                owner_id=ctx["owner_id"],
                provider=ctx["provider"],
                model=ctx["model"],
                is_first_interaction=ctx["is_first_interaction"],
                history=ctx["history"],
            )
            return "All nicknames cleared"
        except Exception as exc:
            return f"Error: could not clear nicknames — {exc}"


class SwitchModel(Tool):
    name = "switch_model"
    description = "Switch the LLM provider and model for this channel. Owner only."
    parameters = {
        "type": "object",
        "properties": {
            "provider": {
                "type": "string",
                "enum": ["openai", "ollama"],
                "description": "LLM provider",
            },
            "model": {"type": "string", "description": "Model name"},
        },
        "required": ["provider", "model"],
    }

    def __init__(self, db: Database, group_id: str, owner_id: str) -> None:
        self._db = db
        self._group_id = group_id
        self._owner_id = owner_id

    async def execute(self, *, sender_id: str = "", **kwargs) -> str:
        if sender_id != self._owner_id:
            return "Only the channel owner can switch models."

        provider = kwargs.get("provider", "")
        model = kwargs.get("model", "")
        if not provider or not model:
            return "Error: provider and model are required."

        try:
            ctx = await self._db.load_group_context(self._group_id)
            if ctx is None:
                return "Error: group context not found."

            await self._db.save_group_context(
                group_id=self._group_id,
                default_name=ctx["default_name"],
                display_name=ctx["display_name"],
                nicknames=ctx["nicknames"],
                owner_id=ctx["owner_id"],
                provider=provider,
                model=model,
                is_first_interaction=ctx["is_first_interaction"],
                history=ctx["history"],
            )
            return f"Switched to {provider}/{model}"
        except Exception as exc:
            return f"Error: could not switch model — {exc}"
