# Referral System - Deterministic, Immutable, Payment-Safe Implementation

## Summary

Complete restoration of the referral system with deterministic registration, immutable referrer_id, and payment-safe reward logic.

## Key Principles

1. **Deterministic Registration**: Referral registration happens on FIRST interaction (any update), not just /start
2. **Immutable referrer_id**: Once set, referrer_id cannot be changed
3. **Clear Lifecycle States**: REGISTERED → ACTIVATED
4. **Payment-Safe**: Every payment/topup rewards referrer
5. **Idempotent Operations**: All operations are safe to retry

## Files Modified

### 1. `app/services/referrals/service.py` (NEW)
- `process_referral_registration()` - Single source of truth for referral registration
- `activate_referral()` - Transitions referral from REGISTERED to ACTIVATED
- `get_referral_state()` - Gets current referral state
- `ReferralState` enum - NONE, REGISTERED, ACTIVATED

### 2. `app/utils/referral_middleware.py` (NEW)
- `process_referral_on_first_interaction()` - Processes referral on first user interaction
- Extracts referral code from /start command or callback data
- Returns referral info for notification

### 3. `database.py`
- **Added**: `referred_at` column to users table (timestamp when referral was registered)
- **Updated**: `register_referral()` - Sets `referred_at` timestamp
- **Updated**: `process_referral_reward()` - Activates referral on first payment
- **Updated**: `get_referral_level_info()` - Uses `referrals.first_paid_at` for paid count
- **Added**: `get_referral_statistics()` - Complete referral statistics

### 4. `handlers.py`
- **Updated**: `/start` handler - Uses new referral service
- **Updated**: `callback_activate_trial()` - Activates referral and sends notification
- **Updated**: `callback_referral_stats()` - Uses new statistics function
- **Updated**: Payment handlers - Already use `process_referral_reward()` correctly

## Implementation Details

### 1. Referral Registration

**Location**: `app/services/referrals/service.py::process_referral_registration()`

**Rules**:
- Called on FIRST user interaction (any update with referral code)
- referrer_id is IMMUTABLE (set once, never overwritten)
- Self-referral is blocked
- Referral loops are blocked
- Sets `referred_at` timestamp

**Flow**:
```
User interaction with referral code
  → process_referral_registration()
  → Check if referrer_id already exists (IMMUTABLE)
  → Validate referrer exists
  → Check for loops
  → Register referral (atomic)
  → Set referrer_id + referred_at
  → Return REGISTERED state
```

### 2. Referral Lifecycle

**States**:
- `NONE`: No referral relationship
- `REGISTERED`: User came via referral link (referrer_id set)
- `ACTIVATED`: First paid action OR trial (first_paid_at set)

**Transitions**:
- `NONE → REGISTERED`: On first interaction with referral code
- `REGISTERED → ACTIVATED`: On first payment, topup, or trial

**Activation Points**:
- Trial activation → `activate_referral(type="trial")`
- Balance topup → `process_referral_reward()` sets `first_paid_at`
- Subscription purchase → `process_referral_reward()` sets `first_paid_at`

### 3. Payments & Rewards

**Balance Top-up**:
- `finalize_purchase()` for `period_days == 0`
- Calls `process_referral_reward()` → Activates referral + rewards referrer
- Sends notification to referrer

**Subscription Purchase**:
- `finalize_purchase()` for `period_days > 0`
- Calls `process_referral_reward()` → Activates referral + rewards referrer
- Sends notification with subscription period

**Renewals**:
- Treated as new purchases
- Rewards referrer on EVERY renewal
- Uses same `process_referral_reward()` logic

### 4. Notifications

**Registration Notification**:
- Sent when referral is registered (REGISTERED state)
- Includes: username, registration date
- Message: "Новый реферал зарегистрирован!"

**Trial Activation Notification**:
- Sent when referral activates trial
- Includes: username, trial period
- Message: "Ваш реферал активировал пробный период!"

**Payment Notifications**:
- Sent on balance topup and subscription purchase
- Includes: username, amount, cashback, subscription period
- Uses unified `format_referral_notification_text()` service

