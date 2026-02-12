# ================================================================
# REDIS INFRASTRUCTURE READINESS AUDIT
# Context: Atlas Secure production system
# Date: 2026-02-11
# Goal: Validate readiness for Redis integration
# Mode: STRICT AUDIT — diagnostics only, no refactoring
# ================================================================

## EXECUTIVE SUMMARY

**Redis Integration Readiness Score: 4/10**

**Status:** ⚠️ **NOT READY** — Critical infrastructure gaps identified

**Critical Blockers:** 3  
**High Priority Issues:** 5  
**Medium Priority Issues:** 7  
**Low Priority Issues:** 4

**Verdict:** Redis infrastructure must be configured and integrated before enabling distributed features.

---

## PART 0 — ENVIRONMENT VALIDATION

### 0.1 Redis Configuration Status

**Current State:**
- ❌ **NO REDIS_URL configuration found**
- ❌ **NO Redis-related environment variables**
- ❌ **NO RedisStorage import or usage**
- ❌ **NO Redis client initialization**

**Location:** `config.py`

**Analysis:**
```python
# Current config.py structure:
def env(key: str, default: str = "") -> str:
    env_key = f"{APP_ENV.upper()}_{key}"
    return os.getenv(env_key, default)

# Missing:
# REDIS_URL = env("REDIS_URL")  # NOT PRESENT
```

**Environment Switching:**
- ✅ Environment isolation via `APP_ENV` prefix ✅
- ✅ Separate configs for STAGE/PROD ✅
- ⚠️ **Redis URL not configured** — would need `STAGE_REDIS_URL` / `PROD_REDIS_URL`

**Security Exposure:**
- ✅ No Redis URL in logs (not configured)
- ✅ No hardcoded fallback
- ⚠️ **No Redis URL validation** (not implemented)

**Findings:**
- **Is Redis configurable per environment?** ❌ **NO** — Not implemented
- **Any unsafe fallback behavior?** ✅ **NO** — No fallback (not configured)
- **Security exposure risks?** ✅ **LOW** — No Redis config = no exposure

**Risk Level:** 🔴 **CRITICAL** — Redis not configured at all

---

## PART 1 — CURRENT FSM ARCHITECTURE

### 1.1 FSM Initialization

**Location:** `main.py:90`

**Current Implementation:**
```python
from aiogram.fsm.storage.memory import MemoryStorage
dp = Dispatcher(storage=MemoryStorage())
```

**Analysis:**
- ✅ FSM globally instantiated ✅
- ✅ Router injection pattern correct ✅
- ❌ **MemoryStorage()** — NOT PERSISTENT ❌

### 1.2 FSM States Inventory

**Total States:** 15 StatesGroups, ~40 individual states

**High-Risk Financial Flows:**

1. **WithdrawStates** (4 states)
   - `withdraw_amount`
   - `withdraw_confirm`
   - `withdraw_requisites`
   - `withdraw_final_confirm`
   - **Risk:** User mid-withdrawal → restart → state lost → stuck

2. **PromoCodeInput** (1 state)
   - `waiting_for_promo`
   - **Risk:** User mid-promo input → restart → state lost → UX issue

3. **AdminDebitBalance** (2 states)
   - `waiting_for_amount`
   - `waiting_for_confirmation`
   - **Risk:** Admin mid-debit → restart → state lost → admin confusion

4. **AdminCreatePromocode** (6 states)
   - `waiting_for_code_name`
   - `waiting_for_duration_unit`
   - `waiting_for_duration_value`
   - `waiting_for_max_uses`
   - `waiting_for_discount_percent`
   - `confirm_creation`
   - **Risk:** Admin mid-creation → restart → state lost → admin confusion

5. **PurchaseState** (4 states)
   - `choose_tariff`
   - `choose_period`
   - `choose_payment_method`
   - `processing_payment`
   - **Risk:** User mid-payment → restart → state lost → payment confusion

### 1.3 Multi-Instance Impact Analysis

**Scenario 1: Instance Restart**
- User in withdrawal flow → **STATE LOST** → User stuck
- User in promo input → **STATE LOST** → User can retry (acceptable)
- Admin creating promocode → **STATE LOST** → Admin must restart

