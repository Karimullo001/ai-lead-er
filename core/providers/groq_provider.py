from __future__ import annotations
import os
from ._litellm_base import LiteLLMProvider


class GroqProvider(LiteLLMProvider):
    name = "groq"
    litellm_prefix = "groq/"
    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        super().__init__(api_key or os.getenv("GROQ_API_KEY"), base_url)
        self.models_list = ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"]
    pricing = {
        "llama-3.3-70b-versatile": (0.00059, 0.00079),
        "llama-3.1-8b-instant": (0.00005, 0.00008),
    }
