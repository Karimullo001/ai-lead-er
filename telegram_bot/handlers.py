from __future__ import annotations
import base64, mimetypes, tempfile
import asyncio, json, logging, os, time
from pathlib import Path
from typing import Any, Dict, Optional
from aiogram import Router, F
from aiogram.filters import Command, CommandStart, CommandObject
from aiogram.types import Message, CallbackQuery, BufferedInputFile

from core.task_manager import TaskManager, TaskStatus
from core.queue import TaskQueue
from core.approval import ApprovalGate
from core.model_router import ModelRouter
from .keyboards import approval_kb, task_actions_kb
from .notifier import Notifier
from core.conversation_memory import ConversationMemory

log = logging.getLogger("agentos.tg.handlers")
router = Router()

# These are wired at startup by bot.py:
STATE: Dict[str, Any] = {}
_VOICE_PREF: Dict[int, bool] = {}


def fmt_duration(sec: float) -> str:
    if sec < 60: return f"{int(sec)}s"
    if sec < 3600: return f"{int(sec//60)}m {int(sec%60)}s"
    return f"{int(sec//3600)}h {int((sec%3600)//60)}m"


def progress_bar(p: float, width: int = 20) -> str:
    filled = int(max(0.0, min(1.0, p)) * width)
    return "█" * filled + "░" * (width - filled)


# ---------------- /start ----------------

@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject):
    name = message.from_user.first_name if message.from_user else "do'stim"
    await message.answer(
        f"👋 Salom, {name}! Men sening shaxsiy AI yordamchingman.\n\n"
        "✨ *Imkoniyatlarim:*\n"
        "• 💬 *Erkin suhbat:* Har qanday savolingizga tezkor va aqlli javoblar (Claude va ChatGPT)\n"
        "• 🎙️ *Ovozli xabarlar:* Ovozli xabar yuboring — tinglab, tahlil qilib javob beraman\n"
        "• 🖼️ *Rasm tahlili:* Rasm yoki skrinshot yuboring — ko'rib tushuntirib beraman\n"
        "• 📄 *Hujjatlar:* PDF, Word, kod yoki matn fayllarini tahlil qilaman\n"
        "• 🔍 *Jonli internet:* `/search <mavzu>` orqali yangilik va ma'lumotlar qidirish\n"
        "• ⚡ *Tezkor oqim:* Javoblar jonli streaming rejimida yoziladi\n\n"
        "🛠 *Buyruqlar:*\n"
        "• /search <so'rov> — internetdan qidirish\n"
        "• /voice — ovozli javob rejimini yoqish/o'chirish\n"
        "• /clear yoki /reset — suhbat tarixini tozalash\n"
        "• /memory — eslab qolingan ma'lumotlar\n"
        "• /status — tizim va AI holati\n"
        "• /task <vazifa> — uzoq muddatli fon vazifasini yaratish\n"
        "• /tasks — vazifalar ro'yxati\n"
        "• /help — ushbu yordam xabari",
        parse_mode="Markdown"
    )


@router.message(Command("expert"))
async def cmd_expert(message: Message):
    await message.answer("🧠 *Expert mode ON*\n\nPolymath • Business Coach • Startup Builder • Creative • Security • Biohacker • Engineer • Psychologist • Chess Master • Profiler • Stoic • Dr. House • Crypto • Global Vibes\n\nSavolingiz yoki vazifangizni yuboring. Kerakli ko'nikmalarni avtomatik ishga solaman.", parse_mode="Markdown")


@router.message(Command("help"))
async def cmd_help(message: Message):
    await cmd_start(message, None)  # type: ignore[arg-type]


@router.message(Command("search"))
async def cmd_search(message: Message, command: CommandObject):
    query = (command.args or "").strip()
    if not query:
        await message.answer("ℹ️ *Qidiruvdan foydalanish:*\n`/search <mavzu>`\nMisol: `/search O'zbekiston sun'iy intellekt yangiliklari`", parse_mode="Markdown")
        return
    status_msg = await message.answer(f"🔍 Internetdan qidirilmoqda: *\"{query}\"*...", parse_mode="Markdown")
    try:
        from core.web_research import research
        results = research(query, max_results=5)
        prompt = (
            f"Foydalanuvchi internetdan quyidagini qidirdi: '{query}'.\n"
            f"Quyida qidiruv natijalari keltirilgan:\n\n"
            f"{results}\n\n"
            f"Ushbu ma'lumotlarga tayangan holda aniq, tushunarli va batafsil javob ber. "
            f"Manbalarni ko'rsat."
        )
        try:
            await status_msg.delete()
        except Exception:
            pass
        header = f"🔍 *Qidiruv:* _{query}_\n\n"
        await _chat_reply(message, prompt, prefix_header=header, display_query=f"Qidiruv: {query}")
    except Exception as e:
        log.exception("cmd_search failed: %s", e)
        await status_msg.edit_text(f"⚠️ Qidiruvda xatolik yuz berdi: {str(e)[:150]}")


