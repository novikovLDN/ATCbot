# Test Payment Without CryptoBot Webhook Secret

**Context:** Validate end-to-end payment flow when `CRYPTOBOT_WEBHOOK_SECRET` is not configured.

**Behavior:** Without webhook secret, CryptoBot webhook is rejected. **crypto_payment_watcher** polls every 30s and activates payment via CryptoBot API (token-only). Expect activation within **5–30 seconds** after payment.

---

## Step 1 — Verify Health Endpoint

```bash
curl https://api.mynewllcw.com/health
```

Expected:

```json
{"status": "ok", "db_ready": true, "timestamp": "..."}
```

If not OK: stop and fix infrastructure.

---

## Step 2 — Verify Bot Is Running

1. Open Telegram, find @atlassecure_bot.
2. Send: `/start`
3. Bot must respond.

---

## Step 3 — Create Test Invoice

In bot:

1. Click **Buy 30 days** (or minimal test plan).
2. Bot sends payment button.
3. Tap **Pay** → CryptoBot payment page opens.

---

## Step 4 — Pay From CryptoBot Balance

In CryptoBot payment screen:

1. Choose **Pay from balance**.
2. Confirm payment.
3. Wait **5–30 seconds** (crypto_payment_watcher interval).

---

## Step 5 — Verify Activation

Expected in Telegram (within ~30s):

- "Subscription activated"
- VPN config link (`vless://...`)

If not received within 60s → proceed to Step 6.

---

## Step 6 — Check Railway Logs

Railway → ATCbot → Deployments → Logs

Look for:

- `PAYMENT_CHECK_ATTEMPT` / `Crypto payment auto-confirmed`
- `finalize_purchase: START` / `grant_access`
- No duplicate activations

Without webhook secret: expect `Crypto Bot webhook: invalid signature` or `disabled` — this is expected. Activation comes from the watcher.

---

## Step 7 — Verify VPN User on Server

```bash
journalctl -u vpn-api -n 50 --no-pager
```

Expected: new UUID added, user activated.

---

## Step 8 — Test VPN Connection

1. Copy vless link from bot.
2. Import into VPN client.
3. Connect.
4. Open https://2ip.io — IP should match server location.

---

## Expected Result

- Payment detected by crypto_payment_watcher.
- Subscription activated via `finalize_purchase` → `grant_access`.
- VPN link issued.
- User connects successfully.
- No duplicate activations (idempotency).

---

## Troubleshooting

| Issue | Check |
|-------|-------|
| No activation after 60s | `CRYPTOBOT_TOKEN` set? `provider_invoice_id` saved in `pending_purchases`? |
| Watcher not running | Ensure crypto_payment_watcher task is started (main.py). |
| Webhook returns 200 "unauthorized" | Expected when `CRYPTOBOT_WEBHOOK_SECRET` not set. Watcher handles activation. |
| Production recommendation | Configure `CRYPTOBOT_WEBHOOK_SECRET` for immediate webhook activation. |

---

## Critical Rules

- Do not modify production database manually.
- Do not restart Xray during test.
- Use minimal payment amount.
- Idempotency logic is respected (finalize_purchase checks status).
- Webhook logic is not bypassed — activation via polling fallback.
