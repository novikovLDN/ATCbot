# FULL SYSTEM WORKERS AUDIT — SENIOR LEVEL

**Date:** 2025-02-15  
**Scope:** All background workers, DB pool, financial flows, crash recovery, UUID/timezone/logging.  
**Assumption:** Production with real money, 50k users, 19k+ subscriptions.  
**No code changes:** Audit only.

---

## SECTION 1 — WORKER STRUCTURE VALIDATION

### 1.1 Per-worker summary

| Worker | Infinite loop + sleep | ITERATION_START/END | Exceptions caught | No silent exit | Bounded batch | acquire_connection / no conn across HTTP |
|--------|------------------------|----------------------|-------------------|---------------|--------------|----------------------------------------|
| activation_worker | ✅ 300s sleep | ✅ log_worker_iteration_* | ✅ | ✅ | ✅ limit=50 | ✅ fetch with acquire; attempt_activation uses pool, no conn during HTTP |
| auto_renewal | ✅ 600s sleep | ✅ | ✅ | ✅ | ✅ BATCH_SIZE=100 | ⚠️ Single conn for full transaction; Phase B sends Telegram **after** conn released ✅ |
| fast_expiry_cleanup | ✅ 60s sleep | ✅ | ✅ | ✅ | ✅ BATCH_SIZE=100, id>last_seen_id | ✅ Fetch/release; HTTP (remove_uuid) outside conn ✅ |
| crypto_payment_watcher | ✅ 30s sleep | ✅ | ✅ | ✅ | ✅ LIMIT 100 | ❌ **CRITICAL:** One conn held for entire loop; Crypto API + finalize_purchase (HTTP) while holding conn |
| trial_notifications | ✅ 300s sleep | ✅ | ✅ | ✅ | ✅ BATCH_SIZE=100, id>last | ❌ **CRITICAL:** pool.acquire() held during send_trial_notification (Telegram HTTP) and during remove_vless_user in expire path |
| reminders | ✅ 45min sleep | ✅ log_event iteration | ✅ | ✅ | ❌ **No LIMIT** | ✅ No conn during send (fetch then loop without conn) |
| reconcile_xray_state | ✅ 600s sleep | ✅ duration log | ✅ | ✅ | ✅ BATCH_SIZE_LIMIT=100 | ✅ acquire per fetch/live_check; HTTP (list/remove) outside conn ✅ |
| health_check | ✅ 10min sleep | No iteration log | ✅ | ✅ | N/A | ✅ pool.acquire() only for SELECT 1 |
| health_server | ✅ sleep 3600 | N/A | ✅ | ✅ | N/A | No DB in handler (DB_READY only) |

### 1.2 DB connection policy violations

- **crypto_payment_watcher:** `async with pool.acquire() as conn` wraps the full `for row in pending_purchases` loop. Inside the loop: `cryptobot.check_invoice_status(invoice_id)` (HTTP) and `database.finalize_purchase(...)` (which itself does VPN API and acquires its own conn). **Connection held across Crypto API and across finalize_purchase.** Violates “no DB connection held across HTTP/Crypto API.”
- **trial_notifications:** In `_process_single_trial_notification` and `_process_single_trial_expiration`, `async with pool.acquire() as conn` is held while calling `send_trial_notification` (→ `safe_send_message`, Telegram HTTP) and in expire path while calling `vpn_utils.remove_vless_user(uuid_val)` (HTTP). **Connection held across Telegram and VPN API.**
- **finalize_purchase (database.py):** Called from crypto watcher (and handlers). Uses `async with pool.acquire() as conn`; before starting the transaction it runs Phase 1: `await vpn_utils.add_vless_user(...)`. **Connection held during VPN API call.** This is a shared financial path, so counted as critical.

### 1.3 Nested acquire_connection / transaction span

- **auto_renewal:** Single transaction spans: SELECT FOR UPDATE SKIP LOCKED, UPDATE last_auto_renewal_at, decrease_balance, grant_access, INSERT payment, notification idempotency check. All use same `conn`. No HTTP inside transaction. ✅  
- **activation_worker:** No nested acquire; short-lived acquire per fetch and per mark/notification. ✅  
- **fast_expiry:** No transaction wrapping entire batch; per-item update in its own short transaction. ✅  
- **reconcile:** No transaction around HTTP; live check then release then delete. ✅  

