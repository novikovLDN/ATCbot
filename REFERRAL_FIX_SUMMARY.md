# Referral System Fix - Root Cause Resolution

## Problem
- `REFERRAL_REFERRER_NOT_FOUND` errors
- `referral_reward_success=False, reason=no_referrer`
- Cashback not credited
- Notifications not sent

## Root Cause
In `app/services/referrals/service.py::process_referral_registration()`:
- Code was trying to find referrer using `find_user_by_referral_code("ref_123456")`
- But database stores referral codes as generated strings (e.g., "ABC123"), not as `ref_<telegram_id>`
- The referrer lookup failed, so referral was never saved

## Fix Applied

### 1. Fixed Referrer Lookup (`app/services/referrals/service.py`)
**Before:**
```python
referrer = await database.find_user_by_referral_code(referral_code)  # ❌ Wrong!
```

**After:**
```python
# Extract telegram_id from "ref_<telegram_id>" format
referrer_telegram_id = int(referral_code[4:])
# Use telegram_id directly to find user
referrer_user = await database.get_user(referrer_telegram_id)  # ✅ Correct!
```

### 2. Added Explicit Logging
- `REFERRAL_SAVED` - When referral is persisted to DB
- `REFERRAL_RESOLVED` - When referrer is resolved at payment time
- `REFERRAL_REWARD_APPLIED` - When cashback is credited
- `REFERRAL_NOTIFICATION_SENT` - When notification is sent

### 3. Added Persistence Verification
- After saving referrer_id, verify it was actually persisted
- Log `REFERRAL_SAVE_FAILED` if referrer_id is not found after save

### 4. Improved Backward Compatibility
- `process_referral_reward()` now checks both `referrer_id` and `referred_by` columns
- Ensures existing referrals continue to work

### 5. Enhanced Notification Logging
- Track notification success/failure
- Log `REFERRAL_NOTIFICATION_FAILED` if sending fails

## Files Modified

1. **`app/services/referrals/service.py`**
   - Fixed referrer lookup to use `get_user(telegram_id)` instead of `find_user_by_referral_code()`
   - Added `REFERRAL_SAVED` logging

2. **`database.py`**
   - Added `REFERRAL_RESOLVED` logging in `process_referral_reward()`
   - Added `REFERRAL_REWARD_APPLIED` logging
   - Added persistence verification in `register_referral()`
   - Added fallback to `referred_by` column

3. **`handlers.py`**
   - Enhanced notification logging with success/failure tracking
   - Added `REFERRAL_NOTIFICATION_SENT` / `REFERRAL_NOTIFICATION_FAILED` logs

## Testing Checklist

- [ ] `/start ref_123456` → referrer_id saved correctly
- [ ] Payment → `REFERRAL_RESOLVED` appears in logs
- [ ] Payment → `REFERRAL_REWARD_APPLIED` appears in logs
- [ ] Payment → `referral_reward_success=True`
- [ ] Payment → `REFERRAL_NOTIFICATION_SENT` appears in logs
- [ ] Referrer receives notification
- [ ] Balance topup → same flow works
- [ ] No more `REFERRAL_REFERRER_NOT_FOUND` errors

## Expected Log Flow

1. **Registration:**
   ```
   REFERRAL_SAVED [referrer=123456, referred=789012, referrer_id_persisted=True]
   REFERRAL_REGISTERED [referrer=123456, referred=789012]
   ```

2. **Payment:**
   ```
   REFERRAL_RESOLVED [buyer=789012, referrer=123456, purchase_id=xxx]
   REFERRAL_REWARD_APPLIED [referrer=123456, buyer=789012, amount=10.00 RUB]
   REFERRAL_NOTIFICATION_SENT [type=purchase, referrer=123456, referred=789012]
   ```

## Key Changes Summary

- **Critical Fix**: Use `get_user(telegram_id)` instead of `find_user_by_referral_code()`
- **Logging**: Added explicit logs at each step
- **Verification**: Verify referrer_id is actually saved
- **Backward Compat**: Support both `referrer_id` and `referred_by` columns
