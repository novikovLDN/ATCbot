# Full System Audit — Post Watchdog & Polling Patch

**Focus:** Purchases, renewals, notifications, Xray sync, worker side effects.  
**Scope:** Verify that watchdog refactor, advisory lock, polling restart logic, and heartbeat tracking did **not** break financial flows, renewal/UUID lifecycle, referrals, notifications, or workers.

**Audit type:** Code-level and path-level (traced execution paths).

---

## 1) Executive Summary

- **Purchase atomicity:** Confirmed. All balance mutations use `conn=conn` inside a single transaction; no `pool.acquire()` inside nested transaction in finalize paths. Two-phase activation (VPN API before commit) is used with explicit orphan cleanup on Phase 2 failure.
- **Renewal flow:** Confirmed. UUID is not regenerated on renewal; `subscription_end = max(expires_at, now) + duration`; expiry is monotonic; renewal never truncates duration or resets `subscription_start`. Xray sync is deferred via `renewal_xray_sync_after_commit` when caller holds transaction; `ensure_user_in_xray` uses 404→add_user fallback with same UUID.
- **Referral logic:** Confirmed. Reward is applied inside the same transaction as the purchase; idempotency by `purchase_id` in `referral_rewards`; no reward on failed purchase; financial errors propagate and rollback.
- **Notifications:** Confirmed. Success/VPN key/referral notifications are sent **after** `finalize_purchase` / `finalize_balance_purchase` return (i.e. after commit). Idempotency via `is_payment_notification_sent` / `mark_payment_notification_sent`.
- **Watchdog & polling:** Confirmed. Polling restart only cancels the task named `"polling_task"`; no process kill; no DB reinit; advisory lock and workers are unchanged. No new background tasks appended on polling restart; no duplicate worker spawning.
- **Workers:** All critical workers (activation_worker, auto_renewal, trial_notifications, crypto_payment_watcher, fast_expiry_cleanup, xray_sync) call `log_worker_iteration_start()` (which calls `mark_worker_iteration()`). reconcile_xray_state does **not** call it (medium: worker heartbeat can be stale if only reconciliation runs; multi-signal watchdog uses 90s worker threshold and requires all three stale).

**Critical finding:** Two-phase flows (finalize_purchase, finalize_balance_purchase) call VPN API (add_vless_user) **before** DB commit. This is by design; orphan cleanup on Phase 2 failure is implemented. No external API call that **mutates financial state** runs before commit; only VPN provisioning runs pre-commit with cleanup on rollback.

---

## 2) Critical Issues

**None.** No path found where:
- balance decreases and subscription is not extended and no refund,
- referral reward is applied and purchase is rolled back,
- user is notified of success but transaction is rolled back,
- Xray user is added but DB has no active subscription (orphan cleanup on tx failure is present).

---

## 3) High Risk Issues

**None.** All traced financial paths use a single connection/transaction for the critical section; referral and balance updates are in the same transaction as the purchase.

---

## 4) Medium Risk

1. **reconcile_xray_state does not call `log_worker_iteration_start`**  
   The multi-signal watchdog uses `last_worker_iteration_timestamp` from workers that call `log_worker_iteration_start`. reconcile_xray_state does not, so if it were the only worker running for >90s (e.g. others disabled or sleeping), the worker heartbeat could be stale. Impact: only matters when **all three** (event_loop, worker, healthcheck) are stale; event_loop and healthcheck are updated every 5s and on each /health hit. **Recommendation:** Optionally add `log_worker_iteration_start("reconcile_xray_state", iteration_number=...)` at the start of each reconciliation run for consistency.

2. **404 fallback in ensure_user_in_xray uses exception message**  
   Fallback to add_user is gated by `InvalidResponseError` and `"Client not found" in str(e)`. If the API ever returns 404 with a different message, fallback would not run. Code path: `vpn_utils.update_vless_user` raises `InvalidResponseError(error_msg)` with `error_msg = "Client not found in Xray: ..."`. So current behaviour is consistent; risk is future API change. **Recommendation:** Document that 404 response must keep "Client not found" semantics for fallback.

---

## 5) Confirmed Safe Areas

