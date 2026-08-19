"""Remnawave admin operations: backfill numeric id + telegramId в панель 3.x."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.dashboard.deps import require_admin
from app.services import remnawave_backfill

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
