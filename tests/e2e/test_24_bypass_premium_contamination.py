"""Scenario 24 — the bypass cache columns point at the PREMIUM entity.

Production 2026-09-14: ~300 tg_*_premium entities had expireAt ~10 years ahead.
subscriptions.remnawave_id / remnawave_uuid (bypass columns) held the premium
entity (backfill contamination); on the expiry → bypass-only transition
extend_remnawave_for_bypass PATCHed "the bypass" found through them — the
premium — to expireAt = now + 10 years, ACTIVE.

Now: the premium keeps its ended date, the real bypass (username == str(tg))
is the one kept ACTIVE, and the row's bypass columns are re-written to it.
"""
from __future__ import annotations

from datetime import timedelta

import database
from tests.e2e import flows
from tests.e2e.world import naive, new_user, utcnow

# docs/RUNBOOK.md §7 — the owner's read-only count of contaminated rows
CONTAMINATED = """
SELECT count(*) FROM subscriptions
 WHERE (remnawave_id IS NOT NULL AND remnawave_id = remnawave_premium_id)
    OR (remnawave_uuid IS NOT NULL AND remnawave_uuid = remnawave_premium_uuid)
"""


async def test_contaminated_cache_never_extends_premium_on_expiry(e2e):
    u = new_user()
    await flows.seed_active(e2e, u, "basic", utcnow() + timedelta(days=20), flag="on")
    prem, byp = e2e.panel.premium(u.id), e2e.panel.bypass(u.id)
    assert prem is not None and byp is not None
    sub = await e2e.sub(u.id)
    assert sub["remnawave_premium_id"] == prem["id"] and sub["remnawave_id"] == byp["id"], sub

    await e2e.pool.execute(
        "UPDATE subscriptions SET remnawave_id = remnawave_premium_id, "
        "remnawave_uuid = remnawave_premium_uuid WHERE telegram_id=$1", u.id)
    assert await e2e.val(CONTAMINATED + " AND telegram_id=$1", u.id) == 1

    past = utcnow() - timedelta(minutes=5)
    await e2e.pool.execute("UPDATE subscriptions SET expires_at=$2 WHERE telegram_id=$1", u.id, naive(past))
    prem["expireAt"], prem["status"] = past, "EXPIRED"
    byp["status"] = "DISABLED"             # the extend must really PATCH the bypass

    assert await database.check_and_disable_expired_subscription(u.id) is True
    await e2e.settle()

    prem, byp = e2e.panel.premium(u.id), e2e.panel.bypass(u.id)
    assert prem["expireAt"] == past and prem["status"] == "EXPIRED", "no free premium"
    assert byp["status"] == "ACTIVE" and byp["expireAt"] > utcnow() + timedelta(days=5 * 365)
    sub = await e2e.sub(u.id)
    assert (sub["is_bypass_only"], sub["remnawave_id"], sub["remnawave_uuid"]) == \
        (True, byp["id"], byp["vlessUuid"]), sub
    assert await e2e.val(CONTAMINATED + " AND telegram_id=$1", u.id) == 0
