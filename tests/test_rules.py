"""Deterministic rules: each test pins one methodological invariant from docs/RULE_CATALOG.md."""

from datetime import date
from decimal import Decimal

from competitive_intelligence.rules import apply_judge, engine
from competitive_intelligence.rules.engine import RuleOutcome
from competitive_intelligence.schemas import VerdictValue
from competitive_intelligence.tools import calculate_annual_cost
from competitive_intelligence.tools.reference import PriceOffer

D = Decimal
COSTS = {"p": {"a": D("8200"), "b": D("7640"), "c": D("8960")}}


def test_annual_cost_is_exact_decimal():
    offer = PriceOffer(
        offer_id="o",
        supplier_id="a",
        product_id="p",
        monthly_fee=D("150.10"),
        price_mwh=D("3200.33"),
        source_url="https://example.org/x",
        source_ref="t",
        captured_at="2026-11-10T09:00:00Z",
        valid_as_of="2026-11-10",
    )
    cost = calculate_annual_cost(offer, D("2"))
    assert isinstance(cost, Decimal)
    assert cost == D("8201.86")


def test_price_rank_cheaper_competitor_contradicts():
    outcome = engine.price_rank(supplier_id="a", scope="cz_market", costs_by_profile=COSTS)
    assert outcome.rule_verdict is VerdictValue.CONTRADICTED


def test_price_rank_winning_small_sample_does_not_confirm_market_claim():
    outcome = engine.price_rank(supplier_id="b", scope="cz_market", costs_by_profile=COSTS)
    assert outcome.rule_verdict is VerdictValue.UNVERIFIABLE
    assert outcome.rule_verdict is not VerdictValue.PARTIALLY_SUPPORTED


def test_price_rank_explicit_sample_claim_can_be_supported():
    outcome = engine.price_rank(supplier_id="b", scope="sample", costs_by_profile=COSTS)
    assert outcome.rule_verdict is VerdictValue.SUPPORTED


def test_price_rank_missing_own_offer_is_not_contradiction():
    outcome = engine.price_rank(supplier_id="z", scope="cz_market", costs_by_profile=COSTS)
    assert outcome.rule_verdict is VerdictValue.UNVERIFIABLE


def test_pct_cheaper_without_basis_is_unverifiable():
    outcome = engine.price_pct_cheaper(
        claimed_pct=D("15"), basis=None, costs={"p": (D("8500"), D("10000"))}
    )
    assert outcome.rule_verdict is VerdictValue.UNVERIFIABLE


def test_pct_cheaper_supported_and_contradicted():
    costs = {"p": (D("8500"), D("10000"))}  # 15 % saving
    ok = engine.price_pct_cheaper(claimed_pct=D("15"), basis="annual_trade_cost", costs=costs)
    bad = engine.price_pct_cheaper(claimed_pct=D("20"), basis="annual_trade_cost", costs=costs)
    assert ok.rule_verdict is VerdictValue.SUPPORTED
    assert bad.rule_verdict is VerdictValue.CONTRADICTED


def test_pct_cheaper_on_total_bill_basis_is_not_checked_against_trade_cost():
    outcome = engine.price_pct_cheaper(
        claimed_pct=D("15"), basis="total_bill", costs={"p": (D("8500"), D("10000"))}
    )
    assert outcome.rule_verdict is VerdictValue.UNVERIFIABLE


def test_pct_cheaper_mixed_profiles_is_unverifiable():
    costs = {"small": (D("8500"), D("10000")), "large": (D("9500"), D("10000"))}
    outcome = engine.price_pct_cheaper(claimed_pct=D("15"), basis="annual_trade_cost", costs=costs)
    assert outcome.rule_verdict is VerdictValue.UNVERIFIABLE


def test_price_guarantee():
    claimed = date(2028, 12, 31)
    assert (
        engine.price_guarantee(claimed_until=claimed, fixed_until=claimed, exclusions_unclear=False).rule_verdict
        is VerdictValue.SUPPORTED
    )
    assert (
        engine.price_guarantee(
            claimed_until=claimed, fixed_until=date(2027, 12, 31), exclusions_unclear=False
        ).rule_verdict
        is VerdictValue.CONTRADICTED
    )
    unclear = engine.price_guarantee(claimed_until=claimed, fixed_until=claimed, exclusions_unclear=True)
    assert unclear.rule_verdict is VerdictValue.UNVERIFIABLE and unclear.review_required
    assert (
        engine.price_guarantee(claimed_until=claimed, fixed_until=None, exclusions_unclear=False).rule_verdict
        is VerdictValue.UNVERIFIABLE
    )


def test_green_share_national_mix_never_decides_a_product_claim():
    outcome = engine.green_share(
        claimed_pct=D("100"), renewable_share=D("14.5"), product_specific=False, guarantees_documented=False
    )
    assert outcome.rule_verdict is VerdictValue.UNVERIFIABLE


