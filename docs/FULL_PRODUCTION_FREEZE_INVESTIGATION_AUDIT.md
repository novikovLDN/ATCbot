# FULL PRODUCTION FREEZE INVESTIGATION AUDIT

**Goal:** Determine exact root cause of Telegram long-poll freeze in PROD.  
**Scope:** Runtime and architecture audit; temporary instrumentation; diagnosis only (no fix).  
**Context:** PROD branch, single polling run, Telegram network watchdog (HTTP success), advisory lock, reconciliation, DB pool max_size=15, getUpdates polling_timeout=30.  
**Observed:** After ~10–15 min, bot stops responding; no new "Update handled" logs; process alive; workers and healthcheck OK; no crash, no os._exit, no exception in logs.

---

## SECTION 1 — TELEGRAM POLLING LIFECYCLE

### 1.1 Call path of `dp.start_polling`

- **Entry:** `main.py`: `start_polling()` → `await dp.start_polling(bot, allowed_updates=..., polling_timeout=POLLING_REQUEST_TIMEOUT, handle_signals=False, close_bot_session=True)`.
- **Task:** Polling runs inside `polling_task = asyncio.create_task(start_polling(), name="polling_task")`. So the coroutine that awaits long poll is the one **inside** aiogram’s `Dispatcher.start_polling()`; our `start_polling()` only awaits it once (no restart loop on PROD).
- **Where getUpdates is awaited:** Inside aiogram 3, `start_polling()` runs a loop that calls `bot.get_updates(...)` (or equivalent). The **same event loop** runs this loop; there is no separate thread. The parameter we pass as `polling_timeout=30` is the **long-poll `timeout`** sent to the Telegram API (how long the server holds the connection). It is **not** the HTTP client timeout.
- **Cancellation:** When `dp.stop_polling()` is called (e.g. in `finally` on shutdown), the polling loop should receive cancellation. If getUpdates is blocked in a socket read, cancellation can only take effect when that await yields (e.g. on timeout or data). So if the TCP layer never returns and there is no request timeout, the polling coroutine can hang until the process exits.

### 1.2 getUpdates and timeouts

- **polling_timeout (30s):** This is the **Telegram long-poll parameter** `timeout`. The server holds the request open for up to 30 seconds and then returns (with updates or empty list). It does **not** set the HTTP/client timeout.
- **Session request timeout (aiogram):** `AiohttpSession.make_request()` uses `session.post(..., timeout=self.timeout if timeout is None else timeout)`. `BaseSession` default is **60 seconds**. So each Telegram API request (including getUpdates) has a **total HTTP timeout of 60s** unless the Bot was created with a custom session with a different timeout. Our code uses `Bot(token=config.BOT_TOKEN)` — no custom session, so **60s** applies.
- **If getUpdates hangs at TCP layer:** If the TCP connection stops receiving data (e.g. NAT/firewall drop, server not closing connection), the `session.post()` call will wait until:
  - **Either** the 60s timeout fires → `asyncio.TimeoutError` → aiogram raises `TelegramNetworkError` → polling loop typically catches and can retry or exit.
  - **Or** the connection is closed or data arrives.
  So in theory, a pure TCP hang should surface as an exception after 60s. If no exception is seen, either: (1) the hang is elsewhere (dispatcher/handlers/event loop), or (2) something else is still succeeding (e.g. another make_request from handlers/healthcheck), or (3) the deployed binary uses a different/session timeout (e.g. no timeout).
- **Keepalive:** aiohttp/ClientSession does not enable TCP keepalive by default in a way that would quickly detect dead connections; 60s is the main safety.

### 1.3 Retry and control return

- **Retry inside aiogram polling:** The library’s polling loop typically catches `TelegramNetworkError` and continues (next getUpdates). So after a 60s timeout we’d expect a log/retry, not silent freeze.
- **If getUpdates hangs forever:** With default 60s timeout, control **should** return after 60s with `TelegramNetworkError`. If the process stays alive with **no** exception and **no** os._exit, then either the timeout is not applied (e.g. custom session), or the freeze is **not** in the getUpdates HTTP call (e.g. dispatcher or handler path).

