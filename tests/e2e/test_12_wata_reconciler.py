"""WATA reconciler — the money path for a LOST webhook (app/workers/wata_reconciler.py).

WATA says the order is Paid, but its callback never reached us. One real
reconciler pass (`_reconcile_iteration`) must finalize it exactly like the
webhook would (same process_confirmed_payment): access granted once, the user
told, no admin noise; a second pass and a late webhook change nothing. No
payment at WATA → nothing. Paid with the wrong amount → not credited, the
admin is alerted once.
"""
import uuid

import pytest

from app.workers import wata_reconciler
from tests.e2e import flows
from tests.e2e.world import new_user
from tests.fakes import providers_http as prov


@pytest.fixture(autouse=True)
def _reconciler_memory(monkeypatch):
    """Per-order 30 s throttle and the mismatch-alert memory are process state."""
    monkeypatch.setattr(wata_reconciler, "_LAST_LOOKUP_AT", {})
    monkeypatch.setattr(wata_reconciler, "_MISMATCH_ALERTED", set())


async def _stale_wata_purchase(e2e, u, *, paid_amount=None, currency="RUB"):
    p = await e2e.create_purchase(u, "basic", 30, provider="wata")
    # older than STALE_THRESHOLD_MIN (2 min): the reconciler only looks at those
    await e2e.pool.execute(
        "UPDATE pending_purchases SET created_at = NOW() - interval '5 minutes' WHERE purchase_id=$1",
        p["purchase_id"])
    if paid_amount is not None:
        e2e.providers.wata_paid[p["purchase_id"]] = {
            "id": str(uuid.uuid4()), "orderId": p["purchase_id"], "status": "Paid",
            "kind": "Payment", "amount": paid_amount, "currency": currency,
        }
    return p


async def _pass(e2e):
    wata_reconciler._LAST_LOOKUP_AT.clear()      # the next real pass is ≥ 2 min later
    await wata_reconciler._reconcile_iteration(e2e.bot)
    await e2e.settle()
    await e2e.provisioning_tick()


@pytest.mark.parametrize("flag", ["off", "on"])
async def test_lost_webhook_is_recovered_once(e2e, flag):
    u = new_user()
    await e2e.register(u)
    e2e.provisioning(flag)
    p = await _stale_wata_purchase(e2e, u, paid_amount=199.0)
    before = await flows.snapshot(e2e, u.id)
    mark = e2e.tg.mark()

    await _pass(e2e)
    await flows.check_purchase(e2e, u.id, before, "basic", 30)
    assert await e2e.val("SELECT status FROM pending_purchases WHERE purchase_id=$1", p["purchase_id"]) == "paid"
    assert e2e.user_texts(u.id, mark), "user not told about the recovered payment"
    assert e2e.admin_texts(mark) == [], e2e.admin_texts(mark)
    state = ((await e2e.sub(u.id))["expires_at"], e2e.panel.bypass_limit(u.id), len(await e2e.payments(u.id)))

    await _pass(e2e)                              # second pass: nothing to do
    late = await e2e.webhook(prov.wata_webhook(p["purchase_id"], 199.0))   # the webhook finally arrives
    assert (late.status, late.body["status"]) == (200, "already_processed"), late
    assert ((await e2e.sub(u.id))["expires_at"], e2e.panel.bypass_limit(u.id),
            len(await e2e.payments(u.id))) == state


async def test_nothing_paid_and_link_still_open_changes_nothing(e2e):
    u = new_user()
    await e2e.register(u)
    p = await _stale_wata_purchase(e2e, u)
    # the payment link is still alive at WATA (GET /links/{id} → 200 Opened)
    e2e.providers.wata_links[p["provider_invoice_id"]] = {"amount": 199.0, "orderId": p["purchase_id"]}
    await _pass(e2e)
    assert await e2e.val("SELECT status FROM pending_purchases WHERE purchase_id=$1", p["purchase_id"]) == "pending"
    assert await e2e.payments(u.id) == [] and await e2e.sub(u.id) is None


async def test_unpaid_link_purged_at_wata_expires_the_purchase(e2e):
    """WATA dropped the link (GET /links/{id} → 404, retention): the pending row is
    expired so the reconciler stops polling it — nothing is credited."""
    u = new_user()
    await e2e.register(u)
    p = await _stale_wata_purchase(e2e, u)
    await _pass(e2e)
    assert await e2e.val("SELECT status FROM pending_purchases WHERE purchase_id=$1", p["purchase_id"]) == "expired"
    assert await e2e.payments(u.id) == [] and await e2e.sub(u.id) is None
    assert e2e.panel.users == {}


@pytest.mark.parametrize("amount,currency", [(100.0, "RUB"), (199.0, "USD")])
async def test_paid_with_wrong_amount_or_currency_is_not_credited_and_alerted_once(e2e, amount, currency):
    u = new_user()
    await e2e.register(u)
    p = await _stale_wata_purchase(e2e, u, paid_amount=amount, currency=currency)
    mark = e2e.tg.mark()
    await _pass(e2e)
    await _pass(e2e)
    assert await e2e.val("SELECT status FROM pending_purchases WHERE purchase_id=$1", p["purchase_id"]) == "pending"
    assert await e2e.payments(u.id) == [] and e2e.panel.premium(u.id) is None
    alerts = [t for t in e2e.admin_texts(mark) if p["purchase_id"] in t]
    assert len(alerts) == 1, e2e.admin_texts(mark)
