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
                message TEXT NOT NULL,
                type TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                sent_by BIGINT NOT NULL
            )
        """)
        
        # Таблица broadcast_log
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS broadcast_log (
                id SERIAL PRIMARY KEY,
                broadcast_id INTEGER NOT NULL REFERENCES broadcasts(id) ON DELETE CASCADE,
                telegram_id BIGINT NOT NULL,
                status TEXT NOT NULL,
                sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
            """INSERT INTO subscriptions (telegram_id, vpn_key, expires_at, reminder_sent)
               VALUES ($1, $2, $3, FALSE)
               ON CONFLICT (telegram_id) 
               DO UPDATE SET vpn_key = $2, expires_at = $3, reminder_sent = FALSE""",
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
                await conn.execute(
                    """INSERT INTO subscriptions (telegram_id, vpn_key, expires_at, reminder_sent)
                       VALUES ($1, $2, $3, FALSE)
                       ON CONFLICT (telegram_id) 
                       DO UPDATE SET vpn_key = $2, expires_at = $3, reminder_sent = FALSE""",
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
    """Отметить, что напоминание отправлено пользователю"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE subscriptions SET reminder_sent = TRUE WHERE telegram_id = $1",
            telegram_id
        )


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


async def create_broadcast(title: str, message: str, broadcast_type: str, sent_by: int) -> int:
    """Создать новое уведомление
    
    Args:
        title: Заголовок уведомления
        message: Текст уведомления
        broadcast_type: Тип уведомления (info | maintenance | security | promo)
        sent_by: Telegram ID администратора
    
    Returns:
        ID созданного уведомления
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO broadcasts (title, message, type, sent_by)
               VALUES ($1, $2, $3, $4)
               RETURNING id""",
            title, message, broadcast_type, sent_by
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


async def log_broadcast_send(broadcast_id: int, telegram_id: int, status: str):
    """Записать результат отправки уведомления
    
    Args:
        broadcast_id: ID уведомления
        telegram_id: Telegram ID пользователя
        status: Статус отправки (sent | failed)
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO broadcast_log (broadcast_id, telegram_id, status)
               VALUES ($1, $2, $3)""",
            broadcast_id, telegram_id, status
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
