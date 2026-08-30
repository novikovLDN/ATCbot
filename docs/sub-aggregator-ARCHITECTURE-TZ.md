# ТЗ: sub-aggregator — как устроен и как с ним работать

**Для передачи другому ИИ-агенту / инженеру.** Документ самодостаточный:
описывает готовую реализацию агрегатора подписок внутри ATCbot, его
контракты, кеш-модель, нагрузочные характеристики и правила доработки.

---

## 0. Что это одним абзацем

У каждого клиента VPN есть **две** сущности в панели Remnawave и, значит,
две ссылки-подписки: **main** (premium — основные сервера, лимит по сроку)
и **gb** (bypass — обходные сервера, лимит по трафику). Агрегатор по одной
ссылке `https://<домен>/a/<token>` скачивает обе апстрим-подписки,
**склеивает в одну** (XRAY base64), проставляет гибридные заголовки и
отдаёт клиенту (Happ / v2rayTun / v2rayNG / Streisand / …). Живёт **внутри
бота** как FastAPI-роутер (не отдельный сервис), деплой на Railway.

**HTML-страницы НЕТ** — любой запрос (браузер или VPN-клиент) получает
сырую base64-подписку. (Раньше была браузерная onboarding-страница, убрана
по решению владельца; код удалён.)

---

## 1. Расположение и структура

| Что | Файл |
|---|---|
| Сам агрегатор (FastAPI-роутер) | `app/api/sub_aggregator_route.py` |
| Бот-хелпер (upsert пары + invalidate) | `app/services/sub_aggregator.py` |
| Admin-команда `/aggregator` | `app/handlers/admin/sub_aggregator_cmd.py` |
| Таблица маппинга (миграция) | `migrations/079_sub_pairs.sql` |
| Config-константы | `config.py` (блок `SUB_AGGREGATOR_*`) |
| Тесты (юниты+интеграция) | `tests/services/test_sub_aggregator_route.py` |
| Тесты нагрузки | `tests/services/test_sub_aggregator_load.py` |

Роутер монтируется в `app/api/__init__.py` (`app.include_router(...)`).
Эндпоинты: `GET /a/{token}`, `POST /a/_invalidate/{token}`, `GET /a/_metrics`.

---

## 2. Таблица `sub_pairs` (источник истины)

```sql
CREATE TABLE IF NOT EXISTS sub_pairs (
    token          TEXT PRIMARY KEY,          -- стабильный ключ ссылки (32 URL-safe символа)
    telegram_id    BIGINT NOT NULL,
    main_sub_url   TEXT NOT NULL,             -- ПОЛНАЯ апстрим-ссылка premium (main)
    gb_sub_url     TEXT NOT NULL,             -- ПОЛНАЯ апстрим-ссылка bypass (gb)
    main_user_uuid UUID NULL,                 -- для вебхук-инвалидации (может быть NULL)
    gb_user_uuid   UUID NULL,
    status         TEXT NOT NULL DEFAULT 'active',  -- active | revoked
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX sp_telegram_id_uq ON sub_pairs (telegram_id);
CREATE INDEX sp_main_uuid ON sub_pairs (main_user_uuid);
CREATE INDEX sp_gb_uuid   ON sub_pairs (gb_user_uuid);
```

Пишет — **только бот** (`sub_aggregator.ensure_pair`), читает — агрегатор.

---

## 3. Контракт запроса `GET /a/{token}`

### 3.1 Валидация
- `token` матчится `^[A-Za-z0-9_-]{4,128}$`. Не матчится → **404** `Not found`
  (+ метрика `not_found`, тик attack-детектора). Никакого похода в БД.

### 3.2 Основной поток (по шагам, как в `aggregate()`)
1. **Fresh-кэш hit** → отдать мгновенно (`x-cache: hit`). ~13 µs.
2. **Загрузить пару** через `_load_pair` (pair-кеш 1ч → иначе `SELECT`
   из `sub_pairs`). Нет пары → **404** (+ negative-кеш 60с от token-флуда).
