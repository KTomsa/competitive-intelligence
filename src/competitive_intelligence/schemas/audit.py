"""Audit request, status and result contracts (docs/API_CONTRACTS.md)."""

from typing import Literal

from pydantic import AwareDatetime, Field, field_validator, model_validator

from .claim import Claim, Source
from .common import AuditMode, AuditStatus, ClaimType, Origin, StrictModel, VerdictValue
from .evidence import Evidence
from .verdict import Verdict


class AuditCreateRequest(StrictModel):
    supplier_ids: list[str] = Field(min_length=1, max_length=5)
    profile_ids: list[str] = Field(min_length=1, max_length=4)
    commodity: Literal["electricity"] = "electricity"
    mode: AuditMode = AuditMode.FIXTURE

    @field_validator("supplier_ids", "profile_ids")
    @classmethod
    def unique_ids(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)) or any(not value or len(value) > 100 for value in values):
            raise ValueError("Identifiers must be nonempty, unique and at most 100 characters")
        return values


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

    @model_validator(mode="after")
    def consistent_references(self) -> "AuditResult":
        def unique(items, key):
            values = [getattr(item, key) for item in items]
            if len(values) != len(set(values)):
                raise ValueError("Result contains duplicate identifiers")
            return set(values)

        suppliers = unique(self.suppliers, "supplier_id")
        unique(self.sources, "source_id")
        claim_ids = unique(self.claims, "id")
        if unique(self.evidence, "claim_id") != claim_ids or unique(self.verdicts, "claim_id") != claim_ids:
            raise ValueError("Each claim requires exactly one evidence record and verdict")
        sources = {source.source_id: source for source in self.sources}
        claims = {claim.id: claim for claim in self.claims}
        evidence = {item.claim_id: item for item in self.evidence}
        verdicts = {item.claim_id: item for item in self.verdicts}
        for source in self.sources:
            if source.supplier_id not in suppliers:
                raise ValueError("Source belongs to an unselected supplier")
        for claim in self.claims:
            source = sources.get(claim.source_id)
            if (source is None or claim.supplier_id != source.supplier_id
                    or claim.source_url != source.source_url or claim.origin != source.origin
                    or claim.captured_at != source.captured_at):
                raise ValueError("Claim provenance differs from its source")
            item, verdict = evidence[claim.id], verdicts[claim.id]
            facts = unique(item.facts, "evidence_id")
            if item.rule_result in (VerdictValue.SUPPORTED, VerdictValue.CONTRADICTED) and not facts:
                raise ValueError("A determinate rule outcome requires traceable evidence")
            if item.rule_result != verdict.rule_verdict or not set(verdict.evidence_ids) <= facts:
                raise ValueError("Verdict references incompatible evidence")
        for item in self.briefing.items:
            claim = claims.get(item.claim_id)
            if claim is None or item.supplier_id != claim.supplier_id or item.verdict != verdicts[claim.id].verdict:
                raise ValueError("Briefing cites an incompatible claim or verdict")
        expected: dict[ClaimType, dict[VerdictValue, int]] = {}
        for verdict in self.verdicts:
            row = expected.setdefault(claims[verdict.claim_id].claim_type, {})
            row[verdict.verdict] = row.get(verdict.verdict, 0) + 1
        if self.briefing.matrix != expected:
            raise ValueError("Briefing matrix differs from saved verdicts")
        return self
