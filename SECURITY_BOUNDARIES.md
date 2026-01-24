# Security Boundaries & Compliance Readiness

This document defines security boundaries and compliance rules for the system.

## D3.1 - Security Boundaries

### Handlers Layer

**Rules:**
- ✅ NO secrets in handlers (use config/env)
- ✅ NO raw SQL (use database functions)
- ✅ NO external API calls directly (use services)
- ✅ NO business logic (delegate to services)
- ✅ Input validation required
- ✅ Output sanitization required

**Examples:**
```python
# ✅ CORRECT: Use service layer
result = await subscription_service.create_purchase(...)

# ❌ WRONG: Direct database access
await conn.execute("INSERT INTO ...")
```

### Services Layer

**Rules:**
- ✅ NO Telegram imports (`aiogram`)
- ✅ NO HTTP responses (return data structures)
- ✅ NO logging PII (user IDs, payment details)
- ✅ NO side effects (pure business logic)
- ✅ Domain exceptions only (no generic exceptions)

**Examples:**
```python
# ✅ CORRECT: Return structured data
return SubscriptionStatus(is_active=True, expires_at=...)

# ❌ WRONG: Send Telegram message
await bot.send_message(...)
```

### Infrastructure Layer

**Rules:**
- ✅ Retries bounded (max retries defined)
- ✅ Timeouts mandatory (all external calls)
- ✅ Circuit-breaker semantics respected (cooldown)
- ✅ No infinite loops (max iterations)
- ✅ Resource limits enforced (connection pools)

**Examples:**
```python
# ✅ CORRECT: Bounded retries
await retry_async(fn, retries=2, ...)

# ❌ WRONG: Infinite retries
while True:
    try:
        await operation()
    except:
        pass  # Infinite loop
```

## D3.2 - Audit Trail Policy

### Required Audit Events

1. **Payment Events** (REQUIRED):
   - Payment creation
   - Payment finalization
   - Payment failures
   - Fields: `payment_id`, `user_id`, `amount`, `status`, `timestamp`
   - NEVER log: `card_number`, `cvv`, `payment_token`

2. **Subscription Lifecycle** (REQUIRED):
   - Subscription creation
   - Subscription activation
   - Subscription expiry
   - Subscription renewal
   - Fields: `subscription_id`, `user_id`, `action`, `status`, `timestamp`

3. **Admin Actions** (REQUIRED):
   - VIP grant/revoke
   - Discount create/delete
   - User block/unblock
   - Reissue operations
   - Fields: `admin_id`, `action`, `target_user_id`, `timestamp`

4. **System Degradation** (REQUIRED):
   - Component transitions (HEALTHY → DEGRADED → UNAVAILABLE)
   - Recovery events
   - Cooldown activations
   - Fields: `component`, `status`, `transition`, `timestamp`

5. **Security Events** (REQUIRED):
   - Authentication failures
   - Authorization violations
   - Suspicious patterns
   - Fields: `event_type`, `severity`, `timestamp`
   - NEVER log: `password`, `token`, `secret`

### Recommended Audit Events

1. **Trial Activation** (RECOMMENDED):
   - Trial start
   - Trial completion
   - Fields: `user_id`, `trial_id`, `status`, `timestamp`

2. **VPN Lifecycle** (RECOMMENDED):
   - UUID creation
   - UUID removal
   - Fields: `user_id`, `uuid`, `action`, `timestamp`
   - NEVER log: `vless_link` (may contain sensitive routing info)

### Audit Retention

- **Payment Events**: 365 days
- **Subscription Lifecycle**: 365 days
- **Admin Actions**: 365 days
- **System Degradation**: 90 days
- **Security Events**: 365 days
- **Trial Activation**: 90 days
- **VPN Lifecycle**: 90 days

## D3.3 - Incident Readiness

### Incident Context

Every system degradation episode gets:
- **Incident ID**: UUID for correlation
- **Start Time**: When incident began
- **Correlation ID**: For log/alert/metric correlation

### Timeline Reconstruction

**Answer to "What happened?" ≤ 5 minutes:**

1. **Check Incident Context**:
   - Current incident ID (if any)
   - Incident start time

2. **Check SystemState**:
   - Component statuses
   - Transition history

3. **Check Metrics**:
   - Latency spikes
   - Retry counts
   - Cost anomalies

4. **Check Alerts**:
   - Alert history
   - Alert correlation with incident ID

5. **Check Logs**:
   - Filter by incident ID
   - Filter by time window
   - Filter by component

### Correlation Example

```python
# Incident started
incident_id = "abc-123-def-456"
logger.warning(f"[INCIDENT {incident_id}] System unavailable")

# All subsequent logs include incident_id
logger.error(f"[INCIDENT {incident_id}] Database connection failed")
logger.info(f"[INCIDENT {incident_id}] Recovery started")

# Metrics tagged with incident_id
metrics.set_gauge("system_state_status", 2.0, metadata={"incident_id": incident_id})

# Alerts include incident_id
alert.metadata["incident_id"] = incident_id
```

## Compliance Checklist

- [ ] No secrets in code (all in config/env)
- [ ] No raw SQL in handlers
- [ ] No external calls in handlers
- [ ] No Telegram in services
- [ ] No PII in logs
- [ ] Retries bounded
- [ ] Timeouts mandatory
- [ ] Audit trail complete
- [ ] Incident correlation ready
- [ ] Security boundaries documented
