# Platega API — руководство по интеграции

> Источник: предоставлено владельцем 2026-09-13, составлено по официальной OpenAPI-документации Platega.
> Используется как эталон для сверки `platega_service.py` и платёжного ядра.

---

## 1. Общие сведения

| Параметр | Значение |
|---|---|
| Базовый URL | `https://app.platega.io/` |
| Формат | JSON по HTTPS |
| Основная авторизация | заголовки `X-MerchantId` + `X-Secret` |
| Авторизация Payout API (выводы, карты) | `Authorization: PG-HMAC ...` (раздел 8) |

### 1.1 Авторизация (основной API)

Во **все** запросы, кроме Payout API, передаются два заголовка:

```
X-MerchantId: <MerchantId (UUID)>
X-Secret:     <API ключ>
```

Ошибки: `400` — валидация; `401` — неверные `X-MerchantId`/`X-Secret`; `404` — транзакция не найдена.

### 1.2 Карта эндпоинтов

| Группа | Метод | Путь | Назначение |
|---|---|---|---|
| Платежи | POST | `/transaction/process` | Создать платёж с заданным методом |
| Платежи | POST | `/v2/transaction/process` | Создать платёж, метод выбирает плательщик |
| Платежи | GET | `/transaction/{id}` | Статус транзакции |
| Платежи | GET | `/h2h/{id}` | QR или ссылка для H2H (по запросу к менеджеру) |
| Платежи | POST | `/transaction/export/json` | Выгрузка транзакций в JSON |
| Платежи | POST | `/transaction/export/csv` | Выгрузка транзакций в CSV (ссылка на файл) |
| Возвраты | GET | `/transaction/{id}/cancel-supported` | Можно ли отменить транзакцию |
| Возвраты | POST | `/transaction/{id}/cancel` | Отмена или возврат |
| Подписки | POST | `/transaction/process` (`paymentMethod: 6`) | Создать СБП-подписку |
| Подписки | GET | `/subscription/{subscriptionId}` | Получить подписку |
| Подписки | GET | `/subscription` | Список подписок |
| Подписки | POST | `/subscription/{subscriptionId}/cancel` | Отменить подписку |
| Балансы | GET | `/balance/all` | Балансы мерчанта |
| Выводы (HMAC) | POST | `/api/v1/payouts/card-rub` | Вывод на рублёвую карту |
| Выводы (HMAC) | GET | `/api/v1/cards` | Сохранённые карты |

## 2. Справочники

### 2.1 Способы оплаты (`PaymentMethodInt`, число)

| Код | Метод |
|---|---|
| 2 | СБП (QR) |
| 3 | ЕРИП |
| 6 | СБП-подписка (рекуррент), только для создания подписки |
| 11 | Карточный эквайринг |
| 12 | Международная оплата |
| 13 | Криптовалюта |
| 14 | Sberpay |

В ответах метод приходит строкой, например `SBPQR` или `Subscription`.

### 2.2 Статус транзакции (`PaymentStatus`)

`PENDING` → `CONFIRMED` (оплачено) | `CANCELED` (не оплачено или истекло) | `CHARGEBACKED` (возврат).

### 2.3 Статус подписки

| В API (`GET /subscription/{id}`) | В callback | Смысл |
|---|---|---|
| `PendingAgreement` | — | Ожидание подтверждения привязки (30 мин) |
| `Active` | `SUBSCRIPTION_ACTIVATED` | Списания идут по расписанию |
| `PastDue` | `SUBSCRIPTION_PAST_DUE` | Списание не прошло. Статус постоянный: провайдер не повторяет попытку, выход только через явную отмену |
| `Cancelled` | `SUBSCRIPTION_CANCELLED` | Отменена мерчантом (через API) или плательщиком (по ссылке из email) |
| `Failed` | `SUBSCRIPTION_FAILED` | Привязка не удалась при первой активации |

⚠️ В списке `GET /subscription` поля `status` и `intervalUnit` приходят **числами**, а в `GET /subscription/{id}` — **строками**.

### 2.4 Интервал подписки (`SubscriptionInterval`)

| Код | Период | Макс. `intervalCount` |
|---|---|---|
| 1 | день | 31 |
| 2 | неделя | 4 |
| 3 | месяц (30 дней) | 12 |
| 4 | год | 3 |

## 3. Платежи

### 3.1 Создать платёж с заданным методом

`POST /transaction/process`. Поле `id` **не передавать**, его генерирует система.

```json
{
  "paymentMethod": 2,
  "paymentDetails": { "amount": 500, "currency": "RUB" },
  "description": "Оплата заказа #293",
  "return": "https://example.com/success",
  "failedUrl": "https://example.com/fail",
  "payload": "любая строка, вернётся в callback",
  "orderId": "ID вашего внутреннего платежа",
  "metadata": { "userId": "123456789", "userName": "@username" }
}
```

