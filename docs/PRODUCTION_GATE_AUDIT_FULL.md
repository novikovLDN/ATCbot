# FULL SYSTEM PRE-PRODUCTION AUDIT

**Role:** Principal Backend Engineer / Staff Security Architect  
**Mode:** ZERO-ASSUMPTION, SERVER-AWARE, FAILURE-ORIENTED  
**Scope:** Entire repository (bot, DB, activation, expiry, reconciliation, xray_api, payments, config, workers)  
**Assumptions:** 50k users, real money, hostile env, duplicate webhooks, network instability, Railway + Cloudflare, Xray REALITY + XTLS Vision

---

## SECTION 1 — ARCHITECTURE INTEGRITY

### 1.1 Strict API-only VLESS architecture

| Check | Status | Evidence |
|-------|--------|----------|
| Bot NEVER generates VLESS links | ✅ PASS | No `f"vless://"` in `app/` |
| No XRAY_SERVER_IP/PORT/SNI/PBK in bot | ✅ PASS | These exist only in `xray_api/main.py`, `config.py` |
| No fallback VLESS generation | ✅ PASS | Bot uses `vless_url` from API/DB only |
| No xray_manager usage | ✅ PASS | `grep` returns 0 in app |
| No legacy SSH-based VLESS | ✅ PASS | No SSH/outline references in app |
| `vless://` in bot code | ⚠️ Test only | `tests/integration/test_vpn_entitlement.py:24` — mock return only |

**VLESS generation locations:**
- `xray_api/main.py:278` — `vless_url = f"vless://{server_address}?{query_string}#{quote(fragment)}"` (API server, not bot)

### 1.2 add_vless_user / remove_vless_user placement

**add_vless_user called INSIDE DB transaction: 0 occurrences** ✅

All add_vless_user call sites:
- `database.py:4248`, `4260` — inside `grant_access` when `_caller_holds_transaction=False` (standalone, no tx)
- `database.py:4596` — `approve_payment_atomic` Phase 1, OUTSIDE `async with conn.transaction():`
- `database.py:6501` — `finalize_purchase` Phase 1, OUTSIDE transaction
- `database.py:7539` — `admin_grant_access_atomic` Phase 1, OUTSIDE
- `database.py:7691` — `finalize_balance_purchase` Phase 1, OUTSIDE
- `database.py:8112` — `admin_grant_access_minutes_atomic` Phase 1, OUTSIDE
- `vpn_utils.py:430` — `ensure_user_in_xray` fallback (see 1.3)
- `reconcile_xray_state.py` — N/A (uses remove, not add)

**remove_vless_user called INSIDE DB transaction: 0 occurrences** ✅

- `database.py:3683` — `reissue_vpn_key_atomic` — AFTER `async with conn.transaction():` block ends (line 3665), in post-commit phase
- `reconcile_xray_state.py:95` — No transaction wraps this (fetch is separate, no tx during remove)

### 1.3 CRITICAL: ensure_user_in_xray inside transaction

**B1: grant_access RENEWAL path calls ensure_user_in_xray inside caller's transaction**

**Location:** `database.py:3909` — `await vpn_utils.ensure_user_in_xray(...)`

**Flow:** When `approve_payment_atomic`, `finalize_purchase`, `admin_grant_access_atomic`, etc. call `grant_access(conn=conn, _caller_holds_transaction=True)` and the subscription is **renewal** (not new issuance):
- grant_access takes the renewal branch (line 3864+)
- Calls `ensure_user_in_xray` (external HTTP to Xray API) at 3909
- Then `conn.execute` UPDATE at 3926
- All inside the caller's `async with conn.transaction():`

**Impact:** Same as C-1/C-2 — DB connection held during network I/O (5–10s), pool exhaustion, cascading failures.

**Verdict:** ❌ **BLOCKING** — External API call inside active DB transaction.

### 1.4 safe_remove_vless_user_with_retry usage

| Path | Uses safe_remove? |
|------|-------------------|
| ORPHAN_PREVENTED (approve, finalize, balance) | ✅ Yes |
| OLD_UUID_REMOVED_AFTER_COMMIT | ✅ Yes |
| ADMIN_REVOKE | ✅ Yes |
| check_and_disable_expired_subscription | ✅ Yes |
| reissue_vpn_key_atomic (old UUID) | ❌ Uses `remove_vless_user` |
| reconcile_xray_state | ❌ Uses `remove_vless_user` |

**C-3:** Reconciliation uses `remove_vless_user` without retry — `reconcile_xray_state.py:95`  
**Medium:** Transient failure leaves orphan until next run (10 min).

---

## SECTION 2 — PAYMENT SECURITY

