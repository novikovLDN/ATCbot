"""Модуль для отправки напоминаний об окончании подписки"""
import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import database
import config
from app import i18n
from app.services.language_service import resolve_user_language
from app.services.notifications import service as notification_service
from app.services.notifications.service import ReminderType
from app.utils.telegram_safe import safe_send_message
from app.core.structured_logger import log_event
# import outline_api  # DISABLED - мигрировали на Xray Core (VLESS)

# Idempotency: skip if reminder sent within this window (container restart guard)
REMINDER_IDEMPOTENCY_WINDOW = timedelta(minutes=30)

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

                # Idempotency: skip if reminder sent recently (container restart guard)
                last_reminder_at = subscription.get("last_reminder_at")
                if last_reminder_at and isinstance(last_reminder_at, datetime):
                    try:
                        # subscription from get_subscriptions_for_reminders is normalized (aware UTC via _from_db_utc)
                        last_at = last_reminder_at if last_reminder_at.tzinfo else last_reminder_at.replace(tzinfo=timezone.utc)
                        delta = datetime.now(timezone.utc) - last_at
                        if 0 <= delta.total_seconds() < REMINDER_IDEMPOTENCY_WINDOW.total_seconds():
                            logger.debug(f"Skipping reminder for user {telegram_id}: last_reminder_at within idempotency window")
                            continue
                    except (TypeError, AttributeError):
                        pass

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
                    # Send reminder (safe_send_message handles chat_not_found, blocked)
                    sent = await safe_send_message(bot, telegram_id, text, reply_markup=keyboard)
                    if sent is None:
                        continue
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

    iteration = 0
    while True:
        iteration += 1
        start_time = time.time()
        try:
            # Отправляем напоминания об окончании подписки
            await send_smart_reminders(bot)
            duration_ms = int((time.time() - start_time) * 1000)
            log_event(
                logger,
                component="worker",
                operation="reminders_iteration",
                correlation_id=str(iteration),
                outcome="success",
                duration_ms=duration_ms,
            )
        except asyncio.CancelledError:
            log_event(
                logger,
                component="worker",
                operation="reminders_iteration",
                correlation_id=str(iteration),
                outcome="cancelled",
            )
            break
        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            log_event(
                logger,
                component="worker",
                operation="reminders_iteration",
                correlation_id=str(iteration),
                outcome="failed",
                duration_ms=duration_ms,
                reason=str(e)[:200],
                level="error",
            )
            logger.exception("Error in reminders_task: %s", e)
        
        # Проверяем каждые 45 минут для баланса между точностью и нагрузкой
        await asyncio.sleep(45 * 60)  # 45 минут в секундах
