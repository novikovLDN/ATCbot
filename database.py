import asyncpg
import asyncio
import os
import sys
import hashlib
import base64
import uuid as uuid_lib
import random
import string
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, Tuple, TYPE_CHECKING, List
import logging
import config
import vpn_utils
from app.utils.retry import retry_async
from app.core.recovery_cooldown import (
    get_recovery_cooldown,
    ComponentName,
)
from app.core.system_state import ComponentStatus
from app.core.metrics import get_metrics, timer
from app.core.cost_model import get_cost_model, CostCenter
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
# UTC HELPERS: DB boundary — TIMESTAMP WITHOUT TIME ZONE requires naive UTC
# ====================================================================================
# PostgreSQL schema uses TIMESTAMP (without time zone). asyncpg expects naive datetime
# for these columns. Application layer uses timezone-aware UTC.
# STRICT RULE: All datetime passed TO asyncpg → _to_db_utc. All datetime read FROM DB → _from_db_utc.
# ====================================================================================

def _to_db_utc(dt: datetime) -> datetime:
    """
    Convert aware UTC datetime to naive UTC for DB storage.
    Must raise if dt is not timezone-aware UTC.
    """
    if dt is None:
        return None
    assert dt.tzinfo == timezone.utc, f"Expected UTC, got tzinfo={dt.tzinfo}"
    return dt.replace(tzinfo=None)


def _from_db_utc(dt: datetime) -> datetime:
    """
    Convert naive DB datetime to aware UTC.
    DB TIMESTAMP columns return naive datetime (stored as UTC).
    """
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc)
    return dt.replace(tzinfo=timezone.utc)


def _generate_subscription_uuid() -> str:
    """Canonical subscription UUID generation. DB is source of truth. Single place for new UUIDs."""
    u = str(uuid_lib.uuid4())
    assert u, "UUID generation failed: empty"
    assert len(u) >= 32, f"UUID generation failed: invalid length {len(u)}"
    return u


def _ensure_utc(dt: datetime) -> datetime:
    """Ensure datetime is timezone-aware UTC. Naive assumed UTC. Other TZ converted. Use _from_db_utc for DB reads."""
    if dt is None:
        return None
    if dt.tzinfo is not None and dt.tzinfo == timezone.utc:
        return dt
    if dt.tzinfo is None:
        return datetime(
            dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second, dt.microsecond,
            tzinfo=timezone.utc
        )
    return dt.astimezone(timezone.utc)


def _normalize_subscription_row(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Convert naive DB datetime columns to aware UTC. Use when returning subscription dicts."""
    if row is None:
        return None
    d = dict(row)
    for k in ("expires_at", "trial_expires_at", "created_at", "activated_at", "last_reminder_at",
              "last_auto_renewal_at", "last_notification_sent_at", "first_traffic_at"):
        if k in d and d[k] is not None and isinstance(d[k], datetime):
            d[k] = _from_db_utc(d[k])
    return d


# ====================================================================================
# SAFE DATA HELPERS: Утилиты для безопасной обработки NULL значений
# ====================================================================================

def safe_int(value: Any) -> int:
    """
    Безопасное преобразование значения в int с обработкой None
    
    Args:
        value: Значение для преобразования (может быть None, int, str, Decimal)
    
    Returns:
        int: Преобразованное значение или 0 если None
    """
    if value is None:
        return 0
    try:
        return int(value)
    except (ValueError, TypeError):
        return 0


def safe_float(value: Any) -> float:
    """
    Безопасное преобразование значения в float с обработкой None
    
    Args:
        value: Значение для преобразования (может быть None, int, float, str, Decimal)
    
    Returns:
        float: Преобразованное значение или 0.0 если None
    """
    if value is None:
        return 0.0
    try:
        return float(value)
    except (ValueError, TypeError):
        return 0.0


def safe_get(dictionary: Dict[str, Any], key: str, default: Any = None) -> Any:
    """
    Безопасное получение значения из словаря с обработкой отсутствующих ключей
    
    Args:
        dictionary: Словарь
        key: Ключ
        default: Значение по умолчанию
    
    Returns:
        Значение из словаря или default
    """
    if dictionary is None:
        return default
    return dictionary.get(key, default)


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
        # Retry pool.acquire() on transient database errors only
        conn = await retry_async(
            lambda: pool.acquire(),
            retries=2,
            base_delay=0.5,
            max_delay=2.0,
            retry_on=(asyncpg.PostgresError,)
        )
        async with conn as new_conn:
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
        # Retry pool.acquire() on transient database errors only
        conn = await retry_async(
            lambda: pool.acquire(),
            retries=2,
            base_delay=0.5,
            max_delay=2.0,
            retry_on=(asyncpg.PostgresError,)
        )
        async with conn as new_conn:
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
    """Build asyncpg.create_pool kwargs. Single source of truth for all pool creation."""
    return {
        "min_size": int(os.getenv("DB_POOL_MIN_SIZE", "2")),
        "max_size": int(os.getenv("DB_POOL_MAX_SIZE", "15")),
        "max_inactive_connection_lifetime": 300,
        "timeout": int(os.getenv("DB_POOL_ACQUIRE_TIMEOUT", "10")),
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
        # B4.3 - SAFE RETRY RE-ENABLE: Check cooldown before retrying pool creation
        from datetime import datetime, timezone
        recovery_cooldown = get_recovery_cooldown(cooldown_seconds=60)
        now = datetime.now(timezone.utc)
        
        # If database is in cooldown, use minimal retries
        if recovery_cooldown.is_in_cooldown(ComponentName.DATABASE, now):
            retries = 0  # No retries during cooldown
        else:
            retries = 1  # Normal retry behavior
        
        # C1.1 - METRICS: Measure pool creation latency
        pool_config = _get_pool_config()
        with timer("db_latency_ms"):
            # Retry pool creation on transient errors only
            _pool = await retry_async(
                lambda: asyncpg.create_pool(DATABASE_URL, **pool_config),
                retries=retries,
                base_delay=0.5,
                max_delay=5.0,
                retry_on=(asyncpg.PostgresError,)
            )
        
        # C1.1 - METRICS: Track retries
        if retries > 0:
            metrics = get_metrics()
            metrics.increment_counter("retries_total", value=retries)
        
        # D2.1 - COST CENTERS: Track DB connection cost
        cost_model = get_cost_model()
        cost_model.record_cost(CostCenter.DB_CONNECTIONS, cost_units=1.0)
        
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
        # Таблица users
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT UNIQUE NOT NULL,
                username TEXT,
                language TEXT DEFAULT 'ru',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Миграция: добавляем referral_level, если его нет
        try:
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_level TEXT DEFAULT 'base' CHECK (referral_level IN ('base', 'vip'))")
        except Exception:
            pass
        
        # Таблица pending_purchases - контекст покупки для защиты от устаревших кнопок
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS pending_purchases (
                id SERIAL PRIMARY KEY,
                purchase_id TEXT UNIQUE NOT NULL,
                telegram_id BIGINT NOT NULL,
                tariff TEXT NOT NULL CHECK (tariff IN ('basic', 'plus')),
                period_days INTEGER NOT NULL,
                price_kopecks INTEGER NOT NULL,
                promo_code TEXT,
                status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'paid', 'expired')),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP
            )
        """)
        
        # Миграция: устанавливаем expires_at для существующих pending purchases с NULL expires_at
        try:
            await conn.execute("""
                UPDATE pending_purchases 
                SET expires_at = created_at + INTERVAL '30 minutes'
                WHERE expires_at IS NULL
                AND status = 'pending'
            """)
        except Exception:
            pass
        
        # Создаем индексы для быстрого поиска
        try:
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_pending_purchases_status ON pending_purchases(status)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_pending_purchases_telegram_id ON pending_purchases(telegram_id)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_pending_purchases_purchase_id ON pending_purchases(purchase_id)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_pending_purchases_expires_at ON pending_purchases(expires_at)")
        except Exception:
            # Индексы уже существуют
            pass
        
        # Таблица payments
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT NOT NULL,
                tariff TEXT NOT NULL,
                amount INTEGER,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                purchase_id TEXT
            )
        """)
        
        # P0 HOTFIX: Ensure idempotency columns exist (migration 012 compatibility)
        # These columns are added by migration 012, but if table is recreated,
        # we need to add them here to prevent schema drift
        try:
            await conn.execute("""
                ALTER TABLE payments
                ADD COLUMN IF NOT EXISTS telegram_payment_charge_id TEXT
            """)
            await conn.execute("""
                ALTER TABLE payments
                ADD COLUMN IF NOT EXISTS cryptobot_payment_id TEXT
            """)
        except Exception:
            # Columns may already exist or migration handles this
            pass
        
        # Таблица subscriptions
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT UNIQUE NOT NULL,
                outline_key_id INTEGER,
                vpn_key TEXT,
                expires_at TIMESTAMP NOT NULL,
                reminder_sent BOOLEAN DEFAULT FALSE,
                reminder_3d_sent BOOLEAN DEFAULT FALSE,
                reminder_24h_sent BOOLEAN DEFAULT FALSE,
                reminder_3h_sent BOOLEAN DEFAULT FALSE,
                reminder_6h_sent BOOLEAN DEFAULT FALSE,
                admin_grant_days INTEGER DEFAULT NULL,
                auto_renew BOOLEAN DEFAULT FALSE
            )
        """)
        
        # Миграция: добавляем auto_renew, если его нет
        try:
            await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS auto_renew BOOLEAN DEFAULT FALSE")
        except Exception:
            pass
        
        # Миграция: добавляем поле для защиты от повторного автопродления
        try:
            await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS last_auto_renewal_at TIMESTAMP")
        except Exception:
            pass
        
        # Миграция: добавляем last_notification_sent_at для автопродления
        try:
            await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS last_notification_sent_at TIMESTAMP")
        except Exception:
            pass
        
        # Миграция: добавляем новые поля для напоминаний, если их нет
        try:
            await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS reminder_3d_sent BOOLEAN DEFAULT FALSE")
            await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS reminder_24h_sent BOOLEAN DEFAULT FALSE")
            await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS outline_key_id INTEGER")
            # Делаем vpn_key nullable для поддержки старых записей
            await conn.execute("ALTER TABLE subscriptions ALTER COLUMN vpn_key DROP NOT NULL")
            await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS reminder_3h_sent BOOLEAN DEFAULT FALSE")
            await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS reminder_6h_sent BOOLEAN DEFAULT FALSE")
            
            # Trial notification flags (без миграции - используем существующую структуру)
            await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS trial_notif_6h_sent BOOLEAN DEFAULT FALSE")
            await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS trial_notif_18h_sent BOOLEAN DEFAULT FALSE")
            await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS trial_notif_30h_sent BOOLEAN DEFAULT FALSE")
            await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS trial_notif_42h_sent BOOLEAN DEFAULT FALSE")
            await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS trial_notif_54h_sent BOOLEAN DEFAULT FALSE")
            await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS trial_notif_60h_sent BOOLEAN DEFAULT FALSE")
            await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS trial_notif_71h_sent BOOLEAN DEFAULT FALSE")
            await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS admin_grant_days INTEGER DEFAULT NULL")
            # Поля для умных уведомлений
            await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS activated_at TIMESTAMP")
            await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS last_bytes BIGINT DEFAULT 0")
            await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS first_traffic_at TIMESTAMP")
            await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS smart_notif_no_traffic_20m_sent BOOLEAN DEFAULT FALSE")
            await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS smart_notif_no_traffic_24h_sent BOOLEAN DEFAULT FALSE")
            await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS smart_notif_first_connection_sent BOOLEAN DEFAULT FALSE")
            await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS smart_notif_3days_usage_sent BOOLEAN DEFAULT FALSE")
            await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS smart_notif_7days_before_expiry_sent BOOLEAN DEFAULT FALSE")
            await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS smart_notif_expiry_day_sent BOOLEAN DEFAULT FALSE")
            await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS smart_notif_expired_24h_sent BOOLEAN DEFAULT FALSE")
            await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS smart_notif_vip_offer_sent BOOLEAN DEFAULT FALSE")
            # Поле для anti-spam защиты (минимальный интервал между уведомлениями)
            await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS last_notification_sent_at TIMESTAMP")
            
            # Xray Core migration: добавляем uuid, status, source для VLESS
            await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS uuid TEXT")
            await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'active'")
            await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'payment'")
        except Exception:
            # Колонки уже существуют
            pass
        
        # Миграция: добавляем поле notification_sent в payments для идемпотентности уведомлений
        try:
            await conn.execute("ALTER TABLE payments ADD COLUMN IF NOT EXISTS notification_sent BOOLEAN DEFAULT FALSE")
        except Exception:
            pass
        
        # Миграция: добавляем поля для delayed activation (premium flow)
        try:
            await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS activation_status TEXT DEFAULT 'active'")
            await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS activation_attempts INTEGER DEFAULT 0")
            await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS last_activation_error TEXT")
        except Exception:
            pass
        
        # Миграция: добавляем поле balance в users (хранится в копейках как INTEGER)
        try:
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS balance INTEGER NOT NULL DEFAULT 0")
        except Exception:
            pass
        
        # Trial usage tracking (без миграций - используем ALTER TABLE IF NOT EXISTS)
        try:
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS trial_used_at TIMESTAMP")
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS trial_expires_at TIMESTAMP")
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS trial_completed_sent BOOLEAN DEFAULT FALSE")
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS smart_offer_sent BOOLEAN DEFAULT FALSE")
        except Exception:
            pass
            # Если колонка уже существует как NUMERIC, конвертируем в INTEGER (копейки)
            # Это безопасно, так как мы всегда работаем с копейками
        except Exception:
            # Колонка уже существует
            pass
        
        # Таблица balance_transactions
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS balance_transactions (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                amount NUMERIC NOT NULL,
                type TEXT NOT NULL,
                source TEXT,
                description TEXT,
                related_user_id BIGINT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Миграция: добавляем related_user_id, если его нет
        try:
            await conn.execute("ALTER TABLE balance_transactions ADD COLUMN IF NOT EXISTS related_user_id BIGINT")
        except Exception:
            pass
        
        # Миграция: добавляем поле source в balance_transactions, если его нет
        try:
            await conn.execute("ALTER TABLE balance_transactions ADD COLUMN IF NOT EXISTS source TEXT")
            # Меняем тип amount на NUMERIC для точности
            await conn.execute("ALTER TABLE balance_transactions ALTER COLUMN amount TYPE NUMERIC USING amount::NUMERIC")
        except Exception:
            # Колонка уже существует или ошибка миграции
            pass
        
        # Миграция: добавляем поля для реферальной программы
        try:
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_code TEXT")
            # Добавляем referrer_id (или referred_by для обратной совместимости)
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS referrer_id BIGINT")
            # Если есть referred_by, но нет referrer_id - копируем данные
            await conn.execute("""
                UPDATE users 
                SET referrer_id = referred_by 
                WHERE referrer_id IS NULL AND referred_by IS NOT NULL
            """)
            # Создаем индекс для быстрого поиска по referral_code
            await conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_referral_code ON users(referral_code) WHERE referral_code IS NOT NULL")
            # Создаем индекс для быстрого поиска по referrer_id
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_users_referrer_id ON users(referrer_id) WHERE referrer_id IS NOT NULL")
        except Exception:
            # Колонки уже существуют
            pass
        
        # Таблица referrals (партнёрская программа)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS referrals (
                id SERIAL PRIMARY KEY,
                referrer_user_id BIGINT NOT NULL,
                referred_user_id BIGINT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_rewarded BOOLEAN DEFAULT FALSE,
                reward_amount INTEGER DEFAULT 0,
                UNIQUE (referred_user_id)
            )
        """)
        
        # Создаём индекс для быстрого поиска по партнёру
        try:
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_referrals_referrer ON referrals(referrer_user_id)")
        except Exception:
            pass
        
        # Миграция: переименовываем колонки, если они еще старые
        try:
            await conn.execute("ALTER TABLE referrals RENAME COLUMN referrer_id TO referrer_user_id")
        except Exception:
            pass
        try:
            await conn.execute("ALTER TABLE referrals RENAME COLUMN referred_id TO referred_user_id")
        except Exception:
            pass
        try:
            await conn.execute("ALTER TABLE referrals RENAME COLUMN rewarded TO is_rewarded")
        except Exception:
            pass
        try:
            await conn.execute("ALTER TABLE referrals ADD COLUMN IF NOT EXISTS reward_amount INTEGER DEFAULT 0")
        except Exception:
            pass
        try:
            await conn.execute("ALTER TABLE referrals ADD COLUMN IF NOT EXISTS first_paid_at TIMESTAMP")
        except Exception:
            pass
        
        # Таблица referral_rewards - история всех начислений реферального кешбэка
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS referral_rewards (
                id SERIAL PRIMARY KEY,
                referrer_id BIGINT NOT NULL,
                buyer_id BIGINT NOT NULL,
                purchase_id TEXT,
                purchase_amount INTEGER NOT NULL,
                percent INTEGER NOT NULL,
                reward_amount INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Создаём индексы для быстрого поиска
        try:
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_referral_rewards_referrer ON referral_rewards(referrer_id)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_referral_rewards_buyer ON referral_rewards(buyer_id)")
            # Частичный уникальный индекс для предотвращения дубликатов начислений по одному purchase_id
            await conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_referral_rewards_unique_buyer_purchase ON referral_rewards(buyer_id, purchase_id) WHERE purchase_id IS NOT NULL")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_referral_rewards_purchase_id ON referral_rewards(purchase_id) WHERE purchase_id IS NOT NULL")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_referral_rewards_created_at ON referral_rewards(created_at)")
        except Exception:
            pass
        
        # Таблица vpn_keys
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS vpn_keys (
                id SERIAL PRIMARY KEY,
                vpn_key TEXT UNIQUE NOT NULL,
                is_used BOOLEAN DEFAULT FALSE,
                assigned_to BIGINT,
                assigned_at TIMESTAMP
            )
        """)
        
        # Таблица audit_log
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id SERIAL PRIMARY KEY,
                action TEXT NOT NULL,
                telegram_id BIGINT NOT NULL,
                target_user BIGINT,
                details TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Миграция: добавляем колонки для VPN lifecycle audit (если их нет)
        try:
            await conn.execute("ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS uuid TEXT")
            await conn.execute("ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS source TEXT")
            await conn.execute("ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS result TEXT CHECK (result IN ('success', 'error'))")
            # STEP 5 — PART C: CORRELATION & TRACEABILITY
            # Add correlation_id column for traceability
            await conn.execute("ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS correlation_id TEXT")
            # Создаём индекс для быстрого поиска по UUID
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_log_uuid ON audit_log(uuid) WHERE uuid IS NOT NULL")
            # Создаём индекс для быстрого поиска по action
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_log_action ON audit_log(action)")
            # Создаём индекс для быстрого поиска по source
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_log_source ON audit_log(source) WHERE source IS NOT NULL")
            # STEP 5 — PART C: CORRELATION & TRACEABILITY
            # Index for correlation_id for fast incident timeline reconstruction
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_log_correlation_id ON audit_log(correlation_id) WHERE correlation_id IS NOT NULL")
        except Exception:
            # Колонки уже существуют
            pass
        
        # Таблица subscription_history
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS subscription_history (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT NOT NULL,
                vpn_key TEXT NOT NULL,
                start_date TIMESTAMP NOT NULL,
                end_date TIMESTAMP NOT NULL,
                action_type TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Таблица broadcasts
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS broadcasts (
                id SERIAL PRIMARY KEY,
                title TEXT NOT NULL,
                message TEXT,
                message_a TEXT,
                message_b TEXT,
                is_ab_test BOOLEAN DEFAULT FALSE,
                type TEXT NOT NULL,
                segment TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                sent_by BIGINT NOT NULL
            )
        """)
        
        # Добавляем колонки для миграции
        try:
            await conn.execute("ALTER TABLE broadcasts ADD COLUMN IF NOT EXISTS segment TEXT")
            await conn.execute("ALTER TABLE broadcasts ADD COLUMN IF NOT EXISTS is_ab_test BOOLEAN DEFAULT FALSE")
            await conn.execute("ALTER TABLE broadcasts ADD COLUMN IF NOT EXISTS message_a TEXT")
            await conn.execute("ALTER TABLE broadcasts ADD COLUMN IF NOT EXISTS message_b TEXT")
        except Exception:
            # Колонки уже существуют или таблицы нет
            pass
        
        # Таблица broadcast_log
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS broadcast_log (
                id SERIAL PRIMARY KEY,
                broadcast_id INTEGER NOT NULL REFERENCES broadcasts(id) ON DELETE CASCADE,
                telegram_id BIGINT NOT NULL,
                status TEXT NOT NULL,
                variant TEXT,
                sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Добавляем колонку variant для миграции
        try:
            await conn.execute("ALTER TABLE broadcast_log ADD COLUMN IF NOT EXISTS variant TEXT")
        except Exception:
            # Колонка уже существует или таблицы нет
            pass

        # Таблица incident_settings (режим инцидента)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS incident_settings (
                id SERIAL PRIMARY KEY,
                is_active BOOLEAN DEFAULT FALSE,
                incident_text TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Таблица user_discounts (персональные скидки)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS user_discounts (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT UNIQUE NOT NULL,
                discount_percent INTEGER NOT NULL,
                expires_at TIMESTAMP NULL,
                created_by BIGINT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Таблица vip_users (VIP-статус)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS vip_users (
                telegram_id BIGINT UNIQUE NOT NULL PRIMARY KEY,
                granted_by BIGINT NOT NULL,
                granted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Таблица promo_codes (промокоды)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS promo_codes (
                code TEXT UNIQUE NOT NULL PRIMARY KEY,
                discount_percent INTEGER NOT NULL,
                max_uses INTEGER NULL,
                used_count INTEGER DEFAULT 0,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Таблица promo_usage_logs (логи использования промокодов)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS promo_usage_logs (
                id SERIAL PRIMARY KEY,
                promo_code TEXT NOT NULL,
                telegram_id BIGINT NOT NULL,
                tariff TEXT NOT NULL,
                discount_percent INTEGER NOT NULL,
                price_before INTEGER NOT NULL,
                price_after INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Создаём одну строку, если её нет
        existing = await conn.fetchval("SELECT COUNT(*) FROM incident_settings")
        if existing == 0:
            await conn.execute("""
                INSERT INTO incident_settings (is_active, incident_text)
                VALUES (FALSE, NULL)
            """)
        
        # Инициализируем промокоды, если их нет
        await _init_promo_codes(conn)
        
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
        
        # PART C.3: After successful init_db(), explicitly call SystemState.recalculate()
        # PART E.6: Explicit logging on SystemState update (REQUIRED)
        try:
            from app.core.system_state import recalculate_from_runtime
            system_state = recalculate_from_runtime()
            
            # PART E.6: Explicit logging format: "SystemState updated: DEGRADED (db=healthy, payments=healthy, vpn_api=degraded)"
            state_str = "HEALTHY" if system_state.is_healthy else ("DEGRADED" if system_state.is_degraded else "UNAVAILABLE")
            logger.info(
                f"SystemState updated: {state_str} "
                f"(db={system_state.database.status.value}, "
                f"payments={system_state.payments.status.value}, "
                f"vpn_api={system_state.vpn_api.status.value})"
            )
        except Exception as e:
            # SystemState recalculation must not break init_db()
            logger.debug(f"Error recalculating SystemState: {e}")
        
        return True


async def _init_promo_codes(conn):
    """Инициализация промокодов в базе данных"""
    # Check if promo_codes has id/deleted_at (post-021 schema)
    has_id = await conn.fetchval(
        "SELECT 1 FROM information_schema.columns WHERE table_name = 'promo_codes' AND column_name = 'id'"
    )
    has_deleted_at = await conn.fetchval(
        "SELECT 1 FROM information_schema.columns WHERE table_name = 'promo_codes' AND column_name = 'deleted_at'"
    )
    use_new_schema = bool(has_id and has_deleted_at)

    # 1. Деактивируем устаревший промокод
    if use_new_schema:
        await conn.execute("""
            UPDATE promo_codes
            SET is_active = FALSE, deleted_at = NOW()
            WHERE UPPER(code) = 'COURIER40' AND (deleted_at IS NULL OR is_active = TRUE)
        """)
    else:
        await conn.execute("""
            UPDATE promo_codes SET is_active = FALSE WHERE code = 'COURIER40'
        """)

    # 2. Добавляем актуальные промокоды
    if use_new_schema:
        # Partial unique index: ON CONFLICT (code) WHERE is_active AND deleted_at IS NULL
        for row in [
            ("ELVIRA064", 64, 50),
            ("YAbx30", 30, None),
            ("FAM50", 50, 50),
            ("COURIER30", 30, 40),
        ]:
            code, discount, max_uses = row
            await conn.execute("""
                INSERT INTO promo_codes (code, discount_percent, max_uses, is_active, deleted_at)
                VALUES ($1, $2, $3, TRUE, NULL)
                ON CONFLICT (code) WHERE (is_active = true AND deleted_at IS NULL)
                DO UPDATE SET discount_percent = EXCLUDED.discount_percent, max_uses = EXCLUDED.max_uses
            """, code, discount, max_uses)
    else:
        await conn.execute("""
            INSERT INTO promo_codes (code, discount_percent, max_uses, is_active)
            VALUES
                ('ELVIRA064', 64, 50, TRUE),
                ('YAbx30', 30, NULL, TRUE),
                ('FAM50', 50, 50, TRUE),
                ('COURIER30', 30, 40, TRUE)
            ON CONFLICT (code) DO UPDATE SET
                discount_percent = EXCLUDED.discount_percent,
                max_uses = EXCLUDED.max_uses,
                is_active = EXCLUDED.is_active
        """)


async def get_user(telegram_id: int) -> Optional[Dict[str, Any]]:
    """Получить пользователя по Telegram ID"""
    # Защита от работы с неинициализированной БД
    if not DB_READY:
        logger.warning("DB not ready, get_user skipped")
        return None
    pool = await get_pool()
    if pool is None:
        logger.warning("Pool is None, get_user skipped")
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM users WHERE telegram_id = $1", telegram_id
        )
        return dict(row) if row else None


async def get_user_balance(telegram_id: int) -> float:
    """
    Получить баланс пользователя в рублях
    
    Args:
        telegram_id: Telegram ID пользователя
    
    Returns:
        Баланс в рублях (0.0 если пользователь не найден)
    """
    from decimal import Decimal
    # Защита от работы с неинициализированной БД
    if not DB_READY:
        logger.warning("DB not ready, get_user_balance skipped")
        return 0.0
    pool = await get_pool()
    if pool is None:
        logger.warning("Pool is None, get_user_balance skipped")
        return 0.0
    async with pool.acquire() as conn:
        balance = await conn.fetchval(
            "SELECT balance FROM users WHERE telegram_id = $1", telegram_id
        )
        if balance is None:
            return 0.0
        # Конвертируем из копеек в рубли
        if isinstance(balance, (int, Decimal)):
            return float(balance) / 100.0
        return float(balance) if balance else 0.0


async def increase_balance(telegram_id: int, amount: float, source: str = "telegram_payment", description: Optional[str] = None) -> bool:
    """
    Увеличить баланс пользователя (атомарно)
    
    Args:
        telegram_id: Telegram ID пользователя
        amount: Сумма в рублях (положительное число)
        source: Источник пополнения ('telegram_payment', 'admin', 'referral')
        description: Описание транзакции
    
    Returns:
        True если успешно, False при ошибке
    """
    if amount <= 0:
        logger.error(f"Invalid amount for increase_balance: {amount}")
        return False
    
    # Конвертируем рубли в копейки для хранения
    amount_kopecks = int(amount * 100)
    
    # Защита от работы с неинициализированной БД
    if not DB_READY:
        logger.warning("DB not ready, increase_balance skipped")
        return False
    pool = await get_pool()
    if pool is None:
        logger.warning("Pool is None, increase_balance skipped")
        return False
    async with pool.acquire() as conn:
        async with conn.transaction():
            try:
                # CRITICAL: advisory lock per user для защиты от race conditions
                await conn.execute(
                    "SELECT pg_advisory_xact_lock($1)",
                    telegram_id
                )
                
                # Обновляем баланс
                await conn.execute(
                    "UPDATE users SET balance = balance + $1 WHERE telegram_id = $2",
                    amount_kopecks, telegram_id
                )
                
                # Определяем тип транзакции на основе source
                transaction_type = "topup"
                if source == "referral" or source == "referral_reward":
                    transaction_type = "cashback"
                elif source == "admin" or source == "admin_adjustment":
                    transaction_type = "admin_adjustment"
                
                # Записываем транзакцию
                await conn.execute(
                    """INSERT INTO balance_transactions (user_id, amount, type, source, description)
                       VALUES ($1, $2, $3, $4, $5)""",
                    telegram_id, amount_kopecks, transaction_type, source, description
                )
                
                # Structured logging
                logger.info(
                    f"BALANCE_INCREASED user={telegram_id} amount={amount:.2f} RUB "
                    f"({amount_kopecks} kopecks) source={source}"
                )
                return True
            except Exception as e:
                logger.exception(f"Error increasing balance for user {telegram_id}")
                return False


async def decrease_balance(telegram_id: int, amount: float, source: str = "subscription_payment", description: Optional[str] = None) -> bool:
    """
    Уменьшить баланс пользователя (атомарно)
    
    Args:
        telegram_id: Telegram ID пользователя
        amount: Сумма в рублях (положительное число)
        source: Источник списания ('subscription_payment', 'admin', 'refund')
        description: Описание транзакции
    
    Returns:
        True если успешно, False при ошибке или недостатке средств
    """
    if amount <= 0:
        logger.error(f"Invalid amount for decrease_balance: {amount}")
        return False
    
    # Конвертируем рубли в копейки для хранения
    amount_kopecks = int(amount * 100)
    
    # Защита от работы с неинициализированной БД
    if not DB_READY:
        logger.warning("DB not ready, decrease_balance skipped")
        return False
    pool = await get_pool()
    if pool is None:
        logger.warning("Pool is None, decrease_balance skipped")
        return False
    async with pool.acquire() as conn:
        async with conn.transaction():
            try:
                # CRITICAL: advisory lock per user для защиты от race conditions
                await conn.execute(
                    "SELECT pg_advisory_xact_lock($1)",
                    telegram_id
                )
                
                # SELECT FOR UPDATE для блокировки строки до конца транзакции
                row = await conn.fetchrow(
                    "SELECT balance FROM users WHERE telegram_id = $1 FOR UPDATE",
                    telegram_id
                )
                
                if not row:
                    logger.error(f"User {telegram_id} not found")
                    return False
                
                current_balance = row["balance"]
                
                if current_balance < amount_kopecks:
                    logger.warning(f"Insufficient balance for user {telegram_id}: {current_balance} < {amount_kopecks}")
                    return False
                
                # Обновляем баланс
                new_balance = current_balance - amount_kopecks
                await conn.execute(
                    "UPDATE users SET balance = $1 WHERE telegram_id = $2",
                    new_balance, telegram_id
                )
                
                # Определяем тип транзакции на основе source
                transaction_type = "subscription_payment"
                if source == "admin" or source == "admin_adjustment":
                    transaction_type = "admin_adjustment"
                elif source == "auto_renew":
                    transaction_type = "subscription_payment"  # Автопродление - это тоже оплата подписки
                elif source == "refund":
                    transaction_type = "topup"  # Возврат средств
                
                # Записываем транзакцию (amount отрицательный для списания)
                await conn.execute(
                    """INSERT INTO balance_transactions (user_id, amount, type, source, description)
                       VALUES ($1, $2, $3, $4, $5)""",
                    telegram_id, -amount_kopecks, transaction_type, source, description
                )
                
                # Structured logging
                logger.info(
                    f"BALANCE_DECREASED user={telegram_id} amount={amount:.2f} RUB "
                    f"({amount_kopecks} kopecks) source={source}"
                )
                return True
            except Exception as e:
                logger.exception(f"Error decreasing balance for user {telegram_id}")
                return False


async def log_balance_transaction(telegram_id: int, amount: float, transaction_type: str, source: Optional[str] = None, description: Optional[str] = None) -> bool:
    """
    Записать транзакцию баланса (без изменения баланса)
    
    Args:
        telegram_id: Telegram ID пользователя
        amount: Сумма в рублях (может быть отрицательной)
        transaction_type: Тип транзакции ('topup', 'subscription_payment', 'refund', 'bonus')
        source: Источник транзакции
        description: Описание транзакции
    
    Returns:
        True если успешно, False при ошибке
    """
    amount_kopecks = int(amount * 100)
    
    # Защита от работы с неинициализированной БД
    if not DB_READY:
        logger.warning("DB not ready, log_balance_transaction skipped")
        return False
    pool = await get_pool()
    if pool is None:
        logger.warning("Pool is None, log_balance_transaction skipped")
        return False
    async with pool.acquire() as conn:
        try:
            await conn.execute(
                """INSERT INTO balance_transactions (user_id, amount, type, source, description)
                   VALUES ($1, $2, $3, $4, $5)""",
                telegram_id, amount_kopecks, transaction_type, source, description
            )
            logger.info(f"Logged balance transaction: user={telegram_id}, amount={amount} RUB, type={transaction_type}, source={source}")
            return True
        except Exception as e:
            logger.exception(f"Error logging balance transaction for user {telegram_id}")
            return False


# Старые функции для совместимости
async def add_balance(telegram_id: int, amount: int, transaction_type: str, description: Optional[str] = None) -> bool:
    """
    Добавить средства на баланс пользователя (атомарно)
    
    DEPRECATED: Используйте increase_balance() вместо этой функции.
    Эта функция оставлена для обратной совместимости и защищена advisory lock + FOR UPDATE.
    
    Args:
        telegram_id: Telegram ID пользователя
        amount: Сумма в копейках (положительное число)
        transaction_type: Тип транзакции ('topup', 'bonus', 'refund')
        description: Описание транзакции
    
    Returns:
        True если успешно, False при ошибке
    """
    if amount <= 0:
        logger.error(f"Invalid amount for add_balance: {amount}")
        return False
    
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            try:
                # CRITICAL: advisory lock per user для защиты от race conditions
                await conn.execute(
                    "SELECT pg_advisory_xact_lock($1)",
                    telegram_id
                )
                
                # CRITICAL: SELECT FOR UPDATE для блокировки строки до конца транзакции
                row = await conn.fetchrow(
                    "SELECT balance FROM users WHERE telegram_id = $1 FOR UPDATE",
                    telegram_id
                )
                
                if not row:
                    logger.error(f"User {telegram_id} not found")
                    return False
                
                # Обновляем баланс (строка уже заблокирована FOR UPDATE)
                await conn.execute(
                    "UPDATE users SET balance = balance + $1 WHERE telegram_id = $2",
                    amount, telegram_id
                )
                
                # Записываем транзакцию
                await conn.execute(
                    """INSERT INTO balance_transactions (user_id, amount, type, description)
                       VALUES ($1, $2, $3, $4)""",
                    telegram_id, amount, transaction_type, description
                )
                
                logger.info(f"Added {amount} kopecks to balance for user {telegram_id}, type={transaction_type}")
                return True
            except Exception as e:
                logger.exception(f"Error adding balance for user {telegram_id}")
                return False


async def subtract_balance(telegram_id: int, amount: int, transaction_type: str, description: Optional[str] = None) -> bool:
    """
    Списать средства с баланса пользователя (атомарно)
    
    DEPRECATED: Используйте decrease_balance() вместо этой функции.
    Эта функция оставлена для обратной совместимости и защищена advisory lock + FOR UPDATE.
    
    Args:
        telegram_id: Telegram ID пользователя
        amount: Сумма в копейках (положительное число)
        transaction_type: Тип транзакции ('spend')
        description: Описание транзакции
    
    Returns:
        True если успешно, False при ошибке или недостатке средств
    """
    if amount <= 0:
        logger.error(f"Invalid amount for subtract_balance: {amount}")
        return False
    
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            try:
                # CRITICAL: advisory lock per user для защиты от race conditions
                await conn.execute(
                    "SELECT pg_advisory_xact_lock($1)",
                    telegram_id
                )
                
                # CRITICAL: SELECT FOR UPDATE для блокировки строки до конца транзакции
                row = await conn.fetchrow(
                    "SELECT balance FROM users WHERE telegram_id = $1 FOR UPDATE",
                    telegram_id
                )
                
                if not row:
                    logger.error(f"User {telegram_id} not found")
                    return False
                
                current_balance = row["balance"]
                
                if current_balance < amount:
                    logger.warning(f"Insufficient balance for user {telegram_id}: {current_balance} < {amount}")
                    return False
                
                # Обновляем баланс (строка уже заблокирована FOR UPDATE)
                await conn.execute(
                    "UPDATE users SET balance = balance - $1 WHERE telegram_id = $2",
                    amount, telegram_id
                )
                
                # Записываем транзакцию (amount отрицательный для списания)
                await conn.execute(
                    """INSERT INTO balance_transactions (user_id, amount, type, description)
                       VALUES ($1, $2, $3, $4)""",
                    telegram_id, -amount, transaction_type, description
                )
                
                logger.info(f"Subtracted {amount} kopecks from balance for user {telegram_id}, type={transaction_type}")
                return True
            except Exception as e:
                logger.exception(f"Error subtracting balance for user {telegram_id}")
                return False


# ====================================================================================
# WITHDRAWAL REQUESTS (Atlas Secure balance withdrawal system)
# ====================================================================================

async def create_withdrawal_request(
    telegram_id: int,
    username: Optional[str],
    amount_kopecks: int,
    requisites: str,
) -> Optional[int]:
    """
    Создать заявку на вывод средств (в транзакции со списанием баланса).
    Advisory lock по telegram_id для защиты от гонок.

    Args:
        telegram_id: Telegram ID пользователя
        username: Username (опционально)
        amount_kopecks: Сумма в копейках
        requisites: Реквизиты (СБП, карта, счёт)

    Returns:
        ID созданной заявки или None при ошибке/недостатке средств
    """
    if amount_kopecks <= 0:
        logger.error(f"Invalid amount_kopecks for create_withdrawal_request: {amount_kopecks}")
        return None
    if not DB_READY:
        logger.warning("DB not ready, create_withdrawal_request skipped")
        return None
    pool = await get_pool()
    if pool is None:
        return None
    async with pool.acquire() as conn:
        async with conn.transaction():
            try:
                # CRITICAL: advisory lock per user для защиты от race conditions
                await conn.execute("SELECT pg_advisory_xact_lock($1)", telegram_id)
                
                # CRITICAL: SELECT FOR UPDATE для блокировки строки до конца транзакции
                row = await conn.fetchrow(
                    "SELECT balance FROM users WHERE telegram_id = $1 FOR UPDATE",
                    telegram_id
                )
                
                if not row:
                    logger.error(f"User {telegram_id} not found for withdrawal")
                    return None
                
                current = row["balance"]
                
                if current < amount_kopecks:
                    logger.warning(f"Insufficient balance for withdrawal: user={telegram_id}, balance={current}, amount={amount_kopecks}")
                    return None
                
                # Обновляем баланс (строка уже заблокирована FOR UPDATE)
                await conn.execute(
                    "UPDATE users SET balance = balance - $1 WHERE telegram_id = $2",
                    amount_kopecks, telegram_id
                )
                await conn.execute(
                    """INSERT INTO balance_transactions (user_id, amount, type, source, description)
                       VALUES ($1, $2, $3, $4, $5)""",
                    telegram_id, -amount_kopecks, "withdrawal", "withdrawal_request",
                    f"Вывод средств: {requisites[:50]}"
                )
                row = await conn.fetchrow(
                    """INSERT INTO withdrawal_requests (telegram_id, username, amount, requisites, status)
                       VALUES ($1, $2, $3, $4, 'pending')
                       RETURNING id""",
                    telegram_id, username, amount_kopecks, requisites
                )
                wid = row["id"]
                
                # Structured logging with correlation_id
                correlation_id = str(uuid_lib.uuid4())
                logger.info(
                    f"WITHDRAWAL_REQUEST_CREATED withdrawal_id={wid} user={telegram_id} "
                    f"amount={amount_kopecks} kopecks correlation_id={correlation_id}"
                )
                return wid
            except Exception as e:
                logger.exception(f"Error creating withdrawal request for user {telegram_id}: {e}")
                return None


async def get_withdrawal_request(wid: int) -> Optional[Dict[str, Any]]:
    """Получить заявку на вывод по ID."""
    if not DB_READY:
        return None
    pool = await get_pool()
    if pool is None:
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM withdrawal_requests WHERE id = $1", wid)
        return dict(row) if row else None


async def approve_withdrawal_request(wid: int, processed_by: int) -> bool:
    """Подтвердить заявку (status=approved). Средства уже списаны при создании."""
    if not DB_READY:
        return False
    pool = await get_pool()
    if pool is None:
        return False
    async with pool.acquire() as conn:
        async with conn.transaction():
            # CRITICAL: SELECT FOR UPDATE для защиты от двойного подтверждения
            row = await conn.fetchrow(
                "SELECT id FROM withdrawal_requests WHERE id = $1 AND status = 'pending' FOR UPDATE",
                wid
            )
            if not row:
                return False
            
            # Обновляем статус
            result = await conn.execute(
                "UPDATE withdrawal_requests SET status = 'approved', processed_at = NOW(), processed_by = $1 WHERE id = $2",
                processed_by, wid
            )
            if result == "UPDATE 1":
                # Structured logging
                logger.info(f"WITHDRAWAL_APPROVED withdrawal_id={wid} processed_by={processed_by}")
                return True
            return False


async def reject_withdrawal_request(wid: int, processed_by: int) -> bool:
    """Отклонить заявку и вернуть средства на баланс."""
    if not DB_READY:
        return False
    pool = await get_pool()
    if pool is None:
        return False
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT id, telegram_id, amount FROM withdrawal_requests WHERE id = $1 AND status = 'pending' FOR UPDATE",
                wid
            )
            if not row:
                return False
            telegram_id = row["telegram_id"]
            amount_kopecks = row["amount"]
            
            # CRITICAL: advisory lock per user для защиты от race conditions
            await conn.execute("SELECT pg_advisory_xact_lock($1)", telegram_id)
            
            # CRITICAL: SELECT FOR UPDATE для блокировки строки до конца транзакции
            user_row = await conn.fetchrow(
                "SELECT balance FROM users WHERE telegram_id = $1 FOR UPDATE",
                telegram_id
            )
            
            if not user_row:
                logger.error(f"User {telegram_id} not found for withdrawal rejection refund")
                return False
            
            # Обновляем баланс (строка уже заблокирована FOR UPDATE)
            await conn.execute(
                "UPDATE users SET balance = balance + $1 WHERE telegram_id = $2",
                amount_kopecks, telegram_id
            )
            await conn.execute(
                """INSERT INTO balance_transactions (user_id, amount, type, source, description)
                   VALUES ($1, $2, $3, $4, $5)""",
                telegram_id, amount_kopecks, "refund", "withdrawal_rejected",
                f"Возврат средств: заявка #{wid} отклонена"
            )
            await conn.execute(
                "UPDATE withdrawal_requests SET status = 'rejected', processed_at = NOW(), processed_by = $1 WHERE id = $2",
                processed_by, wid
            )
            # Structured logging
            logger.info(
                f"WITHDRAWAL_REJECTED withdrawal_id={wid} processed_by={processed_by} "
                f"user={telegram_id} refunded={amount_kopecks} kopecks"
            )
            return True


async def find_user_by_id_or_username(telegram_id: Optional[int] = None, username: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Найти пользователя по Telegram ID или username
    
    Args:
        telegram_id: Telegram ID пользователя (опционально)
        username: Username пользователя без @ (опционально)
    
    Returns:
        Словарь с данными пользователя или None, если не найден
    
    Note:
        Должен быть указан хотя бы один параметр. Если указаны оба, приоритет у telegram_id.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        if telegram_id is not None:
            # Поиск по ID имеет приоритет
            row = await conn.fetchrow(
                "SELECT * FROM users WHERE telegram_id = $1", telegram_id
            )
            return dict(row) if row else None
        elif username is not None:
            # Поиск по username (case-insensitive)
            row = await conn.fetchrow(
                "SELECT * FROM users WHERE LOWER(username) = LOWER($1)", username
            )
            return dict(row) if row else None
        else:
            return None


def generate_referral_code(telegram_id: int) -> str:
    """
    Генерирует детерминированный referral_code для пользователя
    
    Args:
        telegram_id: Telegram ID пользователя
    
    Returns:
        Строка из 6-8 символов (A-Z, 0-9)
    """
    # Используем хеш для детерминированности
    hash_obj = hashlib.sha256(str(telegram_id).encode())
    hash_bytes = hash_obj.digest()
    
    # Используем base32 для получения только букв и цифр
    # Убираем padding и берем первые 6 символов
    encoded = base64.b32encode(hash_bytes).decode('ascii').rstrip('=')
    
    # Берем первые 6 символов и приводим к верхнему регистру
    code = encoded[:6].upper()
    
    return code


async def create_user(telegram_id: int, username: Optional[str] = None, language: str = "ru"):
    """Создать нового пользователя с автоматической генерацией referral_code"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Генерируем referral_code если его нет
        referral_code = generate_referral_code(telegram_id)
        
        await conn.execute(
            """INSERT INTO users (telegram_id, username, language, referral_code) 
               VALUES ($1, $2, $3, $4) 
               ON CONFLICT (telegram_id) DO NOTHING""",
            telegram_id, username, language, referral_code
        )
        
        # Если пользователь уже существовал, обновляем referral_code если его нет
        user = await get_user(telegram_id)
        if user and not user.get("referral_code"):
            await conn.execute(
                "UPDATE users SET referral_code = $1 WHERE telegram_id = $2",
                referral_code, telegram_id
            )


async def find_user_by_referral_code(referral_code: str) -> Optional[Dict[str, Any]]:
    """Найти пользователя по referral_code"""
    if not DB_READY:
        logger.warning("DB not ready (degraded mode), find_user_by_referral_code skipped")
        return None
    
    pool = await get_pool()
    if pool is None:
        return None
    
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM users WHERE referral_code = $1", referral_code
            )
            return dict(row) if row else None
    except (asyncpg.UndefinedTableError, asyncpg.PostgresError) as e:
        logger.warning(f"users table missing or referral_code column missing — skipping: {e}")
        return None
    except Exception as e:
        logger.warning(f"Error finding user by referral code: {e}")
        return None


