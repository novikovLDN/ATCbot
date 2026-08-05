"""Финализация VPN-подписки после оплаты через Telegram.

ЧТО ЗДЕСЬ
    Один вызов сервиса — finalize_subscription_payment — и разбор того, чем
    он кончился: подписка выдана, выдача отложена (activation pending) или
    случилась ошибка. Всё, что происходит ПОСЛЕ успешной выдачи (экран
    успеха, комбо-трафик, очистка), живёт в соседних модулях.

ПОЧЕМУ ВЫДЕЛЕНО
    Это единственное место обработчика, где принимается доменное решение о
    подписке, и оно тонуло между выдачей товаров сверху и вёрсткой экрана
    снизу. Здесь же — четыре ветки обработки ошибок, каждая со своим
    сообщением и своим log_handler_exit.

ЧТО ЛЕГКО СЛОМАТЬ
    Инвариант «бот никогда не генерирует VLESS сам». Ключ приходит только
    из API панели; пустой ключ — это RuntimeError, а не повод собрать ключ
    локально. Уберёте проверку — человек получит нерабочую строку и будет
    уверен, что купил доступ.

    Ветка отложенной активации. Она возвращает None так же, как ошибки, —
    но платёж при этом УСПЕШЕН. Не превращайте её в ошибочную: человек
    получит «оплата не прошла» при списанных деньгах.

    Возврат None означает «обработчик обязан выйти»: экран успеха и
    начисления после этого не должны выполняться ни при каких условиях.
"""
import logging
import time
from dataclasses import dataclass
from typing import Any, Optional

from aiogram.fsm.context import FSMContext
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

import config
import database
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.services.payments import service as payment_service
from app.services.payments.exceptions import (
    InvalidPaymentPayloadError,
    PaymentAmountMismatchError,
    PaymentFinalizationError,
)
from app.utils.logging_helpers import log_handler_exit, classify_error
from app.handlers.payments.payment_preflight import PaymentEnvelope, PurchaseContext

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FinalizedSubscription:
    """Результат финализации, разобранный на части.

    Держим и сам result: экран успеха и начисление комбо-трафика читают из
    него флаги (is_combo, is_basic_to_plus_upgrade, referral_reward), а их
    состав меняется в сервисном слое.
    """

    result: Any
    payment_id: Any
    expires_at: Any
    vpn_key: Any
    is_renewal: bool
    subscription_type: str
    vpn_key_plus: Any


