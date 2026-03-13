-- Migration: Add broadcast_discounts table for promo buy buttons
CREATE TABLE IF NOT EXISTS broadcast_discounts (
    id SERIAL PRIMARY KEY,
    broadcast_id INTEGER NOT NULL UNIQUE REFERENCES broadcasts(id) ON DELETE CASCADE,
    discount_percent INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
