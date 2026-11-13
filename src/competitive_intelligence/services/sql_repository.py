"""Azure SQL queue, fenced worker transactions and versioned audit snapshots."""

import json
from datetime import timedelta
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import and_, func, or_, select

from ..models import AuditEventRow, AuditJob, AuditRun, BriefingRow, Publication
from ..schemas import AuditCreateRequest, AuditError, AuditEvent, AuditMode, AuditResult, AuditStatus, AuditStatusView
from .publication import public_result
from .repository import AuditNotFound, LeaseLost, PROGRESS, StoredAuditError


def decode_result(payload, *, envelope=False):
    try:
        data = json.loads(payload)
        if envelope:
            if data["version"] != 1:
                raise ValueError("Unsupported version")
            data = data["audit_result"]
        return AuditResult.model_validate(data)
    except (ValueError, KeyError, TypeError, ValidationError):
        raise StoredAuditError("Persisted audit payload is incompatible") from None


def audit_uuid(audit_id):
    try:
        return UUID(audit_id)
    except (ValueError, AttributeError):
        raise AuditNotFound(audit_id) from None


def locked(statement, model, *, skip_locked=False):
    hint = "WITH (UPDLOCK, READPAST, READCOMMITTEDLOCK)" if skip_locked else "WITH (UPDLOCK, HOLDLOCK)"
    return statement.with_hint(model, hint, dialect_name="mssql")


