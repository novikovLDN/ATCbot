"""
Fast Expiry Cleanup - автоматическое отключение истёкших VPN подписок

Фоновая задача для немедленного отзыва VPN-доступа после истечения подписки.
Работает асинхронно, не блокирует основной event loop бота.

Требования:
- Запускается каждые 1-5 минут (настраивается через переменную окружения)
- Использует UTC время для сравнения дат
- Идемпотентна (безопасно запускать несколько раз)
- Устойчива к сетевым ошибкам (повтор в следующем цикле)
"""
import asyncio
import logging
import os
import time
from datetime import datetime, timezone
import asyncpg
import database
import config
from app.services.vpn import service as vpn_service
from app.core.system_state import (
    SystemState,
    healthy_component,
    degraded_component,
    unavailable_component,
)
from app.core.metrics import get_metrics
from app.core.cost_model import get_cost_model, CostCenter
from app.utils.logging_helpers import (
    log_worker_iteration_start,
    log_worker_iteration_end,
    classify_error,
)
from app.core.cooperative_yield import cooperative_yield
from app.core.pool_monitor import acquire_connection

logger = logging.getLogger(__name__)

# Event loop protection: max iteration time (prevents 300s blocking)
MAX_ITERATION_SECONDS = int(os.getenv("FAST_EXPIRY_MAX_ITERATION_SECONDS", "15"))
BATCH_SIZE = 100
_worker_lock = asyncio.Lock()

# Интервал проверки: 1-5 минут (настраивается через переменную окружения)
# По умолчанию: 60 секунд (1 минута)
CLEANUP_INTERVAL_SECONDS = int(os.getenv("CLEANUP_INTERVAL_SECONDS", "60"))
# Ограничиваем интервал от 60 секунд (1 минута) до 300 секунд (5 минут)
CLEANUP_INTERVAL_SECONDS = max(60, min(300, CLEANUP_INTERVAL_SECONDS))

# STEP 3 — PART B: WORKER LOOP SAFETY
# Minimum safe sleep on failure to prevent tight retry storms
MINIMUM_SAFE_SLEEP_ON_FAILURE = 10  # seconds


