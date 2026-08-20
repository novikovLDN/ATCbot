"""DB ↔ Remnawave panel traffic-limit reconciliation.

Общий движок для CLI-скрипта (`scripts/audit_bypass_traffic_mismatch.py`)
и dashboard-endpoint (`app/api/dashboard/routes/traffic_audit.py`):

  1. Собирает всех юзеров с bypass entity (subscriptions.remnawave_uuid
     или remnawave_id NOT NULL).
  2. Для каждого:
     expected = subscription_base_bytes + Σ traffic_purchases.gb_amount * 1024**3
     actual   = panel.trafficLimitBytes
     used     = panel.usedTrafficBytes
     shortfall = max(0, expected - actual). Report если > 100 MB.
  3. apply_fix(result) → PATCH trafficLimitBytes = expected + used
     (used история сохраняется, remaining = ровно expected).

Rate-limit защита: max_concurrent + batch_sleep — не убивает панель.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, asdict
from typing import Any, Optional

import config
import database
from app.services import remnawave_api

logger = logging.getLogger(__name__)

# Порог shortfall для отчёта — округления при GiB↔bytes конверсии
# могут дать несколько MB, игнорируем.
SHORTFALL_TOLERANCE_BYTES = 100 * 1024 * 1024   # 100 MB


@dataclass
class UserRow:
    telegram_id: int
    subscription_type: str
    period_days: Optional[int]
    is_bypass_only: bool
    remnawave_uuid: Optional[str]
    remnawave_id: Optional[int]
    traffic_purchases_gb: int


@dataclass
class TrafficPurchaseDetail:
    id: int
    gb_amount: int
    price_rub: int
    payment_method: Optional[str]
    created_at: Optional[str]


@dataclass
class AuditResult:
    tg: int
    subscription_type: str
    period_days: Optional[int]
    is_bypass_only: bool
    traffic_purchases_gb: int
    traffic_purchases: list[TrafficPurchaseDetail]   # empty в bulk-mode
    expected_bytes: int
    actual_bytes: int
    used_bytes: int
    shortfall_bytes: int
    panel_status: str
    kind: str                   # "match" | "mismatch" | "no_entity" | "panel_error"
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _subscription_base_bytes(sub_type: str, period_days: Optional[int]) -> int:
    """GB-лимит от тарифа подписки (не считая купленные traffic packs).

    Правила:
      trial               → TRIAL_BYPASS_MB MB
      combo_basic/plus    → COMBO_TARIFFS[t][p]["gb"] GB
      basic / plus        → TRAFFIC_LIMITS[t][p] (уже bytes)
      всё остальное       → 0 (bypass-only, apple_id, spotify, gift, farm...)
    """
    sub_type = (sub_type or "").strip().lower()
    p = int(period_days or 30)
    if sub_type == "trial":
        return int(getattr(config, "TRIAL_BYPASS_MB", 500)) * (1024 ** 2)
    combo = getattr(config, "COMBO_TARIFFS", {}) or {}
    if sub_type in combo:
        per_period = combo[sub_type].get(p) or {}
        gb = int(per_period.get("gb") or 0)
        return gb * (1024 ** 3)
    limits = getattr(config, "TRAFFIC_LIMITS", {}) or {}
    if sub_type in limits:
        table = limits[sub_type]
        if isinstance(table, dict):
            if p in table:
                return int(table[p])
            available = sorted(table.keys())
            for cand in available:
                if cand >= p:
                    return int(table[cand])
            if available:
                return int(table[available[-1]])
        if isinstance(table, int):
            return int(table)
    return 0


async def fetch_candidates(
    *,
    limit: Optional[int] = None,
    only_tg: Optional[int] = None,
) -> list[UserRow]:
    """Собрать список юзеров для аудита из subscriptions + traffic_purchases."""
    pool = await database.get_pool()
    if pool is None:
        raise RuntimeError("database pool not ready")

    # `subscriptions` не хранит period_days напрямую — берём из последнего
    # paid-события в subscription_history (action_type ∈ {purchase, renewal,
    # auto_renew, trial}). Fallback: pending_purchases последняя paid-строка.
    # Если ничего нет — оставляем NULL, base_bytes упадёт в дефолт 30 дней.
    sql = """
        SELECT s.telegram_id,
               COALESCE(s.subscription_type, 'basic') AS subscription_type,
               COALESCE(
                 (SELECT GREATEST(
                    1,
                    CAST(
                      EXTRACT(EPOCH FROM (h.end_date - h.start_date)) / 86400
                      AS INTEGER
                    )
                  )
                  FROM subscription_history h
                  WHERE h.telegram_id = s.telegram_id
                    AND h.action_type IN ('purchase','renewal','auto_renew','trial','initial')
                  ORDER BY h.created_at DESC NULLS LAST, h.id DESC
                  LIMIT 1),
                 (SELECT p.period_days
                  FROM pending_purchases p
                  WHERE p.telegram_id = s.telegram_id
                    AND p.status = 'paid'
                    AND p.period_days IS NOT NULL
                  ORDER BY p.created_at DESC
                  LIMIT 1),
                 30
               ) AS period_days,
               COALESCE(s.is_bypass_only, FALSE)     AS is_bypass_only,
               s.remnawave_uuid,
               s.remnawave_id,
               COALESCE((SELECT SUM(gb_amount) FROM traffic_purchases WHERE telegram_id = s.telegram_id), 0) AS gp
        FROM subscriptions s
        WHERE (s.remnawave_uuid IS NOT NULL AND s.remnawave_uuid <> '')
           OR s.remnawave_id IS NOT NULL
    """
    params: list = []
    if only_tg is not None:
        sql += " AND s.telegram_id = $1"
        params.append(int(only_tg))
    sql += " ORDER BY s.telegram_id"
    if limit is not None:
        sql += f" LIMIT {int(limit)}"

    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)
    return [
        UserRow(
            telegram_id=int(r["telegram_id"]),
            subscription_type=str(r["subscription_type"] or "basic"),
            period_days=int(r["period_days"]) if r["period_days"] is not None else None,
            is_bypass_only=bool(r["is_bypass_only"]),
            remnawave_uuid=(r["remnawave_uuid"] or None),
            remnawave_id=int(r["remnawave_id"]) if r["remnawave_id"] is not None else None,
            traffic_purchases_gb=int(r["gp"] or 0),
        )
        for r in rows
    ]


def compute_expected_bytes(row: UserRow) -> int:
    """Ожидаемый trafficLimitBytes: subscription base + пакеты GB."""
    if row.is_bypass_only:
        base = 0
    else:
        base = _subscription_base_bytes(row.subscription_type, row.period_days)
    return base + int(row.traffic_purchases_gb) * (1024 ** 3)


async def _fetch_traffic_purchase_details(telegram_id: int) -> list[TrafficPurchaseDetail]:
    """Отдельные строки traffic_purchases для одного юзера — для detail-view."""
    pool = await database.get_pool()
    if pool is None:
        return []
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT id, gb_amount, price_rub, payment_method, created_at
                     FROM traffic_purchases
                    WHERE telegram_id = $1
                    ORDER BY created_at DESC NULLS LAST, id DESC""",
                int(telegram_id),
            )
        return [
            TrafficPurchaseDetail(
                id=int(r["id"]),
                gb_amount=int(r["gb_amount"] or 0),
                price_rub=int(r["price_rub"] or 0),
                payment_method=(r["payment_method"] or None),
                created_at=r["created_at"].isoformat() if r["created_at"] else None,
            )
            for r in rows
        ]
    except Exception:
        return []


