"""Public output is a validated DTO, never an ORM row or worker trace."""

from datetime import datetime

from ..schemas import AuditResult


def public_result(result: AuditResult, published_at: datetime) -> AuditResult:
    # Request headers, job tokens, prompts and raw bodies have no public fields.
    payload = {name: getattr(result, name) for name in AuditResult.model_fields}
    payload["published_at"] = published_at
    return AuditResult.model_validate(payload).model_copy(deep=True)
