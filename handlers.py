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

from aiogram import Router, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from datetime import datetime, timezone
import logging
import database
import config
import time
import asyncio
from typing import Optional, Dict, Any, Union
from app.services.subscriptions.service import (
    check_and_disable_expired_subscription as check_subscription_expiry_service,
)
from app.services.trials import service as trial_service
from app.services.language_service import resolve_user_language, DEFAULT_LANGUAGE
from app.i18n import get_text as i18n_get_text
from app.core.feature_flags import get_feature_flags
from app.constants.loyalty import get_loyalty_status_names, get_loyalty_screen_attachment




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
# - External API calls: isolated in try/except, mapped to dependency_error
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

def _is_inaccessible_error(error_msg: str) -> bool:
    """Check if Telegram error indicates an inaccessible/deleted message."""
    patterns = (
        "message to edit not found",
        "message can't be edited",
        "message is not accessible",
        "chat not found",
        "message to delete not found",
    )
    return any(p in error_msg for p in patterns)


async def _send_fallback(
    bot: Optional[Bot],
    message: Message,
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup],
    parse_mode: Optional[str],
    context: str,
) -> None:
    """Send a new message as fallback when edit is impossible."""
    if bot is None:
        logger.warning("Cannot send fallback (%s): bot not provided", context)
        return
    chat_id = None
    if hasattr(message, "chat") and message.chat:
        chat_id = message.chat.id
    elif hasattr(message, "from_user") and message.from_user:
        chat_id = message.from_user.id
    if not chat_id:
        logger.warning("Cannot send fallback (%s): no chat_id", context)
        return
    try:
        await bot.send_message(chat_id, text, reply_markup=reply_markup, parse_mode=parse_mode)
        logger.info("Sent fallback message (%s): chat_id=%s", context, chat_id)
    except Exception as e:
        logger.error("Failed to send fallback (%s): %s", context, e)


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
                logger.info("Message inaccessible (no chat attr), sent new message instead: chat_id=%s", chat_id)
            else:
                logger.warning("Message inaccessible (no chat attr) and cannot determine chat_id")
        except Exception as send_error:
            logger.error("Failed to send fallback message after inaccessible check: %s", send_error)
        return
    
    # Безопасная проверка текущего текста сообщения
    current_text = getattr(message, 'text', None) or getattr(message, 'caption', None)

    # Сравниваем текущий текст с новым — skip edit if identical
    if current_text and current_text == text:
        current_markup = getattr(message, 'reply_markup', None)
        if reply_markup is None:
            if current_markup is None:
                return
        elif current_markup and _markups_equal(current_markup, reply_markup):
            return

    # Photo message: edit caption instead of text
    has_photo = getattr(message, "photo", None) and len(message.photo) > 0
    if has_photo:
        try:
            await message.edit_caption(caption=text, reply_markup=reply_markup, parse_mode=parse_mode)
            return
        except TelegramBadRequest as e:
            err = str(e).lower()
            if "message is not modified" in err:
                return
            if _is_inaccessible_error(err):
                await _send_fallback(bot, message, text, reply_markup, parse_mode, "photo inaccessible")
                return
            raise

    try:
        await message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except TelegramBadRequest as e:
        error_msg = str(e).lower()
        if "message is not modified" in error_msg:
            return
        elif _is_inaccessible_error(error_msg):
            await _send_fallback(bot, message, text, reply_markup, parse_mode, "edit failed")
        else:
            raise
    except AttributeError as e:
        logger.warning("AttributeError in safe_edit_text, message may be inaccessible: %s", e)
        await _send_fallback(bot, message, text, reply_markup, parse_mode, "AttributeError")


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
        logger.debug("Reply markup not modified (expected): %s", e)

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
        logger.warning("Error getting incident settings: %s", e)
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
            logger.warning("Error checking trial availability for user %s: %s", telegram_id, e)
    
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


def get_instruction_keyboard(language: str, platform: str = "unknown"):
    """
    Клавиатура экрана 'Инструкция' для v2RayTun.
    Всегда показываем 5 кнопок: Android, Windows, iOS, MacOS, TV.
    """
    buttons = [
        [
            InlineKeyboardButton(
                text=i18n_get_text(language, "instruction._download_android", "🤖 Android"),
                url="https://play.google.com/store/apps/details?id=com.v2raytun.android"
            ),
            InlineKeyboardButton(
                text=i18n_get_text(language, "instruction._download_desktop", "💻 Windows"),
                url="https://www.mediafire.com/folder/lpcbgr4ox8u5x/Atlas_Secure"
            ),
        ],
        [
            InlineKeyboardButton(
                text=i18n_get_text(language, "instruction._download_ios", "📱 iOS"),
                url="https://apps.apple.com/tr/app/v2raytun/id6476628951"
            ),
            InlineKeyboardButton(
                text=i18n_get_text(language, "instruction._download_macos", "🍎 MacOS"),
                url="https://apps.apple.com/tr/app/v2raytun/id6476628951"
            ),
        ],
        [
            InlineKeyboardButton(
                text=i18n_get_text(language, "instruction._download_tv", "📺 TV"),
                url="https://play.google.com/store/apps/details?id=com.v2raytun.android"
            ),
        ],
    ]
    
    # Кнопка копирования ключа
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
# Bounded to prevent memory leaks — oldest entries evicted when limit reached
_REISSUE_LOCKS: dict[int, asyncio.Lock] = {}
_REISSUE_LOCKS_MAX = 10000


def get_reissue_lock(user_id: int) -> asyncio.Lock:
    if user_id not in _REISSUE_LOCKS:
        if len(_REISSUE_LOCKS) >= _REISSUE_LOCKS_MAX:
            # Evict oldest unlocked entries
            to_remove = [k for k, v in list(_REISSUE_LOCKS.items()) if not v.locked()][:1000]
            for k in to_remove:
                del _REISSUE_LOCKS[k]
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




async def show_payment_method_selection(
    callback: CallbackQuery,
    tariff_type: str,
    period_days: int,
    final_price_kopecks: int
):
    """ЭКРАН 3 — Выбор способа оплаты
    
    Показывает кнопки:
    - 💰 Баланс (доступно: N ₽)
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
    
    # Кнопка оплаты через СБП (Platega, +11%)
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "payment.sbp"),
        callback_data="pay:sbp"
    )])

    # Кнопка оплаты Telegram Stars
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "payment.stars"),
        callback_data="pay:stars"
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
        logger.exception("Error showing payment method selection: %s", e)
        await callback.answer(
            i18n_get_text(language, "errors.payment_processing"),
            show_alert=True
        )


# NOTE: All @router handlers have been moved to app/handlers/ modules.
# This router is NOT included in the dispatcher — only app.handlers.router is.
# Handlers that were here (pre_checkout_query, cmd_admin, cmd_pending_activations,
# cmd_admin_audit, cmd_xray_sync, cmd_reissue_key, callback_fallback, and
# AdminCreatePromocode FSM handlers) now live in their respective modules under app/handlers/.


