# Production Security Audit — Final Report

**Date:** 2025  
**Role:** Principal Security Engineer / Staff Backend Architect  
**Scope:** Payment, Activation, UUID lifecycle, Expiry, Renewal, Reconciliation, Xray API, DoS/pool safety  
**Assumptions:** 50k users, hostile env, duplicate webhooks, partial failures, Railway + Cloudflare

---

## 1. CRITICAL

### C-1: remove_vless_user inside DB transaction (grant_access)

| Field | Value |
|-------|-------|
| **File** | `database.py` |
| **Function** | `grant_access` (lines 4177–4191) |
| **Attack scenario** | N/A (integrity/failure) |
| **Failure scenario** | User has expired subscription with UUID. New purchase triggers `finalize_purchase` → `grant_access(conn=conn, pre_provisioned_uuid=...)`. `grant_access` removes old UUID via `remove_vless_user(uuid)` while caller holds `conn.transaction()`. VPN API timeout (5s) or network failure keeps DB connection open and can exhaust pool. |
| **Data integrity risk** | Medium (no data corruption, but pool exhaustion → cascading failures) |
| **Exploitability** | Not directly exploitable |
| **Remediation** | Move old-UUID removal out of transaction: Phase 1 read + remove outside tx, Phase 2 DB update inside tx. Same two-phase pattern as `check_and_disable_expired_subscription`. |

---

### C-2: remove_vless_user inside DB transaction (admin_revoke_access_atomic)

| Field | Value |
|-------|-------|
| **File** | `database.py` |
| **Function** | `admin_revoke_access_atomic` (lines 8182–8227) |
| **Attack scenario** | N/A |
| **Failure scenario** | Admin revokes access. `remove_vless_user(uuid)` runs inside `conn.transaction()`. VPN API latency/timeout holds transaction and can exhaust connection pool. |
| **Data integrity risk** | Medium (pool exhaustion) |
| **Exploitability** | Low (admin action only) |
| **Remediation** | Two-phase: fetch subscription in tx, commit; call `remove_vless_user` outside tx; new transaction for DB update. |

---

### C-3: Reconciliation uses remove_vless_user without retry

| Field | Value |
|-------|-------|
| **File** | `reconcile_xray_state.py` |
| **Function** | `reconcile_xray_state` (line 95) |
| **Attack scenario** | N/A |
| **Failure scenario** | Orphan removal calls `remove_vless_user` without retry. Transient network/VPN API failure leaves orphans until next run (10 min). Not a correctness bug, but weaker resilience than ORPHAN_PREVENTED paths. |
| **Data integrity risk** | Low (delayed cleanup) |
| **Exploitability** | N/A |
| **Remediation** | Use `safe_remove_vless_user_with_retry` for orphan removal. |

---

## 2. HIGH

### H-1: Amount tolerance ±1 RUB

| Field | Value |
|-------|-------|
| **File** | `database.py` |
| **Function** | `finalize_purchase` (line 6468) |
| **Attack scenario** | Attacker pays 99 RUB for 100 RUB subscription. `amount_diff = 1.0`, `if amount_diff > 1.0` is false → passes. Sub pays 99 RUB for 100 RUB plan. |
| **Failure scenario** | Currency/rounding drift could cause legitimate payments to fail if tolerance is too tight. |
| **Data integrity risk** | Low (1 RUB per abuse) |
| **Exploitability** | Medium (requires CryptoBot invoice manipulation or similar) |
| **Remediation** | Document tolerance as intentional; consider lowering to ±0.01 or per-tariff config. |

---

### H-2: CryptoBot webhook returns 200 on invalid signature

| Field | Value |
|-------|-------|
| **File** | `cryptobot_service.py` |
| **Function** | `handle_webhook` (lines 282–290) |
| **Attack scenario** | Replay with wrong signature returns 200 "unauthorized". Attacker cannot process payment but may probe behavior. |
| **Failure scenario** | Invalid/missing signature logs warning and returns 200. Designed to avoid retries from provider; acceptable for anti-replay. |
| **Data integrity risk** | None (payment not processed) |
| **Exploitability** | Low (no payment processing) |
| **Remediation** | Consider 401 for invalid signature to signal auth failure; keep idempotent handling. |

