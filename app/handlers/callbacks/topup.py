"""Пополнение внутреннего баланса.

Отличие от оплаты подписки: здесь покупка создаётся с
purchase_type='balance_topup', и после подтверждения деньги зачисляются на
баланс, а не выдаётся товар. Именно этот момент считается выручкой —
дальнейшие траты с баланса уже внутреннее движение (см.
database/analytics.py, REVENUE_EXTERNAL_ONLY_SQL).
"""
import asyncio
import logging
import math
import time

import config
import database
from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice, Message
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext

from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.services.subscriptions import service as subscription_service
from app.services.subscriptions.service import is_subscription_active
from app.handlers.notifications import notify_referral_cashback
from app.core.rate_limit import check_rate_limit
from app.handlers.common.guards import ensure_db_ready_callback, ensure_db_ready_message
from app.handlers.common.utils import (
    safe_edit_text,
    safe_edit_reply_markup,
    get_promo_session,
    clear_promo_session,
    sanitize_display_name,
)
from app.handlers.common.keyboards import (
    get_profile_keyboard,
    get_payment_success_keyboard,
)
from app.handlers.common.screens import show_profile
from app.handlers.common.states import TopUpStates, WithdrawStates, PurchaseState

# Автоудаление инвойса — общее для всех платёжных экранов.
from app.handlers.callbacks._invoice_cleanup import (
    INVOICE_TIMEOUT,
    _schedule_invoice_deletion,
)

topup_router = Router()
logger = logging.getLogger(__name__)


@topup_router.callback_query(F.data.startswith("topup_sbp:"))
async def callback_topup_sbp(callback: CallbackQuery):
    """Пополнение баланса через СБП (Platega.io, +11%)"""
    if not await ensure_db_ready_callback(callback):
        return

    telegram_id = callback.from_user.id

    is_allowed, rate_limit_message = check_rate_limit(telegram_id, "payment_init")
    if not is_allowed:
        language = await resolve_user_language(telegram_id)
        await callback.answer(rate_limit_message or i18n_get_text(language, "common.rate_limit_message"), show_alert=True)
        return
    language = await resolve_user_language(telegram_id)

    amount_str = callback.data.split(":")[1]
    try:
        amount = int(amount_str)
    except ValueError:
        await callback.answer(i18n_get_text(language, "errors.invalid_amount"), show_alert=True)
        return

    if amount <= 0 or amount > 100000:
        await callback.answer(i18n_get_text(language, "errors.invalid_amount"), show_alert=True)
        return

    import platega_service
    if not platega_service.is_enabled():
        await callback.answer(i18n_get_text(language, "payment.sbp_unavailable"), show_alert=True)
        return

    try:
        # Применяем наценку +11%
        amount_kopecks = amount * 100
        sbp_amount_kopecks = platega_service.apply_sbp_markup(amount_kopecks)
        sbp_amount_rubles = sbp_amount_kopecks / 100.0

        purchase_id = await subscription_service.create_balance_topup_purchase(
            telegram_id=telegram_id,
            amount_kopecks=sbp_amount_kopecks,
            currency="RUB"
        )

        tx_data = await platega_service.create_transaction(
            amount_rubles=sbp_amount_rubles,
            description=f"Пополнение баланса на {amount} ₽",
            purchase_id=purchase_id,
        )

        transaction_id = tx_data["transaction_id"]
        redirect_url = tx_data["redirect_url"]

        try:
            await database.update_pending_purchase_invoice_id(purchase_id, str(transaction_id))
        except Exception as e:
            logger.error(f"Failed to save transaction_id to DB: purchase_id={purchase_id}, error={e}")

        logger.info(
            f"balance_topup_invoice_created: provider=platega, user={telegram_id}, "
            f"purchase_id={purchase_id}, base_amount={amount}, sbp_amount={sbp_amount_rubles:.2f}"
        )

        text = i18n_get_text(language, "payment.sbp_waiting", amount=sbp_amount_rubles)

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=i18n_get_text(language, "payment.sbp_pay_button"),
                url=redirect_url
            )],
            [InlineKeyboardButton(
                text=i18n_get_text(language, "common.back"),
                callback_data="topup_balance"
            )]
        ])

        await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")
        await callback.answer()

    except Exception as e:
        logger.exception(f"Error creating Platega SBP transaction for balance top-up: {e}")
        await callback.answer(i18n_get_text(language, "errors.payment_create"), show_alert=True)


