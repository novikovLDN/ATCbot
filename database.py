import asyncpg
import os
import sys
import hashlib
import base64
import uuid
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, Tuple, TYPE_CHECKING, List
import logging
import config
import vpn_utils
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

# Получаем DATABASE_URL из переменных окружения
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("ERROR: DATABASE_URL environment variable is not set!", file=sys.stderr)
    sys.exit(1)

# Глобальный пул соединений
_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    """Получить пул соединений, создав его при необходимости"""
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=10)
        logger.info("Database connection pool created")
    return _pool


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


async def init_db() -> bool:
    """
    Инициализация базы данных и создание таблиц
    
    Returns:
        True если инициализация успешна, False если произошла ошибка
        
    Raises:
        Любые исключения пробрасываются наверх для обработки в startup guard
    """
    global DB_READY
    pool = await get_pool()
    
    # ====================================================================================
    # VERSIONED MIGRATIONS: Применяем миграции перед созданием таблиц
    # ====================================================================================
    try:
        import migrations
        migrations_success = await migrations.run_migrations_safe(pool)
        if not migrations_success:
            logger.error("Failed to apply database migrations")
            return False
        logger.info("Database migrations applied successfully")
    except Exception as e:
        logger.exception(f"Error applying migrations: {e}")
        return False
    
    async with pool.acquire() as conn:
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
                expires_at TIMESTAMP NOT NULL
            )
        """)
        
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
        
        # Миграция: добавляем поле balance в users (хранится в копейках как INTEGER)
        try:
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS balance INTEGER NOT NULL DEFAULT 0")
        except Exception:
            pass
        
        # Trial usage tracking (без миграций - используем ALTER TABLE IF NOT EXISTS)
        try:
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS trial_used_at TIMESTAMP")
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS trial_expires_at TIMESTAMP")
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
            # Создаём индекс для быстрого поиска по UUID
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_log_uuid ON audit_log(uuid) WHERE uuid IS NOT NULL")
            # Создаём индекс для быстрого поиска по action
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_log_action ON audit_log(action)")
            # Создаём индекс для быстрого поиска по source
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_log_source ON audit_log(source) WHERE source IS NOT NULL")
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
    # Успешная инициализация
    DB_READY = True
    return True


async def _init_promo_codes(conn):
    """Инициализация промокодов в базе данных"""

    # 1. Деактивируем устаревший промокод
    await conn.execute("""
        UPDATE promo_codes
        SET is_active = FALSE
        WHERE code = 'COURIER40'
    """)

    # 2. Добавляем актуальные промокоды
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
    pool = await get_pool()
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
    pool = await get_pool()
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
    
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            try:
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
                
                logger.info(f"Increased balance by {amount} RUB ({amount_kopecks} kopecks) for user {telegram_id}, source={source}")
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
    
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            try:
                # Проверяем баланс
                current_balance = await conn.fetchval(
                    "SELECT balance FROM users WHERE telegram_id = $1", telegram_id
                )
                
                if current_balance is None:
                    logger.error(f"User {telegram_id} not found")
                    return False
                
                if current_balance < amount_kopecks:
                    logger.warning(f"Insufficient balance for user {telegram_id}: {current_balance} < {amount_kopecks}")
                    return False
                
                # Обновляем баланс
                await conn.execute(
                    "UPDATE users SET balance = balance - $1 WHERE telegram_id = $2",
                    amount_kopecks, telegram_id
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
                
                logger.info(f"Decreased balance by {amount} RUB ({amount_kopecks} kopecks) for user {telegram_id}, source={source}")
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
    
    pool = await get_pool()
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
                # Обновляем баланс
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
                # Проверяем баланс
                current_balance = await conn.fetchval(
                    "SELECT balance FROM users WHERE telegram_id = $1", telegram_id
                )
                
                if current_balance is None:
                    logger.error(f"User {telegram_id} not found")
                    return False
                
                if current_balance < amount:
                    logger.warning(f"Insufficient balance for user {telegram_id}: {current_balance} < {amount}")
                    return False
                
                # Обновляем баланс
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
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM users WHERE referral_code = $1", referral_code
        )
        return dict(row) if row else None


async def register_referral(referrer_user_id: int, referred_user_id: int) -> bool:
    """
    Зарегистрировать реферала
    
    Args:
        referrer_user_id: Telegram ID реферера
        referred_user_id: Telegram ID приглашенного пользователя
    
    Returns:
        True если регистрация успешна, False если уже зарегистрирован или ошибка
    """
    # Запрет self-referral
    if referrer_user_id == referred_user_id:
        logger.warning(f"Self-referral attempt blocked: user_id={referrer_user_id}")
        return False
    
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
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
            
            # Обновляем referrer_id у пользователя (устанавливается только один раз)
            # Также обновляем referred_by для обратной совместимости
            await conn.execute(
                """UPDATE users 
                   SET referrer_id = $1, referred_by = $1 
                   WHERE telegram_id = $2 
                   AND referrer_id IS NULL 
                   AND referred_by IS NULL""",
                referrer_user_id, referred_user_id
            )
            
            logger.info(f"Referral registered: referrer={referrer_user_id}, referred={referred_user_id}")
            return True
        except Exception as e:
            logger.exception(f"Error registering referral: referrer_id={referrer_user_id}, referred_id={referred_user_id}")
            return False


async def get_referral_stats(telegram_id: int) -> Dict[str, int]:
    """
    Получить статистику рефералов для пользователя
    
    Returns:
        Словарь с ключами: total_referred, total_rewarded
    """
    pool = await get_pool()
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
    try:
        pool = await get_pool()
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
    except Exception as e:
        logger.exception(f"Error in get_referral_cashback_percent for partner_id={partner_id}: {e}")
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
    try:
        pool = await get_pool()
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
    except Exception as e:
        logger.exception(f"Error in get_referral_level_info for partner_id={partner_id}: {e}")
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


async def process_referral_reward_cashback(referred_user_id: int, payment_amount_rubles: float) -> bool:
    """
    DEPRECATED: Используйте process_referral_reward вместо этой функции.
    Оставлена для обратной совместимости.
    """
    result = await process_referral_reward(
        buyer_id=referred_user_id,
        purchase_id=None,
        amount_rubles=payment_amount_rubles
    )
    return result.get("success", False)


async def _process_referral_reward_cashback_OLD(referred_user_id: int, payment_amount_rubles: float) -> bool:
    """
    Начислить кешбэк партнёру при КАЖДОЙ оплате приглашенного пользователя
    
    DEPRECATED: Используйте process_referral_reward вместо этой функции.
    Оставлена для обратной совместимости.
    
    Args:
        referred_user_id: Telegram ID приглашенного пользователя, который совершил оплату
        payment_amount_rubles: Сумма оплаты в рублях
    
    Returns:
        True если кешбэк начислен, False если партнёр не найден или ошибка
    """
    # Вызываем новую функцию без purchase_id (для legacy поддержки)
    result = await process_referral_reward(
        buyer_id=referred_user_id,
        purchase_id=None,
        amount_rubles=payment_amount_rubles
    )
    return result.get("success", False)


async def process_referral_reward(
    buyer_id: int,
    purchase_id: Optional[str],
    amount_rubles: float
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
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            try:
                # 1. Получаем реферера покупателя
                user = await conn.fetchrow(
                    "SELECT referrer_id FROM users WHERE telegram_id = $1",
                    buyer_id
                )
                
                if not user:
                    logger.debug(f"process_referral_reward: User {buyer_id} not found")
                    return {
                        "success": False,
                        "referrer_id": None,
                        "percent": None,
                        "reward_amount": None,
                        "message": "User not found"
                    }
                
                referrer_id = user.get("referrer_id")
                
                if not referrer_id:
                    # Пользователь не был приглашён через реферальную программу
                    logger.debug(f"process_referral_reward: User {buyer_id} has no referrer")
                    return {
                        "success": False,
                        "referrer_id": None,
                        "percent": None,
                        "reward_amount": None,
                        "message": "No referrer"
                    }
                
                # 2. ЗАЩИТА ОТ САМОРЕФЕРАЛА
                if referrer_id == buyer_id:
                    logger.warning(f"process_referral_reward: Self-referral detected: user {buyer_id}")
                    return {
                        "success": False,
                        "referrer_id": referrer_id,
                        "percent": None,
                        "reward_amount": None,
                        "message": "Self-referral detected"
                    }
                
                # 3. ЗАЩИТА ОТ ПОВТОРНОГО НАЧИСЛЕНИЯ (если purchase_id указан)
                if purchase_id:
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
                            "message": "Reward already processed for this purchase"
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
                        "message": "Invalid reward amount"
                    }
                
                # 7. Начисляем кешбэк на баланс реферера
                await conn.execute(
                    "UPDATE users SET balance = balance + $1 WHERE telegram_id = $2",
                    reward_amount_kopecks, referrer_id
                )
                
                # 8. Записываем транзакцию баланса
                await conn.execute(
                    """INSERT INTO balance_transactions (user_id, amount, type, source, description, related_user_id)
                       VALUES ($1, $2, $3, $4, $5, $6)""",
                    referrer_id, reward_amount_kopecks, "cashback", "referral",
                    f"Реферальный кешбэк {percent}% за оплату пользователя {buyer_id}",
                    buyer_id
                )
                
                # 9. Создаём запись в referral_rewards (история начислений)
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
                
            except Exception as e:
                logger.exception(f"Error processing referral reward: buyer_id={buyer_id}, purchase_id={purchase_id}: {e}")
                return {
                    "success": False,
                    "referrer_id": None,
                    "percent": None,
                    "reward_amount": None,
                    "message": f"Error: {str(e)}"
                }


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
    pool = await get_pool()
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
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            try:
                now = datetime.now()
                
                # Получаем подписку, которая истекла, но ещё не очищена
                row = await conn.fetchrow(
                    """SELECT * FROM subscriptions 
                       WHERE telegram_id = $1 
                       AND expires_at <= $2 
                       AND status = 'active'
                       AND uuid IS NOT NULL""",
                    telegram_id, now
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
                            await _log_vpn_lifecycle_audit_async(
                                action="vpn_expire",
                                telegram_id=telegram_id,
                                uuid=uuid,
                                source="auto-expiry",
                                result="success",
                                details=f"Real-time expiration check, expires_at={expires_at.isoformat()}"
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
                    telegram_id, now
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
    
    pool = await get_pool()
    async with pool.acquire() as conn:
        now = datetime.now()
        row = await conn.fetchrow(
            "SELECT * FROM subscriptions WHERE telegram_id = $1 AND status = 'active' AND expires_at > $2",
            telegram_id, now
        )
        return dict(row) if row else None


async def get_subscription_any(telegram_id: int) -> Optional[Dict[str, Any]]:
    """Получить подписку пользователя независимо от статуса (активная или истекшая)
    
    Возвращает подписку, если она существует, даже если expires_at <= now.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM subscriptions WHERE telegram_id = $1",
            telegram_id
        )
        return dict(row) if row else None


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
            "trial_used_at": row["trial_used_at"],
            "trial_expires_at": row["trial_expires_at"]
        }


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
            """, trial_expires_at, telegram_id)
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
        now = datetime.now()
        
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
            telegram_id, now
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
        now = datetime.now()
        row = await conn.fetchrow(
            """SELECT * FROM subscriptions 
               WHERE id = $1 
               AND status = 'active' 
               AND expires_at > $2""",
            subscription_id, now
        )
        return dict(row) if row else None


async def update_subscription_uuid(subscription_id: int, new_uuid: str) -> None:
    """Обновить UUID подписки
    
    Args:
        subscription_id: ID подписки
        new_uuid: Новый UUID
    
    Note:
        НЕ меняет статус
        НЕ трогает даты
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
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
        now = datetime.now()
        rows = await conn.fetch(
            """SELECT * FROM subscriptions 
               WHERE status = 'active' 
               AND expires_at > $1
               ORDER BY id ASC""",
            now
        )
        return [dict(row) for row in rows]


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
    try:
        new_uuid = await vpn_utils.reissue_vpn_access(old_uuid)
    except Exception as e:
        logger.error(
            f"reissue_subscription_key: VPN_API_FAILED [subscription_id={subscription_id}, "
            f"telegram_id={telegram_id}, error={str(e)}]"
        )
        raise
    
    # 3. Обновляем UUID в БД
    try:
        await update_subscription_uuid(subscription_id, new_uuid)
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


