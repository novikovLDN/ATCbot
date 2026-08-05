"""
Database operations for Remnawave traffic integration.

- remnawave_uuid CRUD on subscriptions table
- traffic notification flags on users table
- traffic_purchases table
- user_traffic_discounts table (promo discounts on traffic packs)
"""
import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

import database.core as _core
from database.core import get_pool

logger = logging.getLogger(__name__)


# ── Remnawave UUID ─────────────────────────────────────────────────────

async def get_remnawave_uuid(telegram_id: int) -> Optional[str]:
    if not _core.DB_READY:
        return None
    pool = await get_pool()
    if pool is None:
        return None
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT remnawave_uuid FROM subscriptions WHERE telegram_id = $1 AND status = 'active'",
            telegram_id,
        )


async def set_remnawave_uuid(telegram_id: int, uuid: str) -> None:
    if not _core.DB_READY:
        return
    pool = await get_pool()
    if pool is None:
        return
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE subscriptions SET remnawave_uuid = $1 WHERE telegram_id = $2 AND status = 'active'",
            uuid, telegram_id,
        )


async def clear_remnawave_uuid(telegram_id: int) -> None:
    if not _core.DB_READY:
        return
    pool = await get_pool()
    if pool is None:
        return
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE subscriptions SET remnawave_uuid = NULL WHERE telegram_id = $1",
            telegram_id,
        )


# ── Remnawave premium UUID (MainServer squad, migration 045) ──────────

async def get_remnawave_premium_uuid(telegram_id: int) -> Optional[str]:
    """Return the Remnawave UUID of the premium (MainServer) entity, if any."""
    if not _core.DB_READY:
        return None
    pool = await get_pool()
    if pool is None:
        return None
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT remnawave_premium_uuid FROM subscriptions "
            "WHERE telegram_id = $1 AND status = 'active'",
            telegram_id,
        )


async def set_remnawave_premium_uuid(
    telegram_id: int,
    uuid: str,
    *,
    mark_migrated: bool = True,
) -> None:
    """Store the premium Remnawave UUID. Also stamps samopis_migrated_at by default."""
    if not _core.DB_READY:
        return
    pool = await get_pool()
    if pool is None:
        return
    async with pool.acquire() as conn:
        if mark_migrated:
            await conn.execute(
                "UPDATE subscriptions "
                "SET remnawave_premium_uuid = $1, samopis_migrated_at = NOW() "
                "WHERE telegram_id = $2 AND status = 'active'",
                uuid, telegram_id,
            )
        else:
            await conn.execute(
                "UPDATE subscriptions SET remnawave_premium_uuid = $1 "
                "WHERE telegram_id = $2 AND status = 'active'",
                uuid, telegram_id,
            )


async def set_remnawave_premium_uuid_and_url(
    telegram_id: int,
    uuid: str,
    sub_url: Optional[str],
    *,
    short_uuid: Optional[str] = None,
    mark_migrated: bool = True,
) -> None:
    """Atomically persist (uuid, subscription_url, short_uuid) for the premium entity.

    Used by the migration script so the fallback router never has to call
    Remnawave just to learn the URL — single UPDATE keeps the columns in
    sync.  Any of sub_url / short_uuid may be None when the panel didn't
    return them; callers can patch sub_url later via
    set_remnawave_premium_sub_url().
    """
    if not _core.DB_READY:
        return
    pool = await get_pool()
    if pool is None:
        return
    async with pool.acquire() as conn:
        if mark_migrated:
            await conn.execute(
                "UPDATE subscriptions "
                "SET remnawave_premium_uuid = $1, "
                "    remnawave_premium_sub_url = $2, "
                "    remnawave_premium_short_uuid = $3, "
                "    samopis_migrated_at = NOW() "
                "WHERE telegram_id = $4 AND status = 'active'",
                uuid, sub_url, short_uuid, telegram_id,
            )
        else:
            await conn.execute(
                "UPDATE subscriptions "
                "SET remnawave_premium_uuid = $1, "
                "    remnawave_premium_sub_url = $2, "
                "    remnawave_premium_short_uuid = $3 "
                "WHERE telegram_id = $4 AND status = 'active'",
                uuid, sub_url, short_uuid, telegram_id,
            )


