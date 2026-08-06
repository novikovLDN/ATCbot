"""Промокоды: выдача, валидация, атомарное потребление, статистика.

Выделено из database/subscriptions.py, который разросся до 5100 строк.
Группа самодостаточная: снаружи ей нужны только пул соединений и helper
приведения времени.
"""
import asyncpg
import logging
import random
import string
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import database.core as _core
from database.core import get_pool, _to_db_utc
from app.utils.security import mask_secret

# Промокод — предъявительский код на скидку: max_uses обычно больше единицы,
# и кто прочитал код в логе, тот получил рабочую скидку. Маскируем там, где
# код на момент записи РАБОТАЕТ. Там, где он уже мёртв (исчерпан, выключен,
# не найден), маска не прячет ничего, а разбор ломает — код оставлен целиком.

logger = logging.getLogger(__name__)

# Условие «промокод годен»: активен, не удалён, не истёк и не исчерпан.
# Держится одной строкой, потому что повторяется в нескольких запросах.
_ACTIVE_PROMO_WHERE = (
    "is_active = true AND deleted_at IS NULL "
    "AND (expires_at IS NULL OR expires_at > NOW()) "
    "AND (max_uses IS NULL OR used_count < max_uses)"
)


async def get_promo_code(code: str) -> Optional[Dict[str, Any]]:
    """Получить любой промокод по коду (может быть неактивным). Для валидации используйте get_active_promo_by_code."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM promo_codes WHERE UPPER(code) = UPPER($1) ORDER BY created_at DESC LIMIT 1",
            code
        )
        return dict(row) if row else None


async def get_active_promo_by_code(conn, code: str) -> Optional[Dict[str, Any]]:
    """Получить активный промокод по коду (is_active, !deleted, !expired, !exhausted). Требует conn."""
    has_deleted_at = await conn.fetchval(
        "SELECT 1 FROM information_schema.columns WHERE table_name = 'promo_codes' AND column_name = 'deleted_at'"
    )
    if has_deleted_at:
        row = await conn.fetchrow(
            f"""
            SELECT * FROM promo_codes
            WHERE UPPER(code) = UPPER($1) AND {_ACTIVE_PROMO_WHERE}
            ORDER BY id DESC NULLS LAST, created_at DESC
            LIMIT 1
            """,
            code
        )
    else:
        row = await conn.fetchrow(
            """
            SELECT * FROM promo_codes
            WHERE UPPER(code) = UPPER($1)
              AND is_active = true
              AND (expires_at IS NULL OR expires_at > NOW())
              AND (max_uses IS NULL OR used_count < max_uses)
            ORDER BY created_at DESC
            LIMIT 1
            """,
            code
        )
    return dict(row) if row else None


async def has_active_promo(code: str) -> bool:
    """Проверить, есть ли активный промокод с таким кодом"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        promo = await get_active_promo_by_code(conn, code)
        return promo is not None


async def check_promo_code_valid(code: str) -> Optional[Dict[str, Any]]:
    """Проверить, валиден ли промокод и вернуть его данные (только активный)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await get_active_promo_by_code(conn, code)


async def log_promo_code_usage(
    promo_code: str,
    telegram_id: int,
    tariff: str,
    discount_percent: int,
    price_before: int,
    price_after: int
):
    """Записать использование промокода в лог"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO promo_usage_logs 
            (promo_code, telegram_id, tariff, discount_percent, price_before, price_after)
            VALUES ($1, $2, $3, $4, $5, $6)
        """, promo_code.upper(), telegram_id, tariff, discount_percent, price_before, price_after)


async def get_promo_stats() -> list:
    """
    Получить статистику по промокодам через SQL-агрегацию.
    Без кеширования. Активный промокод: is_active, !deleted, !expired, !exhausted.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        has_id = await conn.fetchval(
            "SELECT 1 FROM information_schema.columns WHERE table_name = 'promo_codes' AND column_name = 'id'"
        )
        if not has_id:
            rows = await conn.fetch("""
                SELECT code, discount_percent, max_uses, used_count, is_active, expires_at, created_at, created_by
                FROM promo_codes ORDER BY code
            """)
            return [dict(row) for row in rows]
        rows = await conn.fetch("""
            SELECT id, code, discount_percent, max_uses, used_count, is_active, deleted_at,
                   expires_at, created_at, created_by,
                   (is_active = true AND deleted_at IS NULL
                    AND (expires_at IS NULL OR expires_at > NOW())
                    AND (max_uses IS NULL OR used_count < max_uses)) AS is_effective_active
            FROM promo_codes
            ORDER BY code, created_at DESC
        """)
        return [dict(row) for row in rows]


