"""Celý běh nad uloženými snapshoty, záložní cesta a HTTP rozhraní. Běží bez sítě a bez modelu."""
import pytest

from competitive_intelligence import agents, workflow
from competitive_intelligence.app import create_app
from competitive_intelligence.connectors import eru, http, ote, pricelist, web


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
        for proof in supplier.get("green_evidence") or []:
            assert web.contains(page["text"], proof["page_marker"]), proof["label"]
        for offer in supplier["offers"]:
            if offer.get("page_marker"):
                assert web.contains(page["text"], offer["page_marker"]), offer["id"]
            document = pricelist.load(offer, live=False)
            if document is not None:  # ceník se stažitelnou adresou musí obsahovat cenu i stálý plat
                assert pricelist.contains_all(document["text"], offer["price_list"]["markers"]), offer["id"]


def test_official_snapshots_parse():
    bands = eru.load(live=False)
    assert eru.select_band(bands["rows"], "D02d", 12, bands["months"][-1]) is not None
    table = ote.load(live=False)
    assert table["ranking"][0][1] > table["ranking"][1][1]
    assert all(count is not None for _, count in table["ranking"])  # prázdná buňka není nula
    gas = ote.load_gas(live=False)
    assert gas["source"]["data_period"].startswith("20") and len(gas["ranking"]) > 10
    # tabulka plynu zkracuje názvy na 20 znaků
    assert ote.same_supplier("innogy Energie, s.r.", "innogy Energie, s.r.o.")
    assert not ote.same_supplier("ČEZ, a. s.", "ČEZ Prodej, a.s.")


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
    for bad in ({"supplier_ids": ["https://evil.example"]}, {"supplier_ids": "cez"}, {"supplier_ids": []},
                {"mode": "snapshot"}, {"supplier_ids": [["cez"]]}):
        assert client.post("/api/v1/analyze", json=bad).status_code == 400, bad
    assert client.post("/api/v1/analyze", data="{oops", content_type="application/json").status_code == 400
    assert client.post("/api/v1/analyze").status_code == 400


def test_api_requires_demo_code_when_configured(monkeypatch):
    monkeypatch.setenv("DEMO_CODE", "tajne")
    client = create_app().test_client()
    body = {"mode": "snapshot", "supplier_ids": ["cez"]}
    assert client.post("/api/v1/analyze", json=body).status_code == 403
    assert client.post("/api/v1/analyze", json={**body, "code": "tajne"}).status_code == 200


def _live_from_snapshot(alter=None):
    """Napodobí živě stažené ceníky pomocí snapshotů; `alter` může text jednoho ceníku změnit."""
    original = pricelist.load

    def fake(offer, live=True):
        document = original(offer, live=False)
        if document is None:
            return None
        document["source"]["origin"], document["source"]["warning"] = "live", None
        if alter:
            document["text"] = alter(offer, document["text"])
        return document

    return fake


def test_price_missing_from_live_price_list_no_longer_decides(monkeypatch):
    def alter(offer, text):
        return text.replace("3 340,00", "3 999,00") if offer["id"] == "cez-online12" else text

    monkeypatch.setattr(pricelist, "load", _live_from_snapshot(alter))
    report = workflow.run(["cez"], live=True)
    offers = {o["id"]: o for o in report["suppliers"][0]["offers"]}
    assert offers["cez-online12"]["stale"] and not offers["cez-online12"]["benchmark"]["eligible"]
    assert offers["cez-fix36"]["confirmed_live"]
    assert any("už neplatí" in w for w in report["warnings"])
    listed = [row["product"] for group in report["comparison"] for row in group["rows"]]
    assert "Elektřina Online na 1 rok" not in listed  # zastaralá nabídka se nesrovnává


