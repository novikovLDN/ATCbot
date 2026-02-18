-- Migration 026: Add game_last_played column to users table
-- Tracks when user last played the bowling game (7-day cooldown)

ALTER TABLE users ADD COLUMN IF NOT EXISTS game_last_played TIMESTAMP WITH TIME ZONE;