def test_green_share_missing_guarantees_is_not_contradiction():
    outcome = engine.green_share(
        claimed_pct=D("100"), renewable_share=D("100"), product_specific=True, guarantees_documented=False
    )
    assert outcome.rule_verdict is VerdictValue.UNVERIFIABLE


def test_green_share_product_evidence_decides():
    args = {"claimed_pct": D("100"), "product_specific": True, "guarantees_documented": True}
    assert engine.green_share(renewable_share=D("100"), **args).rule_verdict is VerdictValue.SUPPORTED
    assert engine.green_share(renewable_share=D("60"), **args).rule_verdict is VerdictValue.CONTRADICTED


def test_green_generic_flags_risk_but_is_not_a_factual_verdict():
    outcome = engine.green_generic()
    assert outcome.rule_verdict is VerdictValue.UNVERIFIABLE
    assert outcome.possible_regulatory_risk and outcome.risk_reason and outcome.review_required


def test_opm_is_not_customer_count():
    outcome = engine.customer_count(
        claimed_count=1_000_000, claim_metric="customers", observed=1_240_000, observed_metric="opm"
    )
    assert outcome.rule_verdict is VerdictValue.UNVERIFIABLE


def test_opm_claim_checked_against_opm():
    outcome = engine.customer_count(
        claimed_count=1_000_000, claim_metric="opm", observed=1_240_000, observed_metric="opm"
    )
    assert outcome.rule_verdict is VerdictValue.SUPPORTED


def test_soft_trust_and_other_are_always_unverifiable():
    assert engine.soft_trust().rule_verdict is VerdictValue.UNVERIFIABLE
    assert engine.other().rule_verdict is VerdictValue.UNVERIFIABLE


def _outcome(verdict: VerdictValue) -> RuleOutcome:
    return RuleOutcome(verdict, "test-v1", "reasoning")


def test_judge_can_only_downgrade_to_unverifiable():
    for rule_verdict in VerdictValue:
        verdict = apply_judge(
            claim_id="c", outcome=_outcome(rule_verdict), evidence_ids=[], mismatch_reason="jiný produkt"
        )
        assert verdict.verdict is VerdictValue.UNVERIFIABLE
        assert verdict.rule_verdict is rule_verdict
        if rule_verdict is VerdictValue.UNVERIFIABLE:
            assert not verdict.judge_changed
        else:
            assert verdict.judge_changed and verdict.judge_reason == "jiný produkt"


def test_judge_without_flag_keeps_rule_verdict():
    for rule_verdict in VerdictValue:
        verdict = apply_judge(
            claim_id="c", outcome=_outcome(rule_verdict), evidence_ids=[], mismatch_reason=None
        )
        assert verdict.verdict is rule_verdict and not verdict.judge_changed


def test_unsupported_rank_does_not_use_cheapest_rule():
    from competitive_intelligence.agents import FixtureEvidenceAgent
    from competitive_intelligence.schemas import coerce_rule
    from competitive_intelligence.services import load_demo_fixture
    from competitive_intelligence.services.orchestrator import validate_claims
    from competitive_intelligence.tools import ReferenceStore

    bundle = load_demo_fixture()
    current = validate_claims(bundle.raw_claims[:1], bundle.sources, bundle.canonical_texts)[0][0]
    current.rule = coerce_rule({"id": "price_rank", "params": {"scope": "sample", "rank": 2}})
    _, outcome = FixtureEvidenceAgent(ReferenceStore(bundle.reference, bundle.profiles), bundle.supplier_ids).gather(current, list(bundle.profiles))
    assert outcome.rule_verdict is VerdictValue.UNVERIFIABLE


def test_percentage_claim_cannot_compute_an_unselected_profile():
    from competitive_intelligence.agents import FixtureEvidenceAgent
    from competitive_intelligence.schemas import coerce_rule
    from competitive_intelligence.services import load_demo_fixture
    from competitive_intelligence.services.orchestrator import validate_claims
    from competitive_intelligence.tools import ReferenceStore

    bundle = load_demo_fixture()
    current = validate_claims(bundle.raw_claims[:1], bundle.sources, bundle.canonical_texts)[0][0]
    current.rule = coerce_rule({"id": "price_pct_cheaper", "params": {
        "pct": "7", "basis": "annual_trade_cost", "baseline_supplier_id": "supplier_demo_c", "profile_id": "d02d_4mwh"}})
    facts, outcome = FixtureEvidenceAgent(ReferenceStore(bundle.reference, bundle.profiles), bundle.supplier_ids).gather(current, ["d02d_2mwh"])
    assert outcome.rule_verdict is VerdictValue.UNVERIFIABLE and not facts