---

## SECTION 2 — DATA LOSS & SKIP AUDIT

### ACTIVATION_WORKER

- **Skip risk:** Query is `ORDER BY id ASC LIMIT 50` with filter `activation_status='pending' AND activation_attempts < max_attempts`. No keyset (no `id > last_id`). Each run fetches the same “first 50” by id until they are activated or marked failed; next run naturally sees the next 50. New rows inserted during a run will appear in a later run (same query returns lowest ids first). **No deterministic skip of rows.** ✅  
- **Crash mid-batch:** Rows not yet activated remain pending; next run will pick them up. Idempotency: activation_service uses max_attempts and marks failed; duplicate activation is prevented by status and VPN/DB semantics. ✅  
- **Ordering:** Deterministic `ORDER BY id ASC`. ✅  

### AUTO_RENEWAL

- **Renewal failure after payment:** Payment (balance decrease + grant_access + payment INSERT) is inside one transaction. On failure, rollback; last_auto_renewal_at is not committed. ✅  
- **expires_at / last_auto_renewal_at:** Updated in same transaction; atomic. ✅  
- **Double-renew:** `last_auto_renewal_at` set at start of processing; condition `last_auto_renewal_at IS NULL OR last_auto_renewal_at < expires_at - 12 hours` and FOR UPDATE SKIP LOCKED prevent double renewal. ✅  
- **Renewal window:** Uses `database._to_db_utc(renewal_threshold)` and `_to_db_utc(now)`; UTC-consistent. ✅  

### FAST_EXPIRY_CLEANUP

- **Expired rows remain active:** Keyset pagination `id > last_seen_id ORDER BY id ASC LIMIT 100`; multiple batches until no rows. Re-check before UPDATE (expires_at < now_utc) and paid-subscription check. If VPN remove fails, UUID stays in processing_uuids and is retried next cycle; DB not updated until VPN success. ✅  
- **UTC:** now_utc and _to_db_utc / _from_db_utc used. ✅  
- **Trial vs paid:** Explicit `get_active_paid_subscription` before expiring trial; paid user never has trial UUID revoked. ✅  

### CRYPTO_PAYMENT_WATCHER

- **Payment marked paid but activation fail:** finalize_purchase is atomic (single transaction): pending→paid, payment INSERT, grant_access, etc. If activation fails, transaction rolls back; pending stays pending. ✅  
- **Webhook vs polling:** Crypto webhook (if registered) and this watcher can both see same invoice. finalize_purchase uses `UPDATE pending_purchases SET status='paid' WHERE purchase_id=$1 AND status='pending'`; second caller gets UPDATE 0 and raises ValueError (already processed). Idempotent. ✅  
- **Duplicate payment:** Same idempotency; no double activation. ✅  
- **Crash after payment marked paid but before activation:** In finalize_purchase, “mark paid” and activation are in the same transaction; no partial commit. ✅  

### TRIAL_NOTIFICATIONS

- **Duplicate notifications:** DB flags (trial_notif_6h_sent, etc.) updated after send; on retry, should_send checks flags. Risk: if send succeeds but DB update fails (e.g. conn held during send, then error), flag not set and duplicate possible. **Medium:** Ensure update runs and consider marking “sent” before send with rollback on send failure.  
- **Trial cleanup deletes paid:** get_active_paid_subscription check before expiring trial; re-check in _process_single_trial_expiration. ✅  
- **Trial→paid upgrade:** Paid subscription is separate row; trial expiry only touches source='trial'. ✅  

### REMINDERS

- **Send twice:** mark_reminder_sent(telegram_id, reminder_type) after send; should_send_reminder uses last_reminder_* and time windows. Idempotency window 30 min. If worker crashes after send but before mark_reminder_sent, duplicate possible. **Medium.**  
- **Reminder skip if worker crashes:** Next run re-evaluates; no persistent “in progress” lock. Some reminders may be sent late but not lost. ✅  
- **Logic idempotent:** Flags and time windows reduce duplicates; not fully idempotent under crash-after-send.  

### RECONCILE_XRAY_STATE

