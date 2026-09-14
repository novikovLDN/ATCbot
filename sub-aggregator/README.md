# sub-aggregator

Fault-tolerant HTTP aggregator that merges two Remnawave subscription URLs
(main + gb) into one stable link. One `https://<SUB_DOMAIN>/<token>` that
clients (Happ, v2rayNG, Streisand, v2Box) can use forever.

Published behind two Russian TCP-passthrough fronts (RF-1 active, RF-2
backup) with DNS active/passive failover. The origin runs the actual
service; the fronts hold no TLS certs and no state.

* **FR-numbers** below refer to sections in the internal spec (see task
  brief §4). Every FR has at least one test in `test/`.
* **All 29 tests are green** — see the “Test output” section.

---

## Architecture at a glance

```
┌──────────────┐                       ┌────────────────────┐
│   client     │─── HTTPS 443 ────────►│  RF-1 / RF-2       │  (stream TCP-passthrough)
│ (Happ, ...)  │                       │  nginx stream {}   │
└──────────────┘                       └─────────┬──────────┘
                                                 │  TCP (WireGuard tunnel)
                                                 ▼
                                       ┌────────────────────┐
                                       │  origin nginx      │  (TLS terminate here)
                                       │  server_name       │
                                       │  <SUB_DOMAIN>      │
                                       └─────────┬──────────┘
                                                 │  proxy_pass 127.0.0.1:8080
                                                 ▼
                          ┌───────────────────────────────────────┐
                          │  sub-aggregator (Node 20 + Fastify)   │
                          │  ├── Redis SWR cache (fresh + stale)  │
                          │  ├── Postgres map: token → 2 sub URLs │
                          │  └── undici pool: 2 parallel GETs     │
                          └───────────────────────────────────────┘
                                                 │
                                                 ▼
                          ┌───────────────────────────────────────┐
                          │  Remnawave panel: /api/sub/main/<u>   │
                          │                   /api/sub/gb/<u>     │
                          └───────────────────────────────────────┘
```

DNS failover script (§Deploy) probes both fronts every minute and flips
the A-record on 3 consecutive RF-1 failures, restores on 5 consecutive OKs.

---

## ENV

| Name                    | Default                                                         | Notes |
|-------------------------|-----------------------------------------------------------------|-------|
| `PORT`                  | `8080`                                                          | HTTP listen |
| `LOG_LEVEL`             | `info`                                                          | pino level |
| `PG_DSN`                | `postgres://aggregator:aggregator@127.0.0.1:5432/aggregator`    | read-only role recommended (see migration) |
| `REDIS_URL`             | `redis://127.0.0.1:6379`                                        | ioredis URL |
| `CACHE_TTL`             | `300`                                                           | fresh copy TTL (s) |
| `STALE_TTL`             | `259200`                                                        | SWR fallback TTL (s) — 3 days |
| `MAP_TTL`               | `3600`                                                          | token → row cache TTL (s) |
| `NEG_MAP_TTL`           | `60`                                                            | negative token cache TTL (s) |
| `UPSTREAM_TIMEOUT_MS`   | `2000`                                                          | per-attempt timeout |
| `UPSTREAM_RETRIES`      | `1`                                                             | attempts = 1 + retries, 100–300 ms jitter |
| `INTERNAL_SECRET`       | `""`                                                            | header `x-internal-secret` on `/internal/*`; required |
| `WEBHOOK_SECRET`        | `""`                                                            | HMAC-SHA256 secret; empty → falls back to `INTERNAL_SECRET` gate |
| `WEBHOOK_SIG_HEADER`    | `x-remnawave-signature`                                         | hex, optional `sha256=` prefix |
| `REVOKED_REMARK`        | `Subscription revoked. Contact support.`                        | shown as vless#remark on revoked stub |
| `METRICS_ALLOWED_CIDR`  | `10.0.0.0/8`                                                    | used by `nginx/origin.conf` only, not by the app |

---

## `sub_pairs` table (contract with the bot)

```sql
-- migrations/001_sub_pairs.sql
CREATE TABLE IF NOT EXISTS sub_pairs (
    token         TEXT PRIMARY KEY,
    main_sub_url  TEXT NOT NULL,
    gb_sub_url    TEXT NOT NULL,
    main_user_uuid UUID NULL,
    gb_user_uuid   UUID NULL,
    status        TEXT NOT NULL DEFAULT 'active',   -- active | revoked
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS sp_main_uuid ON sub_pairs (main_user_uuid);
CREATE INDEX IF NOT EXISTS sp_gb_uuid   ON sub_pairs (gb_user_uuid);
```

