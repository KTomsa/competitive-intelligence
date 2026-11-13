"""Fixed-order audit pipeline: collect -> extract -> verify -> judge -> report.

Stages hand each other typed Claim / Evidence / Verdict objects; there is no
free conversation between agents. Phase 1 wires the fixture adapters, so the
whole run is offline and deterministic.
"""

from collections import Counter

from pydantic import ValidationError

from ..agents import FixtureEvidenceAgent
from ..rules import apply_judge
from ..schemas import (
    AuditMode,
    AuditResult,
    AuditStatus,
    Briefing,
    BriefingItem,
    Claim,
    ClaimType,
    Evidence,
    Source,
    Verdict,
    VerdictValue,
    coerce_rule,
    quote_in_text,
)
from ..tools import ReferenceStore
from .fixtures import FixtureBundle
from .repository import AuditRepository, utc_now

VERDICT_LABELS = {
    VerdictValue.SUPPORTED: "podloženo",
    VerdictValue.PARTIALLY_SUPPORTED: "částečně podloženo",
    VerdictValue.CONTRADICTED: "v rozporu s daty",
    VerdictValue.UNVERIFIABLE: "nelze ověřit",
}


def extract_fixture_claims(
    bundle: FixtureBundle, sources: list[Source]
) -> tuple[list[Claim], list[str]]:
    """Turn fixture claim candidates into validated Claims.

    The same checks will guard the LLM extractor: closed rule catalogue,
    schema validation and the literal-quote test against the captured text.
    """
    by_id = {s.source_id: s for s in sources}
    claims: list[Claim] = []
    dropped: list[str] = []
    for raw in bundle.raw_claims:
        source = by_id.get(raw.get("source_id", ""))
        if source is None:
            continue  # claim belongs to a supplier outside this audit
        claim_id = raw.get("id", "?")
        try:
            claim = Claim.model_validate(
                {
                    **raw,
                    "rule": coerce_rule(raw.get("rule")),
                    "supplier_id": source.supplier_id,
                    "source_url": source.source_url,
                    "captured_at": source.captured_at,
                    "origin": source.origin,
                }
            )
        except ValidationError:
            dropped.append(f"Tvrzení {claim_id} zahozeno: neplatná struktura nebo parametry pravidla.")
            continue
        if not quote_in_text(claim.quote, bundle.canonical_texts[source.source_id]):
            dropped.append(f"Tvrzení {claim_id} zahozeno: citace není doslova v textu stránky.")
            continue
        claims.append(claim)
    return claims, dropped


def build_briefing(claims: list[Claim], verdicts: list[Verdict]) -> Briefing:
    by_claim = {c.id: c for c in claims}
    matrix: dict[ClaimType, dict[VerdictValue, int]] = {}
    totals: Counter[VerdictValue] = Counter()
    items: list[BriefingItem] = []
    for verdict in verdicts:
        claim = by_claim[verdict.claim_id]
        row = matrix.setdefault(claim.claim_type, {})
        row[verdict.verdict] = row.get(verdict.verdict, 0) + 1
        totals[verdict.verdict] += 1
        if verdict.verdict is VerdictValue.CONTRADICTED:
            text = (
                f"Tvrzení „{claim.quote}“ je v rozporu s dostupnými daty. Může jít o příležitost "
                "k vymezení, pokud vlastní nabídka ve stejném srovnání obstojí; to je potřeba ověřit."
            )
        elif verdict.possible_regulatory_risk:
            text = (
                f"Tvrzení „{claim.quote}“ nelze ověřit a nese možné regulatorní riziko. "
                "Doporučeno předat k právní kontrole; nejde o právní závěr."
            )
        elif verdict.judge_changed:
            text = (
                f"U tvrzení „{claim.quote}“ důkaz neodpovídá přesně tomu, co tvrzení říká. "
                "Před jakýmkoli použitím je nutná ruční kontrola."
            )
        else:
            continue
        items.append(
            BriefingItem(
                claim_id=claim.id, supplier_id=claim.supplier_id, verdict=verdict.verdict, text=text
            )
        )
    counts = ", ".join(f"{VERDICT_LABELS[v]}: {totals[v]}" for v in VerdictValue if totals[v])
    summary = (
        f"Posouzeno {len(verdicts)} tvrzení ({counts}). "
        "Chybějící důkaz není důkazem nepravdivosti; přehled není žebříčkem dodavatelů."
        if verdicts
        else "Nebylo nalezeno žádné ověřitelné tvrzení."
    )
    return Briefing(summary=summary, items=items, matrix=matrix)


