# Подпроект C — платежи и выдача: план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** закрыть все подтверждённые дефекты денежного пути — чтобы ни один оплаченный
рубль не терялся молча и ни один товар не уходил бесплатно.

**Architecture:** правки точечные, в существующих модулях. Никакой перестройки: она
относится к подпроекту B. Каждый дефект сопровождается тестом, воспроизводящим его на
моках, без обращения к боевым провайдерам и БД.

**Tech Stack:** Python 3.11, aiogram 3.30, FastAPI, asyncpg, pytest + pytest-asyncio.

## Global Constraints

- Запуск всего только через `.venv/bin/python` (система несёт Python 3.9.6).
- Baseline до правок: **39 упавших тестов из 279**, ruff 6 ошибок. Рост = регрессия.
- Схему БД не трогаем: две конкурирующие системы миграций разводятся в подпроекте B.
- Никаких новых зависимостей.
- Тесты не ходят в сеть и в БД — только моки.
- Сообщения коммитов на русском, тело описывает причину, не только действие.

---

### Task 0: Починить сравнение naive и aware datetime

Это первым: пока 25+ тестов падают по одной причине, мы не увидим собственные регрессии.

**Files:**
- Modify: `app/services/subscriptions/service.py` — `parse_expires_at`
- Test: `tests/services/test_subscriptions.py` (существует, сейчас падает)

**Interfaces:**
- Produces: `parse_expires_at(value) -> Optional[datetime]` — всегда timezone-aware в UTC.

- [ ] **Шаг 1. Убедиться, что тесты падают именно по этой причине**

Run: `.venv/bin/python -m pytest tests/services/test_subscriptions.py -q 2>&1 | tail -5`
Expected: FAIL, `TypeError: can't compare offset-naive and offset-aware datetimes`

- [ ] **Шаг 2. Прочитать `parse_expires_at` и увидеть, где теряется tzinfo**

Run: `grep -n "def parse_expires_at" -A 40 app/services/subscriptions/service.py`

- [ ] **Шаг 3. Привести результат к UTC-aware**

В `parse_expires_at`, в каждой ветке, возвращающей datetime, добавить нормализацию:

```python
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)
    return parsed
```

Naive-значение трактуется как UTC: именно так пишет БД после миграции TIMESTAMPTZ.

- [ ] **Шаг 4. Прогнать тесты подписок и триалов**

Run: `.venv/bin/python -m pytest tests/services/test_subscriptions.py tests/services/test_trials.py -q 2>&1 | tail -5`
Expected: PASS

- [ ] **Шаг 5. Прогнать весь набор и сравнить с baseline**

Run: `.venv/bin/python -m pytest -q 2>&1 | tail -3`
Expected: упавших строго меньше 39, выросших падений нет

- [ ] **Шаг 6. Коммит**

```bash
git add app/services/subscriptions/service.py
git commit -m "fix: parse_expires_at всегда возвращает aware-datetime в UTC"
```

---

### Task 1: Проверка подписи вебхука Lava

Сейчас `_verify_webhook_signature` определена и не вызывается ни разу. Эндпоинт
публичный: POST с телом `{"order_id": "purchase_<id>", "status": "success"}` активирует
подписку без оплаты. Воспроизведено PoC на этапе аудита.

**Files:**
- Modify: `lava_service.py:195` — `process_webhook_data` принимает сырое тело и проверяет подпись
- Modify: `lava_service.py:183` — отсутствующий ключ больше не означает «пропустить проверку»
- Modify: `app/api/payment_webhook.py:190` — читать сырые байты и передавать их
- Test: `tests/test_webhook_signatures.py` (существует, для Lava тестов нет ни одного)

**Interfaces:**
- Consumes: `_verify_webhook_signature(body_bytes: bytes, received_sig: str) -> bool`
- Produces: `process_webhook_data(headers: dict, body: dict, bot: Bot, raw_body: bytes) -> dict`

- [ ] **Шаг 1. Написать падающие тесты**

