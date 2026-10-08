from __future__ import annotations
import asyncio, logging, os, signal
from .task_manager import TaskManager
from .queue import TaskQueue
from .watchdog import Watchdog
from .health import Heartbeat

log = logging.getLogger("agentos.watchdog_entry")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")


async def main():
    dsn = os.getenv("DATABASE_URL", "postgresql://agent:agentpass@postgres/agents")
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
    tm = TaskManager(dsn); await tm.connect()
    q = TaskQueue(redis_url, consumer_name="watchdog"); await q.connect()
    hb = Heartbeat(tm, "watchdog", 15.0); await hb.start()
    wd = Watchdog(tm, q,
                  stuck_after_seconds=float(os.getenv("STUCK_AFTER_SECONDS", "600")),
                  poll_seconds=float(os.getenv("WATCHDOG_POLL", "30")))
    stop = asyncio.Event()
    loop = asyncio.get_event_loop()
    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(s, stop.set)
        except NotImplementedError:
            pass
    runner = asyncio.create_task(wd.start())
    await stop.wait()
    await wd.stop()
    runner.cancel()
    await hb.stop()
    await q.close(); await tm.close()


if __name__ == "__main__":
    asyncio.run(main())
