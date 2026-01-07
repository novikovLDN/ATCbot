import asyncpg
import os
import sys
import hashlib
import base64
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, Tuple
import logging
import config
import outline_api

logger = logging.getLogger(__name__)

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
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        logger.info("Database connection pool closed")


async def init_db():
    """Инициализация базы данных и создание таблиц"""
    pool = await get_pool()
    
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
        
        # Таблица payments
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT NOT NULL,
                tariff TEXT NOT NULL,
                amount INTEGER,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
        except Exception:
            # Колонки уже существуют
            pass
        
        # Миграция: добавляем поле balance в users (хранится в копейках как INTEGER)
        try:
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS balance INTEGER NOT NULL DEFAULT 0")
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
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS referred_by BIGINT")
            # Создаем индекс для быстрого поиска по referral_code
            await conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_referral_code ON users(referral_code) WHERE referral_code IS NOT NULL")
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
            ('ELVIRA064', 50, 50, TRUE),
            ('YAbx30', 30, NULL, TRUE),
            ('FAM50', 50, 50, TRUE),
            ('COURIER30', 30, 40, TRUE)
        ON CONFLICT (code) DO NOTHING
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
            
            # Обновляем referred_by у пользователя
            await conn.execute(
                "UPDATE users SET referred_by = $1 WHERE telegram_id = $2 AND referred_by IS NULL",
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
    Определить процент кешбэка на основе количества приглашённых рефералов
    
    Прогрессивная шкала:
    - 0-24 приглашённых → 10%
    - 25-49 приглашённых → 25%
    - 50+ приглашённых → 45%
    
    Args:
        partner_id: Telegram ID партнёра
    
    Returns:
        Процент кешбэка (10, 25 или 45)
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Считаем количество приглашённых пользователей
        total_referred = await conn.fetchval(
            "SELECT COUNT(*) FROM referrals WHERE referrer_user_id = $1",
            partner_id
        ) or 0
        
        # Определяем процент по прогрессивной шкале
        if total_referred >= 50:
            return 45
        elif total_referred >= 25:
            return 25
        else:
            return 10


async def get_total_cashback_earned(partner_id: int) -> float:
    """
    Получить общую сумму заработанного кешбэка партнёром
    
    Args:
        partner_id: Telegram ID партнёра
    
    Returns:
        Сумма кешбэка в рублях
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Суммируем все транзакции типа 'referral_reward' для партнёра
        total_kopecks = await conn.fetchval(
            """SELECT COALESCE(SUM(amount), 0) 
               FROM balance_transactions 
               WHERE user_id = $1 AND type = 'referral_reward'""",
            partner_id
        ) or 0
        
        return total_kopecks / 100.0  # Конвертируем из копеек в рубли


async def process_referral_reward_cashback(referred_user_id: int, payment_amount_rubles: float) -> bool:
    """
    Начислить кешбэк партнёру при КАЖДОЙ оплате приглашенного пользователя
    
    Args:
        referred_user_id: Telegram ID приглашенного пользователя, который совершил оплату
        payment_amount_rubles: Сумма оплаты в рублях
    
    Returns:
        True если кешбэк начислен, False если партнёр не найден или ошибка
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            try:
                # Получаем партнёра (реферера) для этого пользователя
                user = await conn.fetchrow(
                    "SELECT referred_by FROM users WHERE telegram_id = $1", referred_user_id
                )
                
                if not user or not user.get("referred_by"):
                    # Пользователь не был приглашён через реферальную программу
                    logger.debug(f"User {referred_user_id} has no referrer")
                    return False
                
                partner_id = user["referred_by"]
                
                # Проверяем, что партнёр не приглашает сам себя
                if partner_id == referred_user_id:
                    logger.warning(f"Self-referral detected: user {referred_user_id}")
                    return False
                
                # Определяем процент кешбэка на основе текущего количества рефералов
                cashback_percent = await get_referral_cashback_percent(partner_id)
                
                # Рассчитываем кешбэк (в копейках)
                cashback_rubles = payment_amount_rubles * (cashback_percent / 100.0)
                cashback_kopecks = int(cashback_rubles * 100)
                
                if cashback_kopecks <= 0:
                    logger.warning(f"Invalid cashback amount: {cashback_kopecks} kopecks for payment {payment_amount_rubles} RUB")
                    return False
                
                # Начисляем кешбэк на баланс партнёра
                await conn.execute(
                    "UPDATE users SET balance = balance + $1 WHERE telegram_id = $2",
                    cashback_kopecks, partner_id
                )
                
                # Записываем транзакцию баланса с указанием связанного пользователя
                await conn.execute(
                    """INSERT INTO balance_transactions (user_id, amount, type, source, description, related_user_id)
                       VALUES ($1, $2, $3, $4, $5, $6)""",
                    partner_id, cashback_kopecks, "referral_reward", "referral",
                    f"Реферальный кешбэк {cashback_percent}% за оплату пользователя {referred_user_id}",
                    referred_user_id
                )
                
                # Логируем событие
                details = f"Referral cashback awarded: partner={partner_id} ({cashback_percent}%), referred={referred_user_id}, payment={payment_amount_rubles:.2f} RUB, cashback={cashback_rubles:.2f} RUB ({cashback_kopecks} kopecks)"
                await _log_audit_event_atomic(
                    conn,
                    "referral_cashback",
                    partner_id,
                    referred_user_id,
                    details
                )
                
                logger.info(f"Referral cashback awarded: partner={partner_id}, referred={referred_user_id}, percent={cashback_percent}%, amount={cashback_rubles:.2f} RUB")
                return True
                
            except Exception as e:
                logger.exception(f"Error processing referral cashback: referred_user_id={referred_user_id}")
                return False


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
                       AND outline_key_id IS NOT NULL""",
                    telegram_id, now
                )
                
                if not row:
                    return False  # Подписка активна или отсутствует
                
                subscription = dict(row)
                outline_key_id = subscription.get("outline_key_id")
                
                if outline_key_id:
                    # Удаляем ключ из Outline API
                    try:
                        success = await outline_api.delete_outline_key(outline_key_id)
                        if success:
                            logger.info(f"Instantly disabled expired subscription for user {telegram_id}, deleted outline_key_id={outline_key_id}")
                        else:
                            logger.warning(f"Failed to delete Outline key {outline_key_id} for expired subscription, user {telegram_id}")
                    except Exception as e:
                        logger.error(f"Error deleting Outline key {outline_key_id} for expired subscription, user {telegram_id}: {e}")
                
                # Очищаем данные в БД
                await conn.execute(
                    """UPDATE subscriptions 
                       SET outline_key_id = NULL, vpn_key = NULL 
                       WHERE telegram_id = $1 AND expires_at <= $2""",
                    telegram_id, now
                )
                
                return True
                
            except Exception as e:
                logger.exception(f"Error in check_and_disable_expired_subscription for user {telegram_id}")
                return False


async def get_subscription(telegram_id: int) -> Optional[Dict[str, Any]]:
    """Получить активную подписку пользователя
    
    Активной считается подписка, у которой expires_at > текущего времени.
    
    Перед возвратом проверяет и отключает истёкшие подписки.
    """
    # Сначала проверяем и отключаем истёкшие подписки
    await check_and_disable_expired_subscription(telegram_id)
    
    pool = await get_pool()
    async with pool.acquire() as conn:
        now = datetime.now()
        row = await conn.fetchrow(
            "SELECT * FROM subscriptions WHERE telegram_id = $1 AND expires_at > $2",
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


async def create_subscription(telegram_id: int, vpn_key: str, months: int) -> Tuple[datetime, bool]:
    """Создать или продлить подписку для пользователя
    
    Если подписка уже существует, продлевает её срок действия.
    Формула продления: expires_at = max(current_expires_at, now) + tariff_duration
    """
    now = datetime.now()
    tariff_duration = timedelta(days=months * 30)
    
    # Получаем текущую подписку
    current_subscription = await get_subscription(telegram_id)
    
    if current_subscription:
        # Продление: берем максимальное значение между текущим expires_at и now
        current_expires_at = current_subscription["expires_at"]
        base_date = max(current_expires_at, now)
        expires_at = base_date + tariff_duration
        is_renewal = True
    else:
        # Новая подписка
        expires_at = now + tariff_duration
        is_renewal = False
    
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO subscriptions (telegram_id, vpn_key, expires_at, reminder_sent, reminder_3d_sent, reminder_24h_sent, reminder_3h_sent, reminder_6h_sent, admin_grant_days)
               VALUES ($1, $2, $3, FALSE, FALSE, FALSE, FALSE, FALSE, NULL)
               ON CONFLICT (telegram_id) 
               DO UPDATE SET vpn_key = $2, expires_at = $3, reminder_sent = FALSE, reminder_3d_sent = FALSE, reminder_24h_sent = FALSE, reminder_3h_sent = FALSE, reminder_6h_sent = FALSE, admin_grant_days = NULL""",
            telegram_id, vpn_key, expires_at
        )
        return expires_at, is_renewal


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
    - удаляет старый ключ из Outline
    - создает новый ключ через Outline API
    - обновляет subscriptions (outline_key_id, vpn_key)
    - expires_at НЕ меняется
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
                now = datetime.now()
                subscription_row = await conn.fetchrow(
                    "SELECT * FROM subscriptions WHERE telegram_id = $1 AND expires_at > $2",
                    telegram_id, now
                )
                
                if not subscription_row:
                    logger.error(f"Cannot reissue VPN key for user {telegram_id}: no active subscription")
                    return None, None
                
                subscription = dict(subscription_row)
                old_outline_key_id = subscription.get("outline_key_id")
                old_vpn_key = subscription.get("vpn_key", "")
                
                # 2. Удаляем старый ключ из Outline (если есть)
                if old_outline_key_id:
                    try:
                        await outline_api.delete_outline_key(old_outline_key_id)
                        logger.info(f"Deleted old Outline key {old_outline_key_id} for user {telegram_id} during reissue")
                    except Exception as e:
                        logger.warning(f"Failed to delete old Outline key {old_outline_key_id} for user {telegram_id}: {e}")
                        # Продолжаем, даже если не удалось удалить старый ключ
                
                # 3. Создаем новый ключ через Outline API
                key_result = await outline_api.create_outline_key()
                if not key_result:
                    logger.error(f"Failed to create Outline key for reissue for user {telegram_id}")
                    return None, None
                
                new_outline_key_id, new_vpn_key = key_result
                
                # 4. Обновляем подписку (expires_at НЕ меняется)
                await conn.execute(
                    "UPDATE subscriptions SET outline_key_id = $1, vpn_key = $2 WHERE telegram_id = $3",
                    new_outline_key_id, new_vpn_key, telegram_id
                )
                
                # 5. Записываем в историю подписок
                expires_at = subscription["expires_at"]
                await _log_subscription_history_atomic(conn, telegram_id, new_vpn_key, now, expires_at, "manual_reissue")
                
                # 6. Записываем событие в audit_log
                details = f"User {telegram_id}, Old key: {old_vpn_key[:20] if old_vpn_key else 'N/A'}..., New key: {new_vpn_key[:20]}..., Expires: {expires_at.isoformat()}"
                await _log_audit_event_atomic(conn, "vpn_key_reissued", admin_telegram_id, telegram_id, details)
                
                logger.info(f"VPN key reissued for user {telegram_id} by admin {admin_telegram_id}")
                return new_vpn_key, old_vpn_key
                
            except Exception as e:
                logger.exception(f"Error in reissue_vpn_key_atomic for user {telegram_id}, transaction rolled back")
                raise


