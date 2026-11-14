"""Release boundaries: fail closed, bounded admission and safe failure responses."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError

from competitive_intelligence.app import create_app
from competitive_intelligence.config import Settings
from competitive_intelligence.schemas import AuditCreateRequest, AuditResult
from competitive_intelligence.schemas.claim import require_https
from competitive_intelligence.services import InMemoryAuditRepository, load_demo_fixture
from competitive_intelligence.services.admission import AdmissionLimits, AdmissionRejected
from competitive_intelligence.services.sql_repository import SqlAuditRepository
from competitive_intelligence.web.security import RateLimiter
from competitive_intelligence.worker import process_next

from .test_persistence import QueuedRepository


@pytest.mark.parametrize("overrides", [
    {"environment": "production"},
    {"environment": "production", "database_url": "configured", "audit_access_code": "change-me"},
    {"environment": "production", "database_url": "configured", "audit_access_code": "long-enough-access-code"},
    {"rate_limit_per_minute": 0}, {"audit_launches_per_day": 0}, {"max_content_length": 0},
])
def test_invalid_release_configuration_fails_closed(overrides):
    with pytest.raises(ValueError):
        Settings(**overrides)


def test_production_host_validation_and_https_headers(auth):
    client = create_app({"environment": "production", "database_url": "configured",
                         "audit_access_code": auth["X-Audit-Access-Code"],
                         "trusted_hosts": ("preview.example.org",)}, repository=QueuedRepository()).test_client()
    assert client.get("/", base_url="https://attacker.example.org").status_code == 400
    response = client.get("/", base_url="https://preview.example.org")
    assert response.status_code == 200
    assert response.headers["Strict-Transport-Security"] == "max-age=31536000"
    assert response.headers["Cache-Control"] == "no-store"


def test_oversized_body_is_rejected_before_repository_write(client, auth):
    response = client.post("/api/v1/audits", data='{"x":"' + "x" * 20000 + '"}',
                           content_type="application/json", headers=auth)
    assert response.status_code == 413
    assert response.get_json()["error"]["code"] == "REQUEST_ENTITY_TOO_LARGE"


def test_duplicate_selection_is_rejected(client, payload, auth):
    payload["supplier_ids"] *= 2
    assert client.post("/api/v1/audits", json=payload, headers=auth).status_code == 422


def test_unexpected_error_is_redacted_in_response_and_log(caplog):
    class Broken(QueuedRepository):
        def get_latest_published(self):
            raise RuntimeError("PASSWORD=should-never-appear")

    response = create_app(repository=Broken()).test_client().get("/api/v1/audits/latest")
    assert response.status_code == 500
    assert response.get_json()["error"]["code"] == "INTERNAL_ERROR"
    assert "should-never-appear" not in response.text + caplog.text
    assert response.headers["X-Request-ID"] in caplog.text


def test_database_health_is_separate_from_liveness():
    class Broken(QueuedRepository):
        def check_ready(self):
            raise OperationalError("SQL secret", {}, Exception("password"))

    client = create_app(repository=Broken()).test_client()
    assert client.get("/health/live").status_code == 200
    ready = client.get("/health/ready")
    assert ready.status_code == 503
    assert "password" not in ready.text
    assert ready.headers["Cache-Control"] == "no-store"


def test_request_ids_are_generated_not_echoed(client):
    first = client.get("/health/live", headers={"X-Request-ID": "untrusted"})
    second = client.get("/health/live")
    assert len(first.headers["X-Request-ID"]) == 32
    assert first.headers["X-Request-ID"] != second.headers["X-Request-ID"]


def test_quota_survives_recreated_web_app_and_ip_changes(payload, auth):
    repo = QueuedRepository()
    config = {"audit_access_code": auth["X-Audit-Access-Code"], "audit_launches_per_minute": 1}
    one = create_app(config, repository=repo).test_client()
    assert one.post("/api/v1/audits", json=payload, headers=auth).status_code == 202
    two = create_app(config, repository=repo).test_client()
    rejected = two.post("/api/v1/audits", json=payload, headers=auth,
                        environ_overrides={"REMOTE_ADDR": "192.0.2.10"})
    assert rejected.status_code == 429
    assert rejected.headers["Retry-After"] == "60"
    assert len(repo._records) == 1


def test_admission_is_atomic_under_concurrent_launches():
    repo = InMemoryAuditRepository()
    request = AuditCreateRequest(supplier_ids=["supplier_demo_a"], profile_ids=["d02d_2mwh"])
    limits = AdmissionLimits(2, 10, 2)

    def launch(_):
        try:
            return repo.create_audit(request, limits=limits)
        except AdmissionRejected:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(launch, range(16)))
    assert sum(item is not None for item in results) == 2
    assert len(repo._queue) == 2


def test_daily_and_pending_quotas_and_window_expiration(monkeypatch):
    import competitive_intelligence.services.repository as module

    repo = InMemoryAuditRepository()
    request = AuditCreateRequest(supplier_ids=["supplier_demo_a"], profile_ids=["d02d_2mwh"])
    first = repo.create_audit(request)
    with pytest.raises(AdmissionRejected):
        repo.create_audit(request, limits=AdmissionLimits(100, 100, 1))
    repo.fail(first, "TEST", "failure")
    with pytest.raises(AdmissionRejected) as error:
        repo.create_audit(request, limits=AdmissionLimits(100, 1, 100))
    assert error.value.retry_after == 86400
    future = datetime.now(UTC) + timedelta(days=2)
    monkeypatch.setattr(module, "utc_now", lambda: future)
    assert repo.create_audit(request, limits=AdmissionLimits(1, 1, 1))


def test_limiter_bounds_client_memory_and_expires_windows(monkeypatch):
    import competitive_intelligence.web.security as module

    clock = [0.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    limiter = RateLimiter(1, max_clients=2)
    assert limiter.allow("one") and limiter.allow("two")
    assert not limiter.allow("three") and not limiter.allow("one")
    clock[0] = 60
    assert limiter.allow("three")
    assert len(limiter._hits) == 1


@pytest.mark.parametrize("outcomes,allowed,retry_after", [
    ([0, 0, 0, 0], True, None),
    ([-1], False, 60),
    ([0, 30], False, 86400),
    ([0, 0, 5, 0], False, 60),
    ([0, 0, 0, 10], False, 60),
])
def test_sql_admission_fails_before_insert_and_uses_transaction_lock(outcomes, allowed, retry_after):
    calls, rows = [], []

    class Session:
        def scalar(self, statement, params=None):
            calls.append((str(statement), params))
            return outcomes.pop(0)

        def add(self, row):
            rows.append(row)

        def flush(self):
            pass

    class Sessions:
        @contextmanager
        def begin(self):
            yield Session()

    repo = SqlAuditRepository(Sessions())
    repo._now = lambda _: datetime.now(UTC)
    request = AuditCreateRequest(supplier_ids=["supplier_demo_a"], profile_ids=["d02d_2mwh"])
    if allowed:
        assert repo.create_audit(request, limits=AdmissionLimits(5, 30, 10))
        assert len(rows) == 3
    else:
        with pytest.raises(AdmissionRejected) as error:
            repo.create_audit(request, limits=AdmissionLimits(5, 30, 10))
        assert error.value.retry_after == retry_after and not rows
    assert "sp_getapplock" in calls[0][0] and "@LockOwner='Transaction'" in calls[0][0]
    assert calls[0][1] == {"resource": "fact-or-watt:audit-admission:v1"}


@pytest.mark.parametrize("url", ["https://user:secret@example.org/", "https://", "https://example.org:8443/",
                                     "https://example.org/\nattack", "https://example.org\\@evil.org/", "http://example.org"])
def test_public_source_urls_cannot_embed_credentials_or_invalid_authorities(url):
    with pytest.raises(ValueError):
        require_https(url)


@pytest.mark.parametrize("mutation", ["claim_source", "missing_evidence", "duplicate_verdict", "briefing", "matrix"])
def test_incompatible_public_results_cannot_be_approved(mutation):
    repo = InMemoryAuditRepository()
    bundle = load_demo_fixture()
    audit_id = repo.create_audit(AuditCreateRequest(supplier_ids=bundle.supplier_ids, profile_ids=list(bundle.profiles)))
    process_next(repo, bundle)
    result = repo.get_result(audit_id).model_dump(mode="json")
    if mutation == "claim_source":
        result["claims"][0]["source_id"] = "unrelated"
    elif mutation == "missing_evidence":
        result["evidence"].pop()
    elif mutation == "duplicate_verdict":
        result["verdicts"].append(result["verdicts"][0])
    elif mutation == "briefing":
        result["briefing"]["items"][0]["claim_id"] = "invented"
    else:
        result["briefing"]["matrix"] = {}
    with pytest.raises(ValidationError):
        AuditResult.model_validate(result)
