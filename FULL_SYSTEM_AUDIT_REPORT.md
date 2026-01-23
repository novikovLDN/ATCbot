# FULL SYSTEM AUDIT REPORT
**Project:** ATCbot  
**Branch:** stage  
**Date:** 2024  
**Type:** READ-ONLY AUDIT (NO CODE CHANGES)

---

## EXECUTIVE SUMMARY

### PROD-Readiness: **WITH CONDITIONS**

The system is architecturally sound with proper transaction boundaries, idempotency mechanisms, and degraded mode handling. However, several HIGH and MEDIUM risks require attention before production deployment.

### Top 5 Risks (Ranked)

1. **CRITICAL:** Dead flags in DB schema (smart_notif_*, trial_notif_18h/30h/42h/54h) — potential confusion, no cleanup strategy
2. **HIGH:** XRAY API dependency — if VPN API fails, subscriptions cannot be activated even after payment
3. **HIGH:** Auto-renewal race condition window — 12-hour protection may overlap with concurrent scheduler runs
4. **MEDIUM:** Notification idempotency gaps — some notifications (trial expiration, referral rewards) lack idempotency
5. **MEDIUM:** Scheduler overlap risk — multiple schedulers run independently, potential duplicate execution on restart

---

## DETAILED FINDINGS

### PHASE 1 — ARCHITECTURE & FLOW

#### Entry Points
- **Telegram Updates:** `main.py:264` — `dp.start_polling(bot)` handles all Telegram events
- **Webhooks:** None (Telegram Payments uses polling, CryptoBot uses polling watcher)
- **Schedulers:** 6 background tasks (reminders, trial notifications, auto-renewal, crypto watcher, fast cleanup, healthcheck)

#### Core Domains
1. **Payments:** `finalize_purchase()`, `finalize_balance_purchase()`, `finalize_balance_topup()`
2. **Subscriptions:** `grant_access()` — single source of truth for VPN access
3. **Referrals:** `process_referral_reward()` — cashback calculation and distribution
4. **Notifications:** Multiple modules (reminders, trial_notifications, auto_renewal, handlers)

#### DB Access Patterns
- **Connection Pool:** `asyncpg.Pool` with min_size=1, max_size=10
- **Transaction Boundaries:** All financial operations use `async with conn.transaction()`
- **Degraded Mode:** `DB_READY` flag gates critical operations

#### Single Points of Failure
1. **XRAY API** (`vpn_utils.py`) — if unavailable, no new subscriptions can be activated
   - **Risk Level:** HIGH
   - **Impact:** Payment succeeds but user gets no VPN access
   - **Mitigation:** Partial — error logged, but user sees generic error

2. **Database Connection Pool** — single pool for all operations
   - **Risk Level:** MEDIUM
   - **Impact:** If pool exhausted, all operations block
   - **Mitigation:** Degraded mode exists, but pool exhaustion not handled

3. **FSM State Storage** — `MemoryStorage()` — lost on restart
   - **Risk Level:** LOW
   - **Impact:** User loses purchase flow state, must restart
   - **Mitigation:** Acceptable for MVP

#### Tight Couplings
1. **grant_access() → vpn_utils** — direct dependency, no abstraction
2. **finalize_purchase() → grant_access()** — hard coupling, cannot test separately
3. **Schedulers → database.get_pool()** — all schedulers depend on DB being ready

#### Implicit Assumptions
1. **XRAY_API_URL/XRAY_API_KEY** — assumed available for subscription activation
2. **DB_READY flag** — assumed correctly set after `init_db()`
3. **Telegram API** — assumed always available (no retry logic for bot.send_message)

---

### PHASE 2 — PAYMENTS & MONEY SAFETY

#### Payment Flows Enumerated

