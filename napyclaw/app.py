from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any

from napyclaw.agent import Agent, AgentLoopError
from napyclaw.channels.base import Channel, Message
from napyclaw.config import Config
from napyclaw.db import Database
from napyclaw.injection_guard import InjectionGuard
from napyclaw.memory import MemoryBackend
from napyclaw.models.base import LLMClient
from napyclaw.models.openai_client import LLMUnavailableError
from napyclaw.shield import ContentShield
from napyclaw.tools.base import Tool

_SEARCH_BLOCK = re.compile(
    r"<!-- SEARCH_RESULTS -->.*?<!-- /SEARCH_RESULTS -->",
    re.DOTALL,
)


def _strip_search_results(text: str) -> str:
    return _SEARCH_BLOCK.sub("", text).strip()


@dataclass
class GroupContext:
    group_id: str
    default_name: str
    display_name: str
    nicknames: list[str]
    owner_id: str
    active_client: LLMClient
    is_first_interaction: bool
    agent: Agent


class GroupQueue:
    """Ensures only one agent runs per group at a time."""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}

    async def run(self, group_id: str, coro: Any) -> Any:
        if group_id not in self._locks:
            self._locks[group_id] = asyncio.Lock()
        async with self._locks[group_id]:
            return await coro


class NapyClaw:
    def __init__(
        self,
        config: Config,
        db: Database,
        channel: Channel,
        build_tools: Any = None,
        build_client: Any = None,
        build_system_prompt: Any = None,
        injection_guard: InjectionGuard | None = None,
        shield: ContentShield | None = None,
        memory: MemoryBackend | None = None,
    ) -> None:
        self.config = config
        self.db = db
        self.channel = channel
        self.queue = GroupQueue()
        self.contexts: dict[str, GroupContext] = {}
        self.bot_user_id: str = ""
        self._injection_guard = injection_guard
        self._shield = shield
        self._memory = memory

        # Pluggable factories — set by start() or injected for testing
        self._build_tools = build_tools or (lambda ctx: [])
        self._build_client = build_client
        self._build_system_prompt = build_system_prompt or self._default_system_prompt

    async def start(self) -> None:
        """Initialize and run napyclaw."""
        self.config.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.config.groups_dir.mkdir(parents=True, exist_ok=True)

        # Restore group contexts from DB
        all_contexts = await self.db.load_all_group_contexts()
        for row in all_contexts:
            client = self._build_client(row["provider"], row["model"])
            ctx = GroupContext(
                group_id=row["group_id"],
                default_name=row["default_name"],
                display_name=row["display_name"],
                nicknames=row["nicknames"],
                owner_id=row["owner_id"],
                active_client=client,
                is_first_interaction=row["is_first_interaction"],
                agent=Agent(
                    client=client,
                    tools=[],  # Wired after context creation
                    system_prompt="",
                    config=self.config,
                    history=row["history"],
                    injection_guard=self._injection_guard,
                ),
            )
            ctx.agent.tools = self._build_tools(ctx)
            ctx.agent.system_prompt = self._build_system_prompt(ctx)
            self.contexts[row["group_id"]] = ctx

        # Register message handler and connect
        self.channel.register_handler(self.handle_message)
        await self.channel.connect()

    def _matches_trigger(self, text: str, context: GroupContext) -> bool:
        """Check if message text triggers the bot for this group."""
        lower = text.lower()

        # Check all trigger names
        names = [context.default_name, context.display_name] + context.nicknames
        for name in names:
            if f"@{name.lower()}" in lower:
                return True

        # Check Slack native mention
        if self.bot_user_id and f"<@{self.bot_user_id}>" in text:
            return True

        return False

    def _matches_any_trigger(self, text: str) -> GroupContext | None:
        """Check if message matches any known group context trigger."""
        for ctx in self.contexts.values():
            if self._matches_trigger(text, ctx):
                return ctx
        return None

    async def handle_message(self, msg: Message) -> None:
        """Handle an incoming message from any channel."""
        import uuid
        from datetime import datetime, timezone

        # Scan and redact before any storage or processing
        if self._shield:
            shield_result = self._shield.scan(msg.text)
            clean_text = shield_result.clean_text
            if shield_result.has_blocked:
                await self.db.log_shield_detection(
                    id=str(uuid.uuid4()),
                    group_id=msg.group_id,
                    sender_id=msg.sender_id,
                    detection_types=[d.type for d in shield_result.detections if d.redacted],
                    timestamp=datetime.now(timezone.utc).isoformat(),
                )
        else:
            clean_text = msg.text

        # Store redacted text
        await self.db.save_message(
            id=str(uuid.uuid4()),
            group_id=msg.group_id,
            sender_id=msg.sender_id,
            sender_name=msg.sender_name,
            text=clean_text,
            timestamp=msg.timestamp,
            channel_type=msg.channel_type,
        )

        # Get or create context for this group
        ctx = self.contexts.get(msg.group_id)

        if ctx is None:
            # Check if any trigger matches — for new groups, check the default name pattern
            # or Slack native mention
            should_create = False
            if self.bot_user_id and f"<@{self.bot_user_id}>" in msg.text:
                should_create = True

            if not should_create:
                return  # No context, no trigger — store only

            # Create new group context
            channel_name = msg.channel_name
            default_name = channel_name[0].upper() + channel_name[1:] + "_napy"

            client = self._build_client(
                self.config.default_provider, self.config.default_model
            )
            ctx = GroupContext(
                group_id=msg.group_id,
                default_name=default_name,
                display_name=default_name,
                nicknames=[],
                owner_id=msg.sender_id,
                active_client=client,
                is_first_interaction=True,
                agent=Agent(
                    client=client,
                    tools=[],
                    system_prompt="",
                    config=self.config,
                    injection_guard=self._injection_guard,
                ),
            )
            ctx.agent.tools = self._build_tools(ctx)
            ctx.agent.system_prompt = self._build_system_prompt(ctx)
            self.contexts[msg.group_id] = ctx
        else:
            # Existing context — check trigger
            if not self._matches_trigger(msg.text, ctx):
                return

        # Run agent through the group queue
        await self.queue.run(msg.group_id, self._run_agent(ctx, msg, clean_text))

    async def _run_agent(self, context: GroupContext, msg: Message, text: str) -> None:
        """Execute agent and send response. Runs inside GroupQueue lock."""
        # Inject relevant memories into system prompt for this turn
        if self._memory:
            memories = await self._memory.search(text, context.group_id)
            base_prompt = self._build_system_prompt(context)
            if memories:
                memory_block = "\n\n## Relevant memories\n" + "\n".join(f"- {m}" for m in memories)
                context.agent.system_prompt = base_prompt + memory_block
            else:
                context.agent.system_prompt = base_prompt

        try:
            await self.channel.set_typing(msg.group_id, True)
            response = await context.agent.run(text, sender_id=msg.sender_id)
            await self.channel.send(msg.group_id, response)
        except AgentLoopError:
            await self.channel.send(
                msg.group_id,
                "I got stuck in a loop. Please try rephrasing your request.",
            )
        except LLMUnavailableError as e:
            await self.channel.send(msg.group_id, str(e))
        finally:
            await self.channel.set_typing(msg.group_id, False)

        # Capture exchange to memory — strip raw search results, keep synthesis
        if self._memory and response:
            await self._memory.capture(
                f"User: {text}\nAssistant: {_strip_search_results(response)}",
                group_id=context.group_id,
            )

        # Persist context after each turn
        if context.is_first_interaction:
            context.is_first_interaction = False

        await self.db.save_group_context(
            group_id=context.group_id,
            default_name=context.default_name,
            display_name=context.display_name,
            nicknames=context.nicknames,
            owner_id=context.owner_id,
            provider=context.active_client.provider,
            model=context.active_client.model,
            is_first_interaction=context.is_first_interaction,
            history=context.agent.history,
            job_title=getattr(context, 'job_title', None),
            memory_enabled=getattr(context, 'memory_enabled', True),
            channel_type=getattr(context, 'channel_type', 'slack'),
        )

    def _default_system_prompt(self, ctx: GroupContext) -> str:
        parts = [f"Your name is {ctx.display_name}."]

        if ctx.nicknames:
            parts.append(f"Your nicknames are: {', '.join(ctx.nicknames)}.")

        if ctx.is_first_interaction:
            parts.append(
                "This is your first conversation in this channel. "
                "Introduce yourself and ask if the user would like to give you a different name."
            )

        parts.append(
            f"You are running on {ctx.active_client.provider}/{ctx.active_client.model}."
        )

        return " ".join(parts)
