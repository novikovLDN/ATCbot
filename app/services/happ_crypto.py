"""Happ crypto-link (happ://crypt4/) — sealed subscription URL wrapper.

Wraps a plain subscription URL in Happ's `happ://crypt4/<base64>` deep link.
The payload is RSA-4096 / PKCS#1 v1.5 encrypted with the Happ V4 public key
and base64-encoded with the standard alphabet (Happ's parser requires the
standard alphabet — NOT urlsafe).

Only the Happ client can decrypt the link (it ships the matching private
key). Sub-stitute clients (V2Box, Streisand, Hiddify, NekoBox, …) cannot
open these — for those we keep the plain `https://` subscription URL.

Implementation is intentionally pure-stdlib (no `cryptography` /
`PyCryptodome` dependency): RSA "public encrypt" is just `c = m**e mod n`,
and PKCS#1 v1.5 padding is a simple byte recipe. This keeps the bot's
build minimal and avoids OpenSSL ABI woes on slim Docker images.

Format spec (Happ V4):
    c          = RSA_PKCS1v1_5_encrypt(content_utf8, happ_pub_v4)
    deep_link  = "happ://crypt4/" + base64(c)
    maxlen     = k - 11 = 512 - 11 = 501 bytes (k = |n|/8 = 512)

Sources:
    @kastov/cryptohapp NPM package
    https://happ.su/main/dev-docs/crypto-link

This module is meant to be a drop-in: when Happ rotates to crypt5 we
only need to swap the PEM constant and the prefix below; callers stay
the same.
"""
from __future__ import annotations

import base64
import logging
import os

logger = logging.getLogger(__name__)

DEEP_LINK_PREFIX = "happ://crypt4/"

# Happ V4 RSA-4096 public key (SubjectPublicKeyInfo, e=65537).
# Source: provided by the Happ author (kastov) and published at
# happ.su/main/dev-docs/crypto-link. Rotate together with DEEP_LINK_PREFIX
# whenever Happ rolls a new crypt version.
_HAPP_PUBLIC_KEY_V4_PEM = """\
-----BEGIN PUBLIC KEY-----
MIICIjANBgkqhkiG9w0BAQEFAAOCAg8AMIICCgKCAgEA3UZ0M3L4K+WjM3vkbQnz
ozHg/cRbEXvQ6i4A8RVN4OM3rK9kU01FdjyoIgywve8OEKsFnVwERZAQZ1Trv60B
hmaM76QQEE+EUlIOL9EpwKWGtTL5lYC1sT9XJMNP3/CI0gP5wwQI88cY/xedpOEB
W72EmOOShHUm/b/3m+HPmqwc4ugKj5zWV5SyiT829aFA5DxSjmIIFBAms7DafmSq
LFTYIQL5cShDY2u+/sqyAw9yZIOoqW2TFIgIHhLPWek/ocDU7zyOrlu1E0SmcQQb
LFqHq02fsnH6IcqTv3N5Adb/CkZDDQ6HvQVBmqbKZKf7ZdXkqsc/Zw27xhG7OfXC
tUmWsiL7zA+KoTd3avyOh93Q9ju4UQsHthL3Gs4vECYOCS9dsXXSHEY/1ngU/hjO
WFF8QEE/rYV6nA4PTyUvo5RsctSQL/9DJX7XNh3zngvif8LsCN2MPvx6X+zLouBX
zgBkQ9DFfZAGLWf9TR7KVjZC/3NsuUCDoAOcpmN8pENBbeB0puiKMMWSvll36+2M
YR1Xs0MgT8Y9TwhE2+TnnTJOhzmHi/BxiUlY/w2E0s4ax9GHAmX0wyF4zeV7kDkc
vHuEdc0d7vDmdw0oqCqWj0Xwq86HfORu6tm1A8uRATjb4SzjTKclKuoElVAVa5Jo
oh/uZMozC65SmDw+N5p6Su8CAwEAAQ==
-----END PUBLIC KEY-----
"""


# ── Minimal DER reader (SPKI parsing) ──────────────────────────────


class _DER:
    """Sliding cursor over DER bytes; returns one TLV element per read()."""

    __slots__ = ("d", "i")

    def __init__(self, data: bytes) -> None:
        self.d = data
        self.i = 0

    def read(self) -> tuple[int, bytes]:
        d = self.d
        i = self.i
        tag = d[i]
        i += 1
        length = d[i]
        i += 1
        if length & 0x80:
            n = length & 0x7F
            length = int.from_bytes(d[i:i + n], "big")
            i += n
        body = d[i:i + length]
        i += length
        self.i = i
        return tag, body


