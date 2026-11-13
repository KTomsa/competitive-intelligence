"""Source archive integrity, approval reads, lease fencing and worker integration."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.dialects import mssql
from sqlalchemy.exc import OperationalError

from competitive_intelligence.models import AuditSourceRow, BaselineClaimRow, BaselineRow, SourceCaptureRow, SourceRow, SupplierRow
from competitive_intelligence.config import Settings
from competitive_intelligence.parsers import PARSER_VERSION, sha256_text
from competitive_intelligence.schemas import AuditCreateRequest, AuditMode, Origin
from competitive_intelligence.services import InMemoryAuditRepository
from competitive_intelligence.services.repository import LeaseLost
from competitive_intelligence.services.sql_repository import SqlAuditRepository
from competitive_intelligence.services.sql_sources import MAX_CANONICAL_BYTES, SourceStorageError, SqlSourceStorage
from competitive_intelligence.services.stores import Snapshot
from competitive_intelligence.worker import process_next

from .conftest import FakeWeb, make_context, page_html
from .source_fakes import SourceSessions

NOW = datetime(2026, 11, 13, 12, tzinfo=UTC)


class SourceBoundary(InMemoryAuditRepository):
    """Isolate source transactions from the separately tested audit repository."""

    durable = True
    lease_seconds = 60
    source_capture_success_status = "ok"  # Confirmed CK_captures_fetch_status value.
    source_storage = SqlAuditRepository.source_storage

    def __init__(self, sessions):
        super().__init__()
        self.sessions = sessions
        self.lease_valid = True
        self.fences = 0

    def _owned(self, session, audit_id):
        self.fences += 1
        if not self.lease_valid:
            raise LeaseLost()
        return None, None, NOW


@pytest.fixture
def boundary(registry):
    sessions = SourceSessions()
    for supplier in registry.suppliers:
        sessions.rows[SupplierRow].append(SupplierRow(
            supplier_id=uuid4(), supplier_code=supplier.database_code,
            display_name=supplier.name, is_active=True, created_at=NOW,
        ))
    return SourceBoundary(sessions)


@pytest.fixture
def archive(boundary, registry):
    return SqlSourceStorage(boundary, str(uuid4()), registry)


def snapshot(registry, text="Captured marketing text", captured_at=NOW):
    page = registry.get("cez").pages[0]
    return Snapshot(source_id=page.source_id, source_url=page.url, captured_at=captured_at,
                    parser_version=PARSER_VERSION, content_sha256="a" * 64,
                    canonical_hash=sha256_text(text), text=text)


def approve(archive, boundary, *, reviewed=True):
    capture = boundary.sessions.rows[SourceCaptureRow][-1]
    baseline = BaselineRow(
        baseline_id=uuid4(), source_id=capture.source_id, capture_id=capture.capture_id,
        canonical_sha256=capture.canonical_sha256, parser_version=capture.parser_version,
        extractor_version="review-v1", approved_at=NOW, approved_by="test-reviewer", is_active=True,
    )
    claim = BaselineClaimRow(
        baseline_claim_id=uuid4(), baseline_id=baseline.baseline_id, product_id=None,
        quote=capture.canonical_text, source_ref="main > h1", claim_type="green", rule_id="green_generic",
        rule_params_json="{}", human_reviewed=reviewed,
    )
    boundary.sessions.rows[BaselineRow].append(baseline)
    boundary.sessions.rows[BaselineClaimRow].append(claim)
    return baseline, claim


def test_capture_is_immutable_idempotent_and_readable_by_new_adapter(archive, boundary, registry):
    first = snapshot(registry)
    assert archive.snapshots.save(first)
    assert archive.snapshots.save(first)
    assert len(boundary.sessions.rows[SourceCaptureRow]) == 1
    later = snapshot(registry, captured_at=NOW + timedelta(days=1))
    archive.snapshots.save(later)
    restarted = SqlSourceStorage(boundary, str(uuid4()), registry)
    assert restarted.snapshots.get(first.source_id) == later
    assert boundary.sessions.rows[SourceCaptureRow][0].captured_at == NOW
    assert len(boundary.sessions.rows[SupplierRow]) == 3  # No reference-data writes.
    assert len(boundary.sessions.rows[SourceRow]) == 1
    stored = boundary.sessions.rows[SourceCaptureRow][0]
    assert stored.fetch_status == "ok"
    assert stored.http_status == 200  # Satisfies CK_captures_http.
    assert stored.valid_from is None and stored.valid_to is None  # Open validity interval.


@pytest.mark.parametrize("mutation", [
    {"canonical_hash": "b" * 64}, {"parser_version": "html-v0"},
    {"source_id": "../../unknown"}, {"source_url": "https://unapproved.example/"},
])
def test_invalid_capture_never_writes(archive, boundary, registry, mutation):
    with pytest.raises(SourceStorageError):
        archive.snapshots.save(snapshot(registry).model_copy(update=mutation))
    assert boundary.sessions.rows[SourceCaptureRow] == []
    assert boundary.sessions.rows[SourceRow] == []


def test_capture_size_is_bounded_in_utf16_without_truncation(archive, boundary, registry):
    text = "😀" * (MAX_CANONICAL_BYTES // 4 + 1)
    with pytest.raises(SourceStorageError):
        archive.snapshots.save(snapshot(registry, text=text))
    assert boundary.sessions.rows[SourceCaptureRow] == []


def test_missing_or_inactive_supplier_fails_without_creating_reference_rows(archive, boundary, registry):
    boundary.sessions.rows[SupplierRow][0].is_active = False
    with pytest.raises(SourceStorageError):
        archive.snapshots.save(snapshot(registry))
    assert not boundary.sessions.rows[SourceRow]


def test_existing_source_identity_is_reused_and_ambiguous_urls_fail(archive, boundary, registry):
    original_id = uuid4()
    source = SourceRow(source_id=original_id, supplier_id=boundary.sessions.rows[SupplierRow][0].supplier_id,
        source_url=registry.get("cez").pages[0].url, source_type="web_page", is_active=True, created_at=NOW)
    boundary.sessions.rows[SourceRow].append(source)
    archive.snapshots.save(snapshot(registry))
    assert boundary.sessions.rows[SourceCaptureRow][0].source_id == original_id
    boundary.sessions.rows[SourceRow].append(SourceRow(source_id=uuid4(), supplier_id=source.supplier_id,
        source_url=source.source_url, source_type="web_page", is_active=True, created_at=NOW))
    with pytest.raises(SourceStorageError):
        archive.snapshots.get("cez_home")


def test_corrupted_archive_is_reported_instead_of_used_as_fallback(archive, boundary, registry):
    archive.snapshots.save(snapshot(registry))
    boundary.sessions.rows[SourceCaptureRow][0].canonical_text = "tampered"
    with pytest.raises(SourceStorageError):
        archive.snapshots.get("cez_home")


@pytest.mark.parametrize("failure_status", ["blocked", "error"])
def test_old_parser_and_failed_captures_are_not_fallbacks(archive, boundary, registry, failure_status):
    archive.snapshots.save(snapshot(registry))
    row = boundary.sessions.rows[SourceCaptureRow][0]
    row.fetch_status = failure_status
    assert archive.snapshots.get("cez_home") is None
    row.fetch_status, row.parser_version = "ok", "html-v0"
    assert archive.snapshots.get("cez_home") is None


def test_baseline_reads_only_active_reviewed_claims_with_matching_provenance(archive, boundary, registry):
    first = snapshot(registry)
    archive.snapshots.save(first)
    baseline, claim = approve(archive, boundary)
    restored = archive.baselines.get(first.source_id)
    assert restored.claims[0]["id"] == str(claim.baseline_claim_id)
    assert restored.claims[0]["quote"] == first.text and restored.approved_at == NOW
    claim.human_reviewed = False
    assert archive.baselines.get(first.source_id).claims == []
    baseline.is_active = False
    assert archive.baselines.get(first.source_id) is None


@pytest.mark.parametrize("corruption", ["hash", "params", "capture_source"])
def test_invalid_baseline_provenance_fails_clearly(archive, boundary, registry, corruption):
    archive.snapshots.save(snapshot(registry))
    baseline, claim = approve(archive, boundary)
    if corruption == "hash":
        baseline.canonical_sha256 = "b" * 64
    elif corruption == "params":
        claim.rule_params_json = "[]"
    else:
        boundary.sessions.rows[SourceCaptureRow][0].source_id = uuid4()
    with pytest.raises(SourceStorageError):
        archive.baselines.get("cez_home")


def test_capture_and_audit_source_writes_are_fenced(archive, boundary, registry):
    value = snapshot(registry)
    boundary.lease_valid = False
    with pytest.raises(LeaseLost):
        archive.snapshots.save(value)
    assert not boundary.sessions.rows[SourceCaptureRow]
    boundary.lease_valid = True
    web = FakeWeb({registry.get("cez").pages[0].url: page_html("Claim")})
    context, _ = make_context(web)
    outcome = context.scout.with_storage(archive).collect("cez", registry.get("cez").pages[0])
    association = boundary.sessions.rows[AuditSourceRow][0]
    assert association.origin == "live" and association.fetch_was_live
    boundary.lease_valid = False
    with pytest.raises(LeaseLost):
        archive.record(outcome.source)
    assert len(boundary.sessions.rows[AuditSourceRow]) == 1


def test_unchanged_baseline_and_changed_source_keep_separate_capture_history(archive, boundary, registry):
    page = registry.get("cez").pages[0]
    ctx, _ = make_context(FakeWeb({page.url: page_html("Approved claim")}))
    scout = ctx.scout.with_storage(archive)
    first = scout.collect("cez", page)
    baseline, _ = approve(archive, boundary)
    reused = scout.collect("cez", page)
    assert reused.source.origin is Origin.BASELINE
    association = boundary.sessions.rows[AuditSourceRow][0]
    assert association.baseline_id == baseline.baseline_id
    assert len(boundary.sessions.rows[AuditSourceRow]) == 1
    assert reused.baseline_claims[0]["quote"] == first.text
    changed_ctx, _ = make_context(FakeWeb({page.url: page_html("Changed claim")}))
    changed = changed_ctx.scout.with_storage(archive).collect("cez", page)
    assert changed.source.origin is Origin.LIVE and changed.baseline_claims is None
    assert association.baseline_id is None
    assert len(boundary.sessions.rows[SourceCaptureRow]) == 3
    assert boundary.sessions.rows[SourceCaptureRow][0].canonical_text == first.text


def test_revoked_approval_cannot_be_recorded_as_baseline(archive, boundary, registry):
    page = registry.get("cez").pages[0]
    ctx, _ = make_context(FakeWeb({page.url: page_html("Approved claim")}))
    scout = ctx.scout.with_storage(archive)
    scout.collect("cez", page)
    baseline, _ = approve(archive, boundary)
    outcome = scout.collect("cez", page)
    baseline.is_active = False
    with pytest.raises(SourceStorageError, match="approval changed"):
        archive.record(outcome.source)


def test_recreated_worker_uses_sql_snapshot_with_its_original_timestamp(boundary, registry):
    first_ctx, memory = make_context(FakeWeb({p.url: page_html("Claim") for p in registry.get("cez").pages}))
    request = AuditCreateRequest(supplier_ids=["cez"], profile_ids=["d02d_2mwh"], mode=AuditMode.LIVE)
    # In-memory audit IDs are opaque strings; this fake substitutes UUIDs for SQL associations.
    first_id = boundary.create_audit(request)
    uuid_id = str(uuid4())
    boundary._records[uuid_id] = boundary._records.pop(first_id)
    boundary._queue = [uuid_id]
    process_next(boundary, first_ctx)
    first = boundary.get_result(uuid_id)
    assert first is not None and all(s.origin is Origin.LIVE for s in first.sources)
    assert memory.get("cez_home") is None  # Injected memory/file stores were replaced.
    second_id = boundary.create_audit(request)
    second_uuid = str(uuid4())
    boundary._records[second_uuid] = boundary._records.pop(second_id)
    boundary._queue = [second_uuid]
    restarted_ctx, _ = make_context(FakeWeb({}))
    process_next(boundary, restarted_ctx)
    second = boundary.get_result(second_uuid)
    assert second is not None and all(s.origin is Origin.SNAPSHOT for s in second.sources)
    assert [s.captured_at for s in second.sources] == [s.captured_at for s in first.sources]
    assert all(s.fallback_reason for s in second.sources)
    assert len(boundary.sessions.rows[SourceCaptureRow]) == 2
    assert len(boundary.sessions.rows[AuditSourceRow]) == 4


def test_storage_outage_is_not_disguised_as_a_source_failure(boundary, registry, caplog):
    ctx, _ = make_context(FakeWeb({p.url: page_html("Claim") for p in registry.get("cez").pages}))
    request = AuditCreateRequest(supplier_ids=["cez"], profile_ids=["d02d_2mwh"], mode="live")
    audit_id = boundary.create_audit(request)

    def unavailable(*args):
        raise OperationalError("private SQL", {}, Exception("PASSWORD=hidden"))

    boundary._owned = unavailable
    process_next(boundary, ctx)
    assert boundary.get_result(audit_id) is None
    assert boundary.get_status(audit_id).error.code == "PIPELINE_ERROR"
    assert "hidden" not in caplog.text


def test_source_lock_compiles_for_sql_server(archive, boundary, registry):
    archive.snapshots.save(snapshot(registry))
    compiled = [str(statement.compile(dialect=mssql.dialect())) for statement in boundary.sessions.statements]
    assert any("ci.suppliers WITH (UPDLOCK, HOLDLOCK)" in sql for sql in compiled)


def test_confirmed_success_status_is_the_default_without_environment(tmp_path, monkeypatch, registry):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SOURCE_CAPTURE_SUCCESS_STATUS", raising=False)
    assert Settings.from_env().source_capture_success_status == "ok"
    repo = SqlAuditRepository(SourceSessions())
    storage = repo.source_storage(str(uuid4()), registry)
    assert isinstance(storage, SqlSourceStorage)
    assert storage.success_status == "ok"


@pytest.mark.parametrize("value", ["", " ", "error", "ERROR", "a" * 21, "blocked", "success"])
def test_invalid_success_status_fails_before_a_database_write(boundary, registry, value):
    with pytest.raises(ValueError):
        SqlSourceStorage(boundary, str(uuid4()), registry, success_status=value)


def test_success_status_comes_from_host_configuration(monkeypatch):
    monkeypatch.setenv("SOURCE_CAPTURE_SUCCESS_STATUS", "ok")
    assert Settings.from_env().source_capture_success_status == "ok"


@pytest.mark.parametrize("value", ["blocked", "error", "success"])
def test_non_success_configuration_is_rejected_before_opening_a_connection(monkeypatch, value):
    monkeypatch.setenv("SOURCE_CAPTURE_SUCCESS_STATUS", value)
    with pytest.raises(ValueError, match="must be 'ok'"):
        Settings.from_env()
    with pytest.raises(ValueError, match="must be 'ok'"):
        SqlAuditRepository(SourceSessions(), source_capture_success_status=value)
