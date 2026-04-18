from __future__ import annotations

from typing import TYPE_CHECKING

import httpx

from napyclaw.tools.base import Tool

if TYPE_CHECKING:
    from napyclaw.config import Config
    from napyclaw.db import Database


class RenameBot(Tool):
    name = "rename_bot"
    description = "Change the bot's display name in this channel. Owner only."
    parameters = {
        "type": "object",
        "properties": {
            "new_name": {"type": "string", "description": "The new display name"},
        },
        "required": ["new_name"],
    }

    def __init__(self, db: Database, group_id: str, owner_id: str) -> None:
        self._db = db
        self._group_id = group_id
        self._owner_id = owner_id

    async def execute(self, *, sender_id: str = "", **kwargs) -> str:
        if sender_id != self._owner_id:
            return "Only the channel owner can rename me."

        new_name = kwargs.get("new_name", "").strip()
        if not new_name:
            return "Error: new_name is required."

        # Capitalize first letter
        new_name = new_name[0].upper() + new_name[1:]

        try:
            ctx = await self._db.load_group_context(self._group_id)
            if ctx is None:
                return "Error: group context not found."

            await self._db.save_group_context(
                group_id=self._group_id,
                default_name=ctx["default_name"],
                display_name=new_name,
                nicknames=ctx["nicknames"],
                owner_id=ctx["owner_id"],
                provider=ctx["provider"],
                model=ctx["model"],
                is_first_interaction=ctx["is_first_interaction"],
                history=ctx["history"],
            )
            return f"Renamed to {new_name}"
        except Exception as exc:
            return f"Error: could not rename — {exc}"


class AddNickname(Tool):
    name = "add_nickname"
    description = "Add a nickname for the bot in this channel. Any user can add one."
    parameters = {
        "type": "object",
        "properties": {
            "nickname": {"type": "string", "description": "The nickname to add"},
        },
        "required": ["nickname"],
    }

    def __init__(self, db: Database, group_id: str) -> None:
        self._db = db
        self._group_id = group_id

    async def execute(self, **kwargs) -> str:
        nickname = kwargs.get("nickname", "").strip()
        if not nickname:
            return "Error: nickname is required."

        try:
            ctx = await self._db.load_group_context(self._group_id)
            if ctx is None:
                return "Error: group context not found."

            nicknames = ctx["nicknames"]
            if nickname not in nicknames:
                nicknames.append(nickname)

            await self._db.save_group_context(
                group_id=self._group_id,
                default_name=ctx["default_name"],
                display_name=ctx["display_name"],
                nicknames=nicknames,
                owner_id=ctx["owner_id"],
                provider=ctx["provider"],
                model=ctx["model"],
                is_first_interaction=ctx["is_first_interaction"],
                history=ctx["history"],
            )
            return f"Nickname '{nickname}' added"
        except Exception as exc:
            return f"Error: could not add nickname — {exc}"


class ClearNicknames(Tool):
    name = "clear_nicknames"
    description = "Remove all nicknames for the bot in this channel. Owner only."
    parameters = {
        "type": "object",
        "properties": {},
    }

    def __init__(self, db: Database, group_id: str, owner_id: str) -> None:
        self._db = db
        self._group_id = group_id
        self._owner_id = owner_id

    async def execute(self, *, sender_id: str = "", **kwargs) -> str:
        if sender_id != self._owner_id:
            return "Only the channel owner can clear nicknames."

        try:
            ctx = await self._db.load_group_context(self._group_id)
            if ctx is None:
                return "Error: group context not found."

            await self._db.save_group_context(
                group_id=self._group_id,
                default_name=ctx["default_name"],
                display_name=ctx["display_name"],
                nicknames=[],
                owner_id=ctx["owner_id"],
                provider=ctx["provider"],
                model=ctx["model"],
                is_first_interaction=ctx["is_first_interaction"],
                history=ctx["history"],
            )
            return "All nicknames cleared"
        except Exception as exc:
            return f"Error: could not clear nicknames — {exc}"


RenameBotTool = RenameBot