@router.message(Command("voice"))
async def cmd_voice(message: Message, command: CommandObject):
    chat_id = message.chat.id
    args = (command.args or "").strip().lower()
    if args in ("on", "1", "yes", "yoq"):
        _VOICE_PREF[chat_id] = True
    elif args in ("off", "0", "no", "ochir"):
        _VOICE_PREF[chat_id] = False
    else:
        _VOICE_PREF[chat_id] = not _VOICE_PREF.get(chat_id, False)
    state = "🔊 *YONIQ* (Bot javoblarni audio tarzida ham jo'natadi)" if _VOICE_PREF[chat_id] else "🔇 *O'CHIQ* (Faqat matnli javoblar)"
    await message.answer(f"🎙️ *Ovozli javob rejimi:* {state}", parse_mode="Markdown")


@router.message(Command("clear"))
@router.message(Command("reset"))
async def cmd_clear(message: Message):
    tm = STATE.get("tm")
    pool = getattr(tm, "pool", None)
    uid = _uid(message)
    chat_id = message.chat.id
    if pool:
        try:
            from core.conversation_memory import ConversationMemory
            cm = ConversationMemory(pool)
            await cm.clear(uid, chat_id)
        except Exception as e:
            log.warning("Memory clear failed: %s", e)
    _CHAT_HISTORY.pop(chat_id, None)
    await message.answer("🧹 *Suhbat xotirasi tozalandi!*\nYangi mavzuda suhbatlashishimiz mumkin.", parse_mode="Markdown")


@router.message(Command("memory"))
async def cmd_memory(message: Message):
    tm = STATE.get("tm")
    pool = getattr(tm, "pool", None)
    uid = _uid(message)
    facts = await _load_profile(pool, uid) if pool else []
    if not facts:
        await message.answer("🧠 Siz haqingizda hali eslab qolingan maxsus faktlar yo'q.\nSuhbat davomida ismingiz, kasbingiz yoki qiziqishlaringiz haqida yozsangiz, ularni avtomatik eslab qolaman!")
        return
    text = "🧠 *Siz haqingizda eslab qolingan faktlar:*\n\n" + "\n".join(f"• {f}" for f in facts)
    text += "\n\n_Barchasini o'chirish uchun: `/forget all`_"
    await message.answer(text, parse_mode="Markdown")


@router.message(Command("forget"))
async def cmd_forget(message: Message, command: CommandObject):
    tm = STATE.get("tm")
    pool = getattr(tm, "pool", None)
    uid = _uid(message)
    if not pool:
        await message.answer("Xotira vaqtinchalik mavjud emas.")
        return
    args = (command.args or "").strip()
    if args.lower() in ("all", "barchasi"):
        try:
            async with pool.acquire() as c:
                await c.execute("DELETE FROM memory_items WHERE user_id=$1 AND kind='profile_fact'", uid)
            await message.answer("🧹 Siz haqingizdagi barcha eslab qolingan ma'lumotlar o'chirildi.")
        except Exception as e:
            await message.answer(f"⚠️ Xatolik: {e}")
    else:
        await message.answer("Barcha eslab qolingan ma'lumotlarni o'chirish uchun: `/forget all`", parse_mode="Markdown")


# ---------------- /status ----------------

@router.message(Command("status"))
async def cmd_status(message: Message):
    from core.health import system_health
    tm: TaskManager = STATE["tm"]
    router_m: ModelRouter = STATE["router"]
    health = await system_health(tm, router_m, STATE["redis_url"])
    # counts
    running = await tm.list_tasks(status=TaskStatus.RUNNING.value, limit=100)
    queued = await tm.list_tasks(status=TaskStatus.QUEUED.value, limit=100)
    pending = await tm.list_tasks(status=TaskStatus.PENDING.value, limit=100)
    workers = health.get("workers", {})
    prov = health.get("providers", {})
    lines = [
        "🤖 *AgentOS*",
        f"System: {'🟢 ONLINE' if health.get('postgres') else '🔴 DEGRADED'}",
        "",
        "*Workers:*",
    ]
    for w, ok in workers.items():
        lines.append(f"  {'🟢' if ok else '🔴'} {w}")
    if not workers:
        lines.append("  (none reporting yet)")
    lines += [
        "",
        f"*Queue:* {len(queued) + len(pending)} tasks",
        f"*Running:* {len(running)} tasks",
        "",
        "*Models:*",
    ]
    for p, ok in prov.items():
        lines.append(f"  {'🟢' if ok else '🔴'} {p}")
    if not prov:
        lines.append("  (no providers configured)")
    lines += [
        "",
        f"*Redis:* {'🟢' if health.get('redis') else '🔴'}",
        f"*Postgres:* {'🟢' if health.get('postgres') else '🔴'}",
    ]
    await message.answer("\n".join(lines), parse_mode="Markdown")


# ---------------- /task ----------------

@router.message(Command("task"))
async def cmd_task(message: Message, command: CommandObject):
    text = (command.args or "").strip()
    if not text:
        await message.answer("Usage: /task <description>")
        return
    await _create_task_and_ack(message, text)


# ---------------- natural language ----------------

@router.message(F.text & ~F.text.startswith("/"))
async def nl_handler(message: Message):
    text = (message.text or "").strip()
    if not text:
        return
    # Simple greetings should be answered directly, not scheduled as tasks.
    greeting = text.lower().strip(" !?.")
    if greeting in {"hi", "hello", "hey", "salom", "assalomu alaykum", "yo"}:
        await message.answer("👋 Salom! Men AgentOSman. Nima qilamiz?")
        return
    await _chat_or_task(message, text)




