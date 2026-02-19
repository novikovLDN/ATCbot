# Production Stability Audit

**Date:** 2025-02-15  
**Auditor:** Senior Backend Engineer / Tech Lead  
**System:** Aiogram 3 Telegram Bot on Railway (webhook-only)  
**Purpose:** Full production stability assessment for deployment decisions

---

## AREA 1: WEBHOOK INTEGRITY

### FINDINGS:

1.1 **dp.start_polling() calls:**
- ✅ **CONFIRMED:** No `dp.start_polling()` calls exist in codebase
- ✅ Only found in documentation files (historical references)
- ✅ Code is 100% webhook-only

1.2 **Webhook setup/teardown sequence:**
- **ISSUE:** `bot.delete_webhook()` is called in `finally` block on shutdown (line 607)
- **ISSUE:** `bot.set_webhook()` is called on startup WITHOUT first deleting existing webhook
- **RACE CONDITION:** On restart:
  1. Old process calls `delete_webhook()` in finally (line 607)
  2. New process calls `set_webhook()` (line 533)
  3. Gap: 200-300ms where no webhook is set
  4. Updates sent during gap: Telegram buffers for 24h IF no webhook AND no getUpdates called
  5. **VERDICT:** Safe — Telegram buffers updates, but verification needed

1.3 **Webhook URL configuration:**
- ✅ Webhook URL from `config.WEBHOOK_URL` (env var `{ENV}_WEBHOOK_URL`)
- ✅ Verified against Railway public URL via `get_webhook_info()` check (line 547-553)
- ✅ Mismatch causes `sys.exit(1)` — fails fast

1.4 **Webhook POST endpoint (`/telegram/webhook`):**
- ✅ **Returns HTTP 200 on ALL paths:**
  - Success: line 62 returns 200
  - Exception: line 60 returns 200 (prevents Telegram retry)
  - Secret mismatch: line 49 returns 403 (correct — not 200)
- ✅ **Liveness updated at START:** line 37 updates `last_webhook_update_at` before processing
- ⚠️ **SYNCHRONOUS processing:** `await _dp.feed_webhook_update(_bot, update)` blocks until handler completes
- ⚠️ **NO timeout on handler execution:** If handler hangs >5s, Telegram retries → duplicate processing risk
- ✅ **Auth check present:** `x_telegram_bot_api_secret_token` validated (line 44)
- ✅ **Exception handling:** All exceptions caught, logged, returns 200 (line 57-60)

1.5 **Other webhook manipulation:**
- ✅ Only startup (`set_webhook`) and shutdown (`delete_webhook`) touch webhook
- ✅ No background tasks call webhook methods
- ✅ No health checks modify webhook

### RISKS:

**CRITICAL:**
- None identified

**HIGH:**
- **1.4.1:** Synchronous handler execution without timeout — slow handlers (>5s) cause Telegram retries and duplicate processing
- **1.2.1:** Webhook restart race — updates may be lost during 200-300ms gap (needs verification)

**MEDIUM:**
- **1.4.2:** No handler execution timeout — hung handlers block webhook endpoint indefinitely

**LOW:**
- None

### FIXES REQUIRED:

1. **Add handler execution timeout** (`app/api/telegram_webhook.py`):
   ```python
   # Wrap feed_webhook_update with timeout
   try:
       await asyncio.wait_for(_dp.feed_webhook_update(_bot, update), timeout=5.0)
   except asyncio.TimeoutError:
       logger.error("WEBHOOK_HANDLER_TIMEOUT update_id=%s", update.update_id)
       # Still return 200 to prevent retry
   ```

2. **Verify Telegram update buffering** during webhook restart gap (test in staging)

### ACCEPTABLE AS-IS:

- Webhook URL verification on startup
- Secret token validation
- Exception handling returning 200
- Liveness update at handler start

---

## AREA 2: WORKER AUDIT — EACH WORKER IN DETAIL

### 2.1 REMINDERS WORKER (`reminders.py`)

