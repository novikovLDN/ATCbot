# Production Hardening — Step 6: Deadlock & Consistency Audit

**Date:** 2026-02-12  
**Scope:** Static analysis for concurrency, deadlocks, idempotency, lifecycle consistency  
**Type:** Verification pass — no logic changes unless real issue found

---

## Executive Summary

| Verdict | **SAFE FOR PRODUCTION** |
|---------|-------------------------|
| Blocking issues | None |
| Minor considerations | 1 (see Phase G) |

---

## PHASE A — Semaphore Safety

| Check | Status | Evidence |
|-------|--------|----------|
| ConcurrencyLimiterMiddleware does not wrap background tasks | ✅ | Registered on `dp.update.middleware()` only. Background tasks (reminders, healthcheck, crypto_watcher, etc.) run via `asyncio.create_task` and never pass through `dp.update`. |
| Semaphore applied only to `dp.update` | ✅ | Single registration: `dp.update.middleware(ConcurrencyLimiterMiddleware(update_semaphore))`. |
| No nested semaphore usage | ✅ | Single `async with self._semaphore:` per update; no inner acquire. |
| No blocking await in critical section except handler execution | ✅ | Critical section: `async with self._semaphore:` → `await handler(event, data)` → done. No extra awaits. |

**Confirmed safe areas:** ConcurrencyLimiterMiddleware, semaphore scope, critical section.

---

## PHASE B — Database Deadlock Review

| Check | Status | Evidence |
|-------|--------|----------|
| No nested `pool.acquire()` in activation_service | ✅ | Uses `conn` when passed; otherwise single `async with pool.acquire()`. No nesting. |
| No nested `pool.acquire()` in finalize_purchase | ✅ | Single `async with pool.acquire() as conn:` and `async with conn.transaction()`. Calls `grant_access(conn=conn)` — passes conn, no second acquire. |
| No nested `pool.acquire()` in grant_access | ✅ | When `conn` is None, acquires once with `pool.acquire()` and releases in `finally`. When `conn` is passed, uses it directly. |
| No nested `pool.acquire()` in broadcast | ✅ | Uses `database.get_eligible_no_subscription_broadcast_users()` (internal pool usage); broadcast handlers do not nest acquires. |
| No transaction nesting across different connections | ✅ | Single connection per operation; `conn.transaction()` used consistently. |
| No connection acquired inside another `async with pool.acquire()` | ✅ | `grant_access(conn=conn)` receives connection; all callers pass conn when in transaction. |

**Confirmed safe areas:** Pool usage, transaction boundaries, no connection nesting.

---

## PHASE C — Lock Ordering

| Check | Status | Evidence |
|-------|--------|----------|
| `_config_file_lock` only around file writes | ✅ | `xray_api/main.py`: lock used only around `await asyncio.to_thread(_save_xray_config_file, ...)`. |
| No await inside lock except `asyncio.to_thread` | ✅ | Only `await asyncio.to_thread(...)` inside lock; no other awaits. |
| No lock acquisition inside DB transaction blocks | ✅ | Config file lock and DB transactions are separate; no lock held during DB work. |

**Confirmed safe areas:** Config lock usage, no lock/DB mixing.

---

## PHASE D — Idempotency

| Check | Status | Evidence |
|-------|--------|----------|
| Payment idempotency guard before finalize logic | ✅ | `finalize_purchase`: early `if status != "pending"` (line ~6156) and `UPDATE ... WHERE status='pending'`. Guard before any mutations. |
| Crypto webhook returns 200 even on logic error | ✅ | `cryptobot_service.handle_webhook`: all branches return HTTP 200 (success, already_processed, invalid, error). |
| Retry loops do not double-apply DB changes | ✅ | `crypto_payment_watcher` retries `finalize_purchase`; idempotency enforced inside `finalize_purchase`. `init_db` retry: `DB_READY` guard prevents re-running migrations. |

**Confirmed safe areas:** Idempotency guards, webhook response codes, retry safety.

---

## PHASE E — Memory Pressure

| Check | Status | Evidence |
|-------|--------|----------|
| No unbounded dicts or caches | ✅ | No global unbounded caches found. |
| Promo sessions have TTL eviction | ✅ | `get_promo_session` checks `expires_at` and clears expired entries. |
| No global list grows unbounded | ✅ | No unbounded list accumulation. |
| `background_tasks` not appended repeatedly on recovery | ✅ | Recovery uses `recovered_tasks` set; each task type added at most once. List is bounded by fixed task set. |

**Confirmed safe areas:** Memory usage patterns, TTL eviction, bounded collections.

---

## PHASE F — Middleware Order

| Check | Status | Evidence |
|-------|--------|----------|
| Order: ConcurrencyLimiter → TelegramErrorBoundary → Routers | ✅ | `main.py` lines 101–105: `dp.update.middleware(ConcurrencyLimiterMiddleware(...))` then `dp.update.middleware(TelegramErrorBoundaryMiddleware())` then `dp.include_router(root_router)`. |
| No duplicate registration | ✅ | Each middleware registered once. |
| No middleware recursion | ✅ | Middlewares are linear; no self-calling. |

**Confirmed safe areas:** Middleware order, single registration, no recursion.

---

## PHASE G — Shutdown Consistency

| Check | Status | Evidence |
|-------|--------|----------|
| All tasks awaited | ✅ | Shutdown: cancel all `background_tasks`, then `await task` for each. |
| No task restarts itself during shutdown | ✅ | Workers exit on `CancelledError` (break or re-raise); no restart logic. |
| No background loop swallows CancelledError | ✅ | Workers handle `CancelledError`: `break` (reminders, activation_worker, trial_notifications, crypto_watcher, healthcheck, auto_renewal) or `raise` (fast_expiry_cleanup, broadcast_service). TelegramErrorBoundaryMiddleware re-raises. |

**Minor consideration:** `broadcast.py` `_run_broadcast` (fire-and-forget via `asyncio.create_task`) catches `CancelledError` and does not re-raise. It is not in `background_tasks`, so it is never cancelled during shutdown. Fire-and-forget tasks may still run during process exit. Not a blocking issue.

**Confirmed safe areas:** Shutdown flow, task cancellation, CancelledError handling in tracked workers.

---

## Potential Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| Fire-and-forget broadcast task not tracked at shutdown | Low | Task completes or errors; process exit will terminate it. Consider tracking long-running broadcasts if needed. |

---

## Final Verdict

### ✅ **SAFE FOR PRODUCTION**

- No deadlock patterns (single acquire per operation, no nesting).
- Semaphore scoped correctly to Telegram updates only.
- Payment and webhook idempotency in place.
- Shutdown cancels and awaits all tracked tasks; workers handle `CancelledError` correctly.
- Memory usage is bounded; promo sessions use TTL eviction.
- Middleware order and registration are correct.

---

## Files Audited

- `app/core/concurrency_middleware.py`
- `app/core/telegram_error_middleware.py`
- `main.py`
- `database.py` (finalize_purchase, grant_access, pool usage)
- `app/services/activation/service.py`
- `xray_api/main.py`
- `cryptobot_service.py`
- `app/handlers/admin/broadcast.py`
- `app/handlers/common/utils.py` (promo session)
- `broadcast_service.py`
- `reminders.py`, `trial_notifications.py`, `activation_worker.py`, `crypto_payment_watcher.py`
- `fast_expiry_cleanup.py`, `auto_renewal.py`, `healthcheck.py`, `health_server.py`