### 1.4 Instrumentation (temporary)

Add logging:

- **Before each getUpdates call:** log timestamp (monotonic), next `offset` value.
- **After each getUpdates response:** log timestamp, elapsed time, `offset` used, number of updates in response, max `update_id` if any.
- **Purpose:** Distinguish “getUpdates never returns” (no “after” log) from “getUpdates returns but no handler runs” (after log present, no “Update handled”).

---

## SECTION 2 — EVENT LOOP LIVENESS

### 2.1 Synchronous / blocking risk

- **Handlers:** No `time.sleep` or synchronous `requests` in handler paths; HTTP is aiohttp/httpx async. Middleware chain: `update_timestamp_middleware` → `ConcurrencyLimiterMiddleware` (semaphore) → `TelegramErrorBoundaryMiddleware` → routers.
- **Heavy CPU:** No long synchronous CPU loops identified in handlers; heavy work is in workers with cooperative yield and caps.
- **Blocking I/O:** No blocking file I/O in hot paths.
- **Risk:** If many handlers are waiting on the **same** resource (e.g. DB pool acquire), the event loop is still ticking; only those tasks are blocked. So “event loop stall” would require something that blocks the **thread** (e.g. sync call in a critical path). Not found in handler entry points.

### 2.2 Event loop monitor (temporary)

- Every **2 seconds** log monotonic time delta since last tick.
- If delta **> 5s**, log **CRITICAL** “event_loop_stall_detected” with delta.
- **Interpretation:** If after freeze we see no stall >5s, the loop is running; freeze is likely in a coroutine (e.g. getUpdates or dispatcher/handler queue). If we see repeated stalls >5s, something is blocking the loop.

### 2.3 Dispatcher behaviour

- Dispatcher processes updates by scheduling handler tasks (or running them in the observer chain). If getUpdates keeps returning updates and the loop is not stalled, dispatcher should keep scheduling. If “Update handled” logs stop while “getUpdates after” logs continue with non-empty updates, the bottleneck is in **dispatcher/handler execution** (e.g. semaphore full, or handlers stuck on DB).

---

## SECTION 3 — DB POOL & RESOURCE STARVATION

### 3.1 Pool configuration (from code)

- **max_size=15**, min_size=2 (env: DB_POOL_MAX_SIZE, DB_POOL_MIN_SIZE).
- **acquire timeout:** 10s (DB_POOL_ACQUIRE_TIMEOUT).
- **command_timeout:** 30s (DB_POOL_COMMAND_TIMEOUT).
- **Advisory lock:** One connection held for process lifetime (`instance_lock_conn = await pool.acquire()`); that connection is **not** returned to the pool until shutdown. So effectively **14** connections available for handlers and workers.

### 3.2 Exhaustion and leaks

- **Leaks:** All normal paths use `async with pool.acquire()` or explicit release. One known long-lived hold: advisory lock (by design). Workers that hold conn across HTTP (e.g. crypto_payment_watcher, trial_notifications) can hold a conn for the duration of that iteration.
- **Exhaustion:** With 14 usable conns, many concurrent handlers (each doing DB) + workers (reconciliation, activation, healthcheck, etc.) can drive pool to 15. New acquire() calls then wait up to 10s; if they all wait, handler tasks block and “Update handled” can stop while getUpdates might still return (updates queued behind semaphore or stuck on acquire).

### 3.3 Instrumentation (temporary)

- Log periodically (e.g. every 30s): pool size, **active connections** (e.g. `pool.get_size() - pool.get_idle_size()` or equivalent), **waiting** count if exposed.
- Log around **acquire:** at start of acquire (or in a wrapper), on success, and on release (with label/caller if possible).
- **Purpose:** If at freeze time we see pool active=15 and many waiters, classify toward **C) DB pool starvation**. If pool has free conns, starvation is less likely.

