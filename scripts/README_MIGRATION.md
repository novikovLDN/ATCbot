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
| `scripts/verify_samopis_migration.py` | read-only consistency check (DB buckets + optional panel probe) |
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

## Task 2 cut-over (new purchases use Remnawave only)

`config.PURCHASE_FLOW_REMNAWAVE` defaults to **`true`** — the bot is
fully on Remnawave and the legacy samopis xray master is no longer
called from the create / renew / delete paths.  Every new buy /
trial / paid renewal goes through
`app/services/purchase_flow.provision_subscription`, which provisions:

  Premium entity → MainServer squad, `trafficLimitBytes=0`, `expireAt=subscription_end`
  Bypass entity  → Clients squad,    far-future `expireAt`, byte-limited per tariff

Tariff → bypass GB mapping (used for the bypass entity only; premium is
duration-bound):

| Tariff | Bypass cap |
| --- | --- |
| `basic` / `plus` | 10 GB (config.TRAFFIC_LIMITS) |
| `combo_basic` / `combo_plus` | per `COMBO_TARIFFS[tariff][period_days]["gb"]` |
| trial (any source) | `TRIAL_BYPASS_GB` GB (default 1) |

Renewal: PATCH expireAt on premium, ACCUMULATE bypass traffic (never reset).

Un-migrated legacy user buying for the first time after cut-over:
`provision_subscription` finds the samopis `subscriptions.uuid` and uses
it as the forced `vlessUuid` for the new premium entity, so the legacy
VLESS link the user has saved keeps working.

Required env when enabling:

```env
PURCHASE_FLOW_REMNAWAVE=true
REMNAWAVE_API_URL=https://rmnw.atlassecure.ru
REMNAWAVE_API_TOKEN=<token>                          # or REMNAWAVE_TOKEN
REMNAWAVE_MAIN_SQUAD_UUID=<MainServer squad uuid>
REMNAWAVE_SQUAD_UUID=<Clients squad uuid>            # or REMNAWAVE_CLIENTS_SQUAD_UUID
TRIAL_BYPASS_GB=1                                    # optional, default 1
REMNAWAVE_BYPASS_USERNAME_PATTERN={telegram_id}      # keep existing naming
```

Operator runbook for the cutover:
1. Deploy.  `PURCHASE_FLOW_REMNAWAVE` is true by default — samopis
   xray master is bypassed for create / renew / delete.
   `vpn_utils.add_vless_user` / `update_vless_user` /
   `remove_vless_user` become no-ops (return stubs) so any residual
   recovery / admin reissue caller doesn't crash on a decommissioned
   service.
2. Watch `PURCHASE_FLOW_DONE` / `LAZY_PROVISION_*` /
   `VPN_UTILS_*_NOOP` log lines on the next few purchases / trials.
   Every active user should end up with both Remnawave entities.
3. Emergency rollback: set `PURCHASE_FLOW_REMNAWAVE=false` and
   restart.  Legacy samopis path resumes; the same DB rows continue
   to work because their `subscriptions.uuid` is reused as forced
   `vlessUuid` on the Remnawave side and as the samopis xray UUID
   on the legacy side.

## What is NOT in this change (follow-ups)

- Existing un-migrated legacy buyers' first renewal after cut-over works (forced uuid path), but bulk back-fill via the migration script is still recommended to populate `remnawave_premium_uuid` + caches for everyone proactively.
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

- Acquires a PID lock at `$MIGRATION_LOG_DIR/migration.lock` (default `/tmp/migration.lock`, override with `--lock-file`) for `--apply`. A second `--apply` while the first is running aborts immediately. Stale locks from crashed runs are detected by `os.kill(pid, 0)` and cleared automatically.
- Selects every `subscriptions` row where `status='active'`, `uuid IS NOT NULL`, `expires_at > NOW()`, and `subscription_type != 'trial'`. Rows that already have `remnawave_premium_uuid` set are skipped automatically (resumable). `--include-already-migrated` bypasses the skip if you need a full re-run.
- For each candidate, calls `remnawave_premium.create_premium_user_entity`, which:
  1. **Preflight** — calls `remnawave_api.find_user_by_username(tg_{tg_id}_premium)`. This is a single `GET /api/users/by-username/{name}` call on Remnawave v2.7.4; 200 means the username is taken, 404 (errorCode `A063`) means free. If the entity already exists and is ours (`telegramId` match OR description contains "samopis"), the script **adopts** it and returns `recovered=True` without POSTing.
  2. If the username is owned by an unrelated user, the script aborts that row with `error="conflict_unrelated_user"`. Nothing is overwritten without `--force-overwrite` (not yet implemented; would need to be added explicitly).
  3. Otherwise POSTs `/api/users` with the legacy samopis UUID in the panel's **`vlessUuid`** field — that is the identifier embedded in VLESS connection strings on v2.7+, so reusing it keeps legacy subscription links working on the new inbounds. The panel-internal `uuid` is always panel-assigned and is what subsequent management calls use.
  4. If the panel returns **409** (race with a parallel run), the script re-runs the username lookup and adopts the entity if it's ours.
  5. If the panel returns **400/422** (forced `vlessUuid` rejected), retries WITHOUT the forced UUID. The panel-assigned `vlessUuid` is what ends up in the VLESS link; legacy compat will only be preserved when the panel honoured the forced value (`forced_uuid_accepted=True` in the CSV log).
