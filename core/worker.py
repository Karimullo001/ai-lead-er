from __future__ import annotations
import json, uuid
from core.builtin_tools import register_builtins
import asyncio, logging, os, signal, sys, time
from typing import Any, Dict, Optional

from .approval import ApprovalGate, ApprovalRequest
from .comm_bus import CommunicationBus
from .events import EventStore
from .factory import AgentFactory
from .llm import LLMClient
from .models import Task, TaskResult
from .orchestrator import Orchestrator
from .reliability import ReliabilityEngine
from .task_manager import TaskManager, TaskStatus
from .queue import TaskQueue
from .cost_tracker import CostTracker, CostEntry
from .model_router import ModelRouter
from .tools import Sandbox, ToolRegistry
from .health import Heartbeat
from .conversation_memory import ConversationMemory

log = logging.getLogger("agentos.worker")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")


class Worker:
    def __init__(self, name: str):
        self.name = name
        self.stop = asyncio.Event()
        self.tm: Optional[TaskManager] = None
        self.queue: Optional[TaskQueue] = None
        self.orch: Optional[Orchestrator] = None
        self.cost: Optional[CostTracker] = None
        self.hb: Optional[Heartbeat] = None
        self.conv=None

    async def setup(self) -> None:
        from demo.agents import build_demo_specs, register_demo_tools
        dsn = os.getenv("DATABASE_URL", "postgresql://agent:agentpass@postgres/agents")
        redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")

        self.tm = TaskManager(dsn)
        await self.tm.connect()
        self.conv=ConversationMemory(self.tm.pool)
        await self.conv.ensure()
        self.queue = TaskQueue(redis_url, consumer_name=self.name)
        await self.queue.connect()
        self.cost = CostTracker(task_manager=self.tm)
        self.hb = Heartbeat(self.tm, service_name=f"worker:{self.name}", interval=15.0)
        await self.hb.start()

        tools = ToolRegistry()
        register_demo_tools(tools)

        events = EventStore(dsn=dsn)
        await events.connect()
        comm = CommunicationBus()
        router = ModelRouter()
        llm = LLMClient(router=router)
        approval = ApprovalGate(
            auto_approve_in_dev=os.getenv("AUTO_APPROVE", "false").lower() == "true")
        factory = AgentFactory(
            llm=llm, tools=tools, event_store=events, comm_bus=comm,
            reliability=ReliabilityEngine(), approval_gate=approval,
            sandbox=Sandbox(prefer_docker=os.getenv("SANDBOX_MODE", "docker") == "docker"),
            memory_persist_dir=os.getenv("CHROMA_DIR"))
        self.orch = Orchestrator(events, comm)
        for spec in build_demo_specs():
            self.orch.register_agent(factory.build(spec))
        log.info("Worker %s ready with %d agents", self.name, len(self.orch.agents))

    async def loop(self) -> None:
        assert self.queue and self.tm and self.orch
        while not self.stop.is_set():
            owner = f"{self.name}:{uuid.uuid4().hex}"
            locked = False
            refresh_task = None
            entry_id = None
            task_id = None
            try:
                # Lock BEFORE claim: only one worker can consume the next FIFO
                # entry at a time.
                while not self.stop.is_set():
                    locked = await self.queue.acquire_execution_lock(owner)
                    if locked:
                        break
                    await asyncio.sleep(0.5)
                if not locked:
                    continue

                msg = await self.queue.claim_one(block_ms=3000)
                if not msg:
                    await self.queue.release_execution_lock(owner)
                    locked = False
                    continue

                entry_id = msg["entry_id"]
                task_id = msg["data"].get("task_id")
                if not task_id:
                    await self.queue.to_dlq(entry_id, "?", "missing task_id")
                    continue

                try:
                    _age_h = (time.time() * 1000 - int(str(entry_id).split("-")[0])) / 3_600_000
                except Exception:
                    _age_h = 0
                if _age_h > 12:
                    log.warning("Dropping stale queue entry %s (%.1fh old)", entry_id, _age_h)
                    await self.queue.to_dlq(entry_id, task_id, "stale queue entry (>12h)")
                    continue

                async def _refresh_lock():
                    while True:
                        await asyncio.sleep(60)
                        if not await self.queue.refresh_execution_lock(owner, ttl_seconds=1800):
                            log.warning("Execution lock refresh lost for %s", owner)
                            return

                refresh_task = asyncio.create_task(_refresh_lock())
                await asyncio.wait_for(
                    self._run_task(task_id),
                    timeout=float(os.getenv("TASK_TIMEOUT_SECONDS", "900")))
                await self.queue.ack(entry_id)
            except Exception as e:
                log.exception("Task %s crashed", task_id or "?")
                if not task_id:
                    await asyncio.sleep(1)
                if task_id:
                    await self.tm.set_status(task_id, TaskStatus.FAILED, error=str(e))
                    if entry_id:
                        await self.queue.to_dlq(entry_id, task_id, str(e))
            finally:
                if refresh_task:
                    refresh_task.cancel()
                    try:
                        await refresh_task
                    except asyncio.CancelledError:
                        pass
                if locked:
                    try:
                        await self.queue.release_execution_lock(owner)
                    except Exception:
                        log.exception("Failed to release execution lock")

    async def _run_task(self, task_id: str) -> None:
        assert self.tm and self.orch
        row = await self.tm.get_task(task_id)
        if not row:
            log.warning("Task %s not found in DB", task_id)
            return
        if row["status"] in (TaskStatus.COMPLETED.value, TaskStatus.CANCELLED.value):
            return
        if row["status"] == TaskStatus.PAUSED.value:
            log.info("Task %s is paused; skipping", task_id)
            return


    async def _monitor_task_progress(self, task_id: str) -> None:
        seen = 0
        step_ok = 0
        plan_steps = 0
        while True:
            try:
                evs = await self.events.get_events("task:" + task_id, after_version=seen)
                if evs:
                    seen = evs[-1].version
                for ev in evs:
                    et = ev.type
                    data = ev.data or {}
                    if et == "kernel.started":
                        await self.tm.set_status(task_id, TaskStatus.RUNNING, current_step="starting", progress=0.05)
                    elif et == "kernel.perceived":
                        await self.tm.set_status(task_id, TaskStatus.RUNNING, current_step="understanding", progress=0.12)
                    elif et == "kernel.planned":
                        plan_steps = max(plan_steps, len(data.get("steps") or []))
                        await self.tm.set_status(task_id, TaskStatus.RUNNING, current_step="planning", progress=0.20)
                    elif et == "kernel.step_ok":
                        step_ok += 1
                        denom = max(plan_steps, step_ok + 1)
                        progress = min(0.88, 0.20 + 0.65 * (step_ok / denom))
                        await self.tm.set_status(task_id, TaskStatus.RUNNING, current_step=str(data.get("step") or "executing"), progress=progress)
                    elif et == "kernel.reflection":
                        await self.tm.set_status(task_id, TaskStatus.RUNNING, current_step="self-correcting", progress=0.72)
                    elif et == "kernel.handoff":
                        await self.tm.set_status(task_id, TaskStatus.RUNNING, current_step="delegating", progress=0.55)
                    elif et == "kernel.completed":
                        await self.tm.set_status(task_id, TaskStatus.RUNNING, current_step="finalizing", progress=0.95)
            except Exception as exc:
                log.debug("progress monitor: %s", exc)
            row = await self.tm.get_task(task_id)
            if row and row.get("status") in ("COMPLETED", "FAILED", "CANCELLED"):
                return
            await asyncio.sleep(1)

        await self.tm.set_status(task_id, TaskStatus.RUNNING, current_step="starting", progress=0.05)
        await self.tm.log_event(task_id, "worker.claimed", {"worker": self.name})

        original_description = row["description"]
        description=row["description"]
        if self.conv and row.get("chat_id") and row.get("user_id"):
            try:
                h=await self.conv.render(row["user_id"],row["chat_id"])
                if h: description="Past conversation:\n"+h+"\n\nTask:\n"+description
            except Exception: pass
        task = Task(id=task_id, description=description,
                    domain=row.get("domain") or "general")
        entry_agent = row.get("entry_agent_id") or "coordinator-1"

        # Load latest checkpoint if any (for resume).
        cp = await self.tm.latest_checkpoint(task_id)
        if cp:
            await self.tm.log_event(task_id, "worker.resumed",
                                    {"from_checkpoint": True,
                                     "step_index": cp.get("step_index")})

        monitor = asyncio.create_task(self._monitor_task_progress(task_id))
        try:
            # Snapshot before running.
            await self.tm.save_checkpoint(task_id, {"phase": "pre_run",
                                                    "task": task.model_dump()})
            result: TaskResult = await self.orch.run_task(task, entry_agent)
            artifacts = result.artifacts or []
            result_text = result.summary or ""
            if artifacts:
                result_text += "\\n\\n__ARTIFACTS_JSON__\\n" + json.dumps(artifacts, ensure_ascii=False, default=str)
            # Save result and status.
            if result.success:
                explicit_artifacts = [
                    a for a in (result.artifacts or [])
                    if isinstance(a, dict) and any(k in a for k in ("path", "file_path", "url", "content"))
                ]
                if explicit_artifacts:
                    from .artifact_validator import validate_all
                    artifact_validation = validate_all(explicit_artifacts)
                    await self.tm.log_event(task_id, "artifact_validation",
                                            artifact_validation)
                    if not artifact_validation["valid"]:
                        raise RuntimeError("Artifact validation failed: " +
                                           json.dumps(artifact_validation)[:1800])
                try:
                    await self.tm.remember(
                        str(row.get("user_id") or "api"),
                        f"Task: {original_description}\nResult: {result.summary[:8000]}",
                        task_id=task_id, kind="task_summary", importance=0.7)
                except Exception:
                    log.debug("Memory write unavailable", exc_info=True)
                await self.tm.set_status(task_id, TaskStatus.COMPLETED,
                                         result=result_text,
                                         progress=1.0,
                                         current_step="completed")
            else:
                rc = int(row.get("retry_count") or 0)
                max_r = int(row.get("max_retries") or 3)
                if rc < max_r:
                    await self.tm.set_status(task_id, TaskStatus.WAITING_RETRY,
                                             error=result.error or "unknown",
                                             current_step=f"retry {rc+1}/{max_r}")
                    # Re-enqueue with backoff via the queue.
                    await self.queue.enqueue(task_id, {"retry": str(rc + 1)})
                else:
                    await self.tm.set_status(task_id, TaskStatus.FAILED,
                                             error=result.error or "unknown",
                                             result=result_text, progress=1.0)
            await self.tm.save_checkpoint(task_id, {"phase": "post_run",
                                                    "task": task.model_dump(),
                                                    "result": result.model_dump()})
            await self.tm.log_event(task_id, "worker.finished",
                                    {"success": result.success})
        except Exception as e:
            log.exception("Task %s failed inside kernel", task_id)
            await self.tm.save_checkpoint(task_id, {"phase": "error",
                                                    "error": str(e)})
            raise

    async def shutdown(self) -> None:
        self.stop.set()
        if self.hb:
            await self.hb.stop()
        if self.queue:
            await self.queue.close()
        if self.tm:
            await self.tm.close()


async def _main():
    name = os.getenv("WORKER_NAME", f"worker-{os.getpid()}")
    w = Worker(name)
    await w.setup()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda: asyncio.create_task(w.shutdown()))
        except NotImplementedError:
            pass

    await w.loop()


if __name__ == "__main__":
    asyncio.run(_main())
