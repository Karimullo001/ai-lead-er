from __future__ import annotations
import asyncio, logging, os, time
from typing import Optional
from .task_manager import TaskManager, TaskStatus
from .queue import TaskQueue

log = logging.getLogger("agentos.watchdog")


class Watchdog:
    """
    Detects and recovers:
      * stuck RUNNING tasks (updated_at too old) → re-enqueue
      * dead workers (missing heartbeats) → log (Docker restarts the container)
      * excessive retries → move to FAILED
      * provider outages → handled by ModelRouter circuit breaker
    """

    def __init__(self, task_manager: TaskManager, queue: TaskQueue,
                 stuck_after_seconds: float = 600.0, poll_seconds: float = 30.0):
        self.tm = task_manager
        self.queue = queue
        self.stuck_after = stuck_after_seconds
        self.poll = poll_seconds
        self._stop = asyncio.Event()

    async def start(self) -> None:
        log.info("Watchdog started (stuck_after=%ss, poll=%ss)",
                 self.stuck_after, self.poll)
        while not self._stop.is_set():
            try:
                await self._tick()
            except Exception:
                log.exception("Watchdog tick failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.poll)
            except asyncio.TimeoutError:
                pass

    async def stop(self) -> None:
        self._stop.set()

    async def _tick(self) -> None:
        stuck = await self.tm.stale_running_tasks(older_than_seconds=self.stuck_after)
        for t in stuck:
            tid = t["task_id"]
            rc = int(t.get("retry_count") or 0)
            log.warning("Watchdog: task %s stuck for > %ss (retries=%d)",
                        tid, self.stuck_after, rc)
            await self.tm.log_event(tid, "watchdog.stuck",
                                    {"updated_at": t["updated_at"], "retries": rc})
            if rc < int(t.get("max_retries") or 3):
                # Requeue with incremented retry count.
                await self.tm.set_status(tid, TaskStatus.WAITING_RETRY,
                                         current_step="watchdog requeue",
                                         error="watchdog: stuck")
                await self.tm.pool.execute(
                    "UPDATE tasks SET retry_count = COALESCE(retry_count,0)+1 WHERE task_id=$1",
                    tid)
                await self.queue.enqueue(tid, {"requeued_by": "watchdog"})
            else:
                await self.tm.set_status(tid, TaskStatus.FAILED,
                                         error="watchdog: max retries exceeded")

    async def health(self) -> dict:
        try:
            stuck = await self.tm.stale_running_tasks(self.stuck_after)
            return {"stuck_tasks": len(stuck)}
        except Exception as e:
            return {"error": str(e)}
