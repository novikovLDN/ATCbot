# Referral System - Critical Fixes

## Problems Fixed

### 1. DB Schema Mismatch ✅
**Issue**: Code was using `referred_at` column that doesn't exist in database schema.

**Fix**: 
- Removed all `referred_at` references
- Use only `referred_by` column (which exists in schema)
- Updated comments to clarify: "DO NOT use referred_at - column doesn't exist in schema"

**Files Changed**:
- `database.py` - `register_referral()` function
- `app/services/referrals/service.py` - inline registration logic

### 2. asyncpg Exception Handling ✅
**Issue**: `asyncpg.ProgrammingError` doesn't exist, causing uncaught exceptions.

**Fix**: 
- Replaced all `asyncpg.ProgrammingError` with `asyncpg.PostgresError`
- Updated 16 exception handlers across `database.py`

**Files Changed**:
- `database.py` - All exception handlers

### 3. Referrer Not Persisted ✅
**Issue**: Referrer lookup was failing, causing `REFERRAL_REFERRER_NOT_FOUND` errors.

**Fix**:
- Changed referrer lookup from `find_user_by_referral_code("ref_123456")` to `get_user(telegram_id)`
- The referral code format is `ref_<telegram_id>`, so we extract the ID directly
- Added persistence verification after saving referrer_id

**Files Changed**:
- `app/services/referrals/service.py` - `process_referral_registration()`

### 4. register_referral Idempotency ✅
**Issue**: Function didn't properly handle already-registered cases.

**Fix**:
- Returns `False` if referral already exists (idempotent)
- Verifies referrer_id was actually saved after UPDATE
- Checks both `referrer_id` and `referred_by` columns
- Handles case where different referrer already set (immutable)

**Files Changed**:
- `database.py` - `register_referral()` function

### 5. Referral Registration on /start ✅
**Issue**: Need to ensure referral is registered before trial/payment.

**Fix**:
- `/start` handler calls `process_referral_on_first_interaction()` 
- This happens BEFORE trial activation or payment
- Registration is idempotent (safe to call multiple times)

**Files Changed**:
- `handlers.py` - `/start` handler (already correct)

### 6. Reward Flow Referrer Resolution ✅
**Issue**: Need to ensure referrer is resolved correctly at payment time.

**Fix**:
- `process_referral_reward()` checks both `referrer_id` and `referred_by`
- Added `REFERRAL_RESOLVED` log when referrer is found
- Added `REFERRAL_REWARD_APPLIED` log when cashback is credited

**Files Changed**:
- `database.py` - `process_referral_reward()` function

### 7. Explicit Logging ✅
**Added Logs**:
- `REFERRAL_SAVED` - When referral is persisted to DB
- `REFERRAL_RESOLVED` - When referrer is resolved at payment time
- `REFERRAL_REWARD_APPLIED` - When cashback is credited
- `REFERRAL_NOTIFICATION_SENT` - When notification is sent
- `REFERRAL_NOTIFICATION_FAILED` - When notification fails

## Code Changes Summary

### `app/services/referrals/service.py`
```python
# BEFORE: Wrong lookup
referrer = await database.find_user_by_referral_code(referral_code)  # ❌

# AFTER: Direct lookup by telegram_id
referrer_telegram_id = int(referral_code[4:])  # Extract from "ref_<id>"
referrer_user = await database.get_user(referrer_telegram_id)  # ✅
```

### `database.py`
```python
# BEFORE: Used non-existent column
SET referrer_id = $1, referred_by = $1, referred_at = NOW()  # ❌

# AFTER: Use only existing columns
SET referrer_id = $1, referred_by = $1  # ✅
```

```python
# BEFORE: Wrong exception
except (asyncpg.UndefinedTableError, asyncpg.ProgrammingError)  # ❌

# AFTER: Correct exception
except (asyncpg.UndefinedTableError, asyncpg.PostgresError)  # ✅
```

## Testing Checklist

- [ ] `/start ref_123456` → `REFERRAL_SAVED` appears in logs
- [ ] `/start ref_123456` → `referrer_id` saved in DB (check `users.referred_by`)
- [ ] Payment → `REFERRAL_RESOLVED` appears in logs
- [ ] Payment → `REFERRAL_REWARD_APPLIED` appears in logs
- [ ] Payment → `referral_reward_success=True`
- [ ] Payment → `REFERRAL_NOTIFICATION_SENT` appears in logs
- [ ] Referrer receives notification
- [ ] No `UndefinedColumnError` for `referred_at`
- [ ] No `asyncpg.ProgrammingError` exceptions
- [ ] No `REFERRAL_REFERRER_NOT_FOUND` for valid referral codes
- [ ] No `reason=no_referrer` for valid referrals

## Expected Log Flow

### Registration:
```
REFERRAL_SAVED [referrer=123456, referred=789012, referrer_id_persisted=True]
REFERRAL_REGISTERED [referrer=123456, referred=789012]
REFERRAL_NOTIFICATION_SENT [type=registration, referrer=123456, referred=789012]
```

### Payment:
```
REFERRAL_RESOLVED [buyer=789012, referrer=123456, purchase_id=xxx]
REFERRAL_REWARD_APPLIED [referrer=123456, buyer=789012, amount=10.00 RUB]
REFERRAL_NOTIFICATION_SENT [type=purchase, referrer=123456, referred=789012]
```

## Key Fixes

1. **Schema Compatibility**: Removed `referred_at`, use only `referred_by`
2. **Exception Handling**: Use `asyncpg.PostgresError` instead of non-existent `ProgrammingError`
3. **Referrer Lookup**: Use `get_user(telegram_id)` directly instead of `find_user_by_referral_code()`
4. **Persistence Verification**: Verify referrer_id is actually saved after UPDATE
5. **Idempotency**: `register_referral()` returns False if already registered (correct behavior)

## Files Modified

1. `app/services/referrals/service.py` - Fixed referrer lookup
2. `database.py` - Fixed exception handling, removed `referred_at`, improved `register_referral()`
3. `handlers.py` - Enhanced notification logging

All changes are minimal and focused on fixing the root causes without changing business logic.
