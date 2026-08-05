"""Фундамент работы с базой: пул соединений, готовность, инициализация.

ЧТО ЗДЕСЬ
    Глобальный флаг DB_READY, строка подключения, расчёт и создание пула,
    init_db со всеми проверками после миграций и два флага уведомлений о
    платежах. Плюс реэкспорт хелперов из database/db_helpers.py и
    легаси-бутстрапа схемы из database/legacy_schema.py.

ПОЧЕМУ ЭТОТ ФАЙЛ ОСОБЕННЫЙ
    Его импортируют почти все остальные модули пакета — тридцать с лишним
    штук. Любой импорт ОТСЮДА в сторону прикладного модуля, который сам
    тянет core, замыкает кольцо, и бот не поднимается вовсе: падение
    происходит на `import database`, до первой строчки логики. Поэтому
    core тянет только то, что про него ничего не знает: db_helpers и
    legacy_schema не импортируют database.core — проверьте это, прежде чем
    добавлять сюда новый импорт.

DB_READY — ЖИВЁТ ТОЛЬКО ЗДЕСЬ
    Это изменяемый флаг, а не константа. Двести с лишним мест читают его
    как `_core.DB_READY` — через атрибут модуля, а НЕ через
    `from database.core import DB_READY`: импорт по имени сделает копию,
    которая навсегда останется False, и весь бот тихо уйдёт в
    деградированный режим. По той же причине init_db и close_pool не могут
    переехать в соседний файл: их `global DB_READY` присвоит флаг чужому
    модулю, а читать все продолжат этот. database/__init__.py специально
    проксирует чтение и запись сюда (см. _DatabaseModuleProxy).

ЧТО ЛЕГКО СЛОМАТЬ
    1. Порядок в init_db. Пул создаётся до миграций и ПЕРЕСОЗДАЁТСЯ после
       них: asyncpg кэширует prepared statements, и после смены схемы
       старые соединения начинают падать на ровном месте.
    2. DB_READY выставляется в True ровно один раз и только после проверки
       всех обязательных таблиц. Поднимете флаг раньше — бот пустит
       пользователей в полусломанную базу.
    3. Размер пула. См. _get_pool_config: цифра посчитана под конкретное
       число воркеров и лимит Railway Postgres в 97 соединений.
"""
import asyncpg
import asyncio
import os
import sys
from typing import Optional, TYPE_CHECKING
import logging
import config
import vpn_utils
from app.utils.retry import retry_async
from app.core.system_state import ComponentStatus
# Легаси-DDL и посев промокодов вынесены отсюда — см. database/legacy_schema.py.
# Импорт односторонний: legacy_schema про core ничего не знает и знать не
# должен, иначе получится кольцо и бот не поднимется вовсе.
# _init_promo_codes переэкспортируется: его по имени берёт database/__init__.py.
from database.legacy_schema import (  # noqa: F401
    apply_legacy_schema_bootstrap,
    _init_promo_codes,
)
# outline_api removed - use vpn_utils instead

if TYPE_CHECKING:
    from aiogram import Bot

logger = logging.getLogger(__name__)

# ====================================================================================
# SAFE STARTUP GUARD: Глобальный флаг готовности базы данных
# ====================================================================================
# Этот флаг отражает, инициализирована ли база данных и безопасна ли она для использования.
# Если False, бот работает в деградированном режиме (degraded mode).
# ====================================================================================
DB_READY: bool = False


# ====================================================================================
# ХЕЛПЕРЫ ВРЕМЕНИ И БЕЗОПАСНЫХ ПРЕОБРАЗОВАНИЙ
# ====================================================================================
# Сами функции живут в database/db_helpers.py — они ничего не знают ни про пул,
# ни про DB_READY, и специально не импортируют core: так их можно тянуть откуда
# угодно, не рискуя кольцевым импортом.
#
# Реэкспорт здесь обязателен: примерно тридцать модулей и database/__init__.py
# годами берут их как `from database.core import _to_db_utc, safe_int, ...`.
# Уберёте строку — упадёт импорт пакета, то есть старт бота.
#
# ПРАВИЛО ГРАНИЦЫ (полностью объяснено в db_helpers.py): всё, что идёт В
# asyncpg, проходит через _to_db_utc; всё, что читается ИЗ базы, — через
# _from_db_utc. Колонки TIMESTAMP WITHOUT TIME ZONE, asyncpg ждёт naive UTC.
# ====================================================================================
from database.db_helpers import (  # noqa: F401,E402
    _to_db_utc,
    _from_db_utc,
    _generate_subscription_uuid,
    _ensure_utc,
    _normalize_subscription_row,
    safe_int,
    safe_float,
    safe_get,
)


