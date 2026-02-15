# Full Forensic Audit — 11 Minute Freeze Investigation

**Mode:** Read-only. No code changes.  
**Goal:** Identify exact root cause of periodic ~11 minute freeze (process alive, no crash, logs stop, bot unresponsive, only redeploy restores).

---

## SECTION 1 — Timeline Reconstruction

### 1.1 Exact timing correlation

| Component | Interval (default) | Fires at T = |
|-----------|---------------------|--------------|
| AUTO_RENEWAL_INTERVAL_SECONDS | 600s (10 min) | 600, 1200, 1800, ... |
| RECONCILIATION_INTERVAL_SECONDS | 600s | 600, 1200, ... |
| health_check_task | `sleep(10*60)` = 600s | 600, 1200, ... |
| fast_expiry_cleanup | CLEANUP_INTERVAL_SECONDS = 60s (clamped 60–300) | 60, 120, ..., **600**, ... |
| crypto_payment_watcher | CHECK_INTERVAL_SECONDS = 30s | 30, 60, ..., **600**, ... |

**Conclusion:** At **T = 600 seconds** the following can start in the same window:

- **auto_renewal** (wakes from 600s sleep, acquires _worker_lock, runs process_auto_renewals)
- **reconcile_xray_state_task** (wakes from 600s sleep, acquires _worker_lock, runs reconcile_xray_state with 20s timeout)
- **health_check_task** (wakes from 600s sleep, runs perform_health_check)
- **fast_expiry_cleanup** (10th tick at 600s, acquires _worker_lock, runs inner batch loop)
- **crypto_payment_watcher** (20th tick at 600s, acquires _worker_lock, runs check_crypto_payments)

So **multiple 600s-aligned workers** can become runnable at the same time. They do not all start in the same instant (asyncio schedules them after their sleep completes), but within a short window several will run.

### 1.2 Burst DB activity at 600s boundary

- **auto_renewal:** Holds one connection for the entire inner `while True` batch (up to MAX_ITERATION_SECONDS = 15s), and in Phase B does one `pool.acquire()` per notification (sequential).
- **reconcile:** Multiple short `pool.acquire()` in keyset loop; then HTTP (list_vless_users, remove_vless_user); no long-held conn in one shot.
- **health_check:** One `pool.acquire()` in check_database_connection; brief.
- **fast_expiry:** Holds **one** connection for the entire inner batch loop, including **HTTP** (remove_uuid_if_needed) per row — can be tens of seconds.
- **crypto_payment_watcher:** Short-lived acquires per batch; no hold across HTTP in a single conn.

So at T=600 we can have: **1 (advisory) + 1 (auto_renewal) + 1 (reconcile keyset) + 1 (health) + 1 (fast_expiry batch)** = 5 connections in use quickly. If activation_worker, trial_notifications, or handlers are also active, total can approach 14. **Burst DB (and HTTP) activity at 600s is confirmed.**

### 1.3 Freeze relative to worker log

- **Logs stop immediately or gradually:** Not determinable from code alone. If the cause is pool exhaustion, the next task that tries `pool.acquire()` would block (up to 10s); logs would stop when no task that logs can run (e.g. workers stuck on acquire, or the task that was about to log is blocked).
- **Health endpoint:** Implemented as aiohttp handler; **does not use pool** (only reads `database.DB_READY`). So if the event loop is still running, health can keep responding. If the loop is fully blocked, health would stop.
- **Watchdog:** Fires only when `are_all_stale()` is True (event_loop > 60s, worker > 90s, healthcheck > 90s). If any one of the three heartbeats keeps updating, watchdog does **not** fire.
- **Event loop heartbeat:** Task runs every 5s, does `mark_event_loop_heartbeat()` and `await asyncio.sleep(5)`. It does **not** log in the loop. So if the event loop is running at all, this task keeps the event_loop heartbeat fresh.

**Precise correlation mapping:**

- Freeze aligns with **600-second boundary** (multiple workers + fast_expiry + crypto at 600s).
- At 600s: burst of pool usage and, in fast_expiry, **one connection held across many HTTP calls**.
- If pool is exhausted, subsequent `pool.acquire()` blocks (up to 10s per waiter); workers and handlers that need a conn stop making progress → no new worker/handler logs; bot stops responding.
- Event_loop_heartbeat can still run (no DB/HTTP), so event_loop heartbeat stays fresh.
- If health is still called (e.g. by Railway or load balancer), health handler runs (no pool), so healthcheck heartbeat stays fresh.
- Then worker heartbeat is the only one that can go stale (no worker completes an iteration), so **are_all_stale() stays False** (we need all three stale). **Watchdog does not fire.**
- So: **process alive, logs stop (no worker/handler completion), health may still respond, watchdog does not exit.**

