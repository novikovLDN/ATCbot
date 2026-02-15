# Full Forensic Audit — ~11 Minute Production Freeze

**Scope:** Diagnose hard freeze (event loop stall or deadlock) after ~11 minutes. No code changes; analysis and report only.

---

## 1. Confirmed Safe Components

- **Polling layer:** `polling_task` created once with name `"polling_task"`; `polling_heartbeat_watchdog` cancels only that task; `start_polling_with_auto_restart` catches `CancelledError` and continues loop; no duplicate task creation on restart.
- **Watchdogs:** Event-loop heartbeat every 5s; multi-signal watchdog every 30s; no `os._exit`; `SystemExit(1)` only when all three heartbeats stale.
- **Blocking I/O in async:** No `time.sleep()` in production async paths; no synchronous `requests.*`; no `subprocess.*` or `os.system()` in bot code. `xray_api/main.py` uses `asyncio.to_thread(_load_xray_config_file)` (separate Xray API service).
- **HTTP timeouts:** `vpn_utils` uses `HTTP_TIMEOUT` (config, min 3s); `cryptobot_service` and `payments/cryptobot` use `timeout=10.0` or `30.0`; httpx clients created with explicit timeout in all traced call sites.
- **Reconciliation timeout:** `reconcile_xray_state_task` wraps `reconcile_xray_state()` in `asyncio.wait_for(..., timeout=RECONCILIATION_TIMEOUT_SECONDS)` (20s).
- **Advisory lock release:** Main uses `pool.release(instance_lock_conn)` in finally; activation service and `reissue_vpn_key_atomic` use `pg_advisory_unlock` in `finally`.
- **Worker loops:** All major `while True` worker loops contain `await` (e.g. `asyncio.sleep`, `pool.acquire()`, or `cooperative_yield()`). No unbounded CPU-only loop without yield.
- **Idempotency:** `finalize_purchase` / referral / notifications use `purchase_id` and `is_payment_notification_sent`; no double-processing from retries alone.

---

## 2. High Probability Freeze Candidates (Ranked)

### 2.1 **DB connection held across long external HTTP (fast_expiry_cleanup)**

**Path:** `fast_expiry_cleanup_task` → `async with _worker_lock` → inner `while True` → `async with pool.acquire() as conn` → `for i, row in enumerate(rows):` → `await vpn_service.remove_uuid_if_needed(...)` (HTTP to Xray API) while **same `conn` is still held**.

**Risk:** One connection is held for the entire batch. Each iteration does `get_active_paid_subscription(conn)`, then `remove_uuid_if_needed()` (HTTP), then `_log_vpn_lifecycle_audit_async`. If the batch has many rows and/or VPN API is slow or hangs (e.g. TCP stall without timeout), the connection is held for a long time. Pool `command_timeout` is 30s; if the server closes the connection mid-loop, subsequent use of `conn` can raise and/or leave the connection in a bad state. Combined with other workers also holding connections, this can contribute to **pool exhaustion** or **connection state corruption**.

**Correlation with ~11 min:** If fast_expiry runs on a schedule that aligns with other 600s tasks, a long-running batch could overlap with auto_renewal/reconcile and increase pressure.

---

### 2.2 **threading.Lock from async code (circuit_breaker, rate_limits, metrics)**

**Path:** Every VPN API call (add_vless_user, update_vless_user, remove_vless_user, list_vless_users, check_xray_health) goes through `app.core.circuit_breaker`: `get_circuit_breaker("vpn_api")` → `with _registry_lock` (threading), then `should_skip()` / `record_failure()` → `with self._lock` (threading).

**Risk:** In a single-threaded asyncio process, `with self._lock` blocks the **entire event loop** for the duration of the critical section. The sections are short (state read/update). If any code path ever held a threading lock across an `await`, the event loop would block until that await completes while no other coroutine can run—so the lock would never be released by the “other” coroutine (same thread), leading to **permanent deadlock**. Audit did not find `await` inside `with self._lock` in circuit_breaker/rate_limit/metrics. So the direct risk is **short event-loop stalls** (microseconds per VPN call), not an 11-minute freeze by itself, unless lock contention or a slow path is introduced elsewhere.

