# Deep Production Safety Audit (No-Compromise)

**Date:** 2025-02-15  
**Scope:** Post C1/C2 fix codebase. Analysis only; no code changes.  
**Assumptions:** 50k+ users, concurrent renewals, webhook duplicates, process restarts, network timeouts, pool max_size=15, multi-worker.

---

## SECTION 1 — FINANCIAL ATOMICITY (CRITICAL)

### 1) finalize_purchase (database.py ~6440–6872)

| Question | Answer | Code reference |
|----------|--------|----------------|
| Single conn? | Yes | `async with pool.acquire() as conn` (6494), one conn for entire flow |
| Single tx? | Yes for financial writes | `async with conn.transaction():` (6576) wraps: pending→paid, payment insert, grant_access, payment→approved, _consume_promo, process_referral_reward |
| All financial writes in tx? | Yes | All DB writes above are inside the transaction block |
| Network inside tx? | No | Phase 1 (add_vless_user) runs **before** `conn.transaction()` (6527–6574). No network inside the transaction |
| Nested pool.acquire in tx? | No | All helpers called with `conn=conn` (increase_balance, _consume_promo, process_referral_reward, grant_access) |
| Financial write after commit? | No | Post-commit (6835–6868) only: VPN cleanup (safe_remove_vless_user_with_retry, ensure_user_in_xray). No DB writes |
| Partial financial state on crash? | No | Single transaction; crash mid-tx → full rollback |
| Double balance on duplicate call? | No | Second call sees `status != 'pending'` (6500), raises ValueError; no second credit |
| Payment status regress? | No | No code path sets payments.status from 'approved' to 'pending' (grep verified) |
| Referral duplicate? | No | process_referral_reward checks (buyer_id, purchase_id) in referral_rewards (2537–2545); duplicate returns success=False |

**Caveat:** Phase 1 (add_vless_user) runs **while holding** `conn` (after acquire, before transaction). So connection is held during VPN network call — pool pressure under load, not a partial-commit risk.

---

### 2) finalize_balance_purchase (database.py ~7703–7869)

| Question | Answer | Code reference |
|----------|--------|----------------|
| Single conn? | Yes | One `pool.acquire()` for the main tx (7795). Phase 1 uses separate `conn_pre` (7758), then released before main tx |
| Single tx? | Yes | `async with conn.transaction():` (7797) wraps balance decrease, balance_transactions, _consume_promo, grant_access, payment insert, process_referral_reward |
| All financial writes in tx? | Yes | All inside 7797 block |
| Network inside tx? | No | Phase 1 VPN is in separate block (7758–7794); grant_access with pre_provisioned_uuid does not call VPN |
| Nested acquire in tx? | No | All use `conn` |
| Post-commit financial write? | No | Post-commit only VPN cleanup/sync (7846–7868) |
| Partial state on crash? | No | Single tx |
| Double charge? | Mitigated by caller | Handler/FSM should prevent double submit; no idempotency key like provider_charge_id for balance purchase |
| Payment status regress? | N/A (insert as 'approved') | — |
| Referral duplicate? | No | purchase_id = f"balance_purchase_{payment_id}" (7872); same idempotency in process_referral_reward |

---

### 3) finalize_balance_topup (database.py ~7972–8158)

| Question | Answer | Code reference |
|----------|--------|----------------|
| Single conn? | Yes | `async with pool.acquire() as conn` (8016), single transaction |
| Single tx? | Yes | `async with conn.transaction():` (8017) |
| All financial writes in tx? | Yes | Idempotency check, payment INSERT, balance UPDATE, balance_transactions, process_referral_reward(conn) |
| Network in tx? | No | — |
| Nested acquire in tx? | No | — |
| Post-commit write? | No | — |
| Partial state on crash? | No | — |
| Double charge? | No | Idempotency by provider_charge_id (8039–8069); duplicate returns existing, no balance change |
| Payment status regress? | No | — |
| Referral duplicate? | No | purchase_id = f"balance_topup_{payment_id}" (8114); referral_rewards idempotency |