---

## SECTION 2 — Event Loop Starvation Audit

### 2.1 Synchronous I/O in async paths

- **time.sleep:** Not used in production async code paths (only asyncio.sleep).
- **requests.*** : Not used.
- **Blocking file I/O:** Not used in bot code. (xray_api uses asyncio.to_thread for config load in a separate service.)
- **subprocess:** Only in xray_api (separate process); not in bot.
- **Heavy CPU loops without await:** All worker batch loops use `cooperative_yield()` (asyncio.sleep(0)) every 50 items or similar, or are capped by MAX_ITERATION_SECONDS. No unbounded CPU loop without yield found.

**Verdict:** No synchronous I/O or unbounded CPU loop in async paths that would starve the loop.

### 2.2 threading.Lock usage

- **Await inside lock:** Searched all `with self._lock` / `with _registry_lock` in circuit_breaker.py, circuit_breakers.py, rate_limits.py, rate_limit.py, bulkheads.py, metrics.py. **No `await` inside any of these blocks.** All are short state read/update.
- **Nested lock across async boundaries:** None found. Locks are released before any await.
- **Hold time:** Minimal (in-memory state only).

**Verdict:** threading.Lock is not the cause of an 11-minute stall; hold time is negligible.

### 2.3 Long CPU-bound loops without yield

- All major loops either: call `await cooperative_yield()` (or equivalent) periodically, or are inside a transaction with a time cap (MAX_ITERATION_SECONDS), or iterate over a bounded batch. **No confirmed long CPU loop without yield.**

### 2.4 Tasks holding the event loop

- **while True without await:** Every `while True` in main and workers contains `await asyncio.sleep(...)` or `await pool.acquire()` or other await; none is a tight spin.
- **Unresolving future:** No evidence of awaiting a future that never completes, except **pool.acquire()** when the pool is exhausted: it blocks for up to **timeout** (10s) then raises. So it does eventually resolve (success or timeout).
- **Recursion:** No unbounded recursion found in hot paths.

**Conclusion:** Event loop starvation is not caused by sync I/O, threading locks, or unbounded loops. The only plausible “wait forever” is if something in the **call chain** of an awaited call never completes (e.g. external HTTP with a broken timeout). All httpx usages audited use an explicit timeout.

---

## SECTION 3 — DB Pool Starvation Deep Audit

### 3.1 Pool configuration

- max_size = 15 (DB_POOL_MAX_SIZE)
- 1 connection reserved for advisory lock (main) → **14 effective**
- acquire timeout = 10s (DB_POOL_ACQUIRE_TIMEOUT)
- command_timeout = 30s (DB_POOL_COMMAND_TIMEOUT)

### 3.2 Every place pool.acquire() is used (summary)

- **main.py:** One acquire for advisory lock (held until shutdown).
- **database.py:** Many; most are `async with pool.acquire() as conn`. One path in `grant_access` when conn is None: `conn = await pool.acquire()` then later `await pool.release(conn)` if should_release_conn.
- **Workers:** activation_worker (one per subscription via async with), auto_renewal (one for batch then sequential for notifications), fast_expiry (one per inner batch), trial_notifications (one per batch / per row), reconcile (one per keyset page), crypto_payment_watcher (short acquires), healthcheck (one during perform_health_check).

### 3.3 Paths that hold connection across HTTP or long work

| Path | Holds conn across HTTP? | Holds conn for full batch? | Max hold time (theory) |
|------|--------------------------|----------------------------|-------------------------|
| fast_expiry_cleanup inner loop | **Yes** (remove_uuid_if_needed) | **Yes** (one conn for whole batch) | 15s cap (MAX_ITERATION_SECONDS) but conn held across many HTTP calls |
| activation_worker (per sub) | **Yes** (add_vless_user while holding conn) | No (one conn per subscription) | VPN timeout (e.g. 5s) + DB work |
| database.reissue_vpn_key_atomic | **Yes** (add_vless_user, remove_vless_user) | No | VPN timeout × 2 + DB |
| auto_renewal | No (HTTP/Telegram after commit) | Yes (one conn for batch ≤15s) | ~15s |
| reconcile | No (HTTP after keyset loop) | No (short per page) | Short |
| trial_notifications | Per-row: can hold conn while calling remove_vless_user in _process_single_trial_expiration | Per row | VPN timeout |
| crypto_payment_watcher | No | No | Short |
| health_check | No (check_vpn_keys is after pool use) | No | Short |

### 3.4 Can 3–5 workers overlap and consume 14 connections?

