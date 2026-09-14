"""Remnawave panel — read-only statistics for the Panel screen.

Everything goes through app/services/panel_stats (cached, GET only,
short timeouts). The DB side is read before and separately from the
panel calls; no connection is held across HTTP.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query

from app.api.dashboard.deps import require_admin
from app.services import panel_stats
from database import metrics as mx

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_admin)])


@router.get("/overview")
async def panel_overview():
    db_counts = await mx.panel_entity_counts()
    data = await panel_stats.system_overview()
    return {
        **data,
        "db": db_counts,
        "discrepancy": panel_stats.discrepancy(data["system"], db_counts),
        "cache_ttl_seconds": panel_stats.PANEL_TTL_SECONDS,
    }


@router.get("/nodes")
async def panel_nodes():
    return await panel_stats.nodes_overview()


@router.get("/bandwidth")
async def panel_bandwidth(days: int = Query(14, ge=2, le=90)):
    return await panel_stats.nodes_usage(days)