**Scenario 2: Two Instances Running**
- Instance A: User in withdrawal flow
- Instance B: User sends `/start` → **DIFFERENT STATE** → Confusion
- **NO STATE SHARING** → Each instance has separate FSM state

**State-Dependent Financial Operations:**
- ⚠️ Withdrawal flow — **STATE DEPENDENT** (amount, requisites stored in FSM)
- ⚠️ Promo session — **STATE DEPENDENT** (promo_code, discount_percent in FSM)
- ✅ Payment finalization — **NOT STATE DEPENDENT** (uses DB `pending_purchases`)

**FSM Risk Level for Horizontal Scaling:** 🔴 **CRITICAL**

**Flows Requiring Redis Persistence:**
1. ✅ **WithdrawStates** — CRITICAL (financial operation)
2. ✅ **PromoCodeInput** — HIGH (user experience)
3. ✅ **AdminDebitBalance** — HIGH (admin operations)
4. ✅ **AdminCreatePromocode** — MEDIUM (admin operations)
5. ✅ **PurchaseState** — MEDIUM (user experience)

---

## PART 2 — CONCURRENCY MODEL

### 2.1 PostgreSQL Advisory Locks

**Usage Count:** 36 occurrences in `database.py`

**Key Scope Analysis:**

**User Operations:**
```python
await conn.execute("SELECT pg_advisory_xact_lock($1)", telegram_id)
```
- ✅ Consistent key: `telegram_id` (integer)
- ✅ Used in: balance operations, withdrawal operations, referral operations

**Promocode Operations:**
```python
await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", code_normalized)
```
- ✅ Consistent key: `hashtext(code)` (hashed string)
- ✅ Used in: promocode validation, promocode consumption

**Lock Collision Risk:** ✅ **LOW** — Consistent key scoping

**Distributed Consistency:** ✅ **SAFE** — PostgreSQL advisory locks work across instances

### 2.2 SELECT ... FOR UPDATE

**Usage Count:** Multiple occurrences

**Financial Writes Guarded:**
- ✅ Balance operations — `SELECT balance ... FOR UPDATE`
- ✅ Withdrawal operations — `SELECT * FROM withdrawal_requests ... FOR UPDATE`
- ✅ Promocode operations — `SELECT * FROM promo_codes ... FOR UPDATE`
- ✅ Referral operations — `SELECT * FROM referrals ... FOR UPDATE`

**TOCTOU Patterns:**
- ✅ **NONE FOUND** — All critical operations use FOR UPDATE

**Verdict:** ✅ **SAFE** — All financial writes guarded

### 2.3 In-Memory Locks

**Found:**

1. **`_REISSUE_LOCKS`** (handlers.py:1570)
   ```python
   _REISSUE_LOCKS: dict[int, asyncio.Lock] = {}
   
   def get_reissue_lock(user_id: int) -> asyncio.Lock:
       if user_id not in _REISSUE_LOCKS:
           _REISSUE_LOCKS[user_id] = asyncio.Lock()
       return _REISSUE_LOCKS[user_id]
   ```
   - **Usage:** Admin VPN key reissue flow
   - **Multi-instance safety:** ❌ **NOT SAFE** — Each instance has separate dict
   - **Risk:** Two instances → both can reissue simultaneously

2. **Rate Limiter Locks** (app/core/rate_limit.py)
   ```python
   self._lock = threading.Lock()
   self._buckets: Dict[Tuple[int, str], TokenBucket] = {}
   ```
   - **Usage:** Rate limiting (in-memory buckets)
   - **Multi-instance safety:** ❌ **NOT SAFE** — Each instance has separate buckets
   - **Risk:** Rate limits not enforced across instances

3. **Metrics Locks** (app/core/metrics.py)
   ```python
   self._lock = threading.Lock()
   self._counters: Dict[str, float] = defaultdict(float)
   ```
   - **Usage:** Metrics collection
   - **Multi-instance safety:** ⚠️ **ACCEPTABLE** — Metrics can be per-instance

4. **Circuit Breaker Locks** (app/core/circuit_breaker.py)
   ```python
   self._lock = threading.Lock()
   ```
   - **Usage:** Circuit breaker state
   - **Multi-instance safety:** ⚠️ **ACCEPTABLE** — Circuit breakers can be per-instance