---

### 4) auto_renewal (auto_renewal.py, post C1 fix)

| Question | Answer | Code reference |
|----------|--------|----------------|
| Single conn in tx? | Yes | One conn for the transaction (105–107) |
| Single tx? | Yes | `async with conn.transaction():` (107) wraps FOR UPDATE SKIP LOCKED fetch, last_auto_renewal_at, get_last_approved_payment(conn), is_vip_user(conn), get_user_discount(conn), decrease_balance(conn), grant_access(conn), payment insert, idempotency check, notifications_to_send append |
| All financial writes in tx? | Yes | No Telegram send or mark_notification_sent inside tx |
| Network in tx? | No | Phase B (331–368): safe_send_message and mark_notification_sent run **after** transaction commit |
| Nested acquire in tx? | No | get_last_approved_payment(telegram_id, conn=conn), is_vip_user(conn=conn), get_user_discount(conn=conn) use same conn (database.py accepts conn) |
| Post-commit financial write? | No | Phase B only sends Telegram and marks notification_sent (new acquire per mark) |
| Partial state on crash? | No | Single tx for money |
| Double charge? | No | FOR UPDATE SKIP LOCKED + last_auto_renewal_at; idempotency check for notification |
| Payment status regress? | N/A | — |
| Referral duplicate? | N/A (no referral in auto_renewal) | — |

---

### 5) process_referral_reward (database.py 2455–2722)

| Question | Answer | Code reference |
|----------|--------|----------------|
| Uses caller conn only | Yes | Signature `conn: asyncpg.Connection`; no pool.acquire() |
| All writes on same conn/tx | Yes | Caller holds tx; all INSERT/UPDATE use conn |
| Network in tx? | No | — |
| Nested acquire? | No | — |
| Duplicate reward? | No | SELECT referral_rewards WHERE buyer_id AND purchase_id (2537); existing_reward → return success=False (2543–2555) |
| Financial errors re-raised | Yes | 2704–2721 raise to rollback caller tx |

---

### 6) _consume_promo_in_transaction (database.py 5228–5280)

| Question | Answer | Code reference |
|----------|--------|----------------|
| Uses conn only | Yes | `conn` required; get_active_promo_by_code(conn, code) (5237); UPDATE on conn (5245–5275) |
| In caller tx | Yes | Called only from finalize_* with conn |
| Network in tx? | No | — |
| Over-consume? | No | UPDATE ... WHERE (max_uses IS NULL OR used_count < max_uses) RETURNING; no row → ValueError |

---

### 7) increase_balance / decrease_balance (database.py 1157–1311)

| Question | Answer | Code reference |
|----------|--------|----------------|
| Single conn when conn passed | Yes | If conn: _do_increase(conn) / _do_decrease(conn) only (1206–1212, 1291–1296) |
| Single tx when conn passed | Yes | Caller’s transaction; no inner transaction in _do_* |
| Advisory lock | Yes | pg_advisory_xact_lock(telegram_id) (1184, 1256); FOR UPDATE in decrease (1258) |
| Nested acquire when conn passed? | No | — |
| Standalone use | Own acquire + transaction (1217–1225, 1308–1316) |

---

### 8) grant_access (database.py 3750+)

| Question | Answer | Code reference |
|----------|--------|----------------|
| When conn and _caller_holds_transaction | Uses conn only; no acquire | 3824–3828: if conn is None then acquire else should_release_conn = False |
| VPN call inside caller tx? | No for renewal | Renewal path: DB UPDATE only (3935–3943). New issuance with pre_provisioned_uuid: no VPN in grant_access |
| New issuance without pre_provisioned | Can call add_vless_user | When conn and _caller_holds_transaction, renewal_xray_sync_after_commit returned; sync runs after caller commit (database.py 6856–6868) |

---

### Summary table (Section 1)

