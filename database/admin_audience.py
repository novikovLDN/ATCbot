"""Кому можно слать: подбор аудитории для админских рассылок.

ЧТО ЗДЕСЬ
    Два вопроса и ничего больше: кто сейчас без подписки (и до кого вообще
    доходят сообщения) и кто сидит на живом триале. Оба списка — вход для
    рассылок и промо-догоняющих.

ПОЧЕМУ НЕ В database/broadcast_segments.py
    Там сегменты выбираются по имени из фиксированного перечня и заточены
    под мастер рассылки. Эти два запроса вызываются напрямую из воркеров и
    admin-хендлеров, у них своя семантика «ещё не платит» — смешивать их с
    сегментами значило бы менять поведение рассылок при правке промо.

ЧТО ЛЕГКО СЛОМАТЬ
    Колонка users.is_reachable появляется миграцией и на отставшей схеме её
    может не быть: запрос ловит asyncpg.UndefinedColumnError и повторяет без
    неё. Убрать фолбэк — значит уронить подбор аудитории на старой базе.

    Триал пишет в subscriptions строку с source='trial'. Фильтр «есть живая
    платная подписка» ОБЯЗАН исключать source='trial' явно, иначе аудитория
    триальщиков выходит пустой: каждый из них выглядит уже платящим.

    Сравнение времени в get_active_trial_telegram_ids идёт через параметр
    $1, а не NOW(): users.trial_expires_at — TIMESTAMP без зоны, а
    subscriptions.expires_at — TIMESTAMPTZ. Смешать их с NOW() = ошибка
    «operator does not exist», на которую в этом репозитории уже наступали.
"""
import asyncpg
import logging
from datetime import datetime, timezone

import database.core as _core
from database.core import get_pool, _to_db_utc, _from_db_utc

logger = logging.getLogger(__name__)


async def get_eligible_no_subscription_broadcast_users() -> list:
    """Get users eligible for no-subscription broadcast.
    Eligible = no active paid subscription, no active trial, is_reachable=TRUE.
    Returns list of dicts with telegram_id. Defensive: fallback if is_reachable missing.
    """
    if not _core.DB_READY:
        logger.warning("DB not ready, get_eligible_no_subscription_broadcast_users skipped")
        return []
    pool = await get_pool()
    if pool is None:
        return []
    async with pool.acquire() as conn:
        now = _to_db_utc(datetime.now(timezone.utc))
        query_with_reachable = """
            SELECT u.telegram_id
            FROM users u
            LEFT JOIN subscriptions paid_s ON paid_s.telegram_id = u.telegram_id
                AND paid_s.status = 'active'
                AND paid_s.expires_at > $1
                AND paid_s.source != 'trial'
            WHERE paid_s.id IS NULL
              AND (u.trial_expires_at IS NULL OR u.trial_expires_at <= $1)
              AND COALESCE(u.is_reachable, TRUE) = TRUE
        """
        fallback_query = """
            SELECT u.telegram_id
            FROM users u
            LEFT JOIN subscriptions paid_s ON paid_s.telegram_id = u.telegram_id
                AND paid_s.status = 'active'
                AND paid_s.expires_at > $1
                AND paid_s.source != 'trial'
            WHERE paid_s.id IS NULL
              AND (u.trial_expires_at IS NULL OR u.trial_expires_at <= $1)
        """
        try:
            rows = await conn.fetch(query_with_reachable, now)
        except asyncpg.UndefinedColumnError:
            logger.warning("DB_SCHEMA_OUTDATED: is_reachable missing, no_sub_broadcast fallback")
            rows = await conn.fetch(fallback_query, now)
        return [{"telegram_id": row["telegram_id"]} for row in rows]


async def check_user_still_eligible_for_no_sub_broadcast(conn, telegram_id: int, now: datetime) -> bool:
    """Race-condition re-check before sending. Returns True if still eligible."""
    from database.subscriptions import get_active_paid_subscription
    paid = await get_active_paid_subscription(conn, telegram_id, now)
    if paid:
        return False
    try:
        row = await conn.fetchrow(
            "SELECT trial_expires_at, is_reachable FROM users WHERE telegram_id = $1",
            telegram_id
        )
    except asyncpg.UndefinedColumnError:
        row = await conn.fetchrow(
            "SELECT trial_expires_at FROM users WHERE telegram_id = $1",
            telegram_id
        )
    if not row:
        return False
    trial_expires_at = row.get("trial_expires_at")
    if trial_expires_at:
        trial_expires_at_utc = _from_db_utc(trial_expires_at)
        now_utc = now if (getattr(now, "tzinfo", None) is not None) else datetime.now(timezone.utc)
        if trial_expires_at_utc > now_utc:
            return False
    is_reachable = row.get("is_reachable")
    if is_reachable is False:
        return False
    return True


async def get_active_trial_telegram_ids() -> list:
    """Telegram IDs of users currently on an active trial — and ONLY
    on a trial (no live PAID premium subscription).

    Trial activation writes a `subscriptions` row with source='trial',
    status='active', subscription_type='basic' (default tariff in
    grant_access), expires_at = trial end, is_bypass_only=FALSE. That
    means the "looks like an active paid sub" filter MUST exclude
    source='trial' explicitly — otherwise the audience comes out
    empty (every trial user gets filtered as if they were already
    paying).

    Time comparison uses an explicit `$1` parameter (not NOW()):
    `users.trial_expires_at` is TIMESTAMP without tz in this DB while
    `subscriptions.expires_at` is TIMESTAMPTZ. Mixing NOW() with a
    naive TIMESTAMP column triggers `operator does not exist`
    failures — the same class of error we hit before in this repo.
    `_to_db_utc` produces the naive-UTC datetime asyncpg can compare
    against both columns.

    Filters:
      - users.trial_expires_at > $1                  → trial running
      - NO subscriptions row with:
          - status='active', expires_at > $1
          - source != 'trial'                        → really paid
          - is_bypass_only=FALSE
          - subscription_type IN paid tariffs

    Returns sorted list of telegram_id integers.
    """
    pool = await get_pool()
    now = _to_db_utc(datetime.now(timezone.utc))
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT u.telegram_id
               FROM users u
               WHERE u.trial_expires_at IS NOT NULL
                 AND u.trial_expires_at > $1
                 AND NOT EXISTS (
                     SELECT 1 FROM subscriptions s
                     WHERE s.telegram_id = u.telegram_id
                       AND s.status = 'active'
                       AND s.expires_at > $1
                       AND COALESCE(s.source, '') != 'trial'
                       AND COALESCE(s.is_bypass_only, FALSE) = FALSE
                       AND s.subscription_type IN (
                           'basic', 'plus', 'biz_starter', 'biz_team',
                           'biz_business', 'biz_pro', 'biz_enterprise',
                           'biz_ultimate'
                       )
                 )
               ORDER BY u.telegram_id""",
            now,
        )
    return [r["telegram_id"] for r in rows]