**At T = 600s:**

- Advisory: 1
- auto_renewal: 1 (up to ~15s)
- fast_expiry: 1 (up to ~15s, **held across HTTP**)
- reconcile: 1 (short per keyset iteration)
- health_check: 1 (short)
- activation_worker: 1 per subscription being processed (sequential)
- trial_notifications: 1–2 (fetch + per-row)
- Handlers: N (up to MAX_CONCURRENT_UPDATES = 20, but semaphore limits concurrency)

So **5–8** connections can be in use quickly from workers alone. If handlers (e.g. payment finalization) also use the pool, and several are active, we can reach **14**. So **pool exhaustion at 600s is plausible.**

### 3.5 When pool.acquire() blocks

- **Behavior:** Awaitable blocks until a connection is free or **timeout** (10s). Then either success or asyncpg timeout exception.
- **Event loop:** The awaiting coroutine yields; the event loop keeps running. So **other tasks run** (including event_loop_heartbeat, health server). So the loop is not “dead” — only the tasks waiting on the pool are stuck.
- **Cascade:** If many tasks await pool.acquire(), they queue; each gets a turn when a conn is released. So we get a cascade of 10s waits. If releases are slow (e.g. fast_expiry holding conn for 15s), the queue drains slowly. **Logs can “stop”** in the sense that no worker/handler completes and logs, while event_loop_heartbeat (which doesn’t log) keeps the heartbeat fresh.

**Worst-case pool usage model at T=600s:**

- 1 advisory + 1 auto_renewal + 1 fast_expiry + 1 reconcile + 1 health + 1–2 activation/trial + 2–6 handlers ≈ **9–13** connections. With a spike of handlers or an extra worker, **14 can be reached**. Once at 14, the next acquire blocks for up to 10s. **Starvation can explain “bot stops responding” and “no new logs” from workers/handlers**, and (with health and event_loop_heartbeat still running) **watchdog not firing**.

---

## SECTION 4 — Deadlock Analysis

### 4.1 Advisory locks

- **Main:** One session lock (ADVISORY_LOCK_KEY 987654321) on a dedicated conn; no nesting.
- **Per-user / per-subscription:** `pg_advisory_xact_lock(telegram_id)` or `pg_advisory_lock(subscription_id)` in database and activation service; used with a single conn per flow; released in same flow (transaction end or finally).
- **Ordering:** No cross-worker ordering requirement (each worker uses its own conn and lock keys). No inconsistent ordering between main and workers (main does not take per-user locks).

**Verdict:** No advisory lock deadlock identified.

### 4.2 asyncio.Lock

- Each worker has its own `_worker_lock` (activation, auto_renewal, reconcile, crypto, fast_expiry). No shared asyncio lock between workers. No nested acquisition (e.g. worker A holding lock and awaiting something that needs worker B’s lock) found.

**Verdict:** No asyncio deadlock.

### 4.3 threading.Lock

- No lock inversion; no await while holding a threading lock.

### 4.4 Database transaction + advisory lock

- Advisory locks are taken on a conn that holds the transaction (or session); unlock/release in same path. No “transaction A waits for lock held by B who waits for A” pattern.

**Conclusion:** No deadlock graph identified. Freeze is not explained by deadlock.

---

## SECTION 5 — HTTP Timeout & Hanging Calls

- **httpx:** All traced calls use `AsyncClient(timeout=HTTP_TIMEOUT)` or explicit timeout (e.g. 10.0, 30.0). HTTP_TIMEOUT from config (≥3s). No default infinite timeout.
- **.json():** Used after response is received inside the same async with block; no use of .json() on a never-resolved response.
- **aiohttp server:** Health and Crypto webhook handlers are async; no blocking call in handler body.
- **VPN API hang:** If the TCP connection stalls without a proper close (e.g. half-open), the read may block until the OS TCP timeout (often minutes). httpx timeouts apply to connect and read; if the OS does not report socket activity, timeout should still fire. So **theoretical** long stall is possible but not typical.
- **Retry:** Retries are bounded (e.g. MAX_RETRIES=2); no infinite retry loop.

**Conclusion:** External API hang could cause a long wait only if timeout does not fire (e.g. OS/network edge case). It does not by itself explain a deterministic ~11 minute freeze; it could contribute if combined with pool hold (one conn held for the duration of the hang).

---

## SECTION 6 — Watchdog Failure Analysis

**Why the watchdog does not fire during the freeze:**