- **Grace window:** RECONCILE_GRACE_SECONDS (120s); no delete if last_touch or expires_at within grace. ✅  
- **Active UUID never removed:** Live re-check; if status='active' skip. ✅  
- **Reissue safe:** Reconcile does not create UUIDs; only removes orphans. ✅  
- **No deletion without live re-check:** Every delete preceded by acquire_connection + SELECT. ✅  
- **DB authoritative:** Orphan = in Xray not in DB snapshot; then live re-check. ✅  
- **No DB conn across HTTP:** Confirmed. ✅  
- **O(N) bounded:** BATCH_SIZE_LIMIT=100 for fetch and for orphans_list. ✅  

---

## SECTION 3 — PERFORMANCE & DELAY AUDIT

| Worker | Interval | Max iteration / batch | Overlap risk | Blocking / slow |
|--------|----------|------------------------|--------------|------------------|
| activation_worker | 300s | 15s cap, 50 items | Low | cooperative_yield every 50 |
| auto_renewal | 600s | 15s cap, 100 batch | Low | Same |
| fast_expiry_cleanup | 60s | 15s cap, 100 batch | Low | Same |
| crypto_payment_watcher | 30s | 15s cap, 100 batch | Medium if finalize slow | Holds conn whole loop |
| trial_notifications | 300s | No hard cap per run | Medium | Holds conn during Telegram |
| reminders | 45min | No cap | High at 50k | Unbounded fetch |
| reconcile | 600s | 20s timeout, 100 batch | Low | cooperative_yield |

- **Reminders:** get_subscriptions_for_reminders() has **no LIMIT**; fetches all rows with expires_at > now. At 50k users / 19k+ subscriptions this can be large memory and long-held connection. **Critical at scale.**  
- **Indexes:** subscriptions have idx_subscriptions_uuid_unique, idx_subscriptions_active_expiry; pending_purchases indexed on status, telegram_id, purchase_id, expires_at. Reminder query orders by s.expires_at ASC and filters on expires_at > $1; index on expires_at supports it but full result set is still loaded.  
- **Full table scans:** Activation query uses activation_status + activation_attempts; auto_renewal uses status, auto_renew, expires_at, last_auto_renewal_at. Indexes exist on status/expires_at where applicable. No full scan of 19k rows in a single query except reminders effectively loading all active subscriptions.  

---

## SECTION 4 — DB POOL SAFETY

- **Pool config:** max_size=15 (DB_POOL_MAX_SIZE), min_size=2.  
- **Worst-case concurrent usage:**  
  - Polling (1) + activation (1–2) + auto_renewal (1–2) + fast_expiry (1–2) + crypto (1, long-held) + trial (1–2, long-held) + reminders (1) + reconcile (1) + health (1) + handlers (variable).  
  - **crypto_payment_watcher** and **trial_notifications** holding one conn each for long periods (HTTP/Telegram) increases pool pressure.  
  - **finalize_purchase** (from crypto or handler) holds conn during Phase 1 VPN call.  
- **Starvation risk:** Under load, 15 connections can be exhausted if several workers hold conns across slow HTTP. **High risk** with current crypto + trial + finalize_purchase patterns.  
- **acquire_timeout:** asyncpg default; no explicit acquire_timeout in code.  
- **Advisory lock:** Single instance; released in main() finally. No deadlock with pool acquire. ✅  

---

## SECTION 5 — FINANCIAL CONSISTENCY AUDIT

- **finalize_purchase:** Single transaction: pending→paid, payment INSERT, grant_access (or balance top-up), payment approved, referral. Atomic. ✅  
- **activation:** Pending→active/failed in activation_service with conn; no payment or expires_at change in worker. ✅  
- **renewal:** decrease_balance + grant_access + payment INSERT in one transaction; last_auto_renewal_at in same transaction. ✅  
- **Reissue:** grant_access with existing UUID path (renewal) vs new UUID; action_type checked to avoid accidental UUID regeneration. ✅  
- **Expiration:** fast_expiry updates status to expired and clears uuid/vpn_key only after VPN remove and re-check. ✅  
- **No state payment=paid and subscription=inactive:** finalize_purchase ties payment and subscription in one transaction. ✅  
- **No active subscription without UUID provisioned:** Activation and finalize create UUID; reconciliation does not remove active. ✅  
- **Partial renew:** Not possible; single transaction. ✅  