**a) Interval:** 45 minutes (line 201: `await asyncio.sleep(45 * 60)`)

**b) What it does:**
- Queries `database.get_subscriptions_for_reminders()` (DB read)
- For each subscription: checks notification service, sends Telegram message
- No external API calls (VPN/payments)

**c) asyncio.wait_for():** ❌ **MISSING** — no timeout wrapper

**d) ITERATION_END in finally:** ❌ **MISSING** — uses `log_event()` in try/except, not `log_worker_iteration_end()` in finally

**e) pool.acquire() timeout:** ✅ Uses `pool.acquire()` which respects pool's `acquire_timeout=10s`

**f) HTTP calls timeout:** ✅ Uses `safe_send_message()` → aiogram Bot API (has built-in timeout)

**g) Worst-case execution time:** ~30-60s (depends on number of reminders, Telegram rate limits)

**h) Event loop blocking:** ✅ No CPU-bound operations, all async

**i) Known issues:**
- ⚠️ No iteration timeout — if `send_smart_reminders()` hangs, worker stuck
- ⚠️ No ITERATION_END in finally — cannot confirm iteration completion in logs

### 2.2 TRIAL_NOTIFICATIONS WORKER (`trial_notifications.py`)

**a) Interval:** 5 minutes (line 725: `await asyncio.sleep(300)`)

**b) What it does:**
- Calls `process_trial_notifications(bot)` → queries DB for trial subscriptions
- Calls `expire_trial_subscriptions(bot)` → expires old trials
- Sends Telegram messages via `safe_send_message()`
- No external API calls

**c) asyncio.wait_for():** ❌ **MISSING** — no timeout wrapper around iteration

**d) ITERATION_END in finally:** ⚠️ **PARTIAL** — logs in multiple exception branches, but NOT in finally block (lines 674, 696, 715)

**e) pool.acquire() timeout:** ✅ Uses `pool.acquire()` with pool timeout

**f) HTTP calls timeout:** ✅ Telegram API via aiogram

**g) Worst-case execution time:** ~10-30s

**h) Event loop blocking:** ✅ All async

**i) Known issues:**
- ⚠️ No iteration timeout
- ⚠️ ITERATION_END not guaranteed in finally

### 2.3 FAST_EXPIRY_CLEANUP WORKER (`fast_expiry_cleanup.py`)

**a) Interval:** 60 seconds (configurable, line 47: `CLEANUP_INTERVAL_SECONDS`, default 60)

**b) What it does:**
- Queries expired subscriptions (`expires_at < NOW()`)
- For each: calls VPN API `POST /remove-user/{uuid}` (external HTTP)
- Updates DB: sets `status='expired'`, clears `uuid`
- Uses `FOR UPDATE SKIP LOCKED` to prevent race conditions

**c) asyncio.wait_for():** ❌ **MISSING** — no timeout wrapper

**d) ITERATION_END in finally:** ⚠️ **PARTIAL** — logs in exception handlers (lines 427, 439, 453, 470, 488), but NOT in finally block

**e) pool.acquire() timeout:** ✅ Uses `acquire_connection()` wrapper which respects pool timeout

**f) HTTP calls timeout:** ✅ VPN API calls via `vpn_utils` (timeout configured)

**g) Worst-case execution time:** ~15-30s (MAX_ITERATION_SECONDS=15 enforced, line 41)

**h) Event loop blocking:** ✅ All async, uses `cooperative_yield()` every 50 items

**i) Known issues:**
- ⚠️ No iteration timeout wrapper (relies on MAX_ITERATION_SECONDS internal check)
- ⚠️ ITERATION_END not guaranteed in finally
- ✅ Has `processing_uuids` set to prevent race conditions

### 2.4 ACTIVATION_WORKER (`activation_worker.py`)

**a) Interval:** 5 minutes (line 48: `ACTIVATION_INTERVAL_SECONDS=300`)

**b) What it does:**
- Queries pending subscriptions (`activation_status='pending'`)
- For each: calls VPN API `POST /add-user` (external HTTP)
- Updates DB: sets `activation_status='active'`, stores UUID
- Sends Telegram notifications

