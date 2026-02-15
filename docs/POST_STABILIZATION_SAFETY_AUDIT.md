# Post-Stabilization Production Safety Audit

**Audit Date:** 2026-02-13  
**Scope:** auto_renewal.py, trial_notifications.py, fast_expiry_cleanup.py, reconcile_xray_state.py  
**Context:** After final batching + keyset + transaction fixes

---

## 1. Executive Summary

The stabilization patch has addressed the critical runtime errors (NameError, released connection, indentation) and transaction atomicity. Financial operations in auto_renewal now use a shared connection (decrease_balance, grant_access, increase_balance all receive `conn=conn`), ensuring atomicity. Keyset pagination is correct across all workers. One medium-risk finding remains: cooperative_yield inside the auto_renewal transaction holds the transaction open during yield. No critical issues found; system is suitable for production with the noted caveats.

---

## 2. Critical Issues

**None.** All previously identified critical issues have been resolved:
- No NameError / UnboundLocalError
- No connection used outside `async with` block
- Indentation / logic structure correct

---

## 3. High-Risk Areas

**None.** All high-priority items are addressed:
- decrease_balance / grant_access / increase_balance use same transaction
- fast_expiry uses `conn` within scope for entire batch
- trial_notifications has TOCTOU hardening (fresh active_paid check)

---

## 4. Medium Risk Areas

| Area | Description | Mitigation |
|------|-------------|------------|
| **cooperative_yield inside transaction** | auto_renewal calls `cooperative_yield()` (asyncio.sleep(0)) at line 135 inside `async with conn.transaction()`. Transaction is held open across yield. | Minimal impact (sleep(0)); batch limited to 100; MAX_ITERATION_SECONDS caps iteration. |
| **get_last_approved_payment uses own conn** | auto_renewal calls `database.get_last_approved_payment(telegram_id)` without passing conn. Read-only; does not affect write atomicity. | Acceptable for read; write path is atomic. |
| **Silent except: pass** | auto_renewal L509, fast_expiry L217: `except Exception: pass` for system state build. fast_expiry L440: `except Exception: pass` for audit log. | Intentional fallback; no financial impact. Consider debug logging. |
| **processing_uuids unbounded** | fast_expiry `processing_uuids` set never cleared between worker cycles. Grows over time (UUIDs discarded after processing but set retains them until discard). | Actually: `discard(uuid)` removes after each row—set only holds in-flight UUIDs. No unbounded growth. **Resolved.** |

---

## 5. Confirmed Safe Areas

### SECTION 1 — Runtime Stability
- **Variables:** `telegram_id`, `subscription`, `language` defined at loop top (L141-144 auto_renewal).
- **Conn scope:** No conn used after leaving `async with pool.acquire()`.
- **Indentation:** Correct; `notification_already_sent` only in balance-sufficient branch; `else` correctly paired with `if balance_rubles >= amount_rubles`.
- **Defensive check:** `isinstance(subscriptions, list)` present (L126-129).

### SECTION 2 — Financial Safety

| Path | Outcome | Atomicity |
|------|---------|-----------|
| A) Balance sufficient → successful renewal | decrease_balance(conn) → grant_access(conn) → payment insert(conn) → notification | ✅ Same transaction; rollback on any failure |
| B) grant_access returns expires_at=None | Refund via increase_balance(conn) | ✅ Same transaction |
| C) UUID regenerated unexpectedly | Refund via increase_balance(conn) | ✅ Same transaction |
| D) Exception before commit | Full rollback (conn.transaction() context) | ✅ |
| E) Balance insufficient | No decrease, no grant; only log | ✅ No financial mutation |
| F) notification_already_sent | continue; balance/grant already committed in same tx | ✅ |
| G) DB exception during payment insert | Rollback; no commit | ✅ |

**Confirmations:**
1. decrease_balance, grant_access, increase_balance (refund) all use `conn=conn` — same transaction.
2. No path where balance decreases, subscription does not extend, and no refund occurs.
3. Crash before commit → entire transaction rolls back.

### SECTION 3 — Pagination Integrity

| Worker | Keyset | ORDER BY | LIMIT | last_id Update | Termination | OFFSET |
|--------|--------|----------|-------|----------------|-------------|--------|
| **auto_renewal** | N/A (FOR UPDATE SKIP LOCKED) | s.id ASC | $3 | N/A | break when empty | No |
| **fast_expiry_cleanup** | id > $2 | id ASC | $3 | if rows: last_seen_id=rows[-1]["id"] | break when empty | No |
| **trial_notifications** | s.id > $2 | s.id ASC | $3 | last_subscription_id=rows[-1]["subscription_id"] | break when empty | No |
| **expire_trial_subscriptions** | u.telegram_id > $2 | u.telegram_id ASC | $3 | last_telegram_id=rows[-1]["telegram_id"] | break when empty | No |
| **reconcile_xray_state** | id > $1 | id ASC | $2 | last_seen_id=rows[-1]["id"] | break when empty | No |

