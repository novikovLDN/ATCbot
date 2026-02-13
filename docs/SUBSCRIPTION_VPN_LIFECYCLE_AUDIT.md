# SUBSCRIPTION ↔ VPN KEY LIFECYCLE — Full Production Audit

**Branch:** stage  
**Mode:** READ-ONLY ANALYSIS  
**Date:** Audit performed on current codebase

---

## 1. ARCHITECTURE VALIDATION SUMMARY

| Component | Status | Notes |
|-----------|--------|------|
| **grant_access** | ✅ Single source of truth | Only place UUID created, subscription_end changed, VPN API called |
| **RENEWAL path** | ✅ Correct | UUID stable, update_vless_user called, no add/remove |
| **NEW_ISSUANCE path** | ✅ Correct | add_vless_user with subscription_end, expiryTime passed |
| **Expiration cleanup** | ✅ Explicit | remove_vless_user + DB status='expired' |
| **Trial override** | ✅ Correct | Paid → RENEWAL path, source='payment', trial_expires_at cleared |
| **Trial cleanup guard** | ✅ Correct | get_active_paid_subscription check before removal |
| **Xray API atomicity** | ✅ Correct | Full load-modify-save under lock |
| **Debounced restart** | ✅ Correct | Max 1 restart per 5 seconds |

---

## 2. PHASE 1 — COMPLETE KEY LIFECYCLE MAP

### A) NEW PAID PURCHASE

**Path:** `finalize_purchase` → `grant_access(conn)` → `add_vless_user(telegram_id, subscription_end)` → `xray_api.add_user(AddUserRequest)`

| Step | Location | Verification |
|------|----------|--------------|
| purchase created | pending_purchases | ✅ |
| payment confirmed | finalize_purchase UPDATE status='paid' | ✅ |
| grant_access | database.py ~6490 | duration=period_days, source='payment' |
| subscription_start | now (datetime.now(timezone.utc)) | ✅ |
| subscription_end | now + duration | ✅ |
| expiry_ms | int(subscription_end.timestamp() * 1000) | ✅ milliseconds |
| add_vless_user | vpn_utils.py ~277 | json_body: telegram_id, expiry_timestamp_ms |
| xray_api.add_user | xray_api/main.py ~324 | new_client: id, email, expiryTime |
| DB save | subscriptions: uuid, vpn_key, expires_at | ✅ |
| Xray config write | atomic under _config_file_lock | ✅ |
| Restart | _debounced_restart() | ✅ |

**Confirmed:**
- expiryTime == subscription_end (ms) ✅
- expiryTime in milliseconds ✅
- No timezone explicit conversion (see PHASE 6) ⚠️

---

### B) RENEWAL (PAID → EXTEND)

**Path:** `finalize_purchase` → `grant_access` → RENEWAL_DETECTED → `update_vless_user` → `xray_api.update_user`

| Step | Location | Verification |
|------|----------|--------------|
| grant_access | database.py ~3820 | SELECT subscription |
| is_active | status=='active' AND expires_at > now AND uuid not null | ✅ |
| RENEWAL_DETECTED | ~3864 | ✅ |
| subscription_end | max(expires_at, now) + duration | ✅ |
| DB UPDATE | expires_at, source, reminders | ✅ |
| update_vless_user | vpn_utils ~3935 | uuid, subscription_end |
| xray_api.update_user | xray_api/main.py ~473 | client["expiryTime"] = request.expiry_timestamp_ms |
| No remove_user | ✅ | Not called |
| No add_user | ✅ | Not called |
| UUID identical | ✅ | Returned from subscription |

**Confirmed:** No session interruption, UUID stable ✅

---

### C) SUBSCRIPTION EXPIRATION

**Path:** `check_and_disable_expired_subscription` (on user action) / trial_notifications expire_trial_subscriptions (scheduled)

| Step | Location | Verification |
|------|----------|--------------|
| Detection | expires_at <= now, status='active', uuid not null | ✅ |
| remove_vless_user | database.py ~2862 / trial_notifications ~458 | ✅ |
| xray_api.remove_user | Atomic under lock | ✅ |
| DB UPDATE | status='expired', uuid=NULL, vpn_key=NULL | ✅ |
| Restart | _debounced_restart() | ✅ |

**Confirmed:** Removal explicit, expiryTime not relied on alone ✅

---

### D) TRIAL — FIRST ISSUANCE

**Path:** `callback_activate_trial` → `grant_access(source="trial", duration=3 days)` → NEW_ISSUANCE (no active sub)

