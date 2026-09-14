"""Sales funnel state (owner 2026-09-14, docs/audit/SCOPE.md «Воронка продаж»).

Who is in which chain is read from DB state only — no hooks in the existing
workers:

  start — /start done (captcha passed), no trial ever, no subscription row ever,
          no paid payment. Anchor: users.created_at.
  trial — the trial ended (users.trial_expires_at in the past), no paid payment
          since the trial started, no active premium now. Anchor: trial end.
  paid  — the last history period (subscription_history, max end_date) ended,
          the user paid at least once (purchase / renewal / auto_renew), no
          active premium now, no paid payment after that end. Anchor: that end.
          (The subscriptions row of a bypass-only user carries a +10 years
          placeholder; the history keeps the real end.)

«Active premium» = a subscriptions row active until later than now that is not
bypass-only and not a trial. Only events at or after the cut-off (app_settings
sales_funnel_started_at, the first worker pass) enter a chain — the old base is
never messaged retroactively; it gets one dashboard campaign (segments in
database.admin).

Time: every parameter is an AWARE UTC datetime cast to timestamptz; naive
columns (users.trial_*) are compared as UTC like the rest of the code base.
No function here does HTTP or Telegram I/O.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from database.core import get_pool

logger = logging.getLogger(__name__)

STARTED_AT_KEY = "sales_funnel_started_at"
CHAINS = ("start", "trial", "paid")

_PAID_PAYMENT = "p.status IN ('paid', 'approved')"
_PAID_HISTORY = "('purchase', 'renewal', 'auto_renew')"

# $1 = now, $2 = lower bound of the anchor (cut-off / look-back window).
_ACTIVE_PREMIUM = """EXISTS (
        SELECT 1 FROM subscriptions s
        WHERE s.telegram_id = u.telegram_id
          AND s.status = 'active'
          AND s.expires_at > $1::timestamptz
          AND COALESCE(s.is_bypass_only, FALSE) = FALSE
          AND COALESCE(s.source, '') NOT IN ('bypass_only', 'trial'))"""

# Candidate SQL per chain → (telegram_id, anchor_at). {user} / {hist_user}
# become "AND … = $N" for the single-user recheck right before a send.
_CANDIDATES: Dict[str, str] = {
    "start": f"""
        SELECT u.telegram_id, u.created_at::timestamptz AS anchor_at
        FROM users u
        WHERE u.created_at >= $2::timestamptz
          AND u.created_at <= $1::timestamptz
          AND u.trial_used_at IS NULL
          AND u.captcha_passed_at IS NOT NULL
          AND COALESCE(u.is_reachable, TRUE) = TRUE
          AND NOT EXISTS (SELECT 1 FROM subscriptions s WHERE s.telegram_id = u.telegram_id)
          AND NOT EXISTS (SELECT 1 FROM payments p WHERE p.telegram_id = u.telegram_id AND {_PAID_PAYMENT})
          {{user}}""",
    "trial": f"""
        SELECT u.telegram_id, u.trial_expires_at::timestamptz AS anchor_at
        FROM users u
        WHERE u.trial_expires_at >= ($2::timestamptz AT TIME ZONE 'UTC')
          AND u.trial_expires_at <= ($1::timestamptz AT TIME ZONE 'UTC')
          AND u.trial_used_at IS NOT NULL
          AND COALESCE(u.is_reachable, TRUE) = TRUE
          AND NOT EXISTS (
              SELECT 1 FROM payments p
              WHERE p.telegram_id = u.telegram_id AND {_PAID_PAYMENT}
                AND p.created_at >= u.trial_used_at)
          AND NOT {_ACTIVE_PREMIUM}
          {{user}}""",
    "paid": f"""
        SELECT h.telegram_id, h.last_end AS anchor_at
        FROM (
            SELECT sh.telegram_id,
                   MAX(sh.end_date)::timestamptz AS last_end,
                   BOOL_OR(sh.action_type IN {_PAID_HISTORY}) AS was_paid
            FROM subscription_history sh
            WHERE sh.telegram_id IN (
                    SELECT DISTINCT e.telegram_id FROM subscription_history e
                    WHERE e.end_date >= $2::timestamptz AND e.end_date <= $1::timestamptz)
              AND sh.action_type NOT IN ('trial', 'bypass_only')
              {{hist_user}}
            GROUP BY sh.telegram_id
        ) h
        JOIN users u ON u.telegram_id = h.telegram_id
        WHERE h.was_paid
          AND h.last_end >= $2::timestamptz
          AND h.last_end <= $1::timestamptz
          AND COALESCE(u.is_reachable, TRUE) = TRUE
          AND NOT {_ACTIVE_PREMIUM}
          AND NOT EXISTS (
              SELECT 1 FROM payments p
              WHERE p.telegram_id = u.telegram_id AND {_PAID_PAYMENT}
                AND p.created_at > h.last_end)
          {{user}}""",
}


def candidates_sql(chain: str, *, single_user_param: Optional[int] = None) -> str:
    """The chain's eligibility SELECT; with single_user_param=N it is limited
    to telegram_id = $N (the recheck right before a send)."""
    sql = _CANDIDATES[chain]
    if single_user_param is None:
        return sql.format(user="", hist_user="")
    return sql.format(user=f"AND u.telegram_id = ${single_user_param}",
                      hist_user=f"AND sh.telegram_id = ${single_user_param}")


def _due_sql(chain: str) -> str:
    # $3 step names, $4 offsets (seconds) in chain order, $5 chain,
    # $6 start of today (MSK) for the daily cap, $7 batch size.
    return f"""
        WITH cand AS ({candidates_sql(chain)}),
        due AS (
            SELECT c.telegram_id, c.anchor_at,
                   (SELECT st.step
                    FROM unnest($3::text[], $4::float8[]) WITH ORDINALITY AS st(step, off_s, ord)
                    WHERE c.anchor_at + make_interval(secs => st.off_s) <= $1::timestamptz
                    ORDER BY st.ord DESC LIMIT 1) AS step
            FROM cand c
        )
        SELECT d.telegram_id, d.anchor_at, d.step
        FROM due d
        WHERE d.step IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM funnel_messages f
              WHERE f.telegram_id = d.telegram_id AND f.chain = $5
                AND f.anchor_at = d.anchor_at AND f.step = d.step)
          AND NOT EXISTS (
              SELECT 1 FROM funnel_messages f
              WHERE f.telegram_id = d.telegram_id
                AND f.status IN ('claimed', 'sent')
                AND f.sent_at >= $6::timestamptz)
        ORDER BY d.anchor_at
        LIMIT $7"""


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


async def get_or_init_started_at(now: datetime) -> datetime:
    """The funnel cut-off: written once (the first worker pass), then read."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO app_settings (key, value) VALUES ($1, $2) ON CONFLICT (key) DO NOTHING",
            STARTED_AT_KEY, _aware(now).isoformat(),
        )
        value = await conn.fetchval("SELECT value FROM app_settings WHERE key = $1", STARTED_AT_KEY)
    return _aware(datetime.fromisoformat(value))