async def register_referral(referrer_user_id: int, referred_user_id: int) -> bool:
    """
    Зарегистрировать реферала
    
    Args:
        referrer_user_id: Telegram ID реферера
        referred_user_id: Telegram ID приглашенного пользователя
    
    Returns:
        True если регистрация успешна, False если уже зарегистрирован или ошибка
    """
    if not DB_READY:
        logger.warning("DB not ready (degraded mode), register_referral skipped")
        return False
    
    # Запрет self-referral
    if referrer_user_id == referred_user_id:
        logger.warning(
            f"REFERRAL_SELF_ATTEMPT [user_id={referrer_user_id}, "
            f"referrer_id={referrer_user_id}, referred_id={referred_user_id}]"
        )
        return False
    
    pool = await get_pool()
    if pool is None:
        return False
    
    try:
        async with pool.acquire() as conn:
            # Проверяем, что пользователь еще не был приглашен
            existing = await conn.fetchrow(
                "SELECT * FROM referrals WHERE referred_user_id = $1", referred_user_id
            )
            if existing:
                return False
            
            # Создаем запись о реферале
            await conn.execute(
                """INSERT INTO referrals (referrer_user_id, referred_user_id, is_rewarded, reward_amount)
                   VALUES ($1, $2, FALSE, 0)
                   ON CONFLICT (referred_user_id) DO NOTHING""",
                referrer_user_id, referred_user_id
            )
            
            # Обновляем referrer_id у пользователя (IMMUTABLE - устанавливается только один раз)
            # Также обновляем referred_by для обратной совместимости
            # DO NOT use referred_at - column doesn't exist in schema
            result = await conn.execute(
                """UPDATE users 
                   SET referrer_id = $1, referred_by = $1
                   WHERE telegram_id = $2 
                   AND referrer_id IS NULL 
                   AND referred_by IS NULL""",
                referrer_user_id, referred_user_id
            )
            
            # Verify that referrer_id was actually saved
            if result == "UPDATE 1":
                # Double-check by reading back
                saved_user = await conn.fetchrow(
                    "SELECT referrer_id, referred_by FROM users WHERE telegram_id = $1",
                    referred_user_id
                )
                if saved_user and (saved_user.get("referrer_id") == referrer_user_id or saved_user.get("referred_by") == referrer_user_id):
                    logger.info(
                        f"REFERRAL_SAVED [referrer={referrer_user_id}, referred={referred_user_id}, "
                        f"referrer_id_persisted=True]"
                    )
                    logger.info(f"REFERRAL_REGISTERED [referrer={referrer_user_id}, referred={referred_user_id}]")
                    return True
                else:
                    logger.error(
                        f"REFERRAL_SAVE_FAILED [referrer={referrer_user_id}, referred={referred_user_id}, "
                        f"referrer_id_not_persisted]"
                    )
                    return False
            else:
                # UPDATE 0 means referrer_id was already set (idempotent - this is OK)
                # Check if it matches expected referrer
                existing_user = await conn.fetchrow(
                    "SELECT referrer_id, referred_by FROM users WHERE telegram_id = $1",
                    referred_user_id
                )
                if existing_user:
                    existing_referrer = existing_user.get("referrer_id") or existing_user.get("referred_by")
                    if existing_referrer == referrer_user_id:
                        logger.debug(
                            f"REFERRAL_ALREADY_EXISTS [referrer={referrer_user_id}, referred={referred_user_id}, "
                            f"referrer_id_already_set]"
                        )
                        return False  # Already registered with same referrer (idempotent)
                    else:
                        logger.warning(
                            f"REFERRAL_CONFLICT [referrer={referrer_user_id}, referred={referred_user_id}, "
                            f"existing_referrer={existing_referrer}]"
                        )
                        return False  # Different referrer already set (immutable)
                return False
    except (asyncpg.UndefinedTableError, asyncpg.PostgresError) as e:
        logger.warning(f"referrals or users table missing or inaccessible — skipping referral registration: {e}")
        return False
    except Exception as e:
        logger.exception(f"Error registering referral: referrer_id={referrer_user_id}, referred_id={referred_user_id}")
        return False


async def mark_referral_active(referred_user_id: int, conn: Optional[asyncpg.Connection] = None) -> bool:
    """
    Пометить реферала как активного (активировал trial или подписку).
    
    Это обновляет запись в referrals, чтобы реферал считался активным.
    Вызывается при активации trial или первой подписки.
    
    Args:
        referred_user_id: Telegram ID реферала
        conn: Соединение с БД (если None, создаётся новое)
    
    Returns:
        True если успешно, False иначе
    """
    if not DB_READY:
        logger.warning("DB not ready (degraded mode), mark_referral_active skipped")
        return False
    
    if conn is None:
        pool = await get_pool()
        if pool is None:
            return False
        async with pool.acquire() as conn:
            return await _mark_referral_active_internal(referred_user_id, conn)
    else:
        return await _mark_referral_active_internal(referred_user_id, conn)


async def _mark_referral_active_internal(referred_user_id: int, conn: asyncpg.Connection) -> bool:
    """Internal helper for marking referral as active"""
    try:
        # Проверяем, существует ли запись о реферале
        referral_row = await conn.fetchrow(
            "SELECT referrer_user_id FROM referrals WHERE referred_user_id = $1",
            referred_user_id
        )
        
        if referral_row:
            # Запись существует - просто логируем (уже активен)
            logger.debug(f"Referral already exists: referred={referred_user_id}")
            return True
        else:
            # Записи нет - получаем referrer_id из users
            user_row = await conn.fetchrow(
                "SELECT referrer_id FROM users WHERE telegram_id = $1",
                referred_user_id
            )
            
            if not user_row or not user_row.get("referrer_id"):
                # Нет реферера - это нормально (не все пользователи приглашены)
                logger.debug(f"No referrer for user: referred={referred_user_id}")
                return False
            
            referrer_user_id = user_row["referrer_id"]
            
            # Создаем запись о реферале (если её нет)
            await conn.execute(
                """INSERT INTO referrals (referrer_user_id, referred_user_id, is_rewarded, reward_amount)
                   VALUES ($1, $2, FALSE, 0)
                   ON CONFLICT (referred_user_id) DO NOTHING""",
                referrer_user_id, referred_user_id
            )
            
            logger.info(f"REFERRAL_MARKED_ACTIVE [referrer={referrer_user_id}, referred={referred_user_id}]")
            return True
            
    except (asyncpg.UndefinedTableError, asyncpg.PostgresError) as e:
        logger.warning(f"referrals table missing or inaccessible — skipping mark_referral_active: {e}")
        return False
    except Exception as e:
        logger.exception(f"Error marking referral as active: referred_id={referred_user_id}")
        return False


async def get_referral_stats(telegram_id: int) -> Dict[str, int]:
    """
    Получить статистику рефералов для пользователя
    
    Returns:
        Словарь с ключами: total_referred, total_rewarded
    """
    if not DB_READY:
        logger.warning("DB not ready (degraded mode), get_referral_stats skipped")
        return {"total_referred": 0, "total_rewarded": 0}
    
    pool = await get_pool()
    if pool is None:
        return {"total_referred": 0, "total_rewarded": 0}
    
    try:
        async with pool.acquire() as conn:
            total_referred = await conn.fetchval(
                "SELECT COUNT(*) FROM referrals WHERE referrer_user_id = $1", telegram_id
            )
            # total_rewarded больше не используется (кешбэк начисляется при каждой оплате)
            total_rewarded = await conn.fetchval(
                "SELECT COUNT(*) FROM referrals WHERE referrer_user_id = $1 AND is_rewarded = TRUE", telegram_id
            )
            
            return {
                "total_referred": total_referred or 0,
                "total_rewarded": total_rewarded or 0
            }
    except (asyncpg.UndefinedTableError, asyncpg.PostgresError) as e:
        logger.warning(f"referrals table missing or inaccessible — skipping: {e}")
        return {"total_referred": 0, "total_rewarded": 0}
    except Exception as e:
        logger.warning(f"Error getting referral stats: {e}")
        return {"total_referred": 0, "total_rewarded": 0}


async def get_referral_cashback_percent(partner_id: int) -> int:
    """
    Определить процент кешбэка на основе количества оплативших рефералов
    
    Прогрессивная шкала (вычисляется динамически на основе ОПЛАТИВШИХ):
    - 0-24 оплативших → 10%
    - 25-49 оплативших → 25%
    - 50+ оплативших → 45%
    
    Args:
        partner_id: Telegram ID партнёра
    
    Returns:
        Процент кешбэка (10, 25 или 45)
    
    SAFE: Всегда возвращает валидный процент, даже если данных нет
    """
    if not DB_READY:
        logger.warning("DB not ready (degraded mode), get_referral_cashback_percent skipped")
        return 10
    
    pool = await get_pool()
    if pool is None:
        return 10
    
    try:
        async with pool.acquire() as conn:
            # Считаем количество РЕФЕРАЛОВ, КОТОРЫЕ ОПЛАТИЛИ (из referral_rewards)
            paid_referrals_count_val = await conn.fetchval(
                """SELECT COUNT(DISTINCT rr.buyer_id)
                   FROM referral_rewards rr
                   WHERE rr.referrer_id = $1""",
                partner_id
            )
            paid_referrals_count = safe_int(paid_referrals_count_val)
        
        # Определяем процент по прогрессивной шкале
        if paid_referrals_count >= 50:
            return 45
        elif paid_referrals_count >= 25:
            return 25
        else:
            return 10
    except (asyncpg.UndefinedTableError, asyncpg.PostgresError) as e:
        logger.warning(f"referral_rewards table missing or inaccessible — skipping: {e}")
        return 10
    except Exception as e:
        logger.warning(f"Error in get_referral_cashback_percent for partner_id={partner_id}: {e}")
        # Возвращаем безопасное значение по умолчанию
        return 10


def calculate_referral_percent(invited_count: int) -> int:
    """
    Рассчитать процент кешбэка на основе количества приглашённых рефералов
    
    Прогрессивная шкала:
    - 0-24 приглашённых → 10%
    - 25-49 приглашённых → 25%
    - 50+ приглашённых → 45%
    
    Args:
        invited_count: Количество приглашённых пользователей
    
    Returns:
        Процент кешбэка (10, 25 или 45)
    """
    if invited_count >= 50:
        return 45
    elif invited_count >= 25:
        return 25
    else:
        return 10


async def get_referral_level_info(partner_id: int) -> Dict[str, Any]:
    """
    Получить информацию об уровне реферала и прогрессе до следующего уровня
    
    ВАЖНО: Уровень определяется по количеству РЕФЕРАЛОВ, КОТОРЫЕ ОПЛАТИЛИ подписку
    (не по количеству приглашённых, а по количеству оплативших)
    
    Args:
        partner_id: Telegram ID партнёра
    
    Returns:
        Словарь с ключами:
        - current_level: текущий процент (10, 25 или 45)
        - referrals_count: текущее количество приглашённых (из таблицы referrals)
        - paid_referrals_count: количество рефералов, которые оплатили подписку (из referral_rewards)
        - next_level: следующий процент (25, 45 или None)
        - referrals_to_next: сколько нужно оплативших рефералов до следующего уровня (или None)
    
    SAFE: Всегда возвращает валидный словарь с безопасными значениями по умолчанию
    """
    if not DB_READY:
        logger.warning("DB not ready (degraded mode), get_referral_level_info skipped")
        return {
            "current_level": 10,
            "referrals_count": 0,
            "paid_referrals_count": 0,
            "next_level": 25,
            "referrals_to_next": 25
        }
    
    pool = await get_pool()
    if pool is None:
        return {
            "current_level": 10,
            "referrals_count": 0,
            "paid_referrals_count": 0,
            "next_level": 25,
            "referrals_to_next": 25
        }
    
    try:
        async with pool.acquire() as conn:
            # Считаем количество приглашённых пользователей (из таблицы referrals)
            # Безопасная обработка NULL
            referrals_count_val = await conn.fetchval(
                "SELECT COUNT(*) FROM referrals WHERE referrer_user_id = $1",
                partner_id
            )
            referrals_count = safe_int(referrals_count_val)
            
            # Считаем количество РЕФЕРАЛОВ, КОТОРЫЕ ОПЛАТИЛИ подписку (из referral_rewards)
            # Это важное отличие: уровень определяется по оплатившим, а не по приглашённым
            paid_referrals_count_val = await conn.fetchval(
                """SELECT COUNT(DISTINCT rr.buyer_id)
                   FROM referral_rewards rr
                   WHERE rr.referrer_id = $1""",
                partner_id
            )
            paid_referrals_count = safe_int(paid_referrals_count_val)
            
            # Определяем текущий уровень и следующий НА ОСНОВЕ ОПЛАТИВШИХ
            if paid_referrals_count >= 50:
                current_level = 45
                next_level = None
                referrals_to_next = None
            elif paid_referrals_count >= 25:
                current_level = 25
                next_level = 45
                referrals_to_next = 50 - paid_referrals_count
            else:
                current_level = 10
                next_level = 25
                referrals_to_next = 25 - paid_referrals_count
            
            return {
                "current_level": current_level,
                "referrals_count": referrals_count,
                "paid_referrals_count": paid_referrals_count,
                "next_level": next_level,
                "referrals_to_next": referrals_to_next
            }
    except (asyncpg.UndefinedTableError, asyncpg.PostgresError) as e:
        logger.warning(f"referrals or referral_rewards table missing or inaccessible — skipping: {e}")
        return {
            "current_level": 10,
            "referrals_count": 0,
            "paid_referrals_count": 0,
            "next_level": 25,
            "referrals_to_next": 25
        }
    except Exception as e:
        logger.warning(f"Error in get_referral_level_info for partner_id={partner_id}: {e}")
        # Возвращаем безопасные значения по умолчанию
        return {
            "current_level": 10,
            "referrals_count": 0,
            "paid_referrals_count": 0,
            "next_level": 25,
            "referrals_to_next": 25
        }


async def get_total_cashback_earned(partner_id: int) -> float:
    """
    Получить общую сумму заработанного кешбэка партнёром
    
    Args:
        partner_id: Telegram ID партнёра
    
    Returns:
        Сумма кешбэка в рублях (0.0 если данных нет)
    
    SAFE: Всегда возвращает float, даже если данных нет
    """
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            # Суммируем все транзакции типа 'cashback' для партнёра
            # COALESCE гарантирует, что NULL станет 0
            total_kopecks_val = await conn.fetchval(
                """SELECT COALESCE(SUM(amount), 0) 
                   FROM balance_transactions 
                   WHERE user_id = $1 AND type = 'cashback'""",
                partner_id
            )
            total_kopecks = safe_int(total_kopecks_val)
            
            return total_kopecks / 100.0  # Конвертируем из копеек в рубли
    except Exception as e:
        logger.exception(f"Error in get_total_cashback_earned for partner_id={partner_id}: {e}")
        return 0.0


async def get_referral_metrics(user_id: int) -> Dict[str, int]:
    """
    Получить разделённые метрики рефералов для пользователя.
    
    КРИТИЧНО:
    - total_referrals: ВСЕ приглашённые (без фильтров)
    - active_paid_referrals: Только с активной подпиской (expires_at > NOW())
    
    Args:
        user_id: Telegram ID пользователя
    
    Returns:
        {
            "total_referrals": int,  # Всего приглашено (без фильтров)
            "active_paid_referrals": int  # Активных с подпиской
        }
    """
    if not DB_READY:
        return {
            "total_referrals": 0,
            "active_paid_referrals": 0
        }
    
    pool = await get_pool()
    if pool is None:
        return {
            "total_referrals": 0,
            "active_paid_referrals": 0
        }
    
    try:
        async with pool.acquire() as conn:
            # 1️⃣ Всего приглашено: ВСЕ записи из referrals
            total_referrals_val = await conn.fetchval(
                "SELECT COUNT(*) FROM referrals WHERE referrer_user_id = $1",
                user_id
            )
            total_referrals = safe_int(total_referrals_val)
            
            # 2️⃣ Активных с подпиской: только те, у кого активная подписка
            active_paid_referrals_val = await conn.fetchval(
                """SELECT COUNT(DISTINCT r.referred_user_id)
                   FROM referrals r
                   INNER JOIN subscriptions s ON s.telegram_id = r.referred_user_id
                   WHERE r.referrer_user_id = $1
                   AND s.expires_at IS NOT NULL
                   AND s.expires_at > NOW()""",
                user_id
            )
            active_paid_referrals = safe_int(active_paid_referrals_val)
            
            return {
                "total_referrals": total_referrals,
                "active_paid_referrals": active_paid_referrals
            }
    except (asyncpg.UndefinedTableError, asyncpg.PostgresError) as e:
        logger.warning(f"referrals or subscriptions table missing — skipping: {e}")
        return {
            "total_referrals": 0,
            "active_paid_referrals": 0
        }
    except Exception as e:
        logger.warning(f"Error in get_referral_metrics for user_id={user_id}: {e}")
        return {
            "total_referrals": 0,
            "active_paid_referrals": 0
        }


def calculate_referral_level(total_referrals: int) -> Dict[str, Any]:
    """
    Рассчитать уровень реферала СТРОГО на основе total_referrals.
    
    ⚠️ ВАЖНО: Уровень определяется СТРОГО по total_referrals.
    НЕ используется active_paid_referrals, rewards, revenue.
    
    Пороги соответствуют существующим уровням из loyalty.py:
    - 0-24: Silver Access (10%)
    - 25-49: Gold Access (25%)
    - 50+: Platinum Access (45%)
    
    Args:
        total_referrals: Общее количество приглашённых рефералов
    
    Returns:
        {
            "current_level_name": str,  # "Silver Access", "Gold Access", "Platinum Access"
            "cashback_percent": int,  # 10, 25, 45
            "next_level_name": Optional[str],  # Следующий уровень или None
            "remaining_connections": int  # До следующего уровня (max(0, ...))
        }
    """
    # Структура уровней: соответствует LOYALTY_TIERS из app/constants/loyalty.py
    # Пороги: 0-24 → Silver, 25-49 → Gold, 50+ → Platinum
    REFERRAL_LEVELS = [
        {"name": "Platinum Access", "threshold": 50, "cashback": 45},
        {"name": "Gold Access", "threshold": 25, "cashback": 25},
        {"name": "Silver Access", "threshold": 0, "cashback": 10},  # Базовый уровень
    ]
    
    # Сортируем по threshold DESC (от большего к меньшему)
    levels_sorted = sorted(REFERRAL_LEVELS, key=lambda x: x["threshold"], reverse=True)
    
    # Находим текущий уровень (максимальный, где total_referrals >= threshold)
    current_level = None
    for level in levels_sorted:
        if total_referrals >= level["threshold"]:
            current_level = level
            break
    
    # Если не найден (не должно произойти, т.к. есть базовый уровень с threshold=0)
    if current_level is None:
        current_level = {"name": "Silver Access", "threshold": 0, "cashback": 10}
    
    # Находим следующий уровень (первый, где threshold > total_referrals)
    next_level = None
    for level in levels_sorted:
        if level["threshold"] > total_referrals:
            next_level = level
    
    # Рассчитываем remaining_connections
    if next_level:
        remaining = next_level["threshold"] - total_referrals
        remaining = max(0, remaining)  # ⚠️ ОБЯЗАТЕЛЬНО: никогда не отрицательный
    else:
        remaining = 0  # Максимальный уровень достигнут
    
    return {
        "current_level_name": current_level["name"],
        "cashback_percent": current_level["cashback"],
        "next_level_name": next_level["name"] if next_level else None,
        "remaining_connections": remaining
    }


async def get_referral_statistics(partner_id: int) -> Dict[str, Any]:
    """
    Получить полную статистику рефералов для партнёра.
    
    НОВАЯ ЛОГИКА:
    - total_invited: Всего приглашено (из referrals, без фильтров)
    - active_paid_referrals: Активных с подпиской (expires_at > NOW())
    - Уровень рассчитывается СТРОГО по total_invited
    
    Returns:
        {
            "total_invited": int,  # Всего приглашено
            "active_paid_referrals": int,  # Активных с подпиской
            "total_cashback_earned": float,  # Общий кешбэк в рублях
            "last_activity_at": Optional[datetime],  # Последняя активность реферала
            "current_level_name": str,  # "Silver Access", "Gold Access", "Platinum Access"
            "cashback_percent": int,  # 10, 25, 45
            "next_level_name": Optional[str],  # Следующий уровень или None
            "remaining_connections": int  # До следующего уровня
        }
    """
    if not DB_READY:
        return {
            "total_invited": 0,
            "active_paid_referrals": 0,
            "total_cashback_earned": 0.0,
            "last_activity_at": None,
            "current_level_name": "Silver Access",
            "cashback_percent": 10,
            "next_level_name": "Gold Access",
            "remaining_connections": 5
        }
    
    pool = await get_pool()
    if pool is None:
        return {
            "total_invited": 0,
            "active_paid_referrals": 0,
            "total_cashback_earned": 0.0,
            "last_activity_at": None,
            "current_level_name": "Silver Access",
            "cashback_percent": 10,
            "next_level_name": "Gold Access",
            "remaining_connections": 5
        }
    
    try:
        async with pool.acquire() as conn:
            # Получаем разделённые метрики
            metrics = await get_referral_metrics(partner_id)
            total_invited = metrics["total_referrals"]
            active_paid_referrals = metrics["active_paid_referrals"]
            
            # Total cashback earned
            total_cashback_kopecks_val = await conn.fetchval(
                """SELECT COALESCE(SUM(amount), 0) 
                   FROM balance_transactions 
                   WHERE user_id = $1 AND type = 'cashback'""",
                partner_id
            )
            total_cashback_kopecks = safe_int(total_cashback_kopecks_val)
            total_cashback_earned = total_cashback_kopecks / 100.0
            
            # Last activity timestamp (последняя оплата реферала)
            last_activity_row = await conn.fetchrow(
                """SELECT MAX(r.first_paid_at) as last_activity
                   FROM referrals r
                   WHERE r.referrer_user_id = $1 AND r.first_paid_at IS NOT NULL""",
                partner_id
            )
            last_activity_at = last_activity_row.get("last_activity") if last_activity_row else None
            
            # Рассчитываем уровень СТРОГО по total_invited
            level_info = calculate_referral_level(total_invited)
            
            # Debug логирование
            logger.info(
                f"REF_STATS user={partner_id} "
                f"total={total_invited} "
                f"active_paid={active_paid_referrals} "
                f"level={level_info['current_level_name']} "
                f"remaining={level_info['remaining_connections']}"
            )
            
            return {
                "total_invited": total_invited,
                "active_paid_referrals": active_paid_referrals,
                "total_cashback_earned": total_cashback_earned,
                "last_activity_at": last_activity_at,
                "current_level_name": level_info["current_level_name"],
                "cashback_percent": level_info["cashback_percent"],
                "next_level_name": level_info["next_level_name"],
                "remaining_connections": level_info["remaining_connections"]
            }
    except Exception as e:
        logger.exception(f"Error getting referral statistics for partner_id={partner_id}: {e}")
        return {
            "total_invited": 0,
            "active_paid_referrals": 0,
            "total_cashback_earned": 0.0,
            "last_activity_at": None,
            "current_level_name": "Silver Access",
            "cashback_percent": 10,
            "next_level_name": "Gold Access",
            "remaining_connections": 5
        }


async def process_referral_reward_cashback(referred_user_id: int, payment_amount_rubles: float) -> bool:
    """
    DEPRECATED: Используйте process_referral_reward вместо этой функции.
    Оставлена для обратной совместимости.
    
    ВНИМАНИЕ: Эта функция больше не работает, так как process_referral_reward
    теперь требует conn и purchase_id. Используйте process_referral_reward напрямую.
    """
    raise NotImplementedError(
        "process_referral_reward_cashback is deprecated and no longer functional. "
        "Use process_referral_reward directly with conn and purchase_id parameters."
    )


async def _process_referral_reward_cashback_OLD(referred_user_id: int, payment_amount_rubles: float) -> bool:
    """
    Начислить кешбэк партнёру при КАЖДОЙ оплате приглашенного пользователя
    
    DEPRECATED: Используйте process_referral_reward вместо этой функции.
    Оставлена для обратной совместимости.
    
    ВНИМАНИЕ: Эта функция больше не работает, так как process_referral_reward
    теперь требует conn и purchase_id. Используйте process_referral_reward напрямую.
    """
    raise NotImplementedError(
        "_process_referral_reward_cashback_OLD is deprecated and no longer functional. "
        "Use process_referral_reward directly with conn and purchase_id parameters."
    )


