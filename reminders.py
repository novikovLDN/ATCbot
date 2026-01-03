"""Модуль для отправки напоминаний об окончании подписки"""
import asyncio
import logging
from datetime import datetime, timedelta
from aiogram import Bot
import database
import localization
import config

logger = logging.getLogger(__name__)


async def send_expiring_reminders(bot: Bot):
    """Отправить напоминания пользователям, у которых подписка истекает через 3 дня"""
    try:
        subscriptions = await database.get_subscriptions_needing_reminder()
        
        if not subscriptions:
            return
        
        logger.info(f"Found {len(subscriptions)} subscriptions needing reminders")
        
        for subscription in subscriptions:
            telegram_id = subscription["telegram_id"]
            
            try:
                # Получаем язык пользователя
                user = await database.get_user(telegram_id)
                language = user.get("language", "ru") if user else "ru"
                
                # Получаем текст напоминания
                text = localization.get_text(language, "subscription_expiring_reminder")
                
                # Отправляем напоминание
                await bot.send_message(telegram_id, text)
                
                # Отмечаем, что напоминание отправлено
                await database.mark_reminder_sent(telegram_id)
                
                logger.info(f"Reminder sent to user {telegram_id}")
                
            except Exception as e:
                # Ошибка для одного пользователя не должна ломать цикл
                logger.error(f"Error sending reminder to user {telegram_id}: {e}", exc_info=True)
                continue
                
    except Exception as e:
        logger.exception(f"Error in send_expiring_reminders: {e}")


async def reminders_task(bot: Bot):
    """Фоновая задача для отправки напоминаний (выполняется раз в сутки)"""
    while True:
        try:
            await send_expiring_reminders(bot)
        except Exception as e:
            logger.exception(f"Error in reminders_task: {e}")
        
        # Ждем 24 часа до следующей проверки
        await asyncio.sleep(24 * 60 * 60)  # 24 часа в секундах

