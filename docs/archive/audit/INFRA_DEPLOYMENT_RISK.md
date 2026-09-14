# Infrastructure & Deployment Risk

## Railway Multi-Instance

**Current:** Single process per deployment. `INSTANCE_LOCK_FILE = "/tmp/atlas_bot.lock"` in main.py prevents multiple processes on the **same** host.

**Risk:** Railway can run multiple replicas (different containers/hosts). Each has its own filesystem. Lock file does **not** protect across instances.

**Impact:** If replicas > 1:
- Multiple activation workers process same pending subscriptions → orphan UUIDs
- Multiple fast_expiry_cleanup process same expirations → duplicate remove calls (idempotent, low impact)
- Multiple crypto_payment_watcher poll same pending → both may call finalize_purchase; idempotency protects

**Recommendation:** Ensure single replica for background workers, or use distributed locking (Redis, DB advisory locks) for activation and expiry.

---

## Restart / Crash During grant_access

**Scenario:** grant_access calls add_vless_user (success), then DB INSERT fails or process crashes before commit.

**Result:** UUID in Xray, no subscription row (or rollback). Orphan UUID.

**Mitigation:** xray_sync can reconcile Xray ↔ DB. Manual cleanup for orphans.

---

## Webhook Timeout

**Scenario:** CryptoBot webhook hits /webhook/payment. finalize_purchase holds transaction while calling grant_access → add_vless_user. VPN API slow → webhook times out.

**Result:** CryptoBot may retry. First attempt may eventually commit or rollback. Retry could see status='paid' (idempotent) or status='pending' (retry processes again).

**Recommendation:** Respond 200 quickly; process payment asynchronously (queue + worker). Or ensure VPN call completes within webhook timeout.

---

## Polling + Workers — No Distributed Coordination

| Worker | Coordination | Multi-Instance Safe |
|--------|--------------|---------------------|
| activation_worker | None | No |
| fast_expiry_cleanup | processing_uuids (in-memory) | No |
| crypto_payment_watcher | None (finalize_purchase idempotent) | Yes (idempotent) |
| auto_renewal | FOR UPDATE SKIP LOCKED | Yes |

---

## Database Connection Pool

Single pool per process. Under load, pool exhaustion can block workers and handlers. Monitor pool size and wait times.