---

## SECTION 6 — CRASH RECOVERY SAFETY

- **After payment marked paid but before activation:** Not possible; same transaction in finalize_purchase. ✅  
- **During activation:** Subscription stays pending; activation_worker retries; max_attempts and mark_activation_failed protect. ✅  
- **During renewal:** Transaction rollback; last_auto_renewal_at not committed; next run can retry. ✅  
- **During reconcile delete:** Delete is per UUID after live re-check; crash leaves orphan in Xray, retried next run. ✅  
- **During expiration:** If VPN remove succeeded but DB update not, next cycle re-checks and can update or retry VPN. processing_uuids is in-memory so lost on crash; duplicate VPN remove is idempotent. ✅  
- **During reminder send:** If crash after send but before mark_reminder_sent, duplicate reminder possible. **Medium.**  
- **During UUID reissue:** grant_access in transaction; rollback on failure. ✅  

---

## SECTION 7 — IDENTITY & UUID CONSISTENCY

- **Unique constraint:** idx_subscriptions_uuid_unique (migration 024). ✅  
- **No reuse:** New UUID per subscription via _generate_subscription_uuid; reconciliation only removes orphans. ✅  
- **Orphan UUID:** Reconcile removes only after grace and live re-check. ✅  
- **No duplicate DB UUID:** Unique index. ✅  
- **Active subscription without UUID:** Activation and finalize create UUID; fast_expiry only clears when expired. ✅  
- **Race activation vs reconcile:** Reconcile skips status='active' and grace window; activation sets active after add_vless_user. Safe. ✅  

---

## SECTION 8 — TIMEZONE & TIME CONSISTENCY

- **UTC:** database._to_db_utc / _from_db_utc used in workers; now = datetime.now(timezone.utc). ✅  
- **Expiration/renewal windows:** All use UTC. ✅  
- **Reminder schedule:** should_send_reminder uses subscription expires_at (normalized); 3d/24h/3h windows. ✅  

---

## SECTION 9 — LOGGING & OBSERVABILITY

- **Iteration start/end:** activation_worker, auto_renewal, fast_expiry, crypto_payment_watcher, trial_notifications use log_worker_iteration_start/end with outcome, items_processed, duration_ms. ✅  
- **reminders:** log_event with outcome success/failed, duration_ms. ✅  
- **reconcile:** duration and counts logged. ✅  
- **health_check:** No iteration counter; logs pass/fail and messages. **Minor.**  
- **Silent except:** Workers catch CancelledError, PostgresError, TimeoutError, and generic Exception; all log and either break (CancelledError) or sleep and continue. No bare except or silent swallow. ✅  
- **Financial operations:** finalize_purchase and activation log with purchase_id/user/correlation; payment_event_received, payment_verified logged. ✅  

---

## SECTION 10 — OUTPUT REQUIREMENTS

### 1. Critical issues (must fix)

| ID | Issue | Location | Fix |
|----|-------|----------|-----|
| C1 | DB connection held across Crypto API and across finalize_purchase (which does VPN API) | crypto_payment_watcher.py: single pool.acquire() for entire loop | Fetch batch with short-lived conn; for each row: release conn, check invoice (HTTP), then call finalize_purchase (which may acquire its own conn). Do not hold one conn for the whole loop. |
| C2 | DB connection held during VPN API (add_vless_user) in Phase 1 | database.py finalize_purchase | Perform Phase 1 (add_vless_user) before acquiring conn, or acquire conn only for the transaction block and run Phase 1 outside that block without holding any other conn. |
| C3 | DB connection held during Telegram send and during VPN remove in trial worker | trial_notifications.py _process_single_trial_* | Release conn before send_trial_notification and before remove_vless_user; re-acquire only for DB updates (or use a short transaction that does not span HTTP). |
| C4 | Unbounded fetch in reminders: no LIMIT, all subscriptions with expires_at > now | database.py get_subscriptions_for_reminders() | Add keyset pagination (e.g. id > last_id ORDER BY id LIMIT N) or at least a hard LIMIT (e.g. 2000) and document SLA; iterate in batches. |

