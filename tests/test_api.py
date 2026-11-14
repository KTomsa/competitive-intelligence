"""HTTP API and pages through the Flask test client."""

import importlib.util
from pathlib import Path

from competitive_intelligence.app import create_app


def test_public_latest_works_without_auth(client):
    response = client.get("/api/v1/audits/latest")
    assert response.status_code == 200
    body = response.get_json()
    assert body["mode"] == "fixture" and body["published_at"]
    assert len(body["verdicts"]) == 9


def test_latest_is_404_when_nothing_published():
    client = create_app({"seed_demo_audit": False, "audit_access_code": "x"}).test_client()
    response = client.get("/api/v1/audits/latest")
    assert response.status_code == 404
    assert response.get_json()["error"]["code"] == "NOT_PUBLISHED"


def test_post_without_or_with_wrong_code_is_rejected(client, payload):
    assert client.post("/api/v1/audits", json=payload).status_code == 401
    wrong = client.post("/api/v1/audits", json=payload, headers={"X-Audit-Access-Code": "nope"})
    assert wrong.status_code == 401
    assert wrong.get_json()["error"]["code"] == "UNAUTHORIZED"


def test_post_fails_closed_when_no_code_is_configured(payload):
    client = create_app({"audit_access_code": None}).test_client()
    response = client.post("/api/v1/audits", json=payload, headers={"X-Audit-Access-Code": ""})
    assert response.status_code == 503


def test_authorized_post_returns_202_and_audit_is_readable(client, payload, auth):
    response = client.post("/api/v1/audits", json=payload, headers=auth)
    assert response.status_code == 202
    created = response.get_json()
    assert created["status"] == "queued"

    status = client.get(created["status_url"]).get_json()
    assert status["status"] == "completed" and status["events"]
    results = client.get(created["results_url"])
    assert results.status_code == 200
    assert results.get_json()["audit_id"] == created["audit_id"]
    # A newly run audit is not published automatically.
    assert results.get_json()["published_at"] is None
    assert client.get("/api/v1/audits/latest").get_json()["audit_id"] != created["audit_id"]


def test_access_code_never_appears_in_responses(client, payload, auth):
    created = client.post("/api/v1/audits", json=payload, headers=auth).get_json()
    for url in (created["status_url"], created["results_url"], "/api/v1/audits/latest", "/"):
        assert auth["X-Audit-Access-Code"] not in client.get(url).get_data(as_text=True)


def test_validation_errors(client, auth, payload):
    unknown = client.post(
        "/api/v1/audits", json={**payload, "supplier_ids": ["supplier_real"]}, headers=auth
    )
    assert unknown.status_code == 422
    extra = client.post("/api/v1/audits", json={**payload, "url": "https://evil.example"}, headers=auth)
    assert extra.status_code == 422
    empty = client.post("/api/v1/audits", data="not json", headers=auth)
    assert empty.status_code == 422


def test_live_audit_runs_against_registered_suppliers(client, auth, payload):
    live = {**payload, "mode": "live", "supplier_ids": ["cez", "eon", "innogy"]}
    created = client.post("/api/v1/audits", json=live, headers=auth)
    assert created.status_code == 202
    result = client.get(created.get_json()["results_url"]).get_json()
    assert result["mode"] == "live" and len(result["sources"]) == 8
    assert {s["origin"] for s in result["sources"]} == {"live"}


def test_fixture_supplier_is_rejected_in_live_mode(client, auth, payload):
    response = client.post("/api/v1/audits", json={**payload, "mode": "live"}, headers=auth)
    assert response.status_code == 422


def test_unknown_audit_is_404_json(client):
    for url in ("/api/v1/audits/audit_missing", "/api/v1/audits/audit_missing/results"):
        response = client.get(url)
        assert response.status_code == 404
        assert response.get_json()["error"]["code"] == "AUDIT_NOT_FOUND"


def test_results_of_unfinished_audit_are_409(app, client):
    from competitive_intelligence.schemas import AuditCreateRequest

    repo = app.extensions["fact_or_watt"].repo
    audit_id = repo.create_audit(
        AuditCreateRequest(supplier_ids=["supplier_demo_a"], profile_ids=["d02d_2mwh"])
    )
    response = client.get(f"/api/v1/audits/{audit_id}/results")
    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "AUDIT_NOT_READY"


def test_rate_limit(payload):
    client = create_app({"audit_access_code": "x", "rate_limit_per_minute": 3}).test_client()
    codes = [client.post("/api/v1/audits", json=payload).status_code for _ in range(5)]
    assert codes == [401, 401, 401, 429, 429]


def test_api_errors_have_no_traceback(client):
    response = client.post("/api/v1/audits/latest")
    assert response.status_code == 405
    body = response.get_json()
    assert set(body["error"]) == {"code", "message", "details"}
    assert "Traceback" not in response.get_data(as_text=True)


def test_pages_render_and_escape_audit_id(client):
    index = client.get("/")
    assert index.status_code == 200
    html = index.get_data(as_text=True)
    assert "Demo Energie A" in html and 'lang="cs"' in html
    assert "script-src 'self'" in index.headers["Content-Security-Policy"]

    hostile = client.get("/audits/%22%3E%3Cimg%20src%3Dx%20onerror%3Dalert(1)%3E")
    assert hostile.status_code == 200
    assert "<img src=x" not in hostile.get_data(as_text=True)


def test_static_assets_are_served(client):
    assert client.get("/static/style.css").status_code == 200
    script = client.get("/static/js/app.js")
    assert script.status_code == 200
    assert "innerHTML =" not in script.get_data(as_text=True)


def test_vercel_entrypoint_exposes_flask_app():
    path = Path(__file__).resolve().parents[1] / "src" / "index.py"
    spec = importlib.util.spec_from_file_location("vercel_index", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.app.test_client().get("/").status_code == 200


def test_app_file_runs_directly_with_package_imports(monkeypatch):
    import runpy

    from flask import Flask

    started = []

    def capture_run(app, **options):
        client = app.test_client()
        started.append((options, client.get("/").status_code,
                        client.get("/static/js/app.js").status_code))

    monkeypatch.setattr(Flask, "run", capture_run)
    # Exercise the same __main__/package context as VS Code's Run Python File,
    # without opening a server socket or contacting the production database.
    path = Path(__file__).resolve().parents[1] / "src" / "competitive_intelligence" / "app.py"
    monkeypatch.syspath_prepend(str(path.parents[1]))
    runpy.run_path(str(path), run_name="__main__")
    assert started == [({"debug": False}, 200, 200)]
