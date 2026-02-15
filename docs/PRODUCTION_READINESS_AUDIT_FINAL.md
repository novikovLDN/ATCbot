# Production Readiness Audit — Final Server-Aware Consistency Report

**Audit Date:** 2025  
**Role:** Principal / Staff Engineer  
**Scope:** Full server-aware consistency across Bot, Activation, DB, VPN API, Xray config, Reconciliation, Expiry, Network, Railway  
**Architecture:** API-only VLESS (Xray + REALITY + XTLS Vision); Bot never generates links locally

---

## 1. Executive Summary

**Production Ready: CONDITIONAL — YES with 3 blocking fixes**

| Criterion | Status |
|-----------|--------|
| Two-phase activation (add_vless_user outside tx) | ✅ PASS |
| Orphan cleanup with retry | ✅ PASS |
| Constant-time API key comparison | ✅ PASS |
| Reconciliation circuit breaker | ✅ PASS |
| No fallback VLESS generation | ⚠️ FAIL (dead code path) |
| External calls outside DB transactions | ⚠️ FAIL (1 path) |
| Xray port alignment | ⚠️ VERIFY |

**Risk Score: 28/100** (after fixes: ~18)

---

## 2. Risk Score Breakdown

| Category | Raw | Weighted |
|----------|-----|----------|
| Critical (blocking) | 3 | 15 |
| High | 2 | 6 |
| Medium | 3 | 5 |
| Low | 2 | 2 |

---

## 3. Critical Risks (Blocking Prod)

### CR-1: Fallback to `xray_manager.create_vless_user` violates architecture

**Location:** `app/handlers/payments/payments_messages.py:583-598`

**Finding:** When `vpn_key` is empty after `finalize_purchase`, the handler falls back to `xray_manager.create_vless_user`. The architecture explicitly forbids local/fallback VLESS generation.

**Evidence:**
```python
if not vpn_key:
    try:
        from xray_manager import create_vless_user
        vpn_key = await asyncio.to_thread(create_vless_user)
```

**Impact:** 
- `xray_manager` does not exist in the repo → `ImportError` at runtime
- If it were added, it would use legacy SSH/paramiko path (documented in VPN_AUDIT_README) — incompatible with API-only architecture
- Violates "Bot never reconstructs link" and "No fallback VLESS generation"

**Remediation:** Remove fallback block. On empty `vpn_key`, log CRITICAL and return error to user. Activation worker will eventually provision via `pending_activation`.

---

### CR-2: `remove_vless_user` inside DB transaction (check_and_disable_expired_subscription)

**Location:** `database.py:2905-2959`

**Finding:** `check_and_disable_expired_subscription` calls `vpn_utils.remove_vless_user(uuid)` inside `async with conn.transaction():`. External API call during active DB transaction.

**Evidence:**
```python
async with conn.transaction():
    ...
    if uuid:
        try:
            await vpn_utils.remove_vless_user(uuid)  # EXTERNAL CALL INSIDE TX
```

**Impact:**
- VPN API timeout (5s default) holds transaction open
- DB connection held during network I/O → connection pool exhaustion under load
- Potential deadlock if multiple users expire concurrently and pool is exhausted

**Remediation:** Two-phase: (1) fetch row in transaction, (2) commit, (3) call `remove_vless_user` outside transaction, (4) if success, run second transaction to mark expired. Or: call remove outside transaction before starting it, then transaction only for DB update.

---

### CR-3: Xray port 4443 vs VLESS link port — potential mismatch

**Context:** Audit states "Xray on port 4443"; xray_api default is `XRAY_PORT=443`.

**Location:** `xray_api/main.py:46`, `generate_vless_link` uses `XRAY_PORT`

**Finding:** If production Xray inbound listens on 4443 but `XRAY_PORT` is 443 (or vice versa), the VLESS link will point to the wrong port and users cannot connect.

**Remediation:** 
- Verify `XRAY_PORT` env on Xray API server matches actual Xray inbound port
- Document in runbook: `XRAY_PORT` must equal inbound `port` in config.json
- For REALITY camouflage, 443 is typical; 4443 is valid if explicitly configured

---

## 4. High Risks

### HR-1: Reconciliation uses `remove_vless_user` without retry

**Location:** `reconcile_xray_state.py:94`

**Finding:** Orphan removal uses `vpn_utils.remove_vless_user(uuid)` directly. ORPHAN_PREVENTED paths use `safe_remove_vless_user_with_retry`, but reconciliation does not.

**Impact:** Transient VPN API failure during reconciliation leaves orphan in Xray; next run will retry, but 10-minute interval means delayed cleanup.

