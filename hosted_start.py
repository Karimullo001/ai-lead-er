"""Hosted single-process Render entrypoint for AgentOS Free plan.

Patches the generated FastAPI lifespan before importing the app, then runs
Uvicorn. This keeps API + worker + Telegram polling inside one web service.
"""
from pathlib import Path
import os
import sys

TARGET = Path("api/main.py")
source = TARGET.read_text(encoding="utf-8")

marker = "STATE: Dict[str, Any] = {}"
if "HOSTED_RUNTIME: Dict[str, Any]" not in source:
    source = source.replace(
        marker,
        marker + "\nHOSTED_RUNTIME: Dict[str, Any] = {}",
        1,
    )

old = """    STATE.update(dict(tm=tm, queue=q, router=router, cost=cost, redis_url=redis_url))
    log.info("API ready with providers: %s", router.available_providers())
    yield
    await q.close(); await tm.close()
    STATE.clear()"""

new = """    STATE.update(dict(tm=tm, queue=q, router=router, cost=cost, redis_url=redis_url))
    log.info("API ready with providers: %s", router.available_providers())
    if os.getenv("EMBEDDED_RUNTIME", "true").lower() == "true":
        from hosted_runtime import start_hosted_runtime
        HOSTED_RUNTIME.update(await start_hosted_runtime())
    yield
    if HOSTED_RUNTIME:
        from hosted_runtime import stop_hosted_runtime
        await stop_hosted_runtime(HOSTED_RUNTIME)
        HOSTED_RUNTIME.clear()
    await q.close(); await tm.close()
    STATE.clear()"""

if "start_hosted_runtime" not in source:
    if old not in source:
        raise RuntimeError("Expected generated API lifespan block was not found")
    source = source.replace(old, new, 1)

TARGET.write_text(source, encoding="utf-8")

import uvicorn
uvicorn.run(
    "api.main:app",
    host="0.0.0.0",
    port=int(os.getenv("PORT", "10000")),
    log_level=os.getenv("LOG_LEVEL", "info"),
)
