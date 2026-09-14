# WATA API — выжимка для интеграции

> Источник: https://wata.pro/api (руководство пользователя: https://wata.pro/userguide). Выжимка сделана 2026-09-13.
> Используется как эталон для сверки `wata_service.py` и `app/workers/wata_reconciler.py`.

## Базовые URL и авторизация

- Prod: `https://api.wata.pro/api/h2h/`
- Sandbox: `https://api-sandbox.wata.pro/api/h2h/`
- Авторизация: заголовок `Authorization: Bearer <access-token>` (JWT). Токен живёт 1–12 месяцев; после истечения API отвечает `401`.

## Создание платёжной ссылки

`POST /api/h2h/links/`

| Поле | Обяз. | Описание |
|---|---|---|
| `amount` | да | Число, 2 знака после запятой, **в рублях, не в копейках**. От 10 RUB / 1 USD / 1 EUR до 999 999.99 |
| `currency` | да | `RUB` / `EUR` / `USD` |
| `type` | нет | `OneTime` (по умолчанию) / `ManyTime` |
| `orderId` | нет | ID заказа мерчанта |
| `description` | нет | |
| `successRedirectUrl` / `failRedirectUrl` | нет | |
| `expirationDateTime` | нет | По умолчанию 3 дня; от 10 минут до 30 дней |
| `isArbitraryAmountAllowed`, `arbitraryAmountPrompts` | нет | Произвольная сумма |

Ответ: `{ id (UUID), url, status: Opened|Closed, amount, currency, orderId, expirationDateTime }`.

## Статус транзакции

- `GET /api/h2h/transactions/{transactionId}` возвращает `id`, `status` (`Created|Pending|Paid|Declined`), `orderId`, `amount`, `currency`, `errorCode`, `errorDescription`, `paymentTime`.
- Поиск: `GET /api/h2h/v2/transactions/?orderId=&creationTimeFrom=&creationTimeTo=&amountFrom=&amountTo=&currencies=&statuses=&maxResultCount=` (не больше 1000 результатов).

## Webhook

- **Подпись:** заголовок `X-Signature`, алгоритм **RSA SHA512** (PKCS1) по **сырому JSON-телу**. Публичный ключ отдаёт `GET /api/h2h/public-key`.
- **Тело:** `{ transactionId, transactionStatus: Created|Pending|Paid|Declined, kind: Payment|Refund, orderId, amount, currency, paymentTime, errorCode, errorDescription }`.
- **Типы:** Preauth (до запроса в банк; таймаут 10 с, без ответа транзакция отклоняется), Postpayment (после подтверждения банка; выдаём услугу), Refund.
- **Ответ:** обязательно `200 OK`.
- **Повторы:** для Postpayment таймаут 1 минута, **повторы до 32 часов** с растущим интервалом.

## Возвраты

`POST /api/h2h/transactions/refunds`, тело `{ originalTransactionId, amount (>0, ≤ исходной суммы) }`.

## Лимиты

Только для GET: **1 запрос в 30 секунд на объект/клиента**, при превышении — `429`. Касается `/links`, `/links/{id}`, `/transactions`, `/transactions/{id}`.

## Способы оплаты

Карты (Visa, МИР), СБП, T-Pay, SberPay.
