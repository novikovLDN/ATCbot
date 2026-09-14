# app/services/payments — CLAUDE.md

Платёжный слой. Дополняет корневой `CLAUDE.md` (не повторяет). **Деньги — самая дорогая по цене
ошибки подсистема; любая правка тут требует теста + понимания идемпотентности.**

## Два pipeline — НЕ путать точки входа

- **`confirmation.py::process_confirmed_payment`** — единая финализация для **внешних провайдеров**
  (platega/cryptobot/wata). Провайдер-вебхук → своя верификация → сюда.
- **`service.py`** — параллельный, более «чистый» DDD-слой (`PaymentResult`/`BalanceTopupResult`,
  исключения `PaymentAmountMismatchError`/`PaymentAlreadyProcessedError`/`PaymentFinalizationError`).
  Путь для Telegram-native (Payments/Stars), НЕ вызывается вебхуками провайдеров.
- При дебаге платежа **сперва определи, какой это pipeline** — иначе будешь смотреть не тот код.

## Провайдеры (тонкие клиенты в корне репо)

Все одного паттерна: `is_enabled()` → `create_invoice/transaction()` → `process_webhook_data()` →
делегация в `confirmation.py`. Создание платежа обёрнуто `retry_async(retries=2, base_delay=1.0, max_delay=5.0)`.

| Файл (корень) | Провайдер | Верификация вебхука | Осторожно |
|---|---|---|---|
| `platega_service.py` | СБП(2)/карта(11)/intl(12) | статич. креды в заголовках `X-MerchantId/X-Secret` через `hmac.compare_digest` (НЕ подпись тела) | рекуррентные СБП-подписки (6) **удалены** (см. ниже). `return`/`failedUrl` REQUIRED (400 без них) |
| `cryptobot_service.py` | крипта (fiat RUB → USDT/TON/BTC/…) | HMAC-SHA256 по RAW body, секрет = `SHA256(API_TOKEN)` (не сам токен) | обрабатывает только `update_type=="invoice_paid"` + `status=="paid"` |
| `wata_service.py` | карта/СБП/T-Pay | RSA-SHA512 (PKCS1v15) по RAW body, `X-Signature`. Ключ: `WATA_PUBLIC_KEY_PEM` (env, без сети) или лениво `GET /public-key` под `asyncio.Lock`, кеш в процессе, неудачи не кешируются. **Fail-closed** | нет ключа / нет `cryptography` → `TransientPaymentError` → 500; неверная подпись → перезапрос ключа 1 раз (только если не pinned, не чаще 1/60с) → `WataSignatureError` (наследник Transient) → 500. VPN: нет суммы / сумма ≤0 / валюта ≠ RUB → отказ + forced admin-alert; магазин (`telegram_premium/stars`, `steam`, `spotify`, `apple_id_*`) — прежний путь. `Refund` → forced admin-alert, доступ не отзываем. `Declined` → юзеру тикет `WATA-XXXXXXXX` + алерт (cooldown). 401 API → алерт (cooldown 1 ч). Orphan `Paid` → admin-alert. rate limit 1 GET/30с — только fallback-верификация, не polling |

> **WATA: подпись fail-closed.** WATA повторяет post-payment webhook до 32 часов, пока не получит 200,
> поэтому 500 при невозможности проверки не теряет платёж — компромисса «не терять платежи vs security»
> здесь нет. Неподписанный/неверно подписанный webhook не принимается никогда. Ротация ключа: без
> `WATA_PUBLIC_KEY_PEM` подхватывается автоматически; с ним — обновить переменную.

> **Platega: рекуррентные подписки (paymentMethod=6) удалены** (решение владельца, 2026-09): нет
> `create_subscription`, кнопки/хендлера `pay:sbp_sub`, обработки списаний/статусов, `check_subscription_status`,
> entrypoint `platega_recurring`. Таблицы `platega_subscriptions`/`…_charges` (миграции 074/082) остаются
> (DROP — отдельным релизом), модуль `database/platega_subscriptions.py` — только чтение.
> **Страховка:** callback по подписке (UpperCamel, `SubscriptionId` / `Status`=`SUBSCRIPTION_*`) на общем URL
> и на `/webhooks/platega-subscription` → та же проверка `X-MerchantId/X-Secret` (и `DB_READY` → 500) → лог
> `PLATEGA_RECURRING_DISABLED_CALLBACK` + `payment_errors` (stage `platega_recurring_disabled`) + **forced**
> алерт (SubscriptionId, charge Id, Status, Amount, TG ID, инструкция отменить подписку в кабинете Platega и
> вернуть/выдать вручную) → 200. Доступ НЕ выдаётся, в БД подписок ничего не пишется. Живые подписки:
> `SELECT subscription_id, telegram_id, status, next_charge_at FROM platega_subscriptions WHERE status IN ('Active','PendingAgreement','PastDue');`
> (или админ-команда `/platega_sub_status`).

