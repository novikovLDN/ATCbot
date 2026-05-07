"""Unit tests for app.core.rate_limit (async-safe token bucket)."""
from __future__ import annotations

import asyncio
import time

import pytest

from app.core import rate_limit as rl


@pytest.mark.asyncio
async def test_allows_below_limit():
    cfg = rl.RateLimitConfig("t1", max_requests=3, window_seconds=60)
    limiter = rl.RateLimiter()
    for _ in range(3):
        ok, msg = await limiter.check_rate_limit(1, "t1", custom_config=cfg)
        assert ok and msg is None


@pytest.mark.asyncio
async def test_blocks_above_limit():
    cfg = rl.RateLimitConfig("t2", max_requests=2, window_seconds=60)
    limiter = rl.RateLimiter()
    await limiter.check_rate_limit(1, "t2", custom_config=cfg)
    await limiter.check_rate_limit(1, "t2", custom_config=cfg)
    ok, msg = await limiter.check_rate_limit(1, "t2", custom_config=cfg)
    assert ok is False
    assert msg and "секунд" in msg


@pytest.mark.asyncio
async def test_per_user_isolation():
    cfg = rl.RateLimitConfig("t3", max_requests=1, window_seconds=60)
    limiter = rl.RateLimiter()
    ok1, _ = await limiter.check_rate_limit(1, "t3", custom_config=cfg)
    ok2, _ = await limiter.check_rate_limit(2, "t3", custom_config=cfg)
    ok1_again, _ = await limiter.check_rate_limit(1, "t3", custom_config=cfg)
    assert ok1 is True
    assert ok2 is True
    assert ok1_again is False


@pytest.mark.asyncio
async def test_unknown_action_passes_through():
    limiter = rl.RateLimiter()
    ok, msg = await limiter.check_rate_limit(1, "unknown_action_xyz")
    assert ok is True
    assert msg is None


@pytest.mark.asyncio
async def test_lru_eviction(monkeypatch):
    monkeypatch.setattr(rl, "MAX_TRACKED_KEYS", 3)
    cfg = rl.RateLimitConfig("t4", max_requests=5, window_seconds=60)
    limiter = rl.RateLimiter()
    # Insert 5 distinct keys; oldest 2 must evict.
    for i in range(5):
        await limiter.check_rate_limit(i, "t4", custom_config=cfg)
    snap = await limiter.snapshot()
    assert snap["buckets_total"] == 3


@pytest.mark.asyncio
async def test_prune_stale(monkeypatch):
    cfg = rl.RateLimitConfig("t5", max_requests=2, window_seconds=60)
    limiter = rl.RateLimiter()
    clock = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    await limiter.check_rate_limit(1, "t5", custom_config=cfg)
    clock[0] += 10_000.0  # > 2 hours
    pruned = await limiter.prune_stale(max_age_seconds=7200.0)
    assert pruned == 1


@pytest.mark.asyncio
async def test_module_level_helper_works():
    # Use a known action key from DEFAULT_RATE_LIMITS to exercise the helper path.
    ok, _ = await rl.check_rate_limit(42, "trial_activate")
    # First call should succeed (limit is 1 per hour).
    assert ok is True
    ok2, msg = await rl.check_rate_limit(42, "trial_activate")
    assert ok2 is False and msg is not None


@pytest.mark.asyncio
async def test_concurrent_calls_dont_double_charge():
    """Two concurrent calls must not both succeed when the limit is 1."""
    cfg = rl.RateLimitConfig("t6", max_requests=1, window_seconds=60)
    limiter = rl.RateLimiter()
    results = await asyncio.gather(
        limiter.check_rate_limit(1, "t6", custom_config=cfg),
        limiter.check_rate_limit(1, "t6", custom_config=cfg),
    )
    successes = sum(1 for ok, _ in results if ok)
    assert successes == 1
