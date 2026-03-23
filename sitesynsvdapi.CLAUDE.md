# ATCbot — Atlas Secure VPN Telegram Bot

## Project Overview

Production Telegram bot for VPN subscription management built with Python 3.11, aiogram 3.x (async), FastAPI, PostgreSQL 16 (asyncpg), Redis 5.2. Deployed on Railway PaaS via Docker. Supports 7 languages (ru, en, de, ar, kk, tj, uz).

**Product**: Telegram bot selling VPN subscriptions (basic/plus/business tiers) with multiple payment providers, referral program, promo codes, balance system, admin panel, game mechanics (dice, farm).

---

## Architecture

```
main.py (Entry Point)
  ├── FastAPI + Uvicorn (webhook server)
  ├── aiogram Dispatcher (Telegram handlers)
  ├── Middleware Stack:
  │   ├── ConcurrencyLimiterMiddleware (max 20 concurrent updates)
  │   ├── TelegramErrorBoundaryMiddleware
  │   ├── PrivateChatOnlyMiddleware
  │   └── GlobalRateLimitMiddleware
  ├── Background Workers (9 tasks)
  └── Graceful Shutdown (webhook delete → task cancel → lock release → pool close)
```

### Directory Structure

```
ATCbot/
├── app/                    # Main application package
│   ├── api/                # FastAPI endpoints (payment_webhook, telegram_webhook)
│   ├── constants/          # Loyalty constants
│   ├── core/               # Infrastructure (middleware, rate limiting, logging, feature flags)
│   ├── handlers/           # Telegram handlers
│   │   ├── admin/          # Admin panel (access, activations, audit, broadcast, finance, stats)
│   │   ├── callbacks/      # Inline button handlers (language, payments, subscriptions, navigation)
│   │   ├── common/         # Shared utilities (keyboards, states, decorators, guards)
│   │   ├── payments/       # Payment FSMs (buy, topup, withdraw, promo)
│   │   ├── user/           # User commands (/start, /profile, /support, /connect, /referrals)
│   │   └── game.py         # Game mechanics (dice, farming)
│   ├── i18n/               # Translation strings (7 languages)
│   ├── services/           # Business logic layer
│   │   ├── activation/     # VPN activation (3-phase: prefetch → API call → atomic DB update)
│   │   ├── admin/          # Admin operations
│   │   ├── notifications/  # User notifications
│   │   ├── payments/       # Payment processing & confirmation
│   │   ├── referrals/      # Referral rewards
│   │   ├── subscriptions/  # Subscription lifecycle
│   │   ├── trials/         # Trial period management
│   │   └── vpn/            # VPN integration layer
│   ├── utils/              # Utilities (redis, security, audit, retry, telegram_safe)
│   └── workers/            # Background workers (farm_notifications)
├── database/               # Database access layer
│   ├── core.py             # Connection pooling, DB_READY flag, datetime conversion
│   ├── subscriptions.py    # Subscription queries & lifecycle (largest file, ~218 KB)
│   ├── users.py            # User queries & balance operations
│   └── admin.py            # Admin operations, audit logs, broadcasts
├── migrations/             # 36 SQL migration files
├── tests/                  # pytest + pytest-asyncio
├── xray_api/               # VPN API microservice (separate)
├── scripts/                # Operational scripts (xray resync, vpn audit)
├── docs/                   # 83 markdown documentation files
├── Root workers:           # activation_worker, auto_renewal, fast_expiry_cleanup,
│                           # trial_notifications, reminders, broadcast_service,
│                           # xray_sync, admin_notifications, healthcheck
├── config.py               # Environment config with prefix isolation (PROD_/STAGE_/LOCAL_)
├── handlers.py             # Handler helper functions (safe_edit, promo sessions)
├── vpn_utils.py            # Xray VPN API client (add/remove/update/upgrade users)
├── platega_service.py      # Platega SBP payment provider
├── cryptobot_service.py    # CryptoBot crypto payment provider
└── Dockerfile              # python:3.11-slim, non-root user
```

