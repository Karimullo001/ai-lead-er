from __future__ import annotations
import re
from typing import Any, Dict, List, Optional
from pydantic import BaseModel


class GuardrailResult(BaseModel):
    passed: bool
    reason: str = ""
    redacted_text: Optional[str] = None


class Guardrail:
    name: str = "guardrail"

    async def check(self, text: str,
                    context: Optional[Dict[str, Any]] = None) -> GuardrailResult:
        return GuardrailResult(passed=True)


class PIIGuardrail(Guardrail):
    name = "pii"
    PATTERNS = {
        "email": re.compile(r"[\w\.-]+@[\w\.-]+\.\w+"),
        "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
        "credit_card": re.compile(r"\b(?:\d[ -]*?){13,16}\b"),
        "phone": re.compile(r"\b\+?\d[\d\s\-\(\)]{7,}\d\b"),
    }

    async def check(self, text, context=None):
        redacted = text
        found: List[str] = []
        for label, pat in self.PATTERNS.items():
            if pat.search(redacted):
                found.append(label)
                redacted = pat.sub(f"[REDACTED_{label.upper()}]", redacted)
        if found:
            return GuardrailResult(passed=False,
                                   reason=f"PII detected: {', '.join(found)}",
                                   redacted_text=redacted)
        return GuardrailResult(passed=True)


class PromptInjectionGuardrail(Guardrail):
    name = "prompt_injection"
    PATTERNS = [
        re.compile(r"ignore (all|previous) instructions", re.I),
        re.compile(r"disregard (all|previous) (instructions|prompts)", re.I),
        re.compile(r"you are now (a|an) ", re.I),
        re.compile(r"reveal your (system prompt|instructions)", re.I),
        re.compile(r"jailbreak", re.I),
    ]

    async def check(self, text, context=None):
        for pat in self.PATTERNS:
            if pat.search(text):
                return GuardrailResult(passed=False,
                                       reason=f"prompt injection signal: {pat.pattern}")
        return GuardrailResult(passed=True)


class LengthGuardrail(Guardrail):
    name = "length"

    def __init__(self, max_chars: int = 40000):
        self.max_chars = max_chars

    async def check(self, text, context=None):
        if len(text) > self.max_chars:
            return GuardrailResult(passed=False,
                                   reason=f"input exceeds {self.max_chars} chars")
        return GuardrailResult(passed=True)


class GuardrailChain:
    def __init__(self, guardrails: Optional[List[Guardrail]] = None):
        self.guardrails = guardrails or [
            LengthGuardrail(), PromptInjectionGuardrail(), PIIGuardrail()]

    async def check(self, text: str, context: Optional[Dict[str, Any]] = None) -> GuardrailResult:
        cur = text
        for g in self.guardrails:
            res = await g.check(cur, context)
            if not res.passed:
                return res
            if res.redacted_text:
                cur = res.redacted_text
        return GuardrailResult(passed=True, redacted_text=cur)
