# Telegram Bot ↔ Xray Integration via VPN-API

## Architecture

```
Telegram Bot
   ↓ HTTP (vpn_utils / vpn_client)
vpn-api (Xray API) — localhost:8000 or Cloudflare Tunnel
   ↓
Xray config manager
   ↓
Xray Core (VLESS + REALITY, port 443)
```

- **No direct edits** to `/usr/local/etc/xray/config.json` from the bot
- All client management goes through vpn-api (Xray API)
- Bot does **not** restart Xray
- Operations are idempotent

## VPN-API Endpoints (Xray API)

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/health` | GET | Availability check |
| `/add-user` | POST | Create client (uuid, telegram_id, expiry_timestamp_ms) |
| `/update-user` | POST | Extend subscription (uuid, expiry_timestamp_ms) |
| `/remove-user/{uuid}` | POST | Disable client |

## API Contract

### Create user (POST /add-user)

```json
{
  "uuid": "required-from-request",
  "telegram_id": 123456,
  "expiry_timestamp_ms": 1770000000000
}
```

Response: `{"uuid": "...", "vless_link": "vless://...", "link": "vless://..."}`

### Extend user (POST /update-user)

```json
{
  "uuid": "...",
  "expiry_timestamp_ms": 1770000000000
}
```

### Disable user (POST /remove-user/{uuid})

Path parameter: uuid

## Bot Services

| Module | Responsibility |
|--------|----------------|
| `vpn_utils` | Low-level HTTP client to Xray API |
| `app/services/vpn_client` | High-level facade (create_user, extend_user, disable_user, get_user) |
| `database.grant_access` | Orchestrates create/renew, DB + VPN |

## Data Storage

**subscriptions** table (equivalent to vpn_users):

- telegram_id, uuid, expires_at, status, vpn_key
- One active uuid per user
- On extend → update expires_at
- On disable → remove uuid from Xray, mark expired in DB

## Background Jobs

| Job | Purpose |
|-----|---------|
| `fast_expiry_cleanup` | Expire subscriptions, call remove-user for expired UUIDs |
| `activation_worker` | Activate pending subscriptions (VPN API was down) |
| `xray_sync` | Reconcile DB → Xray after restart |

## Environment Config

```
XRAY_API_URL=https://api.myvpncloud.net  # or http://127.0.0.1:8000
XRAY_API_KEY=<secret>
XRAY_API_TIMEOUT=5
VPN_PROVISIONING_ENABLED=true
```

## Safety Mechanisms

1. **VPN API unreachable**: Do not create user in DB; return error to user
2. **Extend fails**: Do not update expiry in DB
3. **Circuit breaker**: Skips VPN calls when API is failing
4. **Fallback**: `xray_manager.create_vless_user` when VPN API disabled (legacy SSH path)

## Verification

```bash
# On server
curl -s http://127.0.0.1:8000/health
# Expected: {"status":"ok"}
```