---

### H-3: grant_access removes old UUID before invariant check

| Field | Value |
|-------|-------|
| **File** | `database.py` |
| **Function** | `grant_access` (lines 4177–4191) |
| **Finding** | Old UUID removal runs before `_caller_holds_transaction` / `pre_provisioned_uuid` checks. When called from transactional callers with an existing UUID, removal happens inside transaction. Same as C-1. |
| **Remediation** | See C-1. |

---

## 3. MEDIUM

### M-1: DB pool sizing under load

| Field | Value |
|-------|-------|
| **File** | `database.py` |
| **Config** | `DB_POOL_MAX_SIZE=15`, `command_timeout=30` |
| **Failure scenario** | 500 concurrent payments + activation + expiry workers can exhaust 15 connections. Long transactions (e.g. with C-1/C-2) increase risk. |
| **Remediation** | Tune pool for load; ensure no external calls inside transactions. |

---

### M-2: safe_remove retry blocks caller

| Field | Value |
|-------|-------|
| **File** | `vpn_utils.py` |
| **Function** | `safe_remove_vless_user_with_retry` |
| **Finding** | Up to 3 retries with 1s, 2s, 4s backoff (~7s total) can block activation/finalize error paths. Acceptable for correctness; may add latency. |
| **Remediation** | Monitor latency; consider fire-and-forget orphan cleanup with background retry. |

---

### M-3: XRAY_RECONCILIATION_ENABLED defaults false

| Field | Value |
|-------|-------|
| **Config** | `config.py`, `XRAY_RECONCILIATION_ENABLED` |
| **Finding** | Reconciliation off by default. Orphans accumulate until manually enabled. |
| **Remediation** | Document; enable in production and verify breaker/backoff. |

---

## 4. LOW

### L-1: Telegram successful_payment — no CryptoBot-style signature

| Field | Value |
|-------|-------|
| **File** | `app/handlers/payments/payments_messages.py` |
| **Finding** | Telegram payments use provider payload; CryptoBot uses HMAC. Different trust model; Telegram flow relies on provider correctness. |
| **Remediation** | Document trust boundaries; no change required if provider is trusted. |

---

### L-2: Trial expiry remove_vless_user not in transaction

| Field | Value |
|-------|-------|
| **File** | `trial_notifications.py` (line 460) |
| **Finding** | `remove_vless_user` runs outside DB transaction. Correct pattern. |
| **Remediation** | None. |

---

## SECTION 1 — Payment Security Audit

| Check | Result |
|-------|--------|
| Duplicate webhook → 2 subscriptions? | No. `UPDATE ... WHERE status='pending'` is atomic; second call gets `UPDATE 0`, raises. |
| Duplicate purchase_id race? | No. `purchase_id TEXT UNIQUE`; single winner. |
| Replay with same payload? | Signature verified with `hmac.compare_digest`. Invalid signature → 200 "unauthorized", no processing. |
| Amount tampering? | Expected price from DB (`price_kopecks`); tolerance ±1 RUB. |
| Money without key? | No. Phase 1 add_vless_user; Phase 2 tx; on tx failure, safe_remove prevents orphan. |
| Key without payment? | No. Key created only after `UPDATE pending_purchases SET status='paid'` succeeds. |
| Double extend? | No. `UPDATE ... WHERE status='pending'` and subscription logic prevent double activation. |

---

## SECTION 2 — UUID Lifecycle Integrity

| Check | Result |
|-------|--------|
| add_vless_user inside tx? | No. Two-phase everywhere. |
| remove_vless_user inside tx? | Yes. grant_access (4177–4191), admin_revoke (8218). |
| safe_remove for orphan cleanup? | Yes for ORPHAN_PREVENTED. No for reconciliation. |
| subscriptions.uuid UNIQUE? | Yes. `idx_subscriptions_uuid_unique` (partial). |
| Orphan after add → DB fail? | No. safe_remove_vless_user_with_retry in except. |
| DB active, Xray missing? | Possible (e.g. manual Xray reset). Reconciliation logs CRITICAL; no auto-recreate. |
| UUID in 2 users? | No. UNIQUE partial index. |

