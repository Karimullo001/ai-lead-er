from __future__ import annotations
import json, logging, os, time, uuid
from enum import Enum
from typing import Any, Dict, List, Optional
import asyncpg

log = logging.getLogger("agentos.tasks")


class TaskStatus(str, Enum):
    PENDING = "PENDING"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    WAITING_RETRY = "WAITING_RETRY"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


TERMINAL = {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}


SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
  task_id         TEXT PRIMARY KEY,
  user_id         TEXT NOT NULL,
  chat_id         TEXT,
  description     TEXT NOT NULL,
  domain          TEXT DEFAULT 'general',
  status          TEXT NOT NULL,
  priority        INT  DEFAULT 5,
  entry_agent_id  TEXT NOT NULL,
  current_step    TEXT,
  progress        REAL DEFAULT 0,
  parent_task_id  TEXT,
  retry_count     INT DEFAULT 0,
  max_retries     INT DEFAULT 3,
  budget_usd      REAL,
  cost_usd        REAL DEFAULT 0,
  deadline_ts     DOUBLE PRECISION,
  result          TEXT,
  error           TEXT,
  plan_json       JSONB,
  created_at      DOUBLE PRECISION NOT NULL,
  started_at      DOUBLE PRECISION,
  updated_at      DOUBLE PRECISION NOT NULL,
  completed_at    DOUBLE PRECISION
);
CREATE INDEX IF NOT EXISTS tasks_user_idx ON tasks(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS tasks_status_idx ON tasks(status);

CREATE TABLE IF NOT EXISTS task_checkpoints (
  checkpoint_id   TEXT PRIMARY KEY,
  task_id         TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
  version         INT NOT NULL,
  state_json      JSONB NOT NULL,
  created_at      DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS cp_task_idx ON task_checkpoints(task_id, version DESC);

CREATE TABLE IF NOT EXISTS task_events (
  event_id        TEXT PRIMARY KEY,
  task_id         TEXT NOT NULL,
  event_type      TEXT NOT NULL,
  data            JSONB NOT NULL,
  created_at      DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS te_task_idx ON task_events(task_id, created_at);

CREATE TABLE IF NOT EXISTS scheduled_jobs (
  job_id          TEXT PRIMARY KEY,
  user_id         TEXT NOT NULL,
  chat_id         TEXT,
  description     TEXT NOT NULL,
  cron            TEXT,
  run_at          DOUBLE PRECISION,
  entry_agent_id  TEXT,
  enabled         BOOLEAN DEFAULT TRUE,
  last_run        DOUBLE PRECISION,
  next_run        DOUBLE PRECISION,
  created_at      DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS user_settings (
  user_id         TEXT PRIMARY KEY,
  chat_id         TEXT,
  preferences     JSONB DEFAULT '{}'::jsonb,
  created_at      DOUBLE PRECISION NOT NULL,
  updated_at      DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
  id              BIGSERIAL PRIMARY KEY,
  user_id         TEXT,
  action          TEXT NOT NULL,
  details         JSONB,
  created_at      DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS heartbeats (
  service         TEXT PRIMARY KEY,
  last_seen       DOUBLE PRECISION NOT NULL,
  meta            JSONB
);

CREATE TABLE IF NOT EXISTS memory_items (
  memory_id       TEXT PRIMARY KEY,
  user_id         TEXT NOT NULL,
  task_id         TEXT,
  kind            TEXT NOT NULL,
  content         TEXT NOT NULL,
  importance      REAL DEFAULT 0.5,
  created_at      DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS memory_user_idx ON memory_items(user_id, created_at DESC);
"""


class TaskManager:
    def __init__(self, dsn: str):
        self.dsn = dsn
        self.pool: Optional[asyncpg.Pool] = None

    async def connect(self) -> None:
        self.pool = await asyncpg.create_pool(self.dsn, min_size=1, max_size=int(os.getenv("DB_POOL_MAX","4")))
        async with self.pool.acquire() as c:
            await c.execute(SCHEMA)
        log.info("TaskManager connected and schema applied")

    async def close(self) -> None:
        if self.pool:
            await self.pool.close()

    # ---------------- long-term memory ----------------

    async def remember(self, user_id: str, content: str, task_id: str | None = None,
                       kind: str = "task_summary", importance: float = 0.5) -> None:
        if self.pool is None or not str(content).strip():
            return
        async with self.pool.acquire() as c:
            await c.execute(
                """INSERT INTO memory_items(memory_id,user_id,task_id,kind,content,importance,created_at)
                   VALUES($1,$2,$3,$4,$5,$6,$7)""",
                str(uuid.uuid4()), str(user_id), task_id, kind,
                str(content)[:12000], max(0.0, min(1.0, float(importance))), time.time()
            )

    async def recall(self, user_id: str, query: str, limit: int = 8) -> list[dict]:
        if self.pool is None:
            return []
        words = {x for x in str(query).lower().split() if len(x) > 2}
        async with self.pool.acquire() as c:
            rows = await c.fetch(
                """SELECT memory_id,task_id,kind,content,importance,created_at
                   FROM memory_items WHERE user_id=$1
                   ORDER BY created_at DESC LIMIT 100""", str(user_id))
        scored = []
        for r in rows:
            content = str(r["content"])
            low = content.lower()
            overlap = sum(1 for word in words if word in low)
            scored.append((overlap + float(r["importance"]) * 0.25, dict(r)))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [x[1] for x in scored[:max(1, int(limit))]]

    # ---------------- tasks ----------------

    async def create_task(self, user_id: str, chat_id: Optional[str], description: str,
                          entry_agent_id: str = "coordinator-1",
                          domain: str = "general",
                          priority: int = 5,
                          budget_usd: Optional[float] = None,
                          deadline_ts: Optional[float] = None,
                          parent_task_id: Optional[str] = None,
                          task_id: Optional[str] = None) -> str:
        tid = task_id or str(uuid.uuid4())
        now = time.time()
        assert self.pool is not None
        async with self.pool.acquire() as c:
            await c.execute("""
                INSERT INTO tasks(task_id, user_id, chat_id, description, domain,
                                  status, priority, entry_agent_id, budget_usd,
                                  deadline_ts, parent_task_id, created_at, updated_at)
                VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$12)
            """, tid, str(user_id), str(chat_id) if chat_id else None,
                description, domain, TaskStatus.PENDING.value, priority,
                entry_agent_id, budget_usd, deadline_ts, parent_task_id, now)
        await self.log_event(tid, "task.created",
                             {"user_id": str(user_id), "description": description[:300]})
        return tid

    async def set_status(self, task_id: str, status: TaskStatus,
                         current_step: Optional[str] = None,
                         progress: Optional[float] = None,
                         error: Optional[str] = None,
                         result: Optional[str] = None) -> None:
        sets = ["status=$2", "updated_at=$3"]
        args: List[Any] = [task_id, status.value, time.time()]
        i = 4
        if current_step is not None:
            sets.append(f"current_step=${i}"); args.append(current_step); i += 1
        if progress is not None:
            sets.append(f"progress=${i}"); args.append(progress); i += 1
        if error is not None:
            sets.append(f"error=${i}"); args.append(error); i += 1
        if result is not None:
            sets.append(f"result=${i}"); args.append(result); i += 1
        if status == TaskStatus.RUNNING:
            sets.append("started_at=COALESCE(started_at, $3)")
        if status in TERMINAL:
            sets.append("completed_at=$3")
        sql = f"UPDATE tasks SET {', '.join(sets)} WHERE task_id=$1"
        assert self.pool is not None
        async with self.pool.acquire() as c:
            await c.execute(sql, *args)
        await self.log_event(task_id, f"task.{status.value.lower()}",
                             {"step": current_step, "progress": progress})

    async def add_task_cost(self, task_id: str, delta: float) -> None:
        assert self.pool is not None
        async with self.pool.acquire() as c:
            await c.execute(
                "UPDATE tasks SET cost_usd = COALESCE(cost_usd,0)+$2, updated_at=$3 "
                "WHERE task_id=$1", task_id, delta, time.time())

    async def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        assert self.pool is not None
        async with self.pool.acquire() as c:
            row = await c.fetchrow("SELECT * FROM tasks WHERE task_id=$1", task_id)
        return dict(row) if row else None

    async def list_tasks(self, user_id: Optional[str] = None,
                         status: Optional[str] = None, limit: int = 20) -> List[Dict[str, Any]]:
        q = "SELECT * FROM tasks WHERE 1=1"
        args: List[Any] = []
        if user_id:
            args.append(str(user_id)); q += f" AND user_id=${len(args)}"
        if status:
            args.append(status); q += f" AND status=${len(args)}"
        q += f" ORDER BY created_at DESC LIMIT {int(limit)}"
        assert self.pool is not None
        async with self.pool.acquire() as c:
            rows = await c.fetch(q, *args)
        return [dict(r) for r in rows]

    async def claim_next(self) -> Optional[Dict[str, Any]]:
        """Atomically select the highest-priority PENDING task and mark RUNNING."""
        assert self.pool is not None
        async with self.pool.acquire() as c:
            async with c.transaction():
                row = await c.fetchrow("""
                    SELECT * FROM tasks
                    WHERE status IN ($1, $2)
                    ORDER BY priority ASC, created_at ASC
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                """, TaskStatus.PENDING.value, TaskStatus.WAITING_RETRY.value)
                if not row:
                    return None
                await c.execute(
                    "UPDATE tasks SET status=$2, started_at=COALESCE(started_at,$3), "
                    "updated_at=$3 WHERE task_id=$1",
                    row["task_id"], TaskStatus.RUNNING.value, time.time())
        return dict(row)

    async def stale_running_tasks(self, older_than_seconds: float = 300) -> List[Dict[str, Any]]:
        """For watchdog: RUNNING tasks whose updated_at is older than the threshold."""
        cutoff = time.time() - older_than_seconds
        assert self.pool is not None
        async with self.pool.acquire() as c:
            rows = await c.fetch(
                "SELECT * FROM tasks WHERE status=$1 AND updated_at < $2",
                TaskStatus.RUNNING.value, cutoff)
        return [dict(r) for r in rows]

    # ---------------- checkpoints ----------------

    async def save_checkpoint(self, task_id: str, state: Dict[str, Any]) -> int:
        assert self.pool is not None
        async with self.pool.acquire() as c:
            async with c.transaction():
                v = await c.fetchval(
                    "SELECT COALESCE(MAX(version),0)+1 FROM task_checkpoints WHERE task_id=$1",
                    task_id)
                await c.execute("""
                    INSERT INTO task_checkpoints(checkpoint_id, task_id, version,
                                                 state_json, created_at)
                    VALUES($1,$2,$3,$4,$5)
                """, str(uuid.uuid4()), task_id, v, json.dumps(state), time.time())
        return int(v)

    async def latest_checkpoint(self, task_id: str) -> Optional[Dict[str, Any]]:
        assert self.pool is not None
        async with self.pool.acquire() as c:
            row = await c.fetchrow(
                "SELECT state_json FROM task_checkpoints WHERE task_id=$1 "
                "ORDER BY version DESC LIMIT 1", task_id)
        if not row:
            return None
        s = row["state_json"]
        return json.loads(s) if isinstance(s, str) else s

    # ---------------- events ----------------

    async def log_event(self, task_id: str, event_type: str,
                        data: Optional[Dict[str, Any]] = None) -> None:
        if self.pool is None:
            return
        assert self.pool is not None
        async with self.pool.acquire() as c:
            await c.execute("""
                INSERT INTO task_events(event_id, task_id, event_type, data, created_at)
                VALUES($1,$2,$3,$4,$5)
            """, str(uuid.uuid4()), task_id, event_type,
                json.dumps(data or {}), time.time())

    async def list_events(self, task_id: str, limit: int = 200) -> List[Dict[str, Any]]:
        assert self.pool is not None
        async with self.pool.acquire() as c:
            rows = await c.fetch(
                "SELECT * FROM task_events WHERE task_id=$1 ORDER BY created_at ASC LIMIT $2",
                task_id, limit)
        out = []
        for r in rows:
            d = dict(r)
            if isinstance(d.get("data"), str):
                d["data"] = json.loads(d["data"])
            out.append(d)
        return out

    # ---------------- scheduled jobs ----------------

    async def create_scheduled_job(self, user_id: str, chat_id: Optional[str],
                                   description: str, cron: Optional[str],
                                   run_at: Optional[float], entry_agent_id: str = "coordinator-1",
                                   next_run: Optional[float] = None) -> str:
        jid = str(uuid.uuid4())
        assert self.pool is not None
        async with self.pool.acquire() as c:
            await c.execute("""
                INSERT INTO scheduled_jobs(job_id, user_id, chat_id, description,
                                           cron, run_at, entry_agent_id, next_run, created_at)
                VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9)
            """, jid, str(user_id), str(chat_id) if chat_id else None,
                description, cron, run_at, entry_agent_id, next_run, time.time())
        return jid

    async def list_scheduled_jobs(self, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        assert self.pool is not None
        if user_id:
            async with self.pool.acquire() as c:
                rows = await c.fetch(
                    "SELECT * FROM scheduled_jobs WHERE user_id=$1 ORDER BY created_at DESC",
                    str(user_id))
        else:
            async with self.pool.acquire() as c:
                rows = await c.fetch("SELECT * FROM scheduled_jobs ORDER BY created_at DESC")
        return [dict(r) for r in rows]

    async def due_scheduled_jobs(self, now: Optional[float] = None) -> List[Dict[str, Any]]:
        now = now or time.time()
        assert self.pool is not None
        async with self.pool.acquire() as c:
            rows = await c.fetch(
                "SELECT * FROM scheduled_jobs WHERE enabled=TRUE AND next_run IS NOT NULL "
                "AND next_run <= $1", now)
        return [dict(r) for r in rows]

    async def update_job_next_run(self, job_id: str, next_run: Optional[float]) -> None:
        assert self.pool is not None
        async with self.pool.acquire() as c:
            await c.execute(
                "UPDATE scheduled_jobs SET last_run=$2, next_run=$3 WHERE job_id=$1",
                job_id, time.time(), next_run)

    # ---------------- audit / heartbeat ----------------

    async def audit(self, user_id: Optional[str], action: str,
                    details: Optional[Dict[str, Any]] = None) -> None:
        assert self.pool is not None
        async with self.pool.acquire() as c:
            await c.execute("""
                INSERT INTO audit_log(user_id, action, details, created_at)
                VALUES($1,$2,$3,$4)
            """, str(user_id) if user_id else None, action,
                json.dumps(details or {}), time.time())

    async def heartbeat(self, service: str, meta: Optional[Dict[str, Any]] = None) -> None:
        assert self.pool is not None
        async with self.pool.acquire() as c:
            await c.execute("""
                INSERT INTO heartbeats(service, last_seen, meta) VALUES($1,$2,$3)
                ON CONFLICT (service) DO UPDATE
                  SET last_seen=EXCLUDED.last_seen, meta=EXCLUDED.meta
            """, service, time.time(), json.dumps(meta or {}))

    async def read_heartbeats(self) -> List[Dict[str, Any]]:
        assert self.pool is not None
        async with self.pool.acquire() as c:
            rows = await c.fetch("SELECT * FROM heartbeats ORDER BY service")
        return [dict(r) for r in rows]
