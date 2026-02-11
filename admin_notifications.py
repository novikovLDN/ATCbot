"""
Admin Notifications Module

Sends Telegram notifications to admin about bot state changes.
All messages use localization (admin language: ru).
"""
import localization
import logging
from datetime import datetime
from typing import Optional
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup
import config

logger = logging.getLogger(__name__)

# Флаг для отслеживания, было ли отправлено уведомление о деградированном режиме
# Это предотвращает спам при повторных попытках инициализации
_degraded_notification_sent = False
_recovered_notification_sent = False

# Защита от спама для уведомлений о pending activations
# Отправляем не чаще раза в час
_last_pending_notification_time = None
PENDING_NOTIFICATION_COOLDOWN_SECONDS = 3600  # 1 час


async def notify_admin_degraded_mode(bot: Bot):
    """
    Уведомить администратора о том, что бот работает в деградированном режиме
    
    Args:
        bot: Экземпляр Telegram бота
        
    Отправляет сообщение только один раз при переходе в деградированный режим.
    """
    global _degraded_notification_sent
    
    # Если уведомление уже отправлено, не отправляем снова
    if _degraded_notification_sent:
        return
    
    message = localization.get_text("ru", "admin_degraded_mode")
    
    # Use unified entry point for consistent error handling and observability
    success = await send_admin_notification(
        bot=bot,
        message=message,
        notification_type="degraded_mode",
        parse_mode=None
    )
    
    if success:
        _degraded_notification_sent = True


async def notify_admin_recovered(bot: Bot):
    """
    Уведомить администратора о том, что бот восстановил работу с БД
    
    Args:
        bot: Экземпляр Telegram бота
        
    Отправляет сообщение только один раз при восстановлении.
    """
    global _recovered_notification_sent
    
    # Если уведомление уже отправлено, не отправляем снова
    if _recovered_notification_sent:
        return
    
    message = localization.get_text("ru", "admin_recovered")
    
    # Use unified entry point for consistent error handling and observability
    success = await send_admin_notification(
        bot=bot,
        message=message,
        notification_type="recovered",
        parse_mode=None
    )
    
    if success:
        _recovered_notification_sent = True


def reset_notification_flags():
    """
    Сбросить флаги уведомлений (для тестирования или после длительного простоя)
    
    Это позволяет отправить уведомления заново, если бот перезапускается.
    """
    global _degraded_notification_sent, _recovered_notification_sent
    _degraded_notification_sent = False
    _recovered_notification_sent = False


async def notify_admin_pending_activations(bot: Bot, pending_count: int, oldest_pending: list):
    """
    Уведомить администратора о подписках с отложенной активацией
    
    Защита от спама: отправляет уведомление не чаще раза в час.
    
    Args:
        bot: Экземпляр Telegram бота
        pending_count: Общее количество pending подписок
        oldest_pending: Список словарей с информацией о старейших pending подписках
                       [{"subscription_id": int, "telegram_id": int, "attempts": int, 
                         "error": str, "pending_since": datetime}]
    """
    global _last_pending_notification_time
    
    try:
        if pending_count == 0:
            return
        
        # Защита от спама: проверяем, прошёл ли cooldown
        import time
        current_time = time.time()
        if (_last_pending_notification_time is not None and 
            current_time - _last_pending_notification_time < PENDING_NOTIFICATION_COOLDOWN_SECONDS):
            logger.debug(f"Skipping pending activations notification (cooldown active)")
            return
        
        admin_lang = "ru"
        title = localization.get_text(admin_lang, "admin_pending_activations_title")
        total = localization.get_text(admin_lang, "admin_pending_activations_total", count=pending_count)
        message_lines = [title, total]
        
        if oldest_pending:
            message_lines.append(localization.get_text(admin_lang, "admin_pending_activations_top"))
            for idx, sub in enumerate(oldest_pending[:5], 1):
                pending_since = sub.get("pending_since", "N/A")
                if isinstance(pending_since, datetime):
                    pending_since_str = pending_since.strftime("%d.%m.%Y %H:%M")
                else:
                    pending_since_str = str(pending_since)
                
                error_preview = sub.get("error", "N/A")
                if error_preview and len(error_preview) > 50:
                    error_preview = error_preview[:50] + "..."
                
                row = localization.get_text(
                    admin_lang,
                    "admin_pending_activations_row",
                    idx=idx,
                    subscription_id=sub["subscription_id"],
                    telegram_id=sub["telegram_id"],
                    attempts=sub["attempts"],
                    pending_since=pending_since_str,
                    error=error_preview
                )
                message_lines.append(row)
        
        message = "".join(message_lines)
        
        # Use unified entry point for consistent error handling and observability
        success = await send_admin_notification(
            bot=bot,
            message=message,
            notification_type="pending_activations",
            parse_mode=None
        )
        
        if success:
            # Обновляем время последнего уведомления только при успешной отправке
            _last_pending_notification_time = current_time
        
    except Exception as e:
        logger.exception(f"Error in notify_admin_pending_activations (non-fatal): {e}")
        # Не пробрасываем исключение - это не критично


