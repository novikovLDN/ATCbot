"""Персональные скидки и VIP-статус.

ЧТО ЗДЕСЬ ЕСТЬ
    Выдача, чтение и снятие персональной скидки, разовая скидка за
    приглашение друга, а также VIP-статус.

КАК УСТРОЕНА СКИДКА
    Скидка привязана к пользователю и имеет срок действия. Просроченная
    скидка не удаляется из базы — она просто перестаёт считаться активной,
    поэтому любая проверка обязана сравнивать expires_at с текущим временем,
    а не просто смотреть на наличие строки.

РАЗОВАЯ СКИДКА ЗА ПРИГЛАШЕНИЕ
    has_claimed_referral_share_discount отвечает на вопрос «этот пользователь
    уже забирал такую скидку». Признак не сбрасывается: скидка разовая за всё
    время жизни аккаунта, иначе её можно получать бесконечно.

VIP
    Отдельный флаг, не связанный со скидками. Влияет на доступ к отдельным
    возможностям и проверяется через is_vip_user, которая умеет работать
    внутри чужой транзакции — передавайте conn, если вызываете её оттуда,
    иначе получите чтение вне транзакции и рассогласование.

ПОЧЕМУ ОТДЕЛЬНЫМ ФАЙЛОМ
    Выделено из database/admin.py. Это маркетинговые инструменты: их правят
    при работе над акциями, а не над платежами.
"""
import asyncpg
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import config
import database.core as _core
from database.core import get_pool, _to_db_utc, _from_db_utc

logger = logging.getLogger(__name__)


async def get_user_discount(telegram_id: int, conn: Optional[asyncpg.Connection] = None) -> Optional[Dict[str, Any]]:
    """Получить активную персональную скидку пользователя
    
    Args:
        telegram_id: Telegram ID пользователя
        conn: Опциональное соединение (если передано — используется оно, без pool.acquire)
    
    Returns:
        Словарь с данными скидки или None, если скидки нет или она истекла
    """
    now = datetime.now(timezone.utc)
    if conn is not None:
        row = await conn.fetchrow(
            """SELECT * FROM user_discounts 
               WHERE telegram_id = $1 
               AND (expires_at IS NULL OR expires_at > $2)""",
            telegram_id, _to_db_utc(now)
        )
        return dict(row) if row else None
    pool = await get_pool()
    async with pool.acquire() as acquired:
        row = await acquired.fetchrow(
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
    from database.subscriptions import _log_audit_event_atomic

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


# ==================== REFERRAL SHARE-DISCOUNT CLAIMS ====================
#
# Lifetime registry of users who have already activated the «Поделиться
# скидкой» broadcast deep-link. Each telegram_id can claim only once,
# enforced via PRIMARY KEY on telegram_id. See migration 060.


async def has_claimed_referral_share_discount(telegram_id: int) -> bool:
    """True если пользователь уже когда-либо активировал refd-скидку.

    Используется в start.py при обработке `refd_<code>` — если True,
    показываем «уже было активировано», без повторной выдачи."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            row = await conn.fetchrow(
                "SELECT 1 FROM referral_share_discount_claims WHERE telegram_id = $1",
                telegram_id,
            )
            return row is not None
        except Exception as e:
            # Если таблицы ещё нет (миграция не накатана) — считаем что
            # не клеймил, чтобы фича не падала. После миграции всё
            # выровняется естественным путём.
            logger.warning(
                "REFDC_CHECK_FAIL user=%s error=%s — treating as not-claimed",
                telegram_id, e,
            )
            return False


async def record_referral_share_discount_claim(
    telegram_id: int,
    referrer_id: int,
    discount_percent: int,
    duration_hours: int,
    expires_at: datetime,
) -> bool:
    """Зафиксировать факт активации refd-скидки этим юзером.

    PRIMARY KEY на telegram_id даёт идемпотентность: повторный INSERT
    с тем же id просто упадёт по конфликту → возвращаем False, чтобы
    вызывающий не выдал скидку повторно (race в один и тот же tick)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            result = await conn.execute(
                """INSERT INTO referral_share_discount_claims
                   (telegram_id, referrer_id, discount_percent, duration_hours, expires_at)
                   VALUES ($1, $2, $3, $4, $5)
                   ON CONFLICT (telegram_id) DO NOTHING""",
                telegram_id, referrer_id, discount_percent, duration_hours,
                _to_db_utc(expires_at),
            )
            # asyncpg возвращает 'INSERT 0 1' при успехе, 'INSERT 0 0' при конфликте
            return result.endswith(" 1")
        except Exception as e:
            logger.exception(
                "REFDC_RECORD_FAIL user=%s referrer=%s error=%s",
                telegram_id, referrer_id, e,
            )
            return False


async def delete_user_discount(telegram_id: int, deleted_by: int) -> bool:
    """Удалить персональную скидку пользователя

    Args:
        telegram_id: Telegram ID пользователя
        deleted_by: Telegram ID администратора, удалившего скидку

    Returns:
        True если успешно, False в случае ошибки
    """
    from database.subscriptions import _log_audit_event_atomic

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

async def is_vip_user(telegram_id: int, conn: Optional[asyncpg.Connection] = None) -> bool:
    """Проверить, является ли пользователь VIP
    
    Args:
        telegram_id: Telegram ID пользователя
        conn: Опциональное соединение (если передано — используется оно, без pool.acquire)
    
    Returns:
        True если пользователь VIP, False иначе
    """
    if conn is not None:
        row = await conn.fetchrow(
            "SELECT telegram_id FROM vip_users WHERE telegram_id = $1",
            telegram_id
        )
        return row is not None
    pool = await get_pool()
    async with pool.acquire() as acquired:
        row = await acquired.fetchrow(
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
    from database.subscriptions import _log_audit_event_atomic

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
    from database.subscriptions import _log_audit_event_atomic

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
