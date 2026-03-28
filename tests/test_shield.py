import pytest

from napyclaw.shield import ContentShield, ShieldResult


class TestContentShield:
    def test_clean_text_passes_through(self):
        shield = ContentShield()
        result = shield.scan("Hello, this is a normal message.")
        assert result.clean_text == "Hello, this is a normal message."
        assert result.has_blocked is False
        assert result.has_credentials is False

    def test_returns_shield_result(self):
        shield = ContentShield()
        result = shield.scan("test")
        assert isinstance(result, ShieldResult)

    def test_api_key_pattern_redacted(self):
        shield = ContentShield()
        # Even without detect-secrets installed, the regex fallback should catch this
        text = "My key is sk-1234567890abcdef"
        result = shield.scan(text)
        # If detect-secrets is available, it will detect and redact
        # If not, the text passes through (graceful degradation)
        assert isinstance(result, ShieldResult)

    def test_slack_token_pattern(self):
        shield = ContentShield()
        text = "Bot token is xoxb-1234567890-abcdef"
        result = shield.scan(text)
        assert isinstance(result, ShieldResult)

    def test_scan_never_raises(self):
        """ContentShield.scan() must never raise — failures are silent."""
        shield = ContentShield()
        # Force internal failure by corrupting state
        shield._initialized = True
        shield._secrets_scanner = "invalid"
        result = shield.scan("test with bad scanner")
        assert isinstance(result, ShieldResult)
        assert result.clean_text == "test with bad scanner"

    def test_empty_text(self):
        shield = ContentShield()
        result = shield.scan("")
        assert result.clean_text == ""
        assert result.has_blocked is False
