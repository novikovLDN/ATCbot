"""The home screen used to load its ~30 queries strictly end to end.

/metrics/overview is the slowest page in the dashboard and it shares the
bot's connection pool; every section it needs is independent of the
others, so they are loaded together. The test pins that: fourteen loads
of 50 ms each must not add up.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from app.api.dashboard.routes import metrics as routes

DELAY = 0.05
PARTS = 14

TOTALS = {
    "net_kopecks": 100_000, "net_count": 10, "gross_kopecks": 120_000,
    "shop_kopecks": 10_000, "proxy_kopecks": 5_000, "game_kopecks": 5_000,
}
PAYERS = {"payers": 8, "new": 3, "returning": 5}
ACTIVE = {
    "with_access": 100, "paid": 80, "by_kind": {"paid": 80}, "granted_sources": {},
    "expiring_7d": {"total": 4, "auto_renew_on": 3}, "auto_renew_share": 50.0, "by_tariff": {},
}
RENEWALS = {"renewal_rate": 70.0, "churn_rate": 30.0}


@pytest.fixture
def slow_sections(monkeypatch):
    """Every section of the report takes DELAY to load."""
    async def slow(value):
        await asyncio.sleep(DELAY)
        return value

    monkeypatch.setattr(routes.rev, "totals", lambda *a, **k: slow(dict(TOTALS)))
    monkeypatch.setattr(routes.rev, "payers", lambda *a, **k: slow(dict(PAYERS)))
    monkeypatch.setattr(routes.rev, "series", lambda *a, **k: slow([]))
    monkeypatch.setattr(routes.mx, "new_users", lambda *a, **k: slow(5))
    monkeypatch.setattr(routes.mx, "active_subscriptions", lambda *a, **k: slow(dict(ACTIVE)))
    monkeypatch.setattr(routes.mx, "renewals", lambda *a, **k: slow(dict(RENEWALS)))
    monkeypatch.setattr(routes.mx, "renewal_pipeline", lambda *a, **k: slow(None))
    monkeypatch.setattr(routes, "_payments_health", lambda *a, **k: slow(None))
    monkeypatch.setattr(routes, "_delivery", lambda *a, **k: slow(None))
    monkeypatch.setattr(routes, "_system_health", lambda *a, **k: slow(None))
    monkeypatch.setattr(routes, "_panel_quick", lambda *a, **k: slow((None, None)))


async def test_overview_loads_its_sections_together(slow_sections):
    started = time.monotonic()
    report = await routes._overview(7)
    elapsed = time.monotonic() - started

    assert elapsed < PARTS * DELAY / 2, f"sections still load one after another: {elapsed:.2f}s"
    assert report["window_days"] == 7
    assert [k["key"] for k in report["kpis"]][:2] == ["revenue", "payments"]


async def test_a_failing_section_still_fails_the_report(monkeypatch, slow_sections):
    """Behaviour of the sequential version: a hard section raises, and
    the route turns it into a 500 — it does not render half a screen."""
    async def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(routes.rev, "totals", boom)

    with pytest.raises(RuntimeError):
        await routes._overview(7)