async def _media_task(message: Message, kind: str) -> None:
    """Turn Telegram media into a normal AgentOS task with durable media context."""
    bot = STATE.get("bot")
    if bot is None:
        await message.answer("⚠️ Media runtime is not ready yet. Please retry.")
        return
    try:
        suffix = ".bin"
        tg_file_id = None
        if kind == "voice" and message.voice:
            tg_file_id = message.voice.file_id; suffix = ".ogg"
        elif kind == "audio" and message.audio:
            tg_file_id = message.audio.file_id; suffix = mimetypes.guess_extension(message.audio.mime_type or "") or ".mp3"
        elif kind == "photo" and message.photo:
            tg_file_id = message.photo[-1].file_id; suffix = ".jpg"
        elif kind == "video" and message.video:
            tg_file_id = message.video.file_id; suffix = ".mp4"
        elif kind == "document" and message.document:
            tg_file_id = message.document.file_id; suffix = mimetypes.guess_extension(message.document.mime_type or "") or ".bin"
        if not tg_file_id:
            await message.answer("⚠️ I couldn't read that media.")
            return
        tg = await bot.get_file(tg_file_id)
        # Create the destination atomically to avoid mktemp race conditions.
        tmp = tempfile.NamedTemporaryFile(prefix="agentos_media_", suffix=suffix, delete=False)
        path = tmp.name
        tmp.close()
        await bot.download(tg, destination=path)

        analysis = ""
        if kind in ("voice", "audio", "photo"):
            try:
                from core.media_ingest import analyze
                analysis = await analyze(path, "audio" if kind in ("voice","audio") else "image")
            except Exception as exc:
                analysis = "Media analysis unavailable: " + type(exc).__name__

        caption = (message.caption or "").strip()
        prompt = (
            f"User sent a {kind} attachment. "
            f"Use the attached local media at {path} when tools support it. "
            f"Analyze it and answer the user's request. "
            f"Caption/request: {caption or '(no caption; infer the useful task)'}"
        )
        if analysis:
            prompt += "\n\nPre-analysis from multimodal ingestion:\n" + analysis[:12000]
        await _create_task_and_ack(message, prompt)
    except Exception as exc:
        log.exception("media ingestion failed")
        await message.answer("⚠️ Media received, but ingestion failed safely. Please retry once.")

@router.message(F.voice | F.audio)
async def voice_handler(message: Message):
    bot = STATE.get("bot") or message.bot
    tg_file_id = message.voice.file_id if message.voice else message.audio.file_id
    ext = ".ogg" if message.voice else ".mp3"
    status_msg = await message.answer("🎙️ _Ovoz tinglanmoqda va tahlil qilinmoqda..._", parse_mode="Markdown")
    try:
        tg = await bot.get_file(tg_file_id)
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp_path = tmp.name
        await bot.download(tg, destination=tmp_path)

        from core.voice_service import transcribe_audio
        text = await transcribe_audio(tmp_path)
        try:
            os.remove(tmp_path)
        except Exception:
            pass

        if not text:
            await status_msg.edit_text("⚠️ Ovozdan matn ajratib bo'lmadi.")
            return

        try:
            await status_msg.delete()
        except Exception:
            pass

        header = f"🎙️ *\"{text}\"*\n\n"
        await _chat_reply(message, text, prefix_header=header, allow_voice_reply=True)
    except Exception as e:
        log.exception("Voice handling failed: %s", e)
        await status_msg.edit_text(f"⚠️ Ovozni qayta ishlashda xatolik: {str(e)[:150]}")


@router.message(F.photo)
async def photo_handler(message: Message):
    bot = STATE.get("bot") or message.bot
    status_msg = await message.answer("🖼️ _Rasm yuklanmoqda va ko'rilmoqda..._", parse_mode="Markdown")
    try:
        tg_file_id = message.photo[-1].file_id
        tg = await bot.get_file(tg_file_id)
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp_path = tmp.name
        await bot.download(tg, destination=tmp_path)

        raw = Path(tmp_path).read_bytes()
        try:
            os.remove(tmp_path)
        except Exception:
            pass

        b64 = base64.b64encode(raw).decode("utf-8")
        caption = (message.caption or "").strip()
        query = caption or "Iltimos, ushbu rasmda nimalar borligini batafsil tahlil qilib bering."

        try:
            await status_msg.delete()
        except Exception:
            pass

        header = f"🖼️ *Rasm:* _{caption}_\n\n" if caption else "🖼️ *Rasm tahlili:*\n\n"
        await _multimodal_chat_reply(
            message=message,
            text_query=query,
            image_b64=b64,
            prefix_header=header,
        )
    except Exception as e:
        log.exception("Photo handling failed: %s", e)
        await status_msg.edit_text(f"⚠️ Rasmni tahlil qilishda xatolik: {str(e)[:150]}")


@router.message(F.video)
async def video_handler(message: Message):
    await _media_task(message, "video")


