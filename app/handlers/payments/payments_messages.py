"""
Payment message handlers: successful_payment, photo

VPN key: Primary path via grant_access → vpn_utils.add_vless_user (Xray API).
Architecture invariant: Bot never generates VLESS locally. vpn_key must come from API only.
"""
import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Optional

from aiogram import Router, F
from aiogram.filters import StateFilter
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, PreCheckoutQuery
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
from app.handlers.common.keyboards import get_payment_success_keyboard
from app.handlers.common.utils import clear_promo_session
from app.handlers.common.emoji import CE

payments_router = Router()
logger = logging.getLogger(__name__)

# TG-RT-4: Telegram gives the bot 10 s to answer pre_checkout_query (then the
# user's payment fails). The purchase lookup gets at most this long; a hung DB
# (exhausted pool) takes the existing "DB error → approve" branch.
PRE_CHECKOUT_DB_TIMEOUT_S = 5.0
# Language of the rejection text; DB lookup + this stay well under the 10 s.
PRE_CHECKOUT_LANG_TIMEOUT_S = 2.0


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
            pending = await asyncio.wait_for(
                database.get_pending_purchase(purchase_id, telegram_id, check_expiry=True),
                timeout=PRE_CHECKOUT_DB_TIMEOUT_S,
            )
            if not pending:
                logger.warning(
                    "PRE_CHECKOUT_REJECTED purchase_id=%s telegram_id=%s reason=expired_or_not_found",
                    purchase_id, telegram_id,
                )
                try:
                    language = await asyncio.wait_for(
                        resolve_user_language(telegram_id), timeout=PRE_CHECKOUT_LANG_TIMEOUT_S,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    language = DEFAULT_LANGUAGE
                await pre_checkout_query.answer(
                    ok=False, error_message=i18n_get_text(language, "payment.expired"),
                )
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


@payments_router.message(
    F.photo,
)
async def log_incoming_photo_file_id(message: Message):
    """Log file_id of incoming photos for later use (e.g. loyalty images).

    Если фото прислал админ в личку — сразу отвечаем file_id в чат,
    чтобы не лезть в логи вручную.
    """
    try:
        telegram_id = message.from_user.id if message.from_user else 0
        file_id = message.photo[-1].file_id
        logger.info(
            "PHOTO_FILE_ID_RECEIVED [telegram_id=%s, file_id=%s]",
            telegram_id,
            file_id,
        )
        from app.utils.security import is_admin
        if (
            message.chat.type == "private"
            and message.from_user
            and is_admin(telegram_id)
        ):
            await message.reply(
                f"🆔 <b>photo</b>\n<code>{file_id}</code>",
                parse_mode="HTML",
            )
    except Exception as e:
        logger.warning("PHOTO_FILE_ID_RECEIVED log failed: %s", e)


_SHOP_CARD_PROVIDER = "telegram_card"


async def _alert_shop_order_lost(bot, stage, pending, telegram_id, amount_rubles, *,
                                 reason, error=None, provider=_SHOP_CARD_PROVIDER):
    """Forced admin alert + payment_errors for a paid shop order (no secrets)."""
    from app.services.payments.confirmation import alert_shop_order_not_notified
    await alert_shop_order_not_notified(
        bot,
        stage=stage,
        purchase_id=pending.get("purchase_id"),
        telegram_id=telegram_id,
        provider=provider,
        product=f"{pending.get('purchase_type') or '—'} / {pending.get('tariff') or '—'}",
        amount_rubles=amount_rubles,
        reason=reason,
        error=error,
        pending=pending,
    )


async def _alert_if_shop_row_not_pending(bot, payload, telegram_id, payment, is_stars_payment) -> bool:
    """successful_payment for a SHOP purchase whose row is no longer 'pending'
    (create_pending_purchase expired it when the buyer opened another payment
    method after pre-checkout passed): the buyer is charged and sees an error,
    the order is not placed. Behaviour stays as is; the admin gets a forced
    alert to fulfil or refund manually. Never raises.

    Returns True when the shop alert was sent, so the caller skips the generic
    money-taken alert (one incident = one alert)."""
    if not payload or not payload.startswith("purchase:") or payload.startswith("purchase:promo:"):
        return False
    purchase_id = payload.split(":", 1)[1]
    try:
        row = await database.get_pending_purchase_by_id(purchase_id, check_expiry=False)
        from app.services.payments.confirmation import _is_notification_only_purchase
        if not row or row.get("telegram_id") != telegram_id or not _is_notification_only_purchase(row):
            return False
        total = payment.total_amount or 0
        await _alert_shop_order_lost(
            bot, "shop_order_not_pending", row, telegram_id,
            None if is_stars_payment else total / 100.0,
            reason=(
                f"строка покупки в статусе '{row.get('status')}' (покупатель открыл другой способ "
                "оплаты) — бот ответил ошибкой, заказ НЕ оформлен"
                + (f"; оплачено {total} XTR" if is_stars_payment else "")
            ),
            provider="telegram_stars" if is_stars_payment else _SHOP_CARD_PROVIDER,
        )
        return True
    except Exception as e:  # noqa: BLE001
        logger.error("SHOP_ROW_NOT_PENDING_ALERT_FAILED purchase_id=%s: %s", purchase_id, type(e).__name__)
        return False


async def _alert_money_taken_not_granted(
    bot,
    *,
    telegram_id: int,
    purchase_id,
    error,
    stage: str,
    provider: str = "telegram_payment",
    amount_rubles=None,
    tariff=None,
    period_days=None,
) -> None:
    """Telegram already charged the user, but the purchase was not (fully)
    processed: payment_errors row + FORCED admin alert (docs/audit/03_payment_matrix.md,
    alert coverage). Telegram never retries successful_payment, so without this
    the admin would learn about it only from the user. Never raises."""
    err = error if isinstance(error, BaseException) else RuntimeError(str(error))
    try:
        await database.log_payment_error(
            stage=stage,
            telegram_id=telegram_id,
            purchase_id=str(purchase_id) if purchase_id else None,
            payment_provider=provider,
            amount_rubles=amount_rubles,
            error_message=f"{type(err).__name__}: {err}"[:500],
        )
    except Exception as log_err:
        logger.warning("TELEGRAM_PAYMENT_ERROR_LOG_FAILED stage=%s: %s", stage, log_err)
    try:
        from app.services.admin_alerts import alert_payment_failure
        await alert_payment_failure(
            bot, provider, telegram_id, str(purchase_id or "—"), err,
            is_transient=False,
            amount_rubles=amount_rubles, tariff=tariff, period_days=period_days,
        )
    except Exception as alert_err:
        logger.warning("TELEGRAM_PAYMENT_ALERT_FAILED stage=%s: %s", stage, alert_err)


_UNAVAILABLE_PERSIST_INTERVAL_SEC = 30.0
_UNAVAILABLE_PERSIST_MAX_WAIT_SEC = 3600.0
_unavailable_persist_tasks: set = set()


def _paid_while_unavailable_record(message: Message, reason: str) -> dict:
    """Everything the admin needs to grant a Telegram payment by hand (no secrets)."""
    payment = message.successful_payment
    user = message.from_user
    return {
        "reason": reason,
        "telegram_id": user.id if user else None,
        "username": getattr(user, "username", None) if user else None,
        "currency": getattr(payment, "currency", None),
        "total_amount": getattr(payment, "total_amount", None),
        "invoice_payload": getattr(payment, "invoice_payload", None),
        "telegram_payment_charge_id": getattr(payment, "telegram_payment_charge_id", None),
        "provider_payment_charge_id": getattr(payment, "provider_payment_charge_id", None),
    }


async def _log_paid_while_unavailable(record: dict) -> bool:
    """payment_errors row for a payment taken while the bot could not finalize it."""
    is_stars = record.get("currency") == "XTR"
    total = record.get("total_amount") or 0
    row_id = await database.log_payment_error(
        stage=f"telegram_paid_{record['reason']}",
        telegram_id=record.get("telegram_id"),
        purchase_id=(record.get("invoice_payload") or "")[:200] or None,
        payment_provider="telegram_stars" if is_stars else "telegram_payment",
        amount_rubles=None if is_stars else total / 100.0,
        error_message=(
            f"Telegram charged {total} {'XTR' if is_stars else 'kopecks'} while "
            f"{record['reason']} — nothing granted, grant manually"
        ),
        raw_payload=record,
    )
    return row_id is not None


async def _persist_when_db_ready(record: dict) -> None:
    """DB was down when Telegram charged the user: write the payment_errors row
    as soon as the DB is back (bounded wait; the forced alert already went out)."""
    waited = 0.0
    while waited < _UNAVAILABLE_PERSIST_MAX_WAIT_SEC:
        await asyncio.sleep(_UNAVAILABLE_PERSIST_INTERVAL_SEC)
        waited += _UNAVAILABLE_PERSIST_INTERVAL_SEC
        if database.DB_READY:
            try:
                if await _log_paid_while_unavailable(record):
                    logger.info(
                        "TELEGRAM_PAID_UNAVAILABLE_PERSISTED tg=%s charge=%s",
                        record.get("telegram_id"), record.get("telegram_payment_charge_id"),
                    )
                    return
            except Exception as e:  # noqa: BLE001
                logger.warning("TELEGRAM_PAID_UNAVAILABLE_PERSIST_FAILED: %s", type(e).__name__)
    logger.error(
        "TELEGRAM_PAID_UNAVAILABLE_NOT_PERSISTED tg=%s charge=%s — only the admin alert has it",
        record.get("telegram_id"), record.get("telegram_payment_charge_id"),
    )


async def _handle_paid_while_unavailable(message: Message, *, reason: str) -> None:
    """Telegram already charged the user (card or Stars) but the bot cannot
    finalize now: the DB is not ready or the payments kill switch is off.
    Telegram never resends successful_payment, so:
      - FORCED admin alert with TG ID, amount, currency, payload and charge ids;
      - a payment_errors row now, or once the DB is back;
      - the user sees an honest text: payment received, access will be granted
        manually soon, do NOT pay again (never a "try again" / buy button).
    Never raises."""
    record = _paid_while_unavailable_record(message, reason)
    charge = record.get("telegram_payment_charge_id") or ""
    ref = charge[-8:] if charge else str(getattr(message, "message_id", "") or "—")
    is_stars = record.get("currency") == "XTR"
    total = record.get("total_amount") or 0
    amount_line = f"{total} XTR" if is_stars else f"{total / 100.0:.2f} RUB"
    try:
        from app.services.admin_alerts import send_alert
        await send_alert(
            message.bot,
            "payment",
            (
                "[PERMANENT] Telegram payment taken, NOTHING granted\n"
                f"Reason: {reason}\n"
                f"User TG ID: {record.get('telegram_id')}"
                + (f" (@{record['username']})" if record.get("username") else "")
                + "\n"
                f"Amount: {amount_line}\n"
                f"Payload: {record.get('invoice_payload')}\n"
                f"Telegram charge id: {charge or '—'}\n"
                f"Provider charge id: {record.get('provider_payment_charge_id') or '—'}\n"
                f"User reference: {ref}\n"
                "Grant the purchase manually (the user was told not to pay again)."
            ),
            force=True,
        )
    except Exception as e:  # noqa: BLE001
        logger.error("TELEGRAM_PAID_UNAVAILABLE_ALERT_FAILED: %s", type(e).__name__)

    persisted = False
    if database.DB_READY:
        try:
            persisted = await _log_paid_while_unavailable(record)
        except Exception as e:  # noqa: BLE001
            logger.warning("TELEGRAM_PAID_UNAVAILABLE_LOG_FAILED: %s", type(e).__name__)
    if not persisted:
        task = asyncio.create_task(_persist_when_db_ready(record))
        _unavailable_persist_tasks.add(task)
        task.add_done_callback(_unavailable_persist_tasks.discard)

    try:
        language = await resolve_user_language(message.from_user.id)
    except Exception:  # noqa: BLE001 — DB down: default language
        language = DEFAULT_LANGUAGE
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text=i18n_get_text(language, "main.support_button"),
        url="https://t.me/atlas_suppbot",
    )]])
    try:
        await message.answer(
            i18n_get_text(language, "main.service_unavailable_payment", ref=ref),
            reply_markup=keyboard, parse_mode="HTML",
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("TELEGRAM_PAID_UNAVAILABLE_USER_MSG_FAILED: %s", type(e).__name__)
    logger.error(
        "TELEGRAM_PAID_WHILE_UNAVAILABLE reason=%s tg=%s amount=%s payload=%s",
        reason, record.get("telegram_id"), amount_line, record.get("invoice_payload"),
    )


def _expected_stars(pending: dict) -> Optional[int]:
    """Stars the invoice of this purchase asked for (same rules as the invoice
    code: callback_pay_stars / callback_gift_pay_stars). None — unknown."""
    from app.services import tariffs
    ptype = pending.get("purchase_type") or "subscription"
    try:
        if ptype == "subscription":
            key = tariffs.tariff_key(pending.get("tariff"), bool(pending.get("is_combo")))
            # Same rule as the invoice: a discounted row (price below the catalog)
            # asked for the discounted stars.
            return tariffs.stars_for_purchase(
                key, int(pending.get("period_days") or 0), int(pending.get("price_kopecks") or 0),
            )
        if ptype == "gift":
            row = (config.TARIFFS_STARS.get(pending.get("tariff")) or {}).get(pending.get("period_days"))
            if row:
                return int(row["price"])
            return tariffs.stars_for_rub(int(pending.get("price_kopecks") or 0) / 100)
    except Exception as e:  # noqa: BLE001 — unknown tariff → caller keeps the raw amount
        logger.warning("STARS_EXPECTED_UNKNOWN purchase_id=%s: %s", pending.get("purchase_id"), e)
    return None


def _stars_paid_to_rubles(pending: dict, paid_stars: int) -> float:
    """RUB amount of a Stars payment for a `purchase:` row.

    price_kopecks of a Stars purchase is its RUB list price, so the paid stars
    are converted at the purchase's own rate: price × paid / expected (exact
    payment → exactly the price; underpayment stays an underpayment and the
    amount check rejects it with an alert). Legacy rows, created before this
    rule, stored stars × 100 — they keep 1 star = 1 unit as before."""
    price_kopecks = int(pending.get("price_kopecks") or 0)
    paid = int(paid_stars or 0)
    if paid * 100 == price_kopecks:
        return float(paid)
    expected = _expected_stars(pending)
    if not expected:
        return float(paid)
    return round(price_kopecks / 100.0 * paid / expected, 2)


@payments_router.message(F.refunded_payment)
async def process_refunded_payment(message: Message):
    """TG-RT-3: Telegram refunded a payment (a Stars refund arrives as the
    `refunded_payment` service message). Owner rule (docs/audit/SCOPE.md):
    refunds → log + payment_errors + FORCED admin alert; access is NOT revoked
    automatically. Never raises."""
    rp = message.refunded_payment
    tg = message.from_user.id if message.from_user else None
    is_stars = rp.currency == "XTR"
    amount_line = f"{rp.total_amount} XTR" if is_stars else f"{rp.total_amount / 100.0:.2f} {rp.currency}"
    charge = rp.telegram_payment_charge_id or "—"
    logger.warning(
        "TELEGRAM_REFUND tg=%s amount=%s payload=%s charge=%s — access NOT revoked",
        tg, amount_line, rp.invoice_payload, charge,
    )
    try:
        await database.log_payment_error(
            stage="telegram_refund",
            telegram_id=tg,
            purchase_id=(rp.invoice_payload or "")[:200] or None,
            payment_provider="telegram_stars" if is_stars else "telegram_payment",
            amount_rubles=None if is_stars else rp.total_amount / 100.0,
            error_message=f"Telegram refunded {amount_line}; access not revoked automatically",
            raw_payload={
                "telegram_id": tg, "currency": rp.currency, "total_amount": rp.total_amount,
                "invoice_payload": rp.invoice_payload, "telegram_payment_charge_id": rp.telegram_payment_charge_id,
                "provider_payment_charge_id": rp.provider_payment_charge_id,
            },
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("TELEGRAM_REFUND_LOG_FAILED tg=%s: %s", tg, type(e).__name__)
    try:
        from app.services.admin_alerts import send_alert
        await send_alert(
            message.bot,
            "payment",
            (
                "[REFUND] Telegram refunded a payment\n"
                f"User TG ID: {tg}\n"
                f"Amount: {amount_line}\n"
                f"Payload: {rp.invoice_payload}\n"
                f"Telegram charge id: {charge}\n"
                "Access was NOT revoked automatically — review the user's subscription manually."
            ),
            force=True,
        )
    except Exception as e:  # noqa: BLE001
        logger.error("TELEGRAM_REFUND_ALERT_FAILED tg=%s: %s", tg, type(e).__name__)


async def _remember_charge(payment_id, payment) -> None:
    """TG-RT-2: remember the Telegram charge of a finalized purchase, so a
    re-delivered successful_payment is recognised. Never raises."""
    charge = getattr(payment, "telegram_payment_charge_id", None)
    if payment_id and charge:
        await database.remember_telegram_charge(int(payment_id), charge)


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
        # The money is already taken (successful_payment): never "try later".
        await _handle_paid_while_unavailable(message, reason="payments_disabled")
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
        if config.REMNAWAVE_ENABLED:
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
        await _handle_paid_while_unavailable(message, reason="db_not_ready")
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

    # TG-RT-2: Telegram re-delivers an update the webhook did not answer with
    # 2xx (process killed mid-request by a redeploy). This charge is already on
    # a payments row → it was finalized: no error screen for the user, no false
    # "money taken, nothing granted" alert. A DIFFERENT charge is not matched.
    charge_id = getattr(payment, "telegram_payment_charge_id", None)
    if charge_id and await database.telegram_charge_seen(charge_id):
        logger.warning(
            "TELEGRAM_PAYMENT_REDELIVERED tg=%s charge=…%s payload=%s — already finalized, skipped",
            telegram_id, charge_id[-8:], payload,
        )
        return

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
                # Telegram already took the money and never resends successful_payment.
                await _alert_money_taken_not_granted(
                    message.bot, telegram_id=telegram_id, purchase_id=provider_charge_id, error=e,
                    stage="telegram_balance_topup_failed", provider=topup_provider,
                    amount_rubles=payment_amount_rubles,
                )
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
            
            # One top-up success message for webhook and Telegram alike (08 #16).
            from app.services.payments.success_message import build_topup_success
            text, keyboard = build_topup_success(
                language, amount=payment_amount_rubles, balance=new_balance,
            )
            
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
            
            # No referral cashback for a top-up (owner rule N17); the referrer is
            # notified by the accrual itself (app.services.notifications.referral_cashback).

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
        # «Заказ не теряется»: a shop row that stopped being 'pending' between
        # pre-checkout and successful_payment — money taken, order not placed.
        shop_alerted = await _alert_if_shop_row_not_pending(
            message.bot, payload, telegram_id, payment, is_stars_payment,
        )
        language = await resolve_user_language(telegram_id)
        await message.answer(i18n_get_text(language, "errors.payment_processing"), parse_mode="HTML")
        if not shop_alerted:
            await _alert_money_taken_not_granted(
                message.bot, telegram_id=telegram_id, purchase_id=purchase_id_from_payload, error=e,
                stage="telegram_purchase_not_found",
                provider="telegram_stars" if is_stars_payment else "telegram_payment",
                amount_rubles=log_amount,
            )
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
        await message.answer(i18n_get_text(language, "errors.payment_processing"), parse_mode="HTML")
        await _alert_money_taken_not_granted(
            message.bot, telegram_id=telegram_id, purchase_id=purchase_id_from_payload, error=e,
            stage="telegram_payment_service_error",
            provider="telegram_stars" if is_stars_payment else "telegram_payment",
            amount_rubles=log_amount,
        )
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
        shop_alerted = await _alert_if_shop_row_not_pending(
            message.bot, payload, telegram_id, payment, is_stars_payment,
        )
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
        if not shop_alerted:
            await _alert_money_taken_not_granted(
                message.bot, telegram_id=telegram_id, purchase_id=purchase_id,
                error="pending purchase not found — paid via Telegram, nothing granted",
                stage="telegram_purchase_not_found",
                provider="telegram_stars" if is_stars_payment else "telegram_payment",
            )
        return
    
    tariff_type = pending_purchase["tariff"]
    period_days = pending_purchase["period_days"]
    promo_code_used = pending_purchase.get("promo_code")
    # Для Stars: total_amount = кол-во Stars → рублёвая цена покупки; для RUB: копейки
    payment_amount_rubles = (
        _stars_paid_to_rubles(pending_purchase, payment.total_amount)
        if is_stars_payment else payment.total_amount / 100.0
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
                await _remember_charge(gift_result.get("payment_id"), payment)
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
                await message.answer(i18n_get_text(language, "errors.payment_processing"), parse_mode="HTML")
                await _alert_money_taken_not_granted(
                    message.bot, telegram_id=telegram_id, purchase_id=purchase_id,
                    error="gift finalization returned an unexpected result", stage="telegram_gift_failed",
                    provider=payment_provider_name, amount_rubles=payment_amount_rubles,
                    tariff=tariff_type, period_days=period_days,
                )
                return
        except Exception as e:
            logger.exception(f"Gift payment finalization failed: user={telegram_id}, error={e}")
            await message.answer(i18n_get_text(language, "errors.payment_processing"), parse_mode="HTML")
            await _alert_money_taken_not_granted(
                message.bot, telegram_id=telegram_id, purchase_id=purchase_id, error=e,
                stage="telegram_gift_failed", provider=payment_provider_name,
                amount_rubles=payment_amount_rubles, tariff=tariff_type, period_days=period_days,
            )
            return

    # --- Telegram Premium purchase: mark paid + send success + notify admin ---
    is_premium_purchase = pending_purchase.get("purchase_type") == "telegram_premium"
    if is_premium_purchase:
        try:
            # Send success screen BEFORE marking paid (purchase data still accessible)
            from app.handlers.payments.telegram_premium import send_premium_success
            await send_premium_success(
                message.bot, telegram_id, purchase_id, pending_purchase,
            )
            await database.mark_pending_purchase_paid(purchase_id)
            # N17: referral cashback for any purchase (shop too); never raises.
            await database.award_referral_cashback(
                buyer_id=telegram_id, purchase_id=purchase_id, amount_rubles=payment_amount_rubles)
            logger.info(
                "PREMIUM_PAYMENT_FINALIZED purchase_id=%s user=%s amount=%s",
                purchase_id, telegram_id, payment_amount_rubles,
            )
        except Exception as e:
            logger.exception("PREMIUM_PAYMENT_ERROR purchase_id=%s error=%s", purchase_id, e)
            await message.answer(i18n_get_text(language, "errors.payment_processing"), parse_mode="HTML")
        await state.clear()
        duration_ms = (time.time() - start_time) * 1000
        log_handler_exit(
            handler_name="process_successful_payment",
            outcome="success",
            telegram_id=telegram_id,
            operation="payment_finalization",
            duration_ms=duration_ms,
            payment_type="telegram_premium",
        )
        return

    # --- Telegram Stars purchase: mark paid + send success + notify admin ---
    is_stars_purchase = pending_purchase.get("purchase_type") == "telegram_stars"
    if is_stars_purchase:
        try:
            from app.handlers.payments.telegram_stars_purchase import send_stars_success
            await send_stars_success(
                message.bot, telegram_id, purchase_id, pending_purchase,
            )
            await database.mark_pending_purchase_paid(purchase_id)
            # N17: referral cashback for any purchase (shop too); never raises.
            await database.award_referral_cashback(
                buyer_id=telegram_id, purchase_id=purchase_id, amount_rubles=payment_amount_rubles)
            logger.info(
                "STARS_PAYMENT_FINALIZED purchase_id=%s user=%s amount=%s",
                purchase_id, telegram_id, payment_amount_rubles,
            )
        except Exception as e:
            logger.exception("STARS_PAYMENT_ERROR purchase_id=%s error=%s", purchase_id, e)
            await message.answer(i18n_get_text(language, "errors.payment_processing"), parse_mode="HTML")
        await state.clear()
        duration_ms = (time.time() - start_time) * 1000
        log_handler_exit(
            handler_name="process_successful_payment",
            outcome="success",
            telegram_id=telegram_id,
            operation="payment_finalization",
            duration_ms=duration_ms,
            payment_type="telegram_stars",
        )
        return

    # --- Steam top-up: mark paid + send success + notify admin ---
    is_steam_purchase = pending_purchase.get("purchase_type") == "steam"
    if is_steam_purchase:
        try:
            from app.handlers.payments.steam_purchase import send_steam_success
            await send_steam_success(
                message.bot, telegram_id, purchase_id, pending_purchase,
            )
            await database.mark_pending_purchase_paid(purchase_id)
            # N17: referral cashback for any purchase (shop too); never raises.
            await database.award_referral_cashback(
                buyer_id=telegram_id, purchase_id=purchase_id, amount_rubles=payment_amount_rubles)
            logger.info(
                "STEAM_PAYMENT_FINALIZED purchase_id=%s user=%s amount=%s",
                purchase_id, telegram_id, payment_amount_rubles,
            )
        except Exception as e:
            logger.exception("STEAM_PAYMENT_ERROR purchase_id=%s error=%s", purchase_id, e)
            await message.answer(i18n_get_text(language, "errors.payment_processing"), parse_mode="HTML")
        await state.clear()
        duration_ms = (time.time() - start_time) * 1000
        log_handler_exit(
            handler_name="process_successful_payment",
            outcome="success",
            telegram_id=telegram_id,
            operation="payment_finalization",
            duration_ms=duration_ms,
            payment_type="steam",
        )
        return

    # --- Apple ID purchase: mark paid + send success + notify admin ---
    is_apple_purchase = pending_purchase.get("purchase_type") == "apple_id"
    if is_apple_purchase:
        try:
            tariff = pending_purchase.get("tariff", "apple_id_usa_0")
            tariff_parts = tariff.split("_")
            region = tariff_parts[2] if len(tariff_parts) >= 3 else "usa"
            nominal = int(tariff_parts[3]) if len(tariff_parts) >= 4 else 0

            from app.handlers.callbacks.navigation import send_apple_id_success
            await send_apple_id_success(
                message.bot, telegram_id, region, nominal, payment_amount_rubles,
            )
            await database.mark_pending_purchase_paid(purchase_id)
            # N17: referral cashback for any purchase (shop too); never raises.
            await database.award_referral_cashback(
                buyer_id=telegram_id, purchase_id=purchase_id, amount_rubles=payment_amount_rubles)
            logger.info("APPLE_PAYMENT_FINALIZED purchase_id=%s user=%s", purchase_id, telegram_id)
        except Exception as e:
            logger.exception("APPLE_PAYMENT_ERROR purchase_id=%s error=%s", purchase_id, e)
            await message.answer(i18n_get_text(language, "errors.payment_processing"), parse_mode="HTML")
        await state.clear()
        return

    # --- Spotify purchase (spotify_purchase.cb_pay_card): mark paid + send success + notify admin ---
    # Mirrors the webhook shop branch (confirmation.process_confirmed_payment):
    # same detection, mark paid first (idempotent), then send_spotify_success.
    # Without this branch the purchase fell through to the VPN path below.
    _spotify_tariff = pending_purchase.get("tariff") or ""
    is_spotify_purchase = (
        pending_purchase.get("purchase_type") == "spotify"
        or _spotify_tariff.startswith("spotify_")
    )
    if is_spotify_purchase:
        try:
            marked = await database.mark_pending_purchase_paid(purchase_id)
        except Exception as e:
            logger.exception("SPOTIFY_PAYMENT_ERROR purchase_id=%s error=%s", purchase_id, e)
            # Money is taken, the order is not marked and nobody is told → forced alert.
            await _alert_shop_order_lost(
                message.bot, "shop_mark_paid_failed", pending_purchase, telegram_id,
                payment_amount_rubles,
                reason="mark_pending_purchase_paid упал (БД) — покупатель видит «ошибка обработки платежа»",
                error=e,
            )
            await message.answer(i18n_get_text(language, "errors.payment_processing"), parse_mode="HTML")
            await state.clear()
            return
        if not marked:
            logger.info(
                "SPOTIFY_PAYMENT_ALREADY_FINALIZED purchase_id=%s user=%s — skipping notification",
                purchase_id, telegram_id,
            )
            await state.clear()
            return
        # N17: referral cashback for any purchase (shop too); never raises.
        await database.award_referral_cashback(
            buyer_id=telegram_id, purchase_id=purchase_id, amount_rubles=payment_amount_rubles)
        try:
            from app.handlers.payments.spotify_purchase import send_spotify_success
            await send_spotify_success(
                message.bot, telegram_id, purchase_id, pending_purchase,
                provider=_SHOP_CARD_PROVIDER,
            )
        except Exception as e:
            logger.error(
                "SPOTIFY_PAYMENT_NOTIFY_FAILED purchase_id=%s error=%s",
                purchase_id, type(e).__name__,
            )
            await _alert_shop_order_lost(
                message.bot, "shop_admin_notify_failed", pending_purchase, telegram_id,
                payment_amount_rubles,
                reason="send_spotify_success упал после mark_pending_purchase_paid",
                error=e,
            )
        logger.info(
            "SPOTIFY_PAYMENT_FINALIZED purchase_id=%s user=%s amount=%s",
            purchase_id, telegram_id, payment_amount_rubles,
        )
        await state.clear()
        duration_ms = (time.time() - start_time) * 1000
        log_handler_exit(
            handler_name="process_successful_payment",
            outcome="success",
            telegram_id=telegram_id,
            operation="payment_finalization",
            duration_ms=duration_ms,
            payment_type="spotify",
        )
        return

    # --- Traffic pack purchase: finalize + add Remnawave traffic ---
    # 🔧 UNIFIED: Использует ту же логику, что и webhook-провайдеры (platega, cryptobot)
    # через _handle_traffic_pack_confirmation. Это гарантирует:
    #   • self-heal через get_bypass_entity_safe (защита от DB-корапшена
    #     remnawave_id → premium)
    #   • 3-branch fallback: top-up → username-resolve+retry → create fresh
    #   • корректные bypass-only setup, trial activation, кнопки/тексты
    # Раньше здесь был свой inline код на remnawave_service.add_traffic, который
    # НЕ имел self-heal и молча ронял PATCH через SAFETY-DROP на premium.
    is_traffic_pack = pending_purchase.get("purchase_type") == "traffic_pack"
    if is_traffic_pack:
        payment_provider_name = "telegram_stars" if is_stars_payment else "telegram_payment"
        try:
            traffic_result = await database.finalize_purchase(
                purchase_id=purchase_id,
                payment_provider=payment_provider_name,
                amount_rubles=payment_amount_rubles,
            )
            if traffic_result and traffic_result.get("is_traffic_pack"):
                await _remember_charge(traffic_result.get("payment_id"), payment)
                traffic_gb = int(traffic_result.get("traffic_gb", 0) or 0)
                tariff_tag = pending_purchase.get("tariff", "") or ""
                from app.services.payments.confirmation import _handle_traffic_pack_confirmation
                try:
                    await _handle_traffic_pack_confirmation(
                        provider=payment_provider_name,
                        bot=message.bot,
                        telegram_id=telegram_id,
                        payment_id=int(traffic_result.get("payment_id") or 0),
                        purchase_id=str(purchase_id),
                        traffic_gb=traffic_gb,
                        tariff_type=tariff_tag,
                    )
                    logger.info(
                        "TRAFFIC_PACK_PAYMENT_FINALIZED purchase_id=%s user=%s gb=%s provider=%s",
                        purchase_id, telegram_id, traffic_gb, payment_provider_name,
                    )
                except Exception as conf_err:
                    logger.exception(
                        "TRAFFIC_PACK_CONFIRMATION_FAIL user=%s gb=%s purchase=%s err=%s",
                        telegram_id, traffic_gb, purchase_id, conf_err,
                    )
                    await _alert_money_taken_not_granted(
                        message.bot, telegram_id=telegram_id, purchase_id=purchase_id, error=conf_err,
                        stage="telegram_traffic_pack_delivery_failed", provider=payment_provider_name,
                        amount_rubles=payment_amount_rubles, tariff=tariff_tag,
                    )
                    await message.answer(
                        i18n_get_text(language, "errors.payment_processing"),
                        parse_mode="HTML",
                    )
            else:
                logger.error(f"Traffic pack finalization unexpected result: {traffic_result}")
                await message.answer(i18n_get_text(language, "errors.payment_processing"), parse_mode="HTML")
                await _alert_money_taken_not_granted(
                    message.bot, telegram_id=telegram_id, purchase_id=purchase_id,
                    error="traffic pack finalization returned an unexpected result",
                    stage="telegram_traffic_pack_failed", provider=payment_provider_name,
                    amount_rubles=payment_amount_rubles, tariff=tariff_type,
                )
        except Exception as e:
            logger.exception(f"Traffic pack payment finalization failed: user={telegram_id}, error={e}")
            await message.answer(i18n_get_text(language, "errors.payment_processing"), parse_mode="HTML")
            await _alert_money_taken_not_granted(
                message.bot, telegram_id=telegram_id, purchase_id=purchase_id, error=e,
                stage="telegram_traffic_pack_failed", provider=payment_provider_name,
                amount_rubles=payment_amount_rubles, tariff=tariff_type,
            )
        await state.clear()
        duration_ms = (time.time() - start_time) * 1000
        log_handler_exit(
            handler_name="process_successful_payment",
            outcome="success",
            telegram_id=telegram_id,
            operation="payment_finalization",
            duration_ms=duration_ms,
            payment_type="traffic_pack",
        )
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
        await _remember_charge(payment_id, payment)
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
                    callback_data="menu_profile",
                    icon_custom_emoji_id=CE["profile"],
                    style="primary",
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
        await _alert_money_taken_not_granted(
            message.bot, telegram_id=telegram_id, purchase_id=purchase_id, error=e,
            stage="telegram_payment_rejected", provider=payment_provider_name,
            amount_rubles=payment_amount_rubles, tariff=tariff_type, period_days=period_days,
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
        
        await _alert_money_taken_not_granted(
            message.bot, telegram_id=telegram_id, purchase_id=purchase_id, error=e,
            stage="telegram_finalization_failed", provider=payment_provider_name,
            amount_rubles=payment_amount_rubles, tariff=tariff_type, period_days=period_days,
        )

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
        await _alert_money_taken_not_granted(
            message.bot, telegram_id=telegram_id, purchase_id=purchase_id, error=e,
            stage="telegram_unexpected_error", provider=payment_provider_name,
            amount_rubles=payment_amount_rubles, tariff=tariff_type, period_days=period_days,
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
    
    # One success message for every payment path (08_payments_ux #3): the tariff
    # change (upgrade) goes through the same builder and the same notification
    # flag (M18: it used to skip mark_payment_notification_sent).
    from app.services.payments.success_message import build_purchase_success
    text, keyboard = await build_purchase_success(
        language,
        subscription_type=subscription_type,
        is_combo=bool(getattr(result, "is_combo", False)),
        period_days=period_days,
        expires_at=expires_at,
        is_renewal=is_renewal,
        is_upgrade=bool(getattr(result, "is_basic_to_plus_upgrade", False)),
        telegram_id=telegram_id,
    )
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
        degradation = i18n_get_text(language, "trial.degradation_notice") if _degradation_notice else ""
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

    # The referrer is notified by the cashback accrual itself, once, after the
    # billing transaction commits (app.services.notifications.referral_cashback).

    logger.info(
        f"process_successful_payment: PAYMENT_COMPLETE [user={telegram_id}, payment_id={payment_id}, "
        f"tariff={tariff_type}, period_days={period_days}, amount={payment_amount_rubles} RUB, "
        f"purchase_id={purchase_id}, expires_at={expires_str}, vpn_key_sent=True, subscription_visible=True]"
    )

    fsm_data = await state.get_data()
    combo_bypass_gb = fsm_data.get("combo_bypass_gb", 0)
    bypass_only_gb = fsm_data.get("bypass_only_gb", 0)

    # T9: finalized through the provisioning outbox → the job "purchase:{id}"
    # owns premium + bypass GB (combo GB from pending_purchases, not FSM); the
    # combo traffic_purchases row was written in the billing tx and the combo
    # flag set by finalize_purchase. No renew_bg / add_bypass_traffic /
    # FSM combo top-up / record_traffic_purchase / set_combo_flag here.
    _via_outbox = getattr(result, "provisioning_job_id", None) is not None
    if _via_outbox:
        logger.info(
            "BYPASS_GB_VIA_OUTBOX: provider=%s user=%s purchase_id=%s job=%s done=%s "
            "is_combo=%s fsm_combo_gb=%s",
            payment_provider_name, telegram_id, purchase_id,
            getattr(result, "provisioning_job_id", None), getattr(result, "provisioning_done", None),
            getattr(result, "is_combo", False), combo_bypass_gb,
        )
        if bypass_only_gb > 0:
            # bypass_only_gb is set nowhere in the bot today; never top up here.
            logger.error(
                "TELEGRAM_BYPASS_ONLY_IGNORED_OUTBOX user=%s gb=%s purchase_id=%s",
                telegram_id, bypass_only_gb, purchase_id,
            )
    else:
        # Fire-and-forget: create or renew Remnawave bypass user
        # Skip for combo purchases — combo traffic is added separately below

        # CRITICAL FSM-FALLBACK: combo_bypass_gb is set in FSM state when the
        # invoice is created, but Telegram Payments are asynchronous — between
        # invoice and SUCCESSFUL_PAYMENT the user can open another menu (which
        # state.clear()s), or the bot can restart (in-memory FSM is gone).
        # If we lost the FSM but finalize tells us this was a combo, recover
        # the GB amount from config.COMBO_TARIFFS by tariff + period_days.
        # Without this, combo Юкасса-buyers got their subscription but NO bypass GB.
        if combo_bypass_gb <= 0 and getattr(result, "is_combo", False):
            _sub_type_for_combo = (
                getattr(result, "subscription_type", None)
                or (tariff_type or "basic")
            ).strip().lower()
            combo_key = f"combo_{_sub_type_for_combo}"
            combo_info = (config.COMBO_TARIFFS or {}).get(combo_key, {}).get(period_days)
            if combo_info and combo_info.get("gb"):
                combo_bypass_gb = int(combo_info["gb"])
                logger.warning(
                    "COMBO_BYPASS_FSM_FALLBACK user=%s gb=%s combo_key=%s period_days=%s "
                    "purchase_id=%s — FSM was empty, recovered from config",
                    telegram_id, combo_bypass_gb, combo_key, period_days, purchase_id,
                )
            else:
                logger.error(
                    "COMBO_BYPASS_FSM_FALLBACK_FAIL user=%s combo_key=%s period_days=%s "
                    "purchase_id=%s — combo config missing, GB cannot be granted",
                    telegram_id, combo_key, period_days, purchase_id,
                )

        try:
            from app.services.remnawave_service import renew_remnawave_user_bg
            _sub_type = (tariff_type or "basic").strip().lower()
            if expires_at and _sub_type != "trial" and combo_bypass_gb <= 0:
                renew_remnawave_user_bg(telegram_id, _sub_type, expires_at, period_days=period_days)
        except Exception as rmn_err:
            logger.warning("REMNAWAVE_HOOK_FAIL: stars tg=%s %s", telegram_id, rmn_err)

        # Combo/Bypass: начисляем трафик обхода если покупка была через комбо или bypass-only
        if combo_bypass_gb > 0 or bypass_only_gb > 0:
            from app.services import remnawave_service
            gb = combo_bypass_gb or bypass_only_gb
            traffic_bytes = gb * 1024**3

            try:
                rmn_success = await remnawave_service.add_bypass_traffic(
                    telegram_id,
                    traffic_bytes,
                    subscription_type=(tariff_type or "basic").strip().lower(),
                    subscription_end=expires_at,
                    period_days=period_days,
                )
                if not rmn_success:
                    logger.warning(f"COMBO_BYPASS_TRAFFIC_FAIL user={telegram_id} gb={gb}")
                await database.record_traffic_purchase(telegram_id, gb, 0)
                logger.info(f"COMBO_BYPASS_TRAFFIC_ADDED user={telegram_id} gb={gb} type={'combo' if combo_bypass_gb else 'bypass_only'}")
            except Exception as traffic_err:
                logger.warning(f"COMBO_BYPASS_TRAFFIC_ERROR user={telegram_id}: {traffic_err}")

            # Mark subscription as combo (OUTSIDE traffic try block)
            if combo_bypass_gb > 0:
                try:
                    await database.set_combo_flag(telegram_id, True)
                    logger.info(f"COMBO_FLAG_SET user={telegram_id}")
                except Exception as flag_err:
                    logger.warning(f"COMBO_FLAG_FAIL user={telegram_id}: {flag_err}")

        from app.services.payments import verify_delivery  # legacy: delayed panel check, alert-only
        verify_delivery.schedule_legacy_check(telegram_id, source="telegram", ref=str(purchase_id), expect_bypass=(tariff_type or "basic").strip().lower() != "trial")

    if combo_bypass_gb > 0 or bypass_only_gb > 0:
        # Bypass-only: activate the 3-day trial if available. T15: before, the
        # function did not exist (AttributeError swallowed → no trial). Now only
        # under the "trial" outbox flag; after the purchase commit, never raises
        # (failure → log + admin alert).
        from app.services import provisioning_flags
        if bypass_only_gb > 0 and provisioning_flags.is_on("trial"):
            from app.services.trials import service as trial_service
            if await trial_service.activate_trial_safely(
                telegram_id, bot=message.bot, where=f"telegram:{purchase_id}",
            ):
                logger.info(f"BYPASS_TRIAL_ACTIVATED user={telegram_id}")

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