"""Bypass-audit list (dashboard «Bypass-аудит»): subscription_history.end_date
may come back tz-aware from the production DB; the grace check compared it
with a naive now → TypeError → DASHBOARD_ROUTE_FAIL list_failed (prod log
2026-09-14). Both naive and aware end_date must work."""
from datetime import datetime, timedelta, timezone

import pytest

import database.admin as admin


class _Conn:
    def __init__(self, end_date):
        self.end_date = end_date

    async def fetch(self, sql, *args):
        if "FROM subscriptions s" in sql:
            return [{
                "telegram_id": 1, "username": "u", "current_expires_at": None,
                "is_bypass_only": True, "current_subscription_type": "basic",
                "current_source": "bypass_only", "is_combo": False,
            }]
        return []

    async def fetchval(self, sql, *args):
        return self.end_date

    async def fetchrow(self, sql, *args):
        return None


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        pool = self

        class _Ctx:
            async def __aenter__(self):
                return pool.conn

            async def __aexit__(self, *exc):
                return False
        return _Ctx()


@pytest.mark.parametrize("aware", [True, False])
@pytest.mark.parametrize("days, grace", [(-10, True), (30, False)])
async def test_victims_grace_check_accepts_naive_and_aware_end_date(monkeypatch, aware, days, grace):
    end = datetime.now(timezone.utc) + timedelta(days=days)
    if not aware:
        end = end.replace(tzinfo=None)

    async def _get_pool():
        return _Pool(_Conn(end))
    monkeypatch.setattr(admin, "get_pool", _get_pool)

    victims = await admin.get_bypass_overwrite_victims()

    assert len(victims) == 1
    assert victims[0]["grace_will_apply"] is grace
