"""
Production-safe pool instrumentation: measure acquire wait time and log if high.

Toggle via env: POOL_MONITOR_ENABLED=true (default: disabled).
When disabled, acquire_connection(pool, label) behaves exactly like pool.acquire().
"""
import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

_POOL_MONITOR_ENABLED = os.getenv("POOL_MONITOR_ENABLED", "").strip().lower() in ("true", "1", "yes")

# Здесь жила пара «_last_pool_wait_spike_monotonic + get_last_pool_wait_spike_monotonic»:
# модульная переменная со временем последнего всплеска ожидания пула и геттер
# к ней «для вотчдога». Вотчдог её никогда не читал — геттер не звали нигде,
# то есть переменная только писалась. Удалено целиком: наблюдаемость всплесков
# даёт логирование ниже (WARNING > 1 с, CRITICAL > 5 с), а мёртвый геттер
# выглядел готовой точкой интеграции для мониторинга, которой на деле нет.
# Понадобится машинно-читаемый сигнал — заводить метрику, а не глобал.


def _is_enabled() -> bool:
    return _POOL_MONITOR_ENABLED


class _MonitoredAcquireContextManager:
    """Async context manager that times pool.acquire() and logs if wait > 1s or > 5s."""

    __slots__ = ("pool", "label", "_conn")

    def __init__(self, pool: Any, label: str) -> None:
        self.pool = pool
        self.label = label
        self._conn = None

    async def __aenter__(self) -> Any:
        start = time.monotonic()
        self._conn = await self.pool.acquire()
        wait_s = time.monotonic() - start
        if wait_s > 5.0:
            logger.critical(
                "pool_acquire_wait_critical label=%s wait_s=%.2f",
                self.label,
                wait_s,
                extra={"pool_monitor": True, "wait_s": wait_s, "label": self.label},
            )
        elif wait_s > 1.0:
            logger.warning(
                "pool_acquire_wait_high label=%s wait_s=%.2f",
                self.label,
                wait_s,
                extra={"pool_monitor": True, "wait_s": wait_s, "label": self.label},
            )
        return self._conn

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        try:
            await self.pool.release(self._conn)
        except Exception as e:
            logger.error("pool_monitor: failed to release connection for '%s': %s", self.label, e)
        finally:
            self._conn = None


def acquire_connection(pool: Any, label: str = "") -> Any:
    """
    Return an async context manager for acquiring a connection from the pool.

    When POOL_MONITOR_ENABLED is true, measures wait time and logs:
    - WARNING if wait > 1.0s
    - CRITICAL if wait > 5.0s

    When POOL_MONITOR_ENABLED is false, behaves exactly like pool.acquire().

    Usage:
        async with acquire_connection(pool, "fast_expiry") as conn:
            ...
    """
    if not _is_enabled():
        return pool.acquire()
    return _MonitoredAcquireContextManager(pool, label or "unknown")
