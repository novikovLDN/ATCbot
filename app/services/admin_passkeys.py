"""WebAuthn (passkey) support for the admin dashboard.

Architecture:
  - one row per registered authenticator (iPhone Face ID, MacBook
    Touch ID, YubiKey, ...) in `admin_passkeys`
  - Relying Party ID = the dashboard's host, e.g. "api.atlassecure.ru"
  - User ID = config.ADMIN_TELEGRAM_ID encoded as bytes (stable across
    sessions, never changes)
  - Challenges live in Redis (5 min TTL) or in-memory fallback

Two flows:
  Registration   — requires an authenticated session (you can only
                   bind a passkey to your account when you've already
                   proven you're the admin via password)
  Authentication — public; produces a fresh cookie session on success
"""
from __future__ import annotations

import base64
import json
import logging
import secrets
import time
from typing import Any, Optional
from urllib.parse import urlparse

from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers.cose import COSEAlgorithmIdentifier
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    PublicKeyCredentialType,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

import config

logger = logging.getLogger(__name__)

_CHALLENGE_TTL_SECONDS = 300  # 5 min
_REDIS_KEY_PREFIX = "dashboard:passkey_challenge:"

# In-memory fallback when Redis isn't configured. Maps
# challenge_token → (challenge_bytes, kind, expires_at).
_MEM_CHALLENGES: dict[str, tuple[bytes, str, float]] = {}


# ── Helpers ──────────────────────────────────────────────────────────


def _rp_id() -> str:
    """Relying-Party ID is the eTLD+1 of the dashboard URL.
    WebAuthn requires it match window.location.hostname."""
    url = getattr(config, "DASHBOARD_BASE_URL", "") or ""
    if not url:
        return ""
    host = urlparse(url).hostname or ""
    return host


def _rp_origin() -> str:
    url = getattr(config, "DASHBOARD_BASE_URL", "") or ""
    if not url:
        return ""
    p = urlparse(url)
    if p.scheme and p.netloc:
        return f"{p.scheme}://{p.netloc}"
    return url


def _user_id_bytes() -> bytes:
    """Stable user_id for WebAuthn. Telegram numeric id, 8 bytes."""
    return int(config.ADMIN_TELEGRAM_ID).to_bytes(8, "big", signed=False)


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


# ── Challenge storage ───────────────────────────────────────────────


async def _redis():
    try:
        from app.utils.redis_client import get_client, is_configured
        if not is_configured():
            return None
        return await get_client()
    except Exception:
        return None


async def store_challenge(challenge: bytes, kind: str) -> str:
    """Save a challenge for later verify. Returns an opaque token the
    client echoes back."""
    token = secrets.token_urlsafe(24)
    payload = {"c": _b64url(challenge), "k": kind}
    r = await _redis()
    if r is not None:
        try:
            await r.set(
                _REDIS_KEY_PREFIX + token,
                json.dumps(payload),
                ex=_CHALLENGE_TTL_SECONDS,
            )
            return token
        except Exception as e:
            logger.warning("passkey challenge redis store failed: %s", e)
    _MEM_CHALLENGES[token] = (challenge, kind, time.time() + _CHALLENGE_TTL_SECONDS)
    return token


async def pop_challenge(token: str, expected_kind: str) -> Optional[bytes]:
    """One-shot lookup: returns the challenge bytes and deletes it."""
    if not token:
        return None
    r = await _redis()
    if r is not None:
        try:
            raw = await r.getdel(_REDIS_KEY_PREFIX + token)
            if raw:
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                data = json.loads(raw)
                if data.get("k") != expected_kind:
                    return None
                return _b64url_decode(data["c"])
        except Exception as e:
            logger.warning("passkey challenge redis lookup failed: %s", e)
    entry = _MEM_CHALLENGES.pop(token, None)
    if entry is None:
        return None
    ch, kind, exp = entry
    if exp < time.time() or kind != expected_kind:
        return None
    return ch


# ── DB access ───────────────────────────────────────────────────────


