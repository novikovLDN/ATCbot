"""Сверка: детальный снимок по одному пользователю.

ЧТО ЗДЕСЬ
    `get_reconciliation_detail` — то, что видит админ, открыв карточку:
    строка подписки, все одобренные платежи, журнал превышений и разрыв
    между фактическим и ожидаемым сроком, плюс сверка с панелью.

ПОЧЕМУ ВЫДЕЛЕНО
    Функция только читает и ничего не меняет. Соседний
    reconciliation_fix.py, наоборот, пишет в панель — держать их в одном
    файле значит править «показать» и «применить» вперемешку.

ЧТО ЛЕГКО СЛОМАТЬ
    Ожидаемый срок считается тем же `_simulate_expiry_from_payments`, что
    и в «Исправить». Разъедутся вызовы — админ увидит одно число, а
    кнопка применит другое, и в логах об этом не будет ни строки.

    Ветка «строки в базе нет» существует не для красоты: юзер может
    остаться только в панели — это ровно тот случай, который экран и
    должен показывать.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

from database.core import get_pool, _from_db_utc
from database.reconciliation_expiry import (
    _extract_period_days_from_tariff,
    _from_db_utc_str,
    _simulate_expiry_from_payments,
)
from database.reconciliation_panel import _fetch_panel_expires_at

logger = logging.getLogger(__name__)


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

