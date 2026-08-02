import asyncio
import hashlib
import json
import logging
import os
import signal
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

    # Single-instance guard: advisory-лок в PostgreSQL.
    #
    # Зачем функция, а не блок кода. Раньше лок брался ровно один раз — на
    # старте и только при database.DB_READY. Если бот поднялся в момент, когда
    # база была недоступна, лок не брался вовсе, а задача восстановления его
    # не пыталась взять никогда. То есть гарантия «работает одна реплика»
    # терялась навсегда, и проверка IS_PROD с выходом из процесса тоже не
    # срабатывала. Две реплики при этом параллельно продлевают подписки и
    # рассылают уведомления.
    #
    # Теперь ту же функцию вызывает retry_db_init сразу после успешного
    # init_db — до запуска восстановленных воркеров.
    global instance_lock_conn
    instance_lock_conn = None

    async def acquire_instance_lock(reason: str) -> bool:
        """Взять advisory-лок. True — взят или уже держим.

        В PROD неудача означает, что где-то работает вторая реплика, —
        завершаем процесс, как и при старте. В остальных окружениях
        предупреждаем и продолжаем без гарантии.
        """
        global instance_lock_conn
        if instance_lock_conn is not None:
            return True
        if not database.DB_READY:
            logger.warning(
                "Advisory-лок не взят (%s): БД не готова, single-instance guard выключен", reason,
            )
            return False
        pool = await database.get_pool()
        if not pool:
            logger.critical("Нет пула БД, advisory-лок взять нельзя (%s). Выходим.", reason)
            sys.exit(1)
        conn = None
        try:
            conn = await pool.acquire()
            # lock_timeout=1s: ждать дольше на старте нельзя, а «занято»
            # само по себе — ответ (значит, есть вторая реплика).
            await conn.execute("SET lock_timeout = '1000'")
            await conn.execute("SELECT pg_advisory_lock($1)", ADVISORY_LOCK_KEY)
            instance_lock_conn = conn
            logger.info("Advisory-лок взят (%s)", reason)
            return True
        except Exception as e:
            if conn is not None:
                try:
                    await pool.release(conn)
                except Exception:
                    pass
            if config.IS_PROD:
                logger.critical(
                    "Advisory-лок не взят в PROD (%s) — вероятно, работает вторая реплика: %s",
                    reason, e,
                )
                sys.exit(1)
            logger.warning(
                "Advisory-лок не взят (%s), продолжаем без single-instance guard: %s", reason, e,
            )
            return False

    await acquire_instance_lock("старт")
    
    # Centralized list for graceful shutdown
    background_tasks = []
    
    # ──────────────────────────────────────────────────────────────────
    #  Фоновые воркеры, зависящие от БД — одна декларативная таблица
    # ──────────────────────────────────────────────────────────────────
    #
    # Зачем таблица вместо десяти одинаковых блоков «если БД готова —
    # создать задачу». Раньше стартовый код и код восстановления после
    # падения БД были написаны отдельно, и списки разошлись: при
    # восстановлении поднимались пять воркеров из девяти. Остальные —
    # trial-уведомления, ферма, монитор трафика, синхронизация с сайтом —
    # молчали до перезапуска процесса, и понять это по логам было нельзя:
    # ошибок нет, просто тишина.
    #
    # Теперь список один. Добавляя воркер сюда, вы автоматически получаете
    # и запуск, и восстановление. Забыть про восстановление невозможно.
    #
    # Поля:
    #   name    — ключ для логов и для проверки «уже запущен».
    #   enabled — доп. условие поверх DB_READY (фича-флаги, конфиг).
    #   start   — корутина, возвращающая задачу или None, если воркер
    #             сам решил не стартовать (например, не настроен).
    async def _start_site_sync(bot_obj):
        """Синхронизация с сайтом стартует, только если он настроен."""
        from app.workers.site_sync_worker import site_sync_worker_task
        from app.services.site_sync import is_enabled as _site_sync_enabled
        if not _site_sync_enabled():
            logger.info("Site sync worker skipped (SITE_API_URL / SITE_BOT_API_KEY не заданы)")
            return None
        return asyncio.create_task(site_sync_worker_task(bot_obj))

    async def start_xray_sync_safe(bot_obj):
        """Xray sync — необязательный воркер, никогда не роняет бота."""
        if not XRAY_SYNC_AVAILABLE:
            logger.info("[XRAY_SYNC] модуль недоступен, пропускаем")
            return None
        if not config.XRAY_SYNC_ENABLED:
            logger.info("[XRAY_SYNC] выключен конфигом (XRAY_SYNC_ENABLED=false)")
            return None
        if not config.VPN_ENABLED:
            logger.info("[XRAY_SYNC] VPN выключен, пропускаем")
            return None
        return asyncio.create_task(xray_sync.start(bot_obj))

    DB_DEPENDENT_WORKERS = [
        {
            "name": "reminders",
            "enabled": lambda: True,
            "start": lambda b: asyncio.create_task(reminders.reminders_task(b)),
        },
        {
            "name": "trial_notifications",
            "enabled": lambda: True,
            "start": lambda b: asyncio.create_task(trial_notifications.run_trial_scheduler(b)),
        },
        {
            "name": "farm_notifications",
            "enabled": lambda: True,
            "start": lambda b: asyncio.create_task(farm_notifications.farm_notifications_task(b)),
        },
        {
            "name": "traffic_monitor",
            "enabled": lambda: config.REMNAWAVE_ENABLED,
            "start": lambda b: asyncio.create_task(traffic_monitor.traffic_monitor_task(b)),
        },
        {
            "name": "fast_expiry_cleanup",
            "enabled": lambda: True,
            "start": lambda b: asyncio.create_task(fast_expiry_cleanup.fast_expiry_cleanup_task(b)),
        },
        {
            "name": "auto_renewal",
            # Два kill-switch: общий по фоновым воркерам и отдельный по
            # автопродлению. Читаем флаги в момент запуска, а не на старте
            # процесса, — при восстановлении они могли измениться.
            "enabled": lambda: (
                get_feature_flags().background_workers_enabled
                and get_feature_flags().auto_renewal_enabled
            ),
            "start": lambda b: asyncio.create_task(auto_renewal.auto_renewal_task(b)),
        },
        {
            "name": "activation_worker",
            "enabled": lambda: True,
            "start": lambda b: asyncio.create_task(activation_worker.activation_worker_task(b)),
        },
        {
            "name": "site_sync",
            "enabled": lambda: True,
            "start": _start_site_sync,
        },
        {
            "name": "xray_sync",
            "enabled": lambda: True,
            "start": start_xray_sync_safe,
        },
    ]

    # Какие воркеры уже подняты. Ключ есть = второй раз не поднимаем.
    started_workers: dict = {}

    async def start_db_workers(reason: str) -> None:
        """Поднять все воркеры из таблицы, которые ещё не запущены.

        Вызывается на старте и после восстановления БД. Ошибка одного
        воркера не мешает остальным: бот важнее любой фоновой задачи.
        """
        if not database.DB_READY:
            logger.warning("Фоновые воркеры пропущены (%s): БД не готова", reason)
            return
        for spec in DB_DEPENDENT_WORKERS:
            name = spec["name"]
            if started_workers.get(name) is not None:
                continue
            try:
                if not spec["enabled"]():
                    logger.info("Воркер %s пропущен (%s): выключен условием", name, reason)
                    continue
                task = spec["start"](bot)
                if asyncio.iscoroutine(task):
                    task = await task
                if task is None:
                    continue
                started_workers[name] = task
                background_tasks.append(task)
                logger.info("Воркер %s запущен (%s)", name, reason)
            except Exception as e:
                logger.warning("Воркер %s не запустился (%s): %s", name, reason, e)

    await start_db_workers("старт")



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

    # NB: incy_crypto.selftest() used to be scheduled here for the
    # crypt1 / Node-sidecar code path. Production `to_incy_link()` is
    # now pure-Python (`incy://add/<plain_url>` — universal across
    # Incy versions, including v2.2.1 that doesn't decode crypt1 yet),
    # so the selftest doesn't tell us anything actionable. The
    # underlying `_spawn` / `selftest` are still callable for the day
    # we switch back — re-instate this hook then.
    
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
                        
                        # Сначала — single-instance лок. Если бот стартовал
                        # при недоступной БД, лок не брался вовсе, и гарантия
                        # «одна реплика» терялась навсегда: retry-задача её не
                        # восстанавливала. Берём здесь, до воркеров, — иначе
                        # две реплики начнут продлевать подписки параллельно.
                        await acquire_instance_lock("после восстановления БД")

                        # Воркеры — из той же таблицы, что и на старте.
                        # Раньше здесь был отдельный список из пяти штук, и
                        # четыре воркера молчали до перезапуска процесса.
                        await start_db_workers("после восстановления БД")

                        # Разовая синхронизация реестра автоуведомлений: на
                        # старте она молча провалилась (БД не было), и админ
                        # не увидел бы их в дашборде до перезапуска процесса.
                        try:
                            from app.services.automated_notifications import sync_registry_to_db
                            synced = await sync_registry_to_db()
                            logger.info(
                                "Реестр автоуведомлений синхронизирован после восстановления: %d",
                                synced,
                            )
                        except Exception as e:
                            logger.warning("automated_notifications sync failed after recovery: %s", e)

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
        #
        # Раньше здесь стоял asyncio.gather по всем задачам. Он ждал их
        # завершения и не реагировал на SIGTERM: Railway при деплое шлёт именно
        # SIGTERM, KeyboardInterrupt при этом не возникает, процесс убивался
        # снаружи и блок finally ниже не выполнялся никогда — вебхук не
        # удалялся, задачи не отменялись, соединения не закрывались.
        #
        # Кроме того, gather не давал заметить смерть отдельной задачи: если
        # падал uvicorn или воркер, процесс продолжал жить пустой оболочкой.
        # FIRST_COMPLETED возвращает управление и в этом случае — процесс
        # корректно завершится, а Railway поднимет его заново.
        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig_name in ("SIGTERM", "SIGINT"):
            sig = getattr(signal, sig_name, None)
            if sig is None:
                continue
            try:
                loop.add_signal_handler(sig, stop_event.set)
                logger.info("SIGNAL_HANDLER_REGISTERED signal=%s", sig_name)
            except NotImplementedError:
                # Windows не поддерживает add_signal_handler
                logger.warning("SIGNAL_HANDLER_UNSUPPORTED signal=%s", sig_name)

        stop_waiter = asyncio.create_task(stop_event.wait(), name="shutdown_signal")
        done, _pending = await asyncio.wait(
            [stop_waiter, *background_tasks],
            return_when=asyncio.FIRST_COMPLETED,
        )
        stop_waiter.cancel()

        for finished in done:
            name = finished.get_name() if hasattr(finished, "get_name") else "unknown"
            if finished is stop_waiter:
                logger.info("SHUTDOWN_SIGNAL_RECEIVED — начинается корректная остановка")
                continue
            exc = finished.exception() if not finished.cancelled() else None
            if exc is not None:
                logger.error(
                    "BACKGROUND_TASK_DIED task=%s error=%s: %s — процесс будет остановлен",
                    name, type(exc).__name__, exc,
                )
            else:
                logger.error("BACKGROUND_TASK_EXITED task=%s — процесс будет остановлен", name)
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