# Функция _get_free_vpn_key_atomic удалена - больше не используется
# VPN-ключи теперь создаются динамически через Outline API


async def grant_access(
    telegram_id: int,
    duration: timedelta,
    source: str,
    admin_telegram_id: Optional[int] = None,
    admin_grant_days: Optional[int] = None,
    conn=None
) -> Tuple[Optional[datetime], Optional[str], bool]:
    """
    ЕДИНАЯ ФУНКЦИЯ ВЫДАЧИ ДОСТУПА (ЕДИНАЯ ТОЧКА ИСТИНЫ)
    
    Защищена от двойного создания Outline-ключей.
    
    Логика:
    1. Если подписка активна (expires_at > now) И outline_key_id IS NOT NULL:
       - НЕ создавать новый ключ
       - ТОЛЬКО обновлять expires_at += duration
    2. Если подписка неактивна (expires_at <= now) ИЛИ её нет:
       - Создать новый Outline-ключ
       - Установить expires_at = now + duration
    
    Args:
        telegram_id: Telegram ID пользователя
        duration: Продолжительность доступа (timedelta)
        source: Источник выдачи ('payment', 'admin', 'test')
        admin_telegram_id: Telegram ID администратора (для admin-источников)
        admin_grant_days: Количество дней для админ-доступа (для умных напоминаний)
        conn: Соединение с БД (если None, создаётся новое)
    
    Returns:
        (expires_at, vpn_key, is_renewal) или (None, None, False) при ошибке
        is_renewal: True если подписка была продлена, False если создана новая
    """
    if conn is None:
        pool = await get_pool()
        conn = await pool.acquire()
        should_release_conn = True
    else:
        should_release_conn = False
    
    try:
        now = datetime.now()
        
        # 1. Получаем текущую подписку
        subscription_row = await conn.fetchrow(
            "SELECT * FROM subscriptions WHERE telegram_id = $1",
            telegram_id
        )
        subscription = dict(subscription_row) if subscription_row else None
        
        # 2. Определяем логику выдачи доступа
        final_outline_key_id = None
        is_renewal = False
        
        if subscription:
            subscription_expires_at = subscription["expires_at"]
            outline_key_id = subscription.get("outline_key_id")
            
            # КРИТИЧЕСКАЯ ЗАЩИТА: если подписка активна И ключ существует - НЕ создаём новый
            if subscription_expires_at > now and outline_key_id is not None:
                # Активная подписка с существующим ключом - только продлеваем
                final_vpn_key = subscription.get("vpn_key")
                final_outline_key_id = outline_key_id
                base_date = max(subscription_expires_at, now)
                expires_at = base_date + duration
                is_renewal = True
                start_date = subscription_expires_at if subscription_expires_at > now else now
                logger.info(f"grant_access: Renewing active subscription for user {telegram_id}, keeping outline_key_id={outline_key_id}, source={source}")
            else:
                # Подписка истекла или ключ отсутствует - создаём новый
                if outline_key_id:
                    logger.info(f"grant_access: Subscription expired/no key for user {telegram_id}, will create new key, source={source}")
                
                # Создаём новый ключ через Outline API
                key_result = await outline_api.create_outline_key()
                if not key_result:
                    logger.error(f"grant_access: Failed to create Outline key for user {telegram_id}, source={source}")
                    return None, None, False
                
                final_outline_key_id, final_vpn_key = key_result
                expires_at = now + duration
                is_renewal = False
                start_date = now
                logger.info(f"grant_access: Created new Outline key for user {telegram_id}, outline_key_id={final_outline_key_id}, source={source}")
        else:
            # Подписки нет - создаём новый ключ
            key_result = await outline_api.create_outline_key()
            if not key_result:
                logger.error(f"grant_access: Failed to create Outline key for new subscription, user {telegram_id}, source={source}")
                return None, None, False
            
            final_outline_key_id, final_vpn_key = key_result
            expires_at = now + duration
            is_renewal = False
            start_date = now
            logger.info(f"grant_access: Created new Outline key for new subscription, user {telegram_id}, outline_key_id={final_outline_key_id}, source={source}")
        
        # 3. Определяем action_type для истории
        if source == "payment":
            history_action_type = "renewal" if is_renewal else "purchase"
        elif source == "admin":
            history_action_type = "admin_grant"
        else:
            history_action_type = source
        
        # 4. Сохраняем/обновляем подписку
        if is_renewal:
            # При продлении activated_at не меняется
            await conn.execute(
                """INSERT INTO subscriptions (telegram_id, outline_key_id, vpn_key, expires_at, reminder_sent, reminder_3d_sent, reminder_24h_sent, reminder_3h_sent, reminder_6h_sent, admin_grant_days)
                   VALUES ($1, $2, $3, $4, FALSE, FALSE, FALSE, FALSE, FALSE, $5)
                   ON CONFLICT (telegram_id) 
                   DO UPDATE SET outline_key_id = $2, vpn_key = $3, expires_at = $4, reminder_sent = FALSE, reminder_3d_sent = FALSE, reminder_24h_sent = FALSE, reminder_3h_sent = FALSE, reminder_6h_sent = FALSE, admin_grant_days = $5""",
                telegram_id, final_outline_key_id, final_vpn_key, expires_at, admin_grant_days
            )
        else:
            # Для новой подписки устанавливаем activated_at
            await conn.execute(
                """INSERT INTO subscriptions (telegram_id, outline_key_id, vpn_key, expires_at, reminder_sent, reminder_3d_sent, reminder_24h_sent, reminder_3h_sent, reminder_6h_sent, admin_grant_days, activated_at, last_bytes)
                   VALUES ($1, $2, $3, $4, FALSE, FALSE, FALSE, FALSE, FALSE, $5, $6, 0)
                   ON CONFLICT (telegram_id) 
                   DO UPDATE SET outline_key_id = $2, vpn_key = $3, expires_at = $4, reminder_sent = FALSE, reminder_3d_sent = FALSE, reminder_24h_sent = FALSE, reminder_3h_sent = FALSE, reminder_6h_sent = FALSE, admin_grant_days = $5, activated_at = COALESCE(subscriptions.activated_at, $6), last_bytes = 0""",
                telegram_id, final_outline_key_id, final_vpn_key, expires_at, admin_grant_days, now
            )
        
        # 5. Записываем в историю подписок
        await _log_subscription_history_atomic(conn, telegram_id, final_vpn_key, start_date, expires_at, history_action_type)
        
        # 6. Записываем событие в audit_log (если есть admin_telegram_id)
        if admin_telegram_id:
            duration_str = f"{duration.days} days" if duration.days > 0 else f"{int(duration.total_seconds() / 60)} minutes"
            details = f"Granted {duration_str} access via {source}, Expires: {expires_at.isoformat()}, VPN key: {final_vpn_key[:20]}..."
            await _log_audit_event_atomic(conn, "admin_grant", admin_telegram_id, telegram_id, details)
        
        logger.info(f"grant_access: Successfully granted access to user {telegram_id}, source={source}, is_renewal={is_renewal}, expires_at={expires_at}")
        return expires_at, final_vpn_key, is_renewal
        
    except Exception as e:
        logger.exception(f"grant_access: Error granting access to user {telegram_id}, source={source}")
        return None, None, False
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


