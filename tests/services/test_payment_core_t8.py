"""
T8 — external-webhook / Telegram finalize_purchase via the provisioning outbox
(docs/audit/02_payment_core_plan.md §A flows 1-2, §B, §D, task T8).

Flag ON (USE_NEW_PROVISIONING=on, entry point "webhook" / "telegram"):
  finalize_purchase → grant_access(defer_panel=True) + promo + referral (+ combo
  traffic_purchases row) + provisioning job "purchase:{id}" in ONE transaction;
  after commit provisioning.run_now. confirmation only notifies the user.

What is real: confirmation.process_confirmed_payment, database.finalize_purchase,
app.services.provisioning (enqueue / run_now / apply), the provisioning worker tick.
What is faked: asyncpg + grant_access (tests/services/payment_core_harness.py),
the Remnawave panel (tests/fakes/panel.FakePanel) and the outbox table
(tests/fakes/provisioning.FakeJobs).

Flag OFF is pinned by the T0 suite (test_payment_core_characterization.py) —
a few flows are re-asserted here to show no job is ever written.
"""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

import config
import database
import database.provisioning_jobs as pj
from app.api import payment_webhook
from app.services import (
    admin_alerts, provisioning, purchase_flow, remnawave_bypass, remnawave_service, tariffs,
)
from app.workers import provisioning_worker
from database import subscriptions as subs
from tests.fakes.panel import FakePanel
from tests.fakes.provisioning import FakeJobs
from tests.services import payment_core_harness as h
from tests.services.payment_core_harness import TG, make_pending, text_prefix

GIB = 1024 ** 3
MODE_VAR = f"{config.APP_ENV.upper()}_USE_NEW_PROVISIONING"
EP_VAR = f"{config.APP_ENV.upper()}_NEW_PROVISIONING_ENTRYPOINTS"
BOT = object()


# ── fixtures ────────────────────────────────────────────────────────────

class _TrackingTx:
    """Harness transaction that records whether the conn is inside it."""

    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        self.conn._in_tx = True
        return self

    async def __aexit__(self, *exc):
        self.conn._in_tx = False
        return False


def _flag(monkeypatch, mode: str, entrypoints: str = "") -> None:
    monkeypatch.setenv(MODE_VAR, mode)
    if entrypoints:
        monkeypatch.setenv(EP_VAR, entrypoints)
    else:
        monkeypatch.delenv(EP_VAR, raising=False)


@pytest.fixture
def env(monkeypatch):
    """Harness world (flag OFF by default) + in-memory outbox + tracked tx."""
    monkeypatch.setenv(MODE_VAR, "off")
    monkeypatch.delenv(EP_VAR, raising=False)
    w = h.install(monkeypatch)
    w.conn._in_tx = False
    w.conn.is_in_transaction = lambda: w.conn._in_tx
    w.conn.transaction = lambda: _TrackingTx(w.conn)
    jobs = FakeJobs(FakePanel()).install(monkeypatch)
    w.jobs = jobs
    w.legacy = {
        "provision_subscription": AsyncMock(side_effect=AssertionError("legacy provision_subscription")),
        "sync_renewal_to_remnawave": AsyncMock(side_effect=AssertionError("legacy sync_renewal")),
        "add_bypass_traffic": AsyncMock(side_effect=AssertionError("legacy add_bypass_traffic")),
        "renew_remnawave_user_bg": MagicMock(side_effect=AssertionError("legacy renew_bg")),
    }
    return w


