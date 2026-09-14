"""Scenario 3 — provider callbacks that must NOT credit, or must credit exactly once.

Duplicates / replays, bad signatures (WATA fail-closed → 500), wrong amount /
currency / provider, unknown purchase, promo exhausted between invoice and
payment. HTTP codes per app/api/payment_webhook._STATUS_HTTP; every rejection
must reach the admin chat.
"""
import uuid

import pytest

import database
from tests.e2e import flows
from tests.e2e.world import new_user
from tests.fakes import providers_http as prov

HOOKS = {
    "sbp": lambda pid, amount, tx: prov.platega_webhook(pid, amount, tx_id=tx),
    "wata": lambda pid, amount, tx: prov.wata_webhook(pid, amount, tx_id=tx),
    "crypto": lambda pid, amount, tx: prov.cryptobot_webhook(pid, amount, invoice_id=int(tx[-6:], 16)),
}


async def _pending(e2e, method="sbp", tariff="basic", period=30, **kw):
    u = new_user()
    await e2e.register(u)
    p = await e2e.create_purchase(u, tariff, period, provider=flows.METHOD_PROVIDER[method], **kw)
    return u, p


async def _nothing_credited(e2e, u, p):
    assert await e2e.payments(u.id) == []
    row = await e2e.row("SELECT status FROM pending_purchases WHERE purchase_id=$1", p["purchase_id"])
    assert row["status"] == "pending"
    assert e2e.panel.premium(u.id) is None and e2e.panel.bypass(u.id) is None


@pytest.mark.parametrize("method", ["sbp", "wata", "crypto"])
async def test_replayed_webhook_credits_once(e2e, method):
    u, p = await _pending(e2e, method)
    hook = HOOKS[method](p["purchase_id"], 199.0, uuid.uuid4().hex)
    first = await e2e.webhook(hook)
    assert first.status == 200 and first.body["status"] == "ok", first
    state = (e2e.panel.premium_expire(u.id), e2e.panel.bypass_limit(u.id), (await e2e.sub(u.id))["expires_at"])
    mark = e2e.tg.mark()

    again = await e2e.webhook(hook)
    assert again.status == 200 and again.body["status"] == "already_processed", again
    assert len(await e2e.payments(u.id)) == 1
    assert (e2e.panel.premium_expire(u.id), e2e.panel.bypass_limit(u.id),
            (await e2e.sub(u.id))["expires_at"]) == state
    assert e2e.admin_texts(mark) == []


async def _auth_failure_is_loud(e2e, u, p, res, mark, provider):
    """Merged fix 60960ba1: an auth failure is answered 500 (the provider retries —
    a real paid callback is never lost silently) + forced admin alert + payment_errors."""
    assert res.status == 500, res
    await _nothing_credited(e2e, u, p)
    assert e2e.admin_texts(mark), f"{provider} auth failure was silent"
    assert any(e["payment_provider"] == provider for e in await e2e.payment_errors())


async def test_platega_wrong_secret_is_500_with_alert(e2e):
    u, p = await _pending(e2e, "sbp")
    mark = e2e.tg.mark()
    res = await e2e.webhook(prov.platega_webhook(p["purchase_id"], 199.0, secret="forged"))
    await _auth_failure_is_loud(e2e, u, p, res, mark, "platega")
    ok = await e2e.webhook(prov.platega_webhook(p["purchase_id"], 199.0))   # the genuine retry
    assert ok.body["status"] == "ok" and len(await e2e.payments(u.id)) == 1


async def test_cryptobot_wrong_hmac_is_500_with_alert(e2e):
    u, p = await _pending(e2e, "crypto")
    path, raw, headers = prov.cryptobot_webhook(p["purchase_id"], 199.0)
    headers["crypto-pay-api-signature"] = prov.cryptobot_sign(raw, token="other:token")
    mark = e2e.tg.mark()
    res = await e2e.webhook((path, raw, headers))
    await _auth_failure_is_loud(e2e, u, p, res, mark, "cryptobot")


async def test_platega_paid_callback_without_purchase_id_is_200_with_alert(e2e):
    path, raw, headers = prov.platega_webhook("x", 199.0)
    body = __import__("json").loads(raw)
    body["payload"] = __import__("json").dumps({"something": "else"})
    mark = e2e.tg.mark()
    res = await e2e.webhook((path, __import__("json").dumps(body).encode(), headers))
    assert res.status == 200, res            # a retry cannot add the missing id
    assert e2e.admin_texts(mark), "a paid callback without purchase id was silent"