async def process_referral_reward(
    buyer_id: int,
    purchase_id: str,
    amount_rubles: float,
    conn: asyncpg.Connection
) -> Dict[str, Any]:
    """
    Начислить реферальный кешбэк рефереру при успешной активации подписки покупателя.
    
    КРИТИЧЕСКИ ВАЖНО:
    - Начисление происходит ТОЛЬКО при успешной активации подписки (source='payment')
    - НЕ начисляется при admin-grant, test-access, free-access
    - Защита от повторного начисления за один purchase_id
    - Защита от самореферала
    
    Args:
        buyer_id: Telegram ID покупателя, который оплатил подписку
        purchase_id: ID покупки (для защиты от повторного начисления). Если None - начисление происходит без защиты
        amount_rubles: Сумма оплаты в рублях
    
    Returns:
        Словарь с результатом:
        {
            "success": bool,
            "referrer_id": Optional[int],
            "percent": Optional[int],
            "reward_amount": Optional[float],
            "message": str
        }
    """
    # BUSINESS LOGIC CHECKS (return structured results, do not raise):
    try:
        # 1. Получаем реферера покупателя
        user = await conn.fetchrow(
            "SELECT referrer_id, referred_by FROM users WHERE telegram_id = $1",
            buyer_id
        )
        
        if not user:
            logger.debug(f"process_referral_reward: User {buyer_id} not found")
            return {
                "success": False,
                "referrer_id": None,
                "percent": None,
                "reward_amount": None,
                "message": "User not found",
                "reason": "user_not_found"
            }
        
        # Use referrer_id, fallback to referred_by for backward compatibility
        referrer_id = user.get("referrer_id") or user.get("referred_by")
        
        if not referrer_id:
            # Пользователь не был приглашён через реферальную программу
            logger.debug(f"process_referral_reward: User {buyer_id} has no referrer")
            return {
                "success": False,
                "referrer_id": None,
                "percent": None,
                "reward_amount": None,
                "message": "No referrer",
                "reason": "no_referrer"
            }
        
        # Log referrer resolution
        logger.info(
            f"REFERRAL_RESOLVED [buyer={buyer_id}, referrer={referrer_id}, "
            f"purchase_id={purchase_id}]"
        )
        
        # 2. ЗАЩИТА ОТ САМОРЕФЕРАЛА
        if referrer_id == buyer_id:
            logger.warning(f"process_referral_reward: Self-referral detected: user {buyer_id}")
            return {
                "success": False,
                "referrer_id": referrer_id,
                "percent": None,
                "reward_amount": None,
                "message": "Self-referral detected",
                "reason": "self_referral"
            }
        
        # 3. ЗАЩИТА ОТ ПОВТОРНОГО НАЧИСЛЕНИЯ (idempotency check)
        # purchase_id теперь обязателен, проверка всегда выполняется
        existing_reward = await conn.fetchrow(
            "SELECT id FROM referral_rewards WHERE buyer_id = $1 AND purchase_id = $2",
            buyer_id, purchase_id
        )
        
        if existing_reward:
            logger.warning(
                f"process_referral_reward: Duplicate reward attempt detected: "
                f"buyer_id={buyer_id}, purchase_id={purchase_id}"
            )
            return {
                "success": False,
                "referrer_id": referrer_id,
                "percent": None,
                "reward_amount": None,
                "message": "Reward already processed for this purchase",
                "reason": "duplicate_reward"
            }
        
        # 4. Обновляем first_paid_at в referrals, если это первый платеж реферала
        referral_row = await conn.fetchrow(
            "SELECT first_paid_at FROM referrals WHERE referrer_user_id = $1 AND referred_user_id = $2",
            referrer_id, buyer_id
        )
        
        if not referral_row:
            # Создаем запись в referrals, если её нет
            await conn.execute(
                """INSERT INTO referrals (referrer_user_id, referred_user_id, first_paid_at)
                   VALUES ($1, $2, NOW())
                   ON CONFLICT (referred_user_id) DO UPDATE
                   SET first_paid_at = COALESCE(referrals.first_paid_at, NOW())""",
                referrer_id, buyer_id
            )
        elif not referral_row.get("first_paid_at"):
            # Обновляем first_paid_at, если он еще не установлен
            await conn.execute(
                "UPDATE referrals SET first_paid_at = NOW() WHERE referrer_user_id = $1 AND referred_user_id = $2 AND first_paid_at IS NULL",
                referrer_id, buyer_id
            )
        
        # 5. Определяем процент кешбэка на основе количества оплативших рефералов
        # Считаем количество рефералов, которые ХОТЯ БЫ ОДИН РАЗ оплатили подписку
        # Используем referrals.first_paid_at как источник истины
        paid_referrals_count = await conn.fetchval(
            """SELECT COUNT(DISTINCT referred_user_id)
               FROM referrals
               WHERE referrer_user_id = $1 AND first_paid_at IS NOT NULL""",
            referrer_id
        ) or 0
        
        # Определяем процент по прогрессивной шкале
        if paid_referrals_count >= 50:
            percent = 45
        elif paid_referrals_count >= 25:
            percent = 25
        else:
            percent = 10
        
        # Вычисляем сколько осталось до следующего уровня
        if paid_referrals_count < 25:
            next_level_threshold = 25
            referrals_needed = 25 - paid_referrals_count
        elif paid_referrals_count < 50:
            next_level_threshold = 50
            referrals_needed = 50 - paid_referrals_count
        else:
            next_level_threshold = None
            referrals_needed = 0
        
        # 6. Рассчитываем сумму кешбэка (в копейках)
        purchase_amount_kopecks = int(amount_rubles * 100)
        reward_amount_kopecks = int(purchase_amount_kopecks * percent / 100)
        reward_amount_rubles = reward_amount_kopecks / 100.0
        
        if reward_amount_kopecks <= 0:
            logger.warning(
                f"process_referral_reward: Invalid reward amount: "
                f"{reward_amount_kopecks} kopecks for payment {amount_rubles} RUB, percent={percent}%"
            )
            return {
                "success": False,
                "referrer_id": referrer_id,
                "percent": percent,
                "reward_amount": None,
                "message": "Invalid reward amount",
                "reason": "invalid_amount"
            }
        
        # FINANCIAL OPERATIONS (raise exceptions on failure, do not catch):
        # 7. Начисляем кешбэк на баланс реферера
        # CRITICAL: advisory lock per user для защиты от race conditions
        await conn.execute(
            "SELECT pg_advisory_xact_lock($1)",
            referrer_id
        )
        
        # CRITICAL: SELECT FOR UPDATE для блокировки строки до конца транзакции
        balance_row = await conn.fetchrow(
            "SELECT balance FROM users WHERE telegram_id = $1 FOR UPDATE",
            referrer_id
        )
        
        if not balance_row:
            raise ValueError(f"Referrer {referrer_id} not found for reward")
        
        # Обновляем баланс (строка уже заблокирована FOR UPDATE)
        await conn.execute(
            "UPDATE users SET balance = balance + $1 WHERE telegram_id = $2",
            reward_amount_kopecks, referrer_id
        )
        
        # 8. Записываем транзакцию баланса
        # Если это упадет - исключение пробросится вверх, транзакция откатится
        await conn.execute(
            """INSERT INTO balance_transactions (user_id, amount, type, source, description, related_user_id)
               VALUES ($1, $2, $3, $4, $5, $6)""",
            referrer_id, reward_amount_kopecks, "cashback", "referral",
            f"Реферальный кешбэк {percent}% за оплату пользователя {buyer_id}",
            buyer_id
        )
        
        # 9. Создаём запись в referral_rewards (история начислений)
        # Если это упадет - исключение пробросится вверх, транзакция откатится
        await conn.execute(
            """INSERT INTO referral_rewards 
               (referrer_id, buyer_id, purchase_id, purchase_amount, percent, reward_amount)
               VALUES ($1, $2, $3, $4, $5, $6)""",
            referrer_id, buyer_id, purchase_id, purchase_amount_kopecks, percent, reward_amount_kopecks
        )
        
        # 10. Логируем событие
        details = (
            f"Referral reward awarded: referrer={referrer_id} ({percent}%), "
            f"buyer={buyer_id}, purchase_id={purchase_id}, "
            f"purchase={amount_rubles:.2f} RUB, reward={reward_amount_rubles:.2f} RUB "
            f"({reward_amount_kopecks} kopecks), paid_referrals_count={paid_referrals_count}"
        )
        await _log_audit_event_atomic(
            conn,
            "referral_reward",
            referrer_id,
            buyer_id,
            details
        )
        
        logger.info(
            f"REFERRAL_REWARD_APPLIED [referrer={referrer_id}, buyer={buyer_id}, "
            f"purchase_id={purchase_id}, percent={percent}%, amount={reward_amount_rubles:.2f} RUB, "
            f"paid_referrals_count={paid_referrals_count}]"
        )
        logger.info(
            f"Referral reward awarded: referrer={referrer_id}, buyer={buyer_id}, "
            f"percent={percent}%, amount={reward_amount_rubles:.2f} RUB, "
            f"paid_referrals_count={paid_referrals_count}"
        )
        
        return {
            "success": True,
            "referrer_id": referrer_id,
            "percent": percent,
            "reward_amount": reward_amount_rubles,
            "paid_referrals_count": paid_referrals_count,
            "next_level_threshold": next_level_threshold,
            "referrals_needed": referrals_needed,
            "message": "Reward awarded successfully"
        }
                
    except (asyncpg.UniqueViolationError, asyncpg.ForeignKeyViolationError, 
            asyncpg.NotNullViolationError, asyncpg.CheckViolationError,
            asyncpg.ConnectionError, asyncpg.TimeoutError) as e:
        # FINANCIAL ERRORS: Database constraint violations, connection issues
        # These MUST propagate to cause transaction rollback
        logger.error(
            f"process_referral_reward: Financial error (will rollback transaction): "
            f"buyer_id={buyer_id}, purchase_id={purchase_id}, error={e}"
        )
        raise  # Re-raise to cause transaction rollback
    
    except asyncpg.PostgresError as e:
        # Other database errors - also financial, must rollback
        logger.error(
            f"process_referral_reward: Database error (will rollback transaction): "
            f"buyer_id={buyer_id}, purchase_id={purchase_id}, error={e}"
        )
        raise  # Re-raise to cause transaction rollback


async def update_user_language(telegram_id: int, language: str):
    """Обновить язык пользователя"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET language = $1 WHERE telegram_id = $2",
            language, telegram_id
        )


async def update_username(telegram_id: int, username: Optional[str]):
    """Обновить username пользователя"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET username = $1 WHERE telegram_id = $2",
            username, telegram_id
        )


async def get_pending_payment_by_user(telegram_id: int) -> Optional[Dict[str, Any]]:
    """Получить pending платеж пользователя"""
    # Защита от работы с неинициализированной БД
    if not DB_READY:
        logger.warning("DB not ready, get_pending_payment_by_user skipped")
        return None
    pool = await get_pool()
    if pool is None:
        logger.warning("Pool is None, get_pending_payment_by_user skipped")
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM payments WHERE telegram_id = $1 AND status = 'pending'",
            telegram_id
        )
        return dict(row) if row else None


async def create_payment(telegram_id: int, tariff: str) -> Optional[int]:
    """Создать платеж и вернуть его ID. Возвращает None, если уже есть pending платеж
    
    Автоматически применяет скидки в следующем порядке приоритета:
    1. VIP-статус (30%) - ВЫСШИЙ ПРИОРИТЕТ
    2. Персональная скидка (admin)
    """
    # Проверяем наличие pending платежа
    existing_payment = await get_pending_payment_by_user(telegram_id)
    if existing_payment:
        return None  # У пользователя уже есть pending платеж
    
    # Рассчитываем цену с учетом скидки
    tariff_data = config.TARIFFS.get(tariff, config.TARIFFS["1"])
    base_price = tariff_data["price"]
    
    # ПРИОРИТЕТ 1: Проверяем VIP-статус (высший приоритет)
    is_vip = await is_vip_user(telegram_id)
    discount_applied = None
    discount_type = None
    
    if is_vip:
        # Применяем VIP-скидку 30% ко всем тарифам
        discounted_price = int(base_price * 0.70)  # 30% скидка
        amount = discounted_price
        discount_applied = 30
        discount_type = "vip"
    else:
        # ПРИОРИТЕТ 2: Проверяем персональную скидку
        personal_discount = await get_user_discount(telegram_id)
        
        if personal_discount:
            # Применяем персональную скидку
            discount_percent = personal_discount["discount_percent"]
            discounted_price = int(base_price * (1 - discount_percent / 100))
            amount = discounted_price
            discount_applied = discount_percent
            discount_type = "personal"
        else:
            # Без скидки - используем базовую цену
            amount = base_price
    
    pool = await get_pool()
    async with pool.acquire() as conn:
        payment_id = await conn.fetchval(
            "INSERT INTO payments (telegram_id, tariff, amount, status) VALUES ($1, $2, $3, 'pending') RETURNING id",
            telegram_id, tariff, amount
        )
        
        # Логируем применение скидки
        if discount_applied:
            details = f"{discount_type} discount applied: tariff={tariff}, base_price={base_price}, discount={discount_applied}%, final_price={amount}"
            await _log_audit_event_atomic(conn, f"{discount_type}_discount_applied", telegram_id, telegram_id, details)
        
        return payment_id


async def get_payment(payment_id: int) -> Optional[Dict[str, Any]]:
    """Получить платеж по ID"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM payments WHERE id = $1", payment_id
        )
        return dict(row) if row else None


async def get_last_approved_payment(telegram_id: int) -> Optional[Dict[str, Any]]:
    """Получить последний утверждённый платёж пользователя
    
    Args:
        telegram_id: Telegram ID пользователя
    
    Returns:
        Словарь с данными платежа или None, если платёж не найден
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT * FROM payments 
               WHERE telegram_id = $1 AND status = 'approved'
               ORDER BY created_at DESC
               LIMIT 1""",
            telegram_id
        )
        return dict(row) if row else None


async def update_payment_status(payment_id: int, status: str, admin_telegram_id: Optional[int] = None):
    """Обновить статус платежа
    
    Args:
        payment_id: ID платежа
        status: Новый статус ('approved', 'rejected', и т.д.)
        admin_telegram_id: Telegram ID администратора (опционально, для аудита)
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Получаем информацию о платеже для аудита
            payment_row = await conn.fetchrow(
                "SELECT telegram_id FROM payments WHERE id = $1",
                payment_id
            )
            target_user = payment_row["telegram_id"] if payment_row else None
            
            # Обновляем статус
            await conn.execute(
                "UPDATE payments SET status = $1 WHERE id = $2",
                status, payment_id
            )
            
            # Записываем в audit_log, если указан admin_telegram_id
            if admin_telegram_id is not None:
                action_type = "payment_rejected" if status == "rejected" else f"payment_status_changed_{status}"
                details = f"Payment ID: {payment_id}, Status: {status}"
                await _log_audit_event_atomic(conn, action_type, admin_telegram_id, target_user, details)


async def check_and_disable_expired_subscription(telegram_id: int) -> bool:
    """
    Проверить и немедленно отключить истёкшую подписку
    
    Вызывается при каждом обращении пользователя для мгновенного отключения доступа.
    Это критично для предотвращения "ghost access" без ожидания scheduler.
    
    Returns:
        True если подписка была отключена, False если подписка активна или отсутствует
    """
    # Защита от работы с неинициализированной БД
    if not DB_READY:
        logger.warning("DB not ready, check_and_disable_expired_subscription skipped")
        return False
    pool = await get_pool()
    if pool is None:
        logger.warning("Pool is None, check_and_disable_expired_subscription skipped")
        return False
    async with pool.acquire() as conn:
        async with conn.transaction():
            try:
                now = datetime.now(timezone.utc)
                
                # Получаем подписку, которая истекла, но ещё не очищена
                row = await conn.fetchrow(
                    """SELECT * FROM subscriptions 
                       WHERE telegram_id = $1 
                       AND expires_at <= $2 
                       AND status = 'active'
                       AND uuid IS NOT NULL""",
                    telegram_id, _to_db_utc(now)
                )
                
                if not row:
                    return False  # Подписка активна или отсутствует
                
                subscription = dict(row)
                uuid = subscription.get("uuid")
                
                if uuid:
                    # Удаляем UUID из Xray API
                    try:
                        await vpn_utils.remove_vless_user(uuid)
                        # Безопасное логирование UUID
                        uuid_preview = f"{uuid[:8]}..." if uuid and len(uuid) > 8 else (uuid or "N/A")
                        logger.info(
                            f"check_and_disable: REMOVED_UUID [action=expire_realtime, user={telegram_id}, "
                            f"uuid={uuid_preview}]"
                        )
                        
                        # VPN AUDIT LOG: Логируем успешное удаление UUID при real-time проверке
                        try:
                            # Явно определяем expires_at из subscription
                            subscription_expires_at = subscription.get("expires_at")
                            expires_at_str = subscription_expires_at.isoformat() if subscription_expires_at else "N/A"
                            await _log_vpn_lifecycle_audit_async(
                                action="vpn_expire",
                                telegram_id=telegram_id,
                                uuid=uuid,
                                source="auto-expiry",
                                result="success",
                                details=f"Real-time expiration check, expires_at={expires_at_str}"
                            )
                        except Exception as e:
                            logger.warning(f"Failed to log VPN expire audit (non-blocking): {e}")
                    except ValueError as e:
                        # VPN API не настроен - логируем и помечаем как expired в БД (UUID уже неактивен)
                        if "VPN API is not configured" in str(e):
                            logger.warning(
                                f"check_and_disable: VPN_API_DISABLED [action=expire_realtime_skip_remove, "
                                f"user={telegram_id}, uuid={uuid}] - marking as expired in DB only"
                            )
                            # Помечаем как expired в БД, даже если не удалось удалить из VPN API
                        else:
                            logger.error(
                                f"check_and_disable: ERROR_REMOVING_UUID [action=expire_realtime_failed, "
                                f"user={telegram_id}, uuid={uuid}, error={str(e)}]"
                            )
                            # Не помечаем как expired если не удалось удалить - повторим в cleanup
                            return False
                    except Exception as e:
                        logger.error(
                            f"check_and_disable: ERROR_REMOVING_UUID [action=expire_realtime_failed, "
                            f"user={telegram_id}, uuid={uuid}, error={str(e)}]"
                        )
                        # Не помечаем как expired если не удалось удалить - повторим в cleanup
                        return False
                
                # Очищаем данные в БД - помечаем как expired
                await conn.execute(
                    """UPDATE subscriptions 
                       SET status = 'expired', uuid = NULL, vpn_key = NULL 
                       WHERE telegram_id = $1 AND expires_at <= $2 AND status = 'active'""",
                    telegram_id, _to_db_utc(now)
                )
                
                return True
                
            except Exception as e:
                logger.exception(f"Error in check_and_disable_expired_subscription for user {telegram_id}")
                return False


async def get_subscription(telegram_id: int) -> Optional[Dict[str, Any]]:
    """Получить активную подписку пользователя
    
    Активной считается подписка, у которой:
    - status = 'active'
    - expires_at > текущего времени
    
    НЕ фильтрует по source (payment/admin/test) - все подписки равны.
    
    Перед возвратом проверяет и отключает истёкшие подписки.
    """
    # Сначала проверяем и отключаем истёкшие подписки
    await check_and_disable_expired_subscription(telegram_id)
    
    # Защита от работы с неинициализированной БД
    if not DB_READY:
        logger.warning("DB not ready, get_subscription skipped")
        return None
    pool = await get_pool()
    if pool is None:
        logger.warning("Pool is None, get_subscription skipped")
        return None
    async with pool.acquire() as conn:
        now = datetime.now(timezone.utc)
        row = await conn.fetchrow(
            "SELECT * FROM subscriptions WHERE telegram_id = $1 AND status = 'active' AND expires_at > $2",
            telegram_id, _to_db_utc(now)
        )
        return _normalize_subscription_row(row) if row else None


async def get_subscription_any(telegram_id: int) -> Optional[Dict[str, Any]]:
    """Получить подписку пользователя независимо от статуса (активная или истекшая)
    
    Возвращает подписку, если она существует, даже если expires_at <= now.
    """
    # Защита от работы с неинициализированной БД
    if not DB_READY:
        logger.warning("DB not ready, get_subscription_any skipped")
        return None
    pool = await get_pool()
    if pool is None:
        logger.warning("Pool is None, get_subscription_any skipped")
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM subscriptions WHERE telegram_id = $1",
            telegram_id
        )
        return _normalize_subscription_row(row) if row else None


async def has_any_subscription(telegram_id: int) -> bool:
    """Проверить, есть ли у пользователя хотя бы одна подписка (любого статуса)
    
    Returns:
        True если есть хотя бы одна запись в subscriptions, False иначе
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT 1 FROM subscriptions WHERE telegram_id = $1 LIMIT 1",
            telegram_id
        )
        return row is not None


async def has_any_payment(telegram_id: int) -> bool:
    """Проверить, есть ли у пользователя хотя бы один платёж (любого статуса)
    
    Returns:
        True если есть хотя бы одна запись в payments, False иначе
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT 1 FROM payments WHERE telegram_id = $1 LIMIT 1",
            telegram_id
        )
        return row is not None


async def has_trial_used(telegram_id: int) -> bool:
    """Проверить, использовал ли пользователь trial-период
    
    Trial считается использованным, если trial_used_at IS NOT NULL
    
    Returns:
        True если trial уже использован, False иначе
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT trial_used_at FROM users WHERE telegram_id = $1",
            telegram_id
        )
        if not row:
            return False
        return row["trial_used_at"] is not None


async def get_trial_info(telegram_id: int) -> Optional[Dict[str, Any]]:
    """Получить информацию о trial для пользователя
    
    Returns:
        Dict с trial_used_at и trial_expires_at или None если пользователь не найден
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT trial_used_at, trial_expires_at FROM users WHERE telegram_id = $1",
            telegram_id
        )
        if not row:
            return None
        return {
            "trial_used_at": _from_db_utc(row["trial_used_at"]) if row["trial_used_at"] else None,
            "trial_expires_at": _from_db_utc(row["trial_expires_at"]) if row["trial_expires_at"] else None
        }


async def get_active_paid_subscription(conn, telegram_id: int, now: datetime):
    """Single source of truth: does user have an active paid (non-trial) subscription?
    Paid subscription ALWAYS overrides trial logic. Used by trial_notifications and
    fast_expiry_cleanup to skip trial notifications and trial cleanup when paid exists.
    Returns: row with expires_at or None. Caller must pass existing conn (same transaction)."""
    return await conn.fetchrow("""
        SELECT expires_at FROM subscriptions
        WHERE telegram_id = $1 AND source != 'trial' AND status = 'active' AND expires_at > $2
        LIMIT 1
    """, telegram_id, _to_db_utc(now))


async def mark_trial_used(telegram_id: int, trial_expires_at: datetime) -> bool:
    """Пометить trial как использованный
    
    Args:
        telegram_id: Telegram ID пользователя
        trial_expires_at: Время окончания trial (now + 72 hours)
    
    Returns:
        True если успешно, False иначе
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            await conn.execute("""
                UPDATE users 
                SET trial_used_at = CURRENT_TIMESTAMP,
                    trial_expires_at = $1
                WHERE telegram_id = $2
            """, _to_db_utc(trial_expires_at), telegram_id)
            logger.info(f"Trial marked as used: user={telegram_id}, expires_at={trial_expires_at.isoformat()}")
            return True
        except Exception as e:
            logger.error(f"Error marking trial as used for user {telegram_id}: {e}")
            return False


async def is_eligible_for_trial(telegram_id: int) -> bool:
    """Проверить, может ли пользователь активировать trial-период
    
    Пользователь может активировать trial ТОЛЬКО если:
    - trial_used_at IS NULL (trial ещё не использован)
    
    ВАЖНО: Наличие подписок или платежей НЕ влияет на eligibility.
    Trial может быть активирован даже если есть активная подписка.
    
    Returns:
        True если пользователь может активировать trial, False иначе
    """
    # КРИТИЧНО: Проверяем ТОЛЬКО trial_used_at
    # Наличие подписок или платежей НЕ блокирует trial
    trial_used = await has_trial_used(telegram_id)
    return not trial_used


async def is_trial_available(telegram_id: int) -> bool:
    """Проверить, доступна ли кнопка "Пробный период 3 дня" в главном меню
    
    Кнопка показывается ТОЛЬКО если ВСЕ условия выполнены:
    1. trial_used_at IS NULL (trial ещё не использован)
    2. Нет активной подписки (status='active' AND expires_at > now)
    3. Нет платных подписок в истории (source='payment')
    
    Returns:
        True если кнопка должна быть показана, False иначе
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        now = datetime.now(timezone.utc)
        
        # Проверка 1: trial_used_at IS NULL
        user_row = await conn.fetchrow(
            "SELECT trial_used_at FROM users WHERE telegram_id = $1",
            telegram_id
        )
        if not user_row:
            return False
        
        if user_row["trial_used_at"] is not None:
            return False
        
        # Проверка 2: Нет активной подписки
        active_subscription = await conn.fetchrow(
            """SELECT 1 FROM subscriptions 
               WHERE telegram_id = $1 
               AND status = 'active' 
               AND expires_at > $2
               LIMIT 1""",
            telegram_id, _to_db_utc(now)
        )
        if active_subscription:
            return False
        
        # Проверка 3: Нет платных подписок в истории (source='payment')
        paid_subscription = await conn.fetchrow(
            """SELECT 1 FROM subscriptions 
               WHERE telegram_id = $1 
               AND source = 'payment'
               LIMIT 1""",
            telegram_id
        )
        if paid_subscription:
            return False
        
        return True


async def get_active_subscription(subscription_id: int) -> Optional[Dict[str, Any]]:
    """Получить активную подписку по ID
    
    Args:
        subscription_id: ID подписки
    
    Returns:
        Словарь с данными подписки или None, если:
        - подписка не найдена
        - статус != "active"
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        now = datetime.now(timezone.utc)
        row = await conn.fetchrow(
            """SELECT * FROM subscriptions 
               WHERE id = $1 
               AND status = 'active' 
               AND expires_at > $2""",
            subscription_id, _to_db_utc(now)
        )
        return _normalize_subscription_row(row) if row else None


async def update_subscription_uuid(subscription_id: int, new_uuid: str, vpn_key: Optional[str] = None) -> None:
    """Обновить UUID подписки (и vpn_key при перевыпуске)
    
    Args:
        subscription_id: ID подписки
        new_uuid: Новый UUID
        vpn_key: VLESS URL (опционально, при перевыпуске)
    
    Note:
        НЕ меняет статус
        НЕ трогает даты
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        if vpn_key is not None:
            await conn.execute(
                "UPDATE subscriptions SET uuid = $1, vpn_key = $2 WHERE id = $3",
                new_uuid, vpn_key, subscription_id
            )
        else:
            await conn.execute(
                "UPDATE subscriptions SET uuid = $1 WHERE id = $2",
                new_uuid, subscription_id
            )
        logger.info(f"Subscription UUID updated: subscription_id={subscription_id}, new_uuid={new_uuid[:8]}...")


async def get_all_active_subscriptions() -> List[Dict[str, Any]]:
    """Получить все активные подписки
    
    Returns:
        Список подписок со статусом 'active' и expires_at > now
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        now = datetime.now(timezone.utc)
        rows = await conn.fetch(
            """SELECT * FROM subscriptions 
               WHERE status = 'active' 
               AND expires_at > $1
               ORDER BY id ASC""",
            _to_db_utc(now)
        )
        return [_normalize_subscription_row(row) for row in rows]


async def reissue_subscription_key(subscription_id: int) -> str:
    """Перевыпустить VPN ключ для подписки (сервисная функция)
    
    Алгоритм:
    1) Получить подписку через get_active_subscription
    2) Если None → выбросить бизнес-ошибку
    3) Сохранить old_uuid
    4) Вызвать reissue_vpn_access(old_uuid)
    5) Получить new_uuid
    6) Обновить uuid в БД через update_subscription_uuid
    7) Вернуть new_uuid
    
    Args:
        subscription_id: ID подписки
    
    Returns:
        Новый UUID (str)
    
    Raises:
        ValueError: Если подписка не найдена или не активна
        VPNAPIError: При ошибках VPN API
    """
    # 1. Получаем активную подписку
    subscription = await get_active_subscription(subscription_id)
    if not subscription:
        error_msg = f"Subscription {subscription_id} not found or not active"
        logger.error(f"reissue_subscription_key: {error_msg}")
        raise ValueError(error_msg)
    
    old_uuid = subscription.get("uuid")
    if not old_uuid:
        error_msg = f"Subscription {subscription_id} has no UUID"
        logger.error(f"reissue_subscription_key: {error_msg}")
        raise ValueError(error_msg)
    
    telegram_id = subscription.get("telegram_id")
    uuid_preview = f"{old_uuid[:8]}..." if old_uuid and len(old_uuid) > 8 else (old_uuid or "N/A")
    logger.info(
        f"reissue_subscription_key: START [subscription_id={subscription_id}, "
        f"telegram_id={telegram_id}, old_uuid={uuid_preview}]"
    )
    
    # 2. Перевыпускаем VPN доступ
    expires_at_raw = subscription.get("expires_at")
    expires_at = _ensure_utc(expires_at_raw) if expires_at_raw else None
    if not expires_at:
        error_msg = f"Subscription {subscription_id} has no expires_at"
        logger.error(f"reissue_subscription_key: {error_msg}")
        raise ValueError(error_msg)
    
    try:
        new_uuid = await vpn_utils.reissue_vpn_access(
            old_uuid=old_uuid,
            telegram_id=telegram_id,
            subscription_end=expires_at
        )
    except Exception as e:
        logger.error(
            f"reissue_subscription_key: VPN_API_FAILED [subscription_id={subscription_id}, "
            f"telegram_id={telegram_id}, error={str(e)}]"
        )
        raise
    
    vless_url = vpn_utils.generate_vless_url(new_uuid)
    
    # 3. Обновляем UUID и vpn_key в БД
    try:
        await update_subscription_uuid(subscription_id, new_uuid, vpn_key=vless_url)
    except Exception as e:
        logger.error(
            f"reissue_subscription_key: DB_UPDATE_FAILED [subscription_id={subscription_id}, "
            f"telegram_id={telegram_id}, new_uuid={new_uuid[:8]}..., error={str(e)}]"
        )
        # КРИТИЧНО: UUID в VPN API уже обновлён, но БД не обновлена
        # Это несоответствие, но мы не можем откатить VPN API
        raise
    
    new_uuid_preview = f"{new_uuid[:8]}..." if new_uuid and len(new_uuid) > 8 else (new_uuid or "N/A")
    logger.info(
        f"reissue_subscription_key: SUCCESS [subscription_id={subscription_id}, "
        f"telegram_id={telegram_id}, old_uuid={uuid_preview}, new_uuid={new_uuid_preview}]"
    )
    
    return new_uuid


async def create_subscription(telegram_id: int, vpn_key: str, months: int) -> Tuple[datetime, bool]:
    """
    DEPRECATED: Эта функция обходит grant_access() и НЕ должна использоваться.
    
    Используйте grant_access() вместо этой функции.
    Эта функция оставлена только для обратной совместимости и будет удалена.
    
    Raises:
        Exception: Всегда, так как эта функция устарела
    """
    error_msg = (
        "create_subscription() is DEPRECATED and should not be used. "
        "Use grant_access() instead. This function bypasses VPN API and UUID management."
    )
    logger.error(f"DEPRECATED create_subscription() called for user {telegram_id}: {error_msg}")
    raise Exception(error_msg)


# Функция get_free_vpn_keys_count удалена - больше не используется
# VPN-ключи теперь создаются динамически через Outline API, лимита нет


async def _log_audit_event_atomic(
    conn,
    action: str,
    telegram_id: int,
    target_user: Optional[int] = None,
    details: Optional[str] = None,
    correlation_id: Optional[str] = None
):
    """
    Записать событие аудита в таблицу audit_log
    
    STEP 5 — COMPLIANCE & AUDITABILITY:
    Must be called ONLY within an active transaction.
    
    PART F — FAILURE SAFETY:
    Non-blocking, best-effort. Never throws exceptions.
    
    Args:
        conn: Database connection (within transaction)
        action: Action type (e.g., 'payment_approved', 'payment_rejected', 'vpn_key_issued', 'subscription_renewed')
        telegram_id: Telegram ID of the actor
        target_user: Telegram ID of the target user (optional)
        details: Additional details (optional, JSON string)
        correlation_id: Correlation ID for tracing (optional)
    """
    try:
        # STEP 5 — PART F: FAILURE SAFETY
        # Try to insert with correlation_id if column exists, fallback to without it
        try:
            await conn.execute(
                """INSERT INTO audit_log (action, telegram_id, target_user, details, correlation_id)
                   VALUES ($1, $2, $3, $4, $5)""",
                action, telegram_id, target_user, details, correlation_id
            )
        except (asyncpg.UndefinedColumnError, asyncpg.PostgresError):
            # Fallback if correlation_id column doesn't exist yet
            await conn.execute(
                """INSERT INTO audit_log (action, telegram_id, target_user, details)
                   VALUES ($1, $2, $3, $4)""",
                action, telegram_id, target_user, details
            )
    except (asyncpg.UndefinedTableError, asyncpg.PostgresError) as e:
        # STEP 5 — PART F: FAILURE SAFETY
        # Log warning but never throw
        logger.warning(f"audit_log table missing or inaccessible — skipping audit log: action={action}, telegram_id={telegram_id}")
    except Exception as e:
        # STEP 5 — PART F: FAILURE SAFETY
        # Log warning but never throw
        logger.warning(f"Error logging audit event: {e}")


async def _log_vpn_lifecycle_audit_async(
    action: str,
    telegram_id: int,
    uuid: Optional[str] = None,
    source: Optional[str] = None,
    result: str = "success",
    details: Optional[str] = None
):
    """
    Записать событие VPN lifecycle в audit_log (async, non-blocking).
    
    Используется для логирования:
    - add_user: создание UUID через VPN API
    - remove_user: удаление UUID через VPN API
    - renew: продление подписки (без создания UUID)
    - expire: автоматическое истечение подписки
    
    Не блокирует основной flow - ошибки логируются, но не пробрасываются.
    
    Args:
        action: Тип действия ('vpn_add_user', 'vpn_remove_user', 'vpn_renew', 'vpn_expire')
        telegram_id: Telegram ID пользователя
        uuid: UUID пользователя (опционально, частично логируется для безопасности)
        source: Источник ('payment', 'admin', 'auto-expiry', 'test')
        result: Результат операции ('success' или 'error')
        details: Дополнительные детали (опционально)
    """
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            # Безопасное логирование UUID (только первые 8 символов в БД)
            uuid_safe = f"{uuid[:8]}..." if uuid and len(uuid) > 8 else (uuid or None)
            
            await conn.execute(
                """INSERT INTO audit_log (action, telegram_id, target_user, uuid, source, result, details)
                   VALUES ($1, $2, $3, $4, $5, $6, $7)""",
                action, telegram_id, telegram_id, uuid_safe, source, result, details
            )
            logger.debug(
                f"VPN audit logged: action={action}, user={telegram_id}, uuid={uuid_safe}, "
                f"source={source}, result={result}"
            )
    except Exception as e:
        # Не блокируем основной flow при ошибках логирования
        logger.warning(f"Failed to log VPN audit event: action={action}, user={telegram_id}, error={e}")


def _log_vpn_lifecycle_audit_fire_and_forget(
    action: str,
    telegram_id: int,
    uuid: Optional[str] = None,
    source: Optional[str] = None,
    result: str = "success",
    details: Optional[str] = None
):
    """
    Записать событие VPN lifecycle в audit_log (fire-and-forget, не блокирует).
    
    Создаёт async task для логирования, не ожидает завершения.
    Используется когда нужно залогировать событие вне async контекста.
    """
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Если event loop уже запущен, создаём task
            asyncio.create_task(
                _log_vpn_lifecycle_audit_async(action, telegram_id, uuid, source, result, details)
            )
        else:
            # Если event loop не запущен, запускаем корутину
            asyncio.run(_log_vpn_lifecycle_audit_async(action, telegram_id, uuid, source, result, details))
    except Exception as e:
        # Не блокируем основной flow
        logger.warning(f"Failed to schedule VPN audit log: action={action}, user={telegram_id}, error={e}")


async def _log_subscription_history_atomic(conn, telegram_id: int, vpn_key: str, start_date: datetime, end_date: datetime, action_type: str):
    """Записать запись в историю подписок
    
    Должна вызываться ТОЛЬКО внутри активной транзакции.
    
    Args:
        conn: Соединение с БД (внутри транзакции)
        telegram_id: Telegram ID пользователя
        vpn_key: VPN-ключ (может быть None для pending activations)
        start_date: Дата начала периода
        end_date: Дата окончания периода
        action_type: Тип действия ('purchase', 'renewal', 'reissue', 'manual_reissue')
    """
    # Пропускаем запись истории для pending activations (vpn_key == None)
    # История будет записана позже, когда activation_worker активирует подписку
    if vpn_key is None:
        logger.info(
            f"SUBSCRIPTION_HISTORY_SKIPPED [reason=pending_activation, user={telegram_id}, "
            f"action={action_type}, subscription_end={end_date.isoformat()}]"
        )
        return
    
    await conn.execute(
        """INSERT INTO subscription_history (telegram_id, vpn_key, start_date, end_date, action_type)
           VALUES ($1, $2, $3, $4, $5)""",
        telegram_id, vpn_key, _to_db_utc(start_date), _to_db_utc(end_date), action_type
    )


async def _log_audit_event_atomic_standalone(
    action: str,
    telegram_id: int,
    target_user: Optional[int] = None,
    details: Optional[str] = None,
    correlation_id: Optional[str] = None
):
    """
    Записать событие аудита в таблицу audit_log (standalone версия)
    
    STEP 5 — COMPLIANCE & AUDITABILITY:
    Creates its own transaction. Used when audit event needs to be logged outside existing transaction.
    
    PART F — FAILURE SAFETY:
    Non-blocking, best-effort. Never throws exceptions.
    
    Args:
        action: Тип действия (например, 'payment_approved', 'payment_rejected', 'vpn_key_issued', 'subscription_renewed')
        telegram_id: Telegram ID администратора, который выполнил действие
        target_user: Telegram ID пользователя, над которым выполнено действие (опционально)
        details: Дополнительные детали действия (опционально, JSON string)
        correlation_id: Correlation ID for tracing (optional)
    """
    if not DB_READY:
        logger.warning("DB not ready (degraded mode), audit log skipped")
        return
    
    pool = await get_pool()
    if pool is None:
        logger.warning("Pool is None, audit log skipped")
        return
    
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                await _log_audit_event_atomic(conn, action, telegram_id, target_user, details, correlation_id)
    except (asyncpg.UndefinedTableError, asyncpg.PostgresError) as e:
        # STEP 5 — PART F: FAILURE SAFETY
        # Log warning but never throw
        logger.warning(f"audit_log table missing or inaccessible — skipping audit log: action={action}, telegram_id={telegram_id}")
    except Exception as e:
        # STEP 5 — PART F: FAILURE SAFETY
        # Log warning but never throw
        logger.warning(f"Error logging audit event (standalone): {e}")


async def reissue_vpn_key_atomic(
    telegram_id: int,
    admin_telegram_id: int,
    correlation_id: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """Атомарно перевыпустить VPN-ключ для пользователя
    
    Перевыпуск возможен ТОЛЬКО если у пользователя есть активная подписка.
    В одной транзакции:
    - pg_advisory_xact_lock (cross-process) — гарантирует один reissue на user_id
    - удаляет старый UUID из Xray API (POST /remove-user/{uuid})
    - создает новый UUID через Xray API (POST /add-user)
    - обновляет subscriptions (uuid, vpn_key)
    - expires_at НЕ меняется (подписка не продлевается)
    - записывает событие в audit_log
    
    Args:
        telegram_id: Telegram ID пользователя
        admin_telegram_id: Telegram ID администратора, который выполняет перевыпуск
        correlation_id: Опциональный ID для корреляции логов
    
    Returns:
        (new_vpn_key, old_vpn_key) или (None, None) если нет активной подписки или ошибка создания ключа
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            try:
                # STEP 4 — POSTGRES ADVISORY LOCK (cross-process safe)
                await conn.execute("SELECT pg_advisory_xact_lock($1)", telegram_id)

                # 1. Проверяем, что у пользователя есть активная подписку
                # КРИТИЧНО: Проверяем status='active', а не только expires_at
                now = datetime.now(timezone.utc)
                subscription_row = await conn.fetchrow(
                    """SELECT * FROM subscriptions 
                       WHERE telegram_id = $1 
                       AND status = 'active' 
                       AND expires_at > $2""",
                    telegram_id, _to_db_utc(now)
                )
                
                if not subscription_row:
                    logger.error(f"Cannot reissue VPN key for user {telegram_id}: no active subscription")
                    return None, None
                
                subscription = dict(subscription_row)
                old_uuid = subscription.get("uuid")
                old_vpn_key = subscription.get("vpn_key", "")
                expires_at = _ensure_utc(subscription["expires_at"])
                
                # 2. Удаляем старый UUID из Xray API (POST /remove-user/{uuid})
                if old_uuid:
                    try:
                        await vpn_utils.remove_vless_user(old_uuid)
                        # Безопасное логирование UUID
                        old_uuid_preview = f"{old_uuid[:8]}..." if old_uuid and len(old_uuid) > 8 else (old_uuid or "N/A")
                        logger.info(
                            f"VPN key reissue [action=remove_old, user={telegram_id}, "
                            f"old_uuid={old_uuid_preview}, reason=admin_reissue]"
                        )
                        
                        # VPN AUDIT LOG: Логируем удаление старого UUID
                        try:
                            await _log_vpn_lifecycle_audit_async(
                                action="vpn_remove_user",
                                telegram_id=telegram_id,
                                uuid=old_uuid,
                                source="admin_reissue",
                                result="success",
                                details=f"Old UUID removed during admin reissue, expires_at={expires_at.isoformat()}"
                            )
                        except Exception as e:
                            logger.warning(f"Failed to log VPN remove_user audit (non-blocking): {e}")
                    except Exception as e:
                        logger.warning(f"Failed to delete old UUID {old_uuid} for user {telegram_id}: {e}")
                        # VPN AUDIT LOG: Логируем ошибку удаления
                        try:
                            await _log_vpn_lifecycle_audit_async(
                                action="vpn_remove_user",
                                telegram_id=telegram_id,
                                uuid=old_uuid,
                                source="admin_reissue",
                                result="error",
                                details=f"Failed to remove old UUID: {str(e)}"
                            )
                        except Exception:
                            pass
                        # Продолжаем, даже если не удалось удалить старый UUID (идемпотентность)
                
                # 3. Xray generates UUID; returned UUID is canonical
                try:
                    from app.core.system_state import recalculate_from_runtime, ComponentStatus
                    system_state = recalculate_from_runtime()
                    if system_state.vpn_api.status != ComponentStatus.HEALTHY:
                        logger.warning(
                            f"reissue_vpn_access: VPN_API_DISABLED [user={telegram_id}] - "
                            f"VPN API is {system_state.vpn_api.status.value}, skipping VPN call"
                        )
                        raise Exception(f"VPN API is {system_state.vpn_api.status.value}, cannot reissue access")
                    vless_result = await vpn_utils.add_vless_user(
                        telegram_id=telegram_id,
                        subscription_end=expires_at,
                        uuid=None
                    )
                    new_uuid = vless_result.get("uuid")
                    new_vpn_key = vless_result.get("vless_url")
                except Exception as e:
                    if "VPN API is" in str(e):
                        raise
                    vless_result = await vpn_utils.add_vless_user(
                        telegram_id=telegram_id,
                        subscription_end=expires_at,
                        uuid=None
                    )
                    new_uuid = vless_result.get("uuid")
                    new_vpn_key = vless_result.get("vless_url")
                    
                    # VPN AUDIT LOG: Логируем создание нового UUID
                    try:
                        await _log_vpn_lifecycle_audit_async(
                            action="vpn_add_user",
                            telegram_id=telegram_id,
                            uuid=new_uuid,
                            source="admin_reissue",
                            result="success",
                            details=f"New UUID created during admin reissue, expires_at={expires_at.isoformat()}"
                        )
                    except Exception as e:
                        logger.warning(f"Failed to log VPN add_user audit (non-blocking): {e}")
                except Exception as e:
                    logger.error(f"Failed to create VLESS user for reissue for user {telegram_id}: {e}")
                    # VPN AUDIT LOG: Логируем ошибку создания
                    try:
                        await _log_vpn_lifecycle_audit_async(
                            action="vpn_add_user",
                            telegram_id=telegram_id,
                            uuid=None,
                            source="admin_reissue",
                            result="error",
                            details=f"Failed to create new UUID: {str(e)}"
                        )
                    except Exception:
                        pass
                    return None, None
                
                # 4. Обновляем подписку (expires_at НЕ меняется - подписка не продлевается)
                await conn.execute(
                    "UPDATE subscriptions SET uuid = $1, vpn_key = $2 WHERE telegram_id = $3",
                    new_uuid, new_vpn_key, telegram_id
                )
                
                # 5. Записываем в историю подписок
                await _log_subscription_history_atomic(conn, telegram_id, new_vpn_key, now, expires_at, "manual_reissue")
                
                # 6. Записываем событие в audit_log (legacy, для совместимости)
                old_key_preview = f"{old_vpn_key[:20]}..." if old_vpn_key and len(old_vpn_key) > 20 else (old_vpn_key or "N/A")
                new_key_preview = f"{new_vpn_key[:20]}..." if new_vpn_key and len(new_vpn_key) > 20 else (new_vpn_key or "N/A")
                details = f"User {telegram_id}, Old key: {old_key_preview}, New key: {new_key_preview}, Expires: {expires_at.isoformat()}"
                await _log_audit_event_atomic(conn, "admin_reissue", admin_telegram_id, telegram_id, details)
                
                # Безопасное логирование UUID
                new_uuid_preview = f"{new_uuid[:8]}..." if new_uuid and len(new_uuid) > 8 else (new_uuid or "N/A")
                log_extra = {"user": telegram_id, "admin": admin_telegram_id, "new_uuid": new_uuid_preview}
                if correlation_id:
                    log_extra["correlation_id"] = correlation_id
                logger.info(
                    f"VPN key reissued [action=admin_reissue, user={telegram_id}, admin={admin_telegram_id}, "
                    f"new_uuid={new_uuid_preview}, expires_at={expires_at.isoformat()}]",
                    extra=log_extra,
                )
                return new_vpn_key, old_vpn_key
                
            except Exception as e:
                logger.exception(f"Error in reissue_vpn_key_atomic for user {telegram_id}, transaction rolled back")
                raise