async def set_remnawave_bypass_cache(
    telegram_id: int,
    uuid: Optional[str],
    sub_url: Optional[str],
    short_uuid: Optional[str],
) -> None:
    """Persist (uuid, subscription_url, short_uuid) for the bypass entity.

    Symmetric helper to set_remnawave_premium_uuid_and_url — keeps the
    three bypass columns (remnawave_uuid, remnawave_bypass_sub_url,
    remnawave_bypass_short_uuid) in sync from a single UPDATE so the
    UI never has to round-trip to the panel just to learn the URL.
    """
    if not _core.DB_READY:
        return
    pool = await get_pool()
    if pool is None:
        return
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE subscriptions "
            "SET remnawave_uuid = COALESCE($1, remnawave_uuid), "
            "    remnawave_bypass_sub_url = COALESCE($2, remnawave_bypass_sub_url), "
            "    remnawave_bypass_short_uuid = COALESCE($3, remnawave_bypass_short_uuid) "
            "WHERE telegram_id = $4 AND status = 'active'",
            uuid, sub_url, short_uuid, telegram_id,
        )


async def get_remnawave_bypass_cache(telegram_id: int) -> Optional[Dict[str, Any]]:
    """Return (uuid, sub_url, short_uuid) for the bypass entity or None."""
    if not _core.DB_READY:
        return None
    pool = await get_pool()
    if pool is None:
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT remnawave_uuid, remnawave_bypass_sub_url, remnawave_bypass_short_uuid "
            "FROM subscriptions WHERE telegram_id = $1 AND status = 'active'",
            telegram_id,
        )
        return dict(row) if row else None


async def set_remnawave_premium_sub_url(telegram_id: int, sub_url: str) -> None:
    """Back-fill the cached subscriptionUrl for the premium entity.

    Used by the fallback router on a cache miss (legacy rows migrated
    before column 046 existed).
    """
    if not _core.DB_READY:
        return
    pool = await get_pool()
    if pool is None:
        return
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE subscriptions SET remnawave_premium_sub_url = $1 "
            "WHERE telegram_id = $2 AND status = 'active'",
            sub_url, telegram_id,
        )


async def clear_remnawave_premium_uuid(telegram_id: int) -> None:
    if not _core.DB_READY:
        return
    pool = await get_pool()
    if pool is None:
        return
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE subscriptions SET remnawave_premium_uuid = NULL "
            "WHERE telegram_id = $1",
            telegram_id,
        )


async def get_subscription_by_premium_uuid(uuid: str) -> Optional[Dict[str, Any]]:
    """Look up a subscription by its premium Remnawave UUID.

    Used by the subscription-URL fallback endpoint to translate a legacy
    samopis UUID (which the migration may have reused as the panel UUID)
    into a Telegram-id / Remnawave-UUID pair.
    """
    if not _core.DB_READY:
        return None
    pool = await get_pool()
    if pool is None:
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT telegram_id, remnawave_premium_uuid, remnawave_premium_sub_url, "
            "       remnawave_uuid, status, subscription_type, expires_at, samopis_migrated_at "
            "FROM subscriptions WHERE remnawave_premium_uuid = $1",
            uuid,
        )
        return dict(row) if row else None


async def get_subscription_by_samopis_uuid(uuid: str) -> Optional[Dict[str, Any]]:
    """Look up a subscription by its legacy samopis Xray UUID."""
    if not _core.DB_READY:
        return None
    pool = await get_pool()
    if pool is None:
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT telegram_id, uuid, remnawave_premium_uuid, remnawave_premium_sub_url, "
            "       remnawave_uuid, status, subscription_type, expires_at, samopis_migrated_at "
            "FROM subscriptions WHERE uuid = $1",
            uuid,
        )
        return dict(row) if row else None


