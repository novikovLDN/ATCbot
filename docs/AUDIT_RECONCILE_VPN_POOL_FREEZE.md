# FULL SYSTEM AUDIT — RECONCILE / VPN API / DB POOL INTERACTION

**Цель:** выявить причину 11-минутного freeze после лога `HTTP Request: GET .../list-users "HTTP/1.1 200 OK"`.  
**Контекст:** после этого лога логи прекращаются, процесс жив, Telegram не отвечает, воркеры выглядят зависшими.  
**Режим:** только диагностика, без предложения фиксов.

---

## STEP 1 — Использование reconcile_xray_state

### Файл: `reconcile_xray_state.py`

**Фоновая задача:** `reconcile_xray_state_task()` (строки 203–291).

- Бесконечный цикл: `while True` → `await asyncio.sleep(RECONCILIATION_INTERVAL_SECONDS)` (600s по умолчанию).
- Одна итерация: `async with _worker_lock:` → `await asyncio.wait_for(reconcile_xray_state(), timeout=RECONCILIATION_TIMEOUT_SECONDS)` (20s).
- При таймауте: `asyncio.TimeoutError`, увеличение счётчика сбоев, circuit breaker при достижении порога.

**Функция `reconcile_xray_state()` (строки 55–200):**

1. **Фаза 1 — снимок БД (keyset):**  
   Цикл `while True` с короткими сессиями:
   - `async with acquire_connection(pool, "reconcile_fetch_db") as conn:` (95–106)  
   - `rows = await conn.fetch(...)`  
   - контекст выходит → **соединение освобождается**.  
   Нет HTTP внутри этого контекста.

2. **Вызов list_vless_users (строка 121):**
   ```python
   xray_uuids = set(await vpn_utils.list_vless_users())
   ```
   Выполняется **вне** любого `acquire_connection` / `pool.acquire()` / транзакции.  
   **Соединение с БД во время этого вызова не держится.**

3. **Фаза 4 — обработка сирот (строки 146–191):**  
   Для каждого UUID:
   - `async with acquire_connection(pool, "reconcile_live_check") as conn:` (156–165) → один `fetchrow`, выход из контекста.
   - Затем **вне** контекста: `await vpn_utils.remove_vless_user(uuid_val)` (184).  
   Т.е. HTTP `remove_vless_user` вызывается **после** освобождения соединения.

**Итог по удержанию БД при HTTP:**

| Участок кода | Держится ли DB во время HTTP? |
|--------------|-------------------------------|
| Цикл keyset (fetch DB) | Нет — только короткий fetch, затем release. |
| `list_vless_users()` (строка 121) | **Нет** — вызывается после выхода из всех `acquire_connection`. |
| Per-orphan live check + remove | Нет — сначала короткий `acquire`/fetch, выход, потом `remove_vless_user`. |

**HOLDING_DB_CONNECTION_DURING_HTTP = False** для всех путей в reconcile.

---

## STEP 2 — Анализ удержания DB-соединения

Проверены все вхождения в `reconcile_xray_state.py`:

- `async with acquire_connection(pool, "reconcile_fetch_db") as conn:` — внутри только `conn.fetch(...)`, HTTP нет.
- `async with acquire_connection(pool, "reconcile_live_check") as conn:` — внутри только `conn.fetchrow(...)`, HTTP после выхода из контекста.

В `vpn_utils.list_vless_users()` (строки 372–400) нет обращения к БД и к `pool`.

**Вывод:** ни один HTTP-вызов (list_vless_users, remove_vless_user) не выполняется при удержанном соединении из пула. Вложенных `acquire` внутри транзакции в этом воркере нет.

---

## STEP 3 — Модель конкуррентности воркера

