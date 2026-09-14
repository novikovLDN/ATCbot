"""
T9 — Telegram-native / Stars VPN purchases via the provisioning outbox
(docs/audit/02_payment_core_plan.md §A flow 2, task T9).

T8 moved database.finalize_purchase to the outbox for provider
telegram_payment / telegram_stars (entry point "telegram"), but
payments_messages.process_successful_payment still delivered GB after commit
(renew_remnawave_user_bg, add_bypass_traffic, the FSM combo fallback) → double
grant with the flag ON. T9: PaymentResult carries provisioning_job_id /
provisioning_done, and the handler skips every panel/GB side effect when the
purchase came from the outbox. Notifications keep their i18n keys.

What is real: payments_messages.process_successful_payment,
payment_service.finalize_subscription_payment, database.finalize_purchase,
app.services.provisioning, the provisioning worker tick.
What is faked: asyncpg + grant_access (payment_core_harness), the panel
(tests/fakes/panel.FakePanel), the outbox table (tests/fakes/provisioning.FakeJobs).
Fixtures `env` / `outbox` come from test_payment_core_t8 (flag OFF / flag ON for
webhook+telegram with every legacy panel writer failing loudly).
"""
from __future__ import annotations

import importlib
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call

import pytest

import database
import database.provisioning_jobs as pj
from app.handlers.payments import payments_messages as pm
from app.services.payments import service as payment_service
from app.workers import provisioning_worker
from tests.services import payment_core_harness as h
from tests.services.payment_core_harness import TG, make_pending, text_prefix
from tests.services.test_payment_core_t8 import (  # noqa: F401 — env / outbox are pytest fixtures
    BOT,
    GIB,
    _close,
    _flag,
    assert_no_legacy_panel_writes,
    env,
    outbox,
    seed_active,
    the_job,
)


# ── helpers ─────────────────────────────────────────────────────────────

def spy_results(monkeypatch) -> list:
    """Collect every PaymentResult the handler gets from the payment service."""
    got: list = []
    real = payment_service.finalize_subscription_payment

    async def spy(*a, **kw):
        res = await real(*a, **kw)
        got.append(res)
        return res

    monkeypatch.setattr(payment_service, "finalize_subscription_payment", spy)
    return got


async def run_tg(w, monkeypatch, pending: dict, *, stars: bool = False, fsm_combo_gb: int = 0):
    """Telegram-native successful_payment; `stars=True` pays in XTR (amount = stars)."""
    if not stars:
        return await h.run_telegram_payment(w, monkeypatch, pending, fsm_combo_gb=fsm_combo_gb)
    # Same as payment_core_harness.run_telegram_payment, currency XTR.
    w.pending = pending

    async def get_pending(pid, tg, check_expiry=True):
        return dict(w.pending) if w.pending and w.pending["purchase_id"] == pid else None
    monkeypatch.setattr(database, "get_pending_purchase", get_pending)
    monkeypatch.setattr(pm, "resolve_user_language", AsyncMock(return_value="ru"))
    monkeypatch.setattr(pm, "i18n_get_text", w.recording_get_text)
    monkeypatch.setattr(pm, "clear_promo_session", AsyncMock())
    state = h._fsm(pending["telegram_id"])
    if fsm_combo_gb:
        await state.update_data(combo_bypass_gb=fsm_combo_gb)
    message = MagicMock()
    message.from_user.id = pending["telegram_id"]
    message.message_id = 1
    message.answer = AsyncMock()
    message.successful_payment = SimpleNamespace(
        currency="XTR", total_amount=pending["price_kopecks"] // 100,
        invoice_payload=f"purchase:{pending['purchase_id']}",
        telegram_payment_charge_id="tg-charge-1",
    )
    await pm.process_successful_payment(message, state)
    await w.drain_background()
    return message


def answered_text(message) -> str:
    return message.answer.await_args.args[0]


def traffic_rows_in_tx(w) -> list:
    return [a for kind, s, a in w.conn.calls if kind == "fetchval" and "insert into traffic_purchases" in s]


@pytest.fixture
def tg_only(outbox, monkeypatch):
    """Flag ON for the "telegram" entry point only."""
    _flag(monkeypatch, "on", "telegram")
    return outbox


