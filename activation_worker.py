"""Модуль для активации отложенных VPN подписок"""
import asyncio
import logging
import os
import random
import time
from datetime import datetime, timezone
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from app.utils.telegram_safe import safe_send_message
import asyncpg
import database
import config
import admin_notifications
from app.services.activation import service as activation_service
from app.services.activation.exceptions import (
    ActivationServiceError,
    ActivationNotAllowedError,
    ActivationMaxAttemptsReachedError,
    ActivationFailedError,
    VPNActivationError,
)
from app.services.language_service import resolve_user_language
from app import i18n
from app.utils.logging_helpers import (
    log_worker_iteration_start,
    log_worker_iteration_end,
    classify_error,
)
from app.core.structured_logger import log_event
from app.core.cooperative_yield import cooperative_yield
from app.core.pool_monitor import acquire_connection

logger = logging.getLogger(__name__)

# Event loop protection: max iteration time (prevents 300s blocking)
MAX_ITERATION_SECONDS = int(os.getenv("ACTIVATION_WORKER_MAX_ITERATION_SECONDS", "15"))
_worker_lock = asyncio.Lock()

# Конфигурация интервала проверки активации (по умолчанию 5 минут)
ACTIVATION_INTERVAL_SECONDS = int(os.getenv("ACTIVATION_INTERVAL_SECONDS", "300"))  # 5 минут
if ACTIVATION_INTERVAL_SECONDS < 60:  # Минимум 1 минута
    ACTIVATION_INTERVAL_SECONDS = 60
if ACTIVATION_INTERVAL_SECONDS > 1800:  # Максимум 30 минут
    ACTIVATION_INTERVAL_SECONDS = 1800

# Максимальное количество попыток активации (используется для логирования)
MAX_ACTIVATION_ATTEMPTS = activation_service.get_max_activation_attempts()