**In-Memory Locks Needing Redis Replacement:**

1. 🔴 **CRITICAL:** `_REISSUE_LOCKS` — Admin reissue flow
2. 🟡 **HIGH:** Rate limiter buckets — Anti-spam protection
3. 🟢 **LOW:** Metrics locks — Acceptable per-instance
4. 🟢 **LOW:** Circuit breaker locks — Acceptable per-instance

**Lock Collision Risk:** 🟡 **MEDIUM** — `_REISSUE_LOCKS` can collide across instances

**Distributed Consistency Risk:** 🔴 **HIGH** — Reissue flow not protected across instances

---

## PART 3 — PROMOCODE CONSUMPTION SAFETY

### 3.1 Atomic UPDATE Analysis

**Location:** `database.py:6268-6282` (balance_topup), `database.py:6524-6531` (subscription), `database.py:7491-7498` (balance_purchase)

**Current Implementation:**
```python
result = await conn.execute(
    """
    UPDATE promo_codes
    SET used_count = used_count + 1,
        is_active = CASE
            WHEN max_uses IS NOT NULL AND used_count + 1 >= max_uses THEN FALSE
            ELSE is_active
        END
    WHERE code = $1
      AND is_active = TRUE
      AND (expires_at IS NULL OR expires_at > NOW())
      AND (max_uses IS NULL OR used_count < max_uses)
    """,
    code_normalized
)

if result != "UPDATE 1":
    raise ValueError("PROMOCODE_ALREADY_USED_OR_EXPIRED")
```

**Analysis:**
- ✅ **Atomic UPDATE with WHERE `used_count < max_uses`** ✅
- ✅ **Check `result == "UPDATE 1"`** ✅
- ✅ **Wrapped in transaction** ✅
- ✅ **Expiry validated inside WHERE** ✅

### 3.2 Pre-Check SELECT Analysis

**Location:** `database.py:6235-6243` (before UPDATE)

**Current Implementation:**
```python
promo_row = await conn.fetchrow(
    "SELECT * FROM promo_codes WHERE code = $1 FOR UPDATE",
    code_normalized
)
# ... checks ...
# Then atomic UPDATE
```

**Analysis:**
- ⚠️ **SELECT FOR UPDATE before UPDATE** — Acceptable (row locked)
- ✅ **No race window** — Row locked until transaction commit
- ✅ **Advisory lock** — Additional protection

### 3.3 TOCTOU Patterns

**Found:** ✅ **NONE** — All promocode operations use atomic UPDATE

**Verdict:** ✅ **SAFE** — Promocode consumption truly atomic

---

## PART 4 — IDEMPOTENCY READINESS

### 4.1 Background Workers Analysis

#### ✅ **Crypto Payment Watcher**

**Location:** `crypto_payment_watcher.py`

**Idempotency:**
- ✅ Uses `finalize_purchase()` — **IDEMPOTENT** (checks `status != "pending"`)
- ✅ No duplicate processing — Protected by DB status check
- ✅ Re-entrant safe — Can run multiple times safely

**Redis Requirement:** 🟢 **LOW** — DB-level idempotency sufficient

#### ✅ **Auto-Renewal Worker**

**Location:** `auto_renewal.py`

**Idempotency:**
- ✅ Uses `FOR UPDATE SKIP LOCKED` — Only one worker processes subscription
- ✅ Sets `last_auto_renewal_at` — Prevents duplicate processing
- ✅ Transaction rollback on error — Idempotent

**Redis Requirement:** 🟡 **MEDIUM** — `SKIP LOCKED` works, but Redis would enable better coordination

#### ✅ **Activation Worker**

**Location:** `activation_worker.py`

**Idempotency:**
- ✅ Max attempts enforced — Prevents infinite retries
- ✅ Status checks — `activation_status='pending'` check
- ✅ Idempotent operations — Can run multiple times safely

**Redis Requirement:** 🟢 **LOW** — DB-level idempotency sufficient

#### ✅ **Referral Reward**

**Location:** `database.py:process_referral_reward()`