**Remediation:** Use `safe_remove_vless_user_with_retry` in reconciliation loop (or a dedicated reconciliation-safe variant with lower retries to avoid long runs).

---

### HR-2: Duplicate webhook / payment replay idempotency

**Location:** `database.py:6554-6562`

**Finding:** `finalize_purchase` uses `UPDATE pending_purchases SET status = 'paid' WHERE purchase_id = $1 AND status = 'pending'`. If result != "UPDATE 1", it raises. Duplicate webhook with same `purchase_id` → second call gets status != 'pending' → ValueError "already processed". Idempotency is preserved for `purchase_id`.

**Gap:** CryptoBot webhook may retry with same `payload` (which maps to `purchase_id`). If webhooks are not deduplicated at HTTP layer, multiple concurrent calls could race. The `UPDATE ... WHERE status = 'pending'` is atomic — only one wins. Loser gets `UPDATE 0` and raises. **Verdict:** Idempotency is correct for single purchase_id. Ensure `purchase_id` is stable per payment (provider_invoice_id or equivalent).

---

## 5. Medium Risks

### MR-1: VPN API timeout (5s) may be short for Cloudflare + Railway

**Location:** `vpn_utils.py:36`, `config.XRAY_API_TIMEOUT` default 5.0

**Finding:** Bot (Railway) → Cloudflare → Xray API (Railway/same region). Cold starts or network latency could exceed 5s.

**Remediation:** Consider 10s for production; ensure retries (2 retries = 3 attempts) cover transient slowness.

---

### MR-2: Xray restart timeout 10s

**Location:** `xray_api/main.py:315`

**Finding:** `asyncio.wait_for(proc.communicate(), timeout=10)` for `systemctl restart xray`. On slow VMs, restart can exceed 10s → TimeoutError → HTTP 500. MutationQueue retries once after 1s.

**Impact:** Add-user returns 500; caller (bot) will retry. UUID may or may not be in config (write completed before restart). Reconciliation can eventually fix orphans.

---

### MR-3: `flow="xtls-rprx-vision"` in link vs config

**Location:** `xray_api/main.py:230-234`

**Finding:** `generate_vless_link` explicitly adds `flow=xtls-rprx-vision`. Xray inbound must have matching flow. Audit assumes REALITY + XTLS Vision — flow is required for traffic to pass.

**Status:** Correctly implemented. No flow in link → traffic would not pass.

---

## 6. Low Risks

### LR-1: API key in logs

**Finding:** Grep shows no logging of `XRAY_API_KEY`. UUID truncated to 8 chars. **PASS**.

---

### LR-2: config.json atomic write

**Location:** `xray_api/main.py:278-298`

**Finding:** `_save_xray_config_file` uses temp file + `shutil.move` — atomic on same filesystem. **PASS**.

---

## 7. Failure Simulation Matrix

| Scenario | UUID Leak | User Access Without DB | DB Active, Xray Not | Retry Amplifies | Self-Heal | Circuit Breaker | Backoff |
|----------|-----------|------------------------|---------------------|-----------------|-----------|-----------------|---------|
| VPN API timeout | No | No | No | No | Yes (retry) | Yes (vpn_api) | Yes |
| VPN API 500 | No | No | No | No | Yes | Yes | Yes |
| Malformed JSON | No | No | N/A | No | No (InvalidResponseError) | No (domain) | N/A |
| uuid without vless_link | No | No | N/A | No | No (raises) | No | N/A |
| DB deadlock | No | No | No | Possibly | Yes (retry) | N/A | DB-level |
| DB tx fail after add | No | **Yes** (orphan) | Yes | No | **Yes** (safe_remove retry) | N/A | Yes |
| remove_vless_user network fail | No | N/A | N/A | No | Partial (retry 3x) | N/A | 1s,2s,4s |
| Xray restart timeout | No | No | Maybe | No | Reconciliation | N/A | MutationQueue retry |
| config.json corruption | No | No | Yes | No | Manual | N/A | N/A |
| Cloudflare 526 | No | No | No | No | Retry | Yes | Yes |
| Railway redeploy mid-tx | No | No | Maybe | No | Reconciliation | N/A | N/A |
| Duplicate webhook | No | No | No | No | Idempotent | N/A | N/A |
| Payment replay | No | No | No | No | Idempotent | N/A | N/A |
| Expiry + renewal race | No | No | No | No | Advisory lock | N/A | N/A |
| Reissue race | No | No | No | No | Advisory lock | N/A | N/A |
| Massive orphan cleanup | No | N/A | N/A | No | Batch limit 100 | Yes | Yes |
| Reconciliation + activation | No | No | No | No | Independent | Yes | Yes |

