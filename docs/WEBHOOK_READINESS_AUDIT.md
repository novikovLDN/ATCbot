# Full System Audit — Webhook Readiness

**Mode:** Read-only. No code modified.  
**Goal:** Verify safe migration from Telegram polling to webhook mode.

---

## SECTION 1 — Startup & Entrypoint

### 1.1 How main() initializes

**Order of initialization (traced in main.py):**

1. **Config / logging** — setup_logging(), config/env.
2. **Bot and Dispatcher** — `bot = Bot(token=...)`, `dp = Dispatcher(storage=MemoryStorage())`.
3. **Concurrency / middlewares** — `update_timestamp_middleware`, `ConcurrencyLimiterMiddleware`, `TelegramErrorBoundaryMiddleware`; `dp.include_router(root_router)`.
4. **DB** — `database.init_db()` (sets DB_READY, creates pool).
5. **Advisory lock** — If DB_READY: `pool = get_pool()`, `instance_lock_conn = pool.acquire()`, `pg_advisory_lock(ADVISORY_LOCK_KEY)`. On failure: release conn and raise.
6. **background_tasks list** — Created empty, then tasks appended.
7. **Workers (all created here, not inside polling):**  
   reminders_task, trial_notifications_task, healthcheck_task, health_server_task, db_retry_task (if !DB_READY), fast_cleanup_task, auto_renewal_task, activation_worker_task, xray_sync_task (if enabled), reconciliation_task (if enabled). Each is `asyncio.create_task(...)` and `background_tasks.append(...)`.
8. **Webhook audit block** — `get_webhook_info()`; if webhook url set, `delete_webhook(drop_pending_updates=True)`; if still set after delete, `sys.exit(1)`.
9. **Watchdogs and polling:**  
   `heartbeat_task` (event_loop_heartbeat), `watchdog_task` (multi-signal), `polling_watchdog_task` (polling_heartbeat_watchdog), `polling_task` (start_polling_with_auto_restart). All appended to background_tasks. Then `await polling_task` (main blocks until polling exits).

**DB pool:** Created inside `database.init_db()` (or on first `get_pool()`). Not created in main() directly; main calls `init_db()` then `get_pool()` for advisory lock.

### 1.2 Confirmations

- **Polling isolated to single task:** Yes. One task named `"polling_task"` runs `start_polling_with_auto_restart()`.
- **No other component depends on polling-specific state:** Yes. Workers use only DB_READY, pool, bot instance; they do not reference polling_task or last_polling_activity. Only `polling_heartbeat_watchdog` and `update_timestamp_middleware` use `last_polling_activity` / `last_update_timestamp`; those are transport-layer (update flow), not business state.
- **Workers not created inside polling lifecycle:** Yes. All workers are created before `polling_task` is created and before `await polling_task`.
- **Advisory lock acquired before workers start:** Yes. Advisory lock is acquired immediately after DB init; then `background_tasks = []` and workers are appended. Lock is not tied to polling.
- **Advisory lock not tied to polling lifecycle:** Yes. Lock is acquired at startup and released in `finally` on shutdown; polling_task is just one of the tasks that run after.

### 1.3 Verifications

- **No worker started conditionally inside polling restart loop:** Yes. `start_polling_with_auto_restart()` only creates a new `Bot()` and calls `delete_webhook` and `dp.start_polling()`; it does not create or append any background task.
- **No background task appended on polling restart:** Yes. Only the initial creation block appends to `background_tasks`; the polling loop does not append.
- **No global state depends on polling being active:** Yes. `last_polling_activity` and `last_update_timestamp` are only used by (1) middleware that runs on every update (so under webhook they would still be updated when updates are fed), and (2) polling_heartbeat_watchdog which only cancels the polling task. No financial or business logic reads these.

---

## SECTION 2 — Polling Dependencies