def run_fixture_audit(repo: AuditRepository, audit_id: str, bundle: FixtureBundle) -> AuditResult:
    request = repo.get_request(audit_id)
    wanted = set(request.supplier_ids)

    repo.set_stage(audit_id, AuditStatus.COLLECTING, "web_scout")
    sources = [s for s in bundle.sources if s.supplier_id in wanted]
    for source in sources:
        label = {
            "live": "načteno živě",
            "baseline": "web beze změny, použita schválená baseline",
            "snapshot": f"web nedostupný ({source.fallback_reason}), použit snapshot",
        }[source.origin.value]
        repo.add_event(
            audit_id,
            "web_scout",
            f"{source.supplier_id}: {label} (ukázková data).",
            level="warning" if source.origin.value == "snapshot" else "info",
            origin=source.origin,
        )

    repo.set_stage(audit_id, AuditStatus.EXTRACTING, "claim_extractor")
    claims, dropped = extract_fixture_claims(bundle, sources)
    repo.add_event(audit_id, "claim_extractor", f"Nalezeno {len(claims)} tvrzení s doslovnou citací.")
    for message in dropped:
        repo.add_event(audit_id, "claim_extractor", message, level="warning")

    repo.set_stage(audit_id, AuditStatus.VERIFYING, "evidence_agents")
    store = ReferenceStore(bundle.reference, bundle.profiles)
    agent = FixtureEvidenceAgent(store, bundle.supplier_ids)
    evidence: list[Evidence] = []
    outcomes = {}
    for claim in claims:
        facts, outcome = agent.gather(claim, request.profile_ids)
        outcomes[claim.id] = outcome
        evidence.append(
            Evidence(
                claim_id=claim.id,
                facts=facts,
                rule_result=outcome.rule_verdict,
                rule_version=outcome.rule_version,
                limitations=list(outcome.limitations),
            )
        )
    repo.add_event(
        audit_id,
        "evidence_agents",
        f"Ověřeno {len(evidence)} tvrzení proti referenčním datům; verdikty spočítal kód.",
    )

    repo.set_stage(audit_id, AuditStatus.JUDGING, "judge")
    verdicts: list[Verdict] = []
    for item in evidence:
        verdict = apply_judge(
            claim_id=item.claim_id,
            outcome=outcomes[item.claim_id],
            evidence_ids=[f.evidence_id for f in item.facts],
            mismatch_reason=bundle.judge_flags.get(item.claim_id),
        )
        verdicts.append(verdict)
        if verdict.judge_changed:
            repo.add_event(
                audit_id,
                "judge",
                f"{item.claim_id}: důkaz neodpovídá tvrzení, verdikt změněn na „nelze ověřit“.",
                level="warning",
            )

    repo.set_stage(audit_id, AuditStatus.REPORTING, "strategist")
    briefing = build_briefing(claims, verdicts)
    repo.add_event(audit_id, "strategist", "Briefing sestaven z uložených verdiktů.")

    result = AuditResult(
        audit_id=audit_id,
        mode=AuditMode.FIXTURE,
        scope_note=bundle.notice,
        suppliers=[s for s in bundle.suppliers if s.supplier_id in wanted],
        profile_ids=request.profile_ids,
        sources=sources,
        claims=claims,
        evidence=evidence,
        verdicts=verdicts,
        briefing=briefing,
        warnings=repo.get_status(audit_id).warnings,
        completed_at=utc_now(),
    )
    repo.complete(audit_id, result)
    return result
