"""
Admin Notifications Module

Sends Telegram notifications to admin about bot state changes:
- Bot enters degraded mode (DB unavailable)
- Bot recovers from degraded mode (DB restored)
"""
import logging
from aiogram import Bot
import config

logger = logging.getLogger(__name__)

# Флаг для отслеживания, было ли отправлено уведомление о деградированном режиме
# Это предотвращает спам при повторных попытках инициализации
_degraded_notification_sent = False
_recovered_notification_sent = False


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

