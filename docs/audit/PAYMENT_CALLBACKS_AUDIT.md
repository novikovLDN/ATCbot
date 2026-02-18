# Payment callbacks dependency audit

**Date:** 2025-02-15  
**Scope:** Two payment callback modules and their registration/imports. No refactor — dependency report only.

---

## 1. Files under audit

| File | Lines | Router name | Exported as |
|------|--------|-------------|-------------|
| `app/handlers/callbacks/payments_callbacks.py` | 1150 | `payments_router` | `payments_router` |
| `app/handlers/payments/callbacks.py` | 953 | `payments_callbacks_router` | `payments_callbacks_router` |

---

## 2. Which router is included in `app/handlers/__init__.py`

**Included routers (from `app/handlers/__init__.py`):**

- `callbacks_router` ← from `.callbacks` (`app/handlers/callbacks/`)
- `user_router` ← from `.user`
- `payments_router` ← from `.payments` (`app/handlers/payments/`)
- `admin_router` ← from `.admin`
- `game_router` ← from `.game`

So the **root** only references the two packages `callbacks` and `payments` by name. It does **not** reference either payment callback file directly.

---

## 3. Are both payment callback routers registered? (double-handling risk)

**Yes. Both are registered.**

Registration chain:

1. **`app/handlers/callbacks/payments_callbacks.py`**
   - Exports: `payments_router`
   - Used in: `app/handlers/callbacks/__init__.py` → `from .payments_callbacks import payments_router` and `router.include_router(payments_router)`
   - So it is part of `callbacks_router`, which is included in the root in `app/handlers/__init__.py`.

2. **`app/handlers/payments/callbacks.py`**
   - Exports: `payments_callbacks_router`
   - Used in: `app/handlers/payments/__init__.py` → `from .callbacks import payments_callbacks_router` and `router.include_router(payments_callbacks_router)`
   - So it is part of `payments_router` (the one from `app/handlers/payments/`), which is included in the root in `app/handlers/__init__.py`.

**Conclusion:** Both modules are active. There is no duplicate handling of the **same** `callback_data` (see callback_data sets below); the risk is architectural (two parallel payment-callback modules) rather than double-handling of one button.

---

## 4. Callback_data coverage (no overlap)

**`app/handlers/callbacks/payments_callbacks.py`** handles:

- `topup_balance`, `topup_amount:*`, `topup_custom`
- `withdraw_start`, `withdraw_confirm_amount`, `withdraw_final_confirm`, `withdraw_cancel`, `withdraw_back_to_*`, `withdraw_approve:*`, `withdraw_reject:*`
- `pay:balance`, `pay:card`, `pay:crypto`
- `topup_crypto:*`, `topup_card:*`, `pay_tariff_card:*`, `crypto_pay:tariff:*`, `pay_crypto_asset:*`, `crypto_pay:balance:*`

**`app/handlers/payments/callbacks.py`** handles:

- `menu_buy_vpn`, `tariff:*`, `period:*`
- `enter_promo`, `crypto_disabled`, `promo_back`
- `payment_test`, `payment_sbp`, `payment_paid`
- `approve_payment:*`, `reject_payment:*`
- `corporate_access_request`, `corporate_access_confirm`

No shared `callback_data` → no double-handling of the same callback.

---

## 5. All places that import from each file

### Imports **from** `app/handlers/callbacks/payments_callbacks.py`

| Importer | What is imported |
|----------|-------------------|
| `app/handlers/callbacks/__init__.py` | `payments_router` (and includes it in the callbacks router) |

No other files import from `payments_callbacks.py` (no direct imports of handler functions or other symbols).

### Imports **from** `app/handlers/payments/callbacks.py`

| Importer | What is imported |
|----------|-------------------|
| `app/handlers/payments/__init__.py` | `payments_callbacks_router` (and includes it in the payments router) |

No other files import from `payments/callbacks.py`.

### Legacy dependency (only from `app/handlers/payments/callbacks.py`)

| From file | Import | Defined in |
|-----------|--------|------------|
| `app/handlers/payments/callbacks.py` (line 20) | `from handlers import show_payment_method_selection` | `handlers.py` (root, ~line 1374) |

So **only** `app/handlers/payments/callbacks.py` depends on the legacy root `handlers.py` for `show_payment_method_selection`. It uses it once (around line 340) when showing the payment method selection after tariff/period choice.

---

## 6. Which file is “canonical” and what is safe to remove

- **There is no single canonical file.** The codebase uses **two** active payment-callback modules:
  - **`app/handlers/callbacks/payments_callbacks.py`** — topup, withdraw, pay:balance/card/crypto, topup_crypto/card, etc.
  - **`app/handlers/payments/callbacks.py`** — buy flow (menu_buy_vpn, tariff/period, promo, payment methods, admin approve/reject, corporate access).

- **Safe to remove (only after refactor):**
  - **Nothing** without refactoring: both modules are registered and handle different callbacks. Removing either file without moving its handlers would break those flows.
  - To **remove** one of them you would need to:
    - Move all its handlers (and any in-file helpers) into the other module (or into a new single module), and
    - Remove its router from the corresponding `__init__.py`, and
    - Fix the legacy import in `app/handlers/payments/callbacks.py`: replace `from handlers import show_payment_method_selection` with an import from the chosen canonical location (e.g. `app/handlers/common` or the consolidated payment callbacks module) once `show_payment_method_selection` is moved out of `handlers.py`.

- **Consolidation recommendation:** If you want one canonical payment-callbacks module, the natural place is **`app/handlers/payments/callbacks.py`** (already under `payments/` with buy, tariff, period, payment methods, admin approval). Then:
  - Move topup/withdraw/pay:* handlers from `app/handlers/callbacks/payments_callbacks.py` into `app/handlers/payments/callbacks.py` (or a sibling module under `payments/`),
  - Remove `from .payments_callbacks import payments_router` and `router.include_router(payments_router)` from `app/handlers/callbacks/__init__.py`,
  - And migrate `show_payment_method_selection` from `handlers.py` into `app/handlers/` (e.g. `app/handlers/common/screens.py` or payments) and update the import in `payments/callbacks.py`.

---

## 7. Summary table

| Question | Answer |
|----------|--------|
| Router from `app/handlers/__init__.py` | Both `callbacks_router` and `payments_router` are included; the two payment callback modules are nested inside these. |
| Both payment callback routers registered? | Yes. One via `callbacks` package, one via `payments` package. No overlapping `callback_data`. |
| Imports of `callbacks/payments_callbacks.py` | Only `app/handlers/callbacks/__init__.py` (imports `payments_router`). |
| Imports of `payments/callbacks.py` | Only `app/handlers/payments/__init__.py` (imports `payments_callbacks_router`). |
| Legacy import | `app/handlers/payments/callbacks.py` → `from handlers import show_payment_method_selection` (defined in root `handlers.py`). |
| Canonical file | None today; both are in use. Prefer consolidating into `app/handlers/payments/callbacks.py` if merging. |
| Safe to remove without refactor? | No. Both modules are live; removing either would break part of payment flows. |