1. **Condition:** Watchdog calls `are_all_stale()` every 30s. It exits only when **all three** are stale: event_loop > 60s, worker > 90s, healthcheck > 90s.
2. **event_loop_heartbeat:** Runs every 5s; **does not use DB or HTTP and does not log**. So as long as the event loop is running, this task runs and keeps `last_event_loop_heartbeat` fresh.
3. **Worker heartbeat:** Updated only when a worker calls `log_worker_iteration_start()` (and thus `mark_worker_iteration()`). If all workers are blocked (e.g. on pool.acquire()), no worker completes an iteration → **worker heartbeat goes stale after 90s**.
4. **Healthcheck heartbeat:** Updated when the health **HTTP handler** runs and returns 200. The health handler **does not use the pool**; it only reads `DB_READY` and calls `mark_healthcheck_success()`. So if the event loop is running and the health endpoint is hit (e.g. by Railway or a load balancer), the healthcheck heartbeat stays fresh.
5. So in a **pool-exhaustion** scenario: event_loop stays fresh, healthcheck can stay fresh (if health is polled), worker goes stale. Then **are_all_stale() is False** (we need all three stale). So **watchdog never fires**.
6. If the **event loop itself** were fully blocked (e.g. by a synchronous blocking call), then event_loop_heartbeat would not run and after 60s event_loop would be stale. Then if worker and healthcheck were also stale, the watchdog would fire. So “process does not exit” and “watchdog does not fire” are consistent with **event loop still running** (e.g. event_loop_heartbeat and health handler still getting scheduled) while **workers and possibly logging** are blocked or starved.

**Summary:** Watchdog does not fire because at least one of the three heartbeats (event_loop or healthcheck) keeps updating. That implies the event loop is not fully dead; the “freeze” is partial (e.g. pool waiters + no worker completion, and possibly logging backpressure).

---

## SECTION 7 — Logging Freeze Analysis

- **Logging implementation:** Standard library `logging` with `StreamHandler(sys.stdout)` and `StreamHandler(sys.stderr)`. Handlers are **synchronous** — they write to stdout/stderr in the same thread.
- **Blocking:** If `sys.stdout.write()` blocks (e.g. pipe full, Railway log backpressure, slow consumer), then **any** call to `logger.*` in that thread blocks the **entire event loop** (asyncio is single-threaded). So one task blocking on logging would freeze the process until the write completes.
- **Watchdog:** When the watchdog decides to exit, it calls `logger.critical(...)`. If logging blocks at that moment, the watchdog would **block and never reach `raise SystemExit(1)`**. So process would stay alive, no exit, and no further logs.
- **Railway:** If the logging pipeline (stdout → Railway agent) backs up, writes can block. So **logging-induced full loop block is possible** and would explain: process alive, logs stop, watchdog does not exit (because it blocks on logger.critical).

**Conclusion:** Logging is synchronous; a blocking stdout/stderr write can freeze the event loop. If the freeze is triggered or prolonged by backpressure, then even the watchdog’s exit path can block on logging.

---

## SECTION 8 — Memory / Resource Exhaustion

- **background_tasks:** Appended to only in the single startup block in main(). Polling restart does **not** append; it only creates a new Bot and calls start_polling again inside the same task. So **background_tasks does not grow** over time.
- **Unbounded lists/dicts:** No unbounded growth in hot paths (rate_limits and similar have cleanup or bounded structures).
- **Unclosed sessions:** httpx clients used as `async with`; aiohttp app long-lived; no repeated session creation without close in hot path.
- **Repeated task creation:** Only at startup; no new worker tasks created in polling loop.
- **Fire-and-forget coroutines:** No `create_task` without storing or awaiting in a way that could leak; all worker tasks are in background_tasks and cancelled in finally.

**Conclusion:** Memory or task-list growth does not explain the periodic 11-minute freeze.

---

## SECTION 9 — Differential vs “Worked Before”

Recent changes that could correlate with the 11-minute freeze:

1. **Advisory lock lifecycle:** One connection held for process lifetime. That **reduces** effective pool from 15 to 14. So **more** pressure, not less. Could make a marginal pool-exhaustion scenario tip over at 600s.
2. **Polling watchdog / heartbeat:** No direct effect on pool or workers; only restarts polling task. Unlikely direct cause.
3. **Multi-signal watchdog:** Uses event_loop + worker + healthcheck. If health and event_loop stay fresh (as argued), watchdog never fires — so the “freeze” behavior (no exit) is **consistent** with this design, not caused by it.
4. **Batching / keyset pagination:** Workers (reconcile, fast_expiry, trial) use batched or keyset loops. **fast_expiry** holding one conn for the whole batch including HTTP is a pre-existing pattern; combined with 600s alignment, it increases the chance of many conns in use at once.
5. **Worker restructuring:** One conn per subscription in activation_worker; one conn per batch in auto_renewal and fast_expiry. These patterns can hold conns for several seconds and, at 600s, overlap.

