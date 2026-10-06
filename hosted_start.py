"""Reliable single-process Render entrypoint for AgentOS Free."""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

import uvicorn
from api.main import app

_original_lifespan = app.router.lifespan_context


@asynccontextmanager
async def _hosted_lifespan(application):
    runtime = None
    async with _original_lifespan(application):
        try:
            # Lazy import: keep the web process able to bind its port quickly.
            from hosted_runtime import start_hosted_runtime
            from hosted_runtime import stop_hosted_runtime

            runtime = await start_hosted_runtime()
            yield
        finally:
            if runtime:
                await stop_hosted_runtime(runtime)


app.router.lifespan_context = _hosted_lifespan

uvicorn.run(
    app,
    host="0.0.0.0",
    port=int(os.getenv("PORT", "10000")),
    log_level=os.getenv("LOG_LEVEL", "info"),
)
