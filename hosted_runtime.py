from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Dict

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from core.approval import ApprovalGate
from core.comm_bus import CommunicationBus
from core.events import EventStore
from core.factory import AgentFactory
from core.llm import LLMClient
from core.model_router import ModelRouter
from core.orchestrator import Orchestrator
from core.queue import TaskQueue
from core.reliability import ReliabilityEngine
from core.task_manager import TaskManager
from core.tools import Sandbox, ToolRegistry
from demo.agents import build_demo_specs, register_demo_tools
from telegram_bot.handlers import router
from telegram_bot.security import AuthMiddleware
from telegram_bot.notifier import get_notifier

log = logging.getLogger("agentos.hosted")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))


async def _build_runtime():
    dsn = os.environ["DATABASE_URL"]
    redis_url = os.environ["REDIS_URL"]

    tm = TaskManager(dsn)
    await tm.connect()
    queue = TaskQueue(redis_url, consumer_name="api-hosted-worker")
    await queue.connect()

    tools = ToolRegistry()
    register_demo_tools(tools)
    events = EventStore(dsn=dsn)
    await events.connect()
    comm = CommunicationBus()
    router_model = ModelRouter()
    llm = LLMClient(router=router_model)
    approval = ApprovalGate(
        auto_approve_in_dev=os.getenv("AUTO_APPROVE", "false").lower() == "true"
    )
    factory = AgentFactory(
        llm=llm,
        tools=tools,
        event_store=events,
        comm_bus=comm,
        reliability=ReliabilityEngine(),
        approval_gate=approval,
        sandbox=Sandbox(prefer_docker=os.getenv("SANDBOX_MODE", "false").lower() == "true"),
    )
    orch = Orchestrator(events, comm)
    for spec in build_demo_specs():
        orch.register_agent(factory.build(spec))
    return tm, queue, orch, router_model


async def _process_queue(tm: TaskManager, queue: TaskQueue, orch: Orchestrator):
    log.info("Hosted AgentOS worker started")
    while True:
        try:
            item = await queue.redis.blpop(queue.queue_name, timeout=2)
            if not item:
                continue
            _, raw = item
            payload = json.loads(raw)
            task_id = payload["task_id"]
            row = await tm.get_task(task_id)
            if not row:
                log.warning("Task %s not found", task_id)
                continue
            from core.models import Task
            await tm.set_status(task_id, "RUNNING")
            task = Task(
                id=task_id,
                description=row["description"],
                domain=row.get("domain", "general"),
            )
            result = await orch.run_task(task, row.get("entry_agent_id", "coordinator-1"))
            if result.success:
                await tm.complete_task(task_id, result.summary, result.model_dump())
            else:
                await tm.fail_task(task_id, result.error or "task failed")
            await queue.redis.publish(
                f"agentos:results:{task_id}",
                json.dumps(result.model_dump(), default=str),
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Hosted worker iteration failed")
            await asyncio.sleep(1)


async def _run_telegram(tm: TaskManager, queue: TaskQueue, orch: Orchestrator, router_model: ModelRouter):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        log.warning("TELEGRAM_BOT_TOKEN not configured; Telegram integration disabled")
        return

    notifier = get_notifier()
    await notifier.start()
    bot = Bot(token, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))
    dp = Dispatcher()
    dp.message.middleware(AuthMiddleware())
    dp.callback_query.middleware(AuthMiddleware())
    dp.include_router(router)

    # handlers use this shared state
    from telegram_bot.handlers import STATE
    STATE.update(
        tm=tm,
        queue=queue,
        router=router_model,
        orch=orch,
        approval=None,
        notifier=notifier,
        redis_url=os.environ["REDIS_URL"],
    )

    log.info("Hosted Telegram bot starting polling")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await notifier.stop()
        await bot.session.close()


async def start_hosted_runtime():
    tm, queue, orch, router_model = await _build_runtime()
    worker_task = asyncio.create_task(_process_queue(tm, queue, orch), name="agentos-worker")
    telegram_task = asyncio.create_task(
        _run_telegram(tm, queue, orch, router_model), name="agentos-telegram"
    ) if os.getenv("TELEGRAM_BOT_TOKEN") else None

    return {
        "tm": tm,
        "queue": queue,
        "worker_task": worker_task,
        "telegram_task": telegram_task,
    }


async def stop_hosted_runtime(runtime: Dict[str, Any]):
    for key in ("telegram_task", "worker_task"):
        task = runtime.get(key)
        if task:
            task.cancel()
    tasks = [runtime[k] for k in ("telegram_task", "worker_task") if runtime.get(k)]
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    try:
        await runtime["queue"].close()
    finally:
        await runtime["tm"].close()
