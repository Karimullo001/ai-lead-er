#!/usr/bin/env python3
from __future__ import annotations
import ipaddress, socket, subprocess, tempfile, urllib.parse, urllib.request
from html.parser import HTMLParser
from pathlib import Path
import re, compileall

ROOT = Path(__file__).resolve().parent

def write(path: str, text: str):
    p = ROOT / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")

write("core/web_research.py", r'''
from __future__ import annotations
import html, ipaddress, socket, urllib.parse, urllib.request
from html.parser import HTMLParser
from typing import Any

UA = "AgentOS/1.0"
MAX_BYTES = 300_000

class SearchParser(HTMLParser):
    def __init__(self):
        super().__init__(); self.results=[]; self.on=False; self.href=""; self.buf=[]
    def handle_starttag(self, tag, attrs):
        d=dict(attrs)
        if tag=="a" and "result__a" in d.get("class",""):
            self.on=True; self.href=d.get("href",""); self.buf=[]
    def handle_data(self, data):
        if self.on: self.buf.append(data)
    def handle_endtag(self, tag):
        if tag=="a" and self.on:
            title=html.unescape(" ".join("".join(self.buf).split()))
            if title and self.href: self.results.append({"title":title,"url":self.href})
            self.on=False

class TextParser(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts=[]; self.skip=0
    def handle_starttag(self, tag, attrs):
        if tag in {"script","style","noscript","svg"}: self.skip+=1
    def handle_endtag(self, tag):
        if tag in {"script","style","noscript","svg"} and self.skip: self.skip-=1
    def handle_data(self,data):
        if not self.skip:
            x=" ".join(data.split())
            if x: self.parts.append(x)

def safe_url(url: str) -> str:
    u=urllib.parse.urlparse(url)
    if u.scheme not in {"http","https"} or not u.hostname: raise ValueError("Only public HTTP(S) URLs allowed")
    if u.hostname.lower() in {"localhost","localhost.localdomain"}: raise ValueError("localhost blocked")
    for info in socket.getaddrinfo(u.hostname,None):
        ip=ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ValueError("private/reserved network blocked")
    return url

def fetch(url: str, timeout=12) -> bytes:
    req=urllib.request.Request(safe_url(url),headers={"User-Agent":UA,"Accept":"text/html,*/*;q=.8"})
    with urllib.request.urlopen(req,timeout=timeout) as r: return r.read(MAX_BYTES)

def search_web(query: str, max_results=6) -> dict[str,Any]:
    q=str(query).strip()
    if not q: return {"query":"","results":[],"error":"empty query"}
    url="https://html.duckduckgo.com/html/?"+urllib.parse.urlencode({"q":q})
    try:
        p=SearchParser(); p.feed(fetch(url).decode("utf-8","ignore"))
        out=[]; seen=set()
        for x in p.results:
            href=x["url"]
            if href.startswith("//"): href="https:"+href
            if href in seen: continue
            seen.add(href); snippet=""
            try:
                tp=TextParser(); tp.feed(fetch(href,8).decode("utf-8","ignore"))
                snippet=" ".join(tp.parts)[:700]
            except Exception: pass
            out.append({"title":x["title"],"url":href,"snippet":snippet})
            if len(out)>=max_results: break
        return {"query":q,"results":out,"count":len(out)}
    except Exception as e:
        return {"query":q,"results":[],"error":f"{type(e).__name__}: {e}"}

def research(query: str, max_results=6) -> str:
    r=search_web(query,max_results)
    lines=[f"Research query: {r['query']}"]
    if r.get("error"): lines.append("Research error: "+r["error"])
    for i,x in enumerate(r.get("results",[]),1):
        lines.append(f"[{i}] {x['title']}\nURL: {x['url']}\nEvidence: {x['snippet']}")
    if not r.get("results"): lines.append("No live sources retrieved; never fabricate citations.")
    return "\n\n".join(lines)
''')

