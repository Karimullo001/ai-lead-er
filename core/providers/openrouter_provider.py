from __future__ import annotations
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