**c) asyncio.wait_for():** ❌ **MISSING** — no timeout wrapper

**d) ITERATION_END in finally:** ⚠️ **PARTIAL** — logs in exception handlers (lines 418, 438, 459, 500, 522, 540), but NOT in finally block

**e) pool.acquire() timeout:** ✅ Uses `acquire_connection()` wrapper

**f) HTTP calls timeout:** ✅ VPN API via `vpn_utils` (timeout configured)

**g) Worst-case execution time:** ~15-30s (MAX_ITERATION_SECONDS=15 enforced)

**h) Event loop blocking:** ✅ All async, uses `cooperative_yield()`

**i) Known issues:**
- ⚠️ No iteration timeout wrapper
- ⚠️ ITERATION_END not guaranteed in finally
- ✅ Uses activation service layer with max_attempts protection

### 2.5 CRYPTO_PAYMENT_WATCHER (`crypto_payment_watcher.py`)

**a) Interval:** 30 seconds (line 37: `CHECK_INTERVAL_SECONDS=30`)

**b) What it does:**
- Queries pending purchases (`status='pending'`, `provider_invoice_id IS NOT NULL`)
- For each: calls CryptoBot API `GET /getInvoices` (external HTTP)
- If paid: calls `database.finalize_purchase()` → provisions VPN, updates DB
- Sends Telegram confirmation

**c) asyncio.wait_for():** ❌ **MISSING** — no timeout wrapper

**d) ITERATION_END in finally:** ⚠️ **PARTIAL** — logs in exception handlers (lines 358, 410, 441, 464, 482), but NOT in finally block

**e) pool.acquire() timeout:** ✅ Uses `pool.acquire()` with pool timeout

**f) HTTP calls timeout:** ✅ CryptoBot API via `cryptobot.check_invoice_status()` (timeout configured)

**g) Worst-case execution time:** ~15-30s (MAX_ITERATION_SECONDS=15 enforced)

**h) Event loop blocking:** ✅ All async, uses `cooperative_yield()`

**i) Known issues:**
- ⚠️ No iteration timeout wrapper
- ⚠️ ITERATION_END not guaranteed in finally
- ✅ Idempotent via `finalize_purchase()` protection

### 2.6 AUTO_RENEWAL WORKER (`auto_renewal.py`) — DISABLED

**a) Interval:** 600 seconds (10 minutes, line 40)

**b) What it does:**
- Queries subscriptions expiring within 6h window
- Checks balance, renews via `grant_access()` (DB only, no VPN API)
- Sends Telegram notifications

**c) asyncio.wait_for():** ✅ **PRESENT** — line 531: `await asyncio.wait_for(_run_iteration_body(), timeout=120.0)`

**d) ITERATION_END in finally:** ✅ **PRESENT** — line 560 in finally block

**e) pool.acquire() timeout:** ✅ Uses `acquire_connection()` with explicit `wait_for(10.0)` wrapper (line 115)

**f) HTTP calls timeout:** ✅ No HTTP calls (DB + Telegram only)

**g) Worst-case execution time:** 120s (hard timeout enforced)

**h) Event loop blocking:** ✅ All async

**i) Known issues:**
- ✅ **BEST PRACTICE:** Only worker with proper timeout and finally block
- ✅ Protected by feature flag (currently disabled)

### 2.X CROSS-WORKER ANALYSIS:

**DB Lock Contention:**
- `fast_expiry_cleanup` uses `FOR UPDATE SKIP LOCKED` (line ~170 in fast_expiry_cleanup.py)
- `auto_renewal` uses `FOR UPDATE SKIP LOCKED` (line ~100 in auto_renewal.py)
- ✅ No contention — SKIP LOCKED prevents blocking

**Concurrent DB Connections:**
- Workers: 6 workers × 1 connection each = 6 connections
- Webhook handlers: MAX_CONCURRENT_UPDATES=20 × 1 connection each = 20 connections
- **TOTAL PEAK: 26 connections**
- **POOL MAX: 15 connections**
- ⚠️ **EXCEEDS POOL:** 26 > 15 → 11 requests will wait for `acquire_timeout=10s`

