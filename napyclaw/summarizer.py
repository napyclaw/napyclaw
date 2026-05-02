from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Awaitable

if TYPE_CHECKING:
    from napyclaw.db import Database
    from napyclaw.models.base import LLMClient


_ASK_FIRST_TYPES = {"responsibility", "job_description"}

_SUMMARIZE_PROMPT = """You are summarizing a conversation batch that is about to be removed from active memory.

Context:
{identity_block}

Exchanges to summarize:
{exchanges}

Instructions:
- Identify what was learned, decided, established, or agreed upon.
- Correct any typos or abandoned trains of thought — capture intent, not exact words.
- Ignore small talk, greetings, and error corrections.
- Return ONLY a JSON array of items. Each item has: type, content, scope.
- type must be one of: responsibility, task, tool, resource, preference, fact
- scope must be: specialist
- Return [] if nothing meaningful happened.

Example output:
[
  {{"type": "task", "content": "Prepare Q2 forecast by end of April.", "scope": "specialist"}},
  {{"type": "resource", "content": "https://example.com/forecast-template", "scope": "specialist"}}
]"""


@dataclass
class SummaryItem:
    type: str
    content: str
    scope: str


def should_summarize(
    history: list[dict],
    verbatim_turns: int = 7,
    summary_turns: int = 5,
) -> bool:
    """Return True when history has more exchanges than verbatim_turns + summary_turns."""
    total_turns = len(history) // 2
    return total_turns > verbatim_turns + summary_turns


def _exchanges_to_summarize(
    history: list[dict],
    verbatim_turns: int,
    summary_turns: int,
) -> list[dict]:
    """Return the oldest summary_turns exchanges (as flat message list)."""
    keep_messages = verbatim_turns * 2
    summary_messages = summary_turns * 2
    start = max(0, len(history) - keep_messages - summary_messages)
    end = max(0, len(history) - keep_messages)
    return history[start:end]


def _format_exchanges(exchanges: list[dict]) -> str:
    lines = []
    for msg in exchanges:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        lines.append(f"{role.capitalize()}: {content}")
    return "\n".join(lines)


class Summarizer:
    def __init__(
        self,
        client: LLMClient,
        notify: Callable[[dict], Awaitable[None]],
        embed_fn: Callable[[str], Awaitable[list[float]]],
    ) -> None:
        self._client = client
        self._notify = notify
        self._embed_fn = embed_fn

    async def run(
        self,
        history: list[dict],
        identity_block: str,
        group_id: str,
        db: Database,
        verbatim_turns: int = 7,
        summary_turns: int = 5,
    ) -> None:
        """Fire-and-forget: summarize oldest batch, route items by trust tier."""
        exchanges = _exchanges_to_summarize(history, verbatim_turns, summary_turns)
        if not exchanges:
            return

        prompt = _SUMMARIZE_PROMPT.format(
            identity_block=identity_block,
            exchanges=_format_exchanges(exchanges),
        )

        try:
            response = await self._client.chat(
                messages=[
                    {
                        "role": "user",
                        "content": (
                            "You are a memory summarizer. Return only valid JSON.\n\n"
                            + prompt
                        ),
                    }
                ],
                tools=[],
            )
            raw = response.text.strip()
            # Strip markdown code fences if present
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()
            items_data = json.loads(raw)
        except Exception:
            return

        for item_data in items_data:
            try:
                item = SummaryItem(
                    type=item_data.get("type", "fact"),
                    content=item_data.get("content", "").strip(),
                    scope=item_data.get("scope", "specialist"),
                )
                if not item.content:
                    continue
                await self._route_item(item, group_id=group_id, db=db)
            except Exception:
                continue

        await self._notify({
            "type": "background_task",
            "group_id": group_id,
            "event": "summarizer_ran",
        })

    async def _route_item(
        self,
        item: SummaryItem,
        group_id: str,
        db: Database,
    ) -> None:
        token = str(uuid.uuid4())
        if item.type in _ASK_FIRST_TYPES:
            await self._notify({
                "type": "memory_pending_approval",
                "group_id": group_id,
                "token": token,
                "entry_type": item.type,
                "content": item.content,
            })
        else:
            try:
                embedding = await self._embed_fn(item.content)
            except Exception:
                embedding = None
            await db.save_specialist_memory(
                id=token,
                group_id=group_id,
                type=item.type,
                content=item.content,
                embedding=embedding,
            )
            await self._notify({
                "type": "memory_queued",
                "group_id": group_id,
                "token": token,
                "entry_type": item.type,
                "content": item.content,
                "window_turns_remaining": 3,
            })
