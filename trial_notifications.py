"""Модуль для отправки уведомлений о пробном периоде (trial)
Отдельный от reminders.py для платных подписок
"""
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Tuple
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import database
import localization
import config

logger = logging.getLogger(__name__)

# Singleton guard: предотвращает повторный запуск scheduler
_TRIAL_SCHEDULER_STARTED = False

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
) -> Tuple[bool, str]:
    """Отправить уведомление о trial
    
    Args:
        bot: Bot instance
        pool: Database connection pool
        telegram_id: Telegram ID пользователя
        notification_key: Ключ локализации для текста уведомления
        has_button: Показывать ли кнопку "Купить доступ"
    
    Returns:
        Tuple[bool, str] - статус отправки:
        - (True, "sent") - уведомление отправлено успешно
        - (False, "failed_permanently") - постоянная ошибка (Forbidden/blocked), больше не пытаться
        - (False, "failed_temporary") - временная ошибка, можно повторить позже
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
        
        return (True, "sent")
    except Exception as e:
        error_str = str(e).lower()
        
        # Проверяем на постоянные ошибки (Forbidden/blocked)
        permanent_errors = [
            "forbidden",
            "bot was blocked",
            "user is deactivated",
            "chat not found",
            "user not found"
        ]
        
        if any(keyword in error_str for keyword in permanent_errors):
            logger.warning(
                f"trial_notification_failed_permanently: user={telegram_id}, notification={notification_key}, "
                f"reason=forbidden_or_blocked, error={str(e)}"
            )
            return (False, "failed_permanently")
        else:
            # Временная ошибка - можно повторить позже
            logger.error(
                f"trial_notification_failed_temporary: user={telegram_id}, notification={notification_key}, "
                f"reason=temporary_error, error={str(e)}"
            )
            return (False, "failed_temporary")


async def process_trial_notifications(bot: Bot):
    """Обработать все уведомления о trial
    
    Проверяет всех пользователей с активным trial и отправляет уведомления
    согласно расписанию на основе trial_expires_at.
    
    КРИТИЧЕСКИЕ ПРОВЕРКИ:
    - subscription.source == "trial"
    - subscription.status == "active"
    - subscription.expires_at > now
    - у пользователя НЕТ активной paid-подписки
    - уведомление ещё не отправлялось (idempotency)
    """
    if not database.DB_READY:
        return
    
    try:
        pool = await database.get_pool()
        async with pool.acquire() as conn:
            now = datetime.now()
            
            # Получаем только пользователей с АКТИВНОЙ trial-подпиской
            # ВАЖНО: INNER JOIN гарантирует наличие trial-подписки
            rows = await conn.fetch("""
                SELECT u.telegram_id, u.trial_expires_at,
                       s.id as subscription_id,
                       s.expires_at as subscription_expires_at,
                       s.trial_notif_6h_sent, s.trial_notif_18h_sent, s.trial_notif_30h_sent,
                       s.trial_notif_42h_sent, s.trial_notif_54h_sent, s.trial_notif_60h_sent,
                       s.trial_notif_71h_sent
                FROM users u
                INNER JOIN subscriptions s ON u.telegram_id = s.telegram_id 
                    AND s.source = 'trial' 
                    AND s.status = 'active'
                    AND s.expires_at > $1
                WHERE u.trial_used_at IS NOT NULL
                  AND u.trial_expires_at IS NOT NULL
                  AND u.trial_expires_at > $1
            """, now)
            
            for row in rows:
                telegram_id = row["telegram_id"]
                trial_expires_at = row["trial_expires_at"]
                subscription_expires_at = row["subscription_expires_at"]
                
                if not trial_expires_at or not subscription_expires_at:
                    continue
                
                # КРИТИЧЕСКАЯ ПРОВЕРКА: subscription.expires_at > now
                if subscription_expires_at <= now:
                    logger.info(
                        f"trial_reminder_skipped: user={telegram_id}, reason=subscription_expired, "
                        f"expires_at={subscription_expires_at.isoformat()}"
                    )
                    continue
                
                # КРИТИЧЕСКАЯ ПРОВЕРКА: у пользователя НЕТ активной paid-подписки
                paid_subscription = await conn.fetchrow("""
                    SELECT 1 FROM subscriptions 
                    WHERE telegram_id = $1 
                    AND source = 'payment'
                    AND status = 'active'
                    AND expires_at > $2
                    LIMIT 1
                """, telegram_id, now)
                
                if paid_subscription:
                    logger.info(
                        f"trial_reminder_skipped: user={telegram_id}, reason=has_active_paid_subscription"
                    )
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
                    # Безопасная проверка флага: NULL считается как False
                    already_sent = row.get(sent_flag_column) is True
                    
                    # Уведомление нужно отправить, если:
                    # - прошло достаточно времени (hours_since_activation >= hours)
                    # - но не слишком много (в пределах 1 часа после нужного времени)
                    # - и ещё не отправлено
                    if (hours_since_activation >= hours and 
                        hours_since_activation < hours + 1 and 
                        not already_sent):
                        
                        # ДОПОЛНИТЕЛЬНАЯ ПРОВЕРКА перед отправкой (для безопасности)
                        # Проверяем, что подписка всё ещё активна
                        subscription_check = await conn.fetchrow("""
                            SELECT id, source, status, expires_at
                            FROM subscriptions
                            WHERE telegram_id = $1
                            AND source = 'trial'
                            AND status = 'active'
                            AND expires_at > $2
                        """, telegram_id, now)
                        
                        if not subscription_check:
                            logger.info(
                                f"trial_reminder_skipped: user={telegram_id}, notification={key}, "
                                f"reason=subscription_no_longer_active"
                            )
                            continue
                        
                        # Проверяем ещё раз отсутствие paid-подписки
                        paid_check = await conn.fetchrow("""
                            SELECT 1 FROM subscriptions 
                            WHERE telegram_id = $1 
                            AND source = 'payment'
                            AND status = 'active'
                            AND expires_at > $2
                            LIMIT 1
                        """, telegram_id, now)
                        
                        if paid_check:
                            logger.info(
                                f"trial_reminder_skipped: user={telegram_id}, notification={key}, "
                                f"reason=has_active_paid_subscription"
                            )
                            continue
                        
                        # Отправляем уведомление
                        success, status = await send_trial_notification(
                            bot, pool, telegram_id, key, has_button
                        )
                        
                        if success:
                            # Помечаем как отправленное (idempotency)
                            # sent_at логируется в send_trial_notification
                            await conn.execute(
                                f"UPDATE subscriptions SET {sent_flag_column} = TRUE "
                                f"WHERE telegram_id = $1 AND source = 'trial' AND status = 'active'",
                                telegram_id
                            )
                            logger.info(
                                f"trial_reminder_sent: user={telegram_id}, notification={key}, "
                                f"hours_since_activation={hours_since_activation:.1f}h, sent_at={datetime.now().isoformat()}"
                            )
                        elif status == "failed_permanently":
                            # Помечаем как permanently failed (idempotency)
                            # Используем sent_flag как failed_flag для постоянных ошибок
                            # Это предотвращает повторные попытки
                            await conn.execute(
                                f"UPDATE subscriptions SET {sent_flag_column} = TRUE "
                                f"WHERE telegram_id = $1 AND source = 'trial' AND status = 'active'",
                                telegram_id
                            )
                            logger.warning(
                                f"trial_reminder_failed_permanently: user={telegram_id}, notification={key}, "
                                f"reason=forbidden_or_blocked, failed_at={datetime.now().isoformat()}, "
                                f"will_not_retry=True"
                            )
                        else:
                            # Временная ошибка - не помечаем как sent, попробуем позже
                            logger.warning(
                                f"trial_reminder_failed_temporary: user={telegram_id}, notification={key}, "
                                f"reason=temporary_error, will_retry=True"
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
            # ВАЖНО: Выбираем только тех, у кого trial_expires_at в пределах последних 24 часов
            # Это предотвращает повторную обработку и отправку умного предложения
            rows = await conn.fetch("""
                SELECT u.telegram_id, u.trial_used_at, u.trial_expires_at,
                       s.uuid, s.expires_at as subscription_expires_at
                FROM users u
                LEFT JOIN subscriptions s ON u.telegram_id = s.telegram_id AND s.source = 'trial' AND s.status = 'active'
                WHERE u.trial_used_at IS NOT NULL
                  AND u.trial_expires_at IS NOT NULL
                  AND u.trial_expires_at <= $1
                  AND u.trial_expires_at > $1 - INTERVAL '24 hours'
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
                    
                    # Проверяем, есть ли у пользователя платная подписка
                    # Если есть - пропускаем умное предложение
                    paid_subscription = await conn.fetchrow(
                        """SELECT 1 FROM subscriptions 
                           WHERE telegram_id = $1 
                           AND source = 'payment'
                           LIMIT 1""",
                        telegram_id
                    )
                    
                    user = await database.get_user(telegram_id)
                    language = user.get("language", "ru") if user else "ru"
                    
                    # Если нет платной подписки - отправляем умное предложение
                    if not paid_subscription and trial_used_at:
                        # Вычисляем длительность использования trial
                        # usage_hours = now - trial_used_at (время с момента активации до истечения)
                        usage_duration = now - trial_used_at
                        usage_hours = usage_duration.total_seconds() / 3600
                        
                        # Определяем рекомендуемый тариф
                        if usage_hours < 24:
                            recommended_tariff = "basic"
                            tariff_name = "Basic"
                        elif usage_hours < 48:
                            recommended_tariff = "plus"
                            tariff_name = "Plus"
                        else:
                            recommended_tariff = "plus"
                            tariff_name = "Plus"
                        
                        # Формируем текст умного предложения
                        smart_offer_text = (
                            "🔓 <b>Пробный доступ завершён</b>\n\n"
                            "Вы активно пользовались VPN — защита и стабильность были с вами эти 3 дня.\n\n"
                            f"🔍 <b>Рекомендуем тариф: {tariff_name}</b>\n"
                            "Он лучше подойдёт под ваш стиль использования.\n\n"
                            "🎁 <b>Для вас промокод -30%: YABX30</b>\n"
                            "Введите его на экране выбора тарифа.\n\n"
                            "Один клик — и защита вернётся."
                        )
                        
                        # Создаём клавиатуру с кнопками
                        smart_offer_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(
                                text=localization.get_text(language, "buy_vpn", default="🔐 Купить доступ"),
                                callback_data="menu_buy_vpn"
                            )],
                            [InlineKeyboardButton(
                                text=localization.get_text(language, "profile", default="👤 Мой профиль"),
                                callback_data="menu_profile"
                            )]
                        ])
                        
                        try:
                            await bot.send_message(telegram_id, smart_offer_text, parse_mode="HTML", reply_markup=smart_offer_keyboard)
                            logger.info(
                                f"smart_offer_sent: user={telegram_id}, usage_hours={usage_hours:.1f}, "
                                f"recommended_tariff={recommended_tariff}, trial_used_at={trial_used_at.isoformat()}, "
                                f"trial_expires_at={trial_expires_at.isoformat()}"
                            )
                        except Exception as e:
                            logger.warning(f"Failed to send smart offer to user {telegram_id}: {e}")
                    else:
                        # Если есть платная подписка - отправляем стандартное сообщение
                        expired_text = localization.get_text(language, "trial_expired_text")
                        try:
                            await bot.send_message(telegram_id, expired_text, parse_mode="HTML")
                            logger.info(
                                f"trial_expired: notification sent (paid subscription exists): user={telegram_id}, "
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
    
    SAFE: Singleton guard предотвращает повторный запуск.
    Если scheduler уже запущен, повторные вызовы игнорируются.
    """
    global _TRIAL_SCHEDULER_STARTED
    
    # Singleton guard: предотвращаем повторный запуск
    if _TRIAL_SCHEDULER_STARTED:
        logger.warning("Trial notifications scheduler already running, skipping duplicate start")
        return
    
    # Устанавливаем флаг перед запуском
    _TRIAL_SCHEDULER_STARTED = True
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
