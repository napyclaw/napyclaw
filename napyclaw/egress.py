from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from napyclaw.db import Database
    from napyclaw.models.base import LLMClient


class EgressDeniedError(Exception):
    pass


@dataclass
class EgressVerdict:
    hostname: str
    verdict: str  # "allow" | "deny"
    confidence: float
    reason: str
    source: str  # "auto_allow" | "auto_deny" | "llm" | "human_approved" | "timeout_deny"
    cached_until: str  # ISO-8601 UTC; empty string = not cached


EGRESS_SYSTEM = """
You are a network egress policy reviewer for an AI agent.
You see ONLY the destination hostname and metadata — never payload or content.
Classify the request and respond in JSON only:
{
  "verdict": "allow|deny|escalate",
  "confidence": 0.0-1.0,
  "reason": "one sentence",
  "ttl_seconds": 3600
}
Escalate when: new/unranked domain, suspicious TLD, domain age < 30 days,
or unusual for this agent's normal traffic pattern.
"""


# Internal always-allow list
_INTERNAL_ALLOW = {
    "slack.com",
    "wss-primary.slack.com",
    "infisical.com",
    "app.infisical.com",
}


class EgressGuard:
    """Outbound domain policy engine with LLM-as-judge."""

    def __init__(
        self,
        judge_client: LLMClient | None = None,
        db: Database | None = None,
        majestic_path: Path | None = None,
    ) -> None:
        self._judge = judge_client
        self._db = db
        self._auto_allow: set[str] = set(_INTERNAL_ALLOW)
        self._auto_deny: set[str] = set()
        self._majestic: set[str] = set()
        self._verdict_cache: dict[str, EgressVerdict] = {}

        if majestic_path and majestic_path.exists():
            self._majestic = set(
                line.strip()
                for line in majestic_path.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.startswith("#")
            )

    def add_auto_allow(self, hostname: str) -> None:
        """Add a hostname to the auto-allow list (e.g., LLM endpoints)."""
        self._auto_allow.add(hostname)

    def add_auto_allow_from_url(self, url: str) -> None:
        """Extract hostname from URL and add to auto-allow."""
        from urllib.parse import urlparse

        parsed = urlparse(url)
        if parsed.hostname:
            self._auto_allow.add(parsed.hostname)

    def load_threat_intel(self, hostnames: set[str]) -> None:
        """Load threat intel hostnames into the auto-deny list."""
        self._auto_deny = hostnames

    async def check(self, hostname: str) -> bool:
        """Check if a hostname is allowed. Returns True if allowed."""
        # Tier 1: auto-deny (threat intel)
        if hostname in self._auto_deny:
            return False

        # Tier 2: auto-allow (internal + majestic + configured endpoints)
        if hostname in self._auto_allow:
            return True
        if hostname in self._majestic:
            return True

        # Check wildcard subdomains in auto-allow
        parts = hostname.split(".")
        for i in range(1, len(parts)):
            parent = ".".join(parts[i:])
            if parent in self._auto_allow:
                return True

        # Tier 3: verdict cache
        cached = self._verdict_cache.get(hostname)
        if cached:
            if cached.cached_until and cached.cached_until > datetime.now(
                timezone.utc
            ).isoformat():
                return cached.verdict == "allow"

        # Tier 4: LLM judge
        if self._judge:
            try:
                verdict = await self._llm_judge(hostname)
                self._verdict_cache[hostname] = verdict
                return verdict.verdict == "allow"
            except Exception:
                # Judge unavailable — default to deny for safety
                return False

        # No judge configured — deny unknown domains
        return False

    async def _llm_judge(self, hostname: str) -> EgressVerdict:
        """Call the LLM judge for domain classification."""
        prompt = json.dumps(
            {
                "hostname": hostname,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

        response = await self._judge.chat([
            {"role": "system", "content": EGRESS_SYSTEM},
            {"role": "user", "content": prompt},
        ])

        try:
            data = json.loads(response.text)
        except (json.JSONDecodeError, TypeError):
            # If LLM doesn't return valid JSON, deny
            return EgressVerdict(
                hostname=hostname,
                verdict="deny",
                confidence=0.0,
                reason="Judge returned invalid response",
                source="llm",
                cached_until="",
            )

        from datetime import timedelta

        ttl = data.get("ttl_seconds", 3600)
        cached_until = (
            datetime.now(timezone.utc) + timedelta(seconds=ttl)
        ).isoformat()

        verdict_str = data.get("verdict", "deny")
        if verdict_str == "escalate":
            # For v1, escalate defaults to deny (Slack approval flow added later)
            verdict_str = "deny"

        return EgressVerdict(
            hostname=hostname,
            verdict=verdict_str,
            confidence=data.get("confidence", 0.0),
            reason=data.get("reason", ""),
            source="llm",
            cached_until=cached_until,
        )

    def build_client(self, **kwargs) -> httpx.AsyncClient:
        """Create a guarded httpx.AsyncClient with egress checking."""
        client = httpx.AsyncClient(**kwargs)
        client.event_hooks["request"] = [self._check_request]
        return client

    async def _check_request(self, request: httpx.Request) -> None:
        hostname = request.url.host
        if hostname and not await self.check(hostname):
            raise EgressDeniedError(f"Egress denied: {hostname}")
