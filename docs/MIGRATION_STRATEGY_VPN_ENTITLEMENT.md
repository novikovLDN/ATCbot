# Migration Strategy: VPN Entitlement Refactor (Orphan UUID Elimination)

## 1. Current Risk

**Orphan UUID on rollback:** If `add_vless_user` (VPN API call) runs inside a DB transaction and the transaction rolls back after the API succeeds, the UUID remains active in Xray but is not stored in the DB. Result: free VPN access, no audit trail, security risk.

## 2. Two-Phase Activation Explanation

**Phase 1 (outside DB transaction):**
1. Call VPN API → `add_vless_user`
2. Receive UUID + vless_link
3. Validate response schema

**Phase 2 (DB transaction):**
1. BEGIN
2. Update pending_purchase → paid
3. Insert payment row
4. Grant subscription with provided UUID
5. COMMIT

If Phase 2 fails: call `remove_vless_user(uuid)`, log `ORPHAN_PREVENTED`.

## 3. Why External Calls Inside DB Transactions Are Unsafe

- DB transaction can roll back for many reasons (referral error, constraint violation, timeout, crash)
- External HTTP call is not part of the transaction
- VPN API success is irreversible from DB perspective
- Rollback undoes DB writes but cannot undo the VPN API call

## 4. Migration Plan (Zero Downtime)

1. **Deploy code** (two-phase activation already in place for finalize_purchase; extended to admin_grant_*, finalize_balance_purchase, approve_payment_atomic)
2. **Run migration 024** — TIMESTAMPTZ, UNIQUE uuid, indexes
3. **Deploy Xray API** with GET /list-users endpoint
4. **Enable reconciliation** — set `XRAY_RECONCILIATION_ENABLED=true` (optional, recommended for production)
5. **Monitor** — ORPHAN_PREVENTED logs, reconciliation metrics

## 5. Rollback Plan

- Code: revert to previous commit; two-phase is backward-compatible (callers that don't pass `pre_provisioned_uuid` still work if they don't hold a transaction)
- Migration 024: TIMESTAMPTZ→TIMESTAMP rollback is complex; recommend forward-only. UNIQUE index can be dropped: `DROP INDEX IF EXISTS idx_subscriptions_uuid_unique`
- Reconciliation: set `XRAY_RECONCILIATION_ENABLED=false`

## 6. Monitoring Plan

- **ORPHAN_PREVENTED** — CRITICAL log; indicates Phase 2 failed and we removed UUID
- **ORPHAN_PREVENTED_REMOVAL_FAILED** — removal failed; manual intervention needed
- **reconciliation_orphans_found**, **reconciliation_orphans_removed**, **reconciliation_missing_in_xray** — metrics
- **reconciliation_removed** — INFO log per orphan removed
- **reconciliation_missing_in_xray** — CRITICAL; UUID in DB but not in Xray

## 7. Expected Logs During First Deployment

- `admin_grant_access_atomic: TWO_PHASE_PHASE1_DONE` — when admin grants access
- `finalize_purchase: TWO_PHASE_PHASE1_DONE` — when CryptoBot/Telegram payment completes
- `Xray reconciliation task started` — if XRAY_RECONCILIATION_ENABLED=true
- No `ORPHAN_PREVENTED` under normal operation

## 8. Manual Reconciliation Command Example

Run reconciliation once (e.g. from admin script or Python REPL):

```python
import asyncio
from reconcile_xray_state import reconcile_xray_state

result = asyncio.run(reconcile_xray_state())
print(result)
# {"orphans_found": N, "orphans_removed": N, "missing_in_xray": N, "errors": []}
```

Or via bot admin command (if implemented): `/reconcile_xray`
