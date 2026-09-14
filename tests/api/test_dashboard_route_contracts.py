"""Dashboard routes the SPA depends on (found by the mobile/contract audit):

- GET /broadcasts/scheduled must not be swallowed by GET /broadcasts/{broadcast_id}
  (it answered 422, so the scheduled list never loaded).
- POST /remnawave/reset-premium-unlimited must report dry_run/samples even when
  there is nothing to check, so the UI does not claim a reset happened.
"""
from __future__ import annotations

from starlette.routing import Match

import database
from app.api.dashboard.routes import broadcasts, remnawave


def _first_get_match(router, path: str):
    scope = {"type": "http", "path": path, "method": "GET", "root_path": ""}
    for route in router.routes:
        match, _ = route.matches(scope)
        if match == Match.FULL:
            return route.path
    return None


def test_scheduled_list_is_not_shadowed_by_broadcast_id():
    assert _first_get_match(broadcasts.router, "/scheduled") == "/scheduled"
    assert _first_get_match(broadcasts.router, "/scheduled/5") == "/scheduled/{sched_id}"
    assert _first_get_match(broadcasts.router, "/12") == "/{broadcast_id}"


class _Conn:
    async def fetch(self, *_a, **_k):
        return []


class _Acquire:
    async def __aenter__(self):
        return _Conn()

    async def __aexit__(self, *_exc):
        return False


class _Pool:
    def acquire(self):
        return _Acquire()


async def test_reset_premium_unlimited_empty_keeps_dry_run(monkeypatch):
    async def get_pool():
        return _Pool()

    monkeypatch.setattr(database, "get_pool", get_pool, raising=False)
    out = await remnawave.reset_premium_unlimited(dry_run=True, concurrent=5, admin={"sub": 1})
    assert out["total"] == 0
    assert out["dry_run"] is True
    assert out["samples"] == []
