#!/usr/bin/env python3
"""
AgentOS → 24/7 Telegram Autonomous AI Platform — Upgrade Overlay.

Run from the existing agentos/ project root:
    python upgrade_agentos.py

- Adds new modules.
- Patches a small, explicitly-listed set of existing files (originals backed up
  to *.bak.agentos_upgrade).
- Preserves: kernel, orchestrator, factory, tools, memory, events, reliability,
  approval, comm_bus, agent, models, prompts, guardrails, tracing.
"""
from __future__ import annotations
import os, shutil, sys
from pathlib import Path

ROOT = Path.cwd()

# Files we will overwrite (with backup).
PATCHED = {
    "core/llm.py",
    "core/worker.py",
    "api/main.py",
    "pyproject.toml",
    ".env.example",
    "docker-compose.yml",
}

FILES: dict[str, str] = {}

# =====================================================================
# core/providers/__init__.py
# =====================================================================
FILES["core/providers/__init__.py"] = r'''from .base import Completion, ModelProvider
from .openai_provider import OpenAIProvider
from .deepseek_provider import DeepSeekProvider
from .gemini_provider import GeminiProvider
from .groq_provider import GroqProvider
from .openrouter_provider import OpenRouterProvider
from .xai_provider import XAIProvider

__all__ = [
    "Completion", "ModelProvider",
    "OpenAIProvider", "DeepSeekProvider", "GeminiProvider",
    "GroqProvider", "OpenRouterProvider", "XAIProvider",
]
'''

# =====================================================================
# core/providers/base.py
# =====================================================================
FILES["core/providers/base.py"] = r'''from __future__ import annotations
import asyncio, time
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field


class Completion(BaseModel):
    text: str
    provider: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float = 0.0
    cost_usd: float = 0.0
    raw: Optional[Dict[str, Any]] = None


class ProviderError(Exception):
    """Raised by providers with a normalized error class for the router."""
    def __init__(self, provider: str, kind: str, message: str):
        super().__init__(f"[{provider}:{kind}] {message}")
        self.provider = provider
        self.kind = kind          # rate_limit | timeout | server_error | auth | bad_request | unknown
        self.message = message


def classify_exception(e: Exception) -> str:
    s = str(e).lower()
    if "rate limit" in s or "429" in s or "too many" in s: return "rate_limit"
    if "timeout" in s or "timed out" in s or "deadline" in s: return "timeout"
    if "401" in s or "unauthor" in s or "invalid api key" in s: return "auth"
    if "400" in s or "bad request" in s: return "bad_request"
    if "500" in s or "502" in s or "503" in s or "504" in s or "unavailable" in s:
        return "server_error"
    return "unknown"


class ModelProvider(ABC):
    name: str = "base"
    # USD per 1K tokens: {model_prefix: (input, output)}
    pricing: Dict[str, Tuple[float, float]] = {}

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None):
        self.api_key = api_key
        self.base_url = base_url

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    @abstractmethod
    def models(self) -> List[str]: ...

    @abstractmethod
    async def complete(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        temperature: float = 0.2,
        max_tokens: int = 2048,
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> Completion: ...

    def default_model(self) -> str:
        ms = self.models()
        if not ms:
            raise RuntimeError(f"{self.name}: no models configured")
        return ms[0]

    def estimate_cost(self, model: str, pt: int, ct: int) -> float:
        for prefix, (pin, pout) in self.pricing.items():
            if model.startswith(prefix):
                return (pt / 1000.0) * pin + (ct / 1000.0) * pout
        return 0.0

    async def health(self) -> bool:
        if not self.available:
            return False
        try:
            await asyncio.wait_for(
                self.complete(self.default_model(),
                              [{"role": "user", "content": "ping"}],
                              temperature=0.0, max_tokens=4),
                timeout=15,
            )
            return True
        except Exception:
            return False
'''

# =====================================================================
# core/providers/_litellm_base.py  (shared implementation)
# =====================================================================
FILES["core/providers/_litellm_base.py"] = r'''from __future__ import annotations
import time
from typing import Any, Dict, List, Optional
from .base import Completion, ModelProvider, ProviderError, classify_exception


class LiteLLMProvider(ModelProvider):
    """Shared LiteLLM-backed provider. Subclasses set `name`, `models_list`,
    `litellm_prefix`, and `env_key`."""

    name: str = "litellm"
    litellm_prefix: str = ""
    env_key: str = ""
    models_list: List[str] = []
    pricing: Dict[str, tuple] = {}

    def models(self) -> List[str]:
        return list(self.models_list)

    async def complete(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        temperature: float = 0.2,
        max_tokens: int = 2048,
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> Completion:
        import litellm
        litellm.drop_params = True
        full = f"{self.litellm_prefix}{model}" if self.litellm_prefix else model
        t0 = time.time()
        call_kwargs: Dict[str, Any] = dict(
            model=full, messages=messages,
            temperature=temperature, max_tokens=max_tokens,
        )
        if self.api_key:
            call_kwargs["api_key"] = self.api_key
        if self.base_url:
            call_kwargs["base_url"] = self.base_url
        if tools:
            call_kwargs["tools"] = tools
            call_kwargs["tool_choice"] = "auto"
        call_kwargs.update(kwargs)
        try:
            resp = await litellm.acompletion(**call_kwargs)
        except Exception as e:
            raise ProviderError(self.name, classify_exception(e), str(e)) from e

        msg = resp.choices[0].message
        text = msg.content or ""
        usage = getattr(resp, "usage", None)
        pt = int(getattr(usage, "prompt_tokens", 0) or 0)
        ct = int(getattr(usage, "completion_tokens", 0) or 0)
        latency = (time.time() - t0) * 1000.0
        return Completion(
            text=text, provider=self.name, model=model,
            prompt_tokens=pt, completion_tokens=ct,
            latency_ms=latency,
            cost_usd=self.estimate_cost(model, pt, ct),
            raw={"finish_reason": getattr(resp.choices[0], "finish_reason", None)},
        )
'''

# =====================================================================
# The 6 providers
# =====================================================================
FILES["core/providers/openai_provider.py"] = r'''from __future__ import annotations
import os
from typing import List
from ._litellm_base import LiteLLMProvider


class OpenAIProvider(LiteLLMProvider):
    name = "openai"
    litellm_prefix = ""
    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        super().__init__(api_key or os.getenv("OPENAI_API_KEY"), base_url)
        self.models_list = [
            os.getenv("DEFAULT_MODEL", "gpt-4o-mini"),
            "gpt-4o",
            "gpt-4.1-mini",
        ]
    pricing = {
        "gpt-4o-mini": (0.00015, 0.0006),
        "gpt-4o": (0.0025, 0.01),
        "gpt-4.1-mini": (0.0004, 0.0016),
    }
'''

FILES["core/providers/deepseek_provider.py"] = r'''from __future__ import annotations
import os
from ._litellm_base import LiteLLMProvider


class DeepSeekProvider(LiteLLMProvider):
    name = "deepseek"
    litellm_prefix = "deepseek/"
    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        super().__init__(api_key or os.getenv("DEEPSEEK_API_KEY"), base_url)
        self.models_list = ["deepseek-chat", "deepseek-reasoner"]
    pricing = {
        "deepseek-chat": (0.00014, 0.00028),
        "deepseek-reasoner": (0.00055, 0.00219),
    }
'''

FILES["core/providers/gemini_provider.py"] = r'''from __future__ import annotations
import os
from ._litellm_base import LiteLLMProvider


class GeminiProvider(LiteLLMProvider):
    name = "gemini"
    litellm_prefix = "gemini/"
    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        super().__init__(api_key or os.getenv("GEMINI_API_KEY"), base_url)
        self.models_list = ["gemini-1.5-flash", "gemini-1.5-pro", "gemini-2.0-flash"]
    pricing = {
        "gemini-1.5-flash": (0.000075, 0.0003),
        "gemini-1.5-pro": (0.00125, 0.005),
        "gemini-2.0-flash": (0.0001, 0.0004),
    }
'''

FILES["core/providers/groq_provider.py"] = r'''from __future__ import annotations
import os
from ._litellm_base import LiteLLMProvider


class GroqProvider(LiteLLMProvider):
    name = "groq"
    litellm_prefix = "groq/"
    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        super().__init__(api_key or os.getenv("GROQ_API_KEY"), base_url)
        self.models_list = ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"]
    pricing = {
        "llama-3.3-70b-versatile": (0.00059, 0.00079),
        "llama-3.1-8b-instant": (0.00005, 0.00008),
    }
'''

FILES["core/providers/openrouter_provider.py"] = r'''from __future__ import annotations
import os
from ._litellm_base import LiteLLMProvider


class OpenRouterProvider(LiteLLMProvider):
    name = "openrouter"
    litellm_prefix = "openrouter/"
    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        super().__init__(api_key or os.getenv("OPENROUTER_API_KEY"), base_url)
        self.models_list = ["anthropic/claude-3.5-sonnet", "meta-llama/llama-3.3-70b-instruct"]
    pricing = {
        "anthropic/claude-3.5-sonnet": (0.003, 0.015),
        "meta-llama/llama-3.3-70b-instruct": (0.00035, 0.0004),
    }
'''

FILES["core/providers/xai_provider.py"] = r'''from __future__ import annotations
import os
from ._litellm_base import LiteLLMProvider


class XAIProvider(LiteLLMProvider):
    name = "xai"
    litellm_prefix = "xai/"
    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        super().__init__(api_key or os.getenv("XAI_API_KEY"), base_url)
        self.models_list = ["grok-2-latest", "grok-beta"]
    pricing = {
        "grok-2-latest": (0.002, 0.01),
        "grok-beta": (0.005, 0.015),
    }
'''