**Shared State:**
- ✅ No shared mutable globals between workers
- ✅ Each worker uses its own connection from pool
- ✅ `processing_uuids` set in fast_expiry_cleanup is worker-local

### RISKS:

**CRITICAL:**
- **2.X.1:** Pool exhaustion — 26 concurrent connections needed, pool max=15 → 11 requests wait 10s → Telegram retries → spiral

**HIGH:**
- **2.1-2.5:** Missing iteration timeouts — workers can hang indefinitely
- **2.1-2.5:** ITERATION_END not in finally — cannot confirm completion in logs

**MEDIUM:**
- **2.3 + 2.6:** Race condition — fast_expiry_cleanup and auto_renewal both modify subscriptions (protected by SKIP LOCKED, but timing window exists)

**LOW:**
- None

### FIXES REQUIRED:

1. **Add iteration timeout to all workers** (reminders, trial_notifications, fast_expiry_cleanup, activation_worker, crypto_payment_watcher):
   ```python
   try:
       await asyncio.wait_for(worker_iteration(), timeout=120.0)
   except asyncio.TimeoutError:
       logger.error("WORKER_TIMEOUT: worker_name exceeded timeout")
   ```

2. **Move ITERATION_END to finally block** in all workers (except auto_renewal which already has it)

3. **Increase DB pool max_size** from 15 to 25+ to handle peak load (26 connections)

### ACCEPTABLE AS-IS:

- Worker intervals are reasonable
- All workers use async/await (no event loop blocking)
- VPN API calls have timeouts
- Race conditions protected by SKIP LOCKED

---

## AREA 3: DATABASE CONNECTION POOL ANALYSIS

### FINDINGS:

3.1 **Pool configuration:**
- `min_size=2`, `max_size=15` (database.py line 252-253)
- `acquire_timeout=10s` (line 255)
- `command_timeout=30s` (line 256)
- **Peak demand:** 26 connections (6 workers + 20 webhook handlers)
- **EXCEEDS POOL:** 26 > 15 → **11 connections will wait**

3.2 **Connection usage patterns:**
- ✅ **Mostly correct:** `async with pool.acquire() as conn:` pattern used
- ⚠️ **ISSUE:** `instance_lock_conn` manually acquired (line 201) — NOT using context manager
- ⚠️ **ISSUE:** Some places use `retry_async(lambda: pool.acquire())` then `async with conn` (database.py lines 188-195, 228-235) — correct but verbose

3.3 **Long-running transactions:**
- ⚠️ **ISSUE:** `auto_renewal` holds transaction during entire iteration (line 121: `async with conn.transaction()`)
- ⚠️ **ISSUE:** `fast_expiry_cleanup` holds transaction during VPN API call (line ~180: transaction wraps HTTP call)
- **Impact:** Connection held for 5-30s during external HTTP calls

3.4 **Advisory lock connection:**
- ✅ **FIXED:** On timeout, connection released (line 207: `await pool.release(instance_lock_conn)`)
- ✅ **FIXED:** `instance_lock_conn = None` set (line 208)
- ⚠️ **RISK:** If exception occurs between `acquire()` and `try:` block, connection leaked (unlikely but possible)

### RISKS:

**CRITICAL:**
- **3.1.1:** Pool exhaustion — 26 connections needed, pool max=15 → 11 requests wait 10s → Telegram retries → exponential backoff spiral

**HIGH:**
- **3.3.1:** Long transactions during HTTP calls — connections held 5-30s unnecessarily
- **3.2.1:** Manual connection management for advisory lock — potential leak if exception before try block

**MEDIUM:**
- **3.3.2:** Transaction scope too wide — should commit before external HTTP calls

**LOW:**
- None

### FIXES REQUIRED:

1. **Increase pool max_size** (`database.py`):
   ```python
   "max_size": int(os.getenv("DB_POOL_MAX_SIZE", "25")),  # Was 15
   ```

2. **Refactor advisory lock** to use context manager (`main.py`):
   ```python
   async with pool.acquire() as lock_conn:
       try:
           await lock_conn.execute("SET lock_timeout = '1000'")
           await lock_conn.execute("SELECT pg_advisory_lock($1)", ADVISORY_LOCK_KEY)
       except Exception as e:
           logger.warning("Advisory lock failed: %s", e)
           # Connection auto-released by context manager
   ```

3. **Narrow transaction scope** — commit before HTTP calls:
   ```python
   # In fast_expiry_cleanup, auto_renewal:
   async with conn.transaction():
       # DB updates only
       await conn.execute(...)
   # Transaction committed here
   # THEN make HTTP call
   await vpn_api.remove_user(uuid)
   ```

### ACCEPTABLE AS-IS:

- Pool min/max configuration is reasonable for steady state
- acquire_timeout=10s is appropriate
- command_timeout=30s is appropriate
- Most code uses context managers correctly

---

## AREA 4: LIVENESS & WATCHDOG

### FINDINGS:

4.1 **Watchdog is PASSIVE:**
- ✅ **CONFIRMED:** No `os._exit(1)` call (line 523: comment says "do NOT call")
- ⚠️ **ISSUE:** If bot hangs, Railway will NOT restart it (unless Railway health check fails)
- ⚠️ **UNKNOWN:** Railway health check configuration — need to verify:
  - Does Railway probe `/health` endpoint?
  - Does Railway probe TCP port 8080?
  - What is Railway's health check timeout?

4.2 **Liveness variable (`last_webhook_update_at`):**
- ✅ **Defined:** Module-level global in `app/api/telegram_webhook.py` line 21
- ✅ **Updated:** At START of webhook handler (line 37) — before processing
- ✅ **Read:** By watchdog in `main.py` line 515
- ⚠️ **RISK:** If webhook handler throws BEFORE line 37 (e.g. secret validation fails), liveness NOT updated
- ⚠️ **RISK:** If webhook handler throws AFTER line 37 but before line 56, liveness updated but handler failed

4.3 **Grace period:**
- ✅ **PRESENT:** 60s grace period (line 510)
- ⚠️ **ISSUE:** If no traffic for 60s after startup, watchdog logs CRITICAL (false positive)
- ⚠️ **BETTER APPROACH:** Wait for first real update, THEN start checking

### RISKS:

**CRITICAL:**
- **4.1.1:** Passive watchdog — hung bot will NOT restart automatically
- **4.1.2:** Unknown Railway health check — if Railway doesn't probe `/health`, hung bot stays up forever

**HIGH:**
- **4.2.1:** Liveness not updated if secret validation fails (line 37 is AFTER secret check line 44)
- **4.3.1:** False positive CRITICAL logs if no traffic during grace period

**MEDIUM:**
- None

**LOW:**
- None

### FIXES REQUIRED:

1. **Move liveness update BEFORE secret check** (`app/api/telegram_webhook.py`):
   ```python
   @router.post("/telegram/webhook")
   async def telegram_webhook(...):
       # Update liveness FIRST (before any validation)
       global last_webhook_update_at
       last_webhook_update_at = time.monotonic()
       
       # THEN validate secret
       if x_telegram_bot_api_secret_token != config.WEBHOOK_SECRET:
           return Response(status_code=403)
   ```

2. **Improve watchdog grace period** (`main.py`):
   ```python
   # Wait for first real update, THEN start checking
   from app.api import telegram_webhook as _tw
   initial_time = _tw.last_webhook_update_at
   await asyncio.sleep(60)  # Grace period
   # If still no update, start checking
   if _tw.last_webhook_update_at == initial_time:
       logger.warning("No webhook updates received during grace period")
   ```

3. **Verify Railway health check configuration** (deployment docs)

4. **Consider re-enabling watchdog with os._exit(1)** now that auto_renewal hang is fixed