### 2. High-risk issues

| ID | Issue | Location | Recommendation |
|----|-------|----------|----------------|
| H1 | Pool starvation under load: crypto + trial + finalize_purchase hold conns during HTTP | Multiple | Fix C1–C3; consider acquire_timeout and pool size review. |
| H2 | Reminder duplicate if crash after send but before mark_reminder_sent | reminders.py | Option: mark “sending” in DB first, then send, then mark “sent”; or accept 30-min idempotency window and document. |
| H3 | Trial notification duplicate if send succeeds but DB flag update fails (conn held during send) | trial_notifications.py | Fix C3 (release conn before send); then update flag in a short transaction after send. |

### 3. Medium-risk issues

| ID | Issue | Location |
|----|-------|----------|
| M1 | reminders: no items_processed or batch count in log | reminders.py |
| M2 | health_check_task: no ITERATION_START/END or iteration number | healthcheck.py |
| M3 | trial_notifications: total_fetched > 1000 only logs warning; no hard cap | trial_notifications.py |

### 4. Minor improvements

- Use acquire_connection(pool, "label") consistently where pool is used (e.g. crypto_payment_watcher, trial_notifications) for pool monitoring when POOL_MONITOR_ENABLED=true.  
- Add explicit acquire_timeout in pool config if asyncpg supports it to fail fast under starvation.  
- Log correlation_id (e.g. iteration or batch id) in finalize_purchase for payment traces.  

### 5. Performance bottlenecks

- **Reminders:** Single unbounded query and in-memory loop; at 50k users this is the main bottleneck and memory risk.  
- **Crypto watcher:** Long-held conn blocks pool for 30s interval; fixing C1 improves throughput.  
- **Trial worker:** Long-held conn during Telegram; fixing C3 improves pool availability.  

### 6. Data safety confirmation

- **Activation:** No skip; deterministic order; idempotent; crash-safe. ✅  
- **Auto-renewal:** Atomic; no double renew; idempotent. ✅  
- **Fast expiry:** Batched; re-check before update; trial/paid protected. ✅  
- **Crypto:** Idempotent finalize; no partial payment state. ✅  
- **Reconcile:** Grace + live re-check; no active UUID removed. ✅  
- **Reminders:** Possible duplicate on crash-after-send; no LIMIT (scale risk). ⚠️  
- **Trial:** Duplicate notification risk if DB update fails; paid-subscription protection in place. ⚠️  

### 7. Financial integrity confirmation

- All financial state changes (payment, balance, subscription, grant_access) that are tied together are in single transactions. No payment=paid with subscription=inactive; no partial renew. ✅  
- finalize_purchase and renewal paths are atomic. ✅  

### 8. Race condition report

- **Activation vs activation:** Single worker + lock; no parallel processing of same subscription. ✅  
- **Auto-renewal vs auto-renewal:** FOR UPDATE SKIP LOCKED + last_auto_renewal_at in same transaction. ✅  
- **Crypto webhook vs watcher:** finalize_purchase idempotent (UPDATE ... status='paid' WHERE status='pending'). ✅  
- **Fast expiry vs renewal:** Fast expiry re-checks expires_at and paid subscription before updating; renewal updates in its own transaction. ✅  
- **Reconcile vs activation:** Reconcile skips active and grace; activation sets active after provision. ✅  
- **Trial expiry vs payment:** get_active_paid_subscription before trial expiry. ✅  

### 9. Pool safety report

- **Current:** max 15 connections; multiple workers hold conns during HTTP (crypto, trial, finalize_purchase Phase 1). **Risk: high** under concurrent load.  
- **After C1–C3:** No conn held across HTTP; pool usage bounded by short transactions and brief acquires. **Risk: low** with same concurrency.  
- **Advisory lock:** Held for process lifetime; released in finally. ✅  

### 10. Final production safety verdict

- **With critical fixes (C1–C4):** System is production-safe for 50k users from a data, financial, and pool perspective. Remaining risks are duplicate reminder/trial notification under crash (mitigated by idempotency window and flags) and are acceptable with documentation.  
- **Without critical fixes:** **Not recommended** for high load: pool starvation and long-held conns can cause timeouts and failed payments/activations; unbounded reminders can cause OOM and long DB holds.  

