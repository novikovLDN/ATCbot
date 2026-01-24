# activation_worker.py Analysis & Fixes

## Executive Summary

**Issues Found:** 5 critical issues  
**Idempotency Issues:** 2  
**State Transition Issues:** 2  
**Observability Gaps:** 1

---

## 1. Idempotency Analysis

### ❌ Issue 1: `attempt_activation()` Lacks Idempotency Check

**Location:** `app/services/activation/service.py` line 293-363

**Problem:**
- `attempt_activation()` does NOT check if subscription is already active before calling VPN API
- If called twice (race condition), it will:
  1. Create duplicate UUIDs in VPN API
  2. Overwrite existing activation_status
  3. Waste VPN API resources

**Current Code:**
```python
async def attempt_activation(...):
    # No check for activation_status before calling VPN API
    vless_result = await vpn_utils.add_vless_user()  # Creates UUID even if already active
    # ...
    await _update_subscription_activated(...)  # Updates without checking current status
```

**Fix Required:**
- Add idempotency check: Verify `activation_status = 'pending'` before calling VPN API
- Use `is_activation_allowed()` helper (already exists but not used in `attempt_activation()`)
- Add WHERE clause in UPDATE to ensure only pending subscriptions are updated

---

### ❌ Issue 2: UPDATE Query Lacks Status Check

**Location:** `app/services/activation/service.py` line 374-383

**Problem:**
- UPDATE query doesn't check `activation_status = 'pending'` in WHERE clause
- Could update already-active subscriptions
- Could overwrite 'failed' status

**Current Code:**
```python
await conn.execute(
    """UPDATE subscriptions
       SET uuid = $1,
           vpn_key = $2,
           activation_status = 'active',
           activation_attempts = $3,
           last_activation_error = NULL
       WHERE id = $4""",  # ❌ No activation_status check
    uuid, vpn_key, new_attempts, subscription_id
)
```

**Fix Required:**
- Add `AND activation_status = 'pending'` to WHERE clause
- Log if update affects 0 rows (subscription already activated)

---

### ⚠️ Issue 3: Duplicate User Notifications (Race Condition)

**Location:** `activation_worker.py` line 213-226

**Problem:**
- User notification is sent AFTER activation succeeds
- If two workers process the same subscription simultaneously:
  1. Both call `attempt_activation()` (one succeeds, one fails idempotency check)
  2. Both send notifications (duplicate messages to user)

**Current Code:**
```python
result = await activation_service.attempt_activation(...)  # Could succeed for both workers
# ...
await bot.send_message(...)  # Both workers send notification
```

**Fix Required:**
- Check activation_status before sending notification
- Or: Use idempotency check in notification sending (check if notification already sent)

---

## 2. VPN_API Unavailable State Handling

### ❌ Issue 4: VPN_API Temporarily Unavailable → Permanent Failure

**Location:** `activation_worker.py` line 237-292, `app/services/activation/service.py` line 322-323

**Problem:**
- If VPN_API is temporarily unavailable:
  1. Worker retries until max attempts (5 by default)
  2. Subscription is marked as 'failed'
  3. When VPN_API becomes available, subscription is already 'failed' and won't be retried
  4. Requires manual intervention to reactivate

**Current Flow:**
```
VPN_API unavailable → VPNActivationError → increment attempts → mark as failed (if max reached)
```

**Expected Flow:**
```
VPN_API unavailable → VPNActivationError → increment attempts → keep as 'pending' (if VPN_API is degraded, not permanently disabled)
```

**Fix Required:**
- Distinguish between:
  - VPN_API permanently disabled (`config.VPN_ENABLED = False`) → mark as failed
  - VPN_API temporarily unavailable (circuit breaker, network error) → keep as pending, retry later
- Add check: If VPN_API is degraded (not disabled), don't mark as failed, keep as pending

---

### ⚠️ Issue 5: Missing Log When VPN_API Unavailable

