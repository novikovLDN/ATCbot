# Platega API — справочник для Claude Code

Выжимка официальной документации Platega. Держать в `docs/PLATEGA_API.md` и подключать
в `CLAUDE.md` строкой `@docs/PLATEGA_API.md`, либо ссылаться на файл в задаче.

- **Base URL:** `https://app.platega.io`
- **Формат:** JSON поверх HTTPS
- **Аутентификация (весь API, кроме Payout):** заголовки `X-MerchantId` и `X-Secret`
- **Аутентификация Payout API:** HMAC-SHA256, схема `PG-HMAC` (раздел 7)

Учётные данные выдаёт менеджер при подключении, они же доступны в ЛК → «Настройки».

---

## 0. Правила при написании кода

1. **Не передавать `id`** в теле создания транзакции — его генерирует система.
2. `paymentMethod` — **число**, не строка (частая ошибка: `"6"` вместо `6`).
3. Ключи только из окружения: `PLATEGA_MERCHANT_ID`, `PLATEGA_SECRET`,
   `PLATEGA_PAYOUT_SECRET`. Не хардкодить, не логировать, не коммитить.
4. Callback платежа и callback подписки имеют **разную капитализацию ключей** —
   нужны два парсера (см. 6.1 и 6.2).
5. Схемы подписки в списке и в детальной ручке различаются типами — нормализовать (5.3).
6. Обработка callback'ов должна быть идемпотентной: до 3 ретраев с интервалом 5 минут.
7. Не выдумывать эндпоинты, которых нет в этом файле — см. раздел 10.

---

## 1. Справочники (enum)

### PaymentMethodInt — способы оплаты

| Значение | Метод |
|---|---|
| `2` | СБП (QR-код) + SberPay, если подключён |
| `3` | ЕРИП |
| `11` | Карточный эквайринг |
| `12` | Международная оплата |
| `13` | Криптовалюта |
| `6` | Рекуррентная СБП-подписка (только `createSubscription`) |

### PaymentStatus

`PENDING` · `CANCELED` · `CONFIRMED` · `CHARGEBACKED`

### SubscriptionStatus

`PendingAgreement` · `Active` · `PastDue` · `Cancelled` · `Failed`

### CallbackSubscriptionStatus

`SUBSCRIPTION_ACTIVATED` · `SUBSCRIPTION_PAST_DUE` · `SUBSCRIPTION_CANCELLED` · `SUBSCRIPTION_FAILED`

### SubscriptionInterval

| Значение | Период |
|---|---|
| `1` | день |
| `2` | неделя |
| `3` | месяц |
| `4` | год |

---

## 2. Платежи

### 2.1 `POST /transaction/process` — создать платёж

| Поле | Тип | Обяз. | Описание |
|---|---|---|---|
| `paymentMethod` | integer | да | См. PaymentMethodInt |
| `paymentDetails.amount` | number | да | Сумма |
| `paymentDetails.currency` | string | да | Например `RUB` |
| `description` | string | да | Назначение платежа |
| `return` | string (uri) | да | Редирект при успехе |
| `failedUrl` | string (uri) | да | Редирект при неудаче |
| `payload` | string | да | Произвольные данные, вернутся в callback |
| `metadata.userId` | string | зависит | ID плательщика в вашей системе (например, Telegram user ID) |
| `metadata.userName` | string | зависит | Доп. данные о плательщике |

**Ответ 200**

```json
{
  "paymentMethod": "SBPQR",
  "transactionId": "3fa85f64-5717-4562-b3fc-2c463f66afa6",
  "redirect": "https://pay.platega.io?qrsbp",
  "return": "https://example.com/success",
  "paymentDetails": "100 RUB",
  "status": "PENDING",
  "expiresIn": "00:15:00",
  "merchantId": "1a021d91-9b26-4762-b303-5d4aac74e921",
  "usdtRate": 93.45
}
```

Ошибки: `400` — валидация, `401` — аутентификация (проверить `X-MerchantId` / `X-Secret`).

**Критично**

- Поле `id` в запросе не передавать.
- Для магазинов отдельных категорий обязателен `metadata.userId`. Его отсутствие при
  наличии требования отключает антифрод-защиту и может привести к отключению магазина.
  Требуется ли это вам — уточнять у менеджера.