async def _log_audit_event_atomic(conn, action: str, telegram_id: int, target_user: Optional[int] = None, details: Optional[str] = None):
    """Записать событие аудита в таблицу audit_log
    
    Должна вызываться ТОЛЬКО внутри активной транзакции.
    
    Args:
        conn: Соединение с БД (внутри транзакции)
        action: Тип действия (например, 'payment_approved', 'payment_rejected', 'vpn_key_issued', 'subscription_renewed')
        telegram_id: Telegram ID администратора, который выполнил действие
        target_user: Telegram ID пользователя, над которым выполнено действие (опционально)
        details: Дополнительные детали действия (опционально)
    """
    await conn.execute(
        """INSERT INTO audit_log (action, telegram_id, target_user, details)
           VALUES ($1, $2, $3, $4)""",
        action, telegram_id, target_user, details
    )


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
        vpn_key: VPN-ключ
        start_date: Дата начала периода
        end_date: Дата окончания периода
        action_type: Тип действия ('purchase', 'renewal', 'reissue', 'manual_reissue')
    """
    await conn.execute(
        """INSERT INTO subscription_history (telegram_id, vpn_key, start_date, end_date, action_type)
           VALUES ($1, $2, $3, $4, $5)""",
        telegram_id, vpn_key, start_date, end_date, action_type
    )


async def _log_audit_event_atomic_standalone(action: str, telegram_id: int, target_user: Optional[int] = None, details: Optional[str] = None):
    """Записать событие аудита в таблицу audit_log (standalone версия)
    
    Создает свою транзакцию. Используется когда нужно записать событие вне существующей транзакции.
    
    Args:
        action: Тип действия (например, 'payment_approved', 'payment_rejected', 'vpn_key_issued', 'subscription_renewed')
        telegram_id: Telegram ID администратора, который выполнил действие
        target_user: Telegram ID пользователя, над которым выполнено действие (опционально)
        details: Дополнительные детали действия (опционально)
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await _log_audit_event_atomic(conn, action, telegram_id, target_user, details)


