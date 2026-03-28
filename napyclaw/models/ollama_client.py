import json
from collections.abc import AsyncIterator

import httpx
from openai import AsyncOpenAI

from napyclaw.models.base import ChatResponse, LLMClient, ToolCall
from napyclaw.models.openai_client import LLMUnavailableError

_DEFAULT_CONTEXT_WINDOW = 2048


class OllamaClient(LLMClient):
    provider = "ollama"

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        http_client=None,
    ) -> None:
        self.model = model
        self.context_window = _DEFAULT_CONTEXT_WINDOW
        self._base_url = base_url

        kwargs = {"api_key": api_key, "base_url": base_url}
        if http_client is not None:
            kwargs["http_client"] = http_client
        self._client = AsyncOpenAI(**kwargs)

    async def fetch_context_window(self) -> None:
        """Fetch context window from Ollama /api/show endpoint.

        Falls back to 2048 if the endpoint is unreachable or the field is missing.
        The base_url points to /v1 (OpenAI compat); the native API is one level up.
        """
        # Strip /v1 suffix to get the native Ollama API base
        api_base = self._base_url.rstrip("/")
        if api_base.endswith("/v1"):
            api_base = api_base[:-3]

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{api_base}/api/show",
                    json={"model": self.model},
                    timeout=5.0,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    ctx_len = data.get("model_info", {}).get("llama.context_length")
                    if ctx_len is not None:
                        self.context_window = int(ctx_len)
        except Exception:
            pass  # Keep default

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> ChatResponse:
        try:
            kwargs = {"model": self.model, "messages": messages}
            if tools:
                kwargs["tools"] = tools
            response = await self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            raise LLMUnavailableError(
                f"Ollama server error: {exc}"
            ) from exc

        choice = response.choices[0]
        message = choice.message

        tool_calls = None
        if message.tool_calls:
            tool_calls = [
                ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=json.loads(tc.function.arguments),
                )
                for tc in message.tool_calls
            ]

        return ChatResponse(
            text=message.content,
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason,
        )

    async def stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> AsyncIterator[str]:
        try:
            kwargs = {"model": self.model, "messages": messages, "stream": True}
            if tools:
                kwargs["tools"] = tools
            response = self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            raise LLMUnavailableError(
                f"Ollama server error: {exc}"
            ) from exc

        async for chunk in response:
            content = chunk.choices[0].delta.content
            if content:
                yield content