@router.message(F.document)
async def document_handler(message: Message):
    bot = STATE.get("bot") or message.bot
    doc = message.document
    filename = doc.file_name or "document.bin"
    suffix = Path(filename).suffix or ".bin"

    status_msg = await message.answer(f"📄 `{filename}` _o'qilmoqda..._", parse_mode="Markdown")
    try:
        tg = await bot.get_file(doc.file_id)
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp_path = tmp.name
        await bot.download(tg, destination=tmp_path)

        from core.document_parser import extract_text_from_file
        doc_text = extract_text_from_file(tmp_path, filename)
        try:
            os.remove(tmp_path)
        except Exception:
            pass

        if not doc_text.strip():
            await status_msg.edit_text(f"⚠️ `{filename}` faylidan matn ajratib bo'lmadi yoki fayl bo'sh.", parse_mode="Markdown")
            return

        try:
            await status_msg.delete()
        except Exception:
            pass

        caption = (message.caption or "").strip()
        user_query = caption or "Ushbu hujjatni tahlil qilib, asosiy mazmunini tushuntirib bering."
        combined = (
            f"[Biriktirilgan hujjat: {filename}]\n"
            f"--- Boshlanishi ---\n{doc_text[:12000]}\n--- Tugashi ---\n\n"
            f"Foydalanuvchi so'rovi: {user_query}"
        )
        header = f"📄 *{filename}* tahlili:\n\n"
        await _chat_reply(message, combined, prefix_header=header, display_query=f"{filename}: {user_query}")
    except Exception as e:
        log.exception("Document handling failed: %s", e)
        await status_msg.edit_text(f"⚠️ Hujjatni tahlil qilishda xatolik: {str(e)[:150]}")

async def _task_progress_loop(task_id: str, chat_id: int, message_id: int) -> None:
    tm: TaskManager = STATE["tm"]
    bot = STATE.get("bot")
    if bot is None:
        return
    last = None
    while True:
        try:
            row = await tm.get_task(task_id)
            if not row:
                return
            status = str(row.get("status") or "QUEUED")
            progress = float(row.get("progress") or 0.0)
            if status == "COMPLETED":
                progress = 100.0
            filled = int(max(0.0, min(100.0, progress)) / 5.0)
            bar = "█" * filled + "░" * (20 - filled)
            labels = {"QUEUED":"⏳ Queued","RUNNING":"🤖 Working","PAUSED":"⏸ Paused","WAITING_APPROVAL":"🔐 Waiting for approval","COMPLETED":"✅ Completed","FAILED":"❌ Failed","CANCELLED":"🛑 Cancelled"}
            label = labels.get(status, status)
            if status == "COMPLETED":
                text = "🤖 AgentOS\n\n" + bar + " 100%\n\n✅ Task completed\nID: " + task_id[:8]
            elif status == "FAILED":
                text = "🤖 AgentOS\n\n" + bar + " " + ("%.0f" % progress) + "%\n\n❌ Task failed\nID: " + task_id[:8] + "\n\nError: " + str(row.get("error") or "unknown")[:1000]
            else:
                text = "🤖 AgentOS\n\n" + bar + " " + ("%.0f" % progress) + "%\n\n" + label + "\nID: " + task_id[:8]
            if text != last:
                try:
                    await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text)
                    last = text
                except Exception:
                    pass
            if status in ("COMPLETED", "FAILED", "CANCELLED"):
                return
        except Exception as e:
            log.debug("task progress loop %s: %s", task_id, e)
        await asyncio.sleep(2)


async def _create_task_and_ack(message: Message, text: str):
    tm: TaskManager = STATE["tm"]
    q: TaskQueue = STATE["queue"]
    user_id = message.from_user.id if message.from_user else 0
    chat_id = message.chat.id
    tid = await tm.create_task(user_id=str(user_id), chat_id=str(chat_id),
                               description=text, entry_agent_id="coordinator-1")
    await q.enqueue(tid)
    await tm.set_status(tid, TaskStatus.QUEUED)
    await tm.audit(str(user_id), "task.created", {"task_id": tid})
    try:
        cm=ConversationMemory(tm.pool); await cm.ensure()
        await cm.append(user_id,chat_id,"user",text)
    except Exception: pass
    short = tid[:8]
    sent = await message.answer(
        "🤖 AgentOS\n\n"
        "░░░░░░░░░░░░░░░░░░░░ 0%\n\n"
        "⏳ Ishlayapman...")
    asyncio.create_task(_task_progress_loop(tid, chat_id, sent.message_id),
                        name="task-progress-" + short)


# ---------------- /tasks ----------------

@router.message(Command("tasks"))
async def cmd_tasks(message: Message):
    tm: TaskManager = STATE["tm"]
    uid = str(message.from_user.id) if message.from_user else "0"
    rows = await tm.list_tasks(user_id=uid, limit=15)
    if not rows:
        await message.answer("You have no tasks yet.")
        return
    lines = ["*Your recent tasks:*"]
    for r in rows:
        short = r["task_id"][:8]
        st = r["status"]
        desc = (r["description"] or "")[:50]
        lines.append(f"`{short}` [{st}] {desc}")
    await message.answer("\n".join(lines), parse_mode="Markdown")


# ---------------- /pause /resume /cancel /retry ----------------

@router.message(Command("pause"))
async def cmd_pause(message: Message, command: CommandObject):
    await _update_task(message, command.args, TaskStatus.PAUSED)


@router.message(Command("resume"))
async def cmd_resume(message: Message, command: CommandObject):
    tm: TaskManager = STATE["tm"]
    q: TaskQueue = STATE["queue"]
    tid = _resolve_task_id(command.args)
    if not tid:
        await message.answer("Usage: /resume <task_id>")
        return
    await tm.set_status(tid, TaskStatus.QUEUED)
    await q.enqueue(tid, {"resumed": "1"})
    await message.answer(f"▶️ Task `{tid[:8]}` resumed.", parse_mode="Markdown")


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, command: CommandObject):
    await _update_task(message, command.args, TaskStatus.CANCELLED)


