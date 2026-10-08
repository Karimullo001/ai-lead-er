from __future__ import annotations
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
