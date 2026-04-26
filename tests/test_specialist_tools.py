from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from napyclaw.tools.specialist_tools import (
    ManageSpecialistMemoryTool,
    SaveToMemoryTool,
    SetJobDescriptionTool,
)


class TestSetJobDescriptionTool:
    def _make_tool(self):
        db = MagicMock()
        db.save_group_context = AsyncMock()
        ctx = MagicMock()
        ctx.group_id = "g-spec"
        ctx.default_name = "Amy"
        ctx.display_name = "Amy"
        ctx.nicknames = ["Amy"]
        ctx.owner_id = "owner"
        ctx.active_client.provider = "openai"
        ctx.active_client.model = "gpt-4o"
        ctx.is_first_interaction = False
        ctx.history = []
        ctx.job_title = "Analyst"
        ctx.memory_enabled = True
        ctx.channel_type = "webchat"
        ctx.job_description = None
        ctx.verbatim_turns = 7
        ctx.summary_turns = 5
        return SetJobDescriptionTool(db=db, ctx=ctx), db, ctx

    async def test_saves_job_description(self):
        tool, db, ctx = self._make_tool()
        result = await tool.execute(description="I own the monthly P&L report.")
        db.save_group_context.assert_called_once()
        call_kwargs = db.save_group_context.call_args[1]
        assert call_kwargs["job_description"] == "I own the monthly P&L report."
        assert "saved" in result.lower() or "updated" in result.lower()

    async def test_empty_description_returns_error(self):
        tool, db, ctx = self._make_tool()
        result = await tool.execute(description="   ")
        assert "error" in result.lower()
        db.save_group_context.assert_not_called()


class TestManageSpecialistMemoryTool:
    def _make_tool(self):
        db = MagicMock()
        db.save_specialist_memory = AsyncMock()
        db.update_specialist_memory = AsyncMock()
        db.delete_specialist_memory = AsyncMock()
        notify = AsyncMock()
        embed_fn = AsyncMock(return_value=[0.1] * 768)
        return ManageSpecialistMemoryTool(db=db, group_id="g-spec", notify=notify, embed_fn=embed_fn), db, notify

    async def test_add_task_saves_to_db(self):
        tool, db, notify = self._make_tool()
        result = await tool.execute(action="add", type="task", content="Prepare Q2 forecast.")
        db.save_specialist_memory.assert_called_once()
        args = db.save_specialist_memory.call_args[1]
        assert args["type"] == "task"
        assert args["content"] == "Prepare Q2 forecast."
        assert args["embedding"] == [0.1] * 768
        assert "saved" in result.lower() or "added" in result.lower()

    async def test_add_task_notifies(self):
        tool, db, notify = self._make_tool()
        await tool.execute(action="add", type="task", content="Prepare Q2 forecast.")
        notify.assert_called_once()

    async def test_add_responsibility_does_not_save_directly(self):
        tool, db, notify = self._make_tool()
        result = await tool.execute(action="add", type="responsibility", content="Own P&L.")
        db.save_specialist_memory.assert_not_called()
        assert "confirm" in result.lower() or "approval" in result.lower() or "pending" in result.lower()

    async def test_delete_calls_db(self):
        tool, db, notify = self._make_tool()
        result = await tool.execute(action="delete", type="task", entry_id="entry-123")
        db.delete_specialist_memory.assert_called_once_with("entry-123")

    async def test_unknown_action_returns_error(self):
        tool, db, notify = self._make_tool()
        result = await tool.execute(action="invalid", type="task", content="x")
        assert "error" in result.lower()


class TestSaveToMemoryTool:
    def _make_tool(self):
        memory = MagicMock()
        memory.capture = AsyncMock()
        notify = AsyncMock()
        return SaveToMemoryTool(memory=memory, group_id="g-spec", notify=notify), memory, notify

    async def test_saves_content(self):
        tool, memory, notify = self._make_tool()
        result = await tool.execute(content="User prefers bullet summaries.")
        memory.capture.assert_called_once_with(
            "User prefers bullet summaries.", group_id="g-spec"
        )
        assert "saved" in result.lower()

    async def test_notifies_after_save(self):
        tool, memory, notify = self._make_tool()
        await tool.execute(content="User prefers bullet summaries.")
        notify.assert_called_once()

    async def test_empty_content_returns_error(self):
        tool, memory, notify = self._make_tool()
        result = await tool.execute(content="  ")
        assert "error" in result.lower()
        memory.capture.assert_not_called()