### 2.1 Idempotency

| Check | Status | Evidence |
|-------|--------|----------|
| UPDATE ... WHERE status='pending' | ✅ | `database.py:6541`, `6386`, `4567`, `4622` |
| purchase_id UNIQUE | ✅ | `migrations/004_add_pending_purchases.sql:18` — `purchase_id TEXT UNIQUE NOT NULL` |
| Double webhook → single subscription | ✅ | `UPDATE pending_purchases SET status='paid' WHERE purchase_id=$1 AND status='pending'` — second gets UPDATE 0, raises |
| Concurrent webhook race | ✅ | Atomic UPDATE; only one wins |

### 2.2 Replay protection

| Check | Status | Evidence |
|-------|--------|----------|
| hmac.compare_digest used | ✅ | `cryptobot_service.py:76` — `hmac.compare_digest(expected_signature, signature)` |
| Invalid signature → no processing | ✅ | Returns 200 "unauthorized", does not call finalize_purchase |
| Webhook returns 200 on invalid sig | ⚠️ | Intentional (avoids provider retries); see H-2 in SECURITY_AUDIT |
| Invalid signature path | Returns before `finalize_purchase` |

### 2.3 Amount validation

| Check | Status | Evidence |
|-------|--------|----------|
| Expected price from DB | ✅ | `cryptobot_service.py:381` — `expected_amount_rubles = pending_purchase["price_kopecks"] / 100.0` |
| No client-trusted price | ✅ | Always from `pending_purchase` |
| Tolerance | ✅ | `amount_diff > 1.0` — 1 RUB tolerance |
| Edge case 99 vs 100 RUB | ⚠️ | 1 RUB diff rejected (strict) |
| CryptoBot currency | ✅ | USD→RUB conversion via `RUB_TO_USD_RATE`; fallback to price_kopecks if amount_value<=0 |

### 2.4 Two-phase activation

| Check | Status |
|-------|--------|
| Phase 1 add_vless_user outside tx | ✅ |
| Phase 2 DB commit | ✅ |
| On tx failure → orphan cleanup | ✅ |
| safe_remove used for orphan | ✅ |

**Simulation matrix:**

| Scenario | Outcome |
|----------|---------|
| DB commit failure after add | ORPHAN_PREVENTED: safe_remove in except |
| Duplicate webhook | Second gets status!=pending or UPDATE 0, raises |
| Provider retry | Same as duplicate; idempotent |
| Network timeout during add_vless_user | Phase 1 fails; no DB write; no orphan |

---

## SECTION 3 — UUID LIFECYCLE & ORPHAN SAFETY

### 3.1 Path analysis

| Path | UUID created but not stored? | DB has UUID, Xray not? | DB removed, Xray has? | Orphan permanent? | safe_remove retry? |
|------|------------------------------|------------------------|------------------------|-------------------|--------------------|
| grant_access (new) | Phase 2 fail → orphan cleanup | Possible if add fails after? No | Post-commit remove fail | Reconciliation | Yes for ORPHAN_PREVENTED |
| grant_access (renewal) | N/A | ensure_user_in_xray fail → DB updated anyway | Post-commit old_uuid remove fail | Reconciliation | Yes |
| reissue_vpn_key_atomic | Tx fail → orphan cleanup | No | remove_vless_user fail | Reconciliation | No for old UUID |
| admin_revoke_access_atomic | N/A | N/A | safe_remove fail | Reconciliation | Yes |
| check_and_disable_expired | Phase 3 UPDATE 0 → orphan in Xray | N/A | Phase 2 fail → DB not updated | Reconciliation | Yes |
| reconciliation | N/A | Logs CRITICAL, no recreate | N/A | No | **No** — uses remove_vless_user |
| ensure_user_in_xray fallback | add_user 404→add | Possible | N/A | Reconciliation | N/A |

### 3.2 Orphan-free invariants

- **New issuance:** Two-phase; tx fail → safe_remove. ✅  
- **Renewal:** ensure_user_in_xray runs inside tx (B1). ⚠️  
- **Reconciliation:** Uses remove_vless_user; transient failure leaves orphan 10 min. Medium.  
- **Expiry:** Phase 3 UPDATE has race (see Section 4). ❌  

---

## SECTION 4 — EXPIRY / RENEWAL RACE CONDITIONS

### 4.1 CRITICAL: Expiry vs renewal race

**E1: check_and_disable_expired_subscription Phase 3 does not re-check expires_at**

**Location:** `database.py:2962-2967`

```sql
UPDATE subscriptions SET status='expired', uuid=NULL, vpn_key=NULL
WHERE id = $1 AND telegram_id = $2 AND uuid = $3 AND status = 'active'
```

