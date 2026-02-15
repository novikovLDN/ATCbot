# Full Production Safety Audit

**Date:** 2025-02-15  
**Scope:** Purchase flows, auto-renewal, notifications, referral rewards, UUID lifecycle, workers, idempotency, crash consistency, atomicity, pagination, connection pool, polling restart.  
**Verdict:** See Section 14.

---

## 1) Executive Summary

The system centralizes financial and subscription logic in **single-source-of-truth** paths: `finalize_purchase`, `grant_access`, `finalize_balance_purchase`, `finalize_balance_topup`, and `process_referral_reward`. All payment and balance mutations use **one connection and one transaction** when called from these paths; referral and promo are **inside** the same transaction. **Idempotency** is enforced at the boundaries (pending_purchase status, provider_charge_id, referral_rewards by purchase_id, notification_sent flag). **Payment status does not regress** from approved to pending.

**Critical issues:** (1) **Auto-renewal** performs **Telegram send and nested pool acquisition** (get_last_approved_payment, is_vip_user, get_user_discount) **inside an open DB transaction**, risking long-held locks and pool pressure. (2) **Activation worker** holds a DB connection across **asyncio.sleep(0.5)** between items.

**High issues:** Reminders worker uses **unbounded fetch** (no LIMIT/pagination). **finalize_purchase** Phase 1 (add_vless_user) runs **before** the transaction; Phase 2 failure triggers orphan UUID cleanup; post-commit VPN sync is non-atomic with DB (acceptable by design).

**Verified safe:** finalize_purchase/finalize_balance_purchase/finalize_balance_topup atomicity; referral idempotency; promo consumption in transaction; balance advisory locks; FOR UPDATE SKIP LOCKED in auto_renewal; payment status monotonicity; UUID stability on renewal; fast_expiry keyset pagination and paid-subscription guard; reconcile only removes orphans; polling watchdog does not create double finalize or duplicate update processing.

---

## 2) Critical Issues

| # | Issue | Location | Evidence |
|---|--------|----------|----------|
| C1 | **Telegram send and nested connections inside auto_renewal transaction** | `auto_renewal.py` ~104–388 | `process_auto_renewals` holds `conn` in `async with conn.transaction()`. Inside the loop it calls: `database.get_last_approved_payment(telegram_id)` (acquires its own pool connection, `database.py:2832`), `database.is_vip_user(telegram_id)`, `database.get_user_discount(telegram_id)` (both acquire pool if no conn), then `safe_send_message(bot, telegram_id, text, ...)` (Telegram API). Sending Telegram messages while holding a transaction keeps row locks and one pool connection for the duration of the send. Nested acquires increase pool usage. **Impact:** Long transactions, pool exhaustion under load, possible deadlocks. **Fix:** Move tariff/price resolution and notification send **after** commit; use only `conn` inside the transaction for decrease_balance, grant_access, payment insert, and idempotency check/mark. |
| C2 | **Connection held during asyncio.sleep in activation worker** | `activation_worker.py:386` | After processing each pending activation, `await asyncio.sleep(0.5)` runs inside `async with pool.acquire() as conn:`. Connection is held for (N × 0.5s) plus processing. **Impact:** Pool starvation when many pending activations. **Fix:** Release conn before sleep, or process in batches and sleep between batches without holding conn. |

---

## 3) High Risk Issues

| # | Issue | Location | Evidence |
|---|--------|----------|----------|
| H1 | **Unbounded reminder fetch** | `database.py:5453` `get_subscriptions_for_reminders()` | Fetches all rows matching `expires_at > now` with `ORDER BY s.expires_at ASC` and **no LIMIT**. **Impact:** Memory and long-held connection at scale. **Fix:** Keyset pagination (e.g. `id > last_id ORDER BY id LIMIT N`) or LIMIT with explicit max. |
| H2 | **finalize_purchase Phase 1 outside transaction** | `database.py:6517–6574` | `add_vless_user` is called **before** `async with conn.transaction()`. If Phase 2 (DB) fails, orphan UUID is removed in `except` via `safe_remove_vless_user_with_retry`. If that removal fails, orphan remains in Xray. **Impact:** Rare orphan UUID in Xray until next reconcile or manual fix. Documented and mitigated by cleanup. |
| H3 | **Post-commit VPN sync not atomic with DB** | `database.py:6856–6868` | After commit, `ensure_user_in_xray` for renewal and `safe_remove_vless_user_with_retry` for old_uuid run outside the transaction. **Impact:** DB says “renewed” but Xray might not be updated if process dies; old UUID might remain in Xray. Acceptable; reconciliation and retries can correct. |