3. **`status='revoked'`** → отдать stub-подписку с одной мёртвой строкой
   `vless://000…@127.0.0.1:443#Subscription%20revoked`, короткий TTL 10с.
4. **Fetch (singleflight)** — 2 параллельных `httpx GET` к
   `main_sub_url` и `gb_sub_url`, форвардя User-Agent клиента.
5. **Успех** → склейка → `_cache_set` (fresh 15с / stale 24ч) → отдать
   (`x-cache: miss`).
6. **Оба апстрима упали** → есть stale-копия → отдать её (`x-cache: stale`).
7. **Ни апстрима, ни stale** → **503** + `retry-after: 30`.

### 3.3 Склейка тела (шаг 5)
- Декодировать каждый апстрим-ответ: base64 → строки, ИЛИ plaintext-список.
  Пустые строки убрать.
- Порядок: **сначала все строки main, затем все строки gb**.
- Точные дубликаты (по всей строке) убрать, порядок сохранить.
- Соединить `\n`, закодировать base64, отдать
  `content-type: text/plain; charset=utf-8`.

### 3.4 Гибридные заголовки ответа
- `subscription-userinfo`: **`upload`/`download`/`total` от gb** (там реальный
  лимит трафика), **`expire` от main** (там реальный срок). Отсутствующие
  числа = 0.
- `profile-title`: от панели (main→gb), fallback — `config.SUB_AGGREGATOR_BRAND_TITLE`
  или `"Atlas Secure"`.
- `profile-update-interval`: `1` (час) — как часто клиент авто-обновляет.
- `profile-web-page-url`, `announce`: проброс с main если есть.
- `support-url`: от панели, fallback — `config.SUPPORT_URL`.
- `x-cache`: `hit | miss | stale` (диагностика).

### 3.5 Частичный ответ
Если один апстрим ответил 200, второй упал/не-200 → отдаём то, что есть,
от живого (merge только его строк). Пустой ответ обоих → 503.

---

## 4. Кеш-модель (in-process, один uvicorn-worker)

**Три уровня, все — обычные `OrderedDict` в памяти процесса.** Redis НЕ
используется (один воркер → синхронизация не нужна; при масштабировании на
N воркеров переходить на Redis — тогда каждый воркер имеет свой кеш, что
допустимо, т.к. TTL короткие).

| Кеш | Ключ → значение | Fresh | Stale | Назначение |
|---|---|---|---|---|
| **body** `_cache` | token → (fresh_until, stale_until, body, headers) | 15с | 24ч | hit мгновенно; stale при падении панели |
| **pair** `_pair_cache` | token → (expires, pair-dict\|None) | 1ч (neg 60с) | — | не бить БД на каждый запрос |
| **singleflight** `_inflight` | token → Future | — | — | параллельные запросы 1 токена = 1 fetch |

- **LRU-граница**: `MAX_CACHE_ENTRIES=20_000` (body), `MAX_PAIR_ENTRIES=40_000`
  (pair). При превышении — `popitem(last=False)` (выселяем самый старый).
  Память ограничена: 20k × ~30КБ ≈ 600 МБ верхняя оценка.
- **Negative-кеш** (pair=None, 60с): защита от флуда случайными токенами —
  повторный тот же random не идёт в БД.
- **Ленивая инвалидация expired**: `_cache_get` на полностью истёкшей записи
  сам её удаляет. Фонового свипера НЕТ (проще; LRU + lazy достаточно).

### 4.1 Singleflight (защита панели от стада)
Cold-start (рестарт бота) или одновременный TTL-expire: 100 клиентов
приходят за одним токеном в одну секунду → делаем **1 пару upstream GET**,
все 100 разделяют результат. Реализация — `_inflight` dict с `asyncio.Future`:
первый (leader) создаёт Future и делает fetch, остальные (followers)
`await` тот же Future. Тест доказывает: 50 параллельных → ровно 2 upstream
вызова.