- **finalize_purchase (database.py):** Single `pool.acquire()` at entry; two-phase: Phase 1 add_vless_user outside transaction; Phase 2 `async with conn.transaction()`: pending→paid, increase_balance(conn=conn) for balance_topup, grant_access(conn=conn), payment approved, process_referral_reward(conn=conn). On exception: uuid_to_cleanup_on_failure removed via safe_remove_vless_user_with_retry. No pool.acquire inside transaction.
- **finalize_balance_purchase (database.py):** Phase 1 add_vless_user in separate `pool.acquire()` block; Phase 2 `async with conn.transaction()`: advisory lock, balance decrease via direct UPDATE, grant_access(conn=conn), payment insert, process_referral_reward(conn=conn). Orphan cleanup on exception. Referral financial errors re-raised to rollback.
- **grant_access (database.py):** Renewal path: `subscription_end = max(expires_at, now) + duration`; DB UPDATE keeps same UUID; validation `subscription_end <= old_expires_at` raises. When _caller_holds_transaction: returns `renewal_xray_sync_after_commit`; caller runs ensure_user_in_xray **after** commit. No UUID regeneration on renewal.
- **increase_balance / decrease_balance:** When `conn` is passed, use only that connection (`_do_increase`/`_do_decrease` on conn); no nested pool.acquire.
- **process_referral_reward:** Requires `conn`; idempotency by (buyer_id, purchase_id) in referral_rewards; balance update and referral_rewards insert in same transaction; financial/DB errors re-raised to rollback.
- **ensure_user_in_xray (vpn_utils.py):** update_vless_user → on 404 (exact: status_code == 404 → InvalidResponseError); only then fallback to add_vless_user with same uuid. Other HTTP errors (e.g. timeout, 5xx) propagate; no blind add_user. No infinite recursion (single fallback path).
- **Notifications (payments_messages.py, cryptobot_service.py, crypto_payment_watcher):** All send success/VPN key/referral **after** finalize_purchase/finalize_balance_purchase returns. Idempotency: is_payment_notification_sent / mark_payment_notification_sent; referral_reward from result (already applied in DB).
- **Polling restart (main.py):** polling_heartbeat_watchdog cancels only tasks with get_name() == "polling_task". start_polling_with_auto_restart on CancelledError: if not shutdown_requested, sleep(2) and continue loop (restart polling). No os._exit, no SystemExit from this path. background_tasks are created once at startup; no new tasks appended when polling restarts. Advisory lock and instance_lock_conn are not touched by polling restart.
- **Workers:** activation_worker, auto_renewal, trial_notifications, crypto_payment_watcher, fast_expiry_cleanup, xray_sync all call log_worker_iteration_start (→ mark_worker_iteration). auto_renewal: one conn.transaction() per batch; decrease_balance(conn=conn), grant_access(conn=conn); Telegram sends in notifications_to_send after transaction.

---

## 6) Concurrency Score

**9/10.**  
- Single advisory lock per user in balance/referral paths; FOR UPDATE and pg_advisory_xact_lock used.  
- Two-phase finalization avoids holding DB transaction across VPN API.  
- Polling restart does not create new connections or tasks; workers are not restarted.

---

## 7) Financial Integrity Score

**9/10.**  
- All balance and referral updates in finalize paths use conn=conn in one transaction.  
- No path: balance decreased without subscription extended and without refund (finalize_balance_purchase: decrease + grant_access + referral in one tx; on failure full rollback).  
- Referral idempotency by purchase_id; duplicate finalize (e.g. webhook retry) raises ValueError(already_processed) before any mutation.

---

## 8) Renewal Safety Score

**9/10.**  
- Renewal: UUID unchanged; subscription_end = max(expires_at, now) + duration; subscription_start unchanged; validation prevents non-monotonic expiry.  
- renewal_xray_sync_after_commit: ensure_user_in_xray called after commit; 404→add_user with same UUID; DB not rolled back on Xray failure.  
- Two quick renewals: same subscription row, FOR UPDATE / advisory lock serializes; no double extension bug observed in code.

---

## 9) Notification Consistency Score

**9/10.**  
- Notifications (success, VPN key, referral) sent only after finalize returns (commit done).  
- Idempotency: is_payment_notification_sent prevents double success message; referral_reward from result (reward already in DB).  
- No code path found where notification is sent inside an open transaction that could later roll back.

---

## 10) Production Verdict

**SAFE**

- Purchase, balance, referral, and renewal logic are consistent with single-transaction and two-phase design.  
- Watchdog and polling changes do not reinit DB, drop advisory lock, or spawn duplicate workers.  
- Notifications and Xray sync are post-commit; 404 fallback is narrow and uses same UUID.  
- Remaining medium items (reconcile heartbeat, 404 message contract) are documentation/consistency improvements, not blockers for production.

---

## Appendix: Path Traces

### finalize_purchase (subscription)
1. pool.acquire() → conn.
2. Pre-fetch pending_purchase (read-only).
3. If new issuance + VPN_ENABLED: add_vless_user (Phase 1) outside transaction; store uuid_to_cleanup_on_failure.
4. conn.transaction(): pending→paid, grant_access(conn, pre_provisioned_uuid), payment approved, process_referral_reward(conn). On exception: cleanup uuid_to_cleanup_on_failure; re-raise.
5. Post-commit: if renewal_xray_sync_after_commit, ensure_user_in_xray(...).

### finalize_balance_purchase
1. Phase 1: pool.acquire(), read subscription; if new issuance, add_vless_user; release conn.
2. pool.acquire() → conn; conn.transaction(): advisory lock, SELECT FOR UPDATE balance, UPDATE balance, grant_access(conn), payment insert, process_referral_reward(conn). On exception: cleanup uuid; re-raise.
3. Post-commit: ensure_user_in_xray if renewal_xray_sync_after_commit.

### ensure_user_in_xray
1. update_vless_user(uuid, subscription_end). On 404: raises InvalidResponseError("Client not found in Xray: ...").
2. Catch InvalidResponseError with "Client not found" → add_vless_user(telegram_id, subscription_end, uuid) (same UUID). Other exceptions propagate.
3. If add_vless_user fails: log CRITICAL, return None; DB already committed by caller.

### Polling restart
1. polling_heartbeat_watchdog: every 15s, if time.monotonic() - last_polling_activity > 90: log POLLING_STUCK_DETECTED, cancel task where get_name() == "polling_task", set last_polling_activity = now.
2. polling_task is start_polling_with_auto_restart(). On CancelledError: if shutdown_requested[0] break; else log "Polling task cancelled — restarting", asyncio.sleep(2), continue (next loop: new Bot(), delete_webhook, start_polling).
3. background_tasks list is not modified in this path; instance_lock_conn and DB pool unchanged.
