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

3. Premium repair — ONE rule (`compute_repair_target`) shared by the
   dashboard fix (`apply_reconciliation_fix`) and the bulk script
   (`app/services/premium_repair`, `scripts/fix_premium_over_issuance.py`):
   • target = max(date by approved payments + admin_grant_days, a sane
     bot-DB date); none / past → NOW + 1 day; never extends the panel,
   • PATCH `{id, expireAt}` on the premium entity (found by username),
   • then, in one short transaction: a `subscription_reconciliation_log`
     row with proof, and a leaked (> 5y, not bypass-only) DB date is
     shortened to the same value.

Over-issuance events are written by `record_over_issuance()` — called by
`app.services.subscription_watchdog` after every write to `expires_at`.
"""
from __future__ import annotations
from app.services.tariffs import extend_expiry, normalize_tier

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import asyncpg

from database.core import get_pool, _to_db_utc, _from_db_utc

logger = logging.getLogger(__name__)


# Threshold — anything above this from NOW is considered suspicious.
_EIGHT_YEARS = timedelta(days=365 * 8)


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
            "subscription_type": normalize_tier(db.get("subscription_type")),
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

    # Expected expiry — simulate the bot's real renewal logic:
    # каждый оплаченный платёж либо стартует новое окно (если была
    # дырка), либо продлевает текущее (если ещё не истекло на момент
    # оплаты). admin_grant_days ложится поверх. Ровно так, как это
    # делает production grant_access при обычной активации.
    #
    # Пример: платёж 01.07.2026 basic_30 → ожидание 31.07.2026,
    # НЕ activated_at + 30 (это давало странные даты в прошлом для
    # старых юзеров, у которых activated_at был много лет назад).
    counted_for_sim = []
    for p in proof_payments:
        if not p.get("counted"):
            continue
        eff_iso = p.get("paid_at") or p.get("created_at")
        eff = _from_db_utc_str(eff_iso)
        if eff:
            counted_for_sim.append({
                "effective_at": eff,
                "period_days": p["period_days"],
            })
    counted_for_sim.sort(key=lambda x: x["effective_at"])
    expected_expires_at = _simulate_expiry_from_payments(
        counted_for_sim, int(admin_grant_days or 0),
    )
    if expected_expires_at is None:
        # Нет платежей И нет admin_grant — считаем что подписки быть
        # не должно вообще; для UI ставим NOW, чтобы delta показал
        # ровно текущий разрыв.
        expected_expires_at = now

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
            "subscription_type": normalize_tier(sub_row["subscription_type"]),
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


def _simulate_expiry_from_payments(
    counted_payments: List[Dict[str, Any]],
    admin_grant_days: int,
) -> Optional[datetime]:
    """Compute the "correct" expires_at by simulating the standard bot renewal
    logic over the user's payment history.

    Each counted payment either:
      • starts a fresh subscription window (if there is no prior window OR
        the previous window has already ended by the time this payment was
        made — gap in subscription);
      • extends the current window (if paid while still-active — like the
        standard "renewal" branch in grant_access).

    Admin grant days (subscriptions.admin_grant_days) are added on TOP of
    the resulting end. Mirrors how admins actually use the grant flow:
    they hand out extra days after the standard payment history.

    A payment period is extended by tariffs.extend_expiry — CALENDAR months
    for the catalog periods (owner decision 2026-09-14), exactly like
    grant_access; admin grant days stay days.

    Example (matches product spec):
      payments=[(01.07.2026, 30)], admin=0 → 01.08.2026
      payments=[(01.06, 30), (25.06, 30)], admin=0 → 01.08 (extend)
      payments=[(01.01.2020, 30), (01.07.2026, 30)], admin=0 → 01.08.2026
        — 6-year gap → last payment starts fresh.

    Args:
        counted_payments: sorted ascending by effective_at, each with keys
            `effective_at: datetime` and `period_days: int`.
        admin_grant_days: total admin_grant_days from subscriptions row.

    Returns None if there are neither payments nor admin grants — caller
    should treat as "no legit subscription time exists → clamp to NOW+1d".
    """
    current_end: Optional[datetime] = None
    for p in counted_payments:
        paid_at = p["effective_at"]
        period = int(p["period_days"] or 0)
        if paid_at is None or period <= 0:
            continue
        if current_end is None or paid_at > current_end:
            # Gap or first payment: start fresh from this payment.
            current_end = extend_expiry(paid_at, period)
        else:
            # Renewal — extend current window.
            current_end = extend_expiry(current_end, period)

    if admin_grant_days and admin_grant_days > 0:
        base = current_end or datetime.now(timezone.utc)
        current_end = base + timedelta(days=admin_grant_days)

    return current_end


# ──────────────────────────────────────────────────────────────────────
#  3. Premium repair — ONE rule for the dashboard fix and the bulk script
# ──────────────────────────────────────────────────────────────────────
#
# Owner spec 2026-09-14: the premium entity's expireAt is set from the
# user's real purchases ("bought a year → a year"); a date in the past
# becomes NOW + 1 day (the panel rejects a past expireAt, 3.4.3 F7).
#
#   by_purchases = _simulate_expiry_from_payments(approved subscription
#                  payments, admin_grant_days)
#   by_db        = subscriptions.expires_at, ONLY for a sane bot-accounted
#                  date: status='active', not bypass-only, < NOW + 5y
#                  (covers balance payments, gifts, game/promo days that
#                  are not in `payments`)
#   target       = max(by_purchases, by_db); None or <= NOW → NOW + 1 day
#   never extend : target >= the panel's current expireAt → no change.
#
# The bypass entity (username = str(telegram_id), +10y by design) and the
# +10y placeholder of a bypass-only DB row are never touched.

_FIVE_YEARS = timedelta(days=365 * 5)
_ONE_DAY = timedelta(days=1)


def _is_bypass_only_row(db: Dict[str, Any]) -> bool:
    return bool(db.get("is_bypass_only")) or (db.get("source") or "") == "bypass_only"


def _count_payments(rows) -> Dict[str, Any]:
    """Approved payment rows (id, tariff, effective_at) → the counted
    subscription payments the simulation runs over, plus proof/summary."""
    counted: List[Dict[str, Any]] = []
    proof_ids: List[int] = []
    total_paid_days = 0
    for p in rows:
        period_days = _extract_period_days_from_tariff((p["tariff"] or "").strip())
        if not period_days:
            continue
        proof_ids.append(p["id"])
        total_paid_days += period_days
        eff = _from_db_utc(p["effective_at"]) if p["effective_at"] else None
        if eff:
            counted.append({"effective_at": eff, "period_days": period_days})
    counted.sort(key=lambda x: x["effective_at"])
    return {
        "counted": counted,
        "proof_payment_ids": proof_ids,
        "total_paid_days": total_paid_days,
        "approved_payments": len(rows),
        "counted_payments": len(proof_ids),
        "last_paid_at": counted[-1]["effective_at"] if counted else None,
    }


async def load_repair_inputs(telegram_ids: List[int]) -> Dict[int, Dict[str, Any]]:
    """Bot-DB state the repair rule needs, for many users in one short
    connection (no HTTP inside). Users without a subscriptions row still get
    an entry (their payments count; the DB date does not)."""
    ids = sorted({int(t) for t in telegram_ids})
    out: Dict[int, Dict[str, Any]] = {}
    if not ids:
        return out
    pool = await get_pool()
    if pool is None:
        raise RuntimeError("db_unavailable")
    async with pool.acquire() as conn:
        sub_rows = await conn.fetch(
            """SELECT telegram_id, expires_at, status, source, admin_grant_days,
                      COALESCE(is_bypass_only, FALSE) AS is_bypass_only
               FROM subscriptions
               WHERE telegram_id = ANY($1::bigint[])""",
            ids,
        )
        pay_rows = await conn.fetch(
            """SELECT telegram_id, id, tariff, COALESCE(paid_at, created_at) AS effective_at
               FROM payments
               WHERE telegram_id = ANY($1::bigint[])
                 AND status = 'approved'
               ORDER BY telegram_id, COALESCE(paid_at, created_at) ASC, id ASC""",
            ids,
        )
    subs = {r["telegram_id"]: r for r in sub_rows}
    pays: Dict[int, list] = {}
    for p in pay_rows:
        pays.setdefault(p["telegram_id"], []).append(p)
    for tg in ids:
        s = subs.get(tg)
        entry = _count_payments(pays.get(tg, []))
        entry.update({
            "db_row": s is not None,
            "db_expires_at": _from_db_utc(s["expires_at"]) if s and s["expires_at"] else None,
            "db_status": s["status"] if s else None,
            "db_source": s["source"] if s else None,
            "db_is_bypass_only": bool(s["is_bypass_only"]) if s else False,
            "admin_grant_days": int((s["admin_grant_days"] if s else 0) or 0),
        })
        out[tg] = entry
    return out


def compute_repair_target(
    inputs: Dict[str, Any],
    *,
    now: datetime,
    panel_expires_at: Optional[datetime],
) -> Dict[str, Any]:
    """The target rule (see the block comment above). Pure."""
    by_purchases = _simulate_expiry_from_payments(
        inputs.get("counted") or [], int(inputs.get("admin_grant_days") or 0),
    )
    db_exp = inputs.get("db_expires_at")
    bypass_only = _is_bypass_only_row({
        "is_bypass_only": inputs.get("db_is_bypass_only"), "source": inputs.get("db_source"),
    })
    by_db = None
    if (db_exp is not None and inputs.get("db_status") == "active"
            and not bypass_only and db_exp < now + _FIVE_YEARS):
        by_db = db_exp

    sources = [d for d in (by_purchases, by_db) if d is not None]
    target = max(sources) if sources else None
    target_source = None
    fallback: Optional[str] = None
    if target is None:
        fallback = "no_payments"
    elif target <= now:
        fallback = "past_date"
    else:
        target_source = "purchases" if by_purchases is not None and target == by_purchases else "db"
    if fallback:
        target = now + _ONE_DAY
        target_source = "fallback"
    return {
        "by_purchases": by_purchases,
        "by_db": by_db,
        "target": target,
        "target_source": target_source,
        "fallback": fallback,
        # A leaked (non bypass-only) DB date is shortened to the same target.
        "db_leaked": db_exp is not None and not bypass_only and db_exp > now + _FIVE_YEARS,
        "would_extend": panel_expires_at is not None and target >= panel_expires_at,
    }


def _iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def record_premium_repair(
    telegram_id: int,
    *,
    old_expires_at: datetime,
    new_expires_at: datetime,
    shorten_db: bool,
    reason: str,
    proof_payment_ids: List[int],
    total_paid_days: int,
    admin_grant_days: int,
    admin_telegram_id: Optional[int],
    now: datetime,
) -> Dict[str, Any]:
    """After a successful panel PATCH: one short transaction that logs the
    change and (only when asked) shortens a leaked DB date to the same value.
    The UPDATE re-checks every guard, so it can only shorten, never touches a
    bypass-only row, and never a row that is already sane."""
    pool = await get_pool()
    if pool is None:
        raise RuntimeError("db_unavailable")
    db_shortened = False
    async with pool.acquire() as conn:
        async with conn.transaction():
            if shorten_db:
                status = await conn.execute(
                    """UPDATE subscriptions SET expires_at = $2
                       WHERE telegram_id = $1
                         AND NOT COALESCE(is_bypass_only, FALSE)
                         AND COALESCE(source, '') <> 'bypass_only'
                         AND expires_at > $3
                         AND expires_at > $2""",
                    telegram_id, _to_db_utc(new_expires_at), _to_db_utc(now + _FIVE_YEARS),
                )
                db_shortened = status.endswith(" 1")
            if db_shortened:
                reason += " [bot-DB leaked date shortened to the same value]"
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
                _to_db_utc(old_expires_at),
                _to_db_utc(new_expires_at),
                (old_expires_at - now).days,
                (new_expires_at - now).days,
                (old_expires_at - new_expires_at).days,
                reason[:2000],
                proof_payment_ids,
                total_paid_days,
                admin_grant_days,
                admin_telegram_id,
            )
    return {"log_id": log_id, "db_shortened": db_shortened}


def _expected_premium_usernames(telegram_id: int) -> set:
    names = {f"tg_{telegram_id}_premium"}
    try:
        from app.services.remnawave_premium import build_premium_username
        names.add(build_premium_username(telegram_id))
    except Exception:
        pass
    return names


async def repair_premium_entity(
    telegram_id: int,
    *,
    panel_id: int,
    panel_username: str,
    panel_expires_at: datetime,
    reason: str,
    admin_telegram_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Repair ONE premium entity: fresh DB read → rule → PATCH {id, expireAt}
    by the entity's numeric panel id → log + DB shortening. Never raises.

    Returns the rule fields plus `action` (fixed / skip / error) and `reason`.
    A failed PATCH leaves panel and DB as they were; a DB write that fails
    after a good PATCH is an error (the panel is already fixed)."""
    out: Dict[str, Any] = {"action": "error", "reason": None, "log_id": None, "db_shortened": False}
    if panel_username not in _expected_premium_usernames(telegram_id):
        out.update(action="skip", reason="not_a_premium_entity")
        return out
    now = datetime.now(timezone.utc)
    try:
        inputs = (await load_repair_inputs([telegram_id]))[telegram_id]
    except Exception as e:
        out["reason"] = f"db_read_failed: {type(e).__name__}"
        return out
    decision = compute_repair_target(inputs, now=now, panel_expires_at=panel_expires_at)
    out.update(inputs=inputs, **decision)
    if decision["would_extend"]:
        out.update(action="skip", reason="would_extend")
        return out
    target = decision["target"]

    try:
        from app.services import remnawave_api
        result = await remnawave_api.update_user(int(panel_id), expireAt=_iso_z(target))
    except Exception as e:
        out["reason"] = f"panel_patch_failed: {type(e).__name__}"
        return out
    if result is None:
        out["reason"] = "panel_patch_rejected"
        return out

    log_reason = reason
    if decision["fallback"]:
        log_reason += f" [fallback: {decision['fallback']}, set to NOW+1d]"
    log_reason += f" [target from {decision['target_source']}; premium entity only]"
    try:
        rec = await record_premium_repair(
            telegram_id,
            old_expires_at=panel_expires_at,
            new_expires_at=target,
            shorten_db=decision["db_leaked"],
            reason=log_reason,
            proof_payment_ids=inputs["proof_payment_ids"],
            total_paid_days=inputs["total_paid_days"],
            admin_grant_days=inputs["admin_grant_days"],
            admin_telegram_id=admin_telegram_id,
            now=now,
        )
    except Exception as e:
        logger.error("PREMIUM_REPAIR_DB_WRITE_FAILED user=%s after panel PATCH: %s",
                     telegram_id, type(e).__name__)
        out["reason"] = f"patched_but_db_write_failed: {type(e).__name__}"
        return out
    out.update(action="fixed", reason=None, **rec)
    return out