async def finalize_subscription(
    message: Message,
    state: FSMContext,
    env: PaymentEnvelope,
    ctx: PurchaseContext,
    start_time: float,
) -> Optional[FinalizedSubscription]:
    """Выдать подписку. None = обработчику надо выйти (ошибка или отложенная
    активация; человеку уже ответили, в лог уже написали)."""
    telegram_id = env.telegram_id
    language = env.language
    is_stars_payment = env.is_stars_payment
    purchase_id = ctx.purchase_id
    tariff_type = ctx.tariff_type
    period_days = ctx.period_days
    payment_amount_rubles = ctx.payment_amount_rubles

    # Finalize subscription payment through payment service
    payment_provider_name = "telegram_stars" if is_stars_payment else "telegram_payment"
    try:
        result = await payment_service.finalize_subscription_payment(
            purchase_id=purchase_id,
            telegram_id=telegram_id,
            payment_provider=payment_provider_name,
            amount_rubles=payment_amount_rubles
        )
        
        payment_id = result.payment_id
        expires_at = result.expires_at
        vpn_key = result.vpn_key
        is_renewal = result.is_renewal
        subscription_type = (getattr(result, "subscription_type", None) or "basic").strip().lower()
        if subscription_type not in config.VALID_SUBSCRIPTION_TYPES:
            subscription_type = "basic"
        vpn_key_plus = getattr(result, "vpn_key_plus", None)
        
        # Проверяем статус активации подписки
        activation_status = result.activation_status
        is_pending_activation = (
            activation_status == "pending" and
            not is_renewal and
            not vpn_key
        )
        
        # Если активация отложена - показываем информационное сообщение
        if is_pending_activation:
            expires_str = expires_at.strftime("%d.%m.%Y") if expires_at else "N/A"
            pending_text = i18n_get_text(language, "payment.pending_activation", date=expires_str)
            
            # Клавиатура с кнопками профиля и поддержки
            pending_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text=i18n_get_text(language, "main.profile"),
                    callback_data="menu_profile"
                )],
                [InlineKeyboardButton(
                    text=i18n_get_text(language, "main.support"),
                    url="https://t.me/atlas_suppbot"
                )]
            ])
            
            # ИДЕМПОТЕНТНОСТЬ: Помечаем ПЕРЕД отправкой, чтобы при краше не было дубля
            try:
                sent = await database.mark_payment_notification_sent(payment_id)
                if not sent:
                    logger.warning(
                        f"NOTIFICATION_FLAG_ALREADY_SET [type=payment_success_pending, payment_id={payment_id}, user={telegram_id}]"
                    )
                    # Already sent — skip to FSM cleanup
                    try:
                        current_state = await state.get_state()
                        if current_state is not None:
                            await state.clear()
                    except Exception:
                        pass
                    return
            except Exception as e:
                logger.error(f"Failed to mark pending activation notification as sent: {e}")

            try:
                await message.answer(
                    pending_text,
                    reply_markup=pending_keyboard,
                    parse_mode="HTML"
                )
                logger.info(
                    f"NOTIFICATION_SENT [type=payment_success_pending, payment_id={payment_id}, user={telegram_id}, purchase_id={purchase_id}, expires_at={expires_str}]"
                )
            except Exception as e:
                logger.error(f"Failed to send pending activation message: user={telegram_id}, error={e}")
            
            # Очищаем FSM state
            try:
                current_state = await state.get_state()
                if current_state is not None:
                    await state.clear()
            except Exception:
                pass
            
            duration_ms = (time.time() - start_time) * 1000
            log_handler_exit(
                handler_name="process_successful_payment",
                outcome="success",
                telegram_id=telegram_id,
                operation="payment_finalization",
                duration_ms=duration_ms,
                activation_status="pending"
            )
            return
        
        # Architecture invariant: Bot never generates VLESS locally.
        # vpn_key must come from XRAY API only.
        if not vpn_key:
            logger.critical(
                "ACTIVATION_FAILED_NO_VPN_KEY",
                extra={"telegram_id": telegram_id}
            )
            raise RuntimeError(
                "VPN activation failed: no vpn_key returned from API."
            )
        
        logger.info(
            f"process_successful_payment: SUBSCRIPTION_ACTIVATED [user={telegram_id}, payment_id={payment_id}, "
            f"purchase_id={purchase_id}, expires_at={expires_at.isoformat()}, is_renewal={is_renewal}, "
            f"vpn_key_length={len(vpn_key) if vpn_key else 0}]"
        )
        
    # Note: PaymentAlreadyProcessedError is no longer raised - service returns existing subscription data
    # If payment was already processed, result contains existing subscription data
        
    except (InvalidPaymentPayloadError, PaymentAmountMismatchError) as e:
        # Payment validation failed
        logger.error(
            f"payment_rejected: provider=telegram_payment, user={telegram_id}, purchase_id={purchase_id}, "
            f"reason={type(e).__name__}, error={str(e)}"
        )
        language = await resolve_user_language(telegram_id)
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
            reason="payment_validation_failed"
        )
        return
        
    except PaymentFinalizationError as e:
        # Payment finalization failed
        error_msg = (
            f"CRITICAL: payment finalization FAILED [user={telegram_id}, purchase_id={purchase_id}, "
            f"tariff={tariff_type}, period_days={period_days}, "
            f"error={str(e)}, error_type={type(e).__name__}]"
        )
        logger.error(error_msg)
        logger.exception(f"process_successful_payment: EXCEPTION_TRACEBACK [user={telegram_id}, purchase_id={purchase_id}]")
        
        language = await resolve_user_language(telegram_id)
        error_text = i18n_get_text(language, "errors.subscription_activation")
        await message.answer(error_text, parse_mode="HTML")
        
        # Log event for admin
        try:
            await database._log_audit_event_atomic_standalone(
                "payment_subscription_activation_failed",
                config.ADMIN_TELEGRAM_ID,
                telegram_id,
                f"Payment received but finalization failed: purchase_id={purchase_id}, error={str(e)}"
            )
        except Exception as log_error:
            logger.error(f"Failed to log audit event: {log_error}")
        
        duration_ms = (time.time() - start_time) * 1000
        error_type = classify_error(e)
        log_handler_exit(
            handler_name="process_successful_payment",
            outcome="failed",
            telegram_id=telegram_id,
            operation="payment_finalization",
            error_type=error_type,
            duration_ms=duration_ms,
            reason="payment_finalization_failed"
        )
        return
        
    except Exception as e:
        # Unexpected error
        error_msg = (
            f"CRITICAL: unexpected error in payment processing [user={telegram_id}, purchase_id={purchase_id}, "
            f"error={str(e)}, error_type={type(e).__name__}]"
        )
        logger.error(error_msg)
        logger.exception(f"process_successful_payment: EXCEPTION_TRACEBACK [user={telegram_id}, purchase_id={purchase_id}]")
        
        language = await resolve_user_language(telegram_id)
        error_text = i18n_get_text(language, "errors.subscription_activation")
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
            reason="unexpected_error"
        )
        return

    return FinalizedSubscription(
        result=result,
        payment_id=payment_id,
        expires_at=expires_at,
        vpn_key=vpn_key,
        is_renewal=is_renewal,
        subscription_type=subscription_type,
        vpn_key_plus=vpn_key_plus,
    )
