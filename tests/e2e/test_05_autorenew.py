"""Scenario 5 — auto-renewal worker (one real pass of process_auto_renewals).

Owner rules: auto-renew charges the REAL tariff of the subscription —
basic → basic price + 10 GB; combo → combo price + combo GB; a legacy biz_*
row renews as Plus at the current Plus price. Money is debited once, in the
batch transaction; messages / panel sync only after that transaction commits
(merged fix e29620c7); a failed batch → one forced admin alert, the next run
renews once.
"""
from datetime import timedelta

import pytest

import auto_renewal
import config
import database.users as db_users
from tests.e2e import flows
from tests.e2e.world import ADMIN, GIB, new_user, utcnow


async def seed_due(e2e, u, tariff: str, period: int = 30, *, balance_rub: float = 5000, hours_left: float = 3):
    await flows.seed_active(e2e, u, flows.base_of(tariff), utcnow() + timedelta(days=5), period=period)
    if tariff.startswith("combo_"):
        # the subscription was bought as combo: set the row like a combo purchase does
        pending = await e2e.create_purchase(u, tariff, period, provider="platega")
        await flows.pay(e2e, u, pending, "sbp")
        await e2e.pool.execute(
            "UPDATE payments SET created_at = created_at - interval '40 days' WHERE telegram_id=$1", u.id)
    await e2e.set_expiry(u.id, utcnow() + timedelta(hours=hours_left))
    await e2e.pool.execute("UPDATE subscriptions SET auto_renew = TRUE WHERE telegram_id=$1", u.id)
    await e2e.pool.execute("UPDATE users SET balance = $2 WHERE telegram_id=$1", u.id, int(balance_rub * 100))


async def run_once(e2e):
    await auto_renewal.process_auto_renewals(e2e.bot)
    await e2e.settle()
    await e2e.provisioning_tick()


def renewal_price(tariff: str, period: int) -> float:
    return flows.tariff_price(tariff, period)


def _autorenew_params():
    out = []
    for flag in ("off", "on"):
        for tariff, period in [("basic", 30), ("plus", 90), ("combo_basic", 30), ("combo_plus", 30)]:
            marks = []
            if flag == "off" and tariff.startswith("combo_"):
                marks = [pytest.mark.xfail(strict=True, reason="legacy flag-off bug T0-AUTORENEW-COMBO "
                                                               "(+10 GB / basic instead of combo GB)")]
            elif flag == "off" and tariff == "plus":
                marks = [pytest.mark.xfail(strict=True, reason="legacy flag-off bug T0-AUTORENEW-PLUS "
                                                               "(Plus renews as Basic)")]
            out.append(pytest.param(flag, tariff, period, marks=marks, id=f"{flag}-{tariff}{period}"))
    return out


@pytest.mark.parametrize("flag,tariff,period", _autorenew_params())
async def test_autorenew_charges_the_real_tariff(e2e, flag, tariff, period):
    u = new_user()
    await seed_due(e2e, u, tariff, period)
    e2e.provisioning(flag, "autorenew" if flag == "on" else None)
    before = await flows.snapshot(e2e, u.id)
    bal = await e2e.balance(u.id)
    mark = e2e.tg.mark()

    await run_once(e2e)

    await flows.check_purchase(e2e, u.id, before, tariff, period)
    assert await e2e.balance(u.id) == pytest.approx(bal - renewal_price(tariff, period))
    assert e2e.user_texts(u.id, mark), "user not told about the auto-renewal"
    assert e2e.admin_texts(mark) == [], e2e.admin_texts(mark)

    # a second pass in the same window renews nothing
    snap2 = (await e2e.sub(u.id))["expires_at"], await e2e.balance(u.id), e2e.panel.bypass_limit(u.id)
    await run_once(e2e)
    assert ((await e2e.sub(u.id))["expires_at"], await e2e.balance(u.id), e2e.panel.bypass_limit(u.id)) == snap2


