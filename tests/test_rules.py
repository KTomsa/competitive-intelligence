"""Pravidla a společná brána důkazů: podložený, vyvrácený a nesrovnatelný případ. Běží bez sítě."""
import copy
from datetime import date

import pytest

from competitive_intelligence import evidence as ev
from competitive_intelligence import rules
from competitive_intelligence.connectors import eru

AUDIT_DAY = date(2026, 11, 16)
BAND_ROWS = [
    {"month": "2026-11", "tariff": "D02d", "fixation_months": 12,
     "low_excl_vat": 3800.0, "high_excl_vat": 3900.0, "low_incl_vat": 4598.0, "high_incl_vat": 4719.0},
    {"month": "2026-11", "tariff": "D02d", "fixation_months": 36,
     "low_excl_vat": 3200.0, "high_excl_vat": 3400.0, "low_incl_vat": 3872.0, "high_incl_vat": 4114.0},
]


def offer(oid, price_excl, fixation=12, tariff="D02d", fee=None, **extra):
    return {
        "id": oid, "product": f"Produkt {oid}", "tariff": tariff, "area": "PRE", "fixation_months": fixation,
        "price_mwh_excl_vat": price_excl,
        "price_mwh_incl_vat": None if price_excl is None else round(price_excl * 1.21, 2),
        "monthly_fee_incl_vat": fee, "confirmed_live": True, "captured_at": "2026-11-13",
        "audience": "new", **extra,
    }


def supplier(sid, offers=(), ote_name=None):
    return {"id": sid, "name": sid.upper(), "ote_name": ote_name or sid, "offers": list(offers),
            "green_evidence": []}


def ote_table(rows: dict, year=2026, latest=None):
    """Tabulka OTE ve stejném tvaru, jaký vrací konektor."""
    if latest is None:
        latest = max(i + 1 for values in rows.values() for i, v in enumerate(values) if v is not None)
    ranking = sorted(((n, v[latest - 1]) for n, v in rows.items() if v[latest - 1] is not None), key=lambda p: -p[1])
    return {
        "table": {"year": year, "latest_month": latest, "suppliers": {n: list(v) for n, v in rows.items()}},
        "ranking": ranking,
        "source": {"data_period": f"{year}-{latest:02d}"},
    }


def months(*values):
    return list(values) + [None] * (12 - len(values))


def make_ctx(suppliers=(), ele=None, gas=None):
    return {
        "suppliers": list(suppliers),
        "profile": {"tariff": "D02d", "area": "PRE", "annual_mwh": [2, 4]},
        "eru": {"rows": copy.deepcopy(BAND_ROWS)},
        "ote": ele or ote_table({"velky": months(1000), "stredni": months(500), "maly": months(10)}),
        "ote_gas": gas,
        "audit_date": AUDIT_DAY,
        "definitions": {},
    }


def claim(**params):
    return {"quote": "", "params": params}


# ---------- ERÚ pásmo ----------

def test_price_favorable_supported_when_not_above_band():
    s = supplier("a", [offer("a1", 3850.0)])
    out = rules.price_favorable(claim(), s, make_ctx([s]))
    assert out["verdict"] == rules.SUPPORTED
    assert out["evidence"][0]["chart"]["position"] == "within"


def test_price_above_band_is_not_a_contradiction():
    s = supplier("a", [offer("a1", 4500.0)])
    assert rules.price_favorable(claim(), s, make_ctx([s]))["verdict"] == rules.UNVERIFIABLE


def test_result_recomputes_when_price_changes():
    s = supplier("a", [offer("a1", 3850.0)])
    ctx = make_ctx([s])
    assert rules.benchmark_offer(s["offers"][0], ctx)["position"] == "within"
    s["offers"][0]["price_mwh_excl_vat"] = 3700.0
    assert rules.benchmark_offer(s["offers"][0], ctx)["position"] == "below"


def test_unconfirmed_offer_is_benchmarked_in_its_capture_month():
    # nepotvrzená nabídka zachycená v lednu 2024 se nesmí měřit dnešním pásmem
    s = supplier("a", [offer("a1", 3850.0, confirmed_live=False, captured_at="2024-01-10")])
    bench = rules.benchmark_offer(s["offers"][0], make_ctx([s]))
    assert bench["period"] == "2024-01" and bench["band"] is None and not bench["eligible"]
    assert rules.price_favorable(claim(), s, make_ctx([s]))["verdict"] == rules.UNVERIFIABLE


