# Production Readiness Audit

**Date:** 2026-02-12  
**Scope:** ATCS Telegram Bot — Full System  
**Rule:** Analysis only. No code changes. No feature suggestions.

---

## PHASE 1 — ARCHITECTURE MAP

### 1.1 Runtime Architecture

| Component | Implementation |
|-----------|----------------|
| **Entry point** | `main.py` → `asyncio.run(main())` |
| **Dispatcher** | `aiogram.Dispatcher(storage=MemoryStorage())` |
| **Storage** | `MemoryStorage()` — in-process, no Redis |
| **Telegram update source** | **Polling only** (`dp.start_polling(bot)`) |
| **Crypto Bot** | Webhook at `POST /webhooks/cryptobot` (separate from Telegram) |
| **Background tasks** | reminder, trial_notifications, healthcheck, health_server, fast_expiry_cleanup, auto_renewal, activation_worker, crypto_payment_watcher |
| **Leader election** | None |
| **Redis** | Not used (no redis_client, update_queue, update_worker in codebase) |
| **DB pool** | asyncpg, `min_size=1`, `max_size=5` (from init_db) |

### 1.2 Conditional Branching

- **WEBHOOK_MODE**: Not present in config or main. No branching.
- **DB_READY**: Used throughout for degraded-mode guards. Workers/tasks skip if `not DB_READY`.
- **APP_ENV**: prod/stage/local — config prefix selection only.

### 1.3 Architecture Anomalies

- **Telegram vs Crypto Bot**: Telegram updates via polling; Crypto Bot payments via HTTP webhook. Separate flows, no conflict.
- **Unreachable code**: None identified.
- **Duplicated infra**: Single DB pool, single health server. No duplication.

---

## PHASE 2 — HANDLERS DEEP ANALYSIS

### 2.1 Structural Findings

| Risk Type | Finding |
|-----------|---------|
| **Blocking I/O** | Admin CSV export (lines 10341–10388): synchronous `tempfile`, `csv.writer`, file write. Blocks event loop. No `run_in_executor`. |
| **DB connection** | Handlers use `ensure_db_ready_message` / `ensure_db_ready_callback`; return early with user feedback when DB not ready. Pattern is correct. |
| **callback.answer()** | 571 usages. `handler_exception_boundary` tries `args[0].answer(error_text)` on exception. Some early-return paths may skip `callback.answer()` — not fully audited per-handler. |
| **Uncaught exceptions** | `handler_exception_boundary` catches, logs, sends error message; returns `None`. Exceptions do not propagate. |
| **Global state mutation** | None significant. `_bot_start_time` is read-only after init. |
| **FSM assumption** | Handlers use `StateFilter`, `state.clear()`. FSM is MemoryStorage — process-local. Safe for single-instance polling. |
| **TelegramBadRequest** | Handled in `safe_edit_text` (message not modified, etc.). Some handlers use raw `edit_text` with local try/except. |
| **safe_edit_text** | 103 usages. Helper guards against "message not modified" and inaccessible messages. |

### 2.2 Specific Risks

- **Long-running handlers**: Bulk reissue (lines 7767, 8017) and broadcast (line 10872) use loops with `asyncio.sleep(1–1.5)`. Admin-only, but can hold the handler for minutes. Under polling this blocks that worker; under high concurrency this could stall other updates.
- **N+1**: Not systematically audited. Suspect in referral/export flows; requires targeted review.
- **Shared mutable state**: None identified.
- **Large payloads**: CSV export iterates over full `data` in memory. Large user/subscription sets can spike memory.

### 2.3 Failure Cascades

- **One handler exception**: Caught by `handler_exception_boundary`; user gets generic error; no cascade.
- **DB error in handler**: Guarded by `ensure_db_ready_*` and exception boundary. Degraded mode messaging.
- **VPN API timeout**: Isolated in vpn_utils/service layers; handlers get error result, no crash.

---

## PHASE 3 — DATABASE LAYER

### 3.1 Pool Configuration

- **init_db**: `min_size=1`, `max_size=5`
- **get_pool** (fallback create): `min_size=1`, `max_size=10` — **inconsistency** with init_db. Normal startup uses init_db’s pool (max_size=5).

### 3.2 acquire() Usage

- Majority: `async with pool.acquire() as conn` — correct.
- `grant_access`: Manual `conn = await pool.acquire()` with `should_release_conn`; release in `finally`. Correct.
- No orphaned `acquire()` without release found.

### 3.3 Transactions

- 22 uses of `async with conn.transaction()`. No nested transactions that could deadlock; asyncpg uses savepoints for nesting.

### 3.4 Potential Leaks

- None identified. All `acquire()` paths have corresponding release/context exit.

### 3.5 Blocking I/O

- All DB access is async (asyncpg). No blocking DB calls.

### 3.6 Retry Loops

- `retry_async` used for pool creation and some `acquire()`; bounded retries. No infinite loops.

---

## PHASE 4 — REDIS LAYER

- **Redis**: Not used in current codebase.
- **Crypto Bot webhook**: No Redis; uses DB for idempotency (e.g. pending_purchase status).
- **Telegram updates**: Polling; no Redis queue.

