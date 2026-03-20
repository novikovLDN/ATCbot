"""
Tests for rate limiter bucket eviction and middleware Redis reconnect.
"""
import time
from unittest.mock import patch

import pytest

from app.core.rate_limit import (
    TokenBucket,
    RateLimiter,
    RateLimitConfig,
)


class TestBucketEviction:
    """Verify that stale buckets are evicted to prevent memory growth."""

    def test_stale_buckets_evicted(self):
        limiter = RateLimiter()
        cfg = RateLimitConfig("test", max_requests=5, window_seconds=60)

        # Create buckets for 100 users
        for uid in range(100):
            limiter.check_rate_limit(uid, "test", cfg)
        assert len(limiter._buckets) == 100

        # Age all buckets beyond BUCKET_MAX_AGE
        for bucket in limiter._buckets.values():
            bucket.last_refill = time.time() - limiter._BUCKET_MAX_AGE - 10

        # Force eviction interval to trigger
        limiter._last_eviction = time.time() - limiter._EVICTION_INTERVAL - 1

        # Next check_rate_limit triggers eviction
        limiter.check_rate_limit(999, "test", cfg)

        # Only user 999's bucket should remain (stale ones evicted)
        assert len(limiter._buckets) == 1
        assert (999, "test") in limiter._buckets

    def test_active_buckets_not_evicted(self):
        limiter = RateLimiter()
        cfg = RateLimitConfig("test", max_requests=5, window_seconds=60)

        # Create fresh bucket
        limiter.check_rate_limit(111, "test", cfg)

        # Force eviction interval
        limiter._last_eviction = time.time() - limiter._EVICTION_INTERVAL - 1

        # Trigger eviction with another user
        limiter.check_rate_limit(222, "test", cfg)

        # Both should remain (both are fresh)
        assert len(limiter._buckets) == 2

    def test_eviction_does_not_run_before_interval(self):
        limiter = RateLimiter()
        cfg = RateLimitConfig("test", max_requests=5, window_seconds=60)

        # Create and age buckets
        limiter.check_rate_limit(111, "test", cfg)
        for bucket in limiter._buckets.values():
            bucket.last_refill = time.time() - limiter._BUCKET_MAX_AGE - 10

        # Don't force eviction interval — it was just set in __init__
        limiter._last_eviction = time.time()

        limiter.check_rate_limit(222, "test", cfg)
        # Stale bucket should NOT be evicted yet
        assert len(limiter._buckets) == 2
