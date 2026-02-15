# Full System Production Audit

**Audit Date:** 2026-02-13  
**Scope:** All workers, financial logic, DB safety, concurrency, pagination, UUID lifecycle, invariants  
**Type:** Correctness, safety, atomicity, race-condition analysis only

---

## 1. Executive Summary

The system has solid foundations: auto_renewal financial path is atomic (conn passed); UUID lifecycle is guarded; keyset pagination is correct; event loop uses asyncio only. **One critical financial atomicity bug** exists in `finalize_purchase` for balance topup: `increase_balance` is called without `conn`, causing balance to commit in a separate transaction. If the outer transaction fails later, the user keeps the balance without purchase finalization. Activation worker lacks SKIP LOCKED, allowing duplicate processing under parallelism. Pool config (max_size=15) should be adequate for 6 workers plus handlers. **Overall: Fix the finalize_purchase balance-topup atomicity before production at scale.**

---

## 2. Critical Issues

### 2.1 finalize_purchase — Balance Topup Not Atomic

**Location:** `database.py` L6598-6603

**Issue:** For balance topup, `increase_balance()` is called **without** `conn=conn`:

```python
balance_increased = await increase_balance(
    telegram_id=telegram_id,
    amount=amount_rubles,
    source="cryptobot" if payment_provider == "cryptobot" else "telegram_payment",
    description=f"Balance top-up via {payment_provider}"
)
```

**Impact:** `increase_balance` acquires its own connection and runs in its own transaction. The balance increase commits separately from the outer `conn.transaction()`. If payment insert, promo consume, or referral fails, the outer transaction rolls back, but the balance increase has already committed. Result: user receives balance without purchase being marked paid — **free money**.

**Affected path:** CryptoBot and Telegram balance topup via `finalize_purchase`.

**Fix required:** Pass `conn=conn` to `increase_balance` so it participates in the same transaction.

---

## 3. High Risk Issues

### 3.1 Activation Worker — No SKIP LOCKED

**Location:** `activation_worker.py`, `app/services/activation/service.py`

**Issue:** `get_pending_subscriptions` uses `ORDER BY id ASC LIMIT $2` without `FOR UPDATE SKIP LOCKED`. Two workers or restarts can process the same pending subscription.

**Impact:** Duplicate activation attempts; possible duplicate UUID creation or double notification. Existing idempotency (subscription_check, UUID comparison) mitigates but does not eliminate the race.

**State:** Best-effort; doc confirms no FOR UPDATE.

### 3.2 Crypto Payment Watcher — No Transaction Around finalize_purchase

**Location:** `crypto_payment_watcher.py` L201-206

**Issue:** `finalize_purchase` is called; it manages its own transaction internally. The watcher holds a single `pool.acquire()` for the whole batch. If `finalize_purchase` fails after partial commit (e.g., balance topup path), inconsistency as above.

**Note:** The critical bug is inside `finalize_purchase`, not the watcher’s transaction structure.

### 3.3 activation_worker — asyncio.sleep(0.5) Inside Loop Holding conn

**Location:** `activation_worker.py` L386

**Issue:** `await asyncio.sleep(0.5)` is inside the loop, which holds `pool.acquire() as conn`. Connection is held for 0.5s per item, without an explicit transaction.

**Impact:** Connection held across sleep; pool pressure under load. Not a correctness bug but a resource usage concern.

---

## 4. Medium Risk Issues

| Issue | Location | Description |
|-------|----------|-------------|
| cooperative_yield in tx | auto_renewal L135 | `cooperative_yield()` inside `conn.transaction()`; transaction held across yield. Minimal (sleep(0)); batch capped. |
| Silent except pass | auto_renewal L509, fast_expiry L217 | `except Exception: pass` for system state build. Intentional; no financial impact. |
| reminders.py no keyset | reminders.py | `get_subscriptions_for_reminders` fetches all; no pagination. Unbounded for large datasets. |
| activation no keyset | activation_service | `get_pending_subscriptions` uses LIMIT only; no `id > last_id`. OK for small pending sets. |

---

## 5. Safe Areas

