# Admin Web Dashboard — карта репо для агента-исполнителя

> Это карта проекта для реализации **веб-дашборда внутри существующего
> бота**. Дашборд встраивается как набор роутов в уже работающий
> `app/api/` FastAPI, использует те же DB-функции, тот же event loop,
> тот же домен (вебхук + дашборд на одном порту).
>
> Документ структурирован по принципу «что трогать нельзя → что
> переиспользуем → что добавляется → в каком порядке».

---

## 0. Executive summary

| Что есть | Состояние |
|---|---|
| FastAPI работает в проде (`app/api/__init__.py`) | ✅ принимает Telegram webhook, payment webhook, deeplink, health |
| Redis-клиент | ✅ опционально, есть `app/utils/redis_client.py` + health-check на line 70 |
| Защита запросов | ✅ `RequestSizeLimitMiddleware` (1MB) |
| Все доменные функции для эндпоинтов | ✅ `database/__init__.py` re-export, ~150 функций |
| Все admin-сценарии | ✅ ~70 callback-prefix'ов в `app/handlers/admin/*` |
| 12 фоновых воркеров | ⚠️ работают, **не трогаем** |
| Remnawave integration | ⚠️ атомарная, **только через high-level функции** |

| Что добавляется |  |
|---|---|
| `app/api/dashboard/` — пакет с роутами | новый, ~10 файлов |
| `app/events.py` — in-process bus | новый, 30 строк |
| 5-6 точек `bus.publish(...)` в существующих хендлерах | мелкие правки |
| `app/handlers/admin/base.py` — добавить `/admin` magic-link | +5 строк |
| `dashboard/` — React + Vite + Tailwind | новый, отдельная папка |
| ENV: `JWT_SECRET`, `DASHBOARD_BASE_URL` | две переменные |

**Главный принцип:** дашборд читает из DB напрямую, пишет через
существующие атомарные функции (`admin_grant_access_atomic`,
`create_broadcast` и т.д.). НЕ дублирует логику ботских хендлеров.

---

## 1. Что трогать НЕЛЬЗЯ (Do Not Touch)

### 1.1. Фоновые воркеры (`main.py:249-508`)

12 задач крутятся в том же event loop. Дашборд **только подписывается
на их события**, никакой другой связи быть не должно.

| Воркер | Файл | Каданс | Зачем |
|---|---|---|---|
| Reminders | `reminders.py` | ~30 мин | напоминания об истечении (7d/3d/1d/24h/3h) |
| Trial Notifications | `trial_notifications.py:679` | 5 мин | 24h/3h уведомления + revoke просроченных trial'ов |
| Fast Expiry Cleanup | `fast_expiry_cleanup.py:52` | 60-300 с | revoke просроченных подписок + bypass-only переход |
| Auto-Renewal | `auto_renewal.py:371` | 5-15 мин | продление с баланса в окне 6h до истечения |
| Activation Worker | `activation_worker.py:371` | 5 мин | обработка pending активаций |
| Farm Notifications | `app/workers/farm_notifications.py:250` | 30 мин | уведомления + штормы фермы |
| Traffic Monitor | `app/workers/traffic_monitor.py:109` | 5 мин | пороги трафика Remnawave (8/5/3/1 GB) |
| Site Sync | `app/workers/site_sync_worker.py:25` | 5 мин | синхронизация баланса с qodev.dev |
| Xray Sync | `xray_sync.py:119` | 5 мин | health-check + ресинк (legacy, скоро уйдёт) |
| Health Check | `healthcheck.py:33` | 10 мин | DB + Redis alive |
| DB Init Retry | `main.py:313` (условный) | 30 с | пересоздание воркеров после восстановления БД |
| Uvicorn (webhook) | `main.py:621` | event-driven | приём апдейтов Telegram |

**Критические инварианты, которые дашборд не должен нарушать:**

- Каждый воркер имеет либо `_worker_lock` (in-memory singleton), либо
  `FOR UPDATE SKIP LOCKED` (PostgreSQL distributed lock).
  **Дашборд не должен запускать те же операции напрямую.** Например,
  не вызывать `process_renewal_for_subscription` — это работа
  auto_renewal worker'а.