# =====================================================================
# core/model_router.py
# =====================================================================
FILES["core/model_router.py"] = r'''from __future__ import annotations
import asyncio, logging, os, time
from typing import Any, Dict, List, Optional
from .providers import (Completion, ModelProvider, OpenAIProvider, DeepSeekProvider,
                        GeminiProvider, GroqProvider, OpenRouterProvider, XAIProvider)
from .providers.base import ProviderError

log = logging.getLogger("agentos.router")

# ---- Task type → ranked provider/model preference --------------------------
# Each entry lists (provider_name, model_hint). The router tries them in order,
# skipping providers that are unhealthy / cooled down.

TASK_PREFERENCES: Dict[str, List[tuple]] = {
    "coding":     [("openai","gpt-4o"), ("openrouter","anthropic/claude-3.5-sonnet"),
                   ("deepseek","deepseek-chat"), ("groq","llama-3.3-70b-versatile")],
    "reasoning":  [("deepseek","deepseek-reasoner"), ("openai","gpt-4o"),
                   ("gemini","gemini-1.5-pro"), ("xai","grok-2-latest")],
    "research":   [("openai","gpt-4o-mini"), ("gemini","gemini-1.5-pro"),
                   ("deepseek","deepseek-chat"), ("groq","llama-3.3-70b-versatile")],
    "fast":       [("groq","llama-3.1-8b-instant"), ("gemini","gemini-1.5-flash"),
                   ("openai","gpt-4o-mini"), ("deepseek","deepseek-chat")],
    "large":      [("gemini","gemini-1.5-pro"), ("openai","gpt-4o"),
                   ("openrouter","anthropic/claude-3.5-sonnet")],
    "vision":     [("openai","gpt-4o"), ("gemini","gemini-1.5-pro")],
    "verify":     [("deepseek","deepseek-reasoner"), ("xai","grok-2-latest"),
                   ("openai","gpt-4o"), ("openrouter","anthropic/claude-3.5-sonnet")],
    "general":    [("openai","gpt-4o-mini"), ("groq","llama-3.3-70b-versatile"),
                   ("deepseek","deepseek-chat"), ("gemini","gemini-1.5-flash"),
                   ("openrouter","meta-llama/llama-3.3-70b-instruct"),
                   ("xai","grok-2-latest")],
}


class CircuitBreaker:
    """Per-provider health/cooling. Opens after N failures, cools down, half-opens."""
    def __init__(self, failure_threshold: int = 3, cooldown_seconds: float = 60.0):
        self.failure_threshold = failure_threshold
        self.cooldown = cooldown_seconds
        self.failures: Dict[str, int] = {}
        self.opened_at: Dict[str, float] = {}
        self.lock = asyncio.Lock()

    def is_open(self, provider: str) -> bool:
        opened = self.opened_at.get(provider)
        if opened is None:
            return False
        if time.time() - opened > self.cooldown:
            # half-open: allow one probe
            self.opened_at.pop(provider, None)
            self.failures[provider] = self.failure_threshold - 1
            return False
        return True

    async def record_success(self, provider: str) -> None:
        async with self.lock:
            self.failures[provider] = 0
            self.opened_at.pop(provider, None)

    async def record_failure(self, provider: str) -> None:
        async with self.lock:
            self.failures[provider] = self.failures.get(provider, 0) + 1
            if self.failures[provider] >= self.failure_threshold:
                self.opened_at[provider] = time.time()
                log.warning("Circuit breaker OPEN for %s", provider)


class ModelRouter:
    """
    Picks a model based on task type; tries providers in preference order with
    exponential backoff, circuit breaker, and automatic fallback.
    """

    def __init__(
        self,
        providers: Optional[List[ModelProvider]] = None,
        breaker: Optional[CircuitBreaker] = None,
        max_attempts_per_provider: int = 2,
        base_backoff_ms: int = 400,
    ):
        if providers is None:
            providers = [
                OpenAIProvider(), DeepSeekProvider(), GeminiProvider(),
                GroqProvider(), OpenRouterProvider(), XAIProvider(),
            ]
        self.providers: Dict[str, ModelProvider] = {p.name: p for p in providers if p.available}
        self.breaker = breaker or CircuitBreaker()
        self.max_attempts_per_provider = max_attempts_per_provider
        self.base_backoff_ms = base_backoff_ms
        log.info("ModelRouter online with providers: %s", list(self.providers))

    # ---- selection ----
    def _candidates(self, task_type: str, model_hint: Optional[str] = None) -> List[tuple]:
        prefs = TASK_PREFERENCES.get(task_type, TASK_PREFERENCES["general"])
        out: List[tuple] = []
        if model_hint:
            # Try to find which provider owns the hint.
            for p in self.providers.values():
                if model_hint in p.models():
                    out.append((p.name, model_hint))
        for pname, model in prefs:
            if pname in self.providers and (pname, model) not in out:
                out.append((pname, model))
        # Last resort: any available provider's default model.
        for p in self.providers.values():
            if (p.name, p.default_model()) not in out:
                out.append((p.name, p.default_model()))
        return out

    async def complete(
        self,
        messages: List[Dict[str, Any]],
        task_type: str = "general",
        model_hint: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> Completion:
        candidates = self._candidates(task_type, model_hint)
        last_error: Optional[Exception] = None
        for pname, model in candidates:
            provider = self.providers.get(pname)
            if provider is None:
                continue
            if self.breaker.is_open(pname):
                log.debug("Skipping %s (breaker open)", pname)
                continue
            for attempt in range(self.max_attempts_per_provider):
                try:
                    comp = await provider.complete(
                        model=model, messages=messages,
                        temperature=temperature, max_tokens=max_tokens,
                        tools=tools, **kwargs,
                    )
                    await self.breaker.record_success(pname)
                    log.info("router: %s/%s ok in %.0fms ($%.5f)",
                             pname, model, comp.latency_ms, comp.cost_usd)
                    return comp
                except ProviderError as e:
                    last_error = e
                    log.warning("router: %s/%s -> %s: %s", pname, model, e.kind, e.message[:160])
                    await self.breaker.record_failure(pname)
                    if e.kind in ("auth", "bad_request"):
                        break  # do not retry same provider
                    if attempt + 1 < self.max_attempts_per_provider:
                        delay = (self.base_backoff_ms * (2 ** attempt)) / 1000.0
                        await asyncio.sleep(delay)
                except Exception as e:
                    last_error = e
                    await self.breaker.record_failure(pname)
                    log.exception("router: unexpected error on %s/%s", pname, model)
                    break
        if last_error is None:
            raise RuntimeError("ModelRouter: no available providers configured")
        raise RuntimeError(f"ModelRouter: all providers failed. Last: {last_error}")

    # ---- health ----
    async def provider_health(self) -> Dict[str, bool]:
        result: Dict[str, bool] = {}
        for name, p in self.providers.items():
            if self.breaker.is_open(name):
                result[name] = False
                continue
            try:
                result[name] = await asyncio.wait_for(p.health(), timeout=20)
            except Exception:
                result[name] = False
        return result

    def available_providers(self) -> List[str]:
        return list(self.providers.keys())
'''

# =====================================================================
# core/llm.py  (PATCHED — becomes a ModelRouter facade, keeps old API)
# =====================================================================
FILES["core/llm.py"] = r'''from __future__ import annotations
import asyncio, json, logging, os, re
from typing import Any, Dict, List, Optional
from .model_router import ModelRouter

log = logging.getLogger("agentos.llm")


class LLMClient:
    """
    Backward-compatible facade over ModelRouter.

    If `router` is None, builds one from env vars. Otherwise, uses the passed
    router (recommended in production so all agents share state/circuit breaker).

    The old `.complete(...)` / `.complete_json(...)` / `.complete_with_tools(...)`
    API is preserved so kernel.py needs no changes.
    """

    def __init__(
        self,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        max_retries: int = 3,
        router: Optional[ModelRouter] = None,
    ):
        self.model_hint = model
        self.api_key = api_key
        self.base_url = base_url
        self.max_retries = max_retries
        self.router = router or ModelRouter()
        # Simple per-instance default task type; kernel can override.
        self.default_task_type = "general"

    async def complete(self, prompt: str, temperature: float = 0.2,
                       max_tokens: int = 2048, system: Optional[str] = None,
                       task_type: Optional[str] = None,
                       model_hint: Optional[str] = None) -> str:
        messages: List[Dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        comp = await self.router.complete(
            messages=messages,
            task_type=task_type or self.default_task_type,
            model_hint=model_hint or self.model_hint,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return comp.text

    async def complete_json(self, prompt: str, temperature: float = 0.1,
                            max_tokens: int = 2048,
                            task_type: Optional[str] = None) -> Dict[str, Any]:
        text = await self.complete(prompt, temperature=temperature,
                                   max_tokens=max_tokens, task_type=task_type)
        return _extract_json(text)

    async def complete_with_tools(self, messages: List[Dict[str, Any]],
                                  tools: List[Dict[str, Any]],
                                  temperature: float = 0.1,
                                  task_type: Optional[str] = None) -> Dict[str, Any]:
        comp = await self.router.complete(
            messages=messages, task_type=task_type or "general",
            temperature=temperature, tools=tools,
        )
        # Router returns text only; tool_calls are provider-specific. For the
        # kernel, the planner path uses complete_json, so this is a fallback.
        return {"content": comp.text, "tool_calls": []}


_JSON_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_JSON_OBJ = re.compile(r"(\{.*\})", re.DOTALL)


def _extract_json(text: str) -> Dict[str, Any]:
    if not text:
        return {}
    for pat in (None, _JSON_FENCE, _JSON_OBJ):
        try:
            if pat is None:
                return json.loads(text)
            m = pat.search(text)
            if m:
                return json.loads(m.group(1))
        except Exception:
            continue
    log.error("Could not parse JSON from LLM: %r", text[:500])
    return {}
'''

