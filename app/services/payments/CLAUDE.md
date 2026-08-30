# app/services/payments — CLAUDE.md

Платёжный слой. Дополняет корневой `CLAUDE.md` (не повторяет). **Деньги — самая дорогая по цене
ошибки подсистема; любая правка тут требует теста + понимания идемпотентности.**

## Два pipeline — НЕ путать точки входа

- **`confirmation.py::process_confirmed_payment`** — единая финализация для **внешних провайдеров**
  (platega/lava/cryptobot/wata). Провайдер-вебхук → своя верификация → сюда.
- **`service.py`** — параллельный, более «чистый» DDD-слой (`PaymentResult`/`BalanceTopupResult`,
  исключения `PaymentAmountMismatchError`/`PaymentAlreadyProcessedError`/`PaymentFinalizationError`).
  Путь для Telegram-native (Payments/Stars), НЕ вызывается вебхуками провайдеров.
- При дебаге платежа **сперва определи, какой это pipeline** — иначе будешь смотреть не тот код.

## Провайдеры (тонкие клиенты в корне репо)

Все одного паттерна: `is_enabled()` → `create_invoice/transaction()` → `process_webhook_data()` →
делегация в `confirmation.py`. Создание платежа обёрнуто `retry_async(retries=2, base_delay=1.0, max_delay=5.0)`.

| Файл (корень) | Провайдер | Верификация вебхука | Осторожно |
|---|---|---|---|
| `platega_service.py` | СБП(2)/карта(11)/intl(12)/подписка(6) | статич. креды в заголовках `X-MerchantId/X-Secret` через `hmac.compare_digest` (НЕ подпись тела) | рекуррентные СБП-подписки — ОТДЕЛЬНЫЙ обработчик `process_subscription_webhook_data` (заглавные ключи `Id/SubscriptionId/Status`, идемпотентность по PK `charge_id`); если подписки нет в БД — charge не пишется (FK), только `orphan_charge` лог. `return`/`failedUrl` REQUIRED (400 без них) |
| `lava_service.py` | карта | HMAC-SHA256(`json.dumps(data)`, secret) в `Signature` + доп. запрос `check_invoice_status()` | **сериализация должна совпадать байт-в-байт** с подписанной — не менять порядок ключей при рефакторинге. `_verify_webhook_signature` **fail-open если `LAVA_SIGN_KEY` не задан** |
| `cryptobot_service.py` | крипта (fiat RUB → USDT/TON/BTC/…) | HMAC-SHA256 по RAW body, секрет = `SHA256(API_TOKEN)` (не сам токен) | обрабатывает только `update_type=="invoice_paid"` + `status=="paid"` |
| `wata_service.py` | карта/СБП/T-Pay | RSA-SHA512, публ. ключ лениво через `GET /public-key`, кеш в процессе | **не переживает деплой без переполучения ключа**; **fail-open** если ключ не получить; сам обрабатывает `Declined` (тикет `WATA-XXXXXXXX`) и orphan `Paid` (admin-alert, не тихий дроп); rate limit 1 GET/30с — только fallback-верификация, не polling |

> **Fail-open на подписи (lava/wata) — осознанный trade-off «не терять платежи» vs security.** Не
> «чинить» в secure-fail, не разобравшись в компромиссе.

## Инварианты финализации (`confirmation.py`) — НЕ регрессировать

- **Порядок:** DB-транзакция `finalize_purchase` (атомарно) коммитится ПЕРВОЙ; доставка (bypass GB /
  premium `expireAt` в Remnawave) — ПОСЛЕ, best-effort. Падение доставки → `TransientPaymentError` →
  провайдер ретраит вебхук (платёж не откатывается).
- **`add_bypass_traffic` НЕ идемпотентен по `purchase_id`** → повторный вебхук = double-add GB.
  Осознанный компромисс (см. комментарии), но при новых правках доставки — держи в голове.
- **`ValueError` в `finalize_purchase` разделён намеренно:** «already processed» (идемпотентный дубль,
  ретрай) vs `PAYMENT_AMOUNT_MISMATCH` (реальная ошибка → admin-alert, не ретраится). Не схлопывать обратно.
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