# ── flag ON: subscription / combo purchases ─────────────────────────────

@pytest.mark.parametrize("stars", [False, True], ids=["rub", "stars"])
async def test_telegram_new_basic_30d_adds_10gb_once(tg_only, monkeypatch, stars):
    w = tg_only
    results = spy_results(monkeypatch)
    before = h.utcnow()

    message = await run_tg(w, monkeypatch, make_pending(), stars=stars)

    job = the_job(w)
    assert (job["idempotency_key"], job["source"], job["status"]) == ("purchase:pid-1", "telegram", "done")
    assert job["context"]["provider"] == ("telegram_stars" if stars else "telegram_payment")
    assert w.panel2.bypass_limit(TG) == 10 * GIB
    assert _close(w.panel2.premium_expire(TG), before + timedelta(days=30))
    # PaymentResult propagation (service.py)
    [res] = results
    assert (res.provisioning_job_id, res.provisioning_done) == (job["id"], True)
    assert res.activation_status == "active" and res.vpn_key
    # notification: same key as the legacy path
    assert "purchase.success_first" in w.i18n_keys
    assert "payment.pending_activation" not in w.i18n_keys
    assert answered_text(message).startswith(text_prefix(w, "purchase.success_first"))
    assert message.answer.await_count == 1
    assert_no_legacy_panel_writes(w)
    w.db.record_traffic_purchase.assert_not_awaited()
    assert traffic_rows_in_tx(w) == []
    # combo flag only from finalize_purchase (cleared for a basic purchase), never from the handler
    assert [c.args[0] for c in w.db.set_combo_flag.await_args_list] == [TG]
    assert not w.db.set_combo_flag.await_args_list[0].args[1]


@pytest.mark.parametrize("fsm_combo_gb", [75, 0], ids=["fsm_kept", "fsm_lost"])
async def test_telegram_combo_basic_30d_new_adds_75gb_exactly_once(tg_only, monkeypatch, fsm_combo_gb):
    w = tg_only

    message = await run_tg(w, monkeypatch, make_pending(is_combo=True), fsm_combo_gb=fsm_combo_gb)

    job = the_job(w)
    assert (job["tariff_key"], job["status"], job["source"]) == ("combo_basic", "done", "telegram")
    assert w.panel2.bypass_limit(TG) == 75 * GIB
    # one Traffic Audit row, written in the billing tx; no second one from the handler
    assert traffic_rows_in_tx(w) == [(TG, 75, 0, "balance")]
    w.db.record_traffic_purchase.assert_not_awaited()
    # combo flag set once, by finalize_purchase
    assert w.db.set_combo_flag.await_args_list == [call(TG, True)]
    assert "purchase.success_first" in w.i18n_keys
    assert answered_text(message).startswith(text_prefix(w, "purchase.success_first"))
    assert_no_legacy_panel_writes(w)


async def test_telegram_renewal_basic_adds_10gb_again(tg_only, monkeypatch):
    w = tg_only
    expires = seed_active(w, bypass_gb=3, days_left=10)

    message = await run_tg(w, monkeypatch, make_pending())

    assert w.panel2.bypass_limit(TG) == 13 * GIB
    assert _close(w.panel2.premium_expire(TG), expires + timedelta(days=30), seconds=2)
    assert the_job(w)["status"] == "done"
    assert "purchase.success_renewal" in w.i18n_keys
    assert answered_text(message).startswith(text_prefix(w, "purchase.success_renewal"))
    assert_no_legacy_panel_writes(w)
    w.db.record_traffic_purchase.assert_not_awaited()


async def test_telegram_renewal_combo_adds_75gb_again(tg_only, monkeypatch):
    w = tg_only
    seed_active(w, bypass_gb=3, is_combo=True)

    message = await run_tg(w, monkeypatch, make_pending(is_combo=True), fsm_combo_gb=75)

    assert w.panel2.bypass_limit(TG) == 78 * GIB
    assert traffic_rows_in_tx(w) == [(TG, 75, 0, "balance")]
    w.db.record_traffic_purchase.assert_not_awaited()
    assert w.db.set_combo_flag.await_args_list == [call(TG, True)]
    assert "purchase.success_renewal" in w.i18n_keys
    assert answered_text(message).startswith(text_prefix(w, "purchase.success_renewal"))
    assert_no_legacy_panel_writes(w)


