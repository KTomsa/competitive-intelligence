"""Audit persistence boundary.

`AuditRepository` is the contract the web app, the worker and the pipeline
depend on. Phase 1 ships only the in-memory implementation, used for the
offline fixture flow and unit tests. The Azure SQL implementation
(SQLAlchemy + Alembic) plugs in behind the same protocol in the next phase;
nothing above this module knows which one it is talking to.
"""

import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal, Protocol

from ..schemas import (
    AuditCreateRequest,
    AuditError,
    AuditEvent,
    AuditResult,
    AuditStatus,
    AuditStatusView,
    Origin,
)

PROGRESS = {
    AuditStatus.QUEUED: 0,
    AuditStatus.COLLECTING: 15,
    AuditStatus.EXTRACTING: 35,
    AuditStatus.VERIFYING: 60,
    AuditStatus.JUDGING: 80,
    AuditStatus.REPORTING: 92,
    AuditStatus.COMPLETED: 100,
}


def utc_now() -> datetime:
    return datetime.now(UTC)


class AuditNotFound(LookupError):
    pass


class AuditRepository(Protocol):
    def create_audit(self, request: AuditCreateRequest) -> str: ...

    def get_request(self, audit_id: str) -> AuditCreateRequest: ...

    def claim_next_job(self) -> str | None: ...

    def set_stage(self, audit_id: str, status: AuditStatus, stage: str) -> None: ...

    def add_event(
        self,
        audit_id: str,
        stage: str,
        message: str,
        level: Literal["info", "warning", "error"] = "info",
        origin: Origin | None = None,
    ) -> None: ...

    def complete(self, audit_id: str, result: AuditResult) -> None: ...

    def fail(self, audit_id: str, code: str, message: str) -> None: ...

    def get_status(self, audit_id: str) -> AuditStatusView: ...

    def get_result(self, audit_id: str) -> AuditResult | None: ...

    def publish(self, audit_id: str) -> AuditResult: ...

    def get_latest_published(self) -> AuditResult | None: ...


@dataclass
class _Record:
    request: AuditCreateRequest
    status: AuditStatus = AuditStatus.QUEUED
    stage: str = "queued"
    updated_at: datetime = field(default_factory=utc_now)
    events: list[AuditEvent] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: AuditError | None = None
    result: AuditResult | None = None
    claimed: bool = False


class InMemoryAuditRepository:
    """Process-local store. State is lost on restart and is not shared between processes."""

    durable = False

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: dict[str, _Record] = {}
        self._queue: list[str] = []
        self._latest_published: str | None = None

    def _get(self, audit_id: str) -> _Record:
        try:
            return self._records[audit_id]
        except KeyError:
            raise AuditNotFound(audit_id) from None

    def create_audit(self, request: AuditCreateRequest) -> str:
        audit_id = f"audit_{uuid.uuid4().hex[:12]}"
        with self._lock:
            self._records[audit_id] = _Record(request=request)
            self._queue.append(audit_id)
        return audit_id

    def get_request(self, audit_id: str) -> AuditCreateRequest:
        with self._lock:
            return self._get(audit_id).request

    def claim_next_job(self) -> str | None:
        with self._lock:
            while self._queue:
                audit_id = self._queue.pop(0)
                record = self._records[audit_id]
                if not record.claimed:
                    record.claimed = True
                    return audit_id
            return None

    def set_stage(self, audit_id: str, status: AuditStatus, stage: str) -> None:
        with self._lock:
            record = self._get(audit_id)
            record.status = status
            record.stage = stage
            record.updated_at = utc_now()

    def add_event(
        self,
        audit_id: str,
        stage: str,
        message: str,
        level: Literal["info", "warning", "error"] = "info",
        origin: Origin | None = None,
    ) -> None:
        with self._lock:
            record = self._get(audit_id)
            record.events.append(
                AuditEvent(
                    event_id=len(record.events) + 1,
                    timestamp=utc_now(),
                    stage=stage,
                    level=level,
                    message=message,
                    origin=origin,
                )
            )
            if level == "warning":
                record.warnings.append(message)
            record.updated_at = utc_now()

    def complete(self, audit_id: str, result: AuditResult) -> None:
        with self._lock:
            record = self._get(audit_id)
            if record.status is AuditStatus.COMPLETED:
                return  # idempotent: a repeated delivery must not overwrite a finished audit
            record.result = result
            record.status = AuditStatus.COMPLETED
            record.stage = "completed"
            record.updated_at = utc_now()

    def fail(self, audit_id: str, code: str, message: str) -> None:
        with self._lock:
            record = self._get(audit_id)
            record.status = AuditStatus.FAILED
            record.stage = "failed"
            record.error = AuditError(code=code, message=message)
            record.updated_at = utc_now()

    def get_status(self, audit_id: str) -> AuditStatusView:
        with self._lock:
            record = self._get(audit_id)
            return AuditStatusView(
                audit_id=audit_id,
                status=record.status,
                stage=record.stage,
                progress_percent=PROGRESS.get(record.status, 0),
                updated_at=record.updated_at,
                events=list(record.events),
                warnings=list(record.warnings),
                error=record.error,
            )

    def get_result(self, audit_id: str) -> AuditResult | None:
        with self._lock:
            return self._get(audit_id).result

    def publish(self, audit_id: str) -> AuditResult:
        with self._lock:
            record = self._get(audit_id)
            if record.result is None:
                raise ValueError("Only a completed audit can be published")
            record.result = record.result.model_copy(update={"published_at": utc_now()})
            self._latest_published = audit_id
            return record.result

    def get_latest_published(self) -> AuditResult | None:
        with self._lock:
            if self._latest_published is None:
                return None
            return self._records[self._latest_published].result
