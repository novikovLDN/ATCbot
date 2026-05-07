# ATCbot — STRIDE Threat Model

**Scope**: ATCbot — Telegram VPN subscription bot (aiogram 3, FastAPI webhook,
PostgreSQL via asyncpg, Redis FSM, Xray + Remnawave VPN backends, deployed on
Railway behind Cloudflare).
**Last revised**: 2026-05-07
**Review cadence**: quarterly, plus on every change to webhook auth, payment
finalisation, or admin surface.

This document supersedes `docs/security/threat_model.md` (kept for legacy
reference). It is calibrated to a small commercial Telegram bot — risk is real
but bounded. We do not pretend this is a bank.

---

## 1. System overview & trust boundaries

### 1.1 Actors and assets

| Actor | Trust level | Notes |
|-------|-------------|-------|
| End user (Telegram client) | Untrusted | Identified only by `telegram_id`. No password. Authenticated transitively via Telegram. |
| Admin (Telegram client) | Privileged | **Single hard-coded ID** in `config.ADMIN_TELEGRAM_ID` (`config.py:77-86`). |
| Telegram Bot API | Trusted vendor | Connected outbound; webhook authenticated by `secret_token` HMAC (`config.WEBHOOK_SECRET`). |
| Payment providers (Platega, CryptoBot, Lava, Telegram Payments / Stars) | Trusted vendors | Each has a distinct webhook authn scheme (HMAC, header secret). |
| VPN backends (Xray API, Remnawave) | Trusted vendors | Bot calls them with bearer/API-key headers; bot never holds Xray private keys. |
| PostgreSQL (Railway managed) | Trusted store | Single tenant; `pg_advisory_lock` is the only single-instance guard. |
| Redis (Railway managed) | Trusted cache | FSM, rate limit, deduplication. Loss is degraded, not fatal. |
| Site sync service (`SITE_API_URL`) | Trusted vendor | Bidirectional bot↔website API. |

### 1.2 Entry points (attack surface)

1. **`POST /telegram/webhook`** — `app/api/telegram_webhook.py` — guarded by
   `X-Telegram-Bot-Api-Secret-Token` (`telegram_webhook.py:42-50`),
   1 MB body cap (`:58-67`), 25 s handler timeout (`:84`).
2. **`POST /webhooks/platega`** and **`POST /platega/callback`** —
   header-secret auth (`platega_service.py:151-159`).
3. **`POST /webhooks/cryptobot`** — HMAC-SHA256 of body, key =
   SHA-256(API token) (`cryptobot_service.py:126-137`).
4. **`POST /webhooks/lava`** — HMAC-SHA256 with `LAVA_SIGN_KEY`
   (`lava_service.py:178-192`); falls back to no-check if key missing (warn).
5. **`POST /telegram/webhook` → SuccessfulPayment** — pre-checkout & successful
   payment events from the Telegram Payments / Stars provider, validated via
   payload binding to `telegram_id`
   (`app/services/payments/service.py:128-279`).
6. **Deep-link redirect** — `app/api/deeplink_redirect.py`. Returns a 302 to
   t.me with the bot username; no auth, public, low risk.
7. **`/health`** — FastAPI health (no auth, returns DB readiness only).
8. **Admin commands inside bot** — only enforced via `is_admin()` check
   (`app/utils/security.py:194-215`) and `@admin_only` decorator
   (`:240-278`).

### 1.3 Assets ranked by sensitivity

| Asset | Where it lives | Sensitivity |
|-------|----------------|-------------|
| `BOT_TOKEN` | env, hashed prefix logged | **Critical** — full bot impersonation. |
| Provider webhook secrets (`PLATEGA_SECRET`, `CRYPTOBOT_API_TOKEN`, `LAVA_SIGN_KEY`, `WEBHOOK_SECRET`) | env | **Critical** — forged payments → free service / mass refunds. |
| `DATABASE_URL` | env | **Critical** — full PII + balance read/write. |
| User PII | `users` table (`telegram_id`, optional `username`, `language`) | Medium — Telegram IDs are not strictly secret but are PII under 152-FZ. |
| Withdrawal requisites | `withdrawal_requests.requisites` (free-form text) | High — bank/IBAN/SBP details. |
| Balances | `users.balance` (kopecks) | High — direct monetary value. |
| `XRAY_API_KEY`, `REMNAWAVE_API_TOKEN` | env | High — full read/write on VPN config; can deactivate paying users. |
| VPN keys (`vpn_key`, `vpn_key_plus`) | `subscriptions` rows | Medium — usable for free traffic until rotated. |
| Admin Telegram ID | env | Medium — disclosure enables targeted phishing. |

