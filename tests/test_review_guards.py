"""Testy k nálezům code review (15. 11. 2026): pokrytí zdrojů, časový limit, přesměrování,
vstupy API a ochrana veřejného dema. Běží bez sítě."""
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from competitive_intelligence import app as app_module
from competitive_intelligence import workflow
from competitive_intelligence.app import create_app
from competitive_intelligence.connectors import http, pricelist


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def refuse(url):
        raise http.FetchError(f"síť je v testech vypnutá: {url}")

    monkeypatch.setattr(http, "fetch_live", refuse)
    monkeypatch.setenv("CI_USE_LLM", "0")
    for name in ("DEMO_CODE", "VERCEL", "PUBLIC_DEMO", "AUDIT_DEADLINE_S"):
        monkeypatch.delenv(name, raising=False)


# ---------- OPS-001: manifest očekávaných zdrojů ----------

def _price_lists_live_except(failing_id):
    original = pricelist.load

    def fake(offer, live=True):
        if offer["id"] == failing_id:
            return None if not (offer.get("price_list") or {}).get("url") else _raise(offer)
        doc = original(offer, live=False)
        if doc is not None:
            doc["source"]["origin"], doc["source"]["warning"] = "live", None
        return doc

    def _raise(offer):
        raise http.FetchError(f"ceník {offer['id']} nejde stáhnout a snapshot chybí")

    return fake


def test_failed_expected_source_makes_run_not_live(monkeypatch):
    monkeypatch.setattr(pricelist, "load", _price_lists_live_except("cez-fix36"))
    report = workflow.run(["cez"], live=True)
    entry = next(m for m in report["manifest"] if m["id"] == "pricelist:cez-fix36")
    assert entry["state"] == "missing"
    assert report["mode"] != "live" and report["coverage"]["missing"] >= 1
    assert any("cez-fix36" in w or "Fix na 3 roky" in w for w in report["warnings"])


def test_manifest_lists_every_expected_source_once():
    report = workflow.run(live=False)
    ids = [m["id"] for m in report["manifest"]]
    assert len(ids) == len(set(ids))
    config = workflow.load_config()
    expected = 3 + 2 * len(config["suppliers"]) + sum(len(s["offers"]) for s in config["suppliers"])
    assert len(ids) == expected
    assert {m["state"] for m in report["manifest"]} <= {"snapshot", "not_required", "missing"}
    manual = [m for m in report["manifest"] if m["state"] == "not_required"]
    assert manual and all("týmem" in m["note"] for m in manual)
    assert len(report["warnings"]) <= 2  # běh ze zálohy nehlásí každý zdroj zvlášť


def test_changed_price_list_is_not_promoted_to_confirmed(monkeypatch):
    original = pricelist.load

    def changed(offer, live=True):
        doc = original(offer, live=False)
        if doc is None:
            return None
        doc["source"]["origin"] = "live"
        if offer["id"] == "cez-fix36":
            doc["text"] += " Nová poznámka v ceníku."
            doc["source"]["text_sha256"] = "jiny-otisk"
        return doc

    monkeypatch.setattr(pricelist, "load", changed)
    report = workflow.run(["cez"], live=True)
    offers = {o["id"]: o for o in report["suppliers"][0]["offers"]}
    assert offers["cez-fix36"]["document_status"] == "changed"
    assert not offers["cez-fix36"]["confirmed_live"] and not offers["cez-fix36"]["stale"]
    assert offers["cez-online12"]["confirmed_live"]


# ---------- OPS-002: jeden časový limit pro celý audit ----------

class _SlowHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - jméno dané knihovnou
        time.sleep(3)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"pozde")

    def log_message(self, *args):
        pass


@pytest.fixture
def slow_server(monkeypatch):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _SlowHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(http, "_check_host", lambda url: None)  # povolit lokální server
    yield f"http://127.0.0.1:{server.server_address[1]}/"
    server.shutdown()


def test_fetch_stops_at_audit_deadline(monkeypatch, slow_server):
    monkeypatch.undo()  # skutečné fetch_live, ale bez sítě: jen lokální pomalý server
    monkeypatch.setattr(http, "_check_host", lambda url: None)
    token = http.DEADLINE.set(time.monotonic() + 1.5)
    try:
        started = time.monotonic()
        with pytest.raises(http.FetchError):
            http.fetch_live(slow_server)
        assert time.monotonic() - started < 2.5
    finally:
        http.DEADLINE.reset(token)


def test_no_new_request_after_deadline(monkeypatch):
    monkeypatch.undo()
    token = http.DEADLINE.set(time.monotonic() - 1)
    try:
        with pytest.raises(http.DeadlineExceeded):
            http.fetch_live("https://eru.gov.cz/ipnc")
    finally:
        http.DEADLINE.reset(token)