## Инварианты финализации (`confirmation.py`) — НЕ регрессировать

- **Порядок:** DB-транзакция `finalize_purchase` (атомарно) коммитится ПЕРВОЙ; доставка (bypass GB /
  premium `expireAt` в Remnawave) — ПОСЛЕ, best-effort. Падение доставки → `TransientPaymentError` →
  провайдер ретраит вебхук (платёж не откатывается).
- **Single-writer по покупке (T6):** `finalize_purchase` держит session-level
  `pg_advisory_lock(_FINALIZE_LOCK_NS, hashtext(purchase_id))` на своём соединении на ВСЮ финализацию
  (включая Phase-1 HTTP в панель — транзакция при этом НЕ открыта) и внутри tx перепроверяет статус
  блокирующим `SELECT … FOR UPDATE`. Дубль вебхука ждёт, затем получает `ValueError("already processed")`
  → 200, без второго provisioning и без ложного PERMANENT. **Не возвращать** `FOR UPDATE SKIP LOCKED`
  вне транзакции (лок снимался сразу — это и был баг).
- **HTTP-коды вебхуков:** одна таблица `payment_webhook._STATUS_HTTP` для всех роутов, общий
  `_run_webhook`. Всё, что сервис ВОЗВРАЩАЕТ (`ok`, `already_processed`, `amount_mismatch`,
  `provider_mismatch`, `not_found`, `rejected`, `invalid*`, `error`, …) — 200: повтор исход не изменит.
  500 — только брошенные `TransientPaymentError` / таймаут / нет бота или сервиса / неожиданное исключение.
  **Исключение — `unauthorized` → 500** (HOW_IT_WORKS P1-4): это может быть оплаченный колбэк при неверных
  ключах у нас; Platega повторяет не-200 3 раза через 5 мин. `unauthorized` и `invalid` (оплаченный колбэк
  без `purchase_id`/`orderId`) дают `payment_errors` (`webhook_unauthorized` / `webhook_invalid`) + forced-алерт
  в бюджете «webhook» (флуд → одна сводка). Причину сервис кладёт в `_detail` — ключи `_…` клиенту не отдаются.
  `process_confirmed_payment` пробрасывает `TransientPaymentError` (отдельный `except` перед общим) —
  не глотать его обратно в `{"status": "error"}`. Новый статус → добавить в `_STATUS_HTTP`.
  Оговорка: ретрай после commit упирается в `lookup_pending_purchase` → `already_processed` (resync не
  повторяется) — 500 здесь сигнал + алерт, настоящий повтор выдачи даст outbox (T8). Поэтому при
  `remnawave_sync_failed` confirmation СНАЧАЛА уведомляет юзера и доставляет legacy-ГБ, потом отвечает 5xx,
  а premium догоняет фоновый re-sync из `purchase_flow` (см. ниже).
- **Legacy-продление — внутри транзакции биллинга** (`finalize_purchase`, `auto_renewal`, баги
  M-RENEW-OUTSIDE-TX / M-AUTORENEW-SYNC-IN-TX, `docs/audit/03_payment_matrix.md`): `grant_access` продления
  получает conn биллинга + `_caller_holds_transaction=True`, синхронизация premium — ПОСЛЕ commit. Не
  возвращать `conn=None` для продления: автокоммит на своём соединении давал двойное продление на ретрае
  вебхука и «деньги списаны, строки payments нет» при сбое панели.
- **Сбой синхронизации premium после продления** (`purchase_flow.sync_renewal_to_remnawave`, legacy):
  `payment_errors` (stage `renewal_sync`) + алерт админу (force, 5 за 5 мин) + ОДИН фоновый re-sync к
  ТЕКУЩЕЙ дате БД (никогда не ниже/выше БД), сдался → forced-алерт. Исключение пробрасывается как раньше.
  Legacy-доливка ГБ (`remnawave_service.renew_remnawave_user`) при неудаче тоже алертит (stage `bypass_topup`).
- **Матрица платежей** — `tests/services/test_payment_matrix.py` (≈1200 ячеек, ~9 с): любая правка
  выдачи/продления/ГБ — прогнать её; флаг-off баги там strict-xfail с id.
