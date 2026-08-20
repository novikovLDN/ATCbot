-- Anti-bot проверка на /start. Пометка «юзер уже прошёл капчу когда-то»:
-- после первого success мы капчу больше не показываем этому telegram_id.
-- NULL = ещё не проходил. TIMESTAMPTZ на случай будущей аналитики /
-- принудительного сброса.

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS captcha_passed_at TIMESTAMPTZ;
