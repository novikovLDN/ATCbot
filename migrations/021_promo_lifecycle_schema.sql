-- Migration 021: Production-grade promo lifecycle with safe recreation
-- Enables: deleted_at, is_active, partial unique index for active promos only
-- Allows recreating promo with same code after deletion/expiration/usage exhaustion

-- 1. Add id column (SERIAL) for primary key
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'promo_codes' AND column_name = 'id'
    ) THEN
        ALTER TABLE promo_codes ADD COLUMN id SERIAL;
        -- Populate id for existing rows
        UPDATE promo_codes SET id = nextval('promo_codes_id_seq') WHERE id IS NULL;
    END IF;
END $$;

-- 2. Add deleted_at column
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'promo_codes' AND column_name = 'deleted_at'
    ) THEN
        ALTER TABLE promo_codes ADD COLUMN deleted_at TIMESTAMP NULL;
    END IF;
END $$;

-- 3. Ensure is_active exists (it should from 001_init)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'promo_codes' AND column_name = 'is_active'
    ) THEN
        ALTER TABLE promo_codes ADD COLUMN is_active BOOLEAN DEFAULT true;
    END IF;
END $$;

-- 4. Drop primary key on code (allows multiple rows with same code for recreation)
DO $$
DECLARE
    pk_name TEXT;
BEGIN
    SELECT conname INTO pk_name
    FROM pg_constraint
    WHERE conrelid = 'promo_codes'::regclass
      AND contype = 'p'
      AND conname IS NOT NULL
    LIMIT 1;
    IF pk_name IS NOT NULL AND pk_name = 'promo_codes_pkey' THEN
        ALTER TABLE promo_codes DROP CONSTRAINT promo_codes_pkey;
    ELSIF pk_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE promo_codes DROP CONSTRAINT %I', pk_name);
    END IF;
END $$;

-- 5. Populate id for existing rows (SERIAL does not backfill on ADD COLUMN)
DO $$
DECLARE
    seq_name TEXT;
BEGIN
    seq_name := pg_get_serial_sequence('promo_codes', 'id');
    IF seq_name IS NOT NULL THEN
        EXECUTE format('UPDATE promo_codes SET id = nextval(%L::regclass) WHERE id IS NULL', seq_name);
    END IF;
END $$;

-- 6. Add primary key on id
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'promo_codes'::regclass AND contype = 'p'
    ) THEN
        ALTER TABLE promo_codes ALTER COLUMN id SET NOT NULL;
        ALTER TABLE promo_codes ADD CONSTRAINT promo_codes_id_pkey PRIMARY KEY (id);
    END IF;
END $$;

-- 7. Drop strict UNIQUE on code if exists (from old schema)
DO $$
DECLARE
    c RECORD;
BEGIN
    FOR c IN
        SELECT conname
        FROM pg_constraint
        WHERE conrelid = 'promo_codes'::regclass
          AND contype = 'u'
          AND conname LIKE '%code%'
    LOOP
        EXECUTE format('ALTER TABLE promo_codes DROP CONSTRAINT IF EXISTS %I', c.conname);
    END LOOP;
END $$;

-- 8. Create partial unique index: only one active promo per code
-- Active = is_active=true AND deleted_at IS NULL
DROP INDEX IF EXISTS unique_active_promo_code;
CREATE UNIQUE INDEX unique_active_promo_code
ON promo_codes (code)
WHERE is_active = true AND deleted_at IS NULL;
