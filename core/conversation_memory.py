from __future__ import annotations
import logging
import re
import time
from typing import List, Optional

log = logging.getLogger("agentos.conv_memory")

_SECRET_PATTERN = re.compile(
    r"(?:sk-[a-zA-Z0-9_\-]{20,}|ghp_[a-zA-Z0-9]{20,}|github_pat_[a-zA-Z0-9_]{30,}|"
    r"(?:\d{8,11}:[a-zA-Z0-9_\-]{30,})|Bearer\s+[a-zA-Z0-9_\-\.]{20,})",
    re.I
)


def redact_secrets(text: str) -> str:
    """Scrub sensitive credentials, API keys, and session tokens from text."""
    if not text:
        return ""
    return _SECRET_PATTERN.sub("[REDACTED_SECRET]", str(text))


class ConversationMemory:
    def __init__(self, pool):
        self.pool = pool

    async def ensure(self) -> None:
        if self.pool is None:
            return
        try:
            async with self.pool.acquire() as c:
                await c.execute("""
                    CREATE TABLE IF NOT EXISTS conversation_messages (
                        id BIGSERIAL PRIMARY KEY,
                        user_id TEXT NOT NULL,
                        chat_id TEXT NOT NULL,
                        role TEXT NOT NULL,
                        content TEXT NOT NULL,
                        created_at DOUBLE PRECISION NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS conv_idx ON conversation_messages(user_id, chat_id, created_at);
                """)
        except Exception as e:
            log.warning("ConversationMemory.ensure failed: %s", e)

    async def append(self, user_id: str | int, chat_id: str | int, role: str, content: str) -> None:
        if self.pool is None or not content:
            return
        sanitized = redact_secrets(str(content))
        try:
            async with self.pool.acquire() as c:
                await c.execute(
                    "INSERT INTO conversation_messages(user_id, chat_id, role, content, created_at) "
                    "VALUES($1, $2, $3, $4, $5)",
                    str(user_id), str(chat_id), str(role), sanitized, time.time()
                )
        except Exception as e:
            log.warning("ConversationMemory.append failed: %s", e)

    async def render(self, user_id: str | int, chat_id: str | int, limit: int = 20) -> str:
        if self.pool is None:
            return ""
        try:
            async with self.pool.acquire() as c:
                rows = await c.fetch(
                    "SELECT role, content FROM conversation_messages "
                    "WHERE user_id=$1 AND chat_id=$2 ORDER BY created_at DESC LIMIT $3",
                    str(user_id), str(chat_id), limit
                )
            return "\n".join(f"[{r['role']}] {str(r['content'])[:1200]}" for r in reversed(rows))
        except Exception as e:
            log.warning("ConversationMemory.render failed: %s", e)
            return ""

    async def clear(self, user_id: str | int, chat_id: str | int) -> None:
        if self.pool is None:
            return
        try:
            async with self.pool.acquire() as c:
                await c.execute(
                    "DELETE FROM conversation_messages WHERE user_id=$1 AND chat_id=$2",
                    str(user_id), str(chat_id)
                )
        except Exception as e:
            log.warning("ConversationMemory.clear failed: %s", e)
