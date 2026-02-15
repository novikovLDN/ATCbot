# FULL PRODUCTION AUDIT — VPN SaaS System

**Audit Date:** 2025  
**Scope:** Bot, API, config, security, infrastructure assumptions  
**Domain:** vpn.mynewllcw.com, api.mynewllcw.com  
**Xray Port (stated):** 4443

---

## 1. Architecture Status: **UNSAFE**

Critical issues in API server prevent production readiness. Bot architecture is correct; API has configuration and Xray client structure violations.

---

## 2. Bot Isolation: **PASS**

| Check | Result |
|-------|--------|
| `vless://` in bot code | ❌ None (only in docs) |
| `f"vless://"` in bot code | ❌ None |
| `generate_vless_url` / `generate_vless_link` | ❌ None in bot |
| XRAY_SERVER_IP, PORT, SNI, PUBLIC_KEY, SHORT_ID, FP | ❌ Removed from config.py |
| flow=, xtls-rprx-vision in bot | ❌ None |
| myvpncloud references | ❌ None |
| add_vless_user requires vless_link | ✅ required_fields schema, raises InvalidResponseError |
| No fallback generation | ✅ Confirmed |
| config.py | ✅ Only XRAY_API_URL, XRAY_API_KEY |

**Verdict:** Bot does not construct VLESS links. API-only architecture is respected in bot code.

---

## 3. API Integrity: **FAIL**

### 3.1 VLESS Link Generation (xray_api/main.py)

| Item | Expected | Actual | Status |
|------|----------|--------|--------|
| Domain in link | vpn.mynewllcw.com | From XRAY_SERVER_IP env (default vpn.mynewllcw.com) | ⚠️ Env-dependent |
| Port | 4443 | XRAY_PORT default **443** | **🚨 CRITICAL** |
| SNI | www.microsoft.com | XRAY_SNI default **vpn.mynewllcw.com** | **🚨 CRITICAL** |
| flow in link | xtls-rprx-vision | ✅ Present in params | PASS |
| encryption | none | ✅ | PASS |
| security | reality | ✅ | PASS |
| Fragment | #AtlasSecure | ✅ | PASS |

### 3.2 Xray Client Config — add-user

**Audit requirement:** Clients must include `"flow": "xtls-rprx-vision"`.

**Actual code (lines 409–414):**
```python
new_client = {
    "id": client_uuid,
    "email": f"user_{request.telegram_id}",
    "expiryTime": request.expiry_timestamp_ms
}
# NO "flow" field
```

**Verdict:** **🚨 CRITICAL — Missing `flow` in Xray config clients.** Xray VLESS REALITY XTLS Vision requires `flow` in client objects. Without it, traffic may connect but not pass.

### 3.3 Xray Client Config — update-user fallback add

**Lines 508–511:** Fallback add also omits `flow`:
```python
vless_inbound["settings"]["clients"].append({
    "id": target_uuid,
    "email": f"user_recovered_{target_uuid[:8]}",
    "expiryTime": request.expiry_timestamp_ms
})
# NO "flow" field
```

**Verdict:** **🚨 CRITICAL** — Same issue as add-user.

### 3.4 xray_api/.env.example

| Variable | Value | Issue |
|----------|-------|-------|
| XRAY_SERVER_IP | 172.86.67.9 | IP instead of domain vpn.mynewllcw.com |
| XRAY_PORT | 443 | Wrong if Xray listens on 4443 |
| XRAY_SNI | www.cloudflare.com | Must match Xray realitySettings.serverNames |
| XRAY_PUBLIC_KEY | fDixPEeh... | Must match Xray server key |
| XRAY_SHORT_ID | a1b2c3d4 | Must match Xray config |

**Verdict:** Example env conflicts with stated production (domain, port 4443, SNI www.microsoft.com).

### 3.5 Positive API Findings

- `_config_file_lock` prevents concurrent config write races
- `asyncio.to_thread` for file I/O
- UUID contract: request UUID mirrored in response
- `generate_vless_link` includes `flow=xtls-rprx-vision` in link
- systemctl restart with 10s timeout and retry

---

## 4. Xray Consistency: **FAIL** (Cannot fully verify)

| Item | Status |
|------|--------|
| config.json in repo | ❌ Not present — cannot inspect |
| Port 4443 | ⚠️ API default 443 — mismatch unless env overridden |
| flow in clients | ❌ API does not add flow to clients |
| dest / serverNames | ❌ Unknown — depends on server config |