@pytest.mark.parametrize("stars", [False, True], ids=["rub", "stars"])
async def test_telegram_traffic_pack_adds_pack_gb_once(tg_only, monkeypatch, stars):
    w = tg_only
    expires = seed_active(w, bypass_gb=3)
    premium_before = w.panel2.premium_expire(TG)
    pending = make_pending(purchase_id="tg-pack", tariff="traffic_15gb", period_days=0,
                           purchase_type="traffic_pack", price_rub=89)

    message = await run_tg(w, monkeypatch, pending, stars=stars)

    job = the_job(w)
    assert (job["tariff_key"], job["source"], job["status"]) == ("pack", "telegram", "done")
    assert w.panel2.bypass_limit(TG) == 18 * GIB
    assert w.panel2.premium_expire(TG) == premium_before
    assert w.sub["expires_at"] == h.naive(expires)
    assert "traffic.purchase_success" in w.i18n_keys
    message.answer.assert_not_awaited()  # no errors.* screen
    assert_no_legacy_panel_writes(w)


# ── flag ON: panel down → pending activation, worker completes ──────────

async def test_telegram_panel_down_shows_pending_activation_then_worker_applies_once(tg_only, monkeypatch):
    w = tg_only
    w.panel2.mode = "down"
    results = spy_results(monkeypatch)
    before = h.utcnow()

    message = await run_tg(w, monkeypatch, make_pending())

    job = the_job(w)
    assert (job["status"], job["attempts"]) == ("pending", 1)
    [res] = results
    assert (res.provisioning_job_id, res.provisioning_done) == (job["id"], False)
    assert res.activation_status == "pending" and res.vpn_key is None
    # success accepted; the user sees the pending-activation screen, not keys
    assert answered_text(message).startswith(text_prefix(w, "payment.pending_activation"))
    assert "purchase.success_first" not in w.i18n_keys
    assert "errors.subscription_activation" not in w.i18n_keys
    assert w.panel2.bypass_limit(TG) is None
    assert w.sub["activation_status"] == "pending"
    assert [c.kwargs.get("force") for c in w.alerts.await_args_list] == [True]

    w.panel2.mode = "ok"
    w.jobs.make_due(job["id"])
    stats = await provisioning_worker.run_tick(BOT)

    assert (stats.processed, stats.done) == (1, 1)
    assert w.jobs.job(job["id"])["status"] == "done"
    assert w.panel2.bypass_limit(TG) == 10 * GIB
    assert _close(w.panel2.premium_expire(TG), before + timedelta(days=30))
    assert w.sub["activation_status"] == "active"
    stats = await provisioning_worker.run_tick(BOT)
    assert stats.processed == 0
    assert w.panel2.bypass_limit(TG) == 10 * GIB
    assert_no_legacy_panel_writes(w)


# ── flag ON: replay of an already-processed purchase ────────────────────

async def test_telegram_replay_of_outbox_purchase_adds_no_gb(tg_only, monkeypatch):
    w = tg_only
    await run_tg(w, monkeypatch, make_pending())
    job = the_job(w)
    assert w.panel2.bypass_limit(TG) == 10 * GIB
    # second delivery: the service takes its idempotent "already processed" branch.
    # The handler's charge-id dedup (TG-RT-2) is bypassed on purpose — the charge
    # was not remembered (crash between commit and the record / concurrent copy).
    w.conn._charges.clear()
    monkeypatch.setattr(payment_service, "check_payment_idempotency",
                        AsyncMock(return_value=(True, dict(w.sub))))
    results = spy_results(monkeypatch)
    w.run_now.reset_mock()

    await run_tg(w, monkeypatch, make_pending())

    [res] = results
    assert (res.provisioning_job_id, res.provisioning_done) == (job["id"], True)
    assert w.panel2.bypass_limit(TG) == 10 * GIB
    assert len(w.jobs.rows) == 1
    assert len(w.grant_calls) == 1
    w.run_now.assert_not_awaited()
    assert_no_legacy_panel_writes(w)


