"""Persistence wiring, safe failures, worker recovery and SQL Server lock semantics."""

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import mssql
from sqlalchemy.exc import OperationalError

from competitive_intelligence.app import create_app
from competitive_intelligence.config import Settings
from competitive_intelligence.db import create_database_engine
from competitive_intelligence.models import AuditJob, BriefingRow, UTCDateTime
from competitive_intelligence.schemas import AuditCreateRequest, AuditStatus
from competitive_intelligence.services import InMemoryAuditRepository, load_demo_fixture
from competitive_intelligence.services.publication import public_result
from competitive_intelligence.services.repository import AuditNotFound, LeaseLost, StoredAuditError
from competitive_intelligence.services.sql_repository import SqlAuditRepository, audit_uuid, decode_result, locked
from competitive_intelligence.worker import main, process_next


class QueuedRepository(InMemoryAuditRepository):
    # A test boundary that advertises durable enqueue semantics, without a DB.
    durable = True
    lease_seconds = 60


def test_durable_post_queues_without_running_pipeline(payload, auth):
    repo = QueuedRepository()
    app = create_app({"audit_access_code": auth["X-Audit-Access-Code"]}, repository=repo)
    client = app.test_client()
    assert client.get("/api/v1/audits/latest").status_code == 404  # no startup writes
    response = client.post("/api/v1/audits", json=payload, headers=auth)
    assert response.status_code == 202
    created = response.get_json()
    assert client.get(created["status_url"]).get_json()["status"] == "queued"
    assert client.get(created["results_url"]).status_code == 409
    process_next(repo, load_demo_fixture())
    restarted = create_app({"seed_demo_audit": False}, repository=repo).test_client()
    assert restarted.get(created["status_url"]).get_json()["status"] == "completed"
    assert restarted.get(created["results_url"]).status_code == 200
    assert restarted.get("/api/v1/audits/latest").status_code == 404
    repo.publish(created["audit_id"], approved_by="test-operator")
    assert restarted.get("/api/v1/audits/latest").get_json()["published_at"]


def test_durable_live_audit_waits_for_worker_and_explicit_publication(ctx, all_pages_ok, auth):
    repo = QueuedRepository()
    client = create_app(
        {"audit_access_code": auth["X-Audit-Access-Code"]}, repository=repo, context=ctx
    ).test_client()
    response = client.post("/api/v1/audits", headers=auth, json={
        "supplier_ids": ["cez"], "profile_ids": ["d02d_2mwh"], "mode": "live",
    })
    assert response.status_code == 202
    created = response.get_json()
    assert all_pages_ok.requests == []
    assert client.get(created["status_url"]).get_json()["status"] == "queued"
    assert client.get(created["results_url"]).status_code == 409

    assert process_next(repo, ctx) == created["audit_id"]
    restarted = create_app(repository=repo, context=ctx).test_client()
    result = restarted.get(created["results_url"]).get_json()
    assert result["mode"] == "live" and len(result["sources"]) == 2
    assert all(source["origin"] == "live" for source in result["sources"])
    assert all("cez.cz" in url for url in all_pages_ok.page_requests())
    assert result["published_at"] is None
    assert restarted.get("/api/v1/audits/latest").status_code == 404
    repo.publish(created["audit_id"], approved_by="test-operator")
    assert restarted.get("/api/v1/audits/latest").get_json()["audit_id"] == created["audit_id"]


def test_database_error_is_redacted_and_does_not_fall_back():
    class Unavailable(QueuedRepository):
        def get_latest_published(self):
            raise OperationalError("secret SQL", {}, Exception("PASSWORD=secret"))

    client = create_app(repository=Unavailable()).test_client()
    response = client.get("/api/v1/audits/latest")
    assert response.status_code == 503
    assert response.get_json()["error"]["code"] == "DATABASE_UNAVAILABLE"
    assert "secret" not in response.get_data(as_text=True)


def test_database_engine_does_not_connect_at_app_import(monkeypatch):
    import mssql_python

    def refuse(*args, **kwargs):
        raise AssertionError("factory must not connect or seed the database")

    monkeypatch.setattr(mssql_python, "connect", refuse)
    app = create_app({"database_url": "mssql+mssqlpython://user:password@invalid.example/db"})
    assert app.extensions["fact_or_watt"].repo.durable
    assert app.test_client().get("/").status_code == 200
    app.extensions["fact_or_watt"].repo.sessions.kw["bind"].dispose()


