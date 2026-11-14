"""Shared audit admission limits; independent of client IP and worker lifetime."""

from dataclasses import dataclass


@dataclass(frozen=True)
class AdmissionLimits:
    per_minute: int
    per_day: int
    max_pending: int


class AdmissionRejected(RuntimeError):
    """A quota or queue bound was reached; no audit was inserted."""

    def __init__(self, retry_after: int = 60):
        super().__init__("Audit admission limit reached")
        self.retry_after = retry_after
