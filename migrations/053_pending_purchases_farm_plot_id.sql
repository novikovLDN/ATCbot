-- Migration 053: pending_purchases.farm_plot_id
--
-- The Farm storm-shield purchase needs to remember which plot the user
-- targeted while the Lava/Платега invoice is in flight.  A dedicated
-- column is cleaner than overloading tariff or promo_code with a
-- "shield_3"-style suffix.
--
-- NULL for every existing purchase row (and for any non-farm purchase).
-- Always NULL unless purchase_type='farm_effect' AND tariff='farm_storm_shield'.

ALTER TABLE pending_purchases ADD COLUMN IF NOT EXISTS farm_plot_id INTEGER;
