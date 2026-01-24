# crypto_payment_watcher.py Analysis & Fixes

## Executive Summary

**Issues Found:** 3 issues  
**Critical Risks:** 1 (silent skip when CryptoBot disabled)  
**Observability Gaps:** 2  
**Payment Loss Risk:** ✅ NONE (payments are safe due to idempotency)

---

## 1. Degraded-Mode Behavior Analysis

### ✅ Correct Behavior:

1. **DB Unavailable (Line 82-91):**
   - ✅ Returns `(0, "skipped")` gracefully
   - ✅ Logs: `PAYMENT_CHECK_SKIP_DB_UNAVAILABLE`
   - ✅ States: `payments_safe=True, will_retry_next_iteration=True`
   - ✅ Payments remain in `pending_purchases` table, will retry next iteration

2. **System State Unavailable (Line 387-402):**
   - ✅ Skips iteration if `system_state.is_unavailable`
   - ✅ Logs warning with reason
   - ✅ Payments remain safe (not processed, not lost)

3. **System State Degraded (Line 407-411):**
   - ✅ Continues normally (doesn't skip)
   - ✅ Logs info message
   - ✅ Correct: Degraded state doesn't block payment processing

4. **Cooldown (Line 415-431):**
   - ✅ Skips iteration during cooldown
   - ✅ Logs reason
   - ✅ Payments remain safe

### ❌ Issues Found:

---

## 2. Issues & Risks

### ❌ Issue 1: Silent Skip When CryptoBot Disabled

**Location:** `crypto_payment_watcher.py` line 76-77

**Problem:**
- If `cryptobot.is_enabled()` returns False, iteration is skipped silently
- No log message
- Not observable
- Could mask configuration issues

**Risk:**
- If CryptoBot is temporarily misconfigured, payments won't be checked
- Payments remain in `pending_purchases` but won't be processed
- No alert to operators

**Current Code:**
```python
if not cryptobot.is_enabled():
    return (0, "skipped")  # ❌ No log
```

**Fix Required:**
- Add explicit log: `PAYMENT_CHECK_SKIP_CRYPTOBOT_DISABLED`
- State: `payments_safe=True, will_retry_when_enabled=True`
- Make it observable

---

### ⚠️ Issue 2: Misleading Return Value on DB Error

**Location:** `crypto_payment_watcher.py` line 93-98

**Problem:**
- Returns `(0, "failed")` but log says `payments_safe=True`
- Inconsistent: if payments are safe, outcome should be "skipped", not "failed"
- "failed" implies something broke, but payments are actually safe

**Current Code:**
```python
except Exception as e:
    logger.error(...)
    logger.info(
        f"PAYMENT_CHECK_SKIP_DB_ERROR [reason=unexpected_error, "
        f"payments_safe=True, will_retry_next_iteration=True]"
    )
    return (0, "failed")  # ❌ Should be "skipped" if payments are safe
```

**Fix Required:**
- Return `(0, "skipped")` if payments are safe
- Only return `(0, "failed")` if payments are at risk (shouldn't happen in this case)

---

### ⚠️ Issue 3: Missing Log for CryptoBot API Unavailable

**Location:** `crypto_payment_watcher.py` line 133

**Problem:**
- If CryptoBot API call fails (network error, timeout), error is caught at line 220
- But no explicit log indicating:
  - Payment check was attempted
  - API call failed
  - Payment will be retried next iteration
  - Payment is safe (not lost)

**Current Code:**
```python
invoice_status = await cryptobot.check_invoice_status(invoice_id)  # Could fail
# ...
except Exception as e:
    logger.error(f"Error checking crypto payment for purchase {purchase_id}: {e}", exc_info=True)
    outcome = "degraded"  # ✅ Correct
```

**Fix Required:**
- Add explicit log before API call: `PAYMENT_CHECK_ATTEMPT`
- Add explicit log on API failure: `PAYMENT_CHECK_API_FAILED` with `payments_safe=True, will_retry=True`

---

## 3. Payment Loss Risk Analysis

### ✅ Payments Are Safe (No Loss Risk):

1. **Idempotency:**
   - `finalize_purchase()` checks `status != 'pending'` (line 5074)
   - Raises `ValueError` if already processed (line 5077)
   - Prevents double-processing

2. **Persistence:**
   - Payments stored in `pending_purchases` table
   - Status remains 'pending' until finalized
   - Survives worker restarts

3. **Retry Logic:**
   - Worker runs every 30 seconds
   - Failed iterations don't lose payments
   - Payments are retried in next iteration

4. **Expiration Handling:**
   - Query only gets non-expired purchases (line 108)
   - Expired purchases handled separately by `cleanup_expired_purchases()`
   - Expired purchases marked as 'expired', not deleted

5. **Transaction Safety:**
   - `finalize_purchase()` uses database transaction
   - All-or-nothing: either fully processed or rolled back
   - No partial state

### ✅ Confirmation: No Payment Loss Risk

**Reasons:**
- Payments persist in database
- Worker retries indefinitely
- Idempotency prevents double-processing
- Transactions ensure atomicity
- Expired purchases are marked, not deleted

---

## 4. Exact Code Fixes

### Fix 1: Add Observable Log When CryptoBot Disabled

**File:** `crypto_payment_watcher.py`  
**Location:** Line 76-77

**Change:**
```python
# OLD:
if not cryptobot.is_enabled():
    return (0, "skipped")

# NEW:
if not cryptobot.is_enabled():
    logger.info(
        f"PAYMENT_CHECK_SKIP_CRYPTOBOT_DISABLED [reason=cryptobot_not_configured, "
        f"payments_safe=True, will_retry_when_enabled=True]"
    )
    return (0, "skipped")
```

---

### Fix 2: Fix Misleading Return Value

**File:** `crypto_payment_watcher.py`  
**Location:** Line 93-98

**Change:**
```python
# OLD:
except Exception as e:
    logger.error(f"crypto_payment_watcher: Unexpected error getting DB pool: {type(e).__name__}: {str(e)[:100]}")
    logger.info(
        f"PAYMENT_CHECK_SKIP_DB_ERROR [reason=unexpected_error, "
        f"payments_safe=True, will_retry_next_iteration=True]"
    )
    return (0, "failed")  # ❌ Misleading

# NEW:
except Exception as e:
    logger.error(f"crypto_payment_watcher: Unexpected error getting DB pool: {type(e).__name__}: {str(e)[:100]}")
    logger.info(
        f"PAYMENT_CHECK_SKIP_DB_ERROR [reason=unexpected_error, "
        f"payments_safe=True, will_retry_next_iteration=True]"
    )
    return (0, "skipped")  # ✅ Consistent with log message
```

---

### Fix 3: Add Explicit Logging for API Calls

**File:** `crypto_payment_watcher.py`  
**Location:** Line 128-134

**Change:**
```python
# OLD:
try:
    # Преобразуем invoice_id в int для CryptoBot API
    invoice_id = int(invoice_id_str)
    
    # Проверяем статус invoice через CryptoBot API
    invoice_status = await cryptobot.check_invoice_status(invoice_id)
    status = invoice_status.get("status")

# NEW:
try:
    # Преобразуем invoice_id в int для CryptoBot API
    invoice_id = int(invoice_id_str)
    
    # Log payment check attempt
    logger.debug(
        f"PAYMENT_CHECK_ATTEMPT [purchase_id={purchase_id}, user={telegram_id}, "
        f"invoice_id={invoice_id}]"
    )
    
    # Проверяем статус invoice через CryptoBot API
    try:
        invoice_status = await cryptobot.check_invoice_status(invoice_id)
        status = invoice_status.get("status")
    except Exception as api_error:
        # CryptoBot API call failed - payment is safe, will retry
        logger.warning(
            f"PAYMENT_CHECK_API_FAILED [purchase_id={purchase_id}, user={telegram_id}, "
            f"invoice_id={invoice_id}, error={type(api_error).__name__}: {str(api_error)[:100]}, "
            f"payments_safe=True, will_retry_next_iteration=True]"
        )
        outcome = "degraded"
        continue  # Skip this purchase, continue with others
```

---

## 5. Skipped Iterations Summary

### Intentional Skips (All Correct):

1. **CryptoBot Disabled (Line 76):**
   - ✅ Intentional: CryptoBot not configured
   - ⚠️ Not observable (needs fix)
   - ✅ Non-destructive: Payments remain in DB

2. **DB Unavailable (Line 86-91):**
   - ✅ Intentional: DB temporarily unavailable
   - ✅ Observable: Logged with `PAYMENT_CHECK_SKIP_DB_UNAVAILABLE`
   - ✅ Non-destructive: Payments remain in DB

3. **System Unavailable (Line 387-402):**
   - ✅ Intentional: System critical components unavailable
   - ✅ Observable: Logged with reason
   - ✅ Non-destructive: Payments remain in DB

4. **Cooldown (Line 415-431):**
   - ✅ Intentional: Database recovery cooldown
   - ✅ Observable: Logged with remaining time
   - ✅ Non-destructive: Payments remain in DB

### All Skips Are:
- ✅ Intentional (valid reasons)
- ⚠️ Observable (mostly, needs fix for CryptoBot disabled)
- ✅ Non-destructive (payments never lost)

---

## 6. Risk Assessment

### Payment Loss Risk: ✅ NONE

**Reasons:**
1. Payments persist in `pending_purchases` table
2. Worker retries every 30 seconds
3. `finalize_purchase()` is idempotent
4. Database transactions ensure atomicity
5. Expired purchases are marked, not deleted

### Operational Risks:

1. **CryptoBot Disabled (Low Risk):**
   - Payments won't be checked
   - But payments remain in DB
   - Will be checked when CryptoBot is re-enabled
   - **Fix:** Add observable log

2. **DB Unavailable (No Risk):**
   - Worker skips iteration
   - Payments remain in DB
   - Will be checked when DB is available
   - **Fix:** Already correct, just improve return value

3. **CryptoBot API Unavailable (No Risk):**
   - API call fails
   - Payment remains in DB
   - Will be retried next iteration
   - **Fix:** Add explicit logging

---

## 7. Summary of Fixes

| Issue | Severity | Location | Fix |
|-------|----------|----------|-----|
| Silent skip when CryptoBot disabled | Medium | Line 76 | Add log |
| Misleading return value | Low | Line 98 | Change to "skipped" |
| Missing API call logging | Low | Line 133 | Add explicit logs |

---

## 8. Testing Recommendations

1. **CryptoBot Disabled Test:**
   - Disable CryptoBot (remove token)
   - Verify: Worker logs skip, returns "skipped"
   - Verify: Payments remain in DB

2. **DB Unavailable Test:**
   - Stop database
   - Verify: Worker logs skip, returns "skipped"
   - Verify: Payments remain in DB
   - Restart database
   - Verify: Payments are checked in next iteration

3. **CryptoBot API Unavailable Test:**
   - Block CryptoBot API (network/firewall)
   - Verify: Worker logs API failure
   - Verify: Payments remain in DB
   - Restore API access
   - Verify: Payments are checked in next iteration

4. **Payment Idempotency Test:**
   - Finalize same payment twice
   - Verify: Second call raises ValueError
   - Verify: Payment not double-processed

---

## 9. Correctness Confirmation

### ✅ Degraded-Mode Behavior: CORRECT
- Skips when system unavailable
- Continues when system degraded
- Payments remain safe

### ✅ Skipped Iterations: INTENTIONAL & OBSERVABLE (mostly)
- All skips have valid reasons
- Most are logged (needs fix for CryptoBot disabled)
- All are non-destructive

### ✅ Payment Loss Risk: NONE
- Payments persist in database
- Worker retries indefinitely
- Idempotency prevents double-processing
- Transactions ensure atomicity