@pytest.fixture
def outbox(env, monkeypatch):
    """Flag ON for webhook + telegram, FakePanel for the provisioning core,
    every legacy panel writer made to fail loudly."""
    w = env
    _flag(monkeypatch, "on", "webhook,telegram")
    panel = FakePanel().install(monkeypatch)
    w.panel2 = panel
    w.jobs.panel = panel
    monkeypatch.setattr(purchase_flow, "provision_subscription", w.legacy["provision_subscription"])
    monkeypatch.setattr(purchase_flow, "sync_renewal_to_remnawave", w.legacy["sync_renewal_to_remnawave"])
    monkeypatch.setattr(remnawave_bypass, "add_bypass_traffic", w.legacy["add_bypass_traffic"])
    monkeypatch.setattr(remnawave_service, "add_bypass_traffic", w.legacy["add_bypass_traffic"], raising=False)
    monkeypatch.setattr(remnawave_service, "renew_remnawave_user_bg", w.legacy["renew_remnawave_user_bg"],
                        raising=False)

    async def complete_activation(tg, *, vpn_key, vpn_key_plus, uuid=None, conn=None):
        if not w.sub or w.sub.get("activation_status") != "pending":
            return False
        w.sub.update(activation_status="active", vpn_key=vpn_key,
                     vpn_key_plus=vpn_key_plus or w.sub.get("vpn_key_plus"),
                     uuid=uuid or w.sub.get("uuid"))
        return True

    w.payment_errors = []

    async def log_payment_error(**kw):
        w.payment_errors.append(kw)

    monkeypatch.setattr(database, "complete_activation", complete_activation, raising=False)
    monkeypatch.setattr(database, "get_remnawave_premium_id", AsyncMock(return_value=None), raising=False)
    monkeypatch.setattr(database, "log_payment_error", log_payment_error, raising=False)
    w.alerts = AsyncMock(return_value=True)
    monkeypatch.setattr(admin_alerts, "send_alert", w.alerts)
    w.payment_failure_alert = AsyncMock()
    monkeypatch.setattr(admin_alerts, "alert_payment_failure", w.payment_failure_alert)
    monkeypatch.setattr(payment_webhook, "_bot", BOT)

    real_run_now = provisioning.run_now
    w.run_now = AsyncMock(side_effect=real_run_now)
    monkeypatch.setattr(provisioning, "run_now", w.run_now)

    real_finalize = database.finalize_purchase
    w.finalize_results = []

    async def finalize_spy(*a, **kw):
        res = await real_finalize(*a, **kw)
        w.finalize_results.append(res)
        return res

    monkeypatch.setattr(database, "finalize_purchase", finalize_spy)
    yield w
    assert provisioning._USER_LOCKS == {}, "user locks must be released"


def seed_active(w, *, bypass_gb=3, days_left=10, tariff="basic", is_combo=False):
    """Active paying user in the DB and in the FakePanel (premium + bypass)."""
    expires = h.utcnow().replace(microsecond=0) + timedelta(days=days_left)
    w.panel2.seed_premium(TG, expires)
    w.panel2.seed_bypass(TG, bypass_gb * GIB)
    w.sub = {
        "telegram_id": TG, "status": "active", "uuid": "11111111-2222-3333-4444-555555555555",
        "vpn_key": "https://panel.test/sub/prem", "vpn_key_plus": "https://panel.test/sub/bp",
        "expires_at": h.naive(expires), "subscription_type": tariff,
        "activation_status": "active", "auto_renew": True, "is_bypass_only": False,
        "is_combo": is_combo,
    }
    return expires


def the_job(w):
    assert len(w.jobs.rows) == 1, f"expected exactly one provisioning job, got {len(w.jobs.rows)}"
    return next(iter(w.jobs.rows.values()))


def sent_text(bot) -> str:
    args = bot.send_message.call_args.args
    return args[1]


def _close(a, b, seconds=120) -> bool:
    return abs((a - b).total_seconds()) <= seconds


def assert_no_legacy_panel_writes(w):
    for name, mock in w.legacy.items():
        assert not mock.called, f"legacy {name} must not run under the outbox flag"


# ── entry point derivation / catalog resolution ─────────────────────────