---

## PHASE 5 — EVENT LOOP HEALTH

| Check | Result |
|-------|--------|
| `time.sleep` | None found. |
| `asyncio.sleep` | Only async sleeps. |
| Blocking CPU loops | None identified. |
| Large sync JSON | Crypto Bot webhook: `json.loads(body_bytes.decode())` — small payloads; acceptable. |
| Unbounded tasks | Workers use `while True` with sleep; bounded by cancellation. `asyncio.create_task` used for fire-and-forget; no unbounded accumulation found. |
| Unawaited coroutines | None identified. |

**Blocking risk**: Admin CSV export uses sync file I/O and can block the event loop for hundreds of ms to seconds on large datasets.

---

## PHASE 6 — FAILURE MODE ANALYSIS

| Scenario | Behavior |
|----------|----------|
| **DB temporarily down** | `DB_READY=False`; workers skip; handlers show degraded message; retry task every 30s. Recovers when DB is back. |
| **Redis down** | N/A — Redis not used. |
| **Telegram slow** | Polling blocks on long poll; handlers may queue. No explicit timeout on `start_polling`. |
| **20 concurrent users** | Pool max_size=5. Possible acquire contention; handlers can wait. No backpressure. |
| **One handler exception** | Caught by boundary; no crash; user sees error. |
| **Background worker crash** | Worker loop catches, logs, sleeps; continues. `crypto_watcher_task` is **not** cancelled/awaited in shutdown — **orphan on exit**. |
| **Crypto Bot webhook slow** | `finalize_purchase` can take seconds (DB + VPN). HTTP connection held until response. Crypto Bot may retry on timeout. |

---

## PHASE 7 — PRODUCTION READINESS SCORE

| Metric | Score (0–10) | Notes |
|--------|--------------|-------|
| **Architectural stability** | 8 | Single process, polling only, clear structure. No Redis/webhook duality. |
| **Handler reliability** | 7 | Exception boundary, DB guards, safe_edit_text. CSV export blocks; some callback.answer paths unclear. |
| **Concurrency safety** | 6 | Pool max 5; no semaphore/backpressure. Polling serializes updates per process. |
| **Event loop health** | 7 | No time.sleep; one blocking path (CSV export). |
| **Overall production readiness** | 7 | Usable for production with fixes below. |

---

## RISK CATEGORIZATION

### BLOCKERS (must fix before prod)

1. **crypto_watcher_task not in shutdown**  
   - **Location**: `main.py` lines 314–322 vs 373–446  
   - **Issue**: Task is created but never cancelled or awaited in `finally`. On shutdown, task can be orphaned; process may exit before it stops.  
   - **Impact**: Unclean shutdown; possible resource leak or incomplete work.

### HIGH RISKS

2. **DB pool size**  
   - **Location**: `database.py` init_db  
   - **Issue**: `max_size=5` may be low for 20+ concurrent handlers.  
   - **Impact**: Acquire timeouts, slow responses under load.

3. **Admin CSV export blocks event loop**  
   - **Location**: `handlers.py` ~10341  
   - **Issue**: Sync `tempfile` + `csv.writer` in async handler.  
   - **Impact**: Event loop blocked for seconds on large exports; affects all handlers.

4. **get_pool vs init_db pool size mismatch**  
   - **Location**: `database.py` init_db (max_size=5) vs get_pool (max_size=10)  
   - **Issue**: Inconsistent config; recovery path could create a different pool.  
   - **Impact**: Confusing behavior if pool is recreated via get_pool.

### MEDIUM RISKS

5. **Crypto Bot webhook holds connection during finalize_purchase**  
   - **Location**: `cryptobot_service.py` handle_webhook  
   - **Issue**: Webhook waits for DB + VPN work; can take seconds.  
   - **Impact**: Provider timeouts, retries, possible duplicate processing if not fully idempotent.

6. **Long-running admin handlers**  
   - **Location**: Bulk reissue, broadcast handlers  
   - **Issue**: Loop with sleep; handler can run for minutes.  
   - **Impact**: Under polling, blocks processing of other updates for that period.

7. **Callback.answer coverage**  
   - **Issue**: Some early-return or error paths may not call `callback.answer()`.  
   - **Impact**: Telegram loading indicators can persist; minor UX issue.

### LOW RISKS

8. **No explicit polling timeout**  
   - **Issue**: aiogram default long-poll behavior.  
   - **Impact**: Slow shutdown if Telegram API is unresponsive.

9. **MemoryStorage for FSM**  
   - **Issue**: State is in-process only.  
   - **Impact**: Lost on restart; acceptable for single-instance polling.

---

## SUMMARY

- Architecture is clear: polling-only for Telegram, separate Crypto Bot webhook.
- No Redis; no hybrid webhook/polling for Telegram.
- Main problems: orphaned `crypto_watcher_task` on shutdown (blocker), blocking CSV export, small DB pool, and Crypto Bot webhook latency.
- Fixing the blocker and high-risk items is recommended before production deployment.
