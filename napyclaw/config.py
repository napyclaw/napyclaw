import os
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


class ConfigError(Exception):
    pass


@dataclass
class Config:
    # LLM
    openai_api_key: str
    openai_base_url: str
    ollama_base_url: str
    ollama_api_key: str
    default_model: str
    default_provider: str

    # Slack
    slack_bot_token: str
    slack_app_token: str

    # Web search
    brave_api_key: str

    # Database
    db_url: str
    vector_embed_model: str

    # OAuth
    oauth_callback_port: int

    # Paths
    workspace_dir: Path
    groups_dir: Path

    # Agent tuning (optional)
    max_history_tokens: int | None

    @classmethod
    def from_infisical(cls) -> "Config":
        client_id = os.environ.get("INFISICAL_CLIENT_ID")
        client_secret = os.environ.get("INFISICAL_CLIENT_SECRET")
        project_id = os.environ.get("INFISICAL_PROJECT_ID")

        if not client_id:
            raise ConfigError(
                "Cannot connect to Infisical. Check INFISICAL_CLIENT_ID and "
                "INFISICAL_CLIENT_SECRET environment variables."
            )
        if not client_secret:
            raise ConfigError(
                "Cannot connect to Infisical. Check INFISICAL_CLIENT_ID and "
                "INFISICAL_CLIENT_SECRET environment variables."
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

        def require(name: str) -> str:
            try:
                result = client.getSecret(
                    GetSecretOptions(
                        environment="prod",
                        project_id=project_id,
                        secret_name=name,
                    )
                )
                return result.secretValue
            except Exception:
                raise ConfigError(f"Missing required config: {name}")

        def optional(name: str) -> str | None:
            try:
                result = client.getSecret(
                    GetSecretOptions(
                        environment="prod",
                        project_id=project_id,
                        secret_name=name,
                    )
                )
                return result.secretValue
            except Exception:
                return None

        max_history_raw = optional("MAX_HISTORY_TOKENS")

        return cls(
            openai_api_key=require("OPENAI_API_KEY"),
            openai_base_url=require("OPENAI_BASE_URL"),
            ollama_base_url=require("OLLAMA_BASE_URL"),
            ollama_api_key=require("OLLAMA_API_KEY"),
            default_model=require("DEFAULT_MODEL"),
            default_provider=require("DEFAULT_PROVIDER"),
            slack_bot_token=require("SLACK_BOT_TOKEN"),
            slack_app_token=require("SLACK_APP_TOKEN"),
            brave_api_key=require("BRAVE_API_KEY"),
            db_url=require("DB_URL"),
            vector_embed_model=require("VECTOR_EMBED_MODEL"),
            oauth_callback_port=int(require("OAUTH_CALLBACK_PORT")),
            workspace_dir=Path(require("WORKSPACE_DIR")),
            groups_dir=Path(require("GROUPS_DIR")),
            max_history_tokens=int(max_history_raw) if max_history_raw else None,
        )
