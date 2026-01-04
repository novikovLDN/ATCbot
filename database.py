import asyncpg
import os
import sys
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, Tuple
import logging
import config

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
                reminder_sent BOOLEAN DEFAULT FALSE,
                reminder_3d_sent BOOLEAN DEFAULT FALSE,
                reminder_24h_sent BOOLEAN DEFAULT FALSE,
                reminder_3h_sent BOOLEAN DEFAULT FALSE,
                reminder_6h_sent BOOLEAN DEFAULT FALSE,
                admin_grant_days INTEGER DEFAULT NULL
            )
        """)
        
        # Миграция: добавляем новые поля для напоминаний, если их нет
        try:
            await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS reminder_3d_sent BOOLEAN DEFAULT FALSE")
            await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS reminder_24h_sent BOOLEAN DEFAULT FALSE")
            await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS reminder_3h_sent BOOLEAN DEFAULT FALSE")
            await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS reminder_6h_sent BOOLEAN DEFAULT FALSE")
            await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS admin_grant_days INTEGER DEFAULT NULL")
        except Exception:
            # Колонки уже существуют
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
        
        # Создаём одну строку, если её нет
        existing = await conn.fetchval("SELECT COUNT(*) FROM incident_settings")
        if existing == 0:
            await conn.execute("""
                INSERT INTO incident_settings (is_active, incident_text)
                VALUES (FALSE, NULL)
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
    """Создать платеж и вернуть его ID. Возвращает None, если уже есть pending платеж
    
    Автоматически применяет скидку 25% для первой покупки на тарифы 3/6/12 месяцев.
    """
    # Проверяем наличие pending платежа
    existing_payment = await get_pending_payment_by_user(telegram_id)
    if existing_payment:
        return None  # У пользователя уже есть pending платеж
    
    # Проверяем, является ли это первой покупкой
    is_first_purchase = await is_user_first_purchase(telegram_id)
    
    # Рассчитываем цену с учетом скидки
    tariff_data = config.TARIFFS.get(tariff, config.TARIFFS["1"])
    base_price = tariff_data["price"]
    
    # Применяем скидку 25% для первой покупки на тарифы 3/6/12 месяцев
    if is_first_purchase and tariff in ["3", "6", "12"]:
        discounted_price = int(base_price * 0.75)  # 25% скидка
        amount = discounted_price
    else:
        amount = base_price
    
    pool = await get_pool()
    async with pool.acquire() as conn:
        payment_id = await conn.fetchval(
            "INSERT INTO payments (telegram_id, tariff, amount, status) VALUES ($1, $2, $3, 'pending') RETURNING id",
            telegram_id, tariff, amount
        )
        
        # Логируем применение скидки
        if is_first_purchase and tariff in ["3", "6", "12"]:
            details = f"First purchase discount applied: tariff={tariff}, base_price={base_price}, discounted_price={discounted_price}"
            await _log_audit_event_atomic(conn, "first_purchase_discount_applied", telegram_id, telegram_id, details)
        
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
            """INSERT INTO subscriptions (telegram_id, vpn_key, expires_at, reminder_sent, reminder_3d_sent, reminder_24h_sent, reminder_3h_sent, reminder_6h_sent, admin_grant_days)
               VALUES ($1, $2, $3, FALSE, FALSE, FALSE, FALSE, FALSE, NULL)
               ON CONFLICT (telegram_id) 
               DO UPDATE SET vpn_key = $2, expires_at = $3, reminder_sent = FALSE, reminder_3d_sent = FALSE, reminder_24h_sent = FALSE, reminder_3h_sent = FALSE, reminder_6h_sent = FALSE, admin_grant_days = NULL""",
            telegram_id, vpn_key, expires_at
        )
        return expires_at, is_renewal