- `paymentDetails` в ответе — то строка (`"100 RUB"`), то объект `{amount, currency}`.
  Парсить оба варианта.
- При `paymentMethod: 13` (крипта) по умолчанию редирект на веб-пейформу; оплата через
  Telegram-бота подключается отдельно через менеджера.

```bash
curl -X POST https://app.platega.io/transaction/process \
  -H "X-MerchantId: $PLATEGA_MERCHANT_ID" \
  -H "X-Secret: $PLATEGA_SECRET" \
  -H "Content-Type: application/json" \
  -d '{
    "paymentMethod": 2,
    "paymentDetails": {"amount": 500, "currency": "RUB"},
    "description": "Оплата заказа #293",
    "return": "https://example.com/success",
    "failedUrl": "https://example.com/fail",
    "payload": "order-293",
    "metadata": {"userId": "123456789", "userName": "@username"}
  }'
```

### 2.2 `GET /transaction/{id}` — статус платежа

`id` — UUID в path, заголовки те же.

| Поле | Описание |
|---|---|
| `id` | UUID транзакции |
| `status` | PaymentStatus |
| `paymentDetails.amount` / `.currency` | Сумма и валюта |
| `merchantName`, `mechantId` | Мерчант (`mechantId` — опечатка в самом API, так и приходит) |
| `comission`, `comissionType`, `comissionUsdt`, `amountUsdt` | Комиссии и суммы в USDT |
| `paymentMethod` | Строковое имя метода, например `SBPQR` |
| `expiresIn` | `HH:MM:SS` |
| `qr` | QR: base64 или URL |
| `return`, `payformSuccessUrl` | Ссылки редиректа |
| `payload`, `externalId`, `description` | Ваши данные |

`404` — транзакция не найдена.

### 2.3 `GET /h2h/{id}` — QR для H2H-транзакции

```json
{
  "amount": 136.12,
  "qr": "https://qr.nspk.ru/...?type=00&bank=...&sum=...&cur=RUB&crc=..."
}
```

> Отсутствие транзакции здесь отдаётся кодом **`400`**, а не `404`. Учесть в обработчике.

---

## 3. Выгрузки транзакций

Три эндпоинта с одинаковым телом запроса:

| Эндпоинт | Результат |
|---|---|
| `POST /transaction/export/csv` | `{"url": "..."}` — ссылка на файл |
| `POST /transaction/export/excel` | `{"url": "..."}` — ссылка на файл |
| `POST /transaction/export/json` | массив транзакций прямо в ответе |

```json
{
  "statuses": ["6", "7"],
  "paymentMethods": ["2", "11"],
  "from": "2026-05-01T00:00:00.000Z",
  "to": "2026-06-16T08:50:04.820Z",
  "timeZoneId": "UTC"
}
```

Элемент JSON-выгрузки:

```json
{
  "recordId": "486c22ef-3524-4a1c-9740-3fe8c3e859d9",
  "createdAt": "2026-06-15 13:44:13",
  "amount": 1150,
  "currencyCode": "RUB",
  "status": "CANCELED",
  "paymentMethod": "SBPQR",
  "description": "1234",
  "payload": ""
}
```

> В фильтре `statuses` — числовые коды строками (`"6"`, `"7"`), в ответе статус приходит
> текстом (`CANCELED`). Таблица соответствия кодов в документации отсутствует.
> `createdAt` в выгрузке — формат `YYYY-MM-DD HH:MM:SS`, без таймзоны, в отличие от ISO
> в остальных ручках.

---

## 4. Балансы

### `GET /balance/all`

```json
[
  {"amount": 15000.5, "currency": "RUB"},
  {"amount": 200, "currency": "USDT", "frozenBalance": 500}
]
```

`frozenBalance` есть не у всех валют — опциональное поле.

---

## 5. Рекуррентные СБП-подписки

Вы один раз создаёте подписку и отправляете плательщика на платёжную форму. Привязку,
активацию и все списания выполняет Platega, вам приходят callback'и; баланс пополняется
по каждому успешному списанию.