- **auto_renewal:** decrease_balance, grant_access, increase_balance (refund) all use `conn=conn`; single transaction; FOR UPDATE SKIP LOCKED; last_auto_renewal_at; UUID regeneration guard.
- **Trial workers:** paid check before destructive action; TOCTOU hardening; no trial-over-paid.
- **UUID lifecycle:** fast_expiry and trial expiration re-check active paid; reconcile only removes orphans.
- **Keyset pagination:** fast_expiry, trial_notifications, expire_trial, reconcile all use `id/telegram_id > last`; ORDER BY; LIMIT; last_id only when rows exist.
- **Event loop:** No `time.sleep`; only `asyncio.sleep`; cooperative_yield; MAX_ITERATION_SECONDS.
- **Conn scope:** No connection used outside `async with pool.acquire()` in audited workers.
- **finalize_purchase subscription path:** grant_access receives `conn=conn`; atomic within transaction.

---

## 6. Phase 1 — Worker Architecture Table

| Worker | Type | Transaction | SKIP LOCKED | Keyset | Holds conn across yield | Max iter time | Event loop starve risk |
|--------|------|-------------|-------------|--------|-------------------------|---------------|-------------------------|
| auto_renewal | Financial | Yes (per batch) | Yes | N/A | cooperative_yield in tx | 15s | Low |
| trial_notifications | Mixed (notify) | No (per-row) | No | s.id > last | No | — | Low |
| expire_trial_subscriptions | Destructive | No (per-row) | No | u.telegram_id > last | No | — | Low |
| fast_expiry_cleanup | Destructive | Per-row update | No | id > last | No | 15s | Low |
| reconcile_xray_state | Destructive (orphans) | No | No | id > last | No | 20s timeout | Low |
| activation_worker | Mixed | No explicit | No | No | sleep(0.5) per row | 15s | Low |
| crypto_payment_watcher | Financial | Inside finalize | N/A | No | No | 15s | Low |
| broadcast (no_sub) | Read+send | No | No | No | No | — | Low |

---

## 7. Phase 2 — Financial Safety

| Check | Status |
|-------|--------|
| All financial mutations use conn=conn | ❌ finalize_purchase balance_topup: increase_balance without conn |
| All financial ops in transaction | ✅ auto_renewal; ❌ balance_topup in finalize |
| No path: balance↓, no extend, no refund | ✅ auto_renewal paths covered |
| No double-renewal | ✅ last_auto_renewal_at + FOR UPDATE SKIP LOCKED |
| last_auto_renewal_at not bypassable | ✅ UPDATE at start; in WHERE |
| No parallel renewal of same subscription | ✅ FOR UPDATE SKIP LOCKED |
| Crash before commit → full rollback | ✅ auto_renewal; ❌ balance_topup (separate tx) |

**Crash simulation (auto_renewal):**
- After decrease, before grant: rollback → ✅
- After grant, before payment insert: rollback → ✅
- After payment insert, before commit: rollback → ✅

**Crash simulation (finalize_purchase balance_topup):**
- After increase_balance: balance committed; if later steps fail → ❌ inconsistent (user keeps balance, purchase not finalized)

---

## 8. Phase 3 — UUID Lifecycle

| Check | Status |
|-------|--------|
| Paid UUID never removed by trial | ✅ get_active_paid_subscription before revoke |
| Paid UUID never removed by fast_expiry | ✅ get_active_paid_subscription before revoke |
| Reconcile only removes orphans | ✅ orphans = xray_uuids - db_uuids |
| No double-remove | ✅ processing_uuids; re-check before DB update |
| No removal without DB update | ✅ DB update only after VPN remove success |
| No DB update without removal attempt | ✅ Skip if removal skipped (non-VPN-disabled path) |

**Race simulation:**
- User buys paid while trial expiration: active_paid checked before and after should_expire → ✅
- User renews while fast_expiry: conn.transaction() re-checks before UPDATE; SKIP_RENEWED if renewed → ✅

---

## 9. Phase 4 — Pagination & Data Loss

| Worker | id > last | ORDER BY | LIMIT | last_id when rows | OFFSET | Infinite loop | Skip rows |
|--------|-----------|----------|-------|-------------------|--------|---------------|-----------|
| fast_expiry | ✅ | id ASC | ✅ | ✅ | No | No | No |
| trial_notifications | s.id > | s.id ASC | ✅ | ✅ | No | No | No |
| expire_trial | u.telegram_id > | ASC | ✅ | ✅ | No | No | No |
| reconcile | id > | id ASC | ✅ | ✅ | No | No | No |

Concurrent inserts: Keyset uses monotonic id; new rows with id > current last_id are fetched in next batch. No skip.

---

## 10. Phase 5 — DB Connection & Pool Safety