---

## 4) Medium Risk Issues

| # | Issue | Location | Evidence |
|---|--------|----------|----------|
| M1 | **get_last_approved_payment / is_vip_user / get_user_discount without conn** | `auto_renewal.py:178, 214, 218` | When called from auto_renewal they use default `conn=None` and thus each acquires a new connection. Already covered under C1; fixing C1 addresses this. |
| M2 | **Reminder idempotency window in-memory** | `reminders.py:82–92` | Uses `last_reminder_at` and a 30-minute window to skip duplicate sends. DB-backed reminder flags also updated after send. Restart within the window could theoretically allow a duplicate; low likelihood. |
| M3 | **processing_uuids in fast_expiry is in-memory** | `fast_expiry_cleanup.py:96, 311, 458` | Prevents double-removal within one run. After restart, the same UUID could be seen again; DB update is guarded by re-check (status/expires_at) before UPDATE, so at most one successful removal. |

---

## 5) Verified Safe Areas

- **finalize_purchase (external/card/crypto):** Single conn, single transaction. Steps: pending_purchase → paid, payment insert, grant_access(conn=conn), payment → approved, _consume_promo_in_transaction(conn), process_referral_reward(conn=conn). No mutation after commit; only VPN cleanup/sync after commit.
- **finalize_balance_topup:** Single transaction; idempotency by provider_charge_id; balance + payment + balance_transactions + process_referral_reward(conn) in one tx.
- **finalize_balance_purchase:** Single transaction; advisory lock; decrease balance, balance_transactions, _consume_promo (if any), grant_access(conn), payment insert, process_referral_reward(conn).
- **increase_balance / decrease_balance:** Accept optional `conn`; use pg_advisory_xact_lock(telegram_id); FOR UPDATE in decrease_balance; when conn provided, no extra acquire.
- **process_referral_reward:** Takes `conn` only; duplicate check by (buyer_id, purchase_id); financial errors re-raised to rollback; no silent swallow.
- **_consume_promo_in_transaction:** Uses conn only; UPDATE ... RETURNING; raises on exhausted/invalid.
- **grant_access:** When conn provided and _caller_holds_transaction=True, no internal acquire; renewal path does not call VPN API; UUID stable on renewal.
- **Payment status:** No code path sets payments.status from 'approved' to 'pending'. Status is monotonic.
- **Auto-renewal:** FOR UPDATE SKIP LOCKED; last_auto_renewal_at set at start of processing; idempotency check for notification; refund path on UUID regeneration error uses same conn.
- **fast_expiry:** Keyset pagination (id > last_seen_id, ORDER BY id ASC, LIMIT); last_seen_id updated only when rows exist; get_active_paid_subscription(conn) used; DB update only after successful VPN remove; re-check before UPDATE.
- **reconcile_xray_state:** Compares DB vs Xray; removes only orphans (in Xray, not in DB); batch limit; does not delete from DB.
- **Activation worker:** get_pending_subscriptions(conn), attempt_activation(conn), mark_* (conn); notification sent after activation; idempotency check before send.
- **Crypto watcher:** Calls finalize_purchase (idempotent); no double credit.
- **Notification idempotency:** mark_payment_notification_sent uses UPDATE ... WHERE notification_sent = FALSE; check before send; mark after send when conn provided (e.g. auto_renewal).

---

## 6) Financial Atomicity Matrix