# =====================================================================
# core/auth.py
# =====================================================================
FILES["core/auth.py"] = r'''from __future__ import annotations
import logging, os, time
from typing import Iterable, Optional, Set
from collections import defaultdict, deque

log = logging.getLogger("agentos.auth")


class RateLimiter:
    """Token-bucket-ish sliding window per key."""
    def __init__(self, max_calls: int = 20, window_seconds: float = 60.0):
        self.max_calls = max_calls
        self.window = window_seconds
        self._hits: defaultdict = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.time()
        dq = self._hits[key]
        while dq and now - dq[0] > self.window:
            dq.popleft()
        if len(dq) >= self.max_calls:
            return False
        dq.append(now)
        return True


class Authorizer:
    """
    Enforces the Telegram allowlist and per-user rate limit.
    Reads TELEGRAM_ALLOWED_USER_IDS as a comma-separated list of ints.
    """
    def __init__(self, allowed_user_ids: Optional[Iterable[int]] = None,
                 rate_limit_per_min: int = 30):
        if allowed_user_ids is None:
            raw = os.getenv("TELEGRAM_ALLOWED_USER_IDS", "")
            allowed_user_ids = [int(x) for x in raw.split(",") if x.strip().isdigit()]
        self.allowed: Set[int] = set(int(x) for x in allowed_user_ids)
        self.rl = RateLimiter(max_calls=rate_limit_per_min, window_seconds=60.0)
        if not self.allowed:
            log.warning("Authorizer: no allowed Telegram user ids configured — "
                        "the bot will reject ALL users. Set TELEGRAM_ALLOWED_USER_IDS.")

    def is_allowed(self, user_id: int) -> bool:
        return user_id in self.allowed

    def check(self, user_id: int) -> tuple[bool, str]:
        if not self.is_allowed(user_id):
            return False, "unauthorized"
        if not self.rl.allow(str(user_id)):
            return False, "rate_limited"
        return True, "ok"
'''

# =====================================================================
# core/cost_tracker.py
# =====================================================================
FILES["core/cost_tracker.py"] = r'''from __future__ import annotations
import asyncio, time
from typing import Dict, List, Optional
from pydantic import BaseModel, Field


class CostEntry(BaseModel):
    ts: float = Field(default_factory=time.time)
    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    task_id: Optional[str] = None


class CostTracker:
    """In-process cost ledger. Persists via TaskManager if provided."""
    def __init__(self, task_manager: Optional[object] = None):
        self._entries: List[CostEntry] = []
        self._per_task: Dict[str, float] = {}
        self._lock = asyncio.Lock()
        self.task_manager = task_manager

    async def record(self, entry: CostEntry) -> None:
        async with self._lock:
            self._entries.append(entry)
            if entry.task_id:
                self._per_task[entry.task_id] = self._per_task.get(entry.task_id, 0.0) + entry.cost_usd
        if self.task_manager and entry.task_id:
            try:
                await self.task_manager.add_task_cost(entry.task_id, entry.cost_usd)
            except Exception:
                pass

    async def task_cost(self, task_id: str) -> float:
        async with self._lock:
            return self._per_task.get(task_id, 0.0)

    async def total_cost(self) -> float:
        async with self._lock:
            return sum(e.cost_usd for e in self._entries)

    def summary(self) -> dict:
        by_provider: Dict[str, float] = {}
        for e in self._entries:
            by_provider[e.provider] = by_provider.get(e.provider, 0.0) + e.cost_usd
        return {"total_usd": sum(by_provider.values()),
                "by_provider": by_provider,
                "calls": len(self._entries)}
'''