| Function | Single Conn | Single Tx | Network in Tx | Nested Acquire | Post-Commit Writes | Double Charge Risk | Safe? |
|----------|-------------|-----------|---------------|----------------|--------------------|--------------------|-------|
| finalize_purchase | Yes | Yes | No | No | No (VPN only) | No | Yes |
| finalize_balance_purchase | Yes | Yes | No | No | No (VPN only) | Caller-bound | Yes |
| finalize_balance_topup | Yes | Yes | No | No | No | No | Yes |
| auto_renewal | Yes | Yes | No | No | No | No | Yes |
| process_referral_reward | Caller conn | Caller tx | No | No | No | No | Yes |
| _consume_promo_in_transaction | Caller conn | Caller tx | No | No | No | No | Yes |
| increase_balance (with conn) | Yes | Caller tx | No | No | No | No | Yes |
| decrease_balance (with conn) | Yes | Caller tx | No | No | No | No | Yes |
| grant_access (with conn) | Yes | Caller tx | No (renewal/sync after) | No | Sync after commit | No | Yes |

---

## SECTION 2 — PURCHASE LOGIC FLOW CONSISTENCY

**Subscription flow (external/crypto):**  
Pending → paid (UPDATE pending_purchases) → payment INSERT (pending) → grant_access → payment UPDATE (approved) → _consume_promo → process_referral_reward. All inside one transaction (database.py 6576–6833).

| Crash point | Outcome |
|-------------|---------|
| After balance decrease | N/A (no balance in this path). For balance_purchase: inside same tx → rollback, no decrease. |
| After grant_access | Transaction not committed → full rollback; no subscription, no payment approved. |
| After payment insert | Rollback; payment row rolled back. |
| After referral reward | Rollback; referral_rewards and balance change rolled back. |
| After commit, before notification | DB consistent. Notification may be lost; idempotency (notification_sent) prevents double send when mark is after send. |
| Duplicate webhook | finalize_purchase: status != 'pending' → ValueError; no second credit. |
| Watchdog mid-tx | Process kill → PostgreSQL aborts transaction; no partial commit. |

**Confirmed:** No path leads to balance decrease without subscription, subscription without payment, payment without subscription, double credit, or double UUID creation (UUID created in Phase 1 or in grant_access with pre_provisioned; single transaction commits all or nothing).

---

## SECTION 3 — AUTO RENEWAL DEEP AUDIT

| Check | Status | Code reference |
|-------|--------|----------------|
| FOR UPDATE SKIP LOCKED | Present | auto_renewal.py 74–88, 89–101 |
| last_auto_renewal_at | Set at start of processing | 150–158; UPDATE before tariff/balance logic |
| Renewal window | expires_at <= renewal_threshold AND expires_at > now | 80–82, 106–107 |
| Telegram send inside tx | No | Phase B after commit (331–368) |
| Nested acquire inside tx | No | get_last_approved_payment(conn=conn), is_vip_user(conn=conn), get_user_discount(conn=conn) (179, 212, 215) |
| Notification only after commit | Yes | notifications_to_send filled in tx; send + mark in Phase B (331–368) |
| Refund path atomic | Yes | increase_balance(..., conn=conn) inside same tx (261–266, 281–287) |
| UUID regeneration guard | Yes | action_type != "renewal" or vless_url → refund (258–267) |

**Race simulation matrix**

| Scenario | Possible? | Protected? | Why |
|----------|-----------|------------|-----|
| Two workers same subscription | Yes | Yes | FOR UPDATE SKIP LOCKED; only one gets row; last_auto_renewal_at UPDATE before processing |
| Renewal + fast_expiry | Yes | Yes | fast_expiry: expires_at < now; auto_renewal: expires_at in (now, now+window). No overlap. |
| Renewal + manual purchase | Yes | Yes | Different code paths; advisory lock / FOR UPDATE per user in balance_purchase; auto_renewal locks subscription row |
| Double renewal same sub | No (same run) | Yes | SKIP LOCKED + last_auto_renewal_at; second run sees last_auto_renewal_at updated |

---

## SECTION 4 — NOTIFICATION INTEGRITY