---

## SECTION 4 — TELEGRAM OFFSET / UPDATE FLOW

### 4.1 What to log

- **Last update_id** received in each getUpdates response (max of the batch).
- **Next offset** passed to the **next** getUpdates call (should be max(update_id) + 1).
- **Behaviour:** Offset must increase over time. If offset gets stuck (same value repeated) or goes backward, that indicates a bug. Duplication can occur if offset is not advanced correctly.

### 4.2 Webhook and second instance

- **Webhook:** `start_polling()` calls `await bot.delete_webhook(drop_pending_updates=True)` before polling. So webhook should be deleted. If webhook were still set, Telegram would send updates there and getUpdates would return empty — consistent with “no new Update handled” only if we still get empty getUpdates responses.
- **Second instance:** If another process used the same token for getUpdates, we’d expect `TelegramConflictError` in logs. User reports no exception → no evidence of conflict in logs; cannot rule out a second instance that doesn’t run getUpdates (e.g. only send_message).

---

## SECTION 5 — TCP FREEZE DETECTION

### 5.1 Instrumentation

- **Wrap `bot.session.make_request`:**
  - Log **method name** (e.g. `getUpdates`), **monotonic time**, “request_start”.
  - On return (success or exception): log “request_end”, **duration**, **exception type** if any.
- **Interpretation:**
  - If for a **getUpdates** call we see “request_start” but **never** “request_end” (and no exception logged), that indicates **TCP freeze** (or a hang in make_request that never times out). After 60s we’d normally expect timeout and “request_end” with exception.
  - If we see “request_end” for getUpdates every ~30s (or after timeout), the freeze is **not** in the getUpdates HTTP call.

---

## SECTION 6 — THREAD SAFETY / TASK LEAKS

### 6.1 Task count and polling task state

- Every **30s** log `len(asyncio.all_tasks())`.
- For the **polling task** (the one running `start_polling()` → `dp.start_polling()`): log whether it **exists**, and whether it is **pending** / **done** / **cancelled** (e.g. by keeping a reference to `polling_task` and inspecting `polling_task.done()`, `polling_task.cancelled()`).
- **Interpretation:** If task count grows unbounded, possible task leak. If the polling task is **done** or **cancelled** while the process is still up and we expect it to be running, that explains “no new updates” (polling has stopped).

---

## ROOT CAUSE CLASSIFICATION

Use the following **only after** collecting logs with the instrumentation above (or equivalent).

| Code | Hypothesis | Evidence to look for |
|------|------------|----------------------|
| **A) TCP long-poll freeze** | getUpdates HTTP call never returns (TCP/network hang; or timeout not applied). | make_request: getUpdates “request_start” logged, no “request_end” for that call; no TelegramNetworkError. Optionally: event loop monitor shows no stall. |
| **B) Dispatcher deadlock** | Dispatcher stops feeding or processing updates (internal lock/queue). | getUpdates “after” logs show responses with updates; “Update handled” stops; event loop not stalled; polling task still pending. |
| **C) DB pool starvation** | Handlers (or critical path) block on pool.acquire(); updates pile up or semaphore full. | Pool active=15 (or high), acquire wait logs; getUpdates may still return; “Update handled” stops or greatly reduced; event loop not stalled. |
| **D) Offset logic bug** | Offset stuck or wrong; duplicate or missing updates; Telegram side effect. | Offset not increasing; or duplicate update_id in logs; or second process (TelegramConflictError). |
| **E) Event loop starvation** | One or more coroutines block the loop (e.g. sync work, rare bug). | Event loop monitor shows stall >5s repeatedly; getUpdates “after” and “Update handled” both stop. |
| **F) Unknown** | Not enough evidence to assign A–E. | Inconclusive logs; or mixed signals. |

---

## EVIDENCE SUMMARY (TO FILL AFTER RUN)

