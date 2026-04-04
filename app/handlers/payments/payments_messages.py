"""
Payment message handlers: successful_payment, photo

VPN key: Primary path via grant_access → vpn_utils.add_vless_user (Xray API).
Architecture invariant: Bot never generates VLESS locally. vpn_key must come from API only.
"""
import logging
import time
from datetime import datetime, timezone

from aiogram import Router, F
from aiogram.filters import StateFilter
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, PreCheckoutQuery, WebAppInfo
from aiogram.fsm.context import FSMContext

import database
import config
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language, DEFAULT_LANGUAGE
from app.services.payments import service as payment_service
from app.services.payments.exceptions import (
    PaymentServiceError,
    InvalidPaymentPayloadError,
    PaymentAmountMismatchError,
    PaymentFinalizationError,
)
from app.core.system_state import (
    SystemState,
    healthy_component,
    degraded_component,
    unavailable_component,
)
from app.utils.logging_helpers import (
    log_handler_entry,
    log_handler_exit,
    classify_error,
)
from app.utils.security import (
    validate_telegram_id,
    validate_payment_payload,
    log_security_warning,
)
from app.core.feature_flags import get_feature_flags
from app.handlers.notifications import send_referral_cashback_notification
from app.handlers.common.keyboards import get_payment_success_keyboard
from app.handlers.common.states import BroadcastCreate
from app.handlers.common.utils import clear_promo_session

payments_router = Router()
logger = logging.getLogger(__name__)


@payments_router.pre_checkout_query()
async def process_pre_checkout_query(pre_checkout_query: PreCheckoutQuery):
    """Подтверждение платежа перед списанием. КРИТИЧНО: ответить в течение таймаута Telegram (10 сек)."""
    payload = pre_checkout_query.invoice_payload or ""
    telegram_id = pre_checkout_query.from_user.id if pre_checkout_query.from_user else 0
    is_stars = (pre_checkout_query.currency == "XTR")
    log_amount = pre_checkout_query.total_amount if is_stars else (pre_checkout_query.total_amount / 100 if pre_checkout_query.total_amount else 0)

    # Валидация purchase payload — отклоняем если pending_purchase истёк или не найден
    if payload.startswith("purchase:"):
        purchase_id = payload.split(":", 1)[1]
        try:
            pending = await database.get_pending_purchase(purchase_id, telegram_id, check_expiry=True)
            if not pending:
                logger.warning(
                    "PRE_CHECKOUT_REJECTED purchase_id=%s telegram_id=%s reason=expired_or_not_found",
                    purchase_id, telegram_id,
                )
                await pre_checkout_query.answer(ok=False, error_message="Invoice expired. Please create a new one.")
                return
        except Exception as e:
            logger.error("PRE_CHECKOUT_DB_ERROR purchase_id=%s error=%s", purchase_id, e)
            # В случае ошибки БД — пропускаем, чтобы не блокировать платёж
    else:
        purchase_id = payload

    await pre_checkout_query.answer(ok=True)
    logger.info(
        "PRE_CHECKOUT_APPROVED purchase_id=%s telegram_id=%s amount=%s %s",
        purchase_id,
        telegram_id,
        log_amount,
        "XTR" if is_stars else "RUB",
    )


@payments_router.message(F.photo, ~StateFilter(BroadcastCreate.waiting_for_message))
async def log_incoming_photo_file_id(message: Message):
    """Log file_id of incoming photos for later use (e.g. loyalty images). Does not send reply."""
    try:
        telegram_id = message.from_user.id if message.from_user else 0
        file_id = message.photo[-1].file_id
        logger.info(
            "PHOTO_FILE_ID_RECEIVED [telegram_id=%s, file_id=%s]",
            telegram_id,
            file_id,
        )
    except Exception as e:
        logger.warning("PHOTO_FILE_ID_RECEIVED log failed: %s", e)