**Recommendation:** Treat as medium risk for responsiveness; ensure no `await` is ever added inside `with self._lock` / `with _registry_lock`.

---

### 2.3 **Session-level pg_advisory_lock held during HTTP (activation_worker, reissue)**

**Path:**  
- `activation_worker` → `attempt_activation(conn=conn)` → `pg_advisory_lock(subscription_id)` on `conn` → `await vpn_utils.add_vless_user(...)` (HTTP) → then transaction → `pg_advisory_unlock` in `finally`.  
- `database.reissue_vpn_key_atomic` → `pg_advisory_lock(telegram_id)` on `conn` → `await vpn_utils.add_vless_user(...)` (HTTP) → transaction → `pg_advisory_unlock` in `finally`.

**Risk:** The same DB connection is held (and not returned to the pool) for the full duration of the VPN HTTP call. If the VPN API is slow or hangs up to the HTTP timeout, that connection is occupied for that long. With pool max 15 and one conn used for the main advisory lock, 14 are available; several such “conn held during HTTP” paths can accumulate and **reduce available connections** for other workers. They do not by themselves explain a full 11-minute freeze unless combined with pool exhaustion (all conns held and new `pool.acquire()` blocks up to `DB_POOL_ACQUIRE_TIMEOUT` 10s, and retries or repeated work extend the stall).

---

### 2.4 **600s (10 min) timing alignment**

**Intervals:**  
- `AUTO_RENEWAL_INTERVAL_SECONDS = 600` (10 min)  
- `RECONCILIATION_INTERVAL_SECONDS = 600`  
- `health_check_task`: `await asyncio.sleep(10 * 60)` (600s)

So at T=600s from start, **auto_renewal**, **reconcile_xray_state_task**, and **health_check_task** can all wake in the same window.

**Risk:**  
- **auto_renewal:** `process_auto_renewals` does `while True: async with pool.acquire() as conn: async with conn.transaction():` and can process up to BATCH_SIZE (100) rows in **one transaction**, with a 15s cap (`MAX_ITERATION_SECONDS`). So one connection can be held for up to ~15s.  
- **reconcile:** Runs with 20s timeout; does keyset pagination (many short acquire/release), then `list_vless_users()` (HTTP), then per-orphan `remove_vless_user()` (HTTP).  
- **health_check:** `perform_health_check()` uses `pool.acquire()` and `check_vpn_keys()` → `vpn_utils.check_xray_health()` (HTTP).

If at 600s all three run and each holds or repeatedly acquires connections while doing HTTP/DB work, **concurrent use can approach or reach pool size**. If any of these paths then blocks (e.g. HTTP hang despite timeout, or a slow DB call), others waiting on `pool.acquire()` will block up to 10s (acquire timeout). Repeated or cascading blocking could extend the stall toward the observed ~11 minutes.

---

## 3. Medium Probability

### 3.1 **auto_renewal Phase B: sequential pool.acquire for notifications**

After the main transaction commits, `for item in notifications_to_send:` does `async with pool.acquire() as notify_conn:` per item to mark notification sent. If the list is large (e.g. many renewals in one run), this is many sequential acquires. Each is short-lived; risk is elevated only if the pool is already stressed and acquire timeouts start firing or pile up.

### 3.2 **trial_notifications inner loops**

`process_trial_notifications` has an inner `while True` with `async with pool.acquire() as conn` and fetch; then `for row in rows: await _process_single_trial_notification(bot, pool, dict(row), now)`. Each `_process_single_trial_*` does its own `pool.acquire()`. So at most two connections in play for that flow (one for the fetch loop, one for the current row’s processing). Not inherently deadlock-prone but adds to concurrent pool usage.

### 3.3 **reconcile_xray_state keyset loop: many short acquires**

`while True: async with pool.acquire() as conn: fetch; ...; await asyncio.sleep(0)`. Connection is released each iteration. If the table is large, many iterations run; each iteration is short. Risk is high only if pool is near exhaustion and acquire starts blocking.

### 3.4 **health_check_task no overall timeout**

`perform_health_check()` is awaited without a wrapper timeout. It uses `pool.acquire()` and `check_vpn_keys()` (HTTP with timeout). If `get_pool()` or `pool.acquire()` blocks (e.g. pool exhausted), the health task could block until acquire timeout (10s). That alone does not explain 11 minutes but can add to a cascade.

