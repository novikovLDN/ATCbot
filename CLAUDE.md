# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Stack

Python 3.11 · aiogram 3.x (webhook-only) · FastAPI + uvicorn · asyncpg / PostgreSQL 16 · Redis (optional, with in-memory fallback) · Docker (non-root) · Railway PaaS.

## Common commands

```bash
# Run locally (use LOCAL_ prefixed env vars in .env)
APP_ENV=local python main.py

# Tests
pip install -r requirements-dev.txt
pytest                                          # all tests
pytest tests/services/test_payments.py          # one file
pytest tests/services/test_payments.py::test_x  # one test
pytest -k "trial and not expired"               # by name pattern

# Lint (only one configured — ruff; rules in pyproject.toml)
ruff check .
ruff check . --fix

# Syntax-only sweep (matches CI)
python -m compileall . -q -x '.venv|__pycache__'

# Apply DB migrations stand-alone (against any reachable DB)
APP_ENV=local python -c "import asyncio, database; asyncio.run(database.init_db())"
```

CI runs ruff, pytest (with a Postgres 16 service container), `pip-audit`/`safety` (non-blocking), a Docker build, and applies every `migrations/*.sql` against a fresh DB. Mirror these locally before pushing.

## Environment variables — non-obvious rules

- `APP_ENV` ∈ {`prod`, `stage`, `local`} (defaults to `prod`). All other env vars **must** use that uppercase prefix (e.g. `STAGE_BOT_TOKEN`, `PROD_DATABASE_URL`). `config.py` reads them via `env("BOT_TOKEN")` which prepends the prefix.
- `config.py` actively **rejects** unprefixed `BOT_TOKEN`, `DATABASE_URL`, `ADMIN_TELEGRAM_ID`, `TG_PROVIDER_TOKEN` and exits with a clear error — do not add fallback reads to bare names.
- Required regardless of env: `BOT_TOKEN`, `ADMIN_TELEGRAM_ID`, `DATABASE_URL`, `WEBHOOK_URL`, `WEBHOOK_SECRET`. `TG_PROVIDER_TOKEN` is required only in `prod`.
- `FEATURE_*_ENABLED` kill switches (no env prefix, see `app/core/feature_flags.py`) default to `true` and are read once at startup; do not toggle them at request time.

## Architectural conventions

### Layering — keep handlers thin

`app/handlers/**` is **routing + presentation only**. Business logic lives in `app/services/<domain>/service.py` (`payments`, `subscriptions`, `trials`, `notifications`, `referrals`, `admin`, `activation`, `vpn`). Services import `database` and `config`, never `aiogram` types — this keeps them unit-testable. Domain failures are signalled with typed exceptions in each `<domain>/exceptions.py`; transient infra errors are retried, domain errors are not (see the policy header in `app/services/payments/service.py`).

`handlers.py` at the repo root is a **legacy file (~1080 lines)** marked "STAGE STABLE SNAPSHOT — do NOT change behavior without test/log/rollback". The migration target is `app/handlers/<area>/`; see `docs/HANDLERS_REFACTOR_PLAN.md`. New handlers go under `app/handlers/`, never into `handlers.py`.

### Database package — flat re-export over a split module

`database/` is split into `core.py`, `users.py`, `subscriptions.py`, `traffic.py`, `admin.py` (each thousands of lines) but `database/__init__.py` re-exports every public symbol so callers do `import database; database.get_user(...)`. The `__init__.py` also installs a module-class proxy so writes like `database.DB_READY = True` propagate to `database.core` — do not replace it with a plain `from .core import *`.

PostgreSQL columns are `TIMESTAMP` (naive UTC). The application layer uses **timezone-aware UTC** everywhere. Cross the boundary only via `database.core._to_db_utc` / `_from_db_utc` — passing a naive datetime into asyncpg or storing an aware datetime is a bug. New migrations should follow `024_schema_hardening_timestamptz_uuid_constraints.sql` and the alignment described in `docs/MIGRATION_TIMESTAMPTZ_ALIGNMENT.md`.

Migrations are numbered SQL files in `migrations/`, applied in ascending integer order by `migrations.py` (one transaction per file, recorded in `schema_migrations`). Pick the next free number; never edit an applied migration. Each migration **must be backward-compatible** — code is deployed to run against both old and new schema (see `STEP 1.4` notes in `migrations.py`).

### Telegram update flow

