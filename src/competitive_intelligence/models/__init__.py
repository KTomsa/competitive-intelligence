"""Mappings verified against the manually provisioned ci schema in Azure SQL.

Operational and source-intake mappings only. Do not call metadata.create_all.
"""

from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy import BigInteger, ForeignKey, ForeignKeyConstraint, Integer, Unicode
from sqlalchemy.dialects.mssql import CHAR, DATE, DATETIME2, NVARCHAR, SMALLINT, TINYINT, UNIQUEIDENTIFIER
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator):
    """The existing datetime2 columns store UTC without a timezone offset."""

    impl = DATETIME2
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Database timestamps must be timezone-aware")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        return value.replace(tzinfo=UTC) if value is not None else None


class Base(DeclarativeBase):
    pass


class AuditRun(Base):
    __tablename__ = "audit_runs"
    __table_args__ = {"schema": "ci"}

    audit_id: Mapped[UUID] = mapped_column(UNIQUEIDENTIFIER, primary_key=True)
    mode: Mapped[str] = mapped_column(Unicode(12))
    status: Mapped[str] = mapped_column(Unicode(20))
    scope_note: Mapped[str | None] = mapped_column(Unicode(1000))
    request_config_json: Mapped[str | None] = mapped_column(NVARCHAR(None))
    progress_percent: Mapped[int] = mapped_column(TINYINT)
    requested_by: Mapped[str | None] = mapped_column(Unicode(150))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    error_code: Mapped[str | None] = mapped_column(Unicode(80))


class AuditJob(Base):
    __tablename__ = "audit_jobs"
    __table_args__ = {"schema": "ci"}

    job_id: Mapped[UUID] = mapped_column(UNIQUEIDENTIFIER, primary_key=True)
    audit_id: Mapped[UUID] = mapped_column(ForeignKey("ci.audit_runs.audit_id"), unique=True)
    status: Mapped[str] = mapped_column(Unicode(20))
    priority: Mapped[int] = mapped_column(SMALLINT)
    available_at: Mapped[datetime] = mapped_column(UTCDateTime())
    attempt_count: Mapped[int] = mapped_column(Integer)
    max_attempts: Mapped[int] = mapped_column(SMALLINT)
    leased_by: Mapped[str | None] = mapped_column(Unicode(160))
    lease_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    heartbeat_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_error_code: Mapped[str | None] = mapped_column(Unicode(80))
    last_error_message: Mapped[str | None] = mapped_column(Unicode(500))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())


class AuditEventRow(Base):
    __tablename__ = "audit_events"
    __table_args__ = {"schema": "ci"}

    event_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    audit_id: Mapped[UUID] = mapped_column(ForeignKey("ci.audit_runs.audit_id"))
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime())
    stage: Mapped[str] = mapped_column(Unicode(50))
    severity: Mapped[str] = mapped_column(Unicode(10))
    message: Mapped[str] = mapped_column(Unicode(1000))
    metadata_json: Mapped[str | None] = mapped_column(NVARCHAR(None))


class BriefingRow(Base):
    __tablename__ = "briefings"
    __table_args__ = {"schema": "ci"}

    audit_id: Mapped[UUID] = mapped_column(ForeignKey("ci.audit_runs.audit_id"), primary_key=True)
    briefing_text: Mapped[str] = mapped_column(NVARCHAR(None))
    briefing_json: Mapped[str | None] = mapped_column(NVARCHAR(None))
    model_name: Mapped[str | None] = mapped_column(Unicode(150))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())


class Publication(Base):
    __tablename__ = "publications"
    __table_args__ = {"schema": "ci"}

    publication_id: Mapped[UUID] = mapped_column(UNIQUEIDENTIFIER, primary_key=True)
    audit_id: Mapped[UUID] = mapped_column(ForeignKey("ci.audit_runs.audit_id"), unique=True)
    public_payload_json: Mapped[str] = mapped_column(NVARCHAR(None))
    approved_by: Mapped[str] = mapped_column(Unicode(150))
    published_at: Mapped[datetime] = mapped_column(UTCDateTime())
    is_current: Mapped[bool]


class SupplierRow(Base):
    __tablename__ = "suppliers"
    __table_args__ = {"schema": "ci"}

    supplier_id: Mapped[UUID] = mapped_column(UNIQUEIDENTIFIER, primary_key=True)
    supplier_code: Mapped[str] = mapped_column(Unicode(80))
    display_name: Mapped[str] = mapped_column(Unicode(200))
    is_active: Mapped[bool]
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())


