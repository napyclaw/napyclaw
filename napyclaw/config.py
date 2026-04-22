import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

try:
    from infisical_client import ClientSettings, GetSecretOptions, InfisicalClient
except ImportError:
    from dataclasses import dataclass as _dc

    @_dc
    class ClientSettings:  # type: ignore[no-redef]
        client_id: str
        client_secret: str

    @_dc
    class GetSecretOptions:  # type: ignore[no-redef]
        environment: str
        project_id: str
        secret_name: str

    InfisicalClient = None  # type: ignore[assignment,misc]

_TOML_SEARCH = [
    Path("napyclaw.toml"),
    Path.home() / ".config" / "napyclaw" / "napyclaw.toml",
]


class ConfigError(Exception):
    pass


def _load_toml(path: Path | None = None) -> dict:
    """Load napyclaw.toml. Searches cwd then ~/.config/napyclaw/ if path not given."""
    candidates = [path] if path else _TOML_SEARCH
    for p in candidates:
        if p and p.exists():
            with open(p, "rb") as f:
                return tomllib.load(f)
    return {}


@dataclass
class Config:
    # LLM
    openai_api_key: str
    openai_base_url: str
    ollama_base_url: str
    ollama_api_key: str
    default_model: str
    default_provider: str

    # Azure AI Foundry (optional)
    foundry_api_key: str | None
    foundry_base_url: str | None

    # AWS Bedrock (optional)
    aws_access_key_id: str | None
    aws_secret_access_key: str | None
    aws_region: str | None

    # Slack
    slack_bot_token: str
    slack_app_token: str

    # Web search
    tavily_api_key: str | None
    exa_api_key: str | None
    search_providers: list[str]
    searxng_url: str | None

    # Database
    db_url: str
    vector_embed_model: str

    # OAuth
    oauth_callback_port: int

    # Container URLs
    egress_url: str
    comms_url: str

    # Paths
    workspace_dir: Path
    groups_dir: Path

    # Agent tuning (optional)
    max_history_tokens: int | None

    @classmethod
    def load(cls, toml_path: Path | None = None) -> "Config":
        """Load config from napyclaw.toml + Infisical secrets."""
        toml = _load_toml(toml_path)
        llm = toml.get("llm", {})
        db = toml.get("db", {})
        app = toml.get("app", {})

        secrets = _load_infisical()

        def secret(name: str) -> str:
            val = secrets.get(name)
            if not val:
                raise ConfigError(f"Missing required secret: {name}")
            return val

        def optional_secret(name: str) -> str | None:
            return secrets.get(name)

        return cls(
            # Secrets from Infisical
            openai_api_key=secret("OPENAI_API_KEY"),
            ollama_api_key=secret("OLLAMA_API_KEY"),
            slack_bot_token=secret("SLACK_BOT_TOKEN"),
            slack_app_token=secret("SLACK_APP_TOKEN"),
            tavily_api_key=optional_secret("TAVILY_API_KEY"),
            exa_api_key=optional_secret("EXA_API_KEY"),
            db_url=db.get("url") or secret("DB_URL"),
            foundry_api_key=optional_secret("FOUNDRY_API_KEY"),
            aws_access_key_id=optional_secret("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=optional_secret("AWS_SECRET_ACCESS_KEY"),
            # App config from toml
            default_provider=llm.get("default_provider", "openai"),
            default_model=llm.get("default_model", "gpt-4o"),
            openai_base_url=llm.get("openai_base_url", "https://api.openai.com/v1"),
            ollama_base_url=llm.get("ollama_base_url", "http://localhost:11434/v1"),
            foundry_base_url=llm.get("foundry_base_url"),
            aws_region=llm.get("aws_region"),
            vector_embed_model=llm.get("vector_embed_model", "nomic-embed-text"),
            search_providers=toml.get("search", {}).get("providers", ["searxng", "exa", "tavily"]),
            searxng_url=toml.get("search", {}).get("searxng_url"),
            oauth_callback_port=int(app.get("oauth_callback_port", 8765)),
            egress_url=os.environ.get("EGRESS_URL", "http://egressguard:8000"),
            comms_url=os.environ.get("COMMS_URL", "http://comms:8001"),
            workspace_dir=Path(app.get("workspace_dir", "/app/workspace")),
            groups_dir=Path(app.get("groups_dir", "/app/groups")),
            max_history_tokens=int(app["max_history_tokens"]) if app.get("max_history_tokens") else None,
        )

    @classmethod
    def from_infisical(cls) -> "Config":
        """Backwards-compatible alias for load()."""
        return cls.load()


def _load_infisical() -> dict[str, str]:
    """Fetch all secrets from Infisical. Returns a flat name→value dict."""
    client_id = os.environ.get("INFISICAL_CLIENT_ID")
    client_secret = os.environ.get("INFISICAL_CLIENT_SECRET")
    project_id = os.environ.get("INFISICAL_PROJECT_ID")

    if not client_id:
        raise ConfigError(
            "Cannot connect to Infisical. Check INFISICAL_CLIENT_ID environment variable."
        )
    if not client_secret:
        raise ConfigError(
            "Cannot connect to Infisical. Check INFISICAL_CLIENT_SECRET environment variable."
        )
    if not project_id:
        raise ConfigError(
            "Cannot connect to Infisical. Check INFISICAL_PROJECT_ID environment variable."
        )
    if InfisicalClient is None:
        raise ConfigError(
            "Cannot connect to Infisical. The infisical-python package is not installed."
        )

    try:
        client = InfisicalClient(
            ClientSettings(client_id=client_id, client_secret=client_secret)
        )
    except Exception as exc:
        raise ConfigError(
            "Cannot connect to Infisical. Check INFISICAL_CLIENT_ID and "
            "INFISICAL_CLIENT_SECRET environment variables."
        ) from exc

    secret_names = [
        "OPENAI_API_KEY",
        "OLLAMA_API_KEY",
        "SLACK_BOT_TOKEN",
        "SLACK_APP_TOKEN",
        "TAVILY_API_KEY",
        "EXA_API_KEY",
        "DB_URL",
        "FOUNDRY_API_KEY",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
    ]

    result: dict[str, str] = {}
    for name in secret_names:
        try:
            val = client.getSecret(
                GetSecretOptions(
                    environment="prod",
                    project_id=project_id,
                    secret_name=name,
                )
            )
            if val and val.secretValue:
                result[name] = val.secretValue
        except Exception:
            pass

    return result
