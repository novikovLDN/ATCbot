"""Экран успешной оплаты и завершение сценария.

ЧТО ЗДЕСЬ
    announce_success  — лог промокода, защита от повторного уведомления,
                        текст и клавиатура «оплачено», кешбэк рефереру
    finish_payment    — уборка после успеха: промо-сессия, FSM, аудит

ПОЧЕМУ ВЫДЕЛЕНО
    Это вёрстка и уборка, а не платёжная логика: правят её из-за названий
    тарифов и формулировок, а лежала она в том же обработчике, что и вызов
    финализации. Разделение позволяет менять текст, не глядя на деньги.

ЧТО ЛЕГКО СЛОМАТЬ
    Порядок mark-before-send. Флаг «уведомление отправлено» ставится ДО
    отправки сообщения: при падении между ними человек уведомление потеряет,
    но не получит два. Обратный порядок даёт дубли на каждом повторном
    вебхуке Telegram, а повторы — штатная ситуация.

    Возврат False из announce_success. Он означает «уведомление уже ушло
    раньше, дальше по сценарию идти нельзя». Проигнорируете — начисления
    комбо-трафика и очистка FSM выполнятся повторно.
"""
import logging
import time

from aiogram.fsm.context import FSMContext
from aiogram.types import Message

import config
import database
from app.handlers.common.keyboards import get_payment_success_keyboard
from app.handlers.common.utils import clear_promo_session
from app.handlers.notifications import notify_referral_cashback
from app.utils.logging_helpers import log_handler_exit
from app.handlers.payments.payment_preflight import PaymentEnvelope, PurchaseContext
from app.handlers.payments.subscription_finalize import FinalizedSubscription

logger = logging.getLogger(__name__)


