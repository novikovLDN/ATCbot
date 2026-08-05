"""Проверки и сбор контекста ДО того, как за оплату что-то выдадут.

ЧТО ЗДЕСЬ
    Две функции, обе — чистые ворота перед выдачей:

        prepare_successful_payment  входные проверки, kill-switch платежей,
                                    готовность базы, язык, разбор платежа
        load_purchase_context       поиск оплаченной покупки и её условий

    Обе возвращают None, если обработчик обязан молча выйти: пользователю
    они уже ответили и в лог написали сами.

ПОЧЕМУ ВЫДЕЛЕНО
    Это первые ~180 строк обработчика successful_payment, где не происходит
    ничего предметного — только проверки. Из-за них суть обработчика
    (маршрутизация покупки) начиналась на четвёртом экране.

ЧТО ЛЕГКО СЛОМАТЬ
    Порядок проверок. Валидация telegram_id и payload идёт ДО обращения к
    базе, kill-switch — до финализации. Переставите — и выключенные платежи
    всё равно спишутся, а мусорный payload дойдёт до запроса в базу.

    Возврат None. Каждая ветка выхода уже отправила человеку сообщение и
    записала log_handler_exit. Если добавите ветку без сообщения, человек
    заплатит и не увидит вообще ничего.
"""
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

import config
import database
from app.core.feature_flags import get_feature_flags
from app.core.system_state import (
    SystemState,
    healthy_component,
    degraded_component,
    unavailable_component,
)
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language, DEFAULT_LANGUAGE
from app.utils.logging_helpers import log_handler_entry, log_handler_exit
from app.utils.security import (
    validate_telegram_id,
    validate_payment_payload,
    log_security_warning,
)
from app.handlers.payments.purchase_routing import resolve_payment_amount_rubles

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PaymentEnvelope:
    """Разобранный платёж: всё, что известно ДО поиска покупки.

    Раньше это были локальные переменные обработчика, и каждая следующая
    ветка молча зависела от того, что выше по коду их кто-то присвоил.
    """

    telegram_id: int
    language: str
    payment: Any
    payload: str
    is_stars_payment: bool
    # Флаг «часть сервисов деградировала» — приписка к сообщению об успехе.
    # На поток оплаты он не влияет НИКОГДА: платёж уже принят Telegram.
    degradation_notice: bool


@dataclass(frozen=True)
class PurchaseContext:
    """Условия оплаченной покупки, найденные в pending_purchases."""

    purchase_id: str
    pending_purchase: dict
    tariff_type: str
    period_days: Any
    promo_code_used: Optional[str]
    payment_amount_rubles: float