def test_real_data_verdicts_from_snapshots():
    report = workflow.run(live=False)
    claims = {c["id"]: c for s in report["suppliers"] for c in s["claims"]}
    # epet: „vždy tu nejlepší cenu“ – existuje levnější srovnatelná nabídka včetně stálého platu
    assert claims["epet-1"]["verdict"] == "contradicted"
    assert "ČEZ Prodej" in claims["epet-1"]["finding"]
    # ČEZ: největší v elektřině a v součtu, v plynu ne
    assert claims["cez-1"]["verdict"] == "partially_supported"
    statuses = [sub["status"] for sub in claims["cez-1"]["subclaims"]]
    assert statuses[0] == "ověřeno" and statuses[1].startswith("v rozporu") and statuses[2] == "ověřeno"
    # PRE: zelené tvrzení s produktovým podkladem
    assert claims["pre-1"]["verdict"] == "supported"
    # počty zákazníků řádově odpovídají součtu odběrných míst elektřiny a plynu
    assert claims["eon-2"]["verdict"] == "partially_supported"
    assert claims["mnd-1"]["verdict"] == "partially_supported"
    # menší dodavatelé: nález s konkrétními čísly, verdikt zůstává opatrný
    assert claims["spp-1"]["verdict"] == "unverifiable" and "Slovensko" in claims["spp-1"]["finding"]
    assert claims["dobra-1"]["verdict"] == "unverifiable" and "CENTROPOL" in claims["dobra-1"]["finding"]
    # garance dorovnání ceny není tvrzení o prvenství
    assert claims["centropol-1"]["verdict"] == "unverifiable"


def test_comparison_is_sorted_by_annual_cost_and_matches_price_lists():
    report = workflow.run(live=False)
    group = report["comparison"][0]
    assert group["fixation_months"] == 36
    costs = [row["annual_cost"][0] for row in group["rows"]]
    assert costs == sorted(costs) and all(c is not None for c in costs)
    cheapest = group["rows"][0]
    assert cheapest["supplier"] == "ČEZ Prodej"
    assert cheapest["annual_cost"] == [12 * 163.35 + 2 * 3250.0, 12 * 163.35 + 4 * 3250.0]
    assert report["sources"]["ote_gas"]["origin"] == "snapshot" and len(report["price_lists"]) >= 9


def test_snapshot_mode_never_calls_model(monkeypatch):
    monkeypatch.setenv("CI_USE_LLM", "1")
    monkeypatch.setenv("GEMINI_API_KEY", "x")

    def boom(*args):
        raise AssertionError("model se v režimu snapshot nesmí volat")

    monkeypatch.setattr(agents, "_parse", boom)
    assert workflow.run(["cez"], live=False)["llm_used"] is False


def test_model_duplicates_of_prepared_claims_are_dropped(monkeypatch):
    page = {"lines": [], "text": "Ke stabilní dodávce energií dáváme Garanci nejnižší ceny. Garance nejnižší ceny"}

    def fake_parse(system, user, schema):
        return schema(claims=[{"quote": "Garance nejnižší ceny", "rule_id": "price_rank"}])

    monkeypatch.setattr(agents, "_parse", fake_parse)
    known = ["Ke stabilní dodávce energií dáváme Garanci nejnižší ceny."]
    assert agents.extract_claims(page, known, use_llm=True)[0] == []


def test_model_cannot_assign_a_rule_the_quote_does_not_support(monkeypatch):
    lines = [
        "90% klientů by nás doporučilo dál",
        "Získejte výhodnější plyn s garancí stálé ceny na celé 3 roky.",
        "Máme nejlevnější elektřinu v celé republice.",
    ]
    page = {"lines": lines, "text": " ".join(lines)}

    def fake_parse(system, user, schema):
        return schema(claims=[
            {"quote": lines[0], "rule_id": "customer_growth"},  # chybná klasifikace modelu
            {"quote": lines[1], "rule_id": "price_guarantee"},   # tvrzení jen o plynu
            {"quote": lines[2], "rule_id": "price_rank"},
        ])

    monkeypatch.setattr(agents, "_parse", fake_parse)
    claims, _ = agents.extract_claims(page, [], use_llm=True)
    assert [(c["quote"], c["rule_id"]) for c in claims] == [(lines[0], "other"), (lines[2], "price_rank")]


def test_vercel_entrypoint_imports():
    import importlib

    assert importlib.import_module("index").app is not None


def test_fetch_refuses_hosts_outside_allowlist(monkeypatch):
    monkeypatch.undo()
    with pytest.raises(http.FetchError):
        http.fetch_live("https://example.com/")
    with pytest.raises(http.FetchError):
        http.fetch_live("http://eru.gov.cz/ipnc")
