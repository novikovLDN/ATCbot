# Refactoring Plan & Enterprise Architecture Target

## 1. PRIORITIZED REFACTORING PLAN

### P0 — CRITICAL (Already Fixed)

All critical issues from audits have been fixed:
- ✅ Callback handler coverage (all `admin:notify:*` patterns)
- ✅ Auto-renewal UTC consistency
- ✅ Healthcheck alert spam protection
- ✅ Expired subscriptions marked correctly when VPN_API disabled
- ✅ Crypto payment watcher degraded mode handling
- ✅ Fast expiry cleanup VPN_API disabled handling

**Status:** Complete, verified in production

---

### P1 — HIGH PRIORITY (Weeks 1-3)

#### P1.1: Extract DB Recovery Logic

**Action:**
1. Create `app/core/db_recovery.py`
2. Move `retry_db_init()` from `main.py:170-261` to `retry_db_initialization(bot, task_manager)`
3. Move task recovery logic (lines 222-236) to `task_manager.recover_tasks()`
4. Update `main.py` to import and call

**Files:** `app/core/db_recovery.py` (NEW), `main.py`  
**Effort:** 2-3 hours  
**Impact:** Separates business logic from bootstrap, testable

---

#### P1.2: Create TaskManager

**Action:**
1. Create `app/core/task_manager.py` with `TaskManager` class:
   - `start_task(name, coro, requires_db=True) -> Optional[Task]`
   - `recover_tasks(bot) -> None`
   - `cleanup_all() -> None`
2. Refactor `main.py:125-314` (task creation) to use TaskManager
3. Refactor `main.py:336-418` (cleanup) to use `TaskManager.cleanup_all()`

**Files:** `app/core/task_manager.py` (NEW), `main.py`  
**Effort:** 3-4 hours  
**Impact:** Eliminates 200+ lines of repetitive code

---

#### P1.3: Extract Admin Handlers

**Action:**
1. Create `app/handlers/admin.py`
2. Move admin handlers (~3000 lines) from `handlers.py`:
   - Admin FSM states (`AdminGrantAccess`, `AdminRevokeAccess`)
   - Admin callback handlers (`callback_admin_*`)
   - Admin helper functions (`get_admin_*_keyboard`)
3. Move admin-related imports
4. Update `handlers.py` router: `router.include_router(admin_router)`

**Files:** `app/handlers/admin.py` (NEW), `handlers.py`  
**Effort:** 4-6 hours  
**Impact:** Reduces `handlers.py` from 10539 to ~7500 lines

---

#### P1.4: Integrate Health Check with Alert Rules

**Action:**
1. In `healthcheck.py`, replace `send_health_alert()` with `app/core/alerts.py`
2. Use `AlertRules.evaluate_all_rules(system_state)` in `health_check_task()`
3. Remove `send_health_alert()` function (lines 318-333)
4. Use `send_alert()` from `app/core/alerts.py`

**Files:** `healthcheck.py`  
**Effort:** 2-3 hours  
**Impact:** Unified alerting, better spam protection

---

### P2 — MEDIUM PRIORITY (Weeks 4-12)

#### P2.1: Create Bootstrap Helpers

**Action:**
1. Create `app/core/bootstrap.py` with:
   - `initialize_database(bot) -> bool`
   - `setup_all_tasks(bot, task_manager) -> None`
2. Extract `main.py:95-314` to bootstrap helpers
3. Update `main.py` to call helpers

**Files:** `app/core/bootstrap.py` (NEW), `main.py`  
**Effort:** 2-3 hours

---

#### P2.2: Make Handlers Thin Controllers

**Action:**
1. Identify business logic in handlers (validation, calculations)
2. Extract to `app/services/` (create new services as needed)
3. Refactor handlers to call services, format responses only

**Files:** `handlers.py`, `app/handlers/admin.py`, `app/services/`  
**Effort:** 8-12 hours (incremental)

---

#### P2.3: Extract Payment Handlers

**Action:**
1. Create `app/handlers/payments.py`
2. Move payment handlers (~500 lines) from `handlers.py`
3. Update router: `handlers.py` includes `payments_router`

**Files:** `handlers.py` → `app/handlers/payments.py`  
**Effort:** 2-3 hours

---

#### P2.4: Extract User Handlers

**Action:**
1. Create `app/handlers/user.py`
2. Move user handlers (profile, menu, etc., ~1000 lines) from `handlers.py`
3. Update router: `handlers.py` includes `user_router`

**Files:** `handlers.py` → `app/handlers/user.py`  
**Effort:** 2-3 hours

---

## 2. SHORT-TERM STABILIZATION CHECKLIST

### Week 1: Verification

- [ ] **Deploy P0 fixes and verify**
  - [ ] Test all `admin:notify:*` callbacks
  - [ ] Test auto_renewal (UTC consistency)
  - [ ] Test healthcheck (1 alert/hour max)
  - [ ] Test expired subscriptions (VPN_API disabled)

- [ ] **Monitor production (48h)**
  - [ ] No `ValueError: invalid literal for int()`
  - [ ] No `Unhandled callback_query`
  - [ ] No alert spam
  - [ ] No timezone issues

- [ ] **Verify incident lifecycle**
  - [ ] Incident starts on unavailable
  - [ ] Incident clears on recovery
  - [ ] Correlation IDs in logs

