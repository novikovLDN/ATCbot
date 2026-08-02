"""Реферальная программа: коды, привязка, уровни и кешбэк.

ЧТО ЗДЕСЬ ЕСТЬ
    Генерация и поиск реферального кода, привязка приглашённого к
    пригласившему, подсчёт уровней, расчёт процента кешбэка и начисление
    вознаграждения за покупку приглашённого.

КАК УСТРОЕНА ПРИВЯЗКА
    Связь «кто кого привёл» ставится один раз при регистрации и не
    переписывается: иначе пользователь мог бы менять пригласившего и
    перераспределять чужой кешбэк. Реферал считается активным только после
    первой оплаты — до этого он не влияет ни на уровень, ни на выплаты.

ПРОЦЕНТ КЕШБЭКА
    Базовый процент растёт от числа приведённых, но у пользователя может
    стоять индивидуальный фиксированный процент. Итоговое значение всегда
    берётся через get_effective_cashback_percent: она учитывает и то и
    другое. Считать процент напрямую от количества рефералов — ошибка,
    из-за неё индивидуальные условия молча игнорируются.

ПОЧЕМУ ОТДЕЛЬНЫМ ФАЙЛОМ
    Выделено из database/users.py, где рефералка занимала больше половины
    файла и мешалась с балансом, фермой и выводом средств.
"""
import asyncpg
import base64
import hashlib
import logging
import random
import string
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import config
import database.core as _core
from database.core import get_pool, _to_db_utc, _from_db_utc, safe_int

logger = logging.getLogger(__name__)


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
        referral_code = generate_referral_code(telegram_id)

        # RETURNING distinguishes a real INSERT from ON CONFLICT DO NOTHING —
        # we only fire user:registered when a new row actually appeared, so
        # the dashboard counter doesn't tick on a return-visit /start.
        inserted_id = await conn.fetchval(
            """INSERT INTO users (telegram_id, username, language, referral_code)
               VALUES ($1, $2, $3, $4)
               ON CONFLICT (telegram_id) DO NOTHING
               RETURNING telegram_id""",
            telegram_id, username, language, referral_code
        )

        # If user already existed (ON CONFLICT DO NOTHING), ensure referral_code is set.
        # Reuse the same connection — no extra pool.acquire().
        await conn.execute(
            "UPDATE users SET referral_code = $1 WHERE telegram_id = $2 AND referral_code IS NULL",
            referral_code, telegram_id
        )

    if inserted_id is not None:
        try:
            from app.events import bus
            bus.publish({
                "type": "user:registered",
                "telegram_id": telegram_id,
                "username": username,
            })
        except Exception:
            pass


async def get_user_referral_code(telegram_id: int) -> Optional[str]:
    """Get the opaque referral_code for a user, generating one if missing."""
    if not _core.DB_READY:
        return None
    pool = await get_pool()
    if pool is None:
        return None
    try:
        async with pool.acquire() as conn:
            code = await conn.fetchval(
                "SELECT referral_code FROM users WHERE telegram_id = $1",
                telegram_id,
            )
            if code:
                return code
            # Generate and persist if missing
            code = generate_referral_code(telegram_id)
            await conn.execute(
                "UPDATE users SET referral_code = $1 WHERE telegram_id = $2 AND referral_code IS NULL",
                code, telegram_id,
            )
            return code
    except Exception as e:
        logger.warning("get_user_referral_code error: %s", type(e).__name__)
        return None


async def find_user_by_referral_code(referral_code: str) -> Optional[Dict[str, Any]]:
    """Найти пользователя по referral_code"""
    if not _core.DB_READY:
        logger.warning("DB not ready (degraded mode), find_user_by_referral_code skipped")
        return None
    
    pool = await get_pool()
    if pool is None:
        return None
    
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM users WHERE referral_code = $1", referral_code
            )
            return dict(row) if row else None
    except (asyncpg.UndefinedTableError, asyncpg.PostgresError) as e:
        logger.warning(f"users table missing or referral_code column missing — skipping: {e}")
        return None
    except Exception as e:
        logger.warning(f"Error finding user by referral code: {e}")
        return None


