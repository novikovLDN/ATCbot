# Production Validation Checklist — mynewllcw.com Domain Migration

After deploying the XRAY / VPN Bot / Domain migration patch:

## 1. Railway Deploy

- [ ] Deploy to Railway
- [ ] Verify build succeeds
- [ ] Check `APP_ENV=prod` is set

## 2. Startup Logs

- [ ] Logs show `INFO: Config loaded for environment: PROD`
- [ ] Logs show `INFO: Using XRAY_API_URL from PROD_XRAY_API_URL`
- [ ] XRAY_API_URL is `https://api.mynewllcw.com` (HTTPS, no private IP)

## 3. Manual API Test

```bash
curl -X POST https://api.mynewllcw.com/add-user \
  -H "Content-Type: application/json" \
  -H "x-api-key: YOUR_API_KEY" \
  -d '{
    "uuid": "11111111-1111-1111-1111-111111111111",
    "telegram_id": 999,
    "expiry_timestamp_ms": 9999999999999
  }'
```

Expected response:
- `uuid` equals request UUID
- `vless_link` / `link` contain `vless://uuid@vpn.mynewllcw.com:443?...` and `#AtlasSecure`
- `flow=xtls-rprx-vision` present (REALITY + XTLS Vision required)

## 4. Bot Activation Test

- [ ] Activate subscription from bot (trial or payment)
- [ ] Confirm VLESS link is received
- [ ] Confirm no fallback generation triggered (check logs for `XRAY_SOURCE_OF_TRUTH`)
- [ ] Confirm no private IP usage
- [ ] Confirm no legacy myvpncloud.net references
- [ ] Confirm `flow=xtls-rprx-vision` in link (REALITY + XTLS Vision)

## 5. Architecture Verification

| Component   | Role                                  |
|------------|----------------------------------------|
| Bot        | Orchestration only — never generates VLESS |
| API        | Cryptographic link authority — single source of truth |
| Xray       | Network authority                      |