### 1.4 Trust-boundary diagram

```
                 ┌──────────────────────────────────────────────┐
                 │                  Internet                    │
                 └─────────────┬────────────────┬───────────────┘
                               │                │
              Telegram updates │                │ Provider webhooks
                               │                │ (Platega, CryptoBot,
                               ▼                ▼  Lava)
                    ┌────────────────────────────────────┐
                    │  Cloudflare → Railway edge          │
                    │  (TLS termination, no IP allowlist) │
                    └─────────────┬──────────────────────┘
                                  │
                                  ▼
        ╔═════════════════════════════════════════════════════════╗
        ║   FastAPI app (uvicorn) — single instance, advisory     ║
        ║   lock on PostgreSQL (main.py:218-235)                  ║
        ║                                                         ║
        ║  ┌────────────────────────┐  ┌───────────────────────┐  ║
        ║  │/telegram/webhook       │  │/webhooks/{provider}   │  ║
        ║  │HMAC: WEBHOOK_SECRET    │  │HMAC or header secret  │  ║
        ║  │Body cap 1 MB, 25 s TO  │  │25 s outer timeout     │  ║
        ║  └──────────┬─────────────┘  └──────────┬────────────┘  ║
        ║             │                            │              ║
        ║             ▼                            ▼              ║
        ║  ┌────────────────────────────────────────────────────┐ ║
        ║  │  aiogram Dispatcher (concurrency sem = 20)         │ ║
        ║  │  middlewares: PrivateChat, RateLimit, ErrorBoundary│ ║
        ║  └─────────────────────────┬──────────────────────────┘ ║
        ║                            │                            ║
        ║         ┌──────────────────┼─────────────────────┐      ║
        ║         ▼                  ▼                     ▼      ║
        ║  payments service   subscription service   admin handlers
        ║                            │                            ║
        ╚════════════════════════════╪════════════════════════════╝
                                     │
              ┌──────────────────────┼──────────────────────────┐
              ▼                      ▼                          ▼
      ┌──────────────┐     ┌─────────────────┐         ┌────────────────┐
      │ PostgreSQL   │     │ Redis (FSM, RL) │         │ Xray / Remna   │
      │ asyncpg pool │     │ rediss optional │         │ HTTPS API + key│
      └──────────────┘     └─────────────────┘         └────────────────┘
```

The dashed boundary at the top is the only externally exposed surface. The DB
and Redis are reached over Railway's internal network. Xray / Remnawave are
reached over public HTTPS (Cloudflare Tunnel) using bearer tokens.

---

## 2. STRIDE per component

Severity numbers below are CVSS-equivalent on a 10-point scale, calibrated to a
small commercial bot (loss is bounded by the customer base, ~10⁴ users).

### 2.1 Spoofing