- `last_auto_renewal_at`, `reminder_*_sent`, `trial_notif_*_sent` —
  идемпотентность через флаги в БД. Дашборд **читать можно**, писать
  нельзя.
- Fast Expiry Cleanup сначала отпускает DB-коннект, потом дёргает VPN
  API. Дашборд не должен открывать долгие транзакции на тех же
  строках.

### 1.2. Remnawave write-path

Панель — `rmnw.atlassecure.ru`. Все апи-вызовы должны идти через
**high-level функции** в `app/services/`. Перечень того, что можно
вызывать из дашборда (write):

| Когда нужно | Функция | Файл |
|---|---|---|
| Создать премиум-entity | `create_premium_user_entity()` | `remnawave_premium.py` |
| Продлить премиум | `renew_premium_user()` | `remnawave_premium.py` |
| Отключить премиум | `disable_premium_user()` | `remnawave_premium.py` |
| Реиссью премиум-UUID | `reissue_premium_user_entity()` | `remnawave_premium.py` |
| Bypass create | `create_bypass_user_entity()` | `remnawave_service.py` |
| Bypass +traffic | `add_bypass_traffic()` (накапливает, не сбрасывает) | `remnawave_bypass.py` |
| Bypass delete | `delete_bypass_user()` | `remnawave_service.py` |
| Оркестратор «всё сразу» (purchase) | `provision_subscription()` | `purchase_flow.py` |

**Нельзя:**
- ❌ Вызывать `remnawave_api.create_user()` напрямую (low-level)
- ❌ Делать PATCH `expireAt` без апдейта `subscriptions.remnawave_premium_uuid` в БД
- ❌ Сбрасывать bypass traffic (только накапливать: read → add → PATCH sum)
- ❌ Удалять entity без очистки соответствующей колонки в БД
- ❌ Создавать entity без `externalSquadUuid` для премиум (Task 6 требование)

**READ из панели — свободно:**
- `get_user(uuid)` — подробности по entity
- `find_user_by_username(username)` — для recovery
- `get_user_traffic(uuid)` — текущий usage
- `get_all_users()` — пагинированный список (есть retry внутри)

Username scheme: `tg_{telegram_id}_premium` (премиум) и `tg_{telegram_id}`
(bypass). Если решим, что дашборд должен показывать статус в панели —
эти ключи и используем.

### 1.3. Bot-only write paths (НЕ из дашборда)

| Функция | Почему bot-only |
|---|---|
| `approve_payment_atomic()` | Платёжный webhook flow; требует payment_id и провайдер-контекст |
| `grant_access()` (low-level) | Внутренняя — вызывается только из admin_grant и approve_payment |
| `finalize_purchase()` | Pending purchases state machine, webhook'и |
| `mark_trial_used()` | Триал активируется через /start или inline-кнопку, eligibility-логика |

Эквивалент для админ-выдачи доступа из дашборда — `admin_grant_access_atomic()` (есть admin-контекст, чище семантика).

---

## 2. Существующая инфраструктура (что переиспользуем)

### 2.1. FastAPI приложение

Уже инициализировано в `app/api/__init__.py`:

```python
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(RequestSizeLimitMiddleware, max_size=1*1024*1024)
app.include_router(telegram_webhook.router)
app.include_router(payment_webhook.router)
app.include_router(deeplink_redirect.router)
# health-check на /health
```

**Дашборд добавляется так:**

```python
# в конец app/api/__init__.py
from app.api import dashboard as _dashboard
app.include_router(_dashboard.router, prefix="/dashboard/api")
app.include_router(_dashboard.ws_router, prefix="/dashboard")  # /dashboard/ws

# раздача React-сборки
from fastapi.staticfiles import StaticFiles
app.mount("/dashboard", StaticFiles(directory="dashboard/dist", html=True), name="dashboard")
```

SPA-fallback на 404 для не-API путей придётся аккуратно делать через
StaticFiles (`html=True` + правильный mount).

### 2.2. Auth helpers