# Здесь были count_migration_broadcast_candidates,
# list_migration_broadcast_candidates и mark_migration_notice_sent —
# выборка адресатов одноразовой рассылки о переезде samopis → Remnawave и
# отметка «уведомлён».
#
# Рассылку (app/services/migration_broadcast.py) удалили: у неё не было ни
# одного вызова, текст был только на русском, а дата отключения старых
# ссылок — 18.05.2026 — прошла. Подробности в
# tests/services/test_migration_broadcast_removed.py. Эти три функции
# остались без единого потребителя и выглядели рабочей частью API слоя БД.
#
# Колонка subscriptions.migration_notice_sent_at (миграция 049) НЕ
# трогается: её удаление — миграция схемы и решение владельца.


async def count_premium_migration_progress() -> Dict[str, int]:
    """Snapshot of where the samopis→Remnawave premium migration stands.

    Returns dict with three counters:
      migrated              — rows that already have remnawave_premium_uuid
                              set (samopis_migrated_at NOT NULL).
      remaining_candidates  — rows still eligible for migration (matches
                              the SQL of list_subscriptions_for_premium_migration).
      total_active_paid     — migrated + remaining (total denominator the
                              admin progress UI shows).
    All counters return 0 if the DB pool isn't ready.
    """
    if not _core.DB_READY:
        return {"migrated": 0, "remaining_candidates": 0, "total_active_paid": 0}
    pool = await get_pool()
    if pool is None:
        return {"migrated": 0, "remaining_candidates": 0, "total_active_paid": 0}

    async with pool.acquire() as conn:
        migrated = await conn.fetchval(
            "SELECT COUNT(*) FROM subscriptions "
            "WHERE remnawave_premium_uuid IS NOT NULL "
            "  AND remnawave_premium_uuid != '' "
            "  AND samopis_migrated_at IS NOT NULL"
        )
        remaining = await conn.fetchval(
            "SELECT COUNT(*) FROM subscriptions "
            "WHERE status = 'active' "
            "  AND uuid IS NOT NULL "
            "  AND uuid != '' "
            "  AND expires_at > NOW() "
            "  AND subscription_type IS DISTINCT FROM 'trial' "
            "  AND (remnawave_premium_uuid IS NULL OR remnawave_premium_uuid = '')"
        )
    return {
        "migrated": int(migrated or 0),
        "remaining_candidates": int(remaining or 0),
        "total_active_paid": int((migrated or 0) + (remaining or 0)),
    }


async def list_subscriptions_for_premium_migration(
    *,
    limit: Optional[int] = None,
    telegram_id: Optional[int] = None,
    include_already_migrated: bool = False,
) -> List[Dict[str, Any]]:
    """Return rows that the samopis→Remnawave-premium migration should process.

    A candidate has:
      - status = 'active'
      - uuid (samopis Xray UUID) IS NOT NULL AND != ''
      - expires_at > NOW()  (unexpired)
      - subscription_type NOT IN ('trial')  (paid users only)
    Unless `include_already_migrated` is True, rows where
    `remnawave_premium_uuid` is already set are excluded so the script
    can be safely resumed.
    """
    if not _core.DB_READY:
        return []
    pool = await get_pool()
    if pool is None:
        return []

    clauses = [
        "status = 'active'",
        "uuid IS NOT NULL",
        "uuid != ''",
        "expires_at > NOW()",
        "subscription_type IS DISTINCT FROM 'trial'",
    ]
    args: list = []
    if not include_already_migrated:
        clauses.append("(remnawave_premium_uuid IS NULL OR remnawave_premium_uuid = '')")
    if telegram_id is not None:
        args.append(telegram_id)
        clauses.append(f"telegram_id = ${len(args)}")

    query = (
        "SELECT telegram_id, uuid, remnawave_uuid, remnawave_premium_uuid, "
        "       subscription_type, expires_at, status, samopis_migrated_at "
        "FROM subscriptions WHERE " + " AND ".join(clauses) +
        " ORDER BY telegram_id"
    )
    if limit is not None and limit > 0:
        query += f" LIMIT {int(limit)}"

    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *args)
        return [dict(r) for r in rows]


# ── Traffic notification flags ─────────────────────────────────────────

