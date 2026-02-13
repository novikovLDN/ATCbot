import asyncio
import hashlib
import json
import logging
import os
import sys
import uuid

# Configure logging FIRST (before any other imports that may log)
# Routes INFO/WARNING → stdout, ERROR/CRITICAL → stderr for correct container classification
from app.core.logging_config import setup_logging
setup_logging()

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand
import config
import database
from app.core.feature_flags import get_feature_flags
from app.core.structured_logger import log_event
from app.handlers import router as root_router
import reminders
import healthcheck
# import outline_cleanup  # DISABLED - мигрировали на Xray Core
import fast_expiry_cleanup
import auto_renewal
import health_server
import admin_notifications
import trial_notifications
import activation_worker
import xray_sync

# ====================================================================================
# STEP 2 — OBSERVABILITY & SLO FOUNDATION: LOGGING CONTRACT
# ====================================================================================
# 
# PART A — LOGGING CONTRACT (FOUNDATION)
# 
# Standard log fields (logical, not enforced by library):
# - component        (handler / worker / service / infra)
# - operation        (what is happening)
# - correlation_id   (request / task / iteration id)
# - outcome          (success | degraded | failed)
# - duration_ms      (when applicable)
# - reason           (short, non-PII explanation)
# 
# PART B — CORRELATION IDS:
# - For handlers: correlation_id = update_id or message_id
# - For workers: correlation_id = iteration_id (UUID or monotonic counter)
# - For services: accept correlation_id if already present, do NOT generate new ones
# 
# PART C — ENTRY / EXIT LOGGING:
# - Handlers: Log ENTRY (component=handler) and EXIT (success/degraded/failed)
# - Workers: Log ITERATION_START and ITERATION_END
# - DO NOT log per-item spam inside loops
# 
# PART D — FAILURE TAXONOMY:
# - infra_error         (DB down, network, timeouts)
# - dependency_error    (VPN API, payment provider)
# - domain_error        (invalid state, business rule)
# - unexpected_error     (bug, invariant violation)
# 
# PART E — SLO SIGNAL IDENTIFICATION (NO ENFORCEMENT):
# - Payment success rate
# - Subscription activation latency
# - Worker iteration success rate
# - System degraded vs unavailable ratio
# 
# SECURITY:
# - DO NOT log secrets, PII, or full payloads
# - Logging configured in app.core.logging_config (STDOUT/STDERR routing)
# ====================================================================================

logger = logging.getLogger(__name__)


INSTANCE_LOCK_FILE = "/tmp/atlas_bot.lock"


