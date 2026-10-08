from __future__ import annotations
import asyncio, json, logging, os
from typing import Any, Dict, List, Optional

log = logging.getLogger("agentos.queue")

STREAM = "agentos:tasks"
GROUP = "agentos:workers"
DLQ = "agentos:tasks:dlq"
AGENTOS_EXECUTION_LOCK = "agentos:execution:lock"


class TaskQueue:
    """
    Redis Streams backed queue with a consumer group.
    - Survives worker restarts (messages persist until acked).
    - Uses XAUTOCLAIM to reclaim messages from dead workers.
    """

    def __init__(self, redis_url: str, consumer_name: str = "worker-1"):
        self.redis_url = redis_url
        self.consumer_name = consumer_name
        self._r = None

    async def connect(self) -> None:
        import redis.asyncio as aioredis
        self._r = aioredis.from_url(self.redis_url, decode_responses=True, socket_timeout=30, socket_connect_timeout=10, health_check_interval=30)
        try:
            await self._r.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
            log.info("Created consumer group %s on %s", GROUP, STREAM)
        except Exception as e:
            if "BUSYGROUP" not in str(e):
                raise
        log.info("TaskQueue connected to %s", self.redis_url)

    async def enqueue(self, task_id: str, extra: Optional[Dict[str, Any]] = None) -> str:
        assert self._r is not None
        msg = {"task_id": task_id}
        if extra:
            msg.update({k: json.dumps(v) if not isinstance(v, str) else v
                        for k, v in extra.items()})
        return await self._r.xadd(STREAM, msg)

    async def claim_one(self, block_ms: int = 5000) -> Optional[Dict[str, Any]]:
        assert self._r is not None
        # First, try to reclaim abandoned messages from dead workers.
        reclaimed = await self._reclaim()
        if reclaimed:
            return reclaimed
        # Then read new messages.
        res = await self._r.xreadgroup(
            GROUP, self.consumer_name, streams={STREAM: ">"},
            count=1, block=block_ms)
        if not res:
            return None
        _, entries = res[0]
        if not entries:
            return None
        entry_id, data = entries[0]
        return {"entry_id": entry_id, "data": data}

    async def _reclaim(self) -> Optional[Dict[str, Any]]:
        assert self._r is not None
        try:
            res = await self._r.xautoclaim(
                STREAM, GROUP, self.consumer_name,
                min_idle_time=60_000, start_id="0-0", count=1)
            if res and len(res) >= 2 and res[1]:
                entry_id, data = res[1][0]
                log.warning("Reclaimed abandoned message %s", entry_id)
                return {"entry_id": entry_id, "data": data, "reclaimed": True}
        except Exception as e:
            log.debug("xautoclaim failed: %s", e)
        return None

    async def acquire_execution_lock(self, owner: str, ttl_seconds: int = 1800) -> bool:
        """Global distributed execution lock: exactly one task runs at a time."""
        assert self._r is not None
        return bool(await self._r.set(
            AGENTOS_EXECUTION_LOCK, owner, nx=True, ex=max(60, int(ttl_seconds))
        ))

    async def refresh_execution_lock(self, owner: str, ttl_seconds: int = 1800) -> bool:
        assert self._r is not None
        cur = await self._r.get(AGENTOS_EXECUTION_LOCK)
        if cur != owner:
            return False
        return bool(await self._r.expire(AGENTOS_EXECUTION_LOCK, max(60, int(ttl_seconds))))

    async def release_execution_lock(self, owner: str) -> bool:
        assert self._r is not None
        # Atomic compare-and-delete via Lua.
        script = """
        if redis.call('GET', KEYS[1]) == ARGV[1] then
            return redis.call('DEL', KEYS[1])
        end
        return 0
        """
        return bool(await self._r.eval(script, 1, AGENTOS_EXECUTION_LOCK, owner))

    async def ack(self, entry_id: str) -> None:
        assert self._r is not None
        await self._r.xack(STREAM, GROUP, entry_id)

    async def to_dlq(self, entry_id: str, task_id: str, error: str) -> None:
        assert self._r is not None
        await self._r.xadd(DLQ, {"task_id": task_id, "error": error[:1000]})
        await self.ack(entry_id)

    async def close(self) -> None:
        if self._r:
            await self._r.aclose()
