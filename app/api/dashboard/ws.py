"""
WebSocket fan-out from app.events.bus.

Browser opens `wss://<host>/dashboard/ws?token=<jwt>`. We verify the
token (WebSocket frames have no headers), accept, then drain the
subscriber queue forever.

A periodic ping keeps the connection alive across NAT/proxy idle
timeouts. Disconnects (or token expiry on subsequent reconnect) are
handled by the client's auto-reconnect loop.
"""
import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.api.dashboard.auth import verify_token
from app.events import bus
from app.services import admin_auth

logger = logging.getLogger(__name__)
router = APIRouter()

_PING_INTERVAL = 25.0  # seconds


@router.websocket("/ws")
async def dashboard_ws(
    websocket: WebSocket,
    token: Optional[str] = Query(default=None),
):
    """Auth: prefer the session cookie (set by /auth/login) so the
    browser's WS handshake just works after password login. Fall
    back to ?token=<JWT> for legacy clients / curl tests."""
    authorized = False

    # 1) Cookie session
    cookie_token = websocket.cookies.get(admin_auth.COOKIE_NAME)
    if cookie_token:
        tg = await admin_auth.lookup_session(cookie_token)
        if tg is not None and admin_auth.is_admin(tg):
            authorized = True

    # 2) JWT in query (legacy / bootstrap)
    if not authorized and token:
        payload = verify_token(token)
        if payload and payload.get("role") == "admin":
            authorized = True

    if not authorized:
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