| # | Threat | Vector | Mitigation (file:line) | Residual | Recommended | Sev |
|---|--------|--------|------------------------|----------|-------------|-----|
| S-1 | Forged Telegram update reaches dispatcher | Attacker POSTs to `/telegram/webhook` with arbitrary update body | Constant-time compare of `X-Telegram-Bot-Api-Secret-Token` (`app/api/telegram_webhook.py:42-50`); `WEBHOOK_SECRET` required (`config.py:378-381`) | Negligible if secret stays secret | Periodic rotation runbook (see `SECRET_ROTATION.md`) | 8.1 |
| S-2 | Forged Platega callback grants free subscription | Empty `X-MerchantId`/`X-Secret` once bypassed auth; now patched | Header constant-time compare with non-empty server-side check (`platega_service.py:151-159`); fix recorded in `SECURITY_CODE_AUDIT_2026_03.md` §1 | Low — IP allowlist not in place | Add CF rule to allow only Platega edge IPs to `/webhooks/platega`; reject others at edge | 7.5 |
| S-3 | Forged CryptoBot callback | Attacker sends arbitrary body with stolen / guessed signature | HMAC-SHA256 with key = SHA-256(API token) (`cryptobot_service.py:126-137`); explicit non-empty signature check | Low | Track and alert on `signature verification failed` log spike | 6.8 |
| S-4 | Forged Lava callback when `LAVA_SIGN_KEY` missing | Bot warns and accepts unsigned (`lava_service.py:184`) | None when key absent | **High in dev/stage** | Make signature mandatory in PROD: refuse to start Lava service if `IS_PROD and not LAVA_SIGN_KEY` | 7.0 |
| S-5 | User impersonation by guessing payload | Attacker sends successful-payment with another user's `telegram_id` in payload | Payload's `telegram_id` is checked against authenticated `from_user.id` in `verify_payment_payload` (`app/services/payments/service.py:165-168, 214-217, 240-243, 265-268`) | Low | Add tests for each payload shape; ensure all new shapes enforce binding | 5.0 |
| S-6 | Admin spoofing through forwarded message | Forwarded message from admin appears in admin's private chat | `is_admin()` checks `from_user.id`, never message author or forward (`app/utils/security.py:194-215`) | Negligible | None (well-handled) | 2.0 |

### 2.2 Tampering