async def process_pending_activations(bot: Bot) -> tuple[int, str]:
    """
    Обработать подписки с отложенной активацией (activation_status='pending')
    
    ИНВАРИАНТЫ:
    - НЕ трогаем payments
    - НЕ трогаем expires_at
    - НЕ создаём новые подписки
    - НЕ дублируем UUID
    - Только перевод состояния: pending -> active или failed
    
    STEP 1.2 - BACKGROUND WORKERS CONTRACT:
    - Each iteration is stateless → no in-memory state across iterations
    - Each iteration may be safely skipped → no side effects if skipped
    - No unbounded retries → max_attempts enforced by activation_service
    - Errors do NOT kill the loop → exceptions caught at task level
    - All external calls guarded by retry_async → transient errors retried
    
    STEP 1.3 - EXTERNAL DEPENDENCIES POLICY:
    - DB unavailable → function returns early (no error raised)
    - VPN API unavailable → activation skipped, subscription remains 'pending'
    - VPN API disabled → activation skipped, subscription remains 'pending' (NOT error)
    - Domain exceptions (ActivationServiceError) → NOT retried, logged and handled
    
    Args:
        bot: Экземпляр Telegram бота для отправки уведомлений
    
    Returns:
        Tuple of (items_processed, outcome) where outcome is "success" | "degraded" | "failed" | "skipped"
    """
    if not database.DB_READY:
        logger.debug("Skipping activation worker: DB not ready")
        return (0, "skipped")
    
    if not config.VPN_ENABLED:
        logger.debug("Skipping activation worker: VPN API not enabled")
        return (0, "skipped")
    
    # RESILIENCE FIX: Handle temporary DB unavailability gracefully
    try:
        pool = await database.get_pool()
        if pool is None:
            logger.warning("Activation worker: Cannot get DB pool")
            return (0, "skipped")
    except (asyncpg.PostgresError, asyncio.TimeoutError, RuntimeError) as e:
        logger.warning(f"activation_worker: Database temporarily unavailable (pool acquisition failed): {type(e).__name__}: {str(e)[:100]}")
        return (0, "skipped")
    except Exception as e:
        logger.error(f"activation_worker: Unexpected error getting DB pool: {type(e).__name__}: {str(e)[:100]}")
        return (0, "failed")
    
    items_processed = 0
    outcome = "success"
    
    try:
        # Fetch pending list with one short-lived connection (no sleep while holding conn)
        async with acquire_connection(pool, "activation_fetch_pending") as conn:
            pending_subscriptions = await activation_service.get_pending_subscriptions(
                max_attempts=MAX_ACTIVATION_ATTEMPTS,
                limit=50,
                conn=conn
            )
            pending_for_notification = await activation_service.get_pending_for_notification(
                threshold_minutes=activation_service.get_notification_threshold_minutes(),
                conn=conn
            )
            if pending_for_notification:
                total_pending_count = await conn.fetchval(
                    "SELECT COUNT(*) FROM subscriptions WHERE activation_status = 'pending'"
                ) or 0
                if total_pending_count > 0:
                    await admin_notifications.notify_admin_pending_activations(
                        bot,
                        total_pending_count,
                        pending_for_notification
                    )
        # conn released here

        if not pending_subscriptions:
            logger.debug("No pending activations found")
            return (0, "success")

        logger.info(f"Found {len(pending_subscriptions)} pending activations to process")
        iteration_start = time.monotonic()

        for i, pending_sub in enumerate(pending_subscriptions):
            if i > 0 and i % 50 == 0:
                await cooperative_yield()
            if time.monotonic() - iteration_start > MAX_ITERATION_SECONDS:
                logger.warning("Activation worker iteration time limit reached, breaking early")
                break
            items_processed += 1
            telegram_id = pending_sub.telegram_id
            subscription_id = pending_sub.subscription_id
            current_attempts = pending_sub.activation_attempts
            expires_at = pending_sub.expires_at

            # POOL STABILITY: attempt_activation uses pool and does not hold conn during HTTP.
            if activation_service.is_subscription_expired(expires_at):
                logger.warning(
                    f"ACTIVATION_SKIP_EXPIRED [subscription_id={subscription_id}, "
                    f"user={telegram_id}, expires_at={expires_at.isoformat() if expires_at else 'N/A'}]"
                )
                try:
                    async with acquire_connection(pool, "activation_mark_expired") as conn:
                        await activation_service.mark_expired_subscription_failed(
                            subscription_id,
                            conn=conn
                        )
                except Exception as e:
                    logger.error(f"Failed to mark expired subscription as failed: {e}")
            else:
                logger.info(
                    f"ACTIVATION_RETRY_ATTEMPT [subscription_id={subscription_id}, "
                    f"user={telegram_id}, attempt={current_attempts + 1}/{MAX_ACTIVATION_ATTEMPTS}]"
                )
                try:
                    activation_start_time = time.time()
                    result = await activation_service.attempt_activation(
                        subscription_id=subscription_id,
                        telegram_id=telegram_id,
                        current_attempts=current_attempts,
                        pool=pool
                    )
                    activation_duration_ms = (time.time() - activation_start_time) * 1000
                    uuid_preview = f"{result.uuid[:8]}..." if result.uuid and len(result.uuid) > 8 else (result.uuid or "N/A")
                    logger.info(
                        f"ACTIVATION_SUCCESS [subscription_id={subscription_id}, "
                        f"user={telegram_id}, uuid={uuid_preview}, attempt={result.attempts}, "
                        f"latency_ms={activation_duration_ms:.2f}]"
                    )
                    try:
                        async with acquire_connection(pool, "activation_notification_check") as conn:
                            subscription_check = await conn.fetchrow(
                                "SELECT activation_status, uuid, subscription_type FROM subscriptions WHERE id = $1",
                                subscription_id
                            )
                        if not subscription_check or subscription_check["activation_status"] != "active":
                            logger.warning(
                                f"ACTIVATION_NOTIFICATION_SKIP [subscription_id={subscription_id}, "
                                f"user={telegram_id}, reason=subscription_not_active]"
                            )
                        elif subscription_check.get("uuid") != result.uuid:
                            logger.info(
                                f"ACTIVATION_NOTIFICATION_SKIP_IDEMPOTENT [subscription_id={subscription_id}, "
                                f"user={telegram_id}, reason=already_notified]"
                            )
                        else:
                            from app.handlers.common.keyboards import get_vpn_key_keyboard
                            language = await resolve_user_language(telegram_id)
                            expires_str = expires_at.strftime("%d.%m.%Y") if expires_at else "N/A"
                            sub_type = (subscription_check.get("subscription_type") or "basic").strip().lower()
                            if sub_type not in ("basic", "plus"):
                                sub_type = "basic"
                            vpn_key = result.vpn_key
                            vpn_key_plus = getattr(result, "vpn_key_plus", None)
                            keyboard = get_vpn_key_keyboard(language)
                            text = i18n.get_text(
                                language,
                                "payment.approved",
                                date=expires_str
                            )
                            sent1 = await safe_send_message(
                                bot, telegram_id, text,
                                reply_markup=keyboard, parse_mode="HTML"
                            )
                            if sent1 is None:
                                pass  # continue to next sub after block
                            elif vpn_key:
                                await safe_send_message(
                                    bot, telegram_id,
                                    "🇩🇪 <b>Atlas Secure</b>",
                                    parse_mode="HTML"
                                )
                                await safe_send_message(
                                    bot, telegram_id,
                                    f"<code>{vpn_key}</code>",
                                    parse_mode="HTML"
                                )
                                if sub_type == "plus" and vpn_key_plus:
                                    await safe_send_message(
                                        bot, telegram_id,
                                        "⚪️ <b>Atlas Secure - White List</b>",
                                        parse_mode="HTML"
                                    )
                                    await safe_send_message(
                                        bot, telegram_id,
                                        f"<code>{vpn_key_plus}</code>",
                                        parse_mode="HTML"
                                    )
                            logger.info(
                                f"ACTIVATION_NOTIFICATION_SENT [subscription_id={subscription_id}, user={telegram_id}]"
                            )
                    except Exception as e:
                        logger.error(
                            f"Failed to send activation notification to user {telegram_id}: {e}"
                            )
                except VPNActivationError as e:
                    error_msg = str(e)
                    new_attempts = current_attempts + 1
                    try:
                        # Simple VPN API availability check
                        vpn_api_permanently_disabled = not config.VPN_ENABLED
                        vpn_api_temporarily_unavailable = False  # Simplified - no SystemState check
                    except Exception:
                        vpn_api_permanently_disabled = not config.VPN_ENABLED
                        vpn_api_temporarily_unavailable = config.VPN_ENABLED
                    if vpn_api_permanently_disabled:
                        logger.warning(
                            f"ACTIVATION_FAILED_VPN_DISABLED [subscription_id={subscription_id}, "
                            f"user={telegram_id}, attempt={new_attempts}/{MAX_ACTIVATION_ATTEMPTS}, "
                            f"error={error_msg}]"
                        )
                    elif vpn_api_temporarily_unavailable:
                        logger.info(
                            f"ACTIVATION_SKIP_VPN_UNAVAILABLE [subscription_id={subscription_id}, "
                            f"user={telegram_id}, attempt={new_attempts}/{MAX_ACTIVATION_ATTEMPTS}, "
                            f"reason=VPN_API_temporarily_unavailable, will_retry=True]"
                        )
                    else:
                        logger.warning(
                            f"ACTIVATION_FAILED [subscription_id={subscription_id}, "
                            f"user={telegram_id}, attempt={new_attempts}/{MAX_ACTIVATION_ATTEMPTS}, "
                            f"error={error_msg}]"
                        )
                    try:
                        should_mark_failed = (
                            vpn_api_permanently_disabled and
                            new_attempts >= MAX_ACTIVATION_ATTEMPTS
                        )
                        async with acquire_connection(pool, "activation_mark_failed") as conn:
                            await activation_service.mark_activation_failed(
                                subscription_id=subscription_id,
                                new_attempts=new_attempts,
                                error_msg=error_msg,
                                max_attempts=MAX_ACTIVATION_ATTEMPTS,
                                conn=conn,
                                mark_as_failed=should_mark_failed
                            )
                        if should_mark_failed:
                            logger.error(
                                f"ACTIVATION_FAILED_FINAL [subscription_id={subscription_id}, "
                                f"user={telegram_id}, attempts={new_attempts}, error={error_msg}]"
                            )
                            try:
                                admin_lang = "ru"
                                admin_message = (
                                    f"{i18n.get_text(admin_lang, 'admin.activation_error_title')}\n\n"
                                    f"{i18n.get_text(admin_lang, 'admin.activation_error_subscription_id', subscription_id=subscription_id)}\n"
                                    f"{i18n.get_text(admin_lang, 'admin.activation_error_user', telegram_id=telegram_id)}\n"
                                    f"{i18n.get_text(admin_lang, 'admin.activation_error_attempts', attempts=new_attempts, max_attempts=MAX_ACTIVATION_ATTEMPTS)}\n"
                                    f"{i18n.get_text(admin_lang, 'admin.activation_error_error', error_msg=error_msg)}\n\n"
                                    f"{i18n.get_text(admin_lang, 'admin.activation_error_status')}\n"
                                    f"{i18n.get_text(admin_lang, 'admin.activation_error_action')}"
                                )
                                if await safe_send_message(
                                    bot, config.ADMIN_TELEGRAM_ID,
                                    admin_message, parse_mode="Markdown"
                                ):
                                    logger.info(
                                        f"Admin notification sent: Activation failed for subscription {subscription_id}"
                                    )
                            except Exception as admin_error:
                                logger.error(
                                    f"Failed to send admin notification: {admin_error}"
                                )
                    except Exception as db_error:
                        logger.error(
                            f"Failed to update activation attempts in DB: {db_error}"
                        )
                except ActivationFailedError as e:
                    error_msg = str(e)
                    new_attempts = current_attempts + 1
                    logger.warning(
                        f"ACTIVATION_FAILED [subscription_id={subscription_id}, "
                        f"user={telegram_id}, attempt={new_attempts}/{MAX_ACTIVATION_ATTEMPTS}, "
                        f"error={error_msg}]"
                    )
                    try:
                        async with acquire_connection(pool, "activation_mark_failed_2") as conn:
                            await activation_service.mark_activation_failed(
                                subscription_id=subscription_id,
                                new_attempts=new_attempts,
                                error_msg=error_msg,
                                max_attempts=MAX_ACTIVATION_ATTEMPTS,
                                conn=conn
                            )
                    except Exception as db_error:
                        logger.error(
                            f"Failed to update activation attempts in DB: {db_error}"
                        )

            # Connection released before sleep — no conn held during asyncio.sleep
            await asyncio.sleep(0.5)

        return (items_processed, outcome)
    except (asyncpg.PostgresError, asyncio.TimeoutError) as e:
        # RESILIENCE FIX: Temporary DB failures are logged as WARNING, not ERROR
        logger.warning(f"activation_worker: Database temporarily unavailable in process_pending_activations: {type(e).__name__}: {str(e)[:100]}")
        return (items_processed, "degraded")
    except Exception as e:
        logger.error(f"activation_worker: Unexpected error in process_pending_activations: {type(e).__name__}: {str(e)[:100]}")
        logger.debug("activation_worker: Full traceback in process_pending_activations", exc_info=True)
        error_type = classify_error(e)
        return (items_processed, "failed")


