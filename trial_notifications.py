"""Модуль для отправки уведомлений о пробном периоде (trial)
Отдельный от reminders.py для платных подписок
"""
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Tuple
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import asyncpg
import database
import localization
import config
from app.services.trials import service as trial_service

logger = logging.getLogger(__name__)

# Singleton guard: предотвращает повторный запуск scheduler
_TRIAL_SCHEDULER_STARTED = False

# Расписание уведомлений получается из service layer
TRIAL_NOTIFICATION_SCHEDULE = trial_service.get_notification_schedule()


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
                       s.trial_notif_6h_sent, s.trial_notif_60h_sent, s.trial_notif_71h_sent
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
                
                # Basic validation - service layer handles business logic
                if not trial_expires_at or not subscription_expires_at:
                    continue
                
                # ФИНАЛЬНОЕ НАПОМИНАНИЕ: за 6 часов до окончания trial
                # Service layer handles all business logic checks
                try:
                    final_reminder_config = trial_service.get_final_reminder_config()
                    final_reminder_sent = row.get(final_reminder_config["db_flag"], False)
                    
                    should_send, reason = await trial_service.should_send_final_reminder(
                        telegram_id=telegram_id,
                        trial_expires_at=trial_expires_at,
                        subscription_expires_at=subscription_expires_at,
                        final_reminder_sent=final_reminder_sent,
                        now=now,
                        conn=conn
                    )
                    
                    if should_send:
                        # Prepare payload via service
                        payload = trial_service.prepare_notification_payload(
                            notification_key=final_reminder_config["notification_key"],
                            has_button=final_reminder_config["has_button"]
                        )
                        
                        # Send notification (I/O operation)
                        success, status = await send_trial_notification(
                            bot, pool, telegram_id, payload["notification_key"], payload["has_button"]
                        )
                        
                        timing = trial_service.calculate_trial_timing(trial_expires_at, now)
                        
                        if success:
                            await conn.execute(
                                f"UPDATE subscriptions SET {final_reminder_config['db_flag']} = TRUE "
                                "WHERE telegram_id = $1 AND source = 'trial' AND status = 'active'",
                                telegram_id
                            )
                            logger.info(
                                f"trial_reminder_sent: user={telegram_id}, notification=final_6h_before_expiry, "
                                f"hours_until_expiry={timing['hours_until_expiry']:.1f}h, sent_at={datetime.now().isoformat()}"
                            )
                        elif status == "failed_permanently":
                            await conn.execute(
                                f"UPDATE subscriptions SET {final_reminder_config['db_flag']} = TRUE "
                                "WHERE telegram_id = $1 AND source = 'trial' AND status = 'active'",
                                telegram_id
                            )
                            logger.warning(
                                f"trial_reminder_failed_permanently: user={telegram_id}, notification=final_6h_before_expiry, "
                                f"reason=forbidden_or_blocked, failed_at={datetime.now().isoformat()}, will_not_retry=True"
                            )
                        else:
                            logger.warning(
                                f"trial_reminder_failed_temporary: user={telegram_id}, notification=final_6h_before_expiry, "
                                f"reason=temporary_error, will_retry=True"
                            )
                        # Skip other notifications for this user in this cycle
                        continue
                    elif reason:
                        logger.debug(
                            f"trial_reminder_skipped: user={telegram_id}, notification=final_6h_before_expiry, "
                            f"reason={reason}"
                        )
                except trial_service.TrialServiceError as e:
                    logger.warning(
                        f"trial_reminder_skipped: user={telegram_id}, notification=final_6h_before_expiry, "
                        f"service_error={type(e).__name__}: {str(e)}"
                    )
                    continue
                
                # Проверяем каждое уведомление в расписании (6h и 48h)
                # Service layer handles all business logic
                notification_flags = {
                    "trial_notif_6h_sent": row.get("trial_notif_6h_sent", False),
                    "trial_notif_60h_sent": row.get("trial_notif_60h_sent", False),
                }
                
                for notification in TRIAL_NOTIFICATION_SCHEDULE:
                    try:
                        # Service layer determines if notification should be sent
                        should_send, reason = await trial_service.should_send_notification(
                            telegram_id=telegram_id,
                            trial_expires_at=trial_expires_at,
                            subscription_expires_at=subscription_expires_at,
                            notification_schedule=notification,
                            notification_flags=notification_flags,
                            now=now,
                            conn=conn
                        )
                        
                        if not should_send:
                            if reason:
                                logger.debug(
                                    f"trial_reminder_skipped: user={telegram_id}, notification={notification['key']}, "
                                    f"reason={reason}"
                                )
                            continue
                        
                        # Prepare payload via service
                        payload = trial_service.prepare_notification_payload(
                            notification_key=notification["key"],
                            has_button=notification["has_button"]
                        )
                        
                        # Send notification (I/O operation)
                        success, status = await send_trial_notification(
                            bot, pool, telegram_id, payload["notification_key"], payload["has_button"]
                        )
                        
                        db_flag = notification.get("db_flag", f"trial_notif_{notification['hours']}h_sent")
                        timing = trial_service.calculate_trial_timing(trial_expires_at, now)
                        
                        if success:
                            # Mark as sent (idempotency)
                            await conn.execute(
                                f"UPDATE subscriptions SET {db_flag} = TRUE "
                                "WHERE telegram_id = $1 AND source = 'trial' AND status = 'active'",
                                telegram_id
                            )
                            notification_flags[db_flag] = True
                            logger.info(
                                f"trial_reminder_sent: user={telegram_id}, notification={notification['key']}, "
                                f"hours_since_activation={timing['hours_since_activation']:.1f}h, sent_at={datetime.now().isoformat()}"
                            )
                        elif status == "failed_permanently":
                            # Mark as permanently failed (idempotency)
                            await conn.execute(
                                f"UPDATE subscriptions SET {db_flag} = TRUE "
                                "WHERE telegram_id = $1 AND source = 'trial' AND status = 'active'",
                                telegram_id
                            )
                            notification_flags[db_flag] = True
                            logger.warning(
                                f"trial_reminder_failed_permanently: user={telegram_id}, notification={notification['key']}, "
                                f"reason=forbidden_or_blocked, failed_at={datetime.now().isoformat()}, "
                                f"will_not_retry=True"
                            )
                        else:
                            # Temporary error - don't mark as sent, will retry later
                            logger.warning(
                                f"trial_reminder_failed_temporary: user={telegram_id}, notification={notification['key']}, "
                                f"reason=temporary_error, will_retry=True"
                            )
                    except trial_service.TrialServiceError as e:
                        logger.warning(
                            f"trial_reminder_skipped: user={telegram_id}, notification={notification['key']}, "
                            f"service_error={type(e).__name__}: {str(e)}"
                        )
                        continue
    except (asyncpg.PostgresError, asyncio.TimeoutError) as e:
        # RESILIENCE FIX: Temporary DB failures are logged as WARNING, not ERROR
        logger.warning(f"trial_notifications: Database temporarily unavailable in process_trial_notifications: {type(e).__name__}: {str(e)[:100]}")
    except Exception as e:
        logger.error(f"trial_notifications: Unexpected error in process_trial_notifications: {type(e).__name__}: {str(e)[:100]}")
        logger.debug("trial_notifications: Full traceback in process_trial_notifications", exc_info=True)


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
                    # Service layer determines if trial should be expired
                    should_expire, reason = await trial_service.should_expire_trial(
                        telegram_id=telegram_id,
                        trial_expires_at=trial_expires_at,
                        now=now,
                        conn=conn
                    )
                    
                    if not should_expire:
                        logger.debug(
                            f"trial_expiry_skipped: user={telegram_id}, reason={reason}"
                        )
                        continue
                    
                    # I/O: Remove UUID from VPN API (if subscription exists)
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
                    
                    # I/O: Mark subscription as expired
                    await conn.execute("""
                        UPDATE subscriptions 
                        SET status = 'expired', uuid = NULL, vpn_key = NULL
                        WHERE telegram_id = $1 AND source = 'trial' AND status = 'active'
                    """, telegram_id)
                    
                    # Service layer determines if completion notification should be sent
                    should_send, send_reason = await trial_service.should_send_completion_notification(
                        telegram_id=telegram_id,
                        conn=conn
                    )
                    
                    if should_send:
                        # Service layer marks trial as completed (idempotent)
                        trial_completed_sent = await trial_service.mark_trial_completed(
                            telegram_id=telegram_id,
                            conn=conn
                        )
                        
                        if trial_completed_sent:
                            # I/O: Get user language and send notification
                            user = await database.get_user(telegram_id)
                            language = user.get("language", "ru") if user else "ru"
                            
                            expired_text = localization.get_text(language, "trial_expired_text")
                            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                                [InlineKeyboardButton(
                                    text="🔐 Купить / Продлить доступ",
                                    callback_data="menu_buy_vpn"
                                )]
                            ])
                            try:
                                await bot.send_message(telegram_id, expired_text, parse_mode="HTML", reply_markup=keyboard)
                                logger.info(
                                    f"trial_expired: notification sent: user={telegram_id}, "
                                    f"trial_used_at={trial_used_at.isoformat() if trial_used_at else None}, "
                                    f"trial_expires_at={trial_expires_at.isoformat() if trial_expires_at else None}"
                                )
                                logger.info(
                                    f"trial_completed: user={telegram_id}, "
                                    f"trial_used_at={trial_used_at.isoformat() if trial_used_at else None}, "
                                    f"trial_expires_at={trial_expires_at.isoformat() if trial_expires_at else None}, "
                                    f"completed_at={now.isoformat()}"
                                )
                            except Exception as e:
                                logger.warning(f"Failed to send trial expiration notification to user {telegram_id}: {e}")
                                # Rollback flag on send error
                                await conn.execute("""
                                    UPDATE users 
                                    SET trial_completed_sent = FALSE 
                                    WHERE telegram_id = $1
                                """, telegram_id)
                        else:
                            logger.info(
                                f"trial_expired_skipped: user={telegram_id}, reason=already_sent"
                            )
                    else:
                        logger.debug(
                            f"trial_completion_notification_skipped: user={telegram_id}, reason={send_reason}"
                        )
                    
                except trial_service.TrialServiceError as e:
                    logger.warning(
                        f"trial_expiry_skipped: user={telegram_id}, service_error={type(e).__name__}: {str(e)}"
                    )
                    continue
                except Exception as e:
                    logger.exception(f"Error expiring trial subscription for user {telegram_id}: {e}")
    except (asyncpg.PostgresError, asyncio.TimeoutError) as e:
        # RESILIENCE FIX: Temporary DB failures are logged as WARNING, not ERROR
        logger.warning(f"trial_notifications: Database temporarily unavailable in expire_trial_subscriptions: {type(e).__name__}: {str(e)[:100]}")
    except Exception as e:
        logger.error(f"trial_notifications: Unexpected error in expire_trial_subscriptions: {type(e).__name__}: {str(e)[:100]}")
        logger.debug("trial_notifications: Full traceback in expire_trial_subscriptions", exc_info=True)


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
            
        except (asyncpg.PostgresError, asyncio.TimeoutError) as e:
            # RESILIENCE FIX: Temporary DB failures don't crash the task loop
            logger.warning(f"trial_notifications: Database temporarily unavailable in scheduler loop: {type(e).__name__}: {str(e)[:100]}")
        except Exception as e:
            logger.error(f"trial_notifications: Unexpected error in scheduler loop: {type(e).__name__}: {str(e)[:100]}")
            logger.debug("trial_notifications: Full traceback for scheduler loop", exc_info=True)
        
        # Ждём 5 минут до следующей проверки
        await asyncio.sleep(300)