async def get_traffic_notification_flags(telegram_id: int) -> Dict[str, bool]:
    if not _core.DB_READY:
        return {}
    pool = await get_pool()
    if pool is None:
        return {}
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT traffic_notified_8gb, traffic_notified_5gb,
                      traffic_notified_3gb, traffic_notified_1gb,
                      traffic_notified_500mb, traffic_notified_0
               FROM users WHERE telegram_id = $1""",
            telegram_id,
        )
        if not row:
            return {}
        return dict(row)


async def set_traffic_notification_flag(telegram_id: int, flag_key: str) -> None:
    if not _core.DB_READY:
        return
    # Whitelist valid flag columns to prevent injection
    valid = {"traffic_notified_8gb", "traffic_notified_5gb", "traffic_notified_3gb", "traffic_notified_1gb", "traffic_notified_500mb", "traffic_notified_0"}
    if flag_key not in valid:
        return
    pool = await get_pool()
    if pool is None:
        return
    async with pool.acquire() as conn:
        await conn.execute(
            f"UPDATE users SET {flag_key} = TRUE WHERE telegram_id = $1",
            telegram_id,
        )


async def reset_traffic_notification_flags(telegram_id: int) -> None:
    if not _core.DB_READY:
        return
    pool = await get_pool()
    if pool is None:
        return
    async with pool.acquire() as conn:
        await conn.execute(
            """UPDATE users SET
                traffic_notified_8gb = FALSE,
                traffic_notified_5gb = FALSE,
                traffic_notified_3gb = FALSE,
                traffic_notified_1gb = FALSE,
                traffic_notified_500mb = FALSE,
                traffic_notified_0 = FALSE
               WHERE telegram_id = $1""",
            telegram_id,
        )


# ── Traffic purchases ──────────────────────────────────────────────────

async def record_traffic_purchase(
    telegram_id: int,
    gb_amount: int,
    price_rub: int,
    payment_method: str = "balance",
    purchase_id: Optional[str] = None,
) -> Optional[int]:
    """Записать выданный пакет ГБ.

    purchase_id — ключ идемпотентности начисления (см. миграцию 075). Его
    передаёт только тот, у кого он есть и у кого он уникален на покупку:
    вебхуки провайдеров и воркер отложенной активации. Для покупок пакетов
    ГБ и bypass-only он остаётся None — там нет идентификатора покупки, и
    выдумывать его нельзя: мнимый ключ хуже отсутствующего.

    Запись делается ПОСЛЕ того, как панель приняла начисление. Порядок
    менять нельзя: строка здесь — это утверждение «гигабайты выданы», и
    если написать её заранее, повтор увидит ключ и не доначислит ничего.
    """
    if not _core.DB_READY:
        return None
    pool = await get_pool()
    if pool is None:
        return None
    async with pool.acquire() as conn:
        # Обе колонки добавлялись миграциями поверх живой таблицы, и код
        # обязан пережить базу, до которой миграция ещё не доехала: без
        # проверки INSERT упал бы на несуществующей колонке и потерял бы
        # запись о начислении вместе с ключом.
        _cols = await conn.fetch(
            """SELECT column_name FROM information_schema.columns
               WHERE table_name = 'traffic_purchases'
                 AND column_name IN ('payment_method', 'purchase_id')"""
        )
        _names = {r["column_name"] for r in _cols}

        fields = ["telegram_id", "gb_amount", "price_rub"]
        values: list = [telegram_id, gb_amount, price_rub]
        if "payment_method" in _names:
            fields.append("payment_method")
            values.append(payment_method)
        if purchase_id is not None and "purchase_id" in _names:
            fields.append("purchase_id")
            values.append(purchase_id)

        placeholders = ", ".join(f"${i}" for i in range(1, len(values) + 1))
        # ON CONFLICT DO NOTHING — по частичному уникальному индексу на
        # purchase_id. Два одновременных вебхука на одну покупку не должны
        # ронять начисление ошибкой уникальности: ключ уже занят, значит
        # запись о выдаче есть, и это ровно то, чего мы добивались.
        return await conn.fetchval(
            f"""INSERT INTO traffic_purchases ({", ".join(fields)})
                VALUES ({placeholders})
                ON CONFLICT DO NOTHING
                RETURNING id""",
            *values,
        )


async def combo_traffic_already_granted(purchase_id: str) -> Optional[bool]:
    """Начислялись ли уже гигабайты за покупку purchase_id.

    True  — начислялись, повторять нельзя;
    False — не начислялись;
    None  — узнать не удалось (нет базы, нет колонки, запрос упал).

    None — не «нет». Вызывающий обязан различать: на неизвестности мы не
    начисляем. Иначе моргнувшая база превращается в раздачу вторых пакетов,
    а вот отказ начислить виден и покупателю, и в payment_errors, и на
    вебхуке приводит к повтору.
    """
    if not purchase_id or not _core.DB_READY:
        return None
    pool = await get_pool()
    if pool is None:
        return None
    try:
        async with pool.acquire() as conn:
            return bool(await conn.fetchval(
                """SELECT EXISTS (
                    SELECT 1 FROM traffic_purchases WHERE purchase_id = $1
                )""",
                purchase_id,
            ))
    except Exception as e:
        logger.error(
            "COMBO_TRAFFIC_KEY_LOOKUP_FAILED purchase_id=%s error=%s: %s "
            "— проверить, начислялись ли ГБ, не удалось; начисление будет "
            "отложено до повтора",
            purchase_id, type(e).__name__, e,
        )
        return None


# ── Queries for traffic monitor worker ─────────────────────────────────

async def get_active_remnawave_users() -> List[Dict[str, Any]]:
    """Users with active subscription AND remnawave_uuid set."""
    if not _core.DB_READY:
        return []
    pool = await get_pool()
    if pool is None:
        return []
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT s.telegram_id, s.remnawave_uuid, s.subscription_type
               FROM subscriptions s
               WHERE s.status = 'active'
                 AND s.remnawave_uuid IS NOT NULL
                 AND s.remnawave_uuid != ''""",
        )
        return [dict(r) for r in rows]


