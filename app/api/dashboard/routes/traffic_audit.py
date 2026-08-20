"""DB ↔ Remnawave panel traffic-limit reconciliation (dashboard endpoints).

  GET  /traffic-audit?limit=200&user=<tg>
       → { total, match, mismatch, no_entity, panel_error,
           shortfall_total_bytes, results: [...] }
  POST /traffic-audit/fix/{tg}   → PATCH одного юзера
  POST /traffic-audit/fix-all    → PATCH всех mismatches

Логика — общая с CLI-скриптом (см. app.services.panel_traffic_audit).
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query

from app.api.dashboard.deps import require_admin
from app.events import bus
from app.services import panel_traffic_audit as pta

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_admin)])


def _serialize_result(r: pta.AuditResult) -> dict[str, Any]:
    d = r.to_dict()
    # Дублируем в GB для удобства фронта (без floating math на клиенте).
    d["expected_gb"] = round(r.expected_bytes / (1024 ** 3), 2)
    d["actual_gb"] = round(r.actual_bytes / (1024 ** 3), 2)
    d["used_gb"] = round(r.used_bytes / (1024 ** 3), 2)
    d["shortfall_gb"] = round(r.shortfall_bytes / (1024 ** 3), 2)
    return d


def _summarize(results: list[pta.AuditResult]) -> dict[str, Any]:
    matches = [r for r in results if r.kind == "match"]
    mismatches = [r for r in results if r.kind == "mismatch"]
    no_entity = [r for r in results if r.kind == "no_entity"]
    errors = [r for r in results if r.kind == "panel_error"]
    total_short = sum(r.shortfall_bytes for r in mismatches)
    return {
        "total": len(results),
        "match": len(matches),
        "mismatch": len(mismatches),
        "no_entity": len(no_entity),
        "panel_error": len(errors),
        "shortfall_total_bytes": total_short,
        "shortfall_total_gb": round(total_short / (1024 ** 3), 2),
    }


@router.get("")
async def list_audit(
    limit: Optional[int] = Query(200, ge=1, le=5000,
                                 description="Сколько юзеров сканировать (default 200, cap 5000)"),
    user: Optional[int] = Query(None, description="Ограничить одним telegram_id"),
    concurrent: int = Query(5, ge=1, le=20,
                            description="Параллельность запросов к панели"),
) -> dict[str, Any]:
    """Прогнать audit и вернуть результаты + summary."""
    try:
        results = await pta.run_audit(
            limit=limit,
            only_tg=user,
            concurrent=concurrent,
            batch_sleep=0.15,
        )
    except Exception as e:
        # Логируем полный traceback чтобы диагностировать (в бразуере видна
        # только detail). Message в detail — уже сжатая инфо для UI.
        logger.exception("traffic_audit list failed: limit=%s user=%s", limit, user)
        raise HTTPException(500, f"audit_failed: {type(e).__name__}: {e}")

    summary = _summarize(results)
    return {
        "summary": summary,
        # Сортируем: mismatches сверху (по shortfall desc), потом всё остальное
        "results": [
            _serialize_result(r)
            for r in sorted(
                results,
                key=lambda x: (
                    0 if x.kind == "mismatch" else
                    (1 if x.kind == "panel_error" else
                     (2 if x.kind == "no_entity" else 3)),
                    -x.shortfall_bytes,
                    x.tg,
                ),
            )
        ],
    }


@router.post("/fix/{telegram_id}")
async def fix_one(
    telegram_id: int = Path(..., gt=0),
    admin: dict = Depends(require_admin),
) -> dict[str, Any]:
    """PATCH одного юзера: пересчитать expected + PATCH panel."""
    try:
        rows = await pta.fetch_candidates(only_tg=telegram_id, limit=1)
    except Exception as e:
        raise HTTPException(500, f"fetch_failed: {e}")
    if not rows:
        raise HTTPException(404, "user_not_found_in_db")

    try:
        result = await pta.audit_one(rows[0])
    except Exception as e:
        raise HTTPException(500, f"audit_failed: {e}")
    if result.kind != "mismatch":
        return {
            "ok": False,
            "reason": f"not_a_mismatch:{result.kind}",
            "audit": _serialize_result(result),
        }

    try:
        outcome = await pta.apply_fix(result)
    except Exception as e:
        raise HTTPException(500, f"fix_failed: {e}")

    if not outcome.get("ok"):
        raise HTTPException(400, f"fix_declined: {outcome.get('reason')}")

    logger.info(
        "TRAFFIC_AUDIT_FIX_ONE tg=%s admin=%s before=%s after=%s",
        telegram_id, admin.get("sub"),
        outcome.get("before_bytes"), outcome.get("after_bytes"),
    )
    bus.publish({
        "type": "traffic_audit:fixed",
        "telegram_id": telegram_id,
        "by": admin.get("sub"),
        **outcome,
    })
    return {"ok": True, **outcome, "audit": _serialize_result(result)}


@router.post("/fix-all")
async def fix_all(
    limit: Optional[int] = Query(None, ge=1, le=5000,
                                 description="Сколько юзеров максимум починить за раз"),
    concurrent: int = Query(3, ge=1, le=10,
                            description="Параллельность PATCH'ей (осторожно)"),
    admin: dict = Depends(require_admin),
) -> dict[str, Any]:
    """Прогнать audit + починить все mismatches. Не транзакционно — каждый юзер отдельно."""
    try:
        results = await pta.run_audit(
            limit=None,
            concurrent=5,
            batch_sleep=0.15,
        )
    except Exception as e:
        raise HTTPException(500, f"audit_failed: {e}")

    mismatches = [r for r in results if r.kind == "mismatch"]
    if limit is not None:
        mismatches = mismatches[:limit]

    if not mismatches:
        return {
            "audit_summary": _summarize(results),
            "fixed": 0,
            "failed": 0,
            "results": [],
        }

    import asyncio as _aio
    sem = _aio.Semaphore(max(1, min(10, concurrent)))

    async def _fix(r: pta.AuditResult) -> dict[str, Any]:
        async with sem:
            out = await pta.apply_fix(r)
            return {
                "telegram_id": r.tg,
                "ok": bool(out.get("ok")),
                "reason": out.get("reason"),
                "before_bytes": out.get("before_bytes", r.actual_bytes),
                "after_bytes": out.get("after_bytes"),
                "used_bytes": out.get("used_bytes", r.used_bytes),
                "expected_bytes": out.get("expected_bytes", r.expected_bytes),
            }

    outcomes = await _aio.gather(*(_fix(r) for r in mismatches))
    fixed = sum(1 for o in outcomes if o["ok"])
    failed = len(outcomes) - fixed

    logger.info(
        "TRAFFIC_AUDIT_FIX_ALL admin=%s fixed=%s failed=%s",
        admin.get("sub"), fixed, failed,
    )
    bus.publish({
        "type": "traffic_audit:fix_all_done",
        "fixed": fixed,
        "failed": failed,
        "by": admin.get("sub"),
    })
    return {
        "audit_summary": _summarize(results),
        "fixed": fixed,
        "failed": failed,
        "results": outcomes,
    }
