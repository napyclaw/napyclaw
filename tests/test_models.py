import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from napyclaw.models.base import ChatResponse, LLMClient, ToolCall
from napyclaw.models.openai_client import OpenAIClient, LLMUnavailableError
from napyclaw.models.ollama_client import OllamaClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_openai_response(
    text: str | None = "Hello!",
    tool_calls: list | None = None,
    finish_reason: str = "stop",
):
    """Build a mock OpenAI ChatCompletion response."""
    choice = MagicMock()
    choice.finish_reason = finish_reason
    message = MagicMock()
    message.content = text

    if tool_calls:
        mock_tcs = []
        for tc in tool_calls:
            mock_tc = MagicMock()
            mock_tc.id = tc["id"]
            mock_tc.function.name = tc["name"]
            mock_tc.function.arguments = json.dumps(tc["arguments"])
            mock_tcs.append(mock_tc)
        message.tool_calls = mock_tcs
    else:
        message.tool_calls = None

    choice.message = message

    response = MagicMock()
    response.choices = [choice]
    return response


# ---------------------------------------------------------------------------
# OpenAIClient tests
# ---------------------------------------------------------------------------


class TestOpenAIClient:
    def test_provider_is_openai(self):
        with patch("napyclaw.models.openai_client.AsyncOpenAI"):
            client = OpenAIClient(
                api_key="sk-test",
                base_url="https://api.openai.com/v1",
                model="gpt-4o",
            )
        assert client.provider == "openai"

    def test_context_window_known_model(self):
        with patch("napyclaw.models.openai_client.AsyncOpenAI"):
            client = OpenAIClient(
                api_key="sk-test",
                base_url="https://api.openai.com/v1",
                model="gpt-4o",
            )
        assert client.context_window == 128_000

    def test_context_window_unknown_model_defaults(self):
        with patch("napyclaw.models.openai_client.AsyncOpenAI"):
            client = OpenAIClient(
                api_key="sk-test",
                base_url="https://api.openai.com/v1",
                model="some-future-model",
            )
        assert client.context_window == 8192

    async def test_chat_text_response(self):
        mock_openai = MagicMock()
        mock_openai.chat.completions.create = AsyncMock(
            return_value=_make_openai_response(text="Hello!", finish_reason="stop")
        )

        with patch("napyclaw.models.openai_client.AsyncOpenAI", return_value=mock_openai):
            client = OpenAIClient(
                api_key="sk-test",
                base_url="https://api.openai.com/v1",
                model="gpt-4o",
            )

        resp = await client.chat([{"role": "user", "content": "hi"}])
        assert isinstance(resp, ChatResponse)
        assert resp.text == "Hello!"
        assert resp.tool_calls is None
        assert resp.finish_reason == "stop"

    async def test_chat_tool_calls(self):
        tool_calls = [{"id": "call_1", "name": "web_search", "arguments": {"query": "test"}}]
        mock_openai = MagicMock()
        mock_openai.chat.completions.create = AsyncMock(
            return_value=_make_openai_response(
                text=None,
                tool_calls=tool_calls,
                finish_reason="tool_calls",
            )
        )

        with patch("napyclaw.models.openai_client.AsyncOpenAI", return_value=mock_openai):
            client = OpenAIClient(
                api_key="sk-test",
                base_url="https://api.openai.com/v1",
                model="gpt-4o",
            )

        resp = await client.chat(
            [{"role": "user", "content": "search for cats"}],
            tools=[{"type": "function", "function": {"name": "web_search"}}],
        )
        assert resp.text is None
        assert len(resp.tool_calls) == 1
        assert resp.tool_calls[0].name == "web_search"
        assert resp.tool_calls[0].arguments == {"query": "test"}
        assert resp.finish_reason == "tool_calls"

    async def test_chat_api_error_raises_unavailable(self):
        mock_openai = MagicMock()
        mock_openai.chat.completions.create = AsyncMock(
            side_effect=Exception("API rate limit exceeded")
        )

        with patch("napyclaw.models.openai_client.AsyncOpenAI", return_value=mock_openai):
            client = OpenAIClient(
                api_key="sk-test",
                base_url="https://api.openai.com/v1",
                model="gpt-4o",
            )

        with pytest.raises(LLMUnavailableError, match="OpenAI"):
            await client.chat([{"role": "user", "content": "hi"}])

    async def test_stream_yields_tokens(self):
        async def mock_stream(*args, **kwargs):
            for token in ["Hello", " ", "world"]:
                chunk = MagicMock()
                chunk.choices = [MagicMock()]
                chunk.choices[0].delta.content = token
                yield chunk

        mock_openai = MagicMock()
        mock_openai.chat.completions.create = MagicMock(return_value=mock_stream())

        with patch("napyclaw.models.openai_client.AsyncOpenAI", return_value=mock_openai):
            client = OpenAIClient(
                api_key="sk-test",
                base_url="https://api.openai.com/v1",
                model="gpt-4o",
            )

        tokens = []
        async for token in client.stream([{"role": "user", "content": "hi"}]):
            tokens.append(token)
        assert tokens == ["Hello", " ", "world"]

    async def test_stream_api_error_raises_unavailable(self):
        mock_openai = MagicMock()
        mock_openai.chat.completions.create = MagicMock(
            side_effect=Exception("connection refused")
        )

        with patch("napyclaw.models.openai_client.AsyncOpenAI", return_value=mock_openai):
            client = OpenAIClient(
                api_key="sk-test",
                base_url="https://api.openai.com/v1",
                model="gpt-4o",
            )

        with pytest.raises(LLMUnavailableError):
            async for _ in client.stream([{"role": "user", "content": "hi"}]):
                pass

    def test_accepts_http_client(self):
        mock_http = MagicMock()
        with patch("napyclaw.models.openai_client.AsyncOpenAI") as mock_cls:
            OpenAIClient(
                api_key="sk-test",
                base_url="https://api.openai.com/v1",
                model="gpt-4o",
                http_client=mock_http,
            )
        _, kwargs = mock_cls.call_args
        assert kwargs["http_client"] is mock_http


