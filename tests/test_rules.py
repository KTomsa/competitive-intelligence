"""Pravidla: podložený, vyvrácený a nesrovnatelný případ. Běží bez sítě."""
import copy

import pytest

from competitive_intelligence import rules
from competitive_intelligence.connectors import eru

BAND_ROWS = [
    {"month": "2026-11", "tariff": "D02d", "fixation_months": 12,
     "low_excl_vat": 3800.0, "high_excl_vat": 3900.0, "low_incl_vat": 4598.0, "high_incl_vat": 4719.0},
    {"month": "2026-11", "tariff": "D02d", "fixation_months": 36,
     "low_excl_vat": 3200.0, "high_excl_vat": 3400.0, "low_incl_vat": 3872.0, "high_incl_vat": 4114.0},
]


def offer(oid, price_excl, fixation=12, tariff="D02d", fee=None):
    return {
        "id": oid, "product": f"Produkt {oid}", "tariff": tariff, "fixation_months": fixation,
        "price_mwh_excl_vat": price_excl,
        "price_mwh_incl_vat": None if price_excl is None else round(price_excl * 1.21, 2),
        "monthly_fee_incl_vat": fee, "confirmed_live": True, "captured_at": "2026-11-13",
    }


def supplier(sid, offers, ote_name=None):
    return {"id": sid, "name": sid.upper(), "ote_name": ote_name or sid, "offers": offers, "green_evidence": []}


def make_ctx(suppliers):
    return {
        "suppliers": suppliers,
        "profile": {"tariff": "D02d", "annual_mwh": [2, 4]},
        "eru": {"rows": copy.deepcopy(BAND_ROWS)},
        "ote": {
            "ranking": [("velky", 1000), ("stredni", 500), ("maly", 10)],
            "source": {"data_period": "2026-09"},
        },
        "offer_month": lambda o: "2026-11",
    }


def test_price_favorable_supported_when_not_above_band():
    s = supplier("a", [offer("a1", 3850.0)])
    out = rules.price_favorable({"params": {}}, s, make_ctx([s]))
    assert out["verdict"] == rules.SUPPORTED
    assert out["evidence"][0]["chart"]["position"] == "within"


def test_price_above_band_is_not_a_contradiction():
    s = supplier("a", [offer("a1", 4500.0)])
    out = rules.price_favorable({"params": {}}, s, make_ctx([s]))
    assert out["verdict"] == rules.UNVERIFIABLE


def test_result_recomputes_when_price_changes():
    s = supplier("a", [offer("a1", 3850.0)])
    ctx = make_ctx([s])
    assert rules.benchmark_offer(s["offers"][0], ctx)["position"] == "within"
    s["offers"][0]["price_mwh_excl_vat"] = 3700.0
    assert rules.benchmark_offer(s["offers"][0], ctx)["position"] == "below"


def test_missing_band_period_stops_verdict():
    s = supplier("a", [offer("a1", 3850.0)])
    ctx = make_ctx([s])
    ctx["offer_month"] = lambda o: "2024-01"  # historické tvrzení se nesmí měřit dnešním pásmem
    out = rules.price_favorable({"params": {}}, s, ctx)
    assert out["verdict"] == rules.UNVERIFIABLE


def test_unknown_tariff_or_vat_is_not_eligible():
    s = supplier("a", [offer("a1", 3850.0, tariff=None)])
    assert rules.price_favorable({"params": {}}, s, make_ctx([s]))["verdict"] == rules.UNVERIFIABLE
    unknown_vat = {**offer("a2", None), "price_mwh_unknown_vat": 3850.0}
    assert rules.benchmark_offer(unknown_vat, make_ctx([s]))["eligible"] is False


def test_price_rank_contradicted_by_cheaper_comparable_offer_with_fees():
    mine = supplier("a", [offer("a1", 3000.0, fee=150.0)])
    rival = supplier("b", [offer("b1", 2800.0, fee=150.0)])
    out = rules.price_rank({"params": {"kind": "superlative"}}, mine, make_ctx([mine, rival]))
    assert out["verdict"] == rules.CONTRADICTED


def test_price_rank_unverifiable_without_monthly_fee():
    mine = supplier("a", [offer("a1", 3000.0)])
    rival = supplier("b", [offer("b1", 2800.0)])
    out = rules.price_rank({"params": {"kind": "superlative"}}, mine, make_ctx([mine, rival]))
    assert out["verdict"] == rules.UNVERIFIABLE
    assert out["opportunity"]