| Operation | Single conn? | Single tx? | Mutations after commit? | Nested pool acquire in tx? |
|-----------|--------------|------------|--------------------------|----------------------------|
| finalize_purchase (subscription) | Yes | Yes | No (only VPN sync/cleanup) | No |
| finalize_purchase (balance topup) | Yes | Yes | No | No |
| finalize_balance_purchase | Yes | Yes | No (only VPN cleanup/sync) | No |
| finalize_balance_topup | Yes | Yes | No | No |
| process_referral_reward | Uses caller conn | Same tx as caller | No | No |
| _consume_promo_in_transaction | Uses caller conn | Same tx as caller | No | No |
| decrease_balance (standalone) | Yes (own acquire) | Yes | No | N/A |
| increase_balance (standalone) | Yes (own acquire) | Yes | No | N/A |
| auto_renewal (decrease + grant + payment) | Yes | Yes | No | **Yes** (get_last_approved_payment, is_vip_user, get_user_discount) |

---

## 7) UUID Lifecycle Matrix

| Event | Who | DB change | Xray change | UUID stable? |
|-------|-----|-----------|-------------|--------------|
| New paid subscription | finalize_purchase / finalize_balance_purchase | INSERT/UPDATE sub, uuid set | add_user (Phase 1 or in grant) | N/A (new) |
| Renewal (payment/auto_renew) | grant_access | UPDATE expires_at, uuid unchanged | ensure_user_in_xray after commit (renewal) | Yes |
| Trial expiry | trial_notifications / fast_expiry | Only if no active paid (get_active_paid_subscription) | remove if trial and expired | N/A |
| Paid subscription expiry | fast_expiry | status=expired, uuid=NULL after VPN remove | remove-user | N/A |
| Orphan in Xray | reconcile_xray_state | None | remove | N/A |
| Pending activation | activation_worker | activation_status=active, uuid set | add/update via attempt_activation | N/A |

- **Paid UUID never removed by trial:** fast_expiry uses `get_active_paid_subscription(conn, telegram_id, now_utc)` and skips when paid exists.
- **Paid UUID never removed by fast_expiry:** Only rows with `expires_at < now` and no active paid are processed.
- **reconcile removes only orphans:** orphans = xray_uuids - db_uuids; only those are removed from Xray.
- **UUID stable on renewal:** grant_access renewal path does not call add_vless_user; only extends expires_at.

---

## 8) Worker Safety Table

| Worker | Keyset / LIMIT | ORDER BY | last_id only when rows | MAX_ITERATION_SECONDS | OFFSET | Unbounded? | Blocking I/O in tx? | Long tx over network? | Conn leak risk |
|--------|----------------|----------|-------------------------|------------------------|--------|------------|----------------------|------------------------|----------------|
| auto_renewal | LIMIT $3 (BATCH) | id ASC | N/A (single batch) | Yes (15s) | No | No | **Yes (send + nested acquire)** | **Yes** | No |
| fast_expiry_cleanup | id > last_seen_id, LIMIT | id ASC | Yes (if rows) | Yes (15s) | No | No | No | No (VPN after tx) | No |
| activation_worker | limit=50 (service) | — | N/A | Yes (15s) | No | No | No | No | **Sleep with conn** |
| reconcile_xray_state | id > last_seen_id, LIMIT | id ASC | Yes | N/A (single batch) | No | No | No | No | No |
| crypto_payment_watcher | ORDER BY created_at DESC, no LIMIT in query | — | N/A | 15s | No | **Unbounded fetch** | No | No (finalize_purchase has its own tx) | No |
| reminders | get_subscriptions_for_reminders | expires_at ASC | N/A | No cap | No | **Yes (no LIMIT)** | No | No | No |
| trial_notifications | BATCH_SIZE 100, keyset in service | — | Yes | — | No | No | No | No | No |

---

## 9) Crash Consistency Matrix

**finalize_purchase (one transaction):**

| Crash point | Outcome |
|-------------|---------|
| Before transaction | No DB change; possible Phase 1 UUID in Xray → cleaned up on next finalize_purchase failure path or reconcile. |
| Mid transaction | Full rollback; Phase 1 UUID orphan → cleanup in except (or remain until reconcile). |
| After commit | All DB committed; post-commit VPN sync/cleanup may not run → renewal may need reconcile; old_uuid may stay in Xray until reconcile. |
| During VPN Phase 1 | No commit yet; no financial state change. |