async def fast_expiry_cleanup_task():
    """
    Fast Expiry Cleanup Task
    
    Автоматическая фоновая задача для отключения истёкших VPN подписок.
    Работает асинхронно, не блокирует основной event loop бота.
    
    Логика:
    1. Находит все подписки где:
       - status = 'active'
       - expires_at (subscription_end) < текущее UTC время
       - uuid IS NOT NULL
    2. Для каждой подписки:
       - Вызывает POST {XRAY_API_URL}/remove-user/{uuid} с заголовком X-API-Key
       - Если API вызов успешен - обновляет статус на 'expired' и очищает uuid/vpn_key
    3. Защита от повторного удаления: проверка что UUID всё ещё существует перед обновлением БД
    4. При ошибке сети - НЕ очищает БД, повторит в следующем цикле
    
    Идемпотентность:
    - remove-user идемпотентен (отсутствие UUID на сервере не считается ошибкой)
    - Повторное удаление одного UUID безопасно
    - Защита от race condition через processing_uuids множество
    
    Не блокирует event loop:
    - Использует async/await для всех операций
    - Сетевые запросы выполняются асинхронно
    - База данных операции выполняются через asyncpg
    """
    logger.info(
        f"Fast expiry cleanup task started (interval: {CLEANUP_INTERVAL_SECONDS} seconds, "
        f"range: 60-300 seconds, using UTC time)"
    )
    
    # Множество для отслеживания UUID, которые мы уже обрабатываем (защита от race condition)
    processing_uuids = set()
    
    iteration_number = 0
    
    while True:
        iteration_start_time = time.time()
        iteration_number += 1
        
        # STEP 2.3 — OBSERVABILITY: Structured logging for worker iteration start
        correlation_id = log_worker_iteration_start(
            worker_name="fast_expiry_cleanup",
            iteration_number=iteration_number
        )
        
        items_processed = 0
        outcome = "success"
        iteration_error_type = None
        
        try:
            await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
            
            # STEP 6 — F5: BACKGROUND WORKER SAFETY
            # Global worker guard: respect FeatureFlags, SystemState, CircuitBreaker
            from app.core.feature_flags import get_feature_flags
            feature_flags = get_feature_flags()
            if not feature_flags.background_workers_enabled:
                logger.warning(
                    f"[FEATURE_FLAG] Background workers disabled, skipping iteration in fast_expiry_cleanup "
                    f"(iteration={iteration_number})"
                )
                outcome = "skipped"
                reason = "background_workers_enabled=false"
                log_worker_iteration_end(
                    worker_name="fast_expiry_cleanup",
                    outcome=outcome,
                    items_processed=0,
                    duration_ms=(time.time() - iteration_start_time) * 1000,
                    reason=reason,
                )
                await asyncio.sleep(MINIMUM_SAFE_SLEEP_ON_FAILURE)
                continue
            
            # READ-ONLY system state awareness: Skip iteration if system is unavailable
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
                
                # STEP 1.1 - RUNTIME GUARDRAILS: Workers read SystemState at iteration start
                # STEP 1.2 - BACKGROUND WORKERS CONTRACT: Skip iteration if system is UNAVAILABLE
                # DEGRADED state does NOT stop iteration (workers continue with reduced functionality)
                if system_state.is_unavailable:
                    logger.warning(
                        f"[UNAVAILABLE] system_state — skipping iteration in fast_expiry_cleanup "
                        f"(database={system_state.database.status.value})"
                    )
                    continue
                
                # PART D.4: Workers continue normally if DEGRADED
                if system_state.is_degraded:
                    logger.info(
                        f"[DEGRADED] system_state detected in fast_expiry_cleanup "
                        f"(continuing with reduced functionality - optional components degraded)"
                    )
            except Exception:
                # Ignore system state errors - continue with normal flow
                pass
            
            # C1.1 - METRICS: Increment background iterations counter
            metrics = get_metrics()
            metrics.increment_counter("background_iterations_total")
            
            # D2.1 - COST CENTERS: Track background iteration cost
            cost_model = get_cost_model()
            cost_model.record_cost(CostCenter.BACKGROUND_ITERATIONS, cost_units=1.0)
            
            # Получаем текущее UTC время для сравнения
            # PostgreSQL TIMESTAMP хранит без timezone, поэтому используем naive datetime
            now_utc = datetime.now(timezone.utc)
            
            # H1 fix: Wrap iteration body with timeout
            async def _run_iteration_body():
                # Event loop protection: prevent overlapping iterations
                async with _worker_lock:
                    # Получаем истёкшие подписки с активными UUID
                    # Используем expires_at (в БД) - это и есть subscription_end
                    # STEP 1.2 - BACKGROUND WORKERS CONTRACT: Each iteration is stateless, may be safely skipped
                    # STEP 1.3 - EXTERNAL DEPENDENCIES POLICY: DB unavailable → iteration skipped, no error raised
                    try:
                        pool = await database.get_pool()
                    except (asyncpg.PostgresError, asyncio.TimeoutError, RuntimeError) as e:
                        logger.warning(f"fast_expiry_cleanup: Database temporarily unavailable (pool acquisition failed): {type(e).__name__}: {str(e)[:100]}")
                        return
                    except Exception as e:
                        logger.error(f"fast_expiry_cleanup: Unexpected error getting DB pool: {type(e).__name__}: {str(e)[:100]}")
                        return

                    try:
                    last_seen_id = 0
                    while True:
                        # POOL_STABILITY: Fetch batch with short-lived conn; release immediately (no HTTP inside).
                        async with acquire_connection(pool, "fast_expiry_fetch") as conn:
                            rows = await conn.fetch(
                                """SELECT id, telegram_id, uuid, vpn_key, expires_at, status, source 
                                   FROM subscriptions 
                                   WHERE status = 'active'
                                   AND expires_at < $1
                                   AND uuid IS NOT NULL
                                   AND id > $2
                                   ORDER BY id ASC
                                   LIMIT $3""",
                                database._to_db_utc(now_utc), last_seen_id, BATCH_SIZE
                            )
                        if not rows:
                            break

                        logger.info(f"cleanup: FOUND_EXPIRED [count={len(rows)}]")
                        loop_start = time.monotonic()
                        for i, row in enumerate(rows):
                            if i > 0 and i % 20 == 0:
                                await cooperative_yield()
                            if time.monotonic() - loop_start > MAX_ITERATION_SECONDS:
                                logger.warning("Fast expiry cleanup iteration time limit reached, breaking early")
                                break
                            items_processed += 1
                            telegram_id = row["telegram_id"]
                            uuid = row["uuid"]
                            expires_at = row["expires_at"]
                            source = row.get("source", "unknown")

                            # ЗАЩИТА: Проверяем что подписка действительно истекла (используем UTC)
                            expires_at_aware = database._from_db_utc(expires_at) if expires_at else None
                            if expires_at_aware is not None and expires_at_aware >= now_utc:
                                logger.warning(
                                    f"cleanup: SKIP_NOT_EXPIRED [user={telegram_id}, expires_at={expires_at.isoformat()}, "
                                    f"now={now_utc.isoformat()}]"
                                )
                                continue

                            # Canonical guard: paid subscription ALWAYS overrides trial (short-lived conn only).
                            async with acquire_connection(pool, "fast_expiry_paid_check") as conn_check:
                                active_paid = await database.get_active_paid_subscription(conn_check, telegram_id, now_utc)
                            if active_paid:
                                paid_expires_at = active_paid["expires_at"]
                                logger.info(
                                    f"SKIP_TRIAL_EXPIRY_PAID_USER: user_id={telegram_id}, "
                                    f"trial_expires_at={expires_at.isoformat() if expires_at else None}, "
                                    f"paid_expires_at={paid_expires_at.isoformat() if paid_expires_at else None}, "
                                    f"expired_subscription_source={source} - "
                                    "User has active paid subscription, skipping expired subscription cleanup"
                                )
                                continue

                            if uuid in processing_uuids:
                                uuid_preview = f"{uuid[:8]}..." if uuid and len(uuid) > 8 else (uuid or "N/A")
                                logger.debug(
                                    f"cleanup: SKIP_ALREADY_PROCESSING [user={telegram_id}, uuid={uuid_preview}] - "
                                    "UUID already being processed"
                                )
                                continue

                            processing_uuids.add(uuid)
                            uuid_preview = f"{uuid[:8]}..." if uuid and len(uuid) > 8 else (uuid or "N/A")

                            try:
                                logger.info(
                                    f"cleanup: REMOVING_UUID [user={telegram_id}, uuid={uuid_preview}, "
                                    f"expires_at={expires_at.isoformat()}]"
                                )
                                # POOL_STABILITY: VPN HTTP call OUTSIDE any DB connection.
                                uuid_removed = await vpn_service.remove_uuid_if_needed(
                                    uuid=uuid,
                                    subscription_status='active',
                                    subscription_expired=True
                                )
                                if uuid_removed:
                                    logger.info(f"cleanup: VPN_API_REMOVED [user={telegram_id}, uuid={uuid_preview}]")
                                else:
                                    vpn_api_disabled = not vpn_service.is_vpn_api_available()
                                    if vpn_api_disabled:
                                        logger.warning(
                                            f"cleanup: VPN_API_DISABLED [user={telegram_id}, uuid={uuid_preview}] - "
                                            "VPN API is not configured, UUID removal skipped but DB will be cleaned"
                                        )
                                    else:
                                        logger.debug(
                                            f"cleanup: UUID_REMOVAL_SKIPPED [user={telegram_id}, uuid={uuid_preview}] - "
                                            "Service layer decided not to remove UUID"
                                        )
                                        processing_uuids.discard(uuid)
                                        continue

                                try:
                                    await database._log_vpn_lifecycle_audit_async(
                                        action="vpn_expire",
                                        telegram_id=telegram_id,
                                        uuid=uuid,
                                        source="auto-expiry",
                                        result="success",
                                        details=f"Auto-expired subscription, expires_at={expires_at.isoformat()}"
                                    )
                                except Exception as e:
                                    logger.warning(f"Failed to log VPN expire audit (non-blocking): {e}")

                                # POOL_STABILITY: DB update with dedicated short-lived conn (no conn held during HTTP).
                                try:
                                    async with acquire_connection(pool, "fast_expiry_update") as conn:
                                        async with conn.transaction():
                                            check_row = await conn.fetchrow(
                                                """SELECT uuid, expires_at, status 
                                                   FROM subscriptions 
                                                   WHERE telegram_id = $1 
                                                   AND uuid = $2 
                                                   AND status = 'active'""",
                                                telegram_id, uuid
                                            )
                                            if check_row:
                                                check_expires_at = database._from_db_utc(check_row["expires_at"]) if check_row["expires_at"] else None
                                                if check_expires_at is not None and check_expires_at >= now_utc:
                                                    logger.warning(
                                                        f"cleanup: SKIP_RENEWED [user={telegram_id}, uuid={uuid_preview}, "
                                                        f"expires_at={check_expires_at.isoformat()}] - subscription was renewed"
                                                    )
                                                else:
                                                    update_result = await conn.execute(
                                                        """UPDATE subscriptions 
                                                           SET status = 'expired', uuid = NULL, vpn_key = NULL 
                                                           WHERE telegram_id = $1 
                                                           AND uuid = $2 
                                                           AND status = 'active'""",
                                                        telegram_id, uuid
                                                    )
                                                    if update_result == "UPDATE 1":
                                                        logger.info(
                                                            f"cleanup: SUBSCRIPTION_EXPIRED [user={telegram_id}, uuid={uuid_preview}, "
                                                            f"expires_at={expires_at.isoformat()}]"
                                                        )
                                                        import config
                                                        await database._log_audit_event_atomic(
                                                            conn,
                                                            "uuid_fast_deleted",
                                                            config.ADMIN_TELEGRAM_ID,
                                                            telegram_id,
                                                            f"Fast-deleted expired UUID {uuid_preview}, expired_at={expires_at.isoformat()}"
                                                        )
                                                        try:
                                                            await database._log_vpn_lifecycle_audit_async(
                                                                action="vpn_expire",
                                                                telegram_id=telegram_id,
                                                                uuid=uuid,
                                                                source="auto-expiry",
                                                                result="success",
                                                                details=f"Subscription expired and UUID removed, expires_at={expires_at.isoformat()}"
                                                            )
                                                        except Exception as e:
                                                            logger.warning(f"Failed to log VPN expire audit (non-blocking): {e}")
                                                        logger.info(
                                                            f"cleanup: SUCCESS [user={telegram_id}, uuid={uuid_preview}, "
                                                            f"expires_at={expires_at.isoformat()}]"
                                                        )
                                                    else:
                                                        logger.warning(
                                                            f"cleanup: UPDATE_FAILED [user={telegram_id}, uuid={uuid_preview}, "
                                                            f"update_result={update_result}] - UUID may have been updated by another process"
                                                        )
                                            else:
                                                logger.debug(
                                                    f"cleanup: UUID_ALREADY_CLEANED [user={telegram_id}, uuid={uuid_preview}] - "
                                                    "UUID was already removed or subscription is no longer active"
                                                )
                                except (asyncpg.PostgresError, asyncio.TimeoutError) as e:
                                    logger.warning(f"fast_expiry_cleanup: Database temporarily unavailable during DB update: {type(e).__name__}: {str(e)[:100]}")
                                except Exception as e:
                                    logger.error(f"fast_expiry_cleanup: Unexpected error during DB update: {type(e).__name__}: {str(e)[:100]}")
                                    logger.debug(f"fast_expiry_cleanup: Full traceback for DB update", exc_info=True)

                            except vpn_service.VPNRemovalError as e:
                                logger.error(
                                    f"cleanup: VPN_REMOVAL_ERROR [user={telegram_id}, uuid={uuid_preview}, error={str(e)}, "
                                    f"error_type={type(e).__name__}] - will retry in next cycle"
                                )
                                try:
                                    await database._log_vpn_lifecycle_audit_async(
                                        action="vpn_expire",
                                        telegram_id=telegram_id,
                                        uuid=uuid,
                                        source="auto-expiry",
                                        result="error",
                                        details=f"Failed to remove UUID via VPN API: {str(e)}, will retry"
                                    )
                                except Exception:
                                    pass

                            except ValueError as e:
                                logger.error(
                                    f"cleanup: VALUE_ERROR [user={telegram_id}, uuid={uuid_preview}, error={str(e)}]"
                                )

                            except Exception as e:
                                logger.error(
                                    f"cleanup: UNEXPECTED_ERROR [user={telegram_id}, uuid={uuid_preview}, "
                                    f"error={str(e)}, error_type={type(e).__name__}] - will retry in next cycle"
                                )
                                logger.exception(f"cleanup: EXCEPTION_TRACEBACK [user={telegram_id}, uuid={uuid_preview}]")

                            finally:
                                processing_uuids.discard(uuid)

                        if rows:
                            last_seen_id = rows[-1]["id"]
                        await asyncio.sleep(0)

                    # STEP 2.3 — OBSERVABILITY: Log once per worker cycle (after all batches)
                    # Note: outcome and items_processed set inside _run_iteration_body
                except (asyncpg.PostgresError, asyncio.TimeoutError) as e:
                    # RESILIENCE FIX: Temporary DB failures are logged as WARNING, not ERROR
                    logger.warning(f"fast_expiry_cleanup: Database temporarily unavailable in main loop: {type(e).__name__}: {str(e)[:100]}")
                    outcome = "degraded"
                except Exception as e:
                    logger.error(f"fast_expiry_cleanup: Unexpected error in main loop: {type(e).__name__}: {str(e)[:100]}")
                    logger.debug("fast_expiry_cleanup: Full traceback in main loop", exc_info=True)
                    outcome = "failed"
            
            # H1 fix: Execute iteration body with timeout wrapper
            try:
                await asyncio.wait_for(_run_iteration_body(), timeout=120.0)
            except asyncio.TimeoutError:
                logger.error(
                    "WORKER_TIMEOUT worker=fast_expiry_cleanup exceeded 120s — iteration cancelled"
                )
                outcome = "timeout"
                iteration_error_type = "timeout"
            except (asyncpg.PostgresError, asyncio.TimeoutError) as e:
                # RESILIENCE FIX: Temporary DB failures don't crash the task
                logger.warning(f"fast_expiry_cleanup: Database temporarily unavailable in task loop: {type(e).__name__}: {str(e)[:100]}")
                outcome = "degraded"
                iteration_error_type = "infra_error"
            except Exception as e:
                logger.error(f"fast_expiry_cleanup: Unexpected error in task loop: {type(e).__name__}: {str(e)[:100]}")
                logger.debug("fast_expiry_cleanup: Full traceback for task loop", exc_info=True)
                outcome = "failed"
                iteration_error_type = classify_error(e)
            finally:
                # H2 fix: ITERATION_END always fires in finally block
                duration_ms = (time.time() - iteration_start_time) * 1000
                log_worker_iteration_end(
                    worker_name="fast_expiry_cleanup",
                    outcome=outcome,
                    items_processed=items_processed,
                    error_type=iteration_error_type if 'iteration_error_type' in locals() else None,
                    duration_ms=duration_ms
                )
                if outcome not in ("success", "cancelled", "skipped"):
                    await asyncio.sleep(MINIMUM_SAFE_SLEEP_ON_FAILURE)
            
        except asyncio.CancelledError:
            logger.info("Fast expiry cleanup task cancelled")
            raise



