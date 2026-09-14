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

# Bot API 9.4 default button style for the whole bot. Patches
# InlineKeyboardButton.__init__ to inject style="danger" when callers
# don't pass one explicitly. MUST run before any handler module
# loads — see docstring for the import-order rationale.
import app.utils.button_defaults  # noqa: F401

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
from app.workers import traffic_monitor
from app.workers import provisioning_worker
from app.workers import sales_funnel

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


async def acquire_instance_lock() -> None:
    """ADVISORY_LOCK_FIX: single-instance guard via PostgreSQL (1 s max wait).

    PROD: lock not acquired → exit (another instance may be running). Elsewhere:
    warn and continue without the guard. No-op when the lock is already held.
    """
    global instance_lock_conn
    if instance_lock_conn is not None:
        return
    pool = await database.get_pool()
    if not pool:
        logger.critical("DB pool missing; cannot acquire advisory lock. Exiting.")
        sys.exit(1)
    try:
        instance_lock_conn = await pool.acquire()
        await instance_lock_conn.execute("SET lock_timeout = '1000'")
        await instance_lock_conn.execute("SELECT pg_advisory_lock($1)", ADVISORY_LOCK_KEY)
        logger.info("Advisory lock acquired")
    except Exception as e:
        if config.IS_PROD:
            logger.critical("Advisory lock not acquired in PROD — another instance may be running: %s", e)
            sys.exit(1)
        logger.warning("Advisory lock not acquired (timeout or error), continuing without single-instance guard: %s", e)
        if instance_lock_conn:
            await pool.release(instance_lock_conn)
            instance_lock_conn = None


async def start_db_services(bot, background_tasks: list, started: dict) -> None:
    """Every DB-dependent background worker — the ONE list for the normal start
    (main) and for the recovery after a DB outage (retry_db_init), so the two
    cannot diverge (N5, docs/audit/06_bug_hunt.md). The single-instance advisory
    lock is taken FIRST (PROD + lock held elsewhere → exit before any worker).
    `started` (name → task) is shared by both paths: a worker starts at most once.
    """
    await acquire_instance_lock()

    def _start(name, factory):
        if name in started:
            return
        task = asyncio.create_task(factory())
        started[name] = task
        background_tasks.append(task)
        logger.info("DB worker started: %s", name)

    _start("reminders", lambda: reminders.reminders_task(bot))
    _start("trial_notifications", lambda: trial_notifications.run_trial_scheduler(bot))
    _start("farm_notifications", lambda: farm_notifications.farm_notifications_task(bot))
    if config.REMNAWAVE_ENABLED:
        _start("traffic_monitor", lambda: traffic_monitor.traffic_monitor_task(bot))
    else:
        logger.info("Traffic monitor task skipped (REMNAWAVE_ENABLED=false)")
    _start("fast_expiry_cleanup", lambda: fast_expiry_cleanup.fast_expiry_cleanup_task(bot))
    flags = get_feature_flags()
    if flags.background_workers_enabled and flags.auto_renewal_enabled:
        _start("auto_renewal", lambda: auto_renewal.auto_renewal_task(bot))
    else:
        logger.warning(
            "Auto-renewal task skipped (feature flag: background_workers=%s, auto_renewal=%s)",
            flags.background_workers_enabled, flags.auto_renewal_enabled,
        )
    _start("activation_worker", lambda: activation_worker.activation_worker_task(bot))
    # Wata reconciler — защита от потерянных webhook'ов (каждые 5 минут)
    try:
        import wata_service as _wata
        if _wata.is_enabled():
            from app.workers.wata_reconciler import wata_reconciler_task
            _start("wata_reconciler", lambda: wata_reconciler_task(bot))
            # Прогрев публичного ключа webhook-подписи (fail-closed): без ключа
            # webhook отвечает 500 до первой успешной загрузки. Не блокирует старт.
            _start("wata_key_warmup", lambda: _wata.warmup_public_key())
        else:
            logger.info("Wata reconciler skipped (WATA_ACCESS_TOKEN not configured)")
    except Exception as e:
        logger.warning("Wata reconciler failed to start: %s", e)
    # Provisioning worker — drains provisioning_jobs (retries, per-user order, alerts).
    # Runs ALWAYS (no feature flag) so enqueued jobs never hang; needs only the DB.
    _start("provisioning_worker", lambda: provisioning_worker.provisioning_worker_task(bot))
    # Sales funnel (docs/audit/SCOPE.md «Воронка продаж»): three chains of sales
    # messages from DB state; only events after its first pass (no retro-sends).
    _start("sales_funnel", lambda: sales_funnel.sales_funnel_task(bot))