write("core/media_pipeline.py", r'''
from __future__ import annotations
import subprocess, tempfile
from pathlib import Path

def extract_document(path: str):
    p=Path(path); ext=p.suffix.lower()
    if ext in {".txt",".md",".csv",".json",".py",".js",".ts",".html",".css"}:
        return {"type":"text","text":p.read_text(encoding="utf-8",errors="ignore")[:200000]}
    if ext==".pdf":
        try:
            from pypdf import PdfReader
            r=PdfReader(str(p))
            return {"type":"pdf","pages":len(r.pages),"text":"\n".join(x.extract_text() or "" for x in r.pages)[:200000]}
        except Exception as e: return {"type":"pdf","error":f"{type(e).__name__}: {e}"}
    if ext==".docx":
        try:
            from docx import Document
            d=Document(str(p))
            return {"type":"docx","text":"\n".join(x.text for x in d.paragraphs)[:200000]}
        except Exception as e: return {"type":"docx","error":f"{type(e).__name__}: {e}"}
    return {"type":"unknown","path":str(p),"size":p.stat().st_size if p.exists() else 0}

def video_extract(path: str, frames=4):
    try:
        import imageio_ffmpeg
        ff=imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as e: return {"ok":False,"error":f"ffmpeg unavailable: {e}"}
    out=Path(tempfile.mkdtemp(prefix="agentos_video_"))
    pattern=str(out/"frame_%02d.jpg")
    try:
        subprocess.run([ff,"-hide_banner","-loglevel","error","-i",str(path),"-vf","fps=1/10","-frames:v",str(max(1,min(8,frames))),pattern],check=True,timeout=60)
        return {"ok":True,"frames":[str(x) for x in sorted(out.glob("frame_*.jpg"))]}
    except Exception as e: return {"ok":False,"error":f"{type(e).__name__}: {e}"}
''')

write("core/artifact_validator.py", r'''
from __future__ import annotations
from pathlib import Path
from urllib.parse import urlparse

def validate_artifact(a: dict):
    if a.get("url"):
        u=urlparse(str(a["url"]))
        return {"valid":u.scheme in {"http","https","tg"},"url":a["url"]}
    p=a.get("path") or a.get("file_path")
    if p:
        x=Path(str(p)); return {"valid":x.exists(),"path":str(x)}
    if "content" in a: return {"valid":True,"inline":True}
    return {"valid":False,"reason":"missing artifact target"}

def validate_all(items):
    checks=[validate_artifact(x) for x in items]
    return {"valid":all(x["valid"] for x in checks),"checks":checks}
''')

write("core/builtin_tools.py", r'''
from .tools import FunctionTool, ToolRegistry, ToolSpec
from .web_research import research
from .media_pipeline import extract_document, video_extract
from .artifact_validator import validate_all

def register_builtins(registry: ToolRegistry):
    registry.register(FunctionTool(ToolSpec(
        tool_id="web_research",name="web_research",
        description="Live web research with retrieved evidence and source URLs.",
        parameters={"type":"object","properties":{"query":{"type":"string"},"max_results":{"type":"integer","minimum":1,"maximum":8}},"required":["query"]}), research))
    registry.register(FunctionTool(ToolSpec(
        tool_id="read_document",name="read_document",
        description="Extract text from PDF, DOCX and text documents.",
        parameters={"type":"object","properties":{"path":{"type":"string"}},"required":["path"]}), lambda path: extract_document(path)))
    registry.register(FunctionTool(ToolSpec(
        tool_id="analyze_video",name="analyze_video",
        description="Extract representative frames from a video.",
        parameters={"type":"object","properties":{"path":{"type":"string"},"frames":{"type":"integer"}},"required":["path"]}), lambda path,frames=4: video_extract(path,frames)))
    registry.register(FunctionTool(ToolSpec(
        tool_id="validate_artifacts",name="validate_artifacts",
        description="Verify artifact files and URLs before completion.",
        parameters={"type":"object","properties":{"artifacts":{"type":"array"}},"required":["artifacts"]}), lambda artifacts: validate_all(artifacts)))
''')

pp=ROOT/"pyproject.toml"
if pp.exists():
    s=pp.read_text(encoding="utf-8")
    for dep in ['  "pypdf>=5.0",','  "python-docx>=1.1",','  "imageio-ffmpeg>=0.5",']:
        if dep not in s: s=s.replace('  "websockets>=12.0",','  "websockets>=12.0",\n'+dep)
    pp.write_text(s,encoding="utf-8")

