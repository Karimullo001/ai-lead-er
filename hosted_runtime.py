from __future__ import annotations
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

import hashlib as _agentos_hashlib
from aiogram.types import Update as _AgentOSUpdate
import httpx

async def _render_keep_alive_loop(external_url: str):
    """Keep-alive ping loop to prevent Render free instance from sleeping."""
    url = f"{external_url.rstrip('/')}/healthz"
    log.info("Starting Render keep-alive pinger for: %s", url)
    await asyncio.sleep(60)  # Initial wait after startup
    while not _runtime.get("stopping"):
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.get(url)
                log.debug("Keep-alive ping to %s status: %d", url, r.status_code)
        except Exception as e:
            log.debug("Keep-alive ping notice: %s", e)
        await asyncio.sleep(480)  # Ping every 8 minutes (Render sleeps after 15 mins)

async def _telegram_loop(tm, queue, router_model, orch):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        log.warning("TELEGRAM_BOT_TOKEN missing; Telegram disabled")
        return
    notifier = get_notifier()
    bot = None
    notif_task = None
    keep_alive_task = None
    try:
        if notifier._bot is None:
            await notifier.start()
        bot = Bot(token, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))
        STATE.update(dict(tm=tm, queue=queue, router=router_model, orch=orch,
                          notifier=notifier, bot=bot))
        dp = Dispatcher()
        dp.message.middleware(AuthMiddleware())
        dp.callback_query.middleware(AuthMiddleware())
        dp.include_router(router)
        _runtime["bot"] = bot
        _runtime["dispatcher"] = dp
        external = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")
        if external:
            secret = _agentos_hashlib.sha256(token.encode()).hexdigest()
            webhook = external + "/telegram/webhook"
            await bot.set_webhook(
                webhook,
                secret_token=secret,
                allowed_updates=dp.resolve_used_update_types(),
                drop_pending_updates=False,
            )
            _runtime["telegram_ready"] = True
            _runtime["webhook_owner_url"] = webhook
            log.info("Telegram webhook connected at %s", webhook)
            notif_task = asyncio.create_task(_status_notifier_loop(tm, notifier))
            keep_alive_task = asyncio.create_task(_render_keep_alive_loop(external))
            while not _runtime.get("stopping"):
                await asyncio.sleep(5)
        else:
            # Local fallback: keep the existing polling behavior.
            await bot.delete_webhook(drop_pending_updates=False)
            _runtime["telegram_ready"] = True
            notif_task = asyncio.create_task(_status_notifier_loop(tm, notifier))
            await dp.start_polling(
                bot, allowed_updates=dp.resolve_used_update_types(),
                handle_signals=False,
            )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        _runtime["telegram_ready"] = False
        log.exception("Telegram runtime failed: %s", exc)
    finally:
        if keep_alive_task:
            keep_alive_task.cancel()
        if notif_task:
            notif_task.cancel()
            try:
                await notif_task
            except asyncio.CancelledError:
                pass
        if bot:
            try:
                info = await bot.get_webhook_info()
                current_url = getattr(info, "url", "") or ""
                owned_url = _runtime.get("webhook_owner_url")
                if owned_url and current_url == owned_url:
                    await bot.delete_webhook(drop_pending_updates=False)
            except Exception:
                pass
            try:
                await bot.session.close()
            except Exception:
                pass

_telegram_seen_updates: set[int] = set()
_telegram_seen_order: list[int] = []

async def handle_telegram_webhook(payload: dict, secret_header: str | None = None):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token or not secret_header:
        log.warning("Telegram webhook rejected: missing token or secret header")
        return False
    expected = _agentos_hashlib.sha256(token.encode()).hexdigest()
    if secret_header != expected:
        log.warning("Telegram webhook rejected: invalid secret header")
        return False
    dp = _runtime.get("dispatcher")
    bot = _runtime.get("bot")
    if not dp or not bot:
        log.warning("Telegram webhook rejected: dispatcher/bot not ready")
        return False
    try:
        update = _AgentOSUpdate.model_validate(payload)
        update_id = int(update.update_id)
        if update_id in _telegram_seen_updates:
            log.info("Telegram duplicate update ignored: %s", update_id)
            return True
        _telegram_seen_updates.add(update_id)
        _telegram_seen_order.append(update_id)
        if len(_telegram_seen_order) > 1000:
            old_id = _telegram_seen_order.pop(0)
            _telegram_seen_updates.discard(old_id)

        # CRITICAL FIX: Run feed_update in background task so webhook responds HTTP 200 immediately
        # within 5ms to Telegram. This prevents Telegram webhook timeouts and stops freezing!
        asyncio.create_task(dp.feed_update(bot, update))
        log.info("Telegram webhook update %s dispatched immediately in background", update_id)
        return True
    except Exception:
        log.exception("Telegram webhook update processing failed")
        return False