async def register_referral(referrer_user_id: int, referred_user_id: int) -> bool:
    """
    Зарегистрировать реферала
    
    Args:
        referrer_user_id: Telegram ID реферера
        referred_user_id: Telegram ID приглашенного пользователя
    
    Returns:
        True если регистрация успешна, False если уже зарегистрирован или ошибка
    """
    if not _core.DB_READY:
        logger.warning("DB not ready (degraded mode), register_referral skipped")
        return False
    
    # Запрет self-referral
    if referrer_user_id == referred_user_id:
        logger.warning(
            f"REFERRAL_SELF_ATTEMPT [user_id={referrer_user_id}, "
            f"referrer_id={referrer_user_id}, referred_id={referred_user_id}]"
        )
        return False
    
    pool = await get_pool()
    if pool is None:
        return False
    
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
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

                # Обновляем referrer_id у пользователя (IMMUTABLE - устанавливается только один раз)
                # Также обновляем referred_by для обратной совместимости
                # DO NOT use referred_at - column doesn't exist in schema
                result = await conn.execute(
                    """UPDATE users
                       SET referrer_id = $1, referred_by = $1
                       WHERE telegram_id = $2
                       AND referrer_id IS NULL
                       AND referred_by IS NULL""",
                    referrer_user_id, referred_user_id
                )

                # Анти-петля: проверяем ПОСЛЕ INSERT — не стал ли реферер одновременно нашим рефералом.
                # Защищает от гонки A→B / B→A при одновременных /start командах.
                referrer_row = await conn.fetchrow(
                    "SELECT referrer_id, referred_by FROM users WHERE telegram_id = $1",
                    referrer_user_id
                )
                if referrer_row:
                    ref_of_referrer = referrer_row.get("referrer_id") or referrer_row.get("referred_by")
                    if ref_of_referrer == referred_user_id:
                        logger.warning(
                            f"REFERRAL_LOOP_ABORTED [referrer={referrer_user_id}, referred={referred_user_id}]"
                        )
                        raise Exception("referral_loop_detected")
            
            # Verify that referrer_id was actually saved
            if result == "UPDATE 1":
                # Double-check by reading back
                saved_user = await conn.fetchrow(
                    "SELECT referrer_id, referred_by FROM users WHERE telegram_id = $1",
                    referred_user_id
                )
                if saved_user and (saved_user.get("referrer_id") == referrer_user_id or saved_user.get("referred_by") == referrer_user_id):
                    logger.info(
                        f"REFERRAL_SAVED [referrer={referrer_user_id}, referred={referred_user_id}, "
                        f"referrer_id_persisted=True]"
                    )
                    logger.info(f"REFERRAL_REGISTERED [referrer={referrer_user_id}, referred={referred_user_id}]")
                    return True
                else:
                    logger.error(
                        f"REFERRAL_SAVE_FAILED [referrer={referrer_user_id}, referred={referred_user_id}, "
                        f"referrer_id_not_persisted]"
                    )
                    return False
            else:
                # UPDATE 0 means referrer_id was already set (idempotent - this is OK)
                # Check if it matches expected referrer
                existing_user = await conn.fetchrow(
                    "SELECT referrer_id, referred_by FROM users WHERE telegram_id = $1",
                    referred_user_id
                )
                if existing_user:
                    existing_referrer = existing_user.get("referrer_id") or existing_user.get("referred_by")
                    if existing_referrer == referrer_user_id:
                        logger.debug(
                            f"REFERRAL_ALREADY_EXISTS [referrer={referrer_user_id}, referred={referred_user_id}, "
                            f"referrer_id_already_set]"
                        )
                        return False  # Already registered with same referrer (idempotent)
                    else:
                        logger.warning(
                            f"REFERRAL_CONFLICT [referrer={referrer_user_id}, referred={referred_user_id}, "
                            f"existing_referrer={existing_referrer}]"
                        )
                        return False  # Different referrer already set (immutable)
                return False
    except (asyncpg.UndefinedTableError, asyncpg.PostgresError) as e:
        logger.warning(f"referrals or users table missing or inaccessible — skipping referral registration: {e}")
        return False
    except Exception as e:
        logger.exception(f"Error registering referral: referrer_id={referrer_user_id}, referred_id={referred_user_id}")
        return False


async def mark_referral_active(referred_user_id: int, conn: Optional[asyncpg.Connection] = None) -> bool:
    """
    Пометить реферала как активного (активировал trial или подписку).
    
    Это обновляет запись в referrals, чтобы реферал считался активным.
    Вызывается при активации trial или первой подписки.
    
    Args:
        referred_user_id: Telegram ID реферала
        conn: Соединение с БД (если None, создаётся новое)
    
    Returns:
        True если успешно, False иначе
    """
    if not _core.DB_READY:
        logger.warning("DB not ready (degraded mode), mark_referral_active skipped")
        return False
    
    if conn is None:
        pool = await get_pool()
        if pool is None:
            return False
        async with pool.acquire() as conn:
            return await _mark_referral_active_internal(referred_user_id, conn)
    else:
        return await _mark_referral_active_internal(referred_user_id, conn)


