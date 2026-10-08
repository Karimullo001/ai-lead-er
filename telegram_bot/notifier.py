from __future__ import annotations
import logging, os, mimetypes
from pathlib import Path
from typing import Optional
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import FSInputFile

log = logging.getLogger("agentos.tg.notifier")


class Notifier:
    """Fire-and-forget Telegram notifications by chat_id."""

    def __init__(self, token: Optional[str] = None):
        self.token = token or os.getenv("TELEGRAM_BOT_TOKEN")
        self._bot: Optional[Bot] = None

    async def start(self) -> None:
        if not self.token:
            log.warning("No TELEGRAM_BOT_TOKEN; notifier disabled")
            return
        self._bot = Bot(self.token,
                        default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))

    async def stop(self) -> None:
        if self._bot:
            await self._bot.session.close()

    async def send(self, chat_id: int | str, text: str,
                   reply_markup=None) -> None:
        if not self._bot:
            log.debug("Notifier disabled; would send: %s", text[:120])
            return
        try:
            await self._bot.send_message(int(chat_id), text[:4000],
                                         reply_markup=reply_markup)
        except Exception as e:
            log.warning("Telegram send failed: %s", e)

    async def send_artifact(self, chat_id: int | str, artifact: dict) -> None:
        if not self._bot:
            return
        url = artifact.get("url") or artifact.get("link") or artifact.get("download_url")
        path = artifact.get("path") or artifact.get("file_path")
        name = str(artifact.get("name") or artifact.get("filename") or "artifact")
        kind = str(artifact.get("type") or artifact.get("kind") or "").lower()
        try:
            if url:
                await self._bot.send_message(int(chat_id), "Artifact: " + name + "\n" + url)
                return
            if not path:
                return
            p = Path(str(path))
            if not p.exists():
                return
            mime = mimetypes.guess_type(p.name)[0] or ""
            if kind in ("image", "photo") or mime.startswith("image/"):
                await self._bot.send_photo(int(chat_id), FSInputFile(p), caption=name[:1000])
            elif kind in ("audio", "music") or mime.startswith("audio/"):
                await self._bot.send_audio(int(chat_id), FSInputFile(p), caption=name[:1000])
            elif kind == "video" or mime.startswith("video/"):
                await self._bot.send_video(int(chat_id), FSInputFile(p), caption=name[:1000])
            else:
                await self._bot.send_document(int(chat_id), FSInputFile(p), caption=name[:1000])
        except Exception as exc:
            log.warning("artifact delivery failed: %s", exc)


_notifier: Optional[Notifier] = None


def get_notifier() -> Notifier:
    global _notifier
    if _notifier is None:
        _notifier = Notifier()
    return _notifier
