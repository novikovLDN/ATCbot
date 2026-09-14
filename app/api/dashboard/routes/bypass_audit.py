"""Bypass-overwrite audit & recovery.

Endpoints для админа:
  GET  /bypass-audit            — список пострадавших с детализацией;
  POST /bypass-audit/fix/{tg}   — восстановить одного юзера;
  POST /bypass-audit/fix-all    — восстановить всех (массовая).

Все три — read+write на subscriptions. Дёргать в проде с
осторожностью; всегда сначала GET для preview.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Path

import database
from app.api.dashboard.deps import require_admin
from app.events import bus

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_admin)])


def _serialize_dt(v: Any) -> Any:
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


def _serialize(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_serialize(x) for x in obj]
    return _serialize_dt(obj)


@router.get("")
async def list_victims() -> Dict[str, Any]:
    """Список пострадавших + summary."""
    try:
        victims = await database.get_bypass_overwrite_victims()
    except Exception as e:
        raise HTTPException(500, f"list_failed: {e}")

    can_fix_count = sum(1 for v in victims if v.get("can_fix"))
    total_traffic_gb = sum(int(v.get("traffic_total_gb", 0) or 0) for v in victims)
    return _serialize({
        "total": len(victims),
        "can_fix": can_fix_count,
        "total_traffic_gb_purchased": total_traffic_gb,
        "victims": victims,
    })


@router.post("/fix/{telegram_id}")
async def fix_one(
    telegram_id: int = Path(..., gt=0),
    admin: dict = Depends(require_admin),
) -> Dict[str, Any]:
    """Восстановить одного юзера."""
    try:
        result = await database.fix_bypass_overwrite_victim(telegram_id)
    except Exception as e:
        raise HTTPException(500, f"fix_failed: {e}")

    if not result.get("ok"):
        raise HTTPException(400, f"cannot_fix: {result.get('reason')}")

    logger.info(
        "BYPASS_AUDIT_FIX_ONE tg=%s admin=%s before=%s after=%s",
        telegram_id, admin.get("sub"), result.get("before"), result.get("after"),
    )
    bus.publish({
        "type": "bypass_audit:fixed",
        "telegram_id": telegram_id,
        "by": admin.get("sub"),
    })
    return _serialize(result)


@router.post("/fix-all")
async def fix_all(admin: dict = Depends(require_admin)) -> Dict[str, Any]:
    """Восстановить всех. Возвращает per-user отчёт.

    Не транзакционно по всей пачке — каждый юзер фиксится в своей
    транзакции, сбой одного не блокирует остальных.
    """
    try:
        victims = await database.get_bypass_overwrite_victims()
    except Exception as e:
        raise HTTPException(500, f"list_failed: {e}")

    results: List[Dict[str, Any]] = []
    fixed = 0
    failed = 0
    for v in victims:
        if not v.get("can_fix"):
            results.append({
                "telegram_id": v["telegram_id"],
                "ok": False,
                "reason": "no_paid_history_to_recover_from",
            })
            failed += 1
            continue
        try:
            r = await database.fix_bypass_overwrite_victim(int(v["telegram_id"]))
            if r.get("ok"):
                fixed += 1
            else:
                failed += 1
            results.append({
                "telegram_id": v["telegram_id"],
                "ok": bool(r.get("ok")),
                "reason": r.get("reason"),
                "before": _serialize(r.get("before")),
                "after": _serialize(r.get("after")),
            })
        except Exception as e:
            failed += 1
            results.append({
                "telegram_id": v["telegram_id"],
                "ok": False,
                "reason": f"exception: {type(e).__name__}: {e}",
            })

    logger.info(
        "BYPASS_AUDIT_FIX_ALL admin=%s fixed=%s failed=%s",
        admin.get("sub"), fixed, failed,
    )
    bus.publish({
        "type": "bypass_audit:fix_all_done",
        "fixed": fixed,
        "failed": failed,
        "by": admin.get("sub"),
    })
    return {
        "total": len(victims),
        "fixed": fixed,
        "failed": failed,
        "results": results,
    }
