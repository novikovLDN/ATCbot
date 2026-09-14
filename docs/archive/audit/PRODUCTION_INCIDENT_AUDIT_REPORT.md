# Production Incident Audit Report

**Date:** 2025-02-15  
**Context:** Telegram bot (Aiogram 3) on Railway; crash loop ~3 min; silent process exit.  
**Scope:** PART 1 full system audit + PART 2 simplifications (minimal, surgical).

---

## BUGS FOUND AND FIXES APPLIED

### 1. main.py — Webhook vs polling conflict

- **File:line:** `main.py` ~366–367, ~403–418  
- **Problem:**  
  - "Telegram polling started" was logged unconditionally even in webhook mode (misleading, suggests both modes run).  
  - Webhook audit block (get_webhook_info + delete_webhook) ran in both modes. In webhook mode this deleted the webhook before we set it again (redundant and wrong message "deleting before polling").  
- **Fix applied:**  
  - Log "Telegram polling started" only when `not config.WEBHOOK_URL`; in webhook mode log "Telegram webhook mode (pid=...), no polling".  
  - Run webhook audit (delete webhook if set) only when `not config.WEBHOOK_URL` (polling mode). In webhook mode the block is skipped; webhook is set only in the webhook branch below.  
- **Note:** `dp.start_polling()` is only called in the `else` (polling) branch; it is never called when `config.WEBHOOK_URL` is set. No simultaneous polling + webhook.

### 2. main.py — Watchdog logic

- **File:line:** `main.py` ~558–592  
- **Problem:**  
  - Watchdog read `last_webhook_update_at` (webhook) or `telegram_last_success_monotonic` (polling). Correct.  
  - No grace period after startup → risk of false-positive timeout if no traffic in first 180s.  
  - `os._exit(1)` caused the crash loop; Railway then restarted repeatedly.  
- **Fix applied:**  
  - **SIMPLIFICATION 1 (Option B):** Replaced `os._exit(1)` with a CRITICAL log: `LIVENESS_CHECK_FAILED — would exit (watchdog passive)`. Watchdog is now passive (no process kill).  
  - Added 60s grace period at start of watchdog: `await asyncio.sleep(60)` before the first liveness check.

### 3. app/api/telegram_webhook.py — Webhook endpoint

- **Checked:**  
  - `last_webhook_update_at` is updated at the very start of the POST handler (line 37).  
  - On exception, handler returns 200 to avoid Telegram retries (line 60).  
- **Not changed:** Handler does not return 200 before processing (it processes then returns). Slow processing could cause Telegram retries. Left as-is for minimal change; can be improved later (e.g. 200 + background processing).

### 4. auto_renewal.py (root)

- **Checked:**  
  - Entire iteration body wrapped in `asyncio.wait_for(..., timeout=120.0)` (ITERATION_HARD_TIMEOUT_SECONDS).  
  - `log_worker_iteration_end` is in a `finally` block (always runs).  
  - `pool.acquire()` is wrapped in `asyncio.wait_for(..., timeout=10.0)`.  
  - No HTTP/VPN API calls in this worker (DB + Telegram only).  
- **Unchanged:** Pattern already correct; no code change.

### 5. Dockerfile

- **Checked:** Explicit `COPY migrations/ ./migrations/` is present after `WORKDIR /app` and before `CMD`. Single-stage build.  
- **Unchanged:** Already correct; startup should show "Found 26 migration files" when 026 is present.

### 6. All background workers — ITERATION_END pattern

- **auto_renewal:** ITERATION_END in `finally` + `wait_for(120)` — OK.  
- **trial_notifications, crypto_payment_watcher, fast_expiry_cleanup, activation_worker, xray_sync:** Use `log_worker_iteration_end` in various branches; not all have a single `finally` that always runs.  
- **reminders:** Uses `log_event(..., operation="reminders_iteration")`, not `log_worker_iteration_end`; no `wait_for` around iteration.  
- **Unchanged:** No worker code changed in this pass. Full flattening to a single pattern (SIMPLIFICATION 3) would require touching each worker; left for follow-up to avoid scope creep.

### 7. Feature flags singleton

- **File:line:** `app/core/feature_flags.py`  
- **Problem:** Possible initialization before Railway env vars are available; need to confirm env at startup.  
- **Fix applied:** When initializing the singleton, log raw `os.getenv("FEATURE_AUTO_RENEWAL_ENABLED", "<unset>")` so logs show the value at init time.

---

## SIMPLIFICATIONS APPLIED

- **main.py**  
  - **Watchdog:** Passive mode — no `os._exit(1)`; CRITICAL log only. Added 60s startup grace period.  
  - **One startup mode (webhook only):** Polling log and webhook-delete audit run only when not in webhook mode. Liveness in webhook mode uses only `last_webhook_update_at` (session wrapper that updates `telegram_last_success_monotonic` is installed only when `not config.WEBHOOK_URL`).  
  - **Advisory lock:** Non-blocking with 1s max wait: `SET lock_timeout = '1000'` then `pg_advisory_lock`. On timeout/error, release connection, set `instance_lock_conn = None`, log warning and continue without single-instance guard (no raise).  
- **app/core/feature_flags.py**  
  - Startup log of raw `FEATURE_AUTO_RENEWAL_ENABLED` at init.

---

## UNCHANGED (AND WHY)

- **app/api/telegram_webhook.py:** Already updates liveness at handler start and returns 200 on error; no "return 200 before processing" change (minimal scope).  
- **auto_renewal.py:** Already has wait_for(120), ITERATION_END in finally, and acquire timeout; no change.  
- **Dockerfile:** Already has explicit `COPY migrations/`.  
- **Other workers (reminders, trial_notifications, fast_expiry_cleanup, activation_worker, crypto_payment_watcher):** Not flattened to the single loop pattern in this pass; can be done in a follow-up.  
- **Database schema, payment logic, watchdog timeout value (180s):** Not changed per constraints.

---

## DEPLOYMENT ORDER (FROM USER)

1. Deploy to STAGE with `FEATURE_AUTO_RENEWAL_ENABLED=false`.  
2. Verify in stage logs: "Found 26 migration files", no "Telegram polling started" (webhook only), workers show ITERATION_END after ITERATION_START, stable 10+ minutes.  
3. Set `FEATURE_AUTO_RENEWAL_ENABLED=true` on stage; confirm auto_renewal ITERATION_END within 2 min, no crash in 5+ min.  
4. Deploy to PROD with `FEATURE_AUTO_RENEWAL_ENABLED=false`.  
5. Monitor 10 min, then enable auto_renewal on prod.

---

## SUMMARY

- **Crash loop:** Addressed by making the watchdog passive (no `os._exit(1)`) and adding a 60s grace period.  
- **Webhook/polling clarity:** Polling log and webhook-delete audit only in polling mode; single liveness variable per mode.  
- **Startup delay:** Advisory lock limited to 1s; on failure the process continues without the lock.  
- **Observability:** Feature flag raw env logged at init; watchdog logs CRITICAL when liveness would have failed.
