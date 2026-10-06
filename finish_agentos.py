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
        s="from core.builtin_tools import register_builtins\n"+s
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
    if not token or not secret_header:
        return False
    expected = _agentos_hashlib.sha256(token.encode()).hexdigest()
    if secret_header != expected:
        return False
    dp = _runtime.get("dispatcher")
    bot = _runtime.get("bot")
    if not dp or not bot:
        return False
    update = _AgentOSUpdate.model_validate(payload)
    await dp.feed_update(bot, update)
    return True
'''
        hr.write_text(h, encoding="utf-8")

# Install the webhook HTTP endpoint after hosted_runtime has been generated.
ap = ROOT / "api/main.py"
if ap.exists():
    a = ap.read_text(encoding="utf-8")
    if "/telegram/webhook" not in a:
        a += r'''
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
        ap.write_text(a, encoding="utf-8")