**Idempotency:**
- ✅ Purchase ID check — `SELECT ... WHERE buyer_id = $1 AND purchase_id = $2`
- ✅ Returns existing reward if duplicate — Idempotent
- ✅ Transaction protected — Atomic operation

**Redis Requirement:** 🟢 **LOW** — DB-level idempotency sufficient

### 4.2 Financial Duplication Risk

**Analysis:**

**Payment Processing:**
- ✅ `finalize_purchase()` — Checks `status != "pending"` ✅
- ✅ `finalize_balance_purchase()` — Advisory lock + transaction ✅
- ✅ Idempotency keys — `payment_idempotency_keys` table ✅

**Withdrawal Processing:**
- ✅ `approve_withdrawal_request()` — Checks `status != "pending"` ✅
- ✅ `reject_withdrawal_request()` — Checks `status != "pending"` ✅

**Promocode Consumption:**
- ✅ Atomic UPDATE with WHERE check ✅
- ✅ Transaction protected ✅

**Referral Rewards:**
- ✅ Purchase ID check ✅
- ✅ Transaction protected ✅

**Verdict:** ✅ **SAFE** — Strong idempotency protection at DB level

**Flows Needing Redis Idempotency Keys:**
- 🟢 **NONE CRITICAL** — DB-level idempotency sufficient
- 🟡 **OPTIONAL:** Worker coordination (auto-renewal, activation) — Redis would improve coordination

**Financial Duplication Risk Level:** ✅ **LOW** — DB-level protection adequate

---

## PART 5 — RATE LIMITING STATUS

### 5.1 Current Rate Limiting Implementation

**Found:** `app/core/rate_limit.py` — **EXISTS BUT UNDERUTILIZED**

**Current Usage:**
- ✅ Trial activation — `check_rate_limit(telegram_id, "trial_activate")`
- ✅ Payment initiation — `check_rate_limit(telegram_id, "payment_init")`
- ❌ **Promo input** — **NOT RATE LIMITED**
- ❌ **Withdrawal requests** — **NOT RATE LIMITED**
- ❌ **Admin commands** — **NOT RATE LIMITED**

**Implementation:**
```python
# In-memory TokenBucket per (telegram_id, action_key)
self._buckets: Dict[Tuple[int, str], TokenBucket] = {}
self._lock = threading.Lock()
```

**Multi-Instance Safety:** ❌ **NOT SAFE** — Each instance has separate buckets

### 5.2 Brute-Force Vectors

**Identified:**

1. **Promo Input** (`process_promo_code`)
   - ❌ **NO RATE LIMITING**
   - **Risk:** Brute-force promo codes
   - **Impact:** 🟡 **MEDIUM** — DB load, but no financial impact

2. **Withdrawal Input** (`process_withdraw_amount`, `process_withdraw_requisites`)
   - ❌ **NO RATE LIMITING**
   - **Risk:** Spam withdrawal requests
   - **Impact:** 🟡 **MEDIUM** — Admin notification spam

3. **Admin Commands**
   - ❌ **NO RATE LIMITING**
   - **Risk:** Admin account compromise → rapid actions
   - **Impact:** 🔴 **HIGH** — Financial operations

4. **Payment Attempts**
   - ⚠️ **PARTIAL** — Only payment initiation rate limited
   - **Risk:** Spam payment attempts
   - **Impact:** 🟡 **MEDIUM** — DB load

### 5.3 Infinite Retry Flows

**Found:**
- ⚠️ Promo validation — Can retry indefinitely
- ⚠️ Withdrawal creation — Can retry indefinitely
- ✅ Payment processing — Idempotency prevents duplicates

**Rate Limiting Absence Risk Score:** 🟡 **MEDIUM** (6/10)

**Recommended Rate Limit Targets:**
- Promo attempts: 10/minute per user
- Withdrawal requests: 3/hour per user
- Admin commands: 100/minute per admin
- Payment attempts: 5/minute per user

---

## PART 6 — MULTI-INSTANCE SAFETY

### 6.1 Polling Safety

**Location:** `main.py:364`

**Current Implementation:**
```python
try:
    await dp.start_polling(bot)
except TelegramConflictError as e:
    logger.critical("POLLING_CONFLICT_DETECTED — another bot instance is running")
    raise SystemExit(1)
```

