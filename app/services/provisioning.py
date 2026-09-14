"""Provisioning core: the single place that grants premium days and bypass GB
in Remnawave for a paid purchase / grant. Spec: docs/audit/02_payment_core_plan.md
§A.3, §B, task T4.

  enqueue(conn, …)   outbox write INSIDE the caller's billing transaction
                     (provisioning_jobs.insert_job, idempotent by key); no HTTP.
  run_now(job_id)    fast path after commit: claim → apply → mark_done; on
                     failure mark_retry / mark_dead + admin alert. Never raises.
  apply(job)         the panel work; raises ProvisioningTransient (retry) or
                     ProvisioningPermanent (dead, manual action).

Premium — absolute target, never shortened:
  target = max(job.premium_until, subscriptions.expires_at). Panel expireAt >=
  target → no PATCH. Present → PATCH expireAt=target by the entity's numeric id.
  Absent → create_premium_user_entity(expire_at=target); an ADOPTED entity may
  keep a stale expireAt (its adopt-PATCH can fail silently) → re-read and PATCH
  up to target.

Bypass — CAS with a persisted plan (+N exactly once, crash-safe):
  1. no plan yet: read L → save_bypass_plan(base=L, target=L+N), committed
     before any PATCH (absent entity → base=0, target=N);
  2. re-read C: absent → create with limit=target (an adopted entity keeps its
     old limit → CAS on it); C == target → already applied; C == base → PATCH
     trafficLimitBytes=target (absolute, _trust_bypass); else → Permanent conflict.
  A crash between PATCH and mark_done is seen on retry as C == target.

Delivery verification (owner rule: "bought a month, the panel still shows
20 October" must be caught): after the steps succeed apply() re-reads the
panel — premium expireAt >= ceil(job.premium_until) and status ACTIVE, bypass
trafficLimitBytes == the planned target and status ACTIVE. Otherwise
DeliveryMismatch (a Transient): the job is NOT done and retries (never-shorten /
CAS make that safe); the first mismatch is a forced "DELIVERY_MISMATCH" alert,
after MISMATCH_DEAD_AFTER_ATTEMPTS attempts it goes dead (+ forced alert).

Reads use remnawave_api.get_premium_state / get_bypass_state, which tell
"absent" from "unavailable" — an outage is always Transient, never a create.
Panel functions are reached through module attributes (tests patch them).
No DB transaction is held during HTTP: claim / save_bypass_plan / mark_* /
cache writes are each their own short statement.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid as uuid_lib
from collections import Counter, deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator, Dict, Optional, Tuple

import config
import database
import database.provisioning_jobs as provisioning_jobs
from app.services import admin_alerts, remnawave_api, remnawave_bypass, remnawave_premium, tariffs

logger = logging.getLogger(__name__)

GIB = 1024 ** 3
BACKOFF_BASE_S = 30
BACKOFF_MAX_S = 3600
DEAD_AFTER = timedelta(hours=24)
ALERT_CATEGORY = "payment"
LOG_STAGE = "provisioning"
DEFAULT_RUN_NOW_TIMEOUT_S = 8.0

# create_*_user_entity errors that retrying cannot fix
_PERMANENT_CREATE_ERRORS = frozenset({"conflict_unrelated_user", "non_positive_traffic_limit"})


class ProvisioningTransient(Exception):
    """Retry later: panel unavailable, timeout, failed PATCH, DB hiccup."""


class ProvisioningPermanent(Exception):
    """Retrying will not help (bypass CAS conflict, username held by a stranger)."""


MISMATCH_TAG = "DELIVERY_MISMATCH"
MISMATCH_DEAD_AFTER_ATTEMPTS = 6     # ≈30 min of backoff (60+120+240+480+960 s)


class DeliveryMismatch(ProvisioningTransient):
    """The panel accepted the steps, but a re-read does not show the job's target."""


PREMIUM_DISABLED_TAG = "PREMIUM_DISABLED"

# F7 (docs/providers/remnawave_3.4.3.md): set on the claimed job dict by
# _apply_premium when the premium target is already in the past.
PERIOD_ENDED_TAG = "PREMIUM_PERIOD_ENDED"
PERIOD_ENDED_KEY = "_premium_period_ended"