@payments_router.message(F.successful_payment)
async def process_successful_payment(message: Message, state: FSMContext):
    """Обработчик successful_payment - успешная оплата картой
    
    КРИТИЧНО:
    - Использует finalize_purchase для активации подписки
    - Очищает FSM state после успешной активации
    - Отправляет VPN ключ пользователю
    """
    start_time = time.time()

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
        await message.answer(i18n_get_text(language, "errors.try_later"))
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
        await message.answer(i18n_get_text(language, "errors.try_later"))
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
            i18n_get_text(language, "main.service_unavailable")
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
        
        # Создаем стандартную inline клавиатуру для UX
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=i18n_get_text(language, "buy.renew_button", "buy_renew_button"),
                callback_data="menu_buy_vpn"
            )],
            [InlineKeyboardButton(
                text=i18n_get_text(language, "main.support_button", "support_button"),
                url="https://t.me/Atlas_SupportSecurity"
            )]
        ])
        
        await message.answer(text, reply_markup=keyboard)
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
    
    # Проверяем, является ли это пополнением баланса
    try:
        payload_info = await payment_service.verify_payment_payload(payload, telegram_id)
        
        if payload_info.payload_type == "balance_topup":
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
                await message.answer(error_text)
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
                await message.answer(error_text)
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
            
            # Создаем inline клавиатуру для UX
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text=i18n_get_text(language, "buy.renew_button", "buy_renew_button"),
                    callback_data="menu_buy_vpn"
                )],
                [InlineKeyboardButton(
                    text=i18n_get_text(language, "main.profile", "profile"),
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
                await message.answer(text, reply_markup=keyboard)
                logger.info(
                    f"NOTIFICATION_SENT [type=balance_topup, payment_id={payment_id}, user={telegram_id}]"
                )
            except Exception as e:
                logger.error(
                    f"NOTIFICATION_SEND_FAILED [type=balance_topup, payment_id={payment_id}, "
                    f"user={telegram_id}, error={e}] (notification flagged but message not delivered)"
                )
            
            # Отправляем уведомление о кешбэке (если начислен)
            if referral_reward_result and referral_reward_result.get("success"):
                try:
                    notification_sent = await send_referral_cashback_notification(
                        bot=message.bot,
                        referrer_id=referral_reward_result.get("referrer_id"),
                        referred_id=telegram_id,
                        purchase_amount=payment_amount_rubles,
                        cashback_amount=referral_reward_result.get("reward_amount"),
                        cashback_percent=referral_reward_result.get("percent"),
                        paid_referrals_count=referral_reward_result.get("paid_referrals_count", 0),
                        referrals_needed=referral_reward_result.get("referrals_needed", 0),
                        action_type="topup"
                    )
                    if notification_sent:
                        logger.info(
                            f"REFERRAL_NOTIFICATION_SENT [type=balance_topup, referrer={referral_reward_result.get('referrer_id')}, "
                            f"referred={telegram_id}, amount={payment_amount_rubles} RUB]"
                        )
                        logger.info(f"Referral cashback processed for balance topup: user={telegram_id}, amount={payment_amount_rubles} RUB")
                    else:
                        logger.warning(
                            f"REFERRAL_NOTIFICATION_FAILED [type=balance_topup, referrer={referral_reward_result.get('referrer_id')}, "
                            f"referred={telegram_id}]"
                        )
                except Exception as e:
                    logger.exception(f"Error sending referral cashback notification for balance topup: user={telegram_id}: {e}")
            
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
            
    except InvalidPaymentPayloadError as e:
        logger.error(f"Invalid payment payload: {payload}, error={e}")
        language = await resolve_user_language(telegram_id)
        await message.answer(i18n_get_text(language, "errors.payment_processing"))
        duration_ms = (time.time() - start_time) * 1000
        error_type = classify_error(e)
        log_handler_exit(
            handler_name="process_successful_payment",
            outcome="failed",
            telegram_id=telegram_id,
            operation="payment_finalization",
            error_type=error_type,
            duration_ms=duration_ms,
            reason="invalid_payload"
        )
        return
    except PaymentServiceError as e:
        logger.error(f"Payment service error: {e}")
        language = await resolve_user_language(telegram_id)
        await message.answer(i18n_get_text(language, "errors.payment_processing"))
        duration_ms = (time.time() - start_time) * 1000
        error_type = classify_error(e)
        log_handler_exit(
            handler_name="process_successful_payment",
            outcome="failed",
            telegram_id=telegram_id,
            operation="payment_finalization",
            error_type=error_type,
            duration_ms=duration_ms,
            reason="payment_service_error"
        )
        return
    
    # Обработка платежей за подписку
    # Проверяем, что это платеж за подписку (не balance topup)
    if payload_info.payload_type != "purchase":
        # Legacy formats are not supported for new purchases - only balance topup
        logger.error(f"Unsupported payload type for subscription payment: {payload_info.payload_type}, payload={payload}")
        language = await resolve_user_language(telegram_id)
        await message.answer(i18n_get_text(language, "errors.payment_processing"))
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
        await message.answer(i18n_get_text(language, "errors.payment_processing"))
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
        await message.answer(i18n_get_text(language, "errors.session_expired"))
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
    # Для Stars: total_amount = кол-во Stars напрямую; для RUB: total_amount в копейках
    payment_amount_rubles = payment.total_amount if is_stars_payment else payment.total_amount / 100.0
    
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
        
    # Проверяем, является ли это подарочной подпиской
    is_gift_purchase = pending_purchase.get("purchase_type") == "gift"

    if is_gift_purchase:
        # Подарочная подписка — финализируем напрямую через database
        payment_provider_name = "telegram_stars" if is_stars_payment else "telegram_payment"
        try:
            gift_result = await database.finalize_purchase(
                purchase_id=purchase_id,
                payment_provider=payment_provider_name,
                amount_rubles=payment_amount_rubles,
            )
            if gift_result and gift_result.get("is_gift") and gift_result.get("gift_code"):
                from app.handlers.callbacks.gift import _send_gift_success
                await _send_gift_success(
                    bot=message.bot,
                    telegram_id=telegram_id,
                    language=language,
                    gift_code=gift_result["gift_code"],
                    tariff=gift_result["gift_tariff"],
                    period_days=gift_result["gift_period_days"],
                )
                logger.info(
                    f"GIFT_PAYMENT_FINALIZED purchase_id={purchase_id} user={telegram_id} "
                    f"code={gift_result['gift_code']}"
                )
                await state.clear()
                duration_ms = (time.time() - start_time) * 1000
                log_handler_exit(
                    handler_name="process_successful_payment",
                    outcome="success",
                    telegram_id=telegram_id,
                    operation="payment_finalization",
                    duration_ms=duration_ms,
                    payment_type="gift_subscription",
                )
                return
            else:
                logger.error(f"Gift finalization returned unexpected result: {gift_result}")
                await message.answer(i18n_get_text(language, "errors.payment_processing"))
                return
        except Exception as e:
            logger.exception(f"Gift payment finalization failed: user={telegram_id}, error={e}")
            await message.answer(i18n_get_text(language, "errors.payment_processing"))
            return

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

        # Sync with website (fire-and-forget)
        try:
            from app.handlers.user.site_link import notify_site_after_payment
            await notify_site_after_payment(telegram_id, period_days, tariff_type)
        except Exception as site_err:
            logger.warning("SITE_SYNC_AFTER_PAYMENT_FAILED: user=%s, error=%s", telegram_id, site_err)

        # Remnawave: renew/create user on Yandex node (fire-and-forget)
        try:
            from app.services.remnawave_service import renew_remnawave_user_bg
            if expires_at:
                renew_remnawave_user_bg(telegram_id, expires_at, tariff_type)
        except Exception as rmn_err:
            logger.warning("REMNAWAVE_AFTER_STARS_PAYMENT_FAILED: user=%s, error=%s", telegram_id, rmn_err)

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
                    url="https://t.me/Atlas_SupportSecurity"
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
        await message.answer(error_text)
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
        await message.answer(error_text)
        
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
        await message.answer(error_text)
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
        return
    
    # Один компактный экран: текст + кнопки копирования и профиль (без отдельной отправки ключей)
    is_upgrade = getattr(result, "is_basic_to_plus_upgrade", False)
    if is_upgrade:
        text = (
            f"⭐️ Апгрейд до Plus!\n"
            f"📅 До: {expires_str}\n\n"
            f"📲 Чтобы конфигурации обновились в приложении:\n"
            f"V2rayTUN — нажмите 🔄 (обновить подписку)"
        )
        keyboard = get_payment_success_keyboard(language, subscription_type="plus", is_renewal=True)
        try:
            await message.answer(text, reply_markup=keyboard, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Failed to send upgrade message: user={telegram_id}, error={e}")
    else:
        if config.is_biz_tariff(subscription_type):
            tariff_label, tariff_emoji = "Business", "🏢"
        elif subscription_type == "plus":
            tariff_label, tariff_emoji = "Plus", "⭐️"
        else:
            tariff_label, tariff_emoji = "Basic", "📦"

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
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text=_i18n_get(language, "purchase.success_renewal_btn_profile"),
                    callback_data="menu_profile"
                )],
                [InlineKeyboardButton(
                    text=_i18n_get(language, "purchase.success_renewal_btn_main"),
                    callback_data="menu_main"
                )],
            ])
        else:
            text = _i18n_get(language, "purchase.success_first",
                             tariff_name=f"{tariff_emoji} {tariff_label}",
                             period=period_str,
                             expires_date=expires_str)
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text=_i18n_get(language, "purchase.success_first_btn_connect"),
                    web_app=WebAppInfo(url=MINI_APP_URL),
                )],
                [InlineKeyboardButton(
                    text=_i18n_get(language, "purchase.success_first_btn_instruction"),
                    callback_data="menu_instruction"
                )],
                [InlineKeyboardButton(
                    text=_i18n_get(language, "purchase.success_first_btn_profile"),
                    callback_data="menu_profile"
                )],
            ])
        # ИДЕМПОТЕНТНОСТЬ: Помечаем ПЕРЕД отправкой (mark-before-send pattern)
        # При краше между mark и send — уведомление потеряно, но не дублировано
        try:
            sent = await database.mark_payment_notification_sent(payment_id)
            if not sent:
                logger.warning(
                    f"NOTIFICATION_FLAG_ALREADY_SET [type=payment_success, payment_id={payment_id}, user={telegram_id}]"
                )
                # Already sent by concurrent handler — skip
                return
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

    # КРИТИЧНО: pending_purchase уже помечен как paid в finalize_purchase
    # Реферальный кешбэк уже обработан в finalize_purchase через process_referral_reward
    # Отправляем уведомление рефереру (если кешбэк был начислен)
    referral_reward = result.referral_reward
    if referral_reward and referral_reward.get("success"):
        try:
            # Формируем период подписки для уведомления
            subscription_period = None
            if period_days:
                if period_days == 30:
                    subscription_period = "1 месяц"
                elif period_days == 90:
                    subscription_period = "3 месяца"
                elif period_days == 180:
                    subscription_period = "6 месяцев"
                elif period_days == 365:
                    subscription_period = "12 месяцев"
                else:
                    months = period_days // 30
                    if months > 0:
                        subscription_period = f"{months} месяц" + ("а" if months in [2, 3, 4] else ("ев" if months > 4 else ""))
                    else:
                        subscription_period = f"{period_days} дней"
            
            notification_sent = await send_referral_cashback_notification(
                bot=message.bot,
                referrer_id=referral_reward.get("referrer_id"),
                referred_id=telegram_id,
                purchase_amount=payment_amount_rubles,
                cashback_amount=referral_reward.get("reward_amount"),
                cashback_percent=referral_reward.get("percent"),
                paid_referrals_count=referral_reward.get("paid_referrals_count", 0),
                referrals_needed=referral_reward.get("referrals_needed", 0),
                action_type="purchase",
                subscription_period=subscription_period
            )
            if notification_sent:
                logger.info(
                    f"REFERRAL_NOTIFICATION_SENT [type=purchase, referrer={referral_reward.get('referrer_id')}, "
                    f"referred={telegram_id}, purchase_id={purchase_id}]"
                )
            else:
                logger.warning(
                    "NOTIFICATION_FAILED",
                    extra={
                        "type": "purchase",
                        "referrer": referral_reward.get("referrer_id"),
                        "referred": telegram_id,
                        "purchase_id": purchase_id,
                        "error": "send_referral_cashback_notification returned False"
                    }
                )
        except Exception as e:
            logger.warning(
                "NOTIFICATION_FAILED",
                extra={
                    "type": "purchase",
                    "referred": telegram_id,
                    "purchase_id": purchase_id if 'purchase_id' in locals() else None,
                    "referrer": referral_reward.get("referrer_id") if referral_reward else None,
                    "error": str(e)
                }
            )
    
    logger.info(
        f"process_successful_payment: PAYMENT_COMPLETE [user={telegram_id}, payment_id={payment_id}, "
        f"tariff={tariff_type}, period_days={period_days}, amount={payment_amount_rubles} RUB, "
        f"purchase_id={purchase_id}, expires_at={expires_str}, vpn_key_sent=True, subscription_visible=True]"
    )
    
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