async def get_active_users_without_remnawave() -> List[Dict[str, Any]]:
    """Users with active non-trial subscription but NO remnawave_uuid."""
    if not _core.DB_READY:
        return []
    pool = await get_pool()
    if pool is None:
        return []
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT s.telegram_id, s.subscription_type, s.expires_at
               FROM subscriptions s
               WHERE s.status = 'active'
                 AND s.subscription_type NOT IN ('trial')
                 AND (s.remnawave_uuid IS NULL OR s.remnawave_uuid = '')
                 AND s.expires_at > NOW()
               ORDER BY s.telegram_id""",
        )
        return [dict(r) for r in rows]


# ── Traffic discounts (promo from broadcasts) ─────────────────────────

async def get_user_traffic_discount(telegram_id: int) -> Optional[Dict[str, Any]]:
    """Return active (non-expired) traffic discount for user, or None."""
    if not _core.DB_READY:
        return None
    pool = await get_pool()
    if pool is None:
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT * FROM user_traffic_discounts
               WHERE telegram_id = $1
                 AND (expires_at IS NULL OR expires_at > NOW())
               ORDER BY created_at DESC LIMIT 1""",
            telegram_id,
        )
        return dict(row) if row else None


async def create_user_traffic_discount(
    telegram_id: int,
    discount_percent: int,
    expires_at: Optional[datetime],
    created_by: int,
) -> bool:
    """Create or replace traffic discount for user. Returns True on success."""
    if not _core.DB_READY:
        return False
    pool = await get_pool()
    if pool is None:
        return False
    async with pool.acquire() as conn:
        # Column is TIMESTAMP (naive) — strip tzinfo if present
        naive_expires = expires_at.replace(tzinfo=None) if expires_at and expires_at.tzinfo else expires_at
        await conn.execute(
            """INSERT INTO user_traffic_discounts
                   (telegram_id, discount_percent, expires_at, created_by)
               VALUES ($1, $2, $3, $4)
               ON CONFLICT (telegram_id) DO UPDATE
                   SET discount_percent = $2, expires_at = $3, created_by = $4, created_at = NOW()""",
            telegram_id, discount_percent, naive_expires, created_by,
        )
        return True


async def delete_user_traffic_discount(telegram_id: int) -> bool:
    """Remove traffic discount for user."""
    if not _core.DB_READY:
        return False
    pool = await get_pool()
    if pool is None:
        return False
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM user_traffic_discounts WHERE telegram_id = $1",
            telegram_id,
        )
        return result == "DELETE 1"