def generate_promo_code(length: int = 6) -> str:
    """Генерировать случайный промокод из заглавных букв A-Z"""
    return ''.join(random.choices(string.ascii_uppercase, k=length))


async def create_promocode_atomic(
    code: str,
    discount_percent: int,
    duration_seconds: int,
    max_uses: int,
    created_by: int
) -> Optional[int]:
    """
    Создать промокод атомарно. Разрешает пересоздание, если предыдущий удалён/истёк/исчерпан.
    Блокирует создание, если активный промокод с таким кодом уже существует.
    
    Returns:
        ID созданного промокода или None при конфликте/ошибке
    """
    if not _core.DB_READY:
        logger.warning("DB not ready, create_promocode_atomic skipped")
        return None
    pool = await get_pool()
    if pool is None:
        return None

    code_normalized = code.upper().strip()
    if len(code_normalized) < 3 or len(code_normalized) > 32:
        logger.error(f"Invalid promocode length: {len(code_normalized)}")
        return None
    if not all(c.isalnum() for c in code_normalized):
        # Отбракованная строка промокодом так и не стала — прятать нечего.
        logger.error(f"PROMO_INVALID_CHARS code={code_normalized}")
        return None
    if discount_percent < 0 or discount_percent > 100:
        logger.error(f"Invalid discount_percent: {discount_percent}")
        return None
    if max_uses <= 0:
        logger.error(f"Invalid max_uses: {max_uses}")
        return None
    if duration_seconds <= 0:
        logger.error(f"Invalid duration_seconds: {duration_seconds}")
        return None

    expires_at = datetime.now(timezone.utc) + timedelta(seconds=duration_seconds)

    async with pool.acquire() as conn:
        async with conn.transaction():
            try:
                has_id = await conn.fetchval(
                    "SELECT 1 FROM information_schema.columns WHERE table_name = 'promo_codes' AND column_name = 'id'"
                )
                if has_id:
                    conflict = await conn.fetchrow(
                        """
                        SELECT id FROM promo_codes
                        WHERE UPPER(code) = UPPER($1)
                          AND is_active = true AND deleted_at IS NULL
                          AND (expires_at IS NULL OR expires_at > NOW())
                          AND (max_uses IS NULL OR used_count < max_uses)
                        LIMIT 1
                        """,
                        code_normalized
                    )
                    if conflict:
                        logger.warning(
                            f"PROMO_CONFLICT code={mask_secret(code_normalized)} "
                            f"active promo exists id={conflict['id']}"
                        )
                        return None

                if has_id:
                    row = await conn.fetchrow(
                        """
                        INSERT INTO promo_codes
                        (code, discount_percent, duration_seconds, max_uses, expires_at, created_by, is_active, used_count)
                        VALUES ($1, $2, $3, $4, $5, $6, TRUE, 0)
                        RETURNING id
                        """,
                        code_normalized, discount_percent, duration_seconds, max_uses, _to_db_utc(expires_at), created_by
                    )
                else:
                    row = await conn.fetchrow(
                        """
                        INSERT INTO promo_codes
                        (code, discount_percent, duration_seconds, max_uses, expires_at, created_by, is_active)
                        VALUES ($1, $2, $3, $4, $5, $6, TRUE)
                        RETURNING code
                        """,
                        code_normalized, discount_percent, duration_seconds, max_uses, _to_db_utc(expires_at), created_by
                    )
                if not row:
                    return None

                promo_id = row.get("id") or row.get("code")
                prev_count = await conn.fetchval(
                    "SELECT COUNT(*) FROM promo_codes WHERE UPPER(code) = UPPER($1)",
                    code_normalized
                ) or 0
                is_recreate = has_id and int(prev_count) > 1
                if is_recreate:
                    logger.info(
                        f"PROMO_RECREATED code={mask_secret(code_normalized)} id={promo_id} discount={discount_percent}% "
                        f"max_uses={max_uses} created_by={created_by}"
                    )
                else:
                    logger.info(
                        f"PROMO_CREATED code={mask_secret(code_normalized)} id={promo_id} discount={discount_percent}% "
                        f"max_uses={max_uses} expires_at={expires_at} created_by={created_by}"
                    )
                return int(promo_id) if promo_id else None
            except asyncpg.UniqueViolationError:
                logger.warning(
                    f"PROMO_UNIQUE_VIOLATION code={mask_secret(code_normalized)} "
                    f"— активный промокод с таким кодом уже есть"
                )
                return None
            except Exception as e:
                logger.exception(f"PROMO_CREATE_ERROR code={mask_secret(code_normalized)}: {e}")
                return None


