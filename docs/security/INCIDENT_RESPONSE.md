# ATCbot — Incident response playbooks

**Audience**: on-call engineer + project owner.
**Goal**: stabilise within 1 hour, restore within 24 hours, postmortem within
7 days. The bot is a small commercial service (~10⁴ users) — we calibrate
response accordingly. Do not theatrically over-react to a transient HTTP 500.

This file supersedes `docs/security/incident_response.md` (legacy reference).

## Roles (always 1 person each, can be the same person on a small team)

- **Incident commander (IC)** — owns the timeline and decisions.
- **Comms lead** — drafts the user/admin/regulator message; stays in
  Telegram support DMs.
- **Technical lead** — runs the fix. May not be the IC.

For ATCbot in steady state: the project owner is IC + tech lead; comms goes
through the support Telegram account.

## Severity scale

| Sev | Definition |
|-----|------------|
| **SEV-1** | Money loss in progress, full outage, or active credential abuse. |
| **SEV-2** | A subset of users cannot transact, or PII has been disclosed externally. |
| **SEV-3** | Degraded but recoverable; e.g. one provider down, FSM lost. |

All SEV-1/2 trigger the **emergency rotation** (`SECRET_ROTATION.md` §13)
unless the IC explicitly decides otherwise.

---

## Playbook 1 — Bot token leak (`BOT_TOKEN`)

**Indicators**

- External report (security@telegram.org, SecurityTrails, etc.).
- A scanner notifies that `1234:ABC...` shows up in a public commit/Pastebin.
- `BOT_TOKEN_HASH` (`main.py:103`) shows the same hash in two unrelated logs
  (hard to detect — usually only via external).
- Sentry: `getMe` 401 unauthorised in the last hour despite no deploy.
- Webhook deliveries dropping to 0 with `last_error_message` in
  `getWebhookInfo`.

**T+0 — Containment (≤ 5 min)**

1. IC: open `/incidents/<date>-<slug>.md` (template at end of doc) and tag
   SEV-1.
2. Tech lead: `@BotFather` → revoke `PROD_BOT_TOKEN`. The leaked token is
   instantly dead.
3. Comms lead: draft the admin alert (template below) but do not send yet —
   the bot is the only channel and will be down for ~60 s.

**T+5 — Eradication**

4. Tech lead: paste new token into Railway `PROD_BOT_TOKEN`. Wait for redeploy.
5. Verify: `BOT_TOKEN_HASH` log is the new prefix; `WEBHOOK_VERIFIED` log is
   present; `getMe` returns 200.
6. Confirm webhook URL on Telegram side equals `config.WEBHOOK_URL`; if a
   third party set the webhook elsewhere with the leaked token, this restores
   us. (`bot.set_webhook` at boot does this automatically.)

**T+15 — Recovery**

7. Search the last 7 days of logs for `WEBHOOK_SECRET_MISMATCH` or a sudden
   spike of `update_id` from unfamiliar sources — those would indicate the
   leaked token was being used to set a foreign webhook.
8. If a foreign webhook *was* set, also rotate `WEBHOOK_SECRET` (Playbook 7
   has the procedure).
9. Comms: send the admin alert (`Bot was briefly down for an emergency
   security rotation. No user data was lost.`).

**T+24h**

10. Postmortem doc draft circulated (template at end of file).

**T+7d**

11. Postmortem closed; action items assigned to dates.

**Success criteria**

- Webhook delivery success rate returns to baseline (≥ 99%).
- No SuccessfulPayment on the leaked token side (impossible after revoke,
  but verify by checking `payments` table for unusual entries during the
  window).

**Comms templates**

- Admin alert (sent into our admin chat as soon as the bot is back up):
  > Security: bot token was rotated at $TS due to suspected leak. Bot was
  > down ~60 s. No customer impact expected. Postmortem in 7 days.
- User notification: **none required**. A 60-second outage falls below our
  proactive-comms threshold.
- Regulator: not applicable; no PII disclosed.

---

## Playbook 2 — Database compromise (read or write)

Assume an attacker has at least read access to the production Postgres.
Treat write access as the worst case until proven otherwise.

**Indicators**

- Anomalous `pg_stat_activity` rows from an unknown IP.
- Sentry alerts that `users.balance` constraint is being violated.
- External claim with a sample row from `users` or `withdrawal_requests`.

**T+0 — Containment**

