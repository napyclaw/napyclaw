"""Entry point — builds and starts NapyClaw."""
import asyncio
import signal
import sys

from napyclaw.app import GroupContext, NapyClaw
from napyclaw.channels.slack import SlackChannel
from napyclaw.config import Config, ConfigError
from napyclaw.db import Database
from napyclaw.egress import EgressGuard
from napyclaw.injection_guard import InjectionGuard
from napyclaw.memory import VectorMemory
from napyclaw.models.ollama_client import OllamaClient
from napyclaw.models.openai_client import OpenAIClient
from napyclaw.scheduler import Scheduler
from napyclaw.shield import ContentShield
from napyclaw.tools.file_ops import FileReadTool, FileWriteTool
from napyclaw.tools.identity import AddNickname, ClearNicknames, RenameBotTool, SwitchModel
from napyclaw.tools.messaging import SendMessageTool
from napyclaw.tools.scheduling import ScheduleTaskTool
from napyclaw.tools.web_search import WebSearchTool


async def main() -> None:
    try:
        config = Config.from_infisical()
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    print(f"napyclaw starting with {config.default_provider}/{config.default_model}")

    # --- Core services ---
    db = Database(config.db_url)
    await db.connect()
    print(f"  database connected ({config.db_url.split('@')[-1]})")

    channel = SlackChannel(bot_token=config.slack_bot_token, app_token=config.slack_app_token)
    shield = ContentShield()

    # --- EgressGuard ---
    egress = EgressGuard(db=db)
    # Auto-allow configured LLM endpoints
    egress.add_auto_allow_from_url(config.openai_base_url)
    egress.add_auto_allow_from_url(config.ollama_base_url)
    egress.add_auto_allow("api.search.brave.com")
    guarded_http = egress.build_client()

    # --- Vector memory ---
    memory = VectorMemory(
        pool=db.pool,
        embed_model=config.vector_embed_model,
        ollama_base_url=config.ollama_base_url,
    )
    print(f"  vector memory ready ({config.vector_embed_model})")

    # --- LLM client factory ---
    def build_client(provider: str, model: str):
        if provider == "ollama":
            return OllamaClient(
                base_url=config.ollama_base_url,
                api_key=config.ollama_api_key,
                model=model,
                http_client=guarded_http,
            )
        else:
            return OpenAIClient(
                api_key=config.openai_api_key,
                base_url=config.openai_base_url,
                model=model,
                http_client=guarded_http,
            )

    # --- InjectionGuard ---
    injection_guard = InjectionGuard()

    # --- Tool factory ---
    def build_tools(ctx: GroupContext):
        return [
            WebSearchTool(brave_api_key=config.brave_api_key, http_client=guarded_http),
            FileReadTool(config=config),
            FileWriteTool(config=config),
            SendMessageTool(channel=channel, current_group_id=ctx.group_id),
            ScheduleTaskTool(db=db, group_id=ctx.group_id, owner_id=ctx.owner_id),
            RenameBotTool(db=db, group_id=ctx.group_id, owner_id=ctx.owner_id),
            AddNickname(db=db, group_id=ctx.group_id),
            ClearNicknames(db=db, group_id=ctx.group_id, owner_id=ctx.owner_id),
            SwitchModel(db=db, group_id=ctx.group_id, owner_id=ctx.owner_id),
        ]

    # --- System prompt factory ---
    def build_system_prompt(ctx: GroupContext) -> str:
        parts = [f"Your name is {ctx.display_name}."]

        if ctx.nicknames:
            parts.append(f"Your nicknames are: {', '.join(ctx.nicknames)}.")

        if ctx.is_first_interaction:
            parts.append(
                "This is your first conversation in this channel. "
                "Introduce yourself and ask if the user would like to give you a different name."
            )

        parts.append(
            f"You are running on {ctx.active_client.provider}/{ctx.active_client.model}."
        )

        return " ".join(parts)

    # --- App ---
    app = NapyClaw(
        config=config,
        db=db,
        channel=channel,
        build_tools=build_tools,
        build_client=build_client,
        build_system_prompt=build_system_prompt,
        injection_guard=injection_guard,
        shield=shield,
        memory=memory,
    )

    # Fetch Ollama context window if that's the default provider
    if config.default_provider == "ollama":
        client = build_client("ollama", config.default_model)
        await client.fetch_context_window()
        print(f"  ollama context window: {client.context_window}")

    # --- Start ---
    await app.start()
    app.bot_user_id = channel.bot_user_id
    print(f"  connected to Slack as {channel.bot_user_id}")

    # --- Scheduler ---
    scheduler = Scheduler(
        db=db,
        queue=app.queue,
        channel=channel,
        contexts=app.contexts,
        build_client=build_client,
    )
    await scheduler.start()
    print("  scheduler started")
    print("napyclaw is running. Press Ctrl+C to stop.")

    # --- Keep alive ---
    stop = asyncio.Event()

    def _signal_handler():
        stop.set()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            pass

    try:
        await stop.wait()
    except KeyboardInterrupt:
        pass

    # --- Shutdown ---
    print("\nshutting down...")
    await scheduler.stop()
    await channel.disconnect()
    await db.close()
    print("done.")


if __name__ == "__main__":
    asyncio.run(main())
