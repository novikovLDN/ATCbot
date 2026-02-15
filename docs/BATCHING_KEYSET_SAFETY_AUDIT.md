# Production-Grade Safety Audit: Batching + Keyset Pagination

**Audit Date:** 2026-02-13  
**Scope:** `auto_renewal.py`, `trial_notifications.py`, `fast_expiry_cleanup.py`, `reconcile_xray_state.py`  
**Focus:** Business logic integrity, concurrency safety, pagination correctness, data loss risk, event loop safety, regression validation

---

## 1. ✅ Confirmed Safe Areas

### reconcile_xray_state.py
- **Keyset pagination:** Correct `id > last_seen_id`, `ORDER BY id ASC`, monotonic. No rows skipped.
- **Full UUID collection:** Iterates until empty; collects all DB UUIDs before comparison.
- **Worker type:** TYPE A (read + act, no locking). No financial mutations.
- **No OFFSET:** Uses keyset only; deterministic and stable.
- **Loop termination:** Correct `if not rows: break`.
- **Circuit breaker:** Proper failure handling and backoff.

### trial_notifications.py — Pagination & Structure
- **Keyset pagination:** `s.id > last_subscription_id`, `ORDER BY s.id ASC`. Correct.
- **Batching:** `LIMIT $3` applied; `last_subscription_id = rows[-1]["subscription_id"]` updates correctly.
- **Worker type:** TYPE A (read + act). No financial mutations.
- **Trial/paid guards:** `paid_subscription_expires_at` check; trial never overrides paid.
- **Notification flags:** `trial_notif_6h_sent`, etc. prevent re-sending after flag set.
- **Expire-trial guards:** `get_active_paid_subscription` before expiring; trial never revokes paid.
- **Yield between batches:** `await asyncio.sleep(BATCH_YIELD_SLEEP)` after each batch.

### fast_expiry_cleanup.py — Business Logic & Pagination
- **Keyset pagination:** `id > last_seen_id`, `ORDER BY id ASC`. Correct.
- **Expiration guards:** `expires_at < now_utc`; paid subscription overrides trial.
- **processing_uuids:** Guards against double-processing same UUID within run.
- **Double-deletion guard:** Re-check before DB update; `status='active'` and `uuid=$2`.
- **cooperative_yield:** Every 50 iterations; `MAX_ITERATION_SECONDS` respected.
- **Yield between batches:** `await asyncio.sleep(0)` after each batch.

### auto_renewal.py — Design Intent
- **FOR UPDATE SKIP LOCKED:** Prevents concurrent processing of same subscription.
- **Transaction per batch:** Single transaction for fetch + processing.
- **grant_access(conn=conn):** Uses shared connection for atomicity.
- **last_auto_renewal_at:** Protects against double renewal.
- **Balance deduction before grant_access:** Correct order.
- **UUID validation:** Checks `action == "renewal"` and `vless_url is None`.
- **Refund paths:** On UUID regenerate or `expires_at=None`.
- **Notification idempotency:** `check_notification_idempotency`, `mark_notification_sent`.

---

## 2. ⚠️ Risk Areas

### trial_notifications.py
| Risk | Description |
|------|-------------|
| **TOCTOU on paid subscription** | `paid_subscription_expires_at` comes from batch fetch. User could buy paid subscription after fetch but before processing; trial notification might be sent to a paid user. Mitigation: Add fresh `get_active_paid_subscription(conn, telegram_id, now)` at start of `_process_single_trial_notification`. |
| **Double notification on crash** | `trial_notif_*_sent` is updated after send. If worker crashes between send and UPDATE, next run may re-send. Best-effort only, not transactional idempotency. |
| **Per-row pool.acquire()** | `_process_single_trial_notification` acquires a new connection per row. No lock; fine for read+act, but more connections than batch-level conn. |