async def audit_one(row: UserRow, *, include_details: bool = False) -> AuditResult:
    """Один юзер — сравнить expected vs panel.

    include_details=True → подтянуть отдельные строки traffic_purchases
    (тяжело — в bulk-mode всегда False).
    """
    expected = compute_expected_bytes(row)
    probe = row.remnawave_id if row.remnawave_id is not None else row.remnawave_uuid
    details = await _fetch_traffic_purchase_details(row.telegram_id) if include_details else []

    def _make(kind: str, actual: int, used: int, status: str, note: str = "") -> AuditResult:
        return AuditResult(
            tg=row.telegram_id,
            subscription_type=row.subscription_type,
            period_days=row.period_days,
            is_bypass_only=row.is_bypass_only,
            traffic_purchases_gb=row.traffic_purchases_gb,
            traffic_purchases=details,
            expected_bytes=expected,
            actual_bytes=actual,
            used_bytes=used,
            shortfall_bytes=max(0, expected - actual),
            panel_status=status,
            kind=kind,
            note=note,
        )

    if probe is None:
        return _make("no_entity", 0, 0, "—", "no remnawave_uuid AND no remnawave_id")

    try:
        entity = await remnawave_api.get_user(probe)
    except Exception as e:
        return _make("panel_error", 0, 0, "—", f"{type(e).__name__}: {str(e)[:120]}")
    if not entity:
        return _make("no_entity", 0, 0, "—", "get_user returned None")

    actual = int(entity.get("trafficLimitBytes") or 0)
    used = int(entity.get("usedTrafficBytes") or 0)
    status = str(entity.get("status") or "?")
    shortfall = max(0, expected - actual)
    is_mismatch = shortfall > SHORTFALL_TOLERANCE_BYTES and expected > 0
    return _make("mismatch" if is_mismatch else "match", actual, used, status)