- **Один экземпляр reconcile:** глобальный `_worker_lock` (asyncio.Lock) — только одна реконсиляция в момент времени.
- **Таймаут итерации:** `asyncio.wait_for(reconcile_xray_state(), timeout=RECONCILIATION_TIMEOUT_SECONDS)` (20s). При превышении — TimeoutError, блок снимается.
- **Параллелизм внутри reconcile:** нет `asyncio.gather` по батчам; цикл по сиротам последовательный (по одному: live check → release → remove).
- **Константы:**
  - `BATCH_SIZE_LIMIT` = 100 (env: `XRAY_RECONCILIATION_BATCH_LIMIT`).
  - `RECONCILIATION_INTERVAL_SECONDS` = 600.
  - `list_vless_users()` вызывается **один раз за итерацию** (строка 121), не по каждому UUID.

---

## STEP 4 — Конфигурация HTTP-клиента

Поиск по репозиторию: `httpx.AsyncClient`.

| Место | Клиент | Timeout | Повтор / breaker |
|-------|--------|--------|-------------------|
| `vpn_utils.check_xray_health` | per-call `AsyncClient(timeout=HTTP_TIMEOUT)` | float | нет |
| `vpn_utils` add_user | per-call `AsyncClient(timeout=HTTP_TIMEOUT)` | float | retry_async, circuit_breaker |
| `vpn_utils.list_vless_users` | per-call `AsyncClient(timeout=HTTP_TIMEOUT)` | float | **нет retry, нет circuit_breaker** |
| `vpn_utils` update_vless_user | per-call `AsyncClient(timeout=HTTP_TIMEOUT)` | float | circuit_breaker, retry внутри обёртки |
| `vpn_utils` remove_vless_user | per-call `AsyncClient(timeout=HTTP_TIMEOUT)` | float | retry_async, circuit_breaker |

**HTTP_TIMEOUT** (vpn_utils.py:36):  
`max(config.XRAY_API_TIMEOUT or 5.0, 3.0)` — один float для всего запроса (connect + read в httpx).

- Отдельные connect/read/pool в коде не заданы — используется один общий timeout.
- Клиент каждый раз новый (`async with httpx.AsyncClient(...)`), переиспользования между вызовами нет.
- В `list_vless_users` **нет** `retry_async` и **нет** вызова circuit breaker (в отличие от add/remove).

**response.json():**  
В `list_vless_users` (строка 393): `data = response.json()` — вызывается после `await client.get(...)`. В httpx при дефолтном (не stream) запросе тело читается внутри `await client.get()`, затем `.json()` только парсит буфер. Теоретически блокировка на 11 минут в `.json()` возможна только при гигантском теле; типичный список UUID — малый объём.

---

## STEP 5 — Риск блокировки при разборе JSON

**list_vless_users (vpn_utils.py 386–397):**

```python
async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
    response = await client.get(url, headers=headers)
    if response.status_code != 200:
        ...
    data = response.json()
```

- `response.json()` используется после `await client.get()`.
- При обычном (не stream) режиме httpx тело уже прочитано к моменту возврата из `get()`; `.json()` — синхронный разбор, быстрый для малого ответа.
- Таймаут (один float) в httpx распространяется на весь запрос, включая чтение тела. Если сервер после 200 OK не отдаёт тело или тянет его очень долго, таймаут должен сработать (порядка HTTP_TIMEOUT, 3–5s).

**Вариант, при котором возможна длительная блокировка:**

- Лог "HTTP Request: GET ... 200 OK" может появляться при получении статуса/заголовков (в т.ч. со стороны транспорта/логирования).
- Если фактическое чтение тела выполняется уже после логирования и по какой-то причине не ограничено тем же timeout (баг/особенность версии/прокси), то ожидание тела может затягиваться.
- Тогда "зависание" будет внутри `await client.get()`, а не в `.json()`.

Итог: **ответ может блокироваться на фазе чтения тела после 200 OK**; при корректном применении timeout — не более нескольких секунд; при сбое применения timeout или особом поведении сервера — теоретически длительная блокировка.

---

