"""Пополнение баланса, оплаченное через Telegram Payments или Stars.

ЧТО ЗДЕСЬ
    Одна функция: зачислить деньги на внутренний баланс и сказать об этом
    человеку. Подписки, товары и промокоды сюда не заходят — этот путь
    заканчивается зачислением и уведомлением рефереру о кешбэке.

ПОЧЕМУ ВЫДЕЛЕНО
    В обработчике successful_payment это была ветка на 135 строк внутри
    try/except, из-за которой основной путь (покупка) начинался лишь после
    неё. Ветка самодостаточна: все её выходы — return.

ЧТО ЛЕГКО СЛОМАТЬ
    Идемпотентность. Telegram может прислать событие оплаты повторно, и
    защита тут двухслойная: provider_charge_id уникален у Telegram (по нему
    сервис не зачислит второй раз), а флаг уведомления ставится ПЕРЕД
    отправкой сообщения. Поменяете местами mark и send — при падении между
    ними человек получит второе «баланс пополнен».

    Сумма для Stars. total_amount у Stars — это количество звёзд, а не
    копейки. Рубли берутся из payload; посчитаете как /100 — зачислите
    человеку случайное число.

    Вызывать эту функцию нужно ВНУТРИ того же try, что и разбор payload:
    её исключения обязаны попасть в те же except-ветки, что и раньше.
"""
import logging
import time

from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

import database
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.services.payments import service as payment_service
from app.services.payments.exceptions import PaymentFinalizationError
from app.utils.logging_helpers import log_handler_exit, classify_error
from app.handlers.notifications import notify_referral_cashback
from app.handlers.payments.payment_preflight import PaymentEnvelope

logger = logging.getLogger(__name__)


async def deliver_balance_topup(
    message: Message,
    env: PaymentEnvelope,
    payload_info,
    start_time: float,
) -> None:
    """Зачислить пополнение и уведомить. Все выходы равнозначны: вернулись —
    значит обработчик успешной оплаты обязан завершиться."""
    telegram_id = env.telegram_id
    language = env.language
    payment = env.payment
    is_stars_payment = env.is_stars_payment

    # Пополнение баланса - используем payment service
    # Для Stars: используем рублёвую сумму из payload (Stars — это конвертация, баланс в рублях)
    # Для RUB: total_amount в копейках, делим на 100
    if is_stars_payment:
        payment_amount_rubles = payload_info.amount if payload_info.amount else payment.total_amount
    else:
        payment_amount_rubles = payment.total_amount / 100.0
    
    # КРИТИЧНО: Извлекаем provider_charge_id для идемпотентности
    # Telegram гарантирует уникальность telegram_payment_charge_id
    provider_charge_id = getattr(payment, 'telegram_payment_charge_id', None)
    if not provider_charge_id:
        logger.error(
            f"BALANCE_TOPUP_MISSING_CHARGE_ID [user={telegram_id}, "
            f"payment_total={payment.total_amount}, correlation_id={message.message_id}]"
        )
        error_text = i18n_get_text(language, "errors.payment_processing")
        await message.answer(error_text, parse_mode="HTML")
        return
    
    topup_provider = "telegram_stars" if is_stars_payment else "telegram"
    topup_description = (
        "Пополнение баланса через Telegram Stars"
        if is_stars_payment
        else "Пополнение баланса через Telegram Payments"
    )
    try:
        result = await payment_service.finalize_balance_topup_payment(
            telegram_id=telegram_id,
            amount_rubles=payment_amount_rubles,
            provider=topup_provider,
            provider_charge_id=provider_charge_id,
            description=topup_description,
            correlation_id=str(message.message_id)
        )
    except PaymentFinalizationError as e:
        logger.error(f"Balance topup finalization failed: user={telegram_id}, error={e}")
        error_text = i18n_get_text(language, "errors.payment_processing")
        await message.answer(error_text, parse_mode="HTML")
        duration_ms = (time.time() - start_time) * 1000
        error_type = classify_error(e)
        log_handler_exit(
            handler_name="process_successful_payment",
            outcome="failed",
            telegram_id=telegram_id,
            operation="payment_finalization",
            error_type=error_type,
            duration_ms=duration_ms,
            payment_type="balance_topup"
        )
        return
    
    # Извлекаем результаты
    payment_id = result.payment_id
    new_balance = result.new_balance
    referral_reward_result = result.referral_reward
    
    # ИДЕМПОТЕНТНОСТЬ: Проверяем, было ли уже отправлено уведомление
    notification_already_sent = await database.is_payment_notification_sent(payment_id)
    
    if notification_already_sent:
        logger.info(
            f"NOTIFICATION_IDEMPOTENT_SKIP [type=balance_topup, payment_id={payment_id}, user={telegram_id}]"
        )
        return
    
    # Получаем язык пользователя для сообщения
    language = await resolve_user_language(telegram_id)
    
    # Отправляем сообщение об успешном пополнении
    text = i18n_get_text(language, "main.topup_balance_success", balance=new_balance)
    
    # Создаем inline клавиатуру для UX.
    # Третий позиционный аргумент get_text убран — см. комментарий
    # выше, в ветке «сервис недоступен».
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n_get_text(language, "buy.renew_button"),
            callback_data="menu_buy_vpn"
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "main.profile"),
            callback_data="menu_profile"
        )]
    ])
    
    # ИДЕМПОТЕНТНОСТЬ: Помечаем ПЕРЕД отправкой, чтобы при краше между send и mark
    # не было дубля уведомления. Лучше потерять уведомление, чем отправить дважды.
    try:
        sent = await database.mark_payment_notification_sent(payment_id)
        if not sent:
            logger.warning(
                f"NOTIFICATION_FLAG_ALREADY_SET [type=balance_topup, payment_id={payment_id}, user={telegram_id}]"
            )
            return  # Already sent by another handler/retry
    except Exception as e:
        logger.error(
            f"CRITICAL: Failed to mark notification as sent: payment_id={payment_id}, user={telegram_id}, error={e}"
        )
        # Continue to send — better to risk duplicate than to lose notification entirely

    try:
        await message.answer(text, reply_markup=keyboard, parse_mode="HTML")
        logger.info(
            f"NOTIFICATION_SENT [type=balance_topup, payment_id={payment_id}, user={telegram_id}]"
        )
    except Exception as e:
        logger.error(
            f"NOTIFICATION_SEND_FAILED [type=balance_topup, payment_id={payment_id}, "
            f"user={telegram_id}, error={e}] (notification flagged but message not delivered)"
        )
    
    # Уведомление о кешбэке рефереру — тот же хелпер, что и на
    # остальных путях оплаты (см. app/handlers/notifications.py).
    await notify_referral_cashback(
        message.bot,
        referral_reward_result,
        referred_id=telegram_id,
        purchase_amount=payment_amount_rubles,
        action_type="topup",
        context="telegram_payment:balance_topup",
    )

    # Логируем событие
    logger.info(f"Balance topup successful: user={telegram_id}, amount={payment_amount_rubles} RUB, new_balance={new_balance} RUB")
    duration_ms = (time.time() - start_time) * 1000
    log_handler_exit(
        handler_name="process_successful_payment",
        outcome="success",
        telegram_id=telegram_id,
        operation="payment_finalization",
        duration_ms=duration_ms,
        payment_type="balance_topup"
    )
    return