**finalize_balance_purchase / finalize_balance_topup:** Same as above; single transaction, so either all committed or all rolled back. No mutation after commit except VPN.

**auto_renewal (per-subscription inside one transaction):**

| Crash point | Outcome |
|-------------|---------|
| Before tx | No change. |
| Mid tx (e.g. after decrease_balance, before grant_access) | Rollback; balance not decreased. |
| After commit, before notification send | Payment and subscription committed; user may not get Telegram message; idempotency prevents double charge on retry; notification can be sent on next run if idempotency check/mark is used correctly (mark only after send). |

**fast_expiry:** VPN remove then DB update in nested transaction; if crash after VPN remove but before DB update, UUID is gone in Xray but DB still active — next run will see expired and either skip (already removed) or re-remove (idempotent) and then update DB.

**activation_worker:** DB updates (mark_activation_failed, attempt_activation) use conn; if crash after activation success but before notification, subscription is active; notification may be sent later or skipped by idempotency.

---

## 10) Idempotency Matrix

| Operation | Idempotency key | Second call behavior |
|-----------|-----------------|------------------------|
| finalize_purchase | pending_purchase.status | status != 'pending' → ValueError, no DB change |
| finalize_balance_topup | provider_charge_id in payments | Returns existing payment, no balance change |
| finalize_balance_purchase | Balance + grant + payment in one tx; no external idempotency key | Duplicate handler call could double-spend; handler/FSM should prevent double submit |
| process_referral_reward | (buyer_id, purchase_id) in referral_rewards | existing_reward → success=False, no balance change |
| _consume_promo_in_transaction | used_count < max_uses, UPDATE RETURNING | Exhausted → ValueError, tx rolls back |
| Payment notification | notification_sent on payments | mark_sent only if notification_sent = FALSE; check before send |
| Auto-renewal | last_auto_renewal_at + FOR UPDATE SKIP LOCKED | Same sub not processed twice in same window |

---

## 11) Concurrency & Race Analysis

- **Pool:** max_size 15 (configurable); multiple workers (reminders, auto_renewal, fast_expiry, activation, crypto, reconciliation, health, etc.) share pool. **Risk:** Auto-renewal holds one conn and acquires more (get_last_approved_payment, is_vip_user, get_user_discount) and holds during Telegram send → pool pressure and lock duration.
- **Same subscription:** Auto-renewal uses FOR UPDATE SKIP LOCKED; fast_expiry processes expired only and re-checks before UPDATE; activation uses conn for updates. Manual purchase vs auto_renewal: different code paths; balance decrease and grant_access are serialized by advisory lock / FOR UPDATE per user.
- **Nested transaction:** fast_expiry uses `async with conn.transaction():` inside an outer `async with pool.acquire() as conn:` for the DB update after VPN remove; correct.
- **Parallel mutation on same subscription:** Avoided by FOR UPDATE SKIP LOCKED (auto_renewal), by keyset + re-check (fast_expiry), and by activation_worker using single conn for the subscription row.

---

## 12) Invariant Verification

| Invariant | Status | Notes |
|-----------|--------|------|
| status='active' AND expires_at > now ⇒ uuid exists | Hold | grant_access and finalize set both; fast_expiry clears uuid only when setting expired. |
| status='expired' ⇒ uuid is NULL | Hold | fast_expiry sets status=expired, uuid=NULL together. |
| balance >= 0 always | Hold | decrease_balance checks current_balance >= amount_kopecks; no negative extension. |
| No negative extension | Hold | grant_access validates subscription_end > old_expires_at. |
| Trial never overrides paid | Hold | get_active_paid_subscription used in trial_notifications and fast_expiry; paid wins. |
| Payment must exist for non-trial active | Hold | Payments created in finalize_purchase / finalize_balance_purchase / auto_renewal. |
| No orphan UUID in DB | Hold | reconcile does not delete from DB; fast_expiry clears uuid when expiring. |
| No orphan UUID in Xray | Mitigated | Phase 1 failure cleanup; reconcile removes orphans; rare orphan possible if cleanup fails. |

---

## 13) Production Readiness Score (0–10)

**Score: 7.0**

