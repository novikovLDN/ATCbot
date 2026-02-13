# UUID Consistency & Xray Drift — Architecture Audit

## Audit Logs Added (Temporary — for diagnostics)

| Location | Log Key | Purpose |
|----------|---------|---------|
| database.py grant_access RENEWAL | `UUID_AUDIT_DB_VALUE` | telegram_id, uuid_from_db, repr, len |
| vpn_utils.update_vless_user | `UUID_AUDIT_UPDATE_REQUEST` | uuid_raw, uuid_sent, len_raw, len_sent, IS_STAGE |
| vpn_utils.add_vless_user | `UUID_AUDIT_ADD_REQUEST` | uuid_arg, uuid_sent_to_api |
| xray_api add-user | `UUID_AUDIT_API_RECEIVED` | request.uuid |
| xray_api update-user | `UUID_AUDIT_LOOKUP` | uuid_sought, existing_count, first_5_full, match |

---

## UUID Flow Summary

### 1. DB Source of Truth

- **Column:** `subscriptions.uuid`
- **Format:** Stored as returned by vpn_utils
  - **PROD:** Raw UUID (e.g. `a1b2c3d4-e5f6-...`)
  - **STAGE:** Prefixed `stage-` (e.g. `stage-a1b2c3d4-e5f6-...`) — vpn_utils adds prefix to response
- **Read:** No transformation; `subscription.get("uuid")` returns as stored

### 2. Update Path (renewal)

```
DB uuid → grant_access → update_vless_user(uuid)
                              ↓
                    uuid_raw = uuid.strip()
                    uuid_clean = uuid_raw
                    if IS_STAGE and uuid_clean.startswith("stage-"):
                        uuid_clean = uuid_clean[6:]   # Strip prefix
                              ↓
                    POST /update-user {"uuid": uuid_clean, ...}
```

- **vpn_utils:** Strips `stage-` before sending when `IS_STAGE`
- **Xray API:** Receives `uuid_clean`, looks up by exact match in config clients

### 3. Add Path (new issuance / recreate)

```
add_vless_user(telegram_id, subscription_end, uuid=None)
  - If uuid provided (recreate): uuid_clean = strip, strip stage- if IS_STAGE
  - json_body["uuid"] = uuid_clean (sent to API)
  - API uses as-is, stores in config
```

- **New issuance:** No uuid → API generates, stores raw
- **Recreate:** uuid provided → API uses uuid_clean (no prefix)

### 4. Xray Config Stored Format

- **Always raw UUID** (no `stage-` prefix in config file)
- Config `clients[].id` = UUID string
- Add-user stores `new_uuid` directly (generated or from request.uuid)

### 5. Potential Root Causes of 404

| Cause | Check |
|-------|-------|
| **Prefix mismatch** | DB has `stage-xxx`, we strip to `xxx`, Xray has `xxx` → should match. If Xray had `stage-xxx` (wrong), 404. |
| **Config persistence** | After container restart, config must be on persistent volume |
| **Wrong inbound** | update-user searches first VLESS inbound only? No — iterates all VLESS inbounds |
| **Client never added** | Manual Xray restart, config reverted, or add failed before save |
| **Case/whitespace** | target_uuid.strip() — no case change. Exact match required. |

### 6. Detection Criteria (from logs)

- **A. Does update_vless_user ever succeed?** → Check for `vpn_api update_user: SUCCESS`
- **B. DB uuid == Xray config uuid?** → Compare `UUID_AUDIT_DB_VALUE.uuid_from_db` (after strip) with `UUID_AUDIT_LOOKUP.first_5_full`
- **C. Prefix mismatch?** → `UUID_AUDIT_UPDATE_REQUEST` shows uuid_raw vs uuid_sent
- **D. Update always 404?** → If `UUID_AUDIT_LOOKUP.match=False` and existing_count>0, uuid_sought not in config

---

## Recommendations

1. **Run renewal** and capture logs; inspect `UUID_AUDIT_*` sequence
2. **If match=False** with existing_count>0 → UUID format mismatch (prefix, trim, etc.)
3. **If existing_count=0** → Config empty or client never persisted
4. **If match=True** but still 404 → Logic bug (should not occur)