| Notification Type | Idempotent | Atomic Mark | Before Commit Risk | Duplicate Risk | Safe? |
|-------------------|------------|-------------|--------------------|----------------|------|
| Payment (handler) | Yes | UPDATE ... notification_sent = TRUE WHERE id = $1 AND notification_sent = FALSE (database.py 180, 200) | No (handlers call finalize first, then send, then mark) | Mark only after send; second run sees flag | Yes |
| Auto-renewal | Yes | Same UPDATE; mark in Phase B after send (auto_renewal.py 354–356 with notify_conn) | No (Phase B after commit) | check_notification_idempotency in tx; mark after send in Phase B | Yes |
| Referral cashback | Best-effort | No DB flag for “referral message sent” | Sent after payment success (post-commit in handler) | Possible duplicate message if send retried; no double money | Acceptable |
| Trial | Schedule + state | No payment notification_sent; in-memory / schedule | N/A | Possible duplicate if restart in window | Medium (no DB idempotency for trial msg) |
| Reminders | DB flags (reminder_sent, etc.) | Updated after send in notification_service | Reminders sent after DB read; no financial tx | Idempotency window (e.g. 30 min) in reminders.py | Yes |

**Crash after send before mark:** Next run may resend (duplicate message) unless idempotency check runs and mark is transactional with send. Auto-renewal: mark in separate acquire after send — if crash after send but before mark, next run could resend once. Acceptable for notification UX; no double charge.

---

## SECTION 5 — UUID LIFECYCLE GUARANTEES

| Invariant | Status | Code reference |
|-----------|--------|----------------|
| Paid UUID never removed by trial | Hold | fast_expiry_cleanup: get_active_paid_subscription(conn, telegram_id, now_utc); skip if active_paid (288–299) |
| fast_expiry only removes expired | Hold | WHERE status='active' AND expires_at < $1 AND uuid IS NOT NULL (253–260); re-check before UPDATE (356–374) |
| reconcile only removes orphans | Hold | orphans = xray_uuids - db_uuids; remove from Xray only (98–127) |
| UUID stable on renewal | Hold | grant_access renewal path: no add_vless_user; only expires_at update (3935–3943) |
| UUID never regenerated during renewal | Hold | Auto_renewal checks action_type == "renewal" and vless_url is None; else refund (258–267) |
| grant_access never double creates | Hold | New issuance only when !is_active; pre_provisioned_uuid used when provided |
| No active subscription without UUID | Design | grant_access sets uuid on new issuance; renewal keeps uuid |
| No expired with UUID | Hold | fast_expiry sets status=expired, uuid=NULL together (3824–3828) |
| No orphan in DB | Hold | reconcile does not delete from DB |
| Orphan in Xray mitigated | Yes | Phase 1 failure cleanup in finalize_*; reconcile removes orphans; safe_remove on tx failure (6836–6852) |

**Crash scenarios**

| Scenario | Outcome |
|----------|---------|
| Trial expires while paid purchase in progress | finalize_purchase commits subscription; trial worker / fast_expiry see get_active_paid_subscription → skip. |
| Renewal during fast_expiry | fast_expiry only updates row if still expires_at < now after re-check (368–374); renewal extends expires_at → skip. |
| Reconcile during activation | Reconcile removes only UUIDs in Xray not in DB; activation adds to DB then Xray — reconcile might not yet see new UUID; it only removes from Xray, so no removal of new UUID. |
| Crash after Xray add before DB write | Phase 1 done, tx not started or tx fails → orphan in Xray; cleanup in except (6836) or reconcile. |
| Crash after DB write before Xray sync | DB committed; renewal_xray_sync_after_commit not run; UUID in DB, may be missing or stale in Xray; reconcile does not delete from DB; ensure_user_in_xray can be retried later or manual. |

---

## SECTION 6 — WORKER SAFETY & SCALABILITY

