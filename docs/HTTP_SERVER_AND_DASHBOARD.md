# HTTP-сервер, роуты, HTML-дашборд и редиректы

Стек: **FastAPI**, поднимается **uvicorn** внутри того же asyncio-процесса, что и
бот (один процесс). Точка сборки приложения — `app/api/__init__.py`; запуск
сервера — `main.py`.

---

## 1. Запуск сервера (uvicorn) — `main.py`

```python
# main.py (~639)
import uvicorn
from app.api import app as fastapi_app

uv_config = uvicorn.Config(
    fastapi_app,
    host="0.0.0.0",
    port=config.WEBHOOK_PORT,
    log_level="warning",
)
uv_server = uvicorn.Server(uv_config)
webhook_server_task = asyncio.create_task(uv_server.serve(), name="uvicorn_webhook")
```

Слушает `0.0.0.0:WEBHOOK_PORT`. Перед сервером на проде — reverse-proxy (nginx /
Railway) с HTTPS.

---

## 2. Создание приложения и роуты — `app/api/__init__.py`

```python
from fastapi import FastAPI, Request, Response
from app.api import telegram_webhook, payment_webhook, deeplink_redirect
from app.api import subscription_proxy, sub_aggregator_route

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)  # Swagger выключен

app.add_middleware(RequestSizeLimitMiddleware, max_size=1 * 1024 * 1024)  # 413 на большие тела

app.include_router(telegram_webhook.router)   # приём апдейтов Telegram
app.include_router(payment_webhook.router)     # вебхуки платёжных провайдеров
app.include_router(deeplink_redirect.router)   # GET /open/{client} (HTML-редирект в клиент)

# Опционально (по флагам config):
if config.SUBSCRIPTION_PROXY_ENABLED:
    app.include_router(subscription_proxy.router)     # /sub/{uuid}, /api/sub/{token} → 302
if config.SUB_AGGREGATOR_ENABLED:
    app.include_router(sub_aggregator_route.router)   # GET /a/{token} (склеенная подписка)

@app.get("/health")   # health-check (200 ok / 503 degraded)
async def health(): ...
```

---

## 3. HTML-дашборд (React SPA) — тот же `app/api/__init__.py`

Дашборд — **отдельное React+Vite SPA** (`dashboard/`), собирается в
`dashboard/dist`. FastAPI отдаёт его как статику; API и WebSocket — отдельными
роутерами под тем же префиксом `/dashboard`.

```python
if config.DASHBOARD_ENABLED:                         # включается JWT_SECRET + DASHBOARD_BASE_URL
    from app.api import dashboard as _dashboard
    app.include_router(_dashboard.router,    prefix="/dashboard/api")   # REST: auth, stats, users…
    app.include_router(_dashboard.ws_router, prefix="/dashboard")       # WebSocket /dashboard/ws

    _dist = ".../dashboard/dist"
    if os.path.isdir(_dist):
        from fastapi.staticfiles import StaticFiles
        # html=True → любой не-API путь под /dashboard отдаёт index.html
        # (SPA-роутинг на клиенте). Ассеты (js/css) — из dist.
        app.mount("/dashboard", StaticFiles(directory=_dist, html=True), name="dashboard-spa")
```

Итог по `/dashboard`:
- `/dashboard/api/*` — REST-бэкенд дашборда (`app/api/dashboard/`).
- `/dashboard/ws` — WebSocket (fan-out событий из `app.events.bus`).
- `/dashboard/*` (всё остальное) — **статический `index.html`** SPA + ассеты.
- Вход в дашборд — **WebAuthn** (passkey), не логин/пароль.

> `html=True` в `StaticFiles` = ключевой момент: неизвестные пути отдают
> `index.html`, чтобы клиентский роутер SPA работал (deep links на страницы).

---

## 4. Редиректы

### 4a. Deep-link редирект в VPN-клиент — `app/api/deeplink_redirect.py`

Telegram блокирует кастомные схемы (`happ://`, `incy://`) в кнопках, поэтому
кнопка ведёт на HTTPS-URL `GET /open/{client}?url=<sub_url>`, а сервер отдаёт
**HTML-страницу**, которая JS-редиректит браузер в схему клиента (и показывает
ссылку с кнопкой «Копировать» как fallback).

```python
@router.get("/open/{client}")
async def deeplink_redirect(client: str, url: str = Query(...)):
    if client not in _SCHEMES:                       # happ / incy / v2raytun
        return HTMLResponse("<h3>Unknown client</h3>", status_code=400)
    deep_link = await _build_deep_link(client, url)  # happ://crypt4/…, incy://crypt1/…, v2raytun://import/…
    if not deep_link:
        return HTMLResponse("<...временно недоступен...>", status_code=503)
    return HTMLResponse(_render_page(client, deep_link))   # HTML с авто-редиректом + Copy
```

### 4b. Subscription-proxy (legacy → 302) — `app/api/subscription_proxy.py`

Старые ссылки подписки (`/sub/{uuid}`, `/api/sub/{token}`) отдают **HTTP 302**
на актуальный Remnawave-URL (включается флагом `SUBSCRIPTION_PROXY_ENABLED`):

```python
return RedirectResponse(target, status_code=302)
```

---

## 5. Карта роутов (сводка)

| Метод/путь | Назначение | Ответ |
|---|---|---|
| POST `/<telegram webhook>` | апдейты Telegram | 200 |
| POST `/<payment webhooks>` | вебхуки platega/lava/cryptobot/wata | 200 |
| GET `/open/{client}?url=` | редирект в VPN-клиент | **HTML** (JS-redirect в `happ://`/`incy://`/`v2raytun://`) |
| GET `/sub/{uuid}`, `/api/sub/{token}` | legacy-подписка | **302** на Remnawave URL (опц.) |
| GET `/a/{token}` | склеенная подписка (агрегатор) | текст подписки (опц.) |
| GET `/dashboard/api/*` | REST дашборда | JSON |
| GET `/dashboard/ws` | WebSocket событий | ws |
| GET `/dashboard/*` | React SPA | **HTML `index.html`** + ассеты |
| GET `/health` | health-check | JSON 200/503 |

---

## Что нужно вернуть (для того, кто спрашивал)

- **Дашборд-HTML** отдаётся не как рендер на сервере, а как **готовый
  `index.html`** SPA из `dashboard/dist` через `StaticFiles(html=True)` под
  `/dashboard`. Данные страница берёт из `/dashboard/api/*` (JSON) и
  `/dashboard/ws`.
- **Редиректы** двух типов: (1) HTML-страница `GET /open/{client}` с
  JS-переходом в deep-link клиента; (2) `RedirectResponse(302)` в
  subscription-proxy на реальный Remnawave-URL.