---

## Key Processes & Background Workers

### Startup Sequence (main.py)

1. Logging setup (QueueHandler → background thread, PII sanitization)
2. Bot + Dispatcher creation (Redis FSM storage or MemoryStorage fallback)
3. Middleware stack registration
4. Database initialization (graceful degradation if fails → DB_READY=False)
5. PostgreSQL advisory lock (key: 987654321) — prevents duplicate instances in PROD
6. Background workers spawned (gated by DB_READY flag)
7. Webhook set + verification (hard failure if fails)
8. Uvicorn server start (0.0.0.0:{PORT})

### Background Workers

| Worker | File | Interval | Purpose |
|--------|------|----------|---------|
| **Activation Worker** | `activation_worker.py` | 5 min | Activate pending VPN subscriptions via Xray API |
| **Fast Expiry Cleanup** | `fast_expiry_cleanup.py` | 60 sec | Revoke expired VPN access, mark subscriptions expired |
| **Auto-Renewal** | `auto_renewal.py` | 10 min | Renew expiring subscriptions from user balance |
| **Reminders** | `reminders.py` | 45 min | Send expiry reminders (7d, 3d, 1d, 24h, 3h before expiry) |
| **Trial Notifications** | `trial_notifications.py` | 5 min | Trial expiry notifications + trial subscription cleanup |
| **Farm Notifications** | `app/workers/farm_notifications.py` | 30 min | Game farm plant status notifications |
| **Xray Sync** | `xray_sync.py` | 5 min | Sync active subscriptions to Xray (crash recovery) |
| **Health Check** | `healthcheck.py` | 10 min | DB + Redis connectivity monitoring, admin alerts |
| **DB Retry** | `main.py` (inline) | 30 sec | Retry DB init if startup failed, spawn workers on success |

**Worker Contract**: All workers follow stateless iteration pattern — safe-to-skip, no unbounded retries, cooperative yield, external dependency graceful degradation. Error classification: domain (not retried) vs transient (retried with backoff).

### Shutdown Sequence

1. Delete Telegram webhook
2. Cancel all background tasks (await each with CancelledError handling)
3. Release PostgreSQL advisory lock
4. Close Redis client
5. Close database pool
6. Close bot session

---

## Payment Flow

### Providers
- **Telegram Payments** (YuKassa): native Telegram payment system
- **Platega** (SBP): Russian bank transfer, +11% markup
- **CryptoBot**: cryptocurrency (USDT, TON, BTC, ETH, LTC, TRX)
- **Telegram Stars**: native Telegram currency
- **Balance**: internal user balance (topup via any provider)

### Payment Lifecycle

```
1. Purchase Creation
   create_subscription_purchase(telegram_id, tariff, period, price)
   → pending_purchases record (status="pending", 30-min TTL)

2. Invoice Creation (provider-specific)
   Telegram: send_invoice(payload="purchase:{purchase_id}")
   Platega:  POST /transaction/process → redirect_url
   CryptoBot: POST /createInvoice → pay_url

3. Webhook Processing
   POST /webhooks/{provider}
   → Verify signature (HMAC)
   → Extract purchase_id from payload
   → Validate amount (±1 RUB tolerance)
   → Check idempotency (purchase_id + status)

4. Finalization (ATOMIC transaction)
   → Mark pending_purchases.status = "paid"
   → Create payments record
   → Create/Update subscription (activation_status="pending" for new)
   → Process referral rewards

5. VPN Activation (background worker)
   → Phase 1: Pre-fetch subscription (release DB connection)
   → Phase 2: POST /add-user to Xray API (no DB connection held)
   → Phase 3: Atomic DB update with pg_advisory_lock(subscription_id)
   → Orphan UUID cleanup on Phase 3 failure
```

