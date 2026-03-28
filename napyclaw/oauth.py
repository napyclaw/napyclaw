from __future__ import annotations

from typing import Any


class OAuthCallbackServer:
    """Lightweight HTTP listener for OAuth redirect callbacks.

    Phase 1 scaffold — provider-specific flows added in Phase 2.
    """

    def __init__(self) -> None:
        self._server: Any = None
        self._port: int = 8765

    async def start(self, port: int = 8765) -> None:
        """Start the OAuth callback HTTP server."""
        self._port = port
        # Phase 2: start aiohttp/starlette server on this port
        # For now, this is a no-op scaffold

    async def stop(self) -> None:
        """Stop the server."""
        if self._server:
            self._server = None

    async def get_authorization_url(self, provider: str, user_id: str) -> str:
        """Generate OAuth authorization URL for a provider.

        Phase 2: Implement per-provider OAuth URL generation.
        """
        raise NotImplementedError(
            f"OAuth provider '{provider}' not yet implemented. "
            "Provider implementations are added in Phase 2."
        )

    async def handle_callback(self, code: str, state: str) -> None:
        """Handle OAuth redirect callback.

        Phase 2: Exchange code for tokens, store refresh_token in Infisical.
        """
        raise NotImplementedError("OAuth callback handling added in Phase 2.")