1. IC: SEV-1.
2. Tech lead: in Railway, **rotate the DB password immediately** (see
   `SECRET_ROTATION.md` §2). This kills both the legitimate bot connection
   and any attacker session. Bot enters degraded mode (`main.py:185-211`).
3. While Postgres reconnects, kick any sessions other than ours:
   ```sql
   SELECT pg_terminate_backend(pid)
   FROM pg_stat_activity
   WHERE datname = current_database()
     AND application_name NOT LIKE 'atcbot%'
     AND pid <> pg_backend_pid();
   ```
4. Snapshot the database **now** (Railway → Postgres → Backups → Create).
   This snapshot is your forensic evidence.

**T+15 — Scope assessment**

5. From the snapshot (not the live DB), enumerate what the attacker could
   have read:
   - PII: `users.telegram_id`, `users.username`, `users.language`,
     `users.balance`.
   - Financial: `payments`, `pending_purchases`, `withdrawal_requests`.
   - Secrets in DB: **none should exist**. If any do (e.g. accidentally
     stored API keys), treat as a separate Playbook 1 / 9 trigger.

6. Compute exposure window: `MIN(...)` to `MAX(...)` from `pg_stat_activity`
   logs that contained the attacker. Log this in the incident doc.

**T+1h — Eradication**

7. Run all rotations from `SECRET_ROTATION.md` §13 — assume any secret the
   bot used was visible in the connection's environment.
8. Force a withdrawal freeze:
   ```sql
   UPDATE withdrawal_requests SET status='frozen' WHERE status='pending';
   ```
9. Audit recent admin actions in `audit_log` for the exposure window.
   Anything outside business hours or volume is suspicious.

**T+24h — Disclosure (RU 152-FZ — "Personal Data Law")**

10. Russia's Personal Data Law (FZ-152) requires Roskomnadzor to be notified
    within **24 hours** of an unauthorised access, and a description of
    measures within **72 hours**. Telegram IDs + usernames + payment data
    are personal data under this law. The comms lead drafts:
    - User notification (Telegram broadcast) listing what was exposed and
      what we did.
    - Roskomnadzor notification — the user's lawyer or DPO uses the
      template at <https://pd.rkn.gov.ru/>.
11. Decide on bonus / refund — the bot owner's call. Document the decision
    in the incident file.

**T+7d**

12. Postmortem.

**Success criteria**

- New DB password issued; only `atcbot%` connections in `pg_stat_activity`.
- All withdrawal requests pending pre-incident reviewed manually.
- Notification to Roskomnadzor sent within 24 h (if PII was actually read).

**Comms templates**

- User: in RU + EN. State plainly: "On $DATE between $T1 and $T2, an
  unauthorised party may have viewed your Telegram ID and balance. No
  payment card numbers are stored by us. We have rotated all credentials.
  No action is required from you. Contact $SUPPORT if you have questions."
- Regulator: per Roskomnadzor template (legal review required).

---

## Playbook 3 — Payment webhook spoofing detected

**Indicators**

- `Platega webhook: invalid signature` log spike.
- A `payments` row exists with `provider='platega'` but no matching record
  in Platega dashboard.
- A user reports getting a subscription without paying.

**T+0**

1. IC: SEV-1 if money is moving; SEV-2 if it is contained to one user.
2. Tech lead: pause processing of the affected provider. Set the env var
   `PROD_PLATEGA_SECRET=` to empty value? **No — that bypasses the auth
   path**. Instead, add a Cloudflare WAF rule blocking
   `POST /webhooks/platega` and `POST /platega/callback` while we
   investigate. Outbound payouts from balance are unaffected.
3. Snapshot DB.

**T+15 — Audit window**

4. Identify the audit window: from the first suspicious webhook to "now".
   ```sql
   SELECT id, telegram_id, amount, purchase_id, created_at
   FROM payments
   WHERE provider='platega'
     AND created_at > $START
   ORDER BY created_at DESC;
   ```
5. Cross-check each row against the Platega merchant dashboard. Anything
   not in the dashboard is fraudulent.
6. For each fraudulent row:
   - Mark `payments.status='disputed'`.
   - Reverse the resulting `subscriptions` row (set `status='revoked'`).
   - Remove the user's VPN UUID via Xray API to stop free traffic.

**T+1h — Eradication**

7. Rotate `PROD_PLATEGA_SECRET` (and `PROD_PLATEGA_MERCHANT_ID` if Platega
   re-issues both) — `SECRET_ROTATION.md` §8.
8. Add the Cloudflare IP-allowlist rule for the provider's published egress
   ranges (item 4 in THREAT_MODEL §6 gap list).
