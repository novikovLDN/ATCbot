"""
Shared httpx.AsyncClient instances with connection pooling.

Creating a new httpx.AsyncClient per request forces a fresh TCP+TLS
handshake on every external API call (50-300ms each). Reusing a single
AsyncClient lets httpx pool keep-alive connections so subsequent calls
to the same host are near-instant.

Each "name" maps to one long-lived AsyncClient. Clients are created
lazily on first use and reused for the lifetime of the process. They
are closed on shutdown via close_all().
"""
import logging
from typing import Dict, Optional

import httpx

logger = logging.getLogger(__name__)

_clients: Dict[str, httpx.AsyncClient] = {}

# Default connection pool sizing. The bot does not need huge concurrency
# per upstream — a few dozen keep-alive connections are plenty.
_DEFAULT_LIMITS = httpx.Limits(
    max_connections=50,
    max_keepalive_connections=20,
    keepalive_expiry=30.0,
)


def get_client(
    name: str,
    timeout: Optional[httpx.Timeout] = None,
    limits: Optional[httpx.Limits] = None,
) -> httpx.AsyncClient:
    """
    Return the shared AsyncClient for `name`, creating it on first call.

    The returned client MUST NOT be used as a context manager — it is
    shared across requests. Just call methods on it directly:

        client = get_client("vpn", timeout=...)
        response = await client.post(url, json=body)
    """
    existing = _clients.get(name)
    if existing is not None and not existing.is_closed:
        return existing

    if timeout is None:
        timeout = httpx.Timeout(connect=5.0, read=15.0, write=5.0, pool=5.0)
    client = httpx.AsyncClient(timeout=timeout, limits=limits or _DEFAULT_LIMITS)
    _clients[name] = client
    logger.info("HTTP_CLIENT_CREATED name=%s", name)
    return client


class _SharedClientCM:
    """Async ctx manager that yields a shared client WITHOUT closing it.

    Lets call sites keep the `async with ... as client:` shape while reusing
    a long-lived pooled client. Closing is handled centrally by close_all().
    """

    __slots__ = ("_client",)

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def __aenter__(self) -> httpx.AsyncClient:
        return self._client

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


def shared(
    name: str,
    timeout: Optional[httpx.Timeout] = None,
    limits: Optional[httpx.Limits] = None,
) -> _SharedClientCM:
    """Drop-in replacement for `httpx.AsyncClient(...)` as a context manager.

    Use:
        async with http_client.shared("vpn", VPN_HTTP_TIMEOUT) as client:
            response = await client.post(url, ...)

    The underlying client is shared and pooled across calls.
    """
    return _SharedClientCM(get_client(name, timeout=timeout, limits=limits))


async def close_all() -> None:
    """Close all shared clients. Call once during graceful shutdown."""
    for name, client in list(_clients.items()):
        try:
            await client.aclose()
            logger.info("HTTP_CLIENT_CLOSED name=%s", name)
        except Exception as e:
            logger.warning("HTTP_CLIENT_CLOSE_FAILED name=%s err=%s", name, e)
    _clients.clear()
