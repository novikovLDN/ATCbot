-- UUID single source of truth: remove stage- prefix from subscriptions.uuid
-- Safe, idempotent: only affects prefixed UUIDs; raw UUIDs untouched.
-- Run once. substring(uuid from 7) removes 'stage-' (6 chars + 1 = from position 7).

UPDATE subscriptions
SET uuid = substring(uuid from 7)
WHERE uuid LIKE 'stage-%';