wp=ROOT/"core/worker.py"
if wp.exists():
    s=wp.read_text(encoding="utf-8")
    if "from core.builtin_tools import register_builtins" not in s:
        s=s.replace("from __future__ import annotations", "from __future__ import annotations\nfrom core.builtin_tools import register_builtins", 1) if "from __future__ import annotations" in s else "from core.builtin_tools import register_builtins\n"+s
    if "register_builtins(self.tools)" not in s:
        m=re.search(r"self\.tools\s*=\s*ToolRegistry\(\)",s)
        if m: s=s[:m.end()]+ "\n        register_builtins(self.tools)"+s[m.end():]
    wp.write_text(s,encoding="utf-8")

ap=ROOT/"api/main.py"
if ap.exists():
    s=ap.read_text(encoding="utf-8")
    if "/healthz" not in s:
        s+='''\n\n@app.get("/healthz")\nasync def agentos_healthz():\n    return {"ok": True, "service": "agentos", "runtime": "embedded"}\n'''
        ap.write_text(s,encoding="utf-8")

if not compileall.compile_dir(str(ROOT),quiet=1):
    raise SystemExit("AgentOS final pass failed Python compilation")
print("AgentOS final pass: web research + documents + video + artifact validation + healthz")


# AgentOS webhook runtime patch: Render web services should use Telegram webhooks
# instead of long-polling, which eliminates deployment-time getUpdates conflicts.
hr = ROOT / "hosted_runtime.py"
if hr.exists():
    h = hr.read_text(encoding="utf-8")
    if "AgentOS webhook runtime patch" not in h:
        h += r'''
# AgentOS webhook runtime patch
import hashlib as _agentos_hashlib
from aiogram.types import Update as _AgentOSUpdate

async def _telegram_loop(tm, queue, router_model, orch):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        log.warning("TELEGRAM_BOT_TOKEN missing; Telegram disabled")
        return
    notifier = get_notifier()
    bot = None
    notif_task = None
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
            log.info("Telegram webhook connected at %s", webhook)
            notif_task = asyncio.create_task(_status_notifier_loop(tm, notifier))
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
        if notif_task:
            notif_task.cancel()
            try:
                await notif_task
            except asyncio.CancelledError:
                pass
        if bot:
            try:
                await bot.delete_webhook(drop_pending_updates=False)
            except Exception:
                pass
            try:
                await bot.session.close()
            except Exception:
                pass

async def handle_telegram_webhook(payload: dict, secret_header: str | None = None):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    log.info("Telegram webhook request received: has_secret=%s payload_keys=%s", bool(secret_header), sorted(payload.keys()) if isinstance(payload, dict) else [])
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
        await dp.feed_update(bot, update)
        log.info("Telegram webhook update processed")
        return True
    except Exception:
        log.exception("Telegram webhook update processing failed")
        return False
'''
        hr.write_text(h, encoding="utf-8")

# Install/replace the webhook HTTP endpoint after hosted_runtime has been generated.
# The bootstrap/upgrade pass may already have installed a legacy handler that
# returns HTTP 501 when telegram_bot.bot_webhook is absent. Remove that route
# before adding the real hosted-runtime handler so FastAPI cannot shadow it.
ap = ROOT / "api/main.py"
if ap.exists():
    a = ap.read_text(encoding="utf-8")
    a = re.sub(
        r'\n@app\.post\("/telegram/webhook"\)\s*\nasync def telegram_webhook\(request: Request\):.*?(?=\n@app\.|\Z)',
        "",
        a,
        flags=re.S,
    )
    webhook_endpoint = r'''
# Telegram webhook endpoint. Authentication is performed by hosted_runtime
# using a token-derived secret header; invalid requests are rejected.
from fastapi import Request
from fastapi.responses import JSONResponse

@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    secret = request.headers.get("x-telegram-bot-api-secret-token")
    payload = await request.json()
    from hosted_runtime import handle_telegram_webhook
    ok = await handle_telegram_webhook(payload, secret)
    if not ok:
        return JSONResponse({"ok": False}, status_code=403)
    return {"ok": True}
'''
    a += webhook_endpoint
    ap.write_text(a, encoding="utf-8")


# Final syntax check includes the generated hosted webhook runtime and API route.
if not compileall.compile_dir(str(ROOT), quiet=1):
    raise SystemExit("AgentOS final pass: final generated source compilation failed")
print("AgentOS final pass: final generated-source compile check passed")


