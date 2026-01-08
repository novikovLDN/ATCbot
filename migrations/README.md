# Database Migrations

This directory contains versioned database schema migrations.

## Migration Files

Migration files must follow the naming pattern:
```
<number>_<description>.sql
```

Example:
- `001_init.sql` - Initial schema
- `002_add_balance.sql` - Add balance system
- `003_add_referrals.sql` - Add referral system

## Migration Rules

1. **Each migration is transactional** - if it fails, it rolls back completely
2. **Migrations are applied in order** - sorted by version number
3. **Applied migrations are recorded** in `schema_migrations` table
4. **Migrations are idempotent** - can be safely re-run (use `IF NOT EXISTS`, `ON CONFLICT`, etc.)

## Creating a New Migration

1. Create a new file: `migrations/009_<description>.sql`
2. Write SQL statements (use `IF NOT EXISTS` for safety)
3. Test the migration locally
4. Commit to repository

## Migration Format

```sql
-- Migration 009: Description
-- Brief explanation of what this migration does

-- SQL statements here
CREATE TABLE IF NOT EXISTS table_name (
    ...
);

-- Use IF NOT EXISTS for safety
ALTER TABLE table_name ADD COLUMN IF NOT EXISTS column_name TYPE;
```

## Important Notes

- **Always use `IF NOT EXISTS`** for CREATE statements
- **Always use `ADD COLUMN IF NOT EXISTS`** for ALTER TABLE
- **Use `ON CONFLICT DO NOTHING`** for INSERT statements
- **Test migrations** before deploying to production
- **Never modify existing migration files** - create new ones instead

