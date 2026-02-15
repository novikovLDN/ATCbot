# FULL SYSTEM ARCHITECTURE AUDIT — STAGE vs PROD

**Date:** 2025-02-15  
**Goal:** Determine why STAGE does not freeze while PROD freezes every ~11 minutes.  
**Scope:** Runtime architecture, event loop, workers, DB pool, Xray/HTTP, Telegram layer, financial logic; structural diff stage vs prod.  
**No code changes:** Analysis and report only.

---

## SECTION 1 — RUNTIME ARCHITECTURE

### 1.1 STAGE (branch: stage) — Checklist

**1. Identify:**

| Item | STAGE |
|------|--------|
| **Asyncio tasks created at startup** | **Up to 11** (reminder, trial_notifications, healthcheck, health_server, [db_retry], fast_cleanup, auto_renewal, activation_worker, xray_sync?, crypto_watcher). Polling is **not** a separate task — it runs in the main coroutine inside `while True`. |
| **Exact list of background_tasks** | reminder_task, trial_notifications_task, healthcheck_task, health_server_task, [db_retry_task if !DB_READY], fast_cleanup_task, auto_renewal_task, activation_worker_task, [xray_sync_task if enabled], crypto_watcher_task. **No** reconciliation_task, **no** heartbeat/watchdog/updater tasks. |
| **Polling has restart loop** | **YES** — `while True:` around `dp.start_polling()`; on exception (except CancelledError, TelegramConflictError) → `await asyncio.sleep(5)` → `continue`. |
| **Watchdog uses os._exit** | **NO** — no watchdog on STAGE. |
| **Polling coroutine has while True** | **YES** — the **outer** loop in main() is `while True` (polling restart loop). The polling coroutine itself is the single `await dp.start_polling(...)` call. |
| **Heartbeat updater exists** | **NO**. |
| **Silence-based watchdog exists** | **NO**. |

**2. Confirm:**

| Item | STAGE |
|------|--------|
| **Exactly one dp.start_polling call** | One call **per loop iteration** (inside `while True`). So there is exactly one call at a time; the loop can invoke it again after an exception. |
| **No overlapping polling_task restarts** | **NOT CONFIRMED** — on any exception the loop starts a new `dp.start_polling()` after 5s; the previous getUpdates may still be in flight → overlapping getUpdates possible. |
| **No task.cancel("polling_task")** | **CONFIRMED** — there is no named "polling_task"; polling runs in the main flow. No cancel by name. |

**3. Verify:**

| Item | STAGE |
|------|--------|
| **handle_signals=False in polling** | **YES** — `handle_signals=False` passed to `dp.start_polling(...)`. |
| **close_bot_session=True** | **NO** — not passed (default is True in aiogram 3; code does not set it explicitly). |
| **No nested polling creation** | **YES** — only one `dp.start_polling` call site; no nested creation. |

### 1.2 PROD (branch: main)

**Asyncio tasks at startup (order):**

- reminder_task, trial_notifications_task, healthcheck_task, health_server_task
- db_retry_task (if not DB_READY), fast_cleanup, auto_renewal, activation_worker, xray_sync, **reconciliation_task** (if RECONCILIATION_AVAILABLE and XRAY_RECONCILIATION_ENABLED), crypto_watcher
- **Then:** heartbeat_task (event_loop_heartbeat), watchdog_task (polling_watchdog), polling_watchdog_task (polling_heartbeat_watchdog), **polling_task** (start_polling) + **updater_task** (polling_heartbeat_updater, appended inside start_polling)

**Polling on PROD:**

- **Restart loop:** NO — single run `await dp.start_polling(...)` inside `start_polling()`; no `while True` around polling.
- **Watchdog using os._exit(1):** YES — `polling_heartbeat_watchdog()` when `silence > POLLING_DEAD_TIMEOUT` (120s).
- **Silence-based watchdog:** YES — liveness tick from **polling_heartbeat_updater** (every 5s). So PROD detects “no tick for 120s” → os._exit(1). Tick is updated by **event loop progress** (updater runs every 5s), not by Telegram HTTP.
- **Heartbeat updater:** YES — `polling_heartbeat_updater` runs every 5s and updates `last_polling_loop_tick_monotonic`.
- **Exactly one dp.start_polling call:** YES (single call in start_polling).
- **Overlapping restarts:** No in-process restart; only process exit.
- **task.cancel("polling_task"):** No.
- **handle_signals:** False. **close_bot_session:** True.
- **Advisory lock:** PROD holds PostgreSQL advisory lock (one conn); released in finally.