async def retry_db_init(bot, background_tasks: list, started: dict, *, retry_interval: float = 30) -> None:
    """Фоновая задача повторной инициализации БД (DB недоступна при старте).

    - проверяет доступность БД каждые retry_interval секунд;
    - при успехе (или если БД стала доступна извне) — start_db_services: ТОТ ЖЕ
      набор воркеров, что при обычном старте, advisory lock первым;
    - никогда не падает (исключения логируются), не блокирует event loop.
    """
    if database.DB_READY:
        logger.info("Database already ready, retry task not needed")
        return

    logger.info("Starting DB initialization retry task (will retry every %s seconds)", retry_interval)

    while True:
        try:
            await asyncio.sleep(retry_interval)

            if database.DB_READY:
                logger.info("Database became available, starting DB services")
                await start_db_services(bot, background_tasks, started)
                break

            logger.info("🔄 Retrying database initialization...")
            try:
                success = await database.init_db()
                if success:
                    # init_db() sets DB_READY and recalculates SystemState internally;
                    # migrations are never re-run once DB_READY=True.
                    logger.info("✅ DATABASE RECOVERY SUCCESSFUL — RESUMING FULL FUNCTIONALITY")
                    try:
                        await admin_notifications.notify_admin_recovered(bot)
                    except Exception as e:
                        logger.error(f"Failed to send recovery notification: {e}")
                    await start_db_services(bot, background_tasks, started)
                    logger.info("DB retry task completed successfully, stopping retry loop")
                    break
                else:
                    logger.warning("Database initialization retry failed, will retry later")
            except Exception as e:
                logger.warning(f"Database initialization retry error: {type(e).__name__}: {e}")
                logger.debug("Full retry error details:", exc_info=True)

        except asyncio.CancelledError:
            logger.info("DB retry task cancelled")
            break
        except Exception as e:
            logger.exception(f"Unexpected error in DB retry task: {e}")
            await asyncio.sleep(retry_interval)

    logger.info("DB retry task finished")


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
    # TG-RT-6: a late answerCallbackQuery ("query is too old") must not abort the handler
    from app.utils.telegram_request_middleware import install as install_request_middlewares
    install_request_middlewares(bot)
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
    MAX_CONCURRENT_UPDATES = int(os.getenv("MAX_CONCURRENT_UPDATES", "20"))
    update_semaphore = asyncio.Semaphore(MAX_CONCURRENT_UPDATES)
    logger.info("CONCURRENCY_LIMIT=%s", MAX_CONCURRENT_UPDATES)
    
    from app.core.concurrency_middleware import ConcurrencyLimiterMiddleware
    from app.core.telegram_error_middleware import TelegramErrorBoundaryMiddleware
    from app.core.chat_filter_middleware import PrivateChatOnlyMiddleware
    from app.core.rate_limit_middleware import GlobalRateLimitMiddleware
    from app.core.last_seen_middleware import LastSeenMiddleware

    dp.update.middleware(ConcurrencyLimiterMiddleware(update_semaphore))
    dp.update.middleware(TelegramErrorBoundaryMiddleware())
    # 1. Фильтр приватных чатов (отсекает группы до любой обработки)
    dp.message.middleware(PrivateChatOnlyMiddleware())
    dp.callback_query.middleware(PrivateChatOnlyMiddleware())
    # 2. Rate limiting
    dp.message.middleware(GlobalRateLimitMiddleware())
    dp.callback_query.middleware(GlobalRateLimitMiddleware())
    # 3. last_seen_at bump (fire-and-forget) for the Farm storm online/offline split
    dp.message.middleware(LastSeenMiddleware())
    dp.callback_query.middleware(LastSeenMiddleware())

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

    # Single-instance advisory lock + every DB worker: ONE function for this normal
    # start and for the recovery after a DB outage (retry_db_init), so the two
    # paths cannot diverge again (N5). The lock is taken BEFORE any worker.
    global instance_lock_conn
    instance_lock_conn = None

    # Centralized list for graceful shutdown
    background_tasks = []
    # name → task of every DB worker already started (shared with retry_db_init)
    db_workers_started: dict = {}

    if database.DB_READY:
        await start_db_services(bot, background_tasks, db_workers_started)
    else:
        logger.warning("DB not ready; advisory lock and DB workers deferred until the DB recovers")

    # Запуск фоновой задачи для health-check
    healthcheck_task = asyncio.create_task(healthcheck.health_check_task(bot))
    background_tasks.append(healthcheck_task)
    logger.info("Health check task started")

    # Admin notifier — fans the app.events.bus out to admin Telegram DMs
    # (payment errors, broadcast completions, daily revenue milestones).
    # Cheap to run: it just subscribes to the in-process bus.
    try:
        from app.services.admin_notifier import run_admin_notifier
        admin_notifier_task = asyncio.create_task(run_admin_notifier(bot))
        background_tasks.append(admin_notifier_task)
        logger.info("Admin notifier task started")
    except Exception as e:
        logger.warning("admin_notifier failed to start: %s", e)

    # Automated notifications registry sync (migration 068). Upsert-only
    # для defaults — админ-правки не затираются. Ошибка не критична: если
    # sync упал, bot всё равно работает по in-code REGISTRY defaults.
    try:
        from app.services.automated_notifications import sync_registry_to_db
        synced = await sync_registry_to_db()
        logger.info("Automated notifications registry synced: %d specs", synced)
    except Exception as e:
        logger.warning("automated_notifications sync failed: %s", e)

    # Scheduled + recurring broadcasts (migration 067)
    # Long-lived task: раз в минуту проверяет БД и запускает готовые рассылки.
    try:
        from app.services.scheduled_broadcasts_worker import (
            run_scheduled_broadcasts_worker,
        )
        sched_bcast_task = asyncio.create_task(
            run_scheduled_broadcasts_worker(bot)
        )
        background_tasks.append(sched_bcast_task)
        logger.info("Scheduled broadcasts worker started")
    except Exception as e:
        logger.warning("scheduled_broadcasts_worker failed to start: %s", e)

    # NB: Incy deep-links are NOT pure-Python. `incy_crypto.to_incy_link()`
    # first tries the crypt1 path (`to_incy_link_crypt1` → `_spawn` →
    # `node scripts/incy_encode.mjs`, npm `@incy/link-encoder`) and only on
    # failure falls back to plain `incy://add/<url>`. The Node toolchain in
    # the Docker image is therefore required for crypt1 links.
    
    # ====================================================================================
    # HTTP Health Check Server
    # ====================================================================================
    # Запускаем HTTP сервер для мониторинга и диагностики
    # Endpoint: GET /health - возвращает статус БД и приложения
    # ====================================================================================
    # In webhook mode, /health is served by FastAPI (app/api/__init__.py)
    # No separate health server needed
    
    # ====================================================================================
    # SAFE STARTUP GUARD: DB unavailable at start → retry every 30 s. On recovery
    # retry_db_init calls the same start_db_services (lock first, full worker set).
    # ====================================================================================
    if not database.DB_READY:
        background_tasks.append(asyncio.create_task(
            retry_db_init(bot, background_tasks, db_workers_started)
        ))
        logger.info("DB retry task started (will retry every 30 seconds until DB is ready)")
    else:
        logger.info("Database already ready, skipping retry task")

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
            BotCommand(command="hwadd", description="📲 Добавить устройство"),
            BotCommand(command="docs", description="🔐 Политика конфиденциальности"),
            BotCommand(command="language", description="Изменить язык"),
        ])
        # Same commands for English-language Telegram clients (default above is RU).
        await bot.set_my_commands([
            BotCommand(command="start", description="Main menu"),
            BotCommand(command="profile", description="My profile"),
            BotCommand(command="connect", description="Connect"),
            BotCommand(command="buy", description="Buy access"),
            BotCommand(command="referral", description="Loyalty program"),
            BotCommand(command="info", description="About the service"),
            BotCommand(command="support", description="Support"),
            BotCommand(command="help", description="Help"),
            BotCommand(command="instruction", description="Setup guide"),
            BotCommand(command="hwadd", description="📲 Add a device"),
            BotCommand(command="docs", description="🔐 Privacy policy"),
            BotCommand(command="language", description="Change language"),
        ], language_code="en")
        logger.info("Bot commands registered (ru default, en)")
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
                # Keep the updates queued during a restart / deploy: Telegram delivers
                # them now. Dropping them lost successful_payment (money taken, nothing
                # granted, no alert); the handlers are idempotent per purchase / charge.
                drop_pending_updates=False,
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
        
        # Telegram successful_payment finalizations run as shielded tasks outside
        # background_tasks and need the DB pool + bot session: give them up to
        # 20 s before anything is cancelled / closed. (On SIGTERM uvicorn's
        # shutdown hook already drained them — then this returns at once.)
        try:
            from app.api import telegram_webhook as _tg_webhook
            await _tg_webhook.drain_payment_tasks()
        except Exception as e:
            logger.warning("shutdown_payment_drain_failed error=%s", e)

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