def test_below_band_wording_is_not_a_market_rank():
    s = supplier("a", [offer("a1", 3700.0)])
    out = rules.price_favorable(claim(), s, make_ctx([s]))
    assert out["verdict"] == rules.SUPPORTED
    text = out["finding"] + out["evidence"][0]["value"]
    assert "pod indikativním pásmem ERÚ" in text and "čtvrt" not in text
    assert "není změřené pořadí" in out["limitation"]


# ---------- společná oprávněnost nabídek ----------

@pytest.mark.parametrize("change, reason", [
    ({"tariff": "D25d"}, "sazbu"),
    ({"area": "EGD"}, "území"),
    ({"stepped": True}, "měnící"),
    ({"stale": True}, "znovu zachytit"),
    ({"fixation_months": None}, "fixaci"),
    ({"price_mwh_incl_vat": None}, "s DPH"),
])
def test_ineligible_offers_give_a_reason(change, reason):
    check = ev.offer_eligibility(offer("a1", 3000.0, fee=100.0, **change), make_ctx())
    assert not check.ok and reason in check.reason


def test_future_dated_price_list_is_not_trusted():
    future = offer("a1", 3000.0, fee=100.0, price_list={"effective_from": "2027-01-01"})
    check = ev.offer_eligibility(future, make_ctx())
    assert not check.ok and "2027-01-01" in check.reason


def test_audience_filter_accepts_offers_open_to_everyone():
    ctx = make_ctx()
    assert ev.offer_eligibility(offer("a", 3000.0, audience="all"), ctx, audience="new").ok
    assert not ev.offer_eligibility(offer("b", 3000.0, audience="existing"), ctx, audience="new").ok


def test_wrong_tariff_is_out_of_rules_and_rankings_alike():
    mine = supplier("a", [offer("a1", 3000.0, fee=150.0)])
    rival = supplier("b", [offer("b1", 2000.0, fee=150.0, tariff="D25d")])
    ctx = make_ctx([mine, rival])
    assert rules.price_rank(claim(kind="superlative"), mine, ctx)["evidence"] == []
    assert not ev.offer_eligibility(rival["offers"][0], ctx, need_fees=True, audience="new").ok


# ---------- cenové prvenství a procenta ----------

def test_price_rank_contradicted_by_cheaper_comparable_offer_with_fees():
    mine = supplier("a", [offer("a1", 3000.0, fee=150.0)])
    rival = supplier("b", [offer("b1", 2800.0, fee=150.0)])
    out = rules.price_rank(claim(kind="superlative"), mine, make_ctx([mine, rival]))
    assert out["verdict"] == rules.CONTRADICTED


def test_price_rank_unverifiable_without_monthly_fee():
    mine = supplier("a", [offer("a1", 3000.0)])
    rival = supplier("b", [offer("b1", 2800.0)])
    out = rules.price_rank(claim(kind="superlative"), mine, make_ctx([mine, rival]))
    assert out["verdict"] == rules.UNVERIFIABLE and out["opportunity"]


def test_cheapest_in_sample_does_not_prove_cheapest_on_market():
    mine = supplier("a", [offer("a1", 2500.0, fee=100.0)])
    rival = supplier("b", [offer("b1", 2800.0, fee=150.0)])
    assert rules.price_rank(claim(kind="superlative"), mine, make_ctx([mine, rival]))["verdict"] == rules.UNVERIFIABLE


def test_explicit_sample_claim_can_be_supported():
    mine = supplier("a", [offer("a1", 2500.0, fee=100.0)])
    rival = supplier("b", [offer("b1", 2800.0, fee=150.0)])
    assert rules.price_rank(claim(scope="sample"), mine, make_ctx([mine, rival]))["verdict"] == rules.SUPPORTED


def test_different_fixation_is_not_comparable():
    mine = supplier("a", [offer("a1", 3000.0, fixation=12, fee=100.0)])
    rival = supplier("b", [offer("b1", 2000.0, fixation=36, fee=100.0)])
    out = rules.price_rank(claim(kind="superlative"), mine, make_ctx([mine, rival]))
    assert out["verdict"] == rules.UNVERIFIABLE and out["evidence"] == []


def test_fee_can_outweigh_a_lower_unit_price():
    mine = supplier("a", [offer("a1", 3000.0, fee=100.0)])
    rival = supplier("b", [offer("b1", 2900.0, fee=400.0)])
    assert rules.price_rank(claim(kind="superlative"), mine, make_ctx([mine, rival]))["verdict"] == rules.UNVERIFIABLE