async def _mark_referral_active_internal(referred_user_id: int, conn: asyncpg.Connection) -> bool:
    """Internal helper for marking referral as active"""
    try:
        # Проверяем, существует ли запись о реферале
        referral_row = await conn.fetchrow(
            "SELECT referrer_user_id FROM referrals WHERE referred_user_id = $1",
            referred_user_id
        )
        
        if referral_row:
            # Запись существует - просто логируем (уже активен)
            logger.debug(f"Referral already exists: referred={referred_user_id}")
            return True
        else:
            # Записи нет - получаем referrer_id из users
            user_row = await conn.fetchrow(
                "SELECT referrer_id FROM users WHERE telegram_id = $1",
                referred_user_id
            )
            
            if not user_row or not user_row.get("referrer_id"):
                # Нет реферера - это нормально (не все пользователи приглашены)
                logger.debug(f"No referrer for user: referred={referred_user_id}")
                return False
            
            referrer_user_id = user_row["referrer_id"]
            
            # Создаем запись о реферале (если её нет)
            await conn.execute(
                """INSERT INTO referrals (referrer_user_id, referred_user_id, is_rewarded, reward_amount)
                   VALUES ($1, $2, FALSE, 0)
                   ON CONFLICT (referred_user_id) DO NOTHING""",
                referrer_user_id, referred_user_id
            )
            
            logger.info(f"REFERRAL_MARKED_ACTIVE [referrer={referrer_user_id}, referred={referred_user_id}]")
            return True
            
    except (asyncpg.UndefinedTableError, asyncpg.PostgresError) as e:
        logger.warning(f"referrals table missing or inaccessible — skipping mark_referral_active: {e}")
        return False
    except Exception as e:
        logger.exception(f"Error marking referral as active: referred_id={referred_user_id}")
        return False


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