**Location:** `activation_worker.py` line 237-292

**Problem:**
- When VPN_API is unavailable, error is logged but no explicit log indicates:
  - Subscription remains pending (not failed)
  - Will be retried in next iteration
  - VPN_API status (degraded vs disabled)

**Fix Required:**
- Add explicit log: `ACTIVATION_SKIP_VPN_UNAVAILABLE [subscription_id=..., reason=...]`
- Log VPN_API status (degraded vs disabled)
- Log whether subscription will be retried or marked as failed

---

## 3. State Transition Ambiguity

### ❌ Issue 6: Race Condition in get_pending_subscriptions()

**Location:** `app/services/activation/service.py` line 192-219

**Problem:**
- Two workers could fetch the same subscription simultaneously:
  1. Worker A fetches subscription_id=123 (status='pending', attempts=2)
  2. Worker B fetches subscription_id=123 (status='pending', attempts=2)
  3. Worker A processes it, updates to 'active'
  4. Worker B also processes it (idempotency check should prevent, but currently doesn't)

**Current Code:**
```python
rows = await conn.fetch(
    """SELECT ...
       WHERE activation_status = 'pending'
         AND activation_attempts < $1
       ORDER BY id ASC
       LIMIT $2""",
    max_attempts, limit
)
```

**Fix Required:**
- Add row-level locking (SELECT FOR UPDATE SKIP LOCKED)
- Or: Use idempotency check in `attempt_activation()` to prevent duplicate processing

---

## 4. Observability Gaps

### ⚠️ Issue 7: Missing Idempotency Logs

**Location:** `app/services/activation/service.py` line 293-363

**Problem:**
- No explicit log when activation is skipped due to idempotency (already active)
- No log when UPDATE affects 0 rows (subscription already activated)

**Fix Required:**
- Add log: `ACTIVATION_SKIP_IDEMPOTENT [subscription_id=..., reason=already_active]`
- Log UPDATE result (rows affected)

---

## 5. Exact Code Fixes

### Fix 1: Add Idempotency Check to `attempt_activation()`

**File:** `app/services/activation/service.py`  
**Location:** Line 293-363

**Change:**
```python
async def attempt_activation(
    subscription_id: int,
    telegram_id: int,
    current_attempts: int,
    conn: Optional[Any] = None
) -> ActivationResult:
    """
    Attempt to activate a subscription by calling VPN API.
    
    IDEMPOTENCY: This function is idempotent - if subscription is already active,
    it will not create duplicate UUIDs or overwrite existing activation.
    """
    # IDEMPOTENCY CHECK: Verify subscription is still pending before proceeding
    if conn is None:
        pool = await database.get_pool()
        if pool is None:
            raise ActivationFailedError("Database pool is not available")
        async with pool.acquire() as conn:
            async with conn.transaction():
                return await _attempt_activation_with_idempotency(
                    conn, subscription_id, telegram_id, current_attempts
                )
    else:
        async with conn.transaction():
            return await _attempt_activation_with_idempotency(
                conn, subscription_id, telegram_id, current_attempts
            )


async def _attempt_activation_with_idempotency(
    conn: Any,
    subscription_id: int,
    telegram_id: int,
    current_attempts: int
) -> ActivationResult:
    """Internal helper with idempotency check"""
    # Check current subscription status (with row lock to prevent race conditions)
    subscription_row = await conn.fetchrow(
        """SELECT activation_status, uuid, vpn_key, activation_attempts
           FROM subscriptions
           WHERE id = $1
           FOR UPDATE SKIP LOCKED""",
        subscription_id
    )
    
    if not subscription_row:
        raise ActivationFailedError(f"Subscription {subscription_id} not found")
    
    current_status = subscription_row["activation_status"]
    
    # IDEMPOTENCY: If already active, return existing activation
    if current_status == "active":
        return ActivationResult(
            success=True,
            uuid=subscription_row.get("uuid"),
            vpn_key=subscription_row.get("vpn_key"),
            activation_status="active",
            attempts=subscription_row.get("activation_attempts", current_attempts)
        )
    
    # Verify still pending
    if current_status != "pending":
        raise ActivationNotAllowedError(
            f"Subscription {subscription_id} is not pending (status={current_status})"
        )
    
    # Check if VPN API is available
    if not config.VPN_ENABLED:
        raise VPNActivationError("VPN API is not enabled")
    
    # Call VPN API to create UUID
    try:
        vless_result = await vpn_utils.add_vless_user()
        new_uuid = vless_result.get("uuid")
        vless_url = vless_result.get("vless_url")
    except Exception as e:
        raise VPNActivationError(f"VPN API call failed: {e}") from e
    
    # Validate UUID
    if not new_uuid:
        raise VPNActivationError("VPN API returned empty UUID")
    
    # Validate vless_url
    if not vless_url:
        raise VPNActivationError("VPN API returned empty vless_url")
    
    # Validate vless link format
    if not vpn_utils.validate_vless_link(vless_url):
        raise VPNActivationError("VPN API returned invalid vless_url (contains flow=)")
    
    # Update subscription in database (with idempotency check in WHERE clause)
    rows_affected = await conn.execute(
        """UPDATE subscriptions
           SET uuid = $1,
               vpn_key = $2,
               activation_status = 'active',
               activation_attempts = $3,
               last_activation_error = NULL
           WHERE id = $4
             AND activation_status = 'pending'""",  # ✅ Idempotency check
        new_uuid, vless_url, current_attempts + 1, subscription_id
    )
    
    # Verify update succeeded
    if rows_affected == "UPDATE 0":
        # Another worker already activated this subscription
        # Fetch current state
        updated_row = await conn.fetchrow(
            "SELECT uuid, vpn_key, activation_status, activation_attempts FROM subscriptions WHERE id = $1",
            subscription_id
        )
        if updated_row and updated_row["activation_status"] == "active":
            return ActivationResult(
                success=True,
                uuid=updated_row.get("uuid"),
                vpn_key=updated_row.get("vpn_key"),
                activation_status="active",
                attempts=updated_row.get("activation_attempts", current_attempts + 1)
            )
        raise ActivationFailedError(f"Failed to update subscription {subscription_id} (concurrent modification)")
    
    return ActivationResult(
        success=True,
        uuid=new_uuid,
        vpn_key=vless_url,
        activation_status="active",
        attempts=current_attempts + 1
    )
```

---

### Fix 2: Improve VPN_API Unavailable Handling

**File:** `activation_worker.py`  
**Location:** Line 237-292

**Change:**
```python
except VPNActivationError as e:
    # VPN API error - check if VPN_API is permanently disabled or temporarily unavailable
    error_msg = str(e)
    new_attempts = current_attempts + 1
    
    # Check VPN_API status
    from app.core.system_state import recalculate_from_runtime, ComponentStatus
    system_state = recalculate_from_runtime()
    vpn_api_permanently_disabled = not config.VPN_ENABLED
    vpn_api_temporarily_unavailable = (
        system_state.vpn_api.status == ComponentStatus.DEGRADED and
        config.VPN_ENABLED
    )
    
    if vpn_api_permanently_disabled:
        # VPN_API is permanently disabled - mark as failed if max attempts reached
        logger.warning(
            f"ACTIVATION_FAILED_VPN_DISABLED [subscription_id={subscription_id}, "
            f"user={telegram_id}, attempt={new_attempts}/{MAX_ACTIVATION_ATTEMPTS}, "
            f"error={error_msg}]"
        )
    elif vpn_api_temporarily_unavailable:
        # VPN_API is temporarily unavailable - keep as pending, will retry
        logger.info(
            f"ACTIVATION_SKIP_VPN_UNAVAILABLE [subscription_id={subscription_id}, "
            f"user={telegram_id}, attempt={new_attempts}/{MAX_ACTIVATION_ATTEMPTS}, "
            f"reason=VPN_API_temporarily_unavailable, will_retry=True]"
        )
    else:
        # VPN_API error (network, timeout, etc.) - increment attempts
        logger.warning(
            f"ACTIVATION_FAILED [subscription_id={subscription_id}, "
            f"user={telegram_id}, attempt={new_attempts}/{MAX_ACTIVATION_ATTEMPTS}, "
            f"error={error_msg}]"
        )
    
    try:
        # Only mark as failed if VPN_API is permanently disabled AND max attempts reached
        # Otherwise, keep as pending for retry
        should_mark_failed = (
            vpn_api_permanently_disabled and
            new_attempts >= MAX_ACTIVATION_ATTEMPTS
        )
        
        await activation_service.mark_activation_failed(
            subscription_id=subscription_id,
            new_attempts=new_attempts,
            error_msg=error_msg,
            max_attempts=MAX_ACTIVATION_ATTEMPTS,
            conn=conn,
            mark_as_failed=should_mark_failed  # New parameter
        )
        
        # If max attempts reached and VPN_API is permanently disabled, send admin notification
        if should_mark_failed:
            logger.error(
                f"ACTIVATION_FAILED_FINAL [subscription_id={subscription_id}, "
                f"user={telegram_id}, attempts={new_attempts}, error={error_msg}]"
            )
            # ... admin notification code ...
```

---

### Fix 3: Add Idempotency Check Before User Notification

**File:** `activation_worker.py`  
**Location:** Line 194-234

**Change:**
```python
# Send notification to user (with idempotency check)
try:
    # IDEMPOTENCY: Verify subscription is still active before sending notification
    # (prevents duplicate notifications if two workers process same subscription)
    subscription_check = await conn.fetchrow(
        "SELECT activation_status, uuid FROM subscriptions WHERE id = $1",
        subscription_id
    )
    
    if not subscription_check or subscription_check["activation_status"] != "active":
        logger.warning(
            f"ACTIVATION_NOTIFICATION_SKIP [subscription_id={subscription_id}, "
            f"user={telegram_id}, reason=subscription_not_active]"
        )
        continue
    
    # Only send notification if this is the first activation (not a retry of already-active subscription)
    if subscription_check.get("uuid") != result.uuid:
        logger.info(
            f"ACTIVATION_NOTIFICATION_SKIP_IDEMPOTENT [subscription_id={subscription_id}, "
            f"user={telegram_id}, reason=already_notified]"
        )
        continue
    
    user = await database.get_user(telegram_id)
    # ... rest of notification code ...
```

---

## 6. Summary of Issues

| Issue | Severity | Location | Fix Priority |
|-------|----------|----------|--------------|
| Idempotency check missing in `attempt_activation()` | Critical | service.py:293 | High |
| UPDATE lacks status check | Critical | service.py:374 | High |
| VPN_API unavailable → permanent failure | High | activation_worker.py:237 | High |
| Race condition in get_pending_subscriptions() | Medium | service.py:192 | Medium |
| Missing observability logs | Low | Multiple | Low |

---

## 7. Testing Recommendations

1. **Idempotency Test:**
   - Call `attempt_activation()` twice for same subscription
   - Verify: Only one UUID created, only one notification sent

2. **Race Condition Test:**
   - Start two workers simultaneously
   - Verify: No duplicate activations, no duplicate notifications

3. **VPN_API Unavailable Test:**
   - Disable VPN_API temporarily
   - Verify: Subscriptions remain pending, not marked as failed
   - Re-enable VPN_API
   - Verify: Subscriptions are retried and activated

4. **State Transition Test:**
   - Verify: pending → active (success)
   - Verify: pending → failed (max attempts, VPN_API disabled)
   - Verify: pending → pending (VPN_API temporarily unavailable)