# =====================================================================
# core/task_manager.py  (PostgreSQL-backed)
# =====================================================================
FILES["core/task_manager.py"] = r'''from __future__ import annotations
import json, logging, time, uuid
from enum import Enum
from typing import Any, Dict, List, Optional
import asyncpg

log = logging.getLogger("agentos.tasks")


class TaskStatus(str, Enum):
    PENDING = "PENDING"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    WAITING_RETRY = "WAITING_RETRY"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


TERMINAL = {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}


SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
  task_id         TEXT PRIMARY KEY,
  user_id         TEXT NOT NULL,
  chat_id         TEXT,
  description     TEXT NOT NULL,
  domain          TEXT DEFAULT 'general',
  status          TEXT NOT NULL,
  priority        INT  DEFAULT 5,
  entry_agent_id  TEXT NOT NULL,
  current_step    TEXT,
  progress        REAL DEFAULT 0,
  parent_task_id  TEXT,
  retry_count     INT DEFAULT 0,
  max_retries     INT DEFAULT 3,
  budget_usd      REAL,
  cost_usd        REAL DEFAULT 0,
  deadline_ts     DOUBLE PRECISION,
  result          TEXT,
  error           TEXT,
  plan_json       JSONB,
  created_at      DOUBLE PRECISION NOT NULL,
  started_at      DOUBLE PRECISION,
  updated_at      DOUBLE PRECISION NOT NULL,
  completed_at    DOUBLE PRECISION
);
CREATE INDEX IF NOT EXISTS tasks_user_idx ON tasks(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS tasks_status_idx ON tasks(status);

CREATE TABLE IF NOT EXISTS task_checkpoints (
  checkpoint_id   TEXT PRIMARY KEY,
  task_id         TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
  version         INT NOT NULL,
  state_json      JSONB NOT NULL,
  created_at      DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS cp_task_idx ON task_checkpoints(task_id, version DESC);

CREATE TABLE IF NOT EXISTS task_events (
  event_id        TEXT PRIMARY KEY,
  task_id         TEXT NOT NULL,
  event_type      TEXT NOT NULL,
  data            JSONB NOT NULL,
  created_at      DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS te_task_idx ON task_events(task_id, created_at);

CREATE TABLE IF NOT EXISTS scheduled_jobs (
  job_id          TEXT PRIMARY KEY,
  user_id         TEXT NOT NULL,
  chat_id         TEXT,
  description     TEXT NOT NULL,
  cron            TEXT,
  run_at          DOUBLE PRECISION,
  entry_agent_id  TEXT,
  enabled         BOOLEAN DEFAULT TRUE,
  last_run        DOUBLE PRECISION,
  next_run        DOUBLE PRECISION,
  created_at      DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS user_settings (
  user_id         TEXT PRIMARY KEY,
  chat_id         TEXT,
  preferences     JSONB DEFAULT '{}'::jsonb,
  created_at      DOUBLE PRECISION NOT NULL,
  updated_at      DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
  id              BIGSERIAL PRIMARY KEY,
  user_id         TEXT,
  action          TEXT NOT NULL,
  details         JSONB,
  created_at      DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS heartbeats (
  service         TEXT PRIMARY KEY,
  last_seen       DOUBLE PRECISION NOT NULL,
  meta            JSONB
);
"""


class TaskManager:
    def __init__(self, dsn: str):
        self.dsn = dsn
        self.pool: Optional[asyncpg.Pool] = None

    async def connect(self) -> None:
        self.pool = await asyncpg.create_pool(self.dsn, min_size=1, max_size=10)
        async with self.pool.acquire() as c:
            await c.execute(SCHEMA)
        log.info("TaskManager connected and schema applied")

    async def close(self) -> None:
        if self.pool:
            await self.pool.close()

    # ---------------- tasks ----------------

    async def create_task(self, user_id: str, chat_id: Optional[str], description: str,
                          entry_agent_id: str = "coordinator-1",
                          domain: str = "general",
                          priority: int = 5,
                          budget_usd: Optional[float] = None,
                          deadline_ts: Optional[float] = None,
                          parent_task_id: Optional[str] = None,
                          task_id: Optional[str] = None) -> str:
        tid = task_id or str(uuid.uuid4())
        now = time.time()
        assert self.pool is not None
        async with self.pool.acquire() as c:
            await c.execute("""
                INSERT INTO tasks(task_id, user_id, chat_id, description, domain,
                                  status, priority, entry_agent_id, budget_usd,
                                  deadline_ts, parent_task_id, created_at, updated_at)
                VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$12)
            """, tid, str(user_id), str(chat_id) if chat_id else None,
                description, domain, TaskStatus.PENDING.value, priority,
                entry_agent_id, budget_usd, deadline_ts, parent_task_id, now)
        await self.log_event(tid, "task.created",
                             {"user_id": str(user_id), "description": description[:300]})
        return tid

    async def set_status(self, task_id: str, status: TaskStatus,
                         current_step: Optional[str] = None,
                         progress: Optional[float] = None,
                         error: Optional[str] = None,
                         result: Optional[str] = None) -> None:
        sets = ["status=$2", "updated_at=$3"]
        args: List[Any] = [task_id, status.value, time.time()]
        i = 4
        if current_step is not None:
            sets.append(f"current_step=${i}"); args.append(current_step); i += 1
        if progress is not None:
            sets.append(f"progress=${i}"); args.append(progress); i += 1
        if error is not None:
            sets.append(f"error=${i}"); args.append(error); i += 1
        if result is not None:
            sets.append(f"result=${i}"); args.append(result); i += 1
        if status == TaskStatus.RUNNING:
            sets.append("started_at=COALESCE(started_at, $3)")
        if status in TERMINAL:
            sets.append("completed_at=$3")
        sql = f"UPDATE tasks SET {', '.join(sets)} WHERE task_id=$1"
        assert self.pool is not None
        async with self.pool.acquire() as c:
            await c.execute(sql, *args)
        await self.log_event(task_id, f"task.{status.value.lower()}",
                             {"step": current_step, "progress": progress})

    async def add_task_cost(self, task_id: str, delta: float) -> None:
        assert self.pool is not None
        async with self.pool.acquire() as c:
            await c.execute(
                "UPDATE tasks SET cost_usd = COALESCE(cost_usd,0)+$2, updated_at=$3 "
                "WHERE task_id=$1", task_id, delta, time.time())

    async def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        assert self.pool is not None
        async with self.pool.acquire() as c:
            row = await c.fetchrow("SELECT * FROM tasks WHERE task_id=$1", task_id)
        return dict(row) if row else None

    async def list_tasks(self, user_id: Optional[str] = None,
                         status: Optional[str] = None, limit: int = 20) -> List[Dict[str, Any]]:
        q = "SELECT * FROM tasks WHERE 1=1"
        args: List[Any] = []
        if user_id:
            args.append(str(user_id)); q += f" AND user_id=${len(args)}"
        if status:
            args.append(status); q += f" AND status=${len(args)}"
        q += f" ORDER BY created_at DESC LIMIT {int(limit)}"
        assert self.pool is not None
        async with self.pool.acquire() as c:
            rows = await c.fetch(q, *args)
        return [dict(r) for r in rows]

    async def claim_next(self) -> Optional[Dict[str, Any]]:
        """Atomically select the highest-priority PENDING task and mark RUNNING."""
        assert self.pool is not None
        async with self.pool.acquire() as c:
            async with c.transaction():
                row = await c.fetchrow("""
                    SELECT * FROM tasks
                    WHERE status IN ($1, $2)
                    ORDER BY priority ASC, created_at ASC
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                """, TaskStatus.PENDING.value, TaskStatus.WAITING_RETRY.value)
                if not row:
                    return None
                await c.execute(
                    "UPDATE tasks SET status=$2, started_at=COALESCE(started_at,$3), "
                    "updated_at=$3 WHERE task_id=$1",
                    row["task_id"], TaskStatus.RUNNING.value, time.time())
        return dict(row)

    async def stale_running_tasks(self, older_than_seconds: float = 300) -> List[Dict[str, Any]]:
        """For watchdog: RUNNING tasks whose updated_at is older than the threshold."""
        cutoff = time.time() - older_than_seconds
        assert self.pool is not None
        async with self.pool.acquire() as c:
            rows = await c.fetch(
                "SELECT * FROM tasks WHERE status=$1 AND updated_at < $2",
                TaskStatus.RUNNING.value, cutoff)
        return [dict(r) for r in rows]

    # ---------------- checkpoints ----------------

    async def save_checkpoint(self, task_id: str, state: Dict[str, Any]) -> int:
        assert self.pool is not None
        async with self.pool.acquire() as c:
            async with c.transaction():
                v = await c.fetchval(
                    "SELECT COALESCE(MAX(version),0)+1 FROM task_checkpoints WHERE task_id=$1",
                    task_id)
                await c.execute("""
                    INSERT INTO task_checkpoints(checkpoint_id, task_id, version,
                                                 state_json, created_at)
                    VALUES($1,$2,$3,$4,$5)
                """, str(uuid.uuid4()), task_id, v, json.dumps(state), time.time())
        return int(v)

    async def latest_checkpoint(self, task_id: str) -> Optional[Dict[str, Any]]:
        assert self.pool is not None
        async with self.pool.acquire() as c:
            row = await c.fetchrow(
                "SELECT state_json FROM task_checkpoints WHERE task_id=$1 "
                "ORDER BY version DESC LIMIT 1", task_id)
        if not row:
            return None
        s = row["state_json"]
        return json.loads(s) if isinstance(s, str) else s

    # ---------------- events ----------------

    async def log_event(self, task_id: str, event_type: str,
                        data: Optional[Dict[str, Any]] = None) -> None:
        if self.pool is None:
            return
        assert self.pool is not None
        async with self.pool.acquire() as c:
            await c.execute("""
                INSERT INTO task_events(event_id, task_id, event_type, data, created_at)
                VALUES($1,$2,$3,$4,$5)
            """, str(uuid.uuid4()), task_id, event_type,
                json.dumps(data or {}), time.time())

    async def list_events(self, task_id: str, limit: int = 200) -> List[Dict[str, Any]]:
        assert self.pool is not None
        async with self.pool.acquire() as c:
            rows = await c.fetch(
                "SELECT * FROM task_events WHERE task_id=$1 ORDER BY created_at ASC LIMIT $2",
                task_id, limit)
        out = []
        for r in rows:
            d = dict(r)
            if isinstance(d.get("data"), str):
                d["data"] = json.loads(d["data"])
            out.append(d)
        return out

    # ---------------- scheduled jobs ----------------

    async def create_scheduled_job(self, user_id: str, chat_id: Optional[str],
                                   description: str, cron: Optional[str],
                                   run_at: Optional[float], entry_agent_id: str = "coordinator-1",
                                   next_run: Optional[float] = None) -> str:
        jid = str(uuid.uuid4())
        assert self.pool is not None
        async with self.pool.acquire() as c:
            await c.execute("""
                INSERT INTO scheduled_jobs(job_id, user_id, chat_id, description,
                                           cron, run_at, entry_agent_id, next_run, created_at)
                VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9)
            """, jid, str(user_id), str(chat_id) if chat_id else None,
                description, cron, run_at, entry_agent_id, next_run, time.time())
        return jid

    async def list_scheduled_jobs(self, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        assert self.pool is not None
        if user_id:
            async with self.pool.acquire() as c:
                rows = await c.fetch(
                    "SELECT * FROM scheduled_jobs WHERE user_id=$1 ORDER BY created_at DESC",
                    str(user_id))
        else:
            async with self.pool.acquire() as c:
                rows = await c.fetch("SELECT * FROM scheduled_jobs ORDER BY created_at DESC")
        return [dict(r) for r in rows]

    async def due_scheduled_jobs(self, now: Optional[float] = None) -> List[Dict[str, Any]]:
        now = now or time.time()
        assert self.pool is not None
        async with self.pool.acquire() as c:
            rows = await c.fetch(
                "SELECT * FROM scheduled_jobs WHERE enabled=TRUE AND next_run IS NOT NULL "
                "AND next_run <= $1", now)
        return [dict(r) for r in rows]

    async def update_job_next_run(self, job_id: str, next_run: Optional[float]) -> None:
        assert self.pool is not None
        async with self.pool.acquire() as c:
            await c.execute(
                "UPDATE scheduled_jobs SET last_run=$2, next_run=$3 WHERE job_id=$1",
                job_id, time.time(), next_run)

    # ---------------- audit / heartbeat ----------------

    async def audit(self, user_id: Optional[str], action: str,
                    details: Optional[Dict[str, Any]] = None) -> None:
        assert self.pool is not None
        async with self.pool.acquire() as c:
            await c.execute("""
                INSERT INTO audit_log(user_id, action, details, created_at)
                VALUES($1,$2,$3,$4)
            """, str(user_id) if user_id else None, action,
                json.dumps(details or {}), time.time())

    async def heartbeat(self, service: str, meta: Optional[Dict[str, Any]] = None) -> None:
        assert self.pool is not None
        async with self.pool.acquire() as c:
            await c.execute("""
                INSERT INTO heartbeats(service, last_seen, meta) VALUES($1,$2,$3)
                ON CONFLICT (service) DO UPDATE
                  SET last_seen=EXCLUDED.last_seen, meta=EXCLUDED.meta
            """, service, time.time(), json.dumps(meta or {}))

    async def read_heartbeats(self) -> List[Dict[str, Any]]:
        assert self.pool is not None
        async with self.pool.acquire() as c:
            rows = await c.fetch("SELECT * FROM heartbeats ORDER BY service")
        return [dict(r) for r in rows]
'''

# =====================================================================
# core/checkpoints.py  (small helper)
# =====================================================================
FILES["core/checkpoints.py"] = r'''from __future__ import annotations
from typing import Any, Dict, Optional
from .models import Plan, Task


class CheckpointState:
    """Serializable snapshot of an in-flight task."""

    @staticmethod
    def serialize(task: Task, plan: Optional[Plan], step_index: int,
                  step_outputs: list, iteration: int, replans: int,
                  extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return {
            "task": task.model_dump(),
            "plan": plan.model_dump() if plan else None,
            "step_index": step_index,
            "step_outputs": step_outputs,
            "iteration": iteration,
            "replans": replans,
            "extra": extra or {},
        }

    @staticmethod
    def deserialize(state: Dict[str, Any]):
        t = Task(**state["task"])
        p = Plan(**state["plan"]) if state.get("plan") else None
        return t, p, state.get("step_index", 0), state.get("step_outputs", []), \
               state.get("iteration", 0), state.get("replans", 0)
'''

