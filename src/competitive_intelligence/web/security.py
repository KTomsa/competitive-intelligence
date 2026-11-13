"""Access-code check and a best-effort rate limiter for the audit launch endpoint."""

import hmac
import threading
import time
from collections import defaultdict, deque


def access_code_matches(provided: str | None, expected: str) -> bool:
    """Constant-time comparison; the code itself is never logged or echoed."""
    if not provided:
        return False
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


class RateLimiter:
    """Sliding one-minute window per client.

    Process-local: on serverless hosting each instance counts separately, so
    this slows down guessing but is not a spend cap. A durable limiter belongs
    with the database-backed repository.
    """

    def __init__(self, per_minute: int) -> None:
        self._per_minute = per_minute
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, client: str) -> bool:
        now = time.monotonic()
        with self._lock:
            hits = self._hits[client]
            while hits and now - hits[0] > 60:
                hits.popleft()
            if len(hits) >= self._per_minute:
                return False
            hits.append(now)
            return True