| Item | Location | Transport-only? | Business/financial dependency? | Removing polling breaks workers? |
|------|----------|-----------------|--------------------------------|----------------------------------|
| `dp.start_polling` | main.py ~621 | Yes | No | No (workers are independent). |
| `delete_webhook` | main.py ~553, ~612 | Yes (ensures clean polling) | No | No. |
| Polling watchdog (multi-signal) | main.py ~579 | N/A (watches event loop + worker + health) | No | No. |
| `polling_heartbeat_watchdog` | main.py ~587 | Yes (restarts only polling_task) | No | No. Under webhook there is no polling_task; watchdog would need to be removed or repurposed. |
| `update_timestamp_middleware` | main.py ~151 | Yes (runs per update) | No | No. Same middleware can run when updates are fed via webhook. |
| `last_polling_activity` | main.py | Yes | No | No. Only middleware and polling_heartbeat_watchdog use it. |
| `polling_task` cancellation | main.py ~596 | Yes | No | No. |

**Conclusion:** All polling-related code is transport-layer only. No business or financial logic depends on polling heartbeat or polling lifecycle. Removing polling and feeding updates via webhook does not break worker lifecycle; only the polling_heartbeat_watchdog becomes obsolete (or must be replaced by a “no updates received” alert, not a task cancel).

---

## SECTION 3 — Webhook Compatibility Check

### 3.1 Readiness for webhook

- **aiohttp:** Already used for health server and Crypto Bot webhook (`health_server.py`, `cryptobot_service.register_webhook_route`). Same app can expose a Telegram webhook route.
- **Feed update:** aiogram 3.x supports `dp.feed_webhook_update(bot, update)` or equivalent (e.g. receive JSON body, parse Update, feed to dispatcher). Handlers receive the same `Update` object; no change needed in handler signatures.
- **Handlers do not assume polling:** Handlers use `Message`, `CallbackQuery`, `FSMContext`, etc. They do not reference `get_updates`, polling timeout, or long-polling behavior.
- **No manual get_updates:** Not used anywhere in the codebase.
- **No dependency on polling timeout behavior:** Concurrency is enforced by `ConcurrencyLimiterMiddleware` and semaphore; no logic relies on polling_timeout value.
- **No blocking operations inside handlers:** Handlers are async and use `await` for DB and HTTP; no `time.sleep` or sync blocking in handler paths.

### 3.2 Handlers doing heavy DB + HTTP

- **process_successful_payment** (payments_messages.py): Calls payment_service finalize (DB + possibly VPN). Can take a few seconds. Must complete within Telegram webhook response timeout (60s); typically well under.
- **Balance purchase callback** (payments_callbacks.py): Calls `database.finalize_balance_purchase` (DB + grant_access). Same as above.
- **Crypto Bot webhook** (cryptobot_service): Already a webhook; calls `finalize_purchase` and sends Telegram messages. Runs in same process; no change for Telegram webhook.

**Recommendation:** No handler clearly exceeds 5–10s in normal conditions. For webhook, ensure total request handling (including finalize + send message) stays under 60s. If needed in future, heavy work can be offloaded to a background task and webhook returns 200 quickly after enqueueing.

---

## SECTION 4 — Workers Safety Analysis

