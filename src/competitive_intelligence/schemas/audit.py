"""Audit request, status and result contracts (docs/API_CONTRACTS.md)."""

from typing import Literal

from pydantic import AwareDatetime, Field

from .claim import Claim, Source
from .common import AuditMode, AuditStatus, ClaimType, Origin, StrictModel, VerdictValue
from .evidence import Evidence
from .verdict import Verdict


class AuditCreateRequest(StrictModel):
    supplier_ids: list[str] = Field(min_length=1, max_length=5)
    profile_ids: list[str] = Field(min_length=1, max_length=4)
    commodity: Literal["electricity"] = "electricity"
    mode: AuditMode = AuditMode.FIXTURE


class AuditCreated(StrictModel):
    audit_id: str
    status: AuditStatus
    status_url: str
    results_url: str


class AuditEvent(StrictModel):
    event_id: int
    timestamp: AwareDatetime
    stage: str
    level: Literal["info", "warning", "error"] = "info"
    message: str
    origin: Origin | None = None


class AuditError(StrictModel):
    code: str
    message: str


class AuditStatusView(StrictModel):
    audit_id: str
    status: AuditStatus
    stage: str
    progress_percent: int = Field(ge=0, le=100)
    updated_at: AwareDatetime
    events: list[AuditEvent] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error: AuditError | None = None


class SupplierInfo(StrictModel):
    supplier_id: str
    name: str


class BriefingItem(StrictModel):
    claim_id: str
    supplier_id: str
    verdict: VerdictValue
    text: str


class Briefing(StrictModel):
    summary: str
    items: list[BriefingItem] = Field(default_factory=list)
    # Counts of verdicts per claim type; never a per-supplier score.
    matrix: dict[ClaimType, dict[VerdictValue, int]] = Field(default_factory=dict)


class AuditResult(StrictModel):
    audit_id: str
    mode: AuditMode
    scope_note: str
    suppliers: list[SupplierInfo]
    profile_ids: list[str]
    sources: list[Source]
    claims: list[Claim]
    evidence: list[Evidence]
    verdicts: list[Verdict]
    briefing: Briefing
    warnings: list[str] = Field(default_factory=list)
    completed_at: AwareDatetime
    published_at: AwareDatetime | None = None
