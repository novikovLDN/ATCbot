import asyncpg
import asyncio
import os
import sys
import hashlib
import base64
import uuid as uuid_lib
import random
import json
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, Tuple, TYPE_CHECKING, List
import logging
import config
import vpn_utils
from app.utils.retry import retry_async
from app.core.system_state import ComponentStatus
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
    if dt.tzinfo != timezone.utc:
        raise ValueError(f"Expected UTC, got tzinfo={dt.tzinfo}")
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
    if not u:
        raise RuntimeError("UUID generation failed: empty")
    if len(u) < 32:
        raise RuntimeError(f"UUID generation failed: invalid length {len(u)}")
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
    """Build asyncpg.create_pool kwargs. Single source of truth for all pool creation."""
    return {
        "min_size": int(os.getenv("DB_POOL_MIN_SIZE", "2")),
        "max_size": int(os.getenv("DB_POOL_MAX_SIZE", "25")),  # Increased from 15 to handle peak load (6 workers + 20 webhook handlers)
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
                tariff TEXT NOT NULL CHECK (tariff IN ('basic', 'plus', 'biz_starter', 'biz_team', 'biz_business', 'biz_pro', 'biz_enterprise', 'biz_ultimate')),
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

        # SECURITY: Unique constraint on purchase_id for approved/paid payments (idempotency)
        try:
            await conn.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_payments_unique_purchase_approved
                ON payments(purchase_id)
                WHERE purchase_id IS NOT NULL AND status IN ('approved', 'paid')
            """)
        except Exception:
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
            await conn.execute("ALTER TABLE payments ADD COLUMN IF NOT EXISTS paid_at TIMESTAMP")
        except Exception:
            pass
        
        # Миграция: добавляем поля для delayed activation (premium flow)
        try:
            await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS activation_status TEXT DEFAULT 'active'")
            await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS activation_attempts INTEGER DEFAULT 0")
            await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS last_activation_error TEXT")
        except Exception:
            pass

        # Миграция 032: subscription_type для VPN API tariff (basic / plus)
        try:
            await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS subscription_type TEXT DEFAULT 'basic'")
        except Exception:
            pass

        # Миграция 033: vpn_key_plus для Plus (второй vless-ключ)
        try:
            await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS vpn_key_plus TEXT")
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
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS special_offer_created_at TIMESTAMP")
        except Exception:
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

        # Таблица broadcast_discounts (скидки для кнопок уведомлений)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS broadcast_discounts (
                id SERIAL PRIMARY KEY,
                broadcast_id INTEGER NOT NULL UNIQUE REFERENCES broadcasts(id) ON DELETE CASCADE,
                discount_percent INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

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
        
        # Таблица user_traffic_discounts (промо-скидки на трафик из рассылок)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS user_traffic_discounts (
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

        # Миграция 034: расширяем CHECK constraint для бизнес-тарифов в pending_purchases
        try:
            await conn.execute("""
                ALTER TABLE pending_purchases DROP CONSTRAINT IF EXISTS pending_purchases_tariff_check
            """)
            await conn.execute("""
                ALTER TABLE pending_purchases ADD CONSTRAINT pending_purchases_tariff_check
                CHECK (tariff IN ('basic', 'plus', 'biz_starter', 'biz_team', 'biz_business', 'biz_pro', 'biz_enterprise', 'biz_ultimate'))
            """)
        except Exception:
            pass

        # Миграция 035: добавляем колонку country для бизнес-тарифов
        try:
            await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS country TEXT")
        except Exception:
            pass
        try:
            await conn.execute("ALTER TABLE pending_purchases ADD COLUMN IF NOT EXISTS country TEXT")
        except Exception:
            pass

        # Миграция 036: is_combo и is_bypass_only для комбо/bypass подписок
        try:
            await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS is_combo BOOLEAN DEFAULT FALSE")
        except Exception:
            pass
        try:
            await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS is_bypass_only BOOLEAN DEFAULT FALSE")
        except Exception:
            pass

        # Миграция 037: is_combo для pending_purchases
        try:
            await conn.execute("ALTER TABLE pending_purchases ADD COLUMN IF NOT EXISTS is_combo BOOLEAN DEFAULT FALSE")
        except Exception:
            pass

        # Миграция 038: traffic_notified_8gb и traffic_notified_5gb
        try:
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS traffic_notified_8gb BOOLEAN DEFAULT FALSE")
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS traffic_notified_5gb BOOLEAN DEFAULT FALSE")
        except Exception:
            pass

        # Таблица gift_subscriptions — подарочные подписки
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS gift_subscriptions (
                id SERIAL PRIMARY KEY,
                gift_code TEXT UNIQUE NOT NULL,
                buyer_telegram_id BIGINT NOT NULL,
                tariff TEXT NOT NULL,
                period_days INTEGER NOT NULL,
                price_kopecks INTEGER NOT NULL,
                purchase_id TEXT,
                status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'paid', 'activated', 'expired')),
                activated_by BIGINT,
                activated_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP
            )
        """)
        try:
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_gift_subscriptions_code ON gift_subscriptions(gift_code)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_gift_subscriptions_buyer ON gift_subscriptions(buyer_telegram_id)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_gift_subscriptions_status ON gift_subscriptions(status)")
        except Exception:
            pass

        # Миграция: purchase_type для gift в pending_purchases
        try:
            await conn.execute("ALTER TABLE pending_purchases ADD COLUMN IF NOT EXISTS purchase_type TEXT DEFAULT 'subscription'")
        except Exception:
            pass

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


