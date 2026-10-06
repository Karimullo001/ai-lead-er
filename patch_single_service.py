from pathlib import Path

p = Path("api/main.py")
s = p.read_text(encoding="utf-8")

if "HOSTED_RUNTIME" not in s:
    marker = "STATE: Dict[str, Any] = {}"
    if marker not in s:
        raise SystemExit("STATE marker not found")
    s = s.replace(marker, marker + "\nHOSTED_RUNTIME: Dict[str, Any] = {}", 1)

if "start_hosted_runtime" not in s:
    old = """    STATE.update(dict(tools=tools, events=events, comm=comm, approval=approval,
                      llm=llm, factory=factory, orch=orch))
    log.info("AgentOS ready with %d agents", len(orch.agents))
    yield
    STATE.clear()"""
    new = """    STATE.update(dict(tools=tools, events=events, comm=comm, approval=approval,
                      llm=llm, factory=factory, orch=orch))
    log.info("AgentOS ready with %d agents", len(orch.agents))
    if os.getenv("EMBEDDED_RUNTIME", "true").lower() == "true":
        from hosted_runtime import start_hosted_runtime
        HOSTED_RUNTIME.update(await start_hosted_runtime())
    yield
    if HOSTED_RUNTIME:
        from hosted_runtime import stop_hosted_runtime
        await stop_hosted_runtime(HOSTED_RUNTIME)
        HOSTED_RUNTIME.clear()
    STATE.clear()"""
    if old in s:
        s = s.replace(old, new, 1)

p.write_text(s, encoding="utf-8")
print("ok")

from pathlib import Path as _Path
_Path("hosted_runtime.py").write_text(r'''from __future__ import annotations
import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from core.worker import Worker
from core.model_router import ModelRouter
from telegram_bot.handlers import router, STATE
from telegram_bot.security import AuthMiddleware
from telegram_bot.notifier import get_notifier
from telegram_bot.bot import _status_notifier_loop

log = logging.getLogger("agentos.hosted")
_runtime = {}

async def _telegram_loop(tm, queue, router_model, orch):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        log.warning("TELEGRAM_BOT_TOKEN missing; Telegram disabled")
        return
    delay = 2.0
    while not _runtime.get("stopping"):
        bot = None
        notif_task = None
        notifier = get_notifier()
        try:
            if notifier._bot is None:
                await notifier.start()
            bot = Bot(token, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))
            await bot.delete_webhook(drop_pending_updates=False)
            me = await bot.get_me()
            STATE.update(dict(tm=tm, queue=queue, router=router_model,
                              orch=orch, notifier=notifier, bot=bot))
            dp = Dispatcher()
            dp.message.middleware(AuthMiddleware())
            dp.callback_query.middleware(AuthMiddleware())
            dp.include_router(router)
            notif_task = asyncio.create_task(_status_notifier_loop(tm, notifier))
            _runtime["telegram_ready"] = True
            log.info("Telegram bot connected as @%s", me.username)
            delay = 2.0
            await dp.start_polling(
                bot,
                allowed_updates=dp.resolve_used_update_types(),
                handle_signals=False,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _runtime["telegram_ready"] = False
            log.warning("Telegram polling attempt failed; retrying in %.1fs: %s",
                        delay, exc)
            await asyncio.sleep(delay)
            delay = min(delay * 2.0, 30.0)
        finally:
            if notif_task:
                notif_task.cancel()
                try:
                    await notif_task
                except asyncio.CancelledError:
                    pass
            if bot:
                try:
                    await bot.session.close()
                except Exception:
                    pass

async def start_hosted_runtime():
    _runtime.clear()
    _runtime["stopping"] = False
    _runtime["telegram_ready"] = False

    worker = Worker("api-hosted-worker")
    await worker.setup()
    assert worker.tm and worker.queue and worker.orch

    STATE.update(dict(
        tm=worker.tm,
        queue=worker.queue,
        router=ModelRouter(),
        orch=worker.orch,
    ))

    worker_task = asyncio.create_task(worker.loop())
    telegram_task = asyncio.create_task(
        _telegram_loop(worker.tm, worker.queue, STATE["router"], worker.orch)
    )
    _runtime.update(
        worker=worker,
        worker_task=worker_task,
        telegram_task=telegram_task,
        tm=worker.tm,
        queue=worker.queue,
    )
    return _runtime

async def stop_hosted_runtime(state):
    state["stopping"] = True
    tg = state.get("telegram_task")
    if tg:
        tg.cancel()
        try:
            await tg
        except asyncio.CancelledError:
            pass
    wt = state.get("worker_task")
    if wt:
        wt.cancel()
        try:
            await wt
        except asyncio.CancelledError:
            pass
    worker = state.get("worker")
    if worker:
        try:
            await worker.shutdown()
        except Exception:
            log.exception("worker shutdown failed")
''', encoding="utf-8")