**Bot writes; aggregator only reads.** After any change (INSERT / UPDATE
status / rewrite URLs), the bot MUST call:

```
POST https://<SUB_DOMAIN>/internal/invalidate/<token>
Header: x-internal-secret: <INTERNAL_SECRET>
```

This drops the cached body + map so the next client GET hits fresh URLs.
Without invalidation, changes propagate after `CACHE_TTL` (default 5 min).

---

## HTTP endpoints

| Method / path                          | Auth                              | Purpose |
|---|---|---|
| `GET /:token`, `GET /:token/*`          | none — token is the auth          | merged subscription |
| `POST /internal/invalidate/:token`      | `x-internal-secret`               | drop `sub:`, `stale:`, `map:` |
| `POST /internal/webhook`                | HMAC-SHA256 body (or shared secret if `WEBHOOK_SECRET=""`) | async invalidate all tokens matching `data.uuid` |
| `GET /healthz`                          | none                              | `{"ok":true}` always |
| `GET /readyz`                           | none                              | 200 iff Redis PONGs |
| `GET /metrics`                          | none (nginx restricts by IP)      | Prometheus text |

Response headers:

* `content-type: text/plain; charset=utf-8` (body is base64-encoded)
* `subscription-userinfo: upload=..; download=..; total=..; expire=..`
   * `upload/download/total` — from **gb** upstream
   * `expire` — from **main** upstream (falls back to gb)
* `profile-title`, `profile-update-interval`, `profile-web-page-url`,
   `support-url`, `announce`, `routing` — passed through from **main** if
   present. Anything else the upstream sends is dropped.
* `x-cache: hit | stale | miss | stub`

---

## Failure modes

| Situation                                    | Behaviour |
|---|---|
| Both upstreams fail, stale exists            | 200 with stale body, `x-cache: stale`, refresh in background |
| Both upstreams fail, no stale                | 503, `retry-after: 30` |
| One upstream fails, stale exists             | Serve stale (single-source merge would be lossy) |
| One upstream fails, no stale                 | Serve what we got + WARN log |
| Postgres down, map cached                    | Serve from Redis map (may be stale up to `MAP_TTL`) |
| Postgres down, map missing                   | 404 |
| Token unknown                                | 404 (negative-cached for `NEG_MAP_TTL`) |
| Token malformed (`../`, spaces, > 128 chars) | 404 without touching DB |
| `status='revoked'`                           | 200 stub — single vless to 127.0.0.1 with remark |

---

## Deploy

### 1. Origin (bot server)

```bash
# 1.1 apply migration (once)
psql "$PG_DSN" -f migrations/001_sub_pairs.sql

# 1.2 build & run the service
cd sub-aggregator
cp .env.example .env      # fill INTERNAL_SECRET, WEBHOOK_SECRET, PG_DSN
docker compose up -d --build

# 1.3 install nginx origin
sudo cp nginx/origin.conf /etc/nginx/sites-available/sub-aggregator
sudo sed -i \
  -e "s|SUB_DOMAIN|sub.YOUR-DOMAIN.io|g" \
  -e "s|ACME_DIR|/var/www/acme|g" \
  -e "s|TLS_CERT|/etc/letsencrypt/live/sub.YOUR-DOMAIN.io/fullchain.pem|g" \
  -e "s|TLS_KEY|/etc/letsencrypt/live/sub.YOUR-DOMAIN.io/privkey.pem|g" \
  -e "s|WG_CIDR|10.8.0.0/24|g" \
  /etc/nginx/sites-available/sub-aggregator
sudo ln -sf /etc/nginx/sites-available/sub-aggregator /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx

# 1.4 issue TLS
sudo certbot certonly --webroot -w /var/www/acme -d sub.YOUR-DOMAIN.io
```

### 2. Fronts (RF-1 and RF-2 — identical)

```bash
# nginx must be built with stream module (default on Ubuntu/Debian).
sudo cp nginx/front-stream.conf /etc/nginx/streams-enabled/sub-aggregator.conf
sudo sed -i \
  -e "s|ORIGIN_WG_IP|10.8.0.1|g" \
  -e "s|ORIGIN_PORT|443|g" \
  /etc/nginx/streams-enabled/sub-aggregator.conf

# On many distros the streams-enabled dir isn't auto-included — add to nginx.conf:
#   include /etc/nginx/streams-enabled/*.conf;   # in a stream {} block at top level
sudo nginx -t && sudo systemctl reload nginx
```

