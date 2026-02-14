# Payment Validation Report

## Idempotency Key Usage

| Key | Table | Purpose |
|-----|-------|---------|
| purchase_id | pending_purchases | Unique per purchase, used in payload |
| cryptobot_payment_id | payments | Provider invoice ID (optional) |
| telegram_payment_charge_id | payments | Telegram payment charge ID |
| referral_rewards (buyer_id, purchase_id) | referral_rewards | UNIQUE constraint |

**finalize_purchase** idempotency:
- `SELECT * FROM pending_purchases WHERE purchase_id`
- If status != 'pending' → raise ValueError (already processed)
- `UPDATE pending_purchases SET status='paid' WHERE purchase_id AND status='pending'`
- If UPDATE 0 → raise (concurrent or invalid)

## provider_invoice_id Handling

- Set by `update_pending_purchase_invoice_id(purchase_id, invoice_id)` after CryptoBot invoice creation
- Used by crypto_payment_watcher to poll `check_invoice_status(invoice_id)`
- Webhook uses payload → purchase_id; invoice_id passed to finalize_purchase for audit

## pending_purchases TTL

- Created with `expires_at = created_at + 30 minutes`
- `update_pending_purchase_invoice_id` resets `expires_at = now + 30 minutes`
- Webhook: `get_pending_purchase_by_id(..., check_expiry=False)` — accepts payment even after expires_at
- Watcher: `WHERE expires_at > NOW()` — skips expired. Payment after expiry not detected by watcher; webhook can still process.

## finalize_purchase Atomic Safety

- Single transaction
- Steps: validate → UPDATE pending → INSERT payment → grant_access
- Rollback on any failure
- **Issue:** grant_access calls add_vless_user inside same TX. If add_vless_user succeeds and later step fails, rollback leaves orphan UUID in Xray.

## Pending Expiry After Payment

If user pays after 30 min:
- Watcher skips (expires_at <= NOW)
- Webhook processes (check_expiry=False) — correct behavior
