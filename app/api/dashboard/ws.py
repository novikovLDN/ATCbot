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

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.api.dashboard.auth import verify_token
from app.events import bus

logger = logging.getLogger(__name__)
router = APIRouter()

_PING_INTERVAL = 25.0  # seconds


@router.websocket("/ws")
async def dashboard_ws(websocket: WebSocket, token: str = Query(...)):
    payload = verify_token(token)
    if not payload or payload.get("role") != "admin":
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