**1. Card Payment (Telegram Payments)**
- **Entry:** `handlers.py:3878` — `process_successful_payment()`
- **Transaction Start:** `finalize_purchase()` called (line 4263)
- **Transaction End:** Payment record created, subscription activated, notification sent
- **DB Failure Mid-Flow:** Transaction rollback, user sees error, payment already charged by Telegram
- **Idempotency:** ✅ YES — `pending_purchase.status` check prevents double processing
- **User Sees on Failure:** Generic error message (line 4306)
- **Risk:** ⚠️ User charged but no access if VPN API fails AFTER payment commit

**2. Balance Purchase**
- **Entry:** `handlers.py:2788` — `callback_pay_balance()`
- **Transaction Start:** `finalize_balance_purchase()` called (line 2914)
- **Transaction End:** Balance debited, subscription activated, payment record created
- **DB Failure Mid-Flow:** Transaction rollback, balance restored
- **Idempotency:** ✅ YES — `payment_id` check via `notification_sent` flag
- **User Sees on Failure:** Error message (line 2953)
- **Risk:** ⚠️ Balance debited but no access if VPN API fails AFTER DB commit

**3. Balance Topup**
- **Entry:** `handlers.py:3922` — `process_successful_payment()` (balance branch)
- **Transaction Start:** `finalize_balance_topup()` called (line 3952)
- **Transaction End:** Balance credited, payment record created
- **DB Failure Mid-Flow:** Transaction rollback, user charged but balance not credited
- **Idempotency:** ✅ YES — `payment_id` check via `notification_sent` flag
- **User Sees on Failure:** Error message (line 3964)
- **Risk:** ⚠️ User charged but balance not credited (requires manual fix)

**4. Auto-Renewal**
- **Entry:** `auto_renewal.py:28` — `process_auto_renewals()`
- **Transaction Start:** `finalize_balance_purchase()` called (line 147)
- **Transaction End:** Balance debited, subscription extended, payment record created
- **DB Failure Mid-Flow:** Transaction rollback, subscription not extended
- **Idempotency:** ✅ YES — `last_auto_renewal_at` check prevents double renewal
- **User Sees on Failure:** Silent (no notification on failure)
- **Risk:** ⚠️ Silent failure — user may not know renewal failed

**5. Crypto Payment (CryptoBot)**
- **Entry:** `crypto_payment_watcher.py:17` — `check_crypto_payments()`
- **Transaction Start:** `finalize_purchase()` called (line 95)
- **Transaction End:** Payment record created, subscription activated
- **DB Failure Mid-Flow:** Transaction rollback, crypto payment already confirmed
- **Idempotency:** ✅ YES — `pending_purchase.status` check
- **User Sees on Failure:** Silent (watcher logs error, no user notification)
- **Risk:** ⚠️ Crypto payment confirmed but no access if VPN API fails

#### Money Safety Analysis

**Possible Double Charge:**
- ❌ **NO** — All payment flows use idempotency checks (`pending_purchase.status`, `notification_sent` flag)
- ✅ **SAFE**

**Possible Charge Without Access:**
- ⚠️ **YES** — If VPN API fails AFTER payment commit but BEFORE VPN key generation
  - **Location:** `database.py:3071` — `vpn_utils.add_vless_user()` called after payment committed
  - **Risk Level:** HIGH
  - **Impact:** User pays, gets error message, no VPN access
  - **Mitigation:** Manual admin intervention required

**Possible Access Without Charge:**
- ❌ **NO** — All access grants require payment record (`grant_access()` called only after payment)
- ✅ **SAFE**

---

### PHASE 3 — SUBSCRIPTIONS & ACCESS CONTROL

#### grant_access() Behavior

**New Subscription:**
- Creates UUID via `vpn_utils.add_vless_user()`
- Sets `subscription_start = now()`, `subscription_end = now() + duration`
- Returns `action: "new_issuance"`

**Renewal:**
- **Condition:** `subscription exists AND status == "active" AND expires_at > now() AND uuid IS NOT NULL`
- Does NOT call VPN API
- Only extends `expires_at`
- Returns `action: "renewal"`

