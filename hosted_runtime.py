from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from core.model_router import ModelRouter
from core.worker import Worker
from core.task_manager import TaskManager
from core.queue import TaskQueue
from core.orchestrator import Orchestrator
from demo.agents import build_demo_specs, register_demo_tools
from core.approval import ApprovalGate
from core.comm_bus import CommunicationBus
from core.events import EventStore
from core.factory import AgentFactory
from core.reliability import ReliabilityEngine
from core.tools import Sandbox, ToolRegistry
from core.llm import LLMClient
from telegram_bot.handlers import router
from telegram_bot.security import AuthMiddleware
from telegram_bot.notifier import get_notifier

log = logging.getLogger("agentos.hosted")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))


async def _telegram_runtime():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        log.warning("TELEGRAM_BOT_TOKEN not configured; Telegram integration disabled")
        await asyncio.Event().wait()
        return

    dsn = os.environ["DATABASE_URL"]
    redis_url = os.environ["REDIS_URL"]
    tm = TaskManager(dsn)
    await tm.connect()
    queue = TaskQueue(redis_url, consumer_name="api-hosted-telegram")
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
        llm=llm, tools=tools, event_store=events, comm_bus=comm,
        reliability=ReliabilityEngine(), approval_gate=approval,
        sandbox=Sandbox(prefer_docker=False),
    )
    orch = Orchestrator(events, comm)
    for spec in build_demo_specs():
        orch.register_agent(factory.build(spec))

    from telegram_bot.handlers import STATE
    notifier = get_notifier()
    await notifier.start()
    STATE.update(
        tm=tm, queue=queue, router=router_model, orch=orch,
        approval=approval, notifier=notifier, redis_url=redis_url
    )

    bot = Bot(token, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))
    dp = Dispatcher()
    dp.message.middleware(AuthMiddleware())
    dp.callback_query.middleware(AuthMiddleware())
    dp.include_router(router)

    log.info("AgentOS embedded Telegram service: starting polling")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await notifier.stop()
        await bot.session.close()
        await queue.close()
        await tm.close()


async def start_hosted_runtime() -> Dict[str, Any]:
    worker = Worker(name="api-hosted-worker")
    await worker.setup()
    worker_task = asyncio.create_task(worker.loop(), name="agentos-worker-embedded")

    telegram_task = None
    if os.getenv("TELEGRAM_BOT_TOKEN"):
        telegram_task = asyncio.create_task(
            _telegram_runtime(), name="agentos-telegram-embedded"
        )

    log.info("AgentOS single-service runtime started: worker=%s telegram=%s",
             True, bool(telegram_task))
    return {"worker": worker, "worker_task": worker_task, "telegram_task": telegram_task}


async def stop_hosted_runtime(runtime: Dict[str, Any]) -> None:
    worker = runtime.get("worker")
    if worker:
        worker.stop.set()

    for key in ("telegram_task", "worker_task"):
        task = runtime.get(key)
        if task:
            task.cancel()

    tasks = [runtime[k] for k in ("telegram_task", "worker_task") if runtime.get(k)]
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)

    if worker:
        await worker.close()