9. Lift the WAF block.

**T+24h — Customer notification**

10. Affected legitimate users (those whose subscriptions were revoked
    because the same `purchase_id` was forged) receive an apology + a manual
    re-activation: comms lead handles 1:1.

**Success criteria**

- No new `signature verification failed` logs after rotation.
- All disputed `payments` rows reconciled.

---

## Playbook 4 — Mass account takeover signal

**Indicators**

- Spike of withdrawal requests from new requisites (different phone / IBAN
  per request) within a short window.
- Sentry: spike in `Unauthorized admin access blocked` from many
  `telegram_id`s — but this should be normal noise; alert when > 3σ.
- Multiple users in support claiming they did not request a withdrawal.

**T+0 — Emergency freeze**

1. IC: SEV-1.
2. Tech lead: freeze withdrawals system-wide:
   ```sql
   UPDATE withdrawal_requests
   SET status='frozen', frozen_at=NOW(), frozen_reason='mass_ato_freeze'
   WHERE status='pending';
   ```
   (`frozen_at`/`frozen_reason` columns may not yet exist; if not, add
   `status='frozen'` only and note the reason in incident doc.)
3. Disable the `withdraw_start` callback by toggling a feature flag (add
   one if it does not exist — see `app/core/feature_flags.py`).
4. Send the emergency admin alert.

**T+15 — Investigation**

5. From the audit log, list affected users and the IPs/devices that started
   the FSM:
   ```sql
   SELECT user_id, count(*) c, min(created_at), max(created_at)
   FROM audit_log
   WHERE event='withdraw_started'
     AND created_at > NOW() - INTERVAL '24 hours'
   GROUP BY user_id
   HAVING count(*) > 1
   ORDER BY c DESC;
   ```
6. Pattern-check the requisites field: same phone, same name, IBAN re-use
   across users → strong ATO signal.

**T+1h — Eradication**

7. Reverse fraudulent withdrawals in DB. **Do not** automate — each one is
   a money decision that the owner must approve.
8. Force-logout: aiogram has no session concept on the user side, so the
   countermeasure is bot-level: set affected `users.is_locked=true` and
   reject their commands until they reach support.

**T+24h**

9. User-facing notification: "Withdrawals are temporarily disabled while we
   investigate suspicious activity. Active subscriptions and bot service
   are unaffected." Issue refunds/credits as the owner decides.
10. Re-enable withdrawals after the freeze, with a 24-hour cooldown for any
    user whose `users.created_at` is < 30 d.

**Success criteria**

- No new fraudulent withdrawal in 48 h.
- All frozen requests reviewed; legitimate ones re-released.

---

## Playbook 5 — Admin account compromise

**Indicators**

- Admin reports they no longer have access to their Telegram session.
- An admin command sequence is performed at unusual hours.
- A new admin Telegram ID appears in `audit_log` "admin promotion" rows
  (we have no such table today, but if we did).

**T+0 — Lock out**

