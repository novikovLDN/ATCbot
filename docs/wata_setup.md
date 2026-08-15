# Wata (wata.pro) — настройка и диагностика

## Что это
Платёжная система Wata — прямая замена Platega/YooKassa/Lava. Один
endpoint `POST /links` создаёт payment link (карта / СБП / T-Pay), Wata
шлёт webhook когда юзер оплатил.

## Быстрый чек-лист (что должно быть настроено)

### 1. Переменные окружения (`.env` / деплой-конфиг)
```
WATA_ACCESS_TOKEN=<Bearer JWT из кабинета Wata → API tokens>
WATA_SANDBOX=false           # true = api-sandbox.wata.pro (тесты)
PUBLIC_BASE_URL=https://your-domain.com   # HTTPS обязателен
```

Проверить что подтянулось: `/wata_status` (админ-команда в боте) —
покажет `token_len`, `sandbox`, `api_url`, `enabled`.

### 2. Личный кабинет Wata → Webhooks
- **URL**: `{PUBLIC_BASE_URL}/webhooks/wata`
  - Пример: `https://atlassecure.uk/webhooks/wata`
- **Метод**: POST
- **События** (минимум): `Payment.Paid`, `Payment.Declined`
- **HTTPS обязателен** — по http Wata не шлёт
- **Подпись**: RSA-SHA512, публичный ключ бот сам получает через
  `GET /public-key` (кэшируется в памяти)

### 3. Проверка что endpoint жив
```bash
curl -X POST https://your-domain.com/webhooks/wata \
     -H 'Content-Type: application/json' \
     -d '{"transactionStatus":"Paid","orderId":"test"}'
```
Должен вернуть `{"status":"invalid_signature"}` (200/400 — но НЕ 404 и НЕ 502).
Если 404 — проблема в роутинге / reverse-proxy.
Если 502/504 — бот не поднят или не проброшен порт.

## Почему webhook мог не дойти после реальной оплаты

| Симптом | Причина | Как чинить |
|---|---|---|
| В логах бота нет `Wata webhook:` | Wata не отправила | Проверить регистрацию URL в кабинете Wata |
| `signature verification failed` | Middleware пересобрал JSON | Убедиться что `raw_body` не менялся до вызова verify |
| `already_processed` | Дубль webhook'а | Норма, Wata может слать несколько раз |
| `no public key — skipping signature check` | `api.wata.pro/public-key` недоступен | Проверить сеть до api.wata.pro из контейнера |
| `pending not found for order=X` | orderId разошёлся с БД | Проверить `pending_purchases.purchase_id` = orderId |
| В логах нет ничего | Reverse-proxy отфильтровал | nginx/traefik логи `/webhooks/wata` |

**Команда для быстрого grep в проде:**
```bash
grep -i "wata" /var/log/bot.log | tail -50
```

## Тестовая оплата (диагностика)

1. **Sandbox**: поставить `WATA_SANDBOX=true`, использовать тестовые
   карты из докой Wata → `4111 1111 1111 1111` и т.п.
2. **Прод, малая сумма**: 10 RUB минимум (Wata min).
3. Смотреть в логи бота `PLATEGA_SHIM_TO_LAVA` или `Wata webhook:` —
   первое = shim работает, второе = webhook пришёл.

## Endpoints в проекте

| Что | Где |
|---|---|
| Клиент API | `wata_service.py` |
| Webhook handler | `app/api/payment_webhook.py` (`/webhooks/wata`) |
| Кнопки Wata в UI | `payments_callbacks.py`, `traffic.py`, `steam_purchase.py`, `spotify_purchase.py`, `gift.py`, `telegram_stars_purchase.py`, `navigation.py` (Apple) |
| Финализация оплаты | `app/services/payments/confirmation.py` (`process_confirmed_payment`) |
| Diagnostic-команда | `/wata_status` (admin only) — `app/handlers/admin/base.py` |

## Rollback: скрыть Wata обратно на admin-only

В `wata_service.py::is_visible_to()` раскомментировать блок
«rollback: только админ видит Wata» и удалить `return is_enabled()`.
Rebuild + restart. Юзеры увидят прежние платёжки без Wata-кнопок.
