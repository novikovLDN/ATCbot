-- 059_ambassador_cashback_floor.sql
--
-- Переход реферальной программы на «Круг Амбассадоров» (5 уровней).
-- Старая шкала: Silver 10% / Gold 25% (25+) / Platinum 45% (50+).
-- Новая шкала: Проводник 10% / Хранитель 20% (25+) / Инсайдер 30% (50+) /
--              Лидер 40% (75+) / Амбассадор 45% (100+).
--
-- Текущие Platinum-юзеры (50+ оплативших рефералов) получают 45%, а по
-- новой шкале им бы давалось 30% (50-74) или 40% (75-99). Чтобы не
-- понижать лояльных, вводим персональный «пол» процента — grandfather.

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS cashback_floor_percent INTEGER;

COMMENT ON COLUMN users.cashback_floor_percent IS
    'Персональный минимум процента кэшбэка (grandfather от старой шкалы или admin-grant). NULL = брать по тиру.';

-- Backfill: всем, у кого на момент миграции 50+ оплативших рефералов,
-- фиксируем floor=45 (их старый Platinum-процент).
UPDATE users SET cashback_floor_percent = 45
WHERE cashback_floor_percent IS NULL
  AND telegram_id IN (
      SELECT referrer_user_id
      FROM referrals
      WHERE first_paid_at IS NOT NULL
      GROUP BY referrer_user_id
      HAVING COUNT(DISTINCT referred_user_id) >= 50
  );