## STEP 6 — Конфигурация пула БД

**Файл:** `database.py`, `_get_pool_config()` (253–261).

| Параметр | Значение (default) |
|----------|--------------------|
| min_size | 2 |
| max_size | 15 |
| timeout (acquire_timeout) | 10 |
| command_timeout | 30 |

- Одно соединение постоянно занято advisory lock (main.py).  
- Эффективно для воркеров/хендлеров: **14 соединений**.

**Может ли один зависший корутин с соединением заблокировать пул?**

- Да: если корутин держит соединение и не делает `release` (зависает внутри операции или перед возвратом соединения в пул), это соединение вычитается из доступных.
- В reconcile соединения берутся короткими сессиями (`acquire_connection`), и ни одна из них не держится во время HTTP. Поэтому **зависание в list_vless_users само по себе не держит соединение**. Но если бы зависание было внутри `acquire_connection` (например, после acquire, до release), тогда одно соединение было бы занято на время зависания.

**Худший случай по соединениям в reconcile:**

- Много коротких `acquire` (keyset + по одному на orphan). При 100 сиротах — до 1 + N раз по одному соединению на короткое время. Параллельно другие воркеры и хендлеры тоже берут соединения. При 15 max и 1 под advisory lock теоретически возможна нехватка соединений (очередь на `pool.acquire()` с timeout 10s), но не из-за "удержания одного соединения на время list_vless_users", т.к. во время list_vless_users соединений reconcile не держит.

---

## STEP 7 — Здоровье event loop

- В reconcile: все циклы содержат `await` (sleep(0), cooperative_yield, acquire_connection, fetch, list_vless_users, remove_vless_user). Долгих синхронных циклов без await нет.
- `response.json()` в list_vless_users — синхронный, но на малом ответе быстрый.
- В circuit_breaker: `with self._lock` (threading.Lock) без `await` внутри — короткая критическая секция; блокировка event loop на время удержания lock, но не на 11 минут.
- Риск: если где-то в цепочке (в т.ч. внутри httpx/нижних слоёв) при чтении ответа выполняется длительный синхронный/блокирующий вызов, event loop может остановиться на это время и тогда таймаут `wait_for(20s)` не поможет (отмена срабатывает только на следующем await).

---

## STEP 8 — Сценарий "мнимого deadlock" / freeze

Описание сценария:

1. Reconcile входит в `reconcile_xray_state()` под `_worker_lock`.
2. Keyset-фаза завершается, соединения отпущены.
3. Вызывается `await vpn_utils.list_vless_users()`.
4. Выполняется `await client.get(url)` к `/list-users`.
5. Сервер отдаёт "HTTP/1.1 200 OK" (и, возможно, заголовки) — в логах появляется "HTTP Request: GET ... list-users 200 OK".
6. Сервер не отдаёт тело или отдаёт его бесконечно медленно; либо по какой-то причине таймаут на read не срабатывает.
7. Корутин reconcile застревает в `await client.get()` (или, гипотетически, в следующем синхронном шаге).
8. `asyncio.wait_for(..., 20)` через 20s пытается отменить корутин. Отмена срабатывает только на следующем await. Если блокировка в синхронном коде или в вызове, который не отменяется, корутин не доходит до await и не отменяется — **таймаут не освобождает lock**.
9. `_worker_lock` остаётся занятым, следующая итерация reconcile не стартует.
10. Остальные воркеры и Telegram polling продолжают работать, **если** они не ждут того же lock. Lock у reconcile свой, другие воркеры его не берут — значит, они не блокируются этим lock’ом.
11. Но если при этом **весь event loop** блокируется (например, из-за блокирующего read в транспорте или долгого sync-кода в цепочке list_vless_users), то все задачи останавливаются: и polling, и воркеры, и healthcheck. Тогда наблюдаемая картина "логи прекратились, процесс жив, бот не отвечает" совпадает.

**Вывод:** сценарий структурно возможен, если:

