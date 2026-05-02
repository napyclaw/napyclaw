from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from napyclaw.summarizer import Summarizer, SummaryItem, should_summarize


def _make_history(n: int) -> list[dict]:
    history = []
    for i in range(n):
        history.append({"role": "user", "content": f"Message {i}"})
        history.append({"role": "assistant", "content": f"Response {i}"})
    return history


class TestShouldSummarize:
    def test_triggers_when_over_limit(self):
        history = _make_history(13)  # 13 exchanges = 26 messages > default 12
        assert should_summarize(history, verbatim_turns=7, summary_turns=5) is True

    def test_no_trigger_when_under_limit(self):
        history = _make_history(6)
        assert should_summarize(history, verbatim_turns=7, summary_turns=5) is False

    def test_exactly_at_limit_no_trigger(self):
        history = _make_history(6)  # 12 messages = 6 exchanges, exactly at 12
        assert should_summarize(history, verbatim_turns=7, summary_turns=5) is False

    def test_custom_window(self):
        history = _make_history(6)  # 6 exchanges > verbatim_turns(3) + summary_turns(2) = 5
        assert should_summarize(history, verbatim_turns=3, summary_turns=2) is True


class TestSummaryItemRouting:
    def _make_summarizer(self):
        embed_fn = AsyncMock(return_value=[0.1] * 768)
        return Summarizer(client=MagicMock(), notify=AsyncMock(), embed_fn=embed_fn)

    async def test_responsibility_routes_to_pending_approval(self):
        notify = AsyncMock()
        summarizer = Summarizer(client=MagicMock(), notify=notify, embed_fn=AsyncMock(return_value=[0.1]*768))
        item = SummaryItem(type="responsibility", content="I own P&L.", scope="specialist")
        await summarizer._route_item(item, group_id="g-spec", db=MagicMock())
        call_args = notify.call_args[0][0]
        assert call_args["type"] == "memory_pending_approval"

    async def test_task_routes_to_correction_window(self):
        notify = AsyncMock()
        db = MagicMock()
        db.save_specialist_memory = AsyncMock()
        summarizer = Summarizer(client=MagicMock(), notify=notify, embed_fn=AsyncMock(return_value=[0.1]*768))
        item = SummaryItem(type="task", content="Prepare Q2 forecast.", scope="specialist")
        await summarizer._route_item(item, group_id="g-spec", db=db)
        call_args = notify.call_args[0][0]
        assert call_args["type"] == "memory_queued"
        assert call_args["window_turns_remaining"] == 3

    async def test_task_saves_with_embedding(self):
        notify = AsyncMock()
        db = MagicMock()
        db.save_specialist_memory = AsyncMock()
        embed_fn = AsyncMock(return_value=[0.1] * 768)
        summarizer = Summarizer(client=MagicMock(), notify=notify, embed_fn=embed_fn)
        item = SummaryItem(type="task", content="Prepare Q2 forecast.", scope="specialist")
        await summarizer._route_item(item, group_id="g-spec", db=db)
        args = db.save_specialist_memory.call_args[1]
        assert args["embedding"] == [0.1] * 768

    async def test_fact_routes_to_correction_window(self):
        notify = AsyncMock()
        db = MagicMock()
        db.save_specialist_memory = AsyncMock()
        summarizer = Summarizer(client=MagicMock(), notify=notify, embed_fn=AsyncMock(return_value=[0.1]*768))
        item = SummaryItem(type="fact", content="ETL runs at 3am.", scope="specialist")
        await summarizer._route_item(item, group_id="g-spec", db=db)
        call_args = notify.call_args[0][0]
        assert call_args["type"] == "memory_queued"
