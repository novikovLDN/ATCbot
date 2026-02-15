# Production Audit: VPN Entitlement Refactor

**Audit Date:** 2025  
**Scope:** Orphan UUID elimination, two-phase activation, DB constraints, reconciliation job  
**Assumption:** 10k–50k users, real money, hostile environment, webhook duplication, partial failures

---

## 1. Executive Summary

**Production Ready: NO**

Critical orphan UUID paths remain in `reissue_vpn_key_atomic` and `activation_service`. Two-phase activation is correctly implemented for `finalize_purchase`, `admin_grant_access_*`, `finalize_balance_purchase`, and `approve_payment_atomic`, but two code paths still call `add_vless_user` inside an active DB transaction. These must be fixed before production deployment.

---

## 2. SECTION 1 — ORPHAN UUID ELIMINATION AUDIT

### 1.1 add_vless_user NEVER inside DB transaction

**FAIL**

| Location | Transaction Scope | add_vless_user Call | Verdict |
|----------|-------------------|---------------------|---------|
| `database.py` 3698, 3712 | `reissue_vpn_key_atomic` line 3622: `async with conn.transaction():` | Inside tx | **CRITICAL** |
| `app/services/activation/service.py` 401 | `_attempt_activation_with_idempotency` called from line 341: `async with conn.transaction():` | Inside tx | **CRITICAL** |
| `database.py` 4338, 4350 | `grant_access` — only when `pre_provisioned_uuid` not set | Protected by `_caller_holds_transaction` invariant | OK |
| `finalize_purchase` | Phase 1 outside tx (line 6556–6540), Phase 2 inside | Phase 1 only | OK |
| `admin_grant_access_*` | Phase 1 outside (7567–7628), Phase 2 inside | Phase 1 only | OK |
| `finalize_balance_purchase` | Phase 1 outside (7754–7773), Phase 2 inside | Phase 1 only | OK |
| `approve_payment_atomic` | Phase 1 outside (4631–4705), Phase 2 inside | Phase 1 only | OK |
| `xray_sync.full_sync` | No DB transaction wraps add | Outside | OK |
| `vpn_utils.ensure_user_in_xray` | Called from grant_access renewal; add_user is 404 fallback with SAME uuid | Low risk (uuid already in DB) | Acceptable |

### 1.2 Two-phase flow strict enforcement

**PASS** (for refactored paths): `finalize_purchase`, `admin_grant_access_atomic`, `admin_grant_access_minutes_atomic`, `finalize_balance_purchase`, `approve_payment_atomic` correctly separate Phase 1 (outside tx) and Phase 2 (inside tx).

**FAIL** (for non-refactored paths): `reissue_vpn_key_atomic`, `activation_service._attempt_activation_with_idempotency`.

### 1.3 DB tx failure after UUID creation → remove_vless_user

**PASS** (refactored paths): Each has `except` block calling `remove_vless_user(uuid_to_cleanup_on_failure)` and logging `ORPHAN_PREVENTED`.

**FAIL** (non-refactored paths): No cleanup on rollback.

### 1.4 No code path: UUID created → rollback → UUID not removed

**FAIL**: `reissue_vpn_key_atomic` and `activation_service` can create UUID, have tx rollback, and never call `remove_vless_user`.

### 1.5 Cleanup failure not silently suppressed

**PASS**: All `ORPHAN_PREVENTED` blocks log `ORPHAN_PREVENTED_REMOVAL_FAILED` on remove failure; they do not swallow the original exception.

### 1.6 remove_vless_user idempotent

**PASS**: `vpn_utils.remove_vless_user` treats 404 as success (idempotent); `xray_api/main.py` remove-user endpoint returns 200 for missing UUID.

### 1.7 Retry on orphan cleanup

**FAIL**: Orphan cleanup uses a single `remove_vless_user` call with no retry. Task spec requested retry for transient failures.

---

## 3. SECTION 2 — DB CONSTRAINT HARDENING AUDIT

### 3.1 All datetime columns TIMESTAMPTZ

**PARTIAL PASS**

Migration 024 converts only a subset of timestamp columns:

| Table | Columns converted | Status |
|-------|-------------------|--------|
| subscriptions | expires_at, activated_at, last_auto_renewal_at, last_reminder_at | OK |
| payments | created_at, paid_at | OK |
| pending_purchases | created_at, expires_at | OK |

**Remaining TIMESTAMP WITHOUT TIME ZONE** (not in migration 024): `users.created_at`, `subscription_history.*`, `audit_log.created_at`, `referral_rewards.created_at`, `balance_transactions`, `broadcast_log.sent_at`, `promo_codes.*`, `withdrawal_requests.*`, `admin_broadcasts.*`, `vpn_keys.assigned_at`, etc. For billing/subscription flows, the critical ones are covered.