@pytest.mark.parametrize("url", [
    "sqlite:///bad.db", "postgresql://user@host/db",
    "mssql+mssqlpython://user@host/db?encrypt=no",
    "mssql+mssqlpython://user@host/db?TrustServerCertificate=yes",
    "not a URL",
])
def test_database_backend_and_tls_fail_closed(url):
    with pytest.raises(ValueError):
        create_database_engine(url)


def test_worker_recovers_pipeline_failure_without_logging_secret(monkeypatch, caplog):
    import competitive_intelligence.worker as worker

    repo = InMemoryAuditRepository()
    audit_id = repo.create_audit(AuditCreateRequest(supplier_ids=["supplier_demo_a"], profile_ids=["d02d_2mwh"]))

    def fail(*args):
        raise RuntimeError("LLM_API_KEY=private-value")

    monkeypatch.setattr(worker, "run_fixture_audit", fail)
    assert process_next(repo, load_demo_fixture()) == audit_id
    assert repo.get_status(audit_id).status is AuditStatus.FAILED
    assert "private-value" not in caplog.text


def test_lost_lease_does_not_fail_someone_elses_delivery(monkeypatch):
    import competitive_intelligence.worker as worker

    repo = InMemoryAuditRepository()
    audit_id = repo.create_audit(AuditCreateRequest(supplier_ids=["supplier_demo_a"], profile_ids=["d02d_2mwh"]))

    def lost(*args):
        raise LeaseLost()

    monkeypatch.setattr(worker, "run_fixture_audit", lost)
    assert process_next(repo, load_demo_fixture()) == audit_id
    assert repo.get_status(audit_id).error is None


def test_claim_uses_sql_server_queue_locks():
    statement = locked(select(AuditJob).limit(1), AuditJob, skip_locked=True)
    sql = str(statement.compile(dialect=mssql.dialect()))
    assert "TOP" in sql and "WITH (UPDLOCK, READPAST, READCOMMITTEDLOCK)" in sql
    assert "ci.audit_jobs" in sql


def test_utc_datetime_round_trip():
    value = datetime(2026, 1, 1, tzinfo=UTC)
    column_type = UTCDateTime()
    stored = column_type.process_bind_param(value, None)
    assert stored.tzinfo is None
    assert column_type.process_result_value(stored, None) == value
    with pytest.raises(ValueError):
        column_type.process_bind_param(stored, None)


def test_invalid_uuid_is_a_not_found_instead_of_driver_failure():
    with pytest.raises(AuditNotFound):
        audit_uuid("audit_missing")