@router.message(Command("retry"))
async def cmd_retry(message: Message, command: CommandObject):
    tm: TaskManager = STATE["tm"]
    q: TaskQueue = STATE["queue"]
    tid = _resolve_task_id(command.args)
    if not tid:
        await message.answer("Usage: /retry <task_id>")
        return
    await tm.set_status(tid, TaskStatus.QUEUED, error="")
    await tm.pool.execute(
        "UPDATE tasks SET retry_count = COALESCE(retry_count,0)+1 WHERE task_id=$1", tid)
    await q.enqueue(tid, {"manual_retry": "1"})
    await message.answer(f"🔁 Task `{tid[:8]}` re-queued.", parse_mode="Markdown")


@router.message(Command("stop"))
async def cmd_stop(message: Message, command: CommandObject):
    # /stop is an alias for /cancel
    await cmd_cancel(message, command)


async def _update_task(message: Message, args: Optional[str], status: TaskStatus):
    tm: TaskManager = STATE["tm"]
    tid = _resolve_task_id(args)
    if not tid:
        await message.answer(f"Usage: /{status.value.lower()} <task_id>")
        return
    await tm.set_status(tid, status)
    emoji = {"PAUSED": "⏸", "CANCELLED": "❌"}.get(status.value, "")
    await message.answer(f"{emoji} Task `{tid[:8]}` → {status.value}", parse_mode="Markdown")


def _resolve_task_id(args: Optional[str]) -> Optional[str]:
    if not args:
        return None
    args = args.strip()
    # Accept full UUID or 8-char prefix; caller-side resolve happens in tm.
    return args


# ---------------- /agents /agent ----------------

@router.message(Command("agents"))
async def cmd_agents(message: Message):
    orch = STATE["orch"]
    lines = ["*Registered agents:*"]
    for spec in orch.list_agents():
        lines.append(f"  • `{spec['agent_id']}` ({spec['role']}) — {spec['name']}")
    await message.answer("\n".join(lines), parse_mode="Markdown")


@router.message(Command("agent"))
async def cmd_agent(message: Message, command: CommandObject):
    orch = STATE["orch"]
    aid = (command.args or "").strip()
    a = orch.get_agent(aid)
    if not a:
        await message.answer(f"Agent `{aid}` not found.", parse_mode="Markdown")
        return
    await message.answer(
        f"*{a.spec.name}* (`{a.spec.agent_id}`)\n"
        f"Role: {a.spec.role.value}\nState: {a.state.value}\n"
        f"Tools: {', '.join(a.spec.tools) or '(none)'}\n"
        f"Handoffs: {', '.join(a.spec.handoff_targets) or '(none)'}",
        parse_mode="Markdown")


# ---------------- /logs ----------------

@router.message(Command("logs"))
async def cmd_logs(message: Message, command: CommandObject):
    tm: TaskManager = STATE["tm"]
    tid = _resolve_task_id(command.args)
    if not tid:
        await message.answer("Usage: /logs <task_id>")
        return
    # Resolve prefix → full id
    full = await _resolve_prefix(tm, tid)
    if not full:
        await message.answer("Task not found.")
        return
    events = await tm.list_events(full, limit=60)
    if not events:
        await message.answer("No events yet.")
        return
    lines = [f"*Logs for `{full[:8]}`*"]
    for e in events:
        ts = time.strftime("%H:%M:%S", time.localtime(e["created_at"]))
        data = e.get("data") or {}
        extra = ""
        if isinstance(data, dict):
            extra = ", ".join(f"{k}={str(v)[:30]}" for k, v in list(data.items())[:3])
        lines.append(f"`{ts}` {e['event_type']} {extra}")
    await message.answer("\n".join(lines[:60]), parse_mode="Markdown")


async def _resolve_prefix(tm: TaskManager, prefix: str) -> Optional[str]:
    if len(prefix) >= 32:
        return prefix
    assert tm.pool is not None
    async with tm.pool.acquire() as c:
        row = await c.fetchrow(
            "SELECT task_id FROM tasks WHERE task_id LIKE $1 LIMIT 1", prefix + "%")
    return row["task_id"] if row else None


# ---------------- /memory ----------------

@router.message(Command("memory"))
async def cmd_memory(message: Message):
    tm: TaskManager = STATE["tm"]
    uid = str(message.from_user.id) if message.from_user else "0"
    rows = await tm.list_tasks(user_id=uid, limit=5)
    lines = ["*Recent memory snapshot*", ""]
    for r in rows:
        lines.append(f"• `{r['task_id'][:8]}` — {(r['description'] or '')[:60]}")
    if not rows:
        lines.append("(no history yet)")
    await message.answer("\n".join(lines), parse_mode="Markdown")


# ---------------- /schedule ----------------

