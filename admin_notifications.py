"""
Admin Notifications Module

Sends Telegram notifications to admin about bot state changes:
- Bot enters degraded mode (DB unavailable)
- Bot recovers from degraded mode (DB restored)
- Pending VPN activations (long-pending or high attempts)
"""
import logging
from datetime import datetime
from aiogram import Bot
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
    
    try:
        message = (
            "⚠️ **БОТ РАБОТАЕТ В ДЕГРАДИРОВАННОМ РЕЖИМЕ**\n\n"
            "База данных недоступна.\n\n"
            "• Бот запущен и отвечает на команды\n"
            "• Критические операции блокируются\n"
            "• Пользователи получают сообщения о временной недоступности\n\n"
            "Бот будет автоматически пытаться восстановить соединение с БД каждые 30 секунд.\n\n"
            "Проверьте:\n"
            "• Доступность PostgreSQL\n"
            "• Правильность DATABASE_URL\n"
            "• Сетевые настройки"
        )
        
        await bot.send_message(
            config.ADMIN_TELEGRAM_ID,
            message,
            parse_mode="Markdown"
        )
        
        _degraded_notification_sent = True
        logger.info(f"Admin notification sent: Bot entered degraded mode (admin_id={config.ADMIN_TELEGRAM_ID})")
        
    except Exception as e:
        logger.exception(f"Error sending degraded mode notification to admin: {e}")
        # Не пробрасываем исключение - это не критично


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
    
    try:
        message = (
            "✅ **СЛУЖБА ВОССТАНОВЛЕНА**\n\n"
            "База данных стала доступна.\n\n"
            "• Бот работает в полнофункциональном режиме\n"
            "• Все операции восстановлены\n"
            "• Фоновые задачи запущены"
        )
        
        await bot.send_message(
            config.ADMIN_TELEGRAM_ID,
            message,
            parse_mode="Markdown"
        )
        
        _recovered_notification_sent = True
        logger.info(f"Admin notification sent: Service restored (admin_id={config.ADMIN_TELEGRAM_ID})")
        
    except Exception as e:
        logger.exception(f"Error sending recovery notification to admin: {e}")
        # Не пробрасываем исключение - это не критично


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
        
        message_lines = [
            "⚠️ **ОТЛОЖЕННЫЕ АКТИВАЦИИ VPN**\n",
            f"Всего pending подписок: **{pending_count}**\n"
        ]
        
        if oldest_pending:
            message_lines.append("\n**Топ-5 старейших:**\n")
            for idx, sub in enumerate(oldest_pending[:5], 1):
                pending_since = sub.get("pending_since", "N/A")
                if isinstance(pending_since, datetime):
                    pending_since_str = pending_since.strftime("%d.%m.%Y %H:%M")
                else:
                    pending_since_str = str(pending_since)
                
                error_preview = sub.get("error", "N/A")
                if error_preview and len(error_preview) > 50:
                    error_preview = error_preview[:50] + "..."
                
                message_lines.append(
                    f"{idx}. ID: `{sub['subscription_id']}` | "
                    f"User: `{sub['telegram_id']}` | "
                    f"Попыток: {sub['attempts']} | "
                    f"С {pending_since_str}\n"
                    f"   Ошибка: `{error_preview}`\n"
                )
        
        message = "\n".join(message_lines)
        
        await bot.send_message(
            config.ADMIN_TELEGRAM_ID,
            message,
            parse_mode="Markdown"
        )
        
        logger.info(
            f"Admin notification sent: Pending activations (count={pending_count}, "
            f"admin_id={config.ADMIN_TELEGRAM_ID})"
        )
        
        # Обновляем время последнего уведомления
        _last_pending_notification_time = current_time
        
    except Exception as e:
        logger.exception(f"Error sending pending activations notification to admin: {e}")
        # Не пробрасываем исключение - это не критично

