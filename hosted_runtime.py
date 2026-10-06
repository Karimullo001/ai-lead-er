"""Embedded AgentOS runtime for Free Render.

The API must bind its port even if worker/Telegram initialization is slow.
All heavy imports and connections therefore happen in background tasks.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

log = logging.getLogger("agentos.hosted")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))


async def _telegram_runtime() -> None:
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
    from telegram_bot.handlers import STATE, router
    from telegram_bot.notifier import get_notifier
    from telegram_bot.security import AuthMiddleware

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        log.warning("TELEGRAM_BOT_TOKEN missing; Telegram disabled")
        return

    log.info("Telegram runtime: token configured; connecting to Postgres")
    dsn = os.environ["DATABASE_URL"]
    redis_url = os.environ["REDIS_URL"]

    tm = TaskManager(dsn)
    await tm.connect()
    log.info("Telegram runtime: Postgres connected")
    queue = TaskQueue(redis_url, consumer_name="api-hosted-telegram")
    await queue.connect()
    log.info("Telegram runtime: Redis connected")

    tools = ToolRegistry()
    register_demo_tools(tools)
    events = EventStore(dsn=dsn)
    await events.connect()
    log.info("Telegram runtime: EventStore connected")
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
        sandbox=Sandbox(prefer_docker=False),
    )
    orch = Orchestrator(events, comm)
    for spec in build_demo_specs():
        orch.register_agent(factory.build(spec))

    notifier = get_notifier()
    await notifier.start()
    log.info("Telegram runtime: notifier ready")
    STATE.update(
        tm=tm, queue=queue, router=router_model, orch=orch,
        approval=approval, notifier=notifier, redis_url=redis_url
    )

    bot = Bot(token, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))
    dp = Dispatcher()
    dp.message.middleware(AuthMiddleware())
    dp.callback_query.middleware(AuthMiddleware())
    dp.include_router(router)

    log.info("Telegram polling starting")
    try:
        me = await bot.get_me()
        log.info("Telegram bot connected as @%s", me.username or me.id)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("Telegram polling stopped with an error")
    finally:
        await notifier.stop()
        await bot.session.close()
        await queue.close()
        await tm.close()
        log.info("Telegram runtime stopped")


async def _worker_runtime() -> None:
    from core.worker import Worker

    worker = Worker(name="api-hosted-worker")
    await worker.setup()
    log.info("Embedded worker initialized")
    try:
        await worker.loop()
    except asyncio.CancelledError:
        raise
    finally:
        try:
            await worker.shutdown()
        except Exception:
            log.exception("Embedded worker shutdown failed")


async def _supervisor() -> None:
    log.info("Hosted runtime supervisor starting")
    worker_task = asyncio.create_task(_worker_runtime(), name="agentos-worker-embedded")
    telegram_task = None

    telegram_task = asyncio.create_task(
        _telegram_runtime(), name="agentos-telegram-embedded"
    )

    tasks = [worker_task] + ([telegram_task] if telegram_task else [])
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for name, result in zip(("worker", "telegram"), results):
        if isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError):
            log.error("Hosted %s task crashed: %r", name, result)
        else:
            log.warning("Hosted %s task exited", name)


async def start_hosted_runtime() -> dict[str, Any]:
    supervisor = asyncio.create_task(_supervisor(), name="agentos-hosted-supervisor")
    supervisor.add_done_callback(_report_supervisor)
    log.info("AgentOS hosted supervisor launched")
    return {"supervisor_task": supervisor}


def _report_supervisor(task: asyncio.Task) -> None:
    if task.cancelled():
        log.info("Hosted runtime supervisor cancelled")
        return
    exc = task.exception()
    if exc:
        log.error("Hosted runtime supervisor crashed", exc_info=exc)
    else:
        log.warning("Hosted runtime supervisor exited")


async def stop_hosted_runtime(runtime: dict[str, Any]) -> None:
    task = runtime.get("supervisor_task")
    if task:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    log.info("Hosted runtime stopped")
