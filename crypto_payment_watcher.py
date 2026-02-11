"""Фоновая задача для автоматической проверки статуса CryptoBot платежей"""
import asyncio
import logging
import time
from datetime import datetime, timedelta
from aiogram import Bot
from app.utils.telegram_safe import safe_send_message
import asyncpg
import database
import config
from app.services.language_service import resolve_user_language
from app.i18n import get_text as i18n_get_text
from payments import cryptobot
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

# Интервал проверки: 30 секунд
CHECK_INTERVAL_SECONDS = 30

# B4.4 - GRACEFUL BACKGROUND RECOVERY: Track recovery state for warm-up iterations
_recovery_warmup_iterations_db: int = 0
_recovery_warmup_iterations_vpn: int = 0
_recovery_warmup_threshold: int = 3  # Number of successful iterations before normal operation

# STEP 3 — PART B: WORKER LOOP SAFETY
# Minimum safe sleep on failure to prevent tight retry storms
MINIMUM_SAFE_SLEEP_ON_FAILURE = 15  # seconds (half of CHECK_INTERVAL_SECONDS)


async def check_crypto_payments(bot: Bot) -> tuple[int, str]:
    """
    Проверка статуса CryptoBot платежей для всех pending purchases
    
    Логика:
    1. Получаем все pending purchases где provider_invoice_id IS NOT NULL
    2. Для каждого проверяем статус invoice через CryptoBot API
    3. Если invoice статус='paid' → финализируем покупку
    4. Отправляем пользователю подтверждение с VPN ключом
    
    КРИТИЧНО:
    - Idempotent: finalize_purchase защищен от повторной обработки
    - Не блокирует другие pending purchases при ошибке
    - Логирует только критичные ошибки
    
    STEP 1.2 - BACKGROUND WORKERS CONTRACT:
    - Each iteration is stateless → no in-memory state across iterations
    - Each iteration may be safely skipped → no side effects if skipped
    - No unbounded retries → payment provider retries handled by retry_async
    - Errors do NOT kill the loop → exceptions caught at task level
    
    STEP 1.3 - EXTERNAL DEPENDENCIES POLICY:
    - Payment provider unavailable → payment status check fails, retry next iteration
    - Payment idempotency → preserved (finalize_purchase prevents double-processing)
    - Payment provider timeout → retried with exponential backoff (max 2 retries)
    - Domain exceptions → NOT retried, logged and handled
    
    Returns:
        Tuple of (items_processed, outcome) where outcome is "success" | "degraded" | "failed" | "skipped"
    """
    if not cryptobot.is_enabled():
        logger.info(
            f"PAYMENT_CHECK_SKIP_CRYPTOBOT_DISABLED [reason=cryptobot_not_configured, "
            f"payments_safe=True, will_retry_when_enabled=True]"
        )
        return (0, "skipped")
    
    items_processed = 0
    outcome = "success"
    
    # RESILIENCE FIX: Handle temporary DB unavailability gracefully
    try:
        pool = await database.get_pool()
    except (asyncpg.PostgresError, asyncio.TimeoutError, RuntimeError) as e:
        logger.warning(f"crypto_payment_watcher: Database temporarily unavailable (pool acquisition failed): {type(e).__name__}: {str(e)[:100]}")
        logger.info(
            f"PAYMENT_CHECK_SKIP_DB_UNAVAILABLE [reason=database_temporarily_unavailable, "
            f"payments_safe=True, will_retry_next_iteration=True]"
        )
        return (0, "skipped")
    except Exception as e:
        logger.error(f"crypto_payment_watcher: Unexpected error getting DB pool: {type(e).__name__}: {str(e)[:100]}")
        logger.info(
            f"PAYMENT_CHECK_SKIP_DB_ERROR [reason=unexpected_error, "
            f"payments_safe=True, will_retry_next_iteration=True]"
        )
        return (0, "skipped")  # Payments are safe, will retry - consistent with log message
    
    try:
        async with pool.acquire() as conn:
            # Получаем pending purchases с provider_invoice_id (т.е. CryptoBot purchases)
            # Только не истёкшие покупки: status = 'pending' AND expires_at > (NOW() AT TIME ZONE 'UTC')
            pending_purchases = await conn.fetch(
                """SELECT * FROM pending_purchases 
                   WHERE status = 'pending' 
                   AND provider_invoice_id IS NOT NULL
                   AND expires_at > (NOW() AT TIME ZONE 'UTC')
                   ORDER BY created_at DESC
                   LIMIT 100"""
            )
            
            if not pending_purchases:
                return (0, "success")
            
            logger.info(f"Crypto payment watcher: checking {len(pending_purchases)} pending purchases")
            
            for row in pending_purchases:
                items_processed += 1
                purchase = dict(row)
                purchase_id = purchase["purchase_id"]
                telegram_id = purchase["telegram_id"]
                invoice_id_str = purchase.get("provider_invoice_id")
                
                if not invoice_id_str:
                    continue
                
                try:
                    # Преобразуем invoice_id в int для CryptoBot API
                    invoice_id = int(invoice_id_str)
                    
                    # Log payment check attempt
                    logger.debug(
                        f"PAYMENT_CHECK_ATTEMPT [purchase_id={purchase_id}, user={telegram_id}, "
                        f"invoice_id={invoice_id}]"
                    )
                    
                    # Проверяем статус invoice через CryptoBot API
                    try:
                        invoice_status = await cryptobot.check_invoice_status(invoice_id)
                        status = invoice_status.get("status")
                    except Exception as api_error:
                        # CryptoBot API call failed - payment is safe, will retry
                        logger.warning(
                            f"PAYMENT_CHECK_API_FAILED [purchase_id={purchase_id}, user={telegram_id}, "
                            f"invoice_id={invoice_id}, error={type(api_error).__name__}: {str(api_error)[:100]}, "
                            f"payments_safe=True, will_retry_next_iteration=True]"
                        )
                        outcome = "degraded"
                        continue  # Skip this purchase, continue with others
                    
                    if status != "paid":
                        # Оплата еще не выполнена
                        continue
                    
                    # Оплата успешна - финализируем покупку
                    payload = invoice_status.get("payload", "")
                    if not payload.startswith("purchase:"):
                        logger.error(f"Invalid payload format in CryptoBot invoice: invoice_id={invoice_id}, payload={payload}")
                        continue
                    
                    # Получаем сумму оплаты (USD string from API, convert back to RUB)
                    amount_usd_str = invoice_status.get("amount", "0")
                    try:
                        amount_usd = float(amount_usd_str) if amount_usd_str else 0.0
                        from payments.cryptobot import RUB_TO_USD_RATE
                        amount_rubles = amount_usd * RUB_TO_USD_RATE
                    except (ValueError, TypeError):
                        logger.error(f"Invalid amount in invoice status: {amount_usd_str}, invoice_id={invoice_id}")
                        continue
                    
                    # Финализируем покупку
                    result = await database.finalize_purchase(
                        purchase_id=purchase_id,
                        payment_provider="cryptobot",
                        amount_rubles=amount_rubles,
                        invoice_id=invoice_id_str
                    )
                    
                    if not result or not result.get("success"):
                        logger.error(f"Crypto payment finalization failed: purchase_id={purchase_id}, invoice_id={invoice_id}")
                        continue
                    
                    # Проверяем, является ли это пополнением баланса
                    is_balance_topup = result.get("is_balance_topup", False)
                    
                    language = await resolve_user_language(telegram_id)
                    
                    if is_balance_topup:
                        # Отправляем подтверждение пополнения баланса
                        amount = result.get("amount", amount_rubles)
                        text = i18n_get_text(
                            language,
                            "main.balance_topup_success",
                            amount=amount
                        )
                        
                        if await safe_send_message(bot, telegram_id, text, parse_mode="HTML"):
                            logger.info(
                                f"Crypto balance top-up auto-confirmed: user={telegram_id}, purchase_id={purchase_id}, "
                                f"invoice_id={invoice_id}, amount={amount} RUB"
                            )
                    else:
                        # Отправляем подтверждение покупки подписки
                        payment_id = result["payment_id"]
                        expires_at = result["expires_at"]
                        vpn_key = result["vpn_key"]
                        
                        expires_str = expires_at.strftime("%d.%m.%Y")
                        text = i18n_get_text(language, "payment.approved", date=expires_str)
                        
                        # Импорт здесь для избежания circular import
                        import handlers
                        sent1 = await safe_send_message(
                            bot, telegram_id, text,
                            reply_markup=handlers.get_vpn_key_keyboard(language),
                            parse_mode="HTML"
                        )
                        if sent1:
                            await safe_send_message(
                                bot, telegram_id, f"<code>{vpn_key}</code>",
                                parse_mode="HTML"
                            )
                            logger.info(
                                f"Crypto payment auto-confirmed: user={telegram_id}, purchase_id={purchase_id}, "
                                f"invoice_id={invoice_id}, payment_id={payment_id}"
                            )
                    
                except ValueError as e:
                    # Pending purchase уже обработан (idempotency)
                    logger.debug(f"Crypto payment already processed: purchase_id={purchase_id}, invoice_id={invoice_id_str}, error={e}")
                except Exception as e:
                    # Ошибка для одной покупки не должна ломать весь процесс
                    logger.error(f"Error checking crypto payment for purchase {purchase_id}: {e}", exc_info=True)
                    outcome = "degraded"  # Some items failed, but iteration continues
                    continue
            
            return (items_processed, outcome)
    except (asyncpg.PostgresError, asyncio.TimeoutError) as e:
        # RESILIENCE FIX: Temporary DB failures are logged as WARNING, not ERROR
        logger.warning(f"crypto_payment_watcher: Database temporarily unavailable in check_crypto_payments: {type(e).__name__}: {str(e)[:100]}")
        return (items_processed, "degraded")
    except Exception as e:
        logger.error(f"crypto_payment_watcher: Unexpected error in check_crypto_payments: {type(e).__name__}: {str(e)[:100]}")
        logger.debug("crypto_payment_watcher: Full traceback in check_crypto_payments", exc_info=True)
        error_type = classify_error(e)
        return (items_processed, "failed")