### Idempotency
- **Payments**: purchase_id as correlation key, status check prevents duplicate processing
- **Activation**: pg_advisory_lock + re-check state inside lock
- **Workers**: flag-based deduplication (reminder_sent, trial_notif_*_sent)

---

## VPN Integration (Xray/VLESS)

### API Operations (vpn_utils.py → XRAY_API_URL)

| Endpoint | Purpose |
|----------|---------|
| `POST /add-user` | Create VLESS client (uuid, tariff, expiry) |
| `POST /update-user/{uuid}` | Extend expiry |
| `POST /upgrade-to-plus/{uuid}` | Add plus whitelist access |
| `POST /remove-plus/{uuid}` | Remove plus access |
| `POST /remove-user/{uuid}` | Fully disable client |
| `GET /health` | Health check |

**Security**: HTTPS mandatory in production, no private IPs, X-API-Key auth, response validation, 2 retries on transient errors only (5xx, timeout).

### Subscription Types
- **basic**: Single VLESS link (REALITY + XTLS Vision)
- **plus**: Base64 subscription URL (multiple servers, auto-update)
- **biz_***: 6 business tiers (biz_starter through biz_ultimate) with country-specific pricing

---

## Database

### Connection Pool
- AsyncPG: min 2, max 25 connections
- Acquire timeout: 10s, command timeout: 30s
- DB_READY flag gates all worker operations
- Graceful degradation: bot continues in degraded mode if DB unavailable

### Key Tables
- `users` — telegram_id (PK), balance_kopecks, language, farm_plots (JSONB)
- `subscriptions` — status, activation_status, uuid, vpn_key, expires_at, auto_renew
- `payments` — provider, amount, status, purchase_id (FK)
- `pending_purchases` — purchase_id (UUID PK), status, tariff, period_days, 30-min TTL
- `referrals` — referrer/referee tracking, reward status
- `promocodes` — code, discount_percent, usage limits, expiry
- `admin_broadcasts` — broadcast audit
- `audit_log` — all major operations logged
- `balance_transactions` — balance change audit trail

### SQL Patterns
- Advisory locks: `pg_advisory_xact_lock(telegram_id)` for balance operations
- `SELECT...FOR UPDATE SKIP LOCKED` for auto-renewal (prevents races)
- 3-phase pattern: DB read → external API call (outside tx) → DB update
- DateTime: naive UTC in DB, aware UTC in app (`_to_db_utc` / `_from_db_utc`)
- All queries parameterized (no f-string interpolation)

---

## Rate Limiting

### Global (middleware)
- Normal: 30 req/60s per user
- `/start`: 8 req/60s
- Flood: 60+ req/60s → 5-minute temporary ban
- Implementation: Redis sorted sets (sliding window) or in-memory fallback
- Memory safety: MAX_TRACKED_USERS=50,000, evicts oldest 50% when exceeded

### Per-Action (service layer)
- Token bucket algorithm, per-user per-action
- `admin_action`: 10/60s, `payment_init`: 5/60s, `trial_activate`: 1/3600s
- Soft fail: returns (is_allowed, error_message), no exceptions

---

## Feature Flags

Environment variables, immutable after startup, all default to True:
- `FEATURE_PAYMENTS_ENABLED`
- `FEATURE_VPN_PROVISIONING_ENABLED`
- `FEATURE_AUTO_RENEWAL_ENABLED`
- `FEATURE_BACKGROUND_WORKERS_ENABLED`
- `FEATURE_ADMIN_ACTIONS_ENABLED`

---

## Security

### Input Validation (app/utils/security.py)
- Telegram ID: type + positive + max 2^63
- Callback data: regex whitelist patterns
- Promo codes: alphanumeric + underscore only, max 50 chars
- Message text: max 4096 chars

### Authorization
- Admin: `is_admin(telegram_id)` — single admin (ADMIN_TELEGRAM_ID)
- Ownership: `owns_resource(telegram_id, resource_telegram_id)` — fail-closed
- `@admin_only` decorator for admin handlers

