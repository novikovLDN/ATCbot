"""Web Push (browser system notifications) for the admin dashboard.

Storage:
  - app_settings.key='vapid_keys'   — JSON {public, private_pem},
                                        auto-generated on first call
  - admin_push_subscriptions        — each registered browser/device

Flow:
  1. Browser GETs /settings/push/vapid-key, calls PushManager.subscribe
     with that key as applicationServerKey.
  2. Browser POSTs the SubscriptionInfo to /settings/push/subscribe.
  3. send_to_all() iterates current subs and uses pywebpush; 404 / 410
     responses purge dead endpoints.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Any, Optional

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

logger = logging.getLogger(__name__)


# ── VAPID key storage ────────────────────────────────────────────────


async def _read_setting(key: str) -> Optional[str]:
    from database.core import get_pool
    pool = await get_pool()
    if pool is None:
        return None
    try:
        async with pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT value FROM app_settings WHERE key = $1", key,
            )
    except Exception as e:
        logger.warning("_read_setting(%s) failed: %s", key, e)
        return None


async def _write_setting(key: str, value: str) -> bool:
    from database.core import get_pool
    pool = await get_pool()
    if pool is None:
        return False
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO app_settings (key, value)
                   VALUES ($1, $2)
                   ON CONFLICT (key) DO UPDATE
                       SET value = EXCLUDED.value, updated_at = NOW()""",
                key, value,
            )
        return True
    except Exception as e:
        logger.warning("_write_setting(%s) failed: %s", key, e)
        return False


def _generate_vapid_keys() -> dict[str, str]:
    """Generate a fresh EC SECP256R1 keypair. Returns:
       - public:        base64url-encoded uncompressed point (65 bytes)
       - private_pem:   PKCS8 PEM (kept for backwards-compat / debugging)
       - private_raw:   base64url-encoded 32-byte EC scalar
                        (RFC8292 §2, what pywebpush 2.x / py-vapid actually
                        wants — feeding it PEM trips an ASN.1 parser bug
                        on some cryptography versions)."""
    priv = ec.generate_private_key(ec.SECP256R1())
    priv_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    pub = priv.public_key()
    pub_raw = pub.public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    pub_b64 = base64.urlsafe_b64encode(pub_raw).rstrip(b"=").decode("ascii")
    priv_raw_bytes = priv.private_numbers().private_value.to_bytes(32, "big")
    priv_raw_b64 = (
        base64.urlsafe_b64encode(priv_raw_bytes).rstrip(b"=").decode("ascii")
    )
    return {
        "public": pub_b64,
        "private_pem": priv_pem,
        "private_raw": priv_raw_b64,
    }


def _raw_from_pem(pem: str) -> Optional[str]:
    """Recover the base64url-encoded 32-byte EC scalar from a PEM
    private key — used to migrate VAPID keys that were created before
    we started storing private_raw."""
    try:
        key = serialization.load_pem_private_key(pem.encode(), password=None)
        if not isinstance(key, ec.EllipticCurvePrivateKey):
            return None
        raw = key.private_numbers().private_value.to_bytes(32, "big")
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    except Exception as e:
        logger.warning("VAPID PEM→raw migration failed: %s", e)
        return None


async def get_vapid_keys() -> dict[str, str]:
    """Return existing keys or auto-generate on first call. Keys
    persist in app_settings. Auto-migrates older records that only
    have PEM (no private_raw) on first read."""
    raw = await _read_setting("vapid_keys")
    if raw:
        try:
            data = json.loads(raw)
            if (
                isinstance(data, dict)
                and "public" in data
                and "private_pem" in data
            ):
                if "private_raw" not in data:
                    derived = _raw_from_pem(data["private_pem"])
                    if derived:
                        data["private_raw"] = derived
                        await _write_setting("vapid_keys", json.dumps(data))
                        logger.info("VAPID keys migrated: added private_raw")
                return data
        except Exception:
            pass
    keys = _generate_vapid_keys()
    await _write_setting("vapid_keys", json.dumps(keys))
    logger.info("VAPID keys generated (first time)")
    return keys


async def get_public_key() -> str:
    keys = await get_vapid_keys()
    return keys["public"]


# ── Subscription storage ────────────────────────────────────────────


async def upsert_subscription(
    *,
    endpoint: str,
    p256dh: str,
    auth: str,
    user_agent: Optional[str] = None,
    label: Optional[str] = None,
) -> bool:
    from database.core import get_pool
    pool = await get_pool()
    if pool is None:
        return False
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO admin_push_subscriptions
                       (endpoint, p256dh, auth, user_agent, label)
                   VALUES ($1, $2, $3, $4, $5)
                   ON CONFLICT (endpoint) DO UPDATE
                       SET p256dh = EXCLUDED.p256dh,
                           auth = EXCLUDED.auth,
                           user_agent = COALESCE(EXCLUDED.user_agent, admin_push_subscriptions.user_agent),
                           label = COALESCE(EXCLUDED.label, admin_push_subscriptions.label)""",
                endpoint, p256dh, auth,
                (user_agent or "")[:300] or None,
                (label or "")[:60] or None,
            )
        return True
    except Exception as e:
        logger.exception("upsert_subscription failed: %s", e)
        return False


async def remove_subscription(endpoint: str) -> bool:
    from database.core import get_pool
    pool = await get_pool()
    if pool is None:
        return False
    try:
        async with pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM admin_push_subscriptions WHERE endpoint = $1",
                endpoint,
            )
        return "1" in result
    except Exception as e:
        logger.warning("remove_subscription failed: %s", e)
        return False


async def list_subscriptions() -> list[dict]:
    from database.core import get_pool
    pool = await get_pool()
    if pool is None:
        return []
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT id, endpoint, p256dh, auth, user_agent, label,
                          created_at, last_used_at
                   FROM admin_push_subscriptions
                   ORDER BY created_at DESC"""
            )
        out = []
        for r in rows:
            d = dict(r)
            for k in ("created_at", "last_used_at"):
                if d.get(k):
                    d[k] = d[k].isoformat()
            out.append(d)
        return out
    except Exception as e:
        logger.warning("list_subscriptions failed: %s", e)
        return []


