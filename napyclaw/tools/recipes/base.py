from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING

from napyclaw.tools.base import Tool

if TYPE_CHECKING:
    from napyclaw.config import Config


class RecipeTool(Tool):
    """Base class for recipe import tools.

    Phase 1 scaffold — concrete implementations added in Phase 2.
    Each recipe wraps an import flow as an agent-callable action.
    """

    def __init__(self, config: Config) -> None:
        self._config = config

    async def get_credential(self, provider: str, sender_id: str) -> str | None:
        """Look up a credential for a provider/user pair.

        Phase 2: Read from Config (loaded from Infisical at startup).
        Returns None if not found.
        """
        # Convention: key pattern is {PROVIDER}_{USER_ID}_REFRESH_TOKEN
        # or {PROVIDER}_{USER_ID}_KEY for static credentials
        return None

    async def _require_credential(self, provider: str, sender_id: str) -> str | None:
        """Get credential or return a helpful error message."""
        cred = await self.get_credential(provider, sender_id)
        if cred is None:
            return None
        return cred

    def _missing_credential_message(self, provider: str) -> str:
        return (
            f"I don't have your {provider} credentials yet. "
            f"Say 'connect {provider}' to set it up."
        )