async def apply_reconciliation_fix(
    telegram_id: int,
    admin_telegram_id: int,
    *,
    reason: str = "manual reconciliation via dashboard",
) -> Dict[str, Any]:
    """Dashboard «Сверка» → «Исправить» for one user: the same rule as the
    bulk script (repair_premium_entity). The premium entity is found in the
    panel by username (a cached DB uuid can be stale / contaminated).

    Never extends: when the rule's date is not earlier than the panel's
    current expireAt nothing is written (error="would_extend")."""
    pool = await get_pool()
    if pool is None:
        return {"success": False, "error": "db_unavailable"}

    base: Dict[str, Any] = {
        "success": False, "log_id": None, "old_expires_at": None, "new_expires_at": None,
        "days_removed": 0, "total_paid_days": 0, "admin_grant_days_kept": 0,
        "proof_payment_ids": [], "fallback_applied": None, "panel_updated": False,
        "panel_error": None, "is_bypass_only": False,
    }
    entity = None
    try:
        from app.services import remnawave_api
        from app.services.remnawave_premium import build_premium_username
        entity = await remnawave_api.find_user_by_username(build_premium_username(telegram_id))
    except Exception as e:
        logger.warning("RECONCILIATION_PANEL_LOOKUP_FAIL user=%s: %s", telegram_id, type(e).__name__)
    panel_expires_at = _parse_remnawave_dt((entity or {}).get("expireAt"))
    if not entity or entity.get("id") is None or panel_expires_at is None:
        base.update(error="panel_entity_unavailable",
                    panel_error="premium entity not found in the panel (or panel unavailable)")
        return base

    res = await repair_premium_entity(
        telegram_id,
        panel_id=int(entity["id"]),
        panel_username=(entity.get("username") or "").strip(),
        panel_expires_at=panel_expires_at,
        reason=reason,
        admin_telegram_id=admin_telegram_id,
    )
    inputs = res.get("inputs") or {}
    target = res.get("target")
    fixed = res["action"] == "fixed"
    base.update({
        "success": fixed,
        "log_id": res.get("log_id"),
        "old_expires_at": panel_expires_at.isoformat(),
        "new_expires_at": target.isoformat() if target else None,
        "days_removed": (panel_expires_at - target).days if target and fixed else 0,
        "total_paid_days": inputs.get("total_paid_days", 0),
        "admin_grant_days_kept": inputs.get("admin_grant_days", 0),
        "proof_payment_ids": inputs.get("proof_payment_ids", []),
        "fallback_applied": "would_extend" if res.get("reason") == "would_extend" else res.get("fallback"),
        "panel_updated": fixed or (res.get("reason") or "").startswith("patched_but"),
        "panel_error": None if fixed else res.get("reason"),
        "is_bypass_only": bool(inputs.get("db_is_bypass_only")),
    })
    if res.get("reason") == "would_extend":
        base["error"] = "would_extend"
    logger.info(
        "RECONCILIATION_FIX user=%s action=%s reason=%s fallback=%s target_source=%s log_id=%s",
        telegram_id, res["action"], res.get("reason"), res.get("fallback"),
        res.get("target_source"), res.get("log_id"),
    )
    return base


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
