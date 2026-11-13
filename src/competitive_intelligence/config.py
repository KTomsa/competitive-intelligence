"""Runtime settings, read from the environment. Secrets never have defaults."""

import os
from dataclasses import dataclass, field, replace
from typing import Any

from dotenv import dotenv_values


@dataclass(frozen=True)
class Settings:
    audit_access_code: str | None = field(default=None, repr=False)
    database_url: str | None = field(default=None, repr=False)
    rate_limit_per_minute: int = 10
    worker_lease_seconds: int = 60
    worker_max_attempts: int = 3
    worker_retry_seconds: int = 10
    # Confirmed CK_captures_fetch_status: ok, blocked, error.
    source_capture_success_status: str = "ok"
    # Run one fictional fixture audit at startup and publish it, so the public
    # page has something to show before any database is connected.
    seed_demo_audit: bool = True

    @classmethod
    def from_env(cls) -> "Settings":
        # Read local development configuration without overriding the host environment
        # or mutating os.environ. Importing the module does not load secrets.
        env = {**dotenv_values(".env"), **os.environ}
        return cls(
            audit_access_code=env.get("AUDIT_ACCESS_CODE") or None,
            database_url=env.get("DATABASE_URL") or None,
            rate_limit_per_minute=int(env.get("AUDIT_RATE_LIMIT_PER_MINUTE") or "10"),
            seed_demo_audit=env.get("SEED_DEMO_AUDIT", "1") != "0",
            worker_lease_seconds=int(env.get("WORKER_LEASE_SECONDS") or "60"),
            worker_max_attempts=int(env.get("WORKER_MAX_ATTEMPTS") or "3"),
            worker_retry_seconds=int(env.get("WORKER_RETRY_SECONDS") or "10"),
            source_capture_success_status=env.get("SOURCE_CAPTURE_SUCCESS_STATUS") or "ok",
        )

    def __post_init__(self) -> None:
        if self.worker_lease_seconds < 3 or self.worker_max_attempts < 1 or self.worker_retry_seconds < 0:
            raise ValueError("Invalid worker lease, attempts or retry settings")
        if self.source_capture_success_status != "ok":
            raise ValueError("SOURCE_CAPTURE_SUCCESS_STATUS must be 'ok'")

    def with_overrides(self, overrides: dict[str, Any] | None) -> "Settings":
        return replace(self, **overrides) if overrides else self
