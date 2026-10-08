from __future__ import annotations
import logging, os
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from core.model_router import ModelRouter
from core.task_manager import TaskManager, TaskStatus
from core.queue import TaskQueue
from core.cost_tracker import CostTracker
from core.health import system_health
from core.scheduler import _next_cron_ts
from core.tracing import setup_tracing

log = logging.getLogger("agentos.api")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

STATE: Dict[str, Any] = {}
HOSTED_RUNTIME: Dict[str, Any] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_tracing("agentos-api")
    dsn = os.getenv("DATABASE_URL", "postgresql://agent:agentpass@postgres/agents")
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
    tm = TaskManager(dsn); await tm.connect()
    q = TaskQueue(redis_url, consumer_name="api"); await q.connect()
    router = ModelRouter()
    cost = CostTracker(task_manager=tm)
    STATE.update(dict(tm=tm, queue=q, router=router, cost=cost, redis_url=redis_url))
    log.info("API ready with providers: %s", router.available_providers())
    yield
    await q.close(); await tm.close()
    STATE.clear()


app = FastAPI(title="AgentOS API", version="2.0.0", lifespan=lifespan)


# ---------------- schemas ----------------

class TaskCreate(BaseModel):
    description: str
    user_id: str = "api"
    chat_id: Optional[str] = None
    entry_agent_id: str = "coordinator-1"
    domain: str = "general"
    priority: int = 5
    budget_usd: Optional[float] = None
    deadline_ts: Optional[float] = None


class TaskOut(BaseModel):
    task_id: str
    user_id: str
    status: str
    description: str
    progress: float = 0.0
    result: Optional[str] = None
    error: Optional[str] = None
    cost_usd: float = 0.0


class ScheduleCreate(BaseModel):
    user_id: str
    chat_id: Optional[str] = None
    description: str
    cron: Optional[str] = None
    run_at: Optional[float] = None
    entry_agent_id: str = "coordinator-1"


# ---------------- tasks ----------------

@app.post("/tasks", response_model=TaskOut)
async def create_task(body: TaskCreate):
    tm: TaskManager = STATE["tm"]
    q: TaskQueue = STATE["queue"]
    tid = await tm.create_task(
        user_id=body.user_id, chat_id=body.chat_id, description=body.description,
        entry_agent_id=body.entry_agent_id, domain=body.domain,
        priority=body.priority, budget_usd=body.budget_usd,
        deadline_ts=body.deadline_ts)
    await q.enqueue(tid)
    await tm.set_status(tid, TaskStatus.QUEUED)
    return TaskOut(task_id=tid, user_id=body.user_id, status="QUEUED",
                   description=body.description)


@app.get("/tasks")
async def list_tasks(user_id: Optional[str] = None, status: Optional[str] = None,
                     limit: int = 20):
    tm: TaskManager = STATE["tm"]
    rows = await tm.list_tasks(user_id=user_id, status=status, limit=limit)
    return {"tasks": rows}


@app.get("/tasks/{task_id}")
async def get_task(task_id: str):
    tm: TaskManager = STATE["tm"]
    row = await tm.get_task(task_id)
    if not row:
        raise HTTPException(404, "not found")
    return row


@app.get("/tasks/{task_id}/events")
async def get_task_events(task_id: str, limit: int = 200):
    tm: TaskManager = STATE["tm"]
    return {"events": await tm.list_events(task_id, limit)}


@app.post("/tasks/{task_id}/pause")
async def pause_task(task_id: str):
    await STATE["tm"].set_status(task_id, TaskStatus.PAUSED)
    return {"status": "PAUSED"}


@app.post("/tasks/{task_id}/resume")
async def resume_task(task_id: str):
    await STATE["tm"].set_status(task_id, TaskStatus.QUEUED)
    await STATE["queue"].enqueue(task_id)
    return {"status": "QUEUED"}


@app.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: str):
    await STATE["tm"].set_status(task_id, TaskStatus.CANCELLED)
    return {"status": "CANCELLED"}


@app.post("/tasks/{task_id}/retry")
async def retry_task(task_id: str):
    tm: TaskManager = STATE["tm"]
    await tm.set_status(task_id, TaskStatus.QUEUED, error="")
    await tm.pool.execute(
        "UPDATE tasks SET retry_count = COALESCE(retry_count,0)+1 WHERE task_id=$1",
        task_id)
    await STATE["queue"].enqueue(task_id)
    return {"status": "QUEUED"}


# ---------------- scheduled jobs ----------------

@app.post("/schedule")
async def create_schedule(body: ScheduleCreate):
    tm: TaskManager = STATE["tm"]
    next_run = body.run_at
    if body.cron:
        next_run = _next_cron_ts(body.cron)
    jid = await tm.create_scheduled_job(
        user_id=body.user_id, chat_id=body.chat_id,
        description=body.description, cron=body.cron, run_at=body.run_at,
        entry_agent_id=body.entry_agent_id, next_run=next_run)
    return {"job_id": jid, "next_run": next_run}


@app.get("/schedule")
async def list_schedules(user_id: Optional[str] = None):
    return {"jobs": await STATE["tm"].list_scheduled_jobs(user_id=user_id)}


# ---------------- health / metrics ----------------

@app.get("/health")
async def health():
    return await system_health(STATE["tm"], STATE["router"], STATE["redis_url"])


@app.get("/metrics")
async def metrics():
    tm: TaskManager = STATE["tm"]
    all_tasks = await tm.list_tasks(limit=1000)
    by_status: Dict[str, int] = {}
    for t in all_tasks:
        by_status[t["status"]] = by_status.get(t["status"], 0) + 1
    cost = STATE["cost"].summary()
    return {"tasks_by_status": by_status,
            "providers": STATE["router"].available_providers(),
            "cost": cost}


# ---------------- telegram webhook (optional) ----------------

@app.get("/healthz")
async def agentos_healthz():
    return {"ok": True, "service": "agentos", "runtime": "embedded"}

# Telegram webhook endpoint. Authentication is performed by hosted_runtime
# using a token-derived secret header; invalid requests are rejected.
from fastapi import Request
from fastapi.responses import JSONResponse

@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    secret = request.headers.get("x-telegram-bot-api-secret-token")
    payload = await request.json()
    from hosted_runtime import handle_telegram_webhook
    ok = await handle_telegram_webhook(payload, secret)
    if not ok:
        return JSONResponse({"ok": False}, status_code=403)
    return {"ok": True}

@app.get("/healthz")
async def production_healthz():
    tm = STATE.get("tm")
    router = STATE.get("router")
    data = {"ok": bool(tm and getattr(tm, "pool", None)),
            "service": "agentos", "runtime": "embedded"}
    try:
        data["providers"] = router.available_providers() if router else []
    except Exception:
        data["providers"] = []
    try:
        rows = await tm.read_heartbeats() if tm else []
        data["heartbeats"] = {r["service"]: r["last_seen"] for r in rows}
    except Exception:
        data["heartbeats"] = {}
    return data

# Final Telegram webhook route replacement: remove every previous route with
# the same path so a legacy 501 handler cannot shadow the real handler.
try:
    _telegram_webhook_routes = [r for r in list(app.router.routes) if getattr(r, "path", None) == "/telegram/webhook"]
    for _route in _telegram_webhook_routes:
        try:
            app.router.routes.remove(_route)
        except ValueError:
            pass
    app.add_api_route("/telegram/webhook", telegram_webhook, methods=["POST"])
except Exception:
    pass
