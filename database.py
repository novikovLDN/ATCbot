import asyncpg
import os
import sys
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, Tuple
import logging

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
                vpn_key TEXT NOT NULL,
                expires_at TIMESTAMP NOT NULL,
                reminder_sent BOOLEAN DEFAULT FALSE
            )
        """)
        
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
        
        logger.info("Database tables initialized")


async def get_user(telegram_id: int) -> Optional[Dict[str, Any]]:
    """Получить пользователя по Telegram ID"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM users WHERE telegram_id = $1", telegram_id
        )
        return dict(row) if row else None


async def create_user(telegram_id: int, username: Optional[str] = None, language: str = "ru"):
    """Создать нового пользователя"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (telegram_id, username, language) VALUES ($1, $2, $3) ON CONFLICT (telegram_id) DO NOTHING",
            telegram_id, username, language
        )


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
    """Создать платеж и вернуть его ID. Возвращает None, если уже есть pending платеж"""
    # Проверяем наличие pending платежа
    existing_payment = await get_pending_payment_by_user(telegram_id)
    if existing_payment:
        return None  # У пользователя уже есть pending платеж
    
    pool = await get_pool()
    async with pool.acquire() as conn:
        payment_id = await conn.fetchval(
            "INSERT INTO payments (telegram_id, tariff, status) VALUES ($1, $2, 'pending') RETURNING id",
            telegram_id, tariff
        )
        return payment_id


async def get_payment(payment_id: int) -> Optional[Dict[str, Any]]:
    """Получить платеж по ID"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM payments WHERE id = $1", payment_id
        )
        return dict(row) if row else None


async def update_payment_status(payment_id: int, status: str):
    """Обновить статус платежа"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE payments SET status = $1 WHERE id = $2",
            status, payment_id
        )


async def get_subscription(telegram_id: int) -> Optional[Dict[str, Any]]:
    """Получить активную подписку пользователя
    
    Активной считается подписка, у которой expires_at > текущего времени.
    """
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
            """INSERT INTO subscriptions (telegram_id, vpn_key, expires_at, reminder_sent)
               VALUES ($1, $2, $3, FALSE)
               ON CONFLICT (telegram_id) 
               DO UPDATE SET vpn_key = $2, expires_at = $3, reminder_sent = FALSE""",
            telegram_id, vpn_key, expires_at
        )
        return expires_at, is_renewal


async def _get_free_vpn_key_atomic(conn, telegram_id: int) -> Optional[str]:
    """Атомарно получить свободный VPN-ключ из таблицы vpn_keys
    
    Использует SELECT ... FOR UPDATE для защиты от race condition.
    Должна вызываться ТОЛЬКО внутри активной транзакции.
    
    Returns:
        VPN-ключ (str) или None, если свободных ключей нет
    """
    # Выбираем один свободный ключ с блокировкой строки
    row = await conn.fetchrow(
        """SELECT vpn_key FROM vpn_keys
           WHERE is_used = FALSE
           LIMIT 1
           FOR UPDATE""",
    )
    
    if not row:
        return None
    
    vpn_key = row["vpn_key"]
    now = datetime.now()
    
    # Помечаем ключ как использованный
    await conn.execute(
        """UPDATE vpn_keys
           SET is_used = TRUE,
               assigned_to = $1,
               assigned_at = $2
           WHERE vpn_key = $3""",
        telegram_id, now, vpn_key
    )
    
    return vpn_key


async def approve_payment_atomic(payment_id: int, months: int) -> Tuple[Optional[datetime], bool, Optional[str]]:
    """Атомарно подтвердить платеж в одной транзакции
    
    В одной транзакции:
    - обновляет payment → approved
    - получает VPN-ключ из таблицы vpn_keys (если нужен новый)
    - создает/продлевает subscription с VPN-ключом
    
    Логика выдачи ключей:
    - Если подписка активна (expires_at > now): переиспользуется существующий ключ
    - Если подписка закончилась (expires_at <= now) или её нет: используется новый ключ из БД
    
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
                tariff_duration = timedelta(days=months * 30)
                
                subscription_row = await conn.fetchrow(
                    "SELECT * FROM subscriptions WHERE telegram_id = $1",
                    telegram_id
                )
                subscription = dict(subscription_row) if subscription_row else None
                
                # 4. Определяем, какой ключ использовать
                if subscription:
                    subscription_expires_at = subscription["expires_at"]
                    if subscription_expires_at > now:
                        # Подписка активна - переиспользуем существующий ключ
                        final_vpn_key = subscription["vpn_key"]
                        base_date = max(subscription_expires_at, now)
                        expires_at = base_date + tariff_duration
                        is_renewal = True
                        logger.info(f"Renewing active subscription for user {telegram_id}, reusing vpn_key, expires_at: {subscription_expires_at} -> {expires_at}")
                    else:
                        # Подписка закончилась - получаем новый ключ из БД
                        final_vpn_key = await _get_free_vpn_key_atomic(conn, telegram_id)
                        if not final_vpn_key:
                            logger.error(f"No free VPN keys available for payment {payment_id}, user {telegram_id}")
                            return None, False, None
                        expires_at = now + tariff_duration
                        is_renewal = False
                        logger.info(f"Subscription expired for user {telegram_id}, using new vpn_key from DB, expires_at: {expires_at}")
                else:
                    # Подписки никогда не было - получаем новый ключ из БД
                    final_vpn_key = await _get_free_vpn_key_atomic(conn, telegram_id)
                    if not final_vpn_key:
                        logger.error(f"No free VPN keys available for payment {payment_id}, user {telegram_id}")
                        return None, False, None
                    expires_at = now + tariff_duration
                    is_renewal = False
                    logger.info(f"Creating new subscription for user {telegram_id}, using new vpn_key from DB, expires_at: {expires_at}")
                
                # 5. Создаем/обновляем подписку (reminder_sent сбрасывается в FALSE при продлении)
                await conn.execute(
                    """INSERT INTO subscriptions (telegram_id, vpn_key, expires_at, reminder_sent)
                       VALUES ($1, $2, $3, FALSE)
                       ON CONFLICT (telegram_id) 
                       DO UPDATE SET vpn_key = $2, expires_at = $3, reminder_sent = FALSE""",
                    telegram_id, final_vpn_key, expires_at
                )
                
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
    """Отметить, что напоминание отправлено пользователю"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE subscriptions SET reminder_sent = TRUE WHERE telegram_id = $1",
            telegram_id
        )
