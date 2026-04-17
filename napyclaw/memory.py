from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import asyncpg
    import httpx


class MemoryBackend(ABC):
    @abstractmethod
    async def search(self, query: str, group_id: str, top_k: int = 5) -> list[str]:
        """Return relevant memory strings for the agent system prompt."""
        ...

    @abstractmethod
    async def capture(self, content: str, group_id: str | None = None) -> None:
        """Store a memory. group_id=None → global memory."""
        ...

    @abstractmethod
    async def load_context(self) -> str:
        """Return a static context string (summary facts, not search results)."""
        ...


class NullMemory(MemoryBackend):
    """No-op backend for private sessions. Discards everything."""

    async def search(self, query: str, group_id: str, top_k: int = 5) -> list[str]:
        return []

    async def capture(self, content: str, group_id: str | None = None) -> None:
        pass

    async def load_context(self) -> str:
        return ""


class MarkdownMemory(MemoryBackend):
    """Fallback backend — reads/writes {groups_dir}/{group_id}/MEMORY.md."""

    def __init__(self, groups_dir: Path, group_id: str) -> None:
        self._path = groups_dir / group_id / "MEMORY.md"

    @property
    def path(self) -> Path:
        return self._path

    async def search(self, query: str, group_id: str, top_k: int = 5) -> list[str]:
        """Returns full file contents — no semantic filtering."""
        content = await self.load_context()
        if content:
            return [content]
        return []

    async def capture(self, content: str, group_id: str | None = None) -> None:
        """Append a line to the MEMORY.md file."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(content + "\n")

    async def load_context(self) -> str:
        if self._path.exists():
            return self._path.read_text(encoding="utf-8")
        return ""


class VectorMemory(MemoryBackend):
    """PostgreSQL + pgvector backend with Ollama embeddings.

    Accepts an existing asyncpg.Pool (from Database) — does not manage
    its own connection lifecycle.
    """

    def __init__(
        self,
        pool: asyncpg.Pool | None,
        embed_model: str,
        ollama_base_url: str,
    ) -> None:
        self._pool = pool
        self._embed_model = embed_model
        # Strip /v1 for native Ollama API
        base = ollama_base_url.rstrip("/")
        if base.endswith("/v1"):
            base = base[:-3]
        self._ollama_base = base

    async def _embed(self, text: str) -> list[float]:
        """Generate embedding via Ollama /api/embeddings endpoint."""
        import httpx

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self._ollama_base}/api/embeddings",
                json={"model": self._embed_model, "prompt": text},
                timeout=30.0,
            )
            resp.raise_for_status()
            return resp.json()["embedding"]

    async def search(self, query: str, group_id: str, top_k: int = 5) -> list[str]:
        if not self._pool:
            return []

        try:
            embedding = await self._embed(query)
        except Exception:
            return []

        embedding_str = "[" + ",".join(str(x) for x in embedding) + "]"

        rows = await self._pool.fetch(
            "SELECT content, similarity FROM match_thoughts($1::vector, $2, $3)",
            embedding_str,
            group_id,
            top_k,
        )
        return [row["content"] for row in rows]

    async def capture(
        self, content: str, group_id: str | None = None, user_id: str = "system"
    ) -> None:
        if not self._pool:
            return

        try:
            embedding = await self._embed(content)
        except Exception:
            return

        embedding_str = "[" + ",".join(str(x) for x in embedding) + "]"

        await self._pool.execute(
            """
            INSERT INTO thoughts (content, embedding, group_id, user_id)
            VALUES ($1, $2::vector, $3, $4)
            """,
            content,
            embedding_str,
            group_id,
            user_id,
        )

    async def load_context(self) -> str:
        return ""