**Required fixes:**
1. Add `"flow": "xtls-rprx-vision"` to every client in add-user and update-user fallback.
2. Set XRAY_PORT=4443 (or match Xray inbound) in API env.
3. Align XRAY_SNI with Xray realitySettings.serverNames (e.g. www.microsoft.com if used there).
4. Add a reference config.json or runbook so Xray config can be audited.

---

## 5. Cloudflare Correctness: **UNKNOWN**

Cloudflare settings cannot be audited from code. Assumptions:

- SSL: Full (strict)
- api.mynewllcw.com → Origin
- vpn.mynewllcw.com → A record to server

**Recommendation:** Verify DNS, SSL mode, and certificate SAN via Cloudflare dashboard.

---

## 6. Railway Isolation: **PASS**

**Bot .env.example:**
- ✅ PROD_XRAY_API_URL, PROD_XRAY_API_KEY
- ✅ No XRAY_SERVER_IP, PORT, SNI, PUBLIC_KEY, SHORT_ID, FP
- ✅ APP_ENV=prod
- ✅ PROD_VPN_PROVISIONING_ENABLED not listed (default true when VPN enabled)

**Recommendation:** Confirm PROD_VPN_PROVISIONING_ENABLED=true in Railway if provisioning must be enabled.

---

## 7. Critical Issues Found

1. **API add-user: clients missing `flow`** — New clients in Xray config lack `"flow": "xtls-rprx-vision"`. Required for XTLS Vision; otherwise traffic may not work.
2. **API update-user fallback: clients missing `flow`** — Same as above for recovered clients.
3. **API default port 443** — Xray runs on 4443; default XRAY_PORT=443 will generate wrong port in VLESS links unless overridden.
4. **API default SNI** — Default vpn.mynewllcw.com may not match Xray realitySettings.serverNames (e.g. www.microsoft.com).
5. **xray_api .env.example** — Uses IP, port 443, and SNI www.cloudflare.com instead of production values.

---

## 8. Medium Issues

1. **PRODUCTION_VALIDATION_CHECKLIST** — Expects port 443 in link; should be 4443 if that is production.
2. **API key comparison** — Uses `!=`; consider constant-time comparison for defense in depth.
3. **main.py hasattr(config, "XRAY_SERVER_IP")** — Always False after config refactor; check is harmless but ineffective.
4. **docs/NEW_KEY_ISSUANCE_AUDIT.md** — States "flow NOT used (REALITY incompatible)" — contradicts current design; should be updated.

---

## 9. Improvement Recommendations

1. **Immediate:** Add `"flow": "xtls-rprx-vision"` to all client objects in xray_api add-user and update-user.
2. **Immediate:** Update xray_api .env.example to port 4443, domain vpn.mynewllcw.com, and correct SNI.
3. **Immediate:** Document required Xray config (inbound port, flow, realitySettings) and add a validation script or reference config.
4. **Short-term:** Validate API response link format (port, SNI, flow) in integration tests.
5. **Short-term:** Add startup check in API that XRAY_PORT and XRAY_SNI match expected production values.
6. **Security:** Use `secrets.compare_digest` for API key comparison.
7. **Operational:** Add health check that verifies Xray config contains flow for all VLESS clients.

---

## 10. UUID Handling

| Check | Result |
|-------|--------|
| DB stores API UUID | ✅ new_uuid = uuid_from_api |
| No UUID prefixing | ✅ _validate_uuid_no_prefix rejects stage-/prod-/test- |
| No UUID mutation | ✅ str(uuid).strip() only |
| API mirrors request UUID | ✅ client_uuid = uuid_from_request |

---

## 11. Security Audit Summary

| Item | Status |
|------|--------|
| API key in logs | ❌ Not logged |
| BOT_TOKEN in logs | ❌ Only sha256 hash prefix |
| Full UUID in logs | ❌ Truncated to 8 chars |
| Config write races | ✅ _config_file_lock |
| systemctl failure | ✅ Handled with retry |
| JSON validation | ✅ Pydantic + manual checks |
| Header spoofing | ⚠️ X-API-Key only; no HMAC on body |

---

## 12. Final Production Readiness Score: **58/100**

**Breakdown:**
- Bot architecture: 25/25 ✅
- API link generation: 10/20 (flow in link ✅, port/SNI defaults ❌)
- API Xray client config: 0/15 (no flow in clients) ❌
- Config/env hygiene: 8/15 (bot ✅, API example ❌)
- Security: 10/15 ✅
- Documentation/config validation: 5/10 ⚠️

**Blocker:** Add `flow` to Xray client config in API and align port/SNI with production before going live.