# ---------------------------------------------------------------------------
# OllamaClient tests
# ---------------------------------------------------------------------------


class TestOllamaClient:
    def test_provider_is_ollama(self):
        with patch("napyclaw.models.ollama_client.AsyncOpenAI"):
            client = OllamaClient(
                base_url="http://100.1.2.3:11434/v1",
                api_key="ollama",
                model="llama3.3:latest",
            )
        assert client.provider == "ollama"

    def test_default_context_window(self):
        with patch("napyclaw.models.ollama_client.AsyncOpenAI"):
            client = OllamaClient(
                base_url="http://100.1.2.3:11434/v1",
                api_key="ollama",
                model="llama3.3:latest",
            )
        assert client.context_window == 2048

    async def test_fetch_context_window_success(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "model_info": {"llama.context_length": 65536}
        }

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_response)

        with patch("napyclaw.models.ollama_client.AsyncOpenAI"):
            client = OllamaClient(
                base_url="http://100.1.2.3:11434/v1",
                api_key="ollama",
                model="llama3.3:latest",
            )

        with patch("napyclaw.models.ollama_client.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            await client.fetch_context_window()

        assert client.context_window == 65536

    async def test_fetch_context_window_failure_keeps_default(self):
        with patch("napyclaw.models.ollama_client.AsyncOpenAI"):
            client = OllamaClient(
                base_url="http://100.1.2.3:11434/v1",
                api_key="ollama",
                model="llama3.3:latest",
            )

        with patch("napyclaw.models.ollama_client.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(
                side_effect=Exception("connection refused")
            )
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            await client.fetch_context_window()

        assert client.context_window == 2048

    async def test_chat_text_response(self):
        mock_openai = MagicMock()
        mock_openai.chat.completions.create = AsyncMock(
            return_value=_make_openai_response(text="Ollama says hi", finish_reason="stop")
        )

        with patch("napyclaw.models.ollama_client.AsyncOpenAI", return_value=mock_openai):
            client = OllamaClient(
                base_url="http://100.1.2.3:11434/v1",
                api_key="ollama",
                model="llama3.3:latest",
            )

        resp = await client.chat([{"role": "user", "content": "hi"}])
        assert resp.text == "Ollama says hi"
        assert resp.finish_reason == "stop"

    async def test_chat_error_raises_unavailable(self):
        mock_openai = MagicMock()
        mock_openai.chat.completions.create = AsyncMock(
            side_effect=Exception("connection refused")
        )

        with patch("napyclaw.models.ollama_client.AsyncOpenAI", return_value=mock_openai):
            client = OllamaClient(
                base_url="http://100.1.2.3:11434/v1",
                api_key="ollama",
                model="llama3.3:latest",
            )

        with pytest.raises(LLMUnavailableError, match="Ollama"):
            await client.chat([{"role": "user", "content": "hi"}])

    async def test_stream_yields_tokens(self):
        async def mock_stream(*args, **kwargs):
            for token in ["Olla", "ma"]:
                chunk = MagicMock()
                chunk.choices = [MagicMock()]
                chunk.choices[0].delta.content = token
                yield chunk

        mock_openai = MagicMock()
        mock_openai.chat.completions.create = MagicMock(return_value=mock_stream())

        with patch("napyclaw.models.ollama_client.AsyncOpenAI", return_value=mock_openai):
            client = OllamaClient(
                base_url="http://100.1.2.3:11434/v1",
                api_key="ollama",
                model="llama3.3:latest",
            )

        tokens = []
        async for token in client.stream([{"role": "user", "content": "hi"}]):
            tokens.append(token)
        assert tokens == ["Olla", "ma"]

    def test_accepts_http_client(self):
        mock_http = MagicMock()
        with patch("napyclaw.models.ollama_client.AsyncOpenAI") as mock_cls:
            OllamaClient(
                base_url="http://100.1.2.3:11434/v1",
                api_key="ollama",
                model="llama3.3:latest",
                http_client=mock_http,
            )
        _, kwargs = mock_cls.call_args
        assert kwargs["http_client"] is mock_http
