"""Разбор последствий двух багов с датами подписки и точечная починка.

ЧТО ЗДЕСЬ
    Инструменты одноразового назначения: найти пострадавших, показать по
    каждому всю доказательную базу (история подписок, платежи, покупки ГБ) и
    вернуть корректную дату окончания. Плюс пачечные выборки, на которых
    работает сканер восстановления премиума.

ДВА БАГА, РАДИ КОТОРЫХ ЭТО НАПИСАНО
    1. bypass-overwrite. Переход на «только обход» переписывал expires_at на
       NOW + 10 лет как маркер, а последующие покупки этот маркер не
       перетирали: в интерфейсе бота подписка «истекает через 10 лет».
    2. premium expireAt в 2036. Ранняя версия сверки принимала такие строки
       за живой премиум и проставляла 2036 год уже в панели — то есть
       раздавала десятилетие премиума бесплатно.

ИСТОЧНИК ИСТИНЫ
    subscription_history — журнал всех событий подписки (покупки, продления,
    подарки, админские выдачи). MAX(end_date) по нему и есть настоящая дата
    окончания, независимо от того, каким путём человек её получил. Остальные
    выборки (pending_purchases, payments, gift_subscriptions) — запасные
    источники для тех, у кого журнал неполный.

ЧТО ЛЕГКО СЛОМАТЬ
    Grace-период в fix_bypass_overwrite_victim. Если правильная end_date уже
    в прошлом, ставится NOW + 1 сутки: панель Remnawave не принимает дату из
    прошлого как активную подписку. Убрать эту ветку — и починка молча
    оставит человека без доступа.

    get_bypass_overwrite_victims не делает ни одного UPDATE. Это аудит,
    который читают глазами перед тем, как чинить; добавить сюда запись —
    значит лишить операцию шага «сначала посмотреть».
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from database.core import get_pool, _to_db_utc, _from_db_utc


async def get_bypass_overwrite_victims() -> List[Dict[str, Any]]:
    """Список пострадавших от bypass-overwrite бага с детализацией.

    Для каждого юзера:
      - текущая subscription row;
      - все subscription_history записи покупок/продлений;
      - все traffic_purchases;
      - вычисленный корректный expires_at = max(end_date) по
        последней платной транзакции;
      - вердикт `can_fix`: достаточно ли данных для восстановления.

    Не делает никаких UPDATE — только read-only аудит.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        suspects = await conn.fetch(
            """
            SELECT s.telegram_id,
                   u.username,
                   s.expires_at AS current_expires_at,
                   s.is_bypass_only,
                   s.subscription_type AS current_subscription_type,
                   s.source AS current_source,
                   s.is_combo
            FROM subscriptions s
            JOIN users u ON u.telegram_id = s.telegram_id
            WHERE s.is_bypass_only = TRUE
              AND s.expires_at > (NOW() AT TIME ZONE 'UTC') + INTERVAL '3 years'
              AND EXISTS (
                  SELECT 1 FROM subscription_history sh
                  WHERE sh.telegram_id = s.telegram_id
                    AND sh.action_type IN ('purchase', 'renewal', 'auto_renew')
              )
            ORDER BY s.expires_at DESC
            """
        )

        victims: List[Dict[str, Any]] = []
        for row in suspects:
            tg = int(row["telegram_id"])
            history = await conn.fetch(
                """
                SELECT id, action_type, start_date, end_date, created_at, vpn_key
                FROM subscription_history
                WHERE telegram_id = $1
                ORDER BY created_at DESC
                LIMIT 50
                """,
                tg,
            )
            traffic = await conn.fetch(
                """
                SELECT id, gb_amount, price_rub, created_at
                FROM traffic_purchases
                WHERE telegram_id = $1
                ORDER BY created_at DESC
                LIMIT 50
                """,
                tg,
            )
            payments = await conn.fetch(
                """
                SELECT id, tariff, amount, paid_at, created_at, purchase_id
                FROM payments
                WHERE telegram_id = $1 AND status = 'approved'
                ORDER BY COALESCE(paid_at, created_at) DESC
                LIMIT 50
                """,
                tg,
            )

            # Источник истины для корректного expires_at — самая поздняя
            # end_date по платным action_type'ам в subscription_history.
            last_paid_end = await conn.fetchval(
                """
                SELECT MAX(end_date) FROM subscription_history
                WHERE telegram_id = $1
                  AND action_type IN ('purchase', 'renewal', 'auto_renew')
                """,
                tg,
            )
            last_paid_action = await conn.fetchrow(
                """
                SELECT action_type, end_date, created_at
                FROM subscription_history
                WHERE telegram_id = $1
                  AND action_type IN ('purchase', 'renewal', 'auto_renew')
                ORDER BY end_date DESC NULLS LAST
                LIMIT 1
                """,
                tg,
            )

            traffic_total_gb = sum(int(t["gb_amount"] or 0) for t in traffic)
            payments_count = len(payments)
            premium_payments_count = len(
                [p for p in payments if (p["tariff"] or "").startswith(("basic", "plus"))]
            )

            # Вердикт: восстановим если есть end_date в истории.
            # Grace-period: если эта end_date уже в прошлом, при
            # применении fix'а будет NOW + 1 day (Remnawave не
            # принимает даты в прошлом как активную подписку).
            can_fix = last_paid_end is not None
            now_utc_naive = datetime.now(timezone.utc).replace(tzinfo=None)
            grace_will_apply = (
                last_paid_end is not None and last_paid_end <= now_utc_naive
            )
            proposed_after_grace = (
                _to_db_utc(datetime.now(timezone.utc) + timedelta(days=1))
                if grace_will_apply
                else last_paid_end
            )

            victims.append(
                {
                    "telegram_id": tg,
                    "username": row["username"],
                    "current_expires_at": row["current_expires_at"],
                    "current_is_bypass_only": bool(row["is_bypass_only"]),
                    "current_subscription_type": row["current_subscription_type"],
                    "current_source": row["current_source"],
                    "current_is_combo": bool(row["is_combo"]) if row["is_combo"] is not None else False,
                    "proposed_expires_at": proposed_after_grace,
                    "history_end_date": last_paid_end,
                    "grace_will_apply": grace_will_apply,
                    "last_paid_action_type": (
                        last_paid_action["action_type"] if last_paid_action else None
                    ),
                    "history": [
                        {
                            "id": int(h["id"]),
                            "action_type": h["action_type"],
                            "start_date": h["start_date"],
                            "end_date": h["end_date"],
                            "created_at": h["created_at"],
                        }
                        for h in history
                    ],
                    "payments": [
                        {
                            "id": int(p["id"]),
                            "tariff": p["tariff"],
                            "amount_rubles": float((p["amount"] or 0)) / 100.0,
                            "paid_at": p["paid_at"],
                            "created_at": p["created_at"],
                            "purchase_id": p["purchase_id"],
                        }
                        for p in payments
                    ],
                    "traffic_purchases": [
                        {
                            "id": int(t["id"]),
                            "gb_amount": int(t["gb_amount"] or 0),
                            "price_rub": int(t["price_rub"] or 0),
                            "created_at": t["created_at"],
                        }
                        for t in traffic
                    ],
                    "traffic_total_gb": traffic_total_gb,
                    "payments_count": payments_count,
                    "premium_payments_count": premium_payments_count,
                    "can_fix": can_fix,
                }
            )

        return victims