async def reissue_vpn_key_atomic(telegram_id: int, admin_telegram_id: int) -> Tuple[Optional[str], Optional[str]]:
    """Атомарно перевыпустить VPN-ключ для пользователя
    
    Перевыпуск возможен ТОЛЬКО если у пользователя есть активная подписка.
    В одной транзакции:
    - удаляет старый UUID из Xray API (POST /remove-user/{uuid})
    - создает новый UUID через Xray API (POST /add-user)
    - обновляет subscriptions (uuid, vpn_key)
    - expires_at НЕ меняется (подписка не продлевается)
    - записывает событие в audit_log
    
    Args:
        telegram_id: Telegram ID пользователя
        admin_telegram_id: Telegram ID администратора, который выполняет перевыпуск
    
    Returns:
        (new_vpn_key, old_vpn_key) или (None, None) если нет активной подписки или ошибка создания ключа
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            try:
                # 1. Проверяем, что у пользователя есть активная подписка
                # КРИТИЧНО: Проверяем status='active', а не только expires_at
                now = datetime.now()
                subscription_row = await conn.fetchrow(
                    """SELECT * FROM subscriptions 
                       WHERE telegram_id = $1 
                       AND status = 'active' 
                       AND expires_at > $2""",
                    telegram_id, now
                )
                
                if not subscription_row:
                    logger.error(f"Cannot reissue VPN key for user {telegram_id}: no active subscription")
                    return None, None
                
                subscription = dict(subscription_row)
                old_uuid = subscription.get("uuid")
                old_vpn_key = subscription.get("vpn_key", "")
                expires_at = subscription["expires_at"]
                
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
                
                # 3. Создаем новый UUID через Xray API (POST /add-user)
                try:
                    vless_result = await vpn_utils.add_vless_user()
                    new_uuid = vless_result["uuid"]
                    new_vpn_key = vless_result["vless_url"]
                    
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
                logger.info(
                    f"VPN key reissued [action=admin_reissue, user={telegram_id}, admin={admin_telegram_id}, "
                    f"new_uuid={new_uuid_preview}, expires_at={expires_at.isoformat()}]"
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
        {
            "uuid": str,
            "vless_url": Optional[str],  # только если новый UUID
            "subscription_end": datetime
        }
        
        Или None при ошибке
    
    Raises:
        Exception: При любых ошибках (не возвращает None, выбрасывает исключение)
    """
    if conn is None:
        pool = await get_pool()
        conn = await pool.acquire()
        should_release_conn = True
    else:
        should_release_conn = False
    
    try:
        now = datetime.now()
        
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
            expires_at = subscription.get("expires_at")
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
                subscription_start = subscription.get("activated_at") or subscription.get("expires_at") or now
                
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
                
                # КРИТИЧЕСКИ ВАЖНО: Обновляем БД БЕЗ вызова VPN API
                # UUID НЕ МЕНЯЕТСЯ - VPN соединение продолжает работать без перерыва
                try:
                    await conn.execute(
                        """UPDATE subscriptions 
                           SET expires_at = $1, 
                               status = 'active',
                               reminder_sent = FALSE,
                               reminder_3d_sent = FALSE,
                               reminder_24h_sent = FALSE,
                               reminder_3h_sent = FALSE,
                               reminder_6h_sent = FALSE
                           WHERE telegram_id = $2""",
                        subscription_end, telegram_id
                    )
                    
                    # ВАЛИДАЦИЯ: Проверяем что запись обновлена
                    updated_subscription = await conn.fetchrow(
                        "SELECT expires_at, status, uuid FROM subscriptions WHERE telegram_id = $1",
                        telegram_id
                    )
                    if not updated_subscription or updated_subscription["expires_at"] != subscription_end:
                        error_msg = f"Failed to verify subscription renewal for user {telegram_id}"
                        logger.error(f"grant_access: ERROR_RENEWAL_VERIFICATION [user={telegram_id}, error={error_msg}]")
                        raise Exception(error_msg)
                    
                    logger.info(
                        f"grant_access: RENEWAL_SAVED_SUCCESS [user={telegram_id}, "
                        f"subscription_end={updated_subscription['expires_at'].isoformat()}, "
                        f"status={updated_subscription['status']}, uuid={uuid[:8]}...]"
                    )
                except Exception as e:
                    logger.error(f"grant_access: RENEWAL_SAVE_FAILED [user={telegram_id}, error={str(e)}]")
                    raise Exception(f"Failed to renew subscription in database: {e}") from e
                
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
            error_msg = (
                f"Cannot create VPN access for user {telegram_id}: VPN API is not configured. "
                "Please set XRAY_API_URL and XRAY_API_KEY environment variables."
            )
            logger.error(error_msg)
            raise Exception(error_msg)
        
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
        
        # Создаем новый UUID через Xray API с retry логикой
        logger.info(f"grant_access: CALLING_VPN_API [action=add_user, user={telegram_id}, source={source}]")
        
        # КРИТИЧНО: Retry логика для VPN API (2 попытки = 3 всего с задержкой)
        import asyncio
        MAX_VPN_RETRIES = 2
        RETRY_DELAY_SECONDS = 1.0
        
        last_exception = None
        vless_result = None
        new_uuid = None
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
                vless_result = await vpn_utils.add_vless_user()
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
                    f"grant_access: VPN_API_SUCCESS [action=add_user, user={telegram_id}, uuid={uuid_preview}, "
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
        
        # Проверяем что UUID и VLESS URL получены после retry
        if not new_uuid or not vless_url:
            error_msg = f"VPN API failed to return UUID/vless_url after retries for user {telegram_id}"
            logger.error(f"grant_access: CRITICAL_VPN_API_FAILURE [user={telegram_id}, error={error_msg}]")
            raise Exception(error_msg)
        
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
        
        # ВАЛИДАЦИЯ: Запрещено выдавать ключ без записи в БД
        logger.info(
            f"grant_access: SAVING_TO_DB [user={telegram_id}, uuid={uuid_preview}, "
            f"subscription_start={subscription_start.isoformat()}, subscription_end={subscription_end.isoformat()}, "
            f"status=active, source={source}]"
        )
        
        # Сохраняем/обновляем подписку
        try:
            await conn.execute(
                """INSERT INTO subscriptions (
                       telegram_id, uuid, vpn_key, expires_at, status, source,
                       reminder_sent, reminder_3d_sent, reminder_24h_sent,
                       reminder_3h_sent, reminder_6h_sent, admin_grant_days,
                       activated_at, last_bytes,
                       trial_notif_6h_sent, trial_notif_18h_sent, trial_notif_30h_sent,
                       trial_notif_42h_sent, trial_notif_54h_sent, trial_notif_60h_sent,
                       trial_notif_71h_sent
                   )
                   VALUES ($1, $2, $3, $4, 'active', $5, FALSE, FALSE, FALSE, FALSE, FALSE, $6, $7, 0,
                           FALSE, FALSE, FALSE, FALSE, FALSE, FALSE, FALSE)
                   ON CONFLICT (telegram_id) 
                   DO UPDATE SET 
                       uuid = $2,
                       vpn_key = $3,
                       expires_at = $4,
                       status = 'active',
                       source = $5,
                       reminder_sent = FALSE,
                       reminder_3d_sent = FALSE,
                       reminder_24h_sent = FALSE,
                       reminder_3h_sent = FALSE,
                       reminder_6h_sent = FALSE,
                       admin_grant_days = $6,
                       activated_at = $7,
                       last_bytes = 0,
                       trial_notif_6h_sent = FALSE,
                       trial_notif_18h_sent = FALSE,
                       trial_notif_30h_sent = FALSE,
                       trial_notif_42h_sent = FALSE,
                       trial_notif_54h_sent = FALSE,
                       trial_notif_60h_sent = FALSE,
                       trial_notif_71h_sent = FALSE""",
                telegram_id, new_uuid, vless_url, subscription_end, source, admin_grant_days, subscription_start
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
            f"subscription_start={subscription_start.isoformat()}, subscription_end={subscription_end.isoformat()}, "
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
                now = datetime.now()
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
                if not is_renewal:
                    # Проверяем, есть ли у пользователя реферер
                    user_row = await conn.fetchrow(
                        "SELECT referrer_id, referred_by FROM users WHERE telegram_id = $1", telegram_id
                    )
                    if user_row:
                        # Используем referrer_id, если есть, иначе referred_by (для обратной совместимости)
                        referrer_id = user_row.get("referrer_id") or user_row.get("referred_by")
                        
                        if referrer_id:
                            # ЗАЩИТА ОТ РЕФЕРАЛЬНОГО ФРОДА: invited_user_id НЕ должен быть равен referrer_id
                            if referrer_id == telegram_id:
                                logger.warning(
                                    f"REFERRAL FRAUD: Cashback blocked - invited_user_id ({telegram_id}) == referrer_id ({referrer_id}). "
                                    f"Payment ID: {payment_id}, Amount: {payment.get('amount', 0) / 100.0:.2f} RUB"
                                )
                            else:
                                # Проверяем, что кешбэк еще не был начислен за этого пользователя
                                referral_row = await conn.fetchrow(
                                    "SELECT is_rewarded FROM referrals WHERE referrer_user_id = $1 AND referred_user_id = $2",
                                    referrer_id, telegram_id
                                )
                                
                                # Начисляем кешбэк только если он еще не был начислен (is_rewarded = FALSE)
                                if referral_row and not referral_row.get("is_rewarded"):
                                    try:
                                        # Получаем сумму платежа в рублях
                                        payment_amount_rubles = payment.get("amount", 0) / 100.0  # Конвертируем из копеек
                                        
                                        if payment_amount_rubles > 0:
                                            # Получаем процент кешбэка на основе количества приглашённых
                                            referrals_count = await conn.fetchval(
                                                "SELECT COUNT(*) FROM referrals WHERE referrer_user_id = $1",
                                                referrer_id
                                            ) or 0
                                            
                                            # Определяем процент кешбэка (прогрессивная шкала)
                                            if referrals_count >= 50:
                                                cashback_percent = 45
                                            elif referrals_count >= 25:
                                                cashback_percent = 25
                                            else:
                                                cashback_percent = 10
                                            
                                            # Рассчитываем кешбэк (в копейках)
                                            cashback_rubles = payment_amount_rubles * (cashback_percent / 100.0)
                                            cashback_kopecks = int(cashback_rubles * 100)
                                            
                                            if cashback_kopecks > 0:
                                                # Начисляем кешбэк на баланс реферера
                                                await conn.execute(
                                                    "UPDATE users SET balance = balance + $1 WHERE telegram_id = $2",
                                                    cashback_kopecks, referrer_id
                                                )
                                                
                                                # Записываем транзакцию баланса
                                                await conn.execute(
                                                    """INSERT INTO balance_transactions (user_id, amount, type, source, description, related_user_id)
                                                       VALUES ($1, $2, $3, $4, $5, $6)""",
                                                    referrer_id, cashback_kopecks, "cashback", "referral",
                                                    f"Реферальный кешбэк {cashback_percent}% за оплату пользователя {telegram_id}",
                                                    telegram_id
                                                )
                                                
                                                # Помечаем кешбэк как начисленный (только один раз за пользователя)
                                                await conn.execute(
                                                    "UPDATE referrals SET is_rewarded = TRUE, reward_amount = $1 WHERE referrer_user_id = $2 AND referred_user_id = $3",
                                                    cashback_kopecks, referrer_id, telegram_id
                                                )
                                                
                                                # Получаем обновлённый баланс реферера для уведомления
                                                referrer_balance_row = await conn.fetchrow(
                                                    "SELECT balance FROM users WHERE telegram_id = $1", referrer_id
                                                )
                                                referrer_balance = referrer_balance_row["balance"] / 100.0 if referrer_balance_row else 0.0
                                                
                                                # Логируем событие
                                                details = f"Referral cashback awarded: referrer={referrer_id} ({cashback_percent}%), referred={telegram_id}, payment={payment_amount_rubles:.2f} RUB, cashback={cashback_rubles:.2f} RUB ({cashback_kopecks} kopecks)"
                                                await _log_audit_event_atomic(
                                                    conn,
                                                    "referral_cashback",
                                                    referrer_id,
                                                    telegram_id,
                                                    details
                                                )
                                                
                                                logger.info(f"Referral cashback awarded: referrer_id={referrer_id}, referred_id={telegram_id}, percent={cashback_percent}%, amount={cashback_rubles:.2f} RUB")
                                                
                                                # Отправляем уведомление рефереру о начислении кешбэка (вне транзакции)
                                                if bot:
                                                    try:
                                                        notification_text = (
                                                            f"🔥 Вам начислен кешбэк!\n"
                                                            f"Ваш друг оформил подписку.\n"
                                                            f"💰 Начислено: {cashback_rubles:.2f} ₽\n"
                                                            f"Баланс: {referrer_balance:.2f} ₽"
                                                        )
                                                        await bot.send_message(
                                                            chat_id=referrer_id,
                                                            text=notification_text
                                                        )
                                                        logger.info(f"Referral cashback notification sent to referrer_id={referrer_id}, cashback={cashback_rubles:.2f} RUB")
                                                    except Exception as e:
                                                        # Не блокируем транзакцию при ошибке отправки уведомления
                                                        logger.warning(f"Failed to send referral cashback notification to referrer_id={referrer_id}: {e}")
                                            else:
                                                logger.warning(f"Invalid cashback amount: {cashback_kopecks} kopecks for payment {payment_amount_rubles} RUB")
                                    except Exception as e:
                                        logger.exception(f"Error processing referral cashback for referred_id={telegram_id}")
                                else:
                                    logger.debug(f"Referral cashback already awarded for referrer_id={referrer_id}, referred_id={telegram_id}")
                
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
    now = datetime.now()
    reminder_date = now + timedelta(days=3)
    
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT * FROM subscriptions 
               WHERE expires_at > $1 
               AND expires_at <= $2
               AND reminder_sent = FALSE
               ORDER BY expires_at ASC""",
            now, reminder_date
        )
        return [dict(row) for row in rows]


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
            f"UPDATE subscriptions SET {flag_name} = TRUE WHERE telegram_id = $1",
            telegram_id
        )


async def get_promo_code(code: str) -> Optional[Dict[str, Any]]:
    """Получить промокод по коду"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM promo_codes WHERE UPPER(code) = UPPER($1)", code
        )
        return dict(row) if row else None