# ====================================================================================
# ФЛАГИ УВЕДОМЛЕНИЙ О ПЛАТЕЖАХ
# ====================================================================================
# Единственные две функции в этом файле, которые ходят в прикладную таблицу.
# Оставлены здесь намеренно: вынести их в отдельный модуль — значит завести
# database.payment_notifications, который импортирует core ради get_pool, тогда
# как core обязан реэкспортировать его имена обратно (по именам их берёт
# database/__init__.py). Такое кольцо держится только на порядке импортов и
# рассыпается от первого же `import database.payment_notifications` в чужом
# файле, причём падением на старте бота. Семьдесят строк того не стоят.
# ====================================================================================


async def mark_payment_notification_sent(
    payment_id: int,
    conn: Optional[asyncpg.Connection] = None
) -> bool:
    """
    Атомарно пометить уведомление о платеже как отправленное (идемпотентность).
    
    Args:
        payment_id: ID платежа из таблицы payments
        conn: Существующее соединение (опционально, если None - создается новое)
    
    Returns:
        True если флаг был установлен (первая отправка), False если уже был установлен (повторная попытка)
    
    Raises:
        asyncpg exceptions: При ошибках БД
    """
    if conn:
        # Используем существующее соединение (внутри транзакции)
        result = await conn.execute(
            "UPDATE payments SET notification_sent = TRUE WHERE id = $1 AND notification_sent = FALSE",
            payment_id
        )
        # asyncpg execute возвращает строку вида "UPDATE 1" или "UPDATE 0"
        return "1" in result
    else:
        # Создаем новое соединение
        pool = await get_pool()
        if pool is None:
            raise RuntimeError("Database pool is not available")
        async with pool.acquire() as new_conn:
            result = await new_conn.execute(
                "UPDATE payments SET notification_sent = TRUE WHERE id = $1 AND notification_sent = FALSE",
                payment_id
            )
            return "1" in result


async def is_payment_notification_sent(
    payment_id: int,
    conn: Optional[asyncpg.Connection] = None
) -> bool:
    """
    Проверить, было ли уже отправлено уведомление о платеже.
    
    Args:
        payment_id: ID платежа из таблицы payments
        conn: Существующее соединение (опционально)
    
    Returns:
        True если уведомление уже отправлено, False если еще не отправлено
    """
    if conn:
        notification_sent = await conn.fetchval(
            "SELECT notification_sent FROM payments WHERE id = $1",
            payment_id
        )
        return notification_sent is True
    else:
        pool = await get_pool()
        if pool is None:
            return False
        async with pool.acquire() as new_conn:
            notification_sent = await new_conn.fetchval(
                "SELECT notification_sent FROM payments WHERE id = $1",
                payment_id
            )
            return notification_sent is True

# Получаем DATABASE_URL из переменных окружения через config.env()
# Используем префикс окружения (STAGE_DATABASE_URL / PROD_DATABASE_URL)
DATABASE_URL = config.env("DATABASE_URL")

# ====================================================================================
# DB POOL CONFIG — Production-safe, ENV-overridable, single source of truth
# ====================================================================================
def _get_pool_config() -> dict:
    """Build asyncpg.create_pool kwargs. Single source of truth for all pool creation.

    Откуда взялся max_size=50:
      - обработчики апдейтов Telegram: до 30 одновременно;
      - фоновые воркеры: 9 длинных задач, каждая берёт соединение на итерацию.
        Восемь из main.py DB_DEPENDENT_WORKERS (reminders, trial_notifications,
        farm_notifications, traffic_monitor, fast_expiry_cleanup, auto_renewal,
        activation_worker, site_sync) плюс scheduled_broadcasts_worker.
        Сверху ещё healthcheck со своим SELECT 1 раз в цикл;
      - вебхуки оплат на FastAPI: до 10;
      - запас на всплески: ~4.
      Итого около 50. Railway Postgres даёт 97 соединений — оставшееся нужно
      миграциям, pg_dump и ручным запросам, поэтому выше 50 поднимать нельзя
      без пересчёта всего списка.

      Раньше здесь стояло «~6 воркеров (reminders, trials, activation,
      xray_sync)». Число занижено, а xray_sync не существует вовсе — движок
      xray убран, подписки выдаёт Remnawave. Цифры пула не менялись, поправлен
      только счёт: по нему решают, можно ли добавить ещё один воркер.

    acquire timeout raised to 15s so burst requests queue instead of failing.
    """
    return {
        "min_size": int(os.getenv("DB_POOL_MIN_SIZE", "5")),
        "max_size": int(os.getenv("DB_POOL_MAX_SIZE", "50")),
        "max_inactive_connection_lifetime": 300,
        "timeout": int(os.getenv("DB_POOL_ACQUIRE_TIMEOUT", "15")),
        "command_timeout": int(os.getenv("DB_POOL_COMMAND_TIMEOUT", "30")),
    }