@pytest.mark.parametrize("flag", ["off", "on"])
async def test_legacy_biz_row_renews_as_plus_at_plus_price(e2e, flag):
    u = new_user()
    await seed_due(e2e, u, "plus", 30)
    await e2e.pool.execute("UPDATE subscriptions SET subscription_type='biz_team' WHERE telegram_id=$1", u.id)
    await e2e.pool.execute("UPDATE payments SET tariff='biz_team_30' WHERE telegram_id=$1", u.id)
    e2e.provisioning(flag, "autorenew" if flag == "on" else None)
    bal = await e2e.balance(u.id)
    before = await flows.snapshot(e2e, u.id)
    await run_once(e2e)
    exp = (await e2e.sub(u.id))["expires_at"]
    assert abs((exp - flows.add_months(before.expires_at, 1)).total_seconds()) < 5
    assert await e2e.balance(u.id) == pytest.approx(bal - config.TARIFFS["plus"][30]["price"])
    assert abs((e2e.panel.premium_expire(u.id) - exp).total_seconds()) <= 1.5


async def test_insufficient_balance_renews_nothing(e2e):
    u = new_user()
    await seed_due(e2e, u, "basic", 30, balance_rub=50)
    before = await flows.snapshot(e2e, u.id)
    mark = e2e.tg.mark()
    await run_once(e2e)
    sub = await e2e.sub(u.id)
    assert sub["expires_at"] == before.expires_at
    assert await e2e.balance(u.id) == 50.0
    assert len(await e2e.payments(u.id)) == before.payments
    assert e2e.admin_texts(mark) == []


async def test_insufficient_balance_tells_once_and_a_top_up_renews_in_the_window(e2e):
    """#5: not enough on the balance → silence, and the attempt burnt the period:
    a top-up an hour later renewed nothing and the subscription ended."""
    from app.i18n import get_text
    u = new_user()
    await seed_due(e2e, u, "basic", 30, balance_rub=150)
    before = await flows.snapshot(e2e, u.id)
    price = renewal_price("basic", 30)
    mark = e2e.tg.mark()

    await run_once(e2e)

    texts = e2e.user_texts(u.id, mark)
    head = get_text("ru", "autorenew.insufficient_balance", amount="", balance="", missing="", deadline="")
    assert len(texts) == 1 and texts[0].split("\n", 1)[0] == head.split("\n", 1)[0], texts
    assert f"{price - 150:g} ₽" in texts[0] and "МСК" in texts[0]
    assert (await e2e.sub(u.id))["last_auto_renewal_at"] is None, "the period's attempt is given back"

    m2 = e2e.tg.mark()
    await run_once(e2e)                                    # still short: told once per period
    assert e2e.user_texts(u.id, m2) == []
    assert (await e2e.sub(u.id))["expires_at"] == before.expires_at

    await e2e.pool.execute("UPDATE users SET balance = $2 WHERE telegram_id=$1", u.id, int(500 * 100))
    await run_once(e2e)                                    # after the top-up, within the window
    await flows.check_purchase(e2e, u.id, before, "basic", 30)
    assert await e2e.balance(u.id) == pytest.approx(500 - price)


async def test_already_expired_subscription_is_not_auto_renewed(e2e):
    u = new_user()
    await seed_due(e2e, u, "basic", 30, hours_left=-1)
    before = await flows.snapshot(e2e, u.id)
    await run_once(e2e)
    assert (await e2e.sub(u.id))["expires_at"] == before.expires_at
    assert await e2e.balance(u.id) == 5000.0


class _CommitFailsOnce:
    """The batch connection; its pre-COMMIT `SELECT 1` fails once — a batch-level
    failure (connection lost / error outside every savepoint)."""

    def __init__(self, conn, state):
        self._conn, self._state = conn, state

    def __getattr__(self, name):
        return getattr(self._conn, name)

    async def execute(self, sql, *args, **kw):
        if sql.strip() == "SELECT 1" and not self._state["fired"]:
            self._state["fired"] = True
            raise ConnectionError("injected: connection lost right before COMMIT")
        return await self._conn.execute(sql, *args, **kw)


