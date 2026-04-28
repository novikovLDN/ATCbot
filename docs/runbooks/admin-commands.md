# Admin commands

Hidden commands not registered in `set_my_commands` (intentionally — only the
admin Telegram ID can run them). All checks happen in handler code via
`config.ADMIN_TELEGRAM_ID` or the `@admin_only` decorator in
`app/utils/security.py`.

| Command | Source | Purpose |
|---|---|---|
| `/admin` | `app/handlers/admin/base.py` | Open admin dashboard |
| `/admin_audit` | `app/handlers/admin/audit.py` | Show recent audit log entries |
| `/pending_activations` | `app/handlers/admin/activations.py` | List subscriptions stuck in `activation_status='pending'` |
| `/promo_stats` | `app/handlers/admin/promo_fsm.py` | Promo code usage statistics |
| `/reissue_key` | `app/handlers/admin/reissue.py` | Reissue VPN key for a specific user |
| `/notify_no_subscription` | `app/handlers/admin/notifications.py` | Trigger broadcast to users without an active subscription |
| `/white` | `app/handlers/admin/access.py` | Whitelist user (manual access grant) |

## Notes

- Each command MUST validate `from_user.id == config.ADMIN_TELEGRAM_ID` before
  performing any side effect. The preferred mechanism is `@admin_only` from
  `app/utils/security.py`; legacy handlers still use inline checks (see
  `docs/HANDLERS_REFACTOR_PLAN.md`).
- Unauthorized access attempts are logged via `log_security_warning` with
  `[SECURITY_WARNING]` prefix.
- These commands do NOT appear in the Telegram client menu and are not
  documented for end users on purpose.
