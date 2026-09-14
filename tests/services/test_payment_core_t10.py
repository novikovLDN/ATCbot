"""T10 — balance purchases via the provisioning outbox (entrypoint "balance").

Spec: docs/audit/02_payment_core_plan.md §A flow 3, §B, task T10, T0 finding 1.

Real code: payments_callbacks.callback_pay_balance → database.finalize_balance_purchase
→ provisioning.enqueue / run_now → provisioning.apply, and the T5 worker tick.
Fakes: T0 harness World (asyncpg, grant_access contract fake with defer_panel,
i18n recording), tests/fakes/panel.FakePanel (Remnawave, installed over the
harness panel for every function the provisioning core uses) and
tests/fakes/provisioning.FakeJobs (outbox). Every legacy panel entry point
(renew_remnawave_user_bg, add_bypass_traffic, provision_subscription,
sync_renewal_to_remnawave) is replaced by a sentinel that must stay uncalled.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import config
import database
from app.api import payment_webhook
from app.services import admin_alerts, provisioning, purchase_flow, remnawave_service
from app.workers import provisioning_worker as worker
from tests.fakes.panel import FakePanel
from tests.fakes.provisioning import FakeJobs
from tests.services import payment_core_harness as h
from tests.services.payment_core_harness import TG, text_prefix

GIB = 1024 ** 3
BOT = object()
MODE_VAR = f"{config.APP_ENV.upper()}_USE_NEW_PROVISIONING"
EP_VAR = f"{config.APP_ENV.upper()}_NEW_PROVISIONING_ENTRYPOINTS"


def _price_kopecks(tariff="basic", period_days=30, combo=False) -> int:
    table = config.COMBO_TARIFFS[f"combo_{tariff}"] if combo else config.TARIFFS[tariff]
    return table[period_days]["price"] * 100


def _sent_text(mock) -> str:
    args = mock.call_args.args
    return args[1] if len(args) > 1 and isinstance(args[1], str) else args[0]


def _balance_tx_inserts(w):
    return [a for kind, s, a in w.conn.calls if kind == "execute" and "insert into balance_transactions" in s]


def _debits(w):
    return [a for kind, s, a in w.conn.calls if kind == "execute" and s.startswith("update users set balance")]


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv(MODE_VAR, "on")
    monkeypatch.setenv(EP_VAR, "balance")
    w = h.install(monkeypatch)
    w.conn.is_in_transaction = lambda: True  # harness conn = the caller's open tx
    panel = FakePanel().install(monkeypatch)
    jobs = FakeJobs(panel).install(monkeypatch)

    completions = []

    async def complete_activation(tg, *, vpn_key, vpn_key_plus=None, uuid=None, conn=None):
        completions.append(tg)
        if not w.sub or w.sub.get("activation_status") != "pending":
            return False
        w.sub.update(activation_status="active", vpn_key=vpn_key, vpn_key_plus=vpn_key_plus, uuid=uuid)
        return True

    monkeypatch.setattr(database, "complete_activation", complete_activation)
    monkeypatch.setattr(database, "get_remnawave_premium_id", AsyncMock(return_value=None))
    payment_errors = AsyncMock(return_value=1)
    monkeypatch.setattr(database, "log_payment_error", payment_errors)
    alerts = AsyncMock(return_value=True)
    monkeypatch.setattr(admin_alerts, "send_alert", alerts)
    monkeypatch.setattr(payment_webhook, "_bot", BOT)

    legacy = SimpleNamespace(
        renew_bg=MagicMock(), add_bypass=AsyncMock(return_value=True),
        provision=AsyncMock(side_effect=AssertionError("Phase-1 provisioning must not run")),
        sync=AsyncMock(side_effect=AssertionError("legacy renewal sync must not run")),
    )
    monkeypatch.setattr(remnawave_service, "renew_remnawave_user_bg", legacy.renew_bg)
    monkeypatch.setattr(remnawave_service, "add_bypass_traffic", legacy.add_bypass)
    monkeypatch.setattr(purchase_flow, "provision_subscription", legacy.provision)
    monkeypatch.setattr(purchase_flow, "sync_renewal_to_remnawave", legacy.sync)
    yield SimpleNamespace(w=w, panel=panel, jobs=jobs, alerts=alerts, legacy=legacy,
                          completions=completions, payment_errors=payment_errors)
    assert provisioning._USER_LOCKS == {}, "user locks must be released"


def seed_active(e, *, bypass_gb=3, days_left=10, tariff="basic"):
    """Paying user: active sub row + premium and bypass entities in the panel."""
    e.w.seed_active_subscription(TG, tariff=tariff, bypass_gb=bypass_gb, days_left=days_left)
    e.panel.seed_premium(TG, h.aware(e.w.sub["expires_at"]))
    e.panel.seed_bypass(TG, bypass_gb * GIB)


def assert_no_legacy_panel_work(e):
    e.legacy.renew_bg.assert_not_called()
    e.legacy.add_bypass.assert_not_awaited()
    e.legacy.provision.assert_not_awaited()
    e.legacy.sync.assert_not_awaited()
    assert e.w.panel.calls == []          # the harness (legacy) panel is untouched
    assert e.w.background == []
    e.w.db.set_combo_flag.assert_not_awaited()  # set in the billing tx instead


def only_job(e):
    assert len(e.jobs.rows) == 1
    return next(iter(e.jobs.rows.values()))


# ═══════════════════════════════════════════════════════════════════════
# GB / premium per scenario (flag ON)
# ═══════════════════════════════════════════════════════════════════════

async def test_balance_basic_30d_new_adds_10gb_once(env, monkeypatch):
    e = env
    start = e.w.balance_kopecks

    callback = await h.run_balance_purchase(e.w, monkeypatch)

    assert e.panel.bypass_limit(TG) == 10 * GIB
    job = only_job(e)
    assert (job["idempotency_key"], job["source"], job["tariff_key"], job["status"]) == \
        ("balance:501", "balance", "basic", "done")
    assert job["bypass_add_bytes"] == 10 * GIB
    assert job["premium_until"] == h.aware(e.w.sub["expires_at"])
    assert e.panel.premium_expire(TG) >= job["premium_until"].replace(microsecond=0)
    assert e.w.sub["activation_status"] == "active" and e.w.sub["vpn_key"]
    assert e.w.balance_kopecks == start - _price_kopecks()
    assert len(_balance_tx_inserts(e.w)) == 1
    grant = e.w.grant_calls[0]
    assert (grant["defer_panel"], grant["caller_holds_tx"], grant["pre_provisioned"]) == (True, True, None)
    assert "purchase.success_first" in e.w.i18n_keys
    assert _sent_text(callback.message.answer).startswith(text_prefix(e.w, "purchase.success_first"))
    assert_no_legacy_panel_work(e)
    e.w.db.record_traffic_purchase.assert_not_awaited()
    e.alerts.assert_not_awaited()


async def test_balance_plus_90d_new_adds_10gb_and_uses_plus_welcome(env, monkeypatch):
    e = env

    await h.run_balance_purchase(e.w, monkeypatch, tariff="plus", period_days=90)

    assert e.panel.bypass_limit(TG) == 10 * GIB
    assert only_job(e)["tariff_key"] == "plus"
    assert "purchase.success_first" in e.w.i18n_keys
    assert e.w.conn.payment_inserts()[0][1] == "plus_90"
    assert_no_legacy_panel_work(e)


async def test_balance_basic_renewal_adds_10gb_again(env, monkeypatch):
    e = env
    seed_active(e, bypass_gb=3)
    premium_before = e.panel.premium_expire(TG)

    callback = await h.run_balance_purchase(e.w, monkeypatch)

    assert e.panel.bypass_limit(TG) == 13 * GIB
    assert e.panel.premium_expire(TG) > premium_before
    assert e.panel.premium_expire(TG) >= h.aware(e.w.sub["expires_at"]).replace(microsecond=0)
    assert only_job(e)["status"] == "done"
    assert "purchase.success_renewal" in e.w.i18n_keys
    assert _sent_text(callback.message.answer).startswith(
        text_prefix(e.w, "purchase.success_renewal"))
    assert_no_legacy_panel_work(e)


async def test_balance_combo_basic_30d_new_adds_combo_gb_only(env, monkeypatch):
    e = env
    start = e.w.balance_kopecks

    await h.run_balance_purchase(e.w, monkeypatch, combo_gb=75)

    assert e.panel.bypass_limit(TG) == 75 * GIB
    job = only_job(e)
    assert (job["tariff_key"], job["status"], job["context"]["is_combo"]) == ("combo_basic", "done", True)
    assert e.w.balance_kopecks == start - _price_kopecks(combo=True)
    combo_sql = [s for kind, s, _ in e.w.conn.calls
                 if kind == "execute" and s == "update subscriptions set is_combo = true where telegram_id = $1"]
    assert len(combo_sql) == 1                                      # in the billing tx
    e.w.db.record_traffic_purchase.assert_awaited_once_with(TG, 75, 0)   # one ledger row
    assert_no_legacy_panel_work(e)


async def test_balance_combo_basic_30d_renewal_adds_combo_gb_again(env, monkeypatch):
    e = env
    seed_active(e, bypass_gb=3)

    await h.run_balance_purchase(e.w, monkeypatch, combo_gb=75)

    assert e.panel.bypass_limit(TG) == 78 * GIB
    assert only_job(e)["tariff_key"] == "combo_basic"
    e.w.db.record_traffic_purchase.assert_awaited_once_with(TG, 75, 0)
    assert_no_legacy_panel_work(e)


# ═══════════════════════════════════════════════════════════════════════
# Panel down: the purchase succeeds, provisioning is queued
# ═══════════════════════════════════════════════════════════════════════

async def test_panel_down_purchase_succeeds_pending_then_worker_completes(env, monkeypatch):
    e = env
    e.panel.mode = "down"
    start = e.w.balance_kopecks

    callback = await h.run_balance_purchase(e.w, monkeypatch)

    # purchase committed once, user sees the deferred-activation text
    assert e.w.balance_kopecks == start - _price_kopecks()
    assert len(_debits(e.w)) == 1 and len(_balance_tx_inserts(e.w)) == 1
    assert len(e.w.conn.payment_inserts()) == 1
    assert "payment.pending_activation" in e.w.i18n_keys
    assert "errors.payment_processing" not in e.w.i18n_keys
    assert _sent_text(callback.message.answer).startswith(text_prefix(e.w, "payment.pending_activation"))
    assert e.w.sub["activation_status"] == "pending"
    # job queued for retry + forced alert + payment_errors row
    job = only_job(e)
    assert (job["status"], job["attempts"]) == ("pending", 1)
    assert e.alerts.await_count == 1 and e.alerts.await_args.kwargs.get("force") is True
    assert e.alerts.await_args.args[0] is BOT
    assert e.payment_errors.await_args.kwargs["purchase_id"] == "balance:501"
    assert e.panel.bypass_limit(TG) is None

    # the panel recovers → the worker finishes the job, GB added once
    e.panel.mode = "ok"
    e.jobs.make_due(job["id"])
    s = await worker.run_tick(BOT)

    assert (s.processed, s.done) == (1, 1)
    assert job["status"] == "done"
    assert e.panel.bypass_limit(TG) == 10 * GIB
    assert e.w.sub["activation_status"] == "active" and e.w.sub["vpn_key"]
    assert e.w.balance_kopecks == start - _price_kopecks()  # still one debit
    assert_no_legacy_panel_work(e)


# ═══════════════════════════════════════════════════════════════════════
# No money → no job; double tap → one debit, one job
# ═══════════════════════════════════════════════════════════════════════

async def test_insufficient_balance_in_finalize_no_debit_no_job(env):
    e = env
    e.w.balance_kopecks = 100

    with pytest.raises(ValueError, match="Insufficient balance"):
        await database.finalize_balance_purchase(
            telegram_id=TG, tariff_type="basic", period_days=30, amount_rubles=199.0,
        )

    assert e.w.balance_kopecks == 100
    assert _debits(e.w) == [] and _balance_tx_inserts(e.w) == []
    assert e.w.conn.payment_inserts() == []
    assert e.jobs.rows == {} and e.w.grant_calls == []
    assert e.panel.calls == []


async def test_insufficient_balance_in_handler_no_debit_no_job(env, monkeypatch):
    e = env
    e.w.balance_kopecks = 100

    callback = await h.run_balance_purchase(e.w, monkeypatch)

    assert "errors.insufficient_balance" in e.w.i18n_keys
    assert callback.answer.await_args.kwargs.get("show_alert") is True
    assert e.w.balance_kopecks == 100 and _debits(e.w) == []
    assert e.jobs.rows == {} and e.w.grant_calls == []


async def _prepare_shared_session(w, monkeypatch, tariff="basic", period_days=30):
    """Patches of run_balance_purchase + ONE FSM session shared by two taps.
    Every Telegram/DB round-trip yields to the loop, like real I/O."""
    import app.services.automated_notifications as autonotif
    from app.handlers.callbacks import payments_callbacks as pc
    from app.handlers.common.states import PurchaseState

    async def _yield(*_a, **_k):
        await asyncio.sleep(0)

    async def _balance(_tg):
        await asyncio.sleep(0)
        return w.balance_kopecks / 100

    state = h._fsm(TG)
    await state.set_state(PurchaseState.choose_payment_method)
    await state.update_data(tariff_type=tariff, period_days=period_days,
                            final_price_kopecks=_price_kopecks(tariff, period_days), combo_bypass_gb=0)
    monkeypatch.setattr(database, "get_user_balance", _balance, raising=False)
    monkeypatch.setattr(pc, "check_rate_limit", lambda *_a, **_k: (True, None))
    monkeypatch.setattr(pc, "resolve_user_language", AsyncMock(return_value="ru"))
    monkeypatch.setattr(pc, "i18n_get_text", w.recording_get_text)
    monkeypatch.setattr(pc, "get_promo_session", AsyncMock(return_value=None))
    monkeypatch.setattr(pc, "clear_promo_session", AsyncMock())
    monkeypatch.setattr(autonotif, "is_notification_enabled", AsyncMock(return_value=False))
    monkeypatch.setattr(autonotif, "get_notification_text", AsyncMock(return_value=None))
    monkeypatch.setattr(autonotif, "log_notification_send", AsyncMock())

    def tap():
        callback = MagicMock()
        callback.from_user.id = TG
        callback.answer = AsyncMock(side_effect=_yield)
        callback.message.answer = AsyncMock(side_effect=_yield)
        callback.message.delete = AsyncMock(side_effect=_yield)
        return callback

    return pc, state, tap


async def test_double_tap_two_concurrent_callbacks_one_debit_one_job(env, monkeypatch):
    e = env
    start = e.w.balance_kopecks  # enough for several purchases
    pc, state, tap = await _prepare_shared_session(e.w, monkeypatch)
    first, second = tap(), tap()

    await asyncio.gather(pc.callback_pay_balance(first, state), pc.callback_pay_balance(second, state))
    await e.w.drain_background()

    assert e.w.balance_kopecks == start - _price_kopecks()
    assert len(_debits(e.w)) == 1 and len(_balance_tx_inserts(e.w)) == 1
    assert len(e.w.conn.payment_inserts()) == 1
    assert len(e.w.grant_calls) == 1
    assert only_job(e)["status"] == "done"
    assert e.panel.bypass_limit(TG) == 10 * GIB
    assert "errors.session_expired_processing" in e.w.i18n_keys  # the second tap was refused


async def test_double_tap_flag_off_one_debit(env, monkeypatch):
    """Double-tap guard does not depend on the outbox flag (legacy path)."""
    e = env
    monkeypatch.setenv(MODE_VAR, "off")
    start = e.w.balance_kopecks
    pc, state, tap = await _prepare_shared_session(e.w, monkeypatch)
    first, second = tap(), tap()

    await asyncio.gather(pc.callback_pay_balance(first, state), pc.callback_pay_balance(second, state))
    await e.w.drain_background()

    assert e.w.balance_kopecks == start - _price_kopecks()
    assert len(_debits(e.w)) == 1 and len(_balance_tx_inserts(e.w)) == 1
    assert len(e.w.grant_calls) == 1
    assert "errors.session_expired_processing" in e.w.i18n_keys


# ═══════════════════════════════════════════════════════════════════════
# Flag OFF / not listed / shadow → legacy path, no outbox
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("mode,entrypoints", [("off", "balance"), ("on", "webhook,telegram"), ("shadow", "")],
                         ids=["flag_off", "balance_not_listed", "shadow"])
async def test_flag_not_on_for_balance_keeps_legacy_path(monkeypatch, mode, entrypoints):
    monkeypatch.setenv(MODE_VAR, mode)
    monkeypatch.setenv(EP_VAR, entrypoints)
    w = h.install(monkeypatch)
    jobs = FakeJobs(FakePanel()).install(monkeypatch)
    enqueue = AsyncMock(side_effect=AssertionError("outbox must not be used"))
    monkeypatch.setattr(provisioning, "enqueue", enqueue)

    await h.run_balance_purchase(w, monkeypatch)

    assert jobs.rows == {}
    enqueue.assert_not_awaited()
    grant = w.grant_calls[0]
    assert grant["defer_panel"] is False and grant["pre_provisioned"] is not None  # Phase 1 ran
    assert "purchase.success_first" in w.i18n_keys
