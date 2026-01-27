# main.py Analysis & Refactoring Suggestions

## Executive Summary

**File Size:** 431 lines  
**Bootstrap Logic:** ~150 lines (minimal but could be cleaner)  
**Business Logic Found:** 3 areas that should be extracted  
**Critical Issues:** 0  
**Refactoring Opportunities:** 3 safe extractions suggested

---

## 1. Bootstrap Logic Analysis

### ✅ Current Bootstrap Logic (Lines 69-431)

**Structure:**
1. Configuration logging (lines 74-77)
2. Bot/Dispatcher initialization (lines 80-84)
3. DB initialization (lines 95-123)
4. Task creation (lines 125-314)
5. Telegram polling (lines 331-334)
6. Cleanup (lines 336-422)

**Assessment:**
- ✅ **Minimal:** Core bootstrap is minimal (bot init, DB init, polling)
- ⚠️ **Deterministic:** Mostly deterministic, but task recovery logic adds complexity
- ⚠️ **Repetitive:** Task creation/cleanup code is very repetitive

### Issues:

1. **Repetitive Task Management (Lines 125-314, 336-418):**
   - Same pattern repeated 8+ times: check `DB_READY`, create task, log
   - Same cleanup pattern repeated 8+ times: cancel, await, handle CancelledError
   - Could be abstracted into helper functions

2. **Nested Function (Lines 170-261):**
   - `retry_db_init()` is 91 lines long
   - Contains business logic for DB recovery
   - Contains business logic for task recovery
   - Should be extracted to a separate module

---

## 2. Business Logic That Should Not Live in main.py

### ❌ Issue 1: DB Recovery Logic

**Location:** Lines 170-261 (`retry_db_init()` nested function)

**Problem:**
- 91 lines of business logic for DB recovery
- Contains task recovery logic (lines 222-236)
- Contains admin notification logic (lines 216-219)
- Should be in `app/core/bootstrap.py` or `app/core/db_recovery.py`

**Current Code:**
```python
async def retry_db_init():
    """91 lines of DB recovery business logic"""
    # ... DB retry logic ...
    # ... Task recovery logic ...
    # ... Admin notification logic ...
```

**Suggested Extraction:**
- Extract to `app/core/db_recovery.py`:
  - `async def retry_db_initialization(bot: Bot, task_manager: TaskManager) -> None`
  - Handles DB retry, task recovery, admin notifications

---

### ❌ Issue 2: Task Recovery Logic

**Location:** Lines 222-236 (inside `retry_db_init()`)

**Problem:**
- Business logic for recovering tasks after DB recovery
- Hardcoded task names and recovery logic
- Should be abstracted into a TaskManager class

**Current Code:**
```python
if reminder_task is None and recovered_tasks["reminder"] is None:
    recovered_tasks["reminder"] = asyncio.create_task(reminders.reminders_task(bot))
    logger.info("Reminders task started (recovered)")
# ... repeated 4 times ...
```

**Suggested Extraction:**
- Create `app/core/task_manager.py`:
  - `class TaskManager` with methods:
    - `start_task(name, coro) -> asyncio.Task`
    - `recover_tasks(bot) -> None`
    - `cleanup_all() -> None`

---

### ⚠️ Issue 3: Admin Notification Calls

**Location:** Lines 93, 108-111, 118-121, 216-219

**Problem:**
- Direct calls to `admin_notifications` in bootstrap
- While minimal, these are business logic calls
- Could be abstracted into bootstrap helpers

**Current Code:**
```python
admin_notifications.reset_notification_flags()  # Line 93
await admin_notifications.notify_admin_degraded_mode(bot)  # Lines 109, 119
await admin_notifications.notify_admin_recovered(bot)  # Line 217
```

**Assessment:**
- ✅ **Acceptable:** These are minimal and part of bootstrap flow
- ⚠️ **Could be extracted:** To `app/core/bootstrap.py` helpers if desired

---

## 3. Repetitive Code Patterns

### Pattern 1: Task Creation (Repeated 8+ times)

**Current Pattern:**
```python
task_name_task = None
if database.DB_READY:
    task_name_task = asyncio.create_task(module.task_function(bot))
    logger.info("Task name task started")
else:
    logger.warning("Task name task skipped (DB not ready)")
```

**Suggested Helper:**
```python
# In app/core/task_manager.py
async def create_task_if_ready(
    name: str,
    coro: Coroutine,
    requires_db: bool = True
) -> Optional[asyncio.Task]:
    """Create task if conditions are met"""
    if requires_db and not database.DB_READY:
        logger.warning(f"{name} task skipped (DB not ready)")
        return None
    task = asyncio.create_task(coro)
    logger.info(f"{name} task started")
    return task
```

---

### Pattern 2: Task Cleanup (Repeated 8+ times)

**Current Pattern:**
```python
if task:
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
```

**Suggested Helper:**
```python
# In app/core/task_manager.py
async def cancel_and_await(task: Optional[asyncio.Task]) -> None:
    """Cancel task and await cancellation"""
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
```

---

## 4. Suggested Safe Extractions (No Refactor Now)

### Extraction 1: DB Recovery Module

**File:** `app/core/db_recovery.py` (NEW)

**Functions:**
- `async def retry_db_initialization(bot: Bot, task_manager: TaskManager) -> None`
  - Extracted from `retry_db_init()` nested function
  - Handles DB retry loop
  - Calls `task_manager.recover_tasks()` after recovery