### 3.2 subscriptions.uuid UNIQUE

**PASS**: `CREATE UNIQUE INDEX idx_subscriptions_uuid_unique ON subscriptions(uuid) WHERE uuid IS NOT NULL`

### 3.3 Index for expiry worker

**PASS**: `CREATE INDEX idx_subscriptions_active_expiry ON subscriptions(expires_at) WHERE status = 'active'`

### 3.4 FK subscriptions.payment_id

**NOT ADDED** (by design): Migration 024 does not add `subscriptions.payment_id REFERENCES payments(id)`.

### 3.5 Migration lock duration

**PASS**: Migration uses `ALTER COLUMN ... USING ...` per column; no long full-table locks expected. `CREATE INDEX IF NOT EXISTS` is non-blocking for concurrent reads.

### 3.6 Existing data handling

**PASS**: `USING expires_at AT TIME ZONE 'UTC'` treats existing naive timestamps as UTC. Null UUIDs are allowed; UNIQUE index uses `WHERE uuid IS NOT NULL`.

---

## 4. SECTION 3 — RECONCILIATION JOB AUDIT

### 4.1 Fetches UUIDs from DB

**PASS**: `SELECT uuid FROM subscriptions WHERE uuid IS NOT NULL` (reconcile_xray_state.py:55–58)

### 4.2 Fetches UUIDs from Xray API

**PASS**: `vpn_utils.list_vless_users()` → GET /list-users

### 4.3 Compute ORPHANS and MISSING

**PASS**: `orphans = xray_uuids - db_uuids`, `missing_in_xray = db_uuids - xray_uuids`

### 4.4 For ORPHANS: remove, batch limit, structured log

**PASS**: Calls `remove_vless_user`, batch limit `BATCH_SIZE_LIMIT` (default 100), logs `reconciliation_removed`.

### 4.5 For MISSING: log CRITICAL, no auto-create

**PASS**: Logs `reconciliation_missing_in_xray` at CRITICAL, does not auto-recreate.

### 4.6 Feature flag

**PASS**: `XRAY_RECONCILIATION_ENABLED` exists; default `false` in config.

### 4.7 Concurrency, mass-delete, circuit breaker

**PARTIAL**:
- Concurrency: Single worker loop; no explicit locking (acceptable).
- Mass-delete: Batch limit 100 prevents unbounded deletes.
- Circuit breaker: **FAIL** — Reconciliation does not use circuit breaker. If VPN API fails, it will keep trying every 10 minutes with no backoff.

---

## 5. SECTION 4 — TRANSACTION SAFETY AUDIT

### 5.1 VPN API success → DB failure → UUID removed

**PASS** (refactored paths): `finalize_purchase`, `admin_grant_*`, `finalize_balance_purchase`, `approve_payment_atomic` — all call `remove_vless_user` in `except`.

**FAIL** (non-refactored): `reissue_vpn_key_atomic`, `activation_service`.

### 5.2 DB success → crash before commit

**PASS**: DB transaction not committed until all steps succeed; crash before commit rolls back. Two-phase ensures UUID is only created before commit; on crash, either Phase 2 never started (no UUID) or we never reached commit (tx rolls back, orphan cleanup in except cannot run if process crashed — acceptable; reconciliation will remove orphan).

### 5.3 Webhook duplicate → idempotency

**PASS**: `pending_purchase status='pending'` guard; `UPDATE ... WHERE status='pending'` atomic; duplicate webhook gets `UPDATE 0`, raises.

### 5.4 Expiry worker + renewal race

**PASS**: Expiry worker checks `get_active_paid_subscription`; renewal extends `expires_at`. `remove_vless_user` is idempotent. DB UPDATE uses `WHERE telegram_id = $1 AND uuid = $2 AND status = 'active'`; renewal changes status/expires before expiry worker’s UPDATE.

### 5.5 Reissue flow: old UUID removed before new saved

**PASS**: `reissue_vpn_key_atomic` removes old UUID first, then adds new. Risk: add succeeds, DB UPDATE fails → new UUID orphan (see 1.1).

---

## 6. SECTION 5 — IDEMPOTENCY & PAYMENT SAFETY REGRESSION

- **Duplicate webhook**: PASS — `finalize_purchase` idempotency preserved.
- **Duplicate finalize_purchase**: PASS — pending status guard.
- **UUID uniqueness at DB**: PASS — `idx_subscriptions_uuid_unique`.
- **Balance topup**: PASS — Logic unchanged; two-phase applies only to subscription path.
- **No new external API inside tx**: FAIL — `reissue_vpn_key_atomic` and `activation_service` still call `add_vless_user` inside transaction.