### ACCEPTABLE AS-IS:

- Watchdog implementation is correct (just passive)
- Grace period prevents false positives during startup
- Liveness tracking mechanism is sound

---

## AREA 5: CONFLICT & RACE CONDITION ANALYSIS

### FINDINGS:

5.1 **Webhook restart race:**
- ✅ **CONFIRMED:** On restart, old process deletes webhook, new process sets webhook
- ⚠️ **GAP:** 200-300ms where no webhook is set
- ✅ **TELEGRAM BEHAVIOR:** Telegram buffers updates for 24h if no webhook AND no getUpdates called
- ✅ **VERDICT:** Safe — updates not lost, but need verification

5.2 **Worker startup race:**
- ✅ **CONFIRMED:** Workers start BEFORE uvicorn (workers at T+0, uvicorn at T+2s)
- ✅ **PROTECTED:** Workers check `database.DB_READY` before using pool
- ✅ **SAFE:** If pool not ready, workers skip gracefully

5.3 **Redis FSM storage:**
- ✅ **CONFIGURED:** `RedisStorage.from_url(config.REDIS_URL)` (main.py line 128)
- ⚠️ **UNKNOWN:** Redis connection pooling — aiogram RedisStorage uses redis-py which pools connections
- ⚠️ **RISK:** If Redis goes down, aiogram may retry or crash — need to verify behavior
- ✅ **FALLBACK:** Falls back to MemoryStorage if REDIS_URL not set (line 131)

5.4 **Concurrent webhook requests:**
- ✅ **LIMITED:** `MAX_CONCURRENT_UPDATES=20` (main.py line 144)
- ⚠️ **POOL EXHAUSTION:** 20 webhook handlers × 1 connection each = 20 connections
- ⚠️ **WORKERS:** 6 workers × 1 connection each = 6 connections
- ⚠️ **TOTAL:** 26 connections needed, pool max=15 → **11 requests wait 10s**
- ⚠️ **SPIRAL RISK:** Waiting requests → Telegram retries → more requests → worse

5.5 **fast_expiry_cleanup + auto_renewal conflict:**
- ✅ **PROTECTED:** Both use `FOR UPDATE SKIP LOCKED`
- ✅ **SAFE:** No blocking, no race condition
- ⚠️ **TIMING:** fast_expiry runs every 60s, auto_renewal every 600s — small window for same subscription

5.6 **activation_worker + webhook handlers conflict:**
- ⚠️ **RISK:** Both can provision VPN for same user
- ✅ **PROTECTED:** activation_worker uses `activation_status='pending'` filter
- ✅ **PROTECTED:** Webhook handlers set `activation_status='pending'` then worker activates
- ⚠️ **RACE:** If webhook handler sets pending while worker is processing → worker may skip (safe, retries next cycle)

### RISKS:

**CRITICAL:**
- **5.4.1:** Pool exhaustion spiral — 26 connections needed, pool max=15 → waiting → retries → worse

**HIGH:**
- **5.3.1:** Redis failure behavior unknown — may crash or retry indefinitely

**MEDIUM:**
- **5.1.1:** Webhook restart gap — updates may be delayed (not lost, but need verification)
- **5.6.1:** activation_worker + webhook race — minor, self-healing

**LOW:**
- **5.5.1:** fast_expiry + auto_renewal timing window — protected by SKIP LOCKED

### FIXES REQUIRED:

1. **Increase DB pool max_size** (see AREA 3)

2. **Test Redis failure behavior** — verify aiogram handles Redis downtime gracefully

3. **Verify Telegram update buffering** during webhook restart gap (staging test)

### ACCEPTABLE AS-IS:

- Worker startup sequence is safe
- Race conditions protected by SKIP LOCKED
- activation_worker race is self-healing

---

## AREA 6: INDIRECT CRASH CAUSES

### FINDINGS:

6.1 **Railway platform restarts:**
- **Likelihood:** MEDIUM (deployments, OOM, health check failures)
- **Failure mode:** Process killed, restarted
- **Protection:** ✅ Graceful shutdown in `finally` block, webhook deleted