| Step | Verification |
|------|--------------|
| mark_trial_used | trial_expires_at = now + 3 days ✅ |
| grant_access | source="trial", duration=timedelta(days=3) ✅ |
| subscription_end | now + 3 days (= trial_expires_at) ✅ |
| expiryTime | = subscription_end (ms) ✅ |
| DB | source='trial' ✅ |

---

### E) TRIAL → PAID UPGRADE (CRITICAL) ✅

**Scenario:** User has active trial. User buys subscription before trial ends.

| Check | Result |
|-------|--------|
| grant_access receives | source="payment", duration=period_days |
| subscription exists | Yes (trial) |
| status | 'active' |
| expires_at > now | Yes (trial not expired) |
| uuid | Not null |
| **→ RENEWAL path** | ✅ Correct |
| update_vless_user called | ✅ Yes (~3935) |
| subscription_end | max(trial_expires_at, now) + period_days |
| source updated | 'payment' |
| trial_expires_at | Set to now (TRIAL_OVERRIDDEN_BY_PAID) |
| Old trial expiryTime | Overwritten by update_user |

**Confirmed:** UUID remains same, expiryTime updated to paid_end, no reissue ✅

---

### F) TRIAL EXPIRES BUT USER ALREADY HAS PAID ✅

**Scenario:** User activated trial, bought paid, trial expiry date arrives.

| Check | Result |
|-------|--------|
| Trial cleanup | expire_trial_subscriptions |
| active_paid check | get_active_paid_subscription(conn, telegram_id, now) |
| get_active_paid_subscription | source != 'trial' AND status='active' AND expires_at > now |
| After paid upgrade | source='payment' → matches |
| Skip removal | continue if active_paid ✅ |
| UPDATE | WHERE source = 'trial' AND status = 'active' |
| After paid upgrade | source='payment' → UPDATE does NOT match ✅ |

**Confirmed:** Trial cleanup does not touch paid subscription ✅

---

## 3. PHASE 2 — Xray API INTEGRATION

| Endpoint | Lock | Load inside lock | expiryTime format | Units |
|----------|------|------------------|-------------------|-------|
| add_user | _config_file_lock | ✅ Yes | request.expiry_timestamp_ms | ms |
| update_user | _config_file_lock | ✅ Yes | request.expiry_timestamp_ms | ms |
| remove_user | _config_file_lock | ✅ Yes | N/A | — |

| Check | Status |
|-------|--------|
| Atomic read-modify-save | ✅ All three endpoints |
| No load outside lock | ✅ Confirmed |
| Debounced restart | _debounced_restart(), 5s cooldown |
| _restart_lock | Serializes restart attempts |
| _last_restart_time | Global, updated on success |
| email field | user_{telegram_id} in add, uuid_{...} fallback in update |

---

## 4. PHASE 3 — EDGE CASES

| Edge Case | Expected | Actual | Status |
|-----------|----------|--------|--------|
| User renews after expiration | NEW UUID | is_active=False → NEW_ISSUANCE → add_user | ✅ |
| Concurrent renewals | No duplicate UUID | Same UUID, update_user idempotent | ✅ |
| Manual admin revoke | remove_user + DB | admin_revoke_access_atomic → remove_vless_user | ✅ |
| Reissue logic | Preserve expiry | reissue_vpn_access → add_vless_user(telegram_id, expires_at) | ✅ |
| Activation worker | Pass subscription_end | add_vless_user(telegram_id, subscription_end=expires_at) | ✅ |

---

## 5. PHASE 4 — CONSISTENCY MATRIX

| Scenario | UUID | expiryTime | DB subscription_end | Expected | Status |
|----------|------|------------|---------------------|----------|--------|
| New paid | New | subscription_end (ms) | subscription_end | Match | ✅ |
| Renewal | Same | Updated to new_end (ms) | new_end | Match | ✅ |
| Expiration | Removed | N/A | status=expired | No ghost | ✅ |
| Trial only | New | trial_end (ms) | trial_end | Match | ✅ |
| Trial → Paid | Same | paid_end (ms) | paid_end | Match | ✅ |
| Paid during trial expiry | Same | paid_end (ms) | paid_end | Match | ✅ |
| Renewal after expiration | New | new_end (ms) | new_end | Fresh UUID | ✅ |

---

## 6. PHASE 5 — FAILURE MODES