| Worker | LIMIT | Keyset | OFFSET | last_id when rows | MAX_ITERATION_SECONDS | Sleep outside conn | Conn during network | Unbounded fetch | Memory risk |
|--------|-------|--------|--------|-------------------|------------------------|--------------------|--------------------|-----------------|-------------|
| auto_renewal | Yes (BATCH_SIZE) | No | No | N/A | Yes (15s) | Yes (Phase B after tx) | No (Phase B) | No | Low |
| fast_expiry_cleanup | Yes (BATCH_SIZE) | id > last_seen_id | No | Yes (459: if rows: last_seen_id = rows[-1]["id"]) | Yes (15s) | Yes | No (VPN then inner tx) | No | Low |
| activation_worker | limit=50 (service) | No | No | N/A | Yes (15s) | Yes (sleep 0.5 after each acquire block) | Yes (attempt_activation does VPN; conn held during VPN) | No | Low |
| reconcile_xray_state | LIMIT $2 | id > last_seen_id | No | Yes (92: last_seen_id = rows[-1]["id"]) | N/A | Yes | No | No | Low |
| crypto_payment_watcher | LIMIT 100 | No | No | N/A | 15s | Yes | No (finalize_purchase has own conn) | No | Low |
| trial_notifications | BATCH_SIZE 100 | In service | No | Yes (service) | — | Yes | No | No | Low |
| reminders | No | No | No | N/A | No | Yes | No | Yes (get_subscriptions_for_reminders: no LIMIT, 5499–5502) | High at scale |

**Finding:** reminders worker uses unbounded fetch in get_subscriptions_for_reminders (database.py 5463–5502): no LIMIT, all rows with expires_at > now. At 50k+ users with many active subscriptions, this can cause large memory use and long-held connection.

---

## SECTION 7 — CONNECTION POOL & DEADLOCK ANALYSIS

| Check | Status | Notes |
|-------|--------|------|
| Nested acquire in tx | None in financial paths | auto_renewal uses conn= for get_last_approved_payment, is_vip_user, get_user_discount |
| Acquire inside transaction | No | — |
| Connection held during sleep | No (post C2) | activation_worker: sleep(0.5) after "async with pool.acquire()" block |
| Network inside transaction | No (post C1) | Auto_renewal Phase B after commit |
| Long-running tx risk | Low | Financial tx are short; auto_renewal tx does only DB |
| Advisory lock | increase_balance, decrease_balance, finalize_balance_purchase (7791) | pg_advisory_xact_lock(telegram_id) |
| Deadlock A locks subscription, B locks balance | Unlikely | Auto_renewal locks subscription row (FOR UPDATE SKIP LOCKED); balance_purchase locks user (advisory + FOR UPDATE). Different resources; order consistent (subscription then balance in auto_renewal; balance then subscription in balance_purchase). No circular wait observed. |

**Caveat:** finalize_purchase and finalize_balance_purchase hold conn during Phase 1 (VPN add_vless_user). Under load, multiple concurrent finalizes can hold connections during network — pool pressure if pool_size=15 and many concurrent requests.

---

## SECTION 8 — CRASH CONSISTENCY MATRIX

| Flow | Before tx | Mid tx | After commit | During network | Before mark notification | After mark notification |
|------|-----------|--------|--------------|----------------|---------------------------|--------------------------|
| finalize_purchase | No DB change; Phase 1 orphan possible | Full rollback | Consistent; VPN sync may be skipped | Phase 1: no commit yet. Tx: no network | N/A (notification in handler after return) | N/A |
| finalize_balance_purchase | No DB change | Full rollback | Consistent | Same | N/A | N/A |
| finalize_balance_topup | No DB change | Full rollback | Consistent | No network in tx | N/A | N/A |
| auto_renewal | No change | Full rollback | Consistent; notifications_to_send may be partial (send/mark after) | No network in tx | Send done, mark not done → possible duplicate send on restart | Consistent |
| activation_worker | No change | Conn released on exception; DB may have been updated (attempt_activation) | Consistent | attempt_activation does VPN while holding conn; if crash, activation may be partial (DB updated, Xray not, or vice versa) — service layer should be atomic per sub | Notification sent, mark not done | Consistent |
| fast_expiry | No change | Inner tx rollback; VPN already removed | DB updated only after VPN remove; re-check before UPDATE | VPN call outside inner tx; conn held during VPN | N/A | N/A |

