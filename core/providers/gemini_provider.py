from __future__ import annotations
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