class PremiumDisabled(ProvisioningPermanent):
    """P1-5: the premium entity already has the paid expireAt but is DISABLED in
    the panel. Never auto-enabled (an admin may have disabled the user on
    purpose): the job goes terminal on the first attempt (no pointless retries)
    with ONE forced alert. The bypass GB of the job are delivered first."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def backoff_seconds(attempts: int) -> int:
    """Delay before the next attempt: min(30s · 2^attempts, 1h)."""
    n = max(0, min(int(attempts), 20))
    return min(BACKOFF_BASE_S * (2 ** n), BACKOFF_MAX_S)


# ── enqueue ────────────────────────────────────────────────────────────

async def enqueue(
    conn,
    *,
    key: str,
    telegram_id: int,
    ent,
    premium_until: Optional[datetime],
    source: str,
    context: Optional[Dict[str, Any]] = None,
) -> int:
    """Write the provisioning job in the caller's open billing transaction
    (outbox: billing without a job, or a job without billing, is impossible).
    Idempotent by `key` — returns the existing job id on a duplicate. No HTTP.
    """
    in_tx = getattr(conn, "is_in_transaction", None)
    if callable(in_tx) and not in_tx():
        raise RuntimeError("provisioning.enqueue must run inside the caller's billing transaction")
    ctx = dict(context or {})
    ctx.setdefault("premium_days", ent.premium_days)
    ctx.setdefault("premium_tier", ent.premium_tier)
    return await provisioning_jobs.insert_job(
        conn, key=key, telegram_id=telegram_id, source=source, ent=ent,
        premium_until=premium_until, context=ctx,
    )


# ── run_now / process_claimed ──────────────────────────────────────────

async def run_now(job_id: int, *, bot=None, timeout: float = DEFAULT_RUN_NOW_TIMEOUT_S) -> bool:
    """Claim `job_id` and apply it now. Never raises. True only if this call
    completed the job; False if it failed (queued for retry / dead, alerted)
    or could not be claimed (already done, leased, behind an earlier job)."""
    try:
        job = await provisioning_jobs.claim(job_id)
    except Exception as e:
        logger.error("PROVISIONING_CLAIM_FAILED: job=%s %s: %s", job_id, type(e).__name__, e)
        return False
    if job is None:
        logger.info("PROVISIONING_RUN_NOW_SKIPPED: job=%s (done, leased or queued behind an earlier job)", job_id)
        return False
    return await process_claimed(job, bot=bot, timeout=timeout)


_USER_LOCKS: Dict[int, asyncio.Lock] = {}
_USER_LOCK_REFS: Dict[int, int] = {}


@asynccontextmanager
async def user_lock(telegram_id: int) -> AsyncIterator[None]:
    """In-process per-user serialization: one job of a user applies at a time
    in this process (run_now and the worker both go through process_claimed).
    The DB lease + per-user FIFO in claim() cover restarts; this covers a lease
    that expired while an apply of the same user is still running here.
    The entry is dropped when nobody holds or waits for it."""
    tg = int(telegram_id)
    lock = _USER_LOCKS.get(tg)
    if lock is None:
        lock = _USER_LOCKS[tg] = asyncio.Lock()
    _USER_LOCK_REFS[tg] = _USER_LOCK_REFS.get(tg, 0) + 1
    try:
        async with lock:
            yield
    finally:
        _USER_LOCK_REFS[tg] -= 1
        if _USER_LOCK_REFS[tg] <= 0:
            _USER_LOCK_REFS.pop(tg, None)
            _USER_LOCKS.pop(tg, None)


async def process_claimed(job: Dict[str, Any], *, bot=None, timeout: Optional[float] = None) -> bool:
    """Apply an already-claimed job and record the outcome, under user_lock.
    Never raises (except task cancellation). Shared by run_now and the worker."""
    async with user_lock(int(job.get("telegram_id") or 0)):
        return await _process_claimed_locked(job, bot=bot, timeout=timeout)


async def _process_claimed_locked(job: Dict[str, Any], *, bot, timeout: Optional[float]) -> bool:
    job_id = job.get("id")
    try:
        await _apply_with_timeout(job, timeout)
    except ProvisioningPermanent as e:
        await _record_failure(job, e, permanent=True, bot=bot)
        return False
    except Exception as e:  # Transient, timeout, or an unexpected bug → retry
        await _record_failure(job, e, permanent=False, bot=bot)
        return False
    try:
        await provisioning_jobs.mark_done(int(job_id))
    except Exception as e:
        # Panel work is applied; the lease expires and the idempotent retry is a no-op.
        logger.error("PROVISIONING_MARK_DONE_FAILED: job=%s %s: %s", job_id, type(e).__name__, e)
        return False
    logger.info(
        "PROVISIONING_DONE: job=%s key=%s tg=%s attempts=%s",
        job_id, job.get("idempotency_key"), job.get("telegram_id"), job.get("attempts"),
    )
    if job.get(PERIOD_ENDED_KEY) is not None:
        await _alert_period_ended(job, bot)
    return True


async def _alert_period_ended(job: Dict[str, Any], bot) -> None:
    """F7: ONE forced alert for a job finished after its paid period ended
    (sent once: the job is done, later ticks never pick it again). Never raises."""
    until = job[PERIOD_ENDED_KEY]
    text = "\n".join([
        f"{PERIOD_ENDED_TAG}: the job was processed after the paid period had already ended — "
        "premium NOT extended (the panel rejects a past expireAt), job finished without retries.",
        "Access is not lost: the premium entity is expired, as it should be. "
        "Decide manually whether the user needs compensation.",
        f"user: tg:{job.get('telegram_id')}",
        f"purchase: {job.get('idempotency_key')}",
        f"paid until: {until.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        f"job_id: {job.get('id')}, source: {job.get('source')}, tariff: {job.get('tariff_key')}",
        f"bypass: +{_fmt_gb(int(job.get('bypass_add_bytes') or 0))} GB (delivered)",
    ])
    logger.critical(
        "PROVISIONING_PREMIUM_PERIOD_ENDED_DONE: job=%s key=%s tg=%s paid_until=%s",
        job.get("id"), job.get("idempotency_key"), job.get("telegram_id"), until.isoformat(),
    )
    # HOW_IT_WORKS P2: never only a log line — payment_errors + the shared
    # budget/digest (a failed send or no bot keeps it for the next digest).
    try:
        await database.log_payment_error(
            stage="premium_period_ended",
            telegram_id=job.get("telegram_id"),
            purchase_id=job.get("idempotency_key"),
            error_code="period_ended",
            error_message=f"job {job.get('id')} done after the paid period ended ({until.isoformat()})",
        )
    except Exception as e:
        logger.warning("PROVISIONING_PAYMENT_ERROR_LOG_FAILED: job=%s %s", job.get("id"), e)
    await report_payment_alert(
        "period_ended", text, reason=f"period ended, source={job.get('source')}",
        telegram_id=job.get("telegram_id"), key=job.get("idempotency_key"), bot=bot,
    )


async def _apply_with_timeout(job: Dict[str, Any], timeout: Optional[float]) -> None:
    if timeout is None:
        await apply(job)
        return
    try:
        await asyncio.wait_for(apply(job), timeout)
    except asyncio.TimeoutError as e:
        raise ProvisioningTransient(f"timeout after {timeout}s") from e


# ── apply ──────────────────────────────────────────────────────────────

async def apply(job: Dict[str, Any]) -> None:
    """Make the panel reflect `job`. Idempotent. Raises ProvisioningTransient /
    ProvisioningPermanent. After the premium entity is in place a deferred
    grant_access (activation_status='pending') is completed (T7)."""
    premium: Optional[Tuple[Optional[str], Optional[str]]] = None
    if job.get("premium_until") is not None:
        premium = await _apply_premium(job)
    bypass_url: Optional[str] = None
    if int(job.get("bypass_add_bytes") or 0) > 0:
        try:
            bypass_url = await _apply_bypass(job)
        except Exception:
            if premium is not None:
                # Premium works already: hand the user the key even while the
                # GB part retries or is dead (never let activation_worker re-provision).
                await _complete_activation_if_pending(job, premium, None, best_effort=True)
            raise
    if premium is not None:
        await _complete_activation_if_pending(job, premium, bypass_url)
    await _verify_delivery(job)


async def _complete_activation_if_pending(
    job: Dict[str, Any], premium: Tuple[Optional[str], Optional[str]], bypass_url: Optional[str],
    *, best_effort: bool = False,
) -> None:
    """database.complete_activation for a row still in activation_status='pending'
    (idempotent: an active row is never touched). Keys as purchase_flow writes
    them: vpn_key = premium subscription URL, vpn_key_plus = bypass URL."""
    tg = int(job["telegram_id"])
    uuid, url = premium
    try:
        sub = await _db(database.get_subscription_any(tg), "get_subscription_any")
        if not sub or sub.get("activation_status") != "pending":
            return
        if not url:
            raise ProvisioningTransient("premium subscriptionUrl missing — activation left pending")
        changed = await _db(
            database.complete_activation(tg, vpn_key=url, vpn_key_plus=bypass_url or None, uuid=uuid),
            "complete_activation",
        )
    except ProvisioningTransient as e:
        if not best_effort:
            raise
        logger.warning("PROVISIONING_ACTIVATION_COMPLETE_FAILED: job=%s tg=%s %s", job.get("id"), tg, e)
        return
    if changed:
        logger.info("PROVISIONING_ACTIVATION_COMPLETED: job=%s tg=%s", job.get("id"), tg)


def _activation_uuid(sub: Optional[Dict[str, Any]], fallback: Optional[str]) -> Optional[str]:
    """subscriptions.uuid as purchase_flow writes it: a valid legacy uuid stays."""
    legacy = (sub or {}).get("uuid")
    return legacy if remnawave_premium._is_valid_full_uuid(legacy) else fallback


async def _panel(coro, what: str):
    """Await a panel call; any exception becomes Transient."""
    try:
        return await coro
    except (ProvisioningTransient, ProvisioningPermanent):
        raise
    except Exception as e:
        raise ProvisioningTransient(f"{what}: {type(e).__name__}: {e}") from e


async def _read_state(reader, telegram_id: int, what: str) -> Tuple[str, Optional[Dict[str, Any]]]:
    state, ent = await _panel(reader(telegram_id), f"{what} read")
    if state == "present" and isinstance(ent, dict):
        return "present", ent
    if state == "absent":
        return "absent", None
    if state == "unavailable":
        raise ProvisioningTransient(f"panel unavailable ({what} read)")
    raise ProvisioningTransient(f"unexpected {what} state {state!r}")


def _raise_for_create(res, what: str) -> None:
    msg = f"{what} create failed: status={res.status} error={res.error}"
    if res.error in _PERMANENT_CREATE_ERRORS:
        raise ProvisioningPermanent(msg)
    raise ProvisioningTransient(msg)


def _entity_id(ent: Dict[str, Any]) -> Optional[int]:
    try:
        return int(ent["id"]) if ent.get("id") is not None else None
    except (TypeError, ValueError):
        return None


def _entity_uuid(ent: Dict[str, Any]) -> Optional[str]:
    v = ent.get("uuid") or ent.get("vlessUuid")
    return str(v) if v else None


def _premium_tag(job: Dict[str, Any]) -> Tuple[Optional[str], bool]:
    """(tag, retag) for the premium entity of `job` (owner 2026-09-14, tags by
    tariff). A purchase / renewal / trial / gift carries its tariff tag and sets
    it (retag=True). A day grant ("grant") keeps the entity's current tag; only
    a NEW entity gets the grant tier's tag (BASIC / PLUS)."""
    key = str(job.get("tariff_key") or "")
    if key == "grant":
        return tariffs.premium_panel_tag((job.get("context") or {}).get("premium_tier")), False
    return tariffs.premium_panel_tag(key), True