**Most plausible correlation:** **Advisory lock (one fewer conn) + 600s alignment of workers + fast_expiry holding conn across HTTP** — together they make pool exhaustion at the 600s boundary more likely. The freeze “started” after these changes could be explained by the reduced effective pool and the same 600s burst.

---

## SECTION 10 — Final Root Cause Hypothesis

### 1. Most probable root cause (ranked)

**Primary: DB pool exhaustion at the 600-second boundary.**

- At T=600s, auto_renewal, reconcile, health_check, fast_expiry (10th run), and crypto watcher (20th run) all wake.
- Several of them (and possibly handlers) call `pool.acquire()`. fast_expiry holds one connection for the entire batch including VPN HTTP; auto_renewal holds one for up to 15s.
- Effective pool is 14. With 1 advisory + 2–3 workers holding conns for several seconds + health + reconcile + activation/trial/handlers, the pool can reach 14.
- Once full, the next `pool.acquire()` blocks for up to 10s. Workers and handlers that need a conn do not complete → no new worker/handler logs, bot stops responding.
- Event_loop_heartbeat (no DB) and health handler (no pool) keep running → their heartbeats stay fresh → watchdog does not fire.
- Process stays alive, logs appear to stop (no completion of work that logs), only redeploy frees the conns and restores behavior.

**Secondary: Logging (stdout/stderr) blocking under backpressure.**

- If at some point the logging pipeline (e.g. Railway) backs up, `logger.*` can block in the main thread.
- Then the entire event loop blocks, including the watchdog’s `logger.critical(...)` before `raise SystemExit(1)`. So the process never exits and no further logs appear.
- This could be the **mechanism** that turns “slow” (pool exhaustion) into “total freeze” (no logs, no watchdog exit), or a standalone cause if a burst of logs triggers backpressure.

### 2. Secondary possible cause

- **VPN/HTTP stall** (e.g. TCP half-open) while a worker holds a connection: one conn held for a long time, contributing to exhaustion or prolonged recovery. Less likely to be the only cause of a deterministic ~11 min pattern without pool pressure.

### 3. What would explain the observed behavior

| Observation | Explanation |
|-------------|-------------|
| Process alive | No crash; event loop still runs (heartbeat and/or health). |
| Logs stop | Workers/handlers blocked on pool.acquire() do not complete, so they don’t log; or logging itself blocks. |
| No watchdog exit | At least one of event_loop or healthcheck heartbeat stays fresh → are_all_stale() is False; or watchdog blocks on logger.critical. |
| No Railway crash | Process is not killed; container is healthy from outside (e.g. health still 200 if hit). |
| Manual redeploy fixes it | Restart frees all connections and clears any logging backpressure; pool starts clean. |

### 4. Would webhook fix it?

**Partially.**

- Webhook does not remove the 600s alignment of workers or the pattern of holding a conn across HTTP in fast_expiry. So **pool exhaustion at 600s can still occur** with webhook.
- Webhook can change **update** delivery (HTTP POST vs long-poll), so fewer long-lived “polling” connections and slightly different concurrency profile. It does not change worker schedules or pool size.
- So webhook alone would **not** fix the root cause (pool pressure / 600s burst). Fixes should target: (1) not holding a single conn across the whole fast_expiry batch and HTTP, (2) reducing overlap at 600s (e.g. stagger intervals), (3) optional: more pool capacity or timeouts. Logging: ensure non-blocking or fire-and-forget if needed.

---

## Confidence and Next Instrumentation Step

- **Confidence level:** **Medium–High.** Pool exhaustion at 600s fits the timing, the “no exit” (watchdog condition), and the “logs stop” (no worker/handler completion). Logging block is a plausible amplifier or alternative that fits “no exit” and “logs stop.”
- **Exact next instrumentation step to confirm:**  
  **Log pool utilization at acquire and release:** e.g. wrap or patch `pool.acquire()` / `pool.release()` (or the pool implementation) to log timestamp, current pool size (e.g. `pool.get_size()` / `pool.get_idle_size()` if available), and wait time (time from acquire start to acquire success). Run in production until the next freeze. If at ~600s you see pool size hit 14 (or max) and acquire wait times spike (e.g. ~10s), the **pool exhaustion** hypothesis is confirmed. Optionally, log when any connection is held longer than e.g. 10s (and by which caller) to confirm long-held conns (e.g. fast_expiry) at 600s.

---

**End of forensic audit. No code was modified.**
