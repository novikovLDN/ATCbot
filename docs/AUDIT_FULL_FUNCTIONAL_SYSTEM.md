# ATLAS SECURE — Full Functional System Audit

**Mode:** Read-only. No code modifications.  
**Focus:** Data integrity, subscription safety, UUID safety, financial atomicity, no data loss.  
**Assumptions:** Adversarial production conditions, crash at any line, production scale.

---

## SECTION 1 — SUBSCRIPTION DATA INTEGRITY

### 1.1 Flows that modify subscriptions (traced)

| Flow | UPDATE/INSERT pattern | WHERE clause | Rows affected |
|------|------------------------|-------------|---------------|
| **grant_access (renewal)** | UPDATE subscriptions SET expires_at, uuid, status, ... | **WHERE telegram_id = $3** only | **All rows for that telegram_id** (schema assumes 1 row/user) |
| **grant_access (new issuance)** | INSERT or UPDATE | telegram_id; INSERT has no unique constraint in code | 1 (fetchrow before implies 1) |
| **finalize_purchase** | Via grant_access + pending_purchase → paid | purchase_id, status | 1 |
| **finalize_balance_purchase** | Via grant_access (conn=conn) | Same as grant_access | 1 |
| **auto_renewal** | last_auto_renewal_at then grant_access | telegram_id + conditions; FOR UPDATE SKIP LOCKED on SELECT | 1 per iteration (SKIP LOCKED) |
| **fast_expiry_cleanup** | UPDATE status='expired', uuid=NULL | **WHERE telegram_id = $1 AND uuid = $2 AND status = 'active'** | 0 or 1 (row-specific) |
| **Trial expiration** | database.expire_subscription_by_id (trial_notifications / real-time expiry) | id, telegram_id, uuid, status, expires_at | 0 or 1 |
| **reconcile_xray_state** | **No DB UPDATE** — removes UUID from Xray only | N/A | N/A |
| **activation flow** | UPDATE subscriptions SET uuid, vpn_key, activation_status | **WHERE id = $4 AND activation_status = 'pending'** | 0 or 1 (primary key + guard) |
| **reissue_vpn_key_atomic** | UPDATE subscriptions SET uuid, vpn_key | **WHERE telegram_id = $3** only | **All rows for that user** |

### 1.2 Subscription state transitions verified

- **pending → active:** activation_worker (attempt_activation) and grant_access (new issuance with pre_provisioned_uuid or add_vless_user). Guard: `WHERE id = $4 AND activation_status = 'pending'` (activation); grant_access uses INSERT or UPDATE by telegram_id.
- **active → expired:** fast_expiry_cleanup (WHERE telegram_id AND uuid AND status='active'); database.expire_subscription_by_id (WHERE id, telegram_id, uuid, status, expires_at).
- **trial → expired:** Same expiry paths; fast_expiry skips if get_active_paid_subscription(conn, telegram_id, now) returns a row (trial never overrides paid).
- **Renewal logic:** grant_access renewal path: subscription_end = max(expires_at, now) + duration; UPDATE uses WHERE telegram_id = $3 (no id).

### 1.3 Invariants

| Invariant | Status | Notes |
|-----------|--------|--------|
| status=active ⇒ expires_at > now | **Enforced by logic** | Expiry flows set status='expired' when expires_at &lt; now; renewal extends expires_at. |
| status=expired ⇒ uuid IS NULL | **Enforced** | All expiry UPDATEs set uuid = NULL, vpn_key = NULL. |
| Trial never overrides paid | **Enforced** | fast_expiry calls get_active_paid_subscription (source != 'trial', status='active', expires_at > now); skip if active paid. |
| Renewal never shortens subscription | **Enforced** | subscription_end = max(expires_at, now) + duration; validation raises if subscription_end &lt;= old_expires_at. |
| Renewal extends from max(expires_at, now) | **Enforced** | database.py L3811. |
| No duplicate subscription rows for one user | **Application assumption** | grant_access and reissue use fetchrow(SELECT ... WHERE telegram_id = $1) and UPDATE WHERE telegram_id. If schema allowed multiple rows per user, **renewal and reissue would update all rows** (no WHERE id). |
| No partial activation state left | **Mitigated** | Activation UPDATE is WHERE id AND activation_status='pending'. On failure, orphan UUID is removed (safe_remove_vless_user_with_retry). |

