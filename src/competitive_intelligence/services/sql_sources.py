"""Bounded Azure source archives and baseline reads, fenced by the job lease."""

import json
from datetime import UTC
from typing import TYPE_CHECKING
from uuid import uuid4, uuid5

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AuditSourceRow, BaselineClaimRow, BaselineRow, SourceCaptureRow, SourceRow, SupplierRow
from ..parsers import PARSER_VERSION, sha256_text
from ..registry import SupplierRegistry
from ..schemas import Origin, Source
from .repository import StoredAuditError
from .sql_repository import audit_uuid, locked
from .stores import Baseline, BaselineStore, Snapshot, SnapshotStore

if TYPE_CHECKING:
    from .sql_repository import SqlAuditRepository

# NVARCHAR is UTF-16: cap canonical text at 1 MB per capture, without truncation.
MAX_CANONICAL_BYTES = 1_000_000


class SourceStorageError(StoredAuditError):
    """Source configuration or stored provenance is unusable; never fall back to files."""


def checked_snapshot(snapshot: Snapshot) -> Snapshot:
    try:
        value = Snapshot.model_validate(snapshot.model_dump())
        if (value.parser_version != PARSER_VERSION
                or len(value.text.encode("utf-16-le")) > MAX_CANONICAL_BYTES
                or sha256_text(value.text) != value.canonical_hash):
            raise ValueError("Incompatible source archive")
        return value
    except (ValueError, ValidationError):
        raise SourceStorageError("Source archive failed integrity, version or size validation") from None


class SqlSnapshotStore:
    def __init__(self, storage: "SqlSourceStorage") -> None:
        self.storage = storage

    def get(self, source_id: str) -> Snapshot | None:
        return self.storage.get_snapshot(source_id)

    def save(self, snapshot: Snapshot) -> bool:
        self.storage.save_snapshot(snapshot)
        return True


class SqlBaselineStore:
    def __init__(self, storage: "SqlSourceStorage") -> None:
        self.storage = storage

    def get(self, source_id: str) -> Baseline | None:
        return self.storage.get_baseline(source_id)