async def main():
    # Single instance guard: prevent multiple bot processes
    if os.path.exists(INSTANCE_LOCK_FILE):
        logger.critical("Another instance detected (lock file exists). Exiting.")
        print("Another instance detected. Exiting.")
        sys.exit(1)
    try:
        with open(INSTANCE_LOCK_FILE, "w") as f:
            f.write(str(os.getpid()))
    except OSError as e:
        logger.warning("Could not create instance lock file: %s", e)

    # Конфигурация уже проверена в config.py
    # Если переменные окружения не заданы, программа завершится с ошибкой

    instance_id = os.getenv("POLLING_INSTANCE_ID", str(uuid.uuid4()))
    from datetime import datetime, timezone
    process_start_dt = datetime.now(timezone.utc).isoformat()
    logger.info(
        "BOT_INSTANCE_STARTED pid=%s instance_id=%s PROCESS_START_TIMESTAMP=%s",
        os.getpid(), instance_id, process_start_dt
    )
    bot_token_hash = hashlib.sha256(config.BOT_TOKEN.encode()).hexdigest()[:8] if config.BOT_TOKEN else "N/A"
    logger.info("BOT_TOKEN_HASH=%s (first 8 chars of sha256)", bot_token_hash)
    from app.core.runtime_context import set_bot_start_time
    set_bot_start_time(datetime.now(timezone.utc))

    # Логируем информацию о конфигурации при старте
    logger.info(f"Starting bot in {config.APP_ENV.upper()} environment")
    logger.info(f"Using BOT_TOKEN from {config.APP_ENV.upper()}_BOT_TOKEN")
    logger.info(f"Using DATABASE_URL from {config.APP_ENV.upper()}_DATABASE_URL")
    logger.info(f"Using ADMIN_TELEGRAM_ID from {config.APP_ENV.upper()}_ADMIN_TELEGRAM_ID")

    # Defensive: payments enabled but no CryptoBot token → will silently disable
    flags = get_feature_flags()
    if flags.payments_enabled and not config.CRYPTOBOT_TOKEN:
        logger.warning("PAYMENTS_ENABLED_BUT_NO_CRYPTOBOT_TOKEN — CryptoBot disabled until token is set")

    # Инициализация бота и диспетчера
    bot = Bot(token=config.BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    
    # Global concurrency limiter for update processing
    MAX_CONCURRENT_UPDATES = int(os.getenv("MAX_CONCURRENT_UPDATES", "20"))
    update_semaphore = asyncio.Semaphore(MAX_CONCURRENT_UPDATES)
    logger.info("CONCURRENCY_LIMIT=%s", MAX_CONCURRENT_UPDATES)
    
    # Register middlewares (order: 1 ConcurrencyLimiter, 2 TelegramErrorBoundary, 3 Routers)
    from app.core.concurrency_middleware import ConcurrencyLimiterMiddleware
    from app.core.telegram_error_middleware import TelegramErrorBoundaryMiddleware
    dp.update.middleware(ConcurrencyLimiterMiddleware(update_semaphore))
    dp.update.middleware(TelegramErrorBoundaryMiddleware())
    
    # Регистрация handlers
    dp.include_router(root_router)
    
    # ====================================================================================
    # SAFE STARTUP GUARD: Инициализация базы данных с защитой от краша
    # ====================================================================================
    # Бот должен ВСЕГДА запускаться, даже если БД недоступна.
    # В случае ошибки бот работает в деградированном режиме.
    # ====================================================================================
    # Сбрасываем флаги уведомлений при старте (чтобы уведомления отправлялись при каждом старте)
    admin_notifications.reset_notification_flags()
    
    try:
        success = await database.init_db()
        # init_db() уже устанавливает DB_READY внутри себя после создания всех таблиц
        if success:
            logger.info("✅ База данных инициализирована успешно")
            # Проверяем, что DB_READY установлен корректно
            if not database.DB_READY:
                logger.error("CRITICAL: init_db() returned True but DB_READY is False")
                database.DB_READY = False
        else:
            logger.error("❌ DB INIT FAILED — RUNNING IN DEGRADED MODE")
            # DB_READY уже установлен в init_db()
            # Уведомляем администратора о деградированном режиме
            try:
                await admin_notifications.notify_admin_degraded_mode(bot)
            except Exception as e:
                logger.error(f"Failed to send degraded mode notification: {e}")
    except Exception as e:
        # КРИТИЧЕСКИ ВАЖНО: Не пробрасываем исключение, не останавливаем процесс
        logger.exception("❌ DB INIT FAILED — RUNNING IN DEGRADED MODE")
        logger.error(f"Database initialization error: {type(e).__name__}: {e}")
        database.DB_READY = False
        # Уведомляем администратора о деградированном режиме
        try:
            await admin_notifications.notify_admin_degraded_mode(bot)
        except Exception as e:
            logger.error(f"Failed to send degraded mode notification: {e}")
        # Продолжаем запуск бота в деградированном режиме
    
    # Centralized list for graceful shutdown
    background_tasks = []
    
    # Запуск фоновой задачи для напоминаний (только если БД готова)
    reminder_task = None
    if database.DB_READY:
        reminder_task = asyncio.create_task(reminders.reminders_task(bot))
        background_tasks.append(reminder_task)
        logger.info("Reminders task started")
    else:
        logger.warning("Reminders task skipped (DB not ready)")
    
    # Запуск фоновой задачи для trial-уведомлений (только если БД готова)
    trial_notifications_task = None
    if database.DB_READY:
        trial_notifications_task = asyncio.create_task(trial_notifications.run_trial_scheduler(bot))
        background_tasks.append(trial_notifications_task)
        logger.info("Trial notifications scheduler started")
    else:
        logger.warning("Trial notifications scheduler skipped (DB not ready)")
    
    # Запуск фоновой задачи для health-check
    healthcheck_task = asyncio.create_task(healthcheck.health_check_task(bot))
    background_tasks.append(healthcheck_task)
    logger.info("Health check task started")
    
    # ====================================================================================
    # HTTP Health Check Server
    # ====================================================================================
    # Запускаем HTTP сервер для мониторинга и диагностики
    # Endpoint: GET /health - возвращает статус БД и приложения
    # ====================================================================================
    health_server_host = os.getenv("HEALTH_SERVER_HOST", "0.0.0.0")
    health_server_port = int(os.getenv("HEALTH_SERVER_PORT", "8080"))
    health_server_task = asyncio.create_task(
        health_server.health_server_task(host=health_server_host, port=health_server_port, bot=bot)
    )
    background_tasks.append(health_server_task)
    logger.info(f"Health check HTTP server started on http://{health_server_host}:{health_server_port}/health")
    
    # ====================================================================================
    # SAFE STARTUP GUARD: Фоновая задача повторной инициализации БД
    # ====================================================================================
    # Пытается восстановить соединение с БД каждые 30 секунд
    # ====================================================================================
    # Переменные для отслеживания восстановленных задач (для db_retry_task)
    recovered_tasks = {
        "reminder": None,
        "fast_cleanup": None,
        "auto_renewal": None,
        "activation_worker": None,
        "xray_sync": None,
    }
    
    async def retry_db_init():
        """
        Фоновая задача для автоматической повторной инициализации БД
        
        Требования:
        - Запускается только если DB_READY == False
        - Проверяет доступность БД каждые 30 секунд
        - При успешной инициализации:
          - устанавливает DB_READY = True
          - логирует восстановление
          - завершает цикл (break)
        - Никогда не падает (все исключения обрабатываются)
        - Не блокирует главный event loop
        """
        nonlocal reminder_task, fast_cleanup_task, auto_renewal_task, activation_worker_task, xray_sync_task, recovered_tasks, background_tasks
        retry_interval = 30  # секунд
        
        # Если БД уже готова, задача не запускается
        if database.DB_READY:
            logger.info("Database already ready, retry task not needed")
            return
        
        logger.info("Starting DB initialization retry task (will retry every 30 seconds)")
        
        while True:
            try:
                # Ждём интервал перед следующей попыткой
                await asyncio.sleep(retry_interval)
                
                # Проверяем, не стала ли БД доступной извне
                if database.DB_READY:
                    logger.info("Database became available, stopping retry task")
                    break
                
                # Пытаемся инициализировать БД
                logger.info("🔄 Retrying database initialization...")
                try:
                    success = await database.init_db()
                    if success:
                        # PART B.4: init_db() already sets DB_READY = True internally
                        # PART B.4: if returns True → STOP retry loop
                        # PART B.4: NEVER re-run migrations once DB_READY=True
                        # PART A.2: init_db() already recalculates SystemState internally
                        logger.info("✅ DATABASE RECOVERY SUCCESSFUL — RESUMING FULL FUNCTIONALITY")
                        
                        # Уведомляем администратора о восстановлении
                        try:
                            await admin_notifications.notify_admin_recovered(bot)
                        except Exception as e:
                            logger.error(f"Failed to send recovery notification: {e}")
                        
                        # Запускаем задачи, которые были пропущены при старте
                        if reminder_task is None and recovered_tasks["reminder"] is None:
                            t = asyncio.create_task(reminders.reminders_task(bot))
                            recovered_tasks["reminder"] = t
                            background_tasks.append(t)
                            logger.info("Reminders task started (recovered)")
                        
                        if fast_cleanup_task is None and recovered_tasks["fast_cleanup"] is None:
                            t = asyncio.create_task(fast_expiry_cleanup.fast_expiry_cleanup_task())
                            recovered_tasks["fast_cleanup"] = t
                            background_tasks.append(t)
                            logger.info("Fast expiry cleanup task started (recovered)")
                        
                        if auto_renewal_task is None and recovered_tasks["auto_renewal"] is None:
                            t = asyncio.create_task(auto_renewal.auto_renewal_task(bot))
                            recovered_tasks["auto_renewal"] = t
                            background_tasks.append(t)
                            logger.info("Auto-renewal task started (recovered)")
                        
                        if activation_worker_task is None and recovered_tasks["activation_worker"] is None:
                            t = asyncio.create_task(activation_worker.activation_worker_task(bot))
                            recovered_tasks["activation_worker"] = t
                            background_tasks.append(t)
                            logger.info("Activation worker task started (recovered)")
                        
                        if xray_sync_task is None and recovered_tasks["xray_sync"] is None:
                            asyncio.create_task(xray_sync.trigger_startup_sync())
                            t = asyncio.create_task(xray_sync.xray_sync_worker_task(bot))
                            recovered_tasks["xray_sync"] = t
                            background_tasks.append(t)
                            logger.info("Xray sync worker started (recovered)")
                        
                        # Успешно инициализировали БД - выходим из цикла
                        logger.info("DB retry task completed successfully, stopping retry loop")
                        break
                    else:
                        # Инициализация не удалась, попробуем снова через интервал
                        logger.warning("Database initialization retry failed, will retry later")
                        
                except Exception as e:
                    # Ошибка при попытке инициализации - логируем, но продолжаем попытки
                    logger.warning(f"Database initialization retry error: {type(e).__name__}: {e}")
                    logger.debug("Full retry error details:", exc_info=True)
                    # Продолжаем цикл для следующей попытки
                    
            except asyncio.CancelledError:
                # Задача отменена (например, при остановке бота)
                logger.info("DB retry task cancelled")
                break
            except Exception as e:
                # Неожиданная ошибка в самом цикле - логируем и продолжаем
                logger.exception(f"Unexpected error in DB retry task: {e}")
                # Продолжаем работу даже при ошибках
                await asyncio.sleep(retry_interval)
        
        logger.info("DB retry task finished")
    
    # ====================================================================================
    # Запуск фоновой задачи повторной инициализации БД (только если БД не готова)
    # ====================================================================================
    db_retry_task_instance = None
    if not database.DB_READY:
        db_retry_task_instance = asyncio.create_task(retry_db_init())
        background_tasks.append(db_retry_task_instance)
        logger.info("DB retry task started (will retry every 30 seconds until DB is ready)")
    else:
        logger.info("Database already ready, skipping retry task")
    
    # Outline cleanup task DISABLED - мигрировали на Xray Core (VLESS)
    # Старая задача outline_cleanup больше не используется
    # cleanup_task = asyncio.create_task(outline_cleanup.outline_cleanup_task())
    # logger.info("Outline cleanup task started")
    cleanup_task = None
    logger.info("Outline cleanup task disabled (using Xray Core now)")
    
    # Запуск фоновой задачи для быстрой очистки истёкших подписок (только если БД готова)
    fast_cleanup_task = None
    if database.DB_READY:
        fast_cleanup_task = asyncio.create_task(fast_expiry_cleanup.fast_expiry_cleanup_task())
        background_tasks.append(fast_cleanup_task)
        logger.info("Fast expiry cleanup task started")
    else:
        logger.warning("Fast expiry cleanup task skipped (DB not ready)")
    
    # Запуск фоновой задачи для автопродления подписок (только если БД готова)
    auto_renewal_task = None
    if database.DB_READY:
        auto_renewal_task = asyncio.create_task(auto_renewal.auto_renewal_task(bot))
        background_tasks.append(auto_renewal_task)
        logger.info("Auto-renewal task started")
    else:
        logger.warning("Auto-renewal task skipped (DB not ready)")
    
    # Запуск фоновой задачи для активации отложенных подписок (только если БД готова)
    activation_worker_task = None
    if database.DB_READY:
        activation_worker_task = asyncio.create_task(activation_worker.activation_worker_task(bot))
        background_tasks.append(activation_worker_task)
        logger.info("Activation worker task started")
    else:
        logger.warning("Activation worker task skipped (DB not ready)")

    # Xray sync: startup full sync + periodic health/sync worker
    xray_sync_task = None
    if database.DB_READY and config.VPN_ENABLED:
        asyncio.create_task(xray_sync.trigger_startup_sync())
        xray_sync_task = asyncio.create_task(xray_sync.xray_sync_worker_task(bot))
        background_tasks.append(xray_sync_task)
        logger.info("Xray sync worker started")
    else:
        logger.warning("Xray sync skipped (DB not ready or VPN disabled)")
    
    # Запуск фоновой задачи для автоматической проверки CryptoBot платежей (только если БД готова)
    crypto_watcher_task = None
    if database.DB_READY:
        try:
            import crypto_payment_watcher
            crypto_watcher_task = asyncio.create_task(crypto_payment_watcher.crypto_payment_watcher_task(bot))
            background_tasks.append(crypto_watcher_task)
            logger.info("Crypto payment watcher task started")
        except Exception as e:
            logger.warning(f"Crypto payment watcher task skipped: {e}")
    else:
        logger.warning("Crypto payment watcher task skipped (DB not ready)")
    
    # ====================================================================================
    # TELEGRAM POLLING: Start polling ONLY AFTER DB init attempt finishes
    # ====================================================================================
    # ENSURE polling is started ONCE and ONLY from the primary process
    # Polling starts AFTER all initialization (DB, workers, health checks)
    # ====================================================================================
    if database.DB_READY:
        logger.info("✅ Бот запущен в полнофункциональном режиме")
    else:
        logger.warning("⚠️ Бот запущен в ДЕГРАДИРОВАННОМ режиме (БД недоступна)")
    
    # 3️⃣ ADD explicit startup log with PID
    pid = os.getpid()
    logger.info(f"Telegram polling started (pid={pid})")

    # PART 4 — Polling self-check: STAGE startup guard
    if os.getenv("ENVIRONMENT") == "STAGE":
        logger.info("STAGE_STARTUP_GUARD_ACTIVE")
    
    # 4️⃣ Register bot slash commands (runs once on startup)
    try:
        await bot.set_my_commands([
            BotCommand(command="start", description="Запустить бота"),
            BotCommand(command="profile", description="Мой профиль"),
            BotCommand(command="buy", description="Купить доступ"),
            BotCommand(command="referral", description="Программа лояльности"),
            BotCommand(command="info", description="О сервисе"),
            BotCommand(command="help", description="Поддержка"),
            BotCommand(command="instruction", description="Инструкция"),
            BotCommand(command="language", description="Изменить язык"),
        ])
        logger.info("Bot commands registered")
    except Exception as e:
        logger.warning(f"Failed to register bot commands: {e}")
    
    # Log dispatcher configuration before polling
    try:
        used_updates = dp.resolve_used_update_types()
        logger.info(f"DISPATCHER_READY updates={used_updates}")
    except Exception as e:
        logger.warning(f"Failed to resolve update types: {e}")
        used_updates = None

    # ====================================================================================
    # PHASE 1 — TELEGRAM WEBHOOK AUDIT (before polling)
    # Ensure webhook is not interfering with polling
    # ====================================================================================
    try:
        webhook_info = await bot.get_webhook_info()
        webhook_audit = {
            "url": webhook_info.url or "",
            "has_custom_certificate": getattr(webhook_info, "has_custom_certificate", None),
            "pending_update_count": getattr(webhook_info, "pending_update_count", None),
            "ip_address": getattr(webhook_info, "ip_address", None),
            "last_error_date": getattr(webhook_info, "last_error_date", None),
            "last_error_message": getattr(webhook_info, "last_error_message", None),
            "max_connections": getattr(webhook_info, "max_connections", None),
        }
        logger.info("WEBHOOK_AUDIT_STATE %s", json.dumps(webhook_audit, default=str))

        if webhook_info.url and webhook_info.url.strip():
            logger.warning("WEBHOOK_ACTIVE url=%s — deleting before polling", webhook_info.url)
            await bot.delete_webhook(drop_pending_updates=True)
            webhook_info_after = await bot.get_webhook_info()
            webhook_after = {
                "url": webhook_info_after.url or "",
                "pending_update_count": getattr(webhook_info_after, "pending_update_count", None),
            }
            logger.info("WEBHOOK_AFTER_DELETE_STATE %s", json.dumps(webhook_after, default=str))
            if webhook_info_after.url and webhook_info_after.url.strip():
                logger.critical(
                    "CRITICAL: webhook.url still non-empty after delete: %s — exiting",
                    webhook_info_after.url
                )
                sys.exit(1)
    except Exception as e:
        logger.exception("Webhook audit failed: %s", e)
        sys.exit(1)

    try:
        # 2️⃣ Wrap dispatcher.start_polling() so it is called ONLY from the primary process
        # Polling is started ONCE and ONLY AFTER DB init attempt finishes
        # Restart guard: dispatcher crash does not kill process
        from aiogram.exceptions import TelegramConflictError

        while True:
            try:
                # PHASE 2: delete_webhook BEFORE dp.start_polling (no conditional skip)
                await bot.delete_webhook(drop_pending_updates=True)
                logger.info("Webhook deleted before polling start")

                logger.info(
                    "POLLING_START pid=%s instance_id=%s",
                    os.getpid(), instance_id
                )
                log_event(
                    logger,
                    component="polling",
                    operation="polling_start",
                    outcome="success",
                    correlation_id=instance_id,
                )
                await dp.start_polling(
                    bot,
                    allowed_updates=used_updates if used_updates else None,
                    polling_timeout=30,
                    handle_signals=False
                )
            except asyncio.CancelledError:
                logger.info("POLLING_STOP reason=cancelled")
                log_event(logger, component="polling", operation="polling_cancelled", outcome="cancelled")
                break
            except TelegramConflictError as e:
                try:
                    webhook_info = await bot.get_webhook_info()
                    webhook_snapshot = {
                        "url": webhook_info.url or "",
                        "pending_update_count": getattr(webhook_info, "pending_update_count", None),
                    }
                except Exception as we:
                    webhook_snapshot = {"error": str(we)}
                logger.critical(
                    "TELEGRAM_CONFLICT_DETECTED timestamp=%s instance_id=%s pid=%s webhook_state=%s",
                    datetime.now(timezone.utc).isoformat(),
                    instance_id,
                    os.getpid(),
                    json.dumps(webhook_snapshot, default=str),
                )
                log_event(
                    logger,
                    component="polling",
                    operation="conflict",
                    outcome="failed",
                    reason="another bot instance is running",
                    level="critical",
                )
                logger.critical("polling conflict traceback", exc_info=True)
                raise SystemExit(1)
            except Exception as e:
                logger.error(
                    "POLLING_EXCEPTION type=%s reason=%s instance_id=%s pid=%s",
                    type(e).__name__, str(e)[:200], instance_id, os.getpid(),
                    exc_info=True
                )
                log_event(
                    logger,
                    component="polling",
                    operation="polling_crash",
                    outcome="failed",
                    reason=str(e)[:200],
                    level="error",
                )
                logger.info("Restarting polling in 5 seconds...")
                await asyncio.sleep(5)
    except SystemExit:
        raise
    finally:
        log_event(logger, component="shutdown", operation="shutdown_start", outcome="success")
        
        # Stop polling cleanly
        try:
            if hasattr(dp, "stop_polling"):
                await dp.stop_polling()
        except Exception as e:
            logger.debug("stop_polling: %s", e)
        
        # Cancel and await all background tasks gracefully
        log_event(
            logger,
            component="shutdown",
            operation="shutdown_tasks_cancelling",
            outcome="success",
            reason=f"count={len(background_tasks)}",
        )
        
        # Step 1: Cancel all tasks
        for task in background_tasks:
            if task and not task.done():
                task.cancel()
        
        # Step 2: Await all tasks (handle CancelledError gracefully)
        for task in background_tasks:
            if task:
                try:
                    await task
                except asyncio.CancelledError:
                    # Expected during shutdown - task was cancelled gracefully
                    pass
                except Exception as e:
                    logger.error(f"Error during shutdown of task {task.get_name() if hasattr(task, 'get_name') else 'unknown'}: {e}")
        
        log_event(logger, component="shutdown", operation="shutdown_tasks_cancelled", outcome="success")
        
        # Close DB pool
        try:
            await database.close_pool()
        except Exception as e:
            logger.error(f"Error closing database pool: {e}")
        
        # Close bot session
        try:
            await bot.session.close()
            logger.info("Bot session closed")
        except Exception as e:
            logger.debug(f"Error closing bot session: {e}")
        
        log_event(logger, component="shutdown", operation="shutdown_completed", outcome="success")

        # Remove instance lock file
        try:
            if os.path.exists(INSTANCE_LOCK_FILE):
                os.remove(INSTANCE_LOCK_FILE)
                logger.info("Instance lock file removed")
        except OSError as e:
            logger.warning("Could not remove instance lock file: %s", e)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен")