| # | Threat | Vector | Mitigation | Residual | Recommended | Sev |
|---|--------|--------|------------|----------|-------------|-----|
| T-1 | Race condition lets balance go negative | Concurrent `decrease_balance()` + `finalize_balance_purchase()` | Advisory lock on withdrawal path only (`WITHDRAWAL_BALANCE_AUDIT.md` Critical #1-3); CHECK constraint `balance_non_negative` | Medium — DB constraint catches but causes tx failure UX | Add `pg_advisory_xact_lock(telegram_id)` to all balance-modifying paths | 7.5 |
| T-2 | Payment amount mismatch | Provider sends a manipulated amount | `validate_payment_amount` with ±1 RUB tolerance raises `PaymentAmountMismatchError` (`app/services/payments/service.py:286-313`) | Low | Tighten tolerance to ±0.50 RUB and alert on mismatch | 5.5 |
| T-3 | Replay of successful_payment payload | Attacker replays old paid update | `check_payment_idempotency` keyed on `purchase_id` + `payments.purchase_id` UNIQUE (`app/services/payments/service.py:320-365`) | Low | None | 4.5 |
| T-4 | Promo-code injection / SQL | Attacker sends crafted promo code | Promo code regex `^[A-Za-z0-9_]+$` (`app/utils/security.py:184`); all SQL is parameterized (asyncpg `$1,$2`) | Negligible | Add `bandit`/`semgrep` rule blocking new f-string SQL — see `DEPENDENCY_SCANNING.md` | 3.0 |
| T-5 | Audit log tampering | Attacker with DB write deletes `audit_log` rows | Logical only; table is writable by app role | High once DB compromised | Append-only role; nightly export to off-platform sink | 6.0 |
| T-6 | Referral self/loop fraud | User creates fake-referral chain to farm rewards | Self- and 1-hop loop detection (`app/services/referrals/service.py:101-161`); `referrer_id` immutable | Medium — multi-hop loops not blocked | Add cycle detection beyond 1 hop; cap rewards per IP/device fingerprint | 5.0 |

### 2.3 Repudiation

| # | Threat | Vector | Mitigation | Residual | Recommended | Sev |
|---|--------|--------|------------|----------|-------------|-----|
| R-1 | User denies authorising withdrawal | User claims they did not press confirm | FSM-gated, two-step confirm (`WITHDRAWAL_BALANCE_AUDIT.md` 3.2); admin alert with full payload (`healthcheck.py:17-30` pattern reused) | Medium — no `correlation_id` on withdrawal flow | Tag every withdrawal with `correlation_id=f"withdraw_{wid}"`, persist | 4.5 |
| R-2 | Admin denies executing mass refund | "Wasn't me" defence | `audit_log` writes by `admin_only` decorator (`app/utils/security.py:240-278`) but log is mutable | Medium | Append-only audit table + 2FA on critical admin actions (see §6) | 5.5 |
| R-3 | Provider denies sending callback | Provider claims callback was never sent | Inbound HTTP request logs include source IP, body hash | Low | Persist raw provider payload for 90 days in `payment_provider_events` | 3.5 |

### 2.4 Information disclosure

| # | Threat | Vector | Mitigation | Residual | Recommended | Sev |
|---|--------|--------|------------|----------|-------------|-----|
| I-1 | PII leaks in logs / Sentry | Stack trace embeds telegram_id, balance | `sanitize_for_logging()` masks token-like keys (`app/utils/security.py:361-396`); no full payload logging required by `main.py:76-78` | Medium — no allowlist, regex-based | Add Sentry `before_send` scrubber; introduce explicit `log_event` schema | 5.0 |
| I-2 | Admin alert exposes user PII | `healthcheck._send_admin_alert` and balance/withdrawal alerts include user data | Alerts intentionally include user IDs; no card data; SBP requisites included in withdrawal alert (`healthcheck.py:17-30`) | Medium — admin chat could be screenshotted | Truncate requisites in alert, store full only in DB; `log_audit_event` on disclosure | 5.5 |
| I-3 | `/health` exposes DB status | Externally fetchable | Returns boolean only; no DSN | Negligible | None | 1.5 |
| I-4 | OpenAPI / docs exposed | FastAPI default `/docs` | `docs_url=None` enforced in PROD per `SECURITY_CODE_AUDIT_2026_03.md` | Negligible | Confirm via CI smoke test | 2.0 |
| I-5 | DB read by attacker (compromised replica creds) | Third party with read-only DSN | Encryption at rest by Railway, TLS in transit; secrets only in env | High blast radius (full PII + balances) | Quarterly review of DSNs; PII column-level encryption for `withdrawal_requests.requisites` | 7.0 |
| I-6 | VPN key leaked through Telegram | User shares vless link | Stored unencrypted in DB; printable on demand | Accepted (it is the product) | Per-user UUID rotation on policy violation; device limit (`config.DEVICE_LIMITS`) | 3.0 |

### 2.5 Denial of service

| # | Threat | Vector | Mitigation | Residual | Recommended | Sev |
|---|--------|--------|------------|----------|-------------|-----|
| D-1 | Webhook flood by user | One Telegram ID floods bot | Per-user 30 req / 60 s, 60→ban 5 min (`app/core/rate_limit_middleware.py:19-26`); Redis sliding window with memory fallback | Low | Tune flood threshold per user tier; emit Sentry on FLOOD_BAN | 4.5 |
| D-2 | Memory exhaustion via tracker maps | Attacker creates 50K+ Telegram IDs | `MAX_TRACKED_USERS=50_000`, eviction in `_cleanup_old` (`rate_limit_middleware.py:27-29, 141-160`) | Low | Cardinality alarm if tracked > 40K | 3.5 |
| D-3 | Body-size DoS | Large POST to webhook | 1 MB cap in `telegram_webhook.py:58-67` and FastAPI middleware | Low | Add same cap to `/webhooks/*`; currently relies on uvicorn defaults | 4.0 |
| D-4 | Slow-payment provider stalls event loop | Provider hangs, ties up worker | 25 s outer timeout per webhook (`payment_webhook.py:26`), 25 s in TG webhook | Low | None | 3.0 |
| D-5 | Concurrent update saturates Postgres | aiogram floods DB pool | `MAX_CONCURRENT_UPDATES=20` (`main.py:155`); pool monitor (`app/core/pool_monitor.py`) | Low | Per-handler concurrency keys for hot paths | 3.5 |
| D-6 | DDoS via `/health` | Load probe hammers DB SELECT | `/health` returns DB readiness without DB call when degraded | Low | Add per-IP rate limit for `/health` at Cloudflare | 2.5 |

### 2.6 Elevation of privilege

| # | Threat | Vector | Mitigation | Residual | Recommended | Sev |
|---|--------|--------|------------|----------|-------------|-----|
| E-1 | Non-admin reaches admin handler | Missing `@admin_only` on new handler | Decorator + `is_admin()` everywhere; `ALLOWED_CALLBACK_PATTERNS` allows `^admin_.*$` but each handler re-checks | Medium — depends on developer discipline | Lint rule (semgrep) requiring `@admin_only` for any handler whose callback matches `^admin_`; CI job in `DEPENDENCY_SCANNING.md` | 7.0 |
| E-2 | Admin compromise = full control | Admin account is single Telegram ID, no MFA | `is_admin()` is `==` check (`app/utils/security.py:215`) | **High** — single point of failure | Multi-admin RBAC table; require Telegram-OTP confirm for mass refund / balance edit > N RUB | 7.5 |
| E-3 | Privilege escalation via callback IDOR | `owns_resource(telegram_id, resource_telegram_id)` not used everywhere | Helper exists (`app/utils/security.py:281-307`) but adoption is partial | Medium | Audit each handler that takes user-supplied IDs and require `require_ownership` | 6.0 |
| E-4 | Container escape via uvicorn | RCE in dependency | Pinned requirements, isolated container | Low | Daily Dependabot, blocked-on-CRITICAL CI | 5.0 |
| E-5 | Admin command injection through forwarded admin message | Telegram admin command piped from a forwarded message | aiogram parses commands from `text`; `from_user` is the forwarder, not the original sender | Negligible | None | 2.0 |

---

## 3. Attack tree — "attacker steals user balance"

Goal: cause `users.balance` to be transferred or spent against the legitimate
owner's will, or have a withdrawal paid to the attacker.

```
G: Steal user balance
├── 3A. Forge a payment finalization
│   ├── 3A1. Bypass /webhooks/* HMAC
│   │   ├── steal provider secret  (covered by S-2/S-3/S-4)
│   │   └── exploit empty-cred bug (FIXED, SECURITY_CODE_AUDIT §1)
│   └── 3A2. Replay paid event   (BLOCKED by purchase_id idempotency)
├── 3B. Race condition on balance
│   ├── 3B1. decrease_balance vs finalize_balance_purchase  (CRITICAL, open)
│   ├── 3B2. admin debit + user withdraw                   (CRITICAL, open)
│   └── 3B3. CHECK constraint catches negative             (mitigates impact)
├── 3C. IDOR / impersonation
│   ├── 3C1. Submit withdraw for another user
│   │   └── BLOCKED — withdraw flow keys on from_user.id, not on supplied id
│   └── 3C2. Replay another user's purchase payload
│       └── BLOCKED — payload telegram_id checked vs from_user.id
├── 3D. Compromise admin
│   ├── 3D1. Steal admin Telegram session  (out of band)
│   ├── 3D2. Phish admin into running /admin debit  (open — single ID, no 2FA)
│   └── 3D3. Use leaked BOT_TOKEN to send admin a malicious deep link (low)
├── 3E. SQL injection
│   └── BLOCKED — all queries parameterized via asyncpg
└── 3F. Compromise database
    ├── 3F1. Leaked DSN / Railway compromise (high impact, low likelihood)
    └── 3F2. PostgreSQL CVE                  (mitigated by managed Railway)
```

| Path | Probability | Impact | Current state |
|------|-------------|--------|---------------|
| 3A1 (forge HMAC) | Low | High | Mitigated by HMAC + non-empty checks |
| 3A2 (replay) | Low | Low | Mitigated by `purchase_id` UNIQUE + idempotency |
| 3B1/3B2 (race) | Medium | High (caps at single account balance) | **Partially open**; CHECK constraint = belt; no advisory lock = no suspenders |
| 3C* (IDOR) | Low | Medium | Mitigated; needs ongoing audit of new handlers |
| 3D2 (phish admin) | Medium | Critical | **Open** — single admin, no 2FA |
| 3F1 (DB leak) | Low | Critical | DSN-only access; rotation runbook needed |

### 3.1 Attack tree — "attacker drains admin"

Goal: cause admin to authorise a payout or balance edit benefiting the attacker.

```
G: Drain admin
├── 4A. Phish admin Telegram session
│   ├── 4A1. Targeted social engineering on admin DM
│   └── 4A2. Malicious deep link sent via bot (bot impersonation)
│       └── requires BOT_TOKEN leak — see SECRET_ROTATION
├── 4B. Compromise admin device
│   ├── 4B1. Stealer malware on admin's phone / desktop
│   └── 4B2. Telegram desktop session hijack
├── 4C. Coerce admin via fake "anomaly"
│   ├── 4C1. Spoof a withdrawal request with confederate user
│   └── 4C2. Time-pressure admin to approve hastily
├── 4D. Compromise CI / deploy pipeline
│   ├── 4D1. Push backdoor that adds attacker as second admin
│   │   └── BLOCKED today (single admin ID env var) but trivially modifiable
│   └── 4D2. Modify is_admin() to true-everywhere
└── 4E. Replay an admin callback
    └── BLOCKED — admin callbacks re-check is_admin() per request
```

| Path | Probability | Impact | Current state |
|------|-------------|--------|---------------|
| 4A1 / 4B* | Medium | Critical | **Open** — depends on admin's personal hygiene |
| 4C* | Low-Medium | Medium | Mitigated only by FSM two-step confirms |
| 4D1 | Low | Critical | **Open** — no required code review or signed deploy |
| 4D2 | Low | Critical | Detectable via semgrep rule on `is_admin` |

---

## 5. Top 15 risks (ranked)

| Rank | ID | Title | Severity | Recommended fix priority |
|------|----|----|---|---|
| 1 | T-1 | Race condition: balance can go negative across `decrease_balance` ↔ `finalize_balance_purchase` | 7.5 | P0 — add advisory lock to all balance writes (1 day) |
| 2 | E-2 | Single admin ID, no MFA on critical actions | 7.5 | P0 — per-action OTP, multi-admin RBAC (1 week) |
| 3 | S-2 | No IP allowlist on `/webhooks/platega` | 7.5 | P1 — Cloudflare rule per provider (1 day) |
| 4 | I-5 | DB compromise blast radius (PII + balances + requisites) | 7.0 | P1 — column-level encryption for requisites; quarterly secret rotation |
| 5 | E-1 | Forgotten `@admin_only` on new admin handler | 7.0 | P1 — semgrep rule + CI block |
| 6 | S-4 | Lava webhook accepts unsigned if `LAVA_SIGN_KEY` empty | 7.0 | P1 — refuse to start in PROD without sign key |
| 7 | S-1 | Telegram webhook secret static, no rotation cadence | 8.1 (latent) | P2 — 90-day rotation runbook (see SECRET_ROTATION) |
| 8 | T-5 | `audit_log` is mutable, attacker with DB write can erase trace | 6.0 | P2 — append-only role; ship to S3 nightly |
| 9 | I-2 | Withdrawal admin alerts include full requisites (PII) | 5.5 | P2 — truncate in alert, full only in DB |
| 10 | E-3 | Partial adoption of `require_ownership` | 6.0 | P2 — audit + lint rule |
| 11 | T-2 | Payment amount tolerance ±1 RUB; could mask small skim | 5.5 | P3 — drop to ±0.50 RUB, alert on every mismatch |
| 12 | T-6 | Multi-hop referral loops not blocked | 5.0 | P3 — cycle detection > 1 hop, IP/device cap |
| 13 | I-1 | PII may leak in unexpected stack traces | 5.0 | P3 — Sentry `before_send` scrubber |
| 14 | D-3 | `/webhooks/*` rely on uvicorn body limits | 4.0 | P3 — explicit 1 MB cap |
| 15 | R-1 | No correlation_id on withdrawal flow | 4.5 | P3 — propagate `withdraw_<wid>` across logs |

---

## 6. Defences not yet implemented (gap list)

The items below are **explicitly missing**. Implementing each closes one or
more risks above.

1. **Multi-admin RBAC.** Today `config.ADMIN_TELEGRAM_ID` is a single integer.
   Replace with `admins(telegram_id, role, created_at)` table, where roles are
   `superadmin`, `support`, `finance`. `is_admin()` becomes a DB lookup with a
   1-second cache. Closes E-2, simplifies on-call rotation.

2. **2FA on critical admin actions.** Define "critical" as: mass refund,
   balance edit > 1 000 RUB, withdrawal approval > 5 000 RUB, broadcast to all
   users. Generate a 6-digit OTP, deliver via separate Telegram channel
   (e.g. via a second bot or SMS), require entry in same FSM flow before commit.
   Closes E-2, R-2, mitigates 4A/4B.

3. **Audit-log immutability.** `audit_log` table is currently writable by the
   app role. Solution: separate `audit_writer` role with `INSERT` only;
   nightly export to S3-compatible bucket with object-lock; alert if row count
   per day drops sharply.

4. **Webhook IP allowlist for payment providers.** Cloudflare WAF rules on
   `/webhooks/platega`, `/webhooks/cryptobot`, `/webhooks/lava` to allow only
   each vendor's published egress ranges. Layered with HMAC, not in place of.

5. **Anomaly detection on payment amounts.** Run a job every 5 minutes that
   flags: amount > p99 of last 30 days for that user, sudden burst of > N
   topups from one user, bursts of failed amount-mismatches. Emit admin alert.

6. **Honeytoken admin command.** Register `/admin_audit_dump` (visible only in
   internal docs, never used in real ops) that does nothing but emit a HIGH
   severity alert if invoked. Detects compromised admin sessions and stale
   forks of the codebase.

7. **Append-only payment-event store.** Table
   `payment_provider_events(id, provider, ip, raw_body_sha256, raw_body, received_at)`
   for forensics. 90-day retention, then anonymise.

8. **Per-handler ownership lint.** A semgrep rule that demands either
   `@admin_only` or `require_ownership(...)` for any handler that takes a
   user-supplied id from `callback.data` or message text.

9. **Balance write lock.** All four balance-modifying functions
   (`increase_balance`, `decrease_balance`, `finalize_balance_purchase`,
   `process_referral_reward`) must take `pg_advisory_xact_lock(telegram_id)`
   first. Closes T-1.

10. **Sentry PII scrubber.** Centralised `before_send` that drops keys
    matching the same allowlist as `sanitize_for_logging`. Closes I-1.

11. **PII column encryption.** `withdrawal_requests.requisites` is free-form
    text and may contain card masks, IBANs, SBP phone numbers. Encrypt with
    `pgcrypto` symmetric key held in `WITHDRAWAL_REQUISITES_KEY`; decrypt only
    in admin handler, not in alerts.

12. **Mandatory Lava signing.** Refuse to start in PROD if `LAVA_SIGN_KEY` is
    empty, instead of warning (`lava_service.py:184`). Closes S-4.

13. **`/webhooks/*` body cap.** Replicate the explicit 1 MB cap from
    `telegram_webhook.py` to each payment webhook handler.

14. **Single-instance lock alarm.** If `pg_advisory_lock` fails in PROD, we
    `sys.exit(1)` (`main.py:229-231`). Add an admin alert before exit so the
    on-call gets paged within seconds, not via Railway.

15. **Cloudflare bot management on `/health`.** Public `/health` is fine for
    Railway, but its presence on the open internet is a free probe surface.
    Restrict to Railway's monitor IPs.

---

## Appendix A — quick reference of authn surfaces

| Endpoint | Authn | Code |
|----------|-------|------|
| `POST /telegram/webhook` | `X-Telegram-Bot-Api-Secret-Token` constant-time compared to `WEBHOOK_SECRET` | `app/api/telegram_webhook.py:42-50` |
| `POST /webhooks/platega`, `POST /platega/callback` | `X-MerchantId` + `X-Secret` header pair, both required non-empty | `platega_service.py:151-159` |
| `POST /webhooks/cryptobot` | `crypto-pay-api-signature` HMAC-SHA256(body, SHA256(API_TOKEN)) | `cryptobot_service.py:126-137` |
| `POST /webhooks/lava` | `Authorization` HMAC-SHA256(body, LAVA_SIGN_KEY); skipped if key missing (warning) | `lava_service.py:178-192` |
| Admin commands | `is_admin(from_user.id) == True` | `app/utils/security.py:194-215` |
| Resource access | `owns_resource(telegram_id, resource.owner)` | `app/utils/security.py:281-307` |

## Appendix B — assumptions & non-goals

- We assume Telegram itself is not compromised. If Telegram is, all bets are off.
- We assume Railway managed Postgres / Redis are not compromised at the platform
  level. Their breach is treated as "DB compromise", procedure in
  `INCIDENT_RESPONSE.md`.
- We do not aspire to PCI-DSS scope. Card data never touches the bot — providers
  hold it.
- The bot is single-tenant; multi-tenancy threats (cross-tenant leaks) are out
  of scope.
