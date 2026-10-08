from __future__ import annotations
import asyncio, logging, random
from typing import Any, Awaitable, Callable, Dict, List, Optional
from pydantic import BaseModel
from .models import Verification

log = logging.getLogger("agentos.reliability")


class FailurePattern(BaseModel):
    name: str
    symptoms: List[str]
    retry_strategy: str = "exponential_jitter"
    max_retries: int = 3
    fallback: Optional[str] = None


class ReliabilityEngine:
    FAILURE_PATTERNS: List[FailurePattern] = [
        FailurePattern(name="rate_limit",
                       symptoms=["429", "rate limit", "too many requests"],
                       retry_strategy="exponential_jitter", max_retries=6),
        FailurePattern(name="timeout",
                       symptoms=["timeout", "timed out", "deadline exceeded"],
                       retry_strategy="exponential_jitter", max_retries=3),
        FailurePattern(name="silent_none",
                       symptoms=["none", "empty response", "missing output"],
                       retry_strategy="immediate", max_retries=2, fallback="return_error"),
        FailurePattern(name="tool_not_found",
                       symptoms=["toolnotfound", "unknown tool"],
                       retry_strategy="re_plan", max_retries=1),
        FailurePattern(name="verification_failed",
                       symptoms=["verification failed", "check failed", "checklist"],
                       retry_strategy="re_plan", max_retries=2),
        FailurePattern(name="context_overflow",
                       symptoms=["token", "context window", "maximum context"],
                       retry_strategy="summarize_and_retry", max_retries=1),
        FailurePattern(name="syntax_error",
                       symptoms=["syntaxerror", "syntax error", "indentationerror"],
                       retry_strategy="re_plan", max_retries=2),
    ]

    def __init__(self):
        self._checklists: Dict[str, List[Callable[[Dict[str, Any], Dict[str, Any]],
                                                  Awaitable[Dict[str, Any]]]]] = {}
        self._register_defaults()

    async def execute_with_recovery(self, operation, policy, context):
        max_retries = policy.get("max_retries", 3)
        base_ms = policy.get("backoff_base_ms", 500)
        last_err: Optional[Exception] = None
        for attempt in range(max_retries + 1):
            try:
                return await operation()
            except Exception as e:
                last_err = e
                pattern = self.match_failure([str(e)])
                if pattern and pattern.retry_strategy == "re_plan":
                    raise
                if attempt >= max_retries:
                    break
                delay_ms = self._compute_delay(pattern, attempt, base_ms)
                log.warning("Retry %d/%d for %s after %dms: %s",
                            attempt + 1, max_retries, context.get("step"), delay_ms, e)
                await asyncio.sleep(delay_ms / 1000.0)
        assert last_err is not None
        raise last_err

    def match_failure(self, symptoms: List[str]) -> Optional[FailurePattern]:
        text = " ".join(symptoms).lower()
        for p in self.FAILURE_PATTERNS:
            if any(s in text for s in p.symptoms):
                return p
        return None

    def _compute_delay(self, pattern, attempt, base_ms):
        if pattern and pattern.retry_strategy == "exponential_jitter":
            return base_ms * (2 ** attempt) + random.uniform(0, base_ms)
        return base_ms

    def register_checklist(self, name, checks):
        self._checklists[name] = checks

    async def verify(self, checklist, evidence, context) -> Verification:
        checks = self._checklists.get(checklist, self._checklists.get("default", []))
        failures: List[str] = []
        for check in checks:
            try:
                res = await check(evidence, context)
                if not res.get("passed", False):
                    failures.append(res.get("reason", "unnamed check failed"))
            except Exception as e:
                failures.append(f"checker error: {e}")
        return Verification(passed=len(failures) == 0,
                            reason="; ".join(failures) if failures else "All checks passed",
                            failure_signals=failures, evidence=evidence)

    def _register_defaults(self) -> None:
        async def not_empty(evidence, _ctx):
            out = evidence.get("output")
            ok = out is not None and (not isinstance(out, str) or out.strip() != "")
            return {"passed": ok, "reason": "empty output" if not ok else ""}

        async def success_flag(evidence, _ctx):
            return {"passed": bool(evidence.get("success", True)),
                    "reason": "step marked failed"}

        async def code_check(evidence, _ctx):
            s = str(evidence.get("output", "")).lower()
            bad = ("traceback" in s) or ("syntaxerror" in s) or \
                  ("exception" in s and "handled" not in s)
            return {"passed": not bad, "reason": "code raised an error" if bad else ""}

        async def research_check(evidence, _ctx):
            s = str(evidence.get("output", "")).lower()
            ok = any(k in s for k in ("source", "http", "cite"))
            return {"passed": ok, "reason": "no sources present" if not ok else ""}

        async def deployment_check(evidence, _ctx):
            s = str(evidence.get("output", "")).lower()
            ok = ("200" in s) or ("ok" in s) or ("healthy" in s)
            return {"passed": ok, "reason": "no healthy signal" if not ok else ""}

        self._checklists["default"] = [not_empty, success_flag]
        self._checklists["code"] = [not_empty, success_flag, code_check]
        self._checklists["research"] = [not_empty, success_flag, research_check]
        self._checklists["deployment"] = [not_empty, success_flag, deployment_check]