### 1.3 Polling architecture diagram (textual)

**STAGE:**

```
main()
  ├── background_tasks: [reminder, trial, healthcheck, health_server, (db_retry), fast_cleanup, auto_renewal, activation, xray_sync?, crypto]
  └── while True:
        try:
          delete_webhook()
          await dp.start_polling(bot, polling_timeout=30, handle_signals=False)  # no close_bot_session
        except CancelledError: break
        except TelegramConflictError: raise SystemExit(1)
        except Exception: log; await asyncio.sleep(5); continue  # RESTART LOOP
```

**PROD:**

```
main()
  ├── advisory_lock (pool.acquire + pg_advisory_lock)
  ├── background_tasks: [reminder, trial, healthcheck, health_server, (db_retry), fast_cleanup, auto_renewal, activation, xray_sync?, reconciliation?, crypto]
  ├── heartbeat_task (event_loop_heartbeat every 5s)
  ├── watchdog_task (polling_watchdog every 30s, multi-signal: event_loop + worker + healthcheck)
  ├── polling_watchdog_task (every 10s: if silence > 120s → os._exit(1))
  ├── polling_task = start_polling()
  │     ├── updater_task (polling_heartbeat_updater every 5s → last_polling_loop_tick_monotonic)
  │     ├── delete_webhook()
  │     └── await dp.start_polling(bot, polling_timeout=POLLING_REQUEST_TIMEOUT, handle_signals=False, close_bot_session=True)
  └── await polling_task  → finally: cancel all, advisory unlock, close pool/session
```

### 1.4 Lifecycle of polling coroutine

- **STAGE:** Polling runs inside `while True`. If `start_polling()` returns (normal) or raises (e.g. timeout/network), the loop catches Exception, sleeps 5s, and calls `dp.start_polling()` again. So lifecycle = repeated start → run → exception → sleep → start. If getUpdates **hangs without raising** (TCP freeze), the coroutine never returns and no exception is raised → **no restart**, process appears frozen from the outside.
- **PROD:** Polling runs once in `start_polling()`. When it returns or raises, the try block exits and `finally` runs (shutdown). No in-process restart. If getUpdates hangs, the **polling_heartbeat_updater** still runs every 5s (event loop is not blocked by one awaiting coroutine), so `last_polling_loop_tick_monotonic` keeps updating and **polling_heartbeat_watchdog does NOT fire**. So on PROD, a pure TCP freeze of getUpdates also does **not** trigger os._exit(1). The only way PROD exits is: (1) multi-signal watchdog (event_loop + worker + healthcheck all stale), or (2) actual exception/return from start_polling.

---

## SECTION 2 — EVENT LOOP SAFETY

**Scan (blocking / missing await / long loops):**

- **time.sleep:** Not used in bot/workers (only asyncio.sleep).
- **requests library:** Not used; all HTTP via aiohttp/httpx async.
- **Missing await:** Not systematically scanned; no obvious sync HTTP in hot paths.
- **Long synchronous CPU loops:** Workers use cooperative_yield and MAX_ITERATION_SECONDS caps.
- **Infinite while True without await:** All worker loops contain `await asyncio.sleep(...)` before or within the loop; polling loop on stage has `await asyncio.sleep(5)` on exception.

**Blocking risk:** None identified (no sync time.sleep or requests in worker/polling paths).

**Loop without await:** All `while True` loops in main.py and workers either await sleep or await a long-running call (e.g. start_polling). Safe.

**Potentially starving coroutine:** On PROD, advisory lock holds one connection for process lifetime. If pool is exhausted, workers waiting on pool.acquire() could delay; healthcheck also uses pool. If healthcheck and workers all block on pool, multi-signal watchdog could see “all stale” and raise SystemExit(1).

---

## SECTION 3 — WORKER ARCHITECTURE

(Full detail in WORKERS_AUDIT_REPORT.md.)

**Per-worker audit (STAGE runs same worker code as PROD except no reconcile):**

