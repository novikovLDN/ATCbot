refactor: extract full business logic into service layer

This commit introduces a comprehensive service layer architecture,
extracting all business logic from handlers into dedicated service modules.

## Service Layer Modules

### Created Services
- **subscriptions**: Subscription status, pricing, purchase lifecycle
- **trials**: Trial expiration, notification timing, completion logic
- **payments**: Payment verification, validation, idempotency checks
- **activation**: Activation retry logic, pending state management
- **notifications**: Reminder scheduling, notification windows, idempotency
- **admin**: User overview aggregation, admin action decisions
- **vpn**: VPN API availability, UUID removal decisions

### Key Changes

**Service Layer:**
- Moved all business logic from handlers.py into service modules
- Introduced domain exceptions for each service
- Added dataclasses for structured return types (SubscriptionStatus, AdminUserOverview, etc.)
- Services are pure business logic: no aiogram imports, no logging, no Telegram calls

**Handlers:**
- Reduced from 8000+ lines to thin orchestration layer
- Handlers now only: parse input, call services, format responses, send Telegram messages
- Removed all date calculations, status checks, and business decisions from handlers
- All business logic delegated to appropriate services

**Testing:**
- Added comprehensive unit tests for service layer
- Tests cover: subscription status, trial timing, payment validation, admin decisions
- All tests use mocks, no real database connections
- Tests are deterministic and focused on business logic

## Architecture Benefits

1. **Separation of Concerns**: Business logic isolated from Telegram API layer
2. **Testability**: Services can be tested independently without Telegram/DB dependencies
3. **Maintainability**: Business rules centralized in service layer
4. **Reusability**: Services can be used by handlers, background tasks, and future APIs
5. **Readability**: Handlers read like scenario scripts, services contain clear business rules

## Backward Compatibility

- ✅ 100% backward compatible
- ✅ No behavior changes
- ✅ No database schema changes
- ✅ No API changes
- ✅ All existing functionality preserved

## Files Changed

**New Service Modules:**
- app/services/subscriptions/service.py
- app/services/trials/service.py
- app/services/payments/service.py
- app/services/activation/service.py
- app/services/notifications/service.py
- app/services/admin/service.py
- app/services/vpn/service.py

**Refactored:**
- handlers.py (reduced complexity, delegated to services)
- auto_renewal.py (uses notification service)
- reminders.py (uses notification service)
- trial_notifications.py (uses trial service)
- fast_expiry_cleanup.py (uses VPN service)
- activation_worker.py (uses activation service)

**Tests:**
- tests/services/test_subscriptions.py
- tests/services/test_trials.py
- tests/services/test_payments.py
- tests/services/test_admin.py
- tests/conftest.py
- pytest.ini

**Dependencies:**
- requirements.txt (added pytest, pytest-asyncio)

## Migration Notes

No migration required. This is a pure code refactoring with no database
or API changes. All existing functionality works exactly as before.