| Worker | asyncio.sleep / yield | Does not block loop | Independent of polling | Releases DB in all paths | Timeout on HTTP | Exception handling in loop | Alignment (600s) risk | Pool / long-conn risk |
|--------|------------------------|---------------------|------------------------|---------------------------|-----------------|----------------------------|-----------------------|------------------------|
| activation_worker | Yes (cooperative_yield, sleep 0.5 after conn release) | Yes | Yes | Yes (async with pool.acquire) | VPN timeout in vpn_utils | Yes | No 600s | Holds conn during VPN call (session lock) |
| auto_renewal | Yes (sleep 600, cooperative_yield) | Yes | Yes | Yes | N/A (DB only in tx) | Yes | Yes (600s) | Holds 1 conn for batch (≤15s) |
| fast_expiry_cleanup | Yes (cooperative_yield) | Yes | Yes | Yes | VPN timeout | Yes | No | Holds conn across HTTP (risk) |
| reconcile_xray_state | Yes (sleep 0, wait_for 20s) | Yes | Yes | Yes | Yes (HTTP_TIMEOUT) | Yes | Yes (600s) | Short acquires |
| trial_notifications | Yes (sleep, batch yield) | Yes | Yes | Yes | Yes (VPN) | Yes | No | Per-row acquire |
| crypto_payment_watcher | Yes (sleep, yield) | Yes | Yes | Yes | Yes | Yes | No | Short acquires |
| health_check_task | Yes (sleep 600) | Yes | Yes | Yes | Yes (check_vpn_keys) | Yes | Yes (600s) | Brief acquire |

**Summary:** All workers use asyncio.sleep or cooperative yield; none depend on polling; all release connections via context managers or explicit release. Risks: (1) fast_expiry holds one conn across HTTP calls (see forensic audit); (2) activation holds conn during VPN call; (3) 600s alignment of auto_renewal, reconcile, health_check. These are unchanged under webhook; webhook does not add new workers or change pool usage pattern.

---

## SECTION 5 — DB Pool & Connection Lifecycle

- **Pool size:** min 2, max 15 (env DB_POOL_MIN_SIZE / DB_POOL_MAX_SIZE).
- **Acquire timeout:** 10s (DB_POOL_ACQUIRE_TIMEOUT).
- **Command timeout:** 30s (DB_POOL_COMMAND_TIMEOUT).
- **Advisory lock:** 1 connection held for process lifetime (not returned to pool until shutdown).
- **Effective capacity:** 14 connections when advisory lock is held.
- **Connections held during HTTP:** (1) fast_expiry_cleanup holds one conn for entire batch including `remove_uuid_if_needed` (HTTP). (2) activation_worker and reissue_vpn_key_atomic hold conn during add_vless_user (HTTP). No nested acquire inside an already-held conn in the same coroutine (no pool.acquire inside conn.transaction in same task for these paths).
- **Leak check:** No acquire without release in normal paths; all use `async with pool.acquire()` or explicit release in finally. Instance_lock_conn is released in main() finally via pool.release(instance_lock_conn).

**Scenario (50 concurrent updates):** Under webhook, 50 concurrent POSTs can trigger 50 handler runs. Each may use pool (e.g. finalize_purchase, or read-only). With MAX_CONCURRENT_UPDATES=20, only 20 run at once; each typically holds a conn briefly. So 20 conns for handlers + up to ~6–8 for workers (if they run at same time) can approach 14. Risk of pool starvation exists today under high load; webhook can increase concurrency if many updates arrive at once, so same risk or slightly higher.

**Conclusion:** Pool starvation risk under webhook is MEDIUM (same as today; possibly slightly higher if webhook receives bursts). No new leak or missing release introduced by webhook.

---

## SECTION 6 — Financial Integrity Check

- **finalize_purchase / finalize_balance_purchase:** Use single transaction; no reference to polling or transport. Notifications sent after commit (caller sends after return). Referral applied inside same transaction via process_referral_reward(conn=conn).
- **grant_access:** No polling dependency; renewal extends from current expires_at; UUID not regenerated on renewal.
- **process_referral_reward:** Requires conn; idempotency by purchase_id; no polling dependency.
- **ensure_user_in_xray / renewal_xray_sync_after_commit:** Called post-commit; no polling dependency.
- **Payment finalization:** Triggered by Telegram payment handler or Crypto webhook; neither depends on polling lifecycle.

**Verdict:** No financial logic depends on polling transport. Safe for webhook from a financial-integrity perspective.

---

## SECTION 7 — VPN / Xray Logic

