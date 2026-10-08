"""Single-process Render entrypoint for AgentOS."""
from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager

import uvicorn

os.environ["EMBEDDED_RUNTIME"] = "false"

from api.main import app

@asynccontextmanager
async def _hosted_lifespan(application):
    runtime_task = None
    runtime_state = None

    async def boot():
        await asyncio.sleep(1)
        from hosted_runtime import start_hosted_runtime
        return await start_hosted_runtime()

    runtime_task = asyncio.create_task(boot())
    try:
        yield
        if runtime_task.done() and not runtime_task.cancelled():
            try:
                runtime_state = runtime_task.result()
            except Exception:
                runtime_state = None
    finally:
        if runtime_state is None and runtime_task.done() and not runtime_task.cancelled():
            try:
                runtime_state = runtime_task.result()
            except Exception:
                runtime_state = None

        if runtime_state:
            try:
                from hosted_runtime import stop_hosted_runtime
                await stop_hosted_runtime(runtime_state)
            except Exception:
                pass

        if runtime_task and not runtime_task.done():
            runtime_task.cancel()
            try:
                await runtime_task
            except asyncio.CancelledError:
                pass

app.router.lifespan_context = _hosted_lifespan

uvicorn.run(
    app,
    host="0.0.0.0",
    port=int(os.getenv("PORT", "10000")),
    log_level=os.getenv("LOG_LEVEL", "info"),
)