### 1.4 UPDATE query risks

- **grant_access (renewal) L3946–3955:** `WHERE telegram_id = $3` — **if multiple subscription rows per user exist, all are updated.** Same uuid/expires_at written to all. Application uses fetchrow (single row); schema is assumed 1:1 user:subscription.
- **reissue_vpn_key_atomic L3688–3689:** `WHERE telegram_id = $3` — same multi-row risk.
- **fast_expiry L376–380:** WHERE telegram_id AND uuid AND status='active' — **0 rows:** row already cleaned or renewed; **>1 rows:** impossible for same (telegram_id, uuid) if uuid is unique per row.
- **Activation L442–446:** WHERE id = $4 AND activation_status = 'pending' — **0 rows:** idempotent (concurrent activation); **>1:** no (primary key).

### 1.5 Crash simulation (subscription)

| Scenario | Result |
|----------|--------|
| Crash before commit (finalize_purchase / finalize_balance / auto_renewal) | Full rollback; no subscription/balance/referral change. |
| Crash after HTTP (add_vless_user) before DB | Orphan UUID in Xray. Mitigation: uuid_to_cleanup_on_failure + safe_remove_vless_user_with_retry on tx failure (finalize_*, activation, reissue). |
| Crash after DB before notification | Subscription/payment committed; user may not get Telegram message. Notification idempotency (payment_id / mark_notification_sent) prevents double-send on retry. |
| Crash during renewal sync (ensure_user_in_xray after commit) | Subscription extended in DB; Xray may have old expiry. ensure_user_in_xray is best-effort post-commit; logged as CRITICAL on failure. |
| Crash during UUID removal (fast_expiry) | If after HTTP remove, before DB UPDATE: UUID gone from Xray, DB still active+uuid — **inconsistent**. Next run: row still expires_at&lt;now, remove_uuid_if_needed no-op (UUID already removed?), then UPDATE clears DB. So DB can briefly show active with UUID that is no longer in Xray. |
| Crash during reconciliation | Reconcile does not write to subscriptions; only removes UUIDs from Xray. No subscription corruption. |

**Verified no scenario where:**  
- subscription is active but UUID missing (except activation_status='pending' by design).  
- subscription expired but UUID remains (expiry UPDATEs set uuid=NULL).  
- trial removes paid UUID (get_active_paid_subscription guard).  
- paid subscription loses UUID unexpectedly (renewal path keeps uuid; new issuance sets it).  
- renewal generates new UUID (renewal path does not call add_vless_user).  
- renewal loses UUID (renewal UPDATE sets uuid = $4 with existing uuid).

---

## SECTION 2 — UUID LIFECYCLE SAFETY

### 2.1 Creation

- **Initial issuance (payment):** finalize_purchase Phase 1 add_vless_user (no conn held), then grant_access(..., pre_provisioned_uuid=..., _caller_holds_transaction=True). grant_access uses pre_provisioned_uuid, no second add_vless_user.
- **Activation worker:** attempt_activation: Phase 1 fetch, Phase 2 add_vless_user (no conn), Phase 3 advisory lock + UPDATE by id. UUID created in Xray then committed to DB.
- **Renewal:** grant_access renewal path does **not** call add_vless_user; UUID unchanged.
- **Reissue:** reissue_vpn_key_atomic: add_vless_user then UPDATE subscriptions SET uuid, vpn_key WHERE telegram_id; then remove old UUID from Xray.

### 2.2 Removal