@pytest.mark.parametrize("variant", ["bad_signature", "unsigned"])
async def test_wata_bad_signature_is_fail_closed_500(e2e, variant):
    u, p = await _pending(e2e, "wata")
    hook = prov.wata_webhook(p["purchase_id"], 199.0, sign=variant != "unsigned",
                             bad_signature=variant == "bad_signature")
    mark = e2e.tg.mark()
    res = await e2e.webhook(hook)
    assert res.status == 500, res          # WATA retries up to 32 h — never 200
    await _nothing_credited(e2e, u, p)
    assert any("X-Signature" in t for t in e2e.admin_texts(mark)), e2e.admin_texts(mark)
    # the genuine, correctly signed callback that follows is credited
    ok = await e2e.webhook(prov.wata_webhook(p["purchase_id"], 199.0))
    assert ok.status == 200 and ok.body["status"] == "ok"
    assert len(await e2e.payments(u.id)) == 1


@pytest.mark.parametrize("method", ["sbp", "wata", "crypto"])
async def test_underpayment_is_rejected_with_admin_alert(e2e, method):
    u, p = await _pending(e2e, method)
    mark = e2e.tg.mark()
    res = await e2e.webhook(HOOKS[method](p["purchase_id"], 100.0, uuid.uuid4().hex))
    assert (res.status, res.body["status"]) == (200, "amount_mismatch"), res
    await _nothing_credited(e2e, u, p)
    assert any("mismatch" in t.lower() for t in e2e.admin_texts(mark)), e2e.admin_texts(mark)


async def test_platega_foreign_currency_is_rejected(e2e):
    u, p = await _pending(e2e, "sbp")
    mark = e2e.tg.mark()
    res = await e2e.webhook(prov.platega_webhook(p["purchase_id"], 199.0, currency="USD"))
    assert (res.status, res.body["status"]) == (200, "rejected"), res
    await _nothing_credited(e2e, u, p)
    assert any("REJECTED" in t for t in e2e.admin_texts(mark)), e2e.admin_texts(mark)


async def test_wata_foreign_currency_is_rejected(e2e):
    u, p = await _pending(e2e, "wata")
    mark = e2e.tg.mark()
    res = await e2e.webhook(prov.wata_webhook(p["purchase_id"], 199.0, currency="USD"))
    assert (res.status, res.body["status"]) == (200, "invalid_currency"), res
    await _nothing_credited(e2e, u, p)
    assert any("invalid_currency" in t for t in e2e.admin_texts(mark)), e2e.admin_texts(mark)


async def test_callback_from_another_provider_is_rejected(e2e):
    """Invoice issued by Platega, a CryptoBot callback for it arrives."""
    u, p = await _pending(e2e, "sbp")
    mark = e2e.tg.mark()
    res = await e2e.webhook(prov.cryptobot_webhook(p["purchase_id"], 199.0))
    assert (res.status, res.body["status"]) == (200, "provider_mismatch"), res
    await _nothing_credited(e2e, u, p)
    assert e2e.admin_texts(mark), "no admin alert on provider mismatch"
    errs = await e2e.payment_errors()
    assert any(e["purchase_id"] == p["purchase_id"] for e in errs), errs


@pytest.mark.parametrize("method", ["sbp", "wata", "crypto"])
async def test_unknown_purchase_is_not_found_with_alert(e2e, method):
    mark = e2e.tg.mark()
    pid = f"purchase_{uuid.uuid4().hex[:16]}"
    res = await e2e.webhook(HOOKS[method](pid, 199.0, uuid.uuid4().hex))
    assert (res.status, res.body["status"]) == (200, "not_found"), res
    assert any(pid in t for t in e2e.admin_texts(mark)), e2e.admin_texts(mark)
    assert any(e["purchase_id"] == pid for e in await e2e.payment_errors())


async def test_promo_exhausted_between_invoice_and_payment_is_honoured(e2e):
    await database.create_promocode_atomic("E2EHALF", 50, 86400, 1, 1)
    u, p = await _pending(e2e, "sbp", price_rub=99.50, promo_code="E2EHALF")
    # someone else used the last slot while the user was paying
    await e2e.pool.execute("UPDATE promo_codes SET used_count = max_uses WHERE code='E2EHALF'")
    before = await flows.snapshot(e2e, u.id)
    mark = e2e.tg.mark()

    res = await e2e.webhook(prov.platega_webhook(p["purchase_id"], 99.50))
    assert (res.status, res.body["status"]) == (200, "ok"), res
    await flows.check_purchase(e2e, u.id, before, "basic", 30)
    assert (await e2e.payments(u.id))[-1]["amount"] == 9_950
    assert any("Promo honoured after payment" in t for t in e2e.admin_texts(mark)), e2e.admin_texts(mark)
    used = await e2e.val("SELECT used_count FROM promo_codes WHERE code='E2EHALF' ORDER BY id DESC LIMIT 1")
    assert used == 1      # CHECK promocodes_used_not_exceed_max: the counter stops at the cap