- **Deduction –1.5:** Auto-renewal runs Telegram send and nested DB acquires inside transaction (critical).
- **Deduction –0.5:** Activation worker holds conn across asyncio.sleep(0.5).
- **Deduction –0.5:** Unbounded or large fetches (reminders, crypto watcher).
- **Deduction –0.5:** Minor risks (reminder idempotency window, processing_uuids in-memory).

**Strengths:** Single-source-of-truth financial paths, atomic transactions, idempotency at boundaries, payment status monotonicity, UUID lifecycle and reconciliation design, keyset pagination where used, FOR UPDATE SKIP LOCKED in auto_renewal.

---

## 14) Final Verdict

**Production at scale: CONDITIONAL YES**

- **Condition:** Address **C1** (move Telegram send and tariff/price resolution out of the auto_renewal transaction) and **C2** (do not hold conn across asyncio.sleep in activation_worker). Until then, run at moderate scale and monitor pool usage and transaction duration.
- **Business logic, payments, UUID, DB integrity:** No change required for correctness; financial atomicity and idempotency are in place.
- **Recommendation:** Fix C1 and C2, then add LIMIT/keyset to reminders and crypto watcher for scalability.

---

## Section 1 — Purchase Logic (Detailed)

- **finalize_purchase:** One conn (`pool.acquire()`), one transaction. Phase 1 (add_vless_user) **outside** tx; inside tx: pending → paid, payment insert (or balance_topup path), grant_access(conn), payment → approved, _consume_promo(conn), process_referral_reward(conn). All financial mutations share same conn; no function in the path acquires a new pool connection when conn is passed. No mutation after commit except VPN sync/cleanup (no DB writes).
- **Balance purchase (finalize_balance_purchase):** Same pattern; decrease_balance(conn), _consume_promo(conn), grant_access(conn), payment insert, process_referral_reward(conn) in one tx.
- **Balance topup (finalize_balance_topup):** Idempotency by provider_charge_id; then payment insert, balance update, balance_transactions, process_referral_reward(conn) in one tx.
- **Crash after balance decrease:** In finalize_balance_purchase the decrease is inside the same tx as grant_access and payment; crash mid-tx → full rollback, so balance not decreased.
- **Crash after grant_access:** Same; rollback, no subscription update.
- **Crash after payment insert:** Rollback, no payment row.
- **Crash after referral reward:** Rollback, no referral_rewards row.
- **Crash after promo consume:** Rollback.
- **Crash before commit:** Full rollback.
- **Crash after commit but before notification:** DB consistent; notification may be skipped or sent later; idempotency (notification_sent) prevents double-send when implemented as “mark only after send.”

---

## Section 2 — Auto-Renewal

- **FOR UPDATE SKIP LOCKED:** Used in query (auto_renewal.py ~74–101); only one process can take each row.
- **last_auto_renewal_at:** Set at start of processing (UPDATE ... WHERE ... AND (last_auto_renewal_at IS NULL OR last_auto_renewal_at < expires_at - 12h)); prevents re-processing same sub in same window.
- **Renewal window:** expires_at <= renewal_threshold (now + RENEWAL_WINDOW) and expires_at > now.
- **Insufficient balance:** No renewal; no notification (by design).
- **Refund path:** On UUID regeneration error, increase_balance(..., source="refund", conn=conn) in same tx.
- **UUID regeneration guard:** Check action_type == "renewal" and vless_url is None; else refund.
- **cooperative_yield:** Used every 50 items; MAX_ITERATION_SECONDS enforced.
- **Race with fast_expiry:** fast_expiry only touches expired (expires_at < now); auto_renewal only touches expires_at in [now, now+RENEWAL_WINDOW]; no overlap.
- **Race with manual purchase:** Different code paths; balance and subscription updated under same conn in each path; advisory lock / FOR UPDATE per user.

---

## Section 3 — Notifications

