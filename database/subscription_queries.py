"""Чтения по подпискам и платежам.

ЧТО ЗДЕСЬ
    Запросы, которые ничего не меняют и никуда не ходят по сети:
        get_subscription / get_subscription_any / get_active_subscription
        get_all_active_subscriptions
        get_payment / get_last_approved_payment / get_pending_payments
        has_any_subscription / has_any_payment / is_user_first_purchase
        get_admin_stats

ПОЧЕМУ ОТДЕЛЬНО
    Их зовут хендлеры, дашборд и воркеры — то есть почти всё. Раньше ради
    одного SELECT импортировался модуль с выдачей доступа и проведением
    оплаты целиком.

ЕДИНСТВЕННОЕ ИСКЛЮЧЕНИЕ ИЗ «ТОЛЬКО ЧТЕНИЕ»
    get_subscription первым делом зовёт check_and_disable_expired_subscription
    из database/subscription_state.py, и та может погасить истёкшую строку
    и сходить в панель. Так было всегда: чтение подписки — самая частая
    операция в боте, и на неё повешена ленивая проверка истечения. Убрать
    вызов нельзя — истёкшие подписки перестанут гаситься между прогонами
    воркера. Из-за этого модуль не «чисто читающий», и добавлять сюда
    запись в других функциях всё равно не стоит.

ЧТО ЛЕГКО СЛОМАТЬ
    _core.DB_READY читается через модуль, а не импортируется по имени:
    иначе получится копия значения на момент импорта, которая никогда не
    станет True, и бот молча уйдёт в деградированный режим навсегда.
"""
import asyncpg
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import database.core as _core
from database.core import get_pool, _to_db_utc, _normalize_subscription_row
# Ленивая проверка истечения на пути чтения подписки — см. докстринг выше.
from database.subscription_state import check_and_disable_expired_subscription

logger = logging.getLogger(__name__)


async def get_payment(payment_id: int) -> Optional[Dict[str, Any]]:
    """Получить платеж по ID"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM payments WHERE id = $1", payment_id
        )
        return dict(row) if row else None


async def get_last_approved_payment(telegram_id: int, conn: Optional[asyncpg.Connection] = None) -> Optional[Dict[str, Any]]:
    """Получить последний утверждённый платёж пользователя
    
    Args:
        telegram_id: Telegram ID пользователя
        conn: Опциональное соединение (если передано — используется оно, без pool.acquire)
    
    Returns:
        Словарь с данными платежа или None, если платёж не найден
    """
    if conn is not None:
        row = await conn.fetchrow(
            """SELECT * FROM payments 
               WHERE telegram_id = $1 AND status = 'approved'
               ORDER BY created_at DESC
               LIMIT 1""",
            telegram_id
        )
        return dict(row) if row else None
    pool = await get_pool()
    async with pool.acquire() as acquired:
        row = await acquired.fetchrow(
            """SELECT * FROM payments 
               WHERE telegram_id = $1 AND status = 'approved'
               ORDER BY created_at DESC
               LIMIT 1""",
            telegram_id
        )
        return dict(row) if row else None


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
    if not _core.DB_READY:
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
    if not _core.DB_READY:
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


async def get_pending_payments() -> list:
    """Получить все pending платежи (для админа)"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM payments WHERE status = 'pending' ORDER BY created_at DESC"
        )
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


async def get_admin_stats() -> Dict[str, int]:
    """Получить статистику для админ-дашборда

    Returns:
        Словарь с ключами:
        - total_users: всего пользователей
        - active_subscriptions: активных подписок
        - expired_subscriptions: истёкших подписок
        - total_payments: всего платежей
        - approved_payments: подтверждённых платежей
    """
    if not _core.DB_READY:
        logger.warning("DB not ready, get_admin_stats skipped")
        return {"total_users": 0, "active_subscriptions": 0, "expired_subscriptions": 0, "total_payments": 0, "approved_payments": 0}
    pool = await get_pool()
    if pool is None:
        logger.warning("Pool is None, get_admin_stats skipped")
        return {"total_users": 0, "active_subscriptions": 0, "expired_subscriptions": 0, "total_payments": 0, "approved_payments": 0}
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
        
        return {
            "total_users": total_users or 0,
            "active_subscriptions": active_subscriptions or 0,
            "expired_subscriptions": expired_subscriptions or 0,
            "total_payments": total_payments or 0,
            "approved_payments": approved_payments or 0,
        }