**Degraded Mode Behavior:**
- If `VPN_ENABLED == False`, `grant_access()` raises exception
- **Location:** `database.py:3027`
- **Impact:** Subscription cannot be activated even if payment succeeds
- **Risk Level:** HIGH

**XRAY Dependency:**
- **Critical Path:** `database.py:2678` — `vpn_utils.add_vless_user()` must succeed
- **Failure Handling:** Exception raised, transaction rollback
- **User Impact:** Payment succeeds but subscription not activated

#### Invariants Verified

**✅ Subscription cannot exist without payment:**
- All subscriptions created via `grant_access()` called from `finalize_purchase()` or `finalize_balance_purchase()`
- Both functions require payment record creation

**✅ Renewal cannot generate new VPN key:**
- `grant_access()` checks `uuid IS NOT NULL` before renewal path
- Renewal path does NOT call `vpn_utils.add_vless_user()`

**⚠️ Expired users cannot access VPN:**
- `fast_expiry_cleanup.py` removes UUID from VPN API
- **Gap:** If cleanup task fails, expired users may retain access until manual cleanup

#### Edge Cases

**Long-Lived Subscriptions:**
- ✅ Handled — `expires_at` stored as TIMESTAMP, no overflow risk

**Manual Admin Grants:**
- ✅ Handled — `grant_access()` with `source='admin'` creates new UUID

**Overlapping Grants:**
- ⚠️ **Risk:** If admin grants access while payment processing, both may create UUIDs
- **Location:** `database.py:2885` — renewal check may miss concurrent admin grant
- **Risk Level:** MEDIUM

---

### PHASE 4 — NOTIFICATIONS & UX CONSISTENCY

#### Remaining Notification Types

**Payment Notifications:**
1. Payment success (card) — ✅ Idempotent (`notification_sent` flag)
2. Balance purchase success — ✅ Idempotent (`notification_sent` flag)
3. Balance topup success — ✅ Idempotent (`notification_sent` flag)
4. Auto-renewal success — ✅ Idempotent (`notification_sent` flag)
5. Payment rejected — ❌ Not idempotent (admin-triggered, low risk)

**Trial Notifications:**
1. Trial activation — ❌ Not idempotent (sent once per user, low risk)
2. Trial expiration — ❌ Not idempotent (uses `trial_completed_sent` flag, but flag may be reset on error)
3. Trial reminders (6h, 48h, final 6h) — ✅ Idempotent (DB flags: `trial_notif_6h_sent`, `trial_notif_60h_sent`, `trial_notif_71h_sent`)

**Reminder Notifications:**
1. Reminder 3d — ✅ Idempotent (`reminder_3d_sent` flag)
2. Reminder 24h — ✅ Idempotent (`reminder_24h_sent` flag)
3. Reminder 3h — ✅ Idempotent (`reminder_3h_sent` flag)
4. Reminder 6h (admin grants) — ✅ Idempotent (`reminder_6h_sent` flag)

**Referral Notifications:**
1. Referral cashback — ❌ Not idempotent (no flag, can be sent multiple times)

**Admin Notifications:**
1. Degraded mode alert — ✅ Idempotent (singleton guard)
2. Recovery notification — ✅ Idempotent (singleton guard)

#### Notification Timing

**✅ All notifications sent AFTER transaction commit:**
- Payment notifications sent after `finalize_purchase()` completes
- Trial notifications sent after DB writes complete
- Reminders sent after flag updates

**⚠️ Exception:** Referral cashback notification sent during transaction (line 3956 in handlers.py)
- **Risk:** If notification send fails, transaction may rollback
- **Risk Level:** LOW (notification failure unlikely to cause rollback)

#### Missing CTAs

**All critical notifications now have CTAs:**
- ✅ Payment success — "📋 Скопировать ключ"
- ✅ Balance topup — "🔐 Купить / Продлить доступ", "👤 Мой профиль"
- ✅ Auto-renewal — "👤 Мой профиль", "🔐 Купить / Продлить доступ"
- ✅ Trial expiration — "🔐 Купить / Продлить доступ"
- ✅ Payment rejected — "🔐 Купить / Продлить доступ", "🆘 Поддержка"