1. IC: SEV-1.
2. Tech lead: from a separate trusted machine (not the admin's), connect
   to Postgres directly using the DSN from your password manager. Do not
   use the bot's runtime container.
3. Set a "panic" admin id in env that only you control. In Railway, set
   `PROD_ADMIN_TELEGRAM_ID=<your_known_id>`. Redeploy. The compromised id
   is now ineffective because `is_admin()` is `==` (`security.py:215`).
4. Freeze withdrawals (Playbook 4 step 2).
5. Disable the FSM storage password if Redis FSM may carry stale admin
   sessions:
   ```
   redis-cli FLUSHDB     # if FSM data only in this DB
   ```
   This drops every in-flight FSM, including the attacker's admin flow.

**T+15**

6. Review `audit_log` and `payments` for unauthorised actions during the
   exposure window.
7. The original admin recovers their Telegram account (Telegram support).

**T+1h — Recovery**

8. After the original admin is back, set `PROD_ADMIN_TELEGRAM_ID` back to
   the canonical value. Lift the panic id.
9. Reverse any fraudulent admin actions.

**T+24h**

10. The admin enables Telegram 2FA cloud password if not already on, and
    rotates their Telegram session list (Settings → Devices).
11. As a follow-up, implement gap §1 (Multi-admin RBAC) and §2 (2FA on
    critical actions) in `THREAT_MODEL.md`. This was a very expensive
    incident; do not let it happen twice with the same architecture.

**Recovery via direct DB**

If the bot itself is somehow making the compromised id functional (e.g. if
ADMIN_TELEGRAM_ID is cached in DB rather than env in a future refactor):

```sql
-- Sanity: confirm canonical admin id matches env
SELECT current_setting('app.admin_telegram_id', true);
-- If a DB-backed admin table exists:
DELETE FROM admins WHERE telegram_id = <compromised_id>;
INSERT INTO admins (telegram_id, role) VALUES (<your_id>, 'superadmin');
```

---

## Playbook 6 — VPN backend (Xray / Remnawave) compromise

**Indicators**

- Xray API server reports unauthorised key changes.
- Customer support: VPN users see foreign devices on their key.
- Remnawave panel: traffic going to an unknown squad.

**T+0**

1. IC: SEV-2 (does not threaten money directly, threatens service).
2. Tech lead: rotate `PROD_XRAY_API_KEY` (`SECRET_ROTATION.md` §6) and
   `PROD_REMNAWAVE_API_TOKEN` (§7).
3. From the Xray side, force-rotate every user UUID and re-emit their VPN
   key. The bot's API contract is "API is source of truth"
   (`payments/service.py:514-520`); the bot will then sync.

**T+1h — Data leak assessment**

4. **What can leak?** The Xray/Remnawave API knows a user's UUID, their
   subscription type, and their traffic counters. **It does not see plaintext
   user traffic** if Reality is properly configured — the keys for that are
   in Xray itself, never in the API. Telegram identities are not in the VPN
   backend at all.
5. **User-facing message**: "Due to a security incident on our VPN
   infrastructure, we have rotated your access key. Please re-import the
   profile in your VPN client. No traffic content was exposed."
6. Send the message to all currently active subscribers via a broadcast.

**T+24h**

7. Postmortem includes review of how the VPN backend was compromised
   (separate from bot codebase).

**Success criteria**

- All active subscribers re-issued; complaint count returns to baseline
  within 48 h.

---

## Playbook 7 — DDoS via webhook flood

**Indicators**

- `WEBHOOK_BODY_TOO_LARGE` (`telegram_webhook.py:62`) repeated frequently.
- `WEBHOOK_SECRET_MISMATCH` (`telegram_webhook.py:46`) at unusual rate.
- Railway egress / CPU charts spiking.
- Concurrency limiter (`MAX_CONCURRENT_UPDATES=20`, `main.py:155`) saturated.

**Current defences (already in place)**

- `MAX_BODY_SIZE = 1 MB` enforced in `telegram_webhook.py:58-67`.
- Rate limit middleware: 30/60s, flood-ban at 60/60s for 5 min
  (`rate_limit_middleware.py:19-26`).
- Bounded tracker maps (`MAX_TRACKED_USERS=50_000`,
  `MAX_BANNED_USERS=10_000`).
- Concurrency semaphore (`main.py:155`).

**T+0 — Containment**

1. IC: SEV-2 if the bot is degraded, SEV-3 if it is just noisy.
2. Tech lead: enable Cloudflare's "Under Attack" mode for the `*.railway.app`
   route, or for the custom domain. This adds a JS challenge — Telegram and
   payment providers are server-side and **will fail it**, so this is only
   useful for traffic to non-webhook paths. For webhooks specifically:
   - In Cloudflare, restrict `/webhooks/*` to the provider's published IP
     ranges via a firewall rule (this should already exist per
     THREAT_MODEL §6 gap 4).
   - Restrict `/telegram/webhook` to Telegram's published Bot API CIDRs
     (149.154.160.0/20, 91.108.4.0/22).

**T+15 — Mitigation**

3. If the flood is from a single Telegram ID slipping through, manually ban
   them via Redis: `SET rl:ban:<id> 1 EX 86400`.
4. If the flood is unauthenticated (mostly 403 in our logs), Cloudflare WAF
   handles it; we are mostly fine — the metric to watch is uvicorn CPU.

**T+1h**

5. Tune thresholds if the legitimate traffic profile has shifted. Edit
   constants in `rate_limit_middleware.py`. Deploy.
6. Consider scaling Railway replicas — currently single instance because
   of the advisory lock; horizontal scaling requires architecture change
   (queue-based workers, leader election).

**Success criteria**

- 4xx rate at edge < 5% sustained.
- p95 webhook latency < 1 s.

---

## Playbook 8 — Insider threat (developer with prod access)

**Indicators**

- A developer leaves the team / is terminated.
- An admin action attributed to a developer's machine occurs after their
  off-boarding.
- Out-of-band tip-off.

**Pre-condition: who has prod access today?**

- Railway project members (env var read/write, deploy).
- GitHub repo write access.
- Cloudflare account access.
- Optionally: `@BotFather` if multiple people are in the chat.
- Optionally: payment provider dashboards.

This list **is the actual access matrix**. Maintain it in
`docs/security/access_matrix.md` (not yet created).

**T+0 — Containment**

1. IC: project owner.
2. Revoke GitHub access: remove from org / repo collaborators.
3. Revoke Railway access: project settings → members → remove.
4. Rotate every secret as if compromise (`SECRET_ROTATION.md` §13). The
   former developer plausibly had local copies of `.env`.
5. If they had `@BotFather` chat access (i.e., ownership of the Telegram
   account that owns the bot), this is a major incident — escalate to
   Telegram support to transfer bot ownership.

**T+1h — Audit**

6. Review `audit_log` for the period from the last day of trust to now.
   Anything anomalous gets reversed.
7. Review `git log` for commits in the last 90 days from this developer.
   Look for suspicious additions: new admin id added, new endpoint without
   auth, new outbound HTTP call to an unfamiliar host, hard-coded backdoor.
   Two extra reviewers.

**T+24h**

8. Update access matrix.
9. Document the gap that allowed insider risk (e.g., no required code review
   on `main`) and close it.

**T+7d**

10. Postmortem includes a "least-privilege gaps" section.

**Least-privilege gaps to fix proactively**

- All deploys today are direct pushes to `main`. **Action**: require PR + 1
  review for `main`; CI gating per `DEPENDENCY_SCANNING.md`.
- Single Postgres role; no `audit_writer` separation. **Action**: split.
- Secrets are visible in plaintext in Railway. **Action**: investigate
  Railway's secret-managed feature or move to Doppler/Infisical.

---

## Postmortem template

For every SEV-1 and SEV-2, write a postmortem in `docs/postmortem/<date>-<slug>.md`.
Blameless. Aim for honesty over polish.

```markdown
# Postmortem: <slug>

- **Date**: YYYY-MM-DD
- **Severity**: SEV-X
- **IC**: <name>
- **Duration**: T+0 (HH:MM UTC) → T+resolved (HH:MM UTC)
- **Customer impact**: <e.g. ~12 users could not buy a subscription for 18 minutes>
- **Money impact**: <RUB amount>

## Summary
2-3 sentences. What happened. What broke. How we fixed it.

## Timeline
- HH:MM — first signal (and from where: Sentry / report / log)
- HH:MM — IC declared
- HH:MM — containment action X taken
- HH:MM — eradication
- HH:MM — verification
- HH:MM — declared resolved

## Root cause (5 whys)
1. Why did <symptom> happen? — <answer>
2. Why did <answer 1> happen? — <answer>
3. Why did <answer 2> happen? — <answer>
4. Why did <answer 3> happen? — <answer>
5. Why did <answer 4> happen? — <root cause>

## Contributing factors
- <factor 1, e.g. monitoring gap>
- <factor 2, e.g. ambiguous runbook>
- <factor 3, e.g. no automated test for X>

## What went well
- <e.g. body cap caught the worst of the DDoS>
- <e.g. advisory lock prevented split brain>

## What did not go well
- <e.g. alert was missed because admin was asleep>
- <e.g. rotation took 30 min, target was 15>

## Action items
| # | Action | Owner | Due | Severity |
|---|--------|-------|-----|----------|
| 1 | <e.g. Add semgrep rule for new f-string SQL> | <name> | <date> | High |
| 2 | <e.g. Implement multi-admin RBAC> | <name> | <date> | High |
| 3 | <e.g. Add IP allowlist on /webhooks/*> | <name> | <date> | Medium |

## Detection
How would we have caught this earlier? Concrete monitoring/alert change.

## Prevention
What change makes this class of incident structurally impossible (or
much harder)?
```

---

## Quick links

- Threat model: [`THREAT_MODEL.md`](./THREAT_MODEL.md)
- Secret rotation: [`SECRET_ROTATION.md`](./SECRET_ROTATION.md)
- Dependency scanning: [`DEPENDENCY_SCANNING.md`](./DEPENDENCY_SCANNING.md)
- Prior audits: [`SECURITY_CODE_AUDIT_2026_03.md`](../../SECURITY_CODE_AUDIT_2026_03.md), [`WITHDRAWAL_BALANCE_AUDIT.md`](../../WITHDRAWAL_BALANCE_AUDIT.md)
