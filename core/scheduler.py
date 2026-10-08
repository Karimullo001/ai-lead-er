from __future__ import annotations
import asyncio, logging, os, time
from datetime import datetime, timezone
from typing import Optional
from croniter import croniter
from .task_manager import TaskManager
from .queue import TaskQueue

log = logging.getLogger("agentos.scheduler")


def _next_cron_ts(cron: str, base: Optional[float] = None) -> float:
    base_dt = datetime.fromtimestamp(base or time.time(), tz=timezone.utc)
    it = croniter(cron, base_dt)
    return it.get_next(datetime).timestamp()


class Scheduler:
    """
    Polls PostgreSQL for due scheduled jobs every N seconds and enqueues tasks.
    Persistent across restarts because jobs live in `scheduled_jobs`.
    """

    def __init__(self, task_manager: TaskManager, queue: "TaskQueue",
                 poll_seconds: float = 15.0):
        self.tm = task_manager
        self.queue = queue
        self.poll_seconds = poll_seconds
        self._stop = asyncio.Event()

    async def start(self) -> None:
        log.info("Scheduler started (poll=%ss)", self.poll_seconds)
        while not self._stop.is_set():
            try:
                await self._tick()
            except Exception:
                log.exception("Scheduler tick failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.poll_seconds)
            except asyncio.TimeoutError:
                pass

    async def stop(self) -> None:
        self._stop.set()

    async def _tick(self) -> None:
        due = await self.tm.due_scheduled_jobs()
        for job in due:
            try:
                tid = await self.tm.create_task(
                    user_id=job["user_id"], chat_id=job["chat_id"],
                    description=job["description"],
                    entry_agent_id=job.get("entry_agent_id") or "coordinator-1",
                    domain="scheduled")
                await self.queue.enqueue(tid)
                next_run: Optional[float] = None
                if job.get("cron"):
                    next_run = _next_cron_ts(job["cron"])
                await self.tm.update_job_next_run(job["job_id"], next_run)
                await self.tm.log_event(tid, "scheduler.fired",
                                        {"job_id": job["job_id"]})
                log.info("Scheduled job %s fired → task %s", job["job_id"], tid)
            except Exception:
                log.exception("Failed to fire scheduled job %s", job.get("job_id"))
