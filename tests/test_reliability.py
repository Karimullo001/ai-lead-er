import pytest
from core.reliability import ReliabilityEngine


def test_match_rate_limit():
    r = ReliabilityEngine()
    p = r.match_failure(["HTTP 429 Too Many Requests"])
    assert p is not None and p.name == "rate_limit"


@pytest.mark.asyncio
async def test_verification_default_passes():
    r = ReliabilityEngine()
    v = await r.verify("default", {"success": True, "output": "hello"}, {})
    assert v.passed


@pytest.mark.asyncio
async def test_verification_empty_fails():
    r = ReliabilityEngine()
    v = await r.verify("default", {"success": True, "output": "  "}, {})
    assert not v.passed


@pytest.mark.asyncio
async def test_retry_recovers_from_transient():
    r = ReliabilityEngine()
    attempts = {"n": 0}

    async def op():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RuntimeError("timeout")
        return "ok"

    res = await r.execute_with_recovery(
        op, {"max_retries": 5, "backoff_base_ms": 10}, {"step": "s"})
    assert res == "ok" and attempts["n"] == 3
