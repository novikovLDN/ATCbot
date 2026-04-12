"""
Telegram Premium purchase flow.

Screens:
1. Enter username (with validation & attempt limit)
2. Choose period (3/6/12 months)
3. Choose payment method (card via YooKassa/TG Payments)
4. Payment processing → success screen + admin notification
"""
import asyncio
import logging
import re
from datetime import datetime, timezone

from aiogram import Router, F, Bot
from aiogram.types import (
    CallbackQuery,
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    LabeledPrice,
)
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext

import config
import database
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.core.rate_limit import check_rate_limit
from app.handlers.common.guards import ensure_db_ready_callback
from app.handlers.common.states import TelegramPremiumState
from app.handlers.common.utils import safe_edit_text

premium_router = Router()
logger = logging.getLogger(__name__)

# --- Constants ---
MAX_USERNAME_ATTEMPTS = 7

# Valid Telegram username: @, then 5-32 latin chars/digits/underscores
_USERNAME_RE = re.compile(r"^@[A-Za-z][A-Za-z0-9_]{4,31}$")

PREMIUM_PLANS = {
    90: {"price_rubles": 1590, "label_key": "premium.period_3m", "period_text": "3 мес."},
    180: {"price_rubles": 2690, "label_key": "premium.period_6m", "period_text": "6 мес."},
    365: {"price_rubles": 3790, "label_key": "premium.period_12m", "period_text": "12 мес."},
}

INVOICE_TIMEOUT = getattr(config, "INVOICE_TIMEOUT_SECONDS", 900)


# ─── helpers ───

def _is_safe_text(text: str) -> bool:
    """Reject overly long input, control chars, and known-bad unicode."""
    if not text or len(text) > 64:
        return False
    # Block control characters (except normal whitespace)
    if any(ord(c) < 32 and c not in ("\n", "\r", "\t") for c in text):
        return False
    # Block zero-width and other invisible unicode
    if re.search(r"[\u200b-\u200f\u202a-\u202e\u2060-\u2069\ufeff]", text):
        return False
    return True


def _is_valid_username(text: str) -> bool:
    """Check that text is a valid Telegram username (latin only, 5-32 chars after @)."""
    if not _is_safe_text(text):
        return False
    # Must contain only ASCII (reject cyrillic / other scripts after @)
    try:
        text.encode("ascii")
    except UnicodeEncodeError:
        return False
    return bool(_USERNAME_RE.match(text))


def _get_back_to_main_kb(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n_get_text(language, "premium.back_button"),
            callback_data="menu_main",
        )],
    ])


async def _schedule_invoice_deletion(bot: Bot, chat_id: int, message_id: int, timeout: int = INVOICE_TIMEOUT):
    try:
        await asyncio.sleep(timeout)
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass


# ─── Screen 1: Enter username ───

@premium_router.callback_query(F.data == "premium_buy")
async def callback_premium_buy(callback: CallbackQuery, state: FSMContext):
    """Entry point — show 'enter username' screen."""
    if not await ensure_db_ready_callback(callback):
        return
    try:
        await callback.answer()
    except Exception:
        pass

    language = await resolve_user_language(callback.from_user.id)
    await state.clear()
    await state.set_state(TelegramPremiumState.waiting_for_username)
    await state.update_data(premium_attempts=MAX_USERNAME_ATTEMPTS)

    text = i18n_get_text(language, "premium.enter_username")
    kb = _get_back_to_main_kb(language)

    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.bot.send_message(
        callback.message.chat.id, text, reply_markup=kb, parse_mode="HTML",
    )


# ─── Screen 1b: Process username input ───