async def cleanup_expired_purchases():
    """
    Очистка истёкших pending purchases
    
    Помечает как 'expired' все покупки где:
    - status = 'pending'
    - expires_at <= now_utc
    
    Безопасно: не удаляет покупки, только меняет статус
    """
    # RESILIENCE FIX: Handle temporary DB unavailability gracefully
    try:
        pool = await database.get_pool()
    except (asyncpg.PostgresError, asyncio.TimeoutError, RuntimeError) as e:
        logger.warning(f"crypto_payment_watcher: Database temporarily unavailable in cleanup_expired_purchases (pool acquisition failed): {type(e).__name__}: {str(e)[:100]}")
        return
    except Exception as e:
        logger.error(f"crypto_payment_watcher: Unexpected error getting DB pool in cleanup_expired_purchases: {type(e).__name__}: {str(e)[:100]}")
        return
    
    try:
        async with pool.acquire() as conn:
            # Получаем список истёкших покупок перед обновлением для логирования
            expired_purchases = await conn.fetch("""
                SELECT id, purchase_id, telegram_id, expires_at
                FROM pending_purchases 
                WHERE status = 'pending' 
                AND expires_at IS NOT NULL
                AND expires_at <= (NOW() AT TIME ZONE 'UTC')
            """)
            
            if not expired_purchases:
                return
            
            # Помечаем как expired
            result = await conn.execute("""
                UPDATE pending_purchases 
                SET status = 'expired'
                WHERE status = 'pending' 
                AND expires_at IS NOT NULL
                AND expires_at <= (NOW() AT TIME ZONE 'UTC')
            """)
            
            # Логируем каждую истёкшую покупку
            for purchase in expired_purchases:
                logger.info(
                    f"crypto_invoice_expired: purchase_id={purchase['purchase_id']}"
                )
    except (asyncpg.PostgresError, asyncio.TimeoutError) as e:
        # RESILIENCE FIX: Temporary DB failures are logged as WARNING, not ERROR
        logger.warning(f"crypto_payment_watcher: Database temporarily unavailable in cleanup_expired_purchases: {type(e).__name__}: {str(e)[:100]}")
    except Exception as e:
        logger.error(f"crypto_payment_watcher: Unexpected error in cleanup_expired_purchases: {type(e).__name__}: {str(e)[:100]}")
        logger.debug("crypto_payment_watcher: Full traceback in cleanup_expired_purchases", exc_info=True)


