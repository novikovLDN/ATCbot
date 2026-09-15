-- Broadcast gift buttons with a period (owner 2026-09-15): «−30% на 1 месяц»,
-- «−30% на 3 месяца», «−40% на 1 год» discount ONLY that period (any plan of
-- it), for 24 h, on the regular tariff screen — «if the button says 1 year
-- −40 %, it is 1 year −40 %». Kept apart from user_discounts (the one general
-- personal discount per user) so a period gift never replaces it; the price
-- engine (calculate_final_price) takes the largest single discount.
-- Timestamps are naive UTC (project contract: _to_db_utc / _from_db_utc).
CREATE TABLE IF NOT EXISTS user_period_discounts (
    telegram_id      BIGINT    NOT NULL,
    period_days      INTEGER   NOT NULL,
    discount_percent INTEGER   NOT NULL CHECK (discount_percent > 0 AND discount_percent < 100),
    expires_at       TIMESTAMP NOT NULL,
    source           TEXT,
    created_at       TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC'),
    PRIMARY KEY (telegram_id, period_days)
);
