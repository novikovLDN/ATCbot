"""Payment providers faked at the HTTP boundary + correctly signed webhooks.

FakeProviders is an httpx MockTransport for the provider APIs the bot CALLS
(invoice creation), installed into platega_service / wata_service /
cryptobot_service as their `httpx`. The webhook builders produce exactly what
the provider would POST to us, signed per docs/providers/*.md:

  Platega   static headers X-MerchantId / X-Secret (no body signature)
  WATA      X-Signature = base64(RSA-SHA512 PKCS#1 v1.5 over the RAW body),
            verified with the public key pinned via WATA_PUBLIC_KEY_PEM
  CryptoBot crypto-pay-api-signature = hex(HMAC-SHA256(key=SHA256(token), raw body))
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import itertools
import json
import uuid as uuid_lib
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

import httpx

PLATEGA_MERCHANT = "merchant-e2e"
PLATEGA_SECRET = "platega-secret-e2e"
CRYPTOBOT_TOKEN = "12345:cryptobot-e2e-token"
WATA_TOKEN = "wata-jwt-e2e"

_KEY = None


def _wata_key():
    global _KEY
    if _KEY is None:
        from cryptography.hazmat.primitives.asymmetric import rsa
        _KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return _KEY


def wata_public_pem() -> str:
    from cryptography.hazmat.primitives import serialization
    return _wata_key().public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()


def wata_sign(raw: bytes) -> str:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    sig = _wata_key().sign(raw, padding.PKCS1v15(), hashes.SHA512())
    return base64.b64encode(sig).decode()


def cryptobot_sign(raw: bytes, token: str = CRYPTOBOT_TOKEN) -> str:
    secret = hashlib.sha256(token.encode()).digest()
    return hmac.new(secret, raw, hashlib.sha256).hexdigest()


_tx = itertools.count(1)


# ── webhook builders: (path, raw body, headers) ─────────────────────────

def platega_webhook(purchase_id: str, amount: float, *, status: str = "CONFIRMED",
                    currency: str = "RUB", tx_id: Optional[str] = None,
                    merchant: str = PLATEGA_MERCHANT, secret: str = PLATEGA_SECRET) -> Tuple[str, bytes, Dict[str, str]]:
    body = {
        "id": tx_id or str(uuid_lib.uuid4()),
        "amount": amount,
        "currency": currency,
        "status": status,
        "paymentMethod": 2,
        "paymentDetails": {"amount": amount, "currency": currency},
        "payload": json.dumps({"purchase_id": purchase_id}),
    }
    raw = json.dumps(body).encode()
    return "/webhooks/platega", raw, {"X-MerchantId": merchant, "X-Secret": secret,
                                       "Content-Type": "application/json"}


def wata_webhook(order_id: str, amount: Any, *, status: str = "Paid", kind: str = "Payment",
                 currency: str = "RUB", tx_id: Optional[str] = None, sign: bool = True,
                 bad_signature: bool = False) -> Tuple[str, bytes, Dict[str, str]]:
    body = {
        "transactionType": "CardCrypto",
        "kind": kind,
        "transactionId": tx_id or str(uuid_lib.uuid4()),
        "transactionStatus": status,
        "terminalName": "atlas",
        "amount": amount,
        "currency": currency,
        "orderId": order_id,
        "orderDescription": "Atlas Secure VPN",
        "paymentTime": "2026-09-14T10:00:00Z",
        "commission": 0,
    }
    raw = json.dumps(body).encode()
    headers = {"Content-Type": "application/json"}
    if sign:
        signed = raw + b" " if bad_signature else raw
        headers["X-Signature"] = wata_sign(signed)
    return "/webhooks/wata", raw, headers


def cryptobot_webhook(purchase_id: str, amount: float, *, status: str = "paid",
                      invoice_id: Optional[int] = None, token: str = CRYPTOBOT_TOKEN,
                      update_type: str = "invoice_paid") -> Tuple[str, bytes, Dict[str, str]]:
    inv = invoice_id or (900_000 + next(_tx))
    body = {
        "update_id": inv,
        "update_type": update_type,
        "request_date": "2026-09-14T10:00:00.000Z",
        "payload": {
            "invoice_id": inv,
            "status": status,
            "currency_type": "fiat",
            "fiat": "RUB",
            "amount": f"{amount:.2f}",
            "paid_asset": "USDT",
            "payload": json.dumps({"purchase_id": purchase_id}),
        },
    }
    raw = json.dumps(body).encode()
    return "/webhooks/cryptobot", raw, {"crypto-pay-api-signature": cryptobot_sign(raw, token),
                                         "Content-Type": "application/json"}


# ── provider APIs the bot calls ─────────────────────────────────────────

class FakeProviders:
    def __init__(self) -> None:
        self.requests: List[Tuple[str, str, Any]] = []
        self.platega_tx: Dict[str, Dict[str, Any]] = {}
        self.wata_links: Dict[str, Dict[str, Any]] = {}
        self.cryptobot_invoices: Dict[int, Dict[str, Any]] = {}
        # WATA transaction search (GET /api/h2h/v2/transactions/?orderId=…),
        # used by the reconciler: orderId → transaction object. A test puts a
        # Paid transaction here to simulate "paid, but the webhook was lost".
        self.wata_paid: Dict[str, Dict[str, Any]] = {}
        self.down = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        try:
            body = json.loads(request.content.decode() or "null") if request.content else None
        except ValueError:
            body = None
        host, path = request.url.host, request.url.path
        self.requests.append((request.method, f"{host}{path}", body))
        if self.down:
            raise httpx.ConnectError("provider down", request=request)
        if path.endswith("/transaction/process") and request.method == "POST":
            tx = str(uuid_lib.uuid4())
            purchase_id = json.loads(body["payload"])["purchase_id"]
            self.platega_tx[tx] = {"purchase_id": purchase_id, "body": body, "path": path}
            link = "url" if path.endswith("/v2/transaction/process") else "redirect"   # as Platega answers
            return httpx.Response(200, json={"transactionId": tx, link: f"https://pay.platega.test/{tx}",
                                             "status": "PENDING"})
        if path.endswith("/h2h/links") and request.method == "POST":
            link = str(uuid_lib.uuid4())
            self.wata_links[link] = body
            return httpx.Response(200, json={"id": link, "url": f"https://pay.wata.test/{link}",
                                             "status": "Opened", "amount": body["amount"],
                                             "orderId": body["orderId"]})
        if "/h2h/links/" in path and request.method == "GET":
            link = path.rstrip("/").rsplit("/", 1)[-1]
            if link not in self.wata_links:          # purged by WATA retention / never existed
                return httpx.Response(404, json={"error": "link not found"})
            body = self.wata_links[link]
            return httpx.Response(200, json={"id": link, "status": "Opened",
                                             "amount": body.get("amount"), "orderId": body.get("orderId")})
        if path.rstrip("/").endswith("/h2h/v2/transactions") and request.method == "GET":
            order = request.url.params.get("orderId")
            items = [self.wata_paid[order]] if order in self.wata_paid else []
            return httpx.Response(200, json={"items": items, "totalCount": len(items)})
        if path.endswith("/h2h/public-key"):
            return httpx.Response(200, json={"value": wata_public_pem()})
        if path.endswith("/createInvoice") and request.method == "POST":
            inv = 700_000 + next(_tx)
            purchase_id = json.loads(body["payload"])["purchase_id"]
            self.cryptobot_invoices[inv] = {"purchase_id": purchase_id, "body": body}
            return httpx.Response(200, json={"ok": True, "result": {
                "invoice_id": inv, "status": "active",
                "mini_app_invoice_url": f"https://t.me/CryptoBot/app?startapp=invoice-{inv}",
                "web_app_invoice_url": f"https://app.cr.bot/invoices/{inv}",
                "bot_invoice_url": f"https://t.me/CryptoBot?start={inv}"}})
        return httpx.Response(404, json={"error": f"no route {request.method} {path}"})

    def shim(self) -> SimpleNamespace:
        transport = httpx.MockTransport(self.handler)

        def client(*args, **kwargs):
            kwargs.pop("transport", None)
            return httpx.AsyncClient(*args, transport=transport, **kwargs)

        return SimpleNamespace(
            AsyncClient=client, Timeout=httpx.Timeout, TimeoutException=httpx.TimeoutException,
            HTTPError=httpx.HTTPError, HTTPStatusError=httpx.HTTPStatusError,
            ConnectError=httpx.ConnectError, RequestError=httpx.RequestError,
        )

    def install(self, monkeypatch) -> "FakeProviders":
        import config
        import cryptobot_service
        import platega_service
        import wata_service
        from app.workers import wata_reconciler
        shim = self.shim()
        for mod in (platega_service, wata_service, cryptobot_service, wata_reconciler):
            monkeypatch.setattr(mod, "httpx", shim)
        monkeypatch.setattr(config, "PLATEGA_MERCHANT_ID", PLATEGA_MERCHANT)
        monkeypatch.setattr(config, "PLATEGA_SECRET", PLATEGA_SECRET)
        monkeypatch.setattr(platega_service, "PLATEGA_MERCHANT_ID", PLATEGA_MERCHANT)
        monkeypatch.setattr(platega_service, "PLATEGA_SECRET", PLATEGA_SECRET)
        monkeypatch.setattr(platega_service, "PLATEGA_API_URL", "https://app.platega.test")
        monkeypatch.setattr(config, "CRYPTOBOT_API_TOKEN", CRYPTOBOT_TOKEN)
        monkeypatch.setattr(cryptobot_service, "CRYPTOBOT_API_TOKEN", CRYPTOBOT_TOKEN)
        monkeypatch.setattr(cryptobot_service, "CRYPTOBOT_API_URL", "https://pay.crypt.test/api")
        monkeypatch.setattr(config, "WATA_ACCESS_TOKEN", WATA_TOKEN)
        monkeypatch.setattr(wata_service, "WATA_ACCESS_TOKEN", WATA_TOKEN)
        monkeypatch.setattr(wata_service, "WATA_API_URL", "https://api.wata.test/api/h2h")
        monkeypatch.setattr(wata_service, "_PINNED_PUBLIC_KEY_PEM", wata_public_pem())
        monkeypatch.setattr(wata_service, "_PUBLIC_KEY_PEM", None)
        monkeypatch.setattr(config, "TG_PROVIDER_TOKEN", "tg-provider-e2e")
        return self


__all__ = [
    "CRYPTOBOT_TOKEN", "FakeProviders", "PLATEGA_MERCHANT", "PLATEGA_SECRET", "WATA_TOKEN",
    "cryptobot_sign", "cryptobot_webhook", "platega_webhook", "wata_public_pem", "wata_sign",
    "wata_webhook",
]
