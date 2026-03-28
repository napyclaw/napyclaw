import json
from collections.abc import AsyncIterator

from openai import AsyncOpenAI

from napyclaw.models.base import ChatResponse, LLMClient, ToolCall


class LLMUnavailableError(Exception):
    pass


# Hardcoded context windows — unknown models default to 8192 (conservative)
_CONTEXT_WINDOWS: dict[str, int] = {
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "gpt-4-turbo": 128_000,
    "gpt-4": 8_192,
    "gpt-3.5-turbo": 16_385,
    "o1": 200_000,
    "o1-mini": 128_000,
    "o1-preview": 128_000,
    "o3": 200_000,
    "o3-mini": 200_000,
    "o4-mini": 200_000,
}

_DEFAULT_CONTEXT_WINDOW = 8_192


class OpenAIClient(LLMClient):
    provider = "openai"

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        http_client=None,
    ) -> None:
        self.model = model
        self.context_window = _CONTEXT_WINDOWS.get(model, _DEFAULT_CONTEXT_WINDOW)

        kwargs = {"api_key": api_key, "base_url": base_url}
        if http_client is not None:
            kwargs["http_client"] = http_client
        self._client = AsyncOpenAI(**kwargs)

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
                f"OpenAI API error: {exc}"
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
                f"OpenAI API error: {exc}"
            ) from exc

        async for chunk in response:
            content = chunk.choices[0].delta.content
            if content:
                yield content
