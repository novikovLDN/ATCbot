-- Migration 002: Add balance system
-- Adds balance column to users and creates balance_transactions table
-- 
-- КРИТИЧНО: Эта миграция зависит от migration 001 (таблица users должна существовать)
-- Если users не существует, миграция безопасно пропустит операции с users
-- но создаст balance_transactions (независимая таблица)

-- Add balance column to users (stored in kopecks as INTEGER)
-- Проверяем существование таблицы users перед ALTER TABLE
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables 
        WHERE table_name = 'users'
    ) THEN
        ALTER TABLE users ADD COLUMN IF NOT EXISTS balance INTEGER NOT NULL DEFAULT 0;
    ELSE
        RAISE WARNING 'Table users does not exist, skipping balance column addition';
    END IF;
END $$;

-- Balance transactions table (независимая таблица, создаем всегда)
CREATE TABLE IF NOT EXISTS balance_transactions (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    amount NUMERIC NOT NULL,
    type TEXT NOT NULL,
    source TEXT,
    description TEXT,
    related_user_id BIGINT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Add related_user_id column if missing (только если таблица существует)
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables 
        WHERE table_name = 'balance_transactions'
    ) THEN
        ALTER TABLE balance_transactions ADD COLUMN IF NOT EXISTS related_user_id BIGINT;
    END IF;
END $$;

-- Add source column if missing (только если таблица существует)
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables 
        WHERE table_name = 'balance_transactions'
    ) THEN
        ALTER TABLE balance_transactions ADD COLUMN IF NOT EXISTS source TEXT;
    END IF;
END $$;

-- Ensure amount is NUMERIC type
-- Используем DO блок для безопасной проверки типа колонки
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'balance_transactions' 
        AND column_name = 'amount' 
        AND data_type != 'numeric'
    ) THEN
        ALTER TABLE balance_transactions ALTER COLUMN amount TYPE NUMERIC USING amount::NUMERIC;
    END IF;
END $$;