| Check | Status |
|-------|--------|
| No conn outside async with | ✅ |
| No yield while holding conn (except noted) | ⚠️ cooperative_yield in auto_renewal tx; sleep(0.5) in activation |
| No long-lived conn across batches | ✅ |
| No incorrect nested acquire | ✅ |
| Pool config | min=2, max=15, acquire_timeout=10s, command_timeout=30s |

6 workers + handlers: Each worker typically 1 conn per batch; broadcast acquires per user. 15 connections should be sufficient for moderate load.

---

## 11. Phase 6 — Event Loop Safety

| Check | Status |
|-------|--------|
| time.sleep | ❌ Not used |
| Blocking file I/O | ❌ |
| Sync HTTP | ❌ |
| Yield between batches | ✅ |
| MAX_ITERATION_SECONDS | ✅ 15s (auto_renewal, activation, crypto, fast_expiry) |
| Heavy CPU loops | ❌ |

Worst-case stall: ~15s if MAX_ITERATION_SECONDS is hit (early break). cooperative_yield every 50 iterations limits sustained blocking.

---

## 12. Phase 7 — Concurrency & Race Conditions

| Scenario | Outcome | Safety |
|----------|---------|--------|
| TOCTOU trial vs paid | Fresh active_paid in trial_notifications | ✅ Safe |
| Parallel renewals | FOR UPDATE SKIP LOCKED | ✅ Safe |
| Trial vs paid (expire) | Double active_paid check | ✅ Safe |
| Activation duplicate | No SKIP LOCKED; idempotency checks | ⚠️ Best-effort |
| Reconcile vs expiry | Orphans only; no overlap with valid UUIDs | ✅ Safe |
| Double notification | trial_notif_* flags; payment idempotency | ✅ Safe |

---

## 13. Phase 8 — Crash Consistency Matrix

| Worker | Crash before fetch | Crash mid-batch | Crash mid-tx | Crash after API | Crash before commit |
|--------|--------------------|-----------------|--------------|-----------------|---------------------|
| auto_renewal | OK | Rollback | Rollback | Rollback | Rollback |
| finalize (subscription) | OK | Rollback | Rollback | Orphan cleanup | Rollback |
| finalize (balance_topup) | OK | ⚠️ Balance may commit | ⚠️ Balance may commit | ⚠️ | ⚠️ Inconsistent |
| fast_expiry | OK | UUID in processing_uuids; retry | conn.tx rollback | VPN done, DB not; retry | Rollback |
| trial expiration | OK | Per-row conn | Rollback | Partial; retry-safe | Rollback |
| reconcile | OK | Orphan list partial | N/A | Remove done; no DB | N/A |

---

## 14. Phase 9 — Notification Idempotency

| Mechanism | Status |
|-----------|--------|
| trial_notif_* flags | ✅ Set after send; prevent re-send |
| notification_service idempotency | ✅ check_notification_idempotency; mark_notification_sent |
| Broadcast retry | Per-user; no global retry storm |
| Permanent vs temporary | failed_permanently → set flag, no retry; temporary → retry |

No infinite retry storm; duplicate permanent notifications prevented by flags.

---

## 15. Phase 10 — Invariant Check

| Invariant | Guaranteed? |
|-----------|-------------|
| status='active' AND expires_at > now ⇒ UUID exists | Best-effort (activation may be pending) |
| status='expired' ⇒ UUID NULL | ✅ Enforced by update |
| balance ≥ 0 | ✅ CHECK/decrease logic |
| last_auto_renewal_at ≤ expires_at | ✅ Logic + 12h window |
| Trial never overrides paid | ✅ Guards in place |
| Active subscription has payment (except trial) | Best-effort (admin grants) |

---

## 16. Scores

| Score | Value | Notes |
|-------|-------|-------|
| Crash Safety | 7/10 | Balance topup atomicity bug |
| Financial Atomicity | 6/10 | finalize_purchase balance path |
| Concurrency Safety | 8/10 | Activation no SKIP LOCKED |
| Overall Production | **7/10** | Fix balance_topup before scale |

---

## 17. Production Readiness Verdict

**Safe for production at scale?**  
**NO** — until `finalize_purchase` balance topup passes `conn=conn` to `increase_balance`, there is a critical financial inconsistency risk (balance committed without purchase finalization).

**After fix:** The rest of the system is sufficiently robust for production, with the documented medium risks accepted.
