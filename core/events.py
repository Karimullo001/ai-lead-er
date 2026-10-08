from __future__ import annotations
import json, logging, time, uuid
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

log = logging.getLogger("agentos.events")


class Event(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    stream_id: str = "global"
    type: str
    data: Dict[str, Any] = Field(default_factory=dict)
    timestamp: float = Field(default_factory=time.time)
    version: int = 1


class EventStore:
    def __init__(self, dsn: Optional[str] = None):
        self.dsn = dsn
        self.pool = None
        self._mem: List[Event] = []
        self._versions: Dict[str, int] = {}

    async def connect(self) -> None:
        if not self.dsn:
            return
        try:
            import asyncpg  # type: ignore
            self.pool = await asyncpg.create_pool(self.dsn)
            await self.pool.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    stream_id TEXT NOT NULL,
                    type TEXT NOT NULL,
                    data JSONB NOT NULL,
                    timestamp DOUBLE PRECISION NOT NULL,
                    version INT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_events_stream ON events(stream_id, version);
            """)
            log.info("EventStore connected to Postgres")
        except Exception as e:
            log.warning("Postgres unavailable, using in-memory event store: %s", e)
            self.pool = None

    async def append(self, event: Event) -> Event:
        v = self._versions.get(event.stream_id, 0) + 1
        self._versions[event.stream_id] = v
        event.version = v
        self._mem.append(event)
        if self.pool is not None:
            try:
                await self.pool.execute(
                    "INSERT INTO events(event_id, stream_id, type, data, timestamp, version) "
                    "VALUES($1,$2,$3,$4,$5,$6)",
                    event.event_id, event.stream_id, event.type,
                    json.dumps(event.data), event.timestamp, event.version)
            except Exception as e:
                log.error("EventStore append failed: %s", e)
        return event

    async def get_events(self, stream_id: str, after_version: int = 0) -> List[Event]:
        if self.pool is not None:
            try:
                rows = await self.pool.fetch(
                    "SELECT event_id, stream_id, type, data, timestamp, version "
                    "FROM events WHERE stream_id=$1 AND version>$2 ORDER BY version",
                    stream_id, after_version)
                return [Event(event_id=r["event_id"], stream_id=r["stream_id"],
                              type=r["type"],
                              data=json.loads(r["data"]) if isinstance(r["data"], str) else r["data"],
                              timestamp=r["timestamp"], version=r["version"])
                        for r in rows]
            except Exception as e:
                log.error("EventStore read failed: %s", e)
        return [e for e in self._mem if e.stream_id == stream_id and e.version > after_version]
