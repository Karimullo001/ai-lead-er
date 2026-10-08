from __future__ import annotations
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
        raw = comp.raw or {}
        return {
            "content": comp.text,
            "tool_calls": raw.get("tool_calls") or [],
            "provider": comp.provider,
            "model": comp.model,
        }


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