Существует проверка `from_user.id == config.ADMIN_TELEGRAM_ID` в каждом
admin-хендлере. Для дашборда — JWT (PyJWT) с тем же `ADMIN_TELEGRAM_ID`
как `sub`.

### 2.3. Redis (опционально, но желательно для будущего)

Уже подключён — `app/utils/redis_client.py`. Сейчас используется
только для health-check. **Для in-process bus НЕ нужен**, но если
понадобится разделить на два сервиса — переключение тривиальное.

---

## 3. API-маппинг: DB-функции → REST-эндпоинты

### 3.1. READ-эндпоинты (аналитика, главный экран)

| Эндпоинт | DB функция | Что возвращает |
|---|---|---|
| `GET /api/stats/overview` | `get_extended_bot_stats()` | вся главная стата одним вызовом |
| `GET /api/stats/business` | `get_business_metrics()` | approval time, lifetime, renewals |
| `GET /api/stats/revenue` | `get_total_revenue()`, `get_arpu()`, `get_ltv()` | оборотные метрики |
| `GET /api/stats/period?hours=N` | `get_analytics_by_period(hours)` | данные за окно N часов |
| `GET /api/stats/daily?date=YYYY-MM-DD` | `get_daily_summary(date)` | сводка за день |
| `GET /api/stats/monthly?year=N&month=M` | `get_monthly_summary(y, m)` | сводка за месяц |
| `GET /api/stats/purchase-breakdown` | `get_purchase_breakdown()` | разбивка по тарифам/периодам |
| `GET /api/stats/promo` | `get_promo_stats()` | использование промокодов |

### 3.2. Юзеры

| Эндпоинт | DB функция |
|---|---|
| `GET /api/users/search?q=` | `find_user_by_id_or_username(q)` |
| `GET /api/users/{tg_id}` | `get_user(tg_id)` + `get_user_extended_stats(tg_id)` |
| `GET /api/users/{tg_id}/balance` | `get_user_balance(tg_id)` |
| `GET /api/users/{tg_id}/subscription` | `get_subscription(tg_id)` |
| `GET /api/users/{tg_id}/history` | `get_subscription_history(tg_id, limit)` |
| `GET /api/users/{tg_id}/payments` | через подзапрос payments WHERE telegram_id |
| `GET /api/users/{tg_id}/discount` | `get_user_discount(tg_id)` |
| `GET /api/users/{tg_id}/vip` | `is_vip_user(tg_id)` |
| `GET /api/users/{tg_id}/trial` | `get_trial_info(tg_id)` |
| `POST /api/users/{tg_id}/grant` body `{days, tariff}` | `admin_grant_access_atomic(...)` |
| `POST /api/users/{tg_id}/grant-minutes` body `{minutes}` | `admin_grant_access_minutes_atomic(...)` |
| `POST /api/users/{tg_id}/revoke` | `admin_revoke_access_atomic(...)` |
| `POST /api/users/{tg_id}/switch-tariff` body `{tariff}` | `admin_switch_tariff(...)` |
| `POST /api/users/{tg_id}/discount` body `{percent, expires_at}` | `create_user_discount(...)` |
| `DELETE /api/users/{tg_id}/discount` | `delete_user_discount(tg_id)` |
| `POST /api/users/{tg_id}/balance` body `{delta_rubles, reason}` | `increase_balance` / `decrease_balance` |
| `POST /api/users/{tg_id}/vip` | `grant_vip_status(tg_id, admin_id)` |
| `DELETE /api/users/{tg_id}/vip` | `revoke_vip_status(tg_id, admin_id)` |
| `DELETE /api/users/{tg_id}` | `admin_delete_user_complete(tg_id, admin_id)` |

### 3.3. Подписки и платежи

| Эндпоинт | DB функция |
|---|---|
| `GET /api/subscriptions/active` | `get_active_premium_subscribers()` (плюс пагинация) |
| `GET /api/subscriptions/{id}` | `get_active_subscription(id)` |
| `GET /api/subscriptions/{id}/history` | `get_subscription_history(...)` |
| `GET /api/payments/{id}` | `get_payment(id)` |
| `GET /api/payments?status=` | прямой SQL или новая функция |

