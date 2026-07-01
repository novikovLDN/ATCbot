"""Subscription reconciliation & over-issuance audit helpers.

Backs the admin dashboard's «Сверка» screen. Three responsibilities:

1. `find_over_issuance_candidates()` — list users whose PREMIUM subscription
   currently expires more than 8 years in the future. Bypass-only rows are
   filtered out (they intentionally sit at NOW + 10y).

2. `get_reconciliation_detail(telegram_id)` — for one user, pull:
   • the current subscription row,
   • all approved subscription payments (basic_*/plus_*/combo_* — excluding
     gifts/topups/traffic packs),
   • admin grants captured in `subscriptions.admin_grant_days`,
   • the delta between actual and expected expiry.

3. `apply_reconciliation_fix(...)` — inside a single transaction:
   • recompute the expected expiry from paid days + admin_grant_days,
   • update `subscriptions.expires_at` to that value (never earlier than
     activated_at + paid duration, never in the past — we clamp to
     `activated_at + total_days`),
   • insert a row into `subscription_reconciliation_log` with proof.

Over-issuance events are written by `record_over_issuance()` — called by
`app.services.subscription_watchdog` after every write to `expires_at`.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import asyncpg

from database.core import get_pool, _to_db_utc, _from_db_utc

logger = logging.getLogger(__name__)


# Threshold — anything above this from NOW is considered suspicious.
_EIGHT_YEARS = timedelta(days=365 * 8)

# Max parallel Remnawave API calls when cross-checking candidate panel dates.
_PANEL_FETCH_CONCURRENCY = 8


# ──────────────────────────────────────────────────────────────────────
#  Remnawave premium entity — source of truth for actual expireAt
# ──────────────────────────────────────────────────────────────────────

async def _fetch_panel_expires_at(
    telegram_id: int,
    remnawave_premium_uuid: Optional[str],
) -> Optional[datetime]:
    """Fetch the Remnawave premium entity's `expireAt` — this is the
    authoritative expiration for VPN access. The bot's `subscriptions.expires_at`
    can go stale (leftover from bypass-only transitions, migration back-fills,
    admin scripts, …); the panel value is what actually controls the user.

    Lookup order:
      1. by cached `remnawave_premium_uuid` (fast — direct GET /api/users/{uuid})
      2. by username `tg_{telegram_id}_premium` (fallback for rows where the
         uuid was never cached).

    Returns None on any failure — the caller then falls back to the DB value
    (i.e. keeps the row as a candidate so it is not silently dropped)."""
    try:
        from app.services import remnawave_api
        from app.services.remnawave_premium import build_premium_username
    except Exception as e:
        logger.warning("reconciliation: remnawave_api import failed: %s", e)
        return None

    payload = None
    if remnawave_premium_uuid:
        try:
            payload = await remnawave_api.get_user(remnawave_premium_uuid)
        except Exception as e:
            logger.debug(
                "reconciliation: get_user(uuid=%s) failed for tg=%s: %s",
                remnawave_premium_uuid[:8], telegram_id, e,
            )

    if not payload:
        try:
            payload = await remnawave_api.find_user_by_username(
                build_premium_username(telegram_id)
            )
        except Exception as e:
            logger.debug(
                "reconciliation: find_user_by_username failed for tg=%s: %s",
                telegram_id, e,
            )
            return None

    if not payload:
        return None

    raw = payload.get("expireAt") or payload.get("expire_at")
    if not raw:
        return None
    try:
        # Remnawave returns ISO-8601 (usually with trailing 'Z').
        if isinstance(raw, str) and raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = datetime.fromisoformat(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


async def _bulk_fetch_panel_expires_at(
    entries: List[Dict[str, Any]],
) -> Dict[int, Optional[datetime]]:
    """Fetch Remnawave `expireAt` for many candidates in parallel with a
    concurrency cap so we don't hammer the panel. Returns a dict
    `telegram_id → datetime | None`."""
    if not entries:
        return {}

    sem = asyncio.Semaphore(_PANEL_FETCH_CONCURRENCY)

    async def _one(row: Dict[str, Any]):
        tg = row["telegram_id"]
        uuid = row.get("remnawave_premium_uuid")
        async with sem:
            dt = await _fetch_panel_expires_at(tg, uuid)
        return tg, dt

    results = await asyncio.gather(*[_one(r) for r in entries], return_exceptions=True)
    out: Dict[int, Optional[datetime]] = {}
    for res in results:
        if isinstance(res, Exception):
            continue
        tg, dt = res
        out[tg] = dt
    return out


# ──────────────────────────────────────────────────────────────────────
#  1. Candidates (list)
# ──────────────────────────────────────────────────────────────────────

import re

# Matches the default premium-entity username pattern `tg_{telegram_id}_premium`.
# See app/services/remnawave_premium.py:build_premium_username. If deployment
# uses a custom REMNAWAVE_PREMIUM_USERNAME_PATTERN, the tail/head is customised
# but the telegram_id digits are always present as the numeric group.
_PREMIUM_USERNAME_RE = re.compile(r"^tg_(\d+)_premium$")


def _parse_remnawave_dt(raw) -> Optional[datetime]:
    """Parse Remnawave-returned expireAt into a UTC-aware datetime."""
    if not raw:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


async def find_over_issuance_candidates(limit: int = 200) -> List[Dict[str, Any]]:
    """List users whose Remnawave premium entity (`tg_{telegram_id}_premium`)
    has expireAt > NOW + 8 years.

    Panel-driven: the Remnawave panel is the source of truth for real VPN
    access, so we scan it directly and then enrich with bot-DB data.
    The alternative (start from `subscriptions.expires_at > NOW+8y`) misses
    users where the bot DB was already patched but the panel still carries
    the anomaly.

    Ordering: most-suspicious first (largest panel expires_at).

    Bypass-only DB rows would legitimately have expires_at at NOW+10y — but
    those users don't own a `tg_<id>_premium` entity, so they never appear
    in this list.
    """
    pool = await get_pool()
    if pool is None:
        return []
    now = datetime.now(timezone.utc)
    cutoff = now + _EIGHT_YEARS

    # ── Step 1: scan the Remnawave panel ──────────────────────────────
    try:
        from app.services import remnawave_api
    except Exception as e:
        logger.error("find_over_issuance_candidates: remnawave_api import failed: %s", e)
        return []

    all_users = await remnawave_api.get_all_users()
    if all_users is None:
        # Cannot list — fail loudly with a marker row so the dashboard
        # renders a warning rather than an empty list masquerading as OK.
        logger.error(
            "find_over_issuance_candidates: get_all_users returned None — panel unreachable"
        )
        return [{
            "telegram_id": 0,
            "username": None,
            "subscription_type": None,
            "source": None,
            "status": None,
            "admin_grant_days": None,
            "is_bypass_only": False,
            "expires_at": None,
            "panel_expires_at": None,
            "panel_available": False,
            "activated_at": None,
            "days_from_now": 0,
            "years_from_now": 0,
            "panel_unreachable": True,
        }]

    over_from_panel: List[Dict[str, Any]] = []
    for u in all_users:
        username = (u.get("username") or "").strip()
        m = _PREMIUM_USERNAME_RE.match(username)
        if not m:
            continue
        try:
            tg_id = int(m.group(1))
        except (ValueError, TypeError):
            continue
        panel_expires_at = _parse_remnawave_dt(u.get("expireAt"))
        if not panel_expires_at or panel_expires_at <= cutoff:
            continue
        over_from_panel.append({
            "telegram_id": tg_id,
            "panel_username": username,
            "panel_expires_at": panel_expires_at,
            "panel_uuid": u.get("uuid"),
            "panel_status": u.get("status"),
        })

    if not over_from_panel:
        return []

    over_from_panel.sort(key=lambda x: x["panel_expires_at"], reverse=True)
    over_from_panel = over_from_panel[:limit]

    # ── Step 2: enrich with bot-DB (subscriptions + users) ────────────
    tg_ids = [x["telegram_id"] for x in over_from_panel]
    async with pool.acquire() as conn:
        try:
            rows = await conn.fetch(
                """SELECT
                       s.telegram_id,
                       s.expires_at,
                       s.activated_at,
                       s.subscription_type,
                       s.source,
                       s.status,
                       s.admin_grant_days,
                       s.remnawave_premium_uuid,
                       COALESCE(s.is_bypass_only, FALSE) AS is_bypass_only,
                       COALESCE(u.username, '') AS username
                   FROM subscriptions s
                   LEFT JOIN users u ON u.telegram_id = s.telegram_id
                   WHERE s.telegram_id = ANY($1::bigint[])""",
                tg_ids,
            )
        except (asyncpg.UndefinedColumnError, asyncpg.PostgresError) as e:
            logger.warning(
                "find_over_issuance_candidates: DB enrichment failed: %s", e,
            )
            rows = []

    db_map = {r["telegram_id"]: dict(r) for r in rows}

    out: List[Dict[str, Any]] = []
    for entry in over_from_panel:
        tg = entry["telegram_id"]
        db = db_map.get(tg) or {}
        db_expires_at = (
            _from_db_utc(db["expires_at"]) if db.get("expires_at") else None
        )
        panel_expires_at = entry["panel_expires_at"]
        panel_days = (panel_expires_at - now).days

        out.append({
            "telegram_id": tg,
            "username": (db.get("username") or None) or None,
            "subscription_type": db.get("subscription_type"),
            "source": db.get("source"),
            "status": db.get("status"),
            "admin_grant_days": db.get("admin_grant_days"),
            "is_bypass_only": db.get("is_bypass_only", False),
            "expires_at": db_expires_at.isoformat() if db_expires_at else None,
            "panel_expires_at": panel_expires_at.isoformat(),
            "panel_available": True,
            "panel_username": entry["panel_username"],
            "activated_at": (
                _from_db_utc(db["activated_at"]).isoformat()
                if db.get("activated_at") else None
            ),
            "days_from_now": panel_days,
            "years_from_now": round(panel_days / 365.0, 2),
            "db_row_missing": tg not in db_map,
        })

    return out


# ──────────────────────────────────────────────────────────────────────
#  2. Detail — expected vs actual for one user
# ──────────────────────────────────────────────────────────────────────

def _extract_period_days_from_tariff(tariff: str) -> Optional[int]:
    """Parse `basic_30`, `plus_365`, `combo_basic_180` etc. into period days.

    Returns None for anything that isn't a subscription-time payment (traffic
    packs, gifts, topups, bypass GB packs).
    """
    if not tariff or tariff == "balance_topup":
        return None
    if tariff.startswith(("gift_", "traffic_", "bypass_", "farm_", "apple_", "steam_")):
        return None
    parts = tariff.split("_")
    if not parts:
        return None
    # combo_basic_180 → last part; basic_30 → last part; plus_365 → last part.
    try:
        days = int(parts[-1])
    except ValueError:
        return None
    # Sanity: subscription periods are 30/90/180/365 in prod. Anything above
    # 730 days from a single payment is almost certainly a parse artefact.
    if 1 <= days <= 730:
        return days
    return None


async def get_reconciliation_detail(telegram_id: int) -> Dict[str, Any]:
    """Full reconciliation snapshot for a single user."""
    pool = await get_pool()
    if pool is None:
        return {}
    now = datetime.now(timezone.utc)

    async with pool.acquire() as conn:
        sub_row = await conn.fetchrow(
            """SELECT telegram_id, expires_at, activated_at, subscription_type,
                      source, status, admin_grant_days, remnawave_premium_uuid,
                      COALESCE(is_bypass_only, FALSE) AS is_bypass_only
               FROM subscriptions
               WHERE telegram_id = $1""",
            telegram_id,
        )
        if not sub_row:
            # No bot-DB row — user may still exist in the Remnawave panel
            # (that's exactly the case we want to surface). Return an empty
            # snapshot with panel data so the dashboard can still render.
            panel_expires_at = await _fetch_panel_expires_at(telegram_id, None)
            panel_days_from_now = (
                (panel_expires_at - now).days if panel_expires_at else None
            )
            return {
                "telegram_id": telegram_id,
                "found": bool(panel_expires_at),
                "db_row_missing": True,
                "subscription": {
                    "expires_at": None,
                    "activated_at": None,
                    "subscription_type": None,
                    "source": None,
                    "status": None,
                    "is_bypass_only": False,
                    "admin_grant_days": 0,
                },
                "panel": {
                    "expires_at": (
                        panel_expires_at.isoformat() if panel_expires_at else None
                    ),
                    "days_from_now": panel_days_from_now,
                    "available": panel_expires_at is not None,
                    "matches_db": False,
                },
                "payments": [],
                "total_paid_days": 0,
                "actual_days_from_now": 0,
                "expected_days_from_now": 0,
                "expected_expires_at": now.isoformat(),
                "delta_days": 0,
                "over_issuance_events": [],
            }

        payment_rows = await conn.fetch(
            """SELECT id, tariff, amount, status, paid_at, created_at, purchase_id
               FROM payments
               WHERE telegram_id = $1
                 AND status = 'approved'
               ORDER BY COALESCE(paid_at, created_at) ASC""",
            telegram_id,
        )

        over_rows = await conn.fetch(
            """SELECT id, created_at, grant_action, source, tariff,
                      old_expires_at, new_expires_at, duration_added_seconds,
                      admin_telegram_id, admin_grant_days, caller_context
               FROM subscription_over_issuance_log
               WHERE telegram_id = $1
               ORDER BY created_at DESC
               LIMIT 20""",
            telegram_id,
        )

    expires_at = _from_db_utc(sub_row["expires_at"])
    activated_at = _from_db_utc(sub_row["activated_at"]) if sub_row["activated_at"] else None
    admin_grant_days = sub_row["admin_grant_days"] or 0

    total_paid_days = 0
    proof_payments: List[Dict[str, Any]] = []
    for p in payment_rows:
        tariff = (p["tariff"] or "").strip()
        period_days = _extract_period_days_from_tariff(tariff)
        item = {
            "id": p["id"],
            "tariff": tariff,
            "amount_rubles": (p["amount"] or 0) / 100.0,
            "status": p["status"],
            "paid_at": (
                _from_db_utc(p["paid_at"]).isoformat()
                if p["paid_at"] else None
            ),
            "created_at": (
                _from_db_utc(p["created_at"]).isoformat()
                if p["created_at"] else None
            ),
            "purchase_id": p["purchase_id"],
            "period_days": period_days,
            "counted": bool(period_days),
        }
        if period_days:
            total_paid_days += period_days
            proof_payments.append(item)
        else:
            # Non-counted (traffic pack / gift / topup) — still surface for context.
            proof_payments.append(item)

    # Expected expiry = activated_at + total_paid_days + admin_grant_days.
    # If activated_at is unknown, fall back to earliest paid_at.
    base_start = activated_at
    if base_start is None and proof_payments:
        first_paid = next(
            (
                _from_db_utc_str(p["paid_at"]) or _from_db_utc_str(p["created_at"])
                for p in proof_payments if p.get("counted")
            ),
            None,
        )
        base_start = first_paid
    if base_start is None:
        base_start = now  # last-resort: treat as starting today

    total_days = total_paid_days + int(admin_grant_days or 0)
    expected_expires_at = base_start + timedelta(days=total_days)

    actual_days_from_now = (expires_at - now).days if expires_at else 0
    expected_days_from_now = (expected_expires_at - now).days
    delta_days = actual_days_from_now - expected_days_from_now

    over_issuance_events = []
    for e in over_rows:
        over_issuance_events.append({
            "id": e["id"],
            "created_at": _from_db_utc(e["created_at"]).isoformat() if e["created_at"] else None,
            "grant_action": e["grant_action"],
            "source": e["source"],
            "tariff": e["tariff"],
            "old_expires_at": (
                _from_db_utc(e["old_expires_at"]).isoformat()
                if e["old_expires_at"] else None
            ),
            "new_expires_at": _from_db_utc(e["new_expires_at"]).isoformat(),
            "duration_added_seconds": e["duration_added_seconds"],
            "admin_telegram_id": e["admin_telegram_id"],
            "admin_grant_days": e["admin_grant_days"],
            "caller_context": e["caller_context"],
        })

    # Cross-check with the Remnawave premium entity — real source of truth
    # for VPN access. Falls back to None on any panel API failure.
    panel_expires_at = await _fetch_panel_expires_at(
        telegram_id, sub_row["remnawave_premium_uuid"],
    )
    panel_days_from_now = (
        (panel_expires_at - now).days if panel_expires_at else None
    )
    # If panel disagrees with DB by more than a day, the DB is likely stale.
    panel_matches_db = (
        panel_expires_at is not None
        and expires_at is not None
        and abs((panel_expires_at - expires_at).total_seconds()) < 86400
    )

    return {
        "telegram_id": telegram_id,
        "found": True,
        "subscription": {
            "expires_at": expires_at.isoformat() if expires_at else None,
            "activated_at": activated_at.isoformat() if activated_at else None,
            "subscription_type": sub_row["subscription_type"],
            "source": sub_row["source"],
            "status": sub_row["status"],
            "is_bypass_only": sub_row["is_bypass_only"],
            "admin_grant_days": admin_grant_days,
        },
        "panel": {
            "expires_at": panel_expires_at.isoformat() if panel_expires_at else None,
            "days_from_now": panel_days_from_now,
            "available": panel_expires_at is not None,
            "matches_db": panel_matches_db,
        },
        "payments": proof_payments,
        "total_paid_days": total_paid_days,
        "actual_days_from_now": actual_days_from_now,
        "expected_days_from_now": expected_days_from_now,
        "expected_expires_at": expected_expires_at.isoformat(),
        "delta_days": delta_days,
        "over_issuance_events": over_issuance_events,
    }


def _from_db_utc_str(iso: Optional[str]) -> Optional[datetime]:
    """Parse an ISO-8601 string (as saved by proof_payments) back into a
    timezone-aware datetime. Small helper used only by get_reconciliation_detail
    for computing base_start from the earliest counted payment."""
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# ──────────────────────────────────────────────────────────────────────
#  3. Fix — apply reconciliation
# ──────────────────────────────────────────────────────────────────────

async def apply_reconciliation_fix(
    telegram_id: int,
    admin_telegram_id: int,
    *,
    reason: str = "manual reconciliation via dashboard",
) -> Dict[str, Any]:
    """Recompute expires_at from approved payments + admin_grant_days, apply
    the correction in a single transaction, and log the before/after.

    Returns a dict describing the outcome — see below.
    """
    pool = await get_pool()
    if pool is None:
        return {"success": False, "error": "db_unavailable"}

    now = datetime.now(timezone.utc)

    async with pool.acquire() as conn:
        async with conn.transaction():
            sub_row = await conn.fetchrow(
                """SELECT expires_at, activated_at, admin_grant_days,
                          COALESCE(is_bypass_only, FALSE) AS is_bypass_only
                   FROM subscriptions
                   WHERE telegram_id = $1
                   FOR UPDATE""",
                telegram_id,
            )
            if not sub_row:
                return {"success": False, "error": "no_subscription"}
            if sub_row["is_bypass_only"]:
                return {
                    "success": False,
                    "error": "bypass_only_subscription_skipped",
                }

            old_expires_at = _from_db_utc(sub_row["expires_at"])
            activated_at = _from_db_utc(sub_row["activated_at"]) if sub_row["activated_at"] else None
            admin_grant_days = sub_row["admin_grant_days"] or 0

            payment_rows = await conn.fetch(
                """SELECT id, tariff, COALESCE(paid_at, created_at) AS effective_at
                   FROM payments
                   WHERE telegram_id = $1
                     AND status = 'approved'
                   ORDER BY COALESCE(paid_at, created_at) ASC""",
                telegram_id,
            )

            proof_ids: List[int] = []
            total_paid_days = 0
            earliest_effective: Optional[datetime] = None
            for p in payment_rows:
                period_days = _extract_period_days_from_tariff((p["tariff"] or "").strip())
                if not period_days:
                    continue
                proof_ids.append(p["id"])
                total_paid_days += period_days
                eff = _from_db_utc(p["effective_at"]) if p["effective_at"] else None
                if eff and (earliest_effective is None or eff < earliest_effective):
                    earliest_effective = eff

            base_start = activated_at or earliest_effective or now
            total_days = total_paid_days + int(admin_grant_days or 0)
            new_expires_at = base_start + timedelta(days=total_days)

            # Safety guards:
            #  – never move expires_at further into the future than it was.
            #  – if the calculated new_expires_at is in the past (e.g. user paid
            #    for basic_30 four years ago and never renewed), we still write
            #    it — the standard expiry cleanup worker will pick it up next
            #    cycle and either mark expired or transition to bypass-only.
            if old_expires_at and new_expires_at > old_expires_at:
                return {
                    "success": False,
                    "error": "would_extend_not_shorten",
                    "old_expires_at": old_expires_at.isoformat(),
                    "new_expires_at": new_expires_at.isoformat(),
                }

            days_removed = (
                (old_expires_at - new_expires_at).days
                if old_expires_at else 0
            )

            await conn.execute(
                """UPDATE subscriptions
                   SET expires_at = $1
                   WHERE telegram_id = $2""",
                _to_db_utc(new_expires_at),
                telegram_id,
            )

            log_id = await conn.fetchval(
                """INSERT INTO subscription_reconciliation_log (
                       telegram_id, old_expires_at, new_expires_at,
                       old_days_from_now, new_days_from_now, days_removed,
                       reason, proof_payment_ids, total_paid_days,
                       admin_grant_days_kept, admin_telegram_id
                   )
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                   RETURNING id""",
                telegram_id,
                _to_db_utc(old_expires_at) if old_expires_at else _to_db_utc(now),
                _to_db_utc(new_expires_at),
                (old_expires_at - now).days if old_expires_at else 0,
                (new_expires_at - now).days,
                days_removed,
                reason,
                proof_ids,
                total_paid_days,
                int(admin_grant_days or 0),
                admin_telegram_id,
            )

    logger.info(
        "RECONCILIATION_FIX_APPLIED user=%s old=%s new=%s removed_days=%s "
        "total_paid_days=%s admin_grant_days=%s proof_ids=%s log_id=%s",
        telegram_id,
        old_expires_at.isoformat() if old_expires_at else None,
        new_expires_at.isoformat(),
        days_removed,
        total_paid_days,
        admin_grant_days,
        proof_ids,
        log_id,
    )

    return {
        "success": True,
        "log_id": log_id,
        "old_expires_at": old_expires_at.isoformat() if old_expires_at else None,
        "new_expires_at": new_expires_at.isoformat(),
        "days_removed": days_removed,
        "total_paid_days": total_paid_days,
        "admin_grant_days_kept": int(admin_grant_days or 0),
        "proof_payment_ids": proof_ids,
    }


# ──────────────────────────────────────────────────────────────────────
#  4. Audit logs (list)
# ──────────────────────────────────────────────────────────────────────

async def list_reconciliation_log(limit: int = 100) -> List[Dict[str, Any]]:
    pool = await get_pool()
    if pool is None:
        return []
    async with pool.acquire() as conn:
        try:
            rows = await conn.fetch(
                """SELECT id, telegram_id, old_expires_at, new_expires_at,
                          old_days_from_now, new_days_from_now, days_removed,
                          reason, proof_payment_ids, total_paid_days,
                          admin_grant_days_kept, admin_telegram_id, created_at
                   FROM subscription_reconciliation_log
                   ORDER BY created_at DESC
                   LIMIT $1""",
                limit,
            )
        except asyncpg.UndefinedTableError:
            return []
    return [_serialize(r) for r in rows]


async def list_over_issuance_log(limit: int = 100) -> List[Dict[str, Any]]:
    pool = await get_pool()
    if pool is None:
        return []
    async with pool.acquire() as conn:
        try:
            rows = await conn.fetch(
                """SELECT id, telegram_id, old_expires_at, new_expires_at,
                          duration_added_seconds, grant_action, source, tariff,
                          admin_telegram_id, admin_grant_days,
                          caller_context, created_at
                   FROM subscription_over_issuance_log
                   ORDER BY created_at DESC
                   LIMIT $1""",
                limit,
            )
        except asyncpg.UndefinedTableError:
            return []
    return [_serialize(r) for r in rows]


# ──────────────────────────────────────────────────────────────────────
#  5. Over-issuance recording (called from subscription_watchdog)
# ──────────────────────────────────────────────────────────────────────

async def record_over_issuance(
    telegram_id: int,
    *,
    old_expires_at: Optional[datetime],
    new_expires_at: datetime,
    grant_action: str,
    source: Optional[str],
    tariff: Optional[str],
    admin_telegram_id: Optional[int],
    admin_grant_days: Optional[int],
    caller_context: Optional[str],
) -> Optional[int]:
    """Insert one over-issuance log row. Fire-and-forget — never raises."""
    pool = await get_pool()
    if pool is None:
        return None
    try:
        duration_added = None
        if new_expires_at and old_expires_at:
            duration_added = int((new_expires_at - old_expires_at).total_seconds())
        elif new_expires_at:
            duration_added = int(
                (new_expires_at - datetime.now(timezone.utc)).total_seconds()
            )
        async with pool.acquire() as conn:
            log_id = await conn.fetchval(
                """INSERT INTO subscription_over_issuance_log (
                       telegram_id, old_expires_at, new_expires_at,
                       duration_added_seconds, grant_action, source, tariff,
                       admin_telegram_id, admin_grant_days, caller_context
                   )
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                   RETURNING id""",
                telegram_id,
                _to_db_utc(old_expires_at) if old_expires_at else None,
                _to_db_utc(new_expires_at),
                duration_added,
                grant_action,
                source,
                tariff,
                admin_telegram_id,
                admin_grant_days,
                (caller_context or "")[:2000],
            )
        return log_id
    except Exception as e:
        logger.warning(
            "record_over_issuance failed user=%s: %s", telegram_id, e,
        )
        return None


def _serialize(row) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in row.items():
        if hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        elif isinstance(v, (bytes, bytearray)):
            continue
        else:
            out[k] = v
    return out


__all__ = [
    "find_over_issuance_candidates",
    "get_reconciliation_detail",
    "apply_reconciliation_fix",
    "list_reconciliation_log",
    "list_over_issuance_log",
    "record_over_issuance",
]
