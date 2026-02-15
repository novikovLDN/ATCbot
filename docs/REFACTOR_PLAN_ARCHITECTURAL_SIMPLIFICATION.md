# Senior-Level Full Refactor Plan — ATCS Telegram Bot

**Goal:** Production-safe architectural simplification without breaking business logic.  
**Principle:** Simplify layers, remove redundancy, preserve payments, subscriptions, UUID, VPN, renewals, financial integrity, idempotency, notifications.

---

## PHASE 1 — Structural Audit (Current State)

### 1.1 Project Tree (Core Components)

| Layer | Files / Modules |
|-------|------------------|
| **Entry** | `main.py` |
| **Workers** | `activation_worker.py`, `auto_renewal.py`, `fast_expiry_cleanup.py`, `crypto_payment_watcher.py`, `trial_notifications.py`, `reminders.py`, `reconcile_xray_state.py`, `xray_sync.py` |
| **Health** | `healthcheck.py`, `health_server.py` |
| **DB** | `database.py`, `migrations.py` |
| **VPN/HTTP** | `vpn_utils.py`, `app/services/vpn_client.py` |
| **Payments** | `app/services/payments/service.py`, `payments/cryptobot.py`, `cryptobot_service.py`, `app/api/payment_webhook.py` |
| **Core** | `app/core/watchdog_heartbeats.py`, `app/core/pool_monitor.py`, `app/core/circuit_breaker.py`, `app/core/cooperative_yield.py`, `app/core/recovery_cooldown.py`, `app/utils/retry.py` |
| **Handlers** | `app/handlers/*` (routers, FSM, admin, payments, user) |

### 1.2 Background Tasks (main.py)

| Task | Source | Purpose |
|------|--------|---------|
| reminder_task | reminders.reminders_task | Subscription reminders |
| trial_notifications_task | trial_notifications.run_trial_scheduler | Trial expiry notifications |
| healthcheck_task | healthcheck.health_check_task | Periodic health check |
| health_server_task | health_server.health_server_task | HTTP health endpoint |
| db_retry_task | retry_db_init (internal) | DB reconnection |
| fast_cleanup_task | fast_expiry_cleanup.fast_expiry_cleanup_task | Expired subscription cleanup |
| auto_renewal_task | auto_renewal.auto_renewal_task | Auto-renew subscriptions |
| activation_worker_task | activation_worker.activation_worker_task | Pending activation queue |
| xray_sync_task | xray_sync.start | Xray sync (optional) |
| reconciliation_task | reconcile_xray_state.reconcile_xray_state_task | Orphan UUID cleanup (optional) |
| crypto_watcher_task | crypto_payment_watcher.crypto_payment_watcher_task | Crypto payment polling |
| heartbeat_task | event_loop_heartbeat | Event loop tick (watchdog signal) |
| watchdog_task | polling_watchdog | Multi-signal freeze detection |
| network_watchdog_task | telegram_network_watchdog | Telegram HTTP liveness → os._exit(1) |
| polling_task | start_polling | dp.start_polling |
| freeze_audit_loop_monitor / freeze_audit_pool_tasks | (when FREEZE_AUDIT_INSTRUMENTATION=1) | Diagnostic only |

### 1.3 Locks

| Lock | Location | Scope |
|------|----------|--------|
| PostgreSQL advisory lock | main.py | Single-instance (process lifetime) |
| _worker_lock (asyncio.Lock) | reconcile_xray_state.py | One reconciliation at a time |
| _config_file_lock (asyncio.Lock) | xray_api/main.py | Config file read/write |
| self._lock (asyncio.Lock) | xray_api/main.py | Xray state |
| threading.Lock | app/core/circuit_breaker.py | Circuit breaker state (no await inside) |

### 1.4 Retry Mechanisms

| Where | Mechanism | Scope |
|-------|------------|--------|
| app/utils/retry.py | retry_async | Generic (DB, HTTP) |
| vpn_utils add_vless_user | retry_async(..., retries=2) | VPN add |
| vpn_utils remove_vless_user | retry_async(..., retries=2) | VPN remove |
| database get_pool | retry_async on create_pool | Pool creation |
| database init_db / pool acquire | retry_async in places | Transient errors |

No nested retry (retry inside retry) identified in hot paths.

### 1.5 Circuit Breakers

| Component | Location | Used By |
|------------|----------|---------|
| get_circuit_breaker("vpn_api") | app/core/circuit_breaker.py | vpn_utils add_vless_user, update_vless_user, remove_vless_user |
| list_vless_users | — | **Not** using circuit breaker |
| reconcile_xray_state | Own breaker (failure count + open_until) | reconcile_xray_state_task |