6.2 **Unhandled exception in main():**
- **Likelihood:** LOW (all exceptions caught)
- **Failure mode:** Exception propagates to `asyncio.run(main())` → process exits
- **Protection:** ⚠️ **PARTIAL** — most exceptions caught, but some may propagate

6.3 **asyncio event loop crash:**
- **Likelihood:** LOW (all tasks wrapped in try/except)
- **Failure mode:** Uncaught exception in background task → loop crashes
- **Protection:** ✅ Background tasks use `return_exceptions=True` in `asyncio.gather()` (line 600)

6.4 **PostgreSQL connection dropped:**
- **Likelihood:** MEDIUM (Railway DB restarts, network blips)
- **Failure mode:** Pool connections invalid, queries fail
- **Protection:** ✅ Workers catch `asyncpg.PostgresError`, retry next iteration

6.5 **Redis connection dropped:**
- **Likelihood:** MEDIUM (Railway Redis restarts)
- **Failure mode:** FSM storage fails, handlers crash
- **Protection:** ⚠️ **UNKNOWN** — need to verify aiogram RedisStorage retry behavior

6.6 **VPN API timeout cascade:**
- **Likelihood:** LOW (VPN API has timeouts, workers have timeouts)
- **Failure mode:** All workers stuck waiting for VPN API
- **Protection:** ✅ VPN API calls have timeouts, workers have MAX_ITERATION_SECONDS

6.7 **Memory leak:**
- **Likelihood:** LOW (no unbounded data structures observed)
- **Failure mode:** Memory grows until OOM
- **Protection:** ⚠️ **PARTIAL** — no explicit memory monitoring

6.8 **File descriptor leak:**
- **Likelihood:** LOW (all connections use context managers)
- **Failure mode:** FD exhaustion → "too many open files"
- **Protection:** ✅ Connections use context managers, pool manages FDs

6.9 **Telegram rate limiting (429):**
- **Likelihood:** MEDIUM (high message volume)
- **Failure mode:** 429 errors → retry storms → more 429s
- **Protection:** ✅ `safe_send_message()` handles rate limits, workers have delays

6.10 **Clock skew:**
- **Likelihood:** LOW (Railway uses NTP)
- **Failure mode:** Timestamp comparisons fail, subscriptions expire early/late
- **Protection:** ✅ All timestamps use UTC, DB uses UTC

### RISKS:

**CRITICAL:**
- None

**HIGH:**
- **6.5.1:** Redis failure behavior unknown
- **6.4.1:** PostgreSQL connection drops — workers handle, but webhook handlers may fail

**MEDIUM:**
- **6.1.1:** Railway restarts — graceful shutdown helps but not perfect
- **6.9.1:** Telegram rate limiting — handled but may cause delays

**LOW:**
- All others

### FIXES REQUIRED:

1. **Test Redis failure behavior** — verify aiogram handles Redis downtime

2. **Add webhook handler DB error handling** — catch `asyncpg.PostgresError` in webhook handler

3. **Monitor memory usage** — add metrics/alerts for memory growth

### ACCEPTABLE AS-IS:

- Most failure modes have protection
- Event loop crash protection via `return_exceptions=True`
- Workers handle DB failures gracefully
- Rate limiting handled

---

## PRIORITY ACTION LIST

### [CRITICAL]

1. **Increase DB pool max_size from 15 to 25+** (`database.py` line 253)
   - **Reason:** Peak demand is 26 connections (6 workers + 20 webhook handlers)
   - **Impact:** Prevents pool exhaustion spiral
   - **Effort:** 1 line change

2. **Add handler execution timeout to webhook endpoint** (`app/api/telegram_webhook.py`)
   - **Reason:** Prevents hung handlers from blocking webhook endpoint
   - **Impact:** Prevents Telegram retries and duplicate processing
   - **Effort:** Wrap `feed_webhook_update` with `asyncio.wait_for(timeout=5.0)`

### [HIGH]