async def approve_payment_atomic(payment_id: int, months: int, admin_telegram_id: int) -> Tuple[Optional[datetime], bool, Optional[str]]:
    """Атомарно подтвердить платеж в одной транзакции
    
    В одной транзакции:
    - обновляет payment → approved
    - создает VPN-ключ через Outline API (если нужен новый)
    - создает/продлевает subscription с VPN-ключом
    - записывает событие в audit_log
    
    Логика выдачи ключей:
    - Если подписка активна (expires_at > now): переиспользуется существующий ключ Outline
    - Если подписка закончилась (expires_at <= now) или её нет: создается новый ключ через Outline API
    
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
                expires_at, final_vpn_key, is_renewal = await grant_access(
                    telegram_id=telegram_id,
                    duration=tariff_duration,
                    source="payment",
                    admin_telegram_id=None,
                    admin_grant_days=None,
                    conn=conn
                )
                
                if expires_at is None or final_vpn_key is None:
                    logger.error(f"Failed to grant access for payment {payment_id}, user {telegram_id}")
                    return None, False, None
                
                history_action_type = "renewal" if is_renewal else "purchase"
                
                # 7. Записываем событие в audit_log
                audit_action_type = "subscription_renewed" if is_renewal else "payment_approved"
                details = f"Payment ID: {payment_id}, Tariff: {months} months, Expires: {expires_at.isoformat()}, VPN key: {final_vpn_key[:20]}..."
                await _log_audit_event_atomic(conn, audit_action_type, admin_telegram_id, telegram_id, details)
                
                # 8. Обрабатываем реферальный бонус (только при первой оплате, не при продлении)
                if not is_renewal:
                    # Проверяем, есть ли у пользователя реферер
                    user_row = await conn.fetchrow(
                        "SELECT referred_by FROM users WHERE telegram_id = $1", telegram_id
                    )
                    if user_row and user_row["referred_by"]:
                        # Начисляем бонус рефереру (используем то же соединение)
                        try:
                            # Получаем запись о реферале
                            referral_row = await conn.fetchrow(
                                "SELECT * FROM referrals WHERE referred_id = $1 AND rewarded = FALSE", telegram_id
                            )
                            
                            if referral_row:
                                referral = dict(referral_row)
                                referrer_id = referral["referrer_id"]
                                
                                # Начисляем бонус рефереру: +7 дней доступа
                                bonus_duration = timedelta(days=7)
                                expires_at_bonus, vpn_key_bonus, is_renewal_bonus = await grant_access(
                                    telegram_id=referrer_id,
                                    duration=bonus_duration,
                                    source="referral",
                                    admin_telegram_id=None,
                                    admin_grant_days=None,
                                    conn=conn
                                )
                                
                                if expires_at_bonus:
                                    # Помечаем бонус как начисленный
                                    await conn.execute(
                                        "UPDATE referrals SET rewarded = TRUE WHERE referred_id = $1",
                                        telegram_id
                                    )
                                    
                                    # Логируем событие
                                    await _log_audit_event_atomic(
                                        conn,
                                        "referral_reward",
                                        referrer_id,
                                        telegram_id,
                                        f"Referral bonus granted: +7 days, expires_at={expires_at_bonus.isoformat()}"
                                    )
                                    
                                    logger.info(f"Referral bonus granted: referrer_id={referrer_id}, referred_id={telegram_id}")
                        except Exception as e:
                            logger.exception(f"Error processing referral reward for referred_id={telegram_id}")
                
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
                expires_at, final_vpn_key, _ = await grant_access(
                    telegram_id=telegram_id,
                    duration=duration,
                    source="admin",
                    admin_telegram_id=admin_telegram_id,
                    admin_grant_days=days,
                    conn=conn
                )
                
                if expires_at is None or final_vpn_key is None:
                    logger.error(f"Failed to grant access for admin grant, user {telegram_id}")
                    return None, None
                
                logger.info(f"Admin {admin_telegram_id} granted {days} days access to user {telegram_id}")
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
                expires_at, final_vpn_key, _ = await grant_access(
                    telegram_id=telegram_id,
                    duration=duration,
                    source="admin",
                    admin_telegram_id=admin_telegram_id,
                    admin_grant_days=None,  # Для минутного доступа не используется
                    conn=conn
                )
                
                if expires_at is None or final_vpn_key is None:
                    logger.error(f"Failed to grant access for admin grant minutes, user {telegram_id}")
                    return None, None
                
                logger.info(f"Admin {admin_telegram_id} granted {minutes} minutes access to user {telegram_id}")
                return expires_at, final_vpn_key
                
            except Exception as e:
                logger.exception(f"Error in admin_grant_access_minutes_atomic for user {telegram_id}, transaction rolled back")
                raise


async def admin_revoke_access_atomic(telegram_id: int, admin_telegram_id: int) -> bool:
    """Атомарно лишить доступа пользователя (админ)
    
    В одной транзакции:
    - удаляет VPN-ключ из Outline (если есть outline_key_id)
    - устанавливает expires_at = NOW()
    - очищает outline_key_id и vpn_key
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
                outline_key_id = subscription.get("outline_key_id")
                vpn_key = subscription.get("vpn_key", "")
                
                # 2. Удаляем VPN-ключ из Outline (если есть)
                if outline_key_id:
                    try:
                        deleted = await outline_api.delete_outline_key(outline_key_id)
                        if deleted:
                            logger.info(f"Deleted Outline key {outline_key_id} for user {telegram_id} during admin revoke")
                        else:
                            logger.warning(f"Failed to delete Outline key {outline_key_id} for user {telegram_id} (may already be deleted)")
                    except Exception as e:
                        # Не падаем, если ключ уже удален или произошла ошибка
                        logger.error(f"Error deleting Outline key {outline_key_id} for user {telegram_id}: {e}", exc_info=True)
                
                # 3. Очищаем подписку: устанавливаем expires_at = NOW(), очищаем outline_key_id и vpn_key
                await conn.execute(
                    "UPDATE subscriptions SET expires_at = $1, outline_key_id = NULL, vpn_key = NULL WHERE telegram_id = $2",
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
