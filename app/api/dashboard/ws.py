"""
WebSocket fan-out from app.events.bus.

Auth: the session cookie only. The browser sends it with the same-origin
handshake automatically; there is no token in the query string (URLs end
up in proxy and access logs). The Origin header is checked to stop
cross-site WebSocket hijacking.

A periodic ping keeps the connection alive across NAT/proxy idle
timeouts. Disconnects are handled by the client's reconnect loop.
"""
import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.api.dashboard.security import check_ws_origin
from app.events import bus
from app.services import admin_auth

logger = logging.getLogger(__name__)
router = APIRouter()

_PING_INTERVAL = 25.0  # seconds


async def _authorized(websocket: WebSocket) -> bool:
    if not check_ws_origin(websocket.headers.get("origin"), websocket.headers.get("host")):
        return False
    cookie_token = websocket.cookies.get(admin_auth.COOKIE_NAME)
    if not cookie_token:
        return False
    tg = await admin_auth.lookup_session(cookie_token)
    return tg is not None and admin_auth.is_admin(tg)


@router.websocket("/ws")
async def dashboard_ws(websocket: WebSocket):
    if not await _authorized(websocket):
        await websocket.close(code=4001)
        return

    await websocket.accept()
    q = bus.subscribe()
    pinger: asyncio.Task | None = None

    async def _ping_loop():
        while True:
            await asyncio.sleep(_PING_INTERVAL)
            try:
                await websocket.send_json({"type": "ping"})
            except Exception:
                return

    try:
        pinger = asyncio.create_task(_ping_loop())
        while True:
            event = await q.get()
            await websocket.send_json(event)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.warning("DASHBOARD_WS_ERROR: %s", e)
    finally:
        bus.unsubscribe(q)
        if pinger is not None:
            pinger.cancel()
