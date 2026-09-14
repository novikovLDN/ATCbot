"""Sales funnel worker — a thin loop around app.services.sales_funnel.service.run_pass.

Started by main.start_db_services (the one shared starter). Every few minutes:
one pass over the three chains, a bounded batch each; a failed pass is logged
and the loop goes on; CancelledError propagates (shutdown).
"""
from __future__ import annotations

import asyncio
import logging
import random

import database
from app.services.sales_funnel import service

logger = logging.getLogger(__name__)

INTERVAL_SECONDS = 300
PASS_TIMEOUT_SECONDS = 240
STARTUP_DELAY_SECONDS = (20, 60)


async def sales_funnel_task(bot) -> None:
    await asyncio.sleep(random.uniform(*STARTUP_DELAY_SECONDS))  # noqa: S311 — jitter, not crypto
    while True:
        try:
            if database.DB_READY:
                await asyncio.wait_for(service.run_pass(bot), timeout=PASS_TIMEOUT_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — the loop must survive any pass
            logger.warning("SALES_FUNNEL_PASS_FAILED err=%s: %s", type(e).__name__, str(e)[:200])
        await asyncio.sleep(INTERVAL_SECONDS)
