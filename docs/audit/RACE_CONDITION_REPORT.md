# Race Condition Report

## CRITICAL: Activation Worker — Orphan UUID

**Location:** `app/services/activation/service.py` — `attempt_activation`, `get_pending_subscriptions`

**Issue:** No `SELECT FOR UPDATE` when fetching pending subscriptions. Two activation workers (e.g. two Railway instances or rapid restarts) can:

1. Both fetch the same pending subscription
2. Both generate different UUIDs (`uuid4()`)
3. Both call `add_vless_user` → Xray gets two users for one subscription
4. First `UPDATE ... WHERE activation_status='pending'` wins
5. Second gets `rows_affected=0` and returns first's result
6. Second worker's UUID is never stored in DB → **orphan UUID in Xray**

**Fix:** Use `SELECT ... FOR UPDATE SKIP LOCKED` in `_fetch_pending_subscriptions`, or lock at application level (e.g. Redis) before processing.

---

## CRITICAL: External API Inside DB Transaction

**Location:** `database.py` — `check_and_disable_expired_subscription`

**Issue:** `remove_vless_user(uuid)` is called **inside** `conn.transaction()`. If VPN API is slow or times out:

- Transaction holds row locks
- Other operations on same subscription block
- Long-running HTTP inside transaction increases deadlock and timeout risk

**Fix:** Remove UUID from Xray first (outside transaction), then open transaction only for DB update. Same pattern as fast_expiry_cleanup.

---

## CRITICAL: External API Inside DB Transaction — finalize_purchase

**Location:** `database.py` — `finalize_purchase` calls `grant_access(conn=conn)` which calls `add_vless_user` **inside** the same transaction.

**Issue:** HTTP call to VPN API while DB transaction is open. If VPN API hangs:

- Transaction holds locks (pending_purchases, payments, subscriptions)
- Payment webhook may timeout
- Retries can cause duplicate processing attempts

**Fix:** Split into two phases: (1) DB-only: mark pending paid, create payment, create subscription row with activation_status; (2) outside transaction: call grant_access (or activation worker) for VPN provisioning. Or use saga/outbox pattern.

---

## HIGH: fast_expiry_cleanup — processing_uuids In-Memory

**Location:** `fast_expiry_cleanup.py` — `processing_uuids = set()`

**Issue:** In-memory set does not protect against multiple instances. With multiple Railway replicas:

- Each replica has its own `processing_uuids`
- Two replicas can process same subscription
- Both call remove_vless_user (idempotent, OK)
- Both try UPDATE — second may update already-expired row (no-op or overwrite)

**Fix:** Use `SELECT ... FOR UPDATE SKIP LOCKED` when selecting subscriptions to expire, so only one process can claim each row.

---

## HIGH: Double Activation on Payment Retry

**Location:** `cryptobot_service.handle_webhook`, `crypto_payment_watcher` → `finalize_purchase`

**Mitigation:** `finalize_purchase` checks `status != 'pending'` and raises ValueError. `UPDATE pending_purchases SET status='paid'` is atomic with `WHERE status='pending'`. Second caller gets 0 rows.

**Residual risk:** If webhook and watcher both run for same payment before either commits, both could pass the check. Unlikely given transaction boundaries, but worth monitoring.

---

## MEDIUM: get_subscription + check_and_disable

**Location:** `database.py` — `get_subscription` calls `check_and_disable_expired_subscription` before fetching.

**Issue:** No lock. Two concurrent requests for same user could both run expiration logic. `remove_vless_user` is idempotent; DB UPDATE uses `WHERE expires_at <= $2 AND status='active'`. Second UPDATE affects 0 rows. Low impact.

---

## LOW: Auto-Renewal — Protected

**Location:** `auto_renewal.py` — uses `FOR UPDATE SKIP LOCKED` on subscription selection.

**Status:** Correct. Only one worker can process each subscription per cycle.
