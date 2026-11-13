"""Opt-in Azure tests. Existing schema only; all application writes roll back.

Run with RUN_AZURE_INTEGRATION=1 and a separate TEST_DATABASE_URL. These tests
never use DATABASE_URL as a write target, create tables or add reference data.
"""

import os
from datetime import timedelta

import pytest
from dotenv import dotenv_values
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from competitive_intelligence.app import create_app
from competitive_intelligence.db import check_schema, create_database_engine
from competitive_intelligence.models import AuditJob
from competitive_intelligence.schemas import AuditCreateRequest, AuditStatus
from competitive_intelligence.services import load_demo_fixture
from competitive_intelligence.services.orchestrator import run_fixture_audit
from competitive_intelligence.services.repository import LeaseLost
from competitive_intelligence.services.sql_repository import SqlAuditRepository, audit_uuid

pytestmark = pytest.mark.azure


@pytest.fixture
def sessions():
    if os.environ.get("RUN_AZURE_INTEGRATION") != "1":
        pytest.skip("Azure integration is opt-in")
    local = dotenv_values(".env")
    test_url = os.environ.get("TEST_DATABASE_URL") or local.get("TEST_DATABASE_URL")
    live_url = os.environ.get("DATABASE_URL") or local.get("DATABASE_URL")
    if not test_url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    target = make_url(test_url)
    if live_url:
        live = make_url(live_url)
        target_identity = ((target.host or "").casefold(), (target.database or "").casefold())
        live_identity = ((live.host or "").casefold(), (live.database or "").casefold())
        if target_identity == live_identity:
            pytest.skip("TEST_DATABASE_URL must identify a different database from DATABASE_URL")
    engine = create_database_engine(test_url)
    try:
        check_schema(engine)
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                yield sessionmaker(connection, expire_on_commit=False, join_transaction_mode="create_savepoint")
            finally:
                transaction.rollback()
    finally:
        engine.dispose()


def request():
    return AuditCreateRequest(supplier_ids=["supplier_demo_a"], profile_ids=["d02d_2mwh"])


def test_azure_enqueue_restart_complete_and_publish(sessions):
    repo = SqlAuditRepository(sessions)
    app = create_app({"audit_access_code": "test-code"}, repository=repo)
    response = app.test_client().post("/api/v1/audits", json=request().model_dump(mode="json"),
                                      headers={"X-Audit-Access-Code": "test-code"})
    assert response.status_code == 202
    audit_id = response.get_json()["audit_id"]
    restarted = SqlAuditRepository(sessions)
    assert restarted.get_status(audit_id).status is AuditStatus.QUEUED
    assert restarted.claim_next_job() == audit_id
    assert SqlAuditRepository(sessions).claim_next_job() is None
    restarted.heartbeat(audit_id)
    result = run_fixture_audit(restarted, audit_id, load_demo_fixture())
    restarted.complete(audit_id, result)  # duplicate delivery is idempotent
    reader = SqlAuditRepository(sessions)
    assert reader.get_status(audit_id).status is AuditStatus.COMPLETED
    assert reader.get_result(audit_id).model_dump() == result.model_dump()
    reader.publish(audit_id, approved_by="integration-test")
    latest = SqlAuditRepository(sessions).get_latest_published()
    assert latest.audit_id == audit_id and latest.published_at
    assert latest.sources and latest.evidence and latest.mode == "fixture"
    assert reader.publish(audit_id).published_at == latest.published_at


def test_azure_expired_lease_fences_old_worker_and_bounds_retries(sessions):
    old = SqlAuditRepository(sessions, max_attempts=2, retry_seconds=0)
    audit_id = old.create_audit(request())
    assert old.claim_next_job() == audit_id
    with sessions.begin() as session:
        job = session.scalar(select(AuditJob).where(AuditJob.audit_id == audit_uuid(audit_id)))
        job.lease_expires_at = old._now(session) - timedelta(seconds=1)
    new = SqlAuditRepository(sessions, retry_seconds=0)
    assert new.claim_next_job() == audit_id
    with pytest.raises(LeaseLost):
        old.set_stage(audit_id, AuditStatus.EXTRACTING, "claim_extractor")
    with pytest.raises(LeaseLost):
        old.fail(audit_id, "STALE", "old delivery")
    new.retry(audit_id, "PIPELINE_ERROR", "test failure")
    assert new.get_status(audit_id).status is AuditStatus.FAILED
    assert new.claim_next_job() is None


def test_azure_retry_and_expired_final_delivery_become_terminal(sessions):
    repo = SqlAuditRepository(sessions, max_attempts=2, retry_seconds=0)
    audit_id = repo.create_audit(request())
    assert repo.claim_next_job() == audit_id
    repo.retry(audit_id, "TEMPORARY", "test failure")
    assert repo.get_status(audit_id).status is AuditStatus.QUEUED
    assert repo.claim_next_job() == audit_id
    with sessions.begin() as session:
        job = session.scalar(select(AuditJob).where(AuditJob.audit_id == audit_uuid(audit_id)))
        job.lease_expires_at = repo._now(session) - timedelta(seconds=1)
    assert SqlAuditRepository(sessions).claim_next_job() is None
    assert repo.get_status(audit_id).error.code == "RETRIES_EXHAUSTED"


def test_azure_source_capture_reused_by_recreated_worker(sessions):
    from competitive_intelligence.config import Settings
    from competitive_intelligence.models import AuditSourceRow, SourceCaptureRow
    from competitive_intelligence.schemas import Origin
    from competitive_intelligence.worker import process_next

    from .conftest import FakeWeb, make_context, page_html

    success_status = Settings.from_env().source_capture_success_status
    ctx, _ = make_context(FakeWeb())
    page = ctx.registry.get("cez").pages[0]
    live_ctx, _ = make_context(FakeWeb({page.url: page_html("Test capture")}))
    repo = SqlAuditRepository(sessions, source_capture_success_status=success_status)
    live_request = AuditCreateRequest(supplier_ids=["cez"], profile_ids=["d02d_2mwh"], mode="live")
    first_id = repo.create_audit(live_request)
    assert process_next(repo, live_ctx) == first_id
    first = repo.get_result(first_id)
    source = next(source for source in first.sources if source.source_id == page.source_id)
    assert source.origin is Origin.LIVE
    with sessions() as session:
        association = session.scalar(select(AuditSourceRow).where(AuditSourceRow.audit_id == audit_uuid(first_id)))
        assert association is not None
        capture = session.get(SourceCaptureRow, association.capture_id)
        assert capture.canonical_text and capture.content_sha256 == source.content_sha256

    restarted = SqlAuditRepository(sessions, source_capture_success_status=success_status)
    second_id = restarted.create_audit(live_request)
    assert process_next(restarted, ctx) == second_id
    second = restarted.get_result(second_id)
    fallback = next(source for source in second.sources if source.source_id == page.source_id)
    assert fallback.origin is Origin.SNAPSHOT and not fallback.fetch_was_live
    assert fallback.captured_at == source.captured_at
    assert fallback.canonical_hash == source.canonical_hash
