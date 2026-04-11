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


def get_renewal_keyboard_7d(language: str) -> InlineKeyboardMarkup:
    """Клавиатура для напоминания за 7 дней"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n.get_text(language, "reminder.paid_7d_btn"),
            callback_data="menu_buy_vpn"
        )],
        [InlineKeyboardButton(
            text=i18n.get_text(language, "main.profile"),
            callback_data="menu_profile"
        )],
    ])


def get_renewal_keyboard_3d(language: str) -> InlineKeyboardMarkup:
    """Клавиатура для напоминания за 3 дня"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n.get_text(language, "reminder.paid_3d_btn"),
            callback_data="menu_buy_vpn"
        )],
    ])


def get_renewal_keyboard_1d(language: str) -> InlineKeyboardMarkup:
    """Клавиатура для напоминания за 1 день"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n.get_text(language, "reminder.paid_1d_btn"),
            callback_data="menu_buy_vpn"
        )],
    ])


def get_renewal_discount_keyboard(language: str) -> InlineKeyboardMarkup:
    """Клавиатура со скидкой 15% за 3 часа до окончания подписки"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n.get_text(language, "reminder.paid_3h_discount_btn"),
            callback_data="paid_discount_15"
        )],
        [InlineKeyboardButton(
            text=i18n.get_text(language, "subscription.renew"),
            callback_data="menu_buy_vpn"
        )],
    ])


def _buy_keyboard(language: str, text_key: str) -> InlineKeyboardMarkup:
    """Single CTA keyboard with configurable button text."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n.get_text(language, text_key), callback_data="menu_buy_vpn")]
    ])


def get_subscription_keyboard(language: str) -> InlineKeyboardMarkup:
    """Клавиатура для оформления подписки"""
    return _buy_keyboard(language, "main.buy")


def get_tariff_1_month_keyboard(language: str) -> InlineKeyboardMarkup:
    """Клавиатура для подписки на 1 месяц"""
    return _buy_keyboard(language, "main.buy")




async def send_smart_reminders(bot: Bot):
    """Отправить умные напоминания пользователям (старая логика для совместимости)"""
    try:
        subscriptions = await database.get_subscriptions_for_reminders()
        
        if not subscriptions:
            return
        
        logger.info("Found %d subscriptions for reminders check", len(subscriptions))
        
        for subscription in subscriptions:
            telegram_id = subscription["telegram_id"]
            
            try:
                # Use notification service to determine if reminder should be sent
                decision = notification_service.should_send_reminder(subscription)
                
                if not decision.should_send:
                    # Skip this subscription (already sent, not in time window, etc.)
                    if decision.reason:
                        logger.debug("Skipping reminder for user %s: %s", telegram_id, decision.reason)
                    continue

                # Idempotency: skip if reminder sent recently (container restart guard)
                last_reminder_at = subscription.get("last_reminder_at")
                if last_reminder_at and isinstance(last_reminder_at, datetime):
                    try:
                        # subscription from get_subscriptions_for_reminders is normalized (aware UTC via _from_db_utc)
                        last_at = last_reminder_at if last_reminder_at.tzinfo else last_reminder_at.replace(tzinfo=timezone.utc)
                        delta = datetime.now(timezone.utc) - last_at
                        if 0 <= delta.total_seconds() < REMINDER_IDEMPOTENCY_WINDOW.total_seconds():
                            logger.debug("Skipping reminder for user %s: last_reminder_at within idempotency window", telegram_id)
                            continue
                    except (TypeError, AttributeError) as e:
                        logger.warning("Invalid last_reminder_at for user %s: %s", telegram_id, e)

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

                elif reminder_type == ReminderType.REMINDER_7D:
                    text = i18n.get_text(language, "reminder.paid_7d")
                    keyboard = get_renewal_keyboard_7d(language)
                    audit_message = "Paid subscription reminder (7d before expiry)"

                elif reminder_type == ReminderType.REMINDER_3D:
                    text = i18n.get_text(language, "reminder.paid_3d_new")
                    keyboard = get_renewal_keyboard_3d(language)
                    audit_message = "Paid subscription reminder (3d before expiry)"

                elif reminder_type == ReminderType.REMINDER_1D:
                    text = i18n.get_text(language, "reminder.paid_1d")
                    keyboard = get_renewal_keyboard_1d(language)
                    audit_message = "Paid subscription reminder (1d before expiry)"

                elif reminder_type == ReminderType.REMINDER_24H:
                    text = i18n.get_text(language, "reminder.paid_24h")
                    keyboard = get_renewal_keyboard(language)
                    audit_message = "Paid subscription reminder (24h before expiry)"

                elif reminder_type == ReminderType.REMINDER_3H:
                    text = i18n.get_text(language, "reminder.paid_3h_special")
                    keyboard = get_renewal_discount_keyboard(language)
                    audit_message = "Paid subscription reminder (3h before expiry) with 15% discount"
                
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
                    
                    logger.info("Reminder (%s) sent to user %s", reminder_type.value, telegram_id)
                
            except Exception as e:
                # Ошибка для одного пользователя не должна ломать цикл
                logger.error("Error sending reminder to user %s: %s", telegram_id, e, exc_info=True)
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
            raise
        except Exception as e:
            logger.error("reminders: Unexpected error in task loop: %s: %.100s", type(e).__name__, str(e))
            logger.debug("reminders: Full traceback for task loop", exc_info=True)
            iteration_outcome = "failed"
            iteration_error_type = classify_error(e)
            try:
                from app.services.admin_alerts import alert_worker_failure
                await alert_worker_failure(bot, "reminders", e, iteration=iteration_number)
            except Exception:
                pass
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
