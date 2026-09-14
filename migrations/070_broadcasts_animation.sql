-- Migration 070: broadcasts.animation_file_id — GIF/MP4 attachment
--
-- Дополнение к migration 064 (photo_file_id). GIF в Telegram —
-- это animation (файл mp4 без звука или собственно .gif). Отправляется
-- через send_animation, caption поддерживает HTML.
--
-- Взаимно исключающее с photo_file_id: если админ добавил GIF, поле
-- photo_file_id должно быть NULL (и наоборот). Валидация на уровне
-- API — не через CHECK constraint, чтобы старые данные не сломать.

ALTER TABLE broadcasts
    ADD COLUMN IF NOT EXISTS animation_file_id TEXT;

ALTER TABLE scheduled_broadcasts
    ADD COLUMN IF NOT EXISTS animation_file_id TEXT;
