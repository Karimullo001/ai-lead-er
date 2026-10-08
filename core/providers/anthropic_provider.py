from __future__ import annotations
import os
from typing import List
from ._litellm_base import LiteLLMProvider


class AnthropicProvider(LiteLLMProvider):
    name = "anthropic"
    litellm_prefix = "anthropic/"

    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        super().__init__(api_key or os.getenv("ANTHROPIC_API_KEY"), base_url)
        self.models_list = [
            "claude-3-5-sonnet-20241022",
            "claude-3-5-haiku-20241022",
            "claude-3-7-sonnet-20250219",
            "claude-3-opus-20240229",
        ]

    pricing = {
        "claude-3-5-sonnet-20241022": (0.003, 0.015),
        "claude-3-5-haiku-20241022": (0.0008, 0.004),
        "claude-3-7-sonnet-20250219": (0.003, 0.015),
        "claude-3-opus-20240229": (0.015, 0.075),
    }
