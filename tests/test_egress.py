import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from napyclaw.egress import EgressDeniedError, EgressGuard, EgressVerdict
from napyclaw.models.base import ChatResponse


class TestEgressGuardTiers:
    async def test_auto_deny_blocks(self):
        guard = EgressGuard()
        guard.load_threat_intel({"evil.com", "malware.net"})
        assert await guard.check("evil.com") is False

    async def test_auto_allow_internal(self):
        guard = EgressGuard()
        assert await guard.check("slack.com") is True
        assert await guard.check("app.infisical.com") is True

    async def test_auto_allow_configured_endpoint(self):
        guard = EgressGuard()
        guard.add_auto_allow("api.openai.com")
        assert await guard.check("api.openai.com") is True

    async def test_auto_allow_from_url(self):
        guard = EgressGuard()
        guard.add_auto_allow_from_url("http://100.1.2.3:11434/v1")
        assert await guard.check("100.1.2.3") is True

    async def test_majestic_allow(self, tmp_path):
        majestic = tmp_path / "majestic_top10k.txt"
        majestic.write_text("google.com\ngithub.com\n", encoding="utf-8")

        guard = EgressGuard(majestic_path=majestic)
        assert await guard.check("google.com") is True
        assert await guard.check("github.com") is True

    async def test_subdomain_of_auto_allow(self):
        guard = EgressGuard()
        # slack.com is in auto-allow, so api.slack.com should be allowed
        assert await guard.check("api.slack.com") is True

    async def test_unknown_domain_denied_without_judge(self):
        guard = EgressGuard()
        assert await guard.check("totally-unknown-domain.xyz") is False

    async def test_auto_deny_takes_priority_over_allow(self):
        guard = EgressGuard()
        guard.add_auto_allow("dual-listed.com")
        guard.load_threat_intel({"dual-listed.com"})
        # Auto-deny checked first
        assert await guard.check("dual-listed.com") is False


class TestEgressGuardLLMJudge:
    async def test_judge_allows(self):
        mock_client = MagicMock()
        mock_client.chat = AsyncMock(
            return_value=ChatResponse(
                text=json.dumps({
                    "verdict": "allow",
                    "confidence": 0.95,
                    "reason": "Known CDN",
                    "ttl_seconds": 3600,
                }),
                tool_calls=None,
                finish_reason="stop",
            )
        )

        guard = EgressGuard(judge_client=mock_client)
        assert await guard.check("cdn.example.com") is True

    async def test_judge_denies(self):
        mock_client = MagicMock()
        mock_client.chat = AsyncMock(
            return_value=ChatResponse(
                text=json.dumps({
                    "verdict": "deny",
                    "confidence": 0.9,
                    "reason": "Suspicious",
                    "ttl_seconds": 300,
                }),
                tool_calls=None,
                finish_reason="stop",
            )
        )

        guard = EgressGuard(judge_client=mock_client)
        assert await guard.check("sketchy.xyz") is False

    async def test_judge_escalate_defaults_to_deny(self):
        mock_client = MagicMock()
        mock_client.chat = AsyncMock(
            return_value=ChatResponse(
                text=json.dumps({
                    "verdict": "escalate",
                    "confidence": 0.5,
                    "reason": "Unknown domain",
                    "ttl_seconds": 300,
                }),
                tool_calls=None,
                finish_reason="stop",
            )
        )

        guard = EgressGuard(judge_client=mock_client)
        assert await guard.check("new-domain.io") is False

    async def test_judge_unavailable_denies(self):
        mock_client = MagicMock()
        mock_client.chat = AsyncMock(side_effect=Exception("connection refused"))

        guard = EgressGuard(judge_client=mock_client)
        assert await guard.check("unknown.com") is False

    async def test_judge_invalid_json_denies(self):
        mock_client = MagicMock()
        mock_client.chat = AsyncMock(
            return_value=ChatResponse(
                text="not valid json",
                tool_calls=None,
                finish_reason="stop",
            )
        )

        guard = EgressGuard(judge_client=mock_client)
        assert await guard.check("bad-response.com") is False

    async def test_verdict_cached(self):
        mock_client = MagicMock()
        mock_client.chat = AsyncMock(
            return_value=ChatResponse(
                text=json.dumps({
                    "verdict": "allow",
                    "confidence": 0.95,
                    "reason": "Safe",
                    "ttl_seconds": 3600,
                }),
                tool_calls=None,
                finish_reason="stop",
            )
        )

        guard = EgressGuard(judge_client=mock_client)

        # First call hits the judge
        assert await guard.check("cached-domain.com") is True
        assert mock_client.chat.call_count == 1

        # Second call uses cache
        assert await guard.check("cached-domain.com") is True
        assert mock_client.chat.call_count == 1  # Not called again


class TestGuardedClient:
    async def test_build_client_returns_httpx_client(self):
        guard = EgressGuard()
        client = guard.build_client()
        assert isinstance(client, __import__("httpx").AsyncClient)
        await client.aclose()

    async def test_denied_request_raises(self):
        guard = EgressGuard()
        # No judge, no auto-allow — unknown domain should be denied
        client = guard.build_client()

        with pytest.raises(EgressDeniedError, match="unknown-evil.com"):
            await client.get("http://unknown-evil.com/test")

        await client.aclose()