# ====================================================================================
# UNIFIED NOTIFICATION ENTRY POINTS (First-Class Service)
# ====================================================================================

async def send_admin_notification(
    bot: Bot,
    message: str,
    notification_type: str = "custom",
    parse_mode: Optional[str] = None,
    **kwargs
) -> bool:
    """
    Unified entry point for sending admin notifications.
    
    This is a first-class notification service that:
    - Logs all delivery attempts explicitly
    - Handles errors gracefully (logs but doesn't crash)
    - Returns success/failure status
    - Makes delivery attempts observable
    
    Args:
        bot: Telegram bot instance
        message: Notification message text
        notification_type: Type of notification (for logging/observability)
                          Examples: "degraded_mode", "recovered", "pending_activations", 
                                   "corporate_access_request", "custom"
        parse_mode: Parse mode for message (None, "HTML", "Markdown")
        **kwargs: Additional arguments passed to bot.send_message
    
    Returns:
        bool: True if notification sent successfully, False otherwise
    
    Never raises exceptions - all errors are logged and handled gracefully.
    """
    if not config.ADMIN_TELEGRAM_ID:
        logger.warning(f"ADMIN_NOTIFICATION_SKIPPED [type={notification_type}, reason=admin_id_not_configured]")
        return False
    
    try:
        logger.info(f"ADMIN_NOTIFICATION_ATTEMPT [type={notification_type}, admin_id={config.ADMIN_TELEGRAM_ID}]")
        
        await bot.send_message(
            config.ADMIN_TELEGRAM_ID,
            message,
            parse_mode=parse_mode or None,  # Default to None to avoid parse errors
            **kwargs
        )
        
        logger.info(f"ADMIN_NOTIFICATION_SENT [type={notification_type}, admin_id={config.ADMIN_TELEGRAM_ID}]")
        return True
        
    except Exception as e:
        logger.error(
            f"ADMIN_NOTIFICATION_FAILED [type={notification_type}, admin_id={config.ADMIN_TELEGRAM_ID}, "
            f"error={type(e).__name__}: {str(e)[:100]}]"
        )
        logger.exception(f"Admin notification delivery failed (non-fatal): {e}")
        return False


async def send_user_notification(
    bot: Bot,
    user_id: int,
    message: str,
    notification_type: str = "custom",
    parse_mode: Optional[str] = None,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    **kwargs
) -> bool:
    """
    Unified entry point for sending user notifications.
    
    This is a first-class notification service that:
    - Logs all delivery attempts explicitly
    - Handles errors gracefully (logs but doesn't crash)
    - Returns success/failure status
    - Makes delivery attempts observable
    
    Args:
        bot: Telegram bot instance
        user_id: Telegram user ID
        message: Notification message text
        notification_type: Type of notification (for logging/observability)
                          Examples: "admin_grant", "admin_revoke", "payment_approved",
                                   "subscription_renewed", "corporate_access_confirmation", "custom"
        parse_mode: Parse mode for message (None, "HTML", "Markdown")
        reply_markup: Optional inline keyboard
        **kwargs: Additional arguments passed to bot.send_message
    
    Returns:
        bool: True if notification sent successfully, False otherwise
    
    Never raises exceptions - all errors are logged and handled gracefully.
    """
    try:
        logger.info(f"USER_NOTIFICATION_ATTEMPT [type={notification_type}, user_id={user_id}]")
        
        await bot.send_message(
            user_id,
            message,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
            **kwargs
        )
        
        logger.info(f"USER_NOTIFICATION_SENT [type={notification_type}, user_id={user_id}]")
        return True
        
    except Exception as e:
        # Handle specific Telegram errors gracefully
        error_type = type(e).__name__
        error_msg = str(e)
        
        # User blocked bot or deleted account
        if "blocked" in error_msg.lower() or "chat not found" in error_msg.lower():
            logger.warning(
                f"USER_NOTIFICATION_SKIPPED [type={notification_type}, user_id={user_id}, "
                f"reason=user_unreachable, error={error_type}]"
            )
        else:
            logger.error(
                f"USER_NOTIFICATION_FAILED [type={notification_type}, user_id={user_id}, "
                f"error={error_type}: {error_msg[:100]}]"
            )
            logger.exception(f"User notification delivery failed (non-fatal): {e}")
        
        return False

