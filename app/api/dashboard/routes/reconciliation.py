"""Subscription reconciliation endpoints — «Сверка» screen backend.

Reads:
  GET  /candidates                    — list users with expires_at > NOW + 8y (premium only)
  GET  /candidates/{telegram_id}      — detailed reconciliation snapshot for one user
  GET  /audit-log                     — recent /fix executions
  GET  /over-issuance-log             — recent auto-detected over-issuance events

Writes:
  POST /fix/{telegram_id}             — recompute expires_at from payments+admin_grant_days
                                        and shorten the row; logs before/after with proof

ПОРЯДОК МАРШРУТОВ
    Литеральный /candidates объявлен ПЕРЕД /candidates/{telegram_id}. Путь с
    параметром перехватывает любой односегментный путь, объявленный ниже.

СЕКРЕТЫ
    Текст исключения наружу — только через scrub_secrets. Здесь это особенно
    важно: обработчики ходят в панель Remnawave, и в тексте ошибки HTTP-клиента
    приезжает URL панели вместе с токеном в заголовке или в query.

ЦЕНА ЗАПРОСА
    /candidates/{telegram_id} делает 1–2 НЕКЭШИРОВАННЫХ обращения к панели
    Remnawave на каждый вызов. Экран раскрывает карточку кандидата по клику и
    ещё раз — когда пришли по ссылке ?tg= из блока «Требует внимания» на
    сводке. Не вызывайте его списком и не ставьте на автообновление: это
    прямая нагрузка на внешний сервис, которая наружу выглядит как ddos с
    нашего IP.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Path, Query

import database
from app.api.dashboard.deps import require_admin
from app.utils.security import scrub_secrets

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_admin)])


@router.get("/candidates")
async def candidates(limit: int = Query(200, gt=0, le=1000)):
    """Users whose PREMIUM subscription expires more than 8 years from now."""
    try:
        rows = await database.find_over_issuance_candidates(limit)
    except Exception as e:
        logger.error("reconciliation.candidates failed: %s", scrub_secrets(e))
        raise HTTPException(500, f"candidates_failed: {scrub_secrets(e)}")
    logger.info("reconciliation.candidates ok: total=%s", len(rows))
    return {"total": len(rows), "items": rows}


@router.get("/candidates/{telegram_id}")
async def candidate_detail(telegram_id: int = Path(..., gt=0)):
    """Detailed reconciliation view for one user — subscription row, all
    counted/uncounted payments, computed expected expiry, delta, and the
    recent auto-detected over-issuance events for context."""
    try:
        detail = await database.get_reconciliation_detail(telegram_id)
    except Exception as e:
        logger.error(
            "reconciliation.detail failed: telegram_id=%s %s",
            telegram_id,
            scrub_secrets(e),
        )
        raise HTTPException(500, f"detail_failed: {scrub_secrets(e)}")
    if not detail.get("found"):
        logger.info("reconciliation.detail not found: telegram_id=%s", telegram_id)
        raise HTTPException(404, "no_subscription")
    # Логируем каждый вызов: он стоит 1–2 запроса в панель Remnawave, и по
    # логу должно быть видно, если экран начал дёргать его пачками.
    logger.info("reconciliation.detail ok: telegram_id=%s", telegram_id)
    return detail


@router.post("/fix/{telegram_id}")
async def apply_fix(
    telegram_id: int = Path(..., gt=0),
    reason: str = Query("manual reconciliation via dashboard", max_length=500),
    admin: dict = Depends(require_admin),
):
    """Recompute expires_at from approved payments + admin_grant_days,
    write it back, log before/after with proof payment_ids.

    Refuses to run if the recomputed value would EXTEND the subscription
    (we only shorten). Refuses on bypass-only rows (they intentionally sit
    at NOW + 10y)."""
    admin_id = int(admin["sub"])
    try:
        result = await database.apply_reconciliation_fix(
            telegram_id, admin_id, reason=reason,
        )
    except Exception as e:
        logger.exception(
            "reconciliation.fix crash: telegram_id=%s %s",
            telegram_id,
            scrub_secrets(e),
        )
        raise HTTPException(500, f"fix_failed: {scrub_secrets(e)}")
    if not result.get("success"):
        # После рефакторинга single-source-of-truth ошибка возможна ровно
        # одна: Remnawave-панель не приняла PATCH expireAt (сеть, 5xx,
        # entity удалён). db_unavailable — только если пул недоступен.
        err = result.get("error")
        if err == "db_unavailable":
            raise HTTPException(503, result)
        # panel_error / любое другое → 502 (upstream не отвечает).
        logger.error(
            "reconciliation.fix rejected: telegram_id=%s error=%s", telegram_id, err
        )
        raise HTTPException(502, result)
    logger.info(
        "reconciliation.fix ok: telegram_id=%s by=%s days_removed=%s",
        telegram_id,
        admin_id,
        result.get("days_removed"),
    )
    return result


@router.get("/audit-log")
async def audit_log(limit: int = Query(100, gt=0, le=500)):
    """Recent reconciliation actions (POST /fix calls)."""
    try:
        rows = await database.list_reconciliation_log(limit)
    except Exception as e:
        logger.error("reconciliation.audit_log failed: %s", scrub_secrets(e))
        raise HTTPException(500, f"audit_failed: {scrub_secrets(e)}")
    logger.info("reconciliation.audit_log ok: rows=%s", len(rows))
    return rows


@router.get("/over-issuance-log")
async def over_issuance_log(limit: int = Query(100, gt=0, le=500)):
    """Recent auto-detected >8y over-issuance events (written by the
    watchdog after every grant_access write to expires_at)."""
    try:
        rows = await database.list_over_issuance_log(limit)
    except Exception as e:
        logger.error("reconciliation.over_issuance_log failed: %s", scrub_secrets(e))
        raise HTTPException(500, f"over_issuance_failed: {scrub_secrets(e)}")
    logger.info("reconciliation.over_issuance_log ok: rows=%s", len(rows))
    return rows
