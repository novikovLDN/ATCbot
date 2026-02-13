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
import uuid as uuid_module
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
from app.handlers.common.states import (
    AdminUserSearch,
    AdminReferralSearch,
    BroadcastCreate,
    AdminBroadcastNoSubscription,
    IncidentEdit,
    AdminGrantAccess,
    AdminRevokeAccess,
    AdminDiscountCreate,
    CorporateAccessRequest,
    PromoCodeInput,
    TopUpStates,
    AdminCreditBalance,
    AdminDebitBalance,
    AdminBalanceManagement,
    WithdrawStates,
    AdminCreatePromocode,
    PurchaseState,
)


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


# NOTE: Handler exception boundary → TelegramErrorBoundaryMiddleware (app/core/telegram_error_middleware.py)


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


def _get_promo_error_keyboard(language: str) -> InlineKeyboardMarkup:
    """Клавиатура с кнопкой 'Назад' при ошибке промокода"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=i18n_get_text(language, "common.back"),
                callback_data="promo_back"
            )
        ]
    ])


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
            text=i18n_get_text(language, "main.ecosystem", "main.ecosystem"),
            callback_data="menu_ecosystem"
        ),
        InlineKeyboardButton(
            text=i18n_get_text(language, "main.help"),
            callback_data="menu_support"
        ),
    ])
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "main.settings", "main.settings"),
        callback_data="menu_settings"
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
    # Кнопка вывода средств
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "profile.withdraw_funds"),
        callback_data="withdraw_start"
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
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.balance_management"), callback_data="admin:balance_management")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.system"), callback_data="admin:system")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.export"), callback_data="admin:export")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.broadcast"), callback_data="admin:broadcast")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.promo_stats"), callback_data="admin_promo_stats")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.referral_stats"), callback_data="admin:referral_stats")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.create_promocode"), callback_data="admin:create_promocode")],
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
from app.handlers.common.screens import show_profile, _open_buy_screen, show_tariffs_main_screen

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


def get_admin_user_keyboard_processing(user_id: int, has_discount: bool = False, is_vip: bool = False, language: str = "ru"):
    """Клавиатура во время перевыпуска ключа: кнопка «Перевыпуск» заменена на disabled состояние (callback_data=noop)"""
    buttons = []
    buttons.append([InlineKeyboardButton(text="⏳ Перевыпуск...", callback_data="noop")])
    if user_id:
        buttons.append([InlineKeyboardButton(text=i18n_get_text(language, "admin.subscription_history"), callback_data=f"admin:user_history:{user_id}")])
        buttons.append([
            InlineKeyboardButton(text=i18n_get_text(language, "admin.grant_access"), callback_data=f"admin:grant:{user_id}"),
            InlineKeyboardButton(text=i18n_get_text(language, "admin.revoke_access"), callback_data=f"admin:revoke:user:{user_id}")
        ])
        if has_discount:
            buttons.append([InlineKeyboardButton(text=i18n_get_text(language, "admin.delete_discount"), callback_data=f"admin:discount_delete:{user_id}")])
        else:
            buttons.append([InlineKeyboardButton(text=i18n_get_text(language, "admin.create_discount"), callback_data=f"admin:discount_create:{user_id}")])
        if is_vip:
            buttons.append([InlineKeyboardButton(text=i18n_get_text(language, "admin.revoke_vip"), callback_data=f"admin:vip_revoke:{user_id}")])
        else:
            buttons.append([InlineKeyboardButton(text=i18n_get_text(language, "admin.grant_vip"), callback_data=f"admin:vip_grant:{user_id}")])
        buttons.append([InlineKeyboardButton(text=i18n_get_text(language, "admin.credit_balance"), callback_data=f"admin:credit_balance:{user_id}")])
    buttons.append([InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# In-memory async lock per user for reissue (prevents parallel execution in single process)
_REISSUE_LOCKS: dict[int, asyncio.Lock] = {}


def get_reissue_lock(user_id: int) -> asyncio.Lock:
    if user_id not in _REISSUE_LOCKS:
        _REISSUE_LOCKS[user_id] = asyncio.Lock()
    return _REISSUE_LOCKS[user_id]


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




async def check_subscription_expiry(telegram_id: int) -> bool:
    """
    Дополнительная защита: проверка и мгновенное отключение истёкшей подписки
    
    Вызывается в начале критичных handlers для дополнительной безопасности.
    Возвращает True если подписка была отключена, False если активна или отсутствует.
    """
    return await check_subscription_expiry_service(telegram_id)
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
async def _open_support_screen(event: Union[Message, CallbackQuery], bot: Bot):
    """Поддержка. Reusable for callback and /help command."""
    msg = event.message if isinstance(event, CallbackQuery) else event
    telegram_id = event.from_user.id
    language = await resolve_user_language(telegram_id)
    text = i18n_get_text(language, "main.support_text", "support_text")
    await safe_edit_text(msg, text, reply_markup=get_support_keyboard(language), bot=bot)
    if isinstance(event, CallbackQuery):
        await event.answer()
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
@router.message(AdminCreatePromocode.waiting_for_code_name)
async def process_admin_promocode_code_name(message: Message, state: FSMContext):
    """Обработка имени промокода"""
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(message.from_user.id)
        await message.answer(i18n_get_text(language, "admin.access_denied"))
        await state.clear()
        return
    
    language = await resolve_user_language(message.from_user.id)
    code_input = message.text.strip() if message.text else ""
    
    # Если пустое сообщение — автогенерация
    if not code_input:
        from database import generate_promo_code
        code = generate_promo_code(6)
    else:
        code = code_input.upper().strip()
        
        # Валидация
        if len(code) < 3 or len(code) > 32:
            await message.answer(i18n_get_text(language, "admin.promocode_code_invalid"))
            return
        
        if not all(c.isalnum() for c in code):
            await message.answer(i18n_get_text(language, "admin.promocode_code_invalid"))
            return
        
        # Проверка активного промокода (разрешаем пересоздание после удаления/истечения/исчерпания)
        if await database.has_active_promo(code):
            await message.answer(i18n_get_text(language, "admin.promocode_code_exists"))
            return
    
    await state.update_data(promocode_code=code)
    await state.set_state(AdminCreatePromocode.waiting_for_discount_percent)
    
    text = i18n_get_text(language, "admin.promocode_discount_prompt")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.cancel"), callback_data="admin:promocode_cancel")]
    ])
    await message.answer(text, reply_markup=keyboard)


@router.message(AdminCreatePromocode.waiting_for_discount_percent)
async def process_admin_promocode_discount(message: Message, state: FSMContext):
    """Обработка процента скидки"""
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(message.from_user.id)
        await message.answer(i18n_get_text(language, "admin.access_denied"))
        await state.clear()
        return
    
    language = await resolve_user_language(message.from_user.id)
    
    try:
        discount_percent = int(message.text.strip())
        if discount_percent < 0 or discount_percent > 100:
            await message.answer(i18n_get_text(language, "admin.promocode_discount_invalid"))
            return
    except ValueError:
        await message.answer(i18n_get_text(language, "admin.promocode_discount_invalid"))
        return
    
    await state.update_data(promocode_discount=discount_percent)
    await state.set_state(AdminCreatePromocode.waiting_for_duration_unit)
    
    text = i18n_get_text(language, "admin.promocode_duration_unit_prompt")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏱ Часы", callback_data="admin:promocode_unit:hours")],
        [InlineKeyboardButton(text="📅 Дни", callback_data="admin:promocode_unit:days")],
        [InlineKeyboardButton(text="🗓 Месяцы", callback_data="admin:promocode_unit:months")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.cancel"), callback_data="admin:promocode_cancel")]
    ])
    await message.answer(text, reply_markup=keyboard)
@router.message(AdminCreatePromocode.waiting_for_duration_value)
async def process_admin_promocode_duration_value(message: Message, state: FSMContext):
    """Обработка значения длительности"""
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(message.from_user.id)
        await message.answer(i18n_get_text(language, "admin.access_denied"))
        await state.clear()
        return
    
    language = await resolve_user_language(message.from_user.id)
    
    try:
        value = int(message.text.strip())
        if value <= 0:
            await message.answer(i18n_get_text(language, "admin.promocode_duration_invalid"))
            return
    except ValueError:
        await message.answer(i18n_get_text(language, "admin.promocode_duration_invalid"))
        return
    
    data = await state.get_data()
    unit = data.get("promocode_duration_unit")
    
    # Конвертация в секунды
    if unit == "hours":
        duration_seconds = value * 3600
    elif unit == "days":
        duration_seconds = value * 86400
    elif unit == "months":
        duration_seconds = value * 30 * 86400
    else:
        await message.answer("Ошибка: неверная единица времени")
        await state.clear()
        return
    
    await state.update_data(promocode_duration_seconds=duration_seconds)
    await state.set_state(AdminCreatePromocode.waiting_for_max_uses)
    
    text = i18n_get_text(language, "admin.promocode_max_uses_prompt")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.cancel"), callback_data="admin:promocode_cancel")]
    ])
    await message.answer(text, reply_markup=keyboard)


@router.message(AdminCreatePromocode.waiting_for_max_uses)
async def process_admin_promocode_max_uses(message: Message, state: FSMContext):
    """Обработка максимального количества использований"""
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(message.from_user.id)
        await message.answer(i18n_get_text(language, "admin.access_denied"))
        await state.clear()
        return
    
    language = await resolve_user_language(message.from_user.id)
    
    try:
        max_uses = int(message.text.strip())
        if max_uses < 1:
            await message.answer(i18n_get_text(language, "admin.promocode_max_uses_invalid"))
            return
    except ValueError:
        await message.answer(i18n_get_text(language, "admin.promocode_max_uses_invalid"))
        return
    
    data = await state.get_data()
    code = data.get("promocode_code")
    discount_percent = data.get("promocode_discount")
    duration_seconds = data.get("promocode_duration_seconds")
    
    # Форматируем длительность для отображения
    if duration_seconds < 3600:
        duration_str = f"{duration_seconds // 60} минут"
    elif duration_seconds < 86400:
        duration_str = f"{duration_seconds // 3600} часов"
    elif duration_seconds < 2592000:
        duration_str = f"{duration_seconds // 86400} дней"
    else:
        duration_str = f"{duration_seconds // 2592000} месяцев"
    
    await state.update_data(promocode_max_uses=max_uses)
    await state.set_state(AdminCreatePromocode.confirm_creation)
    
    text = (
        f"🎟 Подтверждение создания промокода\n\n"
        f"Код: {code}\n"
        f"Скидка: {discount_percent}%\n"
        f"Срок действия: {duration_str}\n"
        f"Лимит использований: {max_uses}\n\n"
        f"Подтвердите создание:"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.promocode_confirm"), callback_data="admin:promocode_confirm")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.promocode_cancel"), callback_data="admin:promocode_cancel")]
    ])
    await message.answer(text, reply_markup=keyboard)
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
@router.callback_query()
async def callback_fallback(callback: CallbackQuery, state: FSMContext):
    """
    Глобальный fallback handler для всех необработанных callback_query
    
    Логирует callback_data и текущее FSM-состояние для отладки.
    Отвечает на callback, чтобы избежать спиннера и ошибок "Query is too old".
    """
    try:
        await callback.answer()
    except Exception:
        pass

    callback_data = callback.data
    telegram_id = callback.from_user.id
    current_state = await state.get_state()

    logger.warning(
        f"Unhandled callback_query: user={telegram_id}, "
        f"callback_data='{callback_data}', "
        f"fsm_state={current_state}"
    )