The bot is **webhook-only**. `main.py` registers the webhook with `secret_token=WEBHOOK_SECRET`, then runs uvicorn (FastAPI) on `$PORT` (defaults to 8080). `app/api/telegram_webhook.py` validates the secret with `hmac.compare_digest`, returns 403 on mismatch, then feeds the update into the aiogram `Dispatcher` with a hard 25 s `asyncio.wait_for` timeout (Railway's request budget is 30 s). On any handler exception or timeout, return **200** so Telegram stops retrying — do not propagate errors to the HTTP layer.

Middleware order in `main.py` matters: `ConcurrencyLimiterMiddleware` (semaphore, default 20) → `TelegramErrorBoundaryMiddleware` → `PrivateChatOnlyMiddleware` (groups/channels are rejected here, not in handlers) → `GlobalRateLimitMiddleware` (Redis-backed sliding window, falls back to in-memory; aggressive flooders banned for 5 min).

### Background workers

Long-running asyncio tasks are launched from `main.py` and tracked in `background_tasks` for graceful shutdown. Every worker must follow this exact loop shape (see `AUDIT_REPORT.md` summary at repo root):

```python
while True:
    try:
        await asyncio.wait_for(_run_iteration(), timeout=120.0)
    except asyncio.TimeoutError:
        logger.error(...)
    except Exception:
        logger.exception(...)
    finally:
        log_worker_iteration_end(...)
    await asyncio.sleep(INTERVAL)   # MUST be outside try/finally
```

Putting `asyncio.sleep` inside the `try` block (it gets cancelled on TimeoutError) or inside `finally` (double-counts on cancel) is the bug class that report tracks.

### Safe-startup / degraded mode

The bot **must boot even when the DB is down**. `main.py` catches `init_db()` failures, sets `database.DB_READY = False`, alerts the admin, and starts a 30-s retry task that brings up workers as soon as the DB returns. When adding new workers, gate them on `database.DB_READY` at startup *and* register a recovery branch inside `retry_db_init` so they spin up after late recovery. The `/health` endpoint (`app/api/__init__.py`) returns 503 when `DB_READY` is false or Redis is unreachable.

In `prod`, `main.py` acquires a PostgreSQL advisory lock (`pg_advisory_lock(987654321)`) on a dedicated pool connection and `sys.exit(1)` if it cannot — this is the single-instance guard. Do not bypass it; do not call `release` outside the shutdown finally block.

### Payment webhooks

Providers: `platega_service` (SBP), `cryptobot_service` (Crypto Pay), `lava_service` (card), Telegram Stars, Telegram Premium, Apple ID. All webhooks live in `app/api/payment_webhook.py` and share these guarantees: signature/auth verified per provider, idempotent (duplicate ⇒ 200, no re-activation), amount tolerance ±1 RUB, pending purchases expire after 30 minutes. Outer wrap is `asyncio.wait_for(..., timeout=25.0)`. On a transient failure return 500 (provider retries); on "already processed" return 200.

### I18n

All user-facing strings live in `app/i18n/{ru,en,uz,tj,de,kk,ar}.LANG` dicts and are fetched via `get_text(language, "dotted.key", **fmt)`. Resolution order: requested language → English → return the key itself (never raises). The user's language comes from `app.services.language_service.resolve_user_language(telegram_id)` — do not read it from `message.from_user.language_code` directly, and never hardcode UI strings in handlers.

### VPN backends

Two distinct providers, both optional:
- **Xray API** (REALITY + VLESS) at `XRAY_API_URL` with `XRAY_API_KEY` — owns ports, SNI, public key, short id. The bot only calls the HTTP API and stores the `vless_link` it returns; it never builds links locally. `XRAY_*` link constants in `config.py` are vestigial — `main.py` warns if any code uses them for link generation.
- **Remnawave** (`REMNAWAVE_API_URL`/`REMNAWAVE_API_TOKEN`) — used for bypass-only / traffic-limited tariffs and traffic monitoring. Gated by `REMNAWAVE_ENABLED`.

Either can be unset without crashing the bot — feature gates default to safe-disabled. Subscription activation is async: webhook marks `activation_status="pending"`, `activation_worker.py` retries provisioning on a 5-min interval.

### Async hygiene

Everything in handlers and services is `async`. Do not introduce `requests`, `time.sleep`, sync `psycopg2`, or `urllib` — `httpx`/`aiohttp`/`asyncpg` are already in the dep set. CPU-bound work (image, hashing) goes through `await asyncio.to_thread(...)`. The `redis` package is `redis.asyncio` (the legacy `aioredis` is deprecated and not in `requirements.txt`).

## Linting

`ruff` config in `pyproject.toml` is intentionally narrow — only `E9`, `F63`, `F7`, `F82`, `S` (security), `B` (bugbear). Style and unused-import rules are off, and several Bugbear/Bandit rules are ignored as documented inline (`B904` no-raise-from, `B008` FastAPI defaults, `S608` validated-flag SQL, etc.). Don't add wide rule selections without matching the pattern of existing code.

## Things not to do

- Don't switch the bot to long-polling or call `dp.start_polling()` — webhook-only is enforced.
- Don't read `os.getenv("BOT_TOKEN")` (or any secret) directly; use `config.env("BOT_TOKEN")` so the env prefix is respected.
- Don't construct VLESS links in the bot — fetch from the Xray API.
- Don't use string concatenation / f-strings for SQL; asyncpg parameters are `$1, $2, ...`.
- Don't add code to the legacy root-level `handlers.py`; new code goes under `app/handlers/`.
- Don't put `await asyncio.sleep(...)` inside a worker's `try`/`finally` — it must follow the `finally` block.
- Don't log `message.text` verbatim; users may send credentials. Strip PII, especially payment payloads.