### Webhook Security
- Telegram: secret token verification
- Platega: X-MerchantId + X-Secret header verification (hmac.compare_digest)
- CryptoBot: HMAC-SHA256(body, SHA256(API_TOKEN)) signature verification
- Content-length limit: 1 MB

### Logging Security
- PII sanitization filter: masks VLESS URLs, bot tokens, DB URLs, UUIDs, Bearer tokens
- Secret masking: `mask_secret()` shows only last 4 chars
- `sanitize_for_logging()` recursively masks sensitive dict keys
- QueueHandler → background thread (event loop never blocks on I/O)
- LOG_FORMAT=json for structured JSON logging in production

---

## Configuration (config.py)

### Environment Isolation
Variables use prefix: `{APP_ENV}_KEY` where APP_ENV = prod | stage | local.
Direct usage of BOT_TOKEN, DATABASE_URL etc. blocked (raises error).

### Required Variables
```
{ENV}_BOT_TOKEN              # Telegram bot token
{ENV}_DATABASE_URL           # PostgreSQL connection
{ENV}_ADMIN_TELEGRAM_ID      # Admin user ID
{ENV}_WEBHOOK_URL            # Webhook endpoint
{ENV}_WEBHOOK_SECRET         # HMAC secret
```

### Optional Variables
```
{ENV}_REDIS_URL              # FSM storage + rate limiting (fallback: memory)
{ENV}_XRAY_API_URL           # VPN API endpoint (HTTPS in prod)
{ENV}_XRAY_API_KEY           # VPN API auth
{ENV}_PLATEGA_MERCHANT_ID    # SBP payment merchant
{ENV}_PLATEGA_SECRET         # SBP payment secret
{ENV}_CRYPTOBOT_API_TOKEN    # Crypto payment token
{ENV}_TG_PROVIDER_TOKEN      # Telegram card payments (required in PROD)
{ENV}_VPN_SERVER_URL         # Plus subscription URL
{ENV}_MINI_APP_URL           # Mini app URL
{ENV}_BOT_USERNAME           # Bot username
{ENV}_MINI_APP_NAME          # Mini app name
PUBLIC_BASE_URL              # Public webhook base URL
LOG_FORMAT                   # "text" (default) or "json"
XRAY_SYNC_ENABLED            # Enable xray sync worker (default: false)
```

### Tariffs
- **basic**: 30/90/180/365 days (RUB prices in TARIFFS dict)
- **plus**: 30/90/180/365 days (higher prices)
- **biz_***: 6 tiers (starter, growth, scale, pro, enterprise, ultimate) with country multipliers (0.9-1.2x)
- **Stars**: separate TARIFFS_STARS dict for Telegram Stars pricing
- **SBP markup**: +11% on Platega payments

---

## Testing & CI/CD

### Tests
```
tests/
├── conftest.py                    # Fixtures
├── services/                      # Service layer tests
│   ├── test_admin.py
│   ├── test_payments.py
│   ├── test_subscriptions.py
│   └── test_trials.py
├── integration/
│   └── test_vpn_entitlement.py
└── test_webhook_signatures.py
```

### CI Pipeline (GitHub Actions)
- Lint: ruff + compileall
- Tests: pytest with PostgreSQL 16
- Security: pip-audit + safety
- Docker build verification
- Migration integrity check

### CD Pipeline
- CI gate → Deploy to staging (stage branch) → Deploy to production (main branch)
- Post-deploy health check
- Dependabot: weekly pip/actions updates, monthly Docker updates

---

## Deployment

- **Platform**: Railway PaaS
- **Container**: python:3.11-slim, non-root user (appuser UID 1000)
- **Entry**: `python main.py`
- **Single instance**: PostgreSQL advisory lock prevents duplicates
- **Port**: injected by Railway (PORT env var)

---

## Error Handling Taxonomy