---

### PHASE 5 — STATE & FLAGS

#### DB Flags Used

**Idempotency Flags:**
- `payments.notification_sent` — ✅ Active (used for all payment notifications)
- `pending_purchases.status` — ✅ Active (prevents double processing)

**Notification Flags:**
- `subscriptions.reminder_3d_sent` — ✅ Active
- `subscriptions.reminder_24h_sent` — ✅ Active
- `subscriptions.reminder_3h_sent` — ✅ Active
- `subscriptions.reminder_6h_sent` — ✅ Active
- `subscriptions.trial_notif_6h_sent` — ✅ Active
- `subscriptions.trial_notif_60h_sent` — ✅ Active (used for 48h reminder)
- `subscriptions.trial_notif_71h_sent` — ✅ Active (used for final 6h reminder)

**Dead Flags (No Longer Used):**
- `subscriptions.smart_notif_no_traffic_20m_sent` — ❌ DEAD
- `subscriptions.smart_notif_no_traffic_24h_sent` — ❌ DEAD
- `subscriptions.smart_notif_first_connection_sent` — ❌ DEAD
- `subscriptions.smart_notif_3days_usage_sent` — ❌ DEAD
- `subscriptions.smart_notif_7days_before_expiry_sent` — ❌ DEAD
- `subscriptions.smart_notif_expiry_day_sent` — ❌ DEAD
- `subscriptions.smart_notif_expired_24h_sent` — ❌ DEAD
- `subscriptions.smart_notif_vip_offer_sent` — ❌ DEAD
- `subscriptions.trial_notif_18h_sent` — ❌ DEAD
- `subscriptions.trial_notif_30h_sent` — ❌ DEAD
- `subscriptions.trial_notif_42h_sent` — ❌ DEAD
- `subscriptions.trial_notif_54h_sent` — ❌ DEAD
- `users.smart_offer_sent` — ❌ DEAD (referenced in init_db but never used)

**Flags Written But Never Read:**
- `subscriptions.last_notification_sent_at` — ⚠️ Written but never checked
- `subscriptions.last_auto_renewal_at` — ✅ Active (used for auto-renewal idempotency)

#### Cleanup Strategy

**No cleanup strategy exists for dead flags.**
- **Risk Level:** LOW (flags are boolean, minimal storage impact)
- **Recommendation:** Future migration to remove dead flags

---

### PHASE 6 — SCHEDULERS & BACKGROUND TASKS

#### Schedulers List

1. **Reminders Task** (`reminders.py:199`)
   - **Interval:** 45 minutes
   - **Responsibility:** Send subscription expiry reminders
   - **Overlap Risk:** LOW — uses DB flags for idempotency
   - **Restart Risk:** LOW — flags prevent duplicate sends

2. **Trial Notifications Scheduler** (`trial_notifications.py:488`)
   - **Interval:** 5 minutes
   - **Responsibility:** Send trial reminders and expiration notifications
   - **Overlap Risk:** LOW — uses DB flags and singleton guard
   - **Restart Risk:** LOW — flags prevent duplicate sends

3. **Auto-Renewal Task** (`auto_renewal.py:351`)
   - **Interval:** 10 minutes (configurable, 5-15 min range)
   - **Responsibility:** Auto-renew subscriptions with sufficient balance
   - **Overlap Risk:** ⚠️ MEDIUM — 12-hour protection window may overlap on restart
   - **Restart Risk:** ⚠️ MEDIUM — if restart occurs within 12 hours, may process same subscription twice

4. **Crypto Payment Watcher** (`crypto_payment_watcher.py:235`)
   - **Interval:** 30 seconds
   - **Responsibility:** Check CryptoBot invoice status and finalize payments
   - **Overlap Risk:** LOW — uses `pending_purchase.status` for idempotency
   - **Restart Risk:** LOW — idempotency prevents double processing