if not DATABASE_URL:
    # В PROD DATABASE_URL обязателен
    if config.APP_ENV == "prod":
        print(f"ERROR: {config.APP_ENV.upper()}_DATABASE_URL is REQUIRED in PROD!", file=sys.stderr)
        sys.exit(1)
    else:
        # В STAGE/LOCAL допустим degraded mode
        logger.warning(f"{config.APP_ENV.upper()}_DATABASE_URL is not set - running in degraded mode")

# Глобальный пул соединений
_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    """
    Получить пул соединений, создав его при необходимости
    
    STEP 1.3 - EXTERNAL DEPENDENCIES POLICY:
    - DB unavailable → RuntimeError raised (pool creation fails)
    - DB timeout → retried with exponential backoff (max 1 retry)
    - DB connection errors → retried only on transient errors (asyncpg.PostgresError)
    - Domain errors are NEVER retried → only transient infra errors are retried
    
    STEP 1.4 - SAFE DEPLOY & ROLLBACK:
    - Pool creation is backward-compatible → no schema assumptions
    - Pool can be created against older schema → migrations applied separately
    """
    global _pool
    if not DATABASE_URL:
        raise RuntimeError(f"{config.APP_ENV.upper()}_DATABASE_URL is not configured")
    if _pool is None:
        pool_config = _get_pool_config()
        _pool = await retry_async(
            lambda: asyncpg.create_pool(DATABASE_URL, **pool_config),
            retries=1,
            base_delay=0.5,
            max_delay=5.0,
            retry_on=(asyncpg.PostgresError,)
        )
        
        logger.info(
            "DB_POOL_CONFIG min=%s max=%s acquire_timeout=%s command_timeout=%s",
            pool_config["min_size"], pool_config["max_size"],
            pool_config["timeout"], pool_config["command_timeout"],
        )
    return _pool


# Note: pool.acquire() is already used with try/except in most places.
# For new code, wrap pool.acquire() calls with retry_async where needed.
# Example: conn = await retry_async(lambda: pool.acquire(), retries=2)


async def close_pool():
    """Закрыть пул соединений"""
    global _pool, DB_READY
    if _pool:
        await _pool.close()
        _pool = None
        DB_READY = False  # Помечаем БД как недоступную при закрытии пула
        logger.info("Database connection pool closed")


def ensure_db_ready() -> bool:
    """
    Проверка готовности базы данных перед выполнением операций
    
    Returns:
        True если БД готова, False если БД недоступна (деградированный режим)
    
    Usage:
        if not ensure_db_ready():
            return  # Операция отменена
    """
    if not DB_READY:
        logger.warning("Database not ready - operation rejected (degraded mode)")
        return False
    return True


async def check_critical_tables() -> bool:
    """
    Проверить существование КРИТИЧЕСКИХ таблиц (users)
    
    CRITICAL таблицы - это таблицы, без которых бот не может работать вообще.
    NON-CRITICAL таблицы (audit_log, incident_settings, referrals) могут отсутствовать.
    
    Returns:
        True если критические таблицы существуют, False если отсутствуют
    """
    if not DATABASE_URL:
        return False
    
    pool = await get_pool()
    if pool is None:
        return False
    
    try:
        async with pool.acquire() as conn:
            # Проверяем только users - это критическая таблица
            users_exists = await conn.fetchval("SELECT to_regclass('public.users')")
            if users_exists is None:
                logger.warning("CRITICAL: users table does not exist")
                return False
            return True
    except Exception as e:
        logger.warning(f"Error checking critical tables: {e}")
        return False


async def _get_pool_safe() -> Optional[asyncpg.Pool]:
    """
    Безопасное получение pool с проверкой DB_READY
    
    Returns:
        Pool если БД готова, None если БД не готова
    """
    if not DB_READY:
        return None
    return await get_pool()


