"""Pydantic contracts passed between pipeline stages and returned by the API."""

from .audit import (
    AuditCreated,
    AuditCreateRequest,
    AuditError,
    AuditEvent,
    AuditResult,
    AuditStatusView,
    Briefing,
    BriefingItem,
    SupplierInfo,
)
from .claim import RULE_IDS, Claim, Rule, Source, coerce_rule, normalize_whitespace, quote_in_text
from .common import AuditMode, AuditStatus, ClaimType, Origin, StrictModel, VerdictValue
from .evidence import Evidence, EvidenceFact
from .verdict import Verdict

__all__ = [
    "RULE_IDS",
    "AuditCreateRequest",
    "AuditCreated",
    "AuditError",
    "AuditEvent",
    "AuditMode",
    "AuditResult",
    "AuditStatus",
    "AuditStatusView",
    "Briefing",
    "BriefingItem",
    "Claim",
    "ClaimType",
    "Evidence",
    "EvidenceFact",
    "Origin",
    "Rule",
    "Source",
    "StrictModel",
    "SupplierInfo",
    "Verdict",
    "VerdictValue",
    "coerce_rule",
    "normalize_whitespace",
    "quote_in_text",
]