### fast_expiry_cleanup.py
| Risk | Description |
|------|-------------|
| **Nested pool.acquire() during update** | Inside the row loop, after VPN removal, acquires `conn2` for DB update. Transaction is separate from main conn. OK for correctness; only slight connection churn. |
| **Logging placement** | `log_worker_iteration_end` is called inside the `while True` loop after every non-empty batch, not once at end of run. Minor observability issue. |
| **processing_uuids across iterations** | `processing_uuids` is never cleared between worker runs. UUIDs from previous runs remain; no functional bug but set grows unbounded over time (UUIDs removed via `discard` after processing). |

### auto_renewal.py — Transaction Boundary
| Risk | Description |
|------|-------------|
| **decrease_balance / increase_balance not using conn** | `database.decrease_balance()` and `increase_balance()` do not accept `conn`; they acquire their own pool and transaction. Balance and subscription updates are not in the same transaction. If process crashes between `decrease_balance` and `grant_access`, balance is deducted but subscription is not renewed. Refund paths exist for explicit failures, but not for crashes. |

---

## 3. ❌ Critical Issues

### auto_renewal.py — Runtime Crash (NameError)

**Variables used but never defined from `sub_row`:**

| Line | Variable | Source | Impact |
|------|----------|--------|--------|
| 146, 152, 154, 160, 164, 173, etc. | `telegram_id` | Should be `sub_row["telegram_id"]` | **NameError** — worker crashes on first subscription |
| 219 | `subscription` | Should be `sub_row` | **NameError** — `subscription.get("balance", 0)` |
| 323 | `language` | Should be `sub_row["language"]` | **NameError** — `i18n.get_text(language, ...)` |

**Effect:** `process_auto_renewals` raises `NameError` as soon as it tries to process any subscription. Auto-renewal does not run.

**Fix:** At start of loop body, add:
```python
telegram_id = sub_row["telegram_id"]
subscription = sub_row  # or use sub_row directly
language = sub_row.get("language", "en")
```

### auto_renewal.py — Indentation / Logic Bug

Lines 318–321 vs 322–322 and 366–372:

- `if notification_already_sent:` and the following notification send block are indented as siblings of `if balance_rubles >= amount_rubles`, not nested inside it.
- The `else: # Баланса не хватает` is paired with `if notification_already_sent`, not with `if balance_rubles >= amount_rubles`.
- When `balance_rubles < amount_rubles`, the `else` branch is skipped; execution falls through to `if notification_already_sent`, which uses an undefined variable → **NameError**.

**Correct structure should be:**
```python
if balance_rubles >= amount_rubles:
    ...  # all renewal logic including notification_already_sent
else:
    # insufficient balance
```

### fast_expiry_cleanup.py — Use of Released Connection

**Location:** Line 299: `get_active_paid_subscription(conn, telegram_id, now_utc)`

**Cause:** `conn` is obtained from:
```python
while True:
    async with pool.acquire() as conn:
        rows = await conn.fetch(...)
    # conn is released here
    for row in rows:
        ...
        active_paid = await database.get_active_paid_subscription(conn, ...)  # conn is released
```

After the `async with` block, `conn` is released to the pool. The loop reuses this released connection. Behavior is undefined (possible "connection is closed" errors or cross-coroutine contamination).

**Fix:** Either keep `conn` in scope for the whole batch:

```python
while True:
    async with pool.acquire() as conn:
        rows = await conn.fetch(...)
        if not rows: break
        for row in rows:
            ...
            active_paid = await database.get_active_paid_subscription(conn, telegram_id, now_utc)
        last_seen_id = rows[-1]["id"]
    await asyncio.sleep(0)
```

or acquire a new connection per row for the active-paid check.

---

## 4. Suggested Hardening Improvements

1. **auto_renewal:**
   - Add `telegram_id = sub_row["telegram_id"]`, use `sub_row` instead of `subscription`, and `language = sub_row.get("language", "en")`.
   - Fix indentation so notification logic and `else: # insufficient balance` are correctly nested under `if balance_rubles >= amount_rubles`.
   - Consider extending `decrease_balance` / `increase_balance` to accept `conn` so balance and subscription updates are in one transaction.