```python
class TestLavaWebhookAuth:
    """Вебхук Lava обязан отвергать запрос без корректной подписи."""

    async def test_missing_signature_rejected(self, monkeypatch):
        import lava_service
        monkeypatch.setattr(lava_service, "LAVA_SIGN_KEY", "secret", raising=False)
        body = {"order_id": "purchase_deadbeef", "status": "success", "amount": 1599}
        result = await lava_service.process_webhook_data(
            headers={}, body=body, bot=None, raw_body=json.dumps(body).encode(),
        )
        assert result["status"] == "unauthorized"

    async def test_wrong_signature_rejected(self, monkeypatch):
        import lava_service
        monkeypatch.setattr(lava_service, "LAVA_SIGN_KEY", "secret", raising=False)
        body = {"order_id": "purchase_deadbeef", "status": "success", "amount": 1599}
        result = await lava_service.process_webhook_data(
            headers={"authorization": "0" * 64}, body=body, bot=None,
            raw_body=json.dumps(body).encode(),
        )
        assert result["status"] == "unauthorized"

    async def test_unconfigured_key_rejects_instead_of_allowing(self, monkeypatch):
        """Отсутствие ключа не должно открывать вебхук настежь."""
        import lava_service
        monkeypatch.setattr(lava_service, "LAVA_SIGN_KEY", "", raising=False)
        body = {"order_id": "purchase_deadbeef", "status": "success", "amount": 1599}
        result = await lava_service.process_webhook_data(
            headers={"authorization": "whatever"}, body=body, bot=None,
            raw_body=json.dumps(body).encode(),
        )
        assert result["status"] == "unauthorized"
```

- [ ] **Шаг 2. Запустить и убедиться, что падают**

Run: `.venv/bin/python -m pytest tests/test_webhook_signatures.py -k Lava -q`
Expected: FAIL — сейчас подпись не проверяется, вернётся не `unauthorized`

- [ ] **Шаг 3. Сделать проверку обязательной**

В `_verify_webhook_signature` заменить «нет ключа — пропускаем» на отказ:

```python
def _verify_webhook_signature(body_bytes: bytes, received_sig: str) -> bool:
    if not LAVA_SIGN_KEY:
        logger.error("Lava webhook: LAVA_SIGN_KEY не настроен — запрос отклонён")
        return False
    if not received_sig:
        return False
    expected = hmac.new(LAVA_SIGN_KEY.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, received_sig)
```

- [ ] **Шаг 4. Вызвать проверку до любых обращений к БД**

В `process_webhook_data` сменить сигнатуру на
`(headers: dict, body: dict, bot: Bot, raw_body: bytes)` и сразу после проверки
`is_enabled()` вставить:

```python
    signature = headers.get("authorization", "")
    if not _verify_webhook_signature(raw_body, signature):
        logger.error(
            "Lava webhook: подпись не прошла проверку, order_id=%s", body.get("order_id")
        )
        return {"status": "unauthorized"}
```

- [ ] **Шаг 5. Передать сырое тело из эндпоинта**

В `app/api/payment_webhook.py:190` читать байты до разбора JSON:

```python
        raw_body = await request.body()
        headers = {k.lower(): v for k, v in request.headers.items()}
        try:
            body = json.loads(raw_body)
        except Exception as e:
            logger.error(f"Lava webhook: invalid JSON: {e}")
            await _log_pe("webhook_invalid_json", "lava", error_message=str(e)[:300])
            return JSONResponse({"status": "invalid"}, status_code=400)

        result = await asyncio.wait_for(
            lava_service.process_webhook_data(headers, body, _bot, raw_body),
            timeout=_WEBHOOK_TIMEOUT,
        )
```

Убедиться, что `import json` в модуле есть; если нет — добавить.

- [ ] **Шаг 6. Тесты проходят**

Run: `.venv/bin/python -m pytest tests/test_webhook_signatures.py -q`
Expected: PASS

- [ ] **Шаг 7. Перепроверка**

Найти все вызовы `process_webhook_data` и убедиться, что ни один не остался со старой
сигнатурой:

Run: `grep -rn "process_webhook_data" --include='*.py' . | grep -v '.venv'`

- [ ] **Шаг 8. Коммит**

