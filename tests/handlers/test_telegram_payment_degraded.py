"""P1 (docs/audit/08_payments_ux.md #1): a Telegram card / Stars
successful_payment that arrives while the DB is not ready or the payments
kill switch is off. The money is already taken and Telegram never resends the
update, so the handler must:
  - send a FORCED admin alert with TG ID, amount, currency, payload, charge id;
  - persist a payment_errors row (now, or once the DB is back);
  - tell the user honestly: received, granted manually, do NOT pay again —
    never "try again later", never a buy button.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import database
from app.handlers.payments import payments_messages as pm
from app.i18n import get_text


def _message(currency="RUB", total=19900, payload="purchase:pid-1"):
    message = MagicMock()
    message.from_user.id = 555
    message.from_user.username = "buyer"
    message.message_id = 9
    message.answer = AsyncMock()
    message.successful_payment = SimpleNamespace(
        currency=currency, total_amount=total, invoice_payload=payload,
        telegram_payment_charge_id="tgcharge-ABCDEFGH12345678",
        provider_payment_charge_id="prov-1",
    )
    return message


@pytest.fixture
def alerts(monkeypatch):
    from app.services import admin_alerts
    sent = AsyncMock(return_value=True)
    monkeypatch.setattr(admin_alerts, "send_alert", sent)
    monkeypatch.setattr(pm, "resolve_user_language", AsyncMock(return_value="ru"))
    return sent


def _assert_honest_answer(message, language="ru"):
    text = message.answer.await_args.args[0]
    assert text == get_text(language, "main.service_unavailable_payment", ref="12345678")
    kb = message.answer.await_args.kwargs["reply_markup"]
    callbacks = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "menu_buy_vpn" not in callbacks        # never suggests paying again
    assert all(cb is None for cb in callbacks)     # only the support URL button


async def test_db_not_ready_alerts_admin_and_answers_honestly(monkeypatch, alerts):
    monkeypatch.setattr(database, "DB_READY", False)
    created = []

    def _fake_task(coro):
        created.append(coro)
        coro.close()
        return MagicMock()
    monkeypatch.setattr(pm.asyncio, "create_task", _fake_task)
    message = _message()

    await pm.process_successful_payment(message, MagicMock())

    alert_text = alerts.await_args.args[2]
    assert alerts.await_args.kwargs["force"] is True
    for part in ("555", "199.00 RUB", "purchase:pid-1", "tgcharge-ABCDEFGH12345678", "db_not_ready"):
        assert part in alert_text
    assert created, "payment_errors row must be written once the DB is back"
    _assert_honest_answer(message)


async def test_payments_disabled_alerts_and_logs_payment_error(monkeypatch, alerts):
    monkeypatch.setattr(database, "DB_READY", True)
    monkeypatch.setattr(pm, "get_feature_flags", lambda: SimpleNamespace(payments_enabled=False))
    log = AsyncMock(return_value=1)
    monkeypatch.setattr(database, "log_payment_error", log)
    message = _message(currency="XTR", total=185)

    await pm.process_successful_payment(message, MagicMock())

    assert "185 XTR" in alerts.await_args.args[2]
    assert "payments_disabled" in alerts.await_args.args[2]
    kw = log.await_args.kwargs
    assert kw["stage"] == "telegram_paid_payments_disabled"
    assert kw["telegram_id"] == 555
    assert kw["payment_provider"] == "telegram_stars"
    assert kw["raw_payload"]["telegram_payment_charge_id"] == "tgcharge-ABCDEFGH12345678"
    _assert_honest_answer(message)


async def test_persist_when_db_ready_writes_the_row(monkeypatch):
    monkeypatch.setattr(pm, "_UNAVAILABLE_PERSIST_INTERVAL_SEC", 0)
    monkeypatch.setattr(database, "DB_READY", True)
    log = AsyncMock(return_value=7)
    monkeypatch.setattr(database, "log_payment_error", log)
    record = pm._paid_while_unavailable_record(_message(), "db_not_ready")
    await pm._persist_when_db_ready(record)
    assert log.await_args.kwargs["stage"] == "telegram_paid_db_not_ready"
    assert log.await_args.kwargs["amount_rubles"] == pytest.approx(199.0)


@pytest.mark.parametrize("lang", ["ru", "en"])
def test_text_never_says_try_again(lang):
    text = get_text(lang, "main.service_unavailable_payment", ref="X").lower()
    assert "try again" not in text and "попробуйте" not in text
    assert ("не нужно" in text) if lang == "ru" else ("do not need to pay again" in text)