async def subscription_count() -> int:
    from database.core import get_pool
    pool = await get_pool()
    if pool is None:
        return 0
    try:
        async with pool.acquire() as conn:
            n = await conn.fetchval(
                "SELECT COUNT(*) FROM admin_push_subscriptions",
            )
            return int(n or 0)
    except Exception:
        return 0


async def _touch_last_used(endpoint: str) -> None:
    from database.core import get_pool
    pool = await get_pool()
    if pool is None:
        return
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE admin_push_subscriptions SET last_used_at = NOW() WHERE endpoint = $1",
                endpoint,
            )
    except Exception:
        pass


# ── Sending ──────────────────────────────────────────────────────────


async def send_to_all(
    *,
    title: str,
    body: str,
    url: Optional[str] = None,
    tag: Optional[str] = None,
    icon: Optional[str] = None,
) -> dict:
    """Fan-out a single notification to every registered subscription.

    Runs synchronously over the list; pywebpush is fast enough for the
    handful of devices a single admin registers. Dead endpoints (404/410)
    are auto-removed. Returns counters."""
    keys = await get_vapid_keys()
    subs = await list_subscriptions()
    if not subs:
        return {"sent": 0, "failed": 0, "removed": 0, "total": 0}

    import config
    from pywebpush import webpush, WebPushException  # lazy — heavy import

    claim_sub = "mailto:noreply@atlassecure.ru"
    base_url = getattr(config, "DASHBOARD_BASE_URL", "") or ""
    payload = {
        "title": title,
        "body": body,
        "url": url or (base_url.rstrip("/") + "/dashboard/"),
        "icon": icon or "/dashboard/icon-192.png",
        "badge": "/dashboard/icon-192.png",
        "tag": tag or "atlas",
    }
    data_str = json.dumps(payload, ensure_ascii=False)

    sent = 0
    failed = 0
    removed = 0
    errors: list[dict] = []

    # py-vapid 1.9+ / pywebpush 2.x: prefer the 32-byte raw base64url
    # private key. PEM goes through an ASN.1 parser that's fragile on
    # some cryptography versions ("ASN.1 parsing error: invalid").
    vapid_pk = keys.get("private_raw") or keys["private_pem"]

    def _do_send(endpoint: str, p256dh: str, auth: str) -> None:
        # vapid_claims must be a fresh dict each call — pywebpush
        # mutates it in place to add "aud"/"exp", and reusing one
        # cross-endpoint would leak the previous aud.
        webpush(
            subscription_info={
                "endpoint": endpoint,
                "keys": {"p256dh": p256dh, "auth": auth},
            },
            data=data_str,
            vapid_private_key=vapid_pk,
            vapid_claims={"sub": claim_sub},
            ttl=60,
        )

    for sub in subs:
        endpoint = sub["endpoint"]
        host = ""
        try:
            from urllib.parse import urlparse
            host = urlparse(endpoint).netloc
        except Exception:
            host = endpoint[:60]
        try:
            # pywebpush is synchronous (uses requests). Push services like
            # Apple's mutualtls.push.apple.com can hang for seconds — never
            # block the event loop.
            await asyncio.to_thread(
                _do_send, endpoint, sub["p256dh"], sub["auth"],
            )
            sent += 1
            await _touch_last_used(endpoint)
            logger.info("PUSH_SEND_OK host=%s", host)
        except WebPushException as e:
            status = getattr(getattr(e, "response", None), "status_code", 0)
            resp_text = ""
            try:
                resp_text = (getattr(e, "response", None).text or "")[:200]
            except Exception:
                pass
            if status in (404, 410):
                # Subscription has been revoked by the user / the push
                # service. Drop it so we don't keep retrying.
                await remove_subscription(endpoint)
                removed += 1
                logger.info(
                    "PUSH_SUB_GONE status=%s host=%s — removed",
                    status, host,
                )
                errors.append({
                    "host": host, "status": status,
                    "reason": "subscription_gone", "detail": resp_text,
                })
            else:
                failed += 1
                logger.warning(
                    "PUSH_SEND_FAIL status=%s host=%s err=%s body=%s",
                    status, host, e, resp_text,
                )
                errors.append({
                    "host": host, "status": status,
                    "reason": "webpush_error",
                    "detail": (str(e) + " " + resp_text).strip()[:240],
                })
        except Exception as e:
            failed += 1
            logger.exception("PUSH_SEND_UNEXPECTED host=%s err=%s", host, e)
            errors.append({
                "host": host, "status": 0,
                "reason": type(e).__name__,
                "detail": str(e)[:240],
            })

    return {
        "sent": sent,
        "failed": failed,
        "removed": removed,
        "total": len(subs),
        "errors": errors,
    }
