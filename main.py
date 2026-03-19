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
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand
import config
import database
from app.core.feature_flags import get_feature_flags
from app.core.structured_logger import log_event
from app.handlers import router as root_router
import reminders
import healthcheck
import fast_expiry_cleanup
import auto_renewal
import admin_notifications
import trial_notifications
import activation_worker
from app.workers import farm_notifications
try:
    import xray_sync
    XRAY_SYNC_AVAILABLE = True
except Exception as e:
    XRAY_SYNC_AVAILABLE = False
    xray_sync = None
    print(f"[XRAY_SYNC] disabled: {e}")

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

# ADVISORY_LOCK_FIX: App-wide key for PostgreSQL advisory lock (replaces file lock).
# Lock is automatically released when process dies (connection closed).
ADVISORY_LOCK_KEY = 987654321

# Advisory lock connection (held for process lifetime); released in finally via pool.release().
instance_lock_conn = None


async def main():
    # Конфигурация уже проверена в config.py
    # Если переменные окружения не заданы, программа завершится с ошибкой

    instance_id = os.getenv("BOT_INSTANCE_ID", str(uuid.uuid4()))
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

    # Architecture assertion: Bot must NOT use XRAY_* for link generation
    if hasattr(config, "XRAY_SERVER_IP"):
        logger.warning("XRAY_* link constants detected in config. Ensure not used for link generation (API-only).")

    # Логируем информацию о конфигурации при старте
    logger.info(f"Starting bot in {config.APP_ENV.upper()} environment")
    logger.info(f"Using BOT_TOKEN from {config.APP_ENV.upper()}_BOT_TOKEN")
    logger.info(f"Using DATABASE_URL from {config.APP_ENV.upper()}_DATABASE_URL")
    logger.info(f"Using ADMIN_TELEGRAM_ID from {config.APP_ENV.upper()}_ADMIN_TELEGRAM_ID")

    # Log payment providers status
    flags = get_feature_flags()
    if flags.payments_enabled:
        import platega_service
        logger.info("PAYMENT_PROVIDERS: platega=%s", platega_service.is_enabled())

    # Инициализация бота и диспетчера
    bot = Bot(token=config.BOT_TOKEN)
    if config.REDIS_URL:
        storage = RedisStorage.from_url(config.REDIS_URL)
        logger.info("FSM_STORAGE=redis (configured)")
        # Validate Redis connectivity at startup
        try:
            from app.utils.redis_client import ping as redis_ping
            redis_ok = await redis_ping()
            if redis_ok:
                logger.info("REDIS_CONNECTIVITY=ok")
            else:
                raise RuntimeError("Redis ping returned False — FSM storage will not work")
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"Redis connectivity check failed: {type(e).__name__}: {e}") from e
    else:
        storage = MemoryStorage()
        logger.warning("FSM_STORAGE=memory — states will be lost on restart")

    dp = Dispatcher(storage=storage)

    # Pass bot and dp to webhook handler
    from app.api import telegram_webhook as tg_webhook_module
    tg_webhook_module.setup(bot, dp)

    # Pass bot to payment webhook handlers
    from app.api import payment_webhook as pay_webhook_module
    pay_webhook_module.setup(bot)

    # Global concurrency limiter for update processing
    MAX_CONCURRENT_UPDATES = int(os.getenv("MAX_CONCURRENT_UPDATES", "100"))
    update_semaphore = asyncio.Semaphore(MAX_CONCURRENT_UPDATES)
    logger.info("CONCURRENCY_LIMIT=%s", MAX_CONCURRENT_UPDATES)
    
    from app.core.concurrency_middleware import ConcurrencyLimiterMiddleware
    from app.core.telegram_error_middleware import TelegramErrorBoundaryMiddleware
    from app.core.chat_filter_middleware import PrivateChatOnlyMiddleware
    from app.core.rate_limit_middleware import GlobalRateLimitMiddleware
    from app.core.metrics_middleware import MetricsMiddleware

    # Metrics middleware FIRST (outermost) — captures all outcomes including errors
    dp.update.middleware(MetricsMiddleware())
    dp.update.middleware(ConcurrencyLimiterMiddleware(update_semaphore))
    dp.update.middleware(TelegramErrorBoundaryMiddleware())
    # 1. Фильтр приватных чатов (отсекает группы до любой обработки)
    dp.message.middleware(PrivateChatOnlyMiddleware())
    dp.callback_query.middleware(PrivateChatOnlyMiddleware())
    # 2. Rate limiting
    dp.message.middleware(GlobalRateLimitMiddleware())
    dp.callback_query.middleware(GlobalRateLimitMiddleware())

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

    # DISTRIBUTED LOCK: try_advisory_lock (non-blocking) for multi-instance safety.
    # If lock is already held by another instance, this instance still starts but
    # background workers are skipped (only webhook handlers run).
    # This enables horizontal scaling: multiple instances handle webhooks,
    # but only one runs background workers (reminders, auto_renewal, etc.).
    global instance_lock_conn
    instance_lock_conn = None
    workers_lock_acquired = False
    if database.DB_READY:
        pool = await database.get_pool()
        if not pool:
            logger.critical("DB pool missing; cannot acquire advisory lock. Exiting.")
            sys.exit(1)
        try:
            instance_lock_conn = await pool.acquire()
            # pg_try_advisory_lock returns TRUE if lock acquired, FALSE if already held
            workers_lock_acquired = await instance_lock_conn.fetchval(
                "SELECT pg_try_advisory_lock($1)", ADVISORY_LOCK_KEY
            )
            if workers_lock_acquired:
                logger.info("Advisory lock acquired — this instance will run background workers")
            else:
                logger.info(
                    "Advisory lock NOT acquired — another instance holds it. "
                    "This instance will only handle webhooks (no background workers)."
                )
                await pool.release(instance_lock_conn)
                instance_lock_conn = None
        except Exception as e:
            logger.warning("Advisory lock check failed, continuing without lock: %s", e)
            if instance_lock_conn:
                try:
                    await pool.release(instance_lock_conn)
                except Exception:
                    pass
                instance_lock_conn = None
            workers_lock_acquired = True  # Fallback: run workers to avoid stalled system
    else:
        logger.warning("DB not ready; skipping advisory lock (single-instance guard disabled)")
        workers_lock_acquired = True  # Run workers when DB recovers
    
    # Centralized list for graceful shutdown
    background_tasks = []

    # Worker supervisor: auto-restarts crashed workers, alerts admin
    from app.core.worker_monitor import WorkerSupervisor
    supervisor = WorkerSupervisor(bot, check_interval_s=30.0)

    # Background workers: only start if this instance holds the advisory lock
    # (or if lock acquisition was skipped). This enables horizontal scaling:
    # multiple instances handle webhooks, but only one runs workers.
    reminder_task = None
    trial_notifications_task = None
    farm_notifications_task = None

    if database.DB_READY and workers_lock_acquired:
        import biz_key_notifications

        # Register ALL workers with supervisor for auto-restart
        supervisor.register("reminders", reminders.reminders_task, bot, max_restarts=10, stale_threshold_s=1800)
        supervisor.register("trial_notifications", trial_notifications.run_trial_scheduler, bot, max_restarts=10, stale_threshold_s=3600)
        supervisor.register("farm_notifications", farm_notifications.farm_notifications_task, bot, max_restarts=10, stale_threshold_s=1800)
        supervisor.register("biz_key_notifications", biz_key_notifications.start_biz_key_notifier, bot, max_restarts=10, stale_threshold_s=3600)
    elif not database.DB_READY:
        logger.warning("Background workers skipped (DB not ready)")
    else:
        logger.info("Background workers skipped (advisory lock held by another instance)")

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
    # In webhook mode, /health is served by FastAPI (app/api/__init__.py)
    # No separate health server needed
    
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
                            t = asyncio.create_task(fast_expiry_cleanup.fast_expiry_cleanup_task(bot))
                            recovered_tasks["fast_cleanup"] = t
                            background_tasks.append(t)
                            logger.info("Fast expiry cleanup task started (recovered)")
                        
                        if auto_renewal_task is None and recovered_tasks["auto_renewal"] is None:
                            _flags_recovery = get_feature_flags()
                            if _flags_recovery.background_workers_enabled and _flags_recovery.auto_renewal_enabled:
                                t = asyncio.create_task(auto_renewal.auto_renewal_task(bot))
                                recovered_tasks["auto_renewal"] = t
                                background_tasks.append(t)
                                logger.info("Auto-renewal task started (recovered)")
                        
                        if activation_worker_task is None and recovered_tasks["activation_worker"] is None:
                            t = asyncio.create_task(activation_worker.activation_worker_task(bot))
                            recovered_tasks["activation_worker"] = t
                            background_tasks.append(t)
                            logger.info("Activation worker task started (recovered)")
                        
                        if XRAY_SYNC_AVAILABLE and config.XRAY_SYNC_ENABLED and xray_sync_task is None and recovered_tasks["xray_sync"] is None:
                            try:
                                t = await start_xray_sync_safe(bot)
                                if t:
                                    recovered_tasks["xray_sync"] = t
                                    background_tasks.append(t)
                                    logger.info("Xray sync worker started (recovered)")
                            except Exception as e:
                                logger.warning("Xray sync recovery failed: %s", e)
                        
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
    
    # Additional workers: gated by advisory lock like above
    fast_cleanup_task = None
    auto_renewal_task = None
    activation_worker_task = None

    if database.DB_READY and workers_lock_acquired:
        # Register additional workers with supervisor
        supervisor.register("fast_cleanup", fast_expiry_cleanup.fast_expiry_cleanup_task, bot, max_restarts=10, stale_threshold_s=1800)

        _flags = get_feature_flags()
        if _flags.background_workers_enabled and _flags.auto_renewal_enabled:
            supervisor.register("auto_renewal", auto_renewal.auto_renewal_task, bot, max_restarts=10, stale_threshold_s=3600)
        else:
            logger.warning(
                "Auto-renewal task skipped (feature flag: background_workers=%s, auto_renewal=%s)",
                _flags.background_workers_enabled, _flags.auto_renewal_enabled
            )

        supervisor.register("activation_worker", activation_worker.activation_worker_task, bot, max_restarts=10, stale_threshold_s=1800)

        # Start ALL registered workers at once
        supervisor_tasks = await supervisor.start_all()
        background_tasks.extend(supervisor_tasks)
        logger.info("Worker supervisor started with %d workers", len(supervisor._workers))
    elif not database.DB_READY:
        logger.warning("Cleanup/renewal/activation workers skipped (DB not ready)")
    else:
        logger.info("Cleanup/renewal/activation workers skipped (advisory lock held by another instance)")

    # Xray sync: safe optional background worker (fail-safe, never crashes bot)
    async def start_xray_sync_safe(bot_obj):
        if not XRAY_SYNC_AVAILABLE:
            print("[XRAY_SYNC] module not available, skipping startup")
            return None
        if not config.XRAY_SYNC_ENABLED:
            logger.info("[XRAY_SYNC] disabled by config (XRAY_SYNC_ENABLED=false), skipping")
            return None
        if not database.DB_READY or not config.VPN_ENABLED:
            logger.info("[XRAY_SYNC] DB or VPN not ready, skipping (will start on recovery if enabled)")
            return None
        try:
            task = asyncio.create_task(xray_sync.start(bot_obj))
            print("[XRAY_SYNC] started successfully")
            return task
        except Exception as e:
            logger.error("[XRAY_SYNC] failed to start: %s", e)
            return None

    xray_sync_task = None
    if workers_lock_acquired:
        xray_sync_task = await start_xray_sync_safe(bot)
        if xray_sync_task:
            background_tasks.append(xray_sync_task)
    else:
        logger.info("Xray sync skipped (advisory lock held by another instance)")
    
    # Bot initialization complete
    if database.DB_READY:
        logger.info("✅ Бот запущен в полнофункциональном режиме")
    else:
        logger.warning("⚠️ Бот запущен в ДЕГРАДИРОВАННОМ режиме (БД недоступна)")
    
    pid = os.getpid()
    logger.info("Telegram webhook mode (pid=%s)", pid)

    # STAGE startup guard
    if os.getenv("ENVIRONMENT") == "STAGE":
        logger.info("STAGE_STARTUP_GUARD_ACTIVE")
    
    # 4️⃣ Register bot slash commands (runs once on startup)
    try:
        await bot.set_my_commands([
            BotCommand(command="start", description="Главное меню"),
            BotCommand(command="profile", description="Мой профиль"),
            BotCommand(command="connect", description="Подключиться"),
            BotCommand(command="buy", description="Купить доступ"),
            BotCommand(command="referral", description="Программа лояльности"),
            BotCommand(command="info", description="О сервисе"),
            BotCommand(command="support", description="Поддержка"),
            BotCommand(command="help", description="Помощь"),
            BotCommand(command="instruction", description="Инструкция"),
            BotCommand(command="language", description="Изменить язык"),
        ])
        logger.info("Bot commands registered")
    except Exception as e:
        logger.warning(f"Failed to register bot commands: {e}")
    
    # Log dispatcher configuration
    try:
        used_updates = dp.resolve_used_update_types()
        logger.info(f"DISPATCHER_READY updates={used_updates}")
    except Exception as e:
        logger.warning(f"Failed to resolve update types: {e}")
        used_updates = None

    try:
        # Start webhook mode
        logger.info("STARTING_WEBHOOK_MODE url=%s port=%s",
                    config.WEBHOOK_URL, config.WEBHOOK_PORT)

        # Register webhook with Telegram (with error logging)
        try:
            await bot.set_webhook(
                url=config.WEBHOOK_URL,
                secret_token=config.WEBHOOK_SECRET,
                drop_pending_updates=True,
                allowed_updates=used_updates if used_updates else None,
            )
            logger.info("WEBHOOK_SET_SUCCESS url=%s", config.WEBHOOK_URL)
        except Exception as e:
            logger.error("WEBHOOK_SET_FAILED url=%s error=%s", config.WEBHOOK_URL, e)
            logger.exception("Failed to set webhook - full traceback:")
            try:
                from app.services.admin_alerts import send_alert
                await send_alert(bot, "worker", f"BOT STARTUP FAILED: Webhook set failed\nError: {type(e).__name__}: {str(e)[:200]}", force=True)
            except Exception:
                pass
            sys.exit(1)

        # Verify webhook was registered correctly
        try:
            wh_info = await bot.get_webhook_info()
            if wh_info.url != config.WEBHOOK_URL:
                logger.critical(
                    "WEBHOOK_VERIFICATION_FAILED expected=%s got=%s",
                    config.WEBHOOK_URL, wh_info.url
                )
                try:
                    from app.services.admin_alerts import send_alert
                    await send_alert(bot, "worker", f"BOT STARTUP FAILED: Webhook URL mismatch\nExpected: {config.WEBHOOK_URL}\nGot: {wh_info.url}", force=True)
                except Exception:
                    pass
                sys.exit(1)
            logger.info("WEBHOOK_VERIFIED url=%s", wh_info.url)
            
            # Log webhook info for diagnostics
            webhook_info_dict = {
                "url": wh_info.url or "",
                "has_custom_certificate": getattr(wh_info, "has_custom_certificate", None),
                "pending_update_count": getattr(wh_info, "pending_update_count", None),
                "last_error_date": getattr(wh_info, "last_error_date", None),
                "last_error_message": getattr(wh_info, "last_error_message", None),
            }
            logger.info("WEBHOOK_INFO %s", json.dumps(webhook_info_dict, default=str))
        except Exception as e:
            logger.error("WEBHOOK_VERIFICATION_FAILED error=%s", e)
            logger.exception("Failed to verify webhook - full traceback:")
            try:
                from app.services.admin_alerts import send_alert
                await send_alert(bot, "worker", f"BOT STARTUP FAILED: Webhook verification failed\nError: {type(e).__name__}: {str(e)[:200]}", force=True)
            except Exception:
                pass
            sys.exit(1)

        # Start uvicorn serving FastAPI
        try:
            import uvicorn
            from app.api import app as fastapi_app

            uv_config = uvicorn.Config(
                fastapi_app,
                host="0.0.0.0",
                port=config.WEBHOOK_PORT,
                log_level="warning",
            )
            uv_server = uvicorn.Server(uv_config)
            webhook_server_task = asyncio.create_task(
                uv_server.serve(), name="uvicorn_webhook"
            )
            background_tasks.append(webhook_server_task)
            logger.info("UVICORN_STARTED host=0.0.0.0 port=%s", config.WEBHOOK_PORT)
        except Exception as e:
            logger.error("UVICORN_START_FAILED port=%s error=%s", config.WEBHOOK_PORT, e)
            logger.exception("Failed to start uvicorn - full traceback:")
            sys.exit(1)

        # Keep process alive — wait for shutdown signal
        await asyncio.gather(*background_tasks, return_exceptions=True)
    except SystemExit:
        raise
    finally:
        log_event(logger, component="shutdown", operation="shutdown_start", outcome="success")
        # Delete webhook on shutdown
        try:
            await bot.delete_webhook()
            logger.info("WEBHOOK_DELETED")
        except Exception as e:
            logger.warning("webhook_delete_failed error=%s", e)
        
        # Cancel and await all background tasks gracefully
        log_event(
            logger,
            component="shutdown",
            operation="shutdown_tasks_cancelling",
            outcome="success",
            reason=f"count={len(background_tasks)}",
        )

        # Step 1: Stop supervisor (cancels all supervised workers)
        try:
            await supervisor.stop_all()
        except Exception as e:
            logger.warning("supervisor stop error: %s", e)

        # Step 2: Cancel remaining tasks
        for task in background_tasks:
            if task and not task.done():
                task.cancel()

        # Step 3: Await all tasks (handle CancelledError gracefully)
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

        # ADVISORY_LOCK_FIX: release lock and dedicated connection before closing pool.
        if instance_lock_conn:
            try:
                await instance_lock_conn.execute("SELECT pg_advisory_unlock($1)", ADVISORY_LOCK_KEY)
                logger.info("Advisory lock released")
            except Exception as e:
                logger.warning("advisory unlock failed: %s", e)
            try:
                pool = await database.get_pool()
                if pool is not None:
                    await pool.release(instance_lock_conn)
                    logger.info("Advisory connection returned to pool")
            except Exception as e:
                logger.warning("advisory connection release failed: %s", e)
            finally:
                instance_lock_conn = None
        
        # Close Redis client
        try:
            from app.utils.redis_client import close as redis_close
            await redis_close()
        except Exception as e:
            logger.debug(f"Error closing Redis client: {e}")

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


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен")

