# Migration 025: Full TIMESTAMPTZ Alignment

## Purpose

Convert all remaining `TIMESTAMP WITHOUT TIME ZONE` columns to `TIMESTAMPTZ` across the schema. Ensures consistent UTC handling, eliminates DST/clock drift risks, and aligns with production audit requirements.

## Prerequisites

- Migration 024 must be applied first (critical billing columns).
- Database session timezone should be UTC.

## Tables and Columns Converted

| Table | Columns |
|-------|---------|
| users | created_at |
| vpn_keys | assigned_at |
| audit_log | created_at |
| subscription_history | start_date, end_date, created_at |
| broadcasts | created_at |
| broadcast_log | sent_at |
| incident_settings | updated_at |
| user_discounts | expires_at, created_at |
| vip_users | granted_at |
| promo_codes | created_at, expires_at, deleted_at |
| promo_usage_logs | created_at |
| referral_rewards | created_at |
| balance_transactions | created_at |
| withdrawal_requests | created_at, processed_at |
| admin_broadcasts | created_at |
| subscriptions | last_notification_sent_at, first_traffic_at |

## Conversion Logic

```sql
ALTER TABLE table_name
ALTER COLUMN column_name
TYPE TIMESTAMPTZ
USING column_name AT TIME ZONE 'UTC';
```

Existing naive timestamps are interpreted as UTC during conversion.

## Rollback

TIMESTAMPTZ → TIMESTAMP rollback is non-trivial (timezone info lost). Recommend forward-only. If required:

```sql
ALTER TABLE table_name
ALTER COLUMN column_name
TYPE TIMESTAMP
USING column_name AT TIME ZONE 'UTC';
```

## Lock Duration

Per-column `ALTER`; no extended full-table lock. Concurrent reads/writes allowed.

## Verification

After migration:
```sql
SELECT table_name, column_name, data_type
FROM information_schema.columns
WHERE table_schema = 'public'
AND data_type LIKE '%timestamp%'
ORDER BY table_name, column_name;
```
All datetime columns should show `timestamp with time zone`.