**Race:**
1. T1: Expiry Phase 1 — SELECT row where expires_at <= now. Gets row R.
2. T2: Renewal — UPDATE subscriptions SET expires_at = future WHERE telegram_id = X. Commits.
3. T1: Expiry Phase 2 — remove UUID from Xray (user just renewed — wrong!)
4. T1: Expiry Phase 3 — UPDATE matches row (id, uuid, status unchanged). Sets status=expired, uuid=NULL.

**Result:** User pays for renewal, immediately loses access. Key loss for active paid user.

**Fix:** Add `AND expires_at <= NOW()` or `AND expires_at <= $4` (now from Phase 1) to Phase 3 WHERE.

### 4.2 Advisory locks

| Function | Advisory lock? |
|----------|----------------|
| finalize_balance_purchase | ✅ pg_advisory_xact_lock(telegram_id) |
| reissue_vpn_key_atomic | ✅ pg_advisory_lock + pg_advisory_xact_lock |
| check_and_disable_expired_subscription | ❌ No advisory lock |
| grant_access (standalone) | ❌ No (callers hold) |
| admin_revoke_access_atomic | ❌ No (uses FOR UPDATE in Phase 1) |
| finalize_purchase | ❌ No advisory lock on expiry path |

### 4.3 Race matrix

| Scenario | Key Loss | Double Remove | Correct? |
|----------|----------|---------------|----------|
| Expiry worker vs renewal | **Yes (E1)** | No | ❌ |
| Reissue during expiry | Lock in reissue | Idempotent remove | ✅ |
| Admin revoke during renewal | Revoke wins | N/A | ✅ |
| Expiry while payment finalization | Possible if E1 | No | ❌ |

---

## SECTION 5 — DATABASE CONSISTENCY

| Check | Status | Evidence |
|-------|--------|----------|
| subscriptions.uuid UNIQUE | ✅ | `migrations/024:131` — `idx_subscriptions_uuid_unique` partial |
| expires_at indexed | ✅ | `idx_subscriptions_active_expiry` |
| Billing timestamps TIMESTAMPTZ | ✅ | Migration 024 |
| UPDATE without telegram_id+uuid guard | N/A | Various; expiry Phase 3 uses id+uuid |
| Pool config | ✅ | `_get_pool_config` min=2, max=15, command_timeout=30 |
| Nested transactions | ❌ None observed |
| Missing await | Spot-check only |

**Pool exhaustion:** B1 (ensure_user_in_xray in tx) is primary vector. C-1/C-2 fixed.

---

## SECTION 6 — RECONCILIATION SAFETY

| Check | Status | Evidence |
|-------|--------|----------|
| XRAY_RECONCILIATION_ENABLED | ✅ | config.py:160, default false |
| Circuit breaker | ✅ | 3 failures → 10 min open |
| Batch limit | ✅ | BATCH_SIZE_LIMIT=100 |
| safe_remove for orphans | ❌ | Uses remove_vless_user |
| missing_in_xray auto-create | ✅ No | Logs CRITICAL only |
| Mass-delete valid UUIDs | ✅ No | Orphans = Xray - DB only |

**C-3 remediation:** Use `safe_remove_vless_user_with_retry` in reconcile_xray_state.py:95.

---

## SECTION 7 — Xray API Server Safety

| Check | Status | Evidence |
|-------|--------|----------|
| Constant-time API key | ✅ | xray_api/main.py:445 — hmac.compare_digest |
| XRAY_PORT validation | ✅ | _validate_xray_port_consistency at startup |
| SNI/PBK/SID from env | ✅ | xray_api/main.py:45-49 |
| config_file_lock | ✅ | _config_file_lock = asyncio.Lock() |
| flow=xtls-rprx-vision | In config | REALITY inbound |
| Restart timeout | MutationQueue batches restarts | Max 1 per FLUSHER_INTERVAL |

---

## SECTION 8 — SERVER-AWARE CONSISTENCY

| Check | Status |
|-------|--------|
| Link port = inbound port | ✅ _validate_xray_port_consistency |
| flow matches inbound | Config-driven |
| XRAY_PORT mismatch | Fails at startup |

---

## SECTION 9 — FAILURE SIMULATION MATRIX

