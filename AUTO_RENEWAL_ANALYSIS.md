# auto_renewal.py Analysis & Fixes

## Executive Summary

**Issues Found:** 1 critical bug  
**Critical Risk:** Timezone mismatch causing incorrect renewal timing  
**UUID Preservation:** ✅ Correct  
**Duplicate Prevention:** ✅ Correct  

---

## 1. Renewal Logic Preserves UUID Correctly

### ✅ Correct Behavior:

1. **UUID Preservation (Line 203-230):**
   - ✅ Calls `grant_access()` with `source="auto_renew"`
   - ✅ Validates that `action_type == "renewal"` and `vless_url is None`
   - ✅ If UUID is regenerated, refunds the balance (line 224-229)
   - ✅ Logs error and prevents incorrect renewal

2. **grant_access() Logic:**
   - ✅ Checks if subscription is active (line 3100 in database.py)
   - ✅ If active: extends `expires_at` without calling VPN API
   - ✅ UUID remains unchanged
   - ✅ Returns `action="renewal"` and `vless_url=None`

**Conclusion:** UUID preservation logic is correct.

---

## 2. No Duplicate Renewals

### ✅ Correct Behavior:

1. **Database-Level Protection:**
   - ✅ Line 81: `FOR UPDATE SKIP LOCKED` - prevents concurrent processing
   - ✅ Only one worker can process a subscription at a time

2. **Application-Level Protection:**
   - ✅ Line 100-108: Updates `last_auto_renewal_at` at START of transaction
   - ✅ Line 111-113: Checks if UPDATE affected 0 rows (already processed)
   - ✅ Line 80: Query condition `last_auto_renewal_at < expires_at - INTERVAL '12 hours'` prevents re-processing
   - ✅ Line 106: UPDATE also checks `last_auto_renewal_at < expires_at - INTERVAL '12 hours'`
   - ✅ Transaction rollback on error (line 95: `async with conn.transaction()`)

3. **Idempotency:**
   - ✅ If transaction fails, `last_auto_renewal_at` is rolled back
   - ✅ Next iteration can retry safely
   - ✅ 12-hour window prevents immediate re-processing

**Conclusion:** Duplicate prevention is correct.

---

## 3. Time Calculations Safety

### ❌ Critical Bug: Timezone Mismatch

**Location:** `auto_renewal.py` line 68

**Problem:**
- Line 68: `now = datetime.now()` - **LOCAL TIME** (not UTC)
- Line 410: `now = datetime.utcnow()` - **UTC** (correct, but in task loop)
- Database stores `expires_at` in UTC (PostgreSQL TIMESTAMP)
- Comparing local time with UTC timestamps causes incorrect results

**Impact:**
- Subscriptions may be renewed too early or too late
- Timezone-dependent behavior (different results in different timezones)
- Incorrect filtering: `expires_at <= renewal_threshold` where `renewal_threshold` is local time
- Incorrect comparison: `expires_at > now` where `now` is local time

**Current Code:**
```python
# Line 68: WRONG - uses local time
now = datetime.now()
renewal_threshold = now + RENEWAL_WINDOW

# Line 82: Compares UTC expires_at with local time
subscriptions = await conn.fetch(
    """SELECT s.*, u.language, u.balance
       FROM subscriptions s
       JOIN users u ON s.telegram_id = u.telegram_id
       WHERE s.status = 'active'
       AND s.auto_renew = TRUE
       AND s.expires_at <= $1   -- renewal_threshold (LOCAL TIME)
       AND s.expires_at > $2    -- now (LOCAL TIME)
       ...
    """,
    renewal_threshold, now  # ❌ LOCAL TIME vs UTC
)

# Line 107: Updates last_auto_renewal_at with LOCAL TIME
update_result = await conn.execute(
    """UPDATE subscriptions 
       SET last_auto_renewal_at = $1  -- now (LOCAL TIME)
       ...
    """,
    now, telegram_id  # ❌ LOCAL TIME stored in UTC column
)
```

**Fix Required:**
- Change `datetime.now()` to `datetime.utcnow()` at line 68
- Ensure all time calculations use UTC consistently

**Edge Cases:**
- ✅ DST transitions: UTC is not affected by DST
- ✅ Timezone changes: UTC is consistent
- ✅ Server timezone changes: UTC is independent

---

## 4. Additional Issues

### ⚠️ Issue: Inconsistent Time Usage in grant_access

**Location:** `database.py` line 3049

**Problem:**
- `grant_access()` also uses `datetime.now()` (local time)
- Should use `datetime.utcnow()` for consistency
- This affects renewal detection logic

**Impact:**
- Renewal detection may be incorrect if server timezone differs from UTC
- `expires_at > now` comparison may be wrong

**Note:** This is in `database.py`, not `auto_renewal.py`, but should be fixed for consistency.

---

## 5. Exact Code Fix

### Fix: Use UTC Consistently

**File:** `auto_renewal.py`  
**Location:** Line 68

**Change:**
```python
# OLD:
now = datetime.now()  # ❌ LOCAL TIME

# NEW:
now = datetime.utcnow()  # ✅ UTC
```

**Verification:**
- Line 410 already uses `datetime.utcnow()` (correct)
- After fix, both will use UTC consistently
- Database comparisons will be correct

---

## 6. Summary of Issues

| Issue | Severity | Location | Fix |
|-------|----------|----------|-----|
| Timezone mismatch (local time vs UTC) | Critical | Line 68 | Change to `datetime.utcnow()` |

---

## 7. Correctness Confirmation

### ✅ UUID Preservation: CORRECT
- ✅ `grant_access()` preserves UUID for active subscriptions
- ✅ Validation checks prevent UUID regeneration
- ✅ Refund logic protects against errors

### ✅ Duplicate Prevention: CORRECT
- ✅ Database-level locking (`FOR UPDATE SKIP LOCKED`)
- ✅ Application-level checks (`last_auto_renewal_at`)
- ✅ Transaction rollback on error
- ✅ 12-hour window prevents immediate re-processing

### ❌ Time Calculations: NEEDS FIX
- ❌ Uses local time instead of UTC
- ✅ After fix: All time calculations will use UTC consistently
- ✅ Edge cases handled (DST, timezone changes)

---

## 8. Testing Recommendations

1. **Timezone Test:**
   - Set server timezone to different timezone (e.g., EST)
   - Create subscription expiring in 5 hours
   - Run auto-renewal worker
   - Verify: Renewal happens at correct time (not affected by server timezone)

2. **UTC Consistency Test:**
   - Verify all `datetime.now()` calls are changed to `datetime.utcnow()`
   - Verify database comparisons use UTC
   - Verify `last_auto_renewal_at` is stored in UTC

3. **Edge Case Test:**
   - Subscription expiring exactly at renewal threshold
   - Subscription expiring just before renewal threshold
   - Subscription expiring just after renewal threshold
   - Verify: Correct filtering and renewal timing

4. **Duplicate Prevention Test:**
   - Start two workers simultaneously
   - Verify: Only one processes each subscription
   - Verify: No duplicate renewals
   - Verify: No duplicate balance deductions
