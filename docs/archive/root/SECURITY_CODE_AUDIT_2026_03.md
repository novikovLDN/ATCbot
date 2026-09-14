# Security & Code Quality Audit Report — March 2026

## Audit Scope
Full codebase audit covering: security, correctness, duplicates, dead code, workers, payments, webhooks, VPN API, database layer, access controls.

---

## CRITICAL FINDINGS (Fixed)

### 1. Platega Webhook Empty-Credential Auth Bypass
**File**: `platega_service.py:153`
**Severity**: CRITICAL
**Issue**: If `PLATEGA_MERCHANT_ID` or `PLATEGA_SECRET` were empty strings, `hmac.compare_digest("", "")` returns `True`, bypassing authentication entirely. An attacker sending empty `X-MerchantId` and `X-Secret` headers could forge payment webhooks.
**Fix**: Added explicit check that server-side credentials are configured before comparison.

### 2. CryptoBot Webhook Missing Signature Header
**File**: `cryptobot_service.py:157`
**Severity**: HIGH
**Issue**: Missing check for empty signature header. If `crypto-pay-api-signature` header was absent, empty string was passed to `verify_webhook_signature()`. While `hmac.compare_digest` would likely reject it, explicit rejection is safer.
**Fix**: Added explicit check for non-empty signature before verification.

### 3. Dead Code: `database_legacy.py` (9,505 lines)
**Severity**: MEDIUM
**Issue**: 441 KB legacy database module with zero imports anywhere in the codebase. Attack surface without purpose.
**Fix**: Deleted.

### 4. Dead Code: `app/core/rate_limits.py` (277 lines)
**Severity**: LOW
**Issue**: Unused rate limiting registry. Zero imports found. Confusing duplicate alongside the active `rate_limit.py`.
**Fix**: Deleted.

### 5. Rate Limiter Wait Time Calculation Bug
**File**: `app/core/rate_limit.py:176`
**Severity**: LOW
**Issue**: `wait_seconds = int((1.0 - remaining) / bucket.refill_rate)` could produce negative or zero values when `remaining > 1.0` due to token refill in `get_remaining()` modifying state after `consume()` failed.
**Fix**: Simplified to `max(1, int(1.0 / bucket.refill_rate))` — always shows correct minimum wait time.

### 6. SSRF Validation Code Triplicated
**File**: `vpn_utils.py` (3 copies at lines ~209, ~560, ~673)
**Severity**: LOW (maintainability)
**Issue**: Identical 12-line SSRF protection block copy-pasted in `add_vless_user`, `update_vless_user`, and `remove_vless_user`.
**Fix**: Extracted to `_validate_api_url_security()` helper, called from all three functions.

---

## FINDINGS — NO ACTION REQUIRED (Correct)

### Security Architecture (GOOD)

| Area | Assessment |
|------|-----------|
| **SQL Injection** | All queries use parameterized `$1, $2` placeholders via asyncpg. Zero f-string SQL found. |
| **Webhook Secret Verification** | Telegram webhook validates `X-Telegram-Bot-Api-Secret-Token` header with timing-safe comparison via constant-time string equality. |
| **Admin Access Control** | Single admin ID checked via `config.ADMIN_TELEGRAM_ID`. Fail-closed design. |
| **Environment Isolation** | Config enforces `APP_ENV` prefix (`PROD_`, `STAGE_`, `LOCAL_`). Direct env var usage blocked at startup. |
| **HTTPS Enforcement** | VPN API URL validated for HTTPS in PROD. Private IP patterns blocked. |
| **Request Size Limits** | Both FastAPI middleware (1MB) and webhook handler (1MB) enforce body size limits. |
| **DDoS Protection** | Rate limiting middleware with per-user tracking, flood bans (60 req/60s → 5 min ban), memory eviction at 50K tracked users. |
| **Concurrency Control** | `asyncio.Semaphore(20)` limits concurrent update processing. |
| **Private Chat Filter** | Middleware rejects all non-private chat updates. Invisible/zero-width character filtering. |
| **Error Boundary** | `TelegramErrorBoundaryMiddleware` catches all handler exceptions except `CancelledError`. |
| **Secret Masking** | `sanitize_for_logging()` masks sensitive keys in log output. |
| **OpenAPI Disabled** | FastAPI docs/redoc/openapi endpoints disabled in production. |

### Worker Architecture (GOOD)

| Worker | Assessment |
|--------|-----------|
| **activation_worker** | Lock-protected iterations, 120s timeout, 15s max iteration time, startup jitter, cooperative yielding. |
| **auto_renewal** | `SELECT ... FOR UPDATE SKIP LOCKED`, atomic transactions, rollback on UUID regeneration, VIP/discount handling. |
| **fast_expiry_cleanup** | Batch processing (100), double-check before DB update, VPN API call outside DB transaction. |
| **reminders** | Idempotency window (30 min), notification service deduplication. |
| **All workers** | Structured start/end logging, `CancelledError` propagation, minimum safe sleep on failure, feature flag checks. |

### Payment Flow (GOOD)

| Area | Assessment |
|------|-----------|
| **Idempotency** | `check_payment_idempotency()` prevents double-processing. `purchase_id` is correlation key. |
| **Amount Validation** | ±1 RUB tolerance with `PaymentAmountMismatchError`. |
| **Payload Verification** | `telegram_id` in payload must match authenticated user. |
| **Provider Auth** | Platega: merchant_id + secret header verification. CryptoBot: HMAC-SHA256 signature. |

---

## DEAD CODE REMOVED

| File | Lines | Reason |
|------|-------|--------|
| `database_legacy.py` | 9,505 | Zero imports, fully replaced by `database/` package |
| `app/core/rate_limits.py` | 277 | Zero imports, replaced by `rate_limit.py` |
| `app/handlers/payments.py` | 0 | Empty stub, replaced by `payments/` package |
| `app/handlers/profile.py` | 0 | Empty stub, replaced by `user/profile.py` |
| `app/handlers/referrals.py` | 0 | Empty stub, replaced by `user/referrals.py` |
| `app/handlers/trials.py` | 0 | Empty stub, trials in services only |

**Total removed**: ~9,782 lines of dead code.

---

## ADVISORY FINDINGS (Not Fixed — Low Priority)

### 1. Root `handlers.py` Still Contains Active Code
`app/handlers/payments/callbacks.py:20` imports `show_payment_method_selection` from root `handlers.py`. This 1,175-line legacy file should be migrated to `app/handlers/` structure.

### 2. `broadcast_service.py` — Standalone Module
Used only by `app/handlers/admin/broadcast.py` via dynamic import. Works correctly but doesn't follow the `app/services/` pattern.

### 3. Memory Growth in Rate Limiter
`app/core/rate_limit.py` `TokenBucket` instances in `_buckets` dict grow without cleanup. For a single-instance bot this is fine, but could grow unbounded over months.

### 4. `auto_renewal.py` Manual Context Manager Usage
Uses `cm.__aenter__()` / `cm.__aexit__()` manually instead of `async with`. Fragile but functionally correct.

### 5. `_fire_and_forget()` in vpn_utils Uses Deprecated Pattern
`asyncio.get_event_loop()` is deprecated in Python 3.10+. Should use `asyncio.get_running_loop()`.
