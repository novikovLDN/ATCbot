# === STAGE STABLE SNAPSHOT ===
# Date: 2026-01-25
# Environment: STAGE
# WARNING:
# Business logic below is considered STABLE.
# Do NOT change behavior without:
#  - test case
#  - log proof
#  - rollback plan
# ==========================================

from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile, LabeledPrice, PreCheckoutQuery
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup, default_state
from aiogram.filters import StateFilter
from datetime import datetime, timedelta
import logging
import database
import config
import time
import csv
import tempfile
import os
import asyncio
import random
from typing import Optional, Dict, Any, Union
from app.services.subscriptions import service as subscription_service
from app.services.subscriptions.service import (
    is_subscription_active,
    get_subscription_status,
    check_and_disable_expired_subscription as check_subscription_expiry_service,
)
from app.services.payments import service as payment_service
from app.services.payments.exceptions import (
    PaymentServiceError,
    InvalidPaymentPayloadError,
    PaymentAmountMismatchError,
    PaymentAlreadyProcessedError,
    PaymentFinalizationError,
)
from app.core.system_state import (
    SystemState,
    healthy_component,
    degraded_component,
    unavailable_component,
)
from app.services.activation import service as activation_service
from app.services.trials import service as trial_service
from app.services.admin import service as admin_service
from app.services.admin.exceptions import (
    AdminServiceError,
    UserNotFoundError,
    InvalidAdminActionError,
)
from app.utils.logging_helpers import (
    log_handler_entry,
    log_handler_exit,
    classify_error,
)
from app.utils.referral_middleware import process_referral_on_first_interaction
from app.services.referrals import activate_referral, ReferralState
from app.services.language_service import resolve_user_language, DEFAULT_LANGUAGE
from app.i18n import get_text as i18n_get_text
from app.utils.security import (
    validate_telegram_id,
    validate_message_text,
    validate_callback_data,
    validate_payment_payload,
    validate_promo_code,
    require_admin,
    require_ownership,
    log_security_warning,
    log_security_error,
    log_audit_event,
    sanitize_for_logging,
)
from app.core.feature_flags import get_feature_flags
from app.core.circuit_breaker import get_circuit_breaker
from app.constants.loyalty import get_loyalty_status_names, get_loyalty_screen_attachment
from app.core.rate_limit import check_rate_limit

# Время запуска бота (для uptime)
_bot_start_time = time.time()


# ====================================================================================
# SAFE USERNAME RESOLUTION HELPER
# ====================================================================================

def safe_resolve_username(user_obj, language: str, telegram_id: int = None) -> str:
    """
    Безопасное разрешение username для отображения.
    
    Priority:
    1. user_obj.username (Telegram username)
    2. user_obj.first_name (имя пользователя)
    3. localized fallback (user_fallback key)
    
    Args:
        user_obj: Telegram user object (Message.from_user, CallbackQuery.from_user, etc.)
        language: User language for fallback text (from DB)
        telegram_id: Optional telegram ID for logging
    
    Returns:
        Строка для отображения (никогда не None)
    """
    if not user_obj:
        return i18n_get_text(language, "common.user")
    
    # Priority 1: Telegram username
    if hasattr(user_obj, 'username') and user_obj.username:
        return user_obj.username
    
    # Priority 2: First name
    if hasattr(user_obj, 'first_name') and user_obj.first_name:
        return user_obj.first_name
    
    # Priority 3: Fallback
    return i18n_get_text(language, "common.user")


def safe_resolve_username_from_db(user_dict: Optional[Dict], language: str, telegram_id: int = None) -> str:
    """
    Безопасное разрешение username из словаря пользователя из БД.
    
    Priority:
    1. user_dict.get("username")
    2. user_dict.get("first_name")
    3. "ID: <telegram_id>" if telegram_id provided
    4. localized fallback (user_fallback key)
    
    Args:
        user_dict: Словарь пользователя из БД
        language: User language for fallback text (from DB)
        telegram_id: Optional telegram ID for fallback
    
    Returns:
        Строка для отображения (никогда не None)
    """
    if not user_dict:
        if telegram_id:
            return f"ID: {telegram_id}"
        return i18n_get_text(language, "common.user")
    
    # Priority 1: Username from DB
    username = user_dict.get("username")
    if username:
        return username
    
    # Priority 2: First name from DB (if exists)
    first_name = user_dict.get("first_name")
    if first_name:
        return first_name
    
    # Priority 3: Telegram ID fallback
    if telegram_id:
        return f"ID: {telegram_id}"
    
    # Priority 4: Generic fallback
    return i18n_get_text(language, "common.user")


# ====================================================================================
# STEP 3 — FAILURE CONTAINMENT & RUNTIME SAFETY
# ====================================================================================
# 
# PART A — HARD FAILURE BOUNDARIES:
# - All handlers must have explicit exception boundaries
# - All workers must have top-level try/except in loops
# - No exception should propagate past its boundary
# 
# PART B — WORKER LOOP SAFETY:
# - Minimum safe sleep on failure (prevents tight retry storms)
# - Always sleep before next iteration
# 
# PART C — SIDE-EFFECT SAFETY:
# - Payment finalization: idempotency check in payment_service.check_payment_idempotency()
# - Subscription activation: idempotency check in activation_service
# - VPN provisioning: idempotency check in vpn_service
# 
# PART D — EXTERNAL DEPENDENCY ISOLATION:
# - VPN API calls: isolated in try/except, mapped to dependency_error
# - Payment provider calls: isolated in try/except, mapped to dependency_error
# - CryptoBot API calls: isolated in try/except, mapped to dependency_error
# 
# PART E — SECRET & CONFIG SAFETY:
# - Secrets never logged (sanitize_for_logging used)
# - Secrets never included in exceptions
# - Required env vars validated at startup (config.py)
# - Fail fast if critical secrets missing (config.py)
# 
# PART F — SECURITY LOGGING POLICY (COMMENTS ONLY):
# 
# SECURITY_WARNING (log_security_warning):
# - Unauthorized access attempts
# - Invalid input (malformed, oversized, unexpected)
# - Suspicious activity patterns
# - Failed authorization checks
# 
# SECURITY_ERROR (log_security_error):
# - Critical security failures
# - Potential attacks
# - System compromise attempts
# - Critical authorization failures
# 
# AUDIT_EVENT (log_audit_event):
# - Admin actions (all admin operations)
# - Payment finalization (successful and failed)
# - Subscription modifications
# - VPN operations
# - Privileged operations
# 
# What gets logged:
# - All security events with correlation_id
# - Admin actions with full context
# - Payment events (sanitized)
# - Authorization failures
# 
# What must NEVER be logged:
# - Secrets (BOT_TOKEN, API keys, passwords)
# - Full payment payloads (only sanitized)
# - Full user data (only IDs and non-sensitive fields)
# - Database connection strings
# 
# Correlation ID usage:
# - All security logs include correlation_id for tracing
# - correlation_id = message_id for handlers
# - correlation_id = iteration_id for workers
# 
# WARNING (logger.warning):
# - Expected failures: DB temporarily unavailable, VPN API disabled, payment provider timeout
# - Transient errors: Network timeouts, connection errors (will retry)
# - Degraded state: System continues with reduced functionality
# - Idempotency skips: Payment already processed, subscription already activated
# 
# ERROR (logger.error):
# - Unexpected failures: Unhandled exceptions, invariant violations
# - Critical errors: Payment finalization failures, activation failures after max attempts
# - Domain errors: Invalid payment amount, invalid subscription state
# 
# Admin alert (admin_notifications):
# - Payment failures: Payment received but finalization failed
# - Activation failures: Subscription activation failed after max attempts
# - System unavailable: System state is UNAVAILABLE for extended period
# 
# Suppress (no logging or minimal logging):
# - Idempotency skips: Payment already processed (logged as INFO, not ERROR)
# - Expected domain errors: Invalid payload format (logged as ERROR but not escalated)
# - VPN API disabled: NOT an error state (logged as WARNING, not ERROR)
# ====================================================================================


def handler_exception_boundary(handler_name: str, operation: str = None):
    """
    Decorator for explicit handler exception boundaries.
    
    STEP 3 — PART A: HARD FAILURE BOUNDARIES
    Ensures no exception propagates past handler boundary.
    
    Args:
        handler_name: Name of the handler function
        operation: Operation name (defaults to handler_name)
    
    Usage:
        @handler_exception_boundary("cmd_start", "user_start")
        @router.message(Command("start"))
        async def cmd_start(message: Message):
            ...
    """
    def decorator(func):
        async def wrapper(*args, **kwargs):
            # Extract correlation_id from message/callback if available
            correlation_id = None
            telegram_id = None
            
            # Try to extract from first argument (Message or CallbackQuery)
            if args and hasattr(args[0], 'message_id'):
                correlation_id = str(args[0].message_id)
            elif args and hasattr(args[0], 'message') and hasattr(args[0].message, 'message_id'):
                correlation_id = str(args[0].message.message_id)
            
            if args and hasattr(args[0], 'from_user'):
                telegram_id = args[0].from_user.id
            elif args and hasattr(args[0], 'message') and hasattr(args[0].message, 'from_user'):
                telegram_id = args[0].message.from_user.id
            
            start_time = time.time()
            op_name = operation or handler_name
            
            # Log handler entry
            log_handler_entry(
                handler_name=handler_name,
                telegram_id=telegram_id,
                operation=op_name,
                correlation_id=correlation_id,
            )
            
            # PART 3 — BUTTON HANDLING WHEN DB IS NOT READY
            # Centralized early-exit guard for DB readiness
            if not database.DB_READY:
                # Extract message/callback for sending warning
                message_or_query = args[0] if args else None
                if message_or_query:
                    try:
                        warning_text = i18n_get_text(
                            "ru",
                            "errors.db_init_stage_warning"
                        )
                        if hasattr(message_or_query, 'answer') and hasattr(message_or_query, 'text'):
                            # This is a Message
                            await message_or_query.answer(warning_text)
                        elif hasattr(message_or_query, 'message') and hasattr(message_or_query, 'answer'):
                            # This is a CallbackQuery
                            await message_or_query.message.answer(warning_text)
                            await message_or_query.answer()
                    except Exception as e:
                        logger.debug(f"Error sending DB not ready warning: {e}")
                # Return early without executing handler
                return
            
            # PART C.7: Handlers MUST check system_state.is_available
            # Return user-friendly message, NEVER throw exceptions in degraded mode
            # Note: DB_READY check above already handles database unavailability
            # Additional system_state check can be added here if needed for other components
            
            try:
                # Execute handler
                result = await func(*args, **kwargs)
                
                # Log successful exit
                duration_ms = (time.time() - start_time) * 1000
                log_handler_exit(
                    handler_name=handler_name,
                    outcome="success",
                    telegram_id=telegram_id,
                    operation=op_name,
                    duration_ms=duration_ms,
                )
                
                return result
                
            except Exception as e:
                # STEP 3 — PART A: HARD FAILURE BOUNDARIES
                # Exception caught at handler boundary - handler exits gracefully
                # No exception propagates past this boundary
                
                duration_ms = (time.time() - start_time) * 1000
                error_type = classify_error(e)
                
                # Log failure
                logger.error(
                    f"[FAILURE_BOUNDARY] Handler exception caught: handler={handler_name}, "
                    f"operation={op_name}, correlation_id={correlation_id}, "
                    f"error_type={error_type}, error={type(e).__name__}: {str(e)[:200]}"
                )
                
                log_handler_exit(
                    handler_name=handler_name,
                    outcome="failed",
                    telegram_id=telegram_id,
                    operation=op_name,
                    error_type=error_type,
                    duration_ms=duration_ms,
                    reason=f"Exception: {type(e).__name__}"
                )
                
                # Handler exits gracefully - no exception propagation
                # User may see generic error message if handler didn't send one
                try:
                    # Try to send generic error message if we have message/callback
                    if args and hasattr(args[0], 'answer'):
                        event = args[0]
                        tid = getattr(getattr(event, 'from_user', None), 'id', None) or telegram_id
                        language = await resolve_user_language(tid) if tid else DEFAULT_LANGUAGE
                        error_text = i18n_get_text(language, "main.error_occurred")
                        await args[0].answer(error_text)
                except Exception:
                    # If we can't send error message, that's OK - handler still exits gracefully
                    pass
                
                # Handler boundary: exception does NOT propagate
                return None
        
        return wrapper
    return decorator


# ====================================================================================
# SAFE STARTUP GUARD: Helper функции для проверки готовности БД
# ====================================================================================

async def safe_edit_text(message: Message, text: str, reply_markup: InlineKeyboardMarkup = None, parse_mode: str = None, bot: Bot = None):
    """
    Безопасное редактирование текста сообщения с обработкой ошибок
    
    Сравнивает текущий контент с новым перед редактированием, чтобы избежать ненужных вызовов API.
    Если сообщение недоступно (inaccessible), использует send_message вместо edit_message.
    
    Args:
        message: Message объект для редактирования
        text: Новый текст сообщения
        reply_markup: Новая клавиатура (опционально) - MUST be InlineKeyboardMarkup, NOT coroutine
        parse_mode: Режим парсинга (HTML, Markdown и т.д.)
        bot: Bot instance (требуется для fallback на send_message)
    """
    # Защита от передачи coroutine вместо InlineKeyboardMarkup
    if asyncio.iscoroutine(reply_markup):
        raise RuntimeError("reply_markup coroutine passed without await. Must await keyboard builder before passing to safe_edit_text.")
    
    # КРИТИЧЕСКАЯ ПРОВЕРКА: Проверяем, что message доступен (не inaccessible/deleted)
    # В aiogram 3.x нет типа InaccessibleMessage, проверяем через hasattr
    if not hasattr(message, 'chat'):
        # Сообщение недоступно - используем send_message как fallback
        if bot is None:
            logger.warning("Message is inaccessible (no chat attr) and bot not provided, cannot send fallback message")
            return
        try:
            # Пытаемся получить chat_id из других источников
            chat_id = None
            if hasattr(message, 'from_user') and hasattr(message.from_user, 'id'):
                chat_id = message.from_user.id
            
            if chat_id:
                await bot.send_message(chat_id, text, reply_markup=reply_markup, parse_mode=parse_mode)
                logger.info(f"Message inaccessible (no chat attr), sent new message instead: chat_id={chat_id}")
            else:
                logger.warning("Message inaccessible (no chat attr) and cannot determine chat_id")
        except Exception as send_error:
            logger.error(f"Failed to send fallback message after inaccessible check: {send_error}")
        return
    
    # Безопасная проверка атрибутов сообщения (никогда не обращаемся напрямую без hasattr)
    current_text = None
    try:
        if hasattr(message, 'text'):
            text_attr = getattr(message, 'text', None)
            if text_attr:
                current_text = text_attr
        if not current_text and hasattr(message, 'caption'):
            caption_attr = getattr(message, 'caption', None)
            if caption_attr:
                current_text = caption_attr
    except AttributeError:
        # Защита от AttributeError - сообщение может быть недоступно
        logger.debug("AttributeError while checking message text/caption, treating as inaccessible")
        current_text = None
    
    # Сравниваем текущий текст с новым (безопасно)
    if current_text and current_text == text:
        # Текст совпадает - проверяем клавиатуру (безопасно)
        current_markup = None
        try:
            if hasattr(message, 'reply_markup'):
                markup_attr = getattr(message, 'reply_markup', None)
                if markup_attr:
                    current_markup = markup_attr
        except AttributeError:
            # Защита от AttributeError
            current_markup = None
        
        if reply_markup is None:
            # Удаление клавиатуры - проверяем, есть ли она
            if current_markup is None:
                # Контент идентичен - не вызываем edit
                return
        else:
            # Сравниваем клавиатуры (упрощённая проверка)
            if current_markup and _markups_equal(current_markup, reply_markup):
                # Контент идентичен - не вызываем edit
                return
    
    # Photo message: edit caption instead of text (e.g. loyalty screen sent as send_photo).
    # Prevents TelegramBadRequest "there is no text in the message to edit".
    has_photo = getattr(message, "photo", None) and len(message.photo) > 0
    if has_photo:
        try:
            await message.edit_caption(caption=text, reply_markup=reply_markup, parse_mode=parse_mode)
            return
        except TelegramBadRequest as e:
            err = str(e).lower()
            if "message is not modified" in err:
                logger.debug(f"Caption not modified (expected): {e}")
                return
            if any(k in err for k in ["message to edit not found", "message can't be edited", "chat not found", "message is inaccessible"]):
                if bot is not None:
                    chat_id = getattr(getattr(message, "chat", None), "id", None) or (getattr(getattr(message, "from_user", None), "id", None) if getattr(message, "from_user", None) else None)
                    if chat_id:
                        await bot.send_message(chat_id, text, reply_markup=reply_markup, parse_mode=parse_mode)
                        logger.info(f"Photo message inaccessible, sent new message instead: chat_id={chat_id}")
                return
            raise

    try:
        await message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except TelegramBadRequest as e:
        error_msg = str(e).lower()
        if "message is not modified" in error_msg:
            # Игнорируем ошибку "message is not modified" - сообщение уже имеет нужное содержимое
            logger.debug(f"Message not modified (expected): {e}")
            return
        elif any(keyword in error_msg for keyword in ["message to edit not found", "message can't be edited", "chat not found", "message is inaccessible"]):
            # Сообщение недоступно - используем send_message как fallback
            if bot is None:
                logger.warning(f"Message inaccessible and bot not provided, cannot send fallback message: {e}")
                return
            
            try:
                # Получаем chat_id безопасно (никогда не обращаемся напрямую без hasattr)
                chat_id = None
                try:
                    if hasattr(message, 'chat'):
                        chat_obj = getattr(message, 'chat', None)
                        if chat_obj and hasattr(chat_obj, 'id'):
                            chat_id = getattr(chat_obj, 'id', None)
                except AttributeError:
                    pass
                
                if not chat_id:
                    try:
                        if hasattr(message, 'from_user'):
                            user_obj = getattr(message, 'from_user', None)
                            if user_obj and hasattr(user_obj, 'id'):
                                chat_id = getattr(user_obj, 'id', None)
                    except AttributeError:
                        pass
                
                if chat_id:
                    await bot.send_message(chat_id, text, reply_markup=reply_markup, parse_mode=parse_mode)
                    logger.info(f"Message inaccessible, sent new message instead: chat_id={chat_id}")
                else:
                    logger.warning(f"Message inaccessible and cannot determine chat_id: {e}")
            except Exception as send_error:
                logger.error(f"Failed to send fallback message after edit failure: {send_error}")
        else:
            # Другие ошибки - пробрасываем
            raise
    except AttributeError as e:
        # Защита от AttributeError при обращении к атрибутам сообщения
        logger.warning(f"AttributeError in safe_edit_text, message may be inaccessible: {e}")
        # Пытаемся использовать send_message как fallback
        if bot is not None:
            try:
                # Получаем chat_id безопасно (никогда не обращаемся напрямую без hasattr)
                chat_id = None
                try:
                    if hasattr(message, 'chat'):
                        chat_obj = getattr(message, 'chat', None)
                        if chat_obj and hasattr(chat_obj, 'id'):
                            chat_id = getattr(chat_obj, 'id', None)
                except AttributeError:
                    pass
                
                if not chat_id:
                    try:
                        if hasattr(message, 'from_user'):
                            user_obj = getattr(message, 'from_user', None)
                            if user_obj and hasattr(user_obj, 'id'):
                                chat_id = getattr(user_obj, 'id', None)
                    except AttributeError:
                        pass
                
                if chat_id:
                    await bot.send_message(chat_id, text, reply_markup=reply_markup, parse_mode=parse_mode)
                    logger.info(f"AttributeError handled, sent new message instead: chat_id={chat_id}")
                else:
                    logger.warning(f"AttributeError handled but cannot determine chat_id: {e}")
            except Exception as send_error:
                logger.error(f"Failed to send fallback message after AttributeError: {send_error}")


def _markups_equal(markup1: InlineKeyboardMarkup, markup2: InlineKeyboardMarkup) -> bool:
    """
    Упрощённое сравнение клавиатур (проверка по callback_data)
    
    Args:
        markup1: Первая клавиатура
        markup2: Вторая клавиатура
    
    Returns:
        True если клавиатуры идентичны, False иначе
    """
    try:
        if markup1 is None and markup2 is None:
            return True
        if markup1 is None or markup2 is None:
            return False
        
        kb1 = markup1.inline_keyboard if hasattr(markup1, 'inline_keyboard') else []
        kb2 = markup2.inline_keyboard if hasattr(markup2, 'inline_keyboard') else []
        
        if len(kb1) != len(kb2):
            return False
        
        for row1, row2 in zip(kb1, kb2):
            if len(row1) != len(row2):
                return False
            for btn1, btn2 in zip(row1, row2):
                if btn1.callback_data != btn2.callback_data:
                    return False
        
        return True
    except Exception:
        # При ошибке сравнения считаем, что клавиатуры разные
        return False


async def safe_edit_reply_markup(message: Message, reply_markup: InlineKeyboardMarkup = None):
    """
    Безопасное редактирование клавиатуры сообщения с обработкой ошибки "message is not modified"
    
    Сравнивает текущую клавиатуру с новой перед редактированием.
    
    Args:
        message: Message объект для редактирования
        reply_markup: Новая клавиатура (или None для удаления)
    """
    # Сравниваем текущую клавиатуру с новой
    if reply_markup is None:
        if message.reply_markup is None:
            # Клавиатура уже удалена - не вызываем edit
            return
    else:
        if message.reply_markup and _markups_equal(message.reply_markup, reply_markup):
            # Клавиатуры идентичны - не вызываем edit
            return
    
    try:
        await message.edit_reply_markup(reply_markup=reply_markup)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise
        # Игнорируем ошибку "message is not modified" - клавиатура уже имеет нужное содержимое
        logger.debug(f"Reply markup not modified (expected): {e}")

# ====================================================================================
# PROMO SESSION MANAGEMENT (In-memory, 5-minute TTL)
# ====================================================================================

async def get_promo_session(state: FSMContext) -> Optional[Dict[str, Any]]:
    """
    Получить активную промо-сессию из FSM state
    
    Returns:
        {
            "promo_code": str,
            "discount_percent": int,
            "expires_at": float (unix timestamp)
        } или None если сессия отсутствует или истекла
    """
    fsm_data = await state.get_data()
    promo_session = fsm_data.get("promo_session")
    
    if not promo_session:
        return None
    
    # Проверяем срок действия
    expires_at = promo_session.get("expires_at")
    current_time = time.time()
    
    if expires_at and current_time > expires_at:
        # Сессия истекла - удаляем её
        await state.update_data(promo_session=None)
        telegram_id = fsm_data.get("_telegram_id", "unknown")
        logger.info(
            f"promo_session_expired: user={telegram_id}, "
            f"promo_code={promo_session.get('promo_code')}"
        )
        return None
    
    return promo_session


async def create_promo_session(
    state: FSMContext,
    promo_code: str,
    discount_percent: int,
    telegram_id: int,
    ttl_seconds: int = 300
) -> Dict[str, Any]:
    """
    Создать промо-сессию с TTL
    
    Args:
        state: FSM context
        promo_code: Код промокода
        discount_percent: Процент скидки
        telegram_id: Telegram ID пользователя (для логирования)
        ttl_seconds: Время жизни в секундах (по умолчанию 300 = 5 минут)
    
    Returns:
        Созданная промо-сессия
    """
    current_time = time.time()
    expires_at = current_time + ttl_seconds
    
    promo_session = {
        "promo_code": promo_code.upper(),
        "discount_percent": discount_percent,
        "expires_at": expires_at
    }
    
    await state.update_data(promo_session=promo_session, _telegram_id=telegram_id)
    
    expires_in = int(expires_at - current_time)
    logger.info(
        f"promo_session_created: user={telegram_id}, promo_code={promo_code.upper()}, "
        f"discount_percent={discount_percent}%, expires_in={expires_in}s"
    )
    
    return promo_session


async def clear_promo_session(state: FSMContext):
    """Удалить промо-сессию"""
    await state.update_data(promo_session=None)


# ====================================================================================
async def ensure_db_ready_message(message_or_query, allow_readonly_in_stage: bool = False) -> bool:
    """
    Проверка готовности базы данных с отправкой сообщения пользователю
    
    НОВАЯ ЛОГИКА:
    - CRITICAL ошибки (users table missing) → блокируем UI в PROD
    - NON-CRITICAL ошибки (audit_log, incident_settings missing) → НЕ блокируем UI
    - В STAGE разрешаем read-only операции даже при отсутствии опциональных таблиц
    
    Args:
        message_or_query: Message или CallbackQuery объект
        allow_readonly_in_stage: Если True, в STAGE разрешает read-only операции без БД
        
    Returns:
        True если БД готова или операция разрешена, False если БД недоступна (сообщение отправлено)
    """
    # Проверяем CRITICAL таблицы (users) - это определяет, можем ли мы работать вообще
    critical_ok = await database.check_critical_tables()
    
    if not critical_ok:
        # CRITICAL ошибка - users table отсутствует
        # В STAGE разрешаем read-only операции (меню, профиль, навигация)
        # В PROD всегда блокируем
        if allow_readonly_in_stage and config.IS_STAGE:
            return True
        
        # DB unavailable: use canonical fallback from service (do not call DB)
        language = DEFAULT_LANGUAGE
        
        # Получаем текст сообщения в зависимости от окружения
        if config.IS_PROD:
            error_text = i18n_get_text(language, "main.service_unavailable")
        else:
            # STAGE/LOCAL: более мягкое сообщение (language=ru when DB unavailable)
            error_text = i18n_get_text(language, "errors.db_init_stage_warning")
        
        # Отправляем сообщение
        try:
            if hasattr(message_or_query, 'answer') and hasattr(message_or_query, 'text'):
                # Это Message
                await message_or_query.answer(error_text)
            elif hasattr(message_or_query, 'message') and hasattr(message_or_query, 'answer'):
                # Это CallbackQuery
                await message_or_query.message.answer(error_text)
                await message_or_query.answer()
        except Exception as e:
            logging.exception(f"Error sending degraded mode message: {e}")
        
        return False
    
    # CRITICAL таблицы существуют - разрешаем работу
    # Даже если DB_READY = False (из-за отсутствия опциональных таблиц), мы можем работать
    return True


async def ensure_db_ready_callback(callback: CallbackQuery, allow_readonly_in_stage: bool = False) -> bool:
    """
    Проверка готовности базы данных для CallbackQuery (для удобства)
    
    Args:
        callback: CallbackQuery объект
        allow_readonly_in_stage: Если True, в STAGE разрешает read-only операции без БД
        
    Returns:
        True если БД готова или операция разрешена в STAGE, False если БД недоступна (сообщение отправлено)
    """
    return await ensure_db_ready_message(callback, allow_readonly_in_stage=allow_readonly_in_stage)


class AdminUserSearch(StatesGroup):
    waiting_for_user_id = State()


class AdminReferralSearch(StatesGroup):
    waiting_for_search_query = State()


class BroadcastCreate(StatesGroup):
    waiting_for_title = State()
    waiting_for_test_type = State()
    waiting_for_message = State()
    waiting_for_message_a = State()
    waiting_for_message_b = State()
    waiting_for_type = State()
    waiting_for_segment = State()
    waiting_for_confirm = State()


class IncidentEdit(StatesGroup):
    waiting_for_text = State()


class AdminGrantAccess(StatesGroup):
    waiting_for_days = State()  # Quick actions (1/7/14 days, 1 year, 10 minutes)
    waiting_for_unit = State()  # 1️⃣ Select unit (days/hours/minutes) for custom duration
    waiting_for_value = State()  # 3️⃣ Enter numeric value
    waiting_for_notify = State()  # 4️⃣ Notify user choice (yes/no)
    confirming = State()


class AdminRevokeAccess(StatesGroup):
    waiting_for_notify_choice = State()  # PART 4: Notify choice for revoke
    confirming = State()


class AdminDiscountCreate(StatesGroup):
    waiting_for_percent = State()
    waiting_for_expires = State()


class CorporateAccessRequest(StatesGroup):
    waiting_for_confirmation = State()


class PromoCodeInput(StatesGroup):
    waiting_for_promo = State()


class TopUpStates(StatesGroup):
    waiting_for_amount = State()


class AdminCreditBalance(StatesGroup):
    waiting_for_user_search = State()
    waiting_for_amount = State()
    waiting_for_confirmation = State()


class PurchaseState(StatesGroup):
    """FSM состояния для процесса покупки"""
    choose_tariff = State()           # Выбор тарифа (Basic/Plus)
    choose_period = State()           # Выбор периода (1/3/6/12 месяцев)
    choose_payment_method = State()   # Выбор способа оплаты (баланс/карта)
    processing_payment = State()      # Обработка оплаты (invoice создан или баланс списывается)

router = Router()

logger = logging.getLogger(__name__)


# Функция send_vpn_keys_alert удалена - больше не используется
# VPN-ключи теперь создаются динамически через Xray API, лимита нет

def get_language_keyboard(language: str = "ru"):
    """Клавиатура для выбора языка (языковые названия показываются в нативной форме)"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=i18n_get_text(language, "lang.button_ru"), callback_data="lang_ru"),
            InlineKeyboardButton(text=i18n_get_text(language, "lang.button_en"), callback_data="lang_en"),
        ],
        [
            InlineKeyboardButton(text=i18n_get_text(language, "lang.button_de"), callback_data="lang_de"),
            InlineKeyboardButton(text=i18n_get_text(language, "lang.button_kk"), callback_data="lang_kk"),
        ],
        [
            InlineKeyboardButton(text=i18n_get_text(language, "lang.button_ar"), callback_data="lang_ar"),
        ],
        [
            InlineKeyboardButton(text=i18n_get_text(language, "lang.button_uz"), callback_data="lang_uz"),
            InlineKeyboardButton(text=i18n_get_text(language, "lang.button_tj"), callback_data="lang_tj"),
        ],
    ])
    return keyboard


async def format_text_with_incident(text: str, language: str) -> str:
    """Добавить баннер инцидента к тексту, если режим активен"""
    # Безопасный вызов: если БД не готова или таблица не существует, пропускаем инцидент
    try:
        if not database.DB_READY:
            return text
        incident = await database.get_incident_settings()
        if incident and incident.get("is_active"):
            banner = i18n_get_text(language, "incident.banner")
            incident_text = incident.get("incident_text")
            if incident_text:
                banner += f"\n{incident_text}"
            return f"{banner}\n\n⸻\n\n{text}"
        return text
    except Exception as e:
        # Если таблица incident_settings не существует или другая ошибка - просто возвращаем текст
        logger.warning(f"Error getting incident settings: {e}")
        return text


async def get_main_menu_keyboard(language: str, telegram_id: int = None):
    """Клавиатура главного меню
    
    Args:
        language: Язык пользователя
        telegram_id: Telegram ID пользователя (обязательно для проверки trial availability)
    
    Кнопка "Пробный период 3 дня" показывается ТОЛЬКО если:
    - trial_used_at IS NULL
    - Нет активной подписки
    - Нет платных подписок в истории (source='payment')
    """
    buttons = []
    
    # КРИТИЧНО: Кнопка "Пробный период 3 дня" только для новых пользователей
    # Используем trial service для строгой проверки всех условий
    if telegram_id and database.DB_READY:
        try:
            is_available = await trial_service.is_trial_available(telegram_id)
            if is_available:
                buttons.append([InlineKeyboardButton(
                    text=i18n_get_text(language, "trial.button"),
                    callback_data="activate_trial"
                )])
        except Exception as e:
            logger.warning(f"Error checking trial availability for user {telegram_id}: {e}")
    
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "main.profile"),
        callback_data="menu_profile"
    )])
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "main.buy"),
        callback_data="menu_buy_vpn"
    )])
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "main.instruction"),
        callback_data="menu_instruction"
    )])
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "main.referral"),
        callback_data="menu_referral"
    )])
    buttons.append([
        InlineKeyboardButton(
            text=i18n_get_text(language, "main.about"),
            callback_data="menu_about"
        ),
        InlineKeyboardButton(
            text=i18n_get_text(language, "main.help"),
            callback_data="menu_support"
        ),
    ])
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "lang.change"),
        callback_data="change_language"
    )])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_back_keyboard(language: str):
    """Кнопка Назад"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="menu_main"
        )]
    ])


def get_profile_keyboard(language: str, has_active_subscription: bool = False, auto_renew: bool = False):
    """Клавиатура профиля (обновленная версия)"""
    buttons = []
    
    # Кнопка продления или покупки подписки
    if has_active_subscription:
        # Если есть активная подписка - показываем кнопку продления
        buttons.append([InlineKeyboardButton(
            text=i18n_get_text(language, "subscription.renew"),
            callback_data="menu_buy_vpn"  # Используем стандартный flow покупки/продления
        )])
        
        # Кнопка автопродления (только для активных подписок)
        if auto_renew:
            buttons.append([InlineKeyboardButton(
                text=i18n_get_text(language, "subscription.auto_renew_disable"),
                callback_data="toggle_auto_renew:off"
            )])
        else:
            buttons.append([InlineKeyboardButton(
                text=i18n_get_text(language, "subscription.auto_renew_enable"),
                callback_data="toggle_auto_renew:on"
            )])
    else:
        # Если нет активной подписки - показываем кнопку покупки
        buttons.append([InlineKeyboardButton(
            text=i18n_get_text(language, "main.buy"),
            callback_data="menu_buy_vpn"
        )])
    
    # Кнопка пополнения баланса (всегда показываем)
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "profile.topup_balance"),
        callback_data="topup_balance"
    )])
    
    # Кнопка копирования ключа (one-tap copy, всегда показываем)
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "profile.copy_key"),
        callback_data="copy_key"
    )])
    
    # Кнопка "Назад"
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "common.back"),
        callback_data="menu_main"
    )])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    return keyboard


def get_profile_keyboard_with_copy(language: str, last_tariff: str = None, is_vip: bool = False, has_subscription: bool = True):
    """Клавиатура профиля с кнопкой копирования ключа и историей (старая версия, для совместимости)"""
    return get_profile_keyboard(language, has_subscription)


def get_profile_keyboard_old(language: str):
    """Клавиатура с кнопками профиля и инструкции (после активации) - старая версия, переименована"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=i18n_get_text(language, "main.profile"),
                callback_data="menu_profile"
            ),
            InlineKeyboardButton(
                text=i18n_get_text(language, "main.instruction"),
                callback_data="menu_instruction"
            ),
        ],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "profile.copy_key"),
            callback_data="copy_key"
        )]
    ])
    return keyboard


def get_vpn_key_keyboard(language: str):
    """Клавиатура для экрана выдачи VPN-ключа после оплаты"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.go_to_connection"),
            callback_data="menu_instruction"
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "profile.copy_key"),
            callback_data="copy_vpn_key"
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "main.profile"),
            callback_data="go_profile"
        )],
    ])
    return keyboard


async def get_tariff_keyboard(language: str, telegram_id: int, promo_code: str = None, purchase_id: str = None):
    """Клавиатура выбора тарифа с учетом скидок (промокод имеет высший приоритет)
    
    DEPRECATED: Эта функция больше не используется напрямую.
    Кнопки тарифов создаются в callback_tariff_type с использованием calculate_final_price.
    
    Args:
        language: Язык пользователя
        telegram_id: Telegram ID пользователя
        promo_code: Промокод (опционально)
        purchase_id: ID покупки (опционально, больше не используется)
    """
    # Эта функция оставлена для обратной совместимости, но не должна использоваться
    # Реальная логика находится в callback_tariff_type
    buttons = []
    
    for tariff_key in config.TARIFFS.keys():
        base_text = i18n_get_text(language, "buy.tariff_button_" + str(tariff_key), f"tariff_button_{tariff_key}")
        buttons.append([InlineKeyboardButton(text=base_text, callback_data=f"tariff_type:{tariff_key}")])
    
    # Кнопка ввода промокода
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "buy.enter_promo"),
        callback_data="enter_promo"
    )])
    
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "common.back"),
        callback_data="menu_main"
    )])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_payment_method_keyboard(language: str):
    """Клавиатура выбора способа оплаты"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n_get_text(language, "payment.test", "payment_test"),
            callback_data="payment_test"
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "payment.sbp", "payment_sbp"),
            callback_data="payment_sbp"
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="menu_buy_vpn"
        )],
    ])
    return keyboard


def get_sbp_payment_keyboard(language: str):
    """Клавиатура для оплаты СБП"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n_get_text(language, "payment.paid_button", "paid_button"),
            callback_data="payment_paid"
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="menu_main"
        )],
    ])
    return keyboard


def get_pending_payment_keyboard(language: str):
    """Клавиатура после нажатия 'Я оплатил'"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="menu_main"
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "main.support", "support"),
            callback_data="menu_support"
        )],
    ])
    return keyboard


def get_about_keyboard(language: str):
    """Клавиатура раздела 'О сервисе'"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n_get_text(language, "main.privacy_policy", "privacy_policy"),
            callback_data="about_privacy"
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "main.our_channel"),
            url="https://t.me/atlas_secure"
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="menu_main"
        )],
    ])
    return keyboard


def get_service_status_keyboard(language: str):
    """Клавиатура экрана 'Статус сервиса'"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="menu_main"
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "main.support", "support"),
            callback_data="menu_support"
        )],
    ])
    return keyboard


def get_support_keyboard(language: str):
    """Клавиатура раздела 'Поддержка'"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n_get_text(language, "support.write_button"),
            url="https://t.me/asc_support"
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="menu_main"
        )],
    ])
    return keyboard


def detect_platform(callback_or_message) -> str:
    """
    Определить платформу пользователя (iOS, Android, или unknown)
    
    Использует эвристики для определения платформы:
    1. Primary: language_code (косвенный сигнал)
    2. Secondary: проверка доступных полей в объекте
    3. Fallback: "unknown" (показываем все кнопки)
    
    Args:
        callback_or_message: CallbackQuery или Message объект из aiogram
    
    Returns:
        "ios", "android", или "unknown"
    """
    try:
        # Получаем пользователя
        if hasattr(callback_or_message, 'from_user'):
            user = callback_or_message.from_user
        elif hasattr(callback_or_message, 'user'):
            user = callback_or_message.user
        else:
            return "unknown"
        
        # PRIMARY: Используем language_code как косвенный сигнал
        # Примечание: это не надежный метод, но может помочь в некоторых случаях
        language_code = getattr(user, 'language_code', None)
        
        if language_code:
            lang_lower = language_code.lower()
            # Эвристика: iOS часто использует региональные коды (ru-RU, en-US)
            # Android чаще использует простые коды (ru, en)
            # Это НЕ надежно, но может помочь в некоторых случаях
            
            # Если language_code содержит дефис (региональный код), склоняемся к iOS
            if '-' in language_code:
                # Это может быть iOS (региональные коды)
                # Но не уверены, поэтому используем как слабый сигнал
                pass
        
        # SECONDARY: Проверка через callback query (если доступно)
        if hasattr(callback_or_message, 'chat_instance'):
            # chat_instance может содержать некоторую информацию о клиенте
            # но не содержит прямой информации о платформе
            pass
        
        # Проверка через web_app (если используется в будущем)
        if hasattr(callback_or_message, 'web_app'):
            # Если пользователь использует Web App, можем определить платформу
            # через navigator.userAgent в клиенте
            # Но это требует реализации на стороне клиента
            pass
        
        # К сожалению, Telegram Bot API не предоставляет прямую информацию о платформе
        # Возвращаем "unknown" для безопасного fallback (показываем все кнопки)
        # 
        # В будущем можно улучшить:
        # 1. Хранить платформу в БД при первом взаимодействии (если пользователь сообщает)
        # 2. Использовать Telegram Web App с определением платформы через JS
        # 3. Анализ паттернов поведения пользователя
        # 4. Использование Mini Apps для определения платформы
        
        return "unknown"
    
    except Exception as e:
        logging.debug(f"Platform detection error: {e}")
        return "unknown"


def get_instruction_keyboard(language: str, platform: str = "unknown"):
    """
    Клавиатура экрана 'Инструкция' для v2RayTun
    
    Args:
        language: Язык пользователя
        platform: Платформа пользователя ("ios", "android", или "unknown")
    """
    buttons = []
    
    # Определяем какие кнопки скачивания показывать
    if platform == "ios":
        # Только iOS
        buttons.append([
            InlineKeyboardButton(
                text=i18n_get_text(language, "instruction._download_ios", "instruction_download_ios"),
                url="https://apps.apple.com/ua/app/v2raytun/id6476628951"
            )
        ])
    elif platform == "android":
        # Только Android
        buttons.append([
            InlineKeyboardButton(
                text=i18n_get_text(language, "instruction._download_android", "instruction_download_android"),
                url="https://play.google.com/store/apps/details?id=com.v2raytun.android"
            )
        ])
    else:
        # Unknown - показываем все кнопки
        buttons.append([
            InlineKeyboardButton(
                text=i18n_get_text(language, "instruction._download_ios", "instruction_download_ios"),
                url="https://apps.apple.com/ua/app/v2raytun/id6476628951"
            ),
            InlineKeyboardButton(
                text=i18n_get_text(language, "instruction._download_android", "instruction_download_android"),
                url="https://play.google.com/store/apps/details?id=com.v2raytun.android"
            ),
        ])
        buttons.append([
            InlineKeyboardButton(
                text=i18n_get_text(language, "instruction._download_desktop", "instruction_download_desktop"),
                url="https://v2raytun.com"
            ),
        ])
    
    # Всегда показываем кнопку копирования ключа (one-tap copy)
    buttons.append([
        InlineKeyboardButton(
            text=i18n_get_text(language, "profile.copy_key", "copy_key"),
            callback_data="copy_vpn_key"
        ),
    ])
    
    # Кнопки навигации
    buttons.append([
        InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="menu_main"
        )
    ])
    buttons.append([
        InlineKeyboardButton(
            text=i18n_get_text(language, "main.support", "support"),
            callback_data="menu_support"
        )
    ])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    return keyboard


def get_admin_dashboard_keyboard(language: str = "ru"):
    """Клавиатура главного экрана админ-дашборда"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.dashboard"), callback_data="admin:dashboard")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.stats"), callback_data="admin:stats")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.analytics"), callback_data="admin:analytics")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.metrics"), callback_data="admin:metrics")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.audit"), callback_data="admin:audit")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.keys"), callback_data="admin:keys")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.user"), callback_data="admin:user")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.credit_balance"), callback_data="admin:credit_balance")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.system"), callback_data="admin:system")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.export"), callback_data="admin:export")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.broadcast"), callback_data="admin:broadcast")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.promo_stats"), callback_data="admin_promo_stats")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.referral_stats"), callback_data="admin:referral_stats")],
    ])
    return keyboard


def get_admin_back_keyboard(language: str = "ru"):
    """Клавиатура с кнопкой 'Назад' для админ-разделов"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:main")],
    ])
    return keyboard


def get_reissue_notification_keyboard(language: str = "ru"):
    """Клавиатура для уведомления о перевыпуске VPN-ключа"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.go_to_instruction"), callback_data="menu_instruction")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.copy_key"), callback_data="copy_vpn_key")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.my_profile"), callback_data="menu_profile")],
    ])
    return keyboard


def get_reissue_notification_text(vpn_key: str, language: str = "ru") -> str:
    """Текст уведомления о перевыпуске VPN-ключа"""
    title = i18n_get_text(language, "main.reissue_notification_title")
    text_body = i18n_get_text(language, "main.reissue_notification_text", vpn_key=vpn_key)
    return f"{title}\n\n{text_body}"


# Re-export from modularized handlers module
from app.handlers.notifications import send_referral_cashback_notification

# Original function moved to app.handlers.notifications


def get_broadcast_test_type_keyboard(language: str = "ru"):
    """Клавиатура выбора типа тестирования"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n_get_text(language, "broadcast._normal"), callback_data="broadcast_test_type:normal")],
        [InlineKeyboardButton(text=i18n_get_text(language, "broadcast._ab_test"), callback_data="broadcast_test_type:ab")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.cancel"), callback_data="admin:broadcast")],
    ])
    return keyboard


def get_broadcast_type_keyboard(language: str = "ru"):
    """Клавиатура выбора типа уведомления"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n_get_text(language, "broadcast._type_info"), callback_data="broadcast_type:info")],
        [InlineKeyboardButton(text=i18n_get_text(language, "broadcast._type_maintenance"), callback_data="broadcast_type:maintenance")],
        [InlineKeyboardButton(text=i18n_get_text(language, "broadcast._type_security"), callback_data="broadcast_type:security")],
        [InlineKeyboardButton(text=i18n_get_text(language, "broadcast._type_promo"), callback_data="broadcast_type:promo")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.cancel"), callback_data="admin:broadcast")],
    ])
    return keyboard


def get_broadcast_segment_keyboard(language: str = "ru"):
    """Клавиатура выбора сегмента получателей"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n_get_text(language, "broadcast._segment_all"), callback_data="broadcast_segment:all_users")],
        [InlineKeyboardButton(text=i18n_get_text(language, "broadcast._segment_active"), callback_data="broadcast_segment:active_subscriptions")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.cancel"), callback_data="admin:broadcast")],
    ])
    return keyboard


def get_broadcast_confirm_keyboard(language: str = "ru"):
    """Клавиатура подтверждения отправки уведомления"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n_get_text(language, "broadcast._confirm_send"), callback_data="broadcast:confirm_send")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.cancel"), callback_data="admin:broadcast")],
    ])
    return keyboard


def get_ab_test_list_keyboard(ab_tests: list, language: str = "ru") -> InlineKeyboardMarkup:
    """Клавиатура списка A/B тестов"""
    buttons = []
    for test in ab_tests[:20]:  # Ограничиваем 20 тестами
        test_id = test["id"]
        title = test["title"][:30] + "..." if len(test["title"]) > 30 else test["title"]
        created_at = test["created_at"]
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
        date_str = created_at.strftime("%d.%m.%Y")
        button_text = f"#{test_id} {title} ({date_str})"
        buttons.append([InlineKeyboardButton(text=button_text, callback_data=f"broadcast:ab_stat:{test_id}")])
    
    buttons.append([InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:broadcast")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_admin_export_keyboard(language: str = "ru"):
    """Клавиатура выбора типа экспорта"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.export_users"), callback_data="admin:export:users")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.export_subscriptions"), callback_data="admin:export:subscriptions")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:main")],
    ])
    return keyboard


def get_admin_user_keyboard(has_active_subscription: bool = False, user_id: int = None, has_discount: bool = False, is_vip: bool = False, language: str = "ru"):
    """Клавиатура для раздела пользователя"""
    buttons = []
    if has_active_subscription:
        callback_data = f"admin:user_reissue:{user_id}" if user_id else "admin:user_reissue"
        buttons.append([InlineKeyboardButton(text=i18n_get_text(language, "admin.reissue_key"), callback_data=callback_data)])
    if user_id:
        buttons.append([InlineKeyboardButton(text=i18n_get_text(language, "admin.subscription_history"), callback_data=f"admin:user_history:{user_id}")])
        # Кнопки выдачи и лишения доступа (всегда доступны)
        buttons.append([
            InlineKeyboardButton(text=i18n_get_text(language, "admin.grant_access"), callback_data=f"admin:grant:{user_id}"),
            InlineKeyboardButton(text=i18n_get_text(language, "admin.revoke_access"), callback_data=f"admin:revoke:user:{user_id}")
        ])
        # Кнопки управления скидками
        if has_discount:
            buttons.append([InlineKeyboardButton(text=i18n_get_text(language, "admin.delete_discount"), callback_data=f"admin:discount_delete:{user_id}")])
        else:
            buttons.append([InlineKeyboardButton(text=i18n_get_text(language, "admin.create_discount"), callback_data=f"admin:discount_create:{user_id}")])
        # Кнопки управления VIP-статусом
        if is_vip:
            buttons.append([InlineKeyboardButton(text=i18n_get_text(language, "admin.revoke_vip"), callback_data=f"admin:vip_revoke:{user_id}")])
        else:
            buttons.append([InlineKeyboardButton(text=i18n_get_text(language, "admin.grant_vip"), callback_data=f"admin:vip_grant:{user_id}")])
        # Кнопка выдачи средств
        buttons.append([InlineKeyboardButton(text=i18n_get_text(language, "admin.credit_balance"), callback_data=f"admin:credit_balance:{user_id}")])
    buttons.append([InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:main")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    return keyboard


def get_admin_payment_keyboard(payment_id: int, language: str = "ru"):
    """Клавиатура для администратора (подтверждение/отклонение платежа)"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=i18n_get_text(language, "admin.confirm", "admin_confirm"),
                callback_data=f"approve_payment:{payment_id}"
            ),
            InlineKeyboardButton(
                text=i18n_get_text(language, "admin.reject", "admin_reject"),
                callback_data=f"reject_payment:{payment_id}"
            ),
        ],
    ])
    return keyboard


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    # SAFE STARTUP GUARD: Проверка готовности БД
    # /start может работать в деградированном режиме (только показ меню),
    # но если БД недоступна, не пытаемся создавать пользователя
    if not database.DB_READY:
        # В STAGE показываем меню без сообщения об ошибке (read-only режим)
        # В PROD показываем сообщение об ошибке
        language = await resolve_user_language(message.from_user.id)
        text = i18n_get_text(language, "main.welcome")
        if config.IS_PROD:
            text += "\n\n" + i18n_get_text(language, "main.service_unavailable")
        keyboard = await get_main_menu_keyboard(language, message.from_user.id)
        await message.answer(text, reply_markup=keyboard)
        return
    """Обработчик команды /start"""
    telegram_id = message.from_user.id
    # Safe username resolution: username or first_name or localized fallback
    user = await database.get_user(telegram_id)
    start_language = await resolve_user_language(telegram_id)
    username = safe_resolve_username(message.from_user, start_language, telegram_id)
    
    # Создаем пользователя если его нет (user already fetched above)
    if not user:
        await database.create_user(telegram_id, username, "ru")
    else:
        # Обновляем username если изменился (safe: username can be None)
        if username is not None:
            await database.update_username(telegram_id, username)
        # Убеждаемся, что у пользователя есть referral_code
        if not user.get("referral_code"):
            # Генерируем код для существующего пользователя
            referral_code = database.generate_referral_code(telegram_id)
            pool = await database.get_pool()
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE users SET referral_code = $1 WHERE telegram_id = $2",
                    referral_code, telegram_id
                )
    
    # 1. REFERRAL REGISTRATION: Process on FIRST interaction
    # This uses the new deterministic referral service
    referral_result = await process_referral_on_first_interaction(message, telegram_id)
    
    # Send notification to referrer if just registered
    if referral_result and referral_result.get("should_notify"):
        try:
            referrer_id = referral_result.get("referrer_id")
            if referrer_id:
                # Get referrer info
                referrer_user = await database.get_user(referrer_id)
                referrer_username = referrer_user.get("username") if referrer_user else None
                referrer_language = await resolve_user_language(referrer_id)
                
                # Get referred user info (safe: username or first_name or fallback)
                referred_username = username  # Already resolved via safe_resolve_username
                # Format display name: add @ prefix if username exists and doesn't have it
                user_fallback_text = i18n_get_text(referrer_language, "common.user")
                if referred_username and not referred_username.startswith("ID:") and referred_username != user_fallback_text:
                    referred_display = f"@{referred_username}" if not referred_username.startswith("@") else referred_username
                else:
                    referred_display = referred_username
                
                first_payment_msg = i18n_get_text(referrer_language, "referral.first_payment_notification")
                title = i18n_get_text(referrer_language, "referral.registered_title")
                user_line = i18n_get_text(referrer_language, "referral.registered_user", user=referred_display)
                date_line = i18n_get_text(referrer_language, "referral.registered_date", date=datetime.now().strftime('%d.%m.%Y %H:%M'))
                notification_text = f"{title}\n\n{user_line}\n{date_line}\n\n{first_payment_msg}"
                
                await message.bot.send_message(
                    chat_id=referrer_id,
                    text=notification_text
                )
                
                logger.info(
                    f"REFERRAL_NOTIFICATION_SENT [type=registration, referrer={referrer_id}, "
                    f"referred={telegram_id}]"
                )
        except Exception as e:
            # Non-critical - log but don't fail
            logger.warning(
                "NOTIFICATION_FAILED",
                extra={
                    "type": "referral_registration",
                    "referrer": referrer_id,
                    "referred": telegram_id,
                    "error": str(e)
                }
            )
    
    # Phase 4: ALWAYS show language selection first (pre-language-binding screen)
    text = i18n_get_text("ru", "lang.select_title")
    await message.answer(text, reply_markup=get_language_keyboard("ru"))


async def format_promo_stats_text(stats: list) -> str:
    """Форматировать статистику промокодов в текст"""
    if not stats:
        return "Промокоды не найдены."
    
    text = "📊 Статистика промокодов\n\n"
    
    for promo in stats:
        code = promo["code"]
        discount_percent = promo["discount_percent"]
        max_uses = promo["max_uses"]
        used_count = promo["used_count"]
        is_active = promo["is_active"]
        
        text += f"{code}\n"
        text += f"— Скидка: {discount_percent}%\n"
        
        if max_uses is not None:
            text += f"— Использовано: {used_count} / {max_uses}\n"
            if is_active:
                text += "— Статус: активен\n"
            else:
                text += "— Статус: исчерпан\n"
        else:
            text += f"— Использовано: {used_count}\n"
            text += "— Статус: без ограничений\n"
        
        text += "\n"
    
    return text


@router.message(Command("promo_stats"))
async def cmd_promo_stats(message: Message):
    """Команда для просмотра статистики промокодов (только для администратора)"""
    # STEP 4 — PART A: INPUT TRUST BOUNDARIES
    # Validate telegram_id
    telegram_id = message.from_user.id
    is_valid, error = validate_telegram_id(telegram_id)
    if not is_valid:
        log_security_warning(
            event="Invalid telegram_id in promo_stats command",
            telegram_id=telegram_id,
            correlation_id=str(message.message_id) if hasattr(message, 'message_id') else None,
            details={"error": error}
        )
        language = await resolve_user_language(message.from_user.id)
        await message.answer(i18n_get_text(language, "errors.try_later"))
        return
    
    # STEP 4 — PART B: AUTHORIZATION GUARDS
    # Explicit admin authorization check - fail closed
    is_authorized, auth_error = require_admin(telegram_id)
    if not is_authorized:
        language = await resolve_user_language(telegram_id)
        await message.answer(i18n_get_text(language, "errors.access_denied", "error_access_denied"))
        return
    
    # STEP 4 — PART F: SECURITY LOGGING POLICY
    # Log admin action
    log_audit_event(
        event="admin_promo_stats_viewed",
        telegram_id=telegram_id,
        correlation_id=str(message.message_id) if hasattr(message, 'message_id') else None
    )
    
    try:
        # Получаем статистику промокодов
        stats = await database.get_promo_stats()
        
        # Формируем текст ответа
        text = await format_promo_stats_text(stats)
        await message.answer(text)
    except Exception as e:
        logger.error(f"Error getting promo stats: {e}")
        language = await resolve_user_language(message.from_user.id)
        await message.answer(i18n_get_text(language, "errors.promo_stats"))


@router.message(Command("profile"))
async def cmd_profile(message: Message):
    """Обработчик команды /profile"""
    # SAFE STARTUP GUARD: Проверка готовности БД
    if not await ensure_db_ready_message(message):
        return
    
    telegram_id = message.from_user.id
    user = await database.get_user(telegram_id)
    
    if not user:
        language = await resolve_user_language(telegram_id)
        await message.answer(i18n_get_text(language, "errors.start_command", "error_start_command"))
        return
    
    language = await resolve_user_language(telegram_id)
    await show_profile(message, language)


@router.message(Command("buy"))
async def cmd_buy(message: Message, state: FSMContext, bot: Bot):
    """Обработчик команды /buy — открывает экран покупки"""
    if not await ensure_db_ready_message(message):
        return
    await _open_buy_screen(message, bot, state)


@router.message(Command("referral"))
async def cmd_referral(message: Message, bot: Bot):
    """Обработчик команды /referral — открывает экран программы лояльности"""
    if not await ensure_db_ready_message(message):
        return
    await _open_referral_screen(message, bot)


@router.message(Command("info"))
async def cmd_info(message: Message, bot: Bot):
    """Обработчик команды /info — открывает экран «О сервисе»"""
    if not await ensure_db_ready_message(message):
        return
    await _open_about_screen(message, bot)


@router.message(Command("help"))
async def cmd_help(message: Message, bot: Bot):
    """Обработчик команды /help — открывает экран поддержки"""
    if not await ensure_db_ready_message(message):
        return
    await _open_support_screen(message, bot)


@router.message(Command("instruction"))
async def cmd_instruction(message: Message, bot: Bot):
    """Обработчик команды /instruction — открывает экран инструкции"""
    if not await ensure_db_ready_message(message):
        return
    await _open_instruction_screen(message, bot)


async def check_subscription_expiry(telegram_id: int) -> bool:
    """
    Дополнительная защита: проверка и мгновенное отключение истёкшей подписки
    
    Вызывается в начале критичных handlers для дополнительной безопасности.
    Возвращает True если подписка была отключена, False если активна или отсутствует.
    """
    return await check_subscription_expiry_service(telegram_id)


async def show_profile(message_or_query, language: str):
    """Показать профиль пользователя (обновленная версия с балансом)"""
    telegram_id = None
    send_func = None
    
    try:
        if isinstance(message_or_query, Message):
            telegram_id = message_or_query.from_user.id
            send_func = message_or_query.answer
        else:
            telegram_id = message_or_query.from_user.id
            send_func = message_or_query.message.edit_text
    except AttributeError as e:
        logger.error(f"Invalid message_or_query type in show_profile: {type(message_or_query)}, error: {e}")
        raise
    
    # REAL-TIME EXPIRATION CHECK: Проверяем и отключаем истекшие подписки сразу
    if telegram_id:
        await check_subscription_expiry_service(telegram_id)
    
    try:
        # Дополнительная защита: проверка истечения подписки
        await check_subscription_expiry(telegram_id)
        
        # Получаем данные пользователя
        user = await database.get_user(telegram_id)
        if not user:
            logger.warning(f"User not found: {telegram_id}")
            error_text = i18n_get_text(language, "errors.profile_load")
            await send_func(error_text)
            return
        
        username = user.get("username") if user else None
        if not username:
            username = f"ID: {telegram_id}"
        
        # Получаем баланс
        balance_rubles = await database.get_user_balance(telegram_id)
        
        # Получаем информацию о подписке (активной или истекшей)
        subscription = await database.get_subscription_any(telegram_id)
        
        # Формируем текст профиля
        text = i18n_get_text(language, "profile.welcome_full", username=username, balance=round(balance_rubles, 2))
        
        # Определяем статус подписки используя subscription service
        subscription_status = get_subscription_status(subscription)
        has_active_subscription = subscription_status.is_active
        has_any_subscription = subscription_status.has_subscription
        activation_status = subscription_status.activation_status
        expires_at = subscription_status.expires_at
        
        # PART E.8: Profile logic - active + pending → show "Activation in progress"
        # PART E.8: NEVER show "no subscription" if activation_status=pending
        # PART E.9: Clear explanation, no contradictions
        if activation_status == "pending" or (has_any_subscription and activation_status == "pending"):
            # PART E.8: Show "Activation in progress" for pending activations
            expires_str = expires_at.strftime("%d.%m.%Y") if expires_at else "N/A"
            text += "\n" + i18n_get_text(language, "profile.subscription_pending", date=expires_str)
        elif has_active_subscription:
            # Подписка активна
            expires_str = expires_at.strftime("%d.%m.%Y") if expires_at else "N/A"
            text += "\n" + i18n_get_text(language, "profile.subscription_active", date=expires_str)
        else:
            # Подписка неактивна (истекла или отсутствует)
            text += "\n" + i18n_get_text(language, "profile.subscription_inactive")
        
        # Получаем статус автопродления и добавляем информацию
        auto_renew = False
        if subscription:
            auto_renew = subscription.get("auto_renew", False)
        
        # Добавляем информацию об автопродлении (только для активных подписок)
        if subscription_status.is_active:
            if auto_renew:
                # Автопродление включено - next_billing_date = expires_at
                if subscription_status.expires_at:
                    next_billing_str = subscription_status.expires_at.strftime("%d.%m.%Y")
                else:
                    next_billing_str = "N/A"
                text += "\n" + i18n_get_text(language, "profile.auto_renew_enabled", next_billing_date=next_billing_str)
            else:
                # Автопродление выключено
                text += "\n" + i18n_get_text(language, "profile.auto_renew_disabled")
        
        # Добавляем подсказку о продлении (для активных и истекших подписок - по требованиям)
        if has_any_subscription:
            text += "\n\n" + i18n_get_text(language, "profile.renewal_hint")
        
        # Добавляем подсказку о покупке, если подписки нет
        if not has_any_subscription:
            text += "\n\n" + i18n_get_text(language, "profile.buy_hint")
        
        # Показываем кнопку "Продлить доступ" если есть подписка (активная или истекшая) - по требованиям
        keyboard = get_profile_keyboard(language, has_any_subscription, auto_renew)
        
        # Отправляем сообщение
        await send_func(text, reply_markup=keyboard)
        
    except Exception as e:
        logger.exception(f"Error in show_profile for user {telegram_id}: {e}")
        # Пытаемся отправить сообщение об ошибке с безопасной обработкой
        try:
            error_text = i18n_get_text(language, "errors.profile_load")
            
            if isinstance(message_or_query, CallbackQuery):
                await message_or_query.message.answer(error_text)
            elif isinstance(message_or_query, Message):
                await message_or_query.answer(error_text)
        except Exception as e2:
            logger.exception(f"Error sending error message to user {telegram_id}: {e2}")
            # Последняя попытка - отправить простой текст без локализации
            try:
                language = await resolve_user_language(telegram_id)
                error_text = i18n_get_text(language, "errors.profile_load")
                if isinstance(message_or_query, CallbackQuery):
                    await message_or_query.message.answer(error_text)
                elif isinstance(message_or_query, Message):
                    await message_or_query.answer(error_text)
            except Exception as e3:
                logger.exception(f"Critical: Failed to send error message to user {telegram_id}: {e3}")


@router.callback_query(F.data.startswith("toggle_auto_renew:"))
async def callback_toggle_auto_renew(callback: CallbackQuery):
    """Включить/выключить автопродление"""
    # SAFE STARTUP GUARD: Проверка готовности БД
    if not await ensure_db_ready_callback(callback):
        return
    
    telegram_id = callback.from_user.id
    action = callback.data.split(":")[1]
    
    pool = await database.get_pool()
    async with pool.acquire() as conn:
        auto_renew = (action == "on")
        await conn.execute(
            "UPDATE subscriptions SET auto_renew = $1 WHERE telegram_id = $2",
            auto_renew, telegram_id
        )
    
    language = await resolve_user_language(telegram_id)
    
    if auto_renew:
        text = i18n_get_text(language, "subscription.auto_renew_enabled_toast")
    else:
        text = i18n_get_text(language, "subscription.auto_renew_disabled_toast")
    
    await callback.answer(text, show_alert=True)
    
    # Обновляем экран профиля
    await show_profile(callback, language)


@router.callback_query(F.data == "change_language")
async def callback_change_language(callback: CallbackQuery):
    """Изменить язык"""
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    
    # Экран выбора языка (канонический вид)
    text = i18n_get_text(language, "lang.select")
    await safe_edit_text(
        callback.message,
        text,
        reply_markup=get_language_keyboard(language)
    )
    await callback.answer()


@router.message(Command("language"))
async def cmd_language(message: Message, bot: Bot):
    """Обработчик команды /language — открывает экран выбора языка"""
    if not await ensure_db_ready_message(message):
        return
    
    telegram_id = message.from_user.id
    language = await resolve_user_language(telegram_id)
    
    text = i18n_get_text(language, "lang.select")
    await bot.send_message(
        message.chat.id,
        text,
        reply_markup=get_language_keyboard(language)
    )


@router.callback_query(F.data.startswith("lang_"))
async def callback_language(callback: CallbackQuery):
    """Обработчик выбора языка"""
    # SAFE STARTUP GUARD: Проверка готовности БД
    if not await ensure_db_ready_callback(callback):
        return
    
    language = callback.data.split("_")[1]
    telegram_id = callback.from_user.id
    
    await database.update_user_language(telegram_id, language)
    
    # Подтверждение смены языка на выбранном языке
    text = i18n_get_text(language, "main.welcome")
    text = await format_text_with_incident(text, language)
    keyboard = await get_main_menu_keyboard(language, telegram_id)
    await safe_edit_text(callback.message, text, reply_markup=keyboard)
    await callback.answer(
        i18n_get_text(language, "lang.changed_toast"),
        show_alert=False
    )


@router.callback_query(F.data == "menu_main")
async def callback_main_menu(callback: CallbackQuery):
    """Главное меню. Delete + answer to support navigation from photo message (loyalty screen)."""
    # SAFE STARTUP GUARD: Главное меню может работать в деградированном режиме
    # В STAGE разрешаем read-only операции (навигация, меню)
    # В PROD блокируем если БД не готова
    if not await ensure_db_ready_callback(callback, allow_readonly_in_stage=True):
        return
    
    try:
        await callback.message.delete()
    except Exception:
        pass
    
    telegram_id = callback.from_user.id
    language = await resolve_user_language(callback.from_user.id)
    
    text = i18n_get_text(language, "main.welcome")
    text = await format_text_with_incident(text, language)
    keyboard = await get_main_menu_keyboard(language, callback.from_user.id)
    await callback.bot.send_message(callback.message.chat.id, text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data == "activate_trial")
async def callback_activate_trial(callback: CallbackQuery, state: FSMContext):
    """Активация пробного периода на 3 дня"""
    # READ-ONLY system state awareness (informational only, does not affect flow)
    try:
        from datetime import datetime
        now = datetime.utcnow()
        db_ready = database.DB_READY
        import config
        
        # STEP 1.1 - RUNTIME GUARDRAILS: SystemState is READ-ONLY snapshot
        # Handlers NEVER block user actions based on SystemState
        # Handlers may only LOG when system is DEGRADED or UNAVAILABLE
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
        
        # Payments component (always healthy)
        payments_component = healthy_component(last_checked_at=now)
        
        system_state = SystemState(
            database=db_component,
            vpn_api=vpn_component,
            payments=payments_component,
        )
        
        # STEP 1.1 - RUNTIME GUARDRAILS: Handlers log degradation but do NOT branch logic
        # PART D.5: Handlers log DEGRADED for VPN-related actions
        # PART D.5: NEVER block payments or DB flows
        if system_state.is_degraded:
            logger.info(
                f"[DEGRADED] system_state detected during callback_activate_trial "
                f"(user={callback.from_user.id}, optional components degraded)"
            )
            _degradation_notice = True
        else:
            _degradation_notice = False
    except Exception:
        # Ignore system state errors - must not affect activation flow
        _degradation_notice = False
    
    # SAFE STARTUP GUARD: Проверка готовности БД
    if not await ensure_db_ready_callback(callback):
        return
    
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    
    # STEP 6 — F3: RATE LIMITING (HUMAN & BOT SAFETY)
    # Rate limit trial activation (once per hour)
    is_allowed, rate_limit_message = check_rate_limit(telegram_id, "trial_activate")
    if not is_allowed:
        await callback.answer(rate_limit_message or i18n_get_text(language, "common.rate_limit_message"), show_alert=True)
        return
    
    # КРИТИЧНО: Проверяем eligibility перед активацией
    is_eligible = await database.is_eligible_for_trial(telegram_id)
    if not is_eligible:
        error_text = i18n_get_text(language, "main.trial_not_available")
        await callback.answer(error_text, show_alert=True)
        logger.warning(f"Trial activation attempted by ineligible user: {telegram_id}")
        return
    
    await callback.answer()
    
    try:
        # КРИТИЧНО: Создаём подписку на 3 дня с source='trial'
        duration = timedelta(days=3)
        now = datetime.now()
        trial_expires_at = now + duration
        
        # ВАЖНО: Сначала помечаем trial как использованный (idempotent)
        # Это предотвращает повторную активацию даже если grant_access упадёт
        success = await database.mark_trial_used(telegram_id, trial_expires_at)
        if not success:
            raise Exception("Failed to mark trial as used")
        
        result = await database.grant_access(
            telegram_id=telegram_id,
            duration=duration,
            source="trial",
            admin_telegram_id=None
        )
        
        uuid = result.get("uuid")
        vpn_key = result.get("vless_url")
        subscription_end = result.get("subscription_end")
        
        if not uuid or not vpn_key:
            raise Exception("Failed to create VPN access for trial")
        
        # 2. REFERRAL LIFECYCLE: Activate referral (REGISTERED → ACTIVATED)
        # Trial activation marks referral as active (no cashback for trial)
        try:
            activation_result = await activate_referral(telegram_id, activation_type="trial")
            if activation_result.get("success") and activation_result.get("was_activated"):
                logger.info(
                    f"REFERRAL_ACTIVATED [referrer={activation_result.get('referrer_id')}, "
                    f"referred={telegram_id}, type=trial, state=ACTIVATED]"
                )
                
                # Send notification to referrer about trial activation
                referrer_id = activation_result.get("referrer_id")
                if referrer_id:
                    try:
                        referrer_user_for_notif = await database.get_user(referrer_id)
                        referrer_language_notif = await resolve_user_language(referrer_id)
                        # Get referred user info (user who activated trial)
                        referred_user = await database.get_user(telegram_id)
                        referred_username = safe_resolve_username_from_db(referred_user, referrer_language_notif, telegram_id)
                        
                        # Format display name: add @ prefix if username exists and doesn't have it
                        user_fallback_text = i18n_get_text(referrer_language_notif, "common.user")
                        if referred_username and not referred_username.startswith("ID:") and referred_username != user_fallback_text:
                            referred_display = f"@{referred_username}" if not referred_username.startswith("@") else referred_username
                        else:
                            referred_display = referred_username
                        
                        first_payment_msg_notif = i18n_get_text(referrer_language_notif, "referral.first_payment_notification")
                        title_trial = i18n_get_text(referrer_language_notif, "referral.trial_activated_title")
                        user_line_trial = i18n_get_text(referrer_language_notif, "referral.trial_activated_user", user=referred_display)
                        trial_period_line = i18n_get_text(referrer_language_notif, "referral.trial_period")
                        notification_text = f"{title_trial}\n\n{user_line_trial}\n{trial_period_line}\n\n{first_payment_msg_notif}"
                        
                        await callback.bot.send_message(
                            chat_id=referrer_id,
                            text=notification_text
                        )
                        
                        logger.info(
                            f"REFERRAL_NOTIFICATION_SENT [type=trial_activation, referrer={referrer_id}, "
                            f"referred={telegram_id}, referred_display={referred_display}]"
                        )
                    except Exception as e:
                        logger.warning(
                            "NOTIFICATION_FAILED",
                            extra={
                                "type": "trial_activation",
                                "referrer": referrer_id,
                                "referred": telegram_id,
                                "error": str(e)
                            }
                        )
        except Exception as e:
            # Non-critical - log but don't fail trial activation
            logger.warning(f"Failed to activate referral for trial: user={telegram_id}, error={e}")
        
        # Логируем активацию trial
        logger.info(
            f"trial_activated: user={telegram_id}, trial_used_at={now.isoformat()}, "
            f"trial_expires_at={trial_expires_at.isoformat()}, subscription_expires_at={subscription_end.isoformat()}, "
            f"uuid={uuid[:8]}..."
        )
        
        # Отправляем сообщение об активации
        success_text = i18n_get_text(
            language, "main.trial_activated_text",
            vpn_key=vpn_key,
            expires_date=subscription_end.strftime("%d.%m.%Y %H:%M")
        )
        
        # B3.1 - SOFT DEGRADATION: Add soft UX notice if degraded (only where messages are sent)
        try:
            if _degradation_notice:
                success_text += "\n\n⏳ Возможны небольшие задержки"
        except NameError:
            pass  # _degradation_notice not set - ignore
        
        await callback.message.answer(success_text, parse_mode="HTML")
        
        # Отправляем VPN-ключ отдельным сообщением
        try:
            await callback.message.answer(f"<code>{vpn_key}</code>", parse_mode="HTML")
        except Exception as e:
            logger.warning(f"Failed to send VPN key with HTML tags: {e}. Sending as plain text.")
            await callback.message.answer(f"🔑 {vpn_key}")
        
        # Обновляем главное меню (кнопка trial должна исчезнуть)
        text = i18n_get_text(language, "main.welcome")
        text = await format_text_with_incident(text, language)
        keyboard = await get_main_menu_keyboard(language, telegram_id)
        await safe_edit_text(callback.message, text, reply_markup=keyboard)
        
    except Exception as e:
        logger.exception(f"Error activating trial for user {telegram_id}: {e}")
        error_text = i18n_get_text(language, "main.trial_activation_error")
        await callback.message.answer(error_text)


@router.callback_query(F.data == "menu_profile", StateFilter(default_state))
@router.callback_query(F.data == "menu_profile")
async def callback_profile(callback: CallbackQuery, state: FSMContext):
    """Мой профиль - работает независимо от FSM состояния"""
    # SAFE STARTUP GUARD: Проверка готовности БД
    if not await ensure_db_ready_callback(callback):
        return
    
    # REAL-TIME EXPIRATION CHECK: Проверяем и отключаем истекшие подписки сразу
    await database.check_and_disable_expired_subscription(callback.from_user.id)
    telegram_id = callback.from_user.id
    
    # Немедленная обратная связь пользователю
    await callback.answer()
    
    # Очищаем FSM состояние, если пользователь был в каком-то процессе
    try:
        current_state = await state.get_state()
        if current_state is not None:
            await state.clear()
            logger.debug(f"Cleared FSM state for user {telegram_id}, was: {current_state}")
    except Exception as e:
        logger.debug(f"FSM state clear failed (may be already clear): {e}")
    
    try:
        logger.info(f"Opening profile for user {telegram_id}")
        
        language = await resolve_user_language(telegram_id)
        
        await show_profile(callback, language)
        
        logger.info(f"Profile opened successfully for user {telegram_id}")
    except Exception as e:
        logger.exception(f"Error opening profile for user {telegram_id}: {e}")
        # Пытаемся отправить сообщение об ошибке
        try:
            user = await database.get_user(telegram_id)
            language = await resolve_user_language(callback.from_user.id)
            try:
                error_text = i18n_get_text(language, "errors.profile_load", "error_profile_load")
            except KeyError:
                logger.error(f"Missing localization key 'error_profile_load' for language '{language}'")
                error_text = "Ошибка загрузки профиля. Попробуйте позже."
            await callback.message.answer(error_text)
        except Exception as e2:
            logger.exception(f"Error sending error message to user {telegram_id}: {e2}")


@router.callback_query(F.data == "menu_vip_access")
async def callback_vip_access(callback: CallbackQuery):
    """Обработчик кнопки 'VIP-доступ'"""
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    
    # Проверяем VIP-статус
    is_vip = await database.is_vip_user(telegram_id)
    
    # Получаем текст VIP-доступа
    text = i18n_get_text(language, "main.vip_access_text", "vip_access_text")
    
    # Добавляем информацию о статусе, если пользователь VIP
    if is_vip:
        text += "\n\n" + i18n_get_text(language, "main.vip_status_active")
    
    # Клавиатура с кнопками
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n_get_text(language, "main.contact_manager_button"),
            url="https://t.me/asc_support"
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="menu_profile"
        )]
    ])
    
    await safe_edit_text(callback.message, text, reply_markup=keyboard)
    await callback.answer()


# callback_renew_same_period - УДАЛЕН
# Этот handler был отключен, так как использует устаревшую модель (months)
# и не соответствует новой двухшаговой логике покупки.
# Для продления подписки используется стандартный flow:
# /buy -> выбор тарифа -> выбор периода -> выбор способа оплаты
    
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    
    # Дополнительная защита: проверка истечения подписки
    await check_subscription_expiry(telegram_id)
    
    # Проверяем наличие АКТИВНОЙ подписки используя subscription service
    # Продление работает для ЛЮБОЙ активной подписки независимо от source (payment/admin/test)
    subscription = await database.get_subscription(telegram_id)
    if not subscription:
        try:
            error_text = i18n_get_text(language, "errors.no_active_subscription")
        except (KeyError, TypeError):
            error_text = "Активная подписка не найдена."
        await callback.message.answer(error_text)
        return
    
    # Проверяем что подписка действительно активна используя service
    if not is_subscription_active(subscription):
        try:
            error_text = i18n_get_text(language, "errors.no_active_subscription")
        except (KeyError, TypeError):
            error_text = "Активная подписка не найдена."
        await callback.message.answer(error_text)
        return
    
    # Определяем тариф для продления
    # Сначала пытаемся получить из последнего утвержденного платежа (для paid-подписок)
    tariff_key = None
    last_payment = await database.get_last_approved_payment(telegram_id)
    if last_payment:
        tariff_key = last_payment.get("tariff")
    
    # Если тариф не найден в платеже (admin/test подписки), используем дефолтный тариф "basic" (30 дней)
    if not tariff_key or tariff_key not in config.TARIFFS:
        tariff_key = "basic"
        logger.info(f"Using default tariff 'basic' (30 days) for renewal: user={telegram_id}, subscription_source=admin_or_test")
    
    # Получаем цену тарифа - используем период 30 дней как дефолт
    if tariff_key not in config.TARIFFS:
        error_msg = f"Invalid tariff_key '{tariff_key}' for user {telegram_id}. Valid tariffs: {list(config.TARIFFS.keys())}"
        logger.error(error_msg)
        raise ValueError(error_msg)
    
    # Используем период 30 дней как дефолт для admin/test подписок
    if 30 not in config.TARIFFS[tariff_key]:
        error_msg = f"Period 30 days not found in tariff '{tariff_key}' for user {telegram_id}"
        logger.error(error_msg)
        raise ValueError(error_msg)
    
    tariff_data = config.TARIFFS[tariff_key][30]  # Используем период 30 дней
    base_price = tariff_data["price"]
    
    # Применяем скидки (VIP, персональная) - та же логика, что при покупке
    base_price_kopecks = base_price * 100
    
    is_vip = await database.is_vip_user(telegram_id)
    if is_vip:
        discounted_price_kopecks = int(base_price * 0.70) * 100  # 30% скидка
        amount_kopecks = discounted_price_kopecks
    else:
        personal_discount = await database.get_user_discount(telegram_id)
        if personal_discount:
            discount_percent = personal_discount["discount_percent"]
            discounted_price_kopecks = int(base_price * (1 - discount_percent / 100)) * 100
            amount_kopecks = discounted_price_kopecks
        else:
            amount_kopecks = base_price_kopecks
    
    # КРИТИЧНО: Валидация минимальной суммы платежа (64 RUB = 6400 kopecks)
    MIN_PAYMENT_AMOUNT_KOPECKS = 6400
    if amount_kopecks < MIN_PAYMENT_AMOUNT_KOPECKS:
        error_text = (
            f"Сумма после скидки ниже минимальной для оплаты картой (64 ₽).\n"
            f"Пожалуйста, выберите другой тариф."
        )
        logger.warning(
            f"payment_blocked_min_amount: user={telegram_id}, renewal=True, "
            f"tariff={tariff_key}, final_price_kopecks={amount_kopecks}, "
            f"min_required={MIN_PAYMENT_AMOUNT_KOPECKS}"
        )
        await callback.answer(error_text, show_alert=True)
        return
    
    amount_rubles = amount_kopecks / 100.0
    
    # Формируем payload (формат: renew:user_id:tariff:timestamp для уникальности)
    payload = f"renew:{telegram_id}:{tariff_key}:{int(time.time())}"
    
    # Формируем описание (используем period_days вместо months)
    period_days = 30  # Дефолтный период для продления
    months = period_days // 30
    if months == 1:
        period_text = "1 месяц"
    elif months in [2, 3, 4]:
        period_text = f"{months} месяца"
    else:
        period_text = f"{months} месяцев"
    description = f"Atlas Secure VPN продление подписки на {period_text}"
    
    logger.info(
        f"invoice_created: user={telegram_id}, renewal=True, tariff={tariff_key}, "
        f"base_price_kopecks={base_price_kopecks}, final_price_kopecks={amount_kopecks}, "
        f"amount_rubles={amount_rubles:.2f}"
    )
    
    try:
        # Отправляем invoice (start_parameter НЕ используется, только payload)
        await callback.bot.send_invoice(
            chat_id=telegram_id,
            title="Atlas Secure VPN",
            description=description,
            payload=payload,
            provider_token=config.TG_PROVIDER_TOKEN,
            currency="RUB",
            prices=[LabeledPrice(label=i18n_get_text(language, "buy.renewal_payment_label"), amount=amount_kopecks)]
        )
        logger.info(f"Sent renewal invoice: user={telegram_id}, tariff={tariff_key}, amount={amount_rubles:.2f} RUB")
    except Exception as e:
        logger.exception(f"Error sending renewal invoice for user {telegram_id}: {e}")
        await callback.answer(i18n_get_text(language, "errors.payment_create"), show_alert=True)


@router.callback_query(F.data.startswith("renewal_pay:"))
async def callback_renewal_pay(callback: CallbackQuery):
    """Обработчик кнопки оплаты продления - отправляет invoice через Telegram Payments"""
    tariff_key = callback.data.split(":")[1]
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    
    # Проверяем наличие provider_token
    if not config.TG_PROVIDER_TOKEN:
        language = await resolve_user_language(telegram_id)
        await callback.answer(i18n_get_text(language, "errors.payments_unavailable"), show_alert=True)
        return
    
    # Рассчитываем цену с учетом скидки (та же логика, что в create_payment)
    if tariff_key not in config.TARIFFS:
        error_msg = f"Invalid tariff_key '{tariff_key}' for user {telegram_id}. Valid tariffs: {list(config.TARIFFS.keys())}"
        logger.error(error_msg)
        language = await resolve_user_language(telegram_id)
        await callback.answer(i18n_get_text(language, "errors.tariff"), show_alert=True)
        return
    
    # Для callback_tariff используем период 30 дней как дефолт (если не указан)
    if 30 not in config.TARIFFS[tariff_key]:
        error_msg = f"Period 30 days not found in tariff '{tariff_key}' for user {telegram_id}"
        logger.error(error_msg)
        language = await resolve_user_language(telegram_id)
        await callback.answer(i18n_get_text(language, "errors.tariff"), show_alert=True)
        return
    
    tariff_data = config.TARIFFS[tariff_key][30]  # Используем период 30 дней
    base_price = tariff_data["price"]
    
    # ПРИОРИТЕТ 1: VIP-статус
    is_vip = await database.is_vip_user(telegram_id)
    
    if is_vip:
        amount = int(base_price * 0.70)  # 30% скидка
    else:
        # ПРИОРИТЕТ 2: Персональная скидка
        personal_discount = await database.get_user_discount(telegram_id)
        
        if personal_discount:
            discount_percent = personal_discount["discount_percent"]
            amount = int(base_price * (1 - discount_percent / 100))
        else:
            # Без скидки
            amount = base_price
    
    # Формируем payload (формат: renew:user_id:tariff:timestamp для уникальности)
    import time
    payload = f"renew:{telegram_id}:{tariff_key}:{int(time.time())}"
    
    # Формируем описание тарифа (используем period_days вместо months)
    period_days = 30  # Дефолтный период для продления
    months = period_days // 30
    if months == 1:
        period_text = "1 месяц"
    elif months in [2, 3, 4]:
        period_text = f"{months} месяца"
    else:
        period_text = f"{months} месяцев"
    description = f"Atlas Secure VPN продление подписки на {period_text}"
    
    # Формируем prices (цена в копейках)
    # Get user language for invoice label
    language = await resolve_user_language(telegram_id)
    prices = [LabeledPrice(label=i18n_get_text(language, "payment.label"), amount=amount * 100)]
    
    try:
        # Отправляем invoice
        await callback.bot.send_invoice(
            chat_id=telegram_id,
            title="Atlas Secure VPN",
            description=description,
            payload=payload,
            provider_token=config.TG_PROVIDER_TOKEN,
            currency="RUB",
            prices=prices
        )
        await callback.answer()
    except Exception as e:
        logger.exception(f"Error sending invoice for renewal: {e}")
        language = await resolve_user_language(telegram_id)
        await callback.answer(i18n_get_text(language, "errors.payment_create"), show_alert=True)


@router.callback_query(F.data == "topup_balance")
async def callback_topup_balance(callback: CallbackQuery):
    """Пополнить баланс"""
    # SAFE STARTUP GUARD: Проверка готовности БД
    if not await ensure_db_ready_callback(callback):
        return
    
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    
    # Показываем экран выбора суммы
    text = i18n_get_text(language, "main.topup_balance_select_amount")
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="250 ₽",
            callback_data="topup_amount:250"
        )],
        [InlineKeyboardButton(
            text="750 ₽",
            callback_data="topup_amount:750"
        )],
        [InlineKeyboardButton(
            text="999 ₽",
            callback_data="topup_amount:999"
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "main.topup_custom_amount"),
            callback_data="topup_custom"
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="menu_profile"
        )],
    ])
    
    await safe_edit_text(callback.message, text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("topup_amount:"))
async def callback_topup_amount(callback: CallbackQuery):
    """Обработка выбора суммы пополнения - показываем экран выбора способа оплаты"""
    # SAFE STARTUP GUARD: Проверка готовности БД
    if not await ensure_db_ready_callback(callback):
        return
    
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    
    # Извлекаем сумму из callback_data
    amount_str = callback.data.split(":")[1]
    try:
        amount = int(amount_str)
    except ValueError:
        await callback.answer(i18n_get_text(language, "errors.invalid_amount"), show_alert=True)
        return
    
    if amount <= 0 or amount > 100000:
        await callback.answer(i18n_get_text(language, "errors.invalid_amount"), show_alert=True)
        return
    
    # Показываем экран выбора способа оплаты
    text = i18n_get_text(language, "main.topup_select_payment_method", amount=amount)
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n_get_text(language, "main.pay_with_card"),
            callback_data=f"topup_card:{amount}"
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "main.pay_crypto"),
            callback_data=f"topup_crypto:{amount}"
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="topup_balance"
        )],
    ])
    
    await safe_edit_text(callback.message, text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data == "topup_custom")
async def callback_topup_custom(callback: CallbackQuery, state: FSMContext):
    """Ввод произвольной суммы пополнения баланса"""
    # SAFE STARTUP GUARD: Проверка готовности БД
    if not await ensure_db_ready_callback(callback):
        return
    
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    
    await callback.answer()
    
    # Переводим пользователя в состояние ввода суммы
    await state.set_state(TopUpStates.waiting_for_amount)
    
    # Отправляем сообщение с инструкцией
    try:
        text = i18n_get_text(language, "main.topup_enter_amount", "topup_enter_amount")
    except KeyError:
        logger.error(f"Missing localization key 'topup_enter_amount' for language '{language}'")
        text = "Введите свою сумму от 100 ₽"
    
    await callback.message.answer(text)


@router.message(TopUpStates.waiting_for_amount)
async def process_topup_amount(message: Message, state: FSMContext):
    """Обработка введенной суммы пополнения - показываем экран выбора способа оплаты"""
    # SAFE STARTUP GUARD: Проверка готовности БД
    if not await ensure_db_ready_message(message):
        await state.clear()
        return
    
    telegram_id = message.from_user.id
    language = await resolve_user_language(telegram_id)
    
    # Проверяем, что сообщение содержит число
    try:
        amount = int(message.text.strip())
    except (ValueError, AttributeError):
        try:
            error_text = i18n_get_text(language, "main.topup_amount_invalid", "topup_amount_invalid")
        except KeyError:
            logger.error(f"Missing localization key 'topup_amount_invalid' for language '{language}'")
            error_text = "Пожалуйста, введите число."
        await message.answer(error_text)
        return
    
    # Проверяем минимальную сумму
    if amount < 100:
        try:
            error_text = i18n_get_text(language, "main.topup_amount_too_low", "topup_amount_too_low")
        except KeyError:
            logger.error(f"Missing localization key 'topup_amount_too_low' for language '{language}'")
            error_text = "Минимальная сумма пополнения: 100 ₽. Пожалуйста, введите сумму не менее 100 ₽."
        await message.answer(error_text)
        return
    
    # Проверяем максимальную сумму (технический лимит)
    if amount > 100000:
        try:
            error_text = i18n_get_text(language, "main.topup_amount_too_high", "topup_amount_too_high")
        except KeyError:
            logger.error(f"Missing localization key 'topup_amount_too_high' for language '{language}'")
            error_text = "Максимальная сумма пополнения: 100 000 ₽. Пожалуйста, введите меньшую сумму."
        await message.answer(error_text)
        return
    
    # Очищаем FSM состояние
    await state.clear()
    
    # Показываем экран выбора способа оплаты
    text = i18n_get_text(language, "main.topup_select_payment_method", amount=amount)
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n_get_text(language, "main.pay_with_card"),
            callback_data=f"topup_card:{amount}"
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "main.pay_crypto"),
            callback_data=f"topup_crypto:{amount}"
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="topup_balance"
        )],
    ])
    
    await message.answer(text, reply_markup=keyboard)


@router.callback_query(F.data == "copy_key")
async def callback_copy_key(callback: CallbackQuery):
    """Копировать VPN-ключ - отправляет ключ как отдельное сообщение"""
    # B3.1 - SOFT DEGRADATION: Read-only awareness (informational only, does not affect flow)
    try:
        from datetime import datetime
        now = datetime.utcnow()
        db_ready = database.DB_READY
        import config
        
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
        
        # Payments component (always healthy)
        payments_component = healthy_component(last_checked_at=now)
        
        system_state = SystemState(
            database=db_component,
            vpn_api=vpn_component,
            payments=payments_component,
        )
        
        # PART D.5: Handlers log DEGRADED for VPN-related actions
        # PART D.5: NEVER block payments or DB flows
        if system_state.is_degraded:
            logger.info(
                f"[DEGRADED] system_state detected during callback_copy_key "
                f"(user={callback.from_user.id}, optional components degraded)"
            )
    except Exception:
        # Ignore system state errors - must not affect key copy flow
        pass
    
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    
    # Дополнительная защита: проверка истечения подписки
    await check_subscription_expiry(telegram_id)
    
    # Получаем активную подписку (проверка через subscriptions)
    subscription = await database.get_subscription(telegram_id)
    
    # PART 8: Fix pending activation UX - disable copy key button until active
    if subscription:
        activation_status = subscription.get("activation_status", "active")
        if activation_status == "pending":
            error_text = i18n_get_text(language, "main.error_activation_pending")
            logging.info(f"copy_key: Activation pending for user {telegram_id}")
            await callback.answer(error_text, show_alert=True)
            return
    
    if not subscription or not subscription.get("vpn_key"):
        error_text = i18n_get_text(language, "errors.no_active_subscription")
        logging.warning(f"copy_key: No active subscription or vpn_key for user {telegram_id}")
        await callback.answer(error_text, show_alert=True)
        return
    
    # Получаем VPN-ключ
    vpn_key = subscription["vpn_key"]
    
    # ЗАЩИТА ОТ РЕГРЕССА: Валидируем VLESS ссылку перед отправкой
    import vpn_utils
    if not vpn_utils.validate_vless_link(vpn_key):
        error_msg = (
            f"REGRESSION: VPN key contains forbidden 'flow=' parameter for user {telegram_id}. "
            "Key will NOT be sent to user."
        )
        logging.error(f"copy_key: {error_msg}")
        error_text = i18n_get_text(language, "errors.subscription_activation")
        await callback.answer(error_text, show_alert=True)
        return
    
    # Отправляем VPN-ключ как отдельное сообщение (позволяет одно нажатие для копирования в Telegram)
    await callback.message.answer(
        f"<code>{vpn_key}</code>",
        parse_mode="HTML"
    )
    
    # Показываем toast уведомление о копировании
    success_text = i18n_get_text(language, "profile.vpn_key_copied_toast")
    await callback.answer(success_text, show_alert=False)

@router.callback_query(F.data == "copy_vpn_key")
async def callback_copy_vpn_key(callback: CallbackQuery):
    """Скопировать VPN-ключ - отправляет ключ как отдельное сообщение"""
    # B3.1 - SOFT DEGRADATION: Read-only awareness (informational only, does not affect flow)
    try:
        from datetime import datetime
        now = datetime.utcnow()
        db_ready = database.DB_READY
        import config
        
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
        
        # Payments component (always healthy)
        payments_component = healthy_component(last_checked_at=now)
        
        system_state = SystemState(
            database=db_component,
            vpn_api=vpn_component,
            payments=payments_component,
        )
        
        # PART D.5: Handlers log DEGRADED for VPN-related actions
        # PART D.5: NEVER block payments or DB flows
        if system_state.is_degraded:
            logger.info(
                f"[DEGRADED] system_state detected during callback_copy_vpn_key "
                f"(user={callback.from_user.id}, optional components degraded)"
            )
    except Exception:
        # Ignore system state errors - must not affect key copy flow
        pass
    
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    
    # Дополнительная защита: проверка истечения подписки
    await check_subscription_expiry(telegram_id)
    
    # Получаем VPN-ключ из активной подписки (проверка через subscriptions)
    subscription = await database.get_subscription(telegram_id)
    
    if not subscription or not subscription.get("vpn_key"):
        error_text = i18n_get_text(language, "errors.no_active_subscription")
        logging.warning(f"copy_vpn_key: No active subscription or vpn_key for user {telegram_id}")
        await callback.answer(error_text, show_alert=True)
        return
    
    # Получаем VPN-ключ
    vpn_key = subscription["vpn_key"]
    
    # ЗАЩИТА ОТ РЕГРЕССА: Валидируем VLESS ссылку перед отправкой
    import vpn_utils
    if not vpn_utils.validate_vless_link(vpn_key):
        error_msg = (
            f"REGRESSION: VPN key contains forbidden 'flow=' parameter for user {telegram_id}. "
            "Key will NOT be sent to user."
        )
        logging.error(f"copy_vpn_key: {error_msg}")
        error_text = i18n_get_text(language, "errors.subscription_activation")
        await callback.answer(error_text, show_alert=True)
        return
    
    # Отправляем VPN-ключ как отдельное сообщение (позволяет одно нажатие для копирования в Telegram)
    await callback.message.answer(
        f"<code>{vpn_key}</code>",
        parse_mode="HTML"
    )
    
    # Показываем toast уведомление о копировании
    success_text = i18n_get_text(language, "profile.vpn_key_copied_toast")
    await callback.answer(success_text, show_alert=False)


@router.callback_query(F.data == "go_profile", StateFilter(default_state))
@router.callback_query(F.data == "go_profile")
async def callback_go_profile(callback: CallbackQuery, state: FSMContext):
    """Переход в профиль с экрана выдачи ключа - работает независимо от FSM состояния"""
    telegram_id = callback.from_user.id
    
    # Немедленная обратная связь пользователю
    await callback.answer()
    
    # Очищаем FSM состояние, если пользователь был в каком-то процессе
    try:
        current_state = await state.get_state()
        if current_state is not None:
            await state.clear()
            logger.debug(f"Cleared FSM state for user {telegram_id}, was: {current_state}")
    except Exception as e:
        logger.debug(f"FSM state clear failed (may be already clear): {e}")
    
    try:
        logger.info(f"Opening profile via go_profile for user {telegram_id}")
        
        language = await resolve_user_language(telegram_id)
        
        await show_profile(callback, language)
        
        logger.info(f"Profile opened successfully via go_profile for user {telegram_id}")
    except Exception as e:
        logger.exception(f"Error opening profile via go_profile for user {telegram_id}: {e}")
        # Пытаемся отправить сообщение об ошибке
        try:
            user = await database.get_user(telegram_id)
            language = await resolve_user_language(callback.from_user.id)
            try:
                error_text = i18n_get_text(language, "errors.profile_load", "error_profile_load")
            except KeyError:
                logger.error(f"Missing localization key 'error_profile_load' for language '{language}'")
                error_text = "Ошибка загрузки профиля. Попробуйте позже."
            await callback.message.answer(error_text)
        except Exception as e2:
            logger.exception(f"Error sending error message to user {telegram_id}: {e2}")


@router.callback_query(F.data == "back_to_main")
async def callback_back_to_main(callback: CallbackQuery):
    """Возврат в главное меню с экрана выдачи ключа"""
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    
    text = i18n_get_text(language, "main.welcome")
    text = await format_text_with_incident(text, language)
    keyboard = await get_main_menu_keyboard(language, telegram_id)
    await safe_edit_text(callback.message, text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data == "subscription_history")
async def callback_subscription_history(callback: CallbackQuery):
    """История подписок"""
    await callback.answer()
    
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    
    # Получаем историю подписок
    history = await database.get_subscription_history(telegram_id, limit=5)
    
    if not history:
        text = i18n_get_text(language, "subscription.history_empty", "subscription_history_empty")
        await callback.message.answer(text)
        return
    
    # Формируем текст истории
    text = i18n_get_text(language, "subscription.history", "subscription_history") + "\n\n"
    
    action_type_map = {
        "purchase": i18n_get_text(language, "subscription.history_action_purchase", "subscription_history_action_purchase"),
        "renewal": i18n_get_text(language, "subscription.history_action_renewal", "subscription_history_action_renewal"),
        "reissue": i18n_get_text(language, "subscription.history_action_reissue", "subscription_history_action_reissue"),
        "manual_reissue": i18n_get_text(language, "subscription.history_action_manual_reissue", "subscription_history_action_manual_reissue"),
    }
    
    for record in history:
        start_date = record["start_date"]
        if isinstance(start_date, str):
            start_date = datetime.fromisoformat(start_date)
        start_str = start_date.strftime("%d.%m.%Y")
        
        end_date = record["end_date"]
        if isinstance(end_date, str):
            end_date = datetime.fromisoformat(end_date)
        end_str = end_date.strftime("%d.%m.%Y")
        
        action_type = record["action_type"]
        action_text = action_type_map.get(action_type, action_type)
        
        text += f"• {start_str} — {action_text}\n"
        
        # Для purchase и reissue показываем ключ
        if action_type in ["purchase", "reissue", "manual_reissue"]:
            key_label = i18n_get_text(language, "subscription.history_key_label")
            text += f"  {key_label} {record['vpn_key']}\n"
        
        expires_label = i18n_get_text(language, "subscription.history_expires")
        text += f"  {expires_label} {end_str}\n\n"
    
    await callback.message.answer(text, reply_markup=get_back_keyboard(language))


async def _open_buy_screen(event: Union[Message, CallbackQuery], bot: Bot, state: FSMContext):
    """
    Купить VPN - выбор типа тарифа (Basic/Plus). Reusable for callback and /buy command.
    """
    msg = event.message if isinstance(event, CallbackQuery) else event
    telegram_id = event.from_user.id
    language = await resolve_user_language(telegram_id)
    
    await state.update_data(purchase_id=None, tariff_type=None, period_days=None)
    await database.cancel_pending_purchases(telegram_id, "new_purchase_started")
    await state.set_state(PurchaseState.choose_tariff)
    
    text = (
        f"{i18n_get_text(language, 'buy.tariff_basic')}\n\n"
        f"{i18n_get_text(language, 'buy.tariff_plus')}\n\n"
        f"{i18n_get_text(language, 'buy.tariff_corporate')}"
    )
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n_get_text(language, "buy.select_basic_button"),
            callback_data="tariff:basic"
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "buy.select_plus_button"),
            callback_data="tariff:plus"
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "buy.enter_promo"),
            callback_data="enter_promo"
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "buy.corporate_button"),
            callback_data="corporate_access_request"
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="menu_main"
        )],
    ])
    
    await safe_edit_text(msg, text, reply_markup=keyboard, bot=bot)
    if isinstance(event, CallbackQuery):
        await event.answer()


@router.callback_query(F.data == "menu_buy_vpn")
async def callback_buy_vpn(callback: CallbackQuery, state: FSMContext):
    """Купить VPN - выбор типа тарифа (Basic/Plus). Entry from inline button."""
    if not await ensure_db_ready_callback(callback):
        return
    await _open_buy_screen(callback, callback.bot, state)


@router.callback_query(F.data == "corporate_access_request")
async def callback_corporate_access_request(callback: CallbackQuery, state: FSMContext):
    """
    🧩 CORPORATE ACCESS REQUEST FLOW
    
    Entry point: User taps "Корпоративный доступ" button.
    Shows confirmation screen with consent text.
    """
    # SAFE STARTUP GUARD: Проверка готовности БД
    if not await ensure_db_ready_callback(callback):
        return
    
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    
    # Set FSM state
    await state.set_state(CorporateAccessRequest.waiting_for_confirmation)
    
    # Show confirmation screen with consent text
    consent_text = i18n_get_text(language, "buy.corporate_consent")
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n_get_text(language, "buy.corporate_confirm"),
            callback_data="corporate_access_confirm"
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "buy.corporate_back"),
            callback_data="menu_buy_vpn"
        )],
    ])
    
    await safe_edit_text(callback.message, consent_text, reply_markup=keyboard)
    await callback.answer()
    
    logger.debug(f"FSM: CorporateAccessRequest.waiting_for_confirmation set for user {telegram_id}")


@router.callback_query(F.data == "corporate_access_confirm", StateFilter(CorporateAccessRequest.waiting_for_confirmation))
async def callback_corporate_access_confirm(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """
    🧩 CORPORATE ACCESS REQUEST FLOW
    
    On confirmation: Send admin notification and user confirmation.
    """
    if not await ensure_db_ready_callback(callback):
        return
    
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    user = await database.get_user(telegram_id)

    try:
        # Get user data (safe: username can be None)
        username = callback.from_user.username if callback.from_user else None
        username_display = f"@{username}" if username else i18n_get_text(language, "common.username_not_set")
        
        # Get subscription status
        subscription = await database.get_subscription(telegram_id)
        has_active_subscription = False
        if subscription:
            from app.services.subscriptions.service import get_subscription_status
            subscription_status = get_subscription_status(subscription)
            has_active_subscription = subscription_status.is_active
        
        subscription_status_text = "ДА" if has_active_subscription else "НЕТ"
        
        # Get registration date
        registration_date = "N/A"
        if user and user.get("created_at"):
            if isinstance(user["created_at"], str):
                from datetime import datetime
                registration_date = datetime.fromisoformat(user["created_at"]).strftime("%d.%m.%Y")
            else:
                registration_date = user["created_at"].strftime("%d.%m.%Y")
        
        # Current date
        from datetime import datetime
        request_date = datetime.now().strftime("%d.%m.%Y")
        
        # Send admin notification using unified service
        import admin_notifications
        admin_message = (
            f"📩 Новый запрос на корпоративный доступ\n\n"
            f"ID: {telegram_id}\n"
            f"Username: {username_display}\n"
            f"Дата запроса: {request_date}\n\n"
            f"Активная подписка: {subscription_status_text}\n"
            f"Дата регистрации в боте: {registration_date}"
        )
        
        admin_notified = await admin_notifications.send_admin_notification(
            bot=bot,
            message=admin_message,
            notification_type="corporate_access_request",
            parse_mode=None
        )
        
        # Send user confirmation message
        user_confirmation_text = i18n_get_text(language, "buy.corporate_request_accepted")

        user_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=i18n_get_text(language, "main.profile"),
                callback_data="menu_profile"
            )],
        ])

        await callback.message.answer(user_confirmation_text, reply_markup=user_keyboard)
        
        # Write audit log
        try:
            await database._log_audit_event_atomic_standalone(
                "corporate_access_request",
                telegram_id,
                None,
                f"Corporate access request: username={username_display}, has_active_subscription={has_active_subscription}, admin_notified={admin_notified}, requested_at={request_date}"
            )
        except Exception as e:
            logger.error(f"Failed to write audit log for corporate access request: {e}")
        
        # Clear FSM
        await state.clear()
        logger.debug(f"FSM: CorporateAccessRequest cleared after confirmation for user {telegram_id}")
        
        await callback.answer()
        
    except Exception as e:
        logger.exception(f"Error in callback_corporate_access_confirm: {e}")
        # Still confirm user even if admin notification fails
        try:
            user_confirmation_text = i18n_get_text(language, "buy.corporate_request_accepted")
            await callback.message.answer(user_confirmation_text)
        except Exception:
            pass
        await state.clear()
        await callback.answer(i18n_get_text(language, "buy.corporate_request_accepted").split("\n")[0], show_alert=True)


@router.callback_query(F.data.startswith("tariff:"))
async def callback_tariff_type(callback: CallbackQuery, state: FSMContext):
    """ЭКРАН 1 — Выбор тарифа (Basic/Plus)
    
    КРИТИЧНО:
    - НЕ создает pending_purchase
    - Только сохраняет tariff_type в FSM
    - Переводит в choose_period
    - Показывает экран выбора периода
    """
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    
    # КРИТИЧНО: Проверяем FSM state - должен быть choose_tariff или None (начало покупки)
    current_state = await state.get_state()
    if current_state not in [PurchaseState.choose_tariff, None]:
        error_text = i18n_get_text(language, "errors.session_expired")
        await callback.answer(error_text, show_alert=True)
        logger.warning(f"Invalid FSM state for tariff: user={telegram_id}, state={current_state}, expected=PurchaseState.choose_tariff or None")
        # Сбрасываем состояние и возвращаем к выбору тарифа
        await state.set_state(PurchaseState.choose_tariff)
        return
    
    # Парсим callback_data безопасно (формат: "tariff:basic" или "tariff:plus")
    try:
        parts = callback.data.split(":")
        if len(parts) < 2:
            user = await database.get_user(callback.from_user.id)
            language = await resolve_user_language(callback.from_user.id)
            await callback.answer(i18n_get_text(language, "errors.tariff"), show_alert=True)
            return
        tariff_type = parts[1]  # "basic" или "plus"
    except (IndexError, ValueError) as e:
        logger.error(f"Invalid tariff callback_data: {callback.data}, error={e}")
        user = await database.get_user(callback.from_user.id)
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "errors.tariff"), show_alert=True)
        return
    
    # Валидация тарифа
    if tariff_type not in config.TARIFFS:
        logger.error(f"Invalid tariff_type: {tariff_type}")
        user = await database.get_user(callback.from_user.id)
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "errors.tariff"), show_alert=True)
        return
    
    # КРИТИЧНО: Сохраняем tariff_type в FSM state
    # Промо-сессия НЕ сбрасывается при выборе тарифа - она независима от покупки
    await state.update_data(tariff_type=tariff_type)
    
    # КРИТИЧНО: Получаем промо-сессию (проверяет срок действия автоматически)
    promo_session = await get_promo_session(state)
    promo_code = promo_session.get("promo_code") if promo_session else None
    
    # КРИТИЧНО: НЕ создаем pending_purchase - только показываем кнопки периодов
    # Определяем описание тарифа в зависимости от типа
    if tariff_type == "basic":
        text = i18n_get_text(language, "buy.tariff_basic_desc")
    else:
        text = i18n_get_text(language, "buy.tariff_plus_desc")
    
    buttons = []
    
    # Получаем цены для выбранного тарифа с учетом скидок
    periods = config.TARIFFS[tariff_type]
    
    # КРИТИЧНО: Логируем контекст промо-сессии для диагностики
    if promo_session:
        expires_at = promo_session.get("expires_at", 0)
        expires_in = max(0, int(expires_at - time.time()))
        logger.info(
            f"Price calculation with promo session: user={telegram_id}, tariff={tariff_type}, "
            f"promo_code={promo_code}, discount={promo_session.get('discount_percent')}%, "
            f"expires_in={expires_in}s"
        )
    
    for period_days, period_data in periods.items():
        # КРИТИЧНО: Используем ЕДИНУЮ функцию расчета цены для отображения
        try:
            price_info = await subscription_service.calculate_price(
                telegram_id=telegram_id,
                tariff=tariff_type,
                period_days=period_days,
                promo_code=promo_code
            )
        except (subscription_service.InvalidTariffError, subscription_service.PriceCalculationError) as e:
            logger.error(f"Error calculating price: tariff={tariff_type}, period={period_days}, error={e}")
            continue  # Пропускаем этот период если ошибка расчета
        
        base_price_rubles = price_info["base_price_kopecks"] / 100.0
        final_price_rubles = price_info["final_price_kopecks"] / 100.0
        has_discount = price_info["discount_percent"] > 0
        
        # КРИТИЧНО: Логируем расчет цены для диагностики
        logger.debug(
            f"Price recalculated: tariff={tariff_type}, period={period_days}, "
            f"base={price_info['base_price_kopecks']}, discount={price_info['discount_percent']}%, "
            f"final={price_info['final_price_kopecks']}, promo_code={promo_code or 'none'}"
        )
        
        months = period_days // 30
        
        # Формируем правильное склонение периода
        if months == 1:
            period_text = i18n_get_text(language, "buy.period_1")
        elif months in [2, 3, 4]:
            period_text = i18n_get_text(language, "buy.period_2_4", months=months)
        else:
            period_text = i18n_get_text(language, "buy.period_5_plus", months=months)
        
        # Формируем текст кнопки с зачеркнутой ценой (если есть скидка)
        if has_discount:
            button_text = i18n_get_text(
                language, "buy.button_price_discount",
                base=int(base_price_rubles), final=int(final_price_rubles), period=period_text
            )
        else:
            button_text = i18n_get_text(
                language, "buy.button_price",
                price=int(final_price_rubles), period=period_text
            )
        
        # КРИТИЧНО: callback_data БЕЗ purchase_id - только tariff и period
        buttons.append([InlineKeyboardButton(
            text=button_text,
            callback_data=f"period:{tariff_type}:{period_days}"
        )])
    
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "common.back"),
        callback_data="menu_buy_vpn"
    )])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await safe_edit_text(callback.message, text, reply_markup=keyboard)
    
    # КРИТИЧНО: Переходим в состояние choose_period
    await state.set_state(PurchaseState.choose_period)
    await callback.answer()


@router.callback_query(F.data.startswith("period:"))
async def callback_tariff_period(callback: CallbackQuery, state: FSMContext):
    """ЭКРАН 2 — Выбор периода тарифа
    
    КРИТИЧНО:
    - НЕ создает pending_purchase
    - НЕ создает invoice
    - Только сохраняет period_days и final_price_kopecks в FSM
    - Переводит в choose_payment_method
    - Открывает экран выбора способа оплаты
    """
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    
    # КРИТИЧНО: Парсим callback_data безопасно (формат: "period:basic:30")
    try:
        parts = callback.data.split(":")
        if len(parts) < 3:
            error_text = i18n_get_text(language, "errors.tariff")
            await callback.answer(error_text, show_alert=True)
            logger.error(f"Invalid period callback_data format: {callback.data}")
            return
        
        tariff_type = parts[1]  # "basic" или "plus"
        period_days = int(parts[2])
    except (IndexError, ValueError) as e:
        error_text = i18n_get_text(language, "errors.tariff")
        await callback.answer(error_text, show_alert=True)
        logger.error(f"Invalid period callback_data: {callback.data}, error={e}")
        return
    
    # КРИТИЧНО: Проверяем FSM state - должен быть choose_period
    current_state = await state.get_state()
    if current_state != PurchaseState.choose_period:
        error_text = i18n_get_text(language, "errors.session_expired")
        await callback.answer(error_text, show_alert=True)
        logger.warning(f"Invalid FSM state for period: user={telegram_id}, state={current_state}, expected=PurchaseState.choose_period")
        # Сбрасываем состояние и возвращаем к выбору тарифа
        await state.set_state(PurchaseState.choose_tariff)
        await callback.message.answer(
            i18n_get_text(language, "buy.select_tariff"),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=i18n_get_text(language, "buy.tariff_label_basic"), callback_data="tariff:basic")],
                [InlineKeyboardButton(text=i18n_get_text(language, "buy.tariff_label_plus"), callback_data="tariff:plus")],
                [InlineKeyboardButton(text=i18n_get_text(language, "buy.back_to_tariffs"), callback_data="menu_main")],
            ])
        )
        return
    
    # Валидация тарифа и периода
    if tariff_type not in config.TARIFFS:
        error_text = i18n_get_text(language, "errors.tariff")
        await callback.answer(error_text, show_alert=True)
        logger.error(f"Invalid tariff_type: {tariff_type}")
        return
    
    if period_days not in config.TARIFFS[tariff_type]:
        error_text = i18n_get_text(language, "errors.tariff")
        await callback.answer(error_text, show_alert=True)
        logger.error(f"Invalid period_days: {period_days} for tariff {tariff_type}")
        return
    
    # КРИТИЧНО: Проверяем, что tariff_type в FSM соответствует выбранному
    fsm_data = await state.get_data()
    stored_tariff = fsm_data.get("tariff_type")
    if stored_tariff != tariff_type:
        logger.warning(f"Tariff mismatch: FSM={stored_tariff}, callback={tariff_type}, user={telegram_id}")
        # Обновляем tariff_type в FSM
        await state.update_data(tariff_type=tariff_type)
    
    # КРИТИЧНО: Получаем промо-сессию (проверяет срок действия автоматически)
    promo_session = await get_promo_session(state)
    promo_code = promo_session.get("promo_code") if promo_session else None
    
    # КРИТИЧНО: Логируем контекст промо-сессии для диагностики
    if promo_session:
        expires_at = promo_session.get("expires_at", 0)
        expires_in = max(0, int(expires_at - time.time()))
        discount_percent = promo_session.get("discount_percent", 0)
        logger.info(
            f"Period selection with promo session: user={telegram_id}, tariff={tariff_type}, "
            f"period={period_days}, promo_code={promo_code}, discount={discount_percent}%, "
            f"expires_in={expires_in}s"
        )
    
    # КРИТИЧНО: Используем ЕДИНУЮ функцию расчета цены
    try:
        price_info = await subscription_service.calculate_price(
            telegram_id=telegram_id,
            tariff=tariff_type,
            period_days=period_days,
            promo_code=promo_code
        )
    except (subscription_service.InvalidTariffError, subscription_service.PriceCalculationError) as e:
        error_text = i18n_get_text(language, "errors.tariff")
        await callback.answer(error_text, show_alert=True)
        logger.error(f"Invalid tariff/period in calculate_price: user={telegram_id}, tariff={tariff_type}, period={period_days}, error={e}")
        return
    
    # КРИТИЧНО: Сохраняем данные в FSM state (БЕЗ создания pending_purchase)
    # Промо-сессия НЕ сохраняется здесь - она уже в FSM и независима от покупки
    await state.update_data(
        tariff_type=tariff_type,
        period_days=period_days,
        final_price_kopecks=price_info["final_price_kopecks"],
        discount_percent=price_info["discount_percent"]
    )
    
    logger.info(
        f"Period selected: user={telegram_id}, tariff={tariff_type}, period={period_days}, "
        f"base_price_kopecks={price_info['base_price_kopecks']}, final_price_kopecks={price_info['final_price_kopecks']}, "
        f"discount_percent={price_info['discount_percent']}%, discount_type={price_info['discount_type']}, "
        f"promo_code={promo_code or 'none'}"
    )
    
    # КРИТИЧНО: Переходим к выбору способа оплаты (НЕ создаем pending_purchase и invoice)
    await state.set_state(PurchaseState.choose_payment_method)
    await show_payment_method_selection(callback, tariff_type, period_days, price_info["final_price_kopecks"])


async def show_payment_method_selection(
    callback: CallbackQuery,
    tariff_type: str,
    period_days: int,
    final_price_kopecks: int
):
    """ЭКРАН 3 — Выбор способа оплаты
    
    Показывает кнопки:
    - 💰 Баланс (доступно: XXX ₽)
    - 💳 Банковская карта
    - ⬅️ Назад
    """
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    
    # Получаем баланс пользователя
    balance_rubles = await database.get_user_balance(telegram_id)
    final_price_rubles = final_price_kopecks / 100.0
    
    # Формируем текст
    text = i18n_get_text(language, "payment.select_method", price=final_price_rubles)
    
    # Формируем кнопки
    buttons = []
    
    # Кнопка оплаты балансом (с указанием доступного баланса)
    balance_button_text = i18n_get_text(language, "payment.balance", balance=balance_rubles)
    buttons.append([InlineKeyboardButton(
        text=balance_button_text,
        callback_data="pay:balance"
    )])
    
    # Кнопка оплаты картой
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "payment.card"),
        callback_data="pay:card"
    )])
    
    # Кнопка оплаты криптовалютой (CryptoBot)
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "payment.crypto"),
        callback_data="pay:crypto"
    )])
    
    # Кнопка "Назад"
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "common.back"),
        callback_data="menu_buy_vpn"
    )])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    
    try:
        await safe_edit_text(callback.message, text, reply_markup=keyboard)
        await callback.answer()
    except Exception as e:
        logger.exception(f"Error showing payment method selection: {e}")
        await callback.answer(
            i18n_get_text(language, "errors.payment_processing"),
            show_alert=True
        )


@router.callback_query(F.data == "pay:balance")
async def callback_pay_balance(callback: CallbackQuery, state: FSMContext):
    """ЭКРАН 4A — Оплата балансом
    
    КРИТИЧНО:
    - Работает ТОЛЬКО в состоянии choose_payment_method
    - Списывает баланс и активирует подписку в ОДНОЙ транзакции
    - Rollback при любой ошибке
    - Начисляет реферальный кешбэк
    - Отправляет VPN ключ пользователю
    """
    telegram_id = callback.from_user.id
    
    # STEP 6 — F3: RATE LIMITING (HUMAN & BOT SAFETY)
    # Rate limit payment initiation
    is_allowed, rate_limit_message = check_rate_limit(telegram_id, "payment_init")
    if not is_allowed:
        language = await resolve_user_language(telegram_id)
        await callback.answer(rate_limit_message or i18n_get_text(language, "common.rate_limit_message"), show_alert=True)
        return
    language = await resolve_user_language(telegram_id)
    
    # КРИТИЧНО: Проверяем FSM state - должен быть choose_payment_method
    current_state = await state.get_state()
    if current_state != PurchaseState.choose_payment_method:
        error_text = i18n_get_text(language, "errors.session_expired")
        await callback.answer(error_text, show_alert=True)
        logger.warning(f"Invalid FSM state for pay:balance: user={telegram_id}, state={current_state}, expected=PurchaseState.choose_payment_method")
        await state.set_state(None)
        return
    
    # КРИТИЧНО: Получаем данные из FSM state (единственный источник правды)
    fsm_data = await state.get_data()
    tariff_type = fsm_data.get("tariff_type")
    period_days = fsm_data.get("period_days")
    final_price_kopecks = fsm_data.get("final_price_kopecks")
    
    if not tariff_type or not period_days or not final_price_kopecks:
        error_text = i18n_get_text(language, "errors.session_expired")
        await callback.answer(error_text, show_alert=True)
        logger.error(f"Missing purchase data in FSM: user={telegram_id}, tariff={tariff_type}, period={period_days}, price={final_price_kopecks}")
        await state.set_state(None)
        return
    
    # Получаем баланс пользователя
    balance_rubles = await database.get_user_balance(telegram_id)
    final_price_rubles = final_price_kopecks / 100.0
    
    # Проверяем, хватает ли баланса
    if balance_rubles < final_price_rubles:
        # Баланса не хватает - показываем alert
        shortage = final_price_rubles - balance_rubles
        error_text = i18n_get_text(
            language,
            "errors.insufficient_balance",
            amount=final_price_rubles,
            balance=balance_rubles,
            shortage=shortage
        )
        await callback.answer(error_text, show_alert=True)
        logger.info(f"Insufficient balance for payment: user={telegram_id}, balance={balance_rubles:.2f} RUB, required={final_price_rubles:.2f} RUB")
        return
    
    # КРИТИЧНО: ИДЕМПОТЕНТНОСТЬ - Проверяем FSM state и предотвращаем повторное списание
    # Если уже в processing_payment - значит оплата уже обрабатывается
    current_state = await state.get_state()
    if current_state == PurchaseState.processing_payment:
        logger.warning(
            f"IDEMPOTENCY_CHECK: Duplicate payment attempt blocked: user={telegram_id}, "
            f"current_state={current_state}, reason=already_processing_payment"
        )
        error_text = i18n_get_text(language, "errors.session_expired_processing")
        await callback.answer(error_text, show_alert=True)
        return
    
    # Баланса хватает - списываем и активируем подписку в ОДНОЙ транзакции
    await callback.answer()
    
    # КРИТИЧНО: Переходим в состояние processing_payment ПЕРЕД списанием баланса
    # Это блокирует повторные клики до завершения транзакции
    await state.set_state(PurchaseState.processing_payment)
    
    # КРИТИЧНО: Формируем данные для активации подписки
    months = period_days // 30
    tariff_name = "Basic" if tariff_type == "basic" else "Plus"
    
    try:
        # КРИТИЧНО: Проверяем, была ли активная подписка ДО платежа
        # Это нужно для определения сценария: первая покупка vs продление
        existing_subscription = await database.get_subscription(telegram_id)
        had_active_subscription_before_payment = is_subscription_active(existing_subscription) if existing_subscription else False
        
        # КРИТИЧНО: Все финансовые операции выполняются атомарно в одной транзакции
        # через finalize_balance_purchase
        months = period_days // 30
        tariff_name = "Basic" if tariff_type == "basic" else "Plus"
        transaction_description = f"Оплата подписки {tariff_name} на {months} месяц(ев)"
        
        result = await database.finalize_balance_purchase(
            telegram_id=telegram_id,
            tariff_type=tariff_type,
            period_days=period_days,
            amount_rubles=final_price_rubles,
            description=transaction_description
        )
        
        if not result or not result.get("success"):
            error_text = i18n_get_text(language, "errors.payment_processing")
            await callback.message.answer(error_text)
            await state.set_state(None)
            return
        
        # Извлекаем результаты
        payment_id = result["payment_id"]
        expires_at = result["expires_at"]
        vpn_key = result["vpn_key"]
        is_renewal = result["is_renewal"]
        referral_reward_result = result.get("referral_reward")
        
        # Отправляем уведомление о кешбэке (если начислен)
        if referral_reward_result and referral_reward_result.get("success"):
            try:
                notification_sent = await send_referral_cashback_notification(
                    bot=callback.message.bot,
                    referrer_id=referral_reward_result.get("referrer_id"),
                    referred_id=telegram_id,
                    purchase_amount=final_price_rubles,
                    cashback_amount=referral_reward_result.get("reward_amount"),
                    cashback_percent=referral_reward_result.get("percent"),
                    paid_referrals_count=referral_reward_result.get("paid_referrals_count", 0),
                    referrals_needed=referral_reward_result.get("referrals_needed", 0),
                    action_type="purchase" if not is_renewal else "renewal"
                )
                if notification_sent:
                    logger.info(f"Referral cashback processed for balance payment: user={telegram_id}, amount={final_price_rubles} RUB")
            except Exception as e:
                logger.warning(
                    "NOTIFICATION_FAILED",
                    extra={
                        "type": "balance_payment_referral",
                        "user": telegram_id,
                        "referrer": referral_reward_result.get("referrer_id") if referral_reward_result else None,
                        "error": str(e)
                    }
                )
        
        # ЗАЩИТА ОТ РЕГРЕССА: Валидируем VLESS ссылку перед отправкой
        # Для продлений vpn_key может быть пустым - получаем из подписки
        if is_renewal and not vpn_key:
            subscription = await database.get_subscription(telegram_id)
            if subscription and subscription.get("vpn_key"):
                vpn_key = subscription["vpn_key"]
        
        # Проверяем статус активации подписки
        subscription_check = await database.get_subscription_any(telegram_id)
        is_pending_activation = (
            subscription_check and 
            subscription_check.get("activation_status") == "pending" and
            not is_renewal
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
                    callback_data="menu_support"
                )]
            ])
            
            try:
                await callback.message.answer(
                    pending_text,
                    reply_markup=pending_keyboard,
                    parse_mode="HTML"
                )
                logger.info(
                    f"Pending activation message sent: user={telegram_id}, payment_id={payment_id}, expires_at={expires_str}"
                )
            except Exception as e:
                logger.error(f"Failed to send pending activation message: user={telegram_id}, error={e}")
            
            # Помечаем уведомление как отправленное
            try:
                sent = await database.mark_payment_notification_sent(payment_id)
                if sent:
                    logger.info(
                        f"NOTIFICATION_SENT [type=balance_purchase_pending, payment_id={payment_id}, user={telegram_id}]"
                    )
            except Exception as e:
                logger.error(f"Failed to mark pending activation notification as sent: {e}")
            
            await state.set_state(None)
            await state.clear()
            return
        
        import vpn_utils
        if vpn_key and not vpn_utils.validate_vless_link(vpn_key):
            error_msg = (
                f"REGRESSION: VPN key contains forbidden 'flow=' parameter for user {telegram_id}. "
                "Key will NOT be sent to user."
            )
            logger.error(f"callback_pay_balance: {error_msg}")
            error_text = i18n_get_text(language, "errors.payment_processing")
            await callback.message.answer(error_text)
            await state.set_state(None)
            return
        
        # КРИТИЧНО: Удаляем промо-сессию после успешной оплаты
        await clear_promo_session(state)
        
        # ИДЕМПОТЕНТНОСТЬ: Проверяем, было ли уже отправлено уведомление
        notification_already_sent = await database.is_payment_notification_sent(payment_id)
        
        if notification_already_sent:
            logger.info(
                f"NOTIFICATION_IDEMPOTENT_SKIP [type=balance_purchase, payment_id={payment_id}, user={telegram_id}, "
                f"scenario={'renewal' if is_renewal else 'first_purchase'}]"
            )
            await state.set_state(None)
            await state.clear()
            return
        
        # КРИТИЧНО: Очищаем FSM после успешной активации
        await state.set_state(None)
        await state.clear()
        
        # Формируем сообщение в зависимости от сценария: первая покупка vs продление
        expires_str = expires_at.strftime("%d.%m.%Y")
        
        if is_renewal:
            success_text = i18n_get_text(language, "payment.success_renewal", date=expires_str, vpn_key=vpn_key)
        else:
            success_text = i18n_get_text(language, "payment.success_first", date=expires_str, vpn_key=vpn_key)
        
        # КРИТИЧНО: Отправляем сообщение с обработкой ошибок HTML parsing
        try:
            await callback.message.answer(
                success_text,
                reply_markup=get_vpn_key_keyboard(language),
                parse_mode="HTML"
            )
            logger.info(
                f"Success message sent for balance payment: user={telegram_id}, "
                f"scenario={'renewal' if is_renewal else 'first_purchase'}, "
                f"expires_at={expires_str}"
            )
        except Exception as e:
            # Если HTML parsing упал - отправляем простой текст без HTML
            logger.error(
                f"Failed to send success message with HTML for user {telegram_id}: {e}. "
                f"Falling back to plain text."
            )
            
            # Fallback: отправляем простой текст без HTML
            if is_renewal:
                fallback_text = i18n_get_text(language, "payment.fallback_renewal", date=expires_str)
            else:
                fallback_text = i18n_get_text(language, "payment.fallback_first", date=expires_str)
            
            try:
                await callback.message.answer(
                    fallback_text,
                    reply_markup=get_vpn_key_keyboard(language)
                    # Без parse_mode="HTML" - обычный текст
                )
                logger.info(f"Fallback success message sent (plain text): user={telegram_id}")
            except Exception as fallback_error:
                logger.exception(f"CRITICAL: Failed to send even fallback success message: {fallback_error}")
        
        # Отправляем VPN-ключ отдельным сообщением (позволяет одно нажатие для копирования)
        try:
            await callback.message.answer(
                f"<code>{vpn_key}</code>",
                parse_mode="HTML"
            )
            logger.info(f"VPN key sent separately: user={telegram_id}, key_length={len(vpn_key)}")
        except Exception as e:
            # Если HTML parsing упал - отправляем ключ без тегов
            logger.error(f"Failed to send VPN key with HTML tags: {e}. Sending as plain text.")
            try:
                await callback.message.answer(f"🔑 {vpn_key}")
                logger.info(f"VPN key sent as plain text: user={telegram_id}")
            except Exception as key_error:
                logger.exception(f"CRITICAL: Failed to send VPN key even as plain text: {key_error}")
        
        # ИДЕМПОТЕНТНОСТЬ: Помечаем уведомление как отправленное (после успешной отправки)
        try:
            sent = await database.mark_payment_notification_sent(payment_id)
            if sent:
                logger.info(
                    f"NOTIFICATION_SENT [type=balance_purchase, payment_id={payment_id}, user={telegram_id}, "
                    f"scenario={'renewal' if is_renewal else 'first_purchase'}]"
                )
            else:
                logger.warning(
                    f"NOTIFICATION_FLAG_ALREADY_SET [type=balance_purchase, payment_id={payment_id}, user={telegram_id}]"
                )
        except Exception as e:
            logger.error(
                f"CRITICAL: Failed to mark notification as sent: payment_id={payment_id}, user={telegram_id}, error={e}"
            )
        
        logger.info(
            f"Subscription activated from balance: user={telegram_id}, "
            f"tariff={tariff_type}, period_days={period_days}, "
            f"amount={final_price_rubles:.2f} RUB, "
            f"scenario={'renewal' if is_renewal else 'first_purchase'}"
        )
        
    except Exception as e:
        logger.exception(f"CRITICAL: Unexpected error in callback_pay_balance: {e}")
        error_text = i18n_get_text(language, "errors.payment_processing")
        await callback.answer(error_text, show_alert=True)
        await state.set_state(None)


@router.callback_query(F.data == "pay:card")
async def callback_pay_card(callback: CallbackQuery, state: FSMContext):
    """ЭКРАН 4B — Оплата картой (Telegram Payments / ЮKassa)
    
    КРИТИЧНО:
    - Работает ТОЛЬКО в состоянии choose_payment_method
    - Создает pending_purchase
    - Создает invoice через Telegram Payments
    - Переводит в processing_payment
    """
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    
    # КРИТИЧНО: Проверяем FSM state - должен быть choose_payment_method
    current_state = await state.get_state()
    if current_state != PurchaseState.choose_payment_method:
        error_text = i18n_get_text(language, "errors.session_expired")
        await callback.answer(error_text, show_alert=True)
        logger.warning(f"Invalid FSM state for pay:card: user={telegram_id}, state={current_state}, expected=PurchaseState.choose_payment_method")
        await state.set_state(None)
        return
    
    # КРИТИЧНО: Получаем данные из FSM state (единственный источник правды)
    fsm_data = await state.get_data()
    tariff_type = fsm_data.get("tariff_type")
    period_days = fsm_data.get("period_days")
    final_price_kopecks = fsm_data.get("final_price_kopecks")
    
    # КРИТИЧНО: Получаем промо-сессию для сохранения в pending_purchase
    promo_session = await get_promo_session(state)
    promo_code = promo_session.get("promo_code") if promo_session else None
    
    if not tariff_type or not period_days or not final_price_kopecks:
        error_text = i18n_get_text(language, "errors.session_expired")
        await callback.answer(error_text, show_alert=True)
        logger.error(f"Missing purchase data in FSM: user={telegram_id}, tariff={tariff_type}, period={period_days}, price={final_price_kopecks}")
        await state.set_state(None)
        return
    
    # Проверяем наличие provider_token
    if not config.TG_PROVIDER_TOKEN:
        error_text = i18n_get_text(language, "errors.payments_unavailable")
        await callback.answer(error_text, show_alert=True)
        logger.error(f"TG_PROVIDER_TOKEN not configured")
        return

    # КРИТИЧНО: Валидация минимальной суммы платежа (64 RUB = 6400 kopecks)
    MIN_PAYMENT_AMOUNT_KOPECKS = 6400
    if final_price_kopecks < MIN_PAYMENT_AMOUNT_KOPECKS:
        error_text = i18n_get_text(language, "errors.payment_min_amount")
        await callback.answer(error_text, show_alert=True)
        logger.warning(
            f"payment_blocked_min_amount: user={telegram_id}, tariff={tariff_type}, period_days={period_days}, "
            f"final_price_kopecks={final_price_kopecks}, min_required={MIN_PAYMENT_AMOUNT_KOPECKS}"
        )
        return
    
    try:
        # КРИТИЧНО: Создаем pending_purchase ТОЛЬКО при выборе оплаты картой
        purchase_id = await subscription_service.create_purchase(
            telegram_id=telegram_id,
            tariff=tariff_type,
            period_days=period_days,
            price_kopecks=final_price_kopecks,
            promo_code=promo_code
        )
        
        # КРИТИЧНО: Сохраняем purchase_id в FSM state
        await state.update_data(purchase_id=purchase_id)
        
        logger.info(
            f"Purchase created for card payment: user={telegram_id}, purchase_id={purchase_id}, "
            f"tariff={tariff_type}, period_days={period_days}, "
            f"final_price_kopecks={final_price_kopecks}"
        )
        
        # Формируем payload
        payload = f"purchase:{purchase_id}"
        
        # Формируем описание тарифа
        months = period_days // 30
        tariff_name = "Basic" if tariff_type == "basic" else "Plus"
        description = i18n_get_text(language, "buy.invoice_description", tariff_name=tariff_name, months=months)

        # Формируем prices (цена в копейках из FSM)
        prices = [LabeledPrice(label=i18n_get_text(language, "buy.invoice_label"), amount=final_price_kopecks)]
        
        # КРИТИЧНО: Создаем invoice через Telegram Payments
        await callback.bot.send_invoice(
            chat_id=telegram_id,
            title="Atlas Secure VPN",
            description=description,
            payload=payload,
            provider_token=config.TG_PROVIDER_TOKEN,
            currency="RUB",
            prices=prices
        )
        
        # КРИТИЧНО: Переводим в состояние processing_payment
        await state.set_state(PurchaseState.processing_payment)
        
        logger.info(
            f"invoice_created: user={telegram_id}, purchase_id={purchase_id}, "
            f"tariff={tariff_type}, period_days={period_days}, "
            f"final_price_kopecks={final_price_kopecks}"
        )
        
        await callback.answer()
        
    except Exception as e:
        logger.exception(f"Error creating invoice for card payment: {e}")
        error_text = i18n_get_text(language, "errors.payment_create")
        await callback.answer(error_text, show_alert=True)
        await state.set_state(None)


@router.callback_query(F.data == "pay:crypto")
async def callback_pay_crypto(callback: CallbackQuery, state: FSMContext):
    """Оплата криптовалютой через CryptoBot
    
    КРИТИЧНО:
    - Работает ТОЛЬКО в состоянии choose_payment_method
    - Создает pending_purchase
    - Создает invoice через CryptoBot API
    - Отправляет payment URL пользователю
    - Использует polling для проверки статуса (NO WEBHOOKS)
    """
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    
    # КРИТИЧНО: Проверяем FSM state - должен быть choose_payment_method
    current_state = await state.get_state()
    if current_state != PurchaseState.choose_payment_method:
        error_text = i18n_get_text(language, "errors.session_expired")
        await callback.answer(error_text, show_alert=True)
        logger.warning(f"Invalid FSM state for pay:crypto: user={telegram_id}, state={current_state}, expected=PurchaseState.choose_payment_method")
        await state.set_state(None)
        return
    
    # КРИТИЧНО: Получаем данные из FSM state
    fsm_data = await state.get_data()
    tariff_type = fsm_data.get("tariff_type")
    period_days = fsm_data.get("period_days")
    final_price_kopecks = fsm_data.get("final_price_kopecks")
    
    # Получаем промо-сессию
    promo_session = await get_promo_session(state)
    promo_code = promo_session.get("promo_code") if promo_session else None
    
    if not tariff_type or not period_days or not final_price_kopecks:
        error_text = i18n_get_text(language, "errors.session_expired")
        await callback.answer(error_text, show_alert=True)
        logger.error(f"Missing purchase data in FSM: user={telegram_id}, tariff={tariff_type}, period={period_days}, price={final_price_kopecks}")
        await state.set_state(None)
        return
    
    # Проверяем наличие CryptoBot конфигурации
    try:
        from payments import cryptobot
        if not cryptobot.is_enabled():
            error_text = i18n_get_text(language, "payment.crypto_unavailable")
            await callback.answer(error_text, show_alert=True)
            logger.error(f"CryptoBot not configured")
            return
    except ImportError:
        error_text = i18n_get_text(language, "payment.crypto_unavailable")
        await callback.answer(error_text, show_alert=True)
        logger.error(f"CryptoBot module not found")
        return
    
    try:
        # Создаем pending_purchase
        purchase_id = await subscription_service.create_purchase(
            telegram_id=telegram_id,
            tariff=tariff_type,
            period_days=period_days,
            price_kopecks=final_price_kopecks,
            promo_code=promo_code
        )
        
        # Сохраняем purchase_id в FSM state
        await state.update_data(purchase_id=purchase_id)
        
        logger.info(
            f"Purchase created for crypto payment: user={telegram_id}, purchase_id={purchase_id}, "
            f"tariff={tariff_type}, period_days={period_days}, final_price_kopecks={final_price_kopecks}"
        )
        
        # Формируем сумму в рублях
        final_price_rubles = final_price_kopecks / 100.0
        
        # Формируем описание тарифа
        months = period_days // 30
        tariff_name = "Basic" if tariff_type == "basic" else "Plus"
        description = i18n_get_text(language, "buy.invoice_description", tariff_name=tariff_name, months=months)

        # Формируем payload (храним purchase_id для идентификации)
        payload = f"purchase:{purchase_id}"

        # Создаем invoice через CryptoBot API
        invoice_data = await cryptobot.create_invoice(
            amount_rub=final_price_rubles,
            description=description,
            payload=payload
        )
        
        invoice_id = invoice_data["invoice_id"]
        payment_url = invoice_data["pay_url"]
        
        # КРИТИЧНО: Сохраняем invoice_id в FSM state для последующей проверки статуса
        await state.update_data(cryptobot_invoice_id=invoice_id)
        
        # Сохраняем invoice_id в БД для автоматической проверки платежей
        try:
            await database.update_pending_purchase_invoice_id(purchase_id, str(invoice_id))
        except Exception as e:
            logger.error(f"Failed to save invoice_id to DB: purchase_id={purchase_id}, invoice_id={invoice_id}, error={e}")
        
        logger.info(
            f"invoice_created: provider=cryptobot, user={telegram_id}, purchase_id={purchase_id}, "
            f"tariff={tariff_type}, period_days={period_days}, invoice_id={invoice_id}, "
            f"final_price_rubles={final_price_rubles:.2f}"
        )
        
        # Отправляем пользователю сообщение с payment URL
        text = i18n_get_text(language, "payment.crypto_waiting", amount=final_price_rubles)

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=i18n_get_text(language, "payment.crypto_pay_button"),
                url=payment_url
            )],
            [InlineKeyboardButton(
                text=i18n_get_text(language, "common.back"),
                callback_data="menu_buy_vpn"
            )]
        ])
        
        await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")
        await callback.answer()
        
        # Очищаем FSM state после создания invoice
        await state.set_state(None)
        await state.clear()
        
    except Exception as e:
        logger.exception(f"Error creating CryptoBot invoice: {e}")
        await callback.answer(i18n_get_text(language, "errors.payment_create"), show_alert=True)
        await state.set_state(None)


@router.callback_query(F.data.startswith("topup_crypto:"))
async def callback_topup_crypto(callback: CallbackQuery):
    """Пополнение баланса через CryptoBot"""
    # SAFE STARTUP GUARD: Проверка готовности БД
    if not await ensure_db_ready_callback(callback):
        return
    
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    
    # Извлекаем сумму из callback_data
    amount_str = callback.data.split(":")[1]
    try:
        amount = int(amount_str)
    except ValueError:
        await callback.answer(i18n_get_text(language, "errors.invalid_amount"), show_alert=True)
        return
    
    if amount <= 0 or amount > 100000:
        await callback.answer(i18n_get_text(language, "errors.invalid_amount"), show_alert=True)
        return
    
    # Проверяем доступность CryptoBot
    from payments import cryptobot
    if not cryptobot.is_enabled():
        await callback.answer(
            i18n_get_text(language, "payment.crypto_unavailable"),
            show_alert=True
        )
        return
    
    try:
        # Создаем pending purchase для пополнения баланса
        # Используем tariff='basic' и period_days=0 как индикатор balance_topup
        amount_kopecks = amount * 100
        purchase_id = await subscription_service.create_purchase(
            telegram_id=telegram_id,
            tariff="basic",  # Используем 'basic' (требование CHECK constraint), period_days=0 будет индикатором
            period_days=0,  # Индикатор balance_topup
            price_kopecks=amount_kopecks,
            promo_code=None
        )
        
        # Формируем описание
        description = f"Пополнение баланса на {amount} ₽"
        
        # Формируем payload (храним purchase_id для идентификации)
        payload = f"purchase:{purchase_id}"
        
        # Создаем invoice через CryptoBot API
        invoice_data = await cryptobot.create_invoice(
            amount_rub=float(amount),
            description=description,
            payload=payload
        )
        
        invoice_id = invoice_data["invoice_id"]
        payment_url = invoice_data["pay_url"]
        
        # Сохраняем invoice_id в БД для автоматической проверки платежей
        try:
            await database.update_pending_purchase_invoice_id(purchase_id, str(invoice_id))
        except Exception as e:
            logger.error(f"Failed to save invoice_id to DB: purchase_id={purchase_id}, invoice_id={invoice_id}, error={e}")
        
        logger.info(
            f"balance_topup_invoice_created: provider=cryptobot, user={telegram_id}, purchase_id={purchase_id}, "
            f"amount={amount} RUB, invoice_id={invoice_id}"
        )
        
        # Отправляем пользователю сообщение с payment URL
        text = i18n_get_text(language, "main.balance_topup_waiting", amount=amount)
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=i18n_get_text(language, "main.crypto_pay_button"),
                url=payment_url
            )],
            [InlineKeyboardButton(
                text=i18n_get_text(language, "common.back"),
                callback_data="topup_balance"
            )]
        ])
        
        await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")
        await callback.answer()
        
    except Exception as e:
        logger.exception(f"Error creating CryptoBot invoice for balance top-up: {e}")
        await callback.answer(i18n_get_text(language, "errors.payment_create"), show_alert=True)


@router.callback_query(F.data == "enter_promo")
async def callback_enter_promo(callback: CallbackQuery, state: FSMContext):
    """Обработчик кнопки ввода промокода"""
    await callback.answer()
    
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    
    # КРИТИЧНО: Проверяем активную промо-сессию
    promo_session = await get_promo_session(state)
    if promo_session:
        # Промокод уже применён - показываем сообщение
        text = i18n_get_text(language, "buy.promo_applied")
        await callback.message.answer(text)
        return

    # Устанавливаем состояние ожидания промокода
    await state.set_state(PromoCodeInput.waiting_for_promo)

    text = i18n_get_text(language, "buy.enter_promo_text")
    await callback.message.answer(text)


@router.callback_query(F.data.startswith("pay_tariff_card:"))
async def callback_pay_tariff_card(callback: CallbackQuery, state: FSMContext):
    """
    Оплата тарифа картой (когда баланса не хватает)
    
    DEPRECATED: Эта функция больше не должна вызываться напрямую.
    Invoice создается автоматически в process_tariff_purchase_selection.
    
    Оставлена для обратной совместимости со старыми кнопками.
    """
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    
    # КРИТИЧНО: Получаем данные из FSM state (единственный источник правды)
    fsm_data = await state.get_data()
    purchase_id = fsm_data.get("purchase_id")
    tariff_type = fsm_data.get("tariff_type")
    period_days = fsm_data.get("period_days")
    
    # Если данных нет в FSM - пытаемся извлечь из callback_data (fallback)
    if not purchase_id or not tariff_type or not period_days:
        try:
            callback_data_parts = callback.data.split(":")
            if len(callback_data_parts) >= 4:
                tariff_type = callback_data_parts[1]
                period_days = int(callback_data_parts[2])
                purchase_id = callback_data_parts[3]
        except (IndexError, ValueError) as e:
            logger.error(f"Invalid pay_tariff_card callback_data: {callback.data}, error={e}")
            error_text = i18n_get_text(language, "errors.session_expired")
            await callback.answer(error_text, show_alert=True)
            return
    
    if not purchase_id or not tariff_type or not period_days:
        error_text = i18n_get_text(language, "errors.session_expired")
        await callback.answer(error_text, show_alert=True)
        logger.warning(f"Missing purchase data in FSM: user={telegram_id}, purchase_id={purchase_id}, tariff={tariff_type}, period={period_days}")
        return
    
    # КРИТИЧНО: Получаем pending_purchase (единственный источник правды о цене)
    pending_purchase = await database.get_pending_purchase(purchase_id, telegram_id, check_expiry=False)
    
    if not pending_purchase:
        # Purchase отсутствует - сессия устарела
        error_text = i18n_get_text(language, "errors.session_expired")
        await callback.answer(error_text, show_alert=True)
        logger.warning(f"Purchase not found in pay_tariff_card: user={telegram_id}, purchase_id={purchase_id}")
        return
    
    # КРИТИЧНО: Проверяем соответствие тарифа и периода
    if pending_purchase["tariff"] != tariff_type or pending_purchase["period_days"] != period_days:
        # Несоответствие - сессия устарела
        logger.error(
            f"Purchase mismatch in pay_tariff_card: user={telegram_id}, purchase_id={purchase_id}, "
            f"stored_tariff={pending_purchase['tariff']}, stored_period={pending_purchase['period_days']}, "
            f"expected_tariff={tariff_type}, expected_period={period_days}"
        )
        error_text = i18n_get_text(language, "errors.session_expired")
        await callback.answer(error_text, show_alert=True)
        return
    
    # КРИТИЧНО: Purchase валиден - используем его цену для invoice
    logger.info(f"Using existing purchase in pay_tariff_card: user={telegram_id}, purchase_id={purchase_id}")
    
    # Проверяем наличие provider_token
    if not config.TG_PROVIDER_TOKEN:
        await callback.answer(i18n_get_text(language, "errors.payments_unavailable"), show_alert=True)
        return

    # Используем данные из pending purchase (а не из FSM)
    amount_rubles = pending_purchase["price_kopecks"] / 100.0
    final_price_kopecks = pending_purchase["price_kopecks"]
    
    # КРИТИЧНО: Валидация минимальной суммы платежа (64 RUB = 6400 kopecks)
    MIN_PAYMENT_AMOUNT_KOPECKS = 6400
    if final_price_kopecks < MIN_PAYMENT_AMOUNT_KOPECKS:
        # Отменяем pending purchase с невалидной ценой
        await database.cancel_pending_purchases(telegram_id, "min_amount_validation_failed")

        error_text = i18n_get_text(language, "errors.payment_min_amount")
        logger.warning(
            f"payment_blocked_min_amount: user={telegram_id}, purchase_id={purchase_id}, "
            f"tariff={tariff_type}, period_days={period_days}, "
            f"final_price_kopecks={final_price_kopecks}, min_required={MIN_PAYMENT_AMOUNT_KOPECKS}"
        )
        await callback.answer(error_text, show_alert=True)
        return
    
    # Используем purchase_id в payload
    payload = f"purchase:{purchase_id}"
    
    # Формируем описание тарифа
    months = period_days // 30
    tariff_name = "Basic" if tariff_type == "basic" else "Plus"
    description = i18n_get_text(language, "buy.invoice_description", tariff_name=tariff_name, months=months)

    # Формируем prices (цена в копейках)
    prices = [LabeledPrice(label=i18n_get_text(language, "buy.invoice_label"), amount=final_price_kopecks)]

    logger.info(
        f"invoice_created: user={telegram_id}, purchase_id={purchase_id}, "
        f"tariff={tariff_type}, period_days={period_days}, "
        f"final_price_kopecks={final_price_kopecks}, amount_rubles={amount_rubles:.2f}"
    )
    
    try:
        # Отправляем invoice
        await callback.bot.send_invoice(
            chat_id=telegram_id,
            title="Atlas Secure VPN",
            description=description,
            payload=payload,
            provider_token=config.TG_PROVIDER_TOKEN,
            currency="RUB",
            prices=prices
        )
        await callback.answer()
    except Exception as e:
        logger.exception(f"Error sending invoice: {e}")
        await callback.answer(i18n_get_text(language, "errors.payment_create"), show_alert=True)


@router.callback_query(F.data.startswith("topup_card:"))
async def callback_topup_card(callback: CallbackQuery):
    """Оплата пополнения баланса картой"""
    telegram_id = callback.from_user.id
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
    import time
    timestamp = int(time.time())
    payload = f"balance_topup_{telegram_id}_{amount}_{timestamp}"
    amount_kopecks = amount * 100
    
    try:
        await callback.bot.send_invoice(
            chat_id=telegram_id,
            title=i18n_get_text(language, "main.topup_invoice_title"),
            description=i18n_get_text(language, "main.topup_invoice_description", amount=amount),
            payload=payload,
            provider_token=config.TG_PROVIDER_TOKEN,
            currency="RUB",
            prices=[LabeledPrice(label=i18n_get_text(language, "main.topup_invoice_label"), amount=amount_kopecks)]
        )
        await callback.answer()
    except Exception as e:
        logger.exception(f"Error sending invoice for balance topup: {e}")
        await callback.answer(i18n_get_text(language, "errors.payment_create"), show_alert=True)


@router.callback_query(F.data.startswith("crypto_pay:tariff:"))
async def callback_crypto_pay_tariff(callback: CallbackQuery, state: FSMContext):
    """Оплата тарифа криптой - ОТКЛЮЧЕНА"""
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    logger.warning(f"crypto_payment_disabled: user={telegram_id}, callback_data={callback.data}")

    await callback.answer(i18n_get_text(language, "payment.crypto_unavailable"), show_alert=True)
    return


@router.callback_query(F.data.startswith("pay_crypto_asset:"))
async def callback_pay_crypto_asset(callback: CallbackQuery, state: FSMContext):
    """Оплата криптой (выбор актива) - ОТКЛЮЧЕНА"""
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    logger.warning(f"crypto_payment_disabled: user={telegram_id}, callback_data={callback.data}")

    await callback.answer(i18n_get_text(language, "payment.crypto_unavailable"), show_alert=True)
    return


@router.callback_query(F.data.startswith("crypto_pay:balance:"))
async def callback_crypto_pay_balance(callback: CallbackQuery):
    """Оплата пополнения баланса криптой - ОТКЛЮЧЕНА"""
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    logger.warning(f"crypto_payment_disabled: user={telegram_id}, callback_data={callback.data}")

    await callback.answer(i18n_get_text(language, "payment.crypto_unavailable"), show_alert=True)
    return


@router.callback_query(F.data == "crypto_disabled")
async def callback_crypto_disabled(callback: CallbackQuery):
    """Обработчик неактивной кнопки крипты"""
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    logger.warning(f"crypto_payment_disabled: user={telegram_id}, callback_data={callback.data}")

    await callback.answer(i18n_get_text(language, "payment.crypto_unavailable"), show_alert=True)
    return


@router.message(PromoCodeInput.waiting_for_promo)
async def process_promo_code(message: Message, state: FSMContext):
    # STEP 4 — PART A: INPUT TRUST BOUNDARIES
    # Validate telegram_id
    telegram_id = message.from_user.id
    is_valid, error = validate_telegram_id(telegram_id)
    if not is_valid:
        log_security_warning(
            event="Invalid telegram_id in promo code input",
            telegram_id=telegram_id,
            correlation_id=str(message.message_id) if hasattr(message, 'message_id') else None,
            details={"error": error}
        )
        language = await resolve_user_language(message.from_user.id)
        await message.answer(i18n_get_text(language, "errors.try_later"))
        return
    
    # STEP 4 — PART A: INPUT TRUST BOUNDARIES
    # Validate message text
    promo_code = message.text.strip().upper() if message.text else None
    is_valid_promo, promo_error = validate_promo_code(promo_code)
    if not is_valid_promo:
        log_security_warning(
            event="Invalid promo code format",
            telegram_id=telegram_id,
            correlation_id=str(message.message_id) if hasattr(message, 'message_id') else None,
            details={"error": promo_error, "promo_code_preview": promo_code[:20] if promo_code else None}
        )
        language = await resolve_user_language(telegram_id)
        text = i18n_get_text(language, "main.invalid_promo")
        await message.answer(text)
        return
    """Обработчик ввода промокода"""
    # SAFE STARTUP GUARD: Проверка готовности БД
    if not await ensure_db_ready_message(message):
        await state.clear()
        return
    
    telegram_id = message.from_user.id
    language = await resolve_user_language(telegram_id)
    
    
    # ⛔ Защита от non-text апдейтов (callback / invoice / system)
    if not message.text:
        await message.answer("Пожалуйста, введите промокод текстом.")
        return

    promo_code = message.text.strip().upper()
    
    # КРИТИЧНО: Проверяем активную промо-сессию
    promo_session = await get_promo_session(state)
    if promo_session and promo_session.get("promo_code") == promo_code:
        # Промокод уже применён в активной сессии - показываем сообщение
        expires_at = promo_session.get("expires_at", 0)
        expires_in = max(0, int(expires_at - time.time()))
        text = i18n_get_text(language, "main.promo_applied")
        await message.answer(text)
        # Возвращаемся к выбору тарифа
        await state.set_state(PurchaseState.choose_tariff)
        tariff_text = i18n_get_text(language, "buy.select_tariff")
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=i18n_get_text(language, "buy.tariff_basic"), callback_data="tariff:basic")],
            [InlineKeyboardButton(text=i18n_get_text(language, "buy.tariff_plus"), callback_data="tariff:plus")],
            [InlineKeyboardButton(
                text=i18n_get_text(language, "buy.enter_promo"),
                callback_data="enter_promo"
            )],
            [InlineKeyboardButton(
                text=i18n_get_text(language, "common.back"),
                callback_data="menu_main"
            )],
        ])
        await message.answer(tariff_text, reply_markup=keyboard)
        return
    
    # Проверяем промокод через базу данных
    promo_data = await database.check_promo_code_valid(promo_code)
    if promo_data:
        # Промокод валиден
        discount_percent = promo_data["discount_percent"]
        
        # КРИТИЧНО: Создаём промо-сессию с TTL 5 минут
        await create_promo_session(
            state=state,
            promo_code=promo_code,
            discount_percent=discount_percent,
            telegram_id=telegram_id,
            ttl_seconds=300
        )
        
        # КРИТИЧНО: НЕ отменяем pending покупки - промо-сессия независима от покупки
        
        # КРИТИЧНО: Возвращаем пользователя к выбору тарифа с обновленными ценами
        await state.set_state(PurchaseState.choose_tariff)
        
        text = i18n_get_text(language, "main.promo_applied")
        await message.answer(text)
        
        logger.info(
            f"promo_applied: user={telegram_id}, promo_code={promo_code}, "
            f"discount_percent={discount_percent}%, old_purchases_cancelled=True"
        )
        
        # Возвращаемся к выбору типа тарифа (Basic/Plus) - цены будут пересчитаны с промокодом
        tariff_text = i18n_get_text(language, "buy.select_tariff")
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=i18n_get_text(language, "buy.tariff_basic", "tariff_basic"),
                callback_data="tariff:basic"
            )],
            [InlineKeyboardButton(
                text=i18n_get_text(language, "buy.tariff_plus", "tariff_plus"),
                callback_data="tariff:plus"
            )],
            [InlineKeyboardButton(
                text=i18n_get_text(language, "buy.enter_promo"),
                callback_data="enter_promo"
            )],
            [InlineKeyboardButton(
                text=i18n_get_text(language, "common.back"),
                callback_data="menu_main"
            )],
        ])
        await message.answer(tariff_text, reply_markup=keyboard)
        await state.set_state(PurchaseState.choose_tariff)
    else:
        # Промокод невалиден
        text = i18n_get_text(language, "main.invalid_promo")
        await message.answer(text)


# Старый обработчик tariff_* удалён - теперь используется новый флоу tariff_type -> tariff_period


@router.pre_checkout_query()
async def process_pre_checkout_query(pre_checkout_query: PreCheckoutQuery):
    """Обработчик pre_checkout_query - подтверждение платежа перед списанием"""
    # Всегда подтверждаем платеж
    await pre_checkout_query.answer(ok=True)
    
    # Логируем событие
    payload = pre_checkout_query.invoice_payload
    telegram_id = pre_checkout_query.from_user.id
    
    logger.info(f"Pre-checkout query: user_id={telegram_id}, payload={payload}, amount={pre_checkout_query.total_amount}")
    
    # Логируем в audit_log
    try:
        await database._log_audit_event_atomic_standalone(
            "telegram_payment_pre_checkout",
            telegram_id,
            telegram_id,
            f"Pre-checkout query: payload={payload}, amount={pre_checkout_query.total_amount / 100} RUB"
        )
    except Exception as e:
        logger.error(f"Error logging pre-checkout query: {e}")


# --- Incoming photo file_id logging (non-intrusive, no reply). Remove after collecting file_ids. ---
@router.message(F.photo)
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


@router.message(F.successful_payment)
async def process_successful_payment(message: Message, state: FSMContext):
    """Обработчик successful_payment - успешная оплата картой
    
    КРИТИЧНО:
    - Использует finalize_purchase для активации подписки
    - Очищает FSM state после успешной активации
    - Отправляет VPN ключ пользователю
    """
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
        now = datetime.utcnow()
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
                callback_data="menu_support"
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
    start_time = time.time()
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
    
    # КРИТИЧНО: Логируем получение события оплаты от Telegram
    logger.info(
        f"payment_event_received: provider=telegram_payment, user={telegram_id}, "
        f"payload={payload}, amount={payment.total_amount / 100.0:.2f} RUB, "
        f"currency={payment.currency}"
    )
    
    # Проверяем, является ли это пополнением баланса
    try:
        payload_info = await payment_service.verify_payment_payload(payload, telegram_id)
        
        if payload_info.payload_type == "balance_topup":
            # Пополнение баланса - используем payment service
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
            
            try:
                result = await payment_service.finalize_balance_topup_payment(
                    telegram_id=telegram_id,
                    amount_rubles=payment_amount_rubles,
                    provider="telegram",
                    provider_charge_id=provider_charge_id,
                    description="Пополнение баланса через Telegram Payments",
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
            
            await message.answer(text, reply_markup=keyboard)
            
            # ИДЕМПОТЕНТНОСТЬ: Помечаем уведомление как отправленное (после успешной отправки)
            try:
                sent = await database.mark_payment_notification_sent(payment_id)
                if sent:
                    logger.info(
                        f"NOTIFICATION_SENT [type=balance_topup, payment_id={payment_id}, user={telegram_id}]"
                    )
                else:
                    logger.warning(
                        f"NOTIFICATION_FLAG_ALREADY_SET [type=balance_topup, payment_id={payment_id}, user={telegram_id}]"
                    )
            except Exception as e:
                logger.error(
                    f"CRITICAL: Failed to mark notification as sent: payment_id={payment_id}, user={telegram_id}, error={e}"
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
        error_text = "Сессия покупки устарела. Пожалуйста, начните заново."
        language = await resolve_user_language(telegram_id)
        await message.answer(i18n_get_text(language, "errors.payment_processing"))
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
    payment_amount_rubles = payment.total_amount / 100.0
    
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
        
    # Finalize subscription payment through payment service
    try:
        result = await payment_service.finalize_subscription_payment(
            purchase_id=purchase_id,
            telegram_id=telegram_id,
            payment_provider="telegram_payment",
            amount_rubles=payment_amount_rubles
        )
        
        payment_id = result.payment_id
        expires_at = result.expires_at
        vpn_key = result.vpn_key
        is_renewal = result.is_renewal
        
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
                    callback_data="menu_support"
                )]
            ])
            
            try:
                await message.answer(
                    pending_text,
                    reply_markup=pending_keyboard,
                    parse_mode="HTML"
                )
                logger.info(
                    f"Pending activation message sent: user={telegram_id}, payment_id={payment_id}, purchase_id={purchase_id}, expires_at={expires_str}"
                )
            except Exception as e:
                logger.error(f"Failed to send pending activation message: user={telegram_id}, error={e}")
            
            # Помечаем уведомление как отправленное
            try:
                sent = await database.mark_payment_notification_sent(payment_id)
                if sent:
                    logger.info(
                        f"NOTIFICATION_SENT [type=payment_success_pending, payment_id={payment_id}, user={telegram_id}, purchase_id={purchase_id}]"
                    )
            except Exception as e:
                logger.error(f"Failed to mark pending activation notification as sent: {e}")
            
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
        
        # КРИТИЧНО: Дополнительная проверка - VPN ключ должен быть валидным после finalize_purchase
        if not vpn_key:
            error_msg = f"VPN key is empty after finalize_purchase: purchase_id={purchase_id}, user={telegram_id}, payment_id={payment_id}"
            logger.error(f"process_successful_payment: CRITICAL_VPN_KEY_MISSING: {error_msg}")
            raise Exception(error_msg)
        
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
        
        # КРИТИЧНО: VPN ключ отправляется СРАЗУ после успешной финализации платежа
        # Валидация уже выполнена внутри finalize_purchase - здесь только отправка
        # КРИТИЧНО: Это гарантирует что пользователь ВСЕГДА получит VPN ключ после оплаты
        
        # Если использован промокод, увеличиваем счетчик использований и логируем
        if promo_code_used:
            try:
                # Получаем данные промокода для логирования
                promo_data = await database.get_promo_code(promo_code_used)
                if promo_data:
                    discount_percent = promo_data["discount_percent"]
                # Рассчитываем price_before (базовая цена тарифа)
                base_price = config.TARIFFS[tariff_type][period_days]["price"]
                price_before = base_price
                price_after = payment_amount_rubles
                
                # Увеличиваем счетчик использований
                await database.increment_promo_code_use(promo_code_used)
                
                # Логируем использование промокода
                await database.log_promo_code_usage(
                    promo_code=promo_code_used,
                    telegram_id=telegram_id,
                    tariff=f"{tariff_type}_{period_days}",
                    discount_percent=discount_percent,
                    price_before=price_before,
                    price_after=price_after
                )
            except Exception as e:
                logger.error(f"Error processing promo code usage: {e}")
    
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
    
    # Отправляем сообщение об успешной активации с гарантированным fallback
    try:
        text = i18n_get_text(language, "payment.approved", date=expires_str)
        # B3.1 - SOFT DEGRADATION: Add soft UX notice if degraded (only where messages are sent)
        try:
            if _degradation_notice:
                text += "\n\n⏳ Возможны небольшие задержки"
        except NameError:
            pass  # _degradation_notice not set - ignore
        await message.answer(text, reply_markup=get_vpn_key_keyboard(language), parse_mode="HTML")
    except Exception as e:
        logger.error(f"Failed to send payment approval message with localization: user={telegram_id}, error={e}")
        # КРИТИЧНО: Fallback на русский текст если локализация не работает
        try:
            fallback_text = f"✅ Оплата подтверждена! Доступ до {expires_str}"
            await message.answer(fallback_text, reply_markup=get_vpn_key_keyboard("ru"), parse_mode="HTML")
        except Exception as fallback_error:
            logger.error(f"Failed to send fallback payment approval message: user={telegram_id}, error={fallback_error}")
        # Не критично - продолжаем отправку ключа
    
    # КРИТИЧНО: Отправляем VPN-ключ отдельным сообщением (позволяет одно нажатие для копирования)
    try:
        await message.answer(f"<code>{vpn_key}</code>", parse_mode="HTML")
        
        logger.info(
            f"process_successful_payment: VPN_KEY_SENT [user={telegram_id}, payment_id={payment_id}, "
            f"purchase_id={purchase_id}, expires_at={expires_str}, vpn_key_length={len(vpn_key)}]"
        )
        
        # ИДЕМПОТЕНТНОСТЬ: Помечаем уведомление как отправленное (после успешной отправки VPN ключа)
        try:
            sent = await database.mark_payment_notification_sent(payment_id)
            if sent:
                logger.info(
                    f"NOTIFICATION_SENT [type=payment_success, payment_id={payment_id}, user={telegram_id}, "
                    f"purchase_id={purchase_id}]"
                )
            else:
                logger.warning(
                    f"NOTIFICATION_FLAG_ALREADY_SET [type=payment_success, payment_id={payment_id}, user={telegram_id}]"
                )
        except Exception as e:
            logger.error(
                f"CRITICAL: Failed to mark notification as sent: payment_id={payment_id}, user={telegram_id}, error={e}"
            )
        
        # КРИТИЧНО: Очищаем FSM state после успешной активации подписки
        try:
            current_state = await state.get_state()
            if current_state is not None:
                await state.clear()
                logger.debug(f"FSM state cleared after successful payment: user={telegram_id}, was_state={current_state}")
        except Exception as e:
            logger.debug(f"FSM state clear failed (may be already clear): {e}")
        
    except Exception as e:
        # КРИТИЧНО: Если не удалось отправить ключ - это критическая ошибка
        error_msg = f"CRITICAL: Failed to send VPN key to user: user={telegram_id}, payment_id={payment_id}, purchase_id={purchase_id}, error={e}"
        logger.error(error_msg)
        # Логируем для админа
        try:
            await database._log_audit_event_atomic_standalone(
                "vpn_key_send_failed",
                config.ADMIN_TELEGRAM_ID,
                telegram_id,
                f"Payment finalized but VPN key send failed: payment_id={payment_id}, purchase_id={purchase_id}, key={vpn_key[:50]}..."
            )
        except Exception:
            pass
        
        # Пытаемся отправить ключ повторно
        try:
            await message.answer(
                f"✅ Оплата подтверждена! Доступ до {expires_str}\n\n"
                f"<code>{vpn_key}</code>",
                parse_mode="HTML"
            )
            logger.info(f"VPN key sent on retry: user={telegram_id}, payment_id={payment_id}")
        except Exception as retry_error:
            logger.error(f"VPN key send retry also failed: user={telegram_id}, error={retry_error}")
            # Ключ есть в БД, пользователь может получить через профиль
    
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


@router.callback_query(F.data == "payment_test")
async def callback_payment_test(callback: CallbackQuery):
    """Тестовая оплата (не работает)"""
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    
    # Тестовая оплата не работает - возвращаем назад
    await callback.answer("Эта функция не работает", show_alert=True)
    text = i18n_get_text(language, "main.select_payment", "select_payment")
    await safe_edit_text(callback.message, text, reply_markup=get_payment_method_keyboard(language))


@router.callback_query(F.data == "payment_sbp")
async def callback_payment_sbp(callback: CallbackQuery, state: FSMContext):
    """Оплата через СБП"""
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    
    data = await state.get_data()
    tariff_key = data.get("tariff", "basic")  # Используем "basic" как дефолт вместо "1"
    
    if tariff_key not in config.TARIFFS:
        error_msg = f"Invalid tariff_key '{tariff_key}' for user {telegram_id}. Valid tariffs: {list(config.TARIFFS.keys())}"
        logger.error(error_msg)
        await callback.answer(i18n_get_text(language, "errors.tariff"), show_alert=True)
        return
    
    # Используем период 30 дней как дефолт
    if 30 not in config.TARIFFS[tariff_key]:
        error_msg = f"Period 30 days not found in tariff '{tariff_key}' for user {telegram_id}"
        logger.error(error_msg)
        await callback.answer(i18n_get_text(language, "errors.tariff"), show_alert=True)
        return
    
    tariff_data = config.TARIFFS[tariff_key][30]  # Используем период 30 дней
    base_price = tariff_data["price"]
    
    # Рассчитываем цену с учетом скидки (та же логика, что в create_payment)
    # ПРИОРИТЕТ 1: VIP-статус
    is_vip = await database.is_vip_user(telegram_id)
    
    if is_vip:
        amount = int(base_price * 0.70)  # 30% скидка
    else:
        # ПРИОРИТЕТ 2: Персональная скидка
        personal_discount = await database.get_user_discount(telegram_id)
        
        if personal_discount:
            discount_percent = personal_discount["discount_percent"]
            amount = int(base_price * (1 - discount_percent / 100))
        else:
            # Без скидки
            amount = base_price
    
    # Формируем текст с реквизитами
    text = i18n_get_text(language, "main.sbp_payment_text", amount=amount)
    
    await safe_edit_text(callback.message, text, reply_markup=get_sbp_payment_keyboard(language))
    await callback.answer()


@router.callback_query(F.data == "payment_paid")
async def callback_payment_paid(callback: CallbackQuery, state: FSMContext):
    """Пользователь нажал 'Я оплатил'"""
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    
    data = await state.get_data()
    tariff_key = data.get("tariff", "1")
    
    # Проверяем наличие pending платежа перед созданием
    existing_payment = await database.get_pending_payment_by_user(telegram_id)
    if existing_payment:
        text = i18n_get_text(language, "payment.pending", "payment_pending")
        await safe_edit_text(callback.message, text, reply_markup=get_pending_payment_keyboard(language))
        await callback.answer("У вас уже есть ожидающий платеж", show_alert=True)
        await state.clear()
        return
    
    # Создаем платеж
    payment_id = await database.create_payment(telegram_id, tariff_key)
    
    if payment_id is None:
        # Это не должно произойти, так как мы проверили выше, но на всякий случай
        text = i18n_get_text(language, "payment.pending", "payment_pending")
        await safe_edit_text(callback.message, text, reply_markup=get_pending_payment_keyboard(language))
        await callback.answer("Не удалось создать платеж. Попробуйте позже.", show_alert=True)
        await state.clear()
        return
    
    # Получаем данные платежа, чтобы показать реальную сумму администратору
    payment = await database.get_payment(payment_id)
    if payment:
        actual_amount = payment["amount"] / 100.0  # Конвертируем из копеек
    else:
        # Fallback: используем базовую цену тарифа basic 30 дней
        if "basic" in config.TARIFFS and 30 in config.TARIFFS["basic"]:
            actual_amount = config.TARIFFS["basic"][30]["price"]
        else:
            actual_amount = 149  # Дефолтная цена
    
    # Отправляем сообщение пользователю
    text = i18n_get_text(language, "payment.pending", "payment_pending")
    await safe_edit_text(callback.message, text, reply_markup=get_pending_payment_keyboard(language))
    await callback.answer()
    
    # Уведомляем администратора с реальной суммой платежа
    # Используем базовую цену тарифа basic 30 дней как fallback
    if tariff_key in config.TARIFFS and 30 in config.TARIFFS[tariff_key]:
        tariff_data = config.TARIFFS[tariff_key][30]
    elif "basic" in config.TARIFFS and 30 in config.TARIFFS["basic"]:
        tariff_data = config.TARIFFS["basic"][30]
        logger.warning(f"Using fallback tariff 'basic' 30 days for tariff_key '{tariff_key}'")
    else:
        error_msg = f"CRITICAL: Cannot find valid tariff data for tariff_key '{tariff_key}'"
        logger.error(error_msg)
        tariff_data = {"price": 149}  # Дефолтная цена
    
    # Safe username extraction: can be None
    user_lang = await resolve_user_language(telegram_id)
    username = (callback.from_user.username if callback.from_user else None) or i18n_get_text(user_lang, "common.username_not_set")
    
    # Admin notification: admin always sees Russian (ADMIN RU ALLOWED)
    admin_text = i18n_get_text(
        "ru",
        "admin.payment_notification",
        username=username,
        telegram_id=telegram_id,
        tariff=f"{tariff_key}_30",
        price=actual_amount
    )
    
    try:
        await callback.bot.send_message(
            config.ADMIN_TELEGRAM_ID,
            admin_text,
            reply_markup=get_admin_payment_keyboard(payment_id, "ru")
        )
    except Exception as e:
        logging.error(f"Error sending admin notification: {e}")
    
    await state.clear()


async def _open_about_screen(event: Union[Message, CallbackQuery], bot: Bot):
    """О сервисе. Reusable for callback and /info command."""
    msg = event.message if isinstance(event, CallbackQuery) else event
    telegram_id = event.from_user.id
    language = await resolve_user_language(telegram_id)
    title = i18n_get_text(language, "main.about_title")
    text = i18n_get_text(language, "main.about_text", "about_text")
    full_text = f"{title}\n\n{text}"
    await safe_edit_text(msg, full_text, reply_markup=get_about_keyboard(language), parse_mode="HTML", bot=bot)
    if isinstance(event, CallbackQuery):
        await event.answer()


@router.callback_query(F.data == "menu_about")
async def callback_about(callback: CallbackQuery):
    """О сервисе. Entry from inline button."""
    await _open_about_screen(callback, callback.bot)


@router.callback_query(F.data == "menu_service_status")
async def callback_service_status(callback: CallbackQuery):
    """Статус сервиса"""
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    
    text = i18n_get_text(language, "main.service_status_text", "service_status_text")
    
    # Добавляем предупреждение об инциденте, если режим активен
    incident = await database.get_incident_settings()
    if incident["is_active"]:
        incident_text = incident.get("incident_text") or i18n_get_text(language, "main.incident_banner", "incident_banner")
        warning = i18n_get_text(language, "main.incident_status_warning", incident_text=incident_text)
        text = text + warning
    
    await safe_edit_text(callback.message, text, reply_markup=get_service_status_keyboard(language))
    await callback.answer()


@router.callback_query(F.data == "about_privacy")
async def callback_privacy(callback: CallbackQuery):
    """Политика конфиденциальности"""
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    
    text = i18n_get_text(language, "main.privacy_policy_text", "privacy_policy_text")
    await safe_edit_text(callback.message, text, reply_markup=get_about_keyboard(language))
    await callback.answer()


async def _open_instruction_screen(event: Union[Message, CallbackQuery], bot: Bot):
    """Инструкция. Reusable for callback and /instruction command."""
    msg = event.message if isinstance(event, CallbackQuery) else event
    telegram_id = event.from_user.id
    language = await resolve_user_language(telegram_id)
    platform = detect_platform(event)
    text = i18n_get_text(language, "instruction._text", "instruction_text")
    await safe_edit_text(msg, text, reply_markup=get_instruction_keyboard(language, platform), bot=bot)
    if isinstance(event, CallbackQuery):
        await event.answer()


@router.callback_query(F.data == "menu_instruction")
async def callback_instruction(callback: CallbackQuery):
    """Инструкция. Entry from inline button."""
    await _open_instruction_screen(callback, callback.bot)


async def _open_referral_screen(event: Union[Message, CallbackQuery], bot: Bot):
    """
    Экран «Программа лояльности». Reusable for callback and /referral command.
    Sends new message (photo or text), does not edit.
    """
    chat_id = event.message.chat.id if isinstance(event, CallbackQuery) else event.chat.id
    telegram_id = event.from_user.id
    language = await resolve_user_language(telegram_id)
    
    async def _send_error(err_text: str):
        if isinstance(event, CallbackQuery):
            await event.answer(err_text, show_alert=True)
        else:
            await bot.send_message(chat_id, err_text)
    
    try:
        stats = await database.get_referral_statistics(telegram_id)
        total_invited = stats.get("total_invited", 0)
        active_paid_referrals = stats.get("active_paid_referrals", 0)
        total_cashback = stats.get("total_cashback_earned", 0.0)
        current_level_name = stats.get("current_level_name", "Silver Access")
        cashback_percent = stats.get("cashback_percent", 10)
        next_level_name = stats.get("next_level_name")
        remaining_connections = stats.get("remaining_connections", 0)
        last_activity_at = stats.get("last_activity_at")
        
        last_activity_str = "—"
        if last_activity_at:
            if isinstance(last_activity_at, str):
                try:
                    last_activity_at = datetime.fromisoformat(last_activity_at.replace("Z", "+00:00"))
                except Exception:
                    pass
            if isinstance(last_activity_at, datetime):
                last_activity_str = last_activity_at.strftime("%d.%m.%Y")
        
        # Формируем строку "До следующего уровня"
        if next_level_name and remaining_connections > 0:
            next_level_line = i18n_get_text(
                language,
                "referral.next_level_line",
                next_status_name=next_level_name,
                remaining_invites=remaining_connections
            )
        else:
            next_level_line = i18n_get_text(language, "referral.max_level_reached")
        
        # Новый формат текста с разделёнными метриками
        text = (
            f"{i18n_get_text(language, 'referral.screen_title')}\n\n"
            f"{i18n_get_text(language, 'referral.total_invited', count=total_invited)}\n"
            f"{i18n_get_text(language, 'referral.active_with_subscription', count=active_paid_referrals)}\n\n"
            f"{i18n_get_text(language, 'referral.current_status', status=current_level_name)}\n"
            f"{i18n_get_text(language, 'referral.cashback_level', percent=cashback_percent)}\n\n"
            f"{next_level_line}\n\n"
            f"{i18n_get_text(language, 'referral.rewards_earned', amount=total_cashback)}\n"
            f"{i18n_get_text(language, 'referral.last_activity', date=last_activity_str)}"
        )
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=i18n_get_text(language, "referral.share_button"),
                callback_data="share_referral_link"
            )],
            [InlineKeyboardButton(
                text=i18n_get_text(language, "referral.stats_button"),
                callback_data="referral_stats"
            )],
            [InlineKeyboardButton(
                text=i18n_get_text(language, "common.back"),
                callback_data="menu_main"
            )],
        ])
        
        file_id = get_loyalty_screen_attachment(current_level_name)
        if file_id:
            await bot.send_photo(
                chat_id=chat_id,
                photo=file_id,
                caption=text,
                reply_markup=keyboard,
                parse_mode=None,
            )
        else:
            await bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=keyboard,
            )
        if isinstance(event, CallbackQuery):
            await event.answer()
        logger.debug(
            f"Referral screen opened: user={telegram_id}, "
            f"total_invited={total_invited}, active_paid={active_paid_referrals}, "
            f"level={current_level_name}, percent={cashback_percent}%, "
            f"cashback={total_cashback:.2f} RUB, remaining={remaining_connections}, with_photo={bool(file_id)}"
        )
    except Exception as e:
        logger.exception(f"Error in referral screen handler: user={telegram_id}: {e}")
        await _send_error(i18n_get_text(language, "errors.profile_load"))


@router.callback_query(F.data == "menu_referral")
async def callback_referral(callback: CallbackQuery):
    """Экран «Программа лояльности». Entry from inline button."""
    await _open_referral_screen(callback, callback.bot)


@router.callback_query(F.data == "share_referral_link")
@router.callback_query(F.data == "copy_referral_link")
async def callback_copy_referral_link(callback: CallbackQuery):
    """Поделиться реферальной ссылкой - отправляет ссылку отдельным сообщением"""
    telegram_id = callback.from_user.id
    language = await resolve_user_language(callback.from_user.id)
    
    try:
        # Получаем username бота для реферальной ссылки
        bot_info = await callback.bot.get_me()
        bot_username = bot_info.username
        # Реферальная ссылка: https://t.me/<bot_username>?start=ref_<telegram_id>
        referral_link = f"https://t.me/{bot_username}?start=ref_{telegram_id}"
        
        # Отправляем ссылку отдельным сообщением для копирования (одно нажатие в Telegram)
        await callback.message.answer(
            f"<code>{referral_link}</code>",
            parse_mode="HTML"
        )
        
        # Показываем toast уведомление
        await callback.answer(i18n_get_text(language, "referral.link_copied"), show_alert=False)
        
        logger.info(f"Referral link sent to user: {telegram_id}")
        
    except Exception as e:
        logger.exception(f"Error in share_referral_link handler: user={telegram_id}: {e}")
        await callback.answer(i18n_get_text(language, "errors.profile_load"), show_alert=True)


@router.callback_query(F.data == "referral_stats")
async def callback_referral_stats(callback: CallbackQuery):
    """Экран «Подробнее» — расширенный презентационный текст. Delete + answer to support navigation from photo (loyalty screen)."""
    telegram_id = callback.from_user.id
    language = await resolve_user_language(callback.from_user.id)
    
    try:
        try:
            await callback.message.delete()
        except Exception:
            pass
        
        stats = await database.get_referral_statistics(telegram_id)
        total_invited = stats.get("total_invited", 0)
        current_level_name = stats.get("current_level_name", "Silver Access")
        next_level_name = stats.get("next_level_name")
        remaining_connections = stats.get("remaining_connections", 0)
        
        if next_level_name and remaining_connections > 0:
            status_footer = i18n_get_text(
                language,
                "referral.status_footer",
                remaining_invites=remaining_connections
            )
        else:
            status_footer = i18n_get_text(language, "referral.max_level_reached")
        
        bot_info = await callback.bot.get_me()
        referral_link = f"https://t.me/{bot_info.username}?start=ref_{telegram_id}"
        
        text = i18n_get_text(
            language,
            "referral.stats_screen",
            referral_link=referral_link,
            current_status_name=current_level_name,
            status_footer=status_footer
        )
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=i18n_get_text(language, "common.back"),
                callback_data="menu_referral"
            )]
        ])
        
        await callback.bot.send_message(callback.message.chat.id, text, reply_markup=keyboard)
        await callback.answer()
        
    except Exception as e:
        logger.exception(f"Error in referral_stats handler: user={telegram_id}: {e}")
        await callback.answer(i18n_get_text(language, "errors.profile_load"), show_alert=True)


@router.callback_query(F.data == "referral_how_it_works")
async def callback_referral_how_it_works(callback: CallbackQuery):
    """Экран «Как работает программа» для реферальной программы"""
    telegram_id = callback.from_user.id
    language = await resolve_user_language(callback.from_user.id)
    
    try:
        text = i18n_get_text(language, "referral.how_it_works_text")
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=i18n_get_text(language, "common.back"),
                callback_data="menu_referral"
            )],
        ])
        
        await safe_edit_text(callback.message, text, reply_markup=keyboard)
        await callback.answer()
        
    except Exception as e:
        logger.exception(f"Error in referral_how_it_works handler: user={telegram_id}: {e}")
        await callback.answer(i18n_get_text(language, "errors.profile_load"), show_alert=True)


async def _open_support_screen(event: Union[Message, CallbackQuery], bot: Bot):
    """Поддержка. Reusable for callback and /help command."""
    msg = event.message if isinstance(event, CallbackQuery) else event
    telegram_id = event.from_user.id
    language = await resolve_user_language(telegram_id)
    text = i18n_get_text(language, "main.support_text", "support_text")
    await safe_edit_text(msg, text, reply_markup=get_support_keyboard(language), bot=bot)
    if isinstance(event, CallbackQuery):
        await event.answer()


@router.callback_query(F.data == "menu_support")
async def callback_support(callback: CallbackQuery):
    """Поддержка. Entry from inline button."""
    await _open_support_screen(callback, callback.bot)


@router.callback_query(F.data.startswith("approve_payment:"))
async def approve_payment(callback: CallbackQuery):
    """Админ подтвердил платеж"""
    await callback.answer()  # ОБЯЗАТЕЛЬНО
    
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        logging.warning(f"Unauthorized approve attempt by user {callback.from_user.id}")
        await callback.answer("Нет доступа", show_alert=True)
        return
    
    try:
        payment_id = int(callback.data.split(":")[1])
        
        logging.info(f"APPROVE pressed by admin {callback.from_user.id}, payment_id={payment_id}")
        
        # Получить платеж из БД
        payment = await database.get_payment(payment_id)
        
        if not payment:
            logging.warning(f"Payment {payment_id} not found for approve")
            await callback.answer("Платеж не найден", show_alert=True)
            return
        
        if payment["status"] != "pending":
            logging.warning(
                f"Attempt to approve already processed payment {payment_id}, status={payment['status']}"
            )
            await callback.answer("Платеж уже обработан", show_alert=True)
            # Удаляем кнопки даже если платеж уже обработан
            await safe_edit_reply_markup(callback.message, reply_markup=None)
            return
        
        telegram_id = payment["telegram_id"]
        tariff_key = payment["tariff"]
        
        # Парсим tariff_key (формат: "basic_30" или "plus_90")
        if "_" in tariff_key:
            tariff_type, period_str = tariff_key.split("_", 1)
            try:
                period_days = int(period_str)
            except ValueError:
                logger.error(f"Invalid period in tariff_key '{tariff_key}' for payment {payment_id}")
                period_days = 30
        else:
            # Fallback: используем basic 30 дней
            tariff_type = "basic"
            period_days = 30
            logger.warning(f"Invalid tariff_key format '{tariff_key}', using fallback: basic_30")
        
        # Получаем данные тарифа
        if tariff_type in config.TARIFFS and period_days in config.TARIFFS[tariff_type]:
            tariff_data = config.TARIFFS[tariff_type][period_days]
        elif "basic" in config.TARIFFS and 30 in config.TARIFFS["basic"]:
            tariff_data = config.TARIFFS["basic"][30]
            logger.warning(f"Using fallback tariff 'basic' 30 days for tariff_key '{tariff_key}'")
        else:
            error_msg = f"CRITICAL: Cannot find valid tariff data for tariff_key '{tariff_key}'"
            logger.error(error_msg)
            user = await database.get_user(callback.from_user.id)
            language = await resolve_user_language(callback.from_user.id)
            await callback.answer(i18n_get_text(language, "errors.invalid_tariff"), show_alert=True)
            return
        
        # Атомарно подтверждаем платеж и создаем/продлеваем подписку
        # VPN-ключ создается через Xray API
        admin_telegram_id = callback.from_user.id
        # Пересчитываем months из period_days для совместимости со старой функцией
        months = period_days // 30
        result = await database.approve_payment_atomic(
            payment_id, 
            months,  # Используем пересчитанное значение из period_days
            admin_telegram_id,
            bot=callback.bot  # Передаём бот для отправки уведомлений рефереру
        )
        expires_at, is_renewal, vpn_key = result
        
        if expires_at is None or vpn_key is None:
            logging.error(f"Failed to approve payment {payment_id} atomically")
            user = await database.get_user(callback.from_user.id)
            language = await resolve_user_language(callback.from_user.id)
            await callback.answer(i18n_get_text(language, "errors.vpn_key_creation"), show_alert=True)
            return
        
        # E) PURCHASE FLOW: Send referral notification for admin-approved payments
        # process_referral_reward was called in approve_payment_atomic, get reward details
        if not is_renewal:
            try:
                # Get payment amount
                payment_row = await database.get_payment(payment_id)
                if payment_row:
                    payment_amount_rubles = (payment_row.get("amount", 0) or 0) / 100.0
                    
                    # Get referral reward from referral_rewards table
                    pool = await database.get_pool()
                    async with pool.acquire() as conn:
                        purchase_id_str = f"admin_approve_{payment_id}"
                        reward_row = await conn.fetchrow(
                            """SELECT referrer_id, percent, reward_amount, 
                               (SELECT COUNT(DISTINCT referred_user_id) FROM referrals 
                                WHERE referrer_user_id = referral_rewards.referrer_id 
                                AND first_paid_at IS NOT NULL) as paid_count
                               FROM referral_rewards 
                               WHERE buyer_id = $1 AND purchase_id = $2
                               ORDER BY id DESC LIMIT 1""",
                            telegram_id, purchase_id_str
                        )
                        
                        if reward_row:
                            referrer_id = reward_row.get("referrer_id")
                            cashback_percent = reward_row.get("percent", 0)
                            cashback_amount = (reward_row.get("reward_amount", 0) or 0) / 100.0
                            paid_referrals_count = reward_row.get("paid_count", 0) or 0
                            
                            # Calculate referrals needed
                            if paid_referrals_count < 25:
                                referrals_needed = 25 - paid_referrals_count
                            elif paid_referrals_count < 50:
                                referrals_needed = 50 - paid_referrals_count
                            else:
                                referrals_needed = 0
                            
                            # Format subscription period
                            subscription_period = f"{months} месяц" + ("а" if months in [2, 3, 4] else ("ев" if months > 4 else ""))
                            
                            # Send notification
                            notification_sent = await send_referral_cashback_notification(
                                bot=callback.bot,
                                referrer_id=referrer_id,
                                referred_id=telegram_id,
                                purchase_amount=payment_amount_rubles,
                                cashback_amount=cashback_amount,
                                cashback_percent=cashback_percent,
                                paid_referrals_count=paid_referrals_count,
                                referrals_needed=referrals_needed,
                                action_type="purchase",
                                subscription_period=subscription_period
                            )
                            if notification_sent:
                                logger.info(f"REFERRAL_NOTIFICATION_SENT [admin_approve, referrer={referrer_id}, referred={telegram_id}, payment_id={payment_id}]")
                            else:
                                logger.warning(
                                    "NOTIFICATION_FAILED",
                                    extra={
                                        "type": "admin_approve_referral",
                                        "referrer": referrer_id,
                                        "referred": telegram_id,
                                        "payment_id": payment_id,
                                        "error": "send_referral_cashback_notification returned False"
                                    }
                                )
            except Exception as e:
                logger.warning(
                    "NOTIFICATION_FAILED",
                    extra={
                        "type": "admin_approve_referral",
                        "payment_id": payment_id,
                        "referrer": referrer_id if 'referrer_id' in locals() else None,
                        "referred": telegram_id if 'telegram_id' in locals() else None,
                        "error": str(e)
                    }
                )
        
        # Логируем продление, если было
        if is_renewal:
            logging.info(f"Subscription renewed for user {telegram_id}, payment_id={payment_id}, expires_at={expires_at}")
        else:
            logging.info(f"New subscription created for user {telegram_id}, payment_id={payment_id}, expires_at={expires_at}")
        
        # Уведомляем пользователя
        language = await resolve_user_language(telegram_id)
        
        expires_str = expires_at.strftime("%d.%m.%Y")
        # Отправляем сообщение об успешной активации (без ключа)
        text = i18n_get_text(language, "payment.approved", date=expires_str)
        
        try:
            await callback.bot.send_message(
                telegram_id, 
                text, 
                reply_markup=get_vpn_key_keyboard(language),
                parse_mode="HTML"
            )
            
            # Отправляем VPN-ключ отдельным сообщением (позволяет одно нажатие для копирования)
            await callback.bot.send_message(
                telegram_id,
                f"<code>{vpn_key}</code>",
                parse_mode="HTML"
            )
            
            logging.info(f"Approval message and VPN key sent to user {telegram_id} for payment {payment_id}")
        except Exception as e:
            logging.error(f"Error sending approval message to user {telegram_id}: {e}")
        
        await safe_edit_text(callback.message, f"✅ Платеж {payment_id} подтвержден")
        # Удаляем inline-кнопки после обработки
        await safe_edit_reply_markup(callback.message, reply_markup=None)
        
    except Exception as e:
        logging.exception(f"Error in approve_payment callback for payment_id={payment_id if 'payment_id' in locals() else 'unknown'}")
        await callback.answer("Ошибка. Проверь логи.", show_alert=True)


@router.message(Command("admin"))
async def cmd_admin(message: Message):
    """Административный дашборд"""
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        logging.warning(f"Unauthorized admin dashboard attempt by user {message.from_user.id}")
        language = await resolve_user_language(message.from_user.id)
        await message.answer(i18n_get_text(language, "admin.access_denied"))
        return
    
    language = await resolve_user_language(message.from_user.id)
    text = i18n_get_text(language, "admin.dashboard_title")
    await message.answer(text, reply_markup=get_admin_dashboard_keyboard(language))


@router.message(Command("pending_activations"))
async def cmd_pending_activations(message: Message):
    """Показать подписки с отложенной активацией (только для админа)"""
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        logging.warning(f"Unauthorized pending_activations attempt by user {message.from_user.id}")
        language = await resolve_user_language(message.from_user.id)
        await message.answer(i18n_get_text(language, "admin.access_denied"))
        return
    
    if not database.DB_READY:
        await message.answer("❌ База данных недоступна")
        return
    
    try:
        pool = await database.get_pool()
        if pool is None:
            await message.answer("❌ Не удалось подключиться к базе данных")
            return
        
        async with pool.acquire() as conn:
            # Получаем общее количество pending подписок
            total_count = await conn.fetchval(
                "SELECT COUNT(*) FROM subscriptions WHERE activation_status = 'pending'"
            ) or 0
            
            # Получаем топ-5 старейших pending подписок
            oldest_pending = await conn.fetch(
                """SELECT id, telegram_id, activation_attempts, last_activation_error, activated_at
                   FROM subscriptions
                   WHERE activation_status = 'pending'
                   ORDER BY COALESCE(activated_at, '1970-01-01'::timestamp) ASC
                   LIMIT 5"""
            )
            
            # Формируем сообщение
            text_lines = [
                "⏳ **ОТЛОЖЕННЫЕ АКТИВАЦИИ VPN**\n",
                f"Всего pending подписок: **{total_count}**\n"
            ]
            
            if total_count == 0:
                text_lines.append("✅ Нет подписок с отложенной активацией")
            else:
                if oldest_pending:
                    text_lines.append("\n**Топ-5 старейших:**\n")
                    for idx, sub_row in enumerate(oldest_pending, 1):
                        subscription_id = sub_row["id"]
                        telegram_id = sub_row["telegram_id"]
                        attempts = sub_row["activation_attempts"]
                        error = sub_row.get("last_activation_error") or "N/A"
                        pending_since = sub_row.get("activated_at")
                        
                        if pending_since:
                            if isinstance(pending_since, str):
                                pending_since = datetime.fromisoformat(pending_since)
                            pending_since_str = pending_since.strftime("%d.%m.%Y %H:%M")
                        else:
                            pending_since_str = "N/A"
                        
                        error_preview = error[:50] + "..." if error and len(error) > 50 else error
                        
                        text_lines.append(
                            f"{idx}. ID: `{subscription_id}` | "
                            f"User: `{telegram_id}`\n"
                            f"   Попыток: {attempts} | "
                            f"С: {pending_since_str}\n"
                            f"   Ошибка: `{error_preview}`\n"
                        )
                else:
                    text_lines.append("\nНет данных о старейших подписках")
            
            text = "\n".join(text_lines)
            await message.answer(text, parse_mode="Markdown")
            
    except Exception as e:
        logger.exception(f"Error in cmd_pending_activations: {e}")
        language = await resolve_user_language(message.from_user.id)
        await message.answer(i18n_get_text(language, "errors.data_fetch", error=str(e)[:100], default=f"❌ Ошибка при получении данных: {str(e)[:100]}"))


@router.callback_query(F.data == "admin:dashboard")
async def callback_admin_dashboard(callback: CallbackQuery):
    """
    2. ADMIN DASHBOARD UI (TELEGRAM)
    
    Display real-time system health with severity indicator.
    """
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    try:
        from app.core.system_health import evaluate_system_health, get_error_summary_compact
        
        # Get system health report
        health_report = await evaluate_system_health()
        error_summary = await get_error_summary_compact()
        
        # Build dashboard text
        text = f"📊 Admin Dashboard\n\n"
        text += health_report.summary
        text += "\n\n"
        
        # Add error summary if any
        if error_summary:
            text += "⚠️ ACTIVE ISSUES:\n\n"
            for i, error in enumerate(error_summary[:5], 1):  # Limit to 5 issues
                text += f"{i}. {error['component'].upper()}: {error['reason']}\n"
                text += f"   → {error['impact']}\n\n"
        
        # Add refresh button
        user = await database.get_user(callback.from_user.id)
        language = await resolve_user_language(callback.from_user.id)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.refresh"), callback_data="admin:dashboard")],
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.test_menu"), callback_data="admin:test_menu")],
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:main")],
        ])
        
        await safe_edit_text(callback.message, text, reply_markup=keyboard)
        await callback.answer()
        
        # Audit log
        await database._log_audit_event_atomic_standalone(
            "admin_dashboard_viewed",
            callback.from_user.id,
            None,
            f"Admin viewed dashboard: severity={health_report.level.value}, issues={len(error_summary)}"
        )
        
    except Exception as e:
        logger.exception(f"Error in callback_admin_dashboard: {e}")
        user = await database.get_user(callback.from_user.id)
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "errors.dashboard_data"), show_alert=True)


@router.callback_query(F.data == "admin:main")
async def callback_admin_main(callback: CallbackQuery):
    """Главный экран админ-дашборда"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    user = await database.get_user(callback.from_user.id)
    language = await resolve_user_language(callback.from_user.id)
    text = i18n_get_text(language, "admin.dashboard_title")
    await safe_edit_text(callback.message, text, reply_markup=get_admin_dashboard_keyboard(language))
    await callback.answer()


@router.callback_query(F.data == "admin_promo_stats")
async def callback_admin_promo_stats(callback: CallbackQuery):
    """Обработчик кнопки статистики промокодов в админ-дашборде"""
    user = await database.get_user(callback.from_user.id)
    language = await resolve_user_language(callback.from_user.id)
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    try:
        # Получаем статистику промокодов
        stats = await database.get_promo_stats()
        
        # Формируем текст ответа
        text = await format_promo_stats_text(stats)
        
        await safe_edit_text(callback.message, text, reply_markup=get_admin_back_keyboard(language))
        await callback.answer()
    except Exception as e:
        logger.error(f"Error getting promo stats: {e}")
        user = await database.get_user(callback.from_user.id)
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "errors.promo_stats"), show_alert=True)


@router.callback_query(F.data == "admin:metrics")
async def callback_admin_metrics(callback: CallbackQuery):
    """Раздел Метрики"""
    user = await database.get_user(callback.from_user.id)
    language = await resolve_user_language(callback.from_user.id)
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    try:
        metrics = await database.get_business_metrics()
        
        text = "📈 Бизнес-метрики\n\n"
        
        # Среднее время подтверждения оплаты
        approval_time = metrics.get('avg_payment_approval_time_seconds')
        if approval_time:
            minutes = int(approval_time / 60)
            seconds = int(approval_time % 60)
            text += f"⏱ Среднее время подтверждения оплаты: {minutes} мин {seconds} сек\n"
        else:
            text += "⏱ Среднее время подтверждения оплаты: нет данных\n"
        
        # Среднее время жизни подписки
        lifetime = metrics.get('avg_subscription_lifetime_days')
        if lifetime:
            text += f"📅 Среднее время жизни подписки: {lifetime:.1f} дней\n"
        else:
            text += "📅 Среднее время жизни подписки: нет данных\n"
        
        # Количество продлений на пользователя
        renewals = metrics.get('avg_renewals_per_user', 0.0)
        text += f"🔄 Среднее количество продлений на пользователя: {renewals:.2f}\n"
        
        # Процент подтвержденных платежей
        approval_rate = metrics.get('approval_rate_percent', 0.0)
        text += f"✅ Процент подтвержденных платежей: {approval_rate:.1f}%\n"
        
        await safe_edit_text(callback.message, text, reply_markup=get_admin_back_keyboard(language))
        await callback.answer()
        
        # Логируем действие
        await database._log_audit_event_atomic_standalone("admin_view_metrics", callback.from_user.id, None, "Admin viewed business metrics")
        
    except Exception as e:
        logging.exception(f"Error in callback_admin_metrics: {e}")
        user = await database.get_user(callback.from_user.id)
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "errors.metrics"), show_alert=True)


@router.callback_query(F.data == "admin:stats")
async def callback_admin_stats(callback: CallbackQuery):
    """Раздел Статистика"""
    user = await database.get_user(callback.from_user.id)
    language = await resolve_user_language(callback.from_user.id)
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    try:
        stats = await database.get_admin_stats()
        
        text = "📊 Статистика\n\n"
        text += f"👥 Всего пользователей: {stats['total_users']}\n"
        text += f"🔑 Активных подписок: {stats['active_subscriptions']}\n"
        text += f"⛔ Истёкших подписок: {stats['expired_subscriptions']}\n"
        text += f"💳 Всего платежей: {stats['total_payments']}\n"
        text += f"✅ Подтверждённых платежей: {stats['approved_payments']}\n"
        text += f"❌ Отклонённых платежей: {stats['rejected_payments']}\n"
        text += f"🔓 Свободных VPN-ключей: {stats['free_vpn_keys']}"
        
        await safe_edit_text(callback.message, text, reply_markup=get_admin_back_keyboard(language))
        await callback.answer()
        
        # Логируем просмотр статистики
        await database._log_audit_event_atomic_standalone("admin_view_stats", callback.from_user.id, None, "Admin viewed statistics")
        
    except Exception as e:
        logging.exception(f"Error in callback_admin_stats: {e}")
        user = await database.get_user(callback.from_user.id)
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "errors.stats"), show_alert=True)


@router.callback_query(F.data == "admin:referral_stats")
async def callback_admin_referral_stats(callback: CallbackQuery):
    """Реферальная статистика - главный экран с общей статистикой"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    user = await database.get_user(callback.from_user.id)
    language = await resolve_user_language(callback.from_user.id)
    await callback.answer()
    
    try:
        # Получаем общую статистику
        overall_stats = await database.get_referral_overall_stats()
        
        # Получаем топ рефереров (первые 10, отсортированные по доходу)
        top_referrers = await database.get_admin_referral_stats(
            search_query=None,
            sort_by="total_revenue",
            sort_order="DESC",
            limit=10,
            offset=0
        )
        
        # Безопасная обработка статистики с дефолтами
        if not overall_stats:
            overall_stats = {
                "total_referrers": 0,
                "total_referrals": 0,
                "total_paid_referrals": 0,
                "total_revenue": 0.0,
                "total_cashback_paid": 0.0,
                "avg_cashback_per_referrer": 0.0
            }
        
        # Безопасное извлечение значений с дефолтами
        total_referrers = database.safe_int(overall_stats.get("total_referrers", 0))
        total_referrals = database.safe_int(overall_stats.get("total_referrals", 0))
        total_paid_referrals = database.safe_int(overall_stats.get("total_paid_referrals", 0))
        total_revenue = database.safe_float(overall_stats.get("total_revenue", 0.0))
        total_cashback_paid = database.safe_float(overall_stats.get("total_cashback_paid", 0.0))
        avg_cashback_per_referrer = database.safe_float(overall_stats.get("avg_cashback_per_referrer", 0.0))
        
        # Формируем текст с общей статистикой
        text = "📈 Реферальная статистика\n\n"
        text += "📊 Общая статистика:\n"
        text += f"• Всего рефереров: {total_referrers}\n"
        text += f"• Всего приглашённых: {total_referrals}\n"
        text += f"• Всего оплат: {total_paid_referrals}\n"
        text += f"• Общий доход: {total_revenue:.2f} ₽\n"
        text += f"• Выплачено кешбэка: {total_cashback_paid:.2f} ₽\n"
        text += f"• Средний кешбэк на реферера: {avg_cashback_per_referrer:.2f} ₽\n\n"
        
        # Топ рефереров (безопасная обработка)
        if top_referrers:
            text += "🏆 Топ рефереров:\n\n"
            for idx, stat in enumerate(top_referrers[:10], 1):
                try:
                    # Безопасное извлечение значений
                    referrer_id = stat.get("referrer_id", "N/A")
                    username = stat.get("username") or f"ID{referrer_id}"
                    invited_count = database.safe_int(stat.get("invited_count", 0))
                    paid_count = database.safe_int(stat.get("paid_count", 0))
                    conversion = database.safe_float(stat.get("conversion_percent", 0.0))
                    revenue = database.safe_float(stat.get("total_invited_revenue", 0.0))
                    cashback = database.safe_float(stat.get("total_cashback_paid", 0.0))
                    cashback_percent = database.safe_int(stat.get("current_cashback_percent", 10))
                    
                    text += f"{idx}. @{username} (ID: {referrer_id})\n"
                    text += f"   Оплативших: {paid_count} | Уровень: {cashback_percent}%\n"
                    text += f"   Доход: {revenue:.2f} ₽ | Кешбэк: {cashback:.2f} ₽\n\n"
                except Exception as e:
                    logger.warning(f"Error processing referrer stat in admin dashboard: {e}, stat={stat}")
                    continue  # Пропускаем проблемную строку
        else:
            text += "🏆 Топ рефереров:\nРефереры не найдены.\n\n"
        
        # Клавиатура с кнопками
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text=i18n_get_text(language, "admin.referral_history"), callback_data="admin:referral_history"),
                InlineKeyboardButton(text=i18n_get_text(language, "admin.referral_top"), callback_data="admin:referral_top")
            ],
            [
                InlineKeyboardButton(text=i18n_get_text(language, "admin.sort_by_revenue"), callback_data="admin:referral_sort:total_revenue"),
                InlineKeyboardButton(text=i18n_get_text(language, "admin.sort_by_invited"), callback_data="admin:referral_sort:invited_count")
            ],
            [
                InlineKeyboardButton(text=i18n_get_text(language, "admin.sort_by_cashback"), callback_data="admin:referral_sort:cashback_paid"),
                InlineKeyboardButton(text=i18n_get_text(language, "admin.search"), callback_data="admin:referral_search")
            ],
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:main")]
        ])
        
        await safe_edit_text(callback.message, text, reply_markup=keyboard)
        
        # Логируем просмотр статистики
        try:
            await database._log_audit_event_atomic_standalone(
                "admin_view_referral_stats", 
                callback.from_user.id, 
                None, 
                f"Admin viewed referral stats: {total_referrers} referrers"
            )
        except Exception as log_error:
            logger.warning(f"Error logging admin referral stats view: {log_error}")
        
    except Exception as e:
        # Структурированное логирование для разработчиков
        logger.exception(
            f"admin_referral_stats_failed: telegram_id={callback.from_user.id}, handler=callback_admin_referral_stats, error={type(e).__name__}: {e}"
        )
        
        # Graceful fallback: показываем пустую статистику, а не ошибку
        try:
            fallback_text = (
                "📈 Реферальная статистика\n\n"
                "📊 Общая статистика:\n"
                "• Всего рефереров: 0\n"
                "• Всего приглашённых: 0\n"
                "• Всего оплат: 0\n"
                "• Общий доход: 0.00 ₽\n"
                "• Выплачено кешбэка: 0.00 ₽\n"
                "• Средний кешбэк на реферера: 0.00 ₽\n\n"
                "🏆 Топ рефереров:\nРефереры не найдены.\n\n"
            )
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text=i18n_get_text(language, "admin.referral_history"), callback_data="admin:referral_history"),
                    InlineKeyboardButton(text=i18n_get_text(language, "admin.referral_top"), callback_data="admin:referral_top")
                ],
                [
                    InlineKeyboardButton(text=i18n_get_text(language, "admin.sort_by_revenue"), callback_data="admin:referral_sort:total_revenue"),
                    InlineKeyboardButton(text=i18n_get_text(language, "admin.sort_by_invited"), callback_data="admin:referral_sort:invited_count")
                ],
                [
                    InlineKeyboardButton(text=i18n_get_text(language, "admin.sort_by_cashback"), callback_data="admin:referral_sort:cashback_paid"),
                    InlineKeyboardButton(text=i18n_get_text(language, "admin.search"), callback_data="admin:referral_search")
                ],
                [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:main")]
            ])
            
            await safe_edit_text(callback.message, fallback_text, reply_markup=keyboard)
        except Exception as fallback_error:
            logger.exception(f"Error in fallback admin referral stats: {fallback_error}")
            user = await database.get_user(callback.from_user.id)
            language = await resolve_user_language(callback.from_user.id)
            await callback.answer(i18n_get_text(language, "errors.referral_stats"), show_alert=True)


@router.callback_query(F.data.startswith("admin:referral_sort:"))
async def callback_admin_referral_sort(callback: CallbackQuery):
    """Сортировка реферальной статистики"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    user = await database.get_user(callback.from_user.id)
    language = await resolve_user_language(callback.from_user.id)
    await callback.answer()
    
    try:
        # Извлекаем параметр сортировки
        sort_by = callback.data.split(":")[-1]
        
        # Получаем статистику с новой сортировкой
        stats_list = await database.get_admin_referral_stats(
            search_query=None,
            sort_by=sort_by,
            sort_order="DESC",
            limit=20,
            offset=0
        )
        
        if not stats_list:
            text = "📊 Реферальная статистика\n\nРефереры не найдены."
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:main")]
            ])
            await safe_edit_text(callback.message, text, reply_markup=keyboard)
            return
        
        # Формируем текст со статистикой
        sort_labels = {
            "total_revenue": "По доходу",
            "invited_count": "По приглашениям",
            "cashback_paid": "По кешбэку"
        }
        sort_label = sort_labels.get(sort_by, "По доходу")
        
        text = f"📊 Реферальная статистика\nСортировка: {sort_label}\n\n"
        text += f"Всего рефереров: {len(stats_list)}\n\n"
        
        # Показываем топ-10 рефереров
        for idx, stat in enumerate(stats_list[:10], 1):
            # Safe extraction: use .get() to avoid KeyError
            username = stat.get("username") or f"ID{stat.get('referrer_id', 'N/A')}"
            invited_count = stat.get("invited_count", 0)
            paid_count = stat.get("paid_count", 0)
            conversion = stat.get("conversion_percent", 0.0)
            revenue = stat.get("total_invited_revenue", 0.0)
            cashback = stat.get("total_cashback_paid", 0.0)
            cashback_percent = stat.get("current_cashback_percent", 0.0)
            referrer_id = stat.get("referrer_id", "N/A")
            
            text += f"{idx}. @{username} (ID: {referrer_id})\n"
            text += f"   Приглашено: {invited_count} | Оплатили: {paid_count} ({conversion}%)\n"
            text += f"   Доход: {revenue:.2f} ₽ | Кешбэк: {cashback:.2f} ₽ ({cashback_percent}%)\n\n"
        
        if len(stats_list) > 10:
            text += f"... и еще {len(stats_list) - 10} рефереров\n\n"
        
        # Клавиатура с кнопками фильтров и сортировки
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text=i18n_get_text(language, "admin.sort_by_revenue"), callback_data="admin:referral_sort:total_revenue"),
                InlineKeyboardButton(text=i18n_get_text(language, "admin.sort_by_invited"), callback_data="admin:referral_sort:invited_count")
            ],
            [
                InlineKeyboardButton(text=i18n_get_text(language, "admin.sort_by_cashback"), callback_data="admin:referral_sort:cashback_paid"),
                InlineKeyboardButton(text=i18n_get_text(language, "admin.search"), callback_data="admin:referral_search")
            ],
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:main")]
        ])
        
        await safe_edit_text(callback.message, text, reply_markup=keyboard)
        
    except Exception as e:
        logging.exception(f"Error in callback_admin_referral_sort: {e}")
        user = await database.get_user(callback.from_user.id)
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "errors.stats_sort"), show_alert=True)


@router.callback_query(F.data == "admin:referral_search")
async def callback_admin_referral_search(callback: CallbackQuery, state: FSMContext):
    """Поиск реферальной статистики"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    user = await database.get_user(callback.from_user.id)
    language = await resolve_user_language(callback.from_user.id)
    await callback.answer()
    
    text = "🔍 Поиск реферальной статистики\n\nВведите telegram_id или username для поиска:"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.cancel"), callback_data="admin:referral_stats")]
    ])
    
    await safe_edit_text(callback.message, text, reply_markup=keyboard)
    await state.set_state(AdminReferralSearch.waiting_for_search_query)


@router.message(AdminReferralSearch.waiting_for_search_query)
async def process_admin_referral_search(message: Message, state: FSMContext):
    """Обработка поискового запроса"""
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(message.from_user.id)
        await message.answer(i18n_get_text(language, "admin.access_denied"))
        await state.clear()
        return
    
    language = await resolve_user_language(message.from_user.id)
    search_query = message.text.strip()
    await state.clear()
    
    try:
        # Получаем статистику с поисковым запросом
        stats_list = await database.get_admin_referral_stats(
            search_query=search_query,
            sort_by="total_revenue",
            sort_order="DESC",
            limit=20,
            offset=0
        )
        
        if not stats_list:
            text = f"📊 Реферальная статистика\n\nПо запросу '{search_query}' ничего не найдено."
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:referral_stats")]
            ])
            await message.answer(text, reply_markup=keyboard)
            return
        
        # Формируем текст со статистикой
        text = f"📊 Реферальная статистика\nПоиск: '{search_query}'\n\n"
        text += f"Найдено рефереров: {len(stats_list)}\n\n"
        
        # Показываем результаты поиска
        for idx, stat in enumerate(stats_list[:10], 1):
            # Safe extraction: use .get() to avoid KeyError
            username = stat.get("username") or f"ID{stat.get('referrer_id', 'N/A')}"
            invited_count = stat.get("invited_count", 0)
            paid_count = stat.get("paid_count", 0)
            conversion = stat.get("conversion_percent", 0.0)
            revenue = stat.get("total_invited_revenue", 0.0)
            cashback = stat.get("total_cashback_paid", 0.0)
            cashback_percent = stat.get("current_cashback_percent", 0.0)
            referrer_id = stat.get("referrer_id", "N/A")
            
            text += f"{idx}. @{username} (ID: {referrer_id})\n"
            text += f"   Приглашено: {invited_count} | Оплатили: {paid_count} ({conversion}%)\n"
            text += f"   Доход: {revenue:.2f} ₽ | Кешбэк: {cashback:.2f} ₽ ({cashback_percent}%)\n\n"
        
        if len(stats_list) > 10:
            text += f"... и еще {len(stats_list) - 10} рефереров\n\n"
        
        # Клавиатура
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text=i18n_get_text(language, "admin.sort_by_revenue"), callback_data="admin:referral_sort:total_revenue"),
                InlineKeyboardButton(text=i18n_get_text(language, "admin.sort_by_invited"), callback_data="admin:referral_sort:invited_count")
            ],
            [
                InlineKeyboardButton(text=i18n_get_text(language, "admin.sort_by_cashback"), callback_data="admin:referral_sort:cashback_paid"),
                InlineKeyboardButton(text=i18n_get_text(language, "admin.search"), callback_data="admin:referral_search")
            ],
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:main")]
        ])
        
        await message.answer(text, reply_markup=keyboard)
        
    except Exception as e:
        logging.exception(f"Error in process_admin_referral_search: {e}")
        language = await resolve_user_language(message.from_user.id)
        await message.answer(i18n_get_text(language, "errors.stats_search"))


@router.callback_query(F.data.startswith("admin:referral_detail:"))
async def callback_admin_referral_detail(callback: CallbackQuery):
    """Детальная информация по рефереру"""
    user = await database.get_user(callback.from_user.id)
    language = await resolve_user_language(callback.from_user.id)
    
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    await callback.answer()
    
    try:
        # Извлекаем referrer_id
        referrer_id = int(callback.data.split(":")[-1])
        
        # Получаем детальную информацию
        detail = await database.get_admin_referral_detail(referrer_id)
        
        if not detail:
            await callback.answer("Реферер не найден", show_alert=True)
            return
        
        # Формируем текст с детальной информацией
        username = detail["username"]
        invited_list = detail["invited_list"]
        
        text = f"📊 Детали реферера\n\n"
        text += f"@{username} (ID: {referrer_id})\n\n"
        text += f"Всего приглашено: {len(invited_list)}\n\n"
        
        if invited_list:
            text += "Приглашённые пользователи:\n\n"
            for idx, invited in enumerate(invited_list[:15], 1):  # Ограничение 15 записей для читаемости
                invited_username = invited["username"]
                registered_at = invited["registered_at"]
                first_payment = invited["first_payment_date"]
                purchase_amount = invited["purchase_amount"]
                cashback_amount = invited["cashback_amount"]
                
                text += f"{idx}. @{invited_username} (ID: {invited['invited_user_id']})\n"
                text += f"   Зарегистрирован: {registered_at.strftime('%Y-%m-%d') if registered_at else 'N/A'}\n"
                if first_payment:
                    text += f"   Первая оплата: {first_payment.strftime('%Y-%m-%d')}\n"
                    text += f"   Сумма: {purchase_amount:.2f} ₽ | Кешбэк: {cashback_amount:.2f} ₽\n"
                else:
                    text += f"   Оплаты нет\n"
                text += "\n"
            
            if len(invited_list) > 15:
                text += f"... и еще {len(invited_list) - 15} пользователей\n\n"
        else:
            text += "Приглашённые пользователи отсутствуют.\n\n"
        
        # Клавиатура
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.back_to_stats"), callback_data="admin:referral_stats")]
        ])
        
        await safe_edit_text(callback.message, text, reply_markup=keyboard)
        
        # Логируем просмотр деталей
        await database._log_audit_event_atomic_standalone(
            "admin_view_referral_detail", 
            callback.from_user.id, 
            referrer_id, 
            f"Admin viewed referral detail for referrer_id={referrer_id}"
        )
        
    except Exception as e:
        logging.exception(f"Error in callback_admin_referral_detail: {e}")
        user = await database.get_user(callback.from_user.id)
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "errors.details"), show_alert=True)


@router.callback_query(F.data == "admin:referral_history")
async def callback_admin_referral_history(callback: CallbackQuery):
    """История начислений реферального кешбэка"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    await callback.answer()
    
    try:
        # Получаем историю начислений (первые 20 записей)
        history = await database.get_referral_rewards_history(
            date_from=None,
            date_to=None,
            limit=20,
            offset=0
        )
        
        # Получаем общее количество для пагинации
        total_count = await database.get_referral_rewards_history_count()
        
        if not history:
            text = "📋 История начислений\n\nНачисления не найдены."
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:referral_stats")]
            ])
            await safe_edit_text(callback.message, text, reply_markup=keyboard)
            return
        
        # Формируем текст с историей
        text = "📋 История начислений\n\n"
        text += f"Всего записей: {total_count}\n\n"
        
        for idx, reward in enumerate(history[:20], 1):
            referrer = reward["referrer_username"]
            buyer = reward["buyer_username"]
            purchase_amount = reward["purchase_amount"]
            percent = reward["percent"]
            reward_amount = reward["reward_amount"]
            created_at = reward["created_at"].strftime("%d.%m.%Y %H:%M") if reward["created_at"] else "N/A"
            
            text += f"{idx}. {created_at}\n"
            text += f"   Реферер: @{referrer} (ID: {reward['referrer_id']})\n"
            text += f"   Покупатель: @{buyer} (ID: {reward['buyer_id']})\n"
            text += f"   Покупка: {purchase_amount:.2f} ₽ | Кешбэк: {percent}% = {reward_amount:.2f} ₽\n\n"
        
        if total_count > 20:
            text += f"... и еще {total_count - 20} записей\n\n"
        
        # Клавиатура
        keyboard_buttons = []
        if total_count > 20:
            keyboard_buttons.append([
                InlineKeyboardButton(text=i18n_get_text(language, "admin.next_page"), callback_data="admin:referral_history:page:1")
            ])
        keyboard_buttons.append([
            InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:referral_stats")
        ])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        await safe_edit_text(callback.message, text, reply_markup=keyboard)
        
        # Логируем просмотр истории
        await database._log_audit_event_atomic_standalone(
            "admin_view_referral_history",
            callback.from_user.id,
            None,
            f"Admin viewed referral history: {len(history)} records"
        )
        
    except Exception as e:
        logging.exception(f"Error in callback_admin_referral_history: {e}")
        user = await database.get_user(callback.from_user.id)
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "errors.rewards_history"), show_alert=True)


@router.callback_query(F.data.startswith("admin:referral_history:page:"))
async def callback_admin_referral_history_page(callback: CallbackQuery):
    """Пагинация истории начислений"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    await callback.answer()
    
    try:
        # Извлекаем номер страницы
        page = int(callback.data.split(":")[-1])
        limit = 20
        offset = page * limit
        
        # Получаем историю начислений
        history = await database.get_referral_rewards_history(
            date_from=None,
            date_to=None,
            limit=limit,
            offset=offset
        )
        
        # Получаем общее количество
        total_count = await database.get_referral_rewards_history_count()
        total_pages = (total_count + limit - 1) // limit
        
        if not history:
            text = "📋 История начислений\n\nНачисления не найдены."
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:referral_stats")]
            ])
            await safe_edit_text(callback.message, text, reply_markup=keyboard)
            return
        
        # Формируем текст
        text = f"📋 История начислений (стр. {page + 1}/{total_pages})\n\n"
        text += f"Всего записей: {total_count}\n\n"
        
        for idx, reward in enumerate(history, 1):
            referrer = reward["referrer_username"]
            buyer = reward["buyer_username"]
            purchase_amount = reward["purchase_amount"]
            percent = reward["percent"]
            reward_amount = reward["reward_amount"]
            created_at = reward["created_at"].strftime("%d.%m.%Y %H:%M") if reward["created_at"] else "N/A"
            
            text += f"{offset + idx}. {created_at}\n"
            text += f"   Реферер: @{referrer} (ID: {reward['referrer_id']})\n"
            text += f"   Покупатель: @{buyer} (ID: {reward['buyer_id']})\n"
            text += f"   Покупка: {purchase_amount:.2f} ₽ | Кешбэк: {percent}% = {reward_amount:.2f} ₽\n\n"
        
        # Клавиатура с пагинацией
        keyboard_buttons = []
        nav_buttons = []
        if page > 0:
            nav_buttons.append(InlineKeyboardButton(text=i18n_get_text(language, "admin.prev"), callback_data=f"admin:referral_history:page:{page - 1}"))
        if offset + limit < total_count:
            nav_buttons.append(InlineKeyboardButton(text=i18n_get_text(language, "admin.forward"), callback_data=f"admin:referral_history:page:{page + 1}"))
        if nav_buttons:
            keyboard_buttons.append(nav_buttons)
        keyboard_buttons.append([
            InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:referral_stats")
        ])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        await safe_edit_text(callback.message, text, reply_markup=keyboard)
        
    except Exception as e:
        logging.exception(f"Error in callback_admin_referral_history_page: {e}")
        user = await database.get_user(callback.from_user.id)
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "errors.rewards_history"), show_alert=True)


@router.callback_query(F.data == "admin:referral_top")
async def callback_admin_referral_top(callback: CallbackQuery):
    """Топ рефереров - расширенный список"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    await callback.answer()
    
    try:
        # Получаем топ рефереров (50 лучших)
        top_referrers = await database.get_admin_referral_stats(
            search_query=None,
            sort_by="total_revenue",
            sort_order="DESC",
            limit=50,
            offset=0
        )
        
        if not top_referrers:
            text = "🏆 Топ рефереров\n\nРефереры не найдены."
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:referral_stats")]
            ])
            await safe_edit_text(callback.message, text, reply_markup=keyboard)
            return
        
        # Формируем текст
        text = "🏆 Топ рефереров\n\n"
        
        for idx, stat in enumerate(top_referrers, 1):
            # Safe extraction: use .get() to avoid KeyError
            username = stat.get("username") or f"ID{stat.get('referrer_id', 'N/A')}"
            invited_count = stat.get("invited_count", 0)
            paid_count = stat.get("paid_count", 0)
            conversion = stat.get("conversion_percent", 0.0)
            revenue = stat.get("total_invited_revenue", 0.0)
            cashback = stat.get("total_cashback_paid", 0.0)
            cashback_percent = stat.get("current_cashback_percent", 0.0)
            referrer_id = stat.get("referrer_id", "N/A")
            
            text += f"{idx}. @{username} (ID: {referrer_id})\n"
            text += f"   Приглашено: {invited_count} | Оплатили: {paid_count} ({conversion}%)\n"
            text += f"   Доход: {revenue:.2f} ₽ | Кешбэк: {cashback:.2f} ₽ ({cashback_percent}%)\n\n"
        
        # Клавиатура
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text=i18n_get_text(language, "admin.sort_by_revenue"), callback_data="admin:referral_sort:total_revenue"),
                InlineKeyboardButton(text=i18n_get_text(language, "admin.sort_by_invited"), callback_data="admin:referral_sort:invited_count")
            ],
            [
                InlineKeyboardButton(text=i18n_get_text(language, "admin.sort_by_cashback"), callback_data="admin:referral_sort:cashback_paid"),
                InlineKeyboardButton(text=i18n_get_text(language, "admin.search"), callback_data="admin:referral_search")
            ],
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:referral_stats")]
        ])
        
        await safe_edit_text(callback.message, text, reply_markup=keyboard)
        
        # Логируем просмотр топа
        await database._log_audit_event_atomic_standalone(
            "admin_view_referral_top",
            callback.from_user.id,
            None,
            f"Admin viewed top referrers: {len(top_referrers)} referrers"
        )
        
    except Exception as e:
        logging.exception(f"Error in callback_admin_referral_top: {e}")
        user = await database.get_user(callback.from_user.id)
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "errors.top_referrers"), show_alert=True)


@router.callback_query(F.data == "admin:analytics")
async def callback_admin_analytics(callback: CallbackQuery):
    """📊 Финансовая аналитика - базовые метрики"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    try:
        # Получаем базовые метрики (оптимизированные запросы)
        total_revenue = await database.get_total_revenue()
        paying_users_count = await database.get_paying_users_count()
        arpu = await database.get_arpu()
        avg_ltv = await database.get_ltv()
        
        # Формируем отчет (краткий и понятный)
        text = (
            f"📊 Финансовая аналитика\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 Общий доход\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"   {total_revenue:,.2f} ₽\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"👥 Платящие пользователи\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"   {paying_users_count} чел.\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📈 ARPU (Average Revenue Per User)\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"   {arpu:,.2f} ₽\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💎 Средний LTV (Lifetime Value)\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"   {avg_ltv:,.2f} ₽\n"
        )
        
        # Клавиатура
        user = await database.get_user(callback.from_user.id)
        language = await resolve_user_language(callback.from_user.id)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.refresh"), callback_data="admin:analytics")],
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:main")]
        ])
        
        await safe_edit_text(callback.message, text, reply_markup=keyboard)
        await callback.answer()
        
        # Логируем действие
        await database._log_audit_event_atomic_standalone(
            "admin_view_analytics",
            callback.from_user.id,
            None,
            "Admin viewed financial analytics"
        )
        
    except Exception as e:
        logger.exception(f"Error in admin analytics: {e}")
        user = await database.get_user(callback.from_user.id)
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "errors.analytics"), show_alert=True)
        await callback.answer("Ошибка при расчете аналитики", show_alert=True)


@router.callback_query(F.data == "admin:analytics:monthly")
async def callback_admin_analytics_monthly(callback: CallbackQuery):
    """Ежемесячная сводка"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    try:
        now = datetime.now()
        current_month = await database.get_monthly_summary(now.year, now.month)
        
        # Предыдущий месяц
        if now.month == 1:
            prev_month = await database.get_monthly_summary(now.year - 1, 12)
        else:
            prev_month = await database.get_monthly_summary(now.year, now.month - 1)
        
        text = (
            f"📅 Ежемесячная сводка\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 Текущий месяц ({current_month['year']}-{current_month['month']:02d})\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"   Доход: {current_month['revenue']:.2f} ₽\n"
            f"   Платежей: {current_month['payments_count']}\n"
            f"   Новых пользователей: {current_month['new_users']}\n"
            f"   Новых подписок: {current_month['new_subscriptions']}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 Предыдущий месяц ({prev_month['year']}-{prev_month['month']:02d})\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"   Доход: {prev_month['revenue']:.2f} ₽\n"
            f"   Платежей: {prev_month['payments_count']}\n"
            f"   Новых пользователей: {prev_month['new_users']}\n"
            f"   Новых подписок: {prev_month['new_subscriptions']}\n\n"
        )
        
        # Сравнение
        revenue_change = current_month['revenue'] - prev_month['revenue']
        revenue_change_percent = (revenue_change / prev_month['revenue'] * 100) if prev_month['revenue'] > 0 else 0
        
        text += (
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📈 Изменение дохода\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"   Изменение: {revenue_change:+.2f} ₽ ({revenue_change_percent:+.1f}%)\n"
        )
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.back_to_analytics"), callback_data="admin:analytics")]
        ])
        
        await safe_edit_text(callback.message, text, reply_markup=keyboard)
        await callback.answer()
        
    except Exception as e:
        logger.exception(f"Error in monthly analytics: {e}")
        await callback.answer("Ошибка при получении ежемесячной сводки", show_alert=True)


@router.callback_query(F.data == "admin:audit")
async def callback_admin_audit(callback: CallbackQuery):
    """Раздел Аудит (переиспользование логики /admin_audit)"""
    user = await database.get_user(callback.from_user.id)
    language = await resolve_user_language(callback.from_user.id)
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    try:
        # Получаем последние 10 записей из audit_log
        audit_logs = await database.get_last_audit_logs(limit=10)
        
        if not audit_logs:
            text = i18n_get_text(language, "admin.audit_empty")
            await safe_edit_text(callback.message, text, reply_markup=get_admin_back_keyboard(language))
            await callback.answer()
            return
        
        # Формируем сообщение
        lines = ["📜 Аудит", ""]
        
        for log in audit_logs:
            # Форматируем дату и время
            created_at = log["created_at"]
            if isinstance(created_at, str):
                created_at = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
            elif isinstance(created_at, datetime):
                pass
            else:
                created_at = datetime.now()
            
            created_str = created_at.strftime("%Y-%m-%d %H:%M")
            
            lines.append(f"🕒 {created_str}")
            lines.append(f"Действие: {log['action']}")
            lines.append(f"Админ: {log['telegram_id']}")
            
            if log['target_user']:
                lines.append(f"Пользователь: {log['target_user']}")
            else:
                lines.append("Пользователь: —")
            
            if log['details']:
                details = log['details']
                if len(details) > 150:
                    details = details[:150] + "..."
                lines.append(f"Детали: {details}")
            else:
                lines.append("Детали: —")
            
            lines.append("")
            lines.append("⸻")
            lines.append("")
        
        # Убираем последний разделитель
        if lines[-1] == "" and lines[-2] == "⸻":
            lines = lines[:-2]
        
        text = "\n".join(lines)
        
        # Проверяем лимит Telegram (4096 символов на сообщение)
        if len(text) > 4000:
            # Уменьшаем до 5 записей
            audit_logs = await database.get_last_audit_logs(limit=5)
            lines = ["📜 Аудит", ""]
            
            for log in audit_logs:
                created_at = log["created_at"]
                if isinstance(created_at, str):
                    created_at = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                elif isinstance(created_at, datetime):
                    pass
                else:
                    created_at = datetime.now()
                
                created_str = created_at.strftime("%Y-%m-%d %H:%M")
                
                lines.append(f"🕒 {created_str}")
                lines.append(f"Действие: {log['action']}")
                lines.append(f"Админ: {log['telegram_id']}")
                
                if log['target_user']:
                    lines.append(f"Пользователь: {log['target_user']}")
                else:
                    lines.append("Пользователь: —")
                
                if log['details']:
                    details = log['details']
                    if len(details) > 100:
                        details = details[:100] + "..."
                    lines.append(f"Детали: {details}")
                else:
                    lines.append("Детали: —")
                
                lines.append("")
                lines.append("⸻")
                lines.append("")
            
            if lines[-1] == "" and lines[-2] == "⸻":
                lines = lines[:-2]
            
            text = "\n".join(lines)
        
        await safe_edit_text(callback.message, text, reply_markup=get_admin_back_keyboard(language))
        await callback.answer()
        
        # Логируем просмотр аудита
        await database._log_audit_event_atomic_standalone("admin_view_audit", callback.from_user.id, None, "Admin viewed audit log")
        
    except Exception as e:
        logging.exception(f"Error in callback_admin_audit: {e}")
        await callback.answer("Ошибка при получении audit log", show_alert=True)


@router.callback_query(F.data == "admin:keys")
async def callback_admin_keys(callback: CallbackQuery):
    """Раздел VPN-ключи в админ-дашборде"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    try:
        # Показываем меню управления ключами
        text = "🔑 Управление VPN-ключами\n\n"
        text += "Доступные действия:\n"
        text += "• Перевыпустить ключ для одного пользователя\n"
        text += "• Перевыпустить ключи для всех активных пользователей\n"
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.reissue_for_user"), callback_data="admin:user")],
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.reissue_all_keys"), callback_data="admin:keys:reissue_all")],
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:main")]
        ])
        
        await safe_edit_text(callback.message, text, reply_markup=keyboard)
        await callback.answer()
        
    except Exception as e:
        logging.exception(f"Error in callback_admin_keys: {e}")
        user = await database.get_user(callback.from_user.id)
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "errors.generic"), show_alert=True)


@router.callback_query(F.data == "admin:keys:reissue_all")
async def callback_admin_keys_reissue_all(callback: CallbackQuery, bot: Bot):
    """Массовый перевыпуск ключей для всех активных пользователей"""
    user = await database.get_user(callback.from_user.id)
    language = await resolve_user_language(callback.from_user.id)
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    await callback.answer("Начинаю массовый перевыпуск...")
    
    try:
        admin_telegram_id = callback.from_user.id
        
        # Получаем все активные подписки
        pool = await database.get_pool()
        async with pool.acquire() as conn:
            now = datetime.now()
            subscriptions = await conn.fetch(
                """SELECT telegram_id, uuid, vpn_key, expires_at 
                   FROM subscriptions 
                   WHERE status = 'active' 
                   AND expires_at > $1 
                   AND uuid IS NOT NULL
                   ORDER BY telegram_id""",
                now
            )
        
        total_count = len(subscriptions)
        success_count = 0
        failed_count = 0
        failed_users = []
        
        if total_count == 0:
            await safe_edit_text(
                callback.message,
                i18n_get_text(language, "admin.no_active_subscriptions_reissue"),
                reply_markup=get_admin_back_keyboard(language)
            )
            return
        
        # Отправляем начальное сообщение
        status_text = f"🔄 Массовый перевыпуск ключей\n\nВсего пользователей: {total_count}\nОбработано: 0/{total_count}\nУспешно: 0\nОшибок: 0"
        status_message = await callback.message.edit_text(status_text, reply_markup=None)
        # Примечание: status_message используется для динамического обновления, защита не нужна
        
        # Обрабатываем каждую подписку
        for idx, sub_row in enumerate(subscriptions, 1):
            subscription = dict(sub_row)
            telegram_id = subscription["telegram_id"]
            
            try:
                # Перевыпускаем ключ
                result = await database.reissue_vpn_key_atomic(telegram_id, admin_telegram_id)
                new_vpn_key, old_vpn_key = result
                
                if new_vpn_key is None:
                    failed_count += 1
                    failed_users.append(telegram_id)
                    logging.error(f"Failed to reissue key for user {telegram_id} in bulk operation")
                    continue
                
                success_count += 1
                
                # Отправляем уведомление пользователю
                try:
                    notify_lang = await resolve_user_language(telegram_id)
                    
                    try:
                        user_text = i18n_get_text(notify_lang, "admin.reissue_user_notification", vpn_key=f"<code>{new_vpn_key}</code>")
                    except (KeyError, TypeError):
                        # Fallback to default if localization not found
                        user_text = get_reissue_notification_text(new_vpn_key)
                    
                    keyboard = get_reissue_notification_keyboard(notify_lang)
                    await bot.send_message(telegram_id, user_text, reply_markup=keyboard, parse_mode="HTML")
                except Exception as e:
                    logging.warning(f"Failed to send reissue notification to user {telegram_id}: {e}")
                
                # Обновляем статус каждые 10 пользователей или в конце
                if idx % 10 == 0 or idx == total_count:
                    status_text = (
                        f"🔄 Массовый перевыпуск ключей\n\n"
                        f"Всего пользователей: {total_count}\n"
                        f"Обработано: {idx}/{total_count}\n"
                        f"✅ Успешно: {success_count}\n"
                        f"❌ Ошибок: {failed_count}"
                    )
                    try:
                        try:
                            await status_message.edit_text(status_text)
                        except TelegramBadRequest as e:
                            if "message is not modified" not in str(e):
                                raise
                    except Exception:
                        pass
                
                # Rate limiting: 1-2 секунды между запросами
                if idx < total_count:
                    import asyncio
                    await asyncio.sleep(1.5)
                    
            except Exception as e:
                failed_count += 1
                failed_users.append(telegram_id)
                logging.exception(f"Error reissuing key for user {telegram_id} in bulk operation: {e}")
                continue
        
        # Финальное сообщение
        final_text = (
            f"✅ Массовый перевыпуск завершён\n\n"
            f"Всего пользователей: {total_count}\n"
            f"✅ Успешно: {success_count}\n"
            f"❌ Ошибок: {failed_count}"
        )
        
        if failed_users:
            failed_list = ", ".join(map(str, failed_users[:10]))
            if len(failed_users) > 10:
                failed_list += f" и ещё {len(failed_users) - 10}"
            final_text += f"\n\nОшибки у пользователей: {failed_list}"
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:keys")]
        ])
        
        try:
            await status_message.edit_text(final_text, reply_markup=keyboard)
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e):
                raise
        
        # Логируем в audit_log
        await database._log_audit_event_atomic_standalone(
            "admin_reissue_all",
            admin_telegram_id,
            None,
            f"Bulk reissue: total={total_count}, success={success_count}, failed={failed_count}"
        )
        
    except Exception as e:
        logging.exception(f"Error in callback_admin_keys_reissue_all: {e}")
        await callback.message.edit_text(
            i18n_get_text(language, "admin.reissue_bulk_error", error=str(e)[:80], default=f"❌ Ошибка при массовом перевыпуске: {str(e)[:80]}"),
            reply_markup=get_admin_back_keyboard(language)
        )


@router.callback_query(F.data.startswith("admin:reissue_key:"))
async def callback_admin_reissue_key(callback: CallbackQuery, bot: Bot):
    """Перевыпуск ключа для одной подписки (по subscription_id)"""
    user = await database.get_user(callback.from_user.id)
    language = await resolve_user_language(callback.from_user.id)
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    try:
        # Получаем subscription_id из callback_data
        subscription_id = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer("Ошибка: неверный формат команды", show_alert=True)
        return
    
    admin_telegram_id = callback.from_user.id
    
    try:
        import vpn_utils
        
        # Проверяем, что подписка активна и получаем данные
        subscription = await database.get_active_subscription(subscription_id)
        if not subscription:
            await callback.answer("Подписка не найдена или не активна", show_alert=True)
            return
        
        telegram_id = subscription.get("telegram_id")
        old_uuid = subscription.get("uuid")
        
        if not old_uuid:
            await callback.answer("У подписки нет UUID для перевыпуска", show_alert=True)
            return
        
        # Перевыпускаем ключ
        await callback.answer("Перевыпускаю ключ...")
        
        try:
            new_uuid = await database.reissue_subscription_key(subscription_id)
        except ValueError as e:
            await callback.answer(f"Ошибка: {str(e)}", show_alert=True)
            return
        except Exception as e:
            logging.exception(f"Failed to reissue key for subscription {subscription_id}: {e}")
            await callback.answer(f"Ошибка при перевыпуске ключа: {str(e)}", show_alert=True)
            return
        
        # Генерируем новый VLESS URL для отображения
        try:
            vless_url = vpn_utils.generate_vless_url(new_uuid)
        except Exception as e:
            logging.warning(f"Failed to generate VLESS URL for new UUID: {e}")
            # Fallback: формируем простой VLESS URL
            try:
                vless_url = f"vless://{new_uuid}@{config.XRAY_SERVER_IP}:{config.XRAY_PORT}?encryption=none&security=reality&type=tcp#AtlasSecure"
            except Exception:
                vless_url = f"vless://{new_uuid}@SERVER:443..."
        
        # Показываем админу результат
        user = await database.get_user(telegram_id)
        user_lang = await resolve_user_language(telegram_id)
        username = user.get("username", i18n_get_text(user_lang, "common.username_not_set")) if user else i18n_get_text(user_lang, "common.username_not_set")
        
        expires_at = subscription["expires_at"]
        if isinstance(expires_at, str):
            expires_at = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
        expires_str = expires_at.strftime("%d.%m.%Y %H:%M")
        
        text = "✅ Ключ успешно перевыпущен\n\n"
        text += f"Подписка ID: {subscription_id}\n"
        text += f"Пользователь: @{username} ({telegram_id})\n"
        text += f"Срок действия: до {expires_str}\n\n"
        text += f"Новый VPN-ключ:\n<code>{vless_url}</code>"
        
        await safe_edit_text(callback.message, text, reply_markup=get_admin_back_keyboard(language), parse_mode="HTML")
        await callback.answer("Ключ успешно перевыпущен")
        
        # Логируем в audit_log
        await database._log_audit_event_atomic_standalone(
            "admin_reissue_key",
            admin_telegram_id,
            telegram_id,
            f"Reissued key for subscription_id={subscription_id}, old_uuid={old_uuid[:8]}..., new_uuid={new_uuid[:8]}..."
        )
        
        # НЕ отправляем уведомление пользователю автоматически (согласно требованиям)
        
    except Exception as e:
        logging.exception(f"Error in callback_admin_reissue_key: {e}")
        await callback.answer("Ошибка при перевыпуске ключа", show_alert=True)


@router.callback_query(F.data == "admin:reissue_all_active")
async def callback_admin_reissue_all_active(callback: CallbackQuery, bot: Bot):
    """Массовый перевыпуск ключей для всех активных подписок"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    await callback.answer("Начинаю массовый перевыпуск...")
    
    try:
        admin_telegram_id = callback.from_user.id
        
        # Получаем все активные подписки
        subscriptions = await database.get_all_active_subscriptions()
        
        total_count = len(subscriptions)
        success_count = 0
        failed_count = 0
        failed_subscriptions = []
        
        if total_count == 0:
            await safe_edit_text(
                callback.message,
                i18n_get_text(language, "admin.no_active_subscriptions_reissue"),
                reply_markup=get_admin_back_keyboard(language)
            )
            return
        
        # Отправляем начальное сообщение
        status_text = f"🔄 Массовый перевыпуск ключей\n\nВсего подписок: {total_count}\nОбработано: 0/{total_count}\nУспешно: 0\nОшибок: 0"
        status_message = await callback.message.edit_text(status_text, reply_markup=None)
        # Примечание: status_message используется для динамического обновления, защита не нужна
        
        # Обрабатываем каждую подписку ИТЕРАТИВНО (НЕ параллельно)
        for idx, subscription in enumerate(subscriptions, 1):
            subscription_id = subscription.get("id")
            telegram_id = subscription.get("telegram_id")
            old_uuid = subscription.get("uuid")
            
            if not subscription_id or not old_uuid:
                failed_count += 1
                failed_subscriptions.append(subscription_id or telegram_id)
                continue
            
            try:
                # Перевыпускаем ключ
                new_uuid = await database.reissue_subscription_key(subscription_id)
                success_count += 1
                
                # Обновляем статус каждые 10 подписок или в конце
                if idx % 10 == 0 or idx == total_count:
                    status_text = (
                        f"🔄 Массовый перевыпуск ключей\n\n"
                        f"Всего подписок: {total_count}\n"
                        f"Обработано: {idx}/{total_count}\n"
                        f"✅ Успешно: {success_count}\n"
                        f"❌ Ошибок: {failed_count}"
                    )
                    try:
                        try:
                            await status_message.edit_text(status_text)
                        except TelegramBadRequest as e:
                            if "message is not modified" not in str(e):
                                raise
                    except Exception:
                        pass
                
                # Rate limiting: 1-2 секунды между запросами
                if idx < total_count:
                    import asyncio
                    await asyncio.sleep(1.5)
                    
            except Exception as e:
                failed_count += 1
                failed_subscriptions.append(subscription_id)
                logging.exception(f"Error reissuing key for subscription {subscription_id} (user {telegram_id}) in bulk operation: {e}")
                continue
        
        # Финальное сообщение
        final_text = (
            f"✅ Массовый перевыпуск завершён\n\n"
            f"Всего подписок: {total_count}\n"
            f"✅ Успешно: {success_count}\n"
            f"❌ Ошибок: {failed_count}"
        )
        
        if failed_subscriptions:
            failed_list = ", ".join(map(str, failed_subscriptions[:10]))
            if len(failed_subscriptions) > 10:
                failed_list += f" и ещё {len(failed_subscriptions) - 10}"
            final_text += f"\n\nОшибки у подписок: {failed_list}"
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:keys")]
        ])
        
        try:
            await status_message.edit_text(final_text, reply_markup=keyboard)
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e):
                raise
        
        # Логируем в audit_log
        await database._log_audit_event_atomic_standalone(
            "admin_reissue_all_active",
            admin_telegram_id,
            None,
            f"Bulk reissue: total={total_count}, success={success_count}, failed={failed_count}"
        )
        
    except Exception as e:
        logging.exception(f"Error in callback_admin_reissue_all_active: {e}")
        await callback.message.edit_text(
            i18n_get_text(language, "admin.reissue_bulk_error", error=str(e)[:80], default=f"❌ Ошибка при массовом перевыпуске: {str(e)[:80]}"),
            reply_markup=get_admin_back_keyboard(language)
        )


@router.callback_query(F.data.startswith("admin:keys:"))
async def callback_admin_keys_legacy(callback: CallbackQuery):
    """Раздел VPN-ключи"""
    user = await database.get_user(callback.from_user.id)
    language = await resolve_user_language(callback.from_user.id)
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    try:
        stats = await database.get_vpn_keys_stats()
        
        text = "🔑 VPN-ключи\n\n"
        text += f"Всего ключей: {stats['total']}\n"
        text += f"Использованных: {stats['used']}\n"
        
        if stats['free'] <= 5:
            text += f"⚠️ Свободных: {stats['free']}\n"
            text += "\n⚠️ ВНИМАНИЕ: Количество свободных ключей критически низкое!"
        else:
            text += f"Свободных: {stats['free']}"
        
        await safe_edit_text(callback.message, text, reply_markup=get_admin_back_keyboard(language))
        await callback.answer()
        
        # Логируем просмотр статистики ключей
        await database._log_audit_event_atomic_standalone("admin_view_keys", callback.from_user.id, None, f"Admin viewed VPN keys stats: {stats['free']} free")
        
    except Exception as e:
        logging.exception(f"Error in callback_admin_keys: {e}")
        await callback.answer("Ошибка при получении статистики ключей", show_alert=True)


@router.callback_query(F.data == "admin:user")
async def callback_admin_user(callback: CallbackQuery, state: FSMContext):
    """Раздел Пользователь - запрос Telegram ID или username"""
    user = await database.get_user(callback.from_user.id)
    language = await resolve_user_language(callback.from_user.id)
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    text = i18n_get_text(language, "admin.user_prompt_enter_id")
    await callback.message.edit_text(text, reply_markup=get_admin_back_keyboard(language))
    await state.set_state(AdminUserSearch.waiting_for_user_id)
    await callback.answer()


@router.message(AdminUserSearch.waiting_for_user_id)
async def process_admin_user_id(message: Message, state: FSMContext):
    """Обработка введённого Telegram ID или username пользователя"""
    # B3.3 - ADMIN OVERRIDE: Admin operations intentionally bypass system_state checks
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(message.from_user.id)
        await message.answer(i18n_get_text(language, "admin.access_denied"))
        await state.clear()
        return
    
    try:
        user_input = message.text.strip()
        
        # Определяем, является ли ввод числом (ID) или строкой (username)
        try:
            target_user_id = int(user_input)
            # Это число - ищем по ID
            user = await database.find_user_by_id_or_username(telegram_id=target_user_id)
            search_by = "ID"
            search_value = str(target_user_id)
        except ValueError:
            # Это строка - ищем по username
            username = user_input.lstrip('@')  # Убираем @, если есть
            if not username:  # Пустая строка после удаления @
                await message.answer("Пользователь не найден.\nПроверьте Telegram ID или username.")
                await state.clear()
                return
            username = username.lower()  # Приводим к нижнему регистру
            user = await database.find_user_by_id_or_username(username=username)
            search_by = "username"
            search_value = username
        
        # Если пользователь не найден
        if not user:
            await message.answer("Пользователь не найден.\nПроверьте Telegram ID или username.")
            await state.clear()
            return
        
        # Получаем полный обзор пользователя через admin service
        try:
            overview = await admin_service.get_admin_user_overview(user["telegram_id"])
        except UserNotFoundError:
            await message.answer("Пользователь не найден.\nПроверьте Telegram ID или username.")
            await state.clear()
            return
        
        # Получаем доступные действия через admin service
        actions = admin_service.get_admin_user_actions(overview)
        
        # Формируем карточку пользователя (только форматирование)
        text = "👤 Пользователь\n\n"
        text += f"Telegram ID: {overview.user['telegram_id']}\n"
        username_display = overview.user.get('username') or 'не указан'
        text += f"Username: @{username_display}\n"
        
        # Язык
        user_language = overview.user.get('language') or 'ru'
        language_display = i18n_get_text("ru", f"lang.button_{user_language}")
        text += f"Язык: {language_display}\n"
        
        # Дата регистрации
        created_at = overview.user.get('created_at')
        if created_at:
            if isinstance(created_at, str):
                created_at = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
            created_str = created_at.strftime("%d.%m.%Y %H:%M")
            text += f"Дата регистрации: {created_str}\n"
        else:
            text += "Дата регистрации: —\n"
        
        text += "\n"
        
        # Информация о подписке
        if overview.subscription:
            expires_at = overview.subscription_status.expires_at
            if expires_at:
                expires_str = expires_at.strftime("%d.%m.%Y %H:%M")
            else:
                expires_str = "—"
            
            if overview.subscription_status.is_active:
                text += "Статус подписки: ✅ Активна\n"
            else:
                text += "Статус подписки: ⛔ Истекла\n"
            
            text += f"Срок действия: до {expires_str}\n"
            text += f"VPN-ключ: {overview.subscription.get('vpn_key', '—')}\n"
        else:
            text += "Статус подписки: ❌ Нет подписки\n"
            text += "VPN-ключ: —\n"
            text += "Срок действия: —\n"
        
        # Статистика
        text += f"\nКоличество продлений: {overview.stats['renewals_count']}\n"
        text += f"Количество перевыпусков: {overview.stats['reissues_count']}\n"
        
        # Персональная скидка
        if overview.user_discount:
            discount_percent = overview.user_discount["discount_percent"]
            expires_at_discount = overview.user_discount.get("expires_at")
            if expires_at_discount:
                if isinstance(expires_at_discount, str):
                    expires_at_discount = datetime.fromisoformat(expires_at_discount.replace('Z', '+00:00'))
                expires_str = expires_at_discount.strftime("%d.%m.%Y %H:%M")
                text += f"\n🎯 Персональная скидка: {discount_percent}% (до {expires_str})\n"
            else:
                text += f"\n🎯 Персональная скидка: {discount_percent}% (бессрочно)\n"
        
        # VIP-статус
        if overview.is_vip:
            text += f"\n👑 VIP-статус: активен\n"
        
        # Используем actions для определения доступных действий
        await message.answer(
            text,
            reply_markup=get_admin_user_keyboard(
                has_active_subscription=overview.subscription_status.is_active,
                user_id=overview.user["telegram_id"],
                has_discount=overview.user_discount is not None,
                is_vip=overview.is_vip
            ),
            parse_mode="HTML"
        )
        
        # Логируем просмотр информации о пользователе
        details = f"Admin searched by {search_by}: {search_value}, found user {user['telegram_id']}"
        await database._log_audit_event_atomic_standalone("admin_view_user", message.from_user.id, user["telegram_id"], details)
        
        await state.clear()
        
    except Exception as e:
        logging.exception(f"Error in process_admin_user_id: {e}")
        await message.answer("Ошибка при получении информации о пользователе. Проверь логи.")
        await state.clear()


@router.callback_query(F.data.startswith("admin:user_history:"))
async def callback_admin_user_history(callback: CallbackQuery):
    """История подписок пользователя (админ)"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    try:
        # Получаем user_id из callback_data
        target_user_id = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer("Ошибка: неверный формат команды", show_alert=True)
        return
    
    try:
        # Получаем историю подписок
        history = await database.get_subscription_history(target_user_id, limit=10)
        
        if not history:
            text = "🧾 История подписок\n\nИстория подписок пуста."
            await callback.message.answer(text, reply_markup=get_admin_back_keyboard(language))
            await callback.answer()
            return
        
        # Формируем текст истории
        text = "🧾 История подписок\n\n"
        
        action_type_map = {
            "purchase": "Покупка",
            "renewal": "Продление",
            "reissue": "Выдача нового ключа",
            "manual_reissue": "Перевыпуск ключа",
        }
        
        for record in history:
            start_date = record["start_date"]
            if isinstance(start_date, str):
                start_date = datetime.fromisoformat(start_date)
            start_str = start_date.strftime("%d.%m.%Y")
            
            end_date = record["end_date"]
            if isinstance(end_date, str):
                end_date = datetime.fromisoformat(end_date)
            end_str = end_date.strftime("%d.%m.%Y")
            
            action_type = record["action_type"]
            action_text = action_type_map.get(action_type, action_type)
            
            text += f"• {start_str} — {action_text}\n"
            
            # Для purchase и reissue показываем ключ
            if action_type in ["purchase", "reissue", "manual_reissue"]:
                text += f"  Ключ: {record['vpn_key']}\n"
            
            text += f"  До: {end_str}\n\n"
        
        await callback.message.answer(text, reply_markup=get_admin_back_keyboard(language))
        await callback.answer()
        
        # Логируем просмотр истории
        await database._log_audit_event_atomic_standalone("admin_view_user_history", callback.from_user.id, target_user_id, f"Admin viewed subscription history for user {target_user_id}")
        
    except Exception as e:
        logging.exception(f"Error in callback_admin_user_history: {e}")
        await callback.answer("Ошибка при получении истории подписок", show_alert=True)


def get_admin_grant_days_keyboard(user_id: int, language: str = "ru"):
    """
    5. ADVANCED ACCESS CONTROL (GRANT / REVOKE)
    
    Keyboard for selecting access duration with quick options and custom duration.
    """
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=i18n_get_text(language, "admin.grant_days_1"), callback_data=f"admin:grant_days:{user_id}:1"),
            InlineKeyboardButton(text=i18n_get_text(language, "admin.grant_days_7"), callback_data=f"admin:grant_days:{user_id}:7"),
        ],
        [
            InlineKeyboardButton(text=i18n_get_text(language, "admin.grant_days_14"), callback_data=f"admin:grant_days:{user_id}:14"),
        ],
        [
            InlineKeyboardButton(text=i18n_get_text(language, "admin.grant_1_year"), callback_data=f"admin:grant_1_year:{user_id}"),
        ],
        [
            InlineKeyboardButton(text=i18n_get_text(language, "admin.grant_minutes_10"), callback_data=f"admin:grant_minutes:{user_id}:10"),
        ],
        [
            InlineKeyboardButton(text=i18n_get_text(language, "admin.grant_custom"), callback_data=f"admin:grant_custom:{user_id}"),
        ],
        [
            InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:user"),
        ]
    ])
    return keyboard


@router.callback_query(F.data.startswith("admin:grant:") & ~F.data.startswith("admin:grant_custom:") & ~F.data.startswith("admin:grant_days:") & ~F.data.startswith("admin:grant_minutes:") & ~F.data.startswith("admin:grant_1_year:") & ~F.data.startswith("admin:grant_unit:") & ~F.data.startswith("admin:grant:notify:") & ~F.data.startswith("admin:notify:"))
async def callback_admin_grant(callback: CallbackQuery, state: FSMContext):
    """
    Entry point: Admin selects "Выдать доступ" for a user.
    Shows quick action buttons (1/7/14 days, 1 year, 10 minutes, custom).
    """
    # B3.3 - ADMIN OVERRIDE: Admin operations intentionally bypass system_state checks
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    await callback.answer()
    
    try:
        user_id = int(callback.data.split(":")[2])
        
        # Сохраняем user_id в состоянии
        await state.update_data(user_id=user_id)
        
        # Показываем клавиатуру выбора срока
        text = "Выберите срок доступа:"
        await callback.message.edit_text(text, reply_markup=get_admin_grant_days_keyboard(user_id))
        await state.set_state(AdminGrantAccess.waiting_for_days)
        
        logger.debug(f"FSM: AdminGrantAccess.waiting_for_days set for user {user_id}")
        
    except Exception as e:
        logging.exception(f"Error in callback_admin_grant: {e}")
        await callback.answer("Ошибка. Проверь логи.", show_alert=True)


@router.callback_query(F.data.startswith("admin:grant_days:"), StateFilter(AdminGrantAccess.waiting_for_days))
async def callback_admin_grant_days(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """
    4️⃣ NOTIFY USER LOGIC (GRANT + REVOKE)
    
    Quick action: Grant access for N days.
    Ask for notify_user choice before executing.
    """
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    await callback.answer()
    
    try:
        parts = callback.data.split(":")
        user_id = int(parts[2])
        days = int(parts[3])
        
        # Save user_id and days in FSM, ask for notify choice
        await state.update_data(user_id=user_id, days=days, action_type="grant_days")
        
        text = f"✅ Выдать доступ на {days} дней\n\nУведомить пользователя?"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.notify_yes"), callback_data="admin:notify:yes")],
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.notify_no"), callback_data="admin:notify:no")],
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.cancel"), callback_data=f"admin:grant:{user_id}")],
        ])
        await callback.message.edit_text(text, reply_markup=keyboard)
        await state.set_state(AdminGrantAccess.waiting_for_notify)
        
        logger.debug(f"FSM: AdminGrantAccess.waiting_for_notify set for quick action (days={days})")
        
    except Exception as e:
        logger.exception(f"Error in callback_admin_grant_days: {e}")
        user = await database.get_user(callback.from_user.id)
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "errors.generic"), show_alert=True)
        await state.clear()


@router.callback_query(F.data.startswith("admin:grant_minutes:"), StateFilter(AdminGrantAccess.waiting_for_days))
async def callback_admin_grant_minutes(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """
    1️⃣ FIX CONTRACT MISUSE: Execute grant BEFORE showing notify buttons.
    2️⃣ STORE NOTIFY CONTEXT EXPLICITLY: Encode data in callback_data.
    
    Quick action: Grant access for N minutes, then ask for notify choice.
    """
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    await callback.answer()
    
    try:
        parts = callback.data.split(":")
        user_id = int(parts[2])
        minutes = int(parts[3])
        
        # 1️⃣ FIX CONTRACT MISUSE: Execute grant FIRST (treat as side-effect only)
        try:
            await database.admin_grant_access_minutes_atomic(
                telegram_id=user_id,
                minutes=minutes,
                admin_telegram_id=callback.from_user.id
            )
            # If no exception → grant is successful (don't check return value)
        except Exception as e:
            logger.exception(f"CRITICAL: Failed to grant admin access (minutes) for user {user_id}, minutes={minutes}, admin={callback.from_user.id}: {e}")
            text = f"❌ Ошибка выдачи доступа: {str(e)[:100]}"
            await safe_edit_text(callback.message, text, reply_markup=get_admin_back_keyboard(language))
            await callback.answer("Ошибка создания ключа", show_alert=True)
            await state.clear()
            return
        
        # 2️⃣ STORE NOTIFY CONTEXT EXPLICITLY: Encode all data in callback_data
        # Format: admin:notify:yes:minutes:<user_id>:<minutes>
        text = f"✅ Доступ выдан на {minutes} минут\n\nУведомить пользователя?"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.notify_yes"), callback_data=f"admin:notify:yes:minutes:{user_id}:{minutes}")],
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.notify_no"), callback_data=f"admin:notify:no:minutes:{user_id}:{minutes}")],
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.cancel"), callback_data=f"admin:grant:{user_id}")],
        ])
        await callback.message.edit_text(text, reply_markup=keyboard)
        
        # Clear FSM - notify handlers will work without FSM
        await state.clear()
        
        logger.debug(f"Grant executed for user {user_id}, minutes={minutes}, waiting for notify choice")
        
    except Exception as e:
        logger.exception(f"Error in callback_admin_grant_minutes: {e}")
        user = await database.get_user(callback.from_user.id)
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "errors.generic"), show_alert=True)
        await state.clear()


@router.callback_query(F.data.startswith("admin:grant_1_year:"), StateFilter(AdminGrantAccess.waiting_for_days))
async def callback_admin_grant_1_year(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """
    4️⃣ NOTIFY USER LOGIC (GRANT + REVOKE)
    
    Quick action: Grant access for 1 year (365 days).
    Ask for notify_user choice before executing.
    """
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    await callback.answer()
    
    try:
        parts = callback.data.split(":")
        user_id = int(parts[2])
        
        # Save user_id in FSM, ask for notify choice
        await state.update_data(user_id=user_id, days=365, action_type="grant_1_year")
        
        text = "✅ Выдать доступ на 1 год\n\nУведомить пользователя?"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.notify_yes"), callback_data="admin:notify:yes")],
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.notify_no"), callback_data="admin:notify:no")],
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.cancel"), callback_data=f"admin:grant:{user_id}")],
        ])
        await callback.message.edit_text(text, reply_markup=keyboard)
        await state.set_state(AdminGrantAccess.waiting_for_notify)
        
        logger.debug(f"FSM: AdminGrantAccess.waiting_for_notify set for quick action (1 year)")
        
    except Exception as e:
        logger.exception(f"Error in callback_admin_grant_1_year: {e}")
        user = await database.get_user(callback.from_user.id)
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "errors.generic"), show_alert=True)
        await state.clear()


@router.callback_query(F.data.startswith("admin:grant_custom:"), StateFilter(AdminGrantAccess.waiting_for_days))
async def callback_admin_grant_custom_from_days(callback: CallbackQuery, state: FSMContext):
    """
    2️⃣ CALLBACK HANDLERS — CRITICAL FIX
    
    Start custom grant flow from waiting_for_days state.
    This is the handler that was missing - works when FSM is in waiting_for_days.
    """
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    await callback.answer()
    
    try:
        user_id = int(callback.data.split(":")[2])
        await state.update_data(user_id=user_id)
        
        text = "⚙️ Настройка доступа\n\nВыберите единицу времени:"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.grant_unit_minutes"), callback_data="admin:grant_unit:minutes")],
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.grant_unit_hours"), callback_data="admin:grant_unit:hours")],
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.grant_unit_days"), callback_data="admin:grant_unit:days")],
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.cancel"), callback_data=f"admin:grant:{user_id}")],
        ])
        await callback.message.edit_text(text, reply_markup=keyboard)
        await state.set_state(AdminGrantAccess.waiting_for_unit)
        
        logger.debug(f"FSM: AdminGrantAccess.waiting_for_unit set for user {user_id} (from waiting_for_days state)")
        
    except Exception as e:
        logger.exception(f"Error in callback_admin_grant_custom_from_days: {e}")
        user = await database.get_user(callback.from_user.id)
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "errors.generic"), show_alert=True)
        await state.clear()


@router.callback_query(F.data.startswith("admin:grant_custom:"))
async def callback_admin_grant_custom(callback: CallbackQuery, state: FSMContext):
    """
    2️⃣ CALLBACK HANDLERS — CRITICAL FIX
    
    Start custom grant flow - select duration unit first.
    Fallback handler (no state filter) - works from any state.
    """
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    await callback.answer()
    
    try:
        user_id = int(callback.data.split(":")[2])
        await state.update_data(user_id=user_id)
        
        text = "⚙️ Настройка доступа\n\nВыберите единицу времени:"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.grant_unit_minutes"), callback_data="admin:grant_unit:minutes")],
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.grant_unit_hours"), callback_data="admin:grant_unit:hours")],
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.grant_unit_days"), callback_data="admin:grant_unit:days")],
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.cancel"), callback_data=f"admin:grant:{user_id}")],
        ])
        await callback.message.edit_text(text, reply_markup=keyboard)
        await state.set_state(AdminGrantAccess.waiting_for_unit)
        
        logger.debug(f"FSM: AdminGrantAccess.waiting_for_unit set for user {user_id} (from any state)")
        
    except Exception as e:
        logger.exception(f"Error in callback_admin_grant_custom: {e}")
        user = await database.get_user(callback.from_user.id)
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "errors.generic"), show_alert=True)
        await state.clear()


@router.callback_query(F.data.startswith("admin:grant_unit:"), StateFilter(AdminGrantAccess.waiting_for_unit))
async def callback_admin_grant_unit(callback: CallbackQuery, state: FSMContext):
    """
    2️⃣ CALLBACK HANDLERS — CRITICAL FIX
    
    Process duration unit selection, move to value input.
    Handler works ONLY in state waiting_for_unit.
    """
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    await callback.answer()
    
    try:
        unit = callback.data.split(":")[2]  # minutes, hours, days (fixed: was [3], now [2] for admin:grant_unit:minutes)
        await state.update_data(duration_unit=unit)
        
        unit_text = {"minutes": "минут", "hours": "часов", "days": "дней"}.get(unit, unit)
        text = f"⚙️ Настройка доступа\n\nЕдиница: {unit_text}\n\nВведите количество (положительное число):"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.cancel"), callback_data="admin:main")],
        ])
        await callback.message.edit_text(text, reply_markup=keyboard)
        await state.set_state(AdminGrantAccess.waiting_for_value)
        
        logger.debug(f"FSM: AdminGrantAccess.waiting_for_value set, unit={unit}")
        
    except Exception as e:
        logger.exception(f"Error in callback_admin_grant_unit: {e}")
        user = await database.get_user(callback.from_user.id)
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "errors.generic"), show_alert=True)
        await state.clear()


@router.message(StateFilter(AdminGrantAccess.waiting_for_value))
async def process_admin_grant_value(message: Message, state: FSMContext):
    """
    PART 1: Process duration value input, move to notify choice.
    """
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(message.from_user.id)
        await message.answer(i18n_get_text(language, "admin.access_denied"))
        await state.clear()
        return
    
    try:
        value = int(message.text.strip())
        if value <= 0:
            await message.answer("❌ Введите положительное число")
            return
        
        data = await state.get_data()
        unit = data.get("duration_unit")
        unit_text = {"minutes": "минут", "hours": "часов", "days": "дней"}.get(unit, unit)
        
        await state.update_data(duration_value=value)
        
        text = f"⚙️ Настройка доступа\n\nПродолжительность: {value} {unit_text}\n\nУведомить пользователя?"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.notify_yes"), callback_data="admin:grant:notify:yes")],
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.notify_no"), callback_data="admin:grant:notify:no")],
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.cancel"), callback_data="admin:main")],
        ])
        await message.answer(text, reply_markup=keyboard)
        await state.set_state(AdminGrantAccess.waiting_for_notify)
        
        logger.debug(f"FSM: AdminGrantAccess.waiting_for_notify set, value={value}, unit={unit}")
        
    except ValueError:
        await message.answer("❌ Введите число")
    except Exception as e:
        logger.exception(f"Error in process_admin_grant_value: {e}")
        await message.answer("Ошибка")
        await state.clear()


@router.callback_query(F.data.startswith("admin:grant:notify:"), StateFilter(AdminGrantAccess.waiting_for_notify))
async def callback_admin_grant_notify(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """
    PART 1: Execute grant access with notify_user choice.
    """
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    await callback.answer()
    
    try:
        notify_user = callback.data.split(":")[3] == "yes"
        data = await state.get_data()
        user_id = data.get("user_id")
        duration_value = data.get("duration_value")
        duration_unit = data.get("duration_unit")
        
        if not all([user_id, duration_value, duration_unit]):
            await callback.answer("Ошибка: данные не найдены", show_alert=True)
            await state.clear()
            return
        
        # PART 3: Convert duration to timedelta
        from datetime import timedelta
        if duration_unit == "minutes":
            duration = timedelta(minutes=duration_value)
        elif duration_unit == "hours":
            duration = timedelta(hours=duration_value)
        else:  # days
            duration = timedelta(days=duration_value)
        
        logger.debug(f"FSM: Executing grant for user {user_id}, duration={duration}, notify_user={notify_user}")
        
        # PART 3: Execute grant_access
        try:
            result = await database.grant_access(
                telegram_id=user_id,
                duration=duration,
                source="admin",
                admin_telegram_id=callback.from_user.id,
                admin_grant_days=None  # Custom duration
            )
            
            expires_at = result["subscription_end"]
            vpn_key = result.get("vless_url") or result.get("uuid", "")
            
            expires_str = expires_at.strftime("%d.%m.%Y %H:%M")
            unit_text = {"minutes": "минут", "hours": "часов", "days": "дней"}.get(duration_unit, duration_unit)
            text = f"✅ Доступ выдан на {duration_value} {unit_text}"
            if notify_user:
                text += "\nПользователь уведомлён."
            else:
                text += "\nДействие выполнено без уведомления."
            await safe_edit_text(callback.message, text, reply_markup=get_admin_back_keyboard(language))
            
            # PART 6: Notify user if flag is True
            if notify_user and vpn_key:
                import admin_notifications
                language = await resolve_user_language(user_id)
                vpn_key_html = f"<code>{vpn_key}</code>" if vpn_key else "⏳ Активация в процессе"
                user_text = f"✅ Вам выдан доступ на {duration_value} {unit_text}\n\nКлюч: {vpn_key_html}\nДействителен до: {expires_str}"
                # Use unified notification service
                await admin_notifications.send_user_notification(
                    bot=bot,
                    user_id=user_id,
                    message=user_text,
                    notification_type="admin_grant_custom",
                    parse_mode="HTML"
                )
            
            # PART 6: Audit log
            await database._log_audit_event_atomic_standalone(
                "admin_grant_access_custom",
                callback.from_user.id,
                user_id,
                f"Admin granted {duration_value} {duration_unit} access, notify_user={notify_user}, expires_at={expires_str}"
            )
            
        except Exception as e:
            logger.exception(f"Error granting custom access: {e}")
            await callback.message.answer(f"❌ Ошибка: {str(e)[:100]}", reply_markup=get_admin_back_keyboard(language))
        
        await state.clear()
        logger.debug(f"FSM: AdminGrantAccess cleared after grant")
        
    except Exception as e:
        logger.exception(f"Error in callback_admin_grant_notify: {e}")
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "errors.generic"), show_alert=True)
        await state.clear()


@router.callback_query(F.data.startswith("admin:notify:yes:minutes:") | F.data.startswith("admin:notify:no:minutes:"))
async def callback_admin_grant_minutes_notify(callback: CallbackQuery, bot: Bot):
    """
    3️⃣ REGISTER EXPLICIT CALLBACK HANDLERS
    4️⃣ IMPLEMENT NOTIFY LOGIC
    
    Handle notify choice for minutes grant.
    Works WITHOUT FSM - all data encoded in callback_data.
    Format: admin:notify:yes|no:minutes:<user_id>:<minutes>
    """
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    await callback.answer()
    
    try:
        # 3️⃣ REGISTER EXPLICIT CALLBACK HANDLERS: Parse callback_data
        parts = callback.data.split(":")
        if len(parts) != 6 or parts[1] != "notify" or parts[3] != "minutes":
            logger.warning(f"Invalid notify callback format: {callback.data}")
            await callback.answer("Ошибка формата команды", show_alert=True)
            return
        
        notify_choice = parts[2]  # "yes" or "no"
        user_id = int(parts[4])
        minutes = int(parts[5])
        
        notify = notify_choice == "yes"
        
        # 4️⃣ ЛОГИРОВАНИЕ: при выборе notify
        logger.info(f"ADMIN_GRANT_NOTIFY_SELECTED [notify={notify_choice}, user_id={user_id}, minutes={minutes}]")
        
        # 4️⃣ IMPLEMENT NOTIFY LOGIC: For admin:notify:yes
        if notify:
            # Use unified notification service
            import admin_notifications
            success = await admin_notifications.send_user_notification(
                bot=bot,
                user_id=user_id,
                message=f"Администратор выдал вам доступ на {minutes} минут",
                notification_type="admin_grant_minutes"
            )
            if success:
                logger.info(f"NOTIFICATION_SENT [type=admin_grant, user_id={user_id}, minutes={minutes}]")
        
        # 4️⃣ IMPLEMENT NOTIFY LOGIC: For admin:notify:no
        else:
            # 4️⃣ ЛОГИРОВАНИЕ: если notify=False
            logger.info(f"ADMIN_GRANT_NOTIFY_SKIPPED [user_id={user_id}, minutes={minutes}]")
        
        # 5️⃣ CLEAN TERMINATION: Edit admin message to "Готово"
        text = f"✅ Доступ выдан на {minutes} минут"
        if notify:
            text += "\nПользователь уведомлён."
        else:
            text += "\nДействие выполнено без уведомления."
        await safe_edit_text(callback.message, text, reply_markup=get_admin_back_keyboard(language))
        
    except ValueError as e:
        logger.warning(f"Invalid callback data format: {callback.data}, error: {e}")
        await callback.answer("Ошибка: неверный формат команды", show_alert=True)
    except Exception as e:
        # 6️⃣ ERROR HANDLING: NO generic Exception raises, graceful exit
        logger.warning(f"Unexpected error in callback_admin_grant_minutes_notify: {e}")
        user = await database.get_user(callback.from_user.id)
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "errors.generic"), show_alert=True)


@router.callback_query(
    (F.data == "admin:notify:yes") | (F.data == "admin:notify:no"),
    StateFilter(AdminGrantAccess.waiting_for_notify)
)
async def callback_admin_grant_quick_notify_fsm(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """
    Handle notify choice for grant_days and grant_1_year (FSM-based flow).
    This handler works WITH FSM state (unlike minutes handler which is FSM-free).
    
    FIX: Missing handler for admin:notify:yes and admin:notify:no used by grant_days and grant_1_year.
    """
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    await callback.answer()
    
    try:
        notify = callback.data == "admin:notify:yes"
        data = await state.get_data()
        user_id = data.get("user_id")
        action_type = data.get("action_type")
        
        if not user_id or not action_type:
            logger.warning(f"Missing FSM data: user_id={user_id}, action_type={action_type}")
            await callback.answer("Ошибка: данные не найдены", show_alert=True)
            await state.clear()
            return
        
        logger.info(f"ADMIN_GRANT_NOTIFY_SELECTED [notify={notify}, user_id={user_id}, action_type={action_type}]")
        
        # Execute grant based on action_type (treat as side-effect, don't check return value)
        if action_type == "grant_days":
            days = data.get("days")
            if not days:
                logger.error(f"Missing days in FSM for grant_days")
                await callback.answer("Ошибка: данные не найдены", show_alert=True)
                await state.clear()
                return
            
            # FIX: Execute grant (treat as side-effect, don't check return value)
            try:
                await database.admin_grant_access_atomic(
                    telegram_id=user_id,
                    days=days,
                    admin_telegram_id=callback.from_user.id
                )
                # If no exception → grant is successful (don't check return value)
            except Exception as e:
                logger.exception(f"Failed to grant access: {e}")
                await callback.answer("Ошибка выдачи доступа", show_alert=True)
                await state.clear()
                return
            
            text = f"✅ Доступ выдан на {days} дней"
            
            if notify:
                try:
                    user_text = f"Администратор выдал вам доступ на {days} дней"
                    await bot.send_message(user_id, user_text)
                    logger.info(f"NOTIFICATION_SENT [type=admin_grant, user_id={user_id}, days={days}]")
                    text += "\nПользователь уведомлён."
                except Exception as e:
                    logger.exception(f"Error sending notification: {e}")
                    text += "\nОшибка отправки уведомления."
            else:
                logger.info(f"ADMIN_GRANT_NOTIFY_SKIPPED [user_id={user_id}, days={days}]")
                text += "\nДействие выполнено без уведомления."
            
            await safe_edit_text(callback.message, text, reply_markup=get_admin_back_keyboard(language))
            
            # Audit log
            await database._log_audit_event_atomic_standalone(
                "admin_grant_access",
                callback.from_user.id,
                user_id,
                f"Admin granted {days} days access, notify_user={notify}"
            )
        
        elif action_type == "grant_1_year":
            # FIX: Execute grant (treat as side-effect, don't check return value)
            try:
                await database.admin_grant_access_atomic(
                    telegram_id=user_id,
                    days=365,
                    admin_telegram_id=callback.from_user.id
                )
                # If no exception → grant is successful (don't check return value)
            except Exception as e:
                logger.exception(f"Failed to grant access: {e}")
                await callback.answer("Ошибка выдачи доступа", show_alert=True)
                await state.clear()
                return
            
            text = "✅ Доступ на 1 год выдан"
            
            if notify:
                # Use unified notification service
                import admin_notifications
                success = await admin_notifications.send_user_notification(
                    bot=bot,
                    user_id=user_id,
                    message="Администратор выдал вам доступ на 1 год",
                    notification_type="admin_grant_1_year"
                )
                if success:
                    logger.info(f"NOTIFICATION_SENT [type=admin_grant, user_id={user_id}, duration=1_year]")
                    text += "\nПользователь уведомлён."
                    text += "\nОшибка отправки уведомления."
            else:
                logger.info(f"ADMIN_GRANT_NOTIFY_SKIPPED [user_id={user_id}, duration=1_year]")
                text += "\nДействие выполнено без уведомления."
            
            await safe_edit_text(callback.message, text, reply_markup=get_admin_back_keyboard(language))
            
            # Audit log
            await database._log_audit_event_atomic_standalone(
                "admin_grant_access_1_year",
                callback.from_user.id,
                user_id,
                f"Admin granted 1 year access, notify_user={notify}"
            )
        
        else:
            logger.warning(f"Unknown action_type: {action_type}")
            await callback.answer("Ошибка: неизвестный тип действия", show_alert=True)
        
        await state.clear()
        
    except Exception as e:
        logger.exception(f"Error in callback_admin_grant_quick_notify_fsm: {e}")
        user = await database.get_user(callback.from_user.id)
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "errors.generic"), show_alert=True)
        await state.clear()


@router.callback_query(F.data.startswith("admin:revoke:user:"))
async def callback_admin_revoke(callback: CallbackQuery, bot: Bot, state: FSMContext):
    """
    1️⃣ CALLBACK DATA SCHEMA (точечно)
    2️⃣ FIX handler callback_admin_revoke
    
    Admin revoke access - ask for notify choice first.
    Handler обрабатывает ТОЛЬКО callback вида: admin:revoke:user:<id>
    """
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    await callback.answer()
    
    try:
        # 2️⃣ FIX: Строгий guard - парсим только admin:revoke:user:<id>
        parts = callback.data.split(":")
        if len(parts) != 4 or parts[2] != "user":
            logger.warning(f"Invalid revoke callback format: {callback.data}")
            await callback.answer("Ошибка формата команды", show_alert=True)
            return
        
        user_id = int(parts[3])
        
        # 4️⃣ FSM CONSISTENCY: Save user_id and ask for notify choice
        await state.update_data(user_id=user_id)
        
        text = i18n_get_text(language, "admin.revoke_confirm_text", "admin_revoke_confirm_text")
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.notify_yes"), callback_data="admin:revoke:notify:yes")],
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.notify_no"), callback_data="admin:revoke:notify:no")],
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.cancel", "admin_cancel"), callback_data=f"admin:user")],
        ])
        await callback.message.edit_text(text, reply_markup=keyboard)
        await state.set_state(AdminRevokeAccess.waiting_for_notify_choice)
        
        # 5️⃣ ЛОГИРОВАНИЕ: выбран user_id
        logger.info(f"Admin {callback.from_user.id} initiated revoke for user {user_id}")
        logger.debug(f"FSM: AdminRevokeAccess.waiting_for_notify_choice set for user {user_id}")
        
    except ValueError as e:
        logger.error(f"Invalid user_id in revoke callback: {callback.data}, error: {e}")
        await callback.answer("Ошибка: неверный ID пользователя", show_alert=True)
        await state.clear()
    except Exception as e:
        logger.exception(f"Error in callback_admin_revoke: {e}")
        user = await database.get_user(callback.from_user.id)
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "errors.generic"), show_alert=True)
        await state.clear()


@router.callback_query(F.data.startswith("admin:revoke:notify:"), StateFilter(AdminRevokeAccess.waiting_for_notify_choice))
async def callback_admin_revoke_notify(callback: CallbackQuery, bot: Bot, state: FSMContext):
    """
    3️⃣ ДОБАВИТЬ ОТДЕЛЬНЫЙ handler для notify
    
    Execute revoke with notify_user choice.
    Handler обрабатывает ТОЛЬКО callback вида: admin:revoke:notify:yes|no
    """
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    await callback.answer()
    
    try:
        # 1️⃣ НОРМАЛИЗАЦИЯ notify (КРИТИЧНО): читаем notify=yes|no
        parts = callback.data.split(":")
        if len(parts) != 4 or parts[2] != "notify":
            logger.warning(f"Invalid revoke notify callback format: {callback.data}")
            await callback.answer("Ошибка формата команды", show_alert=True)
            await state.clear()
            return
        
        # 1️⃣ НОРМАЛИЗАЦИЯ notify: явно приводим к bool
        notify_raw = parts[3]  # "yes" or "no"
        notify = notify_raw == "yes"  # bool: True or False
        
        # 4️⃣ FSM CONSISTENCY: используем сохраненный user_id
        data = await state.get_data()
        user_id = data.get("user_id")
        
        if not user_id:
            logger.error(f"user_id not found in FSM state for revoke notify")
            await callback.answer("Ошибка: user_id не найден", show_alert=True)
            await state.clear()
            return
        
        # 1️⃣ НОРМАЛИЗАЦИЯ notify: сохраняем в FSM ТОЛЬКО bool
        await state.update_data(notify=notify)
        
        # 4️⃣ ЛОГИРОВАНИЕ: при выборе notify
        logger.info(f"ADMIN_REVOKE_NOTIFY_SELECTED [user_id={user_id}, notify={notify}]")
        
        # 3️⃣ ДОБАВИТЬ ОТДЕЛЬНЫЙ handler: вызываем финальный revoke action
        revoked = await database.admin_revoke_access_atomic(
            telegram_id=user_id,
            admin_telegram_id=callback.from_user.id
        )
        
        if not revoked:
            text = "❌ У пользователя нет активной подписки"
            await safe_edit_text(callback.message, text, reply_markup=get_admin_back_keyboard(language))
            await callback.answer("Нет активной подписки", show_alert=True)
        else:
            text = "✅ Доступ отозван"
            if notify:
                text += "\nПользователь уведомлён."
            else:
                text += "\nДействие выполнено без уведомления."
            await safe_edit_text(callback.message, text, reply_markup=get_admin_back_keyboard(language))
            
            # 2️⃣ ПРОВЕРКА notify В ФИНАЛЬНОМ revoke: используем ТОЛЬКО if notify:
            # 3️⃣ ОТПРАВКА УВЕДОМЛЕНИЯ (ЯВНО): если notify=True
            if notify:
                # 5️⃣ ЗАЩИТА ОТ ТИХОГО ПРОПУСКА: проверяем telegram_id
                if not user_id:
                    logger.warning(f"ADMIN_REVOKE_NOTIFY_SKIP: user_id missing, notify=True but cannot send")
                else:
                    try:
                        # 3️⃣ ОТПРАВКА УВЕДОМЛЕНИЯ: используем telegram_id из FSM (НЕ из callback)
                        # 3️⃣ ОТПРАВКА УВЕДОМЛЕНИЯ: текст без форматных рисков (фиксированный)
                        # Use unified notification service
                        import admin_notifications
                        user_text = (
                            "Ваш доступ был отозван администратором.\n"
                            "Если вы считаете это ошибкой — обратитесь в поддержку."
                        )
                        success = await admin_notifications.send_user_notification(
                            bot=bot,
                            user_id=user_id,
                            message=user_text,
                            notification_type="admin_revoke"
                        )
                        if success:
                            # 4️⃣ ЛОГИРОВАНИЕ: при отправке уведомления
                            logger.info(f"NOTIFICATION_SENT [type=admin_revoke, user_id={user_id}]")
                    except Exception as e:
                        logger.exception(f"Error sending notification to user {user_id}: {e}")
                        # Не прерываем выполнение - revoke уже выполнен
            else:
                # 4️⃣ ЛОГИРОВАНИЕ: если notify=False
                logger.info(f"ADMIN_REVOKE_NOTIFY_SKIPPED [user_id={user_id}]")
            
            # Audit log
            await database._log_audit_event_atomic_standalone(
                "admin_revoke_access",
                callback.from_user.id,
                user_id,
                f"Admin revoked access, notify_user={notify}"
            )
        
        # 3️⃣ ДОБАВИТЬ ОТДЕЛЬНЫЙ handler: корректно завершаем FSM
        await state.clear()
        logger.debug(f"FSM: AdminRevokeAccess cleared after revoke")
        
    except Exception as e:
        logger.exception(f"Error in callback_admin_revoke_notify: {e}")
        user = await database.get_user(callback.from_user.id)
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "errors.generic"), show_alert=True)
        await state.clear()
    """Обработчик кнопки 'Лишить доступа'"""
    # B3.3 - ADMIN OVERRIDE: Admin operations intentionally bypass system_state checks
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    await callback.answer()
    
    try:
        # P1 FIX: Safe callback parsing with validation
        # Expected format: admin_revoke:{notify|skip}:{telegram_user_id}
        parts = callback.data.split(":")
        
        if len(parts) != 3:
            logger.error(
                "INVALID_ADMIN_REVOKE_CALLBACK",
                extra={
                    "callback_data": callback.data,
                    "parts_count": len(parts),
                    "admin_id": callback.from_user.id,
                    "correlation_id": str(callback.message.message_id) if callback.message else None
                }
            )
            await callback.answer("Ошибка формата команды", show_alert=True)
            return
        
        _, notify_flag, user_id_raw = parts
        
        # Validate notify_flag
        if notify_flag not in ("notify", "skip"):
            logger.error(
                "INVALID_ADMIN_REVOKE_NOTIFY_FLAG",
                extra={
                    "callback_data": callback.data,
                    "notify_flag": notify_flag,
                    "admin_id": callback.from_user.id,
                    "correlation_id": str(callback.message.message_id) if callback.message else None
                }
            )
            await callback.answer("Ошибка формата команды", show_alert=True)
            return
        
        # Parse user_id safely
        try:
            user_id = int(user_id_raw)
        except ValueError:
            logger.error(
                "INVALID_ADMIN_REVOKE_USER_ID",
                extra={
                    "callback_data": callback.data,
                    "user_id_raw": user_id_raw,
                    "notify_flag": notify_flag,
                    "admin_id": callback.from_user.id,
                    "correlation_id": str(callback.message.message_id) if callback.message else None
                }
            )
            await callback.answer("Ошибка: неверный ID пользователя", show_alert=True)
            return
        
        notify = notify_flag == "notify"
        
        # Лишаем доступа
        revoked = await database.admin_revoke_access_atomic(
            telegram_id=user_id,
            admin_telegram_id=callback.from_user.id
        )
        
        if not revoked:
            # Нет активной подписки
            text = "❌ У пользователя нет активной подписки"
            await safe_edit_text(callback.message, text, reply_markup=get_admin_back_keyboard(language))
            await callback.answer("Нет активной подписки", show_alert=True)
        else:
            # Успешно
            if notify:
                text = "✅ Доступ отозван\nПользователь уведомлён."
            else:
                text = "✅ Доступ отозван"
            await safe_edit_text(callback.message, text, reply_markup=get_admin_back_keyboard(language))
            
            # Уведомляем пользователя только если notify=True
            if notify:
                try:
                    language = await resolve_user_language(user_id)
                    
                    user_text = i18n_get_text(language, "admin.revoke_user_notification", "admin_revoke_user_notification")
                    await bot.send_message(user_id, user_text)
                    logger.info(
                        "ADMIN_REVOKE_NOTIFICATION_SENT",
                        extra={
                            "admin_id": callback.from_user.id,
                            "user_id": user_id,
                            "correlation_id": str(callback.message.message_id) if callback.message else None
                        }
                    )
                except Exception as e:
                    logger.exception(
                        "ADMIN_REVOKE_NOTIFICATION_FAILED",
                        extra={
                            "admin_id": callback.from_user.id,
                            "user_id": user_id,
                            "error": str(e),
                            "correlation_id": str(callback.message.message_id) if callback.message else None
                        }
                    )
            else:
                logger.info(
                    "ADMIN_REVOKE_NOTIFY_SKIPPED",
                    extra={
                        "admin_id": callback.from_user.id,
                        "user_id": user_id,
                        "correlation_id": str(callback.message.message_id) if callback.message else None
                    }
                )
        
    except ValueError as e:
        # P1 FIX: ValueError уже обработан выше, но на всякий случай
        logger.error(
            "ADMIN_REVOKE_VALUE_ERROR",
            extra={
                "callback_data": callback.data if hasattr(callback, 'data') else None,
                "error": str(e),
                "admin_id": callback.from_user.id if hasattr(callback, 'from_user') else None,
                "correlation_id": str(callback.message.message_id) if callback.message else None
            }
        )
        await callback.answer("Ошибка: неверный формат команды", show_alert=True)
    except Exception as e:
        logger.exception(
            "ADMIN_REVOKE_ERROR",
            extra={
                "callback_data": callback.data if hasattr(callback, 'data') else None,
                "error": str(e),
                "admin_id": callback.from_user.id if hasattr(callback, 'from_user') else None,
                "correlation_id": str(callback.message.message_id) if callback.message else None
            }
        )
        await callback.answer("Ошибка. Проверь логи.", show_alert=True)


# ==================== ОБРАБОТЧИКИ ДЛЯ УПРАВЛЕНИЯ ПЕРСОНАЛЬНЫМИ СКИДКАМИ ====================

def get_admin_discount_percent_keyboard(user_id: int, language: str = "ru"):
    """Клавиатура для выбора процента скидки"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="10%", callback_data=f"admin:discount_percent:{user_id}:10"),
            InlineKeyboardButton(text="15%", callback_data=f"admin:discount_percent:{user_id}:15"),
        ],
        [
            InlineKeyboardButton(text="25%", callback_data=f"admin:discount_percent:{user_id}:25"),
            InlineKeyboardButton(text=i18n_get_text(language, "admin.discount_manual"), callback_data=f"admin:discount_percent_manual:{user_id}"),
        ],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:main")],
    ])
    return keyboard


def get_admin_discount_expires_keyboard(user_id: int, discount_percent: int, language: str = "ru"):
    """Клавиатура для выбора срока действия скидки"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=i18n_get_text(language, "admin.discount_expires_7"), callback_data=f"admin:discount_expires:{user_id}:{discount_percent}:7"),
            InlineKeyboardButton(text=i18n_get_text(language, "admin.discount_expires_30"), callback_data=f"admin:discount_expires:{user_id}:{discount_percent}:30"),
        ],
        [
            InlineKeyboardButton(text=i18n_get_text(language, "admin.discount_expires_unlimited"), callback_data=f"admin:discount_expires:{user_id}:{discount_percent}:0"),
            InlineKeyboardButton(text=i18n_get_text(language, "admin.discount_manual"), callback_data=f"admin:discount_expires_manual:{user_id}:{discount_percent}"),
        ],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:main")],
    ])
    return keyboard


@router.callback_query(F.data.startswith("admin:discount_create:"))
async def callback_admin_discount_create(callback: CallbackQuery):
    """Обработчик кнопки 'Назначить скидку'"""
    # B3.3 - ADMIN OVERRIDE: Admin operations intentionally bypass system_state checks
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    try:
        user_id = int(callback.data.split(":")[2])
        
        # Проверяем, есть ли уже скидка
        existing_discount = await database.get_user_discount(user_id)
        if existing_discount:
            discount_percent = existing_discount["discount_percent"]
            text = f"❌ У пользователя уже есть персональная скидка {discount_percent}%.\n\nСначала удалите существующую скидку."
            await safe_edit_text(callback.message, text, reply_markup=get_admin_back_keyboard(language))
            await callback.answer("Скидка уже существует", show_alert=True)
            return
        
        text = f"🎯 Назначить скидку\n\nВыберите процент скидки:"
        await callback.message.edit_text(text, reply_markup=get_admin_discount_percent_keyboard(user_id))
        await callback.answer()
        
    except Exception as e:
        logging.exception(f"Error in callback_admin_discount_create: {e}")
        await callback.answer("Ошибка. Проверь логи.", show_alert=True)


@router.callback_query(F.data.startswith("admin:discount_percent:"))
async def callback_admin_discount_percent(callback: CallbackQuery):
    """Обработчик выбора процента скидки"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    try:
        parts = callback.data.split(":")
        user_id = int(parts[2])
        discount_percent = int(parts[3])
        
        text = f"🎯 Назначить скидку {discount_percent}%\n\nВыберите срок действия скидки:"
        await callback.message.edit_text(text, reply_markup=get_admin_discount_expires_keyboard(user_id, discount_percent))
        await callback.answer()
        
    except Exception as e:
        logging.exception(f"Error in callback_admin_discount_percent: {e}")
        await callback.answer("Ошибка. Проверь логи.", show_alert=True)


@router.callback_query(F.data.startswith("admin:discount_percent_manual:"))
async def callback_admin_discount_percent_manual(callback: CallbackQuery, state: FSMContext):
    """Обработчик для ввода процента скидки вручную"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    try:
        user_id = int(callback.data.split(":")[2])
        
        await state.update_data(discount_user_id=user_id)
        await state.set_state(AdminDiscountCreate.waiting_for_percent)
        
        text = "🎯 Назначить скидку\n\nВведите процент скидки (число от 1 до 99):"
        await safe_edit_text(callback.message, text, reply_markup=get_admin_back_keyboard(language))
        await callback.answer()
        
    except Exception as e:
        logging.exception(f"Error in callback_admin_discount_percent_manual: {e}")
        await callback.answer("Ошибка. Проверь логи.", show_alert=True)


@router.message(AdminDiscountCreate.waiting_for_percent)
async def process_admin_discount_percent(message: Message, state: FSMContext):
    """Обработка введённого процента скидки"""
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(message.from_user.id)
        await message.answer(i18n_get_text(language, "admin.access_denied"))
        await state.clear()
        return
    
    try:
        data = await state.get_data()
        user_id = data.get("discount_user_id")
        
        try:
            discount_percent = int(message.text.strip())
            if discount_percent < 1 or discount_percent > 99:
                await message.answer("Процент скидки должен быть от 1 до 99. Попробуйте снова:")
                return
        except ValueError:
            await message.answer("Введите число от 1 до 99:")
            return
        
        await state.update_data(discount_percent=discount_percent)
        
        text = f"🎯 Назначить скидку {discount_percent}%\n\nВыберите срок действия скидки:"
        await message.answer(text, reply_markup=get_admin_discount_expires_keyboard(user_id, discount_percent))
        await state.set_state(AdminDiscountCreate.waiting_for_expires)
        
    except Exception as e:
        logging.exception(f"Error in process_admin_discount_percent: {e}")
        await message.answer("Ошибка. Проверь логи.")
        await state.clear()


@router.callback_query(F.data.startswith("admin:discount_expires:"))
async def callback_admin_discount_expires(callback: CallbackQuery, bot: Bot):
    """Обработчик выбора срока действия скидки"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    try:
        parts = callback.data.split(":")
        user_id = int(parts[2])
        discount_percent = int(parts[3])
        expires_days = int(parts[4])
        
        # Рассчитываем expires_at
        expires_at = None
        if expires_days > 0:
            expires_at = datetime.now() + timedelta(days=expires_days)
        
        # Создаём скидку
        success = await database.create_user_discount(
            telegram_id=user_id,
            discount_percent=discount_percent,
            expires_at=expires_at,
            created_by=callback.from_user.id
        )
        
        if success:
            expires_str = expires_at.strftime("%d.%m.%Y %H:%M") if expires_at else "бессрочно"
            text = f"✅ Персональная скидка {discount_percent}% назначена\n\nСрок действия: {expires_str}"
            await safe_edit_text(callback.message, text, reply_markup=get_admin_back_keyboard(language))
            await callback.answer("Скидка назначена", show_alert=True)
        else:
            text = "❌ Ошибка при создании скидки"
            await safe_edit_text(callback.message, text, reply_markup=get_admin_back_keyboard(language))
            user = await database.get_user(callback.from_user.id)
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "errors.generic"), show_alert=True)
        
    except Exception as e:
        logging.exception(f"Error in callback_admin_discount_expires: {e}")
        await callback.answer("Ошибка. Проверь логи.", show_alert=True)


@router.callback_query(F.data.startswith("admin:discount_expires_manual:"))
async def callback_admin_discount_expires_manual(callback: CallbackQuery, state: FSMContext):
    """Обработчик для ввода срока действия скидки вручную"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    try:
        parts = callback.data.split(":")
        user_id = int(parts[2])
        discount_percent = int(parts[3])
        
        await state.update_data(discount_user_id=user_id, discount_percent=discount_percent)
        await state.set_state(AdminDiscountCreate.waiting_for_expires)
        
        text = "🎯 Назначить скидку\n\nВведите количество дней действия скидки (или 0 для бессрочной):"
        await safe_edit_text(callback.message, text, reply_markup=get_admin_back_keyboard(language))
        await callback.answer()
        
    except Exception as e:
        logging.exception(f"Error in callback_admin_discount_expires_manual: {e}")
        await callback.answer("Ошибка. Проверь логи.", show_alert=True)


@router.message(AdminDiscountCreate.waiting_for_expires)
async def process_admin_discount_expires(message: Message, state: FSMContext, bot: Bot):
    """Обработка введённого срока действия скидки"""
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(message.from_user.id)
        await message.answer(i18n_get_text(language, "admin.access_denied"))
        await state.clear()
        return
    
    try:
        data = await state.get_data()
        user_id = data.get("discount_user_id")
        discount_percent = data.get("discount_percent")
        
        try:
            expires_days = int(message.text.strip())
            if expires_days < 0:
                await message.answer("Количество дней должно быть неотрицательным. Попробуйте снова:")
                return
        except ValueError:
            await message.answer("Введите число (количество дней или 0 для бессрочной):")
            return
        
        # Рассчитываем expires_at
        expires_at = None
        if expires_days > 0:
            expires_at = datetime.now() + timedelta(days=expires_days)
        
        # Создаём скидку
        success = await database.create_user_discount(
            telegram_id=user_id,
            discount_percent=discount_percent,
            expires_at=expires_at,
            created_by=message.from_user.id
        )
        
        if success:
            expires_str = expires_at.strftime("%d.%m.%Y %H:%M") if expires_at else "бессрочно"
            text = f"✅ Персональная скидка {discount_percent}% назначена\n\nСрок действия: {expires_str}"
            await message.answer(text, reply_markup=get_admin_back_keyboard(language))
        else:
            text = "❌ Ошибка при создании скидки"
            await message.answer(text, reply_markup=get_admin_back_keyboard(language))
        
        await state.clear()
        
    except Exception as e:
        logging.exception(f"Error in process_admin_discount_expires: {e}")
        await message.answer("Ошибка. Проверь логи.")
        await state.clear()


@router.callback_query(F.data.startswith("admin:discount_delete:"))
async def callback_admin_discount_delete(callback: CallbackQuery):
    """Обработчик кнопки 'Удалить скидку'"""
    # B3.3 - ADMIN OVERRIDE: Admin operations intentionally bypass system_state checks
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    try:
        user_id = int(callback.data.split(":")[2])
        
        # Удаляем скидку
        success = await database.delete_user_discount(
            telegram_id=user_id,
            deleted_by=callback.from_user.id
        )
        
        if success:
            text = "✅ Персональная скидка удалена"
            await safe_edit_text(callback.message, text, reply_markup=get_admin_back_keyboard(language))
            await callback.answer("Скидка удалена", show_alert=True)
        else:
            text = "❌ Скидка не найдена или уже удалена"
            await safe_edit_text(callback.message, text, reply_markup=get_admin_back_keyboard(language))
            await callback.answer("Скидка не найдена", show_alert=True)
        
    except Exception as e:
        logging.exception(f"Error in callback_admin_discount_delete: {e}")
        await callback.answer("Ошибка. Проверь логи.", show_alert=True)


# ==================== ОБРАБОТЧИКИ ДЛЯ УПРАВЛЕНИЯ VIP-СТАТУСОМ ====================

async def _show_admin_user_card(message_or_callback, user_id: int, admin_telegram_id: int):
    """Вспомогательная функция для отображения карточки пользователя администратору"""
    # B3.3 - ADMIN OVERRIDE: Admin operations intentionally bypass system_state checks
    language = await resolve_user_language(admin_telegram_id)
    try:
        overview = await admin_service.get_admin_user_overview(user_id)
    except UserNotFoundError:
        if hasattr(message_or_callback, 'edit_text'):
            await message_or_callback.edit_text(
                i18n_get_text(language, "admin.user_not_found"),
                reply_markup=get_admin_back_keyboard(language)
            )
        else:
            await message_or_callback.answer("❌ Пользователь не найден")
        return
    
    # Получаем доступные действия через admin service
    actions = admin_service.get_admin_user_actions(overview)
    
    # Формируем карточку пользователя (только форматирование)
    text = "👤 Пользователь\n\n"
    text += f"Telegram ID: {overview.user['telegram_id']}\n"
    username_display = overview.user.get('username') or 'не указан'
    text += f"Username: @{username_display}\n"
    
    # Язык
    user_language = overview.user.get('language') or 'ru'
    language_display = i18n_get_text("ru", f"lang.button_{user_language}")
    text += f"Язык: {language_display}\n"
    
    # Дата регистрации
    created_at = overview.user.get('created_at')
    if created_at:
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
        created_str = created_at.strftime("%d.%m.%Y %H:%M")
        text += f"Дата регистрации: {created_str}\n"
    else:
        text += "Дата регистрации: —\n"
    
    text += "\n"
    
    # Информация о подписке
    if overview.subscription:
        expires_at = overview.subscription_status.expires_at
        if expires_at:
            expires_str = expires_at.strftime("%d.%m.%Y %H:%M")
        else:
            expires_str = "—"
        
        if overview.subscription_status.is_active:
            text += "Статус подписки: ✅ Активна\n"
        else:
            text += "Статус подписки: ⛔ Истекла\n"
        
        text += f"Срок действия: до {expires_str}\n"
        text += f"VPN-ключ: {overview.subscription.get('vpn_key', '—')}\n"
    else:
        text += "Статус подписки: ❌ Нет подписки\n"
        text += "VPN-ключ: —\n"
        text += "Срок действия: —\n"
    
    # Статистика
    text += f"\nКоличество продлений: {overview.stats['renewals_count']}\n"
    text += f"Количество перевыпусков: {overview.stats['reissues_count']}\n"
    
    # Персональная скидка
    if overview.user_discount:
        discount_percent = overview.user_discount["discount_percent"]
        expires_at_discount = overview.user_discount.get("expires_at")
        if expires_at_discount:
            if isinstance(expires_at_discount, str):
                expires_at_discount = datetime.fromisoformat(expires_at_discount.replace('Z', '+00:00'))
            expires_str = expires_at_discount.strftime("%d.%m.%Y %H:%M")
            text += f"\n🎯 Персональная скидка: {discount_percent}% (до {expires_str})\n"
        else:
            text += f"\n🎯 Персональная скидка: {discount_percent}% (бессрочно)\n"
    
    # VIP-статус
    if overview.is_vip:
        text += f"\n👑 VIP-статус: активен\n"
    
    # Отображаем карточку
    keyboard = get_admin_user_keyboard(
        has_active_subscription=overview.subscription_status.is_active,
        user_id=overview.user["telegram_id"],
        has_discount=overview.user_discount is not None,
        is_vip=overview.is_vip,
        language=language
    )
    
    if hasattr(message_or_callback, 'edit_text'):
        await message_or_callback.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    else:
        await message_or_callback.answer(text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data.startswith("admin:vip_grant:"))
async def callback_admin_vip_grant(callback: CallbackQuery):
    """Обработчик кнопки 'Выдать VIP'"""
    # B3.3 - ADMIN OVERRIDE: Admin operations intentionally bypass system_state checks
    user = await database.get_user(callback.from_user.id)
    language = await resolve_user_language(callback.from_user.id)
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    try:
        user_id = int(callback.data.split(":")[2])
        
        # Проверяем, есть ли уже VIP-статус
        existing_vip = await database.is_vip_user(user_id)
        if existing_vip:
            # Если уже есть VIP, просто обновляем карточку
            await _show_admin_user_card(callback.message, user_id, callback.from_user.id)
            await callback.answer("VIP уже назначен", show_alert=True)
            return
        
        # Назначаем VIP-статус
        success = await database.grant_vip_status(
            telegram_id=user_id,
            granted_by=callback.from_user.id
        )
        
        if success:
            # После успешного назначения VIP обновляем карточку пользователя
            await _show_admin_user_card(callback.message, user_id, callback.from_user.id)
            await callback.answer("✅ VIP-статус выдан", show_alert=True)
        else:
            text = "❌ Ошибка при назначении VIP-статуса"
            await safe_edit_text(callback.message, text, reply_markup=get_admin_back_keyboard(language))
            user = await database.get_user(callback.from_user.id)
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "errors.generic"), show_alert=True)
        
    except Exception as e:
        logging.exception(f"Error in callback_admin_vip_grant: {e}")
        await callback.answer("Ошибка. Проверь логи.", show_alert=True)


@router.callback_query(F.data.startswith("admin:vip_revoke:"))
async def callback_admin_vip_revoke(callback: CallbackQuery):
    """Обработчик кнопки 'Снять VIP'"""
    # B3.3 - ADMIN OVERRIDE: Admin operations intentionally bypass system_state checks
    user = await database.get_user(callback.from_user.id)
    language = await resolve_user_language(callback.from_user.id)
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    try:
        user_id = int(callback.data.split(":")[2])
        
        # Отзываем VIP-статус
        success = await database.revoke_vip_status(
            telegram_id=user_id,
            revoked_by=callback.from_user.id
        )
        
        if success:
            # После успешного снятия VIP обновляем карточку пользователя
            await _show_admin_user_card(callback.message, user_id, callback.from_user.id)
            await callback.answer("✅ VIP-статус снят", show_alert=True)
        else:
            text = "❌ VIP-статус не найден или уже снят"
            await safe_edit_text(callback.message, text, reply_markup=get_admin_back_keyboard(language))
            await callback.answer("VIP не найден", show_alert=True)
        
    except Exception as e:
        logging.exception(f"Error in callback_admin_vip_revoke: {e}")
        await callback.answer("Ошибка. Проверь логи.", show_alert=True)


@router.callback_query(F.data.startswith("admin:user_reissue:"))
async def callback_admin_user_reissue(callback: CallbackQuery):
    """Перевыпуск ключа из админ-дашборда"""
    # B3.3 - ADMIN OVERRIDE: Admin operations intentionally bypass system_state checks
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    try:
        # Получаем user_id из callback_data
        target_user_id = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer("Ошибка: неверный формат команды", show_alert=True)
        return
    
    try:
        admin_telegram_id = callback.from_user.id
        
        # Атомарно перевыпускаем ключ
        result = await database.reissue_vpn_key_atomic(target_user_id, admin_telegram_id)
        new_vpn_key, old_vpn_key = result
        
        if new_vpn_key is None:
            await callback.answer("Не удалось перевыпустить ключ. Нет активной подписки или ошибка создания ключа.", show_alert=True)
            return
        
        # Обновляем информацию о пользователе
        user = await database.get_user(target_user_id)
        subscription = await database.get_subscription(target_user_id)
        
        text = "👤 Информация о пользователе\n\n"
        text += f"Telegram ID: {target_user_id}\n"
        text += f"Username: @{user.get('username', 'не указан') if user else 'не указан'}\n"
        text += "\n"
        
        if subscription:
            expires_at = subscription["expires_at"]
            if isinstance(expires_at, str):
                expires_at = datetime.fromisoformat(expires_at)
            expires_str = expires_at.strftime("%d.%m.%Y %H:%M")
            
            text += "Статус подписки: ✅ Активна\n"
            text += f"Срок действия: до {expires_str}\n"
            text += f"VPN-ключ: <code>{new_vpn_key}</code>\n"
            text += f"\n✅ Ключ перевыпущен!\nСтарый ключ: {old_vpn_key[:20]}..."
            
            # Проверяем VIP-статус и скидку
            is_vip = await database.is_vip_user(target_user_id)
            has_discount = await database.get_user_discount(target_user_id) is not None
            
            await callback.message.edit_text(text, reply_markup=get_admin_user_keyboard(has_active_subscription=True, user_id=target_user_id, has_discount=has_discount, is_vip=is_vip), parse_mode="HTML")
        
        await callback.answer("Ключ успешно перевыпущен")
        
        # Уведомляем пользователя
        try:
            user_text = get_reissue_notification_text(new_vpn_key)
            keyboard = get_reissue_notification_keyboard()
            await callback.bot.send_message(target_user_id, user_text, reply_markup=keyboard, parse_mode="HTML")
        except Exception as e:
            logging.error(f"Error sending reissue notification to user {target_user_id}: {e}")
        
    except Exception as e:
        logging.exception(f"Error in callback_admin_user_reissue: {e}")
        await callback.answer("Ошибка при перевыпуске ключа", show_alert=True)


@router.callback_query(F.data == "admin:system")
async def callback_admin_system(callback: CallbackQuery):
    """
    PART A.3: Admin system status dashboard with severity and error summary.
    """
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    try:
        from app.core.system_state import SystemState, SystemSeverity, recalculate_from_runtime
        
        # PART A.3: Get current system state
        system_state = recalculate_from_runtime()
        
        # PART A.3: Count pending activations
        pending_activations = 0
        try:
            pool = await database.get_pool()
            async with pool.acquire() as conn:
                pending_activations = await conn.fetchval(
                    "SELECT COUNT(*) FROM subscriptions WHERE activation_status = 'pending'"
                ) or 0
        except Exception:
            pass
        
        # PART A.3: Calculate severity
        severity = system_state.get_severity(pending_activations=pending_activations)
        
        # PART A.3: Get error summary
        errors = system_state.get_error_summary()
        
        # PART A.3: Build status text with severity color
        severity_emoji = {
            SystemSeverity.GREEN: "🟢",
            SystemSeverity.YELLOW: "🟡",
            SystemSeverity.RED: "🔴"
        }
        
        text = f"{severity_emoji[severity]} Система ({severity.value.upper()})\n\n"
        
        # PART A.3: Component summary
        text += "📊 Компоненты:\n"
        text += f"  • База данных: {system_state.database.status.value}\n"
        text += f"  • Платежи: {system_state.payments.status.value}\n"
        text += f"  • VPN API: {system_state.vpn_api.status.value}\n"
        text += f"  • Ожидающих активаций: {pending_activations}\n\n"
        
        # PART B.4: Error summary (only actionable issues)
        if errors:
            text += "⚠️ Проблемы:\n"
            for error in errors:
                text += f"  • {error['component']}: {error['reason']}\n"
                text += f"    → {error['impact']}\n"
            text += "\n"
        else:
            text += "✅ Проблем не обнаружено\n\n"
        
        # Uptime
        uptime_seconds = int(time.time() - _bot_start_time)
        uptime_days = uptime_seconds // 86400
        uptime_hours = (uptime_seconds % 86400) // 3600
        uptime_minutes = (uptime_seconds % 3600) // 60
        uptime_str = f"{uptime_days}д {uptime_hours}ч {uptime_minutes}м"
        text += f"⏱ Время работы: {uptime_str}"
        
        # PART C.5: Add test menu button
        user = await database.get_user(callback.from_user.id)
        language = await resolve_user_language(callback.from_user.id)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.test_menu"), callback_data="admin:test_menu")],
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:main")],
        ])
        
        await safe_edit_text(callback.message, text, reply_markup=keyboard)
        await callback.answer()
        
        # Логируем просмотр системной информации
        await database._log_audit_event_atomic_standalone(
            "admin_view_system", 
            callback.from_user.id, 
            None, 
            f"Admin viewed system status: severity={severity.value}, errors={len(errors)}"
        )
        
    except Exception as e:
        logging.exception(f"Error in callback_admin_system: {e}")
        await callback.answer("Ошибка при получении системной информации", show_alert=True)


@router.callback_query(F.data == "admin:test_menu")
async def callback_admin_test_menu(callback: CallbackQuery):
    """
    PART C.5: Admin test menu for testing notifications.
    """
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    text = "🧪 Тестовое меню\n\n"
    text += "Выберите тест для выполнения:\n"
    text += "• Тесты выполняются без реальных платежей\n"
    text += "• VPN API не вызывается\n"
    text += "• Все действия логируются в audit_log(type=test)"
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.test_trial"), callback_data="admin:test:trial_activation")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.test_first_purchase"), callback_data="admin:test:first_purchase")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.test_renewal"), callback_data="admin:test:renewal")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.test_reminders"), callback_data="admin:test:reminders")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:system")],
    ])
    
    await safe_edit_text(callback.message, text, reply_markup=keyboard)
    await callback.answer()
    
    await database._log_audit_event_atomic_standalone(
        "admin_test_menu_viewed",
        callback.from_user.id,
        None,
        "Admin viewed test menu"
    )


@router.callback_query(F.data.startswith("admin:test:"))
async def callback_admin_test(callback: CallbackQuery, bot: Bot):
    """
    PART C.5: Execute admin test actions.
    """
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    test_type = callback.data.split(":")[-1]
    
    try:
        # PART C.5: All tests are logged with type=test
        test_user_id = callback.from_user.id  # Use admin ID as test user
        
        if test_type == "trial_activation":
            # Test trial activation notification
            await bot.send_message(
                test_user_id,
                "🎁 [ТЕСТ] Уведомление об активации триала\n\n"
                "Ваш триал активирован! Пользуйтесь VPN бесплатно."
            )
            result_text = "✅ Тест активации триала выполнен"
            
        elif test_type == "first_purchase":
            # Test first purchase notification
            await bot.send_message(
                test_user_id,
                "💰 [ТЕСТ] Уведомление о первой покупке\n\n"
                "Спасибо за покупку! Ваша подписка активирована."
            )
            result_text = "✅ Тест уведомления о первой покупке выполнен"
            
        elif test_type == "renewal":
            # Test renewal notification
            await bot.send_message(
                test_user_id,
                "🔄 [ТЕСТ] Уведомление о продлении\n\n"
                "Ваша подписка автоматически продлена."
            )
            result_text = "✅ Тест уведомления о продлении выполнен"
            
        elif test_type == "reminders":
            # Test reminder notifications
            await bot.send_message(
                test_user_id,
                "⏰ [ТЕСТ] Напоминание о подписке\n\n"
                "Ваша подписка скоро истечёт. Продлите её сейчас!"
            )
            result_text = "✅ Тест напоминаний выполнен"
            
        else:
            result_text = "❌ Неизвестный тип теста"
        
        # PART C.5: Log test action
        await database._log_audit_event_atomic_standalone(
            "admin_test_executed",
            callback.from_user.id,
            None,
            f"Test type: {test_type}, result: {result_text}"
        )
        
        await callback.answer(result_text, show_alert=True)
        await callback_admin_test_menu(callback)
        
    except Exception as e:
        logger.exception(f"Error in admin test {test_type}: {e}")
        await callback.answer(f"Ошибка выполнения теста: {e}", show_alert=True)


@router.callback_query(F.data == "admin:export")
async def callback_admin_export(callback: CallbackQuery):
    """Раздел Экспорт данных"""
    user = await database.get_user(callback.from_user.id)
    language = await resolve_user_language(callback.from_user.id)
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    text = i18n_get_text(language, "admin.export_prompt")
    await callback.message.edit_text(text, reply_markup=get_admin_export_keyboard(language))
    await callback.answer()


@router.callback_query(F.data.startswith("admin:export:"))
async def callback_admin_export_data(callback: CallbackQuery):
    """Обработка экспорта данных"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    await callback.answer()
    
    try:
        export_type = callback.data.split(":")[2]  # users или subscriptions
        
        # Получаем данные из БД
        if export_type == "users":
            data = await database.get_all_users_for_export()
            filename = "users_export.csv"
            headers = ["ID", "Telegram ID", "Username", "Language", "Created At"]
        elif export_type == "subscriptions":
            data = await database.get_active_subscriptions_for_export()
            filename = "active_subscriptions_export.csv"
            headers = ["ID", "Telegram ID", "VPN Key", "Expires At", "Reminder Sent"]
        else:
            await callback.message.answer("Неверный тип экспорта")
            return
        
        if not data:
            await callback.message.answer("Нет данных для экспорта")
            return
        
        # Создаём временный файл
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, encoding='utf-8', newline='') as tmp_file:
            csv_file_path = tmp_file.name
            
            # Записываем CSV
            writer = csv.writer(tmp_file)
            writer.writerow(headers)
            
            # Маппинг заголовков на ключи в данных
            if export_type == "users":
                key_mapping = {
                    "ID": "id",
                    "Telegram ID": "telegram_id",
                    "Username": "username",
                    "Language": "language",
                    "Created At": "created_at"
                }
            else:  # subscriptions
                key_mapping = {
                    "ID": "id",
                    "Telegram ID": "telegram_id",
                    "VPN Key": "vpn_key",
                    "Expires At": "expires_at",
                    "Reminder Sent": "reminder_sent"
                }
            
            for row in data:
                csv_row = []
                for header in headers:
                    key = key_mapping[header]
                    value = row.get(key)
                    
                    if key == "created_at" or key == "expires_at":
                        # Форматируем дату
                        if value:
                            if isinstance(value, datetime):
                                csv_row.append(value.strftime("%Y-%m-%d %H:%M:%S"))
                            elif isinstance(value, str):
                                csv_row.append(value)
                            else:
                                csv_row.append(str(value))
                        else:
                            csv_row.append("")
                    elif key == "reminder_sent":
                        # Преобразуем boolean в строку
                        csv_row.append("Да" if value else "Нет")
                    else:
                        csv_row.append(str(value) if value is not None else "")
                writer.writerow(csv_row)
        
        # Отправляем файл
        try:
            file_to_send = FSInputFile(csv_file_path, filename=filename)
            await callback.bot.send_document(
                config.ADMIN_TELEGRAM_ID,
                file_to_send,
                caption=f"📤 Экспорт: {export_type}"
            )
            await callback.message.answer("✅ Файл отправлен")
            
            # Логируем экспорт
            await database._log_audit_event_atomic_standalone(
                "admin_export_data",
                callback.from_user.id,
                None,
                f"Exported {export_type}: {len(data)} records"
            )
        finally:
            # Удаляем временный файл
            try:
                os.unlink(csv_file_path)
            except Exception as e:
                logging.error(f"Error deleting temp file {csv_file_path}: {e}")
        
    except Exception as e:
        logging.exception(f"Error in callback_admin_export_data: {e}")
        await callback.message.answer("Ошибка при экспорте данных. Проверь логи.")


@router.callback_query(F.data == "admin:incident")
async def callback_admin_incident(callback: CallbackQuery):
    """Раздел управления инцидентом"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    await callback.answer()
    
    incident = await database.get_incident_settings()
    is_active = incident["is_active"]
    incident_text = incident.get("incident_text") or "Текст не указан"
    
    status_text = i18n_get_text(language, "admin.incident_status_on", "admin_incident_status_on") if is_active else i18n_get_text(language, "admin.incident_status_off", "admin_incident_status_off")
    incident_title = i18n_get_text(language, "admin.incident_title", "admin_incident_title")
    incident_label = i18n_get_text(language, "admin.incident_text_label", "admin_incident_text_label")
    text = f"{incident_title}\n\n{status_text}\n\n{incident_label}\n{incident_text}"
    
    toggle_text = i18n_get_text(language, "admin.incident_enable", "admin_incident_enable") if not is_active else i18n_get_text(language, "admin.incident_disable", "admin_incident_disable")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=toggle_text,
            callback_data="admin:incident:toggle"
        )],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.incident_edit"), callback_data="admin:incident:edit")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:main")],
    ])
    
    await safe_edit_text(callback.message, text, reply_markup=keyboard)
    
    # Логируем действие
    await database._log_audit_event_atomic_standalone("admin_view_incident", callback.from_user.id, None, f"Viewed incident settings (active: {is_active})")


@router.callback_query(F.data == "admin:incident:toggle")
async def callback_admin_incident_toggle(callback: CallbackQuery):
    """Переключение режима инцидента"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    await callback.answer()
    
    incident = await database.get_incident_settings()
    new_state = not incident["is_active"]
    
    await database.set_incident_mode(new_state)
    
    action = "включен" if new_state else "выключен"
    await callback.answer(f"Режим инцидента {action}", show_alert=True)
    
    # Логируем действие
    await database._log_audit_event_atomic_standalone(
        "incident_mode_toggled",
        callback.from_user.id,
        None,
        f"Incident mode {'enabled' if new_state else 'disabled'}"
    )
    
    # Возвращаемся к экрану инцидента
    await callback_admin_incident(callback)


@router.callback_query(F.data == "admin:incident:edit")
async def callback_admin_incident_edit(callback: CallbackQuery, state: FSMContext):
    """Начало редактирования текста инцидента"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    await callback.answer()
    
    text = "Введите текст инцидента (или отправьте /cancel для отмены):"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.cancel"), callback_data="admin:incident")],
    ])
    
    await safe_edit_text(callback.message, text, reply_markup=keyboard)
    await state.set_state(IncidentEdit.waiting_for_text)


@router.message(IncidentEdit.waiting_for_text)
async def process_incident_text(message: Message, state: FSMContext):
    """Обработка текста инцидента"""
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        return
    
    if message.text and message.text.startswith("/cancel"):
        await state.clear()
        await message.answer("Отменено")
        return
    
    incident_text = message.text
    
    # Включаем режим инцидента и сохраняем текст
    await database.set_incident_mode(True, incident_text)
    
    await message.answer(f"✅ Текст инцидента сохранён. Режим инцидента включён.")
    
    # Логируем действие
    await database._log_audit_event_atomic_standalone(
        "incident_text_updated",
        message.from_user.id,
        None,
        f"Incident text updated: {incident_text[:50]}..."
    )
    
    await state.clear()


@router.callback_query(F.data == "admin:broadcast")
async def callback_admin_broadcast(callback: CallbackQuery):
    """Раздел уведомлений"""
    user = await database.get_user(callback.from_user.id)
    language = await resolve_user_language(callback.from_user.id)
    
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    text = i18n_get_text(language, "broadcast._section_title")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n_get_text(language, "broadcast._create"), callback_data="broadcast:create")],
        [InlineKeyboardButton(text=i18n_get_text(language, "broadcast._ab_stats"), callback_data="broadcast:ab_stats")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:main")],
    ])
    await safe_edit_text(callback.message, text, reply_markup=keyboard)
    await callback.answer()
    
    # Логируем действие
    await database._log_audit_event_atomic_standalone("admin_broadcast_view", callback.from_user.id, None, "Admin viewed broadcast section")


@router.callback_query(F.data == "broadcast:create")
async def callback_broadcast_create(callback: CallbackQuery, state: FSMContext):
    """Начать создание уведомления"""
    user = await database.get_user(callback.from_user.id)
    language = await resolve_user_language(callback.from_user.id)
    
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    await callback.answer()
    await state.set_state(BroadcastCreate.waiting_for_title)
    await callback.message.answer(
        i18n_get_text(language, "broadcast._enter_title")
    )


@router.message(BroadcastCreate.waiting_for_title)
async def process_broadcast_title(message: Message, state: FSMContext):
    """Обработка заголовка уведомления"""
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        return
    language = await resolve_user_language(message.from_user.id)
    
    await state.update_data(title=message.text)
    await state.set_state(BroadcastCreate.waiting_for_test_type)
    await message.answer(
        i18n_get_text(language, "broadcast._select_type"),
        reply_markup=get_broadcast_test_type_keyboard(language)
    )


@router.callback_query(F.data.startswith("broadcast_test_type:"))
async def callback_broadcast_test_type(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора типа тестирования"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    await callback.answer()
    language = await resolve_user_language(callback.from_user.id)
    test_type = callback.data.split(":")[1]
    
    await state.update_data(is_ab_test=(test_type == "ab"))
    
    if test_type == "ab":
        await state.set_state(BroadcastCreate.waiting_for_message_a)
        await callback.message.edit_text(
            i18n_get_text(language, "broadcast._enter_variant_a")
        )
    else:
        await state.set_state(BroadcastCreate.waiting_for_message)
        await callback.message.edit_text(
            i18n_get_text(language, "broadcast._enter_message")
        )


@router.message(BroadcastCreate.waiting_for_message_a)
async def process_broadcast_message_a(message: Message, state: FSMContext):
    """Обработка текста варианта A"""
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        return
    language = await resolve_user_language(message.from_user.id)
    
    await state.update_data(message_a=message.text)
    await state.set_state(BroadcastCreate.waiting_for_message_b)
    await message.answer(
        i18n_get_text(language, "broadcast._enter_variant_b")
    )


@router.message(BroadcastCreate.waiting_for_message_b)
async def process_broadcast_message_b(message: Message, state: FSMContext):
    """Обработка текста варианта B"""
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        return
    language = await resolve_user_language(message.from_user.id)
    
    await state.update_data(message_b=message.text)
    await state.set_state(BroadcastCreate.waiting_for_type)
    await message.answer(
        i18n_get_text(language, "broadcast._select_type"),
        reply_markup=get_broadcast_type_keyboard(language)
    )


@router.message(BroadcastCreate.waiting_for_message)
async def process_broadcast_message(message: Message, state: FSMContext):
    """Обработка текста уведомления"""
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        return
    language = await resolve_user_language(message.from_user.id)
    
    await state.update_data(message=message.text)
    await state.set_state(BroadcastCreate.waiting_for_type)
    await message.answer(
        i18n_get_text(language, "broadcast._select_type"),
        reply_markup=get_broadcast_type_keyboard(language)
    )


@router.callback_query(F.data.startswith("broadcast_type:"))
async def callback_broadcast_type(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора типа уведомления"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    await callback.answer()
    broadcast_type = callback.data.split(":")[1]
    
    data = await state.get_data()
    title = data.get("title")
    message_text = data.get("message")
    
    # Формируем предпросмотр
    type_emoji = {
        "info": "ℹ️",
        "maintenance": "🔧",
        "security": "🔒",
        "promo": "🎯"
    }
    type_name = {
        "info": "Информация",
        "maintenance": "Технические работы",
        "security": "Безопасность",
        "promo": "Промо"
    }
    
    await state.update_data(type=broadcast_type)
    await state.set_state(BroadcastCreate.waiting_for_segment)
    
    language = await resolve_user_language(callback.from_user.id)
    
    await callback.message.edit_text(
        i18n_get_text(language, "broadcast._select_segment"),
        reply_markup=get_broadcast_segment_keyboard(language)
    )


@router.callback_query(F.data.startswith("broadcast_segment:"))
async def callback_broadcast_segment(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора сегмента получателей"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    await callback.answer()
    segment = callback.data.split(":")[1]
    
    data = await state.get_data()
    title = data.get("title")
    message_text = data.get("message")
    broadcast_type = data.get("type")
    
    # Формируем предпросмотр
    type_emoji = {
        "info": "ℹ️",
        "maintenance": "🔧",
        "security": "🔒",
        "promo": "🎯"
    }
    type_name = {
        "info": "Информация",
        "maintenance": "Технические работы",
        "security": "Безопасность",
        "promo": "Промо"
    }
    segment_name = {
        "all_users": "Все пользователи",
        "active_subscriptions": "Только активные подписки"
    }
    
    data_for_preview = await state.get_data()
    is_ab_test = data_for_preview.get("is_ab_test", False)
    
    if is_ab_test:
        message_a = data_for_preview.get("message_a", "")
        message_b = data_for_preview.get("message_b", "")
        preview_text = (
            f"{type_emoji.get(broadcast_type, '📢')} {title}\n\n"
            f"🔬 A/B ТЕСТ\n\n"
            f"Вариант A:\n{message_a}\n\n"
            f"Вариант B:\n{message_b}\n\n"
            f"Тип: {type_name.get(broadcast_type, broadcast_type)}\n"
            f"Сегмент: {segment_name.get(segment, segment)}"
        )
    else:
        message_text = data_for_preview.get("message", "")
        preview_text = (
            f"{type_emoji.get(broadcast_type, '📢')} {title}\n\n"
            f"{message_text}\n\n"
            f"Тип: {type_name.get(broadcast_type, broadcast_type)}\n"
            f"Сегмент: {segment_name.get(segment, segment)}"
        )
    
    await state.update_data(segment=segment)
    await state.set_state(BroadcastCreate.waiting_for_confirm)
    
    language = await resolve_user_language(callback.from_user.id)
    
    preview_confirm_text = i18n_get_text(language, "broadcast._preview_confirm", preview=preview_text)
    await callback.message.edit_text(
        preview_confirm_text,
        reply_markup=get_broadcast_confirm_keyboard(language)
    )


@router.callback_query(F.data == "broadcast:confirm_send")
async def callback_broadcast_confirm_send(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Подтверждение и отправка уведомления"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    await callback.answer()
    
    language = await resolve_user_language(callback.from_user.id)
    
    data = await state.get_data()
    title = data.get("title")
    message_text = data.get("message")
    message_a = data.get("message_a")
    message_b = data.get("message_b")
    is_ab_test = data.get("is_ab_test", False)
    broadcast_type = data.get("type")
    segment = data.get("segment")
    
    # Проверка данных
    if not all([title, broadcast_type, segment]):
        await callback.message.answer("Ошибка: не все данные заполнены. Начните заново.")
        await state.clear()
        return
    
    if is_ab_test:
        if not all([message_a, message_b]):
            await callback.message.answer("Ошибка: не заполнены тексты вариантов A и B. Начните заново.")
            await state.clear()
            return
    else:
        if not message_text:
            await callback.message.answer("Ошибка: не заполнен текст уведомления. Начните заново.")
            await state.clear()
            return
    
    try:
        # Создаем уведомление в БД
        broadcast_id = await database.create_broadcast(
            title, message_text, broadcast_type, segment, callback.from_user.id,
            is_ab_test=is_ab_test, message_a=message_a, message_b=message_b
        )
        
        # Формируем сообщения для отправки
        type_emoji = {
            "info": "ℹ️",
            "maintenance": "🔧",
            "security": "🔒",
            "promo": "🎯"
        }
        emoji = type_emoji.get(broadcast_type, "📢")
        
        if is_ab_test:
            final_message_a = f"{emoji} {title}\n\n{message_a}"
            final_message_b = f"{emoji} {title}\n\n{message_b}"
        else:
            final_message = f"{emoji} {title}\n\n{message_text}"
        
        # Получаем список пользователей по сегменту
        user_ids = await database.get_users_by_segment(segment)
        total_users = len(user_ids)
        
        logger.info(
            f"BROADCAST_START broadcast_id={broadcast_id} segment={segment} total_users={total_users}"
        )
        
        await callback.message.edit_text(
            i18n_get_text(language, "broadcast._sending", total=total_users),
            reply_markup=None
        )
        
        # Telegram limit: 20 msg/sec. Batch 20 users, then sleep 1 sec.
        BROADCAST_BATCH_SIZE = 20
        sent_count = 0
        failed_list = []  # [{"telegram_id": int, "error": str}, ...]
        
        def _chunks(lst, n):
            for i in range(0, len(lst), n):
                yield lst[i:i + n]
        
        for batch in _chunks(user_ids, BROADCAST_BATCH_SIZE):
            tasks = []
            for user_id in batch:
                if is_ab_test:
                    variant = "A" if random.random() < 0.5 else "B"
                    msg = final_message_a if variant == "A" else final_message_b
                else:
                    variant = None
                    msg = final_message
                tasks.append((user_id, variant, msg))
            
            for user_id, variant, message_to_send in tasks:
                try:
                    await bot.send_message(user_id, message_to_send)
                    await database.log_broadcast_send(broadcast_id, user_id, "sent", variant)
                    sent_count += 1
                    logger.debug(f"BROADCAST_BATCH_SENT user_id={user_id} broadcast_id={broadcast_id}")
                except Exception as e:
                    err_str = str(e).strip()[:80]
                    failed_list.append({"telegram_id": user_id, "error": err_str})
                    await database.log_broadcast_send(broadcast_id, user_id, "failed", variant)
                    logger.warning(f"BROADCAST_FAILED_USER user_id={user_id} error={err_str}")
            
            if len(batch) == BROADCAST_BATCH_SIZE:
                await asyncio.sleep(1)
        
        failed_count = len(failed_list)
        logger.info(
            f"BROADCAST_COMPLETED broadcast_id={broadcast_id} sent={sent_count} failed={failed_count}"
        )
        
        await database._log_audit_event_atomic_standalone(
            "broadcast_sent",
            callback.from_user.id,
            None,
            f"Broadcast ID: {broadcast_id}, Segment: {segment}, Sent: {sent_count}, Failed: {failed_count}"
        )
        
        # Admin report (localized)
        if failed_count == 0:
            result_text = i18n_get_text(language, "broadcast._report_success", total=total_users, sent=sent_count, broadcast_id=broadcast_id)
        else:
            failed_lines = "\n".join(
                f"{f['telegram_id']} — {f['error']}" for f in failed_list[:20]
            )
            if len(failed_list) > 20:
                failed_lines += f"\n... and {len(failed_list) - 20} more"
            result_text = i18n_get_text(language, "broadcast._report_partial", total=total_users, sent=sent_count, failed=failed_count, broadcast_id=broadcast_id, failed_list=failed_lines)
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.back_to_broadcast"), callback_data="admin:broadcast")],
        ])
        
        await callback.message.edit_text(result_text, reply_markup=keyboard)
        
    except Exception as e:
        logging.exception(f"Error in broadcast send: {e}")
        await callback.message.answer(f"Ошибка при отправке уведомления: {e}")
    
    finally:
        await state.clear()


@router.callback_query(F.data == "broadcast:ab_stats")
async def callback_broadcast_ab_stats(callback: CallbackQuery):
    """Список A/B тестов"""
    user = await database.get_user(callback.from_user.id)
    language = await resolve_user_language(callback.from_user.id)
    
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    await callback.answer()
    
    try:
        ab_tests = await database.get_ab_test_broadcasts()
        
        if not ab_tests:
            text = i18n_get_text(language, "broadcast._ab_stats_empty")
            await safe_edit_text(callback.message, text, reply_markup=get_admin_back_keyboard(language))
            return
        
        text = i18n_get_text(language, "broadcast._ab_stats_select")
        keyboard = get_ab_test_list_keyboard(ab_tests, language)
        await safe_edit_text(callback.message, text, reply_markup=keyboard)
        
        # Логируем действие
        await database._log_audit_event_atomic_standalone("admin_view_ab_stats_list", callback.from_user.id, None, f"Viewed {len(ab_tests)} A/B tests")
    
    except Exception as e:
        logging.exception(f"Error in callback_broadcast_ab_stats: {e}")
        await callback.message.answer(
            i18n_get_text(language, "broadcast._ab_stats_error")
        )


@router.callback_query(F.data.startswith("broadcast:ab_stat:"))
async def callback_broadcast_ab_stat_detail(callback: CallbackQuery):
    """Статистика конкретного A/B теста"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    await callback.answer()
    
    try:
        broadcast_id = int(callback.data.split(":")[2])
        
        # Получаем информацию об уведомлении
        broadcast = await database.get_broadcast(broadcast_id)
        if not broadcast:
            await callback.message.answer("Уведомление не найдено.")
            return
        
        # Получаем статистику
        stats = await database.get_ab_test_stats(broadcast_id)
        
        if not stats:
            text = f"📊 A/B статистика\n\nУведомление: #{broadcast_id}\n\nНедостаточно данных для анализа."
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="broadcast:ab_stats")],
            ])
            await safe_edit_text(callback.message, text, reply_markup=keyboard)
            return
        
        # Формируем текст статистики
        total_sent = stats["total_sent"]
        variant_a_sent = stats["variant_a_sent"]
        variant_b_sent = stats["variant_b_sent"]
        
        # Проценты
        if total_sent > 0:
            percent_a = round((variant_a_sent / total_sent) * 100)
            percent_b = round((variant_b_sent / total_sent) * 100)
        else:
            percent_a = 0
            percent_b = 0
        
        text = (
            f"📊 A/B статистика\n\n"
            f"Уведомление: #{broadcast_id}\n"
            f"Заголовок: {broadcast.get('title', '—')}\n\n"
            f"Вариант A:\n"
            f"— Отправлено: {variant_a_sent} ({percent_a}%)\n\n"
            f"Вариант B:\n"
            f"— Отправлено: {variant_b_sent} ({percent_b}%)\n\n"
            f"Всего отправлено: {total_sent}"
        )
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="broadcast:ab_stats")],
        ])
        
        await safe_edit_text(callback.message, text, reply_markup=keyboard)
        
        # Логируем действие
        await database._log_audit_event_atomic_standalone("admin_view_ab_stat_detail", callback.from_user.id, None, f"Viewed A/B stats for broadcast {broadcast_id}")
    
    except (ValueError, IndexError) as e:
        logging.error(f"Error parsing broadcast ID: {e}")
        await callback.message.answer("Ошибка: неверный ID уведомления.")
    except Exception as e:
        logging.exception(f"Error in callback_broadcast_ab_stat_detail: {e}")
        await callback.message.answer("Ошибка при получении статистики A/B теста. Проверь логи.")


@router.message(Command("admin_audit"))
async def cmd_admin_audit(message: Message):
    """Показать последние записи audit_log (только для админа)"""
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        logging.warning(f"Unauthorized admin_audit attempt by user {message.from_user.id}")
        await message.answer("Недостаточно прав")
        return
    
    try:
        # Получаем последние 10 записей из audit_log
        audit_logs = await database.get_last_audit_logs(limit=10)
        
        if not audit_logs:
            await message.answer("Аудит пуст. Действий не зафиксировано.")
            return
        
        # Формируем сообщение
        lines = ["📜 Audit Log", ""]
        
        for log in audit_logs:
            # Форматируем дату и время
            created_at = log["created_at"]
            if isinstance(created_at, str):
                created_at = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
            elif isinstance(created_at, datetime):
                pass
            else:
                created_at = datetime.now()
            
            created_str = created_at.strftime("%Y-%m-%d %H:%M")
            
            lines.append(f"🕒 {created_str}")
            lines.append(f"Действие: {log['action']}")
            lines.append(f"Админ: {log['telegram_id']}")
            
            if log['target_user']:
                lines.append(f"Пользователь: {log['target_user']}")
            else:
                lines.append("Пользователь: —")
            
            if log['details']:
                lines.append(f"Детали: {log['details']}")
            else:
                lines.append("Детали: —")
            
            lines.append("")
            lines.append("⸻")
            lines.append("")
        
        # Убираем последний разделитель
        if lines[-1] == "" and lines[-2] == "⸻":
            lines = lines[:-2]
        
        text = "\n".join(lines)
        
        # Проверяем лимит Telegram (4096 символов на сообщение)
        if len(text) > 4000:
            # Если текст слишком длинный, обрезаем до первых записей
            # Попробуем уменьшить количество записей
            audit_logs = await database.get_last_audit_logs(limit=5)
            lines = ["📜 Audit Log", ""]
            
            for log in audit_logs:
                created_at = log["created_at"]
                if isinstance(created_at, str):
                    created_at = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                elif isinstance(created_at, datetime):
                    pass
                else:
                    created_at = datetime.now()
                
                created_str = created_at.strftime("%Y-%m-%d %H:%M")
                
                lines.append(f"🕒 {created_str}")
                lines.append(f"Действие: {log['action']}")
                lines.append(f"Админ: {log['telegram_id']}")
                
                if log['target_user']:
                    lines.append(f"Пользователь: {log['target_user']}")
                else:
                    lines.append("Пользователь: —")
                
                if log['details']:
                    # Обрезаем детали если они слишком длинные
                    details = log['details']
                    if len(details) > 200:
                        details = details[:200] + "..."
                    lines.append(f"Детали: {details}")
                else:
                    lines.append("Детали: —")
                
                lines.append("")
                lines.append("⸻")
                lines.append("")
            
            if lines[-1] == "" and lines[-2] == "⸻":
                lines = lines[:-2]
            
            text = "\n".join(lines)
        
        await message.answer(text)
        logging.info(f"Admin audit log viewed by admin {message.from_user.id}")
        
    except Exception as e:
        logging.exception(f"Error in cmd_admin_audit: {e}")
        await message.answer("Ошибка при получении audit log. Проверь логи.")


@router.message(Command("reissue_key"))
async def cmd_reissue_key(message: Message):
    """Перевыпустить VPN-ключ для пользователя (только для админа)"""
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        logging.warning(f"Unauthorized reissue_key attempt by user {message.from_user.id}")
        await message.answer("Нет доступа")
        return
    
    try:
        # Парсим команду: /reissue_key <telegram_id>
        parts = message.text.split()
        if len(parts) != 2:
            await message.answer("Использование: /reissue_key <telegram_id>")
            return
        
        try:
            target_telegram_id = int(parts[1])
        except ValueError:
            await message.answer("Неверный формат telegram_id. Используйте число.")
            return
        
        admin_telegram_id = message.from_user.id
        
        # Атомарно перевыпускаем ключ
        result = await database.reissue_vpn_key_atomic(target_telegram_id, admin_telegram_id)
        new_vpn_key, old_vpn_key = result
        
        if new_vpn_key is None:
            await message.answer(f"❌ Не удалось перевыпустить ключ для пользователя {target_telegram_id}.\nВозможные причины:\n- Нет активной подписки\n- Ошибка создания VPN-ключа")
            return
        
        # Уведомляем пользователя
        try:
            user_text = get_reissue_notification_text(new_vpn_key)
            keyboard = get_reissue_notification_keyboard()
            await message.bot.send_message(target_telegram_id, user_text, reply_markup=keyboard, parse_mode="HTML")
            logging.info(f"Reissue notification sent to user {target_telegram_id}")
        except Exception as e:
            logging.error(f"Error sending reissue notification to user {target_telegram_id}: {e}")
            await message.answer(f"✅ Ключ перевыпущен, но не удалось отправить уведомление пользователю: {e}")
            return
        
        await message.answer(
            f"✅ VPN-ключ успешно перевыпущен для пользователя {target_telegram_id}\n\n"
            f"Старый ключ: <code>{old_vpn_key[:20]}...</code>\n"
            f"Новый ключ: <code>{new_vpn_key}</code>",
            parse_mode="HTML"
        )
        logging.info(f"VPN key reissued for user {target_telegram_id} by admin {admin_telegram_id}")
        
    except Exception as e:
        logging.exception(f"Error in cmd_reissue_key: {e}")
        await message.answer("Ошибка при перевыпуске ключа. Проверь логи.")


@router.callback_query(F.data.startswith("reject_payment:"))
async def reject_payment(callback: CallbackQuery):
    """Админ отклонил платеж"""
    await callback.answer()  # ОБЯЗАТЕЛЬНО
    
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        logging.warning(f"Unauthorized reject attempt by user {callback.from_user.id}")
        await callback.answer("Нет доступа", show_alert=True)
        return
    
    try:
        payment_id = int(callback.data.split(":")[1])
        
        logging.info(f"REJECT pressed by admin {callback.from_user.id}, payment_id={payment_id}")
        
        # Получить платеж из БД
        payment = await database.get_payment(payment_id)
        
        if not payment:
            logging.warning(f"Payment {payment_id} not found for reject")
            await callback.answer("Платеж не найден", show_alert=True)
            return
        
        if payment["status"] != "pending":
            logging.warning(
                f"Attempt to reject already processed payment {payment_id}, status={payment['status']}"
            )
            await callback.answer("Платеж уже обработан", show_alert=True)
            # Удаляем кнопки даже если платеж уже обработан
            await safe_edit_reply_markup(callback.message, reply_markup=None)
            return
        
        telegram_id = payment["telegram_id"]
        admin_telegram_id = callback.from_user.id
        
        # Обновляем статус платежа на rejected (аудит записывается внутри функции)
        await database.update_payment_status(payment_id, "rejected", admin_telegram_id)
        logging.info(f"Payment {payment_id} rejected for user {telegram_id}")
        
        # Уведомляем пользователя
        language = await resolve_user_language(telegram_id)
        
        text = i18n_get_text(language, "payment.rejected", "payment_rejected")
        
        # Создаем inline клавиатуру для UX
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=i18n_get_text(language, "buy.renew_button", "buy_renew_button"),
                callback_data="menu_buy_vpn"
            )],
            [InlineKeyboardButton(
                text=i18n_get_text(language, "main.support_button", "support_button"),
                callback_data="menu_support"
            )]
        ])
        
        try:
            await callback.bot.send_message(telegram_id, text, reply_markup=keyboard)
            logging.info(f"Rejection message sent to user {telegram_id} for payment {payment_id}")
        except Exception as e:
            logging.error(f"Error sending rejection message to user {telegram_id}: {e}")
        
        await callback.message.edit_text(f"❌ Платеж {payment_id} отклонен")
        # Удаляем inline-кнопки после обработки
        await safe_edit_reply_markup(callback.message, reply_markup=None)
        
    except Exception as e:
        logging.exception(f"Error in reject_payment callback for payment_id={payment_id if 'payment_id' in locals() else 'unknown'}")
        await callback.answer("Ошибка. Проверь логи.", show_alert=True)


@router.callback_query(F.data == "admin:credit_balance")
async def callback_admin_credit_balance_start(callback: CallbackQuery, state: FSMContext):
    """Начало процесса выдачи средств - запрос поиска пользователя"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    user = await database.get_user(callback.from_user.id)
    language = await resolve_user_language(callback.from_user.id)
    text = i18n_get_text(language, "admin.credit_balance_prompt", "admin_credit_balance_prompt")
    await callback.message.edit_text(text, reply_markup=get_admin_back_keyboard(language))
    await state.set_state(AdminCreditBalance.waiting_for_user_search)
    await callback.answer()


@router.callback_query(F.data.startswith("admin:credit_balance:"))
async def callback_admin_credit_balance_user(callback: CallbackQuery, state: FSMContext):
    """Начало процесса выдачи средств для конкретного пользователя"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    try:
        user_id = int(callback.data.split(":")[2])
        await state.update_data(target_user_id=user_id)
        
        user = await database.get_user(callback.from_user.id)
        language = await resolve_user_language(callback.from_user.id)
        text = i18n_get_text(language, "admin.credit_balance_user_prompt", user_id=user_id)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.cancel", "admin_cancel"), callback_data=f"admin:user")]
        ])
        await safe_edit_text(callback.message, text, reply_markup=keyboard)
        await state.set_state(AdminCreditBalance.waiting_for_amount)
        await callback.answer()
    except Exception as e:
        logging.exception(f"Error in callback_admin_credit_balance_user: {e}")
        await callback.answer("Ошибка. Проверь логи.", show_alert=True)


@router.message(AdminCreditBalance.waiting_for_user_search)
async def process_admin_credit_balance_user_search(message: Message, state: FSMContext):
    """Обработка поиска пользователя для выдачи средств"""
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(message.from_user.id)
        await message.answer(i18n_get_text(language, "admin.access_denied"))
        await state.clear()
        return
    
    try:
        user_input = message.text.strip()
        
        # Определяем, является ли ввод числом (ID) или строкой (username)
        try:
            target_user_id = int(user_input)
            user = await database.find_user_by_id_or_username(telegram_id=target_user_id)
        except ValueError:
            username = user_input.lstrip('@').lower()
            user = await database.find_user_by_id_or_username(username=username)
        
        if not user:
            await message.answer("Пользователь не найден.\nПроверьте Telegram ID или username.")
            await state.clear()
            return
        
        target_user_id = user["telegram_id"]
        await state.update_data(target_user_id=target_user_id)
        
        text = f"💰 Выдать средства\n\nПользователь: {target_user_id}\n\nВведите сумму в рублях:"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.cancel"), callback_data="admin:main")]
        ])
        await message.answer(text, reply_markup=keyboard)
        await state.set_state(AdminCreditBalance.waiting_for_amount)
        
    except Exception as e:
        logging.exception(f"Error in process_admin_credit_balance_user_search: {e}")
        await message.answer("Ошибка при поиске пользователя. Проверьте логи.")
        await state.clear()


@router.message(AdminCreditBalance.waiting_for_amount)
async def process_admin_credit_balance_amount(message: Message, state: FSMContext):
    """Обработка ввода суммы для выдачи средств"""
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(message.from_user.id)
        await message.answer(i18n_get_text(language, "admin.access_denied"))
        await state.clear()
        return
    
    try:
        amount = float(message.text.strip().replace(",", "."))
        
        if amount <= 0:
            await message.answer("❌ Сумма должна быть положительным числом.\n\nВведите сумму в рублях:")
            return
        
        data = await state.get_data()
        target_user_id = data.get("target_user_id")
        
        if not target_user_id:
            await message.answer("Ошибка: пользователь не найден. Начните заново.")
            await state.clear()
            return
        
        # Сохраняем сумму и показываем подтверждение
        await state.update_data(amount=amount)
        
        user = await database.get_user(target_user_id)
        current_balance = await database.get_user_balance(target_user_id) if user else 0.0
        new_balance = current_balance + amount
        
        text = (
            f"💰 Подтверждение выдачи средств\n\n"
            f"👤 Пользователь: {target_user_id}\n"
            f"💳 Текущий баланс: {current_balance:.2f} ₽\n"
            f"➕ Сумма к выдаче: {amount:.2f} ₽\n"
            f"💵 Новый баланс: {new_balance:.2f} ₽\n\n"
            f"Подтвердите операцию:"
        )
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text=i18n_get_text(language, "admin.confirm"), callback_data="admin:credit_balance_confirm"),
                InlineKeyboardButton(text=i18n_get_text(language, "admin.cancel"), callback_data="admin:credit_balance_cancel")
            ]
        ])
        
        await message.answer(text, reply_markup=keyboard)
        await state.set_state(AdminCreditBalance.waiting_for_confirmation)
        
    except ValueError:
        await message.answer("❌ Неверный формат суммы.\n\nВведите число (например: 500 или 100.50):")
    except Exception as e:
        logging.exception(f"Error in process_admin_credit_balance_amount: {e}")
        await message.answer("Ошибка при обработке суммы. Проверьте логи.")
        await state.clear()


@router.callback_query(F.data == "admin:credit_balance_confirm")
async def callback_admin_credit_balance_confirm(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Подтверждение выдачи средств"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    try:
        data = await state.get_data()
        target_user_id = data.get("target_user_id")
        amount = data.get("amount")
        
        if not target_user_id or not amount:
            await callback.answer("Ошибка: данные не найдены", show_alert=True)
            await state.clear()
            return
        
        # Начисляем баланс
        success = await database.increase_balance(
            telegram_id=target_user_id,
            amount=amount,
            source="admin",
            description=f"Выдача средств администратором {callback.from_user.id}"
        )
        
        if success:
            # Логируем операцию
            await database._log_audit_event_atomic_standalone(
                "admin_credit_balance",
                callback.from_user.id,
                target_user_id,
                f"Admin credited balance: {amount:.2f} RUB"
            )
            
            # Отправляем уведомление пользователю
            try:
                new_balance = await database.get_user_balance(target_user_id)
                notification_text = f"💰 Администратор начислил вам {amount:.2f} ₽ на баланс.\n\nТекущий баланс: {new_balance:.2f} ₽"
                await bot.send_message(chat_id=target_user_id, text=notification_text)
            except Exception as e:
                logger.warning(f"Failed to send balance credit notification to user {target_user_id}: {e}")
            
            new_balance = await database.get_user_balance(target_user_id)
            text = (
                f"✅ Средства успешно начислены\n\n"
                f"👤 Пользователь: {target_user_id}\n"
                f"➕ Сумма: {amount:.2f} ₽\n"
                f"💵 Новый баланс: {new_balance:.2f} ₽"
            )
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:main")]
            ])
            
            await safe_edit_text(callback.message, text, reply_markup=keyboard)
            await state.clear()
            await callback.answer("✅ Средства начислены", show_alert=True)
        else:
            await callback.answer("❌ Ошибка при начислении средств", show_alert=True)
            await state.clear()
            
    except Exception as e:
        logging.exception(f"Error in callback_admin_credit_balance_confirm: {e}")
        await callback.answer("Ошибка. Проверь логи.", show_alert=True)
        await state.clear()


@router.callback_query(F.data == "admin:credit_balance_cancel")
async def callback_admin_credit_balance_cancel(callback: CallbackQuery, state: FSMContext):
    """Отмена выдачи средств"""
    user = await database.get_user(callback.from_user.id)
    language = await resolve_user_language(callback.from_user.id)
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    await callback.message.edit_text(
        i18n_get_text(language, "admin.operation_cancelled"),
        reply_markup=get_admin_back_keyboard(language)
    )
    await state.clear()
    await callback.answer()


# ====================================================================================
# GLOBAL FALLBACK HANDLER: Обработка необработанных callback_query
# ====================================================================================
@router.callback_query()
async def callback_fallback(callback: CallbackQuery, state: FSMContext):
    """
    Глобальный fallback handler для всех необработанных callback_query
    
    Логирует callback_data и текущее FSM-состояние для отладки.
    НЕ отвечает пользователю, чтобы не ломать UX.
    """
    callback_data = callback.data
    telegram_id = callback.from_user.id
    current_state = await state.get_state()
    
    logger.warning(
        f"Unhandled callback_query: user={telegram_id}, "
        f"callback_data='{callback_data}', "
        f"fsm_state={current_state}"
    )
    
    # НЕ отвечаем пользователю - просто логируем для отладки
    # Это позволяет видеть устаревшие/лишние callback_data без ломания UX