- **update_vless_user / add_vless_user / remove_vless_user / list_vless_users:** All use `httpx.AsyncClient(timeout=HTTP_TIMEOUT)` (config, ≥3s). No sync requests.
- **Circuit breaker:** Used before/after VPN calls; `threading.Lock` in circuit_breaker — no `await` inside lock; critical section is minimal (state read/update).
- **404 → add_user fallback:** In ensure_user_in_xray; only on InvalidResponseError with "Client not found"; no blind retry.
- **Retries:** Bounded (e.g. MAX_RETRIES=2 in vpn_utils). No infinite retry loops.

**Verdict:** VPN/Xray layer is webhook-ready; timeouts and async usage are correct.

---

## SECTION 8 — Circuit Breaker & Threading Lock

- **threading.Lock usage:** app/core/circuit_breaker.py, rate_limits.py, rate_limit.py, bulkheads.py, circuit_breakers.py, metrics.py. All use `with self._lock` or `with _registry_lock` for short, synchronous sections. No `await` inside any of these locks in the codebase.
- **Nested locking across async boundaries:** None found. Locks are released before any await.
- **Webhook and concurrency:** Under webhook, more concurrent requests can hit the same process (e.g. many POSTs at once). Each request that triggers VPN or rate-limit or metrics will take a brief threading lock. Contention could slightly increase latency under high concurrency but does not introduce deadlock (single-threaded event loop). No change to lock semantics required for webhook.

---

## SECTION 9 — Health & Watchdogs

- **perform_health_check:** Uses pool.acquire() and check_vpn_keys (HTTP with timeout). Not tied to polling. Health server is aiohttp; endpoint does not depend on polling.
- **Multi-signal watchdog:** Uses event_loop_heartbeat, last_worker_iteration_timestamp, last_successful_healthcheck_timestamp. None depend on polling. Watchdog will not kill process during webhook migration; it only reacts when all three are stale (real freeze).
- **Polling heartbeat watchdog:** Depends on last_polling_activity and cancels only polling_task. Under webhook, (1) last_polling_activity can still be updated by the same middleware when updates are fed, so “activity” remains; (2) there is no polling_task to cancel. So this watchdog must be disabled or repurposed for webhook mode (e.g. alert if no update received for N seconds, without cancelling a task).

**Verdict:** Health and multi-signal watchdog are webhook-ready. Polling heartbeat watchdog is polling-specific and must be removed or repurposed for webhook.

---

## SECTION 10 — Shutdown & Graceful Stop

- **Advisory lock:** Released in finally: pg_advisory_unlock, then pool.release(instance_lock_conn), then instance_lock_conn = None. Not tied to polling.
- **Workers:** All tasks in background_tasks are cancelled, then awaited. Includes polling_task; under webhook that task would not exist (or would be replaced by web server task).
- **DB pool:** database.close_pool() called after advisory lock release. Correct order.
- **Bot session:** bot.session.close() after pool close. No dangling background tasks if all are in background_tasks and properly awaited after cancel.
- **Polling-specific shutdown:** `dp.stop_polling()` is called in finally. Under webhook, equivalent would be stopping the web server (e.g. app runner). No business logic is tied to stop_polling.

**Verdict:** Shutdown is correct and not coupled to polling for business logic; only the “stop polling” call would become “stop web server” in webhook mode.

---

## SECTION 11 — Risk Matrix

| Component | Webhook-ready | Risk level | Blocking issue | Needs refactor before webhook |
|-----------|---------------|------------|----------------|-------------------------------|
| main() startup order | Yes | LOW | No | No |
| Advisory lock | Yes | LOW | No | No |
| Workers (all) | Yes | LOW | No | No |
| Dispatcher / handlers | Yes | LOW | No | No |
| Financial paths | Yes | LOW | No | No |
| VPN/Xray | Yes | LOW | No | No |
| Health server | Yes | LOW | No | No |
| Multi-signal watchdog | Yes | LOW | No | No |
| Polling heartbeat watchdog | No | LOW | Yes (assumes polling_task) | Yes (disable or repurpose) |
| Webhook audit block at startup | No | MEDIUM | Yes (deletes webhook, exits if set) | Yes (invert for webhook: set webhook, skip delete) |
| DB pool (14 effective) | Yes | MEDIUM | No | Optional: monitor under webhook |
| fast_expiry (conn across HTTP) | Yes | MEDIUM | No | Optional (forensic fix) |
| threading.Lock in async code | Yes | LOW | No | No |

