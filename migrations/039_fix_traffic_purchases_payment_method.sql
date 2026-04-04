-- 039: Fix traffic_purchases missing payment_method column
-- Column may be missing if table was created by earlier migration without it
ALTER TABLE traffic_purchases ADD COLUMN IF NOT EXISTS payment_method TEXT;