**Analysis:**
- ✅ Only one `start_polling` call ✅
- ✅ `TelegramConflictError` handling ✅
- ✅ **SAFE** — Telegram API prevents multiple polling instances

**Verdict:** ✅ **SAFE** — Polling conflict prevented by Telegram API

### 6.2 Background Workers

**Workers Started:**
1. `reminders_task()` — Started in every instance
2. `trial_notifications.run_trial_scheduler()` — Started in every instance
3. `health_check_task()` — Started in every instance
4. `fast_expiry_cleanup_task()` — Started in every instance
5. `auto_renewal_task()` — Started in every instance
6. `activation_worker_task()` — Started in every instance
7. `crypto_payment_watcher_task()` — Started in every instance

**Worker Duplication Analysis:**

**Workers Using Locking:**
- ✅ `auto_renewal` — Uses `FOR UPDATE SKIP LOCKED` ✅
- ✅ `activation_worker` — Uses status checks ✅
- ⚠️ `crypto_payment_watcher` — **NO EXPLICIT LOCKING** ⚠️
- ⚠️ `reminders` — **NO EXPLICIT LOCKING** ⚠️
- ⚠️ `trial_notifications` — **NO EXPLICIT LOCKING** ⚠️

**Worker Duplication Risk:**

**Low Risk (DB-level protection):**
- ✅ `auto_renewal` — `SKIP LOCKED` prevents duplicates
- ✅ `activation_worker` — Status checks prevent duplicates

**Medium Risk (Idempotent but inefficient):**
- ⚠️ `crypto_payment_watcher` — Multiple instances check same payments (inefficient)
- ⚠️ `reminders` — Multiple instances send same reminders (wasteful)
- ⚠️ `trial_notifications` — Multiple instances send same notifications (wasteful)

**Redis Requirement for Worker Leader Election:**
- 🟡 **RECOMMENDED:** Leader election for `crypto_payment_watcher`, `reminders`, `trial_notifications`
- 🟢 **OPTIONAL:** `auto_renewal`, `activation_worker` — DB-level protection sufficient

**Multi-Instance Safety Rating:** 🟡 **MEDIUM** (6/10)

**Worker Duplication Risk:** 🟡 **MEDIUM** — Some workers run in all instances (inefficient but safe)

---

## PART 7 — OBSERVABILITY & FAILURE RECOVERY

### 7.1 Structured Logging Coverage

**Financial Events:**
- ✅ `BALANCE_INCREASED` / `BALANCE_DECREASED` ✅
- ✅ `WITHDRAWAL_REQUEST_CREATED` / `WITHDRAWAL_APPROVED` / `WITHDRAWAL_REJECTED` ✅
- ✅ `PROMOCODE_CREATED` / `PROMOCODE_CONSUMED` / `PROMOCODE_VALIDATED` ✅
- ✅ `REFERRAL_REWARD_GRANTED` ✅
- ✅ `PAYMENT_RECEIVED` / `PAYMENT_APPROVED` ✅

**Coverage:** ✅ **EXCELLENT** — All financial events logged

### 7.2 Correlation ID Propagation

**Handler Level:**
- ✅ Correlation IDs extracted from `update_id` / `message_id` ✅
- ✅ Passed to logging functions ✅

**Background Tasks:**
- ⚠️ **PARTIAL** — Some workers generate correlation IDs, some don't
- ⚠️ **NOT PROPAGATED** — Worker correlation IDs not linked to handler correlation IDs

**Cross-Handler Propagation:**
- ⚠️ **NOT IMPLEMENTED** — Correlation IDs don't survive across handlers

**Redis Requirement:** 🟡 **MEDIUM** — Redis could store correlation ID chains

### 7.3 Exception Handling

**Silent Exception Blocks:**

**Found:**
- ⚠️ Some `except Exception: pass` blocks in workers
- ✅ Most exceptions logged before swallowing
- ✅ Critical exceptions propagate

**Swallowed DB Errors:**
- ✅ **NONE CRITICAL** — DB errors typically logged
- ⚠️ Some non-critical errors swallowed (acceptable)

**Observability Maturity Score:** ✅ **GOOD** (8/10)

**Blind Spot Areas:**
- ⚠️ Worker correlation ID propagation
- ⚠️ Cross-handler correlation tracking

