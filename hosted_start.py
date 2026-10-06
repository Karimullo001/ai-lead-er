"""Reliable single-process Render entrypoint for AgentOS Free.

Loads the generated FastAPI app and wraps its lifespan so the embedded worker
and Telegram polling are always started after the API infrastructure is ready.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

import uvicorn

from api.main import app
from hosted_runtime import start_hosted_runtime, stop_hosted_runtime

_original_lifespan = app.router.lifespan_context


@asynccontextmanager
async def _hosted_lifespan(application):
    runtime = None
    async with _original_lifespan(application):
        try:
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
