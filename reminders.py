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
from app.utils.logging_helpers import (
    log_worker_iteration_start,
    log_worker_iteration_end,
    classify_error,
)
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

    iteration_number = 0
    while True:
        iteration_number += 1
        iteration_start_time = time.time()
        
        # H1+H2 fix: Add iteration start logging and timeout wrapper
        correlation_id = log_worker_iteration_start(
            worker_name="reminders",
            iteration_number=iteration_number
        )
        
        iteration_outcome = "success"
        iteration_error_type = None
        
        try:
            # H1 fix: Wrap iteration body with timeout
            async def _run_iteration():
                await send_smart_reminders(bot)
            
            try:
                await asyncio.wait_for(_run_iteration(), timeout=120.0)
            except asyncio.TimeoutError:
                logger.error(
                    "WORKER_TIMEOUT worker=reminders exceeded 120s — iteration cancelled"
                )
                iteration_outcome = "timeout"
                iteration_error_type = "timeout"
        except asyncio.CancelledError:
            logger.info("Reminders task cancelled")
            iteration_outcome = "cancelled"
            break
        except Exception as e:
            logger.error(f"reminders: Unexpected error in task loop: {type(e).__name__}: {str(e)[:100]}")
            logger.debug("reminders: Full traceback for task loop", exc_info=True)
            iteration_outcome = "failed"
            iteration_error_type = classify_error(e)
        finally:
            # H2 fix: ITERATION_END always fires in finally block
            duration_ms = int((time.time() - iteration_start_time) * 1000)
            log_worker_iteration_end(
                worker_name="reminders",
                outcome=iteration_outcome,
                items_processed=0,
                error_type=iteration_error_type,
                duration_ms=duration_ms,
            )
        
        if iteration_outcome == "cancelled":
            break
        
        # Проверяем каждые 45 минут для баланса между точностью и нагрузкой
        await asyncio.sleep(45 * 60)  # 45 минут в секундах