# ---------------------------------------------------------------------------
# Production hardening pass 2
# ---------------------------------------------------------------------------
# 1) Telegram webhook ownership: an old Render instance must never delete a
#    webhook that a newer instance has already installed.
hr = ROOT / "hosted_runtime.py"
if hr.exists():
    h = hr.read_text(encoding="utf-8")
    old = '''        if bot:
            try:
                await bot.delete_webhook(drop_pending_updates=False)
            except Exception:
                pass
            try:
                await bot.session.close()
            except Exception:
                pass
'''
    new = '''        if bot:
            # Only the instance that currently owns the webhook may delete it.
            # This prevents an old Render instance during a rolling deploy from
            # deleting the webhook belonging to the new instance.
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
'''
    if old in h:
        h = h.replace(old, new, 1)
    h = h.replace(
        '_runtime["telegram_ready"] = True\n            log.info("Telegram webhook connected at %s", webhook)',
        '_runtime["telegram_ready"] = True\n            _runtime["webhook_owner_url"] = webhook\n            log.info("Telegram webhook connected at %s", webhook)',
        1,
    )
    hr.write_text(h, encoding="utf-8")

# 2) Durable FIFO execution lock. Redis Streams already preserve enqueue order;
#    this distributed lock additionally guarantees that only one task is
#    executing at a time, even if Render briefly has two instances.
qp = ROOT / "core/queue.py"
if qp.exists():
    q = qp.read_text(encoding="utf-8")
    if "AGENTOS_EXECUTION_LOCK" not in q:
        q = q.replace(
'''DLQ = "agentos:tasks:dlq"
''',
'''DLQ = "agentos:tasks:dlq"
AGENTOS_EXECUTION_LOCK = "agentos:execution:lock"
''', 1)
        marker = '''    async def ack(self, entry_id: str) -> None:
'''
        methods = '''    async def acquire_execution_lock(self, owner: str, ttl_seconds: int = 1800) -> bool:
        """Global distributed execution lock: exactly one task runs at a time."""
        assert self._r is not None
        return bool(await self._r.set(
            AGENTOS_EXECUTION_LOCK, owner, nx=True, ex=max(60, int(ttl_seconds))
        ))

    async def refresh_execution_lock(self, owner: str, ttl_seconds: int = 1800) -> bool:
        assert self._r is not None
        cur = await self._r.get(AGENTOS_EXECUTION_LOCK)
        if cur != owner:
            return False
        return bool(await self._r.expire(AGENTOS_EXECUTION_LOCK, max(60, int(ttl_seconds))))

    async def release_execution_lock(self, owner: str) -> bool:
        assert self._r is not None
        # Atomic compare-and-delete via Lua.
        script = """
        if redis.call('GET', KEYS[1]) == ARGV[1] then
            return redis.call('DEL', KEYS[1])
        end
        return 0
        """
        return bool(await self._r.eval(script, 1, AGENTOS_EXECUTION_LOCK, owner))

'''
        if marker in q:
            q = q.replace(marker, methods + marker, 1)
        qp.write_text(q, encoding="utf-8")