@pytest.mark.parametrize("provider,ep", [
    ("platega", "webhook"), ("wata", "webhook"), ("cryptobot", "webhook"), (None, "webhook"),
    ("telegram_payment", "telegram"), ("telegram_stars", "telegram"), ("TELEGRAM_STARS", "telegram"),
])
def test_provisioning_entrypoint_is_derived_from_payment_provider(provider, ep):
    assert subs.provisioning_entrypoint(provider) == ep


def test_outbox_entitlement_resolves_catalog_and_falls_back_to_legacy_on_unknown(monkeypatch):
    _flag(monkeypatch, "on", "webhook")
    kw = dict(purchase_id="p", is_combo=False, is_traffic_pack=False)
    assert subs._outbox_entitlement("webhook", tariff_type="basic", period_days=30, **kw) \
        == tariffs.for_purchase("basic", 30)
    assert subs._outbox_entitlement("webhook", tariff_type="basic", period_days=30,
                                    purchase_id="p", is_combo=True, is_traffic_pack=False).bypass_bytes \
        == 75 * GIB
    assert subs._outbox_entitlement("webhook", tariff_type="traffic_15gb", period_days=0,
                                    purchase_id="p", is_combo=False, is_traffic_pack=True) \
        == tariffs.for_pack(15)
    # legacy business tariff → Plus entitlement (tariffs.normalize_tier)
    assert subs._outbox_entitlement("webhook", tariff_type="biz_team", period_days=30, **kw) \
        == tariffs.for_purchase("plus", 30)
    # not in the catalog → legacy path, never a failed paid purchase
    assert subs._outbox_entitlement("webhook", tariff_type="gold", period_days=30, **kw) is None
    assert subs._outbox_entitlement("webhook", tariff_type="traffic_7gb", period_days=0,
                                    purchase_id="p", is_combo=False, is_traffic_pack=True) is None
    # entry point not enabled → legacy
    assert subs._outbox_entitlement("telegram", tariff_type="basic", period_days=30, **kw) is None


# ── flag ON: webhook purchases ──────────────────────────────────────────

async def test_webhook_basic_30d_new_adds_10gb_once_and_30d_premium(outbox, monkeypatch):
    w = outbox
    before = h.utcnow()

    out = await h.run_webhook(w, monkeypatch, make_pending())

    assert out["result"] == {"status": "ok"}
    job = the_job(w)
    assert job["idempotency_key"] == "purchase:pid-1"
    assert job["source"] == "webhook"
    assert job["status"] == "done"
    assert job["context"]["provider"] == "platega"
    assert job["context"]["purchase_id"] == "pid-1"
    assert job["context"]["amount"] == 199.0
    # enqueued inside the billing transaction, next to the payment
    assert ("insert_job", "purchase:pid-1", True) in w.conn.calls
    assert w.panel2.bypass_limit(TG) == 10 * GIB
    assert _close(w.panel2.premium_expire(TG), before + timedelta(days=30))
    # DB-only grant in the caller's tx; the job completed the deferred activation
    assert w.grant_calls == [{**w.grant_calls[0], "defer_panel": True, "caller_holds_tx": True,
                              "pre_provisioned": None}]
    assert w.sub["activation_status"] == "active" and w.sub["vpn_key"]
    res = w.finalize_results[-1]
    assert res["provisioning_job_id"] == job["id"] and res["provisioning_done"] is True
    assert {"success", "payment_id", "expires_at", "vpn_key", "is_renewal", "subscription_type",
            "referral_reward", "is_combo", "period_days", "bypass_created_fresh"} <= set(res)
    # user notification: same key as the legacy path, no pending text
    assert "purchase.success_first" in w.i18n_keys
    assert "payment.pending_activation" not in w.i18n_keys
    assert_no_legacy_panel_writes(w)
    w.db.record_traffic_purchase.assert_not_awaited()
    w.db.process_referral_reward.assert_awaited_once()
    w.alerts.assert_not_awaited()


