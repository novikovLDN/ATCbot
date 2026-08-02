-- Migration 072: payments.payment_provider — чем именно заплатили.
--
-- Зачем. Выручка считалась как SUM(payments.amount) WHERE status='approved'.
-- В эту таблицу пишутся ТРИ разных события:
--   1. пополнение баланса картой/криптой (tariff='balance_topup'),
--   2. покупка подписки с этого же баланса (finalize_balance_purchase),
--   3. автопродление с баланса (auto_renewal).
-- Пункты 2 и 3 — внутреннее движение уже учтённых денег, но по строке в
-- payments их не отличить от прямой оплаты картой: tariff у них такой же
-- ('plus_30'), отдельного признака не было. Одни и те же рубли попадали в
-- выручку два-три раза, а реферальный кешбэк, потраченный с баланса,
-- превращался в «выручку» из воздуха.
--
-- Колонка даёт этот признак. Договорённость по всему проекту:
--   выручка = внешние поступления = строки, у которых
--   COALESCE(payment_provider, '') <> 'balance'.
--
-- Миграция аддитивная: колонка nullable, старые строки остаются NULL и
-- продолжают считаться выручкой (для них это верно — до появления покупок
-- с баланса других вариантов не было; там, где связь с покупкой сохранилась,
-- значение восстанавливается бэкфиллом ниже).

ALTER TABLE payments ADD COLUMN IF NOT EXISTS payment_provider TEXT;

-- Бэкфилл по связи payments.purchase_id → pending_purchases.purchase_id:
-- там провайдер писался с миграции 054, и для этих строк восстановить
-- признак можно точно.
UPDATE payments p
SET payment_provider = pp.payment_provider
FROM pending_purchases pp
WHERE p.purchase_id IS NOT NULL
  AND p.purchase_id = pp.purchase_id
  AND p.payment_provider IS NULL
  AND pp.payment_provider IS NOT NULL;

-- Пополнения баланса всегда приходят извне — помечаем явно, чтобы они
-- никогда не отфильтровались вместе с внутренними движениями.
UPDATE payments
SET payment_provider = COALESCE(payment_provider, 'external')
WHERE tariff = 'balance_topup'
  AND payment_provider IS NULL;

CREATE INDEX IF NOT EXISTS idx_payments_provider_status
    ON payments (payment_provider, status);