---

## 5. Инвалидация (мгновенное обновление подписки)

**Ключевое требование: после покупки/продления/докупки ГБ клиент видит
свежие данные.**

- Бот после любой мутации подписки зовёт
  `sub_aggregator.invalidate_bg(telegram_id)` →
  `sub_aggregator_route.clear_cache(token)` — **прямой in-process вызов, 0 мс,
  без сети**. Чистит и body-, и pair-кеш.
- Хуки уже расставлены: `remnawave_premium.renew` (продление),
  `remnawave_bypass.add_traffic` (докупка ГБ), `purchase_flow` (покупка/combo).
- HTTP-эндпоинт `POST /a/_invalidate/{token}` (с `x-internal-secret`) —
  для внешних вызовов (напр. вебхук панели). Бот его НЕ использует
  (ходит напрямую).
- Итог: mutation → clear_cache → следующий GET клиента = свежая склейка из
  панели. Быстрее нельзя — клиент сам опрашивает раз/час (стандарт
  `profile-update-interval`, ниже не выставить).

---

## 6. Нагрузочные характеристики (ИЗМЕРЕНО)

`tests/services/test_sub_aggregator_load.py` — реальные цифры (in-process,
1 ядро, upstream мокнут):

| Сценарий | Пропускная способность | Комментарий |
|---|---|---|
| Горячий кэш | **~76 000 rps** (13 µs/req) | 99% реального трафика |
| Смешанный 95% hit | **~74 000 rps** (hit-ratio 97%) | как в реальности |
| Холодный miss | **~4 800 rps** (209 µs/req) | merge 40 серверов + base64 + dedup, 1 ядро |
| Flood уникальными | память ограничена LRU | не течёт |

**Потолок в проде** ограничен НЕ агрегатором, а:
1. httpx-пул: `max_connections=100`, панель ~40мс → ~1 250 cold-miss/сек =
   **75 000 уникальных подписок/мин**.
2. CPU для merge: ~4 800 merge/сек на ядро.
3. **Панель Remnawave** (обычно тысячи rps).

**Вывод**: на 20k и 100k активных подписок агрегатор — не узкое место.
Клиенты опрашивают раз/час вразнобой → средняя нагрузка единицы rps,
пик — десятки. Даже синхронный cold-start 20k юзеров в минуту = 333 miss/сек,
внутри потолка.

---

## 7. Attack-детектор + метрики

- **Скользящее 60-сек окно** считает: `not_found` (флуд случайных токенов)
  и `upstream_fail` (панель гасят/упала). Пороги: `300/мин` not_found,
  `60/мин` upstream_fail. При пробитии — **разовый** Telegram-алерт админу
  через `admin_alerts.send_alert('security')` (fire-and-forget). Стоимость
  на запрос — инкремент int + сравнение (микросекунды).
- **`GET /a/_metrics`** → JSON: `hits/misses/stale/upstream_ok/upstream_fail/
  singleflight_wait/not_found/revoked/attack_alerts_sent`, размеры кешей,
  `hit_ratio`, `avg_upstream_ms`. Публичный (не sensitive). Мониторинг:
  здоров, когда `hit_ratio > 0.9` и `avg_upstream_ms < 400`.

---

## 8. Config (`config.py`, захардкожено — без ENV по решению владельца)

```python
SUB_AGGREGATOR_ENABLED = True
SUB_AGGREGATOR_URL = "https://subscription.palantirdns.uk"  # публичный домен
SUB_AGGREGATOR_ADMIN_ONLY = True    # beta-gate: пока только админ видит ссылку
SUB_AGGREGATOR_INTERNAL_SECRET = "" # для /a/_invalidate (пусто = приём всех, ок для беты)
```

