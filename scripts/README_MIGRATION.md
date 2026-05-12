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
| `migrations/045_add_remnawave_premium_uuid.sql` | adds `subscriptions.remnawave_premium_uuid`, `subscriptions.samopis_migrated_at`, and a partial index |
| `database/traffic.py` | new helpers: `get/set/clear_remnawave_premium_uuid`, `get_subscription_by_premium_uuid`, `get_subscription_by_samopis_uuid`, `list_subscriptions_for_premium_migration` |
| `database/core.py` (`init_db`) | safety `ALTER TABLE IF NOT EXISTS` mirror of migration 045 |
| `app/services/remnawave_api.py` | `create_user(..., uuid=, squad_uuid=, description=, telegram_id=, raw_response=)` extension + `_request_raw` helper |
| `app/services/remnawave_premium.py` | high-level `create_premium_user_entity`, `renew_premium_user`, `disable_premium_user`, `get_premium_subscription_url`, `build_premium_username` |
| `app/api/subscription_proxy.py` | optional FastAPI router serving `GET /sub/{uuid}` and `GET /api/sub/{token}` with samopis fallback |
| `scripts/migrate_samopis_to_remnawave.py` | one-shot CLI: dry-run by default, `--apply` to migrate, resumable, rate-limited |
| `tests/services/test_remnawave_premium.py` | unit tests for the high-level service and the forced-UUID retry path |
| `tests/test_migrate_samopis_to_remnawave.py` | unit tests for the script's rate limiter, CSV log, validation, and per-row processing |
| `tests/integration/test_subscription_proxy.py` | route tests for the fallback endpoint (uses `fastapi.testclient`) |

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

- Selects every `subscriptions` row where `status='active'`, `uuid IS NOT NULL`, `expires_at > NOW()`, and `subscription_type != 'trial'`. Rows that already have `remnawave_premium_uuid` set are skipped automatically (resumable).
- For each candidate, calls `remnawave_premium.create_premium_user_entity`, which:
  1. Tries `POST /api/users` with the legacy samopis UUID in the `uuid` body field.
  2. If the panel rejects with 400/409/422, retries WITHOUT the forced UUID. The panel-assigned UUID is what gets stored.
- Persists the resulting `(telegram_id → panel uuid)` mapping into `subscriptions.remnawave_premium_uuid` and stamps `samopis_migrated_at = NOW()`.
- Appends one row per user to `migration_log.csv` (configurable via `--log-file`):

```csv
timestamp,telegram_id,uuid_samopis,uuid_remnawave_bypass,uuid_remnawave_premium,forced_uuid_accepted,status,http_status,subscription_url,error
2026-05-12T13:00:01Z,42,11111111-...,abc12345,11111111-...,True,ok,201,https://rmnw.../sub/X,
```

Exit codes: `0` success, `1` config / DB problem (nothing was migrated), `2` one or more rows failed — inspect the CSV.

## Subscription-URL backward compatibility

When `SUBSCRIPTION_PROXY_ENABLED=true`, the bot's FastAPI app exposes:

- `GET /sub/{uuid}` — legacy samopis-style URL
- `GET /api/sub/{token}` — current bot-style URL

Both routes:

1. Look up the UUID in `subscriptions.remnawave_premium_uuid`. If found, fetch the panel-issued subscription URL and 302 to it.
2. Otherwise look up the UUID in `subscriptions.uuid` (legacy samopis). If found, 302 to `LEGACY_SAMOPIS_SUB_BASE_URL/sub/{uuid}` so existing clients keep working during the grace period.
3. Otherwise return 404.

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
