# Balance Topup Idempotency Fix

## Summary
Fixed P0 blocker: Added idempotency protection for balance topup payments to prevent duplicate balance credits from repeated webhooks.

## Changes

### 1. Database Migration
**File:** `migrations/012_add_payment_idempotency_keys.sql`
- Added `telegram_payment_charge_id TEXT` column to `payments` table
- Added `cryptobot_payment_id TEXT` column to `payments` table
- Created UNIQUE indexes on both columns (partial indexes, NULL values excluded)
- These indexes enforce idempotency at the database level

### 2. Database Function Update
**File:** `database.py:6335` - `finalize_balance_topup`
- **New required parameters:**
  - `provider: str` - Payment provider ('telegram' or 'cryptobot')
  - `provider_charge_id: str` - Unique charge ID from provider
  - `correlation_id: Optional[str]` - For log correlation
- **Idempotency check:** Added at the very start of function
  - Checks for existing payment with same `provider_charge_id`
  - Returns early with `reason="already_processed"` if duplicate
  - Logs `BALANCE_TOPUP_DUPLICATE_SKIPPED` event
- **Atomic insert:** Payment record created FIRST, then balance updated
- **Logging:** Added structured logs:
  - `BALANCE_TOPUP_DUPLICATE_SKIPPED` - Duplicate webhook detected
  - `BALANCE_TOPUP_SUCCESS` - Successful balance topup

### 3. Payment Service Update
**File:** `app/services/payments/service.py:311` - `finalize_balance_topup_payment`
- Updated signature to require `provider` and `provider_charge_id`
- Handles idempotent skip gracefully (returns success with existing payment)
- Validates provider and provider_charge_id before calling database function

### 4. Handler Update
**File:** `handlers.py:4898` - `process_successful_payment`
- Extracts `telegram_payment_charge_id` from `payment.telegram_payment_charge_id`
- Fails hard if `provider_charge_id` is missing (logs error, returns early)
- Passes `provider="telegram"` and `provider_charge_id` to payment service
- Adds `correlation_id` for log tracing

## Idempotency Guarantees

1. **Database-level protection:** UNIQUE indexes prevent duplicate inserts
2. **Application-level check:** Explicit duplicate check before balance credit
3. **Atomic transaction:** Payment record and balance update in single transaction
4. **Safe retries:** Repeated webhooks with same `provider_charge_id` are no-ops

## Testing Checklist

- [ ] Apply migration to STAGE database
- [ ] Deploy code to STAGE
- [ ] Test duplicate Telegram webhook:
  - Send same `successful_payment` event twice
  - Verify balance increases only once
  - Verify `BALANCE_TOPUP_DUPLICATE_SKIPPED` log appears
- [ ] Test normal flow:
  - Send new payment webhook
  - Verify balance increases
  - Verify `BALANCE_TOPUP_SUCCESS` log appears
- [ ] Monitor logs for 24-48 hours
- [ ] Deploy to PROD after verification

## Backward Compatibility

- **Breaking change:** `finalize_balance_topup` now requires `provider` and `provider_charge_id`
- **Migration required:** Must apply `012_add_payment_idempotency_keys.sql` before deployment
- **Handler changes:** Only affects Telegram payment handler (already updated)

## Security Impact

- **Financial safety:** Prevents duplicate balance credits (P0 blocker fixed)
- **No new attack vectors:** Idempotency keys are validated and sanitized
- **Audit trail:** All duplicate attempts are logged

## Related Files

- `migrations/012_add_payment_idempotency_keys.sql` - Database migration
- `database.py:6335` - Core idempotency logic
- `app/services/payments/service.py:311` - Service layer wrapper
- `handlers.py:4898` - Telegram payment handler

## Commit Message
```
fix(payments): add idempotency key for balance topups

P0 BLOCKER FIX: Prevent duplicate balance credits from repeated webhooks

- Add telegram_payment_charge_id and cryptobot_payment_id columns to payments table
- Create UNIQUE indexes for idempotency protection
- Update finalize_balance_topup to require provider_charge_id
- Add idempotency check at start of function (returns early if duplicate)
- Update handlers to extract and pass provider_charge_id
- Add structured logging for duplicate detection

This fix ensures that repeated Telegram webhooks do NOT increase user balance.
Database-level UNIQUE constraints provide hard financial protection.

Migration: 012_add_payment_idempotency_keys.sql
```
