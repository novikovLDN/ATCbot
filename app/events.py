"""
In-process async event bus.

Used by the admin web dashboard to receive live updates without
polling the database. The bot publishes events from existing
handlers/services (`bus.publish({...})`) and WebSocket clients
subscribe via `bus.subscribe()` to receive an `asyncio.Queue` that
will be filled with every subsequent event.

Single process only — if/when we split bot and dashboard into two
Railway services, swap the implementation for Redis pub/sub with the
same `publish` / `subscribe` signatures. Callers don't change.

Overflow policy: per-subscriber queue is bounded (200). A slow
WebSocket client that doesn't drain its queue gets new events
dropped (logged as warning) instead of blocking the publisher.
The bot must never stall because a browser tab froze.
"""
import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

_MAX_QUEUE_SIZE = 200


class Bus:
    def __init__(self) -> None:
        self._queues: list[asyncio.Queue] = []

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=_MAX_QUEUE_SIZE)
        self._queues.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        try:
            self._queues.remove(q)
        except ValueError:
            pass

    def publish(self, event: dict[str, Any]) -> None:
        """Non-blocking fan-out. Safe to call from any sync or async context."""
        for q in list(self._queues):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning(
                    "BUS_QUEUE_FULL type=%s — dropping for slow consumer",
                    event.get("type"),
                )

    @property
    def subscriber_count(self) -> int:
        return len(self._queues)


bus = Bus()