"""
SINGLE SOURCE OF TRUTH: grant_access

ЕДИНАЯ ФУНКЦИЯ ВЫДАЧИ ДОСТУПА
Это единственное место, где:
- UUID создаются
- subscription_end изменяется
- VPN API вызывается

КРИТИЧЕСКИЕ ПРАВИЛА:
1. UUID НЕ МЕНЯЕТСЯ пока подписка активна
2. UUID УДАЛЯЕТСЯ немедленно при истечении
3. Admin-подписки ведут себя ИДЕНТИЧНО платным
4. Продление расширяет subscription_end, никогда не заменяет UUID
5. Истекшая подписка → новая покупка → новый UUID
"""


async def grant_access(
    telegram_id: int,
    duration: timedelta,
    source: str,
    admin_telegram_id: Optional[int] = None,
    admin_grant_days: Optional[int] = None,
    conn=None
) -> Dict[str, Any]:
    """
    ЕДИНАЯ ФУНКЦИЯ ВЫДАЧИ ДОСТУПА (SINGLE SOURCE OF TRUTH)
    
    Это ЕДИНСТВЕННОЕ место, где:
    - UUID создаются (через vpn_utils.add_vless_user)
    - subscription_end изменяется
    - VPN API вызывается для создания нового UUID
    
    КРИТИЧЕСКИ ВАЖНО: UUID остаётся стабильным при продлении подписки.
    VPN API /add-user вызывается ТОЛЬКО если нет активного UUID.
    
    ЛОГИКА (СТРОГАЯ):
    Step 1: Получить текущую подписку для telegram_id
    
    Step 2: RENEWAL (продление)
    IF subscription exists AND status == "active" AND expires_at > now() AND uuid IS NOT NULL:
        - НЕ вызывать VPN API /add-user
        - НЕ менять UUID (UUID остаётся стабильным)
        - Только: subscription_end = expires_at + duration
        - Обновить БД
        - Вернуть: {uuid: existing, vless_url: None, subscription_end: new_date, action: "renewal"}
        - Результат: VPN соединение НЕ прерывается
    
    Step 3: NEW ISSUANCE (новая выдача)
    IF no subscription OR status == "expired" OR uuid IS NULL:
        - Вызвать VPN API POST /add-user
        - Получить {uuid, vless_url}
        - Создать/обновить подписку:
            - subscription_start = now (activated_at)
            - subscription_end = now + duration
            - status = "active"
            - source = source
            - uuid = new_uuid
            - vpn_key = vless_url
        - Вернуть: {uuid: new, vless_url: new_link, subscription_end: new_date, action: "new_issuance"}
        - Результат: Пользователь получает новый VLESS ключ
    
    ЗАЩИТА ОТ ДВОЙНОГО СОЗДАНИЯ UUID:
    - UUID создаётся ТОЛЬКО в этой функции
    - Проверка активности подписки перед созданием UUID
    - Атомарные транзакции БД
    
    Args:
        telegram_id: Telegram ID пользователя
        duration: Продолжительность доступа (timedelta)
        source: Источник выдачи ('payment', 'admin', 'test')
        admin_telegram_id: Telegram ID администратора (опционально, для admin-источников)
        admin_grant_days: Количество дней для админ-доступа (опционально)
        conn: Соединение с БД (если None, создаётся новое)
    
    Returns:
        Dict[str, Any] with keys:
            - "uuid": Optional[str] - UUID (None for pending activation)
            - "vless_url": Optional[str] - VLESS URL (None for renewal, present for new issuance)
            - "subscription_end": datetime - Subscription expiration date
            - "action": str - "renewal", "new_issuance", or "pending_activation"
        
        Guaranteed to return a dict. Never returns None.
    
    Raises:
        Exception: При любых ошибках (транзакция откатывается, исключение пробрасывается)
    """
    if conn is None:
        pool = await get_pool()
        conn = await pool.acquire()
        should_release_conn = True
    else:
        should_release_conn = False
    
    try:
        now = datetime.now(timezone.utc)
        
        # Логируем начало операции с полными данными
        duration_str = f"{duration.days} days" if duration.days > 0 else f"{int(duration.total_seconds() / 60)} minutes"
        logger.info(f"grant_access: START [telegram_id={telegram_id}, source={source}, duration={duration_str}]")
        
        # =====================================================================
        # STEP 1: Получить текущую подписку
        # =====================================================================
        subscription_row = await conn.fetchrow(
            "SELECT * FROM subscriptions WHERE telegram_id = $1",
            telegram_id
        )
        subscription = dict(subscription_row) if subscription_row else None
        logger.debug(f"grant_access: GET_SUBSCRIPTION [user={telegram_id}, exists={subscription is not None}]")
        
        # Определяем статус подписки
        if subscription:
            expires_at_raw = subscription.get("expires_at")
            expires_at = _ensure_utc(expires_at_raw) if expires_at_raw else None
            db_status = subscription.get("status")
            uuid = subscription.get("uuid")
            
            # КРИТИЧЕСКАЯ ПРОВЕРКА: Подписка активна ТОЛЬКО если:
            # 1. status = 'active' И
            # 2. expires_at > now() И
            # 3. uuid IS NOT NULL
            is_active = (
                db_status == "active" and
                expires_at and
                expires_at > now and
                uuid is not None
            )
            
            if not is_active:
                # Подписка неактивна (истекла или нет UUID)
                status = "expired"
            else:
                status = "active"
        else:
            status = None
            expires_at = None
            uuid = None
        
        # =====================================================================
        # STEP 2: Активная подписка - ПРОДЛЕНИЕ (без создания нового UUID)
        # =====================================================================
        # КРИТИЧЕСКОЕ ПРОВЕРКА: Подписка активна если:
        # 1. subscription существует
        # 2. status == 'active'
        # 3. expires_at > now() (не истекла)
        # 4. uuid IS NOT NULL (UUID существует)
        if subscription and status == "active" and uuid and expires_at and expires_at > now:
            # UUID_AUDIT_DB_VALUE: Trace UUID from DB for renewal (identical across DB/Xray, no transformation)
            logger.info(
                f"UUID_AUDIT_DB_VALUE [telegram_id={telegram_id}, uuid_from_db={uuid[:8] if uuid else 'N/A'}..., repr={repr(uuid)}]"
            )
            # UUID СТАБИЛЕН - продлеваем подписку БЕЗ вызова VPN API
            logger.info(
                f"grant_access: RENEWAL_DETECTED [user={telegram_id}, current_expires={expires_at.isoformat()}, "
                f"uuid={uuid[:8] if uuid else 'N/A'}..., source={source}] - "
                "Active subscription found, will EXTEND without UUID regeneration"
            )
            # ЗАЩИТА: Не продлеваем если UUID отсутствует (не должно быть, но на всякий случай)
            if not uuid:
                logger.warning(
                    f"grant_access: WARNING_ACTIVE_WITHOUT_UUID [user={telegram_id}, "
                    f"will create new UUID instead of renewal]"
                )
                # Переходим к созданию нового UUID (Step 3)
            else:
                # UUID НЕ МЕНЯЕТСЯ - только продлеваем subscription_end
                old_expires_at = expires_at
                subscription_end = max(expires_at, now) + duration
                # subscription_start сохраняется (activated_at не меняется при продлении)
                _start_raw = subscription.get("activated_at") or subscription.get("expires_at") or now
                subscription_start = _ensure_utc(_start_raw) if _start_raw else now
                
                # ВАЛИДАЦИЯ: Проверяем что subscription_end увеличен
                if subscription_end <= old_expires_at:
                    error_msg = f"Invalid renewal: new_end={subscription_end} <= old_end={old_expires_at} for user {telegram_id}"
                    logger.error(f"grant_access: ERROR_INVALID_RENEWAL [user={telegram_id}, error={error_msg}]")
                    raise Exception(error_msg)
                
                logger.info(
                    f"grant_access: RENEWING_SUBSCRIPTION [user={telegram_id}, old_expires={old_expires_at.isoformat()}, "
                    f"new_expires={subscription_end.isoformat()}, extension_days={duration.days}, uuid={uuid[:8]}...] - "
                    "Extending subscription WITHOUT calling VPN API /add-user"
                )
                
                # TWO-PHASE SAFE RENEWAL: Xray FIRST, DB ONLY after Xray success.
                # ensure_user_in_xray: update → 200 OK; 404 → add with SAME uuid.
                assert subscription_end.tzinfo is not None, "subscription_end must be timezone-aware"
                assert subscription_end.tzinfo == timezone.utc, "subscription_end must be UTC"
                expiry_ms = int(subscription_end.timestamp() * 1000)
                logger.info(f"XRAY_UUID_FLOW [user={telegram_id}, uuid={uuid[:8]}..., operation=update]")
                xray_uuid = None
                try:
                    xray_uuid = await vpn_utils.ensure_user_in_xray(
                        telegram_id=telegram_id,
                        uuid=uuid,
                        subscription_end=subscription_end
                    )
                except Exception as e:
                    logger.critical(
                        f"XRAY_SYNC_FAILED_BUT_PAYMENT_OK "
                        f"[telegram_id={telegram_id}, error={e}]"
                    )
                if xray_uuid and xray_uuid != uuid:
                    uuid = xray_uuid
                    logger.info(f"XRAY_UUID_REPLACED [user={telegram_id}, new_uuid={uuid[:8]}...]")

                # PHASE 2: DB update
                # UUID НЕ МЕНЯЕТСЯ - VPN соединение продолжает работать без перерыва
                try:
                    await conn.execute(
                        """UPDATE subscriptions 
                           SET expires_at = $1, 
                               uuid = $4,
                               status = 'active',
                               source = $2,
                               reminder_sent = FALSE,
                               reminder_3d_sent = FALSE,
                               reminder_24h_sent = FALSE,
                               reminder_3h_sent = FALSE,
                               reminder_6h_sent = FALSE,
                               activation_status = 'active'
                           WHERE telegram_id = $3""",
                        _to_db_utc(subscription_end), source, telegram_id, uuid
                    )
                    
                    # ВАЛИДАЦИЯ: Проверяем что запись обновлена
                    updated_subscription = await conn.fetchrow(
                        "SELECT expires_at, status, uuid FROM subscriptions WHERE telegram_id = $1",
                        telegram_id
                    )
                    if not updated_subscription or _from_db_utc(updated_subscription["expires_at"]) != subscription_end:
                        error_msg = f"Failed to verify subscription renewal for user {telegram_id}"
                        logger.error(f"grant_access: ERROR_RENEWAL_VERIFICATION [user={telegram_id}, error={error_msg}]")
                        raise Exception(error_msg)
                    
                    logger.info(
                        f"grant_access: RENEWAL_SYNC_SUCCESS [telegram_id={telegram_id}, uuid={uuid[:8]}..., "
                        f"old_expiry={old_expires_at.isoformat()}, new_expiry={subscription_end.isoformat()}, "
                        f"expiry_timestamp_ms={expiry_ms}]"
                    )
                    logger.info(
                        f"grant_access: RENEWAL_SAVED_SUCCESS [user={telegram_id}, "
                        f"subscription_end={updated_subscription['expires_at'].isoformat()}, "
                        f"status={updated_subscription['status']}, uuid={uuid[:8]}...]"
                    )
                except Exception as e:
                    logger.error(f"grant_access: RENEWAL_SAVE_FAILED [user={telegram_id}, error={str(e)}]")
                    raise Exception(f"Failed to renew subscription in database: {e}") from e
                
                # WHY: При оплате во время trial явно завершаем trial и логируем — trial_notifications/cleanup не должны трогать paid
                if source == "payment":
                    user_row = await conn.fetchrow("SELECT trial_expires_at FROM users WHERE telegram_id = $1", telegram_id)
                    old_trial_expires_at = user_row["trial_expires_at"] if user_row else None
                    if old_trial_expires_at and _from_db_utc(old_trial_expires_at) > now:
                        await conn.execute(
                            "UPDATE users SET trial_expires_at = $1 WHERE telegram_id = $2 AND trial_expires_at > $1",
                            _to_db_utc(now), telegram_id
                        )
                        logger.info(
                            f"TRIAL_OVERRIDDEN_BY_PAID_SUBSCRIPTION: user_id={telegram_id}, "
                            f"old_trial_expires_at={old_trial_expires_at.isoformat()}, "
                            f"paid_subscription_expires_at={subscription_end.isoformat()}"
                        )
                
                # Определяем action_type для истории
                if source == "payment":
                    history_action_type = "renewal"
                elif source == "admin":
                    history_action_type = "admin_grant"
                else:
                    history_action_type = source
                
                # Записываем в историю подписок
                vpn_key = subscription.get("vpn_key") or subscription.get("uuid", "")
                await _log_subscription_history_atomic(conn, telegram_id, vpn_key, subscription_start, subscription_end, history_action_type)
                
                # Audit log
                if admin_telegram_id:
                    duration_str = f"{duration.days} days" if duration.days > 0 else f"{int(duration.total_seconds() / 60)} minutes"
                    uuid_preview = f"{uuid[:8]}..." if uuid and len(uuid) > 8 else (uuid or "N/A")
                    details = f"Renewed access: {duration_str} via {source}, Expires: {subscription_end.isoformat()}, UUID: {uuid_preview}"
                    await _log_audit_event_atomic(conn, "subscription_renewed", admin_telegram_id, telegram_id, details)
                
                # Безопасное логирование UUID (только первые 8 символов)
                uuid_preview = f"{uuid[:8]}..." if uuid and len(uuid) > 8 else (uuid or "N/A")
                duration_str = f"{duration.days} days" if duration.days > 0 else f"{int(duration.total_seconds() / 60)} minutes"
                extension_days = (subscription_end - old_expires_at).days if old_expires_at else duration.days
                logger.info(
                    f"grant_access: RENEWAL_SUCCESS [action=renewal, telegram_id={telegram_id}, uuid={uuid_preview}, "
                    f"subscription_start={subscription_start.isoformat()}, old_expires={old_expires_at.isoformat()}, "
                    f"new_expires={subscription_end.isoformat()}, extension={extension_days} days, "
                    f"source={source}, duration={duration_str}]"
                )
                logger.info(
                    f"grant_access: UUID_STABLE [action=renewal, telegram_id={telegram_id}, uuid={uuid_preview}] - "
                    "UUID preserved, VPN connection will NOT be interrupted"
                )
                
                # VPN AUDIT LOG: Логируем продление подписки (без создания UUID)
                try:
                    await _log_vpn_lifecycle_audit_async(
                        action="vpn_renew",
                        telegram_id=telegram_id,
                        uuid=uuid,
                        source=source,
                        result="success",
                        details=f"Subscription renewed, old_expires={old_expires_at.isoformat()}, new_expires={subscription_end.isoformat()}, extension={extension_days} days"
                    )
                except Exception as e:
                    logger.warning(f"Failed to log VPN renew audit (non-blocking): {e}")
                
                return {
                    "uuid": uuid,
                    "vless_url": None,  # Не новый UUID, URL не нужен (продление без разрыва соединения)
                    "subscription_end": subscription_end,
                    "action": "renewal"  # Явно указываем тип операции
                }
        
        # =====================================================================
        # STEP 3: Новая выдача доступа - создаём новый UUID
        # =====================================================================
        # Сюда попадаем если:
        # - подписки нет
        # - подписка истекла (expires_at <= now)
        # - статус не 'active'
        # - UUID отсутствует
        
        logger.info(
            f"grant_access: NEW_ISSUANCE_REQUIRED [user={telegram_id}, source={source}, "
            f"reason=no_active_subscription_or_expired] - "
            "Will create NEW UUID via VPN API /add-user"
        )
        
        # ЗАЩИТА: Проверяем доступность VPN API перед созданием UUID
        import config
        if not config.VPN_ENABLED:
            # PREMIUM FLOW: Delayed activation - create subscription with pending status
            # Payment succeeds, subscription is created, but VPN key will be generated later
            logger.info(
                f"grant_access: ACTIVATION_PENDING [user={telegram_id}, source={source}, "
                f"reason=VPN_API_not_available] - "
                "Creating subscription with pending activation status"
            )
            
            # Вычисляем даты
            subscription_start = now
            subscription_end = now + duration
            
            # ВАЛИДАЦИЯ: Проверяем что subscription_end вычислен корректно
            if not subscription_end or subscription_end <= subscription_start:
                error_msg = f"Invalid subscription_end for user {telegram_id}: start={subscription_start}, end={subscription_end}"
                logger.error(f"grant_access: ERROR_INVALID_DATES [user={telegram_id}, error={error_msg}]")
                raise Exception(error_msg)
            
            logger.info(
                f"grant_access: CALCULATED_DATES [user={telegram_id}, subscription_start={subscription_start.isoformat()}, "
                f"subscription_end={subscription_end.isoformat()}, duration_days={duration.days}]"
            )
            
            # Определяем action_type для истории
            if source == "payment":
                history_action_type = "purchase"
            elif source == "admin":
                history_action_type = "admin_grant"
            else:
                history_action_type = source
            
            # Сохраняем подписку с pending activation status
            try:
                await conn.execute(
                    """INSERT INTO subscriptions (
                           telegram_id, uuid, vpn_key, expires_at, status, source,
                           reminder_sent, reminder_3d_sent, reminder_24h_sent,
                           reminder_3h_sent, reminder_6h_sent, admin_grant_days,
                           activated_at, last_bytes,
                           trial_notif_6h_sent, trial_notif_18h_sent, trial_notif_30h_sent,
                           trial_notif_42h_sent, trial_notif_54h_sent, trial_notif_60h_sent,
                           trial_notif_71h_sent,
                           activation_status, activation_attempts, last_activation_error
                       )
                       VALUES ($1, NULL, NULL, $2, 'active', $3, FALSE, FALSE, FALSE, FALSE, FALSE, $4, $5, 0,
                               FALSE, FALSE, FALSE, FALSE, FALSE, FALSE, FALSE,
                               'pending', 0, NULL)
                       ON CONFLICT (telegram_id) 
                       DO UPDATE SET 
                           expires_at = $2,
                           status = 'active',
                           source = $3,
                           reminder_sent = FALSE,
                           reminder_3d_sent = FALSE,
                           reminder_24h_sent = FALSE,
                           reminder_3h_sent = FALSE,
                           reminder_6h_sent = FALSE,
                           admin_grant_days = $4,
                           activated_at = $5,
                           last_bytes = 0,
                           trial_notif_6h_sent = FALSE,
                           trial_notif_18h_sent = FALSE,
                           trial_notif_30h_sent = FALSE,
                           trial_notif_42h_sent = FALSE,
                           trial_notif_54h_sent = FALSE,
                           trial_notif_60h_sent = FALSE,
                           trial_notif_71h_sent = FALSE,
                           activation_status = 'pending',
                           activation_attempts = 0,
                           last_activation_error = NULL,
                           uuid = NULL,
                           vpn_key = NULL""",
                    telegram_id, _to_db_utc(subscription_end), source, admin_grant_days, _to_db_utc(subscription_start)
                )
                
                # ВАЛИДАЦИЯ: Проверяем что запись действительно сохранена
                saved_subscription = await conn.fetchrow(
                    "SELECT expires_at, status, activation_status FROM subscriptions WHERE telegram_id = $1",
                    telegram_id
                )
                if not saved_subscription or _from_db_utc(saved_subscription["expires_at"]) != subscription_end:
                    error_msg = f"Failed to verify subscription save for user {telegram_id}"
                    logger.error(f"grant_access: ERROR_DB_VERIFICATION [user={telegram_id}, error={error_msg}]")
                    raise Exception(error_msg)
                
                subscription_id = await conn.fetchval(
                    "SELECT id FROM subscriptions WHERE telegram_id = $1",
                    telegram_id
                )
                
                logger.info(
                    f"grant_access: ACTIVATION_PENDING [user={telegram_id}, subscription_id={subscription_id}, "
                    f"subscription_end={saved_subscription['expires_at'].isoformat()}, "
                    f"status={saved_subscription['status']}, activation_status={saved_subscription.get('activation_status', 'pending')}]"
                )
            except Exception as e:
                logger.error(
                    f"grant_access: DB_SAVE_FAILED [user={telegram_id}, error={str(e)}]"
                )
                raise Exception(f"Failed to save subscription to database: {e}") from e
            
            # Записываем в историю подписок (без VPN ключа)
            await _log_subscription_history_atomic(conn, telegram_id, None, subscription_start, subscription_end, history_action_type)
            
            # Audit log
            if admin_telegram_id:
                duration_str = f"{duration.days} days" if duration.days > 0 else f"{int(duration.total_seconds() / 60)} minutes"
                details = f"Granted {duration_str} access via {source}, Expires: {subscription_end.isoformat()}, Activation: pending (VPN API unavailable)"
                await _log_audit_event_atomic(conn, "subscription_created", admin_telegram_id, telegram_id, details)
            
            duration_str = f"{duration.days} days" if duration.days > 0 else f"{int(duration.total_seconds() / 60)} minutes"
            logger.info(
                f"grant_access: PENDING_ACTIVATION_SUCCESS [action=pending_activation, telegram_id={telegram_id}, "
                f"subscription_end={subscription_end.isoformat()}, duration={duration_str}, source={source}]"
            )
            
            return {
                "uuid": None,
                "vless_url": None,
                "subscription_end": subscription_end,
                "action": "pending_activation"
            }
        
        # Если был старый UUID и он ещё существует - удаляем его из VPN API
        if uuid:
            try:
                await vpn_utils.remove_vless_user(uuid)
                # Безопасное логирование UUID
                uuid_preview = f"{uuid[:8]}..." if uuid and len(uuid) > 8 else (uuid or "N/A")
                logger.info(
                    f"grant_access: REMOVED_OLD_UUID [action=remove_old, user={telegram_id}, "
                    f"old_uuid={uuid_preview}, reason=creating_new_subscription]"
                )
            except Exception as e:
                logger.warning(
                    f"grant_access: Failed to remove old UUID {uuid} for user {telegram_id}: {e}. "
                    "Continuing with new UUID creation."
                )
        
        # Вычисляем subscription_end ДО вызова VPN API (передаётся в Xray как expiryTime)
        subscription_start = now
        subscription_end = now + duration
        assert subscription_end.tzinfo is not None, "subscription_end must be timezone-aware"
        assert subscription_end.tzinfo == timezone.utc, "subscription_end must be UTC"
        duration_days = duration.days
        expiry_ms = int(subscription_end.timestamp() * 1000)
        logger.info(
            f"grant_access: CALCULATED_DATES [user={telegram_id}, subscription_end={subscription_end.isoformat()}, "
            f"duration_days={duration_days}, expiry_timestamp_ms={expiry_ms}]"
        )
        
        # Xray generates UUID; returned UUID is canonical (source of truth).
        logger.info(f"XRAY_UUID_FLOW [user={telegram_id}, operation=add] Xray will generate UUID")
        logger.info(f"grant_access: CALLING_VPN_API [action=add_user, user={telegram_id}, subscription_end={subscription_end.isoformat()}, source={source}]")

        import asyncio
        MAX_VPN_RETRIES = 2
        RETRY_DELAY_SECONDS = 1.0

        last_exception = None
        vless_result = None
        vless_url = None

        for attempt in range(MAX_VPN_RETRIES + 1):
            if attempt > 0:
                delay = RETRY_DELAY_SECONDS * attempt
                logger.info(
                    f"grant_access: VPN_API_RETRY [user={telegram_id}, attempt={attempt + 1}/{MAX_VPN_RETRIES + 1}, "
                    f"delay={delay}s, previous_error={str(last_exception)}]"
                )
                await asyncio.sleep(delay)
            
            try:
                # PART D.7: If vpn_api != healthy, NEVER call VPN API
                # Mark cleanup as pending, log SKIPPED (VPN_API_DISABLED)
                try:
                    from app.core.system_state import recalculate_from_runtime, ComponentStatus
                    system_state = recalculate_from_runtime()
                    if system_state.vpn_api.status != ComponentStatus.HEALTHY:
                        logger.warning(
                            f"grant_access: VPN_API_DISABLED [user={telegram_id}] - "
                            f"VPN API is {system_state.vpn_api.status.value}, skipping VPN call, marking as pending"
                        )
                        # PART D.7: Mark cleanup as pending (activation_status = 'pending')
                        # UUID will be created later when VPN API is healthy
                        vless_result = None
                        new_uuid = None
                        vless_url = None
                        # Skip VPN API call, will be handled in activation worker
                    else:
                        vless_result = await vpn_utils.add_vless_user(
                            telegram_id=telegram_id,
                            subscription_end=subscription_end,
                            uuid=None
                        )
                        new_uuid = vless_result.get("uuid")
                        vless_url = vless_result.get("vless_url")
                except Exception as e:
                    logger.warning(f"grant_access: VPN_API_CHECK_FAILED [user={telegram_id}]: {e}, proceeding with VPN call")
                    vless_result = await vpn_utils.add_vless_user(
                        telegram_id=telegram_id,
                        subscription_end=subscription_end,
                        uuid=None
                    )
                    new_uuid = vless_result.get("uuid")
                    vless_url = vless_result.get("vless_url")
                
                # ВАЛИДАЦИЯ: Проверяем что UUID и VLESS URL получены
                if not new_uuid:
                    error_msg = f"VPN API returned empty UUID for user {telegram_id}"
                    logger.error(f"grant_access: ERROR_VPN_API_RESPONSE [user={telegram_id}, attempt={attempt + 1}, error={error_msg}]")
                    last_exception = Exception(error_msg)
                    if attempt < MAX_VPN_RETRIES:
                        continue
                    raise last_exception
                
                if not vless_url:
                    error_msg = f"VPN API returned empty vless_url for user {telegram_id}"
                    logger.error(f"grant_access: ERROR_VPN_API_RESPONSE [user={telegram_id}, attempt={attempt + 1}, error={error_msg}]")
                    last_exception = Exception(error_msg)
                    if attempt < MAX_VPN_RETRIES:
                        continue
                    raise last_exception
                
                # КРИТИЧНО: Валидация VLESS ссылки ПЕРЕД финализацией платежа
                if not vpn_utils.validate_vless_link(vless_url):
                    error_msg = f"VPN API returned invalid vless_url (contains flow=) for user {telegram_id}"
                    logger.error(f"grant_access: ERROR_INVALID_VLESS_URL [user={telegram_id}, attempt={attempt + 1}, error={error_msg}]")
                    last_exception = Exception(error_msg)
                    if attempt < MAX_VPN_RETRIES:
                        continue
                    raise last_exception
                
                # Успешно получен валидный UUID и VLESS URL
                uuid_preview = f"{new_uuid[:8]}..." if new_uuid and len(new_uuid) > 8 else (new_uuid or "N/A")
                logger.info(
                    f"grant_access: ACTIVATION_IMMEDIATE_SUCCESS [action=add_user, user={telegram_id}, uuid={uuid_preview}, "
                    f"source={source}, attempt={attempt + 1}, vless_url_length={len(vless_url) if vless_url else 0}]"
                )
                break  # Успех - выходим из цикла retry
                
            except Exception as e:
                last_exception = e
                logger.error(
                    f"grant_access: VPN_API_FAILED [action=add_user_failed, user={telegram_id}, "
                    f"source={source}, attempt={attempt + 1}/{MAX_VPN_RETRIES + 1}, error={str(e)}]"
                )
                
                if attempt < MAX_VPN_RETRIES:
                    # Продолжаем retry
                    continue
                else:
                    # Все попытки исчерпаны - логируем и выбрасываем исключение
                    error_msg = f"Failed to create VPN access after {MAX_VPN_RETRIES + 1} attempts: {e}"
                    logger.error(
                        f"grant_access: VPN_API_ALL_RETRIES_FAILED [user={telegram_id}, source={source}, "
                        f"attempts={MAX_VPN_RETRIES + 1}, final_error={str(e)}]"
                    )
                    # VPN AUDIT LOG: Логируем ошибку создания UUID
                    try:
                        await _log_vpn_lifecycle_audit_async(
                            action="vpn_add_user",
                            telegram_id=telegram_id,
                            uuid=None,
                            source=source,
                            result="error",
                            details=f"VPN API call failed after {MAX_VPN_RETRIES + 1} attempts: {str(e)}"
                        )
                    except Exception:
                        pass  # Не блокируем при ошибке логирования
                    raise Exception(error_msg) from e
        
        # PART D.7: Handle case where VPN API is disabled (new_uuid is None)
        # If VPN API is disabled, set activation_status to 'pending' instead of raising error
        if not new_uuid or not vless_url:
            # Check if VPN API is disabled (not just failed)
            try:
                from app.core.system_state import recalculate_from_runtime, ComponentStatus
                system_state = recalculate_from_runtime()
                if system_state.vpn_api.status != ComponentStatus.HEALTHY:
                    # PART D.7: VPN API is disabled - mark as pending
                    logger.info(
                        f"grant_access: VPN_API_DISABLED_PENDING [user={telegram_id}] - "
                        f"VPN API is {system_state.vpn_api.status.value}, setting activation_status='pending'"
                    )
                    # Will set activation_status='pending' in DB insert below
                    pending_activation = True
                else:
                    # VPN API is healthy but failed - this is an error
                    error_msg = f"VPN API failed to return UUID/vless_url after retries for user {telegram_id}"
                    logger.error(f"grant_access: CRITICAL_VPN_API_FAILURE [user={telegram_id}, error={error_msg}]")
                    raise Exception(error_msg)
            except Exception as e:
                if "VPN API failed" in str(e):
                    raise  # Re-raise VPN API failure errors
                # If system_state check failed, treat as error
                error_msg = f"VPN API failed to return UUID/vless_url after retries for user {telegram_id}"
                logger.error(f"grant_access: CRITICAL_VPN_API_FAILURE [user={telegram_id}, error={error_msg}]")
                raise Exception(error_msg)
        else:
            pending_activation = False
        
        # subscription_start, subscription_end уже вычислены выше (перед VPN API вызовом)
        # ВАЛИДАЦИЯ: Проверяем что subscription_end вычислен корректно
        if not subscription_end or subscription_end <= subscription_start:
            error_msg = f"Invalid subscription_end for user {telegram_id}: start={subscription_start}, end={subscription_end}"
            logger.error(f"grant_access: ERROR_INVALID_DATES [user={telegram_id}, error={error_msg}]")
            raise Exception(error_msg)
        
        logger.info(
            f"grant_access: CALCULATED_DATES [user={telegram_id}, subscription_start={subscription_start.isoformat()}, "
            f"subscription_end={subscription_end.isoformat()}, duration_days={duration.days}]"
        )
        
        # Определяем action_type для истории
        if source == "payment":
            history_action_type = "purchase"
        elif source == "admin":
            history_action_type = "admin_grant"
        else:
            history_action_type = source
        
        # ВАЛИДАЦИЯ: Запрещено выдавать ключ без записи в БД
        logger.info(
            f"grant_access: SAVING_TO_DB [user={telegram_id}, uuid={uuid_preview}, "
            f"subscription_start={subscription_start.isoformat()}, subscription_end={subscription_end.isoformat()}, "
            f"status=active, source={source}]"
        )
        
        # Сохраняем/обновляем подписку
        try:
            # DEBUG: Валидация количества аргументов
            activation_status_value = 'pending' if pending_activation else 'active'
            args = (telegram_id, new_uuid, vless_url, _to_db_utc(subscription_end), source, admin_grant_days, _to_db_utc(subscription_start), activation_status_value)
            logger.debug(
                f"grant_access: SQL_ARGS_COUNT [user={telegram_id}, "
                f"placeholders=8, args_count={len(args)}, "
                f"activation_status={activation_status_value}]"
            )
            
            await conn.execute(
                """INSERT INTO subscriptions (
                       telegram_id, uuid, vpn_key, expires_at, status, source,
                       reminder_sent, reminder_3d_sent, reminder_24h_sent,
                       reminder_3h_sent, reminder_6h_sent, admin_grant_days,
                       activated_at, last_bytes,
                       trial_notif_6h_sent, trial_notif_18h_sent, trial_notif_30h_sent,
                       trial_notif_42h_sent, trial_notif_54h_sent, trial_notif_60h_sent,
                       trial_notif_71h_sent,
                       activation_status, activation_attempts, last_activation_error
                   )
                   VALUES ($1, $2, $3, $4, 'active', $5, FALSE, FALSE, FALSE, FALSE, FALSE, $6, $7, 0,
                           FALSE, FALSE, FALSE, FALSE, FALSE, FALSE, FALSE,
                           $8, 0, NULL)
                   ON CONFLICT (telegram_id) 
                   DO UPDATE SET 
                       uuid = COALESCE($2, subscriptions.uuid),
                       vpn_key = COALESCE($3, subscriptions.vpn_key),
                       expires_at = $4,
                       status = 'active',
                       source = $5,
                       reminder_sent = FALSE,
                       reminder_3d_sent = FALSE,
                       reminder_24h_sent = FALSE,
                       reminder_3h_sent = FALSE,
                       reminder_6h_sent = FALSE,
                       admin_grant_days = $6,
                       activated_at = COALESCE($7, subscriptions.activated_at),
                       last_bytes = 0,
                       trial_notif_6h_sent = FALSE,
                       trial_notif_18h_sent = FALSE,
                       trial_notif_30h_sent = FALSE,
                       trial_notif_42h_sent = FALSE,
                       trial_notif_54h_sent = FALSE,
                       trial_notif_60h_sent = FALSE,
                       trial_notif_71h_sent = FALSE,
                       activation_status = $8,
                       activation_attempts = 0,
                       last_activation_error = NULL""",
                *args
            )
            
            # ВАЛИДАЦИЯ: Проверяем что запись действительно сохранена
            saved_subscription = await conn.fetchrow(
                "SELECT uuid, expires_at, status FROM subscriptions WHERE telegram_id = $1",
                telegram_id
            )
            if not saved_subscription or saved_subscription["uuid"] != new_uuid:
                error_msg = f"Failed to verify subscription save for user {telegram_id}"
                logger.error(f"grant_access: ERROR_DB_VERIFICATION [user={telegram_id}, error={error_msg}]")
                raise Exception(error_msg)
            
            logger.info(
                f"grant_access: DB_SAVED_SUCCESS [user={telegram_id}, uuid={uuid_preview}, "
                f"subscription_end={saved_subscription['expires_at'].isoformat()}, status={saved_subscription['status']}]"
            )
        except Exception as e:
            logger.error(
                f"grant_access: DB_SAVE_FAILED [user={telegram_id}, uuid={uuid_preview}, error={str(e)}]"
            )
            raise Exception(f"Failed to save subscription to database: {e}") from e
        
        # WHY: При оплате во время trial явно завершаем trial и логируем — trial_notifications/cleanup не должны трогать paid
        if source == "payment":
            user_row = await conn.fetchrow("SELECT trial_expires_at FROM users WHERE telegram_id = $1", telegram_id)
            old_trial_expires_at = user_row["trial_expires_at"] if user_row else None
            if old_trial_expires_at and _from_db_utc(old_trial_expires_at) > now:
                await conn.execute(
                    "UPDATE users SET trial_expires_at = $1 WHERE telegram_id = $2 AND trial_expires_at > $1",
                    _to_db_utc(now), telegram_id
                )
                logger.info(
                    f"TRIAL_OVERRIDDEN_BY_PAID_SUBSCRIPTION: user_id={telegram_id}, "
                    f"old_trial_expires_at={old_trial_expires_at.isoformat()}, "
                    f"paid_subscription_expires_at={subscription_end.isoformat()}"
                )
        
        # Записываем в историю подписок
        await _log_subscription_history_atomic(conn, telegram_id, vless_url, subscription_start, subscription_end, history_action_type)
        
        # Audit log
        if admin_telegram_id:
            duration_str = f"{duration.days} days" if duration.days > 0 else f"{int(duration.total_seconds() / 60)} minutes"
            uuid_preview = f"{new_uuid[:8]}..." if new_uuid and len(new_uuid) > 8 else (new_uuid or "N/A")
            details = f"Granted {duration_str} access via {source}, Expires: {subscription_end.isoformat()}, UUID: {uuid_preview}"
            await _log_audit_event_atomic(conn, "subscription_created", admin_telegram_id, telegram_id, details)
        
        # Безопасное логирование UUID
        uuid_preview = f"{new_uuid[:8]}..." if new_uuid and len(new_uuid) > 8 else (new_uuid or "N/A")
        duration_str = f"{duration.days} days" if duration.days > 0 else f"{int(duration.total_seconds() / 60)} minutes"
        logger.info(
            f"grant_access: NEW_ISSUANCE_SUCCESS [action=new_issuance, telegram_id={telegram_id}, uuid={uuid_preview}, "
            f"subscription_end={subscription_end.isoformat()}, expiry_timestamp_ms={expiry_ms}, duration_days={duration_days}, "
            f"source={source}, duration={duration_str}, vless_url_length={len(vless_url) if vless_url else 0}]"
        )
        logger.info(
            f"grant_access: UUID_CREATED [action=new_issuance, telegram_id={telegram_id}, uuid={uuid_preview}] - "
            "New UUID created via VPN API, user must connect with new VLESS link"
        )
        
        # ВАЛИДАЦИЯ: Возвращаем только если все данные сохранены в БД
        return {
            "uuid": new_uuid,
            "vless_url": vless_url,  # VLESS ссылка готова к выдаче пользователю (новый UUID)
            "subscription_end": subscription_end,
            "action": "new_issuance"  # Явно указываем тип операции
        }
        
    except Exception as e:
        logger.error(
            f"grant_access: ERROR [telegram_id={telegram_id}, source={source}, error={str(e)}, "
            f"error_type={type(e).__name__}]"
        )
        logger.exception(f"grant_access: EXCEPTION_TRACEBACK [user={telegram_id}]")
        raise  # Пробрасываем исключение, не возвращаем None
    finally:
        if should_release_conn:
            pool = await get_pool()
            await pool.release(conn)


