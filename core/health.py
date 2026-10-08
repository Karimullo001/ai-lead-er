from __future__ import annotations
import asyncio, logging, os, time
from typing import Any, Dict, Optional

log = logging.getLogger("agentos.health")


class Heartbeat:
    """Periodically writes a heartbeat row to PostgreSQL."""

    def __init__(self, task_manager, service_name: str, interval: float = 15.0,
                 meta: Optional[Dict[str, Any]] = None):
        self.tm = task_manager
        self.service = service_name
        self.interval = interval
        self.meta = meta or {}
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=3)
            except Exception:
                pass

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.tm.heartbeat(self.service, self.meta)
            except Exception as e:
                log.debug("Heartbeat failed: %s", e)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval)
            except asyncio.TimeoutError:
                pass


async def system_health(task_manager, model_router, redis_url: str) -> Dict[str, Any]:
    """Aggregate health for /health endpoint."""
    out: Dict[str, Any] = {"ts": time.time()}
    # DB
    try:
        await task_manager.read_heartbeats()
        out["postgres"] = True
    except Exception:
        out["postgres"] = False
    # Redis
    try:
        import redis.asyncio as aioredis
        r = aioredis.from_url(redis_url, decode_responses=True)
        await r.ping()
        await r.aclose()
        out["redis"] = True
    except Exception:
        out["redis"] = False
    # Providers
    out["providers"] = await model_router.provider_health()
    # Workers (heartbeats fresh within 60s)
    try:
        rows = await task_manager.read_heartbeats()
        now = time.time()
        out["workers"] = {
            r["service"]: (now - r["last_seen"] < 60.0)
            for r in rows if r["service"].startswith("worker:")
        }
    except Exception:
        out["workers"] = {}
    return out
