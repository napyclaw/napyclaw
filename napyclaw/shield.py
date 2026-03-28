from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class Detection:
    type: str
    redacted: bool
    span: tuple[int, int]


@dataclass
class ShieldResult:
    clean_text: str
    detections: list[Detection] = field(default_factory=list)
    has_blocked: bool = False
    has_credentials: bool = False


# PII types that are allowed through (not redacted)
_ALLOWED_PII = {"PHONE_NUMBER", "EMAIL_ADDRESS", "PERSON", "LOCATION"}

# PII types that get redacted
_REDACTED_PII = {"US_SSN": "ssn", "CREDIT_CARD": "credit_card"}


class ContentShield:
    """Scans content for credentials and PII before storage.

    Uses detect-secrets for credential detection and Presidio for PII.
    Both libraries are lazily imported — if unavailable, scan() returns
    the original text with a warning logged.
    """

    def __init__(self) -> None:
        self._secrets_scanner = None
        self._presidio_analyzer = None
        self._presidio_anonymizer = None
        self._initialized = False

    def _ensure_init(self) -> bool:
        """Lazily initialize scanning libraries. Returns True if ready."""
        if self._initialized:
            return self._secrets_scanner is not None

        self._initialized = True

        try:
            from detect_secrets import SecretsCollection
            from detect_secrets.settings import default_settings

            self._secrets_scanner = (SecretsCollection, default_settings)
        except ImportError:
            self._secrets_scanner = None

        try:
            from presidio_analyzer import AnalyzerEngine
            from presidio_anonymizer import AnonymizerEngine

            self._presidio_analyzer = AnalyzerEngine()
            self._presidio_anonymizer = AnonymizerEngine()
        except ImportError:
            self._presidio_analyzer = None
            self._presidio_anonymizer = None

        return self._secrets_scanner is not None

    def scan(self, text: str) -> ShieldResult:
        """Scan text for credentials and PII, returning clean text."""
        detections: list[Detection] = []
        clean_text = text
        has_credentials = False

        # Phase 1: detect-secrets
        try:
            if self._ensure_init() and self._secrets_scanner:
                SecretsCollection, default_settings = self._secrets_scanner
                clean_text, secret_detections = self._scan_secrets(text)
                detections.extend(secret_detections)
                has_credentials = len(secret_detections) > 0
        except Exception:
            pass  # Scanning failure never blocks message delivery

        # Phase 2: Presidio PII
        try:
            if self._presidio_analyzer and self._presidio_anonymizer:
                clean_text, pii_detections = self._scan_pii(clean_text)
                detections.extend(pii_detections)
        except Exception:
            pass

        has_blocked = any(d.redacted for d in detections)

        return ShieldResult(
            clean_text=clean_text,
            detections=detections,
            has_blocked=has_blocked,
            has_credentials=has_credentials,
        )

    def _scan_secrets(self, text: str) -> tuple[str, list[Detection]]:
        """Scan for secrets using detect-secrets. Returns (cleaned_text, detections)."""
        import tempfile
        import os

        SecretsCollection, default_settings = self._secrets_scanner

        # detect-secrets works on files — write to temp file
        detections = []
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as f:
            f.write(text)
            tmp_path = f.name

        try:
            with default_settings():
                secrets = SecretsCollection()
                secrets.scan_file(tmp_path)

            # Collect all detected secret spans
            for filename, secret_list in secrets.data.items():
                for secret in secret_list:
                    detections.append(
                        Detection(
                            type="secret",
                            redacted=True,
                            span=(0, 0),  # detect-secrets doesn't give char spans
                        )
                    )
        finally:
            os.unlink(tmp_path)

        # Simple redaction: if secrets found, redact high-entropy strings
        clean_text = text
        if detections:
            # Redact anything that looks like an API key or token
            clean_text = re.sub(
                r'(?:sk-|xoxb-|xapp-|ghp_|gho_|Bearer\s+)\S+',
                '[REDACTED:secret]',
                clean_text,
            )

        return clean_text, detections

    def _scan_pii(self, text: str) -> tuple[str, list[Detection]]:
        """Scan for PII using Presidio. Returns (cleaned_text, detections)."""
        results = self._presidio_analyzer.analyze(
            text=text, language="en", entities=None
        )

        detections = []
        clean_text = text
        # Process detections from end to start to preserve offsets
        sorted_results = sorted(results, key=lambda r: r.start, reverse=True)

        for result in sorted_results:
            entity_type = result.entity_type

            if entity_type in _ALLOWED_PII:
                detections.append(
                    Detection(
                        type=entity_type.lower(),
                        redacted=False,
                        span=(result.start, result.end),
                    )
                )
            elif entity_type in _REDACTED_PII:
                label = _REDACTED_PII[entity_type]
                detections.append(
                    Detection(
                        type=label,
                        redacted=True,
                        span=(result.start, result.end),
                    )
                )
                clean_text = (
                    clean_text[: result.start]
                    + f"[REDACTED:{label}]"
                    + clean_text[result.end :]
                )
            else:
                # Unknown PII type — redact to be safe
                detections.append(
                    Detection(
                        type=entity_type.lower(),
                        redacted=True,
                        span=(result.start, result.end),
                    )
                )
                clean_text = (
                    clean_text[: result.start]
                    + f"[REDACTED:{entity_type.lower()}]"
                    + clean_text[result.end :]
                )

        return clean_text, detections
