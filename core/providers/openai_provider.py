from __future__ import annotations
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
