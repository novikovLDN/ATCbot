"""ONE success message for every payment path (docs/audit/08_payments_ux.md #3, #16).

Webhook (Platega / WATA / CryptoBot), balance and Telegram card / Stars all
call build_purchase_success(); top-ups call build_topup_success(). The text
names the tariff (Combo included), the period, the new end date and the
bypass GB the purchase adds; the keyboard is the connect/help keyboard in the
user's language.

i18n goes through the module attribute app.i18n.get_text (not a bound name),
so tests that record i18n keys see these calls.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import config
from app import i18n as _i18n
from app.services import tariffs

logger = logging.getLogger(__name__)

# Dashboard «automated notifications» keys the admin may override (RU only).
_OVERRIDE_FIRST = {"basic": "payment.success_welcome_basic", "plus": "payment.success_welcome_plus"}
_OVERRIDE_RENEWAL = "payment.success_renewal_compact"


def _t(language: str, key: str, **kwargs) -> str:
    return _i18n.get_text(language, key, **kwargs)


def _tier(subscription_type: Optional[str]) -> str:
    tier = tariffs.normalize_tier((subscription_type or "basic").strip().lower())
    return tier if tier in tariffs.BASE_TARIFFS else "basic"


def tariff_display(language: str, subscription_type: Optional[str], is_combo: bool) -> str:
    """«⚡️ Basic» / «👑 Plus» / «🚀 Комбо Basic» / «🚀 Combo Plus» in the user's language."""
    tier = _tier(subscription_type)
    return _t(language, f"purchase.tariff_{'combo_' if is_combo else ''}{tier}")


def period_display(language: str, period_days) -> str:
    """«1 месяц» / «3 месяца» / «12 месяцев» (calendar months); other periods — days."""
    months = tariffs.months_for_period(period_days)
    if months is None:
        return _t(language, "purchase.period_days", days=int(period_days or 0))
    if months == 1:
        return _t(language, "buy.period_text_1")
    if months % 10 in (2, 3, 4) and months % 100 not in (12, 13, 14):
        return _t(language, "buy.period_text_2_4", months=months)
    return _t(language, "buy.period_text_5_plus", months=months)


def bypass_gb_added(subscription_type: Optional[str], is_combo: bool, period_days) -> int:
    """GB of bypass one payment adds (owner rule: Basic/Plus +10 GB every payment,
    Combo — the Combo table for the period). 0 when the catalog has no such row."""
    try:
        key = tariffs.tariff_key(_tier(subscription_type), bool(is_combo))
        return tariffs.for_purchase(key, int(period_days)).bypass_bytes // tariffs.GIB
    except (tariffs.TariffConfigError, TypeError, ValueError):
        return 0


def success_keyboard(language: str) -> InlineKeyboardMarkup:
    """Connect + help, in the user's language (same keyboard on every path)."""
    from app.handlers.common.keyboards import get_payment_success_keyboard
    return get_payment_success_keyboard(language)


async def _links_block(language: str, telegram_id: Optional[int]) -> str:
    """PURCHASE_FLOW_REMNAWAVE: both subscription URLs right in the first-purchase text."""
    if not telegram_id or not getattr(config, "PURCHASE_FLOW_REMNAWAVE", False):
        return ""
    try:
        import database
        sub = await database.get_subscription_any(telegram_id) or {}
    except Exception as e:  # noqa: BLE001 — links are a convenience, the keyboard connects
        logger.warning("PURCHASE_SUCCESS_LINKS_FAIL user=%s: %s", telegram_id, type(e).__name__)
        return ""
    parts = []
    if sub.get("vpn_key"):
        parts.append(_t(language, "purchase.link_premium", url=sub["vpn_key"]))
    if sub.get("vpn_key_plus"):
        parts.append(_t(language, "purchase.link_bypass", url=sub["vpn_key_plus"]))
    return ("\n\n" + "\n\n".join(parts)) if parts else ""


async def _admin_override(language: str, *, tier: str, is_renewal: bool, tariff: str, date: str) -> Optional[str]:
    """The admin's own dashboard text for this event, if one was written."""
    try:
        from app.services.automated_notifications import get_custom_notification_text
        if is_renewal:
            icon = "🚀" if "🚀" in tariff else ("👑" if tier == "plus" else "⚡️")
            return await get_custom_notification_text(
                _OVERRIDE_RENEWAL, language=language,
                params={"tariff_icon": icon, "tariff": tariff, "date": date},
            )
        return await get_custom_notification_text(
            _OVERRIDE_FIRST[tier], language=language, params={"date": date},
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("purchase success override skipped: %s", e)
        return None


async def build_purchase_success(
    language: str,
    *,
    subscription_type: Optional[str],
    is_combo: bool,
    period_days,
    expires_at: Optional[datetime],
    is_renewal: bool,
    is_upgrade: bool = False,
    telegram_id: Optional[int] = None,
) -> tuple[str, InlineKeyboardMarkup]:
    """(text, keyboard) after a paid VPN purchase / renewal / tariff change."""
    tier = _tier(subscription_type)
    tariff = tariff_display(language, tier, bool(is_combo))
    from app.utils.date_utils import format_date_msk
    date = format_date_msk(expires_at) if expires_at else "—"
    gb = bypass_gb_added(tier, bool(is_combo), period_days)
    gb_line = _t(language, "purchase.success_gb_line", gb=gb) if gb > 0 else ""
    params = dict(tariff_name=tariff, period=period_display(language, period_days),
                  expires_date=date, gb_line=gb_line)
    if is_upgrade:
        text = _t(language, "purchase.success_tariff_changed", **params)
    else:
        custom = await _admin_override(language, tier=tier, is_renewal=is_renewal,
                                       tariff=tariff, date=date)
        if custom:
            text = custom
        else:
            text = _t(language, "purchase.success_renewal" if is_renewal else "purchase.success_first",
                      **params)
        if not is_renewal:
            text += await _links_block(language, telegram_id)
    return text, success_keyboard(language)


def build_topup_success(language: str, *, amount: float, balance: Optional[float]) -> tuple[str, InlineKeyboardMarkup]:
    """(text, keyboard) after a balance top-up (webhook and Telegram alike)."""
    from app.handlers.common.emoji import CE
    if balance is None:
        text = _t(language, "main.balance_topup_success", amount=float(amount))
    else:
        text = _t(language, "main.topup_balance_success", amount=float(amount), balance=float(balance))
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=_t(language, "buy.renew_button"),
            callback_data="menu_buy_vpn",
            icon_custom_emoji_id=CE["buy"],
            style="success",
        )],
        [InlineKeyboardButton(
            text=_t(language, "main.profile"),
            callback_data="menu_profile",
            icon_custom_emoji_id=CE["profile"],
            style="primary",
        )],
    ])
    return text, keyboard