---

## SECTION 12 — Final Verdict

### 1. Is the system webhook-ready today?

**NO** — but only due to two localized, non-business items:

- **Startup:** Current code assumes polling. It deletes webhook and exits if webhook URL is set. For webhook mode you must either (a) set webhook and not run this delete logic, or (b) branch on “mode” (polling vs webhook) and only delete webhook when in polling mode.
- **Polling heartbeat watchdog:** It cancels a task named `"polling_task"`. In webhook mode there is no such task. So it must be disabled or repurposed (e.g. “no update received” alert only) to avoid no-op or confusion.

No other component blocks webhook migration. Workers, DB, advisory lock, financial logic, VPN, health, and multi-signal watchdog are transport-agnostic.

### 2. Blocking issues

1. **Webhook audit block (main.py ~535–567):** Deletes webhook and exits if webhook remains set. For webhook mode, this block must be replaced or bypassed so that the bot sets and keeps the webhook instead of deleting it.
2. **Polling heartbeat watchdog (main.py ~587–600):** Depends on existence of `polling_task`. In webhook mode, either do not start this task or replace it with logic that does not cancel a non-existent task (e.g. only log or alert if last_polling_activity is stale).

### 3. Optional hardening

- Add asyncio.wait_for(perform_health_check(), timeout=30) in health_check_task to avoid indefinite block.
- Reduce or monitor long-held connections (fast_expiry, activation) as in forensic audit.
- Under webhook, consider wrapping the Telegram webhook handler in a timeout (e.g. 55s) and returning 200 + processing in background if needed.
- Add mode flag (POLLING vs WEBHOOK) to avoid conditional logic scattered in main().

### 4. Pool starvation risk under webhook

**MEDIUM.** Same as today: 14 effective connections, multiple workers and handlers can use them. Webhook can increase concurrent request count (burst of updates), so pressure could be slightly higher. Mitigation: keep MAX_CONCURRENT_UPDATES (e.g. 20) and semaphore; monitor pool wait time and size under load.

### 5. Event loop stall risk under webhook

**LOW.** Same as today: brief threading.Lock usage in circuit breaker/rate limit/metrics; no await inside locks. Webhook does not introduce new blocking. Long handler work is async (DB/HTTP with await); as long as handlers complete within Telegram’s 60s, no new stall.

### 6. Estimated safe concurrency (2 CPU / 4 GB VPS)

- **Polling today:** One long-poll connection; concurrency is naturally limited by Telegram and by MAX_CONCURRENT_UPDATES (20). 2 CPU / 4 GB is generally sufficient.
- **Webhook:** Same process; concurrency limited by (1) Telegram webhook delivery (they send one update per request and expect 200 within 60s), (2) MAX_CONCURRENT_UPDATES if you apply the same semaphore to webhook handler. With same limits, 2 CPU / 4 GB remains reasonable. If you allow many concurrent webhook requests (e.g. 50+), pool and CPU can be stressed; recommend keeping a cap (e.g. 20–30 concurrent update handlers) and timeouts.

### 7. Should VPN be isolated to a separate VPS?

**Not required for webhook readiness.** VPN/Xray API calls are HTTP with timeouts; they already run in the same process. Isolating VPN to another VPS is an operational/reliability choice (e.g. to avoid one process affecting the other), not a prerequisite for switching to webhook. The audit does not require it.

---

**End of report. No code was modified.**
