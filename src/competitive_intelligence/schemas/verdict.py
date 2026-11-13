"""Verdict contract with the Judge invariant enforced in code."""

from typing import Self

from pydantic import Field, model_validator

from .common import StrictModel, VerdictValue


class Verdict(StrictModel):
    claim_id: str
    rule_verdict: VerdictValue
    verdict: VerdictValue
    judge_changed: bool = False
    judge_reason: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    reasoning: str
    limitations: list[str] = Field(default_factory=list)
    possible_regulatory_risk: bool = False
    risk_reason: str | None = None
    review_required: bool = False

    @model_validator(mode="after")
    def _judge_invariant(self) -> Self:
        if self.judge_changed:
            if self.verdict is not VerdictValue.UNVERIFIABLE:
                raise ValueError("Judge may only change a verdict to 'unverifiable'")
            if not self.judge_reason:
                raise ValueError("judge_reason is required when judge_changed is true")
        elif self.verdict is not self.rule_verdict:
            raise ValueError("verdict must equal rule_verdict unless judge_changed is true")
        if self.possible_regulatory_risk and not self.risk_reason:
            raise ValueError("risk_reason is required when possible_regulatory_risk is true")
        return self
