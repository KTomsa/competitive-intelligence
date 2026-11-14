"""Reproducible, one-to-one claim evaluation. Never manufacture review labels."""

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, Field, field_validator, model_validator

from .schemas import AuditResult, StrictModel, VerdictValue
from .schemas.claim import Rule, SHA256_PATTERN, normalize_whitespace, quote_in_text, require_https
from .schemas.evidence import EvidenceFact

MAX_FILE_BYTES = 4 * 1024 * 1024


class EvaluationSource(StrictModel):
    source_id: str = Field(min_length=1, max_length=100)
    supplier_id: str = Field(min_length=1, max_length=100)
    source_url: str
    captured_at: AwareDatetime
    canonical_text: str = Field(min_length=1, max_length=500000)
    canonical_hash: str = Field(pattern=SHA256_PATTERN)

    _https = field_validator("source_url")(require_https)

    @model_validator(mode="after")
    def correct_hash(self):
        if hashlib.sha256(self.canonical_text.encode("utf-8")).hexdigest() != self.canonical_hash:
            raise ValueError("Evaluation source hash does not match its text")
        return self


class ExpectedClaim(StrictModel):
    source_id: str
    source_ref: str | None = Field(default=None, max_length=500)
    quote: str = Field(min_length=1, max_length=2000)
    rule: Rule
    verdict: VerdictValue
    rationale: str = Field(min_length=10, max_length=4000)
    reference_facts: list[EvidenceFact] = Field(default_factory=list)


class GoldenSet(StrictModel):
    version: Literal[1] = 1
    dataset_id: str = Field(min_length=1, max_length=100)
    kind: Literal["synthetic_regression", "human_reviewed"]
    reviewed_by: str | None = Field(default=None, min_length=1, max_length=150)
    reviewed_at: AwareDatetime | None = None
    sources: list[EvaluationSource] = Field(min_length=1, max_length=100)
    claims: list[ExpectedClaim] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def valid_annotations(self):
        sources = {source.source_id: source for source in self.sources}
        if len(sources) != len(self.sources):
            raise ValueError("Duplicate evaluation source IDs")
        keys = set()
        for claim in self.claims:
            source = sources.get(claim.source_id)
            key = (claim.source_id, normalize_whitespace(claim.quote))
            if source is None or not quote_in_text(claim.quote, source.canonical_text) or key in keys:
                raise ValueError("Golden quotes must be unique literal quotations from a known source")
            keys.add(key)
            if self.kind == "human_reviewed":
                if not claim.source_ref or not claim.source_ref.strip():
                    raise ValueError("Reviewed claims require a source locator")
                if claim.verdict != VerdictValue.UNVERIFIABLE and not claim.reference_facts:
                    raise ValueError("Reviewed determinate verdicts require annotated reference evidence")
        if self.kind == "human_reviewed":
            if not self.reviewed_by or not self.reviewed_by.strip() or self.reviewed_at is None:
                raise ValueError("Human-reviewed data requires an identified reviewer and review timestamp")
            if any(source.captured_at > self.reviewed_at for source in self.sources):
                raise ValueError("Review cannot precede source capture")
        elif self.reviewed_by is not None or self.reviewed_at is not None:
            raise ValueError("Synthetic annotations must not claim human review")
        return self


class Prediction(StrictModel):
    source_id: str
    canonical_hash: str = Field(pattern=SHA256_PATTERN)
    quote: str = Field(min_length=1, max_length=2000)
    rule: Rule
    verdict: VerdictValue


class Predictions(StrictModel):
    version: Literal[1] = 1
    dataset_id: str
    run_id: str = Field(min_length=1, max_length=150)
    claims: list[Prediction] = Field(max_length=1000)


def fraction(numerator: int, denominator: int) -> dict:
    return {"numerator": numerator, "denominator": denominator,
            "value": numerator / denominator if denominator else None}