- **fast_expiry:** remove_uuid_if_needed (HTTP) then UPDATE subscriptions SET uuid=NULL WHERE telegram_id AND uuid AND status='active'. processing_uuids set prevents double-removal in same loop.
- **Trial expiration:** expire_subscription_by_id: safe_remove_vless_user_with_retry then UPDATE by id, telegram_id, uuid, status, expires_at.
- **Reconcile:** Orphans = xray_uuids - db_uuids; remove_vless_user for each. **No DB update.** Risk: **reconcile can remove a UUID that was just committed** (race: db_uuids snapshot taken before commit of new subscription).
- **Reissue old UUID cleanup:** After commit, remove_vless_user(old_uuid). No DB update for old UUID (already replaced).

### 2.3 Invariants

| Check | Status |
|-------|--------|
| UUID never regenerated on renewal | **TRUE** — renewal path does not call add_vless_user. |
| UUID never removed if active paid exists | **TRUE** — fast_expiry uses get_active_paid_subscription; trial expiry uses similar guard. |
| UUID never duplicated between users | **TRUE** — UUID is generated per subscription; no shared UUID. |
| UUID never removed without DB update | **FALSE in one case:** **reconcile** removes from Xray only; DB is source of truth. If DB says no UUID, removal is correct; if DB just added UUID after our snapshot, we wrongly remove (race). |
| DB never updated (expired) without removal attempt | **TRUE** — fast_expiry and expire_subscription_by_id call remove (or safe_remove) before or with UPDATE. |
| processing_uuids prevents double removal | **TRUE** — fast_expiry adds uuid to set before work, discards in finally; skip if uuid in set. |

### 2.4 Races

- **fast_expiry vs renewal:** fast_expiry re-checks before UPDATE (check_row, expires_at &lt; now_utc). Renewal extends expires_at in same row. Possible interleave: fast_expiry fetches row (expired), renewal commits, fast_expiry does HTTP remove then UPDATE WHERE telegram_id AND uuid AND status='active' — UPDATE can match 0 rows if renewal already set a different state. So no double-remove of UUID; DB consistent.
- **reconcile vs activation:** Reconcile builds db_uuids from subscriptions; activation commits new uuid. If activation commits after reconcile’s fetch, that uuid is not in db_uuids → reconcile treats it as orphan and **removes it from Xray**. **Risk: UUID in DB but removed from Xray (reconcile race).**
- **reissue vs renewal:** reissue holds advisory lock (telegram_id); renewal (grant_access) uses same conn in auto_renewal or separate conn in finalize. Reissue is admin path; no shared lock with renewal. Reissue UPDATE by telegram_id could overwrite a row that renewal just updated (if multiple rows). Single-row assumption makes this low probability.
- **processing_uuids:** In-memory; prevents double process in same batch. Does not protect across restarts or multiple workers (different processes).

---

## SECTION 3 — FINANCIAL INTEGRITY

### 3.1 Traced flows

- **finalize_purchase:** Single conn.transaction(): pending_purchase → paid, INSERT payment, grant_access(conn=conn), payment → approved, _consume_promo, process_referral_reward(conn=conn). increase_balance (balance top-up) with conn=conn.
- **finalize_balance_purchase:** pg_advisory_xact_lock(telegram_id), SELECT balance FOR UPDATE, UPDATE balance, balance_transactions INSERT, grant_access(conn=conn), payment INSERT, process_referral_reward(conn=conn). All in one conn.transaction().
- **auto_renewal:** In one conn.transaction(): decrease_balance(conn=conn), grant_access(conn=conn), payment INSERT, notifications_to_send (no financial mutation). Refund path: increase_balance(conn=conn) on renewal failure.
- **process_referral_reward:** Requires conn. Uses pg_advisory_xact_lock(referrer_id), SELECT balance FOR UPDATE, UPDATE balance, balance_transactions INSERT, referral_rewards INSERT. Duplicate check: SELECT from referral_rewards WHERE buyer_id AND purchase_id; raises on DB/financial errors (rollback).
- **increase_balance / decrease_balance:** When conn provided, use _do_increase/_do_decrease(conn); no nested pool.acquire. Advisory lock (telegram_id) and FOR UPDATE in decrease.