async def process_referral_reward(
    buyer_id: int,
    purchase_id: str,
    amount_rubles: float,
    conn: asyncpg.Connection
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
    # BUSINESS LOGIC CHECKS (return structured results, do not raise):
    try:
        # 1. Получаем реферера покупателя
        user = await conn.fetchrow(
            "SELECT referrer_id, referred_by FROM users WHERE telegram_id = $1",
            buyer_id
        )
        
        if not user:
            logger.debug(f"process_referral_reward: User {buyer_id} not found")
            return {
                "success": False,
                "referrer_id": None,
                "percent": None,
                "reward_amount": None,
                "message": "User not found",
                "reason": "user_not_found"
            }
        
        # Use referrer_id, fallback to referred_by for backward compatibility
        referrer_id = user.get("referrer_id") or user.get("referred_by")
        
        if not referrer_id:
            # Пользователь не был приглашён через реферальную программу
            logger.debug(f"process_referral_reward: User {buyer_id} has no referrer")
            return {
                "success": False,
                "referrer_id": None,
                "percent": None,
                "reward_amount": None,
                "message": "No referrer",
                "reason": "no_referrer"
            }
        
        # Log referrer resolution
        logger.info(
            f"REFERRAL_RESOLVED [buyer={buyer_id}, referrer={referrer_id}, "
            f"purchase_id={purchase_id}]"
        )
        
        # 2. ЗАЩИТА ОТ САМОРЕФЕРАЛА
        if referrer_id == buyer_id:
            logger.warning(f"process_referral_reward: Self-referral detected: user {buyer_id}")
            return {
                "success": False,
                "referrer_id": referrer_id,
                "percent": None,
                "reward_amount": None,
                "message": "Self-referral detected",
                "reason": "self_referral"
            }
        
        # 3. ЗАЩИТА ОТ ПОВТОРНОГО НАЧИСЛЕНИЯ (idempotency check)
        # purchase_id теперь обязателен, проверка всегда выполняется
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
                "message": "Reward already processed for this purchase",
                "reason": "duplicate_reward"
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
        
        # Определяем процент по прогрессивной шкале «Круга Амбассадоров»
        if paid_referrals_count >= 100:
            percent = 45
        elif paid_referrals_count >= 75:
            percent = 40
        elif paid_referrals_count >= 50:
            percent = 30
        elif paid_referrals_count >= 25:
            percent = 20
        else:
            percent = 10

        # 5a. Grandfather / admin-grant floor.
        # Пользователи, попавшие под старую шкалу (Platinum=45% при 50+) при
        # миграции 059, имеют cashback_floor_percent=45 — мы не снижаем им
        # процент, даже если по новой шкале они меньше.
        floor = await conn.fetchval(
            "SELECT cashback_floor_percent FROM users WHERE telegram_id = $1",
            referrer_id,
        )
        if floor is not None and floor > percent:
            percent = floor

        # 5a-fix. ADMIN OVERRIDE: cashback_fixed_percent жёстко замещает
        # результат тира + floor. Не суммируется. Работает и в меньшую
        # сторону (напр. штраф 5%) и в большую (напр. VIP 40%).
        fixed = await conn.fetchval(
            "SELECT cashback_fixed_percent FROM users WHERE telegram_id = $1",
            referrer_id,
        )
        if fixed is not None:
            percent = int(fixed)
            logger.info(
                f"REFERRAL_CASHBACK_FIXED_OVERRIDE referrer={referrer_id} "
                f"tier_percent_would_be_after_floor=(overridden) fixed={percent}"
            )

        # Вычисляем сколько осталось до следующего уровня
        if paid_referrals_count < 25:
            next_level_threshold = 25
            referrals_needed = 25 - paid_referrals_count
        elif paid_referrals_count < 50:
            next_level_threshold = 50
            referrals_needed = 50 - paid_referrals_count
        elif paid_referrals_count < 75:
            next_level_threshold = 75
            referrals_needed = 75 - paid_referrals_count
        elif paid_referrals_count < 100:
            next_level_threshold = 100
            referrals_needed = 100 - paid_referrals_count
        else:
            next_level_threshold = None
            referrals_needed = 0
        
        # 5b. Проверяем активный множитель кешбэка (x2 промо-акция)
        # Проверяем сначала персональный множитель, затем глобальную акцию —
        # акция распространяется на ВСЕХ пользователей, не только на тех,
        # кто был подписан на момент запуска.
        cashback_multiplier = 1
        try:
            multiplier_row = await conn.fetchrow(
                """SELECT multiplier FROM user_cashback_multipliers
                   WHERE telegram_id = $1
                   AND starts_at <= NOW() AND ends_at > NOW()
                   ORDER BY multiplier DESC LIMIT 1""",
                referrer_id
            )
            if multiplier_row:
                cashback_multiplier = multiplier_row["multiplier"]
            else:
                # Fallback: проверяем глобальную акцию в cashback_promotions
                global_promo = await conn.fetchrow(
                    """SELECT multiplier FROM cashback_promotions
                       WHERE is_active = TRUE
                       AND starts_at <= NOW() AND ends_at > NOW()
                       ORDER BY multiplier DESC LIMIT 1"""
                )
                if global_promo:
                    cashback_multiplier = global_promo["multiplier"]
            if cashback_multiplier > 1:
                logger.info(
                    f"CASHBACK_MULTIPLIER_ACTIVE [referrer={referrer_id}, "
                    f"multiplier=x{cashback_multiplier}, base_percent={percent}%]"
                )
        except Exception as e:
            logger.warning(f"Failed to check cashback multiplier for {referrer_id}: {e}")

        # Применяем множитель к проценту
        effective_percent = percent * cashback_multiplier

        # 6. Рассчитываем сумму кешбэка (в копейках)
        purchase_amount_kopecks = round(amount_rubles * 100)
        reward_amount_kopecks = int(purchase_amount_kopecks * effective_percent / 100)
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
                "message": "Invalid reward amount",
                "reason": "invalid_amount"
            }
        
        # FINANCIAL OPERATIONS (raise exceptions on failure, do not catch):
        # 7. Начисляем кешбэк на баланс реферера
        # CRITICAL: advisory lock per referrer для защиты от race conditions
        # Consistent with increase_balance() locking pattern — prevents concurrent
        # balance modifications from different purchases for the same referrer
        await conn.execute(
            "SELECT pg_advisory_xact_lock($1)",
            referrer_id
        )
        
        # CRITICAL: SELECT FOR UPDATE для блокировки строки до конца транзакции
        balance_row = await conn.fetchrow(
            "SELECT balance FROM users WHERE telegram_id = $1 FOR UPDATE",
            referrer_id
        )
        
        if not balance_row:
            raise ValueError(f"Referrer {referrer_id} not found for reward")
        
        # Обновляем баланс (строка уже заблокирована FOR UPDATE)
        await conn.execute(
            "UPDATE users SET balance = balance + $1 WHERE telegram_id = $2",
            reward_amount_kopecks, referrer_id
        )
        
        # 8. Записываем транзакцию баланса
        # Если это упадет - исключение пробросится вверх, транзакция откатится
        multiplier_note = f" (x{cashback_multiplier})" if cashback_multiplier > 1 else ""
        await conn.execute(
            """INSERT INTO balance_transactions (user_id, amount, type, source, description, related_user_id)
               VALUES ($1, $2, $3, $4, $5, $6)""",
            referrer_id, reward_amount_kopecks, "cashback", "referral",
            f"Реферальный кешбэк {effective_percent}%{multiplier_note} за оплату пользователя {buyer_id}",
            buyer_id
        )
        
        # 9. Создаём запись в referral_rewards (история начислений)
        # SECURITY: ON CONFLICT предотвращает повторное начисление при race condition
        insert_result = await conn.execute(
            """INSERT INTO referral_rewards
               (referrer_id, buyer_id, purchase_id, purchase_amount, percent, reward_amount)
               VALUES ($1, $2, $3, $4, $5, $6)
               ON CONFLICT (buyer_id, purchase_id) WHERE purchase_id IS NOT NULL DO NOTHING""",
            referrer_id, buyer_id, purchase_id, purchase_amount_kopecks, effective_percent, reward_amount_kopecks
        )
        if insert_result == "INSERT 0":
            # Race condition: another concurrent transaction already inserted this reward
            logger.warning(
                f"REFERRAL_REWARD_DUPLICATE_PREVENTED: buyer_id={buyer_id}, purchase_id={purchase_id} "
                f"(concurrent insert detected, rolling back balance credit)"
            )
            raise ValueError(
                f"Duplicate referral reward prevented for buyer_id={buyer_id}, purchase_id={purchase_id}"
            )
        
        # 10. Логируем событие
        details = (
            f"Referral reward awarded: referrer={referrer_id} ({effective_percent}%"
            f"{multiplier_note}), "
            f"buyer={buyer_id}, purchase_id={purchase_id}, "
            f"purchase={amount_rubles:.2f} RUB, reward={reward_amount_rubles:.2f} RUB "
            f"({reward_amount_kopecks} kopecks), paid_referrals_count={paid_referrals_count}"
        )
        from database.subscriptions import _log_audit_event_atomic
        await _log_audit_event_atomic(
            conn,
            "referral_reward",
            referrer_id,
            buyer_id,
            details
        )
        
        logger.info(
            f"REFERRAL_REWARD_APPLIED [referrer={referrer_id}, buyer={buyer_id}, "
            f"purchase_id={purchase_id}, percent={effective_percent}%{multiplier_note}, "
            f"amount={reward_amount_rubles:.2f} RUB, paid_referrals_count={paid_referrals_count}]"
        )

        return {
            "success": True,
            "referrer_id": referrer_id,
            "percent": effective_percent,
            "reward_amount": reward_amount_rubles,
            "paid_referrals_count": paid_referrals_count,
            "next_level_threshold": next_level_threshold,
            "referrals_needed": referrals_needed,
            "message": "Reward awarded successfully"
        }
                
    except (asyncpg.UniqueViolationError, asyncpg.ForeignKeyViolationError, 
            asyncpg.NotNullViolationError, asyncpg.CheckViolationError,
            asyncpg.PostgresConnectionError, asyncpg.InterfaceError, asyncpg.TimeoutError) as e:
        # FINANCIAL ERRORS: Database constraint violations, connection issues
        # These MUST propagate to cause transaction rollback
        logger.error(
            f"process_referral_reward: Financial error (will rollback transaction): "
            f"buyer_id={buyer_id}, purchase_id={purchase_id}, error={e}"
        )
        raise  # Re-raise to cause transaction rollback
    
    except asyncpg.PostgresError as e:
        # Other database errors - also financial, must rollback
        logger.error(
            f"process_referral_reward: Database error (will rollback transaction): "
            f"buyer_id={buyer_id}, purchase_id={purchase_id}, error={e}"
        )
        raise  # Re-raise to cause transaction rollback