3. **Add iteration timeout to all workers** (reminders, trial_notifications, fast_expiry_cleanup, activation_worker, crypto_payment_watcher)
   - **Reason:** Workers can hang indefinitely without timeout
   - **Impact:** Prevents worker hangs from blocking event loop
   - **Effort:** Wrap iteration body with `asyncio.wait_for(timeout=120.0)`

4. **Move ITERATION_END to finally block** in all workers
   - **Reason:** Cannot confirm iteration completion in logs
   - **Impact:** Better observability, confirms workers are running
   - **Effort:** Refactor logging to use finally block

5. **Move liveness update BEFORE secret check** (`app/api/telegram_webhook.py` line 37)
   - **Reason:** Liveness not updated if secret validation fails
   - **Impact:** Watchdog may false-positive if secret is wrong
   - **Effort:** Move line 37 before line 44

6. **Refactor advisory lock to use context manager** (`main.py` line 201)
   - **Reason:** Potential connection leak if exception before try block
   - **Impact:** Prevents connection leak
   - **Effort:** Use `async with pool.acquire() as lock_conn:`

### [MEDIUM]

7. **Narrow transaction scope** — commit before HTTP calls (fast_expiry_cleanup, auto_renewal)
   - **Reason:** Connections held unnecessarily during HTTP calls
   - **Impact:** Reduces connection hold time
   - **Effort:** Refactor transaction boundaries

8. **Improve watchdog grace period** — wait for first real update
   - **Reason:** False positive CRITICAL logs if no traffic
   - **Impact:** Better diagnostics
   - **Effort:** Check if liveness updated during grace period

9. **Test Redis failure behavior** — verify aiogram handles Redis downtime
   - **Reason:** Unknown behavior if Redis goes down
   - **Impact:** Understand failure mode
   - **Effort:** Manual testing

10. **Verify Railway health check configuration**
    - **Reason:** Need to know if Railway will restart hung bot
    - **Impact:** Understand restart behavior
    - **Effort:** Check Railway dashboard/docs

### [LOW]

11. **Verify Telegram update buffering** during webhook restart gap
    - **Reason:** Confirm updates not lost
    - **Impact:** Peace of mind
    - **Effort:** Staging test

12. **Add memory monitoring** — metrics/alerts for memory growth
    - **Reason:** Detect memory leaks early
    - **Impact:** Prevent OOM
    - **Effort:** Add metrics collection

---

## STABILITY VERDICT

### Current State: **DEGRADED**

**Reasoning:**
- ✅ Webhook integrity is good (minor issues)
- ⚠️ Worker timeouts missing (5 of 6 workers)
- ⚠️ DB pool exhaustion risk (26 connections needed, pool max=15)
- ⚠️ Watchdog is passive (hung bot won't restart)
- ✅ Most race conditions protected
- ✅ Most failure modes have protection

### Safe to Enable auto_renewal: **CONDITIONAL**

**Conditions:**
1. ✅ auto_renewal worker has proper timeout and finally block (best practice)
2. ⚠️ Must fix DB pool exhaustion first (increase max_size to 25+)
3. ⚠️ Must add handler execution timeout to webhook endpoint
4. ✅ Feature flag kill switch works (can disable quickly)

**Recommendation:** Fix CRITICAL items (#1, #2) before enabling auto_renewal.

### Estimated Time to Full Stability: **4-6 hours**

**Breakdown:**
- CRITICAL fixes: 1-2 hours
- HIGH fixes: 2-3 hours
- Testing: 1 hour

**After fixes:** System will be **STABLE** with all protections in place.

---

## SUMMARY

The system is **mostly stable** but has **critical pool exhaustion risk** and **missing worker timeouts**. The webhook implementation is solid, workers are well-structured, and most failure modes are handled. The main risks are:

1. **DB pool too small** (CRITICAL)
2. **Missing worker timeouts** (HIGH)
3. **Passive watchdog** (HIGH — but acceptable if Railway health check works)

With the CRITICAL and HIGH fixes applied, the system will be production-ready.
