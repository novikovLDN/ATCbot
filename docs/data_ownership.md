# Data Ownership & Analytics Foundation

This document defines data ownership, source of truth, and analytics readiness.

## Data Ownership

### User Data

**Source of Truth:**
- `users` table in PostgreSQL
- Primary key: `telegram_id`

**Who Can Write:**
- User creation: System (on first interaction)
- User updates: System (on profile changes)
- User deletion: System (on user request) + Admin

**Who Can Read:**
- Services: All services can read user data
- Handlers: Can read for user operations
- Admin: Can read for admin operations
- Analytics: Can read aggregated data (PII-safe)

**Retention Policy:**
- Active users: Indefinite
- Inactive users: 2 years
- Deleted users: 30 days (soft delete)

### Subscription Data

**Source of Truth:**
- `subscriptions` table in PostgreSQL
- Primary key: `id`
- Foreign key: `user_id` → `users.telegram_id`

**Who Can Write:**
- Subscription creation: `subscription_service`
- Subscription activation: `activation_service`
- Subscription expiry: `fast_expiry_cleanup` (background worker)
- Subscription renewal: `subscription_service`

**Who Can Read:**
- Services: All services can read subscription data
- Handlers: Can read for user operations
- Admin: Can read for admin operations
- Analytics: Can read aggregated data

**Retention Policy:**
- Active subscriptions: Indefinite
- Expired subscriptions: 1 year
- Cancelled subscriptions: 1 year

### Payment Data

**Source of Truth:**
- `payments` table in PostgreSQL
- Primary key: `id`
- Foreign key: `user_id` → `users.telegram_id`

**Who Can Write:**
- Payment creation: `payment_service`
- Payment finalization: `payment_service`
- Payment status updates: `payment_service` + Payment provider webhooks

**Who Can Read:**
- Services: `payment_service`, `subscription_service`
- Handlers: Can read for user operations
- Admin: Can read for admin operations
- Analytics: Can read aggregated data (amounts, dates, no PII)

**Retention Policy:**
- All payments: 7 years (compliance requirement)
- Failed payments: 1 year

### VPN Data

**Source of Truth:**
- `subscriptions.vpn_uuid` in PostgreSQL
- VPN API (Xray Core) for active keys

**Who Can Write:**
- UUID creation: `vpn_service` → VPN API
- UUID removal: `vpn_service` → VPN API
- UUID updates: `vpn_service` → VPN API

**Who Can Read:**
- Services: `vpn_service`, `activation_service`
- Handlers: Can read for user operations
- Admin: Can read for admin operations
- Analytics: Can read aggregated data (counts, no UUIDs)

**Retention Policy:**
- Active UUIDs: Indefinite
- Removed UUIDs: Not stored (removed from VPN API)

### Admin Data

**Source of Truth:**
- `user_discounts` table in PostgreSQL
- `vip_users` table in PostgreSQL
- Admin actions in audit logs

**Who Can Write:**
- Discounts: Admin only
- VIP status: Admin only
- Admin actions: Admin only

**Who Can Read:**
- Services: `admin_service`
- Handlers: Admin handlers only
- Admin: Admin only
- Analytics: Can read aggregated data (counts, no user IDs)

**Retention Policy:**
- Active discounts: Indefinite
- Expired discounts: 1 year
- VIP status: Indefinite
- Admin actions: 7 years (compliance requirement)

---

## Analytics Readiness

### Event Taxonomy

**Event Types:**
- User lifecycle: `USER_CREATED`, `USER_UPDATED`, `USER_DELETED`
- Subscription lifecycle: `SUBSCRIPTION_CREATED`, `SUBSCRIPTION_ACTIVATED`, `SUBSCRIPTION_EXPIRED`, `SUBSCRIPTION_RENEWED`
- Payment lifecycle: `PAYMENT_INITIATED`, `PAYMENT_COMPLETED`, `PAYMENT_FAILED`
- VPN lifecycle: `VPN_KEY_CREATED`, `VPN_KEY_REMOVED`
- Admin actions: `ADMIN_VIP_GRANTED`, `ADMIN_DISCOUNT_CREATED`
- System state: `SYSTEM_DEGRADED`, `SYSTEM_RECOVERED`