async def _two_due(e2e, flag):
    a, b = new_user(), new_user()
    await seed_due(e2e, a, "basic", 30)
    await seed_due(e2e, b, "basic", 30)
    e2e.provisioning(flag, "autorenew" if flag == "on" else None)
    snap = {u.id: (await e2e.sub(u.id))["expires_at"] for u in (a, b)}
    panel = {u.id: e2e.panel.premium_expire(u.id) for u in (a, b)}
    return a, b, snap, panel


async def _renewed_once(e2e, users, snap):
    for u in users:
        exp = (await e2e.sub(u.id))["expires_at"]
        assert abs((exp - flows.add_months(snap[u.id], 1)).total_seconds()) < 5
        assert await e2e.balance(u.id) == pytest.approx(5000.0 - 199.0)
        assert abs((e2e.panel.premium_expire(u.id) - exp).total_seconds()) <= 1.5
        assert len([p for p in await e2e.payments(u.id) if p["purchase_id"] is None]) == 1


@pytest.mark.parametrize("flag", ["off", "on"])
async def test_failed_batch_rolls_back_everything_alerts_once_and_next_run_renews_once(e2e, flag, monkeypatch):
    """Merged fix e29620c7 (N1): nothing of a rolled-back batch is announced or synced."""
    a, b, snap, panel = await _two_due(e2e, flag)
    real_acq = auto_renewal.acquire_connection
    state = {"fired": False}

    def failing_acquire(pool, name):
        cm = real_acq(pool, name)

        class _CM:
            async def __aenter__(self):
                return _CommitFailsOnce(await cm.__aenter__(), state)

            async def __aexit__(self, *exc):
                return await cm.__aexit__(*exc)
        return _CM()

    monkeypatch.setattr(auto_renewal, "acquire_connection", failing_acquire)
    mark = e2e.tg.mark()
    with pytest.raises(ConnectionError):
        await auto_renewal.process_auto_renewals(e2e.bot)
    await e2e.settle()
    await e2e.provisioning_tick()
    assert state["fired"]
    for u in (a, b):
        assert (await e2e.sub(u.id))["expires_at"] == snap[u.id], "renewal survived a rolled-back batch"
        assert await e2e.balance(u.id) == 5000.0
        assert e2e.panel.premium_expire(u.id) == panel[u.id], "panel synced for a rolled-back renewal"
        assert e2e.user_texts(u.id, mark) == [], "announced a renewal that was rolled back"
    alerts = e2e.admin_texts(mark)
    assert len(alerts) == 1 and "rolled back" in alerts[0], alerts

    monkeypatch.setattr(auto_renewal, "acquire_connection", real_acq)
    await run_once(e2e)
    await _renewed_once(e2e, (a, b), snap)


@pytest.mark.parametrize("flag", ["off", "on"])
async def test_one_user_error_is_isolated_alerted_and_retried(e2e, flag, monkeypatch):
    """A per-user error rolls back only that user's savepoint: the others renew,
    the admin gets a forced alert, the next run renews the failed user once."""
    a, b, snap, _ = await _two_due(e2e, flag)
    real = db_users.process_referral_reward

    async def boom(*args, **kw):
        if kw.get("buyer_id") == b.id:
            raise RuntimeError("injected per-user failure")
        return await real(*args, **kw)
    monkeypatch.setattr(db_users, "process_referral_reward", boom)
    mark = e2e.tg.mark()
    await run_once(e2e)
    await _renewed_once(e2e, (a,), snap)
    assert (await e2e.sub(b.id))["expires_at"] == snap[b.id] and await e2e.balance(b.id) == 5000.0
    assert any(str(b.id) in t for t in e2e.admin_texts(mark)), e2e.admin_texts(mark)
    assert e2e.user_texts(b.id, mark) == []

    monkeypatch.setattr(db_users, "process_referral_reward", real)
    await e2e.pool.execute("UPDATE subscriptions SET last_auto_renewal_at = NULL WHERE telegram_id=$1", b.id)
    await run_once(e2e)
    await _renewed_once(e2e, (a, b), snap)