| Worker | Sleep interval | Holds DB across HTTP? | Parallel DB+HTTP? | acquire_connection? | Catches exceptions? | Warm-up mode? | Duration vs interval | Overlap next iteration? | Verdict |
|--------|----------------|------------------------|-------------------|---------------------|---------------------|---------------|----------------------|--------------------------|--------|
| activation_worker | 300s | No | No | Yes (pool_monitor) | Yes | Yes (_recovery_warmup_iterations) | MAX_ITERATION_SECONDS=15; << 300s | No | **SAFE** |
| auto_renewal | 600s | No (Phase B after commit) | No | Yes | Yes | No | 15s cap, batch 100 | No | **SAFE** |
| fast_expiry_cleanup | 60s | No | No | Yes | Yes | Yes | 15s cap, batch 100 | No | **SAFE** |
| crypto_payment_watcher | 30s | **Yes** (conn for whole loop) | Yes (Crypto API + finalize) | No (pool.acquire) | Yes | Yes | 15s cap, LIMIT 100 | Possible if finalize slow | **CRITICAL** |
| trial_notifications | 300s | **Yes** (during send/remove_vless) | Yes | No (pool.acquire) | Yes | No | No hard cap | Medium | **CRITICAL** |
| reminders | 45 min | No | No | No (get_pool in get_subscriptions) | Yes | No | Unbounded fetch | High at scale | **RISK** |
| reconcile_xray_state | 600s (PROD only) | No | No | Yes | Yes | No | 20s timeout, batch 100 | No | **SAFE** |

**Confirmations:**

- **No worker blocks event loop:** All use async I/O and `await asyncio.sleep(...)`; no sync blocking.
- **No unbounded parallelism:** Handlers limited by MAX_CONCURRENT_UPDATES; workers are single-task each; batch limits in place (except reminders fetch).
- **No recursive task spawning:** Only db_retry_task spawns new tasks on DB recovery (reminder, fast_cleanup, auto_renewal, activation_worker, xray_sync); no recursion.

---

## SECTION 4 — DB POOL BEHAVIOR

- **Pool size:** max_size=15 (DB_POOL_MAX_SIZE), min_size=2.
- **acquire_timeout:** timeout=10 (DB_POOL_ACQUIRE_TIMEOUT).
- **command_timeout:** 30 (DB_POOL_COMMAND_TIMEOUT).
- **Connections released before HTTP:** Violations in crypto_payment_watcher, trial_notifications, finalize_purchase Phase 1 (see WORKERS_AUDIT_REPORT).
- **Transaction spans HTTP:** Yes in those paths (critical).
- **Advisory lock blocks pool:** PROD holds one conn for advisory lock; that conn is not in the general pool until released in finally.

**Scenario (50 concurrent users + all workers):** With crypto and trial holding conns during HTTP, plus reconciliation and others acquiring, pool can reach 15. Starvation possible under load. STAGE has one fewer long-running task (no reconciliation), so slightly less pressure.

---

## SECTION 5 — XRAY / HTTP LAYER

- **HTTP timeout:** vpn_utils uses `HTTP_TIMEOUT = max(config.XRAY_API_TIMEOUT, 3.0)` (default 5s).
- **Retry:** retry_async with MAX_RETRIES=2, retry on httpx.HTTPError, TimeoutException, ConnectionError, OSError.
- **httpx client:** New `httpx.AsyncClient(timeout=HTTP_TIMEOUT)` per request (no long-lived client).
- **Connection pooling:** Per-request client; no explicit reuse.
- **HTTP call without timeout:** All vpn_utils calls use HTTP_TIMEOUT.
- **404 fallback to add_user:** Not required; remove-user is idempotent.
- **Try/except:** VPN calls wrapped in try/except and retry_async.

**Could HTTP stall freeze loop?** A single stalled httpx call would block only that coroutine; event loop would still run others. So one stalled Xray call does not freeze the whole process. Pool starvation (many waiters) could make the system appear stuck.

---

## SECTION 6 — TELEGRAM LAYER

- **polling_timeout:** STAGE hardcodes 30; PROD uses POLLING_REQUEST_TIMEOUT (30).
- **Request timeout:** Matches 30s for getUpdates long poll.
- **Session:** Shared bot.session.
- **Overlapping getUpdates:** On STAGE, if an exception triggers restart while previous getUpdates is still in flight, the next start_polling() can start a new getUpdates → **TelegramConflictError**. On PROD, no in-process restart, so no overlapping getUpdates from restart logic.
- **Conflict handling:** STAGE raises SystemExit(1) on TelegramConflictError; PROD does not have a restart loop so conflict is less likely from this code path.

---

## SECTION 7 — FINANCIAL LOGIC SAFETY

