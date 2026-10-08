from __future__ import annotations
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def approval_kb(request_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ APPROVE", callback_data=f"appr:yes:{request_id}"),
        InlineKeyboardButton(text="❌ DENY",    callback_data=f"appr:no:{request_id}"),
    ]])


def task_actions_kb(task_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏸ Pause",  callback_data=f"task:pause:{task_id}"),
         InlineKeyboardButton(text="▶️ Resume", callback_data=f"task:resume:{task_id}")],
        [InlineKeyboardButton(text="❌ Cancel", callback_data=f"task:cancel:{task_id}"),
         InlineKeyboardButton(text="🔁 Retry",  callback_data=f"task:retry:{task_id}")],
    ])


def confirm_kb(action: str, token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Confirm", callback_data=f"conf:yes:{action}:{token}"),
        InlineKeyboardButton(text="❌ Cancel",  callback_data=f"conf:no:{action}:{token}"),
    ]])
