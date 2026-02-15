"""Модуль для отправки уведомлений о пробном периоде (trial)
Отдельный от reminders.py для платных подписок
"""
import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Tuple
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from app.utils.telegram_safe import safe_send_message
import asyncpg
import database
import config
from app import i18n
from app.services.trials import service as trial_service
from app.core.system_state import (
    SystemState,
    healthy_component,
    degraded_component,
    unavailable_component,
)
from app.services.language_service import resolve_user_language
from app.utils.logging_helpers import (
    log_worker_iteration_start,
    log_worker_iteration_end,
    classify_error,
)
from app.core.structured_logger import log_event

logger = logging.getLogger(__name__)

# Singleton guard: предотвращает повторный запуск scheduler
_TRIAL_SCHEDULER_STARTED = False

# Расписание уведомлений получается из service layer
TRIAL_NOTIFICATION_SCHEDULE = trial_service.get_notification_schedule()

# STEP 3 — PART B: WORKER LOOP SAFETY
# Minimum safe sleep on failure to prevent tight retry storms
MINIMUM_SAFE_SLEEP_ON_FAILURE = 60  # seconds (1 minute, less than normal 5-minute interval)

# Production-safe batching: prevent unbounded fetch, long-held connections, event loop starvation
BATCH_SIZE = 100
BATCH_YIELD_SLEEP = 0  # asyncio.sleep(0) for cooperative yield