### 3.4. Рассылки

| Эндпоинт | DB функция |
|---|---|
| `GET /api/broadcasts/recent` | `get_recent_broadcasts(limit)` |
| `GET /api/broadcasts/{id}` | `get_broadcast(id)` |
| `GET /api/broadcasts/{id}/stats` | `get_broadcast_stats(id)` |
| `GET /api/broadcasts/ab-tests` | `get_ab_test_broadcasts()` |
| `GET /api/segments/{name}/count` | `get_users_by_segment(name)` (счёт юзеров) |
| `POST /api/broadcasts` body `{title, message, type, segment, ab?}` | `create_broadcast(...)` + `save_broadcast_discount(...)` |

**Важно:** create_broadcast только создаёт запись. Бот отдельно её
читает и шлёт через broadcast worker. Дашборд **не должен** сам
рассылать. Об этом — событие `broadcast:created` через bus, чтобы
бот заметил сразу, без ожидания.

### 3.5. Гифт-ссылки на ГБ

| Эндпоинт | DB функция |
|---|---|
| `GET /api/bgift/summary` | `get_bypass_gift_links_summary()` |
| `GET /api/bgift/list?page=N` | `list_bypass_gift_links(page)` |
| `GET /api/bgift/{id}` | `get_bypass_gift_link_by_id(id)` |
| `GET /api/bgift/{id}/redemptions` | `get_bypass_gift_link_redemptions(id)` |
| `POST /api/bgift` body `{days, gb, max_uses}` | `create_bypass_gift_link(...)` |
| `DELETE /api/bgift/{id}` | `soft_delete_bypass_gift_link(id)` |

### 3.6. Гифт-подписки (отдельно от ГБ-ссылок)

| Эндпоинт | DB функция |
|---|---|
| `POST /api/gifts` body `{period_days, tariff}` | `generate_gift_code()` + `create_gift_subscription(...)` |
| `GET /api/gifts/{code}` | `get_gift_subscription(code)` |
| `GET /api/users/{tg_id}/gifts` | `get_user_gifts(tg_id)` |

### 3.7. Аудит и экспорт

| Эндпоинт | DB функция | Особенности |
|---|---|---|
| `GET /api/audit/logs?limit=N` | `get_last_audit_logs(limit)` | для timeline-feed |
| `GET /api/audit/premium-recovery` | `get_premium_recovery_candidates()` | кандидаты на починку +10y |
| `GET /api/audit/db-dates` | `get_subscriptions_with_far_future_expires()` | подозрительные даты |
| `GET /api/audit/trial-users` | `get_active_trial_telegram_ids()` | активные триалы |
| `GET /api/export/users.csv` | `get_all_users_for_export()` | стриминг ответом |
| `GET /api/export/subscriptions.csv` | `get_active_subscriptions_for_export()` | стриминг |

CSV — отдавать через `StreamingResponse` с `media_type="text/csv"`, не
аккумулировать в памяти.

### 3.8. Прочее

| Эндпоинт | DB функция |
|---|---|
| `GET /api/referrals/overall` | `get_referral_overall_stats()` |
| `GET /api/referrals/top?by=` | `get_admin_referral_stats(...)` |
| `GET /api/referrals/{id}/detail` | `get_admin_referral_detail(id)` |
| `GET /api/referrals/{id}/history?page=N` | `get_referral_rewards_history(...)` + count |
| `GET /api/incident` | `get_incident_settings()` |
| `POST /api/incident` body `{enabled, text}` | `set_incident_mode(enabled)` + текст в отдельной функции |
| `POST /api/special-offer` body `{telegram_id, percent, expires_at}` | `set_special_offer(...)` |

---

## 4. Что портируем (приоритет)

По частоте использования и ценности:

### Фаза 1 — критичный минимум (1 неделя)

1. **Login / magic-link** — `/admin` команда → JWT URL → дашборд авторизует
2. **Главная страница** — `get_extended_bot_stats` одним запросом
3. **Поиск пользователя** + карточка с действиями (grant, revoke, discount, balance)
4. **Список последних платежей**
5. **Live-обновления через WebSocket** — события `payment:approved`, `trial:activated`, `user:registered`