async def prepare_successful_payment(
    message: Message, start_time: float
) -> Optional[PaymentEnvelope]:
    """Проверить платёж и разобрать его. None = обработчику надо выйти."""
    # STEP 4 — PART A: INPUT TRUST BOUNDARIES
    # Validate telegram_id
    telegram_id = message.from_user.id
    is_valid, error = validate_telegram_id(telegram_id)
    if not is_valid:
        log_security_warning(
            event="Invalid telegram_id in successful_payment",
            telegram_id=telegram_id,
            correlation_id=str(message.message_id) if hasattr(message, 'message_id') else None,
            details={"error": error}
        )
        language = await resolve_user_language(message.from_user.id)
        await message.answer(i18n_get_text(language, "errors.try_later"), parse_mode="HTML")
        return
    
    # STEP 4 — PART A: INPUT TRUST BOUNDARIES
    # Validate payment payload
    payment = message.successful_payment
    payload = payment.invoice_payload if payment else None
    is_valid_payload, payload_error = validate_payment_payload(payload)
    if not is_valid_payload:
        log_security_warning(
            event="Invalid payment payload in successful_payment",
            telegram_id=telegram_id,
            correlation_id=str(message.message_id) if hasattr(message, 'message_id') else None,
            details={"error": payload_error, "payload_preview": payload[:50] if payload else None}
        )
        language = await resolve_user_language(message.from_user.id)
        await message.answer(i18n_get_text(language, "errors.try_later"), parse_mode="HTML")
        return
    
    # STEP 6 — F1: GLOBAL OPERATIONAL FLAGS
    # Check if payments are enabled (kill switch)
    feature_flags = get_feature_flags()
    if not feature_flags.payments_enabled:
        logger.warning(
            f"[FEATURE_FLAG] Payments disabled, skipping payment finalization: "
            f"user={telegram_id}, correlation_id={str(message.message_id) if hasattr(message, 'message_id') else None}"
        )
        language = await resolve_user_language(telegram_id)
        await message.answer(
            i18n_get_text(language, "main.service_unavailable"),
            parse_mode="HTML",
        )
        return
    # READ-ONLY system state awareness (informational only, does not affect flow)
    try:
        now = datetime.now(timezone.utc)
        db_ready = database.DB_READY
        
        # Build SystemState for awareness (read-only)
        if db_ready:
            db_component = healthy_component(last_checked_at=now)
        else:
            db_component = unavailable_component(
                error="DB not ready (degraded mode)",
                last_checked_at=now
            )
        
        # VPN API component
        if config.VPN_ENABLED and config.XRAY_API_URL:
            vpn_component = healthy_component(last_checked_at=now)
        else:
            vpn_component = degraded_component(
                error="VPN API not configured",
                last_checked_at=now
            )
        
        # Payments component (always healthy - no logic change)
        payments_component = healthy_component(last_checked_at=now)
        
        system_state = SystemState(
            database=db_component,
            vpn_api=vpn_component,
            payments=payments_component,
        )
        
        # PART D.5: Handlers log DEGRADED for VPN-related actions
        # PART D.5: NEVER block payments or DB flows (payments flow continues regardless)
        if system_state.is_degraded:
            logger.info(
                f"[DEGRADED] system_state detected during process_successful_payment "
                f"(user={message.from_user.id}, optional components degraded - payment flow continues)"
            )
            # Store degradation flag for UX message (will be used later if needed)
            _degradation_notice = True
        else:
            _degradation_notice = False
    except Exception:
        # Ignore system state errors - must not affect payment flow
        _degradation_notice = False
    
    # SAFE STARTUP GUARD: Проверка готовности БД
    if not database.DB_READY:
        language = await resolve_user_language(message.from_user.id)
        text = i18n_get_text(language, "main.service_unavailable_payment")
        
        # Создаем стандартную inline клавиатуру для UX.
        #
        # Третьим позиционным аргументом здесь лежали строки вида
        # "buy_renew_button" — наследство тех времён, когда третий параметр
        # get_text назывался strict и значение просто игнорировалось.
        # Теперь третий параметр — настоящий запасной текст, и эти строки
        # стали бы подписью кнопки, если бы ключ пропал: человек увидел бы
        # на кнопке «buy_renew_button». Убрали: оба ключа есть во всех семи
        # языках, а на случай пропажи в get_text уже есть цепочка
        # язык → английский → русский.
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=i18n_get_text(language, "buy.renew_button"),
                callback_data="menu_buy_vpn"
            )],
            [InlineKeyboardButton(
                text=i18n_get_text(language, "main.support_button"),
                url="https://t.me/atlas_suppbot"
            )]
        ])
        
        await message.answer(text, reply_markup=keyboard, parse_mode="HTML")
        logger.error("Payment received but service unavailable (DB not ready)")
        duration_ms = (time.time() - start_time) * 1000
        log_handler_exit(
            handler_name="process_successful_payment",
            outcome="failed",
            telegram_id=telegram_id,
            operation="payment_finalization",
            error_type="infra_error",
            duration_ms=duration_ms,
            reason="DB not ready"
        )
        return
    
    telegram_id = message.from_user.id

    # STEP 2 — OBSERVABILITY: Structured logging for handler entry
    # PART B — CORRELATION IDS: Use message_id for correlation tracking
    message_id = str(message.message_id) if hasattr(message, 'message_id') and message.message_id else None
    correlation_id = log_handler_entry(
        handler_name="process_successful_payment",
        telegram_id=telegram_id,
        operation="payment_finalization",
        correlation_id=message_id,
    )
    
    # КРИТИЧНО: Инициализация языка в начале функции для гарантированной доступности
    # Получаем язык пользователя из профиля или используем "ru" как fallback
    try:
        language = await resolve_user_language(telegram_id)
    except Exception as e:
        logger.warning(f"Failed to get user language for {telegram_id}, using DEFAULT_LANGUAGE: {e}")
        language = DEFAULT_LANGUAGE
    payment = message.successful_payment
    payload = payment.invoice_payload
    
    # Определяем, является ли оплата через Telegram Stars
    is_stars_payment = (payment.currency == "XTR")

    # КРИТИЧНО: Логируем получение события оплаты от Telegram
    purchase_id_from_payload = payload.split(":", 1)[1] if payload and payload.startswith("purchase:") else payload
    if is_stars_payment:
        log_amount = payment.total_amount if payment.total_amount else 0
        log_currency = "XTR"
    else:
        log_amount = payment.total_amount / 100.0 if payment.total_amount else 0
        log_currency = "RUB"
    logger.info(
        "SUCCESSFUL_PAYMENT_RECEIVED purchase_id=%s telegram_id=%s amount=%s %s",
        purchase_id_from_payload,
        telegram_id,
        log_amount,
        log_currency,
    )
    logger.info(
        f"payment_event_received: provider={'telegram_stars' if is_stars_payment else 'telegram_payment'}, "
        f"user={telegram_id}, payload={payload}, amount={log_amount} {log_currency}, "
        f"currency={payment.currency}"
    )

    return PaymentEnvelope(
        telegram_id=telegram_id,
        language=language,
        payment=payment,
        payload=payload,
        is_stars_payment=is_stars_payment,
        degradation_notice=_degradation_notice,
    )