async def passkey_count() -> int:
    from database.core import get_pool
    pool = await get_pool()
    if pool is None:
        return 0
    try:
        async with pool.acquire() as conn:
            n = await conn.fetchval("SELECT COUNT(*) FROM admin_passkeys")
            return int(n or 0)
    except Exception as e:
        logger.warning("passkey_count failed: %s", e)
        return 0


async def list_passkeys() -> list:
    from database.core import get_pool
    pool = await get_pool()
    if pool is None:
        return []
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT id, label, aaguid, transports,
                          created_at, last_used_at
                   FROM admin_passkeys
                   ORDER BY created_at DESC"""
            )
        out = []
        for r in rows:
            d = dict(r)
            for k in ("created_at", "last_used_at"):
                if d.get(k):
                    d[k] = d[k].isoformat()
            if d.get("transports"):
                try:
                    d["transports"] = json.loads(d["transports"])
                except Exception:
                    d["transports"] = []
            out.append(d)
        return out
    except Exception as e:
        logger.warning("list_passkeys failed: %s", e)
        return []


async def _existing_credential_ids() -> list[bytes]:
    from database.core import get_pool
    pool = await get_pool()
    if pool is None:
        return []
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT credential_id FROM admin_passkeys"
            )
            return [bytes(r["credential_id"]) for r in rows]
    except Exception:
        return []


async def insert_passkey(
    *,
    credential_id: bytes,
    public_key: bytes,
    sign_count: int,
    transports: Optional[list[str]],
    label: Optional[str],
    aaguid: Optional[str],
) -> bool:
    from database.core import get_pool
    pool = await get_pool()
    if pool is None:
        return False
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO admin_passkeys
                       (credential_id, public_key, sign_count,
                        transports, label, aaguid)
                   VALUES ($1, $2, $3, $4, $5, $6)
                   ON CONFLICT (credential_id) DO NOTHING""",
                credential_id, public_key, sign_count,
                json.dumps(transports or []),
                (label or "Passkey")[:64],
                (aaguid or "")[:64] or None,
            )
        return True
    except Exception as e:
        logger.exception("insert_passkey failed: %s", e)
        return False