def test_annual_cost_is_exact():
    assert rules._annual_cost(offer("a1", 3000.0, fee=163.35), 2) == 12 * 163.35 + 2 * 3630.0


def test_pct_cheaper_supported_contradicted_and_without_basis():
    mine = supplier("a", [offer("a1", 2700.0, fee=100.0)])
    rival = supplier("b", [offer("b1", 3000.0, fee=100.0)])
    ctx = make_ctx([mine, rival])
    assert rules.price_pct_cheaper(claim(pct=5, baseline_supplier_id="b"), mine, ctx)["verdict"] == rules.SUPPORTED
    assert rules.price_pct_cheaper(claim(pct=30, baseline_supplier_id="b"), mine, ctx)["verdict"] == rules.CONTRADICTED
    assert rules.price_pct_cheaper(claim(pct=5), mine, ctx)["verdict"] == rules.UNVERIFIABLE
    total_bill = claim(pct=5, baseline_supplier_id="b", basis="total_bill")
    assert rules.price_pct_cheaper(total_bill, mine, ctx)["verdict"] == rules.UNVERIFIABLE


# ---------- OTE: rozsah tvrzení (QUAL-001) ----------

def opm_claim(claimed, commodities=("elektřina",), comparator="approx", metric="opm", **extra):
    return claim(claimed=claimed, scope={"metric": metric, "commodities": list(commodities),
                                         "comparator": comparator}, **extra)


def test_missing_gas_is_not_zero():
    # recenzent: 1 000 OPM elektřiny, plyn chybí; tvrzeno 1 500 OPM celkem → nelze určit, ne rozpor
    out = rules.customer_count(opm_claim(1500, ("elektřina", "plyn")), supplier("velky"), make_ctx())
    assert out["verdict"] == rules.UNVERIFIABLE and "nelze určit" in out["finding"]


def test_commodities_from_different_periods_are_not_added():
    ele = ote_table({"velky": months(1000), "stredni": months(500), "maly": months(10)})
    gas = ote_table({"velky": months(None, None, 500)}, year=2025)
    ctx = make_ctx(ele=ele, gas=gas)
    both = rules.customer_count(opm_claim(1500, ("elektřina", "plyn")), supplier("velky"), ctx)
    assert both["verdict"] == rules.UNVERIFIABLE and "jiné období" in both["finding"]
    # recenzent: tvrzení jen o elektřině se nesmí potvrdit přičtením plynu
    only_ele = rules.customer_count(opm_claim(1100, comparator="at_least"), supplier("velky"), ctx)
    assert only_ele["verdict"] == rules.CONTRADICTED and "plyn" not in only_ele["finding"]


@pytest.mark.parametrize("comparator, claimed, verdict", [
    ("at_least", 900, rules.SUPPORTED),
    ("at_least", 1100, rules.CONTRADICTED),
    ("exactly", 1000, rules.SUPPORTED),
    ("exactly", 999, rules.CONTRADICTED),
    ("approx", 1030, rules.SUPPORTED),
    ("approx", 1100, rules.UNVERIFIABLE),
    ("approx", 1500, rules.CONTRADICTED),
])
def test_comparators_for_supply_point_claims(comparator, claimed, verdict):
    out = rules.customer_count(opm_claim(claimed, comparator=comparator), supplier("velky"), make_ctx())
    assert out["verdict"] == verdict


def test_claim_period_must_match_data_period():
    out = rules.customer_count(opm_claim(1000, period="2025-12"), supplier("velky"), make_ctx())
    assert out["verdict"] == rules.UNVERIFIABLE and "liší" in out["finding"]


def test_merged_companies_are_summed_only_when_all_values_exist():
    gas = ote_table({"velky": months(300), "stredni": months(100), "x": months(1), "y": months(1),
                     "z": months(1), "w": months(1), "v": months(1), "u": months(1), "t": months(1),
                     "s": months(1)})
    ctx = make_ctx(gas=gas)
    merged = opm_claim(1850, ("elektřina", "plyn"), comparator="at_least", merged_with=["stredni"])
    assert rules.customer_count(merged, supplier("velky"), ctx)["verdict"] == rules.SUPPORTED  # 1300 + 600
    no_gas_row = opm_claim(1000, ("elektřina", "plyn"), comparator="at_least", merged_with=["maly"])
    out = rules.customer_count(no_gas_row, supplier("velky"), ctx)
    assert out["verdict"] == rules.UNVERIFIABLE and "maly (plyn)" in out["finding"]