- `SUB_AGGREGATOR_ADMIN_ONLY=True` → `is_enabled_for(tg)` пускает только
  `ADMIN_TELEGRAM_ID`. Флип на `False` → все юзеры автоматически (код
  user-facing экранов уже подключён через `sub_aggregator.get_url/ensure_pair`).
- Меняешь домен/секрет → правишь эти константы, commit, Railway redeploy.

---

## 9. Бот-сторона (`app/services/sub_aggregator.py`)

- `is_enabled_for(tg) -> bool` — gate (enabled + url задан + admin-only проверка).
- `ensure_pair(tg) -> url|None` — читает `remnawave_premium_sub_url` +
  `remnawave_bypass_sub_url` из `subscriptions`, UPSERT в `sub_pairs` по
  `telegram_id`, зовёт invalidate, возвращает `https://<url>/a/<token>`.
  Вернёт None если нет ОБЕИХ ссылок (trial-only и т.п.).
- `get_url(tg) -> url|None` — быстрый read (без тача апстримов).
- `revoke(tg)` — `status='revoked'` + invalidate.
- `invalidate_bg(tg)` — fire-and-forget clear_cache по token юзера.
- Токен: `secrets.token_urlsafe(24)[:32]` (192 бита энтропии).

---

## 10. Деплой (инфраструктура — НЕ в этом файле)

Агрегатор живёт в боте (Railway). Наружу публикуется через российский
front-VPS: nginx **reverse-proxy** `https://<домен>/a/<token>` →
`https://api.atlassecure.ru/a/<token>` (обычный `proxy_pass`, TLS +
rate-limit на фронте). Никаких Docker/WG/stream — простой HTTPS-прокси.
Домен «отбеливается» отдельно (Cloudflare DNS-only, LE-сертификат).
Подробности — `docs/sub-aggregator-DEPLOY-NEW-CHAT-TZ.md` (если нужно
2 IP / failover — там, но текущая реализация не требует).

---

## 11. Правила доработки (для агента)

1. **Не усложнять.** Владелец требует максимально простую систему. Любое
   добавление уровня кеша / очереди / воркера — только с явной нуждой,
   подтверждённой нагрузочным тестом.
2. **Тесты обязательны.** Любая правка `aggregate()` / кеша / склейки →
   прогнать `pytest tests/services/test_sub_aggregator_route.py` +
   `test_sub_aggregator_load.py -s`. Все 42 должны быть зелёными.
3. **Мутации кеша — только через `_cache_put` / `clear_cache`** (LRU-граница
   и очистка обоих кешей гарантированы там). Не писать в `_cache` напрямую.
4. **Апстримы — всегда параллельно** (`asyncio.gather`), никогда
   последовательно (удвоит latency).
5. **Никогда не блокировать event loop** синхронными тяжёлыми операциями —
   merge должен оставаться <5мс. При росте размера подписок замерить.
6. **`profile-update-interval` не опускать ниже разумного** — это трафик к
   панели ×N. 1 час — баланс свежести и нагрузки.
7. **HTML не возвращать.** Любой UA → base64. (HTML-код удалён; не
   восстанавливать без явного запроса владельца.)
8. **Инвалидация — приоритет.** Любая новая точка мутации подписки в боте
   обязана звать `sub_aggregator.invalidate_bg(tg)`.

---

## 12. Definition of Done для доработок

- [ ] `pytest tests/services/test_sub_aggregator_route.py` — 38/38 зелёные.
- [ ] `pytest tests/services/test_sub_aggregator_load.py -s` — 4/4, цифры
      не упали в разы (hot >5000 rps, cold >1000 rps).
- [ ] `python3 -c "from app.api import sub_aggregator_route"` — импортируется.
- [ ] Роутер отдаёт 3 маршрута: `/a/{token}`, `/a/_invalidate/{token}`,
      `/a/_metrics`.
- [ ] Новые мутации подписки зовут `invalidate_bg`.
- [ ] `/a/_metrics` показывает `hit_ratio > 0.9` под нормальным трафиком.
