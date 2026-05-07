# ATCbot — Dependency scanning policy & enforcement

**Owner**: project lead.
**Scope**: every Python dependency in `requirements.txt` and
`requirements-dev.txt`, plus first-party code patterns that have caused
incidents before.

## 1. Policy

1. **Every PR is scanned.** A pull request cannot merge with a `CRITICAL`
   advisory open against any dependency it touches.
2. **`main` is scanned daily.** Drift (new advisories on existing locked
   versions) is found within 24 h.
3. **Secrets-in-code scan on every PR.** A leaked secret in a diff is treated
   as a `CRITICAL`.
4. **Custom semgrep rules enforce ATCbot-specific invariants** that have
   regressed before — see §3.
5. **Triage SLA**:
   - `CRITICAL`: patched and deployed within **24 h** of disclosure (or pinned
     workaround documented in this file).
   - `HIGH`: within **7 days**.
   - `MEDIUM`: within **30 days**.
   - `LOW`: backlog, reviewed monthly.
6. **License compliance**: production code (everything outside `tests/` and
   `load_tests/`) must contain only permissive licenses (MIT, BSD-2/3, ISC,
   Apache-2.0, MPL-2.0, PSF). **No GPL / AGPL / LGPL** in production deps.
   `pip-licenses` enforces this in CI.

## 2. Tools

| Tool | Purpose | Where it runs |
|------|---------|---------------|
| `pip-audit` | OSV/PyPA advisory feed for installed packages | CI on PR + nightly |
| `safety` | Snyk-curated CVE feed (cross-checks `pip-audit`) | CI on PR + nightly |
| `bandit` | Static analyser for Python (eval, hardcoded creds, weak crypto, sql f-strings) | CI on PR |
| `semgrep` | Pattern-based static analysis with ATCbot-specific rules (§3) | CI on PR |
| `gitleaks` | Detect committed secrets (BOT_TOKEN-shaped strings, etc.) | CI on PR (full repo) |
| `pip-licenses` | Reject GPL family in production deps | CI on PR |
| GitHub Dependabot | PRs to bump deps with advisories | always-on |

## 3. ATCbot-specific semgrep rules

These rules live in `.semgrep/atcbot.yml` and run on every PR. Each rule
targets a regression we have actually seen.

```yaml
# .semgrep/atcbot.yml
rules:
  - id: atcbot-no-fstring-sql
    patterns:
      - pattern-either:
          - pattern: $CONN.execute(f"...$X...")
          - pattern: $CONN.fetch(f"...$X...")
          - pattern: $CONN.fetchrow(f"...$X...")
          - pattern: $CONN.fetchval(f"...$X...")
    message: >
      f-string in a SQL call. Use $1, $2 placeholders with asyncpg. See
      SECURITY_CODE_AUDIT_2026_03.md — all SQL must be parameterized.
    severity: ERROR
    languages: [python]

  - id: atcbot-no-urllib-without-ssrf-guard
    pattern-either:
      - pattern: urllib.request.urlopen(...)
      - pattern: requests.get($URL, ...)
      - pattern: requests.post($URL, ...)
      - pattern: httpx.get($URL, ...)
      - pattern: httpx.post($URL, ...)
      - pattern: aiohttp.ClientSession.$M($URL, ...)
    message: >
      Outbound HTTP call. Confirm $URL is validated by
      vpn_utils._validate_api_url_security or equivalent SSRF guard.
      Private IPs and non-HTTPS must be rejected in PROD.
    severity: WARNING
    languages: [python]
    paths:
      exclude:
        - tests/
        - load_tests/

  - id: atcbot-admin-handler-must-decorate
    pattern-either:
      - pattern: |
          @router.callback_query(F.data.startswith("admin_"))
          async def $F(...):
              ...
      - pattern: |
          @router.message(Command("admin"))
          async def $F(...):
              ...
    pattern-not: |
      @admin_only
      $DECORATOR
      async def $F(...):
          ...
    message: >
      Admin handler missing @admin_only decorator
      (app/utils/security.py:240). Without it, anyone can call this.
    severity: ERROR
    languages: [python]

  - id: atcbot-no-direct-config-env-bypass
    pattern: os.getenv("BOT_TOKEN", ...)
    message: >
      Direct env access for BOT_TOKEN bypasses the PROD_/STAGE_ prefix
      enforcement in config.py:51-57. Use `from config import BOT_TOKEN`.
    severity: ERROR
    languages: [python]

  - id: atcbot-balance-write-needs-advisory-lock
    pattern-either:
      - pattern: |
          $CONN.execute("UPDATE users SET balance = ...")
      - pattern: |
          $CONN.execute("UPDATE users SET balance = balance - ...")
      - pattern: |
          $CONN.execute("UPDATE users SET balance = balance + ...")
    pattern-not-inside: |
      async def $F(...):
          ...
          await $CONN.execute("SELECT pg_advisory_xact_lock(...)")
          ...
    message: >
      Balance write without preceding pg_advisory_xact_lock. See
      WITHDRAWAL_BALANCE_AUDIT.md — race conditions documented.
    severity: ERROR
    languages: [python]

  - id: atcbot-hmac-must-use-compare-digest
    pattern-either:
      - pattern: $A == $B
        metavariable-pattern:
          metavariable: $A
          patterns:
            - pattern-regex: .*(secret|signature|token|hmac).*
    message: >
      Use hmac.compare_digest for secret comparison (constant time).
    severity: WARNING
    languages: [python]
```

## 4. CI workflow

`.github/workflows/security.yml` — drop-in, ready to commit:

