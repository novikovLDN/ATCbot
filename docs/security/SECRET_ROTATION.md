# ATCbot — Secret rotation runbook

**Audience**: on-call engineer / project owner.
**Goal**: rotate any single secret without losing money or paying users; rotate
all secrets within 30 minutes if a compromise is suspected.
**Deployment**: Railway (project `atcbot-production-2f93`), single replica,
PostgreSQL advisory lock for single-instance guard.

All secrets are namespaced by environment: `PROD_*`, `STAGE_*`, `LOCAL_*`. The
loader (`config.py:30-57`) refuses to start if a non-prefixed name is set.

The rotation procedures below assume you have:
- Railway project access (variables tab) for the `prod` and `stage` services.
- Telegram access to the bot owner account.
- DB superuser access (for `DATABASE_URL` rotation).

---

## 0. Generic rotation pattern (zero-downtime)

For any secret that is read at startup only, Railway env var change forces a
restart. We can do better: most secrets are read on the **next handler tick**
because `config.py` is a module-level constants file and is *not* hot-reloaded.
Therefore the only zero-downtime flow available today is:

1. Add the new secret as `PROD_<NAME>_NEXT`.
2. Deploy code that prefers `_NEXT` if present; falls back to current.
3. Switch `PROD_<NAME>` to the new value, remove `_NEXT`.
4. Deploy the original code path.

This is **not implemented yet**. Until it is, every rotation is a Railway
redeploy: ~30-90 s of webhook downtime. Telegram retries undelivered updates;
payment providers retry 5xx. Users see no failure beyond a brief delay.

The advisory lock (`main.py:218-235`) is keyed to the connection, so a redeploy
releases it cleanly when the old container's connection closes. Do not run two
prod containers during a rotation — this is enforced by Railway's "single
deployment" model.

---

## 1. `BOT_TOKEN` (`PROD_BOT_TOKEN`)

- **Used in**: `config.py:70-74`, hashed prefix logged in
  `main.py:102-103`. Only thing that authenticates the bot to Telegram.
- **Blast radius**: Full bot impersonation — attacker can send messages, set
  webhooks elsewhere, exfiltrate updates. Cannot read DB or take payments
  directly (no payment-provider secrets), but can phish admin and users.
- **Rotation**:
  1. Talk to `@BotFather` from the bot owner account → "/mybots" →
     `<bot>` → "API Token" → "Revoke current token". You receive a new token.
  2. In Railway → `prod` service → Variables → set `PROD_BOT_TOKEN=<new>`.
  3. Wait for redeploy. Verify in logs: `BOT_TOKEN_HASH=<8 chars>` (`main.py:103`)
     should be a new hash.
  4. Verify webhook is set: `WEBHOOK_SET_SUCCESS` (`main.py:559`) and
     `WEBHOOK_VERIFIED` (`main.py:584`).
  5. Send `/start` from a test account; expect normal response.
- **Downtime**: ~60 s during redeploy. **Old token is immediately invalid**
  — Telegram delivers any in-flight updates to the new instance only.
- **Validation**: `getMe` returns 200 with same `username`. Check
  Sentry for 401 errors in the first 5 min.
- **Frequency**: 90 days, or **immediately** on suspected leak.

## 2. `DATABASE_URL` (`PROD_DATABASE_URL`)

- **Used in**: asyncpg pool init in `database/`. Held by the advisory-lock
  connection (`main.py:218-235`).
- **Blast radius**: Full read/write of all PII, balances, payments, withdrawal
  requisites. **Highest impact** secret in the system.
- **Rotation**:
  1. In Railway Postgres plugin → Connect → "Reset password" (or via psql:
     `ALTER USER atcbot_app PASSWORD '<new>'` from a superuser session).
  2. Update `PROD_DATABASE_URL` in the Railway service. Trigger redeploy.
  3. The old container will lose its pool; the new container acquires a fresh
     advisory lock (`pg_advisory_lock(987654321)`).
  4. If the old container does not exit cleanly, manually:
     `SELECT pid FROM pg_stat_activity WHERE application_name LIKE 'atcbot%';`
     then `SELECT pg_terminate_backend(<pid>);`. Then verify
     `SELECT pg_advisory_unlock(987654321);` returns true (the new container
     should have already grabbed it).
  5. Verify: `Advisory lock acquired` log line appears once.
- **Downtime**: 60-120 s. Bot enters degraded mode if DB is briefly
  unreachable; safe-startup guard handles this (`main.py:185-211`) and admin
  is notified via `notify_admin_degraded_mode`.