def test_whole_audit_returns_within_budget_with_slow_sources(monkeypatch):
    monkeypatch.setenv("AUDIT_DEADLINE_S", "5")

    def slow(url):
        left = http.remaining()
        time.sleep(max(0.0, min(left or 0, 30)))  # pomalý zdroj spotřebuje celý zbývající čas
        raise http.DeadlineExceeded("vyčerpán čas")

    monkeypatch.setattr(http, "fetch_live", slow)
    started = time.monotonic()
    report = workflow.run(live=True)
    assert time.monotonic() - started < 8
    assert report["mode"] == "mixed" and report["coverage"]["snapshot"] > 0
    assert report["deadline_s"] == 5


# ---------- SEC-001: přesměrování se kontroluje před odesláním ----------

def test_redirect_to_unapproved_host_is_never_requested(monkeypatch):
    monkeypatch.undo()
    requested = []

    def handler(request):
        requested.append(request.url.host)
        if request.url.host == "eru.gov.cz":
            return httpx.Response(302, headers={"location": "https://evil.example/x"})
        return httpx.Response(200, text="nemělo se volat")

    monkeypatch.setattr(http, "TRANSPORT", httpx.MockTransport(handler))
    with pytest.raises(http.FetchError):
        http.fetch_live("https://eru.gov.cz/ipnc")
    assert requested == ["eru.gov.cz"]


def test_redirect_chain_back_to_approved_host_still_blocked(monkeypatch):
    monkeypatch.undo()
    requested = []

    def handler(request):
        requested.append(str(request.url))
        if request.url.path == "/start":
            return httpx.Response(301, headers={"location": "http://eru.gov.cz/plain"})  # bez HTTPS
        return httpx.Response(200, text="ok")

    monkeypatch.setattr(http, "TRANSPORT", httpx.MockTransport(handler))
    with pytest.raises(http.FetchError):
        http.fetch_live("https://eru.gov.cz/start")
    assert requested == ["https://eru.gov.cz/start"]


def test_approved_redirect_is_followed(monkeypatch):
    monkeypatch.undo()

    def handler(request):
        if request.url.path == "/old":
            return httpx.Response(302, headers={"location": "/new"})
        return httpx.Response(200, text="obsah")

    monkeypatch.setattr(http, "TRANSPORT", httpx.MockTransport(handler))
    assert http.fetch_live("https://eru.gov.cz/old").content == b"obsah"


# ---------- QUAL-007: vstupy API ----------

@pytest.mark.parametrize("body", [
    {"supplier_ids": ["cez"], "perspective": ["cez"]},
    {"supplier_ids": ["cez"], "mode": "snapshoot"},
    {"supplier_ids": ["cez"], "extra": 1},
    {"supplier_ids": ["cez"] * 31},
    {"supplier_ids": ["cez"], "code": 5},
])
def test_invalid_requests_return_400_before_any_work(monkeypatch, body):
    monkeypatch.setattr(workflow, "run", lambda *a, **k: pytest.fail("audit se neměl spustit"))
    response = create_app().test_client().post("/api/v1/analyze", json=body)
    assert response.status_code == 400 and response.is_json


def test_unicode_access_code_is_compared_safely(monkeypatch):
    monkeypatch.setenv("DEMO_CODE", "tajné")
    client = create_app().test_client()
    body = {"supplier_ids": ["cez"], "mode": "snapshot"}
    assert client.post("/api/v1/analyze", json={**body, "code": "žluťoučký"}).status_code == 403
    assert client.post("/api/v1/analyze", json={**body, "code": "tajné"}).status_code == 200


# ---------- SEC-002: omezení veřejného dema ----------

def test_public_deployment_requires_code_for_live_audit(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    client = create_app().test_client()
    assert client.post("/api/v1/analyze", json={"supplier_ids": ["cez"], "mode": "live"}).status_code == 403
    assert client.post("/api/v1/analyze", json={"supplier_ids": ["cez"], "mode": "snapshot"}).status_code == 200


def test_body_size_is_limited():
    big = {"supplier_ids": ["cez"], "code": "x" * 20000}
    response = create_app().test_client().post("/api/v1/analyze", json=big)
    assert response.status_code == 413 and response.is_json


def test_live_audits_are_rate_limited_per_address(monkeypatch):
    monkeypatch.setattr(app_module, "LIVE_AUDITS_PER_WINDOW", 2)
    monkeypatch.setattr(workflow, "run", lambda *a, **k: {"ok": True})
    client = create_app().test_client()
    codes = [client.post("/api/v1/analyze", json={"supplier_ids": ["cez"]}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_concurrent_audits_are_capped(monkeypatch):
    monkeypatch.setattr(app_module, "MAX_CONCURRENT_AUDITS", 1)
    release = threading.Event()

    def slow_run(*args, **kwargs):
        release.wait(5)
        return {"ok": True}

    monkeypatch.setattr(workflow, "run", slow_run)
    app = create_app()
    results = []
    first = threading.Thread(target=lambda: results.append(
        app.test_client().post("/api/v1/analyze", json={"supplier_ids": ["cez"], "mode": "snapshot"}).status_code))
    first.start()
    time.sleep(0.3)
    second = app.test_client().post("/api/v1/analyze", json={"supplier_ids": ["cez"], "mode": "snapshot"})
    release.set()
    first.join(5)
    assert second.status_code == 429 and results == [200]