async def init_db() -> bool:
    """
    Инициализация базы данных и создание таблиц
    
    Returns:
        True если инициализация успешна, False если произошла ошибка
        
    Raises:
        Любые исключения пробрасываются наверх для обработки в startup guard
    """
    global DB_READY, _pool
    
    # PART A.3: DB_READY must be set ONLY ONCE after all steps succeed
    # PART D.8: init_db() MUST be idempotent - safe to call N times
    if DB_READY:
        logger.info("Database already initialized (DB_READY=True), skipping init")
        return True
    
    # Сбрасываем DB_READY перед инициализацией (only if not already True)
    DB_READY = False
    
    if not DATABASE_URL:
        logger.error("DATABASE_URL not configured")
        return False
    
    # 1️⃣ AT THE VERY TOP: Explicit DB connectivity probe
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        await conn.execute("SELECT 1")
        await conn.close()
        logger.info("DB connectivity probe successful")
    except Exception as e:
        logger.error(f"DB connectivity probe failed: {e}")
        return False
    
    # 2️⃣ CREATE POOL — AND NOTHING ELSE (single source of truth via _get_pool_config)
    pool_config = _get_pool_config()
    try:
        _pool = await asyncpg.create_pool(DATABASE_URL, **pool_config)
        logger.info(
            "DB_POOL_CONFIG min=%s max=%s acquire_timeout=%s command_timeout=%s",
            pool_config["min_size"], pool_config["max_size"],
            pool_config["timeout"], pool_config["command_timeout"],
        )
    except Exception as e:
        logger.error(f"Failed to create database pool: {e}")
        return False
    
    # 3️⃣ FORCE EVENT LOOP YIELD (CRITICAL — DO NOT SKIP)
    await asyncio.sleep(0)
    
    # 4️⃣ ONLY AFTER yield — RUN MIGRATIONS
    try:
        import migrations
        migrations_success = await migrations.run_migrations_safe(_pool)
        if not migrations_success:
            logger.error("Migration execution failed")
            return False
        logger.info("Database migrations applied successfully")
    except Exception as e:
        logger.error(f"Migration execution failed: {e}")
        return False

    # 4b️⃣ RECREATE POOL after migrations (asyncpg prepared statement cache fix)
    # Schema changes can invalidate cached prepared statements; fresh pool clears cache.
    try:
        await _pool.close()
        _pool = await asyncpg.create_pool(DATABASE_URL, **pool_config)
        logger.info(
            "DB_POOL_RECREATED_AFTER_MIGRATIONS min=%s max=%s acquire_timeout=%s command_timeout=%s",
            pool_config["min_size"], pool_config["max_size"],
            pool_config["timeout"], pool_config["command_timeout"],
        )
    except Exception as e:
        logger.error(f"Failed to recreate pool after migrations: {e}")
        return False

    # 5️⃣ IF migrations_success IS FALSE → already returned False above
    # Now proceed with table creation (pool.acquire() is safe after yield)
    # STRICT PATTERN: async with pool.acquire() as conn
    async with _pool.acquire() as conn:
        # ── Fast-fail on schema locks ──────────────────────────────────
        # Migrations 001–053 have already created every table and column
        # below. The 100+ `CREATE TABLE / ALTER TABLE … IF NOT EXISTS`
        # statements that follow are idempotent legacy fallbacks for
        # bootstrapping a virgin DB. Each one still asks Postgres for
        # ACCESS EXCLUSIVE LOCK on its table — and on a 350k-user prod
        # base, if even one transaction (autovacuum, idle-in-tx) holds
        # a conflicting lock, the ALTER blocks indefinitely. Once asyncpg's
        # client-side command_timeout (30s) fires it CANCELS the query
        # and releases the connection back to the pool — the next
        # `conn.execute()` then crashes with InterfaceError.
        #
        # Server-side lock_timeout/statement_timeout make any stuck
        # statement fail as a normal query error in a few seconds,
        # caught by the surrounding try/except and the loop moves on.
        # Connection stays healthy.
        try:
            await conn.execute("SET lock_timeout = '5s'")
            await conn.execute("SET statement_timeout = '20s'")
        except Exception as e:
            logger.warning("Failed to set statement/lock timeouts: %s", e)

        # ── Легаси-бутстрап схемы: по умолчанию ВЫКЛЮЧЕН ──────────────
        #
        # Сам DDL живёт в database/legacy_schema.py — ~640 строк
        # императивного CREATE TABLE / ALTER TABLE / CREATE INDEX с
        # IF NOT EXISTS. Они выполнялись при каждом старте
        # бота и просили у Postgres ACCESS EXCLUSIVE на users, subscriptions,
        # payments и остальные ключевые таблицы. На боевой базе это значит:
        # одна висящая idle-in-transaction сессия или работающий autovacuum —
        # и ALTER встаёт в очередь, а за ним встают все читающие запросы.
        # Спасал только lock_timeout=5s, после которого ошибка молча
        # проглатывалась (except Exception: pass) — то есть на девственной
        # базе колонка могла не создаться, и никто бы не узнал.
        #
        # Схема теперь живёт в одном месте — каталоге migrations/. Всё, что
        # раньше существовало только здесь (две таблицы и 26 колонок),
        # перенесено в migrations/073_schema_source_of_truth.sql.
        #
        # Блок оставлен как аварийный бутстрап для случая, когда миграции
        # применить нельзя (локальная разработка на пустой базе, ручное
        # восстановление). Включается переменной окружения:
        #     LEGACY_SCHEMA_BOOTSTRAP=1
        # Добавляя новую таблицу или колонку, пишите миграцию — ни сюда,
        # ни в legacy_schema.py НЕ надо.
        #
        # Соединение передаём своё, уже с lock_timeout/statement_timeout
        # (выставлены выше): без них любой ALTER может встать намертво.
        _legacy_bootstrap = os.getenv("LEGACY_SCHEMA_BOOTSTRAP", "").strip().lower() in ("1", "true", "yes")
        if _legacy_bootstrap:
            logger.warning(
                "LEGACY_SCHEMA_BOOTSTRAP=1 — выполняется устаревший DDL из кода. "
                "Это блокирует таблицы; на бою источник схемы — migrations/."
            )
            await apply_legacy_schema_bootstrap(conn)

        logger.info("Database tables initialized")
        
        # ====================================================================================
        # КРИТИЧНО: Проверяем существование всех критичных таблиц после миграций
        # ====================================================================================
        # Если миграции упали частично, таблицы могут отсутствовать
        # Это предотвращает установку DB_READY = True при частично сломанной БД
        required_tables = [
            "users",
            "subscriptions",
            "pending_purchases",
            "payments",
            "balance_transactions"
        ]
        
        missing_tables = []
        for table_name in required_tables:
            table_exists = await conn.fetchval(
                "SELECT to_regclass($1::text)",
                f"public.{table_name}"
            )
            if table_exists is None:
                missing_tables.append(table_name)
        
        if missing_tables:
            logger.error(f"CRITICAL: Required tables are missing after migrations: {missing_tables}")
            logger.error("Database is in BROKEN state - migrations may have failed partially")
            DB_READY = False
            return False
        
        # ====================================================================================
        # КРИТИЧНО: Проверяем что таблица users существует и доступна
        # ====================================================================================
        # users - базовая таблица, без неё БД не может считаться готовой
        users_exists = await conn.fetchval("SELECT to_regclass('public.users')")
        if users_exists is None:
            logger.error("CRITICAL: Table 'users' does not exist after migrations")
            logger.error("This is a critical failure - users table is required for all operations")
            DB_READY = False
            return False
        
        # Проверяем что users таблица имеет базовую структуру
        try:
            users_count = await conn.fetchval("SELECT COUNT(*) FROM users")
            logger.info(f"Users table verified: {users_count} users found")
        except Exception as e:
            logger.error(f"CRITICAL: Cannot query users table: {e}")
            DB_READY = False
            return False
        
        # Логируем информацию о БД для диагностики
        try:
            db_name = await conn.fetchval("SELECT current_database()")
            db_user = await conn.fetchval("SELECT current_user")
            db_schema = await conn.fetchval("SELECT current_schema()")
            logger.info(f"Database connection verified: database={db_name}, user={db_user}, schema={db_schema}")
        except Exception as e:
            logger.warning(f"Could not log database info: {e}")
        
        # 6️⃣ IF SUCCESS: set DB_READY = True and log
        # ТОЛЬКО ПОСЛЕ ПРОВЕРКИ ВСЕХ ТАБЛИЦ И users устанавливаем DB_READY = True
        DB_READY = True
        logger.info("Database fully initialized")
        
        # SystemState recalculation removed - no longer needed
        
        return True


