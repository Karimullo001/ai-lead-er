from __future__ import annotations
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