@premium_router.message(StateFilter(TelegramPremiumState.waiting_for_username))
async def process_premium_username(message: Message, state: FSMContext):
    """Validate entered username."""
    telegram_id = message.from_user.id
    language = await resolve_user_language(telegram_id)
    text = (message.text or "").strip()

    # Silent ignore: no @ at all — don't respond (user may be chatting elsewhere)
    if not text.startswith("@"):
        return

    # Validate username — silently ignore invalid input, only notify when attempts exhausted
    if not _is_valid_username(text):
        data = await state.get_data()
        attempts = data.get("premium_attempts", MAX_USERNAME_ATTEMPTS) - 1
        await state.update_data(premium_attempts=attempts)

        if attempts <= 0:
            # Exhausted all attempts — show message
            await state.clear()
            kb = _get_back_to_main_kb(language)
            await message.answer(
                i18n_get_text(language, "premium.attempts_exhausted"),
                reply_markup=kb,
                parse_mode="HTML",
            )
        # Otherwise: silently ignore, wait for correct input
        return

    # Username is valid — save and show period selection
    username = text  # e.g. @novikovWQ
    await state.update_data(premium_username=username)
    await state.set_state(TelegramPremiumState.choose_period)

    await _show_period_screen(message, language, username)


async def _show_period_screen(message: Message, language: str, username: str):
    """Show period selection keyboard."""
    text = i18n_get_text(language, "premium.choose_period", username=username)
    rows = []
    for days, plan in PREMIUM_PLANS.items():
        rows.append([InlineKeyboardButton(
            text=i18n_get_text(language, plan["label_key"]),
            callback_data=f"premium_period:{days}",
        )])
    rows.append([InlineKeyboardButton(
        text=i18n_get_text(language, "premium.back_button"),
        callback_data="premium_buy",
    )])
    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


# ─── Screen 2: Choose period ───

@premium_router.callback_query(
    F.data.startswith("premium_period:"),
    StateFilter(TelegramPremiumState.choose_period, TelegramPremiumState.choose_payment_method),
)
async def callback_premium_period(callback: CallbackQuery, state: FSMContext):
    """User selected a period — show payment method."""
    try:
        await callback.answer()
    except Exception:
        pass

    try:
        days = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        return

    plan = PREMIUM_PLANS.get(days)
    if not plan:
        return

    language = await resolve_user_language(callback.from_user.id)
    data = await state.get_data()
    username = data.get("premium_username")
    if not username:
        await callback.answer("Session expired", show_alert=True)
        await state.clear()
        return

    price_rubles = plan["price_rubles"]
    period_text = plan["period_text"]

    await state.update_data(
        premium_period_days=days,
        premium_price_rubles=price_rubles,
        premium_period_text=period_text,
    )
    await state.set_state(TelegramPremiumState.choose_payment_method)

    text = i18n_get_text(
        language, "premium.choose_payment",
        username=username, period=period_text, price=f"{price_rubles:,}".replace(",", " "),
    )
    premium_buttons = [
        [InlineKeyboardButton(text="💳 Банковская карта", callback_data="premium_pay:card")],
    ]
    import platega_service
    if platega_service.is_enabled():
        premium_buttons.append([InlineKeyboardButton(
            text=i18n_get_text(language, "payment.international"),
            callback_data="premium_pay:international",
        )])
    premium_buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "premium.back_button"),
        callback_data=f"premium_period_back",
    )])
    kb = InlineKeyboardMarkup(inline_keyboard=premium_buttons)
    await safe_edit_text(callback.message, text, reply_markup=kb)


# Back to period selection from payment screen
@premium_router.callback_query(
    F.data == "premium_period_back",
    StateFilter(TelegramPremiumState.choose_payment_method),
)
async def callback_premium_period_back(callback: CallbackQuery, state: FSMContext):
    try:
        await callback.answer()
    except Exception:
        pass

    language = await resolve_user_language(callback.from_user.id)
    data = await state.get_data()
    username = data.get("premium_username")
    if not username:
        await state.clear()
        return

    await state.set_state(TelegramPremiumState.choose_period)

    text = i18n_get_text(language, "premium.choose_period", username=username)
    rows = []
    for days, plan in PREMIUM_PLANS.items():
        rows.append([InlineKeyboardButton(
            text=i18n_get_text(language, plan["label_key"]),
            callback_data=f"premium_period:{days}",
        )])
    rows.append([InlineKeyboardButton(
        text=i18n_get_text(language, "premium.back_button"),
        callback_data="premium_buy",
    )])
    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    await safe_edit_text(callback.message, text, reply_markup=kb)


