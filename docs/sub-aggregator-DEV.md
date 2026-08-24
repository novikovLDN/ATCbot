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
2. FRESH-кеша НЕТ (TTL=0) → всегда идём в панель за свежим (см. шаг 5)
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
| body | fresh 0с (кеша нет) / stale 24ч | каждый запрос свежий; stale только при падении панели |
| pair | 1ч (neg 60с) | не бить БД на каждый запрос |
| singleflight | — | N параллельных запросов 1 token = 1 fetch |

LRU-cap `MAX_CACHE_ENTRIES=20000` → память не течёт.

## Свежесть — всегда актуально
Цель: **любое** изменение доходит до юзера само, и **ручное обновление
всегда отдаёт актуальную версию**.

- **FRESH_TTL=0 — fresh-кеша нет.** Каждый `GET /a/{token}` (ручной или
  авто-опрос — на сервере они неотличимы) идёт в панель за свежим списком.
  Поэтому правки **напрямую в панели** (добавили/убрали ноду, сменили
  squad) видны на следующем опросе клиента, без всякого `/aggflush`.
- **Бурсты не бьют панель:** параллельные запросы на один token схлопывает
  singleflight (1 fetch на всех ждущих). Токен персональный, опрашивается
  раз/час вразнобой → абсолютный rps низкий.
- **Инвалидация на мутациях бота** осталась, но теперь косметическая:
  `invalidate_bg(tg)` чистит pair-кеш; body всё равно не кешируется.

⚠️ «Сразу» ограничено **клиентом**: VPN-приложение опрашивает подписку
раз в час (`profile-update-interval=1ч`). Серверная часть отдаёт свежак на
КАЖДЫЙ опрос, но частоту опроса задаёт клиент — мы не push'им, клиенты
pull'ят. Ручное обновление в приложении = немедленный опрос → свежак сразу.

`/aggflush` (админ) теперь нужен редко — только сбросить pair-кеш, если
sub-URL сменился в панели без прохода через бот.

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

## Config — ДВА независимых переключателя
```python
SUB_AGGREGATOR_ENABLED = True          # монтирует эндпоинт /a/{token}
                                       #   (обслуживает УЖЕ выданные ссылки)
SUB_AGGREGATOR_ISSUE_ENABLED = False   # выдаём/показываем ли НОВУЮ единую
                                       #   ссылку в боте. False → фича выкл
                                       #   для всех, юзеры на legacy (2 ключа),
                                       #   но эндпоинт продолжает отдавать
                                       #   существующие ссылки
SUB_AGGREGATOR_URL = "https://subscription.palantirdns.uk"
SUB_AGGREGATOR_ADMIN_ONLY = False      # (при ISSUE_ENABLED=True) True = только админ
SUB_AGGREGATOR_UPSTREAM_HOST = "sub.atlassecure.ru"   # живой sub-host панели
# SUB_AGGREGATOR_UPSTREAM_UA — override фикс-UA (default v2rayTun/2.0)
```

**Матрица состояний:**
| ENABLED | ISSUE_ENABLED | Эндпоинт (existing links) | Выдача новым |
|---|---|---|---|
| True | True  | ✅ работает | ✅ выдаём (с учётом ADMIN_ONLY) |
| True | False | ✅ работает | ❌ legacy 2 ключа (**текущее**) |
| False | —     | ❌ не смонтирован | ❌ всё выкл |

Гейт выдачи — `sub_aggregator.is_enabled_for(tg)` (проверяет ENABLED +
ISSUE_ENABLED + URL + ADMIN_ONLY). Эндпоинт монтируется отдельно по
ENABLED в `app/api/__init__.py`. Экраны подключения/профиля при
`is_enabled_for=False` откатываются на legacy: `agg_url or <2 ключа>`.

## Инструменты (в боте, admin)
- `/aggstats` — hit-ratio, latency, upstream fails, размеры кешей, 🟢/🟡
- `/aggcheck [tg]` — диагностика: пара / апстримы (код+тело JSON|base64+серверы) /
  публичный URL по разным UA. Открыт всем (не-админ → только себя)
- Дашборд, карточка юзера → «Перевыпустить ссылку» — новый token, старый мрёт

## Тесты
- `tests/services/test_sub_aggregator_route.py` — юниты + интеграция + singleflight + self-heal
- `tests/services/test_sub_aggregator_load.py` — нагрузка (hot ~76k rps, cold ~4.8k rps)
- `tests/services/test_sub_aggregator_gate.py` — гейт выдачи is_enabled_for (развязка ENABLED/ISSUE_ENABLED)

## Нагрузка
Узкое место — **панель, не агрегатор**. При hit-ratio >90% держит тысячи
rps; cold-miss ограничен httpx-пулом (100 conn). На 20k/100k юзеров запас
большой (клиенты опрашивают раз/час вразнобой).

## Откат
- `SUB_AGGREGATOR_ISSUE_ENABLED=False` → **отключить выдачу для всех, НЕ ломая
  уже выданные ссылки** (эндпоинт жив). Рекомендуемый мягкий откат.
- `SUB_AGGREGATOR_ADMIN_ONLY=True` → сузить выдачу до админа (только если
  ISSUE_ENABLED=True)
- `SUB_AGGREGATOR_ENABLED=False` → ⚠️ ЖЁСТКО: снимает и эндпоинт →
  существующие ссылки у юзеров перестанут открываться. Использовать только
  если надо погасить всю систему.