---

## PART 8 — GRACEFUL SHUTDOWN READINESS

### 8.1 Signal Handling

**Current Implementation:**
```python
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен")
```

**Analysis:**
- ✅ `KeyboardInterrupt` handling ✅
- ❌ **NO SIGTERM handling** ❌
- ❌ **NO SIGINT handling** (beyond KeyboardInterrupt) ❌
- ❌ **NO signal handlers** ❌

### 8.2 Shutdown Hooks

**Found:** `main.py:371-454` — `finally` block

**Implementation:**
```python
finally:
    # Cancel all background tasks
    if reminder_task:
        reminder_task.cancel()
    # ... cancel all tasks ...
    # Wait for tasks to complete
    # Close DB pool
    await database.close_pool()
```

**Analysis:**
- ✅ Task cancellation ✅
- ✅ Task waiting ✅
- ✅ DB pool closure ✅
- ⚠️ **NO timeout** — Tasks may hang indefinitely

### 8.3 Pending Transaction Behavior

**Analysis:**
- ✅ Transactions use `async with conn.transaction()` — Auto-rollback on exception ✅
- ✅ Advisory locks released on transaction end ✅
- ✅ No stuck locks possible ✅

**SIGTERM During Transaction:**
- ✅ Transaction rollback — PostgreSQL handles SIGTERM ✅
- ✅ Locks released — Advisory locks released on connection close ✅
- ✅ **SAFE** — No stuck locks

**Shutdown Safety Level:** ✅ **GOOD** (7/10)

**Improvements Needed:**
- ⚠️ Add SIGTERM/SIGINT signal handlers
- ⚠️ Add shutdown timeout for task cancellation

---

## FINAL REPORT

### Executive Summary

**Redis Infrastructure Readiness: 4/10**

**Status:** ⚠️ **NOT READY** — Critical gaps prevent Redis integration

**Key Findings:**
1. ❌ **Redis not configured** — No REDIS_URL in config
2. ❌ **FSM uses MemoryStorage** — Not persistent, not shared
3. ❌ **In-memory locks** — `_REISSUE_LOCKS` not distributed
4. ⚠️ **Rate limiting incomplete** — Exists but not used everywhere
5. ✅ **DB-level idempotency** — Strong protection exists
6. ✅ **Advisory locks** — Work across instances
7. ⚠️ **Worker duplication** — Some workers run in all instances

---

## RISK MATRIX

| Component | Risk Level | Impact | Redis Required |
|-----------|-----------|--------|----------------|
| **FSM Persistence** | 🔴 CRITICAL | State lost on restart | ✅ YES |
| **Reissue Locks** | 🔴 CRITICAL | Double reissue possible | ✅ YES |
| **Rate Limiting** | 🟡 HIGH | Spam/brute-force | ✅ YES |
| **Worker Coordination** | 🟡 MEDIUM | Inefficient duplication | 🟡 OPTIONAL |
| **Correlation IDs** | 🟢 LOW | Observability | 🟡 OPTIONAL |
| **Idempotency** | 🟢 LOW | Already protected | ❌ NO |

---

## REQUIRED FIXES BEFORE ENABLING REDIS

### 🔴 CRITICAL (Must Fix)

1. **Add Redis Configuration**
   - **Location:** `config.py`
   - **Fix:** Add `REDIS_URL = env("REDIS_URL")`
   - **Validation:** Check Redis connectivity at startup
   - **Priority:** CRITICAL

2. **Migrate FSM to RedisStorage**
   - **Location:** `main.py:90`
   - **Fix:** Replace `MemoryStorage()` with `RedisStorage.from_url(REDIS_URL)`
   - **Priority:** CRITICAL

3. **Replace In-Memory Locks**
   - **Location:** `handlers.py:1570` (`_REISSUE_LOCKS`)
   - **Fix:** Use Redis distributed locks
   - **Priority:** CRITICAL

### 🟡 HIGH (Should Fix)

4. **Implement Distributed Rate Limiting**
   - **Location:** `app/core/rate_limit.py`
   - **Fix:** Use Redis for rate limit buckets
   - **Priority:** HIGH