```yaml
name: security
on:
  pull_request:
    branches: [main, develop]
  push:
    branches: [main]
  schedule:
    # Daily 04:00 UTC scan of main
    - cron: "0 4 * * *"

permissions:
  contents: read
  pull-requests: write
  security-events: write

jobs:
  deps:
    name: Dependency CVE scan
    runs-on: ubuntu-22.04
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: pip
      - name: Install scanners
        run: |
          python -m pip install --upgrade pip
          pip install pip-audit safety pip-licenses
      - name: Install project deps (for accurate scan)
        run: pip install -r requirements.txt -r requirements-dev.txt
      - name: pip-audit
        run: pip-audit --strict --format json --output pip-audit.json
        continue-on-error: false
      - name: safety
        run: safety check --full-report --json > safety.json || true
      - name: License compliance (no GPL family in production)
        run: |
          pip install -r requirements.txt
          pip-licenses --format=json --with-license-file > licenses.json
          python - <<'PY'
          import json, sys
          banned = ("GPL", "AGPL", "LGPL", "SSPL")
          rows = json.load(open("licenses.json"))
          bad = [r for r in rows if any(b in (r.get("License") or "") for b in banned)]
          if bad:
              for r in bad:
                  print(f"BANNED LICENSE: {r['Name']}=={r['Version']} -> {r['License']}")
              sys.exit(1)
          PY
      - name: Upload reports
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: dep-scan-reports
          path: |
            pip-audit.json
            safety.json
            licenses.json

  static:
    name: Static analysis
    runs-on: ubuntu-22.04
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - name: Install
        run: pip install bandit semgrep
      - name: bandit
        run: |
          bandit -r app/ database/ -f json -o bandit.json -x tests,load_tests \
                 -ll  # report MEDIUM and above
      - name: semgrep (curated rulesets)
        run: |
          semgrep --config p/python --config p/owasp-top-ten \
                  --config .semgrep/atcbot.yml \
                  --error --json --output semgrep.json \
                  app/ database/ main.py config.py healthcheck.py \
                  platega_service.py cryptobot_service.py lava_service.py
      - name: Upload reports
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: static-reports
          path: |
            bandit.json
            semgrep.json

  secrets:
    name: Secret scan
    runs-on: ubuntu-22.04
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - name: gitleaks
        uses: gitleaks/gitleaks-action@v2
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          GITLEAKS_CONFIG: .gitleaks.toml

  block-on-critical:
    name: Gate merge on CRITICAL findings
    needs: [deps, static, secrets]
    if: github.event_name == 'pull_request'
    runs-on: ubuntu-22.04
    steps:
      - uses: actions/download-artifact@v4
        with: { name: dep-scan-reports, path: . }
      - uses: actions/download-artifact@v4
        with: { name: static-reports, path: . }
      - name: Evaluate severities
        run: |
          python - <<'PY'
          import json, sys
          critical = []
          # pip-audit output: vulnerabilities[].fix_versions, severity not always
          pa = json.load(open("pip-audit.json"))
          for v in pa.get("dependencies", []):
              for vuln in v.get("vulns", []):
                  if "CRITICAL" in str(vuln.get("aliases", "")) or vuln.get("severity") == "CRITICAL":
                      critical.append(f"pip-audit: {v['name']} {vuln['id']}")
          # bandit
          bd = json.load(open("bandit.json"))
          for r in bd.get("results", []):
              if r.get("issue_severity") == "HIGH" and r.get("issue_confidence") == "HIGH":
                  critical.append(f"bandit: {r['filename']}:{r['line_number']} {r['test_id']}")
          # semgrep
          sg = json.load(open("semgrep.json"))
          for r in sg.get("results", []):
              if r.get("extra", {}).get("severity") == "ERROR":
                  critical.append(f"semgrep: {r['path']}:{r['start']['line']} {r['check_id']}")
          if critical:
              print("\n".join(critical))
              sys.exit(1)
          PY
```

## 5. Dependabot config

`.github/dependabot.yml`:

```yaml
version: 2
updates:
  - package-ecosystem: pip
    directory: "/"
    schedule:
      interval: weekly
      day: monday
    open-pull-requests-limit: 10
    labels: [deps, security]
    groups:
      aiogram-stack:
        patterns: ["aiogram", "aiohttp", "magic-filter"]
      asyncpg-redis:
        patterns: ["asyncpg", "redis", "hiredis"]
      fastapi-stack:
        patterns: ["fastapi", "starlette", "uvicorn", "pydantic*"]
    allow:
      - dependency-type: direct
      - dependency-type: indirect

  - package-ecosystem: github-actions
    directory: "/"
    schedule:
      interval: monthly
```

## 6. Triage workflow

When a finding lands:

1. **Pull request created by Dependabot or scanner.**
2. **Auto-labelled** by severity. CI's `block-on-critical` gate prevents
   merge of any unrelated PR if a `CRITICAL` is open against `main`.
3. **Owner**: project lead. ETA for `CRITICAL` is 24 h: either bump and
   deploy, or document a workaround in this file under a `### Known
   exceptions` heading and ack with risk acceptance signed by project owner.
4. **Re-run after fix** is enforced by required check.

## 7. Known exceptions

(none open as of 2026-05-07)

This section is the only place where a CVE may be acknowledged-and-ignored.
Each entry must specify:

- the package and CVE / GHSA id;
- why the bot is not reachable through the vulnerable code path;
- the date by which the dependency will be upgraded anyway.

## 8. Manual checklist for new dependencies

When adding a new line to `requirements.txt`:

- [ ] License is permissive (`pip-licenses --packages <name>`).
- [ ] Latest release is < 90 days old or has a clear maintainer.
- [ ] No known unfixed `HIGH`/`CRITICAL` (search `pypi.org/project/<name>`).
- [ ] Pinned to an exact version, not `>=`.
- [ ] If it issues outbound HTTP, the SSRF semgrep rule above is satisfied.