async def test_telegram_redelivery_of_a_remembered_charge_is_skipped(tg_only, monkeypatch):
    """TG-RT-2: the same successful_payment re-delivered (same charge id, already
    on the payments row) never reaches the service: no second grant, no GB."""
    w = tg_only
    await run_tg(w, monkeypatch, make_pending())
    assert w.conn._charges == {"tg-charge-1"}
    results = spy_results(monkeypatch)
    w.run_now.reset_mock()

    await run_tg(w, monkeypatch, make_pending())

    assert results == []
    assert len(w.grant_calls) == 1 and len(w.jobs.rows) == 1
    assert w.panel2.bypass_limit(TG) == 10 * GIB
    w.run_now.assert_not_awaited()


# ── shop purchases paid via Telegram Payments: unchanged path ───────────

SHOP_TG_CASES = [
    ("telegram_premium", "premium_3m", "app.handlers.payments.telegram_premium", "send_premium_success"),
    ("telegram_stars", "stars_100", "app.handlers.payments.telegram_stars_purchase", "send_stars_success"),
    ("steam", "steam_500", "app.handlers.payments.steam_purchase", "send_steam_success"),
    ("apple_id", "apple_id_usa_25", "app.handlers.callbacks.navigation", "send_apple_id_success"),
]


@pytest.mark.parametrize("mode", ["off", "on"])
@pytest.mark.parametrize("purchase_type,tariff,module_path,sender", SHOP_TG_CASES,
                         ids=[c[0] for c in SHOP_TG_CASES])
async def test_shop_purchase_via_telegram_payments_is_unchanged(outbox, monkeypatch, mode, purchase_type,
                                                                tariff, module_path, sender):
    w = outbox
    _flag(monkeypatch, mode, "webhook,telegram")
    send_success = AsyncMock()
    monkeypatch.setattr(importlib.import_module(module_path), sender, send_success)
    mark_paid = AsyncMock(return_value=True)
    monkeypatch.setattr(database, "mark_pending_purchase_paid", mark_paid)
    results = spy_results(monkeypatch)
    pending = make_pending(purchase_id="shop-1", tariff=tariff, period_days=0,
                           purchase_type=purchase_type, price_rub=500)

    message = await run_tg(w, monkeypatch, pending)

    mark_paid.assert_awaited_once_with("shop-1")
    send_success.assert_awaited_once()
    if purchase_type == "apple_id":
        assert send_success.await_args.args == (message.bot, TG, "usa", 25, 500.0)
    else:
        assert send_success.await_args.args == (message.bot, TG, "shop-1", pending)
    message.answer.assert_not_awaited()
    assert results == [] and w.finalize_results == []
    assert w.jobs.rows == {} and w.grant_calls == []
    assert w.panel2.calls == [] and not w.panel.touched
    assert_no_legacy_panel_writes(w)


@pytest.mark.parametrize("mode", ["off", "on"])
async def test_spotify_via_telegram_card_never_enters_the_outbox(env, monkeypatch, mode):
    """Spotify (spotify_purchase.cb_pay_card) sends a Telegram invoice. It used to
    have no shop branch in process_successful_payment and fell through to the VPN
    path (legacy finalize_purchase, since its tariff is not in the catalog); the
    old assertions only pinned "no job" and held vacuously. Now it has its own
    shop branch (fix(shop), see test_spotify_telegram_payment.py): mark paid +
    send_spotify_success, never the VPN path, so never the outbox."""
    w = env
    _flag(monkeypatch, mode, "webhook,telegram")
    results = spy_results(monkeypatch)
    mark_paid = AsyncMock(return_value=True)
    monkeypatch.setattr(database, "mark_pending_purchase_paid", mark_paid)
    send_success = AsyncMock()
    monkeypatch.setattr(importlib.import_module("app.handlers.payments.spotify_purchase"),
                        "send_spotify_success", send_success)
    pending = make_pending(purchase_id="spot-1", tariff="spotify_individual_1", period_days=30,
                           purchase_type="spotify", price_rub=500)

    await run_tg(w, monkeypatch, pending)

    mark_paid.assert_awaited_once_with("spot-1")
    send_success.assert_awaited_once()
    assert w.jobs.rows == {}
    assert results == [] and w.grant_calls == []