Плательщик на форме вводит email и подтверждает привязку счёта в банке (СБП/НСПК) —
подписка становится `Active`, дальше сумма списывается автоматически каждый период.
Вызывать ничего не нужно.

### 5.1 `POST /transaction/process` — создать подписку

Тот же путь, что у обычного платежа, отличает `paymentMethod: 6`.

```json
{
  "paymentMethod": 6,
  "paymentDetails": {
    "amount": 500,
    "currency": "RUB",
    "interval": 3
  },
  "description": "Premium подписка"
}
```

- `amount` — сумма одного регулярного списания.
- `interval` — SubscriptionInterval (1 день / 2 неделя / 3 месяц / 4 год).
- `description` показывается плательщику на форме и в email-уведомлениях.

**Ответ 200**

```json
{
  "paymentMethod": "Subscription",
  "transactionId": "11111111-1111-1111-1111-111111111111",
  "redirect": "https://pay.platega.io/subscription/11111111-...",
  "status": "PENDING",
  "merchantId": "22222222-2222-2222-2222-222222222222"
}
```

**Критично**

- `transactionId` здесь — это **ID подписки** (`subscriptionId`). Сохранить: по нему
  приходят callback'и и работают все ручки ниже.
- Денежная транзакция на этом шаге **не создаётся** — транзакции появляются позже,
  по каждому списанию.
- Плательщика отправлять на `redirect` сразу: на подтверждение привязки даётся
  **30 минут**, после чего подписка переходит в `Failed`.

### 5.2 `GET /subscription/{subscriptionId}`

```json
{
  "id": "11111111-1111-1111-1111-111111111111",
  "status": "Active",
  "amount": 100,
  "currencyCode": "RUB",
  "intervalUnit": "Month",
  "intervalCount": 1,
  "startAt": "2026-07-08T09:00:00Z",
  "nextChargeAt": "2026-08-09T09:10:00Z",
  "lastChargeAt": "2026-07-09T09:10:00Z",
  "description": "Premium подписка",
  "createdAt": "2026-07-08T09:00:00Z",
  "customerEmail": "payer@example.com",
  "chargeMetrics": {
    "chargesTotal": 1,
    "chargesSuccess": 1,
    "chargesFailed": 0,
    "totalAmount": 100,
    "lastChargeAt": "2026-07-09T09:10:00Z",
    "nextChargeAt": "2026-08-09T09:10:00Z"
  }
}
```

`404` — не найдена.

### 5.3 `GET /subscription` — список

Query (все опциональные): `status`, `from`, `to`, `page`, `size`.
Даты URL-encoded ISO: `2026-07-01T00%3A00%3A00.000Z`.

```json
{
  "items": [
    {
      "id": "480bc68d-8114-4af1-9637-2ca73e5cfdfc",
      "status": 4,
      "amount": 100,
      "currencyCode": "RUB",
      "intervalUnit": 3,
      "intervalCount": 1,
      "nextChargeAt": null,
      "lastChargeAt": null,
      "customerEmail": null,
      "description": "Подписка",
      "chargesCount": 0,
      "createdAt": "2026-07-14T13:23:16.164247Z"
    }
  ],
  "total": 2,
  "page": 1,
  "size": 20
}
```

> ⚠️ **Расхождение схем.** В списке `status` и `intervalUnit` приходят **числами**,
> в детальной ручке — **строками** (`"Active"`, `"Month"`). Поля `nextChargeAt`,
> `lastChargeAt`, `customerEmail` могут быть `null`. Нужен нормализатор, а не общий
> типизированный маппер на оба ответа.

---

## 6. Callback'и (вебхуки)

URL задаётся в ЛК: Настройки → Callback URLs. Platega шлёт заголовки `X-MerchantId`
и `X-Secret` и JSON-тело.

**Ретраи.** Если успешный ответ не получен за 60 секунд, запрос отменяется, затем до
3 повторных попыток с интервалом 5 минут. Отвечать `200` быстро, обработку — в фон.

**Требования к URL**