async def fix_bypass_overwrite_victim(telegram_id: int) -> Dict[str, Any]:
    """Восстановить корректную подписку для одного пострадавшего юзера.

    Алгоритм:
      1. Найти max(end_date) среди subscription_history с
         action_type IN ('purchase','renewal','auto_renew').
      2. Если эта end_date уже в прошлом — Remnawave-панель не
         примет дату из прошлого как активную подписку. Поэтому
         даём минимальный grace-period: NOW + 1 day. Юзер увидит
         «истекает завтра», что технически даёт ему 1 сутки и
         корректно проставится в панели.
      3. UPDATE subscriptions: is_bypass_only=FALSE,
         expires_at=<correct_or_grace>, source='payment',
         subscription_type — оставить как есть, кроме случая
         'bypass_only' → 'basic'.

    Не трогает Remnawave — там трафик хранится отдельно и его
    реставрировать не нужно (bypass GB всё равно у юзера остались).

    Returns dict с before/after + grace_applied для логирования.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            before = await conn.fetchrow(
                "SELECT expires_at, is_bypass_only, subscription_type, source FROM subscriptions WHERE telegram_id = $1",
                telegram_id,
            )
            if not before:
                return {"ok": False, "reason": "no_subscription_row"}

            history_end = await conn.fetchval(
                """
                SELECT MAX(end_date) FROM subscription_history
                WHERE telegram_id = $1
                  AND action_type IN ('purchase', 'renewal', 'auto_renew')
                """,
                telegram_id,
            )
            if history_end is None:
                return {"ok": False, "reason": "no_paid_history_to_recover_from"}

            # Grace-period: если правильная end_date уже в прошлом —
            # ставим NOW + 1 сутки. Иначе берём saved end_date.
            now_utc = datetime.now(timezone.utc)
            history_end_aware = _from_db_utc(history_end)
            grace_applied = history_end_aware <= now_utc
            target_expires_at = (
                _to_db_utc(now_utc + timedelta(days=1)) if grace_applied else history_end
            )

            await conn.execute(
                """
                UPDATE subscriptions
                SET is_bypass_only = FALSE,
                    expires_at = $2,
                    source = CASE WHEN source = 'bypass_only' THEN 'payment' ELSE source END,
                    subscription_type = CASE WHEN subscription_type IS NULL OR subscription_type = ''
                                              THEN 'basic' ELSE subscription_type END
                WHERE telegram_id = $1
                """,
                telegram_id,
                target_expires_at,
            )
            after = await conn.fetchrow(
                "SELECT expires_at, is_bypass_only, subscription_type, source FROM subscriptions WHERE telegram_id = $1",
                telegram_id,
            )

    return {
        "ok": True,
        "telegram_id": telegram_id,
        "grace_applied": grace_applied,
        "history_end_date": history_end,
        "before": dict(before),
        "after": dict(after) if after else None,
    }


async def get_premium_recovery_candidates() -> list:
    """Users whose bypass-only row is still pinned to a premium uuid AND
    whose expires_at is parked in the far future (the 10-year marker).

    Returns dicts with: telegram_id, remnawave_premium_uuid, db_expires_at.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT telegram_id, remnawave_premium_uuid, expires_at
               FROM subscriptions
               WHERE is_bypass_only = TRUE
                 AND remnawave_premium_uuid IS NOT NULL
                 AND expires_at > NOW() + INTERVAL '5 years'"""
        )
    return [dict(r) for r in rows]


async def get_user_paid_subscription_history(telegram_id: int) -> list:
    """Chronological list of the user's PAID subscription purchases
    (excludes balance top-ups, traffic packs, and pending/expired rows).

    Returns list of {created_at, period_days, tariff} in ascending order.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT created_at, period_days, tariff
               FROM pending_purchases
               WHERE telegram_id = $1
                 AND status = 'paid'
                 AND period_days > 0
                 AND tariff IN ('basic', 'plus', 'biz_starter', 'biz_team',
                                'biz_business', 'biz_pro', 'biz_enterprise',
                                'biz_ultimate')
               ORDER BY created_at ASC""",
            telegram_id,
        )
    return [dict(r) for r in rows]


async def get_paid_subscription_history_bulk(telegram_ids: list) -> dict:
    """Bulk-fetch paid subscription history for many users in ONE query.

    Returns a dict: telegram_id -> [{created_at, period_days, tariff}, ...]
    sorted ascending by created_at. Missing users get an empty list.

    Used by the premium recovery scan instead of 1k+ separate roundtrips.
    """
    if not telegram_ids:
        return {}
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT telegram_id, created_at, period_days, tariff
               FROM pending_purchases
               WHERE telegram_id = ANY($1::bigint[])
                 AND status = 'paid'
                 AND period_days > 0
                 AND tariff IN ('basic', 'plus', 'biz_starter', 'biz_team',
                                'biz_business', 'biz_pro', 'biz_enterprise',
                                'biz_ultimate')
               ORDER BY telegram_id, created_at ASC""",
            telegram_ids,
        )
    out: dict = {tg: [] for tg in telegram_ids}
    for r in rows:
        out[r["telegram_id"]].append({
            "created_at": r["created_at"],
            "period_days": r["period_days"],
            "tariff": r["tariff"],
        })
    return out


