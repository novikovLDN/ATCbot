"""P2/P3 fixes of what users and the admin see around payments
(docs/audit/08_payments_ux.md — findings named in each test)."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import config
import database
from app.i18n import get_text

CYRILLIC = set("абвгдеёжзийклмнопрстуфхцчшщъыьэюя")


def _datas(kb):
    return [b.callback_data for row in kb.inline_keyboard for b in row]


# ── SBP markup label, one button per cash desk (#12, #14, #20) ─────────


def test_sbp_label_shows_the_real_markup_or_nothing(monkeypatch):
    from app.handlers.common.payment_labels import sbp_label
    monkeypatch.setattr(config, "SBP_MARKUP_PERCENT", 0)
    assert "%" not in sbp_label("en") and "%" not in sbp_label("ru")      # EN said «+11%» at 0
    monkeypatch.setattr(config, "SBP_MARKUP_PERCENT", 7)
    assert sbp_label("ru").endswith("(+7%)") and sbp_label("en").endswith("(+7%)")


@pytest.mark.parametrize("wata, platega, expected", [
    (True, True, ["topup_wata:250", "topup_sbp:250", "topup_stars:250"]),
    (False, False, ["topup_card:250", "topup_stars:250"]),
])
def test_topup_methods_one_per_cash_desk(monkeypatch, wata, platega, expected):
    import platega_service
    import wata_service
    from app.handlers.common.payment_labels import topup_method_rows
    monkeypatch.setattr(wata_service, "is_enabled", lambda: wata)
    monkeypatch.setattr(platega_service, "is_enabled", lambda: platega)
    assert [row[0].callback_data for row in topup_method_rows("ru", 250)] == expected


async def test_payment_method_screen(monkeypatch):
    import cryptobot_service
    import platega_service
    import wata_service
    from app.handlers.payments import payment_method_selection as pms
    monkeypatch.setattr(config, "SBP_MARKUP_PERCENT", 0)
    monkeypatch.setattr(wata_service, "is_enabled", lambda: True)
    monkeypatch.setattr(platega_service, "is_enabled", lambda: True)
    monkeypatch.setattr(cryptobot_service, "is_enabled", lambda: False)
    monkeypatch.setattr(database, "get_user_balance", AsyncMock(return_value=0))
    monkeypatch.setattr(pms, "resolve_user_language", AsyncMock(return_value="en"))
    cb = MagicMock()
    cb.from_user.id = 1
    cb.message.delete = AsyncMock()
    cb.message.chat.id = 1
    cb.bot.send_photo = AsyncMock()
    cb.answer = AsyncMock()

    await pms.show_payment_method_selection(cb, "basic", 30, 19900, back_callback="tariff:basic")

    kb = cb.bot.send_photo.await_args.kwargs["reply_markup"]
    datas = _datas(kb)
    assert "pay:card" not in datas and "pay:wata" in datas              # WATA twice → once
    assert datas[-1] == "tariff:basic"                                   # M14: back to the periods
    labels = [b.text for row in kb.inline_keyboard for b in row]
    # (main's button_defaults patch may strip a leading emoji — compare the tail)
    assert any(t.endswith("SBP") for t in labels) and not any("+11%" in t for t in labels)


@pytest.mark.parametrize("hidden, shown", [(True, False), (False, True)])
async def test_cryptobot_button_hidden_while_configured(monkeypatch, hidden, shown):
    """CryptoBot under repair (2026-09-15): the token stays (webhooks keep
    working), only the purchase button is hidden."""
    import cryptobot_service
    from app.handlers.payments import payment_method_selection as pms
    monkeypatch.setattr(cryptobot_service, "CRYPTOBOT_API_TOKEN", "tok")
    monkeypatch.setattr(cryptobot_service, "BUTTON_HIDDEN", hidden)
    monkeypatch.setattr(database, "get_user_balance", AsyncMock(return_value=0))
    monkeypatch.setattr(pms, "resolve_user_language", AsyncMock(return_value="en"))
    cb = MagicMock()
    cb.from_user.id = 1
    cb.message.delete = AsyncMock()
    cb.bot.send_photo = AsyncMock()
    cb.answer = AsyncMock()

    await pms.show_payment_method_selection(cb, "basic", 30, 19900, back_callback="tariff:basic")

    assert cryptobot_service.is_enabled() is True
    assert ("pay:crypto" in _datas(cb.bot.send_photo.await_args.kwargs["reply_markup"])) is shown


def test_invoice_back_and_label_know_the_combo():
    from app.handlers.callbacks import payments_callbacks as pc
    assert pc._invoice_back({"combo_bypass_gb": 75}, "basic") == "combo_tariff:combo_basic"
    assert pc._invoice_back({}, "plus") == "tariff:plus"
    assert pc._tariff_label({"combo_bypass_gb": 75}, "plus", "en") == "Combo Plus"   # M16: was «Plus»
    assert pc._tariff_label({}, "basic", "ru") == "Basic"


# ── /buy = «Продлить VPN» (M13), management screen RU/EN (M9) ──────────


@pytest.mark.parametrize("sub, manage", [
    ({"subscription_type": "plus", "is_combo": False}, True),
    ({"subscription_type": "basic", "is_combo": True}, True),
    (None, False),
    ({"subscription_type": "basic", "is_bypass_only": True}, False),
])
async def test_buy_opens_the_same_screen_as_renew(monkeypatch, sub, manage):
    from app.handlers.payments import callbacks as cb_mod
    monkeypatch.setattr(cb_mod.database, "get_subscription", AsyncMock(return_value=sub))
    monkeypatch.setattr(cb_mod, "resolve_user_language", AsyncMock(return_value="en"))
    opened = AsyncMock()
    monkeypatch.setattr(cb_mod, "_open_buy_screen", opened)
    message = MagicMock()
    message.from_user.id = 5
    message.answer = AsyncMock()
    state = MagicMock()
    state.update_data = AsyncMock()
    state.set_state = AsyncMock()

    await cb_mod.open_buy_or_manage(message, MagicMock(), state)

    if manage:
        opened.assert_not_awaited()
        text = message.answer.await_args.args[0]
        kb = message.answer.await_args.kwargs["reply_markup"]
        assert text.startswith(get_text("en", "buy.manage_title").split("{", 1)[0])
        assert not (set(text.lower()) & CYRILLIC)
        assert not any(set(b.text.lower()) & CYRILLIC for row in kb.inline_keyboard for b in row)
        assert "switch_tariff_menu" in _datas(kb)
    else:
        opened.assert_awaited_once()


async def test_buy_command_delegates(monkeypatch):
    from app.handlers.payments import buy as buy_mod
    from app.handlers.payments import callbacks as cb_mod
    monkeypatch.setattr(buy_mod, "ensure_db_ready_message", AsyncMock(return_value=True))
    target = AsyncMock()
    monkeypatch.setattr(cb_mod, "open_buy_or_manage", target)
    await buy_mod.cmd_buy(MagicMock(), MagicMock(), MagicMock())
    target.assert_awaited_once()


# ── «Отмена» (M17), «Пополнить баланс» on insufficient balance ─────────


async def test_promo_input_has_cancel(monkeypatch):
    from app.handlers.payments import callbacks as cb_mod
    monkeypatch.setattr(cb_mod, "ensure_db_ready_callback", AsyncMock(return_value=True))
    monkeypatch.setattr(cb_mod, "resolve_user_language", AsyncMock(return_value="ru"))
    monkeypatch.setattr(cb_mod, "get_promo_session", AsyncMock(return_value=None))
    cb = MagicMock()
    cb.from_user.id = 1
    cb.answer = AsyncMock()
    cb.message.answer = AsyncMock()
    state = MagicMock()
    state.set_state = AsyncMock()
    await cb_mod.callback_enter_promo(cb, state)
    assert _datas(cb.message.answer.await_args.kwargs["reply_markup"]) == ["promo_back"]


async def test_custom_topup_amount_has_cancel_and_leaves_the_input(monkeypatch):
    from app.handlers.callbacks import payments_callbacks as pc
    from app.handlers.common.states import TopUpStates
    monkeypatch.setattr(pc, "ensure_db_ready_callback", AsyncMock(return_value=True))
    monkeypatch.setattr(pc, "resolve_user_language", AsyncMock(return_value="ru"))
    cb = MagicMock()
    cb.from_user.id = 1
    cb.answer = AsyncMock()
    cb.message.answer = AsyncMock()
    state = MagicMock()
    state.set_state = AsyncMock()
    await pc.callback_topup_custom(cb, state)
    assert _datas(cb.message.answer.await_args.kwargs["reply_markup"]) == ["topup_balance"]

    # «Отмена» → topup_balance leaves the amount-input state
    monkeypatch.setattr(database, "get_user_balance", AsyncMock(return_value=0.0))
    monkeypatch.setattr(pc, "safe_edit_text", AsyncMock())
    state.get_state = AsyncMock(return_value=TopUpStates.waiting_for_amount)
    state.clear = AsyncMock()
    await pc.callback_topup_balance(cb, state)
    state.clear.assert_awaited_once()


async def test_insufficient_balance_offers_a_topup(monkeypatch):
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.storage.base import StorageKey
    from aiogram.fsm.storage.memory import MemoryStorage

    from app.handlers.callbacks import payments_callbacks as pc
    from app.handlers.common.states import PurchaseState
    state = FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=3, user_id=3))
    await state.set_state(PurchaseState.choose_payment_method)
    await state.update_data(tariff_type="basic", period_days=30, final_price_kopecks=19900)
    monkeypatch.setattr(pc, "check_rate_limit", lambda *a, **k: (True, None))
    monkeypatch.setattr(pc, "resolve_user_language", AsyncMock(return_value="en"))
    monkeypatch.setattr(database, "get_user_balance", AsyncMock(return_value=50.0))
    cb = MagicMock()
    cb.from_user.id = 3
    cb.answer = AsyncMock()
    cb.message.answer = AsyncMock()

    await pc.callback_pay_balance(cb, state)

    assert cb.answer.await_args.kwargs.get("show_alert") is True
    assert _datas(cb.message.answer.await_args.kwargs["reply_markup"]) == ["topup_balance"]


# ── admin alert «Bypass не создался» (#25) ─────────────────────────────


async def test_bypass_failed_alert_is_plain_text_and_logged(monkeypatch):
    from app.services import admin_alerts, purchase_flow
    monkeypatch.setattr(database, "log_payment_error", AsyncMock(return_value=1))
    monkeypatch.setattr(purchase_flow, "_alert_bot", lambda: object())
    send = AsyncMock(return_value=True)
    monkeypatch.setattr(admin_alerts, "send_alert", send)

    await purchase_flow._notify_admin_bypass_failed(7, "basic", 502, "<html><body>502 Bad gateway</body></html>")

    assert send.await_args.args[1] == "vpn_api"          # budgeted admin_alerts channel, no parse_mode
    assert "tg:7" in send.await_args.args[2]
    assert database.log_payment_error.await_args.kwargs["stage"] == "bypass_create_failed"


# ── auto-renewal failures → the user is told (#14) ─────────────────────


async def test_autorenew_failure_notice_once_per_day(monkeypatch):
    import auto_renewal as ar
    monkeypatch.setattr(ar, "_failure_notice_sent_at", {})
    monkeypatch.setattr(ar, "resolve_user_language", AsyncMock(return_value="en"))
    sent = AsyncMock(return_value=MagicMock())
    monkeypatch.setattr(ar, "safe_send_message", sent)

    await ar._send_autorenew_failure_notices(object(), [
        (1, "autorenew.failed_debit", 199.0), (1, "autorenew.failed_debit", 199.0),
        (2, "autorenew.failed_refunded", 349.0),
    ])

    assert sent.await_count == 2
    assert sent.await_args_list[0].args[2] == get_text("en", "autorenew.failed_debit", amount=199.0)
    assert _datas(sent.await_args_list[0].kwargs["reply_markup"]) == ["menu_buy_vpn", "topup_balance"]


# ── texts: gifts (#19, #21), EN vs RU / code (#22), SBP / commissions (#12) ──


def test_gift_names_follow_the_language():
    from app.handlers.callbacks import gift
    assert gift._period_display(90, "en") == "3 months"
    assert gift._period_display(90) == "3 месяца"
    assert gift._tariff_display_name("combo_plus", "en") == "Combo Plus"
    assert gift._tariff_display_name("combo_plus") == "Комбо Plus"


def test_texts():
    assert "{gift_link}" in get_text("ru", "gift.success", tariff_name="x", period="y", gift_link="{gift_link}") \
        or "L1NK" in get_text("ru", "gift.success", tariff_name="x", period="y", gift_link="L1NK")
    for lang in ("ru", "en"):
        assert "язык" not in get_text(lang, "gift.activated_welcome", tariff_name="x", period="y").lower()
        assert "language" not in get_text(lang, "gift.activated_welcome", tariff_name="x", period="y").lower()
    en_basic = get_text("en", "buy.tariff_basic_desc")
    en_plus = get_text("en", "buy.tariff_plus_desc")
    assert "10 devices" in en_basic and "14 devices" in en_plus
    assert "including whitelists" not in en_plus
    how = get_text("en", "referral.how_it_works_text")
    assert "20%" in how and "30%" in how and "25%" not in how
    assert "+11%" not in get_text("en", "payment.sbp")
    assert "без комиссий" not in get_text("ru", "main.topup_balance_select_amount", balance="0")
    assert not (set(get_text("en", "payment.wata_waiting", amount="1").lower()) & CYRILLIC)