@router.message(Command("schedule"))
async def cmd_schedule(message: Message, command: CommandObject):
    tm: TaskManager = STATE["tm"]
    raw = (command.args or "").strip()
    if not raw or "::" not in raw:
        await message.answer(
            "Usage:\n"
            "  /schedule <cron> :: <description>\n"
            "  /schedule <YYYY-MM-DD HH:MM> :: <description>\n\n"
            "Examples:\n"
            "  /schedule 0 8 * * * :: Research latest AI news\n"
            "  /schedule 2026-06-01 18:00 :: Check Xcosmos deployment"
        )
        return
    when, _, desc = raw.partition("::")
    when = when.strip(); desc = desc.strip()
    uid = str(message.from_user.id); chat = str(message.chat.id)
    from croniter import croniter
    from datetime import datetime
    cron: Optional[str] = None
    run_at: Optional[float] = None
    next_run: Optional[float] = None
    if croniter.is_valid(when):
        cron = when
        next_run = croniter(when, datetime.now()).get_next(datetime).timestamp()
    else:
        try:
            dt = datetime.strptime(when, "%Y-%m-%d %H:%M")
            run_at = dt.timestamp()
            next_run = run_at
        except Exception:
            await message.answer("Could not parse the schedule. Try a cron expression "
                                 "or 'YYYY-MM-DD HH:MM'.")
            return
    jid = await tm.create_scheduled_job(uid, chat, desc, cron, run_at,
                                        entry_agent_id="coordinator-1",
                                        next_run=next_run)
    await message.answer(f"🗓 Scheduled `{jid[:8]}`.\n"
                         f"Recurring: {cron or 'no (one-shot)'}\n"
                         f"Next run: {time.strftime('%Y-%m-%d %H:%M', time.localtime(next_run))}",
                         parse_mode="Markdown")


# ---------------- /settings ----------------

@router.message(Command("settings"))
async def cmd_settings(message: Message):
    await message.answer(
        "*Settings*\n"
        "• Default model: `" + os.getenv("DEFAULT_MODEL", "gpt-4o-mini") + "`\n"
        "• Max task cost: $" + os.getenv("MAX_TASK_COST", "unlimited") + "\n"
        "• Max task duration: " + os.getenv("MAX_TASK_DURATION", "unlimited") + "\n"
        "• Auto-approve: " + os.getenv("AUTO_APPROVE", "false") + "\n"
        "\nTo change, edit `.env` on the server and restart.",
        parse_mode="Markdown")


# ---------------- approvals / callbacks ----------------

@router.callback_query(F.data.startswith("appr:"))
async def cb_approval(cb: CallbackQuery):
    gate: ApprovalGate = STATE["approval"]
    _, decision, request_id = cb.data.split(":", 2)
    ok = gate.resolve(request_id, approved=(decision == "yes"),
                      decided_by=f"tg:{cb.from_user.id}")
    await cb.answer("Approved" if decision == "yes" else "Denied", show_alert=False)
    if ok:
        await cb.message.edit_reply_markup(reply_markup=None)


@router.callback_query(F.data.startswith("task:"))
async def cb_task_action(cb: CallbackQuery):
    tm: TaskManager = STATE["tm"]
    q: TaskQueue = STATE["queue"]
    _, action, task_id = cb.data.split(":", 2)
    if action == "pause":
        await tm.set_status(task_id, TaskStatus.PAUSED)
    elif action == "resume":
        await tm.set_status(task_id, TaskStatus.QUEUED)
        await q.enqueue(task_id)
    elif action == "cancel":
        await tm.set_status(task_id, TaskStatus.CANCELLED)
    elif action == "retry":
        await tm.set_status(task_id, TaskStatus.QUEUED, error="")
        await q.enqueue(task_id)
    await cb.answer(f"{action} ok")


# ---------------- periodic notifier entry (called by worker/other services) ----

async def notify(chat_id: int | str, text: str, reply_markup=None):
    await STATE["notifier"].send(chat_id, text, reply_markup)


# ---- chat vs task routing (added by final pass) ----
import re as _re

_TASK_RE = _re.compile(
    r"\b(research|build|create|make|generate|write|code|develop|implement|deploy|"
    r"analy[sz]e|scrape|crawl|download|summari[sz]e|translate|draw|design|compare|"
    r"find|search|fix|debug|refactor|convert|plan|"
    r"yarat|tuz|yoz|qil|top|tahlil|izla|yuklab|chiz|"
    r"сделай|создай|напиши|найди|исследуй|разработай|проанализируй|сгенерируй|нарисуй)\b",
    _re.I)
_TASK_NOUNS = _re.compile(
    r"\b(image|video|pdf|docx|excel|spreadsheet|website|app|script|report|file|"
    r"rasm|sayt|ilova|fayl|hisobot|картинк|видео|сайт|приложени|файл|отчет|отчёт)\w*", _re.I)
_FORCE_RE = _re.compile(r"^\s*(?:/task|task:|vazifa:|задача:)\s*(.+)$", _re.I | _re.S)
_CHAT_HISTORY: dict = {}


def _looks_like_task(text: str) -> bool:
    t = (text or "").strip()
    if len(t) > 400:
        return True
    return bool(_TASK_RE.search(t) and (_TASK_NOUNS.search(t) or len(t.split()) >= 6)
                and not _re.match(r"^(what|who|why|how|when|nima|qanday|nega|кто|что|как|почему)\b", t, _re.I))


async def _chat_or_task(message, text: str) -> None:
    m = _FORCE_RE.match(text)
    if m:
        await _create_task_and_ack(message, m.group(1).strip())
        return
    if _looks_like_task(text):
        await _create_task_and_ack(message, text)
        return
    await _chat_reply(message, text)