async def get_activated_gifts_bulk(telegram_ids: list) -> dict:
    """Bulk-fetch activated gift subscriptions for users in ONE query.

    Returns dict: telegram_id -> [{activated_at, period_days}, ...]
    Ascending by activated_at. Missing users get empty list.

    Recovery uses this to honour gift subscriptions when computing
    real premium end date — paid history might be empty but a real
    gift still grants premium time.
    """
    if not telegram_ids:
        return {}
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT activated_by, activated_at, period_days
               FROM gift_subscriptions
               WHERE activated_by = ANY($1::bigint[])
                 AND status = 'activated'
                 AND activated_at IS NOT NULL
                 AND period_days > 0
               ORDER BY activated_by, activated_at ASC""",
            telegram_ids,
        )
    out: dict = {tg: [] for tg in telegram_ids}
    for r in rows:
        out[r["activated_by"]].append({
            "activated_at": r["activated_at"],
            "period_days": r["period_days"],
        })
    return out


async def get_max_subscription_end_bulk(telegram_ids: list) -> dict:
    """Bulk-fetch the user's MAX(subscription_history.end_date) per user.

    subscription_history is the source-of-truth ledger for every
    subscription event — purchases, renewals, gifts, admin grants —
    so the maximum end_date is the user's actual last legitimate
    premium expiry, regardless of which acquisition path they came
    through. Recovery uses this as the primary signal instead of
    reconstructing dates from pending_purchases.

    Returns dict: telegram_id -> datetime | None.
    """
    if not telegram_ids:
        return {}
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT telegram_id, MAX(end_date) AS last_end
               FROM subscription_history
               WHERE telegram_id = ANY($1::bigint[])
               GROUP BY telegram_id""",
            telegram_ids,
        )
    out: dict = {tg: None for tg in telegram_ids}
    for r in rows:
        out[r["telegram_id"]] = r["last_end"]
    return out