async def test_webhook_combo_basic_30d_new_adds_75gb_without_extra_10(outbox, monkeypatch):
    w = outbox

    await h.run_webhook(w, monkeypatch, make_pending(is_combo=True))

    job = the_job(w)
    assert job["tariff_key"] == "combo_basic" and job["status"] == "done"
    assert w.panel2.bypass_limit(TG) == 75 * GIB
    # Traffic Audit row now in the billing transaction (was post-commit)
    tp = [a for kind, s, a in w.conn.calls if kind == "fetchval" and "insert into traffic_purchases" in s]
    assert tp == [(TG, 75, 0, "balance")]
    w.db.record_traffic_purchase.assert_not_awaited()
    assert_no_legacy_panel_writes(w)


async def test_webhook_basic_renewal_adds_10gb_again_and_extends_premium(outbox, monkeypatch):
    w = outbox
    expires = seed_active(w, bypass_gb=3, days_left=10)

    out = await h.run_webhook(w, monkeypatch, make_pending())

    assert out["result"] == {"status": "ok"}
    assert w.panel2.bypass_limit(TG) == 13 * GIB
    assert _close(w.panel2.premium_expire(TG), expires + timedelta(days=30), seconds=2)
    assert the_job(w)["status"] == "done"
    assert w.finalize_results[-1]["is_renewal"] is True
    assert "purchase.success_renewal" in w.i18n_keys
    assert_no_legacy_panel_writes(w)


async def test_webhook_combo_renewal_adds_75gb_again(outbox, monkeypatch):
    w = outbox
    seed_active(w, bypass_gb=3, is_combo=True)

    await h.run_webhook(w, monkeypatch, make_pending(is_combo=True))

    assert w.panel2.bypass_limit(TG) == 78 * GIB
    assert_no_legacy_panel_writes(w)


async def test_webhook_traffic_pack_15_adds_15gb_premium_untouched(outbox, monkeypatch):
    w = outbox
    expires = seed_active(w, bypass_gb=3)
    premium_before = w.panel2.premium_expire(TG)
    pending = make_pending(tariff="traffic_15gb", period_days=0, purchase_type="traffic_pack", price_rub=89)

    out = await h.run_webhook(w, monkeypatch, pending)

    assert out["result"] == {"status": "ok"}
    job = the_job(w)
    assert (job["tariff_key"], job["premium_until"], job["status"]) == ("pack", None, "done")
    assert w.panel2.bypass_limit(TG) == 18 * GIB
    assert w.panel2.premium_expire(TG) == premium_before
    assert w.sub["expires_at"] == h.naive(expires)
    assert w.grant_calls == []
    text = sent_text(out["bot"])
    assert text.startswith(text_prefix(w, "traffic.purchase_success"))
    assert "задерживается" not in text
    assert_no_legacy_panel_writes(w)


async def test_duplicate_webhook_creates_no_second_job_and_no_gb_change(outbox, monkeypatch):
    w = outbox
    await h.run_webhook(w, monkeypatch, make_pending())
    assert w.panel2.bypass_limit(TG) == 10 * GIB
    w.pending["status"] = "paid"  # the first finalize committed
    w.run_now.reset_mock()

    out = await h.run_webhook(w, monkeypatch, make_pending() | {"status": "paid"})

    assert out["result"] == {"status": "already_processed"}
    assert the_job(w)["status"] == "done"
    assert w.panel2.bypass_limit(TG) == 10 * GIB
    assert len(w.grant_calls) == 1
    w.run_now.assert_not_awaited()  # job already done → nothing
    out["bot"].send_message.assert_not_awaited()
    assert_no_legacy_panel_writes(w)