### 3.2 Verification

| Check | Status |
|-------|--------|
| All financial mutations use conn=conn in purchase/renewal/referral paths | **TRUE** — finalize_purchase, finalize_balance_purchase, auto_renewal, process_referral_reward pass conn. |
| No nested pool.acquire inside transaction | **TRUE** — grant_access when conn given does not acquire; increase_balance/decrease_balance when conn given do not acquire. |
| No balance mutation outside transaction | **TRUE** — balance changes only inside caller’s transaction or in their own transaction (standalone call). |
| No referral applied outside purchase transaction | **TRUE** — process_referral_reward called with same conn inside finalize_* / auto_renewal tx. |
| No double referral | **TRUE** — referral_rewards (buyer_id, purchase_id) check; UniqueViolation would raise and rollback. |
| No duplicate purchase processing | **TRUE** — pending_purchase status 'pending' → 'paid' with WHERE purchase_id AND status='pending'; second call gets ValueError. |
| Idempotency by purchase_id | **TRUE** — finalize_purchase raises if status != 'pending'; referral_rewards unique per (buyer_id, purchase_id). |
| No negative balance | **TRUE** — decrease_balance checks current_balance >= amount_kopecks; FOR UPDATE prevents concurrent decrease. |
| No silent partial balance update | **TRUE** — exceptions in _do_increase/_do_decrease propagate or return False; caller in finalize/auto_renewal raises or skips. |

### 3.3 Crash simulation (financial)

- Crash before commit: full rollback; no balance, referral, or subscription change.
- Crash after balance decrease, before subscription update (auto_renewal): transaction rolls back; no partial state.
- Crash after referral INSERT, before commit: rollback; no referral reward.
- Duplicate webhook / duplicate finalize call: status != 'pending' → ValueError; no second grant_access or referral.

**No scenario where:** balance decreases but subscription not extended; referral applied but purchase rolled back; subscription extended without payment; payment marked success but subscription not active (all in one transaction).

---

## SECTION 4 — WORKER LOGIC SAFETY

### 4.1 Checks

| Check | Result |
|-------|--------|
| No infinite loops without await/sleep | **TRUE** — All worker loops contain await asyncio.sleep(...) or await pool.acquire() / cooperative_yield. |
| All pool.acquire released | **TRUE** — async with pool.acquire() or acquire_connection(); no bare acquire without release. |
| No connection held across HTTP | **TRUE** — fast_expiry and activation do HTTP outside conn blocks (post-patch). |
| No await inside threading.Lock | **TRUE** — threading locks used in sync code (rate_limit, circuit_breaker, etc.); no await in those blocks. |
| Advisory locks unlocked | **TRUE** — activation Phase 3 and reissue use try/finally pg_advisory_unlock. |
| No double task creation | **TRUE** — background_tasks list created once; tasks appended once. |
| All workers idempotent on retry | **TRUE** — finalize by purchase_id status; activation by activation_status and id; auto_renewal by last_auto_renewal_at and SKIP LOCKED; fast_expiry re-checks before UPDATE. |

### 4.2 Activation retry safety

- Max attempts enforced; mark_activation_failed updates attempts and optionally status.
- Idempotency: if already active, return existing; UPDATE WHERE id AND activation_status='pending'.

### 4.3 Reconcile safety

- Does not UPDATE subscriptions; only removes UUIDs from Xray. **Risk:** race with activation/payment (stale db_uuids can cause removal of a just-committed UUID).

### 4.4 Trial cleanup safety

- get_active_paid_subscription ensures trial never overrides paid; fast_expiry skips when active paid exists.

---

