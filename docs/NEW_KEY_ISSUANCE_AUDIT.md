# NEW KEY ISSUANCE FLOW — Production Audit

**Context:** Intermittent session instability affecting ONLY newly issued VPN keys. Traffic stops after minutes/hours; reconnect restores it. ~50 users/min load spike occurred.

---

## PHASE 1 — Full New Key Issuance Path

### Call chain

1. **database.grant_access()** (`database.py` ~4133)
   - Detects `NEW_ISSUANCE_REQUIRED` when no active subscription or subscription expired
   - Computes `subscription_end = now + duration`
   - Calls `vpn_utils.add_vless_user()` — **NO arguments**
   - **subscription_end is never passed to VPN API**

2. **vpn_utils.add_vless_user()** (`vpn_utils.py` ~284)
   - `response = await client.post(url, headers=headers)` — **NO request body**
   - Sends POST to `{XRAY_API_URL}/add-user`
   - **No payload: no expireTime, no subscription_end, no duration**

3. **xray_api/main.py add_user()** (`xray_api/main.py` ~294)
   - Accepts **NO request body**
   - Generates UUID: `new_uuid = str(uuid.uuid4())`
   - Adds client: `new_client = {"id": new_uuid}` **only**
   - No `expiryTime`, `totalGB`, `limitIp`, `flow`, `email` in client object

### CHECK results

| Question | Answer |
|----------|--------|
| Is expireTime set inside Xray? | **NO** — client object has only `id` |
| Is expiry passed (seconds/ms)? | **N/A** — not sent |
| Timezone conversion correct? | **N/A** — not sent |
| Are we sending subscription_end to Xray? | **NO** |
| Server-side default TTL? | Unknown — Xray VLESS has no native client expiry |
| Xray override duration internally? | N/A |

---

## PHASE 2 — Xray Configuration

### Client structure (current)

```json
{"id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"}
```

### Missing fields (Xray VLESS inbound clients may support)

- `expiryTime` — Unix timestamp (ms) when client expires
- `totalGB` — data limit
- `limitIp` — connection limit
- `email` — identifier
- `flow` — explicitly NOT used (REALITY incompatible with xtls-rprx-vision)

### Storage

- Clients: `config["inbounds"][vless]["settings"]["clients"]`
- Persisted to file: `XRAY_CONFIG_PATH` (default `/usr/local/etc/xray/config.json`)
- Xray reload: `systemctl restart xray` after each add/remove

### Verification

| Question | Answer |
|----------|--------|
| Does Xray auto-disconnect expired clients? | N/A — no expiry set |
| Cache client list? | Config loaded from file each request |
| Reload after add-user? | Yes — `_restart_xray_async()` |
| Rate/connection limit? | No application-level limit |
| limitIp default? | Not set |
| flow set consistently? | No flow in config or link (REALITY) |

---

## PHASE 3 — Concurrency Audit (CRITICAL)

### Race condition: read-modify-write

**Current flow:**

```
add_user():
  1. config = load_config()           # OUTSIDE lock
  2. modify config (append client)
  3. async with _config_file_lock:    # Lock ONLY around save
       save_config(config)
  4. restart_xray()
```

**Race under load (50 users/min):**

- Request A: load (clients: [])
- Request B: load (clients: [])       # Same initial state
- A: add client_1, save (clients: [1])
- B: add client_2 to B's copy (clients: [2]), save
- **Result: client_1 lost.** User 1 has UUID in DB but not in Xray → connection fails immediately.

**Under high concurrency:** Last writer wins. Clients added by overlapping requests can be overwritten.

### Additional findings

| Question | Answer |
|----------|--------|
| Is /add-user async-safe? | **NO** — load is outside lock |
| Can two requests generate same UUID? | Extremely unlikely (uuid4) |
| UUID generation location? | xray_api (server-side) |
| Can we overwrite client entry? | Yes — overwrite entire config |
| Mutex? | `_config_file_lock` but only around save |
| File-based write? | Yes — `_save_xray_config_file` |

### Partial writes / truncation

- Uses temp file + `shutil.move` for atomic write — **OK**
- No truncation risk

---

## PHASE 4 — Session Instability Root Cause Hypotheses

**Ranked by probability:**

### 1. Concurrency race (HIGH)

Under ~50 users/min, overlapping add-user calls can overwrite each other's config. Users whose client was overwritten would:
- Have valid UUID in DB
- Receive vless_url
- Xray config may not contain their UUID (lost in race) OR
- Xray was restarted multiple times in quick succession → connection flakiness

**Evidence:** Lock does not cover load. Load-modify-save is not atomic.

### 2. Xray restart storm (HIGH)

Each add-user triggers `systemctl restart xray`. At 50 users/min, Xray restarts very frequently. During restart:
- Existing connections drop
- New connections may hit partially reloaded state
- REALITY handshake may be sensitive to restart timing

### 3. REALITY handshake / session behavior (MEDIUM)

