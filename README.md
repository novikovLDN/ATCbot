# ATCbot — Atlas Secure VPN Telegram Bot

Telegram bot for managing VPN subscriptions, payments, and user entitlements.

## Stack

- **Python 3.11** + **aiogram 3.x** (async Telegram bot framework)
- **FastAPI + Uvicorn** (webhook receiver)
- **PostgreSQL 16** (asyncpg)
- **Redis** (FSM storage + rate limiting, optional with in-memory fallback)
- **Docker** (non-root, slim image)
- **Railway** (PaaS deployment)

## Quick Start

```bash
# 1. Clone and install
pip install -r requirements.txt

# 2. Configure environment
cp .env.example .env
# Edit .env with your credentials (use LOCAL_ prefix for development)

# 3. Run
APP_ENV=local python main.py
```

## Environment Configuration

All env vars use environment prefix: `PROD_`, `STAGE_`, or `LOCAL_`.

| Variable | Required | Description |
|----------|----------|-------------|
| `{ENV}_BOT_TOKEN` | Yes | Telegram bot token from @BotFather |
| `{ENV}_DATABASE_URL` | Yes | PostgreSQL connection string |
| `{ENV}_ADMIN_TELEGRAM_ID` | Yes | Admin's Telegram ID for alerts |
| `{ENV}_WEBHOOK_URL` | Yes | Public URL for Telegram webhooks |
| `{ENV}_WEBHOOK_SECRET` | Yes | HMAC secret for webhook validation |
| `{ENV}_REDIS_URL` | No | Redis URL (fallback: in-memory) |
| `{ENV}_XRAY_API_URL` | No | VPN API endpoint |
| `{ENV}_XRAY_API_KEY` | No | VPN API authentication key |
| `{ENV}_PLATEGA_MERCHANT_ID` | No | SBP payment provider ID |
| `{ENV}_PLATEGA_SECRET` | No | SBP payment provider secret |
| `{ENV}_CRYPTOBOT_API_TOKEN` | No | CryptoBot payment token |
| `LOG_FORMAT` | No | `text` (default) or `json` for structured logging |

## Architecture

```
main.py                     # Entry point, lifecycle management
config.py                   # Environment configuration
app/
  api/                      # FastAPI webhook endpoints
    telegram_webhook.py     # Telegram update receiver
    payment_webhook.py      # Payment provider webhooks
  handlers/                 # Telegram command/callback handlers
    common/keyboards.py     # Shared keyboard builders
    admin/                  # Admin panel handlers
  services/                 # Business logic layer
    payments/               # Payment processing + confirmation
    subscriptions/          # Subscription management
    trials/                 # Trial period logic
    notifications/          # Reminder system
    referrals/              # Referral program
    admin_alerts.py         # Critical admin alert service
  core/                     # Middleware, logging, rate limiting
database/                   # Database access layer (asyncpg)
migrations/                 # Numbered SQL migrations
```

## Background Workers

| Worker | Interval | Purpose |
|--------|----------|---------|
| `trial_notifications` | 5 min | Send trial period reminders |
| `auto_renewal` | 5 min | Process subscription auto-renewals |
| `activation_worker` | 5 min | Activate pending VPN subscriptions |
| `health_check` | 10 min | DB + Redis connectivity monitoring |

## Testing

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```

## Deployment

```bash
docker build -t atcbot .
docker run --env-file .env atcbot
```

Single-instance deployment enforced via PostgreSQL advisory lock in production.

## Security

- Webhook authentication: HMAC signature validation
- Rate limiting: per-user sliding window with Redis backend
- SQL injection: parameterized queries only (no f-string SQL)
- Admin access: single ADMIN_TELEGRAM_ID, fail-closed
- Private chat only: groups/channels rejected via middleware
- Payment webhooks: provider-specific signature verification
- Critical alerts: payment/subscription failures sent to admin
