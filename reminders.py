"""Модуль для отправки напоминаний об окончании подписки"""
import asyncio
import logging
from datetime import datetime
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramForbiddenError
import database
import config
from app import i18n
from app.services.language_service import resolve_user_language
from app.services.notifications import service as notification_service
from app.services.notifications.service import ReminderType
# import outline_api  # DISABLED - мигрировали на Xray Core (VLESS)

logger = logging.getLogger(__name__)


def get_renewal_keyboard(language: str) -> InlineKeyboardMarkup:
    """Клавиатура для продления доступа"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n.get_text(language, "subscription.renew"),
            callback_data="menu_buy_vpn"
        )]
    ])
    return keyboard


def get_subscription_keyboard(language: str) -> InlineKeyboardMarkup:
    """Клавиатура для оформления подписки"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n.get_text(language, "main.buy"),
            callback_data="menu_buy_vpn"
        )]
    ])
    return keyboard


def get_tariff_1_month_keyboard(language: str) -> InlineKeyboardMarkup:
    """Клавиатура для подписки на 1 месяц (унифицирована с стандартными CTA)"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n.get_text(language, "main.buy"),
            callback_data="menu_buy_vpn"
        )]
    ])
    return keyboard




async def send_smart_reminders(bot: Bot):
    """Отправить умные напоминания пользователям (старая логика для совместимости)"""
    try:
        subscriptions = await database.get_subscriptions_for_reminders()
        
        if not subscriptions:
            return
        
        logger.info(f"Found {len(subscriptions)} subscriptions for reminders check")
        
        for subscription in subscriptions:
            telegram_id = subscription["telegram_id"]
            
            try:
                # Use notification service to determine if reminder should be sent
                decision = notification_service.should_send_reminder(subscription)
                
                if not decision.should_send:
                    # Skip this subscription (already sent, not in time window, etc.)
                    if decision.reason:
                        logger.debug(f"Skipping reminder for user {telegram_id}: {decision.reason}")
                    continue
                
                language = await resolve_user_language(telegram_id)
                
                # Determine reminder text and keyboard based on reminder type
                reminder_type = decision.reminder_type
                text = None
                keyboard = None
                audit_message = None
                
                if reminder_type == ReminderType.ADMIN_1DAY_6H:
                    text = i18n.get_text(language, "reminder.admin_1day_6h")
                    keyboard = get_subscription_keyboard(language)
                    audit_message = "Admin 1-day reminder (6h before expiry)"
                
                elif reminder_type == ReminderType.ADMIN_7DAYS_24H:
                    text = i18n.get_text(language, "reminder.admin_7days_24h")
                    keyboard = get_tariff_1_month_keyboard(language)
                    audit_message = "Admin 7-day reminder (24h before expiry)"
                
                elif reminder_type == ReminderType.REMINDER_3D:
                    text = i18n.get_text(language, "reminder.paid_3d")
                    keyboard = get_renewal_keyboard(language)
                    audit_message = "Paid subscription reminder (3d before expiry)"
                
                elif reminder_type == ReminderType.REMINDER_24H:
                    text = i18n.get_text(language, "reminder.paid_24h")
                    keyboard = get_renewal_keyboard(language)
                    audit_message = "Paid subscription reminder (24h before expiry)"
                
                elif reminder_type == ReminderType.REMINDER_3H:
                    text = i18n.get_text(language, "reminder.paid_3h")
                    keyboard = get_renewal_keyboard(language)
                    audit_message = "Paid subscription reminder (3h before expiry)"
                
                if text and keyboard:
                    # Send reminder
                    await bot.send_message(telegram_id, text, reply_markup=keyboard)
                    await asyncio.sleep(0.05)  # Telegram rate limit: max 20 msgs/sec

                    # Mark reminder as sent using notification service
                    await notification_service.mark_reminder_sent(telegram_id, reminder_type)
                    
                    # Log to audit_log
                    await database._log_audit_event_atomic_standalone(
                        "reminder_sent",
                        telegram_id,
                        telegram_id,
                        audit_message
                    )
                    
                    logger.info(f"Reminder ({reminder_type.value}) sent to user {telegram_id}")
                
            except TelegramForbiddenError:
                # Пользователь заблокировал бота - это ожидаемое поведение, не ошибка
                logger.info(f"User {telegram_id} blocked bot, skipping reminder")
                continue
            except Exception as e:
                # Ошибка для одного пользователя не должна ломать цикл
                logger.error(f"Error sending reminder to user {telegram_id}: {e}", exc_info=True)
                continue
                
    except Exception as e:
        logger.exception(f"Error in send_smart_reminders: {e}")


async def reminders_task(bot: Bot):
    """Фоновая задача для отправки напоминаний об окончании подписки (выполняется каждые 30-60 минут)"""
    # Небольшая задержка при старте, чтобы БД успела инициализироваться
    await asyncio.sleep(60)
    
    while True:
        try:
            # Отправляем напоминания об окончании подписки
            await send_smart_reminders(bot)
        except Exception as e:
            logger.exception(f"Error in reminders_task: {e}")
        
        # Проверяем каждые 45 минут для баланса между точностью и нагрузкой
        await asyncio.sleep(45 * 60)  # 45 минут в секундах
