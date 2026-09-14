"""The −15 % window a user sees around the end of a paid subscription / trial
(owner 2026-09-14, docs/audit/SCOPE.md «Срок скидки −15 %»):

  * ONE window of 72 h per period (database.subscriptions.SPECIAL_OFFER_DURATION),
    opened by whatever offers it first — the 3 h reminder or the expiry;
  * the texts name its exact end in Moscow time instead of «3 часа» / «7 дней»;
  * a paid subscription that ended tells the user (08_payments_ux P1 #5) —
    after the expiry transaction committed, with the −15 % button while the
    window is open.

Never raises into the caller.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app import i18n as _i18n

logger = logging.getLogger(__name__)

MSK = timezone(timedelta(hours=3))

_tasks: set = set()


def _t(language: str, key: str, **kwargs) -> str:
    return _i18n.get_text(language, key, **kwargs)


def format_deadline(language: str, dt: datetime) -> str:
    """«17.09.2026 14:30 МСК» / «17.09.2026 14:30 Moscow time»."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return f"{dt.astimezone(MSK):%d.%m.%Y %H:%M} {_t(language, 'common.msk')}"


async def _bypass_text(language: str, telegram_id: int) -> tuple[str, bool]:
    """(text, gb_left) for a premium that ended while a bypass entity exists.

    Read AFTER the expiry committed, with no DB connection held: «обход работает»
    only when the panel shows GB left (#2, docs/notifications/matrix.md) — at
    0 GB the VPN is off and the user is told so. Panel unavailable → the GB are
    not named (never a stale or guessed amount)."""
    from app.services.subscriptions import live_state
    bypass = await live_state.read_bypass(telegram_id)
    if bypass.works is False:
        return _t(language, "subscription.expired_gb_spent"), False
    if bypass.works and not bypass.unlimited:
        return _t(language, "subscription.expired_gb_left",
                  remaining=live_state.format_bytes(language, bypass.remaining)), True
    return _t(language, "subscription.expired_gb_works"), True


async def expired_notice(language: str, telegram_id: int, *, has_bypass: bool) -> tuple[str, InlineKeyboardMarkup]:
    """(text, keyboard) for «your subscription ended» — with the −15 % line and
    button while the period's window is open."""
    import database
    try:
        offer = await database.get_special_offer_info(telegram_id)
    except Exception:  # noqa: BLE001 — the notice still goes out, without the offer
        offer = None
    gb_left = False
    if has_bypass:
        text, gb_left = await _bypass_text(language, telegram_id)
    else:
        text = _t(language, "subscription.expired_paid")
    rows = []
    if offer:
        text += _t(language, "subscription.expired_offer_line",
                   deadline=format_deadline(language, offer["expires_at"]))
        rows.append([InlineKeyboardButton(
            text=_t(language, "subscription.btn_renew_discount_15"),
            callback_data="special_offer_buy",
            style="success",
        )])
    if gb_left:
        rows.append([InlineKeyboardButton(text=_t(language, "traffic.buy_traffic_btn"), callback_data="buy_traffic")])
        rows.append([InlineKeyboardButton(text=_t(language, "traffic.buy_subscription"), callback_data="menu_buy_vpn")])
    elif has_bypass:
        # 0 GB left: the VPN is off — renew, or buy GB to get the bypass back
        rows.append([InlineKeyboardButton(text=_t(language, "traffic.buy_subscription"), callback_data="menu_buy_vpn")])
        rows.append([InlineKeyboardButton(text=_t(language, "traffic.buy_traffic_btn"), callback_data="buy_traffic")])
    else:
        rows.append([InlineKeyboardButton(text=_t(language, "main.buy"), callback_data="menu_buy_vpn")])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


async def notify_expired(bot, telegram_id: int, *, has_bypass: bool) -> bool:
    """Send the «subscription ended» notice. Call only after the expiry committed."""
    try:
        from app.services.language_service import resolve_user_language
        from app.utils.telegram_safe import safe_send_message
        language = await resolve_user_language(telegram_id)
        text, keyboard = await expired_notice(language, telegram_id, has_bypass=has_bypass)
        sent = await safe_send_message(bot, telegram_id, text, reply_markup=keyboard, parse_mode="HTML")
        logger.info("EXPIRY_NOTICE_SENT user=%s bypass=%s sent=%s", telegram_id, has_bypass, sent is not None)
        return sent is not None
    except Exception as e:  # noqa: BLE001
        logger.warning("EXPIRY_NOTICE_FAILED user=%s: %s", telegram_id, type(e).__name__)
        return False


def schedule_expired_notice(telegram_id: int, *, has_bypass: bool) -> bool:
    """Same, from code without a bot at hand (check_and_disable_expired_subscription,
    after its commit). False when there is no bot / event loop."""
    try:
        from app.services import purchase_flow
        bot = purchase_flow._alert_bot()
        if bot is None:
            return False
        loop = asyncio.get_running_loop()
    except Exception:  # noqa: BLE001
        return False
    task = loop.create_task(notify_expired(bot, telegram_id, has_bypass=has_bypass))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return True
