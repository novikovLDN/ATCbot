# Final Architectural Cleanup — Deliverable

**Date:** 2025-02  
**Scope:** Production-safe removal of dead code, audit artifacts, and architectural noise. No business logic changes.

---

## 1. Removed Files

| File | Reason |
|------|--------|
| `docs/FULL_PRODUCTION_FREEZE_INVESTIGATION_AUDIT.md` | Deprecated audit artifact |
| `docs/AUDIT_RECONCILE_VPN_POOL_FREEZE.md` | Deprecated audit (reconcile removed) |
| `docs/ASYNC_SAFETY_AUDIT_REPORT.md` | Deprecated audit artifact |
| `docs/FULL_SYSTEM_ARCHITECTURE_AUDIT_STAGE_PROD.md` | Deprecated audit artifact |
| `docs/FORENSIC_AUDIT_11MIN_FREEZE_FULL.md` | Deprecated forensic audit |
| `docs/FORENSIC_AUDIT_11MIN_FREEZE.md` | Deprecated forensic audit |

**Not removed (as requested):**
- `docs/REFACTOR_PLAN_ARCHITECTURAL_SIMPLIFICATION.md` — kept as reference
- `README.md`, migration docs
- No duplicate handler files found (`notifications 2.py`, `__init__ 2.py` were already removed)
- `app/core/watchdog_heartbeats.py` — already deleted in prior refactor
- `cursor/` — not present

---

## 2. Modified Files

| File | Changes |
|------|---------|
| `main.py` | Removed `last_update_timestamp` and `update_timestamp_middleware` (dead). Removed outline_cleanup comment block and disabled cleanup_task log. Removed `# import outline_cleanup` comment. |
| `config.py` | Comment wording: "background reconciliation" → "sync DB subscriptions to Xray" for XRAY_SYNC_ENABLED. No dead flags (XRAY_RECONCILIATION already removed). |
| `app/core/metrics.py` | Removed registration of unused gauges `recovery_in_progress` and `cooldown_active`. |
| `app/core/alerts.py` | Removed `recovery_in_progress` check in degraded alert (gauge no longer set). Docstring: "cooldown, recovery" → "spam cooldown". |
| `health_server.py` | Removed obsolete comments (recovery_cooldown, recovery_in_progress, B4.5). |

---

## 3. Business Logic — Unchanged

- **Payments:** `finalize_purchase`, payment idempotency, CryptoBot flow — not modified.
- **Subscriptions:** Schema, `grant_access`, renewal, `expires_at` — not modified.
- **UUID lifecycle:** Stable on renewal; regenerated only on reissue — not modified.
- **Transaction boundaries:** No DB connection across HTTP; two-phase add/remove preserved — not modified.
- **HTTP timeouts:** Not changed (VPN_HTTP_TIMEOUT, worker intervals unchanged).
- **Advisory lock:** Kept. **telegram_network_watchdog:** Kept.

---

## 4. Final Simplified Architecture

```
main
├── advisory_lock (PostgreSQL, key 987654321)
├── init_db / get_pool (single pool, retry 1)
├── background_tasks (simple list)
│   ├── db_retry_task (if DB not ready)
│   ├── reminders_task
│   ├── fast_expiry_cleanup_task
│   ├── auto_renewal_task
│   ├── activation_worker_task
│   ├── xray_sync_task (optional)
│   ├── crypto_payment_watcher_task
│   └── telegram_network_watchdog
├── telegram_network_watchdog (silence > TELEGRAM_LIVENESS_TIMEOUT → os._exit(1))
├── start_polling (single call, timeout=30, handle_signals=False)
└── graceful shutdown (cancel tasks, release lock, close pool, close bot session)
```

**Workers (simple loops):**
- activation_worker, auto_renewal, fast_expiry_cleanup, crypto_payment_watcher, reminders, trial_notifications, healthcheck.

**VPN (vpn_utils):**
- add_vless_user, update_vless_user, remove_vless_user, ensure_user_in_xray. DB as source of truth; no list_vless_users, no reconcile.

**DB:**
- Single pool; `acquire_connection` (pool_monitor) for labeled acquires; no HTTP inside DB scope.

**Removed from runtime:**
- Reconcile worker, list_vless_users, recovery_cooldown, multi-watchdog, freeze audit, dead gauges (recovery_in_progress, cooldown_active), update_timestamp_middleware.

---

## 5. Dependency Graph (Essential Production Path)

```
main
├── config, database, app.handlers
├── reminders, healthcheck, fast_expiry_cleanup, auto_renewal, health_server
├── admin_notifications, trial_notifications, activation_worker
├── crypto_payment_watcher (lazy import)
├── xray_sync (optional)
├── app.core: feature_flags, structured_logger, logging_config
├── app.core: concurrency_middleware, telegram_error_middleware
└── Bot, Dispatcher, start_polling

database ← vpn_utils, app.utils.retry, app.core.system_state, app.core.metrics
vpn_utils ← config, httpx, app.core.circuit_breaker, app.utils.retry
Workers ← database, app.core.pool_monitor, app.core.cooperative_yield, app.core.metrics
```

No reconcile, no cooldown module, no watchdog_heartbeats. Circuit breaker only for vpn_api. Single retry layer per HTTP call.

---

## Validation (Phase 8)

- `grep "reconcile"` in `.py`: **no matches**
- `grep "FREEZE_AUDIT"` in `.py`: **no matches**
- `grep "recovery_cooldown"` in `.py`: **no matches** (after comment removal)
- `cooldown` in code: only legitimate uses (alert spam, circuit breaker half-open, xray_sync interval)
- Lint: **no errors**
- **Note:** `database.py` is ~8.9k lines (over 1500); splitting was not done to avoid touching business logic. Can be a future refactor.
