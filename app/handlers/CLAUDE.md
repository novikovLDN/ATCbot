# app/handlers — CLAUDE.md

Слой роутинга aiogram. Дополняет корневой `CLAUDE.md`.

## Регистрация роутеров

`main.py:28` → `from app.handlers import router as root_router`. `app/handlers/__init__.py` агрегирует
в порядке: **callbacks → user → payments → admin → game → `unknown_message_router`** (catch-all
ПОСЛЕДНИМ, только `default_state`). Порядок = порядок разрешения; `unknown` всегда в конце.

Порядок импортов слоёв (во избежание циклов, из `docs/archive/HANDLERS_REFACTOR_PLAN.md`): common → callbacks →
user → payments → admin. Каждый следующий слой может импортить из предыдущих, **не наоборот**.

## Подпапки

- `common/` — `states.py` (все FSM `StatesGroup`), `guards.py` (`ensure_db_ready_message/callback`),
  `utils.py`, `keyboards.py`, `screens.py`, `emoji.py`.
  Остальные модули импортят общее **только** из `common/`, не друг у друга.
- `callbacks/` — навигация, язык, subscription-колбэки, gift, beta_apply, bypass_setup.
- `user/` — start, profile, connect, devices, referrals, support, language_commands, bypass_gift_setup.
- `payments/` — buy, callbacks, payment_method_selection (экран выбора способа оплаты `/buy`), promo_fsm,
  topup_fsm, spotify/steam/telegram_premium/telegram_stars_purchase, broadcast_offers (кнопки рассылок
  из дашборда: скидки, подарки, промо-трафик).
- `admin/` — `base.py` (`/admin`: ссылка на дашборд, «Написать пользователю», сброс пароля; чат
  админ → пользователь; `/platega_sub_status`) и 🔒 `apple_id_delivery.py` / `spotify_delivery.py`.
  Старая админка удалена 2026-09-14 — новые админ-функции делаем в дашборде, не в боте.

## NEVER (специфично для хендлеров)

- **Не хардкодить текст в хендлере и не хардкодить `"ru"`.** Все строки — через
  `app.i18n.get_text(user_language, "namespace.key")` (dot-namespace: `main.profile`, `common.back`).
- **Не импортировать `app.core.i18n`** — сломан (нет `manager.py`, 0 импортов). Живой i18n — только
  `app/i18n/` (RU canonical + EN; legacy языки de/ar/kk/tj/uz падают на RU через fallback, исключений не кидает).

## Middleware — разбросаны (нет `app/middlewares/`)

`app/core/{chat_filter,rate_limit,telegram_error,last_seen,concurrency}_middleware.py` + аномалия
`app/utils/referral_middleware.py` (лежит не с остальными — известное расхождение, не баг).

## Валидация правок роутинга

При переносе/дроблении хендлеров — `grep -c "@router\."` до и после, чтобы не потерять хендлер 1:1.
