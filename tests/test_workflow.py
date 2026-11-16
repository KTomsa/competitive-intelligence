"""Celý běh nad uloženými snapshoty, záložní cesta a HTTP rozhraní. Běží bez sítě a bez modelu."""
import pytest

from competitive_intelligence import agents, workflow
from competitive_intelligence.app import create_app
from competitive_intelligence.connectors import eru, eru_decisions, http, ote, pricelist, web


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


def test_model_can_only_reorder_priorities_never_write_text(monkeypatch):
    # recenzent: věta „konkurent lže“ od modelu nesmí projít; model už text vůbec nepíše
    items = [c for s in workflow.run(live=False)["suppliers"] for c in s["claims"]]
    template = agents.write_briefing(items, use_llm=False)
    candidates = [p["claim_id"] for p in template["priorities"]]
    assert len(candidates) >= 2

    def reorder(system, user, schema):
        return schema.model_validate({"priority_claim_ids": list(reversed(candidates)),
                                      "headline": "Konkurent lže.", "summary": "Konkurent lže."})

    monkeypatch.setattr(agents, "_parse", reorder)
    out = agents.write_briefing(items, use_llm=True)
    assert out["method"].startswith("model")
    assert [p["claim_id"] for p in out["priorities"]] == list(reversed(candidates))
    assert "lže" not in out["headline"] + out["summary"]
    assert out["summary"] == template["summary"]
    by_id = {i["id"]: i for i in items}
    assert all(p["why"] == by_id[p["claim_id"]]["finding"] for p in out["priorities"])

    def outside(system, user, schema):  # tvrzení, které kód nevybral jako kandidáta
        unverified = next(i["id"] for i in items if not i["opportunity"])
        return schema.model_validate({"priority_claim_ids": [unverified]})

    monkeypatch.setattr(agents, "_parse", outside)
    assert agents.write_briefing(items, use_llm=True)["method"].startswith("šablona")


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
    # Dobrá Energie + epet: „více než 185 000 odběrných míst“ – metrika i komodity sedí, lze potvrdit
    assert claims["dobra-2"]["verdict"] == "supported"
    # ČEZ „největší dodavatel“ neuvádí metriku: počty odběrných míst jsou jen kontext
    assert claims["cez-1"]["verdict"] == "unverifiable"
    assert any("Plyn" in sub["text"] and "2. místo" in sub["status"] for sub in claims["cez-1"]["subclaims"])
    # PRE: prohlášení dodavatele k produktu je nález, ne nezávislý důkaz
    assert claims["pre-1"]["verdict"] == "unverifiable" and "prohlášení dodavatele" in claims["pre-1"]["finding"]
    # počty zákazníků nelze potvrdit počtem odběrných míst
    assert claims["eon-2"]["verdict"] == claims["mnd-1"]["verdict"] == "unverifiable"
    assert claims["armex-1"]["opportunity"]  # tvrzení je nad počtem odběrných míst v každém měsíci
    assert claims["spp-1"]["verdict"] == "unverifiable" and "více zemích" in claims["spp-1"]["finding"]
    # „největší alternativní“ a „největší český“ nemají v datech oporu: kontext, ne verdikt
    assert claims["dobra-1"]["verdict"] == "unverifiable" and "CENTROPOL" in claims["dobra-1"]["finding"]
    assert claims["centropol-2"]["verdict"] == "unverifiable" and "vlastnictví" in claims["centropol-2"]["finding"]
    # slogan o důvěře: verdikt se nevydává, ale nález uvádí pravomocné rozhodnutí ERÚ
    assert claims["cez-3"]["verdict"] == "unverifiable" and "pravomocné rozhodnutí" in claims["cez-3"]["finding"]
    assert "nenašli" in claims["epet-2"]["finding"]
    # garance dorovnání ceny není tvrzení o prvenství
    assert claims["centropol-1"]["verdict"] == "unverifiable"
    # žádný nález nevydává pásmo ERÚ za změřené pořadí na trhu
    assert not any("čtvrtin" in c["finding"] for c in claims.values())


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


def test_company_perspective_answers_four_questions_from_snapshots():
    report = workflow.run(live=False, perspective="centropol")
    view = report["perspective"]
    assert view["supplier"] == "Centropol"
    # 1. cena: u každé fixace je vidět, kdo je levnější a o kolik
    by_months = {p["fixation_months"]: p for p in view["price"]}
    assert [c["supplier"] for c in by_months[36]["cheaper"]] == ["ČEZ Prodej"]
    assert round(by_months[36]["cheaper"][0]["saving"][0]) == 317
    assert [c["supplier"] for c in by_months[24]["cheaper"]] == ["ARMEX ENERGY", "E.ON"]
    # 2. vlastní napadnutelná tvrzení
    assert {c["id"] for c in view["risky"]} == {"centropol-1", "centropol-2"}
    # 3. doložitelná tvrzení mají zdroj a výhradu a neobsahují zanedbatelné rozdíly
    assert view["provable"] and all(c["source"] and c["caveat"] for c in view["provable"])
    assert any("Rosteme" in c["text"] for c in view["provable"])
    assert not any("o 25 Kč" in c["text"] for c in view["provable"])
    # 4. konkurence: tvrzení, která se nás týkají, a dražší ceny pro stávající zákazníky jinde
    kinds = {w["kind"] for w in view["watch"]}
    assert {"price", "claim", "loyalty", "growth"} <= kinds
    assert any(w["kind"] == "claim" and w["supplier"] == "Dobrá Energie" for w in view["watch"])


