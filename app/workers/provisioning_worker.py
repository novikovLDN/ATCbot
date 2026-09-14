"""Provisioning worker — drains the provisioning_jobs outbox (T5).

Spec: docs/audit/02_payment_core_plan.md §A.4, §B.4-5.

Every `interval` seconds (15 s): repeatedly claim() the next due job
(next_attempt_at <= now, per-user FIFO, lease; an expired 'running' lease is
claimable again) and hand it to provisioning.process_claimed, until nothing is
due or MAX_JOBS_PER_TICK is reached. Backoff, 24 h → dead and admin alerts
live in process_claimed (first failure / dead forced, the rest on cooldown).
Under a flood the forced alerts are budgeted per kind and the overflow is
buffered; every tick (and best effort on shutdown) this worker flushes it as
one digest per kind per window (provisioning.flush_alert_digests).

- Runs ALWAYS (no feature flag) so already-enqueued jobs never hang; main.py
  only requires DB_READY.
- Per-user serialization: provisioning.user_lock (shared with run_now) +
  the DB lease/FIFO.
- One job can't freeze the loop: apply is bounded by JOB_TIMEOUT_S inside
  process_claimed, the whole job by JOB_TIMEOUT_S + JOB_GUARD_S here; both
  are below the lease so a cut job is simply reclaimed after it expires.
- One job's exception never kills the loop; CancelledError propagates.
- Logs: PROVISIONING_WORKER_TICK summary at INFO only when something was
  processed; per-job lines at DEBUG.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict

import database.provisioning_jobs as provisioning_jobs
from app.core.structured_logger import log_event
from app.services import provisioning

logger = logging.getLogger(__name__)

INTERVAL_S = 15.0
MAX_JOBS_PER_TICK = 50
LEASE_S = provisioning_jobs.DEFAULT_LEASE_S      # 120 s
JOB_TIMEOUT_S = 45.0     # panel work of one job (passed to process_claimed)
JOB_GUARD_S = 30.0       # + lock wait, mark_*, payment_errors, alert send
SHUTDOWN_FLUSH_S = 5.0   # best-effort alert digest send when cancelled


@dataclass
class TickStats:
    processed: int = 0
    done: int = 0
    retried: int = 0
    dead: int = 0
    errors: int = 0          # exception / guard timeout / unknown outcome
    claim_failed: bool = False


async def provisioning_worker_task(bot, *, interval: float = INTERVAL_S) -> None:
    """Main loop. Never returns; cancellation propagates."""
    logger.info(
        "PROVISIONING_WORKER started (interval=%ss, max_jobs=%s, job_timeout=%ss, lease=%ss)",
        interval, MAX_JOBS_PER_TICK, JOB_TIMEOUT_S, LEASE_S,
    )
    from app.core import runtime_health  # dashboard liveness (in-memory)
    runtime_health.register("provisioning_worker", interval_s=interval + 120)
    while True:
        try:
            await run_tick(bot)
            runtime_health.beat("provisioning_worker")
        except asyncio.CancelledError:
            await _flush_alerts_on_shutdown(bot)
            logger.info("PROVISIONING_WORKER stopped (cancelled)")
            raise
        except Exception as e:
            logger.error("PROVISIONING_WORKER_TICK_ERROR: %s: %s", type(e).__name__, e, exc_info=True)
            runtime_health.fail("provisioning_worker", e)
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            await _flush_alerts_on_shutdown(bot)
            logger.info("PROVISIONING_WORKER stopped (cancelled)")
            raise


async def _flush_alerts_on_shutdown(bot) -> None:
    """Best effort: send whatever alert digests are still buffered."""
    try:
        await asyncio.wait_for(provisioning.flush_alert_digests(bot, final=True), SHUTDOWN_FLUSH_S)
    except (Exception, asyncio.CancelledError) as e:
        logger.warning("PROVISIONING_WORKER_SHUTDOWN_FLUSH_FAILED: %s: %s", type(e).__name__, e)
    left = provisioning.pending_alert_counts()
    if left:
        # payment_errors still holds every one of them
        logger.critical("PROVISIONING_ALERTS_UNSENT_ON_SHUTDOWN: %s", left)


async def run_tick(
    bot, *, max_jobs: int = MAX_JOBS_PER_TICK, job_timeout: float = JOB_TIMEOUT_S,
) -> TickStats:
    """Process due jobs until none is due or `max_jobs` were taken."""
    stats = TickStats()
    started = time.monotonic()
    for _ in range(max_jobs):
        try:
            job = await provisioning_jobs.claim(lease_s=LEASE_S)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            stats.claim_failed = True
            logger.warning("PROVISIONING_WORKER_CLAIM_FAILED: %s: %s", type(e).__name__, e)
            break
        if job is None:
            break
        stats.processed += 1
        outcome = await _process_one(job, bot, job_timeout)
        setattr(stats, outcome, getattr(stats, outcome) + 1)

    # buffered provisioning alerts (flood) → at most one digest per kind per window
    try:
        await provisioning.flush_alert_digests(bot)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning("PROVISIONING_WORKER_ALERT_FLUSH_FAILED: %s: %s", type(e).__name__, e)

    duration_ms = int((time.monotonic() - started) * 1000)
    msg = (
        f"PROVISIONING_WORKER_TICK processed={stats.processed} done={stats.done} "
        f"retried={stats.retried} dead={stats.dead} errors={stats.errors}"
    )
    if stats.processed or stats.claim_failed:
        log_event(
            logger, component="worker", operation="provisioning_tick",
            outcome="degraded" if (stats.errors or stats.dead or stats.claim_failed) else "success",
            duration_ms=duration_ms, message=msg,
            level="warning" if (stats.errors or stats.dead) else "info",
        )
    else:
        logger.debug(msg)
    return stats


async def _process_one(job: Dict[str, Any], bot, job_timeout: float) -> str:
    """Returns the TickStats counter to bump: done / retried / dead / errors."""
    job_id = job.get("id")
    try:
        ok = await asyncio.wait_for(
            provisioning.process_claimed(job, bot=bot, timeout=job_timeout),
            job_timeout + JOB_GUARD_S,
        )
    except asyncio.TimeoutError:
        # Lease still held → the job is reclaimed once it expires.
        logger.error(
            "PROVISIONING_WORKER_JOB_TIMEOUT: job=%s tg=%s exceeded %ss",
            job_id, job.get("telegram_id"), job_timeout + JOB_GUARD_S,
        )
        return "errors"
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error(
            "PROVISIONING_WORKER_JOB_ERROR: job=%s tg=%s %s: %s",
            job_id, job.get("telegram_id"), type(e).__name__, e, exc_info=True,
        )
        return "errors"
    if ok:
        logger.debug("PROVISIONING_WORKER_JOB: job=%s outcome=done", job_id)
        return "done"
    outcome = await _failed_outcome(job)
    logger.debug("PROVISIONING_WORKER_JOB: job=%s outcome=%s", job_id, outcome)
    return outcome


async def _failed_outcome(job: Dict[str, Any]) -> str:
    """process_claimed returned False: read the row to tell retry from dead."""
    try:
        row = await provisioning_jobs.get_by_key(job["idempotency_key"])
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.debug("PROVISIONING_WORKER_STATUS_READ_FAILED: job=%s %s", job.get("id"), e)
        return "errors"
    status = (row or {}).get("status")
    if status == "pending":
        return "retried"
    if status == "dead":
        return "dead"
    if status == "done":
        return "done"
    return "errors"      # e.g. mark_* failed → still 'running' until the lease expires


__all__ = ["TickStats", "provisioning_worker_task", "run_tick"]
