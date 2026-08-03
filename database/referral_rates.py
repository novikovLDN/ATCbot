"""Процент реферального кешбэка: тир, floor и админский фикс.

ЧТО ЗДЕСЬ
    Всё, что отвечает на вопрос «сколько процентов положено этому партнёру»:
    прогрессивная шкала по числу оплативших, индивидуальный фиксированный
    процент (ставит админ) и чистые функции расчёта уровня.

ПОРЯДОК ПРИОРИТЕТОВ — ЕДИНСТВЕННОЕ, ЧТО ЗДЕСЬ ВАЖНО
    1. cashback_fixed_percent — админский override, замещает всё целиком,
       в том числе в меньшую сторону (штраф).
    2. Иначе max(тир по оплатившим, cashback_floor_percent).

    Итог всегда берут через get_effective_cashback_percent. Считать процент
    напрямую от количества рефералов — ошибка: индивидуальные условия молча
    пропадут, и пользователь получит не то, что ему обещали.

ПОЧЕМУ ОТДЕЛЬНО
    Ставку правят чаще всего остального в рефералке, и правка не должна
    ехать посреди SQL привязки или начисления денег.

ЧТО ЛЕГКО СЛОМАТЬ
    Здесь ДВЕ разные шкалы, и это не дубликат:
    get_referral_cashback_percent даёт старую шкалу 10/25/45 по данным
    referral_rewards, а calculate_referral_level — шкалу «Круга
    Амбассадоров» 10/20/30/40/45 по числу приглашённых. Сведение их в одну
    поменяет и начисления, и то, что видит пользователь.
"""
import asyncpg
import logging
from typing import Any, Dict, Optional

import database.core as _core
from database.core import get_pool, safe_int

logger = logging.getLogger(__name__)


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
    if not _core.DB_READY:
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


async def get_cashback_fixed_percent(telegram_id: int) -> Optional[int]:
    """Прочитать admin-managed fixed %.

    Возвращает int 0..100 если фикс установлен, None если выключен
    (обычная логика тир + floor).
    """
    if not _core.DB_READY:
        return None
    pool = await get_pool()
    if pool is None:
        return None
    async with pool.acquire() as conn:
        val = await conn.fetchval(
            "SELECT cashback_fixed_percent FROM users WHERE telegram_id = $1",
            telegram_id,
        )
        if val is None:
            return None
        try:
            return int(val)
        except (TypeError, ValueError):
            return None


async def set_cashback_fixed_percent(telegram_id: int, percent: int) -> bool:
    """Установить/обновить admin-managed fixed %. 0..100."""
    if not _core.DB_READY:
        return False
    if not (0 <= percent <= 100):
        raise ValueError(f"percent must be in [0, 100], got {percent}")
    pool = await get_pool()
    if pool is None:
        return False
    async with pool.acquire() as conn:
        res = await conn.execute(
            "UPDATE users SET cashback_fixed_percent = $1 WHERE telegram_id = $2",
            percent, telegram_id,
        )
        return res.startswith("UPDATE ") and res != "UPDATE 0"


async def clear_cashback_fixed_percent(telegram_id: int) -> bool:
    """Выключить фикс. После этого юзер возвращается к обычной
    логике (тир + grandfather-floor)."""
    if not _core.DB_READY:
        return False
    pool = await get_pool()
    if pool is None:
        return False
    async with pool.acquire() as conn:
        res = await conn.execute(
            "UPDATE users SET cashback_fixed_percent = NULL WHERE telegram_id = $1",
            telegram_id,
        )
        return res.startswith("UPDATE ") and res != "UPDATE 0"


async def get_effective_cashback_percent(telegram_id: int) -> int:
    """ЭФФЕКТИВНЫЙ процент кешбэка — то, что реально применяется.

    Приоритет:
      1. cashback_fixed_percent (admin-managed override) — если NOT NULL,
         жёстко замещает всё: и тир, и floor.
      2. Иначе — max(тир по оплатившим рефералам, cashback_floor_percent).
         Тир вычисляется через get_referral_cashback_percent.

    Используется везде, где принимается решение о размере кешбэка
    (начисление, отображение юзеру, ответы API).
    """
    fixed = await get_cashback_fixed_percent(telegram_id)
    if fixed is not None:
        return fixed
    # Обычная логика: тир по оплатившим + floor
    tier = await get_referral_cashback_percent(telegram_id)
    pool = await get_pool()
    if pool is None:
        return tier
    try:
        async with pool.acquire() as conn:
            floor = await conn.fetchval(
                "SELECT cashback_floor_percent FROM users WHERE telegram_id = $1",
                telegram_id,
            )
        if floor is not None and int(floor) > tier:
            return int(floor)
    except Exception as e:
        logger.warning("get_effective_cashback_percent floor lookup failed: %s", e)
    return tier


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


def calculate_referral_level(total_referrals: int) -> Dict[str, Any]:
    """
    Рассчитать уровень реферала СТРОГО на основе total_referrals.

    ⚠️ ВАЖНО: Уровень определяется СТРОГО по total_referrals.
    НЕ используется active_paid_referrals, rewards, revenue.

    Пороги «Круга Амбассадоров» (см. LOYALTY_TIERS в app/constants/loyalty.py):
    - 0-24:   Проводник  (10%)
    - 25-49:  Хранитель  (20%)
    - 50-74:  Инсайдер   (30%)
    - 75-99:  Лидер      (40%)
    - 100+:   Амбассадор (45%, фиксируется навсегда)

    Args:
        total_referrals: Общее количество оплативших рефералов

    Returns:
        {
            "current_level_name": str,
            "cashback_percent": int,
            "next_level_name": Optional[str],
            "remaining_connections": int
        }
    """
    # Структура уровней: соответствует LOYALTY_TIERS из app/constants/loyalty.py
    REFERRAL_LEVELS = [
        {"name": "Амбассадор", "threshold": 100, "cashback": 45},
        {"name": "Лидер",      "threshold": 75,  "cashback": 40},
        {"name": "Инсайдер",   "threshold": 50,  "cashback": 30},
        {"name": "Хранитель",  "threshold": 25,  "cashback": 20},
        {"name": "Проводник",  "threshold": 0,   "cashback": 10},
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
        current_level = {"name": "Проводник", "threshold": 0, "cashback": 10}
    
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
