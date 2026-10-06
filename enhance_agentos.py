from pathlib import Path
import json

def patch_file(name, fn):
    p = Path(name)
    s = p.read_text(encoding="utf-8")
    s2 = fn(s)
    if s2 != s:
        p.write_text(s2, encoding="utf-8")
        print("patched", name)

def worker(s):
    if "_monitor_task_progress" in s:
        return s
    marker = '        await self.tm.set_status(task_id, TaskStatus.RUNNING, current_step="starting")'
    helper = '''
    async def _monitor_task_progress(self, task_id: str) -> None:
        seen = 0
        step_ok = 0
        plan_steps = 0
        while True:
            try:
                evs = await self.events.get_events("task:" + task_id, after_version=seen)
                if evs:
                    seen = evs[-1].version
                for ev in evs:
                    et = ev.type
                    data = ev.data or {}
                    if et == "kernel.started":
                        await self.tm.set_status(task_id, TaskStatus.RUNNING, current_step="starting", progress=0.05)
                    elif et == "kernel.perceived":
                        await self.tm.set_status(task_id, TaskStatus.RUNNING, current_step="understanding", progress=0.12)
                    elif et == "kernel.planned":
                        plan_steps = max(plan_steps, len(data.get("steps") or []))
                        await self.tm.set_status(task_id, TaskStatus.RUNNING, current_step="planning", progress=0.20)
                    elif et == "kernel.step_ok":
                        step_ok += 1
                        denom = max(plan_steps, step_ok + 1)
                        progress = min(0.88, 0.20 + 0.65 * (step_ok / denom))
                        await self.tm.set_status(task_id, TaskStatus.RUNNING, current_step=str(data.get("step") or "executing"), progress=progress)
                    elif et == "kernel.reflection":
                        await self.tm.set_status(task_id, TaskStatus.RUNNING, current_step="self-correcting", progress=0.72)
                    elif et == "kernel.handoff":
                        await self.tm.set_status(task_id, TaskStatus.RUNNING, current_step="delegating", progress=0.55)
                    elif et == "kernel.completed":
                        await self.tm.set_status(task_id, TaskStatus.RUNNING, current_step="finalizing", progress=0.95)
            except Exception as exc:
                log.debug("progress monitor: %s", exc)
            row = await self.tm.get_task(task_id)
            if row and row.get("status") in ("COMPLETED", "FAILED", "CANCELLED"):
                return
            await asyncio.sleep(1)

'''
    s = s.replace(marker, helper + marker, 1)
    s = s.replace(marker, '        await self.tm.set_status(task_id, TaskStatus.RUNNING, current_step="starting", progress=0.05)', 1)
    old = '        try:\n            # Snapshot before running.'
    new = '        monitor = asyncio.create_task(self._monitor_task_progress(task_id))\n        try:\n            # Snapshot before running.'
    s = s.replace(old, new, 1)
    old = '            result: TaskResult = await self.orch.run_task(task, entry_agent)\n            # Save result and status.'
    new = '            result: TaskResult = await self.orch.run_task(task, entry_agent)\n            artifacts = result.artifacts or []\n            result_text = result.summary or ""\n            if artifacts:\n                result_text += "\\\\n\\\\n__ARTIFACTS_JSON__\\\\n" + json.dumps(artifacts, ensure_ascii=False, default=str)\n            # Save result and status.'
    s = s.replace(old, new, 1)
    s = s.replace("result=result.summary,\n                                         progress=1.0", "result=result_text,\n                                         progress=1.0", 1)
    s = s.replace("result=result.summary)\n            await self.tm.save_checkpoint", "result=result_text, progress=1.0)\n            await self.tm.save_checkpoint", 1)
    return s

def notifier(s):
    if "send_artifact" in s:
        return s
    s = s.replace("import logging, os", "import logging, os, mimetypes\nfrom pathlib import Path", 1)
    s = s.replace("from aiogram.enums import ParseMode", "from aiogram.enums import ParseMode\nfrom aiogram.types import FSInputFile", 1)
    anchor = "\n\n_notifier:"
    method = '''
    async def send_artifact(self, chat_id: int | str, artifact: dict) -> None:
        if not self._bot:
            return
        url = artifact.get("url") or artifact.get("link") or artifact.get("download_url")
        path = artifact.get("path") or artifact.get("file_path")
        name = str(artifact.get("name") or artifact.get("filename") or "artifact")
        kind = str(artifact.get("type") or artifact.get("kind") or "").lower()
        try:
            if url:
                await self._bot.send_message(int(chat_id), "Artifact: " + name + "\\n" + url)
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
'''
    return s.replace(anchor, method + anchor, 1)