async def get_free_vpn_keys_count() -> int:
    """Получить количество свободных VPN-ключей"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM vpn_keys WHERE is_used = FALSE"
        )
        return count if count else 0


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
    - получает новый vpn_key из vpn_keys
    - обновляет subscriptions.vpn_key
    - старый ключ НЕ возвращается в пул
    - expires_at НЕ меняется
    - записывает событие в audit_log
    
    Args:
        telegram_id: Telegram ID пользователя
        admin_telegram_id: Telegram ID администратора, который выполняет перевыпуск
    
    Returns:
        (new_vpn_key, old_vpn_key) или (None, None) если нет активной подписки или нет свободных ключей
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
                old_vpn_key = subscription["vpn_key"]
                
                # 2. Получаем новый VPN-ключ из таблицы vpn_keys
                new_vpn_key = await _get_free_vpn_key_atomic(conn, telegram_id)
                
                if not new_vpn_key:
                    logger.error(f"Cannot reissue VPN key for user {telegram_id}: no free VPN keys available")
                    return None, None
                
                # 3. Обновляем подписку (expires_at НЕ меняется)
                await conn.execute(
                    "UPDATE subscriptions SET vpn_key = $1 WHERE telegram_id = $2",
                    new_vpn_key, telegram_id
                )
                
                # 4. Записываем в историю подписок
                expires_at = subscription["expires_at"]
                await _log_subscription_history_atomic(conn, telegram_id, new_vpn_key, now, expires_at, "manual_reissue")
                
                # 5. Записываем событие в audit_log
                details = f"User {telegram_id}, Old key: {old_vpn_key[:20]}..., New key: {new_vpn_key[:20]}..., Expires: {expires_at.isoformat()}"
                await _log_audit_event_atomic(conn, "vpn_key_reissued", admin_telegram_id, telegram_id, details)
                
                logger.info(f"VPN key reissued for user {telegram_id} by admin {admin_telegram_id}")
                return new_vpn_key, old_vpn_key
                
            except Exception as e:
                logger.exception(f"Error in reissue_vpn_key_atomic for user {telegram_id}, transaction rolled back")
                raise


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


async def approve_payment_atomic(payment_id: int, months: int, admin_telegram_id: int) -> Tuple[Optional[datetime], bool, Optional[str]]:
    """Атомарно подтвердить платеж в одной транзакции
    
    В одной транзакции:
    - обновляет payment → approved
    - получает VPN-ключ из таблицы vpn_keys (если нужен новый)
    - создает/продлевает subscription с VPN-ключом
    - записывает событие в audit_log
    
    Логика выдачи ключей:
    - Если подписка активна (expires_at > now): переиспользуется существующий ключ
    - Если подписка закончилась (expires_at <= now) или её нет: используется новый ключ из БД
    
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
                        history_action_type = "renewal"
                        start_date = subscription_expires_at if subscription_expires_at > now else now
                        logger.info(f"Renewing active subscription for user {telegram_id}, reusing vpn_key, expires_at: {subscription_expires_at} -> {expires_at}")
                    else:
                        # Подписка закончилась - получаем новый ключ из БД
                        final_vpn_key = await _get_free_vpn_key_atomic(conn, telegram_id)
                        if not final_vpn_key:
                            logger.error(f"No free VPN keys available for payment {payment_id}, user {telegram_id}")
                            return None, False, None
                        expires_at = now + tariff_duration
                        is_renewal = False
                        history_action_type = "reissue"
                        start_date = now
                        logger.info(f"Subscription expired for user {telegram_id}, using new vpn_key from DB, expires_at: {expires_at}")
                else:
                    # Подписки никогда не было - получаем новый ключ из БД
                    final_vpn_key = await _get_free_vpn_key_atomic(conn, telegram_id)
                    if not final_vpn_key:
                        logger.error(f"No free VPN keys available for payment {payment_id}, user {telegram_id}")
                        return None, False, None
                    expires_at = now + tariff_duration
                    is_renewal = False
                    history_action_type = "purchase"
                    start_date = now
                    logger.info(f"Creating new subscription for user {telegram_id}, using new vpn_key from DB, expires_at: {expires_at}")
                
                # 5. Создаем/обновляем подписку (reminder_sent сбрасывается в FALSE при продлении)
                # Для оплаченных тарифов admin_grant_days = NULL
                await conn.execute(
                    """INSERT INTO subscriptions (telegram_id, vpn_key, expires_at, reminder_sent, reminder_3d_sent, reminder_24h_sent, reminder_3h_sent, reminder_6h_sent, admin_grant_days)
                       VALUES ($1, $2, $3, FALSE, FALSE, FALSE, FALSE, FALSE, NULL)
                       ON CONFLICT (telegram_id) 
                       DO UPDATE SET vpn_key = $2, expires_at = $3, reminder_sent = FALSE, reminder_3d_sent = FALSE, reminder_24h_sent = FALSE, reminder_3h_sent = FALSE, reminder_6h_sent = FALSE, admin_grant_days = NULL""",
                    telegram_id, final_vpn_key, expires_at
                )
                
                # 6. Записываем в историю подписок
                await _log_subscription_history_atomic(conn, telegram_id, final_vpn_key, start_date, expires_at, history_action_type)
                
                # 7. Записываем событие в audit_log
                audit_action_type = "subscription_renewed" if is_renewal else "payment_approved"
                details = f"Payment ID: {payment_id}, Tariff: {months} months, Expires: {expires_at.isoformat()}, VPN key: {final_vpn_key[:20]}..."
                await _log_audit_event_atomic(conn, audit_action_type, admin_telegram_id, telegram_id, details)
                
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


