"""Celý běh nad uloženými snapshoty, záložní cesta a HTTP rozhraní. Běží bez sítě a bez modelu."""
import pytest

from competitive_intelligence import agents, workflow
from competitive_intelligence.app import create_app
from competitive_intelligence.connectors import eru, http, ote, web


@pytest.fixture(autouse=True)
def no_network_no_llm(monkeypatch):
    def refuse(url):
        raise http.FetchError(f"síť je v testech vypnutá: {url}")

    monkeypatch.setattr(http, "fetch_live", refuse)
    monkeypatch.setenv("CI_USE_LLM", "0")
    monkeypatch.delenv("DEMO_CODE", raising=False)


def test_snapshot_run_produces_traceable_report():
    report = workflow.run(live=False)
    assert report["mode"] == "snapshot"
    assert report["sources"]["eru"]["origin"] == "snapshot"
    claims = [c for s in report["suppliers"] for c in s["claims"]]
    assert claims
    for claim in claims:
        assert claim["verdict"] in agents.VERDICT_LABEL
        assert claim["source_url"].startswith("https://") and claim["captured_at"]
        assert claim["finding"] and claim["limitation"] and claim["action"]
    known_ids = {c["id"] for c in claims}
    assert all(p["claim_id"] in known_ids for p in report["briefing"]["priorities"])


def test_failed_live_fetch_falls_back_to_dated_snapshot_with_warning():
    report = workflow.run(live=True)  # fetch_live v testech vždy selže
    assert report["mode"] == "mixed"
    assert report["sources"]["eru"]["origin"] == "snapshot"
    assert any("snapshot" in w for w in report["warnings"])
    # cena ze snapshotu se nevydává za živě potvrzenou
    assert not any(o["confirmed_live"] for s in report["suppliers"] for o in s["offers"])


def test_prepared_quotes_exist_in_snapshot_pages():
    for supplier in workflow.load_config()["suppliers"]:
        page = web.load(supplier, live=False)
        for claim in supplier["claims"]:
            assert web.contains(page["text"], claim["quote"]), claim["id"]
        for offer in supplier["offers"]:
            assert web.contains(page["text"], offer["page_marker"]), offer["id"]


def test_official_snapshots_parse():
    bands = eru.load(live=False)
    assert eru.select_band(bands["rows"], "D02d", 12, bands["months"][-1]) is not None
    table = ote.load(live=False)
    assert table["ranking"][0][1] > table["ranking"][1][1]
    assert all(count is not None for _, count in table["ranking"])  # prázdná buňka není nula


def test_keyword_discoveries_get_no_verdict():
    report = workflow.run(["bezdodavatele"], live=False)
    supplier = report["suppliers"][0]
    assert all(not c["discovered"] for c in supplier["claims"])
    assert all("verdict" not in c for c in supplier["candidates"])


def test_extractor_drops_quotes_not_on_page(monkeypatch):
    page = {"lines": ["Máme nejlevnější elektřinu v celé republice."],
            "text": "Máme nejlevnější elektřinu v celé republice."}

    def fake_parse(system, user, schema):
        return schema(claims=[
            {"quote": "Máme nejlevnější elektřinu v celé republice.", "rule_id": "price_rank"},
            {"quote": "Vymyšlený citát, který na stránce není.", "rule_id": "price_rank"},
        ])

    monkeypatch.setattr(agents, "_parse", fake_parse)
    claims, info = agents.extract_claims(page, [], use_llm=True)
    assert [c["quote"] for c in claims] == ["Máme nejlevnější elektřinu v celé republice."]


def test_briefing_falls_back_when_model_cites_unknown_claim(monkeypatch):
    items = [c for s in workflow.run(["centropol"], live=False)["suppliers"] for c in s["claims"]]

    def fake_parse(system, user, schema):
        return schema(headline="x", summary="y", priority_claim_ids=["neexistuje"])

    monkeypatch.setattr(agents, "_parse", fake_parse)
    assert agents.write_briefing(items, use_llm=True)["method"].startswith("šablona")


def test_briefing_rejects_numbers_written_by_model(monkeypatch):
    items = [c for s in workflow.run(["centropol"], live=False)["suppliers"] for c in s["claims"]]
    first = items[0]["id"]

    def with_number(system, user, schema):
        return schema(headline="Fixace na 3 měsíce je sporná.", summary="y", priority_claim_ids=[first])

    monkeypatch.setattr(agents, "_parse", with_number)
    assert agents.write_briefing(items, use_llm=True)["method"].startswith("šablona")

    def clean(system, user, schema):
        return schema(headline="Jedno tvrzení stojí za pozornost.", summary="y", priority_claim_ids=[first])

    monkeypatch.setattr(agents, "_parse", clean)
    out = agents.write_briefing(items, use_llm=True)
    assert out["method"].startswith("model")
    assert out["priorities"][0]["why"] == items[0]["finding"]  # nález je z kódu, ne z modelu


def test_api_runs_only_configured_suppliers():
    client = create_app().test_client()
    assert client.get("/").status_code == 200
    ok = client.post("/api/v1/analyze", json={"supplier_ids": ["cez"], "mode": "snapshot"})
    assert ok.status_code == 200 and [s["id"] for s in ok.get_json()["suppliers"]] == ["cez"]
    assert client.post("/api/v1/analyze", json={"supplier_ids": ["https://evil.example"]}).status_code == 502
    assert client.post("/api/v1/analyze", json={"supplier_ids": "cez"}).status_code == 400


def test_api_requires_demo_code_when_configured(monkeypatch):
    monkeypatch.setenv("DEMO_CODE", "tajne")
    client = create_app().test_client()
    assert client.post("/api/v1/analyze", json={"mode": "snapshot"}).status_code == 403
    assert client.post("/api/v1/analyze", json={"mode": "snapshot", "code": "tajne"}).status_code == 200


def test_vercel_entrypoint_imports():
    import importlib

    assert importlib.import_module("index").app is not None


def test_fetch_refuses_hosts_outside_allowlist(monkeypatch):
    monkeypatch.undo()
    with pytest.raises(http.FetchError):
        http.fetch_live("https://example.com/")
    with pytest.raises(http.FetchError):
        http.fetch_live("http://eru.gov.cz/ipnc")
