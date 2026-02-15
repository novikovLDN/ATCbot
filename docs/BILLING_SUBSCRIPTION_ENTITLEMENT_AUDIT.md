# PRODUCTION-GRADE BILLING + SUBSCRIPTION + VPN ENTITLEMENT AUDIT

**Audit Date:** 2025  
**Scope:** Real-money payment flow, subscription lifecycle, VPN key provisioning  
**Assumption:** 10k users, hostile actors, race conditions, webhook duplication

---

## 1. Billing Integrity: **PASS** (with caveats)

| Check | Result |
|-------|--------|
| pending_purchase status guard | ✅ `status='pending'` required; `UPDATE ... WHERE status='pending'` atomic |
| Amount verification | ✅ `abs(amount_rubles - expected) > 1.0` → reject (1 RUB tolerance) |
| Currency | ⚠️ CryptoBot fiat may be USD; converted via RUB_TO_USD_RATE |
| CryptoBot webhook signature | ✅ HMAC-SHA256, `hmac.compare_digest` (constant-time) |
| No blind trust of client | ✅ Signature verified before processing |
| Provider-confirmed only | ✅ CryptoBot: invoice_paid + status=paid; Telegram: successful_payment from Telegram |

**Caveats:**
- Telegram `pre_checkout_query` answers `ok=True` without server-side amount verification.
- `purchase_id` extracted from payload; no server-side cross-check of tariff/amount from pending_purchase before CryptoBot amount is used when `amount_rubles <= 0`.

---

## 2. Expiration Enforcement: **PASS**

| Check | Result |
|-------|--------|
| Detection | ✅ fast_expiry_cleanup worker, 60–300s interval |
| Logic | ✅ `expires_at < now_utc` (UTC), `status='active'`, `uuid IS NOT NULL` |
| remove_vless_user called | ✅ via vpn_service.remove_uuid_if_needed |
| DB updated after removal | ✅ `status='expired'`, `uuid=NULL`, `vpn_key=NULL` |
| Timezone | ✅ `_to_db_utc` / `_from_db_utc` at DB boundary |
| Grace period | ❌ None — strict `expires_at < now` |
| Race guard | ✅ `processing_uuids` set, `check_row` before UPDATE |
| Paid user protection | ✅ `get_active_paid_subscription` — skip trial expiry if paid active |

**Verdict:** Expired subscriptions lose VPN access; UUID removed from Xray and DB.

---

## 3. UUID Lifecycle Safety: **PASS** (with one critical caveat)

| Check | Result |
|-------|--------|
| Create | ✅ grant_access → add_vless_user → DB save |
| Update (renewal) | ✅ ensure_user_in_xray / update_vless_user, same UUID |
| Reissue | ✅ remove_vless_user(old) → add_vless_user(new) → DB update atomic |
| Delete (expiry) | ✅ remove_vless_user → DB status=expired, uuid=NULL |
| UUID from API only | ✅ Bot does not generate links |
| Xray as source of truth | ✅ `new_uuid = uuid_from_api` |
| No dual generation | ✅ Single UUID before retry loop |

**Critical caveat — orphan UUID on rollback:**
- `grant_access` (and thus `add_vless_user`) is called **inside** the same DB transaction as `finalize_purchase`.
- If VPN API succeeds but a later step fails (e.g. referral_reward, DB write), the transaction rolls back.
- UUID is already created in Xray; DB and pending_purchase revert. **Result: orphan UUID in Xray.**
- Mitigation: xray_sync worker can reconcile; not guaranteed if disabled.

---

## 4. Payment Idempotency: **PASS**

| Check | Result |
|-------|--------|
| Duplicate webhook | ✅ pending_purchase status check before finalize |
| Same payment twice | ✅ `UPDATE pending SET status='paid' WHERE status='pending'` — second gets UPDATE 0, raises |
| payment_id uniqueness | ✅ pending_purchases.purchase_id UNIQUE |
| cryptobot_payment_id | ✅ UNIQUE index (partial, WHERE NOT NULL) |
| telegram_payment_charge_id | ✅ UNIQUE index (partial, WHERE NOT NULL) |
| finalize_purchase atomicity | ✅ Single transaction; rollback on any failure |

**Verdict:** Duplicate webhooks do not cause double activation.

---

## 5. DB Integrity: **FAIL** (partial)

| Check | Result |
|-------|--------|
| subscriptions.telegram_id UNIQUE | ✅ One subscription per user |
| subscriptions.uuid UNIQUE | ❌ **No UNIQUE constraint** — relies on app logic |
| subscriptions.expires_at | ⚠️ `TIMESTAMP` (no timezone) — UTC enforced in app via _to_db_utc |
| payments ↔ subscriptions FK | ❌ No FK — linked by telegram_id + purchase flow |
| Orphan subscriptions | ⚠️ Possible if payment rollback after grant_access partial success |
| Multiple active per user | ✅ Prevented by telegram_id UNIQUE |
| Index on subscription_end | ❌ No explicit index on expires_at (partial) |
| Index on uuid | ❌ No index |
| Index on payment_id | ⚠️ purchase_id in payments — no index on payments.id for subscriptions |