REALITY has different session characteristics than plain TLS. "Reconnect fixes it" fits:
- Session/key material invalidated
- Server-side session cache inconsistency after restarts
- Need fresh handshake

### 4. NAT / firewall idle timeout (MEDIUM)

Idle VPN connections often get dropped by NAT (e.g. 5–30 min). Reconnect establishes new NAT binding. This would affect new and old keys similarly — **less likely** since "only new keys" reported.

### 5. expireTime miscalculation (LOW)

**Ruled out** — we do not pass expireTime to Xray. No expiry set.

### 6. flow mismatch (LOW)

**Ruled out** — flow explicitly omitted for REALITY. Consistent.

### 7. Wrong inbound / routing (LOW)

Code uses first VLESS inbound. If multiple inbounds, all users go to same one. Possible but unlikely to affect only new keys.

---

## PHASE 5 — Logging Audit

### Current logging on new issuance

- `grant_access: NEW_ISSUANCE_REQUIRED`
- `grant_access: CALLING_VPN_API`
- `grant_access: ACTIVATION_IMMEDIATE_SUCCESS` (uuid, source, attempt, vless_url_length)
- `grant_access: NEW_ISSUANCE_SUCCESS` (uuid, subscription_end, action)
- `vpn_api add_user: RESPONSE` (status, response_preview)
- `User added successfully: uuid=...` (xray_api)

### Missing logs

- subscription_end
- duration_days
- Xray expireTime (N/A — not set)
- inbound tag
- limitIp
- totalGB
- flow (N/A — not used)

**Recommendation:** Add `subscription_end`, `duration_days`, `inbound_tag` to grant_access success logs for forensics.

---

## PHASE 6 — Immediate Safe Fix Strategy

### Option A: Remove expireTime from Xray entirely

**Status:** Already the case — we do not set expireTime.

**Action:** Control expiry only in backend; reject expired users before key generation. Already done. No change.

### Option B: Always call update-user on renewal

**Status:** Renewal does NOT call VPN API. Xray client has no expiry, so no update needed for expiry.

**Action:** N/A for expiry. Could add update-user for other fields if needed later.

### Option C: Force reload Xray after add-user

**Status:** Already done — `_restart_xray_async()` after each add/remove.

**Action:** Consider batching: collect adds over N seconds, apply once, single restart. Reduces restart storm.

### Option D: Short delay after add-user before returning key

**Action:** Add e.g. 1–2 second sleep after add-user before returning. May reduce races by spacing requests. **Risk:** Increases latency; does not fix root cause.

### Recommended: Fix concurrency (Option E)

**Action:** Move load inside lock so read-modify-save is atomic:

```python
async with _config_file_lock:
    config = await asyncio.to_thread(_load_xray_config_file, XRAY_CONFIG_PATH)
    # ... modify ...
    await asyncio.to_thread(_save_xray_config_file, config, XRAY_CONFIG_PATH)
```

**Risk:** Low. Ensures no client overwrite under concurrent add-user.

**Secondary:** Consider debounced Xray restart (e.g. restart only after last add in a 5s window) to reduce restart frequency under load.

---

## DELIVERABLE SUMMARY

### 1. Root cause hypothesis (ranked)

1. **Concurrency race** — load outside lock, clients lost under load
2. **Xray restart storm** — frequent restarts under 50 users/min
3. **REALITY session sensitivity** — handshake/session behavior after restarts
4. NAT/firewall (lower — would affect old keys too)

### 2. Exact file + function

| Issue | File | Function/Location |
|-------|------|-------------------|
| Concurrency race | `xray_api/main.py` | `add_user()` lines 307–349 — load outside lock |
| subscription_end not passed | `vpn_utils.py` | `add_vless_user()` — no request body |
| subscription_end not passed | `database.py` | `grant_access()` — calls add_vless_user() with no args |

### 3. Issue type

- **Primary:** Concurrency (read-modify-write race)
- **Secondary:** Load-related (restart storm)
- **Not:** expiry mismatch (expiry not used), Xray config structure (minimal config is valid)

### 4. Proposed minimal safe patch

**File:** `xray_api/main.py`  
**Change:** Wrap load + modify + save in `_config_file_lock`:

```python
async with _config_file_lock:
    config = await asyncio.to_thread(_load_xray_config_file, XRAY_CONFIG_PATH)
    # ... all modify logic ...
    await asyncio.to_thread(_save_xray_config_file, config, XRAY_CONFIG_PATH)
```

Single-file change, low risk, fixes race.

### 5. Long-term stable fix

1. **Atomic read-modify-write** — lock covers full load–modify–save (above).
2. **Debounced Xray restart** — batch restarts (e.g. max 1 restart per 10s) to reduce storm under load.
3. **Optional:** Pass expiry to Xray if/when supported — would align Xray with DB; requires Xray API and config schema changes.
4. **Logging:** Add subscription_end, duration_days to grant_access and VPN API success logs for diagnostics.