async def reactivate_promocode(promo_id: Optional[int] = None, code: Optional[str] = None) -> bool:
    """Re-enable a previously deactivated promocode: UPDATE is_active=true,
    deleted_at=NULL. Counterpart of deactivate_promocode."""
    if not _core.DB_READY:
        return False
    pool = await get_pool()
    if pool is None:
        return False
    async with pool.acquire() as conn:
        has_deleted_at = await conn.fetchval(
            "SELECT 1 FROM information_schema.columns WHERE table_name = 'promo_codes' AND column_name = 'deleted_at'"
        )
        if promo_id is not None:
            if has_deleted_at:
                row = await conn.fetchrow(
                    "UPDATE promo_codes SET is_active = true, deleted_at = NULL WHERE id = $1 RETURNING code",
                    promo_id,
                )
            else:
                row = await conn.fetchrow(
                    "UPDATE promo_codes SET is_active = true WHERE id = $1 RETURNING code",
                    promo_id,
                )
        elif code:
            code_n = code.upper().strip()
            if has_deleted_at:
                row = await conn.fetchrow(
                    "UPDATE promo_codes SET is_active = true, deleted_at = NULL WHERE UPPER(code) = UPPER($1) RETURNING code",
                    code_n,
                )
            else:
                row = await conn.fetchrow(
                    "UPDATE promo_codes SET is_active = true WHERE UPPER(code) = UPPER($1) RETURNING code",
                    code_n,
                )
        else:
            return False
        if row:
            logger.info("PROMO_REACTIVATED code=%s", mask_secret(row.get("code")))
            return True
        return False