```bash
git add lava_service.py app/api/payment_webhook.py tests/test_webhook_signatures.py
git commit -m "fix(lava): проверять подпись вебхука — раньше подписку выдавал любой POST"
```

**Развёртывание:** переменная `LAVA_SIGN_KEY` обязана быть выставлена в Railway.
Без неё вебхук теперь отвечает `unauthorized` — это осознанный выбор в пользу отказа,
а не тихого пропуска.

---

### Task 2: Маршрутизация оплаты Spotify картой

`process_successful_payment` имеет ветки для gift (568), telegram_premium (614),
telegram_stars (643), steam (671), apple_id (699), traffic_pack (720). Ветки для
spotify нет, поэтому покупка проваливается на строку 843 и финализируется как
VPN-подписка: деньги списаны, Spotify не выдан, админ заказа не видит.

**Files:**
- Modify: `app/handlers/payments/payments_messages.py` — добавить ветку перед строкой 843
- Test: `tests/services/test_payments.py`

**Interfaces:**
- Consumes: `send_spotify_success(bot, telegram_id, purchase_id, pending)` из
  `app/handlers/payments/spotify_purchase.py` — уже используется в
  `app/services/payments/confirmation.py:102`.

- [ ] **Шаг 1. Проверить фактическую сигнатуру `send_spotify_success`**

Run: `grep -n "async def send_spotify_success" -A 12 app/handlers/payments/spotify_purchase.py`

- [ ] **Шаг 2. Написать падающий тест**

```python
async def test_spotify_card_payment_does_not_finalize_subscription(monkeypatch):
    """Покупка Spotify картой не должна уходить в ветку VPN-подписки."""
    from app.handlers.payments import payments_messages as pm

    called = {"subscription": False, "spotify": False}

    async def fake_finalize(**kwargs):
        called["subscription"] = True
        raise AssertionError("Spotify не должен финализироваться как подписка")

    async def fake_spotify(*args, **kwargs):
        called["spotify"] = True

    monkeypatch.setattr(pm.payment_service, "finalize_subscription_payment", fake_finalize)
    monkeypatch.setattr(pm, "send_spotify_success", fake_spotify, raising=False)

    # покупка с purchase_type='spotify' должна уйти в ветку Spotify
    assert called["subscription"] is False
```

Тест дописывается по фактической структуре функции после шага 1: в ней много внешних
зависимостей, поэтому мокаются `database.get_pending_purchase_by_id`,
`database.mark_pending_purchase_paid` и отправка сообщений.

- [ ] **Шаг 3. Запустить, убедиться в падении**

Run: `.venv/bin/python -m pytest tests/services/test_payments.py -k spotify -q`
Expected: FAIL

- [ ] **Шаг 4. Добавить ветку по образцу steam (строка 671)**

Перед строкой 843 (`# Finalize subscription payment`) вставить обработку
`purchase_type == "spotify"` либо `tariff.startswith("spotify_")`: пометить покупку
оплаченной через `database.mark_pending_purchase_paid`, вызвать `send_spotify_success`,
залогировать и выйти из функции — ровно так, как это делает ветка steam.

- [ ] **Шаг 5. Тест проходит**

Run: `.venv/bin/python -m pytest tests/services/test_payments.py -k spotify -q`
Expected: PASS

- [ ] **Шаг 6. Перепроверка — не сломалась ли ветка steam**

Run: `.venv/bin/python -m pytest tests/services/test_payments.py -q`

- [ ] **Шаг 7. Коммит**

```bash
git add app/handlers/payments/payments_messages.py tests/services/test_payments.py
git commit -m "fix(spotify): оплата картой больше не финализируется как VPN-подписка"
```

---

### Task 3: Сумма оплаты в Telegram Stars

`payments_messages.py:551`:
`payment_amount_rubles = payment.total_amount if is_stars_payment else payment.total_amount / 100.0`

Для XTR `total_amount` — это количество звёзд, а не рубли. Значение уходит в
`finalize_subscription_payment` как сумма в рублях: выручка и реферальный кешбэк
считаются от числа звёзд.

**Files:**
- Modify: `app/handlers/payments/payments_messages.py:551`
- Test: `tests/services/test_payments.py`

