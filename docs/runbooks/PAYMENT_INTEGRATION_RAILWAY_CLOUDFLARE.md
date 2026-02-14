# Payment Integration (Railway + Cloudflare)

**Objective:** Production-ready crypto payment flow via Crypto Bot (Telegram Crypto Pay).

**Flow:** User pays → Webhook confirms → Subscription activated → VPN access granted.

---

## Architecture

```
User (Bot) → Create invoice (Crypto Bot API)
          → User pays
          → Crypto Bot → POST /webhook/payment (signature verified)
          → database.finalize_purchase → vpn_client (grant_access) → vpn-api
          → User receives VPN key
```

- **Idempotent:** Duplicate webhooks → ignored (status already `paid`).
- **Signature:** `X-Crypto-Pay-API-Signature` HMAC-SHA256 verified.
- **No client trust:** Activation only after webhook confirmation.

---

## Railway Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `PROD_BOT_TOKEN` | Yes | Telegram Bot token |
| `PROD_DATABASE_URL` | Yes | PostgreSQL connection string |
| `PROD_CRYPTOBOT_TOKEN` | Yes | Crypto Pay API token (@CryptoBot) |
| `PROD_CRYPTOBOT_WEBHOOK_SECRET` | Yes | Webhook secret for signature verification |
| `PUBLIC_BASE_URL` | Yes | Public HTTPS URL (e.g. `https://api.yourdomain.com`) |
| `PROD_XRAY_API_URL` | Optional | `http://127.0.0.1:8000` if vpn-api on same host |
| `PROD_XRAY_API_KEY` | Optional | API key for vpn-api |
| `PROD_TG_PROVIDER_TOKEN` | Optional | For card payments (YooKassa) |

---

## Cloudflare Domain Setup

1. **A / CNAME record:** `api.yourdomain.com` → Railway service endpoint.
2. **SSL:** Full (strict).
3. **Always Use HTTPS:** ON.
4. **Proxy:** ON (orange cloud) so Cloudflare terminates SSL.

Webhook URL for Crypto Pay:
```
https://api.yourdomain.com/webhook/payment
```
Or:
```
https://api.yourdomain.com/webhooks/cryptobot
```

---

## Crypto Bot Webhook Configuration

1. Open [@CryptoBot](https://t.me/CryptoBot) → Crypto Pay.
2. Create App → get API Token.
3. Set Webhook URL: `https://api.yourdomain.com/webhook/payment`
4. Get Webhook Secret (for `CRYPTOBOT_WEBHOOK_SECRET`).

---

## Security

1. **Signature:** Every webhook validates `X-Crypto-Pay-API-Signature`.
2. **Idempotency:** `status = 'paid'` → return 200, no re-activation.
3. **Amount check:** Expected vs actual ±1 RUB tolerance.
4. **Pending expiry:** Invoices expire after 30 min (configurable in code).

---

## Testing Plan

1. Create test invoice (Buy VPN → Pay crypto).
2. Pay with test crypto.
3. Confirm webhook received (check logs).
4. Check DB: `payments` status = approved, `pending_purchases` status = paid.
5. Confirm VPN key sent to user.
6. Connect via VPN.
7. Second payment → subscription extended (renewal path).

---

## Troubleshooting

| Issue | Check |
|-------|-------|
| Webhook not received | `PUBLIC_BASE_URL`, Cloudflare SSL, Railway port 8080 |
| Invalid signature | `CRYPTOBOT_WEBHOOK_SECRET` matches Crypto Pay App |
| Payment pending | crypto_payment_watcher polls every 30s as fallback |
| VPN key empty | XRAY_API_URL, XRAY_API_KEY, vpn-api health |

---

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | /health | Health check |
| POST | /webhook/payment | Unified payment webhook (Crypto Bot) |
| POST | /webhooks/cryptobot | Crypto Bot webhook (alias) |

---

## Payment Service Layer

`app/services/payments/service.py`:

| Function | Purpose |
|----------|---------|
| `create_invoice(telegram_id, tariff, period_days, amount_rubles, purchase_id)` | Create CryptoBot invoice, returns pay_url |
| `mark_payment_paid(purchase_id, telegram_id, amount_rubles, provider, invoice_id)` | Finalize payment, activate subscription (idempotent) |
| `mark_payment_failed(purchase_id)` | Mark pending purchase as expired |
| `finalize_subscription_payment(...)` | Internal: full subscription finalization |

---

## Optional Env Aliases

For multi-provider support (future):

| Variable | Maps to | Description |
|----------|---------|-------------|
| `PAYMENT_PROVIDER` | — | `cryptobot` (current) |
| `PAYMENT_API_KEY` | `CRYPTOBOT_TOKEN` | Provider API token |
| `PAYMENT_WEBHOOK_SECRET` | `CRYPTOBOT_WEBHOOK_SECRET` | Webhook signature verification |

Current implementation uses `PROD_CRYPTOBOT_TOKEN` and `PROD_CRYPTOBOT_WEBHOOK_SECRET` directly.