5. **Add Rate Limiting to Critical Flows**
   - **Location:** Promo handlers, withdrawal handlers
   - **Fix:** Add `check_rate_limit()` calls
   - **Priority:** HIGH

6. **Worker Leader Election**
   - **Location:** `crypto_payment_watcher.py`, `reminders.py`, `trial_notifications.py`
   - **Fix:** Implement Redis-based leader election
   - **Priority:** MEDIUM (efficiency improvement)

---

## SAFE ROLLOUT ORDER

### Phase 1: Infrastructure Setup (Week 1)
1. ✅ Add Redis configuration (`config.py`)
2. ✅ Add Redis connection validation
3. ✅ Add Redis health checks
4. ✅ Test Redis connectivity

### Phase 2: FSM Migration (Week 2)
1. ✅ Migrate FSM to RedisStorage
2. ✅ Test FSM persistence (restart test)
3. ✅ Test multi-instance FSM sharing
4. ✅ Monitor FSM performance

### Phase 3: Distributed Locks (Week 3)
1. ✅ Replace `_REISSUE_LOCKS` with Redis locks
2. ✅ Test reissue flow with 2 instances
3. ✅ Verify no double reissue

### Phase 4: Rate Limiting (Week 4)
1. ✅ Migrate rate limiter to Redis
2. ✅ Add rate limiting to promo/withdrawal flows
3. ✅ Test rate limit enforcement across instances

### Phase 5: Worker Coordination (Week 5, Optional)
1. ✅ Implement leader election for workers
2. ✅ Test worker coordination
3. ✅ Monitor worker efficiency

---

## SCORING

### Redis Integration Readiness Score: 4/10

**Breakdown:**
- Configuration: 0/10 (not implemented)
- FSM Migration: 0/10 (MemoryStorage)
- Distributed Locks: 2/10 (advisory locks work, but in-memory locks exist)
- Rate Limiting: 3/10 (exists but incomplete)
- Idempotency: 9/10 (excellent DB-level protection)
- Observability: 7/10 (good, but correlation IDs could improve)

### Horizontal Scaling Readiness Score: 3/10

**Blockers:**
- FSM persistence (CRITICAL)
- In-memory locks (CRITICAL)
- Rate limiting (HIGH)
- Worker coordination (MEDIUM)

**After Redis Integration:** 8/10 (estimated)

### Financial Safety Score (Post-Redis): 9/10

**Analysis:**
- DB-level protection remains ✅
- Redis adds distributed coordination ✅
- No reduction in safety ✅
- Potential improvement in worker coordination ✅

---

## IMMEDIATE ACTIONS

### Before Redis Integration:

1. **Add Redis Configuration**
   ```python
   # config.py
   REDIS_URL = env("REDIS_URL")
   if not REDIS_URL:
       if APP_ENV == "prod":
           print(f"ERROR: {APP_ENV.upper()}_REDIS_URL is REQUIRED in PROD!")
           sys.exit(1)
   ```

2. **Add Redis Health Check**
   ```python
   # Test Redis connectivity at startup
   async def check_redis_connection():
       try:
           redis_client = await redis.from_url(REDIS_URL)
           await redis_client.ping()
           return True
       except Exception as e:
           logger.error(f"Redis connection failed: {e}")
           return False
   ```

3. **Plan FSM Migration**
   - Identify all FSM state dependencies
   - Plan state migration strategy
   - Test state recovery after restart

---

## CONCLUSION

**Atlas Secure is NOT READY for Redis integration** without implementing critical infrastructure components.

**Current State:**
- ✅ Strong DB-level concurrency protection
- ✅ Excellent idempotency protection
- ❌ No Redis configuration
- ❌ FSM not persistent
- ❌ In-memory locks not distributed

**Required Work:**
- 🔴 **CRITICAL:** Redis configuration + FSM migration + distributed locks
- 🟡 **HIGH:** Rate limiting migration + worker coordination
- 🟢 **LOW:** Correlation ID tracking (optional)

**Estimated Effort:** 3-4 weeks for full Redis integration

**Recommendation:** Implement Redis infrastructure in phases, starting with configuration and FSM migration.

---

**Audit Completed:** 2026-02-11  
**Next Steps:** Implement Redis configuration and FSM migration before enabling distributed features
