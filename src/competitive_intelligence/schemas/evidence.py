"""Evidence contract: facts returned by read-only tools and the code-computed rule result."""

from datetime import date
from decimal import Decimal

from pydantic import AwareDatetime, Field, field_validator

from .claim import SHA256_PATTERN, require_https
from .common import StrictModel, VerdictValue


class EvidenceFact(StrictModel):
    evidence_id: str
    statement: str
    value: Decimal | None = None
    unit: str | None = None
    source_url: str
    source_ref: str
    captured_at: AwareDatetime
    valid_as_of: date
    source_content_hash: str = Field(pattern=SHA256_PATTERN)
    scope: str
    limitations: list[str] = Field(default_factory=list)

    _https = field_validator("source_url")(require_https)


class Evidence(StrictModel):
    claim_id: str
    facts: list[EvidenceFact] = Field(default_factory=list)
    rule_result: VerdictValue
    rule_version: str
    limitations: list[str] = Field(default_factory=list)
