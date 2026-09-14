"""One success message for every payment path (docs/audit/08_payments_ux.md #3, #16, #17, M11).

build_purchase_success() is used by the webhook (Platega / WATA / CryptoBot),
the balance purchase and Telegram card / Stars; build_topup_success() by both
top-up paths. The text names the tariff (Combo too — it used to say «Basic»),
the period in calendar months, the new end date and the bypass GB the payment
adds; the keyboard is connect + help in the user's language.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from app.i18n import get_text
from app.services.payments import success_message as sm

END = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _no_dashboard_override(monkeypatch):
    import app.services.automated_notifications as autonotif
    monkeypatch.setattr(autonotif, "get_custom_notification_text", AsyncMock(return_value=None))


def _buttons(kb):
    return [b for row in kb.inline_keyboard for b in row]


@pytest.mark.parametrize("sub_type, combo, period, label_ru, gb, period_ru", [
    ("basic", False, 30, "⚡️ Basic", 10, "1 месяц"),
    ("plus", False, 90, "👑 Plus", 10, "3 месяца"),
    ("basic", True, 30, "🚀 Комбо Basic", 75, "1 месяц"),
    ("plus", True, 365, "🚀 Комбо Plus", None, "12 месяцев"),
    ("biz_team", False, 180, "👑 Plus", 10, "6 месяцев"),      # legacy biz = Plus
])
async def test_first_purchase_text(sub_type, combo, period, label_ru, gb, period_ru):
    import config
    text, kb = await sm.build_purchase_success(
        "ru", subscription_type=sub_type, is_combo=combo, period_days=period,
        expires_at=END, is_renewal=False,
    )
    expected_gb = gb if gb is not None else config.COMBO_TARIFFS["combo_plus"][period]["gb"]
    assert label_ru in text
    assert period_ru in text
    assert "20.10.2026" in text
    assert f"+{expected_gb} ГБ" in text
    assert text.startswith(get_text("ru", "purchase.success_first").split("{", 1)[0])
    assert [b.callback_data for b in _buttons(kb)][0] == "connect_instruction"


async def test_renewal_and_tariff_change_use_their_keys():
    renew, _ = await sm.build_purchase_success(
        "ru", subscription_type="basic", is_combo=False, period_days=30,
        expires_at=END, is_renewal=True,
    )
    assert renew.startswith(get_text("ru", "purchase.success_renewal").split("{", 1)[0])
    changed, _ = await sm.build_purchase_success(
        "ru", subscription_type="plus", is_combo=False, period_days=30,
        expires_at=END, is_renewal=True, is_upgrade=True,
    )
    assert changed.startswith(get_text("ru", "purchase.success_tariff_changed").split("{", 1)[0])
    assert "Plus" in changed


async def test_english_user_gets_english_text_and_buttons():
    text, kb = await sm.build_purchase_success(
        "en", subscription_type="basic", is_combo=True, period_days=90,
        expires_at=END, is_renewal=False,
    )
    assert "Combo Basic" in text and "3 months" in text and "GB" in text
    assert not any(ch in text for ch in "абвгдеёжзийклмнопрстуфхцчшщъыьэюя")
    labels = [b.text for b in _buttons(kb)]
    assert labels == [get_text("en", "trial.activated_btn_connect"), get_text("en", "trial.activated_btn_support")]


@pytest.mark.parametrize("lang", ["ru", "en"])
async def test_text_does_not_mention_buttons_that_are_not_there(lang):
    """M11: «👤 Личный кабинет» / «📊 Мой трафик» were listed, no such buttons."""
    text, _ = await sm.build_purchase_success(
        lang, subscription_type="basic", is_combo=False, period_days=30,
        expires_at=END, is_renewal=False,
    )
    for word in ("Личный кабинет", "Мой трафик", "Dashboard", "My traffic"):
        assert word not in text


async def test_admin_dashboard_text_wins_when_set(monkeypatch):
    import app.services.automated_notifications as autonotif
    custom = AsyncMock(return_value="Свой текст до {date}".replace("{date}", "20.10.2026"))
    monkeypatch.setattr(autonotif, "get_custom_notification_text", custom)
    text, _ = await sm.build_purchase_success(
        "ru", subscription_type="plus", is_combo=False, period_days=30,
        expires_at=END, is_renewal=False,
    )
    assert text == "Свой текст до 20.10.2026"
    assert custom.await_args.args[0] == "payment.success_welcome_plus"


@pytest.mark.parametrize("period, ru", [(730, "24 месяца"), (7, "7 дн.")])
def test_period_display(period, ru):
    assert sm.period_display("ru", period) == ru


def test_topup_success_with_and_without_balance():
    text, kb = sm.build_topup_success("ru", amount=250, balance=400.5)
    assert text == get_text("ru", "main.topup_balance_success", amount=250.0, balance=400.5)
    assert [b.callback_data for b in _buttons(kb)] == ["menu_buy_vpn", "menu_profile"]
    text2, kb2 = sm.build_topup_success("en", amount=250, balance=None)
    assert text2 == get_text("en", "main.balance_topup_success", amount=250.0)
    assert [b.callback_data for b in _buttons(kb2)] == ["menu_buy_vpn", "menu_profile"]