async def announce_success(
    message: Message,
    env: PaymentEnvelope,
    ctx: PurchaseContext,
    fin: FinalizedSubscription,
    start_time: float,
) -> bool:
    """Показать экран «оплачено». False = уведомление уже уходило раньше,
    остальной хвост сценария выполнять нельзя.

    Параметр назван с подчёркиванием намеренно: ниже он читается внутри
    try/except NameError — так было в исходном обработчике, где переменная
    могла оказаться неприсвоенной. Сейчас она есть всегда, но поведение
    оставлено как было.
    """
    promo_code_used = ctx.promo_code_used
    tariff_type = ctx.tariff_type
    period_days = ctx.period_days
    payment_amount_rubles = ctx.payment_amount_rubles
    telegram_id = env.telegram_id
    language = env.language
    _degradation_notice = env.degradation_notice
    result = fin.result
    payment_id = fin.payment_id
    expires_at = fin.expires_at
    is_renewal = fin.is_renewal
    subscription_type = fin.subscription_type
    purchase_id = ctx.purchase_id

    # Промокод уже потреблен в finalize_purchase внутри транзакции
    # Здесь только логируем использование для статистики
    if promo_code_used:
        try:
            promo_data = await database.get_promo_code(promo_code_used)
            if promo_data:
                discount_percent = promo_data["discount_percent"]
                base_price = config.TARIFFS[tariff_type][period_days]["price"]
                await database.log_promo_code_usage(
                    promo_code=promo_code_used,
                    telegram_id=telegram_id,
                    tariff=f"{tariff_type}_{period_days}",
                    discount_percent=discount_percent,
                    price_before=base_price,
                    price_after=payment_amount_rubles
                )
        except Exception as e:
            logger.error(f"Error logging promocode usage: {e}")

    # КРИТИЧНО: VPN ключ уже валидирован в finalize_purchase
    # Здесь только отправка пользователю - это атомарная операция после успешного платежа
    expires_str = expires_at.strftime("%d.%m.%Y")
    
    # ИДЕМПОТЕНТНОСТЬ: Проверяем, было ли уже отправлено уведомление
    notification_already_sent = await database.is_payment_notification_sent(payment_id)
    
    if notification_already_sent:
        logger.info(
            f"NOTIFICATION_IDEMPOTENT_SKIP [type=payment_success, payment_id={payment_id}, user={telegram_id}, "
            f"purchase_id={purchase_id}]"
        )
        duration_ms = (time.time() - start_time) * 1000
        log_handler_exit(
            handler_name="process_successful_payment",
            outcome="success",
            telegram_id=telegram_id,
            operation="payment_finalization",
            duration_ms=duration_ms,
            reason="idempotent_skip"
        )
        return False
    
    # Один компактный экран: текст + кнопки копирования и профиль (без отдельной отправки ключей)
    is_upgrade = getattr(result, "is_basic_to_plus_upgrade", False)
    if is_upgrade:
        _is_combo_purchase = getattr(result, "is_combo", False)
        if _is_combo_purchase:
            upgrade_label = "Комбо Plus" if subscription_type == "plus" else "Комбо Basic"
        else:
            upgrade_label = "Plus" if subscription_type == "plus" else "Basic"
        text = (
            f"✅ Ваш тариф изменён на <b>{upgrade_label}</b>\n"
            f"📅 До: {expires_str}"
        )
        keyboard = get_payment_success_keyboard(language, subscription_type="plus", is_renewal=True)
        try:
            await message.answer(text, reply_markup=keyboard, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Failed to send upgrade message: user={telegram_id}, error={e}")
    else:
        # Determine if this is a combo purchase from finalize result (reliable)
        _is_combo_purchase = getattr(result, "is_combo", False)

        if config.is_biz_tariff(subscription_type):
            tariff_label, tariff_emoji = "Business", "🏢"
        elif subscription_type == "plus" and _is_combo_purchase:
            tariff_label, tariff_emoji = "Комбо Plus", "🚀"
        elif subscription_type == "plus":
            tariff_label, tariff_emoji = "Plus", "💎"
        elif _is_combo_purchase:
            tariff_label, tariff_emoji = "Комбо Basic", "🚀"
        else:
            tariff_label, tariff_emoji = "Basic", "🏆"

        # Build period string
        period_str = ""
        if period_days:
            if period_days == 30:
                period_str = "1 месяц"
            elif period_days == 90:
                period_str = "3 месяца"
            elif period_days == 180:
                period_str = "6 месяцев"
            elif period_days == 365:
                period_str = "1 год"
            else:
                period_str = f"{period_days} дней"

        from app.i18n import get_text as _i18n_get
        from app.handlers.common.keyboards import MINI_APP_URL
        if is_renewal:
            text = _i18n_get(language, "purchase.success_renewal",
                             tariff_name=f"{tariff_emoji} {tariff_label}",
                             period=period_str,
                             expires_date=expires_str)
        else:
            text = _i18n_get(language, "purchase.success_first",
                             tariff_name=f"{tariff_emoji} {tariff_label}",
                             period=period_str,
                             expires_date=expires_str)
        keyboard = get_payment_success_keyboard(language, subscription_type=subscription_type, is_renewal=is_renewal)
        # ИДЕМПОТЕНТНОСТЬ: Помечаем ПЕРЕД отправкой (mark-before-send pattern)
        # При краше между mark и send — уведомление потеряно, но не дублировано
        try:
            sent = await database.mark_payment_notification_sent(payment_id)
            if not sent:
                logger.warning(
                    f"NOTIFICATION_FLAG_ALREADY_SET [type=payment_success, payment_id={payment_id}, user={telegram_id}]"
                )
                # Already sent by concurrent handler — skip
                return False
        except Exception as e:
            logger.error(
                f"CRITICAL: Failed to mark notification as sent: payment_id={payment_id}, user={telegram_id}, error={e}"
            )

        try:
            degradation = ""
            try:
                if _degradation_notice:
                    degradation = "\n\n⏳ Возможны небольшие задержки"
            except NameError:
                pass
            await message.answer(text + degradation, reply_markup=keyboard, parse_mode="HTML")
            logger.info(
                f"NOTIFICATION_SENT [type=payment_success, payment_id={payment_id}, user={telegram_id}, "
                f"purchase_id={purchase_id}]"
            )
        except Exception as e:
            logger.error(f"Failed to send payment success message: user={telegram_id}, error={e}")
            try:
                await message.answer(text, reply_markup=keyboard, parse_mode="HTML")
            except Exception as fallback_err:
                logger.error(f"Fallback also failed: user={telegram_id}, error={fallback_err}")

    logger.info(
        f"process_successful_payment: VPN_KEY_SENT [user={telegram_id}, payment_id={payment_id}, "
        f"purchase_id={purchase_id}, expires_at={expires_str}, subscription_type={subscription_type}]"
    )

    # Кешбэк рефереру начислен внутри finalize_purchase (process_referral_reward),
    # но сообщение отправляет вызывающий код. Формат периода и разбор словаря —
    # в общем хелпере, чтобы все пути оплаты слали одинаковый текст.
    await notify_referral_cashback(
        message.bot,
        result.referral_reward,
        referred_id=telegram_id,
        purchase_amount=payment_amount_rubles,
        action_type="purchase",
        period_days=period_days,
        context=f"telegram_payment:{purchase_id}",
    )

    
    logger.info(
        f"process_successful_payment: PAYMENT_COMPLETE [user={telegram_id}, payment_id={payment_id}, "
        f"tariff={tariff_type}, period_days={period_days}, amount={payment_amount_rubles} RUB, "
        f"purchase_id={purchase_id}, expires_at={expires_str}, vpn_key_sent=True, subscription_visible=True]"
    )

    return True


async def finish_payment(
    state: FSMContext,
    env: PaymentEnvelope,
    ctx: PurchaseContext,
    fin: FinalizedSubscription,
    start_time: float,
) -> None:
    """Убрать за собой: промо-сессия, FSM, запись в аудит, выходной лог.

    Всё здесь обёрнуто в try: платёж уже состоялся и товар выдан, ронять
    обработчик из-за неудачной уборки нельзя — Telegram повторит событие, и
    выдача пойдёт по второму кругу.
    """
    telegram_id = env.telegram_id
    payload = env.payload
    purchase_id = ctx.purchase_id
    payment_amount_rubles = ctx.payment_amount_rubles
    payment_id = fin.payment_id

    # КРИТИЧНО: Удаляем промо-сессию после успешной оплаты
    await clear_promo_session(state)

    # КРИТИЧНО: Очищаем FSM state после успешной активации подписки
    try:
        current_state = await state.get_state()
        if current_state is not None:
            await state.clear()
            logger.debug(f"FSM state cleared after successful payment: user={telegram_id}, was_state={current_state}")
    except Exception as e:
        logger.debug(f"FSM state clear failed (may be already clear): {e}")
    
    # Логируем событие
    try:
        await database._log_audit_event_atomic_standalone(
            "telegram_payment_successful",
            config.ADMIN_TELEGRAM_ID,
            telegram_id,
            f"Telegram payment successful: payment_id={payment_id}, payload={payload}, amount={payment_amount_rubles} RUB, purchase_id={purchase_id}, vpn_key_sent=True"
        )
    except Exception as e:
        logger.error(f"Failed to log audit event: {e}")
    
    # STEP 2 — OBSERVABILITY: Structured logging for handler exit (success)
    # PART E — SLO SIGNAL IDENTIFICATION: Payment success rate
    # This handler exit log (outcome="success") is an SLO signal for payment success rate.
    # Track: outcome="success" vs outcome="failed" for payment_finalization operations.
    duration_ms = (time.time() - start_time) * 1000
    log_handler_exit(
        handler_name="process_successful_payment",
        outcome="success",
        telegram_id=telegram_id,
        operation="payment_finalization",
        duration_ms=duration_ms,
        payment_id=payment_id,
        purchase_id=purchase_id
    )
