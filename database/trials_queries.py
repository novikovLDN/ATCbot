"""Пробный период и персональные спецпредложения.

ЧТО ЗДЕСЬ ЕСТЬ
    Всё, что отвечает на вопросы «положен ли пользователю триал», «когда он
    истекает» и «есть ли у него активное спецпредложение».

ПРАВИЛА ТРИАЛА
    Триал выдаётся один раз за всю жизнь аккаунта — признак хранится в
    users.trial_used и никогда не сбрасывается автоматически. Проверять
    доступность нужно через is_trial_available: она учитывает и уже
    использованный триал, и наличие действующей платной подписки, которую
    триал не должен подменять.

СПЕЦПРЕДЛОЖЕНИЯ
    Персональная скидка с ограниченным сроком, выдаётся вручную или
    автоматикой удержания. has_active_special_offer — единственный
    правильный способ проверить, действует ли оно прямо сейчас: срок
    может истечь между показом экрана и оплатой.

ПОЧЕМУ ОТДЕЛЬНЫМ ФАЙЛОМ
    Выделено из database/subscriptions.py, где эти функции соседствовали
    с платёжными транзакциями. Логика триала завязана на маркетинг, а не
    на деньги, и меняется по другим поводам.
"""
import asyncpg
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import config
import database.core as _core
from database.core import get_pool, _to_db_utc, _from_db_utc, _normalize_subscription_row

logger = logging.getLogger(__name__)


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


async def set_special_offer(telegram_id: int) -> bool:
    """Установить спецпредложение для пользователя (3 дня, -15%).

    Вызывается когда подписка истекает (source='payment').
    """
    if not _core.DB_READY:
        return False
    pool = await get_pool()
    if pool is None:
        return False
    try:
        async with pool.acquire() as conn:
            now_db = _to_db_utc(datetime.now(timezone.utc))
            await conn.execute(
                "UPDATE users SET special_offer_created_at = $1 WHERE telegram_id = $2",
                now_db, telegram_id
            )
        return True
    except Exception as e:
        logger.warning(f"Failed to set special offer for {telegram_id}: {e}")
        return False


async def get_special_offer_info(telegram_id: int) -> Optional[Dict[str, Any]]:
    """Получить информацию о спецпредложении пользователя.

    Returns:
        Dict с ключами: created_at, expires_at, remaining_seconds, remaining_text
        или None если спецпредложение не активно или истекло.
    """
    if not _core.DB_READY:
        return None
    pool = await get_pool()
    if pool is None:
        return None
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT special_offer_created_at FROM users WHERE telegram_id = $1",
                telegram_id
            )
            if not row or not row["special_offer_created_at"]:
                return None

            created_at = _from_db_utc(row["special_offer_created_at"])
            now = datetime.now(timezone.utc)
            expires_at = created_at + timedelta(days=3)
            remaining = expires_at - now

            if remaining.total_seconds() <= 0:
                return None

            total_seconds = int(remaining.total_seconds())
            days = total_seconds // 86400
            hours = (total_seconds % 86400) // 3600

            if days > 0:
                remaining_text = f"{days}д {hours}ч"
            else:
                remaining_text = f"{hours}ч"

            return {
                "created_at": created_at,
                "expires_at": expires_at,
                "remaining_seconds": total_seconds,
                "remaining_text": remaining_text,
                "discount_percent": 15,
            }
    except Exception as e:
        logger.warning(f"Failed to get special offer for {telegram_id}: {e}")
        return None


async def has_active_special_offer(telegram_id: int) -> bool:
    """Проверить, есть ли активное спецпредложение."""
    info = await get_special_offer_info(telegram_id)
    return info is not None
