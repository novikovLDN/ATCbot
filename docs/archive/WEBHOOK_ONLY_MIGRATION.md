# Webhook-Only Mode Migration

**Date:** 2025-02-15  
**Status:** ✅ Completed  
**Impact:** Polling mode completely removed; webhook is now mandatory.

---

## Changes Summary

### 1. **config.py** — WEBHOOK_URL is now mandatory

- **Before:** `WEBHOOK_URL` was optional; if not set, bot would use polling mode.
- **After:** `WEBHOOK_URL` and `WEBHOOK_SECRET` are **REQUIRED**. Bot exits with error if not set.
- **Impact:** No more polling mode fallback; ensures single mode of operation.

```python
# Now REQUIRED - exits if not set
WEBHOOK_URL = env("WEBHOOK_URL")
if not WEBHOOK_URL:
    print(f"ERROR: {APP_ENV.upper()}_WEBHOOK_URL is REQUIRED!")
    sys.exit(1)
```

### 2. **main.py** — Polling code completely removed

#### Removed:
- `POLLING_REQUEST_TIMEOUT` constant
- `telegram_last_success_monotonic` variable (polling liveness tracking)
- Session wrapper for polling mode (`_wrapped_make_request`)
- Webhook audit block (was only for polling mode)
- Entire `else` branch with `dp.start_polling()` logic
- `stop_polling()` call in shutdown
- Separate health server (webhook mode uses FastAPI `/health`)

#### Simplified:
- Watchdog now **webhook-only** — tracks `last_webhook_update_at` only
- Startup logs simplified — no "polling started" message
- Instance ID renamed from `POLLING_INSTANCE_ID` to `BOT_INSTANCE_ID`

#### Added:
- **Enhanced webhook error logging:**
  - `WEBHOOK_SET_FAILED` with full traceback
  - `WEBHOOK_VERIFICATION_FAILED` with detailed error
  - `WEBHOOK_INFO` log with webhook state for diagnostics
  - `UVICORN_START_FAILED` with full traceback

### 3. **Advisory Lock** — Already in place

- PostgreSQL advisory lock prevents multiple instances with same token
- Lock timeout: 1s max (non-blocking)
- On failure: continues without lock (logs warning)

---

## Verification Checklist

After deployment, verify in logs:

- ✅ `WEBHOOK_SET_SUCCESS url=...`
- ✅ `WEBHOOK_VERIFIED url=...`
- ✅ `WEBHOOK_INFO` with webhook state
- ✅ `UVICORN_STARTED host=0.0.0.0 port=...`
- ✅ No "polling started" messages
- ✅ No `TelegramConflictError` (multiple getUpdates)

---

## Environment Variables Required

**MANDATORY:**
- `{ENV}_WEBHOOK_URL` (e.g., `PROD_WEBHOOK_URL`, `STAGE_WEBHOOK_URL`)
- `{ENV}_WEBHOOK_SECRET` (e.g., `PROD_WEBHOOK_SECRET`, `STAGE_WEBHOOK_SECRET`)

**Optional:**
- `BOT_INSTANCE_ID` (defaults to UUID if not set)

---

## Benefits

1. **No conflicts:** Single mode eliminates `TelegramConflictError` from simultaneous polling + webhook
2. **Simpler code:** Removed ~100 lines of polling logic
3. **Better diagnostics:** Enhanced webhook error logging
4. **Production-ready:** Webhook is the standard for production deployments

---

## Migration Notes

- **Local development:** Must set `LOCAL_WEBHOOK_URL` and `LOCAL_WEBHOOK_SECRET` (no more polling fallback)
- **Railway/Production:** Already using webhook; no changes needed if env vars are set
- **Advisory lock:** Prevents multiple instances; if lock fails, process continues (logs warning)

---

## Rollback Plan

If issues occur, revert commits:
- `config.py`: Restore optional `WEBHOOK_URL` check
- `main.py`: Restore polling `else` branch (not recommended — webhook is production standard)