_PERSONA = (
    "Sen foydalanuvchining shaxsiy AI yordamchisisan (AgentOS). Do'stona, norasmiy va qisqa gapir: "
    "foydalanuvchiga 'sen' deb murojaat qil, 'Sizga qanday yordam bera olaman?' kabi rasmiy iboralardan qoch. "
    "Foydalanuvchi qaysi tilda yozsa (o'zbek, rus, ingliz) shu tilda, shunday norasmiy ohangda javob ber. "
    "Sening doimiy xotirang bor: pastdagi 'Foydalanuvchi haqida bilganlaring' va oxirgi suhbat tarixi. "
    "Hech qachon 'suhbatni eslab qolmayman' dema. Bilmagan narsangni o'ylab topma. "
    "Agar foydalanuvchi haqiqiy ish so'rasa (sayt, rasm, taqdimot, kod, tadqiqot), uni aniq tasvirlashini ayt, "
    "shunda sen buni vazifa sifatida ishga tushirasan."
)
_FACT_CUES = _re.compile(
    r"(mening|menga|ismim|yashayman|ishlayman|o'qiyman|yoqadi|eslab qol|esingda tut|"
    r"меня зовут|я живу|я работаю|люблю|запомни|my name|i am|i'm|i live|i work|i like|i love|remember)", _re.I)
_BG: set = set()
_CONV_READY: dict = {}


def _uid(message) -> str:
    return str(message.from_user.id if message.from_user else message.chat.id)


async def _load_profile(pool, uid: str) -> list:
    try:
        async with pool.acquire() as c:
            rows = await c.fetch(
                "SELECT content FROM memory_items WHERE user_id=$1 AND kind='profile_fact' "
                "ORDER BY created_at DESC LIMIT 25", uid)
        return [r["content"] for r in rows]
    except Exception as e:
        log.warning("profile load failed: %s", e)
        return []


async def _load_history(pool, uid: str, chat_id: int, limit: int = 12) -> list:
    try:
        if not _CONV_READY.get("ok"):
            from core.conversation_memory import ConversationMemory
            await ConversationMemory(pool).ensure()
            _CONV_READY["ok"] = True
        async with pool.acquire() as c:
            rows = await c.fetch(
                "SELECT role,content FROM conversation_messages WHERE user_id=$1 AND chat_id=$2 "
                "ORDER BY created_at DESC LIMIT $3", uid, str(chat_id), limit)
        return [{"role": r["role"], "content": str(r["content"])[:1500]} for r in reversed(rows)]
    except Exception as e:
        log.warning("history load failed: %s", e)
        return []


async def _save_turn(pool, uid: str, chat_id: int, user_text: str, answer: str) -> None:
    try:
        from core.conversation_memory import ConversationMemory
        cm = ConversationMemory(pool)
        await cm.append(uid, str(chat_id), "user", user_text)
        await cm.append(uid, str(chat_id), "assistant", answer)
    except Exception as e:
        log.warning("history save failed: %s", e)


async def _extract_facts(router, tm, uid: str, text: str) -> None:
    try:
        comp = await asyncio.wait_for(router.complete(
            [{"role": "system", "content": (
                "Extract durable personal facts about the user from their message (name, city, job, "
                "projects, preferences, goals). Return ONLY JSON like {\"facts\": [\"short fact\"]}. "
                "Use the user's own language. Return {\"facts\": []} if there is nothing durable.")},
             {"role": "user", "content": text}],
            task_type="general", max_tokens=200, temperature=0.0), timeout=40)
        raw = (comp.text or "").replace("```json", "").replace("```", "").strip()
        facts = json.loads(raw).get("facts", [])
        existing = set(await _load_profile(tm.pool, uid))
        for f in facts[:5]:
            f = str(f).strip()[:200]
            if f and f not in existing:
                await tm.remember(uid, f, kind="profile_fact", importance=0.9)
    except Exception as e:
        log.info("fact extraction skipped: %s", e)


_LIVE_SEARCH_CUES = _re.compile(
    r"\b(bugun|hozir|yangilik|ob-havo|obhavo|kurs|dollar|narx|qidir|izla|top|"
    r"today|news|weather|price|search|find|current|latest|"
    r"сегодня|новости|погода|курс|доллар|цена|найди|поиск)\b", _re.I)


