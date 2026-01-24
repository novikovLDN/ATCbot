"""Модуль для активации отложенных VPN подписок"""
import asyncio
import logging
import os
import time
from datetime import datetime
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import asyncpg
import database
import localization
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
from app.core.system_state import (
    SystemState,
    healthy_component,
    degraded_component,
    unavailable_component,
)
from app.core.recovery_cooldown import (
    get_recovery_cooldown,
    ComponentName,
)
from app.core.metrics import get_metrics
from app.core.cost_model import get_cost_model, CostCenter
from app.utils.logging_helpers import (
    log_worker_iteration_start,
    log_worker_iteration_end,
    classify_error,
)

logger = logging.getLogger(__name__)

# Конфигурация интервала проверки активации (по умолчанию 5 минут)
ACTIVATION_INTERVAL_SECONDS = int(os.getenv("ACTIVATION_INTERVAL_SECONDS", "300"))  # 5 минут
if ACTIVATION_INTERVAL_SECONDS < 60:  # Минимум 1 минута
    ACTIVATION_INTERVAL_SECONDS = 60
if ACTIVATION_INTERVAL_SECONDS > 1800:  # Максимум 30 минут
    ACTIVATION_INTERVAL_SECONDS = 1800

# Максимальное количество попыток активации (используется для логирования)
MAX_ACTIVATION_ATTEMPTS = activation_service.get_max_activation_attempts()

