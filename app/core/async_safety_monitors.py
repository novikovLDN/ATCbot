"""
Temporary async safety instrumentation.
Remove after root cause of bot unresponsiveness is fixed.

DO NOT change business logic.
Adds: event loop lag monitor, optional CPU monitor.
"""
import asyncio
import logging
import time

logger = logging.getLogger(__name__)


async def monitor_event_loop_lag():
    """Detect event loop starvation. Logs when lag > 0.2s."""
    loop = asyncio.get_running_loop()
    last = loop.time()
    while True:
        await asyncio.sleep(1)
        now = loop.time()
        lag = now - last - 1
        if lag > 0.2:
            logger.warning("[EVENT_LOOP_LAG] lag=%.3fs", lag)
        last = now


async def monitor_cpu():
    """Optional CPU monitor. Logs when CPU > 80%. Requires psutil."""
    try:
        import psutil
        import os
    except ImportError:
        logger.debug("[ASYNC_SAFETY] CPU monitor skipped (psutil not installed)")
        return
    process = psutil.Process(os.getpid())
    while True:
        await asyncio.sleep(5)
        try:
            cpu = process.cpu_percent(interval=None)
            if cpu > 80:
                logger.warning("[HIGH_CPU] cpu=%.1f%%", cpu)
        except Exception as e:
            logger.debug("[ASYNC_SAFETY] CPU monitor error: %s", e)
