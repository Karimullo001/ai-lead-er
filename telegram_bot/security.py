from __future__ import annotations
import logging
import os
from typing import Optional
from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject, CallbackQuery

from core.auth import Authorizer

log = logging.getLogger("agentos.tg.security")


class AuthMiddleware(BaseMiddleware):
    """Rejects non-allowlisted users with a polite message; enforces rate limit."""

    def __init__(self, authorizer: Optional[Authorizer] = None):
        super().__init__()
        self.auth = authorizer or Authorizer()

    async def __call__(self, handler, event: TelegramObject, data):
        user_id = None
        if isinstance(event, Message) and event.from_user:
            user_id = event.from_user.id
        elif isinstance(event, CallbackQuery) and event.from_user:
            user_id = event.from_user.id
        if user_id is None:
            return
        ok, why = self.auth.check(user_id)
        if not ok:
            log.warning("Rejected Telegram user %s (%s)", user_id, why)
            if isinstance(event, Message):
                await event.answer(
                    "⛔ Unauthorized.\n"
                    f"Your Telegram user id is: `{user_id}`\n"
                    "Ask the operator to add you to TELEGRAM_ALLOWED_USER_IDS.",
                    parse_mode="Markdown")
            elif isinstance(event, CallbackQuery):
                await event.answer("⛔ Unauthorized.", show_alert=True)
            return
        return await handler(event, data)