async def deactivate_promocode(promo_id: Optional[int] = None, code: Optional[str] = None) -> bool:
    """
    Деактивировать промокод: UPDATE is_active=false, deleted_at=now().
    Передайте promo_id (предпочтительно) или code. Логирует PROMO_DEACTIVATED.
    """
    if not _core.DB_READY:
        return False
    pool = await get_pool()
    if pool is None:
        return False
    async with pool.acquire() as conn:
        has_deleted_at = await conn.fetchval(
            "SELECT 1 FROM information_schema.columns WHERE table_name = 'promo_codes' AND column_name = 'deleted_at'"
        )
        if promo_id is not None:
            if has_deleted_at:
                row = await conn.fetchrow(
                    "UPDATE promo_codes SET is_active = false, deleted_at = NOW() WHERE id = $1 RETURNING code",
                    promo_id
                )
            else:
                row = await conn.fetchrow(
                    "UPDATE promo_codes SET is_active = false WHERE id = $1 RETURNING code",
                    promo_id
                )
        elif code:
            code_n = code.upper().strip()
            if has_deleted_at:
                row = await conn.fetchrow(
                    "UPDATE promo_codes SET is_active = false, deleted_at = NOW() WHERE UPPER(code) = UPPER($1) RETURNING code",
                    code_n
                )
            else:
                row = await conn.fetchrow(
                    "UPDATE promo_codes SET is_active = false WHERE UPPER(code) = UPPER($1) RETURNING code",
                    code_n
                )
        else:
            return False
        if row:
            # Код НЕ маскируем: его только что выключили, он больше не работает,
            # а запись существует ровно чтобы ответить «какой именно погасили».
            logger.info(f"PROMO_DEACTIVATED code={row['code']} id={promo_id or 'N/A'}")
            return True
        return False


async def _consume_promo_in_transaction(
    conn, code: str, telegram_id: int, purchase_id: Optional[str] = None
) -> None:
    """
    Потребление промокода внутри транзакции: UPDATE ... WHERE id = ? AND used_count < max_uses RETURNING *
    Если строк не возвращено — промокод исчерпан. Логирует PROMO_USAGE_INCREMENTED.
    Raises ValueError при ошибке.
    """
    code_normalized = code.upper().strip()
    promo = await get_active_promo_by_code(conn, code_normalized)
    if not promo:
        ctx = f" purchase_id={purchase_id}" if purchase_id else ""
        raise ValueError(f"PROMO_INVALID_OR_EXPIRED: code={code_normalized}{ctx}")

    promo_id = promo.get("id")
    has_id = promo_id is not None
    if has_id:
        updated = await conn.fetchrow(
            """
            UPDATE promo_codes
            SET used_count = used_count + 1
            WHERE id = $1 AND (max_uses IS NULL OR used_count < max_uses)
            RETURNING *
            """,
            promo_id
        )
    else:
        updated = await conn.fetchrow(
            """
            UPDATE promo_codes
            SET used_count = used_count + 1
            WHERE UPPER(code) = UPPER($1)
              AND is_active = true
              AND (expires_at IS NULL OR expires_at > NOW())
              AND (max_uses IS NULL OR used_count < max_uses)
            RETURNING *
            """,
            code_normalized
        )
    if not updated:
        ctx = f" purchase_id={purchase_id}" if purchase_id else ""
        # Код НЕ маскируем: сюда попадают только исчерпанные (used_count >=
        # max_uses), то есть уже нерабочие коды. Маска не спрятала бы ничего,
        # а «какая кампания кончилась» — единственный смысл этой записи.
        logger.warning(f"PROMO_EXHAUSTED code={code_normalized} user={telegram_id}{ctx}")
        raise ValueError("PROMO_EXHAUSTED")

    used = updated["used_count"]
    max_uses_val = updated["max_uses"]
    logger.info(
        f"PROMO_USAGE_INCREMENTED code={mask_secret(code_normalized)} id={promo_id or 'N/A'} user={telegram_id} "
        f"used_count={used}/{max_uses_val if max_uses_val else 'unlimited'}"
    )


async def validate_promocode_atomic(code: str) -> Dict[str, Any]:
    """
    Валидация промокода без инкремента счетчика.
    Использует определение активного промо: is_active, !deleted, !expired, !exhausted.
    
    Returns:
        {"success": bool, "promo_data": Optional[Dict], "error": Optional[str]}
    """
    if not _core.DB_READY:
        return {"success": False, "promo_data": None, "error": "invalid"}
    pool = await get_pool()
    if pool is None:
        return {"success": False, "promo_data": None, "error": "invalid"}
    code_normalized = code.upper().strip()
    async with pool.acquire() as conn:
        try:
            promo = await get_active_promo_by_code(conn, code_normalized)
            if not promo:
                return {"success": False, "promo_data": None, "error": "invalid"}
            logger.info(
                f"PROMOCODE_VALIDATED code={mask_secret(code_normalized)} "
                f"used_count={promo.get('used_count', 0)}/{promo.get('max_uses') or 'unlimited'}"
            )
            return {"success": True, "promo_data": dict(promo), "error": None}
        except Exception as e:
            logger.exception(f"PROMO_VALIDATE_ERROR code={mask_secret(code_normalized)}: {e}")
            return {"success": False, "promo_data": None, "error": "invalid"}


