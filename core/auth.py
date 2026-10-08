from __future__ import annotations
import logging, os, time
from typing import Iterable, Optional, Set
from collections import defaultdict, deque

log = logging.getLogger("agentos.auth")


class RateLimiter:
    """Token-bucket-ish sliding window per key."""
    def __init__(self, max_calls: int = 20, window_seconds: float = 60.0):
        self.max_calls = max_calls
        self.window = window_seconds
        self._hits: defaultdict = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.time()
        dq = self._hits[key]
        while dq and now - dq[0] > self.window:
            dq.popleft()
        if len(dq) >= self.max_calls:
            return False
        dq.append(now)
        return True


class Authorizer:
    """
    Enforces the Telegram allowlist and per-user rate limit.
    Reads TELEGRAM_ALLOWED_USER_IDS as a comma-separated list of ints.
    """
    def __init__(self, allowed_user_ids: Optional[Iterable[int]] = None,
                 rate_limit_per_min: int = 60):
        if allowed_user_ids is None:
            raw = os.getenv("TELEGRAM_ALLOWED_USER_IDS", "").strip()
            if not raw or raw in ("*", "all"):
                self.allow_all = True
                self.allowed: Set[int] = set()
                log.info("Authorizer: open mode (TELEGRAM_ALLOWED_USER_IDS is not set)")
            else:
                self.allow_all = False
                self.allowed = set(int(x) for x in raw.split(",") if x.strip().isdigit())
                log.info("Authorizer: whitelist mode with %d allowed users", len(self.allowed))
        else:
            self.allow_all = False
            self.allowed = set(int(x) for x in allowed_user_ids)
        self.rl = RateLimiter(max_calls=rate_limit_per_min, window_seconds=60.0)

    def is_allowed(self, user_id: int) -> bool:
        if getattr(self, "allow_all", False):
            return True
        return user_id in self.allowed

    def check(self, user_id: int) -> tuple[bool, str]:
        if not self.is_allowed(user_id):
            return False, "unauthorized"
        if not self.rl.allow(str(user_id)):
            return False, "rate_limited"
        return True, "ok"