async def _bounded_gather(coros, concurrency: int):
    sem = asyncio.Semaphore(concurrency)

    async def _guarded(c):
        async with sem:
            return await c

    return await asyncio.gather(*(_guarded(c) for c in coros))


async def run_audit(
    *,
    limit: Optional[int] = None,
    only_tg: Optional[int] = None,
    concurrent: int = 5,
    batch_sleep: float = 0.2,
    progress_cb: Optional[callable] = None,
) -> list[AuditResult]:
    """Собрать candidates + прогнать audit_one с rate-limit.

    Если only_tg задан → include_details=True (single-user detail-view).
    Bulk-mode → без детализации (performance).
    """
    concurrent = max(1, min(20, int(concurrent)))
    batch_sleep = max(0.0, float(batch_sleep))

    candidates = await fetch_candidates(limit=limit, only_tg=only_tg)
    if not candidates:
        return []

    include_details = only_tg is not None
    results: list[AuditResult] = []
    batch_size = concurrent * 4
    for i in range(0, len(candidates), batch_size):
        batch = candidates[i:i + batch_size]
        batch_results = await _bounded_gather(
            [audit_one(row, include_details=include_details) for row in batch],
            concurrency=concurrent,
        )
        results.extend(batch_results)
        if progress_cb:
            try:
                progress_cb(len(results), len(candidates))
            except Exception:
                pass
        if i + batch_size < len(candidates) and batch_sleep > 0:
            await asyncio.sleep(batch_sleep)
    return results


async def apply_fix(result: AuditResult) -> dict[str, Any]:
    """Поднять trafficLimitBytes до expected+used для одного юзера.

    Возвращает {"ok": bool, "reason"|"before"|"after"|"new_limit": ...}.
    """
    if result.kind != "mismatch" or result.shortfall_bytes <= SHORTFALL_TOLERANCE_BYTES:
        return {"ok": False, "reason": "not_a_mismatch"}

    rows = await fetch_candidates(only_tg=result.tg, limit=1)
    if not rows:
        return {"ok": False, "reason": "user_vanished_from_db"}
    row = rows[0]
    probe = row.remnawave_id if row.remnawave_id is not None else row.remnawave_uuid
    if probe is None:
        return {"ok": False, "reason": "no_probe_key"}

    new_limit = result.expected_bytes + result.used_bytes
    try:
        resp = await remnawave_api.update_user(
            probe, trafficLimitBytes=new_limit, status="ACTIVE",
        )
    except Exception as e:
        return {"ok": False, "reason": f"exception:{type(e).__name__}:{str(e)[:120]}"}
    if resp is None:
        return {"ok": False, "reason": "patch_returned_none"}

    logger.info(
        "PANEL_TRAFFIC_AUDIT_FIXED tg=%s: %d → %d (used=%d, expected=%d)",
        result.tg, result.actual_bytes, new_limit, result.used_bytes, result.expected_bytes,
    )
    return {
        "ok": True,
        "before_bytes": result.actual_bytes,
        "after_bytes": new_limit,
        "used_bytes": result.used_bytes,
        "expected_bytes": result.expected_bytes,
    }


__all__ = [
    "SHORTFALL_TOLERANCE_BYTES",
    "UserRow",
    "AuditResult",
    "fetch_candidates",
    "compute_expected_bytes",
    "audit_one",
    "run_audit",
    "apply_fix",
]
