from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from napyclaw.memory import MarkdownMemory, NullMemory, VectorMemory


# ---------------------------------------------------------------------------
# NullMemory
# ---------------------------------------------------------------------------


class TestNullMemory:
    async def test_search_returns_empty(self):
        mem = NullMemory()
        assert await mem.search("anything", "G1") == []

    async def test_capture_does_nothing(self):
        mem = NullMemory()
        await mem.capture("some content", "G1")  # Should not raise

    async def test_load_context_returns_empty(self):
        mem = NullMemory()
        assert await mem.load_context() == ""


# ---------------------------------------------------------------------------
# MarkdownMemory
# ---------------------------------------------------------------------------


class TestMarkdownMemory:
    async def test_capture_and_search(self, tmp_path: Path):
        mem = MarkdownMemory(groups_dir=tmp_path, group_id="C001")
        await mem.capture("fact one")
        await mem.capture("fact two")

        results = await mem.search("anything", "C001")
        assert len(results) == 1  # Full file contents as one string
        assert "fact one" in results[0]
        assert "fact two" in results[0]

    async def test_load_context(self, tmp_path: Path):
        mem = MarkdownMemory(groups_dir=tmp_path, group_id="C001")
        await mem.capture("hello world")
        ctx = await mem.load_context()
        assert "hello world" in ctx

    async def test_load_context_empty(self, tmp_path: Path):
        mem = MarkdownMemory(groups_dir=tmp_path, group_id="C001")
        assert await mem.load_context() == ""

    async def test_search_empty(self, tmp_path: Path):
        mem = MarkdownMemory(groups_dir=tmp_path, group_id="C001")
        assert await mem.search("anything", "C001") == []

    async def test_creates_directory(self, tmp_path: Path):
        mem = MarkdownMemory(groups_dir=tmp_path, group_id="new-group")
        await mem.capture("first memory")
        assert mem.path.exists()
        assert mem.path.parent.name == "new-group"

    async def test_path_property(self, tmp_path: Path):
        mem = MarkdownMemory(groups_dir=tmp_path, group_id="C001")
        assert mem.path == tmp_path / "C001" / "MEMORY.md"


# ---------------------------------------------------------------------------
# VectorMemory (mocked — no real Postgres in unit tests)
# ---------------------------------------------------------------------------


class TestVectorMemory:
    def test_strips_v1_from_ollama_url(self):
        mem = VectorMemory(
            pool=None,
            embed_model="nomic-embed-text",
            ollama_base_url="http://100.1.2.3:11434/v1",
        )
        assert mem._ollama_base == "http://100.1.2.3:11434"

    async def test_search_without_connect_returns_empty(self):
        mem = VectorMemory(
            pool=None,
            embed_model="nomic-embed-text",
            ollama_base_url="http://100.1.2.3:11434/v1",
        )
        assert await mem.search("query", "G1") == []

    async def test_capture_without_connect_does_nothing(self):
        mem = VectorMemory(
            pool=None,
            embed_model="nomic-embed-text",
            ollama_base_url="http://100.1.2.3:11434/v1",
        )
        await mem.capture("content", "G1")  # Should not raise

    async def test_load_context_returns_empty(self):
        mem = VectorMemory(
            pool=None,
            embed_model="nomic-embed-text",
            ollama_base_url="http://100.1.2.3:11434/v1",
        )
        assert await mem.load_context() == ""

    async def test_embed_calls_ollama(self):
        import httpx as httpx_mod

        mem = VectorMemory(
            pool=None,
            embed_model="nomic-embed-text",
            ollama_base_url="http://100.1.2.3:11434/v1",
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"embedding": [0.1, 0.2, 0.3]}

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_resp)

        with patch.object(httpx_mod, "AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await mem._embed("test text")

        assert result == [0.1, 0.2, 0.3]
        mock_http.post.assert_called_once()
        call_kwargs = mock_http.post.call_args
        assert "nomic-embed-text" in str(call_kwargs)

    async def test_embed_delegates_to_private_embed(self):
        """Public embed() method delegates to private _embed()."""
        mem = VectorMemory(
            pool=None,
            embed_model="nomic-embed-text",
            ollama_base_url="http://100.1.2.3:11434",
        )
        mem._embed = AsyncMock(return_value=[0.5, 0.6, 0.7])
        result = await mem.embed("hello")
        mem._embed.assert_called_once_with("hello")
        assert result == [0.5, 0.6, 0.7]

    async def test_search_thoughts_returns_content_list(self):
        """search_thoughts calls pool.fetch with correct SQL and returns content list."""
        mock_pool = MagicMock()
        mock_pool.fetch = AsyncMock(return_value=[
            {"content": "thought one", "similarity": 0.9},
            {"content": "thought two", "similarity": 0.8},
        ])
        mem = VectorMemory(
            pool=mock_pool,
            embed_model="nomic-embed-text",
            ollama_base_url="http://100.1.2.3:11434",
        )
        result = await mem.search_thoughts([0.1, 0.2, 0.3], "grp-1", top_k=5)
        assert result == ["thought one", "thought two"]
        mock_pool.fetch.assert_called_once()
        call_args = mock_pool.fetch.call_args[0]
        assert "match_thoughts" in call_args[0]
        # call_args: (sql, embedding_str, group_id, top_k)
        assert call_args[2] == "grp-1"
        assert call_args[3] == 5

    async def test_search_thoughts_empty_embedding_returns_empty(self):
        """search_thoughts returns [] without hitting the pool when embedding is empty."""
        mock_pool = MagicMock()
        mock_pool.fetch = AsyncMock()
        mem = VectorMemory(
            pool=mock_pool,
            embed_model="nomic-embed-text",
            ollama_base_url="http://100.1.2.3:11434",
        )
        result = await mem.search_thoughts([], "grp-1")
        assert result == []
        mock_pool.fetch.assert_not_called()
