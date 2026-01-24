# Chaos Engineering Scenarios

This document describes failure scenarios that can be tested using the chaos engineering module.

## Overview

The chaos engineering module (`app/core/chaos.py`) provides safe failure injection capabilities for testing system resilience. All scenarios are:

- **Safe**: Only enabled in dev/staging environments
- **Reversible**: All failures auto-expire or can be manually removed
- **Observable**: All failures are logged with `[CHAOS]` prefix
- **Production-protected**: Cannot be enabled in production by default

## Enabling Chaos Engineering

```bash
# Set environment variable (dev/staging only)
export STAGE_CHAOS_ENABLED=true  # or LOCAL_CHAOS_ENABLED=true

# In code (dev/staging only)
from app.core.chaos import get_chaos_engine
chaos = get_chaos_engine()
chaos.enable()
```

## Supported Scenarios

### C3.2.1 — Database Unavailable (5 minutes)

**Scenario**: Simulate database being unavailable for 5 minutes.

**Expected Behavior**:
- System transitions to UNAVAILABLE state
- Background workers skip iterations
- Cooldown activates after recovery
- Recovery transitions logged
- No crashes, graceful degradation

**Test**:
```python
from app.core.chaos import get_chaos_engine
chaos = get_chaos_engine()
chaos.enable()
failure_id = chaos.inject_db_unavailable(duration_seconds=300)
```

**Verification**:
- Check logs for `[UNAVAILABLE]` messages
- Check logs for `[COOLDOWN]` messages
- Check logs for `[RECOVERY]` messages
- Verify background workers skip iterations
- Verify system recovers after 5 minutes

### C3.2.2 — VPN API Timeout Storm

**Scenario**: Simulate VPN API timeouts for 1 minute.

**Expected Behavior**:
- VPN API component transitions to DEGRADED
- System continues operating (VPN API is non-critical)
- No user-visible errors
- Recovery after timeout expires

**Test**:
```python
from app.core.chaos import get_chaos_engine
chaos = get_chaos_engine()
chaos.enable()
failure_id = chaos.inject_vpn_api_timeout(duration_seconds=60)
```

**Verification**:
- Check logs for `[DEGRADED]` messages
- Verify system continues operating
- Verify recovery after timeout

### C3.2.3 — Payment Provider Failure

**Scenario**: Simulate payment provider failures for 2 minutes.

**Expected Behavior**:
- Payment operations may fail
- System continues operating
- No crashes
- Recovery after failure expires

**Test**:
```python
from app.core.chaos import get_chaos_engine
chaos = get_chaos_engine()
chaos.enable()
failure_id = chaos.inject_payment_failure(duration_seconds=120)
```

**Verification**:
- Check logs for payment failures
- Verify system continues operating
- Verify recovery after timeout

## C3.3 — Chaos Checklist

Each scenario must verify:

### SystemState Transitions
- [ ] SystemState correctly transitions: HEALTHY → DEGRADED → UNAVAILABLE
- [ ] SystemState correctly transitions: UNAVAILABLE → DEGRADED → HEALTHY
- [ ] Transitions are logged with `[RECOVERY]` prefix

### Cooldown Works
- [ ] Cooldown activates after UNAVAILABLE
- [ ] Background workers skip during cooldown
- [ ] Cooldown logs `[COOLDOWN]` messages
- [ ] Cooldown expires after duration

### Alerts Behave Correctly
- [ ] Alerts are generated for UNAVAILABLE > threshold
- [ ] Alerts are suppressed during cooldown
- [ ] Alerts are suppressed during recovery
- [ ] No alert spam (state tracking works)

### No User-Visible Regressions
- [ ] Handlers continue to work (degraded mode)
- [ ] Admin operations bypass system state
- [ ] No new exceptions thrown
- [ ] No HTTP status code changes
- [ ] UX unchanged

### Recovery Self-Healing
- [ ] System recovers without restart
- [ ] Warm-up iterations work correctly
- [ ] Normal operation resumes after recovery
- [ ] Metrics track recovery state

## Manual Testing

1. **Enable chaos engineering** (dev/staging only)
2. **Inject failure** using chaos engine
3. **Observe system behavior**:
   - Check logs for transitions
   - Check metrics for state changes
   - Verify background workers behavior
   - Verify alerts (if any)
4. **Wait for recovery** or manually remove failure
5. **Verify recovery**:
   - System returns to healthy state
   - Cooldown expires
   - Normal operation resumes

## Production Safety

- Chaos engineering is **NEVER** enabled in production by default
- Production check: `if config.APP_ENV == "prod": return False`
- Feature flag required: `CHAOS_ENABLED=true` (dev/staging only)
- All failures auto-expire after duration
- Manual removal available: `chaos.remove_failure(failure_id)`
