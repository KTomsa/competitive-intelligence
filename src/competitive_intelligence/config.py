"""Runtime settings, read from the environment. Secrets never have defaults."""

import os
from dataclasses import dataclass, replace
from typing import Any


@dataclass(frozen=True)
class Settings:
    audit_access_code: str | None = None
    rate_limit_per_minute: int = 10
    # Run one fictional fixture audit at startup and publish it, so the public
    # page has something to show before any database is connected.
    seed_demo_audit: bool = True

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            audit_access_code=os.environ.get("AUDIT_ACCESS_CODE") or None,
            rate_limit_per_minute=int(os.environ.get("AUDIT_RATE_LIMIT_PER_MINUTE", "10")),
            seed_demo_audit=os.environ.get("SEED_DEMO_AUDIT", "1") != "0",
        )

    def with_overrides(self, overrides: dict[str, Any] | None) -> "Settings":
        return replace(self, **overrides) if overrides else self