### Hard Failures (sys.exit)
- Missing BOT_TOKEN, ADMIN_TELEGRAM_ID, WEBHOOK_URL, WEBHOOK_SECRET
- Webhook set/verification failed
- Advisory lock acquisition failed (PROD only)

### Graceful Degradation
- DB init fails → DB_READY=False, workers skipped, retry every 30s
- Redis unavailable → MemoryStorage (FSM states lost on restart)
- VPN API disabled → VPN operations blocked, bot continues
- Worker iteration fails → log, retry next cycle

### Worker Error Pattern
```python
while True:
    try:
        await do_work()  # 15-120s max
    except asyncio.CancelledError:
        break  # graceful shutdown
    except Exception:
        log_and_classify(domain/infra/dependency/unexpected)
    await asyncio.sleep(SAFE_MINIMUM_SLEEP)
```

---

## Site Integration (Atlas Secure Website ↔ Bot Sync)

### Overview
Website and bot share a common PostgreSQL database. Site integration adds bi-directional sync:
- **Site → Bot**: User clicks "Перейти в Telegram" on site → `/start TOKEN` → bot links accounts
- **Bot → Site**: User pays in bot → `POST /api/bot/extend` → site extends subscription

### Components
- **Config**: `SITE_API_URL`, `BOT_API_KEY` env vars → `SITE_INTEGRATION_ENABLED` flag
- **Client**: `app/services/site_client.py` — HTTP client with X-Bot-Api-Key auth
- **Start handler**: `app/handlers/user/start.py` — 16-char hex token detection
- **Payment sync**: `app/services/payments/confirmation.py` + `app/handlers/payments/payments_messages.py`

### API Endpoints (all require X-Bot-Api-Key header)
| Method | URL | Purpose |
|--------|-----|---------|
| GET | `/api/bot/user?token=XXX` | Get user by telegram_link_token |
| GET | `/api/bot/user-by-telegram?telegram_id=XXX` | Get user by telegram_id |
| POST | `/api/bot/link` | Link Telegram to site account |
| POST | `/api/bot/extend` | Extend subscription after bot payment |

### Flow: Site Token Linking (`/start TOKEN`)
1. User registers on site → gets unique `telegram_link_token` (16 hex chars)
2. User clicks "Перейти в Telegram" → `https://t.me/atlassecure_bot?start=TOKEN`
3. Bot receives `/start TOKEN`:
   - Calls `GET /api/bot/user?token=TOKEN` → gets user data
   - Calls `POST /api/bot/link` with `{token, telegramId}` → links accounts
   - If user has active subscription → shows main menu (skips trial)
   - If no subscription → normal `/start` flow continues

### Flow: Payment Sync (Bot → Site)
1. User pays in bot → `finalize_purchase()` succeeds
2. Fire-and-forget `POST /api/bot/extend` with `{telegramId, days}`
3. Site extends subscription, regenerates VPN key if deleted, credits referrer

### Safety Guarantees
- **Feature flag**: disabled if `SITE_API_URL` or `BOT_API_KEY` not set
- **Best-effort**: all site API calls wrapped in try/except, failures logged but never break bot
- **Fire-and-forget**: extend calls run as background tasks, don't block payment webhook
- **No existing code changed**: all additions are new `if` blocks and new files

---

## Key Design Decisions

1. **DB is source of truth** — Xray is stateless executor, xray_sync recovers from crashes
2. **External API calls outside DB transactions** — 3-phase pattern prevents long-held connections
3. **Advisory locks for critical sections** — per-subscription (activation), per-user (balance)
4. **Idempotency everywhere** — purchase_id, activation_status flags, reminder flags
5. **Fail-closed security** — invalid input returns False, unknown callbacks rejected
6. **Cooperative async** — yield between batches, timeouts on all operations
7. **No unbounded retries** — max attempts + safe sleep intervals
8. **Environment prefix isolation** — prevents cross-environment config leaks
