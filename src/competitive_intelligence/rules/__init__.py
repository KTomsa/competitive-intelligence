"""Deterministic verification rules. Verdicts are computed here, never by an LLM."""

from .engine import RuleOutcome
from .judge import apply_judge

__all__ = ["RuleOutcome", "apply_judge"]
