"""
Управление автоуведомлениями бота — список / детали / edit / stats.

GET  /automated-notifications/               — список всех известных
GET  /automated-notifications/{key}          — детали + trigger_config
PATCH /automated-notifications/{key}         — обновить text/enable/trigger
GET  /automated-notifications/{key}/stats    — статистика отправок
POST /automated-notifications/{key}/reset    — сбросить custom_text к default

Всё требует admin-JWT через require_admin (dependency в router).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field

from app.api.dashboard.deps import require_admin
from app.services.automated_notifications import (
    REGISTRY, get_trigger_config, sync_registry_to_db,
)
from app.services.automated_notifications.helper import (
    get_row, get_stats, update_notification,
)
from database.core import get_pool

router = APIRouter(dependencies=[Depends(require_admin)])


def _serialize(value: Any) -> Any:
    if isinstance(value, list):
        return [_serialize(v) for v in value]
    if isinstance(value, dict):
        return {k: _serialize(v) for k, v in value.items()}
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


@router.get("/")
async def list_notifications() -> List[Dict[str, Any]]:
    """Все зарегистрированные автоуведомления с их статусом.

    Ответ — плоский список dict'ов, каждый содержит:
      key, title, description, category,
      is_enabled, has_custom_text, default_text_ru,
      trigger_config, template_vars, updated_at.

    Если БД пустая (миграция только что применена) — вернём
    записи из in-memory REGISTRY (без is_enabled, но с дефолтами),
    чтобы UI не был пустым до первой синхронизации.
    """
    pool = await get_pool()
    if pool is None:
        return []
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT key, title, description, category, default_text_ru,
                      custom_text_ru, is_enabled, trigger_config,
                      template_vars, updated_at, last_edited_by
               FROM automated_notifications
               ORDER BY category, key"""
        )
    if not rows:
        # Fallback — sync принудительно и повторим.
        try:
            await sync_registry_to_db()
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """SELECT key, title, description, category, default_text_ru,
                              custom_text_ru, is_enabled, trigger_config,
                              template_vars, updated_at, last_edited_by
                       FROM automated_notifications
                       ORDER BY category, key"""
                )
        except Exception:
            pass
    out = []
    for r in rows:
        out.append({
            "key": r["key"],
            "title": r["title"],
            "description": r["description"],
            "category": r["category"],
            "is_enabled": bool(r["is_enabled"]),
            "has_custom_text": bool(r["custom_text_ru"]),
            "default_text_ru": r["default_text_ru"],
            "custom_text_ru": r["custom_text_ru"],
            "trigger_config": r["trigger_config"] or {},
            "template_vars": list(r["template_vars"] or []),
            "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
            "last_edited_by": r["last_edited_by"],
        })
    return out


@router.get("/{key}")
async def get_notification(key: str = Path(..., min_length=3, max_length=80)):
    row = await get_row(key)
    if row is None:
        raise HTTPException(404, f"notification key not found: {key}")
    # Полные детали для edit-modal
    pool = await get_pool()
    async with pool.acquire() as conn:
        r = await conn.fetchrow(
            """SELECT key, title, description, category, default_text_ru,
                      custom_text_ru, is_enabled, trigger_config,
                      template_vars, updated_at, last_edited_by
               FROM automated_notifications WHERE key = $1""",
            key,
        )
    if r is None:
        raise HTTPException(404, "not found")
    return _serialize({
        "key": r["key"],
        "title": r["title"],
        "description": r["description"],
        "category": r["category"],
        "is_enabled": bool(r["is_enabled"]),
        "has_custom_text": bool(r["custom_text_ru"]),
        "default_text_ru": r["default_text_ru"],
        "custom_text_ru": r["custom_text_ru"],
        "trigger_config": r["trigger_config"] or {},
        "template_vars": list(r["template_vars"] or []),
        "updated_at": r["updated_at"],
        "last_edited_by": r["last_edited_by"],
    })


class UpdatePayload(BaseModel):
    """PATCH-body. Все поля опциональные.

    - custom_text_ru: строка → override. Пустая строка → reset к дефолту.
    - is_enabled: полностью отключить/включить отправку.
    - trigger_config: свободный dict, для reminder — {before_expiry_hours, tolerance_hours}.
    """
    custom_text_ru: Optional[str] = Field(None, max_length=4096)
    is_enabled: Optional[bool] = None
    trigger_config: Optional[Dict[str, Any]] = None


@router.patch("/{key}")
async def patch_notification(
    payload: UpdatePayload,
    key: str = Path(..., min_length=3, max_length=80),
    admin: dict = Depends(require_admin),
):
    """Update. Empty payload → 400 (нечего менять).

    Валидация trigger_config: если ключ известен (в REGISTRY) и в
    trigger_config есть 'before_expiry_hours' — оно должно быть
    числом в [0.1, 720] (защита от вреда).
    """
    if payload.model_dump(exclude_none=True) == {}:
        raise HTTPException(400, "empty patch")
    # Валидация trigger_config для reminders
    tc = payload.trigger_config
    if tc is not None:
        if "before_expiry_hours" in tc:
            try:
                v = float(tc["before_expiry_hours"])
                if not (0.1 <= v <= 720):
                    raise HTTPException(
                        400, "before_expiry_hours must be in [0.1, 720]"
                    )
            except (TypeError, ValueError):
                raise HTTPException(400, "before_expiry_hours must be a number")
        if "tolerance_hours" in tc:
            try:
                v = float(tc["tolerance_hours"])
                if not (0 <= v <= 48):
                    raise HTTPException(
                        400, "tolerance_hours must be in [0, 48]"
                    )
            except (TypeError, ValueError):
                raise HTTPException(400, "tolerance_hours must be a number")
    ok = await update_notification(
        key,
        custom_text_ru=payload.custom_text_ru,
        is_enabled=payload.is_enabled,
        trigger_config=payload.trigger_config,
        edited_by=int(admin["sub"]),
    )
    if not ok:
        raise HTTPException(404, "notification key not found")
    return {"ok": True, "key": key}


@router.post("/{key}/reset")
async def reset_notification_text(
    key: str = Path(..., min_length=3, max_length=80),
    admin: dict = Depends(require_admin),
):
    """Сбросить custom_text_ru к None (использовать дефолт из кода)."""
    ok = await update_notification(
        key, custom_text_ru="", edited_by=int(admin["sub"]),
    )
    if not ok:
        raise HTTPException(404, "not found")
    return {"ok": True, "key": key, "reset": True}


@router.get("/{key}/stats")
async def notification_stats(
    key: str = Path(..., min_length=3, max_length=80),
    hours: int = Query(168, gt=0, le=8760),
):
    """Sent/failed/blocked/skipped за N часов."""
    stats = await get_stats(key, hours=hours)
    return {"key": key, "hours": hours, **stats}
