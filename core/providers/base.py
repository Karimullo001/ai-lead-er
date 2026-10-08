from __future__ import annotations
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