- last_seen_id / last_subscription_id / last_telegram_id updated only when rows non-empty.
- Monotonic progression; no infinite loop; no duplicate processing within run.
- Keyset on id/telegram_id is deterministic.

### SECTION 4 — Connection Safety
1. No conn used after leaving `async with pool.acquire()`.
2. fast_expiry: conn held for batch; DB update uses `conn.transaction()` (same conn).
3. No long-held connection across `await asyncio.sleep` (sleep is after conn release in batch loop).
4. cooperative_yield inside auto_renewal transaction — see Medium Risk.
5. Transactions short and bounded (per-batch in auto_renewal; per-row update in fast_expiry).
6. decrease_balance / increase_balance respect passed conn (database.py L1289-1291, L1205-1207).

### SECTION 5 — Trial Safety
1. Trial never overrides paid: `get_active_paid_subscription` before destructive action in _process_single_trial_expiration and _process_single_trial_notification.
2. TOCTOU: Fresh `get_active_paid_subscription(conn, telegram_id, now)` at top of _process_single_trial_notification (L133-136).
3. _process_single_trial_expiration: active_paid check before and after should_expire (L366-376, L393-401).
4. No double revoke: UPDATE uses `WHERE source = 'trial' AND status = 'active'`.
5. Notification flags (trial_notif_6h_sent etc.) prevent duplicate sends; set after successful send.

### SECTION 6 — UUID Lifecycle
1. auto_renewal: Validates `action == "renewal"` and `vless_url is None`; refund if UUID regenerated.
2. UUID removal only in: trial expiration (_process_single_trial_expiration), fast_expiry_cleanup.
3. Both removal paths re-check active paid before removal.
4. reconcile_xray_state: Removes only orphans (xray_uuids - db_uuids); never deletes valid DB UUIDs.
5. No path to paid UUID accidental deletion.

### SECTION 7 — Event Loop Safety
1. No `time.sleep` (only `asyncio.sleep`).
2. No blocking file I/O in workers.
3. No synchronous requests.
4. Yield between batches: `await asyncio.sleep(0)` or `BATCH_YIELD_SLEEP` in all batch loops.
5. MAX_ITERATION_SECONDS respected (auto_renewal L136-138, fast_expiry L279-281).
6. BATCH_SIZE applied everywhere (100 or configurable).

### SECTION 8 — Crash Resilience

| Crash Point | Data Consistency | Financial Correctness | Duplicate Op? | Silent Corruption? |
|-------------|------------------|------------------------|---------------|---------------------|
| Before commit (auto_renewal) | Rollback | No charge | No (last_auto_renewal_at rolled back) | No |
| After commit, before notification | Consistent | Correct | No (idempotency key) | No |
| During VPN removal | DB not updated; retry next cycle | N/A | No | No |
| During refund | Refund in same tx; rollback if crash | Rollback restores balance | No | No |
| During UUID revoke (trial) | Partial (VPN removed, DB maybe not) | N/A | Retry-safe | No |
| During payment insert | Rollback | No charge | No | No |

### SECTION 9 — Observability
1. Worker start: log_worker_iteration_start in all workers.
2. Worker end: log_worker_iteration_end.
3. Errors: logger.error / logger.exception.
4. Silent pass: auto_renewal L509, fast_expiry L217 (system state), fast_expiry L440 (audit log) — intentional; consider debug log.
5. No swallowed exception without logging in critical paths.

---

## 6. Final Production Score

| Area | Score | Notes |
|------|-------|------|
| Renewal safety | **9/10** | Atomic; refund paths; UUID guard. Minor: cooperative_yield in tx. |
| Trial safety | **9/10** | Paid guards; TOCTOU hardening; double-check before revoke. |
| UUID lifecycle | **9/10** | No accidental paid UUID deletion; reconcile only removes orphans. |
| Pagination correctness | **9/10** | Keyset correct; last_id only when rows; no OFFSET. |
| Concurrency safety | **9/10** | FOR UPDATE SKIP LOCKED; conn scope correct; no released conn use. |
| Event loop safety | **8/10** | cooperative_yield in tx; otherwise good. |

**Overall production readiness: 8.8/10**

---

## 7. Clear YES/NO — Safe to Deploy?

**YES.** No critical or high-risk issues. Medium risks are acceptable with current mitigations. All financial, trial, UUID, pagination, and connection safety requirements are satisfied.

---

## Appendix: Path Simulation Summary

**auto_renewal financial paths:**
- Sufficient balance + success: decrease(conn) → grant(conn) → payment(conn) → notify → mark_sent(conn) — all same tx.
- UUID regenerated: decrease(conn) → grant(conn) → detect → increase(conn) refund → continue (tx commits with refund).
- expires_at=None: decrease(conn) → grant(conn) → detect → increase(conn) refund → continue.
- Insufficient balance: no decrease, no grant.
- Exception: tx rollback; last_auto_renewal_at reverted.
