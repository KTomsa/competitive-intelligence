"""Shared enums and the strict base model used by all contracts."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    """Contract model: unknown fields are rejected instead of silently kept."""

    model_config = ConfigDict(extra="forbid")


class VerdictValue(StrEnum):
    SUPPORTED = "supported"
    PARTIALLY_SUPPORTED = "partially_supported"
    CONTRADICTED = "contradicted"
    UNVERIFIABLE = "unverifiable"


class Origin(StrEnum):
    LIVE = "live"
    BASELINE = "baseline"
    SNAPSHOT = "snapshot"


class ClaimType(StrEnum):
    PRICE = "price"
    GREEN = "green"
    CUSTOMER = "customer"
    OTHER = "other"


class AuditStatus(StrEnum):
    QUEUED = "queued"
    COLLECTING = "collecting"
    EXTRACTING = "extracting"
    VERIFYING = "verifying"
    JUDGING = "judging"
    REPORTING = "reporting"
    COMPLETED = "completed"
    FAILED = "failed"


class AuditMode(StrEnum):
    FIXTURE = "fixture"
    LIVE = "live"
