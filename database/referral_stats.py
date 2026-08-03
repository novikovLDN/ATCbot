"""Витрины реферальной статистики — только чтение.

ЧТО ЗДЕСЬ
    Счётчики и агрегаты для экрана рефералки и ответов API: сколько
    приглашено, сколько из них платят, сколько кешбэка заработано, какой
    уровень и сколько до следующего.

ПОЧЕМУ ОТДЕЛЬНО
    Ни одна функция здесь ничего не пишет. Отделив чтение от записи, легко
    отвечать на вопрос «может ли этот запрос испортить деньги» — не может.

ЧТО ЛЕГКО СЛОМАТЬ
    Числа в разных функциях считаются по РАЗНЫМ источникам, и это осознанно:
    get_referral_level_info берёт оплативших из referral_rewards, а
    get_referral_statistics — уровень строго по total_invited из referrals.
    Свести их к одному запросу значит поменять цифры на экране у всех.

    get_referral_statistics поверх посчитанного уровня накладывает сначала
    grandfather-floor, потом админский фикс. Порядок обязан совпадать с
    порядком в database/referral_rates.get_effective_cashback_percent —
    иначе пользователь увидит один процент, а начислится другой.
"""
import logging
from typing import Any, Dict

import asyncpg

import database.core as _core
from database.core import get_pool, safe_int
from database.referral_rates import calculate_referral_level

logger = logging.getLogger(__name__)


async def get_referral_stats(telegram_id: int) -> Dict[str, int]:
    """
    Получить статистику рефералов для пользователя
    
    Returns:
        Словарь с ключами: total_referred, total_rewarded
    """
    if not _core.DB_READY:
        logger.warning("DB not ready (degraded mode), get_referral_stats skipped")
        return {"total_referred": 0, "total_rewarded": 0}
    
    pool = await get_pool()
    if pool is None:
        return {"total_referred": 0, "total_rewarded": 0}
    
    try:
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
    except (asyncpg.UndefinedTableError, asyncpg.PostgresError) as e:
        logger.warning(f"referrals table missing or inaccessible — skipping: {e}")
        return {"total_referred": 0, "total_rewarded": 0}
    except Exception as e:
        logger.warning(f"Error getting referral stats: {e}")
        return {"total_referred": 0, "total_rewarded": 0}


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
    if not _core.DB_READY:
        logger.warning("DB not ready (degraded mode), get_referral_level_info skipped")
        return {
            "current_level": 10,
            "referrals_count": 0,
            "paid_referrals_count": 0,
            "next_level": 25,
            "referrals_to_next": 25
        }
    
    pool = await get_pool()
    if pool is None:
        return {
            "current_level": 10,
            "referrals_count": 0,
            "paid_referrals_count": 0,
            "next_level": 25,
            "referrals_to_next": 25
        }
    
    try:
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
    except (asyncpg.UndefinedTableError, asyncpg.PostgresError) as e:
        logger.warning(f"referrals or referral_rewards table missing or inaccessible — skipping: {e}")
        return {
            "current_level": 10,
            "referrals_count": 0,
            "paid_referrals_count": 0,
            "next_level": 25,
            "referrals_to_next": 25
        }
    except Exception as e:
        logger.warning(f"Error in get_referral_level_info for partner_id={partner_id}: {e}")
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


async def get_referral_metrics(user_id: int) -> Dict[str, int]:
    """
    Получить разделённые метрики рефералов для пользователя.
    
    КРИТИЧНО:
    - total_referrals: ВСЕ приглашённые (без фильтров)
    - active_paid_referrals: Только с активной подпиской (expires_at > NOW())
    
    Args:
        user_id: Telegram ID пользователя
    
    Returns:
        {
            "total_referrals": int,  # Всего приглашено (без фильтров)
            "active_paid_referrals": int  # Активных с подпиской
        }
    """
    if not _core.DB_READY:
        return {
            "total_referrals": 0,
            "active_paid_referrals": 0
        }
    
    pool = await get_pool()
    if pool is None:
        return {
            "total_referrals": 0,
            "active_paid_referrals": 0
        }
    
    try:
        async with pool.acquire() as conn:
            # 1️⃣ Всего приглашено: ВСЕ записи из referrals
            total_referrals_val = await conn.fetchval(
                "SELECT COUNT(*) FROM referrals WHERE referrer_user_id = $1",
                user_id
            )
            total_referrals = safe_int(total_referrals_val)
            
            # 2️⃣ Активных с подпиской: только те, у кого активная подписка
            active_paid_referrals_val = await conn.fetchval(
                """SELECT COUNT(DISTINCT r.referred_user_id)
                   FROM referrals r
                   INNER JOIN subscriptions s ON s.telegram_id = r.referred_user_id
                   WHERE r.referrer_user_id = $1
                   AND s.expires_at IS NOT NULL
                   AND s.expires_at > NOW()""",
                user_id
            )
            active_paid_referrals = safe_int(active_paid_referrals_val)
            
            return {
                "total_referrals": total_referrals,
                "active_paid_referrals": active_paid_referrals
            }
    except (asyncpg.UndefinedTableError, asyncpg.PostgresError) as e:
        logger.warning(f"referrals or subscriptions table missing — skipping: {e}")
        return {
            "total_referrals": 0,
            "active_paid_referrals": 0
        }
    except Exception as e:
        logger.warning(f"Error in get_referral_metrics for user_id={user_id}: {e}")
        return {
            "total_referrals": 0,
            "active_paid_referrals": 0
        }