**Verdict:** **Conditional go.** Fix C1–C4 before production at scale; then acceptable with monitoring.

---

## WORKER-BY-WORKER ANALYSIS

### activation_worker

- **Verified safe:** Loop with 300s sleep; ITERATION_START/END; bounded batch 50; ORDER BY id ASC; no conn across HTTP; attempt_activation uses pool and releases before VPN/Telegram. Idempotent; crash-safe.  
- **Risk areas:** None critical.  

### auto_renewal

- **Verified safe:** Single transaction for renewal; Phase B (notifications) after conn released; FOR UPDATE SKIP LOCKED and last_auto_renewal_at; UTC; idempotent.  
- **Risk areas:** None.  

### fast_expiry_cleanup

- **Verified safe:** Keyset batch; conn not held during VPN remove; re-check before UPDATE; trial/paid protection; processing_uuids for concurrency.  
- **Risk areas:** None.  

### crypto_payment_watcher

- **Verified safe:** finalize_purchase idempotent; payment atomic.  
- **Risk areas:** **Critical:** One conn for entire loop; HTTP (Crypto API) and finalize_purchase (VPN) while holding conn. **Must fix (C1).**  

### trial_notifications

- **Verified safe:** Batch 100; paid-subscription check; DB flags for idempotency.  
- **Risk areas:** **Critical:** conn held during send_trial_notification (Telegram) and during remove_vless_user in expire path. **Must fix (C3).** Possible duplicate if update fails after send.  

### reminders

- **Verified safe:** No conn during send; mark_reminder_sent after send; idempotency window 30 min.  
- **Risk areas:** **Critical:** get_subscriptions_for_reminders has no LIMIT; unbounded at scale. **Must fix (C4).** Duplicate if crash after send before mark (medium).  

### reconcile_xray_state

- **Verified safe:** Grace window; live re-check; no conn across HTTP; BATCH_SIZE_LIMIT; circuit breaker.  
- **Risk areas:** None.  

### health_check / health_server

- **Verified safe:** Read-only checks; no long-held conn; health_server does not use DB in handler.  
- **Risk areas:** Minor: no iteration counter in health_check_task.  

### finalize_purchase (shared path)

- **Verified safe:** Single transaction for all DB updates; idempotent via status='pending'.  
- **Risk areas:** **Critical:** Phase 1 add_vless_user runs while holding conn from pool.acquire(). **Must fix (C2).**  

---

## SUGGESTED PATCHES (SUMMARY)

1. **crypto_payment_watcher:** Refactor so that for each pending purchase you: (a) fetch batch with short conn and release; (b) for each row, call Crypto API (no conn); (c) if paid, call finalize_purchase (no conn held by caller). Ensure finalize_purchase does not hold conn during VPN (see 2).  
2. **database.finalize_purchase:** Move Phase 1 (add_vless_user) to run before acquiring the connection used for the transaction, or ensure no connection is held when calling add_vless_user (e.g. release before Phase 1, then acquire again for the transaction).  
3. **trial_notifications:** In _process_single_trial_notification and _process_single_trial_expiration, do not hold pool.acquire() across send_trial_notification or remove_vless_user. Use short transactions only for DB reads and writes; perform HTTP/Telegram outside any conn.  
4. **get_subscriptions_for_reminders:** Add keyset pagination (id > last_id ORDER BY id LIMIT N, e.g. 500) and loop until no rows; or add a hard LIMIT and document that reminders may span multiple runs.  

---

## FINAL PRODUCTION READINESS SCORE (0–100)

**Score: 68/100**

- **Deductions:**  
  - Critical: connection held across HTTP in 3 places (crypto, trial, finalize_purchase): **-18**  
  - Critical: unbounded reminders fetch: **-8**  
  - High: pool starvation risk: **-4**  
  - Medium: reminder/trial duplicate on crash: **-2**  

- **After C1–C4 and connection-policy fixes:** **88/100** (remaining for reminder/trial idempotency hardening and observability tweaks).

---

*End of audit. No code changes were made; this document is for verification and remediation planning.*