async def test_panel_down_during_webhook_returns_ok_then_worker_applies_gb_once(outbox, monkeypatch):
    w = outbox
    w.panel2.mode = "down"
    before = h.utcnow()

    out = await h.run_webhook(w, monkeypatch, make_pending())

    # billing committed → 200 ok, no Transient / 5xx, the failure is the queue's
    assert out["result"] == {"status": "ok"}
    assert payment_webhook._STATUS_HTTP["ok"] == 200
    w.payment_failure_alert.assert_not_awaited()
    job = the_job(w)
    assert (job["status"], job["attempts"]) == ("pending", 1)
    assert [c.kwargs.get("force") for c in w.alerts.await_args_list] == [True]
    assert [e["error_code"] for e in w.payment_errors] == ["retry"]
    assert w.panel2.bypass_limit(TG) is None
    # new issuance not active yet → pending-activation text, not links
    assert w.finalize_results[-1]["provisioning_done"] is False
    assert sent_text(out["bot"]).startswith(text_prefix(w, "payment.pending_activation"))
    assert "purchase.success_first" not in w.i18n_keys
    assert w.sub["activation_status"] == "pending"

    # panel back; backoff elapsed; worker tick completes the job
    w.panel2.mode = "ok"
    w.jobs.make_due(job["id"])
    stats = await provisioning_worker.run_tick(BOT)

    assert (stats.processed, stats.done) == (1, 1)
    assert w.jobs.job(job["id"])["status"] == "done"
    assert w.panel2.bypass_limit(TG) == 10 * GIB
    assert _close(w.panel2.premium_expire(TG), before + timedelta(days=30))
    assert w.sub["activation_status"] == "active"
    assert len(w.alerts.await_args_list) == 1  # forced alert once
    # another tick changes nothing
    stats = await provisioning_worker.run_tick(BOT)
    assert stats.processed == 0
    assert w.panel2.bypass_limit(TG) == 10 * GIB
    assert_no_legacy_panel_writes(w)


async def test_replay_while_job_pending_runs_job_not_provision_subscription(outbox, monkeypatch):
    w = outbox
    w.panel2.mode = "down"
    await h.run_webhook(w, monkeypatch, make_pending())
    job_id = the_job(w)["id"]
    assert w.jobs.job(job_id)["status"] == "pending"
    w.pending["status"] = "paid"
    w.panel2.mode = "ok"
    w.run_now.reset_mock()

    out = await h.run_webhook(w, monkeypatch, make_pending() | {"status": "paid"})

    assert out["result"] == {"status": "already_processed"}
    w.run_now.assert_awaited_once()
    assert w.run_now.await_args.args == (job_id,)
    w.legacy["provision_subscription"].assert_not_awaited()
    assert w.jobs.job(job_id)["status"] == "done"
    assert w.panel2.bypass_limit(TG) == 10 * GIB
    assert len(w.jobs.rows) == 1


@pytest.mark.parametrize("purchase_type,tariff,module_path,sender", [
    ("telegram_premium", "premium_3m", "app.handlers.payments.telegram_premium", "send_premium_success"),
    ("steam", "steam_500", "app.handlers.payments.steam_purchase", "send_steam_success"),
])
async def test_shop_purchase_under_flag_on_keeps_shop_branch(outbox, monkeypatch, purchase_type, tariff,
                                                             module_path, sender):
    import importlib
    w = outbox
    send_success = AsyncMock()
    monkeypatch.setattr(importlib.import_module(module_path), sender, send_success)
    mark_paid = AsyncMock(return_value=True)
    monkeypatch.setattr(database, "mark_pending_purchase_paid", mark_paid)
    pending = make_pending(purchase_id="shop-1", tariff=tariff, period_days=0,
                           purchase_type=purchase_type, price_rub=500)

    out = await h.run_webhook(w, monkeypatch, pending)

    assert out["result"]["status"] == "ok"
    mark_paid.assert_awaited_once_with("shop-1")
    send_success.assert_awaited_once_with(out["bot"], TG, "shop-1", pending)
    assert w.finalize_results == []
    assert w.jobs.rows == {}
    assert w.panel2.calls == []