# B4.4 - GRACEFUL BACKGROUND RECOVERY: Track recovery state for warm-up iterations
_recovery_warmup_iterations: int = 0
_recovery_warmup_threshold: int = 3  # Number of successful iterations before normal operation


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
        async with pool.acquire() as conn:
            # Get pending subscriptions using activation service
            pending_subscriptions = await activation_service.get_pending_subscriptions(
                max_attempts=MAX_ACTIVATION_ATTEMPTS,
                limit=50,
                conn=conn
            )
            
            # Get subscriptions for admin notification using activation service
            pending_for_notification = await activation_service.get_pending_for_notification(
                threshold_minutes=activation_service.get_notification_threshold_minutes(),
                conn=conn
            )
            
            # Send admin notification if needed
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
            
            if not pending_subscriptions:
                logger.debug("No pending activations found")
                return (0, "success")
            
            logger.info(f"Found {len(pending_subscriptions)} pending activations to process")
            
            for pending_sub in pending_subscriptions:
                items_processed += 1
                telegram_id = pending_sub.telegram_id
                subscription_id = pending_sub.subscription_id
                current_attempts = pending_sub.activation_attempts
                expires_at = pending_sub.expires_at
                
                # Check if subscription expired using activation service
                if activation_service.is_subscription_expired(expires_at):
                    logger.warning(
                        f"ACTIVATION_SKIP_EXPIRED [subscription_id={subscription_id}, "
                        f"user={telegram_id}, expires_at={expires_at.isoformat() if expires_at else 'N/A'}]"
                    )
                    # Mark as failed using activation service
                    try:
                        await activation_service.mark_expired_subscription_failed(
                            subscription_id,
                            conn=conn
                        )
                    except Exception as e:
                        logger.error(f"Failed to mark expired subscription as failed: {e}")
                    continue
                
                # Attempt activation using activation service
                logger.info(
                    f"ACTIVATION_RETRY_ATTEMPT [subscription_id={subscription_id}, "
                    f"user={telegram_id}, attempt={current_attempts + 1}/{MAX_ACTIVATION_ATTEMPTS}]"
                )
                
                try:
                    # PART E — SLO SIGNAL IDENTIFICATION: Subscription activation latency
                    # This activation attempt is an SLO signal for subscription activation latency.
                    # Track: duration from subscription creation to successful activation.
                    activation_start_time = time.time()
                    result = await activation_service.attempt_activation(
                        subscription_id=subscription_id,
                        telegram_id=telegram_id,
                        current_attempts=current_attempts,
                        conn=conn
                    )
                    activation_duration_ms = (time.time() - activation_start_time) * 1000
                    
                    uuid_preview = f"{result.uuid[:8]}..." if result.uuid and len(result.uuid) > 8 else (result.uuid or "N/A")
                    logger.info(
                        f"ACTIVATION_SUCCESS [subscription_id={subscription_id}, "
                        f"user={telegram_id}, uuid={uuid_preview}, attempt={result.attempts}, "
                        f"latency_ms={activation_duration_ms:.2f}]"
                    )
                    
                    # Send notification to user
                    try:
                        user = await database.get_user(telegram_id)
                        language = user.get("language", "ru") if user else "ru"
                        
                        expires_str = expires_at.strftime("%d.%m.%Y") if expires_at else "N/A"
                        
                        # Use localized text for successful activation
                        text = localization.get_text(
                            language,
                            "payment_approved",
                            date=expires_str,
                            default=f"✅ Ваш VPN доступ активирован! Доступ до {expires_str}"
                        )
                        
                        # Use standard keyboard for VPN key
                        import handlers
                        keyboard = handlers.get_vpn_key_keyboard(language)
                        
                        await bot.send_message(
                            telegram_id,
                            text,
                            reply_markup=keyboard,
                            parse_mode="HTML"
                        )
                        
                        # Send VPN key
                        if result.vpn_key:
                            await bot.send_message(
                                telegram_id,
                                f"<code>{result.vpn_key}</code>",
                                parse_mode="HTML"
                            )
                        
                        logger.info(
                            f"ACTIVATION_NOTIFICATION_SENT [subscription_id={subscription_id}, user={telegram_id}]"
                        )
                    except Exception as e:
                        logger.error(
                            f"Failed to send activation notification to user {telegram_id}: {e}"
                        )
                        # Not critical - subscription is already activated
                    
                except VPNActivationError as e:
                    # VPN API error - increment attempt counter
                    error_msg = str(e)
                    new_attempts = current_attempts + 1
                    
                    logger.warning(
                        f"ACTIVATION_FAILED [subscription_id={subscription_id}, "
                        f"user={telegram_id}, attempt={new_attempts}/{MAX_ACTIVATION_ATTEMPTS}, "
                        f"error={error_msg}]"
                    )
                    
                    try:
                        await activation_service.mark_activation_failed(
                            subscription_id=subscription_id,
                            new_attempts=new_attempts,
                            error_msg=error_msg,
                            max_attempts=MAX_ACTIVATION_ATTEMPTS,
                            conn=conn
                        )
                        
                        # If max attempts reached, send admin notification
                        if new_attempts >= MAX_ACTIVATION_ATTEMPTS:
                            logger.error(
                                f"ACTIVATION_FAILED_FINAL [subscription_id={subscription_id}, "
                                f"user={telegram_id}, attempts={new_attempts}, error={error_msg}]"
                            )
                            
                            # Send admin notification
                            try:
                                admin_message = (
                                    f"⚠️ **ОШИБКА АКТИВАЦИИ VPN ПОДПИСКИ**\n\n"
                                    f"Подписка ID: `{subscription_id}`\n"
                                    f"Пользователь: `{telegram_id}`\n"
                                    f"Попыток: {new_attempts}/{MAX_ACTIVATION_ATTEMPTS}\n"
                                    f"Ошибка: `{error_msg}`\n\n"
                                    f"Подписка помечена как `failed`.\n"
                                    f"Требуется ручная активация."
                                )
                                
                                await bot.send_message(
                                    config.ADMIN_TELEGRAM_ID,
                                    admin_message,
                                    parse_mode="Markdown"
                                )
                                
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
                    # Other activation error
                    error_msg = str(e)
                    new_attempts = current_attempts + 1
                    
                    logger.warning(
                        f"ACTIVATION_FAILED [subscription_id={subscription_id}, "
                        f"user={telegram_id}, attempt={new_attempts}/{MAX_ACTIVATION_ATTEMPTS}, "
                        f"error={error_msg}]"
                    )
                    
                    try:
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
                
                # Small delay between processing subscriptions
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
        
        try:
            await asyncio.sleep(ACTIVATION_INTERVAL_SECONDS)
            
            # READ-ONLY system state awareness: Skip iteration if system is unavailable
            try:
                now = datetime.utcnow()
                db_ready = database.DB_READY
                
                # Build SystemState for awareness (read-only)
                if db_ready:
                    db_component = healthy_component(last_checked_at=now)
                else:
                    db_component = unavailable_component(
                        error="DB not ready (degraded mode)",
                        last_checked_at=now
                    )
                
                # VPN API component
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
                
                # STEP 6 — F5: BACKGROUND WORKER SAFETY
                # Global worker guard: respect FeatureFlags, SystemState, CircuitBreaker
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
                
                # STEP 1.1 - RUNTIME GUARDRAILS: Workers read SystemState at iteration start
                # STEP 1.2 - BACKGROUND WORKERS CONTRACT: Skip iteration if system is UNAVAILABLE
                # DEGRADED state does NOT stop iteration (workers continue with reduced functionality)
                if system_state.is_unavailable:
                    logger.warning(
                        f"[UNAVAILABLE] system_state — skipping iteration in activation_worker "
                        f"(database={system_state.database.status.value})"
                    )
                    outcome = "skipped"
                    reason = f"system_state=UNAVAILABLE (database={system_state.database.status.value})"
                    log_worker_iteration_end(
                        worker_name="activation_worker",
                        outcome=outcome,
                        items_processed=0,
                        duration_ms=(time.time() - iteration_start_time) * 1000,
                        reason=reason,
                    )
                    await asyncio.sleep(MINIMUM_SAFE_SLEEP_ON_FAILURE)
                    continue
                
                # STEP 6 — F2: CIRCUIT BREAKER LITE
                # Check circuit breaker for VPN provisioning
                from app.core.circuit_breaker import get_circuit_breaker
                vpn_breaker = get_circuit_breaker("vpn_api")
                if vpn_breaker.should_skip():
                    logger.warning(
                        f"[CIRCUIT_BREAKER] VPN API circuit breaker OPEN, skipping iteration in activation_worker "
                        f"(iteration={iteration_number})"
                    )
                    outcome = "skipped"
                    reason = "vpn_api_circuit_breaker=OPEN"
                    log_worker_iteration_end(
                        worker_name="activation_worker",
                        outcome=outcome,
                        items_processed=0,
                        duration_ms=(time.time() - iteration_start_time) * 1000,
                        reason=reason,
                    )
                    await asyncio.sleep(MINIMUM_SAFE_SLEEP_ON_FAILURE)
                    continue
                
                # PART D.4: Workers continue normally if DEGRADED
                # PART D.4: Workers skip only if system_state == UNAVAILABLE
                # DEGRADED state allows continuation (optional components degraded, critical healthy)
                if system_state.is_degraded:
                    logger.info(
                        f"[DEGRADED] system_state detected in activation_worker "
                        f"(continuing with reduced functionality - optional components degraded)"
                    )
                
                # B4.2 - COOLDOWN & BACKOFF: Check cooldown before starting operations
                recovery_cooldown = get_recovery_cooldown(cooldown_seconds=60)
                if recovery_cooldown.is_in_cooldown(ComponentName.DATABASE, now):
                    remaining = recovery_cooldown.get_cooldown_remaining(ComponentName.DATABASE, now)
                    logger.info(
                        f"[COOLDOWN] skipping activation_worker task due to recent recovery "
                        f"(database cooldown: {remaining}s remaining)"
                    )
                    continue
                
                # B4.4 - GRACEFUL BACKGROUND RECOVERY: Warm-up iteration after recovery
                global _recovery_warmup_iterations
                if system_state.database.status.value == "healthy" and _recovery_warmup_iterations < _recovery_warmup_threshold:
                    if _recovery_warmup_iterations == 0:
                        logger.info(
                            "[RECOVERY] warm-up iteration started in activation_worker "
                            "(minimal batch, no parallelism)"
                        )
                    _recovery_warmup_iterations += 1
                elif system_state.database.status.value == "healthy" and _recovery_warmup_iterations == _recovery_warmup_threshold:
                    logger.info(
                        "[RECOVERY] normal operation resumed in activation_worker "
                        f"(completed {_recovery_warmup_iterations} warm-up iterations)"
                    )
                    _recovery_warmup_iterations += 1  # Prevent repeated logging
                elif system_state.database.status.value != "healthy":
                    # Reset warmup counter if component becomes unhealthy again
                    _recovery_warmup_iterations = 0
            except Exception:
                # Ignore system state errors - continue with normal flow
                pass
            
            # C1.1 - METRICS: Increment background iterations counter
            metrics = get_metrics()
            metrics.increment_counter("background_iterations_total")
            
            # D2.1 - COST CENTERS: Track background iteration cost
            cost_model = get_cost_model()
            cost_model.record_cost(CostCenter.BACKGROUND_ITERATIONS, cost_units=1.0)
            
            # Process pending activations
            items_processed, outcome = await process_pending_activations(bot)
            
            # STEP 2.3 — OBSERVABILITY: Structured logging for worker iteration end
            # PART E — SLO SIGNAL IDENTIFICATION: Worker iteration success rate
            # This iteration end log is an SLO signal for worker iteration success rate.
            # Track: outcome="success" vs outcome="failed"/"degraded" for activation_worker iterations.
            duration_ms = (time.time() - iteration_start_time) * 1000
            error_type = None
            if outcome == "failed":
                error_type = "infra_error"  # Default, will be refined by classify_error if exception caught
            
            log_worker_iteration_end(
                worker_name="activation_worker",
                outcome=outcome,
                items_processed=items_processed,
                error_type=error_type,
                duration_ms=duration_ms
            )
            
        except (asyncpg.PostgresError, asyncio.TimeoutError) as e:
            # RESILIENCE FIX: Temporary DB failures don't crash the task loop
            logger.warning(f"activation_worker: Database temporarily unavailable in task loop: {type(e).__name__}: {str(e)[:100]}")
            
            # STEP 2.3 — OBSERVABILITY: Log iteration end with degraded outcome
            duration_ms = (time.time() - iteration_start_time) * 1000
            log_worker_iteration_end(
                worker_name="activation_worker",
                outcome="degraded",
                items_processed=0,
                error_type="infra_error",
                duration_ms=duration_ms
            )
            
            # STEP 3 — PART B: WORKER LOOP SAFETY
            # Minimum safe sleep on failure to prevent tight retry storms
            await asyncio.sleep(MINIMUM_SAFE_SLEEP_ON_FAILURE)
        except Exception as e:
            logger.error(f"activation_worker: Unexpected error in task loop: {type(e).__name__}: {str(e)[:100]}")
            logger.debug("activation_worker: Full traceback for task loop", exc_info=True)
            
            # STEP 2.3 — OBSERVABILITY: Log iteration end with failed outcome
            duration_ms = (time.time() - iteration_start_time) * 1000
            error_type = classify_error(e)
            log_worker_iteration_end(
                worker_name="activation_worker",
                outcome="failed",
                items_processed=0,
                error_type=error_type,
                duration_ms=duration_ms
            )
            
            # STEP 3 — PART B: WORKER LOOP SAFETY
            # Minimum safe sleep on failure to prevent tight retry storms
            # Worker always sleeps before next iteration, even on failure
            await asyncio.sleep(MINIMUM_SAFE_SLEEP_ON_FAILURE)
