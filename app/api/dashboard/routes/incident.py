"""Incident-mode banner shown to all users until the admin disables it."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import database
from app.api.dashboard.deps import require_admin
from app.events import bus

router = APIRouter(dependencies=[Depends(require_admin)])


@router.get("")
async def incident_get():
    try:
        return await database.get_incident_settings()
    except Exception as e:
        raise HTTPException(500, f"incident_get_failed: {e}")


class IncidentSet(BaseModel):
    is_active: bool
    incident_text: Optional[str] = Field(None, max_length=2000)


@router.post("")
async def incident_set(body: IncidentSet, admin: dict = Depends(require_admin)):
    try:
        await database.set_incident_mode(body.is_active, body.incident_text)
    except Exception as e:
        raise HTTPException(500, f"incident_set_failed: {e}")
    bus.publish({
        "type": "incident:updated",
        "is_active": body.is_active,
        "by": admin.get("sub"),
    })
    return {"ok": True, "is_active": body.is_active}