async def check_promo_code_valid(code: str) -> Optional[Dict[str, Any]]:
    """Проверить, валиден ли промокод и вернуть его данные"""
    promo = await get_promo_code(code)
    if not promo:
        return None
    
    # Проверяем, активен ли промокод
    if not promo.get("is_active", False):
        return None
    
    # Проверяем лимит использований (если задан)
    max_uses = promo.get("max_uses")
    if max_uses is not None:
        used_count = promo.get("used_count", 0)
        if used_count >= max_uses:
            return None
    
    return promo


async def increment_promo_code_use(code: str):
    """Увеличить счетчик использований промокода"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Получаем текущее значение used_count и max_uses
            row = await conn.fetchrow(
                "SELECT used_count, max_uses FROM promo_codes WHERE UPPER(code) = UPPER($1) FOR UPDATE",
                code
            )
            if not row:
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
    """Получить статистику по всем промокодам"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT 
                code,
                discount_percent,
                max_uses,
                used_count,
                is_active
            FROM promo_codes
            ORDER BY code
        """)
        return [dict(row) for row in rows]


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
    
    Returns список подписок с информацией о типе (админ-доступ или оплаченный тариф)
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        now = datetime.now()
        rows = await conn.fetch(
            """SELECT s.*, 
                      (SELECT action_type FROM subscription_history 
                       WHERE telegram_id = s.telegram_id 
                       ORDER BY created_at DESC LIMIT 1) as last_action_type
               FROM subscriptions s
               WHERE s.expires_at > $1
               ORDER BY s.expires_at ASC""",
            now
        )
        return [dict(row) for row in rows]


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
        now = datetime.now()
        
        # Всего пользователей
        total_users = await conn.fetchval("SELECT COUNT(*) FROM users")
        
        # Активных подписок (expires_at > now)
        active_subscriptions = await conn.fetchval(
            "SELECT COUNT(*) FROM subscriptions WHERE expires_at > $1",
            now
        )
        
        # Истёкших подписок (expires_at <= now)
        expired_subscriptions = await conn.fetchval(
            "SELECT COUNT(*) FROM subscriptions WHERE expires_at <= $1",
            now
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
    pool = await get_pool()
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
        
        rows = await conn.fetch(full_query, *params)
        
        # Обрабатываем результаты с безопасной обработкой NULL
        result = []
        for row in rows:
            try:
                referrer_id = row["referrer_id"]
                if referrer_id is None:
                    continue  # Пропускаем строки без referrer_id
                
                # Безопасное извлечение значений с обработкой NULL
                invited_count = safe_int(row.get("invited_count"))
                paid_count = safe_int(row.get("paid_count"))
                
                # Вычисляем процент конверсии (защита от деления на 0)
                conversion_percent = (paid_count / invited_count * 100) if invited_count > 0 else 0.0
                
                # Конвертируем из копеек в рубли с безопасной обработкой NULL
                total_invited_revenue_kopecks = safe_int(row.get("total_invited_revenue_kopecks"))
                total_cashback_paid_kopecks = safe_int(row.get("total_cashback_paid_kopecks"))
                total_invited_revenue = total_invited_revenue_kopecks / 100.0
                total_cashback_paid = total_cashback_paid_kopecks / 100.0
                
                # Определяем текущий процент кешбэка (безопасно)
                try:
                    current_cashback_percent = await get_referral_cashback_percent(referrer_id)
                except Exception as e:
                    logger.warning(f"Error getting cashback percent for referrer_id={referrer_id}: {e}")
                    current_cashback_percent = 10  # Значение по умолчанию
                
                result.append({
                    "referrer_id": referrer_id,
                    "username": row.get("username") or f"ID{referrer_id}",
                    "invited_count": invited_count,
                    "paid_count": paid_count,
                    "conversion_percent": round(conversion_percent, 2),
                    "total_invited_revenue": round(total_invited_revenue, 2),
                    "total_cashback_paid": round(total_cashback_paid, 2),
                    "current_cashback_percent": current_cashback_percent,
                    "first_referral_date": row.get("first_referral_date")
                })
            except Exception as e:
                logger.exception(f"Error processing row in get_admin_referral_stats: {e}, row={dict(row)}")
                continue  # Пропускаем проблемные строки, но продолжаем обработку
        
        return result


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
    pool = await get_pool()
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
    pool = await get_pool()
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
        purchase_id = f"purchase_{uuid.uuid4().hex[:16]}"
        
        # Срок действия контекста покупки (30 минут)
        expires_at = datetime.now() + timedelta(minutes=30)
        
        # Создаем запись о покупке
        await conn.execute(
            """INSERT INTO pending_purchases (purchase_id, telegram_id, tariff, period_days, price_kopecks, promo_code, status, expires_at)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8)""",
            purchase_id, telegram_id, tariff, period_days, price_kopecks, promo_code, "pending", expires_at
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
    pool = await get_pool()
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
            
            if status != "pending":
                error_msg = f"Pending purchase already processed: purchase_id={purchase_id}, status={status}"
                logger.warning(f"finalize_purchase: payment_rejected: reason=already_processed, {error_msg}")
                raise ValueError(error_msg)
            
            tariff_type = pending_purchase["tariff"]
            period_days = pending_purchase["period_days"]
            price_kopecks = pending_purchase["price_kopecks"]
            expected_amount_rubles = price_kopecks / 100.0
            
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
                f"tariff={tariff_type}, period_days={period_days}]"
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
            
            # STEP 4: Создаем payment record
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
            
            # STEP 5: Активируем подписку через grant_access
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
            
            # Получаем VPN ключ
            vpn_key = grant_result.get("vless_url")
            is_renewal = grant_result.get("action") == "renewal"
            
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
            
            # STEP 7: Обрабатываем реферальный кешбэк
            referral_reward_result = None
            try:
                referral_reward_result = await process_referral_reward(
                    buyer_id=telegram_id,
                    purchase_id=purchase_id,
                    amount_rubles=amount_rubles
                )
                logger.info(f"finalize_purchase: referral_reward_processed: purchase_id={purchase_id}, user={telegram_id}, success={referral_reward_result.get('success', False)}")
            except Exception as e:
                # Реферальный кешбэк не критичен - логируем и продолжаем
                logger.warning(f"finalize_purchase: referral reward failed: purchase_id={purchase_id}, error={e}")
            
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
    pool = await get_pool()
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
        now = datetime.now()
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
    pool = await get_pool()
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
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT * FROM audit_log 
               ORDER BY created_at DESC 
               LIMIT $1""",
            limit
        )
        return [dict(row) for row in rows]


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
            now = datetime.now()
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
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT is_active, incident_text FROM incident_settings ORDER BY id LIMIT 1"
        )
        if row:
            return {"is_active": row["is_active"], "incident_text": row["incident_text"]}
        return {"is_active": False, "incident_text": None}


