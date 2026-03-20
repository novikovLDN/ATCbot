"""
Tests for rate limiting (token bucket + middleware).
"""
import time
from unittest.mock import patch

import pytest

from app.core.rate_limit import (
    TokenBucket,
    RateLimiter,
    RateLimitConfig,
    check_rate_limit,
)


class TestTokenBucket:
    """Token bucket algorithm tests."""

    def test_consume_within_limit(self):
        bucket = TokenBucket(max_tokens=5, refill_rate=1.0)
        assert bucket.consume(1) is True
        assert bucket.consume(1) is True

    def test_consume_exhausts_bucket(self):
        bucket = TokenBucket(max_tokens=2, refill_rate=0.0)  # no refill
        assert bucket.consume(1) is True
        assert bucket.consume(1) is True
        assert bucket.consume(1) is False

    def test_refill_restores_tokens(self):
        bucket = TokenBucket(max_tokens=1, refill_rate=100.0)  # fast refill
        assert bucket.consume(1) is True
        assert bucket.consume(1) is False
        # Simulate time passing
        bucket.last_refill -= 1.0  # 1 second ago → 100 tokens refilled (capped at 1)
        assert bucket.consume(1) is True

    def test_consume_multiple_tokens(self):
        bucket = TokenBucket(max_tokens=5, refill_rate=0.0)
        assert bucket.consume(3) is True
        assert bucket.consume(3) is False
        assert bucket.consume(2) is True

    def test_get_remaining(self):
        bucket = TokenBucket(max_tokens=5, refill_rate=0.0)
        assert bucket.get_remaining() == 5.0
        bucket.consume(2)
        assert bucket.get_remaining() == 3.0

    def test_max_tokens_cap(self):
        bucket = TokenBucket(max_tokens=3, refill_rate=100.0)
        bucket.last_refill -= 10.0  # Should refill 1000 tokens but cap at 3
        assert bucket.get_remaining() == 3.0


class TestRateLimiter:
    """Rate limiter per-user per-action tests."""

    def test_allows_within_limit(self):
        limiter = RateLimiter()
        allowed, msg = limiter.check_rate_limit(
            telegram_id=111,
            action_key="test_action",
            custom_config=RateLimitConfig("test_action", max_requests=3, window_seconds=60),
        )
        assert allowed is True
        assert msg is None

    def test_blocks_after_exhaustion(self):
        limiter = RateLimiter()
        cfg = RateLimitConfig("test_action", max_requests=2, window_seconds=60)
        limiter.check_rate_limit(111, "test_action", cfg)
        limiter.check_rate_limit(111, "test_action", cfg)
        allowed, msg = limiter.check_rate_limit(111, "test_action", cfg)
        assert allowed is False
        assert msg is not None
        assert "секунд" in msg

    def test_per_user_isolation(self):
        limiter = RateLimiter()
        cfg = RateLimitConfig("test_action", max_requests=1, window_seconds=60)
        limiter.check_rate_limit(111, "test_action", cfg)
        # User 222 should still be allowed
        allowed, _ = limiter.check_rate_limit(222, "test_action", cfg)
        assert allowed is True

    def test_per_action_isolation(self):
        limiter = RateLimiter()
        cfg_a = RateLimitConfig("action_a", max_requests=1, window_seconds=60)
        cfg_b = RateLimitConfig("action_b", max_requests=1, window_seconds=60)
        limiter.check_rate_limit(111, "action_a", cfg_a)
        # Same user, different action — should be allowed
        allowed, _ = limiter.check_rate_limit(111, "action_b", cfg_b)
        assert allowed is True

    def test_unknown_action_always_allowed(self):
        limiter = RateLimiter()
        allowed, msg = limiter.check_rate_limit(111, "unknown_action_xyz")
        assert allowed is True
        assert msg is None

    def test_get_status(self):
        limiter = RateLimiter()
        # Use a default config (payment_init) so get_status can find it
        limiter.check_rate_limit(111, "payment_init")
        status = limiter.get_status(111, "payment_init")
        assert status["limited"] is True
        assert status["limit"] == 5
        assert status["remaining"] <= 5

    def test_default_configs_exist(self):
        limiter = RateLimiter()
        # Should use default config for payment_init
        allowed, _ = limiter.check_rate_limit(111, "payment_init")
        assert allowed is True


class TestCheckRateLimitConvenience:
    """Test module-level convenience function."""

    def test_convenience_function(self):
        allowed, msg = check_rate_limit(999, "payment_init")
        assert allowed is True
        assert msg is None