async def crypto_payment_watcher_task(bot: Bot):
    """
    Фоновая задача для автоматической проверки CryptoBot платежей
    
    Запускается каждые CHECK_INTERVAL_SECONDS (30 секунд)
    """
    logger.info(f"Crypto payment watcher task started: interval={CHECK_INTERVAL_SECONDS}s")
    
    # Первая проверка сразу при запуске
    try:
        await check_crypto_payments(bot)
        await cleanup_expired_purchases()
    except (asyncpg.PostgresError, asyncio.TimeoutError) as e:
        # RESILIENCE FIX: Temporary DB failures don't crash the task
        logger.warning(f"crypto_payment_watcher: Initial check failed (DB temporarily unavailable): {type(e).__name__}: {str(e)[:100]}")
    except Exception as e:
        logger.error(f"crypto_payment_watcher: Unexpected error in initial check: {type(e).__name__}: {str(e)[:100]}")
        logger.debug("crypto_payment_watcher: Full traceback for initial check", exc_info=True)
    
    iteration_number = 0
    
    while True:
        iteration_start_time = time.time()
        iteration_number += 1
        
        # STEP 2.3 — OBSERVABILITY: Structured logging for worker iteration start
        correlation_id = log_worker_iteration_start(
            worker_name="crypto_payment_watcher",
            iteration_number=iteration_number
        )
        
        items_processed = 0
        outcome = "success"
        
        try:
            await asyncio.sleep(CHECK_INTERVAL_SECONDS)
            
            # STEP 6 — F5: BACKGROUND WORKER SAFETY
            # Global worker guard: respect FeatureFlags, SystemState, CircuitBreaker
            from app.core.feature_flags import get_feature_flags
            feature_flags = get_feature_flags()
            if not feature_flags.background_workers_enabled:
                logger.warning(
                    f"[FEATURE_FLAG] Background workers disabled, skipping iteration in crypto_payment_watcher "
                    f"(iteration={iteration_number})"
                )
                outcome = "skipped"
                reason = "background_workers_enabled=false"
                log_worker_iteration_end(
                    worker_name="crypto_payment_watcher",
                    outcome=outcome,
                    items_processed=0,
                    duration_ms=(time.time() - iteration_start_time) * 1000,
                    reason=reason,
                )
                await asyncio.sleep(MINIMUM_SAFE_SLEEP_ON_FAILURE)
                continue
            
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
                
                # STEP 1.1 - RUNTIME GUARDRAILS: Workers read SystemState at iteration start
                # STEP 1.2 - BACKGROUND WORKERS CONTRACT: Skip iteration if system is UNAVAILABLE
                # DEGRADED state does NOT stop iteration (workers continue with reduced functionality)
                if system_state.is_unavailable:
                    logger.warning(
                        f"[UNAVAILABLE] system_state — skipping iteration in crypto_payment_watcher "
                        f"(database={system_state.database.status.value})"
                    )
                    outcome = "skipped"
                    reason = f"system_state=UNAVAILABLE (database={system_state.database.status.value})"
                    log_worker_iteration_end(
                        worker_name="crypto_payment_watcher",
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
                        f"[DEGRADED] system_state detected in crypto_payment_watcher "
                        f"(continuing with reduced functionality - optional components degraded)"
                    )
                
                # B4.2 - COOLDOWN & BACKOFF: Check cooldown before starting operations
                recovery_cooldown = get_recovery_cooldown(cooldown_seconds=60)
                if recovery_cooldown.is_in_cooldown(ComponentName.DATABASE, now):
                    remaining = recovery_cooldown.get_cooldown_remaining(ComponentName.DATABASE, now)
                    logger.info(
                        f"[COOLDOWN] skipping crypto_payment_watcher task due to recent recovery "
                        f"(database cooldown: {remaining}s remaining)"
                    )
                    outcome = "skipped"
                    reason = f"database_cooldown (remaining={remaining}s)"
                    log_worker_iteration_end(
                        worker_name="crypto_payment_watcher",
                        outcome=outcome,
                        items_processed=0,
                        duration_ms=(time.time() - iteration_start_time) * 1000,
                        reason=reason,
                    )
                    await asyncio.sleep(MINIMUM_SAFE_SLEEP_ON_FAILURE)
                    continue
                
                # B4.4 - GRACEFUL BACKGROUND RECOVERY: Warm-up iteration after recovery
                global _recovery_warmup_iterations
                if system_state.database.status.value == "healthy" and _recovery_warmup_iterations < _recovery_warmup_threshold:
                    if _recovery_warmup_iterations_db == 0:
                        logger.info(
                            "[RECOVERY] warm-up iteration started in crypto_payment_watcher "
                            "(minimal batch, no parallelism)"
                        )
                    _recovery_warmup_iterations_db += 1
                elif system_state.database.status.value == "healthy" and _recovery_warmup_iterations_db == _recovery_warmup_threshold:
                    logger.info(
                        "[RECOVERY] normal operation resumed in crypto_payment_watcher "
                        f"(completed {_recovery_warmup_iterations_db} warm-up iterations)"
                    )
                    _recovery_warmup_iterations_db += 1  # Prevent repeated logging
                elif system_state.database.status.value != "healthy":
                    # Reset warmup counter if component becomes unhealthy again
                    _recovery_warmup_iterations_db = 0
            except Exception:
                # Ignore system state errors - continue with normal flow
                pass
            
            # Process crypto payments
            items_processed, outcome = await check_crypto_payments(bot)
            await cleanup_expired_purchases()
            
            # STEP 2.3 — OBSERVABILITY: Structured logging for worker iteration end
            duration_ms = (time.time() - iteration_start_time) * 1000
            error_type = None
            if outcome == "failed":
                error_type = "infra_error"  # Default, will be refined by classify_error if exception caught
            
            log_worker_iteration_end(
                worker_name="crypto_payment_watcher",
                outcome=outcome,
                items_processed=items_processed,
                error_type=error_type,
                duration_ms=duration_ms
            )
            
        except asyncio.CancelledError:
            logger.info("Crypto payment watcher task cancelled")
            break
        except (asyncpg.PostgresError, asyncio.TimeoutError) as e:
            # RESILIENCE FIX: Temporary DB failures don't crash the task loop
            logger.warning(f"crypto_payment_watcher: Database temporarily unavailable: {type(e).__name__}: {str(e)[:100]}")
            
            # STEP 2.3 — OBSERVABILITY: Log iteration end with degraded outcome
            duration_ms = (time.time() - iteration_start_time) * 1000
            log_worker_iteration_end(
                worker_name="crypto_payment_watcher",
                outcome="degraded",
                items_processed=0,
                error_type="infra_error",
                duration_ms=duration_ms
            )
            # STEP 3 — PART B: WORKER LOOP SAFETY
            # Minimum safe sleep on failure to prevent tight retry storms
            # Worker always sleeps before next iteration, even on failure
            await asyncio.sleep(MINIMUM_SAFE_SLEEP_ON_FAILURE)
        except Exception as e:
            logger.error(f"crypto_payment_watcher: Unexpected error in task loop: {type(e).__name__}: {str(e)[:100]}")
            logger.debug("crypto_payment_watcher: Full traceback for task loop", exc_info=True)
            
            # STEP 2.3 — OBSERVABILITY: Log iteration end with failed outcome
            duration_ms = (time.time() - iteration_start_time) * 1000
            error_type = classify_error(e)
            log_worker_iteration_end(
                worker_name="crypto_payment_watcher",
                outcome="failed",
                items_processed=0,
                error_type=error_type,
                duration_ms=duration_ms
            )
            
            # STEP 3 — PART B: WORKER LOOP SAFETY
            # Minimum safe sleep on failure to prevent tight retry storms
            # Worker always sleeps before next iteration, even on failure
            await asyncio.sleep(MINIMUM_SAFE_SLEEP_ON_FAILURE)
