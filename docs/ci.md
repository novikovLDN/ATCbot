# CI/CD

Two workflows in `.github/workflows/` (plus `pr-checks.yml`: PR size label,
commit subject length, diff secrets scan; unchanged).

| Workflow | Trigger | Purpose |
|---|---|---|
| `ci.yml` (**CI**) | push to `main`, `stage`, `refactor/**`; PR to `main`/`stage`; manual | all quality gates |
| `deploy.yml` (**Deploy**) | `workflow_run` of CI, **only** when CI succeeded for a **push** to `main` (production) or `stage` (staging) | Railway deploy hook + health check |

## Current status (2026-09-13, `refactor/audit-2026-09`)

All jobs are expected green. The first run of the new pipeline found real
defects in application code; they are fixed:

| Job | Finding | Fix |
|---|---|---|
| `Lint & Syntax` | `app/handlers/admin/audit_subs.py` F821 `uuid` undefined → `NameError` after a *successful* panel patch in the audit-fix flow | log `panel_ref` |
| `Lint & Syntax` | `app/handlers/payments/callbacks.py` S112 (silent `continue`), `spotify_purchase.py` S105 (false positive: UI text) | warning log; per-file ignore in `pyproject.toml` (shop file is frozen) |
| `Migration Integrity` | on an **empty DB** `013_fix_referrals_columns.sql` failed: `referrals.first_paid_at` was created only by the inline DDL in `database/core.py`, which runs *after* migrations (059 failed for the same reason). Prod was unaffected only because its schema predates migrations | 013 amended: `ADD COLUMN IF NOT EXISTS` first, idempotent `ADD CONSTRAINT`, no inner `BEGIN;/COMMIT;`. The runner has no checksums and 013 is already recorded on prod, so prod never re-runs it |

Verified locally (Postgres 16 via `pgserver`): both boots pass, all 75
versions, 51 tables and 88 indexes (081, 082 included) are present, the schema
is identical after re-boot, and every migration re-executes cleanly.
Docker Build was not run locally (no Docker on the dev machine).

## CI jobs

All jobs run in parallel. **`CI OK`** aggregates them and fails if any job
failed or was cancelled. It is the one check to require in branch protection.

| Job (check name) | What it does | Blocks? |
|---|---|---|
| `Lint & Syntax` | `ruff check .` with the rules from `pyproject.toml` (ruff pinned in the workflow, `RUFF_VERSION`), then `python -m compileall` | yes |
| `Tests` | `pip install -r requirements-dev.txt`, `pip check`, import smoke (`tests/test_import_smoke.py`), full `pytest`. Tests are hermetic (fake asyncpg connections, env stubbed in `tests/conftest.py`), so **no Postgres service** is used. JUnit XML is uploaded as an artifact | yes |
| `Migration Integrity` | `postgres:16` service, empty DB. `scripts/ci/migration_integrity.py` replays the **real prod boot** (`database.init_db()`: connectivity probe → `migrations.py` runner → pool recreate → inline DDL in `database/core.py` → required-table checks). See below | yes |
| `Frontend Build` | Node 20, `npm ci` from the committed `dashboard/package-lock.json`, `tsc --noEmit` for `tsconfig.app.json` **and** `tsconfig.node.json`, `npm run build`, checks `dist/index.html` | yes |
| `Docker Build` | builds the production `Dockerfile` (not pushed, GHA layer cache), then runs the image: imports `main`, `app.api`, `app.api.dashboard`, `migrations`; checks `dashboard/dist/index.html`, the Incy sidecar (`scripts/incy_encode.mjs` + `node_modules/@incy/link-encoder`), `node`, the migration count, and that it runs as non-root. This catches files dropped by `.dockerignore` | yes |
| `Security Scan` | `pip-audit -r requirements.txt`; `npm audit` for the dashboard (prod deps and all deps) and for the root Incy sidecar. `scripts/ci/security_summary.py` writes a table to the job summary and uploads the JSON reports as the `security-reports` artifact | critical only, see policy |

### Migration Integrity in detail

```
--stage empty    DB must be empty → static checks → boot #1 → verify
--stage rerun    boot #2 on the migrated DB (every redeploy does this) → verify
--stage diagnose (CI runs it only when the job failed) → list ALL failing files
```

* **Static checks:** every `migrations/*.sql` matches `NNN_name.sql` (otherwise
  `migrations.py` silently skips the file). The duplicate `006` is allow-listed;
  any other duplicate version fails, because `schema_migrations` is keyed by the
  numeric prefix and on an existing DB only one of the two files would ever run.
  Files with an explicit `BEGIN;`/`COMMIT;` get a warning: the runner already
  wraps each file in a transaction, and an inner `COMMIT` ends that transaction
  early.
* **Boot #1:** `database.init_db()` must return `True` with `DB_READY=True`.
  Then: every file version is recorded in `schema_migrations`, and every
  table and index created by a migration exists (the list is parsed from the
  SQL, minus drops and renames). A schema snapshot (columns, indexes, constraints)
  is saved.