def get_trial_buy_keyboard(language: str) -> InlineKeyboardMarkup:
    """Клавиатура для покупки доступа (в уведомлениях trial)"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n.get_text(language, "main.buy"),
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
        language = await resolve_user_language(telegram_id)
        
        # Получаем текст уведомления
        text = i18n.get_text(language, notification_key)
        
        # Формируем клавиатуру (если нужно)
        reply_markup = None
        if has_button:
            reply_markup = get_trial_buy_keyboard(language)
        
        # Отправляем уведомление (safe_send_message handles chat_not_found, blocked)
        sent = await safe_send_message(bot, telegram_id, text, reply_markup=reply_markup)
        if sent is None:
            return (False, "failed_permanently")
        await asyncio.sleep(0.05)  # Telegram rate limit: max 20 msgs/sec

        logger.info(
            f"trial_notification_sent: user={telegram_id}, notification={notification_key}, "
            f"has_button={has_button}"
        )
        
        return (True, "sent")
    except Exception as e:
        logger.error(
            f"trial_notification_failed: user={telegram_id}, notification={notification_key}, "
            f"error={str(e)}"
        )
        return (False, "failed_temporary")


async def _process_single_trial_notification(bot: Bot, pool, row: dict, now: datetime):
    """Process trial notifications for a single user. Acquires and releases DB connection internally."""
    telegram_id = row["telegram_id"]
    trial_expires_at = database._from_db_utc(row["trial_expires_at"]) if row["trial_expires_at"] else None
    subscription_expires_at = database._from_db_utc(row["subscription_expires_at"]) if row["subscription_expires_at"] else None
    paid_subscription_expires_at = database._from_db_utc(row["paid_subscription_expires_at"]) if row.get("paid_subscription_expires_at") else None

    if paid_subscription_expires_at:
        logger.info(
            f"trial_expired_skipped_due_to_active_paid_subscription: "
            f"telegram_id={telegram_id}, trial_expires_at={trial_expires_at.isoformat() if trial_expires_at else None}, "
            f"paid_subscription_expires_at={paid_subscription_expires_at.isoformat() if paid_subscription_expires_at else None}, "
            "reason=active_paid_subscription_exists"
        )
        return

    if not trial_expires_at or not subscription_expires_at:
        return

    async with pool.acquire() as conn:
        try:
            # TOCTOU: Re-check active paid subscription (may have been bought after batch fetch)
            active_paid = await database.get_active_paid_subscription(conn, telegram_id, now)
            if active_paid:
                logger.debug("trial_notification skipped due to active paid subscription")
                return

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
                payload = trial_service.prepare_notification_payload(
                    notification_key=final_reminder_config["notification_key"],
                    has_button=final_reminder_config["has_button"]
                )
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
                        f"hours_until_expiry={timing['hours_until_expiry']:.1f}h, sent_at={datetime.now(timezone.utc).isoformat()}"
                    )
                elif status == "failed_permanently":
                    await conn.execute(
                        f"UPDATE subscriptions SET {final_reminder_config['db_flag']} = TRUE "
                        "WHERE telegram_id = $1 AND source = 'trial' AND status = 'active'",
                        telegram_id
                    )
                    logger.warning(
                        f"trial_reminder_failed_permanently: user={telegram_id}, notification=final_6h_before_expiry, "
                        f"reason=forbidden_or_blocked, failed_at={datetime.now(timezone.utc).isoformat()}, will_not_retry=True"
                    )
                else:
                    logger.warning(
                        f"trial_reminder_failed_temporary: user={telegram_id}, notification=final_6h_before_expiry, "
                        f"reason=temporary_error, will_retry=True"
                    )
                return
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
            return

        notification_flags = {
            "trial_notif_6h_sent": row.get("trial_notif_6h_sent", False),
            "trial_notif_60h_sent": row.get("trial_notif_60h_sent", False),
        }
        for notification in TRIAL_NOTIFICATION_SCHEDULE:
            try:
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
                payload = trial_service.prepare_notification_payload(
                    notification_key=notification["key"],
                    has_button=notification["has_button"]
                )
                success, status = await send_trial_notification(
                    bot, pool, telegram_id, payload["notification_key"], payload["has_button"]
                )
                db_flag = notification.get("db_flag", f"trial_notif_{notification['hours']}h_sent")
                timing = trial_service.calculate_trial_timing(trial_expires_at, now)
                if success:
                    await conn.execute(
                        f"UPDATE subscriptions SET {db_flag} = TRUE "
                        "WHERE telegram_id = $1 AND source = 'trial' AND status = 'active'",
                        telegram_id
                    )
                    notification_flags[db_flag] = True
                    logger.info(
                        f"trial_reminder_sent: user={telegram_id}, notification={notification['key']}, "
                        f"hours_since_activation={timing['hours_since_activation']:.1f}h, sent_at={datetime.now(timezone.utc).isoformat()}"
                    )
                elif status == "failed_permanently":
                    await conn.execute(
                        f"UPDATE subscriptions SET {db_flag} = TRUE "
                        "WHERE telegram_id = $1 AND source = 'trial' AND status = 'active'",
                        telegram_id
                    )
                    notification_flags[db_flag] = True
                    logger.warning(
                        f"trial_reminder_failed_permanently: user={telegram_id}, notification={notification['key']}, "
                        f"reason=forbidden_or_blocked, failed_at={datetime.now(timezone.utc).isoformat()}, "
                        f"will_not_retry=True"
                    )
                else:
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
        now = datetime.now(timezone.utc)
        now_db = database._to_db_utc(now)
        last_subscription_id = 0
        total_fetched = 0

        # Query strings (same every batch)
        query_with_reachable = """
            SELECT u.telegram_id, u.trial_expires_at,
                       s.id as subscription_id,
                       s.expires_at as subscription_expires_at,
                       s.trial_notif_6h_sent, s.trial_notif_60h_sent, s.trial_notif_71h_sent,
                       paid_s.expires_at as paid_subscription_expires_at
                FROM users u
                INNER JOIN subscriptions s ON u.telegram_id = s.telegram_id
                    AND s.source = 'trial'
                    AND s.status = 'active'
                    AND s.expires_at > $1
                LEFT JOIN subscriptions paid_s ON u.telegram_id = paid_s.telegram_id
                    AND paid_s.source != 'trial'
                    AND paid_s.status = 'active'
                    AND paid_s.expires_at > $1
                WHERE u.trial_used_at IS NOT NULL
                  AND u.trial_expires_at IS NOT NULL
                  AND u.trial_expires_at > $1
                  AND COALESCE(u.is_reachable, TRUE) = TRUE
                  AND s.id > $2
            ORDER BY s.id ASC
            LIMIT $3
            """
        fallback_query = """
            SELECT u.telegram_id, u.trial_expires_at,
                       s.id as subscription_id,
                       s.expires_at as subscription_expires_at,
                       s.trial_notif_6h_sent, s.trial_notif_60h_sent, s.trial_notif_71h_sent,
                       paid_s.expires_at as paid_subscription_expires_at
                FROM users u
                INNER JOIN subscriptions s ON u.telegram_id = s.telegram_id
                    AND s.source = 'trial'
                    AND s.status = 'active'
                    AND s.expires_at > $1
                LEFT JOIN subscriptions paid_s ON u.telegram_id = paid_s.telegram_id
                    AND paid_s.source != 'trial'
                    AND paid_s.status = 'active'
                    AND paid_s.expires_at > $1
                WHERE u.trial_used_at IS NOT NULL
                  AND u.trial_expires_at IS NOT NULL
                  AND u.trial_expires_at > $1
                  AND s.id > $2
            ORDER BY s.id ASC
            LIMIT $3
            """

        while True:
            async with pool.acquire() as conn:
                try:
                    rows = await conn.fetch(query_with_reachable, now_db, last_subscription_id, BATCH_SIZE)
                except asyncpg.UndefinedColumnError:
                    logger.warning("DB_SCHEMA_OUTDATED: is_reachable missing, trial_notifications fallback to legacy query")
                    rows = await conn.fetch(fallback_query, now_db, last_subscription_id, BATCH_SIZE)

            if not rows:
                break

            total_fetched += len(rows)
            logger.info("[WORKER_ITEMS] worker=trial_notifications fetched=%d (last_id=%d)", len(rows), last_subscription_id)
            if total_fetched > 1000:
                logger.warning("[WORKER_ITEMS] worker=trial_notifications total_fetched=%d > 1000", total_fetched)

            for row in rows:
                await _process_single_trial_notification(bot, pool, dict(row), now)

            last_subscription_id = rows[-1]["subscription_id"]
            await asyncio.sleep(BATCH_YIELD_SLEEP)
    except (asyncpg.PostgresError, asyncio.TimeoutError) as e:
        # RESILIENCE FIX: Temporary DB failures are logged as WARNING, not ERROR
        logger.warning(f"trial_notifications: Database temporarily unavailable in process_trial_notifications: {type(e).__name__}: {str(e)[:100]}")
    except Exception as e:
        logger.error(f"trial_notifications: Unexpected error in process_trial_notifications: {type(e).__name__}: {str(e)[:100]}")
        logger.debug("trial_notifications: Full traceback in process_trial_notifications", exc_info=True)


async def _process_single_trial_expiration(bot: Bot, pool, row: dict, now: datetime):
    """Process expiration for a single trial user. Acquires and releases DB connection internally."""
    telegram_id = row["telegram_id"]
    uuid_val = row["uuid"]
    trial_used_at = database._from_db_utc(row["trial_used_at"]) if row["trial_used_at"] else None
    trial_expires_at = database._from_db_utc(row["trial_expires_at"]) if row["trial_expires_at"] else None

    async with pool.acquire() as conn:
        try:
            # PRODUCTION HOTFIX: Trial must NEVER revoke VPN or modify subscription if user has active paid.
            active_paid = await database.get_active_paid_subscription(conn, telegram_id, now)
            if active_paid:
                paid_expires_at = active_paid["expires_at"]
                logger.info(
                    "Trial cleanup skipped: user has active paid subscription; "
                    f"telegram_id={telegram_id}, trial_expires_at={trial_expires_at.isoformat() if trial_expires_at else None}, "
                    f"paid_expires_at={paid_expires_at.isoformat() if paid_expires_at else None}"
                )
                return

            should_expire, reason = await trial_service.should_expire_trial(
                telegram_id=telegram_id,
                trial_expires_at=trial_expires_at,
                now=now,
                conn=conn
            )
            if not should_expire:
                logger.debug(f"trial_expiry_skipped: user={telegram_id}, reason={reason}")
                return

            logger.info(
                f"TRIAL_EXPIRATION_EXECUTED: "
                f"telegram_id={telegram_id}, trial_expires_at={trial_expires_at.isoformat() if trial_expires_at else None}, "
                f"decision=EXECUTED"
            )

            active_paid_recheck = await database.get_active_paid_subscription(conn, telegram_id, now)
            if active_paid_recheck:
                paid_expires_at = active_paid_recheck["expires_at"]
                logger.info(
                    "Trial cleanup skipped: user has active paid subscription; "
                    f"telegram_id={telegram_id}, trial_expires_at={trial_expires_at.isoformat() if trial_expires_at else None}, "
                    f"paid_expires_at={paid_expires_at.isoformat() if paid_expires_at else None}"
                )
                return

            if uuid_val:
                import vpn_utils
                try:
                    await vpn_utils.remove_vless_user(uuid_val)
                    logger.info(f"trial_expired: VPN access revoked: user={telegram_id}, uuid={uuid_val[:8]}...")
                except Exception as e:
                    logger.warning(f"Failed to remove VPN UUID for expired trial: user={telegram_id}, error={e}")

            await conn.execute("""
                UPDATE subscriptions 
                SET status = 'expired', uuid = NULL, vpn_key = NULL
                WHERE telegram_id = $1 AND source = 'trial' AND status = 'active'
            """, telegram_id)

            should_send, send_reason = await trial_service.should_send_completion_notification(
                telegram_id=telegram_id,
                conn=conn
            )
            if should_send:
                trial_completed_sent = await trial_service.mark_trial_completed(
                    telegram_id=telegram_id,
                    conn=conn
                )
                if trial_completed_sent:
                    language = await resolve_user_language(telegram_id)
                    expired_text = i18n.get_text(language, "trial.expired")
                    keyboard = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(
                            text=i18n.get_text(language, "main.buy"),
                            callback_data="menu_buy_vpn"
                        )]
                    ])
                    sent = await safe_send_message(
                        bot, telegram_id, expired_text,
                        parse_mode="HTML", reply_markup=keyboard
                    )
                    if sent:
                        await asyncio.sleep(0.05)
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
                    else:
                        logger.warning(f"TRIAL_EXPIRED_SKIP_CHAT_NOT_FOUND user={telegram_id}")
                else:
                    logger.info(f"trial_expired_skipped: user={telegram_id}, reason=already_sent")
            else:
                logger.debug(f"trial_completion_notification_skipped: user={telegram_id}, reason={send_reason}")
        except trial_service.TrialServiceError as e:
            logger.warning(f"trial_expiry_skipped: user={telegram_id}, service_error={type(e).__name__}: {str(e)}")
        except Exception as e:
            logger.exception(f"Error expiring trial subscription for user {telegram_id}: {e}")


async def expire_trial_subscriptions(bot: Bot):
    """Завершить истёкшие trial-подписки
    
    Trial рассматривается как временный флаг, не как источник прав доступа.
    Защита: trial НИКОГДА не отменяет подписку, у которой source != 'trial' и expires_at > now().
    
    Когда trial_expires_at <= now:
    - Проверяет наличие активной подписки с source != 'trial' и expires_at > now()
    - Если есть — пропуск (не меняем статус, не трогаем VPN, не шлём уведомления), лог trial_expired_skipped_due_to_active_paid_subscription
    - Иначе: помечает trial-подписку как expired, удаляет trial UUID из VPN API, при необходимости шлёт уведомление
    """
    if not database.DB_READY:
        return

    try:
        pool = await database.get_pool()
        now = datetime.now(timezone.utc)
        now_db = database._to_db_utc(now)
        last_telegram_id = 0

        query_with_reachable = """
            SELECT u.telegram_id, u.trial_used_at, u.trial_expires_at,
                   s.uuid, s.expires_at as subscription_expires_at
            FROM users u
            LEFT JOIN subscriptions s ON u.telegram_id = s.telegram_id AND s.source = 'trial' AND s.status = 'active'
            WHERE u.trial_used_at IS NOT NULL
              AND u.trial_expires_at IS NOT NULL
              AND u.trial_expires_at <= $1
              AND u.trial_expires_at > $1 - INTERVAL '24 hours'
              AND COALESCE(u.is_reachable, TRUE) = TRUE
              AND u.telegram_id > $2
            ORDER BY u.telegram_id ASC
            LIMIT $3
        """
        fallback_query = """
            SELECT u.telegram_id, u.trial_used_at, u.trial_expires_at,
                   s.uuid, s.expires_at as subscription_expires_at
            FROM users u
            LEFT JOIN subscriptions s ON u.telegram_id = s.telegram_id AND s.source = 'trial' AND s.status = 'active'
            WHERE u.trial_used_at IS NOT NULL
              AND u.trial_expires_at IS NOT NULL
              AND u.trial_expires_at <= $1
              AND u.trial_expires_at > $1 - INTERVAL '24 hours'
              AND u.telegram_id > $2
            ORDER BY u.telegram_id ASC
            LIMIT $3
        """

        while True:
            async with pool.acquire() as conn:
                try:
                    rows = await conn.fetch(query_with_reachable, now_db, last_telegram_id, BATCH_SIZE)
                except asyncpg.UndefinedColumnError:
                    logger.warning("DB_SCHEMA_OUTDATED: is_reachable missing, expire_trial fallback to legacy query")
                    rows = await conn.fetch(fallback_query, now_db, last_telegram_id, BATCH_SIZE)

            if not rows:
                break

            for row in rows:
                await _process_single_trial_expiration(bot, pool, dict(row), now)

            last_telegram_id = rows[-1]["telegram_id"]
            await asyncio.sleep(BATCH_YIELD_SLEEP)
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
    
    iteration_number = 0
    
    while True:
        iteration_start_time = time.time()
        iteration_number += 1
        
        # STEP 2.3 — OBSERVABILITY: Structured logging for worker iteration start
        correlation_id = log_worker_iteration_start(
            worker_name="trial_notifications",
            iteration_number=iteration_number
        )
        
        try:
            # STEP 6 — F5: BACKGROUND WORKER SAFETY
            # Global worker guard: respect FeatureFlags, SystemState, CircuitBreaker
            from app.core.feature_flags import get_feature_flags
            feature_flags = get_feature_flags()
            if not feature_flags.background_workers_enabled:
                logger.warning(
                    f"[FEATURE_FLAG] Background workers disabled, skipping iteration in trial_notifications "
                    f"(iteration={iteration_number})"
                )
                outcome = "skipped"
                reason = "background_workers_enabled=false"
                log_worker_iteration_end(
                    worker_name="trial_notifications",
                    outcome=outcome,
                    items_processed=0,
                    duration_ms=(time.time() - iteration_start_time) * 1000,
                    reason=reason,
                )
                await asyncio.sleep(MINIMUM_SAFE_SLEEP_ON_FAILURE)
                continue
            
            # STEP 1.1 - RUNTIME GUARDRAILS: Read SystemState at iteration start
            # STEP 1.2 - BACKGROUND WORKERS CONTRACT: Check system state before processing
            try:
                now = datetime.now(timezone.utc)
                db_ready = database.DB_READY
                
                # Build SystemState for awareness (read-only)
                if db_ready:
                    db_component = healthy_component(last_checked_at=now)
                else:
                    db_component = unavailable_component(
                        error="DB not ready (degraded mode)",
                        last_checked_at=now
                    )
                
                # VPN API component (not critical for trial notifications)
                if config.VPN_ENABLED and config.XRAY_API_URL:
                    vpn_component = healthy_component(last_checked_at=now)
                else:
                    vpn_component = degraded_component(
                        error="VPN API not configured",
                        last_checked_at=now
                    )
                
                # Payments component (always healthy)
                payments_component = healthy_component(last_checked_at=now)
                
                system_state = SystemState(
                    database=db_component,
                    vpn_api=vpn_component,
                    payments=payments_component,
                )
                
                # STEP 1.2: Skip iteration if system is UNAVAILABLE
                # DEGRADED state does NOT stop iteration (workers continue with reduced functionality)
                if system_state.is_unavailable:
                    logger.warning(
                        f"[UNAVAILABLE] system_state — skipping iteration in trial_notifications scheduler "
                        f"(database={system_state.database.status.value})"
                    )
                    await asyncio.sleep(300)  # Sleep before next check
                    continue
                
                # STEP 1.2: DEGRADED state allows continuation (workers continue with reduced functionality)
                if system_state.is_degraded:
                    logger.info(
                        f"[DEGRADED] system_state detected in trial_notifications scheduler "
                        f"(continuing with reduced functionality)"
                    )
            except Exception:
                # Ignore system state errors - continue with normal flow
                pass
            
            # Обрабатываем уведомления
            await process_trial_notifications(bot)
            
            # Завершаем истёкшие trial-подписки
            await expire_trial_subscriptions(bot)
            
            # STEP 2.3 — OBSERVABILITY: Structured logging for worker iteration end (success)
            duration_ms = (time.time() - iteration_start_time) * 1000
            log_worker_iteration_end(
                worker_name="trial_notifications",
                outcome="success",
                items_processed=0,  # Trial notifications don't track items per iteration
                duration_ms=duration_ms
            )
            logger.info("[WORKER_DURATION] worker=trial_notifications duration=%.3fs", duration_ms / 1000.0)
            
        except asyncio.CancelledError:
            log_event(
                logger,
                component="worker",
                operation="trial_notifications_iteration",
                outcome="cancelled",
            )
            break
        except (asyncpg.PostgresError, asyncio.TimeoutError) as e:
            # RESILIENCE FIX: Temporary DB failures don't crash the task loop
            logger.warning(f"trial_notifications: Database temporarily unavailable in scheduler loop: {type(e).__name__}: {str(e)[:100]}")
            
            # STEP 2.3 — OBSERVABILITY: Log iteration end with degraded outcome
            duration_ms = (time.time() - iteration_start_time) * 1000
            log_worker_iteration_end(
                worker_name="trial_notifications",
                outcome="degraded",
                items_processed=0,
                error_type="infra_error",
                duration_ms=duration_ms
            )
            
            # STEP 3 — PART B: WORKER LOOP SAFETY
            # Minimum safe sleep on failure to prevent tight retry storms
            await asyncio.sleep(MINIMUM_SAFE_SLEEP_ON_FAILURE)
            continue  # Skip normal sleep after failure
        except Exception as e:
            logger.error(f"trial_notifications: Unexpected error in scheduler loop: {type(e).__name__}: {str(e)[:100]}")
            logger.debug("trial_notifications: Full traceback for scheduler loop", exc_info=True)
            
            # STEP 2.3 — OBSERVABILITY: Log iteration end with failed outcome
            duration_ms = (time.time() - iteration_start_time) * 1000
            error_type = classify_error(e)
            log_worker_iteration_end(
                worker_name="trial_notifications",
                outcome="failed",
                items_processed=0,
                error_type=error_type,
                duration_ms=duration_ms
            )
            
            # STEP 3 — PART B: WORKER LOOP SAFETY
            # Minimum safe sleep on failure to prevent tight retry storms
            await asyncio.sleep(MINIMUM_SAFE_SLEEP_ON_FAILURE)
            continue  # Skip normal sleep after failure
        
        # STEP 3 — PART B: WORKER LOOP SAFETY
        # Worker always sleeps before next iteration (normal operation)
        # Ждём 5 минут до следующей проверки
        await asyncio.sleep(300)