WireGuard between origin ↔ RF-1 and origin ↔ RF-2 is a prerequisite —
setup depends on your existing VPN topology, see `wg-quick(8)`.

### 3. DNS failover (independent host)

```bash
sudo install -m 0755 scripts/dns-failover.sh /usr/local/sbin/sub-failover
sudo mkdir -p /var/lib/sub-failover /etc/sub-failover

sudo tee /etc/sub-failover/env >/dev/null <<'EOF'
SUB_DOMAIN=sub.YOUR-DOMAIN.io
RF1_IP=RF1.PUBLIC.IP
RF2_IP=RF2.PUBLIC.IP
CF_ZONE_ID=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
CF_RECORD_ID=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
CF_API_TOKEN=CHANGE_ME
EOF

sudo tee /etc/cron.d/sub-failover >/dev/null <<'EOF'
* * * * * root . /etc/sub-failover/env && /usr/local/sbin/sub-failover
EOF
```

State file lives in `/var/lib/sub-failover/state`. Watch it with
`journalctl -t sub-failover -f`.

---

## Quick curl checks

```bash
# health
curl -s https://sub.YOUR-DOMAIN.io/healthz
# → {"ok":true}

# a real subscription (replace TOKEN)
curl -s -i https://sub.YOUR-DOMAIN.io/TOKEN | head -20
# → look for x-cache: miss on first call, hit on repeat

# invalidate after bot updates the row
curl -s -X POST https://sub.YOUR-DOMAIN.io/internal/invalidate/TOKEN \
  -H "x-internal-secret: $INTERNAL_SECRET"
# → {"ok":true}   (nginx blocks this from the outside — call locally or via WG)

# webhook (from the panel host)
BODY='{"event":"user.limited","data":{"uuid":"11111111-1111-1111-1111-111111111111"}}'
SIG=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$WEBHOOK_SECRET" -hex | awk '{print $2}')
curl -s -X POST https://sub.YOUR-DOMAIN.io/internal/webhook \
  -H "content-type: application/json" \
  -H "x-remnawave-signature: sha256=$SIG" \
  --data-raw "$BODY"
# → {"ok":true}   (any matching tokens are invalidated async)
```

---

## Test output

```
$ npm test
> node --test test/unit/*.test.js test/integration/*.test.js

...
# tests 29
# suites 0
# pass 29
# fail 0
```

Breakdown:
* `test/unit/userinfo.test.js`   — 8 tests, header parse/merge (FR-4)
* `test/unit/aggregator.test.js` — 9 tests, body merge / dedupe / header filter (FR-3, FR-4)
* `test/unit/stub.test.js`       — 2 tests, revoked stub (FR-7)
* `test/integration/flow.test.js` — 10 tests, full HTTP flow through Fastify

Integration tests use a **real mock panel on `node:http`** and
**`ioredis-mock`** for the SWR cache — no external services required.
`node --test` is Node 20+ built-in, no jest/vitest.

---

## Layout

```
sub-aggregator/
├── src/
│   ├── server.js         # Fastify entry, buildApp() for tests
│   ├── config.js         # ENV parsing + defaults
│   ├── db.js             # pg pool + setDbImplForTest hook
│   ├── redis.js          # ioredis + setRedisForTest hook
│   ├── logger.js         # pino + tokenTag()
│   ├── metrics.js        # prom-client registry
│   ├── aggregator.js     # body merge (FR-3), header filter (FR-4)
│   ├── userinfo.js       # subscription-userinfo parse + hybrid build
│   ├── upstream.js       # undici GET with timeout + retry
│   ├── singleflight.js   # per-key promise deduplication
│   ├── stub.js           # revoked/limited stub body
│   └── routes/
│       ├── subscription.js  # GET /:token (SWR cache, singleflight)
│       ├── internal.js      # invalidate + webhook + HMAC verify
│       └── health.js        # /healthz /readyz /metrics
├── test/
│   ├── unit/
│   └── integration/
├── migrations/001_sub_pairs.sql
├── nginx/
│   ├── origin.conf         # TLS + rate-limit + /internal deny + /metrics WG-only
│   └── front-stream.conf   # TCP passthrough (no TLS)
├── scripts/dns-failover.sh # active/passive DNS via Cloudflare API
├── Dockerfile              # multi-stage, alpine, non-root, HEALTHCHECK
├── docker-compose.yml      # aggregator + redis
├── .env.example
└── README.md
```