* **Boot #2 (idempotency):** `init_db()` again must succeed, record no new
  versions and leave the schema identical to the snapshot. Then every migration
  file is re-executed inside a rolled-back transaction. Files that cannot be
  re-run show up as a warning; `--strict-reapply` turns that into a failure.
* **Diagnose:** applies every file in runner order on a scratch database
  (`<db>_diag`), continuing past errors, so one run reports every broken file.
  The runner itself stops at the first failure.

The old gate ran `psql -f` per file **without `ON_ERROR_STOP`**. `psql` exits 0
even when the SQL fails, so that gate could not fail on a SQL error. It also
bypassed the runner and the inline DDL.

### Security policy (why it is mostly non-blocking)

* **Blocking:** a `critical` advisory in the **production** npm tree of the
  dashboard or the Incy sidecar (`npm audit --omit=dev`).
* **Reported only** (`::warning::` annotations, job summary, 30-day artifact):
  * pip-audit findings: the PyPI advisory feed has no severity, so there is
    no safe automatic threshold, and a CVE published overnight should not
    block an urgent payment fix;
  * npm high/moderate/low;
  * dev-only npm packages (`sharp`, `vite`, …), which never reach the image
    at runtime.
* `safety check` was removed. Since Safety 3 it needs an account and API key,
  and it was failing silently behind `|| true`.
* A scanner that crashes or leaves no report shows up as a warning in the
  summary. It is never skipped silently.

## Deploy workflow

`CI` (push to main) ─success→ `Deploy / CI Gate` → `Deploy Production`
(environment `production`) → `Post-Deploy Health Check`.

* It never runs for PRs, `refactor/**` or other branches. The gate checks
  `conclusion == success`, `event == push`, and branch `main` or `stage`.
* It deploys the exact commit CI tested (`workflow_run.head_sha`).
* **Missing secrets are explicit.** No `PRODUCTION_DEPLOY_HOOK_URL` /
  `STAGING_DEPLOY_HOOK_URL` gives a `::warning:: Production NOT deployed` and a
  job-summary line. The job stays green because the secret is optional, but
  the run no longer looks like a deploy. Without a health URL secret, the health
  check is skipped with a warning. When a hook did fire, a failing health
  check fails the run.

## What the repository owner must configure in GitHub

1. **Branch protection** for `main` (and `stage`): require the status check
   **`CI OK`**, require branches to be up to date, and block direct pushes.
2. **Secrets** (Settings → Secrets and variables → Actions; or per environment):
   `PRODUCTION_DEPLOY_HOOK_URL`, `STAGING_DEPLOY_HOOK_URL`,
   `PRODUCTION_HEALTH_URL`, `STAGING_HEALTH_URL` (e.g. `https://<host>/health`).
   All are optional. Without them, Deploy only reports that nothing was deployed.
3. **Environments** `production` and `staging` (Settings → Environments):
   optionally add required reviewers to `production` for a manual approval step.
4. **Railway:** if the Railway service auto-deploys from GitHub on push, it
   deploys **regardless of CI**. Either enable Railway's *"Wait for CI"*
   (service → Settings → Source), or turn off auto-deploy and let the deploy
   hook above be the only trigger. Otherwise the hook causes a second deploy
   of the same commit.

## Running every job locally

Python 3.11 venv with `requirements-dev.txt` installed:

```bash
# Lint & Syntax
ruff check .
python -m compileall -q -x '(^|/)(\.venv|venv|node_modules|\.git)(/|$)' .

# Tests (+ import smoke)
pytest -q -o addopts="" tests/test_import_smoke.py
pytest -q -o addopts=""

# Migration Integrity. Needs an EMPTY Postgres 16 database, e.g.
#   docker run --rm -d -p 5432:5432 -e POSTGRES_PASSWORD=p postgres:16
# or, without Docker: pip install pgserver (bundles Postgres 16 binaries).
export DATABASE_URL=postgresql://postgres:p@localhost:5432/postgres
python scripts/ci/migration_integrity.py --stage empty    --snapshot /tmp/snap.json
python scripts/ci/migration_integrity.py --stage rerun    --snapshot /tmp/snap.json
python scripts/ci/migration_integrity.py --stage diagnose
# (the script removes DATABASE_URL from the env before importing config.py,
#  which forbids the bare variable, and sets APP_ENV=local + LOCAL_DATABASE_URL)

# Frontend Build
cd dashboard && npm ci && npx tsc --noEmit -p tsconfig.app.json \
  && npx tsc --noEmit -p tsconfig.node.json && npm run build

# Docker Build
docker build -t atcbot:ci .

# Security Scan
pip install pip-audit && pip-audit -r requirements.txt
(cd dashboard && npm audit --omit=dev)
```

After changing `dashboard/package.json`, regenerate and commit the lockfile
(`npm install` in `dashboard/`). `npm ci` in CI fails on a stale lockfile.
