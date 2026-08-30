# app/handlers — CLAUDE.md

Слой роутинга aiogram. Дополняет корневой `CLAUDE.md`.

## Регистрация роутеров

`main.py:28` → `from app.handlers import router as root_router`. `app/handlers/__init__.py` агрегирует
в порядке: **callbacks → user → payments → admin → game → `unknown_message_router`** (catch-all
ПОСЛЕДНИМ, только `default_state`). Порядок = порядок разрешения; `unknown` всегда в конце.

Порядок импортов слоёв (во избежание циклов, из `HANDLERS_REFACTOR_PLAN.md`): common → callbacks →
user → payments → admin. Каждый следующий слой может импортить из предыдущих, **не наоборот**.

## Подпапки

- `common/` — `states.py` (все FSM `StatesGroup`), `guards.py` (`ensure_db_ready_message/callback`),
  `decorators.py` (`handler_exception_boundary`), `utils.py`, `keyboards.py`, `screens.py`, `emoji.py`.
  Остальные модули импортят общее **только** из `common/`, не друг у друга.
- `callbacks/` — навигация, язык, subscription-колбэки, `admin_callbacks`, gift, beta_apply, bypass_setup.
- `user/` — start, profile, connect, devices, referrals, support, language_commands, bypass_gift_setup.
- `payments/` — buy, callbacks, promo_fsm, topup_fsm, withdraw_fsm, spotify/steam/telegram_premium/telegram_stars_purchase.
- `admin/` — ~25 файлов (access, activations, broadcast, finance, migration, reconcile, stats, sub_aggregator_cmd, …).

## NEVER (специфично для хендлеров)

- **Не редактировать корневой `/handlers.py`** (49 КБ, `=== STAGE STABLE SNAPSHOT ===`) — мёртвый
  снапшот старого монолита: `grep -c "@router\." handlers.py` = 0, `main.py` его не импортит. Реальный
  роутинг — только здесь, в `app/handlers/*`. Рефактор монолита уже выполнен (структура = `HANDLERS_REFACTOR_PLAN.md`).
- **Не хардкодить текст в хендлере и не хардкодить `"ru"`.** Все строки — через
  `app.i18n.get_text(user_language, "namespace.key")` (dot-namespace: `main.profile`, `common.back`).
- **Не импортировать `app.core.i18n`** — сломан (нет `manager.py`, 0 импортов). Живой i18n — только
  `app/i18n/` (RU canonical + EN; legacy языки de/ar/kk/tj/uz падают на RU через fallback, исключений не кидает).

## Middleware — разбросаны (нет `app/middlewares/`)

`app/core/{chat_filter,rate_limit,telegram_error,last_seen,concurrency}_middleware.py` + аномалия
`app/utils/referral_middleware.py` (лежит не с остальными — известное расхождение, не баг).

## Валидация правок роутинга

При переносе/дроблении хендлеров — `grep -c "@router\."` до и после, чтобы не потерять хендлер 1:1.