def _parse_spki_rsa(pem: str) -> tuple[int, int]:
    """Extract (n, e) from a PEM-encoded RSA SubjectPublicKeyInfo blob.

    Structure (RFC 5280 / RFC 8017):
        SEQUENCE {
            AlgorithmIdentifier   -- SEQUENCE (OID + NULL); skipped
            BIT STRING            -- wraps the RSAPublicKey:
                SEQUENCE {
                    INTEGER n
                    INTEGER e
                }
        }
    """
    b64 = "".join(line.strip() for line in pem.splitlines() if "-----" not in line)
    _, outer = _DER(base64.b64decode(b64)).read()  # outer SEQUENCE
    inner = _DER(outer)
    inner.read()                                    # AlgorithmIdentifier (skip)
    _, bitstr = inner.read()                        # BIT STRING
    # bitstr[0] = number of unused bits in the final byte. For SPKI it
    # is always 0x00 (byte-aligned), so we can just skip one byte.
    _, pub = _DER(bitstr[1:]).read()                # RSAPublicKey SEQUENCE
    nums = _DER(pub)
    _, n_b = nums.read()                            # INTEGER n
    _, e_b = nums.read()                            # INTEGER e
    return int.from_bytes(n_b, "big"), int.from_bytes(e_b, "big")


_N, _E = _parse_spki_rsa(_HAPP_PUBLIC_KEY_V4_PEM)
_K = (_N.bit_length() + 7) // 8                     # 512 for RSA-4096
MAX_CONTENT = _K - 11                               # 501 — PKCS#1 v1.5 limit

# Sanity assertions — wrong PEM here would be a deploy-time issue,
# better to fail at import than to silently produce broken deeplinks.
assert _N.bit_length() == 4096, (
    f"Happ V4 modulus must be 4096-bit (got {_N.bit_length()})"
)
assert _E == 65537, f"Happ V4 public exponent must be 65537 (got {_E})"
assert _K == 512
assert MAX_CONTENT == 501


# ── PKCS#1 v1.5 public-key encrypt ────────────────────────────────


def _rsa_pkcs1v15_encrypt(msg: bytes) -> bytes:
    """Return PKCS#1 v1.5 (type-02) public-key encryption of `msg`.

    EM layout (RFC 8017 §7.2.1):
        EM = 0x00 || 0x02 || PS || 0x00 || M
    where PS is at least 8 octets of non-zero random bytes,
    and |EM| == k.
    """
    if len(msg) > MAX_CONTENT:
        raise ValueError(
            f"content too long for crypt4: {len(msg)} > {MAX_CONTENT}"
        )
    ps_len = _K - 3 - len(msg)
    # PS must contain only non-zero octets (RFC 8017). os.urandom can
    # produce zeros; filter them out and top up until we have ps_len.
    ps = bytearray()
    while len(ps) < ps_len:
        for b in os.urandom(ps_len - len(ps)):
            if b != 0:
                ps.append(b)
                if len(ps) == ps_len:
                    break
    em = b"\x00\x02" + bytes(ps) + b"\x00" + msg
    c_int = pow(int.from_bytes(em, "big"), _E, _N)
    # to_bytes(_K, ...) preserves leading-zero bytes of the ciphertext;
    # without it shorter ciphertexts would silently lose bytes.
    return c_int.to_bytes(_K, "big")


# ── Public API ────────────────────────────────────────────────────


def to_crypt_link(content: str) -> str:
    """Wrap a UTF-8 string (typically a subscription URL) into
    `happ://crypt4/<base64>`. Two calls on the same input return different
    ciphertexts thanks to the random PS — that's a feature, not a bug."""
    ct = _rsa_pkcs1v15_encrypt(content.encode("utf-8"))
    return DEEP_LINK_PREFIX + base64.b64encode(ct).decode("ascii")


def format_for_user(url: str | None) -> str | None:
    """Same as `to_crypt_link` but tolerant: pass-through for None/empty
    inputs, and fall back to the raw URL on any encryption error so the
    user never gets an empty/broken link in the UI. Errors are logged
    loudly via `logger.exception`."""
    if not url:
        return url
    try:
        return to_crypt_link(url)
    except Exception:
        logger.exception("Failed to build happ crypt link; using raw URL")
        return url


__all__ = [
    "DEEP_LINK_PREFIX",
    "MAX_CONTENT",
    "to_crypt_link",
    "format_for_user",
]