def _calculate_subscription_days(months: int) -> int:
    """
    Рассчитать количество дней для подписки на основе количества месяцев
    
    Args:
        months: Количество месяцев (1, 3, 6, 12)
    
    Returns:
        Количество дней (30, 90, 180, 365)
    """
    days_map = {
        1: 30,
        3: 90,
        6: 180,
        12: 365
    }
    return days_map.get(months, months * 30)


async def approve_payment_atomic(payment_id: int, months: int, admin_telegram_id: int, bot: Optional["Bot"] = None) -> Tuple[Optional[datetime], bool, Optional[str]]:
    """Атомарно подтвердить платеж в одной транзакции
    
    В одной транзакции:
    - обновляет payment → approved
    - создает VPN-ключ через Xray API (если нужен новый)
    - создает/продлевает subscription с VPN-ключом
    - записывает событие в audit_log
    
    Логика выдачи ключей:
    - Использует единую функцию grant_access()
    - Если подписка активна (status='active' AND expires_at > now): продлевает, UUID не меняется
    - Если подписка закончилась или её нет: создается новый UUID через Xray API
    
    Args:
        payment_id: ID платежа
        months: Количество месяцев подписки
        admin_telegram_id: Telegram ID администратора, который выполняет approve
    
    Returns:
        (expires_at, is_renewal, vpn_key) или (None, False, None) при ошибке или отсутствии ключей
        vpn_key - ключ, который был использован/переиспользован
    
    При любой ошибке транзакция откатывается.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            try:
                # 1. Проверяем, что платеж существует и в статусе pending
                payment_row = await conn.fetchrow(
                    "SELECT * FROM payments WHERE id = $1 AND status = 'pending'",
                    payment_id
                )
                if not payment_row:
                    logger.error(f"Payment {payment_id} not found or not pending for atomic approve")
                    return None, False, None
                
                payment = dict(payment_row)
                telegram_id = payment["telegram_id"]
                
                # 2. Обновляем статус платежа на approved
                await conn.execute(
                    "UPDATE payments SET status = 'approved' WHERE id = $1",
                    payment_id
                )
                
                # 3. Получаем подписку БЕЗ фильтра по активности (нужно проверить expires_at)
                now = datetime.now(timezone.utc)
                days = _calculate_subscription_days(months)
                tariff_duration = timedelta(days=days)
                
                subscription_row = await conn.fetchrow(
                    "SELECT * FROM subscriptions WHERE telegram_id = $1",
                    telegram_id
                )
                subscription = dict(subscription_row) if subscription_row else None
                
                # 4. Используем единую функцию grant_access (защищена от двойного создания ключей)
                result = await grant_access(
                    telegram_id=telegram_id,
                    duration=tariff_duration,
                    source="payment",
                    admin_telegram_id=None,
                    admin_grant_days=None,
                    conn=conn
                )
                
                expires_at = result["subscription_end"]
                # Если vless_url есть - это новый UUID, используем его
                # Если vless_url нет - это продление, получаем vpn_key из подписки
                if result.get("vless_url"):
                    final_vpn_key = result["vless_url"]
                    is_renewal = False
                else:
                    # Продление - получаем vpn_key из существующей подписки
                    subscription_after = await conn.fetchrow(
                        "SELECT vpn_key FROM subscriptions WHERE telegram_id = $1",
                        telegram_id
                    )
                    if subscription_after and subscription_after.get("vpn_key"):
                        final_vpn_key = subscription_after["vpn_key"]
                    else:
                        # Fallback: используем UUID (не должно быть, но на всякий случай)
                        final_vpn_key = result.get("uuid", "")
                    is_renewal = True
                
                # 7. Записываем событие в audit_log
                audit_action_type = "subscription_renewed" if is_renewal else "payment_approved"
                vpn_key_display = final_vpn_key[:50] if len(final_vpn_key) > 50 else final_vpn_key
                details = f"Payment ID: {payment_id}, Tariff: {months} months, Expires: {expires_at.isoformat()}, UUID: {result['uuid']}, VPN: {vpn_key_display}..."
                await _log_audit_event_atomic(conn, audit_action_type, admin_telegram_id, telegram_id, details)
                
                # 8. Обрабатываем реферальный кешбэк (только при первой оплате, не при продлении)
                # E) PURCHASE FLOW: Use unified process_referral_reward function
                if not is_renewal:
                    try:
                        # Получаем сумму платежа в рублях
                        payment_amount_rubles = payment.get("amount", 0) / 100.0  # Конвертируем из копеек
                        
                        if payment_amount_rubles > 0:
                            # Используем единую функцию process_referral_reward
                            # purchase_id = f"admin_approve_{payment_id}" для уникальности
                            purchase_id = f"admin_approve_{payment_id}"
                            referral_reward_result = await process_referral_reward(
                                buyer_id=telegram_id,
                                purchase_id=purchase_id,
                                amount_rubles=payment_amount_rubles,
                                conn=conn
                            )
                            
                            if referral_reward_result.get("success"):
                                logger.info(
                                    f"REFERRAL_CASHBACK_GRANTED [payment_id={payment_id}, "
                                    f"referrer={referral_reward_result.get('referrer_id')}, "
                                    f"amount={referral_reward_result.get('reward_amount')} RUB, "
                                    f"percent={referral_reward_result.get('percent')}%]"
                                )
                                
                                # Отправляем уведомление рефереру (вне транзакции)
                                # NOTE: Notification is sent from handler, not from database layer
                                # This keeps database layer clean (no bot dependencies)
                                logger.info(
                                    f"REFERRAL_CASHBACK_GRANTED [payment_id={payment_id}, "
                                    f"referrer={referral_reward_result.get('referrer_id')}, "
                                    f"referred={telegram_id}, amount={referral_reward_result.get('reward_amount')} RUB]"
                                )
                            else:
                                # BUSINESS LOGIC ERROR: Reward skipped but payment continues
                                reason = referral_reward_result.get("reason", "unknown")
                                logger.debug(
                                    f"Referral reward skipped for payment {payment_id}: "
                                    f"user={telegram_id}, reason={reason}"
                                )
                    except Exception as e:
                        # Не блокируем транзакцию при ошибке обработки реферального кешбэка
                        logger.exception(f"Error processing referral reward for payment {payment_id}: {e}")
                
                logger.info(f"Payment {payment_id} approved atomically for user {telegram_id}, is_renewal={is_renewal}")
                return expires_at, is_renewal, final_vpn_key
                
            except Exception as e:
                logger.exception(f"Error in atomic approve for payment {payment_id}, transaction rolled back")
                raise


async def get_pending_payments() -> list:
    """Получить все pending платежи (для админа)"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM payments WHERE status = 'pending' ORDER BY created_at DESC"
        )
        return [dict(row) for row in rows]


async def get_subscriptions_needing_reminder() -> list:
    """Получить подписки, которым нужно отправить напоминание
    
    Возвращает список подписок, где:
    - expires_at > now (активная)
    - reminder_sent = FALSE
    - expires_at <= now + 3 days
    """
    # Защита от работы с неинициализированной БД
    if not DB_READY:
        logger.warning("DB not ready, get_subscriptions_needing_reminder skipped")
        return []
    now = datetime.now(timezone.utc)
    reminder_date = now + timedelta(days=3)
    
    pool = await get_pool()
    if pool is None:
        logger.warning("Pool is None, get_subscriptions_needing_reminder skipped")
        return []
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT * FROM subscriptions 
               WHERE expires_at > $1 
               AND expires_at <= $2
               AND reminder_sent = FALSE
               ORDER BY expires_at ASC""",
            _to_db_utc(now), _to_db_utc(reminder_date)
        )
        return [_normalize_subscription_row(row) for row in rows]


async def mark_reminder_sent(telegram_id: int):
    """Отметить, что напоминание отправлено пользователю (старая функция, для совместимости)"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE subscriptions SET reminder_sent = TRUE WHERE telegram_id = $1",
            telegram_id
        )


async def mark_reminder_flag_sent(telegram_id: int, flag_name: str):
    """Отметить, что конкретное напоминание отправлено пользователю
    
    Args:
        telegram_id: Telegram ID пользователя
        flag_name: Имя флага ('reminder_3d_sent', 'reminder_24h_sent', 'reminder_3h_sent', 'reminder_6h_sent')
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            f"UPDATE subscriptions SET {flag_name} = TRUE, last_reminder_at = (NOW() AT TIME ZONE 'UTC') WHERE telegram_id = $1",
            telegram_id
        )


async def mark_user_unreachable(telegram_id: int) -> None:
    """Mark user as unreachable (chat not found, blocked). Background workers filter by is_reachable."""
    if not DB_READY:
        return
    try:
        pool = await get_pool()
        if pool is None:
            return
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET is_reachable = FALSE WHERE telegram_id = $1",
                telegram_id
            )
    except asyncpg.UndefinedColumnError:
        logger.debug("mark_user_unreachable skipped: is_reachable column not present")
    except Exception as e:
        logger.warning(f"mark_user_unreachable failed for user={telegram_id}: {e}")


async def update_last_reminder_at(subscription_id: int) -> None:
    """Update last_reminder_at for idempotency guard (container restart protection)."""
    if not DB_READY:
        return
    try:
        pool = await get_pool()
        if pool is None:
            return
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE subscriptions SET last_reminder_at = (NOW() AT TIME ZONE 'UTC') WHERE id = $1",
                subscription_id
            )
    except Exception as e:
        logger.warning(f"update_last_reminder_at failed for subscription_id={subscription_id}: {e}")


# Active promo definition: is_active=true AND deleted_at IS NULL AND expires_at > now() AND used_count < max_uses
_ACTIVE_PROMO_WHERE = (
    "is_active = true AND deleted_at IS NULL "
    "AND (expires_at IS NULL OR expires_at > NOW()) "
    "AND (max_uses IS NULL OR used_count < max_uses)"
)


async def get_promo_code(code: str) -> Optional[Dict[str, Any]]:
    """Получить любой промокод по коду (может быть неактивным). Для валидации используйте get_active_promo_by_code."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM promo_codes WHERE UPPER(code) = UPPER($1) ORDER BY created_at DESC LIMIT 1",
            code
        )
        return dict(row) if row else None


async def get_active_promo_by_code(conn, code: str) -> Optional[Dict[str, Any]]:
    """Получить активный промокод по коду (is_active, !deleted, !expired, !exhausted). Требует conn."""
    has_deleted_at = await conn.fetchval(
        "SELECT 1 FROM information_schema.columns WHERE table_name = 'promo_codes' AND column_name = 'deleted_at'"
    )
    if has_deleted_at:
        row = await conn.fetchrow(
            f"""
            SELECT * FROM promo_codes
            WHERE UPPER(code) = UPPER($1) AND {_ACTIVE_PROMO_WHERE}
            ORDER BY id DESC NULLS LAST, created_at DESC
            LIMIT 1
            """,
            code
        )
    else:
        row = await conn.fetchrow(
            """
            SELECT * FROM promo_codes
            WHERE UPPER(code) = UPPER($1)
              AND is_active = true
              AND (expires_at IS NULL OR expires_at > NOW())
              AND (max_uses IS NULL OR used_count < max_uses)
            ORDER BY created_at DESC
            LIMIT 1
            """,
            code
        )
    return dict(row) if row else None


async def has_active_promo(code: str) -> bool:
    """Проверить, есть ли активный промокод с таким кодом"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        promo = await get_active_promo_by_code(conn, code)
        return promo is not None


async def check_promo_code_valid(code: str) -> Optional[Dict[str, Any]]:
    """Проверить, валиден ли промокод и вернуть его данные (только активный)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await get_active_promo_by_code(conn, code)


async def increment_promo_code_use(code: str):
    """
    Увеличить счетчик использований промокода.
    
    DEPRECATED: Используйте apply_promocode_atomic для атомарного применения промокода.
    Эта функция оставлена для обратной совместимости.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # CRITICAL: Advisory lock для защиты от race conditions
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1))",
                code.upper()
            )
            
            # Получаем текущее значение used_count и max_uses с FOR UPDATE
            row = await conn.fetchrow(
                "SELECT used_count, max_uses, expires_at, is_active FROM promo_codes WHERE UPPER(code) = UPPER($1) FOR UPDATE",
                code
            )
            if not row:
                return
            
            # Проверяем срок действия
            expires_at = row.get("expires_at")
            if expires_at and _from_db_utc(expires_at) < datetime.now(timezone.utc):
                # Деактивируем промокод при истечении срока
                await conn.execute(
                    "UPDATE promo_codes SET is_active = FALSE WHERE UPPER(code) = UPPER($1)",
                    code
                )
                return
            
            # Проверяем активность
            if not row.get("is_active", False):
                return
            
            used_count = row["used_count"]
            max_uses = row["max_uses"]
            
            # Увеличиваем счетчик
            new_count = used_count + 1
            
            # Если достигли лимита, деактивируем промокод
            if max_uses is not None and new_count >= max_uses:
                await conn.execute("""
                    UPDATE promo_codes 
                    SET used_count = $1, is_active = FALSE 
                    WHERE UPPER(code) = UPPER($2)
                """, new_count, code)
            else:
                await conn.execute("""
                    UPDATE promo_codes 
                    SET used_count = $1 
                    WHERE UPPER(code) = UPPER($2)
                """, new_count, code)


async def log_promo_code_usage(
    promo_code: str,
    telegram_id: int,
    tariff: str,
    discount_percent: int,
    price_before: int,
    price_after: int
):
    """Записать использование промокода в лог"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO promo_usage_logs 
            (promo_code, telegram_id, tariff, discount_percent, price_before, price_after)
            VALUES ($1, $2, $3, $4, $5, $6)
        """, promo_code.upper(), telegram_id, tariff, discount_percent, price_before, price_after)


async def get_promo_stats() -> list:
    """
    Получить статистику по промокодам через SQL-агрегацию.
    Без кеширования. Активный промокод: is_active, !deleted, !expired, !exhausted.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        has_id = await conn.fetchval(
            "SELECT 1 FROM information_schema.columns WHERE table_name = 'promo_codes' AND column_name = 'id'"
        )
        if not has_id:
            rows = await conn.fetch("""
                SELECT code, discount_percent, max_uses, used_count, is_active, expires_at, created_at, created_by
                FROM promo_codes ORDER BY code
            """)
            return [dict(row) for row in rows]
        rows = await conn.fetch("""
            SELECT id, code, discount_percent, max_uses, used_count, is_active, deleted_at,
                   expires_at, created_at, created_by,
                   (is_active = true AND deleted_at IS NULL
                    AND (expires_at IS NULL OR expires_at > NOW())
                    AND (max_uses IS NULL OR used_count < max_uses)) AS is_effective_active
            FROM promo_codes
            ORDER BY code, created_at DESC
        """)
        return [dict(row) for row in rows]


def generate_promo_code(length: int = 6) -> str:
    """Генерировать случайный промокод из заглавных букв A-Z"""
    return ''.join(random.choices(string.ascii_uppercase, k=length))


async def create_promocode_atomic(
    code: str,
    discount_percent: int,
    duration_seconds: int,
    max_uses: int,
    created_by: int
) -> Optional[int]:
    """
    Создать промокод атомарно. Разрешает пересоздание, если предыдущий удалён/истёк/исчерпан.
    Блокирует создание, если активный промокод с таким кодом уже существует.
    
    Returns:
        ID созданного промокода или None при конфликте/ошибке
    """
    if not DB_READY:
        logger.warning("DB not ready, create_promocode_atomic skipped")
        return None
    pool = await get_pool()
    if pool is None:
        return None

    code_normalized = code.upper().strip()
    if len(code_normalized) < 3 or len(code_normalized) > 32:
        logger.error(f"Invalid promocode length: {len(code_normalized)}")
        return None
    if not all(c.isalnum() for c in code_normalized):
        logger.error(f"Invalid promocode characters: {code_normalized}")
        return None
    if discount_percent < 0 or discount_percent > 100:
        logger.error(f"Invalid discount_percent: {discount_percent}")
        return None
    if max_uses <= 0:
        logger.error(f"Invalid max_uses: {max_uses}")
        return None
    if duration_seconds <= 0:
        logger.error(f"Invalid duration_seconds: {duration_seconds}")
        return None

    expires_at = datetime.now(timezone.utc) + timedelta(seconds=duration_seconds)

    async with pool.acquire() as conn:
        async with conn.transaction():
            try:
                has_id = await conn.fetchval(
                    "SELECT 1 FROM information_schema.columns WHERE table_name = 'promo_codes' AND column_name = 'id'"
                )
                if has_id:
                    conflict = await conn.fetchrow(
                        """
                        SELECT id FROM promo_codes
                        WHERE UPPER(code) = UPPER($1)
                          AND is_active = true AND deleted_at IS NULL
                          AND (expires_at IS NULL OR expires_at > NOW())
                          AND (max_uses IS NULL OR used_count < max_uses)
                        LIMIT 1
                        """,
                        code_normalized
                    )
                    if conflict:
                        logger.warning(f"PROMO_CONFLICT code={code_normalized} active promo exists id={conflict['id']}")
                        return None

                if has_id:
                    row = await conn.fetchrow(
                        """
                        INSERT INTO promo_codes
                        (code, discount_percent, duration_seconds, max_uses, expires_at, created_by, is_active, used_count)
                        VALUES ($1, $2, $3, $4, $5, $6, TRUE, 0)
                        RETURNING id
                        """,
                        code_normalized, discount_percent, duration_seconds, max_uses, _to_db_utc(expires_at), created_by
                    )
                else:
                    row = await conn.fetchrow(
                        """
                        INSERT INTO promo_codes
                        (code, discount_percent, duration_seconds, max_uses, expires_at, created_by, is_active)
                        VALUES ($1, $2, $3, $4, $5, $6, TRUE)
                        RETURNING code
                        """,
                        code_normalized, discount_percent, duration_seconds, max_uses, _to_db_utc(expires_at), created_by
                    )
                if not row:
                    return None

                promo_id = row.get("id") or row.get("code")
                prev_count = await conn.fetchval(
                    "SELECT COUNT(*) FROM promo_codes WHERE UPPER(code) = UPPER($1)",
                    code_normalized
                ) or 0
                is_recreate = has_id and int(prev_count) > 1
                if is_recreate:
                    logger.info(
                        f"PROMO_RECREATED code={code_normalized} id={promo_id} discount={discount_percent}% "
                        f"max_uses={max_uses} created_by={created_by}"
                    )
                else:
                    logger.info(
                        f"PROMO_CREATED code={code_normalized} id={promo_id} discount={discount_percent}% "
                        f"max_uses={max_uses} expires_at={expires_at} created_by={created_by}"
                    )
                return int(promo_id) if promo_id else None
            except asyncpg.UniqueViolationError:
                logger.warning(f"Promocode unique violation (active conflict): {code_normalized}")
                return None
            except Exception as e:
                logger.exception(f"Error creating promocode {code_normalized}: {e}")
                return None


async def deactivate_promocode(promo_id: Optional[int] = None, code: Optional[str] = None) -> bool:
    """
    Деактивировать промокод: UPDATE is_active=false, deleted_at=now().
    Передайте promo_id (предпочтительно) или code. Логирует PROMO_DEACTIVATED.
    """
    if not DB_READY:
        return False
    pool = await get_pool()
    if pool is None:
        return False
    async with pool.acquire() as conn:
        has_deleted_at = await conn.fetchval(
            "SELECT 1 FROM information_schema.columns WHERE table_name = 'promo_codes' AND column_name = 'deleted_at'"
        )
        if promo_id is not None:
            if has_deleted_at:
                row = await conn.fetchrow(
                    "UPDATE promo_codes SET is_active = false, deleted_at = NOW() WHERE id = $1 RETURNING code",
                    promo_id
                )
            else:
                row = await conn.fetchrow(
                    "UPDATE promo_codes SET is_active = false WHERE id = $1 RETURNING code",
                    promo_id
                )
        elif code:
            code_n = code.upper().strip()
            if has_deleted_at:
                row = await conn.fetchrow(
                    "UPDATE promo_codes SET is_active = false, deleted_at = NOW() WHERE UPPER(code) = UPPER($1) RETURNING code",
                    code_n
                )
            else:
                row = await conn.fetchrow(
                    "UPDATE promo_codes SET is_active = false WHERE UPPER(code) = UPPER($1) RETURNING code",
                    code_n
                )
        else:
            return False
        if row:
            logger.info(f"PROMO_DEACTIVATED code={row['code']} id={promo_id or 'N/A'}")
            return True
        return False


async def _consume_promo_in_transaction(
    conn, code: str, telegram_id: int, purchase_id: Optional[str] = None
) -> None:
    """
    Потребление промокода внутри транзакции: UPDATE ... WHERE id = ? AND used_count < max_uses RETURNING *
    Если строк не возвращено — промокод исчерпан. Логирует PROMO_USAGE_INCREMENTED.
    Raises ValueError при ошибке.
    """
    code_normalized = code.upper().strip()
    promo = await get_active_promo_by_code(conn, code_normalized)
    if not promo:
        ctx = f" purchase_id={purchase_id}" if purchase_id else ""
        raise ValueError(f"PROMO_INVALID_OR_EXPIRED: code={code_normalized}{ctx}")

    promo_id = promo.get("id")
    has_id = promo_id is not None
    if has_id:
        updated = await conn.fetchrow(
            """
            UPDATE promo_codes
            SET used_count = used_count + 1
            WHERE id = $1 AND (max_uses IS NULL OR used_count < max_uses)
            RETURNING *
            """,
            promo_id
        )
    else:
        updated = await conn.fetchrow(
            """
            UPDATE promo_codes
            SET used_count = used_count + 1
            WHERE UPPER(code) = UPPER($1)
              AND is_active = true
              AND (expires_at IS NULL OR expires_at > NOW())
              AND (max_uses IS NULL OR used_count < max_uses)
            RETURNING *
            """,
            code_normalized
        )
    if not updated:
        ctx = f" purchase_id={purchase_id}" if purchase_id else ""
        logger.warning(f"PROMO_EXHAUSTED code={code_normalized} user={telegram_id}{ctx}")
        raise ValueError("PROMO_EXHAUSTED")

    used = updated["used_count"]
    max_uses_val = updated["max_uses"]
    logger.info(
        f"PROMO_USAGE_INCREMENTED code={code_normalized} id={promo_id or 'N/A'} user={telegram_id} "
        f"used_count={used}/{max_uses_val if max_uses_val else 'unlimited'}"
    )


async def validate_promocode_atomic(code: str) -> Dict[str, Any]:
    """
    Валидация промокода без инкремента счетчика.
    Использует определение активного промо: is_active, !deleted, !expired, !exhausted.
    
    Returns:
        {"success": bool, "promo_data": Optional[Dict], "error": Optional[str]}
    """
    if not DB_READY:
        return {"success": False, "promo_data": None, "error": "invalid"}
    pool = await get_pool()
    if pool is None:
        return {"success": False, "promo_data": None, "error": "invalid"}
    code_normalized = code.upper().strip()
    async with pool.acquire() as conn:
        try:
            promo = await get_active_promo_by_code(conn, code_normalized)
            if not promo:
                return {"success": False, "promo_data": None, "error": "invalid"}
            logger.info(
                f"PROMOCODE_VALIDATED code={code_normalized} "
                f"used_count={promo.get('used_count', 0)}/{promo.get('max_uses') or 'unlimited'}"
            )
            return {"success": True, "promo_data": dict(promo), "error": None}
        except Exception as e:
            logger.exception(f"Error validating promocode {code_normalized}: {e}")
            return {"success": False, "promo_data": None, "error": "invalid"}


async def consume_promocode_atomic(code: str, telegram_id: int) -> None:
    """
    Потребление промокода — инкремент счетчика использований.
    Вызывается ТОЛЬКО при успешной оплате.
    
    CRITICAL: Эта функция должна вызываться только после успешной оплаты.
    
    Raises:
        ValueError: Если промокод не найден, уже исчерпан или невалиден
    """
    if not DB_READY:
        raise ValueError("PROMO_DB_NOT_READY")
    
    pool = await get_pool()
    if pool is None:
        raise ValueError("PROMO_DB_NOT_READY")
    
    code_normalized = code.upper().strip()
    
    async with pool.acquire() as conn:
        async with conn.transaction():
            try:
                # CRITICAL: Advisory lock на код для защиты от race conditions
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))",
                    code_normalized
                )
                
                # CRITICAL: SELECT FOR UPDATE для блокировки строки
                row = await conn.fetchrow(
                    """
                    SELECT *
                    FROM promo_codes
                    WHERE code = $1
                    FOR UPDATE
                    """,
                    code_normalized
                )
                
                if not row:
                    raise ValueError("PROMO_NOT_FOUND")
                
                # Проверяем активность
                if not row.get("is_active", False):
                    raise ValueError("PROMO_INACTIVE")
                
                # Проверяем срок действия
                expires_at = row.get("expires_at")
                if expires_at:
                    expired_check = await conn.fetchval(
                        "SELECT expires_at < NOW() FROM promo_codes WHERE code = $1",
                        row["code"]
                    )
                    if expired_check:
                        await conn.execute(
                            "UPDATE promo_codes SET is_active = FALSE WHERE code = $1",
                            row["code"]
                        )
                        raise ValueError("PROMO_EXPIRED")
                
                # Проверяем лимит использований
                used_count = row.get("used_count", 0)
                max_uses = row.get("max_uses")
                if max_uses is not None and used_count >= max_uses:
                    raise ValueError("PROMO_ALREADY_CONSUMED")
                
                # SUCCESS — увеличиваем счетчик использований атомарно
                await conn.execute(
                    """
                    UPDATE promo_codes
                    SET used_count = used_count + 1
                    WHERE code = $1
                    """,
                    row["code"]
                )
                
                # Получаем обновленное значение used_count
                updated_row = await conn.fetchrow(
                    "SELECT used_count, max_uses FROM promo_codes WHERE code = $1",
                    row["code"]
                )
                new_count = updated_row["used_count"]
                
                # Автоматическая деактивация при достижении лимита
                if max_uses is not None and new_count >= max_uses:
                    await conn.execute(
                        """
                        UPDATE promo_codes
                        SET is_active = FALSE
                        WHERE code = $1
                        AND used_count >= max_uses
                        """,
                        row["code"]
                    )
                
                logger.info(
                    f"PROMOCODE_CONSUMED code={code_normalized} user={telegram_id} "
                    f"used_count={new_count}/{max_uses if max_uses else 'unlimited'}"
                )
                
            except ValueError:
                # Пробрасываем ValueError как есть
                raise
            except Exception as e:
                logger.exception(f"Error consuming promocode {code_normalized}: {e}")
                raise ValueError("PROMO_CONSUME_ERROR")