**Critical:**
- **TIMESTAMP WITHOUT TIME ZONE:** Schema uses `TIMESTAMP`; app treats as UTC. DST/clock drift risk if data is ever interpreted in another TZ.
- **No UNIQUE on subscriptions.uuid:** Two users could theoretically share a UUID only via bug; no DB-level protection.

---

## 6. Fraud Resistance: **PASS** (with notes)

| Attack | Mitigation |
|--------|------------|
| Replay webhook | Signature verification; idempotency via pending status |
| Payment ID reuse | purchase_id UNIQUE in pending_purchases |
| Invoice spoof | CryptoBot signature; Telegram delivers successful_payment |
| Manual DB manipulation | No application-level guard; DB access control required |
| Race expiry vs renewal | grant_access renewal path; auto_renewal uses FOR UPDATE SKIP LOCKED |
| UUID collision | uuid4; API mirrors request UUID |
| Forced reissue abuse | Admin-only; advisory lock in reissue |
| Negative balance | SELECT FOR UPDATE on balance; atomic debit |

---

## 7. Critical Vulnerabilities

1. **Orphan UUID on transaction rollback**  
   - External VPN API call inside DB transaction.  
   - VPN success + later failure → rollback → UUID remains in Xray.  
   - **Fix:** Two-phase: create UUID in Xray first, then DB in separate transaction; or reconciliation job.

2. **TIMESTAMP without time zone**  
   - `expires_at` and similar columns are `TIMESTAMP` (no TZ).  
   - UTC semantics enforced only in code.  
   - **Fix:** Migrate to `TIMESTAMPTZ` for all datetime columns.

3. **No UNIQUE on subscriptions.uuid**  
   - Duplicate UUID across subscriptions not prevented by DB.  
   - **Fix:** `CREATE UNIQUE INDEX ON subscriptions(uuid) WHERE uuid IS NOT NULL`.

---

## 8. Medium Risks

1. **Two payments in parallel for same user**  
   - Different purchase_ids; both can create subscriptions.  
   - `subscriptions.telegram_id UNIQUE` forces one row per user; grant_access handles renewal vs new.  
   - Risk: Overlapping purchases could interact in edge cases.

2. **CryptoBot amount fallback**  
   - If `amount_rubles <= 0`, uses `pending_purchase["price_kopecks"]/100`.  
   - Slightly increases risk if payload is malformed.

3. **Telegram pre_checkout always ok=True**  
   - No server-side amount validation before charge.  
   - Telegram is trusted; risk is low but present.

4. **remove_vless_user failure**  
   - fast_expiry_cleanup: if removal fails, DB is not updated; retried next cycle.  
   - User keeps access until next successful removal.

---

## 9. Architectural Weaknesses

1. **External call inside transaction**  
   - grant_access (→ add_vless_user) runs inside finalize_purchase transaction.  
   - Long-held locks and orphan UUID risk.

2. **No subscription ↔ payment FK**  
   - Harder to audit and enforce referential integrity.

3. **Mixed idempotency keys**  
   - Subscription payments: purchase_id + cryptobot_payment_id (or telegram charge in that column).  
   - Balance topup: provider_charge_id with idempotency check.  
   - Schema and naming could be clearer.

4. **Single subscription per user**  
   - telegram_id UNIQUE on subscriptions.  
   - No support for overlapping or multiple subscriptions per user.

---

## 10. Hardening Recommendations

1. **Separate transaction phases**  
   - Phase 1: Validate payment, call VPN API, obtain UUID.  
   - Phase 2: DB transaction for pending update, payment insert, grant_access DB updates.  
   - On Phase 2 failure: log orphan UUID, run reconciliation to remove from Xray.

2. **Schema migration**  
   - Use `TIMESTAMPTZ` for all datetime columns.

3. **subscriptions.uuid uniqueness**  
   - `CREATE UNIQUE INDEX idx_subscriptions_uuid ON subscriptions(uuid) WHERE uuid IS NOT NULL`.

4. **Indexes**  
   - `CREATE INDEX idx_subscriptions_expires_at ON subscriptions(expires_at) WHERE status = 'active'`  
   - `CREATE INDEX idx_subscriptions_uuid ON subscriptions(uuid) WHERE uuid IS NOT NULL` (or as above for uniqueness).

5. **Reconciliation job**  
   - Periodic job: compare DB subscriptions vs Xray clients; remove orphan UUIDs in Xray.

6. **Payment–subscription link**  
   - Add `payment_id` (or similar) to subscriptions for traceability.

---

## 11. Production Readiness Score: **72/100**

| Category | Score | Notes |
|----------|-------|------|
| Billing integrity | 18/20 | Amount check, signature, idempotency |
| Expiration | 18/20 | Worker, remove from Xray, UTC handling |
| UUID lifecycle | 12/15 | Correct flow; orphan risk on rollback |
| Payment idempotency | 15/15 | Strong guards |
| DB integrity | 8/15 | Missing UNIQUE on uuid, TIMESTAMPTZ |
| Fraud resistance | 13/15 | Good coverage |
| Failure recovery | 5/10 | Orphan UUID, no formal reconciliation |

**Blocker for high-assurance production:** Resolve orphan UUID on rollback (transaction phasing or reconciliation) and add UNIQUE constraint on subscriptions.uuid.