## SECTION 5 — DATA LOSS SCENARIOS

### 5.1 Potential vectors

1. **Subscription row lost:** No DELETE on subscriptions in traced flows; only UPDATE. **Risk: LOW.**
2. **UUID overwritten:** Renewal explicitly keeps uuid; reissue overwrites by design. **Risk: LOW** (except reissue/renewal multi-row if schema allows).
3. **Referral reward lost:** Only if process_referral_reward raises after referral_rewards INSERT and transaction rolls back — then whole purchase rolls back. **Risk: LOW.**
4. **Payment lost:** Payment INSERT in same transaction as grant_access; rollback loses both. **Risk: LOW.**
5. **activation_status stuck:** mark_activation_failed and attempt_activation update status; max_attempts can mark failed. **Risk: LOW.**
6. **Pending purchase stuck:** If finalize_purchase crashes after UPDATE pending_purchase → paid but before commit, rollback; if after commit, subscription is active and payment approved — retry gets "already processed". **Partial state:** crash after grant_access commit but before payment UPDATE to approved leaves payment status 'pending' while subscription active. **Risk: MEDIUM** — payment record inconsistent; no double-grant on retry.
7. **Notification flag lost:** mark_notification_sent is best-effort after send; if it fails, next check may resend. **Risk: LOW** (duplicate notification, not data loss).
8. **Silent except: pass:** fast_expiry_cleanup L442–443: `except Exception: pass` after _log_vpn_lifecycle_audit_async in VPNRemovalError handler — only skips audit log. activation_service L417–418, 433–434, 487–488: after safe_remove_vless_user_with_retry in orphan prevention — intentional so return path is not failed by cleanup. **Risk: LOW.**

### 5.2 Swallowed exceptions

- increase_balance(conn=conn) on exception returns False (no re-raise); caller in finalize_purchase raises. **OK.**
- decrease_balance(conn=conn) same. **OK.**
- No identified “logging-only error without rollback” in financial or subscription paths; process_referral_reward and DB errors re-raise.

---

## SECTION 6 — NOTIFICATION CONSISTENCY

- **Notification sent only after commit:** finalize_purchase returns after commit; caller sends message and marks notification. auto_renewal: notifications_to_send after transaction commit, then send + mark_notification_sent. **TRUE.**
- **Idempotency:** mark_notification_sent(payment_id) / check_notification_idempotency(payment_id); UPDATE payments SET notification_sent WHERE id AND notification_sent = FALSE. **TRUE.**
- **No notification for rolled back transaction:** Notifications sent only after finalize_* / auto_renewal success (post-commit). **TRUE.**
- **No double notification:** Idempotency check before send in auto_renewal; mark after send. **TRUE.**
- **Activation notification:** Sent after attempt_activation success; subscription_check confirms activation_status and uuid. **TRUE.**

---

## SECTION 7 — CONCURRENCY + RACE CONDITIONS

| Scenario | Result |
|----------|--------|
| Two renewals simultaneously | auto_renewal uses FOR UPDATE SKIP LOCKED and last_auto_renewal_at update; only one worker gets each row. **Safe.** |
| Renewal + fast_expiry | fast_expiry re-checks row before UPDATE; renewal extends expires_at. UPDATE 0 rows if renewed. **Safe.** |
| Activation + reconcile | **Risk:** Reconcile may remove UUID just committed by activation (stale db_uuids). **BLOCKING-level risk for that UUID** (user loses VPN until re-add). |
| Payment + activation | Different resources (payment vs subscription id); activation uses advisory lock per subscription_id. **Safe.** |
| Trial expiration + payment | get_active_paid_subscription and payment path extend subscription; trial expiry skips if paid. **Safe.** |
| Reissue + renewal | Reissue holds advisory lock(telegram_id); renewal in another conn. If same user, reissue UPDATE by telegram_id; renewal also by telegram_id. Single-row assumption: one row updated by each; order may leave one winner. **Acceptable** (admin reissue vs background renewal). |

