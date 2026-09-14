# Final Risk Score & Required Fixes

## Risk Score: 6.5 / 10

**Interpretation:** Moderate–high risk for production at scale. Critical issues in activation worker and transaction design. Payments and renewals are relatively well protected.

---

## Score Breakdown

| Area | Score (0–10) | Notes |
|------|--------------|-------|
| Payment idempotency | 8 | purchase_id, status checks, amount validation |
| Subscription renewal | 9 | No UUID regeneration, no add_vless_user on renewal |
| Activation worker | 3 | Race condition → orphan UUIDs |
| Expiration worker | 6 | Xray-first, DB-second; in-memory lock fails for multi-instance |
| Transaction safety | 4 | External API inside transactions |
| Multi-instance deployment | 2 | No distributed locking |
| Payment finalization | 5 | Atomic but external call inside TX |

---

## REQUIRED FIXES (by priority)

### P0 — Critical (Before scaling to thousands)

1. **Activation worker: Add FOR UPDATE SKIP LOCKED**
   - File: `app/services/activation/service.py`
   - In `_fetch_pending_subscriptions`: add `FOR UPDATE SKIP LOCKED` to SELECT
   - Or: acquire row lock before add_vless_user, release after UPDATE

2. **Activation: Move add_vless_user after DB reservation**
   - Option A: UPDATE subscriptions SET activation_status='activating' WHERE id=? AND activation_status='pending' RETURNING id (with FOR UPDATE)
   - Then add_vless_user
   - Then UPDATE uuid, activation_status='active'
   - Option B: Generate UUID, INSERT into "activation_queue" with lock, process one-by-one

3. **check_and_disable_expired_subscription: External call outside TX**
   - Remove UUID from Xray first (outside transaction)
   - Open transaction only for SELECT + UPDATE

### P1 — High (Before multi-replica)

4. **finalize_purchase: External call outside TX**
   - Mark pending paid, create payment in one TX
   - Call grant_access (or enqueue) outside TX for VPN provisioning
   - Consider pending_activation flow: create subscription with status=pending, let activation_worker do add_vless_user

5. **fast_expiry_cleanup: Use FOR UPDATE SKIP LOCKED**
   - Replace in-memory processing_uuids with DB row locking on subscription fetch

6. **Enforce single replica or add distributed lock**
   - Document: Run exactly 1 replica for workers
   - Or: Redis/DB lock around activation and expiry workers

### P2 — Medium (Hardening)

7. **grant_access: Refactor to avoid external call inside caller's TX**
   - When conn is passed, do not call add_vless_user inside that transaction
   - Use two-phase: (1) reserve/insert with pending, (2) external call, (3) final update

8. **Orphan UUID reconciliation**
   - Periodic job: list Xray users, compare to DB subscriptions, remove orphans

9. **Payment webhook timeout**
   - Ensure VPN call + DB fit within webhook timeout, or move to async processing