| Поле | Обяз. | Описание |
|---|---|---|
| `paymentMethod` | да | Код метода (2.1) |
| `paymentDetails.amount` | да | Сумма (number) |
| `paymentDetails.currency` | да | Например, `RUB` |
| `description` | да | Назначение платежа |
| `return` | да | Редирект при успехе |
| `failedUrl` | да | Редирект при неуспехе |
| `payload` | нет | Произвольные данные, возвращаются в callback и в статусе |
| `orderId` | нет | Внутренний ID платежа |
| `metadata.userId` | зависит от магазина | ID плательщика (для антифрода). Для некоторых категорий магазинов **обязателен**: без него отключается антифрод, а магазин могут отключить. Уточнить у менеджера |
| `metadata.userName` | вместе с userId | Дополнительные данные о плательщике |

Ответ 200:

```json
{ "paymentMethod": "SBPQR", "transactionId": "3fa85f64-...", "redirect": "https://pay.platega.io?...",
  "return": "https://example.com/success", "paymentDetails": "100 RUB", "status": "PENDING",
  "expiresIn": "00:15:00", "merchantId": "1a021d91-...", "usdtRate": 93.45 }
```

Плательщика перенаправить на `redirect`. Платёж живёт `expiresIn`, обычно 15 минут.

### 3.2 Создать платёж без заданного метода

`POST /v2/transaction/process` — тело то же, но **без** `paymentMethod`. Ответ: `{ transactionId, status, url, expiresIn, rate }`. Ссылка на оплату приходит в поле **`url`**, а не `redirect`.

### 3.3 Проверить статус

`GET /transaction/{id}`. Ключевые поля ответа:
- `id`, `status` (2.2), `paymentDetails.amount/currency`, `paymentMethod` (строка);
- `comission`, `comissionUsdt`, `amountUsdt`, `expiresIn`, `qr`, `payload`, `externalId`;
- `description`, `return`, `payformSuccessUrl`.

Опечатки API (`mechantId`, `comission`) сохранены как есть.

### 3.4 H2H

`GET /h2h/{id}` → `{ "amount": 136.12, "qr": "https://qr.nspk.ru/..." }`.

### 3.5 Выгрузка

`POST /transaction/export/json` и `POST /transaction/export/csv`. Тело: `{ "statuses": ["6","7"], "paymentMethods": ["2","11"], "from": "...Z", "to": "...Z", "timeZoneId": "UTC" }`.
- JSON возвращает `[{ recordId, createdAt, amount, currencyCode, status, paymentMethod, description, payload }]`.
- CSV возвращает `{ "url": "..." }`.
- `statuses` — числовые коды в строках; расшифровку уточнить у менеджера.

## 4. Callback (webhook) по транзакции

URL задаётся в ЛК: Настройки → Callback URLs.
- **Требования:** только HTTPS, публичный домен, валидный SSL от доверенного CA. Self-signed, приватные сети и localhost запрещены.
- **Доставка: таймаут 60 с. Без успешного ответа — до 3 повторов с интервалом 5 минут.** Отвечать 200 быстро, обрабатывать асинхронно.
- **Подлинность:** в заголовках приходят `X-MerchantId` и `X-Secret`. Их нужно сверить со своими, при несовпадении запрос отбросить.

```json
{ "id": "00000000-0000-0000-0000-000000000000", "amount": 1000, "currency": "RUB",
  "status": "CONFIRMED", "paymentMethod": 2, "payload": "..." }
```

- `status`: `CONFIRMED` — оплачено, `CANCELED` — не оплачено, `CHARGEBACKED` — возврат.
- **Идемпотентность:** повторы возможны, каждый `id` обрабатывается один раз. При сомнениях — дополнительно `GET /transaction/{id}`.

## 5. Возвраты

1. `GET /transaction/{id}/cancel-supported`. Ответ содержит `supported`, `totalDeductUsdt`, `penaltyNativeAmount`, `penaltyNativeCurrency`, `penaltyUsdt`, `penaltyConversionRate`, `blockReason`. Заголовок `accept: text/plain` обязателен.
2. `POST /transaction/{id}/cancel` → `{ transactionId, accepted, manualControlRequired, message }`. Если `accepted:false` или `manualControlRequired:true`, нужна поддержка.
3. После возврата придёт callback со `status: CHARGEBACKED`.

## 6. Рекуррентные СБП-подписки

На шаге создания денежная транзакция не создаётся: транзакции появляются при каждом списании.

### 6.1 Создать

`POST /transaction/process`:

```json
{ "paymentMethod": 6,
  "paymentDetails": { "amount": 500, "currency": "RUB", "interval": 3, "intervalCount": 1 },
  "description": "Premium подписка" }
```

