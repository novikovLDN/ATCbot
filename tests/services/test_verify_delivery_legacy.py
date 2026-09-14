"""Delayed delivery check for the legacy (flag-off) paths.

verify_delivery.check_legacy_delivery(tg, source=…) re-reads the committed DB
subscription and the panel after the post-commit panel work of balance /
Telegram / auto-renewal / admin grant / gift: panel premium expireAt earlier
than DB expires_at by more than 5 min, a non-ACTIVE or absent premium entity,
or (expect_bypass) an absent / non-ACTIVE bypass entity → one forced
DELIVERY_MISMATCH alert. No retries on these paths — the alert is the action
item. Unavailable panel → skipped (logged). Over the per-window budget the
alert goes into one digest (P2-12); a problem already alerted by the renewal
sync / bypass top-up failure is not alerted twice (P2-25).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

import config
import database
from app.api import payment_webhook
from app.services import admin_alerts
from app.services.payments import verify_delivery as vd
from tests.fakes.panel import FakePanel

GIB = 1024 ** 3
TG = 6161
BOT = object()
NOW = datetime.now(timezone.utc).replace(microsecond=0)
OCT20 = NOW + timedelta(days=7)
NOV20 = OCT20 + timedelta(days=30)


@pytest.fixture
def panel(monkeypatch):
    return FakePanel().install(monkeypatch)


@pytest.fixture
def sub(monkeypatch):
    state = {"row": {"telegram_id": TG, "status": "active", "expires_at": NOV20,
                     "is_bypass_only": False, "activation_status": "active"}}

    async def get_subscription_any(tg):
        return dict(state["row"]) if state["row"] and tg == TG else None
    monkeypatch.setattr(database, "get_subscription_any", get_subscription_any)
    return state


@pytest.fixture
def alerts(monkeypatch):
    mock = AsyncMock(return_value=True)
    monkeypatch.setattr(admin_alerts, "send_alert", mock)
    return mock


@pytest.fixture(autouse=True)
def _isolation(monkeypatch):
    monkeypatch.setattr(config, "REMNAWAVE_ENABLED", True)
    monkeypatch.setattr(payment_webhook, "_bot", BOT)
    vd.reset_legacy_alert_state()
    yield
    vd.reset_legacy_alert_state()


async def check(**kw):
    kw.setdefault("source", "balance")
    kw.setdefault("ref", "balance_purchase_55")
    return await vd.check_legacy_delivery(TG, delay_sec=0, **kw)


# ── premium ────────────────────────────────────────────────────────────

async def test_panel_still_shows_old_date_is_a_forced_mismatch_alert(panel, sub, alerts):
    panel.seed_premium(TG, OCT20)

    text = await check()

    assert text is not None and text.startswith(vd.MISMATCH_TAG)
    alerts.assert_awaited_once()
    assert alerts.await_args.args[:2] == (BOT, "payment")
    assert alerts.await_args.kwargs["force"] is True
    sent = alerts.await_args.args[2]
    for part in (f"user: tg:{TG}", "source: balance", "ref: balance_purchase_55",
                 NOV20.strftime("%Y-%m-%d %H:%M"), OCT20.strftime("%Y-%m-%d %H:%M"),
                 "short by 30d", "no automatic retry",
                 "INSERT INTO provisioning_jobs", f"telegram_id = ANY(ARRAY[{TG}])"):
        assert part in sent, part


async def test_panel_matching_db_is_ok(panel, sub, alerts):
    panel.seed_premium(TG, NOV20 - timedelta(minutes=4))     # within the 5 min tolerance
    assert await check() is None
    alerts.assert_not_awaited()


async def test_panel_later_than_db_is_not_a_delivery_failure(panel, sub, alerts):
    panel.seed_premium(TG, NOV20 + timedelta(days=3))
    assert await check() is None
    alerts.assert_not_awaited()


async def test_premium_absent_or_inactive_is_a_mismatch(panel, sub, alerts):
    assert "premium: entity absent" in await check()
    panel.seed_premium(TG, NOV20)
    panel.premium[TG]["status"] = "DISABLED"
    assert "status DISABLED" in await check()


async def test_unavailable_panel_is_skipped_without_alert(panel, sub, alerts):
    panel.seed_premium(TG, OCT20)
    panel.mode = "down"
    assert await check(expect_bypass=True) is None
    alerts.assert_not_awaited()


@pytest.mark.parametrize("row", [
    None,
    {"status": "expired", "expires_at": NOV20},
    {"status": "active", "expires_at": NOW - timedelta(hours=1)},
    {"status": "active", "expires_at": NOV20, "activation_status": "pending"},
])
async def test_nothing_to_verify(panel, sub, alerts, row):
    sub["row"] = None if row is None else {**sub["row"], **row}
    assert await check() is None
    alerts.assert_not_awaited()


async def test_bypass_only_row_skips_premium(panel, sub, alerts):
    sub["row"]["is_bypass_only"] = True
    panel.seed_bypass(TG, 5 * GIB)
    assert await check(expect_bypass=True) is None


async def test_naive_db_expiry_is_utc(panel, sub, alerts):
    sub["row"]["expires_at"] = NOV20.replace(tzinfo=None)
    panel.seed_premium(TG, NOV20)
    assert await check() is None


# ── bypass ─────────────────────────────────────────────────────────────

async def test_expected_bypass_absent_or_limited_is_a_mismatch(panel, sub, alerts):
    panel.seed_premium(TG, NOV20)
    assert "bypass: entity absent" in await check(expect_bypass=True)
    panel.seed_bypass(TG, 5 * GIB)
    panel.bypass[TG]["status"] = "LIMITED"
    assert "bypass: status LIMITED" in await check(expect_bypass=True)
    assert await check(expect_bypass=False) is None


async def test_bypass_present_and_active_is_ok(panel, sub, alerts):
    panel.seed_premium(TG, NOV20)
    panel.seed_bypass(TG, 10 * GIB)
    assert await check(expect_bypass=True) is None
    alerts.assert_not_awaited()


# ── one incident = one alert (P2-25) ───────────────────────────────────

SYNC_INFO = {"telegram_id": TG, "subscription_end": NOV20, "tariff": "basic", "period_days": 30}


@pytest.fixture
def payment_errors(monkeypatch):
    rows = []

    async def log_payment_error(**kw):
        rows.append(kw)
    monkeypatch.setattr(database, "log_payment_error", log_payment_error, raising=False)
    return rows


async def test_premium_problem_already_alerted_by_the_sync_failure_is_not_alerted_again(
        panel, sub, alerts, payment_errors):
    """The renewal sync failed (forced alert + background re-sync); 20 s later the
    legacy check sees the same short premium date → no second alert."""
    from app.services import purchase_flow
    panel.seed_premium(TG, OCT20)
    await purchase_flow._report_renewal_sync_failure(SYNC_INFO, RuntimeError("panel down"), final=False)
    assert alerts.await_count == 1

    assert await check() is None
    assert alerts.await_count == 1


async def test_bypass_problem_already_alerted_by_the_topup_is_not_alerted_again(
        panel, sub, alerts, payment_errors):
    from app.services import remnawave_service
    panel.seed_premium(TG, NOV20)                     # premium fine, bypass entity missing
    await remnawave_service._alert_bypass_not_delivered(TG, 10 * GIB, "basic", "bypass create failed")
    assert alerts.await_count == 1

    assert await check(expect_bypass=True) is None
    assert alerts.await_count == 1


async def test_a_different_problem_of_the_same_user_is_still_alerted(panel, sub, alerts, payment_errors):
    from app.services import purchase_flow
    panel.seed_premium(TG, OCT20)
    await purchase_flow._report_renewal_sync_failure(SYNC_INFO, RuntimeError("panel down"), final=False)

    text = await check(expect_bypass=True)            # premium: known; bypass absent: new

    assert text is not None and "bypass: entity absent" in text and "premium:" not in text
    assert alerts.await_count == 2


# ── alert budget ───────────────────────────────────────────────────────

async def test_over_budget_alerts_go_into_one_digest_never_dropped(panel, sub, alerts):
    """P2-12: beyond the per-window budget the alerts used to go out without
    force (the category cooldown could drop them silently). Now they are
    buffered and delivered in ONE forced digest by the provisioning worker."""
    from app.services import provisioning
    budget = provisioning.ALERT_IMMEDIATE_PER_WINDOW
    panel.seed_premium(TG, OCT20)
    for _ in range(budget + 2):
        await check()

    assert [c.kwargs["force"] for c in alerts.await_args_list] == [True] * budget
    assert provisioning.pending_alert_counts() == {"legacy_delivery": 2}

    assert await provisioning.flush_alert_digests(BOT, final=True) == 1
    digest = alerts.await_args.args[2]
    assert alerts.await_args.kwargs["force"] is True
    assert "DIGEST" in digest and vd.MISMATCH_TAG in digest and f"tg:{TG}" in digest


async def test_no_bot_logs_and_does_not_raise(monkeypatch, panel, sub, alerts):
    from app.api import telegram_webhook
    monkeypatch.setattr(payment_webhook, "_bot", None)
    monkeypatch.setattr(telegram_webhook, "_bot", None)
    panel.seed_premium(TG, OCT20)
    assert (await check()).startswith(vd.MISMATCH_TAG)
    alerts.assert_not_awaited()


async def test_exception_inside_the_check_never_raises(monkeypatch, panel, sub, alerts):
    monkeypatch.setattr(database, "get_subscription_any", AsyncMock(side_effect=RuntimeError("db")))
    assert await check() is None


# ── scheduling ─────────────────────────────────────────────────────────

async def test_schedule_runs_the_check_in_the_background(monkeypatch):
    seen = []

    async def fake_check(tg, **kw):
        seen.append((tg, kw))
    monkeypatch.setattr(vd, "check_legacy_delivery", fake_check)

    vd.schedule_legacy_check(TG, source="gift", ref="gift:ABC", expect_bypass=True)
    for _ in range(3):
        await asyncio.sleep(0)

    assert seen == [(TG, {"source": "gift", "ref": "gift:ABC", "expect_bypass": True,
                          "delay_sec": vd.LEGACY_CHECK_DELAY_S})]


async def test_schedule_is_a_noop_without_remnawave(monkeypatch):
    monkeypatch.setattr(config, "REMNAWAVE_ENABLED", False)
    called = AsyncMock()
    monkeypatch.setattr(vd, "check_legacy_delivery", called)
    vd.schedule_legacy_check(TG, source="balance")
    await asyncio.sleep(0)
    called.assert_not_called()


def test_schedule_without_a_loop_does_not_raise():
    vd.schedule_legacy_check(TG, source="balance")   # sync context: logged, no exception


def test_fix_sql_is_the_outbox_insert():
    sql = vd.premium_fix_sql([1, 2], tag="t")
    assert sql.startswith("INSERT INTO provisioning_jobs")
    assert "telegram_id = ANY(ARRAY[1,2])" in sql and "ON CONFLICT (idempotency_key) DO NOTHING" in sql
    assert "expires_at" in sql and "bypass_add_bytes" not in sql