async def is_user_first_purchase(telegram_id: int) -> bool:
    """Проверить, является ли это первой покупкой пользователя
    
    Пользователь считается новым, если:
    - у него НИКОГДА не было подтверждённой оплаты (status = 'approved')
    - у него НИКОГДА не было активной или истёкшей подписки
    
    Returns:
        True если это первая покупка, False иначе
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Проверяем наличие подтверждённых платежей
        approved_payment = await conn.fetchrow(
            "SELECT id FROM payments WHERE telegram_id = $1 AND status = 'approved' LIMIT 1",
            telegram_id
        )
        
        if approved_payment:
            return False
        
        # Проверяем наличие подписок в истории (любых, включая истёкшие)
        subscription_history = await conn.fetchrow(
            """SELECT id FROM subscription_history 
               WHERE telegram_id = $1 
               AND action_type IN ('purchase', 'renewal', 'reissue')
               LIMIT 1""",
            telegram_id
        )
        
        if subscription_history:
            return False
        
        return True


async def get_subscriptions_for_reminders() -> list:
    """Получить все активные подписки, которым нужно отправить напоминания

    Filters out users with is_reachable = FALSE (blocked/chat not found).
    Falls back to legacy query if is_reachable column not yet present (migration 014).
    Returns список подписок с информацией о типе (админ-доступ или оплаченный тариф)
    """
    # Защита от работы с неинициализированной БД
    if not DB_READY:
        logger.warning("DB not ready, get_subscriptions_for_reminders skipped")
        return []
    pool = await get_pool()
    if pool is None:
        logger.warning("Pool is None, get_subscriptions_for_reminders skipped")
        return []
    async with pool.acquire() as conn:
        now = datetime.now(timezone.utc)
        query_with_reachable = """
            SELECT s.*,
                   (SELECT action_type FROM subscription_history
                    WHERE telegram_id = s.telegram_id
                    ORDER BY created_at DESC LIMIT 1) as last_action_type
            FROM subscriptions s
            JOIN users u ON s.telegram_id = u.telegram_id
            WHERE s.expires_at > $1
            AND COALESCE(u.is_reachable, TRUE) = TRUE
            ORDER BY s.expires_at ASC"""
        fallback_query = """
            SELECT s.*,
                   (SELECT action_type FROM subscription_history
                    WHERE telegram_id = s.telegram_id
                    ORDER BY created_at DESC LIMIT 1) as last_action_type
            FROM subscriptions s
            WHERE s.expires_at > $1
            ORDER BY s.expires_at ASC"""
        try:
            rows = await conn.fetch(query_with_reachable, _to_db_utc(now))
        except asyncpg.UndefinedColumnError:
            logger.warning("DB_SCHEMA_OUTDATED: is_reachable missing, fallback to legacy query")
            rows = await conn.fetch(fallback_query, _to_db_utc(now))
        return [_normalize_subscription_row(row) for row in rows]


async def get_admin_stats() -> Dict[str, int]:
    """Получить статистику для админ-дашборда
    
    Returns:
        Словарь с ключами:
        - total_users: всего пользователей
        - active_subscriptions: активных подписок
        - expired_subscriptions: истёкших подписок
        - total_payments: всего платежей
        - approved_payments: подтверждённых платежей
        - rejected_payments: отклонённых платежей
        - free_vpn_keys: свободных VPN-ключей
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        now = datetime.now(timezone.utc)
        
        # Всего пользователей
        total_users = await conn.fetchval("SELECT COUNT(*) FROM users")
        
        # Активных подписок (expires_at > now)
        active_subscriptions = await conn.fetchval(
            "SELECT COUNT(*) FROM subscriptions WHERE expires_at > $1",
            _to_db_utc(now)
        )
        
        # Истёкших подписок (expires_at <= now)
        expired_subscriptions = await conn.fetchval(
            "SELECT COUNT(*) FROM subscriptions WHERE expires_at <= $1",
            _to_db_utc(now)
        )
        
        # Всего платежей
        total_payments = await conn.fetchval("SELECT COUNT(*) FROM payments")
        
        # Подтверждённых платежей
        approved_payments = await conn.fetchval(
            "SELECT COUNT(*) FROM payments WHERE status = 'approved'"
        )
        
        # Отклонённых платежей
        rejected_payments = await conn.fetchval(
            "SELECT COUNT(*) FROM payments WHERE status = 'rejected'"
        )
        
        # Свободных VPN-ключей
        free_vpn_keys = await conn.fetchval(
            "SELECT COUNT(*) FROM vpn_keys WHERE is_used = FALSE"
        )
        
        return {
            "total_users": total_users or 0,
            "active_subscriptions": active_subscriptions or 0,
            "expired_subscriptions": expired_subscriptions or 0,
            "total_payments": total_payments or 0,
            "approved_payments": approved_payments or 0,
            "rejected_payments": rejected_payments or 0,
            "free_vpn_keys": free_vpn_keys or 0,
        }


async def get_admin_referral_stats(
    search_query: Optional[str] = None,
    sort_by: str = "total_revenue",  # "total_revenue", "invited_count", "cashback_paid"
    sort_order: str = "DESC",  # "ASC", "DESC"
    limit: int = 50,
    offset: int = 0
) -> List[Dict[str, Any]]:
    """
    Получить агрегированную статистику по всем рефералам для админ-дашборда
    
    Args:
        search_query: Поисковый запрос (telegram_id или username)
        sort_by: Поле для сортировки ("total_revenue", "invited_count", "cashback_paid")
        sort_order: Порядок сортировки ("ASC", "DESC")
        limit: Максимальное количество записей
        offset: Смещение для пагинации
    
    Returns:
        Список словарей с агрегированной статистикой по каждому рефереру:
        - referrer_id: Telegram ID реферера
        - username: Username реферера
        - invited_count: Всего приглашённых
        - paid_count: Сколько оплатили
        - conversion_percent: Процент конверсии
        - total_invited_revenue: Общий доход от приглашённых (рубли)
        - total_cashback_paid: Общий выплаченный кешбэк (рубли)
        - current_cashback_percent: Текущий процент кешбэка
        - first_referral_date: Дата первого приглашения
    """
    if not DB_READY:
        logger.warning("DB not ready (degraded mode), get_admin_referral_stats skipped")
        return []
    
    pool = await get_pool()
    if pool is None:
        return []
    
    try:
        # FIX: Все операции с conn должны происходить строго внутри async with
        async with pool.acquire() as conn:
            # Базовый запрос для агрегированной статистики
            # Используем подзапросы для корректной агрегации
            base_query = """
            SELECT 
                u.telegram_id AS referrer_id,
                u.username,
                COALESCE(ref_stats.invited_count, 0) AS invited_count,
                COALESCE(paid_stats.paid_count, 0) AS paid_count,
                COALESCE(MIN(r.created_at), NULL) AS first_referral_date,
                COALESCE(revenue_stats.total_revenue_kopecks, 0) AS total_invited_revenue_kopecks,
                COALESCE(cashback_stats.total_cashback_kopecks, 0) AS total_cashback_paid_kopecks
            FROM users u
            LEFT JOIN referrals r ON u.telegram_id = r.referrer_user_id
            LEFT JOIN (
                SELECT referrer_user_id, COUNT(DISTINCT referred_user_id) AS invited_count
                FROM referrals
                GROUP BY referrer_user_id
            ) ref_stats ON u.telegram_id = ref_stats.referrer_user_id
            LEFT JOIN (
                SELECT r.referrer_user_id, COUNT(DISTINCT r.referred_user_id) AS paid_count
                FROM referrals r
                INNER JOIN payments p ON r.referred_user_id = p.telegram_id AND p.status = 'approved'
                GROUP BY r.referrer_user_id
            ) paid_stats ON u.telegram_id = paid_stats.referrer_user_id
            LEFT JOIN (
                SELECT r.referrer_user_id, SUM(p.amount) AS total_revenue_kopecks
                FROM referrals r
                INNER JOIN payments p ON r.referred_user_id = p.telegram_id AND p.status = 'approved'
                GROUP BY r.referrer_user_id
            ) revenue_stats ON u.telegram_id = revenue_stats.referrer_user_id
            LEFT JOIN (
                SELECT bt.user_id AS referrer_user_id, SUM(bt.amount) AS total_cashback_kopecks
                FROM balance_transactions bt
                WHERE bt.type = 'cashback' AND bt.source = 'referral'
                GROUP BY bt.user_id
            ) cashback_stats ON u.telegram_id = cashback_stats.referrer_user_id
            """
            
            where_clauses = []
            params = []
            param_index = 1
            
            # Фильтр по поисковому запросу
            if search_query:
                try:
                    # Пробуем найти по telegram_id
                    telegram_id = int(search_query)
                    where_clauses.append(f"u.telegram_id = ${param_index}")
                    params.append(telegram_id)
                    param_index += 1
                except ValueError:
                    # Иначе ищем по username
                    where_clauses.append(f"LOWER(u.username) LIKE LOWER(${param_index})")
                    params.append(f"%{search_query}%")
                    param_index += 1
            
            # Фильтр: показываем только рефереров (тех, кто пригласил хотя бы одного)
            where_clauses.append(f"ref_stats.invited_count > 0 OR EXISTS (SELECT 1 FROM referrals r2 WHERE r2.referrer_user_id = u.telegram_id)")
            
            # Группировка по рефереру
            group_by = "GROUP BY u.telegram_id, u.username, ref_stats.invited_count, paid_stats.paid_count, revenue_stats.total_revenue_kopecks, cashback_stats.total_cashback_kopecks"
            
            # Сортировка
            sort_column_map = {
                "total_revenue": "total_invited_revenue_kopecks",
                "invited_count": "invited_count",
                "cashback_paid": "total_cashback_paid_kopecks"
            }
            sort_column = sort_column_map.get(sort_by, "total_invited_revenue_kopecks")
            order_by = f"ORDER BY {sort_column} {sort_order}, u.telegram_id ASC"
            
            # Пагинация
            limit_clause = f"LIMIT ${param_index} OFFSET ${param_index + 1}"
            params.extend([limit, offset])
            
            # Собираем полный запрос
            where_clause = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""
            full_query = f"{base_query} {where_clause} {group_by} {order_by} {limit_clause}"
            
            # FIX: Все операции с conn.fetch() происходят строго внутри блока async with
            rows = await conn.fetch(full_query, *params)
            
            # FIX: Извлекаем все данные из rows внутри блока async with
            # Преобразуем rows в список словарей, чтобы не зависеть от connection после выхода из блока
            rows_data = []
            for row in rows:
                rows_data.append(dict(row))
        
        # FIX: Обработка результатов происходит ПОСЛЕ выхода из блока async with
        # Это гарантирует, что conn не используется после release
        result = []
        for row_data in rows_data:
            try:
                referrer_id = row_data.get("referrer_id")
                if referrer_id is None:
                    continue  # Пропускаем строки без referrer_id
                
                # Безопасное извлечение значений с обработкой NULL
                invited_count = safe_int(row_data.get("invited_count"))
                paid_count = safe_int(row_data.get("paid_count"))
                
                # Вычисляем процент конверсии (защита от деления на 0)
                conversion_percent = (paid_count / invited_count * 100) if invited_count > 0 else 0.0
                
                # Конвертируем из копеек в рубли с безопасной обработкой NULL
                total_invited_revenue_kopecks = safe_int(row_data.get("total_invited_revenue_kopecks"))
                total_cashback_paid_kopecks = safe_int(row_data.get("total_cashback_paid_kopecks"))
                total_invited_revenue = total_invited_revenue_kopecks / 100.0
                total_cashback_paid = total_cashback_paid_kopecks / 100.0
                
                # Определяем текущий процент кешбэка (безопасно)
                # FIX: Вызываем после выхода из блока conn, чтобы избежать проблем с connection lifecycle
                try:
                    current_cashback_percent = await get_referral_cashback_percent(referrer_id)
                except Exception as e:
                    logger.warning(f"Error getting cashback percent for referrer_id={referrer_id}: {e}")
                    current_cashback_percent = 10  # Значение по умолчанию
                
                result.append({
                    "referrer_id": referrer_id,
                    "username": row_data.get("username") or f"ID{referrer_id}",
                    "invited_count": invited_count,
                    "paid_count": paid_count,
                    "conversion_percent": round(conversion_percent, 2),
                    "total_invited_revenue": round(total_invited_revenue, 2),
                    "total_cashback_paid": round(total_cashback_paid, 2),
                    "current_cashback_percent": current_cashback_percent,
                    "first_referral_date": row_data.get("first_referral_date")
                })
            except Exception as e:
                logger.exception(f"Error processing row in get_admin_referral_stats: {e}, row={row_data}")
                continue  # Пропускаем проблемные строки, но продолжаем обработку
        
        return result
    except asyncpg.PostgresError as e:
        logger.warning(f"referrals or related tables missing or inaccessible — skipping admin referral stats: {e}")
        return []
    except Exception as e:
        logger.warning(f"Error getting admin referral stats: {e}")
        return []


async def get_admin_referral_detail(referrer_id: int) -> Optional[Dict[str, Any]]:
    """
    Получить детальную информацию по конкретному рефереру
    
    Args:
        referrer_id: Telegram ID реферера
    
    Returns:
        Словарь с детальной информацией:
        - referrer_id: Telegram ID реферера
        - username: Username реферера
        - invited_list: Список приглашённых с деталями:
          - invited_user_id: Telegram ID приглашённого
          - username: Username приглашённого
          - registered_at: Дата регистрации
          - first_payment_date: Дата первой оплаты
          - purchase_amount: Сумма покупки (рубли)
          - cashback_amount: Сумма кешбэка (рубли)
          - purchase_id: ID платежа
    """
    if not DB_READY:
        logger.warning("DB not ready (degraded mode), get_admin_referral_detail skipped")
        return None
    
    pool = await get_pool()
    if pool is None:
        return None
    
    try:
        async with pool.acquire() as conn:
            # Получаем информацию о реферере
            referrer = await conn.fetchrow(
                "SELECT telegram_id, username FROM users WHERE telegram_id = $1",
                referrer_id
            )
            
            if not referrer:
                return None
            
            # Получаем список всех приглашённых с детальной информацией
            invited_list_query = """
            SELECT 
                r.referred_user_id AS invited_user_id,
                u.username,
                r.created_at AS registered_at,
                MIN(p.created_at) AS first_payment_date,
                MIN(p.id) AS purchase_id,
                MIN(p.amount) AS purchase_amount_kopecks,
                COALESCE(SUM(CASE 
                    WHEN bt.type = 'cashback' AND bt.source = 'referral' 
                    AND bt.related_user_id = r.referred_user_id THEN bt.amount 
                    ELSE 0 
                END), 0) AS cashback_amount_kopecks
            FROM referrals r
            LEFT JOIN users u ON r.referred_user_id = u.telegram_id
            LEFT JOIN payments p ON r.referred_user_id = p.telegram_id 
                AND p.status = 'approved'
            LEFT JOIN balance_transactions bt ON bt.user_id = $1 
                AND bt.type = 'cashback' 
                AND bt.source = 'referral'
                AND bt.related_user_id = r.referred_user_id
            WHERE r.referrer_user_id = $1
            GROUP BY r.referred_user_id, u.username, r.created_at
            ORDER BY r.created_at DESC
            """
            
            invited_rows = await conn.fetch(invited_list_query, referrer_id)
            
            invited_list = []
            for row in invited_rows:
                invited_list.append({
                    "invited_user_id": row["invited_user_id"],
                    "username": row["username"] or f"ID{row['invited_user_id']}",
                    "registered_at": row["registered_at"],
                    "first_payment_date": row["first_payment_date"],
                    "purchase_amount": (row["purchase_amount_kopecks"] or 0) / 100.0,
                    "cashback_amount": (row["cashback_amount_kopecks"] or 0) / 100.0,
                    "purchase_id": row["purchase_id"]
                })
            
            return {
                "referrer_id": referrer_id,
                "username": referrer["username"] or f"ID{referrer_id}",
                "invited_list": invited_list
            }
    except (asyncpg.UndefinedTableError, asyncpg.PostgresError) as e:
        logger.warning(f"referrals or related tables missing or inaccessible — skipping admin referral detail: {e}")
        return None
    except Exception as e:
        logger.warning(f"Error getting admin referral detail: {e}")
        return None


async def get_referral_overall_stats(
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None
) -> Dict[str, Any]:
    """
    Получить общую статистику по реферальной системе
    
    Args:
        date_from: Начальная дата для фильтрации (опционально)
        date_to: Конечная дата для фильтрации (опционально)
    
    Returns:
        Словарь с общей статистикой:
        - total_referrers: Всего рефереров
        - total_referrals: Всего приглашённых пользователей
        - total_paid_referrals: Всего оплативших рефералов
        - total_revenue: Общий доход от рефералов (рубли)
        - total_cashback_paid: Общий выплаченный кешбэк (рубли)
        - avg_cashback_per_referrer: Средний кешбэк на реферера (рубли)
    """
    if not DB_READY:
        logger.warning("DB not ready (degraded mode), get_referral_overall_stats skipped")
        return {
            "total_referrers": 0,
            "total_referrals": 0,
            "total_paid_referrals": 0,
            "total_revenue": 0.0,
            "total_cashback_paid": 0.0,
            "avg_cashback_per_referrer": 0.0
        }
    
    pool = await get_pool()
    if pool is None:
        return {
            "total_referrers": 0,
            "total_referrals": 0,
            "total_paid_referrals": 0,
            "total_revenue": 0.0,
            "total_cashback_paid": 0.0,
            "avg_cashback_per_referrer": 0.0
        }
    
    try:
        async with pool.acquire() as conn:
            # Базовые условия для фильтрации по дате
            date_filter = ""
            params = []
            if date_from or date_to:
                conditions = []
                if date_from:
                    conditions.append("rr.created_at >= $1")
                    params.append(date_from)
                if date_to:
                    param_idx = len(params) + 1
                    conditions.append(f"rr.created_at <= ${param_idx}")
                    params.append(date_to)
                date_filter = "WHERE " + " AND ".join(conditions)
            
            # Всего рефереров (уникальных)
            # Безопасная обработка NULL через COALESCE
            total_referrers_query = f"""
                SELECT COALESCE(COUNT(DISTINCT rr.referrer_id), 0)
                FROM referral_rewards rr
                {date_filter}
            """
            total_referrers_val = await conn.fetchval(total_referrers_query, *params)
            total_referrers = safe_int(total_referrers_val)
            
            # Всего приглашённых (из таблицы referrals)
            total_referrals_query = "SELECT COALESCE(COUNT(DISTINCT referred_user_id), 0) FROM referrals"
            if date_from or date_to:
                # Если есть фильтр по дате, применяем его к referrals
                if date_from:
                    total_referrals_query += " WHERE created_at >= $1"
                if date_to:
                    param_idx = len([date_from]) + 1
                    total_referrals_query += f" {'AND' if date_from else 'WHERE'} created_at <= ${param_idx}"
            total_referrals_val = await conn.fetchval(total_referrals_query, *params)
            total_referrals = safe_int(total_referrals_val)
            
            # Всего оплативших рефералов (уникальных buyer_id из referral_rewards)
            total_paid_referrals_query = f"""
                SELECT COALESCE(COUNT(DISTINCT rr.buyer_id), 0)
                FROM referral_rewards rr
                {date_filter}
            """
            total_paid_referrals_val = await conn.fetchval(total_paid_referrals_query, *params)
            total_paid_referrals = safe_int(total_paid_referrals_val)
            
            # Общий доход от рефералов (сумма purchase_amount из referral_rewards)
            total_revenue_query = f"""
                SELECT COALESCE(SUM(rr.purchase_amount), 0)
                FROM referral_rewards rr
                {date_filter}
            """
            total_revenue_kopecks_val = await conn.fetchval(total_revenue_query, *params)
            total_revenue_kopecks = safe_int(total_revenue_kopecks_val)
            total_revenue = total_revenue_kopecks / 100.0
            
            # Общий выплаченный кешбэк (сумма reward_amount из referral_rewards)
            total_cashback_query = f"""
                SELECT COALESCE(SUM(rr.reward_amount), 0)
                FROM referral_rewards rr
                {date_filter}
            """
            total_cashback_kopecks_val = await conn.fetchval(total_cashback_query, *params)
            total_cashback_kopecks = safe_int(total_cashback_kopecks_val)
            total_cashback_paid = total_cashback_kopecks / 100.0
            
            # Средний кешбэк на реферера (защита от деления на 0)
            avg_cashback_per_referrer = total_cashback_paid / total_referrers if total_referrers > 0 else 0.0
            
            return {
                "total_referrers": total_referrers,
                "total_referrals": total_referrals,
                "total_paid_referrals": total_paid_referrals,
                "total_revenue": round(total_revenue, 2),
                "total_cashback_paid": round(total_cashback_paid, 2),
                "avg_cashback_per_referrer": round(avg_cashback_per_referrer, 2)
            }
    except (asyncpg.UndefinedTableError, asyncpg.PostgresError) as e:
        logger.warning(f"referrals or referral_rewards tables missing or inaccessible — skipping referral overall stats: {e}")
        return {
            "total_referrers": 0,
            "total_referrals": 0,
            "total_paid_referrals": 0,
            "total_revenue": 0.0,
            "total_cashback_paid": 0.0,
            "avg_cashback_per_referrer": 0.0
        }
    except Exception as e:
        logger.warning(f"Error getting referral overall stats: {e}")
        return {
            "total_referrers": 0,
            "total_referrals": 0,
            "total_paid_referrals": 0,
            "total_revenue": 0.0,
            "total_cashback_paid": 0.0,
            "avg_cashback_per_referrer": 0.0
        }


async def get_referral_rewards_history(
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    limit: int = 50,
    offset: int = 0
) -> List[Dict[str, Any]]:
    """
    Получить историю начислений реферального кешбэка
    
    Args:
        date_from: Начальная дата для фильтрации (опционально)
        date_to: Конечная дата для фильтрации (опционально)
        limit: Максимальное количество записей
        offset: Смещение для пагинации
    
    Returns:
        Список словарей с историей начислений:
        - id: ID записи
        - referrer_id: Telegram ID реферера
        - referrer_username: Username реферера
        - buyer_id: Telegram ID покупателя
        - buyer_username: Username покупателя
        - purchase_amount: Сумма покупки (рубли)
        - percent: Процент кешбэка
        - reward_amount: Сумма кешбэка (рубли)
        - created_at: Дата начисления
        - purchase_id: ID покупки
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Базовый запрос
        base_query = """
            SELECT 
                rr.id,
                rr.referrer_id,
                referrer_user.username AS referrer_username,
                rr.buyer_id,
                buyer_user.username AS buyer_username,
                rr.purchase_amount,
                rr.percent,
                rr.reward_amount,
                rr.created_at,
                rr.purchase_id
            FROM referral_rewards rr
            LEFT JOIN users referrer_user ON rr.referrer_id = referrer_user.telegram_id
            LEFT JOIN users buyer_user ON rr.buyer_id = buyer_user.telegram_id
        """
        
        where_clauses = []
        params = []
        param_index = 1
        
        # Фильтрация по дате
        if date_from:
            where_clauses.append(f"rr.created_at >= ${param_index}")
            params.append(date_from)
            param_index += 1
        
        if date_to:
            where_clauses.append(f"rr.created_at <= ${param_index}")
            params.append(date_to)
            param_index += 1
        
        # Собираем запрос
        where_clause = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""
        order_by = "ORDER BY rr.created_at DESC"
        limit_clause = f"LIMIT ${param_index} OFFSET ${param_index + 1}"
        params.extend([limit, offset])
        
        full_query = f"{base_query} {where_clause} {order_by} {limit_clause}"
        
        rows = await conn.fetch(full_query, *params)
        
        # Обрабатываем результаты
        result = []
        for row in rows:
            result.append({
                "id": row["id"],
                "referrer_id": row["referrer_id"],
                "referrer_username": row["referrer_username"] or f"ID{row['referrer_id']}",
                "buyer_id": row["buyer_id"],
                "buyer_username": row["buyer_username"] or f"ID{row['buyer_id']}",
                "purchase_amount": (row["purchase_amount"] or 0) / 100.0,
                "percent": row["percent"] or 0,
                "reward_amount": (row["reward_amount"] or 0) / 100.0,
                "created_at": row["created_at"],
                "purchase_id": row["purchase_id"]
            })
        
        return result


async def get_referral_rewards_history_count(
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None
) -> int:
    """
    Получить общее количество записей в истории начислений (для пагинации)
    
    Args:
        date_from: Начальная дата для фильтрации (опционально)
        date_to: Конечная дата для фильтрации (опционально)
    
    Returns:
        Общее количество записей
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        base_query = "SELECT COUNT(*) FROM referral_rewards rr"
        
        where_clauses = []
        params = []
        param_index = 1
        
        if date_from:
            where_clauses.append(f"rr.created_at >= ${param_index}")
            params.append(date_from)
            param_index += 1
        
        if date_to:
            where_clauses.append(f"rr.created_at <= ${param_index}")
            params.append(date_to)
            param_index += 1
        
        where_clause = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""
        full_query = f"{base_query} {where_clause}"
        
        count = await conn.fetchval(full_query, *params) or 0
        return count


async def calculate_final_price(
    telegram_id: int,
    tariff: str,
    period_days: int,
    promo_code: Optional[str] = None
) -> Dict[str, Any]:
    """
    ЕДИНАЯ ФУНКЦИЯ РАСЧЕТА ФИНАЛЬНОЙ ЦЕНЫ (SINGLE SOURCE OF TRUTH)
    
    Рассчитывает финальную цену тарифа с учетом всех скидок:
    - Базовая цена из config.TARIFFS
    - Промокод (высший приоритет)
    - VIP-скидка 30% (если нет промокода)
    - Персональная скидка (если нет промокода и VIP)
    
    Args:
        telegram_id: Telegram ID пользователя
        tariff: Тип тарифа ("basic" или "plus")
        period_days: Период в днях (30, 90, 180, 365)
        promo_code: Промокод (опционально)
    
    Returns:
        {
            "base_price_kopecks": int,      # Базовая цена в копейках
            "discount_amount_kopecks": int, # Размер скидки в копейках
            "final_price_kopecks": int,     # Финальная цена в копейках
            "discount_percent": int,        # Процент скидки (0-100)
            "discount_type": str,           # "promo", "vip", "personal", None
            "promo_code": Optional[str],    # Промокод (если применен)
            "is_valid": bool                # True если цена >= 64 RUB
        }
    
    Raises:
        ValueError: Если тариф или период не найдены в конфиге
    """
    import config
    
    # Проверяем валидность тарифа и периода
    if tariff not in config.TARIFFS:
        raise ValueError(f"Invalid tariff: {tariff}")
    
    if period_days not in config.TARIFFS[tariff]:
        raise ValueError(f"Invalid period_days: {period_days} for tariff {tariff}")
    
    # Получаем базовую цену в рублях из конфига
    base_price_rubles = config.TARIFFS[tariff][period_days]["price"]
    base_price_kopecks = int(base_price_rubles * 100)
    
    # ПРИОРИТЕТ 0: Промокод (высший приоритет, перекрывает все остальные скидки)
    promo_data = None
    if promo_code:
        promo_data = await check_promo_code_valid(promo_code.upper())
    
    has_promo = promo_data is not None
    
    # ПРИОРИТЕТ 1: VIP-статус (только если нет промокода)
    is_vip = await is_vip_user(telegram_id) if not has_promo else False
    
    # ПРИОРИТЕТ 2: Персональная скидка (только если нет промокода и VIP)
    personal_discount = None
    if not has_promo and not is_vip:
        personal_discount = await get_user_discount(telegram_id)
    
    # Применяем скидку в порядке приоритета
    discount_amount_kopecks = 0
    discount_percent = 0
    discount_type = None
    final_price_kopecks = base_price_kopecks
    
    if has_promo:
        discount_percent = promo_data["discount_percent"]
        # КРИТИЧНО: Защита от скидки > 100% - ограничиваем до 100%
        discount_percent = min(discount_percent, 100)
        discount_amount_kopecks = int(base_price_kopecks * discount_percent / 100)
        final_price_kopecks = base_price_kopecks - discount_amount_kopecks
        # КРИТИЧНО: Гарантируем, что финальная цена >= 0
        final_price_kopecks = max(final_price_kopecks, 0)
        discount_type = "promo"
        applied_promo_code = promo_code.upper()
    elif is_vip:
        discount_percent = 30
        discount_amount_kopecks = int(base_price_kopecks * discount_percent / 100)
        final_price_kopecks = base_price_kopecks - discount_amount_kopecks
        discount_type = "vip"
        applied_promo_code = None
    elif personal_discount:
        discount_percent = personal_discount["discount_percent"]
        discount_amount_kopecks = int(base_price_kopecks * discount_percent / 100)
        final_price_kopecks = base_price_kopecks - discount_amount_kopecks
        discount_type = "personal"
        applied_promo_code = None
    else:
        applied_promo_code = None
    
    # Округляем до целых копеек
    final_price_kopecks = int(final_price_kopecks)
    
    # Проверяем минимальную цену (64 RUB = 6400 kopecks)
    MIN_PRICE_KOPECKS = 6400
    is_valid = final_price_kopecks >= MIN_PRICE_KOPECKS
    
    return {
        "base_price_kopecks": base_price_kopecks,
        "discount_amount_kopecks": discount_amount_kopecks,
        "final_price_kopecks": final_price_kopecks,
        "discount_percent": discount_percent,
        "discount_type": discount_type,
        "promo_code": applied_promo_code,
        "is_valid": is_valid
    }


async def create_pending_balance_topup_purchase(
    telegram_id: int,
    amount_kopecks: int,
) -> str:
    """
    Create pending purchase for balance top-up only.
    No tariff, no period_days. Separate from subscription logic.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE pending_purchases SET status = 'expired' WHERE telegram_id = $1 AND status = 'pending'",
            telegram_id
        )
        purchase_id = f"purchase_{uuid_lib.uuid4().hex[:16]}"
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=30)
        await conn.execute(
            """INSERT INTO pending_purchases (purchase_id, telegram_id, purchase_type, price_kopecks, status, expires_at)
               VALUES ($1, $2, 'balance_topup', $3, 'pending', $4)""",
            purchase_id, telegram_id, amount_kopecks, _to_db_utc(expires_at)
        )
        logger.info(
            f"BALANCE_TOPUP_PURCHASE_CREATED purchase_id={purchase_id} telegram_id={telegram_id} "
            f"amount={amount_kopecks} kopecks"
        )
        return purchase_id


async def create_pending_purchase(
    telegram_id: int,
    tariff: str,  # "basic" или "plus"
    period_days: int,
    price_kopecks: int,
    promo_code: Optional[str] = None
) -> str:
    """
    Создать pending покупку с уникальным purchase_id
    
    Args:
        telegram_id: Telegram ID пользователя
        tariff: Тип тарифа ("basic" или "plus")
        period_days: Период в днях (30, 90, 180, 365)
        price_kopecks: Цена в копейках
        promo_code: Промокод (опционально)
    
    Returns:
        purchase_id: Уникальный ID покупки
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Отменяем все предыдущие pending покупки этого пользователя
        await conn.execute(
            "UPDATE pending_purchases SET status = 'expired' WHERE telegram_id = $1 AND status = 'pending'",
            telegram_id
        )
        
        # Генерируем уникальный purchase_id
        purchase_id = f"purchase_{uuid_lib.uuid4().hex[:16]}"
        
        # Срок действия контекста покупки (30 минут)
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=30)
        
        # Создаем запись о покупке (subscription only)
        await conn.execute(
            """INSERT INTO pending_purchases (purchase_id, telegram_id, purchase_type, tariff, period_days, price_kopecks, promo_code, status, expires_at)
               VALUES ($1, $2, 'subscription', $3, $4, $5, $6, $7, $8)""",
            purchase_id, telegram_id, tariff, period_days, price_kopecks, promo_code, "pending", _to_db_utc(expires_at)
        )
        
        logger.info(f"Pending purchase created: purchase_id={purchase_id}, telegram_id={telegram_id}, tariff={tariff}, period_days={period_days}, price={price_kopecks} kopecks")
        
        return purchase_id


async def get_pending_purchase(purchase_id: str, telegram_id: int, check_expiry: bool = True) -> Optional[Dict[str, Any]]:
    """
    Получить pending покупку по purchase_id с валидацией
    
    Args:
        purchase_id: ID покупки
        telegram_id: Telegram ID пользователя
        check_expiry: Проверять ли срок действия (по умолчанию True, False для оплаты)
    
    Returns:
        Словарь с данными покупки, если валидна, иначе None
    """
    # Защита от работы с неинициализированной БД
    if not DB_READY:
        logger.warning("DB not ready, get_pending_purchase skipped")
        return None
    pool = await get_pool()
    if pool is None:
        logger.warning("Pool is None, get_pending_purchase skipped")
        return None
    async with pool.acquire() as conn:
        if check_expiry:
            # При обычной проверке (создание покупки) проверяем срок действия
            purchase = await conn.fetchrow(
                """SELECT * FROM pending_purchases 
                   WHERE purchase_id = $1 AND telegram_id = $2 AND status = 'pending' AND expires_at > NOW()""",
                purchase_id, telegram_id
            )
        else:
            # При оплате (webhook) не проверяем срок - покупка может быть оплачена после expires_at
            purchase = await conn.fetchrow(
                """SELECT * FROM pending_purchases 
                   WHERE purchase_id = $1 AND telegram_id = $2 AND status = 'pending'""",
                purchase_id, telegram_id
            )
        
        if purchase:
            return dict(purchase)
        else:
            logger.warning(f"Invalid pending purchase: purchase_id={purchase_id}, telegram_id={telegram_id}, check_expiry={check_expiry}")
            return None


async def cancel_pending_purchases(telegram_id: int, reason: str = "user_action") -> None:
    """
    Отменить все pending покупки пользователя
    
    Args:
        telegram_id: Telegram ID пользователя
        reason: Причина отмены
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE pending_purchases SET status = 'expired' WHERE telegram_id = $1 AND status = 'pending'",
            telegram_id
        )
        
        if result != "UPDATE 0":
            logger.info(f"Pending purchases cancelled: telegram_id={telegram_id}, reason={reason}")


async def update_pending_purchase_invoice_id(purchase_id: str, invoice_id: str) -> bool:
    """
    Обновить provider_invoice_id для pending покупки
    
    Args:
        purchase_id: ID покупки
        invoice_id: Invoice ID от платежного провайдера (CryptoBot)
    
    Returns:
        True если успешно, False если покупка не найдена
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Для crypto purchases устанавливаем TTL = 30 минут с момента создания invoice
        now_utc = datetime.now(timezone.utc)
        expires_at_utc = now_utc + timedelta(minutes=30)
        
        result = await conn.execute(
            "UPDATE pending_purchases SET provider_invoice_id = $1, expires_at = $3 WHERE purchase_id = $2 AND status = 'pending'",
            invoice_id, purchase_id, _to_db_utc(expires_at_utc)
        )
        
        if result == "UPDATE 1":
            logger.info(f"Pending purchase invoice_id updated: purchase_id={purchase_id}, invoice_id={invoice_id}")
            return True
        else:
            logger.warning(f"Failed to update pending purchase invoice_id: purchase_id={purchase_id}, result={result}")
            return False