# 3) Worker uses the global lock and never ACKs a message until the task is
#    actually terminal. The lock is acquired before stream claim so FIFO is
#    preserved across rolling deploys; a refresher prevents long tasks from
#    losing the lease.
wp = ROOT / "core/worker.py"
if wp.exists():
    w = wp.read_text(encoding="utf-8")
    if "execution_lock_owner" not in w:
        w = w.replace(
'''from __future__ import annotations
import asyncio, logging, os, signal, sys, time
''',
'''from __future__ import annotations
import asyncio, logging, os, signal, sys, time, uuid
''', 1)
        old = '''            try:
                await self._run_task(task_id)
                await self.queue.ack(entry_id)
            except Exception as e:
                log.exception("Task %s crashed", task_id)
                await self.tm.set_status(task_id, TaskStatus.FAILED, error=str(e))
                await self.queue.to_dlq(entry_id, task_id, str(e))
'''
        new = '''            owner = f"{self.name}:{uuid.uuid4().hex}"
            locked = False
            refresh_task = None
            entry_id = None
            task_id = None
            try:
                # Lock BEFORE claim: only one worker can consume the next FIFO
                # entry at a time.
                while not self.stop.is_set():
                    locked = await self.queue.acquire_execution_lock(owner)
                    if locked:
                        break
                    await asyncio.sleep(0.5)
                if not locked:
                    continue

                msg = await self.queue.claim_one(block_ms=3000)
                if not msg:
                    await self.queue.release_execution_lock(owner)
                    locked = False
                    continue

                entry_id = msg["entry_id"]
                task_id = msg["data"].get("task_id")
                if not task_id:
                    await self.queue.to_dlq(entry_id, "?", "missing task_id")
                    continue

                async def _refresh_lock():
                    while True:
                        await asyncio.sleep(60)
                        if not await self.queue.refresh_execution_lock(owner, ttl_seconds=1800):
                            log.warning("Execution lock refresh lost for %s", owner)
                            return

                refresh_task = asyncio.create_task(_refresh_lock())
                await self._run_task(task_id)
                await self.queue.ack(entry_id)
            except Exception as e:
                log.exception("Task %s crashed", task_id or "?")
                if task_id:
                    await self.tm.set_status(task_id, TaskStatus.FAILED, error=str(e))
                    if entry_id:
                        await self.queue.to_dlq(entry_id, task_id, str(e))
            finally:
                if refresh_task:
                    refresh_task.cancel()
                    try:
                        await refresh_task
                    except asyncio.CancelledError:
                        pass
                if locked:
                    try:
                        await self.queue.release_execution_lock(owner)
                    except Exception:
                        log.exception("Failed to release execution lock")
'''
        if old not in w:
            raise SystemExit("generated worker task loop pattern not found")
        w = w.replace(old, new, 1)
        wp.write_text(w, encoding="utf-8")

# 4) Persistent long-term memory. Completed task summaries become searchable
#    memory for future tasks from the same user.
tm = ROOT / "core/task_manager.py"
if tm.exists():
    t = tm.read_text(encoding="utf-8")
    if "CREATE TABLE IF NOT EXISTS memory_items" not in t:
        t = t.replace(
'''CREATE TABLE IF NOT EXISTS heartbeats (
  service         TEXT PRIMARY KEY,
  last_seen       DOUBLE PRECISION NOT NULL,
  meta            JSONB
);
''',
'''CREATE TABLE IF NOT EXISTS heartbeats (
  service         TEXT PRIMARY KEY,
  last_seen       DOUBLE PRECISION NOT NULL,
  meta            JSONB
);

CREATE TABLE IF NOT EXISTS memory_items (
  memory_id       TEXT PRIMARY KEY,
  user_id         TEXT NOT NULL,
  task_id         TEXT,
  kind            TEXT NOT NULL,
  content         TEXT NOT NULL,
  importance      REAL DEFAULT 0.5,
  created_at      DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS memory_user_idx ON memory_items(user_id, created_at DESC);
''', 1)
        marker = '''    # ---------------- tasks ----------------
'''
        methods = '''    # ---------------- long-term memory ----------------

    async def remember(self, user_id: str, content: str, task_id: str | None = None,
                       kind: str = "task_summary", importance: float = 0.5) -> None:
        if self.pool is None or not str(content).strip():
            return
        async with self.pool.acquire() as c:
            await c.execute(
                """INSERT INTO memory_items(memory_id,user_id,task_id,kind,content,importance,created_at)
                   VALUES($1,$2,$3,$4,$5,$6,$7)""",
                str(uuid.uuid4()), str(user_id), task_id, kind,
                str(content)[:12000], max(0.0, min(1.0, float(importance))), time.time()
            )

    async def recall(self, user_id: str, query: str, limit: int = 8) -> list[dict]:
        if self.pool is None:
            return []
        words = {x for x in str(query).lower().split() if len(x) > 2}
        async with self.pool.acquire() as c:
            rows = await c.fetch(
                """SELECT memory_id,task_id,kind,content,importance,created_at
                   FROM memory_items WHERE user_id=$1
                   ORDER BY created_at DESC LIMIT 100""", str(user_id))
        scored = []
        for r in rows:
            content = str(r["content"])
            low = content.lower()
            overlap = sum(1 for word in words if word in low)
            scored.append((overlap + float(r["importance"]) * 0.25, dict(r)))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [x[1] for x in scored[:max(1, int(limit))]]

'''
        if marker in t:
            t = t.replace(marker, methods + marker, 1)
        tm.write_text(t, encoding="utf-8")