async def get_vpn_keys_stats() -> Dict[str, int]:
    """Получить статистику по VPN-ключам
    
    Returns:
        Словарь с ключами:
        - total: всего ключей
        - used: использованных ключей
        - free: свободных ключей
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        total = await conn.fetchval("SELECT COUNT(*) FROM vpn_keys")
        used = await conn.fetchval("SELECT COUNT(*) FROM vpn_keys WHERE is_used = TRUE")
        free = await conn.fetchval("SELECT COUNT(*) FROM vpn_keys WHERE is_used = FALSE")
        
        return {
            "total": total or 0,
            "used": used or 0,
            "free": free or 0,
        }


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
    
    В одной транзакции:
    - если есть активная подписка: продлевает expires_at на выбранный срок
    - если подписки нет или истекла: создает новую подписку
    - если нужно: получает новый VPN-ключ
    - записывает в subscription_history (action = admin_grant)
    - записывает событие в audit_log
    
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
                now = datetime.now()
                duration = timedelta(days=days)
                
                # 1. Получаем текущую подписку (если есть)
                subscription_row = await conn.fetchrow(
                    "SELECT * FROM subscriptions WHERE telegram_id = $1",
                    telegram_id
                )
                subscription = dict(subscription_row) if subscription_row else None
                
                # 2. Определяем логику
                if subscription:
                    subscription_expires_at = subscription["expires_at"]
                    if subscription_expires_at > now:
                        # Активная подписка - продлеваем, используем текущий ключ
                        final_vpn_key = subscription["vpn_key"]
                        base_date = max(subscription_expires_at, now)
                        expires_at = base_date + duration
                        start_date = now  # Для истории подписок используем текущую дату
                    else:
                        # Подписка истекла - получаем новый ключ
                        final_vpn_key = await _get_free_vpn_key_atomic(conn, telegram_id)
                        if not final_vpn_key:
                            logger.error(f"No free VPN keys available for admin grant to user {telegram_id}")
                            return None, None
                        expires_at = now + duration
                        start_date = now
                else:
                    # Подписки нет - получаем новый ключ
                    final_vpn_key = await _get_free_vpn_key_atomic(conn, telegram_id)
                    if not final_vpn_key:
                        logger.error(f"No free VPN keys available for admin grant to user {telegram_id}")
                        return None, None
                    expires_at = now + duration
                    start_date = now
                
                # 3. Создаем/обновляем подписку
                # Сохраняем количество дней для админ-доступа (для умных напоминаний)
                await conn.execute(
                    """INSERT INTO subscriptions (telegram_id, vpn_key, expires_at, reminder_sent, reminder_3d_sent, reminder_24h_sent, reminder_3h_sent, reminder_6h_sent, admin_grant_days)
                       VALUES ($1, $2, $3, FALSE, FALSE, FALSE, FALSE, FALSE, $4)
                       ON CONFLICT (telegram_id) 
                       DO UPDATE SET vpn_key = $2, expires_at = $3, reminder_sent = FALSE, reminder_3d_sent = FALSE, reminder_24h_sent = FALSE, reminder_3h_sent = FALSE, reminder_6h_sent = FALSE, admin_grant_days = $4""",
                    telegram_id, final_vpn_key, expires_at, days
                )
                
                # 4. Записываем в историю подписок
                await _log_subscription_history_atomic(conn, telegram_id, final_vpn_key, start_date, expires_at, "admin_grant")
                
                # 5. Записываем событие в audit_log
                details = f"Granted {days} days access, Expires: {expires_at.isoformat()}, VPN key: {final_vpn_key[:20]}..."
                await _log_audit_event_atomic(conn, "admin_grant", admin_telegram_id, telegram_id, details)
                
                logger.info(f"Admin {admin_telegram_id} granted {days} days access to user {telegram_id}")
                return expires_at, final_vpn_key
                
            except Exception as e:
                logger.exception(f"Error in admin_grant_access_atomic for user {telegram_id}, transaction rolled back")
                raise


async def admin_revoke_access_atomic(telegram_id: int, admin_telegram_id: int) -> bool:
    """Атомарно лишить доступа пользователя (админ)
    
    В одной транзакции:
    - устанавливает expires_at = NOW() (если есть активная подписка)
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
                vpn_key = subscription["vpn_key"]
                
                # 2. Устанавливаем expires_at = NOW()
                await conn.execute(
                    "UPDATE subscriptions SET expires_at = $1 WHERE telegram_id = $2",
                    now, telegram_id
                )
                
                # 3. Записываем в историю подписок
                await _log_subscription_history_atomic(conn, telegram_id, vpn_key, now, now, "admin_revoke")
                
                # 4. Записываем событие в audit_log
                details = f"Revoked access, Old expires_at: {old_expires_at.isoformat()}, VPN key: {vpn_key[:20]}..."
                await _log_audit_event_atomic(conn, "admin_revoke", admin_telegram_id, telegram_id, details)
                
                logger.info(f"Admin {admin_telegram_id} revoked access for user {telegram_id}")
                return True
                
            except Exception as e:
                logger.exception(f"Error in admin_revoke_access_atomic for user {telegram_id}, transaction rolled back")
                raise