---

## SECTION 3 — Expiry & Renewal Race Conditions

| Check | Result |
|-------|--------|
| Expiry UPDATE WHERE uuid? | Yes. `check_and_disable`: `WHERE id=$1 AND telegram_id=$2 AND uuid=$3`. fast_expiry: `WHERE telegram_id=$1 AND uuid=$2`. |
| Renewal preserves UUID? | Yes. Active path extends `expires_at` only. |
| remove_vless_user idempotent? | Yes. 404 treated as success. |
| User loses access after renewal? | No. Advisory lock + `get_active_paid_subscription` check. |
| User keeps access after expiry? | Only if remove fails; reconciliation eventually cleans. |

---

## SECTION 4 — Reconciliation Safety

| Check | Result |
|-------|--------|
| Circuit breaker? | Yes. 3 failures → 10 min open. |
| Exponential backoff? | Yes. `await asyncio.sleep(min(60, 2 ** _failure_count))`. |
| Batch limit? | Yes. `BATCH_SIZE_LIMIT=100`. |
| safe_remove for orphans? | No. Uses `remove_vless_user` (no retry). |
| missing_in_xray CRITICAL? | Yes. Logged. |
| Auto-create missing? | No. |
| Delete valid UUID? | No. Orphans = xray_uuids - db_uuids only. |

---

## SECTION 5 — Xray API Security

| Check | Result |
|-------|--------|
| API key constant-time? | Yes. `hmac.compare_digest`. |
| XRAY_PORT validation? | Yes. Startup check; mismatch → RuntimeError. |
| config atomic write? | Yes. temp + rename. |
| config_file_lock? | Yes. |
| flow in link? | Yes. `xtls-rprx-vision`. |
| Private IP block? | Yes. vpn_utils enforces HTTPS, no private IPs. |

---

## SECTION 6 — Server-Aware Consistency

| Grep | Result |
|------|--------|
| xray_manager | 0 in code (docs only) |
| create_vless_user in bot | 0 |
| vless:// in bot | 0 (only test mock) |
| generate_vless_url | 0 |
| XRAY_SERVER_IP in bot | 0 (config check only) |
| remove_vless_user inside conn.transaction | 2 (grant_access, admin_revoke) |

---

## SECTION 7 — DoS & Pool Safety

| Check | Result |
|-------|--------|
| Pool exhaustion risk? | Yes if C-1/C-2 unfixed. External calls in tx hold connections. |
| Event loop block? | No. async throughout. |
| safe_remove blocks main flow? | Only in error path; acceptable. |
| Backpressure? | No explicit backpressure; relies on pool limits. |

---

## SECTION 8 — Production Verdict

| Item | Value |
|------|-------|
| **Production Ready** | CONDITIONAL |
| **Risk Score** | 35/100 |
| **Blocking issues** | C-1, C-2 (remove inside tx) |
| **Worst-case failure** | VPN API timeout during grant_access or admin_revoke → connection held in tx → pool exhaustion → cascading 503s |
| **Recovery effectiveness** | Good for orphans (safe_remove, reconciliation). Weak for pool exhaustion until C-1/C-2 fixed. |
| **Silent failure vectors** | Reconciliation remove failure leaves orphan until next run (10 min). |
| **Data corruption possibility** | Low. DB constraints and two-phase activation protect consistency. |
| **Payment integrity** | High. Idempotency, signature verification, amount from DB. |

---

## Remediation Priority

1. **C-1, C-2:** Refactor grant_access and admin_revoke to two-phase (remove outside tx).
2. **C-3:** Use safe_remove_vless_user_with_retry in reconciliation.
3. **H-1:** Document or tighten amount tolerance.
4. **M-1:** Review and tune DB pool for production load.