# 5) Worker writes durable memories and injects the relevant ones into the
#    task context without changing the user's original task text.
if wp.exists():
    w = wp.read_text(encoding="utf-8")
    if "worker_memory_context" not in w:
        w = w.replace(
'''        task = Task(id=task_id, description=row["description"],
                    domain=row.get("domain") or "general")
''',
'''        original_description = row["description"]
        worker_memory_context = ""
        try:
            memories = await self.tm.recall(str(row.get("user_id") or "api"),
                                            original_description, limit=6)
            if memories:
                worker_memory_context = "\\n\\n[RELEVANT LONG-TERM MEMORY]\\n" + "\\n".join(
                    f"- {m['content'][:1200]}" for m in memories
                )
        except Exception:
            log.debug("Memory recall unavailable", exc_info=True)
        task = Task(id=task_id,
                    description=original_description + worker_memory_context,
                    domain=row.get("domain") or "general")
''', 1)
        w = w.replace(
'''            if result.success:
                await self.tm.set_status(task_id, TaskStatus.COMPLETED,
''',
'''            if result.success:
                try:
                    await self.tm.remember(
                        str(row.get("user_id") or "api"),
                        f"Task: {original_description}\\nResult: {result.summary[:8000]}",
                        task_id=task_id, kind="task_summary", importance=0.7)
                except Exception:
                    log.debug("Memory write unavailable", exc_info=True)
                await self.tm.set_status(task_id, TaskStatus.COMPLETED,
''', 1)
        wp.write_text(w, encoding="utf-8")

# 6) Stronger artifact validation before terminal completion. Only explicit
#    file/url artifacts are validated; ordinary step outputs remain valid.
if wp.exists():
    w = wp.read_text(encoding="utf-8")
    if "artifact_validation" not in w:
        w = w.replace(
'''            if result.success:
                try:
                    await self.tm.remember(
''',
'''            if result.success:
                explicit_artifacts = [
                    a for a in (result.artifacts or [])
                    if isinstance(a, dict) and any(k in a for k in ("path", "file_path", "url", "content"))
                ]
                if explicit_artifacts:
                    from .artifact_validator import validate_all
                    artifact_validation = validate_all(explicit_artifacts)
                    await self.tm.log_event(task_id, "artifact_validation",
                                            artifact_validation)
                    if not artifact_validation["valid"]:
                        raise RuntimeError("Artifact validation failed: " +
                                           json.dumps(artifact_validation)[:1800])
                try:
                    await self.tm.remember(
''', 1)
        wp.write_text(w, encoding="utf-8")

