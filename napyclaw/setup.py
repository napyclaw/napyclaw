"""Interactive setup wizard — writes napyclaw.toml and prints Infisical instructions."""
from __future__ import annotations

import sys
import tomllib
from pathlib import Path

_TOML_PATH = Path("napyclaw.toml")

_PROVIDERS = ["openai", "ollama", "foundry", "bedrock"]

_BEDROCK_REGIONS = [
    "us-east-1", "us-west-2", "eu-west-1", "ap-southeast-1", "ap-northeast-1"
]


def _ask(prompt: str, default: str = "") -> str:
    display = f"{prompt} [{default}]: " if default else f"{prompt}: "
    try:
        answer = input(display).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(0)
    return answer if answer else default


def _choose(prompt: str, options: list[str], default: str = "") -> str:
    opts = "/".join(f"[{o}]" if o == default else o for o in options)
    while True:
        answer = _ask(f"{prompt} ({opts})", default).lower()
        if answer in options:
            return answer
        print(f"  Please choose one of: {', '.join(options)}")


def _section(title: str) -> None:
    print(f"\n{'─' * 50}")
    print(f"  {title}")
    print(f"{'─' * 50}")


def run() -> None:
    print("\nnapyclaw setup wizard")
    print("This will write napyclaw.toml and tell you which secrets to add to Infisical.\n")

    existing: dict = {}
    if _TOML_PATH.exists():
        with open(_TOML_PATH, "rb") as f:
            existing = tomllib.load(f)
        print(f"Found existing {_TOML_PATH} — values shown as defaults.")
        answer = _ask("Overwrite existing config? (yes to proceed)", "no")
        if answer.lower() not in ("yes", "y"):
            print("Aborted — existing config unchanged.")
            sys.exit(0)

    llm = existing.get("llm", {})
    db = existing.get("db", {})
    app = existing.get("app", {})

    # --- LLM provider ---
    _section("LLM Provider")
    print("Which provider do you want as the default?")
    print("  openai   — OpenAI API or OpenRouter")
    print("  ollama   — Local Ollama (over Tailscale or localhost)")
    print("  foundry  — Azure AI Foundry")
    print("  bedrock  — AWS Bedrock")
    provider = _choose("Default provider", _PROVIDERS, llm.get("default_provider", "openai"))

    model = _ask("Default model name (deployment/model name for this provider)",
                 llm.get("default_model", _default_model(provider)))

    # --- Provider-specific URLs ---
    foundry_base_url: str | None = None
    aws_region: str | None = None
    openai_base_url = llm.get("openai_base_url", "https://api.openai.com/v1")
    ollama_base_url = llm.get("ollama_base_url", "http://localhost:11434/v1")

    if provider == "openai":
        _section("OpenAI / OpenRouter")
        openai_base_url = _ask(
            "API base URL (use https://openrouter.ai/api/v1 for OpenRouter)",
            openai_base_url,
        )
    elif provider == "ollama":
        _section("Ollama")
        ollama_base_url = _ask(
            "Ollama base URL (e.g. http://100.x.x.x:11434/v1 over Tailscale)",
            ollama_base_url,
        )
        print("  Tip: set num_ctx in your Modelfile — Ollama defaults to 2048.")
    elif provider == "foundry":
        _section("Azure AI Foundry")
        foundry_base_url = _ask(
            "Foundry endpoint URL (e.g. https://your-name.openai.azure.com/)",
            llm.get("foundry_base_url", ""),
        ) or None
    elif provider == "bedrock":
        _section("AWS Bedrock")
        aws_region = _choose("AWS region", _BEDROCK_REGIONS,
                             llm.get("aws_region", "us-east-1"))

    # --- Vector memory ---
    _section("Vector Memory")
    vector_embed_model = _ask(
        "Ollama embedding model (used for semantic memory — requires Ollama)",
        llm.get("vector_embed_model", "nomic-embed-text"),
    )

    # --- Database ---
    _section("Database")
    print("napyclaw uses PostgreSQL + pgvector. The default docker-compose.yml")
    print("starts one on localhost:5432 with these credentials.")
    db_url = _ask(
        "DB URL",
        db.get("url", "postgresql://napyclaw:napyclaw-local@localhost:5432/napyclaw"),
    )

    # --- App settings ---
    _section("App Settings")
    workspace_dir = _ask(
        "Workspace directory (sandboxed dir for file tools)",
        app.get("workspace_dir", "/app/workspace"),
    )
    groups_dir = _ask(
        "Groups directory (per-group Markdown memory fallback)",
        app.get("groups_dir", "/app/groups"),
    )
    oauth_port = _ask(
        "OAuth callback port",
        str(app.get("oauth_callback_port", 8765)),
    )
    max_history_raw = _ask(
        "Max history tokens (leave blank for auto)",
        str(app["max_history_tokens"]) if app.get("max_history_tokens") else "",
    )

    # --- Write toml ---
    _section("Writing napyclaw.toml")
    lines = [
        "[llm]",
        f'default_provider = "{provider}"',
        f'default_model = "{model}"',
        f'openai_base_url = "{openai_base_url}"',
        f'ollama_base_url = "{ollama_base_url}"',
    ]
    if foundry_base_url:
        lines.append(f'foundry_base_url = "{foundry_base_url}"')
    if aws_region:
        lines.append(f'aws_region = "{aws_region}"')
    lines += [
        f'vector_embed_model = "{vector_embed_model}"',
        "",
        "[db]",
        f'url = "{db_url}"',
        "",
        "[app]",
        f'oauth_callback_port = {oauth_port}',
        f'workspace_dir = "{workspace_dir}"',
        f'groups_dir = "{groups_dir}"',
    ]
    if max_history_raw:
        lines.append(f"max_history_tokens = {max_history_raw}")

    _TOML_PATH.write_text("\n".join(lines) + "\n")
    print(f"  Written to {_TOML_PATH.resolve()}")

    # --- Infisical instructions ---
    _section("Secrets to add to Infisical")
    print("Add these to your Infisical project (environment: prod).\n")

    required_secrets = [
        ("SLACK_BOT_TOKEN", "xoxb-...", "From api.slack.com/apps → OAuth & Permissions"),
        ("SLACK_APP_TOKEN", "xapp-...", "From api.slack.com/apps → Socket Mode"),
        ("SLACK_OWNER_CHANNEL", "C0123ABCD", "Slack channel ID where egress approvals are sent — find it in the channel URL"),
    ]

    if provider == "openai" or provider != "openai":
        required_secrets.append(
            ("OPENAI_API_KEY", "sk-..." if provider == "openai" else "placeholder",
             "From platform.openai.com" if provider == "openai" else "Placeholder — not using OpenAI")
        )
    if provider == "ollama":
        required_secrets.append(
            ("OLLAMA_API_KEY", "ollama", "Any non-empty string")
        )
    else:
        required_secrets.append(
            ("OLLAMA_API_KEY", "placeholder", "Placeholder — not using Ollama")
        )

    if provider == "foundry":
        required_secrets.append(
            ("FOUNDRY_API_KEY", "...", "From Azure AI Foundry → Keys and Endpoints")
        )
    if provider == "bedrock":
        required_secrets += [
            ("AWS_ACCESS_KEY_ID",     "AKIA...", "IAM user access key (or use instance role)"),
            ("AWS_SECRET_ACCESS_KEY", "...",     "IAM user secret key"),
        ]

    optional_search_secrets = [
        ("EXA_API_KEY",    "...",     "Optional. Neural search — better for obscure/recent topics than SearXNG alone."),
        ("TAVILY_API_KEY", "tvly-...", "Optional. AI-native search — clean summaries, good for factual lookups."),
    ]

    col1 = max(len(s[0]) for s in required_secrets + optional_search_secrets) + 2
    col2 = max(len(s[1]) for s in required_secrets + optional_search_secrets) + 2
    print(f"  {'Secret':<{col1}} {'Example':<{col2}} Notes")
    print(f"  {'─'*col1} {'─'*col2} {'─'*40}")
    for name, example, note in required_secrets:
        print(f"  {name:<{col1}} {example:<{col2}} {note}")

    print()
    print("  Search is handled locally by SearXNG (already in docker-compose.yml — no key needed).")
    print("  The following are optional cloud backups that improve results for obscure or recent")
    print("  queries. Leave them out to keep the stack fully self-contained (atomic mode).\n")
    print(f"  {'Secret':<{col1}} {'Example':<{col2}} Notes")
    print(f"  {'─'*col1} {'─'*col2} {'─'*40}")
    for name, example, note in optional_search_secrets:
        print(f"  {name:<{col1}} {example:<{col2}} {note}")

    print("\nThen set these three environment variables on the machine running napyclaw:")
    print("  export INFISICAL_CLIENT_ID=your-machine-identity-client-id")
    print("  export INFISICAL_CLIENT_SECRET=your-machine-identity-client-secret")
    print("  export INFISICAL_PROJECT_ID=your-project-id")

    print("\nNext steps:")
    print("  1. Add the secrets above to Infisical")
    print("  2. Create your Slack app (see README Step 3)")
    print("  3. Run: docker compose up -d")
    if provider == "ollama":
        print(f"  4. Run: ollama pull {model}")
        print(f"  5. Run: ollama pull {vector_embed_model}")
        print("  6. Run: python -m napyclaw")
    else:
        print(f"  4. Run: ollama pull {vector_embed_model}  (for vector memory)")
        print("  5. Run: python -m napyclaw")

    print()
    print("  Atomic mode (fully self-contained, no cloud dependencies):")
    print("  • Use Ollama for LLM inference (set default_provider = 'ollama')")
    print("  • SearXNG is already included — no Exa or Tavily key needed")
    print("  • Run Infisical from this stack: docker compose up infisical")
    print("  • Replace Slack with a self-hosted comms platform (see issue #7)")
    print()
    print("  All components except comms can run fully on-prem today.")
    print()


def _default_model(provider: str) -> str:
    return {
        "openai": "gpt-4o",
        "ollama": "llama3.3:latest",
        "foundry": "gpt-4o",
        "bedrock": "anthropic.claude-3-5-sonnet-20241022-v2:0",
    }.get(provider, "gpt-4o")