- **Validation**: `HEALTH_CHECK db=ok` log within 10 minutes
  (`healthcheck.py:71-73`). Send `/profile` from test account, expect
  balance to render.
- **Frequency**: 180 days, **immediately** on suspected leak.

## 3. `REDIS_URL` (`PROD_REDIS_URL`)

- **Used in**: FSM storage (`main.py:125-139`) and rate-limit middleware
  (`app/core/rate_limit_middleware.py:52-66`).
- **Blast radius**: Loss of FSM means in-flight withdrawal/topup flows are
  reset; loss of rate-limit state allows transient flood. **No PII** is stored
  in Redis (intentional design). An attacker with read access sees rate-limit
  counters and FSM state JSON, which contains only ephemeral conversation data.
- **Rotation**:
  1. Railway Redis plugin → Reset password (or `CONFIG SET requirepass <new>`
     followed by `CONFIG REWRITE`).
  2. Update `PROD_REDIS_URL`. Redeploy.
  3. Active FSM states are lost — users mid-withdrawal must restart.
     Acceptable; we control the impact window.
- **Downtime**: ~60 s. Bot falls back to `MemoryStorage` if Redis is briefly
  absent (`main.py:140-143`); rate limiter falls back to in-memory
  (`rate_limit_middleware.py:64`).
- **Validation**: `REDIS_CONNECTIVITY=ok` (`main.py:133`) and
  `RATE_LIMIT using Redis backend` log lines.
- **Frequency**: 180 days, immediately on leak.

## 4. `WEBHOOK_SECRET` (`PROD_WEBHOOK_SECRET`)

- **Used in**: `config.py:378-381`, `app/api/telegram_webhook.py:42-50`.
  Sent by Telegram in `X-Telegram-Bot-Api-Secret-Token`. The only thing
  that prevents anyone from POSTing forged updates to our webhook URL.
- **Blast radius**: Forged Telegram updates → bot believes any user said
  anything. Includes forged "successful payment" Telegram-Payments updates;
  the payload `telegram_id` binding (`payments/service.py:165-168`) limits
  this to triggering side-effects keyed to the same attacker user, but it
  also unlocks DoS and admin command injection.
- **Rotation**:
  1. Generate a new secret: `python3 -c "import secrets; print(secrets.token_hex(32))"`.
  2. In Railway, set `PROD_WEBHOOK_SECRET=<new>`. Redeploy.
  3. On boot, `bot.set_webhook(secret_token=...)` (`main.py:553-558`)
     re-registers the webhook with the new secret in the same call.
  4. Telegram immediately starts sending the new header.
- **Downtime**: 5-30 s during redeploy. There is no double-secret window;
  any update Telegram had buffered with the old secret will be re-delivered
  with the new one once the webhook is re-registered.
- **Validation**: `WEBHOOK_VERIFIED` log; absence of `WEBHOOK_SECRET_MISMATCH`
  warnings in the first hour.
- **Frequency**: 90 days, immediately on leak.

## 5. `ADMIN_TELEGRAM_ID` (`PROD_ADMIN_TELEGRAM_ID`)

- **Used in**: `config.py:77-86`, all `is_admin()` checks
  (`app/utils/security.py:215`).
- **Blast radius if "compromised"**: The admin's Telegram account has been
  taken over. Not a "secret" in the traditional sense — disclosure is fine
  (the integer leaks every time the bot logs an admin action) but we may need
  to **lock out** the old admin and authorise a new one.
- **Rotation procedure (admin account compromise)**:
  1. **Out-of-band**: contact Telegram support to recover the original admin
     account if possible. If unrecoverable, the original `ADMIN_TELEGRAM_ID`
     is now hostile.
  2. Connect to Postgres directly (you need `PROD_DATABASE_URL` from a secure
     source, **not** from the running container if you suspect the host is
     compromised):
     ```sql
     -- Disable any pending withdrawals before changing admin
     UPDATE withdrawal_requests SET status = 'frozen' WHERE status = 'pending';
     ```
  3. In Railway, set `PROD_ADMIN_TELEGRAM_ID=<new_id>`. Redeploy.
  4. Verify the new admin can run an admin command and the old cannot.
- **Downtime**: ~60 s.
- **Validation**: log a `/admin` command from new id → should succeed; from
  old id → `Unauthorized admin access blocked by @admin_only`
  (`security.py:259`).
- **Frequency**: only on incident. Not subject to scheduled rotation.

## 6. `XRAY_API_KEY` (`PROD_XRAY_API_KEY`)