async def set_incident_mode(is_active: bool, incident_text: Optional[str] = None):
    """Установить режим инцидента
    
    Args:
        is_active: Активен ли режим инцидента
        incident_text: Текст инцидента (опционально)
    """
    pool = await get_pool()
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
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT is_active, incident_text FROM incident_settings ORDER BY id LIMIT 1"
        )
        if row:
            return {"is_active": row["is_active"], "incident_text": row["incident_text"]}
        return {"is_active": False, "incident_text": None}


async def set_incident_mode(is_active: bool, incident_text: Optional[str] = None):
    """Установить режим инцидента
    
    Args:
        is_active: Активен ли режим инцидента
        incident_text: Текст инцидента (опционально)
    """
    pool = await get_pool()
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


async def admin_grant_access_atomic(telegram_id: int, days: int, admin_telegram_id: int) -> Tuple[Optional[datetime], Optional[str]]:
    """Атомарно выдать доступ пользователю на N дней (админ)
    
    Использует единую функцию grant_access (защищена от двойного создания ключей).
    
    Args:
        telegram_id: Telegram ID пользователя
        days: Количество дней доступа (1, 7 или 14)
        admin_telegram_id: Telegram ID администратора
    
    Returns:
        (expires_at, vpn_key) или (None, None) при ошибке или отсутствии ключей
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


async def admin_grant_access_minutes_atomic(telegram_id: int, minutes: int, admin_telegram_id: int) -> Tuple[Optional[datetime], Optional[str]]:
    """Атомарно выдать доступ пользователю на N минут (админ)
    
    Использует единую функцию grant_access (защищена от двойного создания ключей).
    
    Args:
        telegram_id: Telegram ID пользователя
        minutes: Количество минут доступа (например, 10)
        admin_telegram_id: Telegram ID администратора
    
    Returns:
        (expires_at, vpn_key) или (None, None) при ошибке или отсутствии ключей
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
                now = datetime.now()
                
                # 1. Проверяем, есть ли активная подписка
                subscription_row = await conn.fetchrow(
                    "SELECT * FROM subscriptions WHERE telegram_id = $1 AND expires_at > $2",
                    telegram_id, now
                )
                
                if not subscription_row:
                    logger.info(f"No active subscription to revoke for user {telegram_id}")
                    return False
                
                subscription = dict(subscription_row)
                old_expires_at = subscription["expires_at"]
                uuid = subscription.get("uuid")
                vpn_key = subscription.get("vpn_key", "")
                
                # 2. Удаляем UUID из Xray API (если есть)
                if uuid:
                    try:
                        await vpn_utils.remove_vless_user(uuid)
                        logger.info(f"Deleted UUID {uuid} for user {telegram_id} during admin revoke")
                    except Exception as e:
                        # Не падаем, если UUID уже удален или произошла ошибка
                        logger.error(f"Error deleting UUID {uuid} for user {telegram_id}: {e}", exc_info=True)
                
                # 3. Очищаем подписку: устанавливаем expires_at = NOW(), очищаем outline_key_id и vpn_key
                await conn.execute(
                    "UPDATE subscriptions SET expires_at = $1, status = 'expired', uuid = NULL, vpn_key = NULL WHERE telegram_id = $2",
                    now, telegram_id
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
        now = datetime.now()
        row = await conn.fetchrow(
            """SELECT * FROM user_discounts 
               WHERE telegram_id = $1 
               AND (expires_at IS NULL OR expires_at > $2)""",
            telegram_id, now
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
                telegram_id, discount_percent, expires_at, created_by
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
        
        return avg_ltv_kopecks / 100.0  # Конвертируем из копеек в рубли


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


async def get_daily_summary(date: Optional[datetime] = None) -> Dict[str, Any]:
    """
    Получить ежедневную сводку
    
    Args:
        date: Дата для сводки (если None, используется сегодня)
    
    Returns:
        Словарь с ключами: revenue, payments_count, new_users, new_subscriptions
    """
    if date is None:
        date = datetime.now()
    
    start_date = date.replace(hour=0, minute=0, second=0, microsecond=0)
    end_date = start_date + timedelta(days=1)
    
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Доход за день (утвержденные платежи)
        revenue_kopecks = await conn.fetchval(
            """SELECT COALESCE(SUM(amount), 0) 
               FROM payments 
               WHERE status = 'approved' 
               AND created_at >= $1 AND created_at < $2""",
            start_date, end_date
        ) or 0
        
        revenue = revenue_kopecks / 100.0
        
        # Количество платежей
        payments_count = await conn.fetchval(
            """SELECT COUNT(*) 
               FROM payments 
               WHERE status = 'approved' 
               AND created_at >= $1 AND created_at < $2""",
            start_date, end_date
        ) or 0
        
        # Новые пользователи
        new_users = await conn.fetchval(
            """SELECT COUNT(*) 
               FROM users 
               WHERE created_at >= $1 AND created_at < $2""",
            start_date, end_date
        ) or 0
        
        # Новые подписки
        new_subscriptions = await conn.fetchval(
            """SELECT COUNT(*) 
               FROM subscriptions 
               WHERE created_at >= $1 AND created_at < $2""",
            start_date, end_date
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
    start_date = datetime(year, month, 1)
    if month == 12:
        end_date = datetime(year + 1, 1, 1)
    else:
        end_date = datetime(year, month + 1, 1)
    
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Доход за месяц (утвержденные платежи)
        revenue_kopecks = await conn.fetchval(
            """SELECT COALESCE(SUM(amount), 0) 
               FROM payments 
               WHERE status = 'approved' 
               AND created_at >= $1 AND created_at < $2""",
            start_date, end_date
        ) or 0
        
        revenue = revenue_kopecks / 100.0
        
        # Количество платежей
        payments_count = await conn.fetchval(
            """SELECT COUNT(*) 
               FROM payments 
               WHERE status = 'approved' 
               AND created_at >= $1 AND created_at < $2""",
            start_date, end_date
        ) or 0
        
        # Новые пользователи
        new_users = await conn.fetchval(
            """SELECT COUNT(*) 
               FROM users 
               WHERE created_at >= $1 AND created_at < $2""",
            start_date, end_date
        ) or 0
        
        # Новые подписки
        new_subscriptions = await conn.fetchval(
            """SELECT COUNT(*) 
               FROM subscriptions 
               WHERE created_at >= $1 AND created_at < $2""",
            start_date, end_date
        ) or 0
        
        return {
            "year": year,
            "month": month,
            "revenue": revenue,
            "payments_count": payments_count,
            "new_users": new_users,
            "new_subscriptions": new_subscriptions
        }