---

## SECTION 8 — DB POOL + TRANSACTION SAFETY

- **No connection leak:** All acquire via async with or explicit release in finally (e.g. main instance lock).
- **No transaction left open:** Transactions are async with conn.transaction() or explicit commit/rollback on exception.
- **acquire_connection wrapper:** Used in workers; when disabled, delegates to pool.acquire(); when enabled, times wait and releases in __aexit__. **Correct.**
- **Advisory lock always released:** activation Phase 3 and reissue use try/finally pg_advisory_unlock. Main instance lock released in except/finally.
- **Nested transaction:** Not used; single conn.transaction() per flow. **OK.**

---

## SECTION 9 — CRITICAL ASSERTIONS

| Assertion | TRUE/FALSE | File:line / scenario |
|-----------|------------|-----------------------|
| System can lose subscription | **FALSE** | No DELETE; only UPDATE. |
| System can lose UUID | **TRUE** | **reconcile_xray_state:** UUID in DB can be removed from Xray if commit happens after reconcile’s db_uuids fetch. reconcile_xray_state.py L76–99 (fetch), L129 (remove_vless_user). |
| System can lose balance | **FALSE** | Balance changes only in transaction; rollback on failure. |
| System can double-extend subscription | **FALSE** | Renewal extends once per grant_access; idempotency by last_auto_renewal_at and SKIP LOCKED. |
| System can double-apply referral | **FALSE** | referral_rewards (buyer_id, purchase_id) check; UniqueViolation raises. |
| System can send success without DB commit | **FALSE** | Notification sent after finalize_* returns (post-commit). |
| System can leave active subscription without UUID | **TRUE** | **reconcile:** Removes UUID from Xray while DB still has subscription with that UUID (race). Result: status=active, uuid set in DB, but UUID removed from Xray. |
| System can leave UUID without DB subscription | **FALSE** | Orphan prevention: safe_remove_vless_user_with_retry on tx failure; reconcile removes only orphans (in Xray, not in DB). |

---

## SECTION 10 — FINAL VERDICT

### Scores (0–10)

| Area | Score | Notes |
|------|-------|--------|
| **Financial integrity** | **9** | Atomic transactions, conn=conn, referral idempotency, no negative balance. Minor: payment row can stay 'pending' if crash after grant_access, before payment UPDATE. |
| **Subscription safety** | **8** | Invariants enforced; expiry and renewal correct. Risk: grant_access/reissue UPDATE by telegram_id only (multi-row if schema changes). |
| **UUID lifecycle safety** | **7** | Renewal preserves UUID; orphan cleanup on failure. **Deduction: reconcile race can remove just-committed UUID from Xray.** |
| **Worker concurrency** | **8** | No conn across HTTP; advisory locks released; idempotent retries. Reconcile vs activation race. |

### Data loss risk level

**MEDIUM** — Primary residual risk: **reconcile removes a UUID that was just committed** (activation or payment), leaving DB with active subscription but Xray without that UUID (user loses VPN until manual re-add or next ensure_user_in_xray). No financial or subscription row loss; no double charge or double referral.

### Production readiness verdict

**CONDITIONALLY READY.**  

- **Financial and subscription logic:** Production-ready; no blocking issues.  
- **BLOCKING risk:** Reconcile vs activation/payment race (UUID in DB removed from Xray). Mitigation options: (1) disable reconciliation in high-activity windows, (2) add a short delay or “grace period” after subscription commit before considering UUID for orphan list, or (3) reconcile only UUIDs that have been in DB for longer than N seconds.  

**Recommendation:** Treat reconciliation as best-effort orphan cleanup; accept that in rare race a just-committed UUID may be removed and document recovery (e.g. ensure_user_in_xray on next renewal or manual reissue). If zero tolerance for that race, implement one of the mitigations above before production.

---

*End of audit. No code was modified.*