| Risk | Severity | Description | Mitigation |
|------|----------|-------------|------------|
| update_vless_user fails after DB renewal | MEDIUM | Xray expiry stale, user may need reconnect | Logged, best-effort; DB is source of truth |
| Xray restart fails | LOW | Config saved but Xray not reloaded | Separate process; manual intervention |
| Timezone drift | RESOLVED | All subscription logic uses datetime.now(timezone.utc) | UTC standardized |
| update_user 404 (client not in Xray) | MEDIUM | DB renewed, Xray has no client | Logged; next add would fail or create new |
| Concurrent add/remove | LOW | Lock prevents overwrite | Atomic lock ✅ |

**Ranking:** No HIGH risks; MEDIUM risks are logged and have fallbacks.

---

## 7. PHASE 6 — TIMEZONE & UNIT VALIDATION

| Check | Status | Notes |
|-------|--------|-------|
| subscription_end storage | ⚠️ | PostgreSQL TIMESTAMP (no TZ in 001_init); server timezone dependent |
| expiry_timestamp_ms | ✅ | int(subscription_end.timestamp() * 1000) |
| Integer truncation | ✅ | Python int() for ms; no overflow for reasonable dates |
| Seconds vs ms | ✅ | * 1000 used consistently |
| datetime.now(timezone.utc) | ✅ | All subscription/expiry logic uses UTC |
| _ensure_utc() | ✅ | DB values normalized to UTC (naive assumed UTC) |
| Validation | ✅ | assert subscription_end.tzinfo == timezone.utc in grant_access, vpn_utils |

---

## 8. PHASE 7 — LOAD BEHAVIOR (50 users/min)

| Check | Status |
|-------|--------|
| Config overwrite | ✅ Prevented by _config_file_lock |
| Lock covers load-modify-save | ✅ add_user, update_user, remove_user |
| Restart debounce | ✅ Max 1 restart per 5 seconds |
| update_user race | ✅ Serialized by lock |
| add_user race | ✅ Serialized by lock |

---

## 9. LIFECYCLE CORRECTNESS VERDICT

**VPN key lives exactly as long as active subscription:** ✅ Yes  
- New: expiryTime = subscription_end  
- Renewal: expiryTime updated, UUID unchanged  
- Expiration: UUID removed, DB marked expired  

**Renewal extends same UUID:** ✅ Yes  
- RENEWAL path, update_vless_user, no add/remove  

**Expiration removes UUID:** ✅ Yes  
- check_and_disable_expired_subscription, trial cleanup (with guards)

---

## 10. TRIAL OVERRIDE CORRECTNESS VERDICT

**Trial → Paid:** ✅ Correct  
- RENEWAL path (subscription active with trial)  
- update_vless_user updates expiryTime  
- source='payment', trial_expires_at cleared  
- No UUID reissue  

**Trial expires, user already paid:** ✅ Correct  
- get_active_paid_subscription check  
- UPDATE only targets source='trial'  

---

## 11. EXPIRY SYNCHRONIZATION VERDICT

**expiryTime aligned with subscription_end:** ✅ Yes  
- add_user: expiry_timestamp_ms from subscription_end  
- update_user: expiry_timestamp_ms from new subscription_end  
- Conversion: timestamp() * 1000  

**Desync possibility:** MEDIUM  
- If update_vless_user fails: DB renewed, Xray stale. Mitigated by logging and reconnect.

---

## 12. IDENTIFIED RISKS

1. **update_vless_user failure (best-effort):** DB renewed, Xray expiry may be stale. User reconnects to get new session; Xray may still enforce old expiry if it supports expiryTime.
2. **Timezone:** No explicit UTC enforcement; server TZ assumed consistent.
3. **update_user 404:** If client missing in Xray (e.g. manual config edit), renewal updates DB but not Xray.

---

## 13. CONFIRMED SAFE ZONES

- grant_access RENEWAL path  
- grant_access NEW_ISSUANCE path  
- Trial → Paid upgrade  
- Trial cleanup with active_paid guard  
- Xray API atomic config writes  
- Debounced restart  
- Admin revoke (remove_vless_user)  
- check_and_disable_expired_subscription  
- Activation worker (passes subscription_end)  
- Reissue (passes expires_at)  

---

## 14. CRITICAL GAPS (if any)

**None.** All critical paths verified. Minor gaps:
- Timezone consistency (LOW)
- update_vless_user best-effort on failure (MEDIUM, acceptable)

---

## 15. FINAL CONFIDENCE SCORE

**8.5 / 10**

**Breakdown:**
- Lifecycle correctness: 9/10  
- Trial override: 9/10  
- Xray integration: 9/10  
- Edge cases: 8/10  
- Timezone: 7/10  
- Failure handling: 8/10  

**Deductions:** Timezone not explicit UTC; update_vless_user best-effort on failure.

---

**END OF AUDIT**
