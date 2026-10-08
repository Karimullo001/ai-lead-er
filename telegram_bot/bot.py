from __future__ import annotations
import json
import asyncio, logging, os, signal
from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from core.task_manager import TaskManager
from core.queue import TaskQueue
from core.approval import ApprovalGate
from core.model_router import ModelRouter
from core.orchestrator import Orchestrator
from core.events import EventStore
from core.comm_bus import CommunicationBus
from core.factory import AgentFactory
from core.reliability import ReliabilityEngine
from core.tools import ToolRegistry, Sandbox
from core.llm import LLMClient
from demo.agents import build_demo_specs, register_demo_tools

from .handlers import router, STATE
from .security import AuthMiddleware
from .notifier import get_notifier

log = logging.getLogger("agentos.tg.bot")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")


async def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is required")
    dsn = os.getenv("DATABASE_URL", "postgresql://agent:agentpass@postgres/agents")
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")

    # --- shared infrastructure ---
    tm = TaskManager(dsn); await tm.connect()
    q = TaskQueue(redis_url, consumer_name="telegram-bot"); await q.connect()

    # --- orchestrator for read-only /status & /agents queries ---
    tools = ToolRegistry(); register_demo_tools(tools)
    events = EventStore(dsn=dsn); await events.connect()
    comm = CommunicationBus()
    router_model = ModelRouter()
    llm = LLMClient(router=router_model)
    approval = ApprovalGate(auto_approve_in_dev=os.getenv("AUTO_APPROVE","false").lower()=="true")
    factory = AgentFactory(llm=llm, tools=tools, event_store=events, comm_bus=comm,
                           reliability=ReliabilityEngine(), approval_gate=approval,
                           sandbox=Sandbox(prefer_docker=os.getenv("SANDBOX_MODE","docker")=="docker"))
    orch = Orchestrator(events, comm)
    for spec in build_demo_specs():
        orch.register_agent(factory.build(spec))

    # --- notifier ---
    notifier = get_notifier()
    await notifier.start()

    # --- expose shared state to handlers ---
    STATE.update(dict(tm=tm, queue=q, router=router_model, orch=orch,
                      approval=approval, notifier=notifier, redis_url=redis_url))

    # --- aiogram ---
    bot = Bot(token, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))
    STATE["bot"] = bot
    dp = Dispatcher()
    await bot.set_my_commands([
        BotCommand(command="start", description="Boshlash / Yordam"),
        BotCommand(command="search", description="Internetdan qidirish"),
        BotCommand(command="voice", description="Ovozli javob rejimi (ON/OFF)"),
        BotCommand(command="clear", description="Suhbat tarixini tozalash"),
        BotCommand(command="memory", description="Eslab qolingan faktlar"),
        BotCommand(command="status", description="Tizim va AI holati"),
        BotCommand(command="task", description="Fon vazifasi yaratish"),
        BotCommand(command="tasks", description="Vazifalar ro'yxati"),
        BotCommand(command="help", description="Yordam"),
    ])

    dp.message.middleware(AuthMiddleware())
    dp.callback_query.middleware(AuthMiddleware())
    dp.include_router(router)

    stop = asyncio.Event()
    loop = asyncio.get_event_loop()
    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(s, stop.set)
        except NotImplementedError:
            pass

    # Background: poll for status changes and notify chat
    notif_task = asyncio.create_task(_status_notifier_loop(tm, notifier))

    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        stop.set()
        notif_task.cancel()
        await notifier.stop()
        await q.close(); await tm.close()


async def _status_notifier_loop(tm: TaskManager, notifier) -> None:
    seen = set()
    while True:
        try:
            async with tm.pool.acquire() as c:
                rows = await c.fetch("SELECT * FROM tasks WHERE status IN ('COMPLETED','FAILED','WAITING_APPROVAL') ORDER BY updated_at DESC LIMIT 50")
            for r in rows:
                key = str(r["task_id"]) + ":" + str(r["status"])
                if key in seen:
                    continue
                seen.add(key)
                chat_id = r.get("chat_id")
                if not chat_id:
                    continue
                short = str(r["task_id"])[:8]
                if r["status"] == "FAILED":
                    await notifier.send(chat_id, "❌ Task failed " + short + "\n\nError: " + str(r.get("error") or "unknown")[:2500])
                    continue
                if r["status"] == "WAITING_APPROVAL":
                    await notifier.send(chat_id, "🔐 Task " + short + " is waiting for approval.")
                    continue
                raw = r.get("result") or ""
                artifact_json = ""
                if "__ARTIFACTS_JSON__" in raw:
                    raw, artifact_json = raw.split("__ARTIFACTS_JSON__", 1)
                elapsed = (r.get("completed_at") or 0) - (r.get("started_at") or 0)
                await notifier.send(chat_id, "✅ Task completed " + short + "\n⏱ " + str(int(elapsed)) + "s\n\n" + raw.strip()[:3000])
                try:
                    from core.conversation_memory import ConversationMemory
                    cm=ConversationMemory(tm.pool); await cm.ensure()
                    await cm.append(str(r.get("user_id") or ""), str(chat_id), "assistant", raw.strip()[:12000])
                except Exception as exc:
                    log.debug("assistant memory save failed: %s", exc)
                try:
                    artifacts = json.loads(artifact_json.strip()) if artifact_json.strip() else []
                except Exception:
                    artifacts = []
                for artifact in artifacts:
                    if isinstance(artifact, dict):
                        await notifier.send_artifact(chat_id, artifact)
        except Exception as exc:
            log.debug("notifier loop: %s", exc)
        await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())