- только HTTPS, HTTP запрещён;
- только публичные IP или доменные имена;
- валидный SSL от доверенного УЦ, self-signed не допускается;
- запрещены `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `127.0.0.0/8`,
  а также localhost и loopback.

### 6.1 Callback по транзакции

```json
{
  "id": "00000000-0000-0000-0000-000000000000",
  "amount": 1000,
  "currency": "RUB",
  "status": "CONFIRMED",
  "paymentMethod": 2,
  "payload": ""
}
```

`CONFIRMED` — оплата прошла, `CANCELED` — не прошла, `CHARGEBACKED` — возврат средств.

### 6.2 Callback по списанию подписки

Приходит на **каждое** списание, успешное и неуспешное.

```json
{
  "Id": "33333333-3333-3333-3333-333333333333",
  "Amount": 100,
  "Currency": "RUB",
  "Status": "CONFIRMED",
  "PaymentMethod": 6,
  "Payload": "",
  "SubscriptionId": "11111111-1111-1111-1111-111111111111",
  "NextChargeAt": "2026-08-09T09:10:00Z"
}
```

- `Id` — ID транзакции-списания, **новый на каждое списание**; идемпотентность строить по нему.
- `CONFIRMED` — деньги списаны, баланс пополнен на сумму за вычетом комиссии.
- `CANCELED` — списание не прошло: баланс не меняется, `NextChargeAt` = `null`,
  подписка переходит в `PastDue`, провайдер будет повторять попытки.

> ⚠️ Ключи здесь **с заглавной буквы** (`Id`, `Amount`, `Status`), а в обычном callback'е —
> со строчной. Один общий парсер работать не будет.

---

## 7. Payout API (выводы)

Подключается по запросу, по умолчанию недоступен. После подключения в ЛК появляется
раздел Payout API.

**Ключ.** Запросы подписываются секретным ключом (SECRET), выдаётся через ЛК и хранится
только у вас — Platega не имеет к нему доступа после выдачи. Показывается **один раз**
сразу после генерации, повторно посмотреть невозможно.

**Сброс.** Через раздел Payout API в ЛК с подтверждением кодом из email. Новый ключ тоже
показывается один раз. Сброс немедленно инвалидирует старый — запросы, подписанные им,
начнут возвращать ошибку аутентификации.

### 7.1 Подпись HMAC-SHA256

Строка для подписи, элементы через `\n`:

```
METHOD\nPATH\ntimestamp\nidempotency-key\nsha256_hex(body)
```

- `timestamp` — unix-время в секундах, окно приёма ±300 секунд;
- `idempotency-key` — уникальная строка на каждый вывод (UUID), она же в заголовке
  `Idempotency-Key`; для GET не используется и в строке подписи передаётся пустой;
- `sha256_hex(body)` — SHA-256 от тела, hex строчными. Для GET тела нет, хеш пустой строки:
  `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`.

Подпись: `Base64(HMAC-SHA256(SECRET, string_to_sign))`
Заголовок: `Authorization: PG-HMAC kid={MERCHANT_ID}, ts={timestamp}, sig={подпись}`

> ⚠️ Тело сериализуется без лишних пробелов, и **те же байты** идут и в подпись, и в запрос.
> В `requests` передавать `data=body_bytes`, а не `json=body`, иначе подпись не сойдётся.

### 7.2 `POST /api/v1/payouts/card-rub`

Заголовки: `Authorization`, `Idempotency-Key`, `Content-Type: application/json`.

| Поле | Тип | Обяз. | Описание |
|---|---|---|---|
| `cardId` | string | нет | ID сохранённой карты (альтернатива `cardNumber`) |
| `cardNumber` | string | нет | PAN получателя, 16 цифр |
| `amountRub` | integer | да | Сумма в рублях, от 1000 до 87500 |
| `payoutMethod` | string | да | Всегда `CARD` |
| `currencyRequested` | string | да | Всегда `RUB` |

Передаётся либо `cardId`, либо `cardNumber`.

```json
{
  "withdrawalRecordId": "3c0d321d-40c4-46e3-97f0-7a8f50ce03a6",
  "status": "CREATED",
  "cardMasked": "**** 0000",
  "amountUsdtDebited": 13.270341
}
```

`amountUsdtDebited` — сумма, списанная с USDT-баланса мерчанта.

### 7.3 `GET /api/v1/cards`

Query `onlyActive`, по умолчанию `true`. При `false` вернёт также `DISABLED` и `PENDING`.
Авторизация — тот же PG-HMAC.

```json
[
  {
    "cardId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "masked": "•••• •••• •••• 4242",
    "last4": "4242",
    "brand": "Visa",
    "label": "Основная карта",
    "status": "ACTIVE"
  }
]
```

### 7.4 Пример подписи (Python)

```python
import base64, hashlib, hmac, json, os, time, uuid
import requests