async def get_referral_statistics(partner_id: int) -> Dict[str, Any]:
    """
    Получить полную статистику рефералов для партнёра.
    
    НОВАЯ ЛОГИКА:
    - total_invited: Всего приглашено (из referrals, без фильтров)
    - active_paid_referrals: Активных с подпиской (expires_at > NOW())
    - Уровень рассчитывается СТРОГО по total_invited
    
    Returns:
        {
            "total_invited": int,  # Всего приглашено
            "active_paid_referrals": int,  # Активных с подпиской
            "total_cashback_earned": float,  # Общий кешбэк в рублях
            "last_activity_at": Optional[datetime],  # Последняя активность реферала
            "current_level_name": str,  # "Проводник" / "Хранитель" / "Инсайдер" / "Лидер" / "Амбассадор"
            "cashback_percent": int,  # 10, 20, 30, 40, 45
            "next_level_name": Optional[str],  # Следующий уровень или None
            "remaining_connections": int  # До следующего уровня
        }
    """
    if not _core.DB_READY:
        return {
            "total_invited": 0,
            "active_paid_referrals": 0,
            "total_cashback_earned": 0.0,
            "last_activity_at": None,
            "current_level_name": "Проводник",
            "cashback_percent": 10,
            "next_level_name": "Хранитель",
            "remaining_connections": 5
        }
    
    pool = await get_pool()
    if pool is None:
        return {
            "total_invited": 0,
            "active_paid_referrals": 0,
            "total_cashback_earned": 0.0,
            "last_activity_at": None,
            "current_level_name": "Проводник",
            "cashback_percent": 10,
            "next_level_name": "Хранитель",
            "remaining_connections": 5
        }
    
    try:
        async with pool.acquire() as conn:
            # Получаем разделённые метрики
            metrics = await get_referral_metrics(partner_id)
            total_invited = metrics["total_referrals"]
            active_paid_referrals = metrics["active_paid_referrals"]
            
            # Total cashback earned
            total_cashback_kopecks_val = await conn.fetchval(
                """SELECT COALESCE(SUM(amount), 0) 
                   FROM balance_transactions 
                   WHERE user_id = $1 AND type = 'cashback'""",
                partner_id
            )
            total_cashback_kopecks = safe_int(total_cashback_kopecks_val)
            total_cashback_earned = total_cashback_kopecks / 100.0
            
            # Last activity timestamp (последняя оплата реферала)
            last_activity_row = await conn.fetchrow(
                """SELECT MAX(r.first_paid_at) as last_activity
                   FROM referrals r
                   WHERE r.referrer_user_id = $1 AND r.first_paid_at IS NOT NULL""",
                partner_id
            )
            last_activity_at = last_activity_row.get("last_activity") if last_activity_row else None
            
            # Рассчитываем уровень СТРОГО по total_invited
            level_info = calculate_referral_level(total_invited)

            # Grandfather floor: пользователи со старой шкалой имеют
            # cashback_floor_percent=45 — показываем их как «Амбассадор» с 45%
            # и скрываем прогресс к следующему, иначе UI будет противоречить
            # реальному проценту начисления.
            floor_pct = await conn.fetchval(
                "SELECT cashback_floor_percent FROM users WHERE telegram_id = $1",
                partner_id,
            )
            if floor_pct is not None and floor_pct > level_info["cashback_percent"]:
                # Маппим floor → тир: 45 = Амбассадор, 40 = Лидер, и т.д.
                from app.constants.loyalty import LOYALTY_TIERS
                bumped_tier = None
                for lo, _hi, name, pct in LOYALTY_TIERS:
                    if pct == floor_pct:
                        bumped_tier = name
                        break
                if bumped_tier:
                    level_info = {
                        "current_level_name": bumped_tier,
                        "cashback_percent": floor_pct,
                        "next_level_name": None,
                        "remaining_connections": 0,
                    }

            # ADMIN OVERRIDE: cashback_fixed_percent жёстко замещает всё
            # (и тир, и floor). Если admin поставил fix, показываем этот %
            # с пометкой (флаг is_fixed=True). Юзер видит именно этот
            # процент — тот же, что реально начисляется в
            # process_referral_reward. Название уровня не меняем — оно
            # отражает реальный прогресс по рефералам.
            fixed_pct = await conn.fetchval(
                "SELECT cashback_fixed_percent FROM users WHERE telegram_id = $1",
                partner_id,
            )
            is_fixed = False
            if fixed_pct is not None:
                level_info = {
                    "current_level_name": level_info["current_level_name"],
                    "cashback_percent": int(fixed_pct),
                    "next_level_name": None,
                    "remaining_connections": 0,
                }
                is_fixed = True

            # Debug логирование
            logger.info(
                f"REF_STATS user={partner_id} "
                f"total={total_invited} "
                f"active_paid={active_paid_referrals} "
                f"level={level_info['current_level_name']} "
                f"remaining={level_info['remaining_connections']} "
                f"floor={floor_pct}"
            )

            return {
                "total_invited": total_invited,
                "active_paid_referrals": active_paid_referrals,
                "total_cashback_earned": total_cashback_earned,
                "last_activity_at": last_activity_at,
                "current_level_name": level_info["current_level_name"],
                "cashback_percent": level_info["cashback_percent"],
                "next_level_name": level_info["next_level_name"],
                "remaining_connections": level_info["remaining_connections"],
                "is_fixed_percent": is_fixed,
            }
    except Exception as e:
        logger.exception(f"Error getting referral statistics for partner_id={partner_id}: {e}")
        return {
            "total_invited": 0,
            "active_paid_referrals": 0,
            "total_cashback_earned": 0.0,
            "last_activity_at": None,
            "current_level_name": "Проводник",
            "cashback_percent": 10,
            "next_level_name": "Хранитель",
            "remaining_connections": 5,
            "is_fixed_percent": False,
        }