### 1.6 Watchdogs / Liveness

| Component | Trigger | Action |
|-----------|---------|--------|
| event_loop_heartbeat | Every 5s | Sets last_event_loop_heartbeat |
| polling_watchdog | Every 30s | If are_all_stale() → SystemExit(1) |
| telegram_network_watchdog | Every 10s | If no Telegram HTTP success for TELEGRAM_LIVENESS_TIMEOUT → os._exit(1) |
| watchdog_heartbeats | mark_event_loop_heartbeat, mark_worker_iteration, mark_healthcheck_success | Used by polling_watchdog |

### 1.7 DB Acquire Wrappers

| Wrapper | Location | Purpose |
|---------|----------|---------|
| acquire_connection(pool, label) | app/core/pool_monitor.py | Optional timed acquire (POOL_MONITOR_ENABLED); else pool.acquire() |
| pool.acquire() | Many call sites | Direct use |

No DB connection held during HTTP in reconcile (verified in AUDIT_RECONCILE_VPN_POOL_FREEZE.md). Some workers (e.g. auto_renewal Phase B, trial_notifications) may do short acquire for notification after commit.

### 1.8 HTTP Clients

| Usage | Pattern | Timeout |
|-------|--------|--------|
| vpn_utils | Per-call `async with httpx.AsyncClient(timeout=HTTP_TIMEOUT)` | Float (3–5s) |
| cryptobot_service / payments/cryptobot | Per-call AsyncClient(timeout=10 or 30) | Float |
| xray_api/main.py | Per-call AsyncClient(timeout=10) | Float |

No shared long-lived httpx client. No explicit httpx.Timeout(connect=, read=, write=, pool=).

### 1.9 Redundant / Overlapping Layers

- **Three liveness mechanisms:** event_loop_heartbeat, telegram_network_watchdog, multi-signal polling_watchdog. Two can be sufficient (e.g. Telegram HTTP + infra restart).
- **Reconcile:** Has its own circuit breaker (failure count + open_until) plus asyncio.Lock; list_vless_users has no circuit breaker and no retry.
- **Pool monitor:** Optional acquire_connection wrapper; when disabled, identical to pool.acquire(). Useful for diagnostics only.
- **FREEZE_AUDIT_INSTRUMENTATION:** Temporary; can be removed after root cause is fixed.

---

## PHASE 2 — Target Clean Architecture

### A. Polling Layer (Target)

- **Single** `await dp.start_polling(bot, ...)` — no `while True` restart loop.
- **One** process-level liveness: e.g. Telegram HTTP watchdog only (no event-loop tick, no multi-signal). If no successful Telegram API response for N seconds → `os._exit(1)`; Railway restarts.
- No in-process polling restart; no task.cancel("polling_task").
- Clean shutdown: cancel background tasks, release advisory lock, close pool, close bot session.

### B. Workers (Target)

Unified pattern:

```python
async def worker_name_task():
    while True:
        try:
            await do_one_iteration()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log_error(e)
        await asyncio.sleep(INTERVAL)
```

- **Keep:** reminders, trial_notifications, healthcheck, health_server, fast_expiry_cleanup, auto_renewal, activation_worker, crypto_payment_watcher.
- **Reconcile:** Keep if product requires orphan UUID cleanup; simplify to single timeout and optional circuit breaker (no nested locks if possible). If product can tolerate manual cleanup, reconcile can be made optional/removed in a later phase.
- **Remove from workers:** warm-up recovery modes (if any), custom “recovery iterations,” and duplicate breaker logic inside worker (use one shared pattern).
- **xray_sync:** Keep if required; otherwise remove.

### C. HTTP Layer (Target)

- **Option A (minimal change):** Keep per-call client; enforce explicit `httpx.Timeout(connect=5, read=10, write=5, pool=5)` everywhere.
- **Option B (shared client):** One shared `httpx.AsyncClient(timeout=Timeout(...))` for VPN (and optionally for Crypto) with clear lifecycle (create at startup, close on shutdown). Reduces connection churn.
- **Single retry layer:** One retry_async per call site; no retry inside retry. Bounded (e.g. 2 retries).
- **Circuit breaker:** One per domain (e.g. vpn_api); apply to list_vless_users as well.

### D. VPN Layer (Target)