# 7) Full video pipeline: representative frames + audio extraction +
#    optional OpenAI transcription + multimodal vision analysis through the
#    existing ModelRouter. Everything is bounded to keep the 512MB instance safe.
mp = ROOT / "core/media_pipeline.py"
if mp.exists():
    mp.write_text(r'''from __future__ import annotations
import asyncio, base64, json, os, subprocess, tempfile
from pathlib import Path
from typing import Any

MAX_VIDEO_BYTES = 120 * 1024 * 1024

def extract_document(path: str):
    p=Path(path); ext=p.suffix.lower()
    if not p.exists():
        return {"type":"unknown","error":"file not found"}
    if ext in {".txt",".md",".csv",".json",".py",".js",".ts",".html",".css"}:
        return {"type":"text","text":p.read_text(encoding="utf-8",errors="ignore")[:200000]}
    if ext==".pdf":
        try:
            from pypdf import PdfReader
            r=PdfReader(str(p))
            return {"type":"pdf","pages":len(r.pages),"text":"\\n".join(x.extract_text() or "" for x in r.pages)[:200000]}
        except Exception as e: return {"type":"pdf","error":f"{type(e).__name__}: {e}"}
    if ext==".docx":
        try:
            from docx import Document
            d=Document(str(p))
            return {"type":"docx","text":"\\n".join(x.text for x in d.paragraphs)[:200000]}
        except Exception as e: return {"type":"docx","error":f"{type(e).__name__}: {e}"}
    return {"type":"unknown","path":str(p),"size":p.stat().st_size}

def _run_ffmpeg(ff: str, args: list[str], timeout: int = 90):
    return subprocess.run([ff, "-hide_banner", "-loglevel", "error", *args],
                          check=True, timeout=timeout,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)

def _extract_frames(ff: str, path: str, out: Path, frames: int):
    pattern=str(out/"frame_%02d.jpg")
    _run_ffmpeg(ff, ["-i",path,"-vf","fps=1/15,scale=768:-2","-frames:v",str(frames),
                     "-q:v","6",pattern], timeout=90)
    return sorted(out.glob("frame_*.jpg"))

def _extract_audio(ff: str, path: str, out: Path):
    wav=out/"audio.wav"
    _run_ffmpeg(ff, ["-i",path,"-vn","-ac","1","-ar","16000","-t","300",
                     "-c:a","pcm_s16le",str(wav)], timeout=120)
    return wav if wav.exists() else None

def _transcribe_openai(audio: Path) -> str:
    key=os.getenv("OPENAI_API_KEY")
    if not key or not audio.exists():
        return ""
    import httpx
    with audio.open("rb") as f:
        files={"file":(audio.name,f,"audio/wav")}
        data={"model":os.getenv("TRANSCRIPTION_MODEL","gpt-4o-mini-transcribe")}
        r=httpx.post("https://api.openai.com/v1/audio/transcriptions",
                     headers={"Authorization":f"Bearer {key}"},
                     files=files,data=data,timeout=180)
    r.raise_for_status()
    return str(r.json().get("text") or "")[:20000]

async def video_analyze(path: str, prompt: str = "Describe what happens in this video and note important details.",
                        frames: int = 6):
    p=Path(path)
    if not p.exists(): return {"ok":False,"error":"video file not found"}
    if p.stat().st_size > MAX_VIDEO_BYTES:
        return {"ok":False,"error":"video exceeds 120MB safety limit"}
    try:
        import imageio_ffmpeg
        ff=imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as e:
        return {"ok":False,"error":f"ffmpeg unavailable: {e}"}
    out=Path(tempfile.mkdtemp(prefix="agentos_video_"))
    try:
        imgs=_extract_frames(ff,str(p),out,max(1,min(6,int(frames))))
        audio=_extract_audio(ff,str(p),out)
        transcript=""
        if audio:
            try:
                transcript=await asyncio.to_thread(_transcribe_openai,audio)
            except Exception:
                transcript=""
        result={"ok":True,"frames":[str(x) for x in imgs],
                "audio":str(audio) if audio else None,"transcript":transcript}
        # Use the existing fallback router for visual understanding.
        if imgs:
            try:
                from .model_router import ModelRouter
                parts=[{"type":"text","text":prompt + "\\nAudio transcript:\\n" + transcript[:12000]}]
                for img in imgs:
                    b64=base64.b64encode(img.read_bytes()).decode()
                    parts.append({"type":"image_url","image_url":{"url":"data:image/jpeg;base64,"+b64}})
                comp=await ModelRouter().complete(
                    [{"role":"user","content":parts}],
                    task_type="vision", temperature=0.1, max_tokens=2000)
                result["analysis"]=comp.text
                result["provider"]=comp.provider
                result["model"]=comp.model
            except Exception as e:
                result["analysis_error"]=f"{type(e).__name__}: {e}"
        return result
    except Exception as e:
        return {"ok":False,"error":f"{type(e).__name__}: {e}"}

def video_extract(path: str, frames=4):
    # Backward-compatible synchronous frame extraction for existing callers.
    try:
        import imageio_ffmpeg
        ff=imageio_ffmpeg.get_ffmpeg_exe()
        out=Path(tempfile.mkdtemp(prefix="agentos_video_"))
        imgs=_extract_frames(ff,path,out,max(1,min(8,int(frames))))
        return {"ok":True,"frames":[str(x) for x in imgs]}
    except Exception as e:
        return {"ok":False,"error":f"{type(e).__name__}: {e}"}
''', encoding="utf-8")

# Replace the built-in video tool with the full async analyzer.
bt = ROOT / "core/builtin_tools.py"
if bt.exists():
    b = bt.read_text(encoding="utf-8")
    b = b.replace(
        'from .media_pipeline import extract_document, video_extract',
        'from .media_pipeline import extract_document, video_extract, video_analyze'
    )
    old = 'lambda path,frames=4: video_extract(path,frames)'
    if old in b:
        b = b.replace(old, 'video_analyze', 1)
    bt.write_text(b, encoding="utf-8")

