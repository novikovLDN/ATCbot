# Webhook & Polling Audit — Validation Commands

Use these grep commands to validate no Telegram Bot API webhook usage exists.

## Grep Commands for Validation

```bash
# Telegram Bot API webhook (bot.set_webhook / bot.start_webhook)
rg "set_webhook|start_webhook|run_webhook" --type py -l

# Bot/Dispatcher/Polling instantiation (must be exactly ONE each in main.py)
rg "Bot\(|Dispatcher\(|start_polling\(" --type py -n

# Environment variables that might enable webhook
rg "WEBHOOK|webhook" --type py -n
```

## Expected Results

- **set_webhook**: No results in main.py or handlers. Crypto Bot `register_webhook_route` is for payment provider HTTP webhooks, NOT Telegram Bot API.
- **Bot()**: main.py line ~97
- **Dispatcher()**: main.py line ~98
- **start_polling()**: main.py line ~427 (single call inside loop)

## Diagnostic Logs (after deploy)

1. `WEBHOOK_AUDIT_STATE` — webhook.url should be `""`, pending_update_count shown
2. `BOT_TOKEN_HASH` — first 8 chars of sha256, confirms STAGE vs PROD token
3. `POLLING_START` — logged once per polling start
4. `TELEGRAM_CONFLICT_DETECTED` — if conflict occurs, includes webhook_state snapshot