- **finalize_purchase (incl. balance top-up):** Single transaction: pending→paid, payment INSERT, then either (a) balance top-up path: increase_balance + payment row + referral in same transaction, or (b) subscription path: grant_access + payment update. Idempotent via `status='pending'` (UPDATE ... WHERE status='pending'; second caller gets UPDATE 0 → ValueError). **No double activation** from this path.
- **grant_access:** Renewal path extends expires_at on existing subscription without creating new UUID; new issuance creates UUID. **UUID stable during renewal.**
- **Referral logic:** process_referral_reward called inside finalize_purchase transaction; idempotency by purchase_id / referral rules. **No duplicate payment processing** (finalize is single transaction; idempotent).
- **UUID regeneration:** Only when new issuance (no active or expired); renewal does not regenerate. **UUID stable during renewal.**
- **Renewal extension:** auto_renewal uses grant_access with source='auto_renew'; same conn transaction for balance decrease + grant_access + payment INSERT. **No partial renew.**
- **update_user fallback logic:** Not audited in detail; no change in this audit.
- **Race activation vs reconcile:** Reconcile skips status='active' and grace window (RECONCILE_GRACE_SECONDS); activation sets active after add_vless_user. **No race that removes active UUID.**

---

## SECTION 8 — STRUCTURAL DIFF (STAGE vs PROD)

**Focus: polling, watchdog, heartbeat, restart, worker timing, DB pool, XRAY_SYNC.**

| Aspect | PROD (main) | STAGE (stage) |
|--------|-------------|----------------|
| **Instance guard** | PostgreSQL advisory lock (one conn) | File lock (/tmp/atlas_bot.lock) |
| **reconcile_xray_state** | Imported and task started if enabled | Not imported; no reconciliation task |
| **Update timestamp middleware** | Yes (last_update_timestamp) | No |
| **Polling** | Single run: start_polling() → one dp.start_polling, no while True | while True: try start_polling except sleep(5) continue |
| **Polling params** | close_bot_session=True, POLLING_REQUEST_TIMEOUT | close_bot_session not set, polling_timeout=30 |
| **Heartbeat / watchdog** | event_loop_heartbeat, polling_watchdog (multi-signal), polling_heartbeat_watchdog (os._exit(1) at 120s), polling_heartbeat_updater (tick every 5s) | None |
| **Restart logic** | No in-process restart; only finally shutdown | Yes: any exception (except Cancelled/Conflict) → sleep 5 → next iteration |
| **task.cancel(polling_task)** | No | No |
| **Worker timing** | Same workers + reconciliation; jitter in health/workers on main | Same intervals; no reconciliation |
| **DB pool config** | Same (pool config in database.py, shared) | Same |
| **XRAY_SYNC** | Same optional task | Same optional task |
| **Shutdown** | Advisory unlock, then pool release, then close_pool | No advisory; close_pool; remove lock file |

**Exact architectural difference:** PROD has (1) advisory lock, (2) reconciliation task, (3) no polling restart loop (single run + fail-fast watchdogs), (4) event-loop + worker + healthcheck multi-signal watchdog and liveness-based polling watchdog (os._exit at 120s). STAGE has (1) file lock, (2) no reconciliation, (3) **while True polling restart** on any exception with 5s sleep, (4) no watchdog, no heartbeat.

---

## SECTION 9 — FREEZE RISK SCORE (STAGE)

- **Event loop stability (0–10):** 8 — No blocking sync calls; all loops have await. Risk: one runaway coroutine could theoretically starve others (not observed).
- **DB stability (0–10):** 6 — Pool pressure from crypto/trial holding conn across HTTP; no advisory lock.
- **Worker safety (0–10):** 6 — Same critical issues (conn across HTTP) as PROD; no reconciliation.
- **Telegram layer safety (0–10):** 5 — Restart loop can cause TelegramConflictError if exception coincides with in-flight getUpdates; no network/watchdog to recover from TCP freeze.

**Overall STAGE freeze risk (conceptual):** Lower **observed** freeze rate because (1) no multi-signal watchdog that can trigger exit when workers/healthcheck go stale, (2) no liveness watchdog that could exit on timing quirks, (3) fewer tasks (no reconciliation). So STAGE “does not freeze” in the sense that the process is less likely to **exit**; it can still **hang** (stuck getUpdates, no exception) with no watchdog to kill it.

---

## FINAL OUTPUT

### 1. Executive summary

