"""Pydantic contracts: closed rule catalogue, literal quotes and the Judge invariant."""

import pytest
from pydantic import ValidationError

from competitive_intelligence.schemas import RULE_IDS, Verdict, VerdictValue, coerce_rule, quote_in_text
from competitive_intelligence.schemas.claim import OtherRule, PriceRankRule


def test_catalogue_has_exactly_eleven_rules():
    assert len(RULE_IDS) == 11


def test_unknown_rule_id_becomes_other():
    assert isinstance(coerce_rule({"id": "invented_rule", "params": {"x": 1}}), OtherRule)
    assert isinstance(coerce_rule(None), OtherRule)


def test_known_rule_validates_params():
    rule = coerce_rule({"id": "price_rank", "params": {"scope": "cz_market", "rank": 1}})
    assert isinstance(rule, PriceRankRule)
    with pytest.raises(ValidationError):
        coerce_rule({"id": "price_rank", "params": {"scope": "galaxy"}})
    with pytest.raises(ValidationError):
        coerce_rule({"id": "price_rank", "params": {"unexpected": True}})


def test_quote_must_be_literal_except_whitespace():
    text = "Demo.  Nejlevnější elektřina\n v ČR. Konec."
    assert quote_in_text("Nejlevnější elektřina v ČR", text)
    assert not quote_in_text("Nejlevnejsi elektrina v CR", text)
    assert not quote_in_text("Nejlevnější plyn v ČR", text)
    assert not quote_in_text("   ", text)


def _verdict(**overrides):
    base = {"claim_id": "c", "rule_verdict": "contradicted", "verdict": "contradicted", "reasoning": "r"}
    return Verdict.model_validate({**base, **overrides})


def test_verdict_cannot_be_upgraded():
    with pytest.raises(ValidationError):
        _verdict(rule_verdict="unverifiable", verdict="supported")
    with pytest.raises(ValidationError):
        _verdict(rule_verdict="contradicted", verdict="supported", judge_changed=True, judge_reason="x")


def test_verdict_downgrade_requires_reason():
    with pytest.raises(ValidationError):
        _verdict(verdict="unverifiable", judge_changed=True)
    ok = _verdict(verdict="unverifiable", judge_changed=True, judge_reason="jiný produkt")
    assert ok.verdict is VerdictValue.UNVERIFIABLE


def test_verdict_rejects_old_unsupported_value_and_unknown_fields():
    with pytest.raises(ValidationError):
        _verdict(rule_verdict="unsupported", verdict="unsupported")
    with pytest.raises(ValidationError):
        _verdict(trust_score=42)


def test_regulatory_risk_requires_reason():
    with pytest.raises(ValidationError):
        _verdict(possible_regulatory_risk=True)