**Benefits:**
- Separates business logic from bootstrap
- Testable in isolation
- Reusable if needed

**Migration Path:**
1. Create `app/core/db_recovery.py`
2. Move `retry_db_init()` logic there
3. Update `main.py` to import and call

---

### Extraction 2: Task Manager

**File:** `app/core/task_manager.py` (NEW)

**Class:**
```python
class TaskManager:
    def __init__(self):
        self.tasks: Dict[str, Optional[asyncio.Task]] = {}
        self.recovered_tasks: Dict[str, Optional[asyncio.Task]] = {}
    
    async def start_task(
        self,
        name: str,
        coro: Coroutine,
        requires_db: bool = True
    ) -> Optional[asyncio.Task]:
        """Start task if conditions are met"""
        ...
    
    async def recover_tasks(self, bot: Bot) -> None:
        """Recover tasks after DB recovery"""
        ...
    
    async def cleanup_all(self) -> None:
        """Cancel and await all tasks"""
        ...
```

**Benefits:**
- Eliminates repetitive code
- Centralized task management
- Easier to test and maintain

**Migration Path:**
1. Create `app/core/task_manager.py`
2. Refactor task creation to use TaskManager
3. Refactor cleanup to use TaskManager.cleanup_all()

---

### Extraction 3: Bootstrap Helpers

**File:** `app/core/bootstrap.py` (NEW)

**Functions:**
- `async def initialize_database(bot: Bot) -> bool`
  - Extracted from lines 95-123
  - Returns True if DB ready, False otherwise
  - Handles admin notifications internally

- `async def setup_all_tasks(bot: Bot, task_manager: TaskManager) -> None`
  - Extracted from lines 125-314
  - Creates all background tasks
  - Uses TaskManager for consistency

**Benefits:**
- Cleaner main() function
- Testable bootstrap logic
- Reusable bootstrap helpers

**Migration Path:**
1. Create `app/core/bootstrap.py`
2. Move initialization logic there
3. Update `main.py` to call helpers

---

## 5. Current Bootstrap Flow (Deterministic Check)

### ✅ Deterministic Elements:

1. **Configuration Logging (Lines 74-77):**
   - ✅ Deterministic: Always logs same info
   - ✅ No side effects

2. **Bot/Dispatcher Init (Lines 80-84):**
   - ✅ Deterministic: Always creates bot/dispatcher
   - ✅ No business logic

3. **DB Initialization (Lines 95-123):**
   - ⚠️ **Mostly deterministic:** Always attempts init
   - ⚠️ **Side effects:** Admin notifications, DB_READY flag
   - ✅ **Safe:** Errors don't crash bootstrap

4. **Task Creation (Lines 125-314):**
   - ⚠️ **Deterministic:** Always creates same tasks if DB_READY
   - ⚠️ **Side effects:** Background tasks started
   - ✅ **Safe:** Tasks are optional (can skip if DB not ready)

5. **Polling (Lines 331-334):**
   - ✅ Deterministic: Always starts polling
   - ✅ No business logic

### ⚠️ Non-Deterministic Elements:

1. **DB Retry Task (Lines 170-261):**
   - ⚠️ **Non-deterministic:** Only created if DB not ready
   - ⚠️ **Side effects:** May recover tasks later
   - ✅ **Safe:** Doesn't affect bootstrap determinism

**Conclusion:** Bootstrap is mostly deterministic. The only non-deterministic part is the DB retry task, which is acceptable for recovery purposes.

---

## 6. Summary of Findings

### ✅ Bootstrap Logic: ACCEPTABLE
- Minimal core bootstrap (bot init, DB init, polling)
- Mostly deterministic
- Safe error handling

### ❌ Business Logic in main.py: 3 AREAS
1. **DB Recovery Logic (91 lines)** - Should be extracted
2. **Task Recovery Logic (15 lines)** - Should be extracted
3. **Admin Notifications (4 calls)** - Acceptable but could be abstracted

### ⚠️ Repetitive Code: 2 PATTERNS
1. **Task Creation** - Repeated 8+ times
2. **Task Cleanup** - Repeated 8+ times

### 📋 Suggested Extractions (Safe, No Breaking Changes):

1. **`app/core/db_recovery.py`** - DB recovery business logic
2. **`app/core/task_manager.py`** - Task management abstraction
3. **`app/core/bootstrap.py`** - Bootstrap helpers (optional)

---

## 7. Refactoring Priority

### High Priority (Safe, High Impact):
1. **Task Manager** - Eliminates most repetitive code
2. **DB Recovery Module** - Separates business logic

### Medium Priority (Nice to Have):
3. **Bootstrap Helpers** - Cleaner main() but less critical

### Low Priority (Optional):
4. **Admin Notification Abstraction** - Minimal benefit

---

## 8. No Critical Issues Found

**Assessment:**
- ✅ Bootstrap logic is minimal and mostly deterministic
- ✅ No business logic that breaks correctness
- ✅ Error handling is safe
- ✅ No infinite loops or race conditions
- ⚠️ Code could be cleaner with suggested extractions

**Recommendation:**
- No code changes needed unless refactoring
- Suggested extractions are safe and improve maintainability
- All extractions preserve backward compatibility