def test_cheapest_in_sample_does_not_prove_cheapest_on_market():
    mine = supplier("a", [offer("a1", 2500.0, fee=100.0)])
    rival = supplier("b", [offer("b1", 2800.0, fee=150.0)])
    out = rules.price_rank({"params": {"kind": "superlative"}}, mine, make_ctx([mine, rival]))
    assert out["verdict"] == rules.UNVERIFIABLE


def test_different_fixation_is_not_comparable():
    mine = supplier("a", [offer("a1", 3000.0, fixation=12, fee=100.0)])
    rival = supplier("b", [offer("b1", 2000.0, fixation=36, fee=100.0)])
    out = rules.price_rank({"params": {"kind": "superlative"}}, mine, make_ctx([mine, rival]))
    assert out["verdict"] == rules.UNVERIFIABLE
    assert out["evidence"] == []


def test_market_size_by_supply_points():
    ctx = make_ctx([])
    assert rules.market_size({"params": {"scope": "all"}}, supplier("velky", []), ctx)["verdict"] == rules.SUPPORTED
    assert rules.market_size({"params": {"scope": "all"}}, supplier("maly", []), ctx)["verdict"] == rules.CONTRADICTED
    assert rules.market_size({"params": {"scope": "all"}}, supplier("neznamy", []), ctx)["verdict"] == rules.UNVERIFIABLE
    partial = rules.market_size(
        {"params": {"scope": "all", "commodities": ["elektřina", "plyn"]}}, supplier("velky", []), ctx
    )
    assert partial["verdict"] == rules.PARTIAL and len(partial["subclaims"]) == 2


def test_supply_points_are_not_customers():
    out = rules.customer_count({"params": {"claimed": 1000}}, supplier("velky", []), make_ctx([]))
    assert out["verdict"] == rules.UNVERIFIABLE


def test_missing_green_evidence_is_unverifiable_not_false():
    out = rules.green_share({"params": {}}, supplier("a", []), make_ctx([]))
    assert out["verdict"] == rules.UNVERIFIABLE and out["possible_regulatory_risk"]


def test_annual_cost_is_exact():
    assert rules._annual_cost(offer("a1", 3000.0, fee=163.35), 2) == 12 * 163.35 + 2 * 3630.0


def test_explicit_sample_claim_can_be_supported():
    mine = supplier("a", [offer("a1", 2500.0, fee=100.0)])
    rival = supplier("b", [offer("b1", 2800.0, fee=150.0)])
    out = rules.price_rank({"params": {"scope": "sample"}}, mine, make_ctx([mine, rival]))
    assert out["verdict"] == rules.SUPPORTED


def test_pct_cheaper_supported_contradicted_and_without_basis():
    mine = supplier("a", [offer("a1", 2700.0, fee=100.0)])
    rival = supplier("b", [offer("b1", 3000.0, fee=100.0)])
    ctx = make_ctx([mine, rival])
    claim = lambda pct: {"params": {"pct": pct, "baseline_supplier_id": "b"}}
    assert rules.price_pct_cheaper(claim(5), mine, ctx)["verdict"] == rules.SUPPORTED
    assert rules.price_pct_cheaper(claim(30), mine, ctx)["verdict"] == rules.CONTRADICTED
    assert rules.price_pct_cheaper({"params": {"pct": 5}}, mine, ctx)["verdict"] == rules.UNVERIFIABLE
    total_bill = {"params": {"pct": 5, "baseline_supplier_id": "b", "basis": "total_bill"}}
    assert rules.price_pct_cheaper(total_bill, mine, ctx)["verdict"] == rules.UNVERIFIABLE


def test_pct_cheaper_without_fees_is_unverifiable():
    mine = supplier("a", [offer("a1", 2700.0)])
    rival = supplier("b", [offer("b1", 3000.0)])
    claim = {"params": {"pct": 5, "baseline_supplier_id": "b"}}
    assert rules.price_pct_cheaper(claim, mine, make_ctx([mine, rival]))["verdict"] == rules.UNVERIFIABLE