---

## 7. SECTION 6 — PERFORMANCE & SCALE AUDIT

- **Expiry worker query**: PASS — Uses `idx_subscriptions_active_expiry` (expires_at WHERE status='active').
- **Reconciliation DB query**: MEDIUM — `SELECT uuid FROM subscriptions WHERE uuid IS NOT NULL` — no covering index; acceptable for ~10k rows.
- **Full table scans**: None identified in hot payment/activation path.
- **Blocking locks**: `pg_advisory_xact_lock` used in balance purchase, reissue; acceptable for serializing per-user ops.
- **O(n) Python loops**: Reconciliation loops over orphans (max 100); activation/renewal are per-user. Acceptable.

---

## 8. SECTION 7 — SECURITY AUDIT

- **API key comparison**: **FAIL** — `xray_api/main.py:410` uses `api_key != XRAY_API_KEY` (not constant-time). Should use `hmac.compare_digest(api_key, XRAY_API_KEY)`.
- **UUID logging**: PASS — Truncated to 8 chars (`uuid[:8]...`).
- **Secret logging**: PASS — No API keys or tokens logged.
- **Fallback VLESS generation**: PASS — Not reintroduced.
- **XRAY_* in bot**: PASS — Bot uses API-only VLESS; no local generation constants reintroduced.

---

## 9. SECTION 8 — MIGRATION STRATEGY AUDIT

**PASS**: `docs/MIGRATION_STRATEGY_VPN_ENTITLEMENT.md` includes:
- Rollback plan
- Deployment order
- Monitoring plan
- First-deploy reconciliation instructions
- Manual orphan cleanup command (Python REPL example)

---

## 10. CRITICAL FINDINGS

1. **reissue_vpn_key_atomic** (database.py:3622–3760): `add_vless_user` called inside `async with conn.transaction():`. If add succeeds and DB UPDATE fails, new UUID becomes orphan.
2. **activation_service._attempt_activation_with_idempotency** (activation/service.py:336–344): `add_vless_user` called inside `async with conn.transaction():`. If add succeeds and DB UPDATE fails, UUID becomes orphan.

---

## 11. MEDIUM FINDINGS

1. **Orphan cleanup retry**: No retry on `remove_vless_user` in ORPHAN_PREVENTED path; transient failures may leave orphan.
2. **Reconciliation circuit breaker**: No circuit breaker; repeated VPN API failures cause continuous retries.
3. **Xray API key comparison**: Not constant-time; timing side-channel risk.
4. **Remaining TIMESTAMP columns**: Migration 024 does not convert all timestamp columns; non-billing tables still use TIMESTAMP.

---

## 12. FAILURE SIMULATION RESULTS

| Scenario | Expected | Actual |
|----------|----------|--------|
| finalize_purchase: Phase 2 fails after Phase 1 | UUID removed | PASS — ORPHAN_PREVENTED |
| admin_grant_access: Phase 2 fails | UUID removed | PASS — ORPHAN_PREVENTED |
| reissue_vpn_key_atomic: DB UPDATE fails after add | UUID removed | FAIL — No cleanup |
| activation_service: DB UPDATE fails after add | UUID removed | FAIL — No cleanup |
| Duplicate webhook | No duplicate subscription | PASS |
| Expiry + renewal race | No double-remove | PASS |

---

## 13. RISK SCORING

| Category | Score | Notes |
|----------|-------|------|
| Orphan elimination | 55/100 | 2 critical paths unfixed |
| DB constraints | 90/100 | Core columns and indexes correct |
| Reconciliation | 80/100 | Logic correct; no circuit breaker |
| Transaction safety | 60/100 | 2 paths still unsafe |
| Idempotency | 95/100 | Preserved |
| Performance | 85/100 | Indexes in place |
| Security | 75/100 | API key not constant-time |

**Overall: 71/100**

---

## 14. FINAL VERDICT

**PRODUCTION GATE: BLOCKED**

Before production deployment:

1. **Required**: Refactor `reissue_vpn_key_atomic` to two-phase (Phase 1 add_vless_user outside tx, Phase 2 DB update inside tx with orphan cleanup on failure).
2. **Required**: Refactor `activation_service._attempt_activation_with_idempotency` to two-phase.
3. **Recommended**: Use `hmac.compare_digest` for Xray API key validation.
4. **Recommended**: Add retry (e.g. 2 attempts) for `remove_vless_user` in ORPHAN_PREVENTED blocks.
