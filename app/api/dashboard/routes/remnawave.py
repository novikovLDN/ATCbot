"""Remnawave admin operations: backfill + premium-limits normalization."""
import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.dashboard.deps import require_admin
from app.services import remnawave_api, remnawave_backfill

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_admin)])


class BackfillStartBody(BaseModel):
    dry_run: bool = False


@router.post("/backfill/start")
async def backfill_start(body: BackfillStartBody):
    """Запустить фоновый backfill (idempotent — 409 если уже бежит)."""
    return await remnawave_backfill.start(dry_run=body.dry_run)


@router.get("/backfill/status")
async def backfill_status():
    """Текущий прогресс/итог backfill (polling каждые 2 сек)."""
    return remnawave_backfill.get_status()


@router.post("/reset-premium-unlimited")
async def reset_premium_unlimited(
    dry_run: bool = True,
    concurrent: int = 3,
    admin: dict = Depends(require_admin),
) -> dict[str, Any]:
    """Сбросить trafficLimitBytes → 0 (безлимит) для ВСЕХ premium entities.

    По ТЗ premium — безлимит по трафику, ограничен только по expireAt.
    Из-за бага в код-пути (renew_remnawave_user / add_traffic шедший
    на premium вместо bypass) премиумы получили GB-лимиты и переходили
    в статус LIMITED. Этот endpoint нормализует всё разом:

      1. Читает `subscriptions.remnawave_premium_id` (numeric id premium
         entity в панели 3.x, кеш из миграции 078).
      2. Для каждого: GET /api/users/{id} → если trafficLimitBytes > 0,
         PATCH → trafficLimitBytes=0, status='ACTIVE'.
      3. dry_run=True → только показывает что БУДЕТ сделано.

    Rate-limit: max_concurrent=3 (осторожно с PATCH-ами).
    """
    import database
    pool = await database.get_pool()
    if pool is None:
        raise HTTPException(500, "db_not_ready")

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT telegram_id, remnawave_premium_id
                 FROM subscriptions
                WHERE remnawave_premium_id IS NOT NULL"""
        )
    if not rows:
        return {"total": 0, "checked": 0, "limited": 0, "reset": 0, "errors": 0}

    checked = 0
    limited = 0
    reset_done = 0
    errors = 0
    samples: list[dict[str, Any]] = []
    sem = asyncio.Semaphore(max(1, min(10, concurrent)))

    async def _one(tg: int, pid: int) -> None:
        nonlocal checked, limited, reset_done, errors
        async with sem:
            try:
                entity = await remnawave_api._request("GET", f"/api/users/{int(pid)}")
                checked += 1
            except Exception as e:
                errors += 1
                logger.warning("reset_premium GET fail tg=%s id=%s: %s", tg, pid, e)
                return
            if not entity:
                errors += 1
                return
            limit = int(entity.get("trafficLimitBytes") or 0)
            status = str(entity.get("status") or "")
            if limit <= 0 and status == "ACTIVE":
                return  # уже безлимит + активный — ничего не делать
            limited += 1
            if len(samples) < 20:
                samples.append({
                    "telegram_id": tg,
                    "premium_id": pid,
                    "before_limit_bytes": limit,
                    "before_status": status,
                })
            if dry_run:
                return
            try:
                # Явный PATCH — обходим safety-guard в update_user
                # (тот дропает trafficLimitBytes для premium), делаем raw
                # запрос: id + trafficLimitBytes=0 + status=ACTIVE.
                await remnawave_api._request(
                    "PATCH", "/api/users",
                    json={
                        "id": int(pid),
                        "trafficLimitBytes": 0,
                        "trafficLimitStrategy": "NO_RESET",
                        "status": "ACTIVE",
                    },
                )
                reset_done += 1
            except Exception as e:
                errors += 1
                logger.warning("reset_premium PATCH fail tg=%s id=%s: %s", tg, pid, e)

    await asyncio.gather(*(_one(int(r["telegram_id"]), int(r["remnawave_premium_id"])) for r in rows))

    logger.info(
        "PREMIUM_RESET_UNLIMITED admin=%s dry=%s total=%d limited=%d reset=%d errors=%d",
        admin.get("sub"), dry_run, len(rows), limited, reset_done, errors,
    )
    return {
        "total": len(rows),
        "checked": checked,
        "limited": limited,
        "reset": reset_done,
        "errors": errors,
        "dry_run": dry_run,
        "samples": samples,
    }