| # | Scenario | UUID Leak | Double Charge | Key Loss | Pool Exhaustion | Self-Heal | Notes |
|---|----------|-----------|---------------|----------|-----------------|-----------|-------|
| 1 | Duplicate webhook | No | No | No | No | N/A | Idempotent |
| 2 | DB commit fail after add | No | No | No | No | ORPHAN_PREVENTED | safe_remove in except |
| 3 | Renewal ensure_user_in_xray 5s | No | No | No | **Yes** | No | B1 — tx held |
| 4 | Expiry vs renewal race | No | No | **Yes** | No | No | E1 — Phase 3 no re-check |
| 5 | VPN API down during add | Orphan possible | No | No | No | Reconciliation | 10 min delay |
| 6 | Reconciliation remove fail | Yes (10 min) | No | No | No | Next run | C-3 |
| 7 | Xray restart during add | Maybe | No | Maybe | No | Reconciliation | |
| 8 | Railway redeploy mid-tx | No | No | Maybe | No | Reconciliation | |
| 9 | admin_revoke remove fail | Orphan | No | No | No | Reconciliation | |
| 10 | reissue old UUID remove fail | Orphan | No | No | No | Reconciliation | Uses remove_vless_user |
| 11 | CryptoBot invalid signature | No | No | No | No | N/A | 200 unauth |
| 12 | Amount 99 vs 100 RUB | No | No | No | No | Rejected | 1 RUB tolerance |
| 13 | Concurrent finalize_purchase | No | No | No | No | UPDATE atomic | |
| 14 | Activation worker vs expiry | No | No | Possible | No | E1 | |
| 15 | Pool exhausted (B1) | No | No | No | **Yes** | No | Renewal path |
| 16 | Migration 024 not applied | Duplicate UUID? | No | No | No | Schema | |
| 17 | Reconciliation circuit open | Orphans persist | No | No | No | 10 min | |
| 18 | Telegram successful_payment | N/A | No | No | No | Provider trust | No CryptoBot sig |
| 19 | pending_activation timeout | No | No | Key delayed | No | activation_worker | |
| 20 | check_and_disable Phase 2 fail | Orphan in Xray | No | No | No | Phase 3 UPDATE 0 | DB consistent |

---

## SECTION 10 — FINAL VERDICT

### Production Ready: **CONDITIONAL**

**Risk Score: 62/100**

### Blocking issues

| ID | Issue | File:Line | Fix |
|----|-------|-----------|-----|
| B1 | ensure_user_in_xray called inside DB transaction (renewal path) | database.py:3909 | Two-phase: capture uuid, commit tx, call ensure_user_in_xray outside, then optional Phase 3 DB update |
| E1 | Expiry Phase 3 does not re-check expires_at → key loss on renewal race | database.py:2962-2967 | Add `AND expires_at <= $4` (now_utc from Phase 1) to WHERE |

### High risks

| ID | Issue | File:Line |
|----|-------|-----------|
| C-3 | Reconciliation uses remove_vless_user without retry | reconcile_xray_state.py:95 |
| H-2 | reissue_vpn_key_atomic uses remove_vless_user for old UUID | database.py:3683 |

### Medium risks

| ID | Issue |
|----|-------|
| M-1 | XRAY_RECONCILIATION_ENABLED defaults false — orphans accumulate |
| M-2 | check_and_disable_expired_subscription has no advisory lock (expiry worker vs get_subscription) |
| M-3 | Telegram successful_payment has no CryptoBot-style HMAC (relies on provider) |

### Low risks

| ID | Issue |
|----|-------|
| L-1 | 1 RUB amount tolerance may reject 99 vs 100 edge case |
| L-2 | Invalid webhook signature returns 200 (intentional; may confuse monitoring) |

### Exact file:line references

- **B1:** database.py:3909 — ensure_user_in_xray in renewal path inside caller tx  
- **E1:** database.py:2962-2967 — Phase 3 UPDATE missing expires_at re-check  
- **C-3:** reconcile_xray_state.py:95 — remove_vless_user  
- **H-2:** database.py:3683 — remove_vless_user (use safe_remove)

### Suggested fixes (priority order)

1. **B1:** Refactor grant_access renewal path to two-phase: (a) DB UPDATE in tx, (b) ensure_user_in_xray outside tx. On ensure fail, log CRITICAL; reconciliation or manual fix.
2. **E1:** Add `AND expires_at <= $4` to Phase 3 UPDATE in check_and_disable_expired_subscription, passing now_utc from Phase 1.
3. **C-3:** Replace `vpn_utils.remove_vless_user` with `vpn_utils.safe_remove_vless_user_with_retry` in reconcile_xray_state.py.
4. **H-2:** Replace `remove_vless_user` with `safe_remove_vless_user_with_retry` in reissue_vpn_key_atomic for old UUID removal.
5. **M-1:** Document that XRAY_RECONCILIATION_ENABLED should be true in production; consider default true for prod.

---

**Audit completed. Do NOT deploy without addressing B1 and E1.**