def test_customer_growth_uses_supply_points_within_year():
    ctx = make_ctx([])
    ctx["ote"]["table"] = {"year": 2026, "suppliers": {
        "roste": [100, 110, None, None, None, None, None, None, None, None, None, None],
        "klesa": [100, 90, None, None, None, None, None, None, None, None, None, None],
        "jeden": [100] + [None] * 11,
    }}
    assert rules.customer_growth({"params": {}}, supplier("roste", []), ctx)["verdict"] == rules.SUPPORTED
    assert rules.customer_growth({"params": {}}, supplier("klesa", []), ctx)["verdict"] == rules.CONTRADICTED
    assert rules.customer_growth({"params": {}}, supplier("jeden", []), ctx)["verdict"] == rules.UNVERIFIABLE
    fastest = {"params": {"superlative": True}}
    assert rules.customer_growth(fastest, supplier("roste", []), ctx)["verdict"] == rules.UNVERIFIABLE


def test_supply_point_claim_is_checked_against_supply_points():
    ctx = make_ctx([])
    ok = rules.customer_count({"params": {"claimed": 900, "metric": "opm"}}, supplier("velky", []), ctx)
    bad = rules.customer_count({"params": {"claimed": 2000, "metric": "opm"}}, supplier("velky", []), ctx)
    assert ok["verdict"] == rules.SUPPORTED and bad["verdict"] == rules.CONTRADICTED


def test_carbon_offsets_flag_risk_without_factual_verdict():
    out = rules.carbon_neutral({"params": {"uses_offsets": True}}, supplier("a", []), make_ctx([]))
    assert out["verdict"] == rules.UNVERIFIABLE
    assert out["possible_regulatory_risk"] and out["risk_reason"] and out["review_required"]


def test_params_are_read_from_quote_of_automatically_found_claims():
    ctx = make_ctx([])
    with_gas = {"quote": "Přejděte k největšímu dodavateli elektřiny a plynu", "params": {}}
    assert rules.market_size(with_gas, supplier("velky", []), ctx)["verdict"] == rules.PARTIAL
    s = supplier("a", [offer("a1", 3300.0, fixation=36)])
    out = rules.price_guarantee({"quote": "Jistota ceny na 3 roky.", "params": {}}, s, make_ctx([s]))
    assert out["verdict"] == rules.SUPPORTED


def gas_ctx():
    ctx = make_ctx([])
    ctx["ote_gas"] = {
        "ranking": [("plynar", 2000), ("velky", 300), ("stredni", 100)],
        "source": {"data_period": "2026-09"},
    }
    return ctx


def test_largest_in_electricity_and_gas_checks_both_commodities():
    claim = {"params": {"scope": "all", "commodities": ["elektřina", "plyn"]}}
    out = rules.market_size(claim, supplier("velky", []), gas_ctx())
    # velky: elektřina 1., plyn 2. (první je plynar), v součtu 2. za plynarem → jen částečně
    assert out["verdict"] == rules.PARTIAL
    statuses = [sub["status"] for sub in out["subclaims"]]
    assert statuses[0] == "ověřeno" and statuses[1].startswith("v rozporu") and "plynar" in statuses[1]
    ctx = gas_ctx()
    ctx["ote_gas"]["ranking"] = [("velky", 900), ("stredni", 100)]
    assert rules.market_size(claim, supplier("velky", []), ctx)["verdict"] == rules.SUPPORTED


def test_customer_count_against_electricity_plus_gas_supply_points():
    ctx = gas_ctx()  # velky: 1000 + 300 = 1300 OPM
    ok = rules.customer_count({"params": {"claimed": 1200}}, supplier("velky", []), ctx)
    assert ok["verdict"] == rules.PARTIAL and len(ok["subclaims"]) == 2
    too_many = rules.customer_count({"params": {"claimed": 2000}}, supplier("velky", []), ctx)
    assert too_many["verdict"] == rules.CONTRADICTED
    slightly = rules.customer_count({"params": {"claimed": 1350}}, supplier("velky", []), ctx)
    assert slightly["verdict"] == rules.UNVERIFIABLE
    abroad = {"params": {"claimed": 100000, "includes_other_markets": "Česko a Slovensko"}}
    out = rules.customer_count(abroad, supplier("velky", []), ctx)
    assert out["verdict"] == rules.UNVERIFIABLE and "1,3 %" in out["finding"]


