"""Per-IP rate limiting for AI-consuming endpoints.

Enforced in-process (single worker) with a per-IP sliding window keyed by the
client address seen through Caddy's `X-Forwarded-For`. Only the endpoints that
make LLM calls are limited; deterministic graph reads are not, so they can't be
used to burn model budget.

Windows are in memory and reset on restart — acceptable for a single-node demo
server and avoids adding a Redis dependency. A failed (blocked) request is loud:
HTTP 429 with a `Retry-After` header and a JSON error, never a silent success.

Usage:
    limiter = RateLimiter({"ask": (20, 3600), "ingest": (5, 3600)})
    ok, retry_after = limiter.hit(key, "ask")
    if not ok:
        return 429, {"error": f"rate limit exceeded, retry in {retry_after}s"}
"""

from __future__ import annotations

import threading
import time
from typing import Dict, Tuple


class _Counter:
    """Fixed-window counter with per-window resets (cheap and atomic enough)."""

    def __init__(self) -> None:
        self.window_start = time.monotonic()
        self.count = 0


class RateLimiter:
    """Per-key counter for named limits, with a sliding fixed window."""

    def __init__(self, limits: Dict[str, Tuple[int, int]]) -> None:
        """limits: {name: (max_count, window_seconds)} per unique key."""
        self._limits = limits
        self._data: Dict[Tuple[str, str], _Counter] = {}
        self._lock = threading.Lock()

    def hit(self, key: str, name: str) -> Tuple[bool, int]:
        """Record an attempt for `key` under limit `name`.

        Returns (allowed, retry_after_seconds). `retry_after` is 0 when allowed.
        """
        limit_cfg = self._limits.get(name)
        if limit_cfg is None:
            return True, 0
        max_count, window = limit_cfg
        bucket = (key, name)
        now = time.monotonic()
        with self._lock:
            counter = self._data.get(bucket)
            if counter is None or now - counter.window_start >= window:
                counter = _Counter()
                counter.window_start = now
                counter.count = 0
                self._data[bucket] = counter
            if counter.count >= max_count:
                retry_after = max(1, int(window - (now - counter.window_start)))
                return False, retry_after
            counter.count += 1
            return True, 0

    def client_key(self, forwarded_for: str, remote_addr: str) -> str:
        """Pick the real client IP from X-Forwarded-For, else the socket peer."""
        if forwarded_for:
            first = forwarded_for.split(",")[0].strip()
            if first:
                return first
        return remote_addr or "unknown"


# Endpoint -> (max requests per key, window seconds).
# Tuned for a public demo: /ask burns one DeepSeek classification, /ingest burns
# several. Deterministic reads are unlimited.
LIMITS = {
    "ask": (20, 3600),
    "ingest": (5, 3600),
    "suggestions": (10, 3600),
}

limiter = RateLimiter(LIMITS)