class SwitchModel(Tool):
    name = "switch_model"
    description = "Switch the LLM provider and model for this channel. Owner only."
    parameters = {
        "type": "object",
        "properties": {
            "provider": {
                "type": "string",
                "enum": ["openai", "ollama", "foundry", "bedrock"],
                "description": "LLM provider",
            },
            "model": {"type": "string", "description": "Model name"},
        },
        "required": ["provider", "model"],
    }

    def __init__(self, db: Database, group_id: str, owner_id: str) -> None:
        self._db = db
        self._group_id = group_id
        self._owner_id = owner_id

    async def execute(self, *, sender_id: str = "", **kwargs) -> str:
        if sender_id != self._owner_id:
            return "Only the channel owner can switch models."

        provider = kwargs.get("provider", "")
        model = kwargs.get("model", "")
        if not provider or not model:
            return "Error: provider and model are required."

        try:
            ctx = await self._db.load_group_context(self._group_id)
            if ctx is None:
                return "Error: group context not found."

            await self._db.save_group_context(
                group_id=self._group_id,
                default_name=ctx["default_name"],
                display_name=ctx["display_name"],
                nicknames=ctx["nicknames"],
                owner_id=ctx["owner_id"],
                provider=provider,
                model=model,
                is_first_interaction=ctx["is_first_interaction"],
                history=ctx["history"],
            )
            return f"Switched to {provider}/{model}"
        except Exception as exc:
            return f"Error: could not switch model — {exc}"


class ListModelsTool(Tool):
    name = "list_models"
    description = (
        "List available models for a given provider. "
        "Supports: openai (fetches /models), foundry (fetches /openai/deployments), "
        "ollama (fetches /api/tags), bedrock (returns known model IDs)."
    )
    parameters = {
        "type": "object",
        "properties": {
            "provider": {
                "type": "string",
                "enum": ["openai", "foundry", "ollama", "bedrock"],
                "description": "Which provider to query",
            },
        },
        "required": ["provider"],
    }

    _BEDROCK_MODELS = [
        "anthropic.claude-3-5-sonnet-20241022-v2:0",
        "anthropic.claude-3-5-haiku-20241022-v1:0",
        "anthropic.claude-3-opus-20240229-v1:0",
        "amazon.nova-pro-v1:0",
        "amazon.nova-lite-v1:0",
        "amazon.nova-micro-v1:0",
        "meta.llama3-70b-instruct-v1:0",
        "meta.llama3-8b-instruct-v1:0",
    ]

    def __init__(self, config: Config, http_client=None) -> None:
        self._config = config
        self._http = http_client

    async def execute(self, **kwargs) -> str:
        provider = kwargs.get("provider", "")

        try:
            if provider == "openai":
                return await self._list_openai(
                    self._config.openai_base_url, self._config.openai_api_key
                )
            if provider == "foundry":
                if not self._config.foundry_base_url or not self._config.foundry_api_key:
                    return "Foundry is not configured (missing FOUNDRY_BASE_URL or FOUNDRY_API_KEY)."
                return await self._list_foundry(
                    self._config.foundry_base_url, self._config.foundry_api_key
                )
            if provider == "ollama":
                return await self._list_ollama(self._config.ollama_base_url)
            if provider == "bedrock":
                return "Available Bedrock model IDs:\n" + "\n".join(
                    f"- {m}" for m in self._BEDROCK_MODELS
                )
            return f"Unknown provider: {provider}"
        except Exception as exc:
            return f"Error listing models for {provider}: {exc}"

    async def _list_openai(self, base_url: str, api_key: str) -> str:
        url = base_url.rstrip("/") + "/models"
        async with httpx.AsyncClient(http2=False) as client:
            resp = await client.get(
                url, headers={"Authorization": f"Bearer {api_key}"}, timeout=10.0
            )
            resp.raise_for_status()
            data = resp.json()
        models = sorted(m["id"] for m in data.get("data", []))
        return "Available OpenAI models:\n" + "\n".join(f"- {m}" for m in models)

    async def _list_foundry(self, base_url: str, api_key: str) -> str:
        # Azure AI Foundry: GET /openai/deployments?api-version=2024-02-01
        url = base_url.rstrip("/") + "/openai/deployments?api-version=2024-02-01"
        async with httpx.AsyncClient(http2=False) as client:
            resp = await client.get(
                url, headers={"api-key": api_key}, timeout=10.0
            )
            resp.raise_for_status()
            data = resp.json()
        deployments = sorted(d["id"] for d in data.get("value", []))
        if not deployments:
            return "No deployments found in this Foundry project."
        return "Available Foundry deployments:\n" + "\n".join(
            f"- {d}" for d in deployments
        )

    async def _list_ollama(self, base_url: str) -> str:
        api_base = base_url.rstrip("/")
        if api_base.endswith("/v1"):
            api_base = api_base[:-3]
        async with httpx.AsyncClient(http2=False) as client:
            resp = await client.get(f"{api_base}/api/tags", timeout=10.0)
            resp.raise_for_status()
            data = resp.json()
        models = sorted(m["name"] for m in data.get("models", []))
        if not models:
            return "No models found in Ollama."
        return "Available Ollama models:\n" + "\n".join(f"- {m}" for m in models)