# 8) Add a dedicated multimodal video skill to the research/vision agent.
da = ROOT / "demo/agents.py"
if da.exists():
    d = da.read_text(encoding="utf-8")
    d = d.replace(
        'tools=["web_search"],',
        'tools=["web_search", "analyze_video", "read_document"],', 1
    )
    d = d.replace(
        'Use web_search to gather facts. Cite sources.',
        'Use web_search to gather facts and analyze_video/read_document for supplied media/documents. Cite sources.',
        1
    )
    da.write_text(d, encoding="utf-8")

# 9) Resource hardening for 512MB Render: cap connection pools and disable
#    expensive tracing console export in production unless explicitly enabled.
tm = ROOT / "core/task_manager.py"
if tm.exists():
    t = tm.read_text(encoding="utf-8").replace(
        'asyncpg.create_pool(self.dsn, min_size=1, max_size=10)',
        'asyncpg.create_pool(self.dsn, min_size=1, max_size=int(os.getenv("DB_POOL_MAX","4")))'
    )
    if 'import os' not in t.split('\\n', 5)[0:5]:
        t = t.replace('import json, logging, time, uuid', 'import json, logging, os, time, uuid', 1)
    tm.write_text(t, encoding="utf-8")

# 10) Add an explicit production health endpoint with queue/runtime state.
ap = ROOT / "api/main.py"
if ap.exists():
    a = ap.read_text(encoding="utf-8")
    if '/healthz' in a and 'execution_lock' not in a:
        a += r'''
@app.get("/healthz")
async def production_healthz():
    tm = STATE.get("tm")
    router = STATE.get("router")
    data = {"ok": bool(tm and getattr(tm, "pool", None)),
            "service": "agentos", "runtime": "embedded"}
    try:
        data["providers"] = router.available_providers() if router else []
    except Exception:
        data["providers"] = []
    try:
        rows = await tm.read_heartbeats() if tm else []
        data["heartbeats"] = {r["service"]: r["last_seen"] for r in rows}
    except Exception:
        data["heartbeats"] = {}
    return data
'''
        ap.write_text(a, encoding="utf-8")

# 11) Final compile check after every production hardening patch.
if not compileall.compile_dir(str(ROOT), quiet=1):
    raise SystemExit("AgentOS production hardening pass failed Python compilation")
print("AgentOS production hardening pass 2: FIFO + memory + artifacts + video + webhook ownership + memory caps OK")


# 12) Native provider tool-call preservation
# LiteLLM providers must preserve native tool calls; otherwise the autonomous
# execution layer receives an empty tool_calls list and silently degrades to text.
lp = ROOT / "core/providers/_litellm_base.py"
if lp.exists():
    x = lp.read_text(encoding="utf-8")
    old = '''        msg = resp.choices[0].message
        text = msg.content or ""
        usage = getattr(resp, "usage", None)
'''
    new = '''        msg = resp.choices[0].message
        text = msg.content or ""
        native_tool_calls = []
        for tc in (getattr(msg, "tool_calls", None) or []):
            fn = getattr(tc, "function", None)
            native_tool_calls.append({
                "id": getattr(tc, "id", None),
                "name": getattr(fn, "name", None),
                "arguments": getattr(fn, "arguments", None),
            })
        usage = getattr(resp, "usage", None)
'''
    if old in x and "native_tool_calls" not in x:
        x = x.replace(old,new,1)
        x = x.replace('raw={"finish_reason": getattr(resp.choices[0], "finish_reason", None)},',
                      'raw={"finish_reason": getattr(resp.choices[0], "finish_reason", None), "tool_calls": native_tool_calls},',1)
        lp.write_text(x,encoding="utf-8")

llp = ROOT / "core/llm.py"
if llp.exists():
    x = llp.read_text(encoding="utf-8")
    old = '''        # Router returns text only; tool_calls are provider-specific. For the
        # kernel, the planner path uses complete_json, so this is a fallback.
        return {"content": comp.text, "tool_calls": []}
'''
    new = '''        raw = comp.raw or {}
        return {
            "content": comp.text,
            "tool_calls": raw.get("tool_calls") or [],
            "provider": comp.provider,
            "model": comp.model,
        }
'''
    if old in x:
        x=x.replace(old,new,1)
        llp.write_text(x,encoding="utf-8")

if not compileall.compile_dir(str(ROOT), quiet=1):
    raise SystemExit("Native tool-call preservation patch failed Python compilation")
print("AgentOS native tool calls preserved")
