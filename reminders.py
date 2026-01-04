"""Модуль для отправки умных напоминаний об окончании подписки"""
import asyncio
import logging
from datetime import datetime, timedelta
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import database
import localization
import config

logger = logging.getLogger(__name__)


def get_renewal_keyboard(language: str) -> InlineKeyboardMarkup:
    """Клавиатура для продления доступа"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=localization.get_text(language, "renew_subscription"),
            callback_data="menu_buy_vpn"
        )]
    ])
    return keyboard


def get_subscription_keyboard(language: str) -> InlineKeyboardMarkup:
    """Клавиатура для оформления подписки"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=localization.get_text(language, "buy_vpn"),
            callback_data="menu_buy_vpn"
        )]
    ])
    return keyboard


def get_tariff_1_month_keyboard(language: str) -> InlineKeyboardMarkup:
    """Клавиатура для подписки на 1 месяц"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=localization.get_text(language, "subscribe_1_month_button", default="🔐 Подписка на 1 месяц"),
            callback_data="tariff_1"
        )]
    ])
    return keyboard


async def send_smart_reminders(bot: Bot):
    """Отправить умные напоминания пользователям"""
    try:
        subscriptions = await database.get_subscriptions_for_reminders()
        
        if not subscriptions:
            return
        
        logger.info(f"Found {len(subscriptions)} subscriptions for reminders check")
        
        now = datetime.now()
        
        for subscription in subscriptions:
            telegram_id = subscription["telegram_id"]
            expires_at = subscription["expires_at"]
            if isinstance(expires_at, str):
                expires_at = datetime.fromisoformat(expires_at)
            
            admin_grant_days = subscription.get("admin_grant_days")
            last_action_type = subscription.get("last_action_type")
            
            # Определяем тип подписки
            is_admin_grant = admin_grant_days is not None or last_action_type == "admin_grant"
            
            try:
                # Получаем язык пользователя
                user = await database.get_user(telegram_id)
                language = user.get("language", "ru") if user else "ru"
                
                time_until_expiry = expires_at - now
                
                # АДМИН-ВЫДАННЫЙ ДОСТУП
                if is_admin_grant:
                    if admin_grant_days == 1:
                        # 1 день - напоминание за 6 часов
                        if (timedelta(hours=5.5) <= time_until_expiry <= timedelta(hours=6.5) and 
                            not subscription.get("reminder_6h_sent", False)):
                            text = localization.get_text(
                                language, 
                                "reminder_admin_1day_6h"
                            )
                            keyboard = get_subscription_keyboard(language)
                            await bot.send_message(telegram_id, text, reply_markup=keyboard)
                            await database.mark_reminder_flag_sent(telegram_id, "reminder_6h_sent")
                            # Логируем в audit_log
                            await database._log_audit_event_atomic_standalone(
                                "reminder_sent",
                                telegram_id,
                                telegram_id,
                                f"Admin 1-day reminder (6h before expiry)"
                            )
                            logger.info(f"Admin 1-day reminder (6h) sent to user {telegram_id}")
                    
                    elif admin_grant_days == 7:
                        # 7 дней - напоминание за 24 часа
                        if (timedelta(hours=23) <= time_until_expiry <= timedelta(hours=25) and 
                            not subscription.get("reminder_24h_sent", False)):
                            text = localization.get_text(
                                language, 
                                "reminder_admin_7days_24h"
                            )
                            keyboard = get_tariff_1_month_keyboard(language)
                            await bot.send_message(telegram_id, text, reply_markup=keyboard)
                            await database.mark_reminder_flag_sent(telegram_id, "reminder_24h_sent")
                            # Логируем в audit_log
                            await database._log_audit_event_atomic_standalone(
                                "reminder_sent",
                                telegram_id,
                                telegram_id,
                                f"Admin 7-day reminder (24h before expiry)"
                            )
                            logger.info(f"Admin 7-day reminder (24h) sent to user {telegram_id}")
                
                # ОПЛАЧЕННЫЕ ТАРИФЫ
                else:
                    # Напоминание за 3 дня
                    if (timedelta(days=2.9) <= time_until_expiry <= timedelta(days=3.1) and 
                        not subscription.get("reminder_3d_sent", False)):
                        text = localization.get_text(
                            language, 
                            "reminder_paid_3d"
                        )
                        keyboard = get_renewal_keyboard(language)
                        await bot.send_message(telegram_id, text, reply_markup=keyboard)
                        await database.mark_reminder_flag_sent(telegram_id, "reminder_3d_sent")
                        # Логируем в audit_log
                        await database._log_audit_event_atomic_standalone(
                            "reminder_sent",
                            telegram_id,
                            telegram_id,
                            f"Paid subscription reminder (3d before expiry)"
                        )
                        logger.info(f"Paid subscription reminder (3d) sent to user {telegram_id}")
                    
                    # Напоминание за 24 часа
                    elif (timedelta(hours=23) <= time_until_expiry <= timedelta(hours=25) and 
                          not subscription.get("reminder_24h_sent", False)):
                        text = localization.get_text(
                            language, 
                            "reminder_paid_24h"
                        )
                        keyboard = get_renewal_keyboard(language)
                        await bot.send_message(telegram_id, text, reply_markup=keyboard)
                        await database.mark_reminder_flag_sent(telegram_id, "reminder_24h_sent")
                        # Логируем в audit_log
                        await database._log_audit_event_atomic_standalone(
                            "reminder_sent",
                            telegram_id,
                            telegram_id,
                            f"Paid subscription reminder (24h before expiry)"
                        )
                        logger.info(f"Paid subscription reminder (24h) sent to user {telegram_id}")
                    
                    # Напоминание за 3 часа
                    elif (timedelta(hours=2.5) <= time_until_expiry <= timedelta(hours=3.5) and 
                          not subscription.get("reminder_3h_sent", False)):
                        text = localization.get_text(
                            language, 
                            "reminder_paid_3h"
                        )
                        keyboard = get_renewal_keyboard(language)
                        await bot.send_message(telegram_id, text, reply_markup=keyboard)
                        await database.mark_reminder_flag_sent(telegram_id, "reminder_3h_sent")
                        # Логируем в audit_log
                        await database._log_audit_event_atomic_standalone(
                            "reminder_sent",
                            telegram_id,
                            telegram_id,
                            f"Paid subscription reminder (3h before expiry)"
                        )
                        logger.info(f"Paid subscription reminder (3h) sent to user {telegram_id}")
                
            except Exception as e:
                # Ошибка для одного пользователя не должна ломать цикл
                logger.error(f"Error sending reminder to user {telegram_id}: {e}", exc_info=True)
                continue
                
    except Exception as e:
        logger.exception(f"Error in send_smart_reminders: {e}")


async def reminders_task(bot: Bot):
    """Фоновая задача для отправки умных напоминаний (выполняется каждые 30 минут)"""
    # Небольшая задержка при старте, чтобы БД успела инициализироваться
    await asyncio.sleep(60)
    
    while True:
        try:
            await send_smart_reminders(bot)
        except Exception as e:
            logger.exception(f"Error in reminders_task: {e}")
        
        # Проверяем каждые 30 минут для более точного тайминга
        await asyncio.sleep(30 * 60)  # 30 минут в секундах
