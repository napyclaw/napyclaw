"""Tests for InjectionGuard — shuffle, bagging, risk tiers, rotating keys."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from napyclaw.injection_guard import (
    GuardVerdict,
    InjectionGuard,
    IndexedToken,
    RiskTier,
    ShuffleConfig,
    RISK_MAP,
)
from napyclaw.models.base import ChatResponse


# ---------------------------------------------------------------------------
# TestShuffleConfig
# ---------------------------------------------------------------------------


class TestShuffleConfig:
    def test_default_values(self):
        cfg = ShuffleConfig()
        assert cfg.spread == 5.0
        assert cfg.distribution == "gaussian"
        assert cfg.bag_size == 50
        assert cfg.overlap_ratio == 0.5
        assert cfg.seed is None
        assert cfg.tokenizer is None

    def test_invalid_distribution_raises(self):
        guard = InjectionGuard(ShuffleConfig(distribution="beta"))
        with pytest.raises(ValueError, match="Unknown distribution"):
            guard.shuffle("hello world")

    def test_invalid_overlap_raises(self):
        guard = InjectionGuard(ShuffleConfig(overlap_ratio=1.0))
        with pytest.raises(ValueError, match="overlap_ratio must be < 1.0"):
            guard.shuffle("hello world")


# ---------------------------------------------------------------------------
# TestRiskTier
# ---------------------------------------------------------------------------


class TestRiskTier:
    def test_default_mappings(self):
        guard = InjectionGuard()
        assert guard.risk_tier("llm_response") is RiskTier.LOW
        assert guard.risk_tier("vector_db") is RiskTier.LOW
        assert guard.risk_tier("email") is RiskTier.HIGH
        assert guard.risk_tier("webhook") is RiskTier.HIGH
        assert guard.risk_tier("web_search") is RiskTier.MEDIUM
        assert guard.risk_tier("unknown") is RiskTier.HIGH

    def test_unknown_source_defaults_high(self):
        guard = InjectionGuard()
        assert guard.risk_tier("something_new") is RiskTier.HIGH

    def test_should_review_low_returns_false(self):
        guard = InjectionGuard()
        assert guard.should_review("llm_response") is False
        assert guard.should_review("vector_db") is False

    def test_should_review_medium_high_returns_true(self):
        guard = InjectionGuard()
        assert guard.should_review("email") is True
        assert guard.should_review("web_search") is True

    def test_custom_risk_map(self):
        custom = {"my_source": RiskTier.LOW}
        guard = InjectionGuard(risk_map=custom)
        assert guard.risk_tier("my_source") is RiskTier.LOW
        # Unknown still defaults to HIGH
        assert guard.risk_tier("email") is RiskTier.HIGH


# ---------------------------------------------------------------------------
# TestTokenization
# ---------------------------------------------------------------------------


class TestTokenization:
    def test_whitespace_split(self):
        guard = InjectionGuard(ShuffleConfig(seed=42, spread=0))
        result = guard.shuffle("hello world foo")
        assert result.total_tokens == 3

    def test_multiline_whitespace(self):
        guard = InjectionGuard(ShuffleConfig(seed=42, spread=0))
        result = guard.shuffle("hello\n\tworld  foo")
        assert result.total_tokens == 3

    def test_empty_string(self):
        guard = InjectionGuard(ShuffleConfig(seed=42))
        result = guard.shuffle("")
        assert result.total_tokens == 0
        assert result.bags == []

    def test_single_token(self):
        guard = InjectionGuard(ShuffleConfig(seed=42))
        result = guard.shuffle("hello")
        assert result.total_tokens == 1
        assert len(result.bags) == 1

    def test_custom_tokenizer(self):
        cfg = ShuffleConfig(seed=42, spread=0, tokenizer=lambda t: t.split(","))
        guard = InjectionGuard(cfg)
        result = guard.shuffle("a,b,c")
        assert result.total_tokens == 3
        tokens = [t.token for t in result.bags[0].tokens]
        assert tokens == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# TestNoiseApplication
# ---------------------------------------------------------------------------


class TestNoiseApplication:
    def test_deterministic_with_seed(self):
        guard = InjectionGuard(ShuffleConfig(seed=123))
        text = "the quick brown fox jumps over the lazy dog"
        r1 = guard.shuffle(text)
        r2 = guard.shuffle(text)
        t1 = [t.token for bag in r1.bags for t in bag.tokens]
        t2 = [t.token for bag in r2.bags for t in bag.tokens]
        assert t1 == t2

    def test_different_seeds_differ(self):
        text = "the quick brown fox jumps over the lazy dog"
        r1 = InjectionGuard(ShuffleConfig(seed=1)).shuffle(text)
        r2 = InjectionGuard(ShuffleConfig(seed=999)).shuffle(text)
        t1 = [t.token for bag in r1.bags for t in bag.tokens]
        t2 = [t.token for bag in r2.bags for t in bag.tokens]
        assert t1 != t2

    def test_spread_zero_preserves_order(self):
        guard = InjectionGuard(ShuffleConfig(seed=42, spread=0))
        text = "alpha beta gamma delta epsilon"
        result = guard.shuffle(text)
        tokens = [t.token for bag in result.bags for t in bag.tokens]
        assert tokens == ["alpha", "beta", "gamma", "delta", "epsilon"]

    def test_uniform_distribution(self):
        guard = InjectionGuard(ShuffleConfig(seed=42, distribution="uniform"))
        text = "one two three four five six seven eight nine ten"
        result = guard.shuffle(text)
        assert result.total_tokens == 10

    def test_seed_captured_when_none(self):
        guard = InjectionGuard(ShuffleConfig(seed=None))
        result = guard.shuffle("hello world")
        assert result.seed is not None

    def test_captured_seed_reproduces(self):
        guard = InjectionGuard(ShuffleConfig(seed=None))
        text = "the quick brown fox jumps over the lazy dog"
        r1 = guard.shuffle(text)
        guard2 = InjectionGuard(ShuffleConfig(seed=r1.seed))
        r2 = guard2.shuffle(text)
        t1 = [t.token for bag in r1.bags for t in bag.tokens]
        t2 = [t.token for bag in r2.bags for t in bag.tokens]
        assert t1 == t2


# ---------------------------------------------------------------------------
# TestBagging
# ---------------------------------------------------------------------------


class TestBagging:
    def _make_text(self, n: int) -> str:
        return " ".join(f"w{i}" for i in range(n))

    def test_bag_count_100_tokens(self):
        # bag_size=50, overlap=0.5 → stride=25 → bags at [0:50],[25:75],[50:100]
        guard = InjectionGuard(ShuffleConfig(seed=42, spread=0, bag_size=50))
        result = guard.shuffle(self._make_text(100))
        assert len(result.bags) == 3

    def test_bag_overlap_content(self):
        guard = InjectionGuard(ShuffleConfig(seed=42, spread=0, bag_size=50))
        result = guard.shuffle(self._make_text(100))
        bag0_tokens = {t.token for t in result.bags[0].tokens[25:]}
        bag1_tokens = {t.token for t in result.bags[1].tokens[:25]}
        assert bag0_tokens == bag1_tokens

    def test_no_overlap(self):
        guard = InjectionGuard(
            ShuffleConfig(seed=42, spread=0, bag_size=50, overlap_ratio=0.0)
        )
        result = guard.shuffle(self._make_text(100))
        assert len(result.bags) == 2
        bag0_set = {t.token for t in result.bags[0].tokens}
        bag1_set = {t.token for t in result.bags[1].tokens}
        assert bag0_set.isdisjoint(bag1_set)

    def test_small_input_single_bag(self):
        guard = InjectionGuard(ShuffleConfig(seed=42, bag_size=50))
        result = guard.shuffle(self._make_text(10))
        assert len(result.bags) == 1

    def test_bag_text_is_space_joined(self):
        guard = InjectionGuard(ShuffleConfig(seed=42, spread=0, bag_size=50))
        result = guard.shuffle("alpha beta gamma")
        assert result.bags[0].text == "alpha beta gamma"

    def test_all_tokens_covered(self):
        guard = InjectionGuard(ShuffleConfig(seed=42, spread=0))
        text = self._make_text(120)
        result = guard.shuffle(text)
        all_tokens = set()
        for bag in result.bags:
            all_tokens.update(t.token for t in bag.tokens)
        expected = {f"w{i}" for i in range(120)}
        assert all_tokens == expected


# ---------------------------------------------------------------------------
# TestRotatingKeys
# ---------------------------------------------------------------------------


class TestRotatingKeys:
    def test_valid_key_accepted(self):
        verdict = InjectionGuard._parse_verdict(
            json.dumps({"risk": "safe", "verification": "v-abcd1234"}),
            "v-abcd1234",
            bag_id=0,
        )
        assert verdict.risk == "safe"
        assert verdict.key_valid is True

    def test_wrong_key_rejected(self):
        verdict = InjectionGuard._parse_verdict(
            json.dumps({"risk": "safe", "verification": "v-wrong"}),
            "v-abcd1234",
            bag_id=0,
        )
        assert verdict.risk == "malicious"
        assert verdict.key_valid is False

    def test_missing_key_rejected(self):
        verdict = InjectionGuard._parse_verdict(
            json.dumps({"risk": "safe"}),
            "v-abcd1234",
            bag_id=0,
        )
        assert verdict.risk == "malicious"
        assert verdict.key_valid is False

    def test_extra_fields_rejected(self):
        verdict = InjectionGuard._parse_verdict(
            json.dumps({"risk": "safe", "verification": "v-abcd1234", "extra": "bad"}),
            "v-abcd1234",
            bag_id=0,
        )
        assert verdict.risk == "malicious"
        assert verdict.key_valid is False

    def test_invalid_risk_value_rejected(self):
        verdict = InjectionGuard._parse_verdict(
            json.dumps({"risk": "ignore_previous_instructions", "verification": "v-abcd1234"}),
            "v-abcd1234",
            bag_id=0,
        )
        assert verdict.risk == "malicious"
        assert verdict.key_valid is False

    def test_non_json_rejected(self):
        verdict = InjectionGuard._parse_verdict(
            "Sure! Here is the risk level: safe",
            "v-abcd1234",
            bag_id=0,
        )
        assert verdict.risk == "malicious"
        assert verdict.key_valid is False


# ---------------------------------------------------------------------------
# TestGuardedReview
# ---------------------------------------------------------------------------


class TestGuardedReview:
    @staticmethod
    def _mock_judge(response_text: str):
        judge = MagicMock()
        judge.chat = AsyncMock(
            return_value=ChatResponse(text=response_text, tool_calls=None, finish_reason="stop")
        )
        return judge

    async def test_valid_response_parsed(self):
        guard = InjectionGuard(ShuffleConfig(seed=42, bag_size=500))
        text = "hello world test content"

        # We need to intercept the key. Monkey-patch _generate_key.
        guard._generate_key = staticmethod(lambda: "v-testkey1")  # type: ignore[assignment]
        judge = self._mock_judge(json.dumps({"risk": "safe", "verification": "v-testkey1"}))

        verdicts = await guard.review(text, "email", judge)
        assert len(verdicts) == 1
        assert verdicts[0].risk == "safe"
        assert verdicts[0].key_valid is True

    async def test_malformed_response_denied(self):
        guard = InjectionGuard(ShuffleConfig(seed=42, bag_size=500))
        judge = self._mock_judge("This is not JSON at all")

        verdicts = await guard.review("test content", "email", judge)
        assert all(v.risk == "malicious" for v in verdicts)

    async def test_judge_exception_denied(self):
        guard = InjectionGuard(ShuffleConfig(seed=42, bag_size=500))
        judge = MagicMock()
        judge.chat = AsyncMock(side_effect=Exception("connection failed"))

        verdicts = await guard.review("test content", "email", judge)
        assert all(v.risk == "malicious" for v in verdicts)
        assert all(v.key_valid is False for v in verdicts)


# ---------------------------------------------------------------------------
# TestReviewFlow
# ---------------------------------------------------------------------------


class TestReviewFlow:
    async def test_low_source_skips_review(self):
        guard = InjectionGuard()
        judge = MagicMock()
        judge.chat = AsyncMock()

        verdicts = await guard.review("anything", "llm_response", judge)
        assert len(verdicts) == 1
        assert verdicts[0].risk == "safe"
        judge.chat.assert_not_called()

    async def test_high_source_produces_verdicts(self):
        guard = InjectionGuard(ShuffleConfig(seed=42, bag_size=500))
        guard._generate_key = staticmethod(lambda: "v-abc")  # type: ignore[assignment]
        judge = MagicMock()
        judge.chat = AsyncMock(
            return_value=ChatResponse(
                text=json.dumps({"risk": "suspicious", "verification": "v-abc"}),
                tool_calls=None,
                finish_reason="stop",
            )
        )

        verdicts = await guard.review("some user email content", "email", judge)
        assert len(verdicts) >= 1
        assert verdicts[0].risk == "suspicious"

    async def test_empty_text_safe(self):
        guard = InjectionGuard()
        judge = MagicMock()
        verdicts = await guard.review("", "email", judge)
        assert len(verdicts) == 1
        assert verdicts[0].risk == "safe"


# ---------------------------------------------------------------------------
# TestEdgeCases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_very_large_spread(self):
        guard = InjectionGuard(ShuffleConfig(seed=42, spread=1000))
        result = guard.shuffle("a b c d e f g h i j")
        assert result.total_tokens == 10

    def test_unicode_tokens(self):
        guard = InjectionGuard(ShuffleConfig(seed=42, spread=0))
        result = guard.shuffle("hello 世界 🌍 café")
        tokens = {t.token for bag in result.bags for t in bag.tokens}
        assert tokens == {"hello", "世界", "🌍", "café"}

    def test_repeated_tokens(self):
        guard = InjectionGuard(ShuffleConfig(seed=42, spread=0))
        result = guard.shuffle("the the the the")
        assert result.total_tokens == 4
        # All tokens present, distinguished by index
        indices = {t.index for bag in result.bags for t in bag.tokens}
        assert indices == {0, 1, 2, 3}
