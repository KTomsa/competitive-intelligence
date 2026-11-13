"""Loads the fictional demo bundle that drives `mode=fixture` audits."""

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from importlib import resources
from typing import Any

from ..schemas import Source, SupplierInfo, normalize_whitespace


@dataclass(frozen=True)
class FixtureBundle:
    notice: str
    suppliers: list[SupplierInfo]
    profiles: dict[str, Decimal]
    profile_labels: dict[str, str]
    sources: list[Source]
    canonical_texts: dict[str, str]
    raw_claims: list[dict[str, Any]]
    reference: dict[str, Any]
    judge_flags: dict[str, str]

    @property
    def supplier_ids(self) -> list[str]:
        return [s.supplier_id for s in self.suppliers]


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_demo_fixture() -> FixtureBundle:
    raw = json.loads(
        resources.files("competitive_intelligence.fixtures")
        .joinpath("demo_audit.json")
        .read_text(encoding="utf-8")
    )
    sources: list[Source] = []
    texts: dict[str, str] = {}
    for row in raw["sources"]:
        text = row.pop("canonical_text")
        texts[row["source_id"]] = text
        sources.append(
            Source.model_validate(
                {
                    **row,
                    "content_sha256": _sha256(text),
                    "canonical_hash": _sha256(normalize_whitespace(text)),
                }
            )
        )
    return FixtureBundle(
        notice=raw["notice"],
        suppliers=[SupplierInfo.model_validate(s) for s in raw["suppliers"]],
        profiles={p["profile_id"]: Decimal(p["annual_mwh"]) for p in raw["profiles"]},
        profile_labels={p["profile_id"]: p["label"] for p in raw["profiles"]},
        sources=sources,
        canonical_texts=texts,
        raw_claims=raw["claims"],
        reference=raw["reference"],
        judge_flags=raw.get("judge_flags", {}),
    )