def test_supply_point_claim_of_merged_companies_sums_electricity_and_gas():
    ctx = gas_ctx()  # velky 1000 + 300, stredni 500 + 100 → 1900
    claim = lambda n: {"params": {"claimed": n, "metric": "opm", "merged_with": ["stredni"]}}
    assert rules.customer_count(claim(1850), supplier("velky", []), ctx)["verdict"] == rules.SUPPORTED
    assert rules.customer_count(claim(2500), supplier("velky", []), ctx)["verdict"] == rules.CONTRADICTED


def test_claimed_number_is_read_from_quote():
    assert rules._claimed_from_quote("staráme se o více než 185 000 odběrných míst") == (185000, "opm")
    assert rules._claimed_from_quote("v Česku se staráme o 1,5 milionu zákazníků") == (1500000, None)
    assert rules._claimed_from_quote("250 tis. zákazníků") == (250000, None)
    assert rules._claimed_from_quote("jsme tu pro vás") == (None, None)


def test_undefined_qualifier_is_never_a_verdict_but_reports_who_is_bigger():
    ctx = gas_ctx()
    claim = {"params": {"scope": "qualified", "qualifier": "alternativní",
                        "merged_with": ["maly"], "exclude": ["velky", "plynar"]}}
    out = rules.market_size(claim, supplier("maly", []), ctx)  # maly 10 + maly 10? jen jednou → 10
    assert out["verdict"] == rules.UNVERIFIABLE
    assert "stredni" in out["finding"] and out["opportunity"]
    solo = rules.market_size({"params": {"scope": "czech_owned"}}, supplier("maly", []), ctx)
    assert solo["verdict"] == rules.UNVERIFIABLE


def test_stepped_products_and_other_areas_are_not_compared():
    mine = supplier("a", [offer("a1", 3000.0, fee=150.0)])
    stepped = supplier("b", [{**offer("b1", 2000.0, fee=150.0), "stepped": True}])
    claim = {"params": {"kind": "superlative"}}
    assert rules.price_rank(claim, mine, make_ctx([mine, stepped]))["evidence"] == []
    mine["offers"][0]["area"] = "PREdistribuce"
    elsewhere = supplier("c", [{**offer("c1", 2000.0, fee=150.0), "area": "EG.D"}])
    assert rules.price_rank(claim, mine, make_ctx([mine, elsewhere]))["evidence"] == []


def test_fee_can_outweigh_a_lower_unit_price():
    # nižší cena za MWh, ale vysoký stálý plat: při nízké spotřebě dražší → žádný rozpor
    mine = supplier("a", [offer("a1", 3000.0, fee=100.0)])
    rival = supplier("b", [offer("b1", 2900.0, fee=400.0)])
    out = rules.price_rank({"params": {"kind": "superlative"}}, mine, make_ctx([mine, rival]))
    assert out["verdict"] == rules.UNVERIFIABLE


def test_green_claim_with_product_evidence_is_supported_until_evidence_disappears():
    s = supplier("a", [])
    s["green_evidence"] = [{"label": "Produkt EKO", "value": "kryto zárukami původu", "status": "nalezeno"}]
    assert rules.green_share({"params": {}}, s, make_ctx([]))["verdict"] == rules.SUPPORTED
    s["green_evidence"][0]["stale"] = True
    assert rules.green_share({"params": {}}, s, make_ctx([]))["verdict"] == rules.UNVERIFIABLE


def test_below_band_means_cheapest_quarter_in_finding():
    s = supplier("a", [offer("a1", 3700.0)])
    out = rules.price_favorable({"params": {}}, s, make_ctx([s]))
    assert out["verdict"] == rules.SUPPORTED and "nejlevnější čtvrtinou" in out["finding"]


def test_unknown_rule_gets_no_verdict():
    assert rules.evaluate({"rule_id": "vymyslene"}, supplier("a", []), make_ctx([]))["verdict"] == rules.UNVERIFIABLE


def test_eru_rejects_changed_columns_and_bad_values():
    with pytest.raises(eru.EruFormatError):
        eru.parse_csv("Mesic,Sazba\n2026-11,D02d\n".encode("utf-8"))
    header = ",".join(eru.REQUIRED)
    with pytest.raises(eru.EruFormatError):
        eru.parse_csv(f"{header}\n2026-11-01 00:00:00,D02d,12,abc,1,1,1\n".encode("utf-8"))
    duplicate = BAND_ROWS + [BAND_ROWS[0]]
    with pytest.raises(eru.EruFormatError):
        eru.select_band(duplicate, "D02d", 12, "2026-11")