def bot_patch(s):
    if "__ARTIFACTS_JSON__" not in s:
        start = s.find("async def _status_notifier_loop")
        end = s.find("\n\nif __name__ == '__main__':", start)
        if end < 0:
            end = s.find('\n\nif __name__ == "__main__":', start)
        if start >= 0 and end >= 0:
            fn = '''async def _status_notifier_loop(tm: TaskManager, notifier) -> None:
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
                    await notifier.send(chat_id, "❌ Task failed " + short + "\\n\\nError: " + str(r.get("error") or "unknown")[:2500])
                    continue
                if r["status"] == "WAITING_APPROVAL":
                    await notifier.send(chat_id, "🔐 Task " + short + " is waiting for approval.")
                    continue
                raw = r.get("result") or ""
                artifact_json = ""
                if "__ARTIFACTS_JSON__" in raw:
                    raw, artifact_json = raw.split("__ARTIFACTS_JSON__", 1)
                elapsed = (r.get("completed_at") or 0) - (r.get("started_at") or 0)
                await notifier.send(chat_id, "✅ Task completed " + short + "\\n⏱ " + str(int(elapsed)) + "s\\n\\n" + raw.strip()[:3000])
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
'''
            s = s[:start] + fn + s[end:]
    if "BotCommand" not in s:
        s = s.replace("from aiogram import Bot, Dispatcher", "from aiogram import Bot, Dispatcher\nfrom aiogram.types import BotCommand", 1)
    if "set_my_commands" not in s:
        menu = '''    await bot.set_my_commands([
        BotCommand(command="start", description="Open AgentOS"),
        BotCommand(command="task", description="Create a task"),
        BotCommand(command="tasks", description="My tasks"),
        BotCommand(command="status", description="System status"),
        BotCommand(command="agents", description="AI agents"),
        BotCommand(command="pause", description="Pause task"),
        BotCommand(command="resume", description="Resume task"),
        BotCommand(command="cancel", description="Cancel task"),
        BotCommand(command="retry", description="Retry task"),
        BotCommand(command="help", description="Help"),
    ])
'''
        s = s.replace("    dp = Dispatcher()", "    dp = Dispatcher()\n" + menu, 1)
    return s

patch_file("telegram_bot/bot.py", bot_patch)

patch_file("core/worker.py", worker)
patch_file("telegram_bot/notifier.py", notifier)
print("enhancement patch complete")


def expert_layer():
    p = Path("core/expert_layer.py")
    if p.exists():
        return
    p.write_text(r'''from __future__ import annotations
import json
from typing import List, Dict

SKILLS: Dict[str, str] = {
    "polymath": "cross-domain synthesis",
    "business_coach": "business strategy and decisions",
    "startup_builder": "startup, product and execution",
    "idea_creative": "creative ideation and alternatives",
    "security": "defensive security and secure engineering",
    "biohacker": "evidence-based performance and habits",
    "engineer": "software, hardware and systems engineering",
    "psychologist": "evidence-informed behavior and communication",
    "chess_master": "chess calculation and strategy",
    "profiler": "behavioral pattern analysis from supplied evidence",
    "stoic": "Stoic decision framing",
    "dr_house": "diagnostic-style hypothesis testing without unsupported diagnosis",
    "crypto_bro": "crypto/blockchain analysis with risk disclosure",
    "global_vibes": "global culture, trends and cross-cultural context",
}

SYSTEM = """You are AgentOS Expert, the user's private polymathic executive AI.
Answer in the SAME LANGUAGE as the latest user message.
Use relevant persistent conversation/task context.
For current or changing facts, research first when a research tool is available.
Separate verified facts, inference and uncertainty. Never invent sources or results.
Before declaring work finished, verify the actual output.
Prefer fast reliable execution and parallelize independent work.
Do not ask unnecessary clarification; infer sensible defaults.
Combine multiple skills when useful.
For medical, legal, financial or security-sensitive topics, be evidence-based and explicit about uncertainty and safety limits.
Available skills: """ + json.dumps(SKILLS, ensure_ascii=False)

def select(text: str) -> List[str]:
    t = text.lower()
    aliases = {
      "business_coach":["business","strategy","negotiation"],
      "startup_builder":["startup","mvp","founder","product"],
      "idea_creative":["idea","creative","brainstorm","invent"],
      "security":["security","cyber","pentest"],
      "biohacker":["biohack","sleep","nutrition","performance"],
      "engineer":["engineer","engineering","hardware","architecture","debug"],
      "psychologist":["psychology","behavior","relationship"],
      "chess_master":["chess","shaxmat"],
      "profiler":["profile","profiling","behavioral"],
      "stoic":["stoic","stoicism"],
      "dr_house":["diagnostic","diagnose","symptom"],
      "crypto_bro":["crypto","bitcoin","ethereum","blockchain"],
      "global_vibes":["global","culture","trend","geopolitics"],
    }
    found=[k for k,v in aliases.items() if any(x in t for x in v)]
    return found or ["polymath","engineer"]

def context(text: str) -> str:
    return "\n\nEXPERT MODE:\n" + SYSTEM + "\nSelected skills: " + ", ".join(select(text))
''', encoding="utf-8")
    print("created core/expert_layer.py")