- **Used in**: VPN provisioning calls in `vpn_utils.py` and `xray_api/`.
- **Blast radius**: Attacker can list, create, modify, or remove VPN users
  on the Xray server. Can deactivate paying users (DoS). Cannot read user
  traffic.
- **Rotation**:
  1. SSH to the Xray API server (or use its admin UI). Generate a new API
     key. Configure the API server to accept both old and new for 5 minutes
     ("rolling" support is server-side).
  2. Update `PROD_XRAY_API_KEY` in Railway. Redeploy.
  3. Wait until `xray_sync` worker reports a successful tick
     (`[XRAY_SYNC] started successfully` already at boot, then watch for
     successful sync iterations).
  4. Disable the old key on the Xray API server.
- **Downtime**: 0 — Xray provisioning is async; existing VPN users keep
  working because Xray serves traffic from in-memory config independent of
  the API key.
- **Validation**: `add_vless_user` succeeds for a fresh test purchase.
- **Frequency**: 90 days.

## 7. `REMNAWAVE_API_TOKEN` (`PROD_REMNAWAVE_API_TOKEN`)

- **Used in**: `app/services/remnawave_service.py`, traffic monitor worker.
- **Blast radius**: Read/write to Remnawave panel — bypass squad assignment,
  read traffic counters per user. No user PII beyond what the bot already
  has.
- **Rotation**:
  1. In Remnawave admin panel → API tokens → revoke + reissue.
  2. Update `PROD_REMNAWAVE_API_TOKEN` in Railway. Redeploy.
- **Downtime**: 0 — traffic monitor catches up on next tick.
- **Validation**: `REMNAWAVE_ENABLED=true` log; first traffic-monitor tick
  succeeds.
- **Frequency**: 90 days.

## 8. `PLATEGA_MERCHANT_ID` + `PLATEGA_SECRET`

- **Used in**: `platega_service.py:36-37` (outbound) and `:151-159`
  (inbound webhook auth).
- **Blast radius**: With both, attacker can forge SBP webhooks
  (= free subscriptions) and / or impersonate the merchant outbound (limited
  — they would also need merchant account access).
- **Rotation**:
  1. Log in to Platega.io merchant dashboard. Generate a new secret.
  2. Update `PROD_PLATEGA_SECRET` (and `PROD_PLATEGA_MERCHANT_ID` if the
     dashboard rotates that too — usually it does not).
  3. Redeploy.
- **Downtime**: ~60 s. Any Platega webhook delivered during the window will
  fail HMAC and be retried by Platega for ~24 h, so customer impact is limited
  to a delayed activation, not a lost payment.
- **Validation**: a self-test SBP payment of 1 RUB completes end-to-end.
- **Frequency**: 90 days. **Immediately** on signature-mismatch alert.

## 9. `CRYPTOBOT_API_TOKEN`

- **Used in**: `cryptobot_service.py:135-137` (HMAC key derivation:
  SHA-256 of token, then HMAC-SHA-256 of body).
- **Blast radius**: Forged crypto webhooks and outbound CryptoBot API calls.
- **Rotation**:
  1. In `@CryptoBot` → "Crypto Pay" → "API tokens" → revoke + create new.
  2. Update `PROD_CRYPTOBOT_API_TOKEN`. Redeploy.
- **Downtime**: ~60 s.
- **Validation**: a 0.0001 USDT self-test invoice completes; check
  `CryptoBot webhook: signature verification failed` is absent.
- **Frequency**: 90 days.

## 10. `LAVA_JWT_TOKEN` + `LAVA_SIGN_KEY` + `LAVA_SHOP_ID`

- **Used in**: `lava_service.py` (HMAC of webhook with `LAVA_SIGN_KEY`,
  bearer with `LAVA_JWT_TOKEN`).
- **Blast radius**: With both, forge Lava card webhooks → free subscriptions.
  **Note**: `lava_service.py:184` warns and accepts unsigned if `LAVA_SIGN_KEY`
  is empty — this is a risk. Make sure `PROD_LAVA_SIGN_KEY` is always set.
- **Rotation**:
  1. Lava dashboard → Project → Regenerate API key and signing key.
  2. Update `PROD_LAVA_JWT_TOKEN` and `PROD_LAVA_SIGN_KEY`. Redeploy.
- **Downtime**: ~60 s.
- **Validation**: 1 RUB self-test card payment.
- **Frequency**: 90 days.

## 11. `TG_PROVIDER_TOKEN` (Telegram Payments — ЮKassa provider)