def test_market_overview_and_loyalty_gap_from_price_lists():
    report = workflow.run(live=False)
    assert report["perspective"] is None
    market = report["market"]
    rows = {r["id"]: r for r in market["rows"]}
    assert rows["cez"]["price"]["rank"] == 1 and rows["cez"]["electricity"]["abs"] < 0
    assert rows["epet"]["electricity"]["pct"] > 0
    assert market["observations"][-1].startswith("Ve vzorku tedy") and "příčin" in market["observations"][-1]
    assert market["market_cohort"]["included"] > 50
    armex = rows["armex"]  # nemá tříletý produkt: cena se bere z nejdelší fixace, kterou nabízí
    assert armex["price"]["fixation_months"] == 24 and armex["price"]["rank"] == 1
    assert armex["electricity"]["abs"] < 0
    gaps = {(g["supplier_id"], g["fixation_months"]): g for g in market["loyalty_gaps"]}
    cez = gaps[("cez", 36)]
    assert (cez["new_price"], cez["existing_price"], cez["pct"]) == (3250.0, 3630.0, 11.7)
    assert cez["annual_diff"] == [760.0, 1520.0] and cez["inferred"] is False
    assert gaps[("eon", 36)]["inferred"] is True  # u E.ON je rozdělení jen odvozené z názvů


def test_own_loyalty_gap_is_a_caution_not_an_opportunity():
    view = workflow.run(live=False, perspective="cez")["perspective"]
    assert any("stávající zákazníci platí víc" in c for c in view["cautions"])
    assert not any(w["kind"] == "loyalty" and "ČEZ Prodej" in w["text"] for w in view["watch"])
    assert any("Růst u elektřiny tvrdit nelze" in c for c in view["cautions"])


def test_api_validates_perspective():
    client = create_app().test_client()
    body = {"supplier_ids": ["cez"], "mode": "snapshot"}
    ok = client.post("/api/v1/analyze", json={**body, "perspective": "centropol"})
    assert ok.status_code == 200 and ok.get_json()["perspective"]["id"] == "centropol"
    assert client.post("/api/v1/analyze", json={**body, "perspective": "neznamy"}).status_code == 400
    assert client.post("/api/v1/analyze", json=body).get_json()["perspective"] is None


def test_eru_decisions_from_snapshot_are_classified():
    config = {s["id"]: s for s in workflow.load_config()["suppliers"]}
    armex = eru_decisions.load(config["armex"], live=False)
    assert armex["source"]["origin"] == "snapshot"
    assert [d["kind"] for d in armex["decisions"]] == ["practice", "practice", "practice"]
    assert all(d["url"].startswith("https://eru.gov.cz/") and d["date"] for d in armex["decisions"])
    pre = eru_decisions.load(config["pre"], live=False)
    assert any(d["kind"] == "discontinued" for d in pre["decisions"])  # zastavené řízení není přestupek
    assert eru_decisions.load(config["epet"], live=False)["decisions"] == []
    assert eru_decisions.classify("X - rozhodnutí ve sporu", []) == "dispute"


def test_report_lists_decisions_and_counts_them_in_market_overview():
    report = workflow.run(live=False, perspective="centropol")
    rows = {r["id"]: r for r in report["market"]["rows"]}
    assert rows["armex"]["decisions"] == {"offences": 3, "practices": 3, "latest_practice": "01.04.2026"}
    assert rows["epet"]["decisions"]["offences"] == 0
    assert report["sources"]["eru_decisions"]["origin"] == "snapshot"
    armex = next(s for s in report["suppliers"] if s["id"] == "armex")
    assert len(armex["decisions"]) == 3
    view = report["perspective"]
    assert any(w["kind"] == "regulator" and "ARMEX ENERGY 3" in w["text"] for w in view["watch"])
    assert any("o našich přestupcích" in c for c in view["cautions"])


def test_vercel_entrypoint_imports():
    import importlib

    assert importlib.import_module("index").app is not None


def test_fetch_refuses_hosts_outside_allowlist(monkeypatch):
    monkeypatch.undo()
    with pytest.raises(http.FetchError):
        http.fetch_live("https://example.com/")
    with pytest.raises(http.FetchError):
        http.fetch_live("http://eru.gov.cz/ipnc")