# =====================================================================
# core/scheduler.py
# =====================================================================
FILES["core/scheduler.py"] = r'''from __future__ import annotations
import asyncio, logging, os, time
from datetime import datetime, timezone
from typing import Optional
from croniter import croniter
from .task_manager import TaskManager
from .queue import TaskQueue

log = logging.getLogger("agentos.scheduler")


def _next_cron_ts(cron: str, base: Optional[float] = None) -> float:
    base_dt = datetime.fromtimestamp(base or time.time(), tz=timezone.utc)
    it = croniter(cron, base_dt)
    return it.get_next(datetime).timestamp()


class Scheduler:
    """
    Polls PostgreSQL for due scheduled jobs every N seconds and enqueues tasks.
    Persistent across restarts because jobs live in `scheduled_jobs`.
    """

    def __init__(self, task_manager: TaskManager, queue: "TaskQueue",
                 poll_seconds: float = 15.0):
        self.tm = task_manager
        self.queue = queue
        self.poll_seconds = poll_seconds
        self._stop = asyncio.Event()

    async def start(self) -> None:
        log.info("Scheduler started (poll=%ss)", self.poll_seconds)
        while not self._stop.is_set():
            try:
                await self._tick()
            except Exception:
                log.exception("Scheduler tick failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.poll_seconds)
            except asyncio.TimeoutError:
                pass

    async def stop(self) -> None:
        self._stop.set()

    async def _tick(self) -> None:
        due = await self.tm.due_scheduled_jobs()
        for job in due:
            try:
                tid = await self.tm.create_task(
                    user_id=job["user_id"], chat_id=job["chat_id"],
                    description=job["description"],
                    entry_agent_id=job.get("entry_agent_id") or "coordinator-1",
                    domain="scheduled")
                await self.queue.enqueue(tid)
                next_run: Optional[float] = None
                if job.get("cron"):
                    next_run = _next_cron_ts(job["cron"])
                await self.tm.update_job_next_run(job["job_id"], next_run)
                await self.tm.log_event(tid, "scheduler.fired",
                                        {"job_id": job["job_id"]})
                log.info("Scheduled job %s fired → task %s", job["job_id"], tid)
            except Exception:
                log.exception("Failed to fire scheduled job %s", job.get("job_id"))
'''

# =====================================================================
# core/queue.py  (Redis-Streams backed)
# =====================================================================
FILES["core/queue.py"] = r'''from __future__ import annotations
import asyncio, json, logging, os
from typing import Any, Dict, List, Optional

log = logging.getLogger("agentos.queue")

STREAM = "agentos:tasks"
GROUP = "agentos:workers"
DLQ = "agentos:tasks:dlq"


class TaskQueue:
    """
    Redis Streams backed queue with a consumer group.
    - Survives worker restarts (messages persist until acked).
    - Uses XAUTOCLAIM to reclaim messages from dead workers.
    """

    def __init__(self, redis_url: str, consumer_name: str = "worker-1"):
        self.redis_url = redis_url
        self.consumer_name = consumer_name
        self._r = None

    async def connect(self) -> None:
        import redis.asyncio as aioredis
        self._r = aioredis.from_url(self.redis_url, decode_responses=True)
        try:
            await self._r.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
            log.info("Created consumer group %s on %s", GROUP, STREAM)
        except Exception as e:
            if "BUSYGROUP" not in str(e):
                raise
        log.info("TaskQueue connected to %s", self.redis_url)

    async def enqueue(self, task_id: str, extra: Optional[Dict[str, Any]] = None) -> str:
        assert self._r is not None
        msg = {"task_id": task_id}
        if extra:
            msg.update({k: json.dumps(v) if not isinstance(v, str) else v
                        for k, v in extra.items()})
        return await self._r.xadd(STREAM, msg)

    async def claim_one(self, block_ms: int = 5000) -> Optional[Dict[str, Any]]:
        assert self._r is not None
        # First, try to reclaim abandoned messages from dead workers.
        reclaimed = await self._reclaim()
        if reclaimed:
            return reclaimed
        # Then read new messages.
        res = await self._r.xreadgroup(
            GROUP, self.consumer_name, streams={STREAM: ">"},
            count=1, block=block_ms)
        if not res:
            return None
        _, entries = res[0]
        if not entries:
            return None
        entry_id, data = entries[0]
        return {"entry_id": entry_id, "data": data}

    async def _reclaim(self) -> Optional[Dict[str, Any]]:
        assert self._r is not None
        try:
            res = await self._r.xautoclaim(
                STREAM, GROUP, self.consumer_name,
                min_idle_time=60_000, start_id="0-0", count=1)
            if res and len(res) >= 2 and res[1]:
                entry_id, data = res[1][0]
                log.warning("Reclaimed abandoned message %s", entry_id)
                return {"entry_id": entry_id, "data": data, "reclaimed": True}
        except Exception as e:
            log.debug("xautoclaim failed: %s", e)
        return None

    async def ack(self, entry_id: str) -> None:
        assert self._r is not None
        await self._r.xack(STREAM, GROUP, entry_id)

    async def to_dlq(self, entry_id: str, task_id: str, error: str) -> None:
        assert self._r is not None
        await self._r.xadd(DLQ, {"task_id": task_id, "error": error[:1000]})
        await self.ack(entry_id)

    async def close(self) -> None:
        if self._r:
            await self._r.aclose()
'''

# =====================================================================
# core/worker.py  (PATCHED — durable, checkpointed)
# =====================================================================
FILES["core/worker.py"] = r'''from __future__ import annotations
import asyncio, logging, os, signal, sys, time
from typing import Any, Dict, Optional

from .approval import ApprovalGate, ApprovalRequest
from .comm_bus import CommunicationBus
from .events import EventStore
from .factory import AgentFactory
from .llm import LLMClient
from .models import Task, TaskResult
from .orchestrator import Orchestrator
from .reliability import ReliabilityEngine
from .task_manager import TaskManager, TaskStatus
from .queue import TaskQueue
from .cost_tracker import CostTracker, CostEntry
from .model_router import ModelRouter
from .tools import Sandbox, ToolRegistry
from .health import Heartbeat

log = logging.getLogger("agentos.worker")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")


class Worker:
    def __init__(self, name: str):
        self.name = name
        self.stop = asyncio.Event()
        self.tm: Optional[TaskManager] = None
        self.queue: Optional[TaskQueue] = None
        self.orch: Optional[Orchestrator] = None
        self.cost: Optional[CostTracker] = None
        self.hb: Optional[Heartbeat] = None

    async def setup(self) -> None:
        from demo.agents import build_demo_specs, register_demo_tools
        dsn = os.getenv("DATABASE_URL", "postgresql://agent:agentpass@postgres/agents")
        redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")

        self.tm = TaskManager(dsn)
        await self.tm.connect()
        self.queue = TaskQueue(redis_url, consumer_name=self.name)
        await self.queue.connect()
        self.cost = CostTracker(task_manager=self.tm)
        self.hb = Heartbeat(self.tm, service_name=f"worker:{self.name}", interval=15.0)
        await self.hb.start()

        tools = ToolRegistry()
        register_demo_tools(tools)

        events = EventStore(dsn=dsn)
        await events.connect()
        comm = CommunicationBus()
        router = ModelRouter()
        llm = LLMClient(router=router)
        approval = ApprovalGate(
            auto_approve_in_dev=os.getenv("AUTO_APPROVE", "false").lower() == "true")
        factory = AgentFactory(
            llm=llm, tools=tools, event_store=events, comm_bus=comm,
            reliability=ReliabilityEngine(), approval_gate=approval,
            sandbox=Sandbox(prefer_docker=os.getenv("SANDBOX_MODE", "docker") == "docker"),
            memory_persist_dir=os.getenv("CHROMA_DIR"))
        self.orch = Orchestrator(events, comm)
        for spec in build_demo_specs():
            self.orch.register_agent(factory.build(spec))
        log.info("Worker %s ready with %d agents", self.name, len(self.orch.agents))

    async def loop(self) -> None:
        assert self.queue and self.tm and self.orch
        while not self.stop.is_set():
            try:
                msg = await self.queue.claim_one(block_ms=3000)
            except Exception as e:
                log.error("Queue read failed: %s", e)
                await asyncio.sleep(2)
                continue
            if not msg:
                continue
            entry_id = msg["entry_id"]
            task_id = msg["data"].get("task_id")
            if not task_id:
                await self.queue.to_dlq(entry_id, "?", "missing task_id")
                continue
            try:
                await self._run_task(task_id)
                await self.queue.ack(entry_id)
            except Exception as e:
                log.exception("Task %s crashed", task_id)
                await self.tm.set_status(task_id, TaskStatus.FAILED, error=str(e))
                await self.queue.to_dlq(entry_id, task_id, str(e))

    async def _run_task(self, task_id: str) -> None:
        assert self.tm and self.orch
        row = await self.tm.get_task(task_id)
        if not row:
            log.warning("Task %s not found in DB", task_id)
            return
        if row["status"] in (TaskStatus.COMPLETED.value, TaskStatus.CANCELLED.value):
            return
        if row["status"] == TaskStatus.PAUSED.value:
            log.info("Task %s is paused; skipping", task_id)
            return

        await self.tm.set_status(task_id, TaskStatus.RUNNING, current_step="starting")
        await self.tm.log_event(task_id, "worker.claimed", {"worker": self.name})

        task = Task(id=task_id, description=row["description"],
                    domain=row.get("domain") or "general")
        entry_agent = row.get("entry_agent_id") or "coordinator-1"

        # Load latest checkpoint if any (for resume).
        cp = await self.tm.latest_checkpoint(task_id)
        if cp:
            await self.tm.log_event(task_id, "worker.resumed",
                                    {"from_checkpoint": True,
                                     "step_index": cp.get("step_index")})

        try:
            # Snapshot before running.
            await self.tm.save_checkpoint(task_id, {"phase": "pre_run",
                                                    "task": task.model_dump()})
            result: TaskResult = await self.orch.run_task(task, entry_agent)
            # Save result and status.
            if result.success:
                await self.tm.set_status(task_id, TaskStatus.COMPLETED,
                                         result=result.summary,
                                         progress=1.0,
                                         current_step="completed")
            else:
                rc = int(row.get("retry_count") or 0)
                max_r = int(row.get("max_retries") or 3)
                if rc < max_r:
                    await self.tm.set_status(task_id, TaskStatus.WAITING_RETRY,
                                             error=result.error or "unknown",
                                             current_step=f"retry {rc+1}/{max_r}")
                    # Re-enqueue with backoff via the queue.
                    await self.queue.enqueue(task_id, {"retry": str(rc + 1)})
                else:
                    await self.tm.set_status(task_id, TaskStatus.FAILED,
                                             error=result.error or "unknown",
                                             result=result.summary)
            await self.tm.save_checkpoint(task_id, {"phase": "post_run",
                                                    "task": task.model_dump(),
                                                    "result": result.model_dump()})
            await self.tm.log_event(task_id, "worker.finished",
                                    {"success": result.success})
        except Exception as e:
            log.exception("Task %s failed inside kernel", task_id)
            await self.tm.save_checkpoint(task_id, {"phase": "error",
                                                    "error": str(e)})
            raise

    async def shutdown(self) -> None:
        self.stop.set()
        if self.hb:
            await self.hb.stop()
        if self.queue:
            await self.queue.close()
        if self.tm:
            await self.tm.close()


async def _main():
    name = os.getenv("WORKER_NAME", f"worker-{os.getpid()}")
    w = Worker(name)
    await w.setup()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda: asyncio.create_task(w.shutdown()))
        except NotImplementedError:
            pass

    await w.loop()


if __name__ == "__main__":
    asyncio.run(_main())
'''