- **Used in**: `config.py:300-306`, sent in `bot.send_invoice` calls.
- **Blast radius**: Attacker with this token cannot create invoices on our
  behalf in arbitrary chats — it is bound to the bot. Risk is mostly
  reputational (someone else's bot starts looking like ours to ЮKassa).
- **Rotation**:
  1. `@BotFather` → "Payments" → ЮKassa → "Reissue token".
  2. Update `PROD_TG_PROVIDER_TOKEN`. Redeploy.
- **Downtime**: ~60 s. Any in-flight `send_invoice` with the old token
  fails the next attempt; user can retry.
- **Validation**: open a `/buy` flow, get past pre-checkout.
- **Frequency**: 180 days.

## 12. `SITE_BOT_API_KEY`

- **Used in**: `app/services/site_sync.py` and `app/workers/site_sync_worker.py`.
  Outbound `X-Bot-Api-Key` header.
- **Blast radius**: Attacker can call our website's bot API on our behalf.
  Limited blast — depends on the website's authorisation model.
- **Rotation**: regenerate on the website side; update Railway env; redeploy.
- **Downtime**: 0 — site sync is best-effort, every 5 min.
- **Frequency**: 180 days.

---

## 13. Emergency rotation — "I think we are owned"

Target: 30 minutes from suspicion to all secrets rotated.

**T+0 — Stop the bleeding.**

1. In Railway, **suspend** the prod service. The bot stops responding;
   webhook receives 502; Telegram retries for hours, no data lost.
2. Open a war-room thread in your team chat. Note `T+0` timestamp.

**T+5 — Cut access tokens that can be used outside our control.**

These can be exploited even while the bot is down:

3. `@BotFather` → revoke `BOT_TOKEN`.
4. Platega dashboard → revoke `PLATEGA_SECRET`.
5. `@CryptoBot` → revoke `CRYPTOBOT_API_TOKEN`.
6. Lava dashboard → revoke `LAVA_JWT_TOKEN` and `LAVA_SIGN_KEY`.
7. Xray API server → revoke `XRAY_API_KEY`.
8. Remnawave panel → revoke `REMNAWAVE_API_TOKEN`.

**T+15 — Rotate inbound-only secrets (DB, Redis, webhook).**

9. Postgres: `ALTER USER ... PASSWORD ...` via a separate trusted client.
10. Redis: reset password.
11. Generate fresh `WEBHOOK_SECRET` (`secrets.token_hex(32)`).
12. Generate fresh `SITE_BOT_API_KEY`.

**T+25 — Bring it back.**

13. Update **all** `PROD_*` env vars with the new values in Railway.
14. Resume the prod service.
15. Watch for `BOT_TOKEN_HASH=<new>`, `Advisory lock acquired`,
    `WEBHOOK_VERIFIED`, `HEALTH_CHECK db=ok`, `REDIS_CONNECTIVITY=ok`
    within 2 minutes.

**T+30 — Post-rotation checks.**

16. From a test account, verify `/start`, `/profile`, `/buy` flow up to
    payment, and `/support`.
17. Run `SELECT count(*), max(created_at) FROM payments WHERE created_at >
    now() - interval '24 hours';` — note the baseline; subsequent payments
    should resume in normal cadence.
18. Search Sentry / logs for the last 24 h to characterise the breach.
    Open an incident in `INCIDENT_RESPONSE.md` flow (playbook 1 or 2).
19. Schedule a postmortem within 48 h.

**Do not** roll back the rotation. Even if you discover the suspicion was
wrong, having rotated 12 secrets is cheap.

---

## 14. Frequency summary

| Secret | Cadence | Trigger-only? |
|--------|---------|---------------|
| `BOT_TOKEN` | 90 d | Also on suspected leak |
| `WEBHOOK_SECRET` | 90 d | Also on suspected leak |
| `XRAY_API_KEY` | 90 d | |
| `REMNAWAVE_API_TOKEN` | 90 d | |
| `PLATEGA_SECRET` | 90 d | Also on signature-mismatch spike |
| `CRYPTOBOT_API_TOKEN` | 90 d | |
| `LAVA_JWT_TOKEN`, `LAVA_SIGN_KEY` | 90 d | |
| `DATABASE_URL` | 180 d | |
| `REDIS_URL` | 180 d | |
| `TG_PROVIDER_TOKEN` | 180 d | |
| `SITE_BOT_API_KEY` | 180 d | |
| `ADMIN_TELEGRAM_ID` | — | On admin-account compromise |

A calendar reminder per row is acceptable. Once we adopt the `_NEXT` pattern
(see §0) the cadence can be tightened to 30 d for the inbound HMACs without
increasing operational risk.