def test_customer_claims_never_get_a_verdict_from_supply_points():
    ctx = make_ctx()
    for claimed in (900, 1000, 5000):
        out = rules.customer_count(claim(claimed=claimed), supplier("velky"), ctx)
        assert out["verdict"] == rules.UNVERIFIABLE
    over = rules.customer_count(claim(claimed=5000), supplier("velky"), ctx)
    assert over["opportunity"] and "v kterémkoli měsíci" in over["finding"]


def test_multi_country_customer_count_reports_czech_share():
    out = rules.customer_count(claim(claimed=100000, includes_other_markets="CZ+SK"), supplier("velky"), make_ctx())
    assert out["verdict"] == rules.UNVERIFIABLE and "1,0 %" in out["finding"]


def test_claimed_number_is_read_from_quote():
    assert rules._claimed_from_quote("staráme se o více než 185 000 odběrných míst") == (185000, "opm")
    assert rules._claimed_from_quote("v Česku se staráme o 1,5 milionu zákazníků") == (1500000, None)
    assert rules._claimed_from_quote("250 tis. zákazníků") == (250000, None)
    assert rules._claimed_from_quote("jsme tu pro vás") == (None, None)


def test_size_claim_without_metric_is_context_only():
    first = rules.market_size(claim(), supplier("velky"), make_ctx())
    third = rules.market_size(claim(), supplier("maly"), make_ctx())
    assert first["verdict"] == third["verdict"] == rules.UNVERIFIABLE
    assert "neuvádí, podle čeho" in third["finding"] and third["opportunity"]


def test_size_claim_about_supply_points_gets_a_verdict():
    gas = ote_table({"plynar": months(2000), "velky": months(300), "stredni": months(100), "maly": months(5)})
    explicit = claim(scope={"metric": "opm", "commodities": ["elektřina", "plyn"]})
    assert rules.market_size(explicit, supplier("velky"), make_ctx(gas=gas))["verdict"] == rules.PARTIAL
    assert rules.market_size(explicit, supplier("maly"), make_ctx(gas=gas))["verdict"] == rules.CONTRADICTED
    # dodavatel bez řádku v tabulce plynu: chybějící údaj zastaví verdikt
    no_gas = {k: v for k, v in gas["table"]["suppliers"].items() if k != "maly"}
    out = rules.market_size(explicit, supplier("maly"), make_ctx(gas=ote_table(no_gas)))
    assert out["verdict"] == rules.UNVERIFIABLE
    ele_only = claim(scope={"metric": "opm"})
    assert rules.market_size(ele_only, supplier("velky"), make_ctx())["verdict"] == rules.SUPPORTED


def test_czech_or_alternative_qualifier_is_never_a_verdict():
    gas = ote_table({"velky": months(300), "stredni": months(100), "maly": months(5)})
    ctx = make_ctx(gas=gas)
    ctx["definitions"] = {"traditional_suppliers": {"names": ["velky"], "note": "Definice týmu."}}
    czech = rules.market_size(claim(qualifier="český"), supplier("maly"), ctx)
    assert czech["verdict"] == rules.UNVERIFIABLE and "vlastnictví" in czech["finding"]
    alternative = rules.market_size(claim(qualifier="alternativní"), supplier("maly"), ctx)
    assert alternative["verdict"] == rules.UNVERIFIABLE and "stredni" in alternative["finding"]
    assert alternative["opportunity"]


# ---------- OTE: období a růst (QUAL-005) ----------

def test_market_change_uses_one_window_and_cohort():
    # recenzent: A 100/200/200, B chybí/100/200 → společné období únor–březen, +33,3 %, ne +100 %
    table = ote_table({"A": months(100, 200, 200), "B": months(None, 100, 200)})
    change = ev.market_change(table)
    assert change.window.label == "2026-02 až 2026-03" and change.pct == 33.3
    assert (change.cohort, change.excluded) == (2, 0)


def test_supplier_without_value_at_window_edge_has_no_change():
    table = ote_table({"A": months(100, 200, 200), "B": months(100, 150, 200), "C": months(100, 120, None)})
    assert ev.opm_change(table, "C").abs is None
    assert ev.market_change(table).excluded == 1