- [ ] **Шаг 1. Выяснить, какая рублёвая цена известна на этот момент**

Run: `grep -n "price_kopecks\|TARIFFS_STARS" app/handlers/payments/payments_messages.py | head -20`

Ожидание: у `pending_purchase` есть `price_kopecks` — цена в копейках, зафиксированная
при создании покупки. Именно она и есть настоящая рублёвая сумма.

- [ ] **Шаг 2. Написать падающий тест**

```python
def test_stars_payment_amount_uses_purchase_price_not_star_count():
    """Оплата 150 звёзд за тариф 499 ₽ должна записать 499 ₽, а не 150."""
    pending = {"price_kopecks": 49900, "purchase_type": "subscription"}
    amount = resolve_payment_amount_rubles(
        total_amount=150, is_stars=True, pending_purchase=pending
    )
    assert amount == 499.0


def test_card_payment_amount_still_converts_kopecks():
    pending = {"price_kopecks": 49900, "purchase_type": "subscription"}
    amount = resolve_payment_amount_rubles(
        total_amount=49900, is_stars=False, pending_purchase=pending
    )
    assert amount == 499.0
```

- [ ] **Шаг 3. Запустить, убедиться в падении**

Run: `.venv/bin/python -m pytest tests/services/test_payments.py -k amount -q`
Expected: FAIL — функции ещё нет

- [ ] **Шаг 4. Вынести расчёт в чистую функцию**

В `payments_messages.py` рядом с местом использования:

```python
def resolve_payment_amount_rubles(
    total_amount: int, is_stars: bool, pending_purchase: dict | None
) -> float:
    """Рублёвая сумма платежа.

    Для Stars total_amount — количество звёзд, а не рубли, поэтому берём цену,
    зафиксированную при создании покупки. Для карты total_amount в копейках.
    """
    if is_stars:
        price_kopecks = (pending_purchase or {}).get("price_kopecks")
        if price_kopecks:
            return price_kopecks / 100.0
        logger.error(
            "STARS_PRICE_MISSING: не найдена price_kopecks, выручка будет занижена, "
            "stars=%s", total_amount,
        )
        return float(total_amount)
    return total_amount / 100.0
```

- [ ] **Шаг 5. Заменить строку 551 вызовом функции**

- [ ] **Шаг 6. Тесты проходят**

Run: `.venv/bin/python -m pytest tests/services/test_payments.py -q`
Expected: PASS

- [ ] **Шаг 7. Перепроверка сопряжённых находок**

Комбо-тариф по Stars и сгорающий промокод (`payments_callbacks.py:1013` и `:1034`) —
отдельные находки, не перепроверенные. Убедиться чтением, что правка суммы их не
затрагивает, и записать результат.

- [ ] **Шаг 8. Коммит**

```bash
git add app/handlers/payments/payments_messages.py tests/services/test_payments.py
git commit -m "fix(stars): записывать рублёвую цену покупки вместо количества звёзд"
```

---

### Task 4: Разделить причины ValueError в обработке вебхука

`confirmation.py:177` ловит любой `ValueError` и трактует его как «уже обработано»:
возвращает провайдеру HTTP 200 и не поднимает алерт. Под это правило попадают четыре
разные причины из `finalize_purchase`, и только одна из них действительно означает
повтор:

- `purchase_not_found_or_locked` (`subscriptions.py:4449`)
- `already_processed` (4457) — единственная законная
- `invalid_status` (4461)
- `PAYMENT_AMOUNT_MISMATCH` (4487) — платёж с неверной суммой теряется молча

**Files:**
- Modify: `database/subscriptions.py` — ввести типизированные исключения
- Modify: `app/services/payments/confirmation.py:177`
- Test: `tests/services/test_payments.py`

**Interfaces:**
- Produces: `PaymentAlreadyProcessed`, `PaymentAmountMismatch`, `PurchaseLocked`,
  `PurchaseInvalidStatus` — все наследуют `ValueError`, чтобы существующие
  обработчики выше по стеку не сломались.

- [ ] **Шаг 1. Написать падающий тест**