async def consume_promocode_atomic(code: str, telegram_id: int) -> None:
    """
    Потребление промокода — инкремент счетчика использований.
    Вызывается ТОЛЬКО при успешной оплате.
    
    CRITICAL: Эта функция должна вызываться только после успешной оплаты.
    
    Raises:
        ValueError: Если промокод не найден, уже исчерпан или невалиден
    """
    if not _core.DB_READY:
        raise ValueError("PROMO_DB_NOT_READY")
    
    pool = await get_pool()
    if pool is None:
        raise ValueError("PROMO_DB_NOT_READY")
    
    code_normalized = code.upper().strip()
    
    async with pool.acquire() as conn:
        async with conn.transaction():
            try:
                # CRITICAL: Advisory lock на код для защиты от race conditions
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))",
                    code_normalized
                )
                
                # CRITICAL: SELECT FOR UPDATE для блокировки строки
                row = await conn.fetchrow(
                    """
                    SELECT *
                    FROM promo_codes
                    WHERE code = $1
                    FOR UPDATE
                    """,
                    code_normalized
                )
                
                if not row:
                    raise ValueError("PROMO_NOT_FOUND")
                
                # Проверяем активность
                if not row.get("is_active", False):
                    raise ValueError("PROMO_INACTIVE")
                
                # Проверяем срок действия
                expires_at = row.get("expires_at")
                if expires_at:
                    expired_check = await conn.fetchval(
                        "SELECT expires_at < NOW() FROM promo_codes WHERE code = $1",
                        row["code"]
                    )
                    if expired_check:
                        await conn.execute(
                            "UPDATE promo_codes SET is_active = FALSE WHERE code = $1",
                            row["code"]
                        )
                        raise ValueError("PROMO_EXPIRED")
                
                # Проверяем лимит использований
                used_count = row.get("used_count", 0)
                max_uses = row.get("max_uses")
                if max_uses is not None and used_count >= max_uses:
                    raise ValueError("PROMO_ALREADY_CONSUMED")
                
                # SUCCESS — увеличиваем счетчик использований атомарно
                await conn.execute(
                    """
                    UPDATE promo_codes
                    SET used_count = used_count + 1
                    WHERE code = $1
                    """,
                    row["code"]
                )
                
                # Получаем обновленное значение used_count
                updated_row = await conn.fetchrow(
                    "SELECT used_count, max_uses FROM promo_codes WHERE code = $1",
                    row["code"]
                )
                new_count = updated_row["used_count"]
                
                # Автоматическая деактивация при достижении лимита
                if max_uses is not None and new_count >= max_uses:
                    await conn.execute(
                        """
                        UPDATE promo_codes
                        SET is_active = FALSE
                        WHERE code = $1
                        AND used_count >= max_uses
                        """,
                        row["code"]
                    )
                
                logger.info(
                    f"PROMOCODE_CONSUMED code={mask_secret(code_normalized)} user={telegram_id} "
                    f"used_count={new_count}/{max_uses if max_uses else 'unlimited'}"
                )
                
            except ValueError:
                # Пробрасываем ValueError как есть
                raise
            except Exception as e:
                logger.exception(f"PROMO_CONSUME_ERROR code={mask_secret(code_normalized)}: {e}")
                raise ValueError("PROMO_CONSUME_ERROR")