- **Trial:** send_trial_notification; no DB notification_sent flag for trial; in-memory and schedule-based.
- **Payment:** notification_sent on payments; check before send, mark after send (e.g. mark_payment_notification_sent(payment_id, conn=conn)); UPDATE ... WHERE notification_sent = FALSE.
- **Referral:** Sent after payment success; idempotency by payment/referral state.
- **Broadcast:** Not audited in detail.
- **No blocking send inside transaction:** Violation in auto_renewal (safe_send_message inside tx) — see C1.
- **No notification before commit:** Handlers call finalize first, then send after; auto_renewal currently sends inside tx — fix by moving send after commit.

---

## Section 4 — UUID Lifecycle

- **Trial expiration:** trial_notifications and fast_expiry; fast_expiry uses get_active_paid_subscription → paid UUID never removed by trial logic.
- **fast_expiry:** Only status='active', expires_at < now, uuid IS NOT NULL; skips if active_paid; VPN remove then DB update in inner tx; re-check before UPDATE.
- **reconcile_xray_state:** Orphans = Xray − DB; remove only from Xray; no DB delete.
- **activation_worker:** Processes activation_status='pending'; attempt_activation(conn) updates subscription; no double removal.
- **grant_access:** New issuance creates UUID; renewal keeps UUID; no double creation.
- **Race: trial expires while paid purchase in progress:** finalize_purchase sets subscription and (if payment) trial_expires_at to now; trial worker / fast_expiry see paid and skip.
- **Race: renewal during fast_expiry:** fast_expiry only updates rows that are still active and expired; if renewal commits first, expires_at is extended and row no longer matches.
- **Race: reconcile during activation:** reconcile only removes UUIDs not in DB; activation adds to DB then to Xray; reconcile might run in between and not see new UUID yet — it only removes from Xray, so no removal of the new UUID.

---

## Section 5 — Worker Architecture

Summarized in Worker Safety Table (Section 8). Keyset where used: fast_expiry (id > last_seen_id), reconcile (id > last_seen_id). LIMIT used in auto_renewal, fast_expiry, activation (service limit), reconcile. ORDER BY id ASC for keyset. last_seen_id updated only when rows exist (fast_expiry: `if rows: last_seen_id = rows[-1]["id"]`). MAX_ITERATION_SECONDS in auto_renewal, fast_expiry, activation. No OFFSET pagination. Unbounded: reminders, crypto watcher fetch. No time.sleep; asyncio.sleep used; activation_worker holds conn across sleep (C2). Long tx over network in auto_renewal (C1).

---

## Section 6 — Concurrency & Pool

- **Pool:** max_size 15, min_size 2; timeout 10s; command_timeout 30s.
- **Concurrent workers:** reminders, trial_notifications, healthcheck, health_server, db_retry, fast_cleanup, auto_renewal, activation_worker, xray_sync, reconciliation, crypto_watcher, polling_watchdog.
- **activation_worker sleep(0.5):** Holds conn (Section 2, C2).
- **Polling restart:** New bot per loop; delete_webhook + start_polling with same conn pattern; watchdog os._exit(1) on silence.
- **Crypto watcher warm-up:** Recovery warm-up iterations; no extra connection abuse.
- **Deadlock:** Possible if multiple workers lock same user in different order; advisory lock and FOR UPDATE are per-user; auto_renewal’s nested acquire increases contention.

---

## Section 7 — Crash Consistency Matrix

See Section 9 above (Crash Consistency Matrix).

---

## Section 8 — Invariants

See Section 12 above (Invariant Verification).

---

## Section 9 — Anti-Freeze Patch Impact

- **Watchdog kills mid-transaction:** os._exit(1) kills the process. Any in-flight transaction is aborted by PostgreSQL; no partial commit. **Financial inconsistency:** None; DB state is either pre-tx or committed in full.
- **Double finalize_purchase:** No. After first success, pending_purchase.status = 'paid'; second call raises ValueError; no double credit.
- **Duplicate update processing:** Telegram getUpdates may redeliver updates after restart; idempotency is at business level (pending_purchase, provider_charge_id, referral_rewards, notification_sent). Handlers should be idempotent for same update_id/message_id where applicable.
- **Idempotency sufficient:** Yes for payments and referral; notification and balance purchase rely on FSM and single submission in practice.
- **os._exit(1) and financial state:** No DB commit happens after exit; only committed transactions persist. Safe.

---

*End of audit.*
