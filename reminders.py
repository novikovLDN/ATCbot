"""Модуль для отправки умных напоминаний об окончании подписки и уведомлений на основе трафика"""
import asyncio
import logging
from datetime import datetime, timedelta
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import database
import localization
import config
# import outline_api  # DISABLED - мигрировали на Xray Core (VLESS)

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


def get_vip_offer_keyboard(language: str) -> InlineKeyboardMarkup:
    """Клавиатура для предложения VIP-доступа"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=localization.get_text(language, "vip_access_button", default="👑 Улучшить уровень доступа"),
            callback_data="menu_vip_access"
        )]
    ])
    return keyboard


async def _send_notification_with_anti_spam(
    bot: Bot,
    pool,
    telegram_id: int,
    text: str,
    sent_flag_column: str,
    notification_name: str
):
    """Отправить уведомление с обновлением anti-spam полей"""
    try:
        await bot.send_message(telegram_id, text)
        async with pool.acquire() as update_conn:
            await update_conn.execute(
                f"""UPDATE subscriptions 
                   SET {sent_flag_column} = TRUE, 
                       last_notification_sent_at = $1 
                   WHERE telegram_id = $2""",
                datetime.now(), telegram_id
            )
        logger.info(f"Smart notification ({notification_name}) sent to user {telegram_id}")
        return True
    except Exception as e:
        logger.error(f"Error sending notification ({notification_name}) to user {telegram_id}: {e}")
        return False


async def send_smart_notifications(bot: Bot):
    """Отправить умные уведомления на основе трафика и времени"""
    try:
        pool = await database.get_pool()
        async with pool.acquire() as conn:
            # Получаем все активные подписки с UUID (Xray VLESS)
            rows = await conn.fetch("""
                SELECT telegram_id, uuid, activated_at, expires_at, last_bytes,
                       first_traffic_at,
                       smart_notif_no_traffic_20m_sent,
                       smart_notif_no_traffic_24h_sent,
                       smart_notif_first_connection_sent,
                       smart_notif_3days_usage_sent,
                       smart_notif_7days_before_expiry_sent,
                       smart_notif_3days_before_expiry_sent,
                       smart_notif_expiry_day_sent,
                       smart_notif_expired_24h_sent,
                       smart_notif_vip_offer_sent,
                       last_notification_sent_at
                FROM subscriptions
                WHERE status = 'active'
                AND uuid IS NOT NULL
                AND expires_at > NOW()
            """)
        
        if not rows:
            return
        
        logger.info(f"Found {len(rows)} active subscriptions for smart notifications check")
        
        now = datetime.now()
        
        for row in rows:
            subscription = dict(row)
            telegram_id = subscription["telegram_id"]
            uuid = subscription.get("uuid")  # Xray VLESS UUID
            activated_at = subscription.get("activated_at")
            expires_at = subscription["expires_at"]
            last_bytes = subscription.get("last_bytes", 0) or 0
            first_traffic_at = subscription.get("first_traffic_at")
            
            if isinstance(expires_at, str):
                expires_at = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
            if activated_at and isinstance(activated_at, str):
                activated_at = datetime.fromisoformat(activated_at.replace('Z', '+00:00'))
            if first_traffic_at and isinstance(first_traffic_at, str):
                first_traffic_at = datetime.fromisoformat(first_traffic_at.replace('Z', '+00:00'))
            
            try:
                # ANTI-SPAM: Проверяем, прошло ли достаточно времени с последнего уведомления
                last_notification_sent_at = subscription.get("last_notification_sent_at")
                if last_notification_sent_at:
                    if isinstance(last_notification_sent_at, str):
                        last_notification_sent_at = datetime.fromisoformat(last_notification_sent_at.replace('Z', '+00:00'))
                    time_since_last = now - last_notification_sent_at
                    # Минимальный интервал между уведомлениями: 60 минут
                    if time_since_last < timedelta(minutes=60):
                        continue  # Пропускаем этого пользователя, чтобы не спамить
                
                # Получаем язык пользователя
                user = await database.get_user(telegram_id)
                language = user.get("language", "ru") if user else "ru"
                
                # XRAY CORE: Получение трафика через API не поддерживается
                # Используем last_bytes из БД (обновляется другим механизмом при необходимости)
                current_bytes = last_bytes  # Используем сохраненное значение трафика
                if current_bytes is None:
                    current_bytes = 0
                
                # Не обновляем last_bytes в БД, так как Xray API не предоставляет статистику трафика
                # Используем сохраненное значение из БД для проверок
                
                # Проверяем, появился ли трафик впервые
                if current_bytes > 0 and last_bytes == 0 and not first_traffic_at:
                    # Первый трафик - сохраняем время
                    async with pool.acquire() as update_conn:
                        await update_conn.execute(
                            "UPDATE subscriptions SET first_traffic_at = $1 WHERE telegram_id = $2",
                            now, telegram_id
                        )
                    first_traffic_at = now
                
                # 1. УВЕДОМЛЕНИЕ: НЕТ ТРАФИКА ЧЕРЕЗ 20 МИНУТ
                if (activated_at and 
                    (now - activated_at) >= timedelta(minutes=20) and 
                    current_bytes == 0 and 
                    not subscription.get("smart_notif_no_traffic_20m_sent", False)):
                    text = localization.get_text(language, "smart_notif_no_traffic_20m")
                    await _send_notification_with_anti_spam(
                        bot, pool, telegram_id, text,
                        "smart_notif_no_traffic_20m_sent", "no traffic 20m"
                    )
                    continue  # Отправляем только одно уведомление за раз
                
                # 2. УВЕДОМЛЕНИЕ: НЕТ ТРАФИКА ЧЕРЕЗ 24 ЧАСА
                if (activated_at and 
                    (now - activated_at) >= timedelta(hours=24) and 
                    current_bytes == 0 and 
                    not subscription.get("smart_notif_no_traffic_24h_sent", False)):
                    text = localization.get_text(language, "smart_notif_no_traffic_24h")
                    await bot.send_message(telegram_id, text)
                    async with pool.acquire() as update_conn:
                        await update_conn.execute(
                            "UPDATE subscriptions SET smart_notif_no_traffic_24h_sent = TRUE WHERE telegram_id = $1",
                            telegram_id
                        )
                    logger.info(f"Smart notification (no traffic 24h) sent to user {telegram_id}")
                    continue
                
                # 3. УВЕДОМЛЕНИЕ: ПЕРВОЕ ПОДКЛЮЧЕНИЕ (ЕСТЬ ТРАФИК)
                if (current_bytes > 0 and 
                    last_bytes == 0 and 
                    first_traffic_at and 
                    (now - first_traffic_at) >= timedelta(hours=1) and 
                    (now - first_traffic_at) <= timedelta(hours=2) and
                    not subscription.get("smart_notif_first_connection_sent", False)):
                    text = localization.get_text(language, "smart_notif_first_connection")
                    await bot.send_message(telegram_id, text)
                    async with pool.acquire() as update_conn:
                        await update_conn.execute(
                            "UPDATE subscriptions SET smart_notif_first_connection_sent = TRUE WHERE telegram_id = $1",
                            telegram_id
                        )
                    logger.info(f"Smart notification (first connection) sent to user {telegram_id}")
                    continue
                
                # 4. УВЕДОМЛЕНИЕ: 3 ДНЯ ИСПОЛЬЗОВАНИЯ
                if (first_traffic_at and 
                    (now - first_traffic_at) >= timedelta(days=3) and 
                    not subscription.get("smart_notif_3days_usage_sent", False)):
                    text = localization.get_text(language, "smart_notif_3days_usage")
                    await bot.send_message(telegram_id, text)
                    async with pool.acquire() as update_conn:
                        await update_conn.execute(
                            "UPDATE subscriptions SET smart_notif_3days_usage_sent = TRUE WHERE telegram_id = $1",
                            telegram_id
                        )
                    logger.info(f"Smart notification (3 days usage) sent to user {telegram_id}")
                    continue
                
                # 5. УВЕДОМЛЕНИЕ: ЗА 7 ДНЕЙ ДО ОКОНЧАНИЯ
                time_until_expiry = expires_at - now
                if (timedelta(days=6.9) <= time_until_expiry <= timedelta(days=7.1) and 
                    not subscription.get("smart_notif_7days_before_expiry_sent", False)):
                    text = localization.get_text(language, "smart_notif_7days_before_expiry")
                    keyboard = get_renewal_keyboard(language)
                    await bot.send_message(telegram_id, text, reply_markup=keyboard)
                    async with pool.acquire() as update_conn:
                        await update_conn.execute(
                            "UPDATE subscriptions SET smart_notif_7days_before_expiry_sent = TRUE WHERE telegram_id = $1",
                            telegram_id
                        )
                    logger.info(f"Smart notification (7 days before expiry) sent to user {telegram_id}")
                    continue
                
                # 6. УВЕДОМЛЕНИЕ: ЗА 3 ДНЯ ДО ОКОНЧАНИЯ (КЛЮЧЕВОЕ)
                if (timedelta(days=2.9) <= time_until_expiry <= timedelta(days=3.1) and 
                    not subscription.get("smart_notif_3days_before_expiry_sent", False)):
                    text = localization.get_text(language, "smart_notif_3days_before_expiry")
                    keyboard = get_renewal_keyboard(language)
                    await bot.send_message(telegram_id, text, reply_markup=keyboard)
                    async with pool.acquire() as update_conn:
                        await update_conn.execute(
                            "UPDATE subscriptions SET smart_notif_3days_before_expiry_sent = TRUE WHERE telegram_id = $1",
                            telegram_id
                        )
                    logger.info(f"Smart notification (3 days before expiry) sent to user {telegram_id}")
                    continue
                
                # 7. УВЕДОМЛЕНИЕ: В ДЕНЬ ОКОНЧАНИЯ (УТРОМ)
                if (expires_at.date() == now.date() and 
                    now.hour >= 8 and now.hour < 12 and
                    not subscription.get("smart_notif_expiry_day_sent", False)):
                    text = localization.get_text(language, "smart_notif_expiry_day")
                    keyboard = get_renewal_keyboard(language)
                    await bot.send_message(telegram_id, text, reply_markup=keyboard)
                    async with pool.acquire() as update_conn:
                        await update_conn.execute(
                            "UPDATE subscriptions SET smart_notif_expiry_day_sent = TRUE WHERE telegram_id = $1",
                            telegram_id
                        )
                    logger.info(f"Smart notification (expiry day) sent to user {telegram_id}")
                    continue
                
                # 8. УВЕДОМЛЕНИЕ: ПОСЛЕ ОКОНЧАНИЯ (ЧЕРЕЗ 24 ЧАСА)
                if (expires_at < now and 
                    (now - expires_at) >= timedelta(hours=24) and
                    not subscription.get("smart_notif_expired_24h_sent", False)):
                    text = localization.get_text(language, "smart_notif_expired_24h")
                    keyboard = get_subscription_keyboard(language)
                    await bot.send_message(telegram_id, text, reply_markup=keyboard)
                    async with pool.acquire() as update_conn:
                        await update_conn.execute(
                            "UPDATE subscriptions SET smart_notif_expired_24h_sent = TRUE WHERE telegram_id = $1",
                            telegram_id
                        )
                    logger.info(f"Smart notification (expired 24h) sent to user {telegram_id}")
                    continue
                
                # 9. VIP / АПГРЕЙД (ТОЛЬКО АКТИВНЫЕ ПОЛЬЗОВАТЕЛИ)
                if (first_traffic_at and 
                    (now - first_traffic_at) >= timedelta(days=14) and 
                    current_bytes > 0 and
                    not subscription.get("smart_notif_vip_offer_sent", False)):
                    text = localization.get_text(language, "smart_notif_vip_offer")
                    keyboard = get_vip_offer_keyboard(language)
                    await bot.send_message(telegram_id, text, reply_markup=keyboard)
                    async with pool.acquire() as update_conn:
                        await update_conn.execute(
                            "UPDATE subscriptions SET smart_notif_vip_offer_sent = TRUE WHERE telegram_id = $1",
                            telegram_id
                        )
                    logger.info(f"Smart notification (VIP offer) sent to user {telegram_id}")
                    continue
                
            except Exception as e:
                # Ошибка для одного пользователя не должна ломать цикл
                logger.error(f"Error sending smart notification to user {telegram_id}: {e}", exc_info=True)
                continue
                
    except Exception as e:
        logger.exception(f"Error in send_smart_notifications: {e}")


async def send_smart_reminders(bot: Bot):
    """Отправить умные напоминания пользователям (старая логика для совместимости)"""
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
                expires_at = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
            
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
    """Фоновая задача для отправки умных напоминаний и уведомлений (выполняется каждые 30-60 минут)"""
    # Небольшая задержка при старте, чтобы БД успела инициализироваться
    await asyncio.sleep(60)
    
    while True:
        try:
            # Сначала отправляем умные уведомления на основе трафика
            await send_smart_notifications(bot)
            # Затем отправляем старые напоминания (для совместимости)
            await send_smart_reminders(bot)
        except Exception as e:
            logger.exception(f"Error in reminders_task: {e}")
        
        # Проверяем каждые 45 минут для баланса между точностью и нагрузкой
        await asyncio.sleep(45 * 60)  # 45 минут в секундах
