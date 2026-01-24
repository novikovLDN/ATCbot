# Load Shedding Policy

This document defines load shedding priorities and policies for graceful degradation.

## Overview

Load shedding is the process of dropping non-critical traffic to protect critical operations during high load or failures.

**IMPORTANT:**
- ⚠️ **Load shedding is explicit** - No silent throttling
- ⚠️ **Priorities are observable** - All decisions logged
- ⚠️ **Core UX protected** - Critical operations always on

---

## Traffic Priorities

### CRITICAL (Always On)

**Operations:**
- Payment finalization
- Subscription activation (immediate)
- User registration
- Admin critical actions

**Behavior:**
- ✅ Always enabled
- ✅ Always protected
- ✅ Never dropped
- ✅ No throttling

**Examples:**
- `process_successful_payment` handler
- `callback_activate_trial` handler (immediate activation)
- `process_admin_user_id` handler (critical admin)

### HIGH (Protected)

**Operations:**
- Subscription activation (delayed)
- VPN key generation
- Balance top-up
- Subscription renewal

**Behavior:**
- ✅ Enabled under degradation
- ❌ Disabled under unavailability
- ✅ Protected from load shedding
- ⚠️ May be throttled

**Examples:**
- `activation_worker` (delayed activation)
- `callback_copy_vpn_key` handler
- `callback_topup_balance_amount` handler

### NORMAL (Throttled)

**Operations:**
- UI handlers
- Profile views
- Subscription status checks
- Trial activation requests

**Behavior:**
- ✅ Enabled under degradation
- ❌ Disabled under unavailability
- ⚠️ Throttled under load
- ⚠️ May be dropped if necessary

**Examples:**
- `callback_profile` handler
- `callback_tariff_type` handler
- `callback_tariff_period` handler

### LOW (Dropped First)

**Operations:**
- Analytics
- Retries
- Background workers (non-critical)
- Metrics collection (non-critical)

**Behavior:**
- ❌ Disabled under degradation
- ❌ Disabled under unavailability
- ❌ Dropped first under load
- ⚠️ No protection

**Examples:**
- Analytics event collection
- Non-critical retries
- Background worker iterations (non-critical)
- Metrics aggregation (non-critical)

---

## Load Shedding Rules

### Under Normal Load

**All Priorities:**
- ✅ Enabled
- ✅ No throttling
- ✅ No dropping

### Under Degradation

**CRITICAL:**
- ✅ Enabled
- ✅ No throttling

**HIGH:**
- ✅ Enabled
- ⚠️ May be throttled

**NORMAL:**
- ✅ Enabled
- ⚠️ Throttled (50% capacity)

**LOW:**
- ❌ Disabled
- ❌ Dropped

### Under Unavailability

**CRITICAL:**
- ✅ Enabled
- ✅ No throttling

**HIGH:**
- ❌ Disabled
- ❌ Dropped

**NORMAL:**
- ❌ Disabled
- ❌ Dropped

**LOW:**
- ❌ Disabled
- ❌ Dropped

---

## Load Shedding Implementation

### Priority Check

**Before Operation:**
```python
from app.core.traffic_priority import TrafficPriority, is_priority_enabled
from app.core.system_state import SystemState

# Check if operation is enabled
if not is_priority_enabled(
    TrafficPriority.CRITICAL,
    is_degraded=system_state.is_degraded,
    is_unavailable=system_state.is_unavailable
):
    # Operation disabled - return early
    return
```

### Throttling

**For NORMAL Priority:**
- Reduce capacity to 50% under degradation
- Use rate limiting
- Use bulkhead limits

### Dropping

**For LOW Priority:**
- Disable completely under degradation
- Log as INFO (not ERROR)
- Return early without processing

---

## Load Shedding Order

### Shedding Sequence

1. **Analytics** (LOW)
   - First to be dropped
   - No user impact
   - Can be recovered later

2. **Retries** (LOW)
   - Reduce retry frequency
   - Increase backoff delays
   - Drop non-critical retries

3. **Admin Operations** (NORMAL)
   - Throttle admin operations
   - Protect critical admin actions
   - Drop non-critical admin

4. **Background Workers** (NORMAL)
   - Reduce worker frequency
   - Drop non-critical workers
   - Protect critical workers

5. **User Critical Path** (CRITICAL)
   - Last to be affected
   - Always protected
   - Never dropped

---

## Load Shedding Metrics

### Tracked Metrics

**Shedding Events:**
- Operations dropped by priority
- Operations throttled by priority
- Operations protected by priority

**Impact Metrics:**
- User requests dropped
- Background operations dropped
- Analytics events dropped

**Recovery Metrics:**
- Operations resumed
- Capacity restored
- Normal operation resumed

---

## Load Shedding Recovery

### Automatic Recovery

**When System Recovers:**
1. CRITICAL: Already enabled
2. HIGH: Re-enabled automatically
3. NORMAL: Re-enabled automatically
4. LOW: Re-enabled automatically

### Manual Recovery

**If Needed:**
- Operator can manually re-enable priorities
- Operator can adjust throttling
- Operator can restore capacity

---

## Notes

- ⚠️ **Core UX always protected** - CRITICAL operations never dropped
- ⚠️ **Load shedding is explicit** - All decisions logged
- ⚠️ **Priorities are observable** - Status visible in metrics
- ⚠️ **Recovery is automatic** - Priorities re-enabled on recovery
