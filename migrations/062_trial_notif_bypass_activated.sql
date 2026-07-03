-- Migration 062: trial bypass-activated notification flag
--
-- Новое уведомление, которое летит триал-юзеру через ~5 минут после
-- активации: «🛡 Обход белых списков подключён» + кнопка «Включить обход»,
-- ведущая на экран установки в Happ / Incy + ручного показа bypass-ключа.
--
-- Идемпотентность как у всех прочих trial_notif_*_sent — one-shot флаг
-- по строке подписки. Триал-подписки уникальны в scope (source='trial'),
-- один флаг на юзера.

ALTER TABLE subscriptions
    ADD COLUMN IF NOT EXISTS trial_notif_bypass_activated_sent BOOLEAN DEFAULT FALSE;