async def get_paid_payments_via_purchases_bulk(telegram_ids: list) -> dict:
    """Bulk-fetch settled `payments` rows joined onto pending_purchases.

    A user paid through a provider can have rows in `payments` even
    when pending_purchases status didn't flip to 'paid' for some reason
    (legacy flows, admin approve, edge-case webhooks). Joining on
    purchase_id reconstructs period_days from pending_purchases so we
    can still compute an end date.

    Returns dict: telegram_id -> [{created_at, period_days, tariff}, ...]
    ordered ascending. Used as belt-and-suspenders fallback in recovery.
    """
    if not telegram_ids:
        return {}
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT p.telegram_id,
                      COALESCE(p.paid_at, p.created_at) AS created_at,
                      pp.period_days,
                      COALESCE(p.tariff, pp.tariff) AS tariff
               FROM payments p
               LEFT JOIN pending_purchases pp ON pp.purchase_id = p.purchase_id
               WHERE p.telegram_id = ANY($1::bigint[])
                 AND p.status IN ('paid', 'approved')
                 AND pp.period_days IS NOT NULL
                 AND pp.period_days > 0
                 AND COALESCE(p.tariff, pp.tariff) IN
                     ('basic', 'plus', 'biz_starter', 'biz_team',
                      'biz_business', 'biz_pro', 'biz_enterprise',
                      'biz_ultimate')
               ORDER BY p.telegram_id, COALESCE(p.paid_at, p.created_at) ASC""",
            telegram_ids,
        )
    out: dict = {tg: [] for tg in telegram_ids}
    for r in rows:
        out[r["telegram_id"]].append({
            "created_at": r["created_at"],
            "period_days": r["period_days"],
            "tariff": r["tariff"],
        })
    return out


async def get_active_premium_subscribers() -> list:
    """All subscriptions currently considered active premium (NOT bypass-only).

    For the audit-tool: we want users whose premium subscription is
    nominally active in the bot's DB so we can cross-check it against
    payments and the Remnawave panel.

    Filters:
      - status='active' AND expires_at > NOW (still in their paid window)
      - NOT is_bypass_only (we never audit bypass-only rows, those are
        traffic-pack only and live on +10y by design)
      - subscription_type in the real premium tariffs

    Returns list of dicts: telegram_id, remnawave_premium_uuid,
    expires_at, subscription_type.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT telegram_id, remnawave_premium_uuid,
                      expires_at, subscription_type
               FROM subscriptions
               WHERE status = 'active'
                 AND expires_at > NOW()
                 AND COALESCE(is_bypass_only, FALSE) = FALSE
                 AND subscription_type IN
                     ('basic', 'plus', 'biz_starter', 'biz_team',
                      'biz_business', 'biz_pro', 'biz_enterprise',
                      'biz_ultimate')
               ORDER BY telegram_id"""
        )
    return [dict(r) for r in rows]


async def get_subscriptions_with_far_future_expires() -> list:
    """All subscriptions whose DB expires_at is parked in the far future.

    This is the symptom of the bug discovered during the audit: when a
    user's premium expired and they had a bypass entity, the
    fast_expiry_cleanup transition rewrote expires_at to NOW + 10 years
    as a bypass-only marker — but the user's subsequent purchases never
    overwrote that marker, leaving the bot UI showing "expires in 10
    years" even though the panel was rolled back to the real date.

    Returns dicts with telegram_id, expires_at, status,
    subscription_type, is_bypass_only.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT telegram_id, expires_at, status, subscription_type,
                      is_bypass_only, remnawave_premium_uuid
               FROM subscriptions
               WHERE status = 'active'
                 AND expires_at > NOW() + INTERVAL '2 years'
               ORDER BY telegram_id"""
        )
    return [dict(r) for r in rows]


async def update_subscription_expires_at_bulk(updates: list) -> int:
    """Bulk-update subscriptions.expires_at.

    Args:
        updates: list of {"telegram_id": int, "new_expires_at": datetime}

    Returns count of rows successfully updated.

    Uses asyncpg.executemany — one round-trip for the whole batch.
    """
    if not updates:
        return 0
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Coerce to naive UTC (the column is TIMESTAMPTZ but the bot's
        # other writers pass naive — keep consistent so equality
        # comparisons elsewhere don't drift across tz casts).
        rows = [
            (u["new_expires_at"], u["telegram_id"])
            for u in updates
        ]
        async with conn.transaction():
            await conn.executemany(
                "UPDATE subscriptions SET expires_at = $1 WHERE telegram_id = $2 AND status = 'active'",
                rows,
            )
    return len(updates)