- **Проверка провайдера:** `lookup_pending_purchase` — если `pending_purchases.payment_provider` задан и
  ≠ провайдеру вебхука → `provider_mismatch` (200, не зачисляем) + forced admin-alert + `payment_errors`.
  `NULL` (легаси) → принять + лог. Покупки магазина / notification-only (`_is_notification_only_purchase`,
  зеркало ветки магазина) проверку не проходят — прежний путь.
- **`not_found`** (строки покупки нет) → 200 + `payment_errors` + forced admin-alert. WATA алертит сам в
  `wata_service` (с tx id и суммой) — в lookup для `wata` только `payment_errors`, без двойного алерта.
- **Наценка СБП при пополнении (решение владельца 2026-09-14):** `price_kopecks` = сумма с наценкой (её платит
  юзер, её считает выручка и `payments.amount`), `pending_purchases.credit_kopecks` (миграция 083) = запрошенная
  сумма — зачисляется `min(сумма вебхука, credit_kopecks/100)`. `credit_kopecks IS NULL` (все старые строки,
  WATA/CryptoBot) — правило ниже без изменений.
- **Сумма пополнения баланса:** зачисляется `min(сумма вебхука, price_kopecks/100)` — переплата (комиссия
  провайдера) не зачисляется, лог `BALANCE_TOPUP_OVERPAYMENT`; `payments.amount` — зачисленная сумма.
  Реферального кешбэка за пополнение НЕТ (см. ниже). Недоплата сверх tolerance — отказ, как раньше.
  VPN-покупки денег не зачисляют, `payments.amount` = сумма вебхука (без изменений).
- **Реферальный кешбэк (решение владельца 2026-09-14, N17):** только за ПОКУПКУ — любую (подписка,
  комбо, пакет ГБ, подарок, магазин; провайдер, Telegram/Stars, баланс, автопродление с баланса) —
  и **никогда** за пополнение баланса (`finalize_purchase` top-up, `finalize_balance_topup`). База —
  сумма, оплаченная за покупку; один раз на `purchase_id` (проверка в `process_referral_reward` +
  unique `referral_rewards`). Покупки внутри транзакции биллинга зовут `process_referral_reward(conn=…)`
  (`finalize_purchase`, `finalize_balance_purchase`, автопродление — ключ `autorenew_{payment_id}`);
  покупки без неё — `database.award_referral_cashback` (своя транзакция, не бросает): магазин сразу после
  `mark_pending_purchase_paid` (вебхук и Telegram, 🔒-файлы не тронуты), подарок с баланса. Игровые
  покупки фермы (грядка, плёнка) кешбэк не дают — вопрос владельцу. Матрица §11.
- **`add_bypass_traffic` НЕ идемпотентен по `purchase_id`** (старый путь, флаг выключен; outbox-задача идемпотентна по ключу) → повторный вебхук = double-add GB.
  Осознанный компромисс (см. комментарии), но при новых правках доставки — держи в голове.
- **`ValueError` в `finalize_purchase` разделён намеренно (P0-1):** дубль — только
  `database.PurchaseAlreadyProcessed` (наследник ValueError) → `already_processed`;
  `PAYMENT_AMOUNT_MISMATCH` → свой алерт; **любой другой** ValueError → PERMANENT forced-алерт +
  `payment_errors` (stage `finalize_rejected`), статус `error` (200). Не схлопывать обратно.
- **Промокод оплаченной покупки (P0-1):** исчерпан/истёк/выключен между счётом и оплатой → покупка
  всё равно финализируется по оплаченной цене, использование засчитывается сверх лимита
  (`_consume_promo_for_paid_purchase`), админу — info-алерт после commit. Покупка с баланса — строго
  (`_consume_promo_in_transaction`, откат до списания).
- **Webhook-replay resync:** повторный вебхук ре-запускает `provision_subscription` ТОЛЬКО если подписка
  ещё активна И это НЕ bypass-only строка — иначе создаётся фантомный 10-летний premium (был инцидент, пофикшен).
- **`_send_confirmation`:** bypass GB top-up пропускается, если entity создан fresh в этом же платеже
  (иначе double-add). `verify_delivery.py` — всегда fire-and-forget `asyncio.create_task`.
- Идемпотентность уведомлений — отдельный флаг `payment_notifications_sent` поверх DB-lock платежа
  (защита от гонки poll + webhook + кнопка «Проверить»).

## `verify_payment_payload()` (в `service.py`)

Парсит форматы `balance_topup_` / `purchase:` / `renew:` / `purchase:promo:` / legacy `{tg_id}_{tariff}`,
`MAX_PAYLOAD_LENGTH=256`, обязательная сверка `payload_user_id == telegram_id` (защита от подмены чужим ID).
`validate_payment_amount(tolerance=1.0 RUB)` — единый допуск расхождения суммы.