- зависание происходит внутри вызова, связанного с `list_vless_users` (чаще всего — чтение ответа после 200 OK), и
- это зависание блокирует event loop (синхронный/блокирующий read или долгий sync в цепочке), так что `wait_for` не может отменить задачу и разблокировать остальные.

---

## STEP 9 — Итоговая диагностика

### 1. Наиболее вероятная причина (confirmed mechanism)

- **Зависание на чтении тела ответа GET /list-users после получения статуса 200 OK.**
- Цепочка: reconcile вызывает `list_vless_users()` без удержания DB; внутри выполняется `await client.get()`; сервер отдаёт 200 OK (появляется лог); чтение тела ответа блокируется (сервер не закрывает тело, медленная отдача или сбой применения timeout на read). Если это блокирует event loop, все корутины, включая Telegram polling и воркеры, перестают продвигаться.

### 2. Вектор freeze

| Гипотеза | Вероятность | Комментарий |
|----------|-------------|-------------|
| Сетевой read hang после 200 OK | Высокая | Совпадает с последним логом "list-users 200 OK"; timeout должен ограничивать, но при сбое или особенностях сервера возможен длительный hang. |
| Исчерпание пула БД | Низкая | Во время list_vless_users соединения reconcile не держит; после — только короткие acquire. |
| Блокировка на retry/lock | Низкая | В list_vless_users нет retry и нет circuit_breaker; _worker_lock один, другие воркеры его не используют. |
| Deadlock между воркерами | Низкая | Один lock у reconcile; вложенных блокировок и ожидания друг друга не выявлено. |

### 3. Уровень риска

**Критический** — один вызов без circuit breaker и retry может при нестандартном поведении сервера/сети заблокировать весь процесс.

### 4. Влияние на прод

- **Потеря данных:** при падении/рестарте процесса после freeze — нет; реконсиляция идёмная, при следующем запуске повторится.
- **Повреждение подписок:** нет; reconcile только удаляет сирот после live check, финансовые и подписные пути не затрагиваются.
- **Риск по UUID:** при зависании до удаления — лишних удалений нет; при рестарте — обычная логика grace window и live check.

### 5. Требуемый тип изменений

- **Минимальный патч:** таймаут на read для list_vless_users (явный `httpx.Timeout` с read=...), при необходимости — вынести чтение тела в отдельный шаг с явным таймаутом; добавить circuit_breaker и/или retry для list_vless_users, чтобы поведение было таким же, как у add/remove.
- **Архитектурно:** гарантировать, что ни один долгий или потенциально блокирующий вызов (в т.ч. чтение тела ответа) не выполняется в контексте, способном заблокировать event loop без возможности отмены по таймауту.

---

## Сводные таблицы

### DB connection во время HTTP (reconcile)

| Участок | Соединение держится? | HTTP внутри контекста? |
|---------|----------------------|-------------------------|
| Keyset fetch | Да, кратко | Нет |
| list_vless_users() | Нет | — |
| Live check + remove (на orphan) | Да, только на время fetchrow | Нет (remove после выхода) |

### HTTP-клиент (vpn_utils)

| Функция | Timeout | retry_async | circuit_breaker | Примечание |
|---------|--------|-------------|-----------------|------------|
| list_vless_users | HTTP_TIMEOUT (float) | Нет | Нет | Единственный вызов без защиты. |
| add_vless_user | HTTP_TIMEOUT | Да | Да | — |
| remove_vless_user | HTTP_TIMEOUT | Да | Да | — |
| update_vless_user | HTTP_TIMEOUT | В обёртке | Да | — |

### Пул БД

| Параметр | Значение |
|----------|----------|
| max_size | 15 |
| Занято advisory lock | 1 |
| acquire_timeout | 10 |
| command_timeout | 30 |

Фикс в коде в этом отчёте не предлагается — только диагноз и векторы freeze для последующего выбора исправлений.
