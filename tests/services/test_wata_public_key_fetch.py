"""WATA webhook key: lazy fetch from WATA when WATA_PUBLIC_KEY_PEM is not set.

Risk closed: without the env variable, every WATA webhook is verified with
the key fetched from GET {WATA_API_URL}/public-key. The existing tests replace
_fetch_public_key_pem with a mock, so the real fetch (HTTP status handling,
JSON `value`, one-line PEM with literal "\\n") never ran (coverage 09:
_fetch_public_key_pem 12/13 lines unrun). A bug there means either every real
payment callback is refused (500 until WATA gives up after 32 h — then only the
reconciler saves the money) or, worse, a bad key is cached. Fail-closed rules
pinned: a failed or malformed fetch is not cached, the signature check refuses,
a correct signature passes with the fetched key.
"""
from __future__ import annotations

import base64
import json
from types import SimpleNamespace

import httpx
import pytest

crypto = pytest.importorskip("cryptography")
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import padding, rsa  # noqa: E402

import wata_service as ws  # noqa: E402
from app.services.payments.confirmation import TransientPaymentError  # noqa: E402

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PEM = _KEY.public_key().public_bytes(
    serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
BODY = json.dumps({"orderId": "purchase_x", "transactionStatus": "Paid"}).encode()


def _sign(body: bytes, key=_KEY) -> str:
    return base64.b64encode(key.sign(body, padding.PKCS1v15(), hashes.SHA512())).decode()


class _Wata:
    def __init__(self):
        self.answers = []
        self.calls = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/public-key")
        self.calls += 1
        status, payload = self.answers.pop(0) if self.answers else (200, {"value": _PEM})
        return httpx.Response(status, json=payload)


@pytest.fixture
def wata(monkeypatch):
    w = _Wata()
    transport = httpx.MockTransport(w.handler)

    def client(*a, **kw):
        kw.pop("transport", None)
        return httpx.AsyncClient(*a, transport=transport, **kw)
    monkeypatch.setattr(ws, "httpx", SimpleNamespace(AsyncClient=client))
    monkeypatch.setattr(ws, "_PINNED_PUBLIC_KEY_PEM", "")
    monkeypatch.setattr(ws, "_PUBLIC_KEY_PEM", None)
    monkeypatch.setattr(ws, "_PUBLIC_KEY_FETCHED_AT", None)
    monkeypatch.setattr(ws, "_KEY_LOCK", None)
    return w


async def test_fetched_key_verifies_a_real_signature_and_is_cached(wata):
    wata.answers = [(200, {"value": _PEM.replace("\n", "\\n")})]   # one line, literal \n
    assert await ws._verify_webhook_signature(BODY, _sign(BODY)) is True
    assert await ws._verify_webhook_signature(BODY, _sign(BODY)) is True
    assert wata.calls == 1


async def test_failed_fetch_is_not_cached_and_refuses(wata):
    """No key → TransientPaymentError (the webhook answers 500, WATA retries;
    the payment is not lost), never a silent accept."""
    wata.answers = [(503, {"message": "down"})]
    with pytest.raises(TransientPaymentError):
        await ws._verify_webhook_signature(BODY, _sign(BODY))
    assert ws._PUBLIC_KEY_PEM is None
    # WATA is back: the next callback fetches again and passes
    assert await ws._verify_webhook_signature(BODY, _sign(BODY)) is True


@pytest.mark.parametrize("payload", [{"value": "not a pem"}, {}, {"value": ""}])
async def test_malformed_key_is_not_cached_and_refuses(wata, payload):
    wata.answers = [(200, payload)]
    with pytest.raises(TransientPaymentError):
        await ws._verify_webhook_signature(BODY, _sign(BODY))
    assert ws._PUBLIC_KEY_PEM is None


async def test_forged_signature_is_refused_with_the_fetched_key(wata):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    assert await ws._verify_webhook_signature(BODY, _sign(BODY, other)) is False
    assert await ws._verify_webhook_signature(BODY + b" ", _sign(BODY)) is False
    assert await ws._verify_webhook_signature(BODY, "%%%not-base64") is False
    # a stream of forged callbacks does not turn into a stream of key requests
    assert wata.calls <= 2


async def test_fetch_errors_return_none():
    class Boom:
        def AsyncClient(self, *a, **kw):  # noqa: N802
            raise httpx.ConnectError("no route")
    orig = ws.httpx
    ws.httpx = Boom()
    try:
        assert await ws._fetch_public_key_pem() is None
    finally:
        ws.httpx = orig
