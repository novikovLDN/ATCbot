"""Фоновая задача для автоматической проверки статуса CryptoBot платежей"""
import asyncio
import logging
import random
import time
from datetime import datetime, timedelta, timezone
from aiogram import Bot
from app.utils.telegram_safe import safe_send_message
import asyncpg
import database
import config
from app.services.language_service import resolve_user_language
from app.i18n import get_text as i18n_get_text
from payments import cryptobot
from app.utils.logging_helpers import (
    log_worker_iteration_start,
    log_worker_iteration_end,
    classify_error,
)
from app.core.structured_logger import log_event
from app.core.cooperative_yield import cooperative_yield

logger = logging.getLogger(__name__)

# Event loop protection: max iteration time (prevents 300s blocking)
MAX_ITERATION_SECONDS = 15
_worker_lock = asyncio.Lock()

# Интервал проверки: 30 секунд
CHECK_INTERVAL_SECONDS = 30

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
            
            iteration_start = time.monotonic()
            for i, row in enumerate(pending_purchases):
                if i > 0 and i % 50 == 0:
                    await cooperative_yield()
                if time.monotonic() - iteration_start > MAX_ITERATION_SECONDS:
                    logger.warning("Crypto payment watcher iteration time limit reached, breaking early")
                    break
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
                    
                    # Используем price_kopecks из pending_purchases — авторитетная сумма в рублях.
                    # API CryptoBot возвращает в "amount" сумму в крипто-ассете (USDT/TON/BTC),
                    # а не в фиате, поэтому пересчёт через курс даёт неверный результат для TON/BTC.
                    amount_rubles = purchase.get("price_kopecks", 0) / 100.0
                    
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
    
    # Prevent worker burst at startup
    jitter_s = random.uniform(5, 60)
    await asyncio.sleep(jitter_s)
    logger.debug("crypto_payment_watcher: startup jitter done (%.1fs)", jitter_s)
    
    # Первая проверка после jitter
    try:
        async with _worker_lock:
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
        iteration_error_type = None
        should_exit_loop = False
        
        try:
            # Feature flag check
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
            
            # Simple DB readiness check
            if not database.DB_READY:
                logger.warning("crypto_payment_watcher: skipping — DB not ready")
                outcome = "skipped"
                reason = "DB not ready"
                log_worker_iteration_end(
                    worker_name="crypto_payment_watcher",
                    outcome=outcome,
                    items_processed=0,
                    duration_ms=(time.time() - iteration_start_time) * 1000,
                    reason=reason,
                )
                await asyncio.sleep(MINIMUM_SAFE_SLEEP_ON_FAILURE)
                continue
            
            # H1 fix: Wrap iteration body with timeout
            async def _run_iteration():
                # Process crypto payments
                async with _worker_lock:
                    items, result = await check_crypto_payments(bot)
                await cleanup_expired_purchases()
                return items, result
            
            try:
                items_processed, outcome = await asyncio.wait_for(_run_iteration(), timeout=120.0)
            except asyncio.TimeoutError:
                logger.error(
                    "WORKER_TIMEOUT worker=crypto_payment_watcher exceeded 120s — iteration cancelled"
                )
                items_processed = 0
                outcome = "timeout"
                iteration_error_type = "timeout"
            except Exception as e:
                logger.exception(f"crypto_payment_watcher: Unexpected error in iteration: {type(e).__name__}: {str(e)[:100]}")
                items_processed = 0
                outcome = "failed"
                iteration_error_type = classify_error(e)
            
        except asyncio.CancelledError:
            logger.info("Crypto payment watcher task cancelled")
            outcome = "cancelled"
            should_exit_loop = True
        except (asyncpg.PostgresError, asyncio.TimeoutError) as e:
            # RESILIENCE FIX: Temporary DB failures don't crash the task loop
            logger.warning(f"crypto_payment_watcher: Database temporarily unavailable: {type(e).__name__}: {str(e)[:100]}")
            outcome = "degraded"
            iteration_error_type = "infra_error"
        except Exception as e:
            logger.error(f"crypto_payment_watcher: Unexpected error in task loop: {type(e).__name__}: {str(e)[:100]}")
            logger.debug("crypto_payment_watcher: Full traceback for task loop", exc_info=True)
            outcome = "failed"
            iteration_error_type = classify_error(e)
        finally:
            # H2 fix: ITERATION_END always fires in finally block
            duration_ms = (time.time() - iteration_start_time) * 1000
            error_type = iteration_error_type if 'iteration_error_type' in locals() else (None if outcome == "success" else "infra_error")
            log_worker_iteration_end(
                worker_name="crypto_payment_watcher",
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
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
