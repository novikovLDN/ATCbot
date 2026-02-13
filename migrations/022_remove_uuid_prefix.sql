-- UUID single source of truth: remove environment prefixes from subscriptions.uuid
-- Safe, idempotent: only affects prefixed UUIDs; raw UUIDs untouched.

UPDATE subscriptions
SET uuid = REPLACE(uuid, 'stage-', '')
WHERE uuid LIKE 'stage-%';

UPDATE subscriptions
SET uuid = REPLACE(uuid, 'prod-', '')
WHERE uuid LIKE 'prod-%';

UPDATE subscriptions
SET uuid = REPLACE(uuid, 'test-', '')
WHERE uuid LIKE 'test-%';