- **From Section 1:** getUpdates before/after logs; elapsed time; offset.  
- **From Section 2:** Event loop 2s tick; any stall >5s.  
- **From Section 3:** Pool size, active, waiters; acquire/release logs.  
- **From Section 5:** make_request start/end per method; getUpdates duration and exceptions.  
- **From Section 6:** all_tasks() count; polling_task state (pending/done/cancelled).

---

## TECHNICAL EXPLANATION (FREEZE MECHANISM)

**To be written after classification:**

- **If A (TCP freeze):** The getUpdates HTTP request is blocked in the socket layer (or in aiohttp) and never completes. Default 60s timeout should raise; if it doesn’t, either timeout is disabled/overridden or the hang is in a layer that doesn’t respect it. No successful make_request → telegram_last_success_monotonic stops updating → after TELEGRAM_LIVENESS_TIMEOUT (180s) the network watchdog would call os._exit(1). So if watchdog is deployed and never fires, either getUpdates is **not** hanging (hypothesis B/C/D/E) or the timeout is longer than 180s.
- **If B (Dispatcher):** Something in the dispatcher path (middleware, observer, or internal state) stops dispatching or completing handler execution, while getUpdates continues to return updates. Process and loop stay alive; workers and healthcheck unaffected.
- **If C (DB pool):** Many tasks hold or wait for DB connections; handler tasks block on acquire; semaphore (MAX_CONCURRENT_UPDATES) may be full with waiting handlers; “Update handled” stops or slows sharply while getUpdates may keep returning.
- **If D (Offset):** Incorrect offset causes Telegram to resend or skip updates; can look like “no new updates” or duplicate handling; conflict error would point to second consumer.
- **If E (Loop starvation):** A coroutine blocks the event loop (e.g. sync call); getUpdates and all other tasks stop making progress until that call returns.

---

## INSTRUMENTATION IMPLEMENTATION

Temporary instrumentation is implemented in **main.py** and gated by:

- **Env:** `FREEZE_AUDIT_INSTRUMENTATION=1` (or `true` / `yes`).

**What is added when enabled:**

1. **make_request wrapper (Section 5 + 1 + 4):**
   - Logs `FREEZE_AUDIT make_request_start method=<name> offset=<offset> t0=<monotonic>` before each Telegram API call.
   - Logs `FREEZE_AUDIT make_request_end method=... elapsed_s=... count=... max_update_id=...` on success (for getUpdates, count and max_update_id are set).
   - Logs `FREEZE_AUDIT make_request_exception method=... elapsed_s=... exc=...` on exception.
   - If getUpdates has “start” but no “end” and no “exception” for that call → TCP freeze candidate.

2. **Event loop monitor (Section 2):**
   - Every 2s: `FREEZE_AUDIT event_loop_tick delta_s=...`
   - If delta > 5s: `FREEZE_AUDIT event_loop_stall_detected delta_s=...` (CRITICAL).

3. **Pool and tasks monitor (Sections 3, 6):**
   - Every 30s: `FREEZE_AUDIT tasks count=... polling_task_done=... polling_task_cancelled=...`
   - If DB_READY and pool available: `FREEZE_AUDIT pool size=... idle=... active=...` (when asyncpg exposes get_size/get_idle_size).

**Usage:** Set `FREEZE_AUDIT_INSTRUMENTATION=1` in PROD, reproduce the freeze, collect logs, then fill the evidence summary above to assign the root cause (A–F). No fix is proposed in this audit.

---

## FINAL OUTPUT (TO FILL AFTER EVIDENCE)

1. **Root cause classification:** [ A | B | C | D | E | F ]
   - **A)** TCP long-poll freeze  
   - **B)** Dispatcher deadlock  
   - **C)** DB pool starvation affecting handlers  
   - **D)** Offset logic bug  
   - **E)** Event loop starvation  
   - **F)** Unknown  

2. **Evidence from logs:** (paste or summarize log lines that support the classification)

3. **Technical explanation of freeze mechanism:** (one short paragraph)

4. **No fix proposed** — diagnosis only.