- **STAGE** uses a **while True** polling restart loop (sleep 5s on any exception), file lock, and **no** watchdogs or heartbeats. It does **not** use PostgreSQL advisory lock or reconciliation.
- **PROD** uses a **single** polling run, PostgreSQL advisory lock, reconciliation task, **event_loop_heartbeat**, **multi-signal watchdog** (event_loop + worker + healthcheck), and **polling liveness watchdog** (120s silence → os._exit(1)) driven by a **heartbeat updater** (tick every 5s).
- PROD “freeze every ~11 min” is likely either: (a) **multi-signal watchdog** firing when event_loop, worker, and healthcheck heartbeats all go stale (e.g. pool starvation or long blocking), or (b) a real event-loop or process freeze that the liveness watchdog does not fix (because the updater keeps ticking while getUpdates is stuck). STAGE does not have these watchdogs, so it does not **exit** in those situations and can appear “not freezing” (process still up even if bot is unresponsive).

### 2. Root cause hypothesis (PROD freezes, STAGE does not)

- **Hypothesis A:** On PROD, **multi-signal watchdog** (are_all_stale) triggers after a period (e.g. when worker and/or healthcheck heartbeats are not updated). That can happen under pool starvation (workers block on acquire) or if healthcheck is slow. So PROD **exits** periodically (~11 min if stale threshold is in that range), which is perceived as “freeze” (bot down).
- **Hypothesis B:** On PROD, **polling_heartbeat_watchdog** (120s) does **not** fire on a stuck getUpdates, because the **polling_heartbeat_updater** runs every 5s and only needs the event loop to be running. So a TCP freeze of getUpdates alone does not cause exit. The ~11 min pattern then points to multi-signal or another mechanism (e.g. external kill, OOM, platform restart).
- **Hypothesis C:** STAGE “does not freeze” because it has **no** watchdog-induced exits. If getUpdates stalls, STAGE process stays alive but unresponsive; operators may not see it as “freeze” if they only measure process uptime. So the difference is **exit behavior**, not necessarily **liveness**.

### 3. Exact architectural difference

- **Polling:** STAGE = `while True` + restart on exception (5s sleep). PROD = single `start_polling()` with no restart; watchdogs and updater tasks run alongside.
- **Watchdog:** STAGE = none. PROD = (1) multi-signal (event_loop + worker + healthcheck) → SystemExit(1), (2) polling liveness (silence > 120s) → os._exit(1).
- **Heartbeat:** STAGE = none. PROD = event_loop_heartbeat every 5s, polling_heartbeat_updater every 5s.
- **Instance guard:** STAGE = file lock. PROD = advisory lock (one DB conn).
- **Reconciliation:** STAGE = no. PROD = yes (if enabled).

### 4. Should STAGE architecture replace PROD?

**No.** STAGE’s restart loop creates **TelegramConflictError** risk (overlapping getUpdates). STAGE has no way to recover from a **stuck getUpdates** (no watchdog). Recommended direction is the opposite: **PROD-style single-run polling + HTTP-layer network watchdog** (mark alive only on successful Telegram HTTP response, then os._exit on silence) and **no** in-process polling restart. Keep advisory lock and reconciliation on PROD; consider backporting the **Telegram network watchdog** (and removing or re-evaluating the event-loop-tick-based liveness watchdog) so that only real Telegram HTTP silence triggers restart.

### 5. Is webhook migration still necessary?

- For **freeze** mitigation: Not strictly necessary if a **Telegram HTTP-layer network watchdog** is in place (process exits when no successful Telegram response for N seconds; orchestrator restarts). That gives a single getUpdates session per process and no conflict loop.
- For **scalability and reliability**: Webhook avoids long-poll TCP stalls and is the recommended production pattern. So webhook migration remains **recommended** long term; short term, PROD can be stabilized with the network watchdog and no polling restart loop.

### 6. Recommended next steps

1. **Confirm PROD codebase:** Verify that PROD (main) actually has the multi-signal watchdog and the 120s liveness watchdog, and read `app.core.watchdog_heartbeats` (or equivalent) stale thresholds. If they are ~11 min, that explains the exit cadence.
2. **Add Telegram HTTP-layer network watchdog on PROD:** Mark “alive” only on successful `bot.session.make_request` response; if no such response for e.g. 180s, call `os._exit(1)`. Ensures only real Telegram inactivity kills the process.
3. **Remove or narrow in-process polling restart on STAGE:** Replace `while True` + sleep(5) with a single run (like PROD) so STAGE does not risk TelegramConflictError. Optionally add the same network watchdog on STAGE.
4. **Keep PROD advisory lock and reconciliation;** do not switch PROD to file lock or drop reconciliation.
5. **Address WORKERS_AUDIT_REPORT criticals:** Avoid holding DB conn across HTTP in crypto_payment_watcher, trial_notifications, and finalize_purchase Phase 1 to reduce pool starvation and make watchdogs less likely to fire falsely.

---

*End of audit. No code was modified.*