def evaluate(gold: GoldenSet, predictions: Predictions) -> dict:
    if predictions.dataset_id != gold.dataset_id:
        raise ValueError("Predictions belong to a different dataset")
    sources = {source.source_id: source for source in gold.sources}
    expected = {(claim.source_id, normalize_whitespace(claim.quote)): claim for claim in gold.claims}
    matched, correct_rules, correct_params, correct_verdicts, fully_correct = set(), 0, 0, 0, 0
    confusion = Counter()
    for prediction in predictions.claims:
        key = (prediction.source_id, normalize_whitespace(prediction.quote))
        source, claim = sources.get(prediction.source_id), expected.get(key)
        # Wrong-source/hash, hallucinated quotes and duplicate predictions count
        # as false positives. They never remove a false negative from the gold set.
        if source is None or prediction.canonical_hash != source.canonical_hash or claim is None or key in matched:
            continue
        matched.add(key)
        rule_ok = prediction.rule.id == claim.rule.id
        params_ok = prediction.rule == claim.rule
        verdict_ok = prediction.verdict == claim.verdict
        correct_rules += rule_ok
        correct_params += params_ok
        correct_verdicts += verdict_ok
        fully_correct += rule_ok and params_ok and verdict_ok
        confusion[(claim.verdict.value, prediction.verdict.value)] += 1
    tp, count = len(matched), len(gold.claims)
    categories = {"price" if claim.rule.id.startswith("price_") else
                  "green" if claim.rule.id.startswith("green_") or claim.rule.id == "carbon_neutral" else "other"
                  for claim in gold.claims}
    release_eligible = (gold.kind == "human_reviewed" and 20 <= count <= 30
                        and len({sources[claim.source_id].supplier_id for claim in gold.claims}) >= 3
                        and {"price", "green"} <= categories)
    return {
        "version": 1, "dataset_id": gold.dataset_id, "dataset_kind": gold.kind,
        "gold_sha256": hashlib.sha256(gold.model_dump_json().encode()).hexdigest(),
        "predictions_sha256": hashlib.sha256(predictions.model_dump_json().encode()).hexdigest(),
        "run_id": predictions.run_id, "reviewed_by": gold.reviewed_by,
        "reviewed_at": gold.reviewed_at.isoformat() if gold.reviewed_at else None,
        "release_dataset_eligible": release_eligible,
        "counts": {"gold": count, "predicted": len(predictions.claims), "matched": tp,
                   "false_positive": len(predictions.claims) - tp, "false_negative": count - tp},
        "metrics": {"extraction_precision": fraction(tp, len(predictions.claims)),
                    "extraction_recall": fraction(tp, count),
                    "rule_accuracy_matched": fraction(correct_rules, tp),
                    "rule_parameters_accuracy_matched": fraction(correct_params, tp),
                    "verdict_accuracy_matched": fraction(correct_verdicts, tp),
                    "rule_accuracy_all_gold": fraction(correct_rules, count),
                    "verdict_accuracy_all_gold": fraction(correct_verdicts, count),
                    "end_to_end_accuracy": fraction(fully_correct, count)},
        "verdict_confusion_matched": [{"expected": pair[0], "predicted": pair[1], "count": value}
                                      for pair, value in sorted(confusion.items())],
        "limitations": ["Exact source/hash and whitespace-normalized quote matching; no semantic fuzzy matching.",
                         "A reviewed label is supplied by the annotator; software cannot certify human review.",
                         "Synthetic regression scores do not measure production model quality.",
                         "Conditional accuracies exclude missed claims; all-gold metrics include them."],
    }


def load_json(path: str | Path):
    with Path(path).open("rb") as stream:
        body = stream.read(MAX_FILE_BYTES + 1)
    if len(body) > MAX_FILE_BYTES:
        raise ValueError("Evaluation file exceeds size limit")
    return json.loads(body.decode("utf-8-sig"))


def from_audit(gold: GoldenSet, result: AuditResult) -> Predictions:
    sources = {source.source_id: source for source in result.sources}
    verdicts = {verdict.claim_id: verdict for verdict in result.verdicts}
    return Predictions(dataset_id=gold.dataset_id, run_id=result.audit_id, claims=[Prediction(
        source_id=claim.source_id, canonical_hash=sources[claim.source_id].canonical_hash,
        quote=claim.quote, rule=claim.rule, verdict=verdicts[claim.id].verdict) for claim in result.claims])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", required=True)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--predictions")
    inputs.add_argument("--audit-result", help="Saved complete AuditResult JSON")
    parser.add_argument("--output", help="Write inspectable report JSON")
    parser.add_argument("--require-reviewed", action="store_true", help="Reject synthetic or undersized release data")
    parser.add_argument("--min-score", type=float, default=0.9, help="Release floor for precision, recall, rule and verdict accuracy")
    args = parser.parse_args(argv)
    if not 0 <= args.min_score <= 1:
        parser.error("--min-score must be between zero and one")
    try:
        gold = GoldenSet.model_validate(load_json(args.gold))
        predictions = (from_audit(gold, AuditResult.model_validate(load_json(args.audit_result))) if args.audit_result
                       else Predictions.model_validate(load_json(args.predictions)))
        report = evaluate(gold, predictions)
        required = ("extraction_precision", "extraction_recall", "rule_accuracy_all_gold", "verdict_accuracy_all_gold", "end_to_end_accuracy")
        passed = all(report["metrics"][name]["value"] is not None
                     and report["metrics"][name]["value"] >= args.min_score for name in required)
        report["acceptance"] = {"minimum_score": args.min_score, "metrics_pass": passed,
                                "release_pass": passed and report["release_dataset_eligible"]}
        encoded = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            Path(args.output).write_text(encoded, encoding="utf-8")
        print(encoded)
        return 0 if passed and (not args.require_reviewed or report["release_dataset_eligible"]) else 1
    except (OSError, ValueError, KeyError):
        # Validation errors can echo entire documents; do not print them.
        print("Evaluation failed: invalid, incompatible or unreadable input.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
