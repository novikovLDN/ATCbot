# Subscription Flow Diagram

## Payment → Subscription → Activation

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│ 1. PAYMENT CREATION                                                              │
└─────────────────────────────────────────────────────────────────────────────────┘
   User: Buy 30 days
        │
        ▼
   payments_callbacks (pay:crypto)
        │
        ├─► subscription_service.create_subscription_purchase()
        │       └─► database.create_pending_purchase()  [purchase_id, status=pending]
        │
        ├─► cryptobot.create_invoice()  [payments/cryptobot or cryptobot_service]
        │
        └─► database.update_pending_purchase_invoice_id(purchase_id, invoice_id)

┌─────────────────────────────────────────────────────────────────────────────────┐
│ 2. PAYMENT CONFIRMATION (two paths)                                              │
└─────────────────────────────────────────────────────────────────────────────────┘
   PATH A: Webhook (cryptobot_service.handle_webhook)
        │  POST /webhook/payment
        │  Verify X-Crypto-Pay-API-Signature
        │  Parse invoice_paid, payload → purchase_id
        │
        └─► database.finalize_purchase(purchase_id, provider, amount, invoice_id)
                └─► [see 3]

   PATH B: Polling (crypto_payment_watcher)
        │  Every 30s: SELECT pending_purchases WHERE provider_invoice_id IS NOT NULL
        │  cryptobot.check_invoice_status(invoice_id)
        │  If status='paid':
        │
        └─► database.finalize_purchase(purchase_id, provider, amount, invoice_id)
                └─► [see 3]

┌─────────────────────────────────────────────────────────────────────────────────┐
│ 3. FINALIZE PURCHASE (single transaction)                                        │
└─────────────────────────────────────────────────────────────────────────────────┘
   database.finalize_purchase() [conn.transaction()]
        │
        ├─► SELECT pending_purchases WHERE purchase_id
        ├─► CHECK status='pending' (idempotency)
        ├─► CHECK amount ±1 RUB
        ├─► UPDATE pending_purchases SET status='paid'
        ├─► INSERT payments (telegram_id, tariff, amount, purchase_id, cryptobot_payment_id)
        │
        └─► grant_access(telegram_id, duration, source='payment', conn=conn)
                └─► [see 4]

┌─────────────────────────────────────────────────────────────────────────────────┐
│ 4. GRANT ACCESS (subscription creation / renewal)                                │
└─────────────────────────────────────────────────────────────────────────────────┘
   grant_access(telegram_id, duration, source, conn)
        │
        ├─► SELECT subscription WHERE telegram_id
        │
        ├─► IF active (status=active, expires_at>now, uuid NOT NULL):
        │       RENEWAL PATH:
        │       ├─► NO add_vless_user (UUID preserved)
        │       ├─► UPDATE subscriptions SET expires_at = old_expires + duration
        │       └─► RETURN {action: "renewal", uuid: existing}
        │
        └─► ELSE (new or expired):
                NEW ISSUANCE PATH:
                ├─► IF VPN_API disabled: INSERT subscription (activation_status='pending')
                │       └─► activation_worker will call add_vless_user later
                │
                └─► ELSE:
                        ├─► new_uuid = _generate_subscription_uuid()
                        ├─► add_vless_user(telegram_id, subscription_end, uuid=new_uuid)  ← EXTERNAL, INSIDE TX
                        ├─► INSERT/UPDATE subscriptions (uuid, vpn_key, expires_at)
                        └─► RETURN {action: "new_issuance", uuid, vless_url}

┌─────────────────────────────────────────────────────────────────────────────────┐
│ 5. ACTIVATION WORKER (pending only)                                              │
└─────────────────────────────────────────────────────────────────────────────────┘
   activation_worker.process_pending_activations()
        │
        ├─► get_pending_subscriptions()  [NO FOR UPDATE]
        │
        └─► FOR EACH pending:
                activation_service.attempt_activation(subscription_id, telegram_id, conn)
                        ├─► new_uuid = _generate_subscription_uuid()
                        ├─► add_vless_user(telegram_id, expires_at, uuid=new_uuid)  ← EXTERNAL, NO TX
                        └─► UPDATE subscriptions SET uuid, vpn_key, activation_status='active'
                                WHERE id=? AND activation_status='pending'

┌─────────────────────────────────────────────────────────────────────────────────┐
│ 6. EXPIRATION                                                                   │
└─────────────────────────────────────────────────────────────────────────────────┘
   A. check_and_disable_expired_subscription(telegram_id)  [on get_subscription]
        │  SELECT * FROM subscriptions WHERE telegram_id, expires_at<=now, status='active'
        │  [INSIDE conn.transaction()]
        ├─► remove_vless_user(uuid)  ← EXTERNAL INSIDE TX
        └─► UPDATE subscriptions SET status='expired', uuid=NULL

   B. fast_expiry_cleanup_task()
        │  SELECT subscriptions WHERE expires_at<=now, status='active', uuid NOT NULL
        │  [NO FOR UPDATE; processing_uuids in-memory set]
        ├─► vpn_service.remove_uuid_if_needed(uuid)  ← EXTERNAL, OUTSIDE TX
        └─► UPDATE subscriptions SET status='expired', uuid=NULL
                WHERE telegram_id AND uuid AND status='active'
```