- Keep: add_vless_user, remove_vless_user, update_vless_user, list_vless_users (if reconcile kept).
- Keep: strict UUID validation, HTTPS.
- Remove: “excessive isolation wrappers” only if they duplicate the same logic (audit per wrapper). Keep safety that prevents wrong UUID or wrong endpoint.

### E. DB Layer (Target)

- Keep pool, advisory lock, transaction atomicity for financial operations.
- Keep “no DB connection held during HTTP” rule.
- **pool_monitor:** Keep as optional (env-gated); no need to remove. Reduces complexity only if removed and replaced by ad-hoc logging when needed.

### F. Remove or Simplify (Candidates)

| Component | Action | Risk |
|-----------|--------|------|
| event_loop_heartbeat task | Remove if Telegram HTTP watchdog is sole liveness | Low (if Telegram watchdog is reliable) |
| polling_watchdog (multi-signal) | Remove or merge into single “last success” check | Medium (current multi-signal reduces false positives) |
| Reconcile worker | Keep but simplify (timeout, one breaker); or make optional | High if removed (orphans accumulate) |
| FREEZE_AUDIT instrumentation | Remove after freeze root cause fixed | Low |
| Warm-up / recovery modes in workers | Simplify to “log and continue” | Low–medium (per worker) |
| Duplicate cost_model / metrics | Deduplicate only if identical; keep one | Low |

---

## PHASE 3 — Clean Implementation Rules (Invariants)

1. Business logic unchanged: payments, subscriptions, UUID, VPN, renewals, expiry, notifications.
2. No UUID loss, no double activation, no double payment processing, no subscription loss.
3. Financial idempotency preserved (finalize_purchase, grant_access, referral, etc.).
4. No user-visible behavior change.

---

## PHASE 4 — File Cleanup (Proposed)

- **Dead code:** Remove only after grep confirms no references (e.g. legacy handlers, deprecated helpers).
- **Unused files:** e.g. `app/handlers/notifications 2.py`, `app/handlers/__init__ 2.py` — remove if duplicates.
- **Imports:** Clean dead imports after any removal.
- **Duplicate abstractions:** Merge only when two modules do the same thing (with tests).

---

## PHASE 5 — Validation After Refactor

1. **Functional:** Subscription lifecycle, payment flow, UUID lifecycle, renewal, expiry, admin, notifications — trace unchanged.
2. **Concurrency:** No blocking calls, no DB held during HTTP, all HTTP with timeout.
3. **Freeze resilience:** No infinite await, no nested locks that can deadlock, no sync blocking in async path.
4. **Diagram:** Update architecture diagram (e.g. in this doc or separate) to reflect simplified flow.

---

## Removed Components List (Proposed)

- event_loop_heartbeat task (if going to Telegram-only liveness).
- polling_watchdog multi-signal (if replaced by single Telegram HTTP watchdog).
- FREEZE_AUDIT_INSTRUMENTATION block (after production freeze is resolved).
- Duplicate or legacy handler files (e.g. `notifications 2.py`, `__init__ 2.py`) after confirmation.
- Warm-up / recovery iteration logic inside workers where it only adds complexity and no safety.

**Not removed (keep):**

- Advisory lock (single-instance).
- telegram_network_watchdog (or single consolidated watchdog).
- Reconcile (simplified, not dropped).
- Circuit breaker for VPN (and extend to list_vless_users).
- retry_async (single layer).
- Pool and transaction boundaries.

---

## Clean Worker Model (Target)

| Worker | Interval | Pattern | Notes |
|--------|----------|---------|--------|
| reminders_task | 45 min | sleep → do_job → catch log → sleep | No warm-up |
| trial_notifications_task | 300s | same | No warm-up |
| health_check_task | 600s | same | No warm-up |
| health_server_task | Long-lived | HTTP server | Keep |
| fast_expiry_cleanup_task | 60s | same | No conn across HTTP |
| auto_renewal_task | 600s | same | No conn across HTTP (Phase B after commit) |
| activation_worker_task | 300s | same | No conn across HTTP |
| reconcile_xray_state_task | 600s | same + optional breaker | Single lock, one timeout |
| crypto_payment_watcher_task | 30s | same | Warm-up optional |

---

## Clean Polling Model (Target)

- One call: `await dp.start_polling(bot, polling_timeout=30, handle_signals=False, close_bot_session=True)`.
- No `while True` around polling.
- One watchdog: e.g. “no Telegram HTTP success for 180s” → `os._exit(1)`.
- Shutdown: cancel tasks, unlock advisory, close pool, close session.

---

## HTTP Layer Cleanup Plan