# ─── Screen 3: Payment via card (TG Payments / YooKassa) ───

@premium_router.callback_query(
    F.data == "premium_pay:card",
    StateFilter(TelegramPremiumState.choose_payment_method),
)
async def callback_premium_pay_card(callback: CallbackQuery, state: FSMContext):
    """Create pending purchase and send TG Payments invoice."""
    telegram_id = callback.from_user.id

    is_allowed, rate_limit_msg = check_rate_limit(telegram_id, "payment_init")
    if not is_allowed:
        language = await resolve_user_language(telegram_id)
        await callback.answer(
            rate_limit_msg or i18n_get_text(language, "common.rate_limit_message"),
            show_alert=True,
        )
        return

    language = await resolve_user_language(telegram_id)
    data = await state.get_data()

    username = data.get("premium_username")
    days = data.get("premium_period_days")
    price_rubles = data.get("premium_price_rubles")
    period_text = data.get("premium_period_text")

    if not all([username, days, price_rubles, period_text]):
        await callback.answer(
            i18n_get_text(language, "errors.session_expired"), show_alert=True,
        )
        await state.clear()
        return

    if not config.TG_PROVIDER_TOKEN:
        await callback.answer(
            i18n_get_text(language, "errors.payments_unavailable"), show_alert=True,
        )
        return

    price_kopecks = price_rubles * 100

    try:
        # Create pending purchase with purchase_type="telegram_premium"
        # Store target username in `country` field (reused for premium context)
        purchase_id = await database.create_pending_purchase(
            telegram_id=telegram_id,
            tariff="telegram_premium",
            period_days=days,
            price_kopecks=price_kopecks,
            purchase_type="telegram_premium",
            country=username,
        )
        await state.update_data(premium_purchase_id=purchase_id)

        logger.info(
            "PREMIUM_PURCHASE_CREATED user=%s purchase_id=%s username=%s period=%s price=%s",
            telegram_id, purchase_id, username, days, price_rubles,
        )

        payload = f"purchase:{purchase_id}"
        months = days // 30
        description = f"Telegram Premium — {months} мес. для {username}"

        prices = [LabeledPrice(label="Telegram Premium", amount=price_kopecks)]

        invoice_msg = await callback.bot.send_invoice(
            chat_id=telegram_id,
            title="Telegram Premium",
            description=description,
            payload=payload,
            provider_token=config.TG_PROVIDER_TOKEN,
            currency="RUB",
            prices=prices,
        )
        await callback.bot.send_message(
            chat_id=telegram_id,
            text=i18n_get_text(language, "payment.invoice_timeout"),
        )
        asyncio.create_task(
            _schedule_invoice_deletion(callback.bot, telegram_id, invoice_msg.message_id)
        )

        await state.set_state(TelegramPremiumState.processing_payment)

        logger.info(
            "PREMIUM_INVOICE_SENT user=%s purchase_id=%s amount=%s",
            telegram_id, purchase_id, price_kopecks,
        )

    except Exception as e:
        logger.exception("PREMIUM_PAYMENT_ERROR user=%s error=%s", telegram_id, e)
        await callback.answer(
            i18n_get_text(language, "errors.payment_processing"), show_alert=True,
        )

    try:
        await callback.answer()
    except Exception:
        pass


