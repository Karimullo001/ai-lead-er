from __future__ import annotations
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
        call_kwargs.setdefault("timeout", 60)
        call_kwargs.update(kwargs)
        try:
            resp = await litellm.acompletion(**call_kwargs)
        except Exception as e:
            raise ProviderError(self.name, classify_exception(e), str(e)) from e

        msg = resp.choices[0].message
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
        pt = int(getattr(usage, "prompt_tokens", 0) or 0)
        ct = int(getattr(usage, "completion_tokens", 0) or 0)
        latency = (time.time() - t0) * 1000.0
        return Completion(
            text=text, provider=self.name, model=model,
            prompt_tokens=pt, completion_tokens=ct,
            latency_ms=latency,
            cost_usd=self.estimate_cost(model, pt, ct),
            raw={"finish_reason": getattr(resp.choices[0], "finish_reason", None), "tool_calls": native_tool_calls},
        )

    async def stream_complete(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        temperature: float = 0.2,
        max_tokens: int = 2048,
        **kwargs: Any,
    ):
        import litellm
        litellm.drop_params = True
        full = f"{self.litellm_prefix}{model}" if self.litellm_prefix else model
        call_kwargs: Dict[str, Any] = dict(
            model=full, messages=messages,
            temperature=temperature, max_tokens=max_tokens,
            stream=True,
        )
        if self.api_key:
            call_kwargs["api_key"] = self.api_key
        if self.base_url:
            call_kwargs["base_url"] = self.base_url
        call_kwargs.setdefault("timeout", 60)
        call_kwargs.update(kwargs)
        try:
            resp = await litellm.acompletion(**call_kwargs)
            async for chunk in resp:
                if chunk.choices and len(chunk.choices) > 0:
                    delta = chunk.choices[0].delta
                    content = getattr(delta, "content", None) or ""
                    if content:
                        yield content
        except Exception as e:
            raise ProviderError(self.name, classify_exception(e), str(e)) from e
