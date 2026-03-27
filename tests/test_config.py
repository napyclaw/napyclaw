import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path
from napyclaw.config import Config, ConfigError


def _make_infisical_client(secrets: dict) -> MagicMock:
    """Returns a mock InfisicalClient whose getSecret() returns values from `secrets`."""
    client = MagicMock()

    def get_secret(options):
        name = options.secret_name
        if name not in secrets:
            raise KeyError(name)
        result = MagicMock()
        result.secretValue = secrets[name]
        return result

    client.getSecret.side_effect = get_secret
    return client


FULL_SECRETS = {
    "OPENAI_API_KEY": "sk-test",
    "OPENAI_BASE_URL": "https://api.openai.com/v1",
    "OLLAMA_BASE_URL": "http://100.1.2.3:11434/v1",
    "OLLAMA_API_KEY": "ollama",
    "DEFAULT_MODEL": "llama3.3:latest",
    "DEFAULT_PROVIDER": "ollama",
    "SLACK_BOT_TOKEN": "xoxb-test",
    "SLACK_APP_TOKEN": "xapp-test",
    "BRAVE_API_KEY": "brave-test",
    "VECTOR_DB_URL": "postgresql://localhost:5432/napyclaw",
    "VECTOR_EMBED_MODEL": "nomic-embed-text",
    "OAUTH_CALLBACK_PORT": "8765",
    "WORKSPACE_DIR": "/tmp/napyclaw/workspace",
    "DB_PATH": "/tmp/napyclaw/napyclaw.db",
    "GROUPS_DIR": "/tmp/napyclaw/groups",
}

BOOTSTRAP_ENV = {
    "INFISICAL_CLIENT_ID": "id",
    "INFISICAL_CLIENT_SECRET": "secret",
    "INFISICAL_PROJECT_ID": "proj",
}


def test_config_loads_all_fields():
    mock_client = _make_infisical_client(FULL_SECRETS)
    with patch("napyclaw.config.InfisicalClient", return_value=mock_client), \
         patch.dict("os.environ", BOOTSTRAP_ENV):
        config = Config.from_infisical()

    assert config.openai_api_key == "sk-test"
    assert config.default_provider == "ollama"
    assert config.oauth_callback_port == 8765
    assert isinstance(config.workspace_dir, Path)
    assert config.vector_db_url == "postgresql://localhost:5432/napyclaw"
    assert config.max_history_tokens is None


def test_config_vector_db_url_optional():
    secrets = {**FULL_SECRETS}
    del secrets["VECTOR_DB_URL"]
    mock_client = _make_infisical_client(secrets)
    with patch("napyclaw.config.InfisicalClient", return_value=mock_client), \
         patch.dict("os.environ", BOOTSTRAP_ENV):
        config = Config.from_infisical()

    assert config.vector_db_url is None


def test_config_max_history_tokens_optional():
    secrets = {**FULL_SECRETS, "MAX_HISTORY_TOKENS": "4000"}
    mock_client = _make_infisical_client(secrets)
    with patch("napyclaw.config.InfisicalClient", return_value=mock_client), \
         patch.dict("os.environ", BOOTSTRAP_ENV):
        config = Config.from_infisical()

    assert config.max_history_tokens == 4000


def test_config_missing_required_field_raises():
    secrets = {**FULL_SECRETS}
    del secrets["OPENAI_API_KEY"]
    mock_client = _make_infisical_client(secrets)
    with patch("napyclaw.config.InfisicalClient", return_value=mock_client), \
         patch.dict("os.environ", BOOTSTRAP_ENV):
        with pytest.raises(ConfigError, match="OPENAI_API_KEY"):
            Config.from_infisical()


def test_config_missing_client_id_raises():
    with patch.dict("os.environ", {"INFISICAL_CLIENT_SECRET": "s", "INFISICAL_PROJECT_ID": "p"},
                    clear=True):
        with pytest.raises(ConfigError, match="INFISICAL_CLIENT_ID"):
            Config.from_infisical()


def test_config_missing_client_secret_raises():
    with patch.dict("os.environ", {"INFISICAL_CLIENT_ID": "i", "INFISICAL_PROJECT_ID": "p"},
                    clear=True):
        with pytest.raises(ConfigError, match="INFISICAL_CLIENT_SECRET"):
            Config.from_infisical()


def test_config_missing_project_id_raises():
    with patch.dict("os.environ", {"INFISICAL_CLIENT_ID": "i", "INFISICAL_CLIENT_SECRET": "s"},
                    clear=True):
        with pytest.raises(ConfigError, match="INFISICAL_PROJECT_ID"):
            Config.from_infisical()