- Persists `(telegram_id → panel uuid, subscriptionUrl, shortUuid)` atomically into `subscriptions.remnawave_premium_uuid` + `remnawave_premium_sub_url` (migration 046) + `remnawave_premium_short_uuid` (migration 047) and stamps `samopis_migrated_at = NOW()`.
- Appends one row per user to `migration_log.csv` (configurable via `--log-file`). The default path is `$MIGRATION_LOG_DIR/migration_log.csv` if the env var is set, else `/tmp/migration_log.csv` — the Docker image runs the bot as non-root against a read-only `/app`, so writing the log into the working directory raises `PermissionError`. Mount a persistent volume and point `MIGRATION_LOG_DIR` at it if you want the log to survive container restarts. The dashboard's “📥 Migration: download log” button reads from the same path and sends the file back as a Telegram document.

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

## Admin dashboard buttons

`/admin` exposes six buttons next to the gift-link controls. All of them
spawn the same script as the shell invocations above, so behaviour is
identical:

| Button | Subprocess args / behaviour |
| --- | --- |
| 📊 Status | DB snapshot: migrated / remaining counts, lock state, CSV size + tail row |
| 🔬 Verify | Read-only consistency check: DB counters, orphan rows, cache coverage, samples — and `GET /api/users/{uuid}` on a sample of migrated entities to verify they still exist in the panel with the right status/squad/description |
| 📥 Download log | DM the cached `migration_log.csv` (auto-attached after every other action) |
| 🔍 Dry Run 50 | `--limit 50` |
| 🔎 Dry Run FULL | (no `--limit`) |
| 🎯 Apply 1 (test) | `--apply --telegram-id <admin-input> --limit 1` (FSM-prompted) |
| 🛠 Apply 10 | `--apply --limit 10` |
| 🛠 Apply 100 | `--apply --limit 100` (~2 min) |
| 🔢 Apply 500 | `--apply --limit 500` (~10 min at observed ~50 rows/min) |
| 🔢 Apply 1000 | `--apply --limit 1000` (~20 min) |
| 🚨 Apply ALL | `--apply` (gated by a two-step "yes I'm sure" confirm; 180 min timeout) |
| 🧹 Clear stale lock | Manual override — unlinks `/tmp/migration.lock` after a confirm dialog |

### Lock-file ownership and PID reuse

Inside Docker the bot itself runs at PID 1 and the migration script
gets a low PID (e.g. 31).  When a run is killed by the subprocess
timeout the lock file stays behind with the dead PID — but Linux
recycles low PIDs aggressively, so a bare `os.kill(pid, 0)` check on
the next attempt may report "alive" even though the original migration
is gone (the slot now holds a uvicorn worker, redis client, etc.).

Both the script (`_pid_is_alive`) and the dashboard's status/clear-lock
helpers therefore verify the live PID's `/proc/{pid}/cmdline` contains
the substring `migrate_samopis_to_remnawave` before refusing to start.
A live but unrelated process is treated as a stale lock and cleared
automatically.  When the auto-detection cannot decide (or the operator
just wants to force-clear), the **🧹 Clear stale lock** button does so
after a confirm dialog that surfaces the current holder's PID and
liveness.

After every run the bot auto-attaches the freshly-written CSV as a
Telegram document — the explicit Download button is the fallback for
pulling the log later, after the run-result message has scrolled away.

### Streaming progress

`_run_script` parses each stderr line for JSON `event` payloads
(`migrated.created`, `migrated.recovered`, `create.failed`, …) and DMs
the operator every **500** processed rows with a snapshot that includes
live DB counts:

```
🔄 --apply (FULL)
━━━━━━━━━━━━━━━━━━━
This run: 500 processed
DB total migrated: 735 / 4035 (18.2%)
Remaining candidates: 3300
```

So a full Apply ALL on ~4k rows produces ~8 progress messages over
~80 min and the operator never has to refresh Status manually.  The
interval is `_PROGRESS_NOTIFY_EVERY` in
`app/handlers/admin/migration.py` — set to 0 to disable.

## Operational runbook

1. Apply schema migration 045 (auto-applied by `database.init_db()` on next bot deploy).
2. Set `REMNAWAVE_MAIN_SQUAD_UUID` (and the rest of the new env vars) for both stage and prod.
3. On stage, run `python -m scripts.migrate_samopis_to_remnawave` (dry-run) and review the candidate count.
4. On stage, run with `--apply --limit 5 --telegram-id <test-account>` and verify the new Remnawave entity opens its subscription URL correctly.
5. On prod, dry-run, then `--apply --limit 50` in batches. Inspect `migration_log.csv` between batches.
6. Flip `SUBSCRIPTION_PROXY_ENABLED=true` once the majority of users are migrated.
7. Schedule decommission of vpnapi master after a grace period during which the proxy still falls back to the legacy URLs.