# ── both entry points ON: exactly the target GB end to end ──────────────

async def test_webhook_and_telegram_on_together_grant_target_gb_once(outbox, monkeypatch):
    """NEW_PROVISIONING_ENTRYPOINTS=webhook,telegram (the planned first rollout
    step): a Telegram combo purchase then a webhook basic renewal for the same
    user give 75 + 10 GB, nothing more, from two jobs and zero legacy writes."""
    w = outbox
    _flag(monkeypatch, "on", "webhook,telegram")

    await run_tg(w, monkeypatch, make_pending(purchase_id="tg-combo", is_combo=True), fsm_combo_gb=75)
    assert w.panel2.bypass_limit(TG) == 75 * GIB

    out = await h.run_webhook(w, monkeypatch, make_pending(purchase_id="wh-basic"))

    assert out["result"] == {"status": "ok"}
    assert w.panel2.bypass_limit(TG) == 85 * GIB
    rows = sorted(w.jobs.rows.values(), key=lambda r: r["id"])
    assert [(r["idempotency_key"], r["source"], r["status"]) for r in rows] == [
        ("purchase:tg-combo", "telegram", "done"),
        ("purchase:wh-basic", "webhook", "done"),
    ]
    assert traffic_rows_in_tx(w) == [(TG, 75, 0, "balance")]
    w.db.record_traffic_purchase.assert_not_awaited()
    assert_no_legacy_panel_writes(w)


# ── flag OFF / shadow: byte-identical legacy behaviour ──────────────────

@pytest.mark.parametrize("mode", ["off", "shadow"])
async def test_flag_off_telegram_new_basic_keeps_legacy_path(env, monkeypatch, mode):
    """Legacy numbers are T0's (test_payment_core_characterization xfail docstrings):
    new basic via Telegram = fresh 10 + renew_remnawave_user_bg 10."""
    w = env
    _flag(monkeypatch, mode)
    results = spy_results(monkeypatch)

    message = await run_tg(w, monkeypatch, make_pending())

    [res] = results
    assert (res.provisioning_job_id, res.provisioning_done) == (None, None)
    assert w.jobs.rows == {}
    assert w.grant_calls[0]["defer_panel"] is False
    assert w.panel.added_gb(TG) == 20
    assert answered_text(message).startswith(text_prefix(w, "purchase.success_first"))


@pytest.mark.parametrize("fsm_combo_gb", [75, 0], ids=["fsm_kept", "fsm_lost"])
async def test_flag_off_telegram_combo_new_keeps_handler_top_up(env, monkeypatch, fsm_combo_gb):
    """T0 legacy: fresh 75 + handler add_bypass_traffic 75 (incl. FSM fallback),
    record_traffic_purchase + set_combo_flag from the handler."""
    w = env

    await run_tg(w, monkeypatch, make_pending(is_combo=True), fsm_combo_gb=fsm_combo_gb)

    assert w.jobs.rows == {}
    assert w.panel.added_gb(TG) == 150
    w.db.record_traffic_purchase.assert_awaited_once_with(TG, 75, 0)
    assert call(TG, True) in w.db.set_combo_flag.await_args_list
    assert traffic_rows_in_tx(w) == []


async def test_flag_off_telegram_renewal_and_replay_do_no_outbox_lookup(env, monkeypatch):
    w = env
    w.seed_active_subscription(bypass_gb=3)
    get_by_key = AsyncMock(side_effect=AssertionError("no outbox lookup with the flag off"))
    monkeypatch.setattr(pj, "get_by_key", get_by_key)

    await run_tg(w, monkeypatch, make_pending())
    assert w.panel.added_gb(TG) == 10
    w.conn._charges.clear()   # reach the service replay branch (see the test above)
    monkeypatch.setattr(payment_service, "check_payment_idempotency",
                        AsyncMock(return_value=(True, dict(w.sub))))
    results = spy_results(monkeypatch)

    await run_tg(w, monkeypatch, make_pending())

    [res] = results
    assert (res.provisioning_job_id, res.provisioning_done) == (None, None)
    get_by_key.assert_not_awaited()
    assert w.jobs.rows == {}