# =====================================================================
# core/health.py
# =====================================================================
FILES["core/health.py"] = r'''from __future__ import annotations
import asyncio, logging, os, time
from typing import Any, Dict, Optional

log = logging.getLogger("agentos.health")


class Heartbeat:
    """Periodically writes a heartbeat row to PostgreSQL."""

    def __init__(self, task_manager, service_name: str, interval: float = 15.0,
                 meta: Optional[Dict[str, Any]] = None):
        self.tm = task_manager
        self.service = service_name
        self.interval = interval
        self.meta = meta or {}
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=3)
            except Exception:
                pass

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.tm.heartbeat(self.service, self.meta)
            except Exception as e:
                log.debug("Heartbeat failed: %s", e)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval)
            except asyncio.TimeoutError:
                pass


async def system_health(task_manager, model_router, redis_url: str) -> Dict[str, Any]:
    """Aggregate health for /health endpoint."""
    out: Dict[str, Any] = {"ts": time.time()}
    # DB
    try:
        await task_manager.read_heartbeats()
        out["postgres"] = True
    except Exception:
        out["postgres"] = False
    # Redis
    try:
        import redis.asyncio as aioredis
        r = aioredis.from_url(redis_url, decode_responses=True)
        await r.ping()
        await r.aclose()
        out["redis"] = True
    except Exception:
        out["redis"] = False
    # Providers
    out["providers"] = await model_router.provider_health()
    # Workers (heartbeats fresh within 60s)
    try:
        rows = await task_manager.read_heartbeats()
        now = time.time()
        out["workers"] = {
            r["service"]: (now - r["last_seen"] < 60.0)
            for r in rows if r["service"].startswith("worker:")
        }
    except Exception:
        out["workers"] = {}
    return out
'''

# =====================================================================
# core/watchdog.py
# =====================================================================
FILES["core/watchdog.py"] = r'''from __future__ import annotations
import asyncio, logging, os, time
from typing import Optional
from .task_manager import TaskManager, TaskStatus
from .queue import TaskQueue

log = logging.getLogger("agentos.watchdog")


class Watchdog:
    """
    Detects and recovers:
      * stuck RUNNING tasks (updated_at too old) → re-enqueue
      * dead workers (missing heartbeats) → log (Docker restarts the container)
      * excessive retries → move to FAILED
      * provider outages → handled by ModelRouter circuit breaker
    """

    def __init__(self, task_manager: TaskManager, queue: TaskQueue,
                 stuck_after_seconds: float = 600.0, poll_seconds: float = 30.0):
        self.tm = task_manager
        self.queue = queue
        self.stuck_after = stuck_after_seconds
        self.poll = poll_seconds
        self._stop = asyncio.Event()

    async def start(self) -> None:
        log.info("Watchdog started (stuck_after=%ss, poll=%ss)",
                 self.stuck_after, self.poll)
        while not self._stop.is_set():
            try:
                await self._tick()
            except Exception:
                log.exception("Watchdog tick failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.poll)
            except asyncio.TimeoutError:
                pass

    async def stop(self) -> None:
        self._stop.set()

    async def _tick(self) -> None:
        stuck = await self.tm.stale_running_tasks(older_than_seconds=self.stuck_after)
        for t in stuck:
            tid = t["task_id"]
            rc = int(t.get("retry_count") or 0)
            log.warning("Watchdog: task %s stuck for > %ss (retries=%d)",
                        tid, self.stuck_after, rc)
            await self.tm.log_event(tid, "watchdog.stuck",
                                    {"updated_at": t["updated_at"], "retries": rc})
            if rc < int(t.get("max_retries") or 3):
                # Requeue with incremented retry count.
                await self.tm.set_status(tid, TaskStatus.WAITING_RETRY,
                                         current_step="watchdog requeue",
                                         error="watchdog: stuck")
                await self.tm.pool.execute(
                    "UPDATE tasks SET retry_count = COALESCE(retry_count,0)+1 WHERE task_id=$1",
                    tid)
                await self.queue.enqueue(tid, {"requeued_by": "watchdog"})
            else:
                await self.tm.set_status(tid, TaskStatus.FAILED,
                                         error="watchdog: max retries exceeded")

    async def health(self) -> dict:
        try:
            stuck = await self.tm.stale_running_tasks(self.stuck_after)
            return {"stuck_tasks": len(stuck)}
        except Exception as e:
            return {"error": str(e)}
'''

# =====================================================================
# telegram_bot/__init__.py
# =====================================================================
FILES["telegram_bot/__init__.py"] = r'''"""AgentOS Telegram bot package (aiogram 3)."""
'''

# =====================================================================
# telegram_bot/security.py
# =====================================================================
FILES["telegram_bot/security.py"] = r'''from __future__ import annotations
import logging
import os
from typing import Optional
from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject, CallbackQuery

from core.auth import Authorizer

log = logging.getLogger("agentos.tg.security")


class AuthMiddleware(BaseMiddleware):
    """Rejects non-allowlisted users with a polite message; enforces rate limit."""

    def __init__(self, authorizer: Optional[Authorizer] = None):
        super().__init__()
        self.auth = authorizer or Authorizer()

    async def __call__(self, handler, event: TelegramObject, data):
        user_id = None
        if isinstance(event, Message) and event.from_user:
            user_id = event.from_user.id
        elif isinstance(event, CallbackQuery) and event.from_user:
            user_id = event.from_user.id
        if user_id is None:
            return
        ok, why = self.auth.check(user_id)
        if not ok:
            log.warning("Rejected Telegram user %s (%s)", user_id, why)
            if isinstance(event, Message):
                await event.answer(
                    "⛔ Unauthorized.\n"
                    f"Your Telegram user id is: `{user_id}`\n"
                    "Ask the operator to add you to TELEGRAM_ALLOWED_USER_IDS.",
                    parse_mode="Markdown")
            elif isinstance(event, CallbackQuery):
                await event.answer("⛔ Unauthorized.", show_alert=True)
            return
        return await handler(event, data)
'''

# =====================================================================
# telegram_bot/notifier.py
# =====================================================================
FILES["telegram_bot/notifier.py"] = r'''from __future__ import annotations
import logging, os
from typing import Optional
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

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


_notifier: Optional[Notifier] = None


def get_notifier() -> Notifier:
    global _notifier
    if _notifier is None:
        _notifier = Notifier()
    return _notifier
'''

# =====================================================================
# telegram_bot/keyboards.py
# =====================================================================
FILES["telegram_bot/keyboards.py"] = r'''from __future__ import annotations
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def approval_kb(request_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ APPROVE", callback_data=f"appr:yes:{request_id}"),
        InlineKeyboardButton(text="❌ DENY",    callback_data=f"appr:no:{request_id}"),
    ]])


def task_actions_kb(task_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏸ Pause",  callback_data=f"task:pause:{task_id}"),
         InlineKeyboardButton(text="▶️ Resume", callback_data=f"task:resume:{task_id}")],
        [InlineKeyboardButton(text="❌ Cancel", callback_data=f"task:cancel:{task_id}"),
         InlineKeyboardButton(text="🔁 Retry",  callback_data=f"task:retry:{task_id}")],
    ])


def confirm_kb(action: str, token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Confirm", callback_data=f"conf:yes:{action}:{token}"),
        InlineKeyboardButton(text="❌ Cancel",  callback_data=f"conf:no:{action}:{token}"),
    ]])
'''

# =====================================================================
# telegram_bot/handlers.py
# =====================================================================
FILES["telegram_bot/handlers.py"] = r'''from __future__ import annotations
import asyncio, json, logging, os, time
from typing import Any, Dict, Optional
from aiogram import Router, F
from aiogram.filters import Command, CommandStart, CommandObject
from aiogram.types import Message, CallbackQuery

from core.task_manager import TaskManager, TaskStatus
from core.queue import TaskQueue
from core.approval import ApprovalGate
from core.model_router import ModelRouter
from .keyboards import approval_kb, task_actions_kb
from .notifier import Notifier

log = logging.getLogger("agentos.tg.handlers")
router = Router()

# These are wired at startup by bot.py:
STATE: Dict[str, Any] = {}


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
    name = message.from_user.first_name if message.from_user else "there"
    await message.answer(
        f"👋 Hi {name}. I'm AgentOS — your 24/7 autonomous agent.\n\n"
        "Send me a task in plain English and close Telegram. "
        "I'll keep working on the server and message you when it's done.\n\n"
        "Commands:\n"
        "/task <description> — start a new task\n"
        "/tasks — list your tasks\n"
        "/status — platform health\n"
        "/agents — list agents\n"
        "/agent <id> — show an agent\n"
        "/pause <id> · /resume <id> · /cancel <id> · /retry <id>\n"
        "/logs <id> — task execution trace\n"
        "/memory — recent memory\n"
        "/schedule <cron> :: <task> — schedule recurring\n"
        "/settings — view/change settings\n"
        "/help — this message"
    )


@router.message(Command("help"))
async def cmd_help(message: Message):
    await cmd_start(message, None)  # type: ignore[arg-type]


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
    await _create_task_and_ack(message, text)



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
                text = "🤖 AgentOS\\n\\n" + bar + " 100%\\n\\n✅ Task completed\\nID: " + task_id[:8]
            elif status == "FAILED":
                text = "🤖 AgentOS\\n\\n" + bar + " " + ("%.0f" % progress) + "%\\n\\n❌ Task failed\\nID: " + task_id[:8] + "\\n\\nError: " + str(row.get("error") or "unknown")[:1000]
            else:
                text = "🤖 AgentOS\\n\\n" + bar + " " + ("%.0f" % progress) + "%\\n\\n" + label + "\\nID: " + task_id[:8]
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
    short = tid[:8]
    sent = await message.answer(
        "🤖 AgentOS\\n\\n"
        "░░░░░░░░░░░░░░░░░░░░ 0%\\n\\n"
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
'''