async def fetch_due(chain: str, *, now: datetime, lower: datetime, steps: Sequence[Tuple[str, float]],
                    day_start: datetime, limit: int) -> List[Dict[str, Any]]:
    """Users of `chain` whose latest due step was not sent yet and who got no
    funnel message today. `steps` = (name, offset seconds) in chain order."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            _due_sql(chain), _aware(now), _aware(lower),
            [n for n, _ in steps], [float(s) for _, s in steps], chain, _aware(day_start), int(limit),
        )
    return [{"telegram_id": r["telegram_id"], "anchor_at": _aware(r["anchor_at"]), "step": r["step"]}
            for r in rows]


async def claim(telegram_id: int, chain: str, anchor_at: datetime, step: str, earlier_steps: Sequence[str], *,
                now: datetime, lower: datetime, day_start: datetime,
                other_since: datetime) -> Tuple[Optional[int], str]:
    """Claim one step for sending — in ONE transaction, before any I/O:

    * the chain rule still holds for this user and this event (a purchase or a
      trial activation since the batch was read stops the chain here);
    * no funnel message today (MSK) and no other lifecycle notification in the
      last hours (automated_notification_sends, funnel keys excluded, and the
      traffic notices — users.traffic_notice_last_at);
    * earlier unsent steps are marked skipped (the latest due step wins);
    * the row is inserted — ON CONFLICT DO NOTHING: an overlapping pass or a
      restart never sends the step twice.

    Returns (row id, "claimed") or (None, reason)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext('sales_funnel'), ($1 % 2147483647)::int)",
                               telegram_id)
            row = await conn.fetchrow(candidates_sql(chain, single_user_param=3),
                                      _aware(now), _aware(lower), telegram_id)
            if row is None or _aware(row["anchor_at"]) != _aware(anchor_at):
                return None, "stopped"
            if await conn.fetchval(
                """SELECT 1 FROM funnel_messages
                   WHERE telegram_id = $1 AND status IN ('claimed', 'sent') AND sent_at >= $2::timestamptz
                   LIMIT 1""",
                telegram_id, _aware(day_start),
            ):
                return None, "daily_cap"
            # Other notifications: everything logged to automated_notification_sends
            # (reminders, trial, «subscription ended», auto-renewal) and the traffic
            # notices, whose send time is users.traffic_notice_last_at (naive UTC).
            if await conn.fetchval(
                """SELECT 1 WHERE EXISTS (
                       SELECT 1 FROM automated_notification_sends
                       WHERE telegram_id = $1 AND status = 'sent' AND sent_at >= $2::timestamptz
                         AND key NOT LIKE 'funnel.%')
                   OR EXISTS (
                       SELECT 1 FROM users
                       WHERE telegram_id = $1
                         AND traffic_notice_last_at >= ($2::timestamptz AT TIME ZONE 'UTC'))""",
                telegram_id, _aware(other_since),
            ):
                return None, "other_notification"
            for name in earlier_steps:
                await conn.execute(
                    """INSERT INTO funnel_messages (telegram_id, chain, anchor_at, step, status, sent_at)
                       VALUES ($1, $2, $3, $4, 'skipped', $5)
                       ON CONFLICT (telegram_id, chain, anchor_at, step) DO NOTHING""",
                    telegram_id, chain, _aware(anchor_at), name, _aware(now),
                )
            claim_id = await conn.fetchval(
                """INSERT INTO funnel_messages (telegram_id, chain, anchor_at, step, status, sent_at)
                   VALUES ($1, $2, $3, $4, 'claimed', $5)
                   ON CONFLICT (telegram_id, chain, anchor_at, step) DO NOTHING
                   RETURNING id""",
                telegram_id, chain, _aware(anchor_at), step, _aware(now),
            )
    if claim_id is None:
        return None, "duplicate"
    return int(claim_id), "claimed"


async def finish(claim_id: int, status: str, discount_percent: Optional[int] = None,
                 discount_expires_at: Optional[datetime] = None) -> None:
    """Record the outcome of a claimed step (sent / failed / skipped)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """UPDATE funnel_messages
               SET status = $2, discount_percent = $3, discount_expires_at = $4
               WHERE id = $1""",
            claim_id, status, discount_percent, _aware(discount_expires_at),
        )


__all__ = ["CHAINS", "STARTED_AT_KEY", "candidates_sql", "claim", "fetch_due", "finish",
           "get_or_init_started_at"]