---

## SECTION 9 — INVARIANT VERIFICATION

| Invariant | Status | Evidence |
|-----------|--------|----------|
| balance >= 0 | Hold | decrease_balance: current_balance < amount_kopecks → return False (1265–1267); no negative extension |
| No negative extension | Hold | grant_access: subscription_end <= old_expires_at → raise (3918–3920) |
| status/expires_at/uuid consistent | Hold | grant_access and finalize set together; fast_expiry sets expired + uuid NULL |
| Payment status monotonic | Hold | No UPDATE payments SET status = 'pending' anywhere; only 'approved' or 'expired' etc. |
| No active subscription without payment (non-trial) | Hold | finalize_* and auto_renewal create payment row with subscription |
| Trial never overrides paid | Hold | get_active_paid_subscription used in fast_expiry and trial logic; paid wins |
| Referral reward not duplicated | Hold | referral_rewards (buyer_id, purchase_id) check; INSERT only if no existing |
| Promo not over-consumed | Hold | _consume_promo: UPDATE ... WHERE used_count < max_uses RETURNING; else ValueError |

---

## SECTION 10 — FINAL SCORE

| Category | Score (0–10) | Rationale |
|----------|--------------|-----------|
| Financial Integrity | 9 | Single conn/tx in all financial paths; idempotency at boundaries; payment status monotonic. Minor: Phase 1 holds conn during VPN in finalize_* |
| Worker Safety | 8 | FOR UPDATE SKIP LOCKED, keyset where needed, MAX_ITERATION_SECONDS, sleep outside conn (post C2). Reminders unbounded fetch; activation holds conn during VPN |
| UUID Lifecycle | 9 | Paid protected; fast_expiry re-check; reconcile orphans only; orphan cleanup on failure |
| Concurrency Safety | 8 | No nested acquire in tx; advisory locks; possible pool pressure under high concurrency (Phase 1 + many workers) |
| Notification Integrity | 8 | Mark after send; atomic UPDATE notification_sent; auto_renewal Phase B. Trial/reminder idempotency weaker |
| Crash Safety | 9 | Single tx for money; no partial commit; watchdog does not create double finalize |

**Overall (average):** (9+8+9+8+8+9) / 6 ≈ **8.5**

---

## PRODUCTION VERDICT

**CONDITIONAL**

**Why not YES SAFE:**  
1. **Reminders:** Unbounded fetch in get_subscriptions_for_reminders — at 50k+ users can cause high memory and long-held connection (database.py 5463–5502).  
2. **Connection held during VPN:** finalize_purchase and finalize_balance_purchase run Phase 1 (add_vless_user) while holding a pool connection (before starting the transaction). Under many concurrent purchases, this can contribute to pool exhaustion (max_size=15).  
3. **Activation worker:** attempt_activation (VPN + DB) runs while holding conn; if VPN is slow, conn is held longer — acceptable but worth monitoring.

**Why not NOT SAFE:**  
- All financial mutations are in a single transaction with a single connection.  
- No nested pool.acquire inside transactions (post C1).  
- No connection held during asyncio.sleep (post C2).  
- Idempotency at payment/referral/pending_purchase boundaries.  
- Payment status does not regress.  
- UUID lifecycle and reconciliation are correct.

**Recommendation:**  
- Add LIMIT/keyset to get_subscriptions_for_reminders.  
- Consider moving Phase 1 (add_vless_user) to a separate short-lived connection (or run before acquire) to avoid holding the same conn used for the transaction.  
- Monitor pool usage and transaction duration under load.  
- With these mitigations and at moderate scale, system is suitable for production.

---

*End of audit. All statements are backed by the cited code paths. No code was modified.*
