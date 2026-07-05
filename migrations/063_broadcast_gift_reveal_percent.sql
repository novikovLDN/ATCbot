-- Migration 063: gift_reveal-скидка per-broadcast (админ выбирает %)
--
-- Раньше «Посмотреть подарок» шла с зашитой 20%-скидкой на 48ч.
-- Теперь админ в визарде рассылки выбирает 20/25/30/35/40 —
-- значение сохраняется отдельной колонкой, чтоб не конфликтовать с
-- promo_buy/promo_traffic-скидкой (у них своя `discount_percent`).
--
-- Продолжительность (48ч) остаётся зашитой — она часть механики
-- reveal-эффекта, не варьируется.

ALTER TABLE broadcast_discounts
    ADD COLUMN IF NOT EXISTS gift_reveal_percent INTEGER;
