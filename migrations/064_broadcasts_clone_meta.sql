-- Migration 064: сохраняем photo_file_id и buttons на рассылке
--
-- Нужно, чтобы дашборд мог сделать «Отправить снова» с сохранением
-- исходной картинки и выбора кнопок. Раньше эти поля жили только в
-- request'е create'а и не оседали в БД — админ мог видеть текст
-- прошлой рассылки, но не мог быстро клонировать её с той же
-- визуальной обвязкой.
--
-- photo_file_id — nullable, string (Telegram file_id, до 300 символов
--                  наблюдаемых в API).
-- buttons        — nullable, TEXT[] со значениями из _BUTTON_TYPES
--                  (buy / promo_buy / gift_reveal / ...). NULL = не
--                  сохраняли (старые рассылки до этой миграции).

ALTER TABLE broadcasts
    ADD COLUMN IF NOT EXISTS photo_file_id TEXT;

ALTER TABLE broadcasts
    ADD COLUMN IF NOT EXISTS buttons TEXT[];
