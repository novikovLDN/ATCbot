# Transaction Safety Report

## Anti-Pattern: External API Inside Transaction

| Location | Transaction Scope | External Call Inside |
|----------|-------------------|----------------------|
| database.check_and_disable_expired_subscription | conn.transaction() | vpn_utils.remove_vless_user |
| database.finalize_purchase | conn.transaction() | grant_access → add_vless_user |
| database.grant_access (when conn passed) | caller's transaction | vpn_utils.add_vless_user, remove_vless_user |

**Risk:** Long-held locks, timeout cascades, increased deadlock surface.

**Recommendation:** Perform external calls outside transactions. Use compensation or outbox for consistency.

---

## Correct Transaction Usage

| Location | Pattern | Notes |
|----------|---------|-------|
| database.finalize_balance_topup | Single transaction, DB only | Correct |
| database.increase_balance | SELECT FOR UPDATE, transaction | Correct |
| database.process_referral_reward | Uses conn, SELECT FOR UPDATE | Correct |
| database._consume_promo_in_transaction | Atomic UPDATE | Correct |
| auto_renewal | FOR UPDATE SKIP LOCKED, transaction | Correct |
| fast_expiry_cleanup | Xray call first, then new conn.transaction() for DB | Correct separation |

---

## Missing SELECT FOR UPDATE

| Location | Table | Risk |
|----------|-------|------|
| activation_service._fetch_pending_subscriptions | subscriptions | Two workers process same pending |
| database.get_pending_purchase (finalize path) | pending_purchases | Mitigated by UPDATE ... WHERE status='pending' |
| fast_expiry_cleanup subscription fetch | subscriptions | Multiple instances process same expiry |

---

## Idempotency Mechanisms

| Operation | Mechanism |
|-----------|-----------|
| finalize_purchase | UPDATE pending_purchases SET status='paid' WHERE status='pending'; status check before |
| grant_access renewal | No add_vless_user; only UPDATE expires_at |
| activation UPDATE | WHERE activation_status='pending' |
| referral_reward | UNIQUE (buyer_id, purchase_id), SELECT before insert |
| payments | purchase_id, cryptobot_payment_id for idempotency |

---

## Payment Atomicity

finalize_purchase runs in one transaction:

1. SELECT pending_purchase
2. Validate status, amount
3. UPDATE pending_purchase SET status='paid'
4. INSERT payment
5. grant_access (which may call add_vless_user)

If step 5 fails, entire transaction rolls back. Pending remains 'pending'; payment not created. Safe for retry.

**But:** add_vless_user may have already created user in Xray. On rollback, that user is orphaned (no DB record). Retry would create a new UUID. Orphan accumulates in Xray.