@topup_router.callback_query(F.data.startswith("topup_lava:"))
async def callback_topup_lava(callback: CallbackQuery):
    """Пополнение баланса через Lava (карта)"""
    if not await ensure_db_ready_callback(callback):
        return

    telegram_id = callback.from_user.id

    is_allowed, rate_limit_message = check_rate_limit(telegram_id, "payment_init")
    if not is_allowed:
        language = await resolve_user_language(telegram_id)
        await callback.answer(rate_limit_message or i18n_get_text(language, "common.rate_limit_message"), show_alert=True)
        return
    language = await resolve_user_language(telegram_id)

    amount_str = callback.data.split(":")[1]
    try:
        amount = int(amount_str)
    except ValueError:
        await callback.answer(i18n_get_text(language, "errors.invalid_amount"), show_alert=True)
        return

    if amount <= 0 or amount > 100000:
        await callback.answer(i18n_get_text(language, "errors.invalid_amount"), show_alert=True)
        return

    import lava_service
    if not lava_service.is_enabled():
        await callback.answer(i18n_get_text(language, "payment.lava_unavailable"), show_alert=True)
        return

    try:
        amount_kopecks = amount * 100
        amount_rubles = float(amount)

        purchase_id = await subscription_service.create_balance_topup_purchase(
            telegram_id=telegram_id,
            amount_kopecks=amount_kopecks,
            currency="RUB"
        )

        invoice_data = await lava_service.create_invoice(
            amount_rubles=amount_rubles,
            purchase_id=purchase_id,
            comment=f"Пополнение баланса на {amount} ₽",
        )

        invoice_id = invoice_data["invoice_id"]
        payment_url = invoice_data["payment_url"]

        try:
            await database.update_pending_purchase_invoice_id(purchase_id, str(invoice_id))
        except Exception as e:
            logger.error(f"Failed to save lava invoice_id to DB: purchase_id={purchase_id}, error={e}")

        logger.info(
            f"balance_topup_invoice_created: provider=lava, user={telegram_id}, "
            f"purchase_id={purchase_id}, amount={amount_rubles:.2f}"
        )

        text = i18n_get_text(language, "payment.lava_waiting", amount=amount_rubles)

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=i18n_get_text(language, "payment.lava_pay_button"),
                url=payment_url
            )],
            [InlineKeyboardButton(
                text=i18n_get_text(language, "common.back"),
                callback_data="topup_balance"
            )]
        ])

        lava_msg = await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")
        asyncio.create_task(_schedule_invoice_deletion(callback.bot, telegram_id, lava_msg.message_id))
        await callback.answer()

    except Exception as e:
        logger.exception(f"Error creating Lava invoice for balance top-up: {e}")
        await callback.answer(i18n_get_text(language, "errors.payment_create"), show_alert=True)


@topup_router.callback_query(F.data.startswith("topup_card:"))
async def callback_topup_card(callback: CallbackQuery):
    """Оплата пополнения баланса картой"""
    if not await ensure_db_ready_callback(callback):
        return
    telegram_id = callback.from_user.id

    is_allowed, rate_limit_message = check_rate_limit(telegram_id, "payment_init")
    if not is_allowed:
        language = await resolve_user_language(telegram_id)
        await callback.answer(rate_limit_message or i18n_get_text(language, "common.rate_limit_message"), show_alert=True)
        return
    language = await resolve_user_language(telegram_id)
    
    amount_str = callback.data.split(":")[1]
    try:
        amount = int(amount_str)
    except ValueError:
        await callback.answer(i18n_get_text(language, "errors.invalid_amount"), show_alert=True)
        return
    
    if amount <= 0 or amount > 100000:
        await callback.answer(i18n_get_text(language, "errors.invalid_amount"), show_alert=True)
        return
    
    # Создаем invoice через Telegram Payments
    timestamp = int(time.time())
    payload = f"balance_topup_{telegram_id}_{amount}_{timestamp}"
    amount_kopecks = amount * 100
    
    try:
        invoice_msg = await callback.bot.send_invoice(
            chat_id=telegram_id,
            title=i18n_get_text(language, "main.topup_invoice_title"),
            description=i18n_get_text(language, "main.topup_invoice_description", amount=amount),
            payload=payload,
            provider_token=config.TG_PROVIDER_TOKEN,
            currency="RUB",
            prices=[LabeledPrice(label=i18n_get_text(language, "main.topup_invoice_label"), amount=amount_kopecks)]
        )
        await callback.bot.send_message(chat_id=telegram_id, text=i18n_get_text(language, "payment.invoice_timeout"), parse_mode="HTML")
        asyncio.create_task(_schedule_invoice_deletion(callback.bot, telegram_id, invoice_msg.message_id))
        await callback.answer()
    except Exception as e:
        logger.exception(f"Error sending invoice for balance topup: {e}")
        await callback.answer(i18n_get_text(language, "errors.payment_create"), show_alert=True)
