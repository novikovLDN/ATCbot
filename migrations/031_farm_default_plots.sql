-- Migration 031: Set farm_plot_count to 1 for users who never played farm
-- Purpose: New default is 1 starting plot. Only affects users with empty farm_plots.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_name = 'users'
    ) THEN
        RAISE WARNING 'Table users does not exist, skipping migration 031';
        RETURN;
    END IF;
END $$;

-- Update existing users who have 3 plots but never played (empty farm_plots)
UPDATE users
SET farm_plot_count = 1
WHERE farm_plot_count = 3
  AND (farm_plots = '[]'::jsonb OR farm_plots IS NULL);
