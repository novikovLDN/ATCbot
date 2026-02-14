# Safe Deploy Checklist

## Pre-Deploy

- [ ] Ensure **single replica** (no horizontal scaling) until P0/P1 fixes applied
- [ ] Verify `CRYPTOBOT_WEBHOOK_SECRET` set (or accept polling-only activation)
- [ ] Verify `XRAY_API_URL`, `XRAY_API_KEY` for VPN provisioning
- [ ] Database migrations applied (023_add_payments_paid_at, etc.)
- [ ] vpn-api bound to 127.0.0.1 (secure binding)

## Post-Deploy Validation

- [ ] Health: `curl https://api.<domain>/health` → status ok
- [ ] Bot responds to /start
- [ ] Test payment: create invoice, pay, verify activation within 30s (webhook or watcher)
- [ ] Check logs: no repeated `ACTIVATION_RETRY_ATTEMPT` for same subscription (possible race)
- [ ] Verify no duplicate UUIDs for same user in Xray (manual check if feasible)

## Monitoring

- [ ] Alert on `activation_status='pending'` count > threshold
- [ ] Alert on payment finalization errors
- [ ] Alert on VPN API circuit breaker OPEN
- [ ] Log aggregation for orphan UUID patterns (add_user success, DB update fail)

## Rollback

- [ ] Database: migrations are additive; rollback code deploy first
- [ ] Workers: stopping bot stops all workers; no persistent queue
- [ ] Payments: idempotent; webhook retries safe
