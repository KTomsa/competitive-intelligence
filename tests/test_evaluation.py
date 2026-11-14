"""Exact evaluation denominators and a clearly synthetic 24-case rule regression."""

import hashlib
import json
from pathlib import Path
from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from competitive_intelligence.evaluation import GoldenSet, Predictions, evaluate, main
from competitive_intelligence.rules import engine


def gold_data():
    text = "Nejlevnější elektřina v ČR. Zelená elektřina."
    return {"version": 1, "dataset_id": "synthetic-example-v1", "kind": "synthetic_regression", "sources": [{
        "source_id": "synthetic", "supplier_id": "fictional", "source_url": "https://example.org/synthetic",
        "captured_at": "2026-11-13T12:00:00Z", "canonical_text": text,
        "canonical_hash": hashlib.sha256(text.encode()).hexdigest()}], "claims": [
        {"source_id": "synthetic", "quote": "Nejlevnější elektřina v ČR", "rule": {"id": "price_rank"},
         "verdict": "unverifiable", "rationale": "A small sample cannot establish a nationwide superlative."},
        {"source_id": "synthetic", "quote": "Zelená elektřina", "rule": {"id": "green_generic"},
         "verdict": "unverifiable", "rationale": "Generic language has no testable product metric."}]}


def predictions(gold):
    return {"dataset_id": gold["dataset_id"], "run_id": "synthetic-test", "claims": [
        {"source_id": claim["source_id"], "canonical_hash": gold["sources"][0]["canonical_hash"],
         "quote": claim["quote"], "rule": claim["rule"], "verdict": claim["verdict"]} for claim in gold["claims"]]}


def test_denominators_include_misses_duplicates_and_false_positives():
    gold = gold_data()
    guessed = predictions(gold)
    first = guessed["claims"][0]
    guessed["claims"] = [first, first, {**first, "quote": "Invented claim"}]
    report = evaluate(GoldenSet.model_validate(gold), Predictions.model_validate(guessed))
    assert report["counts"] == {"gold": 2, "predicted": 3, "matched": 1, "false_positive": 2, "false_negative": 1}
    assert report["metrics"]["extraction_precision"] == {"numerator": 1, "denominator": 3, "value": 1 / 3}
    assert report["metrics"]["extraction_recall"]["value"] == 0.5
    assert report["metrics"]["verdict_accuracy_matched"]["value"] == 1
    assert report["metrics"]["verdict_accuracy_all_gold"]["value"] == 0.5
    assert report["release_dataset_eligible"] is False


def test_empty_predictions_have_undefined_precision_and_zero_recall():
    gold = GoldenSet.model_validate(gold_data())
    report = evaluate(gold, Predictions(dataset_id=gold.dataset_id, run_id="empty", claims=[]))
    assert report["metrics"]["extraction_precision"]["value"] is None
    assert report["metrics"]["extraction_recall"]["value"] == 0
    assert report["metrics"]["verdict_accuracy_matched"]["denominator"] == 0


def test_source_hash_mismatch_is_never_matched():
    gold = gold_data()
    guessed = predictions(gold)
    guessed["claims"][0]["canonical_hash"] = "a" * 64
    report = evaluate(GoldenSet.model_validate(gold), Predictions.model_validate(guessed))
    assert report["counts"]["false_positive"] == report["counts"]["false_negative"] == 1


def test_rule_parameters_and_wrong_verdict_are_measured_separately():
    gold = gold_data()
    guessed = predictions(gold)
    guessed["claims"][0]["rule"] = {"id": "price_rank", "params": {"scope": "sample"}}
    guessed["claims"][1]["verdict"] = "supported"
    report = evaluate(GoldenSet.model_validate(gold), Predictions.model_validate(guessed))
    assert report["metrics"]["rule_accuracy_matched"]["value"] == 1
    assert report["metrics"]["rule_parameters_accuracy_matched"]["value"] == 0.5
    assert report["metrics"]["verdict_accuracy_matched"]["value"] == 0.5
    assert report["metrics"]["end_to_end_accuracy"]["value"] == 0