async def mark_pending_purchase_paid(purchase_id: str) -> bool:
    """
    Пометить pending покупку как оплаченную
    
    Args:
        purchase_id: ID покупки
    
    Returns:
        True если успешно, False если покупка не найдена или уже оплачена
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE pending_purchases SET status = 'paid' WHERE purchase_id = $1 AND status = 'pending'",
            purchase_id
        )
        
        if result == "UPDATE 1":
            logger.info(f"Pending purchase marked as paid: purchase_id={purchase_id}")
            return True
        else:
            logger.warning(f"Failed to mark pending purchase as paid: purchase_id={purchase_id}, result={result}")
            return False


async def finalize_purchase(
    purchase_id: str,
    payment_provider: str,
    amount_rubles: float,
    invoice_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    ЕДИНАЯ ФУНКЦИЯ ФИНАЛИЗАЦИИ ПОКУПКИ (SINGLE SOURCE OF TRUTH)
    
    Эта функция вызывается после успешной оплаты (карта или крипта)
    и выполняет ВСЮ бизнес-логику в ОДНОЙ транзакции:
    
    1. Проверяет pending_purchase (должен быть status='pending')
    2. Обновляет pending_purchase → status='paid'
    3. Создает payment record
    4. Активирует подписку через grant_access
    5. Обновляет payment → status='approved'
    6. Обрабатывает реферальный кешбэк
    
    КРИТИЧНО: Все операции в одной транзакции БД.
    Если любой шаг падает → rollback, логирование, исключение.
    
    Args:
        purchase_id: ID покупки из pending_purchases
        payment_provider: 'telegram_payment' или 'cryptobot'
        amount_rubles: Сумма оплаты в рублях
        invoice_id: ID инвойса (опционально, для крипты)
    
    Returns:
        {
            "success": bool,
            "payment_id": int,
            "expires_at": datetime,
            "vpn_key": str,
            "is_renewal": bool
        }
    
    Raises:
        ValueError: Если pending_purchase не найден или уже обработан
        Exception: При любых ошибках активации подписки
    """
    from datetime import timedelta
    
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Начинаем транзакцию
        async with conn.transaction():
            # STEP 1: Получаем и проверяем pending_purchase
            pending_row = await conn.fetchrow(
                "SELECT * FROM pending_purchases WHERE purchase_id = $1",
                purchase_id
            )
            
            if not pending_row:
                error_msg = f"Pending purchase not found: purchase_id={purchase_id}"
                logger.error(f"finalize_purchase: payment_rejected: reason=purchase_not_found, {error_msg}")
                raise ValueError(error_msg)
            
            pending_purchase = dict(pending_row)
            telegram_id = pending_purchase["telegram_id"]
            status = pending_purchase.get("status")
            promo_code = pending_purchase.get("promo_code")  # Получаем промокод из pending_purchase
            
            if status != "pending":
                error_msg = f"Pending purchase already processed: purchase_id={purchase_id}, status={status}"
                logger.warning(f"finalize_purchase: payment_rejected: reason=already_processed, {error_msg}")
                raise ValueError(error_msg)
            
            tariff_type = pending_purchase.get("tariff")
            period_days = pending_purchase.get("period_days")
            purchase_type = pending_purchase.get("purchase_type", "subscription")
            price_kopecks = pending_purchase["price_kopecks"]
            expected_amount_rubles = price_kopecks / 100.0

            # STEP 4: Balance top-up: purchase_type=='balance_topup' OR legacy period_days==0
            is_balance_topup = (purchase_type == "balance_topup") or (period_days == 0)
            
            # КРИТИЧНО: Проверка суммы платежа перед активацией подписки
            # Разрешаем отклонение до 1 рубля (округление, комиссии)
            amount_diff = abs(amount_rubles - expected_amount_rubles)
            if amount_diff > 1.0:
                error_msg = (
                    f"Payment amount mismatch: purchase_id={purchase_id}, user={telegram_id}, "
                    f"expected={expected_amount_rubles:.2f} RUB, actual={amount_rubles:.2f} RUB, "
                    f"diff={amount_diff:.2f} RUB"
                )
                logger.error(f"finalize_purchase: PAYMENT_AMOUNT_MISMATCH: {error_msg}")
                raise ValueError(error_msg)
            
            logger.info(
                f"finalize_purchase: START [purchase_id={purchase_id}, user={telegram_id}, "
                f"provider={payment_provider}, amount={amount_rubles:.2f} RUB (expected={expected_amount_rubles:.2f} RUB), "
                f"purchase_type={purchase_type}, tariff={tariff_type}, period_days={period_days}]"
            )
            
            # Логируем событие получения платежа для аудита
            logger.info(
                f"payment_event_received: purchase_id={purchase_id}, user={telegram_id}, "
                f"provider={payment_provider}, amount={amount_rubles:.2f} RUB, invoice_id={invoice_id or 'N/A'}"
            )
            
            # STEP 2: Проверка суммы пройдена - логируем верификацию
            logger.info(
                f"payment_verified: purchase_id={purchase_id}, user={telegram_id}, "
                f"provider={payment_provider}, amount={amount_rubles:.2f} RUB, "
                f"amount_match=True, purchase_status=pending"
            )
            
            # STEP 3: Обновляем pending_purchase → paid
            result = await conn.execute(
                "UPDATE pending_purchases SET status = 'paid' WHERE purchase_id = $1 AND status = 'pending'",
                purchase_id
            )
            
            if result != "UPDATE 1":
                error_msg = f"Failed to mark pending purchase as paid: purchase_id={purchase_id}"
                logger.error(f"finalize_purchase: payment_rejected: reason=db_update_failed, {error_msg}")
                raise Exception(error_msg)
            
            if is_balance_topup:
                # ОБРАБОТКА ПОПОЛНЕНИЯ БАЛАНСА
                logger.info(
                    f"finalize_purchase: BALANCE_TOPUP [purchase_id={purchase_id}, user={telegram_id}, "
                    f"amount={amount_rubles:.2f} RUB]"
                )
                
                # Увеличиваем баланс пользователя
                balance_increased = await increase_balance(
                    telegram_id=telegram_id,
                    amount=amount_rubles,
                    source="cryptobot" if payment_provider == "cryptobot" else "telegram_payment",
                    description=f"Balance top-up via {payment_provider}"
                )
                
                if not balance_increased:
                    error_msg = f"Failed to increase balance: purchase_id={purchase_id}, user={telegram_id}"
                    logger.error(f"finalize_purchase: {error_msg}")
                    raise Exception(error_msg)
                
                # Создаем payment record для баланса
                payment_id = await conn.fetchval(
                    "INSERT INTO payments (telegram_id, tariff, amount, status) VALUES ($1, $2, $3, 'approved') RETURNING id",
                    telegram_id,
                    "balance_topup",
                    int(amount_rubles * 100)  # Сохраняем в копейках
                )
                
                if not payment_id:
                    error_msg = f"Failed to create payment record: purchase_id={purchase_id}, user={telegram_id}"
                    logger.error(f"finalize_purchase: {error_msg}")
                    raise Exception(error_msg)
                
                # D) BALANCE TOP-UP FLOW: Consume promocode (if used) - atomic UPDATE ... RETURNING
                if promo_code:
                    await _consume_promo_in_transaction(conn, promo_code, telegram_id, purchase_id)
                
                # E) BALANCE TOP-UP FLOW: Process referral reward
                referral_reward_result = await process_referral_reward(
                    buyer_id=telegram_id,
                    purchase_id=purchase_id,
                    amount_rubles=amount_rubles,
                    conn=conn
                )
                
                if referral_reward_result.get("success"):
                    logger.info(
                        f"finalize_purchase: REFERRAL_CASHBACK_GRANTED [BALANCE_TOPUP] "
                        f"purchase_id={purchase_id}, user={telegram_id}, "
                        f"referrer={referral_reward_result.get('referrer_id')}, "
                        f"amount={referral_reward_result.get('reward_amount')} RUB"
                    )
                else:
                    reason = referral_reward_result.get("reason", "unknown")
                    logger.debug(
                        f"finalize_purchase: Referral reward skipped for balance topup: "
                        f"purchase_id={purchase_id}, user={telegram_id}, reason={reason}"
                    )
                
                logger.info(
                    f"balance_topup_completed: purchase_id={purchase_id}, user={telegram_id}, "
                    f"provider={payment_provider}, payment_id={payment_id}, amount={amount_rubles:.2f} RUB"
                )
                
                logger.info(
                    f"finalize_purchase: SUCCESS [BALANCE_TOPUP] [purchase_id={purchase_id}, user={telegram_id}, "
                    f"provider={payment_provider}, payment_id={payment_id}, amount={amount_rubles:.2f} RUB]"
                )
                
                # Возвращаем результат для balance_topup (без VPN ключа)
                return {
                    "success": True,
                    "payment_id": payment_id,
                    "expires_at": None,  # Нет подписки
                    "vpn_key": None,  # Нет VPN ключа
                    "is_renewal": False,
                    "is_balance_topup": True,
                    "amount": amount_rubles,
                    "referral_reward": referral_reward_result
                }
            
            # STEP 5: ОБРАБОТКА ПОДПИСКИ (subscription only)
            if tariff_type is None or period_days is None or period_days <= 0:
                error_msg = f"Invalid subscription purchase: tariff={tariff_type}, period_days={period_days}"
                logger.error(f"finalize_purchase: {error_msg}")
                raise ValueError(error_msg)

            # Создаем payment record
            payment_id = await conn.fetchval(
                "INSERT INTO payments (telegram_id, tariff, amount, status) VALUES ($1, $2, $3, 'pending') RETURNING id",
                telegram_id,
                f"{tariff_type}_{period_days}",
                int(amount_rubles * 100)  # Сохраняем в копейках
            )
            
            if not payment_id:
                error_msg = f"Failed to create payment record: purchase_id={purchase_id}, user={telegram_id}"
                logger.error(f"finalize_purchase: {error_msg}")
                raise Exception(error_msg)
            
            # Активируем подписку через grant_access
            duration = timedelta(days=period_days)
            grant_result = await grant_access(
                telegram_id=telegram_id,
                duration=duration,
                source="payment",
                admin_telegram_id=None,
                admin_grant_days=None,
                conn=conn
            )
            
            if not grant_result:
                error_msg = f"grant_access returned None: purchase_id={purchase_id}, user={telegram_id}"
                logger.error(f"finalize_purchase: {error_msg}")
                raise Exception(error_msg)
            
            expires_at = grant_result.get("subscription_end")
            if not expires_at:
                error_msg = f"grant_access returned None expires_at: purchase_id={purchase_id}, user={telegram_id}"
                logger.error(f"finalize_purchase: {error_msg}")
                raise Exception(error_msg)
            
            # Проверяем action для обработки pending activation
            action = grant_result.get("action")
            is_renewal = action == "renewal"
            
            # PENDING ACTIVATION: Если action == 'pending_activation', это ожидаемое поведение
            # VPN ключ будет создан позже activation_worker'ом
            if action == "pending_activation":
                logger.info(
                    f"finalize_purchase: PENDING_ACTIVATION_ACCEPTED [purchase_id={purchase_id}, user={telegram_id}]"
                )
                
                # Обновляем payment → approved
                await conn.execute(
                    "UPDATE payments SET status = 'approved' WHERE id = $1",
                    payment_id
                )
                
                # Возвращаем успешный результат без vpn_key
                return {
                    "success": True,
                    "payment_id": payment_id,
                    "expires_at": expires_at,
                    "vpn_key": None,
                    "activation_status": "pending",
                    "is_renewal": False
                }
            
            # Получаем VPN ключ для нормальной активации
            vpn_key = grant_result.get("vless_url")
            
            if not vpn_key:
                # Если это продление, получаем ключ из существующей подписки
                if is_renewal:
                    subscription_row = await conn.fetchrow(
                        "SELECT * FROM subscriptions WHERE telegram_id = $1",
                        telegram_id
                    )
                    subscription = dict(subscription_row) if subscription_row else None
                    if subscription and subscription.get("vpn_key"):
                        vpn_key = subscription["vpn_key"]
                    else:
                        # Fallback: генерируем из UUID
                        uuid = grant_result.get("uuid")
                        if uuid:
                            import vpn_utils
                            vpn_key = vpn_utils.generate_vless_url(uuid)
                        else:
                            vpn_key = ""
                else:
                    # Новая подписка без vless_url - генерируем из UUID
                    uuid = grant_result.get("uuid")
                    if uuid:
                        import vpn_utils
                        vpn_key = vpn_utils.generate_vless_url(uuid)
                    else:
                        error_msg = f"No VPN key and no UUID: purchase_id={purchase_id}, user={telegram_id}"
                        logger.error(f"finalize_purchase: {error_msg}")
                        raise Exception(error_msg)
            
            if not vpn_key:
                error_msg = f"VPN key is empty: purchase_id={purchase_id}, user={telegram_id}"
                logger.error(f"finalize_purchase: {error_msg}")
                raise Exception(error_msg)
            
            # КРИТИЧНО: Валидация VPN ключа ПЕРЕД финализацией платежа
            import vpn_utils
            if not vpn_utils.validate_vless_link(vpn_key):
                error_msg = (
                    f"VPN key validation failed (contains forbidden flow= parameter): "
                    f"purchase_id={purchase_id}, user={telegram_id}"
                )
                logger.error(f"finalize_purchase: VPN_KEY_VALIDATION_FAILED: {error_msg}")
                raise Exception(error_msg)
            
            # STEP 6: Обновляем payment → approved
            await conn.execute(
                "UPDATE payments SET status = 'approved' WHERE id = $1",
                payment_id
            )
            
            # STEP 7: Потребляем промокод (если был использован) - atomic UPDATE ... RETURNING
            if promo_code:
                await _consume_promo_in_transaction(conn, promo_code, telegram_id, purchase_id)
            
            # STEP 8: Обрабатываем реферальный кешбэк
            # Обработка реферального кешбэка внутри той же транзакции
            # FINANCIAL errors будут проброшены и откатят всю транзакцию
            # BUSINESS errors вернут success=False и покупка продолжится без награды
            referral_reward_result = await process_referral_reward(
                buyer_id=telegram_id,
                purchase_id=purchase_id,
                amount_rubles=amount_rubles,
                conn=conn
            )
            
            if referral_reward_result.get("success"):
                logger.info(
                    f"finalize_purchase: referral_reward_processed: purchase_id={purchase_id}, "
                    f"user={telegram_id}, referrer={referral_reward_result.get('referrer_id')}, "
                    f"amount={referral_reward_result.get('reward_amount')} RUB"
                )
            else:
                # BUSINESS LOGIC ERROR: Reward skipped but purchase continues
                reason = referral_reward_result.get("reason", "unknown")
                logger.info(
                    f"finalize_purchase: Purchase finalized without referral reward: "
                    f"purchase_id={purchase_id}, user={telegram_id}, reason={reason}"
                )
            
            # КРИТИЧНО: Логируем активацию подписки и выдачу ключа для аудита
            logger.info(
                f"subscription_activated: purchase_id={purchase_id}, user={telegram_id}, "
                f"provider={payment_provider}, payment_id={payment_id}, "
                f"expires_at={expires_at.isoformat()}, is_renewal={is_renewal}"
            )
            
            logger.info(
                f"vpn_key_issued: purchase_id={purchase_id}, user={telegram_id}, "
                f"provider={payment_provider}, payment_id={payment_id}, "
                f"vpn_key_length={len(vpn_key)}, is_renewal={is_renewal}"
            )
            
            logger.info(
                f"finalize_purchase: SUCCESS [purchase_id={purchase_id}, user={telegram_id}, provider={payment_provider}, "
                f"payment_id={payment_id}, expires_at={expires_at.isoformat()}, "
                f"is_renewal={is_renewal}, vpn_key_length={len(vpn_key)}, subscription_activated=True, vpn_key_issued=True]"
            )
            
            return {
                "success": True,
                "payment_id": payment_id,
                "expires_at": expires_at,
                "vpn_key": vpn_key,
                "is_renewal": is_renewal,
                "referral_reward": referral_reward_result  # Добавляем результат реферального кешбэка
            }


async def expire_old_pending_purchases() -> int:
    """
    Автоматически помечает истёкшие pending покупки как expired
    
    Returns:
        Количество истёкших покупок
    """
    # Защита от работы с неинициализированной БД
    if not DB_READY:
        logger.warning("DB not ready, expire_old_pending_purchases skipped")
        return 0
    pool = await get_pool()
    if pool is None:
        logger.warning("Pool is None, expire_old_pending_purchases skipped")
        return 0
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE pending_purchases SET status = 'expired' WHERE status = 'pending' AND expires_at <= NOW()"
        )
        
        # Извлекаем количество обновлённых строк из результата
        # Формат результата: "UPDATE N"
        if result and result.startswith("UPDATE "):
            count = int(result.split()[1])
            if count > 0:
                logger.info(f"Expired {count} old pending purchases")
            return count
        return 0


async def get_all_users_for_export() -> list:
    """Получить всех пользователей для экспорта
    
    Returns:
        Список словарей с данными пользователей
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM users ORDER BY created_at DESC")
        return [dict(row) for row in rows]


async def get_active_subscriptions_for_export() -> list:
    """Получить все активные подписки для экспорта
    
    Returns:
        Список словарей с данными активных подписок
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        now = datetime.now(timezone.utc)
        rows = await conn.fetch(
            "SELECT * FROM subscriptions WHERE expires_at > $1 ORDER BY expires_at DESC",
            now
        )
        return [dict(row) for row in rows]


# Функция get_vpn_keys_stats удалена - больше не используется
# VPN-ключи теперь создаются динамически через Outline API, статистика по пулу не актуальна


async def get_subscription_history(telegram_id: int, limit: int = 5) -> list:
    """Получить историю подписок пользователя
    
    Args:
        telegram_id: Telegram ID пользователя
        limit: Максимальное количество записей (по умолчанию 5)
    
    Returns:
        Список словарей с записями истории, отсортированные по created_at DESC
    """
    # Защита от работы с неинициализированной БД
    if not DB_READY:
        logger.warning("DB not ready, get_subscription_history skipped")
        return []
    pool = await get_pool()
    if pool is None:
        logger.warning("Pool is None, get_subscription_history skipped")
        return []
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT * FROM subscription_history 
               WHERE telegram_id = $1 
               ORDER BY created_at DESC 
               LIMIT $2""",
            telegram_id, limit
        )
        return [dict(row) for row in rows]


async def get_user_extended_stats(telegram_id: int) -> Dict[str, Any]:
    """Получить расширенную статистику пользователя
    
    Args:
        telegram_id: Telegram ID пользователя
    
    Returns:
        Словарь со статистикой:
        - renewals_count: количество продлений подписки
        - reissues_count: количество перевыпусков ключа
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Подсчитываем продления (action_type = 'renewal')
        renewals_count = await conn.fetchval(
            """SELECT COUNT(*) FROM subscription_history 
               WHERE telegram_id = $1 AND action_type = 'renewal'""",
            telegram_id
        )
        
        # Подсчитываем перевыпуски ключа (action_type IN ('reissue', 'manual_reissue'))
        reissues_count = await conn.fetchval(
            """SELECT COUNT(*) FROM subscription_history 
               WHERE telegram_id = $1 AND action_type IN ('reissue', 'manual_reissue')""",
            telegram_id
        )
        
        return {
            "renewals_count": renewals_count or 0,
            "reissues_count": reissues_count or 0
        }


async def get_business_metrics() -> Dict[str, Any]:
    """Получить бизнес-метрики сервиса
    
    Returns:
        Словарь с метриками:
        - avg_payment_approval_time_seconds: среднее время подтверждения оплаты (в секундах)
        - avg_subscription_lifetime_days: среднее время жизни подписки (в днях)
        - avg_renewals_per_user: среднее количество продлений на пользователя
        - approval_rate_percent: процент подтвержденных платежей
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        # 1. Среднее время подтверждения оплаты
        # Используем audit_log для получения времени подтверждения
        # Парсим Payment ID из details поля через CTE
        avg_approval_time = await conn.fetchval(
            """WITH payment_approvals AS (
                SELECT 
                    al.created_at as approved_at,
                    CAST(SUBSTRING(al.details FROM 'Payment ID: ([0-9]+)') AS INTEGER) as payment_id
                FROM audit_log al
                WHERE al.action IN ('payment_approved', 'subscription_renewed')
                AND al.details LIKE 'Payment ID: %'
            )
            SELECT AVG(EXTRACT(EPOCH FROM (pa.approved_at - p.created_at))) 
            FROM payment_approvals pa
            JOIN payments p ON p.id = pa.payment_id
            WHERE p.status = 'approved'"""
        )
        
        # 2. Среднее время жизни подписки (из subscription_history)
        # Используем только завершенные подписки (end_date < now)
        avg_lifetime = await conn.fetchval(
            """SELECT AVG(EXTRACT(EPOCH FROM (end_date - start_date)) / 86400.0)
               FROM subscription_history
               WHERE end_date IS NOT NULL
               AND end_date < NOW()"""
        )
        
        # 3. Среднее количество продлений на пользователя
        total_renewals = await conn.fetchval(
            """SELECT COUNT(*) FROM subscription_history WHERE action_type = 'renewal'"""
        )
        total_users_with_subscriptions = await conn.fetchval(
            """SELECT COUNT(DISTINCT telegram_id) FROM subscription_history"""
        )
        avg_renewals = 0.0
        if total_users_with_subscriptions and total_users_with_subscriptions > 0:
            avg_renewals = (total_renewals or 0) / total_users_with_subscriptions
        
        # 4. Процент подтвержденных платежей
        total_payments = await conn.fetchval("SELECT COUNT(*) FROM payments")
        approved_payments = await conn.fetchval(
            "SELECT COUNT(*) FROM payments WHERE status = 'approved'"
        )
        approval_rate = 0.0
        if total_payments and total_payments > 0:
            approval_rate = ((approved_payments or 0) / total_payments) * 100
        
        return {
            "avg_payment_approval_time_seconds": float(avg_approval_time) if avg_approval_time else None,
            "avg_subscription_lifetime_days": float(avg_lifetime) if avg_lifetime else None,
            "avg_renewals_per_user": float(avg_renewals) if avg_renewals else 0.0,
            "approval_rate_percent": float(approval_rate) if approval_rate else 0.0,
        }


async def get_last_audit_logs(limit: int = 10) -> list:
    """Получить последние записи из audit_log
    
    Args:
        limit: Количество записей для получения (по умолчанию 10)
    
    Returns:
        Список словарей с записями audit_log, отсортированных по created_at DESC
    """
    if not DB_READY:
        logger.warning("DB not ready (degraded mode), get_last_audit_logs skipped")
        return []
    
    pool = await get_pool()
    if pool is None:
        return []
    
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT * FROM audit_log 
                   ORDER BY created_at DESC 
                   LIMIT $1""",
                limit
            )
            return [dict(row) for row in rows]
    except (asyncpg.UndefinedTableError, asyncpg.PostgresError) as e:
        logger.warning(f"audit_log table missing or inaccessible — skipping: {e}")
        return []
    except Exception as e:
        logger.warning(f"Error getting audit logs: {e}")
        return []


async def create_broadcast(title: str, message: str, broadcast_type: str, segment: str, sent_by: int, is_ab_test: bool = False, message_a: str = None, message_b: str = None) -> int:
    """Создать новое уведомление
    
    Args:
        title: Заголовок уведомления
        message: Текст уведомления (для обычных уведомлений)
        broadcast_type: Тип уведомления (info | maintenance | security | promo)
        segment: Сегмент получателей (all_users | active_subscriptions)
        sent_by: Telegram ID администратора
        is_ab_test: Является ли уведомление A/B тестом
        message_a: Текст варианта A (для A/B тестов)
        message_b: Текст варианта B (для A/B тестов)
    
    Returns:
        ID созданного уведомления
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        if is_ab_test:
            row = await conn.fetchrow(
                """INSERT INTO broadcasts (title, message_a, message_b, is_ab_test, type, segment, sent_by)
                   VALUES ($1, $2, $3, TRUE, $4, $5, $6)
                   RETURNING id""",
                title, message_a, message_b, broadcast_type, segment, sent_by
            )
        else:
            row = await conn.fetchrow(
                """INSERT INTO broadcasts (title, message, is_ab_test, type, segment, sent_by)
                   VALUES ($1, $2, FALSE, $3, $4, $5)
                   RETURNING id""",
                title, message, broadcast_type, segment, sent_by
            )
        return row["id"]


async def get_broadcast(broadcast_id: int) -> Optional[Dict[str, Any]]:
    """Получить уведомление по ID"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM broadcasts WHERE id = $1", broadcast_id
        )
        return dict(row) if row else None


async def get_all_users_telegram_ids() -> list:
    """Получить список всех Telegram ID пользователей"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT telegram_id FROM users")
        return [row["telegram_id"] for row in rows]


async def get_eligible_no_subscription_broadcast_users() -> list:
    """Get users eligible for no-subscription broadcast.
    Eligible = no active paid subscription, no active trial, is_reachable=TRUE.
    Returns list of dicts with telegram_id. Defensive: fallback if is_reachable missing.
    """
    if not DB_READY:
        logger.warning("DB not ready, get_eligible_no_subscription_broadcast_users skipped")
        return []
    pool = await get_pool()
    if pool is None:
        return []
    async with pool.acquire() as conn:
        now = datetime.now(timezone.utc)
        query_with_reachable = """
            SELECT u.telegram_id
            FROM users u
            LEFT JOIN subscriptions paid_s ON paid_s.telegram_id = u.telegram_id
                AND paid_s.status = 'active'
                AND paid_s.expires_at > $1
                AND paid_s.source != 'trial'
            WHERE paid_s.id IS NULL
              AND (u.trial_expires_at IS NULL OR u.trial_expires_at <= $1)
              AND COALESCE(u.is_reachable, TRUE) = TRUE
        """
        fallback_query = """
            SELECT u.telegram_id
            FROM users u
            LEFT JOIN subscriptions paid_s ON paid_s.telegram_id = u.telegram_id
                AND paid_s.status = 'active'
                AND paid_s.expires_at > $1
                AND paid_s.source != 'trial'
            WHERE paid_s.id IS NULL
              AND (u.trial_expires_at IS NULL OR u.trial_expires_at <= $1)
        """
        try:
            rows = await conn.fetch(query_with_reachable, now)
        except asyncpg.UndefinedColumnError:
            logger.warning("DB_SCHEMA_OUTDATED: is_reachable missing, no_sub_broadcast fallback")
            rows = await conn.fetch(fallback_query, now)
        return [{"telegram_id": row["telegram_id"]} for row in rows]


async def check_user_still_eligible_for_no_sub_broadcast(conn, telegram_id: int, now: datetime) -> bool:
    """Race-condition re-check before sending. Returns True if still eligible."""
    paid = await get_active_paid_subscription(conn, telegram_id, now)
    if paid:
        return False
    try:
        row = await conn.fetchrow(
            "SELECT trial_expires_at, is_reachable FROM users WHERE telegram_id = $1",
            telegram_id
        )
    except asyncpg.UndefinedColumnError:
        row = await conn.fetchrow(
            "SELECT trial_expires_at FROM users WHERE telegram_id = $1",
            telegram_id
        )
    if not row:
        return False
    trial_expires_at = row.get("trial_expires_at")
    if trial_expires_at and trial_expires_at > now:
        return False
    is_reachable = row.get("is_reachable")
    if is_reachable is False:
        return False
    return True


async def insert_admin_broadcast_record(
    broadcast_type: str,
    total_recipients: int,
    success_count: int = 0,
    fail_count: int = 0
) -> Optional[int]:
    """Insert admin_broadcasts record. Returns id or None."""
    if not DB_READY:
        return None
    try:
        pool = await get_pool()
        if pool is None:
            return None
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """INSERT INTO admin_broadcasts (type, total_recipients, success_count, fail_count)
                   VALUES ($1, $2, $3, $4) RETURNING id""",
                broadcast_type, total_recipients, success_count, fail_count
            )
            return row["id"] if row else None
    except asyncpg.UndefinedTableError:
        logger.debug("admin_broadcasts table not found, skipping audit")
        return None
    except Exception as e:
        logger.warning(f"Failed to insert admin_broadcast record: {e}")
        return None


async def update_admin_broadcast_record(broadcast_id: int, success_count: int, fail_count: int) -> None:
    """Update admin_broadcasts record after completion."""
    if not DB_READY or broadcast_id is None:
        return
    try:
        pool = await get_pool()
        if pool is None:
            return
        async with pool.acquire() as conn:
            await conn.execute(
                """UPDATE admin_broadcasts
                   SET success_count = $1, fail_count = $2 WHERE id = $3""",
                success_count, fail_count, broadcast_id
            )
    except Exception as e:
        logger.warning(f"Failed to update admin_broadcast record: {e}")


async def get_users_by_segment(segment: str) -> list:
    """Получить список Telegram ID пользователей по сегменту
    
    Args:
        segment: Сегмент получателей (all_users | active_subscriptions)
    
    Returns:
        Список Telegram ID пользователей
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        if segment == "all_users":
            rows = await conn.fetch("SELECT telegram_id FROM users")
            return [row["telegram_id"] for row in rows]
        elif segment == "active_subscriptions":
            now = datetime.now(timezone.utc)
            rows = await conn.fetch(
                """SELECT DISTINCT u.telegram_id 
                   FROM users u
                   INNER JOIN subscriptions s ON u.telegram_id = s.telegram_id
                   WHERE s.expires_at > $1""",
                now
            )
            return [row["telegram_id"] for row in rows]
        else:
            logging.warning(f"Unknown segment: {segment}, returning empty list")
            return []


async def log_broadcast_send(broadcast_id: int, telegram_id: int, status: str, variant: str = None):
    """Записать результат отправки уведомления
    
    Args:
        broadcast_id: ID уведомления
        telegram_id: Telegram ID пользователя
        status: Статус отправки (sent | failed)
        variant: Вариант сообщения (A или B для A/B тестов)
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO broadcast_log (broadcast_id, telegram_id, status, variant)
               VALUES ($1, $2, $3, $4)""",
            broadcast_id, telegram_id, status, variant
        )


async def get_broadcast_stats(broadcast_id: int) -> Dict[str, int]:
    """Получить статистику отправки уведомления
    
    Args:
        broadcast_id: ID уведомления
    
    Returns:
        Словарь с количеством отправленных и неудачных отправок
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        sent_count = await conn.fetchval(
            "SELECT COUNT(*) FROM broadcast_log WHERE broadcast_id = $1 AND status = 'sent'",
            broadcast_id
        )
        failed_count = await conn.fetchval(
            "SELECT COUNT(*) FROM broadcast_log WHERE broadcast_id = $1 AND status = 'failed'",
            broadcast_id
        )
        return {"sent": sent_count or 0, "failed": failed_count or 0}


async def get_incident_settings() -> Dict[str, Any]:
    """Получить настройки инцидента
    
    Returns:
        Словарь с is_active и incident_text
    """
    if not DB_READY:
        logger.warning("DB not ready (degraded mode), get_incident_settings skipped")
        return {"is_active": False, "incident_text": None}
    
    pool = await get_pool()
    if pool is None:
        return {"is_active": False, "incident_text": None}
    
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT is_active, incident_text FROM incident_settings ORDER BY id LIMIT 1"
            )
            if row:
                return {"is_active": row["is_active"], "incident_text": row["incident_text"]}
            return {"is_active": False, "incident_text": None}
    except (asyncpg.UndefinedTableError, asyncpg.PostgresError) as e:
        logger.warning(f"incident_settings table missing or inaccessible — skipping: {e}")
        return {"is_active": False, "incident_text": None}
    except Exception as e:
        logger.warning(f"Error getting incident settings: {e}")
        return {"is_active": False, "incident_text": None}


async def set_incident_mode(is_active: bool, incident_text: Optional[str] = None):
    """Установить режим инцидента
    
    Args:
        is_active: Активен ли режим инцидента
        incident_text: Текст инцидента (опционально)
    """
    if not DB_READY:
        logger.warning("DB not ready (degraded mode), set_incident_mode skipped")
        return
    
    pool = await get_pool()
    if pool is None:
        logger.warning("Pool is None, set_incident_mode skipped")
        return
    
    try:
        async with pool.acquire() as conn:
            if incident_text is not None:
                await conn.execute(
                    """UPDATE incident_settings 
                       SET is_active = $1, incident_text = $2, updated_at = CURRENT_TIMESTAMP
                       WHERE id = (SELECT id FROM incident_settings ORDER BY id LIMIT 1)""",
                    is_active, incident_text
                )
            else:
                await conn.execute(
                    """UPDATE incident_settings 
                       SET is_active = $1, updated_at = CURRENT_TIMESTAMP
                       WHERE id = (SELECT id FROM incident_settings ORDER BY id LIMIT 1)""",
                    is_active
                )
    except (asyncpg.UndefinedTableError, asyncpg.PostgresError) as e:
        logger.warning(f"incident_settings table missing or inaccessible — skipping: {e}")
    except Exception as e:
        logger.warning(f"Error setting incident mode: {e}")


async def get_ab_test_broadcasts() -> list:
    """Получить список всех A/B тестов (уведомлений с is_ab_test = true)
    
    Returns:
        Список словарей с данными A/B тестов, отсортированных по created_at DESC
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, title, created_at 
               FROM broadcasts 
               WHERE is_ab_test = TRUE 
               ORDER BY created_at DESC"""
        )
        return [dict(row) for row in rows]


async def get_incident_settings() -> Dict[str, Any]:
    """Получить настройки инцидента
    
    Returns:
        Словарь с is_active и incident_text
    """
    if not DB_READY:
        logger.warning("DB not ready (degraded mode), get_incident_settings skipped")
        return {"is_active": False, "incident_text": None}
    
    pool = await get_pool()
    if pool is None:
        return {"is_active": False, "incident_text": None}
    
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT is_active, incident_text FROM incident_settings ORDER BY id LIMIT 1"
            )
            if row:
                return {"is_active": row["is_active"], "incident_text": row["incident_text"]}
            return {"is_active": False, "incident_text": None}
    except (asyncpg.UndefinedTableError, asyncpg.PostgresError) as e:
        logger.warning(f"incident_settings table missing or inaccessible — skipping: {e}")
        return {"is_active": False, "incident_text": None}
    except Exception as e:
        logger.warning(f"Error getting incident settings: {e}")
        return {"is_active": False, "incident_text": None}


async def set_incident_mode(is_active: bool, incident_text: Optional[str] = None):
    """Установить режим инцидента
    
    Args:
        is_active: Активен ли режим инцидента
        incident_text: Текст инцидента (опционально)
    """
    if not DB_READY:
        logger.warning("DB not ready (degraded mode), set_incident_mode skipped")
        return
    
    pool = await get_pool()
    if pool is None:
        logger.warning("Pool is None, set_incident_mode skipped")
        return
    
    try:
        async with pool.acquire() as conn:
            if incident_text is not None:
                await conn.execute(
                    """UPDATE incident_settings 
                       SET is_active = $1, incident_text = $2, updated_at = CURRENT_TIMESTAMP
                       WHERE id = (SELECT id FROM incident_settings ORDER BY id LIMIT 1)""",
                    is_active, incident_text
                )
            else:
                await conn.execute(
                    """UPDATE incident_settings 
                       SET is_active = $1, updated_at = CURRENT_TIMESTAMP
                       WHERE id = (SELECT id FROM incident_settings ORDER BY id LIMIT 1)""",
                    is_active
                )
    except (asyncpg.UndefinedTableError, asyncpg.PostgresError) as e:
        logger.warning(f"incident_settings table missing or inaccessible — skipping: {e}")
    except Exception as e:
        logger.warning(f"Error setting incident mode: {e}")


async def get_ab_test_stats(broadcast_id: int) -> Optional[Dict[str, Any]]:
    """Получить статистику A/B теста
    
    Args:
        broadcast_id: ID уведомления (должно быть A/B тестом)
    
    Returns:
        Словарь с статистикой:
        - variant_a_sent: количество отправок варианта A
        - variant_b_sent: количество отправок варианта B
        - variant_a_failed: количество неудачных отправок варианта A
        - variant_b_failed: количество неудачных отправок варианта B
        - total_sent: общее количество отправленных
        - total: общее количество (sent + failed)
        Или None, если данных недостаточно
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Проверяем, что это A/B тест
        broadcast = await conn.fetchrow(
            "SELECT is_ab_test FROM broadcasts WHERE id = $1", broadcast_id
        )
        if not broadcast or not broadcast["is_ab_test"]:
            return None
        
        # Статистика по варианту A
        variant_a_sent = await conn.fetchval(
            "SELECT COUNT(*) FROM broadcast_log WHERE broadcast_id = $1 AND variant = 'A' AND status = 'sent'",
            broadcast_id
        )
        variant_a_failed = await conn.fetchval(
            "SELECT COUNT(*) FROM broadcast_log WHERE broadcast_id = $1 AND variant = 'A' AND status = 'failed'",
            broadcast_id
        )
        
        # Статистика по варианту B
        variant_b_sent = await conn.fetchval(
            "SELECT COUNT(*) FROM broadcast_log WHERE broadcast_id = $1 AND variant = 'B' AND status = 'sent'",
            broadcast_id
        )
        variant_b_failed = await conn.fetchval(
            "SELECT COUNT(*) FROM broadcast_log WHERE broadcast_id = $1 AND variant = 'B' AND status = 'failed'",
            broadcast_id
        )
        
        variant_a_sent = variant_a_sent or 0
        variant_a_failed = variant_a_failed or 0
        variant_b_sent = variant_b_sent or 0
        variant_b_failed = variant_b_failed or 0
        
        total_sent = variant_a_sent + variant_b_sent
        total_failed = variant_a_failed + variant_b_failed
        total = total_sent + total_failed
        
        if total == 0:
            return None
        
        return {
            "variant_a_sent": variant_a_sent,
            "variant_b_sent": variant_b_sent,
            "variant_a_failed": variant_a_failed,
            "variant_b_failed": variant_b_failed,
            "total_sent": total_sent,
            "total": total
        }


