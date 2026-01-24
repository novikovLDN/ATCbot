# Data Locality Rules

This document defines data locality rules for multi-region architecture.

## Overview

Data is classified into three categories:
- **Global**: Replicated across regions, eventual consistency
- **Region-Local**: Stored only in originating region
- **Eventual-Consistent**: Replicated asynchronously

---

## Global Data

### Users

**Locality:** Global
**Source of Truth:** Primary region
**Replication:** Eventual consistency
**Access:** All regions can read, writes to primary only

**Fields:**
- `telegram_id` (primary key)
- `language`
- `balance`
- `created_at`
- `updated_at`

**Replication:**
- Writes: Primary region only
- Reads: All regions (may see stale data)
- Consistency: Eventual (within 1 minute)

### Subscriptions

**Locality:** Global
**Source of Truth:** Primary region
**Replication:** Eventual consistency
**Access:** All regions can read, writes to primary only

**Fields:**
- `id` (primary key)
- `user_id` (foreign key)
- `tariff_type`
- `period_days`
- `expires_at`
- `activation_status`
- `vpn_uuid`

**Replication:**
- Writes: Primary region only
- Reads: All regions (may see stale data)
- Consistency: Eventual (within 1 minute)

### Payments

**Locality:** Global
**Source of Truth:** Primary region
**Replication:** Eventual consistency
**Access:** All regions can read, writes to primary only

**Fields:**
- `id` (primary key)
- `user_id` (foreign key)
- `amount`
- `status`
- `payment_id`
- `created_at`

**Replication:**
- Writes: Primary region only
- Reads: All regions (may see stale data)
- Consistency: Eventual (within 1 minute)

### Admin Data

**Locality:** Global
**Source of Truth:** Primary region
**Replication:** Eventual consistency
**Access:** All regions can read, writes to primary only

**Fields:**
- Discounts
- VIP status
- Admin actions

**Replication:**
- Writes: Primary region only
- Reads: All regions (may see stale data)
- Consistency: Eventual (within 1 minute)

---

## Region-Local Data

### Logs

**Locality:** Region-Local
**Storage:** Region-local only
**Retention:** 90 days
**Access:** Region-local only

**Fields:**
- Application logs
- Error logs
- Access logs
- Audit logs (region-local copy)

**Replication:**
- No replication
- Region-local only
- Not accessible from other regions

### Metrics

**Locality:** Region-Local
**Storage:** Region-local only
**Retention:** 30 days
**Access:** Region-local only

**Fields:**
- System metrics
- Performance metrics
- Cost metrics
- Health metrics

**Replication:**
- No replication
- Region-local only
- Aggregated metrics may be exported (async)

### Background Worker State

**Locality:** Region-Local
**Storage:** Region-local only
**Retention:** In-memory only
**Access:** Region-local only

**Fields:**
- Worker iteration state
- Cooldown state
- Recovery state
- Warm-up state

**Replication:**
- No replication
- Region-local only
- Not accessible from other regions

---

## Eventual-Consistent Data

### Analytics

**Locality:** Eventual-Consistent
**Storage:** Region-local + Global (async)
**Retention:** 1 year
**Access:** Region-local (real-time), Global (async)

**Fields:**
- Event data (PII-safe)
- Aggregated metrics
- Business intelligence data

**Replication:**
- Async replication to global
- Delay: 5-15 minutes
- Consistency: Eventual

### Audit Trails

**Locality:** Eventual-Consistent
**Storage:** Region-local + Global (async)
**Retention:** 7 years (payments), 1 year (others)
**Access:** Region-local (real-time), Global (async)

**Fields:**
- Payment events
- Subscription lifecycle
- Admin actions
- System degradation

**Replication:**
- Async replication to global
- Delay: 1-5 minutes
- Consistency: Eventual

---

## Data Consistency Guarantees

### Strong Consistency

**Required For:**
- Payment finalization
- Subscription creation
- Balance updates
- Admin actions

**Guarantee:**
- All reads see latest write
- No stale data
- Primary region only

### Eventual Consistency

**Allowed For:**
- User profile reads
- Subscription status reads
- Payment history reads
- Analytics queries

**Guarantee:**
- Reads may see stale data (up to 1 minute)
- Consistency within 1 minute
- Acceptable for non-critical operations

### No Consistency

**Allowed For:**
- Logs
- Metrics
- Background worker state

**Guarantee:**
- Region-local only
- No cross-region access
- No consistency requirements

---

## Write Rules

### Primary Region

**Allowed:**
- ✅ All writes
- ✅ All reads
- ✅ All operations

**Restrictions:**
- None

### Secondary Regions

**Allowed:**
- ✅ Read operations
- ❌ Write operations (disabled)

**Restrictions:**
- No writes to global data
- Read-only mode
- Analytics writes allowed (region-local)

### Read-Only Mode

**Allowed:**
- ✅ All reads
- ❌ All writes (disabled)

**Restrictions:**
- No writes to any data
- Read-only operations only
- Background workers disabled

---

## Replication Strategy

### Synchronous Replication

**Not Used:**
- Too expensive
- Too slow
- Not required for our use case

### Asynchronous Replication

**Used For:**
- Analytics data
- Audit trails
- Aggregated metrics

**Delay:**
- 1-15 minutes
- Acceptable for analytics
- Not acceptable for transactions

### No Replication

**Used For:**
- Logs
- Metrics
- Background worker state

**Reason:**
- Region-local only
- No cross-region access needed
- Reduces complexity

---

## Notes

- ⚠️ **Primary region is source of truth** for global data
- ⚠️ **Secondary regions are read-only** for global data
- ⚠️ **Eventual consistency is acceptable** for non-critical reads
- ⚠️ **Strong consistency required** for critical operations
- ⚠️ **Region-local data is not replicated** - Region loss = data loss
