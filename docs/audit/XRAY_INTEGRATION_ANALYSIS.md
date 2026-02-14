# Xray Integration Analysis

## API Contract

- **Add user:** POST /add-user — body: `{telegram_id, uuid, expiry_timestamp_ms}`
- **Update user:** POST /update-user — body: `{uuid, expiry_timestamp_ms}`
- **Remove user:** POST /remove-user/{uuid}
- **Health:** GET /health

UUID is always provided by the bot (DB is source of truth). Xray does not generate UUIDs.

## UUID Lifecycle

| State | DB | Xray |
|-------|-----|------|
| New subscription | INSERT with uuid, vpn_key | add-user called with uuid |
| Renewal | UPDATE expires_at only | No call (uuid unchanged) |
| Pending activation | INSERT with activation_status='pending', uuid=NULL | add-user called by activation_worker |
| Expiration | UPDATE status='expired', uuid=NULL | remove-user called |
| Admin reissue | UPDATE uuid, vpn_key | remove-user(old), add-user(new) |

## Orphan UUID Risk

**Definition:** UUID exists in Xray but not in DB (or DB shows expired).

| Scenario | Likelihood | Cause |
|----------|------------|-------|
| Activation race | HIGH | Two workers: A and B both add_vless_user with different UUIDs. A's UPDATE wins. B's UUID is orphan. |
| grant_access rollback | MEDIUM | add_vless_user succeeds, then DB INSERT/UPDATE fails → rollback. UUID in Xray, no DB row. |
| Expiration failure | LOW | remove_vless_user fails, DB not updated. Retry next cycle. |
| VPN disabled at expiry | MEDIUM | fast_expiry_cleanup: VPN disabled → skip remove, still UPDATE DB. DB says expired, UUID may still work if Xray has it. |

## Ghost Subscription Risk

**Definition:** DB shows active but Xray has no user (or wrong uuid).

| Scenario | Likelihood | Cause |
|----------|------------|-------|
| Xray restart | LOW | Config not persisted; users lost. xray_sync can reconcile. |
| add_vless_user timeout | MEDIUM | Call times out after Xray created user; we rollback/retry. Possible inconsistency. |
| Wrong uuid in DB | LOW | Bug in save logic. |

## Idempotency of Xray Operations

- **add-user:** Not idempotent. Same uuid twice = duplicate user or error (provider-dependent).
- **remove-user:** Idempotent. Removing non-existent uuid typically returns success.
- **update-user:** Idempotent for same uuid.

## Renewal Invariant

Renewal MUST NOT call add_vless_user. Verified in grant_access:

```
IF subscription exists AND status == "active" AND expires_at > now AND uuid IS NOT NULL:
    → RENEWAL PATH: UPDATE expires_at only, NO add_vless_user
```

UUID is never regenerated on renewal.