---

## 4. Low Probability

- **Duplicate polling task:** Only one `polling_task` is created and appended to `background_tasks`; no new task created on polling restart.  
- **Wrong task cancelled:** Watchdog cancels by `task.get_name() == "polling_task"`; name is set at creation.  
- **Infinite retry without backoff:** Polling and reconciliation have bounded backoff or timeout.  
- **Unclosed httpx/aiohttp:** httpx clients used as `async with httpx.AsyncClient(...)`; aiohttp app is the health server (long-lived). No obvious leak of clients/sessions in the bot process.  
- **Unbounded list growth:** No pattern found of lists growing without bound in hot paths.

---

## 5. Code Paths That Can Stall the Event Loop

1. **Any `with threading.Lock` / `with self._lock` (circuit_breaker, rate_limits, metrics, bulkheads):** Blocks the event loop for the duration of the critical section. Currently no `await` inside these blocks; sections are short.  
2. **`pool.acquire()` when pool is exhausted:** Blocks up to `DB_POOL_ACQUIRE_TIMEOUT` (10s). If many coroutines are waiting, they run one after another after each release, so total delay can be “N waiters × 10s” in the worst case.  
3. **Long sync work in a coroutine without yield:** No CPU-heavy loop without `await` was found in worker/polling paths; `cooperative_yield()` is used in batch loops.

---

## 6. External Dependencies That Can Hang

| Dependency        | Timeout / bound              | Notes                                      |
|-------------------|------------------------------|--------------------------------------------|
| PostgreSQL        | `command_timeout` 30s, acquire 10s | Pool can block on acquire if exhausted.   |
| Xray / VPN API    | `HTTP_TIMEOUT` (config, ≥3s) | All vpn_utils HTTP uses this.              |
| Crypto Pay API    | 10s or 30s                   | httpx.AsyncClient(timeout=...) in call sites. |
| Telegram Bot API  | aiogram default              | Polling uses `polling_timeout=30`.         |

If any HTTP client is ever used without a timeout (e.g. default), that call could hang until the OS TCP timeout (very long). Audit did not find such a call in the bot’s own code.

---

## 7. Pool Starvation Risks

- **Pool size:** `max_size=15` (env `DB_POOL_MAX_SIZE`). One connection held for the **main advisory lock** for process lifetime → **14 effective**.  
- **Concurrent usage:**  
  - Main: 1 (advisory).  
  - activation_worker: 1 at a time (per subscription).  
  - auto_renewal: 1 for the whole inner batch (up to ~15s).  
  - reconcile: 1 at a time in keyset loop (short).  
  - trial_notifications: 1–2 (fetch + per-row processing).  
  - fast_expiry_cleanup: **1 held for the whole batch**, including **HTTP calls** (see above).  
  - crypto_payment_watcher: 1 at a time.  
  - health_check: 1 during `perform_health_check`.  
  - Handlers: on demand.

If several workers run at once (e.g. at 600s) and each holds a connection for several seconds (especially fast_expiry holding across HTTP), the number of conns in use can approach 14. Once at the limit, the next `pool.acquire()` blocks (up to 10s). If that next acquire is in a critical path (e.g. polling middleware, health, or a worker that others depend on), the system can appear frozen for that period; repeated cycles could approach an 11-minute window.

---

## 8. Deadlock Risks

- **asyncio.Lock:** Each worker uses its own `_worker_lock` (activation, auto_renewal, reconcile, crypto, fast_expiry). No nested acquisition of multiple asyncio locks in one flow; **no asyncio deadlock identified**.  
- **Advisory locks:**  
  - Main: one session-level `pg_advisory_lock(987654321)` on a dedicated conn.  
  - Per-user/per-subscription: `pg_advisory_xact_lock(telegram_id)` or `pg_advisory_lock(subscription_id)` inside call paths that use `conn`.  
  - Order: Main lock is app-wide; user/subscription locks are per-row. No evidence of inconsistent lock ordering that would cause PG deadlock.  
- **threading.Lock:** Single-threaded event loop; no second thread acquiring the same lock. Deadlock would require `with lock: ... await ...`; **none found**.