Ответ: `{ "paymentMethod": "Subscription", "transactionId": "1111...", "redirect": "https://pay.platega.io/subscription/1111...", "status": "PENDING", "merchantId": "2222..." }`.
- ⚠️ `transactionId` здесь = **`subscriptionId`**.
- ⚠️ Редирект нужен сразу: на привязку даётся **30 минут**, иначе подписка переходит в `Failed`.

### 6.2 Получить

`GET /subscription/{subscriptionId}` → `id, status (строка), amount, currencyCode, intervalUnit (строка), intervalCount, startAt, nextChargeAt, lastChargeAt, description, createdAt, customerEmail, chargeMetrics{...}`.

### 6.3 Список

`GET /subscription?status=&from=&to=&page=1&size=20` → `{ items, total, page, size }`. В `items` поля `status` и `intervalUnit` — **числа**.

### 6.4 Отменить

`POST /subscription/{subscriptionId}/cancel` → `{ subscriptionId, status: "cancelled" }`. Вызов идемпотентен.

### 6.5 Callback'и по подпискам

Приходят на тот же URL с теми же заголовками. Поля пишутся с заглавной буквы.
- **Смена статуса подписки** (`Id == SubscriptionId`): `{ "Id", "Amount", "Currency", "Status": "SUBSCRIPTION_ACTIVATED|PAST_DUE|CANCELLED|FAILED", "PaymentMethod": 6, "Payload", "SubscriptionId", "NextChargeAt" }`.
- **Списание** (`Id` = новый ID транзакции): те же поля, `Status` равен `CONFIRMED` или `CANCELED`.
  - `CONFIRMED` — деньги списаны.
  - `CANCELED` — списание не прошло, `NextChargeAt = null`, подписка переходит в `PastDue`, **повторов не будет**.

**Как различить тип callback'а в одном обработчике:**
- `Status` начинается с `SUBSCRIPTION_` → событие подписки.
- Есть `SubscriptionId`, а `Status` равен `CONFIRMED` или `CANCELED` → списание.
- Поля в lowerCamel и нет `SubscriptionId` → обычный платёж.

## 7. Балансы

`GET /balance/all` → `[{ amount, currency, frozenBalance? }]`.

## 8. Payout API (HMAC)

Подключается по запросу. Секретный ключ показывается один раз.

```
string_to_sign = METHOD \n PATH \n timestamp \n idempotency-key \n sha256_hex(body)
signature      = Base64( HMAC-SHA256(SECRET, string_to_sign) )
Authorization: PG-HMAC kid={MERCHANT_ID}, ts={timestamp}, sig={signature}
```

- `timestamp` — unix-секунды, окно ±300 с.
- `Idempotency-Key` — UUID, свой для каждого вывода. Для GET на его месте пустая строка.
- Хэш тела для GET: `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`.

`POST /api/v1/payouts/card-rub`:
- тело: `{ cardNumber | cardId, amountRub (1000–87500), payoutMethod: "CARD", currencyRequested: "RUB" }`;
- ответ: `{ withdrawalRecordId, status: "CREATED", cardMasked, amountUsdtDebited }`.

`GET /api/v1/cards?onlyActive=true` → `[{ cardId, masked, last4, brand, label, status }]`.

## 9. Типовые сценарии

- **Разовый платёж.**
  1. Создать платёж, сохранить `transactionId` ↔ заказ.
  2. Отправить плательщика на `redirect` (для v2 — `url`).
  3. Callback `CONFIRMED` → выдать товар; дубли отсекать по `id`.
  4. Запасной путь — `GET /transaction/{id}`.
- **Подписка.**
  1. `paymentMethod: 6`, сохранить `subscriptionId`, сразу редирект.
  2. `SUBSCRIPTION_ACTIVATED` → доступ открыт.
  3. Каждое списание — callback `CONFIRMED` с `SubscriptionId`.
  4. `CANCELED` или `PAST_DUE` → закрыть доступ.
- **Возврат.** `cancel-supported` → `cancel` → callback `CHARGEBACKED`.

## 10. Чек-лист интеграции

- [ ] `X-MerchantId` и `X-Secret` лежат в env, а не в коде.
- [ ] Callback URL: HTTPS, публичный домен, валидный сертификат, зарегистрирован в ЛК.
- [ ] Обработчик проверяет `X-MerchantId`/`X-Secret`, быстро отвечает 200 и идемпотентен.
- [ ] Один обработчик разбирает три типа тела: платёж, статус подписки, списание.
- [ ] При создании транзакции `id` не передаётся.
- [ ] Выяснено у менеджера, обязателен ли `metadata.userId`.
- [ ] Для подписок: `paymentMethod: 6`, `transactionId` = `subscriptionId`, редирект сразу.
- [ ] Учтено, что `status`/`intervalUnit` в списке — числа, а в одиночном GET — строки.
- [ ] Payout: подписываются те же байты, что отправляются; новый `Idempotency-Key` на каждый вывод; сумма 1000–87500 RUB.