---

## 8. Consistency Guarantees Status

| Guarantee | Status |
|-----------|--------|
| add_vless_user never inside conn.transaction() | ✅ (except grant_access standalone — protected by invariant) |
| remove_vless_user retry for orphan prevention | ✅ (safe_remove_vless_user_with_retry) |
| remove_vless_user idempotent (404 = success) | ✅ |
| subscriptions.uuid UNIQUE (partial) | ✅ (idx_subscriptions_uuid_unique) |
| expires_at indexed (active) | ✅ (idx_subscriptions_active_expiry) |
| All billing timestamps TIMESTAMPTZ | ✅ (024 + 025) |
| No missing FK for subscription-critical | ✅ |
| Advisory locks per user | ✅ (pg_advisory_lock, pg_advisory_xact_lock) |
| No long-running tx around external calls | ⚠️ FAIL (check_and_disable_expired_subscription) |

---

## 9. Infrastructure Misalignment Findings

| Item | Expected | Actual | Verdict |
|------|----------|--------|---------|
| XRAY_PORT | 4443 (per audit context) | 443 (default) | VERIFY |
| Bot has XRAY_SERVER_* | No | No | ✅ |
| XRAY_API_URL | HTTPS, public | Enforced | ✅ |
| SSL mode | Full Strict | Not audited (assume correct) | — |
| api.mynewllcw.com cert | Valid | Not audited | — |

---

## 10. Section-by-Section Verification

### Section 1 — Request Flow Audit

- ✅ add_vless_user never inside active DB transaction (finalize_purchase, activation_service, reissue, admin_grant, approve_payment all two-phase)
- ✅ remove_vless_user retry exists (safe_remove_vless_user_with_retry in ORPHAN_PREVENTED)
- ✅ remove_vless_user idempotent (404 treated as success)
- ✅ Xray restart failure: MutationQueue retries once; critical log on double failure
- ✅ API errors: 401/403 → AuthError; 4xx → InvalidResponseError; 5xx retried
- ⚠️ No fallback VLESS generation: FAIL — dead fallback to xray_manager exists
- ✅ Bot never reconstructs link (uses vpn_key from DB)
- ✅ API always returns vless_link (required_fields check)

### Section 2 — Failure Simulation

See Failure Simulation Matrix above.

### Section 3 — DB Consistency

- ✅ subscriptions.uuid UNIQUE partial
- ✅ expires_at indexed
- ✅ TIMESTAMPTZ for billing (024, 025)
- ✅ No UPDATE without telegram_id guard in critical paths
- ✅ Advisory locks per user

### Section 4 — Xray Config Safety

- ✅ flow=xtls-rprx-vision in link
- ⚠️ Port: XRAY_PORT default 443 — verify matches inbound
- ✅ SNI, public key, short id from env
- ✅ config_file_lock prevents concurrent writes
- ✅ Restart after write only (MutationQueue)
- ✅ Atomic write (temp + rename)

### Section 5 — Reconciliation Stability

- ✅ Feature flag (XRAY_RECONCILIATION_ENABLED)
- ✅ Circuit breaker (3 failures → 10 min open)
- ✅ Exponential delay on failure
- ✅ Batch limit 100
- ✅ Structured logging
- ✅ No auto-create for missing_in_xray
- ⚠️ Orphan removal uses remove_vless_user (no retry)

### Section 6 — Network & Infra

- ⚠️ XRAY_PORT vs 4443 — verify
- ✅ No XRAY_SERVER_* in bot config

### Section 7 — Logging & Observability

- ✅ ORPHAN_CLEANUP_* structured logs
- ✅ UUID truncated
- ✅ API key not logged
- ✅ RECONCILIATION_* logs
- ✅ ACTIVATION_PHASE1/PHASE2 logs

---

## 11. Final Production Gate Verdict

**Verdict: HOLD until CR-1, CR-2, CR-3 addressed**

| Gate | Status |
|------|--------|
| Zero orphan creation paths | ✅ |
| Retry-protected orphan cleanup | ✅ (ORPHAN_PREVENTED); ⚠️ reconciliation |
| No external call inside DB transaction | ❌ (check_and_disable_expired_subscription) |
| No fallback VLESS generation | ❌ (xray_manager fallback) |
| Port alignment | ⚠️ VERIFY |

**Required before production:**
1. Remove `xray_manager.create_vless_user` fallback; on empty vpn_key, fail gracefully.
2. Refactor `check_and_disable_expired_subscription` so `remove_vless_user` runs outside DB transaction.
3. Confirm `XRAY_PORT` matches Xray inbound port in production.

**After fixes: Production Ready: YES**
