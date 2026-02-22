-- Migration 031: Fix farm defaults — plot 0 always exists, reset never-farmed users
-- Purpose: Ensure users who never farmed have exactly 1 plot (plot_id 0).

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

-- Reset users who have never farmed to 1 plot (plot_id 0)
UPDATE users
SET farm_plot_count = 1,
    farm_plots = '[{"plot_id": 0, "status": "empty", "plant_type": null,
                   "planted_at": null, "ready_at": null, "dead_at": null,
                   "notified_ready": false, "notified_12h": false,
                   "notified_dead": false, "water_used_at": null,
                   "fertilizer_used_at": null}]'::jsonb
WHERE (farm_plots = '[]'::jsonb OR farm_plots IS NULL);

-- For users who have farm_plot_count = 3 but only empty or single plot
-- (old default was 3) — reset to 1
UPDATE users
SET farm_plot_count = 1
WHERE farm_plot_count = 3
  AND jsonb_array_length(farm_plots) <= 1;
