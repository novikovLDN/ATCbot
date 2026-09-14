"""Scenario 2 — purchases through the real UI and real provider callbacks.

provider × tariff × state × provisioning flag, the owner's "until 20 Oct +
1 month" example and its panel-failure variant. Every case asserts DB rows,
the panel, the user's messages and the admin chat.
"""
from datetime import timedelta

import pytest

import wata_service
from tests.e2e import flows
from tests.e2e.world import GIB, new_user, utcnow

METHODS = ["sbp", "wata", "crypto", "card", "stars", "balance"]
TARIFFS = [("basic", 30), ("plus", 90), ("combo_basic", 30), ("combo_plus", 30)]
STATES = ["new", "active", "expired"]


async def prepare(e2e, u, state: str, tariff: str) -> None:
    if state == "new":
        await e2e.register(u)
    elif state == "active":
        await flows.seed_active(e2e, u, flows.base_of(tariff), utcnow() + timedelta(days=10))
    else:
        await flows.seed_expired(e2e, u, flows.base_of(tariff))


async def run_purchase(e2e, u, tariff, period, method):
    """UI → invoice → provider callback. Returns (pending, webhook result)."""
    if method == "card":
        # «Банковская карта» opens WATA when WATA is configured; the native
        # Telegram card invoice is the fallback without WATA.
        e2e.mp.setattr(wata_service, "WATA_ACCESS_TOKEN", "")
    if method == "balance":
        need = flows.tariff_price(tariff, period)
        await e2e.pool.execute("UPDATE users SET balance = balance + $2 WHERE telegram_id=$1",
                               u.id, int(need * 100))
    pending = await flows.buy(e2e, u, tariff, period, method)
    if method == "balance":
        return None, None
    assert pending is not None and pending["status"] == "pending", pending
    res = await flows.pay(e2e, u, pending, method)
    return pending, res


# Flag-off cells that are known legacy bugs (docs/audit/03_payment_matrix.md §4);
# the provisioning outbox (flag on) fixes them. T0-TG-NEW-20 / T0-BAL-NEW-20
# (basic/plus, new user) do NOT reproduce end-to-end on the real stack — those
# cells pass here (docs/audit/07_e2e.md §3).
def legacy_bug(method, tariff, state):
    combo = tariff.startswith("combo_")
    if method in ("card", "stars") and state == "new" and combo:
        return "T0-TG-COMBO-NEW"
    if method == "balance" and state == "new" and combo:
        return "T0-BAL-COMBO-NEW"
    # T0-BAL-COMBO-RENEW fixed: the balance path no longer races the base-10 GB
    # background top-up against the Combo top-up (both read-modify-write) — the
    # Combo GB are now exact (the `active` cells and the unit matrix assert it).
    # An EXPIRED Combo renewed from balance may still end with the premium entity
    # DISABLED (forced DELIVERY_MISMATCH alert): pre-existing (bc70d2a2 A/B) and
    # NONDETERMINISTIC (flips between runs of the same code) — likely the expiry
    # path's fire-and-forget disable_premium_user landing after the renewal
    # re-activated premium. Tracked as flaky (non-strict), reported to the owner.
    if method == "balance" and state == "expired" and combo:
        return "E2E-BAL-COMBO-EXPIRED-PREMIUM (pre-existing, flaky race): premium may end DISABLED"
    return None


def matrix_params():
    out = []
    for flag in ("off", "on"):
        for method in METHODS:
            for tariff, period in TARIFFS:
                for state in STATES:
                    marks = []
                    bug = legacy_bug(method, tariff, state) if flag == "off" else None
                    if bug:
                        # A race flips between runs: strict would be red either way.
                        marks.append(pytest.mark.xfail(strict="flaky" not in bug,
                                                       reason=f"legacy flag-off bug {bug}"))
                    out.append(pytest.param(flag, method, tariff, period, state, marks=marks,
                                            id=f"{flag}-{method}-{tariff}{period}-{state}"))
    return out


@pytest.mark.parametrize("flag,method,tariff,period,state", matrix_params())
async def test_purchase(e2e, flag, method, tariff, period, state):
    u = new_user()
    await prepare(e2e, u, state, tariff)
    e2e.provisioning(flag)
    before = await flows.snapshot(e2e, u.id)
    mark = e2e.tg.mark()

    pending, res = await run_purchase(e2e, u, tariff, period, method)
    if res is not None and hasattr(res, "status"):
        assert res.status == 200 and res.body["status"] == "ok", res
    await e2e.settle()
    await e2e.provisioning_tick()

    await flows.check_purchase(e2e, u.id, before, tariff, period)
    assert e2e.user_texts(u.id, mark), "user got no message"
    assert e2e.admin_texts(mark) == [], e2e.admin_texts(mark)
    if pending is not None:
        row = await e2e.row("SELECT status FROM pending_purchases WHERE purchase_id=$1", pending["purchase_id"])
        assert row["status"] == "paid"


async def test_new_user_buys_basic_month_via_platega_sbp(e2e):
    """The whole journey of a brand-new user: /start → captcha → language →
    «Купить» → Basic → 1 month → СБП → Platega CONFIRMED callback."""
    u = new_user()
    await e2e.start_user(u)
    before = await flows.snapshot(e2e, u.id)

    pending = await flows.buy(e2e, u, "basic", 30, "sbp")
    assert pending and pending["payment_provider"] == "platega"
    assert pending["price_kopecks"] == 19_900
    tx = next(iter(e2e.providers.platega_tx.values()))
    assert tx["body"]["paymentDetails"] == {"amount": 199.0, "currency": "RUB"}
    assert tx["body"]["metadata"] == {"userId": str(u.id)}

    mark = e2e.tg.mark()
    res = await flows.pay(e2e, u, pending, "sbp")
    assert res.status == 200 and res.body["status"] == "ok", res

    await flows.check_purchase(e2e, u.id, before, "basic", 30)
    pay = (await e2e.payments(u.id))[-1]
    assert pay["status"] == "approved" and pay["amount"] == 19_900
    assert e2e.user_texts(u.id, mark), "user got no confirmation"
    assert e2e.admin_texts(mark) == [], e2e.admin_texts(mark)