def _invalidate_aggregator(telegram_id: int) -> None:
    try:
        from app.services import sub_aggregator
        sub_aggregator.invalidate_bg(telegram_id)
    except Exception:
        pass


async def _db(coro, what: str):
    try:
        return await coro
    except Exception as e:
        raise ProvisioningTransient(f"db {what}: {type(e).__name__}: {e}") from e


# ── premium ────────────────────────────────────────────────────────────

def _ceil_second(dt: datetime) -> datetime:
    """The panel stores whole seconds; round UP so the target is never shortened."""
    if dt.microsecond:
        return dt.replace(microsecond=0) + timedelta(seconds=1)
    return dt


def _parse_expire(value: Any) -> datetime:
    try:
        if isinstance(value, datetime):
            dt = value
        else:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as e:
        # Unknown current expiry: a PATCH could shorten it — refuse.
        raise ProvisioningPermanent(f"unparseable premium expireAt {value!r}") from e
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


async def _apply_premium(job: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """Returns (uuid, subscription_url) for complete_activation."""
    tg = int(job["telegram_id"])
    target = _ceil_second(job["premium_until"])
    sub = await _db(database.get_subscription_any(tg), "get_subscription_any")
    db_expires = (sub or {}).get("expires_at")
    # A bypass-only row carries a placeholder expires_at (now + 3650d, see
    # ensure_bypass_only_subscription) — it is not a premium date and must never
    # become the panel premium target.
    if (sub or {}).get("is_bypass_only"):
        db_expires = None
    if isinstance(db_expires, datetime) and db_expires.tzinfo is not None and db_expires > target:
        target = _ceil_second(db_expires)

    # Devices by tariff (owner 2026-09-14: Basic 10, Plus 14) — the job's tier.
    tier = (job.get("context") or {}).get("premium_tier")
    tag, retag = _premium_tag(job)
    patch_tag = tag if retag else None
    state, ent = await _read_state(remnawave_api.get_premium_state, tg, "premium")
    if state == "present":
        if await _ensure_premium_expire(tg, ent, target, tier=tier, tag=patch_tag):
            job[PERIOD_ENDED_KEY] = target
        await _persist_premium_cache(
            tg, uuid=_entity_uuid(ent), url=ent.get("subscriptionUrl"),
            short=ent.get("shortUuid"), panel_id=_entity_id(ent), force=False,
        )
        return _activation_uuid(sub, ent.get("vlessUuid") or _entity_uuid(ent)), ent.get("subscriptionUrl")

    legacy_uuid = (sub or {}).get("uuid")
    requested_uuid = legacy_uuid if remnawave_premium._is_valid_full_uuid(legacy_uuid) else str(uuid_lib.uuid4())
    res = await _panel(
        remnawave_premium.create_premium_user_entity(
            tg, requested_uuid=requested_uuid, expire_at=target,
            description=f"Premium via bot ({job.get('tariff_key')})",
            tier=tier,
            tag=tag,
            tag_on_adopt=retag,
        ),
        "premium create",
    )
    if not res.ok:
        _raise_for_create(res, "premium")
    await _persist_premium_cache(
        tg, uuid=res.panel_uuid, url=res.subscription_url, short=res.short_uuid,
        panel_id=res.panel_id, force=True,
    )
    _invalidate_aggregator(tg)
    logger.info(
        "PROVISIONING_PREMIUM_CREATED: job=%s tg=%s recovered=%s expire=%s",
        job.get("id"), tg, res.recovered, target.isoformat(),
    )
    if res.recovered:
        # Adoption: its expireAt PATCH may have failed (stale value) → verify.
        state, ent = await _read_state(remnawave_api.get_premium_state, tg, "premium")
        if state != "present":
            raise ProvisioningTransient("premium entity not visible after adoption")
        if await _ensure_premium_expire(tg, ent, target, tier=tier, tag=patch_tag):
            job[PERIOD_ENDED_KEY] = target
    return requested_uuid, res.subscription_url


async def _ensure_premium_expire(tg: int, ent: Dict[str, Any], target: datetime,
                                 tier: Optional[str] = None, tag: Optional[str] = None) -> bool:
    """PATCH the premium expireAt forward to `target`. True = the paid period
    already ended (target in the past): no PATCH — the panel 3.4.3 rejects a
    past expireAt with 400 and a retry cannot fix that (F7).

    `tier`: the same PATCH sets the tariff's device limit (owner 2026-09-14).
    No extra write when the date needs no PATCH (the outbox stays idempotent
    and minimal — a retry never re-PATCHes); such an entity gets its cap with
    its next extend (every paid renewal / tariff change extends premium).

    `tag` (None = keep): rides in the same PATCH when the entity's tag differs.
    When the date needs no PATCH but the entity carries another tariff's tag
    (the tariff changed), ONE best-effort tag PATCH is sent (_retag_premium) —
    a retry of an applied job sees the tag already set and sends nothing."""
    current = _parse_expire(ent.get("expireAt"))
    if current < target and target <= _utcnow():
        logger.warning(
            "PROVISIONING_PREMIUM_PERIOD_ENDED: tg=%s panel=%s < target=%s <= now — no PATCH",
            tg, current.isoformat(), target.isoformat(),
        )
        return True
    if current >= target:
        if _status(ent) == "DISABLED":
            # P1-5: no PATCH (status=ACTIVE would re-enable a user an admin may
            # have disabled on purpose); _verify_delivery turns it into PremiumDisabled.
            logger.warning(
                "PROVISIONING_PREMIUM_DISABLED: tg=%s panel=%s >= target=%s, status DISABLED — not enabled",
                tg, current.isoformat(), target.isoformat(),
            )
        elif tag and ent.get("tag") and ent.get("tag") != tag:
            # The entity carries ANOTHER tariff's tag → the tariff changed. An
            # untagged (legacy) entity gets its tag with the next PATCH instead:
            # no extra request per user (the backfill tags the rest).
            await _retag_premium(tg, ent, tag, tier)
        logger.info(
            "PROVISIONING_PREMIUM_NOOP: tg=%s panel=%s >= target=%s",
            tg, current.isoformat(), target.isoformat(),
        )
        return False
    ref: Any = _entity_id(ent)
    if ref is None:
        ref = _entity_uuid(ent)
    if ref is None:
        raise ProvisioningTransient("premium entity has no id/uuid")
    fields: Dict[str, Any] = {"expireAt": remnawave_premium._iso_z(target), "status": "ACTIVE"}
    ext_squad = getattr(config, "REMNAWAVE_PREMIUM_EXTERNAL_SQUAD_UUID", None) or None
    if ext_squad:
        fields["externalSquadUuid"] = ext_squad
    if tier:
        fields["hwidDeviceLimit"] = remnawave_premium._device_limit_for(tier)
    if tag and ent.get("tag") != tag:
        fields["tag"] = tag
    result = await _panel(remnawave_api.update_user(ref, **fields), "premium PATCH")
    if result is None:
        raise ProvisioningTransient("premium PATCH not applied")
    _invalidate_aggregator(tg)
    logger.info(
        "PROVISIONING_PREMIUM_EXTENDED: tg=%s %s → %s",
        tg, current.isoformat(), target.isoformat(),
    )
    return False


async def _retag_premium(tg: int, ent: Dict[str, Any], tag: str, tier: Optional[str]) -> None:
    """The tariff changed but the premium date needs no PATCH: one tag PATCH
    (+ the tariff's device cap). Best effort — a failure is logged and the job
    goes on: a tag never blocks or retries a delivery."""
    ref: Any = _entity_id(ent)
    if ref is None:
        ref = _entity_uuid(ent)
    if ref is None:
        return
    fields: Dict[str, Any] = {"tag": tag}
    if tier:
        fields["hwidDeviceLimit"] = remnawave_premium._device_limit_for(tier)
    try:
        result = await remnawave_api.update_user(ref, **fields)
    except Exception as e:  # noqa: BLE001 — best effort
        logger.warning("PROVISIONING_PREMIUM_RETAG_FAILED: tg=%s tag=%s %s: %s", tg, tag, type(e).__name__, e)
        return
    if result is None:
        logger.warning("PROVISIONING_PREMIUM_RETAG_FAILED: tg=%s tag=%s (PATCH not applied)", tg, tag)
        return
    logger.info("PROVISIONING_PREMIUM_RETAGGED: tg=%s %s → %s", tg, ent.get("tag"), tag)


async def _persist_premium_cache(
    tg: int, *, uuid: Optional[str], url: Optional[str], short: Optional[str],
    panel_id: Optional[int], force: bool,
) -> None:
    """Same columns/helpers as purchase_flow.provision_subscription. On the
    present path only written when the cache disagrees with the panel."""
    if not force:
        cached_uuid = await _db(database.get_remnawave_premium_uuid(tg), "get_remnawave_premium_uuid")
        cached_id = await _db(database.get_remnawave_premium_id(tg), "get_remnawave_premium_id")
        uuid_ok = not uuid or str(cached_uuid or "") == uuid
        id_ok = panel_id is None or cached_id == panel_id
        if uuid_ok and id_ok:
            return
    if uuid:
        await _db(
            database.set_remnawave_premium_uuid_and_url(tg, uuid, url, short_uuid=short),
            "set_remnawave_premium_uuid_and_url",
        )
    if panel_id is not None:
        await _db(database.set_remnawave_premium_id(tg, panel_id), "set_remnawave_premium_id")


# ── bypass ─────────────────────────────────────────────────────────────

def _limit(ent: Dict[str, Any]) -> int:
    try:
        return int(ent.get("trafficLimitBytes") or 0)
    except (TypeError, ValueError) as e:
        raise ProvisioningPermanent(f"unparseable trafficLimitBytes {ent.get('trafficLimitBytes')!r}") from e


async def _apply_bypass(job: Dict[str, Any]) -> Optional[str]:
    """Returns the bypass subscription URL for complete_activation."""
    tg = int(job["telegram_id"])
    job_id = int(job["id"])
    add = int(job["bypass_add_bytes"])
    base, target = job.get("bypass_base_bytes"), job.get("bypass_target_bytes")

    if target is None:
        state, ent = await _read_state(remnawave_api.get_bypass_state, tg, "bypass")
        current = _limit(ent) if state == "present" else 0
        row = await _db(provisioning_jobs.save_bypass_plan(job_id, current, current + add), "save_bypass_plan")
        base, target = row["bypass_base_bytes"], row["bypass_target_bytes"]
        logger.info(
            "PROVISIONING_BYPASS_PLAN: job=%s tg=%s base=%s target=%s (+%s)",
            job_id, tg, base, target, add,
        )
    base, target = int(base), int(target)
    # the claimed copy carries the plan: _verify_delivery and the alert text read it
    job["bypass_base_bytes"], job["bypass_target_bytes"] = base, target

    state, ent = await _read_state(remnawave_api.get_bypass_state, tg, "bypass")
    if state == "absent":
        res = await _panel(
            remnawave_bypass.create_bypass_user_entity(
                tg, traffic_limit_bytes=target,
                description=f"Bypass via bot ({job.get('tariff_key')})",
            ),
            "bypass create",
        )
        if not res.ok:
            _raise_for_create(res, "bypass")
        await _persist_bypass_cache(
            tg, uuid=res.panel_uuid, url=res.subscription_url, short=res.short_uuid, panel_id=res.panel_id,
        )
        _invalidate_aggregator(tg)
        logger.info(
            "PROVISIONING_BYPASS_CREATED: job=%s tg=%s recovered=%s limit=%s",
            job_id, tg, res.recovered, target,
        )
        if not res.recovered:
            return res.subscription_url
        # Adoption keeps the entity's old limit → run the CAS on it.
        state, ent = await _read_state(remnawave_api.get_bypass_state, tg, "bypass")
        if state != "present":
            raise ProvisioningTransient("bypass entity not visible after adoption")

    await _cas_bypass(tg, ent, base=base, target=target, job_id=job_id)
    await _persist_bypass_cache(
        tg, uuid=_entity_uuid(ent), url=ent.get("subscriptionUrl"),
        short=ent.get("shortUuid"), panel_id=_entity_id(ent),
    )
    return ent.get("subscriptionUrl")


async def _cas_bypass(tg: int, ent: Dict[str, Any], *, base: int, target: int, job_id: int) -> None:
    current = _limit(ent)
    if current == target:
        logger.info("PROVISIONING_BYPASS_ALREADY_APPLIED: job=%s tg=%s limit=%s", job_id, tg, current)
        return
    if current != base:
        raise ProvisioningPermanent(f"conflict: base={base}, target={target}, current={current}")
    ent_id = _entity_id(ent)
    ref: Any = ent_id if ent_id is not None else _entity_uuid(ent)
    if ref is None:
        raise ProvisioningTransient("bypass entity has no id/uuid")
    # Entity verified as bypass by the state reader → with its own numeric id
    # the premium SAFETY-guard is a false positive (see update_user).
    tag_field = {} if ent.get("tag") == tariffs.PANEL_TAG_BYPASS else {"tag": tariffs.PANEL_TAG_BYPASS}
    result = await _panel(
        remnawave_api.update_user(
            ref, trafficLimitBytes=target, status="ACTIVE", _trust_bypass=ent_id is not None,
            **tag_field,
        ),
        "bypass PATCH",
    )
    if result is None:
        raise ProvisioningTransient("bypass PATCH not applied")
    _invalidate_aggregator(tg)
    logger.info(
        "PROVISIONING_BYPASS_APPLIED: job=%s tg=%s %s → %s",
        job_id, tg, base, target,
    )


async def _persist_bypass_cache(
    tg: int, *, uuid: Optional[str], url: Optional[str], short: Optional[str], panel_id: Optional[int],
) -> None:
    """Same helpers as purchase_flow (set_remnawave_bypass_cache COALESCEs NULLs)."""
    if uuid or url or short:
        await _db(database.set_remnawave_bypass_cache(tg, uuid, url, short), "set_remnawave_bypass_cache")
    if panel_id is not None:
        await _db(database.set_remnawave_id(tg, int(panel_id)), "set_remnawave_id")


# ── delivery verification ──────────────────────────────────────────────

def _iso_s(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _expire_or_none(value: Any) -> Optional[datetime]:
    try:
        return _parse_expire(value)
    except ProvisioningPermanent:
        return None


def _status(ent: Dict[str, Any]) -> str:
    return str(ent.get("status") or "?").upper()


async def _verify_delivery(job: Dict[str, Any]) -> None:
    """Re-read the panel after the steps succeeded. Raises DeliveryMismatch
    when it does not show the job's target, ProvisioningTransient when the
    panel is unavailable (an ordinary retry, no mismatch alert)."""
    tg = int(job["telegram_id"])
    problems = []
    premium_disabled = None
    # F7: the paid period already ended — nothing to extend, nothing to verify.
    if job.get("premium_until") is not None and job.get(PERIOD_ENDED_KEY) is None:
        expected = _ceil_second(job["premium_until"])
        state, ent = await _read_state(remnawave_api.get_premium_state, tg, "premium verify")
        want = f"premium: expected expireAt >= {_iso_s(expected)} ACTIVE"
        if state != "present":
            problems.append(f"{want}, panel: entity absent")
        else:
            actual = _expire_or_none(ent.get("expireAt"))
            if actual is not None and actual >= expected and _status(ent) == "DISABLED":
                premium_disabled = (
                    f"{PREMIUM_DISABLED_TAG}: premium expireAt={_iso_s(actual)} covers the paid "
                    f"{_iso_s(expected)}, but the entity is DISABLED in the panel (not enabled automatically)"
                )
            elif actual is None or actual < expected or _status(ent) != "ACTIVE":
                shown = _iso_s(actual) if actual is not None else repr(ent.get("expireAt"))
                problems.append(f"{want}, panel expireAt={shown} status={_status(ent)}")
    if int(job.get("bypass_add_bytes") or 0) > 0 and job.get("bypass_target_bytes") is not None:
        target = int(job["bypass_target_bytes"])
        state, ent = await _read_state(remnawave_api.get_bypass_state, tg, "bypass verify")
        want = f"bypass: expected trafficLimitBytes={target} ({_fmt_gb(target)} GB) ACTIVE"
        if state != "present":
            problems.append(f"{want}, panel: entity absent")
        else:
            try:
                limit: Optional[int] = int(ent.get("trafficLimitBytes") or 0)
            except (TypeError, ValueError):
                limit = None
            if limit != target or _status(ent) != "ACTIVE":
                problems.append(
                    f"{want}, panel trafficLimitBytes={limit} ({_fmt_gb(limit)} GB) status={_status(ent)}"
                )
    if premium_disabled is not None:
        detail = "; ".join([premium_disabled, *problems])
        logger.error(
            "PROVISIONING_PREMIUM_DISABLED_DEAD: job=%s key=%s tg=%s %s",
            job.get("id"), job.get("idempotency_key"), tg, detail,
        )
        raise PremiumDisabled(detail)
    if problems:
        logger.error(
            "PROVISIONING_DELIVERY_MISMATCH: job=%s key=%s tg=%s %s",
            job.get("id"), job.get("idempotency_key"), tg, "; ".join(problems),
        )
        raise DeliveryMismatch(f"{MISMATCH_TAG}: " + "; ".join(problems))
    logger.info("PROVISIONING_VERIFIED: job=%s tg=%s", job.get("id"), tg)


# ── failure recording / alerts ─────────────────────────────────────────

def _fmt_gb(n: Optional[int]) -> str:
    if n is None:
        return "?"
    return f"{n / GIB:.2f}".rstrip("0").rstrip(".")


async def _record_failure(job: Dict[str, Any], err: BaseException, *, permanent: bool, bot) -> None:
    """mark_retry / mark_dead + payment_errors + admin alert. Never raises."""
    try:
        now = _utcnow()
        job_id = int(job["id"])
        attempts = int(job.get("attempts") or 1)
        created = job.get("created_at") or now
        err_text = f"{type(err).__name__}: {err}"
        expired = (now - created) >= DEAD_AFTER
        mismatch = isinstance(err, DeliveryMismatch)
        # "first" = the previous failure of this job was not a mismatch
        first_mismatch = mismatch and MISMATCH_TAG not in str(job.get("last_error") or "")
        mismatch_exhausted = mismatch and attempts >= MISMATCH_DEAD_AFTER_ATTEMPTS
        dead = permanent or expired or mismatch_exhausted
        next_at = None if dead else now + timedelta(seconds=backoff_seconds(attempts))
        try:
            if dead:
                await provisioning_jobs.mark_dead(job_id, err_text)
            else:
                await provisioning_jobs.mark_retry(job_id, err_text, next_at)
        except Exception as db_err:
            # The lease expires and the job is reclaimed — nothing is lost.
            logger.error("PROVISIONING_MARK_FAILED: job=%s %s: %s", job_id, type(db_err).__name__, db_err)
        if dead:
            logger.critical(
                "PROVISIONING_DEAD: job=%s key=%s tg=%s attempts=%s reason=%s err=%s",
                job_id, job.get("idempotency_key"), job.get("telegram_id"), attempts,
                "permanent" if permanent else ("expired_24h" if expired else "delivery_mismatch"),
                err_text,
            )
        else:
            logger.error(
                "PROVISIONING_RETRY: job=%s key=%s tg=%s attempts=%s next_at=%s err=%s",
                job_id, job.get("idempotency_key"), job.get("telegram_id"), attempts,
                next_at.isoformat(), err_text,
            )
        await _report(job, err_text, dead=dead, expired=expired and not permanent,
                      next_at=next_at, force=dead or attempts <= 1 or first_mismatch, bot=bot,
                      first_mismatch=first_mismatch and not dead)
    except Exception as e:
        logger.error("PROVISIONING_RECORD_FAILURE_ERROR: job=%s %s: %s", job.get("id"), type(e).__name__, e)


def _alert_text(job: Dict[str, Any], err_text: str, *, dead: bool, expired: bool,
                next_at: Optional[datetime]) -> str:
    job_id = job.get("id")
    ctx = job.get("context") or {}
    until = job.get("premium_until")
    if until is not None:
        days = ctx.get("premium_days")
        premium = f"+{days}d until {until.isoformat()}" if days else f"until {until.isoformat()}"
    else:
        premium = "untouched"
    add = int(job.get("bypass_add_bytes") or 0)
    bypass = f"+{_fmt_gb(add)} GB" if add else "untouched"
    if job.get("bypass_target_bytes") is not None:
        bypass += (f" (plan {_fmt_gb(job.get('bypass_base_bytes'))} → "
                   f"{_fmt_gb(job.get('bypass_target_bytes'))} GB)")
    mismatch = MISMATCH_TAG in err_text
    if dead:
        if expired:
            status = "DEAD (24h without success)"
        elif mismatch and "DeliveryMismatch" in err_text:
            status = (f"DEAD (delivery mismatch persisted {job.get('attempts')} attempts: "
                      "paid, the panel does not show it — manual action)")
        else:
            status = "DEAD (permanent error)"
    else:
        status = f"RETRY at {next_at.strftime('%H:%M:%S UTC')}" if next_at else "RETRY"
    head = f"Provisioning job {status}"
    if PREMIUM_DISABLED_TAG in err_text:
        head = (f"{PREMIUM_DISABLED_TAG}: paid, but the premium entity is DISABLED in the panel — "
                f"enable it manually if the user is not banned (job {status}, no automatic retry; "
                f"purchase {job.get('idempotency_key')}, tg:{job.get('telegram_id')})")
    elif mismatch and not dead:
        head = (f"{MISMATCH_TAG}: paid, but after a successful PATCH the panel does not show it "
                f"— job {status}")
    lines = [
        head,
        f"job_id: {job_id}",
        f"key: {job.get('idempotency_key')}",
        f"user: tg:{job.get('telegram_id')}",
        f"tariff: {job.get('tariff_key')}",
        f"premium: {premium}",
        f"bypass: {bypass}",
        f"attempts: {job.get('attempts')}",
        f"error: {err_text[:500]}",
        "",
        "Manual retry:",
        f"UPDATE provisioning_jobs SET status='pending', next_attempt_at=now() AT TIME ZONE 'UTC' WHERE id={job_id};",
    ]
    if "conflict:" in err_text:
        lines += [
            "",
            "Conflict: the bypass limit was changed outside this job. Only after checking the GB "
            "were NOT credited, re-plan from the current limit:",
            f"UPDATE provisioning_jobs SET status='pending', bypass_base_bytes=NULL, "
            f"bypass_target_bytes=NULL, next_attempt_at=now() AT TIME ZONE 'UTC' WHERE id={job_id};",
        ]
    return "\n".join(lines)


def _resolve_bot(bot):
    if bot is not None:
        return bot
    try:
        from app.api import payment_webhook
        return getattr(payment_webhook, "_bot", None)
    except Exception:
        return None


async def _report(job: Dict[str, Any], err_text: str, *, dead: bool, expired: bool,
                  next_at: Optional[datetime], force: bool, bot, first_mismatch: bool = False) -> None:
    try:
        await database.log_payment_error(
            stage=LOG_STAGE,
            telegram_id=job.get("telegram_id"),
            purchase_id=job.get("idempotency_key"),
            error_code="dead" if dead else "retry",
            error_message=err_text,
            raw_payload={
                "job_id": job.get("id"),
                "source": job.get("source"),
                "tariff_key": job.get("tariff_key"),
                "attempts": job.get("attempts"),
                "premium_until": job.get("premium_until"),
                "bypass_add_bytes": job.get("bypass_add_bytes"),
                "bypass_base_bytes": job.get("bypass_base_bytes"),
                "bypass_target_bytes": job.get("bypass_target_bytes"),
            },
        )
    except Exception as e:
        logger.warning("PROVISIONING_PAYMENT_ERROR_LOG_FAILED: job=%s %s", job.get("id"), e)

    kind = _alert_kind(err_text, dead=dead, force=force, first_mismatch=first_mismatch)
    target_bot = _resolve_bot(bot)
    if kind is None:
        # attempt >= 2 of a retrying job: best-effort, on the category cooldown
        if target_bot is None:
            logger.warning("PROVISIONING_ALERT_NO_BOT: job=%s (logged only)", job.get("id"))
            return
        try:
            await admin_alerts.send_alert(
                target_bot, ALERT_CATEGORY,
                _alert_text(job, err_text, dead=dead, expired=expired, next_at=next_at),
                force=False,
            )
        except Exception as e:
            logger.warning("PROVISIONING_ALERT_FAILED: job=%s %s", job.get("id"), e)
        return

    # first failure / dead / conflict: forced per-job alert within the budget,
    # otherwise into the digest (see "alert aggregation" below)
    try:
        if target_bot is None or not _take_immediate_slot(kind):
            _buffer(kind, job, err_text)
            if target_bot is None:
                logger.warning(
                    "PROVISIONING_ALERT_NO_BOT: job=%s kind=%s (buffered for the worker digest)",
                    job.get("id"), kind,
                )
            return
    except Exception as e:
        logger.warning("PROVISIONING_ALERT_BUFFER_FAILED: job=%s %s", job.get("id"), e)
        return
    sent = False
    try:
        sent = bool(await admin_alerts.send_alert(
            target_bot, ALERT_CATEGORY,
            _alert_text(job, err_text, dead=dead, expired=expired, next_at=next_at),
            force=True,
        ))
    except Exception as e:
        logger.warning("PROVISIONING_ALERT_FAILED: job=%s %s", job.get("id"), e)
    if not sent:
        # a lost per-job alert is not dropped: it goes into the next digest
        try:
            _buffer(kind, job, err_text)
        except Exception as e:
            logger.warning("PROVISIONING_ALERT_BUFFER_FAILED: job=%s %s", job.get("id"), e)


# ── alert aggregation (per-window budget + digest) ─────────────────────
#
# A mass event (admin bonus to thousands of users while the panel is down, a
# panel outage at peak) must not produce one forced Telegram message per job,
# yet every failed transaction has to reach the admin (docs/audit/SCOPE.md).
# Per kind — "conflict", "dead", "mismatch", "first_failure":
#   - up to ALERT_IMMEDIATE_PER_WINDOW per-job alerts in a rolling
#     ALERT_WINDOW_S go out immediately (forced), exactly as before;
#   - beyond that, or while a digest of that kind is pending, the job is
#     buffered; flush_alert_digests() — called by the provisioning worker
#     every tick and on shutdown — sends ONE digest per ALERT_WINDOW_S per kind
#     (exact count, time range, top reasons, sample jobs, retry SQL);
#   - a failed send (per-job or digest) keeps the job in the buffer and the
#     next tick retries; memory is capped at ALERT_DIGEST_KEEP entries per
#     kind while the counts stay exact.
# payment_errors still gets one row per failure — the DB is the full record.
# Attempt >= 2 retry alerts keep the plain send_alert cooldown path.
# Process-local state (single-process bot, no extra task). Never raises.

ALERT_WINDOW_S = 300.0
ALERT_IMMEDIATE_PER_WINDOW = 5
ALERT_DIGEST_KEEP = 500
ALERT_DIGEST_SAMPLE = 20
ALERT_DIGEST_TOP_REASONS = 5
ALERT_DIGEST_MAX_CHARS = 3800        # + the send_alert header, below Telegram's 4096
ALERT_DIGEST_ID_SQL_MAX = 150        # more jobs → retry SQL by time range instead of ids
ALERT_KINDS = ("conflict", "dead", "mismatch", "first_failure",
               "webhook", "legacy_delivery", "period_ended")   # flush order: money/GB at risk first
# Money-path alerts that are not provisioning jobs share the same budget + digest
# (report_payment_alert): "webhook" — non-200 / anomalous payment_webhook
# outcomes (P1-1); "legacy_delivery" — verify_delivery legacy DELIVERY_MISMATCH
# beyond its budget (P2-12). Their digests carry no provisioning_jobs SQL.
NON_JOB_KINDS = ("webhook", "legacy_delivery", "period_ended")
# "period_ended" — PREMIUM_PERIOD_ENDED (job done after the paid period ended):
# no retry SQL, a manual compensation decision.
_REASON_KEYS_MAX = 50
_REASON_MAX_CHARS = 120
_OTHER_REASONS = "(other reasons)"

_KIND_TITLE = {
    "conflict": "CONFLICT (bypass limit changed outside the job, GB at risk, manual check)",
    "dead": "DEAD (paid days/GB not delivered, manual action)",
    "mismatch": (f"{MISMATCH_TAG} (after a successful PATCH the panel does not show the paid "
                 "days/GB; retrying automatically with backoff)"),
    "first_failure": "FIRST FAILURE (retrying automatically with backoff)",
    "webhook": "PAYMENT WEBHOOK FAILURES (5xx → the provider retries; check payment_errors)",
    "legacy_delivery": (f"{MISMATCH_TAG} legacy path (paid/granted, the panel does not show it; "
                        "no automatic retry — manual action)"),
    "period_ended": (f"{PERIOD_ENDED_TAG} (job processed after the paid period ended — premium NOT "
                     "extended; decide on compensation manually)"),
}
_KIND_STATUS = {"conflict": "dead", "dead": "dead", "mismatch": "pending", "first_failure": "pending"}

_clock = time.monotonic              # tests patch it


@dataclass
class _Digest:
    total: int = 0
    entries: deque = field(default_factory=lambda: deque(maxlen=ALERT_DIGEST_KEEP))
    reasons: Counter = field(default_factory=Counter)
    first_at: Optional[datetime] = None
    last_at: Optional[datetime] = None

    @property
    def dropped(self) -> int:
        return self.total - len(self.entries)

    def _count_reason(self, reason: str, n: int = 1) -> None:
        if reason not in self.reasons and len(self.reasons) >= _REASON_KEYS_MAX:
            reason = _OTHER_REASONS
        self.reasons[reason] += n

    def _span(self, first: Optional[datetime], last: Optional[datetime]) -> None:
        if first is not None:
            self.first_at = first if self.first_at is None else min(self.first_at, first)
        if last is not None:
            self.last_at = last if self.last_at is None else max(self.last_at, last)

    def add(self, entry: Dict[str, Any]) -> None:
        self.total += 1
        self.entries.append(entry)
        self._count_reason(entry["reason"])
        self._span(entry["at"], entry["at"])

    def absorb(self, newer: "_Digest") -> None:
        """self is the older buffer; newer entries go after it (the deque keeps the latest)."""
        self.total += newer.total
        self.entries.extend(newer.entries)
        for reason, n in newer.reasons.items():
            self._count_reason(reason, n)
        self._span(newer.first_at, newer.last_at)


_immediate_at: Dict[str, deque] = {}     # kind → monotonic times of per-job alerts in the window
_pending: Dict[str, _Digest] = {}        # kind → buffered jobs waiting for a digest
_last_digest_at: Dict[str, float] = {}   # kind → monotonic time of the last digest sent


def reset_alert_state() -> None:
    """Forget the budget and the buffers (tests)."""
    _immediate_at.clear()
    _pending.clear()
    _last_digest_at.clear()


def pending_alert_counts() -> Dict[str, int]:
    """Buffered jobs per kind, not yet delivered in a digest."""
    return {k: d.total for k, d in _pending.items() if d.total}


def _alert_kind(err_text: str, *, dead: bool, force: bool, first_mismatch: bool = False) -> Optional[str]:
    if dead:
        return "conflict" if "conflict:" in err_text else "dead"
    if first_mismatch:
        return "mismatch"
    return "first_failure" if force else None


def _reason(err_text: str) -> str:
    """Group key: whitespace collapsed, job-specific big numbers (bytes, ids) → N."""
    text = re.sub(r"\d{4,}", "N", " ".join(str(err_text).split()))
    return text[:_REASON_MAX_CHARS]


def _take_immediate_slot(kind: str) -> bool:
    now = _clock()
    sent = _immediate_at.setdefault(kind, deque())
    while sent and now - sent[0] >= ALERT_WINDOW_S:
        sent.popleft()
    if kind in _pending or len(sent) >= ALERT_IMMEDIATE_PER_WINDOW:
        return False
    sent.append(now)
    return True


def _buffer(kind: str, job: Dict[str, Any], err_text: str) -> None:
    digest = _pending.get(kind)
    if digest is None:
        digest = _pending[kind] = _Digest()
    digest.add({
        "job_id": job.get("id"),
        "telegram_id": job.get("telegram_id"),
        "key": job.get("idempotency_key"),
        "reason": _reason(err_text),
        "at": _utcnow(),
    })


async def report_payment_alert(kind: str, text: str, *, reason: str, telegram_id: Optional[int] = None,
                               key: Optional[str] = None, bot=None) -> bool:
    """Forced alert for a money-path event that is not a provisioning job
    (NON_JOB_KINDS), within the per-window budget of `kind`. Over budget, no bot
    or a failed send → buffered for the next digest (flushed by the provisioning
    worker every tick): never dropped. Never raises. True = sent immediately."""
    entry = {"id": None, "telegram_id": telegram_id, "idempotency_key": key}
    try:
        target_bot = _resolve_bot(bot)
        if target_bot is None or not _take_immediate_slot(kind):
            _buffer(kind, entry, reason)
            return False
        sent = False
        try:
            sent = bool(await admin_alerts.send_alert(target_bot, ALERT_CATEGORY, text, force=True))
        except Exception as e:
            logger.warning("PAYMENT_ALERT_FAILED: kind=%s %s", kind, e)
        if not sent:
            _buffer(kind, entry, reason)
        return sent
    except Exception as e:
        logger.warning("PAYMENT_ALERT_REPORT_FAILED: kind=%s %s", kind, e)
        return False


def _restore(kind: str, digest: _Digest) -> None:
    newer = _pending.get(kind)
    if newer is not None:
        digest.absorb(newer)
    _pending[kind] = digest


async def flush_alert_digests(bot=None, *, final: bool = False) -> int:
    """Send the pending digests: at most one per kind per ALERT_WINDOW_S
    (`final` — shutdown — ignores the interval). A failed send keeps the
    buffer for the next call. Never raises (except task cancellation).
    Returns the number of digests sent."""
    sent = 0
    for kind in ALERT_KINDS:
        try:
            if await _flush_kind(kind, bot, final=final):
                sent += 1
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("PROVISIONING_ALERT_DIGEST_ERROR: kind=%s %s: %s", kind, type(e).__name__, e)
    return sent


async def _flush_kind(kind: str, bot, *, final: bool) -> bool:
    digest = _pending.get(kind)
    if digest is None or digest.total == 0:
        return False
    now = _clock()
    last = _last_digest_at.get(kind)
    if not final and last is not None and now - last < ALERT_WINDOW_S:
        return False
    target_bot = _resolve_bot(bot)
    if target_bot is None:
        logger.warning("PROVISIONING_ALERT_DIGEST_NO_BOT: kind=%s pending=%s", kind, digest.total)
        return False
    text = _digest_text(kind, digest)
    del _pending[kind]              # failures arriving during the send start a new buffer
    ok = False
    try:
        ok = bool(await admin_alerts.send_alert(target_bot, ALERT_CATEGORY, text, force=True))
    except asyncio.CancelledError:
        _restore(kind, digest)
        raise
    except Exception as e:
        logger.warning("PROVISIONING_ALERT_DIGEST_SEND_ERROR: kind=%s %s: %s", kind, type(e).__name__, e)
    if not ok:
        _restore(kind, digest)
        logger.error(
            "PROVISIONING_ALERT_DIGEST_FAILED: kind=%s pending=%s (kept, retried next tick)",
            kind, _pending[kind].total,
        )
        return False
    _last_digest_at[kind] = now
    logger.warning("PROVISIONING_ALERT_DIGEST_SENT: kind=%s count=%s", kind, digest.total)
    return True


def _db_ts(dt: datetime) -> str:
    """Naive-UTC literal as the provisioning_jobs TIMESTAMP columns hold it."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _digest_text(kind: str, digest: _Digest) -> str:
    """Fit ALERT_DIGEST_MAX_CHARS by shrinking the job sample, then by
    switching the retry SQL from ids to a time range. Counts stay exact."""
    all_ids = digest.dropped == 0 and digest.total <= ALERT_DIGEST_ID_SQL_MAX
    text = ""
    for by_ids in ((True, False) if all_ids else (False,)):
        for sample in (ALERT_DIGEST_SAMPLE, 10, 5, 0):
            text = _build_digest(kind, digest, sample=sample, by_ids=by_ids)
            if len(text) <= ALERT_DIGEST_MAX_CHARS:
                return text
    return text[:ALERT_DIGEST_MAX_CHARS - 1] + "…"


def _build_digest(kind: str, d: _Digest, *, sample: int, by_ids: bool) -> str:
    first = d.first_at or _utcnow()
    last = d.last_at or first
    lo = _db_ts(first - timedelta(minutes=1))
    hi = _db_ts(last + timedelta(minutes=1))
    job_kind = kind not in NON_JOB_KINDS
    lines = [
        f"{'Provisioning' if job_kind else 'Payment alerts'} DIGEST: {_KIND_TITLE[kind]}",
        f"count: {d.total} {'job(s)' if job_kind else 'event(s)'} not alerted one by one "
        f"(over {ALERT_IMMEDIATE_PER_WINDOW} per {int(ALERT_WINDOW_S // 60)} min)",
        f"time: {_db_ts(first)} .. {_db_ts(last)} UTC",
        "top reasons:",
    ]
    top = d.reasons.most_common(ALERT_DIGEST_TOP_REASONS)
    lines += [f"  {n} × {reason}" for reason, n in top]
    rest = sum(d.reasons.values()) - sum(n for _, n in top)
    if rest:
        lines.append(f"  {rest} × {len(d.reasons) - len(top)} other reason(s)")

    shown = list(d.entries)[:sample]
    if shown:
        lines.append(f"{'jobs' if job_kind else 'events'} ({len(shown)} of {d.total}):")
        if job_kind:
            lines += [f"  job {e['job_id']} tg:{e['telegram_id']} key:{str(e['key'])[:64]}" for e in shown]
        else:
            lines += [f"  tg:{e['telegram_id']} {str(e['key'])[:64]}" for e in shown]
    if d.dropped:
        lines.append(f"  (only the latest {len(d.entries)} kept in memory; all {d.total} are in payment_errors)")
    elif len(shown) < d.total:
        lines.append(f"  … and {d.total - len(shown)} more")
    if not job_kind:
        lines += ["", "Details of every event: bot logs (and payment_errors for webhook failures)."]
        return "\n".join(lines)

    status = _KIND_STATUS[kind]
    if by_ids:
        ids = ",".join(str(e["job_id"]) for e in d.entries)
        where = f"status IN ('{status}') AND id = ANY(ARRAY[{ids}])"
        lines += ["", f"Manual retry (all {d.total}):"]
    else:
        if kind == "first_failure":
            where = f"status IN ('pending') AND updated_at >= '{lo}'"
        elif kind == "mismatch":
            where = f"status IN ('pending') AND updated_at >= '{lo}' AND last_error LIKE '%{MISMATCH_TAG}%'"
        else:
            like = "LIKE" if kind == "conflict" else "NOT LIKE"
            where = (f"status IN ('dead') AND updated_at BETWEEN '{lo}' AND '{hi}' "
                     f"AND last_error {like} '%conflict:%'")
        lines += ["", "Manual retry by time range (check SELECT count(*) with the same WHERE first):"]
    lines.append(
        f"UPDATE provisioning_jobs SET status='pending', next_attempt_at=now() AT TIME ZONE 'UTC' "
        f"WHERE {where};"
    )
    if kind in ("first_failure", "mismatch"):
        lines.append("(they retry by themselves with backoff; this only makes them due now)")
    if kind == "conflict":
        lines += [
            "",
            "Conflict: a plain retry conflicts again. Only after checking per user that the GB "
            "were NOT credited, re-plan from the current limit: add bypass_base_bytes=NULL, "
            "bypass_target_bytes=NULL to the SET above.",
        ]
    lines += [
        "",
        f"Every failure is in payment_errors (stage='{LOG_STAGE}', created_at BETWEEN "
        f"'{lo}+00' AND '{hi}+00') and on the dashboard payments page; "
        "per job: provisioning_jobs.last_error.",
    ]
    return "\n".join(lines)


__all__ = [
    "DeliveryMismatch",
    "MISMATCH_TAG",
    "PREMIUM_DISABLED_TAG",
    "PremiumDisabled",
    "ProvisioningPermanent",
    "ProvisioningTransient",
    "apply",
    "backoff_seconds",
    "enqueue",
    "flush_alert_digests",
    "pending_alert_counts",
    "process_claimed",
    "reset_alert_state",
    "run_now",
    "user_lock",
]