@pytest.mark.parametrize("mutation", ["hash", "quote", "duplicate", "review", "synthetic_review", "decisive_without_evidence"])
def test_bad_or_falsely_reviewed_corpus_is_rejected(mutation):
    gold = gold_data()
    if mutation == "hash":
        gold["sources"][0]["canonical_hash"] = "b" * 64
    elif mutation == "quote":
        gold["claims"][0]["quote"] = "Absent quote"
    elif mutation == "duplicate":
        gold["claims"].append(gold["claims"][0])
    elif mutation == "review":
        gold["kind"] = "human_reviewed"
    elif mutation == "synthetic_review":
        gold["reviewed_by"] = "invented-human-review"
    else:
        gold.update(kind="human_reviewed", reviewed_by="analyst", reviewed_at="2026-11-13T13:00:00Z")
        gold["claims"][0]["verdict"] = "supported"
    with pytest.raises(ValidationError):
        GoldenSet.model_validate(gold)


def test_cli_rejects_synthetic_for_release_even_with_perfect_scores(tmp_path, capsys):
    gold = gold_data()
    gold_path, prediction_path, output = tmp_path / "gold.json", tmp_path / "predictions.json", tmp_path / "report.json"
    gold_path.write_text(json.dumps(gold), encoding="utf-8")
    prediction_path.write_text(json.dumps(predictions(gold)), encoding="utf-8")
    args = ["--gold", str(gold_path), "--predictions", str(prediction_path), "--output", str(output)]
    assert main(args) == 0
    assert main([*args, "--require-reviewed"]) == 1
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["acceptance"]["release_pass"] is False
    capsys.readouterr()


def test_reviewed_release_gate_counts_only_suppliers_with_annotated_claims():
    # Entirely fictional test annotations; this verifies the gate, not model quality.
    sources, claims = [], []
    for supplier in range(3):
        quotations = [f"Fiktivní tvrzení {supplier}/{index}" for index in range(8)]
        text = ". ".join(quotations)
        source_id = f"synthetic-{supplier}"
        sources.append({"source_id": source_id, "supplier_id": source_id, "source_url": "https://example.org/test",
                        "captured_at": "2026-11-13T12:00:00Z", "canonical_text": text,
                        "canonical_hash": hashlib.sha256(text.encode()).hexdigest()})
        for index, quotation in enumerate(quotations):
            claims.append({"source_id": source_id, "source_ref": f"sentence:{index}", "quote": quotation,
                           "rule": {"id": "price_rank" if index % 2 else "green_generic"},
                           "verdict": "unverifiable", "rationale": "Fictional test case without reference data."})
    gold = GoldenSet.model_validate({"dataset_id": "fictional-gate-test", "kind": "human_reviewed",
        "reviewed_by": "fictional-test-reviewer", "reviewed_at": "2026-11-13T13:00:00Z",
        "sources": sources, "claims": claims})
    prediction = Predictions(dataset_id=gold.dataset_id, run_id="fictional", claims=[])
    report = evaluate(gold, prediction)
    assert report["release_dataset_eligible"] is True
    # Extra unused supplier metadata must not make a two-supplier gold set pass.
    for current in claims[16:]:
        current["source_id"] = sources[0]["source_id"]
        sources[0]["canonical_text"] += ". " + current["quote"]
    sources[0]["canonical_hash"] = hashlib.sha256(sources[0]["canonical_text"].encode()).hexdigest()
    changed = GoldenSet.model_validate({**gold.model_dump(), "sources": sources, "claims": claims})
    assert evaluate(changed, prediction)["release_dataset_eligible"] is False


def test_invalid_input_is_redacted(tmp_path, capsys):
    source = tmp_path / "secret.json"
    source.write_text('{"private": "NEVER-ECHO-THIS"}', encoding="utf-8")
    assert main(["--gold", str(source), "--predictions", str(source)]) == 2
    assert "NEVER-ECHO-THIS" not in capsys.readouterr().err


def test_real_review_candidates_are_literal_dated_and_unannotated():
    root = Path(__file__).resolve().parents[1]
    candidates = json.loads((root / "eval" / "review-candidates.json").read_text(encoding="utf-8"))
    assert candidates["kind"] == "review_candidate" and len(candidates["claims"]) == 24
    sources = {source["source_id"]: source for source in candidates["sources"]}
    assert {source["supplier_id"] for source in sources.values()} == {"cez", "eon", "innogy"}
    for source in sources.values():
        snapshot = json.loads((root / "src" / "competitive_intelligence" / "data" / "snapshots" / (source["source_id"] + ".json")).read_text(encoding="utf-8"))
        assert source["canonical_text"] == snapshot["text"]
        assert hashlib.sha256(source["canonical_text"].encode()).hexdigest() == source["canonical_hash"]
        assert source["captured_at"] == snapshot["captured_at"]
    for claim in candidates["claims"]:
        text = " ".join(sources[claim["source_id"]]["canonical_text"].split())
        assert claim["quote"] in text
        assert claim["rule"] is None and claim["verdict"] is None
    with pytest.raises(ValidationError):
        GoldenSet.model_validate(candidates)


