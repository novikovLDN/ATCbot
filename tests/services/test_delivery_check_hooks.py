"""Legacy (flag-off) payment/grant paths schedule the delayed delivery check
(verify_delivery.schedule_legacy_check) after their post-commit panel work.

Driven through the T0 harness (tests/services/payment_core_harness.py), which
records schedule_legacy_check calls in World.delivery_checks. An external
webhook FIRST purchase schedules it too; a webhook renewal keeps its own
verify_premium/bypass_delivery (confirmation.py), and outbox jobs are verified
inside provisioning.apply.
"""
from __future__ import annotations

import pytest

import config
from tests.services import payment_core_harness as h

MODE_VAR = f"{config.APP_ENV.upper()}_USE_NEW_PROVISIONING"


@pytest.fixture
def w(monkeypatch):
    monkeypatch.setenv(MODE_VAR, "off")
    return h.install(monkeypatch)


def checks(w, source):
    return [kw for tg, kw in w.delivery_checks if tg == h.TG and kw["source"] == source]


async def test_balance_purchase(w, monkeypatch):
    await h.run_balance_purchase(w, monkeypatch)
    [kw] = checks(w, "balance")
    assert kw["ref"].startswith("balance_purchase_") and kw["expect_bypass"] is True


async def test_telegram_payment(w, monkeypatch):
    await h.run_telegram_payment(w, monkeypatch, h.make_pending())
    [kw] = checks(w, "telegram")
    assert kw["ref"] == "pid-1" and kw["expect_bypass"] is True


async def test_auto_renewal(w, monkeypatch):
    w.seed_active_subscription(days_left=0)
    w.sub["expires_at"] = h.naive(h.utcnow() + h.timedelta(hours=2))
    await h.run_auto_renewal(w, monkeypatch, last_payment_tariff="basic_30")
    [kw] = checks(w, "auto_renewal")
    assert kw["expect_bypass"] is True


async def test_admin_grant_days_premium_only(w, monkeypatch):
    await h.run_admin_grant_days(w, monkeypatch, days=7)
    [kw] = checks(w, "admin_grant")
    assert kw["ref"].endswith(":7d") and kw.get("expect_bypass", False) is False


async def test_gift_activation(w, monkeypatch):
    await h.run_gift_activation(w, monkeypatch, period_days=90)
    [kw] = checks(w, "gift")
    assert kw["ref"] == "gift:GIFT1234" and kw["expect_bypass"] is True


async def test_external_webhook_first_purchase(w, monkeypatch):
    await h.run_webhook(w, monkeypatch, h.make_pending())
    [kw] = checks(w, "webhook")
    assert kw["ref"] == "pid-1" and kw["expect_bypass"] is True


async def test_external_webhook_renewal_keeps_its_own_verification(w, monkeypatch):
    w.seed_active_subscription(days_left=10)
    await h.run_webhook(w, monkeypatch, h.make_pending())
    assert checks(w, "webhook") == []
