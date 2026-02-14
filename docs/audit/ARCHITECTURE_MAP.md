# Architecture Map — ATCS VPN Telegram Bot

## System Overview

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           TELEGRAM BOT (Railway)                                  │
│  main.py → Dispatcher → Handlers → Services                                       │
│  Background: reminders, trial_notifications, fast_expiry_cleanup,                 │
│              auto_renewal, activation_worker, crypto_payment_watcher, xray_sync   │
└─────────────────────────────────────────────────────────────────────────────────┘
         │                    │                    │                    │
         ▼                    ▼                    ▼                    ▼
┌──────────────┐    ┌──────────────┐    ┌──────────────┐    ┌──────────────────┐
│  PostgreSQL  │    │  VPN API     │    │  CryptoBot   │    │  Telegram API    │
│  (subscriptions,│    │  (vpn-api)   │    │  (invoices,  │    │  (Bot, messages) │
│  payments,   │    │  localhost   │    │  webhook)    │    │                  │
│  pending_    │    │  :8000       │    │              │    │                  │
│  purchases)  │    └──────┬───────┘    └──────────────┘    └──────────────────┘
└──────────────┘           │
                           ▼
                  ┌──────────────┐
                  │  Xray Core   │
                  │  (VLESS/     │
                  │  REALITY)    │
                  └──────────────┘
```

## Component Responsibilities

| Component | Responsibility | Entry Points |
|-----------|----------------|--------------|
| **database.py** | Schema, subscriptions, payments, grant_access, finalize_purchase | All services, workers |
| **vpn_utils.py** | add_vless_user, remove_vless_user, ensure_user_in_xray, reissue_vpn_access | database.grant_access, activation_service, vpn_service |
| **cryptobot_service.py** | create_invoice, handle_webhook (CryptoBot) | payments_callbacks, health_server |
| **payments/cryptobot.py** | create_invoice, check_invoice_status (polling) | crypto_payment_watcher, payments_callbacks |
| **activation_worker** | Process pending subscriptions → add Xray user | main.py background task |
| **fast_expiry_cleanup** | Expire subscriptions, remove UUID from Xray | main.py background task |
| **auto_renewal** | Balance-based auto-renew (grant_access renewal path) | main.py background task |
| **crypto_payment_watcher** | Poll CryptoBot, finalize_purchase on paid | main.py background task |

## UUID Generation Points

| Location | Function | When |
|----------|----------|------|
| database.py:70 | `_generate_subscription_uuid()` | Single canonical source |
| database.py:3686 | grant_access (reissue path) | Admin reissue |
| database.py:4262 | grant_access (new issuance) | New subscription |
| app/services/activation/service.py:399 | attempt_activation | Pending activation |
| vpn_utils.py:829 | reissue_vpn_access | VPN key reissue (old removed, new created) |
| database.py:6175, 6218 | create_pending_purchase | purchase_id (not subscription UUID) |

## Xray API Call Sites

| Location | Operation | Context |
|----------|-----------|---------|
| database.py:2928 | remove_vless_user | check_and_disable_expired_subscription |
| database.py:3653 | remove_vless_user | admin reissue (old UUID) |
| database.py:3700, 3709 | add_vless_user | admin reissue (new UUID) |
| database.py:4237–4240 | remove_vless_user | grant_access (old UUID before new) |
| database.py:4306, 4314 | add_vless_user | grant_access (new issuance) |
| database.py:8008 | remove_vless_user | admin revoke |
| vpn_utils.py:482 | add_vless_user | reissue_vpn_access |
| vpn_utils.py:574 | remove_vless_user | remove_vless_user |
| vpn_utils.py:819 | remove_vless_user | reissue_vpn_access (old) |
| vpn_utils.py:831 | add_vless_user | reissue_vpn_access (new) |
| app/services/activation/service.py:401 | add_vless_user | attempt_activation |
| app/services/vpn/service.py:145 | remove_vless_user | remove_uuid (via vpn_service) |
| fast_expiry_cleanup.py | vpn_service.remove_uuid_if_needed | Expiration |
| trial_notifications.py:460 | remove_vless_user | Trial expiry |
| xray_sync.py:92 | add_vless_user | Sync reconciliation |

## Subscription Table Update Sites

| Location | Operation | Condition |
|----------|-----------|-----------|
| database.py:2976 | UPDATE status='expired', uuid=NULL | check_and_disable |
| database.py:3258, 3263 | UPDATE uuid, vpn_key | Activation flow |
| database.py:3746 | UPDATE uuid, vpn_key | Admin reissue |
| database.py:3988 | UPDATE expires_at, uuid | grant_access renewal |
| database.py:4148, 4455 | INSERT subscriptions | grant_access new issuance |
| database.py:424 | activation_service | UPDATE uuid, activation_status |
| database.py:8016 | UPDATE expires_at, status, uuid=NULL | Admin revoke |
| fast_expiry_cleanup.py:394 | UPDATE status, uuid=NULL | Expiration |
| trial_notifications.py:471 | UPDATE status | Trial expiry |