class SqlSourceStorage:
    def __init__(self, repo: "SqlAuditRepository", audit_id: str, registry: SupplierRegistry,
                 *, success_status: str = "ok") -> None:
        if success_status != "ok":
            raise ValueError("Successful source captures must use 'ok'")
        self.repo = repo
        self.audit_id = audit_id
        self.success_status = success_status
        self.pages = {
            page.source_id: (supplier, page)
            for supplier in registry.suppliers for page in supplier.pages
        }
        if len(self.pages) != sum(len(s.pages) for s in registry.suppliers):
            raise SourceStorageError("Source IDs must be unique in the approved registry")
        self.snapshots: SnapshotStore = SqlSnapshotStore(self)
        self.baselines: BaselineStore = SqlBaselineStore(self)

    def _source(self, session: Session, source_id: str, *, create=False, now=None) -> SourceRow | None:
        try:
            supplier, page = self.pages[source_id]
        except KeyError:
            raise SourceStorageError("Source is outside the approved registry") from None
        if not supplier.database_code:
            raise SourceStorageError("Supplier database mapping is missing")
        statement = select(SupplierRow).where(SupplierRow.supplier_code == supplier.database_code)
        # All source creation for one supplier follows the same lock order.
        row = session.scalar(locked(statement, SupplierRow) if create else statement)
        if row is None or not row.is_active or row.supplier_code != supplier.database_code:
            raise SourceStorageError("Mapped supplier is missing or inactive")
        sources = session.scalars(select(SourceRow).where(
            SourceRow.supplier_id == row.supplier_id,
            SourceRow.source_type == "web_page",
            SourceRow.source_url == page.url,
        )).all()
        if len(sources) > 1:
            raise SourceStorageError("Source URL has ambiguous database identities")
        if sources:
            source = sources[0]
            if not source.is_active or source.source_url != page.url:
                raise SourceStorageError("Source is inactive or its exact URL differs")
            return source
        if not create:
            return None
        # This is runtime capture metadata, never supplier/reference-data seeding.
        source = SourceRow(
            source_id=uuid5(row.supplier_id, f"fact-or-watt:web-page:{page.url}"),
            supplier_id=row.supplier_id, source_type="web_page", source_url=page.url,
            source_title=None, publisher_name=None, is_active=True, created_at=now,
        )
        session.add(source)
        session.flush()
        return source

    @staticmethod
    def _snapshot(source_id: str, source_url: str, capture: SourceCaptureRow) -> Snapshot:
        try:
            return checked_snapshot(Snapshot.model_validate({
                "source_id": source_id, "source_url": source_url, "captured_at": capture.captured_at,
                "parser_version": capture.parser_version, "content_sha256": capture.content_sha256,
                "canonical_hash": capture.canonical_sha256, "text": capture.canonical_text,
            }))
        except ValidationError:
            raise SourceStorageError("Stored source capture is incomplete") from None

    def get_snapshot(self, source_id: str) -> Snapshot | None:
        with self.repo.sessions() as session:
            source = self._source(session, source_id)
            if source is None:
                return None
            capture = session.scalar(select(SourceCaptureRow).where(
                SourceCaptureRow.source_id == source.source_id,
                SourceCaptureRow.fetch_status == self.success_status,
                SourceCaptureRow.parser_version == PARSER_VERSION,
            ).order_by(SourceCaptureRow.captured_at.desc(), SourceCaptureRow.capture_id.desc()).limit(1))
            return self._snapshot(source_id, source.source_url, capture) if capture is not None else None

    def save_snapshot(self, snapshot: Snapshot) -> None:
        snapshot = checked_snapshot(snapshot)
        binding = self.pages.get(snapshot.source_id)
        if binding is None or snapshot.source_url != binding[1].url:
            raise SourceStorageError("Capture URL differs from its approved source")
        with self.repo.sessions.begin() as session:
            _, _, now = self.repo._owned(session, self.audit_id)
            source = self._source(session, snapshot.source_id, create=True, now=now)
            if source is None:
                raise SourceStorageError("Capture has no source identity")
            key = json.dumps([
                self.audit_id, snapshot.captured_at.astimezone(UTC).isoformat(),
                snapshot.content_sha256, snapshot.canonical_hash, snapshot.parser_version,
            ])
            capture_id = uuid5(source.source_id, key)
            if session.get(SourceCaptureRow, capture_id) is not None:
                return
            session.add(SourceCaptureRow(
                capture_id=capture_id, source_id=source.source_id, captured_at=snapshot.captured_at,
                valid_as_of=snapshot.captured_at.date(), valid_from=None, valid_to=None,
                fetch_status=self.success_status, http_status=200,
                content_sha256=snapshot.content_sha256, canonical_sha256=snapshot.canonical_hash,
                parser_version=snapshot.parser_version, extractor_version=None,
                canonical_text=snapshot.text, blob_key=None, failure_reason=None, created_at=now,
            ))

    def _baseline(self, session: Session, source: SourceRow, *, for_update=False) -> BaselineRow | None:
        statement = select(BaselineRow).where(
            BaselineRow.source_id == source.source_id, BaselineRow.is_active.is_(True),
        ).order_by(BaselineRow.approved_at.desc(), BaselineRow.baseline_id.desc()).limit(1)
        return session.scalar(locked(statement, BaselineRow) if for_update else statement)

    def get_baseline(self, source_id: str) -> Baseline | None:
        with self.repo.sessions() as session:
            source = self._source(session, source_id)
            if source is None:
                return None
            baseline = self._baseline(session, source)
            if baseline is None:
                return None
            capture = session.get(SourceCaptureRow, baseline.capture_id)
            if (capture is None or capture.source_id != source.source_id
                    or capture.fetch_status != self.success_status
                    or capture.canonical_sha256 != baseline.canonical_sha256
                    or capture.parser_version != baseline.parser_version
                    or not baseline.approved_by.strip()):
                raise SourceStorageError("Approved baseline has inconsistent source provenance")
            # An old parser baseline is incompatible, rather than evidence of a changed claim.
            if baseline.parser_version != PARSER_VERSION:
                return None
            self._snapshot(source_id, source.source_url, capture)
            rows = session.scalars(select(BaselineClaimRow).where(
                BaselineClaimRow.baseline_id == baseline.baseline_id,
                BaselineClaimRow.human_reviewed.is_(True),
            ).order_by(BaselineClaimRow.baseline_claim_id)).all()
            try:
                claims = []
                for row in rows:
                    params = json.loads(row.rule_params_json)
                    if not isinstance(params, dict):
                        raise ValueError("Invalid rule parameters")
                    claims.append({
                        "id": str(row.baseline_claim_id), "source_id": source_id,
                        "product_id": str(row.product_id) if row.product_id else None,
                        "quote": row.quote, "source_ref": row.source_ref, "claim_type": row.claim_type,
                        "human_reviewed": True, "rule": {"id": row.rule_id, "params": params},
                    })
                return Baseline(
                    source_id=source_id, source_url=source.source_url,
                    parser_version=baseline.parser_version, canonical_hash=baseline.canonical_sha256,
                    approved_at=baseline.approved_at, claims=claims,
                )
            except (ValueError, ValidationError):
                raise SourceStorageError("Approved baseline payload is invalid") from None

    def record(self, source: Source) -> None:
        """Link the immutable capture to this delivery, including fallback/approval provenance."""
        supplier, page = self.pages.get(source.source_id, (None, None))
        if supplier is None or source.supplier_id != supplier.supplier_id or source.source_url != page.url:
            raise SourceStorageError("Audit source differs from the approved registry")
        if (source.origin is Origin.SNAPSHOT) == source.fetch_was_live:
            raise SourceStorageError("Audit source origin and fetch state disagree")
        with self.repo.sessions.begin() as session:
            self.repo._owned(session, self.audit_id)
            row = self._source(session, source.source_id)
            if row is None:
                raise SourceStorageError("Audit source has no stored capture")
            capture = session.scalar(select(SourceCaptureRow).where(
                SourceCaptureRow.source_id == row.source_id,
                SourceCaptureRow.fetch_status == self.success_status,
                SourceCaptureRow.captured_at == source.captured_at,
                SourceCaptureRow.content_sha256 == source.content_sha256,
                SourceCaptureRow.canonical_sha256 == source.canonical_hash,
                SourceCaptureRow.parser_version == source.parser_version,
            ).order_by(SourceCaptureRow.capture_id.desc()).limit(1))
            if capture is None:
                raise SourceStorageError("Audit source has no matching immutable capture")
            self._snapshot(source.source_id, source.source_url, capture)
            if (source.checked_at < source.captured_at
                    or source.valid_as_of != capture.valid_as_of
                    or (source.origin is Origin.SNAPSHOT and not source.fallback_reason)):
                raise SourceStorageError("Audit source dates or fallback provenance disagree")
            baseline = self._baseline(session, row, for_update=True) if source.origin is Origin.BASELINE else None
            if source.origin is Origin.BASELINE and (
                baseline is None or baseline.canonical_sha256 != source.canonical_hash
                or baseline.parser_version != source.parser_version
                or baseline.approved_at != source.baseline_approved_at
            ):
                raise SourceStorageError("Baseline approval changed during source intake")
            association = session.scalar(locked(select(AuditSourceRow).where(
                AuditSourceRow.audit_id == audit_uuid(self.audit_id),
                AuditSourceRow.source_id == row.source_id,
            ), AuditSourceRow))
            if association is None:
                association = AuditSourceRow(audit_source_id=uuid4(),
                    audit_id=audit_uuid(self.audit_id), source_id=row.source_id)
                session.add(association)
            association.capture_id = capture.capture_id
            association.baseline_id = baseline.baseline_id if baseline is not None else None
            association.origin = source.origin.value
            association.fetch_was_live = source.fetch_was_live
            association.checked_at = source.checked_at
            association.fallback_reason = source.fallback_reason[:500] if source.fallback_reason else None