# =====================================================================
# telegram_bot/bot.py
# =====================================================================
FILES["telegram_bot/bot.py"] = r'''from __future__ import annotations
import asyncio, logging, os, signal
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from core.task_manager import TaskManager
from core.queue import TaskQueue
from core.approval import ApprovalGate
from core.model_router import ModelRouter
from core.orchestrator import Orchestrator
from core.events import EventStore
from core.comm_bus import CommunicationBus
from core.factory import AgentFactory
from core.reliability import ReliabilityEngine
from core.tools import ToolRegistry, Sandbox
from core.llm import LLMClient
from demo.agents import build_demo_specs, register_demo_tools

from .handlers import router, STATE
from .security import AuthMiddleware
from .notifier import get_notifier

log = logging.getLogger("agentos.tg.bot")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")


async def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is required")
    dsn = os.getenv("DATABASE_URL", "postgresql://agent:agentpass@postgres/agents")
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")

    # --- shared infrastructure ---
    tm = TaskManager(dsn); await tm.connect()
    q = TaskQueue(redis_url, consumer_name="telegram-bot"); await q.connect()

    # --- orchestrator for read-only /status & /agents queries ---
    tools = ToolRegistry(); register_demo_tools(tools)
    events = EventStore(dsn=dsn); await events.connect()
    comm = CommunicationBus()
    router_model = ModelRouter()
    llm = LLMClient(router=router_model)
    approval = ApprovalGate(auto_approve_in_dev=os.getenv("AUTO_APPROVE","false").lower()=="true")
    factory = AgentFactory(llm=llm, tools=tools, event_store=events, comm_bus=comm,
                           reliability=ReliabilityEngine(), approval_gate=approval,
                           sandbox=Sandbox(prefer_docker=os.getenv("SANDBOX_MODE","docker")=="docker"))
    orch = Orchestrator(events, comm)
    for spec in build_demo_specs():
        orch.register_agent(factory.build(spec))

    # --- notifier ---
    notifier = get_notifier()
    await notifier.start()

    # --- expose shared state to handlers ---
    STATE.update(dict(tm=tm, queue=q, router=router_model, orch=orch,
                      approval=approval, notifier=notifier, redis_url=redis_url))

    # --- aiogram ---
    bot = Bot(token, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))
    STATE["bot"] = bot
    dp = Dispatcher()
    dp.message.middleware(AuthMiddleware())
    dp.callback_query.middleware(AuthMiddleware())
    dp.include_router(router)

    stop = asyncio.Event()
    loop = asyncio.get_event_loop()
    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(s, stop.set)
        except NotImplementedError:
            pass

    # Background: poll for status changes and notify chat
    notif_task = asyncio.create_task(_status_notifier_loop(tm, notifier))

    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        stop.set()
        notif_task.cancel()
        await notifier.stop()
        await q.close(); await tm.close()


async def _status_notifier_loop(tm: TaskManager, notifier) -> None:
    """
    Every 15s, check for tasks that moved into terminal state and were not yet
    notified, then send the result to the user's chat.
    """
    seen: set[str] = set()
    while True:
        try:
            async with tm.pool.acquire() as c:
                rows = await c.fetch("""
                    SELECT * FROM tasks
                    WHERE status IN ('COMPLETED','FAILED','WAITING_APPROVAL')
                    ORDER BY updated_at DESC LIMIT 50
                """)
            for r in rows:
                key = f"{r['task_id']}:{r['status']}"
                if key in seen:
                    continue
                seen.add(key)
                chat_id = r.get("chat_id")
                if not chat_id:
                    continue
                short = r["task_id"][:8]
                if r["status"] == "COMPLETED":
                    elapsed = (r.get("completed_at") or 0) - (r.get("started_at") or 0)
                    txt = (f"✅ *Task completed* (`{short}`)\n"
                           f"⏱ {elapsed:.0f}s\n\n"
                           f"*Result:*\n{(r.get('result') or '')[:3000]}")
                elif r["status"] == "FAILED":
                    txt = (f"❌ *Task failed* (`{short}`)\n\n"
                           f"*Error:* {(r.get('error') or 'unknown')[:1500]}")
                else:
                    txt = (f"⏸ Task `{short}` is waiting for approval.")
                await notifier.send(chat_id, txt)
        except Exception as e:
            log.debug("notifier loop: %s", e)
        await asyncio.sleep(15)


if __name__ == "__main__":
    asyncio.run(main())
'''

# =====================================================================
# core/worker_health.py  (scheduler / watchdog entrypoints as standalone)
# =====================================================================
FILES["core/scheduler_entry.py"] = r'''from __future__ import annotations
import asyncio, logging, os, signal
from .task_manager import TaskManager
from .queue import TaskQueue
from .scheduler import Scheduler
from .health import Heartbeat

log = logging.getLogger("agentos.scheduler_entry")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")


async def main():
    dsn = os.getenv("DATABASE_URL", "postgresql://agent:agentpass@postgres/agents")
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
    tm = TaskManager(dsn); await tm.connect()
    q = TaskQueue(redis_url, consumer_name="scheduler"); await q.connect()
    hb = Heartbeat(tm, "scheduler", 15.0); await hb.start()
    sch = Scheduler(tm, q, poll_seconds=float(os.getenv("SCHEDULER_POLL", "15")))
    stop = asyncio.Event()
    loop = asyncio.get_event_loop()
    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(s, stop.set)
        except NotImplementedError:
            pass
    runner = asyncio.create_task(sch.start())
    await stop.wait()
    await sch.stop()
    runner.cancel()
    await hb.stop()
    await q.close(); await tm.close()


if __name__ == "__main__":
    asyncio.run(main())
'''

FILES["core/watchdog_entry.py"] = r'''from __future__ import annotations
import asyncio, logging, os, signal
from .task_manager import TaskManager
from .queue import TaskQueue
from .watchdog import Watchdog
from .health import Heartbeat

log = logging.getLogger("agentos.watchdog_entry")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")


async def main():
    dsn = os.getenv("DATABASE_URL", "postgresql://agent:agentpass@postgres/agents")
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
    tm = TaskManager(dsn); await tm.connect()
    q = TaskQueue(redis_url, consumer_name="watchdog"); await q.connect()
    hb = Heartbeat(tm, "watchdog", 15.0); await hb.start()
    wd = Watchdog(tm, q,
                  stuck_after_seconds=float(os.getenv("STUCK_AFTER_SECONDS", "600")),
                  poll_seconds=float(os.getenv("WATCHDOG_POLL", "30")))
    stop = asyncio.Event()
    loop = asyncio.get_event_loop()
    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(s, stop.set)
        except NotImplementedError:
            pass
    runner = asyncio.create_task(wd.start())
    await stop.wait()
    await wd.stop()
    runner.cancel()
    await hb.stop()
    await q.close(); await tm.close()


if __name__ == "__main__":
    asyncio.run(main())
'''

