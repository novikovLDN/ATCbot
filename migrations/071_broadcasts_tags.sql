-- Migration 071: broadcasts.tag + tag_color — цветные метки для рассылок
--
-- Админ может пометить рассылку тегом (короткая строка, e.g. «летняя
-- акция», «реактивация») и одним из 7 предустановленных цветов.
-- Отображается chip'ом в списке и в BroadcastDetail. Полезно для
-- беглого фильтрования и группировки по цели/кампании.
--
-- tag       — свободная строка ≤40 символов
-- tag_color — один из 7 семантических цветов: gray/red/orange/yellow/
--             green/blue/purple. Валидация на API-уровне, а не через
--             CHECK constraint — чтобы можно было добавлять новые цвета
--             без миграции.

ALTER TABLE broadcasts
    ADD COLUMN IF NOT EXISTS tag TEXT;
ALTER TABLE broadcasts
    ADD COLUMN IF NOT EXISTS tag_color TEXT;

ALTER TABLE scheduled_broadcasts
    ADD COLUMN IF NOT EXISTS tag TEXT;
ALTER TABLE scheduled_broadcasts
    ADD COLUMN IF NOT EXISTS tag_color TEXT;

-- Индекс для будущего фильтра «показать все рассылки с тегом X»
CREATE INDEX IF NOT EXISTS idx_broadcasts_tag ON broadcasts (tag);