---

### Week 2: P1 Refactoring

- [ ] **P1.1: DB Recovery**
  - [ ] Create `app/core/db_recovery.py`
  - [ ] Move `retry_db_init()` from `main.py:170-261`
  - [ ] Update `main.py` imports
  - [ ] Test DB recovery

- [ ] **P1.2: TaskManager**
  - [ ] Create `app/core/task_manager.py`
  - [ ] Refactor `main.py:125-314` (task creation)
  - [ ] Refactor `main.py:336-418` (cleanup)
  - [ ] Test task lifecycle

- [ ] **P1.4: Health Check Alerts**
  - [ ] Replace `send_health_alert()` with `app/core/alerts.py`
  - [ ] Test spam protection
  - [ ] Verify severity

---

### Week 3: Handler Extraction

- [ ] **P1.3: Admin Handlers**
  - [ ] Create `app/handlers/admin.py`
  - [ ] Move admin handlers (~3000 lines)
  - [ ] Move FSM states
  - [ ] Update router
  - [ ] Test admin flows

---

### Week 4: Testing

- [ ] **E2E Testing**
  - [ ] Admin flows (grant, revoke, dashboard)
  - [ ] Payment flows
  - [ ] Auto-renewal
  - [ ] Degraded mode

- [ ] **Performance**
  - [ ] Handler response times (no regression)
  - [ ] Worker iteration times (no regression)
  - [ ] Memory usage (stable)

- [ ] **Documentation**
  - [ ] Update README (module structure)
  - [ ] Document handler organization

---

## 3. LONG-TERM ENTERPRISE ARCHITECTURE TARGET

### Target Structure (6-12 months)

```
atcs/
├── app/
│   ├── core/                    # Infrastructure
│   │   ├── system_state.py     # ✅
│   │   ├── metrics.py          # ✅
│   │   ├── alerts.py           # ✅
│   │   ├── bootstrap.py        # P2.1
│   │   ├── db_recovery.py      # P1.1
│   │   └── task_manager.py    # P1.2
│   │
│   ├── handlers/                # Handler modules
│   │   ├── __init__.py         # Router aggregation
│   │   ├── admin.py            # P1.3
│   │   ├── payments.py        # P2.3
│   │   ├── user.py             # P2.4
│   │   └── common.py           # Shared utilities
│   │
│   ├── services/                # Business logic
│   │   ├── activation/         # ✅
│   │   ├── notifications/      # ✅
│   │   ├── vpn/                # ✅
│   │   ├── payments/          # ✅
│   │   └── subscriptions/     # NEW
│   │
│   ├── workers/                # Background tasks
│   │   ├── activation_worker.py # ✅
│   │   ├── auto_renewal.py     # ✅
│   │   ├── crypto_watcher.py   # ✅
│   │   ├── expiry_cleanup.py   # ✅
│   │   └── reminders.py        # ✅
│   │
│   └── utils/                  # Utilities
│       ├── security.py        # ✅
│       ├── audit.py            # ✅
│       └── logging_helpers.py  # ✅
│
├── database.py                 # Database layer
├── handlers.py                 # Legacy (deprecated)
├── main.py                     # Bootstrap only
└── config.py                   # Configuration
```

### Architecture Principles

1. **Separation:** Handlers (routing) → Services (logic) → Database (data)
2. **Modularity:** Single responsibility per module
3. **Testability:** Business logic in services
4. **Observability:** Structured logging, metrics, alerts

### Migration Phases

**Phase 1 (Weeks 1-2):** Core infrastructure (P1.1, P1.2, P1.4)  
**Phase 2 (Weeks 3-4):** Handler modularization (P1.3, P2.3, P2.4)  
**Phase 3 (Weeks 5-8):** Service layer (P2.2)  
**Phase 4 (Weeks 9-12):** Cleanup (deprecate legacy)

---

## 4. SUCCESS METRICS

### Short-term (1 month)
- [ ] Zero production incidents from refactored code
- [ ] All P1 items completed
- [ ] Handler modularization complete
- [ ] No performance regression

### Long-term (6 months)
- [ ] All handlers in dedicated modules
- [ ] Business logic in service layer
- [ ] `handlers.py` deprecated
- [ ] Clear module boundaries

---

## 5. RISK MITIGATION

### Deployment Strategy
1. Feature flags for new modules
2. Gradual rollout (one module at a time)
3. Enhanced logging during transition
4. Keep legacy code until stable

### Rollback Plan
- Git revert for each module
- Feature flags to disable new code
- Legacy code remains until validation complete

---

## 6. ESTIMATED EFFORT

**P1 (Weeks 1-3):** 11-16 hours
- P1.1: 2-3h
- P1.2: 3-4h
- P1.3: 4-6h
- P1.4: 2-3h

**P2 (Weeks 4-12):** 14-21 hours
- P2.1: 2-3h
- P2.2: 8-12h (incremental)
- P2.3: 2-3h
- P2.4: 2-3h

**Testing & Documentation:** 8-10 hours

**Total:** 33-47 hours (4-6 weeks)

---

## 7. DEPENDENCIES

**P1:** All items can be done in parallel (no dependencies)

**P2:**
- P2.1 can use P1.2 (TaskManager)
- P2.2 should follow P1.3 (Admin Handlers)
- P2.3, P2.4: No dependencies
