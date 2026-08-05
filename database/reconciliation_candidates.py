"""Сверка: список кандидатов на превышение срока.

ЧТО ЗДЕСЬ
    Одна функция — `find_over_issuance_candidates`. Она сканирует панель
    (через кэш из reconciliation_panel) и обогащает найденное данными из
    базы бота.

ПОЧЕМУ ВЫДЕЛЕНО
    Это верхний экран «Сверки», и правят его по своему поводу: порог,
    лимит, набор колонок. Панельный кэш и расчёты сроков к этому
    отношения не имеют.

ЧТО ЛЕГКО СЛОМАТЬ
    Ветка `over_from_panel is None` возвращает строку-маркер
    `panel_unreachable`, а не пустой список. Если её упростить до
    `return []`, недоступная панель нарисуется как «всё чисто» — самая
    дорогая форма вранья в этом экране.

    `limit` режет ЛОКАЛЬНУЮ копию: подрезать список внутри кэша нельзя,
    иначе следующий запрос получит обрезок.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import asyncpg

from database.core import get_pool, _from_db_utc
from database.reconciliation_panel import _scan_panel_for_over_issuance

logger = logging.getLogger(__name__)


# Threshold — anything above this from NOW is considered suspicious.
_EIGHT_YEARS = timedelta(days=365 * 8)


async def find_over_issuance_candidates(
    limit: int = 200,
    force_refresh: bool = False,
) -> List[Dict[str, Any]]:
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

    # ── Step 1: scan the Remnawave panel (кэш на _PANEL_SCAN_TTL_SECONDS) ──
    try:
        over_from_panel = await _scan_panel_for_over_issuance(cutoff, force_refresh)
    except Exception as e:
        logger.error("find_over_issuance_candidates: panel scan failed: %s", e)
        over_from_panel = None

    if over_from_panel is None:
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

    if not over_from_panel:
        return []

    # Скан отдаёт уже отсортированный список (самые подозрительные сверху);
    # режем локальной копией, чтобы не подрезать список в кэше.
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