### Фаза 2 — рассылки (3-5 дней)

6. **Создание custom-рассылки** — wizard (title, message, photo, segment, кнопки)
7. **История рассылок** + статистика отправки
8. **Preset-рассылки** — те 10 промо-шаблонов из `notifications.py`
9. **Сегменты** — счётчик аудитории до отправки

### Фаза 3 — аналитика (3-5 дней)

10. **Финансовая аналитика** — revenue, ARPU, LTV, MRR, графики по периодам
11. **Покупки breakdown** — таблицы по тарифам/периодам
12. **Referral analytics** — топ, история cashback'ов

### Фаза 4 — аудит и операции (3-5 дней)

13. **Audit log timeline**
14. **CSV-экспорт юзеров и подписок**
15. **Premium recovery tool** (read-only список кандидатов + ручной fix)
16. **DB dates audit**
17. **Trial-промо рассылка** (тот, что делали в боте — порт)

### Фаза 5 — спецфичи (опционально)

18. **Гифт-ссылки на ГБ** — CRUD
19. **VIP / Гифт-подписки**
20. **Incident-режим** (баннер)
21. **Удаление пользователя** (с двойным подтверждением)

**Не портируем:**
- ❌ Test menu (`admin:test_menu`) — отладочное
- ❌ Farm Storm, Stage Users — STAGE-only
- ❌ Migration tools — одноразовые скрипты, не дашборд
- ❌ Mass Remnawave Provision — однораз, для миграции
- ❌ Chat with user — отдельная история, лучше оставить в боте
- ❌ Custom MTProto proxy admin — слишком узко

---

## 5. Архитектура дашборда

### 5.1. Структура файлов

```
app/api/dashboard/
├── __init__.py             # router = APIRouter(); ws_router = APIRouter()
├── auth.py                 # JWT verify dep, magic-link verify endpoint
├── deps.py                 # require_admin() FastAPI dependency
├── ws.py                   # /ws endpoint, fan-out из bus
└── routes/
    ├── stats.py            # /api/stats/*
    ├── users.py            # /api/users/*
    ├── subscriptions.py    # /api/subscriptions/*
    ├── payments.py         # /api/payments/*
    ├── broadcasts.py       # /api/broadcasts/*
    ├── bgift.py            # /api/bgift/*
    ├── gifts.py            # /api/gifts/*
    ├── audit.py            # /api/audit/*
    ├── export.py           # /api/export/*.csv (streaming)
    ├── referrals.py        # /api/referrals/*
    └── incident.py         # /api/incident, /api/special-offer

app/events.py               # Bus (asyncio.Queue subscribers)

dashboard/                  # отдельная папка в корне репо
├── package.json
├── vite.config.ts
├── tailwind.config.js
├── tsconfig.json
├── index.html
├── src/
│   ├── main.tsx
│   ├── App.tsx
│   ├── api/                # fetch-обёртки с Authorization header
│   ├── store/              # zustand или просто React Context
│   ├── pages/              # экраны
│   │   ├── Login.tsx
│   │   ├── Dashboard.tsx   # главная
│   │   ├── Users.tsx
│   │   ├── UserDetail.tsx
│   │   ├── Broadcasts.tsx
│   │   ├── BroadcastCreate.tsx
│   │   ├── Analytics.tsx
│   │   ├── Audit.tsx
│   │   └── ...
│   ├── components/
│   │   ├── Sidebar.tsx
│   │   ├── StatCard.tsx
│   │   ├── UserActionsPanel.tsx
│   │   ├── BroadcastWizard.tsx
│   │   └── ...
│   └── ws/
│       └── useEvents.ts    # WebSocket с auto-reconnect
└── dist/                   # билд, отдаётся через StaticFiles
```

### 5.2. In-process event bus