1. Introduce explicit `httpx.Timeout(connect=5, read=10, write=5, pool=5)` (or equivalent) for all VPN and payment HTTP calls.
2. Add circuit_breaker + single retry_async to `list_vless_users` (align with add/remove).
3. Optionally: single shared AsyncClient for VPN (create in main, pass or use global), close in finally.
4. Ensure no call is without timeout and no nested retries.

---

## DB Safety Verification

- All financial mutations in single transaction with single conn (already verified in prior audits).
- No pool.acquire or acquire_connection held across HTTP (reconcile and workers already compliant or fixed).
- Advisory lock: acquire at startup, release in finally; use pool.release(instance_lock_conn).

---

## Risk Assessment

| Change | Risk | Mitigation |
|--------|------|------------|
| Remove event_loop_heartbeat | Low | Rely on Telegram HTTP only; ensure timeout < infra restart |
| Remove multi-signal watchdog | Medium | Keep Telegram HTTP watchdog; consider keeping one “all stale” check with higher threshold |
| Simplify reconcile | Medium | Keep grace window + live re-check; remove only redundant inner logic |
| Shared httpx client | Low–medium | Lifecycle clear; no shared state between requests |
| Remove FREEZE_AUDIT | Low | Only when freeze cause fixed and monitored |

---

## Simplified Dependency Graph (Target)

```
main
├── advisory_lock (DB)
├── background_tasks
│   ├── reminder_task
│   ├── trial_notifications_task
│   ├── healthcheck_task
│   ├── health_server_task
│   ├── db_retry_task (if !DB_READY)
│   ├── fast_expiry_cleanup_task
│   ├── auto_renewal_task
│   ├── activation_worker_task
│   ├── xray_sync_task (optional)
│   ├── reconciliation_task (optional)
│   ├── crypto_watcher_task
│   └── telegram_network_watchdog_task  # single watchdog
└── polling_task (start_polling → dp.start_polling)

Handlers → DB pool, VPN (vpn_utils), payments
Workers → DB pool, VPN, notifications (bot)
VPN → httpx (timeout), circuit_breaker, retry_async
```

---

## Step-by-Step Safe Migration Plan

1. **Step 1 (no behavior change):** Add explicit `httpx.Timeout` to VPN and payment clients; add circuit_breaker + retry to list_vless_users. Deploy, validate.
2. **Step 2:** Remove FREEZE_AUDIT_INSTRUMENTATION block (or leave env-gated). Deploy.
3. **Step 3:** Consolidate watchdogs: keep telegram_network_watchdog; remove event_loop_heartbeat and polling_watchdog, or keep polling_watchdog with a single “telegram success” check. Deploy, monitor.
4. **Step 4:** Simplify reconcile worker (single timeout, one breaker; no extra locks). Deploy.
5. **Step 5:** Optional shared httpx client for VPN; optional removal of duplicate handler files and dead code. Deploy.
6. **Step 6:** Full functional + concurrency + freeze-resilience audit; update architecture diagram.

---

## Refactored Folder Structure (Proposed)

No major reorg required. Suggested cleanups:

- Remove `app/handlers/notifications 2.py`, `app/handlers/__init__ 2.py` if confirmed duplicates.
- Keep `app/core/` (watchdog_heartbeats, pool_monitor, circuit_breaker, retry, cooperative_yield) with reduced surface (fewer tasks, single watchdog).
- Keep workers at top level (activation_worker, auto_renewal, etc.) or move under `app/workers/` in a later phase (optional).

---

## Final System Readiness (Post-Refactor)

- **Static analysis:** Run linter and type check after each step.
- **Concurrency:** No blocking in async path; no DB during HTTP; timeouts on all HTTP.
- **Logical consistency:** Subscription/payment/UUID flows unchanged; idempotency preserved.

**Readiness level:** Document and incremental steps above are intended to bring the system to a **simple, predictable, maintainable** state while preserving **production stability** and **business correctness**. Execute steps in order and validate after each deploy.

---

## Static Analysis & Concurrency Checklist (Post-Refactor)

- **Linting:** Run project linter on modified files after each step.
- **Concurrency:** No `time.sleep`, no blocking I/O in async path; no DB connection held during HTTP; all HTTP calls use explicit timeout.
- **Logical consistency:** Subscription lifecycle, payment flow, UUID lifecycle, renewal, expiry, notifications — trace unchanged; idempotency preserved.
- **Final system readiness:** After all steps, system is **SIMPLE**, **PREDICTABLE**, **MAINTAINABLE**; production stability and business logic preserved.
