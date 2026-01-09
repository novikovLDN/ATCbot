"""Модуль для отправки уведомлений о пробном периоде (trial)
Отдельный от reminders.py для платных подписок
"""
import asyncio
import logging
from datetime import datetime, timedelta
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import database
import localization
import config

logger = logging.getLogger(__name__)

# Расписание уведомлений (в часах от момента активации)
TRIAL_NOTIFICATION_SCHEDULE = [
    {"hours": 6, "key": "trial_notification_6h", "has_button": False},
    {"hours": 18, "key": "trial_notification_18h", "has_button": False},
    {"hours": 30, "key": "trial_notification_30h", "has_button": False},
    {"hours": 42, "key": "trial_notification_42h", "has_button": False},
    {"hours": 54, "key": "trial_notification_54h", "has_button": False},
    {"hours": 60, "key": "trial_notification_60h", "has_button": True},
    {"hours": 71, "key": "trial_notification_71h", "has_button": True},
]


def get_trial_buy_keyboard(language: str) -> InlineKeyboardMarkup:
    """Клавиатура для покупки доступа (в уведомлениях trial)"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=localization.get_text(language, "buy_vpn"),
            callback_data="menu_buy_vpn"
        )]
    ])
    return keyboard


async def send_trial_notification(
    bot: Bot,
    pool,
    telegram_id: int,
    notification_key: str,
    has_button: bool = False
) -> bool:
    """Отправить уведомление о trial
    
    Args:
        bot: Bot instance
        pool: Database connection pool
        telegram_id: Telegram ID пользователя
        notification_key: Ключ локализации для текста уведомления
        has_button: Показывать ли кнопку "Купить доступ"
    
    Returns:
        True если уведомление отправлено, False иначе
    """
    try:
        # Получаем язык пользователя
        user = await database.get_user(telegram_id)
        language = user.get("language", "ru") if user else "ru"
        
        # Получаем текст уведомления
        text = localization.get_text(language, notification_key)
        
        # Формируем клавиатуру (если нужно)
        reply_markup = None
        if has_button:
            reply_markup = get_trial_buy_keyboard(language)
        
        # Отправляем уведомление
        await bot.send_message(telegram_id, text, reply_markup=reply_markup)
        
        logger.info(
            f"trial_notification_sent: user={telegram_id}, notification={notification_key}, "
            f"has_button={has_button}"
        )
        
        return True
    except Exception as e:
        logger.error(f"Error sending trial notification to user {telegram_id}: {e}")
        return False


async def process_trial_notifications(bot: Bot):
    """Обработать все уведомления о trial
    
    Проверяет всех пользователей с активным trial и отправляет уведомления
    согласно расписанию на основе trial_expires_at.
    """
    if not database.DB_READY:
        return
    
    try:
        pool = await database.get_pool()
        async with pool.acquire() as conn:
            now = datetime.now()
            
            # Получаем всех пользователей с активным trial (trial_expires_at > now)
            # и их trial-подписки для проверки флагов уведомлений
            rows = await conn.fetch("""
                SELECT u.telegram_id, u.trial_expires_at,
                       s.trial_notif_6h_sent, s.trial_notif_18h_sent, s.trial_notif_30h_sent,
                       s.trial_notif_42h_sent, s.trial_notif_54h_sent, s.trial_notif_60h_sent,
                       s.trial_notif_71h_sent
                FROM users u
                LEFT JOIN subscriptions s ON u.telegram_id = s.telegram_id AND s.source = 'trial' AND s.status = 'active'
                WHERE u.trial_used_at IS NOT NULL
                  AND u.trial_expires_at IS NOT NULL
                  AND u.trial_expires_at > $1
            """, now)
            
            for row in rows:
                telegram_id = row["telegram_id"]
                trial_expires_at = row["trial_expires_at"]
                
                if not trial_expires_at:
                    continue
                
                # Вычисляем время до окончания trial
                time_until_expiry = trial_expires_at - now
                hours_until_expiry = time_until_expiry.total_seconds() / 3600
                
                # Вычисляем время с момента активации (для обратной совместимости с расписанием)
                # trial_expires_at - now = 72h - hours_until_expiry
                hours_since_activation = 72 - hours_until_expiry
                
                # Проверяем каждое уведомление в расписании
                for notification in TRIAL_NOTIFICATION_SCHEDULE:
                    hours = notification["hours"]
                    key = notification["key"]
                    has_button = notification["has_button"]
                    
                    # Проверяем, нужно ли отправить это уведомление
                    sent_flag_column = f"trial_notif_{hours}h_sent"
                    already_sent = row.get(sent_flag_column, False)
                    
                    # Уведомление нужно отправить, если:
                    # - прошло достаточно времени (hours_since_activation >= hours)
                    # - но не слишком много (в пределах 1 часа после нужного времени)
                    # - и ещё не отправлено
                    # Для 0h уведомления: hours_since_activation должен быть >= 0 и < 1
                    if (hours_since_activation >= hours and 
                        hours_since_activation < hours + 1 and 
                        not already_sent):
                        
                        # Отправляем уведомление
                        success = await send_trial_notification(
                            bot, pool, telegram_id, key, has_button
                        )
                        
                        if success:
                            # Помечаем как отправленное
                            await conn.execute(
                                f"UPDATE subscriptions SET {sent_flag_column} = TRUE WHERE telegram_id = $1",
                                telegram_id
                            )
                            logger.info(
                                f"Trial notification {hours}h sent and marked: user={telegram_id}"
                            )
    
    except Exception as e:
        logger.exception(f"Error processing trial notifications: {e}")


async def expire_trial_subscriptions(bot: Bot):
    """Завершить истёкшие trial-подписки
    
    Когда trial_expires_at <= now:
    - Помечает подписку как expired
    - Удаляет UUID из VPN API
    - Отправляет финальное сообщение пользователю
    - Логирует завершение trial
    """
    if not database.DB_READY:
        return
    
    try:
        pool = await database.get_pool()
        async with pool.acquire() as conn:
            now = datetime.now()
            
            # Получаем всех пользователей с истёкшим trial (trial_expires_at <= now)
            # и их trial-подписки для отзыва доступа
            rows = await conn.fetch("""
                SELECT u.telegram_id, u.trial_used_at, u.trial_expires_at,
                       s.uuid, s.expires_at as subscription_expires_at
                FROM users u
                LEFT JOIN subscriptions s ON u.telegram_id = s.telegram_id AND s.source = 'trial' AND s.status = 'active'
                WHERE u.trial_used_at IS NOT NULL
                  AND u.trial_expires_at IS NOT NULL
                  AND u.trial_expires_at <= $1
            """, now)
            
            for row in rows:
                telegram_id = row["telegram_id"]
                uuid = row["uuid"]
                trial_used_at = row["trial_used_at"]
                trial_expires_at = row["trial_expires_at"]
                
                try:
                    # Удаляем UUID из VPN API (если подписка существует)
                    if uuid:
                        import vpn_utils
                        try:
                            await vpn_utils.remove_vless_user(uuid)
                            logger.info(
                                f"trial_expired: VPN access revoked: user={telegram_id}, uuid={uuid[:8]}..."
                            )
                        except Exception as e:
                            logger.warning(
                                f"Failed to remove VPN UUID for expired trial: user={telegram_id}, error={e}"
                            )
                    
                    # Помечаем подписку как expired (если существует)
                    await conn.execute("""
                        UPDATE subscriptions 
                        SET status = 'expired', uuid = NULL, vpn_key = NULL
                        WHERE telegram_id = $1 AND source = 'trial' AND status = 'active'
                    """, telegram_id)
                    
                    # Отправляем финальное сообщение
                    user = await database.get_user(telegram_id)
                    language = user.get("language", "ru") if user else "ru"
                    
                    expired_text = localization.get_text(language, "trial_expired_text")
                    
                    try:
                        await bot.send_message(telegram_id, expired_text, parse_mode="HTML")
                        logger.info(
                            f"trial_expired: notification sent: user={telegram_id}, "
                            f"trial_used_at={trial_used_at.isoformat() if trial_used_at else None}, "
                            f"trial_expires_at={trial_expires_at.isoformat() if trial_expires_at else None}"
                        )
                    except Exception as e:
                        logger.warning(f"Failed to send trial expiration notification to user {telegram_id}: {e}")
                    
                    logger.info(
                        f"trial_completed: user={telegram_id}, "
                        f"trial_used_at={trial_used_at.isoformat() if trial_used_at else None}, "
                        f"trial_expires_at={trial_expires_at.isoformat() if trial_expires_at else None}, "
                        f"completed_at={now.isoformat()}"
                    )
                    
                except Exception as e:
                    logger.exception(f"Error expiring trial subscription for user {telegram_id}: {e}")
    
    except Exception as e:
        logger.exception(f"Error expiring trial subscriptions: {e}")


async def run_trial_scheduler(bot: Bot):
    """Основной цикл scheduler для trial-уведомлений
    
    Запускается каждые 5 минут для проверки и отправки уведомлений.
    """
    logger.info("Trial notifications scheduler started")
    
    while True:
        try:
            # Обрабатываем уведомления
            await process_trial_notifications(bot)
            
            # Завершаем истёкшие trial-подписки
            await expire_trial_subscriptions(bot)
            
        except Exception as e:
            logger.exception(f"Error in trial scheduler: {e}")
        
        # Ждём 5 минут до следующей проверки
        await asyncio.sleep(300)