async def _stream_and_render_reply(
    message: Message,
    msgs: list,
    task_type: str = "general",
    prefix_header: str = "",
    allow_voice_reply: bool = False,
) -> str:
    router = STATE.get("router")
    bot = STATE.get("bot") or message.bot
    if router is None:
        await message.answer("⚠️ AI runtime is not ready yet. Please retry in a moment.")
        return ""

    try:
        await bot.send_chat_action(message.chat.id, "typing")
    except Exception:
        pass

    status_msg = await message.answer("💬 _O'ylayapman..._", parse_mode="Markdown")
    full_text = ""
    last_edit_time = time.time()
    last_len = 0

    try:
        async for chunk in router.stream(msgs, task_type=task_type, max_tokens=1500, temperature=0.5):
            full_text += chunk
            now = time.time()
            if (now - last_edit_time >= 1.3) and (len(full_text) - last_len >= 8):
                disp = (prefix_header + full_text).strip()
                if len(disp) <= 3900:
                    try:
                        await bot.edit_message_text(
                            chat_id=message.chat.id,
                            message_id=status_msg.message_id,
                            text=disp + " ▌",
                            parse_mode=None
                        )
                        last_edit_time = now
                        last_len = len(full_text)
                    except Exception:
                        pass
    except Exception as e:
        log.warning("router stream interrupted: %s, falling back to complete()", e)
        if not full_text:
            try:
                comp = await asyncio.wait_for(
                    router.complete(msgs, task_type=task_type, max_tokens=1000, temperature=0.5),
                    timeout=60
                )
                full_text = comp.text or "(javob bo'sh bo'ldi)"
            except Exception as e2:
                full_text = f"⚠️ Xatolik yuz berdi: {str(e2)[:150]}"

    final_disp = (prefix_header + full_text).strip() or "(bo'sh javob)"

    # Final rendering
    if len(final_disp) <= 4000:
        try:
            await bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=status_msg.message_id,
                text=final_disp,
                parse_mode="Markdown"
            )
        except Exception:
            try:
                await bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=status_msg.message_id,
                    text=final_disp,
                    parse_mode=None
                )
            except Exception:
                pass
    else:
        try:
            await bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=status_msg.message_id,
                text=final_disp[:3800],
                parse_mode=None
            )
            for i in range(3800, len(final_disp), 3800):
                await message.answer(final_disp[i:i+3800], parse_mode=None)
        except Exception:
            pass

    # Optional voice note audio response
    if allow_voice_reply and _VOICE_PREF.get(message.chat.id, False):
        try:
            from core.voice_service import text_to_speech
            speech_bytes = await text_to_speech(full_text[:900])
            if speech_bytes:
                await message.answer_voice(BufferedInputFile(speech_bytes, filename="voice_reply.ogg"))
        except Exception as e:
            log.warning("Voice TTS playback failed: %s", e)

    return full_text


async def _chat_reply(
    message: Message,
    text: str,
    prefix_header: str = "",
    allow_voice_reply: bool = False,
    display_query: str = "",
) -> None:
    router = STATE.get("router")
    tm = STATE.get("tm")
    if router is None:
        await message.answer("⚠️ AI runtime is not ready yet. Please retry in a moment.")
        return

    pool = getattr(tm, "pool", None)
    uid = _uid(message)
    facts = await _load_profile(pool, uid) if pool else []
    hist = await _load_history(pool, uid, message.chat.id) if pool else _CHAT_HISTORY.setdefault(message.chat.id, [])[-10:]

    system = _PERSONA + "\n\nFoydalanuvchi haqida bilganlaring:\n" + (
        "\n".join("- " + f for f in facts) if facts else "(hozircha hech narsa)")

    # Smart auto-search if query asks for current info
    if _LIVE_SEARCH_CUES.search(text) and len(text.split()) >= 2:
        try:
            from core.web_research import search_web
            s_res = search_web(text[:100], max_results=3)
            if s_res.get("results"):
                snippets = "\n".join(f"- {r['title']}: {r['snippet']}" for r in s_res["results"])
                system += f"\n\nJonli internet ma'lumotlari:\n{snippets}"
        except Exception as ex:
            log.debug("Auto search failed: %s", ex)

    msgs = [{"role": "system", "content": system}] + hist + [{"role": "user", "content": text}]

    answer = await _stream_and_render_reply(
        message=message,
        msgs=msgs,
        task_type="general",
        prefix_header=prefix_header,
        allow_voice_reply=allow_voice_reply,
    )

    save_text = display_query or text
    if pool and answer:
        await _save_turn(pool, uid, message.chat.id, save_text, answer)
        if _FACT_CUES.search(text):
            t = asyncio.create_task(_extract_facts(router, tm, uid, text))
            _BG.add(t)
            t.add_done_callback(_BG.discard)
    elif answer:
        h = _CHAT_HISTORY.setdefault(message.chat.id, [])
        h += [{"role": "user", "content": save_text}, {"role": "assistant", "content": answer}]
        del h[:-20]


async def _multimodal_chat_reply(
    message: Message,
    text_query: str,
    image_b64: str,
    prefix_header: str = "",
) -> None:
    router = STATE.get("router")
    tm = STATE.get("tm")
    if router is None:
        await message.answer("⚠️ AI runtime is not ready yet. Please retry in a moment.")
        return

    pool = getattr(tm, "pool", None)
    uid = _uid(message)
    facts = await _load_profile(pool, uid) if pool else []
    system = _PERSONA + "\n\nFoydalanuvchi haqida bilganlaring:\n" + (
        "\n".join("- " + f for f in facts) if facts else "(hozircha hech narsa)")

    msgs = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": text_query},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
            ],
        },
    ]

    answer = await _stream_and_render_reply(
        message=message,
        msgs=msgs,
        task_type="vision",
        prefix_header=prefix_header,
        allow_voice_reply=False,
    )

    if pool and answer:
        await _save_turn(pool, uid, message.chat.id, f"[Rasm] {text_query}", answer)
    elif answer:
        h = _CHAT_HISTORY.setdefault(message.chat.id, [])
        h += [{"role": "user", "content": f"[Rasm] {text_query}"}, {"role": "assistant", "content": answer}]
        del h[:-20]