# Synthetic regression inputs, not human-annotated supplier findings. Explicit
# expected labels are independent of the functions under test. 24 scenarios
# span missing evidence, counterexamples, comparable profiles and green scope.
D = Decimal
COSTS = {"small": {"a": D("8000"), "b": D("7500"), "c": D("9000")}}
SCENARIOS = [
    ("price_rank", {"supplier_id": "a", "scope": "cz_market", "costs_by_profile": COSTS}, "contradicted"),
    ("price_rank", {"supplier_id": "b", "scope": "cz_market", "costs_by_profile": COSTS}, "unverifiable"),
    ("price_rank", {"supplier_id": "b", "scope": "sample", "costs_by_profile": COSTS}, "supported"),
    ("price_rank", {"supplier_id": "z", "scope": "sample", "costs_by_profile": COSTS}, "unverifiable"),
    ("price_rank", {"supplier_id": "c", "scope": "sample", "costs_by_profile": COSTS}, "contradicted"),
    ("price_rank", {"supplier_id": "a", "scope": "sample", "costs_by_profile": {"p": {"a": D("1")}}}, "unverifiable"),
    ("price_pct_cheaper", {"claimed_pct": D("15"), "basis": "annual_trade_cost", "costs": {"p": (D("8500"), D("10000"))}}, "supported"),
    ("price_pct_cheaper", {"claimed_pct": D("20"), "basis": "annual_trade_cost", "costs": {"p": (D("8500"), D("10000"))}}, "contradicted"),
    ("price_pct_cheaper", {"claimed_pct": D("15"), "basis": None, "costs": None}, "unverifiable"),
    ("price_pct_cheaper", {"claimed_pct": D("15"), "basis": "total_bill", "costs": {"p": (D("8500"), D("10000"))}}, "unverifiable"),
    ("price_pct_cheaper", {"claimed_pct": D("15"), "basis": "annual_trade_cost", "costs": {"p": (D("1"), D("0"))}}, "unverifiable"),
    ("price_pct_cheaper", {"claimed_pct": D("15"), "basis": "annual_trade_cost", "costs": {"p": (D("8500"), D("10000")), "q": (D("9500"), D("10000"))}}, "unverifiable"),
    ("price_guarantee", {"claimed_until": date(2028, 12, 31), "fixed_until": date(2028, 12, 31), "exclusions_unclear": False}, "supported"),
    ("price_guarantee", {"claimed_until": date(2028, 12, 31), "fixed_until": date(2027, 12, 31), "exclusions_unclear": False}, "contradicted"),
    ("price_guarantee", {"claimed_until": date(2028, 12, 31), "fixed_until": None, "exclusions_unclear": False}, "unverifiable"),
    ("price_guarantee", {"claimed_until": date(2028, 12, 31), "fixed_until": date(2028, 12, 31), "exclusions_unclear": True}, "unverifiable"),
    ("green_share", {"claimed_pct": D("100"), "renewable_share": D("100"), "product_specific": True, "guarantees_documented": True}, "supported"),
    ("green_share", {"claimed_pct": D("100"), "renewable_share": D("60"), "product_specific": True, "guarantees_documented": True}, "contradicted"),
    ("green_share", {"claimed_pct": D("100"), "renewable_share": D("100"), "product_specific": False, "guarantees_documented": True}, "unverifiable"),
    ("green_share", {"claimed_pct": D("100"), "renewable_share": D("100"), "product_specific": True, "guarantees_documented": False}, "unverifiable"),
    ("green_share", {"claimed_pct": D("100"), "renewable_share": None, "product_specific": True, "guarantees_documented": True}, "unverifiable"),
    ("green_generic", {}, "unverifiable"),
    ("carbon_neutral", {"uses_offsets": True}, "unverifiable"),
    ("carbon_neutral", {"uses_offsets": False}, "unverifiable"),
]


@pytest.mark.parametrize("rule,inputs,expected", SCENARIOS)
def test_synthetic_price_and_green_regression(rule, inputs, expected):
    approved_rules = {name: getattr(engine, name) for name in (
        "price_rank", "price_pct_cheaper", "price_guarantee", "green_share", "green_generic", "carbon_neutral")}
    assert approved_rules[rule](**inputs).rule_verdict == expected
