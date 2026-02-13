# UUID Consistency — Single Source of Truth

## Architecture (Post-Refactor)

**UUID is a pure 36-char identity token. No prefixes. No transformation.**

| Layer | Format | Rule |
|-------|--------|------|
| DB `subscriptions.uuid` | Raw UUID (36 chars) | Stored exactly as returned by API |
| vpn_utils | Pass-through | Sends uuid exactly as stored; no strip/add |
| xray_api | Pass-through | Uses request.uuid as-is; no modification |
| Xray config `clients[].id` | Raw UUID | Always 36-char UUID |

---

## Audit Logs (Diagnostics)

| Location | Log Key | Purpose |
|----------|---------|---------|
| database.py grant_access RENEWAL | `UUID_AUDIT_DB_VALUE` | telegram_id, uuid_from_db, repr |
| vpn_utils.update_vless_user | `UUID_AUDIT_UPDATE_REQUEST` | uuid sent to API |
| vpn_utils.add_vless_user | `UUID_AUDIT_ADD_REQUEST` | uuid_arg, uuid_sent_to_api |
| xray_api add-user | `UUID_AUDIT_API_RECEIVED` | request.uuid |
| xray_api update-user | `UUID_AUDIT_LOOKUP` | uuid_sought, existing_count, match |

---

## Renewal Flow

```
DB uuid (raw) → grant_access → update_vless_user(uuid)
                                    ↓
                    POST /update-user {"uuid": uuid, ...}  # exact match
                                    ↓
                    If 404 → add_user(uuid=uuid)  # SAME uuid, idempotent recreate
```

- **update** uses DB uuid as-is
- **add (recreate)** uses same uuid when client missing
- DB uuid never changes on renewal
- Xray config does not grow on renewals

---

## Migration

`migrations/022_remove_uuid_prefix.sql` normalizes existing `stage-` prefixed UUIDs:

```sql
UPDATE subscriptions SET uuid = substring(uuid from 7) WHERE uuid LIKE 'stage-%';
```

Run once. Raw UUIDs are untouched.

---

## Verification

1. DB: `SELECT length(uuid), uuid FROM subscriptions WHERE uuid IS NOT NULL` → all length 36
2. No `uuid LIKE 'stage-%'` in DB
3. Renewal: update succeeds without recreate
4. Self-heal: add_user(uuid=uuid) only when client truly missing