---

## 9. Watchdog Failure Modes

- **Freeze with event loop still ticking:** If the loop is not blocked (e.g. only polling or some workers are stuck in a long `await`), the event-loop heartbeat and health endpoint can keep updating. Then `are_all_stale()` stays False and the multi-signal watchdog **does not** fire. So a “soft” freeze (e.g. polling stuck but health still 200) can persist.  
- **Full event loop block:** If the loop is blocked (e.g. by a threading lock or a sync call), no task runs, so all three heartbeats go stale and the watchdog should eventually raise `SystemExit(1)`. User reports “process does NOT exit,” which is more consistent with **event loop still running but some critical path stuck** (e.g. waiting on `pool.acquire()` or a long HTTP wait) rather than a full loop block.  
- **Polling heartbeat watchdog:** Only restarts the polling task. If the freeze is in a worker or in DB/HTTP used by several tasks, restarting polling alone would not fix it.

---

## 10. Exact Next Debugging / Instrumentation Steps

1. **Log pool usage at acquire/release:** In `database.get_pool()` (or pool wrapper), add optional logging on each `acquire()` and `release()` (e.g. current pool size, wait time). Confirm whether around 600s (and ~11 min) the pool hits max and wait time spikes.  
2. **Trace long-held connections:** Add a small layer or callback that records timestamp at `pool.acquire()` and logs (or metrics) when the same connection is held longer than e.g. 5s or 10s. Correlate with fast_expiry_cleanup, auto_renewal, and activation_worker.  
3. **Wrap perform_health_check in wait_for:** e.g. `await asyncio.wait_for(perform_health_check(), timeout=30)` in `health_check_task` to avoid the health task itself blocking indefinitely.  
4. **Log at 600s boundaries:** When `AUTO_RENEWAL_INTERVAL_SECONDS` / `RECONCILIATION_INTERVAL_SECONDS` / 10-min sleep completes, log “worker X starting iteration at T=N”. Confirm overlap of auto_renewal, reconcile, and health at T≈600s.  
5. **Instrument fast_expiry_cleanup:** Log at start and end of each inner batch (and optionally per row) with batch size and duration. If duration approaches 30s (command_timeout), treat as proof of “conn held across HTTP” risk.  
6. **Circuit breaker / lock timing:** Optionally log time spent inside `should_skip()` / `record_failure()` (or use a small decorator) to confirm that threading lock hold time is negligible (microseconds).  
7. **Stale heartbeat test:** In a staging environment, temporarily stop updating one of the three heartbeats and confirm that the multi-signal watchdog fires and exits the process as designed.

---

## Summary Table

| Category              | Finding                                                                 |
|-----------------------|-------------------------------------------------------------------------|
| Blocking in async     | No `time.sleep`/`requests` in bot; threading locks used briefly, no `await` inside. |
| Long loops without yield | None; workers use `await` or `cooperative_yield()`.                     |
| Asyncpg misuse        | **fast_expiry_cleanup** holds one conn for full batch including HTTP; activation/reissue hold conn during VPN HTTP. |
| 600s timing           | auto_renewal, reconcile, health_check all at 600s → concurrent load.   |
| Pool exhaustion       | Plausible when several workers hold conns and fast_expiry holds one long. |
| Deadlock              | No asyncio or PG lock-order deadlock identified; no `await` inside threading lock. |
| Watchdog              | Multi-signal watchdog will not fire if loop is still ticking (e.g. only polling or some workers stuck). |

**Most plausible scenario for ~11 min freeze:** At ~600s, auto_renewal, reconcile, and health_check run together; fast_expiry (or another worker) may also be holding a connection across HTTP. Pool usage approaches or reaches the effective limit of 14; one or more `pool.acquire()` blocks for up to 10s. If the “stuck” path is in a critical task (e.g. the one that drives health or logging), the system can appear fully frozen while the loop is actually blocked on connection acquisition or a long HTTP wait. **Recommended first fix:** Stop holding a single DB connection across the entire fast_expiry batch when doing VPN API calls (e.g. release conn between rows or do HTTP outside the conn-holding loop), and add the instrumentation above to confirm pool and timing correlation.