### 5. Statistics

**Function**: `get_referral_statistics(partner_id)`

**Returns**:
- `total_invited`: All referrals (from referrals table)
- `active_referrals`: Referrals with `first_paid_at IS NOT NULL`
- `total_cashback_earned`: Sum from `balance_transactions` (type='cashback')
- `last_activity_at`: MAX(first_paid_at) from referrals
- `paid_referrals_count`: Same as active_referrals
- `current_level`: 10, 25, or 45 (based on paid_referrals_count)
- `referrals_to_next`: Referrals needed to next level

**Display**:
- Shows in `/menu_referral` → "Статистика приглашений"
- Includes last activity timestamp

### 6. Data Integrity

**Database Constraints**:
- `referrer_id` is IMMUTABLE (enforced at application level)
- `referred_at` timestamp tracks registration time
- `first_paid_at` tracks activation time
- `referral_rewards.purchase_id` ensures idempotency

**Logging**:
- `REFERRAL_REGISTERED` - Registration successful
- `REFERRAL_ACTIVATED` - Activation (trial/payment)
- `REFERRAL_REWARD_GRANTED` - Cashback awarded
- `REFERRAL_NOTIFICATION_SENT` - Notification sent
- `REFERRAL_SELF_ATTEMPT` - Self-referral blocked
- `REFERRAL_LOOP_DETECTED` - Loop detected

## Migration Notes

### Database Schema Changes

```sql
-- Add referred_at column (already in code)
ALTER TABLE users ADD COLUMN IF NOT EXISTS referred_at TIMESTAMP;

-- Ensure referrals table has first_paid_at
ALTER TABLE referrals ADD COLUMN IF NOT EXISTS first_paid_at TIMESTAMP;
```

### Backward Compatibility

- Existing users without `referrer_id` remain unaffected
- Old `referred_by` column still supported (copied to `referrer_id`)
- No breaking changes to payment or subscription flows
- Statistics queries updated to use `referrals.first_paid_at`

## Testing Checklist

### 1. Referral Registration
- [ ] New user with `/start ref_123456` → referrer_id set
- [ ] Existing user with referral code → referrer_id NOT overwritten
- [ ] Self-referral blocked
- [ ] Referral loop blocked
- [ ] `referred_at` timestamp set

### 2. Trial Activation
- [ ] Trial activation → referral activated (first_paid_at set)
- [ ] Notification sent to referrer
- [ ] No cashback for trial

### 3. Balance Top-up
- [ ] Top-up → referral activated (if not already)
- [ ] Cashback credited to referrer
- [ ] Notification sent with amount
- [ ] Idempotency: duplicate purchase_id blocked

### 4. Subscription Purchase
- [ ] Purchase → referral activated (if not already)
- [ ] Cashback credited to referrer
- [ ] Notification sent with period
- [ ] Renewal also rewards referrer

### 5. Statistics
- [ ] Shows total invited
- [ ] Shows active referrals
- [ ] Shows total cashback earned
- [ ] Shows last activity timestamp
- [ ] Level calculation correct (based on paid referrals)

### 6. Edge Cases
- [ ] Multiple payments → all reward referrer
- [ ] Referrer deleted → referral still tracked
- [ ] Payment fails → no reward, no activation
- [ ] Notification fails → payment still succeeds

## Key Improvements

1. **Deterministic**: Registration happens on first interaction, not just /start
2. **Immutable**: referrer_id cannot be changed once set
3. **Clear States**: REGISTERED → ACTIVATED lifecycle
4. **Payment-Safe**: Every payment rewards referrer
5. **Complete Statistics**: Shows all required metrics
6. **Proper Notifications**: Sent on registration, trial, payment
7. **Idempotent**: All operations safe to retry

## Notes

- Referral binding happens BEFORE any monetization
- Trial activation marks referral as active (no cashback)
- Every payment/topup rewards referrer (not just first)
- Renewals are treated as new purchases
- Statistics use `referrals.first_paid_at` as source of truth