def test_settings_read_dotenv_without_exposing_secrets(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("DATABASE_URL=secret-url\nAUDIT_ACCESS_CODE=secret-code\n", encoding="utf-8")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AUDIT_ACCESS_CODE", raising=False)
    settings = Settings.from_env()
    assert settings.database_url == "secret-url"
    assert settings.audit_access_code == "secret-code"
    assert "secret" not in repr(settings)
    monkeypatch.setenv("DATABASE_URL", "host-override")
    assert Settings.from_env().database_url == "host-override"


def test_worker_requires_database_for_durable_commands(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as error:
        main(["run", "--once"])
    assert error.value.code == 2
    assert "DATABASE_URL is required" in capsys.readouterr().err


@pytest.mark.parametrize("payload", ["not JSON", "[]", '{"version":2}', '{"version":1,"audit_result":{}}'])
def test_incompatible_persisted_snapshots_fail_clearly(payload):
    with pytest.raises(StoredAuditError):
        decode_result(payload, envelope=True)


@pytest.fixture
def owned_job():
    """Fenced lifecycle tests with a session fake; no SQLite or database access."""
    now = datetime(2026, 11, 13, tzinfo=UTC)
    run = SimpleNamespace(audit_id=uuid4(), status="collecting", progress_percent=15)
    audit_id = str(run.audit_id)
    job = SimpleNamespace(status="leased", leased_by="delivery-token",
        lease_expires_at=now + timedelta(seconds=10), attempt_count=1, max_attempts=2)
    events = []

    class Session:
        def scalar(self, statement):
            return job

        def get(self, model, key):
            return run

        def add(self, row):
            events.append(row)

    class Sessions:
        @contextmanager
        def begin(self):
            yield Session()

    repo = SqlAuditRepository(Sessions(), lease_seconds=60, retry_seconds=7)
    repo._leases[audit_id] = "delivery-token"
    repo._now = lambda session: now
    return repo, run, job, events, now, audit_id


@pytest.mark.parametrize("loss", ["expired", "new_owner", "completed", "no_token"])
def test_worker_writes_require_current_unexpired_ownership(owned_job, loss):
    repo, run, job, events, now, audit_id = owned_job
    if loss == "expired":
        job.lease_expires_at = now
    elif loss == "new_owner":
        job.leased_by = "another-delivery"
    elif loss == "completed":
        job.status = "completed"
    else:
        repo._leases.clear()
    for write in (lambda: repo.heartbeat(audit_id),
                  lambda: repo.set_stage(audit_id, AuditStatus.EXTRACTING, "claim_extractor"),
                  lambda: repo.fail(audit_id, "STALE", "stale failure")):
        with pytest.raises(LeaseLost):
            write()
    assert run.status == "collecting" and not events


def test_heartbeat_extends_lease_using_server_clock(owned_job):
    repo, run, job, events, now, audit_id = owned_job
    repo.heartbeat(audit_id)
    assert job.heartbeat_at == now
    assert job.lease_expires_at == now + timedelta(seconds=60)


def test_retry_releases_lease_and_delays_the_next_attempt(owned_job):
    repo, run, job, events, now, audit_id = owned_job
    repo.retry(audit_id, "TEMPORARY", "safe error")
    assert job.status == run.status == "queued"
    assert job.available_at == now + timedelta(seconds=7)
    assert job.leased_by is None and job.lease_expires_at is None
    assert audit_id not in repo._leases
    assert events[-1].severity == "warning"


def test_final_retry_is_terminal_and_has_no_public_result(owned_job):
    repo, run, job, events, now, audit_id = owned_job
    job.attempt_count = 2
    repo.retry(audit_id, "PIPELINE_ERROR", "safe error")
    assert job.status == "dead" and run.status == "failed"
    assert run.finished_at == now and run.error_code == "PIPELINE_ERROR"
    assert job.leased_by is None and audit_id not in repo._leases


@pytest.mark.parametrize("mode", ["fixture", "live"])
def test_sql_completion_preserves_mode_provenance_and_pipeline_metadata(owned_job, ctx, mode):
    repo, run, job, rows, now, audit_id = owned_job
    memory = InMemoryAuditRepository()
    request = AuditCreateRequest(
        supplier_ids=["cez"] if mode == "live" else ["supplier_demo_a"],
        profile_ids=["d02d_2mwh"], mode=mode,
    )
    temporary_id = memory.create_audit(request)
    process_next(memory, ctx)
    result = memory.get_result(temporary_id).model_copy(update={"audit_id": audit_id})
    repo.complete(audit_id, result)
    stored = next(row for row in rows if isinstance(row, BriefingRow))
    restored = decode_result(stored.briefing_json, envelope=True)
    assert restored.model_dump() == result.model_dump()
    assert stored.model_name == ("web-scout-deterministic-v1" if mode == "live"
                                 else "fixture-deterministic-v1")
    assert run.status == job.status == "completed"
    assert audit_id not in repo._leases


def test_worker_fixture_command_runs_offline(capsys):
    import json

    assert main(["fixture"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "completed"
    assert len(result["verdicts"]) == 9


def test_public_snapshot_copies_declared_fields_only():
    repo = InMemoryAuditRepository()
    bundle = load_demo_fixture()
    audit_id = repo.create_audit(AuditCreateRequest(supplier_ids=bundle.supplier_ids, profile_ids=list(bundle.profiles)))
    process_next(repo, bundle)
    result = repo.get_result(audit_id)
    # Simulate private runtime metadata attached after DTO construction.
    result.__dict__["provider_trace"] = "private provider prompt"
    public = public_result(result, datetime.now(UTC))
    assert "provider_trace" not in public.__dict__
    assert "private provider prompt" not in public.model_dump_json()
    public.warnings.append("public-only change")
    assert "public-only change" not in result.warnings


def test_database_engine_sets_explicit_tls_defaults():
    engine = create_database_engine("mssql+mssqlpython://user:password@invalid.example/db")
    try:
        assert engine.url.query["Encrypt"] == "yes"
        assert engine.url.query["TrustServerCertificate"] == "no"
    finally:
        engine.dispose()
