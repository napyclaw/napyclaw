import asyncio
import json
from collections.abc import AsyncIterator

from napyclaw.models.base import ChatResponse, LLMClient, ToolCall
from napyclaw.models.openai_client import LLMUnavailableError

_CONTEXT_WINDOWS: dict[str, int] = {
    "anthropic.claude-3-5-sonnet-20241022-v2:0": 200_000,
    "anthropic.claude-3-5-haiku-20241022-v1:0": 200_000,
    "anthropic.claude-3-opus-20240229-v1:0": 200_000,
    "anthropic.claude-3-sonnet-20240229-v1:0": 200_000,
    "anthropic.claude-3-haiku-20240307-v1:0": 200_000,
    "amazon.nova-pro-v1:0": 300_000,
    "amazon.nova-lite-v1:0": 300_000,
    "amazon.nova-micro-v1:0": 128_000,
    "meta.llama3-70b-instruct-v1:0": 128_000,
    "meta.llama3-8b-instruct-v1:0": 128_000,
    "amazon.titan-text-express-v1": 8_192,
}

_DEFAULT_CONTEXT_WINDOW = 8_192


def _to_bedrock_messages(messages: list[dict]) -> tuple[list[dict], list[dict]]:
    """Convert OpenAI-format messages to Bedrock Converse format.

    Returns (system_blocks, bedrock_messages). Tool results (role="tool") are
    merged into a user turn immediately following the assistant tool-call turn,
    as required by the Bedrock Converse API.
    """
    system: list[dict] = []
    bedrock: list[dict] = []

    i = 0
    while i < len(messages):
        msg = messages[i]
        role = msg["role"]

        if role == "system":
            system.append({"text": msg["content"]})
            i += 1
            continue

        if role == "user":
            content: list[dict] = []
            if msg.get("content"):
                content.append({"text": msg["content"]})
            if not content:
                content.append({"text": ""})
            bedrock.append({"role": "user", "content": content})
            i += 1
            continue

        if role == "assistant":
            content = []
            if msg.get("content"):
                content.append({"text": msg["content"]})
            for tc in msg.get("tool_calls") or []:
                content.append({
                    "toolUse": {
                        "toolUseId": tc["id"],
                        "name": tc["function"]["name"],
                        "input": json.loads(tc["function"]["arguments"]),
                    }
                })
            if not content:
                content.append({"text": ""})
            bedrock.append({"role": "assistant", "content": content})
            i += 1

            # Collect following tool-result messages into a user turn
            tool_results: list[dict] = []
            while i < len(messages) and messages[i]["role"] == "tool":
                tm = messages[i]
                tool_results.append({
                    "toolResult": {
                        "toolUseId": tm["tool_call_id"],
                        "content": [{"text": tm["content"]}],
                    }
                })
                i += 1
            if tool_results:
                bedrock.append({"role": "user", "content": tool_results})
            continue

        i += 1

    return system, bedrock


def _to_bedrock_tools(tools: list[dict]) -> dict:
    """Convert OpenAI function tool schemas to Bedrock toolConfig format."""
    return {
        "tools": [
            {
                "toolSpec": {
                    "name": t["function"]["name"],
                    "description": t["function"].get("description", ""),
                    "inputSchema": {
                        "json": t["function"].get(
                            "parameters", {"type": "object", "properties": {}}
                        )
                    },
                }
            }
            for t in tools
        ]
    }


def _parse_response(response: dict) -> ChatResponse:
    message = response["output"]["message"]
    stop_reason = response.get("stopReason", "end_turn")

    text = None
    tool_calls: list[ToolCall] = []

    for block in message.get("content", []):
        if "text" in block:
            text = block["text"]
        elif "toolUse" in block:
            tu = block["toolUse"]
            tool_calls.append(ToolCall(
                id=tu["toolUseId"],
                name=tu["name"],
                arguments=tu["input"],
            ))

    return ChatResponse(
        text=text,
        tool_calls=tool_calls or None,
        finish_reason="tool_calls" if tool_calls else stop_reason,
    )


class BedrockClient(LLMClient):
    provider = "bedrock"

    def __init__(
        self,
        model: str,
        region: str,
        aws_access_key_id: str | None = None,
        aws_secret_access_key: str | None = None,
    ) -> None:
        import boto3  # lazy import — optional dependency
        self.model = model
        self.context_window = _CONTEXT_WINDOWS.get(model, _DEFAULT_CONTEXT_WINDOW)
        creds = {}
        if aws_access_key_id:
            creds = {
                "aws_access_key_id": aws_access_key_id,
                "aws_secret_access_key": aws_secret_access_key,
            }
        self._client = boto3.client("bedrock-runtime", region_name=region, **creds)

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> ChatResponse:
        system, bedrock_messages = _to_bedrock_messages(messages)
        kwargs: dict = {"modelId": self.model, "messages": bedrock_messages}
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["toolConfig"] = _to_bedrock_tools(tools)

        try:
            response = await asyncio.to_thread(self._client.converse, **kwargs)
        except Exception as exc:
            raise LLMUnavailableError(f"Bedrock error: {exc}") from exc

        return _parse_response(response)

    async def stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> AsyncIterator[str]:
        response = await self.chat(messages, tools=tools)
        if response.text:
            yield response.text