```python
# app/events.py
import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


class Bus:
    """In-process async bus. Подписчики — asyncio.Queue.
    Каждый WS-клиент создаёт свою очередь через subscribe().
    На publish() кладём событие во все очереди. На overflow
    (медленный клиент) — пропускаем, чтобы не блокировать бота."""

    def __init__(self):
        self._queues: list[asyncio.Queue] = []

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._queues.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        if q in self._queues:
            self._queues.remove(q)

    def publish(self, event: dict[str, Any]) -> None:
        for q in self._queues:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("BUS_QUEUE_FULL — dropping event for slow consumer")


bus = Bus()
```

### 5.3. WebSocket-эндпоинт

```python
# app/api/dashboard/ws.py
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from app.events import bus
from app.api.dashboard.auth import verify_token

ws_router = APIRouter()


@ws_router.websocket("/ws")
async def dashboard_ws(websocket: WebSocket, token: str = Query(...)):
    payload = verify_token(token)
    if not payload:
        await websocket.close(code=4001)
        return
    await websocket.accept()
    q = bus.subscribe()
    try:
        while True:
            event = await q.get()
            await websocket.send_json(event)
    except WebSocketDisconnect:
        pass
    finally:
        bus.unsubscribe(q)
```

### 5.4. Точки `bus.publish(...)` в существующем коде

Добавить ровно эти 6 точек (минимум):

| Файл | Где | Событие |
|---|---|---|
| `database/subscriptions.py` после `approve_payment_atomic` успеха | `bus.publish({"type": "payment:approved", "telegram_id": tg_id, "amount_rubles": x, "tariff": t})` | финансовая live-обновляшка |
| `database/subscriptions.py` после `mark_trial_used` успеха | `bus.publish({"type": "trial:activated", "telegram_id": tg_id})` | счётчики trial'ов |
| `database/users.py` после `create_user` | `bus.publish({"type": "user:registered", "telegram_id": tg_id})` | прирост юзеров |
| `database/admin.py` после `create_broadcast` | `bus.publish({"type": "broadcast:created", "broadcast_id": bid})` | бот сразу подхватит |
| `database/admin.py` после `admin_grant_access_atomic` | `bus.publish({"type": "admin:grant", "telegram_id": tg_id, "by": admin_id})` | audit timeline |
| `database/users.py` после `increase_balance` / `decrease_balance` | `bus.publish({"type": "balance:changed", "telegram_id": tg_id, "delta": d})` | UI юзеркарточки |

**Импорт `from app.events import bus`** — в начале каждого файла.

### 5.5. /admin magic-link

```python
# в app/handlers/admin/base.py, рядом с существующим хендлером /admin
import jwt
import config

@admin_base_router.message(Command("admin"))
async def cmd_admin_dashboard(message: Message):
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        return
    token = jwt.encode(
        {"sub": message.from_user.id, "role": "admin",
         "exp": datetime.now(timezone.utc) + timedelta(minutes=10)},
        config.JWT_SECRET, algorithm="HS256",
    )
    url = f"{config.DASHBOARD_BASE_URL}/dashboard/?login={token}"
    await message.answer(
        "🛡 Панель администратора",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Открыть дашборд", url=url)],
        ]),
    )
```

### 5.6. Auth-dep для роутов

```python
# app/api/dashboard/deps.py
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt
import config

bearer = HTTPBearer(auto_error=False)


def require_admin(creds: HTTPAuthorizationCredentials = Depends(bearer)):
    if not creds:
        raise HTTPException(401, "Missing token")
    try:
        payload = jwt.decode(creds.credentials, config.JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(401, "Invalid token")
    if payload.get("role") != "admin":
        raise HTTPException(403, "Forbidden")
    return payload
```

Применяется ко всем роутам:

```python
router = APIRouter(dependencies=[Depends(require_admin)])
```

---

## 6. ENV

```env
# Новые
JWT_SECRET=<openssl rand -hex 32>
DASHBOARD_BASE_URL=https://atlas.up.railway.app   # тот же домен, что и webhook

# Уже есть, проверь
BOT_TOKEN=...
DATABASE_URL=...
ADMIN_TELEGRAM_ID=...
REDIS_URL=...   # опционально, для будущего разделения
```

В `config.py` добавить:

```python
JWT_SECRET = env("JWT_SECRET", required=True)
DASHBOARD_BASE_URL = env("DASHBOARD_BASE_URL", default="")
DASHBOARD_ENABLED = bool(JWT_SECRET and DASHBOARD_BASE_URL)
```

В `app/api/__init__.py` mount дашборда обернуть в `if config.DASHBOARD_ENABLED`.

---

## 7. Что использовать на фронте

Стек 1:1 как в исходном ТЗ (но React+Tailwind, всё остальное Python):

- **Vite** + **React 18** + **TypeScript**
- **Tailwind CSS** для стиля
- **shadcn/ui** (опционально, для базовых компонент)
- **React Query** (TanStack Query) для кеша REST-вызовов
- **Zustand** для глобального стора (или Context)
- **Recharts** для графиков

WebSocket-клиент с auto-reconnect — пример из исходного ТЗ
(useChatStore.tsx) переносится 1:1 на JS, только endpoint
`/dashboard/ws?token=...`.

Magic-link приём:

```typescript
// src/main.tsx
const login = new URLSearchParams(location.search).get("login");
if (login) {
  localStorage.setItem("token", login);
  history.replaceState({}, "", location.pathname);
}
```

---

## 8. Чек-лист готовности

- [ ] `app/events.py` создан, Bus импортируется
- [ ] 6 точек `bus.publish(...)` в существующем коде добавлены
- [ ] `app/api/dashboard/` пакет создан, auth + deps + ws работают
- [ ] Все эндпоинты из секции 3 имплементированы (по фазам)
- [ ] `/admin` команда в боте генерит JWT URL
- [ ] `dashboard/` React-приложение собирается в `dashboard/dist`
- [ ] `app/api/__init__.py` mount'ит StaticFiles + dashboard router
- [ ] ENV `JWT_SECRET` и `DASHBOARD_BASE_URL` прописаны в Railway
- [ ] Воркеры в `main.py` не тронуты, не дублированы
- [ ] Никаких прямых вызовов `remnawave_api.*` write-операций — только high-level
- [ ] Никаких прямых `approve_payment_atomic` / `grant_access` /
  `finalize_purchase` / `mark_trial_used` — только `admin_grant_access_atomic`
- [ ] Тест-сценарий: login → главная грузится → создать тестовую рассылку
  → проверить, что бот её разослал в течение 30 секунд (через broadcast worker)
- [ ] Тест-сценарий: выдать 7 дней тестовому юзеру через дашборд →
  проверить, что записалось в БД + в audit_log + Remnawave обновлён

---

## 9. Известные грабли (учитывать заранее)

1. **TIMESTAMPTZ обязательно везде.** Не смешивать наивные datetime
   и UTC-aware. Используй `_to_db_utc()` и `_from_db_utc()`.
2. **Балансы в копейках в БД, в рублях в API.** Конверсия на границе.
3. **Кастомные tg-emoji `<tg-emoji emoji-id="...">` — только проверенные
   ID.** Чужие айди → `DOCUMENT_INVALID`, рассылка не уходит.
4. **CSV-экспорт через `StreamingResponse`**, не аккумулировать в памяти —
   на 358k юзеров это OOM.
5. **VIP > special_offer > personal discount** — приоритет в
   `calculate_final_price`. Дашборд показывает только итог.
6. **fast_expiry_cleanup освобождает DB-коннект перед VPN-вызовом** —
   не открывай долгие транзакции в дашборде на тех же подписках.
7. **WebSocket overflow** — `asyncio.QueueFull` пропускается, не
   блокируется. Медленный фронт не должен ронять бота.
8. **JWT TTL 10 минут** — короткий, magic-link одноразовый. После
   получения дашборд может рефрешить через отдельный refresh-эндпоинт
   (с longer-lived refresh-токеном), если захочется. Сейчас — пускай
   юзер жмёт /admin заново, проще.

---

**Итог:** дашборд встраивается в существующий процесс малыми
изменениями. Главное — не дублировать работу воркеров, использовать
high-level функции и переиспользовать готовые DB-эндпоинты. Время
на фазы 1-3 (критичный минимум + рассылки + аналитика) — ~3 недели
одного агента. Фазы 4-5 — ещё неделя.
