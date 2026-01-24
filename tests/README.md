# Service Layer Unit Tests

This directory contains unit tests for the service layer of the ATCS bot.

## Structure

- `tests/services/` - Service layer tests
  - `test_subscriptions.py` - Subscription service tests
  - `test_trials.py` - Trial service tests
  - `test_payments.py` - Payment service tests
  - `test_admin.py` - Admin service tests

## Running Tests

```bash
# Install dependencies
pip install -r requirements.txt

# Run all tests
pytest

# Run specific test file
pytest tests/services/test_subscriptions.py

# Run with verbose output
pytest -v

# Run with coverage
pytest --cov=app/services
```

## Test Philosophy

- **Focus on business logic**: Tests verify decision-making, not infrastructure
- **Mock database calls**: No real database connections in unit tests
- **Deterministic**: All tests use fixed timestamps and mock data
- **Meaningful names**: Test names clearly describe what is being tested
- **Edge cases**: Tests cover expired, pending, invalid states

## What's Tested

### Subscription Service
- `parse_expires_at()` - Date parsing from various formats
- `is_subscription_active()` - Active/inactive subscription checks
- `get_subscription_status()` - Comprehensive status determination

### Trial Service
- `is_trial_expired()` - Trial expiration checks
- `calculate_trial_timing()` - Timing calculations
- Notification schedule configuration

### Payment Service
- `verify_payment_payload()` - Payload verification and parsing
- `validate_payment_amount()` - Amount validation
- `check_payment_idempotency()` - Idempotency checks

### Admin Service
- `get_admin_user_overview()` - User data aggregation
- `get_admin_user_actions()` - Action availability decisions

## What's NOT Tested

- Database integration (use integration tests)
- Telegram API calls (use integration tests)
- Complex async flows requiring database connections
- Handler layer (separate test suite)
