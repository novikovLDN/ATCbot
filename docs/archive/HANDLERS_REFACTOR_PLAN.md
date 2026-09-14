# Production-Grade Handlers Refactor Plan

**Status:** Plan ready. Execution requires phased implementation.

**Current state:** handlers.py ~11,690 lines, 150+ handlers, single Router.

**Target:** Modular structure under `app/handlers/` with domain-specific modules.

---

## 1. New Directory Structure

```
app/handlers/
    __init__.py           # Root router aggregation
    common/
        __init__.py
        states.py         # All FSM StatesGroups (DONE)
        guards.py         # ensure_db_ready_message, ensure_db_ready_callback
        decorators.py     # handler_exception_boundary
        utils.py          # safe_resolve_username, safe_edit_text, _markups_equal, etc.
        keyboards.py      # All get_*_keyboard functions
    user/
        __init__.py
        start.py          # /start, referral middleware
        profile.py        # /profile, callback_profile, show_profile
        subscription.py   # /buy entry, menu_buy_vpn, tariff/period selection
        referrals.py      # /referral, menu_referral, share/copy/stats
        support.py        # /info, /help, /instruction, menu_support
    admin/
        __init__.py
        admin_base.py     # /admin, admin:dashboard, admin:main
        broadcast.py      # broadcast create, A/B, segment
        reissue.py        # keys:reissue_all, admin:user_reissue, bulk reissue
        stats.py          # admin:stats, admin:referral_stats, admin:analytics
        export.py         # admin:export, CSV export
    payments/
        __init__.py
        purchase.py       # pay:balance, pay:card, pre_checkout, successful_payment
        withdraw.py       # withdraw_* callbacks
        crypto.py         # pay:crypto, topup_crypto, crypto_pay:*
    callbacks/
        __init__.py
        navigation.py     # menu_main, back_to_main, go_profile
        language.py       # change_language, lang_*
        subscription_callbacks.py  # toggle_auto_renew, menu_profile, menu_vip_access
```

---

## 2. Handler-to-Module Mapping

| Handler / Pattern | Target Module |
|-------------------|---------------|
| cmd_start | user/start.py |
| cmd_profile | user/profile.py |
| cmd_buy | user/subscription.py |
| cmd_referral | user/referrals.py |
| cmd_info, cmd_help, cmd_instruction | user/support.py |
| menu_main, back_to_main, go_profile | callbacks/navigation.py |
| change_language, lang_* | callbacks/language.py |
| toggle_auto_renew, menu_profile, menu_vip_access | callbacks/subscription_callbacks.py |
| cmd_admin, admin:dashboard, admin:main | admin/admin_base.py |
| broadcast:* | admin/broadcast.py |
| admin:keys, admin:user_reissue, reissue | admin/reissue.py |
| admin:stats, admin:referral_stats, admin:analytics | admin/stats.py |
| admin:export | admin/export.py |
| pay:balance, pay:card, pre_checkout, successful_payment | payments/purchase.py |
| withdraw_* | payments/withdraw.py |
| pay:crypto, topup_crypto, crypto_pay:* | payments/crypto.py |
| approve_payment | admin/admin_base or payments (admin action) |

---

## 3. Import Dependency Order (Avoid Circular Imports)

1. **common/** — No imports from other handler modules
2. **callbacks/** — Imports from common only
3. **user/** — Imports from common, callbacks
4. **payments/** — Imports from common, callbacks, user (for shared helpers)
5. **admin/** — Imports from common, callbacks, payments (approve_payment)

---

## 4. Execution Phases

### Phase 1: Common Module
- [ ] guards.py
- [ ] decorators.py  
- [ ] utils.py
- [ ] keyboards.py (largest, ~500 lines)

### Phase 2: Callbacks
- [ ] navigation.py
- [ ] language.py
- [ ] subscription_callbacks.py

### Phase 3: User
- [ ] start.py
- [ ] profile.py
- [ ] subscription.py
- [ ] referrals.py
- [ ] support.py

### Phase 4: Payments
- [ ] purchase.py
- [ ] withdraw.py
- [ ] crypto.py

### Phase 5: Admin
- [ ] admin_base.py
- [ ] broadcast.py
- [ ] reissue.py
- [ ] stats.py
- [ ] export.py

### Phase 6: Integration
- [ ] app/handlers/__init__.py (root router)
- [ ] main.py: `from app.handlers import router`
- [ ] Delete handlers.py
- [ ] Run validation checklist

---

## 5. Validation Checklist

1. /start works
2. FSM transitions work
3. Admin panel works
4. Broadcast works
5. CSV export works
6. Purchase flow works
7. Withdraw flow works
8. No ImportError
9. No circular dependency
10. Bot starts successfully

---

## 6. Risk Mitigation

- **Circular imports:** Enforce import order; move shared helpers to common.
- **Missing handlers:** Use grep `@router\.` before/after to confirm 1:1 migration.
- **FSM state references:** All states in common/states.py; domains import from there.
- **Keyboards:** Centralized in common/keyboards.py; domains import.