2. **trial_notifications:**
   - Call `get_active_paid_subscription(conn, telegram_id, now)` at the start of `_process_single_trial_notification` to avoid TOCTOU with paid subscription.

3. **fast_expiry_cleanup:**
   - Keep `conn` in scope for the whole batch and pass it to `get_active_paid_subscription`, or acquire a new conn per row.
   - Move `log_worker_iteration_end` outside the `while True` loop so it runs once per worker iteration.
   - Optionally cap or reset `processing_uuids` per run to avoid unbounded growth.

4. **General:**
   - Add integration tests for keyset pagination with large datasets and concurrent inserts.
   - Add unit tests for `process_auto_renewals` with mock `sub_row` to catch variable-name bugs.

---

## 5. Final Production Safety Score

| Component | Score | Notes |
|-----------|-------|-------|
| auto_renewal.py | **2/10** | Critical NameError + indentation bugs; worker does not run |
| trial_notifications.py | **7/10** | Pagination correct; TOCTOU and best-effort idempotency |
| fast_expiry_cleanup.py | **5/10** | Use of released connection; pagination and logic otherwise OK |
| reconcile_xray_state.py | **9/10** | Solid; only minor hardening possible |

**Overall: 5/10** — Blocked by critical issues in `auto_renewal.py` and `fast_expiry_cleanup.py`.

---

## 6. Explicit Confirmation

| Area | Status | Notes |
|------|--------|-------|
| **Purchases safe?** | ⚠️ N/A | Auto-renewal does not process purchases directly. Purchase flow is separate. |
| **Renewals safe?** | ❌ No | Auto-renewal worker crashes (NameError). No renewals are processed. |
| **Trials safe?** | ✅ Yes | Trial notifications and expire logic are correct; paid guards in place. Minor TOCTOU risk. |
| **Notifications safe?** | ⚠️ Best-effort | Trial notifications may double-send on crash; auto-renewal notifications never sent due to worker crash. |
| **UUID lifecycle safe?** | ⚠️ Partial | Trial and fast_expiry handle UUID correctly when they run. Auto-renewal never runs. Fast_expiry has released-conn bug that can cause incorrect behavior. |

---

## Appendix: Phase-by-Phase Summary

### Phase 1 — Business Logic Integrity
- **auto_renewal:** Logic is sound in design, but runtime bugs prevent execution.
- **trial_notifications, fast_expiry, reconcile:** Business conditions, WHERE clauses, and guards preserved.

### Phase 2 — Concurrency Safety
- **auto_renewal:** FOR UPDATE SKIP LOCKED + single transaction per batch is correct. Critical code bugs prevent execution.
- **trial_notifications:** No locking; acceptable for TYPE A. Minimal race with trial_expire (run sequentially).
- **fast_expiry:** Use of released `conn` is a concurrency/correctness bug.
- **reconcile:** TYPE A; no locking required.

### Phase 3 — Pagination Correctness
- All four modules use keyset pagination with `id > last_seen_id`, `ORDER BY id ASC`, and deterministic termination.
- No OFFSET; no known skip or duplicate rows in design. Fast_expiry bug may affect row handling indirectly.

### Phase 4 — Data Loss Risk
- **auto_renewal:** No renewals occur (worker broken); no silent data loss from logic.
- **fast_expiry:** Released-conn bug may lead to incorrect skips or errors.
- **reconcile:** Full UUID collection; no intentional skips.

### Phase 5 — Event Loop & Pool Safety
- Cooperative yields and `MAX_ITERATION_SECONDS` present where needed.
- No long-held transactions across yields in correct code paths.
- Fast_expiry re-acquires pool inside loop; acceptable but not ideal.

### Phase 6 — Regression Diff
- **Functional changes:** Keyset pagination, batching, SKIP LOCKED in auto_renewal.
- **Preserved:** WHERE clauses, guards, order of operations.
- **Behavioral drift:** Auto-renewal broken by undefined variables and indentation; fast_expiry by released-conn usage.
