# Evaluation and annotation

[review-candidates.json](review-candidates.json) contains **24 unannotated literal quotations** from six saved supplier pages across ČEZ, E.ON and innogy. The stored source text, URL, capture date, SHA-256 and normalized character locator make each quotation inspectable. Sources are dated snapshots, not a new live fetch. Product headings, compound statements and vague slogans deliberately remain candidates for an analyst to accept, reject or split.

There are no human annotations or model-quality scores in this file. The evaluation command rejects it until review is completed. Do not fill reviewer fields with an agent's identity or turn missing evidence into contradiction.

1. An analyst reviews the complete page context, chooses 20–30 claims covering three suppliers and both price and green, and annotates `rule`, `verdict`, `rationale` and any `reference_facts`. Use the closed [rule catalogue](../docs/RULE_CATALOG.md). Set `rule` to an object such as `{"id":"green_generic","params":{}}`, never a free-text label. Use `other` when the catalogue cannot express the claim.
2. Validate product, comparison profile, VAT, region and dates against original reference artifacts. A determinate verdict requires reference facts with their original artifact hashes. Preserve exact quotations and source metadata. Distinguish “insufficient evidence” from “no marketing claim”. The gold set defines the marketing-claim boundary used for precision and recall.
3. Save a separate reviewed copy. Change `kind` to `human_reviewed`, set the real `reviewed_by` and UTC `reviewed_at`, and remove rejected candidates. Keep `sources`, `source_ref` and provenance unchanged. Software checks structure and traceability; it cannot prove a human performed the review.
4. Run the model against these **same captured source texts**; a newly fetched page with a different hash is a different evaluation input. Save the complete audit result or create prediction JSON using the [typed evaluation models](../src/competitive_intelligence/evaluation.py).

```bash
uv run fact-or-watt-evaluate --gold eval/reviewed.json --audit-result audit-result.json --require-reviewed --min-score 0.9 --output evaluation-report.json
```

Alternatively use `--predictions predictions.json`. Prediction shape:

```json
{
  "version": 1,
  "dataset_id": "supplier-review-candidates-2026-11-13",
  "run_id": "recorded-model-run-id",
  "claims": [
    {
      "source_id": "source-id-from-gold",
      "canonical_hash": "64-character-original-source-hash",
      "quote": "exact source quotation",
      "rule": {"id": "other", "params": {}},
      "verdict": "unverifiable"
    }
  ]
}
```

All fractions include the numerator and denominator. Matching requires the same source ID/hash and the same quotation after whitespace normalization. Duplicate/hallucinated/wrong-hash predictions are false positives; missing gold claims are false negatives. Matched-only rule/parameter/verdict accuracy and all-gold/end-to-end accuracy are reported separately. Zero denominators produce `null`, never a perfect score. Confusion counts include matched claims only. `--min-score` is an explicit operating threshold (default 0.9), not an independently endorsed scientific standard; release mode requires precision, recall, all-gold rule/verdict accuracy and end-to-end accuracy to meet it.

The [synthetic 24-case price/green regression](../tests/test_evaluation.py) verifies deterministic rule behavior independently of human review. Its passing tests do not measure LLM extraction quality. Exit codes: `0` configured checks passed, `1` acceptance floor or reviewed-data requirement failed, `2` invalid/unreadable input.
