# Sub-Aggregator — для разработчика

Склеивает две панельные подписки юзера (main + gb) в одну ссылку.
Живёт **внутри бота** как FastAPI-роутер, не отдельный сервис.

## Зачем
У юзера в Remnawave 2 сущности → 2 sub-ссылки:
- **main** (premium) — основные сервера, лимит по сроку
- **gb** (bypass) — обходные сервера, лимит по трафику (ГБ)

Юзеру неудобно добавлять 2 ключа. Агрегатор отдаёт **один**:
`https://<SUB_AGGREGATOR_URL>/a/<token>`.

## Файлы
| Файл | Что |
|---|---|
| `app/api/sub_aggregator_route.py` | сам агрегатор: роутер `/a/{token}`, кеш, merge |
| `app/services/sub_aggregator.py` | бот-хелпер: `ensure_pair`, `invalidate`, `reissue_token` |
| `app/handlers/admin/sub_aggregator_cmd.py` | команды `/aggregator`, `/aggstats`, `/aggcheck` |
| `migrations/079_sub_pairs.sql` | таблица маппинга |
| `config.py` → `SUB_AGGREGATOR_*` | конфиг |

## Таблица `sub_pairs`
```
token          PK           — ключ ссылки (32 URL-safe символа)
telegram_id    UNIQUE       — одна строка на юзера
main_sub_url   TEXT         — панельная ссылка premium
gb_sub_url     TEXT         — панельная ссылка bypass
main_user_uuid / gb_user_uuid — для self-heal при переиздании
status         active|revoked
```
Пишет **бот** (`ensure_pair`), читает **агрегатор**.

## Поток запроса `GET /a/{token}`
```
1. token не матчит regex → 404
2. FRESH-кеш hit (10 мин) → отдать сразу, в панель не идём
3. Загрузить пару: pair-кеш (1ч) → иначе SELECT из sub_pairs; нет → 404
4. status=revoked → base64-заглушка
5. Fetch (singleflight): 2 параллельных GET к панели фикс-UA v2rayTun
   ├─ upstream 404/410 → self-heal: get_user(uuid) → свежий URL → update + retry
   └─ URL нормализуется: мёртвый host / путь /api/sub/ → живой
6. Декод base64 → строки vless, merge (main+gb, dedup), кодим base64
7. Успех → кеш + отдать (x-cache: miss)
8. Оба апстрима упали → stale-копия (24ч) если есть, иначе 503
```

## Заголовки ответа
- `subscription-userinfo`: трафик из **gb**, expire из **main** (гибрид)
- `content-type`: всегда `text/plain` (мы всегда качаем base64)
- `profile-title`, `profile-update-interval=1ч`, `x-cache`

## Кеш (in-process, один uvicorn-worker)
| Слой | TTL | Зачем |
|---|---|---|
| body | fresh 10мин / stale 24ч | не долбить панель; stale при её падении |
| pair | 1ч (neg 60с) | не бить БД на каждый запрос |
| singleflight | — | N параллельных запросов 1 token = 1 fetch |

LRU-cap `MAX_CACHE_ENTRIES=20000` → память не течёт.

## Инвалидация (свежесть)
Свежесть даёт **не короткий TTL, а инвалидация**. Бот после мутации
(покупка / продление / +ГБ) зовёт `sub_aggregator.invalidate_bg(tg)` →
`clear_cache(token)` **in-process, 0мс** → следующий запрос = свежее.
Хуки в `remnawave_premium.renew`, `remnawave_bypass.add_traffic`,
`purchase_flow`.

## ⚠️ Ключевой нюанс: UA-based формат панели
Remnawave отдаёт РАЗНЫЙ формат по User-Agent:
- Happ/Incy UA → `application/json` (НЕ подписка → мусор → «неизвестный тип контента»)
- v2rayTun UA → `text/plain` base64 (универсально)

Поэтому агрегатор качает панель **фикс-UA** (`_upstream_ua()` = v2rayTun),
а НЕ форвардит UA клиента. Всегда получаем base64 → все клиенты едят.

## Эндпоинты
| Метод | Путь | Что |
|---|---|---|
| GET | `/a/{token}` | склеенная подписка |
| POST | `/a/_invalidate/{token}` | сброс кеша (внешние вызовы; бот ходит напрямую) |
| GET | `/a/_metrics` | JSON-метрики |

## Config
```python
SUB_AGGREGATOR_ENABLED = True
SUB_AGGREGATOR_URL = "https://subscription.palantirdns.uk"
SUB_AGGREGATOR_ADMIN_ONLY = False        # beta-gate; True = только админ
SUB_AGGREGATOR_UPSTREAM_HOST = "sub.atlassecure.ru"   # живой sub-host панели
# SUB_AGGREGATOR_UPSTREAM_UA — override фикс-UA (default v2rayTun/2.0)
```

## Инструменты (в боте, admin)
- `/aggstats` — hit-ratio, latency, upstream fails, размеры кешей, 🟢/🟡
- `/aggcheck [tg]` — диагностика: пара / апстримы (код+тело JSON|base64+серверы) /
  публичный URL по разным UA. Открыт всем (не-админ → только себя)
- Дашборд, карточка юзера → «Перевыпустить ссылку» — новый token, старый мрёт

## Тесты
- `tests/services/test_sub_aggregator_route.py` — юниты + интеграция + singleflight + self-heal (46)
- `tests/services/test_sub_aggregator_load.py` — нагрузка (hot ~76k rps, cold ~4.8k rps)

## Нагрузка
Узкое место — **панель, не агрегатор**. При hit-ratio >90% держит тысячи
rps; cold-miss ограничен httpx-пулом (100 conn). На 20k/100k юзеров запас
большой (клиенты опрашивают раз/час вразнобой).

## Откат
- `SUB_AGGREGATOR_ADMIN_ONLY=True` → в бету (только админ)
- `SUB_AGGREGATOR_ENABLED=False` → полностью на legacy (2 отдельные ссылки)