def patch_expert_prompts(s):
    if "expert_layer" in s:
        return s
    s=s.replace("from .models import Context, Plan, Reflection, Task",
                "from .models import Context, Plan, Reflection, Task\nfrom .expert_layer import context as expert_context",1)
    s=s.replace('return f"""{PLANNER_SYSTEM}', 'return f"""{PLANNER_SYSTEM}\\n{expert_context(task.description)}',1)
    s=s.replace('return f"""{REASONING_SYSTEM}', 'return f"""{REASONING_SYSTEM}\\n{expert_context(step_prompt)}',1)
    s=s.replace('return f"""{SYNTHESIS_SYSTEM}', 'return f"""{SYNTHESIS_SYSTEM}\\n{expert_context(task.description)}',1)
    return s

expert_layer()
patch_file("core/prompts.py", patch_expert_prompts)

def add_memory():
    p=Path("core/conversation_memory.py")
    if not p.exists():
        p.write_text('''import json,time
class ConversationMemory:
    def __init__(self,pool): self.pool=pool
    async def ensure(self):
        async with self.pool.acquire() as c:
            await c.execute("""CREATE TABLE IF NOT EXISTS conversation_messages (id BIGSERIAL PRIMARY KEY,user_id TEXT NOT NULL,chat_id TEXT NOT NULL,role TEXT NOT NULL,content TEXT NOT NULL,created_at DOUBLE PRECISION NOT NULL); CREATE INDEX IF NOT EXISTS conv_idx ON conversation_messages(user_id,chat_id,created_at);""")
    async def append(self,user_id,chat_id,role,content):
        async with self.pool.acquire() as c:
            await c.execute("INSERT INTO conversation_messages(user_id,chat_id,role,content,created_at) VALUES($1,$2,$3,$4,$5)",str(user_id),str(chat_id),role,str(content),time.time())
    async def render(self,user_id,chat_id,limit=20):
        async with self.pool.acquire() as c:
            rows=await c.fetch("SELECT role,content FROM conversation_messages WHERE user_id=$1 AND chat_id=$2 ORDER BY created_at DESC LIMIT $3",str(user_id),str(chat_id),limit)
        return "\\n".join("[{}] {}".format(r["role"],str(r["content"])[:1200]) for r in reversed(rows))
''',encoding="utf-8")
def patch_memory_worker(s):
    if "conversation_memory" in s: return s
    s=s.replace("from .health import Heartbeat","from .health import Heartbeat\nfrom .conversation_memory import ConversationMemory",1)
    s=s.replace("        self.hb: Optional[Heartbeat] = None","        self.hb: Optional[Heartbeat] = None\n        self.conv=None",1)
    s=s.replace("        await self.tm.connect()","        await self.tm.connect()\n        self.conv=ConversationMemory(self.tm.pool)\n        await self.conv.ensure()",1)
    old='''        task = Task(id=task_id, description=row["description"],
                    domain=row.get("domain") or "general")'''
    new='''        description=row["description"]
        if self.conv and row.get("chat_id") and row.get("user_id"):
            try:
                h=await self.conv.render(row["user_id"],row["chat_id"])
                if h: description="Past conversation:\\n"+h+"\\n\\nTask:\\n"+description
            except Exception: pass
        task = Task(id=task_id, description=description,
                    domain=row.get("domain") or "general")'''
    return s.replace(old,new,1)
def patch_memory_handlers(s):
    if "conversation_memory" in s: return s
    s=s.replace("from .notifier import Notifier","from .notifier import Notifier\nfrom core.conversation_memory import ConversationMemory",1)
    marker='    await tm.audit(str(user_id), "task.created", {"task_id": tid})'
    repl=marker+'''\n    try:
        cm=ConversationMemory(tm.pool); await cm.ensure()
        await cm.append(user_id,chat_id,"user",text)
    except Exception: pass'''
    return s.replace(marker,repl,1)
add_memory()
patch_file("core/worker.py",patch_memory_worker)
patch_file("telegram_bot/handlers.py",patch_memory_handlers)