async def activation_worker_task(bot: Bot):
    """
    Фоновая задача для периодической обработки отложенных активаций
    
    Args:
        bot: Экземпляр Telegram бота
    """
    logger.info(f"Activation worker task started (interval={ACTIVATION_INTERVAL_SECONDS}s, max_attempts={MAX_ACTIVATION_ATTEMPTS})")
    
    # Prevent worker burst at startup
    jitter_s = random.uniform(5, 60)
    await asyncio.sleep(jitter_s)
    logger.debug("activation_worker: startup jitter done (%.1fs)", jitter_s)
    
    iteration_number = 0
    
    # STEP 3 — PART B: WORKER LOOP SAFETY
    # Minimum safe sleep on failure to prevent tight retry storms
    MINIMUM_SAFE_SLEEP_ON_FAILURE = 10  # seconds
    
    while True:
        iteration_start_time = time.time()
        iteration_number += 1
        
        # STEP 2.3 — OBSERVABILITY: Structured logging for worker iteration start
        correlation_id = log_worker_iteration_start(
            worker_name="activation_worker",
            iteration_number=iteration_number
        )
        
        items_processed = 0
        outcome = "success"
        iteration_error_type = None
        should_exit_loop = False
        
        try:
            # Feature flag check
            from app.core.feature_flags import get_feature_flags
            feature_flags = get_feature_flags()
            if not feature_flags.background_workers_enabled:
                logger.warning(
                    f"[FEATURE_FLAG] Background workers disabled, skipping iteration in activation_worker "
                    f"(iteration={iteration_number})"
                )
                outcome = "skipped"
                reason = "background_workers_enabled=false"
                log_worker_iteration_end(
                    worker_name="activation_worker",
                    outcome=outcome,
                    items_processed=0,
                    duration_ms=(time.time() - iteration_start_time) * 1000,
                    reason=reason,
                )
                await asyncio.sleep(MINIMUM_SAFE_SLEEP_ON_FAILURE)
                continue
            
            # Simple DB readiness check
            if not database.DB_READY:
                logger.warning("activation_worker: skipping — DB not ready")
                outcome = "skipped"
                reason = "DB not ready"
                log_worker_iteration_end(
                    worker_name="activation_worker",
                    outcome=outcome,
                    items_processed=0,
                    duration_ms=(time.time() - iteration_start_time) * 1000,
                    reason=reason,
                )
                await asyncio.sleep(MINIMUM_SAFE_SLEEP_ON_FAILURE)
                continue
            
            # H1 fix: Wrap iteration body with timeout
            async def _run_iteration():
                # Process pending activations (lock prevents overlapping iterations)
                async with _worker_lock:
                    return await process_pending_activations(bot)
            
            try:
                items_processed, outcome = await asyncio.wait_for(_run_iteration(), timeout=120.0)
            except asyncio.TimeoutError:
                logger.error(
                    "WORKER_TIMEOUT worker=activation_worker exceeded 120s — iteration cancelled"
                )
                items_processed = 0
                outcome = "timeout"
                iteration_error_type = "timeout"
            except Exception as e:
                logger.exception(f"activation_worker: Unexpected error in iteration: {type(e).__name__}: {str(e)[:100]}")
                items_processed = 0
                outcome = "failed"
                iteration_error_type = classify_error(e)
            
        except asyncio.CancelledError:
            logger.info("Activation worker task cancelled")
            outcome = "cancelled"
            should_exit_loop = True
        except (asyncpg.PostgresError, asyncio.TimeoutError) as e:
            # RESILIENCE FIX: Temporary DB failures don't crash the task loop
            logger.warning(f"activation_worker: Database temporarily unavailable in task loop: {type(e).__name__}: {str(e)[:100]}")
            outcome = "degraded"
            iteration_error_type = "infra_error"
        except Exception as e:
            logger.error(f"activation_worker: Unexpected error in task loop: {type(e).__name__}: {str(e)[:100]}")
            logger.debug("activation_worker: Full traceback for task loop", exc_info=True)
            outcome = "failed"
            iteration_error_type = classify_error(e)
        finally:
            # H2 fix: ITERATION_END always fires in finally block
            duration_ms = (time.time() - iteration_start_time) * 1000
            error_type = iteration_error_type if 'iteration_error_type' in locals() else (None if outcome == "success" else "infra_error")
            log_worker_iteration_end(
                worker_name="activation_worker",
                outcome=outcome,
                items_processed=items_processed if 'items_processed' in locals() else 0,
                error_type=error_type,
                duration_ms=duration_ms
            )
            if outcome not in ("success", "cancelled", "skipped"):
                await asyncio.sleep(MINIMUM_SAFE_SLEEP_ON_FAILURE)
        
        if should_exit_loop:
            break
        
        # Sleep after iteration completes (outside try/finally)
        await asyncio.sleep(ACTIVATION_INTERVAL_SECONDS)