MERCHANT_ID = os.environ["PLATEGA_MERCHANT_ID"]
SECRET      = os.environ["PLATEGA_PAYOUT_SECRET"]
BASE        = "https://app.platega.io"
PATH        = "/api/v1/payouts/card-rub"

body = {
    "cardNumber": "2200000000000000",   # или "cardId": "<uuid>"
    "amountRub": 1500,                  # 1000..87500
    "payoutMethod": "CARD",
    "currencyRequested": "RUB",
}

idem_key   = str(uuid.uuid4())
body_bytes = json.dumps(body, separators=(",", ":")).encode("utf-8")
ts         = int(time.time())

body_hash      = hashlib.sha256(body_bytes).hexdigest()
string_to_sign = "\n".join(["POST", PATH, str(ts), idem_key, body_hash])

sig = base64.b64encode(
    hmac.new(SECRET.encode(), string_to_sign.encode(), hashlib.sha256).digest()
).decode("ascii")

headers = {
    "Authorization":   f"PG-HMAC kid={MERCHANT_ID}, ts={ts}, sig={sig}",
    "Idempotency-Key": idem_key,
    "Content-Type":    "application/json",
}

# data=, не json= — иначе байты не совпадут с подписанными
resp = requests.post(BASE + PATH, headers=headers, data=body_bytes, timeout=30)
```

---

## 8. Готовые SDK и модули CMS

**SDK**

- PHP: `https://sdk-s.plategadrive.com/platega-sdk-php.zip`
- Python: `https://sdk-s.plategadrive.com/platega-sdk-python.zip`

**Модули CMS** — база `https://platega-modules.plategadrive.com/<файл>`:
`Simpla.zip`, `HopeBilling.zip`, `WHCMS.zip` (WHMCS — обратите внимание на порядок букв
в имени файла), `DLE.zip`, `XenForo.zip`, `Opencart.zip`, `BillManager.zip`,
`Joomla-JoomShopping5.zip`, `WooCommerce.zip`

---

## 9. Чек-лист интеграции

- [ ] Ключи в переменных окружения, не в коде и не в репозитории.
- [ ] Не передавать `id` при создании транзакции.
- [ ] Выяснить у менеджера, обязателен ли `metadata.userId` для вашей категории.
- [ ] Callback-эндпоинт: HTTPS, валидный сертификат, публичный адрес, ответ `200` за <60 с.
- [ ] Проверка `X-MerchantId` / `X-Secret` во входящих callback'ах до обработки.
- [ ] Идемпотентность обработки callback'ов по `id` / `Id` (до 3 ретраев).
- [ ] Раздельные парсеры: callback платежа (строчные ключи) и подписки (заглавные).
- [ ] Нормализация `status` / `intervalUnit` подписок: число в списке, строка в детальной ручке.
- [ ] Обработка `null` в `nextChargeAt` / `lastChargeAt` / `customerEmail`.
- [ ] `paymentDetails` в ответе на создание платежа — и строка, и объект.
- [ ] Редирект на подписочную форму сразу — окно 30 минут до `Failed`.
- [ ] Payouts: новый `Idempotency-Key` на каждый вывод, `data=` вместо `json=`.
- [ ] `GET /h2h/{id}` отдаёт `400`, а не `404`, при отсутствии транзакции.

---

## 10. Не покрыто документацией

Этого в исходных материалах нет — уточнять у менеджера, не додумывать:

- отмена подписки (эндпоинт не документирован);
- числовые коды статусов для фильтра `statuses` в выгрузках;
- подпись/верификация входящих callback'ов помимо заголовков `X-MerchantId` / `X-Secret`;
- rate limits;
- тестовое окружение / песочница.