async def admin_grant_access_atomic(telegram_id: int, days: int, admin_telegram_id: int) -> Tuple[datetime, str]:
    """Атомарно выдать доступ пользователю на N дней (админ)
    
    Использует единую функцию grant_access (защищена от двойного создания ключей).
    
    Args:
        telegram_id: Telegram ID пользователя
        days: Количество дней доступа (1, 7 или 14)
        admin_telegram_id: Telegram ID администратора
    
    Returns:
        Tuple[datetime, str]: (expires_at, vpn_key)
        - expires_at: Дата истечения подписки
        - vpn_key: VPN ключ (vless_url для нового UUID, vpn_key из подписки для продления, или uuid как fallback)
    
    Raises:
        Exception: При любых ошибках (транзакция откатывается, исключение пробрасывается)
        Гарантированно возвращает значения или выбрасывает исключение. Никогда не возвращает None.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            try:
                duration = timedelta(days=days)
                
                # Используем единую функцию grant_access
                result = await grant_access(
                    telegram_id=telegram_id,
                    duration=duration,
                    source="admin",
                    admin_telegram_id=admin_telegram_id,
                    admin_grant_days=days,
                    conn=conn
                )
                
                expires_at = result["subscription_end"]
                # Если vless_url есть - это новый UUID, используем его
                # Если vless_url нет - это продление, получаем vpn_key из подписки
                if result.get("vless_url"):
                    final_vpn_key = result["vless_url"]
                else:
                    # Продление - получаем vpn_key из существующей подписки
                    subscription_row = await conn.fetchrow(
                        "SELECT vpn_key FROM subscriptions WHERE telegram_id = $1",
                        telegram_id
                    )
                    if subscription_row and subscription_row.get("vpn_key"):
                        final_vpn_key = subscription_row["vpn_key"]
                    else:
                        # Fallback: используем UUID
                        final_vpn_key = result.get("uuid", "")
                
                uuid_preview = f"{result['uuid'][:8]}..." if result.get('uuid') and len(result['uuid']) > 8 else (result.get('uuid') or "N/A")
                logger.info(f"admin_grant_access_atomic: SUCCESS [admin={admin_telegram_id}, user={telegram_id}, days={days}, uuid={uuid_preview}, expires_at={expires_at.isoformat()}]")
                return expires_at, final_vpn_key
                
            except Exception as e:
                logger.exception(f"Error in admin_grant_access_atomic for user {telegram_id}, transaction rolled back")
                raise


async def finalize_balance_purchase(
    telegram_id: int,
    tariff_type: str,
    period_days: int,
    amount_rubles: float,
    description: Optional[str] = None,
    promo_code: Optional[str] = None
) -> Dict[str, Any]:
    """
    Атомарно обработать покупку подписки с баланса.
    
    Выполняет в одной транзакции:
    - Списывает баланс
    - Активирует подписку
    - Создает запись о платеже
    - Обрабатывает реферальный кешбэк
    
    Args:
        telegram_id: Telegram ID пользователя
        tariff_type: Тип тарифа ('basic' или 'plus')
        period_days: Количество дней подписки
        amount_rubles: Сумма платежа в рублях
        description: Описание платежа (опционально)
        promo_code: Промокод (опционально, потребляется внутри транзакции)
    
    Returns:
        {
            "success": bool,
            "payment_id": Optional[int],
            "expires_at": Optional[datetime],
            "vpn_key": Optional[str],
            "is_renewal": bool,
            "new_balance": float,
            "referral_reward": Optional[Dict[str, Any]]
        }
    
    Raises:
        ValueError: При недостатке баланса или других бизнес-ошибках
        asyncpg exceptions: При финансовых ошибках (откат транзакции)
    """
    if amount_rubles <= 0:
        raise ValueError(f"Invalid amount for balance purchase: {amount_rubles}")
    
    amount_kopecks = int(amount_rubles * 100)
    pool = await get_pool()
    
    if pool is None:
        raise RuntimeError("Database pool is not available")
    
    async with pool.acquire() as conn:
        async with conn.transaction():
            # CRITICAL: advisory lock per user для защиты от race conditions
            await conn.execute(
                "SELECT pg_advisory_xact_lock($1)",
                telegram_id
            )
            
            # STEP 1: Проверяем и списываем баланс (SELECT FOR UPDATE для блокировки строки)
            row = await conn.fetchrow(
                "SELECT balance FROM users WHERE telegram_id = $1 FOR UPDATE",
                telegram_id
            )
            
            if not row:
                raise ValueError(f"User {telegram_id} not found")
            
            current_balance = row["balance"]
            
            if current_balance < amount_kopecks:
                raise ValueError(
                    f"Insufficient balance: {current_balance} < {amount_kopecks} "
                    f"(user={telegram_id}, required={amount_rubles:.2f} RUB)"
                )
            
            # Списываем баланс
            new_balance = current_balance - amount_kopecks
            await conn.execute(
                "UPDATE users SET balance = $1 WHERE telegram_id = $2",
                new_balance, telegram_id
            )
            
            # Записываем транзакцию баланса
            transaction_description = description or f"Оплата подписки {tariff_type} на {period_days} дней"
            await conn.execute(
                """INSERT INTO balance_transactions (user_id, amount, type, source, description)
                   VALUES ($1, $2, $3, $4, $5)""",
                telegram_id, -amount_kopecks, "subscription_payment", "subscription_payment", transaction_description
            )
            
            # STEP 1.5: Потребляем промокод (если был использован) - atomic UPDATE ... RETURNING
            if promo_code:
                await _consume_promo_in_transaction(conn, promo_code, telegram_id, None)
            
            # STEP 2: Активируем подписку
            duration = timedelta(days=period_days)
            grant_result = await grant_access(
                telegram_id=telegram_id,
                duration=duration,
                source="payment",
                admin_telegram_id=None,
                admin_grant_days=None,
                conn=conn
            )
            
            expires_at = grant_result["subscription_end"]
            vpn_key = grant_result.get("vless_url") or grant_result.get("vpn_key") or ""
            action = grant_result.get("action")
            is_renewal = action == "renewal"
            
            # expires_at is ALWAYS required (for both new and renewal)
            if not expires_at:
                raise ValueError(
                    f"grant_access returned invalid result: expires_at={expires_at}"
                )
            
            # vpn_key is required ONLY for new subscriptions (not for renewals)
            if action != "renewal" and not vpn_key:
                raise ValueError(
                    "grant_access returned invalid result for NEW subscription: vpn_key is missing"
                )
            
            # STEP 3: Создаем запись о платеже
            payment_id = await conn.fetchval(
                "INSERT INTO payments (telegram_id, tariff, amount, status) VALUES ($1, $2, $3, 'approved') RETURNING id",
                telegram_id, f"{tariff_type}_{period_days}", amount_kopecks
            )
            
            if not payment_id:
                raise ValueError(f"Failed to create payment record for user {telegram_id}")
            
            # STEP 4: Обрабатываем реферальный кешбэк
            purchase_id = f"balance_purchase_{payment_id}"
            referral_reward_result = None
            
            try:
                referral_reward_result = await process_referral_reward(
                    buyer_id=telegram_id,
                    purchase_id=purchase_id,
                    amount_rubles=amount_rubles,
                    conn=conn
                )
            except Exception as e:
                # FINANCIAL errors propagate and rollback transaction
                logger.error(
                    f"finalize_balance_purchase: Referral reward financial error "
                    f"(transaction will rollback): user={telegram_id}, purchase_id={purchase_id}, error={e}"
                )
                raise
            
            # STEP 5: Получаем новый баланс
            new_balance_kopecks = await conn.fetchval(
                "SELECT balance FROM users WHERE telegram_id = $1", telegram_id
            )
            new_balance = (new_balance_kopecks or 0) / 100.0
            
            logger.info(
                f"finalize_balance_purchase: SUCCESS [user={telegram_id}, payment_id={payment_id}, "
                f"tariff={tariff_type}, period={period_days}, amount={amount_rubles:.2f} RUB, "
                f"expires_at={expires_at.isoformat()}, is_renewal={is_renewal}, "
                f"new_balance={new_balance:.2f} RUB, referral_reward_success={referral_reward_result.get('success') if referral_reward_result else False}]"
            )
            
            return {
                "success": True,
                "payment_id": payment_id,
                "expires_at": expires_at,
                "vpn_key": vpn_key,
                "is_renewal": is_renewal,
                "new_balance": new_balance,
                "referral_reward": referral_reward_result
            }


async def finalize_balance_topup(
    telegram_id: int,
    amount_rubles: float,
    provider: str,
    provider_charge_id: str,
    description: Optional[str] = None,
    correlation_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Атомарно обработать пополнение баланса с идемпотентностью.
    
    КРИТИЧЕСКИ ВАЖНО: Эта функция идемпотентна по provider_charge_id.
    Повторный вызов с тем же provider_charge_id НЕ увеличит баланс.
    
    Выполняет в одной транзакции:
    - Проверяет идемпотентность (по provider_charge_id)
    - Пополняет баланс (если не дубликат)
    - Создает запись о платеже
    - Обрабатывает реферальный кешбэк
    
    Args:
        telegram_id: Telegram ID пользователя
        amount_rubles: Сумма пополнения в рублях
        provider: Провайдер платежа ('telegram' или 'cryptobot')
        provider_charge_id: Уникальный ID платежа от провайдера (для идемпотентности)
        description: Описание платежа (опционально)
        correlation_id: ID для корреляции логов (опционально)
    
    Returns:
        {
            "success": bool,
            "payment_id": Optional[int],
            "new_balance": float,
            "referral_reward": Optional[Dict[str, Any]],
            "reason": Optional[str]  # "already_processed" if duplicate
        }
    
    Raises:
        ValueError: При некорректной сумме или отсутствии provider_charge_id
        asyncpg exceptions: При финансовых ошибках (откат транзакции)
    """
    if amount_rubles <= 0:
        raise ValueError(f"Invalid amount for balance topup: {amount_rubles}")
    
    if not provider_charge_id:
        raise ValueError("provider_charge_id is required for idempotency")
    
    if provider not in ("telegram", "cryptobot"):
        raise ValueError(f"Invalid provider: {provider}. Must be 'telegram' or 'cryptobot'")
    
    amount_kopecks = int(amount_rubles * 100)
    pool = await get_pool()
    
    if pool is None:
        raise RuntimeError("Database pool is not available")
    
    async with pool.acquire() as conn:
        async with conn.transaction():
            # STEP 1: SCHEMA SAFETY CHECK (P0 HOTFIX - prevent silent failures)
            # Defensive check: ensure idempotency columns exist before querying
            column_exists = await conn.fetchval(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'payments'
                  AND column_name = $1
                """,
                'telegram_payment_charge_id' if provider == 'telegram' else 'cryptobot_payment_id'
            )
            
            if not column_exists:
                error_msg = (
                    f"CRITICAL_SCHEMA_MISMATCH: payments.{'telegram_payment_charge_id' if provider == 'telegram' else 'cryptobot_payment_id'} "
                    f"column missing. Migration 012 may not have been applied correctly. "
                    f"Provider: {provider}, provider_charge_id: {provider_charge_id}"
                )
                logger.error(error_msg)
                raise RuntimeError(error_msg)
            
            # STEP 2: IDEMPOTENCY CHECK (CRITICAL - at the very start)
            existing_payment = await conn.fetchrow(
                """
                SELECT id, telegram_id, amount, status
                FROM payments
                WHERE telegram_payment_charge_id = $1
                   OR cryptobot_payment_id = $1
                """,
                provider_charge_id
            )
            
            if existing_payment:
                logger.warning(
                    f"BALANCE_TOPUP_DUPLICATE_SKIPPED [provider={provider}, "
                    f"provider_charge_id={provider_charge_id}, telegram_id={telegram_id}, "
                    f"correlation_id={correlation_id}, existing_payment_id={existing_payment['id']}]"
                )
                # Return existing payment info without modifying balance
                existing_balance_kopecks = await conn.fetchval(
                    "SELECT balance FROM users WHERE telegram_id = $1", telegram_id
                )
                existing_balance = (existing_balance_kopecks or 0) / 100.0
                
                return {
                    "success": False,
                    "payment_id": existing_payment["id"],
                    "new_balance": existing_balance,
                    "referral_reward": None,
                    "reason": "already_processed"
                }
            
            # STEP 3: Проверяем существование пользователя
            user_exists = await conn.fetchval(
                "SELECT telegram_id FROM users WHERE telegram_id = $1", telegram_id
            )
            
            if user_exists is None:
                raise ValueError(f"User {telegram_id} not found")
            
            # STEP 4: ATOMIC INSERT + CREDIT (payment record FIRST, then balance)
            # Insert payment record with idempotency key
            payment_id = await conn.fetchval(
                """
                INSERT INTO payments (
                    telegram_id,
                    tariff,
                    amount,
                    status,
                    telegram_payment_charge_id,
                    cryptobot_payment_id
                )
                VALUES (
                    $1, $2, $3, 'approved',
                    CASE WHEN $4 = 'telegram' THEN $5 ELSE NULL END,
                    CASE WHEN $4 = 'cryptobot' THEN $5 ELSE NULL END
                )
                RETURNING id
                """,
                telegram_id,
                "balance_topup",
                amount_kopecks,
                provider,
                provider_charge_id
            )
            
            if not payment_id:
                raise ValueError(f"Failed to create payment record for user {telegram_id}")
            
            # STEP 5: Пополняем баланс (AFTER payment record created)
            await conn.execute(
                "UPDATE users SET balance = balance + $1 WHERE telegram_id = $2",
                amount_kopecks, telegram_id
            )
            
            # STEP 6: Записываем транзакцию баланса
            transaction_description = description or f"Пополнение баланса через {provider}"
            transaction_type = "topup"
            await conn.execute(
                """INSERT INTO balance_transactions (user_id, amount, type, source, description)
                   VALUES ($1, $2, $3, $4, $5)""",
                telegram_id, amount_kopecks, transaction_type, provider, transaction_description
            )
            
            # STEP 7: Обрабатываем реферальный кешбэк
            purchase_id = f"balance_topup_{payment_id}"
            referral_reward_result = None
            
            try:
                referral_reward_result = await process_referral_reward(
                    buyer_id=telegram_id,
                    purchase_id=purchase_id,
                    amount_rubles=amount_rubles,
                    conn=conn
                )
            except Exception as e:
                # FINANCIAL errors propagate and rollback transaction
                logger.error(
                    f"finalize_balance_topup: Referral reward financial error "
                    f"(transaction will rollback): user={telegram_id}, purchase_id={purchase_id}, error={e}"
                )
                raise
            
            # STEP 8: Получаем новый баланс
            new_balance_kopecks = await conn.fetchval(
                "SELECT balance FROM users WHERE telegram_id = $1", telegram_id
            )
            new_balance = (new_balance_kopecks or 0) / 100.0
            
            logger.info(
                f"BALANCE_TOPUP_SUCCESS [user={telegram_id}, payment_id={payment_id}, "
                f"provider={provider}, provider_charge_id={provider_charge_id}, "
                f"amount={amount_rubles:.2f} RUB, new_balance={new_balance:.2f} RUB, "
                f"referral_reward_success={referral_reward_result.get('success') if referral_reward_result else False}, "
                f"correlation_id={correlation_id}]"
            )
            
            return {
                "success": True,
                "payment_id": payment_id,
                "new_balance": new_balance,
                "referral_reward": referral_reward_result
            }


async def admin_grant_access_minutes_atomic(telegram_id: int, minutes: int, admin_telegram_id: int) -> Tuple[datetime, str]:
    """Атомарно выдать доступ пользователю на N минут (админ)
    
    Использует единую функцию grant_access (защищена от двойного создания ключей).
    
    Args:
        telegram_id: Telegram ID пользователя
        minutes: Количество минут доступа (например, 10)
        admin_telegram_id: Telegram ID администратора
    
    Returns:
        Tuple[datetime, str]: (expires_at, vpn_key)
        - expires_at: Дата истечения подписки
        - vpn_key: VPN ключ (vless_url для нового UUID, vpn_key из подписки для продления, или uuid как fallback)
    
    Raises:
        Exception: При любых ошибках (транзакция откатывается, исключение пробрасывается)
        Гарантированно возвращает значения или выбрасывает исключение. Никогда не возвращает None.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            try:
                duration = timedelta(minutes=minutes)
                
                # Используем единую функцию grant_access
                result = await grant_access(
                    telegram_id=telegram_id,
                    duration=duration,
                    source="admin",
                    admin_telegram_id=admin_telegram_id,
                    admin_grant_days=None,  # Для минутного доступа не используется
                    conn=conn
                )
                
                expires_at = result["subscription_end"]
                # Если vless_url есть - это новый UUID, используем его
                # Если vless_url нет - это продление, получаем vpn_key из подписки
                if result.get("vless_url"):
                    final_vpn_key = result["vless_url"]
                else:
                    # Продление - получаем vpn_key из существующей подписки
                    subscription_row = await conn.fetchrow(
                        "SELECT vpn_key FROM subscriptions WHERE telegram_id = $1",
                        telegram_id
                    )
                    if subscription_row and subscription_row.get("vpn_key"):
                        final_vpn_key = subscription_row["vpn_key"]
                    else:
                        # Fallback: используем UUID
                        final_vpn_key = result.get("uuid", "")
                
                # Безопасное логирование UUID
                uuid_preview = f"{result['uuid'][:8]}..." if result.get('uuid') and len(result['uuid']) > 8 else (result.get('uuid') or "N/A")
                logger.info(
                    f"admin_grant_access_minutes_atomic: SUCCESS [admin={admin_telegram_id}, user={telegram_id}, "
                    f"minutes={minutes}, uuid={uuid_preview}, expires_at={expires_at.isoformat()}]"
                )
                return expires_at, final_vpn_key
                
            except Exception as e:
                logger.exception(f"Error in admin_grant_access_minutes_atomic for user {telegram_id}, transaction rolled back")
                raise


async def admin_revoke_access_atomic(telegram_id: int, admin_telegram_id: int) -> bool:
    """Атомарно лишить доступа пользователя (админ)
    
    В одной транзакции:
    - удаляет UUID из Xray API (если есть uuid)
    - устанавливает status = 'expired', expires_at = NOW()
    - очищает uuid и vpn_key
    - записывает в subscription_history (action = admin_revoke)
    - записывает событие в audit_log
    
    Args:
        telegram_id: Telegram ID пользователя
        admin_telegram_id: Telegram ID администратора
    
    Returns:
        True если доступ был отозван, False если активной подписки не было
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            try:
                now = datetime.now(timezone.utc)
                now_db = _to_db_utc(now)

                # 1. Проверяем, есть ли активная подписка
                subscription_row = await conn.fetchrow(
                    "SELECT * FROM subscriptions WHERE telegram_id = $1 AND expires_at > $2",
                    telegram_id, now_db
                )
                
                if not subscription_row:
                    logger.info(f"No active subscription to revoke for user {telegram_id}")
                    return False
                
                subscription = dict(subscription_row)
                old_expires_at = subscription["expires_at"]
                uuid = subscription.get("uuid")
                vpn_key = subscription.get("vpn_key", "")
                
                # 2. Удаляем UUID из Xray API (если есть)
                # PART D.7: If vpn_api != healthy, NEVER call VPN API
                if uuid:
                    try:
                        from app.core.system_state import recalculate_from_runtime, ComponentStatus
                        system_state = recalculate_from_runtime()
                        if system_state.vpn_api.status != ComponentStatus.HEALTHY:
                            logger.warning(
                                f"admin_revoke_access_atomic: VPN_API_DISABLED [user={telegram_id}] - "
                                f"VPN API is {system_state.vpn_api.status.value}, skipping UUID removal"
                            )
                            # PART D.7: Mark cleanup as pending, log SKIPPED
                            # UUID will be removed later when VPN API is healthy
                        else:
                            await vpn_utils.remove_vless_user(uuid)
                            logger.info(f"Deleted UUID {uuid} for user {telegram_id} during admin revoke")
                    except Exception as e:
                        # Не падаем, если UUID уже удален или произошла ошибка
                        logger.error(f"Error deleting UUID {uuid} for user {telegram_id}: {e}", exc_info=True)
                
                # 3. Очищаем подписку: устанавливаем expires_at = NOW(), очищаем outline_key_id и vpn_key
                await conn.execute(
                    "UPDATE subscriptions SET expires_at = $1, status = 'expired', uuid = NULL, vpn_key = NULL WHERE telegram_id = $2",
                    now_db, telegram_id
                )
                
                # 4. Записываем в историю подписок (используем старый vpn_key для истории, если был)
                await _log_subscription_history_atomic(conn, telegram_id, vpn_key or "", now, now, "admin_revoke")
                
                # 5. Записываем событие в audit_log
                vpn_key_preview = vpn_key[:20] + "..." if vpn_key else "N/A"
                details = f"Revoked access, Old expires_at: {old_expires_at.isoformat()}, VPN key: {vpn_key_preview}"
                await _log_audit_event_atomic(conn, "admin_revoke", admin_telegram_id, telegram_id, details)
                
                logger.info(f"Admin {admin_telegram_id} revoked access for user {telegram_id}")
                return True
                
            except Exception as e:
                logger.exception(f"Error in admin_revoke_access_atomic for user {telegram_id}, transaction rolled back")
                raise


# ==================== ФУНКЦИИ ДЛЯ РАБОТЫ С ПЕРСОНАЛЬНЫМИ СКИДКАМИ ====================

async def get_user_discount(telegram_id: int) -> Optional[Dict[str, Any]]:
    """Получить активную персональную скидку пользователя
    
    Returns:
        Словарь с данными скидки или None, если скидки нет или она истекла
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        now = datetime.now(timezone.utc)
        row = await conn.fetchrow(
            """SELECT * FROM user_discounts 
               WHERE telegram_id = $1 
               AND (expires_at IS NULL OR expires_at > $2)""",
            telegram_id, _to_db_utc(now)
        )
        return dict(row) if row else None


async def create_user_discount(telegram_id: int, discount_percent: int, expires_at: Optional[datetime], created_by: int) -> bool:
    """Создать или обновить персональную скидку пользователя
    
    Args:
        telegram_id: Telegram ID пользователя
        discount_percent: Процент скидки (10, 15, 25, и т.д.)
        expires_at: Дата истечения скидки (None для бессрочной)
        created_by: Telegram ID администратора, создавшего скидку
    
    Returns:
        True если успешно, False в случае ошибки
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            await conn.execute(
                """INSERT INTO user_discounts (telegram_id, discount_percent, expires_at, created_by)
                   VALUES ($1, $2, $3, $4)
                   ON CONFLICT (telegram_id) 
                   DO UPDATE SET discount_percent = $2, expires_at = $3, created_by = $4, created_at = CURRENT_TIMESTAMP""",
                telegram_id, discount_percent, _to_db_utc(expires_at) if expires_at else None, created_by
            )
            
            # Логируем создание/обновление скидки
            expires_str = expires_at.strftime("%d.%m.%Y %H:%M") if expires_at else "бессрочно"
            details = f"Personal discount created/updated: {discount_percent}%, expires_at: {expires_str}"
            await _log_audit_event_atomic(conn, "admin_create_discount", created_by, telegram_id, details)
            
            return True
        except Exception as e:
            logger.exception(f"Error creating user discount: {e}")
            return False


async def delete_user_discount(telegram_id: int, deleted_by: int) -> bool:
    """Удалить персональную скидку пользователя
    
    Args:
        telegram_id: Telegram ID пользователя
        deleted_by: Telegram ID администратора, удалившего скидку
    
    Returns:
        True если успешно, False в случае ошибки
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            # Проверяем, есть ли скидка
            existing = await conn.fetchrow(
                "SELECT * FROM user_discounts WHERE telegram_id = $1",
                telegram_id
            )
            
            if not existing:
                return False
            
            # Удаляем скидку
            await conn.execute(
                "DELETE FROM user_discounts WHERE telegram_id = $1",
                telegram_id
            )
            
            # Логируем удаление скидки
            discount_percent = existing["discount_percent"]
            details = f"Personal discount deleted: {discount_percent}%"
            await _log_audit_event_atomic(conn, "admin_delete_discount", deleted_by, telegram_id, details)
            
            return True
        except Exception as e:
            logger.exception(f"Error deleting user discount: {e}")
            return False


# ==================== ФУНКЦИИ ДЛЯ РАБОТЫ С VIP-СТАТУСОМ ====================

async def is_vip_user(telegram_id: int) -> bool:
    """Проверить, является ли пользователь VIP
    
    Returns:
        True если пользователь VIP, False иначе
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT telegram_id FROM vip_users WHERE telegram_id = $1",
            telegram_id
        )
        return row is not None


async def grant_vip_status(telegram_id: int, granted_by: int) -> bool:
    """Назначить VIP-статус пользователю
    
    Args:
        telegram_id: Telegram ID пользователя
        granted_by: Telegram ID администратора, назначившего VIP
    
    Returns:
        True если успешно, False в случае ошибки
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            await conn.execute(
                """INSERT INTO vip_users (telegram_id, granted_by)
                   VALUES ($1, $2)
                   ON CONFLICT (telegram_id) 
                   DO UPDATE SET granted_by = $2, granted_at = CURRENT_TIMESTAMP""",
                telegram_id, granted_by
            )
            
            # Логируем назначение VIP
            details = f"VIP status granted to user {telegram_id}"
            await _log_audit_event_atomic(conn, "vip_granted", granted_by, telegram_id, details)
            
            return True
        except Exception as e:
            logger.exception(f"Error granting VIP status: {e}")
            return False


async def revoke_vip_status(telegram_id: int, revoked_by: int) -> bool:
    """Отозвать VIP-статус у пользователя
    
    Args:
        telegram_id: Telegram ID пользователя
        revoked_by: Telegram ID администратора, отозвавшего VIP
    
    Returns:
        True если успешно, False в случае ошибки
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            # Проверяем, есть ли VIP-статус
            existing = await conn.fetchrow(
                "SELECT telegram_id FROM vip_users WHERE telegram_id = $1",
                telegram_id
            )
            
            if not existing:
                return False
            
            # Удаляем VIP-статус
            await conn.execute(
                "DELETE FROM vip_users WHERE telegram_id = $1",
                telegram_id
            )
            
            # Логируем отзыв VIP
            details = f"VIP status revoked from user {telegram_id}"
            await _log_audit_event_atomic(conn, "vip_revoked", revoked_by, telegram_id, details)
            
            return True
        except Exception as e:
            logger.exception(f"Error revoking VIP status: {e}")
            return False


# ============================================================================
# ФИНАНСОВАЯ АНАЛИТИКА
# ============================================================================

async def get_total_revenue() -> float:
    """
    Получить общий доход от всех успешных платежей
    
    Returns:
        Общий доход в рублях (только утвержденные платежи)
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Суммируем все утвержденные платежи
        total_kopecks = await conn.fetchval(
            """SELECT COALESCE(SUM(amount), 0) 
               FROM payments 
               WHERE status = 'approved'"""
        ) or 0
        
        return total_kopecks / 100.0  # Конвертируем из копеек в рубли


async def get_paying_users_count() -> int:
    """
    Получить количество платящих пользователей
    
    Returns:
        Количество уникальных пользователей с хотя бы одним утвержденным платежом
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            """SELECT COUNT(DISTINCT telegram_id) 
               FROM payments 
               WHERE status = 'approved'"""
        ) or 0
        
        return count


async def get_user_ltv(telegram_id: int) -> float:
    """
    Получить LTV (Lifetime Value) пользователя
    
    LTV = общая сумма платежей за подписки (исключая кешбэк)
    
    Args:
        telegram_id: Telegram ID пользователя
    
    Returns:
        LTV в рублях
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Суммируем все утвержденные платежи за подписки
        total_kopecks = await conn.fetchval(
            """SELECT COALESCE(SUM(amount), 0) 
               FROM payments 
               WHERE telegram_id = $1 AND status = 'approved'""",
            telegram_id
        ) or 0
        
        return total_kopecks / 100.0  # Конвертируем из копеек в рубли


async def get_average_ltv() -> float:
    """
    Получить средний LTV по всем пользователям
    
    Returns:
        Средний LTV в рублях
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Получаем LTV для каждого пользователя
        ltv_data = await conn.fetch(
            """SELECT telegram_id, COALESCE(SUM(amount), 0) as total_payments
               FROM payments
               WHERE status = 'approved'
               GROUP BY telegram_id"""
        )
        
        if not ltv_data:
            return 0.0
        
        total_ltv = sum(row["total_payments"] for row in ltv_data)
        avg_ltv = total_ltv / len(ltv_data)
        
        return avg_ltv / 100.0  # Конвертируем из копеек в рубли


async def get_arpu() -> float:
    """
    Получить ARPU (Average Revenue Per User)
    
    ARPU = общий доход / количество платящих пользователей
    
    Returns:
        ARPU в рублях
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Общий доход (только утвержденные платежи)
        total_revenue_kopecks = await conn.fetchval(
            """SELECT COALESCE(SUM(amount), 0) 
               FROM payments 
               WHERE status = 'approved'"""
        ) or 0
        
        total_revenue = total_revenue_kopecks / 100.0
        
        # Количество платящих пользователей
        paying_users_count = await conn.fetchval(
            """SELECT COUNT(DISTINCT telegram_id) 
               FROM payments 
               WHERE status = 'approved'"""
        ) or 0
        
        # ARPU = общий доход / платящие пользователи
        arpu = total_revenue / paying_users_count if paying_users_count > 0 else 0.0
        
        return arpu


async def get_ltv() -> float:
    """
    Получить средний LTV (Lifetime Value) по всем платящим пользователям
    
    LTV = средняя сумма всех платежей пользователя за подписки
    
    Returns:
        Средний LTV в рублях
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Получаем средний LTV через агрегацию (оптимизированный запрос)
        avg_ltv_kopecks = await conn.fetchval(
            """SELECT COALESCE(AVG(user_total), 0)
               FROM (
                   SELECT telegram_id, SUM(amount) as user_total
                   FROM payments
                   WHERE status = 'approved'
                   GROUP BY telegram_id
               ) as user_ltvs"""
        ) or 0
        
        # PART D.8: Fix Decimal arithmetic bug
        # avg_ltv_kopecks may be Decimal from PostgreSQL
        # Use float() conversion to avoid TypeError: unsupported operand type(s) for /: 'Decimal' and 'float'
        return float(avg_ltv_kopecks) / 100.0  # Конвертируем из копеек в рубли


async def get_referral_analytics() -> Dict[str, Any]:
    """
    Получить реферальную аналитику
    
    Returns:
        Словарь с ключами:
        - referral_revenue: доход от рефералов (сумма платежей приглашенных пользователей)
        - cashback_paid: выплаченный кешбэк
        - net_profit: чистая прибыль (referral_revenue - cashback_paid)
        - referred_users_count: количество приглашенных пользователей
        - active_referrals: количество активных рефералов
    """
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            # Доход от рефералов: сумма всех платежей пользователей, у которых есть referrer_id
            referral_revenue_kopecks = await conn.fetchval(
            """SELECT COALESCE(SUM(p.amount), 0)
               FROM payments p
               JOIN users u ON p.telegram_id = u.telegram_id
               WHERE p.status = 'approved' 
               AND (u.referrer_id IS NOT NULL OR u.referred_by IS NOT NULL)"""
        ) or 0
        
        referral_revenue = referral_revenue_kopecks / 100.0
        
        # Выплаченный кешбэк (сумма всех транзакций типа cashback)
        cashback_paid_kopecks = await conn.fetchval(
            """SELECT COALESCE(SUM(amount), 0) 
               FROM balance_transactions 
               WHERE type = 'cashback'"""
        ) or 0
        
        cashback_paid = cashback_paid_kopecks / 100.0
        
        # Чистая прибыль
        net_profit = referral_revenue - cashback_paid
        
        # Количество приглашенных пользователей
        referred_users_count = await conn.fetchval(
            "SELECT COUNT(*) FROM referrals"
        ) or 0
        
        # Количество активных рефералов (с активной подпиской)
        active_referrals = await conn.fetchval(
            """SELECT COUNT(DISTINCT r.referred_user_id)
               FROM referrals r
               JOIN subscriptions s ON r.referred_user_id = s.telegram_id
               WHERE s.expires_at > NOW()"""
        ) or 0
        
        return {
            "referral_revenue": referral_revenue,
            "cashback_paid": cashback_paid,
            "net_profit": net_profit,
            "referred_users_count": referred_users_count,
            "active_referrals": active_referrals
        }
    except (asyncpg.UndefinedTableError, asyncpg.PostgresError) as e:
        logger.warning(f"referrals or related tables missing or inaccessible — skipping referral analytics: {e}")
        return {
            "referral_revenue": 0.0,
            "cashback_paid": 0.0,
            "net_profit": 0.0,
            "referred_users_count": 0,
            "active_referrals": 0
        }
    except Exception as e:
        logger.warning(f"Error getting referral analytics: {e}")
        return {
            "referral_revenue": 0.0,
            "cashback_paid": 0.0,
            "net_profit": 0.0,
            "referred_users_count": 0,
            "active_referrals": 0
        }


async def get_daily_summary(date: Optional[datetime] = None) -> Dict[str, Any]:
    """
    Получить ежедневную сводку
    
    Args:
        date: Дата для сводки (если None, используется сегодня)
    
    Returns:
        Словарь с ключами: revenue, payments_count, new_users, new_subscriptions
    """
    if date is None:
        date = datetime.now(timezone.utc)
    
    start_date = date.replace(hour=0, minute=0, second=0, microsecond=0)
    end_date = start_date + timedelta(days=1)
    
    pool = await get_pool()
    async with pool.acquire() as conn:
        start_naive = _to_db_utc(start_date)
        end_naive = _to_db_utc(end_date)
        # Доход за день (утвержденные платежи)
        revenue_kopecks = await conn.fetchval(
            """SELECT COALESCE(SUM(amount), 0) 
               FROM payments 
               WHERE status = 'approved' 
               AND created_at >= $1 AND created_at < $2""",
            start_naive, end_naive
        ) or 0
        
        revenue = revenue_kopecks / 100.0
        
        # Количество платежей
        payments_count = await conn.fetchval(
            """SELECT COUNT(*) 
               FROM payments 
               WHERE status = 'approved' 
               AND created_at >= $1 AND created_at < $2""",
            start_naive, end_naive
        ) or 0
        
        # Новые пользователи
        new_users = await conn.fetchval(
            """SELECT COUNT(*) 
               FROM users 
               WHERE created_at >= $1 AND created_at < $2""",
            start_naive, end_naive
        ) or 0
        
        # Новые подписки
        new_subscriptions = await conn.fetchval(
            """SELECT COUNT(*) 
               FROM subscriptions 
               WHERE created_at >= $1 AND created_at < $2""",
            start_naive, end_naive
        ) or 0
        
        return {
            "date": start_date.strftime("%Y-%m-%d"),
            "revenue": revenue,
            "payments_count": payments_count,
            "new_users": new_users,
            "new_subscriptions": new_subscriptions
        }


async def get_monthly_summary(year: int, month: int) -> Dict[str, Any]:
    """
    Получить ежемесячную сводку
    
    Args:
        year: Год
        month: Месяц (1-12)
    
    Returns:
        Словарь с ключами: revenue, payments_count, new_users, new_subscriptions
    """
    start_date = datetime(year, month, 1, tzinfo=timezone.utc)
    if month == 12:
        end_date = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        end_date = datetime(year, month + 1, 1, tzinfo=timezone.utc)
    
    pool = await get_pool()
    async with pool.acquire() as conn:
        start_naive = _to_db_utc(start_date)
        end_naive = _to_db_utc(end_date)
        # Доход за месяц (утвержденные платежи)
        revenue_kopecks = await conn.fetchval(
            """SELECT COALESCE(SUM(amount), 0) 
               FROM payments 
               WHERE status = 'approved' 
               AND created_at >= $1 AND created_at < $2""",
            start_naive, end_naive
        ) or 0
        
        revenue = revenue_kopecks / 100.0
        
        # Количество платежей
        payments_count = await conn.fetchval(
            """SELECT COUNT(*) 
               FROM payments 
               WHERE status = 'approved' 
               AND created_at >= $1 AND created_at < $2""",
            start_naive, end_naive
        ) or 0
        
        # Новые пользователи
        new_users = await conn.fetchval(
            """SELECT COUNT(*) 
               FROM users 
               WHERE created_at >= $1 AND created_at < $2""",
            start_naive, end_naive
        ) or 0
        
        # Новые подписки
        new_subscriptions = await conn.fetchval(
            """SELECT COUNT(*) 
               FROM subscriptions 
               WHERE created_at >= $1 AND created_at < $2""",
            start_naive, end_naive
        ) or 0
        
        return {
            "year": year,
            "month": month,
            "revenue": revenue,
            "payments_count": payments_count,
            "new_users": new_users,
            "new_subscriptions": new_subscriptions
        }