class SourceRow(Base):
    __tablename__ = "sources"
    __table_args__ = {"schema": "ci"}

    source_id: Mapped[UUID] = mapped_column(UNIQUEIDENTIFIER, primary_key=True)
    supplier_id: Mapped[UUID | None] = mapped_column(ForeignKey("ci.suppliers.supplier_id"))
    source_type: Mapped[str] = mapped_column(Unicode(30))
    source_url: Mapped[str] = mapped_column(Unicode(2048))
    source_title: Mapped[str | None] = mapped_column(Unicode(300))
    publisher_name: Mapped[str | None] = mapped_column(Unicode(200))
    is_active: Mapped[bool]
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())


class SourceCaptureRow(Base):
    __tablename__ = "source_captures"
    __table_args__ = {"schema": "ci"}

    capture_id: Mapped[UUID] = mapped_column(UNIQUEIDENTIFIER, primary_key=True)
    source_id: Mapped[UUID] = mapped_column(ForeignKey("ci.sources.source_id"))
    captured_at: Mapped[datetime] = mapped_column(UTCDateTime())
    valid_as_of: Mapped[date | None] = mapped_column(DATE)
    valid_from: Mapped[date | None] = mapped_column(DATE)
    valid_to: Mapped[date | None] = mapped_column(DATE)
    fetch_status: Mapped[str] = mapped_column(Unicode(20))
    http_status: Mapped[int | None] = mapped_column(SMALLINT)
    content_sha256: Mapped[str | None] = mapped_column(CHAR(64))
    canonical_sha256: Mapped[str | None] = mapped_column(CHAR(64))
    parser_version: Mapped[str | None] = mapped_column(Unicode(80))
    extractor_version: Mapped[str | None] = mapped_column(Unicode(80))
    canonical_text: Mapped[str | None] = mapped_column(NVARCHAR(None))
    blob_key: Mapped[str | None] = mapped_column(Unicode(1024))
    failure_reason: Mapped[str | None] = mapped_column(Unicode(500))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())


class BaselineRow(Base):
    __tablename__ = "baselines"
    __table_args__ = (
        ForeignKeyConstraint(["capture_id", "source_id"],
                             ["ci.source_captures.capture_id", "ci.source_captures.source_id"]),
        {"schema": "ci"},
    )

    baseline_id: Mapped[UUID] = mapped_column(UNIQUEIDENTIFIER, primary_key=True)
    source_id: Mapped[UUID] = mapped_column(UNIQUEIDENTIFIER)
    capture_id: Mapped[UUID] = mapped_column(UNIQUEIDENTIFIER)
    canonical_sha256: Mapped[str] = mapped_column(CHAR(64))
    parser_version: Mapped[str] = mapped_column(Unicode(80))
    extractor_version: Mapped[str] = mapped_column(Unicode(80))
    approved_at: Mapped[datetime] = mapped_column(UTCDateTime())
    approved_by: Mapped[str] = mapped_column(Unicode(150))
    is_active: Mapped[bool]


class BaselineClaimRow(Base):
    __tablename__ = "baseline_claims"
    __table_args__ = {"schema": "ci"}

    baseline_claim_id: Mapped[UUID] = mapped_column(UNIQUEIDENTIFIER, primary_key=True)
    baseline_id: Mapped[UUID] = mapped_column(ForeignKey("ci.baselines.baseline_id"))
    # Product reference data is administered separately; no product writes here.
    product_id: Mapped[UUID | None] = mapped_column(UNIQUEIDENTIFIER)
    quote: Mapped[str] = mapped_column(NVARCHAR(None))
    source_ref: Mapped[str] = mapped_column(Unicode(500))
    claim_type: Mapped[str] = mapped_column(Unicode(20))
    rule_id: Mapped[str] = mapped_column(Unicode(40))
    rule_params_json: Mapped[str] = mapped_column(NVARCHAR(None))
    human_reviewed: Mapped[bool]


class AuditSourceRow(Base):
    __tablename__ = "audit_sources"
    __table_args__ = (
        ForeignKeyConstraint(["capture_id", "source_id"],
                             ["ci.source_captures.capture_id", "ci.source_captures.source_id"]),
        ForeignKeyConstraint(["baseline_id", "source_id"],
                             ["ci.baselines.baseline_id", "ci.baselines.source_id"]),
        {"schema": "ci"},
    )

    audit_source_id: Mapped[UUID] = mapped_column(UNIQUEIDENTIFIER, primary_key=True)
    audit_id: Mapped[UUID] = mapped_column(ForeignKey("ci.audit_runs.audit_id"))
    source_id: Mapped[UUID] = mapped_column(UNIQUEIDENTIFIER)
    capture_id: Mapped[UUID] = mapped_column(UNIQUEIDENTIFIER)
    baseline_id: Mapped[UUID | None] = mapped_column(UNIQUEIDENTIFIER)
    origin: Mapped[str] = mapped_column(Unicode(12))
    fetch_was_live: Mapped[bool]
    checked_at: Mapped[datetime] = mapped_column(UTCDateTime())
    fallback_reason: Mapped[str | None] = mapped_column(Unicode(500))