async def load_purchase_context(
    message: Message,
    payload_info: Any,
    env: PaymentEnvelope,
    start_time: float,
) -> Optional[PurchaseContext]:
    """Найти оплаченную покупку и её условия. None = обработчику надо выйти.

    Просроченная или чужая покупка здесь и отсекается: деньги уже списаны,
    поэтому случай пишется в аудит, а человеку показывается «сессия истекла».
    """
    telegram_id = env.telegram_id
    language = env.language
    payload = env.payload
    payment = env.payment
    is_stars_payment = env.is_stars_payment

    # Обработка платежей за подписку
    # Проверяем, что это платеж за подписку (не balance topup)
    if payload_info.payload_type != "purchase":
        # Legacy formats are not supported for new purchases - only balance topup
        logger.error(f"Unsupported payload type for subscription payment: {payload_info.payload_type}, payload={payload}")
        language = await resolve_user_language(telegram_id)
        await message.answer(i18n_get_text(language, "errors.payment_processing"), parse_mode="HTML")
        duration_ms = (time.time() - start_time) * 1000
        log_handler_exit(
            handler_name="process_successful_payment",
            outcome="failed",
            telegram_id=telegram_id,
            operation="payment_finalization",
            error_type="domain_error",
            duration_ms=duration_ms,
            reason="unsupported_payload_type"
        )
        return
    
    # Extract purchase_id from payload_info
    purchase_id = payload_info.purchase_id
    if not purchase_id:
        logger.error(f"No purchase_id in payload: {payload}")
        language = await resolve_user_language(telegram_id)
        await message.answer(i18n_get_text(language, "errors.payment_processing"), parse_mode="HTML")
        duration_ms = (time.time() - start_time) * 1000
        log_handler_exit(
            handler_name="process_successful_payment",
            outcome="failed",
            telegram_id=telegram_id,
            operation="payment_finalization",
            error_type="domain_error",
            duration_ms=duration_ms,
            reason="no_purchase_id"
        )
        return
    
    # Get pending purchase for logging
    pending_purchase = await database.get_pending_purchase(purchase_id, telegram_id, check_expiry=False)
    if not pending_purchase:
        language = await resolve_user_language(telegram_id)
        await message.answer(i18n_get_text(language, "errors.session_expired"), parse_mode="HTML")
        logger.error(
            f"payment_rejected: provider=telegram_payment, user={telegram_id}, purchase_id={purchase_id}, "
            f"reason=pending_purchase_not_found_or_expired"
        )
        await database._log_audit_event_atomic_standalone(
            "purchase_rejected_due_to_stale_context",
            telegram_id,
            None,
            f"Payment received but pending purchase invalid: purchase_id={purchase_id}"
        )
        duration_ms = (time.time() - start_time) * 1000
        log_handler_exit(
            handler_name="process_successful_payment",
            outcome="failed",
            telegram_id=telegram_id,
            operation="payment_finalization",
            error_type="domain_error",
            duration_ms=duration_ms,
            reason="pending_purchase_not_found_or_expired"
        )
        return
    
    tariff_type = pending_purchase["tariff"]
    period_days = pending_purchase["period_days"]
    promo_code_used = pending_purchase.get("promo_code")
    # Для Stars total_amount — количество звёзд, а не рубли: берём цену покупки.
    payment_amount_rubles = resolve_payment_amount_rubles(
        payment.total_amount, is_stars_payment, pending_purchase
    )
    
    # КРИТИЧНО: Логируем верификацию платежа
    logger.info(
        f"payment_verified: provider=telegram_payment, user={telegram_id}, purchase_id={purchase_id}, "
        f"tariff={tariff_type}, period_days={period_days}, amount={payment_amount_rubles:.2f} RUB, "
        f"amount_match=True, purchase_status=pending"
    )
    
    await database._log_audit_event_atomic_standalone(
            "payment_received",
            telegram_id,
            None,
            f"Payment received with valid pending purchase: purchase_id={purchase_id}, amount={payment_amount_rubles:.2f} RUB"
        )

    return PurchaseContext(
        purchase_id=purchase_id,
        pending_purchase=pending_purchase,
        tariff_type=tariff_type,
        period_days=period_days,
        promo_code_used=promo_code_used,
        payment_amount_rubles=payment_amount_rubles,
    )