**Event Format:**
- `event_type`: EventType enum
- `entity_id`: ID of the entity
- `timestamp`: UTC timestamp
- `correlation_id`: UUID for correlation
- `metadata`: PII-safe metadata

### Analytical Slices

**Churn Analysis:**
- Events: `SUBSCRIPTION_EXPIRED`, `SUBSCRIPTION_CANCELLED`, `USER_DELETED`
- Dimensions: Time period, subscription type, user segment
- Metrics: Churn rate, retention rate, lifetime value

**Conversion Analysis:**
- Events: `TRIAL_STARTED`, `SUBSCRIPTION_CREATED`, `PAYMENT_COMPLETED`
- Dimensions: Time period, traffic source, user segment
- Metrics: Conversion rate, trial-to-paid rate, revenue

**Renewal Analysis:**
- Events: `SUBSCRIPTION_RENEWED`, `PAYMENT_COMPLETED`
- Dimensions: Time period, subscription type, user segment
- Metrics: Renewal rate, average renewal value, renewal timing

**LTV (Lifetime Value) Analysis:**
- Events: `PAYMENT_COMPLETED`, `SUBSCRIPTION_RENEWED`
- Dimensions: User segment, subscription type, acquisition channel
- Metrics: Average LTV, LTV by segment, LTV trends

**Failure Rates:**
- Events: `PAYMENT_FAILED`, `SUBSCRIPTION_EXPIRED`, `SYSTEM_DEGRADED`
- Dimensions: Time period, component, error type
- Metrics: Failure rate, error distribution, recovery time

**Retry Amplification:**
- Events: `PAYMENT_FAILED`, `VPN_KEY_CREATED` (may indicate retries)
- Dimensions: Time period, operation type, retry count
- Metrics: Retry rate, retry amplification factor, cost impact

---

## GDPR / Data Minimization

### PII Identification

**Personal Identifiable Information (PII):**
- Telegram ID (user identifier)
- Payment amounts (financial data)
- Subscription dates (temporal data)
- VPN UUIDs (access credentials)

**Non-PII:**
- Aggregated metrics
- System state
- Error codes
- Timestamps (without user context)

### Masking Rules

**Logs:**
- Telegram IDs: Logged (required for operations)
- Payment amounts: Logged (required for operations)
- VPN UUIDs: First 8 characters only in logs
- Payment tokens: `[REDACTED]`
- Card numbers: Never logged

**Events:**
- All PII masked in events
- Only aggregated data in analytics
- Correlation IDs for linking (no PII)

**Audit Trails:**
- User IDs: Logged (required for audit)
- Admin actions: Logged (required for audit)
- Sensitive fields: `[REDACTED]`

### Deletion Capability

**User Data Deletion:**
- Soft delete: Mark as deleted, retain for 30 days
- Hard delete: Remove after 30 days
- Cascade: Delete subscriptions, payments (if allowed by law)

**Subscription Data Deletion:**
- Soft delete: Mark as expired/cancelled
- Hard delete: Remove after 1 year
- Cascade: Remove VPN UUIDs

**Payment Data Deletion:**
- Retention: 7 years (compliance requirement)
- Deletion: After 7 years, if requested

### Audit Evidence

**What is Audited:**
- Payment events (required)
- Subscription lifecycle (required)
- Admin actions (required)
- System degradation (required)
- Security events (required)

**Retention:**
- Payment events: 7 years
- Subscription events: 1 year
- Admin actions: 7 years
- System degradation: 90 days
- Security events: 7 years

---

## Data Flow

### Write Path

1. **Handler** → Validates input
2. **Service** → Business logic
3. **Database** → Persists data
4. **Event** → Emits event (if applicable)

### Read Path

1. **Handler/Service** → Queries database
2. **Database** → Returns data
3. **Service** → Processes data
4. **Handler** → Returns response

### Analytics Path

1. **Events** → Collected from services
2. **Aggregation** → PII-safe aggregation
3. **Analytics** → Business intelligence queries
4. **Reports** → Generated reports

---

## Notes

- ⚠️ **Source of truth** is always database
- ⚠️ **No PII in analytics** - Only aggregated data
- ⚠️ **Retention policies** must be enforced
- ⚠️ **Deletion requests** must be honored (GDPR)
