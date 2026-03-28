from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from napyclaw.agent import Agent
from napyclaw.memory import NullMemory

if TYPE_CHECKING:
    from napyclaw.channels.base import Channel
    from napyclaw.models.base import LLMClient
    from napyclaw.tools.base import Tool


@dataclass
class PrivateSession:
    user_id: str
    dm_channel_id: str
    origin_group_id: str
    agent: Agent
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_activity: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # Idle timeout in seconds (30 minutes)
    IDLE_TIMEOUT: int = 1800

    @classmethod
    def create(
        cls,
        user_id: str,
        dm_channel_id: str,
        origin_group_id: str,
        client: LLMClient,
        tools: list[Tool] | None = None,
    ) -> PrivateSession:
        """Create a new private session with NullMemory."""
        agent = Agent(
            client=client,
            tools=tools or [],
            system_prompt=(
                "You are in a private session. Nothing said here will be remembered. "
                "Say 'end private session' when you're done."
            ),
        )
        return cls(
            user_id=user_id,
            dm_channel_id=dm_channel_id,
            origin_group_id=origin_group_id,
            agent=agent,
        )

    def is_expired(self) -> bool:
        """Check if the session has exceeded the idle timeout."""
        elapsed = (datetime.now(timezone.utc) - self.last_activity).total_seconds()
        return elapsed > self.IDLE_TIMEOUT

    def touch(self) -> None:
        """Update last activity timestamp."""
        self.last_activity = datetime.now(timezone.utc)

    async def handle(self, text: str, sender_id: str = "") -> str:
        """Process a message in the private session."""
        self.touch()
        return await self.agent.run(text, sender_id=sender_id)

    def should_end(self, text: str) -> bool:
        """Check if the user wants to end the session."""
        return "end private session" in text.lower()
