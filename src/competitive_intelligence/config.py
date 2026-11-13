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
    llm_api_key: str | None = field(default=None, repr=False)
    # A generic LLM_API_KEY alone does not identify a provider or authorize forwarding it.
    llm_provider: str | None = None
    llm_model: str | None = None
    llm_max_requests: int = 32
    llm_max_input_chars: int = 240000
    llm_max_output_tokens: int = 2000
    llm_max_claims: int = 20
    llm_timeout_seconds: float = 30
    reference_bundle_path: str | None = None
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
            llm_api_key=env.get("LLM_API_KEY") or None,
            llm_provider=env.get("LLM_PROVIDER") or None,
            llm_model=env.get("LLM_MODEL") or None,
            llm_max_requests=int(env.get("LLM_MAX_REQUESTS") or "32"),
            llm_max_input_chars=int(env.get("LLM_MAX_INPUT_CHARS") or "240000"),
            llm_max_output_tokens=int(env.get("LLM_MAX_OUTPUT_TOKENS") or "2000"),
            llm_max_claims=int(env.get("LLM_MAX_CLAIMS") or "20"),
            llm_timeout_seconds=float(env.get("LLM_TIMEOUT_SECONDS") or "30"),
            reference_bundle_path=env.get("REFERENCE_BUNDLE_PATH") or None,
        )

    def __post_init__(self) -> None:
        if self.worker_lease_seconds < 3 or self.worker_max_attempts < 1 or self.worker_retry_seconds < 0:
            raise ValueError("Invalid worker lease, attempts or retry settings")
        if self.source_capture_success_status != "ok":
            raise ValueError("SOURCE_CAPTURE_SUCCESS_STATUS must be 'ok'")
        if self.llm_provider not in (None, "openai", "gemini"):
            raise ValueError("Unsupported LLM_PROVIDER; supported adapters are gemini and openai")
        if self.llm_provider and (not self.llm_api_key or not self.llm_model or len(self.llm_model) > 100):
            raise ValueError("Configured LLM_PROVIDER requires LLM_API_KEY and LLM_MODEL")
        if not (1 <= self.llm_max_requests <= 64 and 1000 <= self.llm_max_input_chars <= 1000000
                and 256 <= self.llm_max_output_tokens <= 8000 and 1 <= self.llm_max_claims <= 50
                and 1 <= self.llm_timeout_seconds <= 60):
            raise ValueError("Invalid LLM limits")

    def with_overrides(self, overrides: dict[str, Any] | None) -> "Settings":
        return replace(self, **overrides) if overrides else self