5. **Fast Expiry Cleanup** (`fast_expiry_cleanup.py:30`)
   - **Interval:** 60 seconds (configurable, 60-300 sec range)
   - **Responsibility:** Remove expired VPN UUIDs from XRAY API
   - **Overlap Risk:** LOW — idempotent API calls
   - **Restart Risk:** LOW — safe to retry

6. **Health Check Task** (`healthcheck.py`)
   - **Interval:** Not specified in audit scope
   - **Responsibility:** Monitor system health
   - **Overlap Risk:** N/A — read-only

#### Tasks That Should Be Disabled on STAGE

**None identified** — All tasks are safe for STAGE:
- Reminders: Read-only, no side effects
- Trial notifications: Read-only, no side effects
- Auto-renewal: Checks balance before renewal, safe
- Crypto watcher: Idempotent, safe
- Fast cleanup: Idempotent, safe

#### Tasks That Can Affect PROD Data

**All financial tasks affect PROD data:**
- Auto-renewal: Debits balance, extends subscriptions
- Crypto watcher: Finalizes payments, activates subscriptions
- **Mitigation:** Both use transactions and idempotency

---

### PHASE 7 — ERROR HANDLING & LOGGING

#### Error Handling Patterns

**Granularity:**
- ✅ Financial operations: Fine-grained try/except with transaction rollback
- ⚠️ Notification sends: Broad try/except, errors logged but not propagated
- ⚠️ VPN API calls: Broad try/except, exceptions raised but not always handled

**Swallowed Exceptions:**
1. **Notification send failures** — logged but not retried
   - **Location:** Multiple (handlers.py, auto_renewal.py, trial_notifications.py)
   - **Risk Level:** LOW (user may not receive notification, but operation succeeds)

2. **Audit log failures** — logged but operation continues
   - **Location:** `database.py:_log_audit_event_atomic_standalone()`
   - **Risk Level:** LOW (audit is non-critical)

3. **Referral reward failures** — logged but purchase continues
   - **Location:** `database.py:process_referral_reward()`
   - **Risk Level:** LOW (reward is bonus, not critical)

#### Logging Quality

**Missing Context:**
- ⚠️ Some log messages lack `purchase_id` or `payment_id`
- ⚠️ Some error logs don't include full exception traceback

**Misleading Log Messages:**
- ⚠️ `"Payment received but DB not ready - payment rejected"` (line 3896) — misleading, should say "service unavailable"
  - **Status:** ✅ FIXED in recent changes (now says "service unavailable")

#### Errors That Should Alert Admin

**Currently Alerted:**
- ✅ Degraded mode activation
- ✅ Database recovery

**Should Alert But Don't:**
- ⚠️ VPN API failures during subscription activation
- ⚠️ Payment finalization failures
- ⚠️ Auto-renewal failures (insufficient balance is logged but not alerted)

#### Errors That Should Be Silent

**Currently Silent:**
- ✅ User notification send failures (correct)
- ✅ Audit log failures (correct)
- ✅ Referral reward failures (correct)

---

### PHASE 8 — SECURITY & ABUSE

#### Telegram-Specific Risks

**Callback Spoofing:**
- ⚠️ **Risk:** Callback data can be manipulated by user
- **Mitigation:** Callback handlers validate user permissions
- **Risk Level:** LOW (no financial impact, only UX manipulation)

**Replay Attacks:**
- ✅ **Protected:** All payment flows use idempotency checks
- ✅ **Safe**

#### Business Logic Abuse

**Free Access Loops:**
- ✅ **Protected:** All access grants require payment or admin action
- ✅ **Safe**

**Referral Abuse:**
- ⚠️ **Risk:** User can refer themselves (if they have multiple Telegram accounts)
- **Location:** `database.py:register_referral()` — no self-referral check
- **Risk Level:** MEDIUM (financial impact: free cashback)
- **Mitigation:** None currently