async def test_balance_topup_under_flag_on_is_unchanged(outbox, monkeypatch):
    w = outbox
    increase = AsyncMock(return_value=True)
    monkeypatch.setattr("database.users.increase_balance", increase)
    pending = make_pending(purchase_id="topup-1", tariff=None, period_days=0,
                           purchase_type="balance_topup", price_rub=199)

    out = await h.run_webhook(w, monkeypatch, pending)

    assert out["result"] == {"status": "ok"}
    res = w.finalize_results[-1]
    assert res["is_balance_topup"] is True and "provisioning_job_id" not in res
    assert increase.await_args.kwargs["amount"] == 199.0
    assert w.jobs.rows == {} and w.grant_calls == []
    assert w.panel2.calls == []
    w.run_now.assert_not_awaited()
    assert "main.balance_topup_success" in w.i18n_keys


# ── flag ON: Telegram-native entry point ────────────────────────────────

async def test_telegram_provider_uses_telegram_entrypoint_and_source(outbox, monkeypatch):
    w = outbox
    _flag(monkeypatch, "on", "telegram")
    w.pending = make_pending(purchase_id="tg-1")

    res = await subs.finalize_purchase("tg-1", "telegram_stars", 199.0)

    job = the_job(w)
    assert (job["source"], job["idempotency_key"], job["status"]) == ("telegram", "purchase:tg-1", "done")
    assert res["provisioning_job_id"] == job["id"]
    assert w.panel2.bypass_limit(TG) == 10 * GIB


async def test_telegram_traffic_pack_flag_on_adds_pack_gb_once(outbox, monkeypatch):
    """payments_messages passes no job id to _handle_traffic_pack_confirmation;
    under the flag it finds the job by key and does not deliver GB itself."""
    w = outbox
    seed_active(w, bypass_gb=3)
    pending = make_pending(purchase_id="tg-pack", tariff="traffic_15gb", period_days=0,
                           purchase_type="traffic_pack", price_rub=89)

    message = await h.run_telegram_payment(w, monkeypatch, pending)

    job = the_job(w)
    assert (job["source"], job["status"]) == ("telegram", "done")
    assert w.panel2.bypass_limit(TG) == 18 * GIB
    assert "traffic.purchase_success" in w.i18n_keys
    assert message.answer.await_count == 0  # no errors.* screen
    assert_no_legacy_panel_writes(w)


async def test_webhook_only_flag_leaves_telegram_provider_on_legacy_path(outbox, monkeypatch):
    w = outbox
    _flag(monkeypatch, "on", "webhook")
    assert subs._outbox_entitlement("telegram", purchase_id="x", tariff_type="basic", period_days=30,
                                    is_combo=False, is_traffic_pack=False) is None
    w.pending = make_pending(purchase_id="tg-legacy", tariff="traffic_15gb", period_days=0,
                             purchase_type="traffic_pack", price_rub=89)

    res = await subs.finalize_purchase("tg-legacy", "telegram_payment", 89.0)

    assert "provisioning_job_id" not in res
    assert w.jobs.rows == {}


# ── flag OFF / shadow: no outbox, T0 behaviour (legacy panel = harness panel) ──

@pytest.mark.parametrize("mode", ["off", "shadow"])
async def test_flag_off_or_shadow_webhook_new_basic_is_legacy(env, monkeypatch, mode):
    w = env
    _flag(monkeypatch, mode)

    out = await h.run_webhook(w, monkeypatch, make_pending())

    assert out["result"] == {"status": "ok"}
    assert w.panel.added_gb(TG) == 10               # T0 value, legacy Phase 1
    assert w.jobs.rows == {}
    assert w.grant_calls[0]["defer_panel"] is False
    assert w.grant_calls[0]["pre_provisioned"] is not None


async def test_flag_off_webhook_renewal_and_combo_are_legacy(env, monkeypatch):
    w = env
    w.seed_active_subscription(bypass_gb=3)

    await h.run_webhook(w, monkeypatch, make_pending(is_combo=True))

    assert w.panel.added_gb(TG) == 75               # T0 value
    w.db.record_traffic_purchase.assert_awaited_once_with(TG, 75, 0)  # post-commit, as before
    assert w.jobs.rows == {}
