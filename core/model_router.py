from __future__ import annotations
import asyncio, logging, os, time
from typing import Any, Dict, List, Optional
from .providers import (Completion, ModelProvider, OpenAIProvider, AnthropicProvider,
                        DeepSeekProvider, GeminiProvider, GroqProvider,
                        OpenRouterProvider, XAIProvider)
from .providers.base import ProviderError

log = logging.getLogger("agentos.router")

# ---- Task type → ranked provider/model preference --------------------------
# Each entry lists (provider_name, model_hint). The router tries them in order,
# skipping providers that are unhealthy / cooled down.

TASK_PREFERENCES: Dict[str, List[tuple]] = {
    "coding":     [("anthropic", "claude-3-5-sonnet-20241022"), ("openai", "gpt-4o"),
                   ("openrouter", "anthropic/claude-3.5-sonnet"),
                   ("deepseek", "deepseek-chat"), ("groq", "llama-3.3-70b-versatile")],
    "reasoning":  [("anthropic", "claude-3-7-sonnet-20250219"), ("anthropic", "claude-3-5-sonnet-20241022"),
                   ("deepseek", "deepseek-reasoner"), ("openai", "gpt-4o"),
                   ("gemini", "gemini-1.5-pro"), ("xai", "grok-2-latest")],
    "research":   [("anthropic", "claude-3-5-sonnet-20241022"), ("openai", "gpt-4o-mini"),
                   ("gemini", "gemini-1.5-pro"), ("deepseek", "deepseek-chat"),
                   ("groq", "llama-3.3-70b-versatile")],
    "fast":       [("anthropic", "claude-3-5-haiku-20241022"), ("groq", "llama-3.1-8b-instant"),
                   ("gemini", "gemini-1.5-flash"), ("openai", "gpt-4o-mini"), ("deepseek", "deepseek-chat")],
    "large":      [("anthropic", "claude-3-5-sonnet-20241022"), ("gemini", "gemini-1.5-pro"),
                   ("openai", "gpt-4o"), ("openrouter", "anthropic/claude-3.5-sonnet")],
    "vision":     [("anthropic", "claude-3-5-sonnet-20241022"), ("openai", "gpt-4o"),
                   ("gemini", "gemini-1.5-pro")],
    "verify":     [("anthropic", "claude-3-5-sonnet-20241022"), ("deepseek", "deepseek-reasoner"),
                   ("xai", "grok-2-latest"), ("openai", "gpt-4o")],
    "general":    [("anthropic", "claude-3-5-sonnet-20241022"), ("openai", "gpt-4o"),
                   ("openai", "gpt-4o-mini"), ("groq", "llama-3.3-70b-versatile"),
                   ("deepseek", "deepseek-chat"), ("gemini", "gemini-1.5-flash"),
                   ("openrouter", "meta-llama/llama-3.3-70b-instruct"),
                   ("xai", "grok-2-latest")],
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
                AnthropicProvider(), OpenAIProvider(), DeepSeekProvider(),
                GeminiProvider(), GroqProvider(), OpenRouterProvider(), XAIProvider(),
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

    async def stream(
        self,
        messages: List[Dict[str, Any]],
        task_type: str = "general",
        model_hint: Optional[str] = None,
        temperature: float = 0.4,
        max_tokens: int = 2048,
        **kwargs: Any,
    ):
        """Stream response chunks with automatic fallback across providers."""
        candidates = self._candidates(task_type, model_hint)
        last_error: Optional[Exception] = None
        for pname, model in candidates:
            provider = self.providers.get(pname)
            if provider is None:
                continue
            if self.breaker.is_open(pname):
                continue
            try:
                if hasattr(provider, "stream_complete"):
                    stream_iter = provider.stream_complete(
                        model=model, messages=messages,
                        temperature=temperature, max_tokens=max_tokens,
                        **kwargs,
                    )
                    has_tokens = False
                    async for chunk in stream_iter:
                        has_tokens = True
                        yield chunk
                    if has_tokens:
                        await self.breaker.record_success(pname)
                        return
                else:
                    comp = await provider.complete(
                        model=model, messages=messages,
                        temperature=temperature, max_tokens=max_tokens,
                        **kwargs,
                    )
                    await self.breaker.record_success(pname)
                    yield comp.text
                    return
            except Exception as e:
                last_error = e
                log.warning("router stream error on %s/%s: %s", pname, model, e)
                await self.breaker.record_failure(pname)
                continue
        if last_error is not None:
            raise RuntimeError(f"ModelRouter stream: all providers failed. Last: {last_error}")
        raise RuntimeError("ModelRouter: no available providers configured for streaming")

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