**Trial Abuse:**
- ✅ **Protected:** `is_trial_available()` checks for existing paid subscriptions
- ✅ **Safe**

#### Config Risks

**Missing Env Vars:**
- ✅ **Handled:** `config.py` validates required vars, exits on missing PROD vars
- ✅ **Safe**

**Unsafe Defaults:**
- ⚠️ **Risk:** `XRAY_SERVER_IP`, `XRAY_PORT`, etc. have hardcoded defaults
- **Location:** `config.py:137-144`
- **Risk Level:** LOW (defaults are placeholders, should be overridden)

---

## CLEANUP CANDIDATES

### Dead Code
1. **Smart notification functions** — removed from `reminders.py`, but flags remain in DB
2. **Trial notification logic** — 18h, 30h, 42h, 54h reminders removed, but flags remain
3. **Outline API references** — commented out in `main.py:11`, but may have lingering imports

### Dead Flags (DB Schema)
- `smart_notif_*` flags (9 flags) — never written, never read
- `trial_notif_18h_sent`, `trial_notif_30h_sent`, `trial_notif_42h_sent`, `trial_notif_54h_sent` — never written, never read
- `users.smart_offer_sent` — written in init_db but never used

### Obsolete Logic
- **FSM state management** — `MemoryStorage()` loses state on restart (acceptable for MVP, but should be documented)

---

## RECOMMENDED NEXT STEPS

### SAFE (Stage)
1. **Add idempotency to referral cashback notifications**
   - Add `referral_reward_notification_sent` flag to `referral_rewards` table
   - Check flag before sending notification

2. **Add idempotency to trial expiration notification**
   - Fix `trial_completed_sent` flag reset on error (line 456 in trial_notifications.py)
   - Make flag update atomic with notification send

3. **Remove dead flags from init_db()**
   - Remove `smart_notif_*` column additions
   - Remove unused `trial_notif_*` column additions
   - Keep columns in DB (no migration), just stop creating them

4. **Improve error logging**
   - Add `purchase_id`/`payment_id` to all payment-related logs
   - Add full traceback to critical error logs

### RISKY (Prod-Impact)
1. **Add admin alerts for VPN API failures**
   - Alert admin when `vpn_utils.add_vless_user()` fails during subscription activation
   - Requires payment to be refunded or manually fixed

2. **Add self-referral prevention**
   - Check `referrer_id != referred_id` in `register_referral()`
   - Prevents free cashback abuse

3. **Add retry logic for VPN API calls**
   - Retry `add_vless_user()` on transient failures
   - Prevents payment success but no access scenarios

4. **Fix auto-renewal race condition**
   - Use database-level locking or optimistic locking
   - Prevents double renewal on scheduler overlap

5. **Add persistent FSM storage**
   - Replace `MemoryStorage()` with Redis or PostgreSQL-backed storage
   - Prevents user state loss on restart

---

## FINAL VERDICT

### What Blocks Production

**Nothing critical blocks production**, but the following should be addressed:

1. **VPN API failure handling** — Users can pay but not get access if VPN API fails
2. **Auto-renewal race condition** — Potential double renewal on scheduler overlap
3. **Referral abuse** — Self-referral not prevented

### What Can Wait

1. **Dead flags cleanup** — Low priority, no functional impact
2. **FSM persistence** — Acceptable for MVP, users can restart flow
3. **Enhanced logging** — Nice to have, not blocking

### What Is Surprisingly Solid

1. **Transaction boundaries** — All financial operations properly wrapped
2. **Idempotency** — Payment flows are well-protected
3. **Degraded mode** — System gracefully handles DB unavailability
4. **Notification system** — Recent refactoring improved consistency significantly

---

## CONCLUSION

The system is **production-ready with conditions**. The architecture is sound, financial operations are safe, and error handling is generally good. The main risks are around VPN API dependency and some edge cases in schedulers. With the recommended fixes, the system would be fully production-ready.

**Overall Grade: B+** (Good, with room for improvement)

---

**END OF AUDIT REPORT**
