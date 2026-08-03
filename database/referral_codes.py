"""Реферальные коды и привязка «кто кого привёл».

ЧТО ЗДЕСЬ
    Генерация кода, создание пользователя вместе с кодом, поиск владельца
    кода и запись связи referrer → referred.

ПОЧЕМУ ОТДЕЛЬНО
    Привязка — единственная часть рефералки, которая пишет в users.referrer_id.
    Всё остальное (проценты, витрины, начисление) только читает эту связь.
    Держать запись связи рядом с расчётом процента опасно: правка ставки
    случайно задевала бы то, что должно быть неизменяемым.

ЧТО ЛЕГКО СЛОМАТЬ
    Связь ставится РОВНО ОДИН РАЗ — UPDATE идёт с условием
    `referrer_id IS NULL AND referred_by IS NULL`. Убрать это условие значит
    разрешить пользователю переназначать пригласившего и уводить чужой кешбэк.
    Анти-петля в register_referral проверяется ПОСЛЕ INSERT внутри той же
    транзакции: только так ловится гонка A→B / B→A при одновременных /start.

    generate_referral_code детерминирован от telegram_id. Смена алгоритма
    меняет коды у всех существующих пользователей и рвёт разосланные ссылки.
"""
import asyncpg
import base64
import hashlib
import logging
from typing import Any, Dict, Optional

import database.core as _core
from database.core import get_pool

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
