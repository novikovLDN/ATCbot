# Async Safety Audit Report

**Date:** 2025-02-13  
**Goal:** Root cause evidence for bot unresponsiveness after ~10–11 minutes  
**Scope:** Detection only. No business logic changes.

---

## 1. Sync I/O Findings (Inside async context)

| # | File | Function | Line | Pattern | Inside async def? |
|---|------|----------|------|---------|-------------------|
| 1 | `main.py` | `main()` | 101-102 | `with open(...) as f: f.write(...)` — blocking file I/O | ✅ Yes |
| 2 | `database.py` | `generate_referral_code()` | 1699 | `hashlib.sha256(...).encode()` — sync crypto | Called from async `create_user()` (once per user) |
| 3 | `cryptobot_service.py` | `_verify_webhook_signature()` | 71-74 | `hmac.new(..., hashlib.sha256)` — sync HMAC | Called from async webhook handler |
| 4 | `app/utils/logging_helpers.py` | multiple | 111, 165, 168, 171, 212, 264, 267, 270, 273, 385, 388, 391 | `json.dumps(log_data)` | Called from async workers/handlers (small payloads) |
| 5 | `main.py` | `main()` | 518, 528, 586 | `json.dumps(webhook_audit, default=str)` | ✅ Yes (startup/error paths) |

**Notes:**
- No `time.sleep` in production code.
- No `requests.*` usage.
- No `psycopg2`.
- No `subprocess.run`.
- CSV export uses `asyncio.to_thread(_generate_csv_file)` — correct.
- `apply_*_patch.py`, `validate_language_content.py`, `migrations.py` — sync file I/O in sync scripts (not async).

---

## 2. Event Loop Starvation — Long-held Connections / Long Iterations

| Worker | Pattern | Evidence |
|--------|---------|----------|
| **fast_expiry_cleanup** | Fetches all expired subscriptions (no LIMIT) | `conn.fetch("... ORDER BY expires_at ASC")` — unbounded |
| **auto_renewal** | Connection held for entire for-loop | `async with pool.acquire() as conn:` wraps `for sub_row in subscriptions:` — conn held for whole iteration |
| **trial_notifications** | Connection held for entire for-loop | `async with pool.acquire() as conn:` wraps `for row in rows:` — conn held for whole iteration |
| **broadcast_service** | Sequential loop, no concurrency cap | `for i, user_row in enumerate(users):` — no LIMIT on fetch; sequential send |
| **reconcile_xray_state** | Fetches all UUIDs | `conn.fetch("SELECT uuid FROM subscriptions WHERE uuid IS NOT NULL")` — unbounded (batch applied later) |

---

## 3. Workers That May Process >1000 Rows Per Iteration

| Worker | Fetch Query | Limit? | Risk |
|--------|-------------|--------|------|
| fast_expiry_cleanup | All expired subscriptions | None | HIGH — 10k users = 10k rows |
| trial_notifications | All eligible trial users | None | HIGH |
| auto_renewal | Subscriptions expiring in RENEWAL_WINDOW | None | MEDIUM |
| reconcile_xray_state | All UUIDs in DB | Batch limit 100 for orphans only | MEDIUM (fetch unbounded) |
| get_users_by_segment | All users or active subscriptions | None | HIGH for "all_users" |
| get_eligible_no_subscription_broadcast_users | All eligible users | None | HIGH |
| crypto_payment_watcher | Pending purchases | LIMIT 100 | OK |
| activation_worker | Pending subscriptions | limit=50 | OK |

---

## 4. Long Transaction / Long-held Connection Patterns

| Location | Pattern |
|----------|---------|
| `auto_renewal.py:71-72` | `async with pool.acquire() as conn:` holds conn for entire `for sub_row in subscriptions:` loop. Each iteration uses `async with conn.transaction():` — conn held across all iterations. |
| `trial_notifications.py:125-126` | `async with pool.acquire() as conn:` holds conn for entire `for row in rows:` loop. |
| `fast_expiry_cleanup.py:248` | `async with pool.acquire() as conn:` holds conn for fetch + full `for i, row in enumerate(rows):` loop. Inner `pool.acquire() as conn2` per row for DB update. |

---

## 5. HTTP Client Summary

| Module | Timeout | Async? |
|--------|---------|--------|
| vpn_utils.py | HTTP_TIMEOUT (3–5s from config) | ✅ httpx.AsyncClient |
| cryptobot_service.py | 10.0s | ✅ httpx.AsyncClient |
| payments/cryptobot.py | 30.0s | ✅ httpx.AsyncClient |
| xray_api/main.py | 10.0s | ✅ httpx.AsyncClient |

No sync HTTP. All use httpx with timeout.

---

## 6. DB Pool Configuration

- `min_size`: 2 (env `DB_POOL_MIN_SIZE`)
- `max_size`: 15 (env `DB_POOL_MAX_SIZE`)
- `acquire_timeout`: 10s (env `DB_POOL_ACQUIRE_TIMEOUT`)

With 6+ workers and long-held connections, pool can be exhausted.

---

## 7. Worker Intervals (Reference)

| Worker | Interval | Fetch limit |
|--------|----------|-------------|
| fast_expiry_cleanup | 60s | None |
| activation_worker | 300s | 50 |
| auto_renewal | 600s | None |
| reconcile_xray_state | 600s | Batch 100 (orphans only) |
| crypto_payment_watcher | 30s | 100 |
| trial_notifications | 300s | None |

---

## 8. Root Cause Evidence Summary

1. **Sync I/O in async main()** — `open/write` at startup (low impact; runs once).
2. **Unbounded fetches** — fast_expiry, trial_notifications, get_users_by_segment, get_eligible_no_subscription can load 10k+ rows.
3. **Long-held connections** — auto_renewal, trial_notifications, fast_expiry hold a pool connection for the whole iteration loop.
4. **No cooperative yield in trial_notifications** — `for row in rows:` has no `await` until send/DB; can starve event loop.
5. **broadcast_service** — sequential loop over all users; 10k users ≈ 8+ min at 0.05s/msg.
6. **DB pool pressure** — max 15 connections; 6 workers + handlers; long-held conns increase contention.

---

## 9. Instrumentation Added

Temporary monitors (no behavior change):

| Phase | Instrumentation | Status |
|-------|-----------------|--------|
| 2 | Event loop lag monitor (`[EVENT_LOOP_LAG]` when lag > 0.2s) | ✅ `app/core/async_safety_monitors.py` |
| 3 | Worker duration logging (`[WORKER_DURATION] worker=<name> duration=Xs`) | ✅ All 6 workers |
| 4 | DB pool acquisition timing (warning if > 2s) | ⏭ Skipped (requires pool wrapper) |
| 5 | HTTP call timing (warning if > 2s) | ⏭ Skipped (requires wrapping each httpx call) |
| 6 | Worker items count (`[WORKER_ITEMS] fetched=N`, warning if > 1000) | ✅ All workers |
| 7 | Long transactions | Documented in §4 (no code change) |
| 8 | CPU usage monitor (`[HIGH_CPU]` when > 80%) | ✅ Optional (requires `psutil`) |

**Files modified:**
- `main.py` — registers monitor tasks
- `app/core/async_safety_monitors.py` — event loop lag + CPU monitors
- `activation_worker.py`, `fast_expiry_cleanup.py`, `auto_renewal.py`, `crypto_payment_watcher.py`, `reconcile_xray_state.py`, `trial_notifications.py` — duration + items logging

Remove after root cause is fixed.
