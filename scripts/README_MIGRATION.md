# samopis → Remnawave migration (premium tier)

This document covers the foundation pieces shipped on branch
`claude/atlas-remnawave-migration-YsDzY` for migrating Atlas Secure VPN's
premium subscribers off the self-written `vpnapi` master (138.124.90.195)
and onto the existing Remnawave panel as a second user entity (squad
"MainServer", unlimited traffic).

The bypass-tier integration (squad "Clients", limited GB) is unchanged —
this work adds a parallel premium entity per user.

## What was shipped

| File | Purpose |
| --- | --- |
| `migrations/045_add_remnawave_premium_uuid.sql` | adds `subscriptions.remnawave_premium_uuid`, `subscriptions.samopis_migrated_at`, partial index |
| `migrations/046_add_remnawave_premium_sub_url.sql` | adds `subscriptions.remnawave_premium_sub_url` (panel URL cache) |
| `migrations/047_add_remnawave_premium_short_uuid.sql` | adds `subscriptions.remnawave_premium_short_uuid` (panel `shortUuid` cache) |
| `database/traffic.py` | new helpers: `get/set/clear_remnawave_premium_uuid`, `set_remnawave_premium_uuid_and_url` (writes uuid + sub_url + short_uuid atomically), `set_remnawave_premium_sub_url`, `get_subscription_by_premium_uuid`, `get_subscription_by_samopis_uuid`, `list_subscriptions_for_premium_migration` |
| `database/core.py` (`init_db`) | safety `ALTER TABLE IF NOT EXISTS` mirror of migrations 045+046+047 |
| `app/services/remnawave_api.py` | `create_user(..., uuid=, squad_uuid=, description=, telegram_id=, raw_response=)` — the `uuid=` value is sent in the panel's `vlessUuid` body field (v2.7+); `_request_raw`; `find_user_by_username` (single call to `/api/users/by-username/{name}`, confirmed working on v2.7.4) |
| `app/services/remnawave_premium.py` | high-level `create_premium_user_entity` with preflight + 409-recovery, `renew_premium_user`, `disable_premium_user`, `get_premium_subscription_url`, `build_premium_username`, `_is_our_entity` |
| `app/api/subscription_proxy.py` | optional FastAPI router serving `GET /sub/{uuid}` and `GET /api/sub/{token}` with DB-cached `subscriptionUrl` + samopis fallback |
| `scripts/migrate_samopis_to_remnawave.py` | one-shot CLI: dry-run by default, `--apply` to migrate, resumable, rate-limited, PID-locked |
| `tests/services/test_remnawave_premium.py` | unit tests covering preflight + 409 recovery + happy/failure paths |
| `tests/services/test_remnawave_api_find.py` | unit tests for the `find_user_by_username` strategy switcher |
| `tests/test_migrate_samopis_to_remnawave.py` | unit tests for rate limiter, CSV log, validation, per-row processing, PID lock |
| `tests/integration/test_subscription_proxy.py` | route tests for cache hit / cache miss / fallback / 404 |

## What is NOT in this change (follow-ups)

- Bot-side cutover at purchase time (handlers.py / `database.subscriptions.grant_access`) so new premium buyers automatically get a Remnawave-premium entity instead of a samopis xray inbound. The plumbing is ready (`remnawave_premium.create_premium_user_entity`); the call site change is intentionally deferred so this PR stays reviewable.
- DNS / Cloudflare changes for `sub.atlassecure.ru`.
- Decommissioning steps for vpnapi master.
- Filling in `REMNAWAVE_MAIN_SQUAD_UUID` for stage/prod — required for `--apply`.

## Required environment variables

These names mirror the existing `REMNAWAVE_*` pattern (env() prepends `STAGE_`/`PROD_`/`LOCAL_` automatically).

```env
# Already in use (bypass tier)
REMNAWAVE_API_URL=https://panel.atlassecure.ru
REMNAWAVE_API_TOKEN=<panel API token from Settings → API Tokens>
REMNAWAVE_SQUAD_UUID=<Clients squad UUID, bypass tier>

# New (premium tier)
REMNAWAVE_MAIN_SQUAD_UUID=<MainServer squad UUID, premium tier>      # REQUIRED for --apply
REMNAWAVE_PREMIUM_FORCE_UUID=true                                    # default true
REMNAWAVE_PREMIUM_USERNAME_PATTERN=tg_{telegram_id}_premium          # capped to 32 chars
REMNAWAVE_PREMIUM_DEVICE_LIMIT=5

# Optional subscription-URL fallback endpoint
SUBSCRIPTION_PROXY_ENABLED=false                                     # default off
LEGACY_SAMOPIS_SUB_BASE_URL=https://api.mynewllcw.com                # only if proxy enabled
```

See `.env.example` for stage/prod-prefixed templates.

## Running the migration script

```bash
# Dry-run (default) — lists candidates, no writes
python -m scripts.migrate_samopis_to_remnawave

# Apply for real
python -m scripts.migrate_samopis_to_remnawave --apply

# Smaller batches first — recommended on prod
python -m scripts.migrate_samopis_to_remnawave --apply --limit 50

# Single-user smoke test
python -m scripts.migrate_samopis_to_remnawave --apply --telegram-id 210948123

# Throttle the panel (default 5 rps)
python -m scripts.migrate_samopis_to_remnawave --apply --rate 3
```

The script:

- Acquires a PID lock at `<log-file>.lock` (or `--lock-file`) for `--apply`. A second `--apply` while the first is running aborts immediately. Stale locks from crashed runs are detected by `os.kill(pid, 0)` and cleared automatically.
- Selects every `subscriptions` row where `status='active'`, `uuid IS NOT NULL`, `expires_at > NOW()`, and `subscription_type != 'trial'`. Rows that already have `remnawave_premium_uuid` set are skipped automatically (resumable). `--include-already-migrated` bypasses the skip if you need a full re-run.
- For each candidate, calls `remnawave_premium.create_premium_user_entity`, which:
  1. **Preflight** — calls `remnawave_api.find_user_by_username(tg_{tg_id}_premium)`. This is a single `GET /api/users/by-username/{name}` call on Remnawave v2.7.4; 200 means the username is taken, 404 (errorCode `A063`) means free. If the entity already exists and is ours (`telegramId` match OR description contains "samopis"), the script **adopts** it and returns `recovered=True` without POSTing.
  2. If the username is owned by an unrelated user, the script aborts that row with `error="conflict_unrelated_user"`. Nothing is overwritten without `--force-overwrite` (not yet implemented; would need to be added explicitly).
  3. Otherwise POSTs `/api/users` with the legacy samopis UUID in the panel's **`vlessUuid`** field — that is the identifier embedded in VLESS connection strings on v2.7+, so reusing it keeps legacy subscription links working on the new inbounds. The panel-internal `uuid` is always panel-assigned and is what subsequent management calls use.
  4. If the panel returns **409** (race with a parallel run), the script re-runs the username lookup and adopts the entity if it's ours.
  5. If the panel returns **400/422** (forced `vlessUuid` rejected), retries WITHOUT the forced UUID. The panel-assigned `vlessUuid` is what ends up in the VLESS link; legacy compat will only be preserved when the panel honoured the forced value (`forced_uuid_accepted=True` in the CSV log).
- Persists `(telegram_id → panel uuid, subscriptionUrl, shortUuid)` atomically into `subscriptions.remnawave_premium_uuid` + `remnawave_premium_sub_url` (migration 046) + `remnawave_premium_short_uuid` (migration 047) and stamps `samopis_migrated_at = NOW()`.
- Appends one row per user to `migration_log.csv` (configurable via `--log-file`):

```csv
timestamp,telegram_id,uuid_samopis,uuid_remnawave_bypass,uuid_remnawave_premium,forced_uuid_accepted,recovered,status,http_status,subscription_url,error
2026-05-12T13:00:01Z,42,11111111-...,abc12345,11111111-...,True,False,ok,201,https://rmnw.../sub/X,
2026-05-12T13:00:02Z,43,22222222-...,,22222222-...,False,True,recovered,200,https://rmnw.../sub/Y,
2026-05-12T13:00:03Z,44,33333333-...,,unknown-uuid,False,False,failed,409,,conflict_unrelated_user
```

Status values:
* `ok` — entity created on this run.
* `recovered` — entity already existed in the panel (interrupted prior run or race) and was adopted.
* `failed` — see `error`. `conflict_unrelated_user` means the username is taken by someone else; investigate manually before re-running.
* `dry-run` — what the row would have done without `--apply`.

Exit codes: `0` success, `1` config / DB / lock problem (nothing was migrated), `2` one or more rows failed — inspect the CSV.

## Subscription-URL backward compatibility

When `SUBSCRIPTION_PROXY_ENABLED=true`, the bot's FastAPI app exposes:

- `GET /sub/{uuid}` — legacy samopis-style URL
- `GET /api/sub/{token}` — current bot-style URL

Both routes:

1. Look up the UUID in `subscriptions.remnawave_premium_uuid`. If `remnawave_premium_sub_url` is cached, 302 to it directly — **no API call**. The migration script populates the cache at write-time, so every migrated user is a single DB read.
2. On cache miss (legacy rows migrated before column 046 existed): GET `/api/users/{uuid}` once, back-fill the column, then 302.
3. Otherwise look up the UUID in `subscriptions.uuid` (legacy samopis). If found, 302 to `LEGACY_SAMOPIS_SUB_BASE_URL/sub/{uuid}` so existing clients keep working during the grace period.
4. Otherwise return 404.

Point `sub.atlassecure.ru` at the bot host (e.g. Cloudflare CNAME → `atcbot-production-2f93.up.railway.app`) only after `SUBSCRIPTION_PROXY_ENABLED=true` is deployed. The router is mounted conditionally so flipping the env var is enough to enable/disable it.

## Testing

```bash
pip install -r requirements-dev.txt
pytest tests/services/test_remnawave_premium.py \
       tests/test_migrate_samopis_to_remnawave.py \
       tests/integration/test_subscription_proxy.py
```

Unit tests mock both the Remnawave HTTP client and the `database` module, so they don't need network or PostgreSQL access. Integration tests use `fastapi.testclient` against the proxy router.

## Operational runbook

1. Apply schema migration 045 (auto-applied by `database.init_db()` on next bot deploy).
2. Set `REMNAWAVE_MAIN_SQUAD_UUID` (and the rest of the new env vars) for both stage and prod.
3. On stage, run `python -m scripts.migrate_samopis_to_remnawave` (dry-run) and review the candidate count.
4. On stage, run with `--apply --limit 5 --telegram-id <test-account>` and verify the new Remnawave entity opens its subscription URL correctly.
5. On prod, dry-run, then `--apply --limit 50` in batches. Inspect `migration_log.csv` between batches.
6. Flip `SUBSCRIPTION_PROXY_ENABLED=true` once the majority of users are migrated.
7. Schedule decommission of vpnapi master after a grace period during which the proxy still falls back to the legacy URLs.
