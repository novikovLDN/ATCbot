"""GET /broadcasts/segments counted every segment (full scans) on every open of
the broadcast screen / the notification editor. Counts are cached server-side
for SEGMENT_COUNTS_TTL_SECONDS; a failed count is not cached."""
from __future__ import annotations

import pytest

import tests.conftest  # noqa: F401  (env before config)
from app.api.dashboard.routes import broadcasts as br


@pytest.fixture
def world(monkeypatch):
    br.reset_segment_counts_cache()
    st = {"calls": 0, "clock": 1000.0, "fail": set()}

    async def count(key):
        st["calls"] += 1
        if key in st["fail"]:
            raise RuntimeError("db down")
        return [1, 2]
    monkeypatch.setattr(br.database, "get_users_by_segment", count)
    monkeypatch.setattr(br, "_clock", lambda: st["clock"])
    yield st
    br.reset_segment_counts_cache()


async def test_segment_counts_are_cached_for_60_seconds(world):
    first = await br.segments_list()
    n = world["calls"]
    assert n == len(first) > 0 and all(s["count"] == 2 for s in first)

    world["clock"] += br.SEGMENT_COUNTS_TTL_SECONDS - 1
    assert await br.segments_list() == first
    assert world["calls"] == n, "no scan within the TTL"

    world["clock"] += 2
    await br.segments_list()
    assert world["calls"] == 2 * n, "recounted after the TTL"


async def test_a_failed_count_is_not_cached(world):
    world["fail"] = {"expired_within_1y"}
    listed = {s["key"]: s["count"] for s in await br.segments_list()}
    assert listed["expired_within_1y"] == -1
    world["fail"] = set()
    calls = world["calls"]
    listed = {s["key"]: s["count"] for s in await br.segments_list()}
    assert listed["expired_within_1y"] == 2 and world["calls"] == calls + 1