```python
async def test_amount_mismatch_alerts_admin_and_does_not_report_success(monkeypatch):
    """Расхождение суммы — не повод отвечать провайдеру 'already_processed'."""
    from app.services.payments import confirmation

    alerts = []

    async def fake_alert(*args, **kwargs):
        alerts.append(kwargs)

    monkeypatch.setattr(confirmation, "alert_payment_failure", fake_alert, raising=False)
    # finalize_purchase поднимает PaymentAmountMismatch
    result = await confirmation.process_confirmed_payment(...)
    assert result["status"] != "already_processed"
    assert alerts, "админ обязан получить алерт о расхождении суммы"
```

- [ ] **Шаг 2. Запустить, убедиться в падении**

Run: `.venv/bin/python -m pytest tests/services/test_payments.py -k mismatch -q`
Expected: FAIL

- [ ] **Шаг 3. Ввести классы исключений**

В `database/subscriptions.py` рядом с `finalize_purchase`:

```python
class PaymentAlreadyProcessed(ValueError):
    """Покупка уже финализирована — законный повтор вебхука."""


class PaymentAmountMismatch(ValueError):
    """Сумма платежа не совпала с ценой покупки."""


class PurchaseLocked(ValueError):
    """Покупка не найдена или заблокирована параллельной обработкой."""


class PurchaseInvalidStatus(ValueError):
    """Покупка в статусе, из которого её нельзя финализировать."""
```

- [ ] **Шаг 4. Заменить четыре `raise ValueError` на соответствующие классы**

Строки 4449, 4457, 4461, 4487 — по одному классу на причину, тексты сообщений сохранить.

- [ ] **Шаг 5. Разнести обработку в `confirmation.py`**

`except PaymentAlreadyProcessed` — прежнее поведение с ресинком и ответом
`already_processed`. `except PaymentAmountMismatch` — алерт админу и ответ со статусом
`error` без «успеха». `except (PurchaseLocked, PurchaseInvalidStatus)` — алерт и отказ.

- [ ] **Шаг 6. Тесты проходят**

Run: `.venv/bin/python -m pytest tests/services/test_payments.py -q`
Expected: PASS

- [ ] **Шаг 7. Перепроверка**

`payment_webhook.py:208` ловит `ValueError` и отвечает 200 `already_processed`. Убедиться,
что новые классы не проваливаются туда мимо новой логики: они наследуют `ValueError`,
поэтому обработка в `confirmation.py` обязана быть строго выше по стеку.

- [ ] **Шаг 8. Прогон всего набора и сравнение с baseline**

Run: `.venv/bin/python -m pytest -q 2>&1 | tail -3`

- [ ] **Шаг 9. Коммит**

```bash
git add database/subscriptions.py app/services/payments/confirmation.py tests/services/test_payments.py
git commit -m "fix(payments): различать причины отказа финализации вместо общего ValueError"
```

---

### Task 5: Итоговая сверка подпроекта C

- [ ] **Шаг 1. Полный прогон**

Run: `.venv/bin/python -m pytest -q 2>&1 | tail -3`
Expected: упавших меньше 39, новых падений нет

- [ ] **Шаг 2. Статический анализ**

Run: `.venv/bin/python -m ruff check . --exclude .venv --exclude graphify-out`
Expected: не больше 6 ошибок, F823 в payments_messages устранён

- [ ] **Шаг 3. Отметить закрытые находки в реестре**

Проставить `"status": "fixed"` и номер коммита для закрытых записей в
`docs/audit-2026-07/findings.json`, перегенерировать отчёты.

- [ ] **Шаг 4. Коммит**

```bash
git add docs/audit-2026-07/
git commit -m "docs: отметить закрытые находки подпроекта C"
```

## Что в этот подпроект НЕ входит

- Наценка СБП 11% (`payments_callbacks.py:1677`), комбо-тариф по Stars (`:1013`),
  сгорающий промокод (`:1034`) — не перепроверены; сначала подтверждение чтением кода,
  затем отдельные задачи.
- Недостижимый сценарий вывода средств — относится к подпроекту D.
- Разделение мега-файлов `database/subscriptions.py` и `database/admin.py` — подпроект B.
