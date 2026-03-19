"""
Worker health monitor: tracks background task heartbeats, detects crashes, triggers auto-restart.

Each worker calls heartbeat() on every iteration.
The monitor periodically checks for stale workers and alerts admin / triggers restart.

Usage in workers:
    from app.core.worker_monitor import worker_heartbeat, worker_error

    async def my_worker(bot):
        while True:
            try:
                # ... do work ...
                worker_heartbeat("my_worker")
            except Exception as e:
                worker_error("my_worker", e)
            await asyncio.sleep(60)

Usage in main.py:
    from app.core.worker_monitor import WorkerSupervisor
    supervisor = WorkerSupervisor(bot)
    supervisor.register("reminders", reminders.reminders_task, bot)
    await supervisor.start_all()
"""
import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Coroutine, Any, Dict, Optional, List

from app.core.metrics import get_metrics

logger = logging.getLogger(__name__)


@dataclass
class WorkerInfo:
    """Runtime info about a supervised worker."""
    name: str
    factory: Callable[..., Coroutine]
    args: tuple = ()
    task: Optional[asyncio.Task] = None
    restart_count: int = 0
    max_restarts: int = 10
    last_start_time: float = 0.0
    stale_threshold_s: float = 1800.0  # 30 min without heartbeat = stale


def worker_heartbeat(name: str) -> None:
    """Called by worker on successful iteration."""
    m = get_metrics()
    m.worker_iterations[name].inc()
    m.worker_last_success[name] = time.monotonic()


def worker_error(name: str, error: Exception) -> None:
    """Called by worker on failed iteration."""
    m = get_metrics()
    m.worker_errors[name].inc()
    m.worker_last_error[name] = time.monotonic()
    m.worker_last_error_msg[name] = f"{type(error).__name__}: {str(error)[:200]}"
    m.errors.record(type(error).__name__, str(error)[:200], component=f"worker:{name}")


def worker_iteration_time(name: str, duration_s: float) -> None:
    """Record worker iteration duration."""
    m = get_metrics()
    m.worker_iteration_latency[name].observe(duration_s)