class SqlAuditRepository:
    durable = True

    def __init__(self, sessions, *, lease_seconds=60, max_attempts=3, retry_seconds=10):
        self.sessions = sessions
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts
        self.retry_seconds = retry_seconds
        self._leases: dict[str, str] = {}

    @staticmethod
    def _now(session):
        return session.scalar(select(func.sysutcdatetime(type_=AuditRun.created_at.type)))

    @staticmethod
    def _run(session, audit_id):
        row = session.get(AuditRun, audit_uuid(audit_id))
        if row is None:
            raise AuditNotFound(audit_id)
        return row

    @staticmethod
    def _event(session, run, now, stage, message, level="info", origin=None):
        session.add(AuditEventRow(
            audit_id=run.audit_id, occurred_at=now, stage=stage, severity=level,
            message=message[:1000], metadata_json=json.dumps({"origin": origin}) if origin else None,
        ))

    def create_audit(self, request):
        audit_id = uuid4()
        with self.sessions.begin() as session:
            now = self._now(session)
            run = AuditRun(audit_id=audit_id, mode=request.mode.value, status="queued",
                           request_config_json=request.model_dump_json(), progress_percent=0, created_at=now)
            session.add(run)
            session.flush()
            session.add(AuditJob(job_id=uuid4(), audit_id=audit_id, status="queued", priority=0,
                                 available_at=now, attempt_count=0, max_attempts=self.max_attempts, created_at=now))
            self._event(session, run, now, "queued", "Audit zařazen do fronty.")
        return str(audit_id)

    def get_request(self, audit_id):
        with self.sessions() as session:
            return AuditCreateRequest.model_validate_json(self._run(session, audit_id).request_config_json)

    def claim_next_job(self):
        while True:
            with self.sessions.begin() as session:
                now = self._now(session)
                job = session.scalar(locked(select(AuditJob).where(or_(
                    and_(AuditJob.status == "queued", AuditJob.available_at <= now),
                    and_(AuditJob.status == "leased", AuditJob.lease_expires_at <= now),
                )).order_by(AuditJob.priority.desc(), AuditJob.available_at, AuditJob.job_id).limit(1),
                    AuditJob, skip_locked=True))
                if job is None:
                    return None
                run = self._run(session, str(job.audit_id))
                if run.status in ("completed", "failed"):
                    job.status = "completed" if run.status == "completed" else "dead"
                    job.leased_by = job.lease_expires_at = None
                    continue
                if job.attempt_count >= job.max_attempts:
                    self._mark_failed(session, run, job, now, "RETRIES_EXHAUSTED", "Vyčerpán počet pokusů o zpracování auditu.")
                    continue
                token = uuid4().hex
                job.status, job.leased_by = "leased", token
                job.attempt_count += 1
                job.heartbeat_at = now
                job.lease_expires_at = now + timedelta(seconds=self.lease_seconds)
                run.status, run.progress_percent, run.error_code = "collecting", 0, None
                run.started_at = run.started_at or now
                self._event(session, run, now, "worker", f"Worker převzal audit (pokus {job.attempt_count}/{job.max_attempts}).")
                audit_id = str(job.audit_id)
            # Ownership is retained only after committing the atomic claim.
            self._leases[audit_id] = token
            return audit_id

    def _owned(self, session, audit_id):
        job = session.scalar(locked(select(AuditJob).where(AuditJob.audit_id == audit_uuid(audit_id)), AuditJob))
        now = self._now(session)
        token = self._leases.get(audit_id)
        if (job is None or token is None or job.status != "leased"
                or job.leased_by != token or job.lease_expires_at <= now):
            raise LeaseLost("Job lease is no longer owned by this delivery")
        return self._run(session, audit_id), job, now

    def heartbeat(self, audit_id):
        with self.sessions.begin() as session:
            _, job, now = self._owned(session, audit_id)
            job.heartbeat_at, job.lease_expires_at = now, now + timedelta(seconds=self.lease_seconds)

    def set_stage(self, audit_id, status, stage):
        with self.sessions.begin() as session:
            run, job, now = self._owned(session, audit_id)
            run.status, run.progress_percent = status.value, PROGRESS.get(status, 0)
            job.heartbeat_at, job.lease_expires_at = now, now + timedelta(seconds=self.lease_seconds)
            self._event(session, run, now, stage, "Zpracování další fáze auditu.")

    def add_event(self, audit_id, stage, message, level="info", origin=None):
        with self.sessions.begin() as session:
            run, _, now = self._owned(session, audit_id)
            self._event(session, run, now, stage, message, level, origin)

    def complete(self, audit_id, result):
        if audit_uuid(result.audit_id) != audit_uuid(audit_id):
            raise ValueError("Result belongs to a different audit")
        with self.sessions.begin() as session:
            if self._run(session, audit_id).status == "completed":
                return
            run, job, now = self._owned(session, audit_id)
            session.add(BriefingRow(audit_id=run.audit_id, briefing_text=result.briefing.summary,
                briefing_json=json.dumps({"version": 1, "audit_result": result.model_dump(mode="json")}, ensure_ascii=False),
                model_name="fixture-deterministic-v1" if result.mode is AuditMode.FIXTURE
                    else "web-scout-deterministic-v1", created_at=now))
            run.status, run.progress_percent, run.finished_at = "completed", 100, now
            run.scope_note = result.scope_note[:1000]
            job.status, job.completed_at = "completed", now
            job.leased_by = job.lease_expires_at = None
            self._event(session, run, now, "completed", "Audit dokončen. Publikace vyžaduje samostatné schválení.")
        self._leases.pop(audit_id, None)

    def _mark_failed(self, session, run, job, now, code, message):
        run.status, run.error_code, run.finished_at = "failed", code[:80], now
        job.status, job.completed_at = "dead", now
        job.last_error_code, job.last_error_message = code[:80], message[:500]
        job.leased_by = job.lease_expires_at = None
        self._event(session, run, now, "failed", message, "error")

    def fail(self, audit_id, code, message):
        with self.sessions.begin() as session:
            run, job, now = self._owned(session, audit_id)
            self._mark_failed(session, run, job, now, code, message)
        self._leases.pop(audit_id, None)

    def retry(self, audit_id, code, message):
        with self.sessions.begin() as session:
            run, job, now = self._owned(session, audit_id)
            if job.attempt_count >= job.max_attempts:
                self._mark_failed(session, run, job, now, code, message)
            else:
                run.status, run.progress_percent = "queued", 0
                job.status, job.last_error_code, job.last_error_message = "queued", code[:80], message[:500]
                job.available_at = now + timedelta(seconds=self.retry_seconds)
                job.leased_by = job.lease_expires_at = None
                self._event(session, run, now, "queued", "Zpracování přerušeno; audit čeká na další pokus.", "warning")
        self._leases.pop(audit_id, None)

    def get_status(self, audit_id):
        with self.sessions() as session:
            run = self._run(session, audit_id)
            rows = session.scalars(select(AuditEventRow).where(AuditEventRow.audit_id == run.audit_id)
                                   .order_by(AuditEventRow.event_id)).all()
            events = [AuditEvent(event_id=row.event_id, timestamp=row.occurred_at, stage=row.stage,
                level=row.severity, message=row.message,
                origin=json.loads(row.metadata_json).get("origin") if row.metadata_json else None) for row in rows]
            status = AuditStatus(run.status)
            terminal = status in (AuditStatus.QUEUED, AuditStatus.COMPLETED, AuditStatus.FAILED)
            return AuditStatusView(audit_id=audit_id, status=status,
                stage=status.value if terminal or not events else events[-1].stage,
                progress_percent=run.progress_percent,
                updated_at=max([run.created_at, *[event.timestamp for event in events]]), events=events,
                warnings=[event.message for event in events if event.level == "warning"],
                error=AuditError(code=run.error_code or "PIPELINE_ERROR", message="Audit se nepodařilo dokončit.")
                    if status is AuditStatus.FAILED else None)

    def _result(self, session, audit_id):
        run = self._run(session, audit_id)
        if run.status != "completed":
            return None
        row = session.get(BriefingRow, run.audit_id)
        if row is None or row.briefing_json is None:
            raise StoredAuditError("Completed audit has no supported result snapshot")
        result = decode_result(row.briefing_json, envelope=True)
        if audit_uuid(result.audit_id) != run.audit_id:
            raise StoredAuditError("Result snapshot belongs to a different audit")
        return result

    def get_result(self, audit_id):
        with self.sessions() as session:
            result = self._result(session, audit_id)
            if result is not None:
                publication = session.scalar(select(Publication).where(Publication.audit_id == audit_uuid(audit_id)))
                if publication is not None:
                    result = result.model_copy(update={"published_at": publication.published_at})
            return result

    def publish(self, audit_id, approved_by="authorized-operator"):
        if not approved_by.strip() or len(approved_by) > 150:
            raise ValueError("Publication requires an approver of at most 150 characters")
        with self.sessions.begin() as session:
            # Serializable range locking covers even the first approval into an empty table.
            current = session.scalars(locked(select(Publication).where(Publication.is_current == True), Publication)).all()
            result = self._result(session, audit_id)
            if result is None:
                raise ValueError("Only completed audits can be published")
            existing = session.scalar(select(Publication).where(Publication.audit_id == audit_uuid(audit_id)))
            if existing is not None and existing.is_current:
                return decode_result(existing.public_payload_json)
            for row in current:
                row.is_current = False
            session.flush()
            now = self._now(session)
            sanitized = public_result(result, now)
            if existing is None:
                existing = Publication(publication_id=uuid4(), audit_id=audit_uuid(audit_id))
                session.add(existing)
            existing.public_payload_json = sanitized.model_dump_json()
            existing.published_at, existing.approved_by, existing.is_current = now, approved_by, True
        return sanitized

    def get_latest_published(self):
        with self.sessions() as session:
            row = session.scalar(select(Publication).where(Publication.is_current == True))
            return decode_result(row.public_payload_json) if row else None