# =====================================================================
# api/main.py  (PATCHED)
# =====================================================================
FILES["api/main.py"] = r'''from __future__ import annotations
import logging, os
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from core.model_router import ModelRouter
from core.task_manager import TaskManager, TaskStatus
from core.queue import TaskQueue
from core.cost_tracker import CostTracker
from core.health import system_health
from core.scheduler import _next_cron_ts
from core.tracing import setup_tracing

log = logging.getLogger("agentos.api")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

STATE: Dict[str, Any] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_tracing("agentos-api")
    dsn = os.getenv("DATABASE_URL", "postgresql://agent:agentpass@postgres/agents")
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
    tm = TaskManager(dsn); await tm.connect()
    q = TaskQueue(redis_url, consumer_name="api"); await q.connect()
    router = ModelRouter()
    cost = CostTracker(task_manager=tm)
    STATE.update(dict(tm=tm, queue=q, router=router, cost=cost, redis_url=redis_url))
    log.info("API ready with providers: %s", router.available_providers())
    yield
    await q.close(); await tm.close()
    STATE.clear()


app = FastAPI(title="AgentOS API", version="2.0.0", lifespan=lifespan)


# ---------------- schemas ----------------

class TaskCreate(BaseModel):
    description: str
    user_id: str = "api"
    chat_id: Optional[str] = None
    entry_agent_id: str = "coordinator-1"
    domain: str = "general"
    priority: int = 5
    budget_usd: Optional[float] = None
    deadline_ts: Optional[float] = None


class TaskOut(BaseModel):
    task_id: str
    user_id: str
    status: str
    description: str
    progress: float = 0.0
    result: Optional[str] = None
    error: Optional[str] = None
    cost_usd: float = 0.0


class ScheduleCreate(BaseModel):
    user_id: str
    chat_id: Optional[str] = None
    description: str
    cron: Optional[str] = None
    run_at: Optional[float] = None
    entry_agent_id: str = "coordinator-1"


# ---------------- tasks ----------------

@app.post("/tasks", response_model=TaskOut)
async def create_task(body: TaskCreate):
    tm: TaskManager = STATE["tm"]
    q: TaskQueue = STATE["queue"]
    tid = await tm.create_task(
        user_id=body.user_id, chat_id=body.chat_id, description=body.description,
        entry_agent_id=body.entry_agent_id, domain=body.domain,
        priority=body.priority, budget_usd=body.budget_usd,
        deadline_ts=body.deadline_ts)
    await q.enqueue(tid)
    await tm.set_status(tid, TaskStatus.QUEUED)
    return TaskOut(task_id=tid, user_id=body.user_id, status="QUEUED",
                   description=body.description)


@app.get("/tasks")
async def list_tasks(user_id: Optional[str] = None, status: Optional[str] = None,
                     limit: int = 20):
    tm: TaskManager = STATE["tm"]
    rows = await tm.list_tasks(user_id=user_id, status=status, limit=limit)
    return {"tasks": rows}


@app.get("/tasks/{task_id}")
async def get_task(task_id: str):
    tm: TaskManager = STATE["tm"]
    row = await tm.get_task(task_id)
    if not row:
        raise HTTPException(404, "not found")
    return row


@app.get("/tasks/{task_id}/events")
async def get_task_events(task_id: str, limit: int = 200):
    tm: TaskManager = STATE["tm"]
    return {"events": await tm.list_events(task_id, limit)}


@app.post("/tasks/{task_id}/pause")
async def pause_task(task_id: str):
    await STATE["tm"].set_status(task_id, TaskStatus.PAUSED)
    return {"status": "PAUSED"}


@app.post("/tasks/{task_id}/resume")
async def resume_task(task_id: str):
    await STATE["tm"].set_status(task_id, TaskStatus.QUEUED)
    await STATE["queue"].enqueue(task_id)
    return {"status": "QUEUED"}


@app.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: str):
    await STATE["tm"].set_status(task_id, TaskStatus.CANCELLED)
    return {"status": "CANCELLED"}


@app.post("/tasks/{task_id}/retry")
async def retry_task(task_id: str):
    tm: TaskManager = STATE["tm"]
    await tm.set_status(task_id, TaskStatus.QUEUED, error="")
    await tm.pool.execute(
        "UPDATE tasks SET retry_count = COALESCE(retry_count,0)+1 WHERE task_id=$1",
        task_id)
    await STATE["queue"].enqueue(task_id)
    return {"status": "QUEUED"}


# ---------------- scheduled jobs ----------------

@app.post("/schedule")
async def create_schedule(body: ScheduleCreate):
    tm: TaskManager = STATE["tm"]
    next_run = body.run_at
    if body.cron:
        next_run = _next_cron_ts(body.cron)
    jid = await tm.create_scheduled_job(
        user_id=body.user_id, chat_id=body.chat_id,
        description=body.description, cron=body.cron, run_at=body.run_at,
        entry_agent_id=body.entry_agent_id, next_run=next_run)
    return {"job_id": jid, "next_run": next_run}


@app.get("/schedule")
async def list_schedules(user_id: Optional[str] = None):
    return {"jobs": await STATE["tm"].list_scheduled_jobs(user_id=user_id)}


# ---------------- health / metrics ----------------

@app.get("/health")
async def health():
    return await system_health(STATE["tm"], STATE["router"], STATE["redis_url"])


@app.get("/metrics")
async def metrics():
    tm: TaskManager = STATE["tm"]
    all_tasks = await tm.list_tasks(limit=1000)
    by_status: Dict[str, int] = {}
    for t in all_tasks:
        by_status[t["status"]] = by_status.get(t["status"], 0) + 1
    cost = STATE["cost"].summary()
    return {"tasks_by_status": by_status,
            "providers": STATE["router"].available_providers(),
            "cost": cost}


# ---------------- telegram webhook (optional) ----------------

@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    """
    Optional webhook mode. Requires the bot to be started with the webhook
    configured to point here; polling mode does not need this endpoint.
    """
    try:
        from telegram_bot.bot_webhook import handle_update  # type: ignore
    except Exception:
        raise HTTPException(501, "telegram webhook handler not installed")
    update = await request.json()
    await handle_update(update)
    return {"ok": True}
'''

# =====================================================================
# migrations/001_init.sql (informational — TaskManager applies automatically)
# =====================================================================
FILES["migrations/001_init.sql"] = r'''-- Auto-applied by core.task_manager.TaskManager.connect().
-- This file exists for reference and manual migrations.
--
-- Tables: tasks, task_checkpoints, task_events, scheduled_jobs,
--         user_settings, audit_log, heartbeats
--
-- See core/task_manager.py :: SCHEMA for the authoritative DDL.
'''

# =====================================================================
# pyproject.toml  (PATCHED)
# =====================================================================
FILES["pyproject.toml"] = r'''[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "agentos"
version = "2.0.0"
description = "24/7 Telegram autonomous multi-agent platform"
requires-python = ">=3.10"
dependencies = [
  "pydantic>=2.6",
  "litellm>=1.40",
  "fastapi>=0.110",
  "uvicorn[standard]>=0.29",
  "httpx>=0.27",
  "chromadb>=0.4.22",
  "asyncpg>=0.29",
  "redis>=5.0",
  "aiogram>=3.13",
  "croniter>=2.0",
  "opentelemetry-sdk>=1.24",
  "opentelemetry-exporter-otlp>=1.24",
  "websockets>=12.0",
]

[project.optional-dependencies]
dev = ["pytest>=8.0", "pytest-asyncio>=0.23", "ruff>=0.4", "mypy>=1.10"]
docker = ["docker>=7.0"]

[tool.setuptools.packages.find]
include = ["core*", "api*", "demo*", "telegram_bot*"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
filterwarnings = ["ignore::DeprecationWarning"]
'''

# =====================================================================
# .env.example  (PATCHED)
# =====================================================================
FILES[".env.example"] = r'''# ==================================================================
# AgentOS 2.0 — environment template
# Copy to .env and fill in. NEVER commit .env to git.
# ==================================================================

# -------- Telegram --------
TELEGRAM_BOT_TOKEN=
TELEGRAM_ALLOWED_USER_IDS=            # comma-separated numeric ids

# -------- Model providers (only the ones you have keys for) --------
OPENAI_API_KEY=
DEEPSEEK_API_KEY=
GEMINI_API_KEY=
GROQ_API_KEY=
OPENROUTER_API_KEY=
XAI_API_KEY=

# -------- Model preferences --------
DEFAULT_MODEL=gpt-4o-mini
DEFAULT_REASONING_MODEL=deepseek-reasoner
DEFAULT_CODING_MODEL=gpt-4o

# -------- Datastores --------
DATABASE_URL=postgresql://agent:agentpass@postgres/agents
REDIS_URL=redis://redis:6379/0
DB_PASSWORD=agentpass

# -------- Task limits --------
MAX_TASK_COST=2.00
MAX_TASK_DURATION=172800                # seconds (48h)
STUCK_AFTER_SECONDS=600
WATCHDOG_POLL=30
SCHEDULER_POLL=15

# -------- Security --------
AUTO_APPROVE=false                      # MUST be false in production
SANDBOX_MODE=docker                     # docker | inproc (inproc = dev only)
LOG_LEVEL=INFO

# -------- Optional --------
CHROMA_DIR=/data/chroma
'''

# =====================================================================
# docker-compose.yml  (PATCHED — adds bot, scheduler, watchdog, healthchecks)
# =====================================================================
FILES["docker-compose.yml"] = r'''version: "3.9"

x-common-env: &common_env
  DATABASE_URL: postgresql://agent:${DB_PASSWORD:-agentpass}@postgres/agents
  REDIS_URL: redis://redis:6379/0
  OPENAI_API_KEY: ${OPENAI_API_KEY}
  DEEPSEEK_API_KEY: ${DEEPSEEK_API_KEY}
  GEMINI_API_KEY: ${GEMINI_API_KEY}
  GROQ_API_KEY: ${GROQ_API_KEY}
  OPENROUTER_API_KEY: ${OPENROUTER_API_KEY}
  XAI_API_KEY: ${XAI_API_KEY}
  DEFAULT_MODEL: ${DEFAULT_MODEL:-gpt-4o-mini}
  DEFAULT_REASONING_MODEL: ${DEFAULT_REASONING_MODEL:-deepseek-reasoner}
  DEFAULT_CODING_MODEL: ${DEFAULT_CODING_MODEL:-gpt-4o}
  AUTO_APPROVE: ${AUTO_APPROVE:-false}
  SANDBOX_MODE: ${SANDBOX_MODE:-docker}
  LOG_LEVEL: ${LOG_LEVEL:-INFO}

services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: agents
      POSTGRES_USER: agent
      POSTGRES_PASSWORD: ${DB_PASSWORD:-agentpass}
    volumes: [pgdata:/var/lib/postgresql/data]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U agent"]
      interval: 5s
      retries: 10
    restart: unless-stopped

  redis:
    image: redis:7-alpine
    command: ["redis-server", "--appendonly", "yes"]
    volumes: [redisdata:/data]
    healthcheck:
'''


from pathlib import Path as _Path
_target = _Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
_target.mkdir(parents=True, exist_ok=True)
for _rel, _content in FILES.items():
    _p = _target / _rel
    if not _p.exists():
        _p.parent.mkdir(parents=True, exist_ok=True)
        _p.write_text(_content, encoding="utf-8")
print(f"AgentOS generated: {len(FILES)} files -> {_target}")