def test_growth_claim_needs_supply_point_metric():
    ele = ote_table({"roste": months(100, 110), "klesa": months(100, 90), "jeden": months(100, None)})
    ctx = make_ctx(ele=ele)
    opm = claim(scope={"metric": "opm"})
    assert rules.customer_growth(opm, supplier("roste"), ctx)["verdict"] == rules.SUPPORTED
    assert rules.customer_growth(opm, supplier("klesa"), ctx)["verdict"] == rules.CONTRADICTED
    assert rules.customer_growth(opm, supplier("jeden"), ctx)["verdict"] == rules.UNVERIFIABLE
    assert rules.customer_growth(claim(), supplier("roste"), ctx)["verdict"] == rules.UNVERIFIABLE
    assert rules.customer_growth(claim(superlative=True), supplier("roste"), ctx)["verdict"] == rules.UNVERIFIABLE


# ---------- zelená tvrzení (QUAL-002) ----------

def green_supplier(product="Produkt A", **extra):
    s = supplier("a")
    s["green_evidence"] = [{"label": "Prohlášení", "value": "kryto zárukami původu", "product": product,
                            "type": "prohlášení dodavatele", "status": "nalezeno na živé stránce", **extra}]
    return s


def test_declaration_for_another_product_is_not_evidence():
    out = rules.green_share(claim(product="Produkt B"), green_supplier("Produkt A"), make_ctx())
    assert out["verdict"] == rules.UNVERIFIABLE and out["evidence"] == []


def test_declaration_is_reported_but_not_a_supported_verdict():
    out = rules.green_share(claim(product="Produkt A"), green_supplier("Produkt A"), make_ctx())
    assert out["verdict"] == rules.UNVERIFIABLE
    assert "prohlášení dodavatele" in out["finding"] and "nezávisle" in out["finding"]
    stale = rules.green_share(claim(product="Produkt A"), green_supplier("Produkt A", stale=True), make_ctx())
    assert "už na živé stránce není" in stale["finding"]


def test_missing_green_evidence_is_unverifiable_not_false():
    out = rules.green_share(claim(), supplier("a"), make_ctx())
    assert out["verdict"] == rules.UNVERIFIABLE and out["possible_regulatory_risk"]


def test_carbon_offsets_flag_risk_without_factual_verdict():
    out = rules.carbon_neutral(claim(uses_offsets=True), supplier("a"), make_ctx())
    assert out["verdict"] == rules.UNVERIFIABLE and out["risk_reason"] and out["review_required"]


# ---------- rozhodnutí ERÚ ----------

def test_trust_slogan_gets_regulator_context_but_no_verdict():
    s = supplier("a")
    practice = {"date": "01.04.2026", "title": "A - rozhodnutí k užití nekalé obchodní praktiky",
                "url": "https://eru.gov.cz/x", "kind": "practice"}
    other = {"date": "01.01.2025", "title": "A - rozhodnutí k porušení povinností držitele licence",
             "url": "https://eru.gov.cz/y", "kind": "offence"}
    stopped = {"date": "01.01.2024", "title": "A - usnesení o zastavení řízení",
               "url": "https://eru.gov.cz/z", "kind": "discontinued"}
    ctx = make_ctx()
    ctx["decisions"] = {"a": {"decisions": [practice, other, stopped], "truncated": False}}
    out = rules.soft_trust(claim(), s, ctx)
    assert out["verdict"] == rules.UNVERIFIABLE and out["opportunity"]
    assert "2 pravomocná rozhodnutí" in out["finding"] and len(out["evidence"]) == 2
    ctx["decisions"] = {"a": {"decisions": [], "truncated": False}}
    none = rules.soft_trust(claim(), s, ctx)["finding"]
    assert "nenašli" in none and "neeviduje" not in none
    ctx["decisions"] = {"a": {"decisions": [], "truncated": True}}
    assert "zkrácen" in rules.soft_trust(claim(), s, ctx)["finding"]


def test_unknown_rule_gets_no_verdict():
    assert rules.evaluate({"rule_id": "vymyslene"}, supplier("a"), make_ctx())["verdict"] == rules.UNVERIFIABLE


def test_eru_rejects_changed_columns_and_bad_values():
    with pytest.raises(eru.EruFormatError):
        eru.parse_csv("Mesic,Sazba\n2026-11,D02d\n".encode("utf-8"))
    header = ",".join(eru.REQUIRED)
    with pytest.raises(eru.EruFormatError):
        eru.parse_csv(f"{header}\n2026-11-01 00:00:00,D02d,12,abc,1,1,1\n".encode("utf-8"))
    duplicate = BAND_ROWS + [BAND_ROWS[0]]
    with pytest.raises(eru.EruFormatError):
        eru.select_band(duplicate, "D02d", 12, "2026-11")
