"""Passkey challenges in Redis: one-shot pop via GET+DEL in MULTI/EXEC.

GETDEL needs Redis >= 6.2; the pipeline works on any version and stays atomic.
"""
from __future__ import annotations

from app.services import admin_passkeys as ap


class _Pipe:
    def __init__(self, store):
        self.store, self.ops = store, []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    def get(self, k):
        self.ops.append(("get", k))

    def delete(self, k):
        self.ops.append(("del", k))

    async def execute(self):
        out = []
        for op, k in self.ops:
            out.append(self.store.get(k) if op == "get" else int(self.store.pop(k, None) is not None))
        return out


class FakeRedis:
    """No getdel attribute on purpose: behaves like a client talking to Redis < 6.2."""

    def __init__(self):
        self.store = {}

    async def set(self, k, v, ex=None):
        self.store[k] = v.encode() if isinstance(v, str) else v

    def pipeline(self, transaction=True):
        assert transaction is True
        return _Pipe(self.store)


async def test_challenge_is_one_shot_without_getdel(monkeypatch):
    r = FakeRedis()

    async def _redis():
        return r
    monkeypatch.setattr(ap, "_redis", _redis)

    token = await ap.store_challenge(b"\x01\x02chal", "auth")
    assert await ap.pop_challenge(token, "auth") == b"\x01\x02chal"
    assert await ap.pop_challenge(token, "auth") is None      # consumed
    assert r.store == {}


async def test_wrong_kind_is_rejected_and_consumed(monkeypatch):
    r = FakeRedis()

    async def _redis():
        return r
    monkeypatch.setattr(ap, "_redis", _redis)

    token = await ap.store_challenge(b"chal", "register")
    assert await ap.pop_challenge(token, "auth") is None
    assert r.store == {}
