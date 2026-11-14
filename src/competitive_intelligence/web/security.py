"""Access-code check and a best-effort rate limiter for the audit launch endpoint."""

import hmac
import threading
import time
from collections import deque


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

    def __init__(self, per_minute: int, *, max_clients: int = 10000) -> None:
        self._per_minute = per_minute
        self._max_clients = max_clients
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, client: str) -> bool:
        now = time.monotonic()
        with self._lock:
            if client not in self._hits:
                if len(self._hits) >= self._max_clients:
                    self._hits = {key: hits for key, hits in self._hits.items() if hits and now - hits[-1] < 60}
                if len(self._hits) >= self._max_clients:
                    return False
                self._hits[client] = deque()
            hits = self._hits[client]
            while hits and now - hits[0] >= 60:
                hits.popleft()
            if len(hits) >= self._per_minute:
                return False
            hits.append(now)
            return True