class WorkerSupervisor:
    """
    Supervises background workers: auto-restart on crash, admin alerts, metrics.

    Key behaviors:
    - Monitors task.done() every check_interval_s
    - On crash: logs, alerts admin, restarts (up to max_restarts)
    - On stale (no heartbeat): alerts admin
    - Provides status for admin dashboard
    """

    def __init__(self, bot, check_interval_s: float = 30.0):
        self._bot = bot
        self._check_interval_s = check_interval_s
        self._workers: Dict[str, WorkerInfo] = {}
        self._monitor_task: Optional[asyncio.Task] = None
        self._running = False

    def register(
        self,
        name: str,
        factory: Callable[..., Coroutine],
        *args,
        max_restarts: int = 10,
        stale_threshold_s: float = 1800.0,
    ) -> None:
        """Register a worker for supervision."""
        self._workers[name] = WorkerInfo(
            name=name,
            factory=factory,
            args=args,
            max_restarts=max_restarts,
            stale_threshold_s=stale_threshold_s,
        )

    async def start_all(self) -> List[asyncio.Task]:
        """Start all registered workers and the monitor loop."""
        tasks = []
        for name, info in self._workers.items():
            task = await self._start_worker(info)
            if task:
                tasks.append(task)

        self._running = True
        self._monitor_task = asyncio.create_task(
            self._monitor_loop(), name="worker_supervisor"
        )
        tasks.append(self._monitor_task)

        logger.info(
            "WORKER_SUPERVISOR started workers=%s",
            list(self._workers.keys()),
        )
        return tasks

    async def _start_worker(self, info: WorkerInfo) -> Optional[asyncio.Task]:
        """Start a single worker task."""
        try:
            task = asyncio.create_task(
                self._wrapped_worker(info),
                name=f"worker:{info.name}",
            )
            info.task = task
            info.last_start_time = time.monotonic()
            logger.info("WORKER_STARTED name=%s restarts=%d", info.name, info.restart_count)
            return task
        except Exception as e:
            logger.error("WORKER_START_FAILED name=%s error=%s", info.name, e)
            return None

    async def _wrapped_worker(self, info: WorkerInfo) -> None:
        """Wrapper that catches worker crash and records metrics."""
        try:
            await info.factory(*info.args)
        except asyncio.CancelledError:
            logger.info("WORKER_CANCELLED name=%s", info.name)
            raise
        except Exception as e:
            logger.exception("WORKER_CRASHED name=%s error=%s", info.name, e)
            worker_error(info.name, e)
            # Don't re-raise — let monitor detect done() and restart

    async def _monitor_loop(self) -> None:
        """Periodic check for crashed/stale workers."""
        await asyncio.sleep(10)  # initial delay

        while self._running:
            try:
                await self._check_workers()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("WORKER_MONITOR_ERROR error=%s", e)

            await asyncio.sleep(self._check_interval_s)

    async def _check_workers(self) -> None:
        """Check all workers, restart crashed ones, alert on stale."""
        now = time.monotonic()
        m = get_metrics()

        for name, info in self._workers.items():
            if info.task is None:
                continue

            # Check if task crashed (done but not cancelled)
            if info.task.done():
                exc = info.task.exception() if not info.task.cancelled() else None

                if info.restart_count >= info.max_restarts:
                    logger.critical(
                        "WORKER_MAX_RESTARTS name=%s restarts=%d — GIVING UP",
                        name, info.restart_count,
                    )
                    await self._alert_admin(
                        f"WORKER PERMANENTLY DEAD: {name}\n"
                        f"Restarts: {info.restart_count}/{info.max_restarts}\n"
                        f"Last error: {exc}\n"
                        f"Manual intervention required!"
                    )
                    continue

                # Restart
                info.restart_count += 1
                error_msg = str(exc)[:200] if exc else "unknown"
                logger.warning(
                    "WORKER_RESTARTING name=%s restart=%d/%d last_error=%s",
                    name, info.restart_count, info.max_restarts, error_msg,
                )

                await self._alert_admin(
                    f"Worker crashed and restarting: {name}\n"
                    f"Restart: {info.restart_count}/{info.max_restarts}\n"
                    f"Error: {error_msg}"
                )

                # Backoff: wait longer for repeated restarts
                backoff = min(2 ** info.restart_count, 60)
                await asyncio.sleep(backoff)

                await self._start_worker(info)
                continue

            # Check for stale worker (no heartbeat for too long)
            last_ok = m.worker_last_success.get(name)
            if last_ok is not None:
                since = now - last_ok
                if since > info.stale_threshold_s:
                    logger.warning(
                        "WORKER_STALE name=%s since_last_ok=%ds threshold=%ds",
                        name, int(since), int(info.stale_threshold_s),
                    )
                    await self._alert_admin(
                        f"Worker stale (no heartbeat): {name}\n"
                        f"Last OK: {int(since)}s ago (threshold: {int(info.stale_threshold_s)}s)\n"
                        f"Worker may be stuck!"
                    )

    async def _alert_admin(self, message: str) -> None:
        """Send alert to admin via Telegram."""
        try:
            from app.services.admin_alerts import send_alert
            await send_alert(self._bot, "worker", message)
        except Exception as e:
            logger.error("SUPERVISOR_ALERT_FAILED error=%s", e)

    def get_all_tasks(self) -> List[asyncio.Task]:
        """Get all managed tasks (for shutdown)."""
        tasks = []
        for info in self._workers.values():
            if info.task and not info.task.done():
                tasks.append(info.task)
        if self._monitor_task and not self._monitor_task.done():
            tasks.append(self._monitor_task)
        return tasks

    async def stop_all(self) -> None:
        """Cancel all workers and monitor."""
        self._running = False
        tasks = self.get_all_tasks()
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        logger.info("WORKER_SUPERVISOR stopped all workers")

    def status_summary(self) -> Dict[str, dict]:
        """Worker status for admin dashboard."""
        result = {}
        now = time.monotonic()
        m = get_metrics()

        for name, info in self._workers.items():
            alive = info.task is not None and not info.task.done()
            last_ok = m.worker_last_success.get(name)
            last_err_time = m.worker_last_error.get(name)
            iterations = m.worker_iterations[name].value
            errors = m.worker_errors[name].value

            if alive and last_ok and (now - last_ok < info.stale_threshold_s):
                health = "healthy"
            elif alive and iterations == 0:
                health = "starting"
            elif alive and last_ok and (now - last_ok >= info.stale_threshold_s):
                health = "stale"
            elif alive:
                health = "running"
            else:
                health = "dead"

            result[name] = {
                "alive": alive,
                "health": health,
                "restarts": info.restart_count,
                "iterations": iterations,
                "errors": errors,
                "uptime_s": int(now - info.last_start_time) if info.last_start_time else 0,
                "since_last_ok_s": int(now - last_ok) if last_ok else None,
                "last_error": m.worker_last_error_msg.get(name),
            }
        return result