async def get_passkey_by_credential_id(credential_id: bytes) -> Optional[dict]:
    from database.core import get_pool
    pool = await get_pool()
    if pool is None:
        return None
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT id, public_key, sign_count
                   FROM admin_passkeys WHERE credential_id = $1""",
                credential_id,
            )
            return dict(row) if row else None
    except Exception:
        return None


async def bump_sign_count(pk_id: int, new_count: int) -> None:
    from database.core import get_pool
    pool = await get_pool()
    if pool is None:
        return
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """UPDATE admin_passkeys
                   SET sign_count = $1, last_used_at = NOW()
                   WHERE id = $2""",
                new_count, pk_id,
            )
    except Exception as e:
        logger.warning("bump_sign_count failed: %s", e)


async def delete_passkey(pk_id: int) -> bool:
    from database.core import get_pool
    pool = await get_pool()
    if pool is None:
        return False
    try:
        async with pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM admin_passkeys WHERE id = $1", pk_id,
            )
        return "1" in result
    except Exception as e:
        logger.warning("delete_passkey failed: %s", e)
        return False


async def purge_all_passkeys() -> None:
    from database.core import get_pool
    pool = await get_pool()
    if pool is None:
        return
    try:
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM admin_passkeys")
    except Exception:
        pass


# ── Public flows ────────────────────────────────────────────────────


async def make_registration_options(
    username: str,
    display_name: str = "Atlas Admin",
) -> tuple[dict[str, Any], str]:
    """Returns (publicKeyCredentialCreationOptions, challenge_token).
    Frontend feeds the options into navigator.credentials.create() and
    returns the attestation along with the token to /register/verify."""
    rp_id = _rp_id()
    if not rp_id:
        raise RuntimeError("DASHBOARD_BASE_URL is not configured")

    # Exclude already-registered credentials so the same authenticator
    # can't be re-bound silently.
    existing = await _existing_credential_ids()
    exclude = [
        PublicKeyCredentialDescriptor(
            id=cid, type=PublicKeyCredentialType.PUBLIC_KEY,
        )
        for cid in existing
    ]

    options = generate_registration_options(
        rp_id=rp_id,
        rp_name="Atlas Admin",
        user_id=_user_id_bytes(),
        user_name=username,
        user_display_name=display_name,
        attestation="none",
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
        exclude_credentials=exclude,
        supported_pub_key_algs=[
            COSEAlgorithmIdentifier.ECDSA_SHA_256,
            COSEAlgorithmIdentifier.RSASSA_PKCS1_v1_5_SHA_256,
        ],
    )

    token = await store_challenge(options.challenge, "register")
    # Convert the options dataclass to a JSON-safe dict via the lib's
    # built-in helper (handles bytes → base64url throughout).
    from webauthn.helpers import options_to_json
    return json.loads(options_to_json(options)), token


async def verify_and_store_registration(
    *,
    challenge_token: str,
    credential: dict[str, Any],
    label: Optional[str] = None,
) -> tuple[bool, Optional[str]]:
    """Verify the browser's attestation and persist the credential.
    Returns (ok, error_message)."""
    rp_id = _rp_id()
    origin = _rp_origin()
    if not rp_id or not origin:
        return False, "rp_not_configured"
    challenge = await pop_challenge(challenge_token, "register")
    if not challenge:
        return False, "challenge_expired"

    try:
        verification = verify_registration_response(
            credential=credential,
            expected_challenge=challenge,
            expected_origin=origin,
            expected_rp_id=rp_id,
            require_user_verification=False,
        )
    except Exception as e:
        logger.warning("passkey register verify failed: %s", e)
        return False, f"verify_failed: {e}"

    ok = await insert_passkey(
        credential_id=verification.credential_id,
        public_key=verification.credential_public_key,
        sign_count=verification.sign_count,
        transports=(credential.get("response", {}) or {}).get("transports") or [],
        label=label,
        aaguid=str(getattr(verification, "aaguid", "") or ""),
    )
    return ok, None if ok else "db_insert_failed"


async def make_authentication_options() -> tuple[dict[str, Any], str]:
    rp_id = _rp_id()
    if not rp_id:
        raise RuntimeError("DASHBOARD_BASE_URL is not configured")

    existing = await _existing_credential_ids()
    allow = [
        PublicKeyCredentialDescriptor(
            id=cid, type=PublicKeyCredentialType.PUBLIC_KEY,
        )
        for cid in existing
    ]
    options = generate_authentication_options(
        rp_id=rp_id,
        allow_credentials=allow,
        user_verification=UserVerificationRequirement.PREFERRED,
    )
    token = await store_challenge(options.challenge, "auth")
    from webauthn.helpers import options_to_json
    return json.loads(options_to_json(options)), token


async def verify_authentication(
    *,
    challenge_token: str,
    credential: dict[str, Any],
) -> tuple[bool, Optional[str]]:
    """Verify the browser's assertion. Returns (ok, error_message)."""
    rp_id = _rp_id()
    origin = _rp_origin()
    if not rp_id or not origin:
        return False, "rp_not_configured"
    challenge = await pop_challenge(challenge_token, "auth")
    if not challenge:
        return False, "challenge_expired"

    raw_credential_id_b64url = credential.get("id") or credential.get("rawId")
    if not raw_credential_id_b64url:
        return False, "missing_credential_id"
    try:
        credential_id_bytes = _b64url_decode(str(raw_credential_id_b64url))
    except Exception:
        return False, "bad_credential_id"

    stored = await get_passkey_by_credential_id(credential_id_bytes)
    if not stored:
        return False, "unknown_credential"

    try:
        verification = verify_authentication_response(
            credential=credential,
            expected_challenge=challenge,
            expected_origin=origin,
            expected_rp_id=rp_id,
            credential_public_key=bytes(stored["public_key"]),
            credential_current_sign_count=int(stored["sign_count"]),
            require_user_verification=False,
        )
    except Exception as e:
        logger.warning("passkey auth verify failed: %s", e)
        return False, f"verify_failed: {e}"

    await bump_sign_count(int(stored["id"]), verification.new_sign_count)
    return True, None