@premium_router.callback_query(
    F.data == "premium_pay:international",
    StateFilter(TelegramPremiumState.choose_payment_method),
)
async def callback_premium_pay_international(callback: CallbackQuery, state: FSMContext):
    """Pay for Telegram Premium via international acquiring (Platega)."""
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    data = await state.get_data()
    username = data.get("premium_username")
    days = data.get("premium_period_days")
    price_rubles = data.get("premium_price_rubles")

    if not all([username, days, price_rubles]):
        await callback.answer(i18n_get_text(language, "errors.session_expired"), show_alert=True)
        await state.clear()
        return

    import platega_service
    if not platega_service.is_enabled():
        await callback.answer("Международная оплата временно недоступна", show_alert=True)
        return

    price_kopecks = price_rubles * 100

    try:
        purchase_id = await database.create_pending_purchase(
            telegram_id=telegram_id, tariff="telegram_premium", period_days=days,
            price_kopecks=price_kopecks, purchase_type="telegram_premium", country=username,
        )

        tx_data = await platega_service.create_transaction(
            amount_rubles=float(price_rubles),
            description=f"TgId:{telegram_id} Telegram Premium {days}d @{username.lstrip('@')}",
            purchase_id=purchase_id,
            payment_method=platega_service.PAYMENT_METHOD_INTERNATIONAL,
        )
        try:
            await database.update_pending_purchase_invoice_id(purchase_id, str(tx_data["transaction_id"]))
        except Exception:
            pass

        text = f"🌍 <b>Международная оплата</b>\n\nСумма: {price_rubles:,} ₽\n\n⏳ Перейдите по ссылке для оплаты."
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🌍 Оплатить", url=tx_data["redirect_url"])],
            [InlineKeyboardButton(text=i18n_get_text(language, "premium.back_button"), callback_data="premium_period_back")],
        ])
        await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")
        await callback.answer()
        await state.clear()

    except Exception as e:
        logger.exception("PREMIUM_INTL_ERROR user=%s: %s", telegram_id, e)
        await callback.answer(i18n_get_text(language, "errors.payment_create"), show_alert=True)


# ─── Post-payment: success handler ───
# The existing process_successful_payment in payments_messages.py handles
# finalize_purchase for all TG Payments. We hook into the post-payment
# notification by checking purchase_type == "telegram_premium" there.
#
# Instead, we provide a helper that can be called from process_successful_payment
# to send the Premium-specific success screen + admin notification.

async def send_premium_success(
    bot: Bot,
    telegram_id: int,
    purchase_id: str,
    purchase: dict | None = None,
):
    """
    Called after successful payment for a telegram_premium purchase.
    Sends user confirmation + admin notification.

    Args:
        purchase: Pre-fetched purchase dict (avoids re-querying after status change).
    """
    language = await resolve_user_language(telegram_id)

    if not purchase:
        purchase = await database.get_pending_purchase_by_id(purchase_id)
    if not purchase:
        logger.error("PREMIUM_SUCCESS_NO_PURCHASE purchase_id=%s", purchase_id)
        return

    period_days = purchase.get("period_days", 0)
    price_kopecks = purchase.get("price_kopecks", 0)
    price_rubles = price_kopecks / 100

    plan = PREMIUM_PLANS.get(period_days)
    period_text = plan["period_text"] if plan else f"{period_days} дн."

    # Username stored in `country` field for telegram_premium purchases
    username = purchase.get("country") or "N/A"

    price_str = f"{price_rubles:,.0f}".replace(",", " ")

    # User success message
    text = i18n_get_text(
        language, "premium.success",
        username=username, period=period_text, price=price_str,
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n_get_text(language, "premium.support_button"),
            url="https://t.me/Atlas_SupportSecurity",
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "premium.back_button"),
            callback_data="menu_main",
        )],
    ])
    try:
        await bot.send_message(telegram_id, text, reply_markup=kb, parse_mode="HTML")
    except Exception as e:
        logger.error("PREMIUM_SUCCESS_MSG_FAILED user=%s error=%s", telegram_id, e)

    # Admin notification
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    admin_text = i18n_get_text(
        "ru", "premium.admin_notification",
        buyer_id=telegram_id,
        username=username,
        period=period_text,
        price=price_str,
        date=now_str,
    )
    try:
        await bot.send_message(config.ADMIN_TELEGRAM_ID, admin_text, parse_mode="HTML")
        logger.info(
            "PREMIUM_ADMIN_NOTIFIED buyer=%s username=%s period=%s price=%s",
            telegram_id, username, period_text, price_str,
        )
    except Exception as e:
        logger.error("PREMIUM_ADMIN_NOTIFY_FAILED error=%s", e)
