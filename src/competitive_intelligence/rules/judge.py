"""Server-side enforcement of the Judge's single allowed action.

The Judge (an LLM in live mode) only *flags* a mismatch between claim and
evidence. This function decides what that flag may do: turn the verdict into
`unverifiable`. There is no code path that upgrades a verdict.
"""

from ..schemas import Verdict, VerdictValue
from .engine import RuleOutcome


def apply_judge(
    *,
    claim_id: str,
    outcome: RuleOutcome,
    evidence_ids: list[str],
    mismatch_reason: str | None,
) -> Verdict:
    rule_verdict = outcome.rule_verdict
    downgrade = bool(mismatch_reason) and rule_verdict is not VerdictValue.UNVERIFIABLE
    return Verdict(
        claim_id=claim_id,
        rule_verdict=rule_verdict,
        verdict=VerdictValue.UNVERIFIABLE if downgrade else rule_verdict,
        judge_changed=downgrade,
        judge_reason=mismatch_reason if downgrade else None,
        evidence_ids=evidence_ids,
        reasoning=outcome.reasoning,
        limitations=list(outcome.limitations),
        possible_regulatory_risk=outcome.possible_regulatory_risk,
        risk_reason=outcome.risk_reason,
        review_required=outcome.review_required or downgrade,
    )
