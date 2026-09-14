"""T16 — free grants (game, promo link, admin bonus, bypass-gift link, broadcast
trial key) through the provisioning outbox. Spec: docs/audit/02_payment_core_plan.md
§A flow 8, §G0; entry point "grants" of provisioning_flags.

Used only when provisioning_flags.is_on("grants"); with the flag off the call
sites keep their legacy code verbatim.

One DB transaction per real-world event, zero HTTP inside it:
  pg_advisory_xact_lock(key)          serializes two deliveries of the same key
  existing job with `key`?  → reuse it, grant nothing again (retry / double click)
  else grant_access(defer_panel=True, _caller_holds_transaction=True)  (days only)
       provisioning.enqueue(key, ent)                                 (outbox)
commit → provisioning.run_now(job) (never raises; failure = job stays pending,
the worker retries, admin is alerted).

Entitlements (owner decision §G0): day grants → tariffs.for_grant (premium only,
0 GB); GB rewards → tariffs.for_bypass_gift (exactly N GB, premium untouched);
a reward with both → ONE job carrying premium_until and bypass_bytes.
"""
from __future__ import annotations

import dataclasses
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import database
from app.services import provisioning, provisioning_flags, tariffs

logger = logging.getLogger(__name__)

ENTRYPOINT = "grants"
JOB_SOURCE = "grants"


def is_on() -> bool:
    """True when the grants entry point must use the outbox."""
    return provisioning_flags.is_on(ENTRYPOINT)


def grant_tier(tariff: Optional[str]) -> str:
    """Premium tier recorded on the job (metadata only — grant_access still gets
    the caller's own tariff). Unknown / legacy values fall back to basic."""
    t = (tariff or "basic").strip().lower()
    try:
        return tariffs.for_grant(t, 1).premium_tier or "basic"
    except tariffs.TariffConfigError:
        return "basic"


def entitlement(*, days: int = 0, gb: int = 0, tier: str = "basic") -> tariffs.Entitlement:
    """Day grant → for_grant (0 GB); GB reward → for_bypass_gift; both → one job."""
    if days and gb:
        ent = tariffs.for_grant(grant_tier(tier), days)
        return dataclasses.replace(ent, bypass_bytes=tariffs.for_bypass_gift(gb).bypass_bytes)
    if days:
        return tariffs.for_grant(grant_tier(tier), days)
    if gb:
        return tariffs.for_bypass_gift(gb)
    raise tariffs.TariffConfigError("grant without days and without GB")


@dataclass(frozen=True)
class GrantOutcome:
    job_id: int
    subscription_end: Optional[datetime]  # premium end (days grants), None for GB-only
    applied: bool       # run_now finished the panel work now (False = queued/alerted)
    duplicate: bool     # the key was already enqueued: nothing granted again


async def grant(
    *,
    telegram_id: int,
    key: str,
    days: int = 0,
    gb: int = 0,
    tier: str = "basic",
    grant_source: str = "admin",
    grant_kwargs: Optional[Dict[str, Any]] = None,
    context: Optional[Dict[str, Any]] = None,
    bot=None,
) -> GrantOutcome:
    """Grant `days` of premium and/or `gb` of bypass for one event identified by
    `key`. Raises only if nothing was committed (the caller's legacy error /
    rollback path applies); once committed the grant is durable."""
    ent = entitlement(days=days, gb=gb, tier=tier)
    pool = await database.get_pool()
    if pool is None:
        raise RuntimeError("grant_outbox.grant: DB pool unavailable")
    duplicate = False
    subscription_end: Optional[datetime] = None
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", key)
            existing = await conn.fetchrow(
                "SELECT id, premium_until FROM provisioning_jobs WHERE idempotency_key = $1", key,
            )
            if existing is not None:
                duplicate = True
                job_id = int(existing["id"])
                if existing["premium_until"] is not None:
                    subscription_end = database._from_db_utc(existing["premium_until"])
            else:
                if days:
                    result = await database.grant_access(
                        telegram_id=telegram_id,
                        duration=timedelta(days=days),
                        source=grant_source,
                        conn=conn,
                        _caller_holds_transaction=True,
                        defer_panel=True,
                        **(grant_kwargs or {}),
                    )
                    subscription_end = (result or {}).get("subscription_end")
                    if subscription_end is None:
                        raise RuntimeError("grant_access returned no subscription_end")
                ctx = {"grant_source": grant_source, "days": days, "gb": gb}
                ctx.update(context or {})
                job_id = await provisioning.enqueue(
                    conn, key=key, telegram_id=telegram_id, ent=ent,
                    premium_until=subscription_end if days else None,
                    source=JOB_SOURCE, context=ctx,
                )
    if duplicate:
        logger.info("GRANT_OUTBOX_DUPLICATE: key=%s tg=%s job=%s", key, telegram_id, job_id)
    applied = await provisioning.run_now(job_id, bot=bot)
    logger.info(
        "GRANT_OUTBOX: key=%s tg=%s job=%s days=%s gb=%s applied=%s duplicate=%s",
        key, telegram_id, job_id, days, gb, applied, duplicate,
    )
    return GrantOutcome(job_id, subscription_end, applied, duplicate)


__all__ = ["ENTRYPOINT", "GrantOutcome", "entitlement", "grant", "grant_tier", "is_on"]